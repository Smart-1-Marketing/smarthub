"""What needs filling in, where it shows, and who is allowed to be told.

The dashboard's birthday block carried this sentence, to everybody:

    Of 14 people on the roster: 7 whose start date is still the 01-08-19
    placeholder from the census. Fill them in under Users and they appear here.

Every word of it is true and it was on the wrong screen. Eleven of the
fourteen accounts are General Access, and `/diagnostics/users` — the page it
sends them to — answers them 403: a to-do addressed to people who cannot do
it, printed under a card they opened to find out whose birthday it is. And
because that sentence was the only record of the gap anywhere in the Hub, the
person who *can* fix it learned about it by happening to look at somebody
else's dashboard.

So a warning of that shape belongs here. This module collects them, each one
naming the page a reader meets it on, and `/api/housekeeping` is a Utilities
path — the panel that lists them is the one page in the Hub whose whole job is
telling an admin what to go and do.

## What belongs in this file, and what does not

A finding here is **housekeeping**: data somebody has to type in, on a screen
somebody else is reading. It is not a defect (`/api/integrity`), not a
provider that is down (`hub/diagnostics.py`), and not a setting that resolved
oddly (`/api/environment`). Those three each have a panel already, and the
trap this codebase keeps meeting is two checks asking one question and
answering it differently on the same screen — so nothing already reported by
one of them is repeated here.

## Rules, each of which is a way to be wrong quietly

**Every finding names the page it shows on.** The point of moving a warning
off the dashboard is that the person who can act on it never saw the
dashboard, so a finding that says only "7 start dates are placeholders" has
lost the half that makes it actionable — where somebody is reading a shorter
list than they think they are.

**A source that could not be read is a finding, not an absence.** Every
report in this Hub that has ever been wrong in an expensive way was wrong this
way: "nobody needs anything" and "we could not look" rendered identically. A
source that raises is listed with `measured: False` and its own reason.

**A source that fails costs only itself.** One unreadable roster must not
empty the panel of everything else — the `google_finder` rule, one screen up.

**Nothing here reaches a provider.** Each source reads what the page it
describes already reads: a roster row, a JSON file on disk. A triage panel
that costs eight outbound calls is a panel people stop opening, and this one
loads beside eight live API checks that are already paid for on that page.

**No values, no dates, no credentials.** The gap is the finding; the birthday
is not. Names are carried because a list of names is what makes "fill these
in" a task rather than a number — and they are names that already sit on the
Users panel this row links to, on a page no General account can open.

**A source with nothing to report is still named.** It goes in `clean`, so
the panel can say what it checked. A panel that renders "nothing outstanding"
without saying what it looked at reads exactly like a panel whose fetch
failed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# How many names a finding carries. The row is a prompt to open the page it
# links to, not a substitute for it — and the Users panel is one click away.
MAX_NAMES = 20


@dataclass
class Finding:
    key: str
    page: str            # where a reader meets this, in the words on screen
    page_path: str       # and the URL of that page
    issue: str           # what is missing, with the count in it
    fix: str             # what to do about it
    fix_path: str = ""   # and where it is done
    count: int = 0
    level: str = "warn"  # warn | low | error
    names: list[str] = field(default_factory=list)
    measured: bool = True
    # Where the answer came from, when that changes how much to trust it.
    # Empty on a source reading its own first-choice store.
    note: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["names"] = self.names[:MAX_NAMES]
        d["names_truncated"] = max(0, len(self.names) - MAX_NAMES)
        return d


def _not_measured(key: str, page: str, page_path: str, what: str,
                  reason: str) -> Finding:
    """The answer that is neither a finding nor a clean bill of health."""
    return Finding(key=key, page=page, page_path=page_path,
                   issue=f"Not measured — {what} ({reason}).",
                   fix="Nothing to fill in until this can be read.",
                   level="error", measured=False)


# --------------------------------------------------------------- the sources

ROSTER_PAGE = "Dashboard — birthdays and work anniversaries"
ROSTER_PATH = "/"
USERS_PANEL = "/diagnostics/users"


def _roster_dates() -> list[Finding]:
    """The three ways the roster leaves somebody out of the block.

    Three findings and not one, because they are fixed by three different
    keystrokes and two of them are different *situations*: a blank is a date
    nobody has, and 2019-08-01 is a date sitting on the Users panel looking
    perfectly filled in. Rolling them into "10 dates missing" is how the
    second kind never gets corrected — somebody opens the panel, sees a date
    against the name, and concludes the report is wrong.
    """
    from . import celebrations
    state = celebrations.roster_gaps()
    people = state.get("people") or 0
    if not people:
        # No rows at all. `roster_gaps()` carries an error beside a perfectly
        # good fallback answer, so the error alone is not the test — a report
        # that read it that way would call a working roster unmeasurable.
        return [_not_measured("roster_dates", ROSTER_PAGE, ROSTER_PATH,
                              "the staff roster could not be read",
                              state.get("error") or "no rows")]

    # The profile table is where a corrected date lands, so an answer read off
    # the census roster instead may name people somebody has already fixed.
    # That is not a reason to withhold the row and every reason to say so.
    note = ""
    if state.get("source") != "profiles":
        note = ("Read from the census roster — the profile table could not be "
                f"read ({state.get('error') or 'no rows yet'}), so any date "
                "corrected under Users is not reflected here.")

    gaps = state.get("gaps") or {}
    out: list[Finding] = []

    def add(key: str, names: list[str], issue: str, fix: str,
            level: str = "warn") -> None:
        if names:
            out.append(Finding(key=key, page=ROSTER_PAGE,
                               page_path=ROSTER_PATH, issue=issue, fix=fix,
                               fix_path=USERS_PANEL, count=len(names),
                               level=level, names=names, note=note))

    birthday = list(gaps.get("birthday") or [])
    hired = list(gaps.get("hired_at") or [])
    placeholder = list(gaps.get("hired_placeholder") or [])

    add("roster_birthday", birthday,
        f"{len(birthday)} of {people} people on the roster have no birthday "
        "on file, so they never appear in the month block and nobody is "
        "told when their day comes round.",
        "Add the date of birth on the person's row under Users.")
    add("roster_start_date", hired,
        f"{len(hired)} of {people} people on the roster have no start date "
        "on file, so they get no work anniversary.",
        "Add the date of hire on the person's row under Users.")
    add("roster_start_placeholder", placeholder,
        f"{len(placeholder)} of {people} people still carry the 2019-08-01 "
        "placeholder start date the census was uploaded with — the day the "
        "Hub's records begin rather than the day any of them started. It "
        "reads as a filled-in date on the Users panel and is treated as a "
        "missing one everywhere else.",
        "Replace it with the real date of hire under Users.")
    return out


EXPORT_PAGE = "Dashboard — scorecard"
EXPORT_PATH = "/"


def _knack_export() -> list[Finding]:
    """The committed export the month-over-month counts are measured in.

    Not a duplicate of the Knack panel on this page: that one reports whether
    the *live* pull is working and how old its cache is. This is the export
    checked into the repo, which nothing refreshes and which four cards on the
    dashboard are still measured in — and the dashboard says so in small grey
    text under the number, to a reader who cannot regenerate it.
    """
    from . import knack_data
    state = knack_data.export_state()
    if not state.get("period"):
        # No month in the file at all. That is not a stale export and not a
        # fresh one, and guessing either way is the confident wrong answer.
        return [_not_measured("knack_export", EXPORT_PAGE, EXPORT_PATH,
                              "the products export names no month",
                              "no thisMonth in clients_app/data/products.json")]
    if not state.get("stale"):
        return []
    return [Finding(
        key="knack_export", page=EXPORT_PAGE, page_path=EXPORT_PATH,
        issue=f"The products export was generated for {state['label']}, and "
              f"it is now {state['current_label']}. The new, lost, up and "
              "down counts on the scorecard are that month's, not this "
              "month's — the card labels them, in grey, under the number.",
        fix="Re-export object_135 to clients_app/data/products.json.",
        count=1, level="low")]


# Every source, in the order the panel lists them. A tuple rather than a
# decorator registry: the whole list is readable in one screen, and a source
# added later is one line here rather than an import somebody has to remember
# to keep.
SOURCES = (
    ("roster_dates", _roster_dates),
    ("knack_export", _knack_export),
)


def findings() -> dict:
    """Everything outstanding, with the page each one shows on.

    Never raises. A source that does is reported under its own name — the
    panel saying "we could not check the roster" is the whole difference
    between this and a panel that quietly checks one fewer thing every
    release.
    """
    out: list[dict] = []
    clean: list[str] = []
    for key, fn in SOURCES:
        try:
            found = list(fn() or [])
        except Exception as exc:            # noqa: BLE001 — one source, not the panel
            out.append(_not_measured(key, "", "", f"the {key} check failed",
                                     type(exc).__name__).as_dict())
            continue
        if not found:
            clean.append(key)
            continue
        out.extend(f.as_dict() for f in found)

    open_rows = [f for f in out if f["measured"]]
    return {
        "findings": out,
        "open": len(open_rows),
        "people_affected": sum(f["count"] for f in open_rows),
        "not_measured": [f["key"] for f in out if not f["measured"]],
        # What was looked at and had nothing to say. Without it, a panel with
        # one row on it cannot be told from a panel that only ran one check.
        "clean": clean,
        "sources": [key for key, _ in SOURCES],
    }


def withheld(gaps: dict) -> dict:
    """What a General account is told about the roster's gaps instead.

    Not nothing, and not the to-do. The reason the sentence was on the
    dashboard at all is sound — a list that quietly shrinks reads as a quiet
    month — so the block still says it is not the whole roster. What goes is
    everything only an admin can act on: the counts, the names and the link to
    a page that would answer them 403.

    One function so both halves of that decision are in one place: a template
    that decided it would be a second description of what a General user sees,
    and the two would drift the day a fourth kind of gap was added.
    """
    any_gap = any(bool(v) for v in (gaps or {}).values())
    return {"withheld": True, "any": any_gap}
