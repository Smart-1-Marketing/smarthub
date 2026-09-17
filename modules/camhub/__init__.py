"""CamHub — a live-cam conditions page with a sponsor system behind it.

Built from the "Lake Cam 2.0 — Design & Build Spec" (`docs/camhub-spec.md`),
Buckeye Lake Winery first. The rule that makes it a product rather than one
client's page: **nothing Buckeye-specific goes in code.** Every data feed is
an adapter registered against a page with IDs in its config; every
conditions tile is optional and collapses when nothing feeds it.

Layout, one file per concern, as the spec lays it out:

    adapters/   nws, usgs, beachguard, ndbc, coops, astro, scrape -- each
                with probe(lat, lon) for discovery and fetch(config) for the
                scheduled pull
    models.py   the tables, prefixed camhub_, on the shared Hub engine
    store.py    pages, sources, the conditions cache, refresh, health,
                provisioning
    tiles.py    the tile registry and the collapse rule
    verdict.py  "Good day on the water?" -- derived, no extra API
    render.py   the server-rendered page context and its JSON-LD
    builder.py  address in, sources out (geocode + concurrent probes)
    seeds.py    the Buckeye Lake Winery build, exactly as the spec resolved it
    cron.py     the scheduled refresh, run under hub/scheduler.py's leader lock
    app.py      the Flask app: staff screens under the Hub login, the cam page
                and its JSON public

Sprints 1 and 2 of the delivery plan are here: adapters, cache, cron, the
source-health screen, and the page. Sponsors render as house ads from the
page's own config until Sprint 3 gives them tables and an editor.
"""
