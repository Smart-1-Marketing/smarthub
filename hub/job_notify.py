"""A pointer to a background job somebody is waiting on, so the Hub can say
"it's done, come back" on whatever page they are on when it finishes.

HyperFrames' two standalone tools already answer "is my render done" —
`modules/hyperframes_tools/jobs.py` is the store, polled from the tool's own
page. What that page cannot do is tell somebody who has since moved on to
another tool that the answer changed. A render takes minutes, exactly like
HeyGen and Runway, and nobody sits on one page for minutes waiting; they go
back to what they were doing and the Hub has no way to interrupt them when
it lands.

This is deliberately *not* a second job store. Each tool keeps its own
detailed state — params, the finished URL, whether it has been filed against
a client — the way `modules/hyperframes_tools/jobs.py` already does, and
that store stays the one place a tool's own page reads from. This is a
lightweight pointer a tool registers *alongside* its own record: who is
waiting, what to call it, where the status can be checked, and where
pressing "View" should send them. `hub-job-notify.js` polls
`/api/background-jobs/mine` from wherever the owner happens to be and tells
them once, the way `hub-qa-nudge.js` already tells them about a review
waiting on their answer.

Four rules, the ones `modules/hyperframes_tools/jobs.py` already states for
its own store, because a pointer that lives on the shared disk is subject to
every one of them:

**One file per pointer, never one file holding all of them.** Two people
finishing renders at the same moment must not have the second overwrite the
first — `hub/drafts.py`'s rule, the one this whole file is modeled on
sharing with `modules/hyperframes_tools/jobs.py`.

**Through `jsonstore`, so a redeploy does not lose it.** A pointer to a
render finished five minutes before a restart is exactly the kind of state
this Hub's own backup rule exists for. Deleted through
`jsonstore.delete_json`, never `os.remove`, or the database mirror restores
it and the sweep below undoes nothing.

**Nothing in it may raise.** A notification system that breaks the page it
is trying to notify from is worse than no notification — every entry point
returns a value, and a caller that could not register a pointer has lost a
"come back" nudge, never the render itself.

**Bounded, and only ever the finished ones.** A pointer nobody has read
about a render that finished days ago is clutter, not a nudge — swept the
same way `modules/hyperframes_tools/jobs.py` sweeps its own rows, and a
pointer still `queued`/`rendering` is never touched by the sweep, because
that is the one row somebody is still waiting on.
"""

from __future__ import annotations

import os
import time
import uuid

from hub import jsonstore

__all__ = ["register", "update", "mine", "forget", "RUNNING", "FINISHED"]

_DIR = "background_jobs"

# The same shape `modules/hyperframes_tools/jobs.py` uses, because the two
# stores are read by an owner reasoning about the same kind of wait.
MAX_PER_OWNER = 40
MAX_AGE_SECONDS = 48 * 3600

RUNNING = ("queued", "rendering", "pending", "processing")
FINISHED = ("done", "failed")


def _path(pointer_id: str) -> str:
    return jsonstore.data_dir(_DIR) + f"/{pointer_id}.json"


def register(*, owner: str, tool: str, label: str, return_url: str,
             status: str = "queued", id: str = "",  # noqa: A002
             poll_url: str = "") -> dict:
    """Somebody just started a job worth telling them about when it lands.

    `tool` is a short machine name ("paint-animation", "commercial_render")
    used only for grouping; `label` is what the popup actually shows
    ("Paint animation — Smart 1 Marketing gets you found"), and `return_url`
    is where "View" sends them. Neither is validated against a fixed list —
    any tool may register a pointer, which is the whole point of this being
    generic rather than one more HyperFrames-only store.

    `id` is optional: a caller that already has its own id for this job
    (`modules/hyperframes_tools/jobs.py`'s own row id, say) may pass it
    through, so `update()` can be called with the same id the caller's own
    status route already has in hand rather than needing a second lookup
    table cross-referencing one id to the other.

    `poll_url` is what makes this follow somebody who has navigated away,
    rather than only somebody who left the tab open. A job's status here
    otherwise only ever advances when *that tool's own page* polls it and
    calls `update()` — the reason HyperFrames' own `_poll()` writes the
    finished URL onto its row on any request, "so closing the tab does not
    lose a render nobody is going to start again." A person who wandered off
    to another tool is not on that page any more, so `hub-job-notify.js`
    fetches `poll_url` itself for any pointer still running, same-origin,
    which reaches the tool's *own* status route and gets the identical
    write-through for free. Optional: a tool with no independent way to be
    polled simply will not self-advance from elsewhere, and its pointer sits
    at its last known status until somebody does visit its own page.
    """
    row = {
        "id": str(id or uuid.uuid4().hex[:16])[:64],
        "owner": str(owner or "")[:200],
        "tool": str(tool or "")[:60],
        "label": str(label or "")[:200],
        "return_url": str(return_url or "")[:400],
        "poll_url": str(poll_url or "")[:400],
        "status": str(status or "queued")[:20],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    _write(row)
    _sweep(row["owner"])
    return row


def update(pointer_id: str, *, status: str) -> dict | None:
    """Move a pointer to its job's current state.

    Called from the same place the tool's own store is updated — the write-
    through a status poll already does — so a pointer never needs its own
    poll of the provider; it just mirrors whatever the tool's own status
    route already learned.
    """
    row = _get(pointer_id)
    if row is None:
        return None
    row["status"] = str(status or row["status"])[:20]
    row["updated_at"] = int(time.time())
    _write(row)
    if row["status"] in FINISHED:
        # The moment a pointer finishes is exactly when the cap should
        # apply — sweeping only from register() would leave a whole batch
        # marked done by update() alone uncounted until somebody's next,
        # unrelated render started.
        _sweep(row["owner"])
    return row


def mine(owner: str) -> list[dict]:
    """This person's pointers, newest first.

    Per owner, never everybody's: `hub-job-notify.js` polls this from any
    page, and a shared list would tell one rep about another rep's render.
    """
    try:
        names = sorted(os.listdir(jsonstore.data_dir(_DIR)))
    except OSError:
        return []
    rows = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = _get(name[:-5])
        if not row or row.get("owner") != owner:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return rows


def forget(pointer_id: str) -> bool:
    """Drop one pointer — a job somebody has already been told about, or one
    they dismissed. Through `jsonstore.delete_json`, never `os.remove`, or
    the database mirror restores it and the discard undoes itself."""
    try:
        jsonstore.delete_json(_path(str(pointer_id)))
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _get(pointer_id: str) -> dict | None:
    if not pointer_id:
        return None
    try:
        row = jsonstore.read_json(_path(str(pointer_id)), default=None)
    except Exception:                                    # noqa: BLE001
        return None
    return row if isinstance(row, dict) else None


def _write(row: dict) -> None:
    try:
        jsonstore.write_json(_path(row["id"]), row)
    except Exception:                                    # noqa: BLE001
        # A pointer that could not be written costs the nudge, never the job
        # it was pointing at — the caller's own store already has the row.
        pass


def _sweep(owner: str) -> None:
    """Drop this owner's oldest FINISHED pointers past the cap or the age.

    Only finished ones — the rule `modules/hyperframes_tools/jobs.py`
    already states: a pointer still running is the one row somebody is
    actually waiting on, and sweeping it is how a render that was about to
    land stops being announced.
    """
    try:
        rows = mine(owner)
    except Exception:                                    # noqa: BLE001
        return
    cutoff = int(time.time()) - MAX_AGE_SECONDS
    finished = [r for r in rows if r.get("status") in FINISHED]
    stale = [r for r in finished if (r.get("created_at") or 0) < cutoff]
    over = finished[MAX_PER_OWNER:] if len(rows) > MAX_PER_OWNER else []
    for row in {r["id"]: r for r in (stale + over)}.values():
        forget(row["id"])
