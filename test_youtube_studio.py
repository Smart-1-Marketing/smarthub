"""Offline behavioral checks for client isolation and YouTube release safety."""
import io
import os
import tempfile
import time
import unittest
import requests
from unittest.mock import patch, Mock
from flask import Flask
from jinja2 import DictLoader
from cryptography.fernet import Fernet

from modules.youtube_studio import register_youtube_studio
from modules.youtube_studio import store, youtube as yt
from modules.youtube_studio.app import oauth_callback, browser_serializer

CID = "UC" + "a" * 22
OTHER = "UC" + "b" * 22


def response(payload=None, code=200, headers=None):
    obj = Mock(status_code=code, ok=200 <= code < 300, headers=headers or {}, content=b"{}")
    obj.json.return_value = payload or {}
    return obj


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"HUB_DATA_DIR": self.tmp.name, "DATABASE_URL": "",
            "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(), "YOUTUBE_API_KEY": "test-key"})
        self.env.start()
        # The shared mirror has its own integration suite. Keep these route
        # tests on real temporary files without leaving SQLite pools open.
        self.mirror_write = patch("hub.jsonstore._upsert", return_value=True)
        self.mirror_read = patch("hub.jsonstore._fetch", return_value=None)
        self.mirror_write.start(); self.mirror_read.start()
        self.auth = patch("hub.auth.user_from_environ", return_value="Test staff")
        self.auth.start()
        self.cfg = patch.object(yt, "oauth_config", return_value=("client", "secret", "https://hub.example/connect/callback"))
        self.cfg.start()
        self.app = Flask(__name__)
        # Match the real Hub, which deliberately leaves Flask sessions unset.
        self.app.config["TESTING"] = True
        self.app.jinja_loader = DictLoader({"base.html": "{% block content %}{% endblock %}"})
        register_youtube_studio(self.app)
        self.app.add_url_rule("/connect/callback", view_func=oauth_callback)
        self.client = self.app.test_client()
        self.client.set_cookie("s1youtube_browser", browser_serializer().dumps({"youtube_csrf": "csrf-test"}))
        store.update(lambda state: store.client(state, "Alpha")["channels"].update({CID: {
            "id": CID, "title": "Alpha channel", "url": "https://www.youtube.com/channel/" + CID}}))

    def tearDown(self):
        self.mirror_write.stop(); self.mirror_read.stop()
        self.cfg.stop(); self.auth.stop(); self.env.stop(); self.tmp.cleanup()

    def post(self, route, data):
        return self.client.post("/tools/youtube/api/" + route, json={"client": "Alpha", **data}, headers={"X-YouTube-CSRF": "csrf-test"})

    def draft(self):
        result = self.post("drafts", {"channel_id": CID, "title": "A useful customer video", "description": "Our source notes"})
        self.assertEqual(result.status_code, 200)
        return result.json["draft_id"]

    def test_google_requests_meter_success_and_failure_without_secrets(self):
        url = yt.ROOT + "upload/private-session?key=secret"
        with patch("hub.quotas.record_google") as meter, patch("requests.get", return_value=response()):
            yt.record_google_request("GET", url)
            meter.assert_called_once_with(yt.ROOT, module="youtube_studio", ok=True)
        with patch("hub.quotas.record_google") as meter, patch("requests.get", side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                yt.record_google_request("GET", url)
            meter.assert_called_once_with(yt.ROOT, module="youtube_studio", ok=False)

    def test_guard_blocks_every_staff_read(self):
        with patch("hub.auth.user_from_environ", return_value=None):
            self.assertEqual(self.client.get("/tools/youtube/api/client?client=Alpha").status_code, 401)
            self.assertEqual(self.client.get("/tools/youtube/").status_code, 302)

    def test_public_invalid_links_fail_without_bypassing_csrf(self):
        before = store.read()
        for action in ("start", "review"):
            self.assertEqual(self.client.post("/connect/youtube/unknown/" + action).status_code, 400)
        self.assertEqual(store.read(), before)
        connect = store.invite("Alpha", CID, "connect")
        review = store.invite("Alpha", CID, "review", self.draft())
        before = store.read()
        for token, action in ((connect, "start"), (review, "review")):
            self.assertEqual(self.client.post("/connect/youtube/" + token + "/" + action).status_code, 403)
        self.assertEqual(store.read(), before)

    def test_csrf_required(self):
        result = self.client.post("/tools/youtube/api/drafts", json={"client": "Alpha"})
        self.assertEqual(result.status_code, 403)

    def test_staff_page_works_without_flask_session_secret(self):
        self.assertIsNone(self.app.secret_key)
        result = self.client.get("/tools/youtube/?client=Alpha")
        self.assertEqual(result.status_code, 200)
        self.assertIn("Accounts &amp; access", result.text.replace("Accounts & access", "Accounts &amp; access"))
        self.assertIn("s1youtube_browser=", result.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", result.headers.get("Set-Cookie", ""))

    def test_parse_accepts_only_channel_urls(self):
        for value in (CID, "https://www.youtube.com/channel/" + CID):
            self.assertEqual(yt.parse_channel(value), ("id", CID))
        self.assertEqual(yt.parse_channel("https://youtube.com/@example/videos"), ("forHandle", "@example"))
        for value in ("https://youtube.com.evil.test/@example", "http://youtube.com/@example", "https://localhost/@example", "https://youtube.com/watch?v=x", "javascript:alert(1)", "https://evil@youtube.com/@example"):
            with self.assertRaises(ValueError):
                yt.parse_channel(value)

    def test_saved_tokens_are_encrypted_and_not_returned(self):
        encrypted = store.encrypt("secret-refresh-token")
        store.update(lambda state: store.client(state, "Alpha")["channels"][CID].update(refresh_token=encrypted))
        with open(store.path(), encoding="utf-8") as saved:
            self.assertNotIn("secret-refresh-token", saved.read())
        self.assertEqual(store.decrypt(encrypted), "secret-refresh-token")
        result = self.client.get("/tools/youtube/api/client?client=Alpha")
        self.assertTrue(result.json["client"]["channels"][0]["connected"])
        self.assertNotIn(encrypted, result.text)
        self.assertNotIn("refresh_token", result.text)

    def test_invite_rotation_and_expiration(self):
        first = store.invite("Alpha", CID)
        second = store.invite("Alpha", CID)
        with self.assertRaises(ValueError): store.lookup(first)
        self.assertEqual(store.lookup(second)["client"], "Alpha")
        with patch("time.time", return_value=time.time() + 8 * 86400):
            with self.assertRaises(ValueError): store.lookup(second)

    def test_invite_cannot_target_another_clients_channel(self):
        self.assertEqual(self.post("invite", {"channel_id": OTHER}).status_code, 400)

    def test_public_invite_does_not_require_staff(self):
        token = store.invite("Alpha", CID)
        with patch("hub.auth.user_from_environ", return_value=None):
            result = self.client.get("/connect/youtube/" + token)
        self.assertEqual(result.status_code, 200)
        self.assertIn("Connect YouTube for Alpha", result.text)
        self.assertEqual(result.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", result.headers["Cache-Control"])

    def test_oauth_start_binds_browser_and_uses_existing_callback(self):
        token = store.invite("Alpha", CID)
        result = self.client.post("/connect/youtube/" + token + "/start", data={"csrf": "csrf-test"})
        self.assertEqual(result.status_code, 302)
        self.assertIn("code_challenge=", result.location)
        self.assertIn("connect%2Fcallback", result.location)
        state = next(iter(store.read()["oauth"]))
        self.assertTrue(state.startswith("yt_"))
        stranger = self.app.test_client()
        result = stranger.get("/connect/callback?state=" + state + "&code=abc")
        self.assertEqual(result.status_code, 400)
        self.assertFalse(store.public_client("Alpha")["channels"][0]["connected"])

    def oauth_state(self):
        token = store.invite("Alpha", CID)
        self.client.post("/connect/youtube/" + token + "/start", data={"csrf": "csrf-test"})
        return next(iter(store.read()["oauth"]))

    def test_oauth_wrong_channel_does_not_attach(self):
        state = self.oauth_state()
        with patch("requests.post", return_value=response({"access_token": "a", "refresh_token": "r", "scope": " ".join(yt.SCOPES)})), patch.object(yt, "api", return_value={"items": [{"id": OTHER, "snippet": {"title": "Wrong"}}]}):
            result = self.client.get("/connect/callback?state=" + state + "&code=abc")
        self.assertEqual(result.status_code, 400)
        self.assertFalse(store.public_client("Alpha")["channels"][0]["connected"])

    def test_oauth_success_is_single_use(self):
        state = self.oauth_state()
        with patch("requests.post", return_value=response({"access_token": "a", "refresh_token": "r", "scope": " ".join(yt.SCOPES)})), patch.object(yt, "api", return_value={"items": [{"id": CID, "snippet": {"title": "Alpha"}}]}):
            result = self.client.get("/connect/callback?state=" + state + "&code=abc")
            self.assertEqual(result.status_code, 200)
            result = self.client.get("/connect/callback?state=" + state + "&code=abc")
            self.assertEqual(result.status_code, 400)
        self.assertTrue(store.public_client("Alpha")["channels"][0]["connected"])

    def test_oauth_partial_scopes_rejected(self):
        state = self.oauth_state()
        with patch("requests.post", return_value=response({"access_token": "a", "refresh_token": "r", "scope": yt.SCOPES[0]})):
            result = self.client.get("/connect/callback?state=" + state + "&code=abc")
        self.assertEqual(result.status_code, 400)

    def test_draft_cross_client_is_rejected(self):
        result = self.post("drafts", {"client": "Beta", "channel_id": CID, "title": "Wrong client"})
        self.assertEqual(result.status_code, 400)

    def test_edit_invalidates_approval(self):
        did = self.draft()
        self.assertEqual(self.post("approve", {"draft_id": did}).status_code, 200)
        self.post("drafts", {"id": did, "channel_id": CID, "title": "A changed title"})
        row = store.public_client("Alpha")["drafts"][0]
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["revision"], 2)

    def test_customer_review_rejects_stale_revision(self):
        did = self.draft()
        token = store.invite("Alpha", kind="review", draft_id=did)
        self.post("drafts", {"id": did, "channel_id": CID, "title": "New title"})
        result = self.client.post("/connect/youtube/" + token + "/review", data={"csrf": "csrf-test", "revision": "1", "decision": "approved"})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(store.public_client("Alpha")["drafts"][0]["status"], "draft")

    def test_review_link_only_exposes_its_draft(self):
        did = self.draft()
        self.post("drafts", {"channel_id": CID, "title": "Private other draft"})
        token = store.invite("Alpha", kind="review", draft_id=did)
        result = self.client.get("/connect/youtube/" + token)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("Private other draft", result.text)

    def test_upload_requires_approval(self):
        did = self.draft()
        with patch.object(yt, "access_token", return_value="access"), patch("requests.post") as send:
            result = self.client.post("/tools/youtube/api/upload", data={"client": "Alpha", "draft_id": did, "video": (io.BytesIO(b"video"), "test.mp4")}, headers={"X-YouTube-CSRF": "csrf-test"})
        self.assertEqual(result.status_code, 400)
        send.assert_not_called()

    def test_upload_is_private_and_cannot_repeat(self):
        did = self.draft(); self.post("approve", {"draft_id": did})
        with patch.object(yt, "access_token", return_value="access"), patch("requests.post", return_value=response(headers={"Location": yt.ROOT + "upload/session"})) as start, patch("requests.put", return_value=response({"id": "vid"})):
            result = self.client.post("/tools/youtube/api/upload", data={"client": "Alpha", "draft_id": did, "video": (io.BytesIO(b"video"), "test.mp4")}, headers={"X-YouTube-CSRF": "csrf-test"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(start.call_args.kwargs["json"]["status"]["privacyStatus"], "private")
            again = self.client.post("/tools/youtube/api/upload", data={"client": "Alpha", "draft_id": did, "video": (io.BytesIO(b"video"), "test.mp4")}, headers={"X-YouTube-CSRF": "csrf-test"})
            self.assertEqual(again.status_code, 400)
            self.assertEqual(start.call_count, 1)

    def test_uncertain_upload_never_retries_automatically(self):
        import requests
        did = self.draft(); self.post("approve", {"draft_id": did})
        with patch.object(yt, "access_token", return_value="access"), patch("requests.post", return_value=response(headers={"Location": yt.ROOT + "upload/session"})), patch("requests.put", side_effect=requests.Timeout):
            result = self.client.post("/tools/youtube/api/upload", data={"client": "Alpha", "draft_id": did, "video": (io.BytesIO(b"video"), "test.mp4")}, headers={"X-YouTube-CSRF": "csrf-test"})
        self.assertEqual(result.status_code, 502)
        self.assertEqual(store.public_client("Alpha")["drafts"][0]["status"], "upload_uncertain")
        self.assertNotIn("upload_session", str(store.public_client("Alpha")))

    def test_publish_rejects_unuploaded_draft(self):
        self.assertEqual(self.post("publish", {"draft_id": self.draft()}).status_code, 400)

    def test_publish_preserves_disclosures_and_reports_private_restriction(self):
        did = self.draft()
        store.update(lambda state: store.client(state, "Alpha")["drafts"][did].update(status="uploaded", video_id="video"))
        calls = []
        def fake_api(resource, params, token, method="GET", body=None):
            if method == "GET":
                return {"items": [{"id": "video", "snippet": {"channelId": CID}, "processingDetails": {"processingStatus": "succeeded"},
                    "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": True, "containsSyntheticMedia": True, "embeddable": False}}]}
            calls.append(body)
            return {"status": {"privacyStatus": "private"}}
        with patch.object(yt, "access_token", return_value="a"), patch.object(yt, "api", side_effect=fake_api):
            result = self.post("publish", {"draft_id": did})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["status"], "uploaded")
        self.assertTrue(calls[0]["status"]["selfDeclaredMadeForKids"])
        self.assertTrue(calls[0]["status"]["containsSyntheticMedia"])
        self.assertFalse(calls[0]["status"]["embeddable"])

    def test_publish_waits_for_processing(self):
        did = self.draft()
        store.update(lambda state: store.client(state, "Alpha")["drafts"][did].update(status="uploaded", video_id="video"))
        with patch.object(yt, "access_token", return_value="a"), patch.object(yt, "api", return_value={"items": [{"snippet": {"channelId": CID}, "processingDetails": {"processingStatus": "processing"}}]}) as provider:
            result = self.post("publish", {"draft_id": did})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(provider.call_count, 1)

    def test_metadata_update_checks_video_ownership(self):
        with patch.object(yt, "access_token", return_value="a"), patch.object(yt, "api", return_value={"items": [{"id": "v", "snippet": {"channelId": OTHER}}]}), patch("requests.put") as write:
            result = self.post("video-details", {"channel_id": CID, "video_id": "v", "title": "Valid title"})
        self.assertEqual(result.status_code, 400); write.assert_not_called()

    def test_metadata_update_preserves_tags(self):
        video = {"id": "v", "etag": "old", "snippet": {"channelId": CID, "title": "Old", "description": "Notes", "tags": ["keep"], "categoryId": "22"}}
        with patch.object(yt, "access_token", return_value="a"), patch.object(yt, "api", return_value={"items": [video]}), patch("requests.put", return_value=response({"id": "v"})) as write:
            result = self.post("video-details", {"channel_id": CID, "video_id": "v", "title": "New title", "description": "New notes", "original_title": "Old", "original_description": "Notes"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(write.call_args.kwargs["json"]["snippet"]["tags"], ["keep"])
        self.assertEqual(write.call_args.kwargs["headers"]["If-Match"], "old")

    def test_review_labels_its_limits(self):
        result = yt.review({"description": "", "videos": []})
        self.assertEqual(result["sample_size"], 0)
        self.assertIn("does not measure", result["note"])

    def test_launch_plan_saved_to_correct_client(self):
        result = self.post("launch", {"services": "Repairs", "audience": "Homeowners", "website": "https://example.com"})
        self.assertEqual(result.status_code, 200)
        self.assertIn("Repairs", store.public_client("Alpha")["launch"]["about"])
        self.assertEqual(store.public_client("Beta")["launch"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
