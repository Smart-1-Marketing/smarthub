"""Per-scene asset sourcing: AI generation button (spec section 7) and
direct uploads. Client-asset and stock-search sourcing live in clients.py /
stock.py respectively — this file covers the two remaining buttons on every
storyboard scene card: 'Generate AI' and 'Upload'."""

from flask import Blueprint, jsonify, request

from ..config import ASSET_SOURCE_PRIORITY
from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import openai_service, cloudinary_service, runway_service

bp = Blueprint("cb_assets", __name__, url_prefix="/api")


# Writes on this blueprint that deliberately record nothing, each with the
# reason. The line this file records on is whether a **file reaches the
# client's own Cloudinary tree**: `generate_ai_video` and the upload do, and
# they record; pointing a scene at something that is already there, or at a
# draft the sweep will remove, does not.
HOUSEKEEPING_ROUTES = {
    "generate_ai_footage": "draws options to choose between. Billed, and the "
                           "bill is `hub/quotas.py`'s answer — nothing is "
                           "kept, so nothing has been made for the client "
                           "yet.",
    "choose_ai_option": "points a scene at one of those drafts. The clip it "
                        "chooses is recorded when it is generated as video.",
    "use_client_asset": "attaches something already in the client's library. "
                        "No file is stored, and the asset was recorded by "
                        "whichever tool put it there.",
}
# A generated clip is client work, so it belongs on the client's 360 record.
try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _cb_log(*_a, **_k):
        return None


def _primary_format(project):
    formats = project.formats or []
    return formats[0] if formats else "16:9"


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
    # A file that reaches the client's own Cloudinary tree, which is the line
    # this module records on: `use_client_asset` beside it points a scene at
    # something already there and stores nothing, so it does not.
    _cb_log("commercial_asset_uploaded", client=client.name,
            detail=(f"Footage uploaded to scene {scene.order_index + 1} of "
                    f"{project.title or 'the spot'}."),
            project=project_id)
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


# ---------------------------------------------------------------------------
# AI video (Runway). The still-frame path above is the FIRST half of this:
# Runway animates a starting frame, so a scene needs an image before it can
# have footage. Kept as its own button rather than folded into "Generate AI"
# because it costs real money per clip and takes minutes, and the spec is
# explicit that AI video is never generated automatically for every scene.
# ---------------------------------------------------------------------------
@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/generate-video")
def generate_ai_video(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}

    # The frame to animate: whatever the caller nominated, else whatever the
    # scene already has. A scene with nothing on it is told to get a frame
    # first rather than being handed a provider error that reads like an
    # outage.
    image_url = data.get("image_url") or scene.asset_url
    if not image_url:
        return jsonify({"ok": False,
                        "error": "Runway animates a starting frame, so this scene needs an "
                                 "image first — generate one, pick stock, or use a client "
                                 "asset, then generate video from it."}), 400

    seconds = float((scene.end or 0) - (scene.start or 0))
    prompt = (data.get("prompt")
              or openai_service.write_runway_prompt(scene.visual_description or "",
                                                    client.to_dict()))
    job = runway_service.generate_from_image(image_url, prompt,
                                             format_id=_primary_format(project),
                                             scene_seconds=seconds)

    meta = dict(scene.asset_meta or {})
    meta["runway_job"] = job
    meta["runway_source_image"] = image_url
    scene.asset_meta = meta
    db.session.commit()

    if job.get("status") != "failed":
        _cb_log("ai_video_generated", client=client.name,
                detail=f"Scene {scene.order_index + 1} · {job.get('duration')}s "
                       f"· {job.get('ratio')}", project=project.id)

    # A scene too long to cover, or a missing frame, is the caller's problem;
    # anything else is Runway's.
    bad_request = job.get("status") == "failed" and not job.get("job_id") \
        and "Runway clips are at most" in (job.get("error") or "")
    code = 400 if bad_request else (502 if job.get("status") == "failed" else 200)
    return jsonify({"ok": job.get("status") != "failed", "job": job,
                    "error": job.get("error"), "scene": scene.to_dict(),
                    "live": runway_service.is_live()}), code


@bp.get("/projects/<int:project_id>/scenes/<int:scene_id>/generate-video/status")
def ai_video_status(project_id, scene_id):
    """Polls the scene's Runway task and finishes it when the clip is ready.

    The write-through is the point, exactly as it is for a spokesperson clip:
    any request for the status attaches a finished clip, so closing the tab
    does not lose a job that has already been paid for.
    """
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    job = (scene.asset_meta or {}).get("runway_job") or {}
    if not job:
        return jsonify({"ok": False, "error": "No AI video has been generated for this "
                                              "scene."}), 404

    meta = dict(scene.asset_meta or {})
    if meta.get("runway_url"):
        return jsonify({"ok": True, "status": "completed", "attached": True,
                        "scene": scene.to_dict()})
    if job.get("status") == "failed":
        return jsonify({"ok": False, "status": "failed", "attached": False,
                        "error": job.get("error"), "scene": scene.to_dict()})

    status = runway_service.check_status(job.get("job_id"))
    meta["runway_job"] = {**job, **status}

    if status.get("status") == "completed" and status.get("video_url"):
        project = CommercialProject.query.get(project_id)
        client = Client.query.get(project.client_id) if project else None
        stored = cloudinary_service.upload_asset(
            status["video_url"], client.slug if client else "unassigned", "video",
            public_id=f"ai-video-p{project_id}-s{scene_id}")
        # A provider URL is signed and expires. Fall back to it if the mirror
        # failed, but record that so the storyboard can say the link will die.
        url = stored.get("secure_url") or status["video_url"]
        meta["runway_url"] = url
        meta["runway_mirrored"] = bool(stored.get("secure_url")) and not stored.get("_mock")
        if stored.get("error"):
            meta["runway_mirror_error"] = stored["error"]
        # The clip length is what QC needs to know a scene is covered.
        meta["clip_seconds"] = job.get("duration")
        meta["media"] = "video"
        scene.asset_type = "ai_generated"
        scene.asset_source = "runway"
        scene.asset_url = url
        scene.asset_thumb_url = meta.get("runway_source_image") or scene.asset_thumb_url
        if client:
            _cb_log("ai_video_ready", client=client.name,
                    detail=f"Scene {scene.order_index + 1}", project=project_id)

    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": status.get("status") != "failed", "status": status.get("status"),
                    "attached": bool(meta.get("runway_url")),
                    "error": status.get("error"), "mock": status.get("_mock", False),
                    "scene": scene.to_dict()})
