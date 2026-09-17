"""The delivery lock, which is the whole reason these two tables exist.

`modules/io_builder/submission_attempts.py` is not a store anybody reads. It
is how a duplicate opportunity is kept out of Smart 1 Suite: `attempts`
reserves an order before the external write and `receipts` replays a completed
response so a retry answers instead of delivering again.

It was a SQLite file on the Render disk, and `BEGIN IMMEDIATE` took that
file's database-wide write lock. **That serialises the two gunicorn workers
sharing one file and says nothing about a second instance** -- and the two
halves of a zero-downtime deploy are two instances, each with its own disk and
its own file. The reservation that exists to stop a duplicate delivery was not
held across the one event it most needed to be.

`test_io_delivery.py` proves the reservation logic end to end through the
decorator. This file proves the thing underneath it that the decorator's own
test cannot see: the reservation is COMMITTED before the delivery function
runs, so a second request is refused by reading `state='pending'` rather than
by the lock. The read-then-reserve window the lock actually protects is a few
microseconds wide, and a lock that had quietly stopped working would pass that
suite every time.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-iolock-")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = (os.environ.get("IO_DELIVERY_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
os.environ.setdefault("SECRET_KEY", "fixture-only")

from hub import dbshim  # noqa: E402
from modules.io_builder import submission_attempts as sa  # noqa: E402

ON_PG = os.environ["DATABASE_URL"].startswith("postgres")


class TheOrderKey(unittest.TestCase):
    def test_it_is_stable_for_the_same_order(self):
        """A key that changed between calls would stop two workers excluding
        each other while every screen looked fine."""
        self.assertEqual(sa._order_key("IO-1042"), sa._order_key("IO-1042"))

    def test_different_orders_get_different_keys(self):
        self.assertNotEqual(sa._order_key("IO-1042"), sa._order_key("IO-1043"))

    def test_it_fits_in_a_signed_64_bit_integer(self):
        """`pg_advisory_xact_lock` takes a bigint. A key outside that range is
        rejected by Postgres, which the handler above would turn into a 503
        about a delivery that never got the chance to happen."""
        for order in ("IO-1", "x" * 200, "", "üñïçødé-99"):
            key = sa._order_key(order)
            self.assertGreaterEqual(key, -(2 ** 63))
            self.assertLess(key, 2 ** 63)

    def test_it_does_not_ask_postgres_to_hash_it(self):
        """`hashtext()` is internal with no compatibility promise.

        Read by AST with the docstring dropped, because this function's own
        docstring says the word -- a substring check over the source matches
        the explanation of why it is not used, which is the trap this repo has
        a name for: prose is not a call site.
        """
        import ast
        import inspect
        fn = ast.parse(inspect.getsource(sa._order_key)).body[0]
        body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                               and isinstance(fn.body[0].value, ast.Constant)
                               ) else fn.body
        code = "\n".join(ast.unparse(n) for n in body)
        self.assertNotIn("hashtext", code)
        # ...and the check can still see one, or it proves nothing.
        self.assertIn("hashtext", ast.unparse(
            ast.parse("def f():\n    return 'hashtext'")))


class TheLockIsReallyTaken(unittest.TestCase):
    """Driven against the database rather than read off the source."""

    def setUp(self):
        dbshim.drop_all_for_tests(("attempts", "receipts"))
        sa._schema_ready = False

    @contextlib.contextmanager
    def _held_by_someone_else(self, order):
        """Hold `order` on another connection, without ever blocking.

        The holder only waits on an Event, never on a lock, so it cannot get
        stuck inside Postgres. That matters: an earlier version of this file
        had the *contender* block on the lock, and against a deliberately
        broken session-scoped lock that thread stayed blocked for ever,
        holding a pooled connection and hanging a later test rather than
        failing this one. A test that hangs on the defect it exists to catch
        is a CI job timeout nobody can read.
        """
        holding, release, done = (threading.Event(), threading.Event(),
                                  threading.Event())

        def hold():
            try:
                with sa._connect() as db:
                    sa._lock_order(db, order)
                    holding.set()
                    release.wait(15)
            finally:
                done.set()

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.assertTrue(holding.wait(10), "the holder never took the lock")
        try:
            yield
        finally:
            release.set()
            done.wait(15)

    def _could_take(self, order) -> bool:
        """Could a second caller take `order` right now? Asked, never waited on.

        `pg_try_advisory_xact_lock` answers immediately, which is what makes
        this whole file unable to hang.
        """
        with sa._connect() as db:
            return bool(db.execute("SELECT pg_try_advisory_xact_lock(?)",
                                   (sa._order_key(order),)).fetchone()[0])

    def test_the_same_order_cannot_be_taken_twice_at_once(self):
        """The assertion the decorator's own suite cannot make.

        `test_io_delivery.py` proves the reservation end to end, but the
        reservation is COMMITTED before the delivery function runs -- so a
        second request there is refused by reading `state='pending'`, not by
        the lock. The read-then-reserve window the lock actually protects is
        microseconds wide, and a lock that had quietly stopped working would
        pass that suite every time.
        """
        if not ON_PG:
            self.skipTest("BEGIN IMMEDIATE is the SQLite branch; see below")
        with self._held_by_someone_else("IO-SAME"):
            self.assertFalse(self._could_take("IO-SAME"),
                             "two callers held the same order at once")

    def test_and_it_is_released_when_the_transaction_ends(self):
        """`pg_advisory_lock` is session-scoped and would survive the
        transaction -- on a pooled connection that leaks the lock to whoever
        gets that connection next, and the order is wedged until the process
        restarts."""
        if not ON_PG:
            self.skipTest("this run is on the SQLite fallback")
        with self._held_by_someone_else("IO-RELEASED"):
            pass
        self.assertTrue(self._could_take("IO-RELEASED"),
                        "the lock outlived the transaction that took it")

    def test_two_different_orders_do_not_wait_on_each_other(self):
        """The narrowing. SQLite's write lock covered the whole file, so two
        reps sending two different orders queued behind one another for no
        reason."""
        if not ON_PG:
            self.skipTest("the file lock is database-wide on the fallback")
        with self._held_by_someone_else("IO-ONE"):
            self.assertTrue(self._could_take("IO-TWO"),
                            "a different order waited on this one")

    def test_the_fallback_still_takes_sqlite_s_own_lock(self):
        """It is not the mechanism any more, but a Hub with no database
        reachable still shares one file between two workers, and that path may
        not quietly lose the lock it was given."""
        if ON_PG:
            self.skipTest("this run is on a managed database")
        sent = []
        with sa._connect() as db:
            real = db.execute
            db.execute = lambda sql, *a: (sent.append(sql), real(sql, *a))[1]
            sa._lock_order(db, "IO-FALLBACK")
        self.assertEqual(sent, ["BEGIN IMMEDIATE"])

    def test_postgres_is_asked_for_a_transaction_scoped_lock(self):
        """`pg_advisory_lock` is session-scoped and would survive the
        transaction -- on a pooled connection that leaks the lock to whoever
        gets that connection next, and the order is wedged until the process
        restarts. The `_xact_` form is released with the transaction."""
        if not ON_PG:
            self.skipTest("this run is on the SQLite fallback")
        sent = []
        with sa._connect() as db:
            real = db.execute
            db.execute = lambda sql, *a: (sent.append(sql), real(sql, *a))[1]
            sa._lock_order(db, "IO-SHAPE")
        self.assertEqual(len(sent), 1)
        self.assertIn("pg_advisory_xact_lock", sent[0])


class TheStatementsBothDialectsTake(unittest.TestCase):
    def setUp(self):
        dbshim.drop_all_for_tests(("attempts", "receipts"))
        sa._schema_ready = False

    def test_the_reservation_upserts_rather_than_failing(self):
        """`INSERT OR REPLACE` is SQLite's own spelling and Postgres has no
        such statement, so the reservation is written as ON CONFLICT DO
        UPDATE -- the one form both take."""
        # No `_lock_order()` here: this class is about the statements, and
        # taking the real lock made it the thing that hung when a deliberately
        # broken session-scoped lock leaked onto a pooled connection. Only the
        # lock tests take the lock, so a leaked one cannot stall a test that is
        # not about locking.
        for token in ("first", "second"):
            with sa._connect() as db:
                db.execute(
                    "INSERT INTO attempts (order_id,request_id,fingerprint,state,response,code)"
                    " VALUES (?,?,?,'pending',NULL,NULL)"
                    " ON CONFLICT (order_id) DO UPDATE SET"
                    " request_id=excluded.request_id,"
                    " fingerprint=excluded.fingerprint,"
                    " state='pending', response=NULL, code=NULL",
                    ("IO-UPSERT", token, "fp"))
        with sa._connect() as db:
            rows = db.execute("SELECT request_id FROM attempts WHERE order_id=?",
                              ("IO-UPSERT",)).fetchall()
        self.assertEqual([r[0] for r in rows], ["second"])

    def test_a_repeated_receipt_does_not_raise(self):
        """It used to: a duplicate raised, was caught by the decorator's
        handler and answered 503 about a delivery that had in fact
        succeeded."""
        for _ in range(2):
            with sa._connect() as db:
                db.execute(
                    "INSERT INTO receipts (order_id,request_id,fingerprint,response,code)"
                    " VALUES (?,?,?,?,?)"
                    " ON CONFLICT (order_id, request_id) DO NOTHING",
                    ("IO-RECEIPT", "tok", "fp", "{}", 200))
        with sa._connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM receipts WHERE order_id=?",
                ("IO-RECEIPT",)).fetchone()[0], 1)


class NoFileIsLeftBehind(unittest.TestCase):
    def test_nothing_opens_a_sqlite_file_any_more(self):
        import ast
        with open(os.path.join(REPO, "modules", "io_builder",
                               "submission_attempts.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        offenders = [n.func.attr for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "connect"
                     and isinstance(n.func.value, ast.Name)
                     and n.func.value.id == "sqlite3"]
        self.assertEqual(offenders, [], offenders)

    def test_and_the_attempts_file_is_not_created(self):
        with sa._connect() as db:
            db.execute("SELECT COUNT(*) FROM attempts").fetchone()
        from hub.jsonstore import data_dir
        self.assertFalse(
            os.path.exists(os.path.join(data_dir("io_delivery"),
                                        "attempts.sqlite3")))


if __name__ == "__main__":
    unittest.main()
