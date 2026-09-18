"""The optimizer's routes, and the instance boundary they cross.

`test_page_image_bytes.py` proves the store layer: bytes written against one
data directory are readable against a different one. This file asks the
question one level up, where a person actually stands — **does `api_save`
succeed when the save is routed to a different instance than the scan?**

That is not the same question, and the gap between them is where a regression
would land. `store.get_bytes()` could be perfect while `api_save` still fails,
because the route reaches it through a job record, an item id and a filename
convention (`f"{item['id']}.webp"`) that the store test never exercises. Three
routes read bytes that way -- `api_save`, `api_preview` and `api_rename` --
and all three answered the same way before the move: an expiry message about a
file that had not expired.

`api_save` is the one that costs something. The person has edited filenames and
alt text by the time they press it, and the old failure threw the batch away
and told them it had expired.

## Why the defect is reproduced rather than described

The headline test passes trivially if the harness cannot tell the two
instances apart -- a shared temp root, a cached engine, a fixture directory
that was not fresh. So `TheHarnessCanSeeTheDefect` puts the module back the
way it was (the database refusing, the disk answering alone) and asserts the
SAME request then fails with the SAME message a person used to get. A green
run means the boundary test is driven against something that really breaks,
not that the assertion happened to hold.

## Scope

A bare Flask app with the blueprint registered at its real prefix, not the
composed `wsgi.application`. Mount shadowing is a real trap in this repo, but
it is `test_blueprint_guards.py` and `tools/linkcheck.py` that guard it; what
is unproven here is the route logic, and a bare app reaches it without a
two-minute boot.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-pir-")
os.environ["HUB_DATA_DIR"] = TMP
# Owned, not inherited: the mirror is keyed relative to the data root, so two
# runs against one inherited database meet each other's rows -- the shape
# test_jsonstore.py names.
os.environ["DATABASE_URL"] = (os.environ.get("PAGE_IMAGE_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(TMP, "hub.sqlite3"))
os.environ.setdefault("SECRET_KEY", "fixture-only")
# A real key would send the naming calls to OpenAI and bill for them. No route
# exercised here needs a suggestion: the batch is built directly.
os.environ.pop("OPENAI_API_KEY", None)

from flask import Flask                                          # noqa: E402

from modules.page_image_optimizer import (app as optimizer_app,   # noqa: E402
                                          bytes_store, store)

MOUNT = "/tools/page-images"

WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"optimized bytes" * 40
THUMB = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"preview" * 10

PAGE = "https://example.com/services"
IMG = "https://example.com/img/DSC_0001.JPG"

EXPIRED = "The optimized file expired before saving."


def _client():
    """The blueprint at its real prefix, with the staff gate satisfied."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    optimizer_app.register(flask_app, url_prefix=MOUNT)
    return flask_app.test_client()


def _second_instance():
    """Empty this instance's filesystem, leaving the configured path alone.

    This is the fixture detail that decides whether the file means anything,
    and the first draft got it wrong. A second instance is NOT a different
    directory: `store.DATA_DIR` comes from `HUB_DATA_DIR`, one configured
    value every instance shares, so the second instance looks for the job at
    exactly the same path and finds nothing at it. Pointing the module at a
    different path instead also moves the `hub/jsonstore.py` mirror key, which
    is relative to the data root -- so the job metadata went missing too, for
    a reason no deploy ever produces, and the route 404'd before it ever
    reached the bytes.

    Wiping the contents is what a redeployed container actually hands you:
    same path, nothing in it. Both mirrors then have to answer -- the job
    record from `jsonstore`, the bytes from `bytes_store` -- which is the
    whole claim.

    (`test_page_image_bytes.py` patches the path and is right to: `bytes_store`
    is keyed on `(job_id, name)` and has no path in it at all. It is the JSON
    half that the path is load-bearing for.)
    """
    root = store.DATA_DIR
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        target = os.path.join(root, name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        else:
            os.remove(target)


def _a_job_with_one_optimized_image(item_id="i0"):
    """A job as `api_batch` leaves it, without downloading anything.

    `_process_one` is a network call, an image decode and a naming suggestion.
    None of those are what this file is testing, and all three would make it
    slow and flaky. What matters downstream is the shape it leaves behind: a
    saved job, a batch of items, and the bytes under `<item id>.webp` and
    `<item id>.preview` -- so that shape is built directly, and
    `TheBatchShapeMatchesTheRoute` keeps it honest.
    """
    job = store.create_job({
        "company": "Acme Roofing",
        "project": "Website refresh",
        "page_name": "Services",
        "page_url": PAGE,
        "page_title": "Services | Acme",
        "page_h1": "Our services",
        "page_description": "",
        "candidates": {IMG: {"url": IMG, "optimizable": True, "bytes": 900_000,
                             "saving": 700_000, "alt": "", "caption": "",
                             "nearest_heading": "Our services"}},
    })
    item = {
        "id": item_id,
        "source_url": IMG,
        "filename": "acme-roofing-services",
        "alt": "A roofer working on a residential roof",
        "ai": False,
        "status": "ready",
        "error": "",
        "naming_error": "",
        "info": {"bytes": 120_000, "source_bytes": 900_000,
                 "saved_bytes": 780_000, "saved_pct": 86,
                 "width": 1600, "height": 900},
        "history": [],
        "context": {"company": "Acme Roofing", "project": "Website refresh"},
    }
    batch_id = store.new_id()
    job.setdefault("batches", {})[batch_id] = {"id": batch_id, "items": [item]}
    store.save_job(job)

    store.put_bytes(job["id"], f"{item_id}.webp", WEBP)
    store.put_bytes(job["id"], f"{item_id}.preview", THUMB)
    return job, batch_id


def _staff():
    """Satisfy `hub/blueprint_guard.py` without issuing a real cookie.

    The guard imports `user_from_environ` inside the request, so this patches
    the function on the module it is read from rather than a local name.
    """
    return patch("hub.auth.user_from_environ",
                 return_value={"email": "fixture@example.com"})


def _cloudinary_stub():
    """`api_save` uploads. Stubbed, or the test bills a real account."""
    up = patch.object(optimizer_app.archive, "upload", return_value={
        "url": "https://res.cloudinary.com/demo/acme-roofing-services.webp",
        "public_id": "smart1-seo-images/acme-roofing-services",
        "stored": True, "note": ""})
    rec = patch.object(optimizer_app.archive, "record", return_value="ok")
    return up, rec


# --------------------------------------------------------------------------- #
# premises
# --------------------------------------------------------------------------- #

class TheDatabaseIsAnsweringHere(unittest.TestCase):
    """Guard the premise: every boundary assertion is vacuous without it."""

    def test_the_bytes_table_is_reachable(self):
        self.assertTrue(
            bytes_store.available(),
            "The bytes table is not reachable, so every read below would be "
            f"answered by the disk fallback: {bytes_store.status()['error']}")


class TheGateIsOn(unittest.TestCase):
    """A 200 below must mean the route ran, not that the guard is off."""

    def test_an_unauthenticated_save_is_refused(self):
        job, batch_id = _a_job_with_one_optimized_image()
        got = _client().post(
            f"{MOUNT}/api/job/{job['id']}/batch/{batch_id}/save", json={})
        self.assertEqual(got.status_code, 401,
                         "The optimizer's API answered without a login. "
                         "hub/blueprint_guard.py has stopped covering it.")


class TheBatchShapeMatchesTheRoute(unittest.TestCase):
    """The fixture builds a batch by hand; this is what stops it drifting.

    If `api_save` ever reads a different key than `<item id>.webp`, the
    boundary tests would keep passing against a fixture nobody uses. So the
    filename convention is read back out of the route's own source.
    """

    def test_save_reads_the_key_the_fixture_writes(self):
        import inspect
        source = inspect.getsource(optimizer_app.api_save)
        self.assertIn('f"{item[\'id\']}.webp"', source,
                      "api_save no longer reads `<item id>.webp`, so the "
                      "fixture in this file is writing a key nothing reads.")


# --------------------------------------------------------------------------- #
# the headline
# --------------------------------------------------------------------------- #

class SaveCrossesTheInstanceBoundary(unittest.TestCase):
    """A scan on one instance, a save on another. The whole point."""

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_a_save_routed_to_a_second_instance_still_saves(self):
        job, batch_id = _a_job_with_one_optimized_image()

        # Everything the scan wrote to this instance's disk is gone. This is
        # the second half of a zero-downtime deploy: same configured path,
        # same database, a filesystem that has never seen this job.
        _second_instance()
        self.assertFalse(os.path.exists(os.path.join(store.DATA_DIR,
                                                     job["id"], "i0.webp")),
                         "the fixture did not actually clear the disk")

        up, rec = _cloudinary_stub()
        with _staff(), up, rec:
            got = _client().post(
                f"{MOUNT}/api/job/{job['id']}/batch/{batch_id}/save",
                json={"items": [{"id": "i0",
                                 "filename": "acme-roofing-services",
                                 "alt": "A roofer at work"}]})

        self.assertEqual(got.status_code, 200)
        body = got.get_json()
        self.assertEqual(
            body["failed"], [],
            "The save was routed to a second instance and lost the batch. "
            f"This is the defect the move exists to fix: {body['failed']}")
        self.assertEqual(len(body["saved"]), 1)
        self.assertEqual(body["saved"][0]["filename"], "acme-roofing-services")

    def test_the_preview_survives_the_boundary(self):
        job, _ = _a_job_with_one_optimized_image()
        _second_instance()
        with _staff():
            got = _client().get(f"{MOUNT}/api/job/{job['id']}/preview/i0")

        self.assertEqual(got.status_code, 200,
                         "The preview 404'd on a second instance, so the "
                         "thumbnails would be broken mid-deploy.")
        self.assertEqual(got.data, THUMB)


# --------------------------------------------------------------------------- #
# the guard on the headline
# --------------------------------------------------------------------------- #

class TheHarnessCanSeeTheDefect(unittest.TestCase):
    """Put the module back the way it was and watch the same request fail.

    Without this, `SaveCrossesTheInstanceBoundary` proves only that the
    request returned 200 -- which it would also do if the two "instances"
    shared a directory, or if the patch never took effect. Here the database
    is made to refuse, exactly as it did before the table existed, so the
    bytes land on the first instance's disk and nowhere else.

    The expected failure is the message a person actually used to see.
    """

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_disk_only_bytes_still_lose_the_batch(self):
        job = store.create_job({
            "company": "Acme Roofing", "project": "Website refresh",
            "page_name": "Services", "page_url": PAGE,
            "page_title": "", "page_h1": "", "page_description": "",
            "candidates": {IMG: {"url": IMG, "optimizable": True,
                                 "bytes": 900_000, "saving": 700_000}},
        })
        item = {"id": "i0", "source_url": IMG, "filename": "acme",
                "alt": "alt", "ai": False, "status": "ready", "error": "",
                "naming_error": "", "history": [], "context": {},
                "info": {"bytes": 1, "source_bytes": 2, "saved_bytes": 1,
                         "saved_pct": 50, "width": 10, "height": 10}}
        batch_id = store.new_id()
        job.setdefault("batches", {})[batch_id] = {"id": batch_id,
                                                   "items": [item]}
        store.save_job(job)

        # The store as it was: the table refuses, so put_bytes falls through to
        # the disk under the CURRENT data directory and nothing else holds it.
        with patch.object(bytes_store, "put", return_value=False):
            store.put_bytes(job["id"], "i0.webp", WEBP)
        self.assertTrue(
            os.path.exists(os.path.join(store.DATA_DIR, job["id"], "i0.webp")),
            "the disk-only write did not happen, so this proves nothing")

        _second_instance()
        up, rec = _cloudinary_stub()
        with _staff(), up, rec, \
                patch.object(bytes_store, "get", return_value=None):
            got = _client().post(
                f"{MOUNT}/api/job/{job['id']}/batch/{batch_id}/save",
                json={"items": [{"id": "i0"}]})

        body = got.get_json()
        self.assertEqual(body["saved"], [],
                         "A disk-only store saved across instances, so the "
                         "two directories in this fixture are not really "
                         "separate and the boundary tests prove nothing.")
        self.assertEqual([f["error"] for f in body["failed"]], [EXPIRED],
                         "The old defect no longer produces the message this "
                         "file is written around.")


# --------------------------------------------------------------------------- #
# the message still means something
# --------------------------------------------------------------------------- #

class ExpiryStillReportsWhenBytesAreReallyGone(unittest.TestCase):
    """The fix must not work by making the error unreachable.

    A save whose bytes are genuinely absent -- swept, or never optimized --
    still has to say so rather than uploading nothing and reporting success.
    """

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_a_swept_batch_is_reported_not_silently_saved(self):
        job, batch_id = _a_job_with_one_optimized_image()
        store.drop_job(job["id"])          # the sweep, or a finished job
        # drop_job removes the metadata too, so put the job back WITHOUT its
        # bytes: this is a batch whose images aged out from under it.
        store.save_job(job)

        up, rec = _cloudinary_stub()
        with _staff(), up, rec:
            got = _client().post(
                f"{MOUNT}/api/job/{job['id']}/batch/{batch_id}/save",
                json={"items": [{"id": "i0"}]})

        body = got.get_json()
        self.assertEqual(body["saved"], [])
        self.assertEqual([f["error"] for f in body["failed"]], [EXPIRED],
                         "Bytes that are genuinely gone were not reported.")

    def test_an_unknown_batch_is_a_404(self):
        job, _ = _a_job_with_one_optimized_image()
        with _staff():
            got = _client().post(
                f"{MOUNT}/api/job/{job['id']}/batch/nosuchbatch/save",
                json={"items": []})
        self.assertEqual(got.status_code, 404)

    def test_an_unknown_job_is_a_404(self):
        with _staff():
            got = _client().post(
                f"{MOUNT}/api/job/nosuchjob/batch/nosuchbatch/save",
                json={"items": []})
        self.assertEqual(got.status_code, 404)


# --------------------------------------------------------------------------- #
# what the person edited is what gets saved
# --------------------------------------------------------------------------- #

class TheEditsSurviveTheSave(unittest.TestCase):
    """The edits are the part losing a batch actually costs."""

    def setUp(self):
        bytes_store.drop_table_for_tests()

    def test_the_edited_filename_and_alt_are_what_is_recorded(self):
        job, batch_id = _a_job_with_one_optimized_image()
        up, rec = _cloudinary_stub()
        with _staff(), up as upload, rec:
            got = _client().post(
                f"{MOUNT}/api/job/{job['id']}/batch/{batch_id}/save",
                json={"items": [{"id": "i0",
                                 "filename": "Roofer On A Roof!!",
                                 "alt": "  A roofer   at work  "}]})
            kwargs = upload.call_args.kwargs

        row = got.get_json()["saved"][0]
        self.assertEqual(row["filename"], "roofer-on-a-roof",
                         "The person's filename was not slugified into the "
                         "one that reaches Cloudinary.")
        self.assertEqual(row["alt"], "A roofer at work",
                         "Whitespace in the edited alt text was not collapsed.")
        self.assertEqual(kwargs["filename"], "roofer-on-a-roof")
        self.assertEqual(kwargs["alt"], "A roofer at work")

    def test_a_skipped_image_is_skipped_not_saved(self):
        job, batch_id = _a_job_with_one_optimized_image()
        up, rec = _cloudinary_stub()
        with _staff(), up as upload, rec:
            got = _client().post(
                f"{MOUNT}/api/job/{job['id']}/batch/{batch_id}/save",
                json={"items": [{"id": "i0", "skip": True}]})

        body = got.get_json()
        self.assertEqual(body["saved"], [])
        self.assertEqual(body["skipped"], [IMG])
        self.assertEqual(upload.call_count, 0,
                         "A skipped image was uploaded anyway.")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
