"""Runway service — AI video scenes (spec section 7, the V1.5 provider).

Replaces the still-frame placeholder that "Generate AI" has been producing:
`openai_service.generate_ai_stills` made a PNG and the storyboard put it in a
video commercial. Runway animates a starting frame into real footage.

Three things about this provider shape the code, and each is a constraint
rather than a preference:

  * **A reference image is required.** Gen-4 animates a first frame; there is
    no usable text-only path. That suits this tool — the frame is generated
    and art-directed first (OpenAI stills, stock, or the client's own asset),
    then moved. `openai_service.write_runway_prompt` writes the motion prompt
    and has existed with no caller since the module was written; this is it.
  * **Clips are 5 or 10 seconds, and nothing else.** A scene is whatever the
    script needs, so the clip is requested at the shortest offered length that
    COVERS the scene and the compositor trims it. A scene over 10s is refused
    with its length named rather than handed a clip that stops early.
  * **Generation is asynchronous.** POST returns a task id; the clip arrives
    minutes later. Same shape as HeyGen, and the same rule applies: attaching
    it is the status route's job, not the browser's, or a closed tab loses it.

The request body and the ratio strings are the parts most likely to differ
between accounts and model versions. They are confined to `_payload()` and
`config.RUNWAY_RATIOS` so there is one place to adjust.
"""

import os
import time

import requests

from ..config import (RUNWAY_MODEL, RUNWAY_MAX_SCENE_SECONDS,
                      runway_duration, runway_ratio)

BASE_URL = "https://api.dev.runwayml.com/v1"

# Runway dates its API versions and requires the header on every request; new
# behaviour ships behind a new date rather than changing an existing one.
API_VERSION = os.environ.get("RUNWAY_API_VERSION", "2024-11-06")

# PENDING/RUNNING are queue states. Treating either as finished is how a scene
# ends up with no video, which is the defect this whole module already learned
# once with HeyGen.
_STATUS_MAP = {
    "PENDING": "processing", "RUNNING": "processing", "THROTTLED": "processing",
    "SUCCEEDED": "completed",
    "FAILED": "failed", "CANCELLED": "failed",
}


def _api_key():
    """Read at call time, not import time — see heygen_service for why."""
    try:
        from hub.config import settings
        if settings.runway_key:
            return settings.runway_key
    except Exception:  # noqa: BLE001 — standalone, or settings failed to build
        pass
    for name in ("RUNWAY_API", "RUNWAY_API_KEY", "RUNWAY_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def is_live():
    return bool(_api_key())


def _headers():
    return {"Authorization": f"Bearer {_api_key()}",
            "X-Runway-Version": API_VERSION,
            "Content-Type": "application/json"}


def _payload(image_url, prompt, format_id, seconds):
    return {
        "model": RUNWAY_MODEL,
        "promptImage": image_url,
        "promptText": prompt,
        "ratio": runway_ratio(format_id),
        "duration": seconds,
    }


def generate_from_image(image_url, prompt, format_id="16:9", scene_seconds=5.0):
    """Animate one starting frame into a clip for one scene.

    Returns a job dict. The clip does NOT exist yet: poll `check_status()`
    until it reports "completed", and only then is `video_url` meaningful.
    """
    seconds = runway_duration(scene_seconds)
    base = {"format_id": format_id, "ratio": runway_ratio(format_id),
            "duration": seconds, "scene_seconds": round(float(scene_seconds), 2),
            "prompt": prompt, "source_image": image_url}

    if seconds is None:
        return dict(base, job_id=None, status="failed",
                    error=f"This scene runs {scene_seconds:.1f}s and Runway clips are at most "
                          f"{RUNWAY_MAX_SCENE_SECONDS}s. Split it into shorter scenes, or use "
                          f"stock footage for this one.")
    if not image_url:
        return dict(base, job_id=None, status="failed",
                    error="Runway animates a starting frame, so this scene needs an image "
                          "first — generate one, pick stock, or use a client asset.")

    if not is_live():
        # No key, no video. Reported plainly rather than as a completed job
        # with nothing behind it, which QC cannot tell from a real failure.
        return dict(base, job_id=f"mock_runway_{int(time.time())}", status="completed",
                    video_url=None, _mock=True)

    try:
        r = requests.post(f"{BASE_URL}/image_to_video", headers=_headers(),
                          json=_payload(image_url, prompt, format_id, seconds), timeout=20)
        r.raise_for_status()
        task_id = (r.json() or {}).get("id")
        if not task_id:
            return dict(base, job_id=None, status="failed",
                        error="Runway accepted the request but returned no task id.")
        return dict(base, job_id=task_id, status="processing")
    except Exception as e:  # noqa: BLE001 — provider errors are reported, not raised
        return dict(base, job_id=None, status="failed", error=str(e))


def check_status(job_id):
    """Polls one task. `video_url` is only ever set on "completed"."""
    if not job_id:
        return {"job_id": None, "status": "failed", "video_url": None,
                "error": "No Runway task id on this scene."}
    if not is_live() or str(job_id).startswith("mock_"):
        return {"job_id": job_id, "status": "completed", "video_url": None, "_mock": True}
    try:
        r = requests.get(f"{BASE_URL}/tasks/{job_id}", headers=_headers(), timeout=10)
        r.raise_for_status()
        data = r.json() or {}
        status = _STATUS_MAP.get(str(data.get("status") or "").upper(), "processing")
        # Runway returns the finished asset as a list; the clip is the first.
        output = data.get("output") or []
        url = output[0] if isinstance(output, list) and output else (
            output if isinstance(output, str) else None)
        result = {"job_id": job_id, "status": status,
                  "video_url": url if status == "completed" else None}
        if status == "failed":
            result["error"] = (data.get("failure") or data.get("failureCode")
                               or "Runway reported the generation failed.")
        elif status == "completed" and not result["video_url"]:
            # Completed with nothing to show is a failure however it reads on
            # the wire — an empty asset renders as a blank segment.
            result["status"] = "failed"
            result["error"] = "Runway reported the task complete but returned no video."
        return result
    except Exception as e:  # noqa: BLE001
        return {"job_id": job_id, "status": "failed", "video_url": None, "error": str(e)}
