"""Creative Studio routes.

Foundation only, per WO-CS1: the front door, the client picker, projects, the
extended Brand Kit, and the Media Library with its Cloudinary backfill.
Templates, AI Tools, Approvals and Usage are placeholder pages behind the
same login -- real content lands in WO-CS2 through WO-CS6.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, render_template, request

from . import brand_ext, config, jobs, layouts, resolver
from .db import db
from .models import (CreativeJob, CsMediaAsset, CsProject, CsProjectVersion,
                     CsTemplate, CsTemplateScene, CsTemplateVariable)

bp = Blueprint("creative_studio", __name__, url_prefix="/creative-studio",
              template_folder="templates")


def _actor() -> str:
    try:
        from hub.auth import user_from_environ
        return user_from_environ(request.environ) or ""
    except Exception:                                     # noqa: BLE001
        return ""


def _guard() -> None:
    from hub.blueprint_guard import install
    install(bp, mount="/creative-studio")


_guard()


# --------------------------------------------------------------- dashboard

@bp.get("/")
def dashboard():                                          # noqa: ANN202
    recent = (CsProject.query.order_by(CsProject.updated_at.desc())
              .limit(8).all())
    return render_template(
        "cs_dashboard.html", title="Creative Studio",
        creative_types=config.CREATIVE_TYPES, recent=[p.as_dict() for p in recent])


@bp.get("/api/clients/search")
def api_client_search():                                  # noqa: ANN202
    """The Hub's own client picker, never a module-local table -- house
    rule 2. Thin wrapper over hub.clients_registry.search_clients so the
    Create New flow and every other picker on this module read one answer."""
    q = (request.args.get("q") or "").strip()
    try:
        from hub.clients_registry import search_clients
        rows = search_clients(q, limit=12)
    except Exception as exc:                              # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__, "clients": []})
    return jsonify({"ok": True, "clients": [
        {"name": r.get("name", ""), "domain": r.get("domain", "")} for r in rows]})


@bp.get("/api/clients/<path:client>/completeness")
def api_client_completeness(client):                       # noqa: ANN202
    """Which Brand Kit fields are missing before a template will render
    well, using the same pill vocabulary Client 360 already uses."""
    try:
        from hub.clients_registry import find_client
        row = find_client(client) or {}
        domain = row.get("domain") or ""
    except Exception:                                     # noqa: BLE001
        domain = ""
    try:
        kit = brand_ext.kit(client, domain)
    except Exception as exc:                              # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__})
    ext = kit.get("ext") or {}
    pills = [
        {"label": "Logo", "state": "ok" if kit.get("logo_tiles") else "warn"},
        {"label": "Colors", "state": "ok" if kit.get("palette") else "warn"},
        {"label": "Phone", "state": "ok" if ext.get("locations") else "warn"},
        {"label": "CTA style", "state": "ok" if ext.get("cta_style") else "warn"},
    ]
    return jsonify({"ok": True, "pills": pills, "has_brand": kit.get("has_brand", False)})


# --------------------------------------------------------------- projects

@bp.get("/projects")
def projects_page():                                       # noqa: ANN202
    rows = CsProject.query.order_by(CsProject.updated_at.desc()).limit(200).all()
    return render_template("cs_projects.html", title="Projects",
                           projects=[p.as_dict() for p in rows],
                           statuses=config.PROJECT_STATUSES)


@bp.post("/api/projects")
def api_create_project():                                  # noqa: ANN202
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    ctype = str(data.get("creative_type") or "").strip()
    client = str(data.get("client_name") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "Name the project."}), 400

    # A template, if one was picked, is the source of truth for what this
    # project defaults to -- CLAUDE.md's "projects pin the version they were
    # created from" rule. template_version is set here and never moves,
    # even if the template is edited and republished afterward.
    template_id = str(data.get("template_id") or "")
    tmpl = None
    if template_id:
        tmpl = CsTemplate.query.get(template_id)
        if tmpl is None:
            return jsonify({"ok": False, "error": "That is not a template "
                            "this Hub has."}), 400
        ctype = ctype or tmpl.creative_type

    if not config.creative_type(ctype):
        return jsonify({"ok": False, "error": "Unknown creative type."}), 400

    # Nothing is invented: a typed client name resolves against the real
    # book or the project is filed with no client, never against a guessed
    # match -- the client_key.resolve() rule this whole Hub is built on.
    if client:
        try:
            from hub.clients_registry import find_client
            hit = find_client(client)
        except Exception:                                 # noqa: BLE001
            hit = None
        if not hit:
            return jsonify({"ok": False, "error": "That is not a client on "
                            "file. Leave it blank for a generic Smart 1 "
                            "asset, or pick one from the list."}), 400
        client = hit.get("name") or client

    project = CsProject(
        client_name=client, name=name[:300], creative_type=ctype,
        template_id=template_id, template_version=(tmpl.version if tmpl else None),
        duration=data.get("duration") or (tmpl.duration if tmpl else None),
        aspect_ratio=str(data.get("aspect_ratio") or (tmpl.aspect_ratio if tmpl else "16:9")),
        status="Draft", created_by=_actor())
    project.brief = data.get("brief") or {}
    db.session.add(project)
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "project_created", actor=_actor(),
                  client=client or None, project=name, creative_type=ctype,
                  template=template_id or None)
    except Exception:                                     # noqa: BLE001
        pass

    return jsonify({"ok": True, "project": project.as_dict()})


@bp.get("/projects/<int:project_id>")
def project_detail(project_id):                            # noqa: ANN202
    project = CsProject.query.get_or_404(project_id)
    versions = project.versions.order_by(CsProjectVersion.version.desc()).all()

    resolved, unresolved = {}, []
    tmpl = CsTemplate.query.get(project.template_id) if project.template_id else None
    if tmpl is not None:
        domain = ""
        if project.client_name:
            try:
                from hub.clients_registry import find_client
                hit = find_client(project.client_name)
                domain = (hit or {}).get("domain", "")
            except Exception:                             # noqa: BLE001
                domain = ""
        resolved = resolver.resolve(tmpl, project, client=project.client_name, domain=domain)
        unresolved = resolver.unresolved_required(resolved)

    return render_template("cs_project_detail.html", title=project.name,
                           project=project.as_dict(),
                           versions=[v.as_dict() for v in versions],
                           statuses=config.PROJECT_STATUSES,
                           template=tmpl.as_dict(with_children=False) if tmpl else None,
                           resolved=resolved, unresolved=unresolved)


# --------------------------------------------------------------- brand kit

@bp.get("/brand-kits")
def brand_kits_index():                                    # noqa: ANN202
    return render_template("cs_brand_kits.html", title="Brand Kits")


@bp.get("/brand-kits/<path:client>")
def brand_kit_page(client):                                # noqa: ANN202
    try:
        from hub.clients_registry import find_client
        row = find_client(client) or {}
        domain = row.get("domain") or ""
    except Exception:                                     # noqa: BLE001
        domain = ""
    kit = brand_ext.kit(client, domain)
    # A grid of several logo tiles, never one logo shown once -- the shape
    # hub/storage.preview_url() exists to cap rather than the _LOGO exemption
    # test_image_download.py carries for a lone mark. It is a no-op on
    # anything not ours (most of these are Brandfetch's own CDN), and caps
    # the ones that are (a logo we stored from an observed sighting).
    try:
        from hub import storage
        for tile in kit.get("logo_tiles") or []:
            tile["preview"] = storage.preview_url(tile.get("url") or "")
    except Exception:                                     # noqa: BLE001
        pass
    return render_template("cs_brand_kit.html", title=f"Brand Kit — {client}",
                           client=client, domain=domain, kit=kit)


@bp.post("/api/brand-kits/<path:client>/lookup")
def api_brand_kit_lookup(client):                          # noqa: ANN202
    """Spend a Brandfetch credit -- behind a button, never a page load, the
    rule hub/brand_lookup.py exists to enforce."""
    try:
        from hub.clients_registry import find_client
        row = find_client(client) or {}
        domain = row.get("domain") or ""
    except Exception:                                     # noqa: BLE001
        domain = ""
    try:
        from hub import brand_lookup
        result = brand_lookup.lookup(domain, client=client, module="creative_studio")
    except Exception as exc:                              # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__})
    return jsonify({"ok": True, **result})


@bp.post("/api/brand-kits/<path:client>")
def api_save_brand_kit(client):                            # noqa: ANN202
    data = request.get_json(silent=True) or {}
    result = brand_ext.save(client, data, actor=_actor())
    if result.get("ok"):
        try:
            from hub import audit
            audit.log("creative_studio", "brand_kit_saved", actor=_actor(), client=client)
        except Exception:                                 # noqa: BLE001
            pass
    return jsonify(result)


# ---------------------------------------------------------- media library

@bp.get("/media")
def media_library():                                        # noqa: ANN202
    client = (request.args.get("client") or "").strip()
    tab = (request.args.get("tab") or "image").strip()
    if tab not in config.ASSET_TYPE_KEYS:
        tab = "image"
    q = CsMediaAsset.query.filter_by(deleted=False, asset_type=tab)
    if client:
        q = q.filter_by(client_name=client)
    rows = q.order_by(CsMediaAsset.created_at.desc()).limit(200).all()
    return render_template("cs_media.html", title="Media Library",
                           client=client, tab=tab, assets=[r.as_dict() for r in rows],
                           asset_types=config.ASSET_TYPES)


@bp.post("/api/media/index")
def api_media_index():                                      # noqa: ANN202
    """Enqueue the backfill sweep for one client's folder. A POST, not a GET
    that runs on page load -- the domain-calendar rule CLAUDE.md states at
    length: a GET that rebuilds is one a reload or a prefetch fires without
    anybody asking."""
    client = (request.get_json(silent=True) or {}).get("client") or ""
    job = jobs.enqueue("index", client_name=client, created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/media/upload")
def api_media_upload():                                      # noqa: ANN202
    """Signed upload through the shared storage layer -- house rule 3, never
    a module-local Cloudinary block. Resource type and folder are derived,
    never chosen by the caller, the exact defect CLAUDE.md names about the
    proposal-suite PDFs that 403'd for it."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file selected."}), 400
    client = (request.form.get("client") or "").strip()
    ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
    asset_type = next((k for k, exts in config.ACCEPTED_EXTENSIONS.items()
                       if ext in exts), "")
    if not asset_type:
        return jsonify({"ok": False, "error": f"'{ext}' is not an accepted "
                        "file type here."}), 400

    data = f.read()
    try:
        from hub import storage
        asset = storage.put("creative_studio", f.filename, data, client=client)
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)[:300]}), 400

    row = CsMediaAsset(
        client_name=client, asset_type=asset_type,
        filename=f.filename, original_filename=f.filename,
        mime_type=f.mimetype or "", file_size=asset.bytes,
        cloudinary_public_id=asset.public_id, cloudinary_url=asset.url,
        created_by=_actor(), source="upload")
    db.session.add(row)
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "media_uploaded", actor=_actor(),
                  client=client or None, filename=f.filename)
    except Exception:                                       # noqa: BLE001
        pass

    return jsonify({"ok": True, "asset": row.as_dict()})


@bp.post("/api/media/<int:asset_id>")
def api_media_update(asset_id):                              # noqa: ANN202
    row = CsMediaAsset.query.get_or_404(asset_id)
    data = request.get_json(silent=True) or {}
    if "filename" in data:
        row.filename = str(data.get("filename") or "")[:400]
    if "tags" in data:
        row.tags = data.get("tags") or []
    db.session.commit()
    return jsonify({"ok": True, "asset": row.as_dict()})


@bp.post("/api/media/<int:asset_id>/delete")
def api_media_delete(asset_id):                              # noqa: ANN202
    """Soft-delete the index row. The Cloudinary asset itself is left for the
    30-day retention sweep -- deleting it outright here would defeat the
    grace period the build spec asks for."""
    row = CsMediaAsset.query.get_or_404(asset_id)
    row.deleted = True
    db.session.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------- jobs

@bp.get("/api/jobs/<int:job_id>")
def api_job_status(job_id):                                  # noqa: ANN202
    job = CreativeJob.query.get_or_404(job_id)
    return jsonify({"ok": True, "job": job.as_dict()})


# ------------------------------------------------- placeholder screens

@bp.get("/templates")
def templates_page():                                        # noqa: ANN202
    rows = CsTemplate.query.filter_by(status="published").order_by(
        CsTemplate.category, CsTemplate.industry, CsTemplate.duration).all()

    ctype = (request.args.get("type") or "").strip()
    industry = (request.args.get("industry") or "").strip()
    duration = (request.args.get("duration") or "").strip()
    aspect = (request.args.get("aspect") or "").strip()
    client = (request.args.get("client") or "").strip()

    filtered = rows
    if ctype:
        filtered = [t for t in filtered if t.creative_type == ctype]
    if industry:
        filtered = [t for t in filtered if t.industry == industry]
    if duration:
        try:
            filtered = [t for t in filtered if t.duration == int(duration)]
        except ValueError:
            pass
    if aspect:
        filtered = [t for t in filtered if t.aspect_ratio == aspect]

    # Filter option values come from what is actually published, never a
    # constant list -- CLAUDE.md's rule for every gallery filter in this Hub:
    # adding a template with a new industry must not need a second edit here.
    all_types = sorted({t.creative_type for t in rows if t.creative_type})
    all_industries = sorted({t.industry for t in rows if t.industry})
    all_durations = sorted({t.duration for t in rows if t.duration})
    all_aspects = sorted({t.aspect_ratio for t in rows if t.aspect_ratio})

    return render_template(
        "cs_templates_gallery.html", title="Templates",
        templates=[t.as_dict(with_children=False) for t in filtered],
        all_types=all_types, all_industries=all_industries,
        all_durations=all_durations, all_aspects=all_aspects,
        selected={"type": ctype, "industry": industry, "duration": duration,
                 "aspect": aspect}, client=client,
        creative_type_labels={t["key"]: t["label"] for t in config.CREATIVE_TYPES},
        industry_labels=config.INDUSTRY_LABELS)


@bp.get("/templates/<path:template_id>")
def template_preview(template_id):                            # noqa: ANN202
    tmpl = CsTemplate.query.get_or_404(template_id)
    client = (request.args.get("client") or "").strip()
    domain = ""
    if client:
        try:
            from hub.clients_registry import find_client
            hit = find_client(client)
            domain = (hit or {}).get("domain", "")
        except Exception:                                    # noqa: BLE001
            domain = ""
    resolved = resolver.resolve(tmpl, None, client=client, domain=domain)
    return render_template(
        "cs_template_preview.html", title=tmpl.name, template=tmpl.as_dict(),
        layouts=layouts.LAYOUTS, resolved=resolved, client=client,
        creative_types=config.CREATIVE_TYPES,
        industry_labels=config.INDUSTRY_LABELS)


@bp.get("/templates/admin")
def templates_admin():                                        # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    rows = CsTemplate.query.order_by(CsTemplate.updated_at.desc()).all()
    return render_template("cs_templates_admin.html", title="Template Admin",
                           templates=[t.as_dict(with_children=False) for t in rows])


@bp.get("/templates/admin/<path:template_id>")
def template_edit(template_id):                               # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    if template_id == "new":
        tmpl_dict = {"id": "", "name": "", "description": "", "category": "",
                    "industry": "general", "duration": 30, "aspect_ratio": "16:9",
                    "creative_type": "", "tags": [], "status": "draft",
                    "version": 0, "scenes": [], "variables": []}
    else:
        tmpl = CsTemplate.query.get_or_404(template_id)
        tmpl_dict = tmpl.as_dict()
    return render_template("cs_template_edit.html", title="Edit template",
                           template=tmpl_dict, layouts=layouts.LAYOUTS,
                           layer_keys=layouts.LAYER_KEYS,
                           animations=layouts.ANIMATIONS,
                           transitions=layouts.TRANSITIONS,
                           aspect_ratios=layouts.ASPECT_RATIOS,
                           creative_types=config.CREATIVE_TYPES)


def _is_admin() -> bool:
    """Template creation is admin-only in Phase 1 (build spec §14).

    Mirrors the reading hub/access.py documents for the Utilities gate,
    without reusing its private closure (`viewer_is_admin` is nested inside
    `create_hub_app()` and not importable): an account's own role decides,
    and a shared-password session counts as Admin because it is the
    emergency door -- the same reasoning, restated here rather than copied
    from a function this module cannot reach.
    """
    try:
        from hub.users_routes import current_account
        account = current_account()
    except Exception:                                        # noqa: BLE001
        return True   # standalone / users module unavailable: nothing to gate
    if account is None:
        return True   # PANEL_PASSWORD session -- the emergency door
    return bool(account.is_admin)


def _admin_refused():
    if request.path.startswith("/creative-studio/api/") or \
            "application/json" in (request.headers.get("Accept") or ""):
        return jsonify({"ok": False, "error": "Template Admin is for admin "
                        "accounts."}), 403
    return render_template("cs_coming_soon.html", title="Template Admin",
                           heading="Template Admin",
                           body="Template Admin is for admin accounts. Ask "
                                "an admin to create or edit a template."), 403


@bp.get("/api/templates/layouts")
def api_layouts():                                             # noqa: ANN202
    return jsonify({"ok": True, "layouts": layouts.LAYOUTS,
                    "layer_keys": layouts.LAYER_KEYS,
                    "animations": layouts.ANIMATIONS,
                    "transitions": layouts.TRANSITIONS,
                    "aspect_ratios": layouts.ASPECT_RATIOS})


@bp.post("/api/templates")
def api_create_template():                                     # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    data = request.get_json(silent=True) or {}
    tid = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    if not tid or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}", tid):
        return jsonify({"ok": False, "error": "Give the template a slug id "
                        "(lowercase letters, digits and hyphens)."}), 400
    if not name:
        return jsonify({"ok": False, "error": "Name the template."}), 400
    if CsTemplate.query.get(tid) is not None:
        return jsonify({"ok": False, "error": f"'{tid}' already exists."}), 400
    if data.get("creative_type") and not config.creative_type(data["creative_type"]):
        return jsonify({"ok": False, "error": "Unknown creative type."}), 400

    tmpl = CsTemplate(id=tid, name=name[:200],
                      description=(data.get("description") or "")[:2000],
                      category=(data.get("category") or "")[:60],
                      industry=(data.get("industry") or "general")[:60],
                      duration=int(data.get("duration") or 30),
                      aspect_ratio=(data.get("aspect_ratio") or "16:9"),
                      creative_type=(data.get("creative_type") or ""),
                      status="draft", version=1, created_by=_actor())
    tmpl.tags = data.get("tags") or []
    db.session.add(tmpl)
    db.session.commit()
    return jsonify({"ok": True, "template": tmpl.as_dict()})


@bp.post("/api/templates/<path:template_id>")
def api_update_template(template_id):                          # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    tmpl = CsTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    for field in ("name", "description", "category", "industry", "aspect_ratio",
                 "creative_type"):
        if field in data:
            setattr(tmpl, field, str(data[field] or "")[:2000])
    if "duration" in data:
        try:
            tmpl.duration = int(data["duration"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Duration must be a "
                            "number of seconds."}), 400
    if "tags" in data:
        tmpl.tags = data.get("tags") or []
    if data.get("creative_type") and not config.creative_type(data["creative_type"]):
        return jsonify({"ok": False, "error": "Unknown creative type."}), 400
    db.session.commit()
    return jsonify({"ok": True, "template": tmpl.as_dict()})


@bp.post("/api/templates/<path:template_id>/duplicate")
def api_duplicate_template(template_id):                       # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    src = CsTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    new_id = (data.get("id") or "").strip()
    if not new_id or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}", new_id):
        return jsonify({"ok": False, "error": "Give the copy a slug id "
                        "(lowercase letters, digits and hyphens)."}), 400
    if CsTemplate.query.get(new_id) is not None:
        return jsonify({"ok": False, "error": f"'{new_id}' already exists."}), 400

    copy = CsTemplate(id=new_id, name=f"{src.name} (copy)",
                      description=src.description, category=src.category,
                      industry=src.industry, duration=src.duration,
                      aspect_ratio=src.aspect_ratio, creative_type=src.creative_type,
                      status="draft", version=1, created_by=_actor())
    copy.tags = src.tags
    db.session.add(copy)
    for scene in src.scenes:
        row = CsTemplateScene(template_id=new_id, position=scene.position,
                              default_duration=scene.default_duration,
                              layout_key=scene.layout_key)
        row.layers = scene.layers
        db.session.add(row)
    for var in src.variables:
        db.session.add(CsTemplateVariable(template_id=new_id, name=var.name,
                                          source=var.source, default=var.default,
                                          required=var.required))
    db.session.commit()
    return jsonify({"ok": True, "template": copy.as_dict()})


@bp.post("/api/templates/<path:template_id>/publish")
def api_publish_template(template_id):                         # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    tmpl = CsTemplate.query.get_or_404(template_id)
    if not tmpl.scenes.count():
        return jsonify({"ok": False, "error": "A template with no scenes "
                        "cannot be published."}), 400
    tmpl.status = "published"
    tmpl.version = (tmpl.version or 0) + 1
    db.session.commit()
    try:
        from hub import audit
        audit.log("creative_studio", "template_published", actor=_actor(),
                  template=tmpl.id, version=tmpl.version)
    except Exception:                                          # noqa: BLE001
        pass
    return jsonify({"ok": True, "template": tmpl.as_dict()})


@bp.post("/api/templates/<path:template_id>/disable")
def api_disable_template(template_id):                         # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    tmpl = CsTemplate.query.get_or_404(template_id)
    tmpl.status = "archived"
    db.session.commit()
    return jsonify({"ok": True, "template": tmpl.as_dict()})


@bp.post("/api/templates/<path:template_id>/scenes")
def api_add_scene(template_id):                                # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    tmpl = CsTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    layout_key = data.get("layout_key") or ""
    if not layouts.layout(layout_key):
        return jsonify({"ok": False, "error": f"'{layout_key}' is not a "
                        "layout this Hub has."}), 400
    layer_data = data.get("layers") or {}
    bad = layouts.validate_layers(layout_key, layer_data)
    if bad:
        return jsonify({"ok": False, "error": f"{layouts.layout(layout_key)['label']} "
                        f"does not accept: {', '.join(bad)}."}), 400
    position = (data.get("position") or (tmpl.scenes.count() + 1))
    row = CsTemplateScene(template_id=tmpl.id, position=int(position),
                          default_duration=float(data.get("default_duration") or 5),
                          layout_key=layout_key)
    row.layers = layer_data
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "scene": row.as_dict()})


@bp.post("/api/templates/<path:template_id>/scenes/<int:scene_id>")
def api_update_scene(template_id, scene_id):                   # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    scene = CsTemplateScene.query.filter_by(id=scene_id, template_id=template_id).first_or_404()
    data = request.get_json(silent=True) or {}
    layout_key = data.get("layout_key", scene.layout_key)
    if not layouts.layout(layout_key):
        return jsonify({"ok": False, "error": f"'{layout_key}' is not a "
                        "layout this Hub has."}), 400
    layer_data = data.get("layers", scene.layers)
    bad = layouts.validate_layers(layout_key, layer_data)
    if bad:
        return jsonify({"ok": False, "error": f"{layouts.layout(layout_key)['label']} "
                        f"does not accept: {', '.join(bad)}."}), 400
    scene.layout_key = layout_key
    scene.layers = layer_data
    if "position" in data:
        scene.position = int(data["position"])
    if "default_duration" in data:
        scene.default_duration = float(data["default_duration"])
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.as_dict()})


@bp.post("/api/templates/<path:template_id>/scenes/<int:scene_id>/delete")
def api_delete_scene(template_id, scene_id):                   # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    scene = CsTemplateScene.query.filter_by(id=scene_id, template_id=template_id).first_or_404()
    db.session.delete(scene)
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/api/templates/<path:template_id>/variables")
def api_add_variable(template_id):                              # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    tmpl = CsTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return jsonify({"ok": False, "error": "Give the variable a name "
                        "(lowercase letters, digits and underscores)."}), 400
    source = data.get("source") or "brief"
    if source not in ("brand", "brief", "weather", "manual"):
        return jsonify({"ok": False, "error": "Unknown variable source."}), 400
    row = CsTemplateVariable(template_id=tmpl.id, name=name, source=source,
                             default=(data.get("default") or "")[:500],
                             required=bool(data.get("required")))
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "variable": row.as_dict()})


@bp.post("/api/templates/<path:template_id>/variables/<int:variable_id>/delete")
def api_delete_variable(template_id, variable_id):              # noqa: ANN202
    if not _is_admin():
        return _admin_refused()
    var = CsTemplateVariable.query.filter_by(id=variable_id, template_id=template_id).first_or_404()
    db.session.delete(var)
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/ai-tools")
def ai_tools_page():                                         # noqa: ANN202
    return render_template("cs_coming_soon.html", title="AI Tools",
                           heading="AI Tools",
                           body="The AI Tools registry ships in WO-CS4, "
                                "pointing generation at the jobs already "
                                "wired in this change and at the tools "
                                "already live under /tools.")


@bp.get("/approvals")
def approvals_page():                                        # noqa: ANN202
    return render_template("cs_coming_soon.html", title="Approvals",
                           heading="Approvals",
                           body="Client approval ships in WO-CS6, reusing "
                                "the ads_builder share mechanism at "
                                "/review/<token> rather than a second "
                                "approval flow.")


@bp.get("/usage")
def usage_page():                                             # noqa: ANN202
    return render_template("cs_coming_soon.html", title="Usage & Costs",
                           heading="Usage & Costs",
                           body="cs_usage_logs and this dashboard ship in "
                                "WO-CS4/WO-CS6, once there are provider "
                                "calls here to meter.")
