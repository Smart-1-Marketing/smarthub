"""Domains Smart 1 bought for a client, and when each one renews.

We buy domains on behalf of clients and bill them back. The record of that has
always been in Knack — `S1M Purchase Domain for Client?` (field_2964) on the
website record, with the fee, the registrar and the renewal billing date
beside it — and nothing read it, so the only way to know what renews next month
was to open object_153 and sort it by eye. A domain that renews unbilled is
money we spent and did not invoice.

So this is the renewal calendar. Three rules it is built on:

**Only ours.** A row appears only where field_2964 says yes. Every other
website record is somebody else's domain and a list that includes them is a
list nobody trusts. `is_ours()` reads a Knack boolean *and* a yes/no dropdown,
because the field can be published either way and a `True` read as the string
"True" would be as wrong as a "Yes" read as nothing.

**The date decides the order, and a missing date is not a zero.**
`field_3298` (Domain Renewal Billing Date) sorts the table. A row with no date
cannot be placed in a month, so it goes in its own group and says so, rather
than sorting to the top as if it renewed in 1970 — an absent date has to read
as "not recorded", never as "overdue".

**This month and the next three, then search.** A renewal calendar is read to
answer "what do I have to bill now"; the rest of the year is a search, not a
scroll. Months are computed from the clock every request — a hard-coded window
is right the month it is written and quietly wrong afterwards.

## Billed

There is no "billed" field in Knack, so the tick is the Hub's, kept in
`hub/jsonstore` beside the rest of the module data. It is stored **against the
renewal billing date it was ticked for**, not against the record: a domain
renews every year, and a tick that stays green when next year's date arrives is
a confident wrong answer of exactly the kind this codebase keeps having to
undo. When the date moves on, the row reads as unbilled again and says when it
was last billed.

## The pull is nightly, not per visit

Every open of this page used to pull object_153 in full — every website
record, paged, over the wire — to answer a question whose answer changes when
somebody buys a domain, which is a few times a month. So the registry is
**snapshotted**: the scheduler re-pulls it once a night and the page renders
what is stored, which is a dictionary scan. A **Refresh** button forces the
pull for the person who has just changed something and wants to see it.

Three rules hold that up, and each is a way for a cache to lie:

**A snapshot carries when it was taken.** `cache_state()` returns the age
beside the rows and the page prints it, because a stale figure presented
without a date is read as today's.

**A failed pull never empties a good snapshot.** The `knack_products` rule:
a transient Knack failure would otherwise turn "seventy domains renew this
year" into "we have bought no domains", which is a confident wrong answer of
exactly the kind this codebase treats as worse than an error. A failed
attempt is recorded *beside* the rows it could not replace, so the page shows
yesterday's list and says the pull failed.

**Only the Knack half is cached.** The billed ticks, the month window and the
search run over the snapshot on every request, so ticking a row reads back
immediately and the calendar rolls into a new month on the day rather than at
the next pull.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from hub import jsonstore

MONTHS_AHEAD = 3

_YES = {"yes", "y", "true", "1", "checked", "on"}

# Bumped when `_purchased()` starts carrying a field it did not before. A
# snapshot written under an older number is missing that key on every row, and
# age cannot see that — the same trap `knack_products.FIELDS_VERSION` exists
# for. An older snapshot is rebuilt rather than served with a hole in it.
SNAPSHOT_VERSION = 1

# The hour (UTC) the nightly pull is due after. Around 3-4am US Eastern, which
# is when nobody is reading this page. The scheduler ticks hourly and asks
# `due_for_refresh()`, so a leader that restarted through the window still
# picks the pull up rather than skipping a day in silence.
def refresh_hour() -> int:
    try:
        return max(0, min(23, int(os.environ.get("DOMAINS_REFRESH_HOUR") or 8)))
    except ValueError:
        return 8


# When there is no snapshot at all, `report()` builds one — but only one, and
# then not again for this long. Without the cooldown a Knack that is up and
# slow costs every visitor the full timeout, one after another, which is the
# per-visit pull back again in its worst form. Per process rather than stored:
# it is a stampede guard, and the nightly job is the real path.
BUILD_RETRY_SECONDS = 300
_BUILD = {"tried": 0.0, "error": ""}

# How old a snapshot has to be before the page stops presenting it as current.
# One missed nightly window plus a margin: a pull that ran last night is fine,
# and one that has not run for a day and a half means the scheduler is not
# doing it and somebody should press Refresh.
STALE_HOURS = 36


def is_ours(row: dict) -> bool:
    """Did Smart 1 buy this domain for the client? (field_2964)"""
    raw = (row or {}).get("domain_bought_raw", (row or {}).get("domain_bought"))
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _YES


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
_DATE_PATTERNS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y",
                  "%B %d, %Y", "%b %d, %Y")


def parse_date(value) -> date | None:
    """Knack hands dates back in whatever the field was configured with.

    Returns None rather than guessing. A date we could not read is reported as
    unreadable further up; silently treating it as today would put a renewal in
    the current month that may be a year away.
    """
    if isinstance(value, dict):                 # {"date": "01/02/2026", ...}
        value = value.get("date") or value.get("iso_timestamp") or ""
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if not s:
        return None
    s = s.split("T")[0].strip()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_window(today: date | None = None, ahead: int = MONTHS_AHEAD) -> list[dict]:
    """This month and the next `ahead`, from the clock. Never hard-coded."""
    today = today or date.today()
    out, y, m = [], today.year, today.month
    for i in range(ahead + 1):
        out.append({"key": f"{y:04d}-{m:02d}",
                    "label": date(y, m, 1).strftime("%B %Y"),
                    "current": i == 0})
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


# ---------------------------------------------------------------------------
# The registry snapshot — pulled nightly, not on every visit
# ---------------------------------------------------------------------------
def _snapshot_path() -> str:
    return os.path.join(jsonstore.data_dir("domains"), "registry.json")


def _purchased(rows) -> list[dict]:
    """The registry reduced to the domains we bought — what the snapshot holds.

    Only the Knack half. No month, no billed state and no search: those are
    derived per request from the clock and from the Hub's own tick store, and
    a snapshot carrying them would freeze the calendar on the day it was taken.
    """
    out = []
    for r in rows or []:
        if not is_ours(r):
            continue
        out.append({
            "record_id": r.get("id", ""),
            "domain": r.get("domain", ""),
            "client": r.get("client", ""),
            "registrar": r.get("registrar", ""),
            "client_status": r.get("client_status", ""),
            "fee": r.get("domain_fee", 0),
            "renewal_billing_date": r.get("renewal_billing_date") or "",
            "renews": r.get("domain_renews", ""),
            "bought_on": r.get("domain_bought_on", ""),
        })
    return out


def snapshot() -> dict:
    """The stored pull, or {} if there has never been one this code can read."""
    data = jsonstore.read_json(_snapshot_path(), default={})
    if not isinstance(data, dict) or not data.get("fetched"):
        return {}
    if int(data.get("version") or 0) != SNAPSHOT_VERSION:
        # Written against an older shape of `_purchased()`. Rebuilt rather
        # than served: the rows are complete for what was being read when they
        # were written and absent for anything added since, and only one of
        # those is visible on the page.
        return {}
    return data


def invalidate() -> None:
    """Drop the snapshot so the next read pulls.

    Called by `knack_websites.forget()`, which runs after any write to
    object_153 — somebody who ticks "did we buy the domain?" on Client 360
    must not have to wait until tomorrow to see the row appear here.
    """
    try:
        jsonstore.delete_json(_snapshot_path())
    except Exception:                                   # noqa: BLE001
        pass
    # And the build cooldown with it: somebody who has just written to the
    # registry is asking for the rebuild now, not in five minutes.
    _BUILD["tried"], _BUILD["error"] = 0.0, ""


def _last_window(now: datetime) -> datetime:
    """The most recent time the nightly pull was due."""
    mark = now.replace(hour=refresh_hour(), minute=0, second=0, microsecond=0)
    return mark if mark <= now else mark - timedelta(days=1)


def due_for_refresh(now: datetime | None = None) -> bool:
    """Has the nightly window passed since the last successful pull?"""
    now = now or datetime.now(timezone.utc)
    snap = snapshot()
    if not snap:
        return True
    taken = datetime.fromtimestamp(float(snap.get("fetched") or 0), timezone.utc)
    return taken < _last_window(now)


def refresh(*, force: bool = True, now: datetime | None = None) -> dict:
    """Re-pull object_153 into the snapshot. Safe to call from anywhere.

    `force=False` is what the scheduler passes: it ticks hourly and this
    returns without touching Knack unless the nightly window has passed.
    """
    now = now or datetime.now(timezone.utc)
    if not force and not due_for_refresh(now):
        return {"ok": True, "skipped": "Not due yet.",
                "next_refresh": _next_refresh(now).isoformat(timespec="minutes")}

    try:
        from hub import knack_websites
        rows = knack_websites.rows(refresh=True)
        error = knack_websites.last_error()
    except Exception as exc:                            # noqa: BLE001
        rows, error = [], f"{type(exc).__name__}: {exc}"

    if not rows:
        # Never overwrite a good snapshot with nothing. A transient Knack
        # failure would otherwise turn a year of renewals into "we have bought
        # no domains", which reads exactly like a true answer. The failed
        # attempt is recorded beside the rows it could not replace.
        why = error or ("Knack returned no website records at all, which is "
                        "a failed read rather than an empty registry.")
        old = snapshot()
        if old:
            old["attempted"] = time.time()
            old["attempt_error"] = why
            jsonstore.write_json(_snapshot_path(), old, indent=1)
            return {"ok": False, "error": why, "kept": len(old.get("rows") or []),
                    "fetched": _iso(old.get("fetched"))}
        return {"ok": False, "error": why, "kept": 0, "fetched": ""}

    ours = _purchased(rows)
    payload = {"version": SNAPSHOT_VERSION, "fetched": time.time(),
               "scanned": len(rows), "count": len(ours), "rows": ours,
               "attempted": time.time(), "attempt_error": ""}
    # Mirrored (the jsonstore default). It is a few hundred small rows, and
    # the mirror is what stops a deploy — which wipes the disk — costing the
    # first person to open the page a full paged pull of the object.
    jsonstore.write_json(_snapshot_path(), payload, indent=1)
    try:
        from hub import audit
        audit.log("hub", "domains_refreshed", count=len(ours),
                  scanned=len(rows))
    except Exception:                                   # noqa: BLE001
        pass
    # Pressing Refresh after fixing whatever was wrong must work at once,
    # rather than being held off by the cooldown the failures set.
    _BUILD["tried"], _BUILD["error"] = 0.0, ""
    return {"ok": True, "count": len(ours), "scanned": len(rows),
            "fetched": _iso(payload["fetched"])}


def _iso(epoch) -> str:
    try:
        return datetime.fromtimestamp(float(epoch or 0), timezone.utc
                                      ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def _next_refresh(now: datetime) -> datetime:
    return _last_window(now) + timedelta(days=1)


def cache_state(now: datetime | None = None) -> dict:
    """When this was last pulled, and whether it can still be called current.

    Printed on the page beside the table. A cached figure with no date on it
    is read as today's, which is the whole way a cache comes to mislead.
    """
    now = now or datetime.now(timezone.utc)
    snap = snapshot()
    if not snap:
        return {"fetched": "", "age_minutes": None, "stale": False,
                "measured": False, "scanned": 0,
                "next_refresh": _next_refresh(now).isoformat(timespec="minutes"),
                "hour": refresh_hour(), "attempt_error": "",
                "line": "Not pulled yet."}
    age = (now.timestamp() - float(snap.get("fetched") or 0)) / 60
    stale = age > STALE_HOURS * 60
    err = str(snap.get("attempt_error") or "")
    line = f"Registry pulled {_ago(age)}."
    if stale:
        line += (" That is older than a night, so the scheduled pull is not "
                 "running — press Refresh.")
    if err:
        line += f" The last attempt to refresh it failed: {err}"
    return {"fetched": _iso(snap.get("fetched")), "age_minutes": round(age),
            "stale": stale, "measured": True,
            "scanned": int(snap.get("scanned") or 0),
            "next_refresh": _next_refresh(now).isoformat(timespec="minutes"),
            "hour": refresh_hour(), "attempt_error": err,
            "attempted": _iso(snap.get("attempted")), "line": line}


def _ago(minutes: float) -> str:
    if minutes < 2:
        return "just now"
    if minutes < 90:
        return f"{round(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} hours ago"
    return f"{round(hours / 24)} days ago"


# ---------------------------------------------------------------------------
# The billed tick
# ---------------------------------------------------------------------------
def _store_path() -> str:
    return os.path.join(jsonstore.data_dir("domains"), "billed.json")


def billed_store() -> dict:
    rows = jsonstore.read_json(_store_path(), default={})
    return rows if isinstance(rows, dict) else {}


def set_billed(record_id: str, billed: bool, *, for_date: str = "",
               actor: str = "") -> dict:
    """Tick or untick one row, against the date it is being billed for."""
    rid = str(record_id or "").strip()
    if not rid:
        return {"ok": False, "error": "No website record id."}
    rows = billed_store()
    if billed:
        rows[rid] = {"billed": True, "for_date": str(for_date or "")[:40],
                     "by": str(actor or "")[:120],
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        rows.pop(rid, None)
    jsonstore.write_json(_store_path(), rows, indent=1)
    return {"ok": True, "record_id": rid, "billed": bool(billed),
            "row": rows.get(rid)}


def _billed_state(rid: str, renewal_raw: str, store: dict) -> dict:
    """Whether this row counts as billed for the renewal date it shows now."""
    hit = store.get(str(rid)) or {}
    if not hit.get("billed"):
        return {"billed": False, "note": ""}
    was = str(hit.get("for_date") or "")
    now = str(renewal_raw or "")
    if was and now and was != now:
        # The renewal rolled. This is a new charge, so it is unbilled again —
        # and says when the last one was, rather than losing the history.
        return {"billed": False,
                "note": f"Billed for {was}; this renewal date is new."}
    return {"billed": True, "by": hit.get("by", ""), "at": hit.get("at", ""),
            "note": f"Ticked by {hit.get('by') or 'someone'}"
                    + (f" on {str(hit.get('at'))[:10]}" if hit.get("at") else "")}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def report(q: str = "", today: date | None = None, *,
           build: bool = True) -> dict:
    """The purchased-domain table: this month, the next three, then the rest.

    Reads the nightly snapshot rather than Knack. `build=False` refuses even
    the one first-run pull, so a caller that must never reach the network —
    a test, or a status panel — can ask what is stored and nothing else.
    """
    today = today or date.today()
    snap = snapshot()
    build_error = ""
    if not snap and build:
        # No snapshot at all: the first open after a deploy onto a fresh disk
        # with no mirror, or the first open ever. One pull, then the nightly
        # job has it. Age is *not* a reason to pull here — that is the whole
        # point of the change, and a page that re-pulls whenever it dislikes
        # the age is the per-visit pull wearing a cache.
        if _BUILD["error"] and time.time() - _BUILD["tried"] < BUILD_RETRY_SECONDS:
            build_error = _BUILD["error"]      # still inside the cooldown
        else:
            out = refresh(force=True)
            if out.get("ok"):
                _BUILD["tried"], _BUILD["error"] = 0.0, ""
            else:
                _BUILD["tried"], _BUILD["error"] = (time.time(),
                                                    str(out.get("error") or ""))
            build_error = _BUILD["error"]
            snap = snapshot()

    state = cache_state()
    store = billed_store()
    ours, undated, unreadable = [], [], 0
    for r in snap.get("rows") or []:
        raw = r.get("renewal_billing_date") or ""
        when = parse_date(raw)
        if raw and not when:
            unreadable += 1
        row = {**r,
               "renewal_billing_iso": when.isoformat() if when else "",
               "month": _month_key(when) if when else "",
               **_billed_state(r.get("record_id", ""), raw, store)}
        (ours if when else undated).append(row)

    ours.sort(key=lambda x: (x["renewal_billing_iso"], x["domain"]))
    undated.sort(key=lambda x: x["domain"])

    window = month_window(today)
    keys = {w["key"] for w in window}
    groups = []
    for w in window:
        items = [x for x in ours if x["month"] == w["key"]]
        groups.append({**w, "rows": items, "count": len(items),
                       "fee_total": round(sum(x["fee"] or 0 for x in items), 2),
                       "unbilled": sum(1 for x in items if not x["billed"])})

    rest = [x for x in ours if x["month"] not in keys]
    needle = str(q or "").strip().lower()
    matches = []
    if needle:
        matches = [x for x in ours + undated
                   if needle in (x["domain"] or "").lower()
                   or needle in (x["client"] or "").lower()
                   or needle in (x["registrar"] or "").lower()]

    note = ("Every domain Smart 1 bought for a client, by the renewal billing "
            "date Knack holds (field_3298). Only records where “S1M Purchase "
            "Domain for Client?” is yes are listed.")
    if state["measured"]:
        note += " " + state["line"]
    if build_error:
        # No snapshot and the pull to make one failed. Empty because we could
        # not look, never because there is nothing — the two must not read
        # alike, and only the second means we have bought no domains.
        note = ("The Knack website registry could not be read (" + build_error +
                "), and there is no earlier pull to fall back on, so this is "
                "empty rather than complete.")
    elif not state["measured"]:
        note += (" Nothing has been pulled yet, so this is not a total. "
                 "Press Refresh.")
    if unreadable:
        note += (f" {unreadable} record(s) carry a renewal billing date this "
                 "could not read; they are with the undated ones rather than "
                 "being placed in a month they may not belong to.")

    return {
        "today": today.isoformat(),
        "groups": groups,
        "later": rest, "later_count": len(rest),
        "undated": undated, "undated_count": len(undated),
        "results": matches, "q": q,
        "total": len(ours) + len(undated),
        "billed_count": sum(1 for x in ours + undated if x["billed"]),
        "fee_total": round(sum(x["fee"] or 0 for x in ours + undated), 2),
        "read_error": build_error,
        "cache": state,
        "note": note,
    }
