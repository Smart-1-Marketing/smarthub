"""Budget-reserved, durable text evaluations. Claimed jobs are never replayed."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

import requests
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, select

from hub import ai_comparisons, ai_models, extensions

log = logging.getLogger(__name__)
PRICES_REVIEWED = "2026-09-12"
PRICE_SOURCE = "https://developers.openai.com/api/docs/models/compare"
# USD / million text tokens, standard service tier, no tools. Unknown IDs fail closed.
RATES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
         "gpt-5.6-terra": (2.00, 12.00), "gpt-5.6-sol": (4.00, 20.00)}
INPUT_LIMIT, OUTPUT_LIMIT = 4096, 2048
meta = MetaData()
jobs = Table("hub_ai_comparison_jobs", meta,
    Column("id", String(36), primary_key=True), Column("fingerprint", String(64), nullable=False),
    Column("month", String(7), nullable=False), Column("state", String(30), nullable=False),
    Column("created", Float, nullable=False), Column("updated", Float, nullable=False),
    Column("reserved", Integer, nullable=False), Column("payload", Text, nullable=False),
    Column("results", Text, nullable=False), Column("error", Text, nullable=False))
budgets = Table("hub_ai_comparison_budgets", meta,
    Column("month", String(7), primary_key=True), Column("committed", Integer, nullable=False))
_ready = set()
_init_lock = threading.Lock()
_worker_lock = threading.Lock()
_worker = None


class QueueError(RuntimeError):
    """Safe, locally authored explanation suitable for the admin page."""


def _engine():
    engine = extensions.engine_for()
    with _init_lock:
        if engine not in _ready:
            if extensions.create_all_metadata(meta):
                raise QueueError("Comparison database is unavailable.")
            _ready.add(engine)
    return engine


def _month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _micros(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 100:
            raise ValueError("Budget must be between 0 and 100 USD.")
        return int((amount * 1000000).to_integral_value(rounding=ROUND_FLOOR))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid USD budget.") from None


def monthly_limit():
    return _micros(os.environ.get("AI_COMPARISON_MONTHLY_USD", "5"))


def rates_current():
    age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(PRICES_REVIEWED).date()).days
    return 0 <= age <= 30


def _decode(row):
    row = dict(row)
    row["payload"] = json.loads(row["payload"])
    row["results"] = json.loads(row["results"])
    row["reserved_usd"] = row["reserved"] / 1000000
    return row


def enqueue(body, actor):
    if not isinstance(body, dict) or body.get("confirm_spend") is not True:
        raise ValueError("Confirm the paid comparison and its budget.")
    token = body.get("request_id")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9-]{36}", token):
        raise ValueError("Reload the page to create a request ID.")
    profile, brief_id, candidate = body.get("profile"), body.get("brief"), body.get("candidate")
    if not isinstance(profile, str) or profile not in ai_models.PROFILES or not profile.endswith(".text"):
        raise ValueError("Choose a writing profile.")
    if not isinstance(brief_id, str) or brief_id not in ai_comparisons.BRIEFS:
        raise ValueError("Choose a fixed brief.")
    active = ai_models.model(profile)
    if body.get("active_model") != active:
        raise ValueError("Active model changed. Reload before queuing.")
    if not isinstance(candidate, str) or candidate not in RATES or active not in RATES or candidate == active:
        raise ValueError("Both models must be different and have reviewed prices in the queue allowlist.")
    cap = _micros(body.get("budget_usd"))
    if cap <= 0 or cap > 250000:
        raise ValueError("Choose a job budget above zero and at most 0.25 USD.")
    # Reserve maximum input + output for BOTH calls, rounded up in integer microdollars.
    reserved = sum(int((Decimal(str(RATES[m][0])) * INPUT_LIMIT + Decimal(str(RATES[m][1])) * OUTPUT_LIMIT).to_integral_value(rounding=ROUND_CEILING)) for m in (active, candidate))
    if reserved > cap:
        raise ValueError(f"This pair requires a reservation of ${reserved / 1000000:.6f}.")
    identity = {"profile": profile, "brief_id": brief_id, "active": active, "candidate": candidate,
                "actor": str(actor), "budget_usd": cap / 1000000}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    engine = _engine()
    with engine.begin() as conn:
        old = conn.execute(select(jobs).where(jobs.c.id == token)).mappings().first()
        if old:
            if old["fingerprint"] != fingerprint:
                raise ValueError("This request ID was already used for a different comparison.")
            return _decode(old)
        if not rates_current():
            raise ValueError("Queue pricing needs review before more paid tests can run.")
        from hub.config import settings
        from hub import scheduler
        if not settings.openai_key or not scheduler.enabled():
            raise ValueError("OpenAI and the background scheduler must be configured.")
        month = _month()
        # Both supported databases provide atomic ON CONFLICT. No read/increment race.
        if engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise QueueError("Unsupported comparison database.")
        conn.execute(insert(budgets).values(month=month, committed=0).on_conflict_do_nothing())
        payload = dict(identity, brief=ai_comparisons.BRIEFS[brief_id], rates={m: RATES[m] for m in (active, candidate)},
                       prices_reviewed=PRICES_REVIEWED, input_limit=INPUT_LIMIT, output_limit=OUTPUT_LIMIT,
                       endpoint="responses", service_tier="default", prompt_version=brief_id)
        # Duplicate submission races insert once; their transaction reserves nothing.
        added = conn.execute(insert(jobs).values(id=token, fingerprint=fingerprint, month=month,
            state="queued", created=time.time(), updated=time.time(), reserved=reserved,
            payload=json.dumps(payload), results="[]", error="").on_conflict_do_nothing())
        if not added.rowcount:
            old = conn.execute(select(jobs).where(jobs.c.id == token)).mappings().one()
            if old["fingerprint"] != fingerprint:
                raise ValueError("Request ID conflict.")
            return _decode(old)
        changed = conn.execute(budgets.update().where(budgets.c.month == month,
            budgets.c.committed + reserved <= monthly_limit()).values(committed=budgets.c.committed + reserved))
        if changed.rowcount != 1:
            raise ValueError("Monthly comparison allowance is exhausted.")
        return _decode(conn.execute(select(jobs).where(jobs.c.id == token)).mappings().one())


def snapshot():
    with _engine().connect() as conn:
        committed = conn.execute(select(budgets.c.committed).where(budgets.c.month == _month())).scalar() or 0
        rows = conn.execute(select(jobs).order_by(jobs.c.created.desc()).limit(50)).mappings().all()
    from hub import scheduler
    return {"jobs": [_decode(r) for r in rows], "committed_usd": committed / 1000000,
            "monthly_usd": monthly_limit() / 1000000, "month": _month(), "models": list(RATES),
            "prices_reviewed": PRICES_REVIEWED, "rates_current": rates_current(),
            "scheduler_enabled": scheduler.enabled()}


def cancel(job_id):
    with _engine().begin() as conn:
        row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        if not row:
            raise ValueError("Comparison job not found.")
        changed = conn.execute(jobs.update().where(jobs.c.id == job_id, jobs.c.state == "queued").values(
            state="canceled", updated=time.time()))
        if changed.rowcount != 1:
            raise ValueError("Only a queued job can be canceled; started requests cannot be undone.")
        conn.execute(budgets.update().where(budgets.c.month == row["month"]).values(
            committed=budgets.c.committed - row["reserved"]))


def _save(job_id, results, state="running", error=""):
    with _engine().begin() as conn:
        changed = conn.execute(jobs.update().where(jobs.c.id == job_id, jobs.c.state == "running").values(
            results=json.dumps(results), state=state, error=error, updated=time.time()))
        if changed.rowcount != 1:
            raise QueueError("Job no longer owns its claim.")


def _post(path, payload, key):
    response = requests.post("https://api.openai.com/v1/" + path,
        headers={"Authorization": "Bearer " + key}, json=payload, timeout=(5, 45), allow_redirects=False)
    if response.status_code != 200:
        raise QueueError(f"OpenAI returned HTTP {response.status_code}; no automatic retry.")
    return response.json()


def _generate(model, payload, key):
    prompt = payload["brief"]["prompt"]
    count = _post("responses/input_tokens", {"model": model, "input": prompt}, key)
    tokens = count.get("input_tokens")
    if type(tokens) is not int or not 0 < tokens <= payload["input_limit"]:
        raise QueueError("Input token preflight could not verify the reservation.")
    started = time.monotonic()
    result = _post("responses", {"model": model, "input": prompt,
        "max_output_tokens": payload["output_limit"], "store": False, "service_tier": "default"}, key)
    elapsed = time.monotonic() - started
    usage = result.get("usage") or {}
    tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
    known = type(tin) is int and type(tout) is int and tin >= 0 and tout >= 0
    returned = result.get("model")
    matches = returned == model or (isinstance(returned, str) and re.fullmatch(re.escape(model) + r"-\d{4}-\d{2}-\d{2}", returned) is not None)
    cost = (tin * payload["rates"][model][0] + tout * payload["rates"][model][1]) / 1000000 if known and matches else None
    texts = [part.get("text", "") for item in result.get("output", []) if item.get("type") == "message"
             for part in item.get("content", []) if part.get("type") == "output_text"]
    script = "\n".join(texts).strip()
    complete = result.get("status") == "completed" and bool(script) and known and matches and tin <= payload["input_limit"] and tout <= payload["output_limit"]
    from hub.ai import _record
    if known:
        _record("ai_model_review", "comparison", returned if isinstance(returned, str) else model, usage, int(elapsed * 1000), complete)
    else:
        from hub import audit
        audit.log("ai_model_review", "usage_unknown", model=model, ok=False)
    normalized = " ".join(script.casefold().split())
    return {"model": model, "returned_model": result.get("model"), "response_id": result.get("id"),
            "script": script, "usage": usage, "estimated_cost_usd": cost,
            "latency_seconds": round(elapsed, 3), "complete": complete,
            "missing": [p for p in payload["brief"]["required"] if " ".join(p.casefold().split()) not in normalized],
            "review": "Human quality, builder compatibility and voice timing still require review."}


def run_one():
    engine = _engine()
    with engine.begin() as conn:
        # A dead worker may have spent credits. Never replay that job or release its allowance.
        conn.execute(jobs.update().where(jobs.c.state == "running", jobs.c.updated < time.time() - 900).values(
            state="needs_attention", error="Worker interrupted; provider outcome may be unknown. Not retried."))
        row = conn.execute(select(jobs).where(jobs.c.state == "queued").order_by(jobs.c.created).limit(1)).mappings().first()
        if not row:
            return {"claimed": 0}
        changed = conn.execute(jobs.update().where(jobs.c.id == row["id"], jobs.c.state == "queued").values(state="running", updated=time.time()))
        if changed.rowcount != 1:
            return {"claimed": 0}
    job = _decode(row)
    payload, results = job["payload"], []
    try:
        from hub.config import settings
        if not settings.openai_key or not rates_current() or job["month"] != _month() or monthly_limit() == 0:
            raise QueueError("Configuration, pricing or budget month changed. Queue a new test after review.")
        if payload["prices_reviewed"] != PRICES_REVIEWED:
            raise QueueError("Reviewed prices changed since this job was queued.")
        if ai_models.model(payload["profile"]) != payload["active"]:
            raise QueueError("Active model changed since this job was queued.")
        for model in (payload["active"], payload["candidate"]):
            if job["month"] != _month():
                raise QueueError("Budget month changed during the comparison.")
            sample = _generate(model, payload, settings.openai_key)
            results.append(sample)
            _save(job["id"], results)
            if not sample["complete"]:
                raise QueueError("Incomplete output or unverifiable usage; review the saved result before another test.")
        _save(job["id"], results, "completed")
    except Exception as exc:  # never expose raw provider bodies, keys or prompts in error messages
        message = str(exc) if isinstance(exc, QueueError) else "Comparison interrupted; outcome may be unknown. No automatic retry."
        _save(job["id"], results, "needs_attention", message)
    return {"claimed": 1, "job_id": job["id"]}


def kick(app):
    """Dedicated short-lived worker: the shared scheduler never waits on model calls."""
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return {"worker": "busy"}
        def work():
            with app.app_context():
                try:
                    run_one()
                except Exception:
                    log.exception("AI comparison worker failed; claimed jobs will not be replayed")
        _worker = threading.Thread(target=work, name="ai-comparison", daemon=True)
        _worker.start()
    return {"worker": "started"}
