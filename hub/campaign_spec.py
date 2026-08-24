"""One campaign shape, shared by the Proposal Builder and the IO Builder.

## The problem

The two tools look disconnected because they are. The IO Builder holds 42
structured fields — client, geography, audience, products, budgets, flight
dates, tracking, creative. The Proposal Builder holds a customer, an industry,
and a list of prose blocks.

So a proposal contains no campaign data. Nothing in it can become an IO,
because there is nothing structured to carry: the numbers exist only inside
sentences. Today converting a proposal means a person reads it and retypes
everything into the IO intake, which is where the transcription errors and the
"the proposal said $4,500 but the IO says $4,000" arguments come from.

## The fix

A `CampaignSpec` is the single shape both tools read and write. A proposal
becomes a spec plus persuasive copy; an IO becomes the same spec plus a rate
card, signature and order number. Converting is then loading, not retyping.

Deliberately:

  * **Everything is optional.** A proposal legitimately has no order number,
    no signature and vague dates. Requiring them would make the spec unusable
    at the stage it matters most.
  * **Every field records its provenance.** `known` came from our records,
    `proposed` was offered to the client but not agreed, `agreed` was signed
    off. An IO must never treat a proposed number as agreed — that is the
    difference between a quote and a bill.
  * **A spec is never silently promoted.** Converting proposal → IO returns
    what still needs confirming rather than filling it in and hoping.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

# How sure we are about a value, and therefore what may be done with it.
KNOWN = "known"          # from our own records
PROPOSED = "proposed"    # offered to the client, not agreed
AGREED = "agreed"        # signed off, safe to bill
NEEDS = "needs_review"   # guessed, or read out of a document

SPEND_SAFE = (AGREED,)   # the only state an IO may bill against


@dataclass
class LineItem:
    """One product on a campaign. Same shape in a proposal and an IO."""
    product: str = ""
    category: str = ""
    monthly: float = 0.0
    months: int = 0
    total: float = 0.0
    start: str = ""
    end: str = ""
    geo: str = ""
    notes: str = ""
    confidence: str = PROPOSED

    def compute_total(self) -> float:
        """Total from monthly x months when it wasn't given.

        Never overwrites a stated total: a negotiated campaign total is often
        deliberately not monthly x months, and recalculating it would quietly
        change an agreed number.
        """
        if self.total:
            return self.total
        if self.monthly and self.months:
            return round(self.monthly * self.months, 2)
        return 0.0


@dataclass
class CampaignSpec:
    """Everything both tools need to describe one campaign."""
    spec_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    stage: str = "proposal"          # proposal | agreed | io

    client: str = ""
    organization: str = ""
    website: str = ""
    industry: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""

    campaign: str = ""
    objectives: str = ""
    audience: str = ""
    geo: str = ""
    start: str = ""
    end: str = ""
    months: int = 0

    items: list = field(default_factory=list)
    monthly_total: float = 0.0
    campaign_total: float = 0.0
    management_fee: float = 0.0

    sales: str = ""
    partner: str = ""
    trafficker: str = ""

    tracking: str = ""
    creative: list = field(default_factory=list)
    notes: str = ""

    # field name -> KNOWN | PROPOSED | AGREED | NEEDS
    confidence: dict = field(default_factory=dict)
    source: str = ""                 # where this spec came from
    # True when the document stated a total of its own, rather than the total
    # being rolled up from the line items here. It changes what recalculate()
    # is allowed to overwrite -- see the note there.
    totals_stated: bool = False
    created: str = field(default_factory=lambda: date.today().isoformat())

    # ---- derived ----
    def recalculate(self) -> None:
        """Roll the line items up — without destroying a stated total.

        A proposal often carries a single agreed monthly figure with the
        products listed but not individually priced. Summing those items gives
        zero, and blindly assigning it wiped a $4,500 budget on the first
        round-trip through storage. A rolled-up total only replaces the stored
        one when the items actually carry figures.
        """
        items = [i if isinstance(i, LineItem) else LineItem(**i) for i in self.items]
        self.items = items
        rolled_monthly = round(sum(i.monthly or 0 for i in items), 2)
        rolled_total = round(sum(i.compute_total() for i in items), 2)
        # A stated total is evidence; a rolled-up one is arithmetic on whatever
        # the reader happened to itemise. They differ for ordinary reasons -- a
        # bundle discount, or a product the reader missed -- and when they do,
        # the document wins. This used to be safe by accident: line items never
        # carried money, so the roll-up was always zero. The proposal reader
        # now prices them, which would have made the sum quietly replace the
        # figure printed on the client's proposal.
        if not self.totals_stated:
            if rolled_monthly or not self.monthly_total:
                self.monthly_total = rolled_monthly
            if rolled_total or not self.campaign_total:
                self.campaign_total = rolled_total
        else:
            if not self.monthly_total:
                self.monthly_total = rolled_monthly
            if not self.campaign_total:
                self.campaign_total = rolled_total
        if not self.campaign_total and self.monthly_total and self.months:
            self.campaign_total = round(self.monthly_total * self.months, 2)
        if not self.months and self.start and self.end:
            self.months = _months_between(self.start, self.end)

    def unconfirmed(self) -> list[str]:
        """Fields an IO must not bill against until someone confirms them."""
        out = []
        for key in ("monthly_total", "campaign_total", "start", "end"):
            if self.confidence.get(key, PROPOSED) not in SPEND_SAFE:
                out.append(key)
        for i, item in enumerate(self.items):
            conf = item.confidence if isinstance(item, LineItem) else item.get("confidence")
            if conf not in SPEND_SAFE:
                name = item.product if isinstance(item, LineItem) else item.get("product")
                out.append(f"item:{name or i}")
        return out

    def ready_for_io(self) -> dict:
        """Whether this can become an IO, and what's blocking it."""
        missing = [k for k in ("client", "start", "end") if not getattr(self, k)]
        if not self.items:
            missing.append("items")
        unconfirmed = self.unconfirmed()
        return {
            "ready": not missing and not unconfirmed,
            "missing": missing,
            "unconfirmed": unconfirmed,
            "note": ("Ready to convert." if not missing and not unconfirmed else
                     "A proposal's numbers are offers, not agreements. Each one "
                     "has to be confirmed before an IO bills against it."),
        }

    def to_dict(self) -> dict:
        self.recalculate()
        d = asdict(self)
        d["items"] = [asdict(i) if isinstance(i, LineItem) else i for i in self.items]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignSpec":
        data = dict(data or {})
        items = [LineItem(**i) if isinstance(i, dict) else i
                 for i in (data.pop("items", []) or [])]
        known = {f for f in cls.__dataclass_fields__}
        spec = cls(**{k: v for k, v in data.items() if k in known})
        spec.items = items
        spec.recalculate()
        return spec


def _months_between(start: str, end: str) -> int:
    try:
        s = date.fromisoformat(str(start)[:10])
        e = date.fromisoformat(str(end)[:10])
    except (ValueError, TypeError):
        return 0
    return max(1, (e.year - s.year) * 12 + (e.month - s.month) + 1)


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------

def from_client(client: str) -> CampaignSpec:
    """A spec seeded with everything the Hub already knows."""
    spec = CampaignSpec(client=client, source="client record")
    try:
        from hub.client_context import context
        f = context(client).get("fields", {})
        spec.website = f.get("website", "")
        spec.industry = f.get("industry", "")
        spec.contact_phone = f.get("phone", "")
        spec.contact_email = f.get("email", "")
        spec.address = f.get("address", "")
        spec.city = f.get("city", "")
        spec.state = f.get("state", "")
        spec.zip = f.get("zip", "")
        spec.geo = ", ".join(x for x in (f.get("city"), f.get("state")) if x)
        for k in ("website", "industry", "contact_phone", "contact_email",
                  "address", "city", "state", "zip", "geo"):
            if getattr(spec, k):
                spec.confidence[k] = KNOWN
    except Exception:                                   # noqa: BLE001
        pass
    spec.confidence["client"] = KNOWN
    return spec


def from_last_io(client: str) -> CampaignSpec | None:
    """A spec built from the client's most recent insertion order.

    Everything is marked AGREED because it was — it ran and was billed. A
    renewal still asks whether it's renewing unchanged, but the figures are
    facts rather than offers.
    """
    try:
        from hub.knack_products import for_client
    except Exception:                                   # noqa: BLE001
        return None
    products = (for_client(client) or {}).get("products") or []
    if not products:
        return None

    by_io: dict[str, list] = {}
    for p in products:
        by_io.setdefault(str(p.get("io") or ""), []).append(p)
    latest = max(by_io, key=lambda k: max(str(x.get("start") or "") for x in by_io[k]))
    lines = by_io[latest]

    spec = from_client(client)
    spec.stage = "io"
    spec.source = f"IO {latest}"
    spec.campaign = next((p.get("campaign") for p in lines if p.get("campaign")), "")
    spec.partner = next((p.get("partner") for p in lines if p.get("partner")), "")
    spec.sales = next((p.get("sales") for p in lines if p.get("sales")), "")
    spec.trafficker = next((p.get("trafficker") for p in lines if p.get("trafficker")), "")
    starts = [p.get("start") for p in lines if p.get("start")]
    ends = [p.get("end") for p in lines if p.get("end")]
    spec.start = min(starts) if starts else ""
    spec.end = max(ends) if ends else ""
    spec.items = [LineItem(
        product=p.get("product", ""), category=p.get("kind", ""),
        monthly=float(p.get("monthly") or 0), total=float(p.get("total") or 0),
        start=p.get("start", ""), end=p.get("end", ""), geo=p.get("geo", ""),
        confidence=AGREED) for p in lines]
    for k in ("start", "end", "monthly_total", "campaign_total"):
        spec.confidence[k] = AGREED
    spec.recalculate()
    return spec


def from_quote(proposal_id: str) -> CampaignSpec | None:
    """A spec from a proposal the live builder saved.

    There is one Proposal Builder and it keeps quotes in ``quotes``, not in
    ``modules.proposal_builder.store`` -- that module is the retired tool's
    read-only archive. Looking only there is how a proposal picker ships
    empty for every proposal written since the consolidation while appearing
    to work, which is exactly what happened to the landing maker. Callers
    should try this first and fall back to the archive, because both kinds of
    document are real proposals real clients received.

    Marked PROPOSED, not AGREED: a quote is what was offered. What the client
    actually bought is the question the IO exists to ask.
    """
    if not str(proposal_id or "").strip().isdigit():
        return None
    db = None
    try:
        from modules.sales_builder.app import Quote, SessionLocal
        db = SessionLocal()
        q = db.query(Quote).filter(Quote.id == int(proposal_id)).first()
        if not q:
            return None
        try:
            state = json.loads(q.data or "{}")
        except (ValueError, TypeError):
            state = {}

        spec = from_client(q.client or "") if q.client else CampaignSpec()
        spec.stage = "proposal"
        spec.source = f"proposal {q.quote_number or proposal_id}"
        spec.client = q.client or spec.client

        # The quote's own columns are the campaign as it was sold, so they
        # win over anything the client record inferred.
        for attr, val in (("website", q.website), ("industry", q.industry),
                          ("campaign", state.get("campaign") or q.package),
                          ("objectives", q.goals_summary),
                          ("geo", q.geo_summary)):
            if val:
                setattr(spec, attr, val)
                spec.confidence[attr] = PROPOSED
        for attr in ("city", "state", "audience", "start", "end",
                     "contact_name", "contact_email", "contact_phone"):
            if state.get(attr):
                setattr(spec, attr, state[attr])
                spec.confidence[attr] = PROPOSED

        try:
            spec.monthly_total = float(q.monthly_budget or 0)
            if spec.monthly_total:
                spec.confidence["monthly_total"] = PROPOSED
                spec.totals_stated = True
        except (TypeError, ValueError):
            pass

        # products_summary is the line-up on the proposal the client received.
        # No amount per line, so the money stays on the total rather than
        # being split into figures nobody quoted.
        for name in [p.strip() for p in str(q.products_summary or "").split(",")
                     if p.strip()]:
            spec.items.append(LineItem(product=name, confidence=NEEDS))

        spec.notes = ("Loaded from a saved proposal. The figures are what was "
                      "quoted, not what was agreed.")
        return spec
    except Exception:                                   # noqa: BLE001
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:                           # noqa: BLE001
                pass


def from_proposal_text(text: str, client: str = "") -> CampaignSpec:
    """Read a proposal document into a spec.

    Everything extracted is PROPOSED at best, and NEEDS where it was inferred.
    A proposal contains ranges, options and "starting at" — treating any of it
    as agreed is how a client gets billed a number nobody signed.
    """
    from hub.io_prefill import from_proposal as _read
    parsed = _read(client, text or "")
    spec = from_client(client) if client else CampaignSpec(source="proposal")
    spec.stage = "proposal"
    spec.source = "proposal document"

    f = parsed.get("fields", {})
    if f.get("monthly_budget"):
        try:
            spec.monthly_total = float(str(f["monthly_budget"]).replace(",", ""))
            spec.confidence["monthly_total"] = NEEDS
            spec.totals_stated = True
        except ValueError:
            pass
    if f.get("campaign_total"):
        try:
            spec.campaign_total = float(str(f["campaign_total"]).replace(",", ""))
            spec.confidence["campaign_total"] = NEEDS
            spec.totals_stated = True
        except ValueError:
            pass
    for key in ("start_date", "end_date"):
        if f.get(key):
            setattr(spec, key.replace("_date", ""), f[key])
            spec.confidence[key.replace("_date", "")] = NEEDS
    if f.get("term_months"):
        try:
            spec.months = int(f["term_months"])
        except ValueError:
            pass
    # products_detail carries an amount per product when the reader could find
    # one; products_mentioned is only names. Prefer the detailed list so the
    # line items arrive with money on them rather than as labels a rep has to
    # price again -- which was the whole reason converting meant retyping.
    detail = f.get("products_detail")
    if isinstance(detail, list) and detail:
        for item in detail:
            if not isinstance(item, dict):
                continue
            name = str(item.get("product") or "").strip()
            if not name:
                continue
            try:
                monthly = float(item.get("monthly") or 0)
            except (TypeError, ValueError):
                monthly = 0.0
            spec.items.append(LineItem(product=name, monthly=monthly,
                                       notes=str(item.get("note") or "")[:200],
                                       confidence=NEEDS))
    else:
        for name in [p.strip() for p in (f.get("products_mentioned") or "").split(",") if p.strip()]:
            spec.items.append(LineItem(product=name, confidence=NEEDS))
    spec.notes = ("Read from a proposal document. Nothing here is confirmed."
                  + (" The reader used AI as well as pattern matching."
                     if parsed.get("read_by") == "ai" else ""))
    return spec


def to_io_payload(spec: CampaignSpec) -> dict:
    """The shape the IO Builder's intake consumes.

    Returns what still needs confirming alongside the values, so the intake
    can ask rather than assume. This is the whole point of the shared spec:
    conversion is loading, not retyping.
    """
    spec.recalculate()
    check = spec.ready_for_io()
    return {
        "mode": "from_spec",
        "spec_id": spec.spec_id,
        "fields": {
            "client_name": spec.client, "website": spec.website,
            "client_contact_name": spec.contact_name,
            "client_contact_email": spec.contact_email,
            "client_contact_phone": spec.contact_phone,
            "address": spec.address, "city": spec.city, "state": spec.state,
            "zip": spec.zip, "geo": spec.geo, "industry": spec.industry,
            "campaign": spec.campaign, "objectives": spec.objectives,
            "audience": spec.audience, "start_date": spec.start,
            "end_date": spec.end, "sales": spec.sales,
            "media_partner": spec.partner, "trafficker": spec.trafficker,
            "tracking": spec.tracking,
        },
        "confidence": spec.confidence,
        "products": [asdict(i) if isinstance(i, LineItem) else i for i in spec.items],
        "monthly_total": spec.monthly_total,
        "campaign_total": spec.campaign_total,
        "creative": spec.creative,
        "ready": check["ready"],
        "confirm": check["unconfirmed"],
        "missing": check["missing"],
        "ask": _questions_for(spec, check),
        "note": check["note"],
    }


def _questions_for(spec: CampaignSpec, check: dict) -> list[str]:
    """What the intake should ask, given what isn't confirmed."""
    out = []
    if "monthly_total" in check["unconfirmed"] and spec.monthly_total:
        out.append(f"The proposal showed ${spec.monthly_total:,.2f}/month — "
                   f"is that the agreed figure?")
    if any(u.startswith("item:") for u in check["unconfirmed"]):
        out.append("Which of these products is the client actually buying?")
    if "start" in check["missing"] or "start" in check["unconfirmed"]:
        out.append("What start date has the client agreed to?")
    if not spec.contact_email:
        out.append("Who is the client contact, and what's their email?")
    if not spec.creative:
        out.append("Is creative being supplied, or are we producing it?")

    # The document's total and its own line items disagree. That is usually a
    # bundle price or a product the reader did not pick up, and both are worth
    # a sentence before anyone signs -- an IO that bills the sum of what was
    # read is not the same as one that bills what was quoted.
    priced = [i for i in spec.items
              if isinstance(i, LineItem) and i.monthly]
    if spec.totals_stated and spec.monthly_total and priced:
        rolled = round(sum(i.monthly for i in priced), 2)
        if rolled and abs(rolled - spec.monthly_total) > 1:
            out.append(
                f"The proposal states ${spec.monthly_total:,.2f}/month, but the "
                f"products read off it come to ${rolled:,.2f} — is something "
                f"bundled, discounted, or missing from the list?")
    return out
