"""SmartForecast's move off its own SQLite file, and the traps on the way.

`modules/smartforecast/store.py` kept a SQLite database on the Render disk.
It already had half a recovery story -- `backup()` dumps it and mirrors the
dump through `hub/jsonstore.py` -- and that half was never the problem. The
other half is that **a file is local to one instance**: the two halves of a
zero-downtime deploy keep two SmartForecast databases, each complete-looking,
and which one answers is which container the browser reached. A site paused on
one is live on the other.

The rows go through the shared engine now, and `modules/smartforecast/db.py`
is where the SQL stays portable so the module's 115 statements did not have to
be rewritten.

## The three things this file exists for

Every one was found by **running** the module against Postgres, not by reading
it, and every one is invisible on SQLite by construction:

  1. **Postgres refuses a forward foreign-key reference.** `engagement_events`
     was declared four tables ahead of the `embed_tokens` it references, which
     SQLite resolves lazily and has accepted for the life of the module.
  2. **A generated-id sequence does not advance on an explicit id**, so the
     demo seed -- which writes `clients(id,...) VALUES(1,...)` six times --
     left a fresh deployment seeding perfectly and raising a duplicate key the
     first time anybody added a client.
  3. **A SELECT alias is not visible in HAVING on Postgres.** `due_sites()`
     read `HAVING latest_expiry ...` and took the whole weather refresh with
     it.

`test_smartforecast.py` covers what the module does; this covers what the
database does underneath it, and CI runs both against SQLite and again against
Postgres, because a backend no test exercises is a backend nobody has checked.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-sfstore-")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = (os.environ.get("SMARTFORECAST_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
os.environ["SMARTFORECAST_DB_PATH"] = os.path.join(TMP, "legacy.sqlite3")
os.environ.setdefault("AUDIT_LOG_PATH", os.path.join(TMP, "audit.jsonl"))
os.environ.setdefault("SECRET_KEY", "fixture-only")

from modules.smartforecast import db  # noqa: E402
from modules.smartforecast import store as sf_store  # noqa: E402

ON_PG = os.environ["DATABASE_URL"].startswith("postgres")


def _engine():
    db._reset_engine_for_tests()
    return db.engine()


class Binds(unittest.TestCase):
    """`?` to a named bind, and the one way it goes wrong."""

    def test_ordinary_placeholders(self):
        self.assertEqual(db.to_named("SELECT * FROM t WHERE a=? AND b=?"),
                         ("SELECT * FROM t WHERE a=:p0 AND b=:p1", ["p0", "p1"]))

    def test_a_question_mark_inside_a_literal_is_not_a_bind(self):
        """`WHERE note = '?'` is a literal, and translating it produces a bind
        nobody supplies -- a statement that then fails at execute time rather
        than here, which is why this scans quoting instead of running a regex.
        """
        sql = "SELECT * FROM t WHERE note = '?'"
        self.assertEqual(db.to_named(sql), (sql, []))

    def test_an_escaped_quote_does_not_end_the_literal(self):
        sql = "INSERT INTO t(a) VALUES('it''s ?')"
        self.assertEqual(db.to_named(sql), (sql, []))

    def test_a_quoted_identifier_is_skipped_too(self):
        got, names = db.to_named('SELECT "col?" FROM t WHERE a=?')
        self.assertEqual(got, 'SELECT "col?" FROM t WHERE a=:p0')
        self.assertEqual(names, ["p0"])

    def test_a_parameter_count_mismatch_is_refused_rather_than_sent(self):
        with self.assertRaises(RuntimeError):
            with db.connect() as con:
                con.execute("SELECT * FROM sites WHERE id=? AND name=?", (1,))


class Statements(unittest.TestCase):
    def test_a_semicolon_inside_a_comment_does_not_split(self):
        """Not hypothetical: the SCHEMA's own note about table order contains
        "...does not exist yet; SQLite resolves them lazily", and the first
        version of the splitter handed the database the second half of an
        English sentence. Found by running it."""
        script = ("-- a note; with a semicolon in it\n"
                  "CREATE TABLE a (id INTEGER);\n"
                  "CREATE TABLE b (id INTEGER);")
        parts = list(db.statements(script))
        self.assertEqual(len(parts), 2, parts)
        self.assertIn("CREATE TABLE a", parts[0])
        self.assertIn("CREATE TABLE b", parts[1])

    def test_a_semicolon_inside_a_literal_does_not_split(self):
        script = "INSERT INTO t(a) VALUES('x;y'); SELECT 1;"
        self.assertEqual(len(list(db.statements(script))), 2)

    def test_a_block_comment_is_skipped(self):
        self.assertEqual(len(list(db.statements("/* one; two */ SELECT 1;"))), 1)


class InsertOrIgnore(unittest.TestCase):
    def test_it_becomes_on_conflict_do_nothing(self):
        got = db.portable("INSERT OR IGNORE INTO t(a) VALUES(?)")
        self.assertNotIn("OR IGNORE", got.upper())
        self.assertIn("ON CONFLICT DO NOTHING", got.upper())

    def test_a_statement_that_already_says_so_is_not_doubled(self):
        got = db.portable("INSERT OR IGNORE INTO t(a) VALUES(?) ON CONFLICT DO NOTHING")
        self.assertEqual(got.upper().count("ON CONFLICT"), 1)

    def test_an_ordinary_insert_is_left_alone(self):
        sql = "INSERT INTO t(a) VALUES(?)"
        self.assertEqual(db.portable(sql), sql)


class TheSchema(unittest.TestCase):
    def test_no_table_references_one_declared_after_it(self):
        """Postgres refuses it; SQLite resolves foreign keys lazily, which is
        why the module shipped with `engagement_events` four tables ahead of
        the `embed_tokens` it references and was correct for years."""
        bad = db.check_declaration_order(sf_store.SCHEMA)
        self.assertEqual(bad, [], f"declared before what they reference: {bad}")

    def test_the_check_bites(self):
        """Confirmed against the defect it was written for, rather than
        trusted because it came back empty."""
        broken = ("CREATE TABLE a (id INTEGER, b_id INTEGER REFERENCES b(id));\n"
                  "CREATE TABLE b (id INTEGER);")
        self.assertEqual(db.check_declaration_order(broken), [("a", "b")])

    def test_every_table_is_named_in_the_order(self):
        """An order that is missing a table would drop it from the reset, the
        sequence fix and the import all at once, silently."""
        declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                                  sf_store.SCHEMA))
        self.assertEqual(declared, set(db.TABLES))

    def test_the_generated_id_placeholder_is_spelled_per_dialect(self):
        self.assertIn("PRIMARY KEY", db.autoid("sqlite"))
        self.assertIn("INTEGER PRIMARY KEY", db.autoid("sqlite"))
        self.assertIn("BIGSERIAL", db.autoid("postgresql"))

    def test_the_schema_carries_no_literal_id_type(self):
        """One schema, spelled per dialect -- not two that drift."""
        self.assertNotIn("id INTEGER PRIMARY KEY", sf_store.SCHEMA)
        self.assertIn("%%AUTOID%%", sf_store.SCHEMA)


class Sequences(unittest.TestCase):
    """The trap that passes every count check and fails on the first write."""

    def test_a_seeded_database_can_take_another_row(self):
        """The demo seed writes `clients(id,...) VALUES(1,...)`, so on
        Postgres the sequence stays at 1 and the next client collides. A fresh
        deployment seeded perfectly and then raised a duplicate key the first
        time anybody added one.
        """
        # From an empty database, deliberately. A failed insert still
        # advances a Postgres sequence, so run after anything that collided
        # this check passes on the broken code -- which is the "catches the
        # defect sometimes" failure this repo names, and it is what the first
        # version of this test did.
        db.drop_all_for_tests()
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            state = db.sequence_state(con)
            behind = sorted(t for t, v in state.items() if not v["ok"])
            self.assertEqual(behind, [],
                             "the seed writes explicit ids, so these sequences "
                             "would collide on the next row")
            cur = con.execute(
                "INSERT INTO clients(name,industry,business_goals_json,created_at) "
                "VALUES(?,?,?,?) RETURNING id",
                ("A Second Client", "hvac", "[]", "2026-01-01T00:00:00Z"))
            self.assertTrue(cur.lastrowid)

    def test_the_verification_says_when_a_sequence_is_behind(self):
        if not ON_PG:
            self.skipTest("SQLite has no sequence and no such failure; the "
                          "Postgres run is what asserts this")
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            # put one back by hand, as an id-preserving copy leaves it
            from sqlalchemy import text as _text
            con._c.execute(_text(
                "SELECT setval(pg_get_serial_sequence('clients','id'), 1, false)"))
            report = db.verify(con, {})
            self.assertFalse(report["ok"])
            self.assertIn("clients", report["sequences_behind"])
            db.fix_sequences(con)
            self.assertTrue(db.verify(con, {})["ok"])

    def test_sqlite_reports_no_sequences_rather_than_a_passing_constant(self):
        if ON_PG:
            self.skipTest("this is the SQLite half")
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            self.assertEqual(db.sequence_state(con), {})
            self.assertEqual(db.verify(con, {})["sequences_checked"], 0)


class Verification(unittest.TestCase):
    def test_row_counts_are_compared_per_table(self):
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            real = int(con.execute("SELECT COUNT(*) FROM clients").fetchone()[0])
            self.assertTrue(db.verify(con, {"clients": real})["ok"])
            bad = db.verify(con, {"clients": real + 99})
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["mismatched"]["clients"]["expected"], real + 99)
            self.assertEqual(bad["mismatched"]["clients"]["got"], real)

    def test_a_missing_table_is_reported_rather_than_raising(self):
        """`pg_get_serial_sequence` raises on a table that is not there rather
        than answering NULL, and a verification that crashes instead of
        reporting is the one shape it may not take."""
        if not ON_PG:
            self.skipTest("the raise is Postgres's")
        sf_store.SmartForecastStore()
        from sqlalchemy import text as _text
        with db.connect() as con:
            con._c.execute(_text("DROP TABLE IF EXISTS engagement_events CASCADE"))
            state = db.sequence_state(con)
            self.assertTrue(state["engagement_events"].get("missing"))
            self.assertFalse(db.verify(con, {})["ok"])


class Backup(unittest.TestCase):
    def test_a_managed_database_takes_no_second_copy(self):
        """A dump beside a backed-up database is a second copy of the truth on
        a different schedule, and `operational_health()` would carry a
        `backup_fresh` that nothing refreshes -- the permanently amber row this
        codebase names as the check people learn to skip."""
        st = sf_store.SmartForecastStore()
        real = db.in_managed_backup
        db.in_managed_backup = lambda: True
        try:
            out = st.backup()
        finally:
            db.in_managed_backup = real
        self.assertTrue(out["ok"])
        self.assertTrue(out["in_database_backup"])
        self.assertNotIn("sql", out)

    def test_the_fallback_still_dumps(self):
        """Where the database is a SQLite file on the very disk this was
        trying to survive the loss of, the dump still earns its keep --
        `hub/jsonstore.status()`'s `same_disk` finding, one store over."""
        if ON_PG:
            self.skipTest("this run is on a managed database")
        st = sf_store.SmartForecastStore()
        out = st.backup()
        self.assertTrue(out["ok"], out)
        self.assertGreater(out["bytes"], 0)

    def test_which_one_is_decided_by_where_the_bytes_are(self):
        self.assertEqual(db.in_managed_backup(), ON_PG)


class WhichDatabaseThisModuleIsOn(unittest.TestCase):
    """A setting, re-read, rather than latched on the first call."""

    def tearDown(self):
        db._reset_engine_for_tests()
        db.engine()

    def test_a_changed_database_url_is_picked_up(self):
        """`hub/jsonstore._init()` gives the reason at length and the same
        thing is true here: a DATABASE_URL that changes after the first query
        was otherwise read as applied while every row went on landing in the
        database the first call happened to see. The remembered URL is what
        notices -- it looks unread, because the only read is in the function
        that also assigns it."""
        first = db.engine()
        self.assertIsNotNone(first)
        real = os.environ["DATABASE_URL"]
        other = "sqlite:///" + os.path.join(TMP, "somewhere-else.sqlite3")
        try:
            os.environ["DATABASE_URL"] = other
            self.assertIsNot(db.engine(), first)
            self.assertEqual(db.dialect(), "sqlite")
        finally:
            os.environ["DATABASE_URL"] = real

    def test_an_unchanged_url_reuses_the_engine(self):
        """The re-read costs one environment read, not a new pool."""
        first = db.engine()
        self.assertIs(db.engine(), first)


class TheModulesOwnInterface(unittest.TestCase):
    """`modules/smartforecast/db` re-exports the shared driver's names.

    The translation layer is `hub/dbshim.py` now, and this module binds its
    own table list to it. Everything a caller reached for through `db.` still
    has to be there, or the move was not the no-op it claims to be.
    """

    def test_every_name_it_declares_exists(self):
        missing = [n for n in db.__all__ if not hasattr(db, n)]
        self.assertEqual(missing, [], missing)

    def test_every_name_a_caller_uses_is_declared(self):
        """A sweep of what the module and its tests actually reach for, so the
        declared interface cannot drift below what is in use."""
        import ast
        import pathlib
        base = pathlib.Path(REPO)
        used = set()
        for path in (base / "modules" / "smartforecast" / "store.py",
                     base / "test_smartforecast_store.py",
                     base / "test_smartforecast.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and \
                        isinstance(node.value, ast.Name) and \
                        node.value.id in ("db", "sfdb") and \
                        not node.attr.startswith("_"):
                    used.add(node.attr)
        undeclared = sorted(used - set(db.__all__))
        self.assertEqual(undeclared, [], undeclared)

    def test_it_declares_nothing_no_caller_wants(self):
        """The other direction. `Cursor` and `engine_error` were re-exported
        with no caller anywhere -- a re-export nothing imports is dead weight
        wearing the word "API"."""
        for gone in ("Cursor", "engine_error"):
            self.assertNotIn(gone, db.__all__)


class OneDatabaseForEveryModule(unittest.TestCase):
    """The names are shared now, and two of them are the obvious ones.

    Every other module in this repo prefixes its tables -- `ads_`, `cb_`,
    `hub_`. SmartForecast's predate the shared engine and do not: `clients`,
    `sites`, `locations` and `schema_migrations` are exactly the names a
    second module would reach for. Nothing collides today, and this is how
    that stays true: a collision in one database is not an error, it is two
    modules reading each other's rows.
    """

    #: The names SmartForecast holds in the shared database.
    OWNED = set(db.TABLES)

    def test_no_other_module_declares_one_of_these_names(self):
        import ast
        import pathlib
        base = pathlib.Path(REPO)
        mine = base / "modules" / "smartforecast"
        offenders = []
        for path in sorted(list((base / "hub").rglob("*.py"))
                           + list((base / "modules").rglob("*.py"))):
            if mine in path.parents:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "__tablename__" not in names:
                    continue
                if isinstance(node.value, ast.Constant) and \
                        node.value.value in self.OWNED:
                    offenders.append(
                        f"{path.relative_to(base)}: {node.value.value}")
        self.assertEqual(offenders, [], offenders)

    def test_the_check_names_every_table_it_is_guarding(self):
        """An exemption list held stale in either direction is the failure
        mode; this one is `db.TABLES`, which the schema is already held to."""
        self.assertEqual(self.OWNED, set(db.TABLES))
        self.assertIn("clients", self.OWNED)
        self.assertIn("sites", self.OWNED)


class TheHealthScreen(unittest.TestCase):
    """`operational_health()` after the file stopped being the database.

    Two of its rows were reading the file directly, and neither one said so.
    On the Hub database `store.path` names a legacy SQLite file that is
    either absent or frozen at import day, so the size row reported a
    confident 0 for a database holding rows, and `backup_fresh` reported
    stale for rows Render backs up nightly.
    """

    def setUp(self):
        db.drop_all_for_tests()
        self.store = sf_store.SmartForecastStore()

    def test_the_size_is_measured_on_whichever_backend_is_answering(self):
        """A seeded database is not zero bytes on either one."""
        out = self.store.operational_health()
        self.assertTrue(out["database_bytes_measured"], out)
        self.assertGreater(out["database_bytes"], 0, out)

    def test_the_size_says_what_it_measured(self):
        """The number's meaning changes with the backend -- a file on SQLite,
        this module's own tables on a cluster that holds every module's. A
        size whose scope a reader has to infer is how "the database shrank"
        gets reported as an incident."""
        out = self.store.operational_health()
        self.assertEqual(out["database_bytes_scope"],
                         "module_tables" if ON_PG else "database_file")

    def test_the_size_is_not_read_off_the_legacy_file(self):
        """The specific defect: `self.path.stat().st_size if exists else 0`.
        On a managed database that path is the legacy SQLite file, so the row
        answered with whatever the import left behind -- here, nothing."""
        if not ON_PG:
            self.skipTest("the file IS the database on the fallback")
        legacy = self.store.path
        self.assertFalse(legacy.exists() and legacy.stat().st_size > 0,
                         "fixture assumption: no legacy file on this run")
        self.assertGreater(self.store.operational_health()["database_bytes"], 0)

    def test_a_size_that_cannot_be_read_is_not_a_zero(self):
        """`measured: False` rather than a 0 nobody measured -- the rule this
        repo has written down since the first check that reported a clean bill
        about a thing it never reached."""
        with self.store.connect() as con:
            real = con.dialect
            # Point it at the other backend's query, which this one cannot
            # answer -- a real unreadable size, not a mocked return value.
            try:
                con.dialect = "sqlite" if ON_PG else "postgresql"
                self.assertIsNone(db.database_bytes(con))
            finally:
                con.dialect = real
            # And the connection still answers. A failed statement leaves a
            # Postgres transaction aborted and refuses every later one on it,
            # so a swallowed size error would take the row counts with it --
            # the whole screen reporting one row's problem.
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM sites").fetchone()[0],
                1)

    def test_the_size_is_measured_on_a_connection_of_its_own(self):
        """Swallowing that error is only safe if it costs the caller nothing,
        and the counts above it are the caller."""
        import ast
        with open(os.path.join(REPO, "modules", "smartforecast", "store.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "operational_health")
        withs = [n for n in fn.body if isinstance(n, ast.With)]
        self.assertEqual(len(withs), 2, ast.unparse(fn))
        self.assertIn("database_bytes", ast.unparse(withs[1]))
        self.assertNotIn("database_bytes", ast.unparse(withs[0]))

    def test_a_managed_database_is_backed_up_by_the_database(self):
        """`backup()` deliberately takes no dump there, so an age read off one
        is missing forever. Reporting that as `backup_fresh: False` is the
        permanently amber row the dump was dropped to avoid."""
        if not ON_PG:
            self.skipTest("this run is on the SQLite fallback")
        out = self.store.operational_health()
        self.assertTrue(out["backup_fresh"], out)
        self.assertEqual(out["backup_by"], "hub_database")

    def test_the_fallback_still_reports_the_dump_age(self):
        """Where the dump is the backup, its age is still the question."""
        if ON_PG:
            self.skipTest("this run is on a managed database")
        self.assertEqual(self.store.operational_health()["backup_by"], "dump")
        self.store.backup()
        out = self.store.operational_health()
        self.assertTrue(out["backup_fresh"], out)
        self.assertIsNotNone(out["backup_created_at"])

    def test_nothing_on_the_screen_stats_the_legacy_path(self):
        """A sweep, because the row came back the last time by hand."""
        import ast
        with open(os.path.join(REPO, "modules", "smartforecast", "store.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "operational_health")
        offenders = [ast.unparse(n) for n in ast.walk(fn)
                     if isinstance(n, ast.Attribute)
                     and n.attr in {"stat", "exists", "st_size"}]
        self.assertEqual(offenders, [], offenders)


class RowShape(unittest.TestCase):
    def test_a_row_answers_to_a_name_and_to_a_position(self):
        """`sqlite3.Row` does both, and the module's 115 statements use both --
        the `SELECT COUNT(*)` reads index by position."""
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            row = con.execute(
                "SELECT COUNT(*) total, MIN(id) first FROM clients").fetchone()
            self.assertEqual(row["total"], row[0])
            self.assertEqual(row["first"], row[1])
            self.assertIn("total", row.keys())

    def test_lastrowid_without_returning_refuses_rather_than_answering_none(self):
        """A None would be written into a foreign key and the row would point
        at nothing."""
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            cur = con.execute(
                "INSERT INTO clients(name,industry,business_goals_json,created_at) "
                "VALUES(?,?,?,?)", ("No Returning", "x", "[]", "2026-01-01T00:00:00Z"))
            with self.assertRaises(RuntimeError):
                cur.lastrowid


class Pragmas(unittest.TestCase):
    def test_the_schema_ledger_is_a_row_rather_than_a_sqlite_counter(self):
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            self.assertEqual(int(con.execute("PRAGMA user_version").fetchone()[0]),
                             sf_store.SCHEMA_VERSION)

    def test_quick_check_answers_whether_the_database_can_be_read(self):
        """Rather than a passing constant, which on the one check whose job is
        to say the database is sound would be the worst possible lie."""
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_an_unknown_pragma_is_refused_by_name(self):
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            with self.assertRaises(RuntimeError):
                con.execute("PRAGMA journal_mode")


class WhatTheModuleReadsFor(unittest.TestCase):
    def test_smartforecast_db_path_selects_no_backend(self):
        """It names the legacy file, for the import and the fallback. The rule
        `hub/audit.py` arrived at one store earlier: a variable that is set on
        the live service and also chooses the backend keeps production on the
        disk while every test passes on the new path."""
        self.assertTrue(os.environ.get("SMARTFORECAST_DB_PATH"))
        st = sf_store.SmartForecastStore()
        with st.connect() as con:
            self.assertEqual(con.dialect.startswith("postgres"), ON_PG)

    def test_the_store_carries_no_sqlite_connect_of_its_own(self):
        """A sweep, because a second door onto the file is how half a module
        quietly stays on the old backend. The import is the one caller that
        may open it, and it is named."""
        import ast
        with open(os.path.join(REPO, "modules", "smartforecast", "store.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        allowed = {"_copy_sqlite_into"}
        offenders = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and \
                   getattr(inner.func, "attr", "") == "connect" and \
                   getattr(getattr(inner.func, "value", None), "id", "") == "sqlite3":
                    if node.name not in allowed:
                        offenders.append(f"{node.name}:{inner.lineno}")
        self.assertEqual(offenders, [],
                         f"these still open the legacy file: {offenders}")


if __name__ == "__main__":
    import shutil
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
