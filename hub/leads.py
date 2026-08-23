"""Every lead, in one place, pushed to GoHighLevel from one place.

## Why this exists

Each landing page and each calculator was given its own GHL webhook. That
means one environment variable per app, each of which can be unset, wrong or
silently failing — and the failure mode is invisible: the visitor sees a
success message, the app returns 200, and the lead is gone. The QA audit
found exactly this across the Smart 1 Suite apps, where `GHL_WEBHOOK_URL`
shipped blank and every lead was binned with no error and no log.

It also means no single place to answer "how many leads did we get last week,
and from which pages?"

So: every source writes here first. The row exists before anything is sent
anywhere. Delivery to GoHighLevel is a separate, retryable step against a
single webhook.

## The order matters

    store the lead  ->  return success to the visitor  ->  push to GHL

Not the other way round. A GHL outage, a rotated token or a typo'd URL must
never destroy a lead we already have. Anything undelivered stays queued and
visible, and can be retried by hand or by the scheduler.
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

_LOCK = threading.Lock()
STORE_NAME = "leads.jsonl"
WEBHOOK_ENV = "HUB_LEAD_WEBHOOK_URL"

# --- Rate limit on the public capture endpoint ----------------------------
#
# /api/leads/capture has to be unauthenticated — landing pages post to it —
# so without a ceiling anyone who finds the URL can fill the panel with
# invented leads until the real ones are unfindable.
#
# The window matches the one already used for the other public endpoint in
# this codebase (modules/google_access), so there is one convention rather
# than two. Set LEADS_RATE_LIMIT=0 to switch the limit off entirely.
#
# Caveat worth knowing: gunicorn runs two workers and this counter is per
# process, so the true ceiling is up to twice the configured number. Counting
# from the stored file instead would be exact, but it would put a full-file
# read on a public endpoint — a cheaper thing for an attacker to abuse than
# the limit itself. Approximate and cheap is the right trade here.
RATE_LIMIT = int(os.environ.get("LEADS_RATE_LIMIT") or 3)
RATE_WINDOW = int(os.environ.get("LEADS_RATE_WINDOW_SECONDS") or 3600)

_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_hits_lock = threading.Lock()


def client_ip(request) -> str:
    """The caller's address, taking the LAST X-Forwarded-For hop.

    The first hop is supplied by the client and is therefore spoofable — a
    header of "1.2.3.4" would let one machine present as a new visitor on
    every request and walk straight past any limit. Render appends the real
    address, so the rightmost entry is the one it vouches for.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def rate_limited(bucket: str, request, limit: int, window: int = 3600) -> bool:
    """True when this caller has spent its allowance for `bucket`.

    Shared so the public landing pages don't each grow their own copy — there
    were four, two of which had no limit at all and two of which read the
    spoofable end of X-Forwarded-For. A public endpoint that calls OpenAI is
    someone else's budget until it has a ceiling.
    """
    if limit <= 0:
        return False
    key = f"{bucket}:{client_ip(request)}"
    now = time.time()
    with _hits_lock:
        b = _hits[key]
        while b and now - b[0] > window:
            b.popleft()
        if len(b) >= limit:
            return True
        b.append(now)
        if len(_hits) > 10000:
            for k in [k for k, v in _hits.items() if not v][:5000]:
                _hits.pop(k, None)
    return False


def rate_check(ip: str) -> tuple[bool, int]:
    """(allowed, seconds_until_a_slot_frees). Never raises."""
    if RATE_LIMIT <= 0:
        return True, 0
    now = time.time()
    with _hits_lock:
        bucket = _hits[ip]
        while bucket and now - bucket[0] > RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            return False, max(1, int(RATE_WINDOW - (now - bucket[0])))
        bucket.append(now)
        if len(_hits) > 10000:            # don't grow without bound
            for k in [k for k, v in _hits.items() if not v][:5000]:
                _hits.pop(k, None)
    return True, 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> str:
    """Where leads are stored.

    HUB_LEADS_FILE overrides it outright. That exists so the delivery tests
    cannot write into the real store: without it, running them anywhere with a
    /var/data mount would inject invented leads into the live panel, and a
    fake lead is worse than a missing one because somebody will chase it.
    """
    override = (os.environ.get("HUB_LEADS_FILE") or "").strip()
    if override:
        os.makedirs(os.path.dirname(override) or ".", exist_ok=True)
        return override
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, STORE_NAME)


def _clean(v, limit: int = 400) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _valid_email(v: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", (v or "").strip(), re.I))


def capture(source: str, page: str, fields: dict, pdf_url: str = "",
            client: str = "", meta: dict | None = None) -> dict:
    """Record a lead. Never raises — a storage fault must not lose the lead.

    `source` is the tool (landing_ads, calculators, smart1hvac…) and `page`
    is the specific page or calculator, because "which page produced this"
    is the question the panel exists to answer.
    """
    row = {
        "id": uuid.uuid4().hex[:12],
        "created": _now(),
        "source": _clean(source, 60),
        "page": _clean(page, 160),
        "client": _clean(client, 120),
        "name": _clean(fields.get("name") or fields.get("full_name"), 120),
        "email": _clean(fields.get("email"), 160),
        "phone": _clean(fields.get("phone"), 40),
        "company": _clean(fields.get("company") or fields.get("business"), 160),
        "pdf_url": _clean(pdf_url, 500),
        "fields": {k: _clean(v) for k, v in (fields or {}).items()
                   if k not in ("name", "email", "phone", "company")},
        "meta": meta or {},
        "delivered": False,
        "attempts": 0,
        "last_error": "",
        # Filled in by delivery. contact_id is the proof the lead reached
        # Smart 1 Suite — and, on a retry, the reason not to write it twice.
        "route": "",
        "contact_id": "",
        "retryable": True,
    }
    try:
        with _LOCK:
            with open(_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        # Still try to deliver — a lost row beats a lost lead.
        row["last_error"] = f"not stored: {type(exc).__name__}"
    try:
        from hub import audit
        audit.log("leads", "captured", client=client or None,
                  source=source, page=page, has_pdf=bool(pdf_url))
    except Exception:                                   # noqa: BLE001
        pass
    return row


def _read_all() -> list[dict]:
    out = []
    try:
        with open(_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue        # one bad line must not hide the rest
    except OSError:
        return []
    return out


def _rewrite(rows: list[dict]) -> None:
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, _path())        # atomic — a crash can't truncate the file


def webhook_url() -> str:
    return (os.environ.get(WEBHOOK_ENV) or "").strip().strip('"').strip("'")


def delivery_mode() -> str:
    """"api", "webhook" or "none" — the one path a lead is written down.

    Chosen from configuration, once, not per lead. That is the whole design:
    the API and the webhook both *create a contact*, so anything that can pick
    between them per attempt can write the same lead twice. A timeout is the
    case that matters — the API may well have succeeded and simply not told us
    — and that is exactly when a "fallback" would duplicate.

    So there is no fallback. If API delivery is configured it is the only path;
    the webhook is what runs until it is. The retry queue in this module is the
    safety net, and it retries down this same path.
    """
    try:
        from hub import ghl_contacts
        if ghl_contacts.configured():
            return "api"
    except Exception:                                   # noqa: BLE001
        pass
    return "webhook" if webhook_url() else "none"


def _deliver_api(row: dict) -> dict:
    """Write the lead as a Suite contact. Only the contact id counts."""
    from hub import ghl_contacts
    res = ghl_contacts.upsert(row)
    row["attempts"] = row.get("attempts", 0) + 1
    row["route"] = "api"
    if res["ok"]:
        row["delivered"] = True
        row["delivered_at"] = _now()
        row["contact_id"] = res["contact_id"]
        row["contact_new"] = res.get("was_new")
        row["last_error"] = ""
    else:
        row["last_error"] = res["error"]
        # A config or payload problem will fail identically forever, so say so
        # rather than letting the scheduler retry it every hour in silence.
        row["retryable"] = bool(res.get("retryable", True))
    return row


def deliver(row: dict) -> dict:
    """Push one lead to GoHighLevel. Returns the updated row."""
    # Already has a contact id: it landed. Re-sending would be the duplicate
    # this design exists to avoid, so a retry over a delivered row is a no-op.
    if row.get("contact_id"):
        row["delivered"] = True
        row.setdefault("delivered_at", _now())
        row["last_error"] = ""
        return row

    mode = delivery_mode()
    if mode == "api":
        return _deliver_api(row)
    if mode == "none":
        row["last_error"] = (
            "No delivery route is configured, so nothing was sent. Set "
            "GHL_LEAD_LOCATION_ID (with GHL_PRIVATE_TOKEN) to deliver over the "
            f"API, or {WEBHOOK_ENV} to use the inbound webhook. The lead is "
            "stored and can be retried once one of them is set.")
        return row

    url = webhook_url()
    row["route"] = "webhook"
    payload = {
        "source": row["source"], "page": row["page"],
        "name": row["name"], "email": row["email"], "phone": row["phone"],
        "company": row["company"], "client": row["client"],
        "pdf_url": row["pdf_url"], "captured_at": row["created"],
        # Tag with the page so GHL workflows can route by where it came from.
        "tags": [t for t in ("smart1-hub", row["source"], row["page"]) if t],
        "extra": row.get("fields") or {},
    }
    try:
        import requests
        r = requests.post(url, json=payload, timeout=20)
        row["attempts"] = row.get("attempts", 0) + 1
        if r.ok:
            row["delivered"] = True
            row["delivered_at"] = _now()
            row["last_error"] = ""
        else:
            # A 4xx/5xx is not an exception — the earlier suite audit found
            # exactly this swallowed, and leads lost silently for hours.
            row["last_error"] = f"GoHighLevel returned HTTP {r.status_code}."
    except Exception as exc:                            # noqa: BLE001
        row["attempts"] = row.get("attempts", 0) + 1
        row["last_error"] = f"Couldn't reach GoHighLevel ({type(exc).__name__})."
    return row


def capture_and_deliver(source: str, page: str, fields: dict,
                        pdf_url: str = "", client: str = "",
                        meta: dict | None = None) -> dict:
    """The one call a landing page or calculator makes."""
    row = capture(source, page, fields, pdf_url, client, meta)
    row = deliver(row)
    _update(row)
    return {"ok": True, "lead_id": row["id"], "delivered": row["delivered"],
            "contact_id": row.get("contact_id", ""),
            "note": (("Sent to Smart 1 Suite." if not row.get("contact_id")
                      else "Created in Smart 1 Suite.") if row["delivered"]
                     else "Saved. " + (row["last_error"] or
                                       "Delivery will be retried."))}


def _update(row: dict) -> None:
    rows = _read_all()
    for i, r in enumerate(rows):
        if r.get("id") == row["id"]:
            rows[i] = row
            break
    else:
        rows.append(row)
    try:
        with _LOCK:
            _rewrite(rows)
    except OSError:
        pass


def mark_converted(lead_id: str, client_name: str, actor: str = "") -> dict | None:
    """Record that a prospect became a client, and which client.

    Deliberately a *link*, not a creation. A client in this Hub is anyone with
    a product in Knack -- that is the billing source of truth, and
    clients_registry.all_clients() reads it. Writing a client record here would
    produce an account that appears in the Hub, never appears on an invoice,
    and disagrees with Knack the moment anyone looks. So the account is created
    in Knack as it always was, and this ties the lead to it so the history
    survives: who came in, from which tool, and what they became.

    Returns the updated row, or None if there is no lead with that id.
    """
    lead_id = str(lead_id or "").strip()
    client_name = _clean(client_name, 200)
    if not lead_id or not client_name:
        return None
    row = next((r for r in _read_all() if r.get("id") == lead_id), None)
    if row is None:
        return None
    row["client"] = client_name
    row["converted_at"] = _now()
    row["converted_by"] = _clean(actor, 120)
    _update(row)
    try:
        from hub import audit
        audit.log("leads", "converted", actor=actor, client=client_name,
                  lead=lead_id, source=row.get("source") or "")
    except Exception:                                   # noqa: BLE001
        pass
    return row


def retry_undelivered(limit: int = 50) -> dict:
    """Re-push anything that hasn't landed. Called by hand or the scheduler.

    Rows already carrying a contact id are skipped by `deliver()` itself, so a
    retry can never write a second contact for a lead that landed. Rows whose
    last failure was a configuration or payload problem are skipped too: they
    will fail identically every hour, and burying the real cause under a rising
    attempt count is how the previous silent failure went unnoticed.
    """
    rows = _read_all()
    mode = delivery_mode()
    sent = failed = blocked = 0
    for r in rows:
        if r.get("delivered") or sent + failed >= limit:
            continue
        if not r.get("retryable", True):
            blocked += 1
            continue
        deliver(r)
        if r.get("delivered"):
            sent += 1
        else:
            failed += 1
    if sent or failed:
        try:
            with _LOCK:
                _rewrite(rows)
        except OSError:
            pass

    note = ""
    if mode == "none":
        note = ("No delivery route is configured, so nothing could be sent. "
                f"Set GHL_LEAD_LOCATION_ID for API delivery, or {WEBHOOK_ENV}.")
    elif failed:
        note = (f"{failed} still undelivered over the "
                f"{'Suite API' if mode == 'api' else 'inbound webhook'}.")
    if blocked:
        note += (f" {blocked} need attention rather than a retry — their last "
                 "error was a configuration or data problem, not a network one.")
    return {"retried": sent + failed, "delivered": sent, "still_failing": failed,
            "needs_attention": blocked, "route": mode, "note": note.strip()}


def listing(days: int = 30, source: str = "", page: str = "",
            undelivered_only: bool = False) -> dict:
    """The lead panel."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(365, days)))
    rows = []
    for r in _read_all():
        try:
            when = datetime.fromisoformat(r.get("created", ""))
        except ValueError:
            when = None
        if when and when < cutoff:
            continue
        if source and r.get("source") != source:
            continue
        if page and page.lower() not in (r.get("page") or "").lower():
            continue
        if undelivered_only and r.get("delivered"):
            continue
        rows.append(r)
    rows.sort(key=lambda r: r.get("created", ""), reverse=True)

    by_page: dict[str, int] = {}
    for r in rows:
        by_page[r.get("page") or "(no page)"] = by_page.get(r.get("page") or "(no page)", 0) + 1
    undelivered = sum(1 for r in rows if not r.get("delivered"))
    return {
        "leads": rows[:500], "count": len(rows),
        "undelivered": undelivered,
        "converted": sum(1 for r in rows if r.get("converted_at")),
        "with_pdf": sum(1 for r in rows if r.get("pdf_url")),
        "by_page": sorted(by_page.items(), key=lambda kv: -kv[1]),
        "sources": sorted({r.get("source") for r in rows if r.get("source")}),
        "days": days,
        "webhook_set": bool(webhook_url()),
        "confirmed": sum(1 for r in rows if r.get("contact_id")),
        "needs_attention": sum(1 for r in rows
                               if not r.get("delivered") and not r.get("retryable", True)),
        **_route_status(),
    }


def _route_status() -> dict:
    """Which way leads are being written, and anything wrong with that."""
    mode = delivery_mode()
    note = ""
    warning = ""
    reason = ""
    try:
        from hub import ghl_contacts
        reason = ghl_contacts.why_not()
    except Exception as exc:                            # noqa: BLE001
        reason = f"hub.ghl_contacts didn't import ({type(exc).__name__})."

    if mode == "none":
        note = ("Nothing is reaching Smart 1 Suite — no delivery route is "
                "configured. Leads are being stored and can be retried once "
                "one is. " + reason)
    elif mode == "webhook":
        note = (f"Delivering over the inbound webhook ({WEBHOOK_ENV}). This "
                "confirms only that Suite accepted the request, not that a "
                "contact exists. Set GHL_LEAD_LOCATION_ID to deliver over the "
                "API and get a contact id back. " + reason)
        # Both routes live is the one configuration that can double-write, and
        # only if something still posts to the webhook directly. Delivery here
        # picks one route, so the Hub won't — but a landing page pointed
        # straight at the webhook URL would, and that is worth saying out loud.
    elif mode == "api" and webhook_url():
        warning = (f"Both routes are configured. The Hub is using the API and "
                   f"will not also fire the webhook, so it won't duplicate — "
                   f"but anything still posting directly to the {WEBHOOK_ENV} "
                   f"URL, or a Suite workflow still triggered by it, will "
                   f"create a second contact. Retire the webhook trigger in "
                   f"Suite and unset {WEBHOOK_ENV} once the API route looks "
                   f"right.")
    return {"route": mode, "note": note, "route_warning": warning,
            "route_label": {"api": "Smart 1 Suite API",
                            "webhook": "inbound webhook",
                            "none": "not configured"}[mode]}
