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
from modules.image_picker import app as picker
from modules.image_picker.models import Base, PickerClient, SavedImage, provider_identity
from flask import Flask

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
        self.client = PickerClient(name="QA Gallery", slug="qa-gallery", share_token="qa-only", industry_key="home_services")
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

    def test_identity_boundary_and_existing_hash_are_stable(self):
        self.assertEqual(provider_identity("x" * 120), "x" * 120)
        hashed = provider_identity("x" * 121)
        self.assertTrue(hashed.startswith("sha256:"))
        self.assertEqual(provider_identity(hashed), hashed)

    def test_copies_with_the_same_long_prefix_keep_distinct_identities(self):
        original = self.file("original")
        row = self.db.get(SavedImage, original["image"]["id"])
        prefix = "client-assets/qa-gallery/" + "x" * 125
        copies = [{"ok": True, "public_id": prefix + suffix,
                   "url": "https://assets.example.test/" + suffix + ".png",
                   "filename": suffix + ".png", "resource_type": "image"}
                  for suffix in ("one", "two")]
        with patch.object(filing, "_copy_asset", side_effect=copies):
            results = [filing.resolve_duplicate(self.db, row, "duplicate",
                       kind="upload", key="new-project", project_name="New project",
                       push_to_suite=False) for _ in copies]
        for result, asset in zip(results, copies):
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["image"]["public_id"], asset["public_id"])
            retried = self.file(asset["public_id"])
            self.assertEqual(retried["image"]["id"], result["image"]["id"])
        self.assertNotEqual(results[0]["image"]["id"], results[1]["image"]["id"])

    def test_upload_route_preserves_long_ids_and_deduplicates_retries(self):
        app = Flask(__name__)
        app.secret_key = "qa-test"
        app.register_blueprint(picker.bp)
        prefix = picker._upload_folder(self.client) + "/" + "x" * 125
        payloads = [{"token": "qa-only", "public_id": prefix + suffix,
                     "secure_url": "https://assets.example.test/" + suffix + ".png"}
                    for suffix in ("one", "two")]
        with patch.object(picker, "session", return_value=self.db), \
             patch.object(picker, "_client_from_token_or_staff", return_value=self.client), \
             patch.object(picker.ghl, "push_image", return_value={"status":"skipped", "file_id":"", "url":"", "error":""}):
            http = app.test_client()
            results = [http.post("/tools/image-picker/api/uploads", json=p).get_json() for p in payloads]
            for result, payload in zip(results, payloads):
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["image"]["public_id"], payload["public_id"])
                retry = http.post("/tools/image-picker/api/uploads", json=payload).get_json()
                self.assertTrue(retry["duplicate"], retry)
                self.assertEqual(retry["image"]["id"], result["image"]["id"])
            self.assertNotEqual(results[0]["image"]["id"], results[1]["image"]["id"])

    def test_stock_save_does_not_collapse_long_provider_ids(self):
        app = Flask(__name__)
        app.secret_key = "qa-test"
        app.register_blueprint(picker.bp)
        items = [{"id": suffix, "provider":"pexels", "provider_image_id":"x" * 125 + suffix,
                  "full":"https://images.pexels.com/fixture.png", "alt":"QA photo"}
                 for suffix in ("one", "two")]
        with patch.object(picker, "resolve_scope", return_value=(self.db, self.client, False)), \
             patch.object(picker, "verify_item", return_value=True), \
             patch.object(picker.cloudinary_sink, "configured", return_value=True), \
             patch.object(picker.cloudinary_sink, "upload_from_url", return_value={"public_id":"qa-image", "delivery_url":"https://assets.example.test/qa.png", "width":32,"height":32,"bytes":100}), \
             patch.object(picker.ghl, "push_image", return_value={"status":"skipped", "file_id":"", "url":"", "error":""}), \
             patch("hub.asset_meta.for_client", return_value={"context":{},"tags":[]}):
            http = app.test_client()
            first = http.post("/tools/image-picker/api/save", json={"items":items}).get_json()
            self.assertEqual(len(first["saved"]), 2, first)
            again = http.post("/tools/image-picker/api/save", json={"items":items}).get_json()
            self.assertEqual(len(again["skipped"]), 2, again)
            self.assertEqual(len(again["saved"]), 0, again)

if __name__ == "__main__":
    unittest.main(verbosity=2)
