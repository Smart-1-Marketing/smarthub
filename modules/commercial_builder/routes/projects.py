"""Commercial project lifecycle: start -> brief -> concepts -> script ->
CTA/music -> variations (spec sections 1, 3, 4, 11, 14, 15)."""

from flask import Blueprint, jsonify, request

from .. import client_link, compliance_spec, cost_spec, library_spec, teardown, vox_spec
from ..config import (COMMERCIAL_LENGTHS, OUTPUT_FORMATS, COMMERCIAL_TYPES, TONE_OPTIONS,
                      PLATFORMS, DEFAULT_PLATFORM, MAX_LENGTHS_PER_BUILD,
                      qr_eligible, qr_required, qr_default_on, get_structure,
                      logo_persistence_eligible,
                      length_warning, in_build_order, DEFAULT_SHOT_GRAMMAR,
                      SHOT_NUMBER_STEP, SHOT_SIZES, SHOT_ANGLES, SHOT_MOVES,
                      CTV_PUBLISHERS, publisher_qr_note, shot_label,
                      VOX_LENGTHS)
from ..db import db
from ..models import (Client, CommercialProject, Scene, Campaign, Variation,
                      ComplianceAck, RenderApproval, RenderJob)
from ..services import (openai_service, qrcode_service, cloudinary_service,
                        qc_service, abcd_service)

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


def _log_project(event, client="", detail="", **extra):
    """Never costs the write it describes.

    `audit.log()`'s first positional is `module` and `for_module` binds it —
    the trap CLAUDE.md names twice. The detail is built by the caller and
    passed in, because `submit_render` proved the swallow protects the *call*
    and not its arguments: an f-string over an attribute the model does not
    have raises before the guard can apply.
    """
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


# Writes on this blueprint that deliberately record nothing, each with the
# reason. Declared rather than left as an absence, so the remainder is a
# decision somebody made rather than one nobody has noticed yet — and an
# entry naming a route that is gone, or one that has since started logging,
# is a caller's to reject.
#
# The line they all sit on: this file is the **wizard**, and a wizard step is
# somebody still working. What reaches a client's record is a spot that was
# started, a render that was submitted, a cut that was approved, a sign-off
# that was given and a spot that was deleted — and every one of those is
# recorded. Writing a row for each keystroke between them would bury those
# five in a hundred autosaves, which is the noise that gets a log ignored.
HOUSEKEEPING_ROUTES = {
    "save_brief": "an autosave of the wizard's own form; the spot it belongs "
                  "to was recorded when it was started.",
    "generate_concepts": "drafts three concepts to choose between. Nothing "
                         "is chosen and nothing reaches the client.",
    "select_concept": "picks one of those drafts. A step inside the wizard, "
                      "not a deliverable leaving it.",
    "generate_script": "writes the blueprint into the draft the rep is "
                       "still editing.",
    "expand_narration": "rewrites one scene's narration in place.",
    "set_music": "a setting on the draft, changed as often as it is "
                 "listened to.",
    "set_cta": "the same, for the end card.",
    "create_campaign": "groups the lengths of one spot so they share a "
                       "concept; the spots themselves were recorded at "
                       "start_commercial.",
    "expand_campaign": "adds another length to that group, which reaches "
                       "start_commercial's own recording.",
    "create_variation": "links a spot to the one it was cut down from. A "
                        "relationship between two rows that are each already "
                        "recorded.",
}


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

    # The type is read before the lengths because it decides which lengths are
    # even offerable: a Vox explainer runs 60-90s and the broadcast list stops
    # at 60, so validating against one list for both would refuse the only
    # durations that format has.
    commercial_type = data.get("commercial_type")
    if commercial_type not in {t["id"] for t in COMMERCIAL_TYPES}:
        return jsonify({"ok": False, "error": "Invalid commercial_type."}), 400
    is_vox = commercial_type == vox_spec.COMMERCIAL_TYPE
    allowed_lengths = VOX_LENGTHS if is_vox else COMMERCIAL_LENGTHS

    requested = data.get("lengths")
    if requested is None:
        requested = [data.get("length_seconds", allowed_lengths[0] if is_vox else 30)]
    lengths, seen = [], set()
    for raw in requested:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value in allowed_lengths and value not in seen:
            seen.add(value)
            lengths.append(value)
    if not lengths:
        return jsonify({"ok": False,
                        "error": f"Choose at least one length from {allowed_lengths}."}), 400
    if is_vox and len(lengths) > 1:
        # The multi-length build exists because a :15 and a :60 are cut down
        # from one :30. A Vox explainer is not cut down from anything -- each
        # length is a different beat list -- so three at once would be three
        # unrelated pieces sharing a concept they do not share.
        return jsonify({"ok": False, "error": (
            "A Vox explainer is built one at a time — the lengths are not cut "
            "down from each other the way a :30 and a :15 are.")}), 400
    if len(lengths) > MAX_LENGTHS_PER_BUILD:
        return jsonify({"ok": False,
                        "error": f"At most {MAX_LENGTHS_PER_BUILD} lengths in one build."}), 400
    # 30, then 15, then the :05, then the :60 — not shortest first. The :30 is
    # the length the others are cut down from, so it is the one to get approved
    # before anything else is built. config.BUILD_ORDER says why.
    lengths = in_build_order(lengths)

    formats = data.get("formats") or ["16:9"]
    valid_ids = {f["id"] for f in OUTPUT_FORMATS}
    formats = [f for f in formats if f in valid_ids] or ["16:9"]

    platform = data.get("platform", DEFAULT_PLATFORM)
    if platform not in {p["id"] for p in PLATFORMS}:
        platform = DEFAULT_PLATFORM
    if is_vox and platform not in vox_spec.PLATFORMS:
        # Refused by name rather than silently corrected. `both` is "CTV and
        # YouTube", so quietly narrowing it to YouTube would build a piece for
        # half the buy somebody asked for and say nothing -- and defaulting it
        # is worse, because the platform field would read as though it had
        # been chosen.
        return jsonify({"ok": False,
                        "error": vox_spec.platform_note(platform)}), 400

    client = Client.query.get(client_id)
    title = (data.get("title") or "").strip()

    # Where the spot is running. On the brief rather than in a new column,
    # because `create_all()` adds no column to an existing table — and because
    # this is deliberately the smallest possible version of the publisher
    # question. It drives one warning today (Amazon takes no QR code) and is
    # shaped so that growing it into real publisher targeting, or dropping it,
    # is neither a migration.
    publishers = [p for p in (data.get("publishers") or [])
                  if p in {x["id"] for x in CTV_PUBLISHERS}]

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
        if publishers:
            project.brief = {"publishers": publishers}
        db.session.add(project)
        projects.append(project)
    db.session.commit()

    # After the commit, so a start the database refused is not written down as
    # work that began. The creating half of a create/destroy pair is the half
    # both earlier triages found missing, and here it is the moment a spot
    # starts existing against a client at all.
    _lengths = ", ".join(":%02d" % p.length_seconds for p in projects)
    _n = len(projects)
    _log_project("cb_commercial_started", client=client.name,
                 detail=(f"{_n} spot{'' if _n == 1 else 's'} started for "
                         f"{client.name} on {platform}: {_lengths}."))

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
    publisher_note = publisher_qr_note(publishers)
    if publisher_note:
        notes.append({"length": 0, "kind": "publisher", "message": publisher_note})

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
        "qr_default_on": qr_default_on(project.length_seconds, project.platform),
        "shot_targets": abcd_service.shot_targets(project.length_seconds),
        "platform": project.platform,
    })


@bp.delete("/<int:project_id>")
def delete_project(project_id):
    """The client delete's failure, one level down and identically.

    `CommercialProject` cascades to its scenes and render jobs and reaches
    none of the three tables that record what a person attested — the
    approvals, the review rounds and the compliance sign-offs. Deleting a
    spot left those pointing at an id that no longer resolves, recorded
    nothing, and answered `{"ok": true}` with no count of what had gone.
    `teardown` is the one reading of all of it, so this route and the client
    one cannot come to disagree about what deleting means.
    """
    project = CommercialProject.query.get_or_404(project_id)
    # Read before the delete, and the row's own rather than anything the
    # caller passed — the `suite_panel` rule.
    title = project.title or f"Spot {project.id}"
    client_name = getattr(getattr(project, "client", None), "name", "") or ""
    counts = teardown.work_behind([project.id])
    # A spot does not weigh itself — see `teardown.summarize`.
    summary = teardown.summarize(counts, include_projects=False)

    if summary and not teardown.confirmed(request, title):
        return jsonify({"ok": False,
                        "error": teardown.confirmation_error(title, summary),
                        "needs_confirmation": True, "project": title,
                        "counts": counts}), 409

    removed_orphans = teardown.sweep_orphans([project.id])
    db.session.delete(project)
    db.session.commit()
    _log_project("cb_commercial_deleted", client=client_name,
                 detail=(f"{title} deleted"
                         + (f", with {summary}." if summary else ".")),
                 counts=counts)
    return jsonify({"ok": True, "project": title, "counts": counts,
                    "removed_orphans": removed_orphans})


# ---------------------------------------------------------------------------
# 3. Commercial Brief
# ---------------------------------------------------------------------------
@bp.put("/<int:project_id>/brief")
def save_brief(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    # Merged, not replaced — the same trap `set_music` had. The Start page
    # writes `publishers` onto the brief before this screen is ever opened, so
    # an assignment here would silently wipe the answer to "where is this
    # running?" the first time somebody saved the brief, and the Amazon
    # warning would go quiet with nothing saying why.
    brief = dict(project.brief or {})
    brief.update({
        "what_advertising": data.get("what_advertising", ""),
        "primary_cta": data.get("primary_cta", ""),
        "landing_page": data.get("landing_page", ""),
        "phone": data.get("phone", ""),
        "target_audience": data.get("target_audience", ""),
        "tone": data.get("tone") if data.get("tone") in TONE_OPTIONS else (data.get("tone") or ""),
    })
    if "publishers" in data:
        brief["publishers"] = [p for p in (data.get("publishers") or [])
                               if p in {x["id"] for x in CTV_PUBLISHERS}]
    # The archetype — what the spot IS, as distinct from how it gets made.
    # It lives here rather than on a column because `commercial_type` already
    # holds both answers and `create_all()` adds no column to an existing
    # table; `library_spec.archetype_for()` reads the legacy value so a
    # project saved before this reads as the archetype it always was.
    if "archetype" in data:
        chosen = str(data.get("archetype") or "")
        brief["archetype"] = chosen if chosen in library_spec.ARCHETYPES else ""
    # What each archetype needs from the client, answered on the same screen
    # it is asked on. Free text: "which customer" is a name, not an option.
    for need in library_spec.NEED_KEYS:
        if need in data:
            brief[need] = str(data.get(need) or "").strip()[:600]
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
        # A Scene row is a SHOT now, not a beat. What holds a beat together is
        # this metadata: every shot in a beat carries the same label and index,
        # and the Blueprint groups on it. Written here rather than inferred
        # later, because the beat is the model's answer and re-deriving it from
        # timings would be guessing at an argument we were told.
        meta["beat"] = sc.get("beat")
        meta["beat_index"] = sc.get("beat_index")
        meta["grammar"] = sc.get("grammar") or dict(DEFAULT_SHOT_GRAMMAR)
        # Numbered in tens, the way a storyboard is, so a shot inserted between
        # 20 and 30 does not renumber the board.
        meta["shot_no"] = (idx + 1) * SHOT_NUMBER_STEP
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
    # Merged, not replaced.
    #
    # This used to assign a fresh two-key dict, which quietly wiped
    # `voice_track_url` and `music_track_url` — the two keys
    # routes/render.py reads to put audio on the render. So saving the music
    # selection after generating a voiceover threw the voiceover away, and
    # the finished commercial came back silent with nothing reading as an
    # error anywhere.
    music = dict(project.music or {})
    music["mood"] = data.get("mood")
    music["level"] = data.get("level", "Medium")
    project.music = music
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

    # A bumper carries neither a QR code nor a persistent logo bug: it is pure
    # brand recall (logo + URL), not a response mechanism, and it already runs
    # the logo full-treatment throughout.
    #
    # Two questions, asked of their own tables. They hold the same lengths
    # today and that is a fact about the tables rather than a rule — a QR code
    # is a response mechanism and needs seconds on screen to be scannable, a
    # logo bug is brand recall and needs none, so one moving must not move the
    # other in silence.
    qr_ok = qr_eligible(project.length_seconds)
    logo_ok = logo_persistence_eligible(project.length_seconds)
    qr_enabled = bool(data.get("qr_enabled")) and qr_ok
    logo_persistent = bool(data.get("logo_persistent", logo_ok)) and logo_ok

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
    # Why there is no code, when there is no code. A blank here used to be
    # indistinguishable from a button nobody pressed, and the service used to
    # fill it with a placeholder rather than admit it — see qrcode_service.
    qr_error = ""
    if qr_enabled and qr_target and (qr_target != prior_cta.get("qr_target_url") or not qr_data_url):
        qr_result = qrcode_service.generate_qr(qr_target)
        qr_data_url = qr_result.get("data_url")
        qr_error = qr_result.get("error") or ""
        if qr_result.get("bytes_io") and cloudinary_service.is_live():
            # `filename` is passed because bytes carry no name, and the
            # extension is what the format is read from — upload_asset asks
            # rather than guessing one, since a wrong guess puts a .png on an
            # MP3. This whole call had never once succeeded: it hands over a
            # BytesIO, and until upload_asset understood bytes it stringified
            # the object and open()'d that as a path.
            upload = cloudinary_service.upload_asset(
                qr_result["bytes_io"], client.slug, "logo",
                public_id=f"project-{project_id}-qr", resource_type="image",
                client_name=client.name, filename="qr.png")
            qr_image_url = upload.get("secure_url") or qr_image_url
            # And the failure was swallowed twice — once by upload_asset's own
            # except, once here by an `or` that never read `error`. The code
            # still renders from the data URL, so this is a note rather than a
            # refusal; saying nothing is what let it run silently for so long.
            if upload.get("error") and not upload.get("secure_url"):
                qr_error = (qr_error + " " if qr_error else "") + (
                    "The code was generated but could not be stored: "
                    f"{upload['error']}")

    project.cta = {
        "style": data.get("style", "logo_centered"),
        "headline": data.get("headline") or client.tagline or "",
        "offer": data.get("offer") or (project.brief or {}).get("what_advertising", ""),
        "website": website,
        "phone": data.get("phone") or client.phone or "",
        "qr_enabled": qr_enabled,
        "qr_required": qr_required(project.length_seconds, project.platform),
        "qr_default_on": qr_default_on(project.length_seconds, project.platform),
        # Which of the named publishers refuse a code, so the CTA step can say
        # so the moment one is chosen rather than at the render.
        "qr_publisher_note": publisher_qr_note((project.brief or {}).get("publishers")),
        "qr_corner": data.get("qr_corner") or "bottom-right",
        # Named rather than left as a blank the panel reads as "not generated
        # yet". QC still blocks a code that is enabled and absent; this is the
        # half that says which of the two it is.
        "qr_error": qr_error,
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
                    "required": qr_required(project.length_seconds, project.platform),
                    "default_on": qr_default_on(project.length_seconds, project.platform),
                    "publisher_note": publisher_qr_note(
                        (project.brief or {}).get("publishers"))})


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


# A shared-password session is a true statement about the session and a
# useless one in a record whose entire value is the name on it. `hub/ad_copy.py`
# refuses the same way, for the same reason.
NOT_A_PERSON = {"", "Shared login", "shared login"}


def _actor_name():
    try:
        from hub import auth as _hub_auth
        user = _hub_auth.user_from_environ(request.environ)
        return (getattr(user, "name", None) or getattr(user, "email", None)
                or str(user or "") or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


@bp.get("/library")
def spot_library():
    """Finished spots, across every client.

    What the dashboard offers is the 25 most recently touched PROJECTS, which
    answers "what was I working on" — a different question from "what have we
    actually delivered for this client", which is what somebody asks when a
    client wants something like the one from the spring.

    Delivered means APPROVED and filed: a render that succeeded is a file
    nobody has watched, and `RenderApproval` is the row that says a human
    signed it off. The distinction is the one `approve_render` already draws,
    read from the other end.
    """
    rows = (db.session.query(RenderApproval, RenderJob, CommercialProject, Client)
            .join(RenderJob, RenderApproval.render_job_id == RenderJob.id)
            .join(CommercialProject, RenderApproval.project_id == CommercialProject.id)
            .join(Client, CommercialProject.client_id == Client.id)
            .order_by(RenderApproval.approved_at.desc())
            .all())

    q = (request.args.get("q") or "").strip().lower()
    length = request.args.get("length") or ""
    fmt = request.args.get("format") or ""
    archetype = request.args.get("archetype") or ""

    out = []
    for approval, job, project, client in rows:
        key, _source = library_spec.archetype_for(project.brief,
                                                  project.commercial_type or "")
        row = {
            "project_id": project.id,
            "title": project.title or "",
            "client": client.name,
            "client_id": client.id,
            "length_seconds": project.length_seconds,
            "format": job.format,
            "archetype": key,
            "archetype_label": library_spec.ARCHETYPES.get(key, {}).get("label", ""),
            "platform": project.platform or "",
            "approved_at": (approval.approved_at.isoformat()
                            if approval.approved_at else None),
            "approved_by": approval.approved_by or "",
            # The Cloudinary copy where there is one. `stored_url` empty means
            # the filing failed and the only copy is a provider URL that
            # expires — reported rather than drawn as a working link.
            "url": approval.stored_url or "",
            "url_note": ("" if approval.stored_url else
                         "The copy in the client's library is missing, so there "
                         "is nothing durable to open."),
        }
        if length and str(row["length_seconds"]) != str(length):
            continue
        if fmt and row["format"] != fmt:
            continue
        if archetype and row["archetype"] != archetype:
            continue
        if q and q not in (row["client"] + " " + row["title"]).lower():
            continue
        out.append(row)

    return jsonify({
        "ok": True,
        "spots": out,
        # Counted over what was ASKED for, never the whole table. A filtered
        # list reporting an unfiltered total is the wrong answer with two
        # right ones either side of it -- the SEO gallery paid for that one.
        "total": len(out),
        "delivered_total": len(rows),
        "lengths": sorted({r[2].length_seconds for r in rows}),
        "formats": sorted({r[1].format for r in rows if r[1].format}),
        "archetypes": sorted({library_spec.archetype_for(
            r[2].brief, r[2].commercial_type or "")[0] for r in rows}),
        "note": ("" if rows else
                 "Nothing has been approved and filed yet. A rendered cut is "
                 "not a delivered one until somebody approves it."),
    })


@bp.get("/cost-preview")
def cost_preview():
    """What building this will consume, before it is built.

    Its own route on the Start page for the reason /spec-preview is there:
    the decision that moves the number — three lengths or one, AI video or
    stock — is made before a project exists, and by the time it shows on the
    usage page the money is gone.
    """
    lengths = [int(x) for x in
               (request.args.get("lengths") or "").split(",") if x.strip().isdigit()]
    formats = [f for f in (request.args.get("formats") or "").split(",") if f.strip()]
    return jsonify({
        "ok": True,
        "estimate": cost_spec.estimate(
            lengths, formats,
            method=request.args.get("method", "stock_vo"),
            ai_video=request.args.get("ai_video") == "1",
            spokesperson=request.args.get("spokesperson") == "1"),
    })


@bp.get("/<int:project_id>/compliance")
def get_compliance(project_id):
    """Which published rules this spot's copy engages, and who has signed it off.

    Its own route rather than a slice of /qc for the reason /abcd is: the
    panel is read while the script is being edited, and QC makes an OpenAI
    call for the spelling pass.
    """
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    result = compliance_spec.scan(
        script=project.script, brief=project.brief, cta=project.cta,
        client=client.to_dict(), commercial_type=project.commercial_type or "")
    key = compliance_spec.findings_key(result)
    ack = (ComplianceAck.query.filter_by(project_id=project.id)
           .order_by(ComplianceAck.id.desc()).first())
    return jsonify({
        "ok": True,
        "compliance": result,
        "summary": compliance_spec.summary(result),
        "needs_acknowledgment": compliance_spec.needs_acknowledgment(result),
        "findings_key": key,
        "acknowledgment": ack.to_dict() if ack else None,
        # A sign-off against a different set of findings is not a sign-off on
        # this one. Reported rather than silently ignored: "nobody has looked"
        # and "somebody looked at an earlier cut" are different situations,
        # and only the second has a name to go back to.
        "acknowledged": bool(ack and ack.findings_key == key),
        "superseded": bool(ack and ack.findings_key != key),
        "not_enforced": compliance_spec.NOT_ENFORCED,
    })


@bp.post("/<int:project_id>/compliance/acknowledge")
def acknowledge_compliance(project_id):
    """One explicit "we have checked what these rules require".

    Recorded against a name and against the exact findings that stood at the
    time — never a boolean on the project, because the question somebody asks
    later is *who* signed this off and *what did it say then*, and a flag
    answers neither. The shape `hub/creative_needs.py` uses for a comp
    confirmation on a low-spend medium.
    """
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    body = request.get_json(silent=True) or {}
    result = compliance_spec.scan(
        script=project.script, brief=project.brief, cta=project.cta,
        client=client.to_dict(), commercial_type=project.commercial_type or "")

    actor = _actor_name()
    if actor in NOT_A_PERSON:
        # `hub/ad_copy.py`'s rule. "Shared login" is a true statement about
        # the session and a useless one in a record whose entire value is the
        # name on it, so it is refused rather than written.
        return jsonify({"ok": False, "error": (
            "This session has no account behind it, so there is no name to "
            "record. Sign in with your own Hub account to acknowledge "
            "this.")}), 400

    ack = ComplianceAck(
        project_id=project.id, acknowledged_by=actor,
        findings_key=compliance_spec.findings_key(result),
        note=str(body.get("note") or "").strip()[:2000])
    ack.findings = result.get("findings") or []
    db.session.add(ack)
    db.session.commit()
    _cb_log("commercial_compliance_acknowledged", client=client.name,
            detail=(f"{project.title or 'Commercial'} · "
                    + (", ".join(compliance_spec.REGIMES[r]["label"]
                                 for r in result.get("regimes", []))
                       or "nothing engaged")),
            project=project.id)
    return jsonify({"ok": True, "acknowledgment": ack.to_dict()})


@bp.get("/<int:project_id>/abcd")
def get_abcd(project_id):
    """How this plan scores against the published thresholds.

    Its own route rather than a slice of /qc, because the Blueprint panel
    updates as shots are edited and re-running the whole QC set — which makes
    an OpenAI call for the spelling pass — on every camera-angle change would
    be a model call per keystroke.
    """
    project = CommercialProject.query.get_or_404(project_id)
    scenes = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    # to_dict() gives the row's own field names; the scorer reads shot shape.
    shots = [{"start": s["start"], "end": s["end"],
              "visual": s.get("visual_description") or "",
              "grammar_note": shot_label((s.get("asset_meta") or {}).get("grammar")),
              "is_cta": s.get("is_cta")}
             for s in scenes]
    return jsonify({"ok": True,
                    "abcd": abcd_service.score(shots, project.length_seconds,
                                               project.platform),
                    "targets": abcd_service.shot_targets(project.length_seconds),
                    "lift": abcd_service.MEASURED_LIFT})


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
