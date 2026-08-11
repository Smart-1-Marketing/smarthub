import os
from decimal import Decimal, InvalidOperation

# Public Simvoly white-label additional-project pricing observed July 2026.
# Override with environment variables if your contracted rate differs.
DEFAULT_WHOLESALE = {
    "basic": {"starter": "8.00", "premium": "25.00", "elite": "55.00", "ultimate": "195.00"},
    "advanced": {"starter": "7.00", "premium": "22.00", "elite": "50.00", "ultimate": "180.00"},
    "ultimate": {"starter": "5.00", "premium": "15.00", "elite": "35.00", "ultimate": "100.00"},
}

PLAN_ALIASES = {
    "mini": "starter",
    "personal": "starter",
    "starter": "starter",
    "premium": "premium",
    "business": "premium",
    "elite": "elite",
    "pro": "elite",
    "ultimate": "ultimate",
}


def normalize_plan(value: str | None) -> str:
    raw = (value or "starter").strip().lower()
    return PLAN_ALIASES.get(raw, raw)


def _decimal_env(name: str, fallback: str | None = None) -> Decimal | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        raw = fallback
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def wholesale_cost(plan: str, tier: str) -> Decimal | None:
    plan = normalize_plan(plan)
    tier = tier if tier in DEFAULT_WHOLESALE else "advanced"
    env_name = f"SIMVOLY_COST_{plan.upper()}"
    return _decimal_env(env_name, DEFAULT_WHOLESALE[tier].get(plan))


def default_retail_price(plan: str) -> Decimal | None:
    plan = normalize_plan(plan)
    return _decimal_env(f"SMART1_PRICE_{plan.upper()}")


def money(value: Decimal | float | int | str | None) -> str:
    if value is None or value == "":
        return "—"
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return "—"
    return f"${d:,.2f}"
