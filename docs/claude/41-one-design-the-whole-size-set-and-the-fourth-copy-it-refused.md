## One design, the whole size set — and the fourth copy it refused to be

`modules/magic_resize` (`/tools/magic-resize`) takes a finished design and
produces every size in a bundle from it. It is a **new module rather than a
feature on Image Creator**, because the two have structurally different
project shapes: Image Creator is one canvas, one size, one Fabric JSON, which
is right for what it does, and this is one design and many frames derived from
it. What it does **not** do is fork that tool's services — the photo search,
the asset lookups and the AI proxies are provider-agnostic and are called, not
copied.

**It is not `/tools/display-ads`, and that is the first trap in this file
rather than a preference.** That prefix is the Display Ad Builder's — the
TypeScript renderer proxied by `hub/ad_builder_proxy.py` — and
DispatcherMiddleware routes purely by URL prefix, so a second module under it
never receives a request and does not even 404: it is swallowed by whichever
mount owns the prefix. The two tools are also different jobs and the names say
so. The Display Ad Builder *generates* a size set from copy and a brand
against hand-authored per-size layouts; this *resizes* a design somebody drew.

**Every dimension the spec kit publishes is read from the kit, and none is
restated here.** This repo already held **three** descriptions of display ad
sizes: `hub/creative_specs.py`, which is the transcription `kit_drift()` holds
against the published page; `modules/ad_builder/src/config/platforms/*.json`,
the renderer's own; and `modules/image_creator.CANVAS_PRESETS`, a canvas
picker. They agree by luck rather than by construction, which is the drift the
two rate cards cost a year. A fourth would have been the one that went stale,
because it is the one no check reads — and the build plan's own table already
disagreed with the kit transcription about the HTML5 package (600 KB against
the kit's 200 KB) before a line of it was written. `sizes.py` names a
`creative_specs` unit id and nothing else: the width, the height and the
weight ceiling are read at import, and a delivered file is judged by
`creative_specs.check()`.

**A unit the kit stops publishing is dropped and named, never guessed at.** It
lands in `UNRESOLVED` and `check_kit_alignment()` reports it, and the bundle
comes back short — a size we cannot verify is one we must not silently deliver
against, and a fallback dimension carried here to keep the bundle whole is the
second copy the module exists to refuse. Six sizes **are** ours, each with
`source: "house"` and its reason: the 336x280 and 320x100 IAB units the kit
does not weigh, Google's two Responsive Display asset sizes (an asset pool
Google lays out itself rather than a banner it serves whole, judged on
Google's published 5 MB with that provenance printed), and the two social
resolutions the kit publishes as a ratio rather than a size. A house size with
no published ceiling is reported **not measured** rather than judged against
the unit it resembles.

**Two tiers, because two different things are being asked.** Between
neighbouring shapes the answer is arithmetic and objects **re-anchor** — each
is measured against the edge it was nearest and put back the same distance
from that edge in the new frame's terms, because scaling raw x/y drifts
everything toward the origin as a frame shrinks and a right-hand button walks
into the middle. Between distant shapes the answer is a layout decision, and
`templates_layout.py` holds four **house-authored, fixed** arrangements —
leaderboard, skyscraper, square/rectangle and story — the choice
`SOCIAL_STRUCTURE_TEMPLATES` makes in the Commercial Builder and for the same
reason: a layout a model picks differs between two renders of one campaign,
and the whole promise of a size set is that it is the same ad. Which tier a
frame took, and why, is printed on the frame.

**A slot contains and never covers, and that is mechanical rather than
taste.** Covering means overflowing; a background may overflow because the
canvas clips it, and a slot clips nothing unless the object carries a clip
path. This module emits plain Fabric objects, so a covered slot is a
photograph hanging off the ad — which the guard then reports as clipped, on
every frame, for ever. The first version did exactly that, and the test is
what found it.

**A frame the engine is unsure about is `needs_review`, never `auto`.** The
guard runs after placement and names the objects: an overlap, or anything past
the frame edge. A background is exempt from both because covering the frame is
its job, and a decorative object is exempt from the overlap check alone — a
rule behind a headline is the design — but not from the bounds check, since a
flourish hanging off the edge is still clipped. The tolerance is there for the
same reason `QR_CODE_RULES` is advisory: a guard that fires on a hairline
touch is one somebody switches off, and switching this one off costs the real
findings.

**Nothing is dropped in silence, and copy is the line that decides.** Every
object a template had no place for is named in `unplaced` and stays on the
design. One carrying **words** marks the frame, and one carrying none is a
note beside it — a photograph left off a 728x90 is obvious to anybody looking
at the frame, and a line of rate copy that did not fit is not, and it is the
client's own words going missing. Nothing is invented for an empty slot
either: a headline slot with no headline is left empty and said so, because
filling it with the next nearest thing is how a disclaimer ends up where the
headline goes.

**The propagation rule is two halves that fail in opposite directions.** A
**copy** edit on the design reaches every frame, a hand-tuned one included —
making a rep retype a headline eight times is how one of the eight goes out
with last week's offer on it — and an edited frame that receives one is
*flagged*, because new words may not fit a box somebody set by hand and
nothing here can see that. A **layout** edit reaches `auto` and `ai` frames
and never an `edited` one: somebody moved that button on purpose, and
regenerating it destroys a decision with nothing on any screen saying so. The
skip is reported rather than silent, since a frame that regenerated into
exactly what it already was and one that was never touched read alike.

**AI recompose is the fallback on one frame, never a pass over the set.**
There is no route that recomposes a set: a model asked for eight layouts
produces eight a person then has to check, which is the work the templates
already did. It is handed roles and boxes and **not** the client, the brand or
the copy; it returns *positions* for objects that already exist; an id it
invented is dropped and counted; an object it did not mention keeps its place;
and its answer goes back through the same guard a template's output does,
because a proposal that arrives with a collision must not read as the fix for
the collision it was asked about. Keeping one is a press, and it is `ai` and
not `auto` — which frames a template produced and which a model adjusted is
what somebody scanning a set wants to know.

**The dimensions are the unit, and are never traded for weight.** Export
judges the browser's rendered bytes against the ceiling and, over it, runs a
quality ladder with `max_edge` pinned above the frame's own longest side so
the shared optimiser cannot resize on the way past: a 300x250 shrunk to
280x233 to save weight is not a Medium Rectangle any more. A frame that cannot
get under its ceiling above the quality floor is **left out of the pack and
named in it** — the rule `deliverProject` already applies to a QA-failing
size, because seven files where there should be eight is a difference ad
operations assumes they caused.

**The type floor is ours and warns rather than blocking.** No platform
publishes a minimum type size for display, so a hard failure would be house
guidance wearing a platform's name — the reason `HOUSE_LEGIBILITY` is kept out
of `THRESHOLDS` in `services/abcd_service.py`. It matches the per-size
`minFontPx` the display-ad renderer already carries, so the two halves of this
Hub at least agree, and the build plan asks for a figure signed off rather
than assumed.

**What is deliberately not built yet**, so its absence is a decision rather
than something to rediscover: the Fabric editing surface is **not** shared
between this module and Image Creator. That refactor is real — `editor.js` is
1,845 lines of module-scope globals with no export structure — and it is the
highest-risk, lowest-immediate-value piece of the plan, which is the note this
file already makes about a big-bang rewrite of working modules. Until it
lands, a frame is produced, judged and reviewed here and hand-tuning one goes
through `POST .../frames/<size>` with objects the editor supplies; `Adjust
Variant` and the browser-side render behind the export button are the half
that needs it.
