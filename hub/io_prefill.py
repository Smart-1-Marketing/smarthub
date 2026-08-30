"""Start an IO from something that already exists.

Three ways in, all of which beat a blank form:

  **New**       everything the Hub knows about the client — contact details,
                website, brand, geography.
  **Renewal**   the client's most recent insertion order, with its products,
                budgets and flight dates carried forward and the dates rolled
                to the next term.
  **Proposal**  a proposal document, read for the numbers and products it
                already contains.

## Why prefill rather than autofill-and-submit

Every field carries where it came from, and nothing is treated as confirmed.
A renewal that silently reuses last year's budget is how a client gets billed
the wrong amount — the rep has to see the number and accept it. So the payload
marks each field `known` (from our records), `carried` (from the last IO, may
have changed), or `needs_review` (guessed or absent).

## What a renewal deliberately does NOT carry

Order number, signature, submission date, and anything time-bound. Copying an
order number would collide with a real IO; copying a signature would be
forging one.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Never carried from a previous IO, whatever the source says.
NEVER_CARRY = {"order_number", "signature", "signed_at", "submitted_at",
               "io_number", "record_id", "id", "status"}


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


def _add_year(iso: str) -> str:
    """Roll a date forward one term, keeping the day of month where possible."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (ValueError, TypeError):
        return ""
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:                  # 29 Feb
        return (d.replace(year=d.year + 1, day=28)).isoformat()


def from_client(client: str) -> dict:
    """Everything the Hub knows, shaped for the IO intake."""
    fields, sources = {}, {}

    def take(key, value, source):
        v = str(value or "").strip()
        if v and key not in fields:
            fields[key] = v
            sources[key] = source

    try:
        from hub.client_context import context
        ctx = context(client)
        f = ctx.get("fields", {})
        take("client_name", f.get("client") or client, "known")
        take("website", f.get("website"), "known")
        take("client_contact_phone", f.get("phone"), "known")
        take("client_contact_email", f.get("email"), "known")
        take("address", f.get("address"), "known")
        take("city", f.get("city"), "known")
        take("state", f.get("state"), "known")
        take("zip", f.get("zip"), "known")
        take("industry", f.get("industry"), "known")
        take("geo", ", ".join(x for x in (f.get("city"), f.get("state")) if x),
             "known")
    except Exception:                                   # noqa: BLE001
        take("client_name", client, "known")

    try:
        from hub.knack_websites import enrich
        reg = enrich(client)
        if reg.get("found"):
            take("website", reg.get("website"), "known")
            take("media_partner", reg.get("media_partner"), "known")
    except Exception:                                   # noqa: BLE001
        pass

    return {"mode": "new", "client": client, "fields": fields,
            "sources": sources,
            "missing": [k for k in ("client_contact_name", "client_contact_email")
                        if k not in fields],
            "note": "Prefilled from the client record. Anything we don't hold "
                    "is left blank for the intake to ask."}


def from_last_io(client: str) -> dict:
    """Carry the client's most recent insertion order forward a term."""
    try:
        from hub.knack_products import for_client
    except Exception as exc:                            # noqa: BLE001
        return {"error": f"Product data unavailable ({type(exc).__name__})."}

    data = for_client(client)
    products = data.get("products") or []
    if not products:
        return {"error": "No previous insertion order on file for this client.",
                "mode": "renewal", "client": client}

    # Group by IO and take the most recent — an IO is a set of products, and
    # renewing one product out of six would produce a smaller campaign than
    # the client actually had.
    by_io: dict[str, list] = {}
    for p in products:
        by_io.setdefault(str(p.get("io") or ""), []).append(p)
    latest_io = max(by_io, key=lambda k: max(
        str(x.get("start") or "") for x in by_io[k]))
    lines = by_io[latest_io]

    base = from_client(client)
    fields, sources = dict(base["fields"]), dict(base["sources"])

    starts = [x.get("start") for x in lines if x.get("start")]
    ends = [x.get("end") for x in lines if x.get("end")]
    if starts:
        fields["start_date"] = _add_year(min(starts))
        sources["start_date"] = "carried"
    if ends:
        fields["end_date"] = _add_year(max(ends))
        sources["end_date"] = "carried"

    for key, src in (("campaign", "campaign"), ("media_partner", "partner"),
                     ("sales", "sales"), ("trafficker", "trafficker")):
        val = next((x.get(src) for x in lines if x.get(src)), "")
        if val:
            fields[key] = val
            sources[key] = "carried"

    carried_products = [{
        "product": p.get("product"), "kind": p.get("kind"),
        "monthly": p.get("monthly", 0), "total": p.get("total", 0),
        "geo": p.get("geo", ""), "url": p.get("url", ""),
        "previous_status": p.get("status", ""),
    } for p in lines]

    monthly = round(sum(float(p.get("monthly") or 0) for p in lines), 2)

    return {
        "mode": "renewal", "client": client,
        "renewing_io": latest_io,
        "previous_start": min(starts) if starts else "",
        "previous_end": max(ends) if ends else "",
        "fields": fields, "sources": sources,
        "products": carried_products,
        "previous_monthly": monthly,
        "ask": [
            "Are all of these products renewing, or has the mix changed?",
            f"Last term ran at ${monthly:,.2f}/month — is that the new budget?",
            "Do the flight dates below look right for the new term?",
            "Is the creative being reused, or is new creative coming?",
        ],
        "note": (f"Carried from IO {latest_io}. Budgets and dates are last "
                 f"term's, rolled forward — confirm each before submitting. "
                 f"Order number and signature are never carried."),
    }


def creative_for(client: str, limit: int = 24) -> dict:
    """Creative already produced for this client, to attach to the IO."""
    out = []
    try:
        from hub import seo
        store = seo.load_store(client) or {}
        for img in (store.get("images") or [])[:limit]:
            out.append({"url": img.get("url"), "name": img.get("filename")
                        or img.get("name"), "kind": "image",
                        "source": "SEO Image Pipeline"})
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from hub.client_brand import brand_kit
        kit = brand_kit(client)
        for logo in (kit.get("logos") or [])[:3]:
            out.append({"url": logo["url"],
                        "name": f"logo ({logo.get('format', '')})",
                        "kind": "logo", "source": "Brandfetch"})
    except Exception:                                   # noqa: BLE001
        pass
    return {"client": client, "creative": out[:limit], "count": len(out),
            "note": "Existing creative for this client. Attaching reuses what "
                    "we already have rather than asking for it again."}


# ---------------------------------------------------------------------------
# Reading a proposal with the model
# ---------------------------------------------------------------------------
#
# The pattern pass below finds dollar figures and eight product keywords. That
# works on proposals written the way we write them and misses everything else:
# a table of line items, "twelve months", a product named in a sentence rather
# than a heading, the client's own contact block. A rep then retypes it, and
# retyping is where "the proposal said $4,500 but the IO says $4,000" comes
# from.
#
# So the model gets a second look. Three rules make that safe:
#
#   1. **It only adds.** The pattern pass runs first and its findings stand.
#      If the model is off, unconfigured, or slow, the result is exactly what
#      it is today -- never worse.
#   2. **Nothing it says is confirmed.** Every field it produces is marked
#      needs_review, the same as a guess, because a proposal contains ranges,
#      options and "starting at". The rep sees the number and accepts it.
#   3. **Disagreement is surfaced, not resolved.** Where the model and the
#      patterns read different figures, both go in the questions. Silently
#      picking one is how a client gets billed something nobody agreed.

AI_MAX_CHARS = 24_000        # ~6k tokens of proposal; longer is padding

_AI_SYSTEM = (
    "You read advertising proposals and pull out only what is written in "
    "them. You never estimate, never fill gaps from experience, and never "
    "convert a range into a single number. If a document says 'starting at "
    "$2,000' the amount is 2000 and the note says it was a starting price. "
    "If something is not in the document, omit the field entirely. Reply with "
    "JSON only."
)

_AI_SCHEMA = """Return this shape, omitting anything the document does not state:

{
  "business_name": "",
  "website": "",
  "contact_name": "", "contact_email": "", "contact_phone": "",
  "geography": "",
  "objectives": "",
  "monthly_budget": 0,
  "campaign_total": 0,
  "term_months": 0,
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "products": [{"name": "", "monthly": 0, "note": "",
                "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD",
                "dates_note": ""}],
  "ambiguities": ["a short question about anything vague, optional or ranged"]
}

Rules:
- Amounts are plain numbers: 4500, not "$4,500/mo".
- monthly_budget is the recurring monthly spend. campaign_total is the whole
  term. Do not compute one from the other; only report what is stated.
- A product often carries its own flight — "(September - November)",
  "(Beginning November)", "months 1-3". Put whatever the document wrote in
  that product's `dates_note`, word for word.
- Only fill a product's start_date/end_date when the document gives a day,
  a month AND a year for it. A month with no year is not a date: leave both
  fields out and let dates_note carry it. Never copy the campaign's dates
  onto a product that did not state its own.
- For each product use the closest name from this list where one clearly
  fits, otherwise the document's own wording:
"""


def _catalogue() -> list[str]:
    """Product names from the rate card, for the model to map onto.

    Giving it our own vocabulary is what makes the output usable downstream:
    "connected TV" in a proposal comes back as the product the IO builder and
    the rate card already know, rather than a synonym nothing matches.
    """
    try:
        from hub import rate_card
        names, seen = [], set()
        for p in rate_card.products():
            n = (p.get("product") or "").split("  *")[0]
            n = n.split(" - This is")[0].strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                names.append(n)
        return names
    except Exception as exc:                            # noqa: BLE001
        logger.info("rate card unavailable for the proposal reader: %s", exc)
        return []


def _clean_card_name(name: str) -> str:
    """A rate-card product without its explanatory tail.

    The card stores selling copy in the name — "Connected TV - Targeted - This
    is played on televisions only *If you require specifc channel(s)..." — and
    that whole string ends up as a line item on an IO unless it is trimmed.
    """
    n = str(name or "").split("  *")[0]
    n = n.split(" - This is")[0]
    return n.strip()


def _card_name(name: str) -> str:
    """Map a product the model named onto the rate card, or keep its wording.

    Deliberately strict. rate_card.search() is a type-ahead: it returns a best
    guess for almost any input, so using it here turned "Search Engine
    Marketing" into "Pay Per Click" — a different product, on an insertion
    order, silently. This accepts an exact match, or a candidate that actually
    contains what the model said. Anything else keeps the document's own
    wording, which a rep can recognise and correct; a confidently wrong
    product name is the thing they cannot.
    """
    want = _clean_card_name(name)
    if not want:
        return str(name or "").strip()
    try:
        from hub import rate_card
        hit = rate_card.find(want)
        if hit and hit.get("product"):
            return _clean_card_name(hit["product"])
        key = _norm(want)
        if len(key) < 4:                    # too short to match on safely
            return want
        for cand in rate_card.search(want, limit=8):
            cand_name = _clean_card_name(cand.get("product") or "")
            ck = _norm(cand_name)
            if ck and (ck == key or key in ck):
                return cand_name
    except Exception as exc:                            # noqa: BLE001
        logger.info("rate card lookup failed for %r: %s", name, exc)
    return want


def _num(v, cap: float = 10_000_000.0) -> float:
    """A number out of whatever the model returned, or 0.0.

    Models answer "4500", 4500, "$4,500/mo" and "4500.00" to the same
    question. This is also the boundary where a hallucinated string stops
    being a number that reaches an invoice.
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
    else:
        m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v or ""))
        if not m:
            return 0.0
        try:
            n = float(m.group(0).replace(",", ""))
        except ValueError:
            return 0.0
    return n if 0 < n <= cap else 0.0


def _iso(v) -> str:
    """An ISO date, or "". Anything else the model offers is dropped."""
    t = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(t).isoformat()
    except (ValueError, TypeError):
        return ""


def _ai_read(client: str, text: str, filename: str = "") -> dict | None:
    """The model's reading of the proposal, validated. None when unavailable.

    Never raises. A proposal that cannot be read by the model is still read by
    the patterns, and the caller cannot tell the difference except that fewer
    fields come back.
    """
    try:
        from hub import ai
    except Exception:                                   # noqa: BLE001
        return None
    if not ai.ready():
        return None

    body = (text or "")[:AI_MAX_CHARS]
    if len(body.strip()) < 40:
        return None

    catalogue = _catalogue()
    prompt = _AI_SCHEMA + ("\n".join(f"  - {n}" for n in catalogue)
                           if catalogue else "  (no catalog available)")
    if len(text) > AI_MAX_CHARS:
        prompt += ("\n\nNote: the document was truncated. Report only what "
                   "appears in the text given.")

    try:
        out = ai.chat_json(
            [{"role": "system", "content": _AI_SYSTEM},
             {"role": "user", "content": prompt},
             {"role": "user", "content":
              f"Proposal{' (' + filename + ')' if filename else ''}"
              f"{' for ' + client if client else ''}:\n\n{body}"}],
            module="io_builder", purpose="proposal_read",
            max_tokens=1200,
            # Reading a document is not a creative task: the same proposal
            # should produce the same numbers twice.
            temperature=0)
    except Exception as exc:                            # noqa: BLE001
        logger.info("AI proposal read unavailable: %s", exc)
        return None

    if not isinstance(out, dict):
        return None

    products = []
    for item in (out.get("products") or [])[:25]:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name") or "").strip()[:120]
        if not nm:
            continue
        products.append({"name": nm,
                         "monthly": _num(item.get("monthly")),
                         "note": str(item.get("note") or "").strip()[:200],
                         "start": _iso(item.get("start_date")),
                         "end": _iso(item.get("end_date")),
                         "dates_note": str(item.get("dates_note")
                                           or "").strip()[:120]})

    def txt(key, limit=200):
        return str(out.get(key) or "").strip()[:limit]

    months = int(_num(out.get("term_months"), cap=120))
    return {
        "business_name": txt("business_name"),
        "website": txt("website", 300),
        "contact_name": txt("contact_name", 120),
        "contact_email": txt("contact_email", 200),
        "contact_phone": txt("contact_phone", 40),
        "geography": txt("geography", 300),
        "objectives": txt("objectives", 600),
        "monthly_budget": _num(out.get("monthly_budget")),
        "campaign_total": _num(out.get("campaign_total")),
        "term_months": months if 0 < months <= 120 else 0,
        "start_date": _iso(out.get("start_date")),
        "end_date": _iso(out.get("end_date")),
        "products": products,
        "ambiguities": [str(q).strip()[:200]
                        for q in (out.get("ambiguities") or [])[:8]
                        if str(q).strip()],
    }


def _read_flight(body: str) -> tuple[str, str]:
    """Start and end dates the document actually states, as ISO strings.

    Returns ``("", "")`` when it says nothing -- which is a real answer and
    must stay distinguishable from a date this module computed.

    Only unambiguous forms are accepted. A bare "10/01/26" is not read,
    because US and international ordering disagree about it and a campaign
    that starts in October versus January is not a difference to guess at.
    """
    if not body:
        return "", ""
    sep = r"(?:through|thru|to|until|[-\u2013\u2014])"

    # ISO, the only fully unambiguous numeric form.
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*" + sep + r"\s*(\d{4}-\d{2}-\d{2})",
                  body, re.I)
    if m:
        return _iso_or_blank(m.group(1)), _iso_or_blank(m.group(2))

    # "October 1, 2026 through September 30, 2027" -- the month is named, so
    # the ordering cannot be misread.
    named = (r"([A-Z][a-z]{2,8})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")
    m = re.search(named + r"\s*" + sep + r"\s*" + named, body, re.I)
    if m:
        return (_named_to_iso(m.group(1), m.group(2), m.group(3)),
                _named_to_iso(m.group(4), m.group(5), m.group(6)))

    # A start on its own, labelled.
    m = re.search(r"(?:start(?:s|ing)?|launch(?:es|ing)?|flight)\s*"
                  r"(?:date)?\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})", body, re.I)
    if m:
        return _iso_or_blank(m.group(1)), ""
    m = re.search(r"(?:start(?:s|ing)?|launch(?:es|ing)?|flight)\s*"
                  r"(?:date)?\s*[:\-]?\s*" + named, body, re.I)
    if m:
        return _named_to_iso(m.group(1), m.group(2), m.group(3)), ""
    return "", ""


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _named_to_iso(month: str, day: str, year: str) -> str:
    idx = _MONTHS.get(str(month)[:3].lower())
    if not idx:
        return ""
    try:
        return date(int(year), idx, int(day)).isoformat()
    except ValueError:
        return ""


def _iso_or_blank(value: str) -> str:
    """A real calendar date, or nothing. 2026-13-45 parses as text and not as
    a date, and passing it on would put it straight onto an insertion order."""
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        return ""


def _add_months(start: date, months: int) -> date:
    """The last day of a term of `months` beginning on `start`.

    Month arithmetic done as a running month count, because doing it as
    `year + months // 12` with the month taken modulo 12 loses a whole year
    whenever the month wraps: a 6-month term from 2026-09-01 came back ending
    2026-02-28 — an insertion order whose end date is before its start. It
    only shows on terms that cross a December, which is why it survived.
    """
    total = start.year * 12 + (start.month - 1) + months
    nxt = date(total // 12, total % 12 + 1, 1)
    return nxt - timedelta(days=1)


def from_proposal(client: str, text: str, filename: str = "") -> dict:
    """Read a proposal for anything that belongs on an IO.

    Deliberately conservative. A proposal is a sales document — it contains
    ranges, options and language like "starting at", none of which is a
    committed number. Everything extracted is marked `needs_review` and the
    rep confirms it. Filling an IO from a proposal without review is how a
    client gets billed a figure nobody agreed.
    """
    fields, sources, found = {}, {}, []
    body = re.sub(r"\s+", " ", text or "")

    # Whitespace either side of the separator. Written without it, this missed
    # "$8,500 / month" -- the spaces around the slash broke the match -- and
    # fell back to the largest LINE ITEM instead of the stated total. That is
    # the worst shape of failure this file can produce: not a blank, but a
    # plausible smaller number, on the field the whole IO is priced from.
    money = re.findall(r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:/|per)?\s*(mo|month|monthly)?",
                       body, re.I)
    monthlies = [float(m[0].replace(",", "")) for m in money if m[1]]
    if monthlies:
        fields["monthly_budget"] = f"{max(monthlies):,.2f}"
        sources["monthly_budget"] = "needs_review"
        found.append(f"monthly budget ${max(monthlies):,.2f}")

    for label, pattern in (
        ("Advanced TV", r"advanced\s+tv|connected\s+tv|\bctv\b"),
        ("Meta", r"\bmeta\b|facebook|instagram"),
        ("Search Engine Marketing", r"\bsem\b|pay per click|\bppc\b|paid search"),
        ("Website SEO", r"\bseo\b|search engine optimi"),
        ("Display", r"\bdisplay\b|programmatic"),
        ("Social Ads", r"social (?:media )?(?:ads|advertising)"),
        ("YouTube", r"youtube|trueview"),
        ("Digital Audio", r"digital audio|streaming audio|podcast"),
    ):
        if re.search(pattern, body, re.I):
            found.append(label)
    if found:
        fields["products_mentioned"] = ", ".join(
            f for f in found if not f.startswith("monthly"))
        sources["products_mentioned"] = "needs_review"

    m = re.search(r"\b(\d{1,2})[-\s]?(?:month|mo)\b", body, re.I)
    months = int(m.group(1)) if m else 0
    if m:
        fields["term_months"] = str(months)
        sources["term_months"] = "needs_review"

    # A flight the document actually states beats one computed from today.
    # This used to skip straight to the computation, so a proposal reading
    # "2026-10-01 through 2027-09-30" produced next month's first instead --
    # printed to the rep under the heading "From the proposal I read", which
    # is the one place an invented value must never appear.
    read_start, read_end = _read_flight(body)
    if read_start:
        fields["start_date"] = read_start
        sources["start_date"] = "needs_review"
        if read_end:
            fields["end_date"] = read_end
            sources["end_date"] = "needs_review"
        elif months:
            fields["end_date"] = _add_months(
                date.fromisoformat(read_start), months).isoformat()
            sources["end_date"] = "assumed"
    elif months:
        # Nothing stated. Still worth offering a sensible start, but it is a
        # suggestion and is labelled as one -- see the note assembled below.
        start = date.today().replace(day=1) + timedelta(days=32)
        start = start.replace(day=1)
        fields["start_date"] = start.isoformat()
        fields["end_date"] = _add_months(start, months).isoformat()
        sources["start_date"] = sources["end_date"] = "assumed"

    if client:
        base = from_client(client)
        for k, v in base["fields"].items():
            fields.setdefault(k, v)
            sources.setdefault(k, base["sources"].get(k, "known"))

    ask = [
        "Is this the agreed budget, or was it a range in the proposal?",
        "Which of these products are actually being bought?",
        "What start date did the client agree to?",
    ]
    if not client:
        ask.append("This isn't an existing client — what's the business name, "
                   "contact and website?")

    read_by = "patterns"
    # Two figures for the same thing, an option the document left open: these
    # used to join `ask` and be printed as bullets under the summary, which is
    # where the whole decision went to die. A conflict is a QUESTION -- it has
    # a shape, it has answers, and the interview asks it before it asks
    # anything else. `ask` keeps only what is genuinely a note.
    conflicts: list[dict] = []
    ai = _ai_read(client, text, filename)
    if ai:
        read_by = "ai"
        _merge_ai(ai, fields, sources, found, ask, client, conflicts)

    return {
        "mode": "proposal", "client": client or "", "filename": filename,
        "fields": fields, "sources": sources,
        "found": found,
        "ask": ask,
        "conflicts": conflicts,
        "read_by": read_by,
        "note": ("Read from the proposal and NOT confirmed. A proposal carries "
                 "options and ranges, not committed numbers — check every "
                 "figure before submitting."
                 + (" The campaign dates are a suggested flight, not something "
                    "the document stated."
                    if sources.get("start_date") == "assumed" else "")),
    }


def _merge_ai(ai: dict, fields: dict, sources: dict, found: list,
              ask: list, client: str, conflicts: list | None = None) -> None:
    """Fold the model's reading into what the patterns already found.

    Mutates in place, because the caller owns the payload and this is the only
    thing that writes to it after the pattern pass.

    The ordering rule throughout: a value the patterns found is not replaced.
    Where the two disagree on money the difference becomes a question rather
    than a decision, since the whole failure this feature exists to prevent is
    an IO carrying a figure nobody agreed to.
    """
    if conflicts is None:
        conflicts = []

    def put(key, value, *, note=""):
        """Set a field the patterns did not fill. Always needs_review."""
        if value in (None, "", 0, 0.0):
            return
        if fields.get(key):
            return
        fields[key] = value
        sources[key] = "needs_review"
        if note:
            found.append(note)

    # --- money ---------------------------------------------------------
    ai_monthly = ai.get("monthly_budget") or 0.0
    pat_monthly = 0.0
    if fields.get("monthly_budget"):
        try:
            pat_monthly = float(str(fields["monthly_budget"]).replace(",", ""))
        except ValueError:
            pat_monthly = 0.0

    def _close(a, b):
        """Same figure, allowing for rounding. A percentage rather than a flat
        amount: $50 apart matters on a $500 budget and is noise on $50,000."""
        return a and b and abs(a - b) / max(a, b) <= 0.01

    if ai_monthly and pat_monthly and not _close(ai_monthly, pat_monthly):
        # The pattern pass takes the largest figure written as "$X per month",
        # which on a proposal that lists channels line by line is the biggest
        # LINE, not the budget. When its number matches one of the products the
        # model itemised, that is what happened — the model read the total and
        # the patterns read a row of the table. Prefer the total, and say so,
        # because leaving the headline number as a single channel is a wrong
        # answer sitting in the field a rep is most likely to accept.
        line_items = [p.get("monthly") or 0.0 for p in (ai.get("products") or [])]
        if any(_close(pat_monthly, m) for m in line_items):
            fields["monthly_budget"] = f"{ai_monthly:,.2f}"
            sources["monthly_budget"] = "needs_review"
            conflicts.append({
                "field": "monthly_budget", "kind": "total_vs_line",
                "question": (f"The proposal totals ${ai_monthly:,.2f}/month "
                             f"across its channels, one of which is "
                             f"${pat_monthly:,.2f}. Which figure is the agreed "
                             f"monthly budget?"),
                "options": [f"{ai_monthly:,.2f}", f"{pat_monthly:,.2f}"],
                "suggested": f"{ai_monthly:,.2f}"})
        else:
            # Genuinely two different figures. Neither is chosen here: that is
            # the difference between a quote and a bill.
            conflicts.append({
                "field": "monthly_budget", "kind": "two_figures",
                "question": (f"The proposal reads as ${pat_monthly:,.2f}/month "
                             f"in one place and ${ai_monthly:,.2f}/month in "
                             f"another. Which is the agreed monthly budget?"),
                "options": [f"{pat_monthly:,.2f}", f"{ai_monthly:,.2f}"],
                "suggested": ""})
    elif ai_monthly:
        put("monthly_budget", f"{ai_monthly:,.2f}",
            note=f"monthly budget ${ai_monthly:,.2f}")

    if ai.get("campaign_total"):
        put("campaign_total", f"{ai['campaign_total']:,.2f}",
            note=f"campaign total ${ai['campaign_total']:,.2f}")

    # --- term and dates ------------------------------------------------
    if ai.get("term_months"):
        put("term_months", str(ai["term_months"]),
            note=f"{ai['term_months']}-month term")
    put("start_date", ai.get("start_date"))
    put("end_date", ai.get("end_date"))

    # --- who ------------------------------------------------------------
    # Only when we do not already know them. For an existing client the Hub's
    # own record is the better source and from_client has already set it.
    put("client_name", ai.get("business_name"))
    put("website", ai.get("website"))
    put("client_contact_name", ai.get("contact_name"))
    put("client_contact_email", ai.get("contact_email"))
    put("client_contact_phone", ai.get("contact_phone"))
    put("geo", ai.get("geography"))
    put("objectives", ai.get("objectives"))

    # --- products -------------------------------------------------------
    # The model maps the proposal's wording onto our rate card, so a name that
    # matches the card is used as the card spells it. Anything else is kept
    # verbatim rather than dropped: an unmatched product is still something
    # the client was quoted, and losing it silently is worse than showing it.
    detail, names = [], []
    for item in ai.get("products") or []:
        name = _card_name(item["name"])
        if name.lower() in [n.lower() for n in names]:
            continue
        names.append(name)
        detail.append({"product": name, "monthly": item.get("monthly") or 0.0,
                       "note": item.get("note", ""),
                       "start": item.get("start", ""),
                       "end": item.get("end", ""),
                       "dates_note": item.get("dates_note", "")})

    if detail:
        fields["products_detail"] = detail
        sources["products_detail"] = "needs_review"
        # The model's list REPLACES the keyword sweep rather than joining it.
        # They use different vocabularies for the same thing — the sweep says
        # "Advanced TV" where the model says "Connected TV - Targeted" — so
        # merging them lists one product twice and turns three channels into
        # five. A count a rep cannot trust is worse than the shorter list, and
        # the model read the document while the sweep matched words in it.
        fields["products_mentioned"] = ", ".join(names)
        sources["products_mentioned"] = "needs_review"
        found[:] = [f for f in found if f.startswith("monthly budget")
                    or f.startswith("campaign total")
                    or f.endswith("-month term")]
        found.extend(names)

    # --- what the model could not settle --------------------------------
    # Its questions go after ours: ours are the ones that always need asking,
    # its are specific to this document.
    for q in ai.get("ambiguities") or []:
        if q not in ask:
            ask.append(q)


# ---------------------------------------------------------------------------
# Flights the document disagrees with itself about
#
# A proposal quotes a campaign and then qualifies half its lines: "Enhanced SEO
# + AI Optimization — $1,400/mo (September - October 2026)", "Meta In-Market
# Home Buyers — $750/mo (September - November)", "ChatGPT / AI Advertising —
# $400/mo (Beginning November)". The header above them says the campaign runs
# September through December.
#
# Read as one flight, every one of those products bills for the wrong number of
# months, and the IO looks right while it does it. Read as nothing, they are
# dropped and the rep retypes them from the PDF.
#
# So the disagreements are collected and *asked*, one per product, with the
# document's own wording quoted back. Two of them are genuinely different
# questions and the distinction is the point:
#
#   stated    the document gave a real date pair for this product and it is
#             not the campaign's. Confirm it -- that is usually deliberate.
#   unresolved  the document gave a window with no year, or an open end
#             ("Beginning November"). There is nothing here to confirm; the
#             rep supplies the dates. A month name turned into a date by this
#             module is a launch date nobody agreed.
# ---------------------------------------------------------------------------

def flight_questions(fields: dict) -> list[dict]:
    """Per-product flights that do not match the campaign, as questions."""
    fields = fields or {}
    detail = fields.get("products_detail") or []
    c_start = str(fields.get("start_date") or "")
    c_end = str(fields.get("end_date") or "")
    out = []
    for row in detail:
        if not isinstance(row, dict):
            continue
        name = str(row.get("product") or "").strip()
        if not name:
            continue
        start, end = str(row.get("start") or ""), str(row.get("end") or "")
        note = str(row.get("dates_note") or "").strip()
        if start and end:
            if start == c_start and end == c_end:
                continue                    # the campaign's own flight
            out.append({
                "product": name, "kind": "stated",
                "start": start, "end": end, "note": note,
                "question": (f'The proposal runs {name} from {start} to {end}'
                             + (f' ("{note}")' if note else "")
                             + (f", not the campaign's {c_start} to {c_end}."
                                if c_start and c_end else ".")
                             + " Are those the dates for this product?"),
            })
        elif note:
            out.append({
                "product": name, "kind": "unresolved",
                "start": start, "end": end, "note": note,
                "question": (f'The proposal says "{note}" against {name}, '
                             f"which isn't a date I can put on an IO. What "
                             f"start and end dates should this product run?"),
            })
    return out


# ---------------------------------------------------------------------------
# What a new IO might be replacing
#
# A rep starting a "New IO" for an existing client is, most of the time,
# replacing one that is already running -- a mid-term change of products or
# budget. Nothing asked, so both orders stayed open: the old one kept billing
# its old line-up and the new one billed beside it. That reads as a client
# spending twice what they agreed, and it is discovered in accounting rather
# than here.
#
# Two facts are needed and only one of them is on file: WHICH order this
# replaces, and WHEN that order should stop. The second is never assumed --
# "the day before the new one starts" is the common answer and is wrong every
# time a campaign overlaps deliberately.
# ---------------------------------------------------------------------------

def open_ios(client: str) -> dict:
    """The client's insertion orders, newest first, for the replace question."""
    if not str(client or "").strip():
        return {"client": "", "ios": [], "note": "No client named."}
    try:
        from hub.knack_products import for_client
    except Exception as exc:                            # noqa: BLE001
        return {"client": client, "ios": [],
                "error": f"Product data unavailable ({type(exc).__name__})."}
    try:
        data = for_client(client) or {}
    except Exception as exc:                            # noqa: BLE001
        # "We could not read Knack" and "this client has no orders" are
        # different answers and only one of them means stop asking.
        return {"client": client, "ios": [], "error": str(exc)[:200]}

    by_io: dict[str, list] = {}
    for p in data.get("products") or []:
        key = str(p.get("io") or "").strip()
        if key:
            by_io.setdefault(key, []).append(p)

    ios = []
    for number, lines in by_io.items():
        starts = [str(x.get("start") or "") for x in lines if x.get("start")]
        ends = [str(x.get("end") or "") for x in lines if x.get("end")]
        ios.append({
            "io": number,
            "start": min(starts) if starts else "",
            "end": max(ends) if ends else "",
            "products": len(lines),
            "monthly": round(sum(float(x.get("monthly") or 0)
                                 for x in lines), 2),
            "label": (f"IO {number} — {len(lines)} product"
                      f"{'' if len(lines) == 1 else 's'}"
                      + (f", {min(starts)} to {max(ends)}"
                         if starts and ends else "")),
        })
    ios.sort(key=lambda r: (r["start"], r["io"]), reverse=True)
    return {"client": client, "ios": ios, "count": len(ios),
            "note": ("Orders already on file for this client. Replacing one "
                     "needs the date it should stop — nothing here assumes "
                     "it.")}
