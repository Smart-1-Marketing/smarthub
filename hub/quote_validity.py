"""How long a quoted price stands, and what happens the day after.

## Why this exists

`VALID_STATUSES` in the Proposal Builder has carried **Expired** since the day
it was written -- with a badge color, a ⏰ in the status picker, and nothing
anywhere that sets it. It was reachable only by a rep remembering to click it,
which in practice meant never.

That was cosmetic right up until the client got a link. `/sales/builder/p/
<token>` lets a client accept a proposal themselves, and the accept route
checks that the link is live, that the reader is not staff, and that this
revision has not already been accepted -- and nothing at all about *when the
quote was written*. So a client can open a link from March in September and
accept it at March's rates, and the Hub files it as a clean acceptance with
their name and the timestamp against it. The rate card moves, the sell
multiplier moves, and the insertion order is then built from a quote nobody
would write today.

A validity window is the ordinary answer, and it is the client's protection as
much as ours: a price with no end on it is a price nobody can plan around.

## The rules

  * **Only a document the client was given can expire.** A Draft was never
    sent -- an old one is abandoned, which is a different word and a different
    thing to do about it. An Approved quote is one they said yes to, and
    expiring an acceptance would take back an agreement. Converted has an
    insertion order behind it. So this applies to **Sent** and to nothing
    else, and `effective_status()` never returns anything but the stored
    status for the other five.
  * **Derived on read, never stored.** The `hub/creative_evergreen.py` rule:
    there are two gunicorn workers, so a status written by whichever one ran a
    sweep is a status the other one disagrees with -- and a stored `Expired`
    would survive an extension, which is a quote reading as dead on the one
    screen a rep would go to revive it.
  * **The clock starts when the client could first see it**, which is the send
    rather than the writing: a quote drafted in March and sent in April stands
    for thirty days from April. Which date answered is carried and printed,
    because "thirty days from when I sent it" and "thirty days from when I
    wrote it" are different promises and the client is holding one of them.
  * **A quote with no date at all is not measured**, never expired. An absent
    timestamp reading as "expired today" is the confident zero this codebase
    treats as worse than an error -- and here it would refuse an acceptance a
    client is entitled to give.
  * **Re-sending restarts it.** A re-send is the current document at current
    rates, which is precisely the thing a fresh window is measuring.
  * **The client is never turned away.** Past the date the page says so and
    names who to ask, rather than 404ing: a revoked or invented token answers
    404 because saying "that one expired" tells somebody probing which tokens
    are real, and an expired quote is a real quote belonging to a real client
    who is trying to say yes. The accept route refuses with the same words, so
    the rule is not one the form merely keeps.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# The house window. Thirty days is the ordinary media term; a deployment that
# quotes differently sets PROPOSAL_VALIDITY_DAYS rather than editing this.
DEFAULT_DAYS = 30
# Bounds on the per-quote override. A zero would expire a quote the moment it
# was sent, and a five-year window is not a window.
MIN_DAYS = 1
MAX_DAYS = 365

# The one status a window applies to. See the rules above: the other five are
# each finished in their own way, and expiring one of them takes something
# back rather than letting it lapse.
EXPIRES_FROM = "Sent"
EXPIRED_STATUS = "Expired"


def house_days() -> int:
    """The deployment's window, read at call time."""
    try:
        from hub.config import settings
        days = int(getattr(settings, "proposal_validity_days", 0) or 0)
    except Exception:                                   # noqa: BLE001
        days = 0
    return _clamp(days or DEFAULT_DAYS)


def _clamp(days) -> int:
    try:
        n = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, n))


def days_for(state) -> dict:
    """How long this quote stands, and whether somebody chose that.

    A per-quote override lives in the quote's own data blob -- never a new
    column, because `create_all()` adds none to an existing table and one here
    would be silently absent on the live Postgres with every local test green.
    """
    house = house_days()
    raw = (state or {}).get("validityDays") if isinstance(state, dict) else None
    if raw in (None, "", 0):
        return {"days": house, "source": "house", "custom": False}
    days = _clamp(raw)
    return {"days": days, "source": "quote", "custom": days != house}


def _as_dt(value):
    """A datetime from whatever the caller has, or None. Never raises."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def window(status: str = "", *, sent_at=None, created_at=None, state=None,
           now=None) -> dict:
    """When this quote stops standing, and whether it has.

    Answers for any quote, including the ones a window does not apply to --
    `applies` says which, so a caller never has to know the status list.
    """
    chosen = days_for(state)
    out = {
        "applies": False, "measured": False, "expired": False,
        "days": chosen["days"], "days_source": chosen["source"],
        "custom": chosen["custom"],
        "expires_on": "", "days_left": None, "counted_from": "",
        "counted_from_at": "", "reason": "",
    }
    stored = str(status or "").strip()
    if stored != EXPIRES_FROM:
        out["reason"] = (f"A {stored.lower() or 'draft'} quote does not expire "
                         f"— only one that has been sent to the client does.")
        return out
    out["applies"] = True

    start = _as_dt(sent_at)
    out["counted_from"] = "sent"
    if start is None:
        start = _as_dt(created_at)
        out["counted_from"] = "written"
    if start is None:
        # Not measured, never expired: an absent date reading as "expired
        # today" refuses an acceptance the client is entitled to give.
        out["counted_from"] = ""
        out["reason"] = ("No send date is recorded, so how long this has been "
                         "out is not measured.")
        return out

    at = _as_dt(now) or datetime.now(timezone.utc)
    ends = start + timedelta(days=out["days"])
    out["measured"] = True
    out["counted_from_at"] = start.isoformat()
    out["expires_on"] = ends.date().isoformat()
    out["expired"] = at >= ends
    # Whole days, rounded up, so the last day of a window reads as 1 rather
    # than 0 while the quote is still perfectly good.
    remaining = (ends - at).total_seconds() / 86400.0
    out["days_left"] = 0 if out["expired"] else max(1, int(remaining + 0.999))
    return out


def effective_status(status: str = "", **kw) -> str:
    """The status as a screen should read it. Derived, never written back."""
    return EXPIRED_STATUS if window(status, **kw).get("expired") else str(status or "")


def client_note(win: dict) -> str:
    """The line the client reads on their own copy of the document.

    An expiry the client cannot see is one we cannot hold them to, so this is
    printed on the proposal rather than kept on a staff screen. It says the
    date rather than the length: "valid for 30 days" on a document with no
    send date on it is arithmetic the reader cannot do.
    """
    win = win or {}
    if not win.get("applies") or not win.get("measured"):
        return ""
    when = _pretty(win.get("expires_on"))
    if win.get("expired"):
        return (f"The pricing in this proposal was valid until {when}. "
                "Ask us for an updated version — the plan stands, the rates "
                "are simply due a refresh.")
    return (f"Pricing and availability in this proposal are held until {when}. "
            "After that we will be glad to re-quote at current rates.")


def staff_note(win: dict) -> str:
    """The same fact for a rep, who needs the days rather than the sentence."""
    win = win or {}
    if not win.get("applies"):
        return win.get("reason") or ""
    if not win.get("measured"):
        return win.get("reason") or "Not measured."
    counted = ("from the day it was sent" if win.get("counted_from") == "sent"
               else "from the day it was written, because no send is recorded")
    if win.get("expired"):
        return (f"Expired on {_pretty(win['expires_on'])} — {win['days']} days "
                f"{counted}. Re-send it to quote at current rates.")
    left = win.get("days_left") or 0
    return (f"Stands until {_pretty(win['expires_on'])} — {left} day"
            f"{'' if left == 1 else 's'} left of {win['days']} {counted}.")


def refusal(win: dict, contact_name: str = "", contact_email: str = "") -> dict:
    """What the client is told when they press accept past the date.

    Never a 404 and never a bare refusal: somebody trying to say yes is the
    last person to turn away with nothing, so this names who to ask.
    """
    who = str(contact_name or "").strip()
    email = str(contact_email or "").strip()
    ask = ""
    if who and email:
        ask = f" Please ask {who} ({email}) for an updated copy."
    elif email:
        ask = f" Please ask {email} for an updated copy."
    elif who:
        ask = f" Please ask {who} for an updated copy."
    else:
        ask = " Please get in touch with your Smart 1 contact for an updated copy."
    return {
        "expired": True,
        "expires_on": (win or {}).get("expires_on", ""),
        "error": ("The pricing in this proposal was valid until "
                  f"{_pretty((win or {}).get('expires_on'))} and needs "
                  "refreshing before it can be accepted." + ask),
    }


def _pretty(iso: str) -> str:
    """A date a client reads, from an ISO one. The ISO string if it will not
    parse -- a wrong-looking date beats a sentence with a gap in it."""
    text = str(iso or "").strip()
    try:
        return datetime.fromisoformat(text).strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        return text or "an earlier date"
