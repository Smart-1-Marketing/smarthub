"""HeyGen Spokesperson Scenes (spec section 8)."""

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import heygen_service

bp = Blueprint("cb_heygen", __name__, url_prefix="/api")


@bp.get("/presenters")
def list_presenters():
    client_id = request.args.get("client_id")
    client_avatar_id = None
    if client_id:
        client = Client.query.get(client_id)
        client_avatar_id = client.preferred_spokesperson_id if client else None
    return jsonify({"ok": True, "presenters": heygen_service.list_presenters(client_avatar_id),
                     "live": heygen_service.is_live()})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/spokesperson")
def generate_spokesperson_clip(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    data = request.get_json(force=True) or {}
    avatar_id = data.get("avatar_id")
    voice_id = data.get("voice_id")
    if not avatar_id:
        return jsonify({"ok": False, "error": "avatar_id is required."}), 400

    job = heygen_service.generate_spokesperson_clip(avatar_id, scene.narration or "", voice_id)
    scene.asset_type = "spokesperson"
    scene.asset_source = "heygen"
    meta = scene.asset_meta or {}
    meta["heygen_job"] = job
    meta["avatar_id"] = avatar_id
    scene.asset_meta = meta
    if job.get("status") == "completed" and job.get("video_url"):
        scene.asset_url = job["video_url"]
    db.session.commit()
    return jsonify({"ok": True, "job": job, "scene": scene.to_dict(), "live": heygen_service.is_live()})


@bp.get("/heygen/status/<job_id>")
def check_status(job_id):
    return jsonify({"ok": True, "status": heygen_service.check_status(job_id)})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/spokesperson/apply")
def apply_spokesperson_result(project_id, scene_id):
    """Called once polling shows a HeyGen job finished, to attach the final
    video URL to the scene."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    data = request.get_json(force=True) or {}
    video_url = data.get("video_url")
    if not video_url:
        return jsonify({"ok": False, "error": "video_url is required."}), 400
    scene.asset_url = video_url
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})
