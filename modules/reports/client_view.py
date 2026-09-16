"""What a client's live page shows: one aggregate, built once per period.

``aggregate(link, period)`` reads the client's mapped fact rows for the
period and returns the dict the page, its ``data.json`` and the PDF all
render from. It is the ONLY builder of a client-facing figure, and two rules
hold on everything it returns:

* **Raw spend never leaves this function.** The fact table's ``spend`` is
  read here, handed to ``pricing.client_price()``, and the answer -- the
  client's *Investment*, or nothing -- is what goes out. No key in the
  result carries the raw number, under any name, whether or not
  ``show_spend`` is on; ``staff_totals()`` is the separate, staff-only
  reading of it.
* **Investment appears only when the link says so.** With ``show_spend``
  off there is no money on the page at all, and the word does not appear.
  With it on, a platform ``client_price()`` cannot price shows delivery only
  and an activity row (``reports_markup_missing``) says which platform
  needs a markup, because a figure silently missing from a total is a
  smaller total that reads as the whole.

**The client-facing dimension is the Smart 1 product**, never the
platform. Every bar, row and figure is grouped by ``CampaignMap.product``
(``products.py`` is the catalog), so two platforms sold as one product are
one bar; a mapping with no product falls back to ``PLATFORM_LABELS`` (with
``view_json.platform_labels`` on top) -- product-style names that name no
vendor -- and ``blank_products()`` flags it on the staff page. No key on
the answer names a platform, and ``products.FORBIDDEN`` is swept over the
page, data.json and the PDF by ``test_reports_crossover.py``.

The aggregate is cached fifteen minutes per (token, period, link version):
a client refreshing, the PDF, and the rep checking the link are three reads
of numbers that change once an hour. Per process, which is fine for a
cache; the link's ``updated_at`` is in the key so a settings change is seen
at once on every worker.
"""
from __future__ import annotations

import calendar
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from . import organic, pricing, store, suite_email, youtube

log = logging.getLogger(__name__)

# Smart 1's product-style names per platform: the fallback a campaign is
# shown under when its mapping has NO product, with the link's own
# view_json.platform_labels override on top. None names a vendor -- the
# hard rule products.FORBIDDEN holds the public page to -- and the staff
# page flags any campaign shown under one of these as a mapping to finish.
PLATFORM_LABELS = {
    "ttd": "Streaming TV / Programmatic",
    "audiogo": "Streaming Audio",
    "groundtruth": "Geofencing",
    "stackadapt": "Programmatic Display",
    "google": "Paid Search",
    "bing": "Paid Search (Microsoft)",
    "meta": "Social (Facebook & Instagram)",
    "linkedin": "LinkedIn",
    "tiktok": "Short-Form Video",
    "x": "X",
    "amazon_sa": "Amazon Ads",
    "amazon_dsp": "Streaming TV (Amazon)",
    "suite": "Smart 1 Suite",
}

# Which platforms report a completion, and what a completion is there.
COMPLETION = {"ttd": "video", "stackadapt": "video", "amazon_dsp": "video", "audiogo": "audio"}


def _completion_kind(f: dict) -> str | None:
    """``video`` / ``audio`` / None for one fact row.

    By platform for the platforms that serve one kind of creative and
    nothing else. By the ROW for Google Ads, because one account serves
    search and YouTube alike: a row carries ``completes`` only when the
    native pull read a video campaign, so that row is a video one and a
    search row is not -- and listing the platform in ``COMPLETION`` would
    draw "Video ads completed 0" for a search-only client, the measured
    nought about a product they are not running that the tile's own gate
    exists to refuse.
    """
    kind = COMPLETION.get(f["platform"])
    if kind:
        return kind
    if f["platform"] == "google" and f.get("completes") is not None:
        return "video"
    return None

SMART1_LOGO = ("https://content.app-sources.com/s/30680510049142132/uploads/"
               "Our_Products_/logo-final-cmyk-hz1line-white-9562849.png?format=webp")
SMART1_SITE = "https://smart1marketing.com"

# Past this many days between today and the newest day with a figure, the
# client's page says the figures run through that day rather than
# implying they are current. Two rather than one: every platform here
# reports through yesterday, and the six-hourly native pull adds a few
# hours on top of that.
DATA_STALE_DAYS = 2
CACHE_SECONDS = 15 * 60
_CACHE: dict[tuple, tuple[float, dict]] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

def period_range(period: str, today: date | None = None) -> dict:
    """{key, label, start, end} for a period key: ``mtd`` (default),
    ``last_month``, ``90d``, or a month as ``YYYY-MM``. An unknown key is
    month-to-date rather than an error: the selector is a link a client
    may edit, and a wrong one should show something."""
    today = today or date.today()
    p = (period or "mtd").strip().lower()
    if p == "last_month":
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        start = end.replace(day=1)
        return {"key": p, "label": f"{start:%B %Y}", "start": start, "end": end}
    if p == "90d":
        return {"key": p, "label": "Last 90 days",
                "start": today - timedelta(days=89), "end": today}
    if len(p) == 7 and p[4] == "-":
        try:
            y, m = int(p[:4]), int(p[5:7])
            start = date(y, m, 1)
            end = date(y, m, calendar.monthrange(y, m)[1])
            if start <= today:
                if end > today:
                    end = today
                return {"key": p, "label": f"{start:%B %Y}", "start": start, "end": end}
        except ValueError:
            # Not a real month (2026-13): fall through to month-to-date
            # rather than 500 the client's page over a typed period.
            pass
    start = today.replace(day=1)
    return {"key": "mtd", "label": f"{today:%B %Y} (month to date)",
            "start": start, "end": today}


def month_options(today: date | None = None, months: int = 12) -> list[dict]:
    """The month picker: the last twelve months, newest first."""
    today = today or date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        out.append({"key": f"{y:04d}-{m:02d}", "label": f"{calendar.month_name[m]} {y}"})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def compact(n) -> str:
    """1,420,000 -> 1.42M; 8,930 -> 8,930."""
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 100_000:
        return f"{n / 1000:.0f}K"
    return f"{int(round(n)):,}"


def money(d: Decimal | None) -> str:
    return "" if d is None else f"${d:,.2f}"


def ctr(clicks: int, imps: int) -> str:
    return f"{(clicks / imps * 100):.2f}%" if imps else "—"


def eastern(dt: datetime | None) -> str:
    if not dt:
        return "not yet"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:                       # noqa: BLE001 - no tz database
        dt = dt.astimezone(timezone(timedelta(hours=-5)))
    return f"{dt:%b} {dt.day}, {dt.year}, {dt:%I:%M %p}".replace(" 0", " ") + " ET"


# ---------------------------------------------------------------------------
# The aggregate
# ---------------------------------------------------------------------------

def _safe(label: str, fn, default):
    """One section's failure costs that section, never the page -- the
    seo_client.html safe() wrapper, server-side. The trend, the logo lookup
    and the sync stamp each reach a different table; a page blanked by the
    one that refused is worse than a page missing it."""
    try:
        return fn()
    except Exception:                       # noqa: BLE001
        log.exception("reports client page: %s section failed", label)
        return default


def _labels(view: dict) -> dict:
    labels = dict(PLATFORM_LABELS)
    for p, v in (view.get("platform_labels") or {}).items():
        if v:
            labels[p] = v
    return labels


# Which (link, platform) an unpriced-platform row has been written for
# today. An unpriced platform is a STATE, and a row per cache miss wrote it
# on every build -- hub/google_index.py's rule about logging a state only
# when it changes. Per process, which on two workers is at most twice a
# day rather than once; the alternative is a read of the activity log on
# every client page open.
_MISSING_LOGGED: dict[tuple[str, str], date] = {}


def _log_missing(link, platform: str) -> None:
    stamp = (link.token, platform)
    if _MISSING_LOGGED.get(stamp) == date.today():
        return
    _MISSING_LOGGED[stamp] = date.today()
    try:
        from hub import audit as hub_audit
        hub_audit.log("reports", "reports_markup_missing", actor="system",
                      client=link.client_name or link.client, client_key=link.client,
                      action="reports_markup_missing", platform=platform,
                      detail=f"{PLATFORM_LABELS.get(platform, platform)}: investment is "
                             "on for this client and no markup or CPM is set, so the "
                             "page shows delivery only for it")
    except Exception:                       # noqa: BLE001 - a log line is not the page
        pass


def _product_of(f: dict, labels: dict) -> tuple[str, bool]:
    """(the product a fact row is shown under, whether it was set). A blank
    product falls back to the platform's generic label -- the link's own
    label override first, then PLATFORM_LABELS -- and the staff page flags
    the mapping as unfinished."""
    p = (f.get("product") or "").strip()
    if p:
        return p, True
    return labels.get(f["platform"], f["platform"]), False


def build(link, period: str, today: date | None = None) -> dict:
    """The aggregate, uncached. ``link`` is a ReportLink row.

    The client-facing dimension is the Smart 1 PRODUCT (``products.py``):
    every figure is grouped by the product a campaign is mapped to, and two
    platforms sharing a product are one bar and one row. No key on the
    answer names a platform: ``products``, ``table`` and ``investment``
    carry the product and nothing about what ran it.
    """
    today = today or date.today()
    rng = period_range(period, today)
    view = link.view
    labels = _labels(view)
    hidden = set(view.get("hidden_platforms") or [])
    facts = [f for f in store.facts_for(link.client, rng["start"], rng["end"])
             if f["platform"] not in hidden]

    # Per product; per (product, platform) underneath it for pricing, which
    # is a platform rule; per (product, campaign) for the table.
    prod: dict[str, dict] = {}
    plat: dict[tuple, dict] = {}
    camp: dict[tuple, dict] = {}
    # Smart 1 Suite rows are OUTCOMES (leads, bookings), not delivery: they
    # feed the leads tile and never draw a zero-impression bar, a table row
    # or an investment line.
    suite_leads = sum(int(f["leads"] or 0) for f in facts if f["platform"] == "suite")
    for f in facts:
        if f["platform"] == "suite":
            continue
        product, _set = _product_of(f, labels)
        p = prod.setdefault(product, {"impressions": 0, "clicks": 0, "conversions": Decimal(0),
                                      "completes": 0, "kinds": set()})
        p["impressions"] += f["impressions"]
        p["clicks"] += f["clicks"]
        p["conversions"] += f["conversions"]
        kind = _completion_kind(f)
        if kind:
            p["completes"] += int(f["completes"] or 0)
            p["kinds"].add(kind)
        pp = plat.setdefault((product, f["platform"]), {"_raw": Decimal(0), "impressions": 0})
        pp["_raw"] += f["spend"]
        pp["impressions"] += f["impressions"]
        key = (product, f["platform"], f["campaign_id"])
        name = f.get("display_name") or f["campaign_name"] or f["campaign_id"]
        c = camp.setdefault(key, {"product": product, "campaign": name, "impressions": 0,
                                  "clicks": 0, "conversions": Decimal(0), "completes": 0,
                                  "kind": kind})
        if c["kind"] is None and kind:
            c["kind"] = kind
        c["impressions"] += f["impressions"]
        c["clicks"] += f["clicks"]
        c["conversions"] += f["conversions"]
        c["completes"] += int(f["completes"] or 0)
        if f.get("display_name"):
            c["campaign"] = f["display_name"]

    show = bool(link.show_spend)
    products, total_inv, unpriced = [], Decimal(0), []
    for product, t in sorted(prod.items(), key=lambda kv: -kv[1]["impressions"]):
        row = {"product": product, "label": product,
               "impressions": t["impressions"], "clicks": t["clicks"],
               "ctr": ctr(t["clicks"], t["impressions"]),
               "conversions": int(t["conversions"]),
               "completes": t["completes"] if t["kinds"] else None,
               "completion_kind": ("audio" if t["kinds"] == {"audio"} else "video")
               if t["kinds"] else None}
        if show:
            # A product is priced per platform underneath it, because the
            # rule is a platform's; one unpriced platform makes the product
            # delivery only rather than a smaller figure that reads as the
            # whole.
            inv, whole = Decimal(0), True
            for (pr, platform), pp in plat.items():
                if pr != product:
                    continue
                price = pricing.client_price(platform, pp["_raw"], pp["impressions"], link)
                if price is None:
                    whole = False
                    _log_missing(link, platform)
                else:
                    inv += price
            if whole:
                total_inv += inv
                row["investment"] = money(inv)
            else:
                unpriced.append(product)
                row["investment"] = None
        products.append(row)
    top = products[0]["impressions"] if products else 0
    for row in products:
        row["share"] = round(row["impressions"] / top * 100) if top else 0

    # Tiles, in the order the work order gives.
    imps = sum(t["impressions"] for t in prod.values())
    clicks = sum(t["clicks"] for t in prod.values())
    tiles = [{"key": "impressions", "label": "People reached (impressions)",
              "value": imps, "display": compact(imps)},
             {"key": "clicks", "label": "Clicks to your site",
              "value": clicks, "display": compact(clicks)}]
    # Gated on the platforms with rows IN THIS PERIOD, not on every
    # platform ever mapped: a video campaign that ran last year must not
    # draw "Video ads completed 0" this month, which reads as a measured
    # nought about a product the client is not running.
    period_platforms = {f["platform"] for f in facts}
    kinds = {k for k in (_completion_kind(f) for f in facts) if k}
    if kinds:
        completes = sum(t["completes"] for t in prod.values())
        label = ("Listens" if kinds == {"audio"} else
                 "Video ads completed" if kinds == {"video"} else
                 "Video & audio ads completed")
        tiles.append({"key": "completes", "label": label, "value": completes,
                      "display": compact(completes)})
    # Store visits: the figure a geofencing buy is bought for, carried in
    # extras under its own name by the GroundTruth pull and the CSV door.
    # Gated on a row that actually carries one -- a geofencing row from a
    # provider table that reports no visits must not draw "Store visits 0",
    # the measured nought the completes tile's own gate refuses.
    visited = [f for f in facts if f["platform"] == "groundtruth"
               and isinstance(f.get("extras"), dict) and f["extras"].get("visits") is not None]
    if visited:
        visits = sum(int(f["extras"].get("visits") or 0) for f in visited)
        tiles.append({"key": "visits", "label": "Store visits", "value": visits,
                      "display": compact(visits)})
    if "suite" in period_platforms:
        tiles.append({"key": "leads", "label": "Leads & bookings", "value": suite_leads,
                      "display": compact(suite_leads)})
    if show:
        tiles.append({"key": "investment", "label": "Investment",
                      "value": str(total_inv), "display": money(total_inv)})

    table = []
    for c in sorted(camp.values(), key=lambda r: -r["impressions"]):
        table.append({"product": c["product"], "label": c["product"],
                      "campaign": c["campaign"], "impressions": c["impressions"],
                      "clicks": c["clicks"], "ctr": ctr(c["clicks"], c["impressions"]),
                      "conversions": int(c["conversions"]),
                      "completes": c["completes"] if c["kind"] else None})

    synced = _safe("synced", store.last_synced_at, None)
    # The newest day any figure on this page comes from. "Updated" below
    # is when a sync last WROTE, which stays fresh while the provider
    # restates last week -- so a feed that stopped delivering new days
    # read as current. The day the figures run through is the honest
    # stamp, and past DATA_STALE_DAYS the page says so in words.
    data_through = max((f["date"] for f in facts), default=None)
    lag = (today - data_through).days if data_through else None
    out = {
        "client_name": link.client_name or link.client,
        "period": {"key": rng["key"], "label": rng["label"],
                   "start": rng["start"].isoformat(), "end": rng["end"].isoformat()},
        "periods": [{"key": "mtd", "label": "This month"},
                    {"key": "last_month", "label": "Last month"},
                    {"key": "90d", "label": "Last 90 days"}],
        "months": month_options(today),
        "tiles": tiles,
        "products": products,
        "trend": _safe("trend", lambda: trend(link, today, hidden), []),
        "table": table,
        "updated": store.iso(synced),
        "updated_et": eastern(synced),
        "data_through": data_through.isoformat() if data_through else None,
        "data_through_label": f"{data_through:%B} {data_through.day}" if data_through else "",
        "data_lag_days": lag,
        "data_stale": bool(lag is not None and lag > DATA_STALE_DAYS),
        "logo_url": view.get("logo_url") or _safe("logo", lambda: _client_logo(link), ""),
        "smart1_logo": SMART1_LOGO,
        "smart1_site": SMART1_SITE,
        "rep_name": view.get("rep_name") or "",
        "rep_email": view.get("rep_email") or "",
        "has_data": bool(facts),
        # The organic-search section, for a client with an SEO product and a
        # linked analytics property; None (and left off the page) otherwise.
        # organic.public_view strips the staff wording so nothing naming a
        # product or a fix reaches the client's document.
        "organic": _safe("organic", lambda: organic.public_view(
            organic.section(link, rng, today)), None),
        # The YouTube channel section, for a client with a live video or
        # social product and a confirmed channel; None (and left off the
        # page) otherwise, and None while the channel has no reading yet.
        "youtube": _safe("youtube", lambda: youtube.public_view(
            youtube.section(link, today)), None),
        # The email campaigns section, for a client with a live email
        # product and a linked Suite sub-account; None (and left off the
        # page) otherwise, and None while nothing sent has been read.
        "email": _safe("email", lambda: suite_email.public_view(
            suite_email.section(link, today)), None),
    }
    if show:
        out["investment"] = {"total": money(total_inv), "delivery_only": unpriced}
    return out


def blank_products(client: str) -> list[dict]:
    """The mapped campaigns of a client whose product is blank -- shown on
    the client's page under a generic label, and flagged on the staff page
    as a mapping nobody finished."""
    return [m for m in store.mapped_campaigns_for(client) if not m.get("product_set")]


def trend(link, today: date, hidden: set) -> list[dict]:
    """Impressions per month for the twelve months ending this one."""
    y, m = today.year, today.month
    months = []
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()
    start = date(months[0][0], months[0][1], 1)
    totals = {ym: 0 for ym in months}
    for f in store.facts_for(link.client, start, today):
        if f["platform"] in hidden:
            continue
        ym = (f["date"].year, f["date"].month)
        if ym in totals:
            totals[ym] += f["impressions"]
    # The last month is the one in progress and is marked as such: drawn
    # as it stands, never projected, but visibly distinct -- a client opening
    # the page on the 3rd would otherwise read a partial month as reach
    # collapsing.
    return [{"month": f"{yy:04d}-{mm:02d}", "label": calendar.month_abbr[mm],
             "impressions": totals[(yy, mm)], "display": compact(totals[(yy, mm)]),
             "partial": (yy, mm) == (today.year, today.month)}
            for yy, mm in months]


def _client_logo(link) -> str:
    """The client's stored logo from the Hub's brand cache, or "" -- never a
    logo observed off a page, the hub/client_brand rule: a wrong logo on a
    client-facing document is worse than none."""
    try:
        from hub import client_brand, client_key as ck
        name = link.client_name or ck.key_label(link.client)
        domain = link.client[2:] if link.client.startswith("d:") else ""
        kit = client_brand.brand_kit(name, domain)
        for logo in kit.get("logos") or []:
            if logo.get("url"):
                return logo["url"]
    except Exception:                       # noqa: BLE001 - no brand cache here
        pass
    return ""


def aggregate(link, period: str, today: date | None = None) -> dict:
    """``build()`` behind a fifteen-minute cache per (token, period, version)."""
    # The pricing rule and the client's mappings are in the key, not only
    # the link's own version: a markup saved on /reports/markup, or a
    # display name corrected on the staff page, must reach the client's
    # page on BOTH workers, and forget() below can only empty this one.
    key = (link.token, period_range(period, today)["key"], store.iso(link.updated_at),
           store.pricing_version(), store.mapping_version(link.client))
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = build(link, period, today)
    with _LOCK:
        _CACHE[key] = (now, data)
        if len(_CACHE) > 500:
            oldest = sorted(_CACHE, key=lambda k: _CACHE[k][0])[:250]
            for k in oldest:
                _CACHE.pop(k, None)
    return data


def pdf_bytes(link, period: str, today: date | None = None) -> bytes:
    """``client_pdf.build(aggregate(...))`` behind the same cache as the
    aggregate: the same key with a marker on the end, the same fifteen
    minutes, and dropped by the same ``forget()``.

    The PDF used to be rebuilt on every request while the page beside it
    was served from cache -- a client refreshing the download was a
    reportlab render each time, on the one route a stranger can hit with
    no login. The key is the aggregate's, so a markup saved on the other
    worker or a display name corrected on the staff page reaches the
    document exactly when it reaches the page; nothing here can serve a
    PDF of numbers the page has stopped showing.
    """
    from . import client_pdf
    key = (link.token, period_range(period, today)["key"], store.iso(link.updated_at),
           store.pricing_version(), store.mapping_version(link.client), "pdf")
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = client_pdf.build(aggregate(link, period, today))
    with _LOCK:
        _CACHE[key] = (now, data)
    return data


def forget(token: str) -> None:
    with _LOCK:
        for k in [k for k in _CACHE if k[0] == token]:
            _CACHE.pop(k, None)


# ---------------------------------------------------------------------------
# Staff-only
# ---------------------------------------------------------------------------

def staff_totals(client: str, start: date, end: date, link=None) -> list[dict]:
    """Raw spend by platform beside what the client would be billed. Staff
    only -- the internal view -- and never handed to a public route."""
    plat: dict[str, dict] = {}
    for f in store.facts_for(client, start, end):
        p = plat.setdefault(f["platform"], {"raw": Decimal(0), "impressions": 0, "clicks": 0})
        p["raw"] += f["spend"]
        p["impressions"] += f["impressions"]
        p["clicks"] += f["clicks"]
    out = []
    for p, t in sorted(plat.items(), key=lambda kv: -kv[1]["raw"]):
        rule = pricing.rule_for(p, link)
        out.append({"platform": p, "label": PLATFORM_LABELS.get(p, p),
                    "raw_spend": t["raw"], "impressions": t["impressions"],
                    "clicks": t["clicks"],
                    "client_price": pricing.client_price(p, t["raw"], t["impressions"], link),
                    "rule": rule})
    return out


def pacing(client: str, today: date | None = None) -> list[dict]:
    """Each of this client's budget lines against its flight, as the pacing
    board computes it -- pacing.compute_line(), through pacing.compute(),
    and NOT a second engine here.

    The first version of this function summed the client's whole spend per
    line (filtered by platform only, never by product) and ignored the
    flight proration, so with two lines on one client the board said
    "Paid Search 0.90 on pace" while this page said "3.90 over pace" about
    the same day; each screen was internally consistent, which is why it
    survived. Two readings of one question drift the day either is edited,
    so this is an adapter: the template's field names over the board's own
    rows. Staff only.
    """
    from . import pacing as pacing_mod
    today = today or date.today()
    out = []
    for r in pacing_mod.compute(today, client=client):
        pace = float(r["pace"]) if r["pace"] is not None else None
        out.append({**r,
                    "spent": r["actual_to_date"], "expected": r["expected_to_date"],
                    "ratio": pace or 0.0, "pct": min(200, round((pace or 0.0) * 100)),
                    "days_in_month": r["days_in_period"],
                    "platform_label": (PLATFORM_LABELS.get(r["platform"], r["platform"])
                                       if r["platform"] else "")})
    return out
