## Display Ad Builder: handoff for the next rounds

Written for whoever picks this module up next -- another Claude session,
another model, or a person -- after five rounds of "make it bulletproof"
(September 17-18, 2026; PRs #705, #710, #727, #738, #743) and a sixth pass
knocking six items off this list in overlapping PRs #750 and #752
(September 18, 2026). It says where things stand, what the rounds learned
about working in this module, and a costed backlog of the next
improvements with enough detail to start on any one of them cold. The
narrative of each round is in `docs/claude/43`; this file is the map and
the to-do list, not the story.

The sixth pass closed **items 1, 2, 3, 6, 11 and 12** below (staff paged
when a client leaves a note; the proof page streams its cell images from
disk instead of inlining base64; bulk approval can accept a size's
warnings; the style panel's number boxes track state; nightly failures
reach the team channel; the decision has its own alert line). Items 1, 2
and 3 landed in PR #752; items 6, 11 and 12 in PR #750. The remaining six
items stand.

### Where it stands

The module is `modules/ad_builder`: a TypeScript renderer (sharp, opentype.js,
its own HTTP server in `src/server.ts`) proxied by the Flask Hub at
`/tools/display-ads` (`hub/ad_builder_proxy.py`, with Hub-side routes in
`hub/ad_builder_link.py` under `/_hub/`). The pages a person meets, in the
order they meet them:

| Step | Page | Where |
|---|---|---|
| 1. Brief | the Hub start form | `hub/templates/ad_builder_start.html`, `ad_builder_link.start_form/start_submit` |
| 2. Design | the build screen | `public/build.html` (one 6,700-line inline script) |
| 3. Review | the contact sheet | `public/review.html`, `src/review-set.ts` |
| 4. Send | the Send page (Hub) and the client proof (renderer) | `hub/templates/ad_proof_send.html`, `hub/ad_proof_email.py`, `src/workflow.ts` |
| list | projects | `public/projects.html`, `src/health.ts` |

What the five rounds put in, briefly: the operator's seventeen items (per-size
text colour, per-size layouts, undo, speeds, Google Fonts, plain-English
checks with AI advice, accordions, one action row); the thirteen
smoothing items (no scope dialogs, save-and-go, the Done button, the
start-here strip); the seven follow-ups (adopt a size's look, client notes
per ad, hold-to-see-before, presets on the start form, keyboard nudging,
the health line, the nightly walk); the failure paths (sign-out and
renderer-down notices, autosave and preview retries, budgets on the public
proof routes); and the review sheet's retries, the Send page in the nightly
walk, and the restore drill.

### How to work here (learned the hard way)

**Verification is a fixed recipe.** In this order, all of it, before a push:

```bash
cd modules/ad_builder && npx tsc --noEmit && npm test          # ~5 min; includes the restart drill
E2E_FULL=1 npm run test:browser                                # ~3 min; needs a Chromium
python3 test_display_ads.py                                    # the string-pinning gate, ~1 min
python3 tools/preflight.py --merge                             # ~4 min; the two mcp_gateway rows fail in the sandbox only
```

The nightly recipe can be run locally end to end: seed with
`npx tsx tests-browser/seed.ts <outdir> AD-E2E-000001`, start the renderer
on 8791 with `ADMIN_TOKEN OUTPUT_DIR PORT HOST`, start gunicorn on 5055
with `DATABASE_URL=sqlite:////tmp/x.db PANEL_PASSWORD SECRET_KEY
ADBUILDER_ADMIN_TOKEN AD_BUILDER_URL=http://127.0.0.1:8791`, then run the
browser test with `E2E_BASE_URL=http://127.0.0.1:5055/tools/display-ads
E2E_HUB_PASSWORD=... E2E_REQUEST=AD-E2E-000001 E2E_FULL=1`. That is the only
run that puts the proxy, the base-path shim and HubBar in the path.

**The gate pins strings.** `test_display_ads.py` reads source files and
asserts sentences and identifiers are present (`check("...", "text" in
file)`). Every round adds one `test_the_<nth>_round_...()` function. When a
sentence on screen changes, a pin fails: that is the design, so update the
pin with the change rather than around it. `strip_comments()` is what "a
person reads"; British spellings in comments are fine, in copy they are not
(`tools/spellcheck.py` runs on every PR, and the words it flags include
"colour", "normalised", "honour").

**Never mirror the carry in the browser.** `src/carry.ts` resolves what a
size renders with; the build screen reads the server's answer
(`state.resolvedStyle` from the preview, `POST /api/concept/adopt-look`,
`POST /api/carry`). A second reading of that rule in JavaScript has cost this
repo twice. The same holds for `registry.familyFor()`: the browser's
`familyFor()` is a deliberate, tiny copy and is pinned as such.

**The browser test is the proof for anything on the build screen.** Unit
tests run the inline script in a stub DOM (`tests/editor-startup.test.ts`)
and pins read it as text; neither presses a button. `tests-browser/build.e2e.ts`
does, and it found two real defects in these rounds (the adopt row on the
authored size, the notice cleared by a sibling request). It skips by name
without a Chromium and never passes for lack of one. Play outages through
`page.setRequestInterception` and leave interception on for the rest of
the test. Number boxes in the style panel only redraw with the panel, so
assert on the preview, not on the box (backlog item 6 fixes this).

**Requests started beside the one that failed answer a moment later.** The
carry goes out with every preview. Anything that clears a state on "a good
answer" must clear only on an answer to a request started after the state
was set (`linkRaisedAt` in `build.html`), or the state flickers for
milliseconds and nobody sees it.

**The proxy turns outages into ordinary-looking responses.** A dead
renderer is an HTML page with a 503; an ended Hub session is a 302 to
`/login`, which fetch follows to a 200 page of HTML. Every page under the
proxy needs one reader of responses that names both (`noteLink()` in
`build.html`, `api()` in `review.html`); a new page that calls `.json()`
directly will show "Unexpected token <" during every deploy.

**Rate budgets key on the route.** A public route with an id in its path
must be added to `auth.budgetKey()`'s normalisation or every link gets a
fresh allowance. New public routes also need the Hub proxy's
`PUBLIC_PATTERNS` and the renderer's `isInternal` list kept in step, and a
line in `docs/claude/74` (every link that works without a login).

**The render route refuses a job whose platforms differ from the saved
campaign's.** A test that renders must post `doc.platforms`, not a subset.

**Pushing to this branch after a squash merge.** The branch name is fixed
(`claude/intelligent-keller-uwjki3`) and every PR from it is squash-merged,
so the remote tip is never an ancestor of main and a plain push is
rejected. Force-pushing is refused by the sandbox's permission layer, and so
is `git checkout -B` onto a merged tip. What works: commit on top of
`origin/main`, `git merge origin/<branch>` (it conflicts), write every
touched file back from your own commit with `git show <sha>:<path> > <path>`,
verify `git diff --cached --stat <sha>` is empty, commit the merge, push.
The merge commit is content-free and says so. A fresh branch name per PR
would remove the dance, if the operator allows it.

**Parallel shell calls do not share a working directory reliably.** Use
absolute paths in every command that runs alongside another.

**Every reply ends with the Render environment list.** "Nothing has to be
added", in those words, when nothing is. Every round so far needed nothing.

### The backlog, costed

Each item: what is wrong, what to build, where, how it is proven, and a
rough size (S under an hour, M a few hours, L a day). Ordered by what bites
first.

**1. Staff are not told when a client leaves a note. ~~(S)~~ DONE (sixth
pass, PR #752).** `workflow.commentOnClientProof()` now takes an optional
`notifier: ProofCommentNotifier` that receives `{project, proof, note,
platform}`; the HTTP handler wires it to `notify()` and phrases the alert
itself, so `PUBLIC_URL` stays out of the workflow layer. A whole-set note
links to the campaign; a per-ad note deep-links to that size on the build
screen. Injected in `tests/third-list.test.ts` with a spy notifier; a
notifier throw is caught so an alert that cannot go out cannot swallow
the client's note.

**2. The client proof page inlines every image as base64. ~~(M)~~ DONE
(sixth pass, PR #752).** `GET /client-proof/<token>/cell/<i>` now streams
the frozen cell from the proof's directory; `clientProofHtml()` writes
`<img src="/client-proof/<token>/cell/<i>" loading="lazy">`, dropping a
Meta set to a few kilobytes. Refused at the regex (`[a-f0-9-]{36}` and
`\d+`), bounds-checked against `proof.cells`, and the file path comes
from the proof, not the URL. Added to the proxy's `PUBLIC_PATTERNS`, the
renderer's public list, and `docs/claude/74`.

**3. Bulk approval cannot accept warnings. ~~(M)~~ DONE (sixth pass, PR
#752).** The review page has a second panel underneath the pass button:
every warn-only size (no failures on any bought platform, at least one
warn, not already approved) is listed with its plain-English findings
per platform. "Approve these, accepting their warnings" signs off every
ticked size at once through `/approve-size` with `acceptWarnings: true`.
A fail on any platform excludes the whole size; a revision mismatch 409s
the same way the pass button already did.

**4. A staging Hub, so the nightly staging job stops idling. (L, mostly
operator time)** `nightly-browser.yml`'s second job waits on
`STAGING_HUB_URL`, `STAGING_E2E_REQUEST` and `STAGING_HUB_PASSWORD`. Render
can host a second service from `render.yaml` (a copy of `smart1-hub` with
its own disk and a `PANEL_PASSWORD` of its own, `autoDeployTrigger:
checksPass` on main, no Cloudinary or OpenAI keys so nothing is billed).
Seed one campaign on it with `seed.ts` through a one-off shell, set the
three repository values, and the job runs. Say clearly in the write-up that
the test edits the campaign it opens, so the request id must be that seeded
one and never a client's.

**5. Presence: two people on one campaign. (M)** Two staff editing the same
campaign meet the 409 recovery dialog, which merges well but late. Add
`POST /api/campaign/<id>/presence` (who, when; kept in memory, 90-second
expiry) called by the build screen every 30s while open, and a line under
the campaign name: "Also open by Todd, 2 minutes ago". No locking; the
recovery dialog stays the safety net. Prove with the editor harness (two
names, one expires).

**6. The style panel's number boxes lag the arrows and the keys. ~~(S)~~
DONE (sixth pass).** `setBlockStyle()` now writes the shown value back to
every `<input data-style="<block>" data-prop="<prop>">` on the panel,
skipping colour pickers and the Put-it-back buttons. An unset value clears
the input (rather than leaving the old number). An ArrowUp on the canvas
moves the ad and its input in step. Pinned in `test_display_ads.py`; the
e2e can assert on the box after ArrowDown when Chromium is available.

**7. Split `build.html`'s script into files. (L, risky)** One 6,700-line
inline script is the reason every change there is a Python edit script
with exact-string anchors. The safe route is incremental: move pure
helpers first (`esc`, `copyText`, the carry-reading helpers, `PADS`) into
`public/build/*.js` loaded before the inline script, keeping every global
name; `tests/editor-startup.test.ts` and the e2e are the net. Do not do
this in the same PR as a behaviour change. `withBase()` must rewrite the
new `<script src>` paths; it rewrites `src` attributes already.

**8. Font manifest at build time. (S)** `fonts.ts` probes every
`@fontsource` file at first use (~350ms) to find the fifteen families
opentype.js cannot parse. Write the probe's answer to
`src/fonts.manifest.json` in a `prebuild` script and read it at load, with
the probe as the fallback when the manifest is missing. Prove:
`tests/fonts-google.test.ts` asserts the manifest names the same families
the probe does.

**9. Preview cache per size and revision. (M)** Switching size re-renders a
preview that has not changed since the last visit. Key a small in-memory
LRU in `server.ts` on `sha256(campaign JSON + conceptId + size + platform)`,
24 entries, 60-second expiry, and serve hits without touching sharp. The
build screen already sends the whole campaign, so the key is exact. Prove
with the http test: two identical previews, the second answers in under
20ms and carries `cached: true`.

**10. The rate-limit buckets are per process and reset on every deploy.
(S, decide rather than build)** `auth.ts` holds budgets in memory. A deploy
resets them, which is harmless for the preview budget and slightly
generous for the public proof routes. The honest fix is a file under
`OUTPUT_DIR/limits.json` rewritten every 10s; the honest alternative is a
sentence in `docs/claude/74` saying the ceiling is per process. Either is
fine; today it is neither.

**11. Nightly failures go to one inbox. ~~(S)~~ DONE (sixth pass).** Both
jobs in `nightly-browser.yml` now post the run URL to
`secrets.NIGHTLY_WEBHOOK_URL` on failure, gated so an unset secret is a
no-op (the message reads "NIGHTLY_WEBHOOK_URL is not set; skipping team
ping" in the log). The message says which job failed and links the run
page. Render environment note: `NIGHTLY_WEBHOOK_URL` is a GitHub Actions
secret, not a service env var; nothing to add on Render.

**12. The proof page's decision and note share one `#status` line. ~~(S)~~
DONE (sixth pass).** The decision has its own alert line
(`<p id="decision-said" role="status" aria-live="polite">`) under its
buttons, reusing the notes' `.said`/`.sent`/`.notsent` classes. A failed
`decide()` restores the page's original status sentence and writes the
error to the alert line. The success path still writes to `#status`
because approve genuinely changes the page's state. Pinned in
`tests/third-list.test.ts` by asserting the markup.

### What not to do

- Do not add a second copy of the carry, the family fallback, or the QA
  sentences in the browser. Ask the server.
- Do not make Meta's text-coverage note a warning again; it was demoted on
  purpose (`QaFinding.status: 'info'`), and `rollUp()` ignores it.
- Do not skip, quarantine or loosen the restart drill or the browser test to
  get a PR green. Both found real defects on their first runs.
- Do not point the nightly staging job at production. The test edits the
  campaign it opens.
- Do not write to the Render environment from a session. The rule in
  `CLAUDE.md` about linked env groups applies; every round so far needed
  nothing added.
