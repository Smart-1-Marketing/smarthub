"""The brief -> concepts -> script -> per-scene stills pipeline, as plain
functions rather than three Flask view bodies.

WO-CS4's own words: "Reuse the existing generators; return the same
structured JSON they return today." Before this file, that logic lived
entirely inside `routes/projects.py::generate_concepts`/`generate_script`
and `routes/assets.py::generate_ai_footage` -- correct for the Storyboard
Editor's own synchronous buttons, and unreachable from anywhere else. A
`creative_jobs` runner has no `project_id` in a URL and no request to read
one from, so Creative Studio's own job kinds (WO-CS4) needed the identical
mutations available as a function call. Copying the route bodies into
`modules/creative_studio` would be the exact drift CLAUDE.md spends its own
history undoing -- "two readings of one question drift the day either is
edited" -- so the logic moved here instead, and both the synchronous routes
and the async job runners call it. There is one implementation.

Every function here takes model rows, never a project_id or a Flask
`request` -- a job runner has app context but no request context, and a
route that already has the rows loaded should not pay for a second query.
A validation failure raises `ValueError` with the same sentence the route
used to hand back as a 400; a caller with a `jsonify` decides the status
code, a caller with a `CreativeJob` decides the failure message.
"""
from __future__ import annotations

from .config import DEFAULT_SHOT_GRAMMAR, SHOT_NUMBER_STEP, qr_eligible
from .db import db
from .models import Scene
from .services import openai_service


def with_hub_facts(client) -> dict:
    """The adopted brand profile, plus what the rest of the Hub holds.

    The profile is a copy taken at adoption -- fonts, pronunciation, preferred
    voice -- and it is deliberately one-way, so it does not move when the
    client record does. What it never had is the client's live products, the
    industry on their Knack record and what their last site scan read off
    their own pages, and a model writing a :30 for a client of eleven years
    was working from a name, a color and a tagline.

    `hub/client_context.for_prompt()` is the one reader every AI feature in
    the Hub appends, so a fact added there reaches the commercial, the
    campaign generator and the blog writer alike. It carries what is *not* on
    file with it: a gap a model cannot see is a gap it fills in.

    Never raises, and never writes back to the profile -- adopting is a copy,
    and the copy is the one a person edited.
    """
    profile = client.to_dict()
    try:
        from hub.client_context import for_prompt
        known = for_prompt(client.name or "", client.website or "")
        if known:
            profile["hub_record"] = known
    except Exception:                                       # noqa: BLE001
        pass
    return profile


def run_concepts(project, client) -> list[dict]:
    """Three materially different concepts from `project.brief`.

    Raises `ValueError` when there is no brief yet -- generating concepts
    against nothing to advertise is the empty-input failure this line
    refuses rather than sending a model a blank brief and reporting whatever
    it invents as a real answer.
    """
    if not project.brief or not project.brief.get("what_advertising"):
        raise ValueError("Save a commercial brief before generating concepts.")

    concepts = openai_service.generate_concepts(
        project.brief, with_hub_facts(client), project.commercial_type)
    project.concepts = concepts
    project.selected_concept_id = None
    project.status = "concepts"
    db.session.commit()
    return concepts


def run_script(project, client, *, concept_id: str | None = None) -> dict:
    """The timed script for the selected concept, and the Scene rows it
    implies.

    `concept_id` lets a caller with no concept-picker screen (Creative
    Studio's own jobs, in Phase 1) name which of `project.concepts` to use;
    the Storyboard Editor's own button leaves it `None` and relies on
    `project.selected_concept_id`, exactly as it always has. Raises
    `ValueError` when neither names a real concept -- a script has to be
    written from *something*, and picking one silently would be a decision
    a rep never made.
    """
    if concept_id:
        project.selected_concept_id = concept_id
    concept = next((c for c in (project.concepts or [])
                    if c["id"] == project.selected_concept_id), None)
    if not concept:
        raise ValueError("Select a concept before generating a script.")

    qr_enabled = (bool((project.cta or {}).get("qr_enabled")) if project.cta
                  else qr_eligible(project.length_seconds))
    script = openai_service.generate_script(
        concept, project.length_seconds, project.brief, client.to_dict(),
        platform=project.platform, qr_enabled=qr_enabled)
    project.script = script
    project.status = "scripted"

    # (Re)build Scene rows from the script. Regenerating the script replaces
    # unlocked scenes only, so a user's manually-approved footage choices
    # for earlier scenes survive a script tweak.
    existing = {s.order_index: s for s in project.scenes.all()}
    for idx, sc in enumerate(script["scenes"]):
        is_last = idx == len(script["scenes"]) - 1
        scene = existing.get(idx)
        if scene and scene.locked:
            continue
        if not scene:
            scene = Scene(project_id=project.id, order_index=idx)
            db.session.add(scene)
        scene.start = sc["start"]
        scene.end = sc["end"]
        scene.narration = sc["voiceover"]
        scene.visual_description = sc["visual"]
        scene.is_cta = is_last
        meta = scene.asset_meta or {}
        # A Scene row is a SHOT now, not a beat. What holds a beat together is
        # this metadata: every shot in a beat carries the same label and index,
        # and the Blueprint groups on it. Written here rather than inferred
        # later, because the beat is the model's answer and re-deriving it from
        # timings would be guessing at an argument we were told.
        meta["beat"] = sc.get("beat")
        meta["beat_index"] = sc.get("beat_index")
        meta["grammar"] = sc.get("grammar") or dict(DEFAULT_SHOT_GRAMMAR)
        # Numbered in tens, the way a storyboard is, so a shot inserted between
        # 20 and 30 does not renumber the board.
        meta["shot_no"] = (idx + 1) * SHOT_NUMBER_STEP
        scene.asset_meta = meta
        if is_last:
            scene.asset_type = "cta"
    # drop stale scenes beyond the new scene count
    for idx, scene in existing.items():
        if idx >= len(script["scenes"]) and not scene.locked:
            db.session.delete(scene)

    db.session.commit()
    return script


def run_stills(scene, client, *, option_count: int = 2) -> list[dict]:
    """AI-generated still options for one scene's background.

    Never the logo: this hands the model only `scene.visual_description`, so
    there is no path by which a client's own mark reaches an image model for
    recreation -- the logo slot always takes a Brand Kit logo, drawn by the
    Blueprint's own layer panel, and this function has no way to touch it.
    """
    options = openai_service.generate_ai_stills(
        scene.visual_description or "", client.to_dict(), option_count=option_count)
    meta = scene.asset_meta or {}
    meta["ai_options"] = options
    scene.asset_meta = meta
    db.session.commit()
    return options


# ---------------------------------------------------------------------------
# Voice, render (WO-CS5). Moved out of routes/voices.py and routes/render.py
# for the same reason as above: Creative Studio's own queued "voice" and
# "render" job kinds need the identical mutations the Storyboard Editor's
# synchronous buttons make, and two readings of one question is the drift
# CLAUDE.md spends its own history undoing.
# ---------------------------------------------------------------------------

def run_full_voiceover(project, client, voice_id, *, stability=0.5, style=0.5, speed=1.0):
    """One continuous voiceover track for the whole commercial, stored and
    written onto `project.music["voice_track_url"]` -- the key
    `creatomate_service.build_source` reads at render time.

    This is the non-presenter path of `routes/voices.py::generate_full_voiceover`
    (its presenter-mode branch dispatches per scene through HeyGen instead,
    and stays there rather than being duplicated here -- WO-CS5's own scope
    is the plain voice track). Raises `ValueError` with a caller-facing
    sentence; never leaves `project.music` pointing at nothing playable.
    """
    from .services import elevenlabs_service
    if not voice_id:
        raise ValueError("Choose a voice first.")

    scenes = project.scenes.all()
    full_text = " ".join(s.narration or "" for s in scenes)
    signature = _timeline_signature(scenes)
    result = elevenlabs_service.generate_voiceover(
        text=full_text, voice_id=voice_id, stability=stability, style=style,
        speed=speed, pronunciation_dict=client.pronunciation_dict)
    result["voice_id"] = voice_id
    had_audio = bool(result.get("audio_bytes"))
    stored = _store_voice_track(project, client, result, signature=signature)
    result.pop("audio_bytes", None)
    result.update(stored)

    if result.get("error") or (had_audio and not stored.get("stored")):
        raise ValueError(result.get("error") or stored.get("store_note"))
    return result


def _timeline_signature(scenes):
    from .services import media_state
    return media_state.timeline_signature([s.to_dict() for s in scenes])


def _store_voice_track(project, client, result, signature=None):
    """Put the generated MP3 somewhere the renderer can reach it. Moved
    verbatim from `routes/voices.py` -- see that module's own note on why
    this exists: a voiceover that is generated and not stored is a silent
    commercial, and this is the one place that stores it.
    """
    import hashlib
    import os
    import tempfile
    from .services import cloudinary_service

    audio = result.get("audio_bytes")
    if not audio:
        if result.get("error"):
            return {"stored": False, "store_note": f"ElevenLabs refused it: {result['error']}"}
        return {"stored": False,
                "store_note": ("Mock mode — no ELEVENLABS_API key is set, so no audio "
                               "was produced and the render will have no narration.")}

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            fh.write(audio)
            tmp_path = fh.name
        upload = cloudinary_service.upload_asset(
            tmp_path, client.slug, "voice",
            public_id=f"project-{project.id}-voice-{hashlib.sha256(audio).hexdigest()[:16]}",
            resource_type="video")
    except Exception as exc:                              # noqa: BLE001
        return {"stored": False, "store_note": f"The voice track could not be stored: {exc}"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass   # a leftover temp file costs disk, not a broken upload

    url = upload.get("secure_url")
    if not url or upload.get("_mock"):
        return {"stored": False,
                "store_note": ("The voice track was generated but could not be stored, so "
                               "the render would have no narration.")}

    music = dict(project.music or {})
    music.update(voice_track_url=url, voice_mode="full", voice_signature=signature,
                 voice_track_stale=False)
    project.music = music
    db.session.commit()
    return {"stored": True, "store_note": "Voice track saved.", "voice_track_url": url}


def renders_through_hyperframes(project) -> bool:
    """Which renderer assembles this spot -- read off the project's own type
    rather than stored on the job, because `RenderJob` carries an opaque
    `provider_render_id` and nothing else that says who issued it, so every
    reader of a job has to be able to ask the same question and get the
    same answer. One reading, for the reason two of them always drift."""
    from . import vox_spec
    return (project.commercial_type or "") == vox_spec.COMMERCIAL_TYPE


def _submit_vox(project, client, fmt, voice_track_url=None):
    """One Vox explainer, whole, through the render service. Moved verbatim
    from `routes/render.py` -- see that module's own note: answers in
    `creatomate_service.submit_render`'s shape so the RenderJob row, the
    poll, the approval and the filing path are all the ones every other
    commercial type already uses."""
    from . import vox_spec
    from hub import hyperframes

    beats = (project.script or {}).get("beats") or []
    if len(beats) < vox_spec.MIN_BEATS:
        return {"id": None, "status": "failed", "url": None,
                "error": (f"This explainer has {len(beats)} beat"
                          f"{'' if len(beats) == 1 else 's'} and needs at least "
                          f"{vox_spec.MIN_BEATS}. Write the beat list first.")}
    client_dict = client.to_dict()
    params = hyperframes.vox_params(
        title=project.title or client_dict.get("name") or "",
        beats=beats, format_id=fmt,
        brand_colors=[c for c in (client_dict.get("brand_colors") or []) if c],
        voice_track_url=voice_track_url or "")
    job = hyperframes.submit("vox-explainer", params)
    return {"id": job.get("job_id"), "status": _job_status(job),
            "url": job.get("url"), "error": job.get("error")}


def _job_status(job):
    """The render service's vocabulary in RenderJob's. Moved verbatim from
    `routes/render.py` -- see that module's own note: a "done" left
    untranslated never satisfies the poll's terminal-state guard, so the job
    is re-checked for ever and the panel never stops spinning."""
    state = (job or {}).get("status")
    if state == "done":
        return "succeeded"
    return state if state in ("queued", "rendering", "failed") else "queued"


def submit_render_job(project, client, scenes, fmt, *, voice_track_url=None, music_track_url=None):
    """Submit one render and record it as a `RenderJob` -- the single
    implementation of what `routes/render.py::submit_render` does per
    format, reused by Creative Studio's own "render" job kind so both build
    through the identical Creatomate/HyperFrames source builder rather than
    a second copy of it drifting. Returns the created `RenderJob` row.
    """
    from .models import RenderJob
    from .services import creatomate_service

    if renders_through_hyperframes(project):
        result = _submit_vox(project, client, fmt, voice_track_url)
    else:
        source = creatomate_service.build_source(
            project.to_dict(include_scenes=False), scenes, fmt,
            voice_track_url, music_track_url)
        result = creatomate_service.submit_render(source)

    job = RenderJob(project_id=project.id, format=fmt,
                    provider_render_id=result.get("id"),
                    status=result.get("status", "queued"),
                    output_url=result.get("url"), error=result.get("error"))
    db.session.add(job)
    db.session.commit()
    return job


def poll_render_job(job) -> None:
    """Refresh one `RenderJob`'s status against its provider, in place.
    `routes/render.py`'s own poll route and Creative Studio's "render" job
    runner both call this rather than each reading Creatomate/HyperFrames
    status their own way."""
    from .models import CommercialProject
    from .services import creatomate_service
    from hub import hyperframes

    if job.status in ("succeeded", "failed"):
        return
    project = CommercialProject.query.get(job.project_id)
    vox = bool(project) and renders_through_hyperframes(project)
    if vox:
        state = hyperframes.status(job.provider_render_id)
        job.status = _job_status(state)
        job.output_url = state.get("url") or job.output_url
        job.error = state.get("error")
    else:
        status = creatomate_service.check_render(job.provider_render_id)
        if not status.get("retryable"):
            job.status = status.get("status") or job.status
        job.output_url = status.get("url") or job.output_url
        job.error = status.get("error")
    db.session.commit()
