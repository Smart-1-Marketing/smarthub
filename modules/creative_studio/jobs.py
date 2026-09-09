"""The creative_jobs sweep -- house rule 4, done in one place.

Nothing long-running happens inside a request. A route that starts work
writes a `CreativeJob` row in state `queued` and returns its id; this sweep,
registered on `hub/scheduler.py` the way `video_tools/alerts.py::sweep()`
already is, is what actually calls a provider, and the UI polls
`/creative/api/jobs/<id>` for `stage`/`progress`/`error` in the meantime.

`index` (WO-CS1), `storyboard`, `script` and `image` (WO-CS4) are implemented.
Voice/heygen/render/variant/pdf are wired into WO-CS5; enqueuing one of those
kinds today writes the row and the sweep marks it failed at its first pass
with a readable reason, rather than a job that "queued" reads as running for
ever with nothing behind it. That is deliberately different from silently
refusing the enqueue: a project should be able to *ask* for a render today
and see honestly that nothing can answer yet, the same distinction
`hub/hyperframes.py` draws between configured, reachable and working.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config, usage
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


def _load_bound(job: CreativeJob):
    """`(cs_project, cb_project, client)` for a job that names a
    `cs_project` already bound to a Commercial Builder storyboard, or raises
    `ValueError` with a sentence the sweep's own try/except turns into the
    job's readable failure. Every generation runner starts here -- one
    reading of "is there something to write onto" rather than three."""
    from .models import CsProject
    from modules.commercial_builder.models import Client as CbClient
    from modules.commercial_builder.models import CommercialProject as CbProject

    project = CsProject.query.get(job.project_id) if job.project_id else None
    if project is None:
        raise ValueError("That project no longer exists.")
    if not project.cb_project_id:
        raise ValueError("This project has not been opened in the Storyboard "
                          "Editor yet.")
    cb_project = CbProject.query.get(project.cb_project_id)
    if cb_project is None:
        raise ValueError("The storyboard this project was bound to no longer exists.")
    client = CbClient.query.get(cb_project.client_id)
    if client is None:
        raise ValueError("That storyboard's brand profile no longer exists.")
    return project, cb_project, client


def _run_storyboard(job: CreativeJob) -> None:
    """Three concepts for a project's brief -- `generation.run_concepts`,
    queued. Binds a template-less project to a blank storyboard first
    (`binder.bind_for_generation`), since a project that has never been
    opened has nothing for a concept to be written onto yet."""
    from . import binder
    from .models import CsProject
    from modules.commercial_builder import generation

    project = CsProject.query.get(job.project_id) if job.project_id else None
    if project is None:
        _fail(job, "That project no longer exists.")
        return
    if not project.cb_project_id:
        result = binder.bind_for_generation(project)
        if not result.get("ok"):
            _fail(job, result.get("error") or "Could not open the storyboard.")
            return

    job.state = "processing"
    job.stage = "Preparing assets"
    db.session.commit()

    try:
        _p, cb_project, client = _load_bound(job)
    except ValueError as exc:
        _fail(job, str(exc))
        return

    job.stage = "Creating scenes"
    db.session.commit()
    try:
        concepts = generation.run_concepts(cb_project, client)
    except ValueError as exc:
        _fail(job, str(exc))
        return
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"{type(exc).__name__}: {exc}")
        return

    usage.record("openai", "concepts", project_id=project.id,
                client_name=project.client_name, quantity=1, unit="call",
                actor=job.created_by)

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"concepts": concepts}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["storyboard"] = _run_storyboard


def _run_script(job: CreativeJob) -> None:
    """The timed script for a project's chosen concept, and the scenes it
    implies -- `generation.run_script`, queued. Creative Studio has no
    concept-picker screen in Phase 1, so a project with concepts but nothing
    selected takes the first one -- `_mock_concepts`' own ordering makes that
    "the direct read," a safe default rather than an arbitrary one."""
    from modules.commercial_builder import generation

    try:
        project, cb_project, client = _load_bound(job)
    except ValueError as exc:
        _fail(job, str(exc))
        return

    job.state = "processing"
    job.stage = "Creating scenes"
    db.session.commit()

    concept_id = None
    if not cb_project.selected_concept_id and cb_project.concepts:
        concept_id = cb_project.concepts[0]["id"]

    try:
        script = generation.run_script(cb_project, client, concept_id=concept_id)
    except ValueError as exc:
        _fail(job, str(exc))
        return
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"{type(exc).__name__}: {exc}")
        return

    usage.record("openai", "script", project_id=project.id,
                client_name=project.client_name, quantity=1, unit="call",
                actor=job.created_by)

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"script": script}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["script"] = _run_script


def _materialize_image(client_name: str, option: dict, index: int) -> tuple[str, str]:
    """`(cloudinary_public_id, url)` for one successful AI-still option.

    A `gpt-image-1` option is a `data:` URI (base64, see
    `openai_service._image_result_url`'s own note on why); a mock or
    `dall-e-*` option is a plain URL. Cloudinary is the durable copy so the
    image is "available as a scene background" past the hour a hosted URL
    would expire -- but a Cloudinary failure, or a mock run with no account
    configured at all, must not cost the option: the option's own URL is
    kept as a fallback rather than the whole still being dropped."""
    url = option.get("url") or ""
    if url.startswith("data:") and ";base64," in url:
        try:
            import base64
            from hub import storage
            header, encoded = url.split(",", 1)
            data = base64.b64decode(encoded)
            asset = storage.put("creative_studio", f"ai-still-{index + 1}.png",
                                data, client=client_name)
            return asset.public_id, asset.url
        except Exception:                                 # noqa: BLE001
            return "", url
    try:
        from hub import storage
        if storage.ready() and url.startswith("http"):
            asset = storage.put_remote("creative_studio", url,
                                       filename=f"ai-still-{index + 1}.png",
                                       client=client_name)
            return asset.public_id, asset.url
    except Exception:                                     # noqa: BLE001
        pass
    return "", url


def _run_image(job: CreativeJob) -> None:
    """AI-still options for one scene's background -- `generation.run_stills`,
    queued, each successful option filed as a `cs_media_assets` row
    (WO-CS4 item 3) so it is available as a scene background from the Media
    Library, not only inside the option the Storyboard Editor drew."""
    from modules.commercial_builder.models import Scene as CbScene

    try:
        project, cb_project, client = _load_bound(job)
    except ValueError as exc:
        _fail(job, str(exc))
        return

    scene_id = (job.payload or {}).get("scene_id")
    scene = CbScene.query.filter_by(id=scene_id, project_id=cb_project.id).first() if scene_id else None
    if scene is None:
        _fail(job, "That scene is not on this project's storyboard.")
        return

    job.state = "processing"
    job.stage = "Preparing assets"
    db.session.commit()

    from modules.commercial_builder import generation
    try:
        options = generation.run_stills(scene, client)
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"{type(exc).__name__}: {exc}")
        return

    media_ids = []
    for i, opt in enumerate(options):
        if not opt.get("url") or opt.get("error"):
            continue
        public_id, url = _materialize_image(project.client_name, opt, i)
        row = CsMediaAsset(
            client_name=project.client_name, asset_type="image",
            filename=f"ai-still-{i + 1}.png", cloudinary_public_id=public_id,
            cloudinary_url=url, source="openai", project_id=project.id,
            created_by=job.created_by)
        row.provider_meta = {"prompt": opt.get("prompt") or "", "scene_id": scene.id}
        db.session.add(row)
        db.session.flush()
        media_ids.append(row.id)
    db.session.commit()

    usage.record("openai", "image", project_id=project.id,
                client_name=project.client_name, quantity=len(options), unit="image",
                actor=job.created_by, ok=bool(media_ids))

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"options": options, "media_asset_ids": media_ids}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["image"] = _run_image


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
