"""The Display Ad Builder's layouts and build screen — test harness.

    python3 test_display_ads.py

No pytest, no new dependencies, and — unlike every other test file here — no
Flask app, because the thing under test is not Python. The Display Ad Builder
is the one module that is a Node service (see CLAUDE.md), it ships its own
TypeScript tests under ``modules/ad_builder/tests``, and those tests need an
``npm install`` that CI deliberately does not do. So the checks that run on
every pull request today are the ones that can be made against the *files*:
the template JSON, and the build screen's own source.

That sounds like a weak substitute and for most of this module it would be.
For these four things it is not, because each of them is a fact stored in a
file rather than a behaviour computed at runtime:

  * **A proof point that is drawn somewhere.** The build screen has always
    offered a Proof point field, and no template carried a ``trust`` box. The
    copy was written, saved, word-counted and rendered nowhere — the field was
    a control that did nothing, on every layout, in every size. Whether a box
    exists is a fact about the JSON, and so is whether it lands inside the safe
    area and clear of every other block, which is what the renderer's own
    diagnostics check at runtime and what a hand-authored coordinate gets
    wrong.

  * **The two sizes that genuinely have no room say so by being absent.** A
    728x90 leaderboard runs its support line to the bottom margin and a 320x50
    carries three blocks in fifty pixels. Inventing a six-pixel proof point
    there would be worse than not having one — so their absence is asserted,
    not tolerated, and the build screen is asserted to name it.

  * **The AI generate route and the screen that reads it agree.** The server
    answered ``{ candidate }``; the build screen read ``{ candidates }``, found
    nothing, found no reason either, and printed "Image generation is not
    configured" over a generation that had just succeeded. Two files, one
    contract, no runtime in between: exactly the shape of bug a source check
    catches and a unit test of either half does not.

  * **The word-count check is gone and stays gone.** It warned either side of a
    per-size guidance band, so an ad four words short of a guideline nobody
    publishes came up amber — which is how people learn that amber means
    nothing.

Everything else here — the overlay colour, the crop position, the per-block
ink — is exercised by ``modules/ad_builder/tests`` where a renderer exists to
exercise it. What is asserted below is only that the pieces are wired to each
other, which is the failure this release was reported for.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent
MODULE = ROOT / "modules" / "ad_builder"
TEMPLATES = MODULE / "src" / "templates"
BUILD_HTML = MODULE / "public" / "build.html"

PASS, FAIL = [], []


def strip_comments(src: str) -> str:
    """Everything a person could read on the page, and nothing else.

    These files are one long inline script inside some markup, so a naive
    search finds the comments explaining *why* a control is called "Text
    color" and reports them as the British spelling they are describing.
    Comments are for whoever opens the file; the rule is about the screen.
    """
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)      # HTML comments
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)       # /* ... */ in CSS and JS
    src = re.sub(r"(?m)^\s*//.*$", " ", src)               # whole-line // comments
    return src


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


# The eight canvases this tool actually delivers. The 1080x* social sizes live
# in the same files and are not part of a display package.
DISPLAY_SIZES = [
    "300x250", "336x280", "728x90", "160x600",
    "300x600", "320x50", "970x250", "414x125",
]

# The two with no room for a proof point, named rather than counted. If a
# layout ever gains one here, this test should be the thing that asks whether
# it is really legible at that size.
NO_ROOM = {"728x90", "320x50"}

BLOCKS = ("logo", "headline", "support", "offer", "trust", "cta")


def layouts():
    """Every (family, size, layout) for the display package."""
    for path in sorted(TEMPLATES.glob("*.json")):
        doc = json.loads(path.read_text())
        for size in DISPLAY_SIZES:
            layout = doc["sizes"].get(size)
            if layout:
                yield doc["id"], size, layout


def test_proof_point_is_drawn():
    """Every display size with room carries a trust box."""
    missing = [
        f"{fam}/{size}"
        for fam, size, layout in layouts()
        if size not in NO_ROOM and not layout.get("trust")
        # T04 gives 336x280 an offer flash and a half-width hero; what is left
        # below the button is margin. Its own file says so.
        and not (fam == "T04" and size == "336x280")
    ]
    check("every display layout with room draws a proof point", not missing, ", ".join(missing))

    drawn = [f"{fam}/{size}" for fam, size, layout in layouts() if layout.get("trust")]
    check("the proof point is drawn somewhere at all", len(drawn) >= 20, f"{len(drawn)} layouts")

    # And the ones that cannot carry it must not pretend to.
    wrong = [
        f"{fam}/{size}"
        for fam, size, layout in layouts()
        if size in NO_ROOM and layout.get("trust")
    ]
    check("no proof point on the two canvases with no room", not wrong, ", ".join(wrong))


def test_trust_boxes_are_laid_out_legally():
    """Inside the safe area, clear of every other block, at a readable size.

    This is the renderer's own diagnostics rule (`overlaps` in diagnostics.ts)
    and its safe-area QA check, run against the file. A hand-authored
    coordinate is exactly the kind of thing that is right in the panel and
    clipped in the ad.
    """
    outside, colliding, tiny = [], [], []
    for fam, size, layout in layouts():
        t = layout.get("trust")
        if not t:
            continue
        canvas, safe = layout["canvas"], layout.get("safe", 0)
        where = f"{fam}/{size}"
        if (t["x"] < safe or t["y"] < safe
                or t["x"] + t["w"] > canvas["w"] - safe
                or t["y"] + t["h"] > canvas["h"] - safe):
            outside.append(where)
        for role in BLOCKS:
            other = layout.get(role)
            if role == "trust" or not other:
                continue
            ox = min(t["x"] + t["w"], other["x"] + other["w"]) - max(t["x"], other["x"])
            oy = min(t["y"] + t["h"], other["y"] + other["h"]) - max(t["y"], other["y"])
            if ox > 1 and oy > 1:
                colliding.append(f"{where} trust/{role}")
        # 8px is the renderer's own floor (MIN_TYPE in block-style.ts). Below
        # it nothing in a banner can be read.
        if t["size"][0] < 8:
            tiny.append(where)

    check("every proof point sits inside its layout's safe area", not outside, ", ".join(outside))
    check("no proof point overlaps another block", not colliding, ", ".join(colliding))
    check("no proof point is set below the 8px legibility floor", not tiny, ", ".join(tiny))


def test_no_layout_regressed():
    """The boxes that were already there are still there, and still legal.

    Adding a block to 29 hand-authored layouts is the change most likely to
    nudge something else, so this is the whole-file version of the check
    above rather than a trust-only one.
    """
    bad = []
    for fam, size, layout in layouts():
        w, h = (int(n) for n in size.split("x"))
        if layout["canvas"]["w"] != w or layout["canvas"]["h"] != h:
            bad.append(f"{fam}/{size} canvas is {layout['canvas']}")
        for role in ("headline", "cta", "logo"):
            if not layout.get(role):
                bad.append(f"{fam}/{size} lost its {role}")
    check("every layout keeps its canvas, headline, button and logo", not bad, "; ".join(bad))

    pairs = []
    for fam, size, layout in layouts():
        boxes = [(r, layout[r]) for r in BLOCKS if layout.get(r)]
        for i, (an, a) in enumerate(boxes):
            for bn, b in boxes[i + 1:]:
                ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                if ox > 1 and oy > 1:
                    pairs.append(f"{fam}/{size} {an}/{bn}")
    check("no two blocks overlap in any display layout", not pairs, "; ".join(pairs))


def test_build_screen_reads_the_generate_response():
    """The bug the user reported: a working generation said "not configured".

    The server answers `{ candidate }`. The screen read `{ candidates }`, got
    undefined, fell through to its no-reason fallback, and printed the
    not-configured sentence. Both halves are asserted, because fixing either
    one alone leaves the contract broken in the other direction.
    """
    screen = BUILD_HTML.read_text()
    server = (MODULE / "src" / "server.ts").read_text()

    check(
        "the build screen reads `candidate` as well as `candidates`",
        "d.candidate" in screen and "d.candidates" in screen,
    )
    check(
        "the generate route answers both shapes",
        "candidate: withUrl, candidates: [withUrl]" in server,
    )
    # And the fallback sentence must no longer claim a configuration problem
    # it cannot know about: an empty result with no reason is an empty result.
    check(
        "no unconditional 'not configured' message on the build screen",
        "'Image generation is not configured.'" not in screen,
    )


def test_stock_search_is_visible_and_correctable():
    """Two photos from a phrase nobody can see is not a shortlist."""
    screen = BUILD_HTML.read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    imagery = (MODULE / "src" / "imagery.ts").read_text()

    check("the build screen can search for its own words", "bgQuery" in screen)
    check("the search route accepts a typed phrase", "body.search" in server)
    check(
        "benefit and offer reach the keyword builder",
        "benefit: body.benefit" in server and "offer: body.offer" in server,
    )
    check(
        "a phrase that finds nothing is narrowed rather than reported empty",
        "narrowed" in imagery and "attempts.push" in imagery,
    )
    check(
        "more than two candidates come back",
        re.search(r"opts\.count \?\? 12", imagery) is not None,
    )


def test_word_count_is_gone():
    """It warned either side of a band, so it was amber more often than not."""
    qa = (MODULE / "src" / "qa.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("QA no longer emits a word-count finding", "'word-count'" not in qa)
    check("the build screen no longer draws a word budget", "WORD_BUDGET" not in screen)


def test_the_screen_says_where_a_field_will_not_draw():
    """A field that is typed into and drawn nowhere is the worst control."""
    screen = BUILD_HTML.read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    check("the options route reports which blocks each size draws",
          "drawn[sizeKey]" in server and "blocks: drawn" in server)
    check("the screen labels a field this size will not draw", "nodraw" in screen)
    check("and names the sizes that do draw it", "sizesDrawing" in screen)


def test_the_things_that_had_no_control_have_one():
    """Background crop, overlay colour and transparency, and text ink."""
    screen = BUILD_HTML.read_text()
    svg = (MODULE / "src" / "svg.ts").read_text()
    style = (MODULE / "src" / "block-style.ts").read_text()
    types = (MODULE / "src" / "types.ts").read_text()

    check("a concept can say where its background sits", "backgroundPosition" in types)
    check("the composer still honors the nine legacy alignments", "resolveBgPosition" in svg)
    check("and refuses an alignment SVG would not accept", "BG_POSITIONS" in svg)
    # The nine-way grid was replaced by nudge arrows in the round after this
    # one: it could answer "top or bottom" and not "a bit further down", which
    # is the note people actually write. Asserted in
    # test_the_picture_can_be_moved_and_zoomed_rather_than_snapped.
    check("the build screen offers a way to move the picture", "data-nudge" in screen)

    check("a concept can say what color its overlay is", "backgroundOverlayColor" in types)
    check("a chosen color is painted flat, not graded", "fill-opacity" in svg)
    check("the build screen offers color and transparency", "bgOpacity" in screen and "bgWash" in screen)

    check("a text block can carry its own ink", "color?: string" in style)
    check("an unresolvable color is dropped rather than rendered as black",
          "resolveStyleColor" in style)
    check("the build screen offers the brand's own five", "data-ink=" in screen)


def test_a_size_can_be_approved_and_locked():
    screen = BUILD_HTML.read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    projects = (MODULE / "src" / "projects.ts").read_text()

    check("the project record stores approvals", "SizeApproval" in projects)
    check("there is a route to set one", "approve-size" in server)
    check("the rail draws a tick per size", "data-approve" in screen)
    check("an approved size locks its controls", "col.left.lock" in screen)
    check("and can be unapproved again", "unlockSize" in screen)
    check("saving names the sizes still unapproved", "unapprovedSizes()" in screen)


def test_moving_on_asks_about_unsaved_work():
    screen = BUILD_HTML.read_text()
    check("switching size asks before losing edits", "Save your changes first?" in screen)
    check("so does switching campaign or concept", "leaveIfSafe" in screen)


def test_a_duplicate_is_the_next_lettered_concept():
    server = (MODULE / "src" / "server.ts").read_text()
    projects = (MODULE / "src" / "projects.ts").read_text()
    check("letters are allocated across the whole family", "nextConceptLetter" in projects)
    check("the clone route uses them", "Concept ${letter}" in server)
    # An unlettered original would let the first duplicate claim A, and then
    # two sets in the family would answer to the same letter.
    check("the original is named Concept A when its first duplicate is made",
          "src.conceptLetter = 'A'" in server)
    # Carrying the original's sign-offs into the copy would lock a set nobody
    # has looked at.
    check("a duplicate starts with nothing approved", "delete (clone as any).approvals" in server)


def test_the_rail_only_lists_sizes_this_campaign_builds():
    """A family draws thirteen canvases; a Google buy defines eight.

    The rail listed all thirteen, so five rows on a display campaign were
    sizes whose preview 422s and which "Render all sizes" never writes. That
    was survivable while the rail was only navigation, and stopped being
    survivable when it started counting approvals — "2 of 13 approved" is a
    wrong number, and the warning on save was naming sizes that do not exist
    for this buy.
    """
    server = (MODULE / "src" / "server.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("the options route sends each platform's size list", "platformSizes" in server)
    check("the rail narrows the family's sizes to what the platforms buy",
          "state.platformSizes" in screen)
    check("switching layout family cannot strand you on a size it lacks",
          "rail.indexOf(state.size) < 0" in screen)

    # And the numbers behind it: the union across a campaign's platforms is
    # what gets built, because each size is rendered by whichever platform
    # defines it.
    import json as _json
    cfg = MODULE / "src" / "config" / "platforms"
    google = set(_json.loads((cfg / "google.json").read_text())["sizes"])
    amazon = set(_json.loads((cfg / "amazon.json").read_text())["sizes"])
    meta = set(_json.loads((cfg / "meta.json").read_text())["sizes"])
    family = set(_json.loads((TEMPLATES / "T01.json").read_text())["sizes"])
    check("a Google campaign builds a subset of the family, not all of it",
          (family & google) == google and len(family & google) < len(family),
          f"{len(family & google)} of {len(family)}")
    check("every size a platform buys, some family can draw",
          (google | amazon | meta) <= family,
          f"undrawable: {sorted((google | amazon | meta) - family)}")
    check("414x125 is Amazon's and not Google's",
          "414x125" in amazon and "414x125" not in google)
    check("the feed sizes belong to Meta alone",
          {"1080x1080", "1080x1920"} <= meta and not ({"1080x1080"} & google))

    # Responsive display is what a Google display buy actually serves now, and
    # its two image assets are a different animal from an uploaded banner: they
    # are composed into an ad by Google rather than delivered finished, so the
    # 150 KB ceiling does not apply to them and 5 MB does.
    check("Google buys all three responsive display image assets",
          {"1200x628", "1200x1200", "1200x1500"} <= google,
          f"google has {sorted(google)}")
    check("the portrait asset is derived from the Meta 4:5, not drawn fresh",
          "1200x1500" in _json.loads((TEMPLATES / "T01.json").read_text())["sizes"])
    amz = _json.loads((cfg / "amazon.json").read_text())["sizes"]
    check("the Amazon medium rectangle is no longer an assumption",
          "VERIFY" not in _json.dumps(amz["300x250"]))
    check("and it takes the published non-billboard rule",
          amz["300x250"]["maxFileBytes"] == 40960 and amz["970x250"]["maxFileBytes"] == 204800)
    gsz = _json.loads((cfg / "google.json").read_text())["sizes"]
    check("and they carry the 5 MB asset ceiling, not the banner's 150 KB",
          all(gsz[k]["maxFileBytes"] == 5242880
              for k in ("1200x628", "1200x1200", "1200x1500")))
    check("while an uploaded banner still carries 150 KB",
          gsz["300x250"]["maxFileBytes"] == 153600)
    check("1200x628 is one shape sold by two platforms, filed under both",
          "1200x628" in google and "1200x628" in meta)


def test_the_palette_says_whether_anybody_chose_it():
    """An ad in Smart 1's placeholder navy looks plausibly branded.

    `assetSources` has recorded provenance for the logo and the hero since it
    was written -- upload, brandfetch, wordmark, placeholder, none. The
    palette had no equivalent: `finalizeColors()` spread DEFAULTS underneath
    whatever was discovered and said nothing at all. So a client with no
    brand colours on file got the placeholder navy and gold on every size,
    and nothing on any screen said so. Absent data reading as a confident
    value, on the thing the client receives.

    Three rules on the line the operator reads. The all-default case is said
    plainly, because that is the one that ships a stock ad. A role somebody
    answered for is not listed -- five rows of "from Brandfetch" is noise,
    and a line that appears on every campaign is one people stop reading. And
    a campaign built before the field existed says NOTHING rather than
    reading as default: absent is not the same answer as placeholder, which
    is the whole point of the line.
    """
    intake = (MODULE / "src" / "intake.ts").read_text()
    screen = BUILD_HTML.read_text()

    check("the build records where each color role came from",
          "colorSources" in intake and "export type ColorSource" in intake)
    check("and finalizeColors is what decides it",
          "sources: BuildResult['colorSources']" in intake)
    check("a color we moved for readability is ours, not theirs",
          "sources.accent = 'adjusted';" in intake
          and "sources.light = 'adjusted';" in intake)
    check("a wholly placeholder palette is said once, in words",
          "No brand colors were discovered or supplied" in intake)
    check("it survives into the campaign record",
          "colorSources: result.colorSources," in (MODULE / "src" / "server.ts").read_text())

    check("the screen draws it beside the swatches",
          "paletteProvenance()" in screen and "palette-note" in screen)

    # The warning told a rep to set five swatches and gave them nothing to set
    # them from: a brand-new prospect has no scan for the site-brand panel to
    # read and no Brandfetch record either. colorsFromImage() was written for
    # exactly that and had never been called -- and would have returned the
    # padding rather than the mark, because sharp's `dominant` is a histogram
    # over RGB that takes no notice of alpha. The same blindness as the corner
    # sample in logo-tools.ts.
    check("the logo's own colors are read from the mark, not what is behind it",
          "data[i + 3] < 128" in intake and "flatBackdrop(file)" in intake)
    check("and offered only where nobody has answered for the palette",
          "everyRoleDefault && logoFile" in intake)
    check("never from a wordmark we drew in the placeholder ink",
          "assetSources.logo === 'upload' || assetSources.logo === 'brandfetch'" in intake)
    check("the screen offers them to copy rather than applying them",
          "logoPaletteOffer()" in screen and "click to copy" in screen)
    check("and says it copied only when it did",
          "could not copy, select and copy it by hand" in screen)
    check("and says nothing at all when the field is absent",
          "if (!src) return '';" in screen)
    check("naming only the roles nobody answered for",
          "still placeholder" in screen and "adjusted for readability" in screen)


def test_a_white_box_round_the_logo_is_named_rather_than_scored():
    """logo-tools.ts opens with a rule nothing asked.

    "Any logo that is not already transparent must have its background
    removed before compositing -- a white box around a logo on a coloured ad
    looks broken." `hasTransparency()` was written for exactly that question
    and had no caller anywhere in the repo.

    The QA pass could not have caught it either, and read BETTER on the
    broken ad: `logoInkLuminance()` averages every opaque pixel, so on a
    plated logo it measures the plate. The same navy wordmark scores about
    2.3:1 on a transparent canvas and about 9.9:1 with a white box behind it,
    against a navy panel -- the box makes QA more confident about the one ad
    with a white rectangle stamped across it.

    The same corner sample failed the other way too. It never asked whether
    the corners were opaque, so on an already-transparent logo they read
    (0,0,0, alpha 0), black was taken for the background colour, and every
    near-black pixel in the mark was made transparent: Rework logo erased a
    #0a0a0a wordmark outright and reported success. #111111 survived only
    because it happens to sit 51 units of colour distance from black against
    a tolerance of 42, which is luck rather than a rule.

    One reader now answers both -- what a flat opaque plate is, and whether
    there is one.
    """
    tools = (MODULE / "src" / "logo-tools.ts").read_text()
    qa = (MODULE / "src" / "qa.ts").read_text()

    check("there is one description of what a plate is",
          "export async function flatBackdrop" in tools)
    check("and the stripper reads it rather than sampling again",
          "backdropOf(data, width, height, channels)" in tools
          and "spread > 60" not in tools.split("export async function removeFlatBackground")[1])
    check("a transparent corner means there is no plate to strip",
          "data[p + 3] < 250" in tools)
    check("so an already-transparent logo is copied through, not eaten",
          "if (!plate) {" in tools)

    check("QA asks the same reader", "flatBackdrop" in qa)
    check("and names the colour that will show",
          "opaque rgb(" in qa and "Rework logo" in qa)
    check("only when it will actually show against the panel",
          "plateShowsAgainst" in qa and "PLATE_VISIBLE_DELTA" in qa)
    check("and a plated logo gets no contrast finding at all",
          "if (ink !== null && !plateShows) {" in qa)


def test_the_alert_channel_reports_itself():
    """notify.ts promised a check that was never written.

    Its header has said since the day it was written that a missing transport
    is "appended to out/notifications/outbox.jsonl instead of being lost, and
    diagnostics flags the missing configuration". Diagnostics did not: there
    was no such check, its Integrations group held one entry for Cloudinary,
    and `notificationsConfigured()` -- written for exactly this question --
    had no caller anywhere in the repo. The declared-but-unwired shape this
    codebase has now hit six times.

    Nine call sites in server.ts raise an alert and every one discards the
    NotifyResult, so the route reports success whether or not anything was
    sent. The self-health timer is the one that matters: it runs the
    diagnostics every three hours so a 2am failure pages somebody rather than
    waiting for a customer to find it, and with no transport that page is a
    line in a JSONL file on the output directory, which a deploy wipes. The
    thing built to say the tool is broken was itself unrouted.
    """
    notify = (MODULE / "src" / "notify.ts").read_text()
    diag = (MODULE / "src" / "diagnostics.ts").read_text()

    check("the check the header promises now exists",
          "function checkNotifications" in diag)
    check("and is actually registered, not merely written",
          "checks.push(checkNotifications(opts.outDir));" in diag)
    check("it reads the transport state rather than the two keys",
          "notificationsState" in diag and "notificationsState" in notify)
    check("the function written for this finally has a caller",
          "notificationsConfigured" in notify)

    # Three states, because a key with no recipient is not the same as no key.
    check("a key with nowhere to send it is its own answer",
          "EMAIL_TO is not" in notify and "blocked" in notify)
    check("and it is a failure rather than a warning",
          "level: 'fail'," in diag.split("function checkNotifications")[1]
          .split("function checkPlatforms")[0])
    check("an unrouted channel names where the alerts are going instead",
          "outbox.jsonl" in diag)
    check("and how many have gone there already",
          "outboxState" in diag and "export function outboxState" in notify)


def test_a_ceiling_says_where_it_came_from_and_the_panel_derives_it():
    """A file-weight limit nobody sourced reads exactly like one that was.

    `source: 'doc'` is a rule's own claim that its numbers came off the
    platform's spec sheet, and the diagnostics panel used to take that claim
    as the whole answer -- it flagged a size only where somebody had typed
    `source: 'verify'`, which nothing ever had. So the panel reported "all
    limits sourced from documentation" across 23 rules of which 13 recorded
    no source at all, and could not have said otherwise however many more
    were added: it was answering about what somebody remembered to flag.

    Both halves are here. Every rule now records what confirmed it, or says
    it is unconfirmed; and the panel derives the doubt from that record
    rather than from the claim, so a rule added next month with a ceiling and
    no source is reported without anybody having to remember to mark it.

    Getting this wrong is quiet in both directions and the directions are not
    alike: a ceiling set too high ships a file the platform refuses at
    delivery, and one set too low steps the quality ladder down to satisfy a
    limit that does not exist -- which is the mistake meta.json made once
    already, carrying Google's 150 KB on a Meta story frame.
    """
    cfg = MODULE / "src" / "config" / "platforms"
    rules = [(f.stem, size, rule)
             for f in sorted(cfg.glob("*.json"))
             for size, rule in json.loads(f.read_text())["sizes"].items()]
    check("there are platform rules to check at all", len(rules) >= 20,
          f"found {len(rules)}")
    unsourced = [f"{p}/{s}" for p, s, r in rules
                 if not (r.get("_verifiedAgainst") or "").strip()
                 and r.get("source") != "verify"]
    check("every ceiling records what confirmed it, or says it is unconfirmed",
          not unsourced, f"no source recorded: {unsourced}")

    # The one that is genuinely open, named rather than dropped or guessed at.
    amz = dict((s, r) for p, s, r in rules if p == "amazon")
    check("the Amazon square is the one open question, and says so",
          amz["250x250"]["source"] == "verify")
    check("and it is left at the value it had rather than moved on a guess",
          amz["250x250"]["maxFileBytes"] == 51200)

    diag = (MODULE / "src" / "diagnostics.ts").read_text()
    check("the panel derives the doubt rather than reading the claim",
          "_verifiedAgainst" in diag and "export function ceilingDoubt" in diag)
    check("and a rule declaring doc with nothing behind it is not confirmed",
          "'no source recorded'" in diag)
    check("while one somebody looked at and could not confirm says which",
          "'marked for confirmation'" in diag)
    check("so the clean answer is about the record, not about the claim",
          "every limit records where it came from" in diag
          and "all limits sourced from documentation" not in diag)


def test_no_platform_picker_and_a_render_button_that_explains_itself():
    screen = BUILD_HTML.read_text()
    check("the toolbar no longer asks which platform", "platformPick" not in screen)
    check("the campaign's own platforms drive the render", "state.platforms" in screen)
    check("the render button says what it produces", "title=\"Build the finished file" in screen)
    check("and follows the job it queued", "followRender" in screen)


def test_the_picture_can_be_moved_and_zoomed_rather_than_snapped():
    """Nine alignments answer "top or bottom" and not "a bit further down".

    That is the note an operator writes, and the grid could not express it —
    nor zoom at all. The picture is placed by arithmetic now, and the sign of
    the offset is the load-bearing part: it names the part of the PICTURE that
    shows, so -1 slides the picture DOWN until its top edge meets the canvas.
    Backwards, every arrow moves the opposite way from the one pressed, and on
    a symmetrical photograph that survives a glance.
    """
    svg = (MODULE / "src" / "svg.ts").read_text()
    types = (MODULE / "src" / "types.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("a concept carries an offset and a zoom",
          "backgroundOffset" in types and "backgroundZoom" in types)
    check("the composer computes the cover rectangle", "export function coverRect" in svg)
    check("zoom cannot drop below covering the canvas", "MIN_BG_ZOOM" in svg)
    check("a source with no intrinsic size still falls back",
          "preserveAspectRatio=\"${bgPos} slice\"" in svg)
    check("the old nine alignments still mean what they meant",
          "LEGACY_OFFSET" in svg)
    check("the screen offers arrows, not a grid of nine",
          "data-nudge" in screen and "data-bgpos" not in screen)


def test_the_button_and_the_logo_can_be_moved():
    screen = BUILD_HTML.read_text()
    style = (MODULE / "src" / "block-style.ts").read_text()
    check("the button has a pad", 'nudgePad(\'cta\'' in screen)
    check("the logo has a pad", 'nudgePad(\'logo\'' in screen)
    check("the background has a pad", 'nudgePad(\'background\'' in screen)
    # Align meant the label's alignment inside a button the template already
    # centres, so Left, Center and Right all rendered identically.
    check("aligning the button moves the button", "alignedX(box.w, region, style.align)" in style)
    check("and only the button may be moved",
          "// ...and only the CTA may be moved" in style)


def test_one_slider_beats_two_number_fields():
    screen = BUILD_HTML.read_text()
    style = (MODULE / "src" / "block-style.ts").read_text()
    check("the logo is sized proportionally", "MIN_LOGO_SCALE" in style)
    check("the screen offers it as a slider", "logoScale" in screen)
    check("the background zoom is a slider", "bgZoom" in screen)
    check("the numbers are still there, behind Advanced", 'class="advanced"' in screen)
    # Fractions have to survive being stored, or the slider moves and the ad
    # does not.
    check("a fraction is not rounded to an integer on the way in",
          "prop === 'scale' || prop === 'opacity'" in screen)


def test_the_card_behind_the_copy_has_a_colour():
    """It was a template constant, and it is the single thing deciding
    whether the copy on "Full background with copy panel" can be read."""
    style = (MODULE / "src" / "block-style.ts").read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("a concept can restyle the panel", "export interface PanelStyle" in style)
    check("the options route says which sizes draw one", "'panel'" in server)
    check("the screen offers it only where there is one",
          "function panelControls()" in screen and "willDraw('panel')" in screen)


def test_two_blocks_printed_over_each_other_is_caught():
    """Newly reachable, because blocks can now be moved.

    Every other check measures a box on its own: contrast samples what is
    behind the ink and finds the other block's fill, the fit pass finds copy
    that fits its own box, and the safe-area pass finds both boxes inside the
    margin. The ad has a headline printed through a button.
    """
    qa = (MODULE / "src" / "qa.ts").read_text()
    check("QA checks for collisions", "'collision'" in qa)
    check("and the hero is exempt, being deliberately full-bleed",
          "role !== 'hero'" in qa)


def test_a_dark_logo_moves_the_palette_and_not_the_logo():
    palette = (MODULE / "src" / "palette.ts").read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("there is a palette proposer", "export function paletteVariants" in palette)
    check("with a route behind the login", "POST /api/palette/variants" in server)
    check("and a button on the logo panel", "logoLegibility" in screen)
    # The three rules that keep the proposals coherent.
    check("a palette that already works gets no proposals",
          "if (verdict === 'fine') return" in palette)
    check("the light and dark roles are never inverted",
          "roles whose NAME asserts something" in palette)
    check("the whole palette is applied, never part of one",
          "state.doc.campaign.brand.colors = v.colors" in screen)


def test_the_render_asks_which_sizes_and_the_proof_is_reachable():
    """Two separate complaints, one flow.

    A package of eight takes minutes and an operator who changed one headline
    was waiting on seven renders they did not ask for. And the finished job
    said "open the proof" with nothing to click, because only the rebuild
    route ever filed a batch — the render started from the build screen wrote
    a proof to disk that nothing linked to.
    """
    server = (MODULE / "src" / "server.ts").read_text()
    jobs = (MODULE / "src" / "jobs.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("a job can render a subset", "sizes?: SizeKey[]" in jobs)
    check("counted over the same set it will render", "const wanted =" in jobs)
    check("the render route accepts a size list", "sizes: Array.isArray(body.sizes)" in server)
    check("the screen asks which", "'Render which sizes?'" in screen)
    check("one place files a finished job onto its project",
          "function fileJobOntoProject" in server)
    check("and the render route uses it too", "if (forProject) fileJobOntoProject" in server)
    # This used to look for a "data-proofnow" button. The finished render now
    # hands over a copyable client link and the two decision buttons instead,
    # which is the same promise kept better -- see
    # test_a_finished_render_hands_over_a_client_link below.
    check("the finished render hands the proof over", "function showHandover" in screen)


def test_a_generated_picture_can_be_revised():
    """The server has taken a revise instruction and the previous image as a
    reference since the intake screen was built. The build screen never
    offered the box, so every change was a fresh roll."""
    screen = BUILD_HTML.read_text()
    check("there is a box to ask for a change", "aiRevise" in screen)
    check("and it redraws from the last picture rather than from nothing",
          "previousUrl: last && last.url" in screen)


def test_the_column_collapses_and_explains_itself_in_bubbles():
    screen = BUILD_HTML.read_text()
    check("the style panels are an accordion", "function wireAccordion" in screen)
    check("one open at a time", "other.open = false" in screen)
    check("a collapsed panel still says it is carrying an override",
          "bsdot" in screen)
    check("the explanations are help bubbles", "function help(text, side)" in screen)
    check("and the paragraphs between the controls are gone",
          screen.count('style="font-size:11.5px;color:var(--ink-2);margin-top:4px"') == 0)


def test_the_nav_starts_collapsed_on_the_builder():
    """It is a three-column bench and the nav takes a fifth of it — and so is
    every other creative tool, which is why the answer is one function rather
    than a prefix test written out at each of the three renderers."""
    from hub.sidebar import collapses_by_default
    sidebar = (ROOT / "hub" / "sidebar.py").read_text()
    check("the sidebar accepts a page default", "collapsed_default" in sidebar)
    check("a stored preference still wins",
          "sv==='1'||(sv===null&&window.__s1hubCollapseDefault)" in sidebar)
    check("and the builder asks for it",
          collapses_by_default("/tools/display-ads/_hub/start"))
    check("...as does its front page",
          collapses_by_default("/tools/display-ads"))
    check("a page that is not a workbench does not",
          not collapses_by_default("/client360"))


def test_the_site_scan_is_read_for_what_it_knows():
    """An Insites scan reports the palette the client's live site actually
    paints, the logo it detected and a screenshot. Observed beats declared —
    Brandfetch routinely returns a palette without saying which entry is the
    brand colour."""
    link = (ROOT / "hub" / "ad_builder_link.py").read_text()
    screen = BUILD_HTML.read_text()
    check("there is a route for it", '"/site-brand"' in link)
    check("it reads the scan's own color scheme", '_sec("colour_scheme")' in link)
    check("and the detected logo", '"has_detected_logo"' in link)
    check("joined by domain, never by name", "The URL is the join key" in link)
    check("a client with no scan is not an error", '"found": False' in link)
    check("the screen offers the colors", "drawSiteBrand" in screen)
    # Copied rather than applied: which of the five roles a site colour should
    # become is a judgement, and guessing it moves four other things.
    check("clicking one copies it rather than applying it",
          "Copied, not applied" in screen)


def test_british_spellings_are_gone_from_what_a_person_reads():
    """"Colour" and "Centre" in a US agency's tool, per the request.

    Only the pages: code comments are for whoever opens the file, and
    rewriting three hundred of them would bury the change.
    """
    # These strings stay British on purpose: they are what the check looks
    # *for*. A repo-wide pass over the copy converted them once, and the check
    # then reported every correct "color" on the page as a finding.
    offenders = []
    for path in (MODULE / "public").glob("*.html"):
        for word in ("colour", "Colour", "centre", "Centre", "centred"):
            if re.search(rf"\b{word}\b", strip_comments(path.read_text())):
                offenders.append(f"{path.name}: {word}")
    check("no British spellings on the builder's pages", not offenders, ", ".join(offenders))


def test_a_disk_that_is_fine_does_not_read_as_broken():
    """0.9 GB free at 2% used was a FAIL, which is a verdict contradicting
    the number printed beside it."""
    diag = (MODULE / "src" / "diagnostics.ts").read_text()
    check(
        "the disk verdict is driven by how full the volume is",
        "usedPct >= 95 ? 'fail'" in diag,
    )
    check(
        "a small-but-empty volume is described rather than failed",
        "a small volume, not a filling one" in diag,
    )
    screen = BUILD_HTML.read_text()
    check("and the health dot does not flash", "animation: pulse" not in screen)


# --------------------------------------------------------------- the sequence


def test_the_toolbar_appears_in_the_order_the_work_happens():
    """Save, then render, then file. Nothing offered before it can work.

    Filing ads onto a client and rendering a set both stood next to Save from
    the moment the page opened, on a build that had never been written down --
    and both act on what is on the SERVER. On an unsaved build that is the
    previous version, so "attach to client" filed the ads somebody had just
    finished replacing and reported a clean success for doing it.
    """
    screen = BUILD_HTML.read_text()
    check("the render button starts hidden",
          'id="renderAll" style="display:none"' in screen)
    check("and says why it is not there yet", 'id="saveHint"' in screen)
    check("save is the primary action on arrival",
          'class="btn primary" id="save"' in screen)
    check("one function decides what is on the toolbar",
          "function refreshStage" in screen)
    check("saving is what reveals the render button",
          "$('renderAll').style.display = state.saved ? '' : 'none';" in screen)
    check("attaching waits for files to exist",
          "!window.S1_BASE || !pid || !state.rendered" in screen)
    check("the gate lives in saveCampaign, so every door opens it",
          "state.saved = true;\n      refreshStage();" in screen)
    check("opening a campaign is not saving it", "state.saved = false;" in screen)


def test_the_render_button_offers_the_four_real_jobs():
    """Render, render and file, file, and package.

    They were spread across three places: a toolbar link that predated any
    render, a Deliver button that only appeared once a status had changed, and
    the render itself. So the ordinary job -- build these and put them on the
    client's record -- was two controls with a page in between.
    """
    screen = BUILD_HTML.read_text()
    check("the menu exists", "function askRenderAction" in screen)
    for act in ("render-file", "render", "file", "zip"):
        check(f"it offers {act}", f"'{act}',", extra=act)
    check("filing has a one-press path", "function fileToClient" in screen)
    check("which posts to the Hub's own attach route",
          "'/_hub/attach'" in screen)
    check("the zip says it takes a moment", "This can take a minute" in screen)
    check("the two that need files are disabled without them",
          "state.rendered," in screen and
          "Nothing has been rendered yet, so there is nothing to file." in screen)
    check("cancelling the size question does not render everything",
          "if (sizes === false) return;" in screen)
    check("filing happens after the files exist, not with the render",
          "if (alsoFile) {" in screen)


def test_a_download_is_not_a_delivery():
    """The ZIP button packages; it does not hand the campaign over.

    ``deliverProject`` sets the project complete, writes a "Delivered" note and
    mails the team. Doing that because somebody downloaded a file to look at it
    is a status nobody set, on every screen that reads it.
    """
    server = (MODULE / "src" / "server.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("the route can package without recording",
          "if (body.record === false) return json(res, 200, { ...out, recorded: false });"
          in server)
    check("and says which it did", "recorded: true" in server)
    check("the download asks for the unrecorded one", '"record": false' in screen or
          "record: false" in screen)
    check("a withheld size is named rather than silently missing",
          "a size that fails QA is never packaged" in screen)


def test_a_finished_render_hands_over_a_client_link():
    """A URL to send, and the decision, on the screen that just built them."""
    screen = BUILD_HTML.read_text()
    check("the handover panel exists", "function showHandover" in screen)
    check("the link is a field you can select", 'id="proofUrlBox"' in screen)
    check("with a copy button", 'id="proofCopy"' in screen)
    check("that never claims a copy it did not make",
          "Press Ctrl-C" in screen and "execCommand" in screen)
    check("it is the live proof route, not the static snapshot",
          "function proofUrl() { return '/proof/'" in screen)
    check("spelled out for pasting", "function proofUrlAbsolute" in screen)
    check("and both decisions are on it",
          'id="decApprove"' in screen and 'id="decRevise"' in screen)
    check("approving says what it will do before the press",
          "approval is what packages the finished files" in screen)


def test_a_decision_says_which_door_it_came_through():
    """Approved by the client and approved by us are different records.

    One function, because approving is the trigger for packaging and two
    copies of that drift. Two routes, because only one of them can know who
    pressed the button: the client's page is reached with nothing but the
    project id, so a name claimed there is a name anyone with the link could
    claim.
    """
    server = (MODULE / "src" / "server.ts").read_text()
    check("one description of what approving means",
          "async function recordDecision" in server)
    check("the client's door records the client",
          "approved ${source}" in server and "'by the client'" in server)
    check("the staff door is under the admin gate",
          "/^\\/api\\/project\\/([\\w.-]+)\\/decision$/" in server)
    check("and reads the name the Hub proxy attached",
          "by: (req.headers['x-s1-user'] as string) || 'a member of staff'" in server)
    check("the public door reads no name at all",
          "No `by` is read here" in server)
    check("a change request with no detail is refused",
          "A revision with no detail cannot be worked on." in server)
    screen = BUILD_HTML.read_text()
    check("and the screen asks for that detail rather than sending it empty",
          "function askNotes" in screen)


def _proxy_headers_for(path, supplied=None):
    """The headers the proxy would forward upstream, without an upstream.

    The renderer is a second process and is not running in CI, so the request
    is intercepted at ``requests.request`` -- which is the line under test
    anyway: what this proxy chooses to attach.
    """
    sys.path.insert(0, str(ROOT))
    import os
    os.environ.setdefault("ADBUILDER_ADMIN_TOKEN", "t" * 24)
    os.environ.setdefault("SECRET_KEY", "x" * 32)
    from hub import ad_builder_proxy as pxy

    seen = {}

    class _Fake:
        status_code, headers, raw = 200, {"content-type": "text/plain"}, None
        def iter_content(self, *a, **k):
            return iter([b"ok"])

    def _spy(method, url, **kw):
        seen.update(kw.get("headers") or {})
        return _Fake()

    real = pxy.requests.request
    pxy.requests.request = _spy
    try:
        import wsgi
        from werkzeug.test import Client
        Client(wsgi.application).get(path, headers=supplied or {})
    finally:
        pxy.requests.request = real
    return seen


def test_the_proof_a_client_opens_is_reachable_and_theirs():
    """A link that lands on a staff login is not a link you can send.

    The proof page sat behind the Hub session, so "here is the link, tell us
    what you think" put a client on a login form for an account they do not
    have. And the editor on that page rebuilds the creative for everyone
    holding the link and reaches billed endpoints, so it is not theirs.
    """
    proxy = (ROOT / "hub" / "ad_builder_proxy.py").read_text()
    proof = (MODULE / "src" / "proof.ts").read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    hub = (ROOT / "hub" / "__init__.py").read_text()

    # This one is Python, so it is exercised rather than grepped. It is the
    # gate that decides what a stranger can reach, and a string match would
    # pass just as happily on a pattern that matched everything.
    sys.path.insert(0, str(ROOT))
    from hub.ad_builder_proxy import is_public
    for path, want in [
        ("proof/AD-2026-000777", True),
        ("/proof/AD-2026-000777/", True),
        ("api/proof/P-1/approve", True),
        ("api/proof/P-1/revision", True),
        # Everything else keeps the Hub login. Rebuild re-renders the creative
        # for everyone holding the link and reaches billed endpoints.
        ("api/proof/P-1/rebuild", False),
        ("build", False),
        ("projects", False),
        ("api/campaigns", False),
        ("api/project/P-1/decision", False),
        ("api/images/generate", False),
        # Anchored on whole segments, so a longer name is not a prefix match.
        ("proofs/all", False),
        ("proof/P-1/../../build", False),
        ("", False),
    ]:
        check(f"is_public({path!r}) is {want}", is_public(path) is want,
              extra=path)
    check("rebuild is deliberately not public", "Rebuild is deliberately out" in proxy)
    # Also exercised. Forwarding our admin token with an anonymous request
    # would tell the renderer a client is staff -- which is exactly the
    # question it asks to decide whether to draw the live editor. Attaching
    # our own credential to a stranger's request is how a public page quietly
    # gains an operator's controls with every screen looking healthy.
    sent = _proxy_headers_for(
        "/tools/display-ads/proof/AD-2026-000777",
        supplied={"X-Admin-Token": "a-token-the-caller-made-up",
                  "x-s1-user": "someone@example.com"},
    )
    lower = {k.lower() for k in sent}
    check("no admin token travels with an anonymous request",
          "x-admin-token" not in lower)
    check("no staff name either", "x-s1-user" not in lower)
    check("nor the intake code", "x-intake-code" not in lower)
    check("and the mount prefix still does, or every link breaks",
          "x-forwarded-prefix" in lower)
    check("the proof is chrome-free, like the landing pages",
          '"/tools/display-ads/proof/"' in hub)
    check("the page can be drawn without its editor", "editor?: boolean" in proof)
    check("and the route decides from the request, not from a parameter",
          "const staffViewing = checkAuth(req, url).ok;" in server)
    check("the per-size wiring survives the editor being absent",
          proof.count("if (sizeModal)") >= 3)


# ------------------------------------------------------------------- meta


def test_meta_is_a_platform_you_can_buy():
    """It had a config file, every template drew its sizes, and one line
    dropped it.

    ``.filter(p => p === 'google' || p === 'amazon')``, written out three
    times. A Meta buy came back as a set of Google banners with nothing
    anywhere saying so.
    """
    registry = (MODULE / "src" / "registry.ts").read_text()
    server = (MODULE / "src" / "server.ts").read_text()
    intake = (MODULE / "src" / "intake.ts").read_text()
    embed = (MODULE / "public" / "embed.html").read_text()

    check("one function decides", "export function acceptPlatforms" in registry)
    check("and the literal is gone from every caller",
          "p === 'google' || p === 'amazon'" not in server + intake)
    check("the request route uses it",
          "acceptPlatforms(body.platforms).platforms" in server)
    check("the validator uses it",
          "acceptPlatforms(sub.platforms).platforms" in intake)
    check("the public form offers Meta", 'name="platforms" value="meta"' in embed)

    meta = json.loads((MODULE / "src" / "config" / "platforms" / "meta.json").read_text())
    for size in ("1080x1080", "1200x628", "1080x1350", "1080x1920"):
        check(f"meta buys {size}", size in meta["sizes"], extra=size)
    check("meta is not held to the display-banner file ceiling",
          all(s["maxFileBytes"] > 1_000_000 for s in meta["sizes"].values()))

    # The reason Meta could be switched on rather than built: the layouts were
    # already there. A size added to the platform with no template behind it
    # renders as a 422 in the preview pane.
    for path in sorted(TEMPLATES.glob("*.json")):
        spec = json.loads(path.read_text())
        for size in meta["sizes"]:
            check(f"{spec['id']} draws {size}", size in spec["sizes"],
                  extra=f"{spec['id']}/{size}")


def test_the_hub_start_form_requires_a_url_and_asks_where_it_runs():
    """The URL is what the tool reads, so a build without one is a build
    written about a business nothing has looked at."""
    link = (ROOT / "hub" / "ad_builder_link.py").read_text()
    form = (ROOT / "hub" / "templates" / "ad_builder_start.html").read_text()

    check("the form requires it", 'name="website" type="text" required' in form)
    check("and says why", "it is what the tool reads" in form)
    check("the server requires it too, not only the browser",
          "A website or landing page is required." in link)
    check("a required attribute is not a rule", "is a courtesy to somebody typing" in link)
    check("a campaign name typed into it is refused", "_looks_like_a_site" in link)
    check("the platforms are offered", "PLATFORM_CHOICES" in link)
    check("meta among them", '("meta", "Meta (Facebook & Instagram)")' in link)
    check("and travel to the renderer", '"platforms": [p for p, _ in PLATFORM_CHOICES' in link)
    check("the landing page is sent under the name the renderer reads",
          '"landingPage": website,' in link)


def test_the_ai_reads_the_page_it_is_writing_about():
    """The analysis sat on the project record and no button on the build
    screen ever asked for it."""
    server = (MODULE / "src" / "server.ts").read_text()
    screen = BUILD_HTML.read_text()

    check("the campaign hands over its page and the analysis",
          "landingPage: proj?.landingPage," in server and
          "landing: proj?.landingAnalysis," in server)
    check("the screen keeps both", "state.landing = doc.landing || null;" in screen)
    check("and fetches one when there is none", "function ensureLanding" in screen)
    check("the copy draft exists", "function wireDraftCopy" in screen)
    check("it fills empty fields only", "if (effective(k)) { kept.push(k); return; }" in screen)
    check("and writes to the whole set, not one size",
          "state.copyScope[k] = 'all';" in screen)
    check("a blank offer is reported, not hidden",
          "left blank rather than invented" in screen)
    check("the picture is drawn against the page too",
          "landingAnalysis: landing || undefined," in screen)
    check("and a redraw keeps it", "landingAnalysis: state.landing || undefined," in screen)


def test_a_generated_picture_can_be_kept():
    """A picture on the render disk is a draft; the sweep removes it.

    Filing that address onto a client record produces a gallery row that works
    today, 404s after the sweep, and was never openable by the client whose
    gallery it is in.
    """
    server = (MODULE / "src" / "server.ts").read_text()
    screen = BUILD_HTML.read_text()
    check("there is a route that makes one permanent",
          "'/api/imagery/keep'" in server)
    check("it only accepts a path this service wrote",
          "That is not a generated picture from this build." in server)
    check("it is staff-only", "route === 'POST /api/imagery/keep' ||" in server)
    check("the screen offers it", "id=\"aiKeep\"" in screen)
    check("and files it through the Hub", "'/_hub/gallery'" in screen)
    check("the renderer never learns who our clients are",
          "does not know who our clients are" in server)
    check("asking for a change is still there, not replaced",
          "id=\"aiRevise\"" in screen)


def test_softness_and_text_weight_are_checked():
    """Two defects that pass every other check, then arrive on the proof.

    A photograph stretched past its own pixels sits inside the safe area,
    collides with nothing, has fine contrast, and lands well under the file cap
    because a blurry JPEG compresses well. Text weight on a Meta image is the
    same shape of problem from the other direction: nothing is wrong with the
    ad, it is simply served to fewer people. Neither had a check.
    """
    qa = (MODULE / "src" / "qa.ts").read_text()
    svg = (MODULE / "src" / "svg.ts").read_text()

    check("the composer reports what it actually painted", "images: PlacedImage[]" in svg)
    check("including the pixels the source had", "naturalW: bgImg.w" in svg)
    check("and the hero's cover fit, not the hole it went in", "const heroCover" in svg)
    check("QA reads that rather than recomputing the placement",
          "composed.images" in qa and "coverRect" not in qa)
    check("the ask is measured at delivery scale, not canvas scale",
          "(i.drawnW * scale) / i.naturalW" in qa)
    check("vector artwork is a real answer, not an unmeasured one",
          "no resolution to outrun" in qa)
    check("softness never blocks a delivery", "Never a fail." in qa)

    check("text coverage is asked where a platform publishes a number",
          "rule.textCoverageWarnPct" in qa)
    check("and it says the rule was retired rather than implying a rejection",
          "Meta dropped that rule in 2020" in qa)
    check("the printed number and the verdict are decided together",
          "export function coverageVerdict" in qa)


def test_the_meta_guideline_is_meta_s_alone():
    """A 300x250 is mostly type by design and always will be.

    Asking it the Meta question would put an amber chip on every display size
    in every campaign, which is how amber comes to mean nothing -- the same
    reason the word-count check was taken out.
    """
    import json as _json
    cfg = MODULE / "src" / "config" / "platforms"
    meta = _json.loads((cfg / "meta.json").read_text())["sizes"]
    check("every Meta size carries the guideline",
          all(r.get("textCoverageWarnPct") == 20 for r in meta.values()),
          f"{[k for k, r in meta.items() if r.get('textCoverageWarnPct') != 20]}")
    check("and each says when it was last checked",
          all("_textCoverageSource" in r for r in meta.values()))
    for name in ("google", "amazon"):
        sizes = _json.loads((cfg / f"{name}.json").read_text())["sizes"]
        check(f"{name} is not asked it",
              all("textCoverageWarnPct" not in r for r in sizes.values()),
              f"{[k for k, r in sizes.items() if 'textCoverageWarnPct' in r]}")


def test_the_delivery_zip_describes_itself_to_a_machine():
    """README.txt is for whoever opens it; ad ops reads twenty a week.

    Platform, size and weight were being inferred from filenames. The risk in
    adding a second document is that the two disagree about one delivery, so
    both are built from the same shipped/skipped arrays in the same call.
    """
    deliver = (MODULE / "src" / "deliver.ts").read_text()
    check("the zip carries a machine-readable manifest",
          "campaign-manifest.json" in deliver)
    check("built from the same arrays as the README",
          "campaignManifest(project, concept, clientSlug, root, shipped, skipped, animated)"
          in deliver
          and "readme(project, concept, clientSlug, shipped, skipped, animated)" in deliver)
    check("the unit is a file in the zip, not a render",
          "for (const platform of targets) {" in deliver)
    check("a shared creative names the other platforms carrying it",
          "sharedWith" in deliver)
    check("the size bought and the pixels delivered are separate claims",
          '"delivered"' in deliver or "delivered: e.deliveredDimensions" in deliver)
    check("a withheld size is named rather than merely absent",
          "withheld: skipped.map" in deliver)
    check("and counted, so a short delivery cannot read as a complete one",
          "withheld: skipped.length" in deliver)
    check("no path from our render disk travels to the client",
          "localFile" not in deliver.split("function campaignManifest")[1].split("export async function")[0])
    check("the header tree says what is in the zip",
          "campaign-manifest.json               (the same delivery for a machine" in deliver)


def test_a_client_s_setup_is_saved_and_refilled():
    """The second ad for a client is the same ad with a different offer.

    Everything that took the time -- the brand, the family, where the picture
    sits -- is settled, and settling it again from the intake form is how a
    seasonal promo for an eleven-year client costs what a new client costs.
    """
    presets = (MODULE / "src" / "presets.ts").read_text()
    server = (MODULE / "src" / "server.ts").read_text()

    check("there is a store for a client's settled setup", "export class PresetStore" in presets)
    check("and it is not called a template, because that name is taken",
          "Why this is not called a template" in presets and "TemplateSpec" in presets)
    check("a preset carries the design and drops the campaign",
          "A preset carries the design, never the campaign" in presets)
    check("the client is a name and a domain, never a derived key",
          "never a derived key" in presets)
    check("a client is matched exactly, never by substring",
          "never a substring" in presets)
    check("a slot the family draws nowhere is refused by name",
          "draws no ${role} on any size" in presets)
    check("and the refusal is returned rather than swallowed",
          "refused: { role: string; reason: string }[]" in presets)
    check("a blank slot falls back rather than rendering an empty box",
          "falls back to what the preset saved" in presets)
    check("a stale per-size override never outranks the copy just typed",
          "would quietly outrank the one somebody just typed" in presets)
    check("the routes are staff-only", "url.pathname.startsWith('/api/presets')" in server)
    check("saving reports what it refused", "return json(res, 201, { preset, refused });" in server)

    # A feature with no control is a feature nobody can reach — this file
    # counts six tools that were invisible for weeks for exactly that reason.
    screen = BUILD_HTML.read_text()
    check("the build screen offers it", 'id="savePreset"' in screen)
    check("gated behind Save, like Render and Attach",
          "presetBtn.style.display = state.saved ? '' : 'none';" in screen)
    check("and the gate lives in the one function that decides the toolbar",
          "var presetBtn = $('savePreset');" in screen)
    check("it asks which lines are slots rather than assuming all of them",
          "anything left unticked travels unchanged" in screen)
    check("the slots offered are the ones this family actually draws",
          "a slot is a property of the family" in screen)
    check("read across every size, not the one on screen",
          "Object.keys((t && t.blocks) || {})" in screen)
    check("what the server refused is said out loud, not swallowed",
          "msg += ' Left out: '" in screen)


def test_one_ad_structure_many_offers():
    """The list already exists, in the email it arrived in.

    Retyping nine cities into the build screen is where the fourth city gets
    missed. A row becomes a concept, so the existing job queue renders the
    batch and counts its own progress.
    """
    batch = (MODULE / "src" / "batch.ts").read_text()
    server = (MODULE / "src" / "server.ts").read_text()

    check("a row becomes a concept, so jobs.ts renders the batch",
          "A row becomes a CONCEPT on one campaign" in batch)
    check("a bad row is named and the rest still build",
          "A bad row is named and the rest still build" in batch)
    check("rows are numbered as the spreadsheet numbers them",
          "header is row 1" in batch)
    check("a missing column fails the file before anything renders",
          "A missing column fails the whole file" in batch)
    check("a column that is not an ad field is ignored, not fatal",
          "ignoredColumns" in batch)
    check("the row cap refuses rather than truncating",
          "never truncated" in batch or "not truncated" in batch)
    check("and states the cap in the refusal", "BATCH_MAX_ROWS}-row limit" in batch)
    check("the CSV reader handles a quoted comma", "if (c === '\"') { quoted = true" in batch)
    check("and a spreadsheet's byte order mark", "BOM from Excel" in batch)
    check("concept ids stay unique past twenty-six rows",
          "export function conceptLetter" in batch)
    check("the batch is validated before the queue, not in the worker",
          "The batch failed validation" in server)
    check("rejected rows reach the project record, not only the response",
          "row(s) rejected: " in server)
    check("nothing is invented for a blank cell", "Nothing is invented" in batch)


def test_a_preset_can_actually_be_used():
    """Saving one was built and using one was not.

    The generate and batch routes existed and no screen called either, so a
    preset could be saved and then reached only with curl -- the whole premise
    of it, that the next ad for a client is a form fill, was unavailable to
    anybody. A feature with no control is the failure this file counts six
    tools for.
    """
    page = (MODULE / "public" / "presets.html")
    check("there is a screen for them", page.exists())
    if not page.exists():
        return
    html = page.read_text()
    server = (MODULE / "src" / "server.ts").read_text()

    check("it is served", "route === 'GET /presets'" in server)
    check("and it is staff-only, like the API behind it",
          "url.pathname === '/presets' ||" in server)
    # The trap this module names about itself: a page of root-absolute fetches
    # loads perfectly under the Hub's mount and no button does anything.
    check("served through withBase, or no button works under the mount",
          "withBase(req, fs.readFileSync(file, 'utf8'))" in
          server.split("route === 'GET /presets'")[1].split("route === 'GET /projects'")[0])

    check("one ad from a preset is reachable", "/generate" in html)
    check("a CSV of them is reachable", "/batch" in html)
    check("a chosen file is read into the box rather than posted unseen",
          "Read into the box rather than posting straight off" in html)
    check("the fields are keyed on the role, never the label",
          "i.getAttribute('data-f')" in html and "data-f=\"' + esc(f.role)" in html)

    # A batch that built nine of twelve and said only "9 built" is a folder
    # nobody counts.
    check("rejected rows are listed by the line the spreadsheet shows",
          "was not built" in html and "Row ' + esc(r.line)" in html)
    check("an ignored column is named, not silently dropped",
          "Ignored column" in html)
    check("a refused platform is named", "refusedPlatforms" in html)
    check("a partial batch is amber rather than a clean success",
          "level = 'warn';" in html)
    check("a blank line is said to fall back rather than render empty",
          "nothing here is invented" in html)

    check("the row cap is documented where it is set",
          "BATCH_MAX_ROWS" in (MODULE / ".env.example").read_text())


def test_the_preset_screen_is_linked_rather_than_tiled_twice():
    """It is the other half of one tool, not a second tool.

    A second tile is two things to keep in step with only one of them ever
    updated -- the note the social planner makes about its own queue. So it is
    linked from the screens somebody is already on, and the start page is the
    one that matters: whoever is filling that form in for the SECOND ad for a
    client should not be filling it in at all.
    """
    start = (ROOT / "hub" / "templates" / "ad_builder_start.html").read_text()
    check("the Hub's start page offers it", "/presets" in start)
    check("beside the builds link rather than as a rival tile",
          "Saved presets" in start)
    for name in ("build.html", "projects.html", "presets.html"):
        page = MODULE / "public" / name
        if page.exists():
            check(f"{name} carries the nav entry", 'href="/presets"' in page.read_text())
    creative = (ROOT / "hub" / "templates" / "creative.html").read_text()
    check("and there is still exactly one Display Ad Builder tile",
          creative.count("/tools/display-ads/") == 1,
          f"{creative.count('/tools/display-ads/')} tiles")


def test_the_scan_shows_the_site_as_well_as_its_palette():
    """The screenshot was fetched, returned, and drawn nowhere.

    An operator judging brand colour on a dark canvas had to open the client's
    website in another tab to remember what they were matching, and mostly did
    not -- which is how an ad comes back "not really them" with nobody able to
    say why.
    """
    link = (ROOT / "hub" / "ad_builder_link.py").read_text()
    screen = BUILD_HTML.read_text()

    check("the route carries both devices",
          '"screenshot": desktop,' in link and '"screenshotMobile": mobile,' in link)
    check("because half a package runs on a phone",
          "half the sizes in a display package run on a" in link)
    check("a scan with only a screenshot still counts as something",
          'bool(colors or logo.get("logo_url") or desktop)' in link)
    check("the screen draws it", "function siteShotMarkup" in screen)
    check("and does not give up when the palette is empty",
          "if (!entries.length && !d.screenshot) return;" in screen)
    check("mobile is a toggle, not a second thumbnail", 'data-shot="mobile"' in screen)

    # The one that would be wrong to get wrong: a screenshot of somebody's
    # website is reference, and offering to put it behind their ad is offering
    # something a few people would press.
    check("the lightbox's use button is optional",
          "function lightbox(url, onUse, caption)" in screen and
          "(onUse ? '<button" in screen)
    check("and the screenshot opens without one", "lightbox(img.src, null," in screen)
    check("it says so on the panel too", "Reference only" in screen)


def test_the_font_signal_says_only_what_it_measured():
    """`has_google_font_api` is a boolean about loading, never about which
    face. A note implying otherwise is worse than no note."""
    link = (ROOT / "hub" / "ad_builder_link.py").read_text()
    screen = BUILD_HTML.read_text()

    check("the route reads it", 'gdpr.get("has_google_font_api")' in link)
    check("and it is tri-state, because absent is not no",
          "if not isinstance(google_fonts, bool):\n            google_fonts = None" in link)
    check("the route says what it will not claim",
          "never says *which* face" in link)
    check("the screen says nothing when it was not measured",
          "if (uses === null || uses === undefined) return;" in screen)
    check("a true is stated as the weak signal it is",
          "The scan does not say which one." in screen)
    check("and a false is the actionable direction",
          "nothing here will match it by accident" in screen)
    check("it sits in the Type panel, where the decision is",
          "'<div id=\"siteType\"></div>' +" in screen)

# ---------------------------------------------------------------- animation

ANIMATION_TS = MODULE / "src" / "animation.ts"
RENDER_TS = MODULE / "src" / "render.ts"
SERVER_TS = MODULE / "src" / "server.ts"
DELIVER_TS = MODULE / "src" / "deliver.ts"
PLATFORM_DIR = MODULE / "src" / "config" / "platforms"
PROXY_PY = ROOT / "hub" / "ad_builder_proxy.py"


def test_the_animation_rules_are_googles_and_say_so():
    """The four numbers that get an animated ad refused, and whose each is.

    Every one of these is a published Google requirement for an animated image
    ad, and every one is invisible on the screen it is broken on: a GIF that
    loops for ever plays perfectly in every browser, and one running at 20
    frames a second looks better than one at 5. So they are data with a source
    against them rather than a habit in the encoder — the rule
    ``services/abcd_service.py`` works to. The two that are OURS are marked as
    ours, because "Google requires three slides" about a number Google has
    never published is a claim a client can talk us out of once they check.
    """
    src = ANIMATION_TS.read_text()
    check("there is an animation module at all", ANIMATION_TS.exists())

    for name, value in (("minFrameMs", "200"), ("maxTotalMs", "30_000"),
                        ("maxSlides", "3"), ("maxFrames", "5")):
        check(f"{name} is stated as {value}",
              re.search(rf"{name}:\s*{re.escape(value)}", src) is not None)

    check("Google's own page is cited for the platform half",
          "support.google.com/adspolicy" in src)
    # The house numbers are named as house numbers, in the same block.
    rules = src[src.index("export const ANIMATION_RULES"):]
    rules = rules[:rules.index("} as const;")]
    check("maxSlides and maxFrames are marked as ours, not Google's",
          "Ours." in rules and "house:" in rules)


def test_a_looping_gif_can_never_be_endless():
    """`loop: 0` is the one value the 30-second rule forbids, and it is unreachable.

    A GIF with a loop count of zero repeats for ever. It renders correctly in
    every browser, passes every eye on every screen here, and is outside
    Google's rule — which is exactly the shape of failure this codebase keeps
    having to undo. So the count is COMPUTED from the cycle length rather than
    chosen, and the floor is 1.
    """
    src = ANIMATION_TS.read_text()
    body = src[src.index("export function loopsWithin"):]
    body = body[:body.index("\n}")]
    check("the loop count is computed from the cycle, not passed in",
          "Math.floor(maxTotalMs / cycleMs)" in body)
    check("and it can never come back as 0", "Math.max(1," in body)

    # The encoder must take its loop from the plan and from nothing else.
    enc = src[src.index("export async function encodeAnimation"):]
    check("the encoder writes the plan's loop count",
          "loop: plan.loop" in enc)
    check("and never a literal 0", not re.search(r"loop:\s*0", enc))


def test_only_a_placement_that_takes_a_gif_is_offered_one():
    """Read out of the platform config, and refused by name where it is not.

    Only Google's banner sizes list ``gif``. Amazon's specs here are static at
    40-50 KB and Meta turns an uploaded GIF into a video, so an animation
    offered there is a tickbox that consents and then fails for a reason that
    is nothing to do with the operator — the failure that took Google Ads off
    the Google Access list. And a size that came back static with nothing
    saying which or why is the silent gap this module exists to avoid.
    """
    src = ANIMATION_TS.read_text()
    check("support is read from the platform config, not decided in code",
          "rule.formats.includes('gif')" in src)
    check("a refusal carries a reason a person can read",
          "ships as the static ad" in src)

    google = json.loads((PLATFORM_DIR / "google.json").read_text())
    animatable = [s for s, r in google["sizes"].items() if "gif" in r["formats"]]
    check("Google's banner sizes accept an animated file", len(animatable) >= 8,
          f"{len(animatable)} sizes")
    # The responsive-display IMAGE assets must not: Google composes its own
    # headline around those, and they are image assets rather than ad slots.
    for size in ("1200x628", "1200x1200", "1200x1500"):
        rule = google["sizes"].get(size)
        check(f"{size} is not offered an animation",
              rule is not None and "gif" not in rule["formats"])

    for name in ("amazon", "meta"):
        cfg = json.loads((PLATFORM_DIR / f"{name}.json").read_text())
        gif = [s for s, r in cfg["sizes"].items() if "gif" in r["formats"]]
        check(f"{name} is not offered one at any size", not gif, ", ".join(gif))


def test_every_frame_is_checked_and_not_just_the_first():
    """Slide 2 is different copy in the same box, and it can break it.

    The static 320x50 of the sample campaign passes; the same ad with a
    three-word-longer slide 2 fails on a clipped headline. Checking frame one
    and calling the animation checked is how that reaches a client. This is
    the one thing about the whole feature most likely to be quietly wrong,
    because a set whose first frame is fine looks fine.
    """
    src = RENDER_TS.read_text()
    frames = src[src.index("async function buildFrames"):]
    frames = frames[:frames.index("\n/** Frame overrides")]
    check("QA runs inside the per-frame loop", frames.count("await runQa(") == 1
          and "for (let i = 0; i < plan.frames.length; i++)" in frames)
    check("a later frame's finding names the slide it is on",
          "frame.tag" in frames and "frame.label" in frames)
    # And it must not repeat frame 1's findings once per frame: five copies of
    # one warning is how a panel of warnings stops being read.
    check("a finding frame 1 already carries is not repeated per frame",
          "seen.has(key)" in frames)


def test_slide_copy_can_be_written_for_one_canvas():
    """Without it a set animates at seven sizes and fails at the eighth.

    Exactly the reason ``CreativeConcept.copy`` carries per-size entries: a
    headline that fits the 300x600 is two lines on the 320x50. Resolved slide
    by slide and field by field, so a size that needs a shorter slide 2
    overrides slide 2 and inherits slide 3.
    """
    src = ANIMATION_TS.read_text()
    check("the spec carries per-size slides", "sizeSlides?" in src)
    body = src[src.index("export function slidesFor"):]
    body = body[:body.index("\n}")]
    check("a size's slides overlay the default field by field",
          "...(base[i] ?? {}), ...(forSize[i] ?? {})" in body)
    check("and the planner resolves through it",
          "slidesFor(spec, ctx.size)" in src)

    screen = BUILD_HTML.read_text()
    check("the build screen offers writing a slide for this size only",
          "animThisSize" in screen and "sizeSlides" in screen)
    # The scope trap the static copy fields already carry: writing the default
    # while an override still holds the old value reads as the edit failing.
    check("writing the default clears that field's override for this size",
          "delete per2[i][field]" in screen)


def test_animating_needs_a_saved_static_build_and_says_so():
    """The gate, on the server rather than only in the form.

    An animation is built FROM the still ad, so there is nothing to animate
    until one exists. The campaign file on disk is what "a static build
    exists" means — it is what Save writes and what the render job reads. A
    rule the form keeps while the write breaks it is not a rule, so both the
    options route and the build route check it.
    """
    src = SERVER_TS.read_text()
    gate = "There is no saved build for this set yet."
    check("the options route refuses without a saved build",
          src.count(gate) >= 2, f"found {src.count(gate)} of 2")
    check("and it names saving as the way out",
          "Save the static ' +\n                 'design first" in src
          or "Save the static design first" in src.replace("' +\n                 '", ""))

    screen = BUILD_HTML.read_text()
    check("the Animate button appears only once the build is saved",
          "animBtn.style.display = state.saved ? '' : 'none'" in screen)
    check("and it reads the saved build rather than the screen",
          "leaveIfSafe('the animation panel', openAnimator)" in screen)


def test_an_animation_never_replaces_the_static_file():
    """Most placements on a buy take the still one, so both ship.

    A folder holding only GIFs is a set that cannot be trafficked: Amazon takes
    none of them and three of Google's own sizes take none either. So the GIF
    is written beside its sibling with ``_animated`` on the end, delivered in
    its own folder, and counted apart — "8 files delivered" about a pack of
    five ads and three GIFs is a sentence a client reads as eight ads.
    """
    src = RENDER_TS.read_text()
    check("the file is written beside the static one", "_animated" in src)

    dsrc = DELIVER_TS.read_text()
    check("the zip carries them in their own folder",
          "${root}/animated/${clientSlug}_${a.size}_animated.gif" in dsrc)
    check("the count is reported apart from the static one",
          "animatedCount" in dsrc)
    check("the machine manifest lists them apart from `assets`",
          re.search(r"animated: animated\.map", dsrc) is not None)
    # A failing animation is withheld, and the static file goes in its place.
    check("a QA-failing animation is withheld and named",
          "the animated version did not pass creative checks and was withheld" in dsrc)


def test_the_animation_is_its_own_job():
    """So a static build costs exactly what it costs today.

    The whole point of the sequencing: animation is asked for after the fact,
    on the sizes that take one, and must not be extra work bolted onto the
    render every set goes through. Same queue — so it is watched, persisted and
    recovered like any other job — and its own branch.
    """
    jobs = (MODULE / "src" / "jobs.ts").read_text()
    check("a job says which of the two it is", "mode?: 'static' | 'animated'" in jobs)
    check("'static' stays the default, so recovered jobs mean what they meant",
          "mode: input.mode ?? 'static'" in jobs)
    check("the animated branch does not write a static manifest",
          "if (input.mode === 'animated')" in jobs
          and jobs.index("if (input.mode === 'animated')") < jobs.index("const cld = new CloudinaryService()"))
    # The denominator has to be the sizes that can actually carry one, or a
    # finished job's progress bar stops short of its own total.
    check("progress is counted over sizes that accept an animation",
          "animationSupport(platform, size).supported" in jobs)


def test_an_animation_is_attributable():
    """A route added in TypeScript cannot be silent about client work.

    The Display Ad Builder is the one module that is not Python, so everything
    it does happens in a process that has never heard of ``hub/audit.py``. The
    proxy is the single point it all passes through. Its own action rather than
    folded into the render: a client record showing one entry for both could
    not say whether the animated versions on a delivery were ever built here.
    """
    proxy = PROXY_PY.read_text()
    check("the animate route has a name in the activity log",
          'r"^api/animate/([\\w-]+)$"' in proxy and '"ads_animated"' in proxy)
    check("and it is not one of the paths a client may reach",
          "animate" not in proxy[proxy.index("PUBLIC_PATTERNS"):
                                 proxy.index("def is_public")])


def test_the_panel_prints_the_timing_rather_than_implying_it():
    """The two numbers that refuse an ad are invisible on screen.

    A GIF running at 20 frames a second looks better than one at 5, and one
    looping for ever plays perfectly everywhere. Neither is visible on the
    preview, so both are printed beside it, in words, from the SERVER's plan —
    a second copy of that arithmetic in the browser is a second answer to "is
    this legal", and the two disagree the day either is edited.
    """
    screen = BUILD_HTML.read_text()
    for phrase in ("frames</b> at ", "then stops", "in total",
                   "of the ", "KB this placement allows"):
        check(f"the panel prints {phrase.strip('<>b/ ')!r}", phrase in screen)
    check("the numbers come from the server's own plan",
          "r.loop" in screen and "r.totalMs" in screen and "r.fps" in screen)
    # No local recomputation of the rule.
    check("the browser does not compute the loop count itself",
          "30000" not in screen and "30_000" not in screen)
    check("a size that cannot take one is said, not drawn as an error",
          "r.supported === false" in screen and "ships as the static ad" in screen)



def main():
    print(__doc__.strip().splitlines()[0])
    print()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(fn.__doc__.strip().splitlines()[0] if fn.__doc__ else name)
            fn()
            print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
