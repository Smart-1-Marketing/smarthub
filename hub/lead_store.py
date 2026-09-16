"""Leads in a table, because a file is local to one instance.

## Why this exists

`hub/leads.py` is the one place every landing page and calculator writes to,
and the module opens by saying a lead we already have is never destroyed. It
kept them in an append-only JSONL on the Render disk.

Two problems, and the disk is only the first.

**The disk is outside the database backup and does not survive being
recreated.** Every lead the business has captured was in one file that nothing
copied anywhere. `jsonstore.unmirrored_json_writers()` is the check that is
supposed to say so, and it could not see this file at all: the pattern it
matched was the literal ``json.dump(`` and the store writes
``fh.write(json.dumps(row) + "\\n")``. It reported a clean bill.

**And a file is local to one instance.** Two gunicorn workers today, and the
two halves of a zero-downtime deploy tomorrow: each holds its own file, and
which one answers is whichever container the browser reached. A lead captured
on one is invisible on the other, and the panel that exists to answer "how
many leads did we get last week" gets a different answer per refresh.

## What changes underneath, and what does not

Every function in `hub/leads.py` keeps its signature and its meaning. What
goes is the *shape* of a mutation. A file has no way to change one row, so
every press -- marking converted, tagging a temperature, recording a delivery
attempt -- read the whole store, changed one dict and wrote the lot back.
`_rewrite()` carries a long comment about the lead that goes missing when a
visitor's capture lands between another worker's read and its ``os.replace``,
and mitigates it by re-reading inside the lock and keeping rows the caller
never saw -- which is only safe because this store never deletes.

An UPDATE by id cannot take a concurrent INSERT with it, so that whole class
of loss is gone rather than mitigated.

## The row is JSON, and four columns are not

`payload` holds the whole lead dict, so nothing is lost or silently truncated
by a schema that drifts from what `capture()` builds -- a lead's `fields` and
`meta` are open-ended by design. The four columns beside it are the ones
something actually filters or sorts on: `created`, `source`, `client` and
`delivered`. A column exists here because a query needs it, not because the
dict has a key.

## A lead is never lost to a storage fault

`capture()` promises it never raises, and that promise is older than this
module. Where the database will not answer, the row goes to a pending file and
the next successful write flushes it -- the arrangement `hub/audit.py` arrived
at one store earlier, for the same reason and with the same rule: a database
that will not answer is a reason to write the row somewhere else, never a
reason to lose it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

try:
    from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Index,
                            Integer, MetaData, String, Table, Text, delete,
                            insert, select, update)
    _SA_ERROR = ""
except Exception as exc:                                # noqa: BLE001
    Table = None                                        # type: ignore
    _SA_ERROR = f"{type(exc).__name__}: {exc}"

_lock = threading.Lock()
_db_lock = threading.Lock()
_engine = None
_table = None
_ready = False
_init_error = ""
_init_retry_at = 0.0
INIT_RETRY_SECONDS = 120

#: Rows written to the pending file because the database would not take them.
_file_rows_written = 0
_import_state: dict = {"ran": False}


def _reason(exc: Exception) -> str:
    """The type and the first line, never the bound parameters.

    A SQLAlchemy error carries the statement and its values, and the values
    here are a visitor's name, email and phone. `status()` is rendered on
    /diagnostics, so an untrimmed message would put a stranger's contact
    details on a staff screen -- and into whatever reads that panel next.
    """
    return f"{type(exc).__name__}: {_one_line(str(exc))}"


def _one_line(text: str) -> str:
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return lines[0][:200] if lines else ""


def _legacy_path() -> str:
    """The JSONL this is migrating from, and the fallback's neighbour.

    `hub/leads.py` owns the path -- HUB_LEADS_FILE and all -- and asking it
    rather than rebuilding the expression keeps one answer to "where was the
    file". It selects no backend: the rule `hub/audit.py` arrived at and
    `modules/smartforecast` repeated, because a variable that is set on the
    live service and also chooses the backend keeps production on the disk
    while every test passes on the new path.
    """
    from . import leads
    return leads._path()


def _init() -> bool:
    """Open the engine and make sure `hub_leads` exists. Once, lazily.

    Never raises. The failure is cached for `INIT_RETRY_SECONDS` rather than
    for the life of the worker: a Hub that came up while Render's Postgres was
    waking would otherwise file every lead of that boot on the disk.
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
            from . import extensions
            _engine = extensions.engine_for()
            meta = MetaData()
            _table = Table(
                "hub_leads", meta,
                # Append order is the primary key, and the lead's own id is a
                # unique column beside it.
                #
                # The other way round is the obvious one and does not work:
                # a generated column autoincrements on NEITHER backend unless
                # it is the primary key -- SQLite gives a rowid only to a
                # column declared exactly INTEGER PRIMARY KEY, and SQLAlchemy
                # attaches a sequence only to the primary key. A `seq` sitting
                # next to a String primary key would therefore be NOT NULL
                # with no default and refuse every insert.
                #
                # BigInteger with the sqlite variant for the same reason as
                # hub_activity: against a BIGINT, SQLite refuses every insert
                # on a NOT NULL id, which is the whole suite quietly on the
                # fallback file with production on the table.
                Column("seq", BigInteger().with_variant(Integer, "sqlite"),
                       primary_key=True, autoincrement=True),
                # The lead's own id. It is already a uuid4 hex that every
                # caller holds -- get(), merge(), mark_converted() -- so it is
                # unique and indexed rather than regenerated: giving the same
                # lead a second identity is how a merge points at the wrong
                # row.
                Column("id", String(40), nullable=False, unique=True),
                Column("created", DateTime, nullable=False, index=True),
                Column("source", String(60), nullable=False, index=True),
                Column("client", String(120), index=True),
                Column("delivered", Boolean, nullable=False, default=False),
                # The whole dict. `fields` and `meta` are open-ended by
                # design, and a column per key would drift from what
                # capture() builds the first time a landing page adds one.
                Column("payload", Text, nullable=False),
            )
            # "Undelivered leads, oldest first" is what the hourly retry sweep
            # asks for, and it is the read that runs without anybody watching.
            Index("ix_hub_leads_delivered_seq", _table.c.delivered,
                  _table.c.seq)
            err = extensions.create_all_metadata(meta, retry=False)
            if err:
                _init_error = _one_line(err)
                _engine = None
                return False
            _ready = True
            _init_error = ""
            return True
        except Exception as exc:                        # noqa: BLE001
            _init_error = _reason(exc)
            _engine = None
            return False


def _when(row: dict) -> datetime:
    """The lead's own timestamp, or now.

    Taken from the row rather than the clock a second time: the import reads
    leads captured months ago, and stamping those with today would file the
    entire history as having arrived on the afternoon somebody deployed this.
    """
    raw = str(row.get("created") or "")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _columns_for(row: dict) -> dict:
    return {
        "id": str(row.get("id") or "")[:40],
        "created": _when(row),
        "source": str(row.get("source") or "")[:60],
        "client": (str(row.get("client"))[:120] if row.get("client") else None),
        "delivered": bool(row.get("delivered")),
        "payload": json.dumps(row, ensure_ascii=False),
    }


def _db_insert(rows: list[dict]) -> bool:
    """Insert leads. False means the caller should write the pending file."""
    if not rows or not _init():
        return False
    try:
        with _engine.begin() as cx:
            cx.execute(insert(_table), [_columns_for(r) for r in rows])
        return True
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return False


def _statement_fault(exc: Exception) -> bool:
    """Whether this error is the statement's fault rather than the backend's.

    A unique-constraint violation means one caller passed something the table
    refused. The database answered -- it answered "no" -- so treating it as an
    outage takes the backend down for the whole cooldown, and for those two
    minutes every lead goes to the pending file and every read falls back to
    the legacy file, because one caller sent a duplicate id.

    Found by a test that forced exactly that to check the rollback: the
    rollback was right, and the store was unreadable afterwards anyway.
    """
    try:
        from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError
    except Exception:                                   # noqa: BLE001
        return False
    return isinstance(exc, (IntegrityError, DataError, ProgrammingError))


def _went_down(exc: Exception) -> None:
    """Record the error, and start the cooldown only for a real outage."""
    global _ready, _init_error, _init_retry_at
    with _db_lock:
        _init_error = _reason(exc)
        if _statement_fault(exc):
            return              # the database answered; it is still there
        _ready = False
        _init_retry_at = time.time() + INIT_RETRY_SECONDS


def all_rows() -> list[dict] | None:
    """Every lead in append order, or None where the database could not answer.

    None rather than [] on a fault, and that distinction is the whole point:
    an empty list here reads as "no leads have ever been captured", which the
    panel would render as a quiet zero and somebody would believe. The caller
    falls back to the file instead.
    """
    if not _init():
        return None
    try:
        with _engine.connect() as cx:
            rows = cx.execute(
                select(_table.c.payload).order_by(_table.c.seq)).fetchall()
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return None
    out = []
    for (payload,) in rows:
        try:
            out.append(json.loads(payload))
        except ValueError:
            continue        # one unreadable row must not hide the rest
    return out


def update_one(row: dict) -> bool:
    """Change one lead by id. False if the database would not take it.

    This is the function the file could not have. Every mutation used to read
    the whole store, change one dict and write the lot back, so a capture that
    landed in between was overwritten -- `hub/leads._rewrite()` carries the
    incident and the mitigation. One statement touching one row cannot take a
    concurrent insert with it.
    """
    if not _init():
        return False
    lead_id = str(row.get("id") or "")
    if not lead_id:
        return False
    try:
        with _engine.begin() as cx:
            cols = _columns_for(row)
            cols.pop("id")                      # the key is not the change
            result = cx.execute(
                update(_table).where(_table.c.id == lead_id).values(**cols))
            if result.rowcount:
                return True
            # Not there yet: `_update()` appends an unknown row rather than
            # dropping it, and so does this.
            cx.execute(insert(_table), [_columns_for(row)])
        return True
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return False


def replace_all(rows: list[dict]) -> bool:
    """Make the table hold exactly `rows`, in one transaction.

    `merge()` is the caller that needs it: absorbing one lead into another
    rewrites both and the module's promise is that neither is destroyed, so
    the two writes have to land together or not at all. In a file that meant
    rewriting everything; here it is a delete and an insert inside one
    transaction, which either commits or leaves the table as it was.

    Rows that arrived while the caller was working are kept, the same rule
    `_rewrite()` documents -- a lead this caller has never seen is one that
    was captured meanwhile, and the only correct thing to do with it is let
    it survive.
    """
    if not _init():
        return False
    try:
        with _engine.begin() as cx:
            known = {str(r.get("id")) for r in rows if r.get("id")}
            existing = cx.execute(select(_table.c.id, _table.c.payload)
                                  .order_by(_table.c.seq)).fetchall()
            arrived = []
            for lead_id, payload in existing:
                if str(lead_id) in known:
                    continue
                try:
                    arrived.append(json.loads(payload))
                except ValueError:
                    continue
            cx.execute(delete(_table))
            keep = [r for r in list(rows) + arrived if r.get("id")]
            if keep:
                cx.execute(insert(_table), [_columns_for(r) for r in keep])
        return True
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return False


def count() -> int:
    """How many leads the table holds, or -1 where it could not be asked."""
    if not _init():
        return -1
    try:
        from sqlalchemy import func
        with _engine.connect() as cx:
            return int(cx.execute(
                select(func.count()).select_from(_table)).scalar() or 0)
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return -1


# ---------------------------------------------------------------------------
# The fallback. capture() promises it never raises, and that promise is older
# than this module.
# ---------------------------------------------------------------------------

def pending_path() -> str:
    """Where a lead goes when the database would not take it.

    Deliberately not the legacy JSONL. Those two files answer different
    questions -- one is the history this is migrating *from*, the other is
    what this process could not write *today* -- and one file holding both
    leaves the import unable to tell them apart, so an outage's leads are
    either imported twice or not at all.
    """
    return _legacy_path() + ".pending"


def _file_rows(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue        # one bad line must not hide the rest
    except OSError:
        return []
    return out


def write_pending(row: dict) -> None:
    """Append one lead to the pending file. Never raises."""
    global _file_rows_written
    path = pending_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _file_rows_written += 1
    except OSError:
        pass    # nowhere left to put it; raising would cost the lead as well


def flush_pending() -> int:
    """Put what the outage captured into the table. Returns leads recovered.

    Renamed before it is read, which is what makes this safe to call from
    every successful write: `os.replace` is atomic, so of two workers reaching
    here at once exactly one gets the file and the other finds nothing. A
    batch the database then refuses is put back rather than dropped -- these
    are the leads that already had one chance to be lost.
    """
    global _file_rows_written
    src = pending_path()
    try:
        if os.path.getsize(src) <= 0:
            return 0
    except OSError:
        return 0
    claimed = f"{src}.{os.getpid()}.{int(time.time() * 1000)}"
    try:
        os.replace(src, claimed)
    except OSError:
        return 0
    rows = _file_rows(claimed)
    if rows and not _db_insert(rows):
        try:                                    # put them back, do not lose
            with _lock, open(src, "a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except OSError:
            pass
        try:
            os.remove(claimed)
        except OSError:
            pass
        return 0
    try:
        os.remove(claimed)
    except OSError:
        pass                        # the leads are in the table; this is litter
    _file_rows_written = max(0, _file_rows_written - len(rows))
    return len(rows)


def store(row: dict) -> str:
    """Put one lead somewhere it will survive. Returns which backend took it.

    "database" or "pending". It never raises and never returns nothing: the
    caller's promise to the visitor is that the lead is recorded before
    anything is sent anywhere, and this is the half of that promise that has
    to hold when Postgres does not answer.

    The flush runs BEFORE this lead, not after, so the outage's leads keep
    their place in front of the one that ended it. Written the other way round
    the recovered rows take higher `seq` values than a lead captured after
    them, and append order -- which is the order the panel shows and the order
    the retry sweep works -- reports the outage as having happened last.
    """
    try:
        # Unconditionally, not gated on `_file_rows_written`. That counter is
        # per process, so a spill written before a restart -- which is most of
        # them, since the deploy is usually what ends the outage -- would find
        # it at zero and never be flushed at all: the leads stay readable and
        # stay off the backup, indefinitely. `flush_pending()` stats the file
        # and returns immediately when there is nothing there, so the cost of
        # asking every time is one stat on a path a person is waiting on.
        flush_pending()
        if _db_insert([row]):
            return "database"
    except Exception:                                   # noqa: BLE001
        pass                # a fault here is a reason to use the file, not to
                            # cost the caller the lead
    write_pending(row)
    return "pending"


# ---------------------------------------------------------------------------
# The one-time import.
# ---------------------------------------------------------------------------

def import_legacy(force: bool = False) -> dict:
    """Carry the JSONL into the table, once, and verify before saying done.

    Once across every instance rather than once per worker: the check and the
    copy are inside one `jsonstore.update_json()`, which holds the thread
    lock, the flock and the Postgres advisory lock that spans instances. Two
    workers both reading "not yet" is every lead stored twice.

    Verified rather than assumed, and a count that does not match leaves the
    marker unwritten so the next boot tries again -- recording that a history
    nobody checked had been carried across is the one outcome worse than not
    having run.
    """
    global _import_state
    path = _legacy_path()
    try:
        if not os.path.getsize(path):
            _import_state = {"ran": False, "reason": "no legacy file"}
            return dict(_import_state)
    except OSError:
        _import_state = {"ran": False, "reason": "no legacy file"}
        return dict(_import_state)

    if not _init():
        _import_state = {"ran": False, "reason": _init_error or "no database"}
        return dict(_import_state)

    outcome: dict = {"ran": False, "reason": "already imported"}

    def _mutate(current):
        if isinstance(current, dict) and current.get("done") and not force:
            return None                     # nothing to write, nothing to do
        rows = [r for r in _file_rows(path) if r.get("id")]
        if not rows:
            outcome.update(ran=False, reason="legacy file holds no leads")
            return None
        if not _db_insert_missing(rows):
            outcome.update(ran=False, reason=_init_error or "insert failed")
            return None
        # Verified by asking which ids are NOT there, rather than by counting.
        # A count that matches is a count, and this table already holds rows
        # the file never had -- anything captured since the cutover -- so
        # arithmetic on totals would report a clean import for a file whose
        # leads were never read. The question is per lead: is this one in the
        # table? Any answer but "all of them" leaves the marker unwritten.
        missing = _missing_ids([str(r["id"]) for r in rows])
        if missing is None:
            outcome.update(ran=False, reason="could not verify the import")
            return None
        if missing:
            outcome.update(ran=False, reason="verification failed",
                           missing=len(missing), sample=sorted(missing)[:5])
            return None
        outcome.update(ran=True, reason="", imported=len(rows),
                       rows_after=count())
        return {"done": True, "imported": len(rows), "from": path,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    try:
        from . import jsonstore
        marker = os.path.join(jsonstore.data_dir("leads"), "import.json")
        jsonstore.update_json(marker, _mutate, default={})
    except Exception as exc:                            # noqa: BLE001
        outcome = {"ran": False, "reason": _reason(exc)}
    _import_state = dict(outcome)
    return dict(outcome)


def _db_insert_missing(rows: list[dict]) -> bool:
    """Insert only the leads the table does not already hold.

    The import is retried whenever the last attempt did not verify, so it has
    to be safe to run twice. Skipping the ids already there is what makes a
    half-finished import finishable rather than a duplicate-key failure that
    can never complete.
    """
    if not _init():
        return False
    try:
        with _engine.begin() as cx:
            have = {str(r[0]) for r in cx.execute(select(_table.c.id))}
            fresh = [r for r in rows if str(r.get("id")) not in have]
            if fresh:
                cx.execute(insert(_table), [_columns_for(r) for r in fresh])
        return True
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return False


def _missing_ids(ids: list[str]) -> set[str] | None:
    """Which of these leads the table does not hold. None if it could not say.

    None and an empty set are different answers and the caller treats them
    differently: "none are missing" marks the import done, "I could not ask"
    must not.
    """
    if not _init():
        return None
    try:
        with _engine.connect() as cx:
            have = {str(r[0]) for r in cx.execute(select(_table.c.id))}
    except Exception as exc:                            # noqa: BLE001
        _went_down(exc)
        return None
    return {i for i in ids if i not in have}


def status() -> dict:
    """Which backend answered, and what the fallback is still holding."""
    pending = -1
    try:
        pending = len(_file_rows(pending_path()))
    except Exception:                                   # noqa: BLE001
        pending = -1
    return {
        "backend": "database" if _ready else "file",
        "ready": bool(_ready),
        "error": _init_error,
        "pending_leads": pending,
        "pending_path": pending_path(),
        "legacy_path": _legacy_path(),
        "import": dict(_import_state),
    }


def drop_for_tests() -> None:
    """Drop the lead table. A test harness only.

    A test file gets a clean start by pinning its own database, which on
    SQLite is a fresh file per run and on a shared Postgres is not -- so on
    Postgres something has to empty it, or every check after the first reads
    the leads the run before it invented. `modules/smartforecast/db.py` holds
    the same arrangement one store over, for the same reason.
    """
    global _ready, _table, _engine, _init_retry_at, _init_error
    if not _init():
        return
    try:
        with _engine.begin() as cx:
            _table.drop(cx, checkfirst=True)
    except Exception:                                   # noqa: BLE001
        pass
    with _db_lock:
        _ready = False
        _table = None
        _engine = None
        _init_retry_at = 0.0
        _init_error = ""
