"""Data-driven trigger evaluation and lifecycle stability for SmartForecast.

Rules arrive as dictionaries loaded from the database.  The evaluator knows
nothing about HVAC, restaurants, or any other industry; those decisions live
in rule data and can therefore be changed without a deploy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


OPERATORS = {
    ">=": lambda actual, expected: actual >= expected,
    ">": lambda actual, expected: actual > expected,
    "<=": lambda actual, expected: actual <= expected,
    "<": lambda actual, expected: actual < expected,
    "==": lambda actual, expected: actual == expected,
    "!=": lambda actual, expected: actual != expected,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def condition_matches(condition: dict, snapshot: dict) -> bool:
    """Evaluate one stored condition against a normalized weather snapshot."""
    metric = str(condition.get("metric") or "")
    operator = str(condition.get("operator") or ">=")
    if operator not in OPERATORS or metric not in snapshot:
        return False
    actual, expected = snapshot.get(metric), condition.get("value")
    actual_num, expected_num = _number(actual), _number(expected)
    if actual_num is not None and expected_num is not None:
        actual, expected = actual_num, expected_num
    try:
        return bool(OPERATORS[operator](actual, expected))
    except (TypeError, ValueError):
        return False


def conditions_match(conditions: Iterable[dict], snapshot: dict,
                     mode: str = "all") -> bool:
    checks = [condition_matches(item, snapshot) for item in conditions]
    if not checks:
        return False
    return any(checks) if mode == "any" else all(checks)


def _alerts(snapshot: dict) -> set[str]:
    raw = snapshot.get("official_alerts") or snapshot.get("official_alert") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def match_rule(rule: dict, snapshot: dict) -> dict | None:
    """Return the rule's matching phase/reason, or ``None``.

    Official alerts are always immediate.  Ordinary current conditions are
    active-event candidates; forecast conditions inside the configured lead
    window are pre-event candidates.
    """
    if not rule.get("enabled", True):
        return None

    issued = _alerts(snapshot)
    watched = {str(item).strip().lower()
               for item in (rule.get("official_alerts") or [])}
    # Weather providers often append timing or location to the canonical alert
    # name ("Heat Advisory issued for Franklin County").  Match the stored
    # alert class inside that headline instead of requiring byte-for-byte text.
    alert_hit = sorted(watch for watch in watched
                       if any(watch in issued_alert for issued_alert in issued))
    if alert_hit:
        return {
            "rule": rule,
            "phase": "active_event",
            "immediate": True,
            "reason": f"Official alert issued: {alert_hit[0]}",
        }

    mode = str(rule.get("condition_mode") or "all")
    active = rule.get("active_conditions") or []
    if conditions_match(active, snapshot, mode):
        return {
            "rule": rule,
            "phase": "active_event",
            "immediate": False,
            "reason": describe_conditions(active, snapshot),
        }

    forecast = rule.get("forecast_conditions") or []
    hours = _number(snapshot.get("hours_until_event"))
    within_lead = hours is None or 0 <= hours <= float(rule.get("lead_hours") or 0)
    if within_lead and conditions_match(forecast, snapshot, mode):
        return {
            "rule": rule,
            "phase": "pre_event",
            "immediate": False,
            "reason": describe_conditions(forecast, snapshot),
        }
    return None


def describe_conditions(conditions: list[dict], snapshot: dict) -> str:
    parts = []
    for condition in conditions:
        metric = str(condition.get("metric") or "weather").replace("_", " ")
        actual = snapshot.get(condition.get("metric"))
        parts.append(f"{metric} {actual:g}" if isinstance(actual, (int, float))
                     else f"{metric} {actual}")
    return ", ".join(parts) or "Configured weather rule matched"


def choose_winner(rules: Iterable[dict], snapshot: dict) -> dict | None:
    candidates = [hit for rule in rules if (hit := match_rule(rule, snapshot))]
    if not candidates:
        return None
    candidates.sort(key=lambda hit: (
        bool(hit["immediate"]),
        int(hit["rule"].get("priority") or 0),
        str(hit["rule"].get("name") or ""),
    ), reverse=True)
    winner = candidates[0]
    winner["matching_count"] = len(candidates)
    return winner


def empty_state() -> dict:
    return {
        "status": "default",
        "current_trigger": None,
        "phase": "default",
        "pending_trigger": None,
        "qualify_count": 0,
        "clear_count": 0,
        "activated_at": None,
        "phase_changed_at": None,
        "post_until": None,
        "cooldown_until": None,
        "cooldown_trigger": None,
        "reason": "No trigger currently qualifies",
    }


def advance_state(state: dict | None, winner: dict | None,
                  now: datetime | None = None) -> tuple[dict, str | None]:
    """Advance lifecycle state and return ``(new_state, transition)``.

    Ordinary rules require their configured consecutive checks. Official
    alerts bypass confirmation. Clearing uses the current rule's stored clear
    count and then its post-event window before returning to default.
    """
    now = now or utcnow()
    current = {**empty_state(), **(state or {})}
    new = dict(current)
    transition = None

    if winner:
        rule = winner["rule"]
        rule_id = str(rule.get("id") or rule.get("key"))
        cooldown_until = parse_time(current.get("cooldown_until"))
        if (not winner.get("immediate") and current.get("cooldown_trigger") == rule_id
                and cooldown_until and now < cooldown_until):
            new["reason"] = f"{rule.get('name', 'Trigger')} is in cooldown until {iso(cooldown_until)}"
            return new, "cooldown"
        required = 1 if winner.get("immediate") else max(
            1, int(rule.get("activation_checks") or 2))

        if current.get("current_trigger") == rule_id:
            new.update({
                "status": "active",
                "phase": winner["phase"],
                "pending_trigger": None,
                "qualify_count": required,
                "clear_count": 0,
                "post_until": None,
                "reason": winner["reason"],
                "rule": rule,
            })
            if current.get("phase") != winner["phase"]:
                new["phase_changed_at"] = iso(now)
                transition = "phase_changed"
            return new, transition

        same_pending = current.get("pending_trigger") == rule_id
        count = int(current.get("qualify_count") or 0) + 1 if same_pending else 1
        new.update({"pending_trigger": rule_id, "qualify_count": count,
                    "clear_count": 0, "reason": winner["reason"], "rule": rule})
        if count < required:
            return new, "qualifying"

        new.update({
            "status": "active",
            "current_trigger": rule_id,
            "phase": winner["phase"],
            "pending_trigger": None,
            "activated_at": iso(now),
            "phase_changed_at": iso(now),
            "post_until": None,
            "cooldown_until": None,
            "cooldown_trigger": None,
        })
        return new, "activated"

    # No candidate.  A post window is kept until its actual expiry.
    post_until = parse_time(current.get("post_until"))
    if current.get("phase") == "post_event" and post_until and now < post_until:
        new["reason"] = "Post-event relevance window is still active"
        return new, None
    if current.get("phase") == "post_event" and (not post_until or now >= post_until):
        cleared = empty_state()
        rule = current.get("rule") or {}
        cooldown_hours = max(0.0, float(rule.get("cooldown_hours") or 0))
        if cooldown_hours:
            cleared.update({"cooldown_until": iso(now + timedelta(hours=cooldown_hours)),
                            "cooldown_trigger": current.get("current_trigger"),
                            "reason": f"Default content; {cooldown_hours:g}-hour trigger cooldown"})
        return cleared, "deactivated"

    if not current.get("current_trigger"):
        new.update({"pending_trigger": None, "qualify_count": 0, "clear_count": 0})
        return new, None

    rule = current.get("rule") or {}
    activated_at = parse_time(current.get("activated_at"))
    min_hours = max(0.0, float(rule.get("min_duration_hours") or 0))
    if activated_at and min_hours and now < activated_at + timedelta(hours=min_hours):
        new.update({"clear_count": 0, "pending_trigger": None, "qualify_count": 0,
                    "reason": f"Holding the configured {min_hours:g}-hour minimum duration"})
        return new, "minimum_duration"
    clear_required = max(1, int(rule.get("clear_checks") or 2))
    clears = int(current.get("clear_count") or 0) + 1
    new.update({"clear_count": clears, "pending_trigger": None,
                "qualify_count": 0,
                "reason": f"Waiting for {clear_required} clear weather checks"})
    if clears < clear_required:
        return new, "clearing"

    post_hours = max(0.0, float(rule.get("post_hours") or 0))
    if post_hours:
        new.update({
            "phase": "post_event",
            "phase_changed_at": iso(now),
            "post_until": iso(now + timedelta(hours=post_hours)),
            "clear_count": clears,
            "reason": f"Weather cleared; {post_hours:g}-hour post-event window",
        })
        return new, "phase_changed"
    cleared = empty_state()
    cooldown_hours = max(0.0, float(rule.get("cooldown_hours") or 0))
    if cooldown_hours:
        cleared.update({"cooldown_until": iso(now + timedelta(hours=cooldown_hours)),
                        "cooldown_trigger": current.get("current_trigger"),
                        "reason": f"Default content; {cooldown_hours:g}-hour trigger cooldown"})
    return cleared, "deactivated"


def simulate(rules: Iterable[dict], snapshot: dict) -> dict:
    """Explain a simulation without mutating lifecycle history."""
    winner = choose_winner(rules, snapshot)
    if not winner:
        return {"winner": None, "phase": "default", "reason": "No trigger matched"}
    rule = dict(winner["rule"])
    return {
        "winner": rule,
        "phase": winner["phase"],
        "reason": winner["reason"],
        "immediate": bool(winner["immediate"]),
        "matching_count": winner.get("matching_count", 1),
    }
