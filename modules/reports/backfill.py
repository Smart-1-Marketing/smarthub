"""History for the fact table: thirty days further back, on request or
nightly until the platform has nothing older.

The native pulls read a trailing window -- thirty days for most, fourteen
for Microsoft Ads and AudioGo -- so the fact table starts the day the first
pull ran and last month is not in it. Every pull but Amazon DSP computes
its window as the ``days`` before ``today``, so the same call with ``today``
set to the day before the oldest native day on file reaches thirty days
further back, through the same parser, the same upsert and the same
campaign map. That is the whole mechanism: ``PULLS`` names the modules,
``window_for()`` picks the window, ``run_platform()`` makes the call and
moves the ledger.

## The ledger

``reports/backfill.json`` (``hub/jsonstore``, mirrored), one entry per
platform:

* ``reached`` -- the first day of the earliest window pulled, so the next
  window ends the day before it. Seeded from the fact table's oldest
  native day when there is no entry yet;
* ``complete`` -- the last window landed nothing, so the platform has no
  history before ``reached``. Nightly stops here; the button still works,
  and a window that lands rows again clears it;
* ``nightly`` -- pull another window every night until complete;
* ``started_at`` / ``finished_at`` / ``last_rows`` / ``last_error`` /
  ``pending`` -- the last run, on the disk both workers read.

The Trade Desk cannot be asked for a window directly: MyReports runs a
schedule and delivers a file later. ``ttd.pull_window()`` creates a one-off
schedule for the window and reads it back on the next run, so a Trade Desk
window is ``pending`` for a day and the ledger does not move until the file
lands. Amazon DSP is the same shape with a shorter wait: its reports are
asynchronous, and ``amazon_dsp.pull_window()`` keeps the report ids it is
waiting on under a lane of their own in the module's note, apart from the
nightly's, so neither run collects the other's reports.

A backfill run for the ``today``-and-``days`` platforms stamps the platform's
native watermark like any pull, so the index's "last pull" line reflects it.
The 3 AM ledger is not touched.
"""
from __future__ import annotations

import importlib
import logging
import os
from datetime import date, datetime, timedelta, timezone

from . import store

log = logging.getLogger(__name__)

WINDOW_DAYS = 30
STALE_MINUTES = 60

# platform -> (module under modules.reports, how it takes a window)
PULLS = {
    "google": ("google_ads_perf", "today_days"),
    "stackadapt": ("stackadapt", "today_days"),
    "audiogo": ("audiogo", "today_days"),
    "bing": ("bing", "today_days"),
    "groundtruth": ("groundtruth", "today_days"),
    "callrail": ("callrail", "today_days"),
    "ttd": ("ttd", "window"),
    "amazon_dsp": ("amazon_dsp", "window"),
}
# Platforms with a native pull and no history path, with the reason the card
# prints. Empty today: Amazon DSP was here until its pull_window() kept its
# pending report ids on a lane of their own.
NOT_WIRED: dict = {}
PLATFORMS = tuple(PULLS) + tuple(NOT_WIRED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def _path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir("reports"), "backfill.json")


def ledger() -> dict:
    try:
        from hub import jsonstore
        data = jsonstore.read_json(_path(), default={}) or {}
    except Exception:                                   # noqa: BLE001
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    from hub import jsonstore
    jsonstore.write_json(_path(), data)


def entry(platform: str) -> dict:
    return dict(ledger().get(platform) or {})


def _update(platform: str, **fields) -> dict:
    data = ledger()
    row = dict(data.get(platform) or {})
    row.update(fields)
    data[platform] = row
    _save(data)
    return row


def set_nightly(platform: str, on: bool) -> dict:
    """Pull another window every night until complete. Turning it on clears
    ``complete`` so a platform that was finished is asked once more."""
    if platform not in PULLS:
        raise ValueError(f"{platform} cannot be backfilled: "
                         + NOT_WIRED.get(platform, "not a native pull"))
    fields = {"nightly": bool(on)}
    if on:
        fields["complete"] = False
    return _update(platform, **fields)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def window_for(platform: str, today: date | None = None) -> tuple[date, date]:
    """The next thirty days back: ending the day before ``reached`` (the
    ledger's), else the day before the oldest native day on file, else
    yesterday. ``(start, end)``, both inclusive."""
    today = today or date.today()
    row = entry(platform)
    reached = _parse_day(row.get("reached"))
    if reached is None:
        reached = store.oldest_dates(source="native").get(platform)
    end = (reached - timedelta(days=1)) if reached else (today - timedelta(days=1))
    start = end - timedelta(days=WINDOW_DAYS - 1)
    return start, end


def _pull_module(name: str):
    return importlib.import_module(f"modules.reports.{name}")


def _call(platform: str, start: date, end: date) -> dict:
    mod_name, how = PULLS[platform]
    mod = _pull_module(mod_name)
    if how == "window":
        return mod.pull_window(start, end)
    # today_days: the module reads the ``days`` before ``today``; Google Ads
    # reads one more (today - days), which overlaps a day and is harmless.
    return mod.pull(days=WINDOW_DAYS, today=end)


def running(platform: str) -> bool:
    row = entry(platform)
    started = row.get("started_at")
    if not started or row.get("finished_at"):
        return False
    try:
        since = datetime.fromisoformat(str(started))
    except ValueError:
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return (_now() - since).total_seconds() < STALE_MINUTES * 60


# ---------------------------------------------------------------------------
# A run
# ---------------------------------------------------------------------------

def run_platform(platform: str, *, today: date | None = None, actor: str = "") -> dict:
    """Pull the next window for one platform and move the ledger.

    ``{"ok", "platform", "start", "end", "rows", "pending", "complete",
    "error"}``. The ledger moves only on a landed window: an error or a
    pending file leaves ``reached`` where it was, and the next run asks for
    the same window again."""
    out = {"ok": False, "platform": platform, "start": None, "end": None, "rows": 0,
           "pending": False, "complete": False, "error": ""}
    if platform in NOT_WIRED:
        out["error"] = NOT_WIRED[platform]
        return out
    if platform not in PULLS:
        out["error"] = f"{platform} is not a native pull."
        return out
    if running(platform):
        out["error"] = "A history pull for this platform is still running; not started again."
        return out
    start, end = window_for(platform, today)
    out["start"], out["end"] = start.isoformat(), end.isoformat()
    # ``last_day`` is the run's own day (the ``today`` it was given), which
    # is what "once a night" is counted in; ``started_at`` is the clock.
    _update(platform, started_at=_iso(_now()), finished_at="", actor=actor or "",
            last_day=(today or date.today()).isoformat(),
            window={"start": out["start"], "end": out["end"]})
    try:
        res = _call(platform, start, end) or {}
    except Exception as exc:                            # noqa: BLE001 - one platform, not the job
        log.exception("reports: history pull failed for %s", platform)
        res = {"ok": False, "rows": 0, "error": f"{type(exc).__name__}: {exc}"[:300]}
    err = str(res.get("error") or "")
    fields = {"finished_at": _iso(_now()), "last_rows": int(res.get("rows") or 0),
              "last_error": err[:500], "pending": {}}
    if res.get("pending"):
        out["pending"] = True
        fields["pending"] = {"start": out["start"], "end": out["end"],
                             "note": str(res.get("note") or res.get("schedule") or "")[:200]}
        fields["last_error"] = ""
        out["ok"] = True
    elif err and not res.get("ok"):
        out["error"] = err
    else:
        rows = int(res.get("rows") or 0)
        out["rows"] = rows
        out["ok"] = True
        fields["reached"] = out["start"]
        # Nothing in thirty days: the platform has no history before this
        # window, and nightly stops here. The button still works, and rows
        # landing again clear the flag.
        fields["complete"] = rows == 0
        out["complete"] = rows == 0
        if err:
            fields["last_error"] = err[:500]
    _update(platform, **fields)
    return out


def due_nightly(today: date | None = None) -> list[str]:
    """Platforms set to nightly that are not complete and did not run today."""
    today = today or date.today()
    out = []
    for platform in PULLS:
        row = entry(platform)
        if not row.get("nightly") or row.get("complete"):
            continue
        if row.get("last_day") == today.isoformat() and not row.get("pending"):
            continue
        out.append(platform)
    return out


def run_nightly(today: date | None = None, actor: str = "scheduler") -> dict:
    """Another window for every platform set to nightly, until complete.
    A pending Trade Desk file is asked for again; a run that already
    happened today is not repeated."""
    out = {"platforms": {}, "rows": 0, "errors": {}, "complete": []}
    for platform in due_nightly(today):
        res = run_platform(platform, today=today, actor=actor)
        out["platforms"][platform] = res
        out["rows"] += res["rows"]
        if res["error"]:
            out["errors"][platform] = res["error"]
        if res["complete"]:
            out["complete"].append(platform)
    return out


# ---------------------------------------------------------------------------
# What the index prints
# ---------------------------------------------------------------------------

def rows(today: date | None = None) -> list[dict]:
    """One row per native platform, for the History card: the oldest day on
    file from any source, how far back the native history reaches, the
    ledger's state and what the buttons may do."""
    oldest_any = store.oldest_dates()
    oldest_native = store.oldest_dates(source="native")
    data = ledger()
    out = []
    for platform in PLATFORMS:
        row = dict(data.get(platform) or {})
        supported = platform in PULLS
        reached = _parse_day(row.get("reached")) or oldest_native.get(platform)
        is_running = running(platform)
        stalled = bool(row.get("started_at") and not row.get("finished_at") and not is_running)
        if not supported:
            state, label = "unsupported", "not wired"
        elif is_running:
            state, label = "running", "pulling"
        elif stalled:
            state, label = "stalled", "stalled"
        elif row.get("pending"):
            state, label = "pending", "file pending"
        elif row.get("last_error"):
            state, label = "error", "failed"
        elif row.get("complete"):
            state, label = "complete", "complete"
        elif row.get("reached"):
            state, label = "partial", "in progress"
        else:
            state, label = "none", "not started"
        start, end = (window_for(platform, today) if supported else (None, None))
        out.append({
            "platform": platform, "label": store.platform_label(platform),
            "supported": supported, "why_not": NOT_WIRED.get(platform, ""),
            "oldest_any": oldest_any.get(platform).isoformat() if oldest_any.get(platform) else None,
            "reached": reached.isoformat() if reached else None,
            "next_start": start.isoformat() if start else None,
            "next_end": end.isoformat() if end else None,
            "state": state, "state_label": label,
            "nightly": bool(row.get("nightly")), "complete": bool(row.get("complete")),
            "running": is_running, "stalled": stalled,
            "last_run_at": row.get("finished_at") or row.get("started_at"),
            "last_rows": row.get("last_rows"), "last_error": row.get("last_error") or "",
            "pending": row.get("pending") or {}, "actor": row.get("actor") or "",
        })
    return out
