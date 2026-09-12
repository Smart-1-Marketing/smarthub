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


class QueueTests(unittest.TestCase):
    def setUp(self):
        from hub import ai_comparison_queue as queue
        from hub.config import settings
        self.q = queue
        self.env = patch.dict(os.environ, {"HUB_SCHEDULER": "1", "AI_COMPARISON_MONTHLY_USD": "5"})
        self.env.start()
        self.key = patch("hub.config.settings", replace(settings, openai_key="test-key"))
        self.key.start()
        self.fresh = patch.object(queue, "rates_current", return_value=True)
        self.fresh.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.key.stop)
        self.addCleanup(self.fresh.stop)
        with queue._engine().begin() as conn:
            conn.execute(queue.jobs.delete())
            conn.execute(queue.budgets.delete())

    def body(self):
        from uuid import uuid4
        return {"request_id": str(uuid4()), "profile": "commercial.text",
                "active_model": ai_models.model("commercial.text"), "candidate": "gpt-5.6-terra",
                "brief": "offer-15-v1", "budget_usd": "0.10", "confirm_spend": True}

    def test_reserves_once_and_cancel_releases_only_queued(self):
        body = self.body()
        row = self.q.enqueue(body, "admin@test")
        self.assertEqual(self.q.enqueue(body, "admin@test")["id"], row["id"])
        self.assertEqual(len(self.q.snapshot()["jobs"]), 1)
        self.assertEqual(self.q.snapshot()["committed_usd"], row["reserved_usd"])
        changed = dict(body, candidate="gpt-5.6-sol")
        with self.assertRaises(ValueError):
            self.q.enqueue(changed, "admin@test")
        self.q.cancel(row["id"])
        self.assertEqual(self.q.snapshot()["committed_usd"], 0)
        with self.assertRaises(ValueError):
            self.q.cancel(row["id"])

    def test_budget_rejection_rolls_back_job(self):
        with patch.dict(os.environ, {"AI_COMPARISON_MONTHLY_USD": "0.001"}):
            with self.assertRaises(ValueError):
                self.q.enqueue(self.body(), "admin@test")
        self.assertEqual(self.q.snapshot()["jobs"], [])
        for patch_body in ({"budget_usd": "NaN"}, {"budget_usd": 0.001}, {"candidate": "gpt-unpriced"}, {"confirm_spend": False}):
            with self.subTest(patch_body=patch_body), self.assertRaises(ValueError):
                self.q.enqueue(dict(self.body(), **patch_body), "admin@test")

    def test_stale_pricing_blocks_paid_queue(self):
        with patch.object(self.q, "rates_current", return_value=False), self.assertRaises(ValueError):
            self.q.enqueue(self.body(), "admin@test")

    @staticmethod
    def answer():
        return {"status": "completed", "model": "gpt-4o-mini", "id": "resp-test",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "Cedar Auto $29. Book today. Offer ends Friday."}]}]}

    def test_worker_generates_pair_once_and_records_real_usage(self):
        before = ai_models.model("commercial.text")
        self.q.enqueue(self.body(), "admin@test")
        with patch.object(self.q, "_post", side_effect=[{"input_tokens": 100}, self.answer(), {"input_tokens": 100}, dict(self.answer(), model="gpt-5.6-terra")]) as post:
            self.q.run_one()
            self.assertEqual(self.q.run_one(), {"claimed": 0})
            self.assertEqual(post.call_count, 4)
            self.assertEqual(post.call_args_list[1].args[1]["max_output_tokens"], 2048)
        row = self.q.snapshot()["jobs"][0]
        self.assertEqual(row["state"], "completed")
        self.assertEqual(len(row["results"]), 2)
        self.assertGreater(row["results"][0]["estimated_cost_usd"], 0)
        self.assertEqual(ai_models.model("commercial.text"), before)

    def test_unknown_outcome_preserves_first_result_and_reservation(self):
        import requests
        row = self.q.enqueue(self.body(), "admin@test")
        with patch.object(self.q, "_post", side_effect=[{"input_tokens": 100}, self.answer(), {"input_tokens": 100}, requests.Timeout("SECRET")]):
            self.q.run_one()
        saved = self.q.snapshot()["jobs"][0]
        self.assertEqual(saved["state"], "needs_attention")
        self.assertEqual(len(saved["results"]), 1)
        self.assertNotIn("SECRET", saved["error"])
        self.assertEqual(self.q.snapshot()["committed_usd"], row["reserved_usd"])
        with patch.object(self.q, "_post", side_effect=AssertionError("must not retry")):
            self.assertEqual(self.q.run_one(), {"claimed": 0})

    def test_preflight_limit_and_partial_response_stop_pair(self):
        self.q.enqueue(self.body(), "admin@test")
        with patch.object(self.q, "_post", return_value={"input_tokens": 5000}) as post:
            self.q.run_one()
            self.assertEqual(post.call_count, 1)
        self.q.enqueue(self.body(), "admin@test")
        with patch.object(self.q, "_post", side_effect=[{"input_tokens": 100}, dict(self.answer(), status="incomplete")]) as post:
            self.q.run_one()
            self.assertEqual(post.call_count, 2)
        self.assertTrue(all(r["state"] == "needs_attention" for r in self.q.snapshot()["jobs"]))

    def test_crashed_worker_is_not_replayed(self):
        self.q.enqueue(self.body(), "admin@test")
        with self.q._engine().begin() as conn:
            conn.execute(self.q.jobs.update().values(state="running", updated=0))
        with patch.object(self.q, "_post", side_effect=AssertionError("must not replay")):
            self.assertEqual(self.q.run_one(), {"claimed": 0})
        self.assertEqual(self.q.snapshot()["jobs"][0]["state"], "needs_attention")

    def test_concurrent_reservations_cannot_overspend(self):
        from concurrent.futures import ThreadPoolExecutor
        def submit(body):
            try:
                return self.q.enqueue(body, "admin@test")["id"]
            except ValueError:
                return None
        with patch.dict(os.environ, {"AI_COMPARISON_MONTHLY_USD": "0.04"}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, [self.body(), self.body()]))
            self.assertEqual(sum(r is not None for r in results), 1)
            self.assertLessEqual(self.q.snapshot()["committed_usd"], 0.04)

    def test_concurrent_duplicate_request_reserves_once(self):
        from concurrent.futures import ThreadPoolExecutor
        body = self.body()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _: self.q.enqueue(body, "admin@test"), range(2)))
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        self.assertEqual(self.q.snapshot()["committed_usd"], rows[0]["reserved_usd"])

    def test_paid_routes_admin_guard_and_page(self):
        app = Flask(__name__, template_folder=str(Path(__file__).parent / "hub/templates"))
        app.register_blueprint(bp)
        app.jinja_env.globals.update(current_user=lambda: None, render_sidebar=lambda **kw: "")
        client = app.test_client()
        url = "/api/diagnostics/ai-models/jobs"
        with patch("hub.users_routes.current_account", return_value=None):
            self.assertEqual(client.post(url, json=self.body()).status_code, 401)
        with patch("hub.users_routes.current_account", return_value=SimpleNamespace(is_admin=False)):
            self.assertEqual(client.post(url, json=self.body()).status_code, 403)
        with patch("hub.users_routes.current_account", return_value=SimpleNamespace(is_admin=True, email="admin@test")):
            self.assertEqual(client.post(url, json=self.body(), headers={"Origin": "https://wrong.test"}).status_code, 403)
            response = client.post(url, json=self.body())
            self.assertEqual(response.status_code, 202)
            self.assertEqual(client.post(url + "/cancel", json={"id": response.json["id"]}).status_code, 200)
            page = client.get("/diagnostics/ai-models")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"comparison-queue-form", page.data)

    def test_two_workers_cannot_execute_same_job(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        entered, release = Event(), Event()
        self.q.enqueue(self.body(), "admin@test")
        def generate(model, payload, key):
            entered.set()
            self.assertTrue(release.wait(5))
            return {"model": model, "complete": True}
        with patch.object(self.q, "_generate", side_effect=generate) as call:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.q.run_one)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(pool.submit(self.q.run_one).result(5), {"claimed": 0})
                finally:
                    release.set()
                self.assertEqual(first.result(5)["claimed"], 1)
            self.assertEqual(call.call_count, 2)

    def test_changed_active_model_blocks_before_provider_request(self):
        self.q.enqueue(self.body(), "admin@test")
        with patch.dict(os.environ, {"COMMERCIAL_OPENAI_MODEL": "gpt-4o"}), patch.object(self.q, "_post") as post:
            self.q.run_one()
            post.assert_not_called()
        self.assertEqual(self.q.snapshot()["jobs"][0]["state"], "needs_attention")

    def test_http_errors_do_not_retry_or_echo_provider_body(self):
        with patch("requests.post", return_value=Mock(status_code=429, text="SECRET")) as post:
            with self.assertRaises(self.q.QueueError) as caught:
                self.q._post("responses", {}, "test-key")
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        from hub import extensions
        for engine in extensions._engines.values():
            engine.dispose()
        TMP.cleanup()
