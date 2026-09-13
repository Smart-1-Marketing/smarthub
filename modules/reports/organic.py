"""The organic-search section of a client's report: analytics, search, and
what we did.

``gate(client_key, client_name)`` decides whether a client gets the section
at all, and ``section(link, rng, today)`` builds it. Both are read by
``client_view.build()``, so the page, its ``data.json`` and the PDF carry
one answer, and by the staff page, which prints the gate so a rep can see
why the section is or is not on the client's report.

## The gate

Two things have to be true, and each is read from the module that owns it:

* **The client has an SEO product** -- a row in ``hub.seo.seo_clients()``,
  which is the live product book filtered to SEO, matched on the exact
  normalized name (``hub.client_key.normalise_name``) and never a
  substring. A client we do not do SEO for gets no organic section however
  much organic traffic their site has: the section says what we did, and
  for them we did nothing.
* **A GA4 property is known for the client** -- attached on the SEO client
  page (``hub.seo.get_links(name)["analytics"]``, a ``resource_id`` and the
  ``google_login`` that can read it), else joined by the Google index
  (``hub.google_index.for_client``). Both carry the login, which is the
  half a report needs: a property id alone reads nothing.

Search Console is the same lookup for the ``gsc`` kind, and it is NOT part
of the gate: a client with analytics and no Search Console gets the
section with that sub-block saying so. Google Business Profile is a
"coming soon" card and nothing else -- ``GOOGLE_GMB_ENABLED`` is off on this
deployment and the Business Profile APIs need per-project access Google
has not granted, so nothing here calls them.

## What is measured, and what is said

GA4 organic -- sessions, users, engaged sessions, key events -- for the
period against the period of the same length immediately before, through
``modules.google_finder.app``'s own pieces (``organic_filter()`` is the one
reading of what "organic" means, ``ga4_batch_run_reports()`` the one call).
A 12-month organic-sessions trend rides in the same HTTP call. Search
Console clicks, impressions, CTR and position for the two periods, and the
top ten queries and pages by clicks. "What we did" counts only rows that
exist in ``hub/seo.py``'s store and the scans table for the period -- a
client who does not buy blogs has no blogs row, never a zero.

Every figure a client reads is labeled in ``LABELS``: *Visits from Google
search*, *Searches that showed your site*, *Average position*. Nothing
names GA4 or Search Console by product unless the link's ``view_json``
says ``name_products``; "Google Analytics" and "Google search" are the
words otherwise. A sub-block that could not be read says so rather than
drawing zeros, and the whole section costs the page nothing when it
fails -- ``client_view`` wraps it the way it wraps the trend.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

# What the client reads. The key is what data.json carries.
LABELS = {
    "section": "Organic search",
    "sessions": "Visits from Google search",
    "users": "Visitors from Google search",
    "engaged": "Engaged visits",
    "key_events": "Key actions taken",
    "gsc_clicks": "Clicks from Google search",
    "gsc_impressions": "Searches that showed your site",
    "gsc_ctr": "Click-through rate",
    "gsc_position": "Average position",
    "queries": "What people searched for",
    "pages": "Pages Google sent them to",
    "work": "What we did this period",
    "trend": "Visits from Google search, last 12 months",
    "gbp": "Google Business Profile",
    "gbp_note": "Coming soon: calls, direction requests and profile views.",
}
# The same labels with the products named, for a link whose view_json says so.
LABELS_NAMED = {**LABELS,
                "sessions": "Organic sessions (Google Analytics 4)",
                "gsc_clicks": "Clicks (Search Console)",
                "gsc_impressions": "Impressions (Search Console)"}

GSC_MISSING_CLIENT = "Google search data is not connected for this site yet."
# The two analytics sentences a CLIENT may read. Neither names a product
# (the LABELS rule) and neither carries the login: the staff wording --
# which account, what it needs -- rides in ``staff_note`` and
# ``public_view()`` strips it, exactly as ``search.staff_note`` is.
GA4_MISSING_CLIENT = "Website visit data is not connected for this site yet."
GA4_FAILED_CLIENT = "Website visit data could not be read for this period."
GSC_MISSING_STAFF = ("Search Console is not connected for this client: attach the "
                     "Search Console property on the SEO client page.")

GA4_MONTHS = 12


# ---------------------------------------------------------------------------
# Sources -- each behind its own function, so a test stands in for one
# ---------------------------------------------------------------------------

def _seo_rows() -> list[dict]:
    from hub import seo as hub_seo
    return hub_seo.seo_clients()


def _links(name: str) -> dict:
    from hub import seo as hub_seo
    return hub_seo.get_links(name)


def _index_for(name: str, url: str) -> dict:
    from hub import google_index
    return google_index.for_client(name, url)


def _gf():
    """The loaded Google Finder module -- the one wsgi.py loaded where it
    has been, so this page does not open a second handle on the token
    database (hub/diagnostics.py's rule)."""
    import sys
    gf = sys.modules.get("gf_app")
    if gf is None:
        from modules.google_finder import app as gf
    return gf


def _seo_store(name: str) -> dict:
    from hub import seo as hub_seo
    return hub_seo.load_store(name)


def _scans(domain: str, start: date, end: date) -> dict:
    """{count, latest_score, latest_at} for completed scans of a domain --
    count within the period, the score from the newest whenever it ran."""
    from hub import scan_facts
    _report, meta, err = scan_facts.latest_report(domain)
    out = {"count": 0, "latest_score": meta.get("score") if meta else None,
           "latest_at": meta.get("scanned_at") if meta else "", "error": err}
    try:
        from sqlalchemy import inspect as sa_inspect, text
        from hub.client_context import canonical_domain
        from hub.extensions import shared_engine
        engine = shared_engine()
        if not sa_inspect(engine).has_table("scans"):
            return out
        # ``completed_at`` is a DateTime column: on Postgres substr() over a
        # timestamp does not exist, and the exception was swallowed below
        # into work.errors, which public_view() strips -- so on the live
        # database the "Site audits run" row was silently absent. date()
        # is a function on both engines; the bounds are bound as dates on
        # Postgres and as ISO text on SQLite, normalize.py's rule.
        pg = engine.dialect.name.startswith("postgres")
        sql = ("SELECT COUNT(*) FROM scans WHERE domain_key = :k AND status = 'complete' "
               "AND date(COALESCE(completed_at, created_at)) BETWEEN :s AND :e")
        with engine.connect() as conn:
            out["count"] = int(conn.execute(text(sql), {
                "k": canonical_domain(domain),
                "s": start if pg else start.isoformat(),
                "e": end if pg else end.isoformat()}).scalar() or 0)
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"{type(exc).__name__}"
    return out


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    try:
        from hub import client_key as ck
        return ck.normalise_name(name)
    except Exception:                                   # noqa: BLE001
        return str(name or "").strip().lower()


def _first_with_id(items) -> dict:
    for it in items or []:
        if isinstance(it, dict) and str(it.get("resource_id") or "").strip():
            return it
    return {}


def gate(client_key: str, client_name: str = "") -> dict:
    """Whether this client gets the section, and why or why not.

    ``{"gated": bool, "product": bool, "ga4": bool, "gsc": bool,
    "property_id", "google_login", "gsc_site", "gsc_login", "site_url",
    "domain", "name", "why": [...], "errors": [...]}``.
    """
    name = (client_name or "").strip()
    domain = client_key[2:] if str(client_key or "").startswith("d:") else ""
    if not name:
        try:
            from hub import client_key as ck
            name = ck.key_label(client_key)
        except Exception:                               # noqa: BLE001
            name = client_key
    out = {"gated": False, "product": False, "ga4": False, "gsc": False,
           "property_id": "", "google_login": "", "gsc_site": "", "gsc_login": "",
           "site_url": "", "domain": domain, "name": name, "products": [],
           "why": [], "errors": []}

    # 1. the product
    try:
        want = _norm(name)
        for row in _seo_rows():
            if _norm(row.get("client") or "") == want:
                out["product"] = True
                out["products"] = list(row.get("products") or [])
                out["site_url"] = row.get("url") or ""
                name = row.get("client") or name
                out["name"] = name
                break
    except Exception as exc:                            # noqa: BLE001
        out["errors"].append(f"the SEO product book could not be read ({type(exc).__name__})")
    if not out["domain"] and out["site_url"]:
        try:
            from hub.client_context import canonical_domain
            out["domain"] = canonical_domain(out["site_url"]) or ""
        except Exception:                               # noqa: BLE001
            pass

    # 2. analytics and search, attached first, then the index
    links, index = {}, {}
    try:
        links = _links(name) or {}
    except Exception as exc:                            # noqa: BLE001
        out["errors"].append(f"the client's attachments could not be read ({type(exc).__name__})")
    ga = _first_with_id(links.get("analytics"))
    gsc = _first_with_id(links.get("gsc"))
    if not ga or not gsc:
        try:
            index = _index_for(name, out["site_url"] or (f"https://{domain}" if domain else "")) or {}
        except Exception as exc:                        # noqa: BLE001
            out["errors"].append(f"the Google index could not be read ({type(exc).__name__})")
        ga = ga or _first_with_id(index.get("ga4"))
        gsc = gsc or _first_with_id(index.get("gsc"))
    if ga:
        out["ga4"] = True
        out["property_id"] = str(ga.get("resource_id") or "").strip()
        out["google_login"] = str(ga.get("google_login") or "").strip().lower()
    if gsc:
        out["gsc"] = True
        out["gsc_site"] = str(gsc.get("resource_id") or gsc.get("name") or "").strip()
        out["gsc_login"] = str(gsc.get("google_login") or out["google_login"]).strip().lower()

    if not out["product"]:
        out["why"].append("no live SEO product on the client's book")
    if not out["ga4"]:
        out["why"].append("no Google Analytics property is linked to the client")
    elif not out["google_login"]:
        out["why"].append("the linked Google Analytics property has no connected login to read it")
        out["ga4"] = False
    out["gated"] = out["product"] and out["ga4"]
    return out


# ---------------------------------------------------------------------------
# GA4
# ---------------------------------------------------------------------------

_GA4_METRICS = [{"name": "sessions"}, {"name": "totalUsers"},
                {"name": "engagedSessions"}, {"name": "keyEvents"}]


def _range_of(row, names: tuple) -> int:
    """Which of the named ranges a row belongs to -- by the name GA4 echoes
    when ranges are named (the hub/analytics_ask rule), then by the
    date_range_N tag."""
    for dv in row.get("dimensionValues") or []:
        val = str(dv.get("value") or "")
        if val in names:
            return names.index(val)
        if val.startswith("date_range_"):
            try:
                return int(val.rsplit("_", 1)[1])
            except ValueError:
                pass
    return 0


def _nums(row) -> list[float]:
    out = []
    for mv in row.get("metricValues") or []:
        try:
            out.append(float(mv.get("value") or 0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _change(now: int, was: int):
    return round((now - was) / was * 100, 1) if was else None


def ga4_requests(rng: dict, today: date) -> tuple[list, list, list]:
    """The two report requests and the month keys the trend covers."""
    start, end = rng["start"], rng["end"]
    span = (end - start).days
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span)
    ranges = [{"startDate": start.isoformat(), "endDate": end.isoformat(), "name": "current"},
              {"startDate": prev_start.isoformat(), "endDate": prev_end.isoformat(), "name": "previous"}]
    months = []
    y, m = today.year, today.month
    for _ in range(GA4_MONTHS):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()
    trend_start = date(months[0][0], months[0][1], 1)
    gf = _gf()
    totals = {"dateRanges": ranges, "metrics": _GA4_METRICS, "dimensionFilter": gf.organic_filter()}
    trend = {"dateRanges": [{"startDate": trend_start.isoformat(), "endDate": today.isoformat()}],
             "dimensions": [{"name": "yearMonth"}], "metrics": [{"name": "sessions"}],
             "dimensionFilter": gf.organic_filter(), "limit": 24}
    return [totals, trend], ranges, months


def _ga4(gate_: dict, rng: dict, today: date) -> dict:
    gf = _gf()
    token, err = gf.account_token(gate_["google_login"])
    if err:
        # ``err`` names the connected Google login ("adops@… is not
        # connected"); that is the staff half and never the client's.
        return {"measured": False, "note": GA4_MISSING_CLIENT, "staff_note": err}
    reqs, ranges, months = ga4_requests(rng, today)
    try:
        reports = gf.ga4_batch_run_reports(token, gate_["property_id"], reqs)
    except Exception as exc:                            # noqa: BLE001
        return {"measured": False, "note": GA4_FAILED_CLIENT,
                "staff_note": f"Google Analytics did not answer ({type(exc).__name__})."}
    cur = [0, 0, 0, 0]
    prev = [0, 0, 0, 0]
    for row in (reports[0] if reports else {}).get("rows") or []:
        vals = [int(v) for v in _nums(row)] + [0, 0, 0, 0]
        if _range_of(row, ("current", "previous")) == 0:
            cur = vals[:4]
        else:
            prev = vals[:4]
    keys = ("sessions", "users", "engaged", "key_events")
    current = dict(zip(keys, cur))
    previous = dict(zip(keys, prev))
    by_month = {f"{y:04d}{m:02d}": 0 for y, m in months}
    for row in (reports[1] if len(reports) > 1 else {}).get("rows") or []:
        dv = row.get("dimensionValues") or []
        ym = str(dv[0].get("value") or "") if dv else ""
        if ym in by_month:
            by_month[ym] = int((_nums(row) or [0])[0])
    trend = [{"month": f"{y:04d}-{m:02d}", "label": calendar.month_abbr[m],
              "sessions": by_month[f"{y:04d}{m:02d}"],
              "display": f"{by_month[f'{y:04d}{m:02d}']:,}",
              "partial": (y, m) == (today.year, today.month)} for y, m in months]
    return {
        "measured": True,
        "current": current, "previous": previous,
        "change": {k: _change(current[k], previous[k]) for k in keys},
        "previous_period": {"start": ranges[1]["startDate"], "end": ranges[1]["endDate"]},
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Search Console
# ---------------------------------------------------------------------------

def _gsc_totals(gf, token, site, start: date, end: date) -> dict:
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": []}
    rows = (gf.gsc_search_analytics(token, site, body) or {}).get("rows") or []
    r = rows[0] if rows else {}
    return {"clicks": int(r.get("clicks") or 0), "impressions": int(r.get("impressions") or 0),
            "ctr": round(float(r.get("ctr") or 0) * 100, 1),
            "position": round(float(r.get("position") or 0), 1)}


def _gsc_top(gf, token, site, start: date, end: date, dim: str) -> list[dict]:
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": [dim], "rowLimit": 10}
    rows = (gf.gsc_search_analytics(token, site, body) or {}).get("rows") or []
    out = []
    for r in rows:
        keys = r.get("keys") or [""]
        out.append({"key": str(keys[0]), "clicks": int(r.get("clicks") or 0),
                    "impressions": int(r.get("impressions") or 0),
                    "ctr": round(float(r.get("ctr") or 0) * 100, 1),
                    "position": round(float(r.get("position") or 0), 1)})
    out.sort(key=lambda r: -r["clicks"])
    return out[:10]


def _gsc(gate_: dict, rng: dict) -> dict:
    if not gate_.get("gsc") or not gate_.get("gsc_site"):
        return {"connected": False, "measured": False, "note": GSC_MISSING_CLIENT,
                "staff_note": GSC_MISSING_STAFF}
    gf = _gf()
    token, err = gf.account_token(gate_.get("gsc_login") or gate_.get("google_login"))
    if err:
        return {"connected": True, "measured": False, "note": "Google search data could not be read.",
                "staff_note": err}
    start, end = rng["start"], rng["end"]
    span = (end - start).days
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span)
    site = gate_["gsc_site"]
    try:
        current = _gsc_totals(gf, token, site, start, end)
        previous = _gsc_totals(gf, token, site, prev_start, prev_end)
        queries = _gsc_top(gf, token, site, start, end, "query")
        pages = _gsc_top(gf, token, site, start, end, "page")
    except Exception as exc:                            # noqa: BLE001
        return {"connected": True, "measured": False,
                "note": "Google search data could not be read.",
                "staff_note": f"Search Console did not answer ({type(exc).__name__})."}
    return {
        "connected": True, "measured": True, "site": site,
        "current": current, "previous": previous,
        "change": {k: _change(current[k], previous[k]) for k in ("clicks", "impressions")},
        "queries": queries, "pages": pages,
    }


# ---------------------------------------------------------------------------
# What we did
# ---------------------------------------------------------------------------

def _work(gate_: dict, rng: dict) -> dict:
    """Counts for the period, only for the things that exist. ``rows`` is
    the list the page draws, in order; a thing the client did not buy or
    that never happened is absent, never zero."""
    start, end = rng["start"].isoformat(), rng["end"].isoformat()
    rows: list[dict] = []
    errors: list[str] = []
    try:
        store = _seo_store(gate_["name"]) or {}
    except Exception as exc:                            # noqa: BLE001
        store, errors = {}, [f"the SEO record could not be read ({type(exc).__name__})"]
    posts = (store.get("blogs") or {}).get("posts") or []
    posted = [p for p in posts if p.get("posted") and start <= str(p.get("date") or "")[:10] <= end]
    if posts and any(p.get("posted") for p in posts):
        rows.append({"key": "blogs", "label": "Blog posts published", "count": len(posted)})
    pages = store.get("pages") or {}
    built = [p for p in pages.values() if start <= str(p.get("created") or "")[:10] <= end]
    if pages:
        rows.append({"key": "schema", "label": "Pages given structured data", "count": len(built)})
    faqs = 0
    try:
        from hub import faq as _faq
        faq_pages = _faq.list_pages(gate_["name"]) or []
        faqs = len([p for p in faq_pages if start <= str(p.get("created") or "")[:10] <= end])
        if faq_pages:
            rows.append({"key": "faqs", "label": "FAQ sections written", "count": faqs})
    except Exception:                                   # noqa: BLE001 - no FAQ store here
        pass
    scans = {"count": 0, "latest_score": None, "latest_at": "", "error": ""}
    if gate_.get("domain"):
        scans = _scans(gate_["domain"], rng["start"], rng["end"])
        if scans.get("error"):
            errors.append("the site scans could not be counted")
        elif scans["count"] or scans["latest_score"] is not None:
            rows.append({"key": "scans", "label": "Site audits run", "count": scans["count"]})
    return {"rows": rows, "latest_score": scans.get("latest_score"),
            "latest_scan_at": scans.get("latest_at") or "", "errors": errors,
            "measured": not errors}


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------

def section(link, rng: dict, today: date | None = None, gate_: dict | None = None) -> dict | None:
    """The organic block of the aggregate, or None when the client is not
    gated in. ``link`` is the ReportLink row (its ``view`` decides the
    labels); ``rng`` is ``client_view.period_range()``'s answer."""
    today = today or date.today()
    g = gate_ or gate(link.client, link.client_name)
    if not g["gated"]:
        return None
    named = bool((link.view or {}).get("name_products"))
    labels = LABELS_NAMED if named else LABELS
    return {
        "labels": dict(labels),
        "period": {"start": rng["start"].isoformat(), "end": rng["end"].isoformat(),
                   "label": rng["label"]},
        "analytics": _ga4(g, rng, today),
        "search": _gsc(g, rng),
        "work": _work(g, rng),
        "gbp": {"coming_soon": True, "label": labels["gbp"], "note": labels["gbp_note"]},
    }


def public_view(block: dict | None) -> dict | None:
    """The block as the client's page and data.json carry it: the staff
    wording is stripped, so a sentence naming the product or the fix never
    reaches a client's document."""
    if not block:
        return None
    out = {k: v for k, v in block.items()}
    search = dict(block.get("search") or {})
    search.pop("staff_note", None)
    out["search"] = search
    analytics = dict(block.get("analytics") or {})
    analytics.pop("staff_note", None)
    out["analytics"] = analytics
    work = dict(block.get("work") or {})
    work.pop("errors", None)
    out["work"] = work
    return out
