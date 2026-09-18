"""The Buckeye Lake Winery build -- the exact configuration the Cam Builder
would produce for 13750 Rosewood Rd NE, Thornville, OH 43076.

Every value here came back from a live call while the spec was written
(2026-09-17), so this is a config file rather than a proposal. It is data,
not code: a second lake is a second dict, and nothing in the module reads
"Buckeye" anywhere but here.

What is deliberately blank, and why:

- `cam_embed_url` -- the current YouTube stream has embedding disabled by
  its owner, so the page is broken today whatever surrounds it. The page
  renders a plain placeholder until a stream that allows embedding is set.
- `config.business.url`, `config.links.*` -- the winery's reservation,
  tasting and events links were not in the spec. A house ad with no link
  renders without a button rather than pointing at a guess.
- `config.pool_datum_confirmed` -- False until one phone call to the Buckeye
  Lake State Park office confirms ODNR's pool table shares USGS's NGVD29
  datum. Until then the tile shows the raw elevation and holds the
  "above normal pool" line back; the delta is the better line, so it is
  worth the call.
"""
from __future__ import annotations

from datetime import datetime, timezone

SEED_DATE = datetime(2026, 9, 17, tzinfo=timezone.utc)

BUCKEYE_SEO_HTML = """
<p><strong>Buckeye Lake</strong> is a 3,100-acre state park lake in central Ohio, about 30 minutes east of Columbus, bordered by the villages of Buckeye Lake, Millersport and Thornville across Licking, Fairfield and Perry counties. Originally built as a feeder reservoir for the Ohio and Erie Canal in the 1820s, it is one of Ohio&rsquo;s oldest man-made lakes, and its 4.1-mile earthen dam was fully reconstructed between 2015 and 2019.</p>
<h3>Reading the lake level</h3>
<p>The level shown above comes from a USGS gauge in the lake near Watkins Island, which reports surface elevation every fifteen minutes. Buckeye Lake&rsquo;s standard summer pool is <strong>891.6 feet</strong>; ODNR draws the lake down about three feet each winter for dock and seawall work, typically from mid-November, refilling between March and May. A reading near normal pool means an ordinary day; a foot or more above usually follows heavy rain upstream.</p>
<h3>Boating on Buckeye Lake</h3>
<ul class="rules">
<li><b>Horsepower</b> Unlimited &mdash; Buckeye Lake is one of Ohio&rsquo;s unlimited-horsepower state park lakes.</li>
<li><b>Speed</b> 10&nbsp;mph everywhere by default. Faster operation only inside designated speed, ski and open zones, and only from sunrise to sunset.</li>
<li><b>After dark</b> 10&nbsp;mph applies across the entire lake, including the speed zones.</li>
<li><b>No wake</b> No wake within 300 feet of any marina, dock, fuel dock or launch ramp, and none inside the marked shore zone.</li>
<li><b>Zone hours</b> Activity zones run 9&nbsp;AM to 8&nbsp;PM, Memorial Day through Labor Day.</li>
<li><b>Education</b> Operators born on or after January 1, 1982 running more than 10&nbsp;HP need an approved boater education course.</li>
</ul>
<p class="fine">Summarized from Ohio Administrative Code 1501:47-3 and ODNR Division of Parks and Watercraft. Zone boundaries are marked by buoys on the water &mdash; confirm the current layout with the Buckeye Lake State Park office.</p>
<h3>What you&rsquo;re looking at</h3>
<p>The camera sits on the winery&rsquo;s west-facing dock, looking across the open water toward Watkins Island and the South Shore. On clear evenings the sun sets directly down the channel, which is the view the winery was built around &mdash; and the reason the cam runs at all.</p>
""".strip()

BUCKEYE_LAKE = {
    "slug": "buckeye-lake",
    "title": "Buckeye Lake Live Cam — Water Level, Weather & Boating Conditions",
    "client_name": "Buckeye Lake Winery",
    "business_name": "Buckeye Lake Winery",
    "location_name": "Buckeye Lake",
    "address": "13750 Rosewood Rd NE, Thornville, OH 43076",
    "lat": 39.921421, "lon": -82.469588,
    "timezone": "America/New_York",
    "location_type": "inland_lake",
    "cam_embed_url": "",
    "cam_embed_type": "youtube",
    "cam_caption": "Looking west across Buckeye Lake from the winery dock — Watkins Island and the South Shore beyond.",
    "seo_html": BUCKEYE_SEO_HTML,
    "status": "live",
    "config": {
        "h1": "Buckeye Lake Live Cam",
        "place": "Thornville, OH",
        "path": "/lake-cam",
        "canonical_url": "",
        "cam_label": "Cam 1 · West dock",
        "night_speed_note": "10 mph after dark",
        "pool_datum_confirmed": False,
        "business": {
            "name": "Buckeye Lake Winery",
            "street": "13750 Rosewood Rd NE", "city": "Thornville", "state": "OH",
            "zip": "43076", "url": "", "phone": "",
        },
        "lake": {"name": "Buckeye Lake", "acres": 3100, "counties": "Licking, Fairfield and Perry",
                 "description": "A 3,100-acre state park lake in central Ohio, built in the 1820s "
                                "as a feeder reservoir for the Ohio and Erie Canal."},
        "links": {"reservations": "", "tastings": "", "events": ""},
        "convert": {"heading": "Come for the wine. Stay for the view.",
                    "body": "The view above is from our dock. Lunch and dinner seven days, "
                            "tastings daily, and the sunset table is worth booking ahead."},
        "house_ads": {
            "presenting": {"name": "Buckeye Lake Winery", "badge": "B",
                           "sub": "Present the Buckeye Lake Cam",
                           "eyebrow": "Sponsorship available",
                           "headline": "This slot is available to one lake business.",
                           "body": "Exclusive presenting sponsorship of the Buckeye Lake Cam — your "
                                   "logo above the stream on every view, a feature block here, and a "
                                   "monthly performance report.",
                           "cta": "Sponsor the cam", "url": ""},
            "supporting": [
                {"name": "Reserve a table", "body": "Lunch and dinner seven days on the dock.",
                 "cta": "Reservations", "url_key": "reservations"},
                {"name": "Join the wine club", "body": "Four bottles a quarter, first pick of small lots, and free tastings.",
                 "cta": "How it works", "url_key": "tastings"},
                {"name": "Events on the water", "body": "Live music, private events and the sunset series.",
                 "cta": "See what's on", "url_key": "events"},
                {"name": "Sponsor this tile", "body": "Reach lake visitors while they plan the day. Monthly reporting included.",
                 "cta": "Get the rate card", "url": ""},
            ],
        },
        "theme": {"wine_deep": "#2E0A13", "wine": "#4A1220", "wine_lift": "#5C1A29",
                  "gold": "#C9963F", "gold_bright": "#E8B75C", "ink": "#F3EBDD"},
    },
    "sources": [
        {"key": "weather_now", "adapter": "nws", "label": "NWS forecast grid (current conditions)",
         "cadence_minutes": 10, "tolerance_minutes": 90,
         "config": {"kind": "gridpoint", "office": "ILN", "grid_x": 104, "grid_y": 81,
                    "timezone": "America/New_York"}},
        {"key": "forecast_daily", "adapter": "nws", "label": "NWS seven-day forecast",
         "cadence_minutes": 60, "tolerance_minutes": 180,
         "config": {"kind": "forecast", "office": "ILN", "grid_x": 104, "grid_y": 81}},
        {"key": "alerts", "adapter": "nws", "label": "NWS watches and warnings",
         "cadence_minutes": 15, "tolerance_minutes": 90,
         # The winery's own point resolves the zone (OHZ065). The neighbors,
         # OHZ056 and OHZ066, are listed but off: turning them on watches the
         # whole lake at the cost of alerts that do not apply at the dock.
         "config": {"kind": "alerts", "lat": 39.9214, "lon": -82.4696, "zones": [],
                    "own_zone": "OHZ065", "lake_wide_zones": ["OHZ056", "OHZ066"]}},
        {"key": "observation", "adapter": "nws", "label": "Nearest NWS observation",
         "cadence_minutes": 10, "tolerance_minutes": 90,
         "config": {"kind": "station", "station": "KVTA",
                    "station_name": "Newark-Heath Airport", "distance_mi": 12}},
        {"key": "lake_level", "adapter": "usgs", "label": "USGS lake surface elevation",
         "cadence_minutes": 15, "tolerance_minutes": 120,
         "config": {"site": "395540082291600", "site_name": "Buckeye Lake near Watkins Island",
                    "parameter": "62614", "site_type": "LK", "lat": 39.9278, "lon": -82.4878,
                    "distance_mi": 3.5}},
        # Provisioned, not featured: precipitation at the same gauge only
        # since May 2026, so no tile reads it until it has a season behind it.
        {"key": "rainfall", "adapter": "usgs", "label": "USGS precipitation at the gauge",
         "cadence_minutes": 60, "tolerance_minutes": 240,
         "config": {"site": "395540082291600", "site_name": "Buckeye Lake near Watkins Island",
                    "parameter": "00045", "distance_mi": 3.5}},
        {"key": "advisories", "adapter": "beachguard", "label": "ODH BeachGuard advisories",
         "cadence_minutes": 60, "tolerance_minutes": 1440,
         "config": {"beach_id": 245, "beach_name": "Buckeye Lake — Fairfield", "distance_mi": 0.1}},
        {"key": "astronomy", "adapter": "astro", "label": "Sunrise, sunset, moon (computed)",
         "cadence_minutes": 360, "tolerance_minutes": 2880,
         "config": {"lat": 39.921421, "lon": -82.469588, "timezone": "America/New_York"}},
        # The one scrape target. Annual cadence; serves the seed until the
        # operator confirms the ODNR page and the selector.
        {"key": "pool_elevation", "adapter": "scrape", "label": "ODNR pool elevations (annual)",
         "cadence_minutes": 60 * 24 * 365, "tolerance_minutes": 60 * 24 * 600,
         "config": {"url": "", "rules": {}, "max_change": 3.0,
                    "seed": {"normal_ft": 891.6, "winter_ft": 888.6,
                             "drawdown": "November 15 – December 15", "refill": "March 1 – May 1"},
                    "seed_date": "2026-09-17", "seed_source": "ODNR winter drawdown table, read 2026-09-17",
                    "source_label": "ODNR"}},
    ],
}

SEEDED_CACHE = {
    "buckeye-lake": {
        "pool_elevation": {"normal_ft": 891.6, "winter_ft": 888.6,
                           "drawdown": "November 15 – December 15", "refill": "March 1 – May 1",
                           "seeded": True, "seed_date": "2026-09-17",
                           "source": "ODNR winter drawdown table, read 2026-09-17"},
    }
}

SEEDS = {"buckeye-lake": BUCKEYE_LAKE}


def provision(slug: str = "buckeye-lake", *, fetch: bool = True) -> dict:
    """Write the page and its sources, seed what was known, run a first fetch.
    Idempotent by slug: the second call updates in place."""
    from . import store
    spec = SEEDS[slug]
    page = store.upsert_page(spec)
    for key, payload in SEEDED_CACHE.get(slug, {}).items():
        existing = store.cache_for(page["id"]).get(key)
        if not existing or not existing.get("payload"):
            store.seed_cache(page["id"], key, payload, SEED_DATE)
    from . import sponsors
    result = {"page": page, "sources": len(spec["sources"]),
              "house_placements": sponsors.ensure_house_placements(page)}
    if fetch:
        result["refresh"] = store.refresh_page(slug, force=True)
    return result


def provision_from_spec(spec: dict, *, fetch: bool = True, seed_cache: dict | None = None) -> dict:
    """Write an operator-supplied spec through the same path as `provision()`.
    `spec` must carry a slug and sources; anything else is optional. Reserved
    for the Cam Builder wizard, which owns the JSON schema in `builder.py`.

    A second seed shipped later has two paths in: a file entry here, or an
    operator running the wizard. Both call this."""
    from . import store
    if not spec.get("slug"):
        raise ValueError("a slug is required")
    page = store.upsert_page(spec)
    for key, payload in (seed_cache or {}).items():
        existing = store.cache_for(page["id"]).get(key)
        if not existing or not existing.get("payload"):
            store.seed_cache(page["id"], key, payload, SEED_DATE)
    from . import sponsors
    result = {"page": page, "sources": len(spec.get("sources") or []),
              "house_placements": sponsors.ensure_house_placements(page)}
    if fetch:
        result["refresh"] = store.refresh_page(spec["slug"], force=True)
    return result


# ---- second seed: a great_lakes location, so the buoy/co-ops adapters are
#      exercised in production and the tile-collapse rule is proved on
#      a page whose water-temp and waves tiles actually light up. Coordinates
#      are the Vermilion, Ohio lighthouse, on Lake Erie's Ohio shoreline;
#      the adapters' own probes fill the sources when the wizard runs
#      probe_all() here for real. This is the ONE second seed the module
#      ships with; every other client comes through the wizard.
VERMILION_HARBOR = {
    "slug": "vermilion-harbor",
    "title": "Vermilion Harbor Live Cam — Lake Erie Conditions",
    "client_name": "Vermilion Harbor",
    "business_name": "Vermilion Harbor",
    "location_name": "Vermilion Harbor, Lake Erie",
    "address": "5741 Liberty Ave, Vermilion, OH 44089",
    "lat": 41.421100, "lon": -82.364100,
    "timezone": "America/New_York",
    "location_type": "great_lakes",
    "cam_embed_url": "",
    "cam_embed_type": "youtube",
    "cam_caption": "Looking north across the harbor toward Lake Erie's south shore.",
    "seo_html": ("<p><strong>Vermilion Harbor</strong> is on Lake Erie's Ohio shoreline, "
                 "halfway between Cleveland and Sandusky. The lighthouse at the end of the pier is "
                 "one of the oldest working navigation lights on the lake; the harbor itself is "
                 "a working port, a fishing dock and, on a summer weekend, one of the busiest sailing "
                 "hubs on the south shore.</p>"),
    "status": "live",
    "config": {
        "h1": "Vermilion Harbor Live Cam",
        "place": "Vermilion, OH",
        "path": "/harbor-cam",
        "canonical_url": "",
        "cam_label": "Vermilion pier cam",
        "night_speed_note": "",
        "pool_datum_confirmed": True,        # tides adapter reads its own datum
        "business": {"name": "Vermilion Harbor", "street": "5741 Liberty Ave",
                     "city": "Vermilion", "state": "OH", "zip": "44089",
                     "url": "", "phone": ""},
        "lake": {"name": "Lake Erie", "description":
                 "The southernmost and shallowest of the Great Lakes, with the "
                 "warmest summer water on the chain and the fastest wave setup "
                 "when the wind turns north."},
        "links": {"reservations": "", "tastings": "", "events": ""},
        "convert": {"heading": "Sponsor the harbor cam.",
                    "body": "Reach every visitor watching the lake before they get in a car -- "
                            "presenting or supporting slots, monthly reporting included."},
        "house_ads": {
            "presenting": {"name": "Vermilion Harbor", "badge": "V",
                           "sub": "Present the Vermilion Cam",
                           "eyebrow": "Sponsorship available",
                           "headline": "One presenting slot on the Lake Erie live cam.",
                           "body": "Exclusive presenting sponsorship of the Vermilion Harbor cam -- "
                                   "your logo above the stream on every view, a feature block below, "
                                   "and a monthly performance report.",
                           "cta": "Sponsor the cam", "url": ""},
            "supporting": [
                {"name": "Sponsor this tile", "body": "Reach visitors while they watch the lake.",
                 "cta": "Get the rate card", "url": ""},
                {"name": "Sponsor this tile", "body": "Reach visitors while they watch the lake.",
                 "cta": "Get the rate card", "url": ""},
                {"name": "Sponsor this tile", "body": "Reach visitors while they watch the lake.",
                 "cta": "Get the rate card", "url": ""},
                {"name": "Sponsor this tile", "body": "Reach visitors while they watch the lake.",
                 "cta": "Get the rate card", "url": ""},
            ],
        },
        "theme": {"wine_deep": "#0b2547", "wine": "#0f3b73", "wine_lift": "#1746a2",
                  "gold": "#f7c948", "gold_bright": "#ffd664", "ink": "#f5f9ff"},
    },
    # sources are left empty on the seed and filled by the wizard's probe on
    # first provision: this is the point of the second seed. `wizard_seed=True`
    # tells the staff /provision route to run the builder rather than expect
    # a static source list. See modules/camhub/app.py provision_from_seed.
    "sources": [],
    "wizard_seed": True,
}

SEEDS["vermilion-harbor"] = VERMILION_HARBOR
