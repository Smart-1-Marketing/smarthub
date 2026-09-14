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

## Guided workflow and publication history

The factory now uses Setup → Generate → Review → Publish, with a next-action button explaining missing work. Output states distinguish a landing page, a planning report and creative briefs. These still do not claim live weather measurements or finished artwork.

Draft setup and five messaging fields can be edited. Saving clears generated artifacts, creative-job association and approvals. Published/archived snapshots cannot be edited. “Edit as a new version” creates a fresh draft within the same campaign; service/location clones create separate campaigns.

`hub_industry_publications` holds the active version for each stable campaign address. Publication and revision numbering take a database row lock on the root page. Publishing archives the previous live snapshot. Unpublishing disables the page, report and embed aliases. Restore requires an archived, previously approved snapshot that still passes technical QA. Existing first-release published pages work without a pointer row and are upgraded when first revised, unpublished or restored. No existing database columns are altered.

Review displays a page preview and widget preview, required lead fields, service/market/goal, desktop/mobile controls, configuration/message differences and version history. Both previews disable capture. Automatic QA inspects supported selections and weather references, required generated outputs, rendered required fields, asset existence, viewport markup and registered report/widget/lead routes. Failures block publication on the server. GHL credentials/location, core field mappings, workflow identifier and marketing-site embedding policy are configuration warnings, not claims of verified provider delivery. Manual approval covers copy, claims, branding/appearance and follow-up/publication responsibility.

Saved Industry Prospect audiences can prefill industry and headquarters geography; staff must confirm the actual service area/radius. Unsupported industries cannot be selected. The canonical audience ID and name are retained in page configuration and lead metadata. The prospect builder links into factory setup. This handoff does not buy contacts, overwrite the audience's existing landing URL or send outreach.

## Campaign reporting

`hub_industry_views` records served public page/widget views against the actual page version, with bounded UTM source/medium/campaign. Staff preview, reports, loader scripts and HEAD requests do not count. Views include reloads and bots and are explicitly not unique visitors. Tracking starts at deployment of this update.

The 30-day report reads the existing central lead store without the lead panel's 500-row display cap, excludes merged leads, and groups by version and UTM source/medium/campaign. Lead-store read failures return an unavailable response rather than zero. The latest 100 matching leads are available for staff qualification. `hub_industry_qualifications` stores the staff decision, actor and update time; qualification is not presented as a GHL opportunity sync. Counters cover every version in the campaign and remain available after unpublishing.

New database tables are created by the existing shared boot path. No new provider credentials are needed. GHL mapping/workflow setup and a real delivery check remain installation-specific. Automatic QA sends no contacts or messages.

Optional GHL mappings also accept `GHL_INDUSTRY_PUBLICATION_ID_FIELD_ID`, `GHL_INDUSTRY_AUDIENCE_ID_FIELD_ID` and `GHL_INDUSTRY_AUDIENCE_NAME_FIELD_ID`. Linked leads receive the canonical audience tag. Existing mappings and delivery behavior are retained.

Workflow validation: nine core tests cover catalog compatibility, audience validation, automatic QA failures, edited-copy escaping and invalidation, revision publication/restore/unpublish, canonical metadata and field mappings, and measured reporting/qualification. Seven composed-application tests pass. The browser test passes saved-audience prefill, desktop/mobile layout, generation and disabled preview capture, approval/publication, revision comparison, stable address, restore/unpublish, lead retry and parent-embed UTM capture.
