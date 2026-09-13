"""Approval, budgeting and review boundaries; providers are mocked and network denied."""
import os
import unittest
from unittest.mock import patch
import test_commercial_reliability as fixture
from modules.commercial_builder.db import db
from modules.commercial_builder.models import RenderJob, RenderApproval, ReviewShare
from modules.commercial_builder.finishing_models import RenderInspection, ProjectBudget, BudgetReservation
from modules.commercial_builder.services import finished_video
from modules.commercial_builder import budget, compliance_spec, generation
from modules.commercial_builder.routes import review


class WorkflowTests(unittest.TestCase):
    setUp = fixture.ReliabilityTests.setUp
    tearDown = fixture.ReliabilityTests.tearDown
    @classmethod
    def tearDownClass(cls):
        with fixture.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def job(self):
        job = RenderJob(project_id=self.project.id, format="16:9", status="succeeded", output_url="https://res.cloudinary.com/test.mp4")
        db.session.add(job); db.session.flush()
        db.session.add(RenderInspection(job_id=job.id, expected=finished_video.expected_output(self.project, "16:9", [self.scene.to_dict()])))
        db.session.commit()
        return job

    def test_unverified_pending_failed_cannot_be_acknowledged_away(self):
        job = self.job()
        for state in ("unverified", "pending", "failed"):
            with patch.object(finished_video, "inspect_job", return_value={"status": state, "checks": []}), patch("modules.commercial_builder.routes.render.cloudinary_service.upload_asset") as upload:
                response = self.http.post(f"{self.base}/render-jobs/{job.id}/approve", json={"acknowledge_compliance": True, "acknowledge_unverified_video": True})
                self.assertEqual(response.status_code, 409)
                upload.assert_not_called()
        self.assertEqual(RenderApproval.query.count(), 0)

    def test_labels_distinguish_changed_and_legacy_cuts(self):
        job = self.job()
        data = self.http.get(self.base + "/render-jobs").get_json()["render_jobs"][0]
        self.assertEqual(data["creative_status"], "current")
        self.assertEqual(data["version"], 1)
        self.assertTrue(data["created_at"].endswith("Z"))
        self.scene.narration = "Changed script"; db.session.commit()
        self.assertEqual(self.http.get(self.base + "/render-jobs").get_json()["render_jobs"][0]["creative_status"], "outdated")
        db.session.delete(db.session.get(RenderInspection, job.id)); db.session.commit()
        self.assertEqual(self.http.get(self.base + "/render-jobs").get_json()["render_jobs"][0]["creative_status"], "unknown")

    def test_link_creation_never_sends_even_with_email(self):
        self.job()
        with patch.object(review, "_deliver_review", wraps=review._deliver_review) as delivery, patch.object(review, "_hub_leads") as leads:
            response = self.http.post(self.base + "/reviews", json={"delivery_mode": "link", "reviewer_email": "reviewer@example.test"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(delivery.call_args.kwargs["email"], "")
            leads.capture_and_deliver.assert_not_called()

    def test_send_requires_email_before_creating_round(self):
        self.job()
        response = self.http.post(self.base + "/reviews", json={"delivery_mode": "send"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReviewShare.query.count(), 0)
        with patch.object(review, "_deliver_review", return_value={"state": "sent"}) as delivery:
            response = self.http.post(self.base + "/reviews", json={"delivery_mode": "send", "reviewer_email": "reviewer@example.test"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(delivery.call_args.kwargs["email"], "reviewer@example.test")

    def test_negated_brief_does_not_hide_real_testimonial(self):
        brief = {"what_advertising": "No performance claims, offers, or testimonials."}
        def engaged(**kw):
            return "ftc_endorsements" in compliance_spec.scan(brief=brief, client={"industry": "Marketing"}, **kw)["regimes"]
        self.assertFalse(engaged())
        self.assertTrue(engaged(script={"scenes": [{"narration": "A real customer shares their story."}]}))
        self.assertTrue(engaged(commercial_type="testimonial"))

    def test_unknown_prior_spend_and_pricing_block_provider(self):
        self.assertEqual(self.http.post(self.base + "/budget", json={"limit_usd": 15}).status_code, 200)
        with patch("modules.commercial_builder.services.creatomate_service.is_live", return_value=True), patch("modules.commercial_builder.services.creatomate_service.submit_render") as provider:
            with self.assertRaises(budget.BudgetBlocked):
                generation.submit_render_job(self.project, self.client, [self.scene.to_dict()], "16:9")
            provider.assert_not_called()
        self.assertEqual(self.http.post(self.base + "/voiceover/full", json={}).status_code, 409)
        self.assertFalse(self.http.get(self.base + "/production-cost").get_json()["total_cost_known"])

    def test_reservations_stop_repeat_spending_and_cannot_reset(self):
        self.http.post(self.base + "/budget", json={"limit_usd": 1, "prior_usd": 0, "confirm_prior": True})
        source = {"width": 1920, "height": 1080, "duration": 6, "frame_rate": 25}
        with patch.dict(os.environ, {"CREATOMATE_MAX_USD_PER_CREDIT": ".20"}):
            budget.reserve_render(self.project, source)
            with self.assertRaises(budget.BudgetBlocked): budget.reserve_render(self.project, source)
        self.assertEqual(BudgetReservation.query.count(), 1)
        self.assertEqual(budget.status(self.project.id)["reserved_usd"], .8)
        self.assertEqual(self.http.post(self.base + "/budget", json={"limit_usd": .5}).status_code, 400)
        self.assertEqual(self.http.post(self.base + "/budget", json={"limit_usd": 1, "prior_usd": 0.01, "confirm_prior": True}).status_code, 400)

    def test_nonfinite_budget_rejected(self):
        for value in ("NaN", "Infinity", -1, True):
            self.assertEqual(self.http.post(self.base + "/budget", json={"limit_usd": value}).status_code, 400)

    def test_provider_storage_host_is_narrowly_allowed(self):
        public = [(2, 1, 6, "", ("8.8.8.8", 443))]
        with patch.object(finished_video.socket, "getaddrinfo", return_value=public):
            finished_video._allowed("https://f002.backblazeb2.com/file/creatomate-c8xg3hsxdu/output.mp4")
            for url in ("https://f002.backblazeb2.com/file/unrelated/output.mp4", "https://backblazeb2.com/file/creatomate-x/a.mp4"):
                with self.assertRaises(ValueError): finished_video._allowed(url)

if __name__ == "__main__":
    unittest.main()
