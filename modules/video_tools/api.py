"""The JSON behind both tool pages.

One set of endpoints, attached to each tool's blueprint by `attach()`. The
tools differ in three routes -- analyse, plan, preflight -- and share the
other six, and sharing them is the point: a source picker, a job list and a
save button that behaved differently on two pages of the same module would be
two bugs waiting to be found separately.

Every write route returns the job row, so the page never has to reconstruct
what the server decided. That is the same rule the plan/options split in
models.py follows: the browser displays the server's answer rather than
computing its own alongside it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import jsonify, request

from . import alerts, config, edits, reframe, silence, sources, waveform
from .db import db
from .models import VideoJob


def _actor() -> str:
    """Who is doing this, read the way the rest of the Hub reads it.

    `hub.current_user()`, and not `flask.session` -- nothing in this Hub has
    ever written a name into the session. Identity is the signed `s1hub_auth`
    cookie, mirrored for a page framed inside Smart 1 Suite, and
    `current_user()` is the one reader of both. Asking the session for it
    returns "" on every call, on every request, for ever: the job row records
    nobody, `audit.log()` drops an empty actor rather than writing it, and
    every screen still reports a clean success -- which is the unattributable
    write this repo has had to undo in seven modules already.
    """
    try:
        from hub import current_user
        return str(current_user() or "")[:60]
    except Exception:                                 # noqa: BLE001 — standalone
        return ""


# `_actor` is defined ABOVE this, and the order is load-bearing rather than
# tidy. `for_module()` takes the function itself, so the name has to exist by
# the time this line runs -- and a NameError here would be caught by the
# `except` below and silently swap the real logger for the no-op stub, so
# every row this module writes would vanish with nothing anywhere saying so.
# That is the same failure the docstring above describes, arriving through a
# different door: it costs the whole log rather than the actor on it.
#
# The actor is bound HERE rather than passed at each call site, because
# `for_module()` with no `actor_fn` writes `actor=None` and `audit.log()`
# drops a falsy actor without comment -- so every row would name nobody, and
# no check anywhere reports an entry that is merely anonymous. Bound once,
# the route added next month is attributed without having to remember.
try:
    from hub import audit as _hub_audit
    _log = _hub_audit.for_module("video_tools", _actor)
except Exception:                                     # noqa: BLE001 — standalone
    def _log(*_a, **_k):
        return None

try:
    from hub.webargs import clamp_int
except Exception:                                     # noqa: BLE001
    def clamp_int(raw, default, low=1, high=200):
        try:
            return max(low, min(high, int(float(raw))))
        except (TypeError, ValueError):
            return default


def _fail(message: str, code: int = 400):
    return jsonify({"error": str(message)}), code


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _source_from(body: dict) -> dict:
    return sources.describe(sources.resolve(body.get("source")))


def attach(bp, tool: str):
    """Hang this module's routes on one tool's blueprint.

    `tool` is "dead_air" or "reframe" and is closed over rather than read from
    the request: the two blueprints are mounted at different prefixes and the
    job rows they write must be attributable without parsing a URL.
    """

    # ------------------------------------------------------------ sources

    @bp.get("/api/sources")
    def api_sources():
        return jsonify({
            "ready": sources.ready(),
            "items": sources.recent(request.args.get("q") or "",
                                    clamp_int(request.args.get("limit"), 24, 1, 60)),
        })

    @bp.post("/api/source")
    def api_source():
        """Resolve whatever was pasted, and report the clip's real shape."""
        try:
            return jsonify(_source_from(_body()))
        except sources.SourceError as exc:
            return _fail(exc)

    # ------------------------------------------------------ analysis/plan

    if tool == "dead_air":

        @bp.post("/api/analyze")
        def api_analyze():
            """Read the audio and propose a cut list. Renders nothing.

            Separate from `/api/render` on purpose: this is the step a person
            looks at, and it has to be cheap enough to re-run while they move
            the sliders. It costs one small PNG.
            """
            body = _body()
            try:
                src = _source_from(body)
                read = waveform.read(sources.base_url(), src["public_id"],
                                     src["duration"])
            except (sources.SourceError, waveform.WaveformError) as exc:
                return _fail(exc)
            plan = silence.plan(
                read["levels"],
                seconds_per_column=read["seconds_per_column"],
                duration=read["duration"],
                gap=_num(body.get("gap"), config.DEFAULT_GAP),
                breath=_num(body.get("breath"), config.DEFAULT_BREATH),
                sensitivity=str(body.get("sensitivity") or config.DEFAULT_SENSITIVITY),
                trim_ends=body.get("trim_ends", True) is not False,
            )
            return jsonify({
                "source": src,
                "waveform": {"url": read["url"], "levels": read["levels"],
                             "seconds_per_column": read["seconds_per_column"]},
                "plan": plan,
                "preview_url": edits.derived_url(
                    src["public_id"],
                    silence.concat_transformation(src["public_id"],
                                                  plan["segments"])),
            })

    if tool == "reframe":

        @bp.post("/api/plan")
        def api_plan():
            """The crop, its arithmetic, and a preview URL. Renders nothing.

            A reframe needs no analysis pass — the transformation is decided
            by the options alone — so this is instant and the preview player
            can be pointed straight at it. Cloudinary builds the derived asset
            on that first request; for a short spot that is a second or two,
            which is why the render step below exists for anything longer.
            """
            body = _body()
            try:
                src = _source_from(body)
            except sources.SourceError as exc:
                return _fail(exc)
            plan = reframe.plan(
                source_width=src["width"], source_height=src["height"],
                ratio=str(body.get("ratio") or config.DEFAULT_RATIO),
                mode=str(body.get("mode") or config.DEFAULT_MODE),
                focus=str(body.get("focus") or config.DEFAULT_FOCUS),
                mute=bool(body.get("mute")),
            )
            return jsonify({
                "source": src, "plan": plan,
                "preview_url": edits.derived_url(src["public_id"],
                                                 plan["transformation"]),
            })

        @bp.post("/api/preflight")
        def api_preflight():
            """Three frames, read by the vision model, before anything is cut.

            Never automatic. It costs a vision call per run, and the module it
            sits beside settled that question already: Commercial Builder's AI
            generate button does not fire on its own for the same reason.
            What it buys is the one thing a preview does not give you quickly
            — a sentence naming what is about to leave the frame.
            """
            body = _body()
            try:
                src = _source_from(body)
            except sources.SourceError as exc:
                return _fail(exc)
            try:
                from hub import ai
            except Exception:                         # noqa: BLE001
                return _fail("This deployment has no OpenAI access, so the "
                             "frame check is unavailable.", 503)
            if not ai.ready():
                return _fail("OPENAI_API_KEY is not set, so the frame check "
                             "is unavailable.", 503)
            frames = _frames(src)
            if not frames:
                return _fail("This clip's duration is unknown, so frames "
                             "cannot be sampled from it.")
            ratio = str(body.get("ratio") or config.DEFAULT_RATIO)
            mode = str(body.get("mode") or config.DEFAULT_MODE)
            try:
                answer = ai.vision(reframe.preflight_prompt(ratio, mode),
                                   frames, module="video_tools",
                                   purpose="reframe_preflight",
                                   max_tokens=500)
            except Exception as exc:                  # noqa: BLE001
                return _fail(f"The frame check did not complete: {exc}", 502)
            _log("reframe_preflight", source=src["public_id"], ratio=ratio)
            return jsonify({"frames": frames, "reading": answer})

    # ------------------------------------------------------------- render

    @bp.post("/api/render")
    def api_render():
        """Turn an approved plan into a job, and ask Cloudinary to build it.

        The plan is recomputed here from the options rather than accepted from
        the browser. A page can be edited; a cut list posted from one is a
        list of offsets this server would then apply to a client's video
        without ever having looked at the audio.
        """
        body = _body()
        try:
            src = _source_from(body)
        except sources.SourceError as exc:
            return _fail(exc)

        options, plan, transformation, error = _build(tool, src, body)
        if error:
            return _fail(error)
        if not transformation:
            return _fail("These settings change nothing about the clip, so "
                         "there is nothing to render.")

        job = VideoJob(tool=tool, source_public_id=src["public_id"],
                       source_duration=src["duration"],
                       source_width=src["width"], source_height=src["height"],
                       client_name=str(body.get("client_name") or "")[:200],
                       actor=_actor(), transformation=transformation,
                       status="building")
        job.options = options
        job.plan = plan
        db.session.add(job)
        db.session.commit()

        try:
            state = edits.submit(src["public_id"], transformation)
        except sources.SourceError as exc:
            job.status, job.error = "failed", str(exc)
            db.session.commit()
            return _fail(exc)
        _apply(job, state)
        db.session.commit()
        _log(f"{tool}_render", source=src["public_id"], job=job.id,
             status=job.status)
        return jsonify(job.as_dict())

    # --------------------------------------------------------------- jobs

    @bp.get("/api/jobs")
    def api_jobs():
        since = datetime.utcnow() - timedelta(days=14)
        rows = (VideoJob.query
                .filter(VideoJob.tool == tool, VideoJob.created_at >= since)
                .order_by(VideoJob.id.desc()).limit(25).all())
        return jsonify({"items": [r.as_dict() for r in rows]})

    @bp.get("/api/jobs/<int:job_id>")
    def api_job(job_id: int):
        """Where one job has got to, asking Cloudinary if it is still open.

        Polling happens here rather than in the browser because the browser
        cannot ask Cloudinary whether a *derived* asset is finished — the URL
        answers 200 either way once it exists, and a player pointed at one
        that is still building shows a first frame and stalls.
        """
        job = VideoJob.query.get(job_id)
        if not job or job.tool != tool:
            return _fail("No such job.", 404)
        if job.status == "building":
            if _timed_out(job):
                job.status = "failed"
                job.error = ("Cloudinary has not finished this edit after "
                             f"{config.JOB_TIMEOUT_SECONDS // 60} minutes. It "
                             "may still land — reload this page later — but "
                             "the page has stopped waiting.")
                db.session.commit()
            else:
                try:
                    _apply(job, edits.poll(job.source_public_id,
                                           job.transformation))
                except sources.SourceError as exc:
                    job.status, job.error = "failed", str(exc)
                db.session.commit()
        # Somebody watching the job has been told about it, so it must not
        # also arrive as a popup on the next page they open. The scheduler
        # sweep is what catches the other case -- they left, it finished, and
        # nothing on their screen was asking.
        if job.status in ("done", "failed") and not job.seen_at:
            alerts.mark_seen([job.id], job.actor or _actor())
        return jsonify(job.as_dict())

    @bp.post("/api/jobs/<int:job_id>/save")
    def api_save(job_id: int):
        """File the finished edit as an asset of its own."""
        job = VideoJob.query.get(job_id)
        if not job or job.tool != tool:
            return _fail("No such job.", 404)
        if job.status != "done" or not job.result_url:
            return _fail("That edit has not finished building yet.")
        if job.saved_public_id:
            return jsonify(job.as_dict())
        name = str((_body().get("client_name") or "")).strip()[:200]
        if name:
            job.client_name = name
        try:
            edits.save(job, url=job.result_url, filename=edits.output_name(job))
        except Exception as exc:                      # noqa: BLE001
            return _fail(f"The edit was built but could not be stored: {exc}",
                         502)
        db.session.commit()
        _log(f"{tool}_saved", source=job.source_public_id, job=job.id,
             public_id=job.saved_public_id, client=job.client_name or None)
        return jsonify(job.as_dict())

    return bp


# ---------------------------------------------------------------- helpers


def _num(raw, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _frames(src: dict, count: int = 3) -> list[str]:
    """Evenly spaced stills, sampled inside the clip.

    Inside rather than at the ends, for the reason
    hub/video_library.keyframe_urls() gives: the first frame of a spot is
    often a fade from black, and a vision model asked what is in the corners
    of black will say nothing is.
    """
    duration = float(src.get("duration") or 0)
    if duration <= 0:
        return []
    base = sources.base_url()
    if not base:
        return []
    from urllib.parse import quote
    pid = quote(str(src.get("public_id") or ""), safe="/")
    step = duration / (count + 1)
    out = []
    for i in range(count):
        second = round(step * (i + 1), 2)
        second = int(second) if second == int(second) else second
        out.append(f"{base}/so_{second}/w_1280,q_auto,f_jpg/{pid}.jpg")
    return out


def _build(tool: str, src: dict, body: dict):
    """(options, plan, transformation, error) for whichever tool asked."""
    if tool == "dead_air":
        options = {
            "gap": _num(body.get("gap"), config.DEFAULT_GAP),
            "breath": _num(body.get("breath"), config.DEFAULT_BREATH),
            "sensitivity": str(body.get("sensitivity") or config.DEFAULT_SENSITIVITY),
            "trim_ends": body.get("trim_ends", True) is not False,
        }
        try:
            read = waveform.read(sources.base_url(), src["public_id"],
                                 src["duration"])
        except waveform.WaveformError as exc:
            return options, {}, "", str(exc)
        plan = silence.plan(read["levels"],
                            seconds_per_column=read["seconds_per_column"],
                            duration=read["duration"], **options)
        if not plan["cuts"]:
            return options, plan, "", ("Nothing to cut at these settings, so "
                                       "the result would be a copy of the "
                                       "original.")
        return options, plan, silence.concat_transformation(
            src["public_id"], plan["segments"]), ""

    options = {
        "ratio": str(body.get("ratio") or config.DEFAULT_RATIO),
        "mode": str(body.get("mode") or config.DEFAULT_MODE),
        "focus": str(body.get("focus") or config.DEFAULT_FOCUS),
        "mute": bool(body.get("mute")),
    }
    plan = reframe.plan(source_width=src["width"], source_height=src["height"],
                        **options)
    return options, plan, plan["transformation"], ""


def _apply(job, state: dict) -> None:
    """Move a job row onto what Cloudinary just said about it."""
    job.status = state.get("status") or job.status
    job.error = state.get("error") or None
    if state.get("url"):
        job.result_url = state["url"]
    if job.status in ("done", "failed") and not job.finished_at:
        job.finished_at = datetime.utcnow()


def _timed_out(job) -> bool:
    if not job.created_at:
        return False
    age = (datetime.utcnow() - job.created_at).total_seconds()
    return age > config.JOB_TIMEOUT_SECONDS
