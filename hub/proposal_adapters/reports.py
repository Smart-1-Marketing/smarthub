"""Adapter: ``reporting_plan`` -> a real client dashboard and real budget
lines in ``modules/reports``, the module that already paces spend, prices
platform markup and serves a client their own report link.

``reporting_plan`` used to run as ``brief``: a paragraph describing what a
reporting cadence should look like, with nothing anywhere that a client
could actually be sent. ``modules/reports`` already answers this -- a
``ReportLink`` is the client-facing dashboard token
(``/reports/r/c/<token>``) and a ``BudgetLine`` is what that dashboard's
pacing is measured against -- so this adapter mints the one and writes the
other, straight through ``modules.reports.store``, the same functions the
module's own Budgets and Client Links screens call.

**One link, one line per channel this proposal actually sells.** Not per
task: ``reporting_plan`` has no ``channel=`` of its own and runs once per
run, after every channel's own plan task, so it reads the whole mix off
``run.analysis()["channels"]`` in one pass rather than being invoked once
per channel. A channel with no parseable dollar figure in its proposal text
is left out and named, never guessed at with a number nobody quoted --
`hub/audit_summary.py`'s rule about a figure this Hub did not measure,
applied to a budget line instead.

**Idempotent the way `tracking_plan` already is.** A retry (the queue's own
retry-on-``ValueError``, or a person re-running a completed task by hand)
must not mint a second live link or double every budget line: the link is
reused if this client already has one (`store.link_for_client`), the same
guarantee `create_link()`'s own docstring makes about a client asking for a
second link -- and a budget line already recorded for this client, this
product and this exact monthly figure is left alone rather than duplicated,
the same "already existed and were left as-is" shape `utm.py` uses against
the UTM book.

**Filed under the module's own key.** ``store.resolve_client()`` is the
reading the Budgets and Client Links screens use, so the link and the lines
land under the same spelling the auto-mapper files the client's campaigns
under (``d:acme.com``, or a name key when the registry cannot see the
client). For a release this adapter filed them under ``run.client`` -- the
display name -- because the resolver lived in the Flask app and this code
has no request; every reader that takes a key then found one of the two.
A link or a line already sitting under the display name is reused, never
re-minted, and nothing new goes under it.

``execution_mode="auto"``: nothing here is client-facing copy or a live
campaign change -- a dashboard link and a budget figure to pace against are
housekeeping a person reviews on the Reports screens, not a document a
client reads unless somebody chooses to hand them the link.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from hub.proposal_execution import Adapter, register_adapter

_MONEY_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def _first_amount(budgets) -> Decimal | None:
    """The first dollar figure in a channel's own ``budgets`` list -- the
    proposal's own quoted text, e.g. ``"$2,000"`` or ``"$2,000/mo"``. Never
    a total or an average across several quoted figures: a channel proposed
    at "$1,500 - $2,500" should be re-typed on the Reports budgets screen by
    a person choosing which end applies, not averaged by this adapter."""
    for raw in budgets or []:
        match = _MONEY_RE.search(str(raw or ""))
        if not match:
            continue
        try:
            amount = Decimal(match.group(0).replace(",", ""))
        except InvalidOperation:
            continue
        if amount > 0:
            return amount
    return None


def run(run, task):
    from modules.reports import store as reports_store

    channels = [c for c in (run.analysis().get("channels") or []) if isinstance(c, dict)]
    if not channels:
        raise ValueError(
            "There is no channel mix on this proposal's analysis yet, so there "
            "is nothing to set up a reporting dashboard for. Re-run analysis, "
            "then re-run this task.")

    priced, unpriced = [], []
    for channel in channels:
        product = str(channel.get("name") or channel.get("key") or "").strip()
        if not product:
            continue
        amount = _first_amount(channel.get("budgets"))
        if amount is None:
            unpriced.append(product)
            continue
        priced.append((product, amount))

    if not priced:
        raise ValueError(
            "None of this proposal's channels carry a dollar figure to pace "
            "against yet. Add one under Budget & flight calendar, then re-run "
            "this task.")

    # Filed under the module's own key -- the same reading the Budgets and
    # Client Links screens use -- so the client's campaigns, their link and
    # their budget lines sit under one spelling. run.client is the Hub
    # client name; the registry resolves it to `d:<domain>` where it can
    # and a name key where it cannot, and either is a key the module's
    # every reader finds.
    key, display = reports_store.resolve_client(run.client, "")
    display = display or run.client

    # A link or a line minted before this resolver existed sits under the
    # raw display name. It is reused rather than re-minted: a second live
    # link for one client is the thing create_link() exists to prevent,
    # and a second line for one product and figure doubles the pacing.
    # Nothing is moved -- the card and the staff screens read both
    # spellings -- but nothing new is written under the old one either.
    # Found by the display name rather than by guessing the old key: the
    # name is stored on every link and line and is the one field both
    # spellings share. It also covers a registry that answers differently
    # between two runs -- Knack down on the retry resolves the client to a
    # name key, and a link minted under the domain key yesterday must still
    # be the one reused rather than a second live link under the new key.
    link = reports_store.link_for_client(key)
    if link is None:
        named = reports_store.links_named(display)
        link = named[0] if named else None
    if link is None:
        link = reports_store.create_link(
            key, client_name=display, created_by="proposal-execution")

    existing = reports_store.budget_lines_for(key)
    seen_ids = {b["id"] for b in existing}
    existing += [b for b in reports_store.budget_lines_named(display) if b["id"] not in seen_ids]
    # Compared as amounts, not as strings: the stored column is Numeric(12,2)
    # and reads back "500.00" against a freshly-parsed Decimal("500") -- the
    # same figure, two different strings, which would have minted a
    # duplicate line on every retry.
    already = {(b["product"], Decimal(str(b["monthly_budget"])).quantize(Decimal("0.01")))
               for b in existing}

    created, kept = [], []
    for product, amount in priced:
        if (product, amount.quantize(Decimal("0.01"))) in already:
            kept.append(product)
            continue
        reports_store.add_budget_line(
            client=key, client_name=display, product=product,
            monthly_budget=amount, created_by="proposal-execution",
            notes=f"From Proposal Execution run #{run.id}.",
            source={"proposal_execution_run": run.id})
        created.append(product)

    return {
        "summary": (f"Set up cross-channel reporting for {run.client}: "
                    f"{len(created)} budget line(s) added"
                    + (f", {len(kept)} already on file" if kept else "")
                    + (f", {len(unpriced)} channel(s) skipped for lacking a "
                       "quoted figure" if unpriced else "") + "."),
        "token": link.token,
        "artifact_url": f"/reports/r/c/{link.token}",
        "client_key": key,
        # Which spelling the reused link sits under, so a link minted under
        # the display name before the resolver existed is visible as such
        # rather than read as this run having filed it under the key.
        "link_filed_under": link.client,
        "budget_lines_added": created,
        "budget_lines_existing": kept,
        "channels_without_a_figure": unpriced,
        "qa": ["Map each channel's real ad-platform campaign under Reports → "
               "Campaign Mapping so pacing has something to compute against.",
               "Confirm every budget figure against the signed proposal before "
               "sharing the dashboard link with the client."],
    }


register_adapter(Adapter("reports", "Cross-Channel Reporting Dashboard", "auto", run))
