## hf-render-service exists now, as a second process in this same container

The previous section's whole premise was that Puppeteer, headless Chrome and
FFmpeg "do not belong in this Flask image" and would run as their own Render
service. That premise held for exactly as long as nobody had built the
service — Paint Animation and Vox Explainer worked in the browser and
produced nothing, because `HF_RENDER_SERVICE_URL` pointed at nothing. It is
built now, at `modules/hf_render_service/`, and it runs the way
`modules/ad_builder` already does: a second background process in the Hub's
own container, supervised by `docker-start.sh`, on loopback, never a second
Render service. That is a deliberate departure from the previous plan rather
than an oversight — standing up and paying for a whole second service is a
bigger ask than the CPU headroom this deployment now has, and the escape
hatch is unchanged: `modules/hf_render_service/render.yaml` documents the
split for the day a render makes the Hub itself feel slow, and nothing
about the module's own code has to change, only where
`HF_RENDER_SERVICE_URL` points.

**The wire contract is unchanged, on purpose.** `hub/hyperframes.py` was
already written and tested against `POST {base}/render/{template}`,
`GET {base}/render/{jobId}/status`, `GET {base}/health` — the service
implements exactly that, so nothing on the Hub side moved.

**Real headless Chrome, frame by frame, never real time.** `src/capture.ts`
drives `window.__setFrame(t)` for every frame it wants and screenshots the
canvas in between; nothing in the template reads the wall clock. That is
what makes the same params produce byte-identical output on a slow container
and a fast one, and it is also what caught two real bugs no amount of
reading the template would have: `drawHandwriting()` originally wrote
`const text = P.text`, which shadows p5's own `text()` drawing function for
the rest of that scope, so the call three lines later stopped being a
function call and every handwriting render failed with `"text is not a
function"`. `drawData()`'s `const pop` did the
identical thing to p5's `pop()`. Both were caught only because
`tests/render.test.ts` actually renders a clip and waits for it to finish
rather than mocking the capture step — a mocked version of that test would
have passed both times.

**p5's own box-wrapped `text(str, x, y, w, h)` does not behave the way it
looks like it should**, and the second real bug was worse than a crash: the
"statement" beat's headline ran off the right edge of a 1920px frame instead
of wrapping, silently, no error, first found by rendering a real frame and
looking at it. `drawWrapped()` in both templates now does the wrapping by
hand — greedy word-wrap against `textWidth()`, one line drawn at a time with
`textAlign(_, TOP)` and a computed `startY` — because that is the only way
this file has found to know exactly where a line landed, which the paint-drop
tip cue in `paint.html` and the beat-to-beat layout in `vox.html` both depend
on.

**`render_templates/`, not `templates/`.** `hub/integrity.py`'s
`check_orphan_templates()` walks every `modules/*/templates` directory
looking for Jinja pages nothing renders — and `paint.html`/`vox.html` are not
Jinja; Puppeteer navigates to them with `page.goto('file://...')`, which
nothing that check understands can see. A directory literally named
`templates` read as two orphans the first time this ran, and — worse — only
*one* of the two, because `vox.html`'s own comment happened to mention
"paint.html" in passing, which is exactly the "prose is not a call site"
trap this file already names a dozen times, satisfying the checker for the
file it never actually reaches. Renamed rather than exempted, the way
`modules/ad_builder` avoids the identical collision by nesting its own layout
JSON under `src/templates`.

**No stock photography for a Vox beat, and that is stated rather than
faked.** A beat carries `image_query` — a search hint a model wrote, never a
resolved URL — because this Node service has no Pexels/Pixabay/AI-image
integration of its own; those all live in the Python Hub. The "collage"
treatment draws a soft abstract panel and prints the query as a caption
rather than pretending it has a photograph. Wiring a resolved image URL onto
each beat is real, deliberate future work — a change to
`vox_spec._BEAT_KEYS` and `hyperframes.vox_params()` — not a silent
workaround here.

**p5.min.js is vendored, never fetched from a CDN, and it self-heals in
dev/test.** `scripts/copy-assets.mjs` copies it from `node_modules/p5` into
`dist/render_templates/` at build time, same as `modules/ad_builder`'s own
asset-copy step. `npm test` and `npm run dev` both run `tsx` directly against
`src/*.ts` with no build step in between, landing on the *source*
`render_templates/` directory instead — which has no `p5.min.js` in it until
something puts one there. `src/capture.ts`'s `ensureP5Vendored()` does that
lazily, on first render, from the same `node_modules/p5` — without it, every
render in dev and every render in CI fails with `"resizeCanvas is not
defined"`, which is not a hypothetical: it is exactly what happened the
first time `npm test` ran against a clean checkout.

**Bounded concurrency, because a render pins a CPU core for the duration of
the capture.** `HF_RENDER_CONCURRENCY` (default 1) queues renders rather than
running them all at once — on a 2-CPU container, unbounded concurrency here
is unbounded competition with the Hub's own gunicorn workers, the risk the
in-container placement decision above accepted in exchange for not paying
for a second service. The queue, the in-memory job table
(`src/jobs.ts`) and the output-file sweep (`sweepOutput()`, 48 hours) all
mirror rules `modules/hyperframes_tools/jobs.py` and
`modules/ad_builder/src/retention.ts` already state for the identical reason
one layer over.

### "It's done, come back" — wherever "back" turns out to be

A render here is minutes of headless Chrome, exactly like HeyGen and Runway,
and nobody sits on the page that started it waiting — they go back to
whatever else they were doing. Until now the only way to learn a render had
finished was to remember to check. `hub/job_notify.py` is a small pointer
registry a tool opts into *alongside* its own job store — it does not
replace `modules/hyperframes_tools/jobs.py`, which stays the detailed record
of params, files and filing; the pointer holds only who is waiting, what to
call it, and where to send them. `hub-job-notify.js` polls
`/api/background-jobs/mine` from wherever the person actually is and tells
them once, a corner card modeled on `hub-qa-nudge.js`.

**Unlike `hub-cheers.js` and `hub-qa-nudge.js`, this one deliberately rides
with the chrome.** Those two stay out of the scripts `wsgi.py`'s `HubBar` and
`hub/__init__.py`'s own injector carry into mounted modules and
blueprint-registered ones — "one place that can raise an interruption is
enough to be sure it is raised once." That reasoning does not apply here: the
whole point of this feature is telling somebody their render finished on
whatever tool they wandered off to, and a mounted module is exactly
"wherever they wandered off to." It is loaded from all three places
(`base.html`, `HubBar`, the third injector) and cannot double-notify for
being loaded from more than one, because each job is marked shown in
`localStorage` by its own id, not once per page.

**A pointer only ever advances when something asks it to, and the person
who started the render is not necessarily still on that page to ask.**
HyperFrames' own `_poll()` already writes the finished URL onto its row on
any request — "closing the tab does not lose a render." `hub-job-notify.js`
gets that write-through for free by fetching each running pointer's own
`poll_url` (the tool's own status route, reused rather than re-implemented)
from wherever it happens to be polling, same-origin only. A tool that
registers a pointer with no `poll_url` simply does not self-advance from
elsewhere — it sits at its last known status until somebody visits its own
page, which is a degrade rather than a requirement.

**The sweep runs from `update()` as well as `register()`, not only the
one `modules/hyperframes_tools/jobs.py` already had.** That store sweeps
only inside `create()`, which means a burst of jobs marked done purely
through `update()` calls — no further registrations to trigger a fresh
sweep — never gets capped until somebody's next, unrelated render starts.
`hub/job_notify.py`'s `update()` sweeps too, the moment a pointer reaches a
finished state, which is exactly when the cap should apply rather than
whenever it next happens to be convenient.

**And declaring `hf_render_service` in `NO_ACTIVITY` broke the check that
holds that table honest, because the check is Python-only and the module is
not.** `check_silent_modules()`'s `seen` dict — the thing the stale-exemption
half of that check reads to decide "does this module still exist" — is built
entirely from `_sources()`, which globs `*.py`. A module directory with zero
Python files in it, which `modules/hf_render_service` genuinely is, never
produces a single entry in `seen`, so the moment it was declared the check
read `hf_render_service not in live` and reported the brand-new declaration
itself as a stale exemption naming a module that "does not exist any more" —
one line after adding it. Not a fluke of this module: `modules/ad_builder`
survived this only because its Hub-side half in `hub/` gives it Python files
to be seen by, which is a coincidence of that module's shape rather than
something this check actually asked for. `live` now also walks
`modules/*` on disk directly and counts any real directory as live whether or
not anything in it is Python — the same fix in spirit as `hub/blog_spec.py`'s
`_KIT_UNREAD` and `services/abcd_service.py`'s `HOUSE_LEGIBILITY`: a check
built for one shape must say so about the module in the other shape, rather
than silently reading it as absent. `test_activity_logging.py`'s own
hardcoded `["calculators"]` expectation was the other half of the same
staleness — a genuine second entry made it wrong the moment it was correct,
which is the ordinary cost of a test asserting a literal list rather than a
property.
