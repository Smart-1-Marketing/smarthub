"""Unfinished work, kept where the person who was doing it can find it again.

## Why this exists

Building an insertion order or a proposal is fifteen minutes of concentration
and a rep almost never gets fifteen uninterrupted minutes. A phone call, a
laptop closing, a browser tab reclaimed by the operating system — and until
now the two builders lost that interruption in opposite directions:

  * **The Proposal Builder saved the work and lost the place.** It autosaves
    every keystroke to the server, so nothing is lost — and reopening the
    quote put the rep back at step one of fourteen, clicking Continue until
    they found where they had been.
  * **The IO Builder kept the place and could lose the work.** Its draft went
    to `localStorage`: the right instinct, and it survives exactly one
    browser. Somebody who picks up a different laptop, opens a private
    window, or has site data cleared finds no draft at all — and nothing on
    any screen says an unfinished IO exists, so it is simply started again
    from the beginning.

This is the half neither had: a draft on the **server**, so it survives the
machine, and a list, so somebody can find it without remembering it exists.

## The rules

  * **One file per draft, never one file holding all of them.** Two reps
    autosaving at the same moment would each write the whole collection back,
    and the second write would silently drop the first one's work — which is
    the exact failure a draft store exists to prevent.
  * **Through `jsonstore`, so it outlives the disk.** Render's disk is not
    backed up and a deploy can hand back an empty one; a draft that only ever
    existed there is a draft the next deploy loses. `jsonstore` mirrors every
    write into the database and `maybe_restore()` puts them back at boot.
  * **Nothing here raises.** A draft is insurance, and insurance that breaks
    the thing it insures is worse than none: every entry point returns a
    value and swallows its own failure, so a rep never loses a form because
    the autosave behind it had a bad day.
  * **Bounded, and never silently.** A per-owner cap and a per-draft size cap,
    because an autosave loop that fills the 5 GB disk takes the whole Hub with
    it. When the cap drops the oldest draft, the save says so rather than
    quietly discarding somebody's work.
  * **The listing carries no state.** It is read into a page and shows titles,
    owners and times; the blob is fetched only when a draft is actually
    resumed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid

from hub import jsonstore

log = logging.getLogger(__name__)

KINDS = ("io", "proposal")

# A rep with more than this many unfinished drafts has a list nobody reads,
# and the disk is shared with everything else the Hub keeps.
MAX_PER_OWNER = 25
# One IO's state is tens of kilobytes. Half a megabyte is room for the largest
# real one and a firm stop well short of anything that could fill the disk.
MAX_BYTES = 512_000

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def new_id() -> str:
    return uuid.uuid4().hex[:20]


def _dir() -> str:
    return jsonstore.data_dir("drafts")


def _path(draft_id: str) -> str:
    return os.path.join(_dir(), f"{draft_id}.json")


def _valid(draft_id) -> str:
    """A draft id we minted, or "". Never a path fragment from a request."""
    raw = str(draft_id or "").strip()
    return raw if _ID_RE.match(raw) else ""


def _clean(value, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def save(kind: str, draft_id: str = "", *, owner: str = "", title: str = "",
         step=0, state=None, extra: dict | None = None) -> dict:
    """Write one draft. Returns what happened, including anything it dropped.

    `step` is deliberately free-form — the IO builder counts questions and the
    proposal builder counts wizard steps, and a draft store that insisted on
    one meaning of "where you were" would be wrong for one of them.
    """
    out = {"ok": False, "id": "", "dropped": [], "error": ""}
    if kind not in KINDS:
        out["error"] = f"unknown kind {kind!r}"
        return out
    draft_id = _valid(draft_id) or new_id()
    row = {
        "id": draft_id,
        "kind": kind,
        "owner": _clean(owner, 160),
        "title": _clean(title, 200) or "Untitled",
        "step": step,
        "updated_at": time.time(),
        "state": state if state is not None else {},
    }
    if isinstance(extra, dict):
        row["extra"] = extra
    try:
        blob = json.dumps(row)
    except (TypeError, ValueError) as exc:
        out["error"] = f"draft could not be serialized: {exc}"
        return out
    if len(blob) > MAX_BYTES:
        out["error"] = ("This draft is too large to keep on the server; your "
                        "browser still has it.")
        return out
    try:
        # Written before the cap is applied, so the newest draft is never the
        # one dropped to make room for itself.
        jsonstore.write_json(_path(draft_id), row)
        out["dropped"] = _enforce_cap(row["owner"], kind, keep=draft_id)
        out["ok"] = True
        out["id"] = draft_id
    except Exception as exc:                            # noqa: BLE001
        log.warning("draft save failed: %s", exc)
        out["error"] = "not saved on the server"
    return out


def get(draft_id: str) -> dict | None:
    """One draft, with its state. None for anything this did not write."""
    draft_id = _valid(draft_id)
    if not draft_id:
        return None
    try:
        row = jsonstore.read_json(_path(draft_id), default=None)
    except Exception as exc:                            # noqa: BLE001
        log.warning("draft read failed: %s", exc)
        return None
    return row if isinstance(row, dict) and row.get("id") == draft_id else None


def _rows() -> list[dict]:
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return []
    rows = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = get(name[:-5])
        if row:
            rows.append(row)
    return rows


def listing(kind: str = "", owner: str = "") -> list[dict]:
    """Unfinished drafts, newest first, without the state blob.

    `owner` sorts rather than filters: on a team this size, a colleague's
    half-finished insertion order is something somebody covering for them
    needs to be able to find, and hiding it is how the same IO gets built
    twice. Whose it is is on the row.
    """
    me = _clean(owner, 160).lower()
    rows = [r for r in _rows() if not kind or r.get("kind") == kind]
    out = []
    for row in rows:
        out.append({"id": row.get("id", ""), "kind": row.get("kind", ""),
                    "owner": row.get("owner", ""), "title": row.get("title", ""),
                    "step": row.get("step", 0),
                    "updated_at": row.get("updated_at", 0),
                    "mine": bool(me) and str(row.get("owner", "")).lower() == me})
    out.sort(key=lambda r: (not r["mine"], -float(r["updated_at"] or 0)))
    return out


def delete(draft_id: str) -> bool:
    """Remove a draft. Through jsonstore, or the mirror restores it.

    `os.remove` on a mirrored file is the one way the backup bites you: the
    delete appears to work and the next read puts the file back.
    """
    draft_id = _valid(draft_id)
    if not draft_id:
        return False
    try:
        return bool(jsonstore.delete_json(_path(draft_id)))
    except Exception as exc:                            # noqa: BLE001
        log.warning("draft delete failed: %s", exc)
        return False


def _enforce_cap(owner: str, kind: str, keep: str = "") -> list[str]:
    """Hold one owner to MAX_PER_OWNER of a kind, oldest dropped, named."""
    if not owner:
        return []
    mine = [r for r in _rows()
            if r.get("kind") == kind
            and str(r.get("owner", "")).lower() == owner.lower()]
    if len(mine) <= MAX_PER_OWNER:
        return []
    mine.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    dropped = []
    for row in mine[MAX_PER_OWNER:]:
        if row.get("id") == keep:
            continue
        if delete(row.get("id", "")):
            dropped.append(row.get("title") or row.get("id", ""))
    return dropped
