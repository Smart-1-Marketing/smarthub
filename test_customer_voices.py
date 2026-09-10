"""Exercise the real customer library API without provider calls or customer data."""
import io
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch, Mock
from types import SimpleNamespace

from flask import Flask
from hub import customer_voices as cv
from modules.radio_promo import voices


class CustomerVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"HUB_DATA_DIR": self.temp.name, "DATABASE_URL": ""})
        self.env.start(); self.addCleanup(self.env.stop)
        self.addCleanup(lambda: cv.jsonstore._engine.dispose() if cv.jsonstore._engine is not None else None)
        self.auth = patch("hub.auth.user_from_environ", return_value="staff@example.test")
        self.auth.start(); self.addCleanup(self.auth.stop)
        app = Flask(__name__, template_folder="hub/templates")
        app.register_blueprint(cv.bp)
        app.testing = True
        self.client = app.test_client()
        self.clone = patch.object(voices, "clone_voice", return_value={"voice_id":"customer123", "requires_verification":False})
        self.provider = self.clone.start(); self.addCleanup(self.clone.stop)

    def submit(self, request_id=None, **changes):
        data = dict(name="Jane", client="Example Company", authorized="true",
                    request_id=request_id or str(uuid.uuid4()), files=(io.BytesIO(b"sample"), "voice.wav"))
        data.update(changes)
        if data.get("voice_id") or data.get("capture_id"):
            data.pop("files", None)
        return self.client.post("/api/customer-voices", data=data)

    def test_authenticated_page_and_api(self):
        self.assertEqual(self.client.get("/tools/customer-voices/").status_code, 200)
        self.auth.stop()
        self.assertEqual(self.client.get("/tools/customer-voices/").status_code, 302)
        self.assertEqual(self.client.get("/api/customer-voices").status_code, 401)
        self.assertEqual(self.submit().status_code, 401)
        self.provider.assert_not_called()

    def test_library_is_shared_durable_and_samples_are_not_retained(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get("/api/customer-voices?ready=1").json["voices"][0]["voice_id"], "customer123")
        self.assertEqual(cv.records()[0]["permission_by"], "staff@example.test")
        with open(cv._path(), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("sample", text)
        self.assertNotIn("voice.wav", text)
        cv.ensure_usable("customer123")
        cv.ensure_usable("stock_voice")

    def test_permission_formats_count_and_size_validated_before_provider(self):
        for changes in ({"authorized":"false"}, {"name":""}, {"client":""},
                        {"files":(io.BytesIO(b"sample"), "script.txt")},
                        {"files":(io.BytesIO(b""), "empty.wav")},
                        {"files":[(io.BytesIO(b"sample"), f"{i}.wav") for i in range(6)]}):
            self.assertEqual(self.submit(**changes).status_code, 400)
        with patch.object(cv, "MAX_FILE", 2):
            self.assertEqual(self.submit().status_code, 400)
        self.provider.assert_not_called()

    def test_verification_blocks_synthesis_until_provider_confirms(self):
        self.provider.return_value["requires_verification"] = True
        record = self.submit().json["voice"]
        self.assertEqual(self.client.get("/api/customer-voices?ready=1").json["voices"], [])
        with self.assertRaises(cv.LibraryError):
            cv.ensure_usable("customer123")
        with patch.object(voices, "get_voice", return_value={"requires_verification":False, "preview_url":"https://example.test/sample.mp3"}):
            self.assertEqual(self.client.post(f"/api/customer-voices/{record['id']}/refresh").json["voice"]["status"], "ready")
        cv.ensure_usable("customer123")

    def test_replay_does_not_create_another_clone(self):
        key = str(uuid.uuid4())
        self.assertEqual(self.submit(key).status_code, 201)
        self.assertEqual(self.submit(key).status_code, 200)
        self.assertEqual(self.submit(key, name="Different voice").status_code, 409)
        self.assertEqual(self.submit().status_code, 200)  # reopened tab, same recordings
        self.provider.assert_called_once()

    def test_all_synthesis_paths_reject_pending_customer_voice_before_http(self):
        from modules.fan_radio import voices as fan
        from modules.commercial_builder.services import elevenlabs_service as commercial
        self.provider.return_value["requires_verification"] = True
        self.submit()
        with patch.object(voices.requests, "post") as post:
            with self.assertRaises(voices.VoiceError):
                voices.render_audio("customer123", "Hello")
            with self.assertRaises(fan.VoiceError):
                fan.render_audio("customer123", "Hello")
            self.assertIn("not ready", commercial.generate_voiceover("Hello", "customer123")["error"])
            post.assert_not_called()

    def test_presenter_uses_audio_input_without_cross_provider_voice_id(self):
        from modules.commercial_builder.services import heygen_service as heygen
        response = Mock(json=lambda: {"data":{"video_id":"video123"}})
        with patch.object(heygen, "is_live", return_value=True), patch.object(heygen, "_headers", return_value={}), patch.object(heygen, "_meter"), patch.object(heygen.requests, "post", return_value=response) as post:
            result = heygen.generate_spokesperson_clip("avatar", "Hello", audio_url="https://example.test/customer.mp3")
            self.assertEqual(result["status"], "processing")
            self.assertEqual(post.call_args.kwargs["json"]["video_inputs"][0]["voice"], {"type":"audio", "audio_url":"https://example.test/customer.mp3"})

    def test_timeout_reservation_survives_retries(self):
        self.provider.side_effect = voices.VoiceError("Timeout")
        key = str(uuid.uuid4())
        self.assertEqual(self.submit(key).status_code, 502)
        self.assertEqual(self.submit(key).status_code, 409)
        self.provider.assert_called_once()
        self.assertEqual(cv.records()[0]["status"], "needs_review")

    def test_submitted_capture_can_be_cloned_without_reupload(self):
        capture = SimpleNamespace(client_name="Capture customer")
        with patch.object(cv, "_capture_sample", return_value=(capture, ("capture.webm", b"recording", "audio/webm"))):
            response = self.submit(capture_id="7")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["voice"]["client"], "Capture customer")
        self.assertEqual(cv.records()[0]["capture_id"], "7")
        self.assertEqual(self.provider.call_args.args[1][0][1], b"recording")

    def test_capture_import_checks_consent_source_redirects_and_size(self):
        import requests
        from modules.commercial_builder import voice_capture_models
        row = SimpleNamespace(status="submitted", consent=True, revoked=False,
            audio_url="https://res.cloudinary.com/example/video/upload/sample.webm",
            original_filename="sample.webm", mime_type="audio/webm")
        model = SimpleNamespace(query=SimpleNamespace(get=lambda _: row))
        response = Mock(status_code=200, iter_content=lambda _: iter([b"audio"]))
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        with patch.object(voice_capture_models, "VoiceCaptureRequest", model), patch.object(requests, "get", return_value=response) as get:
            self.assertEqual(cv._capture_sample(7)[1][1], b"audio")
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            with patch.object(cv, "MAX_FILE", 2), self.assertRaises(cv.LibraryError):
                cv._capture_sample(7)
            response.status_code = 302
            with self.assertRaises(cv.LibraryError): cv._capture_sample(7)
            get.reset_mock()
            row.consent = False
            with self.assertRaises(cv.LibraryError): cv._capture_sample(7)
            row.consent = True; row.revoked = True
            with self.assertRaises(cv.LibraryError): cv._capture_sample(7)
            row.revoked = False; row.audio_url = "http://127.0.0.1/private"
            with self.assertRaises(cv.LibraryError): cv._capture_sample(7)
            get.assert_not_called()

    def test_existing_voice_is_not_recloned(self):
        with patch.object(voices, "get_voice", return_value={"voice_id":"existing", "requires_verification":False}):
            self.assertEqual(self.submit(voice_id="existing").status_code, 201)
            self.assertEqual(self.submit(voice_id="existing").status_code, 400)
        self.provider.assert_not_called()

    def test_clone_keeps_accepted_id_and_verification_without_followup_http(self):
        self.clone.stop()
        with patch.object(voices, "ready", return_value=True), patch.object(voices, "_headers", return_value={}), patch.object(voices.requests, "post", return_value=Mock(status_code=200, json=lambda:{"voice_id":"created", "requires_verification":True})), patch.object(voices.requests, "get") as get:
            result = voices.clone_voice("Jane", [("sample.wav", b"sample", "audio/wav")])
            self.assertEqual(result["voice_id"], "created")
            self.assertTrue(result["requires_verification"])
            get.assert_not_called()

    def test_rejected_clone_shows_cause_and_can_retry_same_submission(self):
        self.provider.side_effect = voices.VoiceRequestRejected("Voices write permission is required.")
        key = str(uuid.uuid4())
        response = self.submit(key)
        self.assertIn("write permission", response.json["error"])
        self.assertNotIn("uncertain", response.json["error"])
        self.assertEqual(cv.records()[0]["status"], "failed")
        self.provider.side_effect = None
        self.assertEqual(self.submit(key).status_code, 201)
        self.assertEqual(self.provider.call_count, 2)

    def test_confirmed_absent_recovery_is_explicit_and_single_use(self):
        key = str(uuid.uuid4())
        self.provider.side_effect = voices.VoiceError("Timeout")
        self.assertEqual(self.submit(key).status_code, 502)
        self.assertEqual(self.submit(key).status_code, 409)
        confirmation = str(uuid.uuid4())
        self.assertEqual(self.submit(key, recovery_confirmation=confirmation).status_code, 502)
        self.assertEqual(self.submit(key, recovery_confirmation=confirmation).status_code, 409)
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(self.submit(key, recovery_confirmation=str(uuid.uuid4())).status_code, 502)
        self.assertEqual(self.submit(key, recovery_confirmation=confirmation).status_code, 409)
        self.provider.side_effect = None
        self.assertEqual(self.submit(key, recovery_confirmation=str(uuid.uuid4())).status_code, 201)
        self.assertEqual(self.provider.call_count, 4)

    def test_original_failed_tab_cannot_reclone_after_another_tab_succeeds(self):
        key = str(uuid.uuid4())
        self.provider.side_effect = voices.VoiceRequestRejected("Rejected")
        self.assertEqual(self.submit(key).status_code, 502)
        self.provider.side_effect = None
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.submit(key).status_code, 200)
        self.assertEqual(self.provider.call_count, 2)

    def test_failed_import_can_be_retried_and_does_not_block_recovered_voice(self):
        with patch.object(voices, "get_voice", side_effect=voices.VoiceError("No access")):
            self.assertEqual(self.submit(voice_id="existing").status_code, 502)
        with patch.object(voices, "get_voice", return_value={"voice_id":"existing", "requires_verification":False}):
            self.assertEqual(self.submit(voice_id="existing").status_code, 201)
        self.assertEqual(len(cv.records()), 1)
        cv.ensure_usable("existing")
        self.provider.assert_not_called()

    def test_clone_rejection_is_distinct_from_unknown_result(self):
        self.clone.stop()
        response = Mock(status_code=403, json=lambda:{"detail":{"status":"missing_permissions", "message":"secret raw body"}})
        with patch.object(voices, "ready", return_value=True), patch.object(voices, "_headers", return_value={}), patch.object(voices.requests, "post", return_value=response):
            with self.assertRaises(voices.VoiceRequestRejected) as error:
                voices.clone_voice("Jane", [("voice.wav", b"sample", "audio/wav")])
            self.assertIn("Voices write", str(error.exception))
            self.assertNotIn("secret", str(error.exception))
            response.status_code = 503
            with self.assertRaises(voices.VoiceError) as error:
                voices.clone_voice("Jane", [("voice.wav", b"sample", "audio/wav")])
            self.assertNotIsInstance(error.exception, voices.VoiceRequestRejected)
            response.status_code = 200; response.json.side_effect = ValueError("bad JSON")
            with self.assertRaises(voices.VoiceError) as error:
                voices.clone_voice("Jane", [("voice.wav", b"sample", "audio/wav")])
            self.assertNotIsInstance(error.exception, voices.VoiceRequestRejected)

    def test_missing_connection_never_sends_clone_or_creates_uncertain_result(self):
        self.clone.stop()
        with patch.object(voices, "ready", return_value=False), patch.object(voices.requests, "post") as post:
            response = self.submit()
            self.assertIn("not connected", response.json["error"])
            self.assertEqual(cv.records()[0]["status"], "failed")
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
