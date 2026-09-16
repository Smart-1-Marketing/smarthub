"""Reusable spoken copy, offered by both radio builders and saved once.

This existed as `modules/fan_radio/script_presets.py`: four default reads, a
custom library on the disk, a route that served both, and **no screen in
either tool**. So a rep could not reach any of it, and the Radio Ad Creator
had no equivalent at all -- the feature was half-built in one tool and absent
from the other. That is the failure CLAUDE.md names as "a tool with no tile is
invisible -- six were, for weeks", and `docs/claude/47-declared-and-never-wired`
one level in.

Closing it means the library is **one store read by both**, rather than a
second copy in the Radio Ad Creator. A saved read is a saved read: the two
tools write the same lengths against the same word budgets and the same read
pace (`hub/radio_spec.py`), so copy genuinely moves between them, and a rep who
saved "our standard closing line" in one tool looking for it in vain in the
other is the drift this module is arranged to avoid.

## The old file is still read, because somebody's saved reads are in it

Writes land in this module's own store. Reads are the **union** of that and the
legacy `fan_radio/script_presets.json`, keyed by id. Nothing is migrated: a
one-time rewrite-on-read would race the second gunicorn worker (the trap
`hub/scheduler.py` exists for), and a copy left behind under the old name is a
second place the truth could live. Reading both forever costs one file read and
loses nothing, and a preset saved before this change is offered in both tools
from the moment it lands.

## `{business}` is filled in here, not in two templates

The defaults are written with a `{business}` placeholder and nothing ever
substituted it, so the four shipped reads would have gone to air saying
"Welcome to {business}". `fill()` is the one substitution, applied on the way
out, and it is a plain replace rather than `str.format` -- a saved read is
somebody's prose and may legitimately contain a brace, which `format` would
raise on and take the whole library down with it.
"""

from __future__ import annotations

import os
import re
import secrets

from hub import jsonstore, script_contents

# The placeholder the defaults are written with. One name, because a second
# spelling is a preset that silently never fills in.
PLACEHOLDER = "{business}"

# Deliberately generic. Each of these is a read either builder would write --
# "game day" is a promotion any business runs, not a football-only one, and the
# football language that *is* Fan Radio's own lives in `phrases.SAFE_PHRASES`
# where the trademark scan can see it.
DEFAULTS = [
    {"id": "default-welcome", "name": "Welcome",
     "script": "Welcome to {business}. We're glad you're here. Stop in and see us today."},
    {"id": "default-game-day", "name": "Game day",
     "script": "Make {business} part of your game day plans. Bring your friends and get ready for a great day."},
    {"id": "default-weekend", "name": "Weekend invitation",
     "script": "Looking for something to do this weekend? Visit {business}. We look forward to seeing you."},
    {"id": "default-thanks", "name": "Customer thank-you",
     "script": "Thank you for choosing {business}. We appreciate your support and look forward to welcoming you back."},
]

MAX_ROWS = 500
MAX_NAME = 80
MAX_SCRIPT = 4000


def _path() -> str:
    """The shared store, which is where every new preset is written."""
    return os.path.join(jsonstore.data_dir("radio_presets"), "presets.json")


def _legacy_path() -> str:
    """Fan Radio's own file, read for as long as it holds anything.

    Not migrated and not written to. See the module docstring: a rewrite on
    read races the second worker, and a copy under the old name is a second
    place the truth could live.
    """
    return os.path.join(jsonstore.data_dir("fan_radio"), "script_presets.json")


def _rows(path: str) -> list:
    rows = jsonstore.read_json(path, default=[])
    return [r for r in rows if isinstance(r, dict) and r.get("script")] \
        if isinstance(rows, list) else []


def custom() -> list:
    """Every saved read, the shared store and the legacy file as one list.

    Keyed by id so a row that somehow exists in both is offered once, with the
    shared store winning -- it is the one being written to.
    """
    seen, out = set(), []
    for row in _rows(_path()) + _rows(_legacy_path()):
        rid = str(row.get("id") or "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        out.append(row)
    return out


def fill(script: str, business: str = "") -> str:
    """The read with the business name in it, where one was given.

    A plain replace rather than `str.format`: a saved read is somebody's prose
    and may contain a brace, and `format` would raise on it and take the whole
    library down. Where no name is supplied the placeholder is left standing
    rather than replaced with nothing -- "Welcome to ." reads like a defect
    somebody has to diagnose, and "Welcome to {business}." reads like a field
    nobody filled in, which is what it is.
    """
    name = str(business or "").strip()
    return str(script or "").replace(PLACEHOLDER, name) if name else str(script or "")


def library(business: str = "") -> dict:
    """The whole library, ready to draw, with `{business}` filled in.

    ``script`` is what the picker inserts and ``template`` is what was saved,
    so a preset re-saved from a project does not bake one client's name into
    the library -- the failure that would turn a reusable read into a
    single-use one on its first reuse.
    """
    def shape(row: dict) -> dict:
        return dict(row, template=row.get("script") or "",
                    script=fill(row.get("script"), business))
    return {"defaults": [shape(r) for r in DEFAULTS],
            "custom": [shape(r) for r in custom()],
            "placeholder": PLACEHOLDER}


def save(name, script, actor: str = "") -> dict:
    """Add a read to the shared library.

    The business name is put *back* to the placeholder on the way in, where the
    caller knows it: a read saved off a live project otherwise carries that
    client's name into every future project that reuses it.
    """
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise ValueError(f"Enter a script name of 1 to {MAX_NAME} characters.")
    if not isinstance(script, str) or not script.strip() or len(script) > MAX_SCRIPT:
        raise ValueError(f"Enter a script of 1 to {MAX_SCRIPT:,} characters.")
    row = {"id": secrets.token_urlsafe(12), "name": name.strip(),
           "script": script.strip(), "created_by": actor}

    def add(rows):
        rows = rows if isinstance(rows, list) else []
        # Counted across both files, or the cap is per-file and the library
        # quietly holds twice what it says it does.
        if len(rows) + len(_rows(_legacy_path())) >= MAX_ROWS:
            raise ValueError(f"The custom script library is full "
                             f"({MAX_ROWS} scripts).")
        rows.append(row)
        return rows

    jsonstore.update_json(_path(), add, default=[])
    return row


# Anchored so a name only matches where a name is, rather than anywhere its
# letters appear. A bare substring search put "{business}" inside "Box office"
# for a client called Ox, inside "Acmeburger" for one called Acme, and -- for a
# client recorded as "A" -- through every "a" in the read. What is deliberately
# NOT a word boundary (`\b`) is the edge itself: a name may end in a period
# ("Acme Inc."), where `\b` sits before the period rather than after it and the
# match fails on the one form that needs it most.
_EDGE_BEFORE = r"(?<![0-9A-Za-z])"
_EDGE_AFTER = r"(?![0-9A-Za-z])"


def _name_forms(business: str) -> list:
    """The ways a script might write this business's name, longest first.

    A record saying "Acme Plumbing, LLC" is called "Acme Plumbing" in every
    script anybody would write, so generalizing only on the recorded spelling
    left the client's literal name in the saved read -- and the next project to
    reuse it read out somebody else's business, which is the whole failure this
    function exists to prevent, on the commonest company format there is.

    The suffix reading is `hub/script_contents.spoken_name()`, the same one the
    content check uses to decide whether a read names the business at all.
    Longest first so "Acme Plumbing, LLC" is preferred over "Acme Plumbing"
    where a script happens to say the whole thing.
    """
    raw = str(business or "").strip()
    if not raw:
        return []
    forms = {raw, script_contents.spoken_name(raw)}
    return sorted((f for f in forms if f.strip()), key=len, reverse=True)


def generalize(script: str, business: str = "") -> str:
    """A read with this project's business name put back to the placeholder.

    The inverse of `fill()`, for the save path: a read saved off a live project
    carries that client's name, and reusing it would read out the wrong
    business. Case-insensitive, because a script says "Acme" where the record
    says "ACME" and both are the same business.
    """
    text = str(script or "")
    for form in _name_forms(business):
        # A callable replacement, so `re` never reads the placeholder as a
        # template -- a backslash or a `\\g<1>` in it would otherwise be
        # expanded, or raise, on somebody's saved read.
        text = re.sub(_EDGE_BEFORE + re.escape(form) + _EDGE_AFTER,
                      lambda _m: PLACEHOLDER, text, flags=re.I)
    return text
