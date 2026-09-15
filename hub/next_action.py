"""The one sentence at the top of Client 360: what to do about this client next.

Client 360 already reads three sources for their own cards -- the health
strip (hub/record_health.py), the Coming up card (hub/client_upcoming.py) and
Pipeline & leads (hub/suite_pipeline.py) -- and each says a great deal, in a
pill or a row, about one facet of the record. Nothing on the page said which
of it mattered most this morning. A rep opened a record with an expired
quote, a cold pipeline and a lapsing domain and had to read every card to
notice any of it, and the record with nothing outstanding looked identical to
the record with three things wrong until somebody had read all nineteen
cards.

This is a fourth reading of no new source: `pick()` takes the three payloads
those cards already computed and orders what they found, worst first, into
one line. It is not a fourth network call, and it is not a fourth idea of
what "overdue" means -- an insertion order ending in nine days is `bad`
because `client_upcoming.py` already decided so; this only orders what is
already `bad` above what is already `warn`.

Priority, most urgent first, each already-decided fact from its own source:

  1. A dated deadline already inside its own `bad` window (an IO ending, or
     already past, a domain renewing, or already past) -- the one fixed
     external date on the record.
  2. Leads gone cold in the client's own Suite pipeline -- money already
     spent on marketing quietly stalling.
  3. Anything the health strip's own queue already flagged `bad` (a lapsed
     proposal, no live product, ...).
  4. A dated deadline in its `warn` window.
  5. Anything the health strip's own queue flagged `warn` (no activity in
     90 days is one of these -- `record_health.py` already raises it there).
  6. The Email Creator skill switched on and its last saved readiness check
     answering not ready -- read from the skill's own stored record, never a
     fresh probe: this module makes no network call of its own.

Nothing here decides "bad" or "warn" -- that judgment belongs to the source
that measured it, so this line and that card can never disagree about
whether something is wrong, only about which wrong thing to say first.

Rules this file keeps, the ones every other Client 360 reader keeps:

  * **Nothing here may raise.** `for_client()` reads the three sources, each
    of which already never raises, and `pick()` is pure over what they hand
    back.
  * **Absent is not clear.** If none of the three sources measured anything
    at all, `measured` is False and the line says so rather than reading as
    "nothing to do".
  * **The idle line is a real answer, not silence.** A client with nothing
    outstanding gets a sentence saying so, in the same state a healthy pill
    uses elsewhere on this page.
"""
from __future__ import annotations

STATES = ("bad", "warn", "ok")


def _line(text: str, state: str, *, go: str = "overview", href: str = "") -> dict:
    assert state in STATES
    return {"measured": True, "text": text, "state": state, "go": go, "href": href}


def _idle(name: str) -> dict:
    return _line(f"Nothing urgent on file for {name}.", "ok")


def _dated(items: list[dict], want_state: str) -> dict | None:
    """The soonest upcoming item already in `want_state` -- items() is
    already sorted soonest-first by client_upcoming.for_client()."""
    for it in items:
        if it.get("state") != want_state:
            continue
        label = str(it.get("label") or "").strip()
        what = str(it.get("what") or "").strip()
        days = it.get("days")
        if days is None:
            when = "no date on file"
        elif days < 0:
            when = f"{-days}d ago"
        elif days == 0:
            when = "today"
        else:
            when = f"in {days}d"
        text = f"{label}" + (f" -- {what}" if what else "") + f" ({when})."
        return _line(text, "bad" if want_state == "bad" else "warn",
                     href=it.get("href") or "")
    return None


def _queued(queue: list[dict], want_level: str) -> dict | None:
    for q in queue:
        if q.get("level") != want_level:
            continue
        title = str(q.get("title") or "").strip()
        if not title:
            continue
        return _line(title, "bad" if want_level == "bad" else "warn",
                     go=str(q.get("section") or "overview"),
                     href=str(q.get("href") or ""))
    return None


def _cold_pipeline(pipeline: dict | None) -> dict | None:
    p = pipeline or {}
    if p.get("state") != "connected":
        return None
    stale = int((p.get("totals") or {}).get("stale") or 0)
    if stale <= 0:
        return None
    days = p.get("stale_days") or 30
    n = "1 lead has" if stale == 1 else f"{stale} leads have"
    return _line(f"{n} gone cold in the pipeline -- no update in {days}+ days.",
                 "warn", href=p.get("suite_url") or "")


def _email_not_ready(skills: dict | None) -> dict | None:
    """The Email Creator's own last saved readiness check, read from the
    skill's stored record -- never a fresh probe. `activate()` is the only
    thing that writes it, so a skill switched on and never (re)verified since
    reads as no check at all, not as failing, which is the honest answer."""
    rec = ((skills or {}).get("email") or {})
    if not rec.get("active"):
        return None
    chk = rec.get("check") or {}
    if not chk:
        return None
    if chk.get("ready"):
        return None
    return _line("The Email Creator is switched on but is not ready to send "
                 "-- " + (chk.get("detail") or "the last check found a problem") + ".",
                 "warn", go="skills")


def pick(name: str, *, health: dict | None = None, upcoming: dict | None = None,
         pipeline: dict | None = None, skills: dict | None = None) -> dict:
    """One line, worst first, from data the three cards already computed.

    Every argument is one of those cards' own payload; a caller with no
    reading for a source passes None or {} and this treats it as unmeasured
    for that source rather than guessing.
    """
    name = str(name or "").strip()
    if not name:
        return {"measured": False, "text": "", "state": "ok", "go": "", "href": ""}

    health = health or {}
    upcoming = upcoming or {}
    pipeline = pipeline or {}
    up_items = list(upcoming.get("items") or [])
    queue = list(health.get("queue") or [])

    any_measured = ("pills" in health) or bool(upcoming.get("measured")) \
        or pipeline.get("state") in ("connected", "not_connected")
    if not any_measured:
        return {"measured": False, "text": "", "state": "ok", "go": "", "href": ""}

    for step in (
        lambda: _dated(up_items, "bad"),
        lambda: _cold_pipeline(pipeline),
        lambda: _queued(queue, "bad"),
        lambda: _dated(up_items, "warn"),
        lambda: _queued(queue, "warn"),
        lambda: _email_not_ready(skills),
    ):
        hit = step()
        if hit:
            return hit
    return _idle(name)


def for_client(name: str, url: str = "", today=None) -> dict:
    """The route's own reader: pulls the three sources and picks. Never
    raises -- each source it reads already answers rather than raising, and
    a source this could not even import is read as unmeasured for that
    source, not as a crash of the whole line."""
    health = upcoming = pipeline = skills = None
    try:
        from . import record_health
        health = record_health.client360(name, today=today)
    except Exception:                              # noqa: BLE001
        health = {}
    try:
        from . import client_upcoming
        upcoming = client_upcoming.for_client(name, url, today)
    except Exception:                              # noqa: BLE001
        upcoming = {}
    try:
        from . import suite_pipeline
        pipeline = suite_pipeline.for_client(name, url)
    except Exception:                              # noqa: BLE001
        pipeline = {}
    try:
        from modules.skills360 import store as skills_store
        skills = (skills_store.get(name) or {}).get("skills")
    except Exception:                              # noqa: BLE001
        skills = None
    return pick(name, health=health, upcoming=upcoming, pipeline=pipeline, skills=skills)
