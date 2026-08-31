"""Who is signed in right now — or as close to that as this Hub can honestly get.

## "Currently logged in" is not a question this Hub can answer

There is no session table. Signing in issues a **signed cookie** and the
server keeps nothing; that is what makes two gunicorn workers and a restart
survivable, and it is also why nothing is ever told that somebody has left.
Closing the tab, shutting the laptop and going home for the day all look
exactly like reading a long page. A number labelled "logged in now" would
therefore be a confident answer to a question nobody here can answer.

So this counts **people seen in the last fifteen minutes**, and every screen
that prints the number says so in those words. Fifteen because it is longer
than reading a page and shorter than a lunch break: long enough that somebody
working through a report is not dropped mid-sentence, short enough that the
count means "around now" rather than "in today at some point".

## What it records, and what it deliberately does not

One row per person: who they are, and when they were last seen. **Not what
page they were on.** A table with a path column in it is a log of what each
member of staff was doing minute by minute, which is a different thing from a
headcount and one nobody asked for. The moment it exists somebody reads it
that way.

Nor a history: a row is overwritten rather than appended, so this can never
become a timesheet either.

## Why a table of its own

`create_all()` creates missing tables and **never adds a column to an existing
one**, so a `last_seen_at` column on `hub_users` would exist on every local
SQLite run and be silently absent on the live Postgres — every test green,
every read of it `None` in production. The same reasoning `hub_user_profiles`
is a separate table for.

## Cost, and the throttle that keeps it near zero

`touch()` is called from a `before_request` and from `AuthGuard`, so without a
throttle this would be a database write on every request the Hub serves. It
writes at most once per person per minute per worker, and a minute of
granularity costs nothing against a fifteen-minute window. The throttle lives
in the process, so two workers mean at most two writes a minute per person —
still nothing, and no coordination needed.

## Rules

* **The shared password is not a person.** `PANEL_PASSWORD` grants a session
  with no account behind it, so it gets its own row, marked `shared`, and the
  screens say so rather than quietly adding it to a headcount people read as
  "how many of us are here".
* **Nothing here may raise.** A presence write failing must cost a page
  nothing — this is a nice-to-have on the dashboard and a hard dependency of
  no route at all. Every entry point swallows, and `active()` reports that it
  could not look rather than returning an empty list, because "nobody is
  signed in" and "we could not read the table" are different answers.
* **What it reports is a sentence, never the exception.** Both screens that
  draw this interpolate `error` straight into the page, and a SQLAlchemy
  `OperationalError` carries the database host, the user it tried to
  authenticate as, and the SQL — on the dashboard, which every account opens.
  An exception is not a message, which is the rule the image and PDF
  optimizers were fixed for; the cause goes to the log.
* **Not knowing who somebody is never writes a second identity for them.**
  `identify()` used to answer `("", False)` both for "two accounts share this
  name" and for "the account table would not answer", so a blip during a
  deploy keyed a row on the *name* for somebody who already had one keyed on
  their *email* — two rows, two chips with one name, and a headcount one too
  high for a quarter of an hour. It carries whether it could look, and
  `touch_display()` writes nothing when it could not: the row from a minute
  ago is still inside the window and still right.
* **A read needs an application context.** `AuthGuard` is WSGI middleware and
  runs without one; it pushes the hub app's context around the write. This is
  the trap `hub/google_index.py` has at length — a swallowed
  `RuntimeError: Working outside of application context` reads as a quiet
  zero on every screen.
"""
from __future__ import annotations

import datetime as _dt
import time as _time

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from hub.extensions import db

# How far back "now" reaches. Longer than reading a page, shorter than lunch.
WINDOW_MINUTES = 15

# How often one person's row may be rewritten, per worker. The window is 15
# minutes wide, so a minute of staleness changes no answer.
_THROTTLE_SECONDS = 60

# key -> monotonic seconds of the last write this process made. Per process on
# purpose: two workers writing the same row a minute apart is two writes a
# minute, which is not worth a lock to avoid.
_LAST_WRITE: dict[str, float] = {}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)


class Presence(db.Model):
    """One row per person, overwritten. Never a history — see the module docstring."""
    __tablename__ = "hub_presence"

    id = Column(Integer, primary_key=True)
    # The account email where there is an account, and "name:<display name>"
    # for a shared-password session, which has none. Derived on write and
    # never handed out as an identifier.
    key = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(160), default="")
    email = Column(String(255), default="")
    shared = Column(Boolean, default=False)
    last_seen = Column(DateTime, default=_now, index=True)


def key_for(name: str = "", email: str = "") -> str:
    email = (email or "").strip().lower()
    if email:
        return email
    return "name:" + (name or "").strip().casefold()


def due(key: str) -> bool:
    """Has this process gone long enough without writing this row?

    A plain dict lookup, so the common case — a request from somebody already
    recorded a moment ago — costs nothing at all.
    """
    if not key:
        return False
    last = _LAST_WRITE.get(key)
    return last is None or (_time.monotonic() - last) >= _THROTTLE_SECONDS


def touch(name: str = "", email: str = "", shared: bool = False) -> bool:
    """Record that this person was just seen. Returns whether it wrote.

    Never raises: a failure here must cost the request nothing. Needs an
    application context — see `touch_from_environ()` for the caller that has
    to make one.
    """
    key = key_for(name, email)
    if not key or key == "name:":
        return False
    if not due(key):
        return False
    # Marked before the write rather than after, so a failing database does
    # not mean a retry on every single request.
    _LAST_WRITE[key] = _time.monotonic()
    try:
        row = Presence.query.filter_by(key=key).first()
        if row is None:
            row = Presence(key=key)
            db.session.add(row)
        row.name = (name or "").strip()[:160]
        row.email = (email or "").strip().lower()[:255]
        row.shared = bool(shared)
        row.last_seen = _now()
        db.session.commit()
        return True
    except Exception:                                   # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:                               # noqa: BLE001
            pass
        return False


def active(window_minutes: int = WINDOW_MINUTES, now=None) -> dict:
    """Everybody seen inside the window, newest first.

    `error` rather than an empty list when the table could not be read:
    "nobody is signed in" and "we could not look" are different answers and
    only one of them is about people.
    """
    now = now or _now()
    cutoff = now - _dt.timedelta(minutes=window_minutes)
    out = {"window_minutes": window_minutes, "people": [], "count": 0,
           "shared_sessions": 0, "error": ""}
    try:
        rows = Presence.query.filter(Presence.last_seen >= cutoff).all()
    except Exception as exc:                            # noqa: BLE001
        # A sentence, not the exception. Both screens that draw this
        # interpolate `error` straight into the page, and a SQLAlchemy
        # OperationalError carries the database host, the user it tried to
        # authenticate as and the SQL it was running -- printed on the
        # dashboard, which every account opens. An exception is not a
        # message: the same rule the two file optimizers were fixed for, and
        # the cause belongs in the log, which is where it goes.
        out["error"] = "the presence table could not be read"
        try:
            import logging
            logging.getLogger(__name__).warning(
                "presence.active could not read the table: %s", exc)
        except Exception:                               # noqa: BLE001
            pass
        return out
    people = []
    for row in rows:
        seen = _aware(row.last_seen) or now
        people.append({
            "name": row.name or (row.email or "").split("@")[0] or "Someone",
            "email": row.email or "",
            "shared": bool(row.shared),
            "last_seen": seen.isoformat(),
            "minutes_ago": max(0, int((now - seen).total_seconds() // 60)),
        })
    people.sort(key=lambda p: (p["minutes_ago"], p["name"].casefold()))
    out["people"] = people
    out["count"] = len(people)
    out["shared_sessions"] = sum(1 for p in people if p["shared"])
    return out


def summary_line(data: dict | None = None) -> str:
    """The one sentence every screen prints, so none of them can word it
    differently — and so none of them can print the count without the window
    it was measured over."""
    data = data if data is not None else active()
    if data.get("error"):
        return "Signed in now: not measured — the presence table could not be read."
    n = data.get("count", 0)
    window = data.get("window_minutes", WINDOW_MINUTES)
    who = "1 person" if n == 1 else f"{n} people"
    line = f"{who} active in the last {window} minutes"
    shared = data.get("shared_sessions", 0)
    if shared:
        line += f" · {shared} of them a shared-password session"
    return line


def touch_display(name: str) -> bool:
    """Record presence from a display name — the only identity a cookie carries.

    One description of what recording somebody means, called by the hub app's
    `before_request` and by `AuthGuard` alike, so a page in a mounted module
    counts for exactly as much as a hub page does.
    """
    if not name:
        return False
    # Throttled on the display name before anything else, because resolving
    # it to an account is a query: on the overwhelming majority of requests
    # this function is a dict lookup and a return.
    if not due("env:" + name):
        return False
    _LAST_WRITE["env:" + name] = _time.monotonic()
    email, shared, looked = identify(name)
    if not looked:
        # A row keyed on the name for somebody whose row is keyed on their
        # email is a second person in the headcount and a second chip with
        # their name on it. Whatever was written up to a minute ago is still
        # inside the window and still right, so the honest thing is to leave
        # it: not knowing who somebody is is not a reason to invent an
        # identity for them.
        return False
    return touch(name=name, email=email, shared=shared)


def touch_from_environ(environ, app) -> bool:
    """Record presence from WSGI middleware, which has no application context.

    `AuthGuard` runs before Flask does, so `db.session` has no context to bind
    to and every read raises `RuntimeError: Working outside of application
    context` — swallowed, that reads as a Hub nobody is ever signed in to.
    This is the trap `hub/google_index.py` documents at length. The context is
    pushed only once the throttle says a write is due, so an ordinary request
    costs a dict lookup.
    """
    try:
        name = environ.get("s1hub.user") or ""
        if not name or not due("env:" + name):
            return False
        with app.app_context():
            return touch_display(name)
    except Exception:                                   # noqa: BLE001
        return False


def identify(name: str) -> tuple[str, bool, bool]:
    """(email, is a shared-password session, we were able to look).

    **Four** answers, not two, and the fourth is why this carries a third
    value rather than the two bits it used to:

      * exactly one account with that name — that is who it is;
      * **no** account with that name — a `PANEL_PASSWORD` session, which is
        not a person and must be counted as its own thing;
      * more than one — a real person whose name two accounts share. We
        cannot say which, and we must not guess: two people here are called
        Todd. It is emphatically *not* a shared session, so it is recorded
        under the name alone and counted as somebody;
      * **we could not ask.** That used to return `("", False)`, which is the
        same value as "two accounts share this name" — so a database blip
        during a deploy keyed the row on the *name* for somebody who already
        had one keyed on their *email*, and the headcount counted them twice
        for the next fifteen minutes, drawing two chips with one name on
        them. The docstring at the top of this module promises **one row per
        person**. It also made `/status` print "no account matched this name"
        about somebody who has one — a confident answer to a question that
        was never asked.
    """
    try:
        from hub.users import User
        rows = User.query.filter_by(name=(name or "").strip()).all()
    except Exception:                                   # noqa: BLE001
        # Not knowing who somebody is must not promote them to "the emergency
        # shared password is in use", nor invent a second identity for them.
        return "", False, False
    if len(rows) == 1:
        return (rows[0].email or ""), False, True
    return "", not rows, True
