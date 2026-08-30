"""Birthdays and work anniversaries, from the roster the Hub already holds.

`hub/user_directory.py` has carried a birthday and a date of hire for all
fourteen people since the census was uploaded, and nothing read either. The
information was on the Users panel, one row at a time, behind Utilities —
which is admin-only, so most of the company could not have found a colleague's
birthday if they had thought to look. A date nobody reads is the same as a
date nobody recorded.

This module answers two questions and keeps them apart, because they are asked
by two different screens:

  * `this_month()` — everything worth marking in the calendar month, for the
    dashboard block. Past, today and still to come, each labelled.
  * `today()` — the slice worth interrupting somebody for, which is the popup
    and nothing else. Interrupting a person for a birthday four days away
    teaches them to close the popup without reading it, and then they close
    the one that mattered.

## Rules, each of which is a way to be wrong quietly

**A missing date is named, never skipped in silence.** Somebody with no
birthday on file drops out of the list entirely, and a list that shrinks
without saying so reads as "nobody has a birthday this month". `not_recorded`
carries the count and the names, and the block prints it.

**A placeholder date is a missing date.** Seven people came off the census
with a hire date of 1 August 2019, which is when the Hub's records begin
rather than when any of them started — so it is in `PLACEHOLDER_HIRE_DATES`
and read as *not recorded*, which puts those seven in the list of people
whose start date needs filling in instead of congratulating all seven on the
same day every year. Correct one in the Users panel and that person appears
here by themselves. The alternative — printing it — is seven confident wrong
answers a year, which is the failure this whole file is written against.

**A day that has passed is dropped.** This block is what is still coming, so
the 8th stops being shown on the 9th. Today stays until tomorrow. It is a
deliberate reversal of what this did first: a month is a calendar, but the
card is read to know who to say something to, and nobody can say happy
birthday to a day that has gone.

**The year of birth is never published.** The panel holds it, this module
reads it to work out the day, and it is not returned: a dashboard block that
prints the whole company's ages is a different feature from one that says whose
birthday it is, and nobody asked for the first.

**A hire date this month in this year is not a first anniversary.** It is
somebody who started this month, and `years: 0` renders as "joined this month"
rather than as a zero-year milestone.

**29 February is marked on the 28th in a common year.** Silently dropping it
means one person's birthday never appears, which is exactly the kind of
absence nothing reports.

**The profile table is the source; the roster is the fallback.** Profiles are
seeded from `user_directory.ROSTER` and then corrected by hand in the Users
panel, so the table is the better answer wherever it has rows. Before the
first sync it is empty, and an empty table is not the same statement as
"nobody has a birthday" — so an empty table falls back to the roster and says
which source it used. A table that could not be *read* is an error, reported
by name, and never an empty list.
"""
from __future__ import annotations

import calendar
import datetime as _dt

_MONTHS = ("", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")

# Hire dates that are records of when the Hub's book starts, not of when
# anybody started. Seven of the fourteen census rows carry 1 August 2019, so
# left in place it congratulates half the company on one day a year on a date
# none of them recognise. Read as "not recorded" instead, which is both true
# and actionable: the seven names appear under the block as start dates to
# fill in. Delete the entry once the real dates are in the Users panel — a
# corrected row stops matching by itself, so nothing else has to change.
PLACEHOLDER_HIRE_DATES = frozenset({"2019-08-01"})


def _today() -> _dt.date:
    return _dt.date.today()


def _people() -> tuple[list[dict], str, str]:
    """(rows, source, error). Never raises.

    Rows carry `name`, `email`, `title`, `birthday` and `hired_at` — whatever
    shape they came in, the two dates are ISO or "".
    """
    from hub import user_directory as ud

    try:
        from hub.user_directory import UserProfile
        rows = [p.as_dict() for p in UserProfile.query.all()]
    except Exception as exc:                            # noqa: BLE001
        # No table yet, or no application context (a scheduled job). Either
        # way we could not look, which is a different answer from "nobody".
        rows, err = [], str(exc)
        try:
            rows = ud.roster_rows()
        except Exception:                               # noqa: BLE001
            return [], "", err
        return _shape(rows), "roster", err

    if not rows:
        return _shape(ud.roster_rows()), "roster", ""
    return _shape(rows), "profiles", ""


def _shape(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        first = (r.get("first_name") or "").strip()
        last = (r.get("last_name") or "").strip()
        name = (r.get("name") or f"{first} {last}").strip()
        if not name:
            name = (r.get("email") or "").split("@")[0]
        out.append({
            "name": name,
            "first_name": first or name.split(" ")[0],
            "email": (r.get("email") or "").strip().lower(),
            "title": (r.get("title") or "").strip(),
            "birthday": (r.get("birthday") or "").strip(),
            "hired_at": (r.get("hired_at") or "").strip(),
        })
    return out


def _iso(value: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _day_this_month(anniversary: _dt.date, year: int, month: int) -> int | None:
    """Which day of `month` this date falls on, or None if it is another month.

    29 February in a common year comes back as the 28th. A birthday that
    disappears three years in four is worse than one marked a day early, and
    the alternative — dropping it — is invisible from every screen.
    """
    if anniversary.month != month:
        return None
    last = calendar.monthrange(year, month)[1]
    return min(anniversary.day, last)


def _gaps(people: list[dict]) -> dict:
    """Who has no date on file, and who has one nobody believes.

    One classifier, read by `this_month()` for the line under the block and by
    `hub/housekeeping.py` for the Diagnostics row. Two copies would drift the
    day a second placeholder date joined the set, and the two screens would
    then disagree about how many people need a start date — which is worse
    than either being wrong on its own, because nothing on either screen says
    which of them to believe.
    """
    no_birthday: list[str] = []
    no_hire: list[str] = []
    placeholder: list[str] = []
    for p in people:
        if _iso(p["birthday"]) is None:
            no_birthday.append(p["name"])
        if p["hired_at"] in PLACEHOLDER_HIRE_DATES:
            # Kept apart from the blanks: "we have no date" and "we have a
            # date nobody believes" are explained differently to somebody who
            # can see one sitting on the Users panel.
            placeholder.append(p["name"])
        elif _iso(p["hired_at"]) is None:
            no_hire.append(p["name"])
    return {"birthday": sorted(no_birthday), "hired_at": sorted(no_hire),
            "hired_placeholder": sorted(placeholder)}


def roster_gaps() -> dict:
    """The dates the roster is missing, asked without a month around them.

    `this_month()` answers this too, as a footnote to a list that changes with
    the day it is read on. Housekeeping wants the gaps and none of the
    birthdays, so it asks for them directly rather than reading them off a
    month — and it needs the count of people they are out of, because "3 with
    no start date" says nothing without the 14.
    """
    people, source, error = _people()
    return {"gaps": _gaps(people), "people": len(people),
            "source": source, "error": error}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def this_month(today: _dt.date | None = None) -> dict:
    """Every birthday and work anniversary in the calendar month.

    Sorted by day, each entry saying whether it has passed, is today, or is
    still to come — the block reads as a month rather than as a queue.
    """
    now = today or _today()
    people, source, error = _people()

    birthdays: list[dict] = []
    anniversaries: list[dict] = []
    # Who is missing a date is not a question about this month, so it is not
    # answered in this loop: `_gaps()` is the one classifier, and the
    # Diagnostics row reads the same one.
    gaps = _gaps(people)

    for p in people:
        bday = _iso(p["birthday"])
        if bday is not None:
            day = _day_this_month(bday, now.year, now.month)
            if day:
                birthdays.append(_entry(p, day, now, kind="birthday"))

        hired = _iso(p["hired_at"])
        # A placeholder start date is a missing one, so nobody is
        # congratulated on the day the Hub's own records begin.
        if hired is not None and hired.year <= now.year \
                and p["hired_at"] not in PLACEHOLDER_HIRE_DATES:
            day = _day_this_month(hired, now.year, now.month)
            years = now.year - hired.year
            # Hired in an earlier year: an anniversary, whether or not the day
            # has come round yet. Hired this month this year: they have just
            # joined, which is worth marking as that and not as a nought-year
            # milestone. A start date still in the future is neither.
            if day and (years > 0 or hired <= now):
                anniversaries.append(
                    _entry(p, day, now, kind="anniversary", years=years,
                           started=hired.isoformat()))

    # What is still to come, today included. A birthday that has passed is
    # dropped rather than dimmed: this card is read to know who to say
    # something to, and there is nothing to say about the 8th on the 9th.
    birthdays = [e for e in birthdays if not e["is_past"]]
    anniversaries = [e for e in anniversaries if not e["is_past"]]
    birthdays.sort(key=lambda e: e["day"])
    anniversaries.sort(key=lambda e: e["day"])

    return {
        "month": _MONTHS[now.month],
        "month_key": now.strftime("%Y-%m"),
        "year": now.year,
        "today": now.isoformat(),
        "birthdays": birthdays,
        "anniversaries": anniversaries,
        "counts": {"birthdays": len(birthdays),
                   "anniversaries": len(anniversaries)},
        # Named, not silently dropped: a shorter list is indistinguishable
        # from a quiet month unless the page can say who is missing from it.
        "not_recorded": gaps,
        "people": len(people),
        "source": source,
        "error": error,
    }


def _entry(person: dict, day: int, now: _dt.date, *, kind: str,
           years: int = 0, started: str = "") -> dict:
    when = now.replace(day=day)
    entry = {
        "kind": kind,
        "name": person["name"],
        "first_name": person["first_name"],
        "email": person["email"],
        "title": person["title"],
        "day": day,
        "day_label": _ordinal(day),
        "date": when.isoformat(),
        # The month and day, and deliberately not the year: the panel knows
        # how old everybody is and the dashboard has no business printing it.
        "date_pretty": f"{when.strftime('%b')} {day}",
        "weekday": when.strftime("%A"),
        "is_today": when == now,
        "is_past": when < now,
        "days_away": (when - now).days,
    }
    if kind == "anniversary":
        entry["years"] = years
        entry["started"] = started
        entry["years_label"] = (
            "joined Smart 1 this month" if years <= 0
            else f"{_ordinal(years)} anniversary")
    return entry


def today(now: _dt.date | None = None) -> dict:
    """Only what is happening today — what the popup is allowed to announce.

    `me` is the signed-in person's entry when it is their own day, so the
    popup can say "Happy birthday" to them rather than about them. Matching is
    on email first and on an exact name second: two people here are called
    Todd, and greeting the wrong one is the kind of confidently wrong answer
    this codebase treats as worse than saying nothing.
    """
    now = now or _today()
    month = this_month(now)
    birthdays = [e for e in month["birthdays"] if e["is_today"]]
    anniversaries = [e for e in month["anniversaries"] if e["is_today"]]
    return {
        "date": now.isoformat(),
        "birthdays": birthdays,
        "anniversaries": anniversaries,
        "any": bool(birthdays or anniversaries),
        "source": month["source"],
        "error": month["error"],
    }


def mine(payload: dict, email: str = "", name: str = "") -> list[dict]:
    """The entries in `payload` that belong to the signed-in person."""
    email = (email or "").strip().lower()
    name = (name or "").strip().casefold()
    out = []
    for entry in list(payload.get("birthdays") or []) + \
            list(payload.get("anniversaries") or []):
        if email and entry.get("email", "").strip().lower() == email:
            out.append(entry)
        elif not email and name and entry.get("name", "").strip().casefold() == name:
            out.append(entry)
    return out
