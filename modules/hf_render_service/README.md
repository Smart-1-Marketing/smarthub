# hf-render-service

The render service `hub/hyperframes.py` talks to over `HF_RENDER_SERVICE_URL`.
Headless Chrome (Puppeteer, bundled Chromium) captures a p5.js template frame
by frame; ffmpeg encodes the sequence into an MP4. Two templates, matching
`hub/hyperframes.TEMPLATES` exactly:

- **paint-animation** — handwriting / paint-on / living-painting treatments
  of one line of copy or one photograph. `render_templates/paint.html`.
- **vox-explainer** — a 60–90s beat-sequence explainer built from a beat
  list already validated by `modules/commercial_builder/vox_spec.py`.
  `render_templates/vox.html`.

## Running in the Hub's own container (the default)

`docker-start.sh` starts this as a second background process alongside
gunicorn, bound to `127.0.0.1:8792`, and points `HF_RENDER_SERVICE_URL` at it
automatically once `dist/src/server.js` exists — nothing to configure on a
normal deploy. See the comment block in `docker-start.sh` for the exact
mechanism, which mirrors `modules/ad_builder`'s own second-process pattern.

This is reached only from the Hub's own Python process over loopback. There
is no browser-facing route here at all — no proxy, no token, no CORS. If you
can reach this service's HTTP port, you are already inside the container.

## Running as its own Render service instead

Puppeteer + headless Chrome + ffmpeg is heavier than the rest of this image,
and running it as a second process shares the Hub's own 2 CPUs during a
render. `render.yaml` is a documentation-only manifest (Render only reads
`render.yaml` at the repository root, exactly like `modules/ad_builder`'s own)
for the day this is worth splitting out onto a service of its own — set
`HF_RENDER_SERVICE_URL` to that service's public origin and nothing else in
the Hub changes.

## Wire contract

```
POST {base}/render/{template}      {...params}  -> 202 {"jobId", "status"}
GET  {base}/render/{jobId}/status               -> 200 {"status", "url"?,
                                                        "error"?, "durationSeconds"?, "progress"?}
GET  {base}/health                               -> 200 {"ok": true, ...}
GET  {base}/files/{jobId}.mp4                    -> the finished file
```

`status` is one of `queued | rendering | done | failed`. This is the exact
contract documented in `hub/hyperframes.py`'s own module docstring — that
file is the source of truth; this service's `src/templates.ts` is a defensive
second reading, not a second definition. `tests/templates.test.ts` pins the
two template names against the literal strings in `hub/hyperframes.py`.

## What this service deliberately does not do

**Fetch a photograph for a Vox beat.** A beat carries `image_query` — a
search hint a model wrote, not a resolved URL — and no stock-photo
integration lives in this Node service (Pexels/Pixabay/AI image generation
are all Python-side, in the Hub). The `collage` treatment renders a soft
abstract panel rather than a real photograph today. Wiring a resolved image
URL onto each beat is a real next step and a deliberate change to
`vox_spec._BEAT_KEYS` and `hyperframes.vox_params()`, not a silent
workaround here.

**Authenticate.** No API key, no admin token. `hub/hyperframes.py`'s own
docstring is explicit about why: this is self-hosted, and the loopback bind
is the whole of its access control.

**Scale past one process.** The job table (`src/jobs.ts`) is in-memory,
matching `modules/ad_builder`'s own worker, whose `render.yaml` documents
the identical constraint: "the job queue is currently in-memory, so a
separate worker process cannot see jobs enqueued by the web service." A
restart loses whatever was mid-render — the Hub's `status()` reads that as
"no record of that job," which is the documented 404 behaviour rather than
a special case.

## Local development

```bash
npm ci
npm run build   # tsc + copies render_templates/ and p5.min.js into dist/
npm start        # or: npm run dev (tsx watch, no build step)
npm test         # tsx --test tests/*.test.ts
```

The HTML pages this service renders live in `render_templates/`, not
`templates/` — deliberately: `hub/integrity.py`'s orphan-template check walks
every `modules/*/templates` directory looking for Jinja pages nothing
renders, and `paint.html`/`vox.html` are not Jinja. They are navigated to
directly by Puppeteer and reached by nothing Flask would recognise as a
render call, so a directory literally named `templates` reads as two orphans
to that check. Same reason `modules/ad_builder` keeps its own layout JSON
under `src/templates` rather than a bare `templates/` at the module root.

Requires `ffmpeg` on `PATH` and the system libraries Puppeteer's bundled
Chromium needs to launch (see the Dockerfile's `hf_render_service` apt
block — the same shared-library set the ad builder's own Chromium
dependency documentation lists).
