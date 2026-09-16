"""Suite-wide append-only activity log.

Every module writes through here so the Hub has ONE attributed history:
logins, GHL account create/delete, etc.

## The backend is the database wherever there is one

This was a JSONL file on the Render persistent disk, and that disk is the one
thing here that is **not** backed up -- the argument `hub/jsonstore.py` opens
with, applied to the log rather than to a JSON blob. Worse than unbacked: a
file is local to one instance, so the two halves of a zero-downtime deploy
keep two different histories and `/activity` shows whichever one answered.
The activity log is the record somebody reconstructs an incident from, and a
record that depends on which worker you reached is not one.

So the rows live in `hub_activity`, through the shared engine -- Postgres in
production, whatever `DATABASE_URL` names in a test. The file is what a Hub
with no database at all falls back to, and it is what the one-time import
reads.

**`AUDIT_LOG_PATH` no longer decides anything**, and that is the part worth
reading twice. It names *where the file is*, which is a fact about the
fallback; the live service sets it, so had it gone on selecting the backend
the obvious design -- "the file when it is set, the database otherwise" --
would have kept production on the disk while every test passed on the new
path. It is read by `_path()`, by the import, and by nothing else.

The reverse is just as bad and is the reason the database is not gated on
Postgres specifically: 78 of the 79 test files that set `AUDIT_LOG_PATH` pin
`DATABASE_URL` at a SQLite file of their own, so a Postgres-only rule would
have left every one of them exercising the file backend while production ran
the database one. A backend no test exercises is a backend nobody has checked.

## Failure is never the caller's problem

`log()` has always swallowed its own failures -- the action is what matters
and a log that breaks it is worse than a missing row -- and that is unchanged:
a database that will not answer falls back to the file, and a file that will
not open is a silent no-op exactly as before. `read()` and `tail()` keep their
signatures and their shape: a row is the same dict the JSONL held, because the
whole entry is stored as its payload and handed back verbatim.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

try:                                                     # pragma: no cover
    from sqlalchemy import (BigInteger, Column, DateTime, Index, Integer,
                            MetaData, String, Table, Text, delete, insert,
                            select)
    _SA_ERROR = ""
except Exception as _exc:                                # noqa: BLE001
    BigInteger = Column = DateTime = Index = Integer = None
    MetaData = None
    String = Table = Text = delete = insert = select = None
    _SA_ERROR = f"{type(_exc).__name__}: {_exc}"

_lock = threading.Lock()

# The database half. `_init()` is lazy for the reason jsonstore's is: import
# time is boot time, and a database still waking must not hold the workers
# back -- the first write is a much better moment to find out.
_db_lock = threading.Lock()
_engine = None
_table = None
_ready = False
_init_done = False
_init_error = ""
_init_retry_at = 0.0
INIT_RETRY_SECONDS = 120

# What the fallback has written since the database stopped answering. Reported
# rather than counted silently: rows in the file on an instance with no disk
# of its own are rows the next deploy takes with it, which is the whole thing
# this module moved to the database to stop.
_file_rows_written = 0
_import_state: dict = {"ran": False}

# Rows kept when `rotate()` prunes. An append-only table with no ceiling is
# the same slow-motion outage the unrotated file was, one storage layer over.
MAX_ROWS = 400_000


def _path() -> str:
    """Where the log *file* lives -- which is no longer where the log lives.

    It is the legacy history the import reads, and the fallback a Hub with no
    database writes to. `AUDIT_LOG_PATH` still wins over the root for it, and
    still for the same reason: it names one file rather than a directory, so
    it is the more specific answer. What it no longer does is choose a
    backend, and the module docstring says why at length -- the live service
    sets it, so a rule reading it as "use the file" would have kept
    production on the disk with every test green.

    Everything else defers to
    `jsonstore.data_root()`, which is *the* place that decides where persistent
    files live and whose own docstring names this failure: "every module had
    its own copy of this expression. They all agreed, which is luck rather
    than design: the moment one of them disagreed, its files would land
    somewhere the backup sweep never looks."

    This was that copy, and it did disagree -- on `HUB_DATA_DIR`, which
    data_root() reads first and this did not read at all. Nothing moves on
    Render, where HUB_DATA_DIR is unset and /var/data is mounted; what changes
    is a test that sets it, which used to be handed the real shared log.

    Never raises: this is the log, and a log that can break a boot is worse
    than one in the wrong place.
    """
    p = os.environ.get("AUDIT_LOG_PATH")
    if p:
        return p
    try:
        from . import jsonstore
        return os.path.join(jsonstore.data_root(), "hub-audit.log.jsonl")
    except Exception:  # noqa: BLE001 — fall back to the expression it replaced
        if os.path.isdir("/var/data"):
            return "/var/data/hub-audit.log.jsonl"
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hub-audit.log.jsonl")


def _reason(exc: Exception) -> str:
    """One line, because `status()` is rendered into /diagnostics.

    SQLAlchemy puts the statement and every bound parameter into `str(exc)` --
    so an insert that failed would have printed the rows it was carrying, and
    those are activity rows naming clients and members of staff, onto a page
    that gets pasted into chats. The rule `services/provider_check.py` works
    to, wearing a traceback. The cause still rides the exception chain, so a
    real fault is diagnosable from the log.
    """
    return f"{type(exc).__name__}: {_one_line(str(exc))}"


def _one_line(text: str) -> str:
    """First line, capped. See `_reason`.

    A string as well as an exception, because `create_all_metadata()` RETURNS
    its error rather than raising it -- so that one reached `status()` whole,
    past the trimmer, which is a rule enforced at one of its two doors.
    """
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return lines[0][:200] if lines else ""


def _init() -> bool:
    """Open the engine and make sure `hub_activity` exists. Once, lazily.

    Never raises. A database that will not answer is a reason to write the
    row to the file, never a reason to lose it -- and the failure is cached
    for `INIT_RETRY_SECONDS` rather than for the life of the worker, because
    a Hub that came up while Render's Postgres was waking would otherwise
    file every row of that boot on a disk nobody backs up.
    """
    global _engine, _table, _ready, _init_done, _init_error, _init_retry_at
    with _db_lock:
        if _init_done and (_ready or time.time() < _init_retry_at):
            return _ready
        # Past here the cooldown has elapsed (or nothing has been tried yet),
        # so this attempt is the retry and the next one waits again.
        _init_done = True
        _init_retry_at = time.time() + INIT_RETRY_SECONDS
        if Table is None:
            _init_error = f"SQLAlchemy unavailable ({_SA_ERROR})"
            return False
        try:
            from . import extensions
            _engine = extensions.engine_for()
            meta = MetaData()
            _table = Table(
                "hub_activity", meta,
                # BigInteger because this table grows for ever, and the
                # sqlite variant because SQLite autoincrements a rowid only
                # for a column declared INTEGER -- against a BIGINT it
                # refuses every insert on a NOT NULL id, which is the whole
                # test suite silently on the fallback file with production on
                # the table. Found by running it.
                Column("id", BigInteger().with_variant(Integer, "sqlite"),
                       primary_key=True, autoincrement=True),
                Column("at", DateTime, nullable=False),
                Column("module", String(80), nullable=False, index=True),
                Column("type", String(80), nullable=False, index=True),
                Column("actor", String(60)),
                Column("payload", Text, nullable=False),
            )
            # Narrowing by module is what /activity's own dropdown does and
            # what client_brand's work index does per client, so the pair is
            # the index that matters rather than either column alone.
            Index("ix_hub_activity_module_id", _table.c.module, _table.c.id)
            # Advisory-locked through extensions, so two workers racing to
            # create it is not the pg_type_typname_nsp_index violation on
            # every deploy. retry=False for jsonstore's reason: this is
            # reached from the write path, not from boot, so the boot backoff
            # would be spent inside somebody's action.
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


def _when(entry: dict) -> datetime:
    """The row's own timestamp, or now.

    `at` is a column so a report can ask for a month without parsing every
    payload, and it is derived from the entry rather than taken from the clock
    a second time -- the import reads rows written years ago, and stamping
    those with today would file the whole of the old log as having happened on
    the afternoon somebody deployed this.
    """
    raw = str(entry.get("time") or "")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _row_for(entry: dict) -> dict:
    return {
        "at": _when(entry),
        "module": str(entry.get("module") or "")[:80],
        "type": str(entry.get("type") or "")[:80],
        "actor": (str(entry.get("actor"))[:60] if entry.get("actor") else None),
        "payload": json.dumps(entry, ensure_ascii=False),
    }


def _db_write(entries: list[dict]) -> bool:
    """Insert rows. False means the caller should write the file instead."""
    if not entries or not _init():
        return False
    try:
        with _engine.begin() as cx:
            cx.execute(insert(_table), [_row_for(e) for e in entries])
        return True
    except Exception as exc:                            # noqa: BLE001
        global _ready, _init_done, _init_error, _init_retry_at
        with _db_lock:
            _ready = False
            _init_done = True
            _init_retry_at = time.time() + INIT_RETRY_SECONDS
            _init_error = _reason(exc)
        return False


def _db_read(limit: int, module: str | None, type_: str | None):
    """The newest rows, or None where the database could not be asked.

    None rather than `[]`, because *we could not look* and *nothing has been
    filed* are different answers and only the second means there is nothing
    here -- the rule `connected_accounts_result()` gives one module over. The
    caller falls back to the file on None and reports the empty list as an
    empty list.

    Ordered by `id`, which is insertion order and therefore the exact
    analogue of the file's own. Ordering on `at` would reorder every row
    written inside one second, and the log stamps to the second.
    """
    if not _init():
        return None
    try:
        q = select(_table.c.payload).order_by(_table.c.id.desc())
        if module:
            q = q.where(_table.c.module == str(module)[:80])
        if type_:
            q = q.where(_table.c.type == str(type_)[:80])
        with _engine.connect() as cx:
            rows = cx.execute(q.limit(max(1, int(limit)))).fetchall()
    except Exception as exc:                            # noqa: BLE001
        global _init_error
        _init_error = _reason(exc)
        return None
    out = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _file_entries(path: str) -> list[dict]:
    """Every parseable row in one JSONL file, oldest first."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def import_legacy(force: bool = False) -> dict:
    """Move the JSONL history into the table, once.

    Three things this has to get right, and each is a way to end up with a log
    that is worse than the one it replaced.

    **Once across every instance, not once per worker.** The check for whether
    it has run and the insert have to be inside one lock or two workers both
    read "not yet" and the whole history lands twice -- so the marker is
    written through `jsonstore.update_json()`, which holds the thread lock,
    the flock and the Postgres advisory lock that spans instances. Deciding
    inside the mutate is what makes that true rather than nearly true.

    **The marker is durable, and it is not the row count.** "The table is
    empty" would re-import the entire old file the first time `rotate()`
    prunes it back to nothing -- a migration that fires again years later, on
    a Hub whose log had been pruned on purpose.

    **A file that cannot be read is not a file with nothing in it.** Nothing
    is marked done on a failed read, so the next boot tries again rather than
    recording that a history we never saw had been carried across.
    """
    path = _path()
    try:
        if not force and os.path.getsize(path) <= 0:
            return {"ran": False, "reason": "no legacy file"}
    except OSError:
        return {"ran": False, "reason": "no legacy file"}
    if not _init():
        return {"ran": False, "reason": _init_error or "no database"}

    try:
        from . import jsonstore
        marker = os.path.join(jsonstore.data_root(), "audit-import.json")
    except Exception as exc:                            # noqa: BLE001
        return {"ran": False, "reason": _reason(exc)}

    outcome: dict = {"ran": False, "reason": "already imported"}

    def _mutate(cur):
        if not isinstance(cur, dict):
            cur = {}
        if cur.get("done") and not force:
            return None                     # nothing to write, and none to do
        entries = _file_entries(path)
        if not entries:
            outcome.update(ran=False, reason="legacy file could not be read")
            return None
        if not _db_write(entries):
            outcome.update(ran=False,
                           reason=_init_error or "the insert did not land")
            return None
        outcome.update(ran=True, reason="", imported=len(entries))
        return {"done": True, "imported": len(entries), "from": path,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    global _import_state
    try:
        jsonstore.update_json(marker, _mutate, default={})
    except Exception as exc:                            # noqa: BLE001
        outcome = {"ran": False, "reason": _reason(exc)}
    _import_state = dict(outcome)
    return outcome


def _pending_path() -> str:
    """Where a row goes when the database would not take it.

    Deliberately not the legacy log. Those two files answer different
    questions -- one is the history this module is migrating *from* and the
    other is what this process could not write *today* -- and one file holding
    both makes the import unable to tell them apart, so the rows written
    during an outage are either imported twice or not at all.

    On a deployment that has never had a database at all this is where the
    whole log ends up, and the name is still the true one: those rows are
    pending a table that does not exist yet. `status()` and the /diagnostics
    row say so in words rather than leaving somebody to infer it from a
    filename.
    """
    return _path() + ".pending"


def _flush_pending() -> int:
    """Put what the outage wrote into the table. Returns rows recovered.

    Renamed before it is read, which is what makes this safe to call from
    every successful write: `os.replace` is atomic, so of two workers reaching
    here at once exactly one gets the file and the other finds nothing. A
    batch the database then refuses is put back rather than dropped -- these
    are the rows that already had one chance to be lost.
    """
    global _file_rows_written
    src = _pending_path()
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
    entries = _file_entries(claimed)
    if entries and not _db_write(entries):
        try:                                   # put it back, do not lose it
            with _lock, open(src, "a", encoding="utf-8") as fh:
                for e in entries:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            # A disk that will not take them back has lost them, and there is
            # nowhere left to put them: raising here would cost the caller the
            # action as well, which is the one thing log() may never do.
            pass
        try:
            os.remove(claimed)
        except OSError:
            pass                    # a leftover claim file is swept by rotate
        return 0
    try:
        os.remove(claimed)
    except OSError:
        pass                        # the rows are in the table; this is litter
    _file_rows_written = max(0, _file_rows_written - len(entries))
    return len(entries)


def _write_file(path: str, entry: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass      # best-effort — never break the action because logging failed


def log(module: str, type_: str, actor: str | None = None, **extra) -> None:
    """Write one entry. Never raises, whatever the backend does.

    `time` is in the extras rather than a parameter, and the merge below is
    what lets a caller override it -- a row is stamped now unless somebody
    passes a time, which is what back-dating a fixture needs and what the
    `at` column is read from. It is documented here rather than left as a
    property of the dict update, because a test relying on an accident is a
    test that breaks on a tidy-up nobody thought was a behaviour change.
    """
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "module": module,
        "type": type_,
    }
    if actor:
        entry["actor"] = str(actor)[:60]
    entry.update({k: v for k, v in extra.items() if v is not None})
    global _file_rows_written
    try:
        if _file_rows_written:
            # Before this row, not after it. The rows an outage wrote happened
            # first, and `id` is what "newest" means -- flushed afterwards they
            # take ids above the row being written now, so the first thing
            # /activity shows once the database comes back is the outage, with
            # everything since it underneath. Found by asserting the order
            # rather than the contents.
            _flush_pending()
        if _db_write([entry]):
            return
        _file_rows_written += 1
        _write_file(_pending_path(), entry)
    except Exception:                                   # noqa: BLE001
        # The rule this module has always worked to: the action is what
        # matters, and a log that can break it is worse than a missing row.
        try:
            _write_file(_pending_path(), entry)
        except Exception:                               # noqa: BLE001
            pass


def _file_rows(limit: int, module: str | None, type_: str | None) -> list[dict]:
    """The newest matching rows across the fallback and the legacy file."""
    out: list[dict] = []
    for path in (_pending_path(), _path()):
        for e in reversed(_file_entries(path)):
            if module and e.get("module") != module:
                continue
            if type_ and e.get("type") != type_:
                continue
            out.append(e)
            if len(out) >= limit:
                return out
    return out


def read(limit: int = 300, module: str | None = None,
         type_: str | None = None) -> list[dict]:
    """The newest entries, narrowed to one module and/or one action.

    `type_` mirrors `tail()`'s, and it is what lets a figure elsewhere in the
    Hub link to *the rows it counted* rather than to everything one module has
    ever written -- a count that opens a wider list than it counted is the
    "Showing 1 of 7" answer, one page over.

    `read()` and `tail()` are one function now. They were two because the file
    backend had a cheap way and an expensive one -- load the whole JSONL and
    reverse it, or seek a byte window from the end and guess how many rows
    fitted -- and a query with an `ORDER BY` and a `LIMIT` is neither. Both
    names are kept because ten call sites use one or the other, and which of
    the two somebody reached for was never a decision about the answer.
    """
    limit = max(1, int(limit))
    rows = _db_read(limit, module, type_)
    if rows is None:
        return _file_rows(limit, module, type_)
    pend = _pending_rows(limit, module, type_)
    if pend:
        # In front, not behind. These were written while the table was
        # refusing, so they are newer than everything in it -- and left out
        # altogether an outage reads on /activity as an hour in which nothing
        # happened. The legacy file is deliberately not read here: the import
        # has already put it in the table, and reading both would show every
        # row of the old history twice.
        rows = (pend + rows)[:limit]
    return rows


def tail(limit: int = 300, module: str | None = None,
         type_: str | None = None) -> list[dict]:
    """read(), under the name ten call sites already use. See read()."""
    return read(limit=limit, module=module, type_=type_)


def _pending_rows(limit: int, module: str | None,
                  type_: str | None) -> list[dict]:
    """The fallback file's newest matching rows, newest first.

    Sized first, so the ordinary path -- a database that is answering and a
    fallback file that has never been written -- costs one `stat` and no
    parse at all.
    """
    path = _pending_path()
    try:
        if os.path.getsize(path) <= 0:
            return []
    except OSError:
        return []
    out = []
    for e in reversed(_file_entries(path)):
        if module and e.get("module") != module:
            continue
        if type_ and e.get("type") != type_:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def rotate(max_mb: int = 64, keep: int = 5) -> bool:
    """Keep the log bounded. Called nightly by the maintenance job.

    An append-only store with no ceiling is a slow-motion outage whichever
    layer it sits on: as a file it filled the Render disk that also held
    uploaded assets, and as a table it is storage nobody is watching and an
    `ORDER BY` that gets slower every month.

    So the table is pruned to `MAX_ROWS` by **id**, which is insertion order,
    rather than by age -- a Hub that was quiet for a year would otherwise have
    its whole history deleted on the morning somebody looked at it. The file
    half still rolls, because a deployment with no database is still writing
    one and it is still on a disk with a size.
    """
    pruned = _prune_rows()
    rolled = _roll_file(_path(), max_mb=max_mb, keep=keep)
    rolled = _roll_file(_pending_path(), max_mb=max_mb, keep=keep) or rolled
    return bool(pruned or rolled)


def _prune_rows() -> int:
    if not _init():
        return 0
    try:
        with _engine.begin() as cx:
            keep_from = cx.execute(
                select(_table.c.id).order_by(_table.c.id.desc())
                .offset(MAX_ROWS).limit(1)).scalar()
            if keep_from is None:
                return 0
            res = cx.execute(delete(_table).where(_table.c.id <= keep_from))
        return int(res.rowcount or 0)
    except Exception as exc:                            # noqa: BLE001
        global _init_error
        _init_error = _reason(exc)
        return 0


def _roll_file(path: str, max_mb: int, keep: int) -> bool:
    try:
        if os.path.getsize(path) < max_mb * 1024 * 1024:
            return False
    except OSError:
        return False
    with _lock:
        for i in range(keep - 1, 0, -1):
            older, newer = f"{path}.{i}", f"{path}.{i-1}" if i > 1 else path
            if os.path.exists(newer):
                try:
                    os.replace(newer, older)
                except OSError:
                    pass        # one generation not rolled; the rest still do
        try:
            open(path, "w").close()
        except OSError:
            return False        # nothing rolled, and saying so beats guessing
    return True


def status() -> dict:
    """Which backend answered, and what the fallback is still holding.

    On the panel rather than in a dict nobody opens, for the reason the whole
    move was made: rows written to a file on an instance with no disk of its
    own are rows the next deploy takes with it, and every screen reads exactly
    the same either way.
    """
    pending = 0
    try:
        pending = len(_file_entries(_pending_path()))
    except Exception:                                   # noqa: BLE001
        pending = -1
    return {
        "backend": "database" if _ready else "file",
        "ready": bool(_ready),
        "error": _init_error,
        "pending_rows": pending,
        "pending_path": _pending_path(),
        "import": dict(_import_state),
        "max_rows": MAX_ROWS,
    }


# ---------------------------------------------------------------------------
# v7 additions — module contract, rotation, and read performance.
#
# Two problems this closes:
#
#   1. Every module had copy-pasted the same defensive wrapper:
#          try: from hub import audit as hub_audit
#          except Exception: hub_audit = None
#      ...and ads_builder went further, doing getattr(hub_audit, fn_name) to
#      guess the function name. That defensiveness existed because Scans once
#      called hub_audit.record(), which does not exist, inside a bare
#      `except: pass` — so no scan reached /activity for the module's entire
#      life, silently. A named contract removes the guessing.
#
#   2. read() loaded the whole JSONL into memory and reversed it. Fine at a few
#      thousand rows, not at a few million. It now tails the file.
# ---------------------------------------------------------------------------

_REGISTERED: dict[str, str] = {}


def for_module(name: str, actor_fn=None):
    """Return a logger bound to one module.

        log = audit.for_module("scans", actor_name)
        log("scan_started", domain=domain)

    Registers the module so /health can report anything that never logs.
    """
    _REGISTERED.setdefault(name, "registered")

    def _log(type_: str, **extra) -> None:
        actor = None
        if actor_fn is not None:
            try:
                actor = actor_fn()
            except Exception:               # noqa: BLE001
                actor = None
        log(name, type_, actor=actor, **extra)

    return _log


# A module whose activity is filed under a name that is not its directory's,
# and whose logging therefore lives outside that directory.
#
# The Display Ad Builder is the only one, and it is the only one because it is
# the only module here that is not Python: `modules/ad_builder` is a TypeScript
# renderer, and its Hub-side half — the client join, the proxy, the audit
# entries — is hub/ad_builder_link.py and hub/ad_builder_proxy.py. Everything
# it writes is filed under "display_ads", the name on the tile, on the
# blueprint, on every help key and on every lead it has ever captured.
#
# It is declared rather than renamed. Renaming the log name to match the
# directory would orphan every entry already written and every Client 360 card
# reading them, to make a static check happy about a string.
#
# hub/integrity.py's silent-module check reads this, so a module listed here is
# looked for by the name it actually logs under, anywhere in the tree.
LOG_NAMES: dict[str, str] = {
    "ad_builder": "display_ads",
    # modules/utm_builder logs under `utm`. Unlike the entry above this is not
    # a module written in another language -- it is simply a shorter name
    # somebody chose -- and it went undeclared, so `client_brand.WORK_KINDS`
    # was keyed on the directory name instead and `work_log()` dropped every
    # row the tool wrote. Declared rather than renamed for the same reason as
    # display_ads: the rows already on disk carry `utm`, and renaming the call
    # site to match a table would orphan all of them to make a string tidy.
    "utm_builder": "utm",
}

# A module that deliberately writes no activity row, and why.
#
# This exists because of the way the silent-module check used to be satisfied.
# It asked whether the *string* "for_module(" appeared in a module's source --
# so seven modules that imported a logger, bound it to a name, wrapped it in a
# no-op fallback and then called it nowhere all read as modules that log.
# Every one of them had a comment above the import saying why logging mattered
# there. pdf_optimizer's said "work that isn't logged is work nobody can point
# to later"; page_image_optimizer's and sites_admin's said "an unattributable
# change to a client's account is one nobody can explain later". All three
# were true, and none of them wrote a row. That is the declared-but-unwired
# integration point this codebase has now found in RECORD_HOOK, io_creative,
# manifest(), thumb_url(), mark_pushed() and check_limits() -- wearing the
# activity log.
#
# The check reads a CALL now, through the AST, so an import can no longer
# silence it. Which leaves the honest remainder: a module whose work genuinely
# does not belong in the activity log. That is a decision, so it is written
# down here with its reason rather than left as a dangling import that
# happens to keep a check quiet -- and integrity.py fails on an entry naming a
# module that no longer exists, the rule check_stale_json_exemptions() works
# to, because an exemption that outlives what it exempted goes on covering
# whatever is written at that path next.
NO_ACTIVITY: dict[str, str] = {
    "calculators": (
        "What this module produces is a LEAD, not client work: a stranger on "
        "somebody else's website types into a public estimate box. Those go "
        "through hub/leads.py, which is the one store, delivery and panel for "
        "a prospect -- the rule modules/scans/leads.py gives at length. An "
        "activity row per public estimate would file hundreds of strangers "
        "into a log whose whole purpose is what we did for a CLIENT, and "
        "would put a prospect on a client 360 record they belong to no part "
        "of. The staff-facing internal calculator deliberately stores nothing "
        "at all, so there is nothing there to attribute either."
    ),
    "marketing_audit": (
        "The calculators shape, one Node process over: what this module "
        "produces is a LEAD, not client work -- an accounting or bookkeeping "
        "partner with no Hub account fills in the audit and the result goes "
        "through hub/leads.py, the one store, delivery and panel for a "
        "prospect. It is also not Python (modules/marketing_audit, a second "
        "Express process proxied whole by hub/marketing_audit_proxy.py), so "
        "there is no call site here to make: the lead is what hub/leads.py's "
        "own capture() already logs under 'leads', and there is no staff "
        "action anywhere in this module to attribute a second row to."
    ),
    "hf_render_service": (
        "Not the ad_builder shape -- this is not a renderer proxied through "
        "to a browser with its own client-facing routes. It is a headless "
        "render backend (modules/hf_render_service, Node/Puppeteer/ffmpeg) "
        "reached only server-to-server, over loopback, by hub/hyperframes.py "
        "-- it never sees a client name, a request, or anything to attribute "
        "a row to. The client-facing half is entirely "
        "modules/hyperframes_tools, which already logs every kept render "
        "under 'paint_animation'/'vox_explainer' the moment a file actually "
        "reaches a client's own gallery -- the LOG_NAMES dict inside that "
        "module's app.py, read by _record()."
    ),
}


def registered_modules() -> list[str]:
    return sorted(_REGISTERED)


# How far back the module list looks. A window rather than the whole log,
# because that file reaches millions of rows and this answers a dropdown.
KNOWN_MODULES_WINDOW = 4000


def known_modules(window: int = KNOWN_MODULES_WINDOW) -> dict:
    """The names `/activity` can be narrowed to, and where each one came from.

    The activity page offered a hand-typed three-entry list -- All, `hub`,
    `suite` -- on a Hub where dozens of modules log. So most of the log could
    not be filtered to at all, and `?module=ads_builder` had nothing to select
    even once the page learned to read it.

    Two sources, unioned, because either alone is wrong:

    **Declared** is `_REGISTERED` (every module that bound a logger through
    `for_module()`), the `LOG_NAMES` aliases, and `client_brand`'s two tables.
    A module that has not logged yet must still be offerable, or a quiet
    module reads as one that does not exist.

    Those two tables are the load-bearing half. `_REGISTERED` only holds
    modules that bound a logger *in this worker*, and several of the busiest
    write through a direct `log("name", ...)` instead -- `ads_builder` is one,
    which is exactly the module the dashboard card links here for, and it was
    therefore offerable only once it had already logged. `WORK_KINDS` and
    `NOT_WORK` are keyed on the name each module **actually logs under**
    (`display_ads`, `utm`, `ads_builder`) rather than on its directory, and
    `/api/integrity`'s `check_work_kinds()` fails on a call site in neither --
    so it is the one list in this Hub already held true against the call
    sites, which makes it the honest thing to read rather than a fourth copy.
    Imported inside the function because `client_brand` imports this module,
    and guarded because a dropdown must not be what breaks the log.

    **Observed** is what the recent log actually carries, which is the half
    that catches a module logging through a direct `log("name", ...)` rather
    than through a bound logger -- `hub` itself does exactly that, which is why
    it was one of the three somebody typed in.

    A name is returned with `seen: False` rather than dropped when it is
    declared and not in the window: *nothing has been filed under this yet* and
    *this is not a module* are different answers, and only the first is worth
    offering. And `window_measured` says whether the log could be read at all,
    because a file that would not open must not come back as a Hub where only
    the declared modules exist.
    """
    declared = set(_REGISTERED) | set(LOG_NAMES.values())
    try:
        from hub import client_brand
        declared |= set(client_brand.WORK_KINDS) | set(client_brand.NOT_WORK)
    except Exception:                                    # noqa: BLE001
        pass
    observed: set[str] = set()
    measured = True
    try:
        for entry in tail(limit=max(1, int(window))):
            name = str(entry.get("module") or "").strip()
            if name:
                observed.add(name)
    except Exception:                                    # noqa: BLE001
        measured = False

    names = sorted(declared | observed)
    return {
        "modules": [{"name": n,
                     "seen": n in observed,
                     "declared": n in declared} for n in names],
        "window": int(window),
        "window_measured": measured,
    }


def silent_modules(expected: list[str]) -> list[str]:
    """Modules that were expected to log and never have. Boot-time check."""
    seen = {e.get("module") for e in read(limit=5000)}
    return sorted(m for m in expected if m not in seen)


def _route_methods(fn) -> set:
    """The HTTP methods a Flask view is registered for, from its decorators."""
    import ast as _ast
    methods, is_route = set(), False
    for d in fn.decorator_list:
        if not (isinstance(d, _ast.Call) and isinstance(d.func, _ast.Attribute)):
            continue
        if d.func.attr == "route":
            is_route = True
            named = False
            for kw in d.keywords:
                if kw.arg == "methods":
                    methods |= {e.value.upper() for e in kw.value.elts
                                if isinstance(e, _ast.Constant)}
                    named = True
            if not named:
                methods.add("GET")
        elif d.func.attr in ("get", "post", "put", "delete", "patch"):
            is_route = True
            methods.add(d.func.attr.upper())
    return methods if is_route else set()


def write_route_attribution(source: str) -> dict:
    """Which of a module's write routes record who did the work, and which do not.

    ``/api/integrity``'s silent-module check asks whether a module logs **at
    all**, and one call site satisfies it — the same shape as the check that
    read the *string* ``for_module(`` and counted the binding. So a module can
    be loudly attributable about a quarter of its work and pass: Sites Admin
    recorded deleting a client's website and not creating one, and Google
    Finder recorded deploying a tag and not deploying a pixel into the same
    container. This is that question asked one level finer.

    Read through the **AST**, never by matching text: the two modules this was
    written for both name ``_audit`` in comments explaining why it had gone
    uncalled, and a check that matches the explanation reports the fix as the
    defect — the rule ``hub/config.py``'s drift check gives at length.

    Returns ``{"logs": [...], "silent": [...], "declared": {name: reason}}``.
    A module declares the writes that deliberately record nothing in its own
    ``HOUSEKEEPING_ROUTES``, so the remainder is a decision somebody made
    rather than one nobody noticed — and an entry naming a route that is gone,
    or one that has since started logging, is a caller's to reject.
    """
    import ast as _ast
    tree = _ast.parse(source)
    logs, silent, declared = [], [], {}

    # The module's own wrapper, resolved from its **definition** rather than
    # guessed from its name. `_audit` and `log` were hard-coded, and
    # `modules/seo_images` calls its wrapper `_log` — so a module that records
    # seven of its writes read as recording none, which is a check inventing
    # findings rather than missing them, and the fastest way to have one
    # switched off. `check_work_kinds()` had to learn the same lesson: a bare
    # `log()` is a module's own wrapper whose first argument is the event, and
    # counting only a direct `audit.log(...)` dropped four modules entirely.
    #
    # A wrapper is a function in this file that itself reaches the shared
    # logger, however it is spelled — `audit.log(...)`, `hub_audit.log(...)`,
    # or a name bound from `audit.for_module(...)`.
    #
    # And **reaching it through another wrapper is still reaching it**, which
    # is the half the first version left out: it counted a function calling
    # `audit.log(...)` by attribute and stopped, so a module that binds
    # `_cb_log = audit.for_module(...)` and then wraps *that* in a helper had
    # every route calling the helper reported silent. Four routes read that
    # way — the Commercial Builder's `submit_render`, `send_for_review` and
    # `client_decide`, and `image_audit.api_image_attach_many` — every one of
    # them recording its work perfectly well. That is a check inventing
    # findings rather than missing them, the failure the paragraph above
    # already names once, and it is worse than a gap here: a module triaged
    # on this answer would declare a logging route as housekeeping.
    #
    # So the set is closed rather than gathered in one pass. It terminates
    # because a pass that adds nothing stops it, and a function is added at
    # most once — mutual recursion between two helpers settles rather than
    # spinning.
    wrappers = {"_audit", "log"}
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            v = node.value
            if (isinstance(v, _ast.Call) and isinstance(v.func, _ast.Attribute)
                    and v.func.attr == "for_module"):
                for t in node.targets:
                    if isinstance(t, _ast.Name):
                        wrappers.add(t.id)

    def _reaches_logger(fn) -> bool:
        for inner in _ast.walk(fn):
            if not isinstance(inner, _ast.Call):
                continue
            f = inner.func
            if (isinstance(f, _ast.Attribute) and f.attr == "log"
                    and "audit" in getattr(f.value, "id", "").lower()):
                return True
            if isinstance(f, _ast.Name) and f.id in wrappers:
                return True
        return False

    defs = [n for n in _ast.walk(tree)
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))]
    grew = True
    while grew:
        grew = False
        for node in defs:
            if node.name not in wrappers and _reaches_logger(node):
                wrappers.add(node.name)
                grew = True

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign) and any(
                getattr(t, "id", "") == "HOUSEKEEPING_ROUTES" for t in node.targets):
            if isinstance(node.value, _ast.Dict):
                declared = {k.value: v.value
                            for k, v in zip(node.value.keys, node.value.values)
                            if isinstance(k, _ast.Constant)
                            and isinstance(v, _ast.Constant)}

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.FunctionDef):
            continue
        if not (_route_methods(node) & {"POST", "PUT", "DELETE", "PATCH"}):
            continue
        writes_a_row = any(
            isinstance(c, _ast.Call) and (
                (isinstance(c.func, _ast.Name) and c.func.id in wrappers)
                or (isinstance(c.func, _ast.Attribute) and c.func.attr == "log"
                    and "audit" in getattr(c.func.value, "id", "").lower()))
            for c in _ast.walk(node))
        (logs if writes_a_row else silent).append(node.name)

    return {"logs": sorted(logs), "silent": sorted(silent), "declared": declared}
