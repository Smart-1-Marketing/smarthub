# Industry Landing Page Factory — first version

Sales → Industry Factory (`/sales/industry-factory`) is separate from the existing Landing Page Maker. No existing vertical routes, weather evaluators, embed policy, or lead delivery paths are removed.

## Workflow

Choose Roofing, service, city/state or ZIP, radius (1–200 miles), conversion goal, and up to three supported triggers. Create a draft. Generate its page and planning report, and optionally queue creative concepts. Review the real page at desktop/mobile widths and complete all four QA attestations. Publish to the Hub. Copy the embed script snippet into the website. Change the setup fields and use either clone action to create another service/location version.

Published page instances are immutable through the factory API. Each clone has a new ID, a parent ID and an incremented version, a fresh pack snapshot, no generated artifacts, and an empty QA checklist. The factory stores its rows in `hub_industry_pages`, created by the existing shared-database boot path; no existing table is altered. Page and report generation are synchronous and show ready only once stored. Creative generation uses the existing queued/running/done/failed worker states and retries.

## Configuration and integration

- `hub/industry_packs/roofing.json`: versioned industry configuration, service/goal choices, roofing copy, widget requirements, trigger references, and creative dispatch profile.
- `hub/industry_config.py`: validates selections and resolves SmartForecast / Hub trigger references. Thresholds are never copied into the pack. Roofing uses `roofing_severe_storm`, `high-wind-shingle-risk`, and `wind-driven-rain`.
- `hub/industry_factory.py`: page storage, authenticated staff routes, preview/publish, public page/widget/report, and canonical lead metadata resolution.
- `hub/industry_creative.py` and the small `hub/creative_jobs.py` adapter: `roofing_weather_v1` selects an industry concept job. Existing source-based radio dispatch remains available. Jobs snapshot the profile and return three lifecycle concepts plus six display/social size briefs. They do not render finished media or send it to clients.
- `hub/__init__.py`: registers the blueprint before shared table creation, exempts only public/preview HTML from staff chrome, and routes industry submissions through canonical metadata resolution before the existing lead capture call.
- `hub/ghl_contacts.py`: adds optional installation-specific custom-field mappings; the existing contact upsert and retry flow remain responsible for delivery.
- `hub/sidebar.py`, `hub/templates/industry_*.html`, `hub/static/industry-*`: responsive staff UI, public page/report and widget. Public embeds reuse `hub.embed.framable` and `with_reporter`.

The public widget posts to `/api/leads/capture` with `source='landing'`. The server resolves industry, family, page ID/version, service, market/radius, trigger profile/IDs, creative profile and conversion goal from the published instance. It retains bounded UTM source/medium/campaign/content/term and referrer values. Existing rate limits still apply. Lead tags include industry, family, market and weather-report offer alongside `smart1-hub`, `landing`, and the page tag. A failed response does not show a success message in the widget.

## External setup before production use

1. Deploy this branch through the normal SmartHub deployment process and confirm database table creation. This implementation does not deploy or change smart1marketing.com.
2. Use the existing GHL private token and sub-account/location configuration. To map metadata to GHL, set optional `GHL_INDUSTRY_<KEY>_FIELD_ID` variables, where KEY is one of INDUSTRY_ID, INDUSTRY_FAMILY, PAGE_ID, PAGE_VERSION, SERVICE, MARKET, RADIUS, TRIGGER_PROFILE, TRIGGER_IDS, CREATIVE_PROFILE, CONVERSION_GOAL, UTM_SOURCE, UTM_MEDIUM, UTM_CAMPAIGN, UTM_CONTENT, UTM_TERM or REFERRER. Unmapped values remain in the Hub lead record.
3. Configure the existing report URL custom-field mapping if a GHL workflow should use the report link. Build and verify the Roofing follow-up workflow in GHL; `widget.workflow` remains null until one exists. No email-delivery promise is shown.
4. Ensure the existing creative scheduler is running. Industry concept jobs require no new provider credential. Finished image/audio/video production remains in the existing creative tools.
5. Embed the published widget on an allowed host using the generated script snippet. The shared `HUB_FRAME_ANCESTORS` policy controls framing. The factory loader forwards the parent campaign UTM parameters and parent page address automatically and resizes the frame through the existing embed message protocol. Existing non-factory loaders retain their current behavior.
6. Configure live SmartForecast campaigns/providers separately. Factory publishing publishes content, not ad spend or a live weather automation. Hail detection is not inferred from wind or a generic severe-weather warning.

## Scope of the report

The first report is an immediately available, printable planning guide with source-catalog trigger conditions/timing, audiences, media recommendations and stage-specific roofing messaging. It is not a live weather observation, geographic damage map, demand score or budget estimate. Market is a service-area label; no geocoding or weather-provider query occurs during page generation. Browser Print / Save as PDF provides a downloadable copy. Production artwork, radio audio and CTV rendering require the existing creative production workflow and approval.

## Validation

`test_industry_factory.py` exercises config loading, invalid selections, source-rule reuse, staff/public route guards, preview/publish requirements, clone reset, canonical lead metadata, GHL mapping and industry creative dispatch. Run with the repository's Python requirements installed. Also run the existing landing-maker, landing-spec, lead-delivery, creative-jobs, SmartForecast, weather-trigger and embed regression tests. All validation must use temporary databases and stubbed outbound delivery.

Verified on September 12, 2026:

| Suite | Result |
| --- | --- |
| `test_industry_core.py` | 4 tests passed |
| `test_industry_factory.py` | 7 tests passed through the composed application |
| `test_landing_maker.py` | 105 checks passed |
| `test_landing_spec.py` | 51 checks passed |
| `test_lead_delivery.py` | All delivery invariants passed |
| `test_creative_jobs.py` | 79 checks passed |
| `test_smartforecast.py` | 31 tests passed |
| `test_weather_triggers.py` | 294 checks passed |
| `test_landing_embeds.py` | 252 checks passed |
| `test_industry_browser.cjs` | Desktop/mobile workflow, generation, preview guard, QA publication, responsive public page, failed lead retry and UTM capture passed |

The browser test uses `tools/industry_browser_fixture.py`, a localhost fixture with temporary data and fake lead delivery. It does not verify live GHL delivery. Run the fixture in one terminal and the browser script in another; it requires Playwright and defaults to installed Edge (`INDUSTRY_TEST_BROWSER` selects another installed channel). Windows Python suites should run with `-X utf8`; the legacy lead-delivery test also needs access to its dedicated `/tmp/hub_lead_delivery_test` fixture folder. No dependency requirement or existing test was changed to accommodate the local environment.

The factory Python suites are registered in `.github/workflows/checks.yml`. The website embed uses the shared loader with an opt-in attribution flag; public pages also remain available without embedding.

Release preparation also verified automatic parent-page UTM/referrer forwarding and iframe resizing in the installed browser. After integration with current `main`, the factory suites, existing embed suite, CI-registration check, JavaScript syntax scan and 159-template scan passed. The full link scan reports zero broken links, zero shadowed routes and zero missing endpoints; its 36 helper checks pass. `tools/linkcheck.py` now normalizes Windows path separators before applying its existing exclusions, matching Linux behavior without adding exclusions.
