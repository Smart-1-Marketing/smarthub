"""Voice Studio (spec section 9) — ElevenLabs voice selection, per-scene
voiceover generation, and per-client pronunciation dictionaries."""

import hashlib

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import cloudinary_service, elevenlabs_service, media_state

# The casting question, shared with the Radio Promo builder. Guarded because
# this module runs standalone, where there is no hub to read it from.
try:
    from hub import voice_casting
except Exception:                                    # noqa: BLE001
    voice_casting = None

bp = Blueprint("cb_voices", __name__, url_prefix="/api")

try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001 — standalone, no Hub to log into
    def _cb_log(*_a, **_k):
        return None


def _log(event, client="", detail="", **extra):
    """Never costs the write it describes. See `routes/review.py:_log`."""
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


# Writes on this blueprint that deliberately record nothing, each with the
# reason. The line: a read that spends a model call is metered by
# `hub/quotas.py`, which is where a bill is answered; the activity log
# answers *what was made for this client*, and only a take that is kept has
# been made.
HOUSEKEEPING_ROUTES = {
    "cast_voices": "ranks the account's voices against what the script needs "
                   "and returns a shortlist. Nothing is chosen and nothing "
                   "is stored.",
    "generate_scene_voiceover": "auditions one scene so a rep can hear the "
                                "voice before committing to a read. The take "
                                "is returned and never stored — the full "
                                "voiceover is the one that is kept, and it "
                                "records.",
}


@bp.get("/voices")
def list_voices():
    return jsonify({"ok": True, "voices": elevenlabs_service.list_voices(),
                    "live": elevenlabs_service.is_live()})


@bp.get("/voice-characteristics")
def voice_characteristics():
    """What the read should sound like, as a set of choices.

    The Voice Studio asked for a voice id out of a flat dropdown of everything
    on the account, in whatever order ElevenLabs returned it and with no way
    to hear any of it. The Radio Promo builder — same provider, same account,
    same question — asks what the read should sound like and offers three
    ranked voices with a preview on each. There is one question now, in
    hub/voice_casting.py, and both tools ask it.
    """
    if voice_casting is None:
        return jsonify({"ok": True, "characteristics": [], "default": {},
                        "note": "Voice casting is unavailable outside the Hub."})
    return jsonify({"ok": True,
                    # The detailed form: each option carries the words it will
                    # match on, and energy carries the `style` value it sends.
                    # A picker built from labels alone gives somebody five rows
                    # of choices and no idea what any of them does.
                    "characteristics": voice_casting.characteristics_detail(),
                    "default": voice_casting.DEFAULT_WANT, "note": ""})


@bp.post("/voices/cast")
def cast_voices():
    """Rank the account's voices against what the read should sound like.

    A POST because the answer depends on a body of choices, and because the
    result is not cacheable by URL in any way a reader would expect. It reads
    only — nothing is saved until a voice is picked.
    """
    data = request.get_json(force=True) or {}
    want = data.get("want") or {}

    # A spot's own tone is a real signal and it has already been captured on
    # the brief. Folding it into the search terms means the casting starts
    # somewhere sensible rather than from nothing — the Proposal Builder
    # shipped four discovery answers that were read by nobody, and this is the
    # same failure one screen along.
    project_id = data.get("project_id")
    if project_id:
        project = CommercialProject.query.get(project_id)
        if project:
            terms = list(want.get("search_terms") or [])
            tone = (project.brief or {}).get("tone")
            if tone:
                terms.append(str(tone))
            want = {**want, "search_terms": terms}

    matched, note = elevenlabs_service.cast_voices(want, int(data.get("count") or 3))
    # How many characteristics were actually asked for. "No preference" is not
    # a question, so counting it would make a voice that matched everything
    # asked read as "2 of 5".
    asked = voice_casting.asked_count(want) if voice_casting else 0
    return jsonify({"ok": True, "voices": matched, "note": note, "asked": asked,
                    "live": elevenlabs_service.is_live()})


@bp.put("/clients/<int:client_id>/pronunciation")
def save_pronunciation(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json(force=True) or {}
    client.pronunciation_dict = data.get("pronunciation_dict") or {}
    db.session.commit()
    # This writes the same brand-profile field `update_client` writes, and
    # that one records. Two routes changing one field with only one of them
    # recorded is the inconsistency this triage exists to close: how a client's
    # name comes to be said differently is exactly what somebody would go
    # looking for later.
    _log("cb_client_updated", client=client.name,
         detail="Brand profile updated: pronunciation_dict.")
    return jsonify({"ok": True, "pronunciation_dict": client.pronunciation_dict})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/voiceover")
def generate_scene_voiceover(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}

    voice_id = data.get("voice_id") or client.preferred_voiceover_id
    if not voice_id:
        return jsonify({"ok": False, "error": "Choose a voice first (or set the client's preferred voiceover)."}), 400
    if media_state.has_presenter(scene.to_dict()):
        return jsonify({"ok": False, "error": "This scene uses HeyGen speech. Cast its voice in the presenter picker."}), 409
    result = _generate_scene_audio(scene, client, voice_id, data)
    return jsonify({"ok": not bool(result.get("error")), "voiceover": result,
                    "error": result.get("error"), "live": elevenlabs_service.is_live()}), (502 if result.get("error") else 200)


def _generate_scene_audio(scene, client, voice_id, data):
    """Persist bytes as audio, never as a JSON field or HTTP response."""
    signature = media_state.speech_signature(scene.to_dict())
    settings = {key: float(data.get(key, default)) for key, default in
                (("stability", 0.5), ("style", 0.5), ("speed", 1.0))}
    take_key = media_state.fingerprint([signature, voice_id, settings, client.pronunciation_dict])
    prior = (scene.asset_meta or {}).get("voiceover") or {}
    if prior.get("take_key") == take_key and prior.get("audio_url") and not prior.get("stale"):
        return {**prior, "stored": True, "store_note": "Using the saved narration."}
    result = elevenlabs_service.generate_voiceover(text=scene.narration or "", voice_id=voice_id,
        pronunciation_dict=client.pronunciation_dict, **settings)
    audio = result.pop("audio_bytes", None)
    result.update(voice_id=voice_id, provider="elevenlabs", speech_signature=signature, take_key=take_key)
    if audio:
        uploaded = cloudinary_service.upload_asset(audio, client.slug, "voice",
            public_id=f"scene-{scene.id}-voice-{hashlib.sha256(audio).hexdigest()[:16]}",
            resource_type="video", filename="narration.mp3")
        if uploaded.get("secure_url") and not uploaded.get("_mock"):
            result.update(audio_url=uploaded["secure_url"], stored=True, store_note="Narration saved.")
        else:
            result["error"] = uploaded.get("error") or "Narration was generated but could not be saved. Check media storage."
    elif not result.get("error"):
        result.update(stored=False, store_note="Mock mode — no narration was produced.")
    if not result.get("error"):
        db.session.refresh(scene)
        result["stale"] = signature != media_state.speech_signature(scene.to_dict())
        scene.asset_meta = {**(scene.asset_meta or {}), "voiceover": result}
        db.session.commit()
    return result


@bp.post("/projects/<int:project_id>/voiceover/full")
def generate_full_voiceover(project_id):
    """Generates one continuous VO track for the whole commercial (used at
    render time so pacing/breath timing is consistent, rather than stitching
    per-scene clips)."""
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}
    scenes = project.scenes.order_by(Scene.order_index).all()
    snapshot = [s.to_dict() for s in scenes]
    signature = media_state.timeline_signature(snapshot)
    presenter_mode = any(media_state.has_presenter(s) for s in snapshot)
    voice_id = data.get("voice_id") or client.preferred_voiceover_id
    narration_scenes = [s for s in scenes if (s.narration or "").strip() and
                        not media_state.has_presenter(s.to_dict())]
    if not voice_id and (not presenter_mode or narration_scenes):
        return jsonify({"ok": False, "error": "Choose a voice first."}), 400

    if presenter_mode:
        takes = [_generate_scene_audio(s, client, voice_id, data) for s in narration_scenes]
        errors = [t["error"] for t in takes if t.get("error")]
        if errors:
            return jsonify({"ok": False, "error": " ".join(errors)}), 502
        db.session.refresh(project)
        music = dict(project.music or {})
        music.pop("voice_track_url", None)
        music.update(voice_mode="scenes", voice_signature=signature, voice_track_stale=False)
        project.music = music
        db.session.commit()
        stored = all(t.get("stored") for t in takes)
        return jsonify({"ok": True, "voiceover": {"stored": stored,
            "duration_estimate": sum(t.get("duration_estimate") or 0 for t in takes),
            "store_note": ("HeyGen speaks the presenter scenes. Other narration is saved on its own scene."
                           if stored else "Mock mode — narration has not been produced.")},
            "live": elevenlabs_service.is_live()})

    # The plain-track path -- one continuous read, generated, stored and
    # written onto `project.music["voice_track_url"]`. Moved into
    # `generation.py` for WO-CS5 so Creative Studio's own "voice" job runs
    # the identical logic rather than a second copy of it: the request, the
    # storage, and the failure-versus-mock distinction below were each their
    # own defect once (see that module's docstring), and two readings of one
    # question is how one of them comes to drift without the other.
    #
    # This was called "preview" and behaved like one, before that fix: it
    # generated the whole voiceover, paid ElevenLabs for every character of
    # it, reported the estimated duration and threw the audio away.
    # `routes/render.py` reads `project.music["voice_track_url"]` to put the
    # voice track on the timeline, and nothing in this module had ever
    # written that key — so every commercial this tool rendered was silent,
    # with no error at either end.
    from .. import generation
    try:
        result = generation.run_full_voiceover(
            project, client, voice_id,
            stability=float(data.get("stability", 0.5)),
            style=float(data.get("style", 0.5)),
            speed=float(data.get("speed", 1.0)))
    except ValueError as exc:
        # The take that could not be kept, logged all the same: "generated,
        # but it could not be stored" is still work somebody asked for, and
        # is exactly the row somebody would go looking for later.
        _log("commercial_voiceover_recorded", client=client.name,
             detail=(f"Voiceover recorded for {project.title or 'the spot'}, "
                     "but it could not be stored."),
             project=project.id)
        return jsonify({"ok": False, "voiceover": {"error": str(exc)},
                        "error": str(exc),
                        "voice_track_url": (project.music or {}).get("voice_track_url") or "",
                        "live": elevenlabs_service.is_live()}), 502

    _log("commercial_voiceover_recorded", client=client.name,
         detail=f"Voiceover recorded for {project.title or 'the spot'}.",
         project=project.id)
    return jsonify({"ok": True, "voiceover": result, "error": None,
                    "voice_track_url": (project.music or {}).get("voice_track_url") or "",
                    "live": elevenlabs_service.is_live()})


# _store_voice_track moved to generation.py for WO-CS5 -- the plain-track
# branch above calls generation.run_full_voiceover instead, which is the
# same function's own private helper now. Nothing else in this module ever
# called it.
