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
# How long a report may sit PENDING before this stops asking for that one and
# asks for a new one. The job runs nightly, so a report still preparing when
# the next tick comes round has missed a whole cycle: either it failed at
# Amazon without saying so, or the id is no longer one Amazon will finish.
# Re-polling it for ever is the failure this exists to stop -- the pull looks
# healthy every night, /reports/ says "still preparing" every night, and
# nothing ever lands. A fresh submission costs one report per advertiser per
# night, which is bounded and cheap; silence is not.
STUCK_AFTER_HOURS = 26
CHECK_PAGE = "/reports/amazon-check"
# What the check page may spend. The nightly job's budget is twenty seconds
# and its download read timeout two minutes, which is right for a job nobody
# is watching and wrong for a request holding one of two gunicorn workers.
CHECK_BUDGET_SECONDS = 8
CHECK_TIMEOUT = (10, 20)
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
        try:
            day = store.parse_date(_day(r.get(f["date"])))
        except ValueError:
            # store.parse_date RAISES on a value it cannot read; it does not
            # answer None. Unguarded, one unreadable day in one line item threw
            # out of this loop and lost the whole advertiser's report -- the
            # opposite of the skipped-and-counted rule three lines below.
            day = None
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
        row["spend"] += _num(r.get(f["spend"])) or 0.0
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
        # Rounded once, here, rather than on every line item: rounding each
        # step compounds, and an order-day with forty line items can drift a
        # fifth of a dollar away from what Amazon billed.
        row["spend"] = round(row["spend"], 2)
        if not row["extras"]["line_items"]:
            row["extras"].pop("line_items")
    return {"rows": rows, "skipped": skipped}


# ---------------------------------------------------------------------------
# What the last run said, and which reports it is still waiting on
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir("reports"), "amazon_dsp_status.json")


def _remember(state: dict, *, keep_pending: bool = False) -> None:
    """Write the note. ``keep_pending`` carries the reports the last tick was
    waiting on straight through, for a run that returned before it ever looked
    at them -- a night that stopped at "not configured", or at an entity that
    would not answer. Overwriting those with this run's empty dict is how one
    throttled night makes the next one pay for every report again."""
    previous = _remembered()
    if keep_pending:
        held = previous.get("pending")
        state = {**state, "pending": held if isinstance(held, dict) else {}}
    # The history lanes ride along untouched: they are pull_window()'s and
    # the nightly's note is not where they are decided.
    if "history" not in state:
        history = previous.get("history")
        state = {**state, "history": history if isinstance(history, dict) else {}}
    try:
        from hub import jsonstore
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                       # noqa: BLE001 - a note is not the pull
        pass


def _lane_pending(lane: str) -> dict:
    """``{advertiser_id: report_id}`` a history lane is waiting on."""
    held = ((_remembered().get("history") or {}).get(lane) or {})
    return {str(a): str(h.get("report_id")) for a, h in held.items()
            if isinstance(h, dict) and h.get("report_id")}


def _lane_since(lane: str) -> dict:
    held = ((_remembered().get("history") or {}).get(lane) or {})
    return {str(a): str(h["since"]) for a, h in held.items() if isinstance(h, dict) and h.get("since")}


def _remember_lane(lane: str, pending: dict) -> None:
    """Write one history lane's pending reports, and nothing else: the
    nightly's ``pending`` and its last result stay as they were."""
    state = _remembered()
    history = dict(state.get("history") or {})
    if pending:
        history[lane] = dict(pending)
    else:
        history.pop(lane, None)
    _remember({**state, "history": history})


def _remembered() -> dict:
    try:
        from hub import jsonstore
        return jsonstore.read_json(_state_path(), default={}) or {}
    except Exception:                       # noqa: BLE001
        return {}


def _remember_pending(advertiser_id: str, report_id: str) -> None:
    """Record a report this process just asked for, without disturbing the
    rest of the note -- so a report submitted from the check page is one the
    nightly pull collects rather than an orphan nobody ever reads."""
    state = _remembered()
    held = dict(state.get("pending") or {})
    held[str(advertiser_id)] = {"report_id": str(report_id),
                                "since": store.iso(store.now())}
    _remember({**state, "pending": held})


def pending_reports() -> dict:
    """``{advertiser_id: report_id}`` the last tick was still waiting on.

    Reads the note's older flat shape too, so the tick after a deploy that
    carried ``{adv: report_id}`` collects what it was waiting on rather than
    paying for a second report.
    """
    out = {}
    for adv, held in (_remembered().get("pending") or {}).items():
        rid = held.get("report_id") if isinstance(held, dict) else held
        if rid:
            out[str(adv)] = str(rid)
    return out


def _carried(pending) -> dict:
    """``{advertiser_id: report_id}`` from either shape.

    ``pull()`` answers ``{adv: {report_id, since}}`` and used to accept only
    ``{adv: report_id}``, so handing a caller's own previous result straight
    back formatted a dict into the report-status URL. Both are read here, in
    the one place either arrives.
    """
    out = {}
    for adv, held in (pending or {}).items():
        rid = held.get("report_id") if isinstance(held, dict) else held
        if rid:
            out[str(adv)] = str(rid)
    return out


def pending_since() -> dict:
    """``{advertiser_id: ISO stamp}`` -- when each pending report was first
    seen. Absent for a note written before this was recorded, which reads as
    "first seen now" rather than as "stuck": a report is not called stuck on
    the strength of a stamp nobody took."""
    out = {}
    for adv, held in (_remembered().get("pending") or {}).items():
        if isinstance(held, dict) and held.get("since"):
            out[str(adv)] = str(held["since"])
    return out


def _hours_since(stamp: str) -> float:
    """Hours between an ISO stamp and now, or 0.0 for anything unreadable --
    an unparsable stamp must not make a live report look stuck."""
    from datetime import datetime, timezone
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 3600.0)


def stuck_reports(since: dict | None = None) -> dict:
    """``{advertiser_id: hours}`` for every pending report past the ceiling
    -- the nightly's, or a lane's when its stamps are passed in."""
    since = pending_since() if since is None else since
    return {adv: round(_hours_since(stamp), 1) for adv, stamp in since.items()
            if _hours_since(stamp) >= STUCK_AFTER_HOURS}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def pull(today: date | None = None, pending: dict | None = None, days: int = LOOKBACK_DAYS,
         sleep=None, clock=None, budget: float | None = None, *,
         window: tuple | None = None, lane: str = "") -> dict:
    """One tick: every advertiser under the entity, the trailing fortnight.

    ``window`` and ``lane`` are ``pull_window()``'s: a fixed ``(start, end)``
    instead of the trailing days, and the pending report ids kept under
    ``lane`` in the note's ``history`` rather than beside the nightly's, so
    neither run collects the other's reports. A lane stamps no watermark
    and leaves the nightly's note alone: history landing is not the night's
    pull happening.

    ``pending`` is ``{advertiser_id: report_id}`` carried from the last tick
    -- the reports Amazon was still preparing. The caller may pass one; left
    out, the module's own note supplies it, so the scheduler wires this up
    the way it wires every other pull and the carrying still happens.

    A report still preparing when the budget runs out is **not a failure**:
    it is recorded as pending with the moment it was first seen, no watermark
    is stamped over it, and the next tick asks for the same reportId rather
    than paying for a second report -- until ``STUCK_AFTER_HOURS``, past which
    that report is given up on, a fresh one is asked for, and both the result
    and the index line say so.
    One advertiser refused (``not_permitted``, most often the API
    application not being approved for it) is that advertiser's problem and
    is named; the rest of the entity still lands.
    """
    out = {"ok": False, "rows": 0, "advertisers": 0, "skipped": 0, "pending": {},
           "gave_up": {}, "failures": [], "error": "", "notes": []}
    st = amazon_status()
    if not st["configured"]:
        out["error"] = not_configured_line()
        if not lane:
            _remember({**out, "at": store.iso(store.now()), "reached": False},
                      keep_pending=True)
        return out
    if not st["connected"]:
        out["error"] = NOT_CONNECTED
        if not lane:
            _remember({**out, "at": store.iso(store.now()), "reached": False},
                      keep_pending=True)
        return out
    if not CONFIRMED:
        out["notes"].append("the field map is a transcription nobody has confirmed -- "
                            f"reading it as a claim; confirm on {CHECK_PAGE}")

    today = today or date.today()
    if pending is not None:
        carried = _carried(pending)
    else:
        carried = _lane_pending(lane) if lane else pending_reports()
    since = _lane_since(lane) if lane else pending_since()
    stale = {adv: h for adv, h in stuck_reports(since).items() if adv in carried}
    for adv in stale:
        # Past the ceiling: stop asking for that report and ask for a new one.
        # Named in the result and on the index, because a pull that quietly
        # starts over is a pull nobody can tell from one that never started.
        carried.pop(adv, None)
        since.pop(adv, None)
    out["gave_up"] = stale
    if stale:
        # Paired, not two sorted lists: sorting the names and the hours
        # separately printed each advertiser against another's duration the
        # moment there were two of them.
        out["notes"].append(
            "asked for a fresh report for "
            + ", ".join(f"{adv} (preparing {stale[adv]:g}h)" for adv in sorted(stale))
            + f", each past {STUCK_AFTER_HOURS}h with nothing landed for it")
    pending = carried
    if window:
        start, end = window
    else:
        start = today - timedelta(days=max(1, int(days)))
        end = today - timedelta(days=1)      # complete days only

    cfg = amz.load_config()
    try:
        adv = amz.list_advertisers(cfg)
    except (amz.AmazonAuthError, amz.AmazonApiError) as exc:
        out["error"] = _entity_error(exc)
        if not lane:
            store.record_sync(PLATFORM, rows=0, error=out["error"], source="native")
            _remember({**out, "at": store.iso(store.now()), "reached": True},
                      keep_pending=True)
        return out
    if not adv.get("ok"):
        out["error"] = amz._redact(adv.get("error") or "the entity's profile is not visible")
        if not lane:
            store.record_sync(PLATFORM, rows=0, error=out["error"], source="native")
            _remember({**out, "at": store.iso(store.now()), "reached": True},
                      keep_pending=True)
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
                # The stamp is the one this report has carried since it was
                # first seen, not now: refreshing it every tick is how a
                # report stays "pending since a moment ago" for ever.
                out["pending"][aid] = {"report_id": state["report_id"],
                                       "since": since.get(aid) or store.iso(store.now())}
                continue
            if state["status"] == "FAILURE":
                out["failures"].append(f"{name}: {amz._redact(state.get('error'))}")
                continue
            parsed = to_facts(amz.download_report(state["location"]), a)
            rows.extend(parsed["rows"])
            out["skipped"] += parsed["skipped"]
        except amz.AmazonApiError as exc:
            out["failures"].append(f"{name} ({exc.kind}): {amz._redact(exc)}")
            _keep_carried(out, aid, pending, since)
        except amz.AmazonAuthError as exc:
            out["failures"].append(f"{name}: {amz._redact(exc)}")
            _keep_carried(out, aid, pending, since)
        except Exception as exc:            # noqa: BLE001 - one advertiser, not the pull
            log.exception("reports: Amazon DSP pull failed for %s", aid)
            out["failures"].append(f"{name}: {type(exc).__name__}: {amz._redact(exc)}")
            _keep_carried(out, aid, pending, since)

    try:
        out["rows"] = store.upsert_rows(rows, today=today) if rows else 0
    except Exception as exc:                # noqa: BLE001
        log.exception("reports: Amazon DSP rows could not be written")
        out["failures"].append(f"the rows could not be written: {type(exc).__name__}: {exc}"[:300])

    out["error"] = "; ".join(out["failures"])[:300]
    out["ok"] = not out["failures"]
    if lane:
        # History: the lane's pending ids are what is remembered, and the
        # night's watermark and note are not touched.
        _remember_lane(lane, out["pending"])
        return out
    if out["pending"] and not rows and not out["failures"]:
        # Nothing landed and nothing is wrong: Amazon is still preparing. No
        # watermark, so /status reads this as a feed whose age is growing
        # rather than as a fault stamped every night -- stackadapt's rule.
        out["ok"] = True
    else:
        store.record_sync(PLATFORM, rows=out["rows"], error=out["error"], source="native")
    _remember({**out, "at": store.iso(store.now()), "reached": True})
    return out


def pull_window(start: date, end: date, **kw) -> dict:
    """One fixed window of history, for ``modules/reports/backfill.py``:
    ``pull()`` over ``(start, end)`` on the lane named for the window, so
    the report ids it waits on are kept apart from the nightly's. Answers
    ``pending`` (a truthy dict) while Amazon is still preparing any of the
    advertisers' reports, and the ledger asks for the same window again."""
    lane = f"{start.isoformat()}..{end.isoformat()}"
    out = pull(today=end + timedelta(days=1), window=(start, end), lane=lane, **kw)
    if out.get("pending"):
        out["note"] = (f"{len(out['pending'])} report(s) still preparing at Amazon for "
                       f"{start} to {end}; the next run collects them")
    return out


def _keep_carried(out: dict, aid: str, pending: dict, since: dict) -> None:
    """Hold on to the report this advertiser was already waiting on when the
    tick refused partway through.

    A throttle or a dropped connection in the middle of polling is not a
    reason to abandon a report Amazon is still preparing. Dropping it here
    looked harmless and was not: the next tick submits a fresh one, which
    resets the first-seen stamp, so a report that never finishes resets its
    own clock every night and ``STUCK_AFTER_HOURS`` never fires for it -- the
    ceiling silently undone by the error path.
    """
    report_id = pending.get(aid)
    if not report_id:
        return
    out["pending"][aid] = {"report_id": report_id,
                           "since": since.get(aid) or store.iso(store.now())}


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
    stuck = stuck_reports()
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
        elif stuck:
            line += (f" -- {len(stuck)} report(s) have been preparing at Amazon for "
                     + ", ".join(f"{h:g}h" for h in sorted(stuck.values()))
                     + f"; past {STUCK_AFTER_HOURS}h the next run asks for a fresh one "
                       "rather than carrying that id again")
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
        "pending": pending_reports(),
        "stuck": stuck,
        "last_pull": native.get("last_run_at") or "",
        "last_error": native.get("error") or "",
        "line": line,
    }


# ---------------------------------------------------------------------------
# /reports/amazon-check
# ---------------------------------------------------------------------------

def check(today: date | None = None) -> dict:
    """``_check()``, and never a raise. The house rule a diagnostics panel
    works to: a page that 500s costs every other panel on it, and this one is
    where somebody goes precisely when the connection is behaving oddly. Any
    escape is reported in the page's own ``error``."""
    try:
        return _check(today=today)
    except Exception as exc:                # noqa: BLE001 - a page, not a pull
        log.exception("reports: the Amazon DSP check page could not be built")
        return {"status": {"configured": False, "connected": False, "missing": [],
                           "region": "", "entity_id": "", "confirmed": CONFIRMED,
                           "line": f"{LABEL}: the check page could not be built"},
                "confirmed": CONFIRMED, "map": dict(FIELD_MAP), "endpoints": {},
                "request": {"host": "", "path": "", "body": {}},
                "window": {"start": "", "end": ""},
                "preflight": None, "sample": None, "resolves": None,
                "error": amz._redact(f"the check page could not be built: "
                                     f"{type(exc).__name__}: {exc}")}


def _check(today: date | None = None) -> dict:
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
        report_id = pending_reports().get(adv["id"])
        if not report_id:
            report_id = amz.submit_report(
                adv["id"], start.isoformat(), end.isoformat(),
                profile_id=pre["entity_profile_id"])
            # Written down the moment it exists. This page is a GET a person
            # refreshes while Amazon is slow, and a reportId that is asked for
            # and never recorded is a report nobody collects -- one orphaned
            # per refresh. Recorded, the next refresh polls this same one and
            # the nightly pull collects it.
            _remember_pending(adv["id"], report_id)
        state = amz.poll_report(adv["id"], report_id, profile_id=pre["entity_profile_id"],
                                budget=CHECK_BUDGET_SECONDS)
        if state["status"] != "SUCCESS":
            out["error"] = (f"the report for {adv.get('name') or adv['id']} is "
                            f"{state['status'].lower()}"
                            + (f": {amz._redact(state.get('error'))}"
                               if state.get("error") else " -- ask again in a moment"))
            return out
        raw = amz.download_report(state["location"], timeout=CHECK_TIMEOUT)
    except (amz.AmazonAuthError, amz.AmazonApiError) as exc:
        out["error"] = amz._redact(f"{exc}")
        return out
    # Dict-filtered the way check_map() filters: the template calls
    # .items() on this, and check()'s guard wraps _check() rather than the
    # render, so a non-dict row here 500s the one page written never to fall
    # over.
    out["sample"] = next((r for r in (raw or []) if isinstance(r, dict)), None)
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
