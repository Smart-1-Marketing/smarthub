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
still decide: exact on the normalized form or not at all, two clients under one
name propose neither, and a resemblance is a suggestion a human presses a
button on. A model that answers badly costs a candidate nobody accepts. It
cannot produce a match.

## The machinery is `hub/name_reading.py`

The batching, the grounding check, the store and the give-up live there,
because three modules in this Hub do this same job on three different messy
strings and writing it three times is the drift `hub/storage.py` exists to
stop. What is here is the part that is actually about Simvoly: the prompt's
description of what surrounds a business name in a project title, the rule
about what is not worth sending, and the shape a reading takes on its way into
the matcher.

## What is not worth sending

A placeholder. `is_placeholder()` has already answered for those, and paying a
model to look at "S1M Test" invites it to find a business in a string that has
none.

## What a reading is not

It is not stored against the client, and it is not stored as a match. The file
holds *what the model thought the business in this string was called*, which is
a reading of a string and stays true whatever the client book does next. The
join is re-derived on every report, so a client added tomorrow is matched by
the next run rather than needing the model again.
"""
from __future__ import annotations

from hub import site_names
from hub.name_reading import BATCH, MAX_PER_RUN, NameReader, prompt_for

__all__ = ["BATCH", "MAX_PER_RUN", "READER", "candidates_for", "forget",
           "pending", "read_missing", "reading_for", "readings", "state"]

READER = NameReader(
    folder="site_names",
    filename="ai_readings.json",
    module="sites_match",
    purpose="project_names",
    # A placeholder is never sent — see above.
    skip=site_names.is_placeholder,
    system_prompt=prompt_for(
        "the titles people gave to website projects in a website builder",
        "a media partner's name in front, a note about the job behind it, a "
        "version marker, a duplicate marker."),
)


# The module's own surface, kept so its callers read as what they are doing
# rather than reaching through a reader object.
def readings() -> dict:
    return READER.readings()


def reading_for(name: str, store: dict | None = None) -> dict:
    return READER.reading_for(name, store)


def pending(names) -> list[str]:
    return READER.pending(names)


def state(names) -> dict:
    return READER.state(names)


def read_missing(names, *, limit: int | None = None) -> dict:
    return READER.read_missing(names, limit=limit)


def forget(name: str = "") -> int:
    return READER.forget(name)


def candidates_for(name: str, store: dict | None = None) -> list[dict]:
    """The stored reading of one title, in `site_names.candidates()`'s shape.

    Returned as a candidate rather than as a match, so it goes through
    `exact_matches()` against the real client book exactly like a candidate a
    rule derived — and carries its own `why`, because "read out of the project
    name by the model" is a different claim from "the name with the media
    partner prefix removed" and only one of them was arrived at by a rule
    somebody can read.
    """
    business = READER.business_in(name, store)
    if not business:
        return []
    from hub.name_reading import key_for
    key = key_for(business)
    if not key:
        return []
    row = READER.reading_for(name, store)
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
