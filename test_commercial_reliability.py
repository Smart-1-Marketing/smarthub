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
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], usage=None)
        with patch.object(openai_service, "_client", return_value=client), patch.object(openai_service, "profile_model", return_value="gpt-6-astra"):
            self.assertEqual(openai_service._chat_json("Return JSON", "test"), {"ok":True})
        kwargs = client.chat.completions.create.call_args.kwargs
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
