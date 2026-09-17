"""What hub.storage hands back when Cloudinary is not configured.

The fallback is not exotic. `settings.cloudinary_ready` is False on any
checkout without the credential -- every test run, every local boot, and any
deployment where the variable did not arrive -- so this is the branch fifty
call sites take whenever the Hub is not fully wired, and it is the one branch
none of them look at: two of the fifty check `.backend`, the rest take `.url`.

The defect this file was written for: that branch returned
`f"/hub/assets/{kind}/{safe}"`, and nothing has ever served that path. It
appears nowhere else in the repository, no route matches it, and a booted app
answers 404. So a Hub without Cloudinary filed links that do not open into
client galleries, prospect records and proposals, and every screen said the
upload had worked.

The assertion that matters is the general one, not the string: whatever
`put()` returns, a relative URL must be a path the app can route. A test that
only asserted `url == ""` would pass over a different invented path.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="s1-storage-")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ.setdefault("SECRET_KEY", "fixture-only")
# Owned, not inherited: ready() reads the composed URL, and a developer with
# real Cloudinary credentials exported would otherwise run a different branch
# of this file than CI does and see it pass for the wrong reason.
for name in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME",
             "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
    os.environ.pop(name, None)

from hub import storage                                   # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture bytes, not a real image" * 8


class TheFallbackIsTheBranchUnderTest(unittest.TestCase):
    """Guard the premise. Everything below is vacuous if Cloudinary answers."""

    def test_cloudinary_is_not_configured_here(self):
        self.assertFalse(storage.ready(),
                         "This file tests the disk fallback; something has "
                         "configured Cloudinary, so nothing below is measuring "
                         "what it claims to measure.")


class ADiskAssetHasNoDeliveryUrl(unittest.TestCase):

    def test_a_relative_url_must_be_one_the_app_can_route(self):
        """The general invariant, driven against the real app.

        This is the assertion that catches the defect and would catch a
        differently-spelled replacement for it. `/hub/assets/...` 404s; so
        would `/assets/...` or `/static/uploads/...`.
        """
        asset = storage.put("seo_images", "routing-probe.png", PNG)
        if not asset.url or asset.url.startswith(("http://", "https://")):
            return                      # nothing relative to resolve
        import wsgi
        from werkzeug.test import Client
        status = Client(wsgi.application).get(asset.url).status_code
        self.assertNotEqual(status, 404,
                            f"put() returned {asset.url!r}, which nothing "
                            f"serves: the booted app answers {status}.")

    def test_the_url_is_empty_rather_than_invented(self):
        asset = storage.put("seo_images", "empty-url.png", PNG)
        self.assertEqual(asset.backend, "disk")
        self.assertEqual(asset.url, "")

    def test_it_says_why_there_is_no_url(self):
        """An empty string on its own is not an explanation."""
        asset = storage.put("seo_images", "explained.png", PNG)
        self.assertTrue(asset.note, "A disk asset carries no reason.")
        self.assertIn("Cloudinary", asset.note)
        # The note names the file, so somebody told "it is on the disk" can go
        # and find it rather than being told a category.
        self.assertIn(TMP, asset.note)

    def test_the_reason_survives_being_filed(self):
        """Callers record as_dict(), not the object."""
        asset = storage.put("seo_images", "as-dict.png", PNG)
        self.assertEqual(asset.as_dict()["note"], asset.note)
        self.assertEqual(asset.as_dict()["url"], "")


class TheBytesAreStillKept(unittest.TestCase):
    """No URL is not the same as no file. A render that cost money stays."""

    def test_the_file_is_on_the_disk_and_identical(self):
        asset = storage.put("proposals", "kept.png", PNG)
        root = os.path.join(TMP, "assets", "proposals")
        written = os.path.join(root, asset.public_id)
        self.assertTrue(os.path.isfile(written), f"{written} was not written.")
        with open(written, "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        self.assertEqual(asset.bytes, len(PNG))

    def test_an_empty_file_is_still_refused(self):
        with self.assertRaises(storage.StorageError):
            storage.put("proposals", "nothing.png", b"")


class WhatFellBackIsCountable(unittest.TestCase):
    """Silence was the other half of the defect."""

    def test_local_assets_counts_the_files_it_wrote(self):
        before = storage.local_assets()
        self.assertTrue(before["measured"])
        storage.put("counted", "one.png", PNG)
        storage.put("counted", "two.png", PNG)
        after = storage.local_assets()
        self.assertEqual(after["files"] - before["files"], 2)
        self.assertEqual(after["bytes"] - before["bytes"], 2 * len(PNG))

    def test_a_directory_it_cannot_read_is_not_reported_as_zero(self):
        """`measured` exists so "none" and "could not look" stay different."""
        with patch("os.walk", side_effect=OSError("denied")):
            out = storage.local_assets()
        self.assertFalse(out["measured"])
        self.assertEqual(out["files"], 0)

    def test_the_diagnostics_row_says_what_is_held(self):
        storage.put("diagnosed", "row.png", PNG)
        from hub import diagnostics
        detail = diagnostics.check_cloudinary().detail
        self.assertIn("no URL", detail)
        self.assertIn("disk", detail)


class TheRemoteFetchStillRefuses(unittest.TestCase):
    """The principle put() now follows was already written down next to it."""

    def test_put_remote_raises_rather_than_faking_a_local_copy(self):
        with self.assertRaises(storage.StorageError) as caught:
            storage.put_remote("seo_images", "https://example.com/x.png")
        self.assertIn("Cloudinary", str(caught.exception))


class TheCloudinaryPathIsUnchanged(unittest.TestCase):
    """The fix must not have been "return nothing" for everybody."""

    def test_a_configured_upload_returns_its_secure_url_and_no_note(self):
        fake = {"public_id": "smart1-seo-images/x", "secure_url":
                "https://res.cloudinary.com/demo/image/upload/x.png"}
        with patch.object(storage, "ready", return_value=True), \
                patch.object(storage, "_configure"), \
                patch("cloudinary.uploader.upload", return_value=fake):
            asset = storage.put("seo_images", "configured.png", PNG)
        self.assertEqual(asset.backend, "cloudinary")
        self.assertEqual(asset.url, fake["secure_url"])
        self.assertEqual(asset.note, "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
