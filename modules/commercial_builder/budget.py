"""Fail-closed project limits. Reservations are ceilings, never invoice totals."""
import math
import os
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from werkzeug.exceptions import Conflict
from .db import db
from .finishing_models import ProjectBudget, BudgetReservation


class BudgetBlocked(Conflict):
    description = "Paid generation is paused by this project's spending limit."


def cents(value):
    if isinstance(value, bool):
        raise ValueError("Enter a valid dollar amount.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 100000:
            raise ValueError("Enter a dollar amount between 0 and 100,000.")
        return int((amount * 100).to_integral_value(rounding=ROUND_CEILING))
    except InvalidOperation:
        raise ValueError("Enter a valid dollar amount.") from None


def status(project_id):
    row = db.session.get(ProjectBudget, project_id)
    if not row:
        return {"configured": False, "note": "No project spending limit set."}
    remaining = None if row.prior_cents is None else max(0, row.limit_cents - row.prior_cents - row.reserved_cents) / 100
    return {"configured": True, "limit_usd": row.limit_cents / 100,
            "prior_usd": None if row.prior_cents is None else row.prior_cents / 100,
            "reserved_usd": row.reserved_cents / 100, "remaining_usd": remaining,
            "note": "Reservations use a configured maximum credit price and remain held even if a request fails. They are not actual charges. Unpriced generation is blocked while a limit is active."}


def reject_unpriced(project_id):
    if db.session.get(ProjectBudget, project_id):
        raise BudgetBlocked("This paid action has no configured cost ceiling. It is paused to protect the project spending limit. Reuse saved media or configure pricing before generating.")


def reserve_render(project, source):
    row = db.session.get(ProjectBudget, project.id)
    if not row:
        return
    if row.prior_cents is None:
        raise BudgetBlocked("Past spending is unknown. Reconcile the project's earlier provider charges before another paid render.")
    try:
        rate = Decimal(os.environ.get("CREATOMATE_MAX_USD_PER_CREDIT", ""))
        if not rate.is_finite() or rate <= 0:
            raise InvalidOperation
    except InvalidOperation:
        raise BudgetBlocked("A maximum Creatomate dollar price per credit is not configured. Paid rendering is paused to protect the spending limit.") from None
    # This route sends full-resolution MP4 at the source's explicit frame rate.
    frames = Decimal(str(source["duration"])) * Decimal(str(source.get("frame_rate", 25)))
    credits = math.ceil(Decimal(source["width"]) * Decimal(source["height"]) * frames / 100000000)
    amount = cents(max(1, credits) * rate)
    updated = ProjectBudget.query.filter(
        ProjectBudget.project_id == project.id, ProjectBudget.prior_cents.isnot(None),
        ProjectBudget.prior_cents + ProjectBudget.reserved_cents + amount <= ProjectBudget.limit_cents
    ).update({ProjectBudget.reserved_cents: ProjectBudget.reserved_cents + amount}, synchronize_session=False)
    if not updated:
        db.session.rollback()
        raise BudgetBlocked("This render would exceed the project's remaining spending limit. No render was submitted.")
    db.session.add(BudgetReservation(project_id=project.id, operation="creatomate_render", cents=amount))
    db.session.commit()  # Reserve durably before the external request, including uncertain failures.
