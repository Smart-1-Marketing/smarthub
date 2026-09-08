"""HeyGen Spokesperson Scenes (spec section 8).

A HeyGen clip takes minutes to render, so generating one and attaching one
are two different requests. The route that starts a job records it on the
scene and returns; `spokesperson_status` is what finishes the job — it polls
HeyGen and, the moment a clip is ready, mirrors it into the client's
Cloudinary library and writes the URL onto the scene.

That write-through is the point. The first version left attaching the clip to
the browser, so a rep who generated a presenter and closed the tab came back
to a scene marked "Spokesperson" with no video on it, and the render composed
a blank segment without an error anywhere. Now any request for the status
completes the job, so re-opening the storyboard is enough.

The clip is mirrored rather than linked because HeyGen's URLs are signed and
expire. Linking one works for days and then does not, which is the worst
shape a bug can have.
"""

import json
import time
import uuid

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import heygen_service, cloudinary_service, media_state

bp = Blueprint("cb_heygen", __name__, url_prefix="/api")


# Writes on this blueprint that deliberately record nothing, with the reason.
HOUSEKEEPING_ROUTES = {
    "apply_spokesperson_result": "attaches a clip whose arrival "
                                 "`spokesperson_status` already recorded. "
                                 "That route is where the provider's answer "
                                 "is turned into an asset, and recording the "
                                 "same clip twice would read as two takes.",
}


# A spokesperson clip is client work, so it belongs on the client's 360
# record. Guarded so the module still runs standalone.
try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _cb_log(*_a, **_k):
        return None


def _primary_format(project):
    """The format a clip is generated at. A project can fan out to several,
    but a presenter is rendered once and composited into each, so it follows
    the first format the project asked for."""
    formats = project.formats or []
    return formats[0] if formats else "16:9"


def _job_of(scene):
    return (scene.asset_meta or {}).get("heygen_job") or {}


def _claim(scene, meta, expected):
    """Reserve a scene atomically before making a paid provider request."""
    count = Scene.query.filter(Scene.id == scene.id, Scene.asset_meta_json == expected).update(
        {Scene.asset_meta_json: json.dumps(meta)}, synchronize_session=False)
    db.session.commit()
    db.session.refresh(scene)
    return bool(count)


@bp.get("/heygen/voices")
def list_voices():
    result = heygen_service.list_voices(request.args.get("token"),
                                      "private" if request.args.get("type") == "private" else "public")
    return jsonify({"ok": not bool(result.get("error")), "live": heygen_service.is_live(), **result}), (502 if result.get("error") else 200)


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
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    avatar_id = data.get("avatar_id")
    voice_id = data.get("voice_id")
    if not avatar_id:
        return jsonify({"ok": False, "error": "avatar_id is required."}), 400
    if heygen_service.is_live() and (not voice_id or data.get("voice_provider") != "heygen"):
        return jsonify({"ok": False, "error": "Choose a HeyGen voice in the presenter picker."}), 400
    if voice_id and (not isinstance(voice_id, str) or not heygen_service.voice_available(voice_id)):
        return jsonify({"ok": False, "error": "That HeyGen voice is unavailable. Reload the voice list and choose again."}), 400
    # Checked here as well as in the service: this one is the caller's mistake
    # and deserves a 400, where a provider failure below deserves a 502. The
    # service keeps its own guard because it is callable without this route.
    if not (scene.narration or "").strip():
        return jsonify({"ok": False, "error": "This scene has no narration for the "
                                              "presenter to read."}), 400

    # Whether the presenter is keyed over footage or fills the frame decides
    # what background HeyGen is asked for, and it cannot be changed after the
    # clip is generated. A scene that already has footage on it keeps that
    # footage as its background unless the caller says otherwise.
    over_footage = data.get("over_footage")
    if over_footage is None:
        over_footage = bool(scene.asset_url) and scene.asset_type != "spokesperson"
    over_footage = bool(over_footage)

    identity = media_state.fingerprint({"avatar": avatar_id, "voice": voice_id,
        "speech": media_state.speech_signature(scene.to_dict()), "format": _primary_format(project),
        "over_footage": over_footage})
    previous = dict(scene.asset_meta or {})
    prior_job = previous.get("heygen_job") or {}
    if prior_job.get("status") in ("submitting", "processing", "pending", "unknown"):
        return jsonify({"ok": False, "error": "A presenter request already exists. Check its status before creating another take.",
                        "job": prior_job}), 409
    if prior_job.get("request_key") == identity and not data.get("regenerate") and not previous.get("presenter_stale"):
        return jsonify({"ok": True, "job": prior_job, "scene": scene.to_dict(), "reused": True,
                        "live": heygen_service.is_live()})
    reservation = {"status": "submitting", "token": uuid.uuid4().hex, "submitted_at": time.time(),
                   "request_key": identity, "speech_signature": media_state.speech_signature(scene.to_dict()),
                   "format_id": _primary_format(project), "voice_id": voice_id, "voice_provider": "heygen"}
    if not _claim(scene, {**previous, "heygen_job": reservation}, scene.asset_meta_json):
        return jsonify({"ok": False, "error": "This scene changed. Reload before generating."}), 409

    job = heygen_service.generate_spokesperson_clip(
        avatar_id, scene.narration or "", voice_id,
        format_id=_primary_format(project), over_footage=over_footage)
    db.session.refresh(scene)
    if _job_of(scene).get("token") != reservation["token"]:
        return jsonify({"ok": False, "error": "This scene changed while HeyGen was accepting the request.", "job": job}), 409
    job = {**reservation, **job}
    if job.get("status") == "failed":
        # A rejected new take must not erase the usable previous clip.
        restored = dict(scene.asset_meta or {})
        if prior_job:
            restored["heygen_job"] = prior_job
        else:
            restored.pop("heygen_job", None)
        scene.asset_meta = restored
        db.session.commit()
        return jsonify({"ok": False, "job": job, "error": job.get("error"), "scene": scene.to_dict()}), 502
    meta = dict(scene.asset_meta or {})
    meta["heygen_job"] = job
    meta["avatar_id"] = avatar_id
    meta["presenter_stale"] = job["speech_signature"] != media_state.speech_signature(scene.to_dict())
    meta.pop("spokesperson_url", None)
    meta.pop("spokesperson_mirrored", None)
    meta.pop("spokesperson_mirror_error", None)
    meta["chroma_key"] = job.get("chroma_key", False)
    meta["chroma_key_color"] = job.get("background_color")
    meta["spokesperson_over_footage"] = over_footage
    if over_footage:
        # The presenter composites on top; the scene keeps its own footage as
        # the background, so asset_url is left alone.
        meta["spokesperson_url"] = None
    else:
        scene.asset_type = "spokesperson"
        scene.asset_source = "heygen"
        scene.asset_url = None       # nothing to show until the clip lands
        scene.asset_thumb_url = None
    scene.asset_meta = meta
    db.session.commit()

    if job.get("status") != "failed":
        client = Client.query.get(project.client_id)
        _cb_log("spokesperson_generated", client=client.name if client else None,
                detail=f"Scene {scene.order_index + 1} · {_primary_format(project)}"
                       f"{' · keyed over footage' if over_footage else ''}",
                project=project.id)

    # A failure here is HeyGen's, not the caller's — the caller's mistakes were
    # already answered with a 400 above.
    status_code = 502 if job.get("status") == "failed" else 200
    return jsonify({"ok": job.get("status") != "failed", "job": job,
                    "error": job.get("error"),
                    "scene": scene.to_dict(),
                    "live": heygen_service.is_live()}), status_code


@bp.get("/projects/<int:project_id>/scenes/<int:scene_id>/spokesperson/status")
def spokesperson_status(project_id, scene_id):
    """Polls the scene's HeyGen job and finishes it when the clip is ready.

    Safe to call repeatedly and from anywhere — once a clip is attached this
    reports "completed" without calling HeyGen again.
    """
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    job = _job_of(scene)
    if not job:
        return jsonify({"ok": False, "error": "No spokesperson clip has been generated "
                                              "for this scene."}), 404

    attached = (scene.asset_meta or {}).get("spokesperson_url") or (
        scene.asset_url if scene.asset_type == "spokesperson" else None)
    if attached and (scene.asset_meta or {}).get("spokesperson_mirrored") is not False and job.get("status") not in ("submitting", "processing", "pending", "unknown"):
        return jsonify({"ok": True, "status": "completed", "attached": True,
                        "scene": scene.to_dict()})

    if job.get("status") == "failed" and (job.get("provider_terminal") or not job.get("job_id")):
        return jsonify({"ok": False, "status": "failed", "attached": False,
                        "error": job.get("error"), "scene": scene.to_dict()})

    if job.get("status") in ("submitting", "unknown") and not job.get("job_id"):
        waiting = job.get("status") == "submitting" and time.time() - job.get("submitted_at", 0) < 90
        return jsonify({"ok": True, "status": "processing" if waiting else "unknown", "attached": False,
                        "error": "Waiting for confirmation. If this persists, recover the clip from HeyGen; do not submit another paid request.",
                        "scene": scene.to_dict()})
    status = dict(job) if job.get("recovered") else heygen_service.check_status(job.get("job_id"))
    # A slow status response must never attach an older take to a newer job.
    db.session.refresh(scene)
    if _job_of(scene).get("job_id") != job.get("job_id"):
        return jsonify({"ok": True, "status": "processing", "attached": False, "scene": scene.to_dict()})
    meta = dict(scene.asset_meta or {})
    meta["heygen_job"] = {**job, **status}

    if status.get("status") == "completed" and status.get("video_url"):
        project = CommercialProject.query.get(project_id)
        client = Client.query.get(project.client_id) if project else None
        stored = cloudinary_service.upload_asset(
            status["video_url"], client.slug if client else "unassigned", "video",
            public_id=f"spokesperson-p{project_id}-s{scene_id}-{media_state.fingerprint(job.get('job_id'))[:12]}")
        db.session.refresh(scene)
        if _job_of(scene).get("job_id") != job.get("job_id"):
            return jsonify({"ok": True, "status": "processing", "attached": False, "scene": scene.to_dict()})
        meta = dict(scene.asset_meta or {})
        meta["heygen_job"] = {**job, **status}
        # Fall back to HeyGen's own URL if the mirror failed, but say so: a
        # signed URL works today and expires, and the storyboard should be
        # able to tell you which one you are looking at.
        url = stored.get("secure_url") or status["video_url"]
        meta["spokesperson_url"] = url
        if not meta.get("spokesperson_over_footage"):
            meta["media"] = "video"
        meta["spokesperson_mirrored"] = bool(stored.get("secure_url")) and not stored.get("_mock")
        if not meta["spokesperson_mirrored"]:
            meta["spokesperson_mirror_error"] = stored.get("error") or "The client library is unavailable. Retry saving this clip."
        else:
            meta.pop("spokesperson_mirror_error", None)
        if not meta.get("spokesperson_over_footage"):
            scene.asset_url = url
            # No thumbnail: HeyGen does not return a poster frame, and putting
            # the video URL in the thumb slot draws an empty box that reads as
            # "no asset". The storyboard labels the card instead.
            scene.asset_thumb_url = None
        if client:
            _cb_log("spokesperson_ready", client=client.name,
                    detail=f"Scene {scene.order_index + 1}", project=project_id)

    scene.asset_meta = {**(scene.asset_meta or {}), **meta}
    db.session.commit()
    return jsonify({"ok": status.get("status") != "failed", "status": status.get("status"),
                    "attached": bool(meta.get("spokesperson_url")),
                    "error": status.get("error"), "mock": status.get("_mock", False),
                    "storage_pending": meta.get("spokesperson_mirrored") is False,
                    "scene": scene.to_dict()})


@bp.get("/heygen/status/<job_id>")
def check_status(job_id):
    """Raw provider status for one job id, with no scene behind it. Kept for
    debugging — the scene-scoped route above is the one that finishes a job."""
    return jsonify({"ok": True, "status": heygen_service.check_status(job_id)})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/spokesperson/apply")
def apply_spokesperson_result(project_id, scene_id):
    """Attaches a video URL to a scene by hand.

    The status route above does this automatically; this stays for the case
    where a clip was recovered from the HeyGen console after a job was lost.
    """
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    data = request.get_json(force=True) or {}
    video_url = data.get("video_url")
    if not video_url:
        return jsonify({"ok": False, "error": "video_url is required."}), 400
    meta = dict(scene.asset_meta or {})
    meta["spokesperson_url"] = video_url
    meta["presenter_stale"] = False
    meta["heygen_job"] = {"status": "completed", "speech_signature": media_state.speech_signature(scene.to_dict()),
                          "recovered": True}
    # A manually recovered URL may also expire; mirror it when configured.
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get(project.client_id)
    stored = cloudinary_service.upload_asset(video_url, client.slug if client else "unassigned", "video",
        public_id=f"spokesperson-p{project_id}-s{scene_id}-recovered-{media_state.fingerprint(video_url)[:12]}")
    meta["spokesperson_url"] = stored.get("secure_url") or video_url
    meta["spokesperson_mirrored"] = bool(stored.get("secure_url")) and not stored.get("_mock")
    meta["heygen_job"]["video_url"] = video_url
    if not meta.get("spokesperson_over_footage"):
        meta["media"] = "video"
        scene.asset_url = meta["spokesperson_url"]
        scene.asset_type = "spokesperson"
        scene.asset_source = "heygen"
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})
