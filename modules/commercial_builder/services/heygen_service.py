"""HeyGen service — AI spokesperson scenes (spec section 8).

Handles listing available presenters (stock + Smart 1 talent roster + a
client's own uploaded avatar) and generating a talking-head clip for a
scene's narration line.
"""

import os
import time

import requests

from ..config import SMART1_TALENT_ROSTER

API_KEY = os.environ.get("HEYGEN_API_KEY")
BASE_URL = "https://api.heygen.com"


def is_live():
    return bool(API_KEY)


def _headers():
    return {"X-Api-Key": API_KEY, "Content-Type": "application/json"}


def list_presenters(client_avatar_id=None):
    """Returns the three-tier picker from spec section 8: Client Avatar,
    Saved Smart 1 Talent, HeyGen Stock Presenter."""
    presenters = {"client_avatar": None, "smart1_talent": SMART1_TALENT_ROSTER, "stock": []}

    if client_avatar_id:
        presenters["client_avatar"] = {"id": client_avatar_id, "name": "Client Avatar"}

    if not is_live():
        presenters["stock"] = [
            {"avatar_id": "mock_avatar_1", "name": "Alex (stock)", "preview_image_url": None},
            {"avatar_id": "mock_avatar_2", "name": "Jordan (stock)", "preview_image_url": None},
        ]
        return presenters

    try:
        r = requests.get(f"{BASE_URL}/v2/avatars", headers=_headers(), timeout=8)
        r.raise_for_status()
        avatars = r.json().get("data", {}).get("avatars", [])
        presenters["stock"] = [
            {"avatar_id": a.get("avatar_id"), "name": a.get("avatar_name"),
             "preview_image_url": a.get("preview_image_url")}
            for a in avatars[:24]
        ]
    except Exception:
        presenters["stock"] = []
    return presenters


def generate_spokesperson_clip(avatar_id, script_text, voice_id=None, background="transparent"):
    """
    Kicks off a HeyGen video generation for one scene's narration.
    Returns {"job_id": ..., "status": "processing"} — poll with check_status().
    """
    if not is_live():
        return {"job_id": f"mock_heygen_{int(time.time())}", "status": "completed",
                "video_url": None, "_mock": True}

    payload = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": avatar_id, "avatar_style": "normal"},
            "voice": {"type": "text", "input_text": script_text,
                      **({"voice_id": voice_id} if voice_id else {})},
            "background": {"type": "color", "value": "#00FF00"} if background == "greenscreen"
                          else {"type": "transparent"},
        }],
        "dimension": {"width": 1080, "height": 1920},
    }
    try:
        r = requests.post(f"{BASE_URL}/v2/video/generate", headers=_headers(), json=payload, timeout=15)
        r.raise_for_status()
        video_id = r.json().get("data", {}).get("video_id")
        return {"job_id": video_id, "status": "processing"}
    except Exception as e:
        return {"job_id": None, "status": "failed", "error": str(e)}


def check_status(job_id):
    if not is_live() or (job_id or "").startswith("mock_"):
        return {"job_id": job_id, "status": "completed", "video_url": None, "_mock": True}
    try:
        r = requests.get(f"{BASE_URL}/v1/video_status.get", headers=_headers(),
                          params={"video_id": job_id}, timeout=8)
        r.raise_for_status()
        data = r.json().get("data", {})
        return {"job_id": job_id, "status": data.get("status"), "video_url": data.get("video_url")}
    except Exception as e:
        return {"job_id": job_id, "status": "failed", "error": str(e)}
