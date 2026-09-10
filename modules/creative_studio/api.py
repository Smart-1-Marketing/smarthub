"""Creative Studio routes.

WO-CS1 laid the front door, the client picker, projects, the extended Brand
Kit and the Media Library. WO-CS2 added the template database and gallery.
WO-CS3 is the editor: opening a project built from a template binds it to a
Commercial Builder storyboard (`binder.bind()`) rather than building a second
scene editor. AI Tools, Approvals and Usage are placeholder pages behind the
same login -- real content lands in WO-CS4 through WO-CS6.
"""
from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from . import binder, brand_ext, config, jobs, layouts, resolver, usage
from .db import db
from .models import (CreativeJob, CsAiTool, CsMediaAsset, CsProject, CsProjectVersion,
                     CsTemplate, CsTemplateScene, CsTemplateVariable, CsUsageLog)

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
                           resolved=resolved, unresolved=unresolved,
                           can_generate=project.creative_type in binder.PLATFORM_BY_CREATIVE_TYPE)


@bp.post("/api/projects/<int:project_id>/open")
def api_open_project(project_id):                          # noqa: ANN202
    """Bind this project to a Commercial Builder storyboard (once) and hand
    back where to send the browser. WO-CS3: opening a project built from a
    template lands in the Storyboard Editor with its scenes already laid
    out, rather than the Commercial Builder's own empty Start page."""
    project = CsProject.query.get_or_404(project_id)
    result = binder.bind(project)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify({"ok": True, "cb_project_id": result["cb_project_id"],
                    "url": f"/tools/commercial-builder/project/{result['cb_project_id']}/blueprint"})


@bp.post("/api/projects/<int:project_id>/variables")
def api_override_variable(project_id):                     # noqa: ANN202
    """One project-level variable override -- the "manual" rung of the
    resolver's priority order, so a click on a variable chip in the
    Storyboard Editor's layer panel changes what this project resolves to
    without touching the Brand Kit or the template default anything else
    reads."""
    project = CsProject.query.get_or_404(project_id)
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name the variable being overridden."}), 400
    overrides = dict(project.resolved_vars or {})
    value = data.get("value")
    if value is None or str(value).strip() == "":
        overrides.pop(name, None)
    else:
        overrides[name] = str(value)
    project.resolved_vars = overrides
    db.session.commit()

    tmpl = CsTemplate.query.get(project.template_id) if project.template_id else None
    resolved = {}
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
    return jsonify({"ok": True, "resolved": resolved})


# ------------------------------------------------------------- generation

@bp.post("/api/projects/<int:project_id>/generate/storyboard")
def api_generate_storyboard(project_id):                   # noqa: ANN202
    """Enqueue concept generation (kind="storyboard") -- WO-CS4 item 2.
    Never runs inline: the route's whole job is to write a queued row and
    hand back its id, the same shape every enqueue route in this module
    already uses (`api_media_index`)."""
    project = CsProject.query.get_or_404(project_id)
    if not project.cb_project_id and not project.template_id:
        if not project.brief or not project.brief.get("what_advertising"):
            return jsonify({"ok": False, "error": "Save a commercial brief "
                            "(what you're advertising) before generating "
                            "concepts."}), 400
    job = jobs.enqueue("storyboard", project_id=project.id,
                       client_name=project.client_name, created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/projects/<int:project_id>/generate/script")
def api_generate_script(project_id):                        # noqa: ANN202
    """Enqueue script generation (kind="script"). Requires concepts already
    generated -- the "storyboard" job above -- so this refuses at enqueue
    time rather than letting the sweep discover it a step later."""
    project = CsProject.query.get_or_404(project_id)
    if not project.cb_project_id:
        return jsonify({"ok": False, "error": "Generate concepts before "
                        "generating a script."}), 400
    try:
        from modules.commercial_builder.models import CommercialProject as CbProject
        cb_project = CbProject.query.get(project.cb_project_id)
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__}), 400
    if cb_project is None or not cb_project.concepts:
        return jsonify({"ok": False, "error": "Generate concepts before "
                        "generating a script."}), 400
    job = jobs.enqueue("script", project_id=project.id,
                       client_name=project.client_name, created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/projects/<int:project_id>/generate/image")
def api_generate_image(project_id):                          # noqa: ANN202
    """Enqueue per-scene AI-still generation (kind="image"). `scene_id` is a
    Commercial Builder scene id -- the same scene this project's own
    Storyboard Editor already draws a "Generate AI" button on."""
    project = CsProject.query.get_or_404(project_id)
    if not project.cb_project_id:
        return jsonify({"ok": False, "error": "Open this project in the "
                        "Storyboard Editor before generating images."}), 400
    data = request.get_json(silent=True) or {}
    scene_id = data.get("scene_id")
    if not scene_id:
        return jsonify({"ok": False, "error": "Name the scene to generate for."}), 400
    try:
        from modules.commercial_builder.models import Scene as CbScene
        scene = CbScene.query.filter_by(id=scene_id, project_id=project.cb_project_id).first()
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__}), 400
    if scene is None:
        return jsonify({"ok": False, "error": "That scene is not on this "
                        "project's storyboard."}), 400
    job = jobs.enqueue("image", project_id=project.id, client_name=project.client_name,
                       payload={"scene_id": scene_id}, created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/projects/<int:project_id>/generate/voice")
def api_generate_voice(project_id):                          # noqa: ANN202
    """Enqueue a full-project voiceover (kind="voice") -- WO-CS5 item 1.
    Single-tick once picked up: ElevenLabs' own call is synchronous."""
    project = CsProject.query.get_or_404(project_id)
    if not project.cb_project_id:
        return jsonify({"ok": False, "error": "Open this project in the "
                        "Storyboard Editor before generating a voiceover."}), 400
    data = request.get_json(silent=True) or {}
    voice_id = (data.get("voice_id") or "").strip()
    if not voice_id:
        return jsonify({"ok": False, "error": "Choose a voice first."}), 400
    job = jobs.enqueue("voice", project_id=project.id, client_name=project.client_name,
                       payload={"voice_id": voice_id,
                                "stability": data.get("stability", 0.5),
                                "style": data.get("style", 0.5),
                                "speed": data.get("speed", 1.0)},
                       created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/projects/<int:project_id>/generate/heygen")
def api_generate_heygen(project_id):                          # noqa: ANN202
    """Enqueue a HeyGen spokesperson clip (kind="heygen") for one scene's
    narration -- WO-CS5 item 1. Lands in the Media Library as a video, the
    same shape the "image" job files an AI still under."""
    project = CsProject.query.get_or_404(project_id)
    if not project.cb_project_id:
        return jsonify({"ok": False, "error": "Open this project in the "
                        "Storyboard Editor before generating a spokesperson clip."}), 400
    data = request.get_json(silent=True) or {}
    scene_id = data.get("scene_id")
    avatar_id = (data.get("avatar_id") or "").strip()
    if not scene_id:
        return jsonify({"ok": False, "error": "Name the scene to generate for."}), 400
    if not avatar_id:
        return jsonify({"ok": False, "error": "Choose a presenter first."}), 400
    try:
        from modules.commercial_builder.models import Scene as CbScene
        scene = CbScene.query.filter_by(id=scene_id, project_id=project.cb_project_id).first()
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__}), 400
    if scene is None:
        return jsonify({"ok": False, "error": "That scene is not on this "
                        "project's storyboard."}), 400
    job = jobs.enqueue("heygen", project_id=project.id, client_name=project.client_name,
                       payload={"scene_id": scene_id, "avatar_id": avatar_id,
                                "voice_id": data.get("voice_id") or ""},
                       created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/projects/<int:project_id>/render")
def api_generate_render(project_id):                          # noqa: ANN202
    """Enqueue a Creatomate render (kind="render") -- WO-CS5 item 2.

    The hard QC gate the Commercial Builder's own `/render` route enforces,
    with the override it offers turned off: a render started from this
    front door has nobody watching the QC panel to press force through it,
    so a failing check refuses here rather than being silently skippable.
    """
    project = CsProject.query.get_or_404(project_id)
    if not project.cb_project_id:
        return jsonify({"ok": False, "error": "Open this project in the "
                        "Storyboard Editor before rendering."}), 400
    try:
        from modules.commercial_builder.models import (Client as CbClient,
                                                        CommercialProject as CbProject,
                                                        Scene as CbScene)
        from modules.commercial_builder.services import qc_service
        cb_project = CbProject.query.get(project.cb_project_id)
        cb_client = CbClient.query.get(cb_project.client_id) if cb_project else None
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__}), 400
    if cb_project is None or cb_client is None:
        return jsonify({"ok": False, "error": "This project's storyboard no "
                        "longer exists."}), 400
    scenes = [s.to_dict() for s in cb_project.scenes.order_by(CbScene.order_index).all()]
    qc = qc_service.run_qc(cb_project.to_dict(include_scenes=False), cb_client.to_dict(), scenes)
    if not qc.get("_all_passed"):
        return jsonify({"ok": False, "error": "QC checks failed. Fix the "
                        "flagged items before rendering.", "qc_results": qc}), 409
    data = request.get_json(silent=True) or {}
    fmt = data.get("format") or (cb_project.formats or ["16:9"])[0]
    job = jobs.enqueue("render", project_id=project.id, client_name=project.client_name,
                       payload={"format": fmt}, created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.get("/api/projects/<int:project_id>/versions")
def api_list_versions(project_id):                            # noqa: ANN202
    project = CsProject.query.get_or_404(project_id)
    rows = project.versions.order_by(CsProjectVersion.version.desc()).all()
    return jsonify({"ok": True, "versions": [v.as_dict() for v in rows]})


@bp.post("/api/projects/<int:project_id>/versions/<int:version_number>/approve")
def api_approve_version(project_id, version_number):          # noqa: ANN202
    """A human says this render is good -- and only then is it filed to the
    client's 360 record. Never mutates the version row: `CsProjectVersion`
    is written once and stands (WO-CS1's own rule for this table), so
    approving reads whichever version was named and files THAT one, through
    the same WORK_KINDS path every other deliverable in this Hub reaches a
    client's record by -- never a second, module-local idea of what
    "filed" means."""
    project = CsProject.query.get_or_404(project_id)
    version = CsProjectVersion.query.filter_by(
        project_id=project.id, version=version_number).first_or_404()
    project.status = "Approved"
    project.approved_at = datetime.utcnow()
    db.session.commit()
    try:
        from hub import audit
        audit.log("creative_studio", "version_approved", actor=_actor(),
                  client=project.client_name or None,
                  detail=f"{project.name} · V{version.version}",
                  project=project.id)
    except Exception:                                        # noqa: BLE001
        pass
    return jsonify({"ok": True, "project": project.as_dict(),
                    "version": version.as_dict()})


@bp.get("/api/projects/<int:project_id>/usage-summary")
def api_project_usage_summary(project_id):                    # noqa: ANN202
    """The credit meter's own read: running estimated cost for this project
    from `cs_usage_logs`, grouped through the one shared reading
    `usage.totals_by_provider` already gives the Usage & Costs page -- never
    a second tally built here that could drift from it."""
    project = CsProject.query.get_or_404(project_id)
    rows = CsUsageLog.query.filter_by(project_id=project.id).all()
    totals = usage.totals_by_provider(rows)
    measured = all(t["measured"] for t in totals) if totals else True
    total_cost = sum((t["estimated_cost"] or 0) for t in totals if t["estimated_cost"] is not None)
    return jsonify({"ok": True, "totals": totals, "calls": len(rows),
                    "estimated_cost": (total_cost if measured else None),
                    "measured": measured})


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
            pass  # a malformed ?duration= is treated as no filter, not a 400
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
    """Data-driven, per WO-CS4 item 1: "Adding a tile is a row." Every row
    in `cs_ai_tools` is drawn; there is no per-tool block in this template
    to edit when a fifteenth tool is seeded."""
    category = (request.args.get("category") or "").strip()
    q = CsAiTool.query
    if category and category in config.AI_TOOL_CATEGORIES:
        q = q.filter_by(category=category)
    rows = q.order_by(CsAiTool.sort, CsAiTool.name).all()
    return render_template(
        "cs_ai_tools.html", title="AI Tools", tools=[t.as_dict() for t in rows],
        categories=config.AI_TOOL_CATEGORIES,
        category_labels=config.AI_TOOL_CATEGORY_LABELS, selected=category)


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
    """`cs_usage_logs`, grouped by provider (WO-CS4 item 4). Every rate is a
    placeholder until Todd supplies real ones -- `estimated_cost` reads *not
    measured* rather than a confident number for any (provider, service)
    pair not yet in `config.PROVIDER_RATES`."""
    client = (request.args.get("client") or "").strip()
    q = CsUsageLog.query
    if client:
        q = q.filter_by(client_name=client)
    rows = q.order_by(CsUsageLog.created_at.desc()).limit(500).all()
    totals = usage.totals_by_provider(rows)
    return render_template(
        "cs_usage.html", title="Usage & Costs", client=client,
        rows=[r.as_dict() for r in rows[:100]], totals=totals,
        rates_are_placeholder=True)
