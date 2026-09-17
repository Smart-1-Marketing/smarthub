## The one module that is not Python

The **Display Ad Builder** (`modules/ad_builder`) is a Node service, not a
Flask module. It is ~10,000 lines of TypeScript with a native image pipeline
(sharp rasterises SVG and steps a quality ladder until each ad fits the
platform's file-weight limit -- Amazon allows 40 KB for some placements), so
porting it to Pillow would change creative that clients already receive.

It runs as a **second process in the same container**. `docker-start.sh` starts
it on 127.0.0.1 with a restart loop and then execs gunicorn as PID 1;
`hub/ad_builder_proxy.py` proxies `/tools/display-ads/*` to it behind the Hub
login and adds the admin token server-side, so nobody needs a second password.

Things that follow from that, each of which has a comment where it lives:

- **Two processes, one plan.** This costs ~150-200 MB of image and a second
  build step. If Render builds start timing out or memory gets tight, the ad
  builder ships its own `render.yaml` and can move to its own service --
  only `AD_BUILDER_URL` changes. Nothing else in the Hub knows the difference.
- **The pages link from the site root.** `fetch('/api/render')` is correct
  standalone and wrong under a mount, so `src/basepath.ts` injects a shim that
  prefixes fetch, XHR and href/src/action from `X-Forwarded-Prefix`. Without
  it the tool loads perfectly and no button does anything.
- **`ADBUILDER_ADMIN_TOKEN` must be set** (16+ characters) or the renderer
  refuses its own internal routes. `/status` says so in words.
- **The client and proposal joins are Python**, in `hub/ad_builder_link.py`.
  The renderer never learns who our clients are; finished ads are filed into
  the client gallery through `modules/image_picker/filing.file_asset`, which
  records the public_id Cloudinary already has rather than re-uploading.
- **Its own tests need an `npm install` CI does not do**, so the gate is
  `test_display_ads.py` — pure Python over the files. That is a weak substitute
  for most of a renderer and exactly the right test for two things. The layouts
  are hand-authored coordinates, so whether a box exists, sits inside the safe
  area and clears every other block is a fact about the JSON. And the build
  screen talks to the render server across a wire nothing typechecks: the
  generate route answered `{ candidate }`, the screen read `{ candidates }`,
  and a generation that had just succeeded reported "image generation is not
  configured" — no runtime in between, so both halves are asserted together.

**A field the build screen offers is not a field any layout draws.** Every size
gets Headline, Supporting line, Offer, Proof point and Call to action. Not one
template carried a `trust` box, so the proof point was typed in, saved,
word-counted and rendered nowhere on every ad this tool has ever produced. The
box exists now wherever there is room — beside the button on the rectangles,
above it on the skyscrapers — and the two canvases with genuinely no room
(728x90, 320x50) say so beside the field rather than accepting copy they will
throw away. `/api/build/options` reports which blocks each size draws, so a
family added later cannot reintroduce the silence.

**A copy edit is per size; a text-box style is per concept.** The panel headed
"Text boxes for 300x250" was applying to all eight, so a type size set to suit
the leaderboard shrank the skyscraper with it while the heading insisted
otherwise. Copy genuinely is per size, and now asks which it means the first
time each field is edited — the answer stays on screen as a toggle rather than
being a dialog nobody can revisit. "Every size" writes the default **and**
clears that field's per-size overrides, or the override keeps winning and the
edit reads as having failed.

**Nine positions cannot express "a bit further down".** A background photo is
drawn to cover the canvas and the overflow is cut off, and the crop anchor was
one of SVG's nine `preserveAspectRatio` alignments — which answers "top or
bottom" and nothing between, and cannot express zoom at all. `svg.coverRect()`
computes the rectangle instead, from an offset and a zoom, so the control is a
pad of arrows and a slider. The offset is a **fraction of the picture's own
overflow, not pixels**, so one setting shows the same part of the photograph on
a 300x250 and a 970x250 — which is what "the same ad in eight sizes" has to
mean. Its sign names *the part of the picture that shows*: -1 is "show me the
top", which slides the picture **down** until its top edge meets the canvas.
Backwards, every arrow moves the opposite way from the one pressed, and on a
symmetrical photograph that survives a glance. The nine old alignments are kept
and converted, because concepts saved before this carry one; a source with no
intrinsic size (an SVG, which sharp reports as 0x0) still falls back to
`preserveAspectRatio`, or every number would be NaN.

**Two blocks printed on top of each other is invisible to every other check.**
Contrast samples what is behind the ink and finds the other block's fill; the
fit pass finds copy that fits its own box perfectly; the safe-area pass finds
both boxes inside the margin. The ad has a headline printed through a button.
It was unreachable while every box came from a hand-authored template — the
diagnostics page checks those for overlaps at boot — and became reachable the
moment the button and the logo gained nudge arrows. `qa.ts` has a `collision`
check now, and it is a **fail**.

**"Align" means two different things, and the button had the wrong one.** For
type it is where the line sits inside its box. For a button, the label is
already centred by every template — so setting the CTA's `align` moved
nothing, and Left, Center and Right rendered identically. On the button it
moves the *button*, within the safe region.

**A control for something the layout does not draw is the proof-point mistake
again.** "Full background with copy panel" puts the copy on a filled card, and
that fill decides whether it can be read — it was a template constant, so a
client whose primary is a mid-grey got copy nobody could read. It is a control
now, offered only on the sizes whose layout actually draws a panel;
`/api/build/options` reports that alongside which copy blocks each size draws.

**A logo is the one asset nobody may edit, so the palette is what moves.**
`modules/ad_builder/src/palette.ts` proposes whole palettes that make the
existing mark read, each with the contrast it achieves,
and a person picks one. It is **arithmetic, not a model**: asking AI for "a
colour that contrasts" is a slow, non-deterministic way to do a subtraction
whose right answer is defined by a published formula, and the result has to be
checkable because the entire point is that it provably reads. Three rules keep
the proposals coherent — a palette that already works gets none at all, a
"change" to the colour it already is is not a proposal, and `light`/`dark` are
never inverted, because those two roles' *names* are what every template
resolves ink against. A dark mark on the `dark` role therefore gets no
recolour: that one needs the reverse logo, and the screen says so.

**The Insites scan already knows the client's real palette.** A scan reports
`colour_scheme` (primary/secondary background, text and accent — observed off
the live pages, not declared), `logo.logo_url` with `has_detected_logo`, and
desktop/mobile screenshots. That is better evidence than Brandfetch, which
routinely returns a palette without labelling which entry is the brand colour.
`/tools/display-ads/_hub/site-brand` reads it through
`modules.scans.app.latest_payload_for_domain`, joined **by domain, never by
name**, and a client with no scan is the ordinary case rather than an error.
The colours are offered beside the swatches and **copied, not applied**: which
of the five roles a site colour should become is a judgement, and guessing it
moves four other things.

**Only the rebuild route filed a finished render onto its project.** The batch
is what carries the proof URL, so a render started from the build screen wrote
a proof to disk that nothing linked to — and the screen told the operator to go
and look at a proof with no way to reach it. `fileJobOntoProject()` is the one
place that does it, and both routes call it.

**A control that cannot do the thing its label says is worse than a missing
one.** "Attach to client" and "Render all sizes" both stood on the toolbar
from the moment the page opened, on a build that had never been written down —
and both act on what is on the **server**, which on an unsaved build is the
previous version. So attaching filed the ads somebody had just finished
replacing, onto a client record, and reported a clean success for doing it.
The toolbar is the order of the work now: Save is the only thing offered on
arrival, Render appears once there is a saved build to render, and Attach once
there are rendered files to attach. The gate lives in `saveCampaign()` rather
than in the Save button's handler, because switching size with unsaved edits,
duplicating a set and starting a render all leave the server holding what is on
screen too — a gate only one of five doors opens is one people learn to resent.

**Four jobs, one button.** Render, render-and-file, file, and package were
spread across three places: a toolbar link that predated any render, a Deliver
button that only appeared once a status had changed, and the render itself. So
the ordinary job — build these and put them on the client's record — was two
controls with a page in between. The render button asks now. **Filing waits for
the files to exist**: started alongside the render it would copy the previous
build onto the client record, which is the failure above wearing a different
hat. And **a download is not a delivery** — `deliverProject` sets the project
complete, writes a "Delivered" note and mails the team, so the ZIP button asks
for `record: false`; the zip is byte-for-byte the same either way. A QA-failing
size is still withheld, and now *named* rather than silently missing from the
folder.

**A link that lands on a staff login is not a link you can send a client.** The
proof was behind the Hub session, so "here is the link, tell us what you think"
put a client on a login form for an account they do not have — and the *static*
`proof_<id>.html` the batch records is worse than that: it is rendered with no
action endpoint behind it, so its Approve button rewrites the page to say
"Approved" and posts nowhere. A client could sign a set off on it and no screen
here would ever know. Everything points at the live `/proof/<requestId>` route
now, `PUBLIC_PATTERNS` in `hub/ad_builder_proxy.py` lets a client reach that
page and the two decisions on it, and the entry is in `CHROMELESS` so a
prospect does not meet the staff sidebar. Two things fall out. **Our
credentials must not travel with an anonymous request** — forwarding the admin
token would tell the renderer a client is staff, which is the exact question it
asks to decide whether to draw the live editor, so a public page would quietly
gain an operator's controls. And **rebuild stays behind the login**: it
re-renders the creative for everyone holding the link and reaches endpoints
that are billed per call.

**And making that page public changed what may be interpolated into it.**
`renderProof` hands six values to an inline `<script>` through
`JSON.stringify` — the copy, the colours, the meta, the per-size overrides,
the delivered pack. That produces perfectly valid JavaScript and does **not**
escape `</script>`, and an HTML parser ends a script block at that literal
string wherever it appears, inside a quoted string included: one of them in
the data closes the block early and everything after it is parsed as markup.
It was harmless while only staff could open the page and is not now.
`meta.promoting` is the one worth naming — it falls back to
`campaign.landing.summary`, which is text read off the *client's own website*,
so it is nobody here's to vouch for. `jsonScript()` escapes `<` (`\u003c` is
the same character to `JSON.parse` and invisible to the HTML parser), and
U+2028/U+2029 with it, since both are legal inside a JSON string and are line
terminators to a JavaScript parser — either one unescaped is a syntax error
that costs the whole page rather than one value. `basepath.ts` needs none of
this: its prefix is refused by allowlist rather than escaped, because nothing
legitimate in a mount path has an angle bracket in it. `tests/proof.test.ts`
asserts all six are escaped rather than one of them, that the value still
round-trips through `JSON.parse` (an escape that changed the data would be a
different bug wearing a fix), and both halves of the editor split — a client
keeps Approve and Request changes, and dropping those alongside the editor
would look like a tidy-up and retire the feature.

**Approving is one event and two doors.** `recordDecision()` is shared, because
approval is the trigger for packaging and two copies of that drift into two
ideas of what "approved" delivers. What is *not* shared is the claim: a record
saying "approved by the client" about a decision an account manager made in the
office is the difference between a campaign that is signed off and one somebody
expects to be. The client's route reads no name at all — it is reached with
nothing but the project id, so a name claimed there is one anyone with the link
could claim — and the staff route sits under `/api/project`, which the admin
gate covers, so the name comes from the Hub proxy. A change request with no
detail is refused by both.

**Meta had a config file, every template drew its sizes, and one line dropped
it.** `.filter(p => p === 'google' || p === 'amazon')`, written out three times
— in the request route, the auto-render branch and the validator. A Meta buy
came back as a set of Google banners with nothing anywhere saying so.
`registry.acceptPlatforms()` is the one answer now, from the directory listing,
and it **names what it refused** rather than quietly building something
smaller. Two things had to be corrected with it: `meta.json` carried Google's
150 KB ceiling, which makes the quality ladder step a 1080x1920 story frame
down until it is mushy to satisfy a limit Meta does not impose; and the start
form never offered the choice at all.

**A URL is mandatory now, because it is what the tool reads.** The page is
fetched, its conversion points counted, and its own words become the first
draft of the headline, the supporting line, the offer, the call to action and
the proof point — and the picture is drawn against it too. It is required in
`start_project()` and not only on the form, because a `required` attribute is a
courtesy to somebody typing rather than a rule. The bug underneath it: the Hub
sent `website` and the renderer reads `landingPage`, so **every build started
from the Hub had no page analysis at all** while builds from the public form
did, and nothing on either screen said which kind you were looking at.

**And the analysis nothing asked for.** It sat on the project record and the
build screen's "write this for me" and "draw me a picture" both worked from a
business name and a headline somebody had already typed. Both read it now.
`suggestCopy()` also answered three of the five fields the screen offers, so a
draft left the offer and the proof point empty on templates that draw both —
and those two are exactly the ones that must never be invented. The prompt
forbids it twice, the code tops neither up, and an empty answer is **reported**
("their page says nothing about the offer, so it was left blank rather than
invented") rather than hidden. The draft fills empty fields only and writes to
every size: a drafted line is the set's copy, and landing it as a per-size
override would leave the other seven empty with the panel insisting the field
was filled.

**A generated picture is a draft, and the sweep removes it.** So keeping one
means moving it to Cloudinary first — `POST /api/imagery/keep`, which accepts
only a path this service wrote, because a route that uploads whatever URL it is
handed is an open relay into our own account. Filing it onto the client stays
the Hub's job: the renderer does not know who our clients are, which is the
line `hub/ad_builder_link.py` draws. Without that move, the gallery gains a row
that opens today, 404s after the sweep, and was never openable by the client
whose gallery it is in.

**And "only a path this service wrote" was one route's rule, not the app's.**
`POST /api/images/generate` takes a `previousUrl` too — on a revision the
previous picture becomes the primary reference, which is what makes "make the
sky darker" iterate rather than re-roll — and it did `path.join(OUT,
url.replace(/^\/files\//, ''))` with nothing in between. `path.join(OUT,
"../../../etc/passwd")` is `/etc/passwd`, and the route then copies whatever it
finds into the campaign's cache directory, which lives under `imagery/` and is
served at `/files/`. So an arbitrary readable file could be lifted into a
web-served folder and handed to an image model, from a value in a POST body.
Nothing errors: a path that resolves is a path that copies. `keep`'s check sat
one route above it in the same file, comment and all — this is the second copy
of a rule that was never written rather than one that drifted, which is the
same failure arriving from the other direction. `assets.generatedImagePath()`
is the one reading now.

**And the reference photos on that route skipped the guard the neighbours use.**
`referenceImages` is a list of URLs this server then fetches, and the loop
tested `^https?://` and nothing else — which admits plain http to any host:
`169.254.169.254`, `127.0.0.1` (the Hub's own gunicorn shares this container),
and every private range. `assetUrlIsSafe` already stands behind
`/api/background/apply`, `/api/logo/apply` and `/api/palette/variants`, and its
own note says why it applies even to a gated route: staff credentials leak and
the check costs nothing. It is applied here too now, with a ceiling and a
timeout — `arrayBuffer()` on an arbitrary URL wrote the whole body to the
volume `retention.ts` exists to keep clear, the shape `landing-images.ts`
already had right. The one real caller passes Cloudinary `secure_url`, so
nothing legitimate is refused. `tests/asset-paths.test.ts` holds both.

**Except that the sweep was not removing it, and the paragraph above was the
rule people reasoned from.** `retention.PRUNABLE` listed `google`, `amazon`,
`cache` and `jobs`; `imagery/` was in neither that list nor the protected one,
so every generated hero stayed on the volume for ever — on the one module whose
whole job is to stop this service eating the disk its neighbours live on.
Nothing errored in either direction: `keep` worked, the drafts simply also
survived. A rule the code does not keep is worse than no rule.

The platform directories were the same failure with a name on it. `render.ts`
writes to `<outDir>/<platform>/<concept>` and the list here said google and
amazon while `meta.json` sat in the registry being rendered — the **fourth**
hardcoded platform list in this app, after the three `.filter(p => p ===
'google' || p === 'amazon')` calls that dropped a Meta buy outright. It is read
from `loadPlatforms()` now, so a platform added next month is swept without
anybody remembering.

And **`deliveries/` is named in `PROTECTED` rather than merely left out**,
which is the difference between a decision and an oversight: an omission reads
as something to fix, and the next person to widen this list takes the file
behind the proof page's download button with it — the link a client opens
whenever they like, turned into a 404 for the one person the tool is for.
`tests/retention.test.ts` drives the clock rather than waiting on it, and holds
all three.

**The enforcer nobody tested said things that were not true.**
`image-budget.fitImageToBudget` is the one place guaranteeing every ingest
path -- a customer upload, a Pixabay hit, an AI generation -- ends up a valid
raster under 150 KB, and `imagery.ts` states the rule "holds by construction:
there is no path here that skips the enforcer". Nothing tested it, and two of
the things it *reported* were wrong.

`reencoded` was `encoded.length !== original.length`, which is true of very
nearly every image, because the file is always re-encoded and the bytes always
differ. Its own description said *"had to be compressed or downscaled to
fit"* -- the field meant **we did**, and it claimed **it had to**. So a
600-byte logo came back flagged, carrying a note that it had been optimized
*"to meet the 150 KB limit"*, about a file at 0.4% of that limit; and
`toFixed(0)` printed its size as **"0 KB"**, on a string whose whole job is to
be read by a person.

And a function that exists to make a file smaller could hand back a larger
one. A high-entropy source already saved hard -- a Pixabay `webformatURL`, a
low-quality photo -- overshoots on the q82 first pass; the ladder pulls it back
under budget and the answer is still bigger than what arrived. Measured, 52 KB
in and 137 KB out, described as optimized. The original is kept now where it
already fits, is inside the dimension cap and is already the format we would
write, and that test asks nothing about whether the ladder ran: **work having
been done is not a reason to prefer a worse result.**

Two things the tests pin that are correct and worth not losing. A source that
genuinely cannot fit is **refused in words** rather than written over budget --
the ladder is bounded at twelve steps and spends four of every five on quality
before it shrinks, so a large incompressible image runs out of them, and the
message says what to do. And the file docstring no longer claims 150 KB is
"deliberately below Google's 150 KB delivered-creative limit", which is not a
thing 150 can be than 150: what keeps a finished ad inside its platform ceiling
is the quality ladder `render.ts` steps on the composed raster, and this budget
keeps a 6 MB phone photo from being the input to it -- a different job, worth
having, just not the one the sentence claimed.

**And the gallery beside it could come back empty with everything healthy.**
Cloudinary publishes a folder as `asset_folder` in dynamic-folder mode and
`folder` in fixed, and a search asking for the wrong one returns **zero**: the
request succeeds, the page renders, and a client's gallery reads as a client
with nothing in it. `cloudinary.searchFolder` picked between the two from
`CLOUDINARY_FOLDER_MODE` -- a variable set in `modules/ad_builder/render.yaml`,
which is the manifest for running the renderer as its *own service*. Here it is
a second process in the Hub's container whose Cloudinary settings
`docker-start.sh` derives from `CLOUDINARY_URL`, and nothing sets the mode. So
the default answered for an account nobody had checked, and `gallery.ts` is
what reads it.

`hub/video_library.py` reached this first, ran both fields against this account,
found they answer identically and asks for **both** -- so the extra clause costs
nothing and there is no setting left that can be silently wrong. The renderer
does the same now, through one exported `folderExpression()` rather than an
expression built inline where nothing could test it. It also takes that note's
other half: the exact form is `=` and the subtree form the trailing wildcard,
because neither alone is enough and the old expression used `:` for both, so
the folder's own assets were matched by a contains rather than an equality.
`folderMode` still decides the shape of a dry-run public_id, which is a
different question and a real one.

**And fixing the search left the folder with two readings of itself.**
`folderExpression()` trims a trailing slash before it builds its clause; the
gallery's *heading* and its *output filename* took the string exactly as
handed in. That asymmetry is the whole failure, because the README's own
folder tree prints the folder **with** a trailing slash -- so pasting it
searched the right tree, found the right assets, and then wrote them to
`gallery_.html`, which is the same file for every project: build a second
gallery and it silently replaces the first, with a success line naming the
file it had just overwritten. The heading went the same way, `slice(-2)` on
`[..., 'summer-solar', '']` giving *"summer-solar — "* -- the client's name
gone and a dash left hanging. A doubled separator does it to the heading
alone, since `pop()` cannot see an empty segment in the middle.
`normalizeFolder()` is the one reading now, and `folderExpression()` reads it
too rather than keeping its own.

**And a relative path is relative to the page that carries it.** A dry run's
manifest holds no hosted URL, so a simulated asset is drawn from a file on
this disk -- `path.relative()` against `out/reports`, hard-coded, whatever
`--out` had actually been given. A gallery written anywhere else had **every
image broken** and still printed the file it had written. The output path is
decided before the assets are built now and `assetsFromManifest()` takes the
directory, because the page's own location is the only thing that path can be
computed from. It was right for the default, which is exactly why it stood.

`main()` is guarded on `require.main` so importing the file for its helpers
does not run the command -- unguarded, a test that imports it throws on an
empty argv and calls `process.exit(1)` on the test runner.
`tests/gallery.test.ts` asserts all of it, and one of its assertions had to be
retargeted first: the doubled-separator case was pinned on the *filename*,
where `pop()` is immune to an empty middle segment, so it was a property that
could not fail -- the same shape as pinning a rounding bug on a figure too
large to round to zero.

**The scan photographed their website and nobody was shown the photograph.**
`website_screenshot` came back from `/_hub/site-brand` and was drawn nowhere,
so an operator judging brand colour on a dark canvas had to open the client's
site in another tab to remember what they were matching — and mostly did not,
which is how an ad comes back "not really them" with nobody able to say why.
It is beside the swatches now, desktop and mobile, because half the sizes in a
display package run on a phone and the two are often laid out nothing alike.
**Reference and never a source**: `lightbox()` takes its "Use this picture"
button only when a caller passes one, so a screenshot opens without it — a
picture of somebody's website is not a background, and the logo in it is a
logo photographed off a page, which `hub/scan_facts.py` already refuses to
merge into what we hold. The panel also stopped giving up when the palette was
empty: keyed on the colours alone, it hid the picture on every site whose
colours the scan could not read.

**`has_google_font_api` says a site loads Google Fonts and never says which
face.** So it is passed on as the weak signal it is rather than dressed up as
a font recommendation, or somebody reads it as one and types a family the
renderer does not have. The useful direction is the one people do not expect:
a **false** is the actionable answer, because their type is self-hosted or
licensed and nothing offered here will match it by accident. It is tri-state —
the check lives in the scan's GDPR section, and a plan that did not run it
leaves the field out entirely, which must not read as "no". Not measured says
nothing at all rather than filling the space.

**And a panel redrawn under a callback is a callback writing to nothing.**
`drawControls()` replaces the whole left column, so an element captured before
a fetch is detached by the time the answer arrives: the write succeeds, the
screen does not change, and it reads as a button that did nothing. Re-read the
node after any redraw.

**Motion is a second pass over an ad that already exists, and the sequencing
is the feature.** `modules/ad_builder/src/animation.ts`. A GIF here is the
static ad played two or three ways: a frame is one more `compose()` with a
different `CopySet` or a different CTA fill, so there is no second renderer and
no way for the moving version to disagree with the still one about anything
except the thing that is moving. It is offered only once a build has been
**saved** — not once a client has approved it, which is a different and later
question — and it runs as its **own job** on the same queue rather than extra
work bolted onto the render. Both halves matter: a set nobody wants animated
costs exactly what it cost before, and an animation asked for on a Friday does
not mean re-rendering eight ads that were signed off on Tuesday. The gate is
enforced on the server (the campaign file on disk is what "a static build
exists" means) as well as in the build screen, because a rule the form keeps
while the write breaks it is not a rule.

**Four published numbers, and two of them are invisible on the screen they are
broken on.** Google requires an animated image ad to be 150 KB or less, to run
at **5 frames a second or slower**, and to **stop animating within 30
seconds** — loops included. A GIF with a loop count of 0 repeats for ever,
renders correctly in every browser, passes every eye here, and is outside the
rule; one at 20fps looks *better* than one at 5. So the loop count is
**computed** from the cycle length rather than chosen (`loopsWithin`, floored
at 1, so `loop: 0` is unreachable from that file), the frame delay has a 200ms
floor, and both are printed on the panel in words beside the preview. The
browser recomputes none of it — a second copy of that arithmetic is a second
answer to "is this legal", and the two disagree the day either is edited.
`ANIMATION_RULES` carries a **source** per number, and `maxSlides: 3` /
`maxFrames: 5` are marked as **ours**, the `services/abcd_service.py` rule:
"Google requires three slides" about a number Google has never published is a
claim a client can talk us out of once they check. (sharp writes `loop - 1`
into the file's Netscape block — the GIF format's count of iterations *after*
the first — and readers disagree about that byte, so the arithmetic is done
against the larger reading and `totalMs` can never understate what a browser
will play.)

**QA runs per frame, and that is the half most likely to be quietly wrong.**
Slide 2 is different copy in the same box: it can overflow, collide with the
button, or lose its contrast where slide 1 fit perfectly. Not hypothetical —
the first run of this against the sample campaign passed the static 320x50 and
**failed** the animated one on a clipped headline, twice, naming the slide. So
every frame goes through `runQa`, frame 1's findings are kept whole (frame 1
*is* the static ad) and later frames contribute only what they got wrong,
tagged by slide. A finding frame 1 already carries is dropped rather than
repeated once per frame, or one note about the type hierarchy becomes five and
buries the one that is about slide 2. The background pass is composed **once**:
none of the motions offered changes what is behind the ink, and re-composing it
per frame triples the slowest step to produce identical bytes.

That failure is also why slides carry **per-size overrides** (`sizeSlides`,
resolved slide by slide and field by field, the way `copyForSize` already
resolves static copy). Without them a set animates at seven sizes and fails at
the eighth on copy nobody can shorten.

**Which sizes take one is read from the platform config, never decided.** Only
Google's eight banner sizes list `gif`; Amazon's specs here are static at
40-50 KB, Meta converts an uploaded GIF into a video, and Google's three
**responsive-display image assets** are image assets — Google composes its own
headline around those. A size that cannot carry one is **refused by name** on
the panel and in the job's own `animationSkipped`, because a set that came back
with five moving ads out of eight and nothing saying which three or why is the
silence this module exists to avoid. `render.ts` still strips `gif` from the
static raster's format list, and the comment there now says why: `gif` in a
format list means that placement will *also* take an animated file.

**And it never replaces the static file.** Most placements on a buy take the
still one, so a folder holding only GIFs is a set that cannot be trafficked.
The GIF is written beside its sibling with `_animated` on the end.
`AnimatedResult` is deliberately not a `RenderResult` with `format: 'gif'`, and
animations are their own list on the project rather than a row on
`RenderBatch` — a batch is a static delivery pack, and one containing only GIFs
would be read by `deliverProject` as the whole of what was built.

**One animation is one decision and one file, and the zip carries none of
them.** They shipped inside the delivery ZIP under `animated/` for exactly one
release, and that was wrong for a reason worth writing down: **a zip is one
act.** It is built once, downloaded once, and every file in it goes out on the
strength of the same press. An animation is not delivered on that press — each
is watched, approved and sent on its own, because somebody who likes the
728x90 may want the 300x250's second slide rewritten. Bundled, an animation
nobody had watched went to a client inside a package somebody approved the
*static set* of, which is the whole distinction the approval draws.

So `deliverProject` **names** them and encloses none: the README says they are
not in the zip and which have been approved, and the machine manifest carries
`inThisZip: false` rather than a path an ops person will not find in the
folder. Silence would be worse than either — a client shown a moving version
who opens a package without one needs the package to account for it.

**The approval is now the only gate between a clipped second slide and a
client's library**, since the zip is no longer what withholds a QA failure. So
`approveAnimation` refuses a failing row **by name** rather than quietly doing
nothing, and `hub/ad_builder_link.approved_animations()` refuses it a second
time — a gate enforced in one place is a gate that moves the day somebody adds
a second door. A sign-off is about the file as it was, so re-animating a size
retires its approval and carries `previouslyApprovedAt`: *"approved on the 3rd,
and rebuilt since"* and *"nobody has looked at this"* are different things to
tell somebody.

**Approving is what sends it, and that is three writes reported apart.** The
renderer records the decision, uploads the file, and the Hub files it onto the
client's record; "approved", "approved and stored" and "on their record" are
different outcomes and one tick for all three is how somebody learns not to
trust the tick — `hub/domain_links.py`'s rule. The decision **survives** a
failed upload and says so, because a Cloudinary that would not answer must not
cost somebody the judgement they made; pressing approve again retries only the
half that did not happen, and an already-stored file is never re-uploaded.
**Who approved comes from the proxy's `X-S1-User` header and never the request
body** — a name a browser can put in a POST is a name anybody can put in a
POST, and it is the entire content of the record.

**And the panel went on describing the delivery it had stopped being part
of.** The build screen's own success message said the files *"are written
beside the static ones and go into the delivery ZIP under `animated/`"* — true
for exactly one release, and nothing corrected it the day approving one became
how it reaches the client. Both halves stayed internally consistent, which is
why it survived: the deliverer really does withhold them and the panel really
does build them, so an operator built eight moving ads and waited for a folder
that was never going to exist. The wording about a failing size was the same
mistake one clause on — *"will not be delivered"* describes a zip that is now
all-static either way; what is actually true is that it **cannot be approved,
and approving is the only thing that sends one**. `test_display_ads.py` asserts
it from **both ends**, because either alone reads as fine.

**Three waits arrived on the one screen this file had already fixed for
having none.** The note above about `bgBusy(what, kind)` was written because
the Display Ad Builder's build screen made three billed calls behind a
sentence of text that did not change. The animation panel then added three
more — encoding a real GIF to preview it, running the job, and the Cloudinary
upload behind Approve — and each said a word in plain text, because `bgBusy`
was **hardwired to the background panel** and nothing generalised it. A helper
that only one panel can reach is how the next panel writes its own.

`waitIn()` and `waitBtn()` are the one reading of *put the mark here*, and
`bgBusy` delegates to the first rather than keeping the copy it had. **Two of
them because there are two targets**, which `hub-thinking.js` already draws
differently: a box gets the glyph and the elapsed line, a button keeps its
width and its **original label** — the failure that helper's own note names,
where a hand-written swap loses the label and re-enables the button in
whichever of the two exit paths the author remembered. That was live here:
`animApprove` ended by redrawing the row from the server, and a fetch that
failed there never rewrote it, leaving the button disabled reading
*"Sending…"* with nothing coming. The handle is ended before the redraw now
rather than by it. The preview is the opposite case and is left to
`isConnected`: five returns each write over the stage, and requiring every one
of them to remember a `.done()` is how one forgets.

**The upload is its own Cloudinary call, and that is not tidiness.**
`uploadCreative` passes `quality: 100`, which is an incoming transformation,
and **any** re-encode of a GIF rewrites its frame delays and its loop block —
the two numbers the compliance check measured. The stored file would still
play, still look right, and no longer have the properties that were verified.
`uploadAnimation` passes nothing but the destination.

**And they reach the client's gallery under their own kind.** `finished_ads()`
reads `project["batches"]`, which animations are deliberately not in, so
without `attach_animations()` an animated ad delivered to a client would be
invisible on that client's own record — the failure this file has already
counted six times, one tool later. `filing.KIND_LABELS` declares `animated_ad`,
because a kind nothing names arrives in a gallery as a bare key under no
heading, and it is filed in the same change that declares it: a label declared
and written by nothing is the `io_creative` failure.

**And the check for that only ran in one direction.** `test_image_audit.py`
has always required every provider `PRODUCERS` *declares* to have a heading —
which catches a label somebody forgot to write, and cannot catch a value
somebody forgot to declare. Those are different failures and only the second
is silent: the file is filed, every count on every screen stays correct, and
the gallery draws it under a bare key. It had already happened twice.
`social_request` was found by somebody opening a client's gallery and is
recorded in that test as one assertion about one string; `animated_ad` arrived
the same way one release later. A list of the two we fixed proves nothing
about the third, so `image_audit.undeclared_providers()` asks every producer
module instead, through the **AST** — prose naming a provider is not a call
site, the rule `hub/config.py`'s drift check gives. It resolves a literal and
a module-level constant holding one (which is how `ANIMATION_KIND` is actually
written) and **names a runtime value as unknowable rather than guessing at
it**, the answer `tools/linkcheck.py` gives about a concatenated URL. It
started green, which is the only way it was worth adding.

**The weight ladder is `raster.ts`'s in GIF terms, and it is cheap for a
reason worth knowing.** A GIF has no quality setting: it has a palette, a
dither, and how different two frames must be before the second re-encodes a
pixel. Both motions offered leave the background **byte-identical** between
frames, so the encoder only ever pays for the words or the button — measured
against this repo's own hero photo, a three-slide 300x250 is 19 KB and a
970x250 is 27 KB, against a 150 KB ceiling. `test_display_ads.py` asserts all
of it, including that Amazon and Meta are offered none at any size.

**And every adjustment made on one size was pasted onto the other ten as raw
pixels.** `styleOverrides` is per **concept** — that is deliberate and right,
because a font, a weight and a brand colour mean the same thing on every
canvas — and it also carries geometry: the CTA's x and y, a block's width, the
type size, the logo's box. Those are pixels, and a pixel authored against a
300x250 means nothing on a 1080x1920. `applyBlockStyles` clamped them to the
target canvas, which stopped anything rendering off-frame and is exactly what
made it invisible: a clamp produces a plausible ad rather than a broken one.
Measured against T01 on this repo's own templates, one ordinary tuning pass —
nudge the button, size the headline, scale the logo — did this:

    728x90     the logo went from 46px tall to 8px, which is `MIN_LOGO`, the
               floor block-style.ts's own constant calls a smudge
    970x250    headline type from [32,44] to [18,18], on a billboard
    1080x1920  the button from y=1118 to y=200 — out of the end card and into
               the middle of the hero photograph

Nothing errored, every size passed its platform's minimum type size, and each
one was internally consistent, which is why it survived: you have to open the
other ten to see it. The operator perfects the first ad and ships ten they
never looked at.

**A departure carries; a pixel does not.** `modules/ad_builder/src/carry.ts`.
Every size already has a hand-authored layout, so an override is not a position
on a canvas — it is a **departure from that canvas's own template**, and what
travels is the departure, in the target's own terms. A dimension carries as a
**ratio** to the template's own, so *"the headline is a tenth bigger than
default"* survives onto a billboard whose default is already twice the size. A
position carries as a **fraction of the frame**, added to the target's own
template position, so nudging the button a quarter of the way across a 300-wide
ad moves it a quarter of the way on every other — rather than abandoning the
composition its own layout put it in. `styleForSize()` is the one reading, and
every render path asks it, so the preview and the delivered file cannot
disagree about what was approved on screen — `copyForSize` one field over.

**The first draft of this scaled everything by the smaller axis ratio, which is
`modules/magic_resize/engine.py`'s rule, and it was a second wrong answer
replacing the first.** That is right *there*: Magic Resize takes one design
into empty frames with no target layout to depart from. Here 300x250 → 728x90
is a factor of 0.36, so it drove the headline to the 8px floor and put the logo
straight back at the smudge — measured, then changed. The two tools share the
idea and deliberately not the arithmetic. Written down rather than left to be
re-derived, because the tempting next edit is to "unify the engine":
`test_display_ads.py` asserts the difference in both directions, so a change to
either rule reports that this note has gone stale rather than letting somebody
assume a shared engine that is not there.

**Trying is half of it; the other half is saying which ones to look at.** The
QA pass already knows how to find a bad layout — collision is a fail, safe-area
and overflow and hierarchy are warnings — so the carry does not re-detect any
of that. What it adds is the fact those checks cannot know: *where this size's
design came from*. `carriedInto()` reports it, a `carry` finding names the
source size, and a size whose departure **would not fit** is marked, because
`applyBlockStyles`' clamp is right and is silent — where it bites, the ad on
screen is not the adjustment that was asked for. It never fails: carrying is
the feature, and a check that fires on every size in a set is one people stop
reading, which is the note `QR_CODE_RULES` already carries.

**The mark leads somewhere, which is the part that makes it worth having.**
`bySize` is a correction authored against the size it names, merged last and
carried nowhere — so the operator opens the one the carry could not place,
nudges it, and the rest of the set is untouched. Without it the flag is a
signpost: the correction would propagate straight back out and the size that
had just been got right would be the next one broken. Same shape as
`concept.copy`, because it is the same question one field over.

**Two rules keep it from being worse than the bug.** A concept with **no
`authoredFor`** is carried nowhere and resolves exactly as it always did —
every ad saved before this exists carries pixels with no record of the canvas
they were drawn against, and guessing one would rewrite creative that has been
approved and delivered; the `backgroundPosition` precedent, superseded and kept
and read only when the newer field is absent. And **geometry with no frame to
come from is dropped, never pasted** — a family switched after tuning leaves
nothing to re-anchor against, so the target's own composition stands and the
report says so, since keeping the pixels silently would be the defect wearing a
fix.

**The browser resolves none of it.** The server returns the resolved style and
the carry report with the preview, and `POST /api/carry` answers for the whole
set at once — it renders nothing and reaches no provider, so the review count
is right the moment something is tuned rather than filling in one size at a
time as previews come back, which is a count nobody can trust. A second reading
of the rule in JavaScript is the mirror this file counts the cost of twice.
`test_display_ads.py` sweeps the **call sites** rather than naming the four
that were wrong, so the fifth render path added next month cannot paste a pixel
again, and `tests/carry.test.ts` drives the rule itself — both halves confirmed
red against the real defect first, and one assertion in the first draft could
not fail at all, because it checked that type stayed above `MIN_TYPE` and
`applyBlockStyles` guarantees exactly that.

**The operator's list, September 2026: what each item turned out to be.**
Todd sent one message with seventeen things wrong with the build screen, and
most of them were one of three underlying defects rather than seventeen.

*"I can't change the text color on any size after the first one"* was
`svg.ts`: over a full-bleed photo the composer replaced every block's ink
with whichever of light/dark survives the overlay, including an ink somebody
had chosen. The first size tuned was usually a flat layout (the colour took)
and the next a photo (it did not), with the panel showing the colour it was
not drawing. `applyBlockStyles` now marks a chosen ink `keepColorOnBg`, the
flag the composer already honoured for templates; the automatic ink stays
for blocks nobody coloured, and the contrast check still says when a chosen
one does not read.

*"Changing layouts after the first image does not work"* was that
`layoutFamily` is per concept, so changing it on the 728x90 changed the
300x250 somebody had just finished. `layoutBySize` names a family for one
canvas, `registry.familyFor()` is the one reading, and every render path,
the fingerprint and the validator ask it. The carry reads the departure
against the authored size's own family (`carry.styleFor()` pairs the two
templates); `test_display_ads.py` sweeps `render.ts` for any path still
reading `getTemplate(concept.layoutFamily)`.

*"The checks should be in plain English... use AI"* is `plain-checks.ts`,
two layers on purpose. `explainFinding()` is deterministic and always there:
it knows every check and turns "below 4.5:1 — headline 2.1:1" into "The
headline is hard to read against what is behind it" plus the control that
fixes it, with an `apply` the screen can do and undo where the fix is one
setting. `adviseFindings()` hands that reading to the model as a floor and
asks for the sentence a senior designer would say about THIS ad, in a
vocabulary of four change shapes the screen can perform -- anything else is
dropped rather than passed on as a button that does nothing, and the model
may reword a finding but never add one. It runs on a press and on the way to
the next size ("would you like to see what this would look like?" -- Yes
shows it and waits for keep or undo, No saves and continues), never per
keystroke, because it is billed. With no key the panel is the deterministic
reading and says so.

The rest were what they said. Type is capped at 200px rather than 96 (the
old cap trimmed a story headline silently). Every line of type moves up and
down (`y` on every block; `x` stays the button's, because across is the
layout's decision). Arrow pads share one moving speed, slow/medium/fast at
1/5/10px, medium by default; the background pad takes fractions under the
same names because its offset is a fraction of the picture's overflow. A
colour well waits for **Use**, and **Use and save** keeps the hex on
`brand.savedColors` once, as a chip on every colour control. There is an
undo stack of campaign snapshots, coalesced for typing. Suggest crop renders
the suggestion from a copy of the campaign before Use it. Animation is
refused for Meta in `animationSupport` by platform name and not only by
format list, so a Meta config that grows `gif` still refuses, and the rail
wears a blue M on every size Meta buys. The left column is six accordions in
the operator's order -- Layout, Copy, Background, Type/Fonts, Text Boxes,
Logo -- one open at a time, and the "Advanced: Layout" fold that a
MutationObserver used to wrap around the layout cards after every redraw is
gone. The five buttons that stood in the toolbar and again under Save now
stand once, in an always-visible action row with a description each: Render,
Animate, Save as preset, Duplicate as the next concept, then the blue Next.
The brief is a form on the overview page for staff (`POST
/api/campaign/<id>/brief` lands it on the submission, the campaign file and
the project, through `saveCampaignDocument` so an approved size cannot be
moved by it, and drops the cached landing analysis when the page changes).
Logos pull from the client's gallery through `/_hub/logos`, which opens on a
folder called "logos" when there is one. A dark logo on a dark backdrop gets
a white or black version made from the primary (`makeMono`, the shape
untouched) rather than a palette move, and the logo-contrast check suggests
which.

**Google Fonts, and the fifteen that cannot be drawn.** The registry is the
Google families a brand is likely to use, vendored through `@fontsource`, and
`fonts.ts` probes every file at load because opentype.js cannot parse every
Google family: Roboto, Inter, DM Sans, Nunito, Oswald, Rubik, Source Sans 3,
Archivo, Lora, Merriweather, Karla, Mulish, Nunito Sans, Roboto Condensed and
Roboto Slab all carry a GSUB chained-context lookup (type 6, format 2) it does
not implement and throw on the first glyph -- the DejaVu failure the file
already recorded, fifteen times over. A family that throws is left off rather
than offered and swapped for Poppins mid-render; `tests/fonts-google.test.ts`
asserts the named ones are absent and every offered weight draws. The probe
costs about 350ms once, on first use, and keeps nothing parsed.

**The second list, the same week: fewer questions, one next button.** With
the first list live, the remaining friction was mostly questions the screen
asked that it already knew the answer to. The copy fields asked "every size
or just this one?" on the first keystroke of each of five fields; the answer
is "every size" and the toggle under the field is there for the tenth time.
Switching size asked "save first?" while autosave was two seconds from
writing anyway; a click saves and goes, and the only dialog left is for a
save that failed, which is the one case where leaving costs something.
Render asked what to do and then which sizes; the usual answer is one button
and the rest is under "More options". The approval tick in the rail was the
control most people never found, so there is a **Done with this size** button
under the checks: it saves, waits for the preview to settle (approval reads
the saved artwork), asks the advice layer about anything open, approves when
clean or when the warnings are accepted, and opens the next unapproved size.
A **start-here strip** shows the six sections as steps with a tick once each
has been looked at -- known from the campaign where it can be (copy, a real
logo) and from "opened this sitting" where it cannot.

Two things were quietly wrong. Meta's text-coverage guideline fired as a
warning on nearly every Meta ad, which put amber on every one and demanded
an acknowledgement at every approval -- the state the word-count check was
removed for. `QaFinding.status` has `info` now: a note, drawn grey, never a
verdict (`rollUp` ignores it), never the lead of the advice. And the advice
suggested "try the light brand color" without knowing whether the brand's
light reads on this background: the contrast check records the luminance it
measured behind each low block on the finding's `data`, and
`inkSuggestion()` picks whichever brand ink clears 4.5:1 against it, saying
"neither reads, darken the overlay" when neither does, and labelling a guess
as one when there is no measurement to go on.

The rest: the AI wait on the way to the next size carries the mark and a
"Skip, just continue"; a new logo resets every concept's tone to full colour
(the white and black files were the old mark's); the live preview is drawn
at no more than 900px on its longer edge (`PREVIEW_MAX_EDGE`, a lower SVG
density rather than a resize, with the background pass and every QA sample
still at delivery scale) so a story is not two megabytes of base64 per
keystroke; `/diagnostics` names any known font family that failed to load;
and a build screen that comes back into view compares the brief with the
server's and offers a reload when the overview page changed it.

**And a test that clicks.** `tests-browser/build.e2e.ts` starts the renderer
against a copy of the sample campaign, opens the build screen in a headless
Chromium through `puppeteer-core`, and walks the first minute of a build:
the six sections, a copy edit with no dialog, Undo, a colour Use and save,
an arrow at Fast, a switch of size with no dialog, the Done button -- and
fails on any page error, console error or 5xx. It is its own npm script
(`test:browser`), not part of `npm test`, because it needs a browser: CI
runs it after the HyperFrames render service's `npm ci` has downloaded
Chrome and borrows that binary, and without one it is **skipped by name**
rather than passed.

**The third list, the seven after that.** Offered as "more ideas" once the
second list shipped, and taken as a set.

*Use this size's look on every size.* `styleOverrides` carries departures
from one authored size to the rest, and a size somebody corrected by hand
keeps its correction (`bySize`). What was missing was the reverse: having
tuned the 728x90 until it was right, making it the size the rest are carried
from. `carry.adoptLook()` is that: the size's resolved style (`styleFor`,
which is already in its own pixels) marked `authoredFor` it, every other
size's correction dropped, and the size's own layout pick promoted to the
set's family when that family draws every size in the set -- otherwise the
rail would lose the sizes it cannot draw, so the family stays per size and
the report names them. `POST /api/concept/adopt-look` resolves it on the
campaign the screen is holding rather than mirroring the carry in the
browser; the screen asks before replacing another size's hand work and Undo
takes it back. Offered in Text Boxes once there is a look to adopt.

*The client's notes, one ad at a time.* The proof page had one box,
"Request changes", and it closes the proof. Looking through eleven sizes,
people notice things one at a time, and a single box at the bottom loses
which ad each remark was about. Every ad on the client proof has a note box
now (`POST /client-proof/<token>/comment`, public through the Hub proxy like
the decision); a note is not a decision, so the proof stays open for the
approve or change request that follows, and it is refused once the version
is approved. Notes live on the proof record, are written to the project's
notes, come back through `/api/project/<id>/workflow`, and the build screen
draws them under that size's checks with a mark on the rail.

*Hold to see before.* The first preview of a size in a sitting is held as
its "before", and a save moves it forward; a chip on the stage swaps the
picture while it is held and swaps back on release. A hold rather than a
toggle so nobody leaves it on and edits the wrong picture.

*Presets on the start form.* "Save as preset" existed and the only way to
use one was the presets page. The Hub's start form lists the renderer's
presets for the chosen client (`saved_presets`, exact client match), draws
the preset's own slots as the form, puts the brief fields away, and
`start_from_preset` calls the renderer's generate route -- refusing a preset
saved under a different account rather than filing the ad against the wrong
record.

*The arrow keys.* With a pad button focused the keys move that pad;
anywhere else that is not a text field they move the last thing nudged.
Shift is a fast press and Alt a slow one, whatever the speed buttons say.

*A health line per campaign.* `health.ts` writes one sentence per project
for the projects list: approvals against the latest review's per-size
verdicts, failures, where the client proof stands and how many notes the
client left -- reading the reviews and proofs directories once each
(`latestReviews`, `clientProofsByProject`) rather than once per row. Absent
data is named ("No review yet"), never shown as a zero.

*A nightly run through the Hub.* `.github/workflows/nightly-browser.yml`
boots the renderer and the Hub the way the container does, signs in through
the real login form, and runs `build.e2e.ts` against
`/tools/display-ads/build` -- the login, the proxy adding the token, the
base-path shim and HubBar's injection are all in the path, and each has
broken the screen alone while every test stayed green. The test takes
`E2E_BASE_URL`, `E2E_HUB_PASSWORD`, `E2E_REQUEST` and `E2E_TOKEN` for that;
with none set it starts its own renderer as before. A second job runs the
same against a staging Hub once the repository variables
`STAGING_HUB_URL` and `STAGING_E2E_REQUEST` and the secret
`STAGING_HUB_PASSWORD` exist; there is no staging service today, so it does
nothing until then, and it must never be pointed at production because the
test edits the campaign it opens.

**The fourth round: the failure paths.** With the features in, "bulletproof"
meant what the screen does when something under it fails, and three of those
failures happen on ordinary days. Every merge restarts the container, so a
person mid-edit meets a renderer that is not there for a minute or two; a Hub
session ends; a connection drops. All three surfaced as "could not save" and
"preview unavailable" with nothing saying why.

*The link, read once.* A fetch wrapper at the top of `build.html` reads every
API answer before any caller sees it. A redirect to `/login` is a sign-out,
named, with the way back in (a link that opens the login in a new tab and
comes back to the same screen); an HTML page with a 5xx on an API route is
the proxy saying the renderer is not answering, named, with what happens to
the edits (kept on this device, and saving retries by itself). The notice
clears on the next answer that is what it should be. The standalone renderer
never redirects and never speaks HTML on an API route, so outside the Hub the
wrapper does nothing.

*Saving and previewing recover on their own.* A blocked autosave retries at
a widening interval (10s, 20s, 40s, 60s) and the hint says when the next try
is -- except after a conflict with somebody else's save or a sign-out, which
a person has to settle. A failed preview retries once, quietly, before saying
anything; the second failure carries a "Try the preview again" button.

*The public proof routes have a ceiling.* Whoever holds a proof link can
call the note and decision routes with no token, and a budget keyed on the
exact path would have given every link a fresh allowance. `auth.budgetKey()`
replaces the token or project id in the path with a placeholder, so one
budget covers every proof: 30 notes and 20 decisions an hour per address on
the client proof, 20 on the older `/api/proof` pair. A body that is not JSON
answers 400 rather than a logged 500, and a note that did not send says so
under the button rather than in a dialog.

*The nightly walk goes further.* The seeded campaign has the project record
every real build has (`seed.ts`), so the browser test can play both outages
through request interception -- one 503 HTML answer on a preview, one
redirect on a save -- and assert the notice, the quiet retry and the
clearing. With `E2E_FULL` set (the nightly jobs), it then builds the contact
sheet on `/review` and waits for every size, reads the health line on
`/projects`, and behind a Hub loads the start form.
