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


def from_site(brief: dict) -> list[dict]:
    """Pictures a scan already found on the client's own website."""
    url = str(brief.get("website") or "").strip()
    if not url:
        return []
    try:
        from modules.scans.app import latest_payload_for_domain
        import json as _json
        blob = _json.dumps(latest_payload_for_domain(url) or {})[:400000]
    except Exception:                                       # noqa: BLE001
        return []
    seen, out = set(), []
    # og:image first where the payload carries one -- a site's own social
    # card is the picture its owner chose to represent it.
    for m in re.finditer(r'"(https://[^"]+?\.(?:jpg|jpeg|png|webp))"', blob, re.I):
        u = m.group(1)
        if u in seen or "logo" in u.lower() or "icon" in u.lower():
            continue
        seen.add(u)
        out.append({"url": u, "alt": "", "credit": "", "source": "their site"})
        if len(out) >= 6:
            break
    return out


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
    site = from_site(brief)
    pool = site + stock(brief, want=max(4, benefits + 2))

    empty = {"hero": None, "cards": [], "band": None,
             "credits": [], "source": "", "available": False}
    if not pool:
        return empty

    used, hero, band = set(), None, None
    for img in pool:
        if img.get("wide", True) and img["url"] not in used:
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
        if img["url"] not in used and img.get("wide", True):
            band = img
            break

    credits = sorted({f"{i['credit']} ({i['source']})"
                      for i in ([hero] + cards + ([band] if band else []))
                      if i and i.get("credit")})
    return {"hero": hero, "cards": cards, "band": band,
            "credits": credits,
            "source": "their site" if site else "stock",
            "available": True}
