"""The picture assets a Performance Max asset group cannot deploy without.

A Search campaign needs no image at all. Performance Max refuses an asset
group without a landscape image, a square image and a logo — and it refuses
the **whole** bulk mutate, so a missing square costs the campaign rather than
the square. That is what this file exists for.

Three decisions in it, each of which a first draft got the other way round.

**A logo is looked up, never generated.** `logo.resolve()` already finds the
client's real mark — on their Hub record, then behind a button on a billed
Brandfetch lookup, then an upload — and a real logo is strictly better than a
plausible AI-generated one, which nobody proof-reads because they recognise
it. `modules/ads_builder/logo.py` makes the same argument about a favicon
scraped off a landing page.

**The ratios are cropped, not asked for.** OpenAI's image models offer
1024x1024, 1536x1024 and 1024x1536 and Google wants 1.91:1, 1:1 and 4:5, so
only the square lines up. Each is generated at the nearest supported
orientation and centre-cropped through `hub/images.crop_to_ratio()` — centre,
because Google's own guidance is that the subject belongs in the middle 80%
of the frame, which is what every prompt here asks for.

**A generation is billed per press, so nothing generates by arriving.** This
is called from a route a person pressed, never from the generator, and every
call records its spend whether or not it worked: a refused call spent nothing
and is out of every billable total, but a wall of them is what a spent
allowance looks like from this side.

The HTTP is this module's own rather than a call into Image Creator's route.
`campaign_ai.py` already talks to OpenAI directly instead of importing
another module's Flask view, and reaching across for a route would be a new
pattern in a codebase that has one.
"""
from __future__ import annotations

import base64
import logging

import requests

from hub import images as hub_images
from hub import storage

from . import logo as logo_lookup, pmax_spec

log = logging.getLogger(__name__)

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
TIMEOUT = 180

# The supported source size closest in orientation to each required ratio,
# named per role rather than computed: gpt-image-1 takes exactly three sizes
# and picking the nearest is a judgment about the crop, not arithmetic.
SOURCE_SIZE = {
    "marketing": "1536x1024",     # 1.5:1 down to 1.91:1 — trims top and bottom
    "square": "1024x1024",        # already 1:1, cropped only to be sure
    "portrait": "1024x1536",      # 1:1.5 up to 4:5 — trims top and bottom
}

# Said to the model on every generation. Google crops up to 20% off the edges
# when it reflows an asset across placements, so a subject at the edge is a
# subject that disappears on some of them.
FRAMING_RULE = (
    "Keep the subject entirely within the middle 80% of the frame. "
    "No words, no lettering, no logos and no text of any kind in the image."
)


class ImageError(RuntimeError):
    """A generation that did not happen, in words a rep can act on."""


def _openai_key() -> str:
    from . import campaign_ai
    return campaign_ai.openai_key()


def _openai_image_model() -> str:
    try:
        from hub.config import settings
        return getattr(settings, "openai_image_model", "") or "gpt-image-1"
    except Exception:                                    # noqa: BLE001
        return "gpt-image-1"


def _note(ok: bool, model: str) -> None:
    """Record the spend. Never raises — a failure to record must not break
    the feature that spent."""
    try:
        from hub import ai as hub_ai
        # The model is passed explicitly because an images response carries no
        # usage block, and openai_cost() prices anything named gpt-image* per
        # image: without the name there is nothing to price.
        hub_ai.note_usage("ads_builder", {}, model=model,
                          purpose="pmax_image", ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def _generate_bytes(prompt: str, size: str) -> bytes:
    key = _openai_key()
    if not key:
        raise ImageError("No OpenAI API key on this deployment. Set OPENAI_API_KEY "
                         "on the Hub service.")
    model = _openai_image_model()
    body = {"model": model, "prompt": f"{prompt.strip()} {FRAMING_RULE}"[:3800],
            "size": size, "n": 1}
    try:
        resp = requests.post(
            OPENAI_IMAGES_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT)
    except Exception as exc:                             # noqa: BLE001
        _note(False, model)
        raise ImageError(f"OpenAI could not be reached: {type(exc).__name__}") from exc
    if not resp.ok:
        _note(False, model)
        raise ImageError(f"OpenAI refused the request: {resp.text[:300]}")
    _note(True, model)

    item = ((resp.json() or {}).get("data") or [{}])[0]
    # gpt-image-1 answers with b64_json and never a url; only the older
    # dall-e-* models return a hosted one, and that expires within the hour.
    # Reading one unconditionally is what made Image Creator draw two empty
    # options and report a clean success, so both shapes are handled.
    raw = item.get("b64_json")
    if raw:
        return base64.b64decode(raw)
    url = item.get("url")
    if url:
        fetched = requests.get(url, timeout=TIMEOUT)
        if fetched.ok:
            return fetched.content
    raise ImageError("OpenAI returned no image data.")


def generate_asset_image(role: str, prompt: str, *, client: str = "",
                         business: str = "") -> dict:
    """One image, at the exact ratio Google refuses without.

    Returns the stored URL and the real dimensions, so a caller can say what
    it got rather than assuming the crop met the floor.
    """
    entry = pmax_spec.IMAGE_ASSETS.get(role)
    if not entry:
        raise ImageError(f'"{role}" is not a Performance Max image role.')
    if not str(prompt or "").strip():
        raise ImageError("Describe the picture this asset group needs.")

    raw = _generate_bytes(prompt, SOURCE_SIZE.get(role, "1024x1024"))
    min_w, min_h = entry["recommended_size"]
    ratio_w, ratio_h = entry["min_size"]
    processed = hub_images.crop_to_ratio(
        raw, ratio_w / ratio_h, min_width=min_w, min_height=min_h, fmt="JPEG")

    if len(processed.data) > pmax_spec.IMAGE_MAX_BYTES:
        # Refused by name rather than shipped over the ceiling: Google rejects
        # the asset and the message it returns names a resource, not a file.
        raise ImageError(
            f"The generated {entry['label'].lower()} came back at "
            f"{len(processed.data) // 1024} KB, over Google's "
            f"{pmax_spec.IMAGE_MAX_BYTES // (1024 * 1024)} MB limit.")

    stored = storage.put(
        "ads_pmax", f"{storage.slug(business or client or 'asset')}-{role}.jpg",
        processed.data, client=client or "")
    url = stored.get("url") if isinstance(stored, dict) else getattr(stored, "url", "")
    return {
        "role": role, "url": url or "",
        # Derived on the row rather than in the template: a 1200x628 hero drawn
        # into a 200px box is the full asset delivered to draw a tile, and
        # Cloudinary bills a credit per gigabyte delivered. hub/storage.py is
        # the one reading of what a preview is; the deploy still uploads the
        # ORIGINAL, because the preview is what a screen shows and never what
        # a client's campaign runs.
        "thumb": storage.preview_url(url or ""),
        "width": processed.width, "height": processed.height,
        "ratio": entry["ratio"], "label": entry["label"],
        "prompt": str(prompt)[:600],
        "meets_minimum": (processed.width >= entry["min_size"][0]
                          and processed.height >= entry["min_size"][1]),
    }


def client_logo(client_name: str, website: str = "", *, allow_live: bool = False) -> dict:
    """The client's real logo, or a named absence.

    Never generated and never guessed at from a domain — a wrong logo on a
    client's own campaign is worse than none, because nobody proof-reads the
    thing they recognise. `logo.resolve()` is the one reader, so this cannot
    disagree with what the estimate shows.
    """
    found = logo_lookup.resolve(client_name, website, allow_live=allow_live) or {}
    url = found.get("url") or ""
    return {
        "role": "logo", "url": url,
        "label": pmax_spec.LOGO_ASSETS["logo"]["label"],
        "ratio": pmax_spec.LOGO_ASSETS["logo"]["ratio"],
        "source": found.get("source") or "",
        "found": bool(url),
        "note": found.get("note") or (
            "" if url else
            "No logo is on file for this client. Performance Max needs one, and "
            "it is an upload or a lookup rather than something to generate."),
    }
