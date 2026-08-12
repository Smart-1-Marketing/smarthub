"""QA reports — data-quality and billing checks across Smart 1 Team + QuickBooks.

Each report returns {"columns": [...], "rows": [...], "note": str} where every
row is a list matching the columns.  A cell may also be a {"text","href"} dict,
which the report page renders as a link.  Everything else is plain text.

Knack-only reports run straight off clients_app/data/*.json; the two invoice
reports need a connected QuickBooks company and degrade with a friendly note
when it isn't connected.
"""
import datetime as _dt

from . import knack_data


# ------------------------------------------------------------------ helpers
def _num(v):
    return knack_data._num(v)


def _parse_date(s):
    """mm/dd/yyyy -> date, else None."""
    s = str(s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def _c360_link(client: str) -> dict:
    from urllib.parse import quote
    return {"text": client, "href": "/client360?q=" + quote(client)}


def _client_groups() -> dict:
    """{client_name: {"rows": [...], "partner", "sales", "live": [...],
        "thisM": bool, "lastM": bool, "this_total", "last_total",
        "live_total", "has_dash": bool, "last_end": date|None}}"""
    groups: dict[str, dict] = {}
    for r in knack_data.products():
        client = str(r.get("client", "")).strip()
        if not client:
            continue
        g = groups.setdefault(client, {
            "rows": [], "live": [], "thisM": False, "lastM": False,
            "this_total": 0.0, "last_total": 0.0, "live_total": 0.0,
            "has_dash": False, "last_end": None,
            "partners": set(), "sales": set(),
        })
        g["rows"].append(r)
        if r.get("partner"):
            g["partners"].add(str(r["partner"]).strip())
        if r.get("sales"):
            g["sales"].add(str(r["sales"]).strip())
        m = _num(r.get("monthly"))
        if r.get("thisM"):
            g["thisM"] = True
            g["this_total"] += m
        if r.get("lastM"):
            g["lastM"] = True
            g["last_total"] += m
        if str(r.get("status", "")).strip().lower() == "live":
            g["live"].append(r)
            g["live_total"] += m
            if isinstance(r.get("dash"), str) and r["dash"].startswith("http"):
                g["has_dash"] = True
        end = _parse_date(r.get("end"))
        if end and (g["last_end"] is None or end > g["last_end"]):
            g["last_end"] = end
    return groups


def _is_active(g: dict) -> bool:
    return bool(g["live"]) or g["thisM"]


def _join(vals) -> str:
    return ", ".join(sorted(v for v in vals if v)) or "—"


def _norm_name(s: str) -> str:
    """Normalize a business name for QB<->Knack matching."""
    import re
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    drop = {"inc", "llc", "co", "corp", "company", "the", "of", "and", "dba",
            "ltd", "lp", "pllc", "pc", "group"}
    words = [w for w in s.split() if w and w not in drop]
    return " ".join(words)


# ------------------------------------------------------------------ reports
def active_clients() -> dict:
    groups = _client_groups()
    rows = []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _is_active(g):
            continue
        rows.append([
            _c360_link(name),
            _join(g["partners"]),
            _join(g["sales"]),
            len(g["live"]),
            _money(g["live_total"]),
            "Yes" if g["has_dash"] else "No",
        ])
    return {
        "columns": ["Client", "Partner", "Salesperson", "Live products",
                    "Live monthly", "Dashboard"],
        "rows": rows,
        "note": f"{len(rows)} clients with a live product or billing this month.",
    }


def no_dashboards() -> dict:
    groups = _client_groups()
    rows = []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _is_active(g) or g["has_dash"]:
            continue
        prods = sorted({str(r.get("product") or "") for r in g["live"]} or
                       {str(r.get("product") or "") for r in g["rows"] if r.get("thisM")})
        rows.append([
            _c360_link(name),
            _join(g["partners"]),
            _join(g["sales"]),
            len(g["live"]),
            ", ".join(p for p in prods if p)[:120] or "—",
            _money(g["live_total"] or g["this_total"]),
        ])
    return {
        "columns": ["Client", "Partner", "Salesperson", "Live products",
                    "Products", "Monthly"],
        "rows": rows,
        "note": (f"{len(rows)} active clients with no Smart 1 Dashboard link on "
                 "any live product — these clients can't see their reporting."),
    }


def stale_90() -> dict:
    groups = _client_groups()
    today = _dt.date.today()
    rows = []
    for name, g in groups.items():
        if g["live"] or g["thisM"]:          # currently active — skip
            continue
        if not g["last_end"]:
            continue
        days = (today - g["last_end"]).days
        if days < 90 or days > 730:          # gone quiet 3–24 months ago
            continue
        last_total = max(g["last_total"], max(
            (_num(r.get("monthly")) for r in g["rows"]
             if _parse_date(r.get("end")) == g["last_end"]), default=0.0))
        rows.append((days, [
            _c360_link(name),
            _join(g["partners"]),
            _join(g["sales"]),
            g["last_end"].strftime("%m/%d/%Y"),
            days,
            _money(last_total),
        ]))
    rows.sort(key=lambda t: t[0])
    return {
        "columns": ["Client", "Partner", "Salesperson", "Last product ended",
                    "Days since", "Last monthly"],
        "rows": [r for _, r in rows],
        "note": (f"{len(rows)} clients with no live product whose last IO ended "
                 "90+ days ago (showing up to 24 months back, most recent first) "
                 "— win-back candidates."),
    }


def lost_by_partner() -> dict:
    groups = _client_groups()
    rows = []
    for name, g in groups.items():
        if g["lastM"] and not g["thisM"] and not g["live"]:
            partner = _join(g["partners"])
            rows.append((partner.lower(), [
                partner,
                _c360_link(name),
                _join(g["sales"]),
                _money(g["last_total"]),
            ]))
    rows.sort(key=lambda t: (t[0], str(t[1][1].get("text", "")).lower()))
    total = sum(_num(str(r[3]).replace("$", "")) for _, r in rows)
    return {
        "columns": ["Partner", "Client", "Salesperson", "Billing last month"],
        "rows": [r for _, r in rows],
        "note": (f"{len(rows)} clients ran last month but have nothing live this "
                 f"month — {_money(total)}/mo walked out the door. Grouped by partner."),
    }


def _scorecard(field: str) -> dict:
    """Shared engine for the salesperson / partner scorecards."""
    by: dict[str, dict] = {}
    for r in knack_data.products():
        who = str(r.get(field, "")).strip()
        client = str(r.get("client", "")).strip()
        if not who or not client:
            continue
        s = by.setdefault(who, {
            "clients_live": set(), "clients_this": set(), "clients_last": set(),
            "live_products": 0, "live_total": 0.0,
            "this_by": {}, "last_by": {},
        })
        m = _num(r.get("monthly"))
        if str(r.get("status", "")).strip().lower() == "live":
            s["live_products"] += 1
            s["live_total"] += m
            s["clients_live"].add(client)
        if r.get("thisM"):
            s["clients_this"].add(client)
            s["this_by"][client] = s["this_by"].get(client, 0.0) + m
        if r.get("lastM"):
            s["clients_last"].add(client)
            s["last_by"][client] = s["last_by"].get(client, 0.0) + m
    rows = []
    for who in sorted(by, key=lambda k: -by[k]["live_total"]):
        s = by[who]
        active = s["clients_live"] | s["clients_this"]
        new = sum(1 for c in s["this_by"] if c not in s["last_by"])
        lost = sum(1 for c in s["last_by"] if c not in s["this_by"])
        up = sum(1 for c, v in s["this_by"].items()
                 if c in s["last_by"] and v > s["last_by"][c] + 0.5)
        down = sum(1 for c, v in s["this_by"].items()
                   if c in s["last_by"] and v < s["last_by"][c] - 0.5)
        rows.append([who, len(active), s["live_products"],
                     _money(s["live_total"]), new, lost, up, down])
    label = "salespeople" if field == "sales" else "partners"
    return {
        "columns": [("Salesperson" if field == "sales" else "Partner"),
                    "Active clients", "Live products", "Live monthly",
                    "New", "Lost", "Increased", "Decreased"],
        "rows": rows,
        "note": (f"{len(rows)} {label}, ranked by live monthly billing. "
                 "New/Lost/Increased/Decreased compare this month's IOs to last month's."),
    }


def salesperson_scorecard() -> dict:
    return _scorecard("sales")


def partner_scorecard() -> dict:
    return _scorecard("partner")


# ------------------------------------------------ QuickBooks-backed reports
def _qb_state():
    from . import quickbooks as qb
    if not qb.configured():
        return qb, ("QuickBooks isn't configured — set QB_CLIENT_ID / "
                    "QB_CLIENT_SECRET and connect from System Status.")
    if not qb.connected():
        return qb, ("QuickBooks isn't connected yet — use Connect QuickBooks "
                    "on the System Status page, then re-run this report.")
    return qb, None


def _month_keys(months: int = 4) -> list[str]:
    """['YYYY-MM' this month, last, 2 prior, 3 prior]"""
    first = _dt.date.today().replace(day=1)
    keys = []
    for _ in range(months):
        keys.append(first.strftime("%Y-%m"))
        first = (first - _dt.timedelta(days=1)).replace(day=1)
    return keys


def _month_label(ym: str) -> str:
    y, m = ym.split("-")
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[int(m)]} {y}"


def billing_comparison() -> dict:
    qb, err = _qb_state()
    keys = _month_keys(4)                      # [this, last, prior2, prior3]
    columns = ["Customer", _month_label(keys[3]), _month_label(keys[2]),
               _month_label(keys[1]), _month_label(keys[0]),
               "Change vs last month"]
    if err:
        return {"columns": columns, "rows": [], "note": err, "needs_qb": True}
    data = qb.monthly_totals_by_customer(4)
    decreases, increases = [], []
    for name in sorted(data, key=str.lower):
        rec = data[name]
        vals = [rec["months"].get(k, 0.0) for k in keys]  # this..prior3
        this, last = vals[0], vals[1]
        delta = round(this - last, 2)
        if abs(delta) < 0.5:
            continue                            # unchanged — not listed
        cust_cell = ({"text": name, "href": qb.customer_link(rec["id"])}
                     if rec.get("id") else name)
        row = [cust_cell, _money(vals[3]), _money(vals[2]),
               _money(vals[1]), _money(vals[0]),
               ("▼ " if delta < 0 else "▲ ") + _money(abs(delta))]
        (decreases if delta < 0 else increases).append((abs(delta), row))
    decreases.sort(key=lambda t: -t[0])
    increases.sort(key=lambda t: -t[0])
    rows = [r for _, r in decreases] + [r for _, r in increases]
    return {
        "columns": columns,
        "rows": rows,
        "note": (f"{len(decreases)} customers invoiced less this month than last, "
                 f"{len(increases)} invoiced more (decreases listed first, biggest "
                 "swing at the top). Totals are summed QuickBooks invoices per "
                 "calendar month; customer names link into QuickBooks."),
    }


def invoice_off() -> dict:
    qb, err = _qb_state()
    columns = ["Customer", "Invoiced this month", "Active products / mo",
               "Difference", "Live products"]
    if err:
        return {"columns": columns, "rows": [], "note": err, "needs_qb": True}
    data = qb.monthly_totals_by_customer(2)     # this + last month is plenty
    this_key = _month_keys(1)[0]

    groups = _client_groups()
    knack_by_norm: dict[str, tuple[str, dict]] = {}
    for name, g in groups.items():
        n = _norm_name(name)
        if n:
            knack_by_norm.setdefault(n, (name, g))

    rows = []
    matched_norms = set()
    for cust in sorted(data, key=str.lower):
        rec = data[cust]
        invoiced = rec["months"].get(this_key, 0.0)
        n = _norm_name(cust)
        hit = knack_by_norm.get(n)
        if not hit:      # try containment both ways for near-matches
            hit = next(((kn, kg) for norm, (kn, kg) in knack_by_norm.items()
                        if norm and n and (norm in n or n in norm)), None)
        if not hit:
            continue     # QB customer with no Smart 1 Team client — skip
        kname, g = hit
        matched_norms.add(_norm_name(kname))
        expected = g["live_total"]
        diff = round(invoiced - expected, 2)
        if abs(diff) < 0.5:
            continue
        cust_cell = ({"text": cust, "href": qb.customer_link(rec["id"])}
                     if rec.get("id") else cust)
        rows.append((abs(diff), [
            cust_cell,
            _money(invoiced),
            _money(expected),
            ("▼ " if diff < 0 else "▲ ") + _money(abs(diff)),
            len(g["live"]),
        ]))
    # active Knack clients with NO invoice at all this month
    for name, g in groups.items():
        if not _is_active(g) or g["live_total"] < 0.5:
            continue
        if _norm_name(name) in matched_norms:
            continue
        n = _norm_name(name)
        in_qb = any(n and (_norm_name(c) == n or n in _norm_name(c) or _norm_name(c) in n)
                    for c in data)
        if in_qb:
            continue
        rows.append((g["live_total"], [
            _c360_link(name), _money(0), _money(g["live_total"]),
            "▼ " + _money(g["live_total"]) + " (no invoice found)",
            len(g["live"]),
        ]))
    rows.sort(key=lambda t: -t[0])
    return {
        "columns": columns,
        "rows": [r for _, r in rows],
        "note": (f"{len(rows)} customers whose QuickBooks invoices this month "
                 "don't match their active-product monthly total (matched by "
                 "business name; biggest gap first). ▼ = invoiced less than "
                 "active products, ▲ = invoiced more."),
    }


# ------------------------------------------------------------------ registry
REPORTS = {
    "active-clients": {
        "title": "Active Clients",
        "desc": "Every client with a live product or billing this month — partner, salesperson, live monthly and dashboard status.",
        "ico": "&#9679;",
        "fn": active_clients,
        "group": "Clients",
    },
    "no-dashboards": {
        "title": "No Dashboards",
        "desc": "Active clients with no Smart 1 Dashboard link on any live product — they can't see their reporting.",
        "ico": "&#9888;",
        "fn": no_dashboards,
        "group": "Clients",
    },
    "stale-90": {
        "title": "No Live Product in 90 Days",
        "desc": "Clients gone quiet — last IO ended 90+ days ago and nothing live now. Win-back candidates.",
        "ico": "&#8987;",
        "fn": stale_90,
        "group": "Clients",
    },
    "lost-by-partner": {
        "title": "Ran Last Month, Not This Month",
        "desc": "Clients that billed last month with nothing live this month — grouped by partner so you can see who's churning.",
        "ico": "&#8595;",
        "fn": lost_by_partner,
        "group": "Clients",
    },
    "sales-scorecard": {
        "title": "Salesperson Scorecard",
        "desc": "Active clients, live products and billing per salesperson, with month-over-month new / lost / increased / decreased.",
        "ico": "&#127942;",
        "fn": salesperson_scorecard,
        "group": "Scorecards",
    },
    "partner-scorecard": {
        "title": "Partner Scorecard",
        "desc": "The same scorecard rolled up by media partner — who's growing and who's shrinking.",
        "ico": "&#129309;",
        "fn": partner_scorecard,
        "group": "Scorecards",
    },
    "billing-comparison": {
        "title": "Customer Billing Comparison",
        "desc": "QuickBooks invoices per customer: this month vs the last three. Decreases listed first, biggest swings on top.",
        "ico": "&#128181;",
        "fn": billing_comparison,
        "group": "Billing (QuickBooks)",
    },
    "invoice-off": {
        "title": "Invoice Off Report",
        "desc": "Customers whose invoiced amount this month doesn't match their active-product monthly total.",
        "ico": "&#9878;",
        "fn": invoice_off,
        "group": "Billing (QuickBooks)",
    },
}


def run(key: str) -> dict:
    meta = REPORTS.get(key)
    if not meta:
        raise KeyError(key)
    out = meta["fn"]()
    out["key"] = key
    out["title"] = meta["title"]
    return out
