"""Finishing contracts: isolated database and synthetic providers; no paid calls."""
import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import test_commercial_reliability as fixture
from modules.commercial_builder.db import db
from modules.commercial_builder.models import CommercialProject, Scene, RenderJob, RenderApproval
from modules.commercial_builder.finishing_models import RenderInspection, BrandPreset, ProductionUsage
from modules.commercial_builder.services import finished_video, creatomate_service, openai_service, media_state
from modules.commercial_builder import variations, generation, usage, recovery


class FinishingTests(unittest.TestCase):
    setUp = fixture.ReliabilityTests.setUp
    tearDown = fixture.ReliabilityTests.tearDown
    @classmethod
    def tearDownClass(cls):
        with fixture.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def approve_source(self):
        job = RenderJob(project_id=self.project.id, format="16:9", status="succeeded", output_url="https://res.cloudinary.com/test/video.mp4")
        db.session.add(job); db.session.flush()
        db.session.add(RenderApproval(project_id=self.project.id, render_job_id=job.id, approved_by="Tester"))
        db.session.commit()
        return job

    def test_timeline_uses_actual_renderer_captions(self):
        self.project.cta = {"captions_enabled": True}
        db.session.commit()
        response = self.http.get(self.base + "/timeline?format=9:16")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        source = creatomate_service.build_source(self.project.to_dict(False), [self.scene.to_dict()], "9:16")
        self.assertEqual(data["captions"], [e for e in source["elements"] if e.get("id", "").startswith("caption_")])
        self.assertEqual(data["safe_insets"]["bottom"], 20)
        self.assertIsNone(data["scenes"][0]["speech_seconds"])

    def test_timeline_end_card_matches_renderer_composition(self):
        self.scene.is_cta = True
        self.project.cta = {"captions_enabled": True, "headline": "A clear call to action"}
        db.session.commit()
        response = self.http.get(self.base + "/timeline").get_json()
        source = creatomate_service.build_source(self.project.to_dict(False), [self.scene.to_dict()], "16:9")
        group = next(e for e in source["elements"] if e.get("id") == f"scene_group_{self.scene.id}")
        self.assertEqual(response["scenes"][0]["overlays"], group["elements"][1:])
        self.assertIn("A clear call to action", [e.get("text") for e in response["scenes"][0]["overlays"]])

    def test_caption_boolean_validation(self):
        self.assertEqual(self.http.post(self.base + "/timeline-settings", json={"captions_enabled": "false"}).status_code, 400)
        self.assertEqual(self.http.post(self.base + "/timeline-settings", json={"captions_enabled": True}).status_code, 200)
        self.assertTrue(self.project.cta["captions_enabled"])

    def test_approved_caption_change_requires_draft(self):
        self.approve_source()
        self.assertEqual(self.http.post(self.base + "/timeline-settings", json={"captions_enabled": True}).status_code, 400)
        self.assertFalse(self.http.get(self.base + "/timeline").get_json()["can_edit_captions"])

    def test_wrong_format_rejected(self):
        self.assertEqual(self.http.get(self.base + "/timeline?format=evil").status_code, 400)

    def test_finished_file_checks_measured_streams(self):
        expected = {"duration": 10, "width": 1920, "height": 1080, "audio_required": True}
        probe = {"format": {"duration": "10.02"}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}]}
        self.assertEqual(finished_video.evaluate(probe, expected)["status"], "passed")
        for broken in ({}, {"format": {"duration": "NaN"}}, {"format": {"duration": "10"}, "streams": probe["streams"][:1]}):
            self.assertEqual(finished_video.evaluate(broken, expected)["status"], "failed")
        self.assertEqual(finished_video.evaluate(probe, expected, decoded=False)["status"], "failed")
        warnings = finished_video.evaluate(probe, expected, content_log="black_start:2 black_end:4 black_duration:2\nsilence_end: 5 | silence_duration: 3")
        self.assertEqual(warnings["status"], "review")
        self.assertEqual(len(warnings["warnings"]), 2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is installed in production/CI, not this local runtime")
    def test_real_mp4_inspection_detects_missing_audio(self):
        job = self.approve_source()
        db.session.add(RenderInspection(job_id=job.id, expected={"duration": 1, "width": 64, "height": 64, "audio_required": True}))
        db.session.commit()
        with tempfile.TemporaryDirectory() as directory:
            file = str(Path(directory) / "silent.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64:d=1", "-c:v", "mpeg4", file], check=True, timeout=20)
            with patch.object(finished_video, "_download", side_effect=lambda url, path: shutil.copyfile(file, path)):
                result = finished_video.inspect_job(job)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(next(c for c in result["checks"] if c["label"] == "Playable video")["passed"])
        self.assertFalse(next(c for c in result["checks"] if c["label"] == "Audio stream")["passed"])

    def test_inspection_url_refuses_local_and_arbitrary_hosts(self):
        for url in ("http://127.0.0.1/x", "file:///etc/passwd", "https://example.com/x", "https://res.cloudinary.com.evil.test/x", "https://user:password@res.cloudinary.com/x"):
            with self.assertRaises(ValueError): finished_video._allowed(url)
        with patch.object(finished_video.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError): finished_video._allowed("https://res.cloudinary.com/x")

    def test_render_captures_submission_expectation(self):
        with patch.object(creatomate_service, "submit_render", return_value={"id": "paid", "status": "queued"}):
            job = generation.submit_render_job(self.project, self.client, [self.scene.to_dict()], "16:9")
        self.project.length_seconds = 30; db.session.commit()
        self.assertEqual(db.session.get(RenderInspection, job.id).expected["duration"], 10)

    def test_inspection_unavailable_is_never_pass(self):
        job = self.approve_source()
        row = RenderInspection(job_id=job.id, expected={"duration": 10})
        db.session.add(row); db.session.commit()
        with patch.object(finished_video.shutil, "which", return_value=None):
            result = finished_video.inspect_job(job)
        self.assertEqual(result["status"], "unverified")
        self.assertIsNotNone(row.checked_at)
        self.assertEqual(finished_video.inspect_job(job), result)

    def test_legacy_job_has_no_invented_expectation(self):
        job = self.approve_source()
        self.assertEqual(finished_video.inspect_job(job)["status"], "unverified")

    def test_technical_failure_blocks_approval(self):
        job = RenderJob(project_id=self.project.id, status="succeeded", output_url="https://res.cloudinary.com/test.mp4")
        db.session.add(job); db.session.commit()
        with patch.object(finished_video, "inspect_job", return_value={"status": "failed", "checks": []}):
            response = self.http.post(f"{self.base}/render-jobs/{job.id}/approve", json={"acknowledge_unverified_video": True, "acknowledge_compliance": True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(RenderApproval.query.count(), 0)

    def test_unavailable_video_requires_explicit_human_acknowledgment(self):
        job = RenderJob(project_id=self.project.id, status="succeeded", output_url="https://res.cloudinary.com/test.mp4")
        db.session.add(job); db.session.commit()
        response = self.http.post(f"{self.base}/render-jobs/{job.id}/approve", json={"acknowledge_compliance": True})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.get_json()["needs_video_acknowledgment"])

    def test_controlled_variation_needs_approved_source(self):
        change = [{"scene_id": self.scene.id, "narration": "A new hook."}]
        preview = variations.plan(self.project, change)
        with self.assertRaises(ValueError): variations.create(self.project, {"changes": change, "plan_key": preview["key"]})

    def test_variation_reuses_footage_and_invalidates_speech(self):
        self.approve_source()
        self.scene.asset_meta = {"voiceover": {"audio_url": "https://example.test/voice.mp3", "speech_signature": media_state.speech_signature(self.scene.to_dict())}}
        self.project.status = "complete"; self.project.platform = "youtube"
        db.session.commit()
        change = [{"scene_id": self.scene.id, "narration": "An entirely new hook."}]
        preview = variations.plan(self.project, change)
        child, result = variations.create(self.project, {"changes": change, "plan_key": preview["key"]})
        copied = child.scenes.first()
        self.assertEqual(child.status, "draft"); self.assertEqual(child.platform, "youtube")
        self.assertEqual(copied.asset_url, self.scene.asset_url)
        self.assertTrue(copied.asset_meta["voiceover"]["stale"])
        self.assertEqual(child.script["scenes"][0]["voiceover"], change[0]["narration"])
        self.assertEqual(child.render_jobs.count(), 0)
        self.assertNotEqual(copied.narration, self.scene.narration)

    def test_changed_source_rejects_old_plan(self):
        self.approve_source()
        change = [{"scene_id": self.scene.id, "narration": "A new hook."}]
        preview = variations.plan(self.project, change)
        self.scene.narration = "Edited in another tab."; db.session.commit()
        with self.assertRaises(ValueError): variations.create(self.project, {"changes": change, "plan_key": preview["key"]})

    def test_source_must_still_match_saved_approved_render(self):
        job = self.approve_source()
        db.session.add(RenderInspection(job_id=job.id, expected=finished_video.expected_output(self.project, "16:9", [self.scene.to_dict()])))
        db.session.commit()
        self.scene.narration = "Changed after approval"; db.session.commit()
        changes = [{"scene_id": self.scene.id, "narration": "Another line"}]
        preview = variations.plan(self.project, changes)
        with self.assertRaisesRegex(ValueError, "source changed"):
            variations.create(self.project, {"changes": changes, "plan_key": preview["key"]})

    def test_locked_scene_cannot_vary(self):
        self.scene.locked = True; db.session.commit()
        with self.assertRaises(ValueError): variations.plan(self.project, [{"scene_id": self.scene.id, "narration": "New."}])

    def test_legacy_brief_variation_persists_and_requires_copy_review(self):
        self.project.status = "complete"; db.session.commit()
        response = self.http.post(self.base + "/variation", json={"variation_type": "offer", "changes": {"what_advertising": "New offer"}})
        self.assertEqual(response.status_code, 201)
        child = db.session.get(CommercialProject, response.get_json()["project"]["id"])
        self.assertEqual(child.brief["what_advertising"], "New offer")
        self.assertTrue(child.brief["variation_needs_script"])
        self.assertEqual(child.status, "draft")
        self.assertFalse(media_state.integrity(child.to_dict(), [s.to_dict() for s in child.scenes.all()])["passed"])

    def test_bad_legacy_input_creates_nothing(self):
        for data in ({"variation_type": "bogus"}, {"variation_type": "duration", "changes": {"length_seconds": -1}}, []):
            self.assertEqual(self.http.post(self.base + "/variation", json=data).status_code, 400)
        self.assertEqual(CommercialProject.query.count(), 1)

    def test_preset_stores_only_settings_and_requires_approval(self):
        self.project.music = {"voice_id": "voice", "voice_track_url": "https://example.test/paid.mp3"}
        self.project.cta = {"headline": "Call us"}; db.session.commit()
        self.assertEqual(self.http.post(self.base + "/brand-presets", json={"name": "Campaign"}).status_code, 400)
        response = self.http.post(self.base + "/brand-presets", json={"name": "Campaign", "approve": True})
        self.assertEqual(response.status_code, 201)
        snapshot = response.get_json()["preset"]["snapshot"]
        self.assertNotIn("voice_track_url", snapshot["music"])
        self.assertEqual(snapshot["brand"]["name"], self.client.name)

    def test_preset_apply_retains_project_media_and_uses_brand_pronunciation(self):
        self.client.pronunciation_dict = {"Gahanna": "guh-HAN-uh"}; db.session.commit()
        response = self.http.post(self.base + "/brand-presets", json={"name": "Approved", "approve": True})
        pid = response.get_json()["preset"]["id"]
        original = self.scene.asset_url
        self.assertEqual(self.http.post(f"{self.base}/brand-presets/{pid}/apply", json={}).status_code, 200)
        self.assertEqual(self.scene.asset_url, original)
        self.assertEqual(self.project.music["pronunciation_dict"], self.client.pronunciation_dict)
        self.assertEqual(self.project.brief["brand_preset_id"], pid)

    def test_identical_preset_keeps_existing_presenter_valid(self):
        self.scene.asset_meta = {"avatar_id": "saved-avatar", "heygen_job": {"status": "completed", "voice_id": "saved-voice", "voice_provider": "heygen"}, "spokesperson_url": "https://example.test/paid.mp4"}
        db.session.commit()
        response = self.http.post(self.base + "/brand-presets", json={"name": "Same casting", "approve": True})
        pid = response.get_json()["preset"]["id"]
        self.assertEqual(response.get_json()["preset"]["snapshot"]["music"]["voice_id"], self.client.preferred_voiceover_id)
        self.assertEqual(self.http.post(f"{self.base}/brand-presets/{pid}/apply", json={}).status_code, 200)
        self.assertFalse(self.scene.asset_meta.get("presenter_stale"))
        self.assertFalse(self.project.music.get("voice_track_stale"))

    def test_project_voice_edit_preserves_music_and_invalidates_paid_speech(self):
        self.project.music = {"voice_id": "old", "voice_track_url": "https://example.test/old.mp3", "mood": "Happy", "level": "Low"}
        db.session.commit()
        response = self.http.put(self.base + "/music", json={"voice_id": "new"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.music["mood"], "Happy")
        self.assertEqual(self.project.music["level"], "Low")
        self.assertTrue(self.project.music["voice_track_stale"])

    def test_heygen_native_voice_uses_project_pronunciation(self):
        from modules.commercial_builder.services import heygen_service
        self.project.music = {"pronunciation_dict": {"Test": "Tess-t"}}
        db.session.commit()
        with patch.object(heygen_service, "is_live", return_value=True), patch.object(heygen_service, "voice_available", return_value=True), \
             patch.object(heygen_service, "generate_spokesperson_clip", return_value={"status": "processing", "job_id": "saved-id"}) as call:
            response = self.http.post(self.scene_url + "/spokesperson", json={"avatar_id": "avatar", "voice_id": "voice", "voice_provider": "heygen", "over_footage": False})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Tess-t Business", call.call_args.args[1])
        self.assertIn("Test Business", self.scene.narration)

    def test_preset_cannot_change_approved_project(self):
        pid = self.http.post(self.base + "/brand-presets", json={"name": "Approved", "approve": True}).get_json()["preset"]["id"]
        self.approve_source()
        self.assertEqual(self.http.post(f"{self.base}/brand-presets/{pid}/apply", json={}).status_code, 400)

    def test_preset_is_scoped_to_client(self):
        from modules.commercial_builder.models import Client
        other = Client(name="Other client", slug="other"); db.session.add(other); db.session.flush()
        preset = BrandPreset(client_id=other.id, source_project_id=99, name="Private", approved_by="Other", snapshot={})
        db.session.add(preset); db.session.commit()
        self.assertEqual(self.http.get(self.base + "/brand-presets").get_json()["presets"], [])
        self.assertEqual(self.http.post(f"{self.base}/brand-presets/{preset.id}/apply", json={}).status_code, 404)

    def test_worker_retries_unavailable_inspection_without_rendering(self):
        job = self.approve_source()
        db.session.add(RenderInspection(job_id=job.id, expected={"duration": 10}))
        db.session.commit()
        with patch.object(finished_video, "inspect_job", return_value={"status": "unverified"}) as inspect, patch.object(creatomate_service, "submit_render") as submit:
            result = recovery.recover_pending()
        self.assertEqual(result["pending"], 1)
        self.assertEqual(inspect.call_count, 1); submit.assert_not_called()

    def test_usage_is_project_scoped_and_unknown_price_is_null(self):
        with usage.scope(self.project.id):
            usage.record("heygen", operation="render")
            usage.record("openai", cost=.012)
            usage.record("elevenlabs", cached=True)
        rows = ProductionUsage.query.all()
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0].cost_usd); self.assertEqual(rows[2].cost_usd, 0)
        data = self.http.get(self.base + "/production-cost").get_json()
        self.assertEqual(data["known_cost_usd"], .012)
        self.assertEqual(data["providers"][0]["unpriced"], 1)

    def test_image_cost_is_attributed_once_across_both_existing_meters(self):
        from hub import ai, quotas
        from types import SimpleNamespace
        with usage.scope(self.project.id), patch.object(ai, "settings", SimpleNamespace(ai_usage_log=True)), patch.object(ai.audit, "log"):
            ai._record("commercial_builder", "still", "gpt-image-1", {}, 30, True)
            quotas.record_image(module="commercial_builder", model="gpt-image-1")
        rows = ProductionUsage.query.all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].cost_usd, quotas.IMAGE_PRICING.get("gpt-image-1"))

    def test_full_narration_reuses_exact_take_until_explicit_regeneration(self):
        from modules.commercial_builder.services import elevenlabs_service, cloudinary_service
        with patch.object(elevenlabs_service, "generate_voiceover", side_effect=lambda **kw: {"audio_bytes": b"synthetic-audio", "duration_estimate": 4}) as voice, \
             patch.object(cloudinary_service, "upload_asset", return_value={"secure_url": "https://res.cloudinary.com/test/audio.mp3"}):
            first = generation.run_full_voiceover(self.project, self.client, "voice")
            second = generation.run_full_voiceover(self.project, self.client, "voice")
            self.assertTrue(first["stored"]); self.assertTrue(second["reused"])
            self.assertEqual(voice.call_count, 1)
            generation.run_full_voiceover(self.project, self.client, "voice", regenerate=True)
            self.assertEqual(voice.call_count, 2)

    def test_new_pronunciation_invalidates_saved_full_take(self):
        from modules.commercial_builder.services import elevenlabs_service, cloudinary_service
        with patch.object(elevenlabs_service, "generate_voiceover", side_effect=lambda **kw: {"audio_bytes": b"synthetic-audio"}) as voice, \
             patch.object(cloudinary_service, "upload_asset", return_value={"secure_url": "https://res.cloudinary.com/test/audio.mp3"}):
            generation.run_full_voiceover(self.project, self.client, "voice")
            self.project.music = {**self.project.music, "pronunciation_dict": {"Test": "Tess-t"}}
            db.session.commit()
            generation.run_full_voiceover(self.project, self.client, "voice")
            self.assertEqual(voice.call_count, 2)

    def test_creative_review_reuses_only_exact_script(self):
        with patch.object(openai_service, "is_live", return_value=True), patch.object(openai_service, "_chat_json", return_value={"summary": "Clear CTA", "suggestions": []}) as call:
            first = self.http.post(self.base + "/creative-review", json={})
            second = self.http.post(self.base + "/creative-review", json={})
            self.assertEqual(first.status_code, 200)
            self.assertTrue(second.get_json()["cached"])
            self.assertEqual(call.call_count, 1)
            self.scene.narration = "Different wording"; db.session.commit()
            self.http.post(self.base + "/creative-review", json={})
            self.assertEqual(call.call_count, 2)

    def test_creative_model_only_for_concepts_and_review(self):
        from hub import ai
        with patch.object(ai, "chat_json", return_value={}) as call, patch.object(openai_service, "profile_model", return_value="fast-writing"):
            openai_service._chat_json("System", "User", purpose="concepts")
            self.assertEqual(call.call_args.kwargs["model"], "gpt-6-astra")
            self.assertEqual(call.call_args.kwargs["extra_payload"]["reasoning_effort"], "low")
            openai_service._chat_json("System", "User")
            self.assertEqual(call.call_args.kwargs["model"], "fast-writing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
