"""The creative_jobs sweep -- house rule 4, done in one place.

Nothing long-running happens inside a request. A route that starts work
writes a `CreativeJob` row in state `queued` and returns its id; this sweep,
registered on `hub/scheduler.py` the way `video_tools/alerts.py::sweep()`
already is, is what actually calls a provider, and the UI polls
`/creative/api/jobs/<id>` for `stage`/`progress`/`error` in the meantime.

Only `kind="index"` is implemented in this change -- the Media Library
backfill. Script/storyboard/image/voice/heygen/render/variant/pdf are wired
into WO-CS4 and WO-CS5; enqueuing one of those kinds today writes the row and
the sweep marks it failed at its first pass with a readable reason, rather
than a job that "queued" reads as running for ever with nothing behind it.
That is deliberately different from silently refusing the enqueue: a project
should be able to *ask* for a script today and see honestly that nothing can
answer yet, the same distinction `hub/hyperframes.py` draws between
configured, reachable and working.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config
from .db import db
from .models import CreativeJob, CsMediaAsset

# Kinds this sweep can actually run today. Anything else in config.JOB_KINDS
# is a declared destination with no engine behind it yet.
_RUNNERS: dict = {}


def _timeout_for(kind: str) -> datetime:
    minutes = config.JOB_TIMEOUT_MINUTES.get(kind, 15)
    return datetime.utcnow() + timedelta(minutes=minutes)


def enqueue(kind: str, *, project_id: int | None = None, client_name: str = "",
           payload: dict | None = None, created_by: str = "") -> CreativeJob:
    """Write a queued job. Never calls a provider -- that is the sweep's job,
    so a route that enqueues one returns to the browser immediately."""
    max_attempts = config.JOB_MAX_ATTEMPTS.get(kind, config.DEFAULT_MAX_ATTEMPTS)
    job = CreativeJob(kind=kind, project_id=project_id, client_name=client_name or "",
                      state="queued", stage="Preparing assets", progress=0,
                      attempts=0, max_attempts=max_attempts,
                      timeout_at=_timeout_for(kind), created_by=created_by or "")
    job.payload = payload or {}
    db.session.add(job)
    db.session.commit()
    return job


def _fail(job: CreativeJob, message: str) -> None:
    job.state = "failed"
    job.error = message[:2000]
    job.finished_at = datetime.utcnow()
    db.session.commit()


def _run_index(job: CreativeJob) -> None:
    """Walk this client's Creative Studio Cloudinary folder and write any
    asset this deployment does not already have a `CsMediaAsset` row for.

    Deliberately scoped to the folder this module writes into rather than
    every tool's own tree -- reading a dozen other modules' Cloudinary
    layouts correctly is real work with its own edge cases (`hub/storage.py`
    is the shared writer, not a shared *reader* of every legacy folder
    shape), and overclaiming "every asset this Hub holds for this client" on
    a Phase 1 sweep that only reaches one folder is exactly the confident
    wrong answer this codebase spends a great deal of its own history
    undoing. What this indexes is real: everything Creative Studio itself
    has ever produced or accepted an upload for. Reaching into the other
    galleries is real future work, named here rather than half-claimed.
    """
    from hub import storage
    from hub.config import settings

    client = job.client_name or ""
    job.state = "processing"
    job.stage = "Indexing Cloudinary"
    db.session.commit()

    prefix = settings.folder("creative_studio")
    if client:
        prefix = f"{prefix}/{storage.slug(client, 'client')}"

    try:
        rows = storage.manifest("creative_studio", max_results=500, prefix=prefix)
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"Could not list Cloudinary: {type(exc).__name__}")
        return

    known = {r.cloudinary_public_id for r in
             CsMediaAsset.query.filter_by(client_name=client).all()}
    added = 0
    for r in rows:
        pid = r.get("public_id") or ""
        if not pid or pid in known:
            continue
        rtype = r.get("resource_type") or "image"
        asset_type = "video" if rtype == "video" else "image"
        row = CsMediaAsset(
            client_name=client, asset_type=asset_type,
            filename=pid.rsplit("/", 1)[-1], original_filename="",
            cloudinary_public_id=pid, cloudinary_url=r.get("secure_url") or "",
            file_size=r.get("bytes"), source="cloudinary_transform")
        db.session.add(row)
        added += 1
    db.session.commit()

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"indexed": added, "seen": len(rows)}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["index"] = _run_index


def sweep(app=None, limit: int = 20) -> dict:
    """Advance every queued/processing job by one step. Registered on
    `hub/scheduler.py`'s JOBS table; never called from a request."""
    now = datetime.utcnow()
    processed, timed_out, failed = 0, 0, 0

    overdue = (CreativeJob.query
               .filter(CreativeJob.state.in_(["queued", "processing", "rendering", "uploading"]))
               .filter(CreativeJob.timeout_at.isnot(None))
               .filter(CreativeJob.timeout_at < now)
               .limit(limit).all())
    for job in overdue:
        _fail(job, "Timed out -- the job did not finish in time.")
        timed_out += 1

    queued = (CreativeJob.query.filter_by(state="queued")
              .order_by(CreativeJob.created_at.asc()).limit(limit).all())
    for job in queued:
        job.attempts = (job.attempts or 0) + 1
        job.started_at = job.started_at or datetime.utcnow()
        db.session.commit()
        runner = _RUNNERS.get(job.kind)
        if runner is None:
            _fail(job, f"'{job.kind}' is not built yet in this deployment.")
            failed += 1
            continue
        try:
            runner(job)
            processed += 1
        except Exception as exc:                          # noqa: BLE001
            if (job.attempts or 0) >= (job.max_attempts or config.DEFAULT_MAX_ATTEMPTS):
                _fail(job, f"{type(exc).__name__}: {exc}"[:2000])
                failed += 1
            else:
                job.state = "queued"
                db.session.commit()

    return {"ok": True, "processed": processed, "timed_out": timed_out, "failed": failed}


def job_sweep(app):
    """The hub/scheduler.py entry point. Pushes its own app context, the
    shape every job_* function in hub/scheduler.py uses -- `_run_job` does
    not push one for its caller, so a job that needs the database has to.
    Nothing here reads request-scoped state, so the flask.g trap CLAUDE.md
    names for the Google sweep does not apply."""
    with app.app_context():
        return sweep(app)
