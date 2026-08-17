"""Provider usage counters, monthly limits, and cost estimation.

Three providers here bill in ways that surprise people:

  * **Brandfetch** — a low monthly allowance. Every logo lookup in Image
    Creator and every brand pull in Suite Panel counts against it, and both
    tools make the call feel free.
  * **Insites** — a credit per site audit. The Scans module already lost
    credits to an email address passing domain validation and to a
    double-click spending two credits on one domain.
  * **OpenAI** — metered per token across eight call sites, which until v7
    recorded nothing at all.

Counts are derived from the activity log rather than a separate counter, so
they cannot drift out of sync with what actually happened, and a redeploy
doesn't reset them. The log is append-only JSONL keyed by month.

Limits are read from the environment so you can raise them the day you change
plan, without a deploy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from hub import audit

# ---------------------------------------------------------------------------
# Provider definitions. `warn_at` is the number Todd asked to be warned at;
# `limit` is the plan allowance where one is known.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quota:
    key: str
    label: str
    unit: str
    warn_at: int
    limit: int
    env_warn: str
    env_limit: str
    note: str

    def thresholds(self) -> tuple[int, int]:
        def _i(name, default):
            try:
                return int(float(os.environ.get(name) or default))
            except (TypeError, ValueError):
                return default
        return _i(self.env_warn, self.warn_at), _i(self.env_limit, self.limit)


QUOTAS: dict[str, Quota] = {
    "brandfetch": Quota(
        "brandfetch", "Brandfetch", "lookups", 80, 100,
        "BRANDFETCH_WARN_AT", "BRANDFETCH_MONTHLY_LIMIT",
        "Logo and brand-colour lookups from Image Creator and Suite Panel. "
        "Cached results do not count."),
    "insites": Quota(
        "insites", "Insites", "scans", 900, 1000,
        "INSITES_WARN_AT", "INSITES_MONTHLY_LIMIT",
        "One credit per site audit. A re-pull of an existing report is free; "
        "starting a new audit is not."),
    "removebg": Quota(
        "removebg", "remove.bg", "cutouts", 40, 50,
        "REMOVEBG_WARN_AT", "REMOVEBG_MONTHLY_LIMIT",
        "One credit per AI cutout. The free white-background removal runs in "
        "the browser and does not count."),
    "openai": Quota(
        "openai", "OpenAI", "calls", 0, 0,
        "OPENAI_WARN_AT", "OPENAI_MONTHLY_LIMIT",
        "Metered by token, not by call. See the cost estimate."),
}


def month_key(when: datetime | None = None) -> str:
    d = when or datetime.now(timezone.utc)
    return d.strftime("%Y-%m")


def record(provider: str, *, module: str = "", detail: str = "",
           cached: bool = False, units: int = 1) -> None:
    """Log one billable call.

    `cached=True` still writes a row — knowing how much the cache is saving
    you is worth as much as knowing what you spent.
    """
    # NB: the extra key is "tool", not "module" — audit.log()'s first
    # positional parameter is already called module, so passing module= here
    # raises TypeError: got multiple values for argument 'module'.
    audit.log("quota", "call", provider=provider, tool=module or "unknown",
              detail=detail[:120], cached=bool(cached), units=int(units),
              month=month_key())


def counts(month: str | None = None, lookback: int = 200000) -> dict[str, dict]:
    """Billable and cached counts per provider for a month."""
    month = month or month_key()
    out: dict[str, dict] = {}
    for row in audit.read(limit=lookback, module="quota"):
        if row.get("month") != month:
            continue
        p = row.get("provider") or "unknown"
        acc = out.setdefault(p, {"billable": 0, "cached": 0, "by_module": {}})
        units = int(row.get("units") or 1)
        if row.get("cached"):
            acc["cached"] += units
        else:
            acc["billable"] += units
            tool = row.get("tool") or "unknown"
            acc["by_module"][tool] = acc["by_module"].get(tool, 0) + units
    return out


def status(month: str | None = None) -> list[dict]:
    """Per-provider usage against its threshold, with a state.

    States: ok | warn (past warn_at) | over (past limit).
    """
    month = month or month_key()
    used = counts(month)
    rows = []
    for q in QUOTAS.values():
        warn_at, limit = q.thresholds()
        acc = used.get(q.key, {"billable": 0, "cached": 0, "by_module": {}})
        n = acc["billable"]
        if limit and n >= limit:
            state, message = "over", (
                f"{q.label}: {n} {q.unit} this month — the {limit} allowance is "
                f"used up. Further calls will fail or bill as overage.")
        elif warn_at and n >= warn_at:
            state, message = "warn", (
                f"{q.label}: {n} of {limit or '—'} {q.unit} used this month, "
                f"past the {warn_at} warning mark.")
        else:
            state, message = "ok", (
                f"{q.label}: {n}{f' of {limit}' if limit else ''} {q.unit} "
                f"this month.")
        pct = round(100 * n / limit) if limit else None
        rows.append({
            "key": q.key, "label": q.label, "unit": q.unit, "month": month,
            "used": n, "cached": acc["cached"], "warn_at": warn_at,
            "limit": limit or None, "percent": pct, "state": state,
            "message": message, "note": q.note,
            "by_module": dict(sorted(acc["by_module"].items(),
                                     key=lambda kv: -kv[1])),
        })
    return rows


def warnings(month: str | None = None) -> list[dict]:
    """Only the rows that need attention — for a banner or an alert job."""
    return [r for r in status(month) if r["state"] in ("warn", "over")]


# ---------------------------------------------------------------------------
# OpenAI cost estimate
# ---------------------------------------------------------------------------

# USD per 1M tokens. Approximate on purpose, and labelled as an estimate
# everywhere it is shown — the authoritative number is OpenAI's own dashboard.
PRICING = {
    "gpt-4o-mini":  {"in": 0.15,  "out": 0.60},
    "gpt-4o":       {"in": 2.50,  "out": 10.00},
    "gpt-4.1":      {"in": 2.00,  "out": 8.00},
    "gpt-4.1-mini": {"in": 0.40,  "out": 1.60},
    "gpt-4.1-nano": {"in": 0.10,  "out": 0.40},
    "o4-mini":      {"in": 1.10,  "out": 4.40},
}
IMAGE_PRICING = {"gpt-image-1": 0.04}   # per 1024x1024 standard image


def _price(model: str) -> dict:
    if model in PRICING:
        return PRICING[model]
    for k in PRICING:                       # tolerate dated suffixes
        if model.startswith(k):
            return PRICING[k]
    return PRICING["gpt-4o-mini"]


def openai_cost(month: str | None = None, lookback: int = 200000) -> dict:
    """Estimated OpenAI spend for a month, broken down by model and module.

    Reads the usage rows hub/ai.py writes on every call. A module that still
    calls OpenAI directly instead of going through hub.ai will not appear here
    — `untracked_modules` names them so the gap is visible rather than silent.
    """
    month = month or month_key()
    by_model: dict[str, dict] = {}
    by_module: dict[str, dict] = {}
    calls = errors = images = 0
    tin = tout = 0

    for row in audit.read(limit=lookback, module="ai"):
        ts = row.get("time", "")
        if not ts.startswith(month):
            continue
        calls += 1
        model = row.get("model") or "unknown"
        mod = row.get("tool") or "unknown"
        i = int(row.get("tokens_in") or 0)
        o = int(row.get("tokens_out") or 0)
        tin += i
        tout += o
        if not row.get("ok"):
            errors += 1

        if model in IMAGE_PRICING or model.startswith("gpt-image"):
            images += 1
            cost = IMAGE_PRICING.get(model, 0.04)
        else:
            p = _price(model)
            cost = i / 1e6 * p["in"] + o / 1e6 * p["out"]

        m = by_model.setdefault(model, {"calls": 0, "tokens_in": 0,
                                        "tokens_out": 0, "cost": 0.0})
        m["calls"] += 1
        m["tokens_in"] += i
        m["tokens_out"] += o
        m["cost"] += cost

        d = by_module.setdefault(mod, {"calls": 0, "cost": 0.0, "errors": 0})
        d["calls"] += 1
        d["cost"] += cost
        if not row.get("ok"):
            d["errors"] += 1

    for d in list(by_model.values()) + list(by_module.values()):
        d["cost"] = round(d["cost"], 4)
    total = round(sum(m["cost"] for m in by_model.values()), 2)

    # Project the month end from the run rate so far.
    now = datetime.now(timezone.utc)
    projected = total
    if month == month_key():
        day = max(1, now.day)
        import calendar
        days = calendar.monthrange(now.year, now.month)[1]
        projected = round(total / day * days, 2)

    return {
        "month": month, "calls": calls, "errors": errors, "images": images,
        "tokens_in": tin, "tokens_out": tout,
        "estimated_cost": total, "projected_month_end": projected,
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])),
        "by_module": dict(sorted(by_module.items(), key=lambda kv: -kv[1]["cost"])),
        "untracked_modules": untracked_openai_modules(),
        "caveat": "Estimated from logged token counts at list prices. "
                  "Treat OpenAI's own dashboard as authoritative — this is for "
                  "spotting a tool that has started spending unexpectedly.",
    }


def untracked_openai_modules() -> list[str]:
    """Modules calling OpenAI directly, so their spend never reaches this page.

    Every one of these is a blind spot in the cost estimate. Migrating a module
    onto hub.ai is what closes it.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    found = set()
    for p in root.rglob("*.py"):
        if "_attic" in p.parts or "__pycache__" in p.parts:
            continue
        if p.name in {"ai.py", "quotas.py", "diagnostics.py"}:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "api.openai.com" in src or "OpenAI(" in src or "openai." in src:
            if "hub.ai" in src or "from hub import ai" in src:
                continue
            parts = p.relative_to(root).parts
            found.add(parts[1] if parts[0] == "modules" and len(parts) > 1
                      else parts[0].replace(".py", ""))
    return sorted(found)


def summary(month: str | None = None) -> dict:
    """Everything the diagnostics page and the nightly alert need."""
    rows = status(month)
    return {"month": month or month_key(), "quotas": rows,
            "warnings": [r for r in rows if r["state"] in ("warn", "over")],
            "openai": openai_cost(month)}
