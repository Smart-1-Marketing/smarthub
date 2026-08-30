"""Reading a business name out of a string somebody typed, once.

Three places in this Hub hold a string that *contains* a business name and is
not one: a Simvoly project title ("FabLocal -  SERVPRO of Southwest San
Antonio"), a QuickBooks invoice line description ("syrons-market.com<TAB>Syrons",
"Foreman Mechanical Services, LLC - foremanmechanical.com"), and a Google
resource label ("Buckeye Marina - new"). Each has hand-written rules that get
most of the way and then run out, and each then has the identical job left
over: hand the leftovers to a model and ask which run of words is the business.

This is that job, once. `hub/site_names_ai.py`, `hub/invoice_names.py` and
`hub/google_names_ai.py` are configurations of it — a prompt, a store and a
rule about what is not worth sending — because writing the batching, the
grounding check, the give-up and the store three times is the drift
`hub/storage.py` and `hub/images.py` exist to stop, and the next improvement
to any of it would otherwise have to land three times.

## The rules, which belong here rather than in each caller

**A reading must be *in* the string it came from.** `is_grounded()` requires
every word of the answer to appear in the original, on the normalized form. A
model asked to extract a name will occasionally expand an abbreviation, correct
a spelling, or supply the company it thinks was meant — and a tidied name is a
different string, which matches a different client or none, with nothing on the
row saying the words were changed. Ungrounded answers are dropped and
**counted**: a prompt that has started inventing is something to see rather
than something to absorb.

**The client book is never in the prompt.** Every caller feeds what comes back
into its own existing matcher, against the real book, under
`hub/client_key.py`'s rules. So the model cannot name a client — it has never
seen one — and a bad answer costs a candidate nobody accepts rather than
producing a wrong match. That is the whole safety argument, and it is a
property of *this* module refusing to accept a book, not of each caller
remembering not to pass one.

**Nothing is read twice.** Readings are keyed on the normalized source string,
so a renamed project is re-read and a re-listed one is not. That is what makes
these affordable: one pass, then free.

**It is a button, never a page load.** These calls are billed and the reports
that would trigger them are opened several times a day — the rule
`hub/brand_lookup.py` arrived at.

**Under `jsonstore.data_dir()`, never a relative path.** A bare
"something/readings.json" resolves against the working directory, lands in the
repo checkout and is wiped on every deploy — the trap CLAUDE.md names about
`os.environ.get("HUB_DATA_DIR", "data")`.

**Nothing here raises.** A reader that breaks the report it feeds is worse than
one that reads nothing, so every entry point returns a value and a failed batch
costs its own strings and not the run.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Iterable

from hub import ai as _hub_ai
from hub import jsonstore
from hub.client_key import normalise_name

# How many strings go in one request. Twenty-five keeps the answer well inside
# the token ceiling with room for a note per row, and makes a thousand-row
# backlog about forty calls.
BATCH = 25

# A ceiling on one press, so a first run on a fresh book cannot spend the
# afternoon's budget in one request nobody is watching. The page says how many
# are left and the button can be pressed again.
MAX_PER_RUN = 400


def key_for(text: str) -> str:
    """The lookup key: the normalized form, so whitespace and punctuation in
    the raw string do not make two keys out of one."""
    return normalise_name(text)


def is_grounded(answer: str, original: str) -> bool:
    """Is every word of the answer actually in the string it came from?

    The one check that makes any of this safe to feed into a matcher.
    """
    a, o = normalise_name(answer), normalise_name(original)
    if not a or not o:
        return False
    words = set(o.split())
    return all(w in words for w in a.split())


class NameReader:
    """One configured reader: a prompt, a store, and what not to send.

    `skip` answers "is this string not worth a call" — a placeholder project
    title, a description that is only a label. It is the caller's rule because
    only the caller's hand-written matcher knows what it has already answered
    for, and paying a model to look at a string that names nobody invites it to
    find somebody.
    """

    def __init__(self, *, folder: str, filename: str, system_prompt: str,
                 module: str, purpose: str,
                 skip: Callable[[str], str] | None = None,
                 batch: int = BATCH, max_per_run: int = MAX_PER_RUN):
        self.folder = folder
        self.filename = filename
        self.system_prompt = system_prompt
        self.module = module
        self.purpose = purpose
        self.skip = skip or (lambda _t: "")
        self.batch = batch
        self.max_per_run = max_per_run

    # ------------------------------------------------------------- storage
    def _path(self) -> str:
        return os.path.join(jsonstore.data_dir(self.folder), self.filename)

    def readings(self) -> dict:
        """What is on file. Reads nothing from a provider and never raises."""
        try:
            data = jsonstore.read_json(self._path(), default={}) or {}
            return data if isinstance(data, dict) else {}
        except Exception:                                   # noqa: BLE001
            return {}

    def reading_for(self, text: str, store: dict | None = None) -> dict:
        """The stored reading of one string, or {}.

        `store` is the whole map, read once by the caller. A report that asks
        this per row on a thousand rows is a thousand file reads, each asking
        jsonstore to restore from the database mirror on a miss.
        """
        src = self.readings() if store is None else store
        return src.get(key_for(text)) or {}

    def business_in(self, text: str, store: dict | None = None) -> str:
        """The business this string was read as naming, or "".

        Re-checks the grounding on read, not only on write: a file written by
        an older prompt, or edited by hand, must not get past the one rule that
        makes this safe.
        """
        row = self.reading_for(text, store)
        business = str(row.get("business") or "").strip()
        if not business or not is_grounded(business, text):
            return ""
        return business

    # -------------------------------------------------------------- asking
    def pending(self, texts: Iterable[str]) -> list[str]:
        """Strings worth spending a call on: named, not skipped, not read."""
        have = self.readings()
        out, seen = [], set()
        for raw in texts or ():
            text = re.sub(r"\s+", " ", str(raw or "")).strip()
            key = key_for(text)
            if not text or not key or key in seen or key in have:
                continue
            if self.skip(text):
                continue
            seen.add(key)
            out.append(text)
        return out

    def state(self, texts: Iterable[str]) -> dict:
        """How much has been read, and how much is left.

        Tri-state on purpose. "Nothing has been read", "everything readable has
        been read" and "we could not look at the file" are three situations,
        and only the first is a button somebody should press — the answer
        `connected_accounts_result()` gives in Google Finder, on a smaller
        question. A file we could not open must never read as zero.
        """
        try:
            return {"measured": True, "read": len(self.readings()),
                    "pending": len(self.pending(texts)),
                    "configured": bool(_hub_ai.ready()), "error": ""}
        except Exception as exc:                            # noqa: BLE001
            return {"measured": False, "read": 0, "pending": 0,
                    "configured": False,
                    "error": f"{type(exc).__name__}: {exc}"}

    def read_missing(self, texts: Iterable[str], *,
                     limit: int | None = None) -> dict:
        """Read what has no reading yet, and store what comes back.

        Returns a report rather than raising: "we could not ask" and "there was
        nothing left to ask about" are different answers, and only the second
        means there is nothing to do.
        """
        todo = self.pending(texts)
        total = len(todo)
        if not total:
            return {"ok": True, "read": 0, "stored": 0, "pending": 0,
                    "ungrounded": 0, "batches": 0, "error": "",
                    "note": "Everything on this list has been read already."}
        if not _hub_ai.ready():
            return {"ok": False, "read": 0, "stored": 0, "pending": total,
                    "ungrounded": 0, "batches": 0,
                    "error": "OPENAI_API_KEY is not set, so nothing was read.",
                    "note": ""}

        todo = todo[:max(1, int(limit or self.max_per_run))]
        store = self.readings()
        stored = ungrounded = batches = 0
        error = ""

        for start in range(0, len(todo), self.batch):
            chunk = todo[start:start + self.batch]
            batches += 1
            try:
                answer = _hub_ai.chat_json(
                    [{"role": "system", "content": self.system_prompt},
                     {"role": "user", "content": "\n".join(
                         f"{i + 1}. {t}" for i, t in enumerate(chunk))}],
                    module=self.module, purpose=self.purpose,
                    max_tokens=2000, temperature=0)
            except Exception as exc:                        # noqa: BLE001
                # One bad batch costs its own strings and not the run: the rest
                # are still worth storing, and these simply stay pending.
                error = error or f"{type(exc).__name__}: {exc}"
                continue

            by_source = {key_for(t): t for t in chunk}
            for row in (answer.get("readings") or []):
                if not isinstance(row, dict):
                    continue
                source = by_source.get(key_for(str(row.get("source") or "")))
                if not source:
                    # An answer about a string we did not send. Dropped rather
                    # than stored under a key nothing will ever look up.
                    continue
                business = re.sub(
                    r"\s+", " ", str(row.get("business") or "")).strip()
                if business and not is_grounded(business, source):
                    ungrounded += 1
                    business = ""
                store[key_for(source)] = {
                    "source": source,
                    "business": business,
                    "confidence": ("high" if str(row.get("confidence") or "")
                                   .lower() == "high" else "low"),
                    "note": str(row.get("note") or "")[:200],
                }
                stored += 1

        try:
            jsonstore.write_json(self._path(), store)
        except Exception as exc:                            # noqa: BLE001
            return {"ok": False, "read": len(todo), "stored": 0,
                    "pending": total, "ungrounded": ungrounded,
                    "batches": batches,
                    "error": f"The readings could not be saved ({exc}).",
                    "note": ""}

        left = max(0, total - stored)
        return {
            "ok": not error or stored > 0,
            "read": len(todo), "stored": stored, "pending": left,
            # Named, not swallowed.
            "ungrounded": ungrounded, "batches": batches, "error": error,
            "note": (f"{stored} read."
                     + (f" {ungrounded} answer(s) were discarded for naming "
                        "words that are not in the original."
                        if ungrounded else "")
                     + (f" {left} still to read — press again." if left else "")
                     + (f" One or more batches failed: {error}"
                        if error else "")),
        }

    def forget(self, text: str = "") -> int:
        """Drop one reading, or all of them. Returns how many went.

        Through `jsonstore.delete_json` where the whole file goes, never a bare
        `os.remove`: removing only the file leaves the database copy to be
        restored by the next read, so the delete appears to work and then
        undoes itself.
        """
        store = self.readings()
        if not text:
            n = len(store)
            try:
                jsonstore.delete_json(self._path())
            except Exception:                               # noqa: BLE001
                return 0
            return n
        key = key_for(text)
        if key not in store:
            return 0
        store.pop(key, None)
        try:
            jsonstore.write_json(self._path(), store)
        except Exception:                                   # noqa: BLE001
            return 0
        return 1


def prompt_for(what: str, shapes: str) -> str:
    """The system prompt, with the four rules every caller needs held here.

    `what` names the kind of string ("titles people gave to website projects"),
    `shapes` describes what tends to surround the business name in it. Only
    those two vary — the rules do not, and a caller writing its own prompt is
    a caller that can quietly drop the rule about not inventing a word.
    """
    return (
        f"You are reading {what}. Each one usually contains the name of a real "
        f"business, with other things around it: {shapes}\n\n"
        "For each one, answer with the run of words that is the BUSINESS's own "
        "name.\n\n"
        "Rules you must follow exactly:\n"
        "1. The business name you return must be words that appear in the "
        "original, in the same order. Do not expand abbreviations, do not "
        "correct spelling, do not add a word that is not there, and do not "
        "supply a company you think was meant. Copy the run of words out.\n"
        "2. If it names no business — it is a personal name, an email address, "
        "a label, a test or a placeholder — return an empty business and say "
        "why.\n"
        "3. If you cannot tell which part is the business, return an empty "
        "business rather than guessing.\n"
        "4. `confidence` is \"high\" only when it plainly contains a business "
        "name and you are certain which words those are.\n\n"
        "Return JSON: {\"readings\": [{\"source\": \"<the original, copied "
        "exactly>\", \"business\": \"<the business's name, or empty>\", "
        "\"confidence\": \"high\" | \"low\", \"note\": \"<a short reason, for "
        "a person reading the row>\"}]}"
    )
