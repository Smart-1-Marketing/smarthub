"""Which part of a Simvoly project name is the business — read by a model.

## The half the hand-written rules cannot reach

`hub/site_names.py` reads three shapes out of a project title — a media-partner
prefix, a placeholder, a trailing marker describing the job — and on this
deployment's own portfolio export that turned 42 raw matches into **305 exact
and 60 candidates** out of 1,021 projects. 229 of the remainder are
placeholders and are correctly unmatchable: "Anna's Website" names nobody, and
a fuzzy pass over it eventually finds an Anna and attaches a stranger's site
to her.

That leaves roughly four hundred projects that carry a real business name in a
shape no rule here anticipated — "SERVPRO Team Wall / Ocean County", "Hern
Marine — Summer '25 (do not delete)", "copy of Buckeye Lake Marina v3 FINAL".
Each one is a client whose website cannot be joined to their scans, their brand
data, their hosting charges or their Client 360 record, because
**the URL is the join key** and nothing here can find the URL.

## The model is never asked which client this is

That is the whole safety argument, and it is worth stating plainly: **the
client book is not in the prompt.** The model is handed project titles and
asked one question — which run of words in this string is the business's own
name — and it answers with a substring of what it was given. It has no way to
name a client, because it has never seen the client list.

Everything downstream is unchanged. The name it returns is fed into
`site_names.exact_matches()` against the real book, so `client_key`'s rules
still decide: exact on the normalised form or not at all, two clients under one
name propose neither, and a resemblance is a suggestion a human presses a
button on. A model that answers badly costs a candidate nobody accepts. It
cannot produce a match.

## Four rules, each a way this goes wrong quietly

**A reading must be *in* the name it came from.** `_is_grounded()` requires
every word of the answer to appear in the original, on the normalised form. A
model asked to extract a business name will occasionally expand an
abbreviation, fix a spelling or supply the company it thinks was meant — and
"SERVPRO of Fresno NW" coming back as "SERVPRO of Fresno Northwest" is a
different string that matches a different client, or none. Ungrounded readings
are dropped and **counted**, because a prompt that starts inventing is
something to see rather than something to absorb.

**A placeholder is never sent.** `is_placeholder()` has already answered for
those, and paying a model to look at "S1M Test" invites it to find a business
in a string that has none.

**Nothing is read twice.** Readings are stored by the *normalised project
name*, so a project renamed is re-read and a project merely re-listed is not.
`read_missing()` sends only what is not on file, which is what makes this
affordable: the whole portfolio is one pass of about fifty calls, and every
later run costs nothing.

**It is a button, never a page load.** The call is billed, and `suggest()` is
a report somebody opens several times a day — the rule `hub/brand_lookup.py`
arrived at for the same reason. `readings()` reads what is stored and never
calls anybody.

## What a reading is not

It is not stored against the client, and it is not stored as a match. The file
holds *what the model thought the business in this string was called*, which is
a reading of a string and stays true whatever the client book does next. The
join is re-derived on every report, so a client added tomorrow is matched by
the next run rather than needing the model again.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

from hub import ai as _hub_ai
from hub import jsonstore, site_names
from hub.client_key import normalise_name

# Stored through jsonstore, so it is mirrored into the database and survives
# the disk being recreated — the Render disk is not backed up, and a pass of
# fifty billed calls is exactly the thing not to pay for twice.
#
# `data_dir()`, not a relative path. A bare "site_names/ai_readings.json" is
# relative to the working directory, so it lands in the repo checkout and is
# wiped on every deploy while the mirror quietly restores it into whichever
# directory the next read happens to resolve — the trap CLAUDE.md names about
# `os.environ.get("HUB_DATA_DIR", "data")` and the reason
# `video_library._state_path()` is written the same way.
_STATE_FILE = "ai_readings.json"


def _store_path() -> str:
    return os.path.join(jsonstore.data_dir("site_names"), _STATE_FILE)

# How many titles go in one request. Twenty-five keeps the answer well inside
# the token ceiling with room for a `why` per row, and makes the whole 1,021
# project portfolio about forty calls.
BATCH = 25

# A ceiling on one press of the button, so a first run on a fresh portfolio
# cannot spend the afternoon's budget in one request nobody is watching. The
# page says how many are left and the button can be pressed again.
MAX_PER_RUN = 400

_MODEL_NOTE = (
    "You are reading the titles people gave to website projects in a website "
    "builder. Each title usually contains the name of a real business, often "
    "with other things around it: a media partner's name in front, a note "
    "about the job behind it, a version marker, a duplicate marker.\n\n"
    "For each title, answer with the run of words that is the BUSINESS's own "
    "name.\n\n"
    "Rules you must follow exactly:\n"
    "1. The business name you return must be words that appear in the title, "
    "in the same order. Do not expand abbreviations, do not correct spelling, "
    "do not add a word that is not there, and do not supply a company you "
    "think was meant. Copy the run of words out.\n"
    "2. If the title names no business — it is somebody's personal name, an "
    "email address, a test or a placeholder — return an empty business and say "
    "why.\n"
    "3. If you cannot tell which part is the business, return an empty "
    "business rather than guessing.\n"
    "4. `confidence` is \"high\" only when the title plainly contains a "
    "business name and you are certain which words those are.\n\n"
    "Return JSON: {\"readings\": [{\"title\": \"<the title, copied "
    "exactly>\", \"business\": \"<the business's name, or empty>\", "
    "\"confidence\": \"high\" | \"low\", \"note\": \"<a short reason, for a "
    "person reading the row>\"}]}"
)


def _key(name: str) -> str:
    return normalise_name(name)


def _is_grounded(answer: str, original: str) -> bool:
    """Is every word of the answer actually in the title it came from?

    The one check that makes this safe to feed into a matcher. A model asked to
    pull a name out of a string will now and then hand back a tidied version of
    it — an expanded abbreviation, a corrected spelling, the parent company —
    and a tidied name is a different string that matches a different client, or
    none at all, with nothing on the row saying the words were changed.
    """
    a, o = normalise_name(answer), normalise_name(original)
    if not a or not o:
        return False
    words = set(o.split())
    return all(w in words for w in a.split())


def readings() -> dict:
    """What is on file. Reads nothing from a provider and never raises."""
    try:
        data = jsonstore.read_json(_store_path(), default={}) or {}
        return data if isinstance(data, dict) else {}
    except Exception:                                       # noqa: BLE001
        return {}


def reading_for(name: str) -> dict:
    """The stored reading of one project title, or {}."""
    return readings().get(_key(name)) or {}


def pending(names: Iterable[str]) -> list[str]:
    """Titles worth spending a call on: named, not a placeholder, not read.

    A placeholder is left out because `is_placeholder()` has already answered
    for it, and because asking a model to find a business in "S1M Test" is
    inviting it to find one.
    """
    have = readings()
    out, seen = [], set()
    for raw in names or ():
        title = re.sub(r"\s+", " ", str(raw or "")).strip()
        key = _key(title)
        if not title or not key or key in seen or key in have:
            continue
        if site_names.is_placeholder(title):
            continue
        seen.add(key)
        out.append(title)
    return out


def candidates_for(name: str, store: dict | None = None) -> list[dict]:
    """The stored reading of one title, in `site_names.candidates()`'s shape.

    Returned as a candidate rather than as a match, so it goes through
    `exact_matches()` against the real client book exactly like a candidate a
    rule derived — and carries its own `why`, because "read out of the project
    name by the model" is a different claim from "the name with the media
    partner prefix removed" and only one of them was arrived at by a rule
    somebody can read.
    """
    # `store` is the whole file, read once by the caller. `sites_match.suggest()`
    # asks this per project, and on a portfolio of a thousand a fresh
    # `readings()` each time is a thousand file reads — each of which asks
    # jsonstore to restore from the database mirror on a miss. Handing the map
    # in is the difference between a report and a stall.
    row = (store.get(_key(name)) or {}) if store is not None else reading_for(name)
    business = str(row.get("business") or "").strip()
    if not business:
        return []
    # Re-checked on read, not only on write: a file written by an older prompt,
    # or edited by hand, must not get past the one rule that makes this safe.
    if not _is_grounded(business, name):
        return []
    key = _key(business)
    if not key:
        return []
    note = str(row.get("note") or "").strip()
    conf = "certain" if row.get("confidence") == "high" else "unsure"
    return [{
        "name": business,
        "key": key,
        "kind": "ai",
        "why": ("the business read out of the project name" +
                (f" ({note})" if note else "") +
                f" — the model was {conf} about it, and it named no client: "
                "it was shown the project title and nothing else"),
    }]


def read_missing(names: Iterable[str], *, limit: int = MAX_PER_RUN) -> dict:
    """Read the titles that have no reading yet, and store what comes back.

    Returns a report rather than raising: "we could not ask" and "there was
    nothing left to ask about" are different answers and only the second means
    there is nothing to do — the rule `connected_accounts_result()` gives in
    Google Finder.
    """
    todo = pending(names)
    total_pending = len(todo)
    if not total_pending:
        return {"ok": True, "read": 0, "stored": 0, "pending": 0,
                "ungrounded": 0, "batches": 0, "error": "",
                "note": "Every project title on this list has been read already."}
    if not _hub_ai.ready():
        return {"ok": False, "read": 0, "stored": 0, "pending": total_pending,
                "ungrounded": 0, "batches": 0,
                "error": "OPENAI_API_KEY is not set, so no titles were read.",
                "note": ""}

    todo = todo[:max(1, int(limit))]
    store = readings()
    stored = ungrounded = batches = 0
    error = ""

    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        batches += 1
        try:
            answer = _hub_ai.chat_json(
                [{"role": "system", "content": _MODEL_NOTE},
                 {"role": "user", "content": "\n".join(
                     f"{i + 1}. {t}" for i, t in enumerate(chunk))}],
                module="sites_match", purpose="project_names",
                max_tokens=2000, temperature=0)
        except Exception as exc:                            # noqa: BLE001
            # One bad batch costs its own titles and not the run: the rest are
            # still worth storing, and the ones that failed simply stay pending
            # for the next press.
            error = error or f"{type(exc).__name__}: {exc}"
            continue

        by_title = {_key(t): t for t in chunk}
        for row in (answer.get("readings") or []):
            if not isinstance(row, dict):
                continue
            title = by_title.get(_key(str(row.get("title") or "")))
            if not title:
                # An answer about a title we did not send. Dropped rather than
                # stored under a name nothing will ever look up.
                continue
            business = re.sub(r"\s+", " ", str(row.get("business") or "")).strip()
            if business and not _is_grounded(business, title):
                ungrounded += 1
                business = ""
            store[_key(title)] = {
                "title": title,
                "business": business,
                "confidence": ("high" if str(row.get("confidence") or "")
                               .lower() == "high" else "low"),
                "note": str(row.get("note") or "")[:200],
            }
            stored += 1

    try:
        jsonstore.write_json(_store_path(), store)
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "read": len(todo), "stored": 0,
                "pending": total_pending, "ungrounded": ungrounded,
                "batches": batches,
                "error": f"The readings could not be saved ({exc}).",
                "note": ""}

    left = max(0, total_pending - stored)
    return {
        "ok": not error or stored > 0,
        "read": len(todo), "stored": stored, "pending": left,
        # Named, not swallowed: a prompt that has started inventing names is
        # something to look at rather than something to quietly discard.
        "ungrounded": ungrounded,
        "batches": batches,
        "error": error,
        "note": (f"{stored} project title(s) read."
                 + (f" {ungrounded} answer(s) were discarded for naming words "
                    "that are not in the title." if ungrounded else "")
                 + (f" {left} still to read — press again." if left else "")
                 + (f" One or more batches failed: {error}" if error else "")),
    }


def forget(name: str = "") -> int:
    """Drop one reading, or all of them. Returns how many went.

    Through `jsonstore.delete_json` where the whole file goes, never a bare
    `os.remove`: removing only the file leaves the database copy to be restored
    by the next read, so the delete appears to work and then undoes itself.
    """
    store = readings()
    if not name:
        n = len(store)
        try:
            jsonstore.delete_json(_store_path())
        except Exception:                                   # noqa: BLE001
            return 0
        return n
    key = _key(name)
    if key not in store:
        return 0
    store.pop(key, None)
    try:
        jsonstore.write_json(_store_path(), store)
    except Exception:                                       # noqa: BLE001
        return 0
    return 1
