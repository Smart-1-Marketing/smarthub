"""One date format for the whole Hub: mm-dd-yy.

Before this there were at least four in use — `%m/%d/%Y`, `%m/%d/%y`,
`%b %Y`, and raw ISO from `.isoformat()` — sometimes two of them in the same
table. A reader comparing "2026-08-31" against "08/31/26" has to stop and
translate, and a column that changes format halfway down reads as a data
problem rather than a formatting one.

`fmt()` takes anything a date arrives as in this codebase — a `date`, a
`datetime`, an ISO string, a `mm/dd/yyyy` string from Knack, or Knack's
`YYYYMMDD` integer — and returns `mm-dd-yy`. Anything it cannot read comes
back as an em dash rather than a half-parsed guess.

New code should call this rather than reaching for strftime.
"""
from __future__ import annotations

import datetime as _dt

BLANK = "—"

# The shapes dates actually arrive in here. Order matters: the ISO form is
# tried first because it is unambiguous, and the day-first forms are absent
# deliberately — nothing in this system produces them, and guessing between
# 03-04-26 and 04-03-26 would silently mis-date a renewal.
_PARSE = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d", "%m-%d-%Y", "%m-%d-%y")

OUT = "%m-%d-%y"


def to_date(value) -> _dt.date | None:
    """Best-effort parse. Returns None rather than raising or guessing."""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, (int, float)):
        # Knack stores some dates as the integer 20260914.
        value = str(int(value))
    s = str(value).strip()
    if not s:
        return None
    # An ISO timestamp: take the date half.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        s = s[:10]
    for fmt in _PARSE:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fmt(value, blank: str = BLANK) -> str:
    """A date as mm-dd-yy, or `blank` when it cannot be read."""
    d = to_date(value)
    return d.strftime(OUT) if d else blank


def sort_key(value):
    """Sort by real date, with unknown dates last rather than first.

    Sorting the formatted string would order 01-05-27 before 12-31-26, which is
    how a "next to expire" list ends up showing next year first.
    """
    d = to_date(value)
    return (d is None, d or _dt.date.max)
