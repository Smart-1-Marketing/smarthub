"""Leads in a table, and the two things that would make the move worthless.

`hub/leads.py` kept every lead the business has captured in an append-only
JSONL on the Render disk. The disk is outside the database backup and does not
survive being recreated -- and it is local to one instance, so the two halves
of a zero-downtime deploy keep two sets of leads and which one answers is
whichever container the browser reached.

The two ways this move could be worse than not making it, and what holds them:

  1. **A lead is lost when the database will not answer.** `capture()` has
     promised since it was written that it never raises: the visitor sees a
     success message either way, so a fault that costs the row is invisible
     from both ends. The pending file and its flush are that promise.
  2. **The backend is silently the wrong one.** A store that reads the table
     in the tests and the file in production, or the reverse, passes
     everything and is wrong the whole time. Both directions are asserted by
     name here.

`test_lead_delivery.py` covers what the module does with a lead; this covers
where the lead is.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-leadstore-")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["HUB_LEADS_FILE"] = os.path.join(TMP, "leads.jsonl")
os.environ["DATABASE_URL"] = (os.environ.get("HUB_LEADS_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
os.environ.setdefault("SECRET_KEY", "fixture-only")

from hub import lead_store  # noqa: E402
from hub import leads  # noqa: E402

ON_PG = os.environ["DATABASE_URL"].startswith("postgres")


def _fresh() -> None:
    """A clean table and no files left over from the check before.

    The cooldown is cleared FIRST. Several checks below break the backend by
    starting a long one, and `drop_for_tests()` asks `_init()` before it drops
    -- so without this the drop silently does nothing and every check after
    the first one to simulate an outage reads the table the one before it
    left. That is an order-dependent suite, which passes on broken code
    depending on what ran first.
    """
    lead_store._ready = False
    lead_store._init_retry_at = 0.0
    lead_store._init_error = ""
    lead_store.drop_for_tests()
    lead_store._file_rows_written = 0
    lead_store._import_state = {"ran": False}
    for path in (lead_store.pending_path(), leads._path()):
        try:
            os.remove(path)
        except OSError:
            pass
    # And the "already imported" marker, which goes through jsonstore and is
    # therefore mirrored into the database -- deleting the file alone leaves
    # the mirror answering, and every import check after the first reads
    # "already imported" about a table that has just been dropped.
    try:
        from hub import jsonstore
        jsonstore.delete_json(os.path.join(jsonstore.data_dir("leads"),
                                           "import.json"))
    except Exception:                                   # noqa: BLE001
        pass
    lead_store._init()


def _lead(lead_id: str, **extra) -> dict:
    row = {"id": lead_id, "created": leads._now(), "source": "landing_ads",
           "page": "/probe", "email": f"{lead_id}@example.com",
           "delivered": False, "retryable": True, "fields": {}, "meta": {}}
    row.update(extra)
    return row


class Backend(unittest.TestCase):
    """Which store answered, asserted in both directions and by name."""

    def setUp(self):
        _fresh()

    def test_a_captured_lead_is_in_the_table(self):
        self.assertEqual(lead_store.store(_lead("aaa")), "database")
        self.assertEqual(lead_store.count(), 1)

    def test_and_not_in_the_legacy_file(self):
        """The half that is easy to leave half-done. A store that writes both
        looks right on every screen and keeps the disk load it was removing."""
        lead_store.store(_lead("bbb"))
        self.assertFalse(os.path.exists(leads._path()),
                         "the legacy file was written to")

    def test_the_leads_file_variable_selects_no_backend(self):
        """HUB_LEADS_FILE names the legacy file, for the import and the
        fallback. The rule `hub/audit.py` arrived at one store earlier: a
        variable that is set on the live service and also chooses the backend
        keeps production on the disk while every test passes on the new
        path."""
        self.assertTrue(os.environ.get("HUB_LEADS_FILE"))
        self.assertEqual(lead_store.status()["backend"], "database")

    def test_the_table_is_what_leads_reads(self):
        lead_store.store(_lead("ccc"))
        self.assertEqual([r["id"] for r in leads._read_all()], ["ccc"])


class WhenTheDatabaseWillNotAnswer(unittest.TestCase):
    """capture() promises it never loses a lead. This is that promise."""

    def setUp(self):
        _fresh()

    def _no_database(self):
        """Break the backend the way an outage does, not by mocking store()."""
        lead_store._ready = False
        lead_store._engine = None
        lead_store._init_retry_at = 9e18       # inside the cooldown, forever

    def test_the_lead_goes_to_the_pending_file(self):
        self._no_database()
        self.assertEqual(lead_store.store(_lead("ddd")), "pending")
        rows = lead_store._file_rows(lead_store.pending_path())
        self.assertEqual([r["id"] for r in rows], ["ddd"])

    def test_and_is_still_read_back(self):
        """A lead in the fallback is not a lead that vanished from the panel."""
        self._no_database()
        lead_store.store(_lead("eee"))
        lead_store._init_retry_at = 0.0
        lead_store._init()
        self.assertIn("eee", [r["id"] for r in leads._read_all()])

    def test_and_read_back_DURING_the_outage_too(self):
        """The interval that is easy to miss. Reading the table, failing, and
        falling back to the legacy file skips the pending spill entirely -- so
        for the length of the outage the leads at risk are the ones the panel
        cannot show, which is the wrong way round."""
        self._no_database()
        lead_store.store(_lead("mid-outage"))
        self.assertIn("mid-outage", [r["id"] for r in leads._read_all()])

    def test_a_lead_is_not_shown_twice_once_it_is_flushed(self):
        self._no_database()
        lead_store.store(_lead("once"))
        lead_store._init_retry_at = 0.0
        lead_store.flush_pending()
        self.assertEqual([r["id"] for r in leads._read_all()], ["once"])

    def test_the_next_capture_puts_it_in(self):
        self._no_database()
        lead_store.store(_lead("fff"))
        lead_store._init_retry_at = 0.0
        self.assertEqual(lead_store.store(_lead("ggg")), "database")
        self.assertEqual([r["id"] for r in leads._read_all()], ["fff", "ggg"])

    def test_the_outage_keeps_its_place_in_the_order(self):
        """Flushed BEFORE the lead that ended the outage, not after.

        Written the other way round the recovered leads take higher ids than
        one captured after them, and append order -- which is the order the
        panel shows and the order the retry sweep works -- reports the outage
        as having happened last.
        """
        self._no_database()
        lead_store.store(_lead("during-1"))
        lead_store.store(_lead("during-2"))
        lead_store._init_retry_at = 0.0
        lead_store.store(_lead("after"))
        self.assertEqual([r["id"] for r in leads._read_all()],
                         ["during-1", "during-2", "after"])

    def test_a_spill_from_a_previous_process_is_flushed(self):
        """The counter that tracks the spill is per process, and the deploy is
        usually what ends the outage -- so gating the flush on it leaves the
        leads captured during the outage readable, and off the backup, for as
        long as nobody captures another one from that same worker."""
        self._no_database()
        lead_store.store(_lead("from-the-old-process"))
        lead_store._init_retry_at = 0.0
        lead_store._file_rows_written = 0          # as a restart leaves it
        self.assertEqual(lead_store.store(_lead("first-after-restart")),
                         "database")
        self.assertEqual(lead_store.count(), 2)

    def test_a_flush_the_database_refuses_puts_them_back(self):
        """These are the leads that already had one chance to be lost."""
        self._no_database()
        lead_store.store(_lead("hhh"))
        real = lead_store._db_insert
        lead_store._db_insert = lambda rows: False
        try:
            self.assertEqual(lead_store.flush_pending(), 0)
        finally:
            lead_store._db_insert = real
        rows = lead_store._file_rows(lead_store.pending_path())
        self.assertEqual([r["id"] for r in rows], ["hhh"])

    def test_capture_still_returns_a_lead(self):
        """The whole promise, driven through the real entry point."""
        self._no_database()
        row = leads.capture("landing_ads", "/outage",
                            {"email": "x@example.com", "name": "X"})
        self.assertTrue(row["id"])
        self.assertIn("x@example.com", row["email"])
        # And says where it went. `last_error` starts as "" in the row
        # literal, so a `setdefault` here would never fire and the one case
        # this reporting exists for would say nothing at all.
        self.assertEqual(row["last_error"], "stored pending a database")

    def test_and_a_capture_that_reached_the_database_says_nothing(self):
        row = leads.capture("landing_ads", "/fine", {"email": "y@example.com"})
        self.assertEqual(row["last_error"], "")


class ChangingOneLead(unittest.TestCase):
    """What a file could not do, and the loss that came of it."""

    def setUp(self):
        _fresh()

    def test_an_update_does_not_touch_the_others(self):
        lead_store.store(_lead("one"))
        lead_store.store(_lead("two"))
        row = _lead("one", delivered=True, contact_id="C1")
        self.assertTrue(lead_store.update_one(row))
        rows = {r["id"]: r for r in leads._read_all()}
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows["one"]["delivered"])
        self.assertFalse(rows["two"]["delivered"])

    def test_a_lead_captured_after_the_read_survives_an_update(self):
        """The incident `_rewrite()` documents: a visitor fills in a landing
        page while a sweep is part-way through, and the sweep's write lands
        the store it read before that capture."""
        lead_store.store(_lead("early"))
        stale = [r for r in leads._read_all()]          # what the sweep read
        lead_store.store(_lead("mid-sweep"))            # the other worker
        changed = dict(stale[0])
        changed["last_error"] = "the sweep's change"
        leads._update(changed)
        rows = {r["id"]: r for r in leads._read_all()}
        self.assertIn("mid-sweep", rows)
        self.assertEqual(rows["early"]["last_error"], "the sweep's change")

    def test_an_unknown_lead_is_added_rather_than_dropped(self):
        """`_update()` appends a row it does not find, and so does this."""
        self.assertTrue(lead_store.update_one(_lead("never-seen")))
        self.assertEqual([r["id"] for r in leads._read_all()], ["never-seen"])

    def test_a_replace_keeps_what_arrived_meanwhile(self):
        lead_store.store(_lead("keep"))
        held = leads._read_all()
        lead_store.store(_lead("arrived"))
        self.assertTrue(lead_store.replace_all(held))
        self.assertEqual(sorted(r["id"] for r in leads._read_all()),
                         ["arrived", "keep"])

    def test_a_replace_that_fails_leaves_the_store_as_it_was(self):
        """One transaction, so a merge's two writes land together or not at
        all -- the module's promise is that neither lead is destroyed.

        The delete happens first, so a failure after it is exactly the shape
        that would empty the store. Forced with two rows carrying the same id,
        which the unique column refuses on both backends: a row with no id at
        all is filtered out before the insert and would pass this by never
        failing.
        """
        lead_store.store(_lead("before"))
        self.assertFalse(lead_store.replace_all([_lead("dup"), _lead("dup")]))
        self.assertEqual([r["id"] for r in leads._read_all()], ["before"])

    def test_a_refused_statement_is_not_an_outage(self):
        """The database answered -- it answered "no". Treating that as the
        backend going down takes it out for the whole cooldown, so for two
        minutes every lead goes to the pending file and every read falls back
        to the legacy file, because one caller sent a duplicate id.

        Found by the check above: the rollback was right, and the store was
        unreadable afterwards anyway.
        """
        lead_store.store(_lead("still-here"))
        lead_store.replace_all([_lead("dup"), _lead("dup")])
        self.assertTrue(lead_store._ready, lead_store._init_error)
        self.assertEqual(lead_store.store(_lead("next")), "database")

    def test_but_a_backend_that_is_gone_is(self):
        """The other direction, so the rule above cannot be widened into
        never standing down at all."""
        lead_store._went_down(OSError("connection refused"))
        self.assertFalse(lead_store._ready)


class TheRowItself(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_the_whole_dict_survives_the_round_trip(self):
        """`fields` and `meta` are open-ended by design, so the payload is the
        row rather than a column per key that drifts from what capture()
        builds."""
        row = _lead("rich", fields={"budget": "10k", "notes": "a" * 300},
                    meta={"utm_source": "google", "nested": {"a": [1, 2]}},
                    client="Quality Air")
        lead_store.store(row)
        back = leads._read_all()[0]
        self.assertEqual(back["fields"], row["fields"])
        self.assertEqual(back["meta"], row["meta"])
        self.assertEqual(back["client"], "Quality Air")

    def test_the_lead_keeps_its_own_id(self):
        """Not a second identity beside it: get(), merge() and
        mark_converted() all hold the id capture() minted."""
        lead_store.store(_lead("its-own-id"))
        self.assertEqual(leads._read_all()[0]["id"], "its-own-id")

    def test_a_lead_keeps_the_date_it_was_captured(self):
        """The import reads leads captured months ago. Stamping those with the
        clock a second time files the whole history as having arrived on the
        afternoon somebody deployed this, and every "how many leads last week"
        answer is then wrong in the same direction."""
        old = _lead("historic", created="2025-03-04T09:15:00+00:00")
        self.assertEqual(lead_store._when(old).year, 2025)
        self.assertEqual(lead_store._when(old).month, 3)
        lead_store.store(old)
        self.assertTrue(leads._read_all()[0]["created"].startswith("2025-03-04"))

    def test_an_unreadable_date_is_now_rather_than_a_raise(self):
        """A lead with a mangled timestamp is still a lead."""
        import datetime
        row = _lead("mangled", created="not a date")
        when = lead_store._when(row)
        self.assertIsInstance(when, datetime.datetime)
        self.assertEqual(lead_store.store(row), "database")

    def test_append_order_is_what_comes_back(self):
        for n in range(5):
            lead_store.store(_lead(f"n{n}"))
        self.assertEqual([r["id"] for r in leads._read_all()],
                         [f"n{n}" for n in range(5)])


class TheImport(unittest.TestCase):
    def setUp(self):
        _fresh()

    def _legacy(self, ids):
        os.makedirs(os.path.dirname(leads._path()), exist_ok=True)
        with open(leads._path(), "w", encoding="utf-8") as fh:
            for i in ids:
                fh.write(json.dumps(_lead(i)) + "\n")

    def test_it_carries_the_file_across(self):
        self._legacy(["old1", "old2", "old3"])
        out = lead_store.import_legacy()
        self.assertTrue(out["ran"], out)
        self.assertEqual(out["imported"], 3)
        self.assertEqual(sorted(r["id"] for r in leads._read_all()),
                         ["old1", "old2", "old3"])

    def test_it_runs_once(self):
        self._legacy(["only"])
        lead_store.import_legacy()
        again = lead_store.import_legacy()
        self.assertFalse(again["ran"])
        self.assertEqual(lead_store.count(), 1)

    def test_a_half_finished_import_can_finish(self):
        """It is retried whenever the last attempt did not verify, so running
        it twice may not be a duplicate-key failure that can never
        complete."""
        self._legacy(["a", "b"])
        lead_store._db_insert([_lead("a")])        # as if it stopped half-way
        out = lead_store.import_legacy()
        self.assertTrue(out["ran"], out)
        self.assertEqual(sorted(r["id"] for r in leads._read_all()), ["a", "b"])

    def test_a_failed_verification_does_not_mark_it_done(self):
        """Recording that a history nobody checked had been carried across is
        the one outcome worse than not having run."""
        self._legacy(["x", "y"])
        real = lead_store._missing_ids
        lead_store._missing_ids = lambda ids: {"y"}
        try:
            out = lead_store.import_legacy()
        finally:
            lead_store._missing_ids = real
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "verification failed")
        # ...and the next boot tries again rather than giving up.
        self.assertTrue(lead_store.import_legacy()["ran"])

    def test_could_not_verify_is_not_verified(self):
        """None and an empty set are different answers."""
        self._legacy(["z"])
        real = lead_store._missing_ids
        lead_store._missing_ids = lambda ids: None
        try:
            out = lead_store.import_legacy()
        finally:
            lead_store._missing_ids = real
        self.assertFalse(out["ran"])
        self.assertIn("verify", out["reason"])

    def test_no_file_is_not_a_failure(self):
        out = lead_store.import_legacy()
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "no legacy file")


class SaysSoOnAScreen(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_status_names_the_backend(self):
        st = lead_store.status()
        self.assertEqual(st["backend"], "database")
        self.assertTrue(st["ready"])

    def test_and_says_so_when_it_is_the_file(self):
        lead_store._ready = False
        lead_store._init_retry_at = 9e18
        self.assertEqual(lead_store.status()["backend"], "file")

    def test_the_error_carries_no_bound_parameters(self):
        """A SQLAlchemy error carries the statement and its values, and the
        values here are a visitor's name, email and phone. /diagnostics is a
        staff screen, not a place to put a stranger's contact details."""
        class Boom(Exception):
            def __str__(self):
                return ("(psycopg.errors.UndefinedTable) relation does not exist\n"
                        "[SQL: INSERT INTO hub_leads ...]\n"
                        "[parameters: {'email': 'visitor@example.com', "
                        "'phone': '555-0100'}]")
        lead_store._went_down(Boom())
        err = lead_store.status()["error"]
        self.assertNotIn("visitor@example.com", err)
        self.assertNotIn("parameters", err)
        self.assertIn("Boom", err)

    def test_diagnostics_carries_a_row_for_it(self):
        from hub import diagnostics
        self.assertIn(diagnostics.check_lead_store, diagnostics.CHECKS)

    def test_the_row_warns_when_leads_are_in_the_fallback(self):
        from hub import diagnostics
        lead_store._ready = False
        lead_store._init_retry_at = 9e18
        row = diagnostics.check_lead_store()
        self.assertEqual(row.state, "warn")
        self.assertIn("instance", row.detail)


class NothingElseReadsTheFile(unittest.TestCase):
    def test_the_store_is_the_only_door_onto_the_leads_path(self):
        """A sweep, because a second door onto the file is how half a module
        quietly stays on the old backend. The named callers are the fallback
        and the import, which are the two that may still open it.
        """
        import ast
        with open(os.path.join(REPO, "hub", "leads.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        allowed = {"_read_all", "_rewrite", "_path", "_exclusive"}
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in allowed:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and \
                        isinstance(inner.func, ast.Name) and \
                        inner.func.id == "open":
                    offenders.append(node.name)
        self.assertEqual(sorted(set(offenders)), [], offenders)


class NoTestReachesPastTheStore(unittest.TestCase):
    """A sweep of the suite, because this is how a check goes quiet.

    `test_landing_maker.py` back-dated a lead by rewriting leads.jsonl. Once
    the leads were in a table that edited a file nothing reads, so the check
    it set up -- a conversion rate split at a page's first open -- would have
    gone on passing while measuring nothing at all. It crashed instead only
    because the file had stopped being created; had anything else written one,
    it would have passed silently.

    Setting HUB_LEADS_FILE is fine: that names the legacy path and selects no
    backend. Opening it is not.
    """

    def test_no_test_file_reads_or_writes_the_leads_path(self):
        import ast
        import pathlib
        mine = pathlib.Path(__file__).name
        offenders = []
        for path in sorted(pathlib.Path(REPO).glob("test_*.py")):
            if path.name == mine:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # `leads._path()` / `_leads._path()` reaching a file call.
                if not isinstance(node.func, ast.Attribute) or \
                        node.func.attr not in {"read_text", "write_text", "open"}:
                    continue
                for inner in ast.walk(node):
                    # `leads._path()`, not any module's `_path()`. Other
                    # modules keep their own file and read it in their own
                    # tests quite correctly -- client_owner is one -- and a
                    # sweep that names those reports work nobody has to do,
                    # which is how a real finding gets skimmed past.
                    if isinstance(inner, ast.Attribute) and \
                            inner.attr == "_path" and \
                            isinstance(inner.value, ast.Name) and \
                            "leads" in inner.value.id:
                        offenders.append(f"{path.name}: {node.func.attr}")
        self.assertEqual(sorted(set(offenders)), [], offenders)


if __name__ == "__main__":
    unittest.main()
