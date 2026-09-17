"""Durable JSON — the disk stays the fast path, the database holds the copy.

## The problem

Roughly fifty places in this codebase persist state by writing a JSON file to
the Render disk. Every one of them is the same four functions, copy-pasted:

    def data_dir():                 "/var/data" if it exists else <repo>/data
    def _read(path, fallback):      open, json.load, except -> fallback
    def _write(path, data):         write .tmp, os.replace
    _lock = threading.Lock()

That works, and the atomic write is right. What it is not is **backed up**.
Render's managed Postgres is backed up; the 5 GB disk mounted at /var/data is
not part of those backups, and it does not survive being recreated — a plan
change, a region move, a corrupted volume, or detaching the disk to resize it
all end with an empty /var/data. The database comes back. The disk does not.

For a cache that is a non-event: `hub/knack_products.py` re-pulls from Knack
and is whole again. For anything where the JSON file is the **only copy** it is
data loss with no recovery path — every SEO client's setup answers and approved
schemas, every Fan Radio project, both OAuth token files, the house client
registry. Nobody would notice until they went looking for it.

## What this does

One shared store, so the fix lands once rather than fifty times:

* **Writes go to disk first, exactly as before** — same atomic .tmp + replace,
  same paths, same speed. Nothing about how a module reads its data changes.
* **Then the payload is mirrored into `hub_json_blobs`**, keyed by the file's
  path relative to the data root. That table lives in the database, so it is
  inside the same backup and the same point-in-time restore as everything else.
* **A read that misses on disk falls back to the database**, rewrites the file,
  and returns it. A recreated disk therefore refills itself as it is used,
  with no restore step and nothing for anyone to remember to run.

## Why the shared engine, but not db.session

`db.session` needs an application context belonging to the app that `db` was
`init_app`-ed against. Half the callers here are dispatcher-mounted modules
with their *own* Flask app, and some are scheduler threads with no app at all —
both would raise "The current Flask app is not registered with this
'SQLAlchemy' instance", which is the trap `hub/extensions.py` already documents.

So this uses Core, not the session — but through `extensions.engine_for()`
rather than its own `create_engine`. Those are two different questions and only
the first one needed avoiding: `engine_for()` hands back a plain Core engine
with no app-context requirement, shared process-wide, so the ORM trap is
sidestepped without opening a pool nobody else can see. `/api/db/structure`
counts engines per module for exactly that reason, and DDL goes through
`create_all_metadata()` so this table is created under the same advisory lock
as every other — two gunicorn workers racing to create it is otherwise the
`pg_type_typname_nsp_index` violation on every deploy.

## Failure is never the caller's problem

A mirror failure is logged and swallowed: the disk write already succeeded, so
the module is no worse off than it was before this file existed. A database
that is down or asleep would otherwise add its timeout to *every* save, so
consecutive failures open a circuit breaker and mirroring goes quiet for a
minute rather than making the whole Hub feel broken.

## Durable or cache — say which

`write_json(..., durable=False)` opts a file out of mirroring. That is the
right call for anything rebuildable, and it is deliberately a *stated* argument
rather than a default: "this one does not need backing up" should be a decision
someone wrote down, not something a reader has to infer. `status()` reports
both sets, so the answer to "what would we lose?" is a page rather than a
guess.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timezone

# The cross-worker half of update_json's lock. Absent off POSIX, where the
# in-process lock still holds -- serialising less is not a reason to refuse
# to save.
try:                                                        # noqa: SIM105
    import fcntl
except ImportError:                                         # pragma: no cover
    fcntl = None                                            # type: ignore[assignment]

# Mirroring is best-effort by design, so SQLAlchemy missing must not stop the
# disk half of this module from working — that half is what modules depend on.
try:
    from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table,
                            Text, delete, select, text as _sa_text)
    _SA_ERROR = ""
except Exception as exc:                                # noqa: BLE001
    Column = DateTime = Integer = MetaData = String = Table = Text = None
    delete = select = _sa_text = None
    _SA_ERROR = f"{type(exc).__name__}: {exc}"


_lock = threading.RLock()

# A blob is state, not a file store — 8 MB is far beyond anything here (the
# largest today is a 5000-row archive at well under 1 MB) and small enough that
# a runaway file is reported rather than quietly pushed into every backup.
MAX_MIRROR_BYTES = 8 * 1024 * 1024

# How long to stop trying after consecutive failures, so a sleeping database
# costs one timeout rather than one per save.
BREAKER_SECONDS = 60
BREAKER_AFTER = 3

_fail_count = 0
_breaker_until = 0.0
_last_error = ""
# Set when the cross-worker flock could not be taken. Reported by status()
# rather than swallowed: a deployment serialising less than it thinks it is
# looks exactly like one that is.
_lock_error = ""

_engine = None
_table = None
_ready = False
_init_error = ""
_init_done = False
_init_retry_at = 0.0
# Which database URL `_engine` was opened against. `_init_done` latches, so
# without this the first read or write in a process decides where the mirror
# goes for the life of it -- and `DATABASE_URL` set after that point is a
# setting that reads as applied and is not. `extensions.engine_for()` already
# re-resolves per call and keys its pool on the URL; this is the one latch in
# front of it.
_engine_url = ""
# Every switch this process made, oldest first, so `status()` can say it
# happened. Bounded because it is read into a page.
_switches: list[tuple[str, str]] = []
MAX_SWITCHES_REPORTED = 5


def _database_url() -> str:
    """The database URL as it is set *now*, or "" if it cannot be resolved.

    Never raises: this is reached from `_init()`, which is on the write path,
    and a mirror that cannot name its own database must still let the disk
    write succeed. Answering "" means "do not re-resolve", which leaves the
    engine exactly where it was -- the safe direction to be wrong in.
    """
    try:
        from . import extensions
        return extensions.database_url() or ""
    except Exception:                                   # noqa: BLE001
        return ""


# A failed connection is retried after this long rather than never. Render's
# Postgres can be asleep when the first write of a boot lands, and caching that
# first failure for the life of the worker would mean a Hub that came up while
# the database was waking never mirrors anything again — backups silently off
# until the next deploy, which is the failure mode this module exists to end.
INIT_RETRY_SECONDS = 120

# Files written with durable=False. Recorded so status() can list what is
# deliberately not backed up next to what is.
_declared_caches: set[str] = set()
_mirrored: dict[str, float] = {}

# Keys whose CURRENT disk contents the mirror does not have: an _upsert that
# failed, or a payload over MAX_MIRROR_BYTES. _authoritative() reads the disk
# for these, because reading the database would quietly revert them.
#
# In-process, and that is the right scope rather than a limitation: it records
# something THIS process did. A worker that restarts loses the note and also
# loses the reason for it -- sweep() re-mirrors anything whose mtime is past
# its last mirror, and on an instance with no disk of its own the local file
# is gone at that point anyway.
_unmirrored_keys: set[str] = set()


# --------------------------------------------------------------------- paths
def data_root() -> str:
    """The one place that decides where persistent files live.

    Every module had its own copy of this expression. They all agreed, which
    is luck rather than design: the moment one of them disagreed, its files
    would land somewhere the backup sweep never looks.
    """
    base = os.environ.get("HUB_DATA_DIR", "").strip()
    if not base:
        base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base


def data_dir(*parts: str) -> str:
    """A subdirectory of the data root, created if needed.

    ``data_dir("fan_radio", "projects")`` replaces the six-line ``data_dir()``
    each module carried.
    """
    path = os.path.join(data_root(), *[str(p) for p in parts if p])
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def key_for(path: str) -> str:
    """Stable database key for a file path.

    Relative to the data root, so the key survives the root itself moving —
    /var/data in production and <repo>/data locally produce the same key, which
    is what lets a production blob be restored into a development checkout.
    """
    try:
        rel = os.path.relpath(os.path.abspath(path), data_root())
    except (OSError, ValueError):
        rel = path
    if rel.startswith(".."):
        # Outside the root (an explicit override path). Key on the absolute
        # location instead of inventing a relative one that collides.
        rel = "abs:" + os.path.abspath(path)
    return rel.replace(os.sep, "/")


# ------------------------------------------------------------------ database
def _init() -> bool:
    """Open the engine and make sure the table exists. Called once, lazily.

    Lazily because import time is boot time: a database still waking up must
    not delay the workers coming online, and the first *write* is a much better
    moment to find out than the first import.
    """
    global _engine, _table, _ready, _init_error, _init_done, _init_retry_at
    global _engine_url
    with _lock:
        # Which database this process is mirroring into is a *setting*, and
        # `_init_done` latches on the first read or write -- so a
        # `DATABASE_URL` that changes after that point was being read as
        # applied while every row went on landing in the database the first
        # call happened to see. Re-resolved here for the same reason
        # `config.public_base_origin()` is read at call time: this is the one
        # variable somebody corrects after finding out where the rows went.
        #
        # In production it never changes, so `wanted` equals `_engine_url` and
        # this costs one environment read and nothing else. It is not free of
        # consequence where it does change -- rows already written are in the
        # old database and are not moved -- so the switch is reported by
        # `status()` rather than made silently.
        wanted = _database_url()
        if _init_done and wanted and wanted != _engine_url:
            _init_done = False
            _ready = False
            _init_retry_at = 0.0
            if _engine_url and (_engine_url, wanted) not in _switches:
                # Capped: a process that somehow flapped between two URLs must
                # not grow this without bound, and the pair is what a reader
                # needs rather than one row per occurrence.
                _switches.append((_engine_url, wanted))
                del _switches[:-MAX_SWITCHES_REPORTED]
        if _init_done:
            if _ready or time.time() < _init_retry_at:
                return _ready
            _init_done = False          # cooldown elapsed — try again below
        _init_done = True
        _init_retry_at = time.time() + INIT_RETRY_SECONDS
        if Table is None:
            _init_error = f"SQLAlchemy unavailable ({_SA_ERROR})"
            return False
        try:
            from . import extensions
            _engine = extensions.engine_for()
            # `wanted` *is* `_database_url()`, taken a few lines up under this
            # same lock: where it came back empty a second call answers empty
            # too, so an empty here means "we could not name the URL this
            # engine is for" rather than a reading nobody took.
            _engine_url = wanted
            meta = MetaData()
            _table = Table(
                "hub_json_blobs", meta,
                Column("key", String(500), primary_key=True),
                Column("payload", Text, nullable=False),
                Column("bytes", Integer, nullable=False, default=0),
                Column("updated_at", DateTime, nullable=False),
            )
            # Advisory-locked, and it returns the error rather than raising, so
            # a database still waking cannot stop this module from loading and
            # saying why it is not mirroring.
            #
            # retry=False because this is not boot. _init() is reached from the
            # write path, so the boot retry budget would be spent inside
            # somebody's save -- a database that is down would cost every
            # writer the full backoff in turn, which is the per-visit timeout
            # this codebase refuses to carry on a page load. The lazy retry
            # this module wants is INIT_RETRY_SECONDS above, and two backoffs
            # stacked is worse than either on its own.
            err = extensions.create_all_metadata(meta, retry=False)
            if err:
                _init_error = err
                _engine = None
                return False
            _ready = True
            return True
        except Exception as exc:                        # noqa: BLE001
            _init_error = f"{type(exc).__name__}: {exc}"
            _engine = None
            return False


def _breaker_open() -> bool:
    return time.time() < _breaker_until


def _note_failure(exc: Exception) -> None:
    global _fail_count, _breaker_until, _last_error
    with _lock:
        _fail_count += 1
        _last_error = f"{type(exc).__name__}: {exc}"
        if _fail_count >= BREAKER_AFTER:
            _breaker_until = time.time() + BREAKER_SECONDS
            _fail_count = 0


def _note_success() -> None:
    global _fail_count, _breaker_until, _last_error
    with _lock:
        _fail_count = 0
        _breaker_until = 0.0
        _last_error = ""


def available() -> bool:
    """Can the mirror be used right now?"""
    return _init() and not _breaker_open()


# ----------------------------------------------------------------- mirroring
def _upsert(key: str, text: str) -> bool:
    if not _init() or _breaker_open():
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with _engine.begin() as cx:
            # UPDATE-then-INSERT rather than a dialect-specific ON CONFLICT:
            # this runs on both Postgres and the SQLite fallback, and the two
            # gunicorn workers only ever race to write the *same* value for the
            # same key, so the loser of an insert race is a no-op either way.
            res = cx.execute(_table.update().where(_table.c.key == key).values(
                payload=text, bytes=len(text), updated_at=now))
            if not res.rowcount:
                try:
                    cx.execute(_table.insert().values(
                        key=key, payload=text, bytes=len(text), updated_at=now))
                except Exception:                       # noqa: BLE001
                    cx.execute(_table.update().where(
                        _table.c.key == key).values(
                            payload=text, bytes=len(text), updated_at=now))
        _note_success()
        with _lock:
            _mirrored[key] = time.time()
            _declared_caches.discard(key)
            _unmirrored_keys.discard(key)
        return True
    except Exception as exc:                            # noqa: BLE001
        _note_failure(exc)
        return False


def _fetch(key: str) -> str | None:
    if not _init() or _breaker_open():
        return None
    try:
        with _engine.connect() as cx:
            row = cx.execute(select(_table.c.payload).where(
                _table.c.key == key)).first()
        _note_success()
        return row[0] if row else None
    except Exception as exc:                            # noqa: BLE001
        _note_failure(exc)
        return None


def _forget(key: str) -> bool:
    if not _init() or _breaker_open():
        return False
    try:
        with _engine.begin() as cx:
            cx.execute(delete(_table).where(_table.c.key == key))
        _note_success()
        with _lock:
            _mirrored.pop(key, None)
        return True
    except Exception as exc:                            # noqa: BLE001
        _note_failure(exc)
        return False


# -------------------------------------------------------------------- public
def read_json(path: str, default=None, *, restore: bool = True):
    """Read a JSON file, falling back to the mirrored copy.

    The disk is tried first and answers almost every call. Only when the file
    is missing or unreadable — a fresh disk, or a write torn by a crash — does
    this reach for the database, and when it finds a copy it puts the file back
    before returning, so the next read is a plain disk read again.

    A missing file with no mirrored copy returns ``default``. That is the same
    answer the hand-rolled ``_read`` gave, so a caller that never had a backup
    behaves exactly as it did.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass

    if not restore:
        return default

    text = _fetch(key_for(path))
    if text is None:
        return default
    try:
        data = json.loads(text)
    except ValueError:
        return default

    # Put it back. Failing to do so is not an error — the value is in hand and
    # the caller gets it either way; the next read simply pays for the fetch.
    try:
        _atomic_write(path, text)
    except OSError:
        pass
    return data


def _atomic_write(path: str, text: str, *, fsync: bool = False) -> None:
    """Write via a temp file in the same directory, then rename.

    The rename is atomic on POSIX, so a reader never sees a half-written file
    and a crash mid-write leaves the previous version intact rather than a
    truncated one. Every module that hand-rolled this got it right; it is
    preserved here so none of them lose it by moving over.

    ``fsync`` is part of keeping that promise. One module's hand-rolled
    version flushed to the platter before the rename, and moving it here
    without this would have quietly traded that away -- which is the exact
    thing the paragraph above says this function exists to prevent. It is
    off by default because it costs a real disk round trip and most callers
    here are writing something a moment's work rebuilds; the caller that had
    it asks for it.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_json(path: str, data, *, durable: bool = True, indent=None,
               fsync: bool = False) -> bool:
    """Write a JSON file and, unless it is a cache, mirror it to the database.

    Returns True when the disk write succeeded — which is the only part the
    caller depends on. Whether the mirror landed is reported by ``status()``,
    not by raising here: a database problem must never turn a working save into
    a failed one.

    Pass ``durable=False`` for anything rebuildable from its source. It stays
    on disk exactly as now and is listed as a known, intentional gap.

    ``fsync=True`` flushes to the platter before the rename, for a caller that
    was doing that itself before it moved here.
    """
    text = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
    key = key_for(path)
    with _lock:
        _atomic_write(path, text, fsync=fsync)

    if not durable:
        with _lock:
            _declared_caches.add(key)
        return True

    size = len(text.encode("utf-8"))
    if size > MAX_MIRROR_BYTES:
        # Reported rather than silently truncated. A backup that quietly holds
        # part of a file is worse than one that says it holds none of it.
        # Both numbers in KB and stated outright: "over 0 MB" is what integer
        # division produced for any cap below a megabyte, and the size that
        # actually breached the cap is the one you need in order to act on it.
        with _lock:
            global _last_error
            _last_error = (f"{key} is {size // 1024} KB, over the "
                           f"{MAX_MIRROR_BYTES // 1024} KB mirror limit, and "
                           f"was NOT backed up")
            _unmirrored_keys.add(key)
        return True

    if not _upsert(key, text):
        # The disk is ahead of the database now. Remembered so a later
        # read-modify-write does not start from the stale mirrored copy and
        # revert this write -- the one way making the mirror authoritative
        # could have been worse than the bug it fixes.
        with _lock:
            _unmirrored_keys.add(key)
    return True


# Per-path locks, so two threads in one worker cannot interleave a
# read-modify-write. Handed out under the module lock and never held across
# it, so there is no ordering cycle with write_json's own use of _lock.
_FILE_LOCKS: dict[str, threading.Lock] = {}


def _file_lock(path: str) -> threading.Lock:
    key = os.path.abspath(path)
    with _lock:
        got = _FILE_LOCKS.get(key)
        if got is None:
            got = _FILE_LOCKS[key] = threading.Lock()
        return got


# ---------------------------------------------------- cross-instance locking
# The flock below serialises workers that share a filesystem. That is every
# worker for as long as this service has one disk mounted at one path -- and
# it is NOT the two halves of a zero-downtime deploy, which are separate
# instances with separate filesystems. Each takes its own flock on its own
# sidecar file, succeeds, and serialises nothing.
#
# Measured, two processes each appending 30 rows through update_json():
#
#     one data root  (one filesystem):   60 of 60 survived
#     two data roots (two filesystems):  34 of 60 survived
#
# Both processes reported success and status() reported no lock error, because
# each flock really was taken -- a lock that succeeds and means nothing, which
# is worse than one that fails loudly. Every instance talks to the same
# database, so a Postgres advisory lock is the only lock available here that
# spans them, and it is tried first wherever the mirror is on Postgres.
LOCK_WAIT_SECONDS = 10.0

# Never let lock connections starve the pool the writes themselves need.
# engine_options() gives 5 + 10 overflow per worker and each holder needs a
# second connection for its own _upsert, so capping holders at 5 leaves a
# third of the pool for everything else. Blocking on this is deliberate:
# falling back to a weaker lock under contention would drop serialisation at
# exactly the moment it is load-bearing.
LOCK_MAX_HELD = 5
_lock_slots = threading.BoundedSemaphore(LOCK_MAX_HELD)

# Which mechanism the last exclusive() actually got, and how many times the
# advisory lock timed out and fell back. Reported by status() for the reason
# _lock_error exists: a deployment serialising less than it thinks it is looks
# exactly like one that is.
# "" until exclusive() has actually taken a lock in this process. A process
# that has not written yet has no lock state to report, and defaulting it to
# a real backend name would have /diagnostics warning about a degraded lock at
# every boot, before anything had locked anything -- the check that cries wolf
# and gets skipped past.
_lock_backend = ""
_lock_timeouts = 0


def _advisory_key(path: str) -> int:
    """A stable signed 64-bit lock id for one file.

    Derived from ``key_for()`` -- the path relative to the data root -- so two
    instances whose roots differ still agree on the id for the same logical
    file. Keyed on the absolute path instead, they would take two different
    locks and serialise nothing, which is the bug this is here to fix wearing
    a different hat.
    """
    import hashlib
    digest = hashlib.blake2b(key_for(path).encode("utf-8"),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


class _PgLock:
    """A held Postgres advisory lock. ``close()`` is the unlock."""

    backend = "postgres"

    def __init__(self, conn, key: int):
        self._conn = conn
        self._key = key

    def close(self) -> None:
        global _lock_error
        try:
            self._conn.execute(_sa_text("SELECT pg_advisory_unlock(:k)"),
                               {"k": self._key})
        except Exception as exc:                        # noqa: BLE001
            # An advisory lock is SESSION-scoped and conn.close() only returns
            # the connection to the pool -- the session survives, so a lock
            # left held would be inherited by whoever is handed that
            # connection next and would wedge them. Invalidating drops the
            # connection instead, and a dropped connection is released by
            # Postgres, which is the one thing that has to happen here.
            _lock_error = (f"advisory unlock failed: "
                           f"{type(exc).__name__}: {exc}")
            try:
                self._conn.invalidate()
            except Exception:                           # noqa: BLE001
                pass
        finally:
            try:
                self._conn.close()
            except Exception:                           # noqa: BLE001
                pass
            _lock_slots.release()


def _take_pg_lock(path: str):
    """Hold this path against every other instance, or answer None.

    Answering None is never a refusal to save: the caller falls back to the
    flock and the in-process lock still holds, which is the rule every entry
    point in this module works to. What it must not do is fail *quietly*, so
    each reason is recorded and status() prints it.
    """
    global _lock_error, _lock_timeouts
    if _sa_text is None or not _init():
        return None
    try:
        if not _engine.dialect.name.startswith("postgres"):
            return None            # SQLite is one process; the flock is enough
    except Exception:                                   # noqa: BLE001
        return None

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    if not _lock_slots.acquire(timeout=LOCK_WAIT_SECONDS):
        _lock_error = (f"{path}: no advisory-lock slot within "
                       f"{LOCK_WAIT_SECONDS}s")
        return None

    key = _advisory_key(path)
    conn = None
    try:
        # AUTOCOMMIT so the lock is not sitting inside an open transaction
        # holding a snapshot for as long as the caller's work takes.
        conn = _engine.connect().execution_options(
            isolation_level="AUTOCOMMIT")
        wait = 0.005
        while True:
            got = conn.execute(_sa_text("SELECT pg_try_advisory_lock(:k)"),
                               {"k": key}).scalar()
            if got:
                return _PgLock(conn, key)
            if time.monotonic() >= deadline:
                # Proceeding unserialised is the lesser evil -- refusing the
                # write is the one thing this module never does -- but it is
                # the return of the measured defect above, so it is counted
                # and named rather than being absorbed into a fallback.
                _lock_timeouts += 1
                _lock_error = (
                    f"{path}: another instance held the advisory lock for "
                    f"{LOCK_WAIT_SECONDS}s; fell back to the flock, which "
                    f"does not span instances")
                break
            time.sleep(wait)
            wait = min(wait * 2, 0.1)
    except Exception as exc:                            # noqa: BLE001
        _lock_error = (f"{path}: advisory lock failed: "
                       f"{type(exc).__name__}: {exc}")
    if conn is not None:
        try:
            conn.close()
        except Exception:                               # noqa: BLE001
            pass
    _lock_slots.release()
    return None


def _take_flock(path: str):
    """Open the sidecar lock file and hold it exclusively, or answer None.

    Split out so the close is visible at the one place that owns it. Failing
    here never costs the write: a filesystem that will not take an flock is a
    reason to serialise less, not a reason to refuse to save, and the caller
    still holds the in-process lock. What it must not do is fail *quietly* --
    a deployment serialising less than it thinks it is looks exactly like one
    that is -- so the reason is recorded and status() reports it.
    """
    global _lock_error
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        handle = open(path + ".lock", "a+")                 # noqa: SIM115
    except OSError as exc:
        _lock_error = f"{path}: {type(exc).__name__}: {exc}"
        return None
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        _lock_error = f"{path}: {type(exc).__name__}: {exc}"
        handle.close()
        return None
    return handle


@contextlib.contextmanager
def exclusive(path: str):
    """Hold one file against every other thread, worker AND instance.

    Three locks because there are three ways to lose a write. A
    ``threading.Lock`` serialises the threads inside one gunicorn worker; an
    ``flock`` on a sidecar file serialises the workers, of which this
    deployment runs two; and a Postgres advisory lock serialises the
    *instances*, which is the only one of the three that still holds once they
    stop sharing a filesystem. Any one alone leaves part of the problem.

    **Failing to take the flock never costs the write.** A filesystem that
    does not support it, or a directory we cannot create a lock file in, is a
    reason to serialise less rather than a reason to refuse to save -- so the
    in-process lock still holds and the write goes ahead. That is the rule
    every entry point in this module already works to.
    """
    global _lock_backend
    thread_lock = _file_lock(path)
    thread_lock.acquire()
    handle = None
    try:
        # Postgres first: it is the only one of the three that spans
        # instances. The flock stands behind it for SQLite, for a database
        # that is down, and for the timeout case -- weaker, and better than
        # the thread lock alone.
        #
        # Both are wrapped because the rule is that ACQUIRING A LOCK NEVER
        # COSTS THE WRITE, and each of them being individually careful is not
        # the same promise: measured, a fault raised out of the lock
        # machinery propagated straight through here and the caller lost a
        # save it would have made without any of this. The thread lock is
        # already held by this point, so the degraded path is exactly the
        # behaviour this module had before either of the other two existed.
        try:
            handle = _take_pg_lock(path)
            if handle is not None:
                _lock_backend = "postgres"
            else:
                handle = _take_flock(path)
                _lock_backend = "flock" if handle is not None else "thread-only"
        except Exception as exc:                        # noqa: BLE001
            global _lock_error
            _lock_error = (f"{path}: locking raised: "
                           f"{type(exc).__name__}: {exc}")
            handle = None
            _lock_backend = "thread-only"
        yield
    finally:
        # Closing the descriptor is what releases the flock, so this is the
        # unlock: an explicit LOCK_UN beside it would be a second call doing
        # what the close already does. The thread lock is released whatever
        # happened above -- a lock still held after an exception is a store
        # that hangs, which is worse than the exception.
        if handle is not None:
            handle.close()
        thread_lock.release()


# The old private spelling, kept because this module's own callers use it and
# renaming a name is not worth a diff across them. It is public now because
# hub/leads.py needs the same two locks over a file this module does not own:
# a second implementation of "hold this against the other worker" is the
# drift this codebase keeps having to undo, and the half that would have been
# missing is the flock.
_exclusive = exclusive


def _authoritative(path: str, default=None, *, durable: bool = True):
    """What every instance last wrote -- not what *this* instance last wrote.

    ``read_json()`` answers from the local disk first. That is right for an
    ordinary read and wrong for the read half of a read-modify-write once
    instances stop sharing a filesystem: the lock serialises them perfectly,
    each one then reads its OWN copy, mutates it, and writes the whole
    collection back. The locking is correct and the update is still lost --
    which is why the lock on its own was not the fix. Measured, three
    alternating turns between two instances kept 4 of 6 rows with a disk-first
    read and all 6 with this one.

    So the mirror wins, because it is the only copy the instances share. The
    disk answers in exactly the three cases where the mirror is not the better
    source:

    * ``durable=False`` -- there is no mirrored copy by definition;
    * the database did not answer, which is also what happens while it is
      down, so the degraded path is the old behaviour rather than a failure;
    * the key is in ``_unmirrored_keys`` -- this process wrote something the
      mirror did not take (a failed upsert, or a payload over the size cap),
      so the mirrored copy is behind the disk and reading it would revert a
      real write.

    The residual window is small and worth stating: a write whose mirror
    failed in a *previous* process is not known about here, so a
    read-modify-write in the window between that restart and the next
    successful mirror or hourly ``sweep()`` can still start from the older
    copy. That is strictly narrower than the defect this replaces, which lost
    every cross-instance update every time.

    The disk stays the fast path for every ordinary read. This is the one
    place that has to be right about which copy is current.
    """
    if not durable:
        return read_json(path, default=default)
    key = key_for(path)
    with _lock:
        ours_is_ahead = key in _unmirrored_keys
    if ours_is_ahead:
        return read_json(path, default=default)
    raw = _fetch(key)
    if raw is None:
        return read_json(path, default=default)
    try:
        return json.loads(raw)
    except ValueError:
        # A payload we cannot parse is not a reason to lose the file: fall
        # back to the disk, which is what the caller had before this existed.
        return read_json(path, default=default)


def update_json(path: str, mutate, *, default=None, durable: bool = True,
                indent=None):
    """Read, change and write one JSON file as a single indivisible step.

    This is the missing half of ``read_json`` and ``write_json``. A store that
    keeps many records in **one** file changes a record by reading the whole
    collection, editing one entry and writing the whole collection back -- and
    with the read outside any lock, two writers each start from the same
    snapshot and the second one to finish silently drops the first one's
    change. Both callers are told they succeeded.

    It is not a race that needs contention over a single record to appear: two
    people editing two *different* projects lose one of the two edits, because
    what is written back is the whole list either way. Reproduced against
    ``modules/radio_promo`` before this existed, with two threads and two
    unrelated projects.

    ``mutate`` is handed the data as read and returns what to write.
    **Returning ``None`` writes nothing** and is the way to say "nothing
    changed" -- the rule ``google_index.apply_domain_matches`` already works
    to, so a sweep that finds nothing to do does not queue a write on both
    workers. The value returned is what is now on disk.

    A ``mutate`` that raises is the caller's own bug and is left to surface;
    the locks are released either way. What must never raise is the locking,
    and it does not.
    """
    with _exclusive(path):
        data = _authoritative(path, default=default, durable=durable)
        changed = mutate(data)
        if changed is None:
            return data
        write_json(path, changed, durable=durable, indent=indent)
        return changed


def delete_json(path: str) -> bool:
    """Remove a file and its mirrored copy.

    Both halves, always. Deleting only the file would leave the blob behind to
    be restored by the next ``read_json`` — the deletion would appear to work
    and then undo itself, which is a far more confusing bug than never having
    had a backup.
    """
    ok = False
    try:
        os.remove(path)
        ok = True
    except OSError:
        pass
    _forget(key_for(path))
    return ok


def mirror_file(path: str) -> bool:
    """Mirror a JSON file that some other code path wrote.

    The sweep uses this to cover writers that have not moved onto ``write_json``
    yet, so a module still doing its own ``json.dump`` is backed up from the
    next sweep rather than from the day someone gets round to editing it.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        json.loads(text)                    # never mirror an unparseable file
    except (OSError, ValueError):
        return False
    if len(text.encode("utf-8")) > MAX_MIRROR_BYTES:
        return False
    return _upsert(key_for(path), text)


# ------------------------------------------------------- disaster / recovery
GENERATION_FILE = ".hub-jsonstore-generation"
_GENERATION_KEY = GENERATION_FILE


def _generation_on_disk() -> str:
    try:
        with open(os.path.join(data_root(), GENERATION_FILE), encoding="utf-8") as fh:
            return json.load(fh).get("id", "")
    except (OSError, ValueError):
        return ""


def stamp_generation() -> str:
    """Record that this disk has been initialised, on disk and in the database.

    The pair is how a *recreated* disk is told apart from a merely empty one.
    A blob in the database with no matching marker on disk means the database
    has seen a disk before and this is not that disk — which is precisely the
    disaster this module exists for, and the only condition under which a bulk
    restore is the right thing to do.
    """
    import secrets
    gen = _generation_on_disk()
    if gen:
        return gen
    gen = secrets.token_hex(8)
    payload = {"id": gen, "at": datetime.now(timezone.utc).isoformat(
        timespec="seconds")}
    try:
        _atomic_write(os.path.join(data_root(), GENERATION_FILE),
                      json.dumps(payload))
    except OSError:
        return ""
    _upsert(_GENERATION_KEY, json.dumps(payload))
    return gen


def disk_is_fresh() -> bool:
    """True when the database holds blobs that this disk has never seen."""
    if _generation_on_disk():
        return False
    return _fetch(_GENERATION_KEY) is not None


def restore_all(limit: int = 20000) -> dict:
    """Write every mirrored blob back to disk.

    Only for a genuinely fresh disk. Restoring over a live one would resurrect
    anything deleted outside ``delete_json`` since the mirror was taken, so the
    caller has to have established that first — ``maybe_restore()`` does.
    """
    if not _init():
        return {"restored": 0, "skipped": 0, "error": _init_error or "unavailable"}
    restored = failed = skipped = 0
    try:
        with _engine.connect() as cx:
            rows = cx.execute(select(_table.c.key, _table.c.payload)
                              .limit(limit)).all()
    except Exception as exc:                            # noqa: BLE001
        _note_failure(exc)
        return {"restored": 0, "skipped": 0, "error": f"{type(exc).__name__}"}

    root = data_root()
    for key, payload in rows:
        if key.startswith("abs:"):
            path = key[4:]
        else:
            path = os.path.join(root, *key.split("/"))
        if os.path.exists(path):
            skipped += 1
            continue
        try:
            _atomic_write(path, payload)
            restored += 1
        except OSError:
            failed += 1
    return {"restored": restored, "skipped": skipped, "failed": failed,
            "total": len(rows)}


def maybe_restore() -> dict:
    """Restore the disk from the database if — and only if — the disk is new.

    Called once at boot. On every ordinary boot this is two cheap queries and
    a no-op. On the boot after a disk is recreated it is the whole recovery.
    """
    try:
        if not _init():
            return {"ran": False, "reason": _init_error or "no database"}
        if not disk_is_fresh():
            stamp_generation()
            return {"ran": False, "reason": "disk already initialised"}
        out = restore_all()
        stamp_generation()
        out["ran"] = True
        try:
            from . import audit
            audit.log("jsonstore", "restore", **{
                k: v for k, v in out.items() if k != "ran"})
        except Exception:                               # noqa: BLE001
            pass
        return out
    except Exception as exc:                            # noqa: BLE001
        return {"ran": False, "reason": f"{type(exc).__name__}: {exc}"}


# ----------------------------------------------------------------- the sweep
# Directories under the data root holding files that are rebuildable from their
# source. Sweeping them would put megabytes of Knack and provider responses
# into the backup to no purpose, and would make "what would we lose?" unreadable.
# "adbuilder-out" is OUTPUT_DIR from render.yaml — the Display Ad Builder's
# renders, project state and brand cache. It is a separate Node service with
# its own storage model and its own render.yaml, and its output is largely
# rasterised ads rather than Hub state, so quietly pulling it into the Hub's
# backup would put megabytes of another service's internals into every restore
# without that service knowing. If its project state needs backing up, that is
# the renderer's decision to make, in its own terms.
SWEEP_SKIP_DIRS = {"assets", "cache", "caches", "tmp", "knack-cache",
                   "ad_builder", "adbuilder-out", "sessions"}
SWEEP_SKIP_FILES = {"knack_products.json", "knack_products_cache.json"}


def sweep(limit: int = 4000) -> dict:
    """Mirror every durable JSON file under the data root.

    Belt and braces for the migration: modules that still write their own JSON
    are covered from the next sweep, and a file whose mirror failed while the
    breaker was open is picked up on the next pass rather than staying stale
    until someone saves it again.
    """
    if not _init():
        return {"mirrored": 0, "error": _init_error or "unavailable"}
    root = data_root()
    seen = mirrored = skipped = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SWEEP_SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".json") or name.endswith(".tmp"):
                continue
            if name in SWEEP_SKIP_FILES:
                skipped += 1
                continue
            path = os.path.join(dirpath, name)
            key = key_for(path)
            if key in _declared_caches:
                skipped += 1
                continue
            seen += 1
            if seen > limit:
                break
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            # Already mirrored since the file last changed.
            if _mirrored.get(key, 0) >= mtime:
                continue
            if mirror_file(path):
                mirrored += 1
        if seen > limit:
            break
    return {"scanned": seen, "mirrored": mirrored, "skipped": skipped,
            "capped": seen > limit}


# ---------------------------------------------------------------- reporting
def mirror_is_on_the_same_disk() -> bool:
    """Is the "backup" sitting on the very disk it is supposed to survive?

    With DATABASE_URL unset, ``extensions.database_url()`` falls back to a
    SQLite file on /var/data — sensible for local development, and worthless
    as a backup, because the mirror is then destroyed by exactly the event it
    exists to protect against. Everything still reports healthy: writes
    succeed, reads succeed, the blob count climbs. That is the most dangerous
    shape a backup can have, so it is detected and said out loud rather than
    left to be discovered after a disk is recreated.
    """
    if not _engine:
        return False
    try:
        if not _engine.dialect.name.startswith("sqlite"):
            return False                # Postgres — a separate service
        db_path = (_engine.url.database or "").strip()
        if not db_path or db_path == ":memory:":
            return False
        root = os.path.abspath(data_root())
        return os.path.abspath(db_path).startswith(root + os.sep)
    except Exception:                                   # noqa: BLE001
        return False


def status() -> dict:
    """What is backed up, what is not, and why — for /diagnostics.

    Deliberately reports "unknown" rather than zero when the database cannot be
    reached. A backup panel showing a confident 0 blobs during an outage would
    read as "nothing is backed up", which is the wrong answer stated well.
    """
    ready = _init()
    out = {
        "ready": ready,
        "error": _init_error or _last_error,
        "lock_error": _lock_error,
        # Which of the three locks exclusive() last actually got. "flock" on
        # Postgres means the advisory lock could not be taken, so writes are
        # serialised within an instance and not between them -- the state that
        # is invisible from every screen unless it is printed here.
        "lock_backend": _lock_backend,
        "lock_timeouts": _lock_timeouts,
        "breaker_open": _breaker_open(),
        # Named rather than left silent: rows written before a switch are in
        # the previous database and are not moved, so a mirror that looks
        # complete may be missing everything written before it. No credential
        # reaches this -- a URL carries a password, and `status()` is rendered
        # into /diagnostics and pasted into chats, which is the rule
        # `services/provider_check.py` states.
        "database_switched": len(_switches),
        "root": data_root(),
        "declared_caches": sorted(_declared_caches),
        # Files this process wrote that the mirror did not take -- a failed
        # upsert, or a payload over the size cap. This is the "what would we
        # lose?" answer that declared_caches deliberately is not: a cache is
        # rebuildable and these are not, so they are listed apart rather than
        # summed into one number that reads as neither.
        "unmirrored": sorted(_unmirrored_keys),
        "same_disk": ready and mirror_is_on_the_same_disk(),
        "blobs": None,
        "bytes": None,
        "newest": None,
    }
    if not ready or _breaker_open():
        return out
    try:
        from sqlalchemy import func
        with _engine.connect() as cx:
            row = cx.execute(select(
                func.count(), func.coalesce(func.sum(_table.c.bytes), 0),
                func.max(_table.c.updated_at))).first()
        out["blobs"] = int(row[0] or 0)
        out["bytes"] = int(row[1] or 0)
        out["newest"] = row[2].isoformat(timespec="seconds") if row[2] else None
        _note_success()
    except Exception as exc:                            # noqa: BLE001
        _note_failure(exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# ------------------------------------------------- who has not moved across
#
# Two scanners were asking "who still writes JSON without the mirror?" and
# answering it differently on the same page. /api/integrity exempted build
# scripts and repo tooling; /api/db/structure did not, so the Diagnostics
# panel reported `1 file writes JSON outside hub/jsonstore.py — ad_builder`
# directly above an audit that had found nothing. The file behind it was
# modules/ad_builder/scripts/fix_safezones.py, a one-off script that rewrites
# layout JSON committed to the repo: it never touches the Render disk, and
# ad_builder is the Node renderer, which keeps no Python state there at all.
# So the row pointed at a module where there was nothing to move, and the two
# answers were both on screen at once.
#
# Which of the two was right matters less than there being one of them. The
# rule lives here because this is the module the question is about, and both
# callers read it rather than keeping a copy — the same reason hub/storage.py
# and hub/images.py exist. It is stdlib-only and reads source without running
# it, so a module that fails to import is still scanned.

SCAN_SKIP_DIRS = frozenset({
    "_attic", "__pycache__", ".git", "node_modules", ".venv", "venv", "env",
    "site-packages", ".tox", "build", "dist",
})

# Each exemption carries the reason it is exempt, and the reason is checked
# against the file rather than assumed: an exemption that outlives the code it
# covered is how a real finding later gets swallowed by a list nobody re-reads.
# This list already had that shape — it named hub/errors.py and hub/audit.py,
# neither of which has used json.dump( since they moved to append-only JSONL,
# so both were being excluded from a pattern they no longer match.
UNMIRRORED_EXEMPT: dict[str, str] = {
    "hub/jsonstore.py": "the mirror itself",
    "hub/integrity.py": "reports this check; it writes no JSON",
    "hub/client_context.py": "reports this check; it writes no JSON",
    "hub/errors.py": "append-only JSONL log, not a whole-file store",
    "hub/audit.py": "append-only JSONL log, not a whole-file store",
    # Found once the check stopped excusing a file for containing the word
    # "jsonstore". Each of these writes JSON to a path, and each is exempt
    # for a reason that names what losing it costs -- not because the loss
    # is nothing, but because none of them is a store of record.
    "modules/io_builder/app.py":
        "a /tmp fallback for the order number, reached only when the Postgres "
        "sequence is unreachable; the sequence is the store, and a per-instance "
        "file is the correct shape for a degraded mode",
    "modules/suite_panel/app.py":
        "idempotency markers with a TTL, not a store: losing one lets a "
        "retried request run twice inside the window, which is the cost, and "
        "mirroring a value that expires would keep it past its expiry",
    "modules/commercial_builder/services/elevenlabs_audio_service.py":
        "an audio cache sidecar keyed by content digest; a lost entry is "
        "re-synthesised, which costs credits rather than data",
    "hub/leads.py":
        "the leads are in hub_leads; the file write left here is the fallback "
        "for a database that will not answer, and /diagnostics' lead-store row "
        "says when it is being used rather than letting it degrade quietly -- "
        "which is the silence this finding is about",
}


def _unmirrored_exempt_reason(rel: str) -> str | None:
    """Why this file is not counted, or None if it is."""
    if rel in UNMIRRORED_EXEMPT:
        return UNMIRRORED_EXEMPT[rel]
    # A build script rewrites files committed to the repo, not state on the
    # data disk, and the repo is in git — which is a better backup than the
    # mirror. Same for repo tooling and the test files.
    if "/scripts/" in rel or rel.startswith("scripts/"):
        return "build script; rewrites files kept in the repo"
    if rel.startswith("tools/"):
        return "repo tooling; holds no state"
    name = rel.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return "test; writes to a temporary directory"
    if "/templates/" in rel:
        return "template, not a module"
    return None


def _module_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "modules" and len(parts) > 1:
        return parts[1]
    if parts[0] == "hub":
        return rel
    return parts[0]


def unmirrored_json_writers(root=None) -> list[dict]:
    """Every source file that writes JSON to the disk with no database copy.

    ``json.dump(`` with the bracket is the signal. The substring without it
    also matches ``json.dumps``, which serialises to a string and touches no
    disk at all — it is in every module that returns JSON from a route, so
    counting it made the number mostly noise.

    A file that mentions ``jsonstore`` is already going through the mirror and
    is not part of this risk; counting the fix as more of the problem is worse
    than not counting. That test used to exempt both scanners by accident,
    because each one's own explanatory text contains the word — they are named
    in ``UNMIRRORED_EXEMPT`` now, so the exemption survives a reworded string.
    """
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    out: list[dict] = []
    for p in sorted(base.rglob("*.py")):
        if any(x in p.parts for x in SCAN_SKIP_DIRS):
            continue
        rel = p.relative_to(base).as_posix()
        if _unmirrored_exempt_reason(rel):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        how = _writes_json_to_disk(src)
        if not how or _calls_the_mirror(src):
            continue
        out.append({"file": rel, "module": _module_of(rel), "how": how})
    return out


# The two calls that serialise JSON, and the ways a string reaches a file.
# ``json.dumps`` matters as much as ``json.dump``: an append-only store writes
# ``fh.write(json.dumps(row) + "\n")``, which is a whole store on the disk and
# matched neither the old pattern nor, once its source happened to contain the
# word "jsonstore", the old exemption. Both held for hub/leads.py, so the
# check reported a clean bill about every lead the business had captured.
# `os.replace` is not here: it renames, so its arguments are paths and the
# serialised string never passes through it. The write it makes durable is
# the `write` above, which is what this matches.
_TO_DISK = ("write", "write_text", "writelines", "writestr")

#: Calling one of these is what "goes through the mirror" means. The word
#: alone is not a call site -- a file that imports ``jsonstore`` for
#: ``data_root()`` or ``exclusive()`` and then writes the file itself is
#: exactly the case this check exists to find, and the substring test excused
#: eleven of them, the lead store and the Google OAuth tokens among them.
MIRROR_WRITERS = frozenset({"write_json", "update_json", "delete_json",
                            "file_asset"})


def _writes_json_to_disk(src: str) -> str:
    """How this source puts JSON on the disk, or "" if it does not.

    Read rather than run, and by AST rather than by substring: the string
    "json.dump(" appears in this file's own prose, and a check that a comment
    can trip is a check whose findings nobody trusts.
    """
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    # Names bound to a `json.dumps(...)` result, so the two-step spelling --
    # `text = json.dumps(row)` then `fh.write(text)` -- is seen too. Nothing
    # in the repo writes that way today except the mirror itself; it is here
    # because the next store written that way would otherwise be invisible,
    # which is the whole failure this check was just found to have.
    serialised: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Attribute) \
                and node.value.func.attr == "dumps" \
                and isinstance(node.value.func.value, ast.Name) \
                and node.value.func.value.id == "json":
            serialised.update(t.id for t in node.targets
                              if isinstance(t, ast.Name))

    dumps_written = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        if fn.attr == "dump" and isinstance(fn.value, ast.Name) and \
                fn.value.id == "json":
            return "json.dump()"
        # The serialised string has to reach the write, not merely share a
        # file with one. `json.dumps` also builds request bodies and database
        # column values all over this repo, and a check that counts those
        # reports thirty-odd findings nobody can act on -- which is the same
        # as reporting none, one screen further along.
        if fn.attr in _TO_DISK and any(
                (isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
                 and k.func.attr == "dumps"
                 and isinstance(k.func.value, ast.Name)
                 and k.func.value.id == "json")
                or (isinstance(k, ast.Name) and k.id in serialised)
                for arg in node.args for k in ast.walk(arg)):
            dumps_written = True
    return "json.dumps() written to a file" if dumps_written else ""


def _calls_the_mirror(src: str) -> bool:
    """Whether the source actually calls a jsonstore writer."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in MIRROR_WRITERS:
            return True
    return False


def stale_exemptions(root=None) -> list[str]:
    """Named exemptions pointing at a file that is no longer there.

    An exemption list is only safe while every entry still names something. A
    deleted file leaves an entry that silently covers whatever is written at
    that path next — which is the one case here that is unambiguous, so it is
    the only one reported. The two append-only logs are deliberately not
    checked against the pattern: they have never matched ``json.dump(`` and
    are listed to record the decision, not to suppress a finding.
    """
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    stale = []
    for rel in UNMIRRORED_EXEMPT:
        p = base / rel
        if not p.is_file():
            stale.append(f"{rel} (no such file)")
    return stale


# ----------------------------------------- the stores that are not JSON
#
# `unmirrored_json_writers()` answers one question -- what is written to the
# Render disk with no copy in the database -- and it is the only check that
# asks it. **A SQLite file is not JSON**, so a store that is a whole database
# was outside the one check that exists to find stores on the disk, whatever
# it held.
#
# Two were, and neither was found by a check. `modules/google_finder/app.py`
# kept Google OAuth refresh tokens in `google_tokens.db`, and
# `modules/io_builder/submission_attempts.py` keeps the attempt and receipt
# tables that stop a retried Suite delivery creating a second opportunity
# against a real insertion order. Both were found by somebody grepping, which
# is the same shape as the lead store: the panel said nothing, and nothing is
# what it could say.
#
# Deliberately `sqlite3.connect()` and nothing else. A module that opens a
# database through SQLAlchemy is answered by `hub/client_context.py`'s
# `_engine_use()`, which already reports who builds their own engine instead of
# taking the shared one; adding `create_engine` here would report the same
# modules twice and would flag `hub/extensions.py` and `modules/reports/store.py`
# for their no-DATABASE_URL fallbacks, which are the shared engine, not a
# second store. Two checks disagreeing on one page is the defect the comment
# above `SCAN_SKIP_DIRS` records. `sqlite3.connect` is the spelling that
# reaches a file on the disk with no engine, no pool and no mirror.

DISK_SQLITE_EXEMPT: dict[str, str] = {
    "modules/smartforecast/store.py":
        "the read side of the one-time import off the legacy file. The store "
        "moved to the shared engine in #648; this connect exists to empty that "
        "file, so counting it would report the migration as the thing to "
        "migrate",
    # Added on this check's first real encounter with a module moving, which
    # is the case the list has to get right or it starts punishing the fix.
    # `_copy_legacy_into()` is the same shape as smartforecast's above: the
    # OAuth tokens are on the shared engine now, and what is left at this call
    # site is the code that empties the file they were in.
    "modules/google_finder/app.py":
        "the read side of the one-time import off google_tokens.db. The tokens "
        "moved to the shared engine in #674; this connect is _copy_legacy_into(), "
        "which exists to empty that file, so counting it would report the "
        "migration as the thing to migrate",
}


DISK_BINARY_EXEMPT: dict[str, str] = {
    # ---- the shared uploader itself ----
    "hub/storage.py":
        "the disk fallback reached only when Cloudinary is unconfigured. It is "
        "the one write here that hands back NO url, deliberately (#693), and "
        "local_assets() counts what is sitting in it on /diagnostics -- so it "
        "is already the reported thing rather than a hidden one",

    # ---- a scratch file that never outlives the call ----
    # Not "small" or "temporary" as an adjective: each of these writes inside a
    # tempfile context manager that deletes the directory on exit, so there is
    # nothing on the data disk after the function returns.
    "hub/image_compress.py":
        "both writes are inside tempfile.TemporaryDirectory(), handing bytes to "
        "pngquant and back; nothing survives the call",
    "modules/pdf_optimizer/app.py":
        "writes into tempfile.mkdtemp(prefix='smart1_pdf_') so Ghostscript has a "
        "path to read; nothing survives the request",
    "modules/commercial_builder/services/finished_video.py":
        "streams the finished video into tempfile.TemporaryDirectory("
        "prefix='cb-inspect-') to inspect it; nothing survives the inspection",

    # ---- a cache, and rebuildable from something else that is kept ----
    "modules/bg_remover/app.py":
        "_cache_put(), keyed by the source digest. Losing it costs one repeat "
        "cutout of an image the caller still has; the cutout itself goes to "
        "Cloudinary",
    "modules/gpt_ads/app.py":
        "_cache_image(), and _image_bytes() falls back to re-fetching from the "
        "stored URL when it is missing -- the pack's image is in Cloudinary and "
        "this is a local copy of it",

    # Rewritten when the bytes moved. The entry it replaces was a considered
    # decision -- it got the Cloudinary half right, and that half is kept
    # below -- but it named the cross-instance cost and accepted it:
    #
    #     a scan on one instance and a save on another reads as that same
    #     expiry message, which is why the two workers this service runs
    #     share a disk rather than a dict
    #
    # That is the defect, stated plainly and left in place. The bytes are in
    # the database now, so the write this file still makes is a FALLBACK, and
    # the exemption has to say the new thing rather than the old one.
    "modules/page_image_optimizer/store.py":
        "put_bytes() writes the optimized .webp and its .preview to the "
        "page_image_bytes table first; this open() is reached only when the "
        "database refused, and is kept for Fan Radio's reason -- a render that "
        "cost a download and real CPU is not thrown away because a backend "
        "would not answer. get_bytes() reads the disk second for one TTL after "
        "a deploy, because a batch the previous release scanned is there and "
        "in no table. Both are swept on the 45-minute TTL "
        "(PAGE_IMAGES_TTL_MINUTES). Cloudinary still holds the durable copy of "
        "an image somebody KEEPS -- save hands it to archive.upload() -- and "
        "uploading on the way in would put every scanned image into the "
        "account including the ones nobody keeps, which is why these go to the "
        "database instead",

    # ---- Cloudinary first, disk only when the upload could not happen ----
    # These are NOT the hub/storage.py defect. Each one tries Cloudinary, keeps
    # the bytes locally only when that fails or is unconfigured, and returns a
    # URL its own module serves -- so the fallback is reachable rather than
    # decorative. What they still are is per-instance, which is why each names
    # the route that would have to follow the bytes if this service ever runs
    # more than one instance.
    "hub/proposals.py":
        "Cloudinary first; the disk copy is served by /api/client/proposals/"
        "file/<name> and is reached only with no Cloudinary URL",
    "modules/hvac/app.py":
        "Cloudinary first; the disk copy is served from /static/reports/ and is "
        "reached only when the upload raised",
    "modules/restaurant/app.py":
        "Cloudinary first; the disk copy is served from /static/reports/ and is "
        "reached only when the upload raised",
    "modules/landing_ads/app.py":
        "Cloudinary first; the disk copy is served by this module's own "
        "/file/<name> route and is reached only with no Cloudinary URL",
    "modules/radio_promo/app.py":
        "Cloudinary first; the disk copy is served by this module's own "
        "/file/<name> route and is reached only when the upload raised",
    "modules/fan_radio/store.py":
        "_write_local(), reached only when the Cloudinary upload raised, because "
        "a render that cost money is never thrown away over a failed upload",
    "modules/image_creator/projects.py":
        "the preview. Cloudinary first; the disk copy is served by this module's "
        "own /api/projects/<id>/preview route",
    "modules/msa/app.py":
        "_store_pdf(), which its own docstring calls the convenience copy -- "
        "Cloudinary holds the durable one, and /pdf/<token> says so when the "
        "local file is gone",

    # ---- a branch with no caller ----
    "modules/commercial_builder/services/elevenlabs_service.py":
        "the out_path= branch of generate_voiceover(), and no caller passes "
        "out_path: both take audio_bytes and send it to Cloudinary. Listed "
        "rather than deleted here because deleting a parameter is a change to "
        "that module, not to this check",
}


def disk_binary_writers(root=None) -> list[dict]:
    """Every source file that writes BYTES to a path on the disk.

    The third question, after ``unmirrored_json_writers()`` and
    ``disk_sqlite_stores()``. Those two ask what JSON and what databases are on
    a disk outside the backup; between them they still could not see a module
    that writes a .webp, a .pdf or an .mp3, which is most of what this suite
    produces.

    That gap is not hypothetical. Both stores ``docs/claude/66`` is about were
    found by somebody grepping, and the binary list was hand-written three
    times while this was being scoped and was wrong all three times -- it
    missed ``hub/proposals.py``, ``modules/hvac``, ``modules/landing_ads``,
    ``modules/radio_promo`` and ``modules/restaurant``. A list a person
    maintains is a list that is already stale.

    Read by AST rather than by substring, for the reason
    ``_writes_json_to_disk`` gives about its own prose: ``open(`` and the
    string ``"wb"`` both appear in the comments around here.

    What counts is the spelling that reaches a file: ``open(path, "wb")``,
    ``Path.open("wb")`` and ``Path.write_bytes()``. Deliberately NOT ``.save()``
    -- that is Werkzeug's upload spelling, no call site in this repo uses it
    for an upload, and counting every ``.save(`` would report the thirty-odd
    ``store.save(project)`` calls that are JSON going through the mirror. That
    is the same trade ``_writes_json_to_disk`` makes about ``json.dumps``:
    thirty findings nobody can act on is the same as none, one screen later.
    """
    import ast
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    out: list[dict] = []
    for p in sorted(base.rglob("*.py")):
        if any(x in p.parts for x in SCAN_SKIP_DIRS):
            continue
        rel = p.relative_to(base).as_posix()
        if _disk_binary_exempt_reason(rel):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        hit = _binary_write_line(tree)
        if hit:
            line, how = hit
            out.append({"file": rel, "module": _module_of(rel),
                        "line": line, "how": how})
    return out


def _binary_write_line(tree) -> tuple[int, str] | None:
    """The first binary write in this tree, as (line, how), or None."""
    import ast
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "write_bytes":
            return (node.lineno, "write_bytes()")
        # `open(p, "wb")` and pathlib's `p.open("wb")` are the same write.
        if not ((isinstance(fn, ast.Name) and fn.id == "open")
                or (isinstance(fn, ast.Attribute) and fn.attr == "open")):
            continue
        for arg in node.args:
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            mode = arg.value
            # "b" alone is not enough: "rb" is a read, and a check that
            # reported every file this suite opens for reading would be noise.
            if "b" in mode and any(c in mode for c in "wax"):
                return (node.lineno, f'open(..., "{mode}")')
    return None


def _disk_binary_exempt_reason(rel: str) -> str | None:
    """Why this file is not counted, or None if it is.

    A third function rather than a shared one, for the reason
    ``_disk_sqlite_exempt_reason`` gives: a file rightly excused from one of
    these checks is not thereby excused from the others, and the three exempt
    for genuinely different reasons.
    """
    if rel in DISK_BINARY_EXEMPT:
        return DISK_BINARY_EXEMPT[rel]
    if rel.startswith("tools/") or "/scripts/" in rel or rel.startswith("scripts/"):
        return "repo tooling; holds no state on the data disk"
    name = rel.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return "test; writes to a temporary directory"
    return None


def stale_binary_exemptions(root=None) -> list[str]:
    """``DISK_BINARY_EXEMPT`` entries pointing at a file that is no longer there.

    For the reason ``stale_exemptions()`` gives: an exemption that outlives its
    file is a claim nobody can check, and the next file to take that path
    inherits an excuse written about different code.
    """
    import pathlib
    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    return sorted(rel for rel in DISK_BINARY_EXEMPT
                  if not (base / rel).exists())


def disk_sqlite_stores(root=None) -> list[dict]:
    """Every source file that opens a SQLite database directly on the disk.

    The disk is outside the database backup and does not survive the service
    being recreated, so what is in one of these is what would be lost -- which
    is the same sentence ``unmirrored_json_writers()`` exists to be able to
    say, about the files it can see.

    Read rather than run, and by AST rather than by substring, for the reason
    ``_writes_json_to_disk`` gives: the text ``sqlite3.connect(`` appears in
    this module's own prose, and a check a comment can trip is one nobody
    trusts.
    """
    import ast
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    out: list[dict] = []
    for p in sorted(base.rglob("*.py")):
        if any(x in p.parts for x in SCAN_SKIP_DIRS):
            continue
        rel = p.relative_to(base).as_posix()
        if _disk_sqlite_exempt_reason(rel):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "connect" \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "sqlite3":
                out.append({"file": rel, "module": _module_of(rel),
                            "line": node.lineno})
                break
    return out


def _disk_sqlite_exempt_reason(rel: str) -> str | None:
    """Why this file is not counted, or None if it is.

    The same shape as ``_unmirrored_exempt_reason``, and deliberately a second
    function rather than a shared one: the two checks exempt for different
    reasons, and a file that is rightly excused from one is not thereby
    excused from the other.
    """
    if rel in DISK_SQLITE_EXEMPT:
        return DISK_SQLITE_EXEMPT[rel]
    if rel.startswith("tools/") or "/scripts/" in rel or rel.startswith("scripts/"):
        return "repo tooling; holds no state on the data disk"
    name = rel.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return "test; writes to a temporary directory"
    return None


def stale_sqlite_exemptions(root=None) -> list[str]:
    """``DISK_SQLITE_EXEMPT`` entries pointing at a file that is no longer there.

    For the reason ``stale_exemptions()`` gives: a deleted file leaves an entry
    that silently covers whatever is written at that path next.
    """
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    return [f"{rel} (no such file)" for rel in DISK_SQLITE_EXEMPT
            if not (base / rel).is_file()]
