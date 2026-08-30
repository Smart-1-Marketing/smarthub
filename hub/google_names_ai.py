"""The business inside a Google resource's label, where the index gave up.

## The loosest rule in the Hub, and what is under it

`hub/google_links.suggest_for()` proposes an owner for an orphaned GA4
property, GTM container, Search Console property or Business Profile, strongest
evidence first: an id Knack already records against a client, then a domain,
then an exact name, then a near name. Its last and loosest rule is a **shared
word** — a container called "Buckeye Marina - new" against a client filed as
"Buckeye Lake Marina" matches on nothing above, and a person reading the row
can see in a second that they are the same business.

That rule is already careful: capped at three, company and platform words
excluded, a word shared by more clients than a ceiling computed against the
book thrown out, and the word itself named on the row because it is the only
evidence there is. What it cannot do is read a label the way a person does.
Google resource names are as improvised as Simvoly project titles —
"FabLocal – SERVPRO Fresno GTM", "GA4 · Twin Oaks (new prop, do not delete)",
"acct 4 buckeye lake marina" — and the business inside them is a run of words
with markers around it.

## Same shape as the other two, and the same safety

This is `hub/name_reading.py` configured for resource labels. The model is
shown the label and nothing else — **never the client book** — and answers with
a run of words out of it, grounded: every word of the answer has to appear in
the original, or it is dropped and counted.

What comes back goes through `client_key.resolve()`, the *same* call
`suggest_for()` already makes on the raw label. So the rules that decide have
not changed at all: an exact normalised name is a match, more than one client
answering to it proposes neither, and anything softer is `possible` and stays a
suggestion a human presses a button on. The reading can only change **which
string** gets asked about — it cannot change the answer, and it cannot produce
a client the book does not hold.

## It never outranks the evidence above it

A recorded id and a domain are identifiers; a reading of a label is a guess
about what somebody meant. So a reading resolves at `name` only where
`client_key` calls it exact, `possible` otherwise, and `_add()`'s existing
confidence ranking means it can never displace a stronger row that was already
there. Attaching a Google property to the wrong client files their analytics
under somebody else's record — which is why every rule in this file, including
this one, ends at a human pressing Attach.
"""
from __future__ import annotations

import re

from hub.name_reading import BATCH, MAX_PER_RUN, NameReader, prompt_for

__all__ = ["BATCH", "MAX_PER_RUN", "READER", "business_in", "forget",
           "labels_of", "pending", "read_missing", "reading_for", "readings",
           "state", "worth_reading"]

# Words a Google resource label is made of that are about Google rather than
# about a business. A label that is only these names nobody, exactly as
# "Annual renewal" names nobody on an invoice — the `hub/site_names.py` rule
# about "Main Site", on a different shelf.
_PLATFORM_WORDS = {
    "ga", "ga4", "gtm", "analytics", "tag", "manager", "container", "property",
    "properties", "account", "acct", "search", "console", "business", "profile",
    "google", "web", "website", "site", "stream", "data", "view", "new", "old",
    "test", "demo", "copy", "backup", "prod", "production", "staging", "dev",
    "main", "primary", "default", "do", "not", "delete", "temp", "tracking",
}


def worth_reading(text: str) -> str:
    """Why this label is not worth a call, or "" if it is.

    Two answers, both cases where paying a model to find a business invites it
    to find one that is not there:

    * a label made **only of platform words** — "GA4 Property (new)", "GTM
      container – test". Those name nobody.
    * a label with **almost nothing in it**.
    """
    raw = " ".join(str(text or "").split())
    if len(raw) < 4:
        return "the label is too short to name anybody"
    words = [w for w in re.split(r"[^a-z0-9]+", raw.lower()) if w]
    if not words:
        return "the label carries no words"
    if all(w in _PLATFORM_WORDS or w.isdigit() or len(w) < 2 for w in words):
        return "the label is made only of platform words, not a business"
    return ""


READER = NameReader(
    folder="google_names",
    filename="ai_readings.json",
    module="google_links",
    purpose="resource_labels",
    skip=worth_reading,
    system_prompt=prompt_for(
        "the names people gave to Google Analytics properties, Tag Manager "
        "containers and Search Console properties",
        "the platform's own words (GA4, GTM, property, container, account), a "
        "media partner's name in front, a note about the job behind it, and "
        "markers like \"new\", \"test\" or \"do not delete\"."),
)


def readings() -> dict:
    return READER.readings()


def reading_for(text: str, store: dict | None = None) -> dict:
    return READER.reading_for(text, store)


def business_in(text: str, store: dict | None = None) -> str:
    """The business this label was read as naming, or "".

    Re-grounded on read: a file written by an older prompt, or edited by hand,
    must not get past the one rule that makes this safe to feed into a matcher.
    """
    return READER.business_in(text, store)


def pending(labels) -> list[str]:
    return READER.pending(labels)


def state(labels) -> dict:
    return READER.state(labels)


def read_missing(labels, *, limit: int | None = None) -> dict:
    return READER.read_missing(labels, limit=limit)


def forget(text: str = "") -> int:
    return READER.forget(text)


def labels_of(rows) -> list[str]:
    """The resource names out of a list of orphan rows, deduped."""
    out, seen = [], set()
    for r in rows or ():
        label = str((r or {}).get("name") or "").strip()
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out
