# SmartForecast Dynamic Website

SmartForecast is a native SmartHub client tool that changes an embedded website
panel in response to stable weather conditions and official alerts. Staff
configuration is protected by the Hub login; only the tokenized embed and its
read-only JSON endpoint are public.

## Routes

- `/tools/smartforecast/` — staff dashboard and six-step workflow
- `/tools/smartforecast/health` — authenticated health check
- `/tools/smartforecast/api/preflight` — authenticated launch-readiness checks
- `/tools/smartforecast/api/qa/run` — authenticated, non-mutating scenario suite
- `/tools/smartforecast/api/sites` — authenticated client/site list and onboarding
- `/tools/smartforecast/api/content/<id>/publish` — approve a saved draft for the live embed
- `/tools/smartforecast/api/embed-token/rotate` — revoke the previous token and issue a replacement
- `/tools/smartforecast/embed/<token>` — public responsive embed
- `/tools/smartforecast/api/public/embed/<token>` — public read-only payload
- `/tools/smartforecast/api/report.csv` — authenticated lifecycle export

## Configuration

```text
WEATHERAPI_KEY=                  # required for live WeatherAPI checks
SMARTFORECAST_DB_PATH=          # optional local override
HUB_SCHEDULER=true              # enabled on the one Render web service
```

On Render, `docker-start.sh` defaults the database to
`/var/data/smartforecast/smartforecast.sqlite3`, on the existing persistent
disk. Locally, the default is `modules/smartforecast/data/smartforecast.sqlite3`.

Every 12 hours the scheduler stores a checksum-protected SQLite SQL dump through
SmartHub's durable JSON store. That backup is mirrored to managed Postgres. If a
new Render disk starts without the SQLite file, the module restores the latest
verified dump before applying schema updates and seed data.

## Local verification

From the repository root:

```powershell
.venv\Scripts\python.exe -m unittest -v test_smartforecast.py
.venv\Scripts\python.exe -m py_compile modules\smartforecast\*.py hub\scheduler.py wsgi.py
```

Without `WEATHERAPI_KEY`, use the simulator or manual override. The provider
refresh endpoint returns a controlled 503 and leaves cached content intact.

## Operational rules

- Official alerts can bypass normal stability delay.
- Competing matches use priority; the current phase also respects minimum
  duration and cooldown.
- Pause returns the public experience to baseline without deleting history.
- Client/site selection is server-backed; every mutation is scoped to the selected site.
- Content saves are drafts. Only an explicit approval updates the public publication.
- Token rotation revokes the old public embed immediately and is recorded in history.
- Simulations are non-mutating unless `persist` is explicitly requested.
- History is append-only at the application layer and is used for audit/export.
- Never commit a real embed token, client content approval, or provider secret.

See `docs/smartforecast-rollout.md` for release gates, QA, pilot and rollback.
