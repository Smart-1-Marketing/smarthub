"""Pictures for a landing page.

A page of grey boxes and body copy does not convert, whatever the words say.
This picks the imagery: a hero, one picture per benefit, and a wide band
behind the closing call to action.

## Where the pictures come from, in order

  1. **The client's own site.** A photo of their actual premises, van or team
     beats any stock library, and it is the only source that is genuinely
     about them. Read from whatever a site scan already collected.
  2. **Stock**, through `modules.image_picker.providers` -- the same Pexels /
     Pixabay / Unsplash search the Image Picker uses, so keys, caching and
     rate limits are configured once.
  3. **Nothing.** No provider configured is a normal state, not an error, and
     it must not produce a page with broken image icons on it. The renderer
     gets an empty set and lays out a gradient hero instead.

## What it will not do

It does not put a picture on the page to fill a hole. A benefit with no
sensible image gets none, and the card renders as text -- three cards with
photos and one without reads as a page still being built, so the renderer
takes all-or-nothing per section rather than a partial set.

Stock photography is illustration and is treated as such: it is never
captioned as the client's own work, never presented as a customer, and never
used as evidence of anything. That line matters because the rest of this
module's output is under the same rule as the copy -- a page may be short,
it may not lie.
"""
from __future__ import annotations

import re

# Enough to fill a hero on a big screen without being a 6 MB download on a
# phone. The providers' "preview" size sits around here.
_MIN_HERO_WIDE = 1200
_MIN_CARD_WIDE = 600

# Words that make a stock search return people at desks in headsets, which is
# the visual language of a template rather than of a real local business.
_NEGATIVES = ["clipart", "vector", "illustration", "3d render", "mockup",
              "call center", "headset"]


def _terms(brief: dict) -> list[str]:
    """What to search for, most specific first.

    Industry plus place beats industry alone: "hvac columbus ohio" returns
    somebody's actual street, "hvac" returns a stock photo of a thermostat.
    """
    industry = str(brief.get("industry") or "").strip()
    city = str(brief.get("city") or "").strip()
    geo = str(brief.get("geo") or "").strip()
    place = city or geo.split(",")[0].strip()
    products = [str(p) for p in (brief.get("products") or []) if p][:2]

    out = []
    if industry and place:
        out.append(f"{industry} {place}")
    if industry:
        out.append(industry)
    for p in products:
        # Rate-card product names are media channels ("Connected TV"), not
        # subjects. Only useful when there is no industry to search on.
        if not industry:
            out.append(p)
    if not out:
        out.append("local business storefront")
    return out[:3]


def _wide_enough(img: dict, minimum: int) -> bool:
    w = int(img.get("width") or 0)
    h = int(img.get("height") or 0)
    # Portrait crops badly in a hero band, so landscape-ish only.
    return w >= minimum and (h == 0 or w >= h)


def theirs(image_url: str, website: str) -> bool:
    """Is this picture actually on the client's own site?

    A scan payload is 440 fields of whatever the crawler saw, and the image
    URLs in it belong to all sorts of people: the scan vendor's own
    screenshots, a Facebook social card, a Google static map, an ad creative,
    **another agency's Cloudinary folder**. The regex below matched every one
    and labelled them all `their site` — so any of them could become the hero
    of a landing page presented as the client's own premises, which is the
    one thing the docstring at the top of this file says stock may never be.

    That is `client_urls.NOT_A_WEBSITE` one module over, and it is not
    hypothetical there either: on this deployment's own product export *every
    single* click-thru domain turned out to be a file host.

    A subdomain counts — `cdn.`, `www.`, `images.` are ordinarily theirs —
    and `hub/client_context.canonical_domain()` is the one reading of what a
    domain means, so this cannot drift from every other join in the Hub.
    """
    from hub.client_context import canonical_domain
    theirs_domain = canonical_domain(website)
    host = canonical_domain(image_url)
    if not theirs_domain or not host:
        return False
    return host == theirs_domain or host.endswith("." + theirs_domain)


def from_site(brief: dict) -> dict:
    """Pictures a scan already found on the client's own website.

    Returns `{"images": [...], "rejected": n}` rather than a bare list: what
    is dropped is counted, because a list that quietly gets shorter cannot be
    told from a site that has no pictures on it.
    """
    url = str(brief.get("website") or "").strip()
    if not url:
        return {"images": [], "rejected": 0}
    try:
        from modules.scans.app import latest_payload_for_domain
        import json as _json
        blob = _json.dumps(latest_payload_for_domain(url) or {})[:400000]
    except Exception:                                       # noqa: BLE001
        return {"images": [], "rejected": 0}
    seen, out, rejected = set(), [], 0
    # og:image first where the payload carries one -- a site's own social
    # card is the picture its owner chose to represent it.
    for m in re.finditer(r'"(https://[^"]+?\.(?:jpg|jpeg|png|webp))"', blob, re.I):
        u = m.group(1)
        if u in seen or "logo" in u.lower() or "icon" in u.lower():
            continue
        seen.add(u)
        if not theirs(u, url):
            rejected += 1
            continue
        out.append({"url": u, "alt": "", "credit": "", "source": "their site",
                    # Not measured, rather than assumed. The payload carries
                    # no dimensions for these, and `pick()` used to read a
                    # missing `wide` as True -- so _MIN_HERO_WIDE was skipped
                    # entirely for the source this module prefers, and a
                    # thumbnail off their page could be the full-bleed hero.
                    "wide": None})
        if len(out) >= 6:
            break
    return {"images": out, "rejected": rejected}


def stock(brief: dict, want: int = 6) -> list[dict]:
    """Stock photography, through the Image Picker's own provider layer."""
    try:
        from modules.image_picker import providers
    except Exception:                                       # noqa: BLE001
        return []
    if not providers.any_provider_configured():
        return []
    try:
        found = providers.search(_terms(brief), per_page=12,
                                 orientation="landscape",
                                 negatives=_NEGATIVES, limit=40)
    except Exception:                                       # noqa: BLE001
        return []
    out = []
    for img in found.get("results") or []:
        url = img.get("preview") or img.get("full") or ""
        if not url or not _wide_enough(img, _MIN_CARD_WIDE):
            continue
        out.append({
            "url": url,
            "wide": _wide_enough(img, _MIN_HERO_WIDE),
            "alt": img.get("alt") or "",
            "credit": img.get("author") or "",
            "credit_url": img.get("author_url") or "",
            "source": img.get("provider") or "stock",
        })
        if len(out) >= want * 2:
            break
    return out


def pick(brief: dict, benefits: int = 0) -> dict:
    """The set of pictures a page needs, or an honest empty set.

    Returns {"hero", "cards", "band", "credits", "source"}. The renderer must
    cope with every one of those being empty -- no provider configured is the
    default state of a fresh deployment, and a landing page that renders as
    broken image icons is worse than one with none.
    """
    found = from_site(brief)
    site = found["images"]
    pool = site + stock(brief, want=max(4, benefits + 2))

    empty = {"hero": None, "cards": [], "band": None, "credits": [],
             "source": "", "available": False,
             "not_theirs": found["rejected"]}
    if not pool:
        return empty

    # `is not False` rather than a default of True. A stock image measured and
    # found narrow is still skipped, exactly as before; one off their own site
    # carries None because nothing measured it, and their site still comes
    # first -- which is this module's stated order and the reason the old
    # default read as harmless.
    used, hero, band = set(), None, None
    for img in pool:
        if img.get("wide") is not False and img["url"] not in used:
            hero = img
            used.add(img["url"])
            break
    if hero is None:
        hero = pool[0]
        used.add(hero["url"])

    # All-or-nothing on the cards: a row where three have photos and one does
    # not reads as a page that is still loading.
    cards = [i for i in pool if i["url"] not in used][:benefits]
    if benefits and len(cards) < benefits:
        cards = []
    for c in cards:
        used.add(c["url"])

    for img in pool:
        if img["url"] not in used and img.get("wide") is not False:
            band = img
            break

    picked = [i for i in ([hero] + cards + ([band] if band else [])) if i]
    credits = sorted({f"{i['credit']} ({i['source']})"
                      for i in picked if i.get("credit")})
    # What the set IS, not what it was searched for. This said "their site"
    # whenever the site search returned anything at all, however much of what
    # was actually picked came from a stock library -- and captioning stock as
    # the client's own work is the one thing the docstring at the top of this
    # file rules out.
    kinds = {i.get("source") for i in picked}
    source = ("their site" if kinds == {"their site"} else
              "their site and stock" if "their site" in kinds else "stock")
    return {"hero": hero, "cards": cards, "band": band,
            "credits": credits,
            "source": source,
            "not_theirs": found["rejected"],
            "available": True}
