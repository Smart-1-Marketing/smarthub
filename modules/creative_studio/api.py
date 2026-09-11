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
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, url_for

from . import binder, brand_ext, campaign_spec, config, jobs, layouts, resolver, usage
from .db import db
from .models import (CreativeJob, CsAiTool, CsCampaign, CsCampaignAsset, CsMediaAsset,
                     CsProject, CsProjectVersion, CsShare, CsShareDecision, CsTemplate,
                     CsTemplateScene, CsTemplateVariable, CsUsageLog)

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


# --------------------------------------------------------------- variations

# The blocking rule WO-CS7 states: "A 9:16 variant with text outside
# 14/35/6 fails; a 1:1 whose CTA sits in the bottom 12% warns." 9:16's
# numbers are a platform's own UI overlay (`layouts.SAFE_ZONES`'s own note),
# so text placed there is not merely tight, it is covered; every other
# aspect's margin is a house legibility inset and stays advisory.
_BLOCKING_SAFE_ZONE_ASPECTS = ("9:16",)


def _scene_layout_specs(project):
    """(layout_key, layer_values, chrome) for every scene of this project's
    bound storyboard that carries a Creative Studio layout -- a scene the
    script pipeline wrote (no `layout_key`) contributes nothing, the same
    "this is not this work order's to touch" rule `binder.bind_variation()`
    applies to reframing."""
    from modules.commercial_builder.models import CommercialProject as CbProject
    from modules.commercial_builder.models import Scene as CbScene
    if not project.cb_project_id:
        return []
    cb_project = CbProject.query.get(project.cb_project_id)
    if cb_project is None:
        return []
    out = []
    for scene in cb_project.scenes.order_by(CbScene.order_index).all():
        meta = scene.asset_meta or {}
        layout_key = meta.get("layout_key")
        if layout_key:
            out.append((layout_key, meta.get("layers") or {}, meta.get("chrome") or {}))
    return out


def _safe_zone_findings(project, aspect: str) -> list[dict]:
    findings = []
    for layout_key, layer_values, chrome in _scene_layout_specs(project):
        findings.extend(layouts.check_safe_zone(
            layout_key, aspect, layer_values,
            logo_url=chrome.get("logo_url", ""), phone=chrome.get("phone", ""),
            website=chrome.get("website", "")))
    return findings


@bp.get("/api/projects/<int:project_id>/variations")
def api_list_variations(project_id):                          # noqa: ANN202
    """Every variation ever created from this project, newest first -- the
    panel below the version row reads this to draw preview thumbnails and
    poll the jobs still building them."""
    project = CsProject.query.get_or_404(project_id)
    rows = (CsProject.query.filter_by(parent_project_id=project.id)
           .order_by(CsProject.created_at.desc()).all())
    return jsonify({"ok": True, "variations": [p.as_dict() for p in rows]})


@bp.post("/api/projects/<int:project_id>/versions/<int:version_number>/variations")
def api_create_variations(project_id, version_number):        # noqa: ANN202
    """Create Variations -- WO-CS7 item 2. One new `cs_projects` row per
    requested aspect (plus the static link image), each queued for a
    single-frame preview -- kind="variant" -- rather than a video render:
    "Preview all" happens before "Render selected" ever spends a Creatomate
    video call, the whole reason a preview render exists as its own,
    cheaper thing.

    `version_number` is read to require a real version exists (there is
    nothing to make a variation FROM before a first render), but a
    variation is built from the parent's CURRENT storyboard, not a frozen
    copy of that one render -- the same distinction `bind()` already draws
    between "the template as it stood" and "the template as it now reads."
    """
    project = CsProject.query.get_or_404(project_id)
    CsProjectVersion.query.filter_by(
        project_id=project.id, version=version_number).first_or_404()
    if not project.cb_project_id:
        return jsonify({"ok": False, "error": "Open this project in the "
                        "Storyboard Editor before creating variations."}), 400

    data = request.get_json(silent=True) or {}
    aspects = [a for a in (data.get("aspects") or [])
              if a in ("16:9", "9:16", "1:1", "4:5")]
    link_image = bool(data.get("link_image"))
    if not aspects and not link_image:
        return jsonify({"ok": False, "error": "Pick at least one size."}), 400

    created, refused = [], []
    for aspect in aspects:
        findings = _safe_zone_findings(project, aspect)
        if findings and aspect in _BLOCKING_SAFE_ZONE_ASPECTS:
            refused.append({"aspect": aspect, "findings": findings})
            continue
        child = CsProject(
            client_name=project.client_name, name=f"{project.name} — {aspect}",
            creative_type=project.creative_type, template_id=project.template_id,
            template_version=project.template_version, duration=project.duration,
            aspect_ratio=aspect, status="Draft",
            parent_project_id=project.id, variation_kind="aspect",
            created_by=_actor())
        db.session.add(child)
        db.session.commit()
        job = jobs.enqueue("variant", project_id=child.id, client_name=project.client_name,
                           created_by=_actor())
        created.append({"project": child.as_dict(), "job": job.as_dict(),
                        "safe_zone_warnings": findings})

    if link_image:
        child = CsProject(
            client_name=project.client_name, name=f"{project.name} — link image",
            creative_type=project.creative_type, template_id=project.template_id,
            template_version=project.template_version, duration=0,
            aspect_ratio="1200x628", status="Draft",
            parent_project_id=project.id, variation_kind="link_image",
            created_by=_actor())
        db.session.add(child)
        db.session.commit()
        job = jobs.enqueue("variant", project_id=child.id, client_name=project.client_name,
                           created_by=_actor())
        created.append({"project": child.as_dict(), "job": job.as_dict(),
                        "safe_zone_warnings": []})

    if created:
        try:
            from hub import audit
            audit.log("creative_studio", "variations_created", actor=_actor(),
                      client=project.client_name or None,
                      detail=f"{len(created)} variation(s) from V{version_number}",
                      project=project.id)
        except Exception:                                    # noqa: BLE001
            pass

    return jsonify({"ok": True, "created": created, "refused": refused})


# --------------------------------------------------------------- WO-CS8: campaigns

@bp.get("/campaigns")
def campaigns_page():                                        # noqa: ANN202
    rows = CsCampaign.query.order_by(CsCampaign.created_at.desc()).limit(200).all()
    out = []
    for row in rows:
        d = row.as_dict()
        d["status"] = campaign_spec.status_of(
            [CsProject.query.get(a.project_id).status
             for a in row.assets.all() if CsProject.query.get(a.project_id)])
        out.append(d)
    return render_template("cs_campaigns.html", title="Campaigns", campaigns=out)


@bp.post("/api/campaigns")
def api_create_campaign():                                    # noqa: ANN202
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name the campaign."}), 400

    client = str(data.get("client_name") or "").strip()
    if client:
        try:
            from hub.clients_registry import find_client
            hit = find_client(client)
        except Exception:                                    # noqa: BLE001
            hit = None
        if not hit:
            return jsonify({"ok": False, "error": "That is not a client on "
                            "file. Leave it blank for a generic Smart 1 "
                            "asset, or pick one from the list."}), 400
        client = hit.get("name") or client

    def _date(key):
        raw = str(data.get(key) or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return None

    campaign = CsCampaign(
        client_name=client, name=name[:300],
        start=_date("start"), end=_date("end"),
        offer=str(data.get("offer") or "").strip(),
        cta=str(data.get("cta") or "").strip()[:300],
        created_by=_actor())
    db.session.add(campaign)
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "campaign_created", actor=_actor(),
                  client=client or None, project=campaign.name)
    except Exception:                                        # noqa: BLE001
        pass

    return jsonify({"ok": True, "campaign": campaign.as_dict()})


def _radio_assets_for(campaign) -> list[dict]:
    """Radio script sets belonging to this campaign's client, read-only --
    WO-CS8 item 6. Matched EXACTLY, never a substring: `hub.client_key`'s
    own rule, applied here because `RadioScriptSet.client_name` is a bare
    string with no id of its own to join on, the same shape this file's own
    `client_name` is."""
    if not campaign.client_name:
        return []
    try:
        from hub.client_key import same_client
        from modules.radio_scripts.models import RadioScriptSet
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    try:
        rows = RadioScriptSet.query.order_by(RadioScriptSet.created_at.desc()).limit(500).all()
    except Exception:                                        # noqa: BLE001
        return []
    for row in rows:
        if same_client(campaign.client_name, "", row.client_name or "", ""):
            out.append(row.to_dict(full=False))
    return out


def _asset_row(asset, campaign) -> dict:
    project = CsProject.query.get(asset.project_id)
    row = asset.as_dict()
    if project is None:
        row["project"] = None
        return row
    row["project"] = project.as_dict()
    row["differs"] = campaign_spec.asset_differs(project.brief, campaign.offer, campaign.cta)
    return row


@bp.get("/campaigns/<int:campaign_id>")
def campaign_detail(campaign_id):                             # noqa: ANN202
    campaign = CsCampaign.query.get_or_404(campaign_id)
    assets = [_asset_row(a, campaign) for a in campaign.assets.all()]
    status = campaign_spec.status_of([a["project"]["status"] for a in assets if a["project"]])
    return render_template(
        "cs_campaign_detail.html", title=campaign.name,
        campaign=campaign.as_dict(), status=status, assets=assets,
        radio_assets=_radio_assets_for(campaign),
        channels=config.CHANNELS, channel_labels=config.CHANNEL_LABELS,
        confirm_threshold=config.batch_confirm_threshold_usd())


@bp.get("/api/campaigns/<int:campaign_id>")
def api_get_campaign(campaign_id):                            # noqa: ANN202
    campaign = CsCampaign.query.get_or_404(campaign_id)
    assets = [_asset_row(a, campaign) for a in campaign.assets.all()]
    status = campaign_spec.status_of([a["project"]["status"] for a in assets if a["project"]])
    row = campaign.as_dict()
    row["status"] = status
    row["assets"] = assets
    return jsonify({"ok": True, "campaign": row})


@bp.post("/api/campaigns/<int:campaign_id>/assets")
def api_add_campaign_asset(campaign_id):                      # noqa: ANN202
    """Add one asset -- WO-CS8 item 2. Resolves a seed template for the
    channel asked for and creates the project it becomes; nothing renders
    and no model is called here."""
    campaign = CsCampaign.query.get_or_404(campaign_id)
    data = request.get_json(silent=True) or {}

    channel = str(data.get("channel") or "").strip()
    if channel and channel not in config.CHANNELS:
        return jsonify({"ok": False, "error": "Unknown channel."}), 400

    try:
        duration = int(data.get("duration") or 30)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Duration must be a number of seconds."}), 400

    industry = str(data.get("industry") or "general").strip() or "general"
    aspect_ratio = str(data.get("aspect_ratio") or "16:9").strip()
    creative_type = str(data.get("creative_type") or "video_commercial").strip()
    role = str(data.get("role") or "").strip()[:40]

    asset, error = binder.create_campaign_asset(
        campaign, industry=industry, duration=duration, aspect_ratio=aspect_ratio,
        creative_type=creative_type, channel=channel, role=role, created_by=_actor())
    if asset is None:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "asset": _asset_row(asset, campaign)})


@bp.post("/api/campaigns/<int:campaign_id>/generate-all")
def api_generate_all_drafts(campaign_id):                     # noqa: ANN202
    """"Generate all drafts" -- WO-CS8 item 3. ONE campaign-level script
    call; every asset's own brief is derived from it and its storyboard
    auto-built, with no further model call. Queued -- writing a whole
    campaign's brief and binding several storyboards is not request-speed
    work, house rule 4."""
    campaign = CsCampaign.query.get_or_404(campaign_id)
    if campaign.assets.count() == 0:
        return jsonify({"ok": False, "error": "Add at least one asset "
                        "before generating drafts."}), 400
    job = jobs.enqueue("campaign_draft", client_name=campaign.client_name,
                       payload={"campaign_id": campaign.id}, created_by=_actor())
    return jsonify({"ok": True, "job": job.as_dict()})


@bp.post("/api/campaigns/<int:campaign_id>/render")
def api_batch_render(campaign_id):                            # noqa: ANN202
    """Batch render -- WO-CS8 item 4. One render job per project id,
    sharing a `batch_id`; a QC failure on one asset is reported and skipped
    rather than cancelling the rest, the same "one asset failing never
    cancels the rest" rule the work order states outright.

    Above `config.batch_confirm_threshold_usd()`, the campaign's own name
    has to be typed back -- the rule `modules/image_picker` and
    `modules/suite_panel` already use for a press that costs real money and
    cannot be undone by clicking again: a checkbox is agreed to without
    reading, a name has to be read to be typed.
    """
    from modules.commercial_builder.models import Client as CbClient
    from modules.commercial_builder.models import CommercialProject as CbProject
    from modules.commercial_builder.models import Scene as CbScene
    from modules.commercial_builder.services import qc_service

    campaign = CsCampaign.query.get_or_404(campaign_id)
    data = request.get_json(silent=True) or {}
    requested_ids = [int(pid) for pid in (data.get("project_ids") or [])
                     if str(pid).isdigit()]
    campaign_project_ids = {a.project_id for a in campaign.assets.all()}
    project_ids = [pid for pid in requested_ids if pid in campaign_project_ids]
    if not project_ids:
        return jsonify({"ok": False, "error": "Pick at least one asset "
                        "on this campaign to render."}), 400

    estimate = campaign_spec.render_estimate(len(project_ids))
    if campaign_spec.needs_confirmation(estimate):
        typed = str(data.get("confirm") or "").strip()
        if typed != campaign.name:
            return jsonify({
                "ok": False, "error": "confirm_required",
                "estimate_usd": estimate,
                "message": f"This batch is estimated at ${estimate:.2f}. "
                          f"Type the campaign's name, \"{campaign.name}\", to render it.",
            }), 409

    batch_id = secrets.token_hex(8)
    rendered, refused = [], []
    for project_id in project_ids:
        project = CsProject.query.get(project_id)
        if project is None or not project.cb_project_id:
            refused.append({"project_id": project_id,
                            "error": "Open this project in the Storyboard Editor first."})
            continue
        try:
            cb_project = CbProject.query.get(project.cb_project_id)
            cb_client = CbClient.query.get(cb_project.client_id) if cb_project else None
        except Exception as exc:                              # noqa: BLE001
            refused.append({"project_id": project_id, "error": type(exc).__name__})
            continue
        if cb_project is None or cb_client is None:
            refused.append({"project_id": project_id,
                            "error": "This project's storyboard no longer exists."})
            continue
        scenes = [s.to_dict() for s in cb_project.scenes.order_by(CbScene.order_index).all()]
        qc = qc_service.run_qc(cb_project.to_dict(include_scenes=False), cb_client.to_dict(), scenes)
        if not qc.get("_all_passed"):
            refused.append({"project_id": project_id,
                            "error": "QC checks failed.", "qc_results": qc})
            continue
        fmt = (cb_project.formats or ["16:9"])[0]
        job = jobs.enqueue("render", project_id=project.id, client_name=project.client_name,
                           payload={"format": fmt, "batch_id": batch_id}, created_by=_actor())
        job.batch_id = batch_id
        db.session.commit()
        rendered.append({"project_id": project_id, "job": job.as_dict()})

    if rendered:
        try:
            from hub import audit
            audit.log("creative_studio", "campaign_batch_render", actor=_actor(),
                      client=campaign.client_name or None, project=campaign.name,
                      detail=f"{campaign.name}: {len(rendered)} of {len(project_ids)} "
                            f"assets queued (batch {batch_id})")
        except Exception:                                     # noqa: BLE001
            pass

    return jsonify({"ok": True, "batch_id": batch_id, "estimate_usd": estimate,
                    "rendered": rendered, "refused": refused})


@bp.get("/api/campaigns/<int:campaign_id>/batch/<batch_id>")
def api_batch_status(campaign_id, batch_id):                  # noqa: ANN202
    campaign = CsCampaign.query.get_or_404(campaign_id)
    jobs_rows = CreativeJob.query.filter_by(batch_id=batch_id).order_by(CreativeJob.id).all()
    if not jobs_rows:
        return jsonify({"ok": False, "error": "No batch with that id on this campaign."}), 404
    done = sum(1 for j in jobs_rows if j.state == "complete")
    failed = sum(1 for j in jobs_rows if j.state == "failed")
    return jsonify({"ok": True, "batch_id": batch_id, "total": len(jobs_rows),
                    "done": done, "failed": failed,
                    "jobs": [j.as_dict() for j in jobs_rows]})


@bp.get("/api/campaigns/<int:campaign_id>/shares")
def api_list_campaign_shares(campaign_id):                    # noqa: ANN202
    from modules.commercial_builder import review_spec
    shares = (CsShare.query.filter_by(kind="campaign", subject_id=campaign_id)
             .order_by(CsShare.round_no.desc(), CsShare.id.desc()).all())
    rows = []
    for share in shares:
        row = share.to_dict()
        row["url"] = _share_url(share.token)
        row["round_state"] = review_spec.round_state(share.round_no)
        rows.append(row)
    return jsonify({"ok": True, "shares": rows,
                    "next_round": review_spec.round_state(len(rows) + 1)})


@bp.post("/api/campaigns/<int:campaign_id>/share")
def api_send_campaign_for_approval(campaign_id):              # noqa: ANN202
    """Send the whole campaign for approval -- WO-CS8 item 5. One
    `kind="campaign"` share whose `subject_id` is the campaign, covering
    every asset that has something to show; per-asset decisions are what
    `CsShareDecision.asset_project_id` (added for exactly this) is for. The
    round counter and cap are `CsShare`'s own, read from
    `modules.commercial_builder.review_spec` exactly as a single version's
    review already does -- one rule, never a second copy of it for a
    campaign."""
    from modules.commercial_builder import review_spec
    campaign = CsCampaign.query.get_or_404(campaign_id)
    assets = campaign.assets.all()
    if not assets:
        return jsonify({"ok": False, "error": "Add at least one asset "
                        "before sending this campaign for approval."}), 400
    body = request.get_json(silent=True) or {}

    previous = CsShare.query.filter_by(kind="campaign", subject_id=campaign.id).all()
    round_no = len(previous) + 1
    for old in previous:
        old.revoked = True

    # A share row still needs a project_id -- a representative one, never
    # read as the subject for this kind. `CsShare`'s own docstring says why
    # `kind` + `subject_id` exist: so a second kind added later needs no
    # second table, not so `project_id` stops meaning anything.
    share = CsShare(token=secrets.token_urlsafe(24), kind="campaign",
                    subject_id=campaign.id, project_id=assets[0].project_id,
                    round_no=round_no, created_by=_actor(),
                    message=str(body.get("message") or "").strip()[:2000])
    db.session.add(share)
    for a in assets:
        p = CsProject.query.get(a.project_id)
        if p is not None:
            p.status = "Client Review"
    db.session.commit()

    state = review_spec.round_state(round_no)
    if state["over"]:
        _log_review("creative_review_rounds_exceeded", campaign,
                    detail=f"Round {round_no} on {campaign.name}")
    _log_review("creative_review_sent", campaign,
               detail=f"{state['label']} · {campaign.name} ({len(assets)} assets)")

    row = share.to_dict()
    row["url"] = _share_url(share.token)
    row["round_state"] = state
    row["delivery"] = _deliver_review(
        campaign, share, row["url"],
        name=str(body.get("reviewer_name") or "").strip()[:200],
        email=str(body.get("reviewer_email") or "").strip()[:200])
    return jsonify({"ok": True, "share": row})


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


# --------------------------------------------------------------- review (WO-CS6)

# Registered in hub/lead_tags.py with the Suite workflow named beside it --
# the source tag is what a Suite workflow triggers on to actually send the
# email, and the Hub has no mail sender of its own. Kept in this file rather
# than imported from review_routes.py (where the public page lives) because
# it is the `capture_and_deliver()` call site below that names it, and
# test_lead_delivery.py's sweep reads a source tag from the AST of the file
# that calls it -- a constant imported across a module boundary is a value
# it cannot follow, which is exactly the "prose is not a call site" trap
# CLAUDE.md names for hub/config.py's own drift check.
REVIEW_SOURCE = "creative_review_ready"


def _share_url(token: str) -> str:
    # cs_review is registered directly on the hub app (see __init__.py),
    # never nested inside this blueprint -- so its endpoint carries no
    # "creative_studio." prefix. linkcheck caught the wrong name here: the
    # try/except below silently absorbed a BuildError and fell back to the
    # literal path, which happens to be byte-identical to what url_for would
    # have built, so nothing on screen or in a test noticed.
    try:
        path = url_for("cs_review.client_review", token=token)
    except Exception:                                       # noqa: BLE001
        path = f"/review/{token}"
    return request.host_url.rstrip("/") + path


@bp.get("/api/projects/<int:project_id>/versions/<int:version_number>/shares")
def api_list_shares(project_id, version_number):              # noqa: ANN202
    """Every round sent on this version, newest first, with the verdict on
    each -- the panel below the version row reads this."""
    from modules.commercial_builder import review_spec
    version = CsProjectVersion.query.filter_by(
        project_id=project_id, version=version_number).first_or_404()
    shares = (CsShare.query.filter_by(kind="render", subject_id=version.id)
             .order_by(CsShare.round_no.desc(), CsShare.id.desc()).all())
    rows = []
    for share in shares:
        row = share.to_dict()
        row["url"] = _share_url(share.token)
        row["verdict"] = review_spec.verdict(row["decisions"])
        row["round_state"] = review_spec.round_state(share.round_no)
        rows.append(row)
    return jsonify({"ok": True, "shares": rows,
                    "next_round": review_spec.round_state(len(rows) + 1)})


@bp.post("/api/projects/<int:project_id>/versions/<int:version_number>/share")
def api_send_for_approval(project_id, version_number):        # noqa: ANN202
    """Mint a token for this version and (optionally) file the reviewer as a
    Suite contact tagged for the review workflow -- WO-CS6 item 1.

    A new token every round, never a reopened one: `CsShare`'s own docstring
    gives the reason, the same one `ReviewShare` in the Commercial Builder
    already states -- a link that has been answered is the record of that
    answer, and handing the same URL out again for round two would overwrite
    round one's decision with no trace there had been one.
    """
    from modules.commercial_builder import review_spec
    project = CsProject.query.get_or_404(project_id)
    version = CsProjectVersion.query.filter_by(
        project_id=project_id, version=version_number).first_or_404()
    body = request.get_json(silent=True) or {}

    previous = CsShare.query.filter_by(kind="render", subject_id=version.id).all()
    round_no = len(previous) + 1
    for old in previous:
        old.revoked = True

    share = CsShare(token=secrets.token_urlsafe(24), kind="render",
                    subject_id=version.id, project_id=project.id,
                    round_no=round_no, created_by=_actor(),
                    message=str(body.get("message") or "").strip()[:2000])
    db.session.add(share)
    project.status = "Client Review"
    db.session.commit()

    state = review_spec.round_state(round_no)
    if state["over"]:
        _log_review("creative_review_rounds_exceeded", project,
                    detail=f"Round {round_no} on {project.name}")
    _log_review("creative_review_sent", project,
               detail=f"{state['label']} · {project.name} V{version.version}")

    row = share.to_dict()
    row["url"] = _share_url(share.token)
    row["round_state"] = state
    row["delivery"] = _deliver_review(
        project, share, row["url"],
        name=str(body.get("reviewer_name") or "").strip()[:200],
        email=str(body.get("reviewer_email") or "").strip()[:200])
    return jsonify({"ok": True, "share": row})


@bp.post("/api/projects/<int:project_id>/shares/<int:share_id>/revoke")
def api_revoke_share(project_id, share_id):                   # noqa: ANN202
    """Switch a link off. What was said on it is kept -- revoking is not
    deleting."""
    share = CsShare.query.filter_by(id=share_id, project_id=project_id).first_or_404()
    share.revoked = True
    db.session.commit()
    _log_review("creative_review_revoked", CsProject.query.get(project_id),
               detail=f"Round {share.round_no} link revoked.")
    return jsonify({"ok": True, "share": share.to_dict()})


def _deliver_review(project, share, url: str, *, name: str, email: str) -> dict:
    """Write the reviewer into Smart 1 Suite, tagged for the review workflow.

    The same shape `modules.commercial_builder.routes.review._deliver_review`
    already uses: `hub.leads.capture_and_deliver`, never a second route to
    Suite. Three answers, never folded -- `sent`, `held` and `skipped` mean
    different things and a rep reading one has to be able to tell them apart.
    """
    if not email:
        return {"state": "skipped", "sent": False,
                "note": "No email given, so the link was not filed for sending — "
                        "copy it and send it yourself."}
    try:
        from hub import leads as _hub_leads
    except Exception:                                       # noqa: BLE001
        return {"state": "held", "sent": False,
                "note": "Running outside the Hub, so nothing could be filed in Suite."}
    try:
        out = _hub_leads.capture_and_deliver(
            source=REVIEW_SOURCE, page=project.name or f"Project #{project.id}",
            fields={"name": name, "email": email, "company": project.client_name or "",
                    "round": str(share.round_no)},
            client=project.client_name or "",
            meta={"report_url": url, "project": project.id, "share_id": share.id,
                  "round": share.round_no, "kind": "creative_review"})
    except Exception as exc:                                # noqa: BLE001
        _log_review("creative_review_delivery_failed", project,
                   detail=f"Round {share.round_no}: {type(exc).__name__}")
        return {"state": "held", "sent": False,
                "note": f"The link exists, but filing it in Suite failed ({type(exc).__name__}). "
                        "Send it by hand."}
    sent = bool(out.get("delivered"))
    _log_review("creative_review_delivered" if sent else "creative_review_delivery_held",
               project, detail=f"Round {share.round_no} to {email}"
               + ("" if sent else f" — {out.get('note', '')}"))
    return {"state": "sent" if sent else "held", "sent": sent,
            "lead_id": out.get("lead_id", ""),
            "note": (f"Filed in Smart 1 Suite as a contact tagged {REVIEW_SOURCE}; the "
                     "review email goes out from the Suite workflow on that tag."
                     if sent else
                     "The link exists, but it did not reach Suite: "
                     + str(out.get("note") or "") + " It is queued on /sales/leads; "
                     "send the link by hand meanwhile.")}


def _log_review(event, project, detail=""):
    """Never costs the write it describes -- see `_log_render` in
    `modules/commercial_builder/routes/render.py` for why the detail string
    is built inside the try rather than by the caller."""
    try:
        from hub import audit
        audit.log("creative_studio", event, actor=_actor(),
                  client=(getattr(project, "client_name", "") or None),
                  detail=detail, project=getattr(project, "id", None))
    except Exception:                                       # noqa: BLE001
        pass


@bp.get("/api/reviews/waiting")
def api_reviews_waiting():                                    # noqa: ANN202
    """Every live round across every project, sorted into who is waiting on
    whom -- the dashboard card and /approvals both read this. Never raises:
    `review_spec.inbox_unmeasured()` is what a failed read answers with,
    because a clean zero over a table that would not answer is the one thing
    this must not draw."""
    from modules.commercial_builder import review_spec
    try:
        shares = CsShare.query.filter_by(revoked=False).order_by(CsShare.id.desc()).all()
        rows = []
        for share in shares:
            project = CsProject.query.get(share.project_id)
            if project is None:
                continue
            decisions = [d.to_dict() for d in share.decisions.all()]
            verdict = review_spec.verdict(decisions)
            version = (CsProjectVersion.query.get(share.subject_id)
                      if share.kind == "render" else None)
            rows.append({
                "share_id": share.id, "project_id": project.id,
                "project_name": project.name, "client": project.client_name or "",
                "version": version.version if version else None,
                "round_no": share.round_no or 1,
                "sent_at": share.created_at.isoformat() if share.created_at else None,
                "sent_by": share.created_by or "",
                "opened_count": share.opened_count or 0,
                "last_opened_at": (share.last_opened_at.isoformat()
                                   if share.last_opened_at else None),
                "answered": verdict["answered"], "outcome": verdict["outcome"],
                "color": verdict["color"], "by": verdict["by"],
                "conflicting": verdict["conflicting"],
                "comments": share.comments.count(),
                "filed": project.status in ("Approved", "Archived"),
                "url": f"/creative-studio/projects/{project.id}",
            })
        return jsonify({"ok": True, **review_spec.inbox(rows)})
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": True, **review_spec.inbox_unmeasured(str(exc))})


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
    """Open shares across every client -- WO-CS6 item 3. Reads the same
    `api_reviews_waiting()` the dashboard card reads, so the two cannot come
    to disagree about what is waiting on whom."""
    data = api_reviews_waiting().get_json()
    return render_template("cs_approvals.html", title="Approvals", **data)


@bp.get("/usage")
def usage_page():                                             # noqa: ANN202
    """`cs_usage_logs`, grouped by provider (WO-CS4 item 4), filtered and
    tiled (WO-CS6 item 4). Every rate is a placeholder until Todd supplies
    real ones -- `estimated_cost` reads *not measured* rather than a
    confident number for any (provider, service) pair not yet in
    `config.PROVIDER_RATES`."""
    client = (request.args.get("client") or "").strip()
    user = (request.args.get("user") or "").strip()
    provider = (request.args.get("provider") or "").strip()
    project_id = (request.args.get("project_id") or "").strip()
    period = (request.args.get("period") or "").strip()   # "today" | "month" | ""

    q = CsUsageLog.query
    if client:
        q = q.filter_by(client_name=client)
    if user:
        q = q.filter_by(created_by=user)
    if provider:
        q = q.filter_by(provider=provider)
    if project_id:
        try:
            q = q.filter_by(project_id=int(project_id))
        except ValueError:
            project_id = ""
    now = datetime.utcnow()
    if period == "today":
        q = q.filter(CsUsageLog.created_at >= now.replace(
            hour=0, minute=0, second=0, microsecond=0))
    elif period == "month":
        q = q.filter(CsUsageLog.created_at >= now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0))

    rows = q.order_by(CsUsageLog.created_at.desc()).limit(500).all()
    totals = usage.totals_by_provider(rows)
    tile_data = usage.tiles(rows)

    # Filter option values come from what has actually been logged, never a
    # fixed list -- the rule CLAUDE.md states for every gallery filter here:
    # a new provider must not need a template edit to become pickable.
    all_clients = sorted({c for (c,) in db.session.query(CsUsageLog.client_name)
                          .distinct() if c})
    all_users = sorted({c for (c,) in db.session.query(CsUsageLog.created_by)
                        .distinct() if c})
    all_providers = sorted({c for (c,) in db.session.query(CsUsageLog.provider)
                            .distinct() if c})

    return render_template(
        "cs_usage.html", title="Usage & Costs",
        client=client, user=user, provider=provider, project_id=project_id,
        period=period, rows=[r.as_dict() for r in rows[:100]],
        totals=totals, tiles=tile_data,
        all_clients=all_clients, all_users=all_users, all_providers=all_providers,
        rates_are_placeholder=True)
