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

    def test_readability_math_matches_wcag_and_defaults_pass(self):
        from modules.smartforecast.readability import assess_readability, contrast_ratio
        self.assertEqual(round(contrast_ratio("#000000", "#ffffff")), 21)
        result = assess_readability({}, {"overlay_opacity": 0})
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(check["ratio"] >= 4.5 for check in result["checks"]))

    def test_readability_rejects_invisible_text_and_button_pairs(self):
        from modules.smartforecast.readability import assess_readability
        result = assess_readability({
            "headline_color": "#303b46", "body_color": "#303b46",
            "button_color": "#ffffff", "button_text": "#ffffff",
        }, {})
        self.assertFalse(result["ok"])
        self.assertEqual({check["key"] for check in result["checks"] if not check["passed"]},
                         {"headline", "body", "button"})


class SmartForecastStoreAndRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "smartforecast.sqlite3")
        os.environ["SMARTFORECAST_DB_PATH"] = self.db_path
        os.environ["AUDIT_LOG_PATH"] = str(Path(self.temp.name) / "hub-audit.log.jsonl")
        from modules.smartforecast.app import app as app_object, _store_for_path
        _store_for_path.cache_clear()
        app_object.config.update(TESTING=True)
        self.client = app_object.test_client()

    def tearDown(self):
        os.environ.pop("SMARTFORECAST_DB_PATH", None)
        os.environ.pop("AUDIT_LOG_PATH", None)
        self.temp.cleanup()

    def test_schema_has_all_core_tables_and_seeded_demo(self):
        response = self.client.get("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["site"]["client_name"], "Quality Air Columbus")
        self.assertGreaterEqual(len(payload["rules"]), 6)

    def test_site_id_query_is_bounded_and_never_raises(self):
        malformed = self.client.get("/api/bootstrap?site_id=not-a-number")
        self.assertEqual(malformed.status_code, 200)
        self.assertEqual(malformed.get_json()["site"]["id"], 1)
        negative = self.client.get("/api/bootstrap?site_id=-500")
        self.assertEqual(negative.status_code, 200)
        self.assertEqual(negative.get_json()["site"]["id"], 1)
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
        self.assertIn("Readability guard", html)
        self.assertEqual(html.count('type="button" data-dialog-close'), 4)
        javascript = (Path(__file__).parent / "modules" / "smartforecast" / "static" /
                      "smartforecast.js").read_text(encoding="utf-8")
        self.assertIn("REQUEST_TIMEOUT_MS = 15000", javascript)
        self.assertIn("loadController?.abort()", javascript)
        self.assertIn(".sf-view\").forEach(panel => panel.hidden = true", javascript)
        self.assertIn('event.key === "Escape"', javascript)
        stylesheet = (Path(__file__).parent / "modules" / "smartforecast" / "static" /
                      "smartforecast.css").read_text(encoding="utf-8")
        self.assertIn(".sf-loading[hidden]{display:none!important}", stylesheet)
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
        self.assertIn("fixed copy-area scrim", embed.get_data(as_text=True))
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

    def test_material_change_reaches_hub_activity_without_exposing_token(self):
        self.client.get("/api/bootstrap")
        response = self.client.post(
            "/api/pause", json={"paused": True},
            headers={"X-Smart1-User": "release-tester@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        audit_path = Path(self.temp.name) / "hub-audit.log.jsonl"
        entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(entries[-1]["module"], "smartforecast")
        self.assertEqual(entries[-1]["type"], "site_paused")
        self.assertEqual(entries[-1]["actor"], "release-tester@example.com")
        self.assertEqual(entries[-1]["client"], "Quality Air Columbus")
        self.assertNotIn("token", entries[-1])

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

    def test_unreadable_brand_cannot_publish_or_pass_preflight(self):
        bootstrap = self.client.get("/api/bootstrap").get_json()
        site = bootstrap["site"]
        history_before = len(bootstrap["history"])
        response = self.client.post("/api/setup", json={
            **site,
            "branding": {
                **site["branding"],
                "headline_color": "#303b46",
                "body_color": "#303b46",
                "button_color": "#ffffff",
                "button_text": "#ffffff",
            },
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        readability = next(item for item in data["preflight"]["checks"]
                           if item["key"] == "readability")
        self.assertEqual(readability["status"], "fail")
        variant = data["current_variant"]
        self.assertFalse(variant["readability"]["ok"])
        publish = self.client.post(f"/api/content/{variant['id']}/publish", json={})
        self.assertEqual(publish.status_code, 400)
        self.assertIn("4.5:1 required", publish.get_json()["error"])
        self.assertEqual(len(self.client.get("/api/bootstrap").get_json()["history"]),
                         history_before)

    def test_brand_colors_are_css_safe(self):
        site = self.client.get("/api/bootstrap").get_json()["site"]
        response = self.client.post("/api/setup", json={
            **site,
            "branding": {**site["branding"], "headline_color": "red;}body{display:none"},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["site"]["branding"]["headline_color"], "#ffffff")

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

    def test_schema_ledger_and_operational_health_are_current(self):
        self.client.get("/api/bootstrap")
        from modules.smartforecast.store import SCHEMA_VERSION
        con = sqlite3.connect(self.db_path)
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            ledger = [row[0] for row in con.execute(
                "SELECT version FROM schema_migrations ORDER BY version")]
        finally:
            con.close()
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(ledger, list(range(1, SCHEMA_VERSION + 1)))
        health = self.client.get("/api/operations")
        self.assertEqual(health.status_code, 200)
        payload = health.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["database_integrity"], "ok")
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)

    def test_maintenance_enforces_retention_and_expires_overrides(self):
        self.client.get("/api/bootstrap")
        from modules.smartforecast.app import store
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=500)).isoformat()
        expired = (now - timedelta(hours=1)).isoformat()
        con = sqlite3.connect(self.db_path)
        try:
            token_id = con.execute("SELECT id FROM embed_tokens WHERE active=1 LIMIT 1").fetchone()[0]
            con.execute("UPDATE weather_snapshots SET observed_at=?", (old,))
            con.execute(
                """INSERT INTO engagement_events(site_id,token_id,event_type,occurred_at,session_hash,
                   referrer_domain,destination_url,metadata_json,dedupe_key)
                   VALUES(1,?,'view',?,'old-session-hash','','','{}','old-event')""",
                (token_id, old),
            )
            con.execute(
                """INSERT INTO manual_overrides(site_id,starts_at,ends_at,active,note,created_by,created_at)
                   VALUES(1,?,?,1,'expired','test',?)""", (old, expired, old),
            )
            con.commit()
        finally:
            con.close()
        result = store().run_maintenance(now)
        self.assertEqual(result["weather_snapshots_deleted"], 1)
        self.assertEqual(result["engagement_events_deleted"], 1)
        self.assertEqual(result["overrides_expired"], 1)

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
