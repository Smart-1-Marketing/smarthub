"""Google's OAuth refresh tokens, off the disk and into the Hub database.

`modules/google_finder` kept four tables in a SQLite file on the Render disk.
The disk is outside the database backup and does not survive being recreated,
and one of those tables holds **Google OAuth refresh tokens**: losing the file
means every connected account has to reconnect, with nothing on any screen
saying why. It was also local to one instance, so the two halves of a
zero-downtime deploy each kept their own set.

## The three things this file exists for

  1. **The tokens move as ciphertext.** `refresh_token_enc` is copied column
     to column and never passes through `_fernet()`. Decrypting to re-encrypt
     would put every account's refresh token in the process's memory for no
     purpose, and would fail outright where TOKEN_ENCRYPTION_KEY has been
     rotated -- turning a migration into a mass disconnect.
  2. **Ids are preserved, and the sequences move with them.**
     `report_alerts.report_id` is a foreign key to `saved_reports.id`, so the
     copy keeps the ids -- which on Postgres leaves every sequence at 1 and
     the first report anybody saves colliding. The row counts all match while
     that is true, which is why verification asks both questions.
  3. **The backend is not silently the wrong one.** A store that reads the
     database in a test and the file in production passes everything and is
     wrong the whole time.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-gtokens-")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["TOKEN_DB_PATH"] = os.path.join(TMP, "google_tokens.db")
os.environ["DATABASE_URL"] = (os.environ.get("GOOGLE_TOKENS_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
os.environ.setdefault("SECRET_KEY", "fixture-only")

from cryptography.fernet import Fernet  # noqa: E402

KEY = Fernet.generate_key().decode()
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY

from hub import dbshim  # noqa: E402
from modules.google_finder import app as gf  # noqa: E402

ON_PG = os.environ["DATABASE_URL"].startswith("postgres")
SECRET = b"1//a-real-looking-refresh-token"


def _fresh() -> None:
    """A clean set of tables, no legacy file, and no import marker.

    The marker goes through `jsonstore`, which **mirrors it into the shared
    database** keyed relative to the data root -- so a fresh temp directory
    does not give a fresh marker, and the check after the first would read
    "already imported" about tables that were just dropped. Found the hard
    way while probing this by hand.
    """
    dbshim.drop_all_for_tests(gf.LEGACY_TABLES)
    gf._schema_ready = False
    gf._import_state = {"ran": False}
    try:
        os.remove(gf._token_db_path())
    except OSError:
        pass            # not there is the state this is reaching for
    try:
        from hub import jsonstore
        jsonstore.delete_json(os.path.join(jsonstore.data_dir("google_finder"),
                                           "sqlite-import.json"))
    except Exception:                                   # noqa: BLE001
        pass            # no marker to clear is that state too


def _legacy_file(*, accounts=1, report_id=7) -> str:
    """A SQLite file shaped exactly as the module used to write one."""
    path = gf._token_db_path()
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS google_accounts (email TEXT PRIMARY KEY,
      refresh_token_enc TEXT NOT NULL, status TEXT DEFAULT 'ACTIVE',
      connected_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS saved_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,
      customer_name TEXT NOT NULL, summary_title TEXT NOT NULL,
      property_id TEXT NOT NULL, google_login TEXT NOT NULL,
      report_data TEXT NOT NULL, created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS report_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,
      report_id INTEGER NOT NULL, notification_email TEXT NOT NULL,
      frequency TEXT NOT NULL, ghl_webhook_url TEXT, created_at INTEGER NOT NULL,
      FOREIGN KEY(report_id) REFERENCES saved_reports(id));
    CREATE TABLE IF NOT EXISTS gtm_change_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,
      google_login TEXT NOT NULL, account_id TEXT NOT NULL, container_id TEXT NOT NULL,
      action_type TEXT NOT NULL, tag_name TEXT NOT NULL, details TEXT NOT NULL,
      created_at INTEGER NOT NULL);
    """)
    now = int(time.time())
    enc = Fernet(KEY.encode()).encrypt(SECRET).decode()
    for n in range(accounts):
        con.execute("INSERT INTO google_accounts VALUES (?,?,?,?,?)",
                    (f"acct{n}@smart1.test", enc, "ACTIVE", now, now))
    con.execute("INSERT INTO saved_reports (id,customer_name,summary_title,"
                "property_id,google_login,report_data,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (report_id, "Acme", "Q3", "P1", "acct0@smart1.test", "{}", now))
    con.execute("INSERT INTO report_alerts (id,report_id,notification_email,"
                "frequency,created_at) VALUES (?,?,?,?,?)",
                (3, report_id, "a@b.test", "weekly", now))
    con.commit()
    con.close()
    return path


class Backend(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_the_tables_are_in_the_shared_database(self):
        with gf._db() as con:
            self.assertEqual(con.dialect.startswith("postgres"), ON_PG)

    def test_the_legacy_path_selects_no_backend(self):
        """TOKEN_DB_PATH names the file the import reads. A variable that is
        set on the live service and also chooses the backend keeps production
        on the disk while every test passes on the new path."""
        self.assertTrue(os.environ.get("TOKEN_DB_PATH"))
        with gf._db() as con:
            self.assertEqual(con.dialect.startswith("postgres"), ON_PG)

    def test_the_legacy_path_is_resolved_per_call(self):
        """It was captured at import, so a variable set afterwards read as
        applied and was not."""
        was = os.environ["TOKEN_DB_PATH"]
        try:
            os.environ["TOKEN_DB_PATH"] = "/tmp/somewhere-else.db"
            self.assertEqual(gf._token_db_path(), "/tmp/somewhere-else.db")
        finally:
            os.environ["TOKEN_DB_PATH"] = was

    def test_no_sqlite_file_is_created_by_ordinary_use(self):
        with gf._db() as con:
            con.execute("INSERT INTO google_accounts (email,refresh_token_enc,"
                        "status,connected_at,updated_at) VALUES (?,?,?,?,?)",
                        ("x@y.test", "ENC", "ACTIVE", 1, 1))
        self.assertFalse(os.path.exists(gf._token_db_path()))


class TheImport(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_it_carries_the_accounts_across(self):
        _legacy_file(accounts=2)
        out = gf.import_legacy()
        self.assertTrue(out["ran"], out)
        self.assertEqual(out["counts"]["google_accounts"], 2)

    def test_the_refresh_token_still_decrypts(self):
        """Moved as ciphertext. If the import had decrypted and re-encrypted,
        this would pass and would have put every account's token through this
        process to do it -- and would fail outright on a rotated key."""
        _legacy_file()
        gf.import_legacy()
        accounts, err = gf.connected_accounts_result()
        self.assertEqual(err, "")
        self.assertEqual(accounts[0]["refresh_token"], SECRET.decode())

    def test_the_import_never_decrypts(self):
        """Asserted by breaking the key rather than by reading the source: a
        deployment whose TOKEN_ENCRYPTION_KEY has been rotated must still be
        able to move its rows, because the ciphertext is opaque to the copy."""
        _legacy_file()
        # The ENVIRONMENT now, not a module attribute. This used to set
        # `gf.TOKEN_ENCRYPTION_KEY` and carried a comment explaining why: the
        # module bound that key at import, so setting the environment rotated
        # nothing and this check passed over a copy that decrypted every
        # token. That trap is gone -- `_fernet()` reads hub/keyring.py, which
        # reads the environment every time -- so the mechanism that was
        # correct then is the one that silently stops testing anything now.
        # Found the same way the original was: by breaking the code on purpose
        # and watching this go on passing.
        was = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
        try:
            os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
            self.assertRaises(Exception, gf._fernet().decrypt,
                              Fernet(was.encode()).encrypt(SECRET))
            out = gf.import_legacy()
        finally:
            os.environ["TOKEN_ENCRYPTION_KEY"] = was
        self.assertTrue(out["ran"], out)
        self.assertEqual(out["counts"]["google_accounts"], 1)
        # ...and the rows are readable again once the real key is back.
        accounts, err = gf.connected_accounts_result()
        self.assertEqual(err, "")
        self.assertEqual(accounts[0]["refresh_token"], SECRET.decode())

    def test_ids_are_preserved_so_the_foreign_key_still_points_home(self):
        _legacy_file(report_id=7)
        gf.import_legacy()
        with gf._db() as con:
            self.assertEqual(con.execute(
                "SELECT id FROM saved_reports").fetchone()[0], 7)
            self.assertEqual(con.execute(
                "SELECT report_id FROM report_alerts").fetchone()[0], 7)

    def test_and_the_next_report_does_not_collide_with_them(self):
        """The trap. A generated-id sequence does not advance on an explicit
        id, so after an id-preserving copy the FIRST row anybody creates
        raises a duplicate key -- with the import reporting success and every
        count matching. SQLite has no sequence, so nothing here would ever
        have shown it."""
        _legacy_file(report_id=7)
        gf.import_legacy()
        with gf._db() as con:
            nid = con.execute(
                "INSERT INTO saved_reports (customer_name,summary_title,"
                "property_id,google_login,report_data,created_at) "
                "VALUES (?,?,?,?,?,?) RETURNING id",
                ("Next", "T", "P", "g", "{}", 1)).lastrowid
        self.assertGreater(nid, 7)

    def test_a_sequence_left_behind_fails_verification(self):
        """And counting alone would not have caught it: every row is there."""
        if not ON_PG:
            self.skipTest("SQLite has no sequence to leave behind")
        _legacy_file(report_id=7)
        real = dbshim.fix_sequences
        dbshim.fix_sequences = lambda con, gen: {}
        try:
            out = gf.import_legacy()
        finally:
            dbshim.fix_sequences = real
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "verification failed")
        self.assertEqual(out["verification"]["short"], {})
        self.assertTrue(out["verification"]["sequences_behind"])

    def test_a_failed_verification_does_not_mark_it_done(self):
        """Recording that a set of OAuth tokens nobody checked had been
        carried across is the one outcome worse than not having run."""
        _legacy_file()
        real = gf._verify_legacy
        gf._verify_legacy = lambda counts: {"ok": False, "short": {"x": 1}}
        try:
            self.assertFalse(gf.import_legacy()["ran"])
        finally:
            gf._verify_legacy = real
        self.assertTrue(gf.import_legacy()["ran"])   # the next boot retries

    def test_it_runs_once(self):
        _legacy_file()
        gf.import_legacy()
        self.assertFalse(gf.import_legacy()["ran"])

    def test_a_half_finished_import_can_finish(self):
        """It is retried whenever the last attempt did not verify, so running
        it twice may not be a duplicate-key failure that can never
        complete."""
        _legacy_file(accounts=2)
        with gf._db() as con:
            con.execute("INSERT INTO google_accounts (email,refresh_token_enc,"
                        "status,connected_at,updated_at) VALUES (?,?,?,?,?)",
                        ("acct0@smart1.test", "ENC", "ACTIVE", 1, 1))
        out = gf.import_legacy()
        self.assertTrue(out["ran"], out)

    def test_no_legacy_file_is_not_a_failure(self):
        out = gf.import_legacy()
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "no legacy database")


class SaysSoOnAScreen(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_diagnostics_carries_a_row(self):
        from hub import diagnostics
        self.assertIn(diagnostics.check_google_token_store, diagnostics.CHECKS)

    def test_and_warns_when_the_import_did_not_verify(self):
        from hub import diagnostics
        gf._import_state = {"ran": False, "reason": "verification failed",
                            "verification": {"short": {},
                                             "sequences_behind": ["saved_reports"]}}
        row = diagnostics.check_google_token_store()
        self.assertEqual(row.state, "warn")
        self.assertIn("run again", row.detail)

    def test_the_debug_route_names_the_backend(self):
        self.assertEqual(gf.import_status().get("ran"), False)


class TheSchemaIsCreatedUnderTheLock(unittest.TestCase):
    """`CREATE TABLE IF NOT EXISTS` is not atomic against a second worker.

    On Postgres two workers running it at the same moment is a duplicate key
    on `pg_type_typname_nsp_index` -- a stack trace in the deploy log on every
    single deploy, which is how a deploy log becomes one nobody reads.
    """

    def setUp(self):
        _fresh()

    def test_the_ddl_goes_through_the_advisory_locked_helper(self):
        from hub import extensions
        seen = []
        real = extensions.create_all_sql
        extensions.create_all_sql = lambda run, *a, **kw: (seen.append(1),
                                                           real(run, *a, **kw))[1]
        try:
            gf._schema_ready = False
            with gf._db() as con:
                con.execute("SELECT 1 FROM google_accounts WHERE 1=0")
        finally:
            extensions.create_all_sql = real
        self.assertEqual(seen, [1])

    def test_a_schema_that_will_not_create_is_raised_not_swallowed(self):
        """`create_all_sql` returns the error rather than raising, so a caller
        that ignores the return value gets a module that looks fine and has no
        tables."""
        from hub import extensions
        real = extensions.create_all_sql
        extensions.create_all_sql = lambda run, *a, **kw: "boom"
        try:
            gf._schema_ready = False
            with self.assertRaises(RuntimeError):
                with gf._db():
                    pass
        finally:
            extensions.create_all_sql = real
            gf._schema_ready = False


class OneDoorOntoTheTables(unittest.TestCase):
    def test_nothing_opens_the_legacy_file_but_the_import(self):
        """A sweep, because a second door onto the file is how half a module
        quietly stays on the old backend."""
        import ast
        with open(os.path.join(REPO, "modules", "google_finder", "app.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        allowed = {"_copy_legacy_into"}
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name in allowed:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and \
                        isinstance(inner.func, ast.Attribute) and \
                        inner.func.attr == "connect" and \
                        isinstance(inner.func.value, ast.Name) and \
                        inner.func.value.id == "sqlite3":
                    offenders.append(node.name)
        self.assertEqual(sorted(set(offenders)), [], offenders)


if __name__ == "__main__":
    unittest.main()
