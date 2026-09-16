"""The native Amazon DSP pull: daily order/line-item figures from the entity.

One entity, many advertisers, one daily report per advertiser: submit, poll
inside ``amazon_ads.BUDGET_SECONDS``, go *pending* if it is not ready, and
collect it on the next tick with the same reportId. Rows land
``platform="amazon_dsp"``, ``source="native"`` through ``store.upsert_rows``,
so the quarantine, the unmapped queue and the watermark treat them like every
other platform's -- and the provider copy of the same campaign-day defers to
them (``normalize.py``'s native-wins rule).

The connection, the entity and every call are ``modules/ads_builder/
amazon_ads.py``; this file is the pull and the decisions it carries:

* **The campaign is the DSP *order*, and the report's finer grain is folded
  before it is written.** The report answers one row per line item per day;
  the fact table's key is the order-day, so the figures are summed per
  order-day and the line items ride in ``extras`` by name. Writing the
  unfolded rows would file the last line item's spend as the order's, which
  is a wrong client-facing number that nothing on screen would question.
  The auto-mapper files a client from the order name the way it does from a
  Google campaign name, and an order whose name carries no client waits on
  ``/reports/unmapped`` for a person rather than being guessed at.
* **Spend is ``totalCost`` in the advertiser's own currency, with no
  divisor.** If the first live row comes back a thousand times too big, the
  *field* is wrong, not the divisor -- say so on ``/reports/amazon-check``
  rather than dividing until it looks right.
* **Purchases are not conversions.** ``totalPurchases`` and
  ``totalDetailPageViews`` are Amazon-storefront metrics: they land in
  ``extras`` under their own names and nothing here writes either of them
  into ``conversions``. A local-services client with no Amazon storefront
  must not read a conversion count off a metric that counts something they
  do not sell. What this does NOT do is stop the client page printing
  *Conversions 0* for this platform: the fact table's conversions column
  defaults to zero and cannot say "not reported", which is the state AudioGo
  and GroundTruth are already in (provider_map carries ``conversions=None``
  for the same reason and the column still holds 0). Suppressing the tile is
  a change to the client view for every such platform at once, and it is not
  made here rather than made for one.
* **Completes only where video served.** ``videoComplete`` is carried on a
  row that has one, so a display-only client draws no *Video ads completed
  0* tile.
* **Fourteen days every time**, because DSP attribution restates for about
  that long; every row is an upsert by key, so re-reading a day is the same
  spend and not twice it.
* **The reconcile figure is a re-read.** An advertiser-level report over the
  same window is the same feed asked again -- it catches a day the restate
  window never re-read, and it is labeled ``independent: False`` because it
  cannot catch the feed itself being wrong.

**The field names below are a transcription**, not a confirmed map: no live
entity has answered yet. ``CONFIRMED`` stays False until a person has seen a
raw row on ``/reports/amazon-check``, and until then ``status()`` says the
pull is reading a claim -- the ``groundtruth_map.py`` discipline.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from modules.ads_builder import amazon_ads as amz

from . import store

log = logging.getLogger(__name__)

PLATFORM = "amazon_dsp"
LABEL = "Amazon DSP"
LOOKBACK_DAYS = 14
CHECK_PAGE = "/reports/amazon-check"
NOT_CONNECTED = ("not connected: nobody has consented as the entity admin yet -- "
                 "open /tools/ads/settings and press Connect Amazon Ads")

# The response field each fact column is read from. Every name in one place,
# so a wrong one is corrected once rather than in four readers.
FIELD_MAP = {
    "date": "date",
    "order_id": "orderId",
    "order_name": "orderName",
    "line_id": "lineItemId",
    "line_name": "lineItemName",
    "spend": "totalCost",
    "impressions": "impressions",
    "clicks": "clickThroughs",
    "completes": "videoComplete",
    "purchases": "totalPurchases",
    "detail_page_views": "totalDetailPageViews",
}

# Flipped from /reports/amazon-check by a person who has seen a live row.
CONFIRMED = False


# ---------------------------------------------------------------------------
# Configured, connected, neither
# ---------------------------------------------------------------------------

def missing() -> list:
    return amazon_status()["missing"]


def amazon_status() -> dict:
    return amz.connection_status()


def configured() -> bool:
    return amazon_status()["configured"]


def connected() -> bool:
    return amazon_status()["connected"]


def not_configured_line() -> str:
    st = amazon_status()
    if st.get("region_problem"):
        return f"not configured: {amz.ENV['region']} " + st["region_problem"]
    return "not configured: " + ", ".join(st["missing"]) + " unset"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def _num(value):
    try:
        return float(str(value).replace(",", "").replace("$", "")) \
            if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    n = _num(value)
    return int(n) if n is not None else 0


def _day(value) -> str:
    """The row's day as ISO. Amazon answers YYYYMMDD on this report and
    ISO on others, so both are read and anything else is skipped."""
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def to_facts(raw, advertiser: dict) -> dict:
    """``{"rows": [...], "skipped": n}`` from one advertiser's report body.

    **The report's grain is finer than the fact table's, so it is folded
    here.** The request asks for ORDER *and* LINE_ITEM, so Amazon answers one
    row per line item per day, while ``AdPerfDaily``'s key is
    (platform, account, campaign, date) with the order as the campaign. Handed
    over unfolded, three line items on one order-day are three upserts into
    one row and the last one silently wins: an order that spent sixty dollars
    is filed as whatever its last line item spent, on a client-facing figure,
    with every screen looking healthy. So the figures are summed per
    order-day the way ``ttd_myreports`` sums a split row, and the line items
    ride in ``extras`` by name rather than as one arbitrary survivor.

    Every field is read with a default and a row missing its day or its
    order id is skipped and counted, never invented -- parse_records()'s
    rule one platform over.
    """
    f = FIELD_MAP
    folded: dict = {}
    order: list = []
    skipped = 0
    aid = str(advertiser.get("id") or "").strip()
    for r in raw or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        day = store.parse_date(_day(r.get(f["date"])))
        order_id = str(r.get(f["order_id"]) or "").strip()
        if day is None or not order_id or not aid:
            skipped += 1
            continue
        key = (aid, order_id, day)
        row = folded.get(key)
        if row is None:
            extras = {"advertiser_name": str(advertiser.get("name") or "").strip(),
                      "line_items": []}
            if advertiser.get("currency"):
                extras["currency"] = str(advertiser["currency"])
            row = {
                "platform": PLATFORM, "source": "native", "date": day,
                "account_id": aid, "campaign_id": order_id,
                "campaign_name": str(r.get(f["order_name"]) or "").strip(),
                "spend": 0.0, "impressions": 0, "clicks": 0,
                # conversions is deliberately absent: see the module docstring.
                "extras": extras,
            }
            folded[key] = row
            order.append(key)
        elif not row["campaign_name"]:
            # A later line item may carry the order name where the first did
            # not; an order with no name at all waits on /reports/unmapped.
            row["campaign_name"] = str(r.get(f["order_name"]) or "").strip()
        row["spend"] = round(row["spend"] + (_num(r.get(f["spend"])) or 0.0), 2)
        row["impressions"] += _int(r.get(f["impressions"]))
        row["clicks"] += _int(r.get(f["clicks"]))
        completes = _num(r.get(f["completes"]))
        if completes:
            row["completes"] = int(row.get("completes") or 0) + int(completes)
        for key_name in ("purchases", "detail_page_views"):
            value = _num(r.get(f[key_name]))
            if value is not None:
                row["extras"][key_name] = (row["extras"].get(key_name) or 0.0) + value
        name = str(r.get(f["line_name"]) or "").strip() or str(r.get(f["line_id"]) or "").strip()
        if name and name not in row["extras"]["line_items"]:
            # Named rather than counted: a client reading "3 line items" learns
            # nothing, and the names are what a person recognizes on a report.
            row["extras"]["line_items"].append(name)
    rows = [folded[k] for k in order]
    for row in rows:
        if not row["extras"]["line_items"]:
            row["extras"].pop("line_items")
    return {"rows": rows, "skipped": skipped}


# ---------------------------------------------------------------------------
# What the last run said, and which reports it is still waiting on
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir("reports"), "amazon_dsp_status.json")


def _remember(state: dict) -> None:
    try:
        from hub import jsonstore
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                       # noqa: BLE001 - a note is not the pull
        pass


def _remembered() -> dict:
    try:
        from hub import jsonstore
        return jsonstore.read_json(_state_path(), default={}) or {}
    except Exception:                       # noqa: BLE001
        return {}


def pending_reports() -> dict:
    """``{advertiser_id: report_id}`` the last tick was still waiting on."""
    got = _remembered().get("pending")
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def pull(today: date | None = None, pending: dict | None = None, days: int = LOOKBACK_DAYS,
         sleep=None, clock=None, budget: float | None = None) -> dict:
    """One tick: every advertiser under the entity, the trailing fortnight.

    ``pending`` is ``{advertiser_id: report_id}`` carried from the last tick
    -- the reports Amazon was still preparing. The caller may pass one; left
    out, the module's own note supplies it, so the scheduler wires this up
    the way it wires every other pull and the carrying still happens.

    A report still preparing when the budget runs out is **not a failure**:
    it is recorded as pending, no watermark is stamped over it, and the next
    tick asks for the same reportId rather than paying for a second report.
    One advertiser refused (``not_permitted``, most often the API
    application not being approved for it) is that advertiser's problem and
    is named; the rest of the entity still lands.
    """
    out = {"ok": False, "rows": 0, "advertisers": 0, "skipped": 0, "pending": {},
           "failures": [], "error": "", "notes": []}
    st = amazon_status()
    if not st["configured"]:
        out["error"] = not_configured_line()
        _remember({**out, "at": store.iso(store.now()), "reached": False})
        return out
    if not st["connected"]:
        out["error"] = NOT_CONNECTED
        _remember({**out, "at": store.iso(store.now()), "reached": False})
        return out
    if not CONFIRMED:
        out["notes"].append("the field map is a transcription nobody has confirmed -- "
                            f"reading it as a claim; confirm on {CHECK_PAGE}")

    today = today or date.today()
    pending = dict(pending if pending is not None else pending_reports())
    start = today - timedelta(days=max(1, int(days)))
    end = today - timedelta(days=1)          # complete days only

    cfg = amz.load_config()
    try:
        adv = amz.list_advertisers(cfg)
    except (amz.AmazonAuthError, amz.AmazonApiError) as exc:
        out["error"] = _entity_error(exc)
        store.record_sync(PLATFORM, rows=0, error=out["error"], source="native")
        _remember({**out, "at": store.iso(store.now()), "reached": True})
        return out
    if not adv.get("ok"):
        out["error"] = amz._redact(adv.get("error") or "the entity's profile is not visible")
        store.record_sync(PLATFORM, rows=0, error=out["error"], source="native")
        _remember({**out, "at": store.iso(store.now()), "reached": True})
        return out

    profile = adv["profile_id"]
    out["advertisers"] = len(adv["advertisers"])
    rows: list = []
    for a in adv["advertisers"]:
        aid = a["id"]
        name = a.get("name") or aid
        try:
            report_id = pending.get(aid) or amz.submit_report(
                aid, start.isoformat(), end.isoformat(), profile_id=profile)
            state = amz.poll_report(aid, report_id, profile_id=profile, budget=budget,
                                    sleep=sleep, clock=clock)
            if state["status"] == "PENDING":
                out["pending"][aid] = state["report_id"]
                continue
            if state["status"] == "FAILURE":
                out["failures"].append(f"{name}: {amz._redact(state.get('error'))}")
                continue
            parsed = to_facts(amz.download_report(state["location"]), a)
            rows.extend(parsed["rows"])
            out["skipped"] += parsed["skipped"]
        except amz.AmazonApiError as exc:
            out["failures"].append(f"{name} ({exc.kind}): {amz._redact(exc)}")
        except amz.AmazonAuthError as exc:
            out["failures"].append(f"{name}: {amz._redact(exc)}")
        except Exception as exc:            # noqa: BLE001 - one advertiser, not the pull
            log.exception("reports: Amazon DSP pull failed for %s", aid)
            out["failures"].append(f"{name}: {type(exc).__name__}: {amz._redact(exc)}")

    try:
        out["rows"] = store.upsert_rows(rows, today=today) if rows else 0
    except Exception as exc:                # noqa: BLE001
        log.exception("reports: Amazon DSP rows could not be written")
        out["failures"].append(f"the rows could not be written: {type(exc).__name__}: {exc}"[:300])

    out["error"] = "; ".join(out["failures"])[:300]
    out["ok"] = not out["failures"]
    if out["pending"] and not rows and not out["failures"]:
        # Nothing landed and nothing is wrong: Amazon is still preparing. No
        # watermark, so /status reads this as a feed whose age is growing
        # rather than as a fault stamped every six hours -- stackadapt's rule.
        out["ok"] = True
    else:
        store.record_sync(PLATFORM, rows=out["rows"], error=out["error"], source="native")
    _remember({**out, "at": store.iso(store.now()), "reached": True})
    return out


def _entity_error(exc) -> str:
    if isinstance(exc, amz.AmazonApiError) and exc.kind == "not_permitted":
        return amz._redact(
            f"the entity refused the read ({exc}) -- an Amazon Ads API application that has "
            "not been approved for this entity answers exactly like a wrong key; this is "
            "the approval, not the key")
    return amz._redact(f"the entity could not be read: {exc}")


# ---------------------------------------------------------------------------
# The line /reports/ prints
# ---------------------------------------------------------------------------

def status() -> dict:
    """The index line. Nothing here carries a credential."""
    st = amazon_status()
    remembered = _remembered()
    sync = store.sync_status().get(PLATFORM) or {}
    native = sync if sync.get("source") == "native" else {}
    # A run that stopped at "not configured" reached nothing, so its stamp is
    # not a last pull: saying otherwise is how a feed that has never once been
    # read reads as one that was read this morning.
    last = (native.get("last_run_at")
            or (remembered.get("at") if remembered.get("reached") else "")
            or "never")
    if not st["configured"]:
        line = f"{LABEL}: " + not_configured_line()
    elif not st["connected"]:
        line = f"{LABEL}: " + NOT_CONNECTED
    else:
        line = f"{LABEL}: connected"
        if remembered.get("advertisers"):
            line += f", {remembered['advertisers']} advertisers"
        line += ", last pull " + str(last)
        if native.get("error"):
            line += " -- " + str(native["error"])
        elif remembered.get("pending"):
            line += (f" -- {len(remembered['pending'])} report(s) still preparing at Amazon; "
                     "the next tick collects them")
        if not CONFIRMED:
            line += f" -- reading the field map as a claim until it is confirmed on {CHECK_PAGE}"
    return {
        "configured": st["configured"], "connected": st["connected"],
        "missing": st["missing"], "region": st["region"],
        "entity_id": st["entity_id"], "confirmed": CONFIRMED,
        "advertisers": remembered.get("advertisers"),
        "pending": remembered.get("pending") or {},
        "last_pull": native.get("last_run_at") or "",
        "last_error": native.get("error") or "",
        "line": line,
    }


# ---------------------------------------------------------------------------
# /reports/amazon-check
# ---------------------------------------------------------------------------

def check(today: date | None = None) -> dict:
    """What the check page shows: the ladder, the call as it would be made,
    and -- where the entity answers -- one raw row against FIELD_MAP.

    Reaches Amazon only as far as the ladder allows, and never at all while
    the connection is unconfigured or unconsented: a credential is not sent
    to find out whether it is set.
    """
    today = today or date.today()
    end = today - timedelta(days=1)
    start = today - timedelta(days=LOOKBACK_DAYS)
    out = {"status": status(), "confirmed": CONFIRMED, "map": dict(FIELD_MAP),
           "endpoints": {k: {"method": v["method"], "path": v["path"],
                             "confirmed": v["confirmed"]} for k, v in amz.ENDPOINTS.items()},
           "request": {"host": "", "path": amz.ENDPOINTS["dsp_report_submit"]["path"],
                       "body": amz.report_request(start.isoformat(), end.isoformat())},
           "window": {"start": start.isoformat(), "end": end.isoformat()},
           "preflight": None, "sample": None, "resolves": None, "error": ""}
    st = out["status"]
    try:
        out["request"]["host"] = amz.load_config().host
    except Exception:                       # noqa: BLE001
        pass
    if not st["configured"]:
        out["error"] = not_configured_line()
        return out
    if not st["connected"]:
        out["error"] = NOT_CONNECTED
        return out
    try:
        out["preflight"] = amz.preflight()
    except Exception as exc:                # noqa: BLE001 - a page, not a pull
        out["error"] = amz._redact(f"{type(exc).__name__}: {exc}")
        return out
    pre = out["preflight"]
    if not pre.get("advertisers"):
        return out
    adv = pre["advertisers"][0]
    try:
        report_id = pending_reports().get(adv["id"]) or amz.submit_report(
            adv["id"], start.isoformat(), end.isoformat(), profile_id=pre["entity_profile_id"])
        state = amz.poll_report(adv["id"], report_id, profile_id=pre["entity_profile_id"])
        if state["status"] != "SUCCESS":
            out["error"] = (f"the report for {adv.get('name') or adv['id']} is "
                            f"{state['status'].lower()}"
                            + (f": {amz._redact(state.get('error'))}"
                               if state.get("error") else " -- ask again in a moment"))
            return out
        raw = amz.download_report(state["location"])
    except (amz.AmazonAuthError, amz.AmazonApiError) as exc:
        out["error"] = amz._redact(f"{exc}")
        return out
    out["sample"] = raw[0] if raw else None
    out["resolves"] = check_map(raw)
    return out


def check_map(raw) -> dict:
    """Does FIELD_MAP resolve against this body? ``{"resolved", "missing",
    "rows"}`` -- the names the report did not carry, so a person correcting
    the map is reading the gap rather than guessing at it."""
    rows = [r for r in (raw or []) if isinstance(r, dict)]
    if not rows:
        return {"resolved": False, "missing": sorted(set(FIELD_MAP.values())),
                "rows": 0, "why": "the report carried no rows"}
    sample = rows[0]
    required = ("date", "order_id", "order_name", "spend", "impressions", "clicks")
    gone = sorted(FIELD_MAP[k] for k in required if FIELD_MAP[k] not in sample)
    return {"resolved": not gone, "missing": gone, "rows": len(rows), "why": ""}


# ---------------------------------------------------------------------------
# reconcile.py
# ---------------------------------------------------------------------------

def month_total(start: date, end: date, budget: float | None = None,
                sleep=None, clock=None) -> dict:
    """Amazon's own figure for the window: the same report asked again over
    the whole month and summed. A re-read, and said to be one.

    Polled inside the pull's own budget, so a report Amazon has not finished
    preparing is *not measured* tonight rather than the nightly job's one
    thread being held for it -- bing_total()'s rule, for the same thread.
    """
    rows, advertisers = [], 0
    cfg = amz.load_config()
    adv = amz.list_advertisers(cfg)
    if not adv.get("ok"):
        raise amz.AmazonApiError(adv.get("error") or "the entity's profile is not visible",
                                 kind="not_found")
    for a in adv["advertisers"]:
        report_id = amz.submit_report(a["id"], start.isoformat(), end.isoformat(),
                                      profile_id=adv["profile_id"])
        state = amz.poll_report(a["id"], report_id, profile_id=adv["profile_id"],
                                budget=budget, sleep=sleep, clock=clock)
        if state["status"] != "SUCCESS":
            raise amz.AmazonApiError(
                f"the month report for {a.get('name') or a['id']} was "
                f"{state['status'].lower()} inside the budget; not measured tonight",
                kind="pending")
        advertisers += 1
        rows.extend(to_facts(amz.download_report(state["location"]), a)["rows"])
    return {"rows": rows, "advertisers": advertisers}
