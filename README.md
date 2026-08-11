# Smart 1 Hub

All six Smart 1 internal tools combined into **one app, one Render service, one login**:

| Path | Module | Was |
|---|---|---|
| `/` | Dashboard, Client 360, Activity, Status | *(new)* |
| `/clients` | Client / Creative Lookup (Knack) | knack-creative-lookup |
| `/google/` | Google Finder (GA4 · GTM · GSC · GMB) | smart1-google-finder |
| `/sites/` | Smart 1 Sites admin (Simvoly) | smart1-simvoly-admin |
| `/suite/` | Suite control panel (GoHighLevel) | smart1-suite-control-panel *(ported Node → Python)* |
| `/tools/image/` | Image Optimizer & Resizer | smart1-image-optimizer |
| `/tools/pdf/` | PDF Optimizer | smart1-pdf-optimizer *(ported FastAPI → Flask)* |

**One login gates everything** — shared team password + your name (12-hour
sessions, per-IP throttling, every login and GHL create/delete recorded in the
suite-wide Activity Log). The old per-app logins are gone; the Sites admin and
Suite panel authenticate through the Hub automatically.

**Client 360** (`/client360`, or the top search bar) looks a client up across
every system at once: Knack IOs and creative, the website record (with H&M
billing), live GA4/GTM matches from the Google Finder — each with one-click
jumps into its GA4 / GTM / Search Console / GMB tools — the Simvoly project,
and the GHL sub-account.

## Deploy on Render (Blueprint)

1. Push this folder to a GitHub repo.
2. Render → **New + → Blueprint** → connect the repo. `render.yaml` creates a
   Docker web service with a 1 GB persistent disk at `/var/data`.
3. Fill in the environment variables it prompts for (see `.env.example` for
   the full annotated list):
   - `PANEL_PASSWORD` — the one team password for the Hub
   - `GHL_PRIVATE_TOKEN`, `GHL_COMPANY_ID`, `BRANDFETCH_API_KEY` — Suite
   - `SIMVOLY_API_KEY`, `DATABASE_URL` (Render Postgres) — Sites
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY` — Google
4. Deploy, open the URL, sign in, then check **System Status** — it live-tests
   every key, token, and connection and tells you exactly what's still missing.

A module with missing config degrades gracefully (clear message on its page +
a flag on System Status); it never takes the rest of the Hub down.

### After first deploy

- **Google**: update the OAuth redirect URI in Google Cloud Console to
  `https://YOUR-SERVICE.onrender.com/google/oauth2callback`, then connect the
  three identities from the Google module. Tokens persist on the disk.
- **Sites**: point `DATABASE_URL` at your existing Render Postgres (the same
  one the old smart1-simvoly-admin used — the schema is unchanged).
- **Clients data**: `clients_app/data/*.json` are the same committed Knack
  exports as before. Keep refreshing them exactly the way you do today
  (`npm run refresh` in the old repo or the nightly GitHub Action), just
  commit the JSONs into `clients_app/data/` here instead.
- **Old services**: keep them running until you've verified each module in
  the Hub, then suspend them one at a time.

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in what you have; everything degrades gracefully
set -a; . ./.env; set +a
python wsgi.py         # http://localhost:8000
```

PDF optimization requires `ghostscript` and `qpdf` locally (the Docker image
installs them).

## How it's put together

- `wsgi.py` composes everything with Werkzeug's `DispatcherMiddleware`: the
  Hub shell at the root, each module mounted at its prefix, an **auth guard**
  in front of every mount (no hub cookie → redirect to `/login`, or 401 JSON
  for API calls), and a floating "⌂ Smart 1 Hub" chip injected into module
  pages so staff can always get back.
- `hub/` — shell app: login (`hub/auth.py`), suite-wide activity log
  (`hub/audit.py`), Knack data reader (`hub/knack_data.py`), dashboard /
  Client 360 / status APIs (`hub/__init__.py`).
- `modules/google_finder`, `modules/sites_admin`, `modules/image_optimizer` —
  the original apps, unchanged apart from Hub integration (session cookie
  names, hub auto-login for Sites, prefixed fetch URLs).
- `modules/suite_panel` — the Node control panel rewritten in Python
  (`app.py`), same API surface, same frontend (`public/index.html`).
- `modules/pdf_optimizer` — the FastAPI optimizer rewritten as Flask, same
  Ghostscript/qPDF pipeline.
- `clients_app/` — the prebuilt Knack lookup bundle; the Hub serves `/static`
  and `/data` at the root because the bundle was built with absolute paths.

## Env var compatibility notes

- `SECRET_KEY` signs the Hub login cookie **and** the Sites session. Set it
  once and keep it stable.
- `PANEL_PASSWORD` replaces the old control-panel password **and** the old
  Sites `ADMIN_USERNAME`/`ADMIN_PASSWORD` (no longer used).
- `FLASK_SECRET_KEY` is still used by the Google Finder's own session, as
  before. `TOKEN_ENCRYPTION_KEY` must never change once accounts are
  connected.
