"""Model review is free, admin-only, durable, and cannot activate a model."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from dataclasses import replace

TMP = tempfile.TemporaryDirectory(prefix="ai-model-review-")
os.environ["HUB_DATA_DIR"] = TMP.name
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(TMP.name) / "test.db")
from flask import Flask
from hub import ai_models, ai, quotas, openai_responses
from hub.ai_model_routes import bp


class ModelReviewTests(unittest.TestCase):
    def test_defaults_and_override_precedence(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ai_models.model("commercial.text"), "gpt-4o-mini")
            self.assertEqual(ai_models.model("radio.text"), "gpt-4o")
            with patch.dict(os.environ, {"OPENAI_TEXT_MODEL": "legacy", "COMMERCIAL_OPENAI_MODEL": "override"}):
                self.assertEqual(ai_models.model("commercial.text"), "override")
                self.assertEqual(ai_models.resolve("commercial.text")["source"], "COMMERCIAL_OPENAI_MODEL")

    def test_listing_is_not_activation(self):
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"id": "gpt-future"}, {"id": "gpt-5.6-terra"}]}
        before = ai_models.model("radio.text")
        from hub.config import settings
        with patch("hub.config.settings", replace(settings, openai_key="test-key")), patch("requests.get", return_value=response) as get:
            ai_models.refresh_catalog()
            get.assert_called_once()
        with patch("requests.get", side_effect=AssertionError("page must not call provider")):
            view = ai_models.inventory()
        self.assertIn("gpt-future", view["discovered"])
        self.assertEqual(ai_models.model("radio.text"), before)
        self.assertEqual(view["profiles"][2]["availability"], "listed")

    def test_review_history_and_validation(self):
        before = ai_models.model("radio.text")
        row = ai_models.record_review("radio.text", "gpt-5.6-terra", "recommend",
                                     "Compared sample A and B; timing requires a separate voice test.", "admin@test")
        self.assertTrue(any(r["id"] == row["id"] for r in ai_models.history()))
        self.assertEqual(ai_models.model("radio.text"), before)
        with self.assertRaises(ValueError):
            ai_models.record_review("radio.text", "bad/key", "recommend", "short", "admin")

    def test_permissions_json_and_escaped_history(self):
        app = Flask(__name__, template_folder=str(Path(__file__).parent / "hub/templates"))
        app.register_blueprint(bp)
        app.jinja_env.globals.update(current_user=lambda: None, render_sidebar=lambda **kw: "")
        client = app.test_client()
        with patch("hub.users_routes.current_account", return_value=None):
            self.assertEqual(client.get("/diagnostics/ai-models").status_code, 401)
        with patch("hub.users_routes.current_account", return_value=SimpleNamespace(is_admin=False)):
            self.assertEqual(client.post("/api/diagnostics/ai-models/refresh", json={}).status_code, 403)
        with patch("hub.users_routes.current_account", return_value=SimpleNamespace(is_admin=True, email="admin@test")):
            self.assertEqual(client.post("/api/diagnostics/ai-models/reviews", data="{}").status_code, 415)
            result = client.post("/api/diagnostics/ai-models/reviews", json={"profile": "radio.text",
                     "candidate": "gpt-5.6-terra", "decision": "test", "evidence": "<script>alert('review')</script> requires review."})
            self.assertEqual(result.status_code, 201)
            page = client.get("/diagnostics/ai-models")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(b"<script>alert('review')", page.data)

    def test_unknown_prices_and_snapshot_match(self):
        self.assertIsNone(ai.estimate_cost("gpt-future", 100, 100))
        self.assertEqual(quotas._price("gpt-4.1-mini-2025-04-14"), quotas.PRICING["gpt-4.1-mini"])
        with patch("hub.quotas.untracked_openai_modules", return_value=[]), patch("hub.audit.read", return_value=[{"time": "2026-09-01", "model": "gpt-future",
                   "tool": "radio", "tokens_in": 100, "tokens_out": 20, "ok": True}]):
            result = quotas.openai_cost("2026-09")
        self.assertIsNone(result["estimated_cost"])
        self.assertEqual(result["unpriced_models"], ["gpt-future"])

    def test_retry_only_transient_http_errors(self):
        success = Mock(status_code=200)
        success.json.return_value = {"ok": True}
        with patch("hub.ai.settings", replace(ai.settings, openai_retries=1)), patch("hub.ai.time.sleep"), patch("requests.post", side_effect=[Mock(status_code=429), success]) as post:
            self.assertEqual(ai._post("/chat/completions", {}, 1), {"ok": True})
            self.assertEqual(post.call_count, 2)
        with patch("requests.post", return_value=Mock(status_code=401)) as post:
            with self.assertRaises(ai.AIUnavailable):
                ai._post("/chat/completions", {}, 1)
            self.assertEqual(post.call_count, 1)

    def test_partial_response_and_unrelated_400(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "incomplete", "output": [{"content": [{"type": "output_text", "text": "partial"}]}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with self.assertRaises(RuntimeError):
                openai_responses.ask("test", module="test", call=lambda *args: response)
            response.status_code = 400
            response.json.return_value = {"error": {"message": "Invalid input"}}
            send = Mock(return_value=response)
            with self.assertRaises(RuntimeError):
                openai_responses.ask("test", module="test", search=True, call=send)
            self.assertEqual(send.call_count, 1)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        from hub import ai_comparisons
        self.comparisons = ai_comparisons
        self.body = {
            "profile": "radio.text", "active_model": ai_models.model("radio.text"),
            "candidate": "gpt-comparison-test", "brief": "offer-15-v1",
            "settings": "Fixed brief v1; same endpoint, voice and request settings.",
        }
        for side in ("active", "proposed"):
            self.body[side] = {"script": "Cedar Auto $29 Book today Offer ends Friday",
                "quality": 3 if side == "active" else 4, "compatibility": "pass",
                "delivery": "pass", "reference": "sample-" + side,
                "notes": "Listened to the sample and verified the supplied facts.",
                "latency_seconds": 2, "reported_cost_usd": 0.01,
                "wav_base64": self.wav(15)}

    @staticmethod
    def wav(seconds):
        import io, wave, base64
        out = io.BytesIO()
        with wave.open(out, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * int(8000 * seconds))
        return base64.b64encode(out.getvalue()).decode("ascii")

    def test_measures_audio_and_preserves_immutable_evidence(self):
        before = ai_models.model("radio.text")
        row = self.comparisons.record(self.body, "admin@test")
        self.assertEqual(row["suggestion"], "Candidate leads on this sample")
        self.assertEqual(row["proposed"]["audio"]["seconds"], 15)
        self.assertTrue(row["proposed"]["within_slot"])
        self.assertNotIn("wav_base64", row["proposed"])
        second = self.comparisons.record(self.body, "admin@test")
        self.assertNotEqual(row["id"], second["id"])
        self.assertTrue(any(r["id"] == row["id"] for r in self.comparisons.history()))
        self.assertEqual(ai_models.model("radio.text"), before)

    def test_missing_measurements_are_unknown(self):
        self.body["proposed"]["wav_base64"] = None
        self.body["proposed"]["reported_cost_usd"] = ""
        row = self.comparisons.record(self.body, "admin@test")
        self.assertEqual(row["suggestion"], "More evidence needed")
        self.assertIsNone(row["proposed"]["within_slot"])
        self.assertIsNone(row["proposed"]["reported_cost_usd"])

    def test_required_wording_and_overrun_block_positive_suggestion(self):
        self.body["proposed"]["script"] = "Cedar Auto $29 Book today"
        self.body["proposed"]["wav_base64"] = self.wav(15.1)
        row = self.comparisons.record(self.body, "admin@test")
        self.assertEqual(row["suggestion"], "Candidate needs revision")
        self.assertEqual(row["proposed"]["missing"], ["Offer ends Friday"])
        self.assertFalse(row["proposed"]["within_slot"])

    def test_revision_detects_obsolete_facts(self):
        self.body["brief"] = "revision-60-v1"
        self.body["proposed"]["script"] = "Saturday at ten. Free admission. Find your next story at Harbor Books. Sunday noon."
        row = self.comparisons.record(self.body, "admin@test")
        self.assertEqual(row["proposed"]["forbidden"], ["Sunday", "noon"])
        self.assertEqual(row["suggestion"], "Candidate needs revision")

    def test_rejects_corrupt_truncated_or_oversized_wav(self):
        import base64
        truncated = base64.b64encode(base64.b64decode(self.wav(1))[:-10]).decode()
        for data in ("bad!", truncated, "x" * (6 * 1024 * 1024)):
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                self.comparisons.measure_wav(data)

    def test_stale_models_and_invalid_inputs_cannot_be_saved(self):
        from copy import deepcopy
        for field, value in (("active_model", "old-model"), ("profile", "radio.image"),
                             ("profile", []), ("brief", {}), ("candidate", self.body["active_model"])):
            body = deepcopy(self.body)
            body[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.comparisons.record(body, "admin@test")
        for field, value in (("quality", 6), ("quality", True), ("reported_cost_usd", "NaN"),
                             ("latency_seconds", -1), ("compatibility", []), ("delivery", {})):
            body = deepcopy(self.body)
            body["proposed"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.comparisons.record(body, "admin@test")

    def test_comparison_route_permissions_origin_and_rendering(self):
        app = Flask(__name__, template_folder=str(Path(__file__).parent / "hub/templates"))
        app.register_blueprint(bp)
        app.jinja_env.globals.update(current_user=lambda: None, render_sidebar=lambda **kw: "")
        client = app.test_client()
        url = "/api/diagnostics/ai-models/comparisons"
        with patch("hub.users_routes.current_account", return_value=None):
            self.assertEqual(client.post(url, json={}).status_code, 401)
        with patch("hub.users_routes.current_account", return_value=SimpleNamespace(is_admin=False)):
            self.assertEqual(client.post(url, json={}).status_code, 403)
        with patch("hub.users_routes.current_account", return_value=SimpleNamespace(is_admin=True, email="admin@test")):
            self.assertEqual(client.post(url, json={}, headers={"Origin": "https://other.test"}).status_code, 403)
            self.assertEqual(client.post(url, data="{}").status_code, 415)
            self.assertEqual(client.post(url, json=[]).status_code, 400)
            self.body["proposed"]["notes"] = "<script>alert('bad')</script> is not executed."
            self.assertEqual(client.post(url, json=self.body).status_code, 201)
            page = client.get("/diagnostics/ai-models")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Candidate leads on this sample", page.data)
            self.assertNotIn(b"<script>alert('bad')</script>", page.data)
            self.assertIn(b"model-comparison-form", page.data)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        from hub import extensions
        for engine in extensions._engines.values():
            engine.dispose()
        TMP.cleanup()
