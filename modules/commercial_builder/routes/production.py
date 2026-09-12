"""Read-only production state and explicitly restored, project-scoped takes."""
from flask import Blueprint, jsonify, request, current_app

from ..db import db
from ..models import CommercialProject, Scene, RenderJob, ProductionTake
from ..history import snapshots, PRESENTER_KEYS, TRACK_KEYS
from ..services import media_state

bp = Blueprint("cb_production", __name__, url_prefix="/api/projects/<int:project_id>")
HOUSEKEEPING_ROUTES = {
    "restore_take": "Restores a draft version; take history retains both versions. No cut is approved or filed.",
}


def current_snapshot(project, scene, kind):
    obj = scene if scene is not None else project
    row = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    return snapshots(row, scene is not None).get(kind)


@bp.get("/production")
def production_status(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    scenes = [s.to_dict() for s in project.scenes.all()]
    issues, generating, saving = [], 0, 0
    for index, scene in enumerate(scenes, 1):
        meta = scene.get("asset_meta") or {}
        job = meta.get("heygen_job") or {}
        if job.get("status") in ("submitting", "unknown") and not job.get("job_id"):
            issues.append(f"Scene {index}: confirm the presenter job in HeyGen before trying again.")
        elif job.get("status") in ("pending", "processing", "submitting") or (
                job.get("status") in ("failed", "unknown") and job.get("job_id") and not job.get("provider_terminal")):
            generating += 1
        elif job.get("status") == "failed" and job.get("provider_terminal"):
            issues.append(f"Scene {index}: HeyGen could not complete the presenter.")
        saving += meta.get("spokesperson_mirrored") is False
        if not scene.get("asset_url") and not scene.get("is_cta") and not job:
            issues.append(f"Scene {index}: choose footage or a presenter.")
    integrity = media_state.integrity(project.to_dict(False), scenes)
    if not integrity["passed"] and not (generating or saving):
        issues.append(integrity["message"])
    jobs = project.render_jobs.order_by(RenderJob.id.desc()).all()
    active = [j for j in jobs if j.status not in ("succeeded", "failed")]
    latest = jobs[0] if jobs else None
    if not scenes:
        label, action, step = "Start your script", "Create your scenes", "concepts"
    elif issues:
        label, action, step = "Needs attention", "Review scenes and narration", "blueprint"
    elif generating:
        label, action, step = "Creating presenter clips", "View presenter progress", "blueprint"
    elif saving:
        label, action, step = "Saving media", "View storage progress", "blueprint"
    elif active:
        label, action, step = "Rendering", "View render progress", "preview"
    elif latest and latest.status == "failed":
        label, action, step = "Render needs attention", "Review the failed render", "preview"
    elif latest and latest.status == "succeeded" and latest.output_url:
        # A historical render is not proof that the current script was rendered.
        label, action, step = "A rendered cut is available", "Review saved cuts", "preview"
    else:
        label, action, step = "Ready for quality checks", "Check and preview", "preview"
    from hub import scheduler
    recovery_state = scheduler.status(current_app._get_current_object())["state"]
    recovery_note = {
        "leading": "Saved jobs continue in the background. You can close this page.",
        "standby": "Saved jobs continue in the background. You can close this page.",
        "off": "Background recovery is off. Use Check status on saved jobs to continue them.",
        "down": "Background recovery is unavailable. Use Check status on saved jobs while the Hub recovers.",
    }[recovery_state]
    return jsonify(ok=True, label=label, action=action, recovery_note=recovery_note,
                   href=f"/tools/commercial-builder/project/{project_id}/{step}",
                   issues=issues, generating=generating, saving=saving, rendering=len(active))


@bp.get("/takes")
def list_takes(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    before = request.args.get("before", type=int)
    query = ProductionTake.query.filter_by(project_id=project_id)
    if before:
        query = query.filter(ProductionTake.id < before)
    rows = query.order_by(ProductionTake.id.desc()).limit(31).all()
    scenes = {s.id: s for s in project.scenes.all()}
    takes = []
    for take in rows[:30]:
        scene = scenes.get(take.scene_id)
        restorable = take.kind == "track" or scene is not None
        current = current_snapshot(project, scene, take.kind) if restorable else None
        takes.append(dict(id=take.id, kind=take.kind, scene_id=take.scene_id,
                          scene_number=scene.order_index + 1 if scene else None,
                          created_at=take.created_at.isoformat() + "Z", snapshot=take.snapshot,
                          current=current, current_digest=media_state.fingerprint(current),
                          restorable=restorable, is_current=current == take.snapshot))
    return jsonify(ok=True, takes=takes, next_before=rows[29].id if len(rows) > 30 else None)


@bp.post("/takes/<int:take_id>/restore")
def restore_take(project_id, take_id):
    project = CommercialProject.query.filter_by(id=project_id).with_for_update().first_or_404()
    take = ProductionTake.query.filter_by(id=take_id, project_id=project_id).first_or_404()
    scene = None
    if take.kind != "track":
        scene = Scene.query.filter_by(id=take.scene_id, project_id=project_id).with_for_update().first()
        if scene is None:
            return jsonify(ok=False, error="This scene was deleted. Its history is still available to copy."), 409
    data = request.get_json(silent=True) or {}
    current = current_snapshot(project, scene, take.kind)
    if data.get("current_digest") != media_state.fingerprint(current):
        return jsonify(ok=False, error="This scene changed. Reload history and review the latest version before restoring."), 409
    value = take.snapshot
    if scene is not None:
        meta = dict(scene.asset_meta or {})
        job = meta.get("heygen_job") or {}
        if job.get("status") in ("pending", "processing", "submitting", "unknown"):
            return jsonify(ok=False, error="Wait for the current presenter job to finish or resolve it before restoring a take."), 409
        if take.kind == "script":
            scene.narration = value.get("narration")
            scene.visual_description = value.get("visual_description")
        elif take.kind == "voice":
            meta["voiceover"] = {**value, "stale": value.get("speech_signature") != media_state.speech_signature(scene.to_dict())}
            scene.asset_meta = meta
        elif take.kind == "presenter":
            if not value.get("spokesperson_mirrored"):
                return jsonify(ok=False, error="This take was not saved to permanent storage. Recover it before restoring."), 409
            for key in PRESENTER_KEYS:
                meta[key] = value.get(key)
            meta.pop("spokesperson_mirror_error", None)
            meta["presenter_stale"] = (value.get("heygen_job") or {}).get("speech_signature") != media_state.speech_signature(scene.to_dict())
            scene.asset_meta = meta
            if value.get("asset"):
                for key in ("asset_type", "asset_source", "asset_url", "asset_thumb_url"):
                    setattr(scene, key, value["asset"].get(key))
            elif not value.get("spokesperson_over_footage"):
                scene.asset_type, scene.asset_source = "spokesperson", "heygen"
                scene.asset_url = value["spokesperson_url"]
    elif take.kind == "track":
        music = dict(project.music or {})
        music.update({k: value.get(k) for k in TRACK_KEYS})
        music["voice_track_stale"] = value.get("voice_signature") != media_state.timeline_signature([s.to_dict() for s in project.scenes.all()])
        project.music = music
    db.session.commit()
    return jsonify(ok=True, message="Take restored. Review timing and quality checks before rendering.")
