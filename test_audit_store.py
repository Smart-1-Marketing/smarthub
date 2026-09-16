"""The activity log after it stopped being a file on a disk nobody backs up.

`hub/audit.py` wrote JSONL to the Render persistent disk. Two things were
wrong with that and only one of them was the backup:

  * the disk is outside the database backup, which is the argument
    `hub/jsonstore.py` opens with; and
  * a file is **local to one instance**, so the two halves of a
    zero-downtime deploy keep two different histories and `/activity` shows
    whichever one answered. The activity log is the record somebody
    reconstructs an incident from, and a record that depends on which worker
    you reached is not one.

The rows are in `hub_activity` now, through the shared engine.

What this file is mostly about is the two ways the move could have been made
and silently not made:

  * **`AUDIT_LOG_PATH` must not select the backend.** The live service sets
    it, so "the file when it is set, the database otherwise" keeps production
    on the disk with every test green.
  * **The database must not be gated on Postgres.** 78 of the 79 test files
    that set `AUDIT_LOG_PATH` pin `DATABASE_URL` at a SQLite file of their
    own, so a Postgres-only rule leaves every one of them on the file backend
    while production runs the table. A backend no test exercises is a backend
    nobody has checked.

Both are asserted directly rather than inferred from a passing suite.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import textwrap
import unittest
import uuid

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-auditstore-")
os.environ["HUB_DATA_DIR"] = TMP
# SQLite by default, and the real thing when a Postgres is named -- the
# backend rule is "wherever the shared engine answers", so both shapes
# have to be exercised or the assertion about not gating on Postgres is
# only half checked. checks.yml runs this file a second time with
# S1_AUDIT_TEST_DB set.
os.environ["DATABASE_URL"] = (os.environ.get("S1_AUDIT_TEST_DB")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
ON_PG = os.environ["DATABASE_URL"].startswith("postgres")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "hub-audit.log.jsonl")
os.environ.setdefault("SECRET_KEY", "fixture-only")

from hub import audit  # noqa: E402


def _reset() -> None:
    """A clean table and a clean pair of files, between checks."""
    audit._init()
    if audit._ready:
        with audit._engine.begin() as cx:
            cx.execute(audit._table.delete())
    for p in (audit._path(), audit._pending_path()):
        try:
            os.remove(p)
        except OSError:
            pass                # not there yet is the ordinary starting state


class Backend(unittest.TestCase):
    def test_the_database_is_the_backend_on_sqlite_too(self):
        """Not gated on Postgres, and this is the assertion that says so.

        Gated on it, the whole suite would exercise the file while production
        ran the table.
        """
        if ON_PG:
            self.skipTest("this run is against Postgres; the SQLite half of "
                          "the rule is what the default run asserts")
        _reset()
        audit.log("harness", "hello", actor="todd")
        self.assertEqual(audit.status()["backend"], "database")
        self.assertTrue(audit.status()["ready"])
        self.assertFalse(os.path.exists(audit._pending_path()),
                         "a working database must not be writing the fallback")

    def test_audit_log_path_does_not_select_the_backend(self):
        """Set -- as it is on the live service -- and the table still wins."""
        _reset()
        self.assertTrue(os.environ.get("AUDIT_LOG_PATH"))
        audit.log("harness", "with_the_variable_set")
        self.assertEqual(audit.status()["backend"], "database")
        self.assertEqual([e["type"] for e in audit.read(limit=5)],
                         ["with_the_variable_set"])
        self.assertFalse(os.path.exists(audit._path()),
                         "the legacy file must not be written to any more")

    def test_the_module_reads_it_for_nothing_but_the_file(self):
        """A sweep, because the point is that nothing ELSE consults it.

        A rule reading it back in somewhere -- to decide a backend, to pick a
        reader -- is the failure this file exists to refuse, and it would not
        show up in any behavioural check that happens to run with it set.
        """
        with open(os.path.join(REPO, "hub", "audit.py"), encoding="utf-8") as fh:
            src = fh.read()
        import ast
        reads = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = getattr(f, "attr", "")
            if name not in ("get", "environ", "getenv"):
                continue
            for a in node.args:
                if isinstance(a, ast.Constant) and a.value == "AUDIT_LOG_PATH":
                    reads.append(node.lineno)
        self.assertEqual(len(reads), 1,
                         f"AUDIT_LOG_PATH is read at {reads}; only _path() may")


class NothingElseReadsTheFile(unittest.TestCase):
    """A sweep, because a list of the seventeen we converted proves nothing
    about the eighteenth.

    Fifteen test files read the activity log off AUDIT_LOG_PATH, and two more
    reached it by name. Under the new backend every one of them is asserting
    about a fallback nothing writes to -- and `test_proposal_progress.py`,
    which SEEDED its evidence by appending JSONL, went on passing against it.
    That is the check that cannot fail, and it is silent: nothing errors, the
    file is simply empty and the code under test is never reached.

    So this asks the question of every test file rather than of the ones
    somebody remembered.
    """

    ALLOWED = {
        # seeds the legacy file on purpose -- the import is what it tests
        "test_audit_store.py": "it is the file this file is about",
        # asserts _path() honours AUDIT_LOG_PATH, and opens nothing
        "test_activity_filter.py": "it compares the path, it does not read it",
    }

    def test_no_test_file_reads_the_log_off_the_disk(self):
        import ast
        offenders = []
        for name in sorted(os.listdir(REPO)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            if name in self.ALLOWED:
                continue
            with open(os.path.join(REPO, name), encoding="utf-8") as fh:
                src = fh.read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # open(...), Path(...).read_text(), audit._path()
                names = {getattr(node.func, "id", ""),
                         getattr(node.func, "attr", "")}
                if not names & {"open", "read_text", "readlines", "_path"}:
                    continue
                seg = ast.get_source_segment(src, node) or ""
                if "AUDIT_LOG_PATH" in seg or "hub-audit.log" in seg:
                    offenders.append(f"{name}:{node.lineno}")
        self.assertEqual(offenders, [],
                         "these read the activity log off the disk, which the "
                         "database backend never writes: " + ", ".join(offenders))

    def test_the_allowlist_does_not_outlive_what_it_exempts(self):
        """An exemption naming a file that is gone goes on covering whatever
        is written at that path next."""
        missing = [n for n in self.ALLOWED
                   if not os.path.exists(os.path.join(REPO, n))]
        self.assertEqual(missing, [])


class SignaturesAndShape(unittest.TestCase):
    def test_a_row_comes_back_exactly_as_it_went_in(self):
        """The whole entry is the payload, so nothing about a row changed."""
        _reset()
        audit.log("scans", "scan_done", actor="todd", client="Acme", n=3)
        row = audit.read(limit=1)[0]
        self.assertEqual(row["module"], "scans")
        self.assertEqual(row["type"], "scan_done")
        self.assertEqual(row["actor"], "todd")
        self.assertEqual(row["client"], "Acme")
        self.assertEqual(row["n"], 3)
        self.assertIn("time", row)

    def test_newest_first_and_the_filters_still_narrow(self):
        _reset()
        for i in range(5):
            audit.log("a" if i % 2 else "b", f"t{i}")
        self.assertEqual([e["type"] for e in audit.read(limit=10)],
                         ["t4", "t3", "t2", "t1", "t0"])
        self.assertEqual([e["type"] for e in audit.read(limit=10, module="a")],
                         ["t3", "t1"])
        self.assertEqual([e["module"] for e in audit.read(limit=10, type_="t2")],
                         ["b"])

    def test_tail_and_read_are_one_answer(self):
        _reset()
        for i in range(4):
            audit.log("m", f"t{i}")
        self.assertEqual(audit.tail(limit=3), audit.read(limit=3))
        self.assertEqual(audit.tail(limit=3, module="m"),
                         audit.read(limit=3, module="m"))

    def test_the_limit_is_exact_whatever_a_row_weighs(self):
        """Asked for n, n come back -- a LIMIT rather than a byte window.

        Deliberately not claiming this catches the old reader: its window was
        sized so generously that a fixture small enough to run quickly could
        not have defeated it either, and an assertion whose red case needs
        300 KB of rows is one nobody would keep. What it does hold is that
        the count does not drift with how long a row is.
        """
        _reset()
        for i in range(20):
            audit.log("m", "t", padding="x" * (i * 400))
        self.assertEqual(len(audit.read(limit=3)), 3)
        self.assertEqual(len(audit.tail(limit=7)), 7)
        self.assertEqual(len(audit.read(limit=100)), 20)

    def test_an_explicit_time_wins(self):
        """Documented in log(), and what back-dating a fixture needs."""
        _reset()
        audit.log("m", "old", time="2020-01-01T00:00:00+00:00")
        self.assertEqual(audit.read(limit=1)[0]["time"],
                         "2020-01-01T00:00:00+00:00")

    def test_a_time_nothing_can_parse_is_not_a_crash(self):
        _reset()
        audit.log("m", "odd", time="not a date")
        self.assertEqual(audit.read(limit=1)[0]["time"], "not a date")


class NeverCostsTheAction(unittest.TestCase):
    """log() has always swallowed its own failures. That is unchanged."""

    def test_a_database_that_refuses_writes_the_fallback(self):
        _reset()
        real = audit._db_write
        audit._db_write = lambda entries: False
        try:
            audit.log("m", "during_the_outage", client="Acme")
        finally:
            audit._db_write = real
        self.assertGreater(os.path.getsize(audit._pending_path()), 0)
        self.assertEqual([e["type"] for e in audit.read(limit=5)],
                         ["during_the_outage"],
                         "a row written during an outage must still be read back")

    def test_a_database_that_RAISES_does_not_take_the_action_with_it(self):
        _reset()
        real = audit._db_write

        def boom(entries):
            raise RuntimeError("the database fell over")

        audit._db_write = boom
        try:
            audit.log("m", "still_written")     # must not raise
        finally:
            audit._db_write = real
        self.assertEqual([e["type"] for e in audit.read(limit=5)],
                         ["still_written"])

    def test_the_outage_rows_go_in_when_it_comes_back(self):
        """And they are not left sitting in a file the next deploy takes."""
        _reset()
        real = audit._db_write
        audit._db_write = lambda entries: False
        try:
            for i in range(3):
                audit.log("m", f"outage{i}")
        finally:
            audit._db_write = real
        audit.log("m", "recovered")
        self.assertEqual(audit.status()["pending_rows"], 0,
                         "nothing may be left owed once the table answered")
        self.assertEqual([e["type"] for e in audit.read(limit=10)],
                         ["recovered", "outage2", "outage1", "outage0"])

    def test_an_outage_row_is_newer_than_the_table_and_reads_that_way(self):
        """In front, not behind.

        Appended after the table's rows, the last hour of an outage sorts
        underneath rows from before it -- and /activity then reads as an hour
        in which the newest thing that happened was older than the outage.
        """
        _reset()
        audit.log("m", "before_the_outage")
        real = audit._db_write
        audit._db_write = lambda entries: False
        try:
            audit.log("m", "during_the_outage")
        finally:
            audit._db_write = real
        self.assertEqual([e["type"] for e in audit.read(limit=5)],
                         ["during_the_outage", "before_the_outage"])

    def test_a_refused_batch_is_put_back_rather_than_dropped(self):
        """These are the rows that already had one chance to be lost."""
        _reset()
        real = audit._db_write
        audit._db_write = lambda entries: False
        try:
            audit.log("m", "owed")
        finally:
            audit._db_write = real
        # the flush runs, and the insert refuses again
        audit._db_write = lambda entries: False
        try:
            audit._flush_pending()
        finally:
            audit._db_write = real
        self.assertEqual([e["type"] for e in audit.read(limit=5)], ["owed"])
        self.assertGreater(os.path.getsize(audit._pending_path()), 0)


class TheRetryCooldown(unittest.TestCase):
    """A database that was asleep must not cost the whole boot.

    Render's Postgres can be waking when the first write of a boot lands.
    Caching that first failure for the life of the worker would mean a Hub
    that came up at the wrong moment files every row of that boot on a disk
    nobody backs up -- silently, because every screen still works.
    """

    def test_a_failure_is_not_a_verdict_for_the_life_of_the_worker(self):
        _reset()
        real_engine, real_ready = audit._engine, audit._ready
        try:
            # the state a refused first attempt leaves behind
            audit._ready = False
            audit._init_error = "was asleep"
            audit._init_retry_at = time.time() + 9999
            self.assertFalse(audit._init(), "inside the cooldown, no retry")

            audit._init_retry_at = time.time() - 1      # cooldown elapsed
            self.assertTrue(audit._init(), "past it, it tries again")
            self.assertTrue(audit._ready)
        finally:
            audit._engine, audit._ready = real_engine, real_ready
            audit._init_retry_at = 0.0

    def test_a_row_written_while_it_was_refusing_is_not_lost(self):
        """The cooldown and the fallback are one behaviour from a caller's
        side: the row goes somewhere either way, and lands in the table when
        the database comes back."""
        _reset()
        real = audit._db_write
        audit._db_write = lambda entries: False
        try:
            audit.log("m", "while_it_was_waking")
        finally:
            audit._db_write = real
        self.assertEqual([e["type"] for e in audit.read(limit=5)],
                         ["while_it_was_waking"])
        audit.log("m", "awake")
        self.assertEqual(audit.status()["pending_rows"], 0)


class TheImport(unittest.TestCase):
    def _seed_legacy(self, n=5):
        with open(audit._path(), "w", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps({
                    "time": f"2020-01-0{i + 1}T00:00:00+00:00",
                    "module": "old", "type": "legacy", "n": i}) + "\n")

    def _forget_marker(self):
        from hub import jsonstore
        try:
            jsonstore.delete_json(os.path.join(jsonstore.data_root(),
                                               "audit-import.json"))
        except Exception:       # no marker yet -- which is what this wants
            pass

    def test_it_carries_the_history_across(self):
        _reset()
        self._forget_marker()
        self._seed_legacy(5)
        out = audit.import_legacy()
        self.assertTrue(out["ran"], out)
        self.assertEqual(out["imported"], 5)
        rows = audit.read(limit=10, module="old")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["time"], "2020-01-05T00:00:00+00:00",
                         "a row's own date, not the day of the deploy")

    def test_a_second_run_imports_nothing(self):
        _reset()
        self._forget_marker()
        self._seed_legacy(5)
        audit.import_legacy()
        again = audit.import_legacy()
        self.assertFalse(again["ran"])
        self.assertEqual(len(audit.read(limit=20, module="old")), 5,
                         "the history must not land twice")

    def test_a_pruned_table_does_not_re_import_the_old_file(self):
        """The marker is durable and is not the row count.

        "The table is empty" would replay the whole old history the first
        time rotate() pruned it back to nothing -- a migration firing again
        years later on a log somebody had pruned on purpose.
        """
        _reset()
        self._forget_marker()
        self._seed_legacy(3)
        audit.import_legacy()
        with audit._engine.begin() as cx:
            cx.execute(audit._table.delete())
        self.assertFalse(audit.import_legacy()["ran"])
        self.assertEqual(audit.read(limit=10, module="old"), [])

    def test_a_file_that_cannot_be_read_is_not_recorded_as_carried_across(self):
        _reset()
        self._forget_marker()
        with open(audit._path(), "w", encoding="utf-8") as fh:
            fh.write("\n")          # bytes, and no parseable row in them
        self.assertFalse(audit.import_legacy()["ran"])
        # ...and the next boot tries again rather than believing it is done
        self._seed_legacy(2)
        self.assertTrue(audit.import_legacy()["ran"])

    def test_no_legacy_file_is_not_a_failure(self):
        _reset()
        self._forget_marker()
        out = audit.import_legacy()
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "no legacy file")


class Rotation(unittest.TestCase):
    def test_the_table_is_pruned_to_a_ceiling(self):
        """An append-only store with no ceiling is a slow-motion outage
        whichever layer it sits on."""
        _reset()
        real = audit.MAX_ROWS
        audit.MAX_ROWS = 5
        try:
            for i in range(9):
                audit.log("m", f"t{i}")
            self.assertTrue(audit.rotate())
            kept = [e["type"] for e in audit.read(limit=50)]
        finally:
            audit.MAX_ROWS = real
        self.assertEqual(kept, ["t8", "t7", "t6", "t5", "t4"],
                         "the newest are kept, by insertion order")

    def test_nothing_to_prune_is_not_a_rotation(self):
        _reset()
        audit.log("m", "one")
        self.assertFalse(audit.rotate())


class SaysSoOnAScreen(unittest.TestCase):
    def test_status_names_the_backend_and_what_the_fallback_holds(self):
        _reset()
        audit.log("m", "row")
        st = audit.status()
        self.assertEqual(st["backend"], "database")
        self.assertEqual(st["pending_rows"], 0)
        self.assertIn("max_rows", st)

    def test_an_exception_is_not_a_message(self):
        """SQLAlchemy puts the statement and every bound parameter into
        str(exc). Those are activity rows naming clients and staff, and
        status() is rendered into /diagnostics and pasted into chats."""
        class Fake(Exception):
            def __str__(self):
                return ("IntegrityError: (sqlite3.IntegrityError) NOT NULL\n"
                        "[SQL: INSERT INTO hub_activity ...]\n"
                        "[parameters: [('Acme Plumbing', 'todd@smart1.agency')]]")
        msg = audit._reason(Fake())
        self.assertNotIn("Acme Plumbing", msg)
        self.assertNotIn("todd@smart1.agency", msg)
        self.assertNotIn("\n", msg)
        self.assertIn("Fake", msg)

    def test_a_returned_error_is_trimmed_too(self):
        """`create_all_metadata()` RETURNS its error rather than raising, so
        it reached status() whole -- a rule enforced at one of its two doors
        is a rule that holds until somebody uses the other."""
        import os as _os
        import subprocess as _sp
        import sys as _sys
        env = dict(_os.environ)
        env["DATABASE_URL"] = "postgresql://u:p@127.0.0.1:1/none"
        env["HUB_DATA_DIR"] = tempfile.mkdtemp(prefix="s1-auditnodb-")
        env["AUDIT_LOG_PATH"] = _os.path.join(env["HUB_DATA_DIR"], "a.jsonl")
        env.pop("S1_AUDIT_TEST_DB", None)
        out = _sp.run([_sys.executable, "-c", textwrap.dedent(f"""
            import sys; sys.path.insert(0, {REPO!r})
            from hub import audit
            audit.log("m", "x")
            st = audit.status()
            print(repr(st["backend"]), repr(st["error"]))
        """)], env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("'file'", out.stdout, "a refused database must degrade")
        self.assertEqual(out.stdout.strip().count("\\n"), 0,
                         f"status()['error'] is not one line: {out.stdout}")
        shutil.rmtree(env["HUB_DATA_DIR"], ignore_errors=True)


class DiagnosticsSaysSo(unittest.TestCase):
    """Invisible from every screen is how a mechanism stops working quietly."""

    def _run(self):
        from hub import diagnostics
        return diagnostics.check_activity_log()

    def test_it_is_one_of_the_checks_that_run(self):
        from hub import diagnostics
        self.assertIn(diagnostics.check_activity_log, diagnostics.CHECKS)

    def test_the_database_backend_reads_ok(self):
        _reset()
        audit.log("m", "row")
        c = self._run()
        self.assertEqual(c.state, "ok")
        self.assertIn("In the database", c.detail)

    def test_a_row_still_owed_is_named_rather_than_counted_quietly(self):
        _reset()
        real = audit._db_write
        audit._db_write = lambda entries: False
        try:
            audit.log("m", "owed")
        finally:
            audit._db_write = real
        c = self._run()
        self.assertEqual(c.state, "warn")
        self.assertIn("fallback", c.detail)

    def test_the_file_backend_says_the_log_is_local_to_this_instance(self):
        _reset()
        was_ready, audit._ready = audit._ready, False
        try:
            c = self._run()
        finally:
            audit._ready = was_ready
        self.assertEqual(c.state, "warn")
        self.assertIn("local to this instance", c.detail)


CHILD = textwrap.dedent('''
    import json, os, sys
    root, db, tag = sys.argv[1], sys.argv[2], sys.argv[3]
    os.environ["HUB_DATA_DIR"] = root
    os.environ["DATABASE_URL"] = db
    os.environ["AUDIT_LOG_PATH"] = os.path.join(root, "audit.jsonl")
    os.environ.setdefault("SECRET_KEY", "fixture-only")
    sys.path.insert(0, %r)
    from hub import audit
    for i in range(20):
        audit.log("harness", tag, actor=tag, n=i)
    print(json.dumps({"backend": audit.status()["backend"]}))
''') % REPO


class TwoInstances(unittest.TestCase):
    """One history, whichever half of the deploy answered.

    This is the half a lock cannot fix and the reason the file had to go: two
    instances with two disks kept two logs, each complete-looking, and
    /activity showed whichever one the browser reached.
    """

    def test_both_instances_write_one_history(self):
        db = os.environ["DATABASE_URL"]
        roots = [tempfile.mkdtemp(prefix="s1-auditinst-") for _ in range(2)]
        tags = [f"inst-{uuid.uuid4().hex[:8]}" for _ in range(2)]
        try:
            procs = [subprocess.Popen(
                [sys.executable, "-c", CHILD, roots[i], db, tags[i]],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for i in range(2)]
            outs = [p.communicate(timeout=180) for p in procs]
            for (out, err), p in zip(outs, procs):
                self.assertEqual(p.returncode, 0, err)
                self.assertEqual(json.loads(out.strip().splitlines()[-1])["backend"],
                                 "database", err)
            rows = audit.read(limit=200, module="harness")
            for tag in tags:
                self.assertEqual(len([r for r in rows if r.get("type") == tag]), 20,
                                 f"{tag} is missing from the one history")
        finally:
            for r in roots:
                shutil.rmtree(r, ignore_errors=True)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
