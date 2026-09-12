"""Resume paid jobs without resubmitting generation. Shared by CLI and scheduler."""
import time
import click
from sqlalchemy.exc import IntegrityError

from .db import db
from .models import Scene, RenderJob, RecoveryAttempt
from .routes.heygen import spokesperson_status
from .generation import poll_render_job
from .services.media_state import fingerprint


def needs_presenter_recovery(scene):
    meta = scene.asset_meta or {}
    job = meta.get("heygen_job") or {}
    return bool((job.get("job_id") or job.get("recovered")) and not job.get("provider_terminal")
                and (job.get("status") in ("pending", "processing", "failed", "unknown")
                     or meta.get("spokesperson_mirrored") is False))


def _claim(key, now):
    if db.session.get(RecoveryAttempt, key) is None:
        try:
            with db.session.begin_nested():
                db.session.add(RecoveryAttempt(key=key))
        except IntegrityError:
            pass
    claimed = RecoveryAttempt.query.filter_by(key=key).filter(
        RecoveryAttempt.next_at <= now, RecoveryAttempt.lease_until <= now).update(
            {"lease_until": now + 600}, synchronize_session=False)
    db.session.commit()
    return bool(claimed)


def recover_pending(limit=8, budget_seconds=45):
    """Bound the batch; durable retry times keep failed jobs from starving others.

    Budget is checked between provider calls, which also have their own timeouts.
    Leases exclude concurrent background workers. Browser polls retain the existing
    job-ID checks and stable storage IDs. No generation endpoint is called.
    """
    started = time.monotonic()
    now = time.time()
    attempts = RecoveryAttempt.query.all()
    due = {r.key for r in attempts if r.next_at > now or r.lease_until > now}
    last = {r.key: r.next_at for r in attempts}
    candidates = []
    for scene in Scene.query.filter(Scene.asset_meta_json.contains('"heygen_job"')).order_by(Scene.id).yield_per(100):
        job = (scene.asset_meta or {}).get("heygen_job") or {}
        key = "presenter:" + fingerprint((scene.id, job.get("job_id") or job.get("video_url") or "recovered"))
        if key not in due and needs_presenter_recovery(scene):
            candidates.append((key, "presenter", scene.project_id, scene.id))
    # New and least-recently-checked jobs go first, even when every old job
    # becomes due again at the next scheduler tick.
    candidates.sort(key=lambda item: last.get(item[0], 0))
    candidates = candidates[:limit]
    renders = [(f"render:{j.id}", "render", j.project_id, j.id)
               for j in RenderJob.query.filter(RenderJob.status.notin_(("succeeded", "failed")),
                                               RenderJob.provider_render_id.isnot(None)).order_by(RenderJob.id).all()
               if f"render:{j.id}" not in due]
    renders.sort(key=lambda item: last.get(item[0], 0))
    renders = renders[:limit]
    queue = []
    for i in range(max(len(candidates), len(renders))):
        queue.extend(group[i] for group in (candidates, renders) if i < len(group))
    queue.sort(key=lambda item: last.get(item[0], 0))
    summary = {"checked": 0, "errors": 0, "pending": 0}
    for key, kind, project_id, item_id in queue[:limit]:
        if time.monotonic() - started >= budget_seconds:
            break
        if not _claim(key, time.time()):
            continue
        error, pending = None, False
        try:
            if kind == "presenter":
                response = spokesperson_status(project_id, item_id)
                if isinstance(response, tuple):
                    response = response[0]
                result = response.get_json()
                scene = db.session.get(Scene, item_id)
                pending = bool(scene and needs_presenter_recovery(scene))
                error = "Media storage is still pending" if result.get("storage_pending") else None
                if pending and result.get("status") in ("failed", "unknown"):
                    error = "Provider status is temporarily unavailable"
            else:
                job = db.session.get(RenderJob, item_id)
                if job:
                    poll_render_job(job)
                    pending = job.status not in ("succeeded", "failed")
                    error = "Provider status is temporarily unavailable" if pending and job.error else None
        except Exception as exc:
            db.session.rollback()
            error, pending = type(exc).__name__, True
        attempt = db.session.get(RecoveryAttempt, key)
        attempt.attempts = attempt.attempts + 1 if error else 0
        attempt.next_at = time.time() + (min(1800, 60 * 2 ** min(attempt.attempts, 5)) if pending else 86400)
        attempt.lease_until = 0
        attempt.last_error = error
        db.session.commit()
        summary["checked"] += 1
        summary["errors"] += bool(error)
        summary["pending"] += pending
    return summary


def install(app):
    @app.cli.command("commercial-recover-presenters")
    @click.option("--limit", default=8, type=click.IntRange(1, 500))
    def recover(limit):
        """Check saved presenter and render jobs; never generate a new paid take."""
        click.echo(recover_pending(limit=limit))
