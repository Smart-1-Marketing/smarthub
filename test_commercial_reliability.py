"""Commercial Builder success/error contracts. Synthetic providers, isolated SQLite.

Run: python test_commercial_reliability.py
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

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
from modules.commercial_builder.services import heygen_service, media_state, creatomate_service, openai_service
from modules.commercial_builder.routes import heygen, voices

app = Flask(__name__)
app.config.update(TESTING=True, SECRET_KEY="test", SQLALCHEMY_DATABASE_URI=os.environ["DATABASE_URL"])
db.init_app(app)
# Only this synthetic app is unguarded; production blueprint/auth code is unchanged.
with patch.object(builder, "_hub_auth", None):
    app.register_blueprint(builder.create_blueprint())


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

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
        self.context.pop()

    def start(self, **overrides):
        body = dict(avatar_id="heygen-avatar", voice_id="heygen-voice", voice_provider="heygen", over_footage=False)
        body.update(overrides)
        with patch.object(heygen_service, "is_live", return_value=True), patch.object(heygen_service, "voice_available", return_value=True):
            return self.http.post(self.scene_url + "/spokesperson", json=body)

    def ready(self, duration=10):
        self.scene.asset_type = "spokesperson"
        self.scene.asset_url = "https://example.test/presenter.mp4"
        self.scene.asset_meta = {"spokesperson_url": self.scene.asset_url, "spokesperson_mirrored": True,
            "heygen_job": {"job_id": "paid-job", "status": "completed", "duration": duration,
                           "speech_signature": media_state.speech_signature(self.scene.to_dict())}}
        db.session.commit()

    def test_successful_scene_audio_is_json_safe(self):
        with patch.object(voices.elevenlabs_service, "generate_voiceover", return_value={"audio_bytes": b"ID3audio", "duration_estimate": 2}), \
             patch.object(voices.cloudinary_service, "upload_asset", return_value={"secure_url": "https://example.test/audio.mp3"}):
            r = self.http.post(self.scene_url + "/voiceover", json={"voice_id": "eleven-voice"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("audio_bytes", r.get_json()["voiceover"])
        self.assertEqual(self.scene.asset_meta["voiceover"]["audio_url"], "https://example.test/audio.mp3")

    def test_full_voice_success_returns_json_and_saved_track(self):
        self.scene.is_cta = True
        db.session.commit()
        with patch.object(voices.elevenlabs_service, "generate_voiceover", return_value={"audio_bytes": b"ID3audio", "duration_estimate": 2}) as generate, \
             patch.object(voices.cloudinary_service, "upload_asset", return_value={"secure_url": "https://example.test/audio.mp3"}):
            r = self.http.post(self.base + "/voiceover/full", json={"voice_id": "eleven-voice"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("audio_bytes", r.get_json()["voiceover"])
        self.assertTrue(self.project.music["voice_signature"])
        self.assertIn(self.scene.narration, generate.call_args.kwargs["text"])

    def test_provider_voice_namespace_is_required(self):
        with patch.object(heygen_service, "generate_spokesperson_clip") as generate:
            r = self.start(voice_provider="elevenlabs")
        self.assertEqual(r.status_code, 400)
        generate.assert_not_called()

    def test_duplicate_pending_generation_never_spends_twice(self):
        with patch.object(heygen_service, "generate_spokesperson_clip", return_value={"job_id":"paid-job", "status":"processing"}) as generate:
            self.assertEqual(self.start().status_code, 200)
            self.assertEqual(self.start().status_code, 409)
        self.assertEqual(generate.call_count, 1)

    def test_existing_heygen_take_keeps_its_legacy_request_key(self):
        self.ready()
        meta = dict(self.scene.asset_meta)
        meta["heygen_job"]["request_key"] = media_state.fingerprint({"avatar":"heygen-avatar", "voice":"heygen-voice",
            "speech":media_state.speech_signature(self.scene.to_dict()), "format":"16:9", "over_footage":False})
        self.scene.asset_meta = meta
        db.session.commit()
        with patch.object(heygen_service, "generate_spokesperson_clip") as generate:
            response = self.start()
            self.assertTrue(response.json["reused"])
            generate.assert_not_called()

    def test_customer_pronunciation_change_creates_fresh_presenter_speech(self):
        from hub import customer_voices
        from modules.radio_promo import voices as tts
        with patch.object(customer_voices, "records", return_value=[{"voice_id":"customer-voice", "status":"ready"}]), \
             patch.object(tts, "ready", return_value=True), \
             patch.object(tts, "render_audio", return_value={"audio":b"audio"}) as synthesize, \
             patch.object(heygen.cloudinary_service, "is_live", return_value=True), \
             patch.object(heygen.cloudinary_service, "upload_asset", return_value={"secure_url":"https://example.test/customer.mp3"}), \
             patch.object(heygen_service, "generate_spokesperson_clip", return_value={"status":"completed", "job_id":"done"}):
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice").status_code, 200)
            self.client.pronunciation_dict = {"Test":"Tess"}
            db.session.commit()
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice").status_code, 200)
            self.assertEqual(synthesize.call_count, 2)
            self.assertIn("Tess", synthesize.call_args.args[1])

    def test_customer_voice_presenter_synthesizes_once_and_reuses_uploaded_audio(self):
        from hub import customer_voices
        from modules.radio_promo import voices as tts
        with patch.object(customer_voices, "records", return_value=[{"voice_id":"customer-voice", "status":"ready"}]), \
             patch.object(tts, "ready", return_value=True), \
             patch.object(tts, "render_audio", return_value={"audio":b"audio"}) as synthesize, \
             patch.object(heygen.cloudinary_service, "is_live", return_value=True), \
             patch.object(heygen.cloudinary_service, "upload_asset", return_value={"secure_url":"https://example.test/customer.mp3"}), \
             patch.object(heygen_service, "generate_spokesperson_clip", return_value={"status":"failed", "error":"Rejected"}) as generate:
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice").status_code, 502)
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice").status_code, 502)
            synthesize.assert_called_once()
            self.assertEqual(generate.call_args.kwargs["audio_url"], "https://example.test/customer.mp3")
            self.assertIsNone(generate.call_args.args[2])

    def test_failed_customer_speech_preserves_previous_clip_and_requires_explicit_retry(self):
        from hub import customer_voices
        from modules.radio_promo import voices as tts
        self.ready()
        old_url = self.scene.asset_url
        with patch.object(customer_voices, "records", return_value=[{"voice_id":"customer-voice", "status":"ready"}]), \
             patch.object(tts, "ready", return_value=True), \
             patch.object(tts, "render_audio", side_effect=tts.VoiceError("Timeout")) as synthesize, \
             patch.object(heygen.cloudinary_service, "is_live", return_value=True), \
             patch.object(heygen_service, "generate_spokesperson_clip") as generate:
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice").status_code, 502)
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice").status_code, 409)
            self.assertEqual(self.scene.asset_url, old_url)
            self.assertEqual(self.scene.asset_meta["heygen_job"]["job_id"], "paid-job")
            self.assertEqual(self.start(voice_provider="customer", voice_id="customer-voice", regenerate=True).status_code, 502)
            self.assertEqual(synthesize.call_count, 2)
            generate.assert_not_called()

    def test_rejected_retake_keeps_existing_asset(self):
        self.ready()
        old_url = self.scene.asset_url
        with patch.object(heygen_service, "generate_spokesperson_clip", return_value={"status":"failed", "error":"provider refused"}):
            self.assertEqual(self.start(regenerate=True).status_code, 502)
        self.assertEqual(self.scene.asset_url, old_url)
        self.assertEqual(self.scene.asset_meta["heygen_job"]["job_id"], "paid-job")

    def test_status_timeout_is_retryable(self):
        with patch.object(heygen_service, "is_live", return_value=True), patch.object(heygen_service.requests, "get", side_effect=requests.Timeout):
            result = heygen_service.check_status("paid-job")
        self.assertEqual(result["status"], "processing")
        self.assertTrue(result["retryable"])

    def test_stale_provider_response_cannot_overwrite_newer_job(self):
        self.scene.asset_meta = {"heygen_job": {"job_id":"old", "status":"processing"}}
        db.session.commit()
        def finish_old(_):
            self.scene.asset_meta = {"heygen_job": {"job_id":"new", "status":"processing"}}
            db.session.commit()
            return {"job_id":"old", "status":"completed", "video_url":"https://example.test/old.mp4"}
        with patch.object(heygen_service, "check_status", side_effect=finish_old), patch.object(heygen.cloudinary_service, "upload_asset") as upload:
            self.http.get(self.scene_url + "/spokesperson/status")
        self.assertEqual(self.scene.asset_meta["heygen_job"]["job_id"], "new")
        upload.assert_not_called()

    def test_edit_during_storage_keeps_stale_flag(self):
        self.scene.asset_meta = {"heygen_job": {"job_id":"paid-job", "status":"processing"}}
        db.session.commit()
        def store(*args, **kwargs):
            self.scene.narration = "A different offer arrived while rendering."
            db.session.commit()
            return {"secure_url":"https://example.test/stored.mp4"}
        with patch.object(heygen_service, "check_status", return_value={"job_id":"paid-job", "status":"completed", "video_url":"https://example.test/old.mp4"}), patch.object(heygen.cloudinary_service, "upload_asset", side_effect=store):
            self.http.get(self.scene_url + "/spokesperson/status")
        self.assertTrue(self.scene.asset_meta["presenter_stale"])

    def test_full_voice_storage_failure_is_not_success(self):
        with patch.object(voices.elevenlabs_service, "generate_voiceover", return_value={"audio_bytes":b"ID3audio"}), patch.object(voices.cloudinary_service, "upload_asset", return_value={"error":"offline"}):
            response = self.http.post(self.base + "/voiceover/full", json={"voice_id":"eleven-voice"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("audio_bytes", response.get_json()["voiceover"])

    def test_legacy_poll_failure_can_recover_and_persists_url(self):
        self.scene.asset_meta = {"heygen_job": {"job_id":"paid-job", "status":"failed", "error":"timeout"}}
        self.scene.asset_type = "spokesperson"
        self.scene.asset_url = None
        db.session.commit()
        with patch.object(heygen_service, "check_status", return_value={"job_id":"paid-job", "status":"completed", "video_url":"https://example.test/signed.mp4", "duration":10}), \
             patch.object(heygen.cloudinary_service, "upload_asset", return_value={"secure_url":"https://example.test/stored.mp4"}):
            result = self.http.get(self.scene_url + "/spokesperson/status")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.scene.asset_url, "https://example.test/stored.mp4")
        self.assertTrue(self.scene.asset_meta["spokesperson_mirrored"])

    def test_storage_failure_can_retry_without_new_generation(self):
        self.scene.asset_type = "spokesperson"
        self.scene.asset_url = None
        self.scene.asset_meta = {"heygen_job":{"job_id":"paid-job", "status":"processing"}}
        db.session.commit()
        with patch.object(heygen_service, "check_status", return_value={"job_id":"paid-job", "status":"completed", "video_url":"https://example.test/signed.mp4", "duration":10}), \
             patch.object(heygen.cloudinary_service, "upload_asset", side_effect=[{"error":"offline"}, {"secure_url":"https://example.test/stored.mp4"}]):
            first = self.http.get(self.scene_url + "/spokesperson/status").get_json()
            self.assertTrue(first["storage_pending"])
            self.assertFalse(media_state.integrity(self.project.to_dict(), [self.scene.to_dict()])["passed"])
            second = self.http.get(self.scene_url + "/spokesperson/status").get_json()
        self.assertFalse(second["storage_pending"])
        self.assertEqual(self.scene.asset_url, "https://example.test/stored.mp4")

    def test_narration_edit_marks_legacy_presenter_and_voice_stale(self):
        self.ready()
        self.project.music = {"voice_track_url":"https://example.test/old.mp3"}
        db.session.commit()
        r = self.http.put(self.scene_url, json={"narration":"A different offer."})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.scene.asset_meta["presenter_stale"])
        self.assertTrue(self.project.music["voice_track_stale"])

    def test_short_and_long_presenters_are_blocked(self):
        for duration in (2, 15):
            self.ready(duration)
            self.assertFalse(media_state.integrity(self.project.to_dict(), [self.scene.to_dict()])["passed"])

    def test_hard_media_failure_cannot_be_overridden(self):
        self.scene.asset_url = None
        db.session.commit()
        with patch("modules.commercial_builder.routes.render.creatomate_service.submit_render") as render:
            r = self.http.post(self.base + "/render", json={"format":"16:9", "force_despite_qc_failures":True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("scene_assets", r.get_json()["hard_failures"])
        render.assert_not_called()

    def test_mixed_voiceover_only_generates_non_presenter_speech(self):
        self.ready()
        other = Scene(project_id=self.project.id, order_index=1, start=10, end=20, narration="Visit our store.")
        db.session.add(other)
        db.session.commit()
        with patch.object(voices.elevenlabs_service, "generate_voiceover", return_value={"audio_bytes": b"ID3audio", "duration_estimate":2}) as generate, \
             patch.object(voices.cloudinary_service, "upload_asset", return_value={"secure_url":"https://example.test/other.mp3"}):
            r = self.http.post(self.base + "/voiceover/full", json={"voice_id":"eleven-voice"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(generate.call_args.kwargs["text"], "Visit our store.")
        self.assertEqual(generate.call_count, 1)
        source = creatomate_service.build_source(self.project.to_dict(), [self.scene.to_dict(), other.to_dict()], "16:9")
        audio = [e for e in source["elements"] if e["type"] == "audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]["time"], 10)
        self.assertNotIn("voice_track_url", self.project.music)

    def test_background_is_muted_and_project_title_is_private(self):
        source = creatomate_service.build_source({"length_seconds":10}, [self.scene.to_dict()], "16:9")
        self.assertEqual(source["elements"][0]["volume"], "0%")
        elements = creatomate_service._cta_overlay_elements({"client":{"name":"Test Business"}}, {"title":"Internal revision 7"}, {}, "ctv")
        self.assertEqual(elements[0]["text"], "Test Business")

    def test_astra_parameters_preserve_selected_profile(self):
        # openai_service._chat_json now routes through hub.ai.chat_json(),
        # which is the one wrapper -- so the reasoning-model payload shape
        # (max_completion_tokens/reasoning_effort, no temperature/max_tokens)
        # is asserted on the request hub.ai actually sends, by mocking
        # hub.ai._post rather than an SDK client this module no longer builds.
        from hub import ai as hub_ai
        seen = {}

        def _fake_post(path, payload, timeout):
            seen["payload"] = payload
            return {"choices": [{"message": {"content": '{"ok": true}'},
                                 "finish_reason": "stop"}], "usage": {}}

        with patch.object(hub_ai, "_post", side_effect=_fake_post), \
             patch.object(hub_ai, "ready", return_value=True), \
             patch.object(openai_service, "profile_model", return_value="gpt-6-astra"):
            self.assertEqual(openai_service._chat_json("Return JSON", "test"), {"ok": True})
        kwargs = seen["payload"]
        self.assertEqual(kwargs["model"], "gpt-6-astra")
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("max_tokens", kwargs)
        self.assertEqual(kwargs["reasoning_effort"], "low")

    def test_narration_requires_actual_audio(self):
        result = media_state.integrity(self.project.to_dict(), [self.scene.to_dict()])
        self.assertFalse(result["passed"])
        self.assertIn("narration", result["message"])

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


class ProductionTests(unittest.TestCase):
    setUp = ReliabilityTests.setUp
    tearDown = ReliabilityTests.tearDown
    ready = ReliabilityTests.ready

    def test_history_does_not_prevent_repairing_legacy_json(self):
        Scene.query.filter_by(id=self.scene.id).update({"asset_meta_json": "{broken"})
        db.session.commit()
        self.scene.asset_meta = {}
        self.scene.narration = "Repaired script."
        db.session.commit()
        self.assertEqual(self.scene.asset_meta, {})
    def test_script_history_compare_restore_and_conflict(self):
        from modules.commercial_builder.models import ProductionTake
        original = self.scene.narration
        self.scene.narration = "A different offer."
        db.session.commit()
        takes = self.http.get(self.base + "/takes").get_json()["takes"]
        old = next(t for t in takes if t["snapshot"].get("narration") == original)
        self.assertEqual(old["current"]["narration"], "A different offer.")
        self.scene.narration = "An intervening edit."
        db.session.commit()
        url = self.base + f"/takes/{old['id']}/restore"
        self.assertEqual(self.http.post(url, json={"current_digest": old["current_digest"]}).status_code, 409)
        fresh = next(t for t in self.http.get(self.base + "/takes").get_json()["takes"] if t["id"] == old["id"])
        self.assertEqual(self.http.post(url, json={"current_digest": fresh["current_digest"]}).status_code, 200)
        self.assertEqual(self.scene.narration, original)
        self.assertEqual(ProductionTake.query.filter_by(kind="script").count(), 3)

    def test_presenter_preserved_before_paid_reservation(self):
        from modules.commercial_builder.models import ProductionTake
        self.ready()
        original = self.scene.asset_url
        ProductionTake.query.delete()  # Simulate a take saved before history shipped.
        db.session.commit()
        expected = self.scene.asset_meta_json
        self.assertTrue(heygen._claim(self.scene, {"heygen_job": {"job_id": "new-job", "status": "processing"}}, expected))
        saved = ProductionTake.query.filter_by(kind="presenter").all()
        self.assertTrue(any(t.snapshot["spokesperson_url"] == original for t in saved))

    def test_deleted_scene_history_remains_readable(self):
        self.scene.narration = "Revised narration."
        db.session.commit()
        db.session.delete(self.scene)
        db.session.commit()
        takes = self.http.get(self.base + "/takes").get_json()["takes"]
        self.assertTrue(takes)
        self.assertTrue(all(not t["restorable"] for t in takes))
        replacement = Scene(project_id=self.project.id, narration="A replacement scene.", order_index=0)
        db.session.add(replacement)
        db.session.commit()
        takes = self.http.get(self.base + "/takes").get_json()["takes"]
        self.assertTrue(all(not t["restorable"] for t in takes))

    def test_history_is_project_scoped(self):
        self.scene.narration = "Another version."
        db.session.commit()
        take = self.http.get(self.base + "/takes").get_json()["takes"][0]
        other = CommercialProject(client_id=self.client.id, title="Other")
        db.session.add(other)
        db.session.commit()
        url = f"/tools/commercial-builder/api/projects/{other.id}/takes/{take['id']}/restore"
        self.assertEqual(self.http.post(url, json={"current_digest": take["current_digest"]}).status_code, 404)

    def test_restored_voice_stays_stale_for_changed_script(self):
        meta = dict(self.scene.asset_meta or {})
        meta["voiceover"] = {"audio_url": "https://example.test/old.mp3", "speech_signature": media_state.speech_signature(self.scene.to_dict())}
        self.scene.asset_meta = meta
        db.session.commit()
        self.scene.narration = "New words."
        self.scene.asset_meta = {"voiceover": {"audio_url": "https://example.test/new.mp3", "speech_signature": media_state.speech_signature(self.scene.to_dict())}}
        db.session.commit()
        old = next(t for t in self.http.get(self.base + "/takes").get_json()["takes"] if t["kind"] == "voice" and t["snapshot"]["audio_url"].endswith("old.mp3"))
        result = self.http.post(self.base + f"/takes/{old['id']}/restore", json={"current_digest": old["current_digest"]})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(self.scene.asset_meta["voiceover"]["stale"])

    def test_recovery_respects_backoff_and_never_generates(self):
        from modules.commercial_builder import recovery
        self.scene.asset_meta = {"heygen_job": {"job_id": "paid", "status": "processing"}}
        db.session.commit()
        with patch.object(recovery, "spokesperson_status", side_effect=RuntimeError("offline")) as poll:
            first = recovery.recover_pending()
            second = recovery.recover_pending()
        self.assertEqual(first["errors"], 1)
        self.assertEqual(second["checked"], 0)
        self.assertEqual(poll.call_count, 1)

    def test_recovery_finishes_render_without_browser(self):
        from modules.commercial_builder import recovery
        job = RenderJob(project_id=self.project.id, provider_render_id="existing", status="rendering")
        db.session.add(job)
        db.session.commit()
        def finish(saved):
            saved.status = "succeeded"
            saved.output_url = "https://example.test/final.mp4"
            db.session.commit()
        with patch.object(recovery, "poll_render_job", side_effect=finish) as poll:
            result = recovery.recover_pending()
        self.assertEqual(result["checked"], 1)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(poll.call_count, 1)

    def test_late_render_poll_cannot_overwrite_completed_cut(self):
        from modules.commercial_builder.generation import poll_render_job
        job = RenderJob(project_id=self.project.id, provider_render_id="existing", status="rendering")
        db.session.add(job)
        db.session.commit()
        def another_poll_finished(_provider_id):
            job.status = "succeeded"
            job.output_url = "https://example.test/finished.mp4"
            db.session.commit()
            return {"status": "queued"}
        with patch.object(creatomate_service, "check_render", side_effect=another_poll_finished):
            poll_render_job(job)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.output_url, "https://example.test/finished.mp4")

    def test_background_storage_recovery_attaches_existing_paid_clip(self):
        from modules.commercial_builder import recovery
        self.ready()
        meta = dict(self.scene.asset_meta)
        meta["spokesperson_mirrored"] = False
        self.scene.asset_meta = meta
        db.session.commit()
        with patch.object(heygen_service, "check_status", return_value={"status": "completed", "video_url": "https://example.test/signed.mp4", "duration": 10}), \
             patch.object(heygen.cloudinary_service, "upload_asset", return_value={"secure_url": "https://example.test/permanent.mp4"}):
            result = recovery.recover_pending()
        self.assertEqual(result["checked"], 1)
        self.assertTrue(self.scene.asset_meta["spokesperson_mirrored"])
        self.assertEqual(self.scene.asset_url, "https://example.test/permanent.mp4")

    def test_recovery_lease_blocks_duplicate_worker(self):
        import time
        from modules.commercial_builder.recovery import _claim
        now = time.time()
        self.assertTrue(_claim("render:123", now))
        self.assertFalse(_claim("render:123", now))

    def test_recovery_does_not_starve_unvisited_jobs(self):
        import time
        from flask import jsonify
        from modules.commercial_builder import recovery
        from modules.commercial_builder.models import RecoveryAttempt
        self.scene.asset_meta = {"heygen_job": {"job_id": "paid", "status": "processing"}}
        db.session.commit()
        with patch.object(recovery, "spokesperson_status", side_effect=lambda *args: jsonify(status="processing")):
            recovery.recover_pending(limit=1)
        attempt = RecoveryAttempt.query.first()
        attempt.next_at = time.time() - 1
        job = RenderJob(project_id=self.project.id, provider_render_id="unvisited", status="rendering")
        db.session.add(job)
        db.session.commit()
        with patch.object(recovery, "poll_render_job") as render, patch.object(recovery, "spokesperson_status") as presenter:
            recovery.recover_pending(limit=1)
        render.assert_called_once()
        presenter.assert_not_called()

    def test_full_track_history_restores_without_generation(self):
        first = {"voice_track_url": "https://example.test/first.mp3", "voice_mode": "full",
                 "voice_signature": media_state.timeline_signature([self.scene.to_dict()])}
        self.project.music = first
        db.session.commit()
        self.project.music = {**first, "voice_track_url": "https://example.test/second.mp3"}
        db.session.commit()
        take = next(t for t in self.http.get(self.base + "/takes").get_json()["takes"]
                    if t["kind"] == "track" and t["snapshot"]["voice_track_url"].endswith("first.mp3"))
        response = self.http.post(self.base + f"/takes/{take['id']}/restore", json={"current_digest": take["current_digest"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.music["voice_track_url"], first["voice_track_url"])
        self.assertFalse(self.project.music["voice_track_stale"])

    def test_restore_refuses_active_presenter_and_project_deletion_cascades(self):
        from modules.commercial_builder.models import ProductionTake
        self.scene.narration = "Changed."
        db.session.commit()
        take = self.http.get(self.base + "/takes").get_json()["takes"][-1]
        self.scene.asset_meta = {"heygen_job": {"job_id": "paid", "status": "processing"}}
        db.session.commit()
        response = self.http.post(self.base + f"/takes/{take['id']}/restore", json={"current_digest": take["current_digest"]})
        self.assertEqual(response.status_code, 409)
        db.session.delete(self.project)
        db.session.commit()
        self.assertEqual(ProductionTake.query.count(), 0)

    def test_production_distinguishes_creation_storage_and_unknown(self):
        self.scene.asset_meta = {"heygen_job": {"job_id": "paid", "status": "processing"}}
        db.session.commit()
        self.assertEqual(self.http.get(self.base + "/production").get_json()["label"], "Creating presenter clips")
        self.ready()
        meta = dict(self.scene.asset_meta)
        meta["spokesperson_mirrored"] = False
        self.scene.asset_meta = meta
        db.session.commit()
        self.assertEqual(self.http.get(self.base + "/production").get_json()["label"], "Saving media")
        self.scene.asset_meta = {"heygen_job": {"status": "unknown"}}
        db.session.commit()
        self.assertEqual(self.http.get(self.base + "/production").get_json()["label"], "Needs attention")


if __name__ == "__main__":
    unittest.main(verbosity=2)
