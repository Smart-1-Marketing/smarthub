"""A `sqlite3`-shaped API over the Hub's shared engine.

## Why this exists

Two modules kept their own SQLite file on the Render disk, and a file is
local to one instance: the two halves of a zero-downtime deploy each keep a
complete-looking database, and which one answers is whichever container the
browser reached.

Moving one is not usually a matter of rewriting its SQL. `modules/smartforecast`
had 115 statements written against `sqlite3` and `modules/google_finder` has a
handful more, all of them perfectly good SQL that Postgres will take -- what
they cannot take is the *driver*: `?` binds, `INSERT OR IGNORE`, `lastrowid`,
`PRAGMA`, and a rowid that autoincrements only for `INTEGER PRIMARY KEY`.

So this is the driver, not a rewrite. A caller opens `connect()` and goes on
writing the SQL it already had.

## It lives in hub/ because the second caller proved it had to

It was written inside `modules/smartforecast/` for that module's move and was
right there. The moment Google Finder's OAuth token store needed the same
thing, the choice was to copy it or to share it -- and a second copy of a
translation layer is the drift `hub/jsonstore.py`, `hub/storage.py` and
`hub/images.py` all exist to stop. `modules/smartforecast/db.py` is still the
name that module imports; it binds its own table list to these functions and
re-exports them, so nothing there changed.

## What it translates

- **`to_named()`** -- `?` to `:p0`, skipping `?` inside string literals,
  escaped quotes and quoted identifiers, because SQLAlchemy's `text()` takes
  named binds only. A parameter-count mismatch is **refused** rather than
  sent.
- **`portable()`** -- `INSERT OR IGNORE` to `ON CONFLICT DO NOTHING`.
- **`autoid()`** -- `BIGSERIAL PRIMARY KEY` or `INTEGER PRIMARY KEY`, so a
  schema carries one placeholder rather than two spellings that drift.
- **`statements()`** -- splits a script on `;` outside literals *and*
  comments. Written the obvious way first, it split on the semicolon inside a
  schema's own `--` comment.
- **`Row`** -- answers to a name and to a position, because `sqlite3.Row` does
  and callers read both ways.
- **PRAGMA** -- `user_version` becomes a row in `schema_migrations`;
  `quick_check` runs a real read and reports `unreadable: <type>` rather than
  a passing constant from the one check whose job is to say the database is
  sound. Any other PRAGMA is refused **by name**, so a new caller gets an
  error rather than a silent no-op.

## The traps it exists to hold, every one found by running it

- Postgres refuses a **forward foreign-key reference**; SQLite resolves them
  lazily. `check_declaration_order()`.
- A generated-id **sequence does not advance on an explicit id**, so a seed or
  an id-preserving import leaves the first row anybody adds raising a
  duplicate key. `fix_sequences()`.
- A **failed** insert still consumes a Postgres sequence value, so a probe
  that tests by inserting answers differently depending on what ran before it.
  `sequence_state()` asks the sequence directly.
- `pg_get_serial_sequence` **raises** on a table that is not there rather than
  answering NULL, so every lookup is guarded by `to_regclass` -- a
  verification that crashes instead of reporting is the one shape it may not
  take.

## Which database, re-read rather than latched

`engine()` re-resolves `DATABASE_URL` on each call, for the reason
`hub/jsonstore._init()` gives at length: a URL that changes after the first
query otherwise reads as applied while every row goes on landing in the
database the first call happened to see. In production it never changes, so
this costs one environment read.

Core rather than `db.session`: half the callers are dispatcher-mounted apps
with their own Flask app and some are scheduler threads with no app context
at all, which is the trap `hub/extensions.py` documents.
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from typing import Iterator


class Row(dict):
    """A `sqlite3.Row` in behaviour: `row["name"]`, `row[0]`, `.keys()`.

    A dict subclass rather than a wrapper, because nearly every call site
    already treats it as a mapping -- the ones that index by position are the
    `SELECT COUNT(*)` reads, which is the small half.
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


def to_named(sql: str) -> tuple[str, list[str]]:
    """`?` to `:p0`, `:p1` ... skipping anything inside a string literal.

    Returns the statement and the bind names in order, so the caller can zip
    them against a positional parameter tuple. A regex over the whole
    statement is what this exists to avoid: `WHERE note = '?'` is a literal
    question mark, and translating it produces a bind nobody supplies and a
    statement that fails at execute time rather than here.
    """
    out: list[str] = []
    names: list[str] = []
    quote = ""
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if ch == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append("'")          # '' is an escaped quote, not an end
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "?":
            name = f"p{len(names)}"
            names.append(name)
            out.append(":" + name)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), names


_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)


def portable(sql: str) -> str:
    """The one rewrite that is vocabulary rather than binds."""
    if _INSERT_OR_IGNORE.search(sql):
        sql = _INSERT_OR_IGNORE.sub("INSERT INTO", sql)
        if "on conflict" not in sql.lower():
            trailing = ""
            body = sql.rstrip()
            while body and body[-1] in ";\n \t":
                trailing = body[-1] + trailing
                body = body[:-1]
            sql = body + " ON CONFLICT DO NOTHING" + trailing
    return sql


def autoid(dialect: str) -> str:
    """What `%%AUTOID%%` means on this database.

    SQLite autoincrements a rowid only for a column declared exactly
    `INTEGER PRIMARY KEY`; against a BIGINT it refuses every insert on a NOT
    NULL id. Postgres needs a sequence, which `INTEGER PRIMARY KEY` does not
    give it.
    """
    return ("BIGSERIAL PRIMARY KEY" if dialect.startswith("postgres")
            else "INTEGER PRIMARY KEY")


def statements(script: str) -> Iterator[str]:
    """Split a DDL script on `;` outside string literals and comments.

    `executescript` is SQLite's; SQLAlchemy runs one statement per call. The
    split tracks quoting for the same reason `to_named` does -- a `;` inside a
    default value would otherwise cut a CREATE TABLE in half, and the half
    that ran would leave a table nobody could see was wrong.

    It tracks **comments** for the same reason, and that one is not
    hypothetical: the SCHEMA's own note about why the table order matters
    contains the sentence "Postgres refuses a foreign key to a table that does
    not exist yet; SQLite resolves them lazily", and the first run of this
    split it into two statements and handed SQLite the second half of an
    English sentence. Found by running it.
    """
    buf: list[str] = []
    quote = ""
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and script.startswith("--", i):
            end = script.find("\n", i)
            end = n if end == -1 else end
            buf.append(script[i:end])
            i = end
            continue
        if ch == "/" and script.startswith("/*", i):
            end = script.find("*/", i + 2)
            end = n if end == -1 else end + 2
            buf.append(script[i:end])
            i = end
            continue
        if ch == ";":
            part = "".join(buf).strip()
            if part:
                yield part
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        yield tail


class Cursor:
    """What `execute()` hands back, shaped like sqlite3's.

    `lastrowid` is deliberately not populated by guessing. Postgres has no
    such thing, and the id comes back from a `RETURNING` clause the caller
    asked for; reading it without one raises rather than answering None,
    because a None would be written into a foreign key and the row would
    point at nothing.
    """

    def __init__(self, rows: list[Row], rowcount: int, lastrowid=None,
                 has_returning: bool = False):
        self._rows = rows
        self.rowcount = rowcount
        self._lastrowid = lastrowid
        self._has_returning = has_returning

    @property
    def lastrowid(self):
        if not self._has_returning:
            raise RuntimeError(
                "lastrowid needs a RETURNING clause on this backend -- add "
                "'RETURNING id' to the INSERT")
        return self._lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


_PRAGMA = re.compile(r"^\s*PRAGMA\s+(\w+)\s*(?:=\s*(\S+))?\s*$", re.I)


class Connection:
    """A `sqlite3.Connection` in behaviour, over a SQLAlchemy connection."""

    def __init__(self, sa_conn, dialect: str):
        self._c = sa_conn
        self.dialect = dialect

    # -- the schema ledger, which is what PRAGMA user_version becomes --------
    def _ledger_get(self) -> int:
        from sqlalchemy import text as _text
        try:
            row = self._c.execute(_text(
                "SELECT MAX(version) FROM schema_migrations")).fetchone()
        except Exception:                                   # noqa: BLE001
            return 0            # before the table exists, nothing is applied
        return int((row[0] if row else 0) or 0)

    def _ledger_set(self, version: int) -> None:
        from sqlalchemy import text as _text
        self._c.execute(
            _text("INSERT INTO schema_migrations(version,applied_at,description) "
                  "VALUES(:v,:a,:d) ON CONFLICT DO NOTHING"),
            {"v": int(version), "a": _now(), "d": "schema version"})

    def _pragma(self, name: str, value: str | None) -> Cursor:
        name = name.lower()
        if name == "foreign_keys":
            # Always on in Postgres. On SQLite it is set when the connection
            # is opened, which is where a session setting belongs.
            return Cursor([], 0)
        if name == "user_version":
            if value is None:
                return Cursor([Row({"user_version": self._ledger_get()})], 1)
            self._ledger_set(int(value))
            return Cursor([], 1)
        if name == "quick_check":
            # No Postgres equivalent, so it answers the question it was being
            # asked -- can this database be read -- rather than a passing
            # constant, which on the one check whose job is to say the
            # database is sound would be the worst possible lie.
            from sqlalchemy import text as _text
            try:
                self._c.execute(_text("SELECT 1")).fetchone()
                return Cursor([Row({"quick_check": "ok"})], 1)
            except Exception as exc:                        # noqa: BLE001
                return Cursor([Row({"quick_check":
                                    f"unreadable: {type(exc).__name__}"})], 1)
        raise RuntimeError(f"PRAGMA {name} has no portable meaning here")

    def execute(self, sql: str, params: tuple | list = ()) -> Cursor:
        from sqlalchemy import text as _text
        m = _PRAGMA.match(sql)
        if m:
            return self._pragma(m.group(1), m.group(2))
        stmt = portable(sql)
        named, binds = to_named(stmt)
        params = tuple(params or ())
        if len(binds) != len(params):
            raise RuntimeError(
                f"{len(binds)} placeholder(s) and {len(params)} parameter(s)")
        res = self._c.execute(_text(named), dict(zip(binds, params)))
        has_returning = " returning " in f" {stmt.lower()} "
        rows: list[Row] = []
        last = None
        if res.returns_rows:
            rows = [Row(r) for r in res.mappings().all()]
            if has_returning and rows:
                last = rows[0][0]
        return Cursor(rows, res.rowcount if res.rowcount is not None else -1,
                      last, has_returning)

    def executescript(self, script: str) -> None:
        for part in statements(script):
            self.execute(part)

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Which database, and the move onto it
# ---------------------------------------------------------------------------

_engine_lock = threading.Lock()
_engine = None
_engine_dialect = ""
_engine_error = ""
_engine_url = ""

# Parents before children. Postgres refuses a foreign key to a table that does
# not exist yet; SQLite resolves them lazily, which is why the module's own
# SCHEMA could declare `engagement_events` four tables before the
# `embed_tokens` it references and be correct for the life of the module.
# `check_declaration_order()` holds the SCHEMA to this, because the next table
# somebody adds could reintroduce it just as invisibly.



def engine():
    """The shared Hub engine, or None with the reason on `engine_error()`.

    Core rather than the session, for the reason `hub/jsonstore.py` gives:
    half the callers here are a dispatcher-mounted app with its own Flask app
    and the scheduler has none at all, and `db.session` needs the context of
    the app `db` was init_app-ed against.

    Never raises. A database that will not answer is a reason to fall back to
    the file, not a reason to take the module down with it.
    """
    global _engine, _engine_dialect, _engine_error, _engine_url
    with _engine_lock:
        # Which database this module is on is a *setting*, so it is re-read
        # rather than latched on the first call -- `hub/jsonstore._init()`
        # gives the reason at length, and the same thing is true here: a
        # DATABASE_URL that changes after the first query was otherwise read
        # as applied while every row went on landing in the database the
        # first call happened to see. In production it never changes, so this
        # costs one environment read.
        try:
            from hub import extensions
            wanted = extensions.database_url() or ""
        except Exception:                                   # noqa: BLE001
            wanted = ""                     # "" means do not re-resolve
        if _engine is not None and (not wanted or wanted == _engine_url):
            return _engine
        try:
            from hub import extensions
            _engine = extensions.engine_for()
            _engine_dialect = _engine.dialect.name
            _engine_url = wanted
            _engine_error = ""
        except Exception as exc:                            # noqa: BLE001
            _engine = None
            _engine_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        return _engine


def engine_error() -> str:
    return _engine_error


def dialect() -> str:
    engine()
    return _engine_dialect


def _reset_engine_for_tests() -> None:
    global _engine, _engine_dialect, _engine_error, _engine_url
    with _engine_lock:
        _engine = None
        _engine_dialect = ""
        _engine_error = ""
        _engine_url = ""


@contextmanager
def connect():
    """A connection shaped like sqlite3's, committed on a clean exit."""
    eng = engine()
    if eng is None:
        raise RuntimeError(_engine_error or "no database")
    raw = eng.connect()
    if eng.dialect.name.startswith("sqlite"):
        from sqlalchemy import text as _text
        raw.execute(_text("PRAGMA foreign_keys = ON"))
    con = Connection(raw, eng.dialect.name)
    try:
        yield con
        con.commit()
    finally:
        raw.close()


def in_managed_backup() -> bool:
    """Are these rows already inside a backup somebody else is taking?

    Postgres on Render is managed and backed up; the SQLite fallback is a file
    on the same 5 GB disk this module was trying to survive the loss of, which
    is `hub/jsonstore.status()`'s `same_disk` finding one store over -- a
    backup that shares the disk it protects is not a backup.

    So this decides whether `backup()`'s SQL dump still earns its keep. It is
    the dialect rather than a setting, because what makes the difference is
    where the bytes physically are.
    """
    return dialect().startswith("postgres")


def sqlite_dump(con: Connection) -> str:
    """The SQL dump, for the fallback where it still means something.

    `iterdump()` belongs to a `sqlite3.Connection`, so this reaches the DBAPI
    connection underneath the SQLAlchemy one rather than reimplementing it --
    a hand-rolled dump would be a second description of the schema, and the
    one thing worse than no backup is one that restores something else.
    """
    raw = getattr(con._c, "connection", None)
    driver = getattr(raw, "driver_connection", None) or getattr(raw, "dbapi_connection", None) or raw
    if not hasattr(driver, "iterdump"):
        raise RuntimeError("this connection cannot be dumped")
    return "\n".join(driver.iterdump())


def database_bytes(con: "Connection", tables) -> int | None:
    """How many bytes this module's rows occupy, or None if that cannot be read.

    On the SQLite fallback this is the file, which is what the health screen
    used to read off `store.path`. On the Hub database there is no per-module
    file: the cluster holds every module's tables, and `store.path` names a
    legacy SQLite file that is either absent or frozen at whatever it held on
    import day -- so reading it there reports a confident 0, or a stale size,
    for a database that is neither empty nor that size. The number here is
    the total size of this module's own tables instead, which is the question
    the screen was asking.

    `None` where even that cannot be read, so the caller says "not measured"
    rather than printing a 0 it did not measure. The size is one row on a
    support screen; it may not be the reason the whole screen 503s.

    A failed statement leaves a Postgres transaction aborted and every
    later one on that connection refused, so this rolls back before it
    returns -- and the caller gives it a connection of its own, because
    swallowing the error is only safe if it costs the caller's reads
    nothing.
    """
    from sqlalchemy import text as _text
    try:
        if con.dialect.startswith("postgres"):
            total = 0
            for table in tables:
                # to_regclass first: pg_total_relation_size RAISES on a table
                # that is not there, the same trap as in sequence_state().
                size = con._c.execute(_text(
                    "SELECT CASE WHEN to_regclass(:t) IS NULL THEN NULL"
                    " ELSE pg_total_relation_size(to_regclass(:t)) END"
                ), {"t": table}).scalar()
                total += int(size or 0)
            return total
        size = con._c.execute(_text(
            "SELECT (SELECT * FROM pragma_page_count())"
            " * (SELECT * FROM pragma_page_size())")).scalar()
        return int(size) if size is not None else None
    except Exception:                                       # noqa: BLE001
        try:
            con._c.rollback()
        except Exception:                                   # noqa: BLE001
            pass
        return None


def bytes_scope(con: Connection) -> str:
    """What `database_bytes()` just measured, named on the screen.

    The number's meaning changes with the backend, and a size whose scope a
    reader has to infer is how "the database shrank" gets reported as an
    incident.
    """
    return "module_tables" if con.dialect.startswith("postgres") else "database_file"


def drop_all_for_tests(tables) -> None:
    """Drop this module's tables. A test harness only.

    A test file gets a clean start by pinning its own database, which on
    SQLite is a fresh file per test and on a shared Postgres is not -- so on
    Postgres something has to empty it, or every check after the first reads
    the one before it. `_reports_testdb.reset()` is the same arrangement one
    module over.

    Children before parents, which is `tables` reversed, and CASCADE on
    Postgres so an order that is right today cannot fail on a foreign key
    somebody adds tomorrow.
    """
    from sqlalchemy import text as _text
    eng = engine()
    if eng is None:
        return
    pg = eng.dialect.name.startswith("postgres")
    with eng.connect() as raw:
        for table in reversed(tables):
            raw.execute(_text(f"DROP TABLE IF EXISTS {table}"
                              + (" CASCADE" if pg else "")))
        raw.commit()


def check_declaration_order(schema: str) -> list[tuple[str, str]]:
    """Tables declared before something they reference. Empty is the rule.

    Reported rather than sorted at execute time: reordering silently would
    make the SCHEMA and what actually runs two different things, which is the
    drift this repo names a dozen of. One block moves, and this says so if
    anybody moves it back.
    """
    # `IF NOT EXISTS` is optional in the pattern: the SCHEMA uses it on every
    # table today, and a check that only matches the spelling in front of it
    # reports a clean bill of health about a table declared the other way --
    # which is the shape of every silently-stopped sweep in this repo.
    blocks = re.findall(
        r"(CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((?:[^;])*?\);)",
        schema, re.S)
    bad: list[tuple[str, str]] = []
    seen: set[str] = set()
    for body, name in blocks:
        for ref in re.findall(r"REFERENCES\s+(\w+)\s*\(", body):
            if ref != name and ref not in seen:
                bad.append((name, ref))
        seen.add(name)
    return bad


def fix_sequences(con: "Connection", generated_id) -> dict:
    """Put every generated-id sequence past the ids that were imported.

    A `BIGSERIAL` sequence does not advance when a row is inserted with an
    explicit id, so after an id-preserving copy it still sits at 1 and the
    FIRST row anybody creates raises a duplicate key. Measured, not reasoned
    about. A no-op on SQLite, which has no sequence.

    Returns what each table was set to, so the verification can say it
    happened rather than assume it.
    """
    from sqlalchemy import text as _text
    if not con.dialect.startswith("postgres"):
        return {}
    out: dict[str, int] = {}
    for table in generated_id:
        row = con._c.execute(_text(
            f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")).fetchone()
        out[table] = int(row[0]) if row else 0
    return out


def sequence_state(con: "Connection", generated_id) -> dict:
    """What each generated-id sequence would hand out next, against MAX(id).

    Asked directly rather than by trying an insert and reading the error. An
    `INSERT ... DEFAULT VALUES` probe cannot tell a sequence collision from a
    NOT NULL column with no default -- it raises either way -- so it answers
    "could not tell" far more often than it answers the question, which is
    the confident-looking check this repo keeps having to undo.

    Empty on SQLite, which has no sequence and no such failure.
    """
    from sqlalchemy import text as _text
    if not con.dialect.startswith("postgres"):
        return {}
    out: dict[str, dict] = {}
    for table in generated_id:
        # to_regclass first, because pg_get_serial_sequence RAISES on a table
        # that is not there rather than answering NULL -- and a verification
        # that crashes instead of reporting is the one shape it may not take.
        if con._c.execute(_text(f"SELECT to_regclass('{table}')")).scalar() is None:
            out[table] = {"max_id": 0, "next": 0, "ok": False,
                          "missing": True}
            continue
        seq = con._c.execute(_text(
            f"SELECT pg_get_serial_sequence('{table}', 'id')")).scalar()
        if not seq:                         # no sequence on this table
            continue
        max_id = int(con._c.execute(_text(
            f"SELECT COALESCE(MAX(id), 0) FROM {table}")).scalar() or 0)
        last, called = con._c.execute(_text(
            f"SELECT last_value, is_called FROM {seq}")).fetchone()
        nxt = int(last) + 1 if called else int(last)
        out[table] = {"max_id": max_id, "next": nxt, "ok": nxt > max_id}
    return out


def verify(con: "Connection", counts: dict, generated_id) -> dict:
    """Did the move actually work? Two questions, because one is not enough.

    **Row counts per table**, which says the copy was complete. And **every
    generated-id sequence is past the ids that were copied**, which says the
    database is usable -- the sequence trap passes the count check with flying
    colours and then fails on the first row anybody creates. The second half
    is the one that would otherwise be found by a customer.
    """
    mismatched = {}
    for table, expected in (counts or {}).items():
        got = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if got != int(expected):
            mismatched[table] = {"expected": int(expected), "got": got}
    seqs = sequence_state(con, generated_id)
    behind = sorted(t for t, v in seqs.items() if not v["ok"])
    return {"ok": not mismatched and not behind,
            "tables": len(counts or {}),
            "mismatched": mismatched,
            "sequences_checked": len(seqs),
            "sequences_behind": behind}
