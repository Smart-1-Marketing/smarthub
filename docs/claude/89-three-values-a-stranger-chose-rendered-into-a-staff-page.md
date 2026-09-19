# Three values a stranger chose, rendered into a staff page

The Display Ad Builder was swept for what reaches a page as markup. Three
findings, all the same shape: **a value somebody outside this process chose,
interpolated by a helper built for a different context.** Each was proven by
rendering it before it was fixed, and each is held by a test in
`modules/ad_builder/tests/escaping.test.ts` that fails when the fix is
reverted.

## A render job's error was the diagnosis and the payload

`diagnostics-page.ts` defines `esc()` and `row()` uses it on every field —
`esc(c.level)`, `esc(c.label)`, `esc(c.detail)`, `esc(c.fix)`. `jobsSection()`,
directly beneath it, escaped **none** of its five: the job id, the status, both
progress counts and `j.error`.

`j.error` is `err?.message ?? String(err)` from the render pipeline, and
several of those interpolate a value a rep supplied:

```
Fetching background failed: 404 <the url they pasted>        assets.ts:242
Asset exceeds 20971520 bytes: <the url>                      assets.ts:245
OpenAI images returned 400: <150 bytes of the response body> imagery.ts:310
```

`assetUrlIsSafe()` passes `https://res.cloudinary.com/demo/image/upload/a.jpg?x=<img src=x onerror=…>`
and is **right** to — it is an SSRF guard, it vouches for the scheme and the
host, and the path and query are none of its business. The error message is
built from the raw `ref`, not a normalised URL, so the markup arrives intact.

It is stored XSS in a staff session, on the page somebody opens *because* a
job has already failed — which is exactly when that row is in the list. Every
field in `jobsSection()` is escaped now, and the note there says why.

## An HTML escaper cannot hold a JavaScript string

`overview.ts` built its brief form's endpoint inside the script:

```js
fetch('/api/campaign/${esc(d.requestId)}/brief', { … })
```

`esc()` escapes `& < > "`. It does not escape the apostrophe that closes that
literal, so a requestId of `x'+alert(document.domain)+'` renders as

```js
fetch('/api/campaign/x'+alert(document.domain)+'/brief', { … })
```

— valid JavaScript, in the staff-only branch (`editable: checkAuth(…).ok`).

The fix removes the context rather than escaping around it: the endpoint rides
on `data-endpoint` and the script reads `form.dataset.endpoint`. `esc()` is
correct for a double-quoted attribute. Where a value genuinely has to land in
a script, `proof.ts` has `jsonScript()` and its docstring explains the
`</script>` and U+2028 cases.

**The first assertion written for this could not fail.** It tested the whole
document for `+alert(document.domain)+`, and the id is also printed as
ordinary HTML text in the header (`Campaign x'+alert(…)+'`), where an
apostrophe means nothing — so it matched the harmless site and passed with the
script untouched. It is asserted against the script block alone. Same shape as
the `kb()` assertion in #219 and the doubled-separator one in #303.

## The unguessable id the submitter could choose

`POST /api/intake` is the one unauthenticated write in this renderer — the
form a client frames on their own marketing site. It generates the requestId
from `crypto.randomBytes`, and the comment where it does so says why: *"the
requestId doubles as the proof link capability, so it must not be
enumerable."*

Two lines later:

```ts
const record = { requestId, receivedAt: …, ...body };          // body wins
await buildCampaign({ requestId, ...body } as Submission, …);  // body wins
```

Spread last, so a submission carrying its own `requestId` replaced the
generated one. It never reached a file path — every write uses the local
const — but it reached the stored campaign, and `server.ts`'s campaigns
listing reads `d.campaign?.requestId` straight back out onto the staff screen.
A submitter choosing the id a capability is minted from is the opposite of
unguessable.

`projects.save({ ...existing, ...body, projectId: existing.projectId })` in
the same file had always pinned its id *after* the spread. That is the rule,
and it is `submission.ts`'s `withServerIdentity(body, identity)` now, so the
argument order cannot express the wrong answer.

There is still **no format validation on requestId anywhere** —
`validate.ts` checks only that it is non-empty, and it is used as a path
segment (`campaigns/${requestId}.json`). Reported rather than fixed: every
id in play is server-generated today, so a constraint added now would be
untested against a real violation.

## And the suite was red for a reason that was not the code

`fonts-google.test.ts` failed two assertions on a clean checkout of main:
3 families where it wants 30. `package.json` declares 36 `@fontsource`
families and `node_modules` held three, because `.claude/hooks/session-start.sh`
tested `[ -d modules/ad_builder/node_modules ]` — the directory exists, so it
reported *"Ad builder dependencies already present"* and skipped the install.
A resumed session inherits whatever the image was built with.

The worse half is the one that does not announce itself: that test's own
docstring describes it — a family declared and not installed is silently
swapped for Poppins mid-render, so a brand whose site sets Lato gets Poppins
while the control shows Lato.

The hook compares a sha1 of `package-lock.json` against a stamp written into
`node_modules` on a successful install, so adding a dependency invalidates it
by construction. Verified in all three states: no stamp installs, a matching
stamp skips, a changed lockfile reinstalls. CI runs `npm ci` and was never
affected.

## What was measured

Coverage over the renderer suite, `node --experimental-test-coverage`:
**89.01% of lines** across 56 source files. The weakest are
`diagnostics-page.ts` (19.5%), `overview.ts` (36.3%), `imagery.ts` (57.6%) and
`server.ts` (66.2% of 3,488 lines) — the first two are where this sweep went.
The rest is the standing list for the next pass.
