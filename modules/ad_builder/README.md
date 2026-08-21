# Smart 1 Ad Builder

## Recent capability additions

**Proof-page live editor.** The proof is now interactive: edit headline,
supporting line, button text, button/text/headline colours, and clean up the
logo, then "Apply changes & rebuild" re-renders every size in place and reloads
onto the fresh ads. A default-copy edit also clears any stale per-size copy
overrides, so all sizes pick up the change (fixing the case where 728x90 and
970x250 kept old copy).

**Logo tools.** Cleaning a logo removes a flat background box, trims dead
space, and (optionally) makes a reversed white version — always preserving the
exact mark, never redrawing it. Transparency is enforced on every logo.

**Imagery.** Pixabay search and AI generation, both guaranteed under 150 KB.
The offer concept can use a full-bleed photo background chosen on the review
step (solid / suggested photos / AI-generated), painted under a legibility
overlay with forced light text; contrast QA validates the real rendered text.

**Sizes & safe zones.** Added 250x250, 160x600 everywhere, and the Meta set
(1080x1080, 1200x628, 1080x1350, 1080x1920) with centred logo/CTA. The 9:16
formats respect Meta's top-14% / bottom-35% platform-UI exclusion zones, with a
QA check that flags violations.

**150 KB enforcement** funnels every image — upload, Pixabay, AI — through one
choke point that compresses to fit rather than rejecting.

---

# smart1-ad-builder — rendering core

This is the piece the rest of the system hangs off: the deterministic renderer
that turns an approved creative concept into a compliant ad package.

The architectural bet from the plan holds here. **OpenAI creates the creative
intelligence; this renderer creates the finished ads.** No image model is ever
asked to draw a headline, a logo or a CTA button.

```
Template JSON  (where things sit)
Brand JSON     (who the advertiser is)     ─┐
Creative JSON  (what the ad says)           ├─→ SVG → Sharp → PNG/JPG → QA
Cloudinary assets                          ─┘
```

## Onboarding a client

Icon Solar is a worked example, not the product. Nothing in the renderer,
templates, platform configs or Cloudinary layer is specific to it — every
client is just another campaign JSON. `campaigns/bella-vista-catering.json` is
a second, deliberately unrelated brand (different palette, different typeface,
different vertical) that renders clean through the same templates.

```bash
npx tsx scripts/scaffold.ts \
  --client "Bella Vista Catering" --domain bellavista.com \
  --campaign "Fall Catering" \
  --primary '#7A2E1F' --secondary '#C4713C' --accent '#F2C14E' --dark '#2A1410' \
  --headline-font Poppins --body-font "Open Sans"
```

That writes `campaigns/<client>.json` plus brand-coloured placeholder assets in
`assets/<client>/`, then validates the result. Edit the copy and render:

```bash
npx tsx src/cli.ts --campaign campaigns/bella-vista-catering.json --platform google
```

### Fonts are the one hard constraint

The renderer converts glyphs to paths from real font files, so it can only use
families in the registry in `src/fonts.ts` — currently Montserrat, Open Sans,
Poppins and DejaVu Sans. **A brand font that is not registered is a validation
error, not a silent fallback.** That is deliberate: a proof rendered in the
wrong typeface looks finished, so nobody checks it.

To add a client's font, vendor the licensed files into the repo and add an
entry to `REGISTRY`. Do not accept customer-uploaded font files.

### Validation runs before every render

`src/validate.ts` checks the brand palette is complete and valid hex, both
fonts resolve, logo and hero files exist on disk, every concept names a real
template, and each size that needs a headline has one. Errors stop the run;
`--skip-validation` overrides. `--verbose` also prints per-platform size
coverage.

## Running it

```bash
npm install
npm run assets            # placeholder logo + three hero orientations
npm run render:google     # all concepts, Google package
npm run render:amazon     # all concepts, Amazon package (2x delivery)
npm run gallery -- --help # see the Cloudinary section below

npx tsx src/cli.ts --platform amazon --concept A --size 970x250 --svg
```

Output lands in `out/<platform>/<conceptId>/` alongside `qa-<platform>.json`.
The process exits non-zero if any creative fails QA, so it drops straight into
a Render background worker without extra wiring.

Current state, Icon Solar sample campaign:

| Platform | Sizes | Result |
|---|---|---|
| Google | 300x250, 336x280, 728x90, 160x600, 300x600, 320x50, 970x250 | 7/7 clean |
| Amazon | the above plus 414x125, with 2x delivery on 320x50, 970x250, 414x125 | 8/8 clean |

Bella Vista Catering (T01 + T04, Poppins, warm palette) renders 11/11 clean on
Google and 12/12 on Amazon from the same templates.

## Why text is converted to paths

`src/fonts.ts` loads real font files with opentype.js and emits every glyph as
an SVG `<path>`. Nothing depends on fontconfig having Montserrat installed on
the host, so a laptop and a Render dyno produce identical bytes, and librsvg's
text-layout quirks are removed from the equation. It also means autofit can
measure a line exactly rather than guessing.

## The three files you will actually edit

**`src/config/platforms/*.json`** — file weights, formats, delivery scale, copy
budgets, minimum font sizes. When Google or Amazon changes a requirement this
is a one-file change, not a deploy of application code.

Amazon entries carry a `source` field. `"doc"` means the value came from the
background research; **`"verify"` means it was inferred and must be confirmed
against Amazon's current spec sheet before first live delivery.** Right now
`300x250` and `336x280` on Amazon are marked `verify`.

**`src/templates/*.json`** — layout families. `T01` (split image) covers all
eight sizes; `T04` (offer led) covers four and deliberately skips the rest, to
demonstrate that the renderer reports gaps rather than inventing a layout.
`T02, T03, T05–T10` from the plan are still to be authored.

To add a size to a family, add a key under `sizes` with a `canvas`, a `safe`
margin, and boxes for the roles you want. Text boxes declare a `[min, max]`
size range and `maxLines`; the fitter walks the range down until the copy fits
and flags overflow when it never does. Coordinates are authored in 1x space and
carried in a viewBox, which is what lets the same document deliver at 2x for
Amazon.

**`schemas/creative-plan.schema.json`** — the Structured Outputs contract for
the OpenAI creative director. The model picks a `layout_family` and writes copy
per size. It never emits coordinates.

## Deploying to Render

| Setting | Value |
|---|---|
| Build command | `npm ci --include=dev && npm run build` |
| Start command | `npm start` |
| Health check path | `/healthz` |
| Node version | 22 |

`render.yaml` in the repo root is a blueprint with the same settings plus the
env vars and disk.

Three things that will bite you if changed:

**`--include=dev` is required.** Render sets `NODE_ENV=production`, which makes
`npm ci` skip devDependencies — and `typescript` is a devDependency, so the
build fails with "tsc: not found" without it.

**The build is two steps, not just `tsc`.** `tsc` compiles `.ts` and ignores
everything else, so the template and platform JSON that the registry reads at
runtime never reaches `dist/`. `npm run build` runs `scripts/copy-assets.mjs`
afterwards to copy them. Running bare `tsc` produces a build that crashes on
first request.

**Attach the disk.** Creatives are written to `OUTPUT_DIR` before upload. With
no disk, they vanish on every deploy and `/files/` 404s for anything rendered
before the last restart.

### Endpoints

```
GET  /healthz                  liveness; cheap, touches no external service
POST /api/render               { campaign, platforms[], upload } -> 202 + jobId
GET  /api/render/:id           job status, results, report paths
GET  /files/<path>             rendered creatives and reports
```

`POST /api/render` validates synchronously and returns 422 with findings if the
campaign is bad, so a missing brand font is an immediate error rather than a job
that fails in the background. Valid jobs return 202 and are rendered off the
request path.

### The worker is not deployable yet

`render.yaml` has the Background Worker commented out. The queue is in-memory,
so a separate worker process would have its own queue and never see jobs from
the web service. Leave `WORKER_MODE` unset and the queue runs in-process. Move
the queue to Render Key Value before splitting them — and note that jobs
currently do not survive a restart, which on Render means a deploy drops
anything in flight.

## Cloudinary, the image report, and the gallery

Copy `.env.example` to `.env` and fill in the three Cloudinary variables.

```bash
npm run render:google:upload      # render, upload, write reports
npx tsx src/cli.ts --platform amazon --dry-run   # preview without uploading

npx tsx src/gallery.ts --find icon                 # list matching projects
npx tsx src/gallery.ts --folder smart1-ads/icon-solar/summer-solar
npx tsx src/gallery.ts --client "Icon Solar" --campaign "Summer Solar"
```

### One folder per project

The folder is derived from the client and campaign name and created on
submission, so the project is visible in the Media Library before any render
runs:

```
smart1-ads/icon-solar/summer-solar/
    source/                          customer uploads, Brandfetch assets
    generated/                       AI-generated hero images
    proofs/concept-a/                proof-screen previews
    final/google/concept-a/          approved deliverables
    final/amazon/concept-a/
```

Every upload is tagged (`client:`, `campaign:`, `request:`, `concept:`,
`platform:`, `size:`, `qa:`) and carries context metadata, so assets can be
found by tag even if someone later moves the folder. The manifest stores the
`public_id`, never the delivery URL — the URL is a function of the
transformation and will change.

**Creatives that fail QA are not uploaded.** They still appear in the report,
flagged, so nothing disappears silently. `--upload-all` overrides this.

**Folder mode matters.** Cloudinary accounts are either fixed-folder (folder is
a prefix of the public_id, searched with `folder:`) or dynamic-folder
(`asset_folder` is independent, searched with `asset_folder:`). Set
`CLOUDINARY_FOLDER_MODE` to match, or folder search returns nothing.

### The image report

Three files land in `out/reports/` after every run:

| File | Purpose |
|---|---|
| `image-report_<requestId>.html` | The list of every image — size, delivered dimensions, format, weight, word count, QA result, Cloudinary public ID. Grouped by platform and concept. |
| `image-report_<requestId>.csv` | Same data flat, for spreadsheets and account-manager handoff. |
| `manifest_<requestId>.json` | Machine-readable record. This is what the HighLevel custom object should reference. |

### The gallery

`src/gallery.ts` searches the Cloudinary folder and builds a gallery page from
what it finds, rather than from local state — so it shows what is actually in
the library, including anything added by hand. Creatives are displayed at
actual pixel size, capped at 500px wide so a 970x250 does not blow out the
layout.

Passing `--manifest out/reports/manifest_<id>.json` makes the command work
before Cloudinary is configured: it falls back to the last render and points at
the local files. That is how the sample gallery in `out/reports/` was produced.



`src/qa.ts` runs before any proof reaches a customer, and every finding is
machine-readable so the copy-shortening step can act without a human:

```json
{ "check": "overflow:headline", "status": "fail",
  "fix": { "action": "shorten", "role": "headline", "maxWords": 4 } }
```

Checks: delivered dimensions, file weight, per-role text overflow, safe area
(including Amazon's asymmetric 640x250 safe zone inside 828x250), logo presence
and canvas share, baked-CTA presence or required absence, word count against
the per-size budget, minimum font size at delivery scale, and contrast.

The contrast check is worth calling out. The renderer composes a second,
text-free pass of the same layout and samples the actual pixels under each text
block. That catches white copy drifting over a bright patch of a photograph —
something a check against the template's nominal background colour would miss.

One trap if you extend it: sharp's `.stats()` reports on the *input* image and
ignores earlier pipeline operations, so `sharp(png).extract(region).stats()`
silently returns whole-canvas statistics. The crop has to be materialised to a
buffer first. This produced phantom contrast warnings until it was caught by
onboarding a second brand whose page average differed from its copy area.

## The intake flow, in order

1. **Business name and website.** The website field is where brand discovery
   starts — it fires on blur and calls `POST /api/brand/discover`.
2. **Confirm or correct the brand.** If Brandfetch found something, the customer
   confirms it. If it found nothing, or they say "this is not my brand", the
   form opens **manual entry**: business name, three colours, headline font.
   Discovery failing is the normal case for a small business, so it returns 200
   with `brand: null` rather than an error.
3. **Project name.** Required, and the thing people search on later. The hint
   asks for a season or year because clients re-run the same campaign.
4. **Landing page.** On blur, `POST /api/landing/analyze` reads the page and
   returns suggested headlines and CTAs as tappable chips.
5. **Uploads** go straight to Cloudinary via a server-signed request.

## Landing page analysis

The landing page is the customer's own approved wording, so reading it beats
asking them to summarise it again in a form field. `src/landing.ts` strips
scripts, nav and footer (a footer full of location links otherwise skews every
suggestion toward geography), then takes one of two paths:

- **OpenAI** when `OPENAI_API_KEY` is set, using Structured Outputs so the
  application receives fields rather than prose. The prompt forbids inventing
  an offer, price or credential the page does not state.
- **A heuristic reader** otherwise — weaker, and it says so in `source` and in
  a warning, but a missing key degrades the suggestions rather than breaking
  intake.

A 429, a timeout, or a page that renders entirely client-side all fall back to
the heuristic with the reason attached. Verified against a stub; **never called
against the live OpenAI API**, since this sandbox has no route to it.

Analysis is cached on the project, and runs in the background on submission so
the customer is not left waiting on a third-party fetch.

## Projects: dated and searchable

Every submission creates a `Project` in `out/projects/`, keyed
`<client>_<project-name>_<date>` so re-running "Spring Promotion" next year
does not overwrite this year's. Each record carries:

- created and updated timestamps, and a dated `RenderBatch` per render
- the Cloudinary folder, plus `AssetLink`s recording what each asset is and
  where it came from (`upload`, `brandfetch`, `generated`, `placeholder`)
- the landing page URL and its cached analysis
- the brand, flagged when it was entered by hand
- keywords lifted from the brief, which is what makes free-text search useful

`GET /api/projects?q=&client=&status=&from=&to=` searches across project name,
client, campaign, domain, landing page and keywords — every term must match, so
narrowing a search narrows the results. Newest first.

Storage is a JSON file per project plus an index: correct for one instance,
wrong the moment there are two. The read/write surface is narrow so it can move
to Postgres without touching callers.

## Brand discovery and asset uploads

`POST /api/brand/discover` takes a domain and returns a `Brand` mapped from
Brandfetch, plus logo choices, colour swatches and warnings. The embed form
calls it when the website field loses focus and shows a confirmation card.

Two mismatches are handled explicitly rather than papered over:

**Fonts.** Brandfetch returns font *names*; the renderer can only use families
whose files are in the registry. An unregistered family maps to the nearest
registered one **and produces a visible warning** — the customer is told their
ads will not be in their brand face until the font is vendored in. Never let
this one pass silently.

**Colours.** Templates need five roles, and `light`/`dark` must contrast or QA
fails on every creative. Brandfetch sometimes reports a near-white as "dark";
when the pair falls below 7:1 the code substitutes white and near-black and
says so.

Discovery failing is not an error. Small businesses often have no public brand
record, so the endpoint returns 200 with `brand: null` and the form falls back
to "upload your logo".

Set `BRANDFETCH_API_KEY`, or `BRANDFETCH_MOCK=1` to work from
`src/examples/brandfetch-sample.json` without a key. The fixture is
deliberately imperfect — unregistered fonts, a bad colour pair — so the
warning paths get exercised rather than a happy path that proves nothing.

### Uploads

`POST /api/assets/upload-signature` returns Cloudinary signed upload params.
The browser then posts the file **directly to Cloudinary**, so nothing large
passes through the Render service. Signed rather than unsigned, so the folder,
tags and allowed formats are fixed server-side and cannot be rewritten by the
page.

Uploads land in `<project>/source/brand` or `<project>/source/<kind>`.

`src/assets.ts` also handles the other direction: `resolveAsset()` turns a
local path, an https URL, or `cloudinary:<publicId>` into a local file the
renderer can read, with caching. `prepareLogo()` rasterises SVG logos, which
sharp cannot composite directly. `validateAsset()` rejects unreadable files and
anything under 200px before it reaches a creative.

## Security

Internal routes — the build screen, diagnostics, campaign read/write, project
search, render queue and `/files/` — require `ADMIN_TOKEN`. It is a shared
secret, which is the right weight for a handful of staff; it is not a user
system, so there are no accounts, roles or audit trail. Set it to something
long and random:

```
ADMIN_TOKEN=$(openssl rand -hex 32)
```

**Without it, those routes are closed to everyone, including you.** That is
deliberate — the alternative is a build screen anyone can read by guessing the
URL.

A token can arrive as a Bearer header, an `X-Admin-Token` header, a `?token=`
query parameter, or the `s1_admin` cookie. The query form exists so a bookmark
works; the server then sets a cookie so the token stops appearing in the
address bar and referrer headers. Comparison is timing-safe.

Public routes stay open because an embedded form on a customer's site calls
them, so they get rate limiting instead. `POST /api/assets/upload-signature`
has the tightest budget: each signature is permission to write into your
Cloudinary account, so an open one is somebody else's free file storage on your
bill.

## Diagnostics

`/diagnostics` runs every check live and reports what is actually working
right now. It exists because every failure in this system has been silent from
the outside: a missing font fell back and rendered the wrong typeface, a stale
build served a 404, a CSP default showed a blank iframe.

It checks the runtime and memory headroom, that each registered font renders
glyphs, that no template has overlapping or out-of-bounds boxes, platform
limits still marked unverified, the admin token and embed origins, disk space
and how much output is being retained, every integration credential, a live
outbound call to each third party, and finally a full end-to-end render.

Every failure states the fix inline. `?format=json` returns the same report and
answers **503 when broken**, so an uptime monitor can watch it directly.

One detail worth keeping: a 403 from an integration is reported as *unproven*
rather than reachable, because a proxy or firewall answering on the service's
behalf looks identical to success otherwise.

## Tests

```bash
npm test
```

29 tests, run against the real renderer. They exist because of bugs that
actually shipped — widening the CTA buttons pushed a headline underneath one,
and repositioning a trust line broke a passing campaign. Both were invisible to
QA, which checks each box against its own rectangle and cannot see two elements
sharing space.

The suite covers layout geometry (overlap, safe area, canvas dimensions), that
the longest CTA the form offers fits every button, typesetting, font health,
contrast maths, offer-token extraction, brand mapping, landing extraction,
Cloudinary signatures, auth and rate limiting, and a full clean render of both
sample campaigns on both platforms.

It has already earned its place: adding the three new template families, it
immediately caught five safe-area breaches and a CTA that could not hold
"Schedule Now".

## The four screens

| Screen | Route | Who opens it |
|---|---|---|
| Intake form | `/embed` | The customer, embedded on the marketing site |
| **Build screen** | `/build` | Smart 1 staff, to edit and re-render |
| Client proof | `out/reports/proof_<id>.html` | The customer, to approve or request changes |
| Image report + gallery | `out/reports/` | Internal record and asset library view |

## The build screen

`/build` is the operator's workbench and the only screen where creative is
changed. It opens any campaign that intake has built, and every edit re-renders
through the real pipeline — the preview is the actual renderer output at
delivery scale, not a CSS approximation, so what you approve is what ships.

A dark studio rather than the proof screen's light gallery, because an operator
spends an hour here judging brand colour and a bright surround skews that all
day. The canvas sits on a neutral mid-grey that does not tint the creative, with
a **Dark page / Light page** toggle for checking how a creative holds up on
either kind of publisher, and the same **Squint** test as the proof screen.

Three things worth knowing about how it behaves:

**Copy edits are per size.** The model is a default copy set plus per-size
overrides. The editor shows the effective value for the size on screen and
writes to that size's override, so shortening the 320x50 headline cannot
silently rewrite the 300x600. Clearing a field falls back to the default.

**QA fixes are one click.** Findings carry a machine-readable
`{ action: 'shorten', role, maxWords }`, so the checks panel offers a
"Shorten headline to 2 words" button rather than making the operator work out
what "does not fit" means in words. This is the same instruction the AI
copywriter will consume when that step lands.

**Switching layout family re-renders every size**, and the rail shows which
sizes that family actually covers — swapping to T04 drops from eight sizes to
four, visibly.

`Render all sizes` saves the campaign and queues the full package through the
same job runner as the CLI.

## The client proof screen

`src/proof.ts` generates `out/reports/proof_<requestId>.html` on every run.
It is the page a customer opens to approve work, so it is built around one
fact about the subject: display ads are seen small, inside someone else's
page, for about a second.

Two controls follow from that, and both are review techniques rather than
decoration:

**Actual size, always.** Creatives render at true pixel dimensions and are
never scaled up. On a phone an oversized unit scrolls inside its own frame
rather than shrinking — scaling it down would misrepresent what the client is
approving.

**Squint test.** Blurs every creative until only the focal hierarchy survives.
This is the manual version of the guidance that an ad needs one point of focus
and must read at a glance; if the message still comes through blurred, it will
survive a second of attention.

**See it in a page** drops the 300x250 and 728x90 into a mock article layout,
because an ad on a white void flatters itself.

The interface palette is a deliberately neutral proofing grey. Brand colours
have to read true, so the surrounding chrome refuses to compete.

## Design rules the QA now enforces

Drawn from Amazon's creative guide and WordStream's teardown of display ads
that worked:

- **Hierarchy.** Amazon is explicit that size variation is how an ad signals
  importance without instructions. QA warns when the headline is less than
  1.4x the supporting line. This immediately caught my own 728x90, where the
  ratio had drifted to 1.25x.
- **One focal point.** Both sources say the same thing from opposite ends —
  "stick to one point of focus" and "minimal clutter". QA warns when offer,
  trust and support all compete below the headline on a canvas too small to
  carry them.
- **Match creative to the size, don't squeeze.** Already the architecture:
  every size has its own layout and its own copy budget.

## No logo is no longer a dead end

A missing logo used to flag the whole request un-renderable and route it to a
"our team will follow up" message. Most small businesses have no logo file to
hand, and discovery does not always return a usable one, so this stranded
real submissions.

Now, when no image logo resolves — upload failed, discovery returned nothing
usable, or nothing was provided — the business name is set as a clean
typographic **wordmark** (`src/wordmark.ts`) in the brand's headline font on a
transparent PNG, and the build continues normally. "Northside Dental" set in
Poppins reads as a brand lockup; every layout renders; the customer gets ads
instead of a hand-off. Uploading a real logo later replaces it. The
confirmation screen no longer has any "we'll be in touch" terminal state — it
always proceeds to previews.

## Why a real submission could vanish as `AD-2026-000000`

The intake handler has an anti-spam honeypot: a hidden field a human never
sees. When it is filled, the server returns a fake-success id
(`AD-<year>-000000`) and renders nothing — the correct response to a bot, but
the exact symptom of a confirmation screen whose skeleton spins forever.

The trap was too aggressive. Browser autofill and password managers fill
hidden fields for genuine humans, ignoring `autocomplete="off"` — so a real
person could trip it. It now takes **two** agreeing signals to reject:

- the hidden text field is filled, **and**
- the form was submitted implausibly fast (under ~1.2s), which autofill+human
  typing never is.

A filled honeypot with human timing is treated as autofill and allowed
through. Every rejection is now logged (`[intake] REJECTED as bot: …`) instead
of vanishing silently, and the confirmation screen tells a rejected user to
email us rather than spinning.

## Knowing what is deployed

The build writes a timestamp that the running server reports three ways:
`GET /version`, the `builtAt` field in `/healthz`, and the boot log
(`build: 2026-…`). This ends the "is the new code actually live?" question for
good — compare the timestamp to when you deployed. Intake and render steps now
log verbosely (`[intake] … accepted`, `[firstlook] … written in 455ms`,
`[intake] … queued batch job …`), so a stuck submission is a visible trail in
the Render logs rather than silence.

## Nothing hangs silently

A render that stalls used to look identical to a render that was merely slow:
the customer watched the skeleton forever and nobody was told anything. Four
defenses now close that class of failure:

1. **The first look renders before the batch is queued**, not alongside it.
   On a small instance both renders previously competed for the same starved
   CPU, so the one image the customer was watching for arrived last, if ever.
2. **A watchdog** fails any job still running after `RENDER_WATCHDOG_MIN`
   minutes (default 10), notifies staff with the reason, and flips the public
   status to `failed` — so the customer sees an honest message instead of an
   eternal skeleton.
3. **Memory guards**: libvips defaults its thread pool to the *host's* core
   count (8 on Render's machines) regardless of the container's real CPU
   share. `sharp.concurrency` now defaults to 2 (`SHARP_CONCURRENCY` to
   raise it) and the buffer cache is off — renders are written once and never
   re-read.
4. **The diagnostics page lists recent render jobs** — status, sizes done,
   age, and the error when there is one — so "it seems stuck" becomes a row
   in a table instead of a guess. If the batch finishes but the quick first
   look was lost to a restart, the status endpoint falls back to serving the
   batch's own 300x250.

## The wait is part of the product

Nobody leaves the page to find out what happened. After submitting:

1. An **animated skeleton** of a 300x250 draws itself while the first render
   runs — the shimmer says "working", the shape says "your ad".
2. Within seconds it is replaced by a **real first concept**: one 300x250 of
   concept A, rendered immediately and outside the job queue, so there is
   something concrete to react to while the full set builds. A live counter
   ("4 of 22 done") ticks underneath, fed by real job progress.
3. When every size is finished, **Review all your ads** appears, opening the
   full proof page.

There is no give-up timer and no "we'll email you" hand-off. The only
terminal states are honest ones, reported by the server rather than guessed
from a timeout: a render that actually failed (staff are auto-notified), or a
submission that cannot render until a logo arrives.

## Internal-only mode

Set `INTAKE_CODE` and the request form gates itself behind a team access
code — one shared code your staff types once per session. Every intake API
call (submit, brand discovery, landing analysis, upload signing) requires it,
checked timing-safe and before rate limiting. It is deliberately not the
admin token: the code circulates around the office, so compromising it must
cost only the ability to submit requests, never the build screen, projects,
or delivered files. Leave it unset and the form is open, as before.

Request IDs are random (`AD-2026-7FBRYSPA`), not timestamp-derived. The id
doubles as the proof-link capability, so it must not be enumerable — and as a
side effect, an id like `AD-2026-000000` is now impossible by construction,
which makes stale-deploy confusion self-evident.

## Final delivery

`POST /api/project/:id/deliver` — or the **Deliver final files** button that
appears in the build screen once a project is approved — packages the approved
concept into one client-ready zip:

    <client>-<campaign>/
      google/<client>_300x250.jpg     files named for the platform-ops person
      google/<client>_728x90.jpg      who uploads them, not for internal ids
      ...
      README.txt                      per-file specs, weights, and platform

Failing creatives never ship; they are listed in the README under "not
included", because silently delivering a broken ad is worse than delivering one
size short. Delivery flips the project to `complete`, records the zip on the
project, and notifies with the download link.

The zip is written by a small STORE-method writer (no dependency, no zip
binary needed on the host; the payload is already-compressed JPG/PNG so
compression would buy nothing). Verified against both `unzip -t` and Python's
`zipfile`.

## Manual overrides

The last 5% of real agency work: "the 300x600 needs the photo nudged — I'll
fix it in Photoshop." That file now has somewhere to go. **Replace with edited
file…** on the build screen (or `POST /api/project/:id/override`) uploads a
finished creative against the size on screen.

The upload is validated against the same platform rules as rendered output —
exact delivered pixel dimensions (including 2x sizes), the platform's
file-weight ceiling, and its allowed formats — so an override cannot smuggle
in a wrong-sized or overweight creative. An amber note under the preview shows
when the current size ships an override, with one-click removal. Overrides
win at delivery time and the README flags them as manually edited.

## Health you can see

Every staff screen now shares one top nav (Build · Projects · Diagnostics) and
a **health dot** that quietly polls diagnostics every five minutes: green
healthy, amber degraded, pulsing red broken; click through for the full page.

The server also watches itself: every `HEALTH_CHECK_HOURS` (default 3) it runs
the full diagnostic suite and sends **one** notification when the verdict
transitions to broken, and one when it recovers. Transition-only on purpose —
a repeating alarm trains people to ignore the channel.

## Wired together

Everything below used to be a standalone piece; this is how they now connect.

**Copy generation is live in intake.** `buildCampaign` calls `generateCopy`
directly — the placeholder word-truncation is now only the fallback for a
missing key or a failed call, not the default path. Because word-budget
compliance doesn't guarantee pixel width ("Check Availability" fits every
budget and overflows several buttons), AI-written CTAs still pass through the
same `shortCta` clamp the deterministic path always used.

**Submission to proof is automatic.** A renderable request is queued the
moment it arrives; nobody has to notice it first. The submission response
returns immediately — rendering happens after, via the existing job queue —
and a notification fires once the proof is ready, or if the render failed, or
if the submission wasn't renderable at all (usually a missing logo), so staff
know a customer is waiting either way.

**Approvals and revisions notify, and revisions surface.** Both proof-screen
buttons now trigger a notification. `GET /api/campaigns` sorts anything with
an open revision to the top, and the build screen's campaign picker marks it
"⚠ NEEDS REVISION" so it can't be scrolled past.

## Notifications

`src/notify.ts` follows the same pattern as every other integration here: a
real transport when configured, an honest fallback otherwise. Two transports,
because small teams split roughly in half between email and Slack:

- **Resend**, via `RESEND_API_KEY` + `EMAIL_TO` — a plain REST call, no SDK.
- **A webhook**, via `NOTIFY_WEBHOOK_URL` — Slack-compatible payload, works
  with any incoming webhook.

Both can be set at once. If neither is set, nothing is lost: the message is
appended to `out/notifications/outbox.jsonl` instead, and diagnostics flags
the missing configuration.

## Job durability

The queue is still in-memory and still single-instance — that has not
changed, and still needs Redis or Postgres before running two web processes.
What changed is narrower: **a job's state now mirrors to disk as it changes**,
so a restart mid-render (a Render deploy is exactly this) no longer drops it
silently. On boot, `recoverJobs()` finds anything left `queued` or `running`
from before the restart and requeues it from scratch — there is no partial
resume, a recovered job re-renders everything, which is simple and safe rather
than clever.

Tested by killing the process with `SIGKILL` mid-render and confirming the job
came back and completed after restart.

## The projects screen

`/projects` — search and filter past work, click through to reopen it in the
build screen. Same dark palette as the build screen on purpose: this is staff
tooling opened in the same session, and a palette switch between the two would
read as leaving the app rather than moving within it.

## Platform limits, now fully confirmed

The two "unverified" Amazon limits turned out to need more than confirming a
number:

- **336x280 is not an Amazon DSP placement.** It is absent from Amazon's own
  spec page entirely — a Google-only size that had been carried into the
  Amazon config by mistake. Removed; Amazon renders now produce 7 sizes for a
  full campaign rather than 8, and none of them is a size Amazon has nowhere
  to run.
- **414x125 was wrong, not just unconfirmed.** Configured at 50KB; Amazon's
  page states 100KB. The error was in the safe direction — the app would have
  forced smaller files than necessary, not shipped oversized ones — but it was
  still incorrect data.

Every remaining Amazon and Google limit was checked directly against
[advertising.amazon.com/resources/ad-specs/dsp/desktop](https://advertising.amazon.com/resources/ad-specs/dsp/desktop)
and matched exactly. Diagnostics no longer shows any unverified platform
limit.

## Copy generation

`src/copywriter.ts` replaces the deterministic word-trimming that stood in for
copywriting. It writes per size rather than shrinking one headline, because a
320x50 carries about five words and a 300x600 carries twenty-four.

The model is constrained hard: it may never state a price, rating, guarantee or
credential that is not in the brief or the landing page, headlines start with
an active verb where natural, and pressure language is stripped on the way out
because Amazon rejects it. Everything returned is then measured, wrapped and QA
checked exactly like hand-written copy.

Without `OPENAI_API_KEY` it falls back to the form-derived copy and says so.
Verified against a stub, including trimming an over-length headline and
removing a banned word; **never called against the live API** from here.

## Retention

Rendered files are written locally before upload and nothing pruned them, which
on a shared box means this service slowly eats the volume its neighbours live
on. `src/retention.ts` sweeps daily: renders older than
`RENDER_RETENTION_DAYS` (30) and cached downloads older than
`CACHE_RETENTION_DAYS` (7). Projects, campaigns, requests and reports are never
touched — they are small and they are the audit trail.

`POST /api/maintenance/sweep` previews what would go; add `?dry=0` to act.

## What this does not do yet

- **The form does not trigger a render.** `POST /api/requests` writes the
  submission to disk; nothing consumes it. Bridging intake to a campaign JSON
  is the next piece.
- OpenAI **image** generation (copy generation is built; imagery is still placeholders)
- Cloudinary smart cropping (upload, folders and search are done)
- the proof screen, natural-language revisions, approval flow
- HighLevel custom object sync and status workflows
- the auto-shorten loop (QA emits the instructions; nothing consumes them yet)

The Cloudinary upload path has been written and typechecked but **not executed
against a live account** — this environment has no network route to
`api.cloudinary.com`. Run `--dry-run` first, then a single `--size 300x250`
upload, before turning it loose on a full package.

The renderer is the dependency for all of it, which is why it is first.

## Note on the sample assets

`npm run assets` writes obvious placeholder art, watermarked "NOT FOR CLIENT
USE". It exists so the pipeline runs offline. Replace with Cloudinary-hosted
customer uploads or OpenAI-generated backgrounds.
