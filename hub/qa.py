"""QA reports — data-quality and billing checks across Smart 1 Team + QuickBooks.

Each report returns {"columns": [...], "rows": [...], "note": str} where every
row is a list matching the columns.  A cell may also be a {"text","href"} dict,
which the report page renders as a link.  Everything else is plain text.

Knack-only reports run straight off clients_app/data/*.json; the two invoice
reports need a connected QuickBooks company and degrade with a friendly note
when it isn't connected.
"""
import datetime as _dt
import json
import os

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
    by_partner: dict[str, list] = {}
    total = 0
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _is_active(g) or g["has_dash"]:
            continue
        total += 1
        prods = sorted({str(r.get("product") or "") for r in g["live"]} or
                       {str(r.get("product") or "") for r in g["rows"] if r.get("thisM")})
        partner = _join(g["partners"])
        by_partner.setdefault(partner, []).append([
            partner if partner != "—" else "(no partner)",
            _c360_link(name),
            _join(g["sales"]),
            len(g["live"]),
            ", ".join(p for p in prods if p)[:120] or "—",
            _money(g["live_total"] or g["this_total"]),
        ])
    rows, styles = [], []
    keys = sorted([k for k in by_partner if k != "—"], key=str.lower) + \
        (["—"] if "—" in by_partner else [])
    for k in keys:
        for r in by_partner[k]:
            rows.append(r)
            styles.append(None)
    return {
        "columns": ["Partner", "Client", "Salesperson", "Live products",
                    "Products", "Monthly"],
        "rows": rows,
        "row_styles": styles,
        "note": (f"{total} active clients with no Smart 1 Dashboard link on any "
                 "live product — broken down by partner (clients with no partner "
                 "listed at the end)."),
    }


def stale_90() -> dict:
    groups = _client_groups()
    today = _dt.date.today()
    by_partner: dict[str, list] = {}
    total = 0
    for name, g in groups.items():
        if g["live"] or g["thisM"]:          # currently active — skip
            continue
        if not g["last_end"]:
            continue
        days = (today - g["last_end"]).days
        if days < 90 or days > 730:          # gone quiet 3–24 months ago
            continue
        total += 1
        last_total = max(g["last_total"], max(
            (_num(r.get("monthly")) for r in g["rows"]
             if _parse_date(r.get("end")) == g["last_end"]), default=0.0))
        partner = _join(g["partners"])
        by_partner.setdefault(partner, []).append((days, last_total, [
            partner if partner != "—" else "(no partner)",
            _c360_link(name),
            _join(g["sales"]),
            g["last_end"].strftime("%m/%d/%Y"),
            days,
            _money(last_total),
        ]))
    rows, styles = [], []
    keys = sorted([k for k in by_partner if k != "—"], key=str.lower) + \
        (["—"] if "—" in by_partner else [])
    grand = 0.0
    for k in keys:
        items = sorted(by_partner[k], key=lambda t: t[0])
        sub = sum(t[1] for t in items)
        grand += sub
        for _, _, r in items:
            rows.append(r)
            styles.append(None)
        label = k if k != "—" else "(no partner)"
        rows.append([f"{label} — subtotal", f"{len(items)} client(s)", "", "", "",
                     _money(sub)])
        styles.append("sub")
    return {
        "columns": ["Partner", "Client", "Salesperson", "Last product ended",
                    "Days since", "Last monthly"],
        "rows": rows,
        "row_styles": styles,
        "note": (f"{total} clients with no live product whose last IO ended 90+ "
                 f"days ago (up to 24 months back) — {_money(grand)}/mo of lapsed "
                 "billing, grouped by partner with subtotals; clients without a "
                 "partner listed at the end."),
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


def _last_12_months() -> list[dict]:
    first = _dt.date.today().replace(day=1)
    out = []
    for _ in range(12):
        out.append({"ym": first.strftime("%Y%m"), "label": first.strftime("%b %Y")})
        first = (first - _dt.timedelta(days=1)).replace(day=1)
    return out


def _month_bounds(ym: str):
    import calendar
    y, m = int(ym[:4]), int(ym[4:6])
    return _dt.date(y, m, 1), _dt.date(y, m, calendar.monthrange(y, m)[1])


def _active_in_month(r: dict, mstart, mend) -> bool:
    """An IO counts for a month when its date range covers any of it and it
    actually ran (Live or Complete — cancelled/pending never count)."""
    status = str(r.get("status", "")).strip().lower()
    if status not in ("live", "complete"):
        return False
    s, e = _parse_date(r.get("start")), _parse_date(r.get("end"))
    if s and s > mend:
        return False
    if e and e < mstart:
        return False
    if not s and not e:          # undated: only trust currently-live rows
        return status == "live"
    return True


def _month_rollup(field: str, ym: str) -> dict:
    """{who: {"clients": {client: budget}, "products": n, "revenue": x}}"""
    mstart, mend = _month_bounds(ym)
    by: dict[str, dict] = {}
    for r in knack_data.products():
        who = str(r.get(field, "")).strip()
        client = str(r.get("client", "")).strip()
        if not who or not client:
            continue
        if not _active_in_month(r, mstart, mend):
            continue
        s = by.setdefault(who, {"clients": {}, "products": 0, "revenue": 0.0})
        m = _num(r.get("monthly"))
        s["clients"][client] = s["clients"].get(client, 0.0) + m
        s["products"] += 1
        s["revenue"] += m
    return by


def _prev_ym(ym: str) -> str:
    first, _ = _month_bounds(ym)
    return (first - _dt.timedelta(days=1)).strftime("%Y%m")


def _scorecard(field: str, month: str = "") -> dict:
    """Salesperson / partner scorecard for any of the last 12 months.
    Rows with zero active clients are hidden; each row is colored by the
    person's revenue vs the previous month (green up / yellow flat / red down)."""
    months = _last_12_months()
    ym = month if month in {m["ym"] for m in months} else months[0]["ym"]
    cur = _month_rollup(field, ym)
    prev = _month_rollup(field, _prev_ym(ym))

    rows, styles = [], []
    for who in sorted(cur, key=lambda k: -cur[k]["revenue"]):
        s = cur[who]
        if not s["clients"]:
            continue
        p = prev.get(who, {"clients": {}, "revenue": 0.0})
        new = sum(1 for c in s["clients"] if c not in p["clients"])
        lost = sum(1 for c in p["clients"] if c not in s["clients"])
        up = sum(1 for c, v in s["clients"].items()
                 if c in p["clients"] and v > p["clients"][c] + 0.5)
        down = sum(1 for c, v in s["clients"].items()
                   if c in p["clients"] and v < p["clients"][c] - 0.5)
        rows.append([who, len(s["clients"]), s["products"], _money(s["revenue"]),
                     new, lost, up, down])
        diff = s["revenue"] - p["revenue"]
        styles.append("green" if diff > 0.5 else ("red" if diff < -0.5 else "yellow"))

    label = "salespeople" if field == "sales" else "partners"
    mlabel = next(m["label"] for m in months if m["ym"] == ym)
    return {
        "columns": [("Salesperson" if field == "sales" else "Partner"),
                    "Active clients", "Active products", "Monthly revenue",
                    "New", "Lost", "Increased", "Decreased"],
        "rows": rows,
        "row_styles": styles,
        "month": ym,
        "month_options": months,
        "note": (f"{len(rows)} {label} active in {mlabel}, ranked by monthly revenue. "
                 "Row color = revenue vs the previous month (green up · yellow flat · red down). "
                 "Counts an IO in a month when its start/end dates cover it."),
    }


def salesperson_scorecard(month: str = "") -> dict:
    return _scorecard("sales", month)


def partner_scorecard(month: str = "") -> dict:
    return _scorecard("partner", month)


# ------------------------------------- missing Google accounts (GA / GTM)
GTM_PRIORITY_KEYWORDS = ("display", "radio", "podcast", "audio", "seo",
                         "search engine marketing", "pay per click", "sem",
                         "paid search", "retargeting")


def _google_coverage(name: str, g: dict) -> dict:
    """What Google plumbing we have for a client: website GA/GTM fields plus
    manually attached accounts."""
    from . import seo
    webs = seo._client_websites(name)
    att = seo.get_links(name)
    domain = ""
    for w in webs:
        d = str(w.get("domain") or "").strip()
        if d:
            domain = d.replace("https://", "").replace("http://", "").strip("/")
            break
    return {
        "has_ga": bool(att.get("analytics")) or any(str(w.get("ga") or "").strip() for w in webs),
        "has_gtm": bool(att.get("gtm")) or any(str(w.get("gtm") or "").strip() for w in webs),
        "domain": domain,
    }


def no_analytics() -> dict:
    groups = _client_groups()
    rows, styles = [], []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _is_active(g):
            continue
        cov = _google_coverage(name, g)
        if cov["has_ga"]:
            continue
        rows.append([
            _c360_link(name),
            _join(g["partners"]),
            _join(g["sales"]),
            cov["domain"] or "—",
            _money(g["live_total"] or g["this_total"]),
            {"search_attach": name, "kind": "analytics", "q": cov["domain"] or name},
        ])
        styles.append(None)
    return {
        "columns": ["Client", "Partner", "Salesperson", "Website",
                    "Monthly", "Analytics account"],
        "rows": rows,
        "row_styles": styles,
        "note": (f"{len(rows)} active clients with no Google Analytics on file "
                 "(website record or attached account). Search your connected "
                 "Google logins and attach the right property — it's saved to "
                 "the client universally and the client drops off this report."),
    }


def no_gtm() -> dict:
    groups = _client_groups()
    priority, suggested = [], []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _is_active(g):
            continue
        cov = _google_coverage(name, g)
        if cov["has_gtm"]:
            continue
        active_products = {str(r.get("product") or "").lower() for r in g["rows"]
                           if str(r.get("status", "")).strip().lower() == "live" or r.get("thisM")}
        is_priority = any(any(k in p for k in GTM_PRIORITY_KEYWORDS) for p in active_products)
        row = [
            _c360_link(name),
            _join(g["partners"]),
            ", ".join(sorted({str(r.get("product") or "") for r in g["live"]}))[:100] or "—",
            _money(g["live_total"] or g["this_total"]),
            {"search_attach": name, "kind": "gtm", "q": cov["domain"] or name},
        ]
        (priority if is_priority else suggested).append(row)
    rows, styles = [], []
    if priority:
        rows.append([f"Running display / audio / SEO / paid search / retargeting — needs GTM ({len(priority)})",
                     "", "", "", ""])
        styles.append("sub")
        for r in priority:
            rows.append(r)
            styles.append(None)
    if suggested:
        rows.append([f"Suggested clients for GTM ({len(suggested)})", "", "", "", ""])
        styles.append("sub")
        for r in suggested:
            rows.append(r)
            styles.append(None)
    return {
        "columns": ["Client", "Partner", "Live products", "Monthly", "GTM container"],
        "rows": rows,
        "row_styles": styles,
        "note": (f"{len(priority) + len(suggested)} active clients with no GTM container on file. "
                 "The first group runs tag-dependent products (display, audio, SEO, paid "
                 "search, retargeting); the rest are suggested candidates. Attaching a "
                 "container saves it to the client universally and removes them here."),
    }


# ------------------------------------------- GHL: Accounting Requests
GHL_BASE = "https://services.leadconnectorhq.com"
_ghl_cache: dict = {}


def _ghl(path: str, params=None, method: str = "GET", body=None):
    import requests as _rq
    token = os.environ.get("GHL_PRIVATE_TOKEN", "")
    if not token:
        raise RuntimeError("GHL_PRIVATE_TOKEN is not configured.")
    headers = {"Authorization": f"Bearer {token}",
               "Version": os.environ.get("GHL_API_VERSION", "2021-07-28"),
               "Accept": "application/json", "Content-Type": "application/json"}
    r = _rq.request(method, GHL_BASE + path, params=params, json=body,
                    headers=headers, timeout=20)
    if not r.ok:
        raise RuntimeError(f"GHL {method} {path} failed (HTTP {r.status_code}): {r.text[:180]}")
    return r.json() if r.text else {}


def _accounting_location() -> tuple[str, str]:
    """(location_id, name) of the Smart 1 Marketing sub-account."""
    override = os.environ.get("GHL_ACCOUNTING_LOCATION_ID", "").strip()
    if override:
        return override, "Smart 1 Marketing"
    if "acct_loc" in _ghl_cache:
        return _ghl_cache["acct_loc"]
    data = _ghl("/locations/search", {
        "companyId": os.environ.get("GHL_COMPANY_ID", ""), "limit": "500"})
    locs = data.get("locations") or []
    hit = next((l for l in locs
                if "smart 1 marketing" in str(l.get("name", "")).lower()), None)
    if not hit:
        raise RuntimeError('No GHL sub-account named "Smart 1 Marketing" found — '
                           "set GHL_ACCOUNTING_LOCATION_ID to pin the location.")
    _ghl_cache["acct_loc"] = (hit.get("id") or hit.get("_id"), hit.get("name"))
    return _ghl_cache["acct_loc"]


def _accounting_pipeline(location_id: str) -> dict:
    key = "acct_pipe:" + location_id
    if key in _ghl_cache:
        return _ghl_cache[key]
    data = _ghl("/opportunities/pipelines", {"locationId": location_id})
    pipes = data.get("pipelines") or []
    hit = next((p for p in pipes
                if "accounting request" in str(p.get("name", "")).lower()), None)
    if not hit:
        names = ", ".join(str(p.get("name")) for p in pipes) or "none"
        raise RuntimeError(f'No "Accounting Requests" pipeline in that location '
                           f"(found: {names}).")
    _ghl_cache[key] = hit
    return hit


def accounting_requests() -> dict:
    columns = ["Request", "Contact", "Value", "Created", "GHL status", "Stage"]
    try:
        loc_id, loc_name = _accounting_location()
        pipe = _accounting_pipeline(loc_id)
    except RuntimeError as exc:
        return {"columns": columns, "rows": [], "note": str(exc)}
    stages = [{"id": s.get("id"), "name": s.get("name")}
              for s in (pipe.get("stages") or [])]
    stage_names = {s["id"]: s["name"] for s in stages}
    try:
        data = _ghl("/opportunities/search", {
            "location_id": loc_id, "pipeline_id": pipe.get("id"),
            "limit": 100})
    except RuntimeError as exc:
        return {"columns": columns, "rows": [], "note": str(exc)}
    opps = data.get("opportunities") or []
    rows = []
    for o in opps:
        contact = (o.get("contact") or {})
        created = str(o.get("createdAt") or "")[:10]
        val = knack_data._num(o.get("monetaryValue"))
        rows.append([
            {"text": o.get("name") or "(unnamed)",
             "href": f"{os.environ.get('GHL_APP_BASE', 'https://app.gohighlevel.com')}"
                     f"/v2/location/{loc_id}/opportunities/list"},
            (contact.get("name") or contact.get("email") or "—"),
            _money(val) if val else "—",
            created,
            str(o.get("status") or "open"),
            {"stage_select": o.get("id"),
             "current": o.get("pipelineStageId"),
             "current_name": stage_names.get(o.get("pipelineStageId"), "")},
        ])
    return {
        "columns": columns,
        "rows": rows,
        "stages": stages,
        "pipeline_id": pipe.get("id"),
        "note": (f"{len(rows)} requests in the \"{pipe.get('name')}\" pipeline "
                 f"({loc_name}). Change the stage right here — it updates GHL "
                 "immediately."),
    }


def set_accounting_stage(opp_id: str, stage_id: str) -> None:
    loc_id, _ = _accounting_location()
    pipe = _accounting_pipeline(loc_id)
    try:
        _ghl(f"/opportunities/{opp_id}/status", method="PUT",
             body={"pipelineId": pipe.get("id"), "pipelineStageId": stage_id})
    except RuntimeError:
        _ghl(f"/opportunities/{opp_id}", method="PUT",
             body={"pipelineId": pipe.get("id"), "pipelineStageId": stage_id})


# ---------------------------------------- invoice-off partner assignments
def _assign_path() -> str:
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "qa-invoice-partner.json")


def invoice_assignments() -> dict:
    try:
        with open(_assign_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def assign_invoice_partner(customer: str, partner: str):
    data = invoice_assignments()
    data[str(customer)] = str(partner)
    with open(_assign_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def partner_list() -> list[str]:
    return sorted({str(r.get("partner", "")).strip()
                   for r in knack_data.products() if str(r.get("partner", "")).strip()},
                  key=str.lower)


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

    # ---- "Compare invoices" narratives: each month vs the current month ----
    comparisons = []
    cur_key = keys[0]
    for prior in keys[1:]:
        cur_total = prior_total = 0.0
        inc, dec, started, stopped = [], [], [], []
        for name, rec in data.items():
            c = rec["months"].get(cur_key, 0.0)
            p = rec["months"].get(prior, 0.0)
            cur_total += c
            prior_total += p
            d = round(c - p, 2)
            if p and not c:
                stopped.append((p, name))
            elif c and not p:
                started.append((c, name))
            elif d > 0.5:
                inc.append((d, name))
            elif d < -0.5:
                dec.append((-d, name))
        inc.sort(reverse=True)
        dec.sort(reverse=True)
        started.sort(reverse=True)
        stopped.sort(reverse=True)
        net = round(cur_total - prior_total, 2)
        parts = [f"Total invoiced: {_money(cur_total)} this month vs "
                 f"{_money(prior_total)} in {_month_label(prior)} "
                 f"({'+' if net >= 0 else '−'}{_money(abs(net))} net)."]
        if dec:
            parts.append(f"{len(dec)} customer(s) are billing less now — biggest: "
                         + ", ".join(f"{n} (−{_money(v)})" for v, n in dec[:3]) + ".")
        if inc:
            parts.append(f"{len(inc)} customer(s) are billing more — biggest: "
                         + ", ".join(f"{n} (+{_money(v)})" for v, n in inc[:3]) + ".")
        if stopped:
            parts.append(f"{len(stopped)} billed in {_month_label(prior)} but have no "
                         "invoice this month: "
                         + ", ".join(f"{n} ({_money(v)})" for v, n in stopped[:3])
                         + ("…" if len(stopped) > 3 else "") + ".")
        if started:
            parts.append(f"{len(started)} are new since {_month_label(prior)}: "
                         + ", ".join(f"{n} (+{_money(v)})" for v, n in started[:3])
                         + ("…" if len(started) > 3 else "") + ".")
        if len(parts) == 1:
            parts.append("No customer-level changes beyond rounding.")
        comparisons.append({"month": _month_label(prior),
                            "vs": _month_label(cur_key),
                            "text": " ".join(parts)})

    return {
        "columns": columns,
        "rows": rows,
        "invoice_comparison": comparisons,
        "note": (f"{len(decreases)} customers invoiced less this month than last, "
                 f"{len(increases)} invoiced more (decreases listed first, biggest "
                 "swing at the top). Totals are summed QuickBooks invoices per "
                 "calendar month; customer names link into QuickBooks."),
    }


def invoice_off() -> dict:
    qb, err = _qb_state()
    columns = ["Customer", "Invoiced this month", "Active products / mo",
               "Difference", "Live products", "Partner"]
    if err:
        return {"columns": columns, "rows": [], "note": err, "needs_qb": True}
    assigned = invoice_assignments()
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
        if cust in assigned:            # resolved to a partner — never show again
            continue
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
            {"assign": cust},
        ]))
    # active Knack clients with NO invoice at all this month
    for name, g in groups.items():
        if not _is_active(g) or g["live_total"] < 0.5:
            continue
        if name in assigned:
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
            {"assign": name},
        ]))
    rows.sort(key=lambda t: -t[0])
    return {
        "columns": columns,
        "rows": [r for _, r in rows],
        "partners": partner_list(),
        "note": (f"{len(rows)} customers whose QuickBooks invoices this month "
                 "don't match their active-product monthly total (matched by "
                 "business name; biggest gap first). ▼ = invoiced less than "
                 "active products, ▲ = invoiced more. Use \"Add to partner\" to "
                 "mark a record as handled by a partner — it's remembered and "
                 "won't show here again (nothing changes anywhere else)."),
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
    "no-analytics": {
        "title": "Clients Without Analytics",
        "desc": "Active clients with no Google Analytics on file — search connected accounts and attach the right property.",
        "ico": "&#128200;",
        "fn": no_analytics,
        "group": "Clients",
    },
    "no-gtm": {
        "title": "Clients Without GTM",
        "desc": "Active clients with no GTM container — split into tag-dependent products vs suggested candidates.",
        "ico": "&#127991;",
        "fn": no_gtm,
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
    "accounting-requests": {
        "title": "Accounting Requests",
        "desc": "Every request in the Accounting Requests pipeline (Smart 1 Marketing · GHL) — change stages right from the report.",
        "ico": "&#128203;",
        "fn": accounting_requests,
        "group": "Accounting",
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


def run(key: str, month: str = "") -> dict:
    meta = REPORTS.get(key)
    if not meta:
        raise KeyError(key)
    if key in ("sales-scorecard", "partner-scorecard"):
        out = meta["fn"](month)
    else:
        out = meta["fn"]()
    out["key"] = key
    out["title"] = meta["title"]
    return out
