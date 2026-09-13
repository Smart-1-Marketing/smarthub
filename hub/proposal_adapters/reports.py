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

    link = reports_store.link_for_client(run.client)
    if link is None:
        link = reports_store.create_link(
            run.client, client_name=run.client, created_by="proposal-execution")

    existing = reports_store.budget_lines_for(run.client)
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
            client=run.client, client_name=run.client, product=product,
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
        "budget_lines_added": created,
        "budget_lines_existing": kept,
        "channels_without_a_figure": unpriced,
        "qa": ["Map each channel's real ad-platform campaign under Reports → "
               "Campaign Mapping so pacing has something to compute against.",
               "Confirm every budget figure against the signed proposal before "
               "sharing the dashboard link with the client."],
    }


register_adapter(Adapter("reports", "Cross-Channel Reporting Dashboard", "auto", run))
