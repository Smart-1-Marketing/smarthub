# SmartForecast operations runbook

This runbook covers the `smart1-hub` Render service after SmartForecast is
merged and deployed. It supplements `smartforecast-rollout.md`; it does not
authorize a production deploy or replace client approval.

## Operating targets

| Signal | Target | First response |
| --- | --- | --- |
| Weather evaluation | Every enabled site checked within 60 minutes | Inspect the `smartforecast` scheduler job and WeatherAPI status |
| Recovery backup | Successful snapshot less than 24 hours old | Run `smartforecast_backup`, then inspect Postgres/jsonstore health |
| Database integrity | `quick_check=ok` and schema at the expected version | Pause affected sites and stop rollout; do not repair over the only copy |
| Public content | Only approved publications; baseline when paused | Pause the site, verify publication history, then replace/approve content |
| Provider failure | Cached/default content remains available | Confirm provider credentials and rate limits; do not force stale weather |

These are internal operating targets for the pilot, not a contractual SLA.

## Render configuration

Required:

```text
WEATHERAPI_KEY=<Render secret>
HUB_SCHEDULER=true
SMARTFORECAST_DB_PATH=/var/data/smartforecast/smartforecast.sqlite3
SMARTFORECAST_WEATHER_RETENTION_DAYS=120
SMARTFORECAST_ENGAGEMENT_RETENTION_DAYS=400
```

The Docker entrypoint supplies the persistent database path when `/var/data`
is mounted. The live Render dashboard must still receive `WEATHERAPI_KEY`;
adding it to `render.yaml` does not guarantee an existing service inherits it.

## Deployment and smoke test

1. Merge an approved branch after CI passes.
2. Confirm the Render deploy uses the intended commit and the boot log names
   `/var/data/smartforecast/smartforecast.sqlite3`.
3. Open `/api/version` and confirm v1.62.0 or the intended later release.
4. Open `/tools/smartforecast/health`; require database integrity `ok`, current
   schema, and `weather_provider_configured: true`.
5. Open the target site in SmartForecast and run Launch Preflight and Scenario
   QA. Resolve every blocking check.
6. Refresh weather, run a non-persistent simulation, and verify desktop/mobile
   previews.
7. Approve the exact content versions intended for the pilot.
8. Open the tokenized embed in a signed-out/private window; verify the CTA and
   one deduplicated view in the report.
9. Confirm the scheduler records a weather check within 30 minutes, a
   maintenance run within 24 hours, and a recovery backup within 12 hours.

## Routine operation

- Review scheduler health and `/tools/smartforecast/api/operations` daily during
  the first pilot week, then at the agreed support cadence.
- Review every lifecycle transition during the first 48 hours of each client.
- Rotate an embed token only when replacing the code immediately; rotation
  invalidates the old embed and records an audit event.
- Save content freely as drafts. Approve only after client/content review; the
  public embed continues serving the last approved publication.
- Treat engagement as observed behavior. Do not claim conversion lift without
  an agreed attribution design and comparison method.

## Incident response

### Incorrect message or activation

1. Pause the affected site. Public content returns to its approved baseline.
2. Capture site, trigger, phase, provider snapshot, content ID, and time window.
3. Decide whether the cause is rule data, provider data, approval, or engine
   behavior. Simulator results alone must not overwrite production history.
4. Correct the draft/rule, rerun Scenario QA and Launch Preflight, approve, then
   resume with a named reviewer.

### Provider outage

1. Confirm the failure in the `smartforecast` scheduler result.
2. Keep sites on cached/default content; do not invent current conditions.
3. Check the Render secret, provider account/rate limit, and outbound network.
4. Resume normal checks after one successful manual refresh and one scheduled
   check. Record the outage window.

### Database or disk problem

1. Pause rollout and preserve the current disk. Do not delete or recreate the
   only database as a diagnostic step.
2. Read `/tools/smartforecast/health` and the Render boot/deploy logs.
3. A fresh disk restores the latest checksum-verified SQL dump mirrored through
   jsonstore into managed Postgres before schema migration and seed logic run.
4. Verify integrity, schema version, site count, active tokens, publication
   status, and recent history before re-enabling sites.
5. If automatic recovery cannot verify a backup, restore in a separate copy and
   compare it before replacing production state.

## Retention and privacy

- Weather snapshots default to 120 days.
- Engagement events default to 400 days.
- Lifecycle/audit history is retained for operational accountability unless a
  separately approved policy says otherwise.
- Engagement stores no IP address, raw session ID, cookie, full referrer path,
  or arbitrary metadata. Referrers become hostnames and session IDs become
  token-salted SHA-256 hashes.
- The daily `smartforecast_maintenance` job enforces retention and closes expired
  manual overrides. Retention settings are clamped to 30–3650 days.

## Rollback

- Site-level: pause SmartForecast or remove the website iframe.
- Content-level: keep the last approved publication live; correct a draft and
  approve again.
- Token-level: rotate only if compromise is suspected and replace the embed.
- Application-level: roll Render back to the last known-good commit. The
  persistent SQLite database and Postgres-mirrored recovery snapshot remain.
