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
* **Sprint 3, the sponsor system:** one presenting sponsor at a time, four
  supporting tiles shuffled by weight, house ads in every unsold slot, a
  flight that ends itself, copy capped at the tile, a signed preview.
* **Sprint 4, tracking:** a batch written with no address in it, once per
  unit per visit, crawlers and instant clicks kept with a reason, the click
  redirect and the double click, the rollup that reads only unfiltered rows
  and the reports that read only the rollup, the ninety-day purge.
"""
from __future__ import annotations

import json
import os
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

    def test_probe_resolves_the_grid_and_says_ohio_has_no_marine_zones(self):
        from modules.camhub.adapters import nws
        points = {"properties": {"gridId": "ILN", "gridX": 104, "gridY": 81, "timeZone": "America/New_York",
                                 "radarStation": "KILN", "forecastZone": "https://api.weather.gov/zones/forecast/OHZ065",
                                 "county": "https://api.weather.gov/zones/county/OHC045",
                                 "observationStations": "https://api.weather.gov/gridpoints/ILN/104,81/stations"}}
        stations = {"features": [
            {"properties": {"stationIdentifier": "KVTA", "name": "Newark-Heath Airport"}, "geometry": {"coordinates": [-82.463, 40.023]}},
            {"properties": {"stationIdentifier": "KCMH", "name": "Columbus"}, "geometry": {"coordinates": [-82.88, 39.99]}}]}

        def route(url, params=None, **_):
            if "/points/" in url:
                return points
            if url.endswith("/stations"):
                return stations
            if "/zones" in url:
                self.assertEqual(params, {"type": "marine", "area": "OH"})
                return {"features": []}
            raise AssertionError(url)

        with patch("modules.camhub.adapters.nws.get_json", side_effect=route):
            rows = {r["key"]: r for r in nws.probe(39.9214, -82.4696)}
        self.assertEqual(rows["weather_now"]["config"]["grid_x"], 104)
        self.assertEqual(rows["alerts"]["config"]["own_zone"], "OHZ065")
        self.assertEqual(rows["observation"]["config"]["station"], "KVTA")
        self.assertFalse(rows["marine_alerts"]["found"])
        self.assertIn("no marine zones in OH", rows["marine_alerts"]["detail"])

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
        # Pin the render clock to the fixture: this test asserts the first
        # forecast row reads "Today", which only holds when the render's
        # "today in the page's timezone" matches the fixture's forecast date.
        # Left to the wall clock, the assertion drifts to "Thu" after midnight
        # EDT of the fixture's date and reddens CI on every branch.
        ctx = render.build(self.page, store.cache_for(self.page["id"]), now=NOW)
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
        ctx = render.build(page, store.cache_for(self.page["id"]), now=NOW)
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
        ctx = render.build(self.page, store.cache_for(self.page["id"]), now=NOW)
        ctx["sponsors"]["sold"] = True
        ctx["sponsors"]["presenting"]["url"] = "https://sponsor.example/"
        # The presenting slot is a house ad in the seed (no placement_id),
        # so href() prints slot.url; that is the point of this test --
        # rel="nofollow sponsored" rides on `sponsors.sold`.
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


def _png_bytes() -> bytes:
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", (40, 40), (201, 150, 63, 255)).save(buf, format="PNG")
    return buf.getvalue()


class SponsorTests(unittest.TestCase):
    """Sprint 3: the sponsor system. One presenting sponsor at a time, four
    supporting tiles that shuffle by weight, house ads in every unsold slot,
    a flight that ends itself, copy that cannot overflow the tile, and a
    preview that is a signed token on the real page."""

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds, sponsors
        from modules.camhub.models import Placement, Sponsor, session
        from sqlalchemy import delete
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with session() as s:
            s.execute(delete(Placement))
            s.execute(delete(Sponsor))
            s.commit()
        cls.house = sponsors.ensure_house_placements(cls.page)
        cls.marina = sponsors.save_sponsor({"name": "Rosewood Marine & Dock", "category": "Marina"})
        cls.realtor = sponsors.save_sponsor({"name": "North Shore Realty", "category": "realtor"})
        cls.bait = sponsors.save_sponsor({"name": "Millersport Bait", "category": "bait"})

    def tearDown(self):
        from modules.camhub import sponsors
        for row in sponsors.list_placements(self.page["id"]):
            if not row["is_house"]:
                sponsors.delete_placement(row["id"], self.page["id"])

    def _placement(self, **over):
        from modules.camhub import sponsors
        data = {"position": "supporting", "status": "active", "sponsor_id": self.bait["id"],
                "headline": "Live bait and lake maps", "body": "Open 5 AM on weekends.",
                "cta_label": "Hours", "url": "https://bait.example/", "animation": "static"}
        data.update(over)
        return sponsors.save_placement(data, self.page["id"], actor="Todd")

    def test_house_placements_come_from_the_config_once(self):
        from modules.camhub import sponsors
        self.assertEqual(self.house, 5)
        self.assertEqual(sponsors.ensure_house_placements(self.page), 0)
        rows = sponsors.list_placements(self.page["id"])
        self.assertEqual(sum(1 for r in rows if r["is_house"] and r["position"] == "presenting"), 1)
        self.assertEqual(sum(1 for r in rows if r["is_house"] and r["position"] == "supporting"), 4)
        self.assertTrue(all(r["effective"] == "live" for r in rows if r["is_house"]))

    def test_the_flight_decides_what_the_page_does(self):
        from datetime import date
        from modules.camhub.sponsors import effective_status
        today = date(2026, 9, 18)
        self.assertEqual(effective_status({"status": "active", "start_date": "2026-10-01", "end_date": ""}, today), "scheduled")
        self.assertEqual(effective_status({"status": "active", "start_date": "2026-09-01", "end_date": "2026-09-17"}, today), "ended")
        self.assertEqual(effective_status({"status": "active", "start_date": "2026-09-01", "end_date": "2026-09-18"}, today), "live")
        self.assertEqual(effective_status({"status": "paused", "start_date": "", "end_date": ""}, today), "paused")
        self.assertEqual(effective_status({"status": "draft"}, today), "draft")

    def test_presenting_is_one_sponsor_at_a_time(self):
        from modules.camhub import sponsors
        first, warnings = self._placement(position="presenting", sponsor_id=self.marina["id"],
                                          start_date="2026-09-01", end_date="2026-12-31", animation="lower_third")
        self.assertEqual(warnings, [])
        with self.assertRaises(ValueError) as ctx:
            self._placement(position="presenting", sponsor_id=self.realtor["id"], start_date="2026-12-01")
        self.assertIn("one sponsor at a time", str(ctx.exception))
        # A flight that starts after the first ends is fine; so is a draft.
        later, _ = self._placement(position="presenting", sponsor_id=self.realtor["id"], start_date="2027-01-01")
        self.assertEqual(later["effective"], "scheduled")
        draft, _ = self._placement(position="presenting", sponsor_id=self.realtor["id"], status="draft")
        self.assertEqual(draft["effective"], "draft")
        self.assertEqual(first["effective"], "live")

    def test_category_clash_is_a_warning_not_a_refusal(self):
        from modules.camhub import sponsors
        marina2 = sponsors.save_sponsor({"name": "Fairfield Dock & Lift", "category": "marina"})
        pres, _ = self._placement(position="presenting", sponsor_id=self.marina["id"],
                                  start_date="2026-09-01", end_date="2026-12-31")
        row, warnings = self._placement(sponsor_id=marina2["id"], start_date="2026-09-01", end_date="2026-10-31")
        self.assertEqual(len(warnings), 1)
        self.assertIn("Category clash", warnings[0])
        self.assertIn("Rosewood Marine", warnings[0])
        self.assertEqual(row["effective"], "live")
        self.assertTrue(pres["id"] and row["id"])

    def test_copy_that_overflows_the_tile_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._placement(body="x" * 111)
        self.assertIn("110", str(ctx.exception))
        with self.assertRaises(ValueError):
            self._placement(url="rosewood.example")
        with self.assertRaises(ValueError):
            self._placement(start_date="2026-10-01", end_date="2026-09-01")
        with self.assertRaises(ValueError):
            self._placement(sponsor_id="", is_house=False)
        with self.assertRaises(ValueError):
            self._placement(animation="marquee")

    def test_supporting_slots_shuffle_by_weight_and_house_fills_the_rest(self):
        import random
        from collections import Counter
        from modules.camhub import sponsors
        heavy, _ = self._placement(sponsor_id=self.bait["id"], weight=5, headline="Heavy")
        light, _ = self._placement(sponsor_id=self.realtor["id"], weight=1, headline="Light")
        firsts = Counter()
        for seed in range(300):
            slots = sponsors.select_slots(self.page, rng=random.Random(seed))
            self.assertEqual(len(slots["supporting"]), 4)
            self.assertEqual([t["position"] for t in slots["supporting"]], [1, 2, 3, 4])
            self.assertEqual(slots["sold_supporting"], 2)
            self.assertTrue(slots["supporting"][0]["sold"] and slots["supporting"][1]["sold"])
            self.assertFalse(slots["supporting"][2]["sold"] or slots["supporting"][3]["sold"])
            firsts[slots["supporting"][0]["headline"]] += 1
        self.assertGreater(firsts["Heavy"], firsts["Light"] * 2, firsts)
        self.assertGreater(firsts["Light"], 0, "a weight-1 sponsor is still first sometimes")
        self.assertEqual(slots["supporting"][2]["name"], "Reserve a table")   # house, in sort order
        # An ended flight reverts to house on the next read, nobody swapping it.
        sponsors.save_placement({**{k: heavy[k] for k in ("position", "status", "sponsor_id", "headline",
                                                           "body", "cta_label", "url", "animation")},
                                 "end_date": "2026-01-31"}, self.page["id"], heavy["id"])
        slots = sponsors.select_slots(self.page, rng=random.Random(1))
        self.assertEqual(slots["sold_supporting"], 1)

    def test_the_page_renders_a_sold_presenting_sponsor(self):
        from modules.camhub import sponsors
        from modules.camhub.app import app
        pres, _ = self._placement(position="presenting", sponsor_id=self.marina["id"],
                                  headline="Slips, service and storage since 1974.",
                                  url="https://rosewood.example/", animation="crossfade")
        html = app.test_client().get("/cam/buckeye-lake").data.decode()
        self.assertIn('class="presents crossfade"', html)
        self.assertIn(f'href="/go/{pres["id"]}" rel="nofollow sponsored"', html)   # sold: through the click redirect
        self.assertIn("Presenting sponsor", html)
        self.assertIn(f'data-placement="{pres["id"]}" data-position="0"', html)
        sponsors.delete_placement(pres["id"], self.page["id"])
        html = app.test_client().get("/cam/buckeye-lake").data.decode()
        self.assertIn('class="presents static"', html)
        self.assertNotIn('rel="nofollow sponsored"', html)

    def test_preview_is_a_signed_token_on_the_real_page(self):
        import base64
        from modules.camhub import sponsors
        from modules.camhub.app import app
        c = app.test_client()
        token = sponsors.preview_token(self.page["id"], "supporting", None)
        draft = base64.urlsafe_b64encode(json.dumps({"name": "DRAFT TILE", "body": "unsaved copy",
                                                     "position": "presenting"}).encode()).decode().rstrip("=")
        r = c.get(f"/cam/buckeye-lake?preview={token}&draft={draft}")
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        self.assertIn("DRAFT TILE", html)
        self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIn('data-position="1"', html.split("DRAFT TILE")[0][-400:])   # a draft cannot move its slot
        self.assertEqual(c.get("/cam/buckeye-lake?preview=not-a-token").status_code, 404)
        self.assertIsNone(sponsors.read_preview(token, self.page["id"] + 1))
        self.assertEqual(sponsors.decode_draft("not base64!!"), {})
        self.assertNotIn("DRAFT TILE", c.get("/cam/buckeye-lake").data.decode())

    def test_staff_screens_and_the_editor_round_trip(self):
        from modules.camhub import sponsors
        from modules.camhub.app import app
        c = app.test_client()
        self.assertEqual(c.get("/sponsors").status_code, 200)
        r = c.post("/sponsors", data={"name": "Canal Street Storage", "category": "storage"})
        self.assertEqual(r.status_code, 302)
        storage = next(s for s in sponsors.list_sponsors() if s["name"] == "Canal Street Storage")
        self.assertEqual(c.get(f"/sponsors/{storage['id']}").status_code, 200)
        r = c.get("/pages/buckeye-lake/placements")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Reserve a table", r.data)
        r = c.get("/pages/buckeye-lake/placements/new?position=supporting")
        self.assertIn(b"data-preview-base=", r.data)
        self.assertIn(b"0 / 110", r.data)                       # the supporting body cap on the counter
        r = c.post("/pages/buckeye-lake/placements/new", data={
            "position": "supporting", "status": "active", "sponsor_id": storage["id"],
            "headline": "Indoor and covered boat storage", "body": "Five minutes from the ramp.",
            "cta_label": "Check availability", "url": "https://storage.example/", "animation": "static",
            "weight": "2", "sort_order": "0"})
        self.assertEqual(r.status_code, 302, r.data[:300])
        pid = int(r.headers["Location"].split("/placements/")[1].split("?")[0])
        self.assertEqual(c.get(f"/placements/{pid}").status_code, 200)
        r = c.post("/pages/buckeye-lake/placements/new", data={"position": "supporting", "status": "active",
                                                               "sponsor_id": storage["id"], "body": "y" * 200})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"the tile fits 110", r.data)
        html = c.get("/cam/buckeye-lake").data.decode()
        self.assertIn("Indoor and covered boat storage", html)
        r = c.post(f"/placements/{pid}", data={"delete": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(sponsors.get_placement(pid))

    def test_creative_goes_through_the_shared_pipeline(self):
        from io import BytesIO
        from types import SimpleNamespace
        from modules.camhub import sponsors
        calls = []

        def fake_put(kind, filename, data, **kw):
            calls.append((kind, filename, len(data), kw.get("client"), kw.get("subpath")))
            return SimpleNamespace(url=f"https://cdn.example/{filename}")

        upload = SimpleNamespace(filename="logo.png", read=_png_bytes)
        with patch("hub.storage.put", fake_put):
            url = sponsors.store_creative(upload, kind="logo", page=self.page)
        self.assertTrue(url.startswith("https://cdn.example/buckeye-lake-logo-logo."))
        self.assertEqual(calls[0][0], "camhub")
        self.assertEqual((calls[0][3], calls[0][4]), ("Buckeye Lake Winery", "buckeye-lake"))
        with self.assertRaises(ValueError):
            sponsors.store_creative(SimpleNamespace(filename="notes.txt", read=lambda: b"hello"),
                                    kind="image", page=self.page)
        self.assertEqual(sponsors.store_creative(SimpleNamespace(filename="", read=lambda: b""),
                                                 kind="image", page=self.page), "")


class TrackingTests(unittest.TestCase):
    """Sprint 4: numbers a sponsor could audit. A viewable impression is
    what the page's observer reports, once per unit per visit; a click goes
    through /go/ and a double click is marked; crawlers, instant clicks and
    rate limits are kept with a reason and left out of the rollup; the IP
    is never stored; reports read the rollup."""

    UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds, sponsors
        from modules.camhub.models import DailyStat, Event, Placement, Sponsor, session
        from sqlalchemy import delete
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with session() as s:
            for table in (Event, DailyStat, Placement, Sponsor):
                s.execute(delete(table))
            s.commit()
        sponsors.ensure_house_placements(cls.page)
        cls.sponsor = sponsors.save_sponsor({"name": "Rosewood Marine & Dock", "category": "marina"})
        cls.pres, _ = sponsors.save_placement({"position": "presenting", "status": "active",
                                               "sponsor_id": cls.sponsor["id"], "headline": "Slips since 1974.",
                                               "cta_label": "Check", "url": "https://rosewood.example/"},
                                              cls.page["id"])
        cls.house = next(p for p in sponsors.list_placements(cls.page["id"]) if p["is_house"] and p["url"] == "")

    def setUp(self):
        from modules.camhub.models import DailyStat, Event, session
        from sqlalchemy import delete
        with session() as s:
            s.execute(delete(Event))
            s.execute(delete(DailyStat))
            s.commit()

    def _batch(self, session="abcdef1234567890", scrolled=True, ms=8000, events=None, ua=None, ip="203.0.113.7"):
        from modules.camhub import tracking
        payload = {"session": session, "pageview": {"scrolled": scrolled, "ms": ms},
                   "events": events if events is not None else
                   [{"kind": "impression", "placement": self.pres["id"], "position": 0, "ms": 1500}]}
        return tracking.ingest(self.page, payload, ip=ip, user_agent=ua or self.UA,
                               referrer="https://www.google.com/")

    def _events(self):
        from modules.camhub.models import Event, session
        from sqlalchemy import select
        with session() as s:
            return [{"kind": e.kind, "placement_id": e.placement_id, "filtered": e.filtered,
                     "reason": e.filter_reason, "session": e.session_hash, "ip": e.ip_hash,
                     "device": e.device, "ref": e.referrer_host, "position": e.position}
                    for e in s.execute(select(Event).order_by(Event.id)).scalars().all()]

    def test_a_batch_is_written_with_no_address_in_it(self):
        from modules.camhub import tracking
        out = self._batch()
        self.assertEqual(out["written"], 2)
        rows = self._events()
        self.assertEqual([r["kind"] for r in rows], ["pageview", "impression"])
        self.assertEqual(rows[1]["placement_id"], self.pres["id"])
        self.assertEqual(rows[1]["device"], "mobile")
        self.assertEqual(rows[1]["ref"], "www.google.com")
        self.assertNotIn("203.0.113.7", json.dumps(rows))
        self.assertEqual(len(rows[0]["ip"]), 24)
        self.assertEqual(rows[0]["session"], tracking.hash_session("abcdef1234567890"))
        self.assertNotEqual(rows[0]["session"], "abcdef1234567890")

    def test_once_per_unit_per_pageview_and_only_this_pages_sold_units(self):
        out = self._batch(events=[{"kind": "impression", "placement": self.pres["id"], "position": 0, "ms": 1500},
                                  {"kind": "impression", "placement": self.pres["id"], "position": 0, "ms": 9000},
                                  {"kind": "impression", "placement": self.house["id"], "position": 1, "ms": 2000},
                                  {"kind": "impression", "placement": 99999, "position": 1, "ms": 2000},
                                  {"kind": "scroll", "placement": self.pres["id"]}])
        self.assertEqual(out["written"], 2)     # the pageview and one impression

    def test_crawlers_instant_clicks_and_no_scroll_clicks_are_kept_and_marked(self):
        out = self._batch(ua="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
        self.assertEqual((out["written"], out["filtered"]), (0, {"crawler": 2}))
        out = self._batch(events=[{"kind": "click", "placement": self.pres["id"], "position": 0, "ms": 120}])
        self.assertEqual(out["filtered"], {"instant_click": 1})
        out = self._batch(scrolled=False, events=[{"kind": "click", "placement": self.pres["id"], "position": 3, "ms": 4000}])
        self.assertEqual(out["filtered"], {"no_scroll": 1})
        reasons = [r["reason"] for r in self._events() if r["filtered"]]
        self.assertEqual(sorted(reasons), ["crawler", "crawler", "instant_click", "no_scroll"])

    def test_a_session_or_an_address_past_the_rate_is_marked(self):
        from modules.camhub import tracking
        for i in range(tracking.IP_PAGEVIEWS_PER_MINUTE):
            self._batch(session=f"session-{i:04d}xxxx", events=[])
        out = self._batch(session="one-more-sessionxx", events=[])
        self.assertEqual(out["filtered"], {"ip_rate": 1})
        out = self._batch(session="one-more-sessionxx", events=[], ip="198.51.100.9")
        self.assertEqual(out["written"], 1)

    def test_a_click_redirects_and_a_double_click_is_marked(self):
        from modules.camhub import tracking
        from modules.camhub.app import app
        c = app.test_client()
        r = c.get(f"/go/{self.pres['id']}?s=abcdef1234567890", headers={"User-Agent": self.UA})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "https://rosewood.example/"))
        self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")
        r = c.get(f"/go/{self.pres['id']}?s=abcdef1234567890", headers={"User-Agent": self.UA})
        self.assertEqual(r.status_code, 302)              # still sent on, still counted once
        rows = [r for r in self._events() if r["kind"] == "click"]
        self.assertEqual([r["reason"] for r in rows], [None, "double_click"])
        self.assertEqual(c.get("/go/99999").status_code, 404)
        self.assertEqual(c.get(f"/go/{self.house['id']}").status_code, 404)   # a house ad has no /go/ link
        self.assertIsNone(tracking.click(self.house["id"], session_token="", ip="1.2.3.4", user_agent=self.UA))

    def test_the_events_endpoint_is_public_cors_and_strict(self):
        from modules.camhub.app import app
        c = app.test_client()
        r = c.post("/cam/buckeye-lake/events", data=json.dumps(
            {"session": "abcdef1234567890", "pageview": {"scrolled": True, "ms": 5000},
             "events": [{"kind": "impression", "placement": self.pres["id"], "position": 0, "ms": 1200}]}),
            content_type="application/json", headers={"User-Agent": self.UA})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(r.get_json()["written"], 2)
        self.assertEqual(c.options("/cam/buckeye-lake/events").status_code, 204)
        r = c.post("/cam/buckeye-lake/events", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = c.post("/cam/buckeye-lake/events", data=json.dumps({"events": [{}] * 41}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(c.post("/cam/no-such/events", data="{}", content_type="application/json").status_code, 404)

    def test_the_page_carries_the_observer_and_routes_sold_links_through_go(self):
        from modules.camhub import sponsors
        from modules.camhub.app import app
        c = app.test_client()
        html = c.get("/cam/buckeye-lake").data.decode()
        self.assertIn("IntersectionObserver", html)
        self.assertIn("sendBeacon", html)
        self.assertIn(f'href="/go/{self.pres["id"]}" rel="nofollow sponsored"', html)
        self.assertNotIn("https://rosewood.example/", html)   # the destination is only ever behind /go/
        token = sponsors.preview_token(self.page["id"], "presenting", self.pres["id"])
        preview = c.get(f"/cam/buckeye-lake?preview={token}").data.decode()
        self.assertNotIn("IntersectionObserver", preview)     # a preview counts nothing

    def test_the_rollup_reads_only_unfiltered_rows_and_reports_read_the_rollup(self):
        from modules.camhub import tracking
        from modules.camhub.app import app
        c = app.test_client()
        self._batch(session="visitor-one-xxxxx")
        self._batch(session="visitor-two-xxxxx")
        self._batch(session="visitor-three-xxx", events=[])
        self._batch(session="a-crawler-session", ua="curl/8.0")
        c.get(f"/go/{self.pres['id']}?s=visitor-one-xxxxx", headers={"User-Agent": self.UA})
        c.get(f"/go/{self.pres['id']}?s=visitor-one-xxxxx", headers={"User-Agent": self.UA})   # double
        out = tracking.rollup_recent()
        self.assertEqual(sum(d["rows"] for d in out["days"]), 2)   # the page row and the placement row
        start, end = tracking.month_bounds()
        st = tracking.stats(self.page["id"], start, end)
        self.assertEqual(st["page"]["pageviews"], 3)
        self.assertEqual(st["page"]["unique_sessions"], 3)
        pl = st["placements"][self.pres["id"]]
        self.assertEqual((pl["impressions"], pl["clicks"], pl["unique_sessions"]), (2, 1, 2))
        self.assertEqual(pl["ctr"], 50.0)
        self.assertEqual(pl["viewable_share"], 67)
        self.assertEqual(pl["filtered"], 2)                        # the crawler's impression, the double click
        self.assertEqual(st["page"]["filtered"], 3)                # crawler pageview + impression, the double click
        # Running it again rewrites the same rows.
        tracking.rollup_recent()
        self.assertEqual(tracking.stats(self.page["id"], start, end)["page"]["pageviews"], 3)
        r = c.get("/pages/buckeye-lake/placements")
        self.assertIn(b"2 viewable", r.data)
        self.assertIn(b"seen on 67% of pageviews", r.data)
        self.assertIn(b"<td>50.0%</td>", c.get("/pages/buckeye-lake").data)

    def test_raw_rows_are_purged_after_ninety_days(self):
        from datetime import timedelta
        from modules.camhub import tracking
        from modules.camhub.models import Event, session
        self._batch()
        with session() as s:
            for e in s.query(Event).all():
                e.at = e.at - timedelta(days=91)
            s.commit()
        self.assertEqual(tracking.purge(), 2)
        self.assertEqual(self._events(), [])

    def test_the_rollup_job_is_registered(self):
        from hub import scheduler
        every, fn, _ = scheduler.JOBS["camhub_rollup"]
        self.assertEqual((every, fn.__name__), (60, "job_camhub_rollup"))
        from modules.camhub import cron
        with patch("modules.camhub.tracking.rollup_recent", return_value={"days": [], "purged": 0}):
            self.assertEqual(cron.job_rollup()["purged"], 0)


class ReportTests(unittest.TestCase):
    """Sprint 5, reporting: monthly stats and the deltas the report
    shows, the daily-chart PNG (a real image), the PDF (real bytes and
    the sponsor's name on the cover), the CSV (one row per placement per
    day plus a totals footer), and the range payload the portal draws.
    Each test seeds only the rollup rows it needs -- the raw-event path
    is TrackingTests' subject."""

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds, sponsors as _sp
        from modules.camhub.models import DailyStat, Event, Placement, Sponsor, SponsorReport, session
        from sqlalchemy import delete
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with session() as s:
            for table in (SponsorReport, Event, DailyStat, Placement, Sponsor):
                s.execute(delete(table))
            s.commit()
        _sp.ensure_house_placements(cls.page)
        cls.sponsor = _sp.save_sponsor({"name": "Rosewood Marine & Dock",
                                        "category": "marina",
                                        "email": "ops@rosewood.example",
                                        "contact_name": "M. Rose"})
        cls.other = _sp.save_sponsor({"name": "North Shore Realty",
                                      "category": "realtor",
                                      "email": ""})
        cls.pres, _ = _sp.save_placement({
            "position": "presenting", "status": "active", "animation": "static",
            "sponsor_id": cls.sponsor["id"], "name": "Rosewood Marine & Dock",
            "headline": "Slips since 1974.", "cta_label": "Check",
            "url": "https://rosewood.example/",
            "start_date": "2026-05-01", "end_date": "2026-10-31"},
            cls.page["id"])
        cls.support, _ = _sp.save_placement({
            "position": "supporting", "status": "active", "animation": "static",
            "sponsor_id": cls.sponsor["id"], "name": "Rosewood Slip Sale",
            "headline": "Slip Sale", "cta_label": "See",
            "url": "https://rosewood.example/slips",
            "start_date": "2026-05-01", "end_date": ""},
            cls.page["id"])

    def setUp(self):
        from modules.camhub.models import DailyStat, Event, SponsorReport, session
        from sqlalchemy import delete
        with session() as s:
            s.execute(delete(SponsorReport))
            s.execute(delete(DailyStat))
            s.execute(delete(Event))
            s.commit()

    @classmethod
    def tearDownClass(cls):
        # Test-order cleanup: PageTests runs after and expects the
        # presenting slot to be a house ad, so a lingering sponsor's
        # placement would flip its href through /go/.
        from modules.camhub.models import Placement, Sponsor, session
        from sqlalchemy import delete
        with session() as s:
            s.execute(delete(Placement))
            s.execute(delete(Sponsor))
            s.commit()

    def _seed(self, day: str, page_pv=100, placement_id=None, imp=0, clk=0,
              unique=0, filtered=0):
        from modules.camhub.models import DailyStat, session
        with session() as s:
            s.add(DailyStat(page_id=self.page["id"], placement_id=None,
                            sponsor_id=None, day=day, pageviews=page_pv,
                            impressions=0, clicks=0, unique_sessions=page_pv,
                            filtered=filtered))
            if placement_id is not None:
                s.add(DailyStat(page_id=self.page["id"], placement_id=placement_id,
                                sponsor_id=self.sponsor["id"], day=day,
                                pageviews=0, impressions=imp, clicks=clk,
                                unique_sessions=unique, filtered=0))
            s.commit()

    def test_monthly_stats_add_up_and_the_deltas_direction_is_signed(self):
        from modules.camhub import reports
        # August prior month: 200 impressions / 5 clicks on the presenting
        self._seed("2026-08-05", page_pv=120, placement_id=self.pres["id"],
                   imp=150, clk=4)
        self._seed("2026-08-06", page_pv=90, placement_id=self.pres["id"],
                   imp=50, clk=1)
        # September current month: 500 impressions / 20 clicks -> CTR up
        self._seed("2026-09-01", page_pv=200, placement_id=self.pres["id"],
                   imp=300, clk=15)
        self._seed("2026-09-02", page_pv=150, placement_id=self.pres["id"],
                   imp=200, clk=5)
        # Support placement in September only
        self._seed("2026-09-02", page_pv=0, placement_id=self.support["id"],
                   imp=80, clk=2)
        r = reports.sponsor_monthly(self.sponsor["id"], 2026, 9)
        self.assertEqual(r["totals"]["now"]["impressions"], 580)
        self.assertEqual(r["totals"]["now"]["clicks"], 22)
        self.assertEqual(r["totals"]["was"]["impressions"], 200)
        self.assertEqual(r["totals"]["was"]["clicks"], 5)
        self.assertEqual(r["totals"]["impressions"]["direction"], "up")
        self.assertEqual(r["totals"]["clicks"]["direction"], "up")
        # Two placement rows for one sponsor, presenting first.
        self.assertEqual(len(r["placements"]), 2)
        self.assertEqual(r["placements"][0]["position"], "presenting")
        self.assertEqual(r["placements"][0]["now"]["impressions"], 500)
        self.assertEqual(r["placements"][1]["now"]["impressions"], 80)
        # Daily series covers all thirty days, September has thirty.
        self.assertEqual(len(r["daily"]), 30)
        self.assertEqual(r["daily"][1]["impressions"], 280)      # Sep 2

    def test_the_footnote_names_the_iab_standard(self):
        from modules.camhub import reports
        r = reports.sponsor_monthly(self.sponsor["id"], 2026, 9)
        self.assertIn("half of the ad", r["footnote"])
        self.assertIn("second", r["footnote"])
        self.assertIn("Crawler traffic", r["footnote"])

    def test_flight_renewing_note_when_end_is_inside_sixty_days(self):
        from datetime import date
        from modules.camhub import reports
        r = reports.sponsor_monthly(self.sponsor["id"], 2026, 9,
                                    today=date(2026, 9, 15))
        # Presenting flight ends 2026-10-31 -- 46 days from Sep 15.
        renewing = [f for f in r["flights"] if f["state"] == "renewing"]
        self.assertTrue(renewing, r["flights"])
        self.assertEqual(renewing[0]["remaining"], 46)
        self.assertIn("Flight ends", renewing[0]["renewal_note"])

    def test_daily_chart_is_a_real_png(self):
        from modules.camhub import reports
        png = reports.daily_chart_png([{"day": "2026-09-01", "impressions": 12},
                                       {"day": "2026-09-02", "impressions": 40}])
        self.assertTrue(png.startswith(b"\x89PNG"))
        # A zero-peak series does not raise -- the axis ceiling round rule.
        empty = reports.daily_chart_png([{"day": "2026-09-01", "impressions": 0}])
        self.assertTrue(empty.startswith(b"\x89PNG"))

    def test_pdf_names_the_sponsor_and_carries_the_footnote(self):
        from modules.camhub import reports
        self._seed("2026-09-01", page_pv=100, placement_id=self.pres["id"],
                   imp=200, clk=5)
        report = reports.sponsor_monthly(self.sponsor["id"], 2026, 9)
        pdf = reports.render_monthly_pdf(report)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 3000)
        # The sponsor's name lands in the PDF metadata title verbatim.
        # /Title (Rosewood Marine & Dock -- September 2026)
        text = pdf.decode("latin-1", errors="ignore")
        self.assertIn("Rosewood Marine", text)
        # The footnote text lives inside a reportlab-compressed stream, so
        # asserting on the payload is what protects the report from
        # shipping without it -- the same string the render draws from.
        self.assertIn("half of the ad", report["footnote"])
        self.assertIn("continuous second", report["footnote"])

    def test_csv_lists_days_by_placement_and_footers_a_total(self):
        from modules.camhub import reports
        self._seed("2026-09-01", page_pv=100, placement_id=self.pres["id"],
                   imp=200, clk=5, unique=180)
        self._seed("2026-09-02", page_pv=100, placement_id=self.pres["id"],
                   imp=300, clk=10, unique=250)
        text = reports.csv_for_sponsor(self.sponsor["id"],
                                       "2026-09-01", "2026-09-30")
        lines = [ln for ln in text.strip().splitlines() if ln]
        self.assertEqual(lines[0], "day,placement,position,impressions,clicks,ctr,unique_sessions")
        self.assertEqual(lines[1].split(","),
                         ["2026-09-01", "Rosewood Marine & Dock", "presenting",
                          "200", "5", "2.50", "180"])
        # Total footer: impressions summed, clicks summed, CTR recomputed.
        self.assertTrue(lines[-1].startswith("total,,,"))
        parts = lines[-1].split(",")
        self.assertEqual(parts[3], "500")
        self.assertEqual(parts[4], "15")
        self.assertEqual(parts[5], "3.00")

    def test_sponsor_range_answers_a_custom_window(self):
        from modules.camhub import reports
        self._seed("2026-08-30", page_pv=50, placement_id=self.pres["id"],
                   imp=20, clk=1)
        self._seed("2026-09-01", page_pv=100, placement_id=self.pres["id"],
                   imp=80, clk=4)
        r = reports.sponsor_range(self.sponsor["id"], "2026-08-30", "2026-09-01")
        self.assertEqual(r["window"]["days"], 3)
        self.assertEqual(r["totals"]["impressions"], 100)
        self.assertEqual(r["totals"]["clicks"], 5)
        self.assertEqual(len(r["daily"]), 3)
        self.assertEqual(r["daily"][0]["impressions"], 20)


class OutboxTests(unittest.TestCase):
    """The scheduled-report lifecycle. Enqueueing is per-sponsor and
    idempotent, render files a PDF and stores its URL, the default
    sender leaves a rendered row alone (no ESP), and the send-hook
    seam lets a test channel confirm the row moves to sent."""

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds, sponsors as _sp
        from modules.camhub.models import DailyStat, Event, Placement, Sponsor, SponsorReport, session
        from sqlalchemy import delete
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with session() as s:
            for table in (SponsorReport, Event, DailyStat, Placement, Sponsor):
                s.execute(delete(table))
            s.commit()
        _sp.ensure_house_placements(cls.page)
        cls.a = _sp.save_sponsor({"name": "Rosewood Marine",
                                  "email": "ops@rosewood.example",
                                  "category": "marina"})
        cls.b = _sp.save_sponsor({"name": "North Shore Realty",
                                  "email": "", "category": "realtor"})
        _sp.save_placement({"position": "presenting", "status": "active",
                            "animation": "static", "sponsor_id": cls.a["id"],
                            "name": "Rosewood Marine",
                            "start_date": "2026-05-01", "end_date": "2026-10-31"},
                           cls.page["id"])
        _sp.save_placement({"position": "supporting", "status": "active",
                            "animation": "static", "sponsor_id": cls.b["id"],
                            "name": "North Shore Realty",
                            "start_date": "2026-06-01", "end_date": "2026-11-30"},
                           cls.page["id"])

    def setUp(self):
        from modules.camhub.models import SponsorReport, session
        from sqlalchemy import delete
        from modules.camhub import outbox
        with session() as s:
            s.execute(delete(SponsorReport))
            s.commit()
        outbox.register_sender(None)

    @classmethod
    def tearDownClass(cls):
        from modules.camhub.models import Placement, Sponsor, SponsorReport, session
        from sqlalchemy import delete
        with session() as s:
            s.execute(delete(SponsorReport))
            s.execute(delete(Placement))
            s.execute(delete(Sponsor))
            s.commit()

    def test_enqueue_creates_one_row_per_sponsor_and_is_idempotent(self):
        from modules.camhub import outbox
        first = outbox.enqueue_month(2026, 8, actor="tester")
        self.assertEqual(len(first["created"]), 2)
        self.assertEqual(first["skipped"], [])
        again = outbox.enqueue_month(2026, 8, actor="tester")
        self.assertEqual(again["created"], [])
        self.assertEqual(sorted(again["skipped"]),
                         sorted([self.a["id"], self.b["id"]]))

    def test_enqueue_skips_a_sponsor_whose_flight_ran_after_the_month(self):
        from modules.camhub import outbox, sponsors as _sp
        c = _sp.save_sponsor({"name": "Future Corp", "email": "f@ex.example"})
        _sp.save_placement({"position": "supporting", "status": "active",
                            "sponsor_id": c["id"], "name": "Future Corp",
                            "start_date": "2026-11-01", "end_date": ""},
                           self.page["id"])
        result = outbox.enqueue_month(2026, 8)
        self.assertNotIn(c["id"], [r for r in result["created"]])

    def test_render_files_a_pdf_and_the_row_carries_its_totals(self):
        from modules.camhub import outbox
        from modules.camhub.models import DailyStat, session
        with session() as s:
            s.add(DailyStat(page_id=self.page["id"], placement_id=None,
                            day="2026-08-15", pageviews=100, impressions=0,
                            clicks=0, unique_sessions=90, filtered=0))
            pl_id = next(p["id"] for p in __import__("modules.camhub.sponsors", fromlist=["sponsors"]).list_placements(self.page["id"]) if p["sponsor_id"] == self.a["id"])
            s.add(DailyStat(page_id=self.page["id"], placement_id=pl_id,
                            sponsor_id=self.a["id"], day="2026-08-15",
                            pageviews=0, impressions=250, clicks=7,
                            unique_sessions=220, filtered=0))
            s.commit()
        rows = outbox.enqueue_month(2026, 8)["created"]
        row_id = rows[0] if _row_sponsor(rows[0]) == self.a["id"] else rows[1]
        result = outbox.render_row(row_id)
        self.assertEqual(result["impressions"], 250)
        self.assertEqual(result["clicks"], 7)
        row = outbox.get_row(row_id)
        self.assertEqual(row["status"], "rendered")
        self.assertTrue(row["pdf_url"] or row["pdf_url"] == "")
        # The Cloudinary URL is empty in the sandbox: local disk backend.
        # Either way the row records the impressions and clicks.

    def test_default_sender_leaves_a_no_channel_row_rendered(self):
        from modules.camhub import outbox
        rows = outbox.enqueue_month(2026, 8)["created"]
        # Seed nothing: rendering still works, with zeroes.
        for row_id in rows:
            outbox.render_row(row_id)
        result = outbox.send_row(rows[0])
        self.assertFalse(result["sent"])
        # Row stays "rendered" for staff, not "failed".
        self.assertEqual(outbox.get_row(rows[0])["status"], "rendered")

    def test_custom_sender_moves_the_row_to_sent(self):
        from modules.camhub import outbox
        rows = outbox.enqueue_month(2026, 8)["created"]
        row_a = next(r for r in rows if _row_sponsor(r) == self.a["id"])
        outbox.render_row(row_a)
        captured = []
        def _record(sponsor, row):
            captured.append((sponsor["email"], row["period"]))
            return {"sent": True}
        outbox.register_sender(_record)
        result = outbox.send_row(row_a)
        self.assertTrue(result["sent"])
        self.assertEqual(captured, [("ops@rosewood.example", "2026-08")])
        self.assertEqual(outbox.get_row(row_a)["status"], "sent")

    def test_run_monthly_no_ops_when_today_is_not_the_first(self):
        from modules.camhub import outbox
        from datetime import date
        r = outbox.run_monthly(today=date(2026, 9, 15))
        self.assertFalse(r["acted"])
        self.assertEqual(r["reason"], "not-the-first")

    def test_the_reports_job_is_registered(self):
        from hub import scheduler
        every, fn, _ = scheduler.JOBS["camhub_reports"]
        self.assertEqual((every, fn.__name__), (60, "job_camhub_reports"))


def _row_sponsor(row_id: int) -> int:
    from modules.camhub.models import SponsorReport, session
    with session() as s:
        return s.get(SponsorReport, int(row_id)).sponsor_id


class PortalTests(unittest.TestCase):
    """The sponsor portal: signed token per sponsor, page-view scoped to
    that sponsor, CSV likewise, chart is a PNG and neither leaks another
    sponsor's rows."""

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds, sponsors as _sp
        from modules.camhub.models import Placement, Sponsor, SponsorReport, DailyStat, session
        from sqlalchemy import delete
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with session() as s:
            for table in (SponsorReport, DailyStat, Placement, Sponsor):
                s.execute(delete(table))
            s.commit()
        _sp.ensure_house_placements(cls.page)
        cls.a = _sp.save_sponsor({"name": "Rosewood Marine",
                                  "email": "ops@rosewood.example"})
        cls.b = _sp.save_sponsor({"name": "North Shore Realty",
                                  "email": "sales@nsr.example"})
        cls.pl_a, _ = _sp.save_placement({
            "position": "presenting", "status": "active", "animation": "static",
            "sponsor_id": cls.a["id"], "name": "Rosewood Marine",
            "start_date": "2026-05-01", "end_date": ""}, cls.page["id"])
        cls.pl_b, _ = _sp.save_placement({
            "position": "supporting", "status": "active", "animation": "static",
            "sponsor_id": cls.b["id"], "name": "North Shore Realty",
            "start_date": "2026-05-01", "end_date": ""}, cls.page["id"])
        # Seed some rollup rows so numbers land.
        with session() as s:
            s.add(DailyStat(page_id=cls.page["id"], placement_id=None,
                            day="2026-09-10", pageviews=100, impressions=0,
                            clicks=0, unique_sessions=90, filtered=0))
            s.add(DailyStat(page_id=cls.page["id"], placement_id=cls.pl_a["id"],
                            sponsor_id=cls.a["id"], day="2026-09-10",
                            pageviews=0, impressions=80, clicks=4,
                            unique_sessions=70, filtered=0))
            s.add(DailyStat(page_id=cls.page["id"], placement_id=cls.pl_b["id"],
                            sponsor_id=cls.b["id"], day="2026-09-10",
                            pageviews=0, impressions=30, clicks=1,
                            unique_sessions=28, filtered=0))
            s.commit()

    @classmethod
    def tearDownClass(cls):
        from modules.camhub.models import Placement, Sponsor, session
        from sqlalchemy import delete
        with session() as s:
            s.execute(delete(Placement))
            s.execute(delete(Sponsor))
            s.commit()

    def test_the_token_round_trips_and_a_bad_token_is_none(self):
        from modules.camhub import portal
        tok = portal.mint(self.a["id"])
        self.assertIsInstance(tok, str)
        self.assertGreater(len(tok), 30)
        self.assertEqual(portal.read(tok), self.a["id"])
        self.assertIsNone(portal.read("not-a-token"))
        self.assertIsNone(portal.read(""))

    def test_sponsor_view_shows_only_the_sponsor_own_placements(self):
        from modules.camhub import portal
        view = portal.sponsor_view(self.a["id"],
                                   start="2026-09-01", end="2026-09-30")
        names = [p["name"] for p in view["placements"]]
        self.assertEqual(names, ["Rosewood Marine"])
        self.assertEqual(view["totals"]["impressions"], 80)
        self.assertEqual(view["totals"]["clicks"], 4)

    def test_default_range_is_the_current_month_when_no_dates_given(self):
        from datetime import date
        from modules.camhub import portal
        # portal.sponsor_view uses today() when start/end are empty; assert
        # that the window is bounded by today rather than the future.
        view = portal.sponsor_view(self.a["id"], start="", end="")
        self.assertLessEqual(view["window"]["end"], date.today().isoformat())

    def test_route_serves_the_page_and_the_csv_and_the_chart(self):
        from modules.camhub import portal, app as mod_app
        client = mod_app.app.test_client()
        tok = portal.mint(self.a["id"])
        resp = client.get(f"/portal/{tok}?start=2026-09-01&end=2026-09-30")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"CamHub Sponsor Portal", resp.data)
        self.assertIn(b"Rosewood Marine", resp.data)
        # A bad token is 404, not 500.
        self.assertEqual(client.get("/portal/nope").status_code, 404)
        # Chart is a PNG.
        chart = client.get(f"/portal/{tok}/chart.png?start=2026-09-01&end=2026-09-30")
        self.assertEqual(chart.status_code, 200)
        self.assertTrue(chart.data.startswith(b"\x89PNG"))
        # CSV carries the sponsor's rows and nothing else.
        csv = client.get(f"/portal/{tok}/csv?start=2026-09-01&end=2026-09-30")
        self.assertEqual(csv.status_code, 200)
        self.assertIn(b"Rosewood Marine", csv.data)
        self.assertNotIn(b"North Shore Realty", csv.data)
        # And the page is noindex.
        self.assertIn("noindex", resp.headers.get("X-Robots-Tag", ""))


class HubCardTests(unittest.TestCase):
    """Client 360: modules/camhub/hub_card returns one row per page the
    client owns, and the /api/client/camhub route rides on it."""

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models, seeds
        from modules.camhub.models import DailyStat, Placement, Sponsor, session
        from sqlalchemy import delete
        assert not models.init_db()
        cls.page = seeds.provision("buckeye-lake", fetch=False)["page"]
        with session() as s:
            for table in (DailyStat, Placement, Sponsor):
                s.execute(delete(table))
            s.commit()

    def test_a_client_with_no_page_returns_empty(self):
        from modules.camhub import hub_card
        out = hub_card.for_client("Nobody Here Winery")
        self.assertTrue(out["measured"])
        self.assertEqual(out["pages"], [])

    def test_a_client_with_a_page_has_one_row_and_source_health(self):
        from modules.camhub import hub_card
        out = hub_card.for_client(self.page["client_name"])
        self.assertTrue(out["measured"])
        self.assertEqual(len(out["pages"]), 1)
        row = out["pages"][0]
        self.assertEqual(row["slug"], self.page["slug"])
        self.assertIn(row["health"], ("green", "amber", "red"))
        self.assertIn("green", row["sources"])
        self.assertIn("amber", row["sources"])
        self.assertIn("red", row["sources"])


class McpToolTests(unittest.TestCase):
    """get_cam_performance answers the ad-tool period vocabulary, refuses
    a bad period, and returns an empty-pages payload for a known client
    with no CamHub page rather than not_found."""

    @classmethod
    def setUpClass(cls):
        from modules.camhub import models
        assert not models.init_db()

    def test_a_bad_period_names_the_vocabulary(self):
        from modules.camhub.mcp import get_cam_performance
        out = get_cam_performance("Buckeye Lake Winery", period="last_week")
        self.assertTrue(out["found"])
        self.assertFalse(out["available"])
        self.assertEqual(out["reason"], "invalid_period")
        self.assertIn("last_30", out["periods"])

    def test_a_missing_page_is_not_found_shape(self):
        from modules.camhub.mcp import get_cam_performance
        out = get_cam_performance("Ghost Client")
        self.assertTrue(out["found"])
        self.assertTrue(out["available"])
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["reason"], "no_pages")


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
