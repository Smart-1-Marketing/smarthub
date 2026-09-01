# Smart 1 Hub

All six Smart 1 internal tools combined into **one app, one Render service, one login**:

| Path | Module | Was |
|---|---|---|
| `/` | Dashboard, Client 360, Activity, Status | *(new)* |
| `/clients` | Client / Creative Lookup (Knack) | knack-creative-lookup |
| `/google/` | Google Finder (GA4 · GTM · GSC · GMB) | smart1-google-finder |
| `/sites/` | Smart 1 Sites admin (Simvoly) | smart1-simvoly-admin |
| `/suite/` | Suite control panel (GoHighLevel) | smart1-suite-control-panel *(ported Node → Python)* |
| `/scans/` | Site Scans (Insites audits) | *(new)* |
| `/tools/seo-images/` | SEO Image Pipeline | *(ported Node → Python)* |
| `/tools/image-creator/` | Image Creator (Fabric.js editor) | *(new)* |
| `/tools/bg-remover/` | Background Remover (remove.bg) | *(new)* |
| `/tools/utm/` | UTM Builder | *(new)* |
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

### Version stamp

Every page footer shows the running build (`v1.5.0 · 2026-08-17 · a1b2c3d`),
also served at `/api/version`. Open the deployed site and confirm it matches
what you pushed. Bump `VERSION` in `hub/version.py` on each deploy.

### House URLs (`/tools/seo-images/house`)

Sites we run in house: no products on the books today, but they need the same
tooling as any client. Adding one makes it selectable everywhere the Hub asks
which client something belongs to, badged **house** so nobody mistakes it for
billable work. A name that already belongs to a real client is refused, so a
paying account can't be mislabelled.

### SEO Image Pipeline (`/tools/seo-images`)

**Resizing happens first.** WebP + `quality:auto` alone does not fix an
oversized image — a 6000px camera photo stays 6000px and still lands as a
multi-megabyte asset. The longest edge is capped before anything else (EXIF
rotation applied first, so portrait photos cap on the right edge), then the
image is converted to WebP. On a 9.1 MB 6000×4000 photo:

| Preset | Result | Saving |
|---|---|---|
| Full-width hero — max 2400px *(default)* | 1.1 MB | 88% |
| Large content — max 1600px | 381 KB | 96% |
| Content image — max 1200px | 170 KB | 98% |
| Keep original size | 9.1 MB | — |

Doing it server-side rather than leaving it to Cloudinary makes the upload
itself faster, stores and bills for less, and means the saved asset is
genuinely small rather than merely served small. Images already under the cap
are left alone, and a resize that doesn't actually shrink the file is discarded.

**Company is a client picker**, searching Knack clients, website records and
house URLs in one list — with product counts and SEO/house badges. Anything
genuinely new can be added as a house site on the spot. **Project** offers the
projects already used for that client, so a second batch joins the first.

Batch up to 5 images (10 MB each). The AI is given the client context — not
just the pixels — so it writes a filename and alt text for *that client's
page*, then every one is shown for review and editing before anything is
uploaded. Approved images are saved to Cloudinary under
`smart1-seo-images/<company>/<project>/`, with the company, URL, project, page
and alt text written into each asset's Cloudinary context.

Images saved against a client surface in two places: a **Client Images** card
at the bottom of that client's Client 360 record, and on the SEO client detail
page with **See client image gallery** and **Optimize client images** buttons.
Client 360's gallery link opens the client's **full gallery** — every folder
and every source, in Client Image Uploads — resolved by name through
`/tools/image-picker/gallery/for-client`; the pipeline's own archive view
(`/tools/seo-images/gallery?company=…`) groups by project and offers the way
to the full gallery when the client has one.

Each save is recorded in a searchable archive (`/var/data/seo-images/`) with
copy-URL, copy-`<img>`-tag, edit-alt-later, delete, and CSV export. Filenames
are de-collided within a batch, and the image bytes never round-trip through
the browser — they stay server-side between the analyse and save steps.

Needs `OPENAI_API_KEY` (vision model, `OPENAI_VISION_MODEL`, default `gpt-4o`)
and Cloudinary. Without the key you still get an editable starting name rather
than a dead end.

### Image Creator (`/tools/image-creator`)

A graphics editor with **Fabric.js as the editing engine** — the design stays a
set of editable objects rather than one flattened image, which is what lets a
saved project be reopened and changed months later.

Built into the Hub rather than as a separate React service so it shares one
login and, more usefully, reaches assets the Hub already owns: the SEO image
gallery filtered by client, the Brandfetch logo and colour cache, and imagery
captured by Insites audits.

- **Universal photo search** — Pexels, Pixabay and Unsplash queried in
  parallel, normalised into one shape, deduplicated and interleaved so the grid
  mixes sources instead of grouping them. One provider failing never breaks the
  search; results are cached so paging doesn't burn rate limit. Optionally
  describe what you need in plain English and let AI derive the search terms.
- **Assets** — logos and brand colours via Brandfetch (SVG stays vector),
  Iconify icons added as vectors, Google Fonts loaded on demand.
- **Backgrounds** — photos, solid colours, gradients, and nine SVG patterns
  generated locally with colour and scale controls.
- **AI** — image and background generation, plus rewrite / shorten / headline /
  CTA on any selected text.
- **Editing** — layers with drag-reorder, lock and hide; undo/redo; alignment;
  image filters; keyboard shortcuts; PNG / JPG / WebP export at 1–3× with
  optional transparency.
- **Projects** save the Fabric JSON plus a preview, attach to a client, and are
  searchable by name, client or tag.

Fabric.js is **vendored** at `modules/image_creator/static/fabric.min.js`
rather than pulled from a CDN, so the editor works behind restrictive networks
and can't be taken down by a CDN outage.

### Background Remover (`/tools/bg-remover`)

Transparent PNG cut-outs via remove.bg, built to be careful with a paid API:
the credit balance is shown before you spend, images are validated and
pre-resized locally so a credit is never wasted on a failure, and results are
cached by content hash so a retry is free. Resize before the cut to control
cost and after it to control the file you keep. Save straight to Cloudinary
against a client.

### UTM Builder (`/tools/utm`)

Tagged campaign URLs filed against the client **and the product** they belong
to, so a link is still explicable six months later. Products come from the
client's live Knack records.

Source, medium and campaign options live in **one editable list** — add,
rename, remove or reset per parameter. That consistency is the point:
`facebook`, `Facebook` and `fb` split one campaign into three in Analytics, so
every value is lowercased and hyphenated centrally. Pick several sources and
mediums to build every combination at once; existing non-UTM query parameters
on the landing page are preserved and existing UTM tags replaced. Saved links
are searchable and export to CSV.

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
- **Clients data**: Knack is the private source used by the Clients screen.
  Never commit client or billing exports. For outage fallback, mount a private
  directory containing `products.json` and `websites.json`, then set
  `CLIENTS_DATA_DIR` to that directory. Sanitized test-only examples live in
  `tests/fixtures/clients/`.
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

## Suite sub-account access (Forms, and anything else per-client)

The agency Private Integration Token (`GHL_PRIVATE_TOKEN`) can read
agency-level things — the sub-account list, snapshots — but **not** resources
that belong to a sub-account. Forms is the one that makes this obvious:
`/forms/` returns a scope error no matter which boxes you tick on the agency
token, because it is not the right *kind* of token.

HighLevel has no API that creates Private Integration Tokens, so the answer is
not "make one per client". It's a Marketplace app, installed once:

1. **Create the app** at `marketplace.gohighlevel.com` → My Apps → Create App.
   - Distribution: **Agency** (and sub-account, so it can be installed on both)
   - Redirect URL: `https://<your-hub>/suite/oauth/callback`
   - Scopes: tick **every** scope in [`hub/ghl_scopes.py`](hub/ghl_scopes.py)
     (`REQUESTED`). A location token inherits only what the agency token was
     granted, so a scope missed here is missed for every client until somebody
     re-consents — which is why the write scopes are asked for now rather than
     when a feature needs them. `NOT_REQUESTED` in the same file says what is
     left out on purpose, and why.
2. **Add the credentials** to Render: `GHL_CLIENT_ID`, `GHL_CLIENT_SECRET`.
3. **Install it on every sub-account.** Agency install with "install on all
   sub-accounts" also covers accounts created later.
4. **Connect once**: Suite → Status → *Sub-account access* → **Connect**, as the
   agency owner. That stores an agency refresh token on the persistent disk.

After that it is automatic. `location_token(location_id)` mints a 24-hour
sub-account token on demand and caches it, so a new client works with no setup
at all. `/status` shows the state under *Suite sub-account access*.

**Check the scope report before calling it done.** HighLevel grants the scopes
it recognises and says nothing about the rest, so a consent that granted half
the list still returns a healthy token and still reads *Connected*. The Suite
panel diffs granted against requested and names the features that are short —
and it separates a scope we have authenticated with before (grant it on the app
and reconnect) from one we have never confirmed (check the spelling in
`hub/ghl_scopes.py` first, because re-consenting for a typo wastes the one
manual step this app exists to stop repeating).

Until step 4 happens nothing changes: every call falls back to the Private
Integration Token exactly as before.

## Hub pages inside Smart 1 Suite

A HighLevel custom menu link is an iframe pointing at a URL. Point it at a Hub
page on the allowlist in [`hub/suite_embed.py`](hub/suite_embed.py) (`EMBEDDABLE` — today
Client 360 and the GET APIs it renders from) and the rep gets it inside Suite
without a second login.

Two limits, both deliberate:

* **It is read-only.** The login cookie is `SameSite=Lax` and browsers do not
  send it into a cross-site frame, so a companion cookie carries the session
  instead — accepted for GET and HEAD only, on allowlisted paths only. A write
  from inside the frame is refused exactly as an anonymous one is. Do not add a
  write-heavy tool to `EMBEDDABLE`: it would load, look complete, and fail on
  save.
* **Only allowlisted hosts may frame it.** `HUB_EMBED_FRAME_ANCESTORS`
  overrides the default (HighLevel's own hosts plus smart1marketing.com). Set
  it if the agency runs on a whitelabel domain. It is an allowlist and never a
  wildcard.

A client-facing page inside a client's own sub-account uses none of this — a
client has no Hub session — and needs HighLevel's SSO handshake. `SSO_NOT_BUILT`
in the same file says what that needs and why the location id it returns is the
whole security model.

## Env var compatibility notes

- `SECRET_KEY` signs the Hub login cookie **and** the Sites session. Set it
  once and keep it stable.
- `PANEL_PASSWORD` replaces the old control-panel password **and** the old
  Sites `ADMIN_USERNAME`/`ADMIN_PASSWORD` (no longer used).
- `FLASK_SECRET_KEY` is still used by the Google Finder's own session, as
  before. `TOKEN_ENCRYPTION_KEY` must never change once accounts are
  connected.
