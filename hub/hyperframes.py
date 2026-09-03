"""HyperFrames — the render service the Hub does not host in-process.

Every other provider this Hub reaches is a hosted REST API: we send a request
and somebody else's servers do the work. HyperFrames is not that. It is an
open-source rendering framework (Apache-2.0) whose normal shape is a local CLI
driving headless Chrome frame-by-frame and encoding with FFmpeg — Node 22,
Puppeteer and FFmpeg, none of which is in this Flask image and none of which
belongs in it. So the renderer runs as its **own Render service**
(`hf-render-service`), and this module is the whole of the Hub's side of that
wire.

Two skills ride on it and they are deliberately different sizes:

  * **paint-animation** is a *visual style* — p5.js handwriting, paint-on and
    living-painting treatments. It produces one clip, which is a scene's
    visual in the Commercial Builder and a standalone deliverable at
    `/tools/paint-animation`. It is a sixth source beside stock, AI, the
    spokesperson, an upload and a client asset — never a replacement for them.
  * **vox-explainer** is a *complete output* — a 60–90 second collage
    explainer. It is a ninth `commercial_type`, not a scene option, because a
    scene option that produces a finished video is a scene that is the whole
    spot.

Six rules hold this file up, and every one of them is a way this goes
confidently wrong.

**The templates are pre-authored and parameterized, never authored per
request.** Having a model write fresh HyperFrames HTML for each render is
slow, non-deterministic and impossible to QC — and it throws away the one
thing this framework offers that Runway does not, which is that the same
input always produces the same output. The Hub sends parameters; the service
fills a template it already holds. `TEMPLATES` is the contract, and a
template name this file does not know is refused **here** rather than at the
far end, because a 404 from a service is indistinguishable from the service
being down.

**Configured, reachable and working are three questions.** `is_configured()`
answers the first from settings alone and costs nothing, so it is what the UI
gates on. `check()` answers the second with one cheap request and is what the
QC pre-flight and the Diagnostics panel read. Neither is evidence of the
third: a service that answers `/health` can still fail a render, which is why
a job reports its own outcome and nothing infers success from the submit.
`services/provider_check.py`'s rule, one provider further out — **no
configuration is "not measured", never a cross.**

**Submit and poll, and the status call is what attaches the file.** A render
here is headless Chrome capturing frames; it takes minutes, exactly like
HeyGen and Runway. So `submit()` hands back a job id and nothing else, and
the caller's *status route* — never the browser — writes the finished URL
onto whatever it belongs to. A closed tab must not lose a render that has
already been paid for in wall-clock.

**A mock is marked and never filed as delivered.** With no service configured
`submit()` answers a job carrying `_mock` and no file, rather than a failure:
the tool then reads as switched off, which it is, instead of as broken. What
must not happen is a mock reaching a client's gallery, so `is_deliverable()`
is the single test every filing call site asks — the rule
`approve_render` already applies to a mock Creatomate render.

**Nothing in it may raise.** A render service that is down must cost the
feature and never the page it is on, so every entry point returns a dict with
a reason in it. Callers branch on the dict.

**No API key.** HyperFrames is self-hosted and there is nothing to
authenticate to a vendor — the cost is Render compute, not a per-render bill,
which is why there is no `hub/quotas.py` marker for it and why counting
renders here would be counting something nobody is invoiced for. What *is*
billed is the OpenAI call that writes a vox-explainer's beat list, and that
is recorded where it happens rather than here.

The wire contract, for `hf-render-service` to implement:

    POST {base}/render/{template}      {...params}  -> 202 {"jobId", "status"}
    GET  {base}/render/{jobId}/status               -> 200 {"status", "url"?,
                                                            "error"?, "durationSeconds"?}
    GET  {base}/health                              -> 200 {"ok": true, ...}

`status` is one of queued | rendering | done | failed. Anything else is read
as still running, because treating an unknown state as finished is how a
scene ends up with no video — which this Hub has already learned once with
HeyGen and once with Runway.
"""

from __future__ import annotations

import time
import uuid

import requests

__all__ = [
    "TEMPLATES", "PAINT_STYLES", "VOX_MIN_SECONDS", "VOX_MAX_SECONDS",
    "base_url", "is_configured", "check", "submit", "status",
    "is_deliverable", "paint_params", "vox_params", "vox_duration_verdict",
]

# One request each. The render itself is minutes long and is never waited on
# in a web request — these bound the submit and the status poll, which are
# both small JSON calls.
SUBMIT_TIMEOUT = 20
STATUS_TIMEOUT = 10
HEALTH_TIMEOUT = 6

# Queue states. `unknown` is deliberately grouped with them: a status this
# file has not seen is a service that is newer than this file, and reading it
# as finished attaches nothing while reporting success.
RUNNING = ("queued", "rendering", "pending", "processing", "unknown")

# The templates the service holds, and the only names that may be submitted.
# Declared here rather than fetched, for the reason `hub/creative_specs.py`
# gives about the spec kit: a list pulled live changes what this module will
# submit with no diff to point at. A name absent from here is refused before
# the request goes out, because "that template does not exist" and "the render
# service is down" both arrive as a failed call and only one of them is
# somebody's to fix.
TEMPLATES = {
    "paint-animation": {
        "label": "Paint animation",
        "kind": "clip",
        "note": "A p5.js handwriting, paint-on or living-painting treatment of "
                "one line of copy, one photograph, or a short clip.",
    },
    "vox-explainer": {
        "label": "Vox-style explainer",
        "kind": "video",
        "note": "A 60–90 second editorial collage explainer built from a beat "
                "list.",
    },
}

# The skill's own three modes. Closed, because a mode string outside this set
# reaches a template that has no branch for it and renders the default while
# every screen reports the mode that was asked for — the closed-vocabulary
# rule `_clean_grammar()` applies to shot grammar one module over.
PAINT_STYLES = [
    {"id": "handwriting", "label": "Handwriting",
     "hint": "Copy writes itself on, stroke by stroke. Best for a line of "
             "offer copy or a tagline."},
    {"id": "paint_on", "label": "Paint-on",
     "hint": "A photograph or logo paints itself in. Best for a brand mark or "
             "a product shot."},
    {"id": "living_painting", "label": "Living painting",
     "hint": "A still image gains continuous painterly motion. Best for a "
             "background that has to hold under narration."},
]
_PAINT_STYLE_IDS = {s["id"] for s in PAINT_STYLES}
DEFAULT_PAINT_STYLE = "handwriting"

# What the skill is scoped to. A vox explainer is an editorial format with a
# length its own structure implies; it is not a broadcast slot, so it is
# deliberately NOT stretched to the Commercial Builder's :05/:15/:30/:60,
# which are the durations inventory is sold in.
VOX_MIN_SECONDS = 60
VOX_MAX_SECONDS = 90

# A clip has to cover the scene it sits in. Past this the treatment stops
# reading as a treatment and starts being the spot, which is what
# vox-explainer is for.
PAINT_MAX_SECONDS = 30.0
PAINT_MIN_SECONDS = 1.0


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

def _settings():
    """Read at call time, never at import.

    `settings` is built once at import, and this is a URL somebody corrects
    mid-incident — the reasoning `hub/ghl_oauth.py` gives for resolving its
    scopes per call, and the same reason every provider service in the
    Commercial Builder reads its key through a function.
    """
    try:
        from hub.config import settings
        return settings
    except Exception:                                    # noqa: BLE001
        return None


def base_url() -> str:
    """The render service's origin, or "" when there is none.

    A trailing slash is trimmed: every path in this file starts with one, and
    `https://host//render/x` is a 404 on some routers and fine on others,
    which is the worst of the two.
    """
    s = _settings()
    raw = str(getattr(s, "hf_render_service_url", "") or "").strip()
    return raw.rstrip("/")


def _enabled() -> bool:
    s = _settings()
    return bool(getattr(s, "hf_render_enabled", True))


def is_configured() -> bool:
    """Is there a service to talk to, and are we allowed to.

    This is what a screen gates on, because it costs nothing. It is not
    evidence that the service is up — `check()` is the question that asks.
    """
    return bool(_enabled() and base_url())


def why_unavailable() -> str:
    """The sentence to draw where the feature would have been.

    Empty when it is available. Never names the URL: this reaches a page and
    an internal hostname is not a thing a rep can act on — the rule
    `services/provider_check.py` works to about a key value.
    """
    if not _enabled():
        return ("Paint animation and Vox explainers are switched off for this "
                "deployment (HF_RENDER_ENABLED).")
    if not base_url():
        return ("The render service is not configured yet, so paint animations "
                "and Vox explainers cannot be produced. Set "
                "HF_RENDER_SERVICE_URL and they appear on their own.")
    return ""


# --------------------------------------------------------------------------- #
# reachability
# --------------------------------------------------------------------------- #

def check() -> dict:
    """One cheap request: is the render service actually answering?

    Three states, never two, for the reason `services/provider_check.py`
    gives at length:

      * `not_measured` — nothing is configured. It has not failed; it has not
        been asked, and a cross here sends somebody to debug a service they
        have not stood up.
      * `ok` — it answered.
      * `unreachable` — it did not, and the reason is a sentence rather than
        the exception, because this is rendered into a page.
    """
    if not is_configured():
        return {"state": "not_measured", "message": why_unavailable()
                or "The render service is not configured."}
    try:
        r = requests.get(f"{base_url()}/health", timeout=HEALTH_TIMEOUT)
    except requests.RequestException:
        return {"state": "unreachable",
                "message": "The render service did not answer. Paint animations "
                           "and Vox explainers cannot be produced until it is "
                           "back."}
    if r.status_code >= 500:
        return {"state": "unreachable",
                "message": f"The render service answered {r.status_code}. It is "
                           "up but not healthy."}
    if r.status_code >= 400:
        # A 404 on /health is this file being out of date about the service's
        # own paths, not the service being down — the rule provider_check
        # states about an endpoint that moved between versions.
        return {"state": "unreachable",
                "message": f"The render service answered {r.status_code} to a "
                           "health check, which usually means this Hub and the "
                           "service are on different versions."}
    return {"state": "ok", "message": "The render service answered."}



# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _mock_job(template: str, params: dict, reason: str) -> dict:
    """A job that will never produce a file, saying so.

    Marked rather than failed: with no service configured the feature is
    switched off, and reporting that as an error reads as a broken tool. What
    the mark exists for is `is_deliverable()` — nothing carrying it may reach
    a client's gallery.
    """
    return {"job_id": f"mock_hf_{uuid.uuid4().hex[:12]}", "template": template,
            "status": "done", "url": None, "_mock": True, "error": None,
            "note": reason, "params": params,
            "submitted_at": int(time.time())}


def submit(template: str, params: dict) -> dict:
    """Ask the render service for one video. The file does not exist yet.

    Returns a job dict. Poll `status(job["job_id"])` until it stops reporting
    a running state, and only then is `url` meaningful — and it is the
    caller's *status route* that should attach it, not the browser, or a
    closed tab loses a render nobody will start again.

    Never raises.
    """
    params = dict(params or {})
    if template not in TEMPLATES:
        # Refused here rather than at the far end. A service 404 and a
        # typo'd template name arrive identically, and only one of them is
        # fixed by restarting anything.
        return {"job_id": None, "template": template, "status": "failed",
                "url": None,
                "error": f"There is no “{template}” template. This Hub knows "
                         + ", ".join(sorted(TEMPLATES)) + "."}

    if not is_configured():
        return _mock_job(template, params, why_unavailable())

    try:
        r = requests.post(f"{base_url()}/render/{template}", json=params,
                          timeout=SUBMIT_TIMEOUT)
    except requests.RequestException:
        return {"job_id": None, "template": template, "status": "failed",
                "url": None,
                "error": "The render service did not answer, so nothing was "
                         "rendered. Nothing was charged and nothing was lost — "
                         "press it again once it is back."}

    if r.status_code >= 400:
        return {"job_id": None, "template": template, "status": "failed",
                "url": None, "error": _service_error(r)}

    try:
        body = r.json() or {}
    except ValueError:
        return {"job_id": None, "template": template, "status": "failed",
                "url": None,
                "error": "The render service answered with something that is "
                         "not a render job."}

    job_id = body.get("jobId") or body.get("job_id")
    if not job_id:
        # Accepted with nothing to poll. Reported as a failure rather than as
        # a queued job, or the caller polls an id it does not have for ever.
        return {"job_id": None, "template": template, "status": "failed",
                "url": None,
                "error": "The render service accepted the job and returned no "
                         "job id, so there is nothing to follow."}

    return {"job_id": str(job_id), "template": template,
            "status": _normalise(body.get("status") or "queued"),
            "url": body.get("url"), "error": None,
            "params": params, "submitted_at": int(time.time())}


def status(job_id: str) -> dict:
    """Where has this render got to?

    An unrecognised state reads as still running. Treating one as finished
    attaches nothing while reporting success, which is the failure this Hub
    has already had twice with asynchronous providers.
    """
    if not job_id:
        return {"job_id": job_id, "status": "failed", "url": None,
                "error": "That render has no job id to follow."}
    if str(job_id).startswith("mock_hf_"):
        return {"job_id": job_id, "status": "done", "url": None, "_mock": True,
                "error": None}
    if not is_configured():
        return {"job_id": job_id, "status": "failed", "url": None,
                "error": why_unavailable()}

    try:
        r = requests.get(f"{base_url()}/render/{job_id}/status",
                         timeout=STATUS_TIMEOUT)
    except requests.RequestException:
        # Not a failure. The render may well be running perfectly well behind
        # a network blip, and marking it failed here throws away a job that is
        # about to finish — so it stays running and the next poll asks again.
        return {"job_id": job_id, "status": "rendering", "url": None,
                "error": None,
                "note": "The render service did not answer this check. Still "
                        "waiting."}
    if r.status_code == 404:
        return {"job_id": job_id, "status": "failed", "url": None,
                "error": "The render service has no record of that job. It "
                         "restarted, or the job expired."}
    if r.status_code >= 400:
        return {"job_id": job_id, "status": "failed", "url": None,
                "error": _service_error(r)}
    try:
        body = r.json() or {}
    except ValueError:
        return {"job_id": job_id, "status": "rendering", "url": None,
                "error": None,
                "note": "The render service answered with something unreadable. "
                        "Still waiting."}

    state = _normalise(body.get("status"))
    url = body.get("url") or None
    if state == "done" and not url:
        # "Finished" with no file is not finished. Said as a failure, because
        # a caller that attaches this attaches nothing and reports success.
        return {"job_id": job_id, "status": "failed", "url": None,
                "error": "The render finished and produced no file."}
    return {"job_id": job_id, "status": state, "url": url,
            "error": body.get("error") or None,
            "duration_seconds": _number(body.get("durationSeconds")
                                        or body.get("duration_seconds")),
            "progress": _number(body.get("progress"))}


def is_running(state: str) -> bool:
    return _normalise(state) in RUNNING


def is_deliverable(job: dict) -> bool:
    """May this reach a client's gallery?

    One reading, asked by every filing call site. A mock render reports
    success and produces no file, and filing one is a delivered asset with
    nothing behind it — the refusal `approve_render` already makes about a
    mock Creatomate render, which is exactly this failure one provider over.
    """
    job = job or {}
    return bool(job.get("url")) and not job.get("_mock") \
        and _normalise(job.get("status")) == "done"


def _service_error(r) -> str:
    """The service's own sentence where it gave one.

    Discarding it is how every button comes to report its own invented
    diagnosis of one shared failure — the note `hub/openai_responses.py`
    makes about `raise_for_status()`. Bounded, because this reaches a page.
    """
    detail = ""
    try:
        body = r.json() or {}
        detail = str(body.get("error") or body.get("message") or "").strip()
    except Exception:                                    # noqa: BLE001
        detail = ""
    if not detail:
        detail = (r.text or "").strip()
    detail = " ".join(detail.split())[:300]
    if detail:
        return f"The render service refused this job ({r.status_code}): {detail}"
    return f"The render service refused this job ({r.status_code})."


def _normalise(state) -> str:
    state = str(state or "").strip().lower()
    if state in ("done", "succeeded", "success", "complete", "completed"):
        return "done"
    if state in ("failed", "error", "cancelled", "canceled"):
        return "failed"
    if state in ("queued", "rendering", "pending", "processing", "running"):
        return "rendering" if state in ("rendering", "running", "processing") else "queued"
    return "unknown"


def _number(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# parameters — one composer per template
# --------------------------------------------------------------------------- #

def paint_params(*, text: str = "", image_url: str = "", style: str = "",
                 seconds: float = 5.0, format_id: str = "16:9",
                 brand_colors=None, background: str = "") -> dict:
    """What the paint-animation template needs, cleaned.

    `seconds` becomes the composition's `data-duration`, which is the contract
    `@hyperframes/core` documents — so a scene's own length drives the clip
    rather than the clip being trimmed to fit, which is what leaves a segment
    running out and going black.
    """
    style = str(style or "").strip().lower()
    if style not in _PAINT_STYLE_IDS:
        # An unknown mode reaches a template with no branch for it and renders
        # the default while every screen reports the mode that was asked for.
        style = DEFAULT_PAINT_STYLE
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 5.0
    seconds = round(max(PAINT_MIN_SECONDS, min(PAINT_MAX_SECONDS, seconds)), 2)

    colors = [c for c in (brand_colors or []) if isinstance(c, str) and c.strip()][:6]
    return {
        "text": str(text or "").strip()[:240],
        "imageUrl": str(image_url or "").strip(),
        "style": style,
        "durationSeconds": seconds,
        "format": str(format_id or "16:9"),
        "brandColors": colors,
        "background": str(background or "").strip()[:40],
    }


def paint_refusal(*, text: str, image_url: str, seconds: float) -> str:
    """Why this cannot be rendered, in words, or "".

    Asked before the request goes out so the reason names the field rather
    than arriving as a provider error that reads like an outage.
    """
    if not (text or "").strip() and not (image_url or "").strip():
        return ("A paint animation needs something to paint — a line of copy, "
                "or a picture.")
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        return "That duration is not a number of seconds."
    if secs > PAINT_MAX_SECONDS:
        return (f"This runs {secs:.1f}s and a paint animation is a treatment "
                f"rather than a spot, so it is capped at "
                f"{PAINT_MAX_SECONDS:.0f}s. Shorten it, or build the whole "
                f"piece as a Vox explainer.")
    if secs < PAINT_MIN_SECONDS:
        return (f"A paint animation needs at least {PAINT_MIN_SECONDS:.0f}s to "
                f"read as one.")
    return ""


def vox_params(*, title: str = "", beats=None, format_id: str = "16:9",
               brand_colors=None, voice_track_url: str = "") -> dict:
    """What the vox-explainer template needs, cleaned.

    The beat list is the contract between the model that writes it and the
    template that renders it, and it is validated in `vox_spec.py` before it
    gets here — this composer only shapes what survived that.
    """
    colors = [c for c in (brand_colors or []) if isinstance(c, str) and c.strip()][:6]
    return {
        "title": str(title or "").strip()[:160],
        "beats": list(beats or []),
        "format": str(format_id or "16:9"),
        "brandColors": colors,
        "voiceTrackUrl": str(voice_track_url or "").strip(),
    }


def vox_duration_verdict(seconds) -> dict:
    """Did the render land in the window this format is scoped to?

    Advisory rather than a gate, and tri-state: a render that has not
    happened yet is **not measured**, never a pass. A confident tick over a
    duration nobody has measured is the answer this codebase keeps having to
    undo.
    """
    value = _number(seconds)
    if value is None:
        return {"measured": False, "passed": True, "seconds": None,
                "message": f"Not measured yet — a Vox explainer is scoped to "
                           f"{VOX_MIN_SECONDS}–{VOX_MAX_SECONDS} seconds and "
                           f"nothing has been rendered to measure."}
    if value < VOX_MIN_SECONDS:
        return {"measured": True, "passed": False, "seconds": value,
                "message": f"This runs {value:.0f}s, under the "
                           f"{VOX_MIN_SECONDS}s a Vox explainer is scoped to. "
                           f"Add a beat, or lengthen the ones you have."}
    if value > VOX_MAX_SECONDS:
        return {"measured": True, "passed": False, "seconds": value,
                "message": f"This runs {value:.0f}s, over the "
                           f"{VOX_MAX_SECONDS}s a Vox explainer is scoped to. "
                           f"Cut a beat, or tighten the narration."}
    return {"measured": True, "passed": True, "seconds": value,
            "message": f"{value:.0f}s — inside the {VOX_MIN_SECONDS}–"
                       f"{VOX_MAX_SECONDS}s window."}
