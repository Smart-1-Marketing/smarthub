"""HeyGen service — AI spokesperson scenes (spec section 8).

Lists the presenters a rep may pick from (stock, the Smart 1 talent roster, a
client's own avatar) and generates a talking-head clip for one scene's
narration line.

Three things here are not obvious and each was a live defect:

  * **A clip is not ready when this returns.** HeyGen renders asynchronously
    and takes minutes, so `generate_spokesperson_clip()` hands back a job id
    and nothing else. Somebody has to poll. `routes/heygen.py` owns that and
    writes the finished URL onto the scene; this module only reports status.
  * **The frame is the project's, not HeyGen's default.** The dimension used
    to be hard-coded to 1080x1920, so every presenter came back vertical —
    including on the 16:9 CTV spots this module mostly builds.
  * **`{"type": "transparent"}` is not a HeyGen background.** The v2 API takes
    `color`, `image` or `video`. Transparency was the *default* argument, so
    the default path was the one that failed live. A presenter that has to sit
    over footage now gets a chroma matte and is keyed at composition time
    (see `creatomate_service`); one that fills the frame gets a solid colour.
"""

import os
import time

import requests

from ..config import (SMART1_TALENT_ROSTER, OUTPUT_FORMATS, CHROMA_KEY_COLOR,
                      SOLID_BACKDROP_COLOR, talent_avatar_overrides)

BASE_URL = "https://api.heygen.com"

_FORMAT_DIMS = {f["id"]: (f["width"], f["height"]) for f in OUTPUT_FORMATS}

# HeyGen's own status vocabulary, mapped onto the three states the rest of the
# module reasons about. "waiting" and "pending" are queue states, not finished
# ones, and treating either as done is how a scene ends up with no video.
_STATUS_MAP = {
    "waiting": "processing", "pending": "processing", "processing": "processing",
    "completed": "completed", "success": "completed",
    "failed": "failed", "error": "failed",
}


def _api_key():
    """Read at call time, not import time.

    The Hub's settings object is the source of truth and accepts every
    spelling this deployment has used; the environ fallback keeps the module
    working when it is run standalone, outside the Hub.
    """
    try:
        from hub.config import settings
        if settings.heygen_key:
            return settings.heygen_key
    except Exception:  # noqa: BLE001 — standalone, or settings failed to build
        pass
    for name in ("HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def is_live():
    return bool(_api_key())


def _headers():
    return {"X-Api-Key": _api_key(), "Content-Type": "application/json"}


def list_presenters(client_avatar_id=None):
    """The three-tier picker from spec section 8: Client Avatar, Saved Smart 1
    Talent, HeyGen Stock Presenter.

    Every entry carries `available` and `avatar_id`. The talent roster ships
    with no HeyGen ids against it, and a tile that looks pickable and then
    refuses is worse than one that says it is not ready — so the flag is
    computed here rather than left for the UI to infer.
    """
    overrides = talent_avatar_overrides()
    talent = []
    for person in SMART1_TALENT_ROSTER:
        avatar_id = overrides.get(person["id"]) or person.get("heygen_avatar_id")
        entry = dict(person)
        entry["heygen_avatar_id"] = avatar_id
        entry["avatar_id"] = avatar_id
        entry["available"] = bool(avatar_id)
        if not avatar_id:
            entry["unavailable_reason"] = (
                "Not linked to a HeyGen avatar yet — create the avatar in the "
                "HeyGen console and add its id to SMART1_TALENT_AVATARS.")
        talent.append(entry)

    presenters = {"client_avatar": None, "smart1_talent": talent, "stock": []}

    if client_avatar_id:
        presenters["client_avatar"] = {"id": client_avatar_id, "avatar_id": client_avatar_id,
                                       "name": "Client Avatar", "available": True}

    if not is_live():
        presenters["stock"] = [
            {"avatar_id": "mock_avatar_1", "name": "Alex (stock)",
             "preview_image_url": None, "available": True},
            {"avatar_id": "mock_avatar_2", "name": "Jordan (stock)",
             "preview_image_url": None, "available": True},
        ]
        return presenters

    try:
        r = requests.get(f"{BASE_URL}/v2/avatars", headers=_headers(), timeout=8)
        r.raise_for_status()
        avatars = r.json().get("data", {}).get("avatars", [])
        presenters["stock"] = [
            {"avatar_id": a.get("avatar_id"), "name": a.get("avatar_name"),
             "preview_image_url": a.get("preview_image_url"),
             "available": bool(a.get("avatar_id"))}
            for a in avatars[:24]
        ]
    except Exception:
        presenters["stock"] = []
    return presenters


def clip_dimensions(format_id):
    """The frame a spokesperson clip must be generated at.

    A clip is rendered once and then composited into every format the project
    fans out to, so it is generated at the project's *primary* format. Getting
    this from the project rather than a constant is the whole point: a 16:9
    CTV spot with a 9:16 presenter in it is a re-shoot, not a crop.
    """
    return _FORMAT_DIMS.get(format_id, _FORMAT_DIMS["16:9"])


def background_for(over_footage):
    """HeyGen v2 accepts `color`, `image` and `video` — there is no
    `transparent`. A presenter that has to sit over scene footage gets a
    chroma matte to be keyed out downstream; one that fills the frame gets a
    solid backdrop, because there is nothing behind it to reveal."""
    if over_footage:
        return {"type": "color", "value": CHROMA_KEY_COLOR}, True
    return {"type": "color", "value": SOLID_BACKDROP_COLOR}, False


def generate_spokesperson_clip(avatar_id, script_text, voice_id=None,
                               format_id="16:9", over_footage=False):
    """
    Kicks off a HeyGen generation for one scene's narration.

    Returns a job dict — `{"job_id": ..., "status": "processing", ...}`. The
    clip does NOT exist yet; poll `check_status()` until it reports
    "completed" and only then is `video_url` meaningful.
    """
    width, height = clip_dimensions(format_id)
    background, chroma_key = background_for(over_footage)
    base = {"width": width, "height": height, "chroma_key": chroma_key,
            "background_color": background["value"], "format_id": format_id}

    if not (script_text or "").strip():
        # HeyGen rejects an empty input_text, and the failure comes back as a
        # provider error that reads like an outage. Say what is actually wrong.
        return dict(base, job_id=None, status="failed",
                    error="This scene has no narration for the presenter to read.")

    if not is_live():
        # Mock mode produces no video. It reports that plainly rather than
        # claiming a completed clip with no URL, which QC cannot tell apart
        # from a live generation that silently failed.
        return dict(base, job_id=f"mock_heygen_{int(time.time())}", status="completed",
                    video_url=None, _mock=True)

    payload = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": avatar_id, "avatar_style": "normal"},
            "voice": {"type": "text", "input_text": script_text,
                      **({"voice_id": voice_id} if voice_id else {})},
            "background": background,
        }],
        "dimension": {"width": width, "height": height},
    }
    try:
        r = requests.post(f"{BASE_URL}/v2/video/generate", headers=_headers(),
                          json=payload, timeout=15)
        r.raise_for_status()
        video_id = r.json().get("data", {}).get("video_id")
        if not video_id:
            # Accepted and unusable. Still recorded: HeyGen took the request,
            # and a row that is only written on success reports a low number
            # in exactly the month somebody is asking why the bill is high.
            _meter(ok=False, detail="accepted with no video id")
            return dict(base, job_id=None, status="failed",
                        error="HeyGen accepted the request but returned no video id.")
        _meter(detail=f"{avatar_id or 'avatar'} · {width}x{height}")
        return dict(base, job_id=video_id, status="processing")
    except Exception as e:  # noqa: BLE001 — provider errors are reported, not raised
        # A refused call spent nothing and is excluded from every billable
        # total, but the row stays — a wall of them is what a spent allowance
        # looks like from this side.
        _meter(ok=False, detail=str(e)[:80])
        return dict(base, job_id=None, status="failed", error=str(e))


def _meter(*, ok=True, detail=""):
    """One clip, counted. Never raises: an indicator must not cost the render.

    Counted per clip because that is how HeyGen bills — unlike Runway, whose
    unit is the second.
    """
    try:
        from hub import quotas as _q
        _q.record_clip(module="commercial_builder", detail=detail, ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def check_status(job_id):
    """Polls one generation. `video_url` is only ever set on "completed"."""
    if not job_id:
        return {"job_id": None, "status": "failed", "video_url": None,
                "error": "No HeyGen job id on this scene."}
    if not is_live() or str(job_id).startswith("mock_"):
        return {"job_id": job_id, "status": "completed", "video_url": None, "_mock": True}
    try:
        r = requests.get(f"{BASE_URL}/v1/video_status.get", headers=_headers(),
                         params={"video_id": job_id}, timeout=8)
        r.raise_for_status()
        data = r.json().get("data", {})
        status = _STATUS_MAP.get((data.get("status") or "").lower(), "processing")
        result = {"job_id": job_id, "status": status,
                  "video_url": data.get("video_url") if status == "completed" else None,
                  "duration": data.get("duration")}
        if status == "failed":
            error = data.get("error")
            result["error"] = (error.get("message") if isinstance(error, dict) else error) \
                or "HeyGen reported the generation failed."
        elif status == "completed" and not result["video_url"]:
            # Completed with nothing to show is a failure, however it reads on
            # the wire — an empty asset would render as a blank segment.
            result["status"] = "failed"
            result["error"] = "HeyGen reported the clip complete but returned no video URL."
        return result
    except Exception as e:  # noqa: BLE001
        return {"job_id": job_id, "status": "failed", "video_url": None, "error": str(e)}
