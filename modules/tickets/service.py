"""Loading, normalizing and rolling up tickets by customer."""
from collections import Counter
from datetime import datetime, timedelta

from . import config, knack_client, normalize


class NotConfigured(Exception):
    """Raised when the field map has not been set yet."""


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
def _hub_clients():
    """The Hub's own client registry, when this is running inside the Hub."""
    for module_name, fn_name in (("hub.clients_registry", "all_clients"),
                                 ("hub.knack_data", "get_clients"),
                                 ("hub.knack_data", "all_clients")):
        try:
            mod = __import__(module_name, fromlist=["*"])
            fn = getattr(mod, fn_name, None)
            if not callable(fn):
                continue
            rows = fn() or []
            out = []
            for row in rows:
                name = normalize.text(row.get("name") or row.get("client_name"))
                if not name:
                    continue
                out.append({
                    "id": normalize.text(row.get("id") or row.get("knack_id")),
                    "name": name,
                    "monthly_price": normalize.number(
                        row.get("monthly_price") or row.get("monthly") or
                        row.get("price") or 0),
                    "products": row.get("products") or [],
                    "status": normalize.text(row.get("status")) or "Active",
                    "source": "hub",
                })
            if out:
                return out
        except Exception:
            continue
    return None


def load_clients(force=False):
    hub_rows = _hub_clients()
    if hub_rows:
        return hub_rows

    eff = config.effective()
    object_key = eff["clients_object"]
    fmap = eff["client_fieldmap"]
    if knack_client.demo_mode():
        from .demo import demo_client_fieldmap
        object_key = object_key or "object_demo_clients"
        fmap = fmap or demo_client_fieldmap()
    if not object_key or not fmap.get("name"):
        return []

    records, _at = knack_client.fetch_records(object_key, force=force)
    clients = []
    for rec in records:
        name = normalize.strip_html(rec.get(fmap["name"]))
        if not name:
            continue
        products = normalize.text(rec.get(fmap.get("products")))
        clients.append({
            "id": normalize.text(rec.get("id")),
            "name": name,
            "monthly_price": normalize.number(
                rec.get(f"{fmap.get('monthly_price')}_raw",
                        rec.get(fmap.get("monthly_price")))),
            "products": [p.strip() for p in products.split(",") if p.strip()],
            "status": normalize.text(rec.get(fmap.get("status"))) or "Active",
            "source": "knack",
        })
    return clients


# --------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------
_auto: dict = {}
_auto_why: dict = {}


def auto_reason(object_key: str) -> str:
    """Why the auto map is what it is — read the object, or could not."""
    return _auto_why.get(object_key or "", "")


def auto_fieldmap(object_key: str) -> dict:
    """The map this module can build without anyone opening the setup page.

    The ids we hold are pinned (config.CONFIRMED_FIELDS, read from
    hub.knack_api), and the handful nobody has pinned — the dates above all —
    are matched against the live object's labels, exactly as the setup page
    does when a person clicks Auto-detect. Doing it here means the report
    works on a fresh deployment instead of greeting the first visitor with
    "map the ticket object and its required fields".

    A saved map still wins: someone who has corrected a guess must not have it
    re-guessed under them. Never raises — if the schema cannot be read the
    caller falls back to NotConfigured, which says what to do.
    """
    if not object_key:
        _auto_why[""] = "No ticket object is set."
        return {}
    if object_key in _auto:
        return _auto[object_key]
    try:
        fields = knack_client.list_fields(object_key)
    except Exception as exc:             # noqa: BLE001 — see the docstring
        # Recorded rather than swallowed. A map that could not be built and a
        # map that came back short need different fixes, and a caller told
        # only "not configured" cannot tell which it has.
        _auto_why[object_key] = (
            f"Could not read {object_key}'s fields from Knack "
            f"({type(exc).__name__}: {str(exc)[:120]}).")
        return {}
    guessed = normalize.guess_fieldmap(fields, config.FIELDS)
    _auto[object_key] = guessed
    _auto_why[object_key] = (
        f"Read {len(fields)} fields from {object_key} and mapped "
        f"{len(guessed)} of {len(config.FIELDS)}.")
    return guessed


def load_tickets(force=False):
    """(tickets, fetched_at). Raises NotConfigured when the map is incomplete."""
    eff = config.effective()
    object_key = eff["tickets_object"]
    fmap = eff["fieldmap"]

    if knack_client.demo_mode():
        from .demo import demo_fieldmap
        object_key = object_key or "object_demo_tickets"
        fmap = fmap or demo_fieldmap()
    else:
        # A saved map wins field by field, not wholesale. Half a map saved
        # from the setup page used to disable the auto map entirely, so one
        # unmapped field left the whole report at "not configured" with the
        # ids sitting right there — the same failure this fix was for.
        fmap = {**auto_fieldmap(object_key), **(fmap or {})}

    missing = [k for k in config.REQUIRED_FIELDS if not fmap.get(k)]
    if not object_key or missing:
        why = auto_reason(object_key)
        raise NotConfigured(
            "Map the ticket object and its required fields on the setup page "
            f"(still needed: {', '.join(missing) or 'ticket object'})."
            + (f" {why}" if why else "")
        )

    records, at = knack_client.fetch_records(object_key, force=force)
    now = datetime.now()

    def get(rec, key):
        fk = fmap.get(key)
        return rec.get(fk) if fk else None

    def get_date(rec, key):
        fk = fmap.get(key)
        if not fk:
            return None
        return normalize.parse_date(rec.get(fk), rec.get(f"{fk}_raw"))

    tickets = []
    for rec in records:
        client_id, client_name = normalize.client_ref(rec, fmap.get("client"))
        status = normalize.text(get(rec, "status"))
        opened = get_date(rec, "opened")
        closed = get_date(rec, "closed")
        last = get_date(rec, "last_update") or closed or opened
        state = normalize.classify_status(status)
        if state == "unknown":
            state = "closed" if closed else "open"

        hours = normalize.number(get(rec, "time_spent"))
        # A "time spent" field holding minutes is common; treat >40 as minutes.
        if hours > 40:
            hours = round(hours / 60.0, 2)

        ticket = {
            "id": normalize.text(rec.get("id")),
            "number": normalize.text(get(rec, "ticket_number")) or normalize.text(rec.get("id"))[-6:],
            "client_id": client_id,
            "client_name": client_name,
            "client_key": normalize.name_key(client_name),
            "status": status or ("Closed" if closed else "Open"),
            "state": state,
            "blocked": normalize.is_blocked(status),
            "opened": opened,
            "closed": closed,
            "last_update": last,
            "type": normalize.text(get(rec, "type")) or "Uncategorized",
            "priority": normalize.text(get(rec, "priority")),
            "assignee": normalize.text(get(rec, "assignee")) or "Unassigned",
            "summary": normalize.strip_html(get(rec, "summary"))[:300],
            "source": normalize.text(get(rec, "source")),
            "hours": hours,
        }
        ticket["age_days"] = _days(opened, now) if state == "open" else None
        ticket["resolve_days"] = _days(opened, closed) if (opened and closed) else None
        ticket["idle_days"] = _days(last, now) if state == "open" else None
        tickets.append(ticket)

    tickets.sort(key=lambda t: t["opened"] or datetime.min, reverse=True)
    return tickets, at


def _days(start, end):
    if not start or not end:
        return None
    return max(0, round((end - start).total_seconds() / 86400.0, 1))


# --------------------------------------------------------------------------
# Rollups
# --------------------------------------------------------------------------
def build(force=False, window_days=None):
    """Everything the pages and reports need, computed once."""
    window_days = window_days or config.LOAD_WINDOW_DAYS
    tickets, at = load_tickets(force=force)
    clients = load_clients(force=force)
    now = datetime.now()
    cutoff = now - timedelta(days=window_days)

    by_key = {}
    for client in clients:
        by_key.setdefault(normalize.name_key(client["name"]), client)

    rollups = {}

    def blank(client):
        return {
            "client_id": client.get("id", ""),
            "name": client["name"],
            "monthly_price": client.get("monthly_price", 0.0),
            "products": client.get("products", []),
            "client_status": client.get("status", ""),
            "matched": bool(client.get("id") or client.get("source")),
            "tickets": [],
        }

    for client in clients:
        rollups[normalize.name_key(client["name"])] = blank(client)

    unmatched = {}
    for ticket in tickets:
        key = ticket["client_key"]
        bucket = rollups.get(key)
        if bucket is None:
            match = normalize.match_client(ticket["client_name"], by_key, clients)
            if match:
                key = normalize.name_key(match["name"])
                bucket = rollups[key]
                ticket["matched_to"] = match["name"]
            else:
                bucket = unmatched.setdefault(key or "(blank)", {
                    "client_id": "", "name": ticket["client_name"] or "(no client)",
                    "monthly_price": 0.0, "products": [], "client_status": "",
                    "matched": False, "tickets": [],
                })
        bucket["tickets"].append(ticket)

    rollups.update(unmatched)

    rows = []
    for key, bucket in rollups.items():
        rows.append(_summarize(key, bucket, now, cutoff, window_days))

    rows.sort(key=lambda r: (-r["open"], -r["window_total"], r["name"].lower()))
    return {
        "rows": rows,
        "tickets": tickets,
        "clients": clients,
        "fetched_at": at,
        "window_days": window_days,
        "demo": knack_client.demo_mode(),
        "totals": _totals(rows, tickets),
    }


def _summarize(key, bucket, now, cutoff, window_days):
    tickets = bucket["tickets"]
    open_tickets = [t for t in tickets if t["state"] == "open"]
    closed_tickets = [t for t in tickets if t["state"] == "closed"]
    window = [t for t in tickets if t["opened"] and t["opened"] >= cutoff]

    resolves = [t["resolve_days"] for t in closed_tickets if t["resolve_days"] is not None]
    avg_resolve = round(sum(resolves) / len(resolves), 1) if resolves else None

    hours_window = sum(t["hours"] or 0 for t in window)
    if hours_window <= 0 and window:
        hours_window = len(window) * config.ASSUMED_HOURS_PER_TICKET
        hours_estimated = True
    else:
        hours_estimated = False

    months = max(window_days / 30.0, 1.0)
    per_month = round(len(window) / months, 1)
    monthly_price = bucket["monthly_price"] or 0.0
    cost_to_serve = round((hours_window / months) * config.INTERNAL_HOURLY_RATE, 2)
    pct = round((cost_to_serve / monthly_price) * 100, 1) if monthly_price else None

    allowance = config.allowance_for(monthly_price)
    breach = [t for t in open_tickets
              if (t["age_days"] or 0) > config.SLA_DAYS and not t["blocked"]]
    warn = [t for t in open_tickets
            if config.SLA_WARN_DAYS <= (t["age_days"] or 0) <= config.SLA_DAYS
            and not t["blocked"]]
    stale = [t for t in open_tickets if (t["idle_days"] or 0) > config.STALE_DAYS]

    if pct is None or not window:
        # No price, or nothing logged in the window. Reporting that as healthy
        # would read as a measurement rather than an absence of one.
        load_state = "unknown"
        if not window:
            pct = None
    elif pct >= config.LOAD_FLAG_PCT:
        load_state = "over"
    elif pct >= config.LOAD_WARN_PCT:
        load_state = "watch"
    else:
        load_state = "ok"

    last_opened = max([t["opened"] for t in tickets if t["opened"]], default=None)
    types = Counter(t["type"] for t in tickets if t["type"])

    return {
        "key": key,
        "client_id": bucket["client_id"],
        "name": bucket["name"],
        "matched": bucket["matched"],
        "monthly_price": monthly_price,
        "products": bucket["products"],
        "client_status": bucket["client_status"],
        "total": len(tickets),
        "open": len(open_tickets),
        "closed": len(closed_tickets),
        "window_total": len(window),
        "per_month": per_month,
        "allowance": allowance,
        "over_allowance": round(per_month - allowance, 1),
        "hours_window": round(hours_window, 2),
        "hours_estimated": hours_estimated,
        "cost_to_serve": cost_to_serve,
        "pct_of_revenue": pct,
        "load_state": load_state,
        "avg_resolve_days": avg_resolve,
        "breaches": len(breach),
        "approaching": len(warn),
        "stale": len(stale),
        "oldest_open_days": max([t["age_days"] or 0 for t in open_tickets], default=0),
        "last_opened": last_opened.isoformat() if last_opened else None,
        "days_since_last": _days(last_opened, now) if last_opened else None,
        "top_type": types.most_common(1)[0][0] if types else "",
        "unassigned": len([t for t in open_tickets if t["assignee"] == "Unassigned"]),
    }


def _totals(rows, tickets):
    open_tickets = [t for t in tickets if t["state"] == "open"]
    return {
        "clients": len([r for r in rows if r["matched"]]),
        "tickets": len(tickets),
        "open": len(open_tickets),
        "breaches": sum(r["breaches"] for r in rows),
        "stale": sum(r["stale"] for r in rows),
        "over_load": len([r for r in rows if r["load_state"] == "over"]),
        "unmatched": len([r for r in rows if not r["matched"]]),
    }


def client_detail(key, force=False):
    data = build(force=force)
    row = next((r for r in data["rows"] if r["key"] == key), None)
    if row is None:
        return None
    tickets = [t for t in data["tickets"]
               if t["client_key"] == key or normalize.name_key(
                   t.get("matched_to") or "") == key]
    by_type = Counter(t["type"] for t in tickets)
    by_month = Counter()
    for t in tickets:
        if t["opened"]:
            by_month[t["opened"].strftime("%Y-%m")] += 1
    return {
        "row": row,
        "tickets": tickets,
        "by_type": by_type.most_common(),
        "by_month": sorted(by_month.items()),
        "fetched_at": data["fetched_at"],
        "demo": data["demo"],
    }
