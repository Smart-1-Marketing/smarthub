# SmartForecast v1 rollout

**Repository:** `smart-1-marketing/smarthub`

**Runtime:** existing `smart1-hub` Render web service

**Plan date:** 2026-09-01
**Objective:** ship weather-triggered website personalization safely, prove it
with one HVAC client, then expand by client count and industry without weakening
the lifecycle, audit, or rollback controls.

The dates below are planning windows, not promises. The implementation for all
seven sections is now present on `codex/smartforecast-v1`; rollout dates still
start when the production `WEATHERAPI_KEY`, deployment approval, and first pilot
client are available.

## Roadmap at a glance

| Horizon | Phase | Target window | Build status | Rollout status / exit gate |
| --- | --- | --- | --- | --- |
| Now | 0. Product foundation | Complete | Done | Complete |
| Now | 1. Production readiness | Sep 1–4 | Done locally | Awaiting Render secret, push/deploy, and production smoke gate |
| Now | 2. Internal QA | Sep 5–11 | Automated gate done | Awaiting live desktop/mobile exercise and reviewer sign-off |
| Next | 3. HVAC pilot | Sep 12–25 | Pilot controls done | Awaiting named client, approved content, placement, and 7–14 day observation |
| Next | 4. Limited rollout | Sep 28–Oct 16 | Multi-client operations done | Awaiting successful pilot before five-to-ten-client cohorts |
| Next | 5. Industry packs | Oct–Nov | Seven packs done | Awaiting client/content/accessibility review of selected packs |
| Next | 6. Reporting and attribution | Oct–Nov | Instrumentation done | Awaiting CRM/form integration and live reconciliation |
| Later | 7. General availability | After all gates | Operations layer done | Awaiting completed rollout gates and business/support approval |

## Phase details

### Phase 0 — Product foundation

- **Status:** Done.
- **Owner:** Smart 1 Engineering.
- **Delivered:** Native SmartHub module, 15-table SQLite model, priority and
  stability rules, official-alert bypass, minimum duration, cooldown,
  post-event recovery, pause/manual override, simulation, public tokenized
  embed, reporting CSV, demo HVAC campaign and responsive creative.
- **Dependencies:** Existing SmartHub login/mounting and scheduler.
- **Residual risk:** Demo content must be replaced or explicitly approved
  before a real client receives the embed.

### Phase 1 — Production readiness

- **Build status:** Done locally; production rollout remains gated.
- **Owner:** Smart 1 Engineering; Render secret setup by the deployment owner.
- **Started now:** `SMARTFORECAST_DB_PATH` is pinned to Render's `/var/data`
  disk; a twice-daily SQL snapshot is mirrored through SmartHub's managed
  Postgres backup; a fresh disk restores the latest verified snapshot; the
  30-minute weather job and backup job report through scheduler health; CI runs
  SmartForecast's lifecycle, storage, embed and recovery tests.
- **Also delivered:** Schema migration ledger, daily retention/override
  maintenance, operational health diagnostics, and the incident/recovery
  runbook.
- **Remaining gate:** Set `WEATHERAPI_KEY` in the live Render dashboard, deploy
  the branch, execute the smoke checklist, and confirm one successful scheduled
  check and backup in production.
- **Dependencies:** Existing Render persistent disk, managed Postgres,
  `HUB_SCHEDULER=true`, WeatherAPI account.
- **Risks:** A Blueprint edit may not update an already-created Render service.
  The Docker entrypoint therefore applies the durable database default, but the
  weather secret still requires an explicit dashboard value.

### Phase 2 — Internal QA and sandbox exercise

- **Build status:** Automated gate done; live environment exercise remains.
- **Owner:** Engineering + internal QA/content reviewer.
- **Scope:** Run all six staff screens; preview the public embed at common phone,
  tablet and desktop widths; simulate heat, cold, rain, snow, wind, watch,
  warning, post-event and clear states; verify priority conflicts, stability,
  minimum duration, cooldown, pause and manual override; test expired/invalid
  tokens and provider failure.
- **Delivered:** Launch Preflight, seven non-mutating weather scenarios,
  lifecycle/publication coverage checks, keyboard-operable tabs, responsive
  preview, and invalid-token/pause/provider-failure coverage.
- **Exit gate:** No P0/P1 defects, screenshots approved, WCAG keyboard/contrast
  review complete, scheduler check is fresh, and the rollback is rehearsed.
- **Dependencies:** Phase 1 live environment and a non-client QA token.
- **Risks:** Simulated transitions can miss real provider payload edge cases;
  retain manual review during the pilot.

### Phase 3 — HVAC pilot

- **Build status:** Pilot controls done; client activation has not started.
- **Owner:** Account lead for approval; Engineering for monitoring; client for
  final content/legal approval.
- **Scope:** One client, one postal-code service area, one embed placement,
  client-approved emergency and post-event messaging. Observe for 7–14 days.
- **Delivered:** HVAC seed content and rules, approval-safe draft/publish flow,
  preflight gate, simulator, pause/override, tokenized embed, and audit history.
- **Exit gate:** No incorrect high-priority activations, no missed provider
  checks longer than 60 minutes, zero unrecoverable history loss, and client
  sign-off on content and experience.
- **Dependencies:** Phase 2 approval, production content, target site access and
  a named rollback contact.
- **Risks:** Weather at one postal code may not represent a large service area;
  do not add multiple locations until location semantics are designed.

### Phase 4 — Limited multi-client rollout

- **Build status:** Multi-client operations done; cohort enrollment is gated on
  the pilot.
- **Owner:** Product/Engineering + Client Operations.
- **Scope:** Add client/site creation and selection, reusable HVAC and generic
  home-services templates, activation checklist, support SOP, token rotation,
  and batch health visibility. Enroll five to ten clients in controlled waves.
- **Delivered:** Server-backed site onboarding/selection, tenant-scoped APIs,
  cross-site isolation tests, draft/publication history, token rotation, and
  per-site operational/preflight visibility.
- **Exit gate:** A trained operator can onboard, verify, pause and roll back a
  client without database access; cross-client isolation tests pass.
- **Dependencies:** Successful HVAC pilot and an agreed operator workflow.
- **Risks:** The current UI is seeded around one site. Multi-client work must
  preserve tenant isolation before scale, not simply expose a site ID field.

### Phase 5 — Industry packs

- **Build status:** Seven data-driven packs done; client/content review remains.
- **Owner:** Product Marketing + Content + Engineering.
- **Scope:** Build approved rule/content packs for the prioritized industries:
  HVAC/home services first, then roofing/waterproofing/plumbing, RV/marine,
  restaurants/hospitality, ski/outdoor, and legal where weather use is valid.
- **Delivered:** HVAC, home services, RV, marine, restaurant, ski, and legal
  catalogs with tested trigger definitions and draft pre/active/post content.
- **Exit gate:** Each pack has defined triggers, claims policy, default creative,
  accessible copy, preview fixtures and automated rule tests.
- **Dependencies:** Phase 4 template model and subject-matter review.
- **Risks:** Industry packs can create misleading urgency. Every pack needs
  truth-in-advertising and client-approval controls.

### Phase 6 — Reporting and attribution

- **Build status:** Privacy-minimized instrumentation done; live destination
  reconciliation remains.
- **Owner:** Analytics + Engineering + Account team.
- **Scope:** Add privacy-conscious embed views, CTA clicks, form/call outcomes,
  campaign and content-version dimensions, export/API delivery, and a client
  report that distinguishes correlation from attributable conversion.
- **Delivered:** Deduplicated views/clicks/conversions, token-salted session
  hashes, hostname-only referrers, allowlisted metadata, content/campaign
  dimensions, rate summaries, CORS integration endpoint, and CSV exports.
- **Exit gate:** Events reconcile end to end in a test account; retention and
  consent rules are documented; reports label attribution limitations.
- **Dependencies:** Stable client/site identity and the destination analytics or
  CRM contract.
- **Risks:** The current event ledger measures lifecycle operation, not marketing
  conversion. Do not present it as ROI before attribution is implemented.

### Phase 7 — General availability and optimization

- **Build status:** V1 operations layer done; general-availability approval is
  still gated on the earlier rollout phases.
- **Owner:** Product owner, Engineering, Client Operations and Support.
- **Scope:** Define service levels, alerts, incident ownership, data retention,
  capacity tests, pricing/packaging, documentation, onboarding and quarterly
  lifecycle-rule reviews. Evaluate moving operational state from SQLite to
  native Postgres when write volume or horizontal scaling warrants it.
- **Delivered:** Health diagnostics, schema ledger, retention maintenance,
  backup freshness visibility, documented operating targets, deployment smoke
  test, incident response, privacy policy, and layered rollback procedures.
- **Exit gate:** All prior gates pass; support and product owners sign off;
  recovery and incident exercises meet the agreed objectives.
- **Dependencies:** Proven pilot and limited rollout metrics.
- **Risks:** SQLite is appropriate for the single-service v1 and backed up, but
  it is not a substitute for a scale architecture decision.

## Render production checklist

1. Confirm the service is `smart1-hub`, deploys from
   `smart-1-marketing/smarthub`, and has the `hub-data` disk at `/var/data`.
2. Set the secret `WEATHERAPI_KEY` in the live Render dashboard. Never commit it.
3. Confirm `HUB_SCHEDULER=true`. There must be exactly one scheduler lease
   holder even though Gunicorn has multiple workers.
4. Deploy the approved commit and confirm the boot log shows
   `[smartforecast] database: /var/data/smartforecast/smartforecast.sqlite3`.
5. Open `/tools/smartforecast/health` while logged in and confirm `ok: true`
   and `weather_provider_configured: true`.
6. Open the tool, save setup, refresh weather, run a non-persistent simulation,
   and verify its public `/embed/<token>` in a private browser window.
7. Confirm the scheduler dashboard records a successful `smartforecast` job
   within 30 minutes and `smartforecast_backup` within 12 hours.
8. Confirm the running build at `/api/version` matches the deployed commit.

## Release, observation and rollback

- Release first to internal QA, then one HVAC pilot, then cohorts of at most
  three clients until the limited-rollout gate is met.
- For the first 48 hours of a client activation, review every lifecycle event
  and compare it with the provider conditions.
- Pause an affected site first if content or conditions are wrong. The baseline
  experience returns without removing history.
- If the module itself is unhealthy, remove the website embed or roll Render
  back to the last known-good deploy. The SmartForecast database stays on the
  persistent disk; the latest verified SQL snapshot is also in managed Postgres.
- Record the incident, affected time window, client sites, incorrect content and
  recovery action before resuming.

## Decisions required before Phase 3

- Name the HVAC pilot client and service-area postal code.
- Approve final copy, phone/CTA destination and images.
- Choose the target website placement and an authorized rollback contact.
- Decide which analytics/CRM destination Phase 6 should use.
