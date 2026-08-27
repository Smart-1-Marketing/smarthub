"""Commercial project lifecycle: start -> brief -> concepts -> script ->
CTA/music -> variations (spec sections 1, 3, 4, 11, 14, 15)."""

from flask import Blueprint, jsonify, request

from .. import client_link
from ..config import (COMMERCIAL_LENGTHS, OUTPUT_FORMATS, COMMERCIAL_TYPES, TONE_OPTIONS,
                      PLATFORMS, DEFAULT_PLATFORM, MAX_LENGTHS_PER_BUILD,
                      qr_eligible, qr_required, get_structure, length_warning)
from ..db import db
from ..models import Client, CommercialProject, Scene, Campaign, Variation
from ..services import openai_service, qrcode_service, cloudinary_service, qc_service

# Where a QR code points and whose account the scan is filed under. In hub/
# rather than here because the answer is the same for anything this Hub puts a
# code on, and because the "HighLevel publishes no QR endpoint" finding needed
# a home somebody would find before asking again.
try:
    from hub import qr_codes
except Exception:                                       # noqa: BLE001 — standalone
    qr_codes = None

bp = Blueprint("cb_projects", __name__, url_prefix="/api/projects")

try:  # deliverables belong on the client's 360 record
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _cb_log(*_a, **_k):
        return None


# ---------------------------------------------------------------------------
# 1. Start Commercial
# ---------------------------------------------------------------------------
@bp.post("")
def start_commercial():
    """Start one commercial, or one per length in a single press.

    `length_seconds` still starts one, unchanged, because everything that
    already calls this route sends it. `lengths` starts several: a client
    almost never wants only a :30, and building the :15 afterwards meant
    walking the whole wizard a second time and getting a different concept out
    of it — the same brief, quoted two ways, which is the failure
    `modules/sales_builder` exists to have fixed once already.

    Several lengths means several projects rather than one project with four
    scripts, because a :15 and a :60 are genuinely different edits: different
    beats, different word budgets, different scenes. They are tied together by
    a Campaign so they share a concept, which is what `expand_campaign` below
    was written for and what nothing has ever called.
    """
    data = request.get_json(force=True) or {}
    client_id = data.get("client_id")
    if not client_id or not Client.query.get(client_id):
        return jsonify({"ok": False, "error": "A valid client_id is required."}), 400

    requested = data.get("lengths")
    if requested is None:
        requested = [data.get("length_seconds", 30)]
    lengths, seen = [], set()
    for raw in requested:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value in COMMERCIAL_LENGTHS and value not in seen:
            seen.add(value)
            lengths.append(value)
    if not lengths:
        return jsonify({"ok": False,
                        "error": f"Choose at least one length from {COMMERCIAL_LENGTHS}."}), 400
    if len(lengths) > MAX_LENGTHS_PER_BUILD:
        return jsonify({"ok": False,
                        "error": f"At most {MAX_LENGTHS_PER_BUILD} lengths in one build."}), 400
    lengths.sort()

    formats = data.get("formats") or ["16:9"]
    valid_ids = {f["id"] for f in OUTPUT_FORMATS}
    formats = [f for f in formats if f in valid_ids] or ["16:9"]

    commercial_type = data.get("commercial_type")
    if commercial_type not in {t["id"] for t in COMMERCIAL_TYPES}:
        return jsonify({"ok": False, "error": "Invalid commercial_type."}), 400

    platform = data.get("platform", DEFAULT_PLATFORM)
    if platform not in {p["id"] for p in PLATFORMS}:
        platform = DEFAULT_PLATFORM

    client = Client.query.get(client_id)
    title = (data.get("title") or "").strip()

    # More than one length is a campaign, so they share a concept rather than
    # being quoted three different ways.
    campaign_id = data.get("campaign_id")
    if len(lengths) > 1 and not campaign_id:
        campaign = Campaign(client_id=client_id,
                            name=title or f"{client.name} commercials",
                            master_concept="")
        db.session.add(campaign)
        db.session.flush()
        campaign_id = campaign.id

    projects = []
    for length in lengths:
        project = CommercialProject(
            client_id=client_id,
            title=(f"{title} — :{length:02d}" if title and len(lengths) > 1
                   else title or f"{client.name} :{length:02d}"),
            length_seconds=length, commercial_type=commercial_type, platform=platform,
            campaign_id=campaign_id, status="draft",
        )
        project.formats = formats
        db.session.add(project)
        projects.append(project)
    db.session.commit()

    # What each length is going to cost, and what the published spec says about
    # running it on this buy — both said now rather than after the work.
    notes = []
    for project in projects:
        warning = length_warning(project.length_seconds)
        if warning:
            notes.append({"length": project.length_seconds, "kind": "cost",
                          "message": warning})
        verdict = qc_service.spec_preview(platform, project.length_seconds, formats)
        if not verdict["passed"]:
            notes.append({"length": project.length_seconds, "kind": "spec",
                          "message": verdict["message"]})

    return jsonify({"ok": True,
                    "project": projects[0].to_dict(),
                    "projects": [p.to_dict(include_scenes=False) for p in projects],
                    "campaign_id": campaign_id,
                    "notes": notes}), 201


@bp.get("/spec-preview")
def preview_spec():
    """What the published creative spec says about a spot nobody has built yet.

    Called from the Start page as the length and platform are picked. It is
    advice, never a block: a :60 CTV cut is a real thing to want for a website
    or a lobby screen, and a tool that refused it would be wrong. What it must
    not do is stay quiet until the render.
    """
    platform = request.args.get("platform") or DEFAULT_PLATFORM
    formats = [f for f in (request.args.get("formats") or "16:9").split(",") if f]
    out = []
    for raw in (request.args.get("lengths") or "").split(","):
        try:
            length = int(raw)
        except (TypeError, ValueError):
            continue
        if length not in COMMERCIAL_LENGTHS:
            continue
        verdict = qc_service.spec_preview(platform, length, formats)
        out.append({"length": length, "passed": verdict["passed"],
                    "message": verdict["message"],
                    "cost_warning": length_warning(length)})
    return jsonify({"ok": True, "lengths": out})


@bp.get("")
def list_projects():
    client_id = request.args.get("client_id")
    query = CommercialProject.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    projects = query.order_by(CommercialProject.updated_at.desc()).all()
    return jsonify({"ok": True, "projects": [p.to_dict(include_scenes=False) for p in projects]})


@bp.get("/<int:project_id>")
def get_project(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return jsonify({"ok": True, "project": project.to_dict()})


@bp.get("/<int:project_id>/structure")
def get_structure_guide(project_id):
    """Duration-specific structural blueprint (hook/value/close beats,
    QR/logo-persistence eligibility) — powers the guide rail above the
    storyboard editor. See config.STRUCTURE_TEMPLATES."""
    project = CommercialProject.query.get_or_404(project_id)
    return jsonify({
        "ok": True,
        "beats": get_structure(project.length_seconds, project.platform),
        "qr_eligible": qr_eligible(project.length_seconds),
        "qr_required": qr_required(project.length_seconds, project.platform),
        "platform": project.platform,
    })


@bp.delete("/<int:project_id>")
def delete_project(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    db.session.delete(project)
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 3. Commercial Brief
# ---------------------------------------------------------------------------
@bp.put("/<int:project_id>/brief")
def save_brief(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    brief = {
        "what_advertising": data.get("what_advertising", ""),
        "primary_cta": data.get("primary_cta", ""),
        "landing_page": data.get("landing_page", ""),
        "phone": data.get("phone", ""),
        "target_audience": data.get("target_audience", ""),
        "tone": data.get("tone") if data.get("tone") in TONE_OPTIONS else (data.get("tone") or ""),
    }
    project.brief = brief
    if project.status == "draft":
        project.status = "brief"
    db.session.commit()
    return jsonify({"ok": True, "project": project.to_dict()})



def _with_hub_facts(client) -> dict:
    """The adopted brand profile, plus what the rest of the Hub holds.

    The profile is a copy taken at adoption -- fonts, pronunciation, preferred
    voice -- and it is deliberately one-way, so it does not move when the
    client record does. What it never had is the client's live products, the
    industry on their Knack record and what their last site scan read off
    their own pages, and a model writing a :30 for a client of eleven years
    was working from a name, a color and a tagline.

    `hub/client_context.for_prompt()` is the one reader every AI feature in
    the Hub appends, so a fact added there reaches the commercial, the
    campaign generator and the blog writer alike. It carries what is *not* on
    file with it: a gap a model cannot see is a gap it fills in.

    Never raises, and never writes back to the profile -- adopting is a copy,
    and the copy is the one a person edited.
    """
    profile = client.to_dict()
    try:
        from hub.client_context import for_prompt
        known = for_prompt(client.name or "", client.website or "")
        if known:
            profile["hub_record"] = known
    except Exception:  # noqa: BLE001
        pass
    return profile


@bp.post("/<int:project_id>/concepts")
def generate_concepts(project_id):
    """Generate three materially different concepts from the brief."""
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    if not project.brief or not project.brief.get("what_advertising"):
        return jsonify({"ok": False, "error": "Save a commercial brief before generating concepts."}), 400

    concepts = openai_service.generate_concepts(
        project.brief, _with_hub_facts(client), project.commercial_type)
    project.concepts = concepts
    project.selected_concept_id = None
    project.status = "concepts"
    db.session.commit()
    return jsonify({"ok": True, "concepts": concepts, "live": openai_service.is_live()})


@bp.post("/<int:project_id>/select-concept")
def select_concept(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    concept_id = data.get("concept_id")
    concepts = project.concepts or []
    if not any(c["id"] == concept_id for c in concepts):
        return jsonify({"ok": False, "error": "Unknown concept_id."}), 400
    project.selected_concept_id = concept_id
    db.session.commit()
    return jsonify({"ok": True, "project": project.to_dict()})


# ---------------------------------------------------------------------------
# 4. AI creates the timed script -> generates the initial Scene rows too
# ---------------------------------------------------------------------------
@bp.post("/<int:project_id>/script")
def generate_script(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)

    concept = next((c for c in (project.concepts or []) if c["id"] == project.selected_concept_id), None)
    if not concept:
        return jsonify({"ok": False, "error": "Select a concept before generating a script."}), 400

    qr_enabled = bool((project.cta or {}).get("qr_enabled")) if project.cta else qr_eligible(project.length_seconds)
    script = openai_service.generate_script(concept, project.length_seconds, project.brief, client.to_dict(),
                                             platform=project.platform, qr_enabled=qr_enabled)
    project.script = script
    project.status = "scripted"

    # (Re)build Scene rows from the script. Regenerating the script replaces
    # unlocked scenes only, so a user's manually-approved footage choices
    # for earlier scenes survive a script tweak.
    existing = {s.order_index: s for s in project.scenes.all()}
    for idx, sc in enumerate(script["scenes"]):
        is_last = idx == len(script["scenes"]) - 1
        scene = existing.get(idx)
        if scene and scene.locked:
            continue
        if not scene:
            scene = Scene(project_id=project.id, order_index=idx)
            db.session.add(scene)
        scene.start = sc["start"]
        scene.end = sc["end"]
        scene.narration = sc["voiceover"]
        scene.visual_description = sc["visual"]
        scene.is_cta = is_last
        meta = scene.asset_meta or {}
        meta["beat"] = sc.get("beat")
        scene.asset_meta = meta
        if is_last:
            scene.asset_type = "cta"
    # drop stale scenes beyond the new scene count
    for idx, scene in existing.items():
        if idx >= len(script["scenes"]) and not scene.locked:
            db.session.delete(scene)

    db.session.commit()
    return jsonify({"ok": True, "script": script, "project": project.to_dict(), "live": openai_service.is_live()})


# ---------------------------------------------------------------------------
# 10. Music
# ---------------------------------------------------------------------------
@bp.put("/<int:project_id>/music")
def set_music(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    project.music = {"mood": data.get("mood"), "level": data.get("level", "Medium")}
    db.session.commit()
    return jsonify({"ok": True, "music": project.music})


# ---------------------------------------------------------------------------
# 11. CTA Builder
# ---------------------------------------------------------------------------
@bp.put("/<int:project_id>/cta")
def set_cta(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}

    # :05 bumpers never carry a QR code or a persistent logo bug — per the
    # CTV/YouTube best-practices brief, a 5-second spot is pure brand recall
    # (logo + URL), not a response mechanism.
    eligible = qr_eligible(project.length_seconds)
    qr_enabled = bool(data.get("qr_enabled")) and eligible
    logo_persistent = bool(data.get("logo_persistent", eligible)) and eligible

    website = data.get("website") or client.website or ""

    # Where the code sends somebody, and whose account the scan is filed
    # under. hub/qr_codes.py owns both and refuses to invent a destination —
    # a code that opens the wrong company's website is worse than an end card
    # with no code on it, because nobody proof-reads the thing that scans.
    plan = _qr_plan(project, client, website, data.get("qr_target_url") or "")
    qr_target = plan["target_url"]

    prior_cta = project.cta or {}
    qr_image_url = prior_cta.get("qr_image_url")
    qr_data_url = prior_cta.get("qr_data_url")
    if qr_enabled and qr_target and (qr_target != prior_cta.get("qr_target_url") or not qr_data_url):
        qr_result = qrcode_service.generate_qr(qr_target)
        qr_data_url = qr_result.get("data_url")
        if cloudinary_service.is_live():
            upload = cloudinary_service.upload_asset(qr_result["bytes_io"], client.slug, "logo",
                                                       public_id=f"project-{project_id}-qr", resource_type="image")
            qr_image_url = upload.get("secure_url") or qr_image_url

    project.cta = {
        "style": data.get("style", "logo_centered"),
        "headline": data.get("headline") or client.tagline or "",
        "offer": data.get("offer") or (project.brief or {}).get("what_advertising", ""),
        "website": website,
        "phone": data.get("phone") or client.phone or "",
        "qr_enabled": qr_enabled,
        "qr_required": qr_required(project.length_seconds, project.platform),
        "qr_corner": data.get("qr_corner") or "bottom-right",
        "qr_target_url": qr_target,
        # What the code actually points at before the tracking was added, and
        # which field it came from. Both are printed on the CTA step: a rep
        # who cannot see the destination cannot tell a code aimed at the
        # campaign landing page from one aimed at the home page.
        "qr_destination_url": plan["destination_url"],
        "qr_destination_source": plan["destination_source"],
        "qr_missing": plan["missing"],
        "qr_attribution": plan["attribution"],
        "qr_provider_note": plan["provider_note"],
        "qr_image_url": qr_image_url,
        "qr_data_url": qr_data_url,
        "logo_persistent": logo_persistent,
        "logo_corner": data.get("logo_corner") or "top-left",
        "client": {"name": client.name, "logo_url": client.logo_url,
                   "primary_color": client.primary_color, "secondary_color": client.secondary_color},
    }
    db.session.commit()
    return jsonify({"ok": True, "cta": project.cta})


def _qr_plan(project, client, website, explicit_target):
    """The QR decision for this project, or a flat answer when hub/ is absent.

    Standalone there is no Suite and no attribution to report, so the
    degraded answer says *not measured* rather than drawing a tick over a
    question nothing asked — the rule the provider check and the Google sweep
    both work to.
    """
    if qr_codes is None:
        target = explicit_target or (project.brief or {}).get("landing_page") or website
        return {"target_url": target, "destination_url": target,
                "destination_source": "", "missing": "",
                "attribution": {"state": "unknown", "location_id": "", "account": "",
                                "note": "Not measured — running outside the Hub."},
                "provider_note": ""}

    if explicit_target:
        # A destination typed by hand is still tracked and still attributed,
        # but it is not second-guessed: somebody who pasted a URL meant it.
        plan = qr_codes.plan(landing_page=explicit_target,
                             campaign=project.title or "", platform=project.platform,
                             content=f"{project.length_seconds}s",
                             client_location_id=_suite_location(client),
                             client_name=client.name)
        return plan

    return qr_codes.plan(
        landing_page=(project.brief or {}).get("landing_page") or "",
        cta_website=website, client_website=client.website or "",
        campaign=project.title or "", platform=project.platform,
        content=f"{project.length_seconds}s",
        client_location_id=_suite_location(client), client_name=client.name)


def _suite_location(client):
    try:
        return client_link.suite_location_id(client.name, client.website or "")
    except Exception:                                    # noqa: BLE001
        return ""


@bp.get("/<int:project_id>/qr-plan")
def get_qr_plan(project_id):
    """Where this spot's QR code would point, before anybody saves anything.

    On the CTA step so the destination and the account are visible while the
    decision is being made rather than after it — the same reason the reach
    panel sits beside the target areas in the Proposal Builder.
    """
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    cta = project.cta or {}
    plan = _qr_plan(project, client, cta.get("website") or client.website or "", "")
    return jsonify({"ok": True, "plan": plan,
                    "eligible": qr_eligible(project.length_seconds),
                    "required": qr_required(project.length_seconds, project.platform)})


# ---------------------------------------------------------------------------
# Writing more narration.
#
# The script writer sizes the read once and stops, which is why a :60 came
# back reading like a :30 with pauses in it. This writes more, inside the word
# budget the length actually has — services/openai_service.narration_budget
# computes the room and the prompt is told the number, because a model asked
# to "write a bit more" writes a bit more whether there were four words of
# room or forty.
# ---------------------------------------------------------------------------
@bp.post("/<int:project_id>/narration/expand")
def expand_narration(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}

    scenes = project.scenes.order_by(Scene.order_index).all()
    payload = [s.to_dict() for s in scenes]
    concept = next((c for c in (project.concepts or [])
                    if c.get("id") == project.selected_concept_id), None)

    scene_index = data.get("scene_index")
    if scene_index is not None:
        try:
            scene_index = int(scene_index)
        except (TypeError, ValueError):
            scene_index = None

    result = openai_service.expand_narration(
        payload, project.length_seconds, project.brief or {}, client.to_dict(),
        concept=concept, scene_index=scene_index)

    written = 0
    for row in result.get("scenes") or []:
        scene = next((s for s in scenes if s.order_index == row["order_index"]), None)
        # A locked scene is one somebody has approved. Rewriting its narration
        # under them is exactly what the lock exists to stop.
        if scene and not scene.locked:
            scene.narration = row["narration"]
            written += 1
    if written:
        db.session.commit()

    refreshed = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    budget = openai_service.narration_budget(refreshed, project.length_seconds)
    return jsonify({"ok": True, "written": written, "note": result.get("note", ""),
                    "budget": budget, "scenes": refreshed,
                    "live": openai_service.is_live()})


@bp.get("/<int:project_id>/narration/budget")
def get_narration_budget(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    scenes = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    return jsonify({"ok": True,
                    "budget": openai_service.narration_budget(scenes, project.length_seconds)})


# ---------------------------------------------------------------------------
# 15. Campaigns — one master concept -> many lengths/formats
# ---------------------------------------------------------------------------
@bp.post("/campaigns")
def create_campaign():
    data = request.get_json(force=True) or {}
    client_id = data.get("client_id")
    if not client_id or not Client.query.get(client_id):
        return jsonify({"ok": False, "error": "A valid client_id is required."}), 400
    campaign = Campaign(client_id=client_id, name=data.get("name", "Untitled campaign"),
                         master_concept=data.get("master_concept", ""))
    db.session.add(campaign)
    db.session.commit()
    return jsonify({"ok": True, "campaign": campaign.to_dict()}), 201


@bp.get("/campaigns/<int:campaign_id>")
def get_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    return jsonify({"ok": True, "campaign": campaign.to_dict()})


@bp.post("/campaigns/<int:campaign_id>/expand")
def expand_campaign(campaign_id):
    """Spins up one CommercialProject per (length x format) combo requested,
    all sharing the campaign's master concept, brief, and CTA (spec section 15)."""
    campaign = Campaign.query.get_or_404(campaign_id)
    data = request.get_json(force=True) or {}
    lengths = [l for l in data.get("lengths", []) if l in COMMERCIAL_LENGTHS]
    formats = [f for f in data.get("formats", []) if f in {fo["id"] for fo in OUTPUT_FORMATS}]
    brief = data.get("brief") or {}
    commercial_type = data.get("commercial_type", "stock_vo")

    if not lengths:
        return jsonify({"ok": False, "error": "Provide at least one length."}), 400

    created = []
    for length in lengths:
        project = CommercialProject(
            client_id=campaign.client_id, campaign_id=campaign.id,
            title=f"{campaign.name} — :{length:02d}", length_seconds=length,
            commercial_type=commercial_type, status="brief",
        )
        project.formats = formats or ["16:9"]
        project.brief = brief
        db.session.add(project)
        created.append(project)
    db.session.commit()
    return jsonify({"ok": True, "projects": [p.to_dict(include_scenes=False) for p in created]}), 201


# ---------------------------------------------------------------------------
# 14. Variations — clone a finished project and change only what's needed
# ---------------------------------------------------------------------------
@bp.post("/<int:project_id>/variation")
def create_variation(project_id):
    parent = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    variation_type = data.get("variation_type")
    changes = data.get("changes") or {}

    child = CommercialProject(
        client_id=parent.client_id, campaign_id=parent.campaign_id,
        title=f"{parent.title} — {variation_type} variation",
        length_seconds=changes.get("length_seconds", parent.length_seconds),
        commercial_type=parent.commercial_type, status=parent.status,
    )
    child.formats = parent.formats
    child.brief = dict(parent.brief or {})
    child.concepts = parent.concepts
    child.selected_concept_id = parent.selected_concept_id
    child.script = parent.script
    child.music = dict(parent.music or {})
    child.cta = dict(parent.cta or {})

    # Apply the requested change on top of the cloned brief/CTA/music so only
    # what changed actually changes — everything else (footage choices,
    # locked scenes) carries over untouched.
    if variation_type == "offer":
        child.brief["what_advertising"] = changes.get("what_advertising", child.brief.get("what_advertising"))
    elif variation_type == "location":
        child.brief["target_audience"] = changes.get("target_audience", child.brief.get("target_audience"))
    elif variation_type == "weather":
        child.brief["what_advertising"] = changes.get("what_advertising", child.brief.get("what_advertising"))
        child.brief["tone"] = changes.get("tone", child.brief.get("tone"))
    elif variation_type == "cta":
        child.cta.update(changes)
    elif variation_type == "voice":
        child.music["voice_id"] = changes.get("voice_id")
    elif variation_type == "duration":
        child.length_seconds = changes.get("length_seconds", child.length_seconds)

    db.session.add(child)
    db.session.flush()  # get child.id before copying scenes

    for scene in parent.scenes.all():
        clone = Scene(
            project_id=child.id, order_index=scene.order_index, start=scene.start, end=scene.end,
            narration=scene.narration, visual_description=scene.visual_description,
            asset_type=scene.asset_type, asset_source=scene.asset_source, asset_url=scene.asset_url,
            asset_thumb_url=scene.asset_thumb_url, is_cta=scene.is_cta,
            # "New footage" variations intentionally unlock every scene so
            # the storyboard editor re-sources everything on regenerate.
            locked=(variation_type != "footage") and scene.locked,
        )
        clone.asset_meta = scene.asset_meta
        db.session.add(clone)

    variation = Variation(parent_project_id=parent.id, child_project_id=child.id,
                           variation_type=variation_type, changes=changes)
    db.session.add(variation)
    db.session.commit()

    # If the brief/CTA/tone changed, re-run the script writer so narration
    # reflects the new offer/location/weather — but only for unlocked scenes.
    if variation_type in ("offer", "location", "weather", "duration"):
        client = Client.query.get(child.client_id)
        concept = next((c for c in (child.concepts or []) if c["id"] == child.selected_concept_id), None)
        if concept:
            script = openai_service.generate_script(concept, child.length_seconds, child.brief, client.to_dict())
            child.script = script
            for idx, sc in enumerate(script["scenes"]):
                scene = child.scenes.filter_by(order_index=idx).first()
                if scene and not scene.locked:
                    scene.start, scene.end = sc["start"], sc["end"]
                    scene.narration, scene.visual_description = sc["voiceover"], sc["visual"]
            db.session.commit()

    return jsonify({"ok": True, "project": child.to_dict(), "variation": variation.to_dict()}), 201
