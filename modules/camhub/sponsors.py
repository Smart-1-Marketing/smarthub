"""Sponsors and placements: the product behind the cam page.

The page has five paid positions in two tiers -- one presenting sponsor,
four supporting -- and the rules here are what make them sellable:

- **The presenting slot is one placement at a time.** Two active presenting
  flights that overlap are a validation error, never a silent overwrite;
  that exclusivity is what the sponsor paid for.
- **Supporting slots fill by weight, shuffled per page load**, so nobody is
  permanently last. The served position rides on each slot for Sprint 4's
  impression log.
- **Fewer than four sold means house ads fill the rest**, in the same visual
  treatment. A flight that ends reverts its slot to house on the next read.
  Nothing expires into a blank space.
- **Category exclusivity is a soft warning** at save time: the conflict is
  named, and a person decides.
- **Copy is capped** where it is entered, because copy that overflows the
  tile is the most common way an ad swap goes wrong.

Creative uploads go through hub/storage.py and hub/images.py -- never to
the disk -- and a preview of an unsaved draft is a signed, short-lived
token on the public page, so a draft is never guessable and the page needs
no second renderer.
"""
from __future__ import annotations

import base64
import json
import logging
import random
from datetime import date, datetime, timezone

from sqlalchemy import select

from .models import Placement, Sponsor, session

log = logging.getLogger("hub")

POSITIONS = ("presenting", "supporting")
STATUSES = ("draft", "active", "paused")
ANIMATIONS = ("lower_third", "wipe", "crossfade", "shimmer", "tagline", "static")
SUPPORTING_SLOTS = 4
LIMITS = {"name": 60, "headline": 70, "body_presenting": 180, "body_supporting": 110,
          "cta_label": 28, "tagline": 60, "alt_text": 160}
PREVIEW_SALT = "camhub-preview"
PREVIEW_MAX_AGE = 3600
DRAFT_FIELDS = ("name", "headline", "body", "cta_label", "url", "animation",
                "tagline_2", "tagline_3", "alt_text", "logo_url", "image_url")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- sponsors

def sponsor_dict(row: Sponsor) -> dict:
    return {"id": row.id, "name": row.name, "category": row.category or "",
            "contact_name": row.contact_name or "", "email": row.email or "",
            "phone": row.phone or "", "website": row.website or "", "notes": row.notes or "",
            "created_at": _aware(row.created_at), "updated_at": _aware(row.updated_at)}


def list_sponsors() -> list[dict]:
    with session() as s:
        return [sponsor_dict(r) for r in s.execute(select(Sponsor).order_by(Sponsor.name)).scalars().all()]


def get_sponsor(sponsor_id: int) -> dict | None:
    with session() as s:
        row = s.get(Sponsor, int(sponsor_id))
        return sponsor_dict(row) if row else None


def save_sponsor(data: dict, sponsor_id: int | None = None) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("A sponsor needs a name.")
    with session() as s:
        row = s.get(Sponsor, int(sponsor_id)) if sponsor_id else None
        if sponsor_id and row is None:
            raise LookupError(f"no sponsor {sponsor_id}")
        if row is None:
            row = Sponsor()
            s.add(row)
        row.name = name[:120]
        row.category = str(data.get("category") or "").strip()[:80]
        row.contact_name = str(data.get("contact_name") or "").strip()[:120]
        row.email = str(data.get("email") or "").strip()[:200]
        row.phone = str(data.get("phone") or "").strip()[:60]
        row.website = str(data.get("website") or "").strip()[:300]
        row.notes = str(data.get("notes") or "").strip()
        s.commit()
        return sponsor_dict(row)


# -------------------------------------------------------------- placements

def placement_dict(row: Placement, sponsor: Sponsor | None = None, today: date | None = None) -> dict:
    out = {
        "id": row.id, "page_id": row.page_id, "sponsor_id": row.sponsor_id,
        "position": row.position or "supporting", "is_house": bool(row.is_house),
        "status": row.status or "draft", "animation": row.animation or "static",
        "name": row.name or (sponsor.name if sponsor else "") or "",
        "headline": row.headline or "", "body": row.body or "", "cta_label": row.cta_label or "",
        "url": row.url or "", "logo_url": row.logo_url or "", "image_url": row.image_url or "",
        "alt_text": row.alt_text or "", "tagline_2": row.tagline_2 or "", "tagline_3": row.tagline_3 or "",
        "start_date": row.start_date or "", "end_date": row.end_date or "",
        "weight": row.weight or 1, "sort_order": row.sort_order or 0,
        "updated_by": row.updated_by or "", "updated_at": _aware(row.updated_at),
        "sponsor_name": sponsor.name if sponsor else "", "category": (sponsor.category if sponsor else "") or "",
    }
    out["effective"] = effective_status(out, today or _today())
    out["days_remaining"] = _days_remaining(out, today or _today())
    return out


def effective_status(p: dict, today: date | None = None) -> str:
    """What the page does with it: draft and paused as set; an active one is
    scheduled before its start, live inside its flight, ended after."""
    today = today or _today()
    if p.get("status") in ("draft", "paused"):
        return p["status"]
    end = p.get("end_date") or ""
    start = p.get("start_date") or ""
    if end and today.isoformat() > end:
        return "ended"
    if start and today.isoformat() < start:
        return "scheduled"
    return "live"


def _days_remaining(p: dict, today: date) -> int | None:
    if not p.get("end_date"):
        return None
    try:
        return (date.fromisoformat(p["end_date"]) - today).days
    except ValueError:
        return None


def list_placements(page_id: int, today: date | None = None) -> list[dict]:
    with session() as s:
        rows = s.execute(select(Placement).where(Placement.page_id == page_id)
                         .order_by(Placement.position, Placement.sort_order, Placement.id)).scalars().all()
        return [placement_dict(r, s.get(Sponsor, r.sponsor_id) if r.sponsor_id else None, today) for r in rows]


def get_placement(placement_id: int) -> dict | None:
    with session() as s:
        row = s.get(Placement, int(placement_id))
        if not row:
            return None
        return placement_dict(row, s.get(Sponsor, row.sponsor_id) if row.sponsor_id else None)


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """Two flights overlap unless one ends before the other starts. An empty
    date is open on that side."""
    if a_end and b_start and a_end < b_start:
        return False
    if b_end and a_start and b_end < a_start:
        return False
    return True


def _iso_or_blank(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"'{text}' is not a date (YYYY-MM-DD).") from exc


def validate(data: dict, page_id: int, placement_id: int | None = None) -> tuple[dict, list[str]]:
    """The cleaned fields, and the soft warnings. Raises ValueError for what
    cannot be saved: a bad position, copy over its cap, dates the wrong way
    round, or a presenting flight that overlaps another."""
    position = str(data.get("position") or "supporting")
    if position not in POSITIONS:
        raise ValueError(f"position must be one of {', '.join(POSITIONS)}")
    status = str(data.get("status") or "draft")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    animation = str(data.get("animation") or "static")
    if animation not in ANIMATIONS:
        raise ValueError(f"animation must be one of {', '.join(ANIMATIONS)}")
    is_house = bool(data.get("is_house"))
    sponsor_id = data.get("sponsor_id")
    sponsor_id = int(sponsor_id) if str(sponsor_id or "").strip() else None
    if not is_house and not sponsor_id:
        raise ValueError("A sold placement needs a sponsor; tick house ad for the winery's own.")
    start, end = _iso_or_blank(data.get("start_date")), _iso_or_blank(data.get("end_date"))
    if start and end and end < start:
        raise ValueError("The flight ends before it starts.")
    body_cap = LIMITS["body_presenting"] if position == "presenting" else LIMITS["body_supporting"]
    caps = {"name": LIMITS["name"], "headline": LIMITS["headline"], "body": body_cap,
            "cta_label": LIMITS["cta_label"], "tagline_2": LIMITS["tagline"],
            "tagline_3": LIMITS["tagline"], "alt_text": LIMITS["alt_text"]}
    clean = {"position": position, "status": status, "animation": animation,
             "is_house": is_house, "sponsor_id": None if is_house else sponsor_id,
             "start_date": start, "end_date": end,
             "url": str(data.get("url") or "").strip()[:600],
             "logo_url": str(data.get("logo_url") or "").strip()[:600],
             "image_url": str(data.get("image_url") or "").strip()[:600]}
    for field, cap in caps.items():
        text = str(data.get(field) or "").strip()
        if len(text) > cap:
            raise ValueError(f"{field.replace('_', ' ')} is {len(text)} characters; the tile fits {cap}.")
        clean[field] = text
    try:
        clean["weight"] = max(1, min(10, int(data.get("weight") or 1)))
        clean["sort_order"] = int(data.get("sort_order") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("weight and sort order are whole numbers") from exc
    if clean["url"] and not clean["url"].lower().startswith(("http://", "https://", "mailto:", "tel:")):
        raise ValueError("The destination must be a full URL starting with https://.")

    warnings: list[str] = []
    if status == "active" and not is_house:
        others = [p for p in list_placements(page_id) if p["id"] != placement_id
                  and not p["is_house"] and p["status"] == "active"]
        if position == "presenting":
            for o in others:
                if o["position"] == "presenting" and _overlaps(start, end, o["start_date"], o["end_date"]):
                    raise ValueError(
                        f"The presenting slot is one sponsor at a time: {o['name'] or o['sponsor_name']} "
                        f"holds it {o['start_date'] or 'open'} to {o['end_date'] or 'open'}.")
        sponsor = get_sponsor(sponsor_id) if sponsor_id else None
        if sponsor and sponsor.get("category"):
            for o in others:
                if o["sponsor_id"] != sponsor_id and o.get("category") and \
                        o["category"].strip().lower() == sponsor["category"].strip().lower() and \
                        _overlaps(start, end, o["start_date"], o["end_date"]):
                    warnings.append(f"Category clash: {o['sponsor_name']} is also {o['category']} and "
                                    f"runs {o['start_date'] or 'open'} to {o['end_date'] or 'open'}. "
                                    "Exclusivity is what the presenting sponsor was sold; decide before this goes live.")
    return clean, warnings


def save_placement(data: dict, page_id: int, placement_id: int | None = None,
                   actor: str = "") -> tuple[dict, list[str]]:
    clean, warnings = validate(data, page_id, placement_id)
    with session() as s:
        row = s.get(Placement, int(placement_id)) if placement_id else None
        if placement_id and (row is None or row.page_id != page_id):
            raise LookupError(f"no placement {placement_id} on this page")
        if row is None:
            row = Placement(page_id=page_id)
            s.add(row)
        for field, value in clean.items():
            setattr(row, field, value)
        row.updated_by = actor[:120]
        s.commit()
        out = placement_dict(row, s.get(Sponsor, row.sponsor_id) if row.sponsor_id else None)
    return out, warnings


def delete_placement(placement_id: int, page_id: int) -> bool:
    with session() as s:
        row = s.get(Placement, int(placement_id))
        if row is None or row.page_id != page_id:
            return False
        s.delete(row)
        s.commit()
        return True


# ------------------------------------------------------------ house ads

def ensure_house_placements(page: dict) -> int:
    """Five house placements from the page's config, created once. The
    unsold fallback the spec asks for: a slot is never an empty box."""
    existing = [p for p in list_placements(page["id"]) if p["is_house"]]
    if existing:
        return 0
    cfg = page.get("config") or {}
    house = cfg.get("house_ads") or {}
    links = cfg.get("links") or {}
    made = 0
    pres = house.get("presenting") or {}
    with session() as s:
        s.add(Placement(page_id=page["id"], position="presenting", is_house=True, status="active",
                        animation="static", name=pres.get("name") or page.get("business_name") or "",
                        headline=pres.get("headline") or "", body=pres.get("body") or "",
                        cta_label=pres.get("cta") or "", url=pres.get("url") or "", sort_order=0,
                        updated_by="provision"))
        made += 1
        for i, t in enumerate(house.get("supporting") or []):
            s.add(Placement(page_id=page["id"], position="supporting", is_house=True, status="active",
                            animation="static", name=t.get("name") or "", headline="",
                            body=t.get("body") or "", cta_label=t.get("cta") or "",
                            url=t.get("url") or links.get(t.get("url_key") or "", ""),
                            sort_order=i, updated_by="provision"))
            made += 1
        s.commit()
    return made


# ------------------------------------------------------------- the slots

def _slot(p: dict, position: int, sold: bool, page: dict) -> dict:
    name = p.get("name") or p.get("sponsor_name") or page.get("business_name") or "Sponsor"
    house = (page.get("config") or {}).get("house_ads") or {}
    pres_cfg = house.get("presenting") or {}
    return {
        "placement_id": p.get("id"), "sponsor_id": p.get("sponsor_id"), "position": position,
        "sold": sold, "name": name, "badge": (name or "S")[0].upper(),
        "logo": p.get("logo_url") or "", "image": p.get("image_url") or "",
        "alt": p.get("alt_text") or name, "headline": p.get("headline") or "",
        "body": p.get("body") or "", "cta": p.get("cta_label") or "", "url": p.get("url") or "",
        "animation": (p.get("animation") or "static") if sold else "static",
        "taglines": [t for t in (p.get("headline"), p.get("tagline_2"), p.get("tagline_3")) if t],
        "sub": (f"Presenting the {page.get('location_name') or 'cam'} Cam" if sold
                else pres_cfg.get("sub") or "Sponsorship available"),
        "eyebrow": "Presenting sponsor" if sold else (pres_cfg.get("eyebrow") or "Sponsorship available"),
    }


def _weighted_shuffle(items: list[dict], rng: random.Random) -> list[dict]:
    """Draw without replacement, each pick proportional to weight, so a
    weight-3 sponsor is first three times as often as a weight-1 -- and is
    still last sometimes, which is what 'fair over time' means."""
    pool, out = list(items), []
    while pool:
        total = sum(max(1, int(p.get("weight") or 1)) for p in pool)
        r = rng.uniform(0, total)
        acc = 0.0
        for p in pool:
            acc += max(1, int(p.get("weight") or 1))
            if r <= acc:
                out.append(p)
                pool.remove(p)
                break
        else:
            out.append(pool.pop())
    return out


def select_slots(page: dict, today: date | None = None, rng: random.Random | None = None,
                 placements: list[dict] | None = None) -> dict:
    """The presenting slot and four supporting slots, as the page renders
    them right now: sold and live first, house ads for the rest."""
    today = today or _today()
    rng = rng or random.Random()
    rows = placements if placements is not None else list_placements(page["id"], today)
    live = [p for p in rows if p["effective"] == "live"]
    sold_pres = sorted([p for p in live if p["position"] == "presenting" and not p["is_house"]],
                       key=lambda p: (p["sort_order"], p["id"] or 0))
    house_pres = sorted([p for p in live if p["position"] == "presenting" and p["is_house"]],
                        key=lambda p: (p["sort_order"], p["id"] or 0))
    if sold_pres:
        presenting = _slot(sold_pres[0], 0, True, page)
    elif house_pres:
        presenting = _slot(house_pres[0], 0, False, page)
    else:
        presenting = _slot(_config_house(page)["presenting"], 0, False, page)

    sold_sup = _weighted_shuffle([p for p in live if p["position"] == "supporting" and not p["is_house"]], rng)
    house_sup = sorted([p for p in live if p["position"] == "supporting" and p["is_house"]],
                       key=lambda p: (p["sort_order"], p["id"] or 0)) or _config_house(page)["supporting"]
    supporting = []
    for p in sold_sup[:SUPPORTING_SLOTS]:
        supporting.append(_slot(p, len(supporting) + 1, True, page))
    i = 0
    while len(supporting) < SUPPORTING_SLOTS and house_sup:
        supporting.append(_slot(house_sup[i % len(house_sup)], len(supporting) + 1, False, page))
        i += 1
    while len(supporting) < SUPPORTING_SLOTS:
        supporting.append(_slot({"name": "Sponsor this tile", "body": "Reach lake visitors while they plan the day.",
                                 "cta_label": "Get the rate card"}, len(supporting) + 1, False, page))
    return {"presenting": presenting, "supporting": supporting, "sold": presenting["sold"],
            "sold_supporting": sum(1 for t in supporting if t["sold"])}


def _config_house(page: dict) -> dict:
    """The page config's house ads, for a page provisioned before the
    house placements existed as rows."""
    cfg = page.get("config") or {}
    house = cfg.get("house_ads") or {}
    links = cfg.get("links") or {}
    pres = dict(house.get("presenting") or {})
    presenting = {"name": pres.get("name") or page.get("business_name") or "", "headline": pres.get("headline"),
                  "body": pres.get("body"), "cta_label": pres.get("cta"), "url": pres.get("url"), "is_house": True}
    supporting = [{"name": t.get("name"), "body": t.get("body"), "cta_label": t.get("cta"),
                   "url": t.get("url") or links.get(t.get("url_key") or "", ""), "is_house": True,
                   "sort_order": i} for i, t in enumerate(house.get("supporting") or [])]
    return {"presenting": presenting, "supporting": supporting}


# --------------------------------------------------------------- preview

def preview_token(page_id: int, position: str, placement_id: int | None = None) -> str:
    from hub.signing import timed_serializer
    return timed_serializer(PREVIEW_SALT).dumps({"page": int(page_id), "position": position,
                                                 "placement": int(placement_id) if placement_id else None})


def read_preview(token: str, page_id: int) -> dict | None:
    """The token's claim, or None when it is not a valid, unexpired token
    for this page."""
    from itsdangerous import BadSignature
    from hub.signing import timed_serializer
    try:
        claim = timed_serializer(PREVIEW_SALT).loads(token, max_age=PREVIEW_MAX_AGE)
    except BadSignature:
        return None
    if not isinstance(claim, dict) or claim.get("page") != int(page_id):
        return None
    if claim.get("position") not in POSITIONS:
        return None
    return claim


def decode_draft(text: str) -> dict:
    """The editor's unsaved fields, urlsafe-base64 JSON; only the copy
    fields are honored, so a draft cannot flip a slot to sold or move it."""
    if not text:
        return {}
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v)[:600] for k, v in data.items() if k in DRAFT_FIELDS and v is not None}


def apply_preview(slots: dict, page: dict, claim: dict, draft: dict) -> dict:
    """Force the previewed placement into its position, draft fields on
    top, so the editor's iframe shows the tile as it would run."""
    base = get_placement(claim["placement"]) if claim.get("placement") else None
    if base is None:
        base = {"position": claim["position"], "is_house": False, "name": "", "headline": "",
                "body": "", "cta_label": "", "url": "", "animation": "static", "effective": "live"}
    merged = {**base, **draft}
    if merged.get("animation") not in ANIMATIONS:
        merged["animation"] = "static"
    sold = not merged.get("is_house")
    if claim["position"] == "presenting":
        slots["presenting"] = _slot(merged, 0, sold, page)
        slots["sold"] = sold
    else:
        slots["supporting"][0] = _slot(merged, 1, sold, page)
        slots["sold_supporting"] = sum(1 for t in slots["supporting"] if t["sold"])
    slots["preview"] = True
    return slots


# ---------------------------------------------------------------- uploads

def store_creative(file_storage, *, kind: str, page: dict) -> str:
    """One uploaded image or logo, capped and converted through the shared
    pipeline, stored through the shared storage; returns its URL."""
    from hub.images import is_image, optimise
    from hub.storage import put
    filename = getattr(file_storage, "filename", "") or ""
    data = file_storage.read() if file_storage else b""
    if not data:
        return ""
    if not is_image(filename):
        raise ValueError(f"{filename or 'that file'} is not an image.")
    edge = 640 if kind == "logo" else 1600
    fmt = "PNG" if kind == "logo" else "WEBP"
    processed = optimise(data, max_edge=edge, fmt=fmt)
    stem = filename.rsplit(".", 1)[0][:60] or kind
    asset = put("camhub", f"{page['slug']}-{kind}-{stem}.{processed.fmt.lower()}", processed.data,
                client=page.get("client_name") or "", subpath=page["slug"])
    return asset.url
