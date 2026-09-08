"""Creative Studio routes.

Foundation only, per WO-CS1: the front door, the client picker, projects, the
extended Brand Kit, and the Media Library with its Cloudinary backfill.
Templates, AI Tools, Approvals and Usage are placeholder pages behind the
same login -- real content lands in WO-CS2 through WO-CS6.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from . import brand_ext, config, jobs
from .db import db
from .models import CreativeJob, CsMediaAsset, CsProject, CsProjectVersion

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

    project = CsProject(client_name=client, name=name[:300], creative_type=ctype,
                        template_id=str(data.get("template_id") or ""),
                        duration=data.get("duration"),
                        aspect_ratio=str(data.get("aspect_ratio") or "16:9"),
                        status="Draft", created_by=_actor())
    project.brief = data.get("brief") or {}
    db.session.add(project)
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "project_created", actor=_actor(),
                  client=client or None, project=name, creative_type=ctype)
    except Exception:                                     # noqa: BLE001
        pass

    return jsonify({"ok": True, "project": project.as_dict()})


@bp.get("/projects/<int:project_id>")
def project_detail(project_id):                            # noqa: ANN202
    project = CsProject.query.get_or_404(project_id)
    versions = project.versions.order_by(CsProjectVersion.version.desc()).all()
    return render_template("cs_project_detail.html", title=project.name,
                           project=project.as_dict(),
                           versions=[v.as_dict() for v in versions],
                           statuses=config.PROJECT_STATUSES)


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
    return render_template("cs_coming_soon.html", title="Templates",
                           heading="Templates",
                           body="The template gallery ships in WO-CS2. It "
                                "will read from cs_templates the same way "
                                "this dashboard's creative-type cards read "
                                "from a config list -- adding a template "
                                "will be a data row, not a code change.")


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
