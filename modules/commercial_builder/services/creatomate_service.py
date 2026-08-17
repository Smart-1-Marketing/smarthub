"""Creatomate service — final timeline assembly and rendering (spec section 12).

This is the only service that touches final video assembly. Everything
upstream (storyboard, voice, music, HeyGen clips) hands this service a
`CommercialProject` with fully-resolved scene asset URLs; this module's job
is purely: build the Creatomate render `source` JSON with exact timing, and
submit/poll the render.

Note: the exact Creatomate Render API JSON shape can shift between API
versions — this builds a source document following Creatomate's documented
"elements with track/time/duration" composition model. If your Creatomate
account's schema differs, this is the one function (`build_source`) to
adjust; nothing else in the module needs to know about it.
"""

import os
import re
import time

import requests

from ..config import OUTPUT_FORMATS, MUSIC_LEVELS, QR_CODE_RULES, LOGO_PERSISTENCE_RULES

API_KEY = os.environ.get("CREATOMATE_API_KEY")
BASE_URL = "https://api.creatomate.com/v1"

_FORMAT_DIMS = {f["id"]: (f["width"], f["height"]) for f in OUTPUT_FORMATS}

# corner id -> (x anchor%, y anchor%) for overlay placement, with a small
# inset so nothing sits flush against the frame edge (text-safe area).
_CORNER_ANCHORS = {
    "top-left": ("6%", "6%"), "top-right": ("94%", "6%"),
    "bottom-left": ("6%", "94%"), "bottom-right": ("94%", "94%"),
}


def is_live():
    return bool(API_KEY)


def _headers():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def build_source(project_dict, scenes, format_id, voice_track_url=None, music_track_url=None):
    """
    Builds a Creatomate render `source` document.

    project_dict: CommercialProject.to_dict()
    scenes: list of Scene.to_dict(), already ordered with resolved asset_url
    """
    width, height = _FORMAT_DIMS.get(format_id, (1920, 1080))
    music = project_dict.get("music") or {}
    music_level = music.get("level", "Medium")
    music_low_db, music_ducked_db = MUSIC_LEVELS.get(music_level, MUSIC_LEVELS["Medium"])

    cta = project_dict.get("cta") or {}
    platform = project_dict.get("platform", "both")
    length_seconds = project_dict.get("length_seconds", 30)

    video_elements = []
    for scene in scenes:
        el_type = "text" if scene.get("is_cta") and not scene.get("asset_url") else \
                  ("video" if (scene.get("asset_url") or "").lower().endswith((".mp4", ".mov", ".webm"))
                   else "image")
        element = {
            "id": f"scene_{scene['id']}",
            "track": 1,
            "time": scene["start"],
            "duration": round(scene["end"] - scene["start"], 2),
            "type": el_type,
        }
        if el_type in ("video", "image") and scene.get("asset_url"):
            element["source"] = scene["asset_url"]
            element["fit"] = "cover"
        if scene.get("is_cta"):
            element["overlay"] = {
                "type": "composition",
                "elements": _cta_overlay_elements(cta, project_dict, scene, platform),
            }
        video_elements.append(element)

    # Persistent/recurring logo bug (spec: CTV best practices) — a small
    # corner mark that runs the WHOLE commercial, not just the end card, so
    # a viewer who looks away mid-spot still catches the brand. Lives on its
    # own track above the scene footage; skipped on :05s (already full-logo
    # the entire time) and whenever the CTA builder has it turned off.
    client_logo = (cta.get("client") or {}).get("logo_url")
    if cta.get("logo_persistent") and client_logo and length_seconds != 5:
        corner = cta.get("logo_corner", LOGO_PERSISTENCE_RULES["default_corner"])
        x, y = _CORNER_ANCHORS.get(corner, _CORNER_ANCHORS["top-left"])
        video_elements.append({
            "id": "logo_bug", "track": 4, "time": 0, "duration": float(length_seconds),
            "type": "image", "source": client_logo,
            "width": f"{LOGO_PERSISTENCE_RULES['size_pct']}%", "x": x, "y": y,
            "x_anchor": "50%", "y_anchor": "50%",
        })

    audio_elements = []
    if voice_track_url:
        audio_elements.append({
            "id": "voice", "track": 2, "time": 0, "type": "audio",
            "source": voice_track_url, "volume": "100%",
        })
    if music_track_url:
        # Ducking: quiet under narration, up in the gaps. Real per-scene VO
        # timing drives the automation keyframes so the voice always dominates.
        keyframes = _music_ducking_keyframes(scenes, music_low_db, music_ducked_db,
                                              project_dict.get("length_seconds", 30))
        audio_elements.append({
            "id": "music", "track": 3, "time": 0, "type": "audio",
            "source": music_track_url, "volume": keyframes,
        })

    return {
        "output_format": "mp4",
        "width": width,
        "height": height,
        "elements": video_elements + audio_elements,
    }


def _cta_overlay_elements(cta, project_dict, scene, platform):
    client = cta.get("client") or {}
    # "Living room" legibility for CTV — bigger, bolder end-card text than a
    # spot that only ever plays on a phone/laptop screen.
    font_size = "8vmin" if platform in ("ctv", "both") else "6vmin"
    font_weight = "800" if platform in ("ctv", "both") else "700"

    elements = [
        {"type": "text", "text": project_dict.get("title") or client.get("name", ""), "y": "18%",
         "font_size": font_size, "font_weight": font_weight},
        {"type": "text", "text": cta.get("offer", ""), "y": "40%",
         "font_size": font_size, "font_weight": font_weight},
        {"type": "text", "text": cta.get("headline", "Schedule Today"), "y": "58%",
         "font_size": font_size, "font_weight": font_weight},
        # Clean, memorable domain rather than a long landing-page path
        # ("BrandName.com", not "brandname.com/promo/ac-tune-up-2026").
        {"type": "text", "text": " | ".join(filter(None, [_clean_domain(cta.get("website")), cta.get("phone")])),
         "y": "74%", "font_size": font_size, "font_weight": font_weight},
    ]

    qr_url = cta.get("qr_image_url") or cta.get("qr_data_url")
    if cta.get("qr_enabled") and qr_url:
        corner = cta.get("qr_corner", QR_CODE_RULES["default_corner"])
        x, y = _CORNER_ANCHORS.get(corner, _CORNER_ANCHORS["bottom-right"])
        elements.append({
            "id": "qr_code", "type": "image", "source": qr_url,
            # High-contrast, >=15% of the frame's shorter dimension, held for
            # the whole CTA/end-card scene (already enforced >=8-10s by QC).
            "width": f"{QR_CODE_RULES['min_screen_pct']}%", "x": x, "y": y,
            "x_anchor": "50%", "y_anchor": "50%", "background_color": "#ffffff", "background_padding": "4%",
        })
    return elements


def _clean_domain(url):
    """Strips scheme/path/query down to a clean, easy-to-remember domain for
    on-screen display — 'BrandName.com', not a long landing-page path."""
    if not url:
        return ""
    domain = re.sub(r"^https?://", "", url).split("/")[0]
    return domain


def _music_ducking_keyframes(scenes, low_db, ducked_db, total_duration):
    """Builds a simple db-level keyframe list: ducked while any scene has VO,
    back to the configured 'low' level in silent gaps."""
    keyframes = []
    for scene in scenes:
        has_vo = bool((scene.get("narration") or "").strip())
        keyframes.append({"time": scene["start"], "value": f"{ducked_db if has_vo else low_db}dB"})
    keyframes.append({"time": total_duration, "value": f"{low_db}dB"})
    return keyframes


def submit_render(source):
    if not is_live():
        return {"id": f"mock_render_{int(time.time())}", "status": "succeeded",
                "url": None, "_mock": True}
    try:
        r = requests.post(f"{BASE_URL}/renders", headers=_headers(), json={"source": source}, timeout=20)
        r.raise_for_status()
        data = r.json()
        render = data[0] if isinstance(data, list) else data
        return {"id": render.get("id"), "status": render.get("status"), "url": render.get("url")}
    except Exception as e:
        return {"id": None, "status": "failed", "error": str(e)}


def check_render(render_id):
    if not is_live() or (render_id or "").startswith("mock_"):
        return {"id": render_id, "status": "succeeded", "url": None, "_mock": True}
    try:
        r = requests.get(f"{BASE_URL}/renders/{render_id}", headers=_headers(), timeout=8)
        r.raise_for_status()
        data = r.json()
        return {"id": data.get("id"), "status": data.get("status"), "url": data.get("url")}
    except Exception as e:
        return {"id": render_id, "status": "failed", "error": str(e)}
