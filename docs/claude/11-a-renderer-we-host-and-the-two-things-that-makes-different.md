## A renderer we host, and the two things that makes different

`hub/hyperframes.py`, `modules/commercial_builder/vox_spec.py` and
`modules/hyperframes_tools`. Every other provider this Hub reaches is a hosted
REST API: we post a request and somebody else's servers do the work. HyperFrames
is an **open-source rendering framework** (Apache-2.0) whose normal shape is a
local CLI driving headless Chrome frame by frame and encoding with FFmpeg —
Node 22, Puppeteer and FFmpeg, none of which is in this Flask image and none of
which belongs in it. So the renderer runs as its **own Render service**,
`hf-render-service`, and `hub/hyperframes.py` is the whole of the Hub's side of
that wire. **That service is a separate deliverable and is not in this repo**;
until it exists, `HF_RENDER_SERVICE_URL` is unset, which is the state every
screen below is written for.

Two skills ride on it and they are deliberately different sizes. **Paint
animation is a treatment** — p5.js handwriting, paint-on and living-painting —
so it is a *sixth scene source* in the Commercial Builder beside stock, AI, the
spokesperson, an upload and a client asset, never a replacement for them. **A
Vox explainer is a complete output**, a 60–90 second editorial collage, so it is
a *ninth commercial type* rather than a scene option: a scene source that
produced a finished video would be a scene that is the whole spot.

**No API key, and therefore no `hub/quotas.py` marker.** It is self-hosted;
what it costs is Render compute rather than a per-render bill, and counting
renders here would be counting something nobody is invoiced for. What *is*
billed is the OpenAI call that writes a beat list, and that is recorded where
it happens.

**Configured, reachable and working are three questions.** `is_configured()`
reads settings and costs nothing, so it is what a page gates on — the paint
button and both standalone forms are simply **absent** without a service,
rather than present and failing at the moment somebody is waiting. `check()` is
one request and is what QC and Diagnostics read. Neither is evidence of the
third, which is why a job reports its own outcome and nothing infers success
from the submit. Not configured is **not measured**, never a cross:
`services/provider_check.py`'s rule, one provider further out.

**A mock is marked and never filed.** With no service a submit answers a job
carrying `_mock` and no file — so the tool reads as switched off, which it is,
rather than as broken. `is_deliverable()` is the **single** reading every filing
call site asks, the refusal `approve_render` already makes about a mock
Creatomate render. A truthy `url` alone is not the test: a job still rendering
has one too.

**An unrecognised render state reads as still running.** Treating one as
finished attaches nothing while reporting success, which this module has
already learned with HeyGen and again with Runway. The status route is also
what **attaches** a finished file, never the browser, so a closed tab does not
lose minutes of rendering nobody will start again.

**The templates are pre-authored and parameterized, never authored per
request.** Having a model write fresh HyperFrames HTML each time is slow,
impossible to QC, and throws away the one thing this framework offers that
Runway does not — the same input always renders the same film. `TEMPLATES` is
the contract and a name absent from it is refused **here**, because a service
404 and a typo'd template name arrive identically and only one is fixed by
restarting anything.

**The beat list is the join, and it is the whole risk.** A model writes JSON, a
template consumes JSON, and nothing between them otherwise checks that the two
agree — the shape `submit_render` already paid for, where an audit line read
`project.name` and `project.length` and every render this tool had ever been
asked for returned a 500 that reached the browser as "Bad response from
server". `vox_spec.validate()` runs over what the model wrote **and** over
anything typed by hand, because a rule the form keeps while the write breaks it
is not a rule. A beat it cannot read is **dropped and counted with its reason**,
never repaired: inventing the missing half of somebody's explainer is this
module writing copy nobody asked for, and a silently shorter list is a video
missing exactly the point somebody wanted made.

**The window is arithmetic, and the per-beat cap has to hold inside it.**
`rebalance()` scales the beats to the 60–90s window rather than the prompt
being asked nicely for a total — a model told "75 seconds" writes beats summing
to 52 and puts 75 in a field beside them. The first version put the whole
remainder on the **longest** beat and produced a 52-second card in a collage
explainer, past a per-beat ceiling that exists because nobody watches one card
for the better part of a minute. A cap honoured everywhere except in the
correction is not a cap; the remainder is spread across the beats with room,
and a list too short to fill the window is **left short** rather than forced —
that list is already failing the beat count, and the honest total is what the
duration check should read.

**A Vox explainer is refused where nobody sells the slot, by the route.**
`vox_spec.PLATFORMS` is `youtube` and `social` and deliberately **not `both`**,
which `config.PLATFORMS` spells *"CTV and YouTube"* — allowed there, a 60–90s
editorial piece is a CTV placement the buy refuses, with the platform field
reading as though it had been checked. `VOX_LENGTHS` is its own list because
`COMMERCIAL_LENGTHS` stops at 60, which is this format's *minimum*. And it is
one length at a time: the multi-length build exists because a :15 is cut down
from a :30, and each Vox length is a different beat list rather than a trim of
another. The Start page hides what it cannot offer and the **create route
refuses it by name**, because the form is not the gate.

**A spot with no storyboard cannot answer a storyboard's checks, and that
nearly shipped as a gate nobody could pass.** `run_qc` runs twenty-odd checks
and six of them read Scene rows — timing, footage, narration length, the CTA,
the YouTube hook and the spelling pass. A Vox explainer has none, so all six
reported real failures about a spot that is fine, and `submit_render` refuses
on `not _all_passed`: **every Vox render there could ever be was refused**,
with the panel naming six things nobody could fix. `NOT_FOR_VOX` marks them
not-applicable with the reason each cannot be answered — the same answer
`_check_render_service` gives a Creatomate spot, from the other direction, so a
reader can still tell *we looked and it is fine* from *this question is not
about this spot*. Declared rather than a `continue`, so a check added later is
a decision about that list rather than an accident.

**The two new checks advise and never block.** The render service being down is
an outage rather than a defect in the spot, and refusing to let somebody finish
a beat list over it is the gate `QR_CODE_RULES` explains getting switched off.
The duration is measured off the **plan** rather than a rendered file, for the
reason `abcd_service` scores the plan: a length problem found on an MP4 is a
re-render and one found on the beat list is free. Both are in **both** JS label
maps — a key absent from one is skipped silently by that panel's loop, which is
the failure `scene_assets` already had.

**The finished file goes to the client's Cloudinary tree and not the image
gallery.** Both skills produce video, and `filing.file_asset` models an image or
a raw file — the Commercial Builder leaves its commercials, spokesperson clips
and voice tracks out of the gallery for exactly that reason, because a row whose
thumbnail can never render is worse than an absent one. So a kept render is
stored through `hub/storage.put_remote()` (Cloudinary fetches it; this process
does not download a video to post it back up) and written to the **activity
log**, which is what puts it on the 360 record — and the two are **reported
apart**, because "stored" and "stored and on their record" are different
outcomes and one tick over both is how somebody learns not to trust the tick.

**A client is optional and is never guessed at.** With none picked the render is
still made and still downloadable, the way the Background Remover and the UTM
Builder work. What is refused is a *typed* name: `client_key.resolve()` answers
for everything, so the test is `known` rather than truthiness — it hands back
the input verbatim under `client` when nothing matched, and reading that as a
match is the typed-name failure the whole check exists to refuse.

**Both tools log under their own names**, declared in
`client_brand.WORK_KINDS`, because a standalone paint animation is not a
commercial and reading as one on a client's record would say we made them a
spot we did not. The name is **data and the call is direct**: an earlier
version passed each tool's bound logger down as a parameter, and a logger
reached through a parameter is one no static walk can follow —
`/api/integrity`'s silent-module check duly reported the module as never
logging at all, which was a true statement about what it could see. The fix is
a call it can read rather than an exemption asking to be trusted.

**One module for two tools, and the templates are prefixed.** Submitting,
polling, storing and filing are identical for both; two directories would be two
copies of that and the next fix to the poll would land in one of them. They are
hub-app blueprints, so they share the hub's Jinja environment and a bare
`index.html` here would resolve against the hub's own templates first —
`hf_paint.html` and `hf_vox.html`, the trap `/api/integrity` has a
high-severity check for. Both carry `hub/blueprint_guard.install()`, because a
blueprint is not behind `AuthGuard`.
