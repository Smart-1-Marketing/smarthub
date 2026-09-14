"""A Proposal Builder quote, read as data rather than as a PDF.

## The problem this closes

The Proposal Execution Center starts a run from a document filed on the
client record and reads its *text*: a regex pass over the prose, then a
model asked to find the channels and the dollar amounts in it. That is the
right shape for a proposal somebody wrote in Word and uploaded. It is the
wrong shape for a quote built in `modules/sales_builder`, which already
holds every fact the analysis is trying to recover -- the line items with
their rate-card product and category, the dollars per line and the term,
the start date, the target areas, the KPIs, and the creative gate's own
answers about who supplies each medium's files. A delivered quote is filed
on the client as a PDF (`Quote.client_filed_as` points at the record), so
execution was reading the rendered document back and asking the rep again
for things the quote had already been told.

This module reads the quote's own state into the same analysis shape the
engine and `hub/proposal_plan.py` already consume, so nothing downstream
has to know which kind of document a run started from.

## Rules

* **A channel is a rate-card family, matched on the category first and the
  product second, never on prose.** `channel_for_item()` reads the line the
  way `hub/creative_needs.medium_of()` does and answers with the execution
  channel key -- the ten keys the text analyzer already knows keep their
  names so the task graph still builds for them, and the card's other
  families (display, connected TV, digital radio, email, signage, paid
  social, web) get keys of their own so the plan can tell them apart.
* **The creative is the gate's own reading.** The channel carries its
  products, and the plan hands those to `creative_needs.required_units()`
  -- the same reader the Proposal Builder's creative step uses -- rather
  than a recipe guessing at the kit product. A quote selling Connected TV
  and Snapchat gets the kit's units for both, and nothing is retyped here.
* **What the quote already answered is carried as an answer, marked as the
  quote's.** The start date, the budget per channel, who supplies each
  medium's creative (from the creative gate, or from a production line on
  the plan), and a reporting cadence the document states. Every one of
  them is offered as `from_text` with its evidence and can be changed; none
  is written anywhere the person cannot see.
* **Nothing is invented for a line that names no channel.** A management
  fee, a consulting engagement and a phone number are on the plan and are
  not campaigns; they are named in the facts rather than folded into the
  nearest channel or silently dropped.
* **Nothing here writes to the quote.** It is a reading. The quote stays
  the Proposal Builder's, and the plan a person reviews stays the run's.

`text()` renders the same facts as plain prose, because the plan's model
pass grounds every specific promise it returns against a quote from the
document -- and a quote built here has no single document until the PDF
is rendered. The prose is deterministic, so the run's `source_hash` moves
only when the quote does.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict

QUOTE_PREFIX = "quote:"

# The channel keys a quote's lines can land on. The first block are the keys
# `hub/proposal_execution.CHANNEL_PATTERNS` already knows, kept verbatim so
# a quote-sourced run builds the same task graph a text-sourced one does;
# the rest are the card's own families. `medium` is the creative gate's
# medium for the channel, which is what decides whether the plan asks who
# supplies its files.
QUOTE_CHANNELS: "OrderedDict[str, dict]" = OrderedDict([
    ("retargeting",   {"name": "Website Retargeting", "medium": "retargeting"}),
    ("paid_search",   {"name": "Paid Search", "medium": "other"}),
    ("seo_ai",        {"name": "SEO", "medium": "other"}),
    ("meta",          {"name": "Meta (Facebook & Instagram)", "medium": "social"}),
    ("youtube_ads",   {"name": "YouTube Advertising", "medium": "video"}),
    ("social",        {"name": "Social Media Management", "medium": "social"}),
    ("paid_social",   {"name": "Paid Social", "medium": "social"}),
    ("display",       {"name": "Display", "medium": "display"}),
    ("ctv",           {"name": "Connected TV / Streaming Video", "medium": "video"}),
    ("digital_radio", {"name": "Digital Radio & Podcasts", "medium": "audio"}),
    ("email",         {"name": "Email Marketing", "medium": "email"}),
    ("dooh",          {"name": "Digital Signage", "medium": "dooh"}),
    ("web",           {"name": "Website", "medium": "other"}),
])

# Rate-card category -> channel key. Category first, because the card files
# four products called "Demographic" under four headings and the heading is
# what says which buy it is. The display family is decided per line below,
# since four programmatic *video* products sit under the DISPLAY heading --
# the trap `creative_needs.EXPLICIT_MEDIUM` exists for.
_CATEGORY_KEYS = {
    "SEARCH ENGINE MARKETING / PAY PER CLICK": "paid_search",
    "SEARCH ENGINE OPTIMIZATION": "seo_ai",
    "META": "meta",
    "SOCIAL ADS - VIDEO": "paid_social",
    "SOCIAL MEDIA MANAGEMENT": "social",
    "YOUTUBE": "youtube_ads",
    "OTT": "ctv",
    "DIGITAL RADIO": "digital_radio",
    "EMAIL MARKETING": "email",
    "SMART 1 SIGNAGE": "dooh",
    "WEB DEVELOPMENT": "web",
}
_DISPLAY_FAMILY = {"DISPLAY", "DATA TARGETED DISPLAY", "MOBILE ONLY",
                   "LOCATION LOOKBACK", "IP TARGETS"}
# Lines that are on the plan and are not a campaign. Named so the facts can
# say what they are rather than the plan folding them into a channel.
_NOT_A_CHANNEL = {"CREATIVE / DESIGN SERVICES", "ADD-ON PRODUCT", "MANAGEMENT",
                  "PRODUCTION", "CONSULTING"}

# A production line on the plan is the quote saying who makes the creative.
# Product -> the channel keys whose files it produces.
_PRODUCTION_LINES = {
    "standard set of 6 ad creation": ("display", "retargeting"),
    "expandable / rich media ad creation": ("display",),
    "social media ad creation per platform": ("meta", "paid_social", "social"),
    "email template creation": ("email",),
    "email template creative production": ("email",),
}

_CADENCE_RE = re.compile(
    r"\b(monthly|every month|each month|month-by-month|weekly|every week|each week|"
    r"quarterly|every quarter|each quarter)\b", re.I)
_CADENCE_KEY = {"monthly": "monthly", "every month": "monthly", "each month": "monthly",
                "month-by-month": "monthly", "weekly": "weekly", "every week": "weekly",
                "each week": "weekly", "quarterly": "quarterly", "every quarter": "quarterly",
                "each quarter": "quarterly"}


def _norm_client(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _money(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"


def quote_id_for(proposal_id) -> int | None:
    """The quote id inside a `quote:<id>` proposal id, or None."""
    text = str(proposal_id or "").strip()
    if not text.startswith(QUOTE_PREFIX):
        return None
    try:
        return int(text[len(QUOTE_PREFIX):])
    except ValueError:
        return None


def _quote_dict(q) -> dict:
    try:
        state = json.loads(q.data or "{}")
    except (TypeError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    return {
        "id": int(q.id), "quote_number": q.quote_number or "", "status": q.status or "",
        "client": q.client or "", "website": q.website or "", "state": state,
        "updated": q.updated_at.isoformat() if getattr(q, "updated_at", None) else "",
        "products_summary": q.products_summary or "", "monthly_budget": q.monthly_budget or 0,
        "months": q.months or 1, "revision": q.revision or 1,
        "client_filed_as": q.client_filed_as or "",
    }


def quote_row(quote_id, client: str) -> dict | None:
    """One quote as a plain dict, or None. Never raises.

    The client is checked rather than trusted: a run is filed against a
    client, and a quote id somebody typed into the URL must not pull
    another client's proposal onto this one's record. A quote that is not
    this client's answers exactly what a quote that does not exist answers.
    """
    try:
        from modules.sales_builder.app import Quote, SessionLocal
        db = SessionLocal()
        try:
            q = db.get(Quote, int(quote_id))
            if q is None or _norm_client(q.client) != _norm_client(client):
                return None
            return _quote_dict(q)
        finally:
            db.close()
    except Exception:                                   # noqa: BLE001
        return None


def quote_choices(client: str) -> tuple[list[dict], str]:
    """The saved quotes for a client, as picker entries, newest first.

    `(choices, error)`: "this client has no saved quote" and "the builder's
    table could not be read" are different answers, and only the first
    means there is nothing to offer.
    """
    want = _norm_client(client)
    if not want:
        return [], ""
    try:
        from modules.sales_builder.app import Quote, SessionLocal
        db = SessionLocal()
        try:
            rows = (db.query(Quote).order_by(Quote.updated_at.desc()).limit(400).all())
            out = []
            for q in rows:
                if _norm_client(q.client) != want:
                    continue
                filed_id, _, _rev = (q.client_filed_as or "").partition("@")
                out.append({
                    "id": f"{QUOTE_PREFIX}{q.id}",
                    "title": (q.products_summary or "Proposal")[:120],
                    "filename": "", "kind": "quote",
                    "quote_number": q.quote_number or "", "status": q.status or "",
                    "date_sent": q.updated_at.strftime("%Y-%m-%d") if q.updated_at else "",
                    "monthly": q.monthly_budget or 0, "filed_id": filed_id,
                })
            return out, ""
        finally:
            db.close()
    except Exception as exc:                            # noqa: BLE001
        return [], f"Saved proposals could not be read ({type(exc).__name__})."


def quote_for_record(record: dict, client: str) -> int | None:
    """The quote a filed proposal record is the PDF of, or None.

    Delivering a quote files its PDF on the client and writes the record id
    onto the quote as `client_filed_as` ("<record id>@<revision>"). That is
    the reverse join: given the record somebody picked, the quote whose
    data it was rendered from -- so the run reads the data rather than the
    rendering. A record nothing points at is an uploaded document and is
    read as one.
    """
    rec_id = str((record or {}).get("id") or "").strip()
    if not rec_id:
        return None
    for choice in quote_choices(client)[0]:
        if choice.get("filed_id") and choice["filed_id"] == rec_id:
            return quote_id_for(choice["id"])
    return None


# ---------------------------------------------------------------------------
# Lines -> channels
# ---------------------------------------------------------------------------
def _medium_of(item: dict) -> str:
    try:
        from hub import creative_needs
        return creative_needs.medium_of(item)
    except Exception:                                   # noqa: BLE001
        return "other"


def channel_for_item(item: dict) -> str:
    """The execution channel a rate-card line belongs to, or "" for a line
    that is not a campaign (a fee, a production line, an add-on)."""
    item = item or {}
    category = str(item.get("category") or "").strip().upper()
    product = str(item.get("product") or "").strip().lower()
    if category in _NOT_A_CHANNEL:
        return ""
    if category == "RETARGETING":
        return "meta" if ("facebook" in product or "instagram" in product) else "retargeting"
    if category in _CATEGORY_KEYS:
        return _CATEGORY_KEYS[category]
    medium = _medium_of(item)
    if category in _DISPLAY_FAMILY:
        return "ctv" if medium == "video" else "display"
    # A category this table has never heard of: the creative gate's own
    # reading of the line decides, and a line it cannot place names nothing.
    by_medium = {"video": "ctv", "audio": "digital_radio", "social": "paid_social",
                 "retargeting": "retargeting", "display": "display", "email": "email",
                 "dooh": "dooh"}
    if medium in by_medium:
        return by_medium[medium]
    if "seo" in product or "search engine optimization" in product:
        return "seo_ai"
    if "pay per click" in product or "ppc" in product or "paid search" in product:
        return "paid_search"
    return ""


def _label(item: dict) -> str:
    try:
        from hub import rate_card
        return rate_card.quote_label(item.get("product"), item.get("category"))
    except Exception:                                   # noqa: BLE001
        return str(item.get("label") or item.get("product") or item.get("category") or "")


def channels_from_state(state: dict) -> tuple[list[dict], list[dict]]:
    """`(channels, other_lines)` -- the campaign channels a quote's lines
    make, and the lines that are on the plan and are not a campaign."""
    state = state or {}
    months = max(1, int(state.get("months") or 1))
    by_key: "OrderedDict[str, dict]" = OrderedDict()
    others: list[dict] = []
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            dollars = float(item.get("dollars") or 0)
        except (TypeError, ValueError):
            dollars = 0.0
        one_time = str(item.get("basis") or "monthly") == "one_time"
        try:
            term = int(item.get("termMonths") or months)
        except (TypeError, ValueError):
            term = months
        term = max(1, min(months, term))
        line = {"product": str(item.get("product") or ""), "category": str(item.get("category") or ""),
                "label": _label(item), "description": str(item.get("description") or "").strip(),
                "dollars": round(dollars, 2), "basis": "one_time" if one_time else "monthly",
                "term_months": 1 if one_time else term}
        key = channel_for_item(item)
        if not key:
            others.append(line)
            continue
        spec = QUOTE_CHANNELS[key]
        ch = by_key.setdefault(key, {"key": key, "name": spec["name"], "medium": spec["medium"],
                                     "media": [], "budgets": [], "deliverables": [], "products": [],
                                     "monthly": 0.0, "one_time": 0.0, "term_months": 0})
        ch["products"].append({"product": line["product"], "category": line["category"]})
        # The creative gate's own reading of each line, kept beside the
        # channel's nominal medium: the card files a Snapchat buy under a
        # video heading and a display family carries video products, and
        # who supplies the files is answered per gate medium.
        gate_medium = _medium_of(item)
        if gate_medium not in ch["media"]:
            ch["media"].append(gate_medium)
            if len(ch["media"]) == 1 and gate_medium != "other":
                ch["medium"] = gate_medium
        deliverable = line["label"] + (f" — {line['description']}" if line["description"] else "")
        if deliverable not in ch["deliverables"]:
            ch["deliverables"].append(deliverable)
        if one_time:
            ch["one_time"] = round(ch["one_time"] + dollars, 2)
        else:
            ch["monthly"] = round(ch["monthly"] + dollars, 2)
            ch["term_months"] = max(ch["term_months"], term)
    for ch in by_key.values():
        if ch["monthly"]:
            ch["budgets"].append(f"{_money(ch['monthly'])}/mo")
        if ch["one_time"]:
            ch["budgets"].append(f"{_money(ch['one_time'])} one-time")
    return list(by_key.values()), others


# ---------------------------------------------------------------------------
# What the quote already answered
# ---------------------------------------------------------------------------
def supply_from_state(state: dict, channels: list[dict], other_lines: list[dict]) -> dict:
    """Who supplies each channel's creative, as far as the quote says.

    {channel_key: {"who": "smart1"|"client", "evidence": "..."}} -- three
    readings, strongest first: the creative gate's own answer for the
    medium (the rep was asked and answered on the quote), a production line
    on the plan (the quote sells the making of it), and the blanket
    `creativeSource` field older quotes carry. A channel none of them
    speaks to is left out, and the plan asks.
    """
    state = state or {}
    out: dict = {}
    by_medium: dict = {}
    try:
        from hub import creative_needs
        for row in creative_needs.evaluate(state).get("media") or []:
            answer = row.get("answer") or ""
            label = str(row.get("label") or row.get("medium") or "").split(" (")[0]
            if answer == creative_needs.HAS:
                by_medium[row["medium"]] = ("client", f"The quote's creative step says the client already has {label.lower()} creative.")
            elif answer == creative_needs.CLIENT_PAYS:
                fee = row.get("fee") or 0
                by_medium[row["medium"]] = ("smart1", f"The quote prices {label.lower()} production" + (f" at {_money(fee)}" if fee else "") + " — Smart 1 produces it.")
            elif answer == creative_needs.COMP:
                by_medium[row["medium"]] = ("smart1", f"The quote comps {label.lower()} production — Smart 1 produces it.")
    except Exception:                                   # noqa: BLE001
        by_medium = {}
    produced: dict = {}
    for line in other_lines:
        keys = _PRODUCTION_LINES.get(str(line.get("product") or "").strip().lower())
        for key in keys or ():
            produced.setdefault(key, f"The quote includes {line.get('label') or line.get('product')}, so Smart 1 produces it.")
    blanket = str(state.get("creativeSource") or "").strip()
    blanket_who = ""
    if blanket.lower().startswith("client"):
        blanket_who = "client"
    elif blanket.lower().startswith("smart 1") or blanket.lower().startswith("smart1"):
        blanket_who = "smart1"
    for ch in channels:
        key, medium = ch["key"], ch.get("medium") or "other"
        gate_media = [m for m in (ch.get("media") or [medium]) if m in by_medium]
        if gate_media:
            who, why = by_medium[gate_media[0]]
        elif key in produced:
            who, why = "smart1", produced[key]
        elif blanket_who and medium != "other":
            who, why = blanket_who, f"Creative source on the quote: {blanket}."
        else:
            continue
        out[key] = {"who": who, "evidence": why}
    return out


def reporting_from_state(state: dict) -> dict:
    """The reporting cadence the document states, or {} when it states none.

    Read off the quote's own Reporting section rather than assumed: a Hub
    proposal always *has* that section, and a cadence nobody wrote into it
    is not one the client was promised.
    """
    for sec in (state or {}).get("sections") or []:
        if not isinstance(sec, dict) or sec.get("id") != "reporting" or not sec.get("enabled", True):
            continue
        body = _plain(sec.get("body") or "")
        m = _CADENCE_RE.search(body)
        if not m:
            return {}
        start = body.rfind(".", 0, m.start()) + 1
        end = body.find(".", m.end())
        sentence = body[start:(end + 1 if end >= 0 else len(body))].strip()
        return {"cadence": _CADENCE_KEY[m.group(1).lower()], "evidence": sentence[:300]}
    return {}


def _plain(text: str) -> str:
    try:
        from hub import proposal_spec
        return proposal_spec.plain_text(text)
    except Exception:                                   # noqa: BLE001
        return re.sub(r"<[^>]+>", "", str(text or ""))


def _geography(state: dict) -> str:
    try:
        from hub import target_areas
        areas = target_areas.normalize(state.get("targetAreas")) or target_areas.from_legacy(state)
        return target_areas.summary(areas, limit=3) if areas else ""
    except Exception:                                   # noqa: BLE001
        return ""


def facts_from_state(state: dict, quote: dict | None = None) -> dict:
    """Everything the plan can pre-answer from the quote, in one dict."""
    state = state or {}
    quote = quote or {}
    channels, others = channels_from_state(state)
    tracking = state.get("trackingPlan") or {}
    facts = {
        "quote_id": quote.get("id"), "quote_number": quote.get("quote_number") or "",
        "revision": quote.get("revision") or 1, "status": quote.get("status") or "",
        "start_date": str(state.get("startDate") or "").strip(),
        "months": max(1, int(state.get("months") or 1)),
        "geography": _geography(state),
        "landing_url": str(state.get("landingUrl") or quote.get("website") or "").strip(),
        "conversion_goal": str(tracking.get("primaryConversion") or "").strip() if isinstance(tracking, dict) else "",
        "supply": supply_from_state(state, channels, others),
        "reporting": reporting_from_state(state),
        "other_lines": others,
        "channels": [c["key"] for c in channels],
    }
    return facts


def analysis_from_quote(quote: dict) -> tuple[dict, str]:
    """The engine's analysis shape, built from a quote's own state, and the
    prose the plan's model pass grounds against."""
    state = quote.get("state") or {}
    channels, others = channels_from_state(state)
    facts = facts_from_state(state, quote)
    monthly = round(sum(c["monthly"] for c in channels), 2)
    one_time = round(sum(c["one_time"] for c in channels) + sum(
        o["dollars"] for o in others if o.get("basis") == "one_time"), 2)
    headline = {}
    if monthly:
        headline["monthly"] = f"{_money(monthly)}/mo"
    if one_time:
        headline["one_time"] = _money(one_time)
    analysis = {
        "client": quote.get("client") or "",
        "channels": channels,
        "facts": {"market": facts["geography"], "landing_url": facts["landing_url"],
                  "conversion_goal": facts["conversion_goal"]},
        "headline_budgets": headline,
        "objectives": [str(o) for o in (state.get("objectives") or []) if str(o).strip()],
        "audiences": [str(a) for a in (state.get("audiences") or []) if str(a).strip()],
        "flight_dates": [facts["start_date"]] if facts["start_date"] else [],
        "recurring": [c["name"] for c in channels if c["monthly"]],
        "source": "quote",
        "quote": facts,
    }
    return analysis, text(quote, channels, facts)


def text(quote: dict, channels: list[dict] | None = None, facts: dict | None = None) -> str:
    """The quote as plain prose -- one line per fact, deterministic."""
    state = quote.get("state") or {}
    if channels is None:
        channels, _others = channels_from_state(state)
    facts = facts or facts_from_state(state, quote)
    lines = [f"{facts.get('quote_number') or 'Proposal'} — {quote.get('client') or ''}".strip(" —")]
    term = f"{facts['months']} month" + ("s" if facts["months"] != 1 else "")
    lines.append(f"Campaign term: {term}" + (f", starting {facts['start_date']}" if facts.get("start_date") else ""))
    if facts.get("geography"):
        lines.append(f"Target areas: {facts['geography']}")
    if state.get("objectives"):
        lines.append("Objectives: " + ", ".join(str(o) for o in state["objectives"]))
    if state.get("kpis"):
        lines.append("KPIs: " + ", ".join(str(k) for k in state["kpis"]))
    if facts.get("landing_url"):
        lines.append(f"Landing page: {facts['landing_url']}")
    lines.append("Media plan:")
    for ch in channels:
        budget = " / ".join(ch["budgets"]) if ch["budgets"] else "no dollar amount"
        lines.append(f"- {ch['name']}: {budget}")
        for d in ch["deliverables"]:
            lines.append(f"  - {d}")
    for other in facts.get("other_lines") or []:
        amount = _money(other["dollars"]) + ("" if other["basis"] == "one_time" else "/mo")
        lines.append(f"- {other['label']}: {amount}" + (f" — {other['description']}" if other.get("description") else ""))
    for key, who in (facts.get("supply") or {}).items():
        lines.append(f"Creative for {QUOTE_CHANNELS.get(key, {}).get('name', key)}: {who['evidence']}")
    for sec in state.get("sections") or []:
        if not isinstance(sec, dict) or not sec.get("enabled", True):
            continue
        body = _plain(sec.get("body") or "").strip()
        if not body:
            continue
        lines.append("")
        lines.append(str(sec.get("title") or sec.get("id") or "").strip())
        lines.append(body)
    return "\n".join(lines).strip() + "\n"


__all__ = ["QUOTE_PREFIX", "QUOTE_CHANNELS", "quote_id_for", "quote_row", "quote_choices",
           "quote_for_record", "channel_for_item", "channels_from_state", "supply_from_state",
           "reporting_from_state", "facts_from_state", "analysis_from_quote", "text"]
