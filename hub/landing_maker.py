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

## The copywriting doctrine, and what a page is built from

`SYSTEM` folds in the web team's direct-response spec: message match (the
headline confirms the visitor is in the right place), pain before relief
before proof, specific claims over vague ones, real urgency only, and proof
ordered to answer the biggest doubt first. `hub/landing_render.py`'s own
notes cover the rest of that spec — the fixed page architecture, one CTA
never diluted, accessible focus states — since those live in the renderer
rather than the prompt.

Two more inputs follow the same never-invented rule as the offer: a rep can
paste in real Google reviews and a client's own GA4 measurement id when
building the page. Neither is guessed at, and neither blocks a build when
it's missing — the page ships without a social-proof section or a tracking
script rather than with a fabricated review or a made-up id, and the build
response says so.

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

    # The service area a PERSON confirmed, and nothing else.
    #
    # `geo` is the proposal's own geo_summary -- a media-buying targeting
    # string like "Carmel, IN + 10-mile radius / Indianapolis DMA / +3 more".
    # The renderer printed it above the fold as "Serving ...", so the first
    # proof a visitor read on a page carrying the client's name was an
    # internal buying instruction, with no field anywhere for a rep to
    # correct it. It stays on the brief because the copy writer is allowed
    # to know where the media runs; it is never what the page prints.
    #
    # A prospect is asked nothing and has no record, so there is nothing
    # confirmed to read -- and the fallback is the client's own city, never
    # the targeting string.
    brief["service_area"] = ""
    if kind != "prospect" and brief["client"]:
        try:
            from hub.schema_questions import confirmed_answer
            brief["service_area"] = confirmed_answer(brief["client"],
                                                     "service_area")
        except Exception:                               # noqa: BLE001
            pass
    if not brief["service_area"]:
        brief["service_area"] = ", ".join(
            p for p in (brief.get("city"), brief.get("state")) if p)

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
        # Whether that logo is the one a rep confirmed on the client record or
        # whatever the lookup happened to rank first. `brand_kit()` already
        # promotes a confirmed pick to `logos[0]`, so the right mark is
        # already being used -- what was missing is any way to tell the two
        # apart before the page goes onto a client's own domain, and nobody
        # proof-reads the thing they recognise.
        brief["logo_confirmed"] = bool(
            (kit.get("template") or {}).get("logo_url"))
    except Exception:                                   # noqa: BLE001
        brief.update({"logo": "", "colors": [], "fonts": [], "description": "",
                      "logo_confirmed": False})

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
    "You are a direct-response conversion strategist writing landing page "
    "copy for a marketing agency's client. Your one job is to turn ad "
    "traffic into a phone call or a form submission, right now, on a phone "
    "screen. You are given only what is actually known about the business.\n\n"
    "Hard rules, because breaking them is what gets an agency sued or "
    "embarrassed, and it always gets caught:\n"
    "- Never invent reviews, testimonials, awards, accreditations, "
    "guarantees, prices, discounts, locations, staff or years in business.\n"
    "- Never write a claim the material doesn't support.\n"
    "- Never manufacture urgency — no countdown language, no fake scarcity, "
    "no deadline that isn't in the material. A real deadline (a genuine "
    "expiry, 24-hour service) is fine to state plainly.\n"
    "- If you have nothing for a section, return an empty string for it and "
    "it will be left out. An honest gap beats an invented fact.\n\n"
    "Copywriting doctrine:\n"
    "- Message match: the headline must instantly confirm to the visitor "
    "that this page is about the thing they were promised, not a general "
    "pitch for the business.\n"
    "- Pain, then relief, then proof: name the visitor's actual problem in "
    "their own words before you say you fix it, and only THEN back that "
    "with proof. Never lead with a credential — nobody trusts a stranger's "
    "credentials before the stranger has shown they understand the problem.\n"
    "- Specificity beats cleverness: a named number or a named service beats "
    "a vague claim ('24-hour emergency service' beats 'here when you need "
    "us'). Be as concrete as the material allows.\n"
    "- Order proof to answer the biggest doubt first — the objection the "
    "visitor is most likely silently having ('are these people legit', "
    "'have they done this before') gets answered soonest.\n\n"
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

    # What the Hub already knows about this client, assembled once by
    # hub/client_brief.py -- the Google rating and review count with a date
    # and a source on each, the products, the location, the voice, and an
    # explicit "not on file, and not to be invented" list. Every other AI
    # landing surface in the building passes it and this one passed nothing,
    # so the "why choose us" section was written with no proof to answer a
    # stranger's doubt with.
    #
    # Only for a client. A prospect has no record here, and a same-named
    # client in the register is a DIFFERENT business -- printing their rating
    # on a stranger's page is the one mistake in this corner that cannot be
    # taken back. The typed form fields are all a prospect page gets, which
    # is what it had before.
    known = ""
    if brief.get("kind") != "prospect" and brief.get("client"):
        try:
            from hub import client_brief
            known = client_brief.for_prompt(
                brief["client"], brief.get("website") or "",
                heading="What we already hold about this client. Use it as "
                        "proof; never state anything it does not.")
        except Exception:                               # noqa: BLE001
            known = ""

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
              "Return JSON with: headline (under 12 words — it must echo "
              "what this page is promoting, so a visitor who just saw an ad "
              "for it feels instantly confirmed they're in the right "
              "place), subhead (one sentence), cta (3-4 words, specific), "
              "benefits (3-5 {title, text} — name the visitor's specific "
              "problem or need first, in their own words, not a generic "
              "category), how_it_works (3 {step, text}), "
              "why_us (3-4 short strings, ordered so the biggest reason to "
              "doubt a stranger is answered first), faqs (3-4 {q, a}).\n\n"
              "Write for ONE next step: " + payload["conversion_goal"]
              + ". " + payload["goal_guidance"]
              + " Before asking, the page must establish "
              + payload["must_establish"] + ".\n"
              + payload["offer_guidance"] + "\n\n"
              + json.dumps(payload)
              + (("\n\n" + known) if known else "")}],
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
# Real reviews, real tracking -- read, never invented
# ---------------------------------------------------------------------------

def _parse_reviews(raw: str) -> tuple[list[dict], list[str]]:
    """A rep's own pasted reviews, one per line, and what was refused.

    ``Author | rating | quote`` -- the author and the rating are both
    optional, so a bare pasted quote still comes through as a review with no
    attribution rather than being dropped. Nothing here writes review text;
    it only reads what a person typed, the trust the offer field already
    gets. Capped at two, which is what the page has room for.

    Two things it will not do, because both publish a claim nobody made
    about a client's own reputation:

    **A rating is a whole 1-5 or it is not a rating.** The old reading was
    ``re.sub(r"[^0-9]", "", rating_raw)``, so a genuine ``4.5`` became the
    integer 45 and the renderer clamped it to five filled stars -- a
    five-star claim typed by nobody, on the one input this tool tells reps
    is never invented. Anything else is refused **by name** and the review
    still runs without stars, rather than being silently shown as zero: a
    rep who watches the stars vanish types ``5`` to get them back, which
    re-enters the false claim by hand.

    **No name is no attribution.** An unattributed quote used to be
    captioned "Google review" by the renderer -- a source nobody supplied.
    It is published as a quote with no byline instead.
    """
    out: list[dict] = []
    refused: list[str] = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) == 3:
            author, rating_raw, quote = parts
        elif len(parts) == 2:
            author, quote = parts
            rating_raw = ""
        else:
            author, rating_raw, quote = "", "", parts[0]
        if not quote:
            continue
        row = {"quote": quote, "author": author}
        if rating_raw:
            if re.fullmatch(r"[1-5]", rating_raw):
                row["rating"] = int(rating_raw)
            else:
                refused.append(rating_raw)
        out.append(row)
        if len(out) >= 2:
            break
    return out, refused


# ---------------------------------------------------------------------------
# Create / store
# ---------------------------------------------------------------------------

def create(proposal_id: str = "", client: str = "", text: str = "",
           direction: str = "trust", goal: str = "", offer: str = "",
           actor: str = "", uploaded_id: str = "", kind: str = "client",
           website: str = "", promoting: str = "", reviews: str = "",
           ga4_id: str = "") -> dict:
    kind = "prospect" if kind == "prospect" else "client"
    brief = brief_from_proposal(proposal_id, client, text, uploaded_id,
                                website, kind)
    if brief["missing"]:
        return {"error": "Still needed: " + ", ".join(brief["missing"])}
    from hub import landing_spec as spec
    goal_id = spec.goal(goal)["id"]
    promoting = spec.promoting_from(brief, promoting)
    copy = write_copy(brief, goal, offer, promoting)
    from .landing_render import render_page, with_endpoint, is_valid_ga4_id
    from .landing_images import pick

    page_id = uuid.uuid4().hex[:12]
    # Minted before the render rather than after it: the slug is what the
    # form posts as `page`, so a lead can name which of a client's pages
    # produced it. Built once here and stored on the row below.
    slug = f"{_slug(brief['client'])}-{page_id[:6]}"
    review_rows, refused_ratings = _parse_reviews(reviews)
    ga4_id = ga4_id.strip()
    offer_state, _offer_note = spec.offer_state(offer)
    pics = pick(brief, benefits=len([b for b in (copy.get("benefits") or [])
                                     if isinstance(b, dict) and b.get("title")]))
    html = render_page(brief, copy, DIRECTIONS.get(direction, DIRECTIONS["trust"]),
                       pics, goal_id=goal_id, reviews=review_rows, ga4_id=ga4_id,
                       slug=slug, offer=offer,
                       offer_usable=offer_state == spec.READ)
    # Absolute, so the form still reaches us from wherever the page is pasted.
    _lead_ep, _view_ep = _endpoints(slug)
    html = with_endpoint(html, _lead_ep, _view_ep)
    from hub.config import settings
    base = settings.public_base_url
    row = {
        "id": page_id,
        "slug": slug,
        "client": brief["client"],
        "kind": kind,
        "website": brief.get("website", ""),
        "images": {"available": pics.get("available", False),
                   "source": pics.get("source", ""),
                   "cards": len(pics.get("cards") or [])},
        # The set itself, not just a count of it. Without this a rewrite
        # re-ran two live provider searches and quietly replaced the
        # photographs on a page already running -- see _picks_for_revision().
        "picks": _keep_picks(pics),
        "proposal_id": proposal_id or uploaded_id,
        "proposal_kind": ("saved" if proposal_id else
                          "uploaded" if uploaded_id else ""),
        "campaign": brief.get("campaign") or offer or goal,
        "direction": direction,
        "goal": goal_id, "goal_label": spec.goal(goal)["label"],
        "offer": offer, "offer_state": offer_state,
        "promoting": promoting,
        "headline": copy.get("headline", ""),
        "copy_source": copy.get("source", ""),
        "brief": brief, "copy": copy,
        "reviews": review_rows, "ga4_id": ga4_id,
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
    if not review_rows:
        note += (" No reviews were given, so the page has no social-proof "
                 "section — paste in 1-2 real Google reviews and rebuild if "
                 "you have them. The page will never invent one.")
        # And the audit has usually already counted them. An Insites scan
        # reads the client's Google listing, so "they have 127 reviews at 4.8"
        # is measured, paid for and was being read by nobody here -- while the
        # sentence above asked a rep to go and find reviews by hand.
        #
        # It is said to the *rep*, and the page still prints only what a
        # person pasted in. A star rating rendered onto a client's own domain
        # off a crawl nobody re-checks is the invented-proof failure this
        # module exists to refuse, one source further out: the reading ages,
        # the page does not.
        note += _google_standing_note(brief.get("website") or brief["client"])
    if refused_ratings:
        # Named rather than silently shown as no stars: a rep who watches
        # the stars vanish types "5" to get them back, which re-enters by
        # hand the false claim this refusal exists to stop.
        note += (" A star rating has to be a whole number 1-5; "
                 + ", ".join(f"\u201c{r}\u201d" for r in refused_ratings[:2])
                 + " couldn't be read, so that review is on the page with no "
                   "stars. Fix the rating and rebuild rather than rounding "
                   "it — the page will never round one up for you.")
    if brief.get("logo") and not brief.get("logo_confirmed"):
        note += (" The logo on the page is the best one the brand lookup "
                 "offered, not one anybody has confirmed — check it against "
                 "their own site before you send the link, and confirm it on "
                 "their client record so every tool uses the same mark.")
    if not brief.get("service_area"):
        note += (" No service area is confirmed for this client, so the page "
                 "doesn't say where they work. Answer \u201cwhich towns, "
                 "counties or radius does the business serve?\u201d on their "
                 "SEO record and rebuild.")
    if ga4_id and not is_valid_ga4_id(ga4_id):
        note += (" That GA4 ID didn't look like a real measurement ID "
                 "(G-XXXXXXX), so no tracking script was added.")
    elif not ga4_id:
        note += (" No GA4 measurement ID was given, so the page has no "
                 "phone-click or form-submit tracking beyond the lead "
                 "already logged in the Hub.")
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
            "url": page_url(row["slug"]),
            "copy_source": copy.get("source"),
            "thin": brief.get("thin", False),
            "note": note}



# What `pick()` chose, kept on the row so a rewrite does not choose again.
_PICK_KEYS = ("hero", "cards", "band", "credits", "source", "available")


def _keep_picks(pics: dict) -> dict:
    """The picture set, small enough to store beside the page.

    `not_theirs` is deliberately left out: it is a count of what a *search*
    rejected on the day it ran, which is a fact about that search rather than
    about this page, and carrying it forward would make it read as a finding
    about a set nobody re-searched.
    """
    return {k: pics.get(k) for k in _PICK_KEYS}


def _picks_for_revision(row: dict, brief: dict, benefits: int) -> tuple[dict, str]:
    """The pictures a rewrite should use, and a sentence if they changed.

    `revise()` re-ran `pick()` on every rewrite -- which is two live provider
    searches and a fetch of the client's own site, answered differently on
    different days. So "make the headline shorter" silently **replaced the
    photographs** on a page already taking paid traffic: the hero a rep chose
    the page for, gone, with the response saying only "Rewritten." Nothing
    errored at either end, and the rep would find out by looking.

    So the set is stored at build and reused. It is re-picked in exactly one
    case -- the rewrite changed how many benefit cards the page draws, so the
    stored row cannot fill it, and `pick()`'s own all-or-nothing rule would
    otherwise leave a row of empty cards. That is said out loud rather than
    done quietly, because it is the one rewrite that does change the pictures.
    """
    # Whether the set was STORED, not whether it has anything in it. A page
    # built with no image provider configured -- which `create()`'s own note
    # calls the default state of a fresh deployment -- keeps an empty set
    # perfectly deliberately, and reading that as an old row sent every one of
    # those pages back through two live searches on every rewrite while
    # telling the rep the page predated a feature it was built under.
    #
    # Reusing an empty set is the right answer as well as the deterministic
    # one: a provider configured since the build does not retrospectively
    # change a page somebody has already sent. `create()` already says to
    # rebuild for a richer page, and a rebuild is what picks.
    if "picks" in row:
        stored = dict(row.get("picks") or {})
        if len(stored.get("cards") or []) == benefits:
            return stored, ""
        from .landing_images import pick
        return pick(brief, benefits=benefits), (
            " The rewrite changed how many benefits the page lists, so the "
            "photographs were chosen again to fill the new row — check them "
            "before you send the link.")
    # A page built before the set was stored. Re-picking is the only thing
    # available, and saying so is better than a rep wondering why the hero
    # moved. Once rebuilt, the row carries its own and this stops happening.
    from .landing_images import pick
    return pick(brief, benefits=benefits), (
        " This page predates stored photography, so the pictures were chosen "
        "again — from now on a rewrite will keep them.")

def _endpoints(slug: str) -> tuple[str, str]:
    """Where a built page posts its leads, and where it reports being read.

    One reading of the base, because the two must agree: a page whose form
    reaches the Hub while its beacon does not is a page whose leads are
    counted against visits nobody recorded, which is the ratio-with-no-
    denominator this whole measurement exists to close.

    `config.public_base_origin()` rather than `settings.public_base_url`,
    because `settings` is a frozen dataclass built once at import and this is
    the one variable somebody corrects mid-incident -- the reasoning
    `hub/oauth_redirects.py` already gives about a callback URI, applied to
    the address a page posts its leads to. Read from `settings`, a corrected
    PUBLIC_BASE_URL needed a redeploy before a rebuilt page picked it up,
    which is when nobody wants one.

    With none set both fall back to a relative path, which works while the
    page is served from the Hub and is what the form has always done.
    """
    from hub.config import public_base_origin
    base = (public_base_origin() or "").rstrip("/")
    lead = f"{base}/api/leads/capture" if base else "/api/leads/capture"
    view = (f"{base}/sales/landing/p/{slug}/opened" if base
            else f"/sales/landing/p/{slug}/opened")
    return lead, view


def _google_standing_note(domain: str) -> str:
    """What the last audit saw on their Google listing, as a sentence.

    Three answers, never two. A listing with a rating is the reason to go and
    fetch a couple of those reviews; a listing nobody has claimed is a
    different conversation and worth saying; and a scan that never ran, or a
    plan that did not measure the listing, says nothing at all rather than
    reading as a business with no reviews. Nothing here may raise -- a page
    that built is not going to fail over a footnote.
    """
    try:
        from hub.scan_facts import social_snapshot
        snap = social_snapshot(domain) or {}
    except Exception:                                       # noqa: BLE001
        return ""
    gbp = snap.get("gbp") or {}
    if not snap.get("found") or not gbp.get("measured"):
        return ""
    seen = str(snap.get("scanned_at") or "")[:10]
    when = f" as of {seen}" if seen else ""
    if gbp.get("found") is False:
        return (" The last audit of their site found no Google Business "
                f"listing{when}, so there may be no reviews to paste.")
    rating, count = gbp.get("rating"), gbp.get("reviews")
    if count:
        rate = f"{rating} from " if rating else ""
        return (f" Their Google listing showed {rate}{count} review(s){when} —"
                " open it, pick the two best and paste them in.")
    if gbp.get("claimed") is False:
        return (" Their Google listing is unclaimed, so nobody is collecting "
                "reviews on it — worth raising before the page goes live.")
    return ""


def page_url(slug: str) -> str:
    """The absolute address of a built page, or "" when we cannot know it.

    The maker has always handed back `/sales/landing/p/<slug>` -- a path,
    which is exactly right for the `window.open()` it was written for and is
    not a thing a rep can send anybody. A path pasted into a text message is
    a broken link; a path pasted into an ad platform is refused.

    Read at call time from `config.public_base_origin()` rather than stamped
    onto the row at build time: `PUBLIC_BASE_URL` is the one variable
    somebody corrects mid-incident, and a page built before the correction
    should not carry the wrong host for the rest of its life. With none set
    this returns "" and every caller says so rather than handing over a path
    dressed as a link -- absent is named, never guessed.
    """
    slug = (slug or "").strip()
    if not slug:
        return ""
    try:
        from hub.config import public_base_origin
        origin = public_base_origin()
    except Exception:                                       # noqa: BLE001
        return ""
    return f"{origin}/sales/landing/p/{slug}" if origin else ""


NO_URL_NOTE = (" The Hub does not know its own public address "
               "(PUBLIC_BASE_URL is not set), so there is no link to copy — "
               "open the page and take the address from the bar.")


def get(id_or_slug: str) -> dict | None:
    want = (id_or_slug or "").strip().lower()
    for r in _load():
        if r.get("id") == want or r.get("slug") == want:
            return r
    return None


VERSION_LIMIT = 10


def _push_version(row: dict, why: str = "") -> None:
    """Put the page as it stands now onto the row's history.

    One writer, because there were two and they disagreed: `revise()` recorded
    the instruction behind a rewrite and `update_html()` did not, and neither
    recorded the *copy* -- so a restored page would be re-rendered from the
    newer text by the next rewrite, quietly undoing the restore. A version is
    what it takes to put the page back, which is the html and the copy it was
    written from.
    """
    row.setdefault("versions", []).append({
        "html": row.get("page_html", ""),
        "copy": row.get("copy") or {},
        "headline": row.get("headline", ""),
        "saved": row.get("updated") or row.get("created"),
        "by": row.get("updated_by") or row.get("by", ""),
        "why": why,
    })
    row["versions"] = row["versions"][-VERSION_LIMIT:]


def update_html(id_or_slug: str, html: str, actor: str = "") -> dict:
    """Save an edited page, keeping the previous version."""
    rows = _load()
    for r in rows:
        if r.get("id") == id_or_slug or r.get("slug") == id_or_slug:
            _push_version(r, "edited by hand")
            r["page_html"] = html
            r["updated"] = _now()
            r["updated_by"] = actor
            _save(rows)
            return {"ok": True, "versions": len(r["versions"])}
    return {"error": "No such landing page."}


def versions(id_or_slug: str) -> dict:
    """What this page used to be, newest first.

    `revise()` has answered "Rewritten. The previous version is kept." since
    the day it was written, and kept ten of them on every row -- and nothing
    anywhere could read one back. A promise a tool makes and cannot honour is
    worse than one it never made: a rep who trusts that sentence and asks for
    a rewrite has no way to get the page they had.

    The html is deliberately not carried. A version is a whole rendered page,
    so a list of ten is most of a megabyte into a screen that only needs to
    say which one to put back.
    """
    row = get(id_or_slug)
    if not row:
        return {"error": "No such landing page."}
    out = []
    for i, v in enumerate(row.get("versions") or []):
        out.append({
            "index": i,
            "saved": v.get("saved") or "",
            "by": v.get("by") or "",
            "why": v.get("why") or "",
            "headline": v.get("headline") or "",
            # Whether a rewrite after restoring this one would start from its
            # words or from today's. Named rather than left to be discovered:
            # rows written before versions carried copy cannot answer, and a
            # restore that silently loses its wording on the next rewrite is
            # the failure the history exists to prevent.
            "has_copy": bool(v.get("copy")),
        })
    out.reverse()
    return {"ok": True, "versions": out, "count": len(out),
            "limit": VERSION_LIMIT, "slug": row.get("slug", ""),
            "current_saved": row.get("updated") or row.get("created") or "",
            "current_headline": row.get("headline") or ""}


def restore(id_or_slug: str, index: int, actor: str = "") -> dict:
    """Put a previous version back, keeping the current one.

    Restoring is itself undoable -- the page as it stands goes onto the stack
    before the old one is written back, so a restore taken by mistake is one
    more press to reverse. That is the same rule `revise()` follows and the
    reason both go through `_push_version`.

    `index` is the position in the stored list, which is what `versions()`
    hands back on every row. It is never a timestamp: two rewrites in one
    minute would name one version twice.
    """
    rows = _load()
    for r in rows:
        if r.get("id") == id_or_slug or r.get("slug") == id_or_slug:
            stack = r.get("versions") or []
            if not isinstance(index, int) or not 0 <= index < len(stack):
                return {"error": "That version is not on this page any more. "
                                 f"Only the last {VERSION_LIMIT} are kept."}
            want = stack[index]
            if not str(want.get("html") or "").strip():
                return {"error": "That version has no page saved against it, "
                                 "so there is nothing to put back."}
            _push_version(r, "replaced by a restore")
            # The stack was rewritten by the push above, so the version being
            # restored has moved: read it out first and drop it from the new
            # stack by identity rather than by the index it used to have.
            r["versions"] = [v for v in r["versions"] if v is not want]
            r["page_html"] = want["html"]
            note = "Put back."
            if want.get("copy"):
                r["copy"] = want["copy"]
                r["headline"] = want.get("headline") or r.get("headline", "")
            else:
                note += (" This version predates the copy history, so the page "
                         "is back and a rewrite will still start from the "
                         "current wording.")
            r["updated"] = _now()
            r["updated_by"] = actor
            _save(rows)
            try:
                from hub import audit
                audit.log("landing_maker", "restored", actor=actor or None,
                          client=r.get("client"), page=r.get("slug"))
            except Exception:                               # noqa: BLE001
                pass
            return {"ok": True, "slug": r.get("slug", ""),
                    "preview": f"/sales/landing/p/{r.get('slug', '')}",
                    "url": page_url(r.get("slug") or ""),
                    "versions": len(r["versions"]), "note": note}
    return {"error": "No such landing page."}


REVISE_SYSTEM = (
    "You are revising landing page copy that is already live. You are given "
    "the current copy as JSON and an instruction from the person who owns "
    "the page.\n\n"
    "Change what the instruction asks for and leave everything else exactly "
    "as it is.\n\n"
    "Return a JSON object containing ONLY the keys you actually changed, "
    "using the same key names and the same shapes as the copy you were "
    "given. Do not return a key you did not change, even unaltered. If the "
    "instruction is about the headline, the object has one key in it.\n\n"
    "Returning the whole document is how a rewrite of one line comes back "
    "having reworded five others, on a page that is already running.\n\n"
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
    #
    # `changed` is what the rewrite actually touched, compared rather than
    # taken on trust: asking for only the changed keys is a request, and a
    # model that returns the whole document anyway must not read as having
    # rewritten one line. It is what the undo below is offered against, and
    # the one number that answers "what did that do to my page".
    changed = []
    for key in list(copy):
        if key in data and data[key] not in (None, ""):
            if data[key] != copy[key]:
                changed.append(key)
            copy[key] = data[key]
    copy["source"] = "ai"

    from .landing_render import render_page, with_endpoint
    pics, pics_note = _picks_for_revision(
        row, brief, benefits=len([b for b in (copy.get("benefits") or [])
                                  if isinstance(b, dict) and b.get("title")]))
    # `goal_id` is passed explicitly. Left off, `render_page()` fell back to
    # `copy["goal_id"]` and, where a rewrite dropped that key, to the default
    # general-inquiry goal -- so a rewrite could quietly change which fields
    # the form draws and which of them are required, on a page already taking
    # paid traffic. The offer and the page's own slug travel for the same
    # reason: a re-render must not lose the offer above the button or the
    # identity every lead is filed under.
    from hub import landing_spec as _spec
    _row_offer = row.get("offer") or ""
    html = render_page(brief, copy,
                       DIRECTIONS.get(row.get("direction"), DIRECTIONS["trust"]),
                       pics, goal_id=row.get("goal") or "",
                       reviews=row.get("reviews"),
                       ga4_id=row.get("ga4_id", ""),
                       slug=row.get("slug", ""), offer=_row_offer,
                       offer_usable=_spec.offer_state(_row_offer)[0] == _spec.READ)
    _lead_ep, _view_ep = _endpoints(row.get("slug", ""))
    html = with_endpoint(html, _lead_ep, _view_ep)

    rows = _load()
    undo_index = None
    for r in rows:
        if r.get("id") == row["id"]:
            _push_version(r, instructions[:200] or "rewritten")
            # The version just pushed is the page as it stood a moment ago,
            # and it is the last one on the stack. Named here rather than
            # left for the rep to find in History: an undo two screens away
            # from the button that caused it is an undo nobody presses, and
            # every other index shifts the next time anything is pushed.
            undo_index = len(r["versions"]) - 1
            r["page_html"] = html
            r["copy"] = copy
            r["headline"] = copy.get("headline", r.get("headline", ""))
            r["picks"] = _keep_picks(pics)
            r["images"] = {"available": pics.get("available", False),
                           "source": pics.get("source", ""),
                           "cards": len(pics.get("cards") or [])}
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
    # What it did, rather than that it did something. "Rewritten" over a
    # rewrite that reworded five sections and one that changed a headline
    # reads identically, and only one of them is what was asked for.
    if not changed:
        note = ("Rewritten, and nothing came back different — the instruction "
                "may already be satisfied, or it asked for something the "
                "copy rules do not allow inventing.")
    else:
        note = ("Rewritten. Changed: " + ", ".join(changed[:6])
                + (f" and {len(changed) - 6} more" if len(changed) > 6 else "")
                + ".")
    note += pics_note
    if undo_index is not None:
        note += " The previous version is kept — Undo puts it straight back."
    return {"ok": True, "slug": row["slug"],
            "preview": f"/sales/landing/p/{row['slug']}",
            "url": page_url(row["slug"]),
            "changed": changed,
            # The one press that reverses this rewrite. `None` where the row
            # could not be written, so a screen cannot offer an undo for
            # something that did not happen.
            "undo_index": undo_index,
            "versions": len(get(row["id"]).get("versions") or []),
            "note": note}


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
    shown = rows[:300]
    # Whether anybody has actually seen these. One query for the page of
    # rows rather than one per row, and one answer about whether it could be
    # read at all -- a table that will not answer and a page nobody has
    # visited both render as a nought, and only one of them is a reason to
    # stop spending on the campaign.
    try:
        from hub import landing_views as lv
        counts = lv.summary_for([r.get("slug") for r in shown])
    except Exception as exc:                                # noqa: BLE001
        counts = {"measured": False, "pages": {},
                  "error": f"The visit counts could not be read. ({type(exc).__name__})"}
    seen = counts.get("pages") or {}
    return {
        # `url` is derived per row rather than stored, so a corrected
        # PUBLIC_BASE_URL reaches every page already built. "" means the Hub
        # cannot name its own host; the screen says so instead of drawing a
        # copy button that would hand over a path.
        "pages": [{**{k: r.get(k) for k in
                      ("id", "slug", "client", "campaign", "headline",
                       "direction", "created", "by", "proposal_id", "updated",
                       "kind", "website", "images")},
                   "url": page_url(r.get("slug") or ""),
                   "versions": len(r.get("versions") or []),
                   # Drawn on the row rather than only on the build result:
                   # the moment somebody needs this is the one before they
                   # send the link, which is days later and on this screen.
                   "readiness": readiness(r),
                   # Absent rather than zero where nothing could be read:
                   # the screen says "not measured" instead of telling a rep
                   # nobody has opened a page that may be doing fine.
                   "views": (seen.get(r.get("slug")) or {}) if counts.get("measured") else None}
                  for r in shown],
        "views_measured": bool(counts.get("measured")),
        "views_error": counts.get("error", ""),
        "views_recent_days": counts.get("recent_days", 0),
        # Three numbers because there are three questions, and the page was
        # printing the second under the first: `count` is how many matched and
        # the table only ever drew 300 of them, so a book past that cap read
        # as a table somebody could count by hand and disagree with. The UTM
        # Builder's "300 of 900", one tool over.
        "shown": len(shown),
        "count": len(rows),
        "clients": sorted({r.get("client") for r in all_rows if r.get("client")}),
        "directions": {k: v["label"] for k, v in DIRECTIONS.items()},
    }



def readiness(row: dict) -> dict:
    """What is still open on a built page, for the screen the rep is on.

    `landing_spec.open_questions()` has computed exactly this since the day it
    was written -- what a page will otherwise write around, and writing around
    a gap is what produces copy that could be about any business in the
    industry. `create()` put the answer on its response as `questions` and
    **no screen has ever drawn it**, so the one list telling a rep what to fix
    before the link goes to a prospect existed, was correct, and was read by
    nobody. The declared-and-never-wired failure this Hub counts a dozen of.

    Asked of the stored row rather than of the build, because the moment that
    matters is the one before somebody hands the link over -- which is days
    after the build and on a different screen. A row that cannot answer is
    said to be unmeasured rather than drawn as a clean bill: "nothing is
    outstanding" and "we could not tell" are different sentences and only the
    first means send it.
    """
    try:
        from hub import landing_spec as spec
        brief = row.get("brief") or {}
        open_q = spec.open_questions(brief, row.get("goal") or "",
                                     row.get("offer") or "",
                                     row.get("promoting") or "")
    except Exception as exc:                                # noqa: BLE001
        return {"measured": False, "questions": [],
                "note": f"The checklist could not be built ({type(exc).__name__})."}

    # Two things that are not open questions about the brief and are still
    # reasons not to send the link yet. Both are facts about the built row
    # rather than judgments, so they are listed with it rather than being a
    # second checklist somewhere else.
    extra = []
    if not page_url(row.get("slug") or ""):
        extra.append("The Hub does not know its own public address, so there "
                     "is no link to send yet." + NO_URL_NOTE.strip())
    if not (row.get("reviews") or []):
        extra.append("No reviews are on the page. Paste in one or two real "
                     "ones and rebuild — the page will never invent one.")
    questions = list(open_q) + extra
    return {"measured": True, "questions": questions,
            "ready": not questions,
            "count": len(questions)}

def for_client(client: str) -> list[dict]:
    """Used by the Client 360 / proposals card."""
    return listing(client=client)["pages"]
