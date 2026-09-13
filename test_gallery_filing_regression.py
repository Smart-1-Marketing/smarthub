"""Gallery filing against real constraints, with no provider calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory(prefix="gallery-filing-regression-")
os.environ["HUB_DATA_DIR"] = _temp.name
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_temp.name) / "isolated.db")
os.environ["HUB_SCHEDULER"] = "0"

from sqlalchemy import CheckConstraint, create_engine, select
from sqlalchemy.orm import Session
from modules.image_picker import filing
from modules.image_picker.models import Base, PickerClient, SavedImage

# SQLite otherwise silently accepts values rejected by production Postgres.
SavedImage.__table__.append_constraint(CheckConstraint(
    "length(provider_image_id) <= 120", name="test_provider_id_width"))
SavedImage.__table__.append_constraint(CheckConstraint(
    "filename <> 'reject-this-asset'", name="test_failed_write"))

class GalleryFilingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.client = PickerClient(name="QA Gallery", slug="qa-gallery", share_token="qa-only")
        self.db.add(self.client)
        self.db.commit()
        self.session_patch = patch.object(filing, "session", return_value=self.db)
        self.session_patch.start()
        self.network_patch = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        self.network_patch.start()
        self.push_patch = patch.object(filing.ghl, "push_image", side_effect=AssertionError("Suite writes forbidden"))
        self.push_patch.start()

    def tearDown(self):
        self.push_patch.stop()
        self.network_patch.stop()
        self.session_patch.stop()
        self.db.close()
        self.engine.dispose()

    def file(self, public_id, **extra):
        return filing.file_asset(client_name="QA Gallery", public_id=public_id,
            url="https://assets.example.test/fixture.png", provider="google_drive",
            push_to_suite=False, **extra)

    def test_long_identity_preserved_and_retry_deduplicated(self):
        original = "client-assets/qa-gallery/ad-assets/io-test/" + "asset-" * 25
        first = self.file(original)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["image"]["public_id"], original)
        self.assertLessEqual(len(first["image"]["provider_image_id"]), 120)
        again = self.file(original)
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["image"]["id"], first["image"]["id"])

    def test_identical_long_prefixes_remain_distinct(self):
        prefix = "folder/" + "x" * 125
        first, second = self.file(prefix + "/one"), self.file(prefix + "/two")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(first["image"]["id"], second["image"]["id"])
        self.assertEqual(len(self.db.scalars(select(SavedImage)).all()), 2)

    def test_short_identity_unchanged(self):
        result = self.file("existing/provider-id")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["image"]["provider_image_id"], "existing/provider-id")

    def test_failed_write_does_not_poison_the_next_asset(self):
        failed = self.file("rejected", filename="reject-this-asset")
        self.assertFalse(failed["ok"])
        self.assertTrue(self.db.is_active)
        next_asset = self.file("next-valid-asset", filename="valid.png")
        self.assertTrue(next_asset["ok"], next_asset)
        self.assertEqual(len(self.db.scalars(select(SavedImage)).all()), 1)

    def test_session_acquisition_failure_remains_a_named_failure(self):
        with patch.object(filing, "session", side_effect=RuntimeError("database unavailable")):
            result = self.file("not-filed")
        self.assertFalse(result["ok"])
        self.assertIn("database unavailable", result["error"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
