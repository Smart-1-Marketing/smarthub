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


def _candidates(client_name: str, website: str) -> list:
    """The (name, domain) pairs worth asking the Hub's brand store for.

    One pair was not enough, and this is why a client whose logo was plainly on
    file came back empty. Brand data is stored two ways — under the slugified
    CLIENT NAME, and in a cache keyed by DOMAIN — and the generator has neither
    reliably:

      * the name typed into the campaign is not always the name the client is
        filed under ("Riverside HVAC" vs "Riverside HVAC LLC"), so the
        name-keyed store misses;
      * the URL on the campaign is the LANDING PAGE, which is often a campaign
        microsite or a subdomain rather than the client's own website, so the
        domain-keyed cache misses too.

    So the client is resolved through ``hub/client_key.py`` first — exact match
    or nothing, never a substring — and both the registry's name and the
    registry's own URL are tried alongside what the campaign carries.
    """
    pairs, seen = [], set()

    def add(name, domain):
        key = ((name or "").strip().lower(), (domain or "").strip().lower())
        if key in seen or not (key[0] or key[1]):
            return
        seen.add(key)
        pairs.append({"name": name or "", "domain": domain or ""})

    page_domain = domain_of(website)
    add(client_name, page_domain)

    try:
        from hub.client_key import resolve as _resolve
        found = _resolve(name=client_name or "", url=website or "")
        if found.get("known"):
            registry_name = found.get("client") or ""
            registry_url = ""
            try:
                from hub import clients_registry
                row = clients_registry.find_client(registry_name)
                registry_url = (row or {}).get("url") or (row or {}).get("domain") or ""
            except Exception:  # noqa: BLE001
                registry_url = ""
            registry_domain = domain_of(registry_url)
            add(registry_name, page_domain)
            add(registry_name, registry_domain)
            add(client_name, registry_domain)
    except Exception:  # noqa: BLE001 — the module runs outside the Hub
        pass

    return pairs


def from_client_record(client_name: str, website: str = "") -> dict:
    """What the Hub has already stored for this client.

    Reports what it looked under when it finds nothing, because "this client
    has no logo" and "we asked under a name they are not filed as" are
    different answers and only one of them means go and fetch one.
    """
    try:
        from hub import client_brand
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "source": "client record",
                "note": f"The client's brand data could not be read: {exc}"}

    tried, had_brand = [], False
    for pair in _candidates(client_name, website):
        tried.append(f"{pair['name'] or '—'} / {pair['domain'] or '—'}")
        try:
            kit = client_brand.brand_kit(pair["name"], pair["domain"])
        except Exception:  # noqa: BLE001
            continue
        if not kit.get("found"):
            continue
        had_brand = True
        best = _pick(kit.get("logos") or [])
        if not best:
            continue
        return {
            "found": True, "source": "client record",
            "url": best["url"], "format": best.get("format", ""),
            "width": best.get("width"), "height": best.get("height"),
            "note": "From the brand details already stored against this client.",
            "colors": [c["hex"] for c in (kit.get("colors") or [])[:6]],
            "matched": pair,
        }

    note = ("Brand details are on file for this client, but none of them carry a logo."
            if had_brand else "Nothing is on file for this client yet.")
    return {"found": False, "source": "client record", "note": note, "tried": tried}


LOOKUP_SOURCE = "logo lookup"


def from_brandfetch(website: str, client_name: str = "") -> dict:
    """A live lookup. Billed, so this is a button and never a page load.

    The provider is named in this file because that is what the code talks to,
    and nowhere on a screen: a rep pressing "Look up the logo" does not need to
    know which service answered, and naming it invites the question of what to
    do when it says no. What the screen gets is where the logo came from —
    "looked up" or "the client record" or "uploaded".
    """
    domain = domain_of(website)
    if not domain:
        return {"found": False, "source": LOOKUP_SOURCE,
                "note": "No website on this campaign, so there is nothing to look a logo up by."}
    key = _key()
    if not key:
        # The variable names belong on Settings, where somebody can act on
        # them, not in front of a rep who cannot. What a rep needs here is the
        # one thing they can do about it.
        return {"found": False, "source": LOOKUP_SOURCE, "unconfigured": True,
                "note": "Logo lookup is not switched on for this deployment — see the "
                        "environment reference on Settings. Upload the logo instead."}
    try:
        resp = requests.get(BRANDFETCH_BRAND.format(domain=domain),
                            headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"found": False, "source": LOOKUP_SOURCE,
                "note": f"The logo lookup could not be reached: {exc}"}

    try:  # counted against the monthly allowance, like every other caller
        from hub import quotas as _q
        _q.record("brandfetch", module="ads_builder", detail=domain)
    except Exception:  # noqa: BLE001
        pass

    if resp.status_code == 404:
        return {"found": False, "source": LOOKUP_SOURCE,
                "note": f"No logo found for {domain}. Upload one instead."}
    if not resp.ok:
        # A refused key and an unreachable service are different answers, and
        # calling the second one "bad key" sends somebody to rotate a good one.
        return {"found": False, "source": LOOKUP_SOURCE,
                "note": f"The logo lookup answered HTTP {resp.status_code}."}

    try:
        payload = resp.json()
    except ValueError:
        return {"found": False, "source": LOOKUP_SOURCE,
                "note": "The logo lookup returned something unreadable."}

    logos = []
    for logo in (payload.get("logos") or []):
        for fmt in (logo.get("formats") or []):
            if not fmt.get("src"):
                continue
            logos.append({"url": fmt["src"], "kind": logo.get("type") or "logo",
                          "format": (fmt.get("format") or "").lower(),
                          "width": fmt.get("width"), "height": fmt.get("height")})
    # Keep what the call paid for. Without this the same client is looked up
    # again from Client 360, from Image Creator and from here, three billed
    # calls for one answer -- and the Client 360 brand card, which reads
    # stored data and never fetches, stays empty through all three.
    try:
        from hub import seo as _hub_seo
        _hub_seo.save_brandfetch(payload.get("domain") or domain, payload,
                                 client=client_name or "")
    except Exception:  # noqa: BLE001 -- the module runs outside the Hub too
        pass

    logos.sort(key=lambda l: (0 if l["format"] == "svg" else 1, -(l.get("width") or 0)))
    best = _pick(logos)
    if not best:
        return {"found": False, "source": LOOKUP_SOURCE,
                "note": f"We know {domain} but found no logo for it."}
    return {
        "found": True, "source": LOOKUP_SOURCE,
        "url": best["url"], "format": best.get("format", ""),
        "width": best.get("width"), "height": best.get("height"),
        "note": f"Looked up from {domain}.",
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
                "next": "Look the logo up, or upload one."}
    live = from_brandfetch(website, client_name)
    if live.get("found"):
        return live
    return {**live, "tried": ["the client record", "a live lookup"],
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
