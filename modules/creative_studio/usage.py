"""One shared writer for `cs_usage_logs` -- WO-CS4's own words: "Every
OpenAI, Runway, Pexels/Pixabay/Unsplash call writes one row." A single
function rather than each job runner building a `CsUsageLog` row by hand,
for the reason every shared writer in this Hub exists: the day a sixth
provider is added, or the cost table gains real numbers, there is one place
that has to change.
"""
from __future__ import annotations

from . import config
from .db import db
from .models import CsUsageLog


def record(provider: str, service: str, *, project_id: int | None = None,
          client_name: str = "", quantity: float = 1.0, unit: str = "call",
          ok: bool = True, actor: str = "") -> CsUsageLog:
    """Write one usage row. Never raises past its own transaction -- a usage
    log that could take a generation job down over a cost estimate would be
    the confident-wrong-answer failure this codebase spends its own history
    undoing, from the wrong direction: metering must not be able to break the
    thing it is metering.

    `estimated_cost` is `None` -- printed as *not measured* rather than a
    silent zero -- whenever `(provider, service)` is not in
    `config.PROVIDER_RATES` yet.
    """
    rate = config.PROVIDER_RATES.get((provider, service))
    cost = round(rate * quantity, 4) if rate is not None else None
    row = CsUsageLog(project_id=project_id, client_name=client_name or "",
                     provider=provider, service=service, quantity=quantity,
                     unit=unit or "call", estimated_cost=cost, ok=bool(ok),
                     created_by=actor or "")
    db.session.add(row)
    db.session.commit()
    return row


def tiles(rows) -> dict:
    """The five figures the Usage & Costs dashboard shows -- WO-CS6 item 4.

    `total_generations` counts every row, refused calls included: a wall of
    refusals is what a spent allowance looks like from this side, the rule
    `hub/quotas.py` already states about a refused OpenAI call, so dropping
    them would understate what was actually attempted. The other three count
    one `service` each. `estimated_cost` is `None`, not a silent zero, the
    moment any one row could not be priced -- the same rule
    `totals_by_provider()` already carries, so the two screens cannot
    disagree about what "not measured" means.
    """
    rows = list(rows or [])

    def _count(service: str) -> int:
        return sum(1 for r in rows if r.service == service)

    measured = all(r.estimated_cost is not None for r in rows) if rows else True
    cost = sum(r.estimated_cost for r in rows if r.estimated_cost is not None)
    return {
        "total_generations": len(rows),
        "videos_rendered": _count("render"),
        "images_generated": _count("image"),
        "voiceovers_generated": _count("voice"),
        "estimated_cost": cost if measured else None,
        "measured": measured,
    }


def totals_by_provider(rows) -> list[dict]:
    """One row per provider: calls, quantity, and a total that is `None`
    (not measured) the moment any one row that provider wrote could not be
    priced -- summing `None` as zero would understate the total and print a
    confident, wrong number, the rule `hub/website_audit.py` states about a
    total that excludes something."""
    by_provider: dict[str, dict] = {}
    for r in rows:
        d = by_provider.setdefault(r.provider, {"provider": r.provider,
                                                 "calls": 0, "quantity": 0.0,
                                                 "estimated_cost": 0.0,
                                                 "measured": True})
        d["calls"] += 1
        d["quantity"] += r.quantity or 0
        if r.estimated_cost is None:
            d["measured"] = False
        elif d["measured"]:
            d["estimated_cost"] += r.estimated_cost
    out = []
    for d in by_provider.values():
        d["estimated_cost"] = d["estimated_cost"] if d["measured"] else None
        out.append(d)
    return sorted(out, key=lambda d: d["provider"])
