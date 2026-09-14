# SmartForecast rollout verification — September 14, 2026

Decision: rollout remains gated. This record supersedes older deployment assumptions only where evidence below establishes a newer fact.

## Verified evidence

- Render service `smart1-hub` (`srv-d9tkjf3m8hqs73ddo290`) uses the Smart-1-Marketing/smarthub repository, main branch, one service instance, and the 5 GB `hub-data` disk mounted at `/var/data`.
- Render reports deployment `dep-dak04p15efls73a5ne80` live, completed at 2026-09-14 14:20:47 UTC, for commit `cf5d6cca26fc31bbe8de1fa8a9c564253ec58e97`. The live SmartHub login screen independently displays `cf5d6cc`, v1.78.1. The authenticated `/api/version` endpoint was not inspected.
- The current instance boot log at 14:20:10 UTC reports `[smartforecast] database: /var/data/smartforecast/smartforecast.sqlite3`.
- The local focused suite passed all 26 tests in 9.276 seconds using the bundled Python runtime. Coverage includes lifecycle, publication, site isolation, token invalidation, contrast calculations, engagement deduplication, retention, and fresh-disk backup restoration. This is local evidence from workspace HEAD `79b44a7614175e1d07002949ebddbecb7fbd86f5`, not an exact-live-build regression run. The module and focused test files had no local changes; other workspace files are modified.
- Browser automation connected successfully. Opening the live SmartForecast staff route redirected to sign-in. No authenticated screen or public pilot embed has been accepted by this audit.

## Remaining gates and acceptance evidence

| Gate | Current status | Evidence required to close |
| --- | --- | --- |
| Production configuration | Partially verified: deployment and durable path confirmed | Authenticated health shows expected schema, integrity `ok`, and `weather_provider_configured: true`; confirm `HUB_SCHEDULER=true` and exactly one scheduler lease holder |
| Scheduler | Unverified | Per-job records for `smartforecast` show a successful real provider check within 30 minutes, with no errors or skipped configuration; maintenance succeeds within 24 hours. Confirm every enabled pilot site has no check gap over 60 minutes |
| Backup | Local restoration passed; production recovery unverified | Successful `smartforecast_backup` within 12 hours, snapshot age below 24 hours, verified durable Postgres mirror, and restoration into a separate disposable copy with integrity/schema/site/publication/history comparison |
| Live mobile/accessibility QA | Blocked on authenticated access and QA site/token | Six staff screens, seven Scenario QA cases, phone/tablet/desktop embed, keyboard focus/order and tabs, contrast at actual content settings, invalid token, pause, provider failure, CTA, screenshots, and rollback rehearsal; no P0/P1 defects |
| HVAC pilot | Client and approved configuration not supplied | Named client, postal code, approved copy/images/CTA, website placement, rollback contact, QA sign-off; record 7–14 days of observation and review every transition in first 48 hours |
| CRM reconciliation | Destination/test account not supplied | Agreed site/campaign/content/timezone mapping; trace a synthetic view, click, and outcome through export and destination; verify duplicate handling and distinguish browser-reported from destination-verified conversions |

Logs filtered for SmartForecast from September 13 through this audit returned boot-path entries only. They do not establish either job success or job failure. A broader scheduler search returned unrelated gallery activity and was not used to close SmartForecast gates.

## Resume checklist

1. Sign in to SmartHub in the prepared Codex browser tab, then inspect `/tools/smartforecast/health`, `/tools/smartforecast/api/operations`, Launch Preflight, and scheduler job records.
2. Record full job outcomes, not only a green scheduler indicator: a job may report skipped credentials or per-site provider failures in its result.
3. Do not treat health `ok: true` as rollout readiness. In the inspected implementation it checks database integrity and schema; provider configuration, weather freshness, and backup freshness are separate fields.
4. Choose a non-client QA site/token before exercising mutations, and finish live accessibility/mobile checks before pilot activation.
5. Supply pilot identity/content/placement/rollback and CRM test-account choices. Record observed events and reconciliation evidence before closing either gate.

No deployment, production setting, publication, token rotation, CRM write, or client activation was performed during this verification.

## Production recovery addendum — 15:55 UTC

The merge/deploy continuation established additional evidence after the audit above:

- Main commit `a3f34689c50baf28fca31e2ad68d2d794e5aeb25` was live in Render deployment `dep-dak19j0ae00c73emuk6g`, completed at 15:39:17 UTC. Its boot log again confirmed the durable SmartForecast database path. The larger combined review release was still in CI at this checkpoint.
- Read-only Postgres inspection located the Hub mirror in `smart1sitesdata` (`dpg-d9lp695bedkc73c1ipr0-a`). The `smartforecast/latest-backup.json` snapshot was created at 14:24:57 UTC, mirrored at 15:39:36 UTC, and carried schema version 3 with a matching SQL SHA-256 checksum.
- That exact production SQL snapshot restored into a disposable local SQLite database. Full integrity check returned `ok`, foreign-key check returned no violations, and every dumped table's row count matched the restored database. Included: 1 site, 13 approved publications, 13 content variants, 1 embed token, 3 engagement events, 36 history rows, and 426 weather snapshots. Temporary backup and recovery files were removed afterward.
- The snapshot contains 425 real `WeatherAPI` observations (latest at 14:24:57 UTC) and one explicitly labeled demo observation. This proves historical provider activity; a backup does not prove current job freshness.
- A read-only `pg_locks` query found exactly one granted scheduler advisory lock for the application's stable lock key at the time inspected.
- The combined release's focused SmartForecast suite passed all 31 tests.

These findings close the missing production mirror/checksum and isolated snapshot-restoration evidence, and establish a single lease holder at the inspection time. Still required: current scheduler results and weather freshness, maintenance freshness, authenticated live mobile/accessibility QA, named/approved HVAC pilot with observation, and CRM test-account reconciliation. A successful backup snapshot alone does not establish the complete scheduled-job outcome or guarantee future recovery cadence.
