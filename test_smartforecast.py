"""Focused regression checks for the SmartForecast Client Tool."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


class SmartForecastEngineTests(unittest.TestCase):
    def setUp(self):
        from modules.smartforecast.store import _seed_rules
        self.rules = _seed_rules()
        self.hot = {
            "temperature": 96, "feels_like": 103, "forecast_high": 98,
            "forecast_low": 75, "rain_probability": 10, "snow_inches": 0,
            "wind_mph": 12, "humidity": 68, "official_alerts": [],
            "hours_until_event": 0,
        }

    def test_highest_priority_matching_rule_wins(self):
        from modules.smartforecast.engine import choose_winner
        winner = choose_winner(self.rules, self.hot)
        self.assertEqual(winner["rule"]["id"], "hvac_extreme_heat")
        self.assertEqual(winner["phase"], "active_event")

    def test_ordinary_weather_requires_two_checks_and_has_post_window(self):
        from modules.smartforecast.engine import advance_state, choose_winner, empty_state
        winner = choose_winner(self.rules, self.hot)
        start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        first, transition = advance_state(empty_state(), winner, start)
        self.assertEqual(transition, "qualifying")
        self.assertIsNone(first["current_trigger"])
        active, transition = advance_state(first, winner, start + timedelta(minutes=30))
        self.assertEqual(transition, "activated")
        self.assertEqual(active["current_trigger"], "hvac_extreme_heat")
        held, transition = advance_state(active, None, start + timedelta(hours=1))
        self.assertEqual(transition, "minimum_duration")
        clearing, transition = advance_state(held, None, start + timedelta(hours=7))
        self.assertEqual(transition, "clearing")
        post, transition = advance_state(clearing, None, start + timedelta(hours=7, minutes=30))
        self.assertEqual(transition, "phase_changed")
        self.assertEqual(post["phase"], "post_event")
        default, transition = advance_state(post, None, start + timedelta(hours=32))
        self.assertEqual(transition, "deactivated")
        self.assertEqual(default["phase"], "default")
        self.assertIsNotNone(default["cooldown_until"])

    def test_official_alert_activates_immediately(self):
        from modules.smartforecast.engine import advance_state, choose_winner, empty_state
        snapshot = {**self.hot, "temperature": 72, "feels_like": 72,
                    "forecast_high": 75, "official_alerts": ["Heat Advisory"]}
        winner = choose_winner(self.rules, snapshot)
        state, transition = advance_state(empty_state(), winner)
        self.assertTrue(winner["immediate"])
        self.assertEqual(transition, "activated")
        self.assertEqual(state["current_trigger"], "hvac_extreme_heat")

    def test_weather_provider_derives_pack_metrics_from_forecast_days(self):
        from modules.smartforecast.provider import normalize_weatherapi
        payload = {
            "location": {"name": "Columbus"},
            "current": {"temp_f": 73, "feelslike_f": 74, "humidity": 54, "wind_mph": 8},
            "forecast": {"forecastday": [
                {"date": "2026-09-04", "day": {"maxtemp_f": 89, "mintemp_f": 64,
                 "daily_chance_of_rain": 20, "maxwind_mph": 11, "totalsnow_cm": 0}},
                {"date": "2026-09-05", "day": {"maxtemp_f": 76, "mintemp_f": 60,
                 "daily_chance_of_rain": 15, "maxwind_mph": 9, "totalsnow_cm": 0}},
                {"date": "2026-09-06", "day": {"maxtemp_f": 79, "mintemp_f": 62,
                 "daily_chance_of_rain": 25, "maxwind_mph": 13, "totalsnow_cm": 0}},
            ]},
        }
        result = normalize_weatherapi(payload)
        self.assertEqual(result["weekend_high"], 79)
        self.assertEqual(result["weekend_rain_probability"], 25)
        self.assertEqual(result["weekend_wind_mph"], 13)
        self.assertEqual(result["sustained_heat_days"], 1)


class SmartForecastStoreAndRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "smartforecast.sqlite3")
        os.environ["SMARTFORECAST_DB_PATH"] = self.db_path
        from modules.smartforecast.app import app as app_object, _store_for_path
        _store_for_path.cache_clear()
        app_object.config.update(TESTING=True)
        self.client = app_object.test_client()

    def tearDown(self):
        os.environ.pop("SMARTFORECAST_DB_PATH", None)
        self.temp.cleanup()

    def test_schema_has_all_core_tables_and_seeded_demo(self):
        response = self.client.get("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["site"]["client_name"], "Quality Air Columbus")
        self.assertGreaterEqual(len(payload["rules"]), 6)
        con = sqlite3.connect(self.db_path)
        try:
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        expected = {"clients", "sites", "locations", "weather_snapshots",
                    "trigger_templates", "site_triggers", "content_slots",
                    "content_variants", "trigger_events", "trigger_event_history",
                    "embed_tokens", "manual_overrides"}
        self.assertTrue(expected.issubset(tables))

    def test_six_screen_shell_simulator_embed_and_csv(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        for label in ("Dashboard", "Website Setup", "Weather Triggers",
                      "Content &amp; Images", "Preview / Simulator", "Trigger Report"):
            self.assertIn(label, html)
        self.assertIn('role="tablist"', html)
        self.assertIn("Launch preflight", html)
        sim = self.client.post("/api/simulate", json={
            "temperature": 96, "feels_like": 103, "forecast_high": 98,
            "forecast_low": 74, "rain_probability": 10, "snow_inches": 0,
            "wind_mph": 12, "humidity": 68, "official_alert": "Heat Advisory",
        })
        self.assertEqual(sim.status_code, 200)
        self.assertEqual(sim.get_json()["winner"]["id"], "hvac_extreme_heat")
        bootstrap = self.client.get("/api/bootstrap").get_json()
        token = bootstrap["site"]["embed_token"]
        embed = self.client.get(f"/embed/{token}")
        self.assertEqual(embed.status_code, 200)
        self.assertIn("smartforecast-hvac-hero.png", embed.get_data(as_text=True))
        exported = self.client.get("/api/report.csv")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("recorded_at,trigger,phase,event", exported.get_data(as_text=True))

    def test_manual_override_is_immutable_history(self):
        data = self.client.get("/api/bootstrap").get_json()
        variant = data["variants"][0]
        before = len(data["history"])
        response = self.client.post("/api/override", json={
            "content_variant_id": variant["id"], "trigger_key": variant["trigger_key"],
            "phase": variant["phase"], "hours": 4, "note": "QA override",
        })
        self.assertEqual(response.status_code, 200)
        after = self.client.get("/api/bootstrap").get_json()["history"]
        self.assertEqual(len(after), before + 1)
        self.assertEqual(after[0]["event_type"], "override_started")
        self.assertTrue(after[0]["manual_override"])

    def test_weather_refresh_caches_once_and_due_query_is_idempotent(self):
        self.client.get("/api/bootstrap")
        snapshot = {
            "temperature": 96, "feels_like": 103, "forecast_high": 98,
            "forecast_low": 74, "rain_probability": 10, "snow_inches": 0,
            "wind_mph": 12, "humidity": 68, "dew_point": 72,
            "official_alerts": [], "hours_until_event": 0,
        }
        con = sqlite3.connect(self.db_path)
        try:
            before = con.execute("SELECT COUNT(*) FROM weather_snapshots").fetchone()[0]
        finally:
            con.close()
        with patch("modules.smartforecast.app.provider.fetch_weather", return_value=snapshot):
            response = self.client.post("/api/weather/refresh", json={})
        self.assertEqual(response.status_code, 200)
        con = sqlite3.connect(self.db_path)
        try:
            after = con.execute("SELECT COUNT(*) FROM weather_snapshots").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(after, before + 1)
        from modules.smartforecast.app import store
        self.assertEqual(store().due_sites(), [])

    def test_preflight_names_blockers_and_qa_is_non_mutating(self):
        bootstrap = self.client.get("/api/bootstrap").get_json()
        self.assertFalse(bootstrap["preflight"]["ok"])
        by_key = {item["key"]: item for item in bootstrap["preflight"]["checks"]}
        self.assertEqual(by_key["database"]["status"], "pass")
        self.assertEqual(by_key["weather_provider"]["status"], "fail")
        before = len(bootstrap["history"])
        qa = self.client.post("/api/qa/run", json={}).get_json()
        self.assertTrue(qa["ok"], qa)
        self.assertEqual(qa["passed"], qa["total"])
        after = len(self.client.get("/api/bootstrap").get_json()["history"])
        self.assertEqual(after, before)

    def test_unknown_public_token_fails_closed(self):
        response = self.client.get("/api/public/embed/not-a-real-token")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["ok"])

    def test_pause_returns_public_embed_to_baseline(self):
        bootstrap = self.client.get("/api/bootstrap").get_json()
        token = bootstrap["site"]["embed_token"]
        response = self.client.post("/api/pause", json={"paused": True})
        self.assertEqual(response.status_code, 200)
        public = self.client.get(f"/api/public/embed/{token}").get_json()
        self.assertEqual(public["state"]["phase"], "default")
        self.assertEqual(public["content"]["trigger_key"], "default")

    def test_multi_client_sites_are_isolated(self):
        created = self.client.post("/api/sites", json={
            "client_name": "North Shore Marine", "site_name": "North Shore Website",
            "domain": "northshore.example", "postal_code": "44077",
            "industry": "Marine / Boat Dealer", "platform": "WordPress",
        })
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        second = created.get_json()
        second_id = second["site"]["id"]
        self.assertEqual(len(second["sites"]), 2)
        self.assertEqual(second["site"]["client_name"], "North Shore Marine")
        update = self.client.post(f"/api/setup?site_id={second_id}", json={
            **second["site"], "client_name": "North Shore Boats",
            "name": "Marine Pilot", "domain": "boats.example", "industry": "Marine / Boat Dealer",
        })
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.get_json()["site"]["client_name"], "North Shore Boats")
        first = self.client.get("/api/bootstrap?site_id=1").get_json()
        self.assertEqual(first["site"]["client_name"], "Quality Air Columbus")
        self.assertNotEqual(first["site"]["embed_token"], second["site"]["embed_token"])

    def test_draft_content_never_changes_public_embed_until_approved(self):
        bootstrap = self.client.get("/api/bootstrap").get_json()
        token = bootstrap["site"]["embed_token"]
        variant = bootstrap["current_variant"]
        original = self.client.get(f"/api/public/embed/{token}").get_json()["content"]["headline"]
        changed = {**variant, "headline": "Draft headline that must not be live"}
        saved = self.client.post(f"/api/content/{variant['id']}", json=changed).get_json()["content"]
        self.assertEqual(saved["approval_status"], "draft")
        still_live = self.client.get(f"/api/public/embed/{token}").get_json()["content"]["headline"]
        self.assertEqual(still_live, original)
        published = self.client.post(f"/api/content/{variant['id']}/publish", json={}).get_json()["content"]
        self.assertEqual(published["approval_status"], "published")
        now_live = self.client.get(f"/api/public/embed/{token}").get_json()["content"]["headline"]
        self.assertEqual(now_live, changed["headline"])

    def test_rotating_embed_token_invalidates_the_old_token(self):
        old = self.client.get("/api/bootstrap").get_json()["site"]["embed_token"]
        rotated = self.client.post("/api/embed-token/rotate", json={}).get_json()
        self.assertTrue(rotated["ok"])
        self.assertNotEqual(rotated["token"], old)
        self.assertEqual(self.client.get(f"/api/public/embed/{old}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/public/embed/{rotated['token']}").status_code, 200)

    def test_industry_pack_applies_editable_rules_and_draft_content(self):
        created = self.client.post("/api/sites", json={
            "client_name": "Lakefront Boats", "domain": "lakefront.example",
            "postal_code": "44077", "industry": "Marine / Boat Dealer",
        }).get_json()
        site_id = created["site"]["id"]
        marine_before = next(pack for pack in created["packs"] if pack["id"] == "marine")
        self.assertTrue(marine_before["recommended"])
        self.assertFalse(marine_before["applied"])
        applied = self.client.post(f"/api/packs/marine/apply?site_id={site_id}", json={})
        self.assertEqual(applied.status_code, 200, applied.get_data(as_text=True))
        data = applied.get_json()
        rule = next(item for item in data["rules"] if item["id"] == "marine_boating_weekend")
        self.assertTrue(rule["enabled"])
        drafts = [item for item in data["variants"] if item["trigger_key"] == rule["id"]]
        self.assertEqual({item["phase"] for item in drafts}, {"pre_event", "active_event", "post_event"})
        self.assertTrue(all(item["approval_status"] == "draft" for item in drafts))
        publication = next(item for item in data["preflight"]["checks"]
                           if item["key"] == "publication_coverage")
        self.assertEqual(publication["status"], "fail")
        token = data["site"]["embed_token"]
        public = self.client.get(f"/api/public/embed/{token}").get_json()
        self.assertEqual(public["content"]["trigger_key"], "default")

    def test_privacy_minimized_engagement_is_deduplicated_and_reported(self):
        bootstrap = self.client.get("/api/bootstrap").get_json()
        token = bootstrap["site"]["embed_token"]
        public = self.client.get(f"/api/public/embed/{token}").get_json()
        content_id = public["content"]["id"]
        common = {"session_id": "browser-session-12345", "content_variant_id": content_id,
                  "referrer": "https://client.example/landing?private=value",
                  "metadata": {"source": "smartforecast_embed", "ignored": "drop-me"}}
        view = self.client.post(f"/api/public/embed/{token}/event",
                                json={**common, "event_type": "view"})
        self.assertEqual(view.status_code, 202)
        self.assertTrue(view.get_json()["recorded"])
        duplicate = self.client.post(f"/api/public/embed/{token}/event",
                                     json={**common, "event_type": "view"})
        self.assertFalse(duplicate.get_json()["recorded"])
        self.client.post(f"/api/public/embed/{token}/event",
                         json={**common, "event_type": "click"})
        self.client.post(f"/api/public/embed/{token}/event", json={
            **common, "event_type": "conversion",
            "metadata": {"conversion_type": "form", "event_id": "lead-001"},
        })
        report = self.client.get("/api/bootstrap").get_json()["report"]
        self.assertEqual(report["views"], 1)
        self.assertEqual(report["clicks"], 1)
        self.assertEqual(report["conversions"], 1)
        self.assertEqual(report["click_rate"], 100.0)
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT session_hash,referrer_domain,metadata_json FROM engagement_events ORDER BY id LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        self.assertNotEqual(row[0], common["session_id"])
        self.assertEqual(len(row[0]), 64)
        self.assertEqual(row[1], "client.example")
        self.assertNotIn("private", row[2])
        self.assertNotIn("ignored", row[2])
        exported = self.client.get("/api/engagement.csv")
        self.assertIn("occurred_at,event_type,content_variant_id", exported.get_data(as_text=True))
        embed = self.client.get(f"/embed/{token}").get_data(as_text=True)
        self.assertIn("sessionStorage", embed)
        self.assertIn(f"/api/public/embed/{token}/event", embed)

    def test_engagement_rejects_invalid_events_and_unpublished_content(self):
        bootstrap = self.client.get("/api/bootstrap").get_json()
        token = bootstrap["site"]["embed_token"]
        invalid = self.client.post(f"/api/public/embed/{token}/event", json={
            "event_type": "purchase", "session_id": "browser-session-12345",
        })
        self.assertEqual(invalid.status_code, 400)
        cross_site = self.client.post(f"/api/public/embed/{token}/event", json={
            "event_type": "view", "session_id": "browser-session-12345",
            "content_variant_id": 999999,
        })
        self.assertEqual(cross_site.status_code, 400)
        preflight = self.client.options(f"/api/public/embed/{token}/event")
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("POST", preflight.headers["Access-Control-Allow-Methods"])

    def test_backup_restores_a_fresh_render_disk(self):
        self.client.get("/api/bootstrap")
        from modules.smartforecast.app import _store_for_path, store
        result = store().backup()
        self.assertTrue(result["ok"], result)
        backup_path = Path(result["path"])
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["sql"].startswith("BEGIN TRANSACTION"))
        os.unlink(self.db_path)
        _store_for_path.cache_clear()
        restored = store().bootstrap()
        self.assertEqual(restored["site"]["client_name"], "Quality Air Columbus")
        self.assertGreaterEqual(len(restored["history"]), 1)


if __name__ == "__main__":
    unittest.main()
