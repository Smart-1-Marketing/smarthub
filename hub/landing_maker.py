"""Landing pages built from a proposal.

A proposal already contains everything a campaign landing page needs — the
client, the offer, the products being sold, the budget, the term. Building the
page from it means the ad, the page and the IO all say the same thing, which
is the failure the QA audit kept finding: a proposal promising one figure and
the campaign delivering another.

## What it reads, in order

  1. A saved proposal — the live builder's ``quotes`` row first, the retired
     tool's archive second. Both, because a rep may be working from either.
  2. The client's brand — logo, colours and fonts from Brandfetch, so the page
     looks like theirs rather than a template with their name on it.
  3. Their existing site — real services, phone number, hours, testimonials.
  4. AI, for the copy only.

Facts are never invented. The brief passed to the model is explicit that it
may not add reviews, awards, guarantees, prices or locations that aren't in
the material — those are the claims that get an agency into trouble, and they
are exactly what a language model will supply if you let it.

## Where a proposal actually lives

There is one Proposal Builder, and it keeps quotes in the ``quotes`` table,
not in ``modules.proposal_builder.store`` — that module is the retired tool's
read-only archive and answers 404 to every API path. Reading only the archive
is how this would have shipped with a proposal picker that was empty for every
proposal written since the consolidation, while looking like it worked.

## What this module is not, yet

The full spec includes a live editor, version compare, three design directions
to choose between, and PNG/PDF export. This builds the page and everything
around it — generation, storage, client attachment, search. The editor is the
next phase; `page_html` is stored so an editor can load and re-save it without
regenerating.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from hub import jsonstore

STORE_NAME = "landing_pages.json"

# Three directions, as the brief requires. They differ in composition and
# weight, not just colour — a palette swap isn't a design choice.
DIRECTIONS = {
    "bold": {
        "label": "Bold and promotional",
        "note": "Large type, high-contrast hero, offer front and center. "
                "Suits a dated deadline or a discount.",
        "hero": "solid", "radius": "10px", "weight": "800",
        "hero_pad": "72px 0 64px", "accent_use": "heavy",
    },
    "trust": {
        "label": "Clean and trustworthy",
        "note": "Calm layout, proof close to the top, restrained color. "
                "Suits legal, medical, financial and home services.",
        "hero": "light", "radius": "12px", "weight": "700",
        "hero_pad": "60px 0 52px", "accent_use": "light",
    },
    "premium": {
        "label": "Premium and image-led",
        "note": "Photography carries the page, type stays quiet. Suits "
                "hospitality, tourism, boats, RVs and anything aspirational.",
        "hero": "image", "radius": "3px", "weight": "600",
        "hero_pad": "104px 0 92px", "accent_use": "minimal",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> str:
    """The store, under the real data directory.

    ``jsonstore.data_dir()`` rather than a local ``/var/data`` probe: a
    finished landing page is client work with no other copy, and the disk it
    would otherwise sit on is not part of any backup.
    """
    return os.path.join(jsonstore.data_dir(), STORE_NAME)


def _load() -> list[dict]:
    return jsonstore.read_json(_path(), default=[]) or []


def _save(rows: list[dict]) -> None:
    jsonstore.write_json(_path(), rows)


def _slug(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-") or "page"


# ---------------------------------------------------------------------------
# Gathering the material
# ---------------------------------------------------------------------------

def _spec_from_quote(proposal_id: str) -> tuple[dict | None, str, str]:
    """A spec from the live builder's quote, if that id is one.

    Returns ``(spec, client, source)``. The quote's own columns are the
    campaign as it was actually sold, so they win over anything inferred:
    ``products_summary`` is the line-up on the proposal the client received.
    """
    if not str(proposal_id).isdigit():
        return None, "", ""
    try:
        from modules.sales_builder.app import Quote, SessionLocal
    except Exception:                                   # noqa: BLE001
        return None, "", ""
    db = None
    try:
        db = SessionLocal()
        q = db.query(Quote).filter(Quote.id == int(proposal_id)).first()
        if not q:
            return None, "", ""
        try:
            state = json.loads(q.data or "{}")
        except ValueError:
            state = {}
        products = [p.strip() for p in str(q.products_summary or "").split(",")
                    if p.strip()]
        spec = {
            "client": q.client or "", "website": q.website or "",
            "industry": q.industry or "",
            "monthly_total": q.monthly_budget or 0,
            "campaign": state.get("campaign") or q.package or "",
            "objectives": q.goals_summary or "",
            "geo": q.geo_summary or "",
            "items": [{"product": p} for p in products],
            "city": state.get("city", ""), "state": state.get("state", ""),
            "contact_phone": state.get("contact_phone", ""),
            "audience": state.get("audience", ""),
            "start": state.get("start", ""), "end": state.get("end", ""),
        }
        return spec, q.client or "", f"proposal {q.quote_number or proposal_id}"
    except Exception:                                   # noqa: BLE001
        return None, "", ""
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:                           # noqa: BLE001
                pass


# Both kinds of proposal on one client. Moved to hub/proposals.py, which is
# where "the proposals we hold for a client" belongs now that the IO Builder
# asks the same question as the landing maker. Re-exported rather than
# duplicated: two copies of this drift, and the drift is invisible because
# each caller sees a plausible list.
from hub.proposals import proposals_for            # noqa: F401,E402

def brief_from_proposal(proposal_id: str = "", client: str = "",
                        text: str = "", uploaded_id: str = "",
                        website: str = "", kind: str = "client") -> dict:
    """Everything known about the campaign, before any copy is written.

    ``kind="prospect"`` is a business we do not have a record for: a sample
    page built to show them what we would do. There is no client record, no
    proposal and no brand on file, so the website is doing all the work --
    which is why it is required for a prospect and optional for a client.
    """
    spec, source = None, ""

    # A proposal written elsewhere and uploaded onto the client record. Read
    # from the record's own stored URL rather than one the caller supplies --
    # a caller-supplied URL here would be an SSRF hole.
    if uploaded_id and client and not text:
        try:
            from hub import _proposal_text_for
            text = _proposal_text_for(client, uploaded_id) or ""
        except Exception:                               # noqa: BLE001
            text = ""
        if not text:
            return {"missing": ["a readable file on that uploaded proposal"],
                    "client": client, "thin": True}

    if proposal_id:
        spec, found_client, source = _spec_from_quote(proposal_id)
        if spec:
            client = client or found_client
    if not spec and proposal_id:
        # The retired builder's archive. Still real proposals real clients
        # received, so a page can still be built from one.
        try:
            from modules.proposal_builder import store as pstore
            rec = pstore.get_proposal(proposal_id) or {}
            spec = rec.get("spec")
            client = client or (rec.get("customer") or {}).get("business_name", "")
            if spec:
                source = f"archived proposal {proposal_id}"
        except Exception:                               # noqa: BLE001
            pass
    if not spec and text:
        from hub.campaign_spec import from_proposal_text
        spec = from_proposal_text(text, client).to_dict()
        source = (f"uploaded proposal {uploaded_id}" if uploaded_id
                  else "uploaded proposal")
    # Not for a prospect: there is no record to read, and a same-named client
    # in the registry is a different business, not this one.
    if not spec and client and kind != "prospect":
        from hub.campaign_spec import from_client
        spec = from_client(client).to_dict()
        source = "client record"

    if kind == "prospect":
        # Nothing to look up. Say so, rather than reporting "client record"
        # as the source for a business that has no record.
        spec, source = spec or {}, source or "prospect website"

    spec = spec or {}
    brief = {
        "kind": kind,
        "client": spec.get("client") or client,
        "website": website or spec.get("website", ""),
        "industry": spec.get("industry", ""),
        "city": spec.get("city", ""), "state": spec.get("state", ""),
        "phone": spec.get("contact_phone", ""),
        "products": [i.get("product") for i in (spec.get("items") or [])
                     if i.get("product")],
        "monthly": spec.get("monthly_total", 0),
        "campaign": spec.get("campaign", ""),
        "objectives": spec.get("objectives", ""),
        "audience": spec.get("audience", ""),
        "geo": spec.get("geo", ""),
        "start": spec.get("start", ""), "end": spec.get("end", ""),
        "source": source,
        "proposal_id": proposal_id,
    }

    # Anything the client record knows that the proposal didn't carry. A quote
    # row has no address or phone on it, and those are what a landing page
    # header is made of.
    if (kind != "prospect" and brief["client"]
            and not (brief["phone"] and brief["city"])):
        try:
            from hub.client_context import context
            f = context(brief["client"]).get("fields", {}) or {}
            brief["phone"] = brief["phone"] or f.get("phone", "")
            brief["city"] = brief["city"] or f.get("city", "")
            brief["state"] = brief["state"] or f.get("state", "")
            brief["website"] = brief["website"] or f.get("website", "")
            brief["industry"] = brief["industry"] or f.get("industry", "")
        except Exception:                               # noqa: BLE001
            pass

    # The brand, so the page looks like theirs.
    try:
        from hub.client_brand import brand_kit
        kit = brand_kit(brief["client"], brief.get("website") or "") or {}
        brief["logo"] = next((l.get("url") for l in (kit.get("logos") or [])
                              if l.get("url")), "")
        brief["colors"] = [c.get("hex") for c in (kit.get("colors") or [])
                           if c.get("hex")][:4]
        brief["fonts"] = [f.get("name") for f in (kit.get("fonts") or [])
                          if f.get("name")][:2]
        brief["description"] = kit.get("description", "")
    except Exception:                                   # noqa: BLE001
        brief.update({"logo": "", "colors": [], "fonts": [], "description": ""})

    # Anything a scan already read off their site — real services, real hours.
    if not brief["phone"]:
        try:
            from modules.scans.app import latest_payload_for_domain
            payload = latest_payload_for_domain(brief["website"] or brief["client"])
            blob = json.dumps(payload or {})[:200000]
            m = re.search(r'"(?:phone|telephone)"\s*:\s*"([^"]{7,20})"', blob)
            brief["phone"] = m.group(1) if m else ""
        except Exception:                               # noqa: BLE001
            pass

    # Only the client is genuinely required. Products make a better page —
    # they're what the campaign is selling — but "no proposal yet" is a
    # supported starting point, so refusing outright would block the case
    # this was asked for. The gap is reported rather than silently accepted.
    missing = []
    if not brief.get("client"):
        missing.append("a name")
    if kind == "prospect" and not brief.get("website"):
        # Without it there is nothing to build a sample page from but the
        # name, and a page written from a name alone is a guess.
        missing.append("their website address")
    brief["missing"] = missing
    brief["thin"] = not brief.get("products")
    return brief


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

SYSTEM = (
    "You write landing page copy for a marketing agency's client. You are "
    "given only what is actually known about the business.\n\n"
    "Hard rules, because breaking them is what gets an agency sued:\n"
    "- Never invent reviews, testimonials, awards, accreditations, "
    "guarantees, prices, discounts, locations, staff or years in business.\n"
    "- Never write a claim the material doesn't support.\n"
    "- If you have nothing for a section, return an empty string for it and "
    "it will be left out. An honest gap beats an invented fact.\n\n"
    "Style: write to the customer, not the business owner. Specific, "
    "benefit-led, plain. No 'Welcome', no 'Experience Excellence', no "
    "'Solutions for Your Needs'. Short paragraphs. One clear action."
)


def write_copy(brief: dict, goal: str, offer: str,
               promoting: str = "") -> dict:
    """Ask for the copy. Falls back to something usable, never to lorem.

    What the writer is given comes from ``hub/landing_spec.py`` rather than
    being assembled here, so the module, the prompt and the renderer are
    reading one description of what this page is for.
    """
    from hub import landing_spec as spec
    g = spec.goal(goal)
    payload = spec.copy_brief(brief, goal, offer, promoting)
    offer_used = payload["offer_state"] == spec.READ

    fallback = {
        "headline": (f"{payload['promoting'] or brief.get('industry') or 'Local'}"
                     f" in {brief.get('city') or brief.get('geo') or 'your area'}"),
        # Only a real offer reaches the subhead. An unclear one used to land
        # here verbatim, so a page could open with "Special offer available"
        # and never say what it was.
        "subhead": (offer.strip() if offer_used else
                    "Tell us what you need and we'll come back to you today."),
        "cta": g["cta"],
        "benefits": [], "how_it_works": [], "faqs": [], "why_us": [],
        "goal_id": g["id"], "offer_state": payload["offer_state"],
        "source": "fallback",
    }
    try:
        from hub import ai
    except Exception:                                   # noqa: BLE001
        return fallback

    try:
        raw = ai.chat(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content":
              "Return JSON with: headline (under 12 words, benefit-led), "
              "subhead (one sentence), cta (3-4 words, specific), "
              "benefits (3-5 {title, text}), how_it_works (3 {step, text}), "
              "why_us (3-4 short strings), faqs (3-4 {q, a}).\n\n"
              "Write for ONE next step: " + payload["conversion_goal"]
              + ". " + payload["goal_guidance"]
              + " Before asking, the page must establish "
              + payload["must_establish"] + ".\n"
              + payload["offer_guidance"] + "\n\n"
              + json.dumps(payload)}],
            module="landing_maker", purpose="page_copy",
            json_mode=True, max_tokens=1600, temperature=0.6)
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M))
        if not isinstance(data, dict):
            return fallback
        data["source"] = "ai"
        data["goal_id"] = g["id"]
        data["offer_state"] = payload["offer_state"]
        for key in ("headline", "subhead", "cta"):
            if not str(data.get(key) or "").strip():
                data[key] = fallback[key]
        return data
    except Exception:                                   # noqa: BLE001
        return fallback


# ---------------------------------------------------------------------------
# Create / store
# ---------------------------------------------------------------------------

def create(proposal_id: str = "", client: str = "", text: str = "",
           direction: str = "trust", goal: str = "", offer: str = "",
           actor: str = "", uploaded_id: str = "", kind: str = "client",
           website: str = "", promoting: str = "") -> dict:
    kind = "prospect" if kind == "prospect" else "client"
    brief = brief_from_proposal(proposal_id, client, text, uploaded_id,
                                website, kind)
    if brief["missing"]:
        return {"error": "Still needed: " + ", ".join(brief["missing"])}
    from hub import landing_spec as spec
    goal_id = spec.goal(goal)["id"]
    promoting = spec.promoting_from(brief, promoting)
    copy = write_copy(brief, goal, offer, promoting)
    from .landing_render import render_page, with_endpoint
    from .landing_images import pick

    page_id = uuid.uuid4().hex[:12]
    pics = pick(brief, benefits=len([b for b in (copy.get("benefits") or [])
                                     if isinstance(b, dict) and b.get("title")]))
    html = render_page(brief, copy, DIRECTIONS.get(direction, DIRECTIONS["trust"]),
                       pics, goal_id=goal_id)
    # Absolute, so the form still reaches us from wherever the page is pasted.
    from hub.config import settings
    base = settings.public_base_url
    html = with_endpoint(html, f"{base}/api/leads/capture" if base
                         else "/api/leads/capture")
    row = {
        "id": page_id,
        "slug": f"{_slug(brief['client'])}-{page_id[:6]}",
        "client": brief["client"],
        "kind": kind,
        "website": brief.get("website", ""),
        "images": {"available": pics.get("available", False),
                   "source": pics.get("source", ""),
                   "cards": len(pics.get("cards") or [])},
        "proposal_id": proposal_id or uploaded_id,
        "proposal_kind": ("saved" if proposal_id else
                          "uploaded" if uploaded_id else ""),
        "campaign": brief.get("campaign") or offer or goal,
        "direction": direction,
        "goal": goal_id, "goal_label": spec.goal(goal)["label"],
        "offer": offer, "offer_state": spec.offer_state(offer)[0],
        "promoting": promoting,
        "headline": copy.get("headline", ""),
        "copy_source": copy.get("source", ""),
        "brief": brief, "copy": copy,
        "page_html": html,
        "created": _now(), "by": actor,
        "versions": [],
    }
    rows = _load()
    rows.insert(0, row)
    _save(rows)
    try:
        from hub import audit
        audit.log("landing_maker", "created", actor=actor or None,
                  client=brief["client"],
                  proposal=proposal_id or uploaded_id or None,
                  direction=direction)
    except Exception:                                   # noqa: BLE001
        pass
    note = ("Copy written by AI from the proposal and the client's own site — "
            "check the facts before it goes live."
            if copy.get("source") == "ai" else
            "AI wasn't available, so the copy is a starting point rather than "
            "finished.")
    if brief.get("thin"):
        note += (" No proposal was attached, so the page has no products on "
                 "it — build it from a proposal to get those.")
    if not pics.get("available"):
        note += (" No image provider is configured, so the page uses a color "
                 "hero rather than photography — set PEXELS_API or "
                 "PIXABAY_API and rebuild for a richer page.")
    if not base:
        # Otherwise the form posts to whatever domain the page is pasted onto.
        note += (" PUBLIC_BASE_URL isn't set, so the lead form uses a relative "
                 "URL and will only work while the page is served from the Hub.")
    # What the page could not answer for itself. Asked rather than written
    # around: copy that works around a gap is copy that could be about any
    # business in the industry.
    questions = spec.open_questions(brief, goal, offer, promoting)
    state, offer_note = spec.offer_state(offer)
    if state != spec.READ:
        note += " " + offer_note

    return {"ok": True, "id": page_id, "slug": row["slug"],
            "kind": kind, "images": row["images"],
            "goal": goal_id, "goal_label": spec.goal(goal)["label"],
            "promoting": promoting, "offer_state": state,
            "questions": questions,
            "preview": f"/sales/landing/p/{row['slug']}",
            "copy_source": copy.get("source"),
            "thin": brief.get("thin", False),
            "note": note}


def get(id_or_slug: str) -> dict | None:
    want = (id_or_slug or "").strip().lower()
    for r in _load():
        if r.get("id") == want or r.get("slug") == want:
            return r
    return None


def update_html(id_or_slug: str, html: str, actor: str = "") -> dict:
    """Save an edited page, keeping the previous version."""
    rows = _load()
    for r in rows:
        if r.get("id") == id_or_slug or r.get("slug") == id_or_slug:
            r.setdefault("versions", []).append(
                {"html": r.get("page_html", ""), "saved": r.get("created"),
                 "by": r.get("by", "")})
            r["versions"] = r["versions"][-10:]
            r["page_html"] = html
            r["updated"] = _now()
            r["updated_by"] = actor
            _save(rows)
            return {"ok": True, "versions": len(r["versions"])}
    return {"error": "No such landing page."}


REVISE_SYSTEM = (
    "You are revising landing page copy that is already live. You are given "
    "the current copy as JSON and an instruction from the person who owns "
    "the page.\n\n"
    "Change what the instruction asks for and leave everything else exactly "
    "as it is. Return the SAME JSON shape with the same keys.\n\n"
    "The rules the original copy was written under still hold, and an "
    "instruction does not lift them: never invent reviews, testimonials, "
    "awards, guarantees, prices, discounts, locations, staff or years in "
    "business, and never write a claim the material does not support. If the "
    "instruction asks for something you have no basis for, leave that part "
    "alone rather than inventing it."
)


def revise(id_or_slug: str, instructions: str, actor: str = "") -> dict:
    """Rewrite a built page against an instruction, keeping the old version.

    The directives the copy was first written under are restated here rather
    than assumed. "Make it punchier" is exactly the kind of instruction that
    walks a model past a prohibition it was given once, several requests ago
    -- the Proposal Builder learned that when its AI rewrite had no
    directives attached at all.
    """
    instructions = str(instructions or "").strip()
    if not instructions:
        return {"error": "Say what you would like changed."}

    row = get(id_or_slug)
    if not row:
        return {"error": "No such landing page."}

    brief = row.get("brief") or {}
    copy = dict(row.get("copy") or {})
    try:
        from hub import ai
        raw = ai.chat(
            [{"role": "system", "content": REVISE_SYSTEM},
             {"role": "user", "content":
              "Current copy:\n" + json.dumps(copy) +
              "\n\nWhat is known about the business:\n" + json.dumps({
                  k: brief.get(k) for k in
                  ("client", "industry", "geo", "city", "products",
                   "description")}) +
              "\n\nInstruction:\n" + instructions}],
            module="landing_maker", purpose="page_revision",
            json_mode=True, max_tokens=1600, temperature=0.5)
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except Exception:                                       # noqa: BLE001
        return {"error": "The rewrite did not come back. Nothing was changed."}

    # Only keys that were already there, so a stray key cannot reshape the
    # page, and a section the model dropped keeps its existing copy.
    for key in list(copy):
        if key in data and data[key] not in (None, ""):
            copy[key] = data[key]
    copy["source"] = "ai"

    from .landing_render import render_page, with_endpoint
    from .landing_images import pick
    pics = pick(brief, benefits=len([b for b in (copy.get("benefits") or [])
                                     if isinstance(b, dict) and b.get("title")]))
    html = render_page(brief, copy,
                       DIRECTIONS.get(row.get("direction"), DIRECTIONS["trust"]),
                       pics)
    from hub.config import settings
    base = settings.public_base_url
    html = with_endpoint(html, f"{base}/api/leads/capture" if base
                         else "/api/leads/capture")

    rows = _load()
    for r in rows:
        if r.get("id") == row["id"]:
            r.setdefault("versions", []).append(
                {"html": r.get("page_html", ""), "saved": r.get("updated") or r.get("created"),
                 "by": r.get("updated_by") or r.get("by", ""),
                 "why": r.get("last_instruction", "")})
            r["versions"] = r["versions"][-10:]
            r["page_html"] = html
            r["copy"] = copy
            r["headline"] = copy.get("headline", r.get("headline", ""))
            r["updated"] = _now()
            r["updated_by"] = actor
            r["last_instruction"] = instructions[:400]
            _save(rows)
            break

    try:
        from hub import audit
        audit.log("landing_maker", "revised", actor=actor or None,
                  client=row.get("client"), page=row.get("slug"))
    except Exception:                                       # noqa: BLE001
        pass
    return {"ok": True, "slug": row["slug"],
            "preview": f"/sales/landing/p/{row['slug']}",
            "versions": len(get(row["id"]).get("versions") or []),
            "note": "Rewritten. The previous version is kept."}


def remove(id_or_slug: str, actor: str = "") -> dict:
    """Delete a built page.

    The store is one mirrored JSON file, so this is a write of the remaining
    rows rather than a file removal -- deleting the file would leave the
    database copy to be restored on the next read, which is the one way the
    backup can bite you.
    """
    rows = _load()
    keep = [r for r in rows
            if r.get("id") != id_or_slug and r.get("slug") != id_or_slug]
    if len(keep) == len(rows):
        return {"error": "No such landing page."}
    gone = next(r for r in rows
                if r.get("id") == id_or_slug or r.get("slug") == id_or_slug)
    _save(keep)
    try:
        from hub import audit
        audit.log("landing_maker", "deleted", actor=actor or None,
                  client=gone.get("client"), page=gone.get("slug"))
    except Exception:                                       # noqa: BLE001
        pass
    return {"ok": True, "deleted": gone.get("slug", "")}


def listing(client: str = "", q: str = "") -> dict:
    """Every landing page, searchable."""
    rows = all_rows = _load()
    if client:
        want = re.sub(r"[^a-z0-9]+", "", client.lower())
        rows = [r for r in rows
                if re.sub(r"[^a-z0-9]+", "", str(r.get("client") or "").lower()) == want]
    if q:
        t = q.lower()
        rows = [r for r in rows if t in json.dumps(
            {k: r.get(k) for k in ("client", "campaign", "headline", "goal",
                                   "offer", "slug")}).lower()]
    return {
        "pages": [{k: r.get(k) for k in
                   ("id", "slug", "client", "campaign", "headline", "direction",
                    "created", "by", "proposal_id", "updated", "kind",
                    "website", "images")}
                  for r in rows[:300]],
        "count": len(rows),
        "clients": sorted({r.get("client") for r in all_rows if r.get("client")}),
        "directions": {k: v["label"] for k, v in DIRECTIONS.items()},
    }


def for_client(client: str) -> list[dict]:
    """Used by the Client 360 / proposals card."""
    return listing(client=client)["pages"]
