"""Write an SEO filename and alt text for one image.

The image is sent with the context that matters: which client, which project,
which page it already lives on, the page title and headings, and any alt text
or caption the site already had. Naming an image well is mostly a question of
knowing where it will sit, which is exactly what a bare pixel-only prompt can't
know.
"""

import base64
import json
import re

from . import settings

STOPWORDS = {"image", "photo", "picture", "img", "final", "new", "copy", "v2",
             "untitled", "screenshot", "dsc", "img20", "download"}

SYSTEM_PROMPT = """You write SEO filenames and alt text for images on a client's
website. You are given the client, the page the image sits on, and the image itself.

Rules:
- filename: 3-7 lowercase words, hyphen separated, no extension, no dates, no
  numbers unless the number is part of the subject. Describe what is in the
  image and tie it to the page's subject where that is honest.
- alt: one plain sentence, under 125 characters, describing what a sighted
  visitor sees. No "image of", no "picture of", no keyword stuffing.
- Never invent a brand, person, place, product name or claim that you cannot
  see in the image or read in the supplied context.
- Never use the words image, photo, picture, final, new, copy, v2, untitled.
- If the image is a logo, icon, chart or screenshot, say so plainly.

Reply with JSON only: {"filename": "...", "alt": "..."}"""


def slugify(text, *, max_words=8):
    text = re.sub(r"[^a-z0-9\s-]", " ", (text or "").lower())
    words = [w for w in re.split(r"[\s-]+", text) if w and w not in STOPWORDS]
    return "-".join(words[:max_words])[:80].strip("-")


def fallback_name(context, source_url=""):
    """A usable name when there's no AI available or the call fails."""
    existing = context.get("existing_alt") or context.get("caption") or ""
    base = slugify(existing) if existing else ""
    if not base:
        tail = re.sub(r"\.[a-z0-9]+$", "", (source_url or "").rsplit("/", 1)[-1])
        base = slugify(tail.replace("_", "-"))
    if not base:
        base = slugify(context.get("page_name") or context.get("page_title") or "")
    stem = "-".join(x for x in [slugify(context.get("company"), max_words=3), base] if x)
    return {
        "filename": (stem or "client-image")[:80],
        "alt": (existing or context.get("page_title") or context.get("company") or "")[
            : settings.ALT_MAX_CHARS
        ],
        "ai": False,
    }


def _user_content(context):
    lines = [
        f"Client: {context.get('company') or 'unknown'}",
        f"Project: {context.get('project') or 'unspecified'}",
        f"Page name: {context.get('page_name') or 'unspecified'}",
        f"Page URL: {context.get('page_url') or 'unspecified'}",
        f"Page title: {context.get('page_title') or ''}",
        f"Page heading: {context.get('page_h1') or ''}",
        f"Nearest heading above this image: {context.get('nearest_heading') or ''}",
        f"Existing alt text on the site: {context.get('existing_alt') or '(none)'}",
        f"Caption on the site: {context.get('caption') or '(none)'}",
        f"Current filename: {context.get('current_filename') or '(none)'}",
    ]
    return "\n".join(lines)


def suggest(image_bytes, context, *, avoid=None, mime="image/webp"):
    """Ask the vision model for a filename and alt. Never raises."""
    if not settings.OPENAI_API_KEY:
        return fallback_name(context, context.get("source_url", ""))

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        prompt = _user_content(context)
        if avoid:
            prompt += (
                "\n\nThese names were already suggested and rejected — write a "
                "materially different one: " + ", ".join(avoid[:5])
            )

        encoded = base64.b64encode(image_bytes).decode("ascii")
        resp = client.chat.completions.create(
            model=settings.OPENAI_VISION_MODEL,
            temperature=0.4,
            max_tokens=200,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{encoded}",
                                   "detail": "low"}},
                ]},
            ],
            timeout=45,
        )
        try:  # record spend so /diagnostics doesn't under-report
            from hub import ai as _hub_ai
            _hub_ai.note_sdk_usage("page_image_optimizer", resp, purpose="alt_text")
        except Exception:  # noqa: BLE001
            pass
        data = json.loads(resp.choices[0].message.content or "{}")
        filename = slugify(data.get("filename", ""))
        alt = re.sub(r"\s+", " ", str(data.get("alt", ""))).strip()
        if not filename:
            raise ValueError("model returned no filename")
        return {
            "filename": filename[:80],
            "alt": alt[: settings.ALT_MAX_CHARS],
            "ai": True,
        }
    except Exception as exc:  # noqa: BLE001 - a naming failure must never block a save
        result = fallback_name(context, context.get("source_url", ""))
        result["error"] = str(exc)[:200]
        return result
