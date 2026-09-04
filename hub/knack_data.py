"""Read-only access to private Knack data and an optional private fallback.

Products read live through `hub/knack_products.py` (object_135) and websites
through `hub/knack_websites.py` (object_153). When Knack cannot be reached,
both may fall back to `products.json` and `websites.json` in the directory set
by `CLIENTS_DATA_DIR`. That directory must be a private mounted volume outside
the source checkout; real client or billing exports are never committed.

Fallback files are loaded lazily and cached until their mtime changes, so an
operator can replace a mounted snapshot without restarting the app.

`campaigns.json` and `live_products.json` used to sit beside them and are
gone: 7,854 rows and 2.1 MB of the first, 96 KB of the second, and not one
reference to either anywhere in the repo — no reader, and for campaigns not
even a `campaigns()` function. They were described in CLAUDE.md as stale,
which implied a refresh would fix them; nothing would.
"""
import json
import os
import datetime as _dt
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.abspath(
    os.environ.get("CLIENTS_DATA_DIR")
    or os.path.join(_ROOT, "clients_app", "data")
)

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _load(name: str):
    path = os.path.join(BASE, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    with _lock:
        _cache[name] = (mtime, data)
    return data


def _records(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("records", "rows", "data", "items"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
        # dict of lists? take the longest list value
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            longest = max(lists, key=len)
            return [r for r in longest if isinstance(r, dict)]
    return []


def products() -> list[dict]:
    return _records(_load("products.json"))


def products_error() -> str:
    """Why the product rows came back empty, or "" if a source answered.

    An empty product list is not an answer this Hub can have: the book is ten
    thousand rows and every client report on `/qa` is built by grouping them.
    `_load()` swallows `OSError` and returns None, so a missing, unreadable or
    malformed export yields `[]` — indistinguishable, to a caller, from a book
    with nobody on it. Six reports then rendered a clean empty table and
    `hub/report_cache.py` stored it as the day's answer: "every client has a
    dashboard, nobody has lapsed, nobody is missing Analytics, nobody
    churned", frozen until tomorrow, on a source that was never read.

    It asks **whichever source answered** rather than the export alone. Those
    were the same question while `/qa` read the export directly; now that it
    reads `_product_source()`, asking the export would report a perfectly good
    live pull as unmeasurable on any deployment whose private fallback happens
    to be absent — refusing to measure on the strength of a file nothing read.

    Told apart from a genuinely empty file because they are different things
    to do about it, and returned as a sentence rather than a bool so the
    report can print the reason it is not measured.
    """
    rows, source, _age = _product_source()
    if source == "knack":
        return ""
    if _load("products.json") is None:
        return ("the products export could not be read, so this is not "
                "measured — which is not the same as there being nothing "
                "to report")
    if not rows:
        return ("the products export was read and holds no rows at all, "
                "which is a source this report cannot use rather than a "
                "book with nobody on it")
    return ""


def products_note(source: str, age_minutes: int | None) -> str:
    """Which source answered, in one sentence, so no screen words it its own.

    Lives here rather than in `hub/seo.py`, which is where it was written and
    whose own comment already said the wording was knack_data's: the SEO list,
    Client 360 and every client report on `/qa` describe the same two sources,
    and two descriptions of one staleness is how two screens come to disagree
    about whether a number can be trusted.
    """
    return (f"Live from Knack, {age_minutes} min old." if source == "knack"
            else "From the private fallback export — verify its snapshot "
                 "date before relying on it.")


def export_websites() -> list[dict]:
    """The private fallback websites export, exactly as it is on disk.

    Kept under its own name because one caller genuinely wants *the export*
    rather than the current truth: `summary()` measures the dashboard's
    scorecard against the export's own period and its thisM / lastM flags, and
    a live site list folded into that would compare two things measured
    differently at the two ends — the failure the whole trends section is
    written about. Everything else wants the live registry; see websites().
    """
    return _records(_load("websites.json"))


def _website_source() -> tuple[list[dict], str, str]:
    """(rows, source, error) — the website records, live if we can get them.

    The same argument `_product_source()` makes, one object later. The client
    registry, the SEO page's website matching, Client 360's website cards and
    the website search all read this, and all of them were reading a 610-row
    fallback JSON refreshed out of band — so a site added in
    Knack last week was invisible to every client picker in the Hub, silently,
    because a short list looks exactly like a complete one. Meanwhile
    `hub/knack_websites.py` has been reading the same object live for the
    domain record, the renewals calendar and the orphan list: the Hub held a
    live answer and a stale one, and the load-bearing readers took the stale.

    **The export stays as the fallback, and a failed pull never empties it.**
    Stale beats empty: a client record showing no website reads as "they have
    none" rather than "we could not reach Knack", which is the confident wrong
    answer this codebase keeps having to undo.
    """
    try:
        from hub import knack_websites
    except Exception as exc:                            # noqa: BLE001
        return export_websites(), "export", f"{type(exc).__name__}"
    try:
        live = knack_websites.rows()
    except Exception as exc:                            # noqa: BLE001
        return export_websites(), "export", f"{type(exc).__name__}"
    if not live:
        # Not configured, or the pull failed and knack_websites swallowed it.
        # Either way it is named rather than passed off as an empty registry.
        return export_websites(), "export", knack_websites.last_error()
    return [website_row_from_live(r) for r in live], "knack", ""


_WEB_CACHE: dict = {"rows": None, "source": "", "at": 0.0}
_WEB_CACHE_SECONDS = 60


def websites() -> list[dict]:
    """Every website record, live where Knack will answer.

    Mapped into the export's own field names so the eight call sites reading
    `name` / `domain` / `liveUrl` / `platform` need no edit — one shape, so a
    reader cannot tell which source answered and cannot come to depend on one.
    `websites_source()` is how a *screen* says which it was.

    Cached for a minute on top of `knack_websites`' own cache, because
    `seo._client_websites()` calls this three times in one function and the
    mapping is 600-odd dicts each time.
    """
    import time
    now = time.time()
    if _WEB_CACHE["rows"] is not None and now - _WEB_CACHE["at"] < _WEB_CACHE_SECONDS:
        return _WEB_CACHE["rows"]
    rows, source, _err = _website_source()
    _WEB_CACHE.update({"rows": rows, "source": source, "at": now})
    return rows


def websites_source() -> str:
    """"knack" or "export" — which answered the last read. For screens only."""
    if _WEB_CACHE["rows"] is None:
        websites()
    return _WEB_CACHE["source"]


def website_row_from_live(rec: dict, domain: str = "") -> dict:
    """One live object_153 record in the export's field names.

    The one mapping. It was written inside `_attachment_only_websites()` for
    the attachment path and is read from both now — two descriptions of "the
    same record in the other shape" is how one of them comes to carry a field
    the other does not.

    `active`, `hmFreq`, `notes`, `manager`, `created` and `domainCost` are
    deliberately **absent** rather than invented: object_153 does not publish
    them, and a False `active` here would read as a dead site on every row.
    That is why `summary()` reads `export_websites()` — it is the only caller
    that needs them, and it needs them measured the same way at both ends.
    """
    dom = str(domain or rec.get("domain") or "").strip().lower()
    return {
        "id": rec.get("id", ""),
        "name": rec.get("client") or rec.get("client_name") or rec.get("organization") or dom,
        "domain": dom,
        "liveUrl": rec.get("production_url") or (("https://" + dom) if dom else ""),
        "platform": rec.get("platform", ""),
        "status": rec.get("client_status", ""),
        "hm": rec.get("hm_fee", 0),
        "hmMonthly": rec.get("hm_fee", 0),
        "partner": rec.get("media_partner", ""),
        "ga": rec.get("ga_account", ""),
        "gtm": rec.get("gtm_account", ""),
        "registrar": rec.get("registrar", ""),
        "domainPurchased": rec.get("domain_bought", ""),
    }


def data_age_hours() -> float | None:
    """How long ago products.json was written to disk.

    That is the *file's* age, and in a Docker deploy every file is written at
    image build time — so this measures the last deploy, not the last data
    refresh, and reads "fresh" for an export generated months earlier. The
    honest staleness signal is the export's own `thisMonth` against the
    calendar; see `export_stale` in summary().
    """
    import time
    try:
        mtime = os.path.getmtime(os.path.join(BASE, "products.json"))
    except OSError:
        return None
    return (time.time() - mtime) / 3600.0


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0


# Statuses that mean the row is over, whatever its dates say. Everything else
# is judged on its term. These two are unambiguous: 8,400 Complete rows and 70
# Revised rows, not one of which covers today.
_FINISHED_STATUSES = {"complete", "revised"}


def is_running(rec: dict) -> bool:
    """Is this insertion order delivering right now?

    The test used to be `status == "live"`, and it missed about a third of the
    work actually running. Knack's status vocabulary is wider than that:
    Assigned, Scheduled, Pending Assets, In Process, Needs Cancelled, Paused
    and Cancelled rows sit inside their dates and bill this month. A client
    whose only current products were two of those reported "0 products, $0"
    beside an end date — a row that says the client is both ending and has
    nothing, which is the fastest way to make a renewal queue stop being
    trusted.

    So either signal counts: the term covers today, or somebody has marked it
    Live. Deliberately a union rather than a swap. Judging on dates alone
    dropped 173 rows that Knack still calls Live but whose end date has passed
    — month-to-month arrangements, and IOs nobody has closed out — and that
    took Hern Marine from four products and $6,500 to "0 products, $0 beside
    an end date", which is the very row this was meant to remove. Widening a
    definition can only add work to a queue; narrowing it hides work, and the
    hidden kind is the expensive kind.

    Complete and Revised never count — finished and superseded — so a stray
    date on one cannot resurrect it.
    """
    status = str(rec.get("status", "")).strip().lower()
    if status in _FINISHED_STATUSES:
        return False
    if status == "live":
        return True

    from hub import dates as _dates
    start = _dates.to_date(rec.get("start"))
    end = _dates.to_date(rec.get("end"))
    if not (start and end):
        return False
    return start <= _dt.date.today() <= end


# A revised IO is superseded: the replacement carries the real numbers, so
# counting both double-counts the month. Complete is deliberately NOT here --
# see ran_in_month() for why it is a pass there and a fail in is_running().
_SUPERSEDED_STATUSES = {"revised"}


def ran_in_month(rec: dict, mstart, mend) -> bool:
    """Did this insertion order deliver during the month [mstart, mend]?

    The sibling of `is_running()`, and it lives here rather than in the report
    that asks it because the two were separately written and separately drifted:
    `qa._active_in_month()` tested `status in ("live", "complete")`, which is
    the narrow test this module's own docstring says "missed about a third of
    the work actually running". On the real export that hid **147 rows and
    $140,439 a month** from August's scorecard, and took two salespeople --
    Debi Greenfield and Kim Marshall -- off it entirely, while every other
    report on the same page counted their work. Two definitions of "running"
    on one page is the `/api/db/structure` versus `/api/integrity` trap; they
    are neighbours now so the next edit to either has to look at both.

    Three deliberate differences from `is_running()`, each because "delivering
    **today**" and "delivered in **March**" are different questions:

      * **Complete counts here.** It is excluded there because a finished row
        cannot cover today; a row that ran January to June plainly delivered in
        March, and dropping it empties every historical month.
      * **Live does not override the dates.** There it is a union, because an
        IO nobody has closed out is still delivering. Asked about a month, a
        Live row with no term would land in all twelve.
      * **A row with no dates at all is not in any month.** It cannot be
        placed, and the export carries 33 of them (31 Open) that would
        otherwise be counted every month of the year.

    What it keeps is the tolerance: anything whose term covers the month counts
    unless it was superseded. That includes Cancelled, because those rows sit
    inside their dates and bill -- the reading `is_running()` already applies,
    and the reason the scorecards now agree with Active Clients. The limit
    worth knowing is that Knack publishes no cancellation *date*, so an IO
    cancelled mid-term is counted for every month its term spans; the
    alternative is dropping 73 live rows worth $85,105 a month that the rest
    of the Hub counts.
    """
    status = str(rec.get("status", "")).strip().lower()
    if status in _SUPERSEDED_STATUSES:
        return False

    from hub import dates as _dates
    start = _dates.to_date(rec.get("start"))
    end = _dates.to_date(rec.get("end"))
    if not (start or end):
        return False
    if start and start > mend:
        return False
    if end and end < mstart:
        return False
    return True


# The old name, kept because renaming a predicate across six modules in the
# same change as redefining it makes the redefinition impossible to review.
_is_live = is_running


def _period_label(yyyymm) -> str:
    s = str(yyyymm or "")
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if len(s) == 6 and s.isdigit():
        return f"{months[int(s[4:6])]} {s[:4]}"
    return s


# The one file in this module that is not re-readable from anywhere. Knack
# reports what is true today; it has no record of what was true last March, so
# a past period here exists in this file and nowhere else and cannot be
# recomputed at any price. It reads like a cache — it sits next to the Knack
# JSONs and is rewritten on every dashboard load — which is exactly why it is
# worth saying that it is not one. Through hub.jsonstore into the backup.
def _history_path() -> str:
    from . import jsonstore
    return os.path.join(jsonstore.data_root(), "hub-metrics-history.json")


# Metrics worth trending. Snapshotted per Knack period so the dashboard can say
# which way each one moved, rather than only what it is today.
TRENDED = ("clients_live", "live_products", "live_budget_monthly",
           "websites_active", "hm_monthly", "estimated_total_monthly")


def _current_period() -> str:
    """The month the Hub is in, read from the clock.

    This used to be products.json's `thisMonth`, and that is a fallback
    export refreshed out of band — it has carried one value since the day it was
    generated. Keying the history on it meant every dashboard load wrote
    today's numbers into that same bucket, a second bucket could never appear,
    and so every trend rendered "– vs last mo – vs last yr" for ever: a
    comparison that looks like history and can never resolve into any.

    The export's own month still labels the export-derived counts below. It is
    a fact about the export, not about today, and the two are only the same
    month by coincidence.
    """
    return _dt.date.today().strftime("%Y%m")


def _period_minus(period: str, months: int) -> str:
    """The YYYYMM that many months before `period`."""
    try:
        y, m = int(str(period)[:4]), int(str(period)[4:6])
    except (TypeError, ValueError):
        return ""
    total = y * 12 + (m - 1) - months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _snapshot(period: str, values: dict) -> dict:
    """Record this period's metrics and return the whole history.

    Written every time the dashboard is read, so the current period is always
    up to date and past periods keep the last value they were given.
    """
    path = _history_path()
    from . import jsonstore
    hist = jsonstore.read_json(path, default={})
    if not isinstance(hist, dict):
        hist = {}
    if period:
        entry = hist.get(period) or {}
        entry.update({k: v for k, v in values.items() if v is not None})
        hist[period] = entry
        try:
            jsonstore.write_json(path, hist)
        except OSError:
            pass
    return hist


# ---------------------------------------------------------------------------
# Why there is no month-over-month comparison on the scorecard
# ---------------------------------------------------------------------------
#
# There was one, and it was removed rather than fixed, because the arithmetic
# under it could not be made honest at this size.
#
# The snapshot history above can only start when it is switched on: the first
# reading is taken the month the Hub is opened, so on a new deployment last
# month has no bucket and the same month last year does not arrive for twelve.
# The obvious way round that is to rebuild the missing months from the export
# — every insertion order has a start date, an end date and a monthly rate, so
# which IOs were billing in March is arithmetic. That was built, and it
# reproduced Knack's own `thisM` / `lastM` flags exactly.
#
# It still had to go. `is_running` — the definition behind every headline
# number on that page — is deliberately a *union*: an IO counts if its term
# covers today **or** Knack still calls it Live, which takes in about 140
# month-to-month rows whose end date has passed and which nobody has closed
# out. A term rebuild cannot see those, so the rebuilt month and the number
# printed above it were measured differently, and no reader could reproduce
# the percentage from the two figures on the card. A number nobody can check
# is worse than no number, and a red 30% on the CEO's dashboard is the worst
# place to learn that.
#
# So the cards carry the headline figures alone. `_snapshot()` still runs on
# every load, because a reading taken this month is the only thing that can
# ever produce a comparison measured the same way at both ends — and it costs
# one small write. When there are two of them, a comparison can come back
# without inventing anything.


def _website_movement(period, websites_active) -> tuple:
    """(movement, the month it is measured from).

    Websites carry no month-over-month fields, so the Hub snapshots the active
    count each month and compares. It compares against the most recent earlier
    month it holds, which is not always last month — so it returns which one,
    and the card names it. Saying "vs last month" over a gap of four is how a
    number nobody can reproduce ends up on a dashboard.
    """
    path = _history_path()
    from . import jsonstore
    hist = jsonstore.read_json(path, default={})
    if not isinstance(hist, dict):
        hist = {}
    key = str(period or "")
    if key:
        entry = hist.get(key) or {}
        entry["websites_active"] = websites_active
        hist[key] = entry
        try:
            jsonstore.write_json(path, hist)
        except OSError:
            pass
    prev_keys = sorted(k for k in hist if k.isdigit() and k < key)
    if not prev_keys:
        return None, ""
    prev_key = prev_keys[-1]
    prev = hist[prev_key].get("websites_active")
    if not isinstance(prev, (int, float)):
        return None, ""
    return websites_active - prev, _period_label(prev_key)


def month_over_month(prods: list[dict]) -> dict:
    """Per-client budget totals for this month vs last month, from the
    lastM/thisM active flags Knack exports on every IO row."""
    this_by, last_by = {}, {}
    for r in prods:
        client = str(r.get("client", "")).strip()
        if not client:
            continue
        m = _num(r.get("monthly"))
        if r.get("thisM"):
            this_by[client] = this_by.get(client, 0.0) + m
        if r.get("lastM"):
            last_by[client] = last_by.get(client, 0.0) + m
    new = sum(1 for c in this_by if c not in last_by)
    lost = sum(1 for c in last_by if c not in this_by)
    increased = sum(1 for c, v in this_by.items() if c in last_by and v > last_by[c] + 0.5)
    decreased = sum(1 for c, v in this_by.items() if c in last_by and v < last_by[c] - 0.5)
    return {"new": new, "lost": lost, "increased": increased, "decreased": decreased}


def export_state() -> dict:
    """The month the private fallback products export was generated for.

    One place decides what "stale" means, because two things ask: the
    dashboard, which labels its month-over-month counts with it, and
    `hub/housekeeping.py`, which lists the export as something to regenerate.
    A second copy of the comparison would let the scorecard and the
    Diagnostics row disagree about whether the export is current, with nothing
    on either screen saying which to believe.

    `_load` is cached on the file's mtime, so this is a dictionary lookup on
    every call after the first.
    """
    raw = _load("products.json")
    period = str(raw.get("thisMonth") or "") if isinstance(raw, dict) else ""
    current = _current_period()
    return {
        "period": period,
        "label": _period_label(period),
        "current": current,
        "current_label": _period_label(current),
        # An export with no month in it is neither stale nor current: the
        # caller is told there is no period rather than handed a False.
        "stale": bool(period and period != current),
    }


def summary() -> dict:
    raw = _load("products.json")
    prods = products()
    # The EXPORT, deliberately, not the live registry. Every figure below is
    # measured against the export's own period and its thisM / lastM flags,
    # and `_active()` reads an `active` field only the export carries. A live
    # list folded in here would compare two things measured differently at the
    # two ends and report 0 active websites and $0 of H&M billing on the
    # dashboard — arithmetic no reader could reproduce, which is the failure
    # the whole trends section of this file exists to undo.
    webs = export_websites()

    live = [r for r in prods if _is_live(r)]
    live_clients = {str(r.get("client", "")).strip() for r in live if r.get("client")}
    all_clients = {str(r.get("client", "")).strip() for r in prods if r.get("client")}
    live_budget = sum(_num(r.get("monthly")) for r in live)

    def _active(w):
        a = w.get("active")
        if isinstance(a, bool):
            return a
        return str(w.get("status", "")).strip().lower() == "active"

    active_sites = [w for w in webs if _active(w)]
    hm_monthly = sum(_num(w.get("hmMonthly")) for w in active_sites)

    # Two different months, and conflating them is what broke the trends.
    # `period` is now — what the snapshot history is keyed on. `export_period`
    # is the month products.json was generated for, which is what its lastM /
    # thisM flags describe and all the new/lost/up/down counts are measured in.
    export = export_state()
    period = export["current"]
    export_period = export["period"]
    export_prev = str(raw.get("lastMonth") or "") if isinstance(raw, dict) else ""
    # Recorded even though nothing renders a comparison today: a reading of
    # this month is the only thing that can ever produce one measured the same
    # way at both ends, and it cannot be taken retrospectively.
    try:
        _snapshot(period, {
            "clients_live": len(live_clients),
            "live_products": len(live),
            "live_budget_monthly": round(live_budget),
            "websites_active": len(active_sites),
            "hm_monthly": round(hm_monthly),
            "estimated_total_monthly": round(live_budget + hm_monthly),
        })
    except Exception:  # noqa: BLE001 — never break the dashboard on history I/O
        pass
    mom = month_over_month(prods)
    try:
        movement, movement_from = _website_movement(period, len(active_sites))
    except Exception:  # noqa: BLE001 — never break the dashboard on history I/O
        movement, movement_from = None, ""

    return {
        "clients_total": len(all_clients),
        "clients_live": len(live_clients),
        "live_products": len(live),
        "live_budget_monthly": round(live_budget),
        "websites_total": len(webs),
        "websites_active": len(active_sites),
        "hm_monthly": round(hm_monthly),
        "estimated_total_monthly": round(live_budget + hm_monthly),
        "new_customers": mom["new"],
        "lost_customers": mom["lost"],
        "increased_customers": mom["increased"],
        "decreased_customers": mom["decreased"],
        "website_movement": movement,
        "website_movement_from": movement_from,
        "this_period": _period_label(export_period),
        "last_period": _period_label(export_prev),
        # The month-over-month counts above come from the export's own flags,
        # so they describe the export's month — not necessarily this one. When
        # the export is behind the calendar they are history, and the card has
        # to say so rather than presenting last quarter's movement as today's.
        "export_stale": export["stale"],
        "period": _period_label(period),
        "data_age_hours": data_age_hours(),
    }


CREATIVE_EXCLUDE = ("sem", "website seo", "listings", "email blast")



# Creative links are recognised by the shape of the URL, not by a `kind` field.
#
# That field means two different things depending on where the row came from:
# in the committed export `kind` is the link type (gdrive, pdf, dropbox), but
# in the live rows from hub.knack_products it is the *product* type ("OTT",
# "Paid Search"). Filtering on it therefore worked against the export and, once
# products were read live, let every row through — and the `url` on a live row
# is the click-thru to the client's own website, not a piece of creative. The
# report would have counted landing pages as artwork.
#
# A Drive/Dropbox/PDF address is unmistakable; a client's homepage is not. So
# the URL decides.
_CREATIVE_HOSTS = ("drive.google.com", "docs.google.com", "dropbox.com",
                   "box.com", "wetransfer.com", "cloudinary.com",
                   "sharepoint.com", "onedrive.live.com", "vimeo.com",
                   "youtube.com", "youtu.be")
_CREATIVE_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4",
                 ".mov", ".psd", ".ai", ".zip", ".eps", ".tif", ".tiff")


def creative_kind(url: str, declared: str = "") -> str:
    """The link type, or "" when this is not a creative link.

    `declared` is honoured only when it is one of the export's own link types;
    a product name arriving in that slot is ignored rather than trusted.
    """
    u = str(url or "").strip().lower()
    if not u.startswith(("http://", "https://")):
        return ""
    d = str(declared or "").strip().lower()
    if d in ("gdrive", "gdoc", "pdf", "dropbox", "file", "image", "video"):
        return d
    for host in _CREATIVE_HOSTS:
        if host in u:
            return "gdrive" if "google.com" in host else host.split(".")[0]
    path = u.split("?")[0]
    for ext in _CREATIVE_EXT:
        if path.endswith(ext):
            return "pdf" if ext == ".pdf" else "file"
    return ""


def _creative_items(prod_records: list[dict]) -> list[dict]:
    """Creative file links (PDF / Drive / Dropbox / file) grouped for display,
    newest year first — mirrors the Clients module's creative section."""
    seen = set()
    items = []
    for r in prod_records:
        # All four External Creative Link fields, not just the first.
        candidates = [u for u in (r.get("creative_urls") or []) if u]             or [r.get("url") or r.get("creative_url")]
        url = next((u for u in candidates if creative_kind(u, r.get("kind"))), None)
        if not url:
            continue
        kind = creative_kind(url, r.get("kind"))
        pname = str(r.get("product", "")).lower()
        if any(x in pname for x in CREATIVE_EXCLUDE):
            continue
        key = (r.get("io"), url)
        if key in seen:
            continue
        seen.add(key)
        ts = str(r.get("ts") or "")
        year = ts[:4] if len(ts) >= 4 and ts[:4].isdigit() else str(r.get("start", ""))[-4:]
        item = {
            "year": year,
            "product": r.get("product"),
            "campaign": r.get("campaign"),
            "io": r.get("io"),
            "url": url,
            "kind": kind,
            "start": r.get("start"),
        }
        # A grouped record shows several companies' creative side by side. The
        # group is a billing relationship, not a rename, so a file that is the
        # other company's work has to keep saying so.
        if r.get("_member"):
            item["member"] = r["_member"]
        items.append(item)
    items.sort(key=lambda i: (i["year"], str(i.get("start") or "")), reverse=True)
    return items


def _attach_library(client: str, items: list[dict]) -> None:
    """Say, per creative row, whether we hold a copy of it ourselves.

    The row's URL is a Drive address, and a Drive address is an address in
    somebody else's filing cabinet: it moves, it gets un-shared, and the row
    goes on looking exactly as healthy as the day it worked. `hub/ad_assets`
    copies that creative into the client's library, and this is what lets the
    card open the copy — with the Drive original still beside it, because the
    media team is still working out of that folder and hiding it would be
    telling them the wrong thing.

    Never raises and never blocks the card: a library that cannot be read
    costs this annotation, not Client 360.
    """
    if not items:
        return
    try:
        from hub import ad_assets
        index = ad_assets.library_index(client)
    except Exception:                                   # noqa: BLE001
        return
    if not index:
        return
    for item in items:
        entry = index.get(str(item.get("url") or ""))
        if not entry:
            continue
        item["library_count"] = entry["count"]
        item["library_gallery"] = entry["gallery"]
        first = (entry["files"] or [{}])[0]
        item["library_url"] = first.get("url", "")


def _parse_gtm(value) -> dict | None:
    """websites.json holds strings like 'AdOps: GTM-TG6FPR8M'."""
    s = str(value or "").strip()
    if not s:
        return None
    label, _, rest = s.partition(":")
    if rest.strip():
        return {"login": label.strip(), "id": rest.strip()}
    return {"login": "", "id": s}


def _product_source() -> tuple[list[dict], str, int | None]:
    """The product records to build Client 360 from, live if we can get them.

    Client 360 used to read these from a static export in the source tree,
    which was only ever as current as the last manual refresh — so a client's insertion
    orders showed last month's line-up while the Knack pull reported success,
    because the two are different sources and only one of them was live.

    hub.knack_products reads object_135 from the API and emits rows with the
    same field names, so it can be swapped in here. The export stays as the
    fallback: stale beats empty, because a client record showing no products
    reads as "this client has none" rather than "we couldn't reach Knack".
    """
    # Deliberately not memoised, though `products_error()` and
    # `_client_groups()` now ask within a few lines of each other and a
    # scorecard asks four times. A minute's memo of the shape `_WEB_CACHE`
    # uses next door costs about a tenth of a second a day here — the reports
    # it serves are built once and held by `hub/report_cache.py` — and buys a
    # window in which a source swapped underneath is invisible to every
    # caller. `hub/seo.py`'s own test swaps one, and found the memo hiding it.
    try:
        from hub import knack_products
        data = knack_products.rows()
        if data.get("source") == "knack" and data.get("rows"):
            return data["rows"], "knack", data.get("age_minutes")
    except Exception:                                   # noqa: BLE001
        pass
    return products(), "export", None


def _attachment_only_websites(client: str) -> list[dict]:
    """Websites attached to a client that the websites export knows nothing of.

    `seo._client_websites()` resolves an attachment against the export and
    drops anything it cannot find there — which is every domain discovered
    somewhere else in the Hub and attached on Match Clients, because the export
    is refreshed by hand and stale by definition. A rep attached a domain and
    Client 360 went on saying "No website record matched": the join was made
    and then not shown.

    Enriched from the live Knack registry where that carries the domain, and
    marked `attached` so it never reads as filed data.
    """
    try:
        from . import seo as _s
        att = _s.get_links(client).get("website") or []
        if not att:
            return []
        have = {str(w.get("domain") or "").strip().lower()
                for w in _s._client_websites(client)}
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for a in att:
        d = str(a.get("domain") or "").strip().lower()
        if not d or d in have:
            continue
        have.add(d)
        try:
            from . import knack_websites as _kw
            extra = _kw.record_for_domain(d) or {}
        except Exception:                               # noqa: BLE001
            extra = {}
        row = website_row_from_live(extra, d)
        # What the attachment itself knows, where the live record does not.
        row["name"] = extra.get("client") or a.get("name") or d
        row["liveUrl"] = (extra.get("production_url") or a.get("liveUrl")
                          or ("https://" + d))
        row["manager"] = ""
        row["attached"] = True
        out.append(row)
    return out


def _exact_client_rows(rows: list[dict], name: str) -> list[dict]:
    """Product rows belonging to exactly this client — no substring match.

    `search_client()` matches the *query* loosely on purpose: someone typing
    "riverside" wants to be shown their options. Pulling a **group member's**
    records in is a different act with a different cost — those rows are merged
    into another company's record and totalled into its billing pill — so it
    matches the way `client_key.resolve()` does: identical normalised names,
    nothing else. "Riverside HVAC" must never collect "Riverside HVAC Supply".
    """
    from hub.client_key import normalise_name
    want = normalise_name(name)
    if not want:
        return []
    out = []
    for r in rows:
        if normalise_name(str(r.get("client") or "")) == want \
                or normalise_name(str(r.get("organization") or "")) == want:
            out.append(r)
    return out


def _exact_website_rows(name: str, url: str = "") -> list[dict]:
    """Website records for exactly this client, by name or canonical domain."""
    from hub.client_key import normalise_name
    from hub.client_context import canonical_domain
    want = normalise_name(name)
    dom = canonical_domain(url)
    out = []
    for w in websites():
        if want and normalise_name(str(w.get("name") or "")) == want:
            out.append(w)
        elif dom and canonical_domain(str(w.get("domain") or w.get("liveUrl") or "")) == dom:
            out.append(w)
    return out


def _merge_group_members(g: dict, raw_rows: list[dict], product_rows: list[dict]) -> None:
    """Fold every other member of this client's group into the group dict.

    Nothing happens unless somebody has pressed **Group** on Client 360 — an
    ungrouped client comes out of here byte-identical to how it went in.

    Every merged row is tagged with the client record it came from, and
    duplicates are dropped once: a product filed under the organisation name is
    found under the parent and the member both, and merging it twice doubles
    the "Active billing" figure in the header. A wrong total looks exactly like
    a right one.
    """
    try:
        from hub import client_groups
    except Exception:                                   # noqa: BLE001
        return
    try:
        primary_url = next((str(w.get("domain") or w.get("liveUrl") or "")
                            for w in g["websites"] if w.get("domain") or w.get("liveUrl")), "")
        info = client_groups.roster(str(g["client"]), primary_url)
    except Exception as exc:                            # noqa: BLE001
        # A group that cannot be read must not take the whole record with it.
        g["group"] = {"grouped": False, "error": f"{type(exc).__name__}: {exc}"}
        return
    g["group"] = info
    if not info.get("grouped") or not info.get("others"):
        return

    prod_key = lambda p: (str(p.get("io") or ""), str(p.get("product") or ""),
                          str(p.get("campaign") or ""), str(p.get("start") or ""))
    web_key = lambda w: (str(w.get("domain") or w.get("liveUrl") or "").lower()
                         or str(w.get("name") or "").lower())

    _, pseen = client_groups.merge_rows(g["products"], prod_key, into=[])
    _, wseen = client_groups.merge_rows(g["websites"], web_key, into=[])
    merged = {"products": 0, "websites": 0, "creative": 0, "missing": []}

    for other in info["others"]:
        oname = str(other.get("name") or "")
        ourl = str(other.get("url") or "")
        rows = _exact_client_rows(product_rows, oname)
        if not rows and not _exact_website_rows(oname, ourl):
            # Named, not dropped: "this member has no products" and "we could
            # not find this member's records at all" are different answers.
            merged["missing"].append(oname)
        before = len(g["products"])
        client_groups.merge_rows(
            [{"product": r.get("product"),
              "product_num": r.get("product_num"),
              "campaign": r.get("campaign"),
              "io": r.get("io"), "status": r.get("status"),
              "monthly": r.get("monthly"), "sales": r.get("sales"),
              "partner": r.get("partner"), "start": r.get("start"),
              "end": r.get("end"), "dash": r.get("dash")} for r in rows],
            prod_key, member=oname, into=g["products"], seen=pseen)
        merged["products"] += len(g["products"]) - before

        for r in rows:
            tagged = dict(r)
            tagged["_member"] = oname
            raw_rows.append(tagged)

        before = len(g["websites"])
        client_groups.merge_rows(
            [{"name": w.get("name"), "domain": w.get("domain"),
              "liveUrl": w.get("liveUrl"), "platform": w.get("platform"),
              "status": w.get("status"), "hmMonthly": w.get("hmMonthly"),
              "partner": w.get("partner"), "manager": w.get("manager"),
              "ga": w.get("ga"), "gtm": w.get("gtm"),
              "registrar": w.get("registrar"),
              "domainPurchased": w.get("domainPurchased")}
             for w in _exact_website_rows(oname, ourl)],
            web_key, member=oname, into=g["websites"], seen=wseen)
        merged["websites"] += len(g["websites"]) - before

        # Website records the Hub attached to the member rather than Knack —
        # including the ones the export has never heard of, which is most of
        # the discovered ones.
        try:
            from . import seo as _seog
            if _seog.get_links(oname).get("website"):
                before = len(g["websites"])
                client_groups.merge_rows(
                    [dict(w, attached=True) for w in _seog._client_websites(oname)]
                    + _attachment_only_websites(oname),
                    web_key, member=oname, into=g["websites"], seen=wseen)
                merged["websites"] += len(g["websites"]) - before
        except Exception:                               # noqa: BLE001
            pass

    g["group"]["merged"] = merged


def search_client(q: str, limit: int = 8) -> list[dict]:
    """Group products + website records by client for Client 360."""
    ql = (q or "").strip().lower()
    if not ql:
        return []
    groups: dict[str, dict] = {}

    raw_by_group: dict[str, list[dict]] = {}

    product_rows, product_source, product_age = _product_source()
    for r in product_rows:
        # Knack holds both a client and an organisation and a product is filed
        # under whichever the salesperson used, so match either — the live rows
        # carry both where the export only ever had one.
        client = str(r.get("client", "")).strip()
        org = str(r.get("organization", "")).strip()
        if ql in client.lower():
            pass
        elif org and ql in org.lower():
            client = client or org
        else:
            continue
        if not client:
            continue
        g = groups.setdefault(client.lower(), {"client": client, "products": [], "websites": []})
        raw_by_group.setdefault(client.lower(), []).append(r)
        g["products"].append({
            "product": r.get("product"),
            # Knack's own Product # (field_2640). It is what the campaign team
            # names a line by, so a rep reading the record can quote it back
            # without opening Knack. The committed export has never carried
            # it, so on that source it is absent rather than blank — the two
            # are told apart on the page, not here.
            "product_num": r.get("product_num"),
            "campaign": r.get("campaign"),
            "io": r.get("io"),
            "status": r.get("status"),
            "monthly": r.get("monthly"),
            "sales": r.get("sales"),
            "partner": r.get("partner"),
            "start": r.get("start"),
            "end": r.get("end"),
            "dash": r.get("dash"),
        })

    for w in websites():
        hay = " ".join(str(w.get(k, "")) for k in ("name", "domain", "liveUrl")).lower()
        if ql not in hay:
            continue
        key = str(w.get("name", "")).strip().lower() or str(w.get("domain", "")).lower()
        # attach to an existing client group when names align, else own group
        target = None
        for gk, g in groups.items():
            if gk and (gk in key or key in gk):
                target = g
                break
        if target is None:
            target = groups.setdefault(key, {"client": w.get("name") or w.get("domain"), "products": [], "websites": []})
        target["websites"].append({
            "name": w.get("name"),
            "domain": w.get("domain"),
            "liveUrl": w.get("liveUrl"),
            "platform": w.get("platform"),
            "status": w.get("status"),
            "hmMonthly": w.get("hmMonthly"),
            "partner": w.get("partner"),
            "manager": w.get("manager"),
            "ga": w.get("ga"),
            "gtm": w.get("gtm"),
            "registrar": w.get("registrar"),
            "domainPurchased": w.get("domainPurchased"),
        })

    # Client 360 is a record lookup, not an active-client report. Product and
    # website rows above cover most names, but omit a client known only through
    # the shared registry (for example a house URL, a historical client whose
    # source row is unavailable, or a manually attached URL). Every other tool
    # uses that registry for its picker, so seed its matches here too instead
    # of making the 360 search the one place an inactive client disappears.
    try:
        from hub import clients_registry as _registry
        for row in _registry.search_clients(ql, limit=500):
            client = str(row.get("name") or "").strip()
            key = client.lower()
            if not client or key in groups:
                continue
            group = groups.setdefault(key, {"client": client, "products": [],
                                            "websites": []})
            url = str(row.get("url") or row.get("domain") or "").strip()
            if url:
                group["websites"].append({
                    "name": client, "domain": row.get("domain") or "",
                    "liveUrl": url, "platform": "", "status": "",
                    "hmMonthly": None, "partner": "", "manager": "",
                    "ga": "", "gtm": "", "registrar": "",
                    "domainPurchased": None, "from_registry": True,
                })
            if row.get("is_io_only"):
                group["io_only"] = True
                group["io_orders"] = list(row.get("io_orders") or [])
    except Exception:  # noqa: BLE001 — the source rows still answer normally
        pass

    # Clients whose only trace is an insertion order.
    #
    # Client 360 reads Knack's products and website records, and a client
    # written up on their first IO has neither until the campaign is set up in
    # Knack — so the day their record is most worth opening it comes back
    # empty, which reads exactly like a name typed wrong. hub/io_clients.py
    # registers them at submit, and only when they resolve to nobody, so this
    # can never shadow a real client: a group is added here ONLY when nothing
    # above produced one under that name.
    try:
        from hub import io_clients as _ioc
        from hub.client_key import normalise_name as _nn
        for row in _ioc.overlay().values():
            nm = str(row.get("name") or "").strip()
            if not nm or ql not in nm.lower():
                continue
            if any(_nn(g["client"]) == _nn(nm) for g in groups.values()):
                continue        # Knack has them; its record is the real one
            g = groups.setdefault(nm.lower(), {"client": nm, "products": [],
                                               "websites": []})
            # Said on the record, not merely stored: "no products yet" and
            # "we have never confirmed this client exists" are different
            # answers, and only one of them is a new business to set up.
            g["io_only"] = True
            g["io_orders"] = list(row.get("orders") or [])
            g["io_first_seen"] = row.get("first_seen") or ""
            g["io_contact"] = row.get("contact") or {}
            if row.get("domain") and not g["websites"]:
                g["websites"].append({
                    "name": nm, "domain": row.get("domain"),
                    "liveUrl": row.get("url") or "",
                    "platform": "", "status": "", "hmMonthly": None,
                    "partner": "", "manager": "", "ga": "", "gtm": "",
                    "registrar": "", "domainPurchased": None,
                    "from_io": True,
                })
    except Exception:  # noqa: BLE001 — a client with a Knack record is
        pass           # unaffected, and this must never break search

    # Grouped clients: fold the other members of the group in. A no-op unless
    # somebody has pressed Group on Client 360 for one of these clients.
    for g in groups.values():
        # Keyed the way _creative_items() reads it below, or the merged raw
        # rows land in a bucket nothing looks in and the creative silently
        # stays one company's.
        gkey = str(g["client"]).strip().lower()
        _merge_group_members(g, raw_by_group.setdefault(gkey, []), product_rows)

    # Hub-attached website records (attach-only, never written back to Knack)
    try:
        from . import seo as _seo
        for g in groups.values():
            att = _seo.get_links(str(g["client"])).get("website", [])
            if not att:
                continue
            have = {str(w.get("domain") or "").lower() for w in g["websites"]}
            for w in _seo._client_websites(str(g["client"])):
                d = str(w.get("domain") or "").lower()
                if d and d in have:
                    continue
                have.add(d)
                g["websites"].append({
                    "name": w.get("name"), "domain": w.get("domain"),
                    "liveUrl": w.get("liveUrl"), "platform": w.get("platform"),
                    "status": w.get("status"), "hmMonthly": w.get("hmMonthly"),
                    "partner": w.get("partner"), "manager": w.get("manager"),
                    "ga": w.get("ga"), "gtm": w.get("gtm"),
                    "registrar": w.get("registrar"),
                    "domainPurchased": w.get("domainPurchased"),
                    "attached": True,
                })
            # And the attachments the export has never heard of, which is how
            # a domain discovered elsewhere in the Hub reaches the record.
            for w in _attachment_only_websites(str(g["client"])):
                if w["domain"] in have:
                    continue
                have.add(w["domain"])
                g["websites"].append(w)
    except Exception:  # noqa: BLE001 — attachments must never break search
        pass

    # hub-side website corrections (platform etc.) — display only
    try:
        from . import seo as _seo2
        for g in groups.values():
            _seo2.apply_website_overrides(str(g["client"]), g["websites"])
    except Exception:  # noqa: BLE001
        pass

    out = list(groups.values())
    # clients with most live products first
    out.sort(key=lambda g: (-len(g["products"]), str(g["client"]).lower()))
    for g in out:
        g["products"].sort(key=lambda p: (0 if is_running(p) else 1, str(p.get("product") or "")))

        # ---- header extras: billing, dashboard link, Smart 1 Site flag ----
        live = [p for p in g["products"] if is_running(p)]
        g["billing_monthly"] = round(sum(_num(p.get("monthly")) for p in live))
        g["dash_url"] = next(
            (p.get("dash") for p in live if isinstance(p.get("dash"), str) and p["dash"].startswith("http")),
            next((p.get("dash") for p in g["products"]
                  if isinstance(p.get("dash"), str) and p["dash"].startswith("http")), None),
        )
        g["smart1_site"] = any(
            "smart1" in str(w.get("platform", "")).replace(" ", "").lower() for w in g["websites"]
        )

        # ---- creative files + GTM containers ----
        gkey = str(g["client"]).strip().lower()
        g["creative"] = _creative_items(raw_by_group.get(gkey, []))[:24]
        _attach_library(g["client"], g["creative"])
        gtms, seen_gtm = [], set()
        for w in g["websites"]:
            parsed = _parse_gtm(w.get("gtm"))
            if parsed and parsed["id"] not in seen_gtm:
                seen_gtm.add(parsed["id"])
                parsed["site"] = w.get("name") or w.get("domain")
                gtms.append(parsed)
        g["gtm_containers"] = gtms
        # Say where the products came from and how old they are. A stale export
        # that looks identical to live data is how last month's insertion
        # orders got read as this month's.
        g["products_source"] = product_source
        g["products_age_minutes"] = product_age
        g["products_note"] = (
            f"Live from Knack, {product_age} min old." if product_source == "knack"
            else "From the private fallback export — verify its snapshot "
                 "date before relying on it.")
        # The same for the website cards, which read the same two sources and
        # were the half still on the export until now.
        g["websites_source"] = websites_source()
    return out[:limit]
