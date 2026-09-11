# hf-render-service

The render service `hub/hyperframes.py` talks to over `HF_RENDER_SERVICE_URL`.
Headless Chrome (Puppeteer, bundled Chromium — see "Which Chrome, and why
$HOME matters" below) captures a p5.js template frame by frame; ffmpeg
encodes the sequence into an MP4. Two templates, matching
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

Requires the system libraries Puppeteer's bundled Chromium needs to launch
(see the Dockerfile's `hf_render_service` apt block, and CI's own "Install
browser runtime libraries" step for the same list without pulling in the
whole `chromium` package) and `ffmpeg` on `PATH`.

## Which Chrome, and why $HOME matters

Puppeteer downloads its own Chrome and, by default, decides where to put it
— and later, where to look for it again to launch — from `$HOME`
(`~/.cache/puppeteer`). That is fine as long as whatever downloads it and
whatever launches it agree on what `$HOME` means, which a plain container
build does not guarantee: this Dockerfile's `npm ci` runs during the image
*build*, and `capture.ts`'s `puppeteer.launch()` runs once the image is
*running* as a Render service — two different points at which the platform
gets to decide `$HOME`, and nothing here had ever checked they agreed. They
did not: every production render failed at launch with "Could not find
Chrome (ver. ...)", invisibly, because nothing here calls `captureFrames()`
at boot to surface it sooner.

`PUPPETEER_CACHE_DIR=/opt/puppeteer-cache` in the Dockerfile is the fix — an
absolute path read at both the download and the launch, so the two cannot
drift apart over what `$HOME` resolves to. Locally, with nothing set,
Puppeteer falls back to `~/.cache/puppeteer` as it always did — the ordinary
`npm ci` experience, no environment variable required for a laptop checkout.

`capture.ts` also accepts `PUPPETEER_EXECUTABLE_PATH` as a named,
unset-by-default override — a way to point it at a different Chrome
binary (the apt `chromium` package already sitting in the image for its
shared libraries, say) without touching the code, should the bundled
download ever stop being the right answer here.
