"""The job store behind both standalone HyperFrames tools.

A render here is minutes of headless Chrome on somebody else's process, so
the job has to outlive the tab that started it — the same reason a HeyGen
clip and a Runway clip are attached by their status route rather than by the
browser. What that needs is somewhere to keep the job between the submit and
the poll, and this is it.

Five rules, each a way a store like this goes wrong quietly.

**One file per job, never one file holding all of them.** Two people
rendering at the same moment would each write the whole collection back and
the second write would drop the first one's job — precisely the failure a
store exists to prevent. `hub/drafts.py`'s rule.

**Through `jsonstore`, so it survives the disk.** The Render disk is not
backed up and a redeploy hands back an empty one; a job lost there is a
render somebody waited five minutes for and cannot get back. Deleted through
`jsonstore.delete_json` and never `os.remove`, or the database mirror
restores it and the discard undoes itself.

**Nothing in it may raise.** A store that breaks the tool it serves is worse
than no store: every entry point returns a value, and a job that could not be
written costs the record of a render rather than the render.

**Bounded, and never in silence.** An unbounded job list on a 5 GB shared
disk takes every other module with it, so old finished jobs are swept — and
the sweep only ever removes a job that has *finished*, because dropping one
still rendering is the one row somebody is actually waiting on.

**The owner is recorded and read.** These are staff jobs and the list is per
person: a page showing everybody's renders is a page where somebody opens
somebody else's client's file.
"""

from __future__ import annotations

import os
import time
import uuid

from hub import jsonstore

__all__ = ["create", "get", "update", "listing", "remove", "MAX_PER_OWNER"]

# Where the rows live. Its own directory so the sweep below can list it
# without walking anything else.
_DIR = "hyperframes_jobs"

# Per owner, oldest finished job dropped first. High enough that a working day
# never reaches it, low enough that the disk cannot be filled by leaving a tab
# open — the shape `hub/drafts.py` uses.
MAX_PER_OWNER = 60

# A job nobody has looked at in a fortnight is not a job anybody is waiting
# on. The finished file is in Cloudinary; this row is only the record of it.
MAX_AGE_SECONDS = 14 * 24 * 3600

_FINISHED = ("done", "failed")


def _path(job_id: str) -> str:
    return jsonstore.data_dir(_DIR) + f"/{job_id}.json"


def create(*, tool: str, owner: str, params: dict, job: dict,
           client: str = "", label: str = "") -> dict:
    """Write down a render that has just been submitted.

    `job` is `hub/hyperframes.submit()`'s answer verbatim, so the row carries
    the provider's own job id and its own error where there was one — a
    submit that failed is still written down, because "we tried and it was
    refused" is what the list has to be able to say rather than the press
    leaving no trace.
    """
    row = {
        "id": uuid.uuid4().hex[:16],
        "tool": str(tool or "")[:40],
        "owner": str(owner or "")[:200],
        "client": str(client or "")[:200],
        "label": str(label or "")[:200],
        "params": params or {},
        "job_id": job.get("job_id"),
        "status": job.get("status") or "queued",
        "url": job.get("url"),
        "error": job.get("error"),
        "mock": bool(job.get("_mock")),
        "note": job.get("note") or "",
        "filed": None,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    _write(row)
    _sweep(row["owner"])
    return row


def get(job_id: str) -> dict | None:
    if not job_id:
        return None
    try:
        row = jsonstore.read_json(_path(str(job_id)), default=None)
    except Exception:                                    # noqa: BLE001
        return None
    return row if isinstance(row, dict) else None


def update(job_id: str, **fields) -> dict | None:
    """Merge fields onto a job.

    Merged rather than assigned, for the reason `set_music` had to be: a
    fresh dict over the row would drop whatever the other half of the request
    had just written onto it.
    """
    row = get(job_id)
    if row is None:
        return None
    row.update(fields)
    row["updated_at"] = int(time.time())
    _write(row)
    return row


def listing(owner: str, tool: str = "") -> list[dict]:
    """This person's jobs, newest first.

    Per owner rather than everybody's: these carry client names and the
    finished files behind them, and a shared list is somebody opening another
    rep's client's render.
    """
    try:
        names = sorted(os.listdir(jsonstore.data_dir(_DIR)))
    except OSError:
        # No directory yet is no jobs yet, which is the ordinary first state.
        return []
    rows = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = get(name[:-5])
        if not row or row.get("owner") != owner:
            continue
        if tool and row.get("tool") != tool:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return rows


def remove(job_id: str) -> bool:
    """Forget one job.

    Through `jsonstore.delete_json`, never `os.remove`: removing only the file
    leaves the database copy to be restored by the next read, so the delete
    appears to work and then undoes itself.
    """
    try:
        jsonstore.delete_json(_path(str(job_id)))
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _write(row: dict) -> None:
    try:
        jsonstore.write_json(_path(row["id"]), row)
    except Exception:                                    # noqa: BLE001
        # A job that could not be written costs the record and never the
        # render — the caller already has the submitted job in hand.
        pass


def _sweep(owner: str) -> None:
    """Drop this owner's oldest FINISHED jobs past the cap or the age.

    Only finished ones. A job still rendering is the one row somebody is
    actually waiting on, and sweeping it is how a render that was about to
    land becomes one nothing can attach.
    """
    try:
        rows = listing(owner)
    except Exception:                                    # noqa: BLE001
        return
    cutoff = int(time.time()) - MAX_AGE_SECONDS
    finished = [r for r in rows if (r.get("status") in _FINISHED)]
    stale = [r for r in finished if (r.get("created_at") or 0) < cutoff]
    over = finished[MAX_PER_OWNER:] if len(rows) > MAX_PER_OWNER else []
    for row in {r["id"]: r for r in (stale + over)}.values():
        remove(row["id"])
