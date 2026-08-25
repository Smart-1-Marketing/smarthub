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

from ..config import (OUTPUT_FORMATS, MUSIC_LEVELS, QR_CODE_RULES, LOGO_PERSISTENCE_RULES,
                      CHROMA_KEY_COLOR)

API_KEY = os.environ.get("CREATOMATE_API_KEY")
BASE_URL = "https://api.creatomate.com/v1"

_FORMAT_DIMS = {f["id"]: (f["width"], f["height"]) for f in OUTPUT_FORMATS}

# Track numbers are layers, and elements sharing a track play SEQUENTIALLY
# rather than stacking — which is why the scenes all share track 1 and
# everything that has to sit on top of them gets a track of its own. Named
# here because the ordering matters and a bare integer three functions down
# does not say why the logo has to outrank the presenter.
TRACK_SCENES = 1
TRACK_VOICE = 2
TRACK_MUSIC = 3
TRACK_PRESENTER = 4     # keyed spokesperson, above the footage it is keyed over
TRACK_LOGO_BUG = 5      # the brand mark stays on top of everything

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
        el_type = _element_type(scene)
        element = {
            "id": f"scene_{scene['id']}",
            "track": TRACK_SCENES,
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

        # A spokesperson generated to stand over this scene's footage was
        # rendered against a chroma matte for exactly this moment. Without
        # this element the clip was either dropped full-frame over the footage
        # it was supposed to share, or — before it was attached at all — not
        # present in the render and silently absent from the finished spot.
        presenter = _presenter_element(scene)
        if presenter:
            video_elements.append(presenter)

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
            "id": "logo_bug", "track": TRACK_LOGO_BUG, "time": 0, "duration": float(length_seconds),
            "type": "image", "source": client_logo,
            "width": f"{LOGO_PERSISTENCE_RULES['size_pct']}%", "x": x, "y": y,
            "x_anchor": "50%", "y_anchor": "50%",
        })

    audio_elements = []
    if voice_track_url:
        audio_elements.append({
            "id": "voice", "track": TRACK_VOICE, "time": 0, "type": "audio",
            "source": voice_track_url, "volume": "100%",
        })
    if music_track_url:
        # Ducking: quiet under narration, up in the gaps. Real per-scene VO
        # timing drives the automation keyframes so the voice always dominates.
        keyframes = _music_ducking_keyframes(scenes, music_low_db, music_ducked_db,
                                              project_dict.get("length_seconds", 30))
        audio_elements.append({
            "id": "music", "track": TRACK_MUSIC, "time": 0, "type": "audio",
            "source": music_track_url, "volume": keyframes,
        })

    return {
        "output_format": "mp4",
        "width": width,
        "height": height,
        "elements": video_elements + audio_elements,
    }


_VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v")

# Asset types that are always video, whatever the URL looks like. A delivery
# URL does not have to carry a file extension — Cloudinary's often does not —
# and guessing from the suffix alone typed a spokesperson clip as an image,
# which renders as a still frame or as nothing at all.
#
# "ai_generated" is deliberately NOT in this set. The Generate AI button
# produces OpenAI *stills* today (Runway is V1.5 and not wired up), so an
# ai_generated scene holds a PNG. Listing it here declared that PNG to
# Creatomate as a video element — the same class of mistake in the opposite
# direction. When a real text-to-video provider lands it will record
# media="video" below and be typed from that, not from its label.
_VIDEO_ASSET_TYPES = {"spokesperson", "stock"}


def _element_type(scene):
    """image, video or text — decided by what the asset IS.

    `asset_meta["media"]` is written at the moment an asset is attached, by
    whichever route attached it, and is the only source here that cannot go
    stale: an asset_type is a label whose meaning changes when the provider
    behind it changes, and a URL suffix is frequently absent.
    """
    url = (scene.get("asset_url") or "")
    if scene.get("is_cta") and not url:
        return "text"
    if not url:
        return "image"
    media = (scene.get("asset_meta") or {}).get("media")
    if media in ("image", "video"):
        return media
    if scene.get("asset_type") in _VIDEO_ASSET_TYPES:
        return "video"
    return "video" if url.lower().split("?")[0].endswith(_VIDEO_SUFFIXES) else "image"


def _presenter_element(scene):
    """The keyed spokesperson layer for one scene, or None.

    Only scenes whose presenter was generated to sit OVER footage get one —
    a full-frame presenter is already the scene's own asset and needs no
    second element. The key colour comes from the same constant the clip was
    generated against (config.CHROMA_KEY_COLOR), so the two can never drift.

    As with `build_source` itself, the chroma-key key name is the part most
    likely to differ between Creatomate API versions; it is the one field to
    adjust here if your account's schema disagrees.
    """
    meta = scene.get("asset_meta") or {}
    url = meta.get("spokesperson_url")
    if not url or not meta.get("spokesperson_over_footage"):
        return None
    element = {
        "id": f"presenter_{scene['id']}",
        "track": TRACK_PRESENTER,
        "time": scene["start"],
        "duration": round(scene["end"] - scene["start"], 2),
        "type": "video",
        "source": url,
        "fit": "contain",
    }
    if meta.get("chroma_key"):
        element["chroma_key"] = {
            "color": meta.get("chroma_key_color") or CHROMA_KEY_COLOR,
            "threshold": 0.25,
        }
    return element


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
