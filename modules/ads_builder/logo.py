"""The client's logo, in the order it should be looked for.

Three sources, tried in order, and **each answer says which one it came from**:

1. **What the Hub already knows.** ``hub/client_brand.brand_kit()`` reads the
   Brandfetch data cached against the client during SEO setup or Image Creator.
   Free, instant, and already the logo everything else in the Hub uses — a
   second lookup that returned a different file would put two different logos
   on two documents for one client.
2. **A live Brandfetch lookup**, when nothing is cached and there is a domain
   to look up. Billed and counted, so it happens on a button rather than on
   page load.
3. **Upload**, which is the only one that always works and is therefore never
   removed from the screen.

The rule underneath all three: **a logo is never guessed at.** No
``https://<clientname>.com/logo.png``, no favicon scraped off the landing page
and called a logo. A wrong logo on a client-facing estimate is worse than no
logo, because nobody proof-reads the thing they recognise.
"""
from __future__ import annotations

import re

import requests

TIMEOUT = 20
BRANDFETCH_BRAND = "https://api.brandfetch.io/v2/brands/{domain}"


def domain_of(url: str) -> str:
    raw = str(url or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw).removeprefix("www.").split("/")[0]
    return raw if "." in raw else ""


def _key() -> str:
    """Through hub.config, so every spelling this deployment might use resolves.

    modules/image_creator reads BRANDFETCH_API_KEY off os.environ directly and
    is named in /api/integrity for it — this is the same setting, read the way
    the trap in CLAUDE.md says to.
    """
    try:
        from hub.config import settings
        return (settings.brandfetch_key or "").strip()
    except Exception:  # noqa: BLE001
        import os
        return (os.environ.get("BRANDFETCH_API") or
                os.environ.get("BRANDFETCH_API_KEY") or "").strip()


def _pick(logos: list) -> dict | None:
    """The one to put on a document: SVG first, then the largest raster.

    A symbol or icon is accepted only when there is no full logo — a favicon
    on a proposal header reads as a placeholder somebody forgot to replace.
    """
    if not logos:
        return None
    full = [l for l in logos if (l.get("kind") or "logo") == "logo"] or logos
    return full[0]


def from_client_record(client_name: str, website: str = "") -> dict:
    """What the Hub has already cached for this client."""
    try:
        from hub import client_brand
        kit = client_brand.brand_kit(client_name or "", domain_of(website))
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "source": "client record",
                "note": f"The client's brand data could not be read: {exc}"}

    if not kit.get("found"):
        return {"found": False, "source": "client record",
                "note": kit.get("note") or "No brand data on file for this client yet."}
    best = _pick(kit.get("logos") or [])
    if not best:
        return {"found": False, "source": "client record",
                "note": "Brand data is on file for this client, but it carries no logo."}
    return {
        "found": True, "source": "client record",
        "url": best["url"], "format": best.get("format", ""),
        "width": best.get("width"), "height": best.get("height"),
        "note": "From the brand data already stored against this client.",
        "colors": [c["hex"] for c in (kit.get("colors") or [])[:6]],
    }


def from_brandfetch(website: str, client_name: str = "") -> dict:
    """A live lookup. Billed, so this is a button and never a page load."""
    domain = domain_of(website)
    if not domain:
        return {"found": False, "source": "Brandfetch",
                "note": "No website on this campaign, so there is no domain to look up."}
    key = _key()
    if not key:
        return {"found": False, "source": "Brandfetch",
                "note": "Brandfetch is not configured on this deployment "
                        "(BRANDFETCH_API / BRANDFETCH_API_KEY). Upload the logo instead."}
    try:
        resp = requests.get(BRANDFETCH_BRAND.format(domain=domain),
                            headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"found": False, "source": "Brandfetch",
                "note": f"Brandfetch could not be reached: {exc}"}

    try:  # counted against the monthly allowance, like every other caller
        from hub import quotas as _q
        _q.record("brandfetch", module="ads_builder", detail=domain)
    except Exception:  # noqa: BLE001
        pass

    if resp.status_code == 404:
        return {"found": False, "source": "Brandfetch",
                "note": f"Brandfetch has no brand for {domain}. Upload the logo instead."}
    if not resp.ok:
        # A refused key and an unreachable service are different answers, and
        # calling the second one "bad key" sends somebody to rotate a good one.
        return {"found": False, "source": "Brandfetch",
                "note": f"Brandfetch answered HTTP {resp.status_code}."}

    try:
        payload = resp.json()
    except ValueError:
        return {"found": False, "source": "Brandfetch",
                "note": "Brandfetch returned something that was not JSON."}

    logos = []
    for logo in (payload.get("logos") or []):
        for fmt in (logo.get("formats") or []):
            if not fmt.get("src"):
                continue
            logos.append({"url": fmt["src"], "kind": logo.get("type") or "logo",
                          "format": (fmt.get("format") or "").lower(),
                          "width": fmt.get("width"), "height": fmt.get("height")})
    logos.sort(key=lambda l: (0 if l["format"] == "svg" else 1, -(l.get("width") or 0)))
    best = _pick(logos)
    if not best:
        return {"found": False, "source": "Brandfetch",
                "note": f"Brandfetch knows {domain} but has no logo for it."}
    return {
        "found": True, "source": "Brandfetch",
        "url": best["url"], "format": best.get("format", ""),
        "width": best.get("width"), "height": best.get("height"),
        "note": f"Fetched live from Brandfetch for {domain}.",
        "colors": [c.get("hex") for c in (payload.get("colors") or [])[:6] if c.get("hex")],
    }


def resolve(client_name: str, website: str, *, allow_live: bool = False) -> dict:
    """The client record first, Brandfetch only when asked.

    ``allow_live`` is False on a page load and True behind the button, because
    a lookup that costs money must not fire because somebody opened a tab.
    """
    stored = from_client_record(client_name, website)
    if stored.get("found"):
        return stored
    if not allow_live:
        return {**stored, "can_fetch": bool(domain_of(website)),
                "next": "Run a Brandfetch lookup, or upload the logo."}
    live = from_brandfetch(website, client_name)
    if live.get("found"):
        return live
    return {**live, "tried": ["client record", "Brandfetch"],
            "next": "Upload the logo."}


def store_uploaded(data: bytes, filename: str, client_name: str) -> dict:
    """Put an uploaded logo where the estimate can render it.

    Through ``hub/storage.py`` rather than a second Cloudinary configuration —
    the opportunistic-migration rule in CLAUDE.md, and the reason the "cap the
    longest edge" fix had to be found in six places the last time.
    """
    if not data:
        return {"found": False, "note": "That file is empty."}
    try:
        from hub import storage
        stored = storage.put("ads_logos", filename or "logo.png", data,
                             client=client_name or "",
                             context={"client": client_name or ""})
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "source": "upload",
                "note": f"The logo could not be stored: {exc}"}
    if not getattr(stored, "url", ""):
        return {"found": False, "source": "upload",
                "note": "The logo was uploaded but no URL came back for it."}
    return {"found": True, "source": "upload", "url": stored.url,
            "public_id": stored.public_id,
            "note": f"Uploaded by hand ({filename})."}
