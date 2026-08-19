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

import re
from datetime import date, timedelta

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

    money = re.findall(r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:/|per\s+)?(mo|month|monthly)?",
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
    if m:
        months = int(m.group(1))
        fields["term_months"] = str(months)
        sources["term_months"] = "needs_review"
        start = date.today().replace(day=1) + timedelta(days=32)
        start = start.replace(day=1)
        fields["start_date"] = start.isoformat()
        fields["end_date"] = (start.replace(
            year=start.year + months // 12,
            month=((start.month - 1 + months % 12) % 12) + 1)
            - timedelta(days=1)).isoformat()
        sources["start_date"] = sources["end_date"] = "needs_review"

    if client:
        base = from_client(client)
        for k, v in base["fields"].items():
            fields.setdefault(k, v)
            sources.setdefault(k, base["sources"].get(k, "known"))

    return {
        "mode": "proposal", "client": client or "", "filename": filename,
        "fields": fields, "sources": sources,
        "found": found,
        "ask": [
            "Is this the agreed budget, or was it a range in the proposal?",
            "Which of these products are actually being bought?",
            "What start date did the client agree to?",
        ] + ([] if client else [
            "This isn't an existing client — what's the business name, "
            "contact and website?"]),
        "note": ("Read from the proposal and NOT confirmed. A proposal carries "
                 "options and ranges, not committed numbers — check every "
                 "figure before submitting."),
    }
