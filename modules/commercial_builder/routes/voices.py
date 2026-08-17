"""Voice Studio (spec section 9) — ElevenLabs voice selection, per-scene
voiceover generation, and per-client pronunciation dictionaries."""

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import elevenlabs_service

bp = Blueprint("cb_voices", __name__, url_prefix="/api")


@bp.get("/voices")
def list_voices():
    return jsonify({"ok": True, "voices": elevenlabs_service.list_voices(), "live": elevenlabs_service.is_live()})


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
