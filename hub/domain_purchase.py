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

**Only the Knack half is cached this way.** The billed ticks, the month window
and the search run over the snapshot on every request, so ticking a row reads
back immediately and the calendar rolls into a new month on the day rather than
at the next pull. QuickBooks is cached separately, by
`hub/domain_renewals.py`, and the one Refresh button pulls both — a refresh
that pulled one of the two would leave a fresh timestamp over a stale answer
to the question the page is actually asked.

## Billed is what QuickBooks invoiced

There is no "billed" field in Knack. There is one in QuickBooks, though not
under that name: every renewal we invoice is a line carrying the product
**Website Domain Renewal**, and `hub/domain_renewals.py` reads those and
matches each one back to a website record. So "billed" is an observation with
evidence behind it — the invoice number, the date, the amount and who it went
to — and the row says which invoice said so.

The Hub's own tick survives beside it, because QuickBooks is not the only way
a renewal gets paid for and a charge whose description names nobody may never
match. A QuickBooks charge is held to the same rule the tick is: it counts for
the renewal it is **near** (`domain_renewals.WINDOW_DAYS`), so last year's
invoice can never mark this year's renewal billed. `billed_source` is always
said out loud — **quickbooks**, **hub**, or neither — and where the two
disagree both are printed rather than one quietly winning.

## Billed is this month's question; do-not-renew is next month's

Asking "did we bill this?" about a renewal three months out is asking about
something that has not happened. What is worth deciding that far ahead is the
opposite one — whether it should renew at all — so the current month carries
the billed tick and every later month carries **do not renew**.

That flag is deliberately *not* held against the renewal date the way the
billed tick is. "Do not renew" is a decision about the domain, and if the
renewal date later rolls forward the domain renewed anyway — which is a
finding, not a flag to quietly clear. `dnr_state()` keeps the mark and says
the date has moved, and `do_not_renew_report()` lists exactly those.

## The year-end reconciliation

`year_to_date()` asks the two questions nobody could ask before, in both
directions: renewals that came due this year with **no invoice** behind them
(money we spent and did not bill), and Website Domain Renewal charges that
match **no record here** (money we billed for a domain this Hub has never
heard of, or one whose description nobody can join up). Neither is a number on
its own — every row carries what it was matched on and what it was not, so the
answer can be acted on rather than merely counted.
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
# 2: the media partner on each row, and the slim index of the records we did
#    *not* buy, which is what tells a renewal charge billed against somebody
#    else's domain from one billed against a domain nothing here carries.
SNAPSHOT_VERSION = 2

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
            # The media partner the site is filed under. On the snapshot
            # because a renewal is chased through the partner rather than the
            # client — the invoice goes to them.
            "partner": r.get("media_partner", ""),
            "registrar": r.get("registrar", ""),
            "client_status": r.get("client_status", ""),
            "fee": r.get("domain_fee", 0),
            "renewal_billing_date": r.get("renewal_billing_date") or "",
            "renews": r.get("domain_renews", ""),
            "bought_on": r.get("domain_bought_on", ""),
        })
    return out


def _others(rows) -> list[dict]:
    """The rest of the registry, reduced to what a charge can be matched on.

    Domains we did not buy are not on this page and never will be. They are on
    the *snapshot* because a Website Domain Renewal charge matched to one of
    them is a different finding from a charge matched to nothing at all —
    either the invoice is wrong or “S1M Purchase Domain for Client?” is — and
    without them the second answer swallows the first in silence.

    Five fields, no dates and no fees: this is an index, not a second copy of
    the registry.
    """
    out = []
    for r in rows or []:
        if is_ours(r):
            continue
        if not (r.get("domain") or r.get("client")):
            continue
        out.append({"record_id": r.get("id", ""),
                    "domain": r.get("domain", ""),
                    "client": r.get("client", ""),
                    "partner": r.get("media_partner", "")})
    return out


def match_rows(snap: dict | None = None) -> list[dict]:
    """The snapshot in the shape `domain_renewals.match_charges()` reads.

    An adapter rather than a second registry read: the whole point of the
    nightly pull is that nothing on this page reaches Knack, and a matcher
    that called `knack_websites.rows()` would put the per-visit pull straight
    back. `domain_bought_raw` is set explicitly on both halves, so `is_ours()`
    answers off the snapshot exactly as it would off the record.
    """
    snap = snapshot() if snap is None else snap
    out = []
    for r in snap.get("rows") or []:
        out.append({"id": r.get("record_id", ""), "domain": r.get("domain", ""),
                    "client": r.get("client", ""),
                    "client_name": r.get("client", ""), "organization": "",
                    "media_partner": r.get("partner", ""),
                    "domain_bought_raw": True})
    for r in snap.get("others") or []:
        out.append({"id": r.get("record_id", ""), "domain": r.get("domain", ""),
                    "client": r.get("client", ""),
                    "client_name": r.get("client", ""), "organization": "",
                    "media_partner": r.get("partner", ""),
                    "domain_bought_raw": False})
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
               "others": _others(rows),
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
# Do not renew
# ---------------------------------------------------------------------------
def _dnr_path() -> str:
    return os.path.join(jsonstore.data_dir("domains"), "do_not_renew.json")


def dnr_store() -> dict:
    rows = jsonstore.read_json(_dnr_path(), default={})
    return rows if isinstance(rows, dict) else {}


def set_do_not_renew(record_id: str, on: bool, *, for_date: str = "",
                     reason: str = "", actor: str = "") -> dict:
    """Mark one domain as not to be renewed, or clear the mark."""
    rid = str(record_id or "").strip()
    if not rid:
        return {"ok": False, "error": "No website record id."}
    rows = dnr_store()
    if on:
        rows[rid] = {"do_not_renew": True,
                     "for_date": str(for_date or "")[:40],
                     "reason": str(reason or "")[:400],
                     "by": str(actor or "")[:120],
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        rows.pop(rid, None)
    jsonstore.write_json(_dnr_path(), rows, indent=1)
    return {"ok": True, "record_id": rid, "do_not_renew": bool(on),
            "row": rows.get(rid)}


def dnr_state(rid: str, renewal_raw: str, store: dict) -> dict:
    """Whether this row is marked do-not-renew, and whether it renewed anyway.

    Unlike the billed tick this is **not** retired when the renewal date rolls.
    Somebody said this domain should not renew; a renewal date that has since
    moved on means it renewed regardless, and clearing the mark would delete
    the only evidence of that. It is kept, and said.
    """
    hit = store.get(str(rid)) or {}
    if not hit.get("do_not_renew"):
        return {"do_not_renew": False, "dnr_note": "", "renewed_anyway": False}
    was = str(hit.get("for_date") or "")
    now = str(renewal_raw or "")
    who = hit.get("by") or "someone"
    when = f" on {str(hit.get('at'))[:10]}" if hit.get("at") else ""
    note = f"Marked do not renew by {who}{when}."
    if hit.get("reason"):
        note += f" Reason: {hit['reason']}"
    moved = bool(was and now and was != now)
    if moved:
        note += (f" It was marked against {was} and the renewal billing date "
                 f"now reads {now} — so it appears to have renewed anyway.")
    return {"do_not_renew": True, "dnr_note": note, "renewed_anyway": moved,
            "dnr_by": hit.get("by", ""), "dnr_at": hit.get("at", ""),
            "dnr_for_date": was, "dnr_reason": hit.get("reason", "")}


# ---------------------------------------------------------------------------
# The QuickBooks side
# ---------------------------------------------------------------------------
def _quickbooks(year: int, refresh_qb: bool = False) -> dict:
    """The Website Domain Renewal charges for one year. Never raises."""
    try:
        from hub import domain_renewals
        return domain_renewals.charges(year, refresh=refresh_qb)
    except Exception as exc:                            # noqa: BLE001
        return {"lines": [], "error": f"QuickBooks could not be read "
                                      f"({type(exc).__name__}: {exc}).",
                "fetched_at": "", "age_hours": None, "cached": False,
                "item": ""}


def _matched(rows: list[dict], lines: list[dict]) -> list[dict]:
    try:
        from hub import domain_renewals
        return domain_renewals.match_charges(lines, rows)
    except Exception:                                   # noqa: BLE001
        return []


def _by_record(matched: list[dict]) -> dict:
    try:
        from hub import domain_renewals
        return domain_renewals.by_record(matched)
    except Exception:                                   # noqa: BLE001
        return {}


def _charge_brief(c: dict) -> dict:
    """One charge as a row on screen shows it."""
    return {"key": c.get("key", ""), "invoice_id": c.get("invoice_id", ""),
            "doc_number": c.get("doc_number", ""), "date": c.get("date", ""),
            "amount": c.get("amount", 0), "customer": c.get("customer", ""),
            "link": c.get("link", ""), "description": c.get("description", ""),
            "matched_on": c.get("matched_on", ""),
            "confidence": c.get("confidence", ""), "why": c.get("why", "")}


def _quickbooks_note(qb: dict) -> str:
    """What to say about the QuickBooks half, including when it said nothing."""
    if qb.get("error"):
        return (qb["error"] + " Billed is therefore whatever has been ticked "
                "here, and nothing more — it is not a statement about what "
                "was invoiced.")
    item = qb.get("item") or "Website Domain Renewal"
    when = ""
    if qb.get("fetched_at"):
        age = qb.get("age_hours")
        when = (f" Read from QuickBooks {qb['fetched_at'][:16].replace('T', ' ')}"
                + (f" ({age}h ago)" if age else "") + ".")
    return (f"Billed is read from QuickBooks: every invoice line carrying the "
            f"product “{item}”, matched back to a domain on the domain in its "
            f"description, or failing that on the client named in it.{when}")


def _billing_evidence(row: dict, charges: list[dict], when: date | None,
                      qb_error: str = "") -> dict:
    """What QuickBooks says about this renewal, folded in with the Hub's tick.

    Three rules, each of which is a way to be confidently wrong:

    * **A charge counts for the renewal it is near.** A domain renews every
      year and is invoiced once; without the window, last year's charge marks
      this year's renewal billed.
    * **A probable match does not mark anything billed.** A near name is a
      suggestion, and a suggestion that ticks a box is a fact nobody agreed
      to. It is shown, named as unconfirmed, and left out of the count.
    * **Where the tick and QuickBooks disagree, both are printed.** A tick
      with no invoice behind it may be a renewal paid another way, or it may
      be a renewal nobody billed — and only a person can tell which.

    And a QuickBooks we could not read never produces "no charge matches this
    renewal". That sentence is a finding; a failed read is not one, and
    printing the first when the second happened is the confident wrong answer
    this whole file is arranged against.
    """
    try:
        from hub.domain_renewals import within_window
    except Exception:                                   # noqa: BLE001
        return {"charges": [], "charges_year": 0, "maybe_charges": [],
                "billed_source": "hub" if row.get("billed") else "",
                "billed_to": ""}

    in_window = [c for c in charges if within_window(c.get("date"), when)]
    firm = [c for c in in_window
            if c.get("confidence") in ("confirmed", "exact")]
    maybe = [c for c in in_window if c.get("confidence") == "probable"]

    hub_tick = bool(row.get("billed"))
    hub_note = row.get("note") or ""
    billed = bool(firm) or hub_tick
    source = "quickbooks" if firm else ("hub" if hub_tick else "")

    parts = []
    if firm:
        c = firm[0]
        doc = c.get("doc_number") or c.get("invoice_id") or "?"
        parts.append(f"Invoice {doc}"
                     + (f" to {c['customer']}" if c.get("customer") else "")
                     + (f" on {c['date']}" if c.get("date") else "")
                     + f" · ${float(c.get('amount') or 0):.2f}.")
        if len(firm) > 1:
            parts.append(f"{len(firm)} charges match this renewal.")
        if hub_tick and hub_note:
            parts.append("Also ticked here — " + hub_note.rstrip("."))
    elif hub_tick:
        parts.append(hub_note or "Ticked here.")
        parts.append("QuickBooks could not be read, so this is the tick alone "
                     "and nothing has been checked against it."
                     if qb_error else
                     "No Website Domain Renewal charge in QuickBooks matches "
                     "this renewal, so this is the tick and not an invoice.")
    elif hub_note:
        # A tick retired because the renewal date rolled still has to say what
        # it was ticked for, or the history goes with it.
        parts.append(hub_note)
    if maybe:
        c = maybe[0]
        doc = c.get("doc_number") or c.get("invoice_id") or "?"
        parts.append(f"Invoice {doc} may be this renewal — {c.get('why', '')} "
                     f"It is not counted as billed until it is confirmed.")

    return {
        "billed": billed,
        "billed_source": source,
        "billed_to": (firm[0].get("customer") if firm else ""),
        "charges": [_charge_brief(c) for c in in_window],
        "maybe_charges": [_charge_brief(c) for c in maybe],
        "charges_year": len(charges),
        "note": " ".join(p for p in parts if p).strip(),
    }


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
    qb = _quickbooks(today.year)
    charges_by_record = _by_record(_matched(match_rows(snap),
                                            qb.get("lines") or []))
    store = billed_store()
    dnr = dnr_store()
    ours, undated, unreadable = [], [], 0
    for r in snap.get("rows") or []:
        raw = r.get("renewal_billing_date") or ""
        when = parse_date(raw)
        if raw and not when:
            unreadable += 1
        rid = r.get("record_id", "")
        row = {**r,
               "renewal_billing_iso": when.isoformat() if when else "",
               "month": _month_key(when) if when else "",
               **_billed_state(rid, raw, store),
               **dnr_state(rid, raw, dnr)}
        row.update(_billing_evidence(row, charges_by_record.get(rid) or [],
                                     when, qb_error=qb.get("error") or ""))
        (ours if when else undated).append(row)

    ours.sort(key=lambda x: (x["renewal_billing_iso"], x["domain"]))
    undated.sort(key=lambda x: x["domain"])

    window = month_window(today)
    keys = {w["key"] for w in window}
    groups = []
    for w in window:
        items = [x for x in ours if x["month"] == w["key"]]
        groups.append({
            **w,
            # This month is a billing question; a month that has not happened
            # is a renew-or-not question. The column follows.
            "column": "billed" if w["current"] else "do_not_renew",
            "rows": items, "count": len(items),
            "fee_total": round(sum(x["fee"] or 0 for x in items), 2),
            "unbilled": sum(1 for x in items if not x["billed"]),
            "dnr_count": sum(1 for x in items if x["do_not_renew"])})

    rest = [x for x in ours if x["month"] not in keys]
    needle = str(q or "").strip().lower()
    matches = []
    if needle:
        matches = [x for x in ours + undated
                   if needle in (x["domain"] or "").lower()
                   or needle in (x["client"] or "").lower()
                   or needle in (x["partner"] or "").lower()
                   or needle in (x["registrar"] or "").lower()]

    note = ("Every domain Smart 1 bought for a client, by the renewal billing "
            "date on its website record. Only records where we are recorded "
            "as having bought the domain are listed.")
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
        "dnr_total": sum(1 for x in ours + undated if x["do_not_renew"]),
        "fee_total": round(sum(x["fee"] or 0 for x in ours + undated), 2),
        "read_error": build_error,
        "cache": state,
        "quickbooks": {k: qb.get(k) for k in
                       ("error", "fetched_at", "age_hours", "cached", "item")},
        "quickbooks_charges": len(qb.get("lines") or []),
        "quickbooks_note": _quickbooks_note(qb),
        "note": note,
    }


# ---------------------------------------------------------------------------
# Do not renew — the report
# ---------------------------------------------------------------------------
def do_not_renew_report(today: date | None = None) -> dict:
    """Every domain somebody has said should not renew.

    Two lists, because they call for opposite actions. **Standing** is the
    queue: these have not renewed yet and somebody has to cancel them at the
    registrar. **Renewed anyway** is the exception report: the mark was made
    against one renewal billing date and the record now carries a later one,
    so the domain renewed after we decided it should not — which is a charge
    to chase, not a queue item.

    A mark against a record that is no longer in the snapshot is named too,
    rather than dropped: it is the one case where the list quietly shrinks.
    """
    today = today or date.today()
    snap = snapshot()
    by_id = {str(r.get("record_id") or ""): r for r in snap.get("rows") or []}
    store = dnr_store()
    state = cache_state()

    standing, renewed, missing = [], [], []
    for rid, hit in sorted(store.items(), key=lambda kv: str(kv[1].get("at"))):
        rec = by_id.get(str(rid))
        if rec is None:
            missing.append({"record_id": rid, "for_date": hit.get("for_date", ""),
                            "by": hit.get("by", ""), "at": hit.get("at", ""),
                            "reason": hit.get("reason", ""),
                            "domain": hit.get("domain", "")})
            continue
        raw = rec.get("renewal_billing_date") or ""
        when = parse_date(raw)
        row = {**rec,
               "renewal_billing_iso": when.isoformat() if when else "",
               **dnr_state(rid, raw, store)}
        (renewed if row.get("renewed_anyway") else standing).append(row)

    standing.sort(key=lambda x: (x["renewal_billing_iso"] or "9999", x["domain"]))
    renewed.sort(key=lambda x: (x["renewal_billing_iso"] or "9999", x["domain"]))

    note = ("Domains somebody has marked as not to renew. The mark is kept "
            "against the renewal billing date it was made for and is never "
            "cleared automatically — a renewal date that has since moved on "
            "means the domain renewed anyway, which is the second list rather "
            "than a mark quietly disappearing.")
    if state["measured"]:
        note += " " + state["line"]
    else:
        # Without a snapshot every mark looks orphaned. That is a failed read,
        # not fourteen deleted records, and the two must not read alike.
        note += (" Nothing has been pulled from the registry yet, so every "
                 "mark below reads as having no record behind it. Press "
                 "Refresh on the renewal calendar.")
    return {"today": today.isoformat(), "standing": standing,
            "standing_count": len(standing),
            "renewed_anyway": renewed, "renewed_count": len(renewed),
            "orphaned": missing, "orphaned_count": len(missing),
            "total": len(store), "cache": state,
            "measured": bool(state["measured"]), "note": note}


# ---------------------------------------------------------------------------
# Year to date: the two directions of the same question
# ---------------------------------------------------------------------------
def year_to_date(year: int | None = None, today: date | None = None) -> dict:
    """What renewed without being billed, and what was billed without a record.

    Both directions, because each hides a different kind of money:

    * **Not billed** — a renewal came due this year and no Website Domain
      Renewal charge in QuickBooks matches it. That is a domain we paid a
      registrar for and did not invoice. Domains marked do-not-renew are
      listed apart, since not billing one of those is correct.
    * **Billed with no record here** — a charge went out and nothing in the
      website registry carries that domain or that client. Either the record
      is missing, or the description names the business in a way nothing can
      join up, and the row shows what it was matched on so a person can say
      which and attach it.

    Neither list is presented as a total when either side failed to read: a
    registry that has never been pulled makes every charge look unrecorded,
    and a QuickBooks that would not answer makes every renewal look unbilled.
    Both travel with the numbers, and `measured` is false while either holds.

    Reads the two caches and pulls neither. POST /api/domains/refresh is the
    one control on this tool that reaches a provider.
    """
    today = today or date.today()
    yr = int(year or today.year)
    start = date(yr, 1, 1)
    end = min(today, date(yr, 12, 31))

    snap = snapshot()
    state = cache_state()
    qb = _quickbooks(yr)
    matched = _matched(match_rows(snap), qb.get("lines") or [])
    by_rec = _by_record(matched)
    dnr = dnr_store()
    store = billed_store()

    not_billed, on_purpose, reconciled = [], [], []
    # Every purchased domain, so the page can offer a real record to attach an
    # unmatched charge to. A searchable list of records and never a text box,
    # for the reason `hub/client_key.py` gives at length: a typed name that
    # matches nothing files the charge against a domain nothing joins to and
    # still reads as a clean save.
    records = []
    undatable = 0
    for r in snap.get("rows") or []:
        rid = str(r.get("record_id") or "")
        records.append({"record_id": rid, "domain": r.get("domain", ""),
                        "client": r.get("client", ""),
                        "partner": r.get("partner", ""),
                        "renewal_billing_date":
                            r.get("renewal_billing_date", "")})
        raw = r.get("renewal_billing_date") or ""
        when = parse_date(raw)
        if not when:
            undatable += 1
            continue
        if not (start <= when <= end):
            continue
        row = {**r, "renewal_billing_iso": when.isoformat(),
               **_billed_state(rid, raw, store),
               **dnr_state(rid, raw, dnr)}
        row.update(_billing_evidence(row, by_rec.get(rid) or [], when,
                                     qb_error=qb.get("error") or ""))
        if row["charges"] and row["billed_source"] == "quickbooks":
            reconciled.append(row)
        elif row["do_not_renew"]:
            on_purpose.append(row)
        else:
            not_billed.append(row)

    unrecorded, mismatched = [], []
    for c in matched:
        if c.get("confidence") == "probable":
            # A near name is a suggestion. Until a person confirms it this
            # charge has no record here — counting it as reconciled would be
            # the Hub agreeing with its own guess, and the row would then
            # disappear from both directions of this report at once.
            unrecorded.append(_orphan_charge(c))
        elif not c.get("record_id"):
            unrecorded.append(_orphan_charge(c))
        elif c.get("is_ours") is False:
            # Matched, but to a record whose registry entry does not say we
            # bought this domain. Billed either way — the disagreement is the
            # finding, and flattening it would lose the only sign of it.
            mismatched.append(_orphan_charge(c))

    suggested = [c for c in unrecorded if c["confidence"] == "probable"]
    unrecorded.sort(key=lambda c: (c["confidence"] != "probable", c["date"]))
    not_billed.sort(key=lambda x: x["renewal_billing_iso"])
    on_purpose.sort(key=lambda x: x["renewal_billing_iso"])

    item = qb.get("item") or "Website Domain Renewal"
    note = (f"1 January {yr} to {end.isoformat()}. Renewals are read from the "
            f"nightly snapshot of the Knack website registry and charges from "
            f"QuickBooks — the product “{item}” — and matched on the domain "
            f"in each line description, or on the client named in it.")
    problems = []
    if not state["measured"]:
        problems.append("The website registry has never been pulled, so every "
                        "charge below reads as unrecorded whether it is or "
                        "not. Press Refresh on the renewal calendar.")
    elif state.get("stale"):
        problems.append(state["line"])
    if qb.get("error"):
        problems.append(qb["error"] + " Every renewal below therefore reads as "
                        "unbilled; that is not a finding, it is a failed read.")
    if undatable:
        problems.append(f"{undatable} purchased domain(s) carry no readable "
                        f"renewal billing date, so they cannot be placed in "
                        f"{yr} at all and are in neither list.")

    return {
        "year": yr, "from": start.isoformat(), "to": end.isoformat(),
        "not_billed": not_billed, "not_billed_count": len(not_billed),
        "not_billed_fees": round(sum(x["fee"] or 0 for x in not_billed), 2),
        "not_renewing": on_purpose, "not_renewing_count": len(on_purpose),
        "reconciled_count": len(reconciled),
        "unrecorded": unrecorded, "unrecorded_count": len(unrecorded),
        "suggested_count": len(suggested),
        "unrecorded_total": round(sum(c["amount"] or 0 for c in unrecorded), 2),
        "not_bought": mismatched, "not_bought_count": len(mismatched),
        "charges_total": len(matched),
        "charges_value": round(sum(float(c.get("amount") or 0)
                                   for c in matched), 2),
        "undatable": undatable,
        "records": sorted(records, key=lambda x: (x["domain"], x["client"])),
        "records_count": len(records),
        "cache": state,
        "quickbooks": {k: qb.get(k) for k in
                       ("error", "fetched_at", "age_hours", "cached", "item")},
        "problems": problems,
        "measured": not problems,
        "note": note,
    }


def _orphan_charge(c: dict) -> dict:
    """A charge on the reconciliation list, with what it was matched on."""
    return {**_charge_brief(c),
            "parsed_domain": (c.get("parsed") or {}).get("domain", ""),
            "parsed_name": (c.get("parsed") or {}).get("name", ""),
            "record_id": c.get("record_id", ""),
            "client": c.get("client", ""),
            "record_domain": c.get("record_domain", ""),
            "partner": c.get("partner", ""),
            "is_ours": c.get("is_ours"),
            "candidates": c.get("candidates") or []}


# ---------------------------------------------------------------------------
# One record, for Client 360
# ---------------------------------------------------------------------------
def status_for_record(record_id: str, today: date | None = None) -> dict:
    """The renewal standing of one website record.

    Client 360 draws this under the domain record, so somebody looking at a
    client can see what their domain costs, whether this year's renewal has
    been invoiced and which invoice did it — without opening the renewal
    calendar and finding the row again.

    Returns `applies: False` for a domain we did not buy, rather than an empty
    panel: "we do not bill this one", "nothing has been pulled yet" and
    "nothing has been recorded" are three different answers and the panel says
    which. Reads the snapshot and never pulls — this runs on every open of a
    client record.
    """
    today = today or date.today()
    rid = str(record_id or "").strip()
    if not rid:
        return {"applies": False, "reason": "No website record."}

    snap = snapshot()
    state = cache_state()
    if not state["measured"]:
        return {"applies": False, "record_id": rid, "cache": state,
                "reason": "The website registry has not been pulled yet, so "
                          "the renewal standing is not measured."}

    rec = next((r for r in snap.get("rows") or []
                if str(r.get("record_id") or "") == rid), None)
    if rec is None:
        known = any(str(r.get("record_id") or "") == rid
                    for r in snap.get("others") or [])
        return {"applies": False, "record_id": rid, "cache": state,
                "reason": ("Smart 1 did not buy this domain, so there is no "
                           "renewal for us to bill." if known else
                           "No purchased domain on the website registry "
                           "carries this record.")}

    raw = rec.get("renewal_billing_date") or ""
    when = parse_date(raw)
    qb = _quickbooks(today.year)
    by_rec = _by_record(_matched(match_rows(snap), qb.get("lines") or []))
    row = {**rec,
           "renewal_billing_iso": when.isoformat() if when else "",
           **_billed_state(rid, raw, billed_store()),
           **dnr_state(rid, raw, dnr_store())}
    row.update(_billing_evidence(row, by_rec.get(rid) or [], when,
                                 qb_error=qb.get("error") or ""))
    row["applies"] = True
    row["this_month"] = bool(when and _month_key(when) == _month_key(today))
    row["quickbooks_error"] = qb.get("error") or ""
    row["dated"] = bool(when)
    row["cache"] = state
    if not when:
        row["not_measured"] = ("No renewal billing date is recorded, so this "
                               "renewal cannot be placed in a month and "
                               "billed is not a question that can be answered "
                               "for it.")
    return row
