"""CamHub: the adapters, the collapse rule, the verdict, the cache, the page.

    python3 test_camhub.py

What each part guards, and why it is a test rather than a note:

* **Adapters normalize without a socket.** Every provider answer here is a
  recorded shape (NWS grid series in Celsius and km/h, the USGS OGC item, a
  BeachGuard row under three spellings, an NDBC text table) and the assert is
  on the number the page shows. The sandbox this was written in could not
  reach any of the hosts, so a fixture is also the only record of what the
  adapter was built against.
* **A tile with no data does not render**, a stale one collapses, fewer than
  three and the strip is gone -- the rule that makes one codebase serve a
  lake in Ohio and a marina in Florida.
* **"Above normal pool" is held back until the datum is confirmed.**
* **A failed fetch keeps the last good payload** and records the error on
  the source, so the page serves stale with a timestamp rather than blank.
* **The cam page is public and indexable; the staff screens are neither.**
* **The job is registered** in hub/scheduler.py, not merely written.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix="s1-camhub-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "camhub.db")
os.environ.setdefault("SECRET_KEY", "camhub-test")
os.environ["HUB_SCHEDULER"] = "false"
os.environ.setdefault("PANEL_PASSWORD", "camhub-test-password")

NOW = datetime(2026, 9, 17, 16, 47, tzinfo=timezone.utc)   # 12:47 PM EDT


def _vt(hours_from_now: int, dur: str = "PT1H") -> str:
    return (NOW + timedelta(hours=hours_from_now)).replace(minute=0).isoformat() + "/" + dur


GRID = {"properties": {
    "updateTime": NOW.isoformat(),
    "temperature": {"values": [{"validTime": _vt(-1), "value": 27.2}, {"validTime": _vt(0), "value": 27.8}]},
    "apparentTemperature": {"values": [{"validTime": _vt(0), "value": 29.4}]},
    "windSpeed": {"values": [{"validTime": _vt(0, "PT3H"), "value": 11.1}]},
    "windGust": {"values": [{"validTime": _vt(0, "PT3H"), "value": 19.3}]},
    "windDirection": {"values": [{"validTime": _vt(0, "PT6H"), "value": 270}]},
    "skyCover": {"values": [{"validTime": _vt(0), "value": 46}]},
    "probabilityOfPrecipitation": {"values": [{"validTime": _vt(0), "value": 20}, {"validTime": _vt(4), "value": 46}]},
    "probabilityOfThunder": {"values": [{"validTime": _vt(0), "value": 5}, {"validTime": _vt(4), "value": 46}]},
    "relativeHumidity": {"values": [{"validTime": _vt(0), "value": 61}]},
    "visibility": {"values": [{"validTime": _vt(0), "value": 16093}]},
}}

FORECAST = {"properties": {"generatedAt": NOW.isoformat(), "periods": [
    {"name": "This Afternoon", "startTime": NOW.isoformat(), "isDaytime": True, "temperature": 86,
     "temperatureUnit": "F", "windSpeed": "7 to 12 mph", "windDirection": "W",
     "shortForecast": "Chance Showers And Thunderstorms", "probabilityOfPrecipitation": {"value": 46}},
    {"name": "Tonight", "startTime": (NOW + timedelta(hours=6)).isoformat(), "isDaytime": False, "temperature": 70,
     "temperatureUnit": "F", "windSpeed": "5 mph", "windDirection": "SW", "shortForecast": "Mostly Cloudy",
     "probabilityOfPrecipitation": {"value": 30}},
    {"name": "Friday", "startTime": (NOW + timedelta(days=1)).isoformat(), "isDaytime": True, "temperature": 81,
     "temperatureUnit": "F", "windSpeed": "9 mph", "windDirection": "SW", "shortForecast": "Sunny",
     "probabilityOfPrecipitation": {"value": None}},
    {"name": "Friday Night", "startTime": (NOW + timedelta(days=1, hours=6)).isoformat(), "isDaytime": False,
     "temperature": 66, "temperatureUnit": "F", "windSpeed": "6 mph", "windDirection": "NW", "shortForecast": "Clear",
     "probabilityOfPrecipitation": {"value": None}},
]}}

ALERT = {"features": [{"id": "https://api.weather.gov/alerts/urn:x:1", "properties": {
    "id": "urn:x:1", "event": "Flood Watch", "severity": "Severe", "urgency": "Expected",
    "headline": "Flood Watch until 8 PM EDT", "onset": NOW.isoformat(), "ends": (NOW + timedelta(hours=8)).isoformat(),
    "areaDesc": "Fairfield; Licking"}}]}

STATION = {"properties": {"timestamp": NOW.isoformat(), "textDescription": "Partly Cloudy",
                          "temperature": {"value": 27.0}, "windSpeed": {"value": 9.3},
                          "windGust": {"value": None}, "windDirection": {"value": 260},
                          "relativeHumidity": {"value": 58.2}}}

USGS_ITEM = {"features": [{"properties": {"monitoring_location_id": "USGS-395540082291600", "parameter_code": "62614",
                                          "value": 892.07, "time": NOW.isoformat(), "unit_of_measure": "ft",
                                          "approval_status": "Provisional"}}]}

BEACH = [{"AdvisoryType": "Bacteria", "AdvisoryLevel": 2, "Reason": "E. coli above the recreational standard",
          "StartDate": (NOW - timedelta(days=2)).isoformat(), "EndDate": None},
         {"advisorytype": "Algal bloom", "severity": 4, "reason": "old",
          "startdate": "2025-07-24T00:00:00", "enddate": "2025-07-31T00:00:00"}]

NDBC_TEXT = ("#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
             "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
             "2026 09 17 16 40 270  4.5  6.1   0.6   4.0   3.5 280 1015.2  24.1  22.3  15.0   MM   MM    MM\n")


def _route(url, params=None, **_):
    """One fake of the provider hosts, keyed by path."""
    if "/gridpoints/ILN/104,81/forecast" in url:
        return FORECAST
    if "/gridpoints/ILN/104,81" in url:
        return GRID
    if "/alerts/active" in url:
        return ALERT
    if "/stations/KVTA/observations/latest" in url:
        return STATION
    if "latest-continuous" in url:
        return USGS_ITEM
    if "advisorieslist" in url:
        return BEACH
    if "monitoringslist" in url:
        return [{"SeasonStart": "2026-05-23", "SeasonEnd": "2026-09-30"}]
    raise AssertionError(f"unexpected URL in test: {url}")


class AdapterTests(unittest.TestCase):
    def test_gridpoint_is_fahrenheit_mph_and_hours_ahead(self):
        from modules.camhub.adapters import nws
        with patch("modules.camhub.adapters.nws.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.nws.utc_now", return_value=NOW):
            p = nws.fetch({"kind": "gridpoint", "office": "ILN", "grid_x": 104, "grid_y": 81})
        self.assertEqual(p["temp_f"], 82)
        self.assertEqual(p["feels_f"], 85)
        self.assertEqual((p["wind_dir"], p["wind_mph"], p["gust_mph"]), ("W", 7, 12))
        self.assertEqual(p["day_max_thunder_pct"], 46)
        self.assertEqual(p["visibility_mi"], 10.0)
        self.assertTrue(any(h["thunder_pct"] == 46 for h in p["hours_ahead"]))

    def test_forecast_groups_day_and_night_with_wind(self):
        from modules.camhub.adapters import nws
        with patch("modules.camhub.adapters.nws.get_json", side_effect=_route):
            p = nws.fetch({"kind": "forecast", "office": "ILN", "grid_x": 104, "grid_y": 81})
        self.assertEqual([(d["high_f"], d["low_f"]) for d in p["days"]], [(86, 70), (81, 66)])
        self.assertEqual(p["days"][0]["icon"], "storm")
        self.assertEqual(p["days"][0]["wind"], "7 to 12 mph")

    def test_alerts_dedupe_across_point_and_zones(self):
        from modules.camhub.adapters import nws
        with patch("modules.camhub.adapters.nws.get_json", side_effect=_route):
            p = nws.fetch({"kind": "alerts", "lat": 39.9214, "lon": -82.4696, "zones": ["OHZ056"]})
        self.assertEqual(len(p["alerts"]), 1)
        self.assertEqual(p["alerts"][0]["event"], "Flood Watch")
        self.assertEqual(len(p["queried"]), 2)

    def test_station_observation_labels_distance(self):
        from modules.camhub.adapters import nws
        with patch("modules.camhub.adapters.nws.get_json", side_effect=_route):
            p = nws.fetch({"kind": "station", "station": "KVTA", "station_name": "Newark-Heath Airport", "distance_mi": 12})
        self.assertEqual((p["temp_f"], p["wind_mph"], p["wind_dir"]), (81, 6, "W"))
        self.assertEqual(p["distance_mi"], 12)

    def test_unknown_kind_is_refused_by_name(self):
        from modules.camhub.adapters import nws
        from modules.camhub.adapters.http import SourceError
        with self.assertRaises(SourceError):
            nws.fetch({"kind": "marine"})

    def test_usgs_lake_level(self):
        from modules.camhub.adapters import usgs
        with patch("modules.camhub.adapters.usgs.get_json", side_effect=_route):
            p = usgs.fetch({"site": "395540082291600", "parameter": "62614"})
        self.assertEqual(p["elevation_ft"], 892.07)
        self.assertTrue(p["provisional"])
        with patch("modules.camhub.adapters.usgs.get_json", return_value={"features": []}):
            from modules.camhub.adapters.http import SourceError
            with self.assertRaises(SourceError):
                usgs.fetch({"site": "395540082291600", "parameter": "00010"})

    def test_beachguard_keeps_active_drops_expired_and_fails_closed(self):
        from modules.camhub.adapters import beachguard
        from modules.camhub.adapters.http import SourceError
        with patch("modules.camhub.adapters.beachguard.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.beachguard.utc_now", return_value=NOW):
            p = beachguard.fetch({"beach_id": 245, "beach_name": "Buckeye Lake — Fairfield"})
        self.assertEqual([a["type"] for a in p["active"]], ["Bacteria"])
        self.assertEqual(p["active"][0]["severity"], 2)
        self.assertTrue(p["in_season"])
        with patch("modules.camhub.adapters.beachguard.get_json", return_value="<html>"):
            with self.assertRaises(SourceError):
                beachguard.fetch({"beach_id": 245})
        self.assertFalse(beachguard.probe(27.9, -82.5)[0]["found"])   # Florida: Ohio-only feed

    def test_ndbc_realtime_table(self):
        from modules.camhub.adapters import ndbc
        p = ndbc.parse_realtime(NDBC_TEXT)
        self.assertEqual((p["water_temp_f"], p["wave_height_ft"], p["wind_mph"]), (72, 2.0, 10))
        self.assertEqual(p["observed_at"], "2026-09-17T16:40:00+00:00")

    def test_astronomy_is_computed_and_sane(self):
        from modules.camhub.adapters import astro
        a = astro.compute(39.921421, -82.469588, "America/New_York", NOW.date())
        self.assertTrue(a["sunrise"].startswith("2026-09-17T07:1"))
        self.assertTrue(a["sunset"].startswith("2026-09-17T19:3"))
        self.assertEqual(a["source"], "computed")
        self.assertIn(a["moon_phase"], astro.PHASES)

    def test_scrape_serves_seed_then_guards_the_extraction(self):
        from modules.camhub.adapters import scrape
        from modules.camhub.adapters.http import SourceError
        seed = {"normal_ft": 891.6, "winter_ft": 888.6}
        p = scrape.fetch({"url": "", "rules": {}, "seed": seed, "seed_date": "2026-09-17"})
        self.assertTrue(p["seeded"])
        self.assertEqual(p["normal_ft"], 891.6)
        html = "<table><tr><td>Buckeye Lake</td><td>Summer pool 891.6</td><td>Winter pool 888.6</td></tr></table>"
        cfg = {"url": "https://ohiodnr.gov/x", "rules": {"normal_ft": r"Summer pool ([\d.]+)",
                                                            "winter_ft": r"Winter pool ([\d.]+)"},
               "max_change": 3.0, "last_good": seed}
        with patch("modules.camhub.adapters.scrape.robots_allows", return_value=True), \
                patch("modules.camhub.adapters.scrape.get_text", return_value=html):
            p = scrape.fetch(cfg)
        self.assertEqual((p["normal_ft"], p["winter_ft"], p["seeded"]), (891.6, 888.6, False))
        with patch("modules.camhub.adapters.scrape.robots_allows", return_value=True), \
                patch("modules.camhub.adapters.scrape.get_text", return_value=html.replace("891.6", "931.6")):
            with self.assertRaises(SourceError):
                scrape.fetch(cfg)
        with patch("modules.camhub.adapters.scrape.robots_allows", return_value=False):
            with self.assertRaises(SourceError):
                scrape.fetch(cfg)

    def test_probe_all_never_raises(self):
        from modules.camhub import adapters
        with patch("modules.camhub.adapters.nws.probe", side_effect=RuntimeError("boom")), \
                patch("modules.camhub.adapters.usgs.probe", return_value=[]), \
                patch("modules.camhub.adapters.beachguard.probe", return_value=[]), \
                patch("modules.camhub.adapters.scrape.probe", return_value=[]):
            rows = adapters.probe_all(39.9, -82.4, "inland_lake", names=("nws", "usgs", "beachguard", "astro", "scrape"))
        self.assertTrue(any(r["adapter"] == "nws" and not r["found"] for r in rows))
        self.assertTrue(any(r["key"] == "astronomy" and r["found"] for r in rows))


def _cache(**payloads):
    return {k: {"payload": v, "fetched_at": NOW - timedelta(minutes=5), "status": "ok", "error": None}
            for k, v in payloads.items()}


PAGE = {"slug": "t", "timezone": "America/New_York", "location_type": "inland_lake",
        "config": {"night_speed_note": "10 mph after dark", "pool_datum_confirmed": False}}
WX = {"temp_f": 82, "feels_f": 85, "wind_mph": 7, "gust_mph": 12, "wind_dir": "W", "sky_pct": 46,
      "day_max_thunder_pct": 46, "day_max_precip_pct": 46,
      "hours_ahead": [{"at": (NOW + timedelta(hours=4)).isoformat(), "thunder_pct": 46, "precip_pct": 46}]}
ASTRO = {"sunset": "2026-09-17T19:35-04:00", "sunrise": "2026-09-17T07:12-04:00"}


class TileTests(unittest.TestCase):
    def test_five_tiles_at_buckeye_and_three_collapse(self):
        from modules.camhub.tiles import build_strip
        strip = build_strip(PAGE, _cache(weather_now=WX, lake_level={"elevation_ft": 892.07, "site": "395540082291600"},
                                         pool_elevation={"normal_ft": 891.6}, astronomy=ASTRO), NOW)
        self.assertTrue(strip["rendered"])
        self.assertEqual([t["key"] for t in strip["tiles"]], ["air", "wind", "lake_level", "sky", "sunset"])
        self.assertEqual({d["key"] for d in strip["dropped"]}, {"water_temp", "chop", "air_quality"})
        self.assertIn("regional", strip["tiles"][1]["sub"])
        self.assertEqual(strip["tiles"][4]["value"], "7:35")

    def test_stale_tile_collapses_and_fewer_than_three_drops_the_strip(self):
        from modules.camhub.tiles import build_strip
        cache = _cache(weather_now=WX, astronomy=ASTRO)
        cache["weather_now"]["fetched_at"] = NOW - timedelta(minutes=95)
        strip = build_strip(PAGE, cache, NOW)
        self.assertFalse(strip["rendered"])
        self.assertEqual([t["key"] for t in strip["tiles"]], ["sunset"])

    def test_lake_level_holds_the_delta_until_the_datum_is_confirmed(self):
        from modules.camhub.tiles import build_strip
        cache = _cache(lake_level={"elevation_ft": 892.07, "site": "395540082291600"},
                       pool_elevation={"normal_ft": 891.6}, weather_now=WX, astronomy=ASTRO)
        raw = next(t for t in build_strip(PAGE, cache, NOW)["tiles"] if t["key"] == "lake_level")
        self.assertEqual(raw["value"], "892.07")
        self.assertIn("datum unconfirmed", raw["sub"])
        confirmed = {**PAGE, "config": {**PAGE["config"], "pool_datum_confirmed": True}}
        delta = next(t for t in build_strip(confirmed, cache, NOW)["tiles"] if t["key"] == "lake_level")
        self.assertEqual(delta["value"], "+0.5")
        self.assertIn("above normal pool", delta["sub"])

    def test_station_is_the_fallback_path_for_the_grid(self):
        from modules.camhub.tiles import build_strip
        strip = build_strip(PAGE, _cache(observation={"temp_f": 81, "wind_mph": 6, "wind_dir": "W",
                                                      "station_name": "Newark-Heath Airport", "distance_mi": 12},
                                         astronomy=ASTRO), NOW)
        air = next(t for t in strip["tiles"] if t["key"] == "air")
        self.assertEqual(air["value"], "81")
        self.assertIn("12 mi", air["source"])


class VerdictTests(unittest.TestCase):
    def test_good_day_with_a_storm_note(self):
        from modules.camhub.verdict import verdict
        v = verdict(PAGE, _cache(weather_now=WX, astronomy=ASTRO), NOW)
        self.assertEqual(v["level"], "caution")   # 46% thunder today
        self.assertIn("Storms possible after 4 pm", v["body"])
        self.assertIn("Light west wind at 7 mph", v["body"])
        calm = {**WX, "day_max_thunder_pct": 10, "hours_ahead": []}
        v = verdict(PAGE, _cache(weather_now=calm, astronomy=ASTRO), NOW)
        self.assertEqual((v["level"], v["headline"]), ("good", "Good day on the water."))

    def test_wind_and_warnings_go_red(self):
        from modules.camhub.verdict import verdict
        v = verdict(PAGE, _cache(weather_now={**WX, "wind_mph": 21, "gust_mph": 30}), NOW)
        self.assertEqual(v["level"], "poor")
        v = verdict(PAGE, _cache(weather_now=WX, alerts={"alerts": [{"event": "Severe Thunderstorm Warning", "severity": "Severe"}]}), NOW)
        self.assertEqual(v["level"], "poor")
        self.assertIn("Severe Thunderstorm Warning is in effect", v["body"])

    def test_beach_advisory_is_fine_for_boating_not_swimming(self):
        from modules.camhub.verdict import verdict
        calm = {**WX, "day_max_thunder_pct": 10, "hours_ahead": []}
        v = verdict(PAGE, _cache(weather_now=calm, advisories={"beach_name": "Fairfield Beach", "in_season": True,
                                                              "active": [{"type": "Algal bloom", "severity": 4}]}), NOW)
        self.assertEqual(v["level"], "caution")
        self.assertEqual(v["headline"], "Fine for boating, not for swimming.")
        self.assertIn("keep people and pets out of the water", v["body"])

    def test_after_dark_carries_the_lake_rule(self):
        from modules.camhub.verdict import verdict
        night = NOW + timedelta(hours=8)
        cache = _cache(weather_now={**WX, "day_max_thunder_pct": 0, "hours_ahead": []}, astronomy=ASTRO)
        for row in cache.values():
            row["fetched_at"] = night - timedelta(minutes=5)
        v = verdict(PAGE, cache, night)
        self.assertTrue(v["after_dark"])
        self.assertIn("10 mph after dark", v["body"])

    def test_no_weather_yet_says_so(self):
        from modules.camhub.verdict import verdict
        v = verdict(PAGE, {}, NOW)
        self.assertEqual(v["headline"], "Conditions are being read.")


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from modules.camhub import models
        err = models.init_db()
        assert not err, err

    def test_provision_is_idempotent_and_seeds_the_pool(self):
        from sqlalchemy import delete
        from modules.camhub import seeds, store
        from modules.camhub.models import ConditionsCache, session
        # A refresh elsewhere in this run may already have stamped the pool
        # row with today; the seed stamp is what a FRESH provision writes.
        with session() as s:
            s.execute(delete(ConditionsCache).where(ConditionsCache.key == "pool_elevation"))
            s.commit()
        first = seeds.provision("buckeye-lake", fetch=False)
        second = seeds.provision("buckeye-lake", fetch=False)
        self.assertFalse(second["page"]["created"])
        self.assertEqual(first["page"]["id"], second["page"]["id"])
        self.assertEqual(len(store.list_pages()), 1)
        sources = store.list_sources(first["page"]["id"])
        self.assertEqual(len(sources), 9)
        self.assertEqual({s["adapter"] for s in sources}, {"nws", "usgs", "beachguard", "astro", "scrape"})
        cache = store.cache_for(first["page"]["id"])
        self.assertEqual(cache["pool_elevation"]["payload"]["normal_ft"], 891.6)
        self.assertEqual(cache["pool_elevation"]["status"], "seeded")
        self.assertEqual(cache["pool_elevation"]["fetched_at"].date(), seeds.SEED_DATE.date())

    def test_refresh_writes_good_payloads_and_keeps_them_through_a_failure(self):
        from modules.camhub import seeds, store
        from modules.camhub.adapters.http import SourceError
        page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with patch("modules.camhub.adapters.http.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.nws.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.usgs.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.beachguard.get_json", side_effect=_route):
            result = store.refresh_page("buckeye-lake", force=True)
        self.assertEqual(result["errors"], [], result)
        cache = store.cache_for(page["id"])
        self.assertEqual(cache["weather_now"]["payload"]["temp_f"], 82)
        self.assertEqual(cache["lake_level"]["payload"]["elevation_ft"], 892.07)
        self.assertEqual(cache["advisories"]["payload"]["active"][0]["type"], "Bacteria")
        self.assertTrue(all(h["state"] == "green" for h in store.health(page["id"])),
                        [(h["key"], h["state"]) for h in store.health(page["id"])])
        # The next pull fails: the error lands on the source, the payload stays.
        src = next(s for s in store.list_sources(page["id"]) if s["key"] == "lake_level")
        with patch("modules.camhub.adapters.usgs.get_json", side_effect=SourceError("HTTP 503")):
            r = store.refresh_source(src, force=True)
        self.assertFalse(r["ok"])
        cache = store.cache_for(page["id"])
        self.assertEqual(cache["lake_level"]["payload"]["elevation_ft"], 892.07)
        self.assertEqual(cache["lake_level"]["status"], "error")
        row = next(h for h in store.health(page["id"]) if h["key"] == "lake_level")
        self.assertEqual((row["state"], row["error_count"]), ("amber", 1))
        self.assertIn("503", row["last_error"])
        # Not due yet: nothing is called.
        with patch("modules.camhub.adapters.usgs.get_json", side_effect=AssertionError("called")):
            r = store.refresh_source(store.list_sources(page["id"])[0])
        self.assertEqual(r.get("skipped"), "not due")

    def test_cron_job_answers_a_dict(self):
        from modules.camhub import cron
        with patch("modules.camhub.store.refresh_due", return_value={"fetched": 0, "skipped": 9, "errors": 0}):
            self.assertEqual(cron.job_refresh()["skipped"], 9)


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with patch("modules.camhub.adapters.nws.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.usgs.get_json", side_effect=_route), \
                patch("modules.camhub.adapters.beachguard.get_json", side_effect=_route):
            from modules.camhub import store
            store.refresh_page("buckeye-lake", force=True)

    def test_render_context_carries_the_temperature_into_the_meta(self):
        from modules.camhub import render, store
        ctx = render.build(self.page, store.cache_for(self.page["id"]))
        self.assertIn("82°F", ctx["meta_description"])
        self.assertIn("892.07", ctx["meta_description"])
        self.assertTrue(ctx["strip"]["rendered"])
        self.assertEqual(len(ctx["forecast"]), 2)
        self.assertEqual(ctx["forecast"][0]["dow"], "Today")
        self.assertEqual([b["tag"] for b in ctx["advisories"]], ["Flood Watch", "Advisory"])
        ld = json.loads(ctx["jsonld"])
        self.assertEqual({d["@type"] for d in ld}, {"LocalBusiness", "Place"})   # no stream yet: no VideoObject
        self.assertEqual(len(ctx["sponsors"]["supporting"]), 4)

    def test_video_object_appears_with_an_embed_and_a_canonical(self):
        from modules.camhub import render, store
        page = {**self.page, "cam_embed_url": "https://www.youtube.com/embed/abc123",
                "config": {**self.page["config"], "canonical_url": "https://example.com/lake-cam",
                           "cam_thumbnail_url": "https://example.com/cam.jpg"}}
        ctx = render.build(page, store.cache_for(self.page["id"]))
        ld = {d["@type"]: d for d in json.loads(ctx["jsonld"])}
        self.assertTrue(ld["VideoObject"]["publication"]["isLiveBroadcast"])
        self.assertEqual(ld["BreadcrumbList"]["itemListElement"][1]["item"], "https://example.com/lake-cam")
        self.assertIn("mute=1", ctx["embed"]["src"])

    def test_module_routes(self):
        from modules.camhub.app import app
        c = app.test_client()
        r = c.get("/cam/buckeye-lake")
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        self.assertEqual(r.headers["X-Robots-Tag"], "index, follow, max-image-preview:large")
        self.assertIn('<meta name="description" content="Live Buckeye Lake webcam', html)
        self.assertIn("currently <strong>82&deg;F</strong>", html)
        self.assertIn('data-tile="lake_level"', html)
        self.assertNotIn('data-tile="water_temp"', html)
        self.assertIn("Flood Watch", html)
        self.assertIn('type="application/ld+json"', html)
        self.assertIn("Live stream returns shortly", html)   # no embed URL on the seed
        self.assertNotIn('rel="nofollow sponsored"', html)   # house ads are not paid links
        r = c.get("/cam/buckeye-lake/data.json")
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(r.get_json()["current"]["temp_f"], 82)
        self.assertEqual(c.get("/cam/no-such-page").status_code, 404)
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(c.get("/pages/buckeye-lake").status_code, 200)
        self.assertIn(b"Still to fill in", c.get("/pages/buckeye-lake").data)
        self.assertEqual(c.get("/health").status_code, 200)
        self.assertEqual(c.get("/api/probe?lat=x").status_code, 400)

    def test_sold_sponsor_links_carry_nofollow_sponsored(self):
        from flask import render_template
        from modules.camhub import render, store
        from modules.camhub.app import app
        ctx = render.build(self.page, store.cache_for(self.page["id"]))
        ctx["sponsors"]["sold"] = True
        ctx["sponsors"]["presenting"]["url"] = "https://sponsor.example/"
        with app.test_request_context("/cam/buckeye-lake"):
            html = render_template("cam.html", **ctx)
        self.assertIn('href="https://sponsor.example/" rel="nofollow sponsored"', html)

    def test_composed_app_public_and_guarded(self):
        from werkzeug.test import Client
        import wsgi
        c = Client(wsgi.application)
        r = c.get("/tools/camhub/cam/buckeye-lake")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["X-Robots-Tag"], "index, follow, max-image-preview:large")
        self.assertNotIn(b"hub-sidebar", r.data)      # no staff chrome on a client page
        r = c.get("/tools/camhub/")
        self.assertIn(r.status_code, (302, 401, 403))  # staff screen behind the login
        r = c.get("/tools/camhub/pages/buckeye-lake/refresh", method="POST")
        self.assertIn(r.status_code, (302, 401, 403, 405))


class WiringTests(unittest.TestCase):
    def test_scheduler_job_is_registered(self):
        from hub import scheduler
        every, fn, desc = scheduler.JOBS["camhub_refresh"]
        self.assertEqual(every, 5)
        self.assertEqual(fn.__name__, "job_camhub_refresh")

    def test_tile_and_mount(self):
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "hub", "templates", "tools.html"), encoding="utf-8") as fh:
            tools = fh.read()
        self.assertIn('href="/tools/camhub/"', tools)
        with open(os.path.join(root, "wsgi.py"), encoding="utf-8") as fh:
            wsgi_src = fh.read()
        self.assertIn('"/tools/camhub": _mount(camhub.app', wsgi_src)
        self.assertIn('"/tools/camhub": "tools"', wsgi_src)
        from hub.config import settings
        self.assertIn("SmartHub-CamModule", settings.camhub_user_agent)
        self.assertTrue(any(r["name"] == "CamHub data feeds" for r in settings.status()))

    def test_nothing_buckeye_specific_outside_the_seed(self):
        """The product rule: a second lake is a second seed, not a code change.
        Read as code -- string literals and names -- with docstrings and
        comments out, because prose may name the first client and code may not."""
        import ast
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "camhub")
        for dirpath, _, files in os.walk(root):
            for name in files:
                if not name.endswith(".py") or name == "seeds.py":
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                literals = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        literals.append(node.value.lower())
                    elif isinstance(node, ast.Name):
                        literals.append(node.id.lower())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                        doc = ast.get_docstring(node, clean=False)
                        if doc and doc.lower() in literals:
                            literals.remove(doc.lower())
                code = "\n".join(literals)
                self.assertNotIn("395540082291600", code, path)
                self.assertNotIn("buckeye", code, path)

if __name__ == "__main__":
    unittest.main(verbosity=1)
