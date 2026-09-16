# Smart 1 Hub

Internal tool suite for Smart 1 Marketing. Flask, deployed on Render via
Docker, ~22 modules mounted under one login.

**Live:** https://smart1.agency · **Repo:** `Smart-1-Marketing/smarthub`

---

## How this guide is organized

This file is loaded into every Claude Code session, so it holds only what
applies to every change. The long-form guide — every trap, data source,
module decision and incident write-up — lives in **`docs/claude/`**, one file
per topic, indexed in `docs/claude/README.md`. Nothing was removed when it was
split out; it was moved.

- **Before editing a module or feature, open the index and read the files that
  cover it.** They are not loaded automatically.
- Code comments and tests that say "CLAUDE.md names/records/says…" refer to
  that material. Look it up in `docs/claude/` rather than assuming it is gone.
- New long-form write-ups go in a new `docs/claude/NN-topic.md` file plus a
  line in the index, not in this file. Keep this file under ~200 lines.

## Client asset home — standing directive, September 15, 2026

Every client has one staff asset home at `/tools/image-picker/gallery/for-client?name=...`.
It always offers **Client Uploads**, **Creative**, and **Hub Projects**, even when
a section is empty. Client 360, the Creative index, and project links use this
home. Source/provider is provenance, not the main folder hierarchy: a Drive
campaign import is Creative, not a client upload just because it came from Drive.

`modules/image_picker/catalog.py` assembles gallery rows and existing tool
records on read. It includes images, audio, video, documents, and editable project
links. This supersedes the older image-only gallery advice in `docs/claude/`.
Rendered work keeps its approval status; listing a render does not approve or
publish it.
Add new client asset producers to this catalog or file through `file_asset()`.
Preserve full delivery URLs and permissions. Do not move old Cloudinary objects
just to reorganize the user's view, and do not create duplicate storage copies.

A named client with no upload record gets the same read-only home. Upload setup
is a separate POST; visiting the home cannot enable sharing. Client matching
must remain exact and refuse ambiguity. Search covers all loaded records,
including those older than 200 items. Failed source reads remain visible.
`test_master_gallery.py` verifies isolation, historical files, deduplication,
empty sections, and source failures. Unassigned Cloudinary objects still need
an evidenced client assignment; never guess ownership from a partial name.

## Architecture in one page

`wsgi.py` is the entry point. It builds the hub app and mounts every module
through `DispatcherMiddleware`, wrapped in `AuthGuard` (login) and `HubBar`
(injects the sidebar, help layer and breadcrumbs into every response).

Modules attach one of two ways, and the difference matters:

1. **Dispatcher-mounted** — a standalone Flask app under a URL prefix
   (`/scans`, `/tools/seo-images`). Has its own Jinja environment.
2. **Blueprint-registered** — registered on the hub app itself
   (`modules/tickets`, `calculators`, `image_picker`, `page_image_optimizer`,
   `google_access`).

Shared services live in `hub/`: `config.py` (typed settings), `storage.py`
(Cloudinary), `ai.py` (OpenAI + cost tracking), `images.py`, `audit.py`
(activity log), `extensions.py` (the shared SQLAlchemy instance),
`jsonstore.py` (JSON on the disk, mirrored to the database),
`scheduler.py` (background jobs).

## Traps to keep in mind on every change

The full list, with the history behind each one, is
`docs/claude/03-traps-every-one-of-these-has-cost-a-working-feature.md`.
The ones that bite most often:

- **A hub route under a mounted prefix is unreachable.** DispatcherMiddleware
  routes by URL prefix, so a hub route under `/sites` never runs. A module page
  calling a root-absolute URL works standalone and 404s once mounted.
- **Module Jinja environments are separate.** Hub globals are invisible inside
  a mounted module; write helpers as
  `{{ help_dot('x') if help_dot is defined else '' }}`.
- **Two gunicorn workers.** Anything with a timer or background thread runs
  twice unless it takes the leader lock in `hub/scheduler.py`.
- **A scheduled job has no request**, so anything cached on `flask.g` fails —
  and a broad `except` turns that into an empty result instead of an error.
- **Env var names drifted.** Read settings through `hub/config.py`, never
  `os.environ` directly.

**Opportunistic migration:** when you edit a module for any reason, move its
Cloudinary, image, JSON-persistence and settings code onto `hub/storage.py`,
`hub/images.py`, `hub/jsonstore.py` and `hub/config.py` while you are in there.
Details and the before/after patterns:
`docs/claude/14-opportunistic-migration-read-this-before-editing-any-module.md`.

## Conventions

- **No new Python dependencies** unless genuinely unavoidable.
- Module layout: `modules/<name>/app.py` (Flask app or blueprint),
  `templates/`, mounted in `wsgi.py` with a try/except and `_fallback_app()`.
- New tools get a tile on the index page that answers the question they answer
  — `hub/templates/creative.html`, `hub/templates/tools.html`, or the `extras`
  list in `qa_home()` — under the right group, and on **one** of them.
  A tool with no tile is invisible — six were, for weeks.
- Anything producing client work should call `audit.log(...)` with
  `client=` so it appears on that client's 360 record.
- Summary first, detail behind a click. Label absent data explicitly.
- Guard boot-time failures, but **record them** — a swallowed exception is how
  `/signup` 404'd for a day with no clue why.

## Verifying a change

Booting the app catches what static analysis misses. The core checks:

```bash
python tools/jscheck.py            # every .js file and inline block, via node
python tools/checktemplates.py     # the Jinja-carrying blocks jscheck skips
python tools/linkcheck.py          # every internal URL resolves, every url_for has a route
python tools/pagecheck.py          # the page the browser actually receives
python tools/integritycheck.py     # known defect patterns
python tools/spellcheck.py         # American English in everything a person reads
```

Then run the test scripts for the modules you touched. The full list, with
what each one guards, is `docs/claude/56-verifying-a-change.md`; a new test
file gets a line there and a step in `.github/workflows/checks.yml`
(`test_ci_gate.py` fails if a test file is not in the workflow).

**All of this runs on every pull request** in `.github/workflows/checks.yml`,
the single gate. Then boot through `wsgi.application` (not just the hub app —
that's how mount shadowing hides) and request the pages you touched.
`/api/integrity` reports known defect patterns; `/login/health` diagnoses
sign-in without a session.

## Every change ends with the Render environment list

Todd has asked for this every time, unprompted: finish by saying **what has to
be added to the Render environment** for the change to work. Not "check the
settings" — the names, and what each one costs if it stays unset.

Say **"nothing"** in as many words when nothing is needed. An omitted list and
an empty one read alike, and the omitted one is how a merged feature sits dead
in production with every screen reporting success.

The Hub already computes this: `Settings.status()` in `hub/config.py` carries
one row per provider with what unset costs, rendered on `/status`, and
`env_report()` on `/diagnostics` says which spelling answered and which were
set and ignored. Read those rather than writing a third description.
**Render's API exposes no way to read the variables that are set**, so anything
said from here about what is *already* on Render is inference from
`render.yaml` — say so rather than implying the live environment was checked.

## Delivery

Background cutouts use Cloudinary; background scene edits use the shared OpenAI
image-edit helper. Run `python test_background_providers.py` alongside
`python test_image_tools.py` for changes to these provider paths.

`git push` from the sandbox has always been blocked, so releases have gone out
as zips uploaded through GitHub's browser UI. **That uploader adds and
overwrites but never deletes**, which is why the repo root accumulated 65
stray files. If you can push directly, do — it removes the whole class of
problem.

## Standing authorization: open the PR, merge on green

Todd has asked that neither of these require asking each time. Both are
durable authorizations, not one-offs.

**Open the pull request.** When a branch carries finished work, open the PR
without asking first — do not stop at "say the word and I'll open one".

**Merge it once CI is green.** Once CI is green on a pull request against this
repo, **merge it without waiting for a fresh confirmation.** Render
auto-deploys `main` on every merge (`autoDeployTrigger: commit`, per the note
at the end of `docs/claude/05-data-sources-and-which-are-stale.md`), so a merge
here is also a deploy; that is expected and does not need a separate go-ahead.

This covers the merge action alone. It does not cover, and none of these are
authorized in advance: force-pushing, rewriting history on a branch you did
not create, merging with CI red or unresolved review comments, or a change
outside what was actually asked for. Where CI is red or a PR is otherwise not
mergeable, the drive-to-green rules elsewhere in this file's operating
instructions apply — fix it or say what is blocking, never merge around it.
