"""Quality checks + Creatomate rendering (spec sections 12-13)."""

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, RenderJob, Scene
from ..services import qc_service, creatomate_service, cloudinary_service

bp = Blueprint("cb_render", __name__, url_prefix="/api/projects/<int:project_id>")


@bp.post("/qc")
def run_qc(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    scenes = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    results = qc_service.run_qc(project.to_dict(include_scenes=False), client.to_dict(), scenes)
    project.qc_results = results
    project.status = "qc"
    db.session.commit()
    return jsonify({"ok": True, "qc_results": results})


@bp.post("/render")
def submit_render(project_id):
    """Renders one job per requested output format (spec section 15: one
    commercial can fan out to 16:9 / 9:16 / 1:1 from the same storyboard)."""
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}
    formats = data.get("formats") or project.formats or ["16:9"]
    force = data.get("force_despite_qc_failures", False)

    scenes = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    qc = qc_service.run_qc(project.to_dict(include_scenes=False), client.to_dict(), scenes)
    project.qc_results = qc
    if not qc["_all_passed"] and not force:
        db.session.commit()
        return jsonify({"ok": False, "error": "QC checks failed. Fix the flagged items or "
                                               "resubmit with force_despite_qc_failures=true.",
                         "qc_results": qc}), 409

    voice_track_url = (project.music or {}).get("voice_track_url")
    music_track_url = (project.music or {}).get("music_track_url")

    jobs = []
    for fmt in formats:
        source = creatomate_service.build_source(project.to_dict(include_scenes=False), scenes, fmt,
                                                   voice_track_url, music_track_url)
        result = creatomate_service.submit_render(source)
        job = RenderJob(project_id=project.id, format=fmt, provider_render_id=result.get("id"),
                         status=result.get("status", "queued"), output_url=result.get("url"),
                         error=result.get("error"))
        db.session.add(job)
        jobs.append(job)

    project.status = "rendering"
    db.session.commit()
    return jsonify({"ok": True, "render_jobs": [j.to_dict() for j in jobs], "live": creatomate_service.is_live()})


@bp.get("/render-jobs")
def list_render_jobs(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return jsonify({"ok": True, "render_jobs": [j.to_dict() for j in project.render_jobs.all()]})


@bp.get("/render-jobs/<int:job_id>/status")
def check_render_job(project_id, job_id):
    job = RenderJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if job.status not in ("succeeded", "failed"):
        status = creatomate_service.check_render(job.provider_render_id)
        job.status = status.get("status", job.status)
        job.output_url = status.get("url") or job.output_url
        job.error = status.get("error")
        db.session.commit()

        if job.status == "succeeded" and job.output_url:
            project = CommercialProject.query.get(project_id)
            client = Client.query.get(project.client_id)
            cloudinary_service.upload_asset(job.output_url, client.slug, "commercial",
                                             public_id=f"project-{project_id}-{job.format.replace(':', 'x')}")
            if all(j.status == "succeeded" for j in project.render_jobs.all()):
                project.status = "complete"
                db.session.commit()
    return jsonify({"ok": True, "render_job": job.to_dict()})
