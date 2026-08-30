"""The business inside a QuickBooks line description, where the rules gave up.

## Why the description is all there is

**The client is not the customer.** A domain renewal is invoiced to the media
partner — one invoice to a radio group carries five renewals for five different
businesses — so the only place the client appears is the free-text line
description, typed by a person in whatever shape that day suggested:

    syrons-market.com<TAB>Syrons
    Foreman Mechanical Services, LLC - foremanmechanical.com
    http://friendsofbridges.org/ - Annual renewal

`hub/domain_renewals.parse_description()` reads a domain and a business out of
that, and `hub/sites_billing.match_line()` runs five rules over the description,
the rest of the invoice and the customer name. Between them they answer most
lines — on this deployment's own invoices all 23 renewal lines carry a domain,
which is the strongest identifier there is and the reason neither report is
broken today.

What both keep is an explicit bucket for what they could not join:
`year_to_date()`'s *"charges that match no record here"*, and Sites Billing's
rule that a resemblance is printed and **still counted as unmatched**. Those two
buckets are the only place this module is used, and nowhere else in either
report.

## Only the leftovers, and only as a suggestion

`read_missing()` is handed the descriptions the deterministic rules have
already failed on. A line the rules answered is never sent: it costs a call to
be told what is already known, and it invites a second opinion on a domain
match, which is an identifier rather than a guess.

What comes back is a **name**, grounded in the description it came from by
`hub/name_reading.py` — every word of the answer has to be in the original, so
a tidied or invented business is dropped and counted. That name then goes
through each report's *existing* name pass, against the real registry, under
`hub/client_key.py`'s rules. The model never sees the registry, so it cannot
name a client.

## It resolves to `probable`, and probable is not billed

This is the load-bearing half, and it is already how both reports behave. A
near name in `domain_renewals` comes back `confidence="probable"`, and
`domain_purchase.year_to_date()` counts a probable charge as having **no record
here** — in both directions — until somebody presses `link_charge()`. A model's
reading is held to exactly that: it can move a charge from *nothing to look at*
to *here is a candidate, confirm it*, and it cannot mark a renewal billed.

That matters more here than anywhere else in the Hub, because a charge
attributed to the wrong client's domain does two wrong things at once: it marks
a renewal billed that was not, **and** it hides a real one from the
reconciliation. The report says so in those words already; nothing here changes
it.
"""
from __future__ import annotations

from hub.name_reading import BATCH, MAX_PER_RUN, NameReader, prompt_for

__all__ = ["BATCH", "MAX_PER_RUN", "READER", "business_in", "forget",
           "pending", "read_missing", "reading_for", "readings", "state",
           "worth_reading"]


def worth_reading(text: str) -> str:
    """Why this description is not worth a call, or "" if it is.

    Two answers, both cases the hand-written rules have already settled:

    * a description that is **only a label** — "Annual renewal", "Renewal
      2026" — names no business, and `_is_label()` has said so. Paying a model
      to find one in it invites it to find one, which is the
      `hub/site_names.py` rule about "Main Site" wearing an invoice.
    * a description with **almost nothing in it**. A reference number or a
      date is not a name.
    """
    from hub.domain_renewals import _is_label, parse_description  # noqa: SLF001
    raw = " ".join(str(text or "").split())
    if len(raw) < 4:
        return "the description is too short to name anybody"
    try:
        parsed = parse_description(raw)
    except Exception:                                       # noqa: BLE001
        parsed = {"name": "", "domain": ""}
    # Strip the URL-shaped spans before asking whether what is left is a label:
    # "http://friendsofbridges.org/ - Annual renewal" is a label plus a domain,
    # and the domain is the report's own strongest rule rather than this one's
    # business to read.
    try:
        from hub.client_urls import strip_domains
        remainder = " ".join(strip_domains(raw).split())
    except Exception:                                       # noqa: BLE001
        remainder = raw
    if not parsed.get("name") and _is_label(remainder):
        return "the description is a label, not a business"
    return ""


READER = NameReader(
    folder="invoice_names",
    filename="ai_readings.json",
    module="domain_renewals",
    purpose="invoice_descriptions",
    skip=worth_reading,
    system_prompt=prompt_for(
        "the descriptions people typed on QuickBooks invoice lines, where the "
        "invoice went to a media partner but the line is for one of that "
        "partner's own clients",
        "a website domain, a tab or a dash separating the parts, a legal "
        "suffix like LLC or Inc, the word renewal, and a year."),
)


def readings() -> dict:
    return READER.readings()


def reading_for(text: str, store: dict | None = None) -> dict:
    return READER.reading_for(text, store)


def business_in(text: str, store: dict | None = None) -> str:
    """The business this description was read as naming, or "".

    Re-grounded on read: a file written by an older prompt, or edited by hand,
    must not get past the one rule that makes this safe to feed into a matcher.
    """
    return READER.business_in(text, store)


def pending(descriptions) -> list[str]:
    return READER.pending(descriptions)


def state(descriptions) -> dict:
    return READER.state(descriptions)


def read_missing(descriptions, *, limit: int | None = None) -> dict:
    return READER.read_missing(descriptions, limit=limit)


def forget(text: str = "") -> int:
    return READER.forget(text)
