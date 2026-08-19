"""Bulk scanning — audit every client with a website on file.

This is the most expensive button in the Hub. One Insites credit per domain,
and the client registry runs to hundreds of entries, so a careless "scan
everything" is a four-figure mistake that cannot be undone. The design is
therefore plan-first: nothing spends until a human has seen the count.

Three safeguards, all on by default:

  1. **Plan before spend.** ``plan()`` is read-only. It resolves the client
     list, works out who would actually be scanned, and returns the credit
     cost. Nothing is started until ``run()`` is called with that plan's id.
  2. **Skip anything recently scanned.** A domain audited inside the freshness
     window is skipped, not re-run. Re-scanning a site nobody has touched in a
     fortnight tells you nothing and costs the same as a new one.
  3. **Cap per run.** Everything beyond the cap is queued rather than fired,
     so a 400-client registry becomes eight reviewed batches instead of one
     irreversible spend.

Plus the guards that fall out of reusing the existing single-scan path: the
duplicate check under ``_LOCK``, the domain validation that rejects email
addresses, and the monthly quota counter that warns at 900.
"""
from __future__ import annotations

import re

import secrets
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone

DEFAULT_CAP = 50
DEFAULT_FRESH_DAYS = 30

# Plans live in memory and expire — a stale plan built against yesterday's
# registry must not be runnable today.
_PLANS: dict[str, dict] = {}
_PLAN_TTL = 30 * 60


@dataclass
class Candidate:
    name: str
    domain: str
    url: str
    source: str = ""
    products: list = field(default_factory=list)
    is_house: bool = False
    state: str = "queued"        # scan | skip_recent | skip_over_cap | skip_no_domain
    reason: str = ""
    last_scanned: str = ""
    last_score: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _now():
    return datetime.now(timezone.utc)


def _clients():
    from hub import clients_registry
    return clients_registry.all_clients()


def _recent_scans(fresh_days: int) -> dict:
    """domain_key -> (iso timestamp, score) for scans inside the window.

    Imported lazily: Scans owns its own database, and a bulk-scan page should
    not take the Hub down if that module fails to import.
    """
    try:
        from modules.scans.app import Scan, SessionLocal
    except Exception:                                   # noqa: BLE001
        return {}
    cutoff = _now() - timedelta(days=fresh_days)
    out: dict = {}
    db = SessionLocal()
    try:
        rows = (db.query(Scan)
                .filter(Scan.status.in_(("running", "complete")))
                .order_by(Scan.created_at.desc()).all())
        for s in rows:
            created = s.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if s.domain_key in out:
                continue
            if created >= cutoff:
                out[s.domain_key] = (created.isoformat(), s.overall_score)
    finally:
        db.close()
    return out


def plan(*, cap: int = DEFAULT_CAP, fresh_days: int = DEFAULT_FRESH_DAYS,
         include_house: bool = False, only_live: bool = False) -> dict:
    """Work out what a run *would* do. Spends nothing.

    Returns a plan with an id; ``run()`` will only accept that id, so the list
    that gets scanned is exactly the list that was reviewed.
    """
    from modules.scans.app import domain_key, looks_like_domain

    recent = _recent_scans(fresh_days)
    cands: list[Candidate] = []
    seen: set[str] = set()

    # Fall back to the click-thru URLs on live products for clients with no
    # website on their registry record. Those clients were being skipped
    # entirely — no scan, no report, invisible — while their live campaigns
    # were pointing at a site the whole time.
    click_thru: dict[str, dict] = {}
    try:
        from hub.knack_products import scan_domains
        for d in scan_domains().get("domains", []):
            key = re.sub(r"[^a-z0-9]+", "", d["client"].lower())
            click_thru.setdefault(key, d)
    except Exception:                                   # noqa: BLE001
        click_thru = {}

    for c in _clients():
        if c.get("is_house") and not include_house:
            continue
        if only_live and not c.get("live"):
            continue
        url = (c.get("url") or c.get("domain") or "").strip()
        name = c.get("name", "")
        from_click = False
        if not url:
            hit = click_thru.get(re.sub(r"[^a-z0-9]+", "", name.lower()))
            if hit:
                url, from_click = hit["domain"], True
        if not url:
            cands.append(Candidate(name, "", "", c.get("source", ""),
                                   c.get("products", []), bool(c.get("is_house")),
                                   "skip_no_domain",
                                   "No website on file, and no click-thru URL "
                                   "on any live product either."))
            continue
        if not looks_like_domain(url):
            cands.append(Candidate(name, "", url, c.get("source", ""),
                                   c.get("products", []), bool(c.get("is_house")),
                                   "skip_no_domain",
                                   "The address on file isn't a valid domain."))
            continue

        key = domain_key(url)
        if key in seen:
            cands.append(Candidate(name, key, url, c.get("source", ""),
                                   c.get("products", []), bool(c.get("is_house")),
                                   "skip_no_domain",
                                   "Another client on file shares this domain."))
            continue
        seen.add(key)

        if key in recent:
            when, score = recent[key]
            cands.append(Candidate(name, key, url, c.get("source", ""),
                                   c.get("products", []), bool(c.get("is_house")),
                                   "skip_recent",
                                   f"Scanned within the last {fresh_days} days.",
                                   when, score))
            continue

        cands.append(Candidate(name, key, url, c.get("source", ""),
                               c.get("products", []), bool(c.get("is_house")),
                               "scan"))

    # The cap decides who actually gets audited, so the order is a product
    # decision, not a formatting one. Alphabetical would spend your credits on
    # whoever is nearest the top of the alphabet. Rank by who the audit is
    # worth most to instead:
    #   1. clients paying for SEO — a site audit is the thing they buy
    #   2. clients with any live product — real revenue attached
    #   3. product count, as a rough proxy for account size
    #   4. name, so a repeat run is deterministic and picks up where it left off
    scannable = [c for c in cands if c.state == "scan"]
    by_name = {c.get("name", ""): c for c in _clients()}

    def rank(c: Candidate):
        src = by_name.get(c.name, {})
        return (
            0 if src.get("is_seo") else 1,
            0 if src.get("live") else 1,
            -int(src.get("product_count") or len(c.products or [])),
            c.name.lower(),
        )

    scannable.sort(key=rank)
    for c in scannable[cap:]:
        c.state = "skip_over_cap"
        c.reason = f"Beyond this run's cap of {cap}. Queued for the next run."

    will_scan = [c for c in cands if c.state == "scan"]
    plan_id = secrets.token_urlsafe(12)
    payload = {
        "plan_id": plan_id,
        "created_at": _now().isoformat(),
        "cap": cap, "fresh_days": fresh_days,
        "include_house": include_house, "only_live": only_live,
        "totals": {
            "clients": len(cands),
            "will_scan": len(will_scan),
            "skip_recent": sum(1 for c in cands if c.state == "skip_recent"),
            "skip_over_cap": sum(1 for c in cands if c.state == "skip_over_cap"),
            "skip_no_domain": sum(1 for c in cands if c.state == "skip_no_domain"),
        },
        "credits": len(will_scan),
        "candidates": [c.as_dict() for c in cands],
        "quota": _quota_note(len(will_scan)),
    }
    _PLANS[plan_id] = {"at": time.time(), "targets": [c.domain for c in will_scan],
                       "urls": {c.domain: c.url for c in will_scan},
                       "names": {c.domain: c.name for c in will_scan}}
    _prune()
    return payload


def _prune():
    now = time.time()
    for k in [k for k, v in _PLANS.items() if now - v["at"] > _PLAN_TTL]:
        _PLANS.pop(k, None)


def _quota_note(cost: int) -> dict:
    """What this run does to the monthly Insites allowance."""
    try:
        from hub import quotas
        row = next(r for r in quotas.status() if r["key"] == "insites")
    except Exception:                                   # noqa: BLE001
        return {"known": False}
    used, limit, warn_at = row["used"], row["limit"], row["warn_at"]
    after = used + cost
    note = {"known": True, "used": used, "limit": limit, "warn_at": warn_at,
            "after": after, "state": "ok"}
    if limit and after > limit:
        note["state"] = "over"
        note["message"] = (
            f"This run would take you to {after} scans against a {limit} "
            f"monthly allowance — {after - limit} over. Reduce the cap or wait "
            f"for the allowance to reset.")
    elif warn_at and after >= warn_at:
        note["state"] = "warn"
        note["message"] = (
            f"This run would take you to {after} scans this month, past the "
            f"{warn_at} warning mark.")
    else:
        note["message"] = (
            f"{used} scans used this month; this run would make it {after}"
            f"{f' of {limit}' if limit else ''}.")
    return note


def run(plan_id: str, *, requested_by: str = "", allow_over_quota: bool = False) -> dict:
    """Start the scans in a previously reviewed plan. This spends credits."""
    from modules.scans.app import (Scan, SessionLocal, _LOCK, _new_public_id,
                                   _text, _now as _scan_now,
                                   PUBLIC_BASE_URL, CALLBACK_TOKEN,
                                   _try_immediate_fetch)
    from modules.scans import insites_client

    entry = _PLANS.get(plan_id)
    if not entry:
        return {"error": "That plan has expired. Build a new one so the list "
                         "reflects the current registry."}, 410
    if time.time() - entry["at"] > _PLAN_TTL:
        _PLANS.pop(plan_id, None)
        return {"error": "That plan has expired. Build a new one."}, 410

    targets = entry["targets"]
    if not targets:
        return {"error": "Nothing to scan in that plan."}, 400

    q = _quota_note(len(targets))
    if q.get("state") == "over" and not allow_over_quota:
        return {"error": q["message"], "quota": q}, 409

    _PLANS.pop(plan_id, None)          # single use — a plan cannot be re-run

    from hub import audit
    log = audit.for_module("scans")
    log("bulk_started", count=len(targets), by=requested_by or "unknown")

    started, failed, skipped = [], [], []
    db = SessionLocal()
    try:
        for key in targets:
            url = entry["urls"].get(key, key)
            name = entry["names"].get(key, "")
            try:
                with _LOCK:
                    # Re-check inside the loop: a colleague may have started
                    # this same domain between the plan and the run.
                    existing = (db.query(Scan)
                                .filter(Scan.domain_key == key,
                                        Scan.status.in_(("running", "complete")))
                                .first())
                    if existing:
                        skipped.append({"domain": key, "name": name,
                                        "reason": "Already scanned since the plan was built."})
                        continue
                    s = Scan(public_id=_new_public_id(), domain_key=key,
                             input_url=_text(url, 600),
                             business_name=_text(name, 300),
                             source="bulk",
                             requested_by=_text(requested_by, 160),
                             status="running", created_at=_scan_now())
                    db.add(s)
                    db.commit()
                # This is the bug that left every bulk scan on "running"
                # forever. The single-scan path hands Insites an
                # `on_completion` URL and saves the returned reportId; this
                # did neither. With no callback URL Insites had nowhere to
                # deliver results, and with no reportId stored, "Check status"
                # and "Re-check all" had nothing to poll — so the row could
                # never resolve by any route.
                on_completion = None
                if PUBLIC_BASE_URL:
                    tok = f"?token={CALLBACK_TOKEN}" if CALLBACK_TOKEN else ""
                    on_completion = (f"{PUBLIC_BASE_URL}/scans/api/callback/"
                                     f"{s.public_id}{tok}")
                resp = insites_client.start_audit(
                    url, on_completion=on_completion, name=name) or {}
                rid = resp.get("reportId") or resp.get("report_id")
                if rid:
                    s.insites_report_id = _text(str(rid), 80)
                    db.commit()
                else:
                    # No report id means nothing can ever resolve this row.
                    # Mark it error now rather than leaving it "running"
                    # indefinitely — a stuck row looks like work in progress.
                    s.status = "error"
                    s.error_message = ("Insites accepted the request but "
                                       "returned no report id, so there is "
                                       "nothing to poll. Try again.")
                    db.commit()
                    failed.append({"domain": key, "name": name,
                                   "error": "no report id returned"})
                    log("scan_failed", detail=key, via="bulk",
                        error="no_report_id")
                    continue

                # A 303 means results already existed — fetch straight away
                # rather than waiting for a callback that won't come.
                if on_completion is None or resp.get("status") == "success":
                    try:
                        _try_immediate_fetch(db, s)
                    except Exception:              # noqa: BLE001
                        pass

                started.append({"domain": key, "name": name,
                                "public_id": s.public_id})
                log("scan_started", detail=key, scan=s.public_id, via="bulk")
            except Exception as exc:                    # noqa: BLE001
                db.rollback()
                failed.append({"domain": key, "name": name,
                               "error": type(exc).__name__})
                log("scan_failed", detail=key, error=type(exc).__name__, via="bulk")
    finally:
        db.close()

    log("bulk_finished", started=len(started), failed=len(failed),
        skipped=len(skipped))
    return {
        "started": started, "failed": failed, "skipped": skipped,
        "counts": {"started": len(started), "failed": len(failed),
                   "skipped": len(skipped)},
        "credits_spent": len(started),
        "message": (f"{len(started)} scans started. Results arrive over the "
                    f"next few minutes — the Site Scans table updates itself."),
    }, 200
