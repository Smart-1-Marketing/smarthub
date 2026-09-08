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


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        from hub import extensions
        for engine in extensions._engines.values():
            engine.dispose()
        TMP.cleanup()
