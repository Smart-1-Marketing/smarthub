"""Voice Studio (spec section 9) — ElevenLabs voice selection, per-scene
voiceover generation, and per-client pronunciation dictionaries."""

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import elevenlabs_service

# The casting question, shared with the Radio Promo builder. Guarded because
# this module runs standalone, where there is no hub to read it from.
try:
    from hub import voice_casting
except Exception:                                    # noqa: BLE001
    voice_casting = None

bp = Blueprint("cb_voices", __name__, url_prefix="/api")


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
    return jsonify({"ok": True, "characteristics": voice_casting.CHARACTERISTICS,
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
    return jsonify({"ok": True, "voices": matched, "note": note,
                    "live": elevenlabs_service.is_live()})


@bp.put("/clients/<int:client_id>/pronunciation")
def save_pronunciation(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json(force=True) or {}
    client.pronunciation_dict = data.get("pronunciation_dict") or {}
    db.session.commit()
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

    result = elevenlabs_service.generate_voiceover(
        text=scene.narration or "",
        voice_id=voice_id,
        stability=float(data.get("stability", 0.5)),
        style=float(data.get("style", 0.5)),
        speed=float(data.get("speed", 1.0)),
        pronunciation_dict=client.pronunciation_dict,
    )
    meta = scene.asset_meta or {}
    meta["voiceover"] = {"voice_id": voice_id, **result}
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": True, "voiceover": result, "live": elevenlabs_service.is_live()})


@bp.post("/projects/<int:project_id>/voiceover/full")
def generate_full_voiceover(project_id):
    """Generates one continuous VO track for the whole commercial (used at
    render time so pacing/breath timing is consistent, rather than stitching
    per-scene clips)."""
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}
    voice_id = data.get("voice_id") or client.preferred_voiceover_id
    if not voice_id:
        return jsonify({"ok": False, "error": "Choose a voice first."}), 400

    full_text = " ".join(s.narration or "" for s in project.scenes.order_by(Scene.order_index).all()
                          if not s.is_cta)
    result = elevenlabs_service.generate_voiceover(
        text=full_text, voice_id=voice_id,
        stability=float(data.get("stability", 0.5)), style=float(data.get("style", 0.5)),
        speed=float(data.get("speed", 1.0)), pronunciation_dict=client.pronunciation_dict,
    )
    return jsonify({"ok": True, "voiceover": result, "live": elevenlabs_service.is_live()})
