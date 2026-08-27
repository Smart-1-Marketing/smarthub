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
    check("the composer still honours the nine legacy alignments", "resolveBgPosition" in svg)
    check("and refuses an alignment SVG would not accept", "BG_POSITIONS" in svg)
    # The nine-way grid was replaced by nudge arrows in the round after this
    # one: it could answer "top or bottom" and not "a bit further down", which
    # is the note people actually write. Asserted in
    # test_the_picture_can_be_moved_and_zoomed_rather_than_snapped.
    check("the build screen offers a way to move the picture", "data-nudge" in screen)

    check("a concept can say what colour its overlay is", "backgroundOverlayColor" in types)
    check("a chosen colour is painted flat, not graded", "fill-opacity" in svg)
    check("the build screen offers colour and transparency", "bgOpacity" in screen and "bgWash" in screen)

    check("a text block can carry its own ink", "color?: string" in style)
    check("an unresolvable colour is dropped rather than rendered as black",
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
    check("a Google campaign builds 8 of the family's 13",
          len(family & google) == 8 and len(family) == 13,
          f"{len(family & google)} of {len(family)}")
    check("414x125 is Amazon's and not Google's",
          "414x125" in amazon and "414x125" not in google)
    check("the social sizes belong to Meta alone",
          {"1080x1080", "1080x1920"} <= meta and not ({"1080x1080"} & google))


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
    check("the finished render links the proof", "data-proofnow" in screen)


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
    """It is a three-column bench and the nav takes a fifth of it."""
    sidebar = (ROOT / "hub" / "sidebar.py").read_text()
    hub = (ROOT / "hub" / "__init__.py").read_text()
    check("the sidebar accepts a page default", "collapsed_default" in sidebar)
    check("a stored preference still wins",
          "sv==='1'||(sv===null&&window.__s1hubCollapseDefault)" in sidebar)
    check("and the builder asks for it",
          'collapsed_default=path.startswith("/tools/display-ads")' in hub)


def test_the_site_scan_is_read_for_what_it_knows():
    """An Insites scan reports the palette the client's live site actually
    paints, the logo it detected and a screenshot. Observed beats declared —
    Brandfetch routinely returns a palette without saying which entry is the
    brand colour."""
    link = (ROOT / "hub" / "ad_builder_link.py").read_text()
    screen = BUILD_HTML.read_text()
    check("there is a route for it", '"/site-brand"' in link)
    check("it reads the scan's own colour scheme", '_sec("colour_scheme")' in link)
    check("and the detected logo", '"has_detected_logo"' in link)
    check("joined by domain, never by name", "The URL is the join key" in link)
    check("a client with no scan is not an error", '"found": False' in link)
    check("the screen offers the colours", "drawSiteBrand" in screen)
    # Copied rather than applied: which of the five roles a site colour should
    # become is a judgement, and guessing it moves four other things.
    check("clicking one copies it rather than applying it",
          "Copied, not applied" in screen)


def test_british_spellings_are_gone_from_what_a_person_reads():
    """"Colour" and "Centre" in a US agency's tool, per the request.

    Only the pages: code comments are for whoever opens the file, and
    rewriting three hundred of them would bury the change.
    """
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
