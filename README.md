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

### Proposals in Client 360

The Proposals card lists proposals from the Proposal Builder **and** the ones
you send outside it. **⇧ Upload proposal** takes a PDF or Word file plus the
date it was sent to the client; the file goes to Cloudinary
(`smart1-proposals/uploads/<client>/`) and the record stays attached to the
client permanently. The date sent stays editable in the table. Without
`CLOUDINARY_URL` set, uploads fall back to the persistent disk.

### SEO: Schema Builder & FAQ Builder

**Schema Builder** completes the client's business information *before* it
generates anything — first from what the Hub already holds (saved business
info, client profile, contacts, Brandfetch), then, only for the fields still
blank, by finding the company's Google Business Profile and reading the
address, phone, hours and category off it. Nothing already on file is ever
overwritten, and the panel shows exactly which fields are known and which are
still missing. Saved pages appear in a table — date created, editable date
added to site, page URL, schema types — with per-page view/edit, per-page
download, delete, and select-all bulk downloads.

**FAQ Builder** takes one page URL, reads that page plus the rest of the site,
and drafts 5-8 questions with answers. Each one is approved, edited, skipped,
or swapped for a fresh question; once nothing is pending it offers to save the
page to the client. The saved-pages table mirrors the schema table, and each
row downloads two ways:

- `</>` **site code** — a self-contained accordion (native `<details>`, no
  JavaScript) styled with the fonts and colours read from the page it came
  from, with its FAQPage JSON-LD inline. Drops straight into a code block on
  any site builder.
- `⇩` **review doc** — a Word document formatted for the customer to mark up.

Both builders need `OPENAI_API_KEY`; without it they fall back to templates so
the workflow still runs.

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
