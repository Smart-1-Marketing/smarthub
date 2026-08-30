"""
Smart 1 Hub — evergreen creative marks for the Stale Creative audit.

The audit answers "how long since we last made creative for this client".
For some campaigns that question has no useful answer: the creative is
deliberately fixed for the flight — an always-on brand spot, a sponsorship
billboard, a rebate banner that runs unchanged until the offer ends — so the
row ages every day while nothing is wrong. Left in, those rows are permanent
red on a report whose whole job is to say what to act on this week, which is
how a report stops being read.

Marking one **evergreen** says the creative will not change for that campaign
and takes the client off the list.

Four rules, each a way this goes quietly wrong:

  * **The mark is stored against the client's name, never the derived match
    key.** `hub/client_key.py` gives the reason at length: a key stored is a
    key that outlives the thing it was derived from, and the audit's matcher
    may be tightened tomorrow. The name is what a person marked; the key is
    re-derived on every read with whatever matcher the audit is using.

  * **The overlay decides, not a cache.** `stale_creative` caches the whole
    audit for five minutes and there are two gunicorn workers, so a mark made
    in one worker would be invisible in the other until its own cache expired
    — the accept-does-not-stick failure `hub/client_urls.missing()` already
    had to undo. So the marks are applied on every read of that cache, from
    the file, rather than baked into it.

  * **Nothing disappears in silence.** A list that quietly gets shorter cannot
    be told from a list that failed to load, so what was withheld is counted,
    listed under its own heading and one press away from coming back.

  * **A mark says who and when.** "This is evergreen" is somebody's decision
    about a campaign, and a decision nobody can attribute is one nobody can
    revisit.

Stored through `hub/jsonstore.py`, so it survives the Render disk being
handed back empty.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from hub import jsonstore

_LOCK = threading.Lock()
_FILE = "evergreen.json"

MAX_NOTE = 300


def _path() -> str:
    return os.path.join(jsonstore.data_dir("stale_creative"), _FILE)


def _load() -> list[dict]:
    rows = jsonstore.read_json(_path(), default=None)
    if isinstance(rows, dict):                     # {"marks": [...]}
        rows = rows.get("marks")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and str(r.get("client") or "").strip()]


def _save(rows: list[dict]) -> bool:
    return jsonstore.write_json(_path(), {"marks": rows}, indent=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def marks() -> list[dict]:
    """Every evergreen mark, newest first. Never raises."""
    try:
        rows = _load()
    except Exception:                              # noqa: BLE001
        return []
    return sorted(rows, key=lambda r: str(r.get("at") or ""), reverse=True)


def by_key(match) -> dict[str, dict]:
    """Marks keyed by the audit's own match key.

    ``match`` is the audit's client matcher, handed in rather than imported,
    so this file cannot come to disagree with the report about what counts as
    the same client.
    """
    out: dict[str, dict] = {}
    for row in marks():
        try:
            key = match(row.get("client"))
        except Exception:                          # noqa: BLE001
            key = None
        if key:
            out.setdefault(key, row)
    return out


def set_mark(client: str, on: bool, *, actor: str = "", note: str = "",
             campaign: str = "") -> dict:
    """Mark or unmark one client. Returns ``{"ok": …}`` and never raises.

    Marking the same client twice keeps the first mark's author and date and
    refreshes the note — a re-press is somebody confirming, not a second
    decision, and overwriting the date would lose when it was actually taken.
    """
    name = str(client or "").strip()
    if not name:
        return {"ok": False, "error": "No client named."}
    try:
        with _LOCK:
            rows = _load()
            lowered = name.casefold()
            existing = [r for r in rows
                        if str(r.get("client") or "").strip().casefold() == lowered]
            if on:
                if existing:
                    for r in existing:
                        if note:
                            r["note"] = str(note)[:MAX_NOTE]
                        if campaign:
                            r["campaign"] = str(campaign)[:200]
                else:
                    rows.append({
                        "client": name,
                        "campaign": str(campaign or "")[:200],
                        "note": str(note or "")[:MAX_NOTE],
                        "by": str(actor or "")[:120],
                        "at": _now(),
                    })
            else:
                rows = [r for r in rows
                        if str(r.get("client") or "").strip().casefold() != lowered]
            if not _save(rows):
                return {"ok": False,
                        "error": "The mark could not be saved. Nothing changed."}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "error": f"The mark could not be saved: {exc}"}
    return {"ok": True, "client": name, "evergreen": bool(on)}
