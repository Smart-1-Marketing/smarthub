"""The page optimizer's bytes, and the instance boundary they now cross.

The defect: `store.put_bytes()` wrote the optimized `.webp` and its `.preview`
to the Render disk and nowhere else. A disk is shared by the two gunicorn
workers and local to one INSTANCE, so a scan on one instance and a save routed
to another found nothing and answered *"The optimized file expired before
saving"* about a file that had not expired — losing the batch, including the
alt text and filenames the person had just edited.

The assertion that matters is the one the disk could never satisfy: **bytes
written against one data directory are readable against a different one**,
with only the database in common. Everything else here is guarding that claim
from passing for the wrong reason.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-pib-")
os.environ["HUB_DATA_DIR"] = TMP
# Owned, not inherited: the mirror is keyed relative to the data root, so two
# runs against one inherited database meet each other's rows -- the shape
# test_jsonstore.py names.
os.environ["DATABASE_URL"] = (os.environ.get("PAGE_IMAGE_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
os.environ.setdefault("SECRET_KEY", "fixture-only")

from modules.page_image_optimizer import bytes_store, store   # noqa: E402

WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"optimized bytes" * 40
THUMB = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"preview" * 10


def _fresh_data_dir():
    """What a second instance has: the same database, its own directory."""
    return tempfile.mkdtemp(prefix="s1-pib-other-")


class TheDatabaseIsAnsweringHere(unittest.TestCase):
    """Guard the premise: every assertion below is vacuous without it."""

    def test_the_table_is_reachable(self):
        self.assertTrue(
            bytes_store.available(),
            "The bytes table is not reachable, so the reads below would be "
            f"answered by the disk fallback: {bytes_store.status()['error']}")


class BytesCrossTheInstanceBoundary(unittest.TestCase):
    """The assertion the disk could not satisfy."""

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_a_second_instance_reads_what_the_first_wrote(self):
        store.put_bytes("job-cross", "i1.webp", WEBP)

        # Point the module at a directory the first write never touched. This
        # is what the second half of a zero-downtime deploy is: same database,
        # its own filesystem. Patched rather than re-imported so the read goes
        # through the same code path a request would.
        other = _fresh_data_dir()
        try:
            with patch.object(store, "DATA_DIR", other):
                got = store.get_bytes("job-cross", "i1.webp")
        finally:
            shutil.rmtree(other, ignore_errors=True)

        self.assertEqual(got, WEBP,
                         "A save routed to a second instance could not read "
                         "the bytes the scan produced.")

    def test_and_the_disk_really_was_not_what_answered(self):
        """Without this the test above passes on a shared /tmp by accident."""
        store.put_bytes("job-proof", "i2.webp", WEBP)
        other = _fresh_data_dir()
        try:
            with patch.object(store, "DATA_DIR", other):
                on_disk = os.path.join(other, "job-proof", "i2.webp")
                self.assertFalse(os.path.exists(on_disk),
                                 "the fixture directory was not fresh")
                self.assertEqual(store.get_bytes("job-proof", "i2.webp"), WEBP)
        finally:
            shutil.rmtree(other, ignore_errors=True)


class TheRoundTripIsByteExact(unittest.TestCase):

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_what_goes_in_comes_out(self):
        store.put_bytes("job-exact", "a.webp", WEBP)
        store.put_bytes("job-exact", "a.preview", THUMB)
        self.assertEqual(store.get_bytes("job-exact", "a.webp"), WEBP)
        self.assertEqual(store.get_bytes("job-exact", "a.preview"), THUMB)

    def test_re_running_a_batch_replaces_rather_than_raises(self):
        """The same (job, name) is written twice when a batch is re-run."""
        store.put_bytes("job-twice", "a.webp", WEBP)
        store.put_bytes("job-twice", "a.webp", THUMB)
        self.assertEqual(store.get_bytes("job-twice", "a.webp"), THUMB)

    def test_a_missing_blob_is_none_not_an_error(self):
        self.assertIsNone(store.get_bytes("job-nothing", "absent.webp"))

    def test_the_traversal_guard_still_bites(self):
        self.assertIsNone(store.get_bytes("job-x", "../../etc/passwd"))
        self.assertIsNone(store.get_bytes("job-x", "/etc/passwd"))
        self.assertIsNone(store.get_bytes("../bad", "a.webp"))


class TheDiskIsStillTheFallback(unittest.TestCase):
    """A render that cost a download and real CPU is not thrown away."""

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_a_refused_database_write_still_keeps_the_bytes(self):
        with patch.object(bytes_store, "put", return_value=False):
            store.put_bytes("job-fallback", "a.webp", WEBP)
        on_disk = os.path.join(store.DATA_DIR, "job-fallback", "a.webp")
        self.assertTrue(os.path.isfile(on_disk),
                        "the bytes were lost when the database refused")
        with open(on_disk, "rb") as fh:
            self.assertEqual(fh.read(), WEBP)

    def test_and_they_are_readable_again(self):
        with patch.object(bytes_store, "put", return_value=False):
            store.put_bytes("job-fallback2", "a.webp", WEBP)
        with patch.object(bytes_store, "get", return_value=None):
            self.assertEqual(store.get_bytes("job-fallback2", "a.webp"), WEBP)

    def test_a_batch_from_the_previous_release_is_not_expired_early(self):
        """The reason get_bytes reads both, for one TTL after a deploy.

        Bytes written by the old release are on the disk and in no table.
        Reading only the table would answer "expired" about a batch somebody
        is still looking at -- the same message this change exists to stop.
        """
        legacy = os.path.join(store.DATA_DIR, "job-legacy")
        os.makedirs(legacy, exist_ok=True)
        with open(os.path.join(legacy, "old.webp"), "wb") as fh:
            fh.write(WEBP)
        self.assertEqual(store.get_bytes("job-legacy", "old.webp"), WEBP)


class WhatIsSweptAndDropped(unittest.TestCase):

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_dropping_a_job_takes_its_rows(self):
        store.put_bytes("job-drop", "a.webp", WEBP)
        store.put_bytes("job-keep", "a.webp", WEBP)
        store.drop_job("job-drop")
        self.assertIsNone(store.get_bytes("job-drop", "a.webp"))
        self.assertEqual(store.get_bytes("job-keep", "a.webp"), WEBP,
                         "dropping one job took another job's bytes")

    def test_the_sweep_takes_what_is_past_the_ttl_and_leaves_the_rest(self):
        store.put_bytes("job-old", "a.webp", WEBP)
        store.put_bytes("job-new", "a.webp", WEBP)
        # Age one row by rewriting its timestamp, rather than by sleeping.
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import update
        old = (datetime.now(timezone.utc).replace(tzinfo=None)
               - timedelta(minutes=600))
        with bytes_store._engine.begin() as cx:
            cx.execute(update(bytes_store._table)
                       .where(bytes_store._table.c.job_id == "job-old")
                       .values(created=old))
        went = bytes_store.sweep(45)
        self.assertGreaterEqual(went, 1)
        self.assertIsNone(store.get_bytes("job-old", "a.webp"))
        self.assertEqual(store.get_bytes("job-new", "a.webp"), WEBP,
                         "the sweep took a batch somebody is still reviewing")


class TheDatabaseIsNotAnUnboundedDumpingGround(unittest.TestCase):

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_an_absurd_blob_is_refused_and_goes_to_the_disk(self):
        """One pathological page must not put 16 MB rows in a shared database."""
        huge = b"x" * (bytes_store.MAX_BLOB_BYTES + 1)
        self.assertFalse(bytes_store.put("job-huge", "a.webp", huge))
        store.put_bytes("job-huge", "a.webp", huge)
        self.assertTrue(os.path.isfile(
            os.path.join(store.DATA_DIR, "job-huge", "a.webp")),
            "an oversized blob was refused by both stores and simply lost")

    def test_an_empty_blob_is_refused(self):
        self.assertFalse(bytes_store.put("job-empty", "a.webp", b""))


class TheStatusRowSaysWhatItKnows(unittest.TestCase):

    def test_it_counts_and_says_it_measured(self):
        bytes_store.drop_table_for_tests()
        store.put_bytes("job-status", "a.webp", WEBP)
        st = bytes_store.status()
        self.assertTrue(st["ready"])
        self.assertTrue(st["measured"])
        self.assertGreaterEqual(st["rows"], 1)

    def test_a_count_it_cannot_take_is_not_reported_as_zero(self):
        """`measured` exists so "nothing in flight" and "could not look" differ."""
        with patch.object(bytes_store, "_engine") as eng:
            eng.begin.side_effect = RuntimeError("connection gone")
            st = bytes_store.status()
        self.assertFalse(st["measured"])
        self.assertIsNone(st["rows"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
