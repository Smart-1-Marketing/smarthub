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
import threading as _threading
import os
import re

from . import jsonstore
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


_SRC = _threading.local()


def _products() -> list[dict]:
    """The product rows every client report here is built from, live first.

    These reports read `knack_data.products()` — the hand-committed export
    that nothing refreshes — while Client 360 read the same object live
    through `_product_source()`. So the Hub held a current answer and a stale
    one to "what is this client running", and `/qa` took the stale one: the
    `hub/seo.py` failure, one page later, and silent in the same way, because
    a short list looks exactly like a complete one.

    The source that answered is recorded for `run()` to print. It is a
    thread-local rather than a return value because a dozen call sites read
    the grouping and none of them wants a tuple; and thread-local rather than
    a plain module dict because two requests must not be able to hand each
    other a staleness note about a read they did not make.
    """
    rows, source, age = knack_data._product_source()
    _SRC.source, _SRC.age = source, age
    return rows


def _source_note() -> str:
    """The sentence for whichever source the last `_products()` read, or ""."""
    source = getattr(_SRC, "source", "")
    return knack_data.products_note(source, getattr(_SRC, "age", None)) if source else ""


_MONTH_MEMO: dict = {}


def _billing_this_month(r: dict) -> bool:
    """Did this IO deliver in the current calendar month?

    The row's own `thisM` used to answer this, and two reports still read it
    per row rather than through `_client_groups()`. It is an export-only flag
    describing the month the export was generated for, so both of them would
    have gone quiet the moment this page started reading Knack live — the
    same trap the grouping above describes, in the two places the grouping
    does not cover.

    Bounds are memoised on the month rather than recomputed: `no_gtm` asks
    this of every row on the book.
    """
    ym = _dt.date.today().strftime("%Y%m")
    b = _MONTH_MEMO.get(ym)
    if b is None:
        b = _MONTH_MEMO[ym] = _month_bounds(ym)
    return _active_in_month(r, b[0], b[1])


def _client_groups() -> dict:
    """{client_name: {"rows": [...], "partner", "sales", "live": [...],
        "thisM": bool, "lastM": bool, "this_total", "last_total",
        "live_total", "has_dash": bool, "last_end": date|None}}

    **`thisM` and `lastM` are computed here, not read off the row.** They are
    Knack's own flags and they exist only on the export — `knack_products`'
    live rows carry neither — so simply pointing this at the live source
    would set both False on every row and every report built on them would go
    quiet rather than wrong: "billed this month" would read $0 for the whole
    book, `lost_by_partner` would report that nobody has ever churned, and
    `stale_90` would lose the guard that keeps a client we are billing off
    the lapsed list. Four confident wrong answers to fix one staleness
    problem, which is why this could not be the one-line swap it looks like.

    The other half is that the flags do not mean what a report reads them as.
    They describe **the month the export was generated for**, and nothing
    recomputes them — so on a deployment whose export has slipped a month,
    "billed this month" is a true statement about a month that has passed,
    printed under a heading that says otherwise, with `export_state()` knowing
    it and no report on this page asking.

    `knack_data.ran_in_month()` answers the same question from the dates and
    the status, which live rows do carry, against the calendar rather than
    against whenever somebody last exported. On this deployment's own export
    the two agree exactly — 373 of 373 rows for this month and 510 of 510 for
    last — which is the evidence that this changes no number today, and the
    reason it is worth doing before it does.
    """
    today = _dt.date.today()
    tstart, tend = _month_bounds(today.strftime("%Y%m"))
    lstart, lend = _month_bounds(_prev_ym(today.strftime("%Y%m")))
    groups: dict[str, dict] = {}
    for r in _products():
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
        if _active_in_month(r, tstart, tend):
            g["thisM"] = True
            g["this_total"] += m
        if _active_in_month(r, lstart, lend):
            g["lastM"] = True
            g["last_total"] += m
        if knack_data.is_running(r):
            g["live"].append(r)
            g["live_total"] += m
            if isinstance(r.get("dash"), str) and r["dash"].startswith("http"):
                g["has_dash"] = True
        end = _parse_date(r.get("end"))
        if end and (g["last_end"] is None or end > g["last_end"]):
            g["last_end"] = end
    return groups


def _unmeasured(columns: list, note: str) -> dict:
    """A report that could not read the products it is built from.

    `measured: False` is what keeps it out of the day's cache and what
    `qa_report.html` draws as "Not measured" rather than as a green tick --
    the rule the whole page works to, applied to the source these reports
    share. Without it, an export that could not be read rendered as "every
    client has a dashboard, nobody has lapsed, nobody is missing Analytics,
    nobody churned", stored until tomorrow.
    """
    return {"columns": columns, "rows": [], "row_styles": [],
            "measured": False, "note": note}


def _is_active(g: dict) -> bool:
    return bool(g["live"]) or g["thisM"]


def client_groups() -> dict:
    """The products on file, grouped by client. The public form of the above.

    `hub/client_health.py` needs exactly what every report on this page needs
    — the live lines, the partner, the rep, whether a dashboard is on file and
    when the last flight ends — and a second copy of that grouping would be
    the drift `hub/storage.py` exists to stop, one table along. Kept a thin
    alias rather than a rename: every report here reads `_client_groups()` and
    renaming it would touch a dozen call sites to make one import look tidy.
    """
    return _client_groups()


def is_active(g: dict) -> bool:
    """Whether a group is a client we are working for now. Public form."""
    return _is_active(g)


def _join(vals) -> str:
    """Join partner names, folding case-only duplicates together.

    Knack holds "MOTO" and "Moto" as separate partner values for the same
    company, so reports grouped by partner showed them as two rows with the
    revenue split between them. Case is not a meaningful distinction here.
    The first spelling encountered wins, so the display stays stable rather
    than flipping between runs.
    """
    seen, out = {}, []
    for v in vals:
        v = str(v or "").strip()
        if not v:
            continue
        k = v.lower()
        if k in seen:
            continue
        seen[k] = v
        out.append(v)
    return ", ".join(sorted(out, key=str.lower)) or "—"


def _norm_name(s: str) -> str:
    """Normalize a business name for QB<->Knack matching.

    Delegates to hub/client_key so this report, the Sites matcher and the
    client crosswalk all agree on when two names are one company. One
    behaviour change came with it: "group" is no longer dropped as a filler
    word. Dropping it merged "Riverside Group" into "Riverside", which are
    routinely two different accounts — a drop list may only remove words that
    cannot tell two businesses apart.
    """
    from hub.client_key import normalise_name
    return normalise_name(s)


def _match_cell(row: dict):
    """How a Suite sub-account was matched to a Knack client, in the table.

    A match and a guess used to look identical here — both rendered "Yes" —
    which is how a mis-attributed sub-account survived every reading of this
    report. Now the evidence is in the cell.
    """
    if row.get("knack_name"):
        if row.get("match_confidence") == "probable":
            return "Probable — check"
        return "Yes" if row.get("match_on") != "domain" else "Yes (domain)"
    cands = row.get("match_candidates") or []
    if cands:
        return "Ambiguous: " + ", ".join(cands[:3]) + ("…" if len(cands) > 3 else "")
    return "No match in Knack"


# ------------------------------------------------- dashboard skip list
#
# Some clients genuinely don't need a dashboard — a one-off creative job, a
# partner who reports themselves. Without somewhere to record that, the same
# names sit on the report forever and people stop reading it.

# Both files below are human decisions with no upstream: who was excused from
# a report and why, and which partner owns an invoiced-off customer. Nothing
# can recompute them, so they go through hub.jsonstore to land in the database
# backup rather than only on a disk that is not backed up. Losing them would
# not look like data loss either — the reports would simply start flagging
# names that were settled months ago, and read as a regression in the checks.
def _skip_path() -> str:
    return os.path.join(jsonstore.data_root(), "dashboard_skips.json")


def _load_skips() -> dict:
    data = jsonstore.read_json(_skip_path(), default={})
    return data if isinstance(data, dict) else {}


def _dash_skipped(client: str) -> bool:
    return _norm_client(client) in _load_skips()


def skip_dashboard(client: str, actor: str = "", reason: str = "") -> dict:
    from datetime import datetime, timezone
    data = _load_skips()
    data[_norm_client(client)] = {
        "client": client, "by": actor,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
    }
    jsonstore.write_json(_skip_path(), data)
    # The skip list is what No Dashboards filters on, so the day's stored copy
    # of it is now wrong. Dropped here rather than at the route: any caller
    # that skips a client has changed that report, and a second description of
    # when to invalidate is one that drifts.
    forget("no-dashboards", "active-clients")
    return {"ok": True, "skipped": len(data)}


def unskip_dashboard(client: str) -> dict:
    data = _load_skips()
    data.pop(_norm_client(client), None)
    jsonstore.write_json(_skip_path(), data)
    forget("no-dashboards", "active-clients")
    return {"ok": True, "skipped": len(data)}


def skipped_dashboards() -> dict:
    """The skip list, so a decision made months ago is reviewable."""
    rows = []
    for rec in sorted(_load_skips().values(), key=lambda r: r.get("client", "")):
        rows.append([
            _c360_link(rec.get("client", "")),
            rec.get("by") or "—",
            (rec.get("at") or "")[:10],
            rec.get("reason") or "—",
            {"actions": [{"label": "Un-skip", "action": "unskip-dashboard",
                          "client": rec.get("client", "")}]},
        ])
    return {"columns": ["Client", "Skipped by", "When", "Reason", ""],
            "rows": rows,
            "note": (f"{len(rows)} client(s) deliberately excluded from the "
                     f"No Dashboards report."
                     if rows else "Nothing skipped.")}


def _norm_client(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


# ------------------------------------------------------------------ reports
from hub import dates as _dates


def _end_bucket(end) -> str:
    """This month / next month / other, from the product end date.

    A renewal conversation is driven by when something stops, so the report is
    organised the way the work is: what ends now, what ends next, everything
    else.
    """
    import datetime as _dt
    if not end:
        return "Other"
    if isinstance(end, str):
        try:
            end = _dt.date.fromisoformat(end[:10])
        except ValueError:
            return "Other"
    today = _dt.date.today()
    if end.year == today.year and end.month == today.month:
        return "Ending this month"
    nxt_y, nxt_m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    if end.year == nxt_y and end.month == nxt_m:
        return "Ending next month"
    return "Other"


def active_clients() -> dict:
    _why = knack_data.products_error()
    if _why:
        return _unmeasured(["Client", "Partner", "Live products", "Live monthly",
         "Ends", "Dashboard"], _why)
    groups = _client_groups()
    buckets = {"Ending this month": [], "Ending next month": [], "Other": []}
    skipped_empty = 0

    for name in groups:
        g = groups[name]
        if not _is_active(g):
            continue
        # `thisM` is a boolean, so a client flagged as billed this month at
        # $0.00 with no live product still passed _is_active — which is why
        # rows showing "0 products, $0" were appearing. A client with neither
        # a live product nor actual billing is not an active client.
        if not g["live"] and not (g.get("this_total") or 0):
            skipped_empty += 1
            continue

        # A client with nothing live cannot be "ending" — whatever they had has
        # already stopped. Filing them under Ending this month put rows reading
        # "0 products" next to renewals that genuinely need a call, which is
        # the fastest way to make a queue stop being trusted. They still appear,
        # under Other, because they are billing and that is worth seeing.
        bucket = _end_bucket(g.get("last_end")) if g["live"] else "Other"

        partner = _join(g["partners"])
        buckets[bucket].append({
            "partner": (partner or "").lower(),
            "ends": g.get("last_end"),
            "name": name.lower(),
            # With nothing running, the last end date is not when this client
            # ends — it is when they stopped. Printing it bare under "Ends"
            # reads as something upcoming, next to a live monthly of $0. Say
            # which it is, and say what they actually billed, because that
            # billing is the only reason the row is here at all.
            "row": [
                _c360_link(name),
                partner,
                len(g["live"]) if g["live"] else {
                    "text": "none running", "muted": True},
                _money(g["live_total"]) if g["live"] else {
                    "text": _money(g.get("this_total") or 0) + " billed this month",
                    "muted": True},
                (_dates.fmt(g.get("last_end")) if g["live"]
                 else {"text": "ended " + _dates.fmt(g.get("last_end")), "muted": True}),
                "Yes" if g["has_dash"] else "No",
            ],
        })

    # Ending buckets group the work by who we call about it. Other is a
    # watch-list, so it leads with whatever expires soonest — sorted on the
    # real date, not the formatted string, or 01-05-27 would sort above
    # 12-31-26.
    for label in ("Ending this month", "Ending next month"):
        buckets[label].sort(key=lambda r: (r["partner"], _dates.sort_key(r["ends"]), r["name"]))
    # "Next to expire" means the next one, not the oldest one. A plain
    # ascending sort put July dates at the top of a list read in August —
    # those have already ended, so they are not what anyone is watching for.
    # Upcoming first, then the already-ended most-recent-first, then unknown.
    import datetime as _d
    _today = _d.date.today()

    def _other_key(r):
        d = _dates.to_date(r["ends"])
        if d is None:
            return (2, _d.date.max, r["partner"], r["name"])
        if d >= _today:
            return (0, d, r["partner"], r["name"])
        return (1, _d.date.max - (d - _d.date.min), r["partner"], r["name"])

    buckets["Other"].sort(key=_other_key)

    tones = {"Ending this month": "now", "Ending next month": "soon", "Other": "later"}
    rows = []
    for label in ("Ending this month", "Ending next month", "Other"):
        if not buckets[label]:
            continue
        rows.append([{"text": f"{label} ({len(buckets[label])})",
                      "group": True, "tone": tones[label]}, "", "", "", "", ""])
        rows.extend(r["row"] for r in buckets[label])

    total = sum(len(v) for v in buckets.values())
    return {
        "columns": ["Client", "Partner", "Live products", "Live monthly",
                    "Ends", "Dashboard"],
        "rows": rows,
        "note": (f"{total} active clients. Ending this month and next are "
                 f"grouped by partner; everything else leads with whatever "
                 f"expires soonest."
                 + (f" {skipped_empty} excluded with no live product and no "
                    f"billing." if skipped_empty else "")),
    }


def no_dashboards() -> dict:
    _why = knack_data.products_error()
    if _why:
        return _unmeasured(["Partner", "Client", "Live products",
         "Products", "Monthly"], _why)
    groups = _client_groups()
    by_partner: dict[str, list] = {}
    total = 0
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _is_active(g) or g["has_dash"]:
            continue
        # At least one LIVE product. A client billed this month with nothing
        # running doesn't need a dashboard — there's nothing to report on, so
        # chasing one is busywork that makes the list look longer than the
        # actual job.
        if not g["live"]:
            continue
        if _dash_skipped(name):
            continue
        total += 1
        prods = sorted({str(r.get("product") or "") for r in g["live"]} or
                       {str(r.get("product") or "") for r in g["rows"]
                        if _billing_this_month(r)})
        partner = _join(g["partners"])
        by_partner.setdefault(partner, []).append([
            partner if partner != "—" else "(no partner)",
            _c360_link(name),
            len(g["live"]),
            ", ".join(p for p in prods if p)[:120] or "—",
            _money(g["live_total"] or g["this_total"]),
            {"actions": [
                {"label": "Add dashboard", "action": "add-dashboard",
                 "client": name},
                {"label": "Skip", "action": "skip-dashboard", "client": name,
                 "confirm": f"Skip {name}? It leaves this list until you "
                            f"un-skip it at the bottom of the page."},
            ]},
        ])
    rows, styles = [], []
    # Fold any remaining case-only duplicate GROUP keys into one bucket, so
    # "MOTO" and "Moto" don't render as two partners with split subtotals.
    folded = {}
    for k, v in list(by_partner.items()):
        canon = next((c for c in folded if c.lower() == k.lower()), k)
        folded.setdefault(canon, []).extend(v)
    by_partner = folded
    keys = sorted([k for k in by_partner if k != "—"], key=str.lower) + \
        (["—"] if "—" in by_partner else [])
    for k in keys:
        for r in by_partner[k]:
            rows.append(r)
            styles.append(None)
    return {
        # Six cells, so six headings. The action cell is headed `""` the way
        # `skipped_dashboards()` heads its own two functions up: the renderer
        # draws one <td> per cell against one <th> per column, so a row
        # carrying a cell no column names puts the Add-dashboard button under
        # a heading belonging to the value on its left — and the CSV export,
        # which writes `columns` as its header row and the cells beneath it,
        # gave every row an unlabelled trailing field.
        "columns": ["Partner", "Client", "Live products",
                    "Products", "Monthly", ""],
        "rows": rows,
        "row_styles": styles,
        "note": (f"{total} active clients with no Smart 1 Dashboard link on any "
                 "live product — broken down by partner (clients with no partner "
                 "listed at the end)."),
    }


def stale_90() -> dict:
    _why = knack_data.products_error()
    if _why:
        return _unmeasured(["Partner", "Client", "Salesperson", "Last product ended",
         "Days since", "Last monthly"], _why)
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
        # 90 days quiet is the flag; 180 is the ceiling. Beyond six months
        # they aren't a lapsed client to chase, they're a former one, and
        # mixing the two makes the list too long to work through.
        if days < 90 or days > 180:
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
            _dates.fmt(g["last_end"]),
            days,
            _money(last_total),
        ]))
    rows, styles = [], []
    # Fold any remaining case-only duplicate GROUP keys into one bucket, so
    # "MOTO" and "Moto" don't render as two partners with split subtotals.
    folded = {}
    for k, v in list(by_partner.items()):
        canon = next((c for c in folded if c.lower() == k.lower()), k)
        folded.setdefault(canon, []).extend(v)
    by_partner = folded
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
        # "up to 24 months back" was never true: the loop above stops at 180
        # days, deliberately and with its reason written beside it. So the one
        # sentence on the page said two years while the table held six months,
        # and a rep working the win-back list believed the 168 clients between
        # six months and two years had been checked and were not lapsed.
        "note": (f"{total} clients with no live product whose last IO ended "
                 f"between 90 and 180 days ago — {_money(grand)}/mo of lapsed "
                 "billing, grouped by partner with subtotals; clients without a "
                 "partner listed at the end. Anything quiet for longer than six "
                 "months is a former client rather than one to chase, and is "
                 "deliberately left off."),
    }


def lost_by_partner() -> dict:
    _why = knack_data.products_error()
    if _why:
        return _unmeasured(["Partner", "Client", "Salesperson", "Billing last month"], _why)
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


from hub.knack_data import ran_in_month as _knack_ran_in_month


def _active_in_month(r: dict, mstart, mend) -> bool:
    """Did this IO deliver during the month? `knack_data.ran_in_month()`.

    It used to be `status in ("live", "complete")` plus a date overlap, written
    here rather than beside `is_running()` — and it is the narrow test that
    function's own docstring says "missed about a third of the work actually
    running". So the two Scorecards counted Live and Complete while every other
    report on this page counted the union: 147 rows and $140,439 a month hidden
    from August, and two salespeople with live work — Debi Greenfield and Kim
    Marshall — absent from the Scorecard entirely, each of whom appears on
    Active Clients three rows up. Nothing said the two were measured
    differently, which is what makes it the `/api/db/structure` versus
    `/api/integrity` trap rather than a disagreement somebody could notice.

    The rule is a neighbour of `is_running()` now rather than a second reading
    of it, and the reasons the two must still differ — Complete is a pass here,
    Live does not override the dates, a row with no dates is in no month — are
    written there, once, where both can be read together.
    """
    return _knack_ran_in_month(r, mstart, mend)


def _month_rollup(field: str, ym: str) -> dict:
    """{who: {"clients": {client: budget}, "products": n, "revenue": x}}"""
    mstart, mend = _month_bounds(ym)
    by: dict[str, dict] = {}
    for r in _products():
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
    # `_month_rollup()` groups `_products()` directly rather than
    # through `_client_groups()`, so it needs the same guard: an export that
    # could not be read makes every salesperson and every partner disappear,
    # which renders as a scorecard nobody is on rather than as a source that
    # would not answer.
    why = knack_data.products_error()
    if why:
        return _unmeasured(
            [("Salesperson" if field == "sales" else "Partner"),
             "Active clients", "Active products", "Monthly revenue"], why)
    months = _last_12_months()
    ym = month if month in {m["ym"] for m in months} else months[0]["ym"]
    cur = _month_rollup(field, ym)
    prev = _month_rollup(field, _prev_ym(ym))
    totals = {"clients": 0, "prev_clients": 0, "products": 0,
              "prev_products": 0, "revenue": 0.0}

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
        # New/Lost/Increased/Decreased were four columns saying what two
        # numbers already imply. The change now sits beside the number it
        # describes, which is where you read it.
        rows.append([
            who,
            {"text": len(s["clients"]), "delta": len(s["clients"]) - len(p["clients"]),
             "title": f"{new} new, {lost} lost vs last month"},
            {"text": s["products"], "delta": s["products"] - p.get("products", 0),
             "title": f"{up} increased, {down} decreased vs last month"},
            _money(s["revenue"]),
        ])
        diff = s["revenue"] - p["revenue"]
        styles.append("green" if diff > 0.5 else ("red" if diff < -0.5 else "yellow"))
        totals["clients"] += len(s["clients"])
        totals["prev_clients"] += len(p["clients"])
        totals["products"] += s["products"]
        totals["prev_products"] += p.get("products", 0)
        totals["revenue"] += s["revenue"]

    label = "salespeople" if field == "sales" else "partners"
    mlabel = next(m["label"] for m in months if m["ym"] == ym)
    return {
        "columns": [("Salesperson" if field == "sales" else "Partner"),
                    "Active clients", "Active products", "Monthly revenue"],
        "rows": rows + ([[
            {"text": "TOTAL", "group": True},
            {"text": totals["clients"],
             "delta": totals["clients"] - totals["prev_clients"]},
            {"text": totals["products"],
             "delta": totals["products"] - totals["prev_products"]},
            _money(totals["revenue"]),
        ]] if rows else []),
        # One style per row including the totals row, or the colouring shifts
        # by one and every partner shows the row above's verdict.
        "row_styles": styles + ([None] if rows else []),
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


def _active_within(g: dict, days: int = 60) -> bool:
    """Has this client had a product running in the last `days`?

    Analytics and GTM only matter for a site we're currently driving traffic
    to. A client who stopped four months ago will show as missing forever, and
    every one of those makes the report less likely to be read.
    """
    if g["live"] or g["thisM"]:
        return True
    end = g.get("last_end")
    if not end:
        return False
    return (_dt.date.today() - end).days <= days


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


def prospect_queue() -> dict:
    """Which prospect to call today, and why.

    Every other report on this page is about a client. This one is about
    somebody who is not one yet — the record `hub/prospect.py` built is worth
    opening, and nothing said which to open.
    """
    from . import prospect_queue as pq
    return pq.build()


def sell_to_clients() -> dict:
    """What each active client's own website says we could sell them.

    Deliberately *not* merged into the two reports below it. Those read what
    we have on file — a property attached, a container linked — and this reads
    what is on the page. A client can have both and still be missing the tag,
    and that gap is the finding rather than something to reconcile away.
    """
    from . import upsell
    return upsell.build()


def no_analytics() -> dict:
    _why = knack_data.products_error()
    if _why:
        return _unmeasured(["Client", "Partner", "Salesperson", "Website",
         "Monthly", "Analytics account"], _why)
    groups = _client_groups()
    rows, styles = [], []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not _active_within(g, 60):
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


def _gtm_from_scan(domain: str) -> str:
    """A GTM container the site scan actually saw on the page.

    Our records can be wrong or simply blank while the tag is live. Reporting
    a client as missing GTM when the scan found one on their homepage sends
    someone to install a second container — which then double-counts every
    event. The page is the authority here, not the record.
    """
    if not domain:
        return ""
    try:
        from modules.scans.app import latest_payload_for_domain
        payload = latest_payload_for_domain(domain) or {}
    except Exception:                                   # noqa: BLE001
        return ""
    for ns in ("google_tag_manager", "tag_manager", "analytics"):
        sec = payload.get(ns)
        if not isinstance(sec, dict):
            continue
        for key in ("container_id", "gtm_id", "gtm_container", "id"):
            val = str(sec.get(key) or "").strip()
            if val.upper().startswith("GTM-"):
                return val
    blob = json.dumps(payload)[:400000]
    m = re.search(r"GTM-[A-Z0-9]{4,10}", blob)
    return m.group(0) if m else ""


def no_gtm() -> dict:
    _why = knack_data.products_error()
    if _why:
        return _unmeasured(["Client", "Partner", "Live products", "Monthly", "GTM container"], _why)
    groups = _client_groups()
    priority, suggested = [], []
    found_on_site = 0
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        # Only clients running something in the last 60 days. A tag on a site
        # we aren't driving traffic to isn't work worth chasing.
        if not _active_within(g, 60):
            continue
        cov = _google_coverage(name, g)
        if cov["has_gtm"]:
            continue
        scan_gtm = _gtm_from_scan(cov.get("domain") or "")
        if scan_gtm:
            # It IS installed — our record just doesn't know. Show it rather
            # than listing them as missing.
            found_on_site += 1
            cov["scan_gtm"] = scan_gtm
        active_products = {str(r.get("product") or "").lower() for r in g["rows"]
                           if knack_data.is_running(r) or _billing_this_month(r)}
        is_priority = any(any(k in p for k in GTM_PRIORITY_KEYWORDS) for p in active_products)
        row = [
            _c360_link(name),
            _join(g["partners"]),
            ", ".join(sorted({str(r.get("product") or "") for r in g["live"]}))[:100] or "—",
            _money(g["live_total"] or g["this_total"]),
            ({"pill": "ok", "text": f"GTM Found · {cov['scan_gtm']}",
              "title": "The site scan saw this container on the page — our "
                       "record just doesn't have it. Copy it onto the website "
                       "record rather than installing a second one."}
             if cov.get("scan_gtm") else
             {"search_attach": name, "kind": "gtm", "q": cov["domain"] or name}),
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
    # Smart 1 Marketing lookups use their own sub-account token when provided,
    # so the precedence here is deliberately the reverse of hub/config.py's:
    # SMART1SUITE_PRIVATE_TOKEN first, then the agency token as the fallback.
    # config treats the two as one setting and prefers GHL_PRIVATE_TOKEN, which
    # is right for the Hub's one write path and wrong here — these accounting
    # reads are scoped to the sub-account, and an agency token silently returns
    # the agency's own empty list rather than refusing.
    #
    # Both names are read rather than one, which is what /api/integrity's
    # drift check asks of anything not going through config: a reader that
    # knows fewer spellings than config does is the defect. Order is the only
    # difference.
    from hub.config import settings as _cfg
    token = (os.environ.get("SMART1SUITE_PRIVATE_TOKEN", "").strip()
             or os.environ.get("GHL_PRIVATE_TOKEN", "").strip()
             or _cfg.ghl_token)
    if not token:
        raise RuntimeError(f"{_cfg.spellings('ghl_token')} is not configured.")
    headers = {"Authorization": f"Bearer {token}",
               "Version": os.environ.get("GHL_API_VERSION", "2021-07-28"),
               "Accept": "application/json", "Content-Type": "application/json"}
    r = _rq.request(method, GHL_BASE + path, params=params, json=body,
                    headers=headers, timeout=20)
    if not r.ok:
        raise RuntimeError(f"GHL {method} {path} failed (HTTP {r.status_code}): {r.text[:180]}")
    return r.json() if r.text else {}


def _accounting_location() -> tuple[str, str]:
    """(location_id, name) of the Smart 1 Marketing sub-account.

    GHL_ACCOUNTING_LOCATION_ID / GHL_LEAD_LOCATION_ID pin it; otherwise it is
    found by name.

    This used to accept SUITE_COMPANY_ID as the override, which is wrong twice
    over: the name says company and hub/config.py reads it as one, and in this
    deployment it holds the same value as GHL_COMPANY_ID. A companyId passed
    where the API wants a locationId addresses the agency rather than the
    sub-account, and nothing in the response says so — the pipeline lookup just
    comes back empty as though there were no pipelines. A company id is
    therefore refused here rather than used.
    """
    # GHL_ACCOUNTING_LOCATION_ID, GHL_LEAD_LOCATION_ID and
    # SMART1_MARKETING_LOCATION_ID are one setting, and a reader that knows two
    # of the three pins the location on one deployment and falls through to the
    # search on the next for no reason anybody can see. So config supplies the
    # names — but the *order* is this file's, and deliberately not config's:
    # the accounting spelling wins here, because this figure is what an invoice
    # is reconciled against and a deployment that has pinned an accounting
    # location has said which sub-account that is. Config prefers the lead
    # location, which is right where a lead is being written and wrong here.
    # Every name in the group is read either way, which is what the drift check
    # in /api/integrity asks of anything not resolved through config outright.
    from hub.config import settings as _cfg
    company = _cfg.ghl_company_id.strip()
    override = (os.environ.get("GHL_ACCOUNTING_LOCATION_ID", "").strip()
                or os.environ.get("GHL_LEAD_LOCATION_ID", "").strip()
                or os.environ.get("SMART1_MARKETING_LOCATION_ID", "").strip())
    if override and company and override == company:
        raise RuntimeError(
            "The configured accounting location id is the same value as the "
            "agency company id. That addresses the agency, not the Smart 1 "
            "Marketing sub-account. Set GHL_ACCOUNTING_LOCATION_ID to the "
            "sub-account (location) id.")
    if override:
        return override, "Smart 1 Marketing"
    if "acct_loc" in _ghl_cache:
        return _ghl_cache["acct_loc"]
    data = _ghl("/locations/search", {
        "companyId": _cfg.ghl_company_id, "limit": "500"})
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


def _ghl_custom_value(o: dict, *needles) -> str:
    """Pull a custom-field value off an opportunity (or its contact) whose
    id / key / name contains one of the needles."""
    sources = [o.get("customFields"), (o.get("contact") or {}).get("customFields"),
               o.get("customField")]
    for needle in needles:
        for src in sources:
            if not isinstance(src, list):
                continue
            for f in src:
                if not isinstance(f, dict):
                    continue
                key = " ".join(str(f.get(k) or "") for k in
                               ("id", "key", "fieldKey", "name", "fieldName")).lower()
                if needle in key:
                    v = (f.get("fieldValue") if f.get("fieldValue") is not None
                         else f.get("value") if f.get("value") is not None
                         else f.get("field_value"))
                    if isinstance(v, list):
                        return ", ".join(str(x) for x in v)
                    if isinstance(v, dict):
                        return ", ".join(str(x) for x in v.values())
                    if v is not None and str(v).strip():
                        return str(v)
    return ""


def _mmddyy(iso: str) -> str:
    s = str(iso or "")[:10]
    try:
        return _dates.fmt(s)
    except ValueError:
        return s


GHL_STATUSES = ("open", "won", "lost", "abandoned")


def accounting_requests() -> dict:
    columns = ["Request", "Company", "Detail", "Created", "Status", "Stage"]
    try:
        loc_id, loc_name = _accounting_location()
        pipe = _accounting_pipeline(loc_id)
    except RuntimeError as exc:
        # "error", not "note": a failed API call must never render as the
        # green "Nothing to report — all clear" empty state. An audit that
        # cannot reach its data source has found nothing BECAUSE it failed,
        # which is the opposite of a clean bill of health.
        return {"columns": columns, "rows": [], "error": str(exc)}
    stages = [{"id": s.get("id"), "name": s.get("name")}
              for s in (pipe.get("stages") or [])]
    stage_names = {s["id"]: s["name"] for s in stages}
    try:
        data = _ghl("/opportunities/search", {
            "location_id": loc_id, "pipeline_id": pipe.get("id"),
            "limit": 100})
    except RuntimeError as exc:
        # "error", not "note": a failed API call must never render as the
        # green "Nothing to report — all clear" empty state. An audit that
        # cannot reach its data source has found nothing BECAUSE it failed,
        # which is the opposite of a clean bill of health.
        return {"columns": columns, "rows": [], "error": str(exc)}
    opps = data.get("opportunities") or []
    rows = []
    for o in opps:
        contact = (o.get("contact") or {})
        organization = (_ghl_custom_value(o, "organization")
                        or contact.get("companyName") or o.get("name") or "(unnamed)")
        # The issue was read from one hardcoded custom-field id, which returns
        # nothing when the field is renamed or a different form is used — so
        # every row showed "—". Try the id first, then the field's own name,
        # so a rename doesn't silently empty the column.
        issue = (_ghl_custom_value(o, "29zlj", "checkbox")
                 or _ghl_custom_value(o, "issue", "reason", "request type",
                                      "what do you need", "problem")
                 or "")
        # The issue IS the request — "Buckeye Lake Winery" tells you who asked,
        # not what for, and a list of client names is not a work queue.
        request = issue.strip() or (o.get("name") or "").strip() or "(no issue given)"
        rows.append([
            {"text": request,
             "href": f"{os.environ.get('GHL_APP_BASE', 'https://app.gohighlevel.com')}"
                     f"/v2/location/{loc_id}/opportunities/list"},
            organization,
            # The request line is a one-liner; the form behind it holds the
            # detail. Rather than widening every row for the few people who
            # need it, put it behind a button.
            {"actions": [{"label": "Summary", "action": "form-summary",
                          "client": str(o.get("id") or "")}]},
            _mmddyy(o.get("createdAt")),
            {"status_select": o.get("id"),
             "current": str(o.get("status") or "open").lower()},
            {"stage_select": o.get("id"),
             "current": o.get("pipelineStageId"),
             "current_name": stage_names.get(o.get("pipelineStageId"), "")},
        ])
    return {
        "columns": columns,
        "rows": rows,
        "stages": stages,
        "statuses": list(GHL_STATUSES),
        "pipeline_id": pipe.get("id"),
        "note": (f"{len(rows)} requests in the \"{pipe.get('name')}\" pipeline "
                 f"({loc_name}). Status and stage are both editable right here — "
                 "changes update GHL immediately."),
    }


def set_accounting_stage(opp_id: str, stage_id: str = "", status: str = "") -> None:
    loc_id, _ = _accounting_location()
    pipe = _accounting_pipeline(loc_id)
    body = {}
    if stage_id:
        body = {"pipelineId": pipe.get("id"), "pipelineStageId": stage_id}
    elif status:
        body = {"status": status}
    try:
        _ghl(f"/opportunities/{opp_id}/status", method="PUT", body=body)
    except RuntimeError:
        _ghl(f"/opportunities/{opp_id}", method="PUT", body=body)
    # The row has just moved stage. Dropped here rather than at the route, for
    # the reason `skip_dashboard()` gives: one description of what a write
    # invalidates, beside the write.
    forget("accounting-requests")


# ------------------------------------------- GHL: Smart 1 Suite SaaS billing
#
# Agency-level "SaaS Configurator" endpoints — distinct from the /opportunities
# calls above. Needs GHL_PRIVATE_TOKEN to be an Agency-level Private
# Integration Token with the SaaS Configurator (Agency-Access) scope enabled,
# plus GHL_COMPANY_ID. Reuses the same two env vars the Suite control panel
# already requires for /locations/search — no new config if that already
# works. Source: GoHighLevel's public OpenAPI spec (saas-v3.json / Agency-
# Access security), current as of Aug 2026. NOT YET RUN AGAINST LIVE DATA —
# the raw shapes below (subscriptionInfo, plan prices) are documented but the
# actual account has never called them from this app. Verify the first live
# run: open System Status, or run one report and read the note if it errors.
GHL_SAAS_VERSION = "v3"


def _ghl_saas(path: str, params=None):
    import requests as _rq
    from hub.config import settings as _cfg
    token = _cfg.ghl_token
    if not token:
        raise RuntimeError(f"{_cfg.spellings('ghl_token')} is not configured.")
    headers = {"Authorization": f"Bearer {token}", "Version": GHL_SAAS_VERSION,
               "Accept": "application/json"}
    r = _rq.get(GHL_BASE + path, params=params, headers=headers, timeout=20)
    if not r.ok:
        raise RuntimeError(
            f"GHL SaaS {path} failed (HTTP {r.status_code}): {r.text[:200]}. "
            "If this is a 401/403, the Private Integration Token most likely "
            "needs the SaaS Configurator scope added — it's a separate scope "
            "from the one that powers Locations/Opportunities.")
    return r.json() if r.text else {}


def _ghl_saas_locations() -> list:
    """Every sub-account GHL has ever put into Smart 1 Suite's SaaS mode,
    each with its subscriptionInfo (status, plan, Stripe ids), paginated."""
    from hub.config import settings as _cfg
    company = _cfg.ghl_company_id
    if not company:
        raise RuntimeError(f"{_cfg.spellings('ghl_company_id')} is not configured.")
    out, page = [], 1
    while True:
        data = _ghl_saas(f"/saas/saas-locations/{company}", {"page": page})
        locs = data.get("locations") if isinstance(data, dict) else data
        locs = locs or []
        out.extend(locs)
        pg = (data.get("pagination") or {}) if isinstance(data, dict) else {}
        if not locs or not pg.get("hasNext"):
            break
        page += 1
        if page > 50:            # sane ceiling — a real runaway would mean a bug
            break
    return out


def _ghl_agency_plans() -> dict:
    """{saasPlanId: plan dict} — turns a location's saasPlanId into a plan
    title and its active monthly price."""
    from hub.config import settings as _cfg
    company = _cfg.ghl_company_id
    data = _ghl_saas(f"/saas/agency-plans/{company}")
    plans = data if isinstance(data, list) else (data.get("plans") or [])
    return {p.get("planId"): p for p in plans if isinstance(p, dict) and p.get("planId")}


def _plan_monthly_price(plan: dict) -> float:
    for pr in (plan or {}).get("prices") or []:
        if pr.get("billingInterval") == "month" and pr.get("active", True):
            return _num(pr.get("amount"))
    return 0.0


# Subscription statuses that count as "billing" for these two reports.
# GHL/Stripe statuses seen in the wild: active, trialing, past_due, paused,
# canceled, incomplete, incomplete_expired, unpaid. "past_due" is included —
# they're still an active subscription that hasn't failed yet; "paused" and
# "canceled" are not.
_ACTIVE_SUB_STATUSES = {"active", "trialing", "past_due"}


def _ghl_billing_rows() -> list:
    """One row per GHL sub-account that has ever been in SaaS mode, resolved
    to a plan name/price and matched to its Smart 1 client record in Knack
    (by normalized business name — same fuzzy match used in invoice_off())."""
    locations = _ghl_saas_locations()
    try:
        plans = _ghl_agency_plans()
    except RuntimeError:
        plans = {}

    from hub import client_key as ck

    groups = _client_groups()
    index = ck.alias_index()

    # Knack client names, grouped by the shared client key rather than by a
    # normalised string of their own. Two Knack rows that resolve to one key
    # are one client, which is the whole point of the key existing.
    knack_by_key = {}
    knack_by_norm = {}
    for name, g in groups.items():
        knack_by_key.setdefault(ck.resolve(name=name, index=index)["key"], (name, g))
        n = _norm_name(name)
        if n:
            knack_by_norm.setdefault(n, (name, g))

    rows = []
    for loc in locations:
        info = loc.get("subscriptionInfo") or {}
        status = str(info.get("subscriptionStatus")
                     or loc.get("subscriptionStatus") or "").strip().lower()
        plan_id = info.get("saasPlanId") or loc.get("saasPlanId")
        plan = plans.get(plan_id) or {}
        raw_name = str(loc.get("name") or loc.get("locationId") or "").strip()
        website = str(loc.get("website") or loc.get("domain") or "").strip()

        # This is the line that used to invent false alarms. The old fallback
        # took the *first* Knack name that contained the sub-account name as a
        # substring, so a sub-account called "Acme" was attributed to whichever
        # of Acme Plumbing, Acme Roofing and Acme Electric came out of the dict
        # first — a different one on a different day, and no way to tell from
        # the report that a guess had been made at all. resolve() matches on
        # domain, then on an exact normalised name, and only offers a near
        # match when exactly one client can possibly be meant.
        hit_info = ck.resolve(name=raw_name, url=website,
                              allow_fuzzy=True, index=index)
        hit = knack_by_key.get(hit_info["key"]) if hit_info["known"] else None
        if hit is None and hit_info["known"]:
            hit = knack_by_norm.get(_norm_name(hit_info["client"]))
        rows.append({
            "location_id": loc.get("locationId"),
            "raw_name": raw_name or "(unnamed sub-account)",
            "status": status,
            "plan_title": plan.get("title") or plan_id or "—",
            "monthly": _plan_monthly_price(plan),
            "knack_name": hit[0] if hit else None,
            "knack_group": hit[1] if hit else None,
            "match_confidence": hit_info["confidence"] if hit else "unmatched",
            "match_on": hit_info["matched_on"] if hit else "",
            # Populated when several clients could be meant. Showing them is
            # what turns "no match" from a dead end into something a person
            # can resolve in ten seconds.
            "match_candidates": hit_info.get("candidates") or [],
        })
    return rows


def ghl_billing_no_products() -> dict:
    """Report: Smart 1 Suite sub-accounts with active billing but nothing
    live on the marketing side — a pure-software client, or a mismatch
    between what GHL is charging and what Knack has on file."""
    columns = ["Client", "GHL sub-account", "Plan", "Monthly", "Billing status",
               "Matched in Knack"]

    # The Knack side of the join. An unreadable export makes every
    # sub-account look like one with no live product, which is this
    # report's own finding -- so it would invent them rather than
    # merely miss them.
    why = knack_data.products_error()
    if why:
        return _unmeasured(columns, why)
    try:
        rows_raw = _ghl_billing_rows()
    except RuntimeError as exc:
        # "error", not "note": a failed API call must never render as the
        # green "Nothing to report — all clear" empty state. An audit that
        # cannot reach its data source has found nothing BECAUSE it failed,
        # which is the opposite of a clean bill of health.
        return {"columns": columns, "rows": [], "error": str(exc)}
    rows = []
    total = 0.0
    ambiguous = 0
    for r in rows_raw:
        if r["status"] not in _ACTIVE_SUB_STATUSES:
            continue
        if r["knack_group"] and _is_active(r["knack_group"]):
            continue          # has a live Smart 1 marketing product — not this report
        client_cell = _c360_link(r["knack_name"]) if r["knack_name"] else r["raw_name"]
        rows.append((str(r["raw_name"]).lower(), [
            client_cell, r["raw_name"], r["plan_title"], _money(r["monthly"]),
            r["status"].replace("_", " ").title(),
            _match_cell(r),
        ]))
        total += r["monthly"]
        if r.get("match_candidates"):
            ambiguous += 1
    rows.sort(key=lambda t: t[0])
    note = (f"{len(rows)} Smart 1 Suite sub-accounts billing (active, trialing "
            f"or past-due) with no live Smart 1 marketing product on file in "
            f"Knack — {_money(total)}/mo of Suite-only billing. \"No match in "
            "Knack\" means the sub-account name couldn't be matched to any "
            "Smart 1 client record at all, so double-check those by hand.")
    if ambiguous:
        note += (f" {ambiguous} could be more than one client and are shown "
                 "unmatched with the candidates listed rather than guessed at "
                 "— a wrong match is what makes this report disagree with the "
                 "invoices.")
    return {"columns": columns, "rows": [r for _, r in rows], "note": note}


def ghl_billing_this_month() -> dict:
    """Report: every Smart 1 Suite sub-account with active billing right now,
    simplified to client / plan / monthly price / status — no Stripe or
    customer IDs, biggest bill first."""
    columns = ["Client", "GHL sub-account", "Plan", "Monthly", "Billing status"]

    # The Knack side of the join. An unreadable export makes every
    # sub-account look like one with no live product, which is this
    # report's own finding -- so it would invent them rather than
    # merely miss them.
    why = knack_data.products_error()
    if why:
        return _unmeasured(columns, why)
    try:
        rows_raw = _ghl_billing_rows()
    except RuntimeError as exc:
        # "error", not "note": a failed API call must never render as the
        # green "Nothing to report — all clear" empty state. An audit that
        # cannot reach its data source has found nothing BECAUSE it failed,
        # which is the opposite of a clean bill of health.
        return {"columns": columns, "rows": [], "error": str(exc)}
    rows = []
    total = 0.0
    for r in rows_raw:
        if r["status"] not in _ACTIVE_SUB_STATUSES:
            continue
        client_cell = _c360_link(r["knack_name"]) if r["knack_name"] else r["raw_name"]
        rows.append((r["monthly"], [
            client_cell, r["raw_name"], r["plan_title"], _money(r["monthly"]),
            r["status"].replace("_", " ").title(),
        ]))
        total += r["monthly"]
    rows.sort(key=lambda t: -t[0])
    return {
        "columns": columns,
        "rows": [r for _, r in rows],
        "note": (f"{len(rows)} Smart 1 Suite sub-accounts with active, trialing or "
                 f"past-due billing this month — {_money(total)}/mo total. Plan and "
                 "price come from the agency's SaaS Configurator plans."),
    }


# ---------------------------------------- invoice-off partner assignments
def _assign_path() -> str:
    return os.path.join(jsonstore.data_root(), "qa-invoice-partner.json")


def invoice_assignments() -> dict:
    data = jsonstore.read_json(_assign_path(), default={})
    return data if isinstance(data, dict) else {}


def assign_invoice_partner(customer: str, partner: str):
    data = invoice_assignments()
    data[str(customer)] = str(partner)
    jsonstore.write_json(_assign_path(), data, indent=1)
    # The assignment is what takes the row off Invoice Off — "remembered, never
    # shown again" is what the button says. It has to be true on the next open
    # as well as on the row the person is looking at.
    forget("invoice-off")


def partner_list() -> list[str]:
    return sorted({str(r.get("partner", "")).strip()
                   for r in _products() if str(r.get("partner", "")).strip()},
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


def _resembling_clients(norm: str, by_norm: dict) -> list:
    """Client names a normalised string merely resembles — never a match.

    `hub/client_key.py` gives the rule at length: match on an exact normalised
    name or on nothing, and offer a near match only when exactly one client
    can possibly be meant. Here even one is offered rather than used, because
    this report has no confirmation step to settle it with — the same answer
    `sites_billing` and `domain_renewals` arrived at, where a resemblance is
    printed and still counted as unmatched.
    """
    if not norm:
        return []
    return sorted({kn for other, (kn, _g) in by_norm.items()
                   if other and (other in norm or norm in other)})


def _join_names(names, cap: int = 3) -> str:
    """Up to `cap` names, then how many more — a row is not a list."""
    shown = list(names)[:cap]
    rest = len(names) - len(shown)
    out = ", ".join(f"“{n}”" for n in shown)
    return out + (f" and {rest} more" if rest > 0 else "")


def invoice_off() -> dict:
    qb, err = _qb_state()
    columns = ["Customer", "Invoiced this month", "Active products / mo",
               "Difference", "Live products", "Partner"]
    if err:
        return {"columns": columns, "rows": [], "note": err, "needs_qb": True}
    # Half this report is the QuickBooks side and half is the Knack side, and
    # only the second one finds an active client nobody is invoicing. With the
    # export unreadable that half goes silent while the first still answers --
    # a report that looks like it ran and is missing the findings it exists
    # for. Checked after the QuickBooks gate, because "connect QuickBooks" is
    # the more actionable of the two.
    why = knack_data.products_error()
    if why:
        return _unmeasured(columns, why)
    assigned = invoice_assignments()
    data = qb.monthly_totals_by_customer(2)     # this + last month is plenty
    this_key = _month_keys(1)[0]

    groups = _client_groups()
    knack_by_norm: dict[str, tuple[str, dict]] = {}
    for name, g in groups.items():
        n = _norm_name(name)
        if n:
            knack_by_norm.setdefault(n, (name, g))

    rows, styles = [], []
    matched_norms = set()
    resembled = 0
    for cust in sorted(data, key=str.lower):
        if cust in assigned:            # resolved to a partner — never show again
            continue
        rec = data[cust]
        invoiced = rec["months"].get(this_key, 0.0)
        n = _norm_name(cust)
        cust_cell = ({"text": cust, "href": qb.customer_link(rec["id"])}
                     if rec.get("id") else cust)
        hit = knack_by_norm.get(n)
        if not hit:
            # Exact or nothing. This used to fall through to `next(... if norm
            # in n or n in norm)` -- an unbounded substring, both directions,
            # first out of a dict ordered by the export. That is the rule
            # `hub/client_key.py` exists to refuse, and it is live: 32 of the
            # 547 client names here contain or are contained by another, and
            # "cirilla s" alone matches 18. So a QuickBooks customer named
            # "Cirilla's" was compared against whichever of eighteen came
            # first, and the variance printed with no sign a guess had been
            # made.
            near = _resembling_clients(n, knack_by_norm)
            if near:
                # Printed, and still counted as unmatched -- the rule
                # `sites_billing` and `domain_renewals` both work to. The row
                # is visible so somebody can settle it, and carries no
                # difference, because there is no client we can stand behind
                # to compute one against.
                resembled += 1
                rows.append((invoiced, [
                    cust_cell,
                    _money(invoiced),
                    "—",
                    "resembles " + _join_names(near) + " — not matched",
                    "",
                    {"assign": cust},
                ]))
                styles.append("sub")
            continue     # QB customer with no Smart 1 Team client — skip
        kname, g = hit
        matched_norms.add(_norm_name(kname))
        expected = g["live_total"]
        diff = round(invoiced - expected, 2)
        if abs(diff) < 0.5:
            continue
        rows.append((abs(diff), [
            cust_cell,
            _money(invoiced),
            _money(expected),
            ("▼ " if diff < 0 else "▲ ") + _money(abs(diff)),
            len(g["live"]),
            {"assign": cust},
        ]))
        styles.append(None)
    # active Knack clients with NO invoice at all this month
    for name, g in groups.items():
        if not _is_active(g) or g["live_total"] < 0.5:
            continue
        if name in assigned:
            continue
        if _norm_name(name) in matched_norms:
            continue
        n = _norm_name(name)
        # Exact, and this direction is the one that *hid* findings. It used to
        # accept containment either way, so an active client with live billing
        # and no invoice at all dropped off the report the moment any
        # QuickBooks customer name merely contained theirs -- which for the
        # eight "N2 Advertising - Cirilla's <city>" rows is a customer called
        # "Cirilla's". Nine clients carrying $22,091/mo of live billing sit in
        # that shape on this deployment's own book.
        if any(n and _norm_name(c) == n for c in data):
            continue
        # A resembling customer is named on the row rather than used to
        # silence it: "no invoice found" and "no invoice under this exact
        # name, but QuickBooks has one that looks like it" send somebody to
        # two different places, and only the first is a billing gap.
        near = sorted({c for c in data if n and _norm_name(c)
                       and (n in _norm_name(c) or _norm_name(c) in n)})
        note = ("no invoice found" if not near else
                "no invoice under this name; QuickBooks has "
                + _join_names(near))
        rows.append((g["live_total"], [
            _c360_link(name), _money(0), _money(g["live_total"]),
            "▼ " + _money(g["live_total"]) + f" ({note})",
            len(g["live"]),
            {"assign": name},
        ]))
        styles.append(None if not near else "sub")
    order = sorted(range(len(rows)), key=lambda i: -rows[i][0])
    rows = [rows[i] for i in order]
    styles = [styles[i] for i in order]
    return {
        "columns": columns,
        "rows": [r for _, r in rows],
        "row_styles": styles,
        "partners": partner_list(),
        "resembled": resembled,
        "note": (f"{len(rows)} customers whose QuickBooks invoices this month "
                 "don't match their active-product monthly total, biggest gap "
                 "first. ▼ = invoiced less than active products, ▲ = "
                 "invoiced more. Names are matched exactly and never on a "
                 "partial. "
                 + (f"{resembled} QuickBooks customer(s) only resemble a "
                    "client and are listed with no difference, because "
                    "there is no client we can stand behind to compute one "
                    "against. " if resembled else "")
                 + "A client invoiced under a similar but different name "
                 "is listed with that name on the row — a different thing "
                 "to chase from no invoice at all. Use \"Add to partner\" to "
                 "mark a record as handled by a partner — it's remembered and "
                 "won't show here again (nothing changes anywhere else)."),
    }


# ------------------------------------------------------------------ registry
def uploads_not_in_suite() -> dict:
    """Galleries holding client files that never reached Smart 1 Suite.

    A client who has gone to the trouble of sending their photos has done the
    hard part. If those files then sit in our gallery and never reach their
    Suite media library, the work is invisible at the moment someone builds
    their page — and nobody finds out until they go looking for an image that
    "should be there".

    Three different reasons land here and they need different actions, so the
    report says which rather than lumping them together:

      no Suite location   the gallery was made for a prospect, or nobody has
                          attached the location yet — expected for a prospect,
                          a gap for a live client
      sync is off         someone turned it off deliberately
      failed              it tried and Suite refused; the error is shown
    """
    # `measured: False` on both refusals, because neither is an answer. Left
    # off, the page reads no rows and draws "Nothing to report — all clear ✓"
    # over the note, so a gallery database that would not open reported every
    # client's uploads as safely in Suite — and `hub/report_cache.py` would
    # have kept that as the day's answer.
    try:
        from modules.image_picker.models import PickerClient, SavedImage, session
    except Exception as exc:                            # noqa: BLE001
        return {"columns": ["Client"], "rows": [], "measured": False,
                "note": f"Client Image Uploads isn't available here ({type(exc).__name__})."}

    try:
        db = session()
        rows_ = db.query(SavedImage, PickerClient).join(
            PickerClient, SavedImage.client_id == PickerClient.id).all()
    except Exception as exc:                            # noqa: BLE001
        return {"columns": ["Client"], "rows": [], "measured": False,
                "note": f"Couldn't read the uploads database ({type(exc).__name__})."}

    by_client: dict = {}
    for img, client in rows_:
        # Only files the client sent us. Stock we picked for them is a
        # different question and has its own answer in the gallery.
        if (img.collection_kind or "") != "upload":
            continue
        b = by_client.setdefault(client.id, {
            "client": client, "total": 0, "waiting": 0,
            "reasons": {}, "last": None, "last_error": "",
        })
        b["total"] += 1
        if img.created_at and (b["last"] is None or img.created_at > b["last"]):
            b["last"] = img.created_at
        if img.ghl_status == "sent":
            continue
        b["waiting"] += 1
        if img.ghl_status == "error":
            reason = "Failed"
            b["last_error"] = (img.ghl_error or "")[:120]
        elif not (client.ghl_location_id or "").strip():
            reason = "No Suite location"
        elif not client.ghl_enabled:
            reason = "Sync is off"
        else:
            reason = "Queued"
        b["reasons"][reason] = b["reasons"].get(reason, 0) + 1

    rows = []
    for b in by_client.values():
        if not b["waiting"]:
            continue
        c = b["client"]
        reason = ", ".join(f"{k} ({v})" for k, v in sorted(b["reasons"].items()))
        rows.append([
            _c360_link(c.name) if getattr(c, "kind", "") == "client" else c.name,
            "Client" if getattr(c, "kind", "") == "client" else "Prospect",
            b["waiting"],
            b["total"],
            reason,
            _dates.fmt(b["last"]),
            b["last_error"] or "",
        ])

    # Most files waiting first — that is the biggest pile of work sitting
    # invisible — then the most recently active gallery.
    rows.sort(key=lambda r: (-r[2], r[0] if isinstance(r[0], str) else ""))

    waiting = sum(r[2] for r in rows)
    return {
        "columns": ["Client", "Type", "Not in Suite", "Uploaded", "Why",
                    "Last upload", "Last error"],
        "rows": rows,
        "note": (f"{waiting} uploaded file(s) across {len(rows)} galler"
                 f"{'y' if len(rows) == 1 else 'ies'} have not reached Smart 1 "
                 f"Suite. A prospect with no Suite location is expected; a "
                 f"client with one is not."
                 if rows else
                 "Every uploaded file has reached Smart 1 Suite."),
    }


# The picker sits immediately after "Mapped to" — the cell it changes — and
# not at the end. On the end it was the seventh column of a table wider than
# its own scroll box, so on an ordinary laptop the header read "MAP TO CLIE"
# and the button read "Map to c": the control was there, cut in half, past the
# right edge, with no scrollbar visible until you tried. A feature you cannot
# see is a feature that does not exist, and "Google login" and "Domains" are
# reference columns nobody has to reach.
GOOGLE_COLUMNS = ["Platform", "Resource", "Mapped to", "Map to client",
                  "Matched on", "Google login", "Domains"]


# Group order on /qa is this dict's insertion order -- qa_home() walks REPORTS
# and appends each group the first time it sees one. So moving an entry moves
# its whole group, and an entry with no "group" at all falls into a default
# bucket literally called "Reports": that is why Uploads Not In Suite used to
# open the page alone under a heading that named nothing.
def google_accounts() -> dict:
    """Every Google resource we can reach, and the client it maps to.

    Unmapped rows sort first, because those are the only ones anybody has to
    act on: a GA4 property nothing is joined to is a property that will not
    appear on a Client 360 page, will not pre-fill a tool, and will not turn
    up when somebody asks "do we have analytics for this client?".

    The rows say *how* each mapping was reached. That matters more than it
    looks: a domain match is the client's own URL and is as certain as this
    gets, while a name match on a GA4 property is a guess the index is willing
    to stand behind but a human should still glance at — GA4 property
    summaries carry no URL, so a name is genuinely all there is.

    Read from hub/google_index.py rather than swept here. A QA report that
    took a minute of Tag Manager pacing to open is a report nobody opens.

    Two things this report does rather than describes:

    **The domain rule is re-applied on every load.** The index only ever ran
    it against the client list as it stood at sweep time, so a client that
    gained a URL an hour ago still had their GTM container listed here as
    belonging to nobody. `apply_domain_matches()` re-joins those now, writes
    nothing when nothing changed, and never touches a resource that already
    has a client. What it joined is counted in the note — a row that changed
    itself between two loads has to say so.

    **Every row carries a client picker.** The report is where somebody
    notices that a property maps to nobody, so it is where they should be able
    to say whose it is, rather than being sent to another screen to find the
    row again. It posts to the same `/api/google/attach` the orphan list uses,
    so attaching writes the client record, the index and the Knack website
    record and reports each — one button that means one thing everywhere.
    Suggestions come from `hub/google_links.suggest_for()` and are proposals,
    never applied: the picker opens on them and a person still chooses.
    """
    from . import google_index
    from . import google_links

    rejoined = google_index.apply_domain_matches()
    st = google_index.status()
    if st["never_built"]:
        # NOT an empty `rows` list with a note. qa_report.html renders any
        # report with no rows as "Nothing to report — all clear ✓" and returns
        # before it ever reaches the note, so the most careful wording in the
        # world was being replaced on screen by a green tick saying the
        # opposite. `unavailable` is rendered instead of the all-clear, which
        # is the whole difference between "we looked and it is fine" and "we
        # could not look".
        detail = ""
        if st["last_error"]:
            detail = (f" The last attempt, at {st['last_attempt']}, failed: "
                      f"{st['last_error']}")
        elif st["last_attempt"]:
            detail = f" Last attempted at {st['last_attempt']}."
        return {
            "columns": GOOGLE_COLUMNS,
            "rows": [],
            "unavailable": {
                "message": ("The Google account index has not been built yet, "
                            "so there is nothing to list. This is NOT the same "
                            "as having no Google accounts — nothing has swept "
                            "them yet." + detail),
                "action_label": "Build the index now",
                "action_post": "/api/google/rebuild",
            },
        }

    rows, styles = [], []
    index_rows = google_index.rows()
    # Built once for the whole list, not once per row: it reads the Knack
    # website registry and the client alias index, and both are a page-load's
    # worth of work each if asked for again on every unmapped property.
    ctx = google_links._context() if any(                        # noqa: SLF001
        not r["client"] for r in index_rows) else None
    for r in index_rows:
        mapped = (_c360_link(r["client"]) if r["client"]
                  else {"text": "— not mapped —",
                        "title": r["match_detail"] or ""})
        matched = r["match"] or ""
        if not r["client"] and r["candidates"]:
            matched = "ambiguous: " + ", ".join(r["candidates"][:3])
        resource = ({"text": r["name"] or r["resource_id"], "href": r["open_url"]}
                    if r["open_url"] else (r["name"] or r["resource_id"]))
        # Suggestions are worked out for the rows that need them and nothing
        # else. A mapped row's picker opens empty — proposing an owner for a
        # resource that already has one is how somebody re-assigns it by
        # accident.
        suggestions = []
        if not r["client"] and ctx is not None:
            try:
                suggestions = [
                    {"client": sg["client"], "confidence": sg["confidence"],
                     "why": sg["why"]}
                    for sg in google_links.suggest_for(r, ctx)[:4]]
            except Exception:                                    # noqa: BLE001
                suggestions = []
        rows.append([
            r["platform"],
            resource,
            mapped,
            {"map_client": r["resource_id"],
             "current": r["client"],
             "suggestions": suggestions,
             # The CSV reads this. A cell with no text exports as a blank
             # column headed "Map to client", which says nothing about
             # whether the row was mapped.
             "text": (f"mapped to {r['client']}" if r["client"]
                      else ("suggested: " + suggestions[0]["client"]
                            if suggestions else "not mapped, no suggestion"))},
            matched or "—",
            r["google_login"],
            ", ".join(r["domains"]) or "—",
        ])
        # Unmapped rows are the finding, so they are the ones tinted.
        # "yellow", not "warn": qa_report.html only styles green/yellow/red/sub,
        # and an unknown name renders as a class with no CSS behind it — the
        # rows would look exactly like every other row and nothing would say so.
        styles.append("yellow" if not r["client"] else None)

    age = (f"{st['age_hours']}h old" if st["age_hours"] is not None else "age unknown")
    stale = " — STALE, the refresh job may not be running" if st["stale"] else ""
    by_match = ", ".join(f"{k} {v}" for k, v in (st["by_match"] or {}).items())
    errs = ""
    if st["errors"]:
        errs = (f" {len(st['errors'])} connected account(s) failed during the "
                f"sweep, so their resources are missing from this list: "
                + ", ".join(str(e.get("email") or "?") for e in st["errors"][:5]) + ".")
    if not rows:
        return {
            "columns": GOOGLE_COLUMNS,
            "rows": [],
            "unavailable": {
                "message": (f"The index was built {st['built_at']} but found "
                            f"no Google resources at all across "
                            f"{len(st['accounts'])} connected login(s). That "
                            f"is worth investigating rather than reading as "
                            f"all-clear: either no account is connected, or "
                            f"the sweep is being refused."
                            + (f" Errors: {st['errors']}" if st["errors"] else "")),
                "action_label": "Re-run the sweep",
                "action_post": "/api/google/rebuild",
            },
        }

    auto = ""
    if rejoined.get("mapped"):
        n = rejoined["mapped"]
        auto = (f" {n} of them {'was' if n == 1 else 'were'} joined to a "
                f"client by domain on this load rather than by the sweep: "
                f"the client had gained that domain since. ")
    elif rejoined.get("error"):
        auto = f" Domain re-matching did not run: {rejoined['error']} "

    return {
        "columns": GOOGLE_COLUMNS,
        "rows": rows,
        "row_styles": styles,
        "note": (f"{st['resources']} Google resources across "
                 f"{len(st['accounts'])} connected login(s): {st['mapped']} "
                 f"mapped to a client, {st['unmapped']} not. Matched on: "
                 f"{by_match or 'nothing yet'}. Index built {st['built_at']} "
                 f"({age}){stale}.{errs}{auto} Unmapped rows are listed first. "
                 f"Anything carrying a client's domain is mapped for you; for "
                 f"the rest — a GA4 property carries no URL, so most of them — "
                 f"pick the customer beside \u201cMapped to\u201d and it is attached to "
                 f"the client record, this index and the Knack website record "
                 f"at once."),
    }


# --------------------------------------------------- Smart 1 Sites vs hosting
def sites_billing() -> dict:
    """Every Smart 1 Sites project against the three hosting products.

    The join lives in `hub/sites_billing.py`; this is the rendering, and the
    only decisions it makes are about what a reader is told:

    * **Findings first, in the order somebody acts on them.** Money going out
      for a dead site, then a charge naming nothing, then a live site nobody is
      billing for. The sites that are billed and fine are last and are still
      printed — the question "which sites are billed?" deserves an answer, not
      only its complement.
    * **An unmatched charge keeps its description.** The description is the
      only evidence there is, and a row that says "no match" without showing
      what it failed to match is a row nobody can settle.
    * **"We could not look" is not "all clear."** `unavailable()` covers every
      way this report cannot answer — QuickBooks unreadable, the site list
      unreadable or empty, the catalogue unreadable, and the three products
      missing from it — and the page renders that as *Not measured* rather than
      as a green tick. A registry that could not be read costs one matching
      rule rather than the report, so it is said in the note instead.
    """
    from . import sites_billing as sb

    columns = ["Site / charge", "Domain", "Site status", "QuickBooks customer",
               "Product", "Last billed", "Amount", "Matched on"]
    _, err = _qb_state()
    if err:
        return {"columns": columns, "rows": [], "note": err, "needs_qb": True}

    rep = sb.report()
    blocked = sb.unavailable(rep)
    if blocked:
        return {"columns": columns, "rows": [],
                "unavailable": {"message": blocked,
                                "action_href": "/status",
                                "action_label": "Open System Status"}}

    c = rep["counts"]
    rows, styles = [], []

    def group(text, tone=None):
        rows.append([{"text": text, "group": True, "tone": tone}]
                    + [""] * (len(columns) - 1))
        styles.append(None)

    def site_cell(site):
        return ({"text": site["name"] or site["project_id"], "href": site["href"]}
                if site["href"] else (site["name"] or site["project_id"]))

    def domain_cell(site):
        if site["domain"]:
            return site["domain"]
        # A project parked on a Simvoly platform domain has no real domain, and
        # printing the platform one would read as an address somebody could
        # visit. Say which of the two blanks it is.
        return {"text": ("platform domain only" if site["raw_domain"]
                         else "no domain recorded"), "muted": True}

    def charge_row(rec, status_cell):
        site = rec["site"]
        cust = rec["customer"] or ""
        cust_cell = ({"text": cust, "href": rec["link"]} if cust and rec["link"]
                     else (cust or {"text": "—", "muted": True}))
        return [site_cell(site), domain_cell(site), status_cell, cust_cell,
                rec["product"] or {"text": "—", "muted": True},
                rec["last_date"] or {"text": "never", "muted": True},
                _money(rec["last_amount"]) if rec["last_date"] else
                {"text": "not measured", "muted": True},
                rec["why"] or ""]

    if rep["billed_inactive"]:
        group(f"Billed and not active ({c['billed_inactive']}) — money going "
              f"out for a site that is not serving", "now")
        for rec in rep["billed_inactive"]:
            rows.append(charge_row(rec, {"text": rec["site"]["reason"], "pill": "bad"}))
            styles.append("red")

    if rep["unmatched"]:
        group(f"Hosting charged, no site matched ({c['unmatched']}) — either we "
              f"host it somewhere else or the description names nothing we hold",
              "now")
        for ln in rep["unmatched"]:
            m = ln.get("match") or {}
            desc = (ln.get("description") or "").strip()
            rows.append([
                {"text": desc or "(no description on this line)",
                 "muted": not desc,
                 "title": (ln.get("invoice_text") or "")[:400]},
                m.get("domain") or {"text": "—", "muted": True},
                {"text": "no site", "pill": "warn"},
                ({"text": ln.get("customer") or "", "href": ln.get("link")}
                 if ln.get("customer") and ln.get("link") else (ln.get("customer") or "")),
                ln.get("product") or "",
                ln.get("date") or "",
                _money(ln.get("amount") or 0),
                m.get("why") or "",
            ])
            styles.append("red")

    if rep["unbilled"]:
        group(f"Live site, nothing billed ({c['unbilled']}) — no hosting charge "
              f"in the last {rep['months']} months", "soon")
        for rec in rep["unbilled"]:
            rows.append(charge_row(rec, {"text": "Active", "pill": "ok"}))
            styles.append("yellow")

    if rep["lapsed"]:
        group(f"Live site, billing lapsed ({c['lapsed']}) — billed once and not "
              f"in the last {rep['recent_months']} months", "soon")
        for rec in rep["lapsed"]:
            rows.append(charge_row(rec, {"text": "Active", "pill": "ok"}))
            styles.append("yellow")

    if rep["short"]:
        group(f"Fewer hosting charges than live sites ({c['short']}) — matched "
              f"on the customer name, so which site each charge covers is not "
              f"stated anywhere", "soon")
        for s in rep["short"]:
            rows.append([
                s["customer"],
                {"text": ", ".join(x["domain"] for x in s["sites"] if x["domain"]) or "—",
                 "muted": True},
                {"text": f"{len(s['sites'])} live sites", "pill": "warn"},
                s["customer"], "—", "—", _money(s["amount"]),
                f"{s['lines']} hosting charge(s) this period against "
                f"{len(s['sites'])} live sites: "
                + ", ".join(x["name"] for x in s["sites"][:4]),
            ])
            styles.append("yellow")

    if rep["ok"]:
        group(f"Billed and active ({c['ok']})")
        for rec in rep["ok"]:
            rows.append(charge_row(rec, {"text": "Active", "pill": "ok"}))
            styles.append(None)

    cat = rep.get("catalogue") or {}
    missing, similar = cat.get("missing") or [], cat.get("similar") or []
    miss_note = ""
    if rep.get("registry_error"):
        # One of the five matching rules did not run. Silence here would read
        # as "the customer name matched nothing", which is a different and
        # worse claim than "we could not check the client registry".
        miss_note += (" The client registry could not be read ("
                      + rep["registry_error"] + "), so charges were not matched "
                      "through it \u2014 some of the unmatched rows below may have "
                      "an owner this run could not look up.")
    if missing:
        miss_note += (" " + ", ".join(f"\u201c{p}\u201d" for p in missing)
                      + (" is" if len(missing) == 1 else " are")
                      + " not in the QuickBooks catalog under that name, so "
                        "nothing can be billed under it \u2014 that is a property of "
                        "the filter, not of the book.")
    if similar:
        # Named rather than matched. A product called "Monthly Web Hosting -
        # Annual" is probably hosting and is not one of the three, and folding
        # it in on a substring is the rule hub/client_key.py refuses; leaving
        # it out in silence loses a tier of revenue from a report that looks
        # complete. So: say it exists and let somebody decide.
        miss_note += (" QuickBooks also has "
                      + ", ".join(f"\u201c{p}\u201d" for p in similar[:5])
                      + (" and others" if len(similar) > 5 else "")
                      + ", which resemble these three and are NOT counted here.")

    return {
        "columns": columns,
        "rows": rows,
        "row_styles": styles,
        "note": (f"{c['sites']} Smart 1 Sites projects ({c['active']} active) "
                 f"against {c['lines']} hosting charges on invoices since "
                 f"{rep['since']}. {c['billed_inactive']} billed with the site "
                 f"not active, {c['unmatched']} charges matching no site, "
                 f"{c['unbilled']} live sites never billed, {c['lapsed']} "
                 f"lapsed, {c['ok']} billed and fine. Charges are joined to a "
                 f"site by a domain in the description first, then by the "
                 f"customer name matched exactly \u2014 a resemblance is printed as "
                 f"\u201cpossible\u201d and still counted as unmatched. Only invoices "
                 f"are read: sales receipts and recurring templates are not, so "
                 f"a site billed either of those ways appears here as unbilled."
                 + miss_note),
    }



def io_reconcile_report() -> dict:
    """Insertion orders we sent that Knack has no campaign for.

    The one seam nothing watched. Submitting an order logs it, registers the
    client when nobody has heard of them, and POSTs it to Smart 1 Suite --
    and then the Hub's involvement ends. A campaign that was never written
    into Knack is indistinguishable from one that was: the log says the order
    went, the overlay row still stands in for a record that never arrived, and
    Client 360 goes on saying the cards are empty. Nobody is billed and
    nothing is trafficked.

    `hub/io_reconcile.py` holds every rule. What matters here is that a run
    which could not read Knack live returns `measured: False`, so
    `report_cache` never freezes "we could not look" into the shape of "there
    is nothing to see".
    """
    from . import io_reconcile

    data = io_reconcile.report()
    note = io_reconcile.note(data)
    if not data.get("measured"):
        return {"columns": ["Order"], "rows": [], "row_styles": [],
                "measured": False, "error": data.get("error", ""),
                "note": note}

    columns = ["Order", "Client", "Submitted", "Flight starts", "Monthly",
               "Partner", "Recorded in", "Settle"]
    rows, styles = [], []

    def _when(row):
        at = row.get("at")
        if not at:
            return "not recorded"
        days = row.get("days")
        stamp = at.strftime("%b %-d, %Y")
        return stamp if days is None else f"{stamp} · {days}d ago"

    def _start(row):
        start = row.get("start")
        if not start:
            return "\u2014"
        stamp = start.strftime("%b %-d, %Y")
        return ("started " + stamp) if row.get("started") else stamp

    for row in data["outstanding"]:
        client = str(row.get("client") or "")
        rows.append([
            str(row.get("order") or ""),
            _c360_link(client) if client else "(no client name)",
            _when(row),
            _start(row),
            _money(row.get("monthly")) if row.get("monthly") else "\u2014",
            str(row.get("partner") or "") or "\u2014",
            ", ".join(row.get("sources") or []) or "\u2014",
            {"io_settle": str(row.get("order") or "")},
        ])
        # A flight already running is red; one merely late is amber. A page of
        # red is a page people scroll past, which is the note this codebase
        # makes about a recommendation drawn in a finding's color.
        styles.append("bad" if row.get("started") else "warn")

    if data["unreconcilable"]:
        rows.append([{"group": f"No order number recorded "
                               f"({len(data['unreconcilable'])})",
                      "tone": "later"}, "", "", "", "", "", "", ""])
        styles.append(None)
        for row in data["unreconcilable"]:
            client = str(row.get("client") or "")
            rows.append([
                "(none)",
                _c360_link(client) if client else "(no client name)",
                _when(row), _start(row),
                _money(row.get("monthly")) if row.get("monthly") else "\u2014",
                str(row.get("partner") or "") or "\u2014",
                ", ".join(row.get("sources") or []) or "\u2014",
                "",
            ])
            styles.append(None)

    if data["settled"]:
        rows.append([{"group": f"Settled — not expected in Knack "
                               f"({len(data['settled'])})", "tone": "later"},
                     "", "", "", "", "", "", ""])
        styles.append(None)
        for row in data["settled"]:
            mark = row.get("settled") or {}
            client = str(row.get("client") or "")
            who = mark.get("by") or "not recorded"
            when = str(mark.get("at") or "")[:10]
            # Why it was settled goes in the settle cell rather than borrowing
            # the Partner column: a column whose heading means one thing above
            # a group rule and another below it is a table nobody can read.
            detail = (io_reconcile.SETTLE_REASONS.get(
                mark.get("reason"), str(mark.get("reason") or ""))
                + f" — {who}, {when}"
                + (f". {mark['note']}" if mark.get("note") else ""))
            rows.append([
                str(row.get("order") or ""),
                _c360_link(client) if client else "(no client name)",
                _when(row), _start(row),
                _money(row.get("monthly")) if row.get("monthly") else "\u2014",
                str(row.get("partner") or "") or "\u2014",
                ", ".join(row.get("sources") or []) or "\u2014",
                {"io_unsettle": str(row.get("order") or ""), "detail": detail},
            ])
            styles.append(None)

    return {
        "columns": columns,
        "rows": rows,
        "row_styles": styles,
        "measured": True,
        "note": note,
        # Read by the settle control, so the reasons on screen cannot drift
        # from the ones the write accepts.
        "settle_reasons": [{"key": k, "label": v}
                           for k, v in io_reconcile.SETTLE_REASONS.items()],
    }


# Whole tools rather than table-returning functions, and every one of them
# answers "what is wrong / what do we owe" -- which is the question the QA page
# exists for. They were on the Tools page under a group called "Client Work",
# which named where the work came from rather than what the screen is for, and
# a report nobody thinks to look for is a report nobody works. Each keeps its
# own URL, so every existing link and every Client 360 crumb still resolves.
#
# Module-level rather than inline in the route that draws them, because two
# things read this now: the QA page, and hub/search_index.py. A list only the
# route could see is a list the search box could not, so "Domain Renewals"
# and "Match Sites to Clients" answered nothing typed into it -- which is the
# same invisibility the tile rule exists to stop, one screen further on.
EXTRAS = [
        # Clients, first: these two are about the book rather than about a
        # system, and the question "which of my clients needs an hour today"
        # is the one somebody opens this page with more often than any audit
        # on it. A report nobody thinks to look for is a report nobody works.
        ("Clients", "my-clients", {
            "title": "My Clients",
            "desc": "Everything outstanding on the clients assigned to you \u2014 "
                    "creative waiting, proposals unanswered, an insertion "
                    "order running out, a dashboard nobody built \u2014 with "
                    "the client carrying the most at the top. Ignore or "
                    "complete anything from the row it is on.",
            "ico": "&#128203;", "href": "/my-clients"}),
        ("Clients", "client-owners", {
            "title": "Assign Clients",
            "desc": "Who owns which client. Assign one at a time, a whole "
                    "selection at once, or everything a media partner "
                    "carries \u2014 and see who is holding nothing.",
            "ico": "&#128100;", "href": "/qa/client-owners"}),
        ("Data Quality", "stale-creative", {
            "title": "Stale Creative",
            "desc": "How long since we last produced creative for each "
                    "active client — and who has never had any.",
            "ico": "&#9203;", "href": "/qa/stale-creative"}),
        ("Data Quality", "unattached-images", {
            "title": "Unattached Images",
            "desc": "Every image the Hub creates, uploads or lets somebody "
                    "choose \u2014 which tools file what they make into a "
                    "client's gallery, and which images name nobody at all. "
                    "Attach one to a client without leaving the row.",
            "ico": "&#128444;", "href": "/qa/unattached-images"}),
        ("Data Quality", "web-tickets", {
            "title": "Web Tickets",
            "desc": "Website change requests from Knack: what's open, "
                    "what's gone stale, and per-client history.",
            "ico": "&#127915;", "href": "/tools/tickets/"}),
        ("Data Quality", "scan-all-clients", {
            "title": "Scan All Clients",
            "desc": "Audit every client with a website on file. Previews "
                    "the credit cost, skips anything scanned recently, and "
                    "caps each run before anything is spent.",
            "ico": "&#9776;", "href": "/scans/bulk"}),
        ("Data Quality", "match-sites", {
            "title": "Match Sites to Clients",
            "desc": "Every website we hold that nobody is attached to. "
                    "Accepting a match writes the client registry, their "
                    "Client 360 record, the Simvoly project and the Knack "
                    "website record at once — and reports each separately.",
            "ico": "&#128279;", "href": "/tools/sites-match"}),
        ("Data Quality", "match-google", {
            "title": "Match Google Accounts",
            "desc": "Every Analytics property, Tag Manager container and "
                    "Search Console property we can reach that maps to no "
                    "client — searchable, with whoever it might belong to "
                    "and why.",
            "ico": "&#128202;", "href": "/tools/google-match"}),
        ("Data Quality", "campaign-assets", {
            "title": "Campaign Assets Needed",
            "desc": "Every campaign on an insertion order still waiting on "
                    "a clarification or on additional assets, grouped by "
                    "media partner then internal sales — so the chase is "
                    "one list per partner.",
            "ico": "&#128230;", "href": "/tools/campaign-assets"}),
        # Billing rather than Data Quality: the question this one answers
        # is whether QuickBooks invoiced a renewal, which is the same
        # question as the three reports it now sits beside.
        ("Billing & Accounting", "domain-renewals", {
            "title": "Domain Renewals",
            "desc": "Every domain Smart 1 bought for a client, by the "
                    "month its renewal is billed. This month says whether "
                    "QuickBooks actually invoiced it; later months ask "
                    "whether it should renew at all.",
            "ico": "&#128197;", "href": "/tools/domains"}),]


REPORTS = {
    "prospect-queue": {
        "title": "Prospects To Chase",
        "desc": "Who to call, in the order the work has to happen — not in the "
                "CRM, the same business twice, audited and unquoted, never "
                "audited, then quoted and waiting.",
        "ico": "&#128222;",
        "fn": prospect_queue,
        "group": "Sales",
    },
    "active-clients": {
        "title": "Active Clients",
        "desc": "Every client with a live product or billing this month — partner, salesperson, live monthly and dashboard status.",
        "ico": "&#9679;",
        "fn": active_clients,
        "group": "Clients",
    },
    "io-not-in-knack": {
        "title": "Orders With No Campaign",
        "desc": "Insertion orders we sent that Knack has no campaign for \u2014 "
                "with the ones whose flight has already started at the top, "
                "because those are running in nobody\u2019s system.",
        "ico": "&#128203;",
        "fn": io_reconcile_report,
        "group": "Data Quality",
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
    "google-accounts": {
        "title": "Google Accounts & Mapping",
        "desc": "Every GA4 property, GTM container, Search Console property "
                "and Business Profile we can reach — and which client each one "
                "maps to, or why it maps to none.",
        "ico": "&#128506;",
        "fn": google_accounts,
        "group": "Data Quality",
    },
    "sell-to-clients": {
        "title": "What We Could Sell Each Client",
        "desc": "Findings read off each active client's own website, out of "
                "audits already paid for — with the never-audited and the "
                "stale named rather than left off.",
        "ico": "&#128176;",
        "fn": sell_to_clients,
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
    "ghl-billing-no-products": {
        "title": "Suite Billing, No Active Product",
        "desc": "Smart 1 Suite sub-accounts with active GHL billing but no live Smart 1 marketing product on file in Knack.",
        "ico": "&#128681;",
        "fn": ghl_billing_no_products,
        "group": "Suite (GoHighLevel)",
    },
    "ghl-billing-this-month": {
        "title": "Suite Billing This Month",
        "desc": "Every Smart 1 Suite sub-account with active GHL billing this month — client, plan and monthly price, simplified.",
        "ico": "&#128179;",
        "fn": ghl_billing_this_month,
        "group": "Suite (GoHighLevel)",
    },
    "uploads-not-in-suite": {
        "title": "Uploads Not In Suite",
        "desc": "Client files uploaded to a gallery that never reached their Smart 1 Suite media library — with the reason for each.",
        "ico": "&#8593;",
        "fn": uploads_not_in_suite,
        "group": "Suite (GoHighLevel)",
    },
    # One group, not two. Splitting them put a single report under "Accounting"
    # and two under "Billing (QuickBooks)", which reads as a distinction the
    # work does not make -- all three are the same question, asked of whichever
    # system happens to hold the answer. The label drops "(QuickBooks)" because
    # it is no longer true of the whole group: Accounting Requests comes from
    # the GHL pipeline, and a heading naming the wrong source is worse than one
    # naming none.
    "billing-comparison": {
        "title": "Customer Billing Comparison",
        "desc": "QuickBooks invoices per customer: this month vs the last three. Decreases listed first, biggest swings on top.",
        "ico": "&#128181;",
        "fn": billing_comparison,
        "group": "Billing & Accounting",
    },
    "invoice-off": {
        "title": "Invoice Off Report",
        "desc": "Customers whose invoiced amount this month doesn't match their active-product monthly total.",
        "ico": "&#9878;",
        "fn": invoice_off,
        "group": "Billing & Accounting",
    },
    "sites-billing": {
        "title": "Sites Billing Report",
        "desc": "Every Smart 1 Sites project against the three QuickBooks "
                "hosting products \u2014 which live sites nobody is billing for, "
                "and which dead ones are still being charged.",
        "ico": "&#127760;",
        "fn": sites_billing,
        "group": "Billing & Accounting",
    },
    "accounting-requests": {
        "title": "Accounting Requests",
        "desc": "Every request in the Accounting Requests pipeline (Smart 1 Marketing · GHL) — change stages right from the report.",
        "ico": "&#128203;",
        "fn": accounting_requests,
        "group": "Billing & Accounting",
    },
}


# The two reports that take a parameter. Everything else answers one
# question, so its cache entry is one file — see `cache_key()`.
MONTHLY = ("sales-scorecard", "partner-scorecard")


def run(key: str, month: str = "") -> dict:
    """One report, with the source it read named on it.

    A stale export looks exactly like live data on screen, which is the whole
    reason `/qa` reading it went unnoticed — so a report built from the
    product book says which source answered, the sentence
    `knack_data.products_note()` gives the SEO list and Client 360.

    It is appended here rather than in each report because the set of reports
    built from products is not a list anybody would keep in step: `_products()`
    records the read, so a report that asks gets the sentence and one that
    does not gets nothing, without a table naming which is which. A report
    that could not measure is left alone — its note already says why it is not
    measured, and a staleness note under that describes rows nobody drew.
    """
    meta = REPORTS.get(key)
    if not meta:
        raise KeyError(key)
    _SRC.source, _SRC.age = "", None
    if key in MONTHLY:
        out = meta["fn"](month)
    else:
        out = meta["fn"]()
    note = _source_note()
    if note and out.get("measured") is not False:
        out["note"] = (str(out.get("note") or "").strip() + " " + note).strip()
    out["key"] = key
    out["title"] = meta["title"]
    return out


def cache_key(key: str, month: str = "") -> tuple[str, str]:
    """The name and parameters one report is cached under.

    The month is part of the key only for the two reports that read it. Every
    other report is handed whatever `?month=` happened to be on the URL and
    ignores it, so keying on it would write a second identical file the first
    time somebody arrived from a scorecard link.
    """
    return f"qa:{key}", (str(month or "").strip() if key in MONTHLY else "")


def run_cached(key: str, month: str = "", *, force: bool = False) -> dict:
    """Today's answer for one report — run once, then read.

    `hub/report_cache.py` holds the rules. What matters at this call site is
    that a report which could not measure — QuickBooks not connected, the GHL
    pipeline unreachable, Knack refusing — is never stored as the day's
    number, so connecting the provider an hour later does not leave the page
    reporting "not configured" until tomorrow.
    """
    from . import report_cache
    name, params = cache_key(key, month)
    return report_cache.serve(name, lambda: run(key, month),
                              params=params, force=force)


def forget(*keys: str) -> int:
    """Drop the cached copy of these reports. Call after a write.

    Every action on a QA report removes a row from it — marking an accounting
    request, assigning an invoice to a partner, skipping a client that needs
    no dashboard, attaching a Google property to its owner. Without this the
    row is still there on the next open and the button reads as having done
    nothing, which is how it comes to be pressed twice.

    With no arguments, every QA report is dropped. That is the right answer
    for a write nobody can attribute to one report: running them again costs
    one build each, and a stale row costs somebody's trust in the page.

    Never raises. These are called from inside the write itself, and a cache
    that could not be dropped must not turn a successful write into a failed
    one — the worst case is a stale row, which the next Refresh clears.
    """
    try:
        from . import report_cache
        if not keys:
            return report_cache.invalidate("qa:")
        return report_cache.invalidate(*[f"qa:{k}" for k in keys])
    except Exception:                                       # noqa: BLE001
        return 0
