"""One run per report per day, shared by both workers.

Every QA report and every report-shaped tool page re-ran its whole build on
each open. That is a year of QuickBooks invoices, a walk of the GoHighLevel
pipeline, a Knack pull and a per-row name match — for an answer that changes
when somebody edits a record, which is a few times a day, not a few times a
minute. Two people opening the Sites Billing Report in the same minute paid
for it twice; one person pressing Back paid for it again.

So a report is run **once, on the first open of the day**, and every open
after that reads what was written. Pressing **Refresh** re-runs it.

Six rules hold it up, and each is a way a cache lies.

**The day is the report's own day.** The key comes from `date.today()` — the
same clock `active_clients()` measures "this month" from and `stale_90()`
measures ninety days from. A cache on a different clock would serve
yesterday's rows under today's heading on exactly the days it matters, and
nothing on the page could say so.

**A failed run never becomes the answer.** If the build raises, or comes back
carrying `error`, `unavailable` or `measured: False`, it is **not stored** —
the previous run is served instead, with the failure named beside it. The
`knack_products` rule: a transient QuickBooks outage would otherwise freeze
"nobody is being billed for these sites" onto the page until tomorrow, which
reads exactly like a true answer. It is also what stops "QuickBooks isn't
connected yet" being cached for a day by whoever opened the report before
anyone connected it. With nothing stored to fall back on, a build that raised
is **re-raised** rather than answered with a payload of our own — half these
reports are a columns/rows table and half are not, and a caller handed the
wrong shape fails somewhere further along that says nothing about what went
wrong.

**The age travels with the rows.** Every payload carries a `cache` block —
when it ran, how long ago, and whether this open re-ran it — and the page
prints it. A cached figure with no date on it is read as today's, which is
the whole way a cache comes to mislead.

**Refresh is a POST.** A GET builds only when nothing is held for today; it
never replaces a good entry. A GET that rebuilds is one a reload, a prefetch
or a link-preview fires without anybody asking, which is the entire cost this
module exists to stop. `hub/domain_purchase.py` settled the same point.

**A write drops what it changed.** Marking an accounting request, assigning
an invoice partner, skipping a dashboard, attaching a Google property —
each of those removes a row from the report it was pressed on. Without
`invalidate()` the row stays until tomorrow and the button reads as having
done nothing, so it gets pressed again.

**A free-text search is not a cache key.** `q=acme` and `q=acm` are two files
on a 5 GB disk, and a search box types one per keystroke. Where a report
filters after it builds, the *build* is cached and the filter runs per
request (`domain_links.orphans`, `google_links.orphans`). Where it cannot be
split, `cacheable()` refuses the key and the report runs live — a search that
is slow is better than a disk that fills.

Entries are written with `durable=False`: this is a cache of something that
rebuilds itself by being asked for again, so mirroring it into the database
would cost a write per report per day for rows nobody would ever restore. A
deploy wipes the disk and the first person to open each report pays for one
run, which is what happened on every open before this existed.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import time

from . import jsonstore

# A payload bigger than this is served but not stored. Reports are read into a
# browser, so anything past a few megabytes is already a page nobody can use —
# and a runaway build must not be able to fill the data disk one day at a time.
MAX_BYTES = 8 * 1024 * 1024

# Entries whose day is older than this are swept on the next write. There is
# one file per report per parameter set, so nothing accumulates in the ordinary
# case; this is for a parameter that has stopped being asked for — a scorecard
# month that has scrolled out of the picker.
KEEP_DAYS = 7

_SAFE = re.compile(r"[^a-z0-9._-]+")


def enabled() -> bool:
    """Off with REPORT_CACHE=off, for a session that must see live numbers."""
    return str(os.environ.get("REPORT_CACHE", "")).strip().lower() not in (
        "0", "off", "false", "no")


def day_key(now: _dt.date | None = None) -> str:
    """Today, on the same clock every report measures itself from."""
    return (now or _dt.date.today()).isoformat()


def _dir() -> str:
    return jsonstore.data_dir("report_cache")


def slug(name: str, params: str = "") -> str:
    """A filename for one report and one parameter set.

    The day is deliberately *not* in it. One file per report, overwritten when
    the day rolls, so yesterday's copy is replaced rather than joining a pile
    of them.
    """
    raw = f"{name}__{params}" if params else str(name)
    out = _SAFE.sub("-", str(raw).strip().lower()).strip("-.") or "report"
    if len(out) > 110:
        # Two long names that differ past the cut would be one file, and one
        # report served under another report's name is the worst thing this
        # module could do. The digest is of the untruncated key, so it cannot
        # happen. Nothing in the Hub is anywhere near this long — it is here
        # so that adding something that is does not need noticing.
        import hashlib
        out = out[:100] + "-" + hashlib.sha1(
            raw.encode("utf-8", "replace")).hexdigest()[:10]
    return out


def _path(name: str, params: str = "") -> str:
    return os.path.join(_dir(), slug(name, params) + ".json")


def cacheable(params: str = "") -> bool:
    """Is this parameter set small enough and bounded enough to key on?

    A month, a scope, a tickbox: yes. Anything a person types: no — see the
    free-text rule above. Callers that filter after building should pass the
    build's own parameters here and apply the typed filter themselves.
    """
    p = str(params or "")
    if len(p) > 60:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9:_.,=-]*", p))


def is_answer(payload) -> bool:
    """Did this run measure anything? Anything else is not the day's answer.

    An empty row list *is* an answer — these reports are exception lists and
    zero findings is the good outcome. What is not an answer is a report that
    could not look: a raised exception, a provider that refused, a source that
    reported itself unreadable. Storing one of those freezes "we could not
    look" into the shape of "there is nothing to see".
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return False
    if payload.get("unavailable"):
        return False
    if payload.get("measured") is False:
        return False
    # `needs_qb` is the QuickBooks half of the same statement, and it was the
    # one this module's own docstring named -- "what stops 'QuickBooks isn't
    # connected yet' being cached for a day by whoever opened the report
    # before anyone connected it" -- while `is_answer()` never asked. So the
    # three billing reports stored it: somebody who connected QuickBooks at
    # 09:10 got "QuickBooks isn't configured" and the Open System Status
    # button for the rest of the day, on every one of them, and read it as
    # the connection having failed. `serve()` already knows the shape -- its
    # own comment three hundred lines down calls the connect call-to-action a
    # payload that "ran and could not measure".
    if payload.get("needs_qb"):
        return False
    return True


# ------------------------------------------------------------------ storage
def read(name: str, params: str = "") -> dict:
    """The stored entry, whatever day it is from. `{}` when there is none."""
    data = jsonstore.read_json(_path(name, params), default={}, restore=False)
    if not isinstance(data, dict) or not data.get("at"):
        return {}
    return data


def write(name: str, params: str, payload: dict, *, now: float | None = None,
          day: str = "") -> bool:
    entry = {"name": name, "params": params, "day": day or day_key(),
             "at": float(now or time.time()), "payload": payload}
    try:
        import json
        if len(json.dumps(entry, default=str)) > MAX_BYTES:
            return False
    except (TypeError, ValueError):
        return False
    ok = jsonstore.write_json(_path(name, params), entry, durable=False)
    if ok:
        _sweep()
    return bool(ok)


def invalidate(*names: str) -> int:
    """Drop every entry whose name starts with one of these.

    Called from the write paths. `invalidate("qa:")` drops every QA report,
    which is the right answer for a write nobody can attribute to one of them:
    a report re-running once is cheap, and a row that survives the button that
    was meant to remove it is not.
    """
    dropped = 0
    wanted = tuple(str(n) for n in names if str(n))
    try:
        listing = os.listdir(_dir())
    except OSError:
        return 0
    for fn in listing:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(_dir(), fn)
        data = jsonstore.read_json(path, default={}, restore=False)
        nm = str((data or {}).get("name") or "")
        if not wanted or any(nm.startswith(w) for w in wanted):
            try:
                jsonstore.delete_json(path)
                dropped += 1
            except Exception:                           # noqa: BLE001
                pass
    return dropped


def _sweep(now: _dt.date | None = None) -> int:
    """Remove entries from a day far enough back that nothing will ask again."""
    cutoff = (now or _dt.date.today()) - _dt.timedelta(days=KEEP_DAYS)
    gone = 0
    try:
        listing = os.listdir(_dir())
    except OSError:
        return 0
    for fn in listing:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(_dir(), fn)
        data = jsonstore.read_json(path, default={}, restore=False)
        try:
            day = _dt.date.fromisoformat(str((data or {}).get("day") or ""))
        except ValueError:
            continue
        if day < cutoff:
            try:
                jsonstore.delete_json(path)
                gone += 1
            except Exception:                           # noqa: BLE001
                pass
    return gone


# ------------------------------------------------------------------- serving
def _ago(minutes: float) -> str:
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} min ago"
    hours = minutes / 60
    if hours < 24:
        h = int(round(hours))
        return "an hour ago" if h == 1 else f"{h} hours ago"
    d = int(hours // 24)
    return "yesterday" if d == 1 else f"{d} days ago"


def _stamp(entry: dict, *, ran: bool, today: str, error: str = "",
           now: float | None = None) -> dict:
    """The `cache` block every served payload carries."""
    at = float(entry.get("at") or 0)
    age = max(0.0, ((now or time.time()) - at) / 60) if at else None
    day = str(entry.get("day") or "")
    same_day = bool(day) and day == today
    if ran:
        line = "Run just now."
    elif at:
        # %-I is glibc-only; lstrip("0") gives the same "9:14 AM" everywhere.
        when = _dt.datetime.fromtimestamp(at).strftime("%I:%M %p").lstrip("0")
        line = (f"Run at {when} today; this is that copy."
                if same_day else
                f"Run {_ago(age or 0)}, on {day}.")
    else:
        line = "Not run yet."
    if not same_day and at and not ran:
        line += (" Today's run has not succeeded, so these are the last "
                 "numbers that did.")
    if error:
        line += f" The attempt to re-run it failed: {error.rstrip('.')}."
    line += " Press Refresh to run it again."
    return {
        "day": day, "today": today, "same_day": same_day,
        "at": _dt.datetime.fromtimestamp(at).isoformat(timespec="seconds") if at else "",
        "age_minutes": None if age is None else round(age),
        "from_cache": not ran, "ran": ran, "error": error,
        "cached": True, "line": line,
    }


def serve(name: str, build, *, params: str = "", force: bool = False,
          now: float | None = None, today: str = "") -> dict:
    """Today's answer for one report: stored if there is one, else built.

    `build` is called with no arguments and returns the report's own payload.
    What comes back is that payload with a `cache` block added, so a caller
    never has to know whether it ran.

    `force=True` is Refresh, and is only ever reached from a POST.

    A build that raises with a stored run behind it serves that run and names
    the failure. A build that raises with nothing behind it **re-raises**: the
    caller has to see its own report fail, in its own shape.

    Two workers opening the same report in the same second will both build it.
    That is the behaviour every open had before this existed, so it is not a
    regression worth a lock — and a lock across two processes is a file
    somebody has to remember to release after a crash.
    """
    today = today or day_key()
    payload_now = float(now or time.time())

    if not enabled() or not cacheable(params):
        out = dict(build() or {})
        out["cache"] = {"cached": False, "from_cache": False, "ran": True,
                        "day": today, "today": today, "same_day": True,
                        "at": "", "age_minutes": 0, "error": "",
                        "line": ("Not cached — this report runs on every open."
                                 if not enabled() else
                                 "Searches are not cached, so this ran just now.")}
        return out

    entry = read(name, params)
    if entry and not force and str(entry.get("day") or "") == today:
        out = dict(entry.get("payload") or {})
        out["cache"] = _stamp(entry, ran=False, today=today, now=payload_now)
        return out

    try:
        fresh = build() or {}
    except Exception as exc:                            # noqa: BLE001
        if not entry:
            # Nothing to fall back on, so this is simply the report failing
            # and the caller must see it fail. Substituting a payload of our
            # own here would be a guess at the shape: half these reports are
            # a columns/rows table and the other half are not, and a caller
            # handed the wrong one raises a KeyError somewhere further along
            # that says nothing about what actually went wrong.
            raise
        why = f"{type(exc).__name__}: {exc}"[:200]
    else:
        why = ""
        if not is_answer(fresh):
            # It answered, and it answered "we could not look". Served as-is
            # when there is nothing better, never stored as the day's number.
            why = (str(fresh.get("error") or "")
                   or "the report could not measure.")
        else:
            out = dict(fresh)
            write(name, params, out, now=payload_now, day=today)
            out["cache"] = _stamp({"at": payload_now, "day": today}, ran=True,
                                  today=today, now=payload_now)
            return out

    if entry:
        # Never overwrite a good run with a failed one. Yesterday's rows with
        # the failure named beside them beat today's blank table.
        out = dict(entry.get("payload") or {})
        out["cache"] = _stamp(entry, ran=False, today=today, error=why,
                              now=payload_now)
        return out

    # It ran and could not measure, with nothing stored to fall back on. Its
    # own payload is what the page needs — the "not measured" panel, the
    # "connect QuickBooks" call to action — so it is passed straight through
    # and simply not kept.
    out = dict(fresh)
    out["cache"] = {"cached": False, "from_cache": False, "ran": True,
                    "day": today, "today": today, "same_day": True,
                    "at": "", "age_minutes": 0, "error": why,
                    "line": "This run did not produce an answer, so nothing "
                            "was kept. Press Refresh to try again."}
    return out


# --------------------------------------------------------------- inspection
def entries() -> list[dict]:
    """What is held right now — for the Diagnostics panel.

    No payloads: this is read into a page, and a report's rows carry client
    names. Names, days and sizes only.
    """
    out = []
    try:
        listing = sorted(os.listdir(_dir()))
    except OSError:
        return out
    today = day_key()
    for fn in listing:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(_dir(), fn)
        data = jsonstore.read_json(path, default={}, restore=False)
        if not isinstance(data, dict) or not data.get("at"):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        rows = data.get("payload")
        out.append({
            "name": str(data.get("name") or ""),
            "params": str(data.get("params") or ""),
            "day": str(data.get("day") or ""),
            "today": str(data.get("day") or "") == today,
            "at": _dt.datetime.fromtimestamp(float(data["at"])
                                             ).isoformat(timespec="seconds"),
            "age_minutes": round(max(0.0, (time.time() - float(data["at"])) / 60)),
            "rows": len(rows.get("rows") or []) if isinstance(rows, dict) else 0,
            "bytes": size,
        })
    out.sort(key=lambda e: (not e["today"], e["name"], e["params"]))
    return out


def state() -> dict:
    held = entries()
    return {"enabled": enabled(), "day": day_key(), "count": len(held),
            "today": sum(1 for e in held if e["today"]),
            "bytes": sum(e["bytes"] for e in held), "entries": held}
