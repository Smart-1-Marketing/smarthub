"""Per-scene asset sourcing: AI generation button (spec section 7) and
direct uploads. Client-asset and stock-search sourcing live in clients.py /
stock.py respectively — this file covers the two remaining buttons on every
storyboard scene card: 'Generate AI' and 'Upload'."""

from flask import Blueprint, jsonify, request

from ..config import ASSET_SOURCE_PRIORITY
from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import openai_service, cloudinary_service

bp = Blueprint("cb_assets", __name__, url_prefix="/api")


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/generate-ai")
def generate_ai_footage(project_id, scene_id):
    """'Generate AI Footage' — deliberately NOT called automatically for
    every scene (cost control per spec section 7); only fires when the user
    clicks the button for a specific scene."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)

    options = openai_service.generate_ai_stills(scene.visual_description or "", client.to_dict())
    meta = scene.asset_meta or {}
    meta["ai_options"] = options
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": True, "options": options, "live": openai_service.is_live()})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/choose-ai-option")
def choose_ai_option(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    data = request.get_json(force=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"ok": False, "error": "url is required"}), 400
    scene.asset_type = "ai_generated"
    scene.asset_source = "openai"
    scene.asset_url = url
    scene.asset_thumb_url = url
    meta = dict(scene.asset_meta or {})
    # A still, and recorded as one. "Generate AI" produces OpenAI images until
    # a text-to-video provider is wired up; the compositor reads this rather
    # than guessing from the asset_type label, so the day that changes it is
    # this line that changes with it.
    meta["media"] = "image"
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/upload")
def upload_scene_asset(project_id, scene_id):
    """Direct file upload for a scene (spec's 'Upload' button)."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)

    if "file" in request.files:
        source = request.files["file"]
    else:
        data = request.get_json(force=True) or {}
        source = data.get("url")
        if not source:
            return jsonify({"ok": False, "error": "Provide a file or a url."}), 400

    result = cloudinary_service.upload_asset(source, client.slug, "video",
                                              public_id=f"project-{project_id}-scene-{scene_id}")
    scene.asset_type = "upload"
    scene.asset_source = "upload"
    scene.asset_url = result.get("secure_url")
    scene.asset_thumb_url = result.get("secure_url")
    db.session.commit()
    return jsonify({"ok": True, "asset": result, "scene": scene.to_dict()})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/use-client-asset")
def use_client_asset(project_id, scene_id):
    """'Use Client Asset' — attach something already in the client's
    Cloudinary Creative Library."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    data = request.get_json(force=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"ok": False, "error": "url is required"}), 400
    scene.asset_type = "client_asset"
    scene.asset_source = "cloudinary"
    scene.asset_url = url
    scene.asset_thumb_url = data.get("thumbnail", url)
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})


@bp.get("/asset-source-priority")
def asset_source_priority():
    """So the front-end can render the waterfall order (client assets ->
    free stock -> premium stock -> AI generation) consistently."""
    return jsonify({"ok": True, "priority": ASSET_SOURCE_PRIORITY})
