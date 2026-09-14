"""Offline workflow coverage; never contacts Google or creates paid campaigns."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp.name, "youtube.sqlite")
os.environ["HUB_DATA_DIR"] = tmp.name

from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from hub import youtube_ads as yt
from hub.extensions import shared_engine


def sample():
    return dict(customer_id="1234567890", name="Video launch", business_name="Test business",
                video_id="dQw4w9WgXcQ", final_url="https://example.com/offer", daily_budget=25,
                logo_asset_id="123", location_id="2840", language_id="1000", headline="See the difference",
                long_headline="See what makes us different", description="Explore our services today.",
                audience_notes="Test two hooks", placements=["youtubeShorts"],
                eu_political="DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING")


class Workflow(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shared_engine().dispose()

    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test-only"
        self.app.jinja_loader = ChoiceLoader([DictLoader({"base.html": "{% block content %}{% endblock %}"}), FileSystemLoader("hub/templates")])
        self.user = True
        yt.install(self.app, lambda: self.user)
        self.client = self.app.test_client()
        self.ga = Mock()
        self.ga.GoogleAdsError = type("GoogleAdsError", (Exception,), {})
        self.ga.connection_status.return_value = {"deploy_ready": False}
        self.ga.request.return_value = {"mutateOperationResponses": []}
        self.mock = patch.object(yt, "services", return_value=(self.ga, Mock()))
        self.mock.start()
        with self.client.session_transaction() as sess:
            sess["youtube_ads_csrf"] = "test-csrf"

    def tearDown(self):
        self.mock.stop()

    def post(self, path, data):
        return self.client.post("/tools/youtube-ads" + path, json=data, headers={"X-CSRF-Token": "test-csrf"})

    def draft(self):
        result = self.post("/api/drafts", sample())
        self.assertEqual(result.status_code, 201, result.json)
        return result.json["id"]

    def test_page_has_all_workflows(self):
        result = self.client.get("/tools/youtube-ads/")
        self.assertEqual(result.status_code, 200)
        for word in [b"Build", b"Optimize", b"Monitor", b"Report", b"YouTube API access alone"]:
            self.assertIn(word, result.data)
        from pathlib import Path
        Path("tmp").mkdir(exist_ok=True)
        Path("tmp/youtube-ads-preview.html").write_text(result.get_data(as_text=True), encoding="utf-8")

    def test_auth_and_csrf(self):
        self.user = False
        self.assertEqual(self.client.get("/tools/youtube-ads/api/drafts").status_code, 401)
        self.user = True
        self.assertEqual(self.client.post("/tools/youtube-ads/api/drafts", json=sample()).status_code, 403)

    def test_input_validation(self):
        for key, value in [("customer_id", "123 OR 1=1"), ("daily_budget", float("nan")), ("daily_budget", -1), ("final_url", "javascript:alert(1)"), ("video_id", "https://evil.example/dQw4w9WgXcQ"), ("placements", []), ("headline", "x" * 41), ("eu_political", "")]:
            data = sample(); data[key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.post("/api/drafts", data).status_code, 400)

    def test_video_urls(self):
        for url in ["https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/shorts/dQw4w9WgXcQ"]:
            self.assertEqual(yt.video_id(url), "dQw4w9WgXcQ")

    def test_draft_account_isolation(self):
        draft = self.draft()
        response = self.post(f"/api/drafts/{draft}/validate", {"customer_id": "9999999999"})
        self.assertEqual(response.status_code, 404)
        self.ga.request.assert_not_called()

    def test_creation_requires_validation_and_confirmation(self):
        draft = self.draft()
        self.assertEqual(self.post(f"/api/drafts/{draft}/create", {"customer_id": "1234567890", "confirmation": "CREATE_PAUSED"}).status_code, 409)
        self.assertEqual(self.post(f"/api/drafts/{draft}/validate", {"customer_id": "1234567890"}).status_code, 200)
        self.assertEqual(self.post(f"/api/drafts/{draft}/create", {"customer_id": "1234567890"}).status_code, 409)

    def test_paused_atomic_creation_and_duplicate_protection(self):
        draft = self.draft()
        self.post(f"/api/drafts/{draft}/validate", {"customer_id": "1234567890"})
        body = {"customer_id": "1234567890", "confirmation": "CREATE_PAUSED"}
        response = self.post(f"/api/drafts/{draft}/create", body)
        self.assertEqual(response.json["state"], "CREATED_PAUSED")
        payload = self.ga.request.call_args.args[2]
        self.assertFalse(payload["partialFailure"])
        self.assertFalse(payload["validateOnly"])
        ops = payload["mutateOperations"]
        self.assertEqual(ops[1]["campaignOperation"]["create"]["status"], "PAUSED")
        self.assertEqual(ops[-1]["adGroupAdOperation"]["create"]["status"], "PAUSED")
        channels = ops[2]["adGroupOperation"]["create"]["demandGenAdGroupSettings"]["channelControls"]["selectedChannels"]
        self.assertTrue(channels["youtubeShorts"])
        self.assertFalse(channels["discover"])
        calls = self.ga.request.call_count
        self.assertEqual(self.post(f"/api/drafts/{draft}/create", body).status_code, 409)
        self.assertEqual(self.ga.request.call_count, calls)

    def test_uncertain_submission_cannot_be_repeated(self):
        draft = self.draft()
        self.post(f"/api/drafts/{draft}/validate", {"customer_id": "1234567890"})
        self.ga.request.side_effect = TimeoutError("test timeout")
        body = {"customer_id": "1234567890", "confirmation": "CREATE_PAUSED"}
        with self.assertLogs(self.app.logger, level="ERROR"):
            self.assertEqual(self.post(f"/api/drafts/{draft}/create", body).status_code, 500)
        self.assertEqual(self.post(f"/api/drafts/{draft}/create", body).status_code, 409)

    def test_report_calculations_and_undefined_ratios(self):
        self.ga.search.side_effect = [[{"customer": {"currencyCode": "USD", "timeZone": "America/New_York"}}], [
            {"campaign": {"id": "1", "name": "Video", "advertisingChannelType": "DEMAND_GEN"}, "metrics": {"costMicros": "50000000", "conversions": 2, "conversionsValue": 150, "impressions": 1000, "clicks": 10, "videoTrueviewViews": 200, "videoTrueviewViewRate": .2}},
            {"campaign": {"id": "2", "name": "Empty"}, "metrics": {}}]]
        response = self.client.get("/tools/youtube-ads/api/report?customer_id=1234567890")
        self.assertEqual(response.status_code, 200, response.json)
        row, empty = response.json["campaigns"]
        self.assertEqual((row["cost"], row["cpa"], row["ctr"], row["roas"]), (50, 25, 1, 3))
        self.assertEqual((row["trueview_views"], row["view_rate"]), (200, 20))
        self.assertIsNone(empty["view_rate"])
        self.assertIsNone(empty["cpa"])
        self.assertIsNone(empty["roas"])

    def test_range_injection_rejected(self):
        response = self.client.get("/tools/youtube-ads/api/report?customer_id=1234567890&range=TODAY%20OR%201=1")
        self.assertEqual(response.status_code, 400)
        self.ga.search.assert_not_called()

    def test_csv_formula_escape(self):
        self.ga.search.side_effect = [[{"customer": {"currencyCode": "USD"}}], [{"campaign": {"id": "1", "name": "=DANGEROUS()", "status": "PAUSED", "advertisingChannelType": "VIDEO"}, "metrics": {}}]]
        result = self.client.get("/tools/youtube-ads/report.csv?customer_id=1234567890")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"'=DANGEROUS()", result.data)
        self.assertIn(b"USD", result.data)


if __name__ == "__main__":
    unittest.main()
