"""Image-tool regression cases; no live providers or production storage.

Run: python test_image_tools.py
"""
import base64
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

_tmp = tempfile.TemporaryDirectory(prefix="image-tools-test-", ignore_cleanup_errors=True)
os.environ["HUB_DATA_DIR"] = _tmp.name
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp.name, "test.db")
os.environ["SECRET_KEY"] = "image-tools-test-secret"
os.environ["AUDIT_LOG_PATH"] = os.path.join(_tmp.name, "audit.jsonl")
# hub.config.settings is a frozen dataclass built once at import, so the key
# hub.ai.image() actually reads has to be set before hub.ai is ever imported
# (directly or via modules.image_creator) -- the same convention
# test_ai_injection.py uses. Mocking modules.image_creator's own _settings()
# no longer controls this: /api/ai/image is routed through hub.ai.image()
# now, which reads hub.config.settings.openai_key rather than the caller's.
os.environ["OPENAI_API_KEY"] = "sk-test-fixture-not-real"

from PIL import Image
from modules.image_optimizer import app as optimizer
from modules.image_creator import app as creator
from modules.bg_remover import app as background


def png(image=None):
    buf = io.BytesIO()
    (image or Image.new("RGB", (80, 60), "red")).save(buf, "PNG")
    return buf.getvalue()


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.client = optimizer.app.test_client()

    def process(self, raw=None, **fields):
        return self.client.post("/process", data={
            "image": (io.BytesIO(raw or png()), "test.png"),
            **{k: str(v) for k, v in fields.items()},
        })

    def test_phone_orientation_before_resize(self):
        for orientation in (5, 6, 7, 8):
            exif = Image.Exif()
            exif[274] = orientation
            buf = io.BytesIO()
            Image.new("RGB", (800, 600), "red").save(buf, "JPEG", exif=exif)
            for optimize in ("true", "false"):
                for fmt in ("PNG", "JPG"):
                    with self.subTest(orientation=orientation, optimize=optimize, format=fmt):
                        r = self.process(buf.getvalue(), width=300, lock_aspect="true",
                                         optimize=optimize, format=fmt)
                        self.assertEqual(r.status_code, 200)
                        out = Image.open(io.BytesIO(r.data))
                        self.assertEqual(out.size, (300, 400))
                        self.assertIsNone(out.getexif().get(274))

    def test_crop_uses_upright_coordinates(self):
        image = Image.new("RGB", (800, 600), "red")
        image.paste("blue", (400, 0, 800, 600))
        exif = Image.Exif()
        exif[274] = 6
        buf = io.BytesIO()
        image.save(buf, "JPEG", exif=exif)
        # In the displayed portrait, the blue half is at the bottom.
        r = self.process(buf.getvalue(), crop_enabled="true", crop_x=100,
                         crop_y=650, crop_width=200, crop_height=100)
        self.assertEqual(r.status_code, 200)
        out = Image.open(io.BytesIO(r.data)).convert("RGB")
        self.assertEqual(out.size, (200, 100))
        red, green, blue = out.getpixel((100, 50))
        self.assertGreater(blue, 240)
        self.assertLess(red + green, 15)

    def test_target_miss_is_explicit_and_dimensions_are_actual(self):
        raw = png(Image.effect_noise((900, 700), 100).convert("RGB"))
        with patch.object(optimizer, "_audit") as audit:
            r = self.process(raw, optimize="true", target_kb=10, format="PNG")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.data), 10240)
        self.assertEqual(r.headers["X-Target-Met"], "false")
        self.assertEqual(r.headers["X-Target-Bytes"], "10240")
        size = Image.open(io.BytesIO(r.data)).size
        self.assertEqual(tuple(int(r.headers[f"X-Output-{k}"]) for k in ("Width", "Height")), size)
        self.assertEqual((audit.call_args.kwargs["width"], audit.call_args.kwargs["height"]), size)

    def test_target_met_and_disabled_are_distinct(self):
        r = self.process(optimize="true", target_kb=10)
        self.assertEqual(r.headers["X-Target-Met"], "true")
        r = self.process(optimize="false", target_kb=10)
        self.assertNotIn("X-Target-Met", r.headers)

    def test_invalid_numbers_name_the_field(self):
        for field, message in (("quality", "Quality must be a whole number."),
                               ("target_kb", "Target size must be a whole number of KB.")):
            for value in ("abc", "", "12.5"):
                with self.subTest(field=field, value=value):
                    r = self.process(**{field: value})
                    self.assertEqual(r.status_code, 400)
                    self.assertEqual(r.get_json()["error"], message)


class BackgroundCreditTests(unittest.TestCase):
    def test_preview_paid_and_cached_usage_are_distinct(self):
        for quality, cached, credits, previews in (
                ("preview", False, 0, 1), ("auto", False, 1, 0),
                ("full", False, 1, 0), ("preview", True, 0, 0),
                ("auto", True, 0, 0)):
            with self.subTest(quality=quality, cached=cached), \
                    patch.object(background, "configured", return_value=True), \
                    patch.object(background, "api_key", return_value="test-only-key"), \
                    patch.object(background, "_sweep"), \
                    patch.object(background, "_cache_get", return_value=png() if cached else None), \
                    patch.object(background, "_cache_put"), \
                    patch.object(background, "_log"), \
                    patch("requests.post", return_value=Mock(ok=True, status_code=200, content=png())) as post, \
                    patch("hub.quotas.record") as record:
                r = background.app.test_client().post("/api/remove", data={
                    "images": (io.BytesIO(png()), "fixture.png"), "quality": quality,
                })
                self.assertEqual(r.status_code, 200)
                body = r.get_json()
                self.assertEqual(body["credits_used"], credits)
                self.assertEqual(body["preview_calls"], previews)
                self.assertEqual(body["results"][0]["billed"], bool(credits))
                self.assertEqual(body["results"][0]["cached"], cached)
                self.assertEqual(body["results"][0]["preview"], quality == "preview")
                self.assertEqual(record.call_count, credits)
                self.assertEqual(post.call_count, 0 if cached else 1)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = creator.app.test_client()
        self.settings = patch.object(creator, "_settings", return_value=SimpleNamespace(
            openai_key="test-only-key", openai_model="test-copy-model",
            openai_image_model="test-image-model"))
        self.settings.start()
        self.addCleanup(self.settings.stop)
        # /api/ai/image is routed through hub.ai.image() now, which records
        # spend through hub.audit.log(..., ok=...) via hub.ai._record()
        # rather than the note_usage() helper the old direct-call path used.
        self.usage = patch("hub.audit.log").start()
        self.addCleanup(patch.stopall)

    def test_rejected_provider_requests_do_not_expose_bodies(self):
        for status in (400, 401, 403, 429, 500):
            response = Mock(ok=False, status_code=status, text="PRIVATE PROVIDER DETAIL")
            for route, body in (("image", {"prompt": "test"}),
                                ("photo-queries", {"prompt": "test"}),
                                ("copy", {"text": "test"})):
                with self.subTest(status=status, route=route), patch("requests.post", return_value=response):
                    r = self.client.post("/api/ai/" + route, json=body)
                    self.assertEqual(r.status_code, 502)
                    message = r.get_json()["error"]
                    self.assertNotIn("PRIVATE", message)
                    self.assertIn("AI", message)

    def test_network_errors_do_not_expose_connection_details(self):
        import requests
        for route, body in (("image", {"prompt": "test"}),
                            ("photo-queries", {"prompt": "test"}),
                            ("copy", {"text": "test"})):
            with patch("requests.post", side_effect=requests.Timeout("PRIVATE CONNECTION DETAIL")):
                r = self.client.post("/api/ai/" + route, json=body)
            self.assertEqual(r.status_code, 502)
            self.assertNotIn("PRIVATE", r.get_json()["error"])

    def test_empty_or_malformed_image_response_is_failure(self):
        for payload in ({"data": []}, {"data": [None]}, {"data": [{}]}, []):
            response = Mock(ok=True, status_code=200)
            response.json.return_value = payload
            with patch("requests.post", return_value=response):
                r = self.client.post("/api/ai/image", json={"prompt": "test"})
            self.assertEqual(r.status_code, 502)
            self.assertFalse(self.usage.call_args.kwargs["ok"])

    def test_successful_generation_is_usable_and_recorded(self):
        raw = png()
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"data": [{"b64_json": base64.b64encode(raw).decode()}]}
        with patch("requests.post", return_value=response) as post:
            r = self.client.post("/api/ai/image", json={"prompt": "blue mug"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(base64.b64decode(r.get_json()["image"].split(",", 1)[1]), raw)
        self.assertEqual(post.call_args.kwargs["json"]["n"], 1)
        self.assertTrue(self.usage.call_args.kwargs["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
