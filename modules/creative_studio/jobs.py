"""The creative_jobs sweep -- house rule 4, done in one place.

Nothing long-running happens inside a request. A route that starts work
writes a `CreativeJob` row in state `queued` and returns its id; this sweep,
registered on `hub/scheduler.py` the way `video_tools/alerts.py::sweep()`
already is, is what actually calls a provider, and the UI polls
`/creative/api/jobs/<id>` for `stage`/`progress`/`error` in the meantime.

`index`, `storyboard`, `script` and `image` (WO-CS4) resolve in one sweep
tick -- a single OpenAI call each. `voice`, `heygen` and `render` (WO-CS5)
cannot: ElevenLabs is synchronous but slow, and HeyGen and Creatomate hand
back a job id and make the caller poll. Those three runners are written to
be called more than once across their lifetime -- once from `state="queued"`
to submit, and again on every later tick while `state` is `"processing"`,
`"rendering"` or `"uploading"`, until they reach `"complete"` or `"failed"`.
`sweep()`'s job-pickup query is what makes that possible: it revisits a job
in any of those in-progress states on every tick, not only ones still
`"queued"`, and a runner that is merely polling (nothing new to report) does
nothing and returns, leaving the job exactly where it was for the next tick
to try again. `attempts` is only ever incremented the moment a job leaves
`"queued"` -- a render is billed at submission, so a poll that finds nothing
new must not count against the one-retry ceiling `config.JOB_MAX_ATTEMPTS`
gives `"render"`; that ceiling matters at submission, not at every check-in
afterward.

`variant` and `pdf` are not implemented; enqueuing either writes the row and
the sweep marks it failed at its first pass with a readable reason, rather
than a job that "queued" reads as running for ever with nothing behind it.
That is deliberately different from silently refusing the enqueue: a project
should be able to *ask* for one today and see honestly that nothing can
answer yet, the same distinction `hub/hyperframes.py` draws between
configured, reachable and working.
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


def _materialize_remote(client_name: str, url: str, filename: str, *, kind: str = "creative_studio"):
    """Cloudinary's durable copy of a provider's own hosted URL, or the URL
    itself if storage is not configured. A HeyGen or Creatomate URL is
    signed and expires; mirroring it is what keeps a media row usable past
    the hour the provider's own link is good for. Never raises past its own
    caller -- a caller that cannot afford to lose the row on a storage
    hiccup catches this and keeps the provider URL rather than filing
    nothing at all."""
    from hub import storage
    if not storage.ready():
        return url, ""
    asset = storage.put_remote(kind, url, filename=filename, client=client_name)
    return asset.url, asset.public_id


def _run_voice(job: CreativeJob) -> None:
    """One continuous voiceover for the whole project -- WO-CS5 item 1.

    Single-tick: ElevenLabs' own call in `elevenlabs_service.generate_voiceover`
    is a synchronous REST request, so there is nothing to poll for. The
    generation itself, and where the MP3 gets stored, is
    `generation.run_full_voiceover` -- the same function the Commercial
    Builder's own plain-track voiceover panel calls, so there is one
    implementation of "cast a voice over this script" rather than two."""
    from modules.commercial_builder import generation

    try:
        project, cb_project, client = _load_bound(job)
    except ValueError as exc:
        _fail(job, str(exc))
        return

    payload = job.payload or {}
    voice_id = payload.get("voice_id") or ""

    job.state = "processing"
    job.stage = "Generating voiceover"
    db.session.commit()

    try:
        result = generation.run_full_voiceover(
            cb_project, client, voice_id,
            stability=payload.get("stability", 0.5),
            style=payload.get("style", 0.5),
            speed=payload.get("speed", 1.0))
    except ValueError as exc:
        _fail(job, str(exc))
        return
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"{type(exc).__name__}: {exc}")
        return

    track_url = result.get("voice_track_url") or ""
    row = CsMediaAsset(
        client_name=project.client_name, asset_type="voiceover",
        filename=f"{project.name or 'voiceover'}.mp3", cloudinary_url=track_url,
        source="elevenlabs", project_id=project.id, created_by=job.created_by)
    db.session.add(row)
    db.session.flush()
    media_id = row.id
    db.session.commit()

    usage.record("elevenlabs", "voice", project_id=project.id,
                client_name=project.client_name, quantity=1, unit="track",
                actor=job.created_by)

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"voice_track_url": track_url, "media_asset_id": media_id}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["voice"] = _run_voice


def _run_heygen(job: CreativeJob) -> None:
    """A spokesperson clip for one scene's narration -- WO-CS5 item 1,
    filed as a `cs_media_assets` row so it is usable as a scene from the
    Media Library, the same shape `_run_image` already uses for AI stills.

    Deliberately self-contained rather than a caller of
    `modules.commercial_builder.routes.heygen`: that module's claim-token
    concurrency guard, chroma-key over-footage compositing and mirror-to-
    Cloudinary bookkeeping are real, well-tested machinery (97 passing
    checks) built around writing straight onto a Commercial Builder scene --
    reusing its private helpers here would make this job a second, riskier
    caller of state that was never meant to be shared. This job asks the
    stateless half of `heygen_service` directly and lands the result in the
    Media Library instead, where the Storyboard Editor's existing
    "Use Client Asset" picker already reads a client's own gallery onto a
    scene (see `test_commercial_wizard.py`'s "Use Client Asset reads the
    client's real gallery too").

    Multi-tick: HeyGen renders asynchronously. `queued` submits and stores
    the provider job id in the job's own payload; every later tick while
    `state == "processing"` polls it, until a video URL lands or the
    provider says failed."""
    from modules.commercial_builder.models import Scene as CbScene
    from modules.commercial_builder.services import heygen_service

    try:
        project, cb_project, client = _load_bound(job)
    except ValueError as exc:
        _fail(job, str(exc))
        return

    payload = dict(job.payload or {})
    avatar_id = payload.get("avatar_id") or ""
    scene_id = payload.get("scene_id")

    if job.state == "queued":
        scene = (CbScene.query.filter_by(id=scene_id, project_id=cb_project.id).first()
                if scene_id else None)
        if scene is None:
            _fail(job, "That scene is not on this project's storyboard.")
            return
        if not avatar_id:
            _fail(job, "Choose a presenter first.")
            return

        job.state = "processing"
        job.stage = "Generating voiceover"
        db.session.commit()

        fmt = (cb_project.formats or ["16:9"])[0]
        result = heygen_service.generate_spokesperson_clip(
            avatar_id, scene.narration or "",
            voice_id=(payload.get("voice_id") or None), format_id=fmt)

        if result.get("status") == "failed":
            _fail(job, result.get("error") or "HeyGen refused the request.")
            return
        if result.get("status") == "completed" and not result.get("video_url"):
            # Mock mode -- no HEYGEN_API key set. Reports success and
            # produces no clip: filed as a failure rather than a media row
            # with no file behind it, the render runner's own rule below.
            _fail(job, "No HEYGEN_API key is set, so this generation is a "
                       "mock: it reported success and produced no clip. "
                       "Nothing was filed.")
            return
        if result.get("status") == "completed":
            payload["video_url"] = result.get("video_url")
        else:
            payload["job_id"] = result.get("job_id")
        payload["scene_id"] = scene_id
        payload["avatar_id"] = avatar_id
        job.payload = payload
        job.stage = "Rendering video"
        db.session.commit()
        if not payload.get("video_url"):
            return   # HeyGen is still generating -- the next tick polls it

    if not payload.get("video_url"):
        status = heygen_service.check_status(payload.get("job_id"))
        if status.get("status") == "failed":
            _fail(job, status.get("error") or "HeyGen reported the generation failed.")
            return
        if status.get("status") != "completed":
            return   # still processing on HeyGen's side -- try again next tick
        if not status.get("video_url"):
            _fail(job, "HeyGen reported the clip complete but returned no video URL.")
            return
        payload["video_url"] = status["video_url"]
        job.payload = payload
        db.session.commit()

    video_url = payload.get("video_url")
    if not video_url:
        return

    job.stage = "Uploading"
    db.session.commit()

    try:
        url, public_id = _materialize_remote(
            project.client_name, video_url, f"spokesperson-{scene_id}.mp4")
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"The finished clip could not be stored: {exc}")
        return

    row = CsMediaAsset(
        client_name=project.client_name, asset_type="video",
        filename=f"spokesperson-{scene_id}.mp4", cloudinary_public_id=public_id,
        cloudinary_url=url, source="heygen", project_id=project.id,
        created_by=job.created_by)
    row.provider_meta = {"avatar_id": avatar_id, "scene_id": scene_id}
    db.session.add(row)
    db.session.flush()
    media_id = row.id
    db.session.commit()

    usage.record("heygen", "spokesperson", project_id=project.id,
                client_name=project.client_name, quantity=1, unit="clip",
                actor=job.created_by)

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"video_url": url, "scene_id": scene_id, "media_asset_id": media_id}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["heygen"] = _run_heygen


def _run_render(job: CreativeJob) -> None:
    """Render this project's video through Creatomate -- WO-CS5 item 2.
    Creatomate is the only render engine; nothing here adds Remotion or
    FFmpeg.

    Deliberately does not touch `modules.commercial_builder`'s own
    `RenderJob` / `RenderApproval` tables. Those back the Commercial
    Builder's per-format, approve-to-unlock-the-next-size workflow, which is
    a different shape from Creative Studio's one-render-per-version
    pipeline -- reusing them here would mean two callers deciding what
    "approved" means about one row. What IS reused is
    `creatomate_service.build_source` / `submit_render` / `check_render`
    themselves (the same three calls `generation.submit_render_job` /
    `poll_render_job` make for the Commercial Builder), so a Creative Studio
    render carries the identical ducking, persistent-logo, QR and end-card
    behaviour a Commercial Builder render does -- one implementation of "how
    do we ask Creatomate for a video," never a second one.

    Multi-tick: `queued` builds the source and submits, storing whatever
    Creatomate hands back in the job's own payload; every later tick while
    `state == "rendering"` polls `check_render` until a URL lands. A poll
    that could not reach Creatomate (`check_render`'s own `retryable`
    shape, carrying no `"status"` key) says nothing about whether the paid
    render itself failed, so it changes nothing and is tried again next
    tick -- the overdue-timeout sweep is what eventually gives up on a
    render that genuinely never resolves.
    """
    from .models import CsProjectVersion
    from modules.commercial_builder.models import Scene as CbScene
    from modules.commercial_builder.services import creatomate_service

    try:
        project, cb_project, client = _load_bound(job)
    except ValueError as exc:
        _fail(job, str(exc))
        return

    payload = dict(job.payload or {})
    fmt = payload.get("format") or (cb_project.formats or ["16:9"])[0]

    if job.state == "queued":
        job.state = "rendering"
        job.stage = "Rendering video"
        db.session.commit()

        scenes = [s.to_dict() for s in cb_project.scenes.order_by(CbScene.order_index).all()]
        voice_track_url = (cb_project.music or {}).get("voice_track_url")
        music_track_url = (cb_project.music or {}).get("music_track_url")
        source = creatomate_service.build_source(
            cb_project.to_dict(include_scenes=False), scenes, fmt,
            voice_track_url, music_track_url)
        result = creatomate_service.submit_render(source)

        if result.get("status") == "failed":
            _fail(job, result.get("error") or "Creatomate refused the render.")
            return
        if result.get("status") == "succeeded" and not result.get("url"):
            # Mock mode -- no CREATOMATE_API_KEY set. Reports success
            # instantly and produces nothing: `approve_render`'s own rule
            # (a mock render is refused rather than filed as a delivered
            # commercial), applied here before a version row exists rather
            # than after.
            _fail(job, "No CREATOMATE_API_KEY is set, so this render is a "
                       "mock: it reported success and produced no file. "
                       "Nothing was filed.")
            return
        if result.get("status") == "succeeded" and result.get("url"):
            payload["render_url"] = result["url"]
        else:
            payload["provider_render_id"] = result.get("id")
        payload["format"] = fmt
        job.payload = payload
        db.session.commit()
        if not payload.get("render_url"):
            return   # still rendering -- the next tick polls it

    if not payload.get("render_url"):
        status = creatomate_service.check_render(payload.get("provider_render_id"))
        if "status" not in status:
            return   # could not reach Creatomate -- try again next tick
        if status["status"] == "failed":
            _fail(job, status.get("error") or "Creatomate reported the render failed.")
            return
        if status["status"] == "succeeded" and not status.get("url"):
            _fail(job, "Creatomate reported the render complete but returned no file.")
            return
        if status["status"] != "succeeded":
            return   # still queued/rendering on Creatomate's side
        payload["render_url"] = status["url"]
        job.payload = payload
        db.session.commit()

    render_url = payload.get("render_url")
    if not render_url:
        return

    job.state = "uploading"
    job.stage = "Uploading"
    db.session.commit()

    try:
        url, public_id = _materialize_remote(
            project.client_name, render_url, f"{project.name or 'commercial'}.mp4")
    except Exception as exc:                              # noqa: BLE001
        _fail(job, f"The finished video could not be stored: {exc}")
        return

    # Never updated in place -- CsProjectVersion's own docstring. A version
    # is written once and stands; approving one files THAT version rather
    # than whatever the row now happens to hold.
    last = project.versions.order_by(CsProjectVersion.version.desc()).first()
    next_version = (last.version if last else 0) + 1
    version = CsProjectVersion(project_id=project.id, version=next_version,
                               render_url=url, thumbnail_url="",
                               creatomate_render_id=payload.get("provider_render_id") or "",
                               created_by=job.created_by)
    db.session.add(version)

    media_row = CsMediaAsset(
        client_name=project.client_name, asset_type="video",
        filename=f"{project.name or 'commercial'}-v{next_version}.mp4",
        cloudinary_public_id=public_id, cloudinary_url=url,
        source="creatomate", project_id=project.id, created_by=job.created_by)
    db.session.add(media_row)

    project.status = "Internal Review"
    db.session.commit()

    usage.record("creatomate", "render", project_id=project.id,
                client_name=project.client_name, quantity=1, unit="render",
                actor=job.created_by)

    job.state = "complete"
    job.stage = "Complete"
    job.progress = 100
    job.output = {"version": next_version, "render_url": url}
    job.finished_at = datetime.utcnow()
    db.session.commit()


_RUNNERS["render"] = _run_render


def sweep(app=None, limit: int = 20) -> dict:
    """Advance every queued/in-progress job by one step. Registered on
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

    # Multi-tick jobs (voice/heygen/render) that submitted on an earlier tick
    # and are waiting on a provider. A job the overdue loop above just failed
    # no longer matches this filter -- `_fail()` commits its new state
    # immediately -- so there is no double-handling to guard against here.
    # `attempts` is deliberately NOT incremented on a revisit: it is billed
    # at submission (the "queued" pickup below), and a poll that finds
    # nothing new must not count against a kind's one-retry ceiling.
    in_progress = (CreativeJob.query
                   .filter(CreativeJob.state.in_(["processing", "rendering", "uploading"]))
                   .order_by(CreativeJob.created_at.asc()).limit(limit).all())
    for job in in_progress:
        runner = _RUNNERS.get(job.kind)
        if runner is None:
            continue
        try:
            runner(job)
            processed += 1
        except Exception as exc:                          # noqa: BLE001
            if (job.attempts or 0) >= (job.max_attempts or config.DEFAULT_MAX_ATTEMPTS):
                _fail(job, f"{type(exc).__name__}: {exc}"[:2000])
                failed += 1
            # else: leave it exactly where it is. The next tick tries again,
            # and the overdue-timeout check above is what eventually gives
            # up on one that genuinely never resolves.

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
