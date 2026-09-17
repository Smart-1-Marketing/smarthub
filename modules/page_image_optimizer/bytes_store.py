"""The optimized image bytes, in the database rather than on one instance.

`store.py` holds a scan between the moment it is run and the moment it is
saved: the job's metadata as JSON (already mirrored into the database by
`hub/jsonstore.py`) and, beside it, the optimized `.webp` and its `.preview`.
Those bytes were the last thing in this repository written to the Render disk
with no copy anywhere else — the one finding `jsonstore.disk_binary_writers()`
reported when it was written.

## What was wrong with the disk, exactly

`store.py`'s own docstring explains why the bytes are not in a module-level
dict:

    the Hub runs more than one worker, and an in-memory batch would vanish the
    moment a save landed on a different worker than the scan

That reasoning is right and stops one level short. A disk is shared by the two
gunicorn **workers** and is local to one **instance** — so the same sentence,
with one word changed, is the remaining defect. A scan on one instance and a
save routed to another finds no bytes, and `api_save` answers *"The optimized
file expired before saving"* about a file that did not expire. The person
loses the batch, including the alt text and filenames they had just edited.

It is not happening today: a Render service with a disk attached cannot deploy
zero-downtime, because the disk mounts to one instance at a time, so there is
never a second instance. It is a store that had to move **before** the disk
could be dropped.

## Why the database and not Cloudinary

Cloudinary is what this repo uses for binary, and for a client's saved image it
is still the answer — `archive.upload()` puts the kept images in
`smart1-seo-images` and that does not change. These are different. They are a
45-minute scratch buffer: the tool scans a page, optimizes every candidate, and
the person keeps some and skips the rest. Uploading on the way in would put
**every scanned image** into the account, including the ones nobody keeps, and
bill for them. Todd chose the database for exactly that reason.

## The disk write is kept as a fallback

`put_bytes()` tries the database and falls back to the file on failure, which
is Fan Radio's rule: a render that cost a download and real CPU is not thrown
away because a backend would not answer. The fallback is per-instance, which is
the behaviour it replaces rather than a regression — a database outage leaves
this tool exactly as good as it was, and no worse.

That is why `modules/page_image_optimizer/store.py` keeps an entry in
`DISK_BINARY_EXEMPT` rather than losing one. `test_jsonstore.py` asserts every
exempted file really does write bytes, so the entry cannot outlive the write it
describes.

## No migration

Nothing is carried across. The bytes expire in 45 minutes
(`PAGE_IMAGES_TTL_MINUTES`) and the sweep runs on every scan, so whatever is on
the disk at deploy time ages out on its own within the hour — and a job
half-finished across a deploy already answered with the expiry message. An
import would move data that is about to be deleted.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    from sqlalchemy import (Column, DateTime, LargeBinary, MetaData, String,
                            Table, delete, insert, select, update)
    _SA_ERROR = ""
except Exception as exc:                                # noqa: BLE001
    Table = None                                        # type: ignore
    _SA_ERROR = f"{type(exc).__name__}: {exc}"

_db_lock = threading.Lock()
_engine = None
_table = None
_ready = False
_init_error = ""
_init_retry_at = 0.0

#: Cached for this long rather than for the life of the worker, the reason
#: hub/lead_store.py gives: a Hub that came up while Render's Postgres was
#: waking would otherwise put every scan of that boot on the disk.
INIT_RETRY_SECONDS = 120

#: Above this a blob is refused and goes to the disk instead. The optimizer
#: caps its download at 25 MB and emits a WebP well under that, so this is not
#: a limit anything normal meets -- it is here so one pathological page cannot
#: put 25 MB rows into the Hub database, which is shared with every other
#: store.
MAX_BLOB_BYTES = 16 * 1024 * 1024


def _one_line(text) -> str:
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return lines[0][:200] if lines else ""


def _reason(exc: Exception) -> str:
    """The type and the first line, never the bound parameters.

    A SQLAlchemy error carries the statement and its values, and the values
    here are image bytes -- `hub/lead_store.py`'s rule, for a different reason:
    an untrimmed message would put a megabyte of WebP into a log line.
    """
    return f"{type(exc).__name__}: {_one_line(exc)}"


def _init() -> bool:
    """Open the engine and make sure the table exists. Once, lazily.

    Never raises: a scan that cannot reach the database writes to the disk,
    which is what this replaces, and a raise here would lose the optimize work
    instead.
    """
    global _engine, _table, _ready, _init_error, _init_retry_at
    with _db_lock:
        if _ready or time.time() < _init_retry_at:
            return _ready
        _init_retry_at = time.time() + INIT_RETRY_SECONDS
        if Table is None:
            _init_error = f"SQLAlchemy unavailable ({_SA_ERROR})"
            return False
        try:
            from hub import extensions
            _engine = extensions.engine_for()
            meta = MetaData()
            _table = Table(
                "page_image_bytes", meta,
                # A composite primary key rather than a generated seq, which
                # is what every other store here needed. Those wanted append
                # order; this one is a keyed cache and (job, key) IS the
                # identity. It also sidesteps the trap the others carry a
                # paragraph about -- a generated column autoincrements on
                # neither backend unless it is the primary key -- because
                # there is no generated column at all.
                Column("job_id", String(64), primary_key=True),
                Column("name", String(160), primary_key=True),
                # Indexed because the TTL sweep is the read that runs without
                # anybody watching, and it asks only this question.
                Column("created", DateTime, nullable=False, index=True),
                Column("data", LargeBinary, nullable=False),
            )
            err = extensions.create_all_metadata(meta, retry=False)
            if err:
                _init_error = err
                return False
            _ready = True
            _init_error = ""
        except Exception as exc:                        # noqa: BLE001
            _init_error = _reason(exc)
            return False
        return True


def available() -> bool:
    """Whether the database is answering for this store right now."""
    return _init()


def put(job_id: str, name: str, data: bytes) -> bool:
    """Store one blob. True if the database took it, False if it would not.

    UPDATE-then-INSERT rather than a dialect-specific ON CONFLICT, which is
    `hub/jsonstore.py`'s spelling and works the same on both backends. The
    same (job, name) is written twice when a batch is re-run, and the second
    write must replace rather than raise.
    """
    if not data or len(data) > MAX_BLOB_BYTES:
        return False
    if not _init():
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with _engine.begin() as cx:
            done = cx.execute(
                update(_table)
                .where(_table.c.job_id == job_id, _table.c.name == name)
                .values(data=data, created=now)).rowcount
            if not done:
                cx.execute(insert(_table).values(
                    job_id=job_id, name=name, data=data, created=now))
        return True
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return False


def get(job_id: str, name: str) -> bytes | None:
    """One blob, or None when the database has not got it.

    None is not an error here and must not be turned into one: the caller
    already has a meaning for a missing blob -- the batch expired -- and it
    falls through to the disk before believing that.
    """
    if not _init():
        return None
    try:
        with _engine.begin() as cx:
            row = cx.execute(
                select(_table.c.data)
                .where(_table.c.job_id == job_id,
                       _table.c.name == name)).first()
        return bytes(row[0]) if row else None
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return None


def drop(job_id: str) -> int:
    """Everything for one job. Returns how many rows went."""
    if not _init():
        return 0
    try:
        with _engine.begin() as cx:
            return cx.execute(
                delete(_table).where(_table.c.job_id == job_id)).rowcount or 0
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return 0


def sweep(ttl_minutes: int) -> int:
    """Delete what is past the TTL. Returns how many rows went.

    One DELETE rather than a listing and a loop: the disk version had to stat
    every directory to find the old ones, and a WHERE clause on an indexed
    column is the same question asked once.
    """
    if not _init():
        return 0
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
              - timedelta(minutes=max(1, int(ttl_minutes))))
    try:
        with _engine.begin() as cx:
            return cx.execute(
                delete(_table).where(_table.c.created < cutoff)).rowcount or 0
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return 0


def _went_down(exc: Exception) -> None:
    """Reopen on the next call rather than staying broken for the worker.

    A dropped connection is the common case -- Render recycles Postgres -- and
    a store that latched on the first failure would send every scan of the rest
    of that worker's life to the disk.
    """
    global _ready, _init_error, _init_retry_at
    _ready = False
    _init_error = _reason(exc)
    _init_retry_at = time.time() + INIT_RETRY_SECONDS


def status() -> dict:
    """For /diagnostics. Never raises, never carries bound parameters."""
    out = {"ready": _init(), "error": _init_error, "rows": None,
           "measured": False}
    if not out["ready"]:
        return out
    try:
        from sqlalchemy import func
        with _engine.begin() as cx:
            out["rows"] = cx.execute(
                select(func.count()).select_from(_table)).scalar()
        out["measured"] = True
    except Exception as exc:                            # noqa: BLE001
        # Left unmeasured rather than reported as zero: "no scans in flight"
        # and "could not count them" are different answers.
        out["error"] = _reason(exc)
    return out


def drop_table_for_tests() -> None:
    """Between tests that share one database, as dbshim does for its own."""
    global _ready, _init_retry_at
    if not _init():
        return
    try:
        with _engine.begin() as cx:
            cx.execute(delete(_table))
    except Exception:                                   # noqa: BLE001
        pass
