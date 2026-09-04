"""
Smart 1 Hub — "Skip this client" marks for the orphaned Google accounts list.

`hub/google_links.py` lists every Google resource the account index could not
join to a client, and for a lot of rows the honest answer this week is "we do
not know whose this is yet, and finding out is not a five-second job" — a
Search Console property with no domain and a name nobody recognises, say. Left
in the list, that row sits there every time the page loads, gets skimmed past,
and never actually gets worked, which is the row somebody stops trusting the
list over.

Skipping one says "not now" rather than "never": it comes off the orphaned
list a person is trying to clear this week, and it goes onto its own list —
still searchable, still attachable — for whenever somebody wants to sit down
and work through the harder ones.

Four rules, the same shape as `hub/creative_evergreen.py` and for the same
reasons:

  * **The mark is stored against the resource id, never the row's suggestions
    or its label.** A resource's name and suggested owners are read fresh from
    the account index on every request; storing a stale copy here would let
    the two drift apart the day the client book changes.

  * **The overlay decides, not a cache.** `google_links._orphan_book()` is
    held for the day, and there are two gunicorn workers, so a mark taken in
    one of them would go on being ignored by the other until its own cache
    expired — the failure `hub/client_urls.missing()` had to undo. The mark is
    applied on every read of that cache, from this file, never baked into it.

  * **Nothing disappears in silence.** A skipped resource does not vanish —
    it moves to its own list, one press away from coming back, and the count
    is on the page rather than only implied by a shorter table.

  * **A mark says who and when.** "Skip this one for now" is somebody's
    decision, and a decision nobody can attribute is one nobody can revisit.

Stored through `hub/jsonstore.py`, so it survives the Render disk being
handed back empty.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from hub import jsonstore

_LOCK = threading.Lock()
_FILE = "orphan_skips.json"

MAX_NOTE = 300


def _path() -> str:
    return os.path.join(jsonstore.data_dir("google_index"), _FILE)


def _key(resource_id: str) -> str:
    return str(resource_id or "").strip().lower()


def _load() -> list[dict]:
    rows = jsonstore.read_json(_path(), default=None)
    if isinstance(rows, dict):                     # {"marks": [...]}
        rows = rows.get("marks")
    if not isinstance(rows, list):
        return []
    return [r for r in rows
            if isinstance(r, dict) and _key(r.get("resource_id"))]


def _save(rows: list[dict]) -> bool:
    return jsonstore.write_json(_path(), {"marks": rows}, indent=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def marks() -> list[dict]:
    """Every skip mark, newest first. Never raises."""
    try:
        rows = _load()
    except Exception:                              # noqa: BLE001
        return []
    return sorted(rows, key=lambda r: str(r.get("at") or ""), reverse=True)


def by_id() -> dict[str, dict]:
    """Marks keyed by resource id, for a caller filtering a row list."""
    out: dict[str, dict] = {}
    for row in marks():
        key = _key(row.get("resource_id"))
        if key:
            out.setdefault(key, row)
    return out


def set_skip(resource_id: str, on: bool, *, actor: str = "", note: str = "",
             name: str = "", platform: str = "") -> dict:
    """Skip or unskip one resource. Returns ``{"ok": …}`` and never raises.

    Skipping the same resource twice keeps the first mark's author and date
    and refreshes the note — a re-press is somebody confirming, not a second
    decision, and overwriting the date would lose when it was actually taken.
    """
    key = _key(resource_id)
    if not key:
        return {"ok": False, "error": "No resource named."}
    try:
        with _LOCK:
            rows = _load()
            existing = [r for r in rows if _key(r.get("resource_id")) == key]
            if on:
                if existing:
                    for r in existing:
                        if note:
                            r["note"] = str(note)[:MAX_NOTE]
                        if name:
                            r["name"] = str(name)[:200]
                        if platform:
                            r["platform"] = str(platform)[:60]
                else:
                    rows.append({
                        "resource_id": str(resource_id or "").strip(),
                        "name": str(name or "")[:200],
                        "platform": str(platform or "")[:60],
                        "note": str(note or "")[:MAX_NOTE],
                        "by": str(actor or "")[:120],
                        "at": _now(),
                    })
            else:
                rows = [r for r in rows if _key(r.get("resource_id")) != key]
            if not _save(rows):
                return {"ok": False,
                        "error": "The skip could not be saved. Nothing changed."}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "error": f"The skip could not be saved: {exc}"}
    return {"ok": True, "resource_id": str(resource_id or "").strip(),
            "skipped": bool(on)}
