"""Video render recovery and request validation. Synthetic providers, isolated SQLite.

Run: python test_video_render.py
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TMP = tempfile.TemporaryDirectory(prefix="commercial-reliability-")
os.environ["HUB_DATA_DIR"] = TMP.name
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(TMP.name) / "test.sqlite3")
os.environ["SECRET_KEY"] = "commercial-test-secret"
os.environ["PANEL_PASSWORD"] = "commercial-test-password"
for name in ("OPENAI_API_KEY", "HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY",
             "ELEVENLABS_API", "ELEVENLABS_API_KEY", "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
    os.environ.pop(name, None)

import requests
from flask import Flask
import modules.commercial_builder as builder
from modules.commercial_builder.db import db
from modules.commercial_builder.models import Client, CommercialProject, Scene, RenderJob
from modules.commercial_builder.services import creatomate_service

app = Flask(__name__)
app.config.update(TESTING=True, SECRET_KEY="test", SQLALCHEMY_DATABASE_URI=os.environ["DATABASE_URL"])
db.init_app(app)
# Only this synthetic app is unguarded; production blueprint/auth code is unchanged.
with patch.object(builder, "_hub_auth", None):
    app.register_blueprint(builder.create_blueprint())


class CompositionTests(unittest.TestCase):
    def test_end_card_is_visible_timed_composition(self):
        project = {"length_seconds": 6, "platform": "youtube",
                   "cta": {"business_name": "Test Brand", "headline": "Bring ideas to life",
                           "website": "https://example.test"}}
        scene = {"id": 2, "start": 3, "end": 6, "is_cta": True}
        source = creatomate_service.build_source(project, [scene], "16:9")
        self.assertEqual((source["width"], source["height"], source["duration"]), (1920, 1080, 6))
        card = source["elements"][0]
        self.assertEqual((card["type"], card["time"], card["duration"]), ("composition", 3, 3))
        layers = card["elements"]
        self.assertEqual(layers[0]["type"], "shape")
        self.assertEqual(layers[0]["fill_color"], "#10243a")
        self.assertTrue(all(layer["time"] == 0 for layer in layers))
        self.assertEqual(len({layer["track"] for layer in layers}), len(layers))
        text = [layer for layer in layers if layer["type"] == "text"]
        self.assertIn("Test Brand", [layer["text"] for layer in text])
        self.assertIn("example.test", [layer["text"] for layer in text])
        self.assertTrue(all(layer["fill_color"] == "#ffffff" for layer in text))
        self.assertNotIn("overlay", card)

    def test_studio_overlay_keeps_background_and_local_timing(self):
        scene = {"id": 3, "start": 4, "end": 8, "asset_type": "stock",
                 "asset_url": "https://example.test/clip.mp4",
                 "asset_meta": {"text_overlay": [{"type": "text", "text": "Hello", "time": 1}]}}
        source = creatomate_service.build_source({"length_seconds": 8}, [scene], "9:16")
        card = source["elements"][0]
        self.assertEqual((source["width"], source["height"]), (1080, 1920))
        self.assertEqual(card["time"], 4)
        self.assertEqual(card["elements"][0]["source"], scene["asset_url"])
        self.assertEqual(card["elements"][0]["time"], 0)
        self.assertEqual(card["elements"][1]["time"], 1)

    def test_paid_submit_explicitly_requests_full_resolution(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"id": "full-resolution", "status": "planned"}
        with patch.object(creatomate_service, "is_live", return_value=True), \
             patch.object(creatomate_service, "_headers", return_value={}), \
             patch.object(creatomate_service, "_meter"), \
             patch.object(creatomate_service.requests, "post", return_value=response) as post:
            source = {"width": 1920, "height": 1080, "elements": []}
            creatomate_service.submit_render(source)
        self.assertEqual(post.call_args.kwargs["json"], {"source": source, "render_scale": 1})


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = Client(name="Test Business", slug="test-business", preferred_voiceover_id="eleven-voice")
        db.session.add(self.client)
        db.session.flush()
        self.project = CommercialProject(client_id=self.client.id, title="Internal revision 7", length_seconds=10)
        self.project.formats = ["16:9"]
        self.project.music = {}
        db.session.add(self.project)
        db.session.flush()
        self.scene = Scene(project_id=self.project.id, order_index=0, start=0, end=10,
                           narration="Call Test Business today.", asset_type="stock", asset_url="https://example.test/stock.mp4")
        db.session.add(self.scene)
        db.session.commit()
        self.http = app.test_client()
        self.base = f"/tools/commercial-builder/api/projects/{self.project.id}"
        self.scene_url = f"{self.base}/scenes/{self.scene.id}"
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Live network disabled in tests"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def test_render_status_timeout_recovers_same_job(self):
        job = RenderJob(project_id=self.project.id, format="16:9", provider_render_id="paid-render", status="rendering")
        db.session.add(job)
        db.session.commit()
        response = unittest.mock.Mock()
        response.json.return_value = {"id": "paid-render", "status": "succeeded", "url": "https://example.test/final.mp4"}
        with patch.object(creatomate_service, "is_live", return_value=True), \
             patch.object(creatomate_service.requests, "get", side_effect=[requests.Timeout("temporary timeout"), response]) as get:
            first = self.http.get(self.base + f"/render-jobs/{job.id}/status").get_json()
            self.assertEqual(first["render_job"]["status"], "rendering")
            second = self.http.get(self.base + f"/render-jobs/{job.id}/status").get_json()
        self.assertEqual(second["render_job"]["status"], "succeeded")
        self.assertEqual(job.output_url, "https://example.test/final.mp4")
        self.assertIsNone(job.error)
        self.assertEqual(get.call_count, 2)

    def test_malformed_render_formats_do_not_submit(self):
        with patch.object(creatomate_service, "submit_render") as submit:
            for formats in ("16:9", [None], [["16:9"]], {"size": "16:9"}):
                result = self.http.post(self.base + "/render", json={"formats": formats})
                self.assertEqual(result.status_code, 400)
            submit.assert_not_called()

    def test_provider_waiting_states_keep_polling(self):
        response = unittest.mock.Mock()
        with patch.object(creatomate_service, "is_live", return_value=True), \
             patch.object(creatomate_service.requests, "get", return_value=response), \
             patch.object(creatomate_service.requests, "post", return_value=response), \
             patch.object(creatomate_service, "_meter"):
            for state in ("planned", "waiting", "transcribing"):
                response.json.return_value = {"id":"paid-render", "status":state}
                self.assertEqual(creatomate_service.check_render("paid-render")["status"], "queued")
                self.assertEqual(creatomate_service.submit_render({})["status"], "queued")
            response.json.return_value = {"id":"paid-render"}
            self.assertTrue(creatomate_service.check_render("paid-render")["retryable"])
            response.json.return_value = {"id":"paid-render", "status":"failed", "error_message":"Bad source"}
            self.assertEqual(creatomate_service.check_render("paid-render")["error"], "Bad source")

if __name__ == "__main__":
    unittest.main(verbosity=2)
