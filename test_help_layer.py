"""The help layer: a bubble that resolves, and one that explains nothing.

`hub/help.py` is the registry, `hub-help.js` draws it, and a key the registry
does not hold is **removed client-side** rather than left as a dead "?". That
is right for the page and is exactly what makes the mistake invisible: the
template reads as helped, the screen shows nothing, no console error, no
failed request. `hub/help.py`'s own note says so, and `test_ads_explainer.py`
asserted it for Smart 1 Ads alone.

Three other tools had placed a bubble on their own title with no entry behind
any of them -- Website Blocks, the Social Content Planner and Video Search.
Video Search's template even carries a comment saying its key must not be
renamed *because renaming would orphan the bubble*, protecting a key that
pointed at nothing.

Run directly: ``python3 test_help_layer.py``. No pytest, no network.
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("KNACK_APP_ID", "test-app")
os.environ.setdefault("KNACK_API_KEY", "test-key")

from hub import help as help_registry, help_audit

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "hub", "help_routes.py"), encoding="utf-8") as _fh:
    _help_routes_src = _fh.read()

FAILURES = []


def ok(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}"
          f"{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        FAILURES.append(f"{label}{(': ' + detail) if detail else ''}")


def check(label, got, want):
    ok_ = got == want
    print(f"  {'ok  ' if ok_ else 'FAIL'}  {label}: {got!r}")
    if not ok_:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


print("Every bubble this Hub places has help behind it")
print("-" * 62)

DATA = help_audit.audit()

ok("the registry was read", DATA["registry"] > 100, str(DATA["registry"]))
ok("and bubbles were found to check", DATA["placed"] > 100, str(DATA["placed"]))
check("no bubble explains nothing", [r["key"] for r in DATA["missing"]], [])
check("and /api/integrity agrees", help_audit.check_dead_bubbles(), [])

# The three that were dead. Named rather than merely counted: the point is
# that these screens now say something, not that a number went to zero.
for key in ("site_blocks.intro", "social.planner", "video_backgrounds.overview"):
    entry = help_registry._BY_KEY.get(key)
    ok(f"{key} resolves", entry is not None)
    if entry:
        ok(f"  ...and says something worth reading", len(entry.body) > 120,
           f"{len(entry.body)} chars")
        ok(f"  ...with a title that is not the tool's name again",
           bool(entry.title) and entry.title.lower() != key.split(".")[0])


print("\nBoth ways a bubble is placed, and the one that cannot be resolved")
print("-" * 62)

# Two screens build a key from a loop. The Proposal Builder's reach panel
# writes data-help="sales_builder.areas.${key}" -- the interpolation inside
# the attribute's own quotes -- and the prospect record's card() concatenates
# outside them, 'data-help="hub.prospect.'+esc(key)+'". A scan for help_dot()
# alone calls both sets dead; a scan that resolved the interpolation would be
# guessing. Named, the way tools/linkcheck.py names a URL built by
# concatenation.
#
# Asserted as prefixes rather than as one hard-coded count: a third screen
# building a key is a thing this file should keep working, and what matters is
# that every entry a runtime prefix reaches is accounted for rather than
# reported as a bubble nobody registered.
RUNTIME_PREFIXES = ("sales_builder.areas.", "hub.prospect.")
ok("a key built at runtime is named rather than resolved",
   any("${" in r["key"] for r in DATA["runtime"]),
   str([r["key"] for r in DATA["runtime"]]))
ok("including one concatenated outside the attribute's quotes",
   any(r["key"] == "hub.prospect." for r in DATA["runtime"]),
   str([r["key"] for r in DATA["runtime"]]))
ok("and the entries those prefixes reach are not called dead",
   bool(DATA["runtime_covers"])
   and all(k.startswith(RUNTIME_PREFIXES) for k in DATA["runtime_covers"]),
   str(DATA["runtime_covers"]))
ok("with every runtime prefix actually reaching something",
   all(any(k.startswith(p) for k in DATA["runtime_covers"])
       for p in RUNTIME_PREFIXES),
   str(DATA["runtime_covers"]))
check("nothing registered is left unaccounted for", DATA["unplaced"], [])

# data-help is a real placement, not decoration: scanning only Jinja misses it.
_lit, _rt = help_audit.placements()
ok("data-help placements are counted too", bool(_rt) or bool(_lit))


print("\nThe check bites")
print("-" * 62)

# Handed a tree that plainly drifts, it must say so. A check that can be
# silenced by an edit somewhere else is worse than no check — the rule
# test_env_config.py and test_spelling.py both work to.
with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "modules", "invented", "templates"))
    os.makedirs(os.path.join(tmp, "hub"))
    with open(os.path.join(tmp, "modules", "invented", "templates", "x.html"),
              "w", encoding="utf-8") as fh:
        fh.write("<h1>Tool {{ help_dot('invented.key.nobody.wrote') "
                 "if help_dot is defined else '' }}</h1>\n")
    drifted = help_audit.audit(tmp)
    check("a placed key with no entry is reported",
          [r["key"] for r in drifted["missing"]], ["invented.key.nobody.wrote"])
    rows = help_audit.check_dead_bubbles(tmp)
    ok("and the finding names the file it is in",
       len(rows) == 1 and rows[0]["file"].endswith("x.html"),
       str(rows))
    ok("and says what to do about it",
       bool(rows) and "hub/help.REGISTRY" in rows[0]["fix"])

    # The same tree with the entry present must come back clean, or the check
    # is reporting the scan rather than the defect.
    with open(os.path.join(tmp, "modules", "invented", "templates", "x.html"),
              "w", encoding="utf-8") as fh:
        fh.write("<h1>Tool {{ help_dot('social.planner') "
                 "if help_dot is defined else '' }}</h1>\n")
    check("a placed key that resolves is not reported",
          [r["key"] for r in help_audit.audit(tmp)["missing"]], [])


print("\nThe rule the templates keep")
print("-" * 62)

# Every call guarded, so a module whose Jinja env never got
# install_template_helpers() loses the icon rather than the page.
#
# Matched as a call *inside a Jinja delimiter*, not as the bare token: a
# template that emits the bubble markup from JavaScript explains in a comment
# that help_dot() is a Jinja global and cannot be used there, and a substring
# pass reports that explanation as an unguarded call. Same rule the env drift
# check, the orphan-template check and the coverage check all work to --
# prose is not a call site.
ROOT = os.path.dirname(os.path.abspath(__file__))
_JINJA_CALL = re.compile(r"\{\{[^}]*help_dot\(|\{%[^%]*help_dot\(")
unguarded = []
for folder in ("hub", "modules"):
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, folder)):
        dirnames[:] = [d for d in dirnames
                       if d not in ("_attic", "node_modules", ".git")]
        for name in filenames:
            if not name.endswith(".html"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if _JINJA_CALL.search(line) and "is defined" not in line:
                        unguarded.append(f"{os.path.relpath(path, ROOT)}:{i}")
check("every help_dot call is guarded", unguarded, [])

# `data-screen` is what offers a tour. hub-help.js already declines to offer
# an empty one, so naming a screen with no steps is not a broken page today —
# but `tour()` falls back to the MODULE prefix, so the day a sibling screen
# registers steps this would serve them over elements that are not on the
# page. That is the Smart 1 Ads failure, and `has_tour()` exists so a layout
# does not have to rely on the client declining.
#
# So the rule is not "never name a screen with no tour" — it is "an
# UNCONDITIONAL data-screen must name one". A declaration wrapped in a
# has_tour() test is correct whether or not the tour exists yet, which is
# what the three hub and module pages that named an empty tour now do.
unguarded_screens = []
for folder in ("hub", "modules"):
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, folder)):
        dirnames[:] = [d for d in dirnames
                       if d not in ("_attic", "node_modules", ".git")]
        for name in filenames:
            if not name.endswith(".html"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
            for m in re.finditer(r'data-screen=["\']([^"\'{}]+)["\']', src):
                screen = m.group(1)
                if help_registry.has_tour(screen):
                    continue
                # Guarded on the same tag? Look back to the start of the
                # element for a has_tour( test.
                start = src.rfind("<", 0, m.start())
                if "has_tour(" in src[start:m.start()]:
                    continue
                rel = os.path.relpath(path, ROOT)
                unguarded_screens.append(f"{rel}: {screen}")
check("an unconditional data-screen names a tour that exists",
      unguarded_screens, [])

# And the guard has to be the real one: `has_tour` is registered by both
# register_help() and install_template_helpers(), so a hub page and a mounted
# module both get it.
ok("has_tour is a template global on both halves of the app",
   "has_tour" in _help_routes_src and _help_routes_src.count("has_tour") >= 3)


print("\nThe walkthrough says which step it cannot run")
print("-" * 62)

# hub/demos.py drives a tool's real screen -- filling its real fields,
# clicking its real buttons -- and every step names the element to act on.
# A step whose element is not there used to be silent in both halves: the
# ring hid itself, and "Do it for me" returned without doing or saying
# anything. That is the failure CLAUDE.md names about Smart 1 Ads' scenario,
# fixed for OFFERING a walkthrough on a screen it was not written for and
# never for RUNNING one.
with open(os.path.join(ROOT, "hub", "static", "hub-demo.js"),
          encoding="utf-8") as _fh:
    DEMOJS = _fh.read()
with open(os.path.join(ROOT, "hub", "static", "hub-help.css"),
          encoding="utf-8") as _fh:
    HELPCSS = _fh.read()

ok("a step whose target is missing says so",
   "s1-demo-gone" in DEMOJS and "cannot be shown or filled in for you" in DEMOJS)
ok("and the button that could only do nothing is not drawn",
   "|| gone;" in DEMOJS.replace(" ", " "))
ok("perform() no longer returns in silence",
   "target-missing" in DEMOJS)
# A target drawn by a fetch arrives after the step is painted. Without a
# repaint the amber line would stand and the button stay hidden on a step
# about to become perfectly workable — a worse answer than the silence it
# replaced. Same debounced observer hub-help.js uses to mount bubbles on
# late-rendered content.
ok("a target that arrives late brings the button back",
   "MutationObserver" in DEMOJS)
# And the observer must not see paint()'s own work: paint writes into the
# panel and moves the ring, so unfiltered it would repaint every 150ms for as
# long as a walkthrough is open.
_obs = DEMOJS[DEMOJS.index("var repaint = null;"):
              DEMOJS.index('window.addEventListener("resize"')]
ok("and it does not repaint on its own writes",
   "ours(records[i].target)" in _obs
   and '"s1-demo-panel"' in _obs and '"s1-demo-ring"' in _obs)
ok("only while a walkthrough is open", "if (!S" in _obs)
ok("the line is styled, or it is invisible",
   ".s1-demo-gone{" in HELPCSS)
# Amber, not red: the narration above it is still correct and still worth
# reading — only the driving cannot happen.
ok("and drawn amber rather than red",
   "#c08a2e" in HELPCSS or "#6b4a12" in HELPCSS)

DEMO = help_audit.demo_targets()
ok("the walkthroughs were measured", DEMO.get("measured") is True)
ok("and every scenario was looked at",
   DEMO["scenarios"] >= 20, str(DEMO["scenarios"]))
ok("steps that name an element were counted",
   DEMO["steps"] > 100, str(DEMO["steps"]))
# Deliberately not asserted to be zero. Fifty-five steps across eighteen
# scenarios name a hook that is in no template; that is a backlog, and a
# check switched on red is a check somebody turns off -- it would take the
# bubble check down with it. What is asserted is that the number is *known*.
ok("the unanchored ones are named rather than counted",
   all(r.get("unanchored") for r in DEMO["rows"]))
ok("and a scenario that drives none of its steps is marked apart",
   all(isinstance(r.get("dead"), bool) for r in DEMO["rows"]))
ok("a fully anchored scenario is not in the list",
   set(DEMO["clean"]).isdisjoint({r["key"] for r in DEMO["rows"]}))

# Fed a scenario whose target plainly exists, it must not be reported.
_anchored = [r for r in DEMO["clean"]]
ok("some walkthroughs are clean, so this is not reporting everything",
   len(_anchored) >= 5, str(len(_anchored)))

# A scenario that drives NONE of its steps is the one worth retiring rather
# than repairing: the learner presses "Do it for me" on every step and nothing
# happens, which is the Smart 1 Ads failure. The backlog above stays a
# backlog; this is the floor under it.
check("no walkthrough drives none of the steps it names",
      [r["key"] for r in DEMO["rows"] if r["dead"]], [])

# A scenario worked down to zero is named rather than left to the count.
# The backlog above stays a backlog on purpose, so nothing here asserts how
# large it is -- but a scenario somebody has repaired must not quietly come
# apart again when a control it drives is renamed. Each of these was every-
# step-dead-or-nearly before it was repaired against the tool that exists.
_REPAIRED = ["seo_images.first_batch",
             "sales_builder.first_quote",
             "sales_builder.deliver"]
_still_broken = sorted(r["key"] for r in DEMO["rows"] if r["key"] in _REPAIRED)
check("a repaired walkthrough stays anchored", _still_broken, [])
# And the list may not outlive its scenarios -- an entry naming one that has
# been renamed or retired would pass by describing nothing, which is the
# stale-exemption failure check_stale_json_exemptions() names.
_known = set(DEMO["clean"]) | {r["key"] for r in DEMO["rows"]}
check("and every scenario it names still exists",
      sorted(k for k in _REPAIRED if k not in _known), [])

# A target is credited by the ATTRIBUTE being there, not by the word.
# `_found()` used to test `name in everything`, so data-demo='unmatched' was
# reported as anchored because the word "unmatched" appears in another tool's
# prose, and data-demo='client-name' because something somewhere has a class
# of that name. Twenty-two steps read as anchored while driving nothing, and
# two whole walkthroughs -- Image Creator's and the UTM builder's -- read as
# working while every driving step in them resolved to no element at all.
# That is the failure this audit exists to report, hiding inside the audit.
_sp = help_audit._spellings("data-demo", "unmatched")
_prose = "<p>3 unmatched projects</p>"
_real = "<div data-demo=\"unmatched\"></div>"
ok("a bare word does not anchor a step",
   not any(x in _prose for x in _sp), _prose)
ok("and the attribute does", any(x in _real for x in _sp), _real)
ok("both quotings count",
   any(x in "<b data-demo='unmatched'>" for x in _sp))
ok("a selector kind it cannot look for asks for nothing",
   help_audit._spellings("class", "x") == ())

# The two that drove nothing at all, now anchored. Named rather than merely
# counted: the point is that these screens can now be walked.
for _key in ("image_creator.promo_post", "utm.campaign_links",
             "pdf_optimizer.compress", "calculators.publish",
             "fan_radio.spot", "radio_promo.first_spot"):
    ok(f"{_key} drives at least one step it names",
       _key not in [r["key"] for r in DEMO["rows"] if r["dead"]])

# Anchored, but nowhere in the tool the walkthrough drives. Reported rather
# than counted as missing -- the element may still be drawn at runtime -- the
# way a target accepted on a prefix already is.
ok("a target that only exists in another tool is named",
   isinstance(DEMO.get("elsewhere"), list), str(type(DEMO.get("elsewhere"))))

# A selector carrying nothing that identifies an element -- an
# `input[type='file']` -- is a step this check cannot speak to, and counting
# it as anchored was a tick over a question nobody asked. It is what let
# `client360.proposal` clear the floor above: three hooks in no template and
# one selector matching a file input on any page in the Hub. Reported apart,
# and it clears nothing.
ok("a selector naming nothing to test is counted apart",
   isinstance(DEMO.get("untestable"), list))
ok("...and every such step says which scenario it is in",
   all(":" in u for u in DEMO["untestable"]), str(DEMO["untestable"]))
ok("...and a scenario carrying one is not called clean",
   all(r["key"] not in DEMO["clean"]
       for r in DEMO["rows"] if r.get("untestable")))

# `data-tour` is how a tour step anchors, and seven walkthrough steps use it
# too. Left out of the parser, 39 anchors in this repo were tested by nothing:
# renaming one out from under the step that drives it changed no count.
ok("a data-tour anchor is a requirement the check reads",
   ("data-tour", "wm-roster") in help_audit._needs("[data-tour='wm-roster']"))

# The five that did. Named rather than merely counted: the point is that these
# screens can now be walked, not that a number went down.
for _key in ("seo.faq_and_schema", "qa.stale_creative", "landing_ads.from_page",
             "tickets.triage", "qa.billing_audit",
             # The two the floor above could not see until an untestable
             # selector stopped counting as a driven step. `client360.proposal`
             # had its hooks placed; `bg_remover.logo_cutout` described a free
             # "remove white background" button the tool has never had, so it
             # was rewritten against the preview cut that actually is free --
             # the Web Tickets "sort by age" rule, since a rep believes a
             # walkthrough.
             "client360.proposal", "bg_remover.logo_cutout"):
    ok(f"{_key} drives every step it names", _key in DEMO["clean"],
       str([r["key"] for r in DEMO["rows"] if r["key"] == _key]))

# A hook a template DERIVES cannot be found whole in any source. The QA index
# writes data-demo="qa-report-{{ key }}" once for every report it lists, so a
# scenario naming a report added next month is anchored without that template
# being edited -- and a plain substring search calls it dead, which is the
# guess linkcheck refuses to make about a concatenated URL.
ok("a derived hook is accepted on its literal prefix",
   "qa-report-" in DEMO["runtime_prefixes"], str(DEMO["runtime_prefixes"]))
ok("and the targets it covers are named rather than folded into the count",
   all(t.startswith("qa-report-") for t in DEMO["runtime"]) and bool(DEMO["runtime"]),
   str(DEMO["runtime"]))
# Three characters at least: a bare data-demo="{{ x }}" names no prefix, and
# one that matched everything would switch the check off.
check("a prefix short enough to match anything is not collected",
      help_audit._runtime_demo_prefixes('data-demo="a{{ k }}"'), [])
check("but a real one is",
      help_audit._runtime_demo_prefixes('data-demo="thing-{{ k }}"'), ["thing-"])


print("\nThe panel that shows it")
print("-" * 62)

with open(os.path.join(ROOT, "hub", "templates", "diagnostics.html"),
          encoding="utf-8") as _fh:
    DIAG = _fh.read()
with open(os.path.join(ROOT, "hub", "access.py"), encoding="utf-8") as _fh:
    ACCESS = _fh.read()

ok("Diagnostics draws the help layer", 'id="diag-help-audit"' in DIAG)
ok("and asks the route for it", '"/api/help-audit"' in DIAG)
ok("the route is behind Utilities", '"/api/help-audit"' in ACCESS)
# One panel, because they are one question asked of two mechanisms.
ok("both halves are on the one panel",
   "renderHelpAudit" in DIAG and "walkthroughs" in DIAG and "bubbles" in DIAG)
# A runtime-built key gets the pill this panel already has for exactly this
# state. The class is composed by row(), so the literal never appears inside
# the renderer -- what is asserted is the state it passes, and that the pill
# it names is one the stylesheet defines.
_render = DIAG[DIAG.index("function renderHelpAudit"):DIAG.index("function loadAll")]
ok("a key built at runtime is drawn as unverified, not as a fault",
   'row("unverified"' in _render and ".p-unverified{" in DIAG)
# And it is never drawn as a warning, which would make a thing nobody can act
# on look like a thing somebody must.
ok("and never as a warning",
   'row("warn"' not in _render.split("Built at runtime")[0].rsplit("row(", 1)[-1])
# --------------------------------------------------------------- coverage
print()
print("And the coverage audit is measured against what the Hub serves")

from hub import help_coverage                                     # noqa: E402

_cov = help_coverage.report()

# The load-bearing assertion, and the one the old version could not make.
# `/api/help/coverage` answered `missing: []` against a hand-typed list of 23
# screens while two dozen tiled tools carried no explanation -- a clean bill
# of health produced by not looking. Finding no tiles must therefore be a
# refusal to answer, never an empty sweep: the same rule the anonymous route
# sweep works to about a mount table it could not read.
ok("the index pages were actually read", _cov["measured"] is True,
   _cov.get("reason", ""))
ok("and they hold the tools the Hub tiles", _cov["tools"] >= 40,
   f"{_cov['tools']} tiles")

# A parse that comes back empty is the failure above. Fed markup that no
# longer matches, the report must say it could not measure rather than
# reporting that nothing is missing.
_real = help_coverage._TILE
try:
    help_coverage._TILE = re.compile(r"<a class=\"no-such-tile\" href=\"([^\"]+)\">(.*?)</a>")
    _blind = help_coverage.report()
    check("a parse that finds no tiles refuses to answer", _blind["measured"], False)
    check("  ...and claims nothing about what is missing", _blind["missing"], [])
    ok("  ...saying why, rather than reporting a clean bill",
       "coverage" in _blind.get("reason", ""), _blind.get("reason", ""))
finally:
    help_coverage._TILE = _real

# Every tile lands in exactly one bucket. A tile in none of them is the
# silence this file exists to end -- it would read as covered by absence.
_seen = sum(len(_cov[k]) for k in ("covered", "missing", "unmapped", "client_facing"))
check("every tile is accounted for", _seen, _cov["tools"])
check("and none is unmapped", [t["href"] for t in _cov["unmapped"]], [])

# A page a prospect reads takes no staff help, and is not reported as a gap:
# the help layer is our explanation of our own screens, so a bubble there is
# an internal note in front of somebody we are selling to.
_client = {t["href"] for t in _cov["client_facing"]}
for _href in ("/land/boat/", "/land/stadium/", "/msa/"):
    ok(f"{_href} is named client-facing rather than unexplained",
       _href in _client)
ok("and each says why", all(t.get("reason") for t in _cov["client_facing"]))

# The other direction, which fails silently: help written under a prefix no
# tile maps to leaves that tool reading as missing while its copy sits there
# written, so somebody writes it twice.
check("no help is registered under a prefix nothing maps to",
      help_coverage.stray_prefixes(), [])

# And the third side, which `stray_prefixes()` structurally cannot see: it
# reduces every screen to its FIRST segment, so `hub.website_audit.*` reduces
# to `hub`, which NOT_A_TOOL exempts as the dashboard and Client 360. That
# exemption has to be broad, so the forward direction is what has to catch a
# tile mapped to a prefix that names the wrong thing -- and it fails in the
# safe-looking way, reporting the tool as never explained.
check("no tile is mapped to a prefix that names the wrong screen",
      help_coverage.mislabeled_prefixes(), [])

# A prefix in either shape resolves. Some screens need two segments to be
# unambiguous -- hub.website_audit is a tool and hub.prospect is a record
# page, and the bare `hub` they share names neither.
ok("a tile may name a two-segment screen",
   any("." in p for p in help_coverage.PREFIXES.values()),
   str(sorted({p for p in help_coverage.PREFIXES.values() if "." in p})))
_audit_tile = [t for t in _cov["covered"] if t["href"] == "/tools/website-audit"]
ok("and the Website Audit tool reads as explained, which it is",
   bool(_audit_tile), str([t["href"] for t in _cov["missing"]]))

# Fed the bug it was written for, it must say so -- a check that cannot fail
# is one nobody can trust, and this one would read green either way.
_saved = dict(help_coverage.PREFIXES)
try:
    help_coverage.PREFIXES["/tools/website-audit"] = "website_audit"
    _bit = help_coverage.mislabeled_prefixes()
    ok("the check bites on a prefix that names the wrong screen",
       [m["prefix"] for m in _bit] == ["website_audit"], str(_bit))
    ok("and names the screen the help is actually under",
       _bit and _bit[0]["registered"] == ["hub.website_audit"], str(_bit))
    # A tool with no help written was never this finding -- "nobody wrote
    # it" and "it is written under another name" are different jobs. Every
    # tiled tool carries help now, so the rule is held by the equality
    # above: the doctored list is exactly the doctored prefix, nothing
    # riding along with it.
finally:
    help_coverage.PREFIXES.clear()
    help_coverage.PREFIXES.update(_saved)
check("and it is green again once restored",
      help_coverage.mislabeled_prefixes(), [])

# And the routes read this rather than restating it. Two hand-typed lists is
# how one surface came to report a retired module as its only finding while
# the other reported nothing at all.
ok("both coverage routes read hub/help_coverage",
   _help_routes_src.count("help_coverage.report()") >= 2)

# Read as an assignment through the AST, not matched as text: the docstrings
# at those two call sites now *explain* the hand-typed lists they replaced,
# and a full-text pass reports the explanation of the fix as the defect --
# the rule check_provider_key_drift() and check_orphan_templates() both work
# to, met here for the third time.
import ast as _ast                                                # noqa: E402

_tree = _ast.parse(_help_routes_src)
_hardcoded = []
for _node in _ast.walk(_tree):
    if not isinstance(_node, _ast.Assign):
        continue
    _names = [t.id for t in _node.targets if isinstance(t, _ast.Name)]
    if "expected" not in _names:
        continue
    if isinstance(_node.value, (_ast.List, _ast.Tuple)) and any(
            isinstance(e, _ast.Constant) for e in _node.value.elts):
        _hardcoded.append(_ast.unparse(_node.value)[:60])
check("neither route assigns a literal list of what to expect",
      _hardcoded, [])

# The bubble text and the tour steps stay outside the login -- they are our
# own explanation of our own screens and the chrome fetches them on every
# page, including the ones a prospect reads. Coverage is a different thing:
# it returns the whole tool inventory and which of it is unfinished, which is
# a roadmap rather than help copy, and it sat under the same anonymous
# prefix.
import tempfile as _tf                                            # noqa: E402

_T = _tf.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_T, "t.db")
os.environ["SECRET_KEY"] = "help-layer-test"
os.environ["PANEL_PASSWORD"] = "test"
os.environ["HUB_DATA_DIR"] = _T
os.environ["AUDIT_LOG_PATH"] = os.path.join(_T, "audit.jsonl")

import wsgi                                                       # noqa: E402
from werkzeug.test import Client                                  # noqa: E402

_anon = Client(wsgi.application)
for _path in ("/api/help/coverage", "/api/demos/coverage"):
    check(f"{_path} refuses a stranger", _anon.get(_path).status_code, 401)
for _path in ("/api/help", "/api/demos"):
    check(f"{_path} stays public, because the chrome fetches it everywhere",
          _anon.get(_path).status_code, 200)

# A scenario's `path` is where a rep is told to go before the walkthrough can
# run at all, and six of them named a page the composed app answers 404 to:
# /tools/google against a module mounted at /google, /tools/sites against
# /sites, /tools/suite against /suite, and three more. Nothing reported it --
# `catalogue()` prints the string, `hub-demo.js` never navigates, and the
# steps then read as anchored-to-nothing for a reason that is one line above
# them. This is the assertion test_oauth_redirects.py already makes about the
# six callback paths, wearing a walkthrough: a route the app serves, or the
# scenario is describing somewhere nobody can open.
#
# A redirect counts as served -- these are staff pages behind AuthGuard, and
# `302 -> /login` is the route existing and refusing an anonymous caller. Only
# a 404 means there is no such page.
print()
from hub import demos as _demos                                    # noqa: E402
print("And every walkthrough names a page this Hub actually serves")
print("-" * 62)
_lost = []
for _s in _demos.SCENARIOS:
    if _anon.get(_s.path, follow_redirects=False).status_code == 404:
        _lost.append(f"{_s.key} -> {_s.path}")
ok("every scenario path is a route the composed app serves", not _lost,
   "; ".join(_lost))
# ...and it can go red: a path nothing is mounted at must be reported.
ok("...and a path nothing serves is reported",
   _anon.get("/tools/a-page-no-module-is-mounted-at").status_code == 404)

_staff = Client(wsgi.application)
_staff.post("/login", data={"password": "test"})
_r = _staff.get("/api/help/coverage")
check("and a signed-in reader gets the coverage", _r.status_code, 200)
# `missing` was asserted non-empty here once -- proof the route had stopped
# reading the hand-typed list that always answered []. The backlog it named
# has since been written down to zero, so a true empty is now the correct
# answer, and the discriminator is the shape only a measured report has:
# measured, a real tile count, and every tile accounted for in exactly one
# bucket. The hand-typed list could produce none of those.
_body = _r.get_json()["coverage"]
ok("which measured the tiles rather than reading a hand-typed list",
   _body.get("measured") is True and _body.get("tools", 0) >= 40
   and _body["tools"] == sum(len(_body.get(k, [])) for k in
                             ("covered", "missing", "unmapped",
                              "client_facing")),
   "an unmeasured answer, or a tile in no bucket, is the hand-typed "
   "list back again")

# And it is on the panel the other two halves are on, rather than being a
# report reachable only over the API. Bubbles, walkthroughs and coverage are
# one question asked of three mechanisms, and split across screens they come
# to disagree about which tools are explained -- the trap
# jsonstore.unmirrored_json_writers() exists to close.
_audit = _staff.get("/api/help-audit")
check("the help-audit panel carries coverage too", _audit.status_code, 200)
ok("with all three halves on the one answer",
   set(_audit.get_json()) >= {"bubbles", "walkthroughs", "coverage"})

with open(os.path.join(ROOT, "hub", "templates", "diagnostics.html"),
          encoding="utf-8") as _fh:
    _DIAG = _fh.read()
_r = _DIAG[_DIAG.index("function renderHelpAudit"):_DIAG.index("function loadAll")]
ok("and the panel draws it", "d.coverage" in _r and '"Coverage"' in _r)
# "we could not look" must not render as "nothing is missing" -- the failure
# this whole change is about, one layer up in the renderer.
ok("checking measured before it draws a count", "c.measured===false" in _r)
ok("and it names the client-facing tiles rather than counting them as gaps",
   "client_facing" in _r)


# ------------------------------------------------ the Proposal Builder
print()
print("The wizard explains its own steps")

# The biggest staff tool in the Hub: fourteen steps a rep spends a quarter of
# an hour in, and until now four bubbles on one panel of it. The keys are
# written where CLAUDE.md already documents a trap -- the card rate being
# buy-side, the budget parting company with the plan, a ZIP rule that reads
# as saved and does nothing -- so the copy says what the field does to the
# output rather than what it is.
_WIZ = os.path.join(ROOT, "modules", "sales_builder", "templates", "index.html")
with open(_WIZ, encoding="utf-8") as _fh:
    _wiz = _fh.read()

_placed = re.findall(r'help:"(sales_builder\.[\w.]+)"', _wiz)
ok("the wizard places a bubble on its steps", len(_placed) >= 12, str(len(_placed)))
check("and each step's key is placed once", len(_placed), len(set(_placed)))

_known = set(help_registry.keys()) if hasattr(help_registry, "keys") else {
    h.key for h in help_registry.REGISTRY}
_dead = sorted(k for k in _placed if k not in _known)
check("every key it places resolves in the registry", _dead, [])

# The rule test_ads_explainer.py holds the client estimate to: the page a
# client reads carries no staff help, because a bubble there is an internal
# note in front of somebody we are selling to.
for _name in ("client_proposal.html", "client_gone.html"):
    with open(os.path.join(ROOT, "modules", "sales_builder", "templates", _name),
              encoding="utf-8") as _fh:
        _client = _fh.read()
    ok(f"{_name} carries no staff help", "data-help" not in _client
       and "help_dot" not in _client)

# And no tour is named. A tour is anchored by selector, and this is one page
# whose markup is replaced on every step -- so one written for it could not
# drive past the step it started on, which is the silence hub-demo.js was
# just fixed to stop. Naming a screen with no steps is the other half of
# that, and hub/help.tour() would fall back to the module prefix and serve
# another screen's steps over elements that are not on the page.
# Matched as an attribute rather than as the bare word: the comment in that
# template *explains* the data-screen it deliberately does not draw, and a
# substring pass reports the explanation as the defect. Fourth time that rule
# has earned its keep -- the env drift check, the orphan-template check and
# the hand-typed-list check all read structure for the same reason.
ok("and it names no tour it cannot drive",
   not re.search(r'data-screen\s*=', _wiz))


# ------------------------------------------------------- the IO Builder
print()
print("The IO Builder explains the document that bills the client")

# A conversational builder rather than a stepped wizard, so the anchors are
# the decision points that are static markup: where the campaign is loaded
# from, the unfinished-order list, the creative checklist, the rates on the
# report, the two PDFs and Submit. The interview itself asks its questions in
# words already.
_IO = os.path.join(ROOT, "modules", "io_builder", "templates", "index.html")
with open(_IO, encoding="utf-8") as _fh:
    _io = _fh.read()

_io_keys = sorted(set(re.findall(r"help_dot\('(io_builder\.[\w.]+)'\)", _io)))
ok("the IO Builder places bubbles", len(_io_keys) >= 5, str(len(_io_keys)))
_io_dead = sorted(k for k in _io_keys if k not in _known)
check("and every one resolves in the registry", _io_dead, [])

# Guarded like every helper call in this codebase: io_builder is
# dispatcher-mounted, so it gets help_dot from install_template_helpers() --
# and a module whose Jinja env somehow did not must lose the icon rather
# than the page. (The sweep above asserts this for every template; named
# here because this file is the one that just gained six.)
_calls = re.findall(r"\{\{[^}]*help_dot\([^}]*\}\}", _io)
ok("every call is guarded", all("is defined" in c for c in _calls),
   str([c[:40] for c in _calls if "is defined" not in c]))

# It renders. A key in the template that never reaches the browser is the
# same silence as one that resolves to nothing.
_page = _staff.get("/tools/io/")
check("the page still builds", _page.status_code, 200)
_rendered = sorted(set(re.findall(rb'data-help="(io_builder\.[\w.]+)"', _page.data)))
check("and every key placed reaches the browser",
      len(_rendered), len(_io_keys))


# ------------------------------------------- one key, one entry
print()
print("A key registered twice is a key whose earlier copy nobody reads")

# _BY_KEY is {h.key: h for h in REGISTRY} and /api/help/registry builds the
# same way, so a second _h() for a key silently wins and the first becomes
# dead copy on a screen that still draws its dot. tour() is worse: it walks
# REGISTRY as a list, so a duplicated key carrying step= puts one step on the
# walkthrough twice.
#
# Not hypothetical. Two sessions wrote IO Builder help against the same
# screen from different branches; the merge was textually clean and landed
# io_builder.report.rates twice, with two different explanations of what the
# rate on that pane is. Nothing here reported it -- every key resolved, every
# dot rendered, and the audit counted the tool as covered.
_reg_keys = [h.key for h in help_registry.REGISTRY]
_dupes = sorted({k for k in _reg_keys if _reg_keys.count(k) > 1})
check("no key is registered twice", _dupes, [])

# Said against the list rather than against a set of itself: as_json() is a
# dict comprehension over REGISTRY, so a collision makes the payload shorter
# than the registry it was built from and every count either side of it still
# agrees with the other. Comparing it to len(set(...)) collapses the
# duplicate on both sides and passes while the entry is being lost, which is
# the shape of a check nobody can trust.
check("every registered entry survives into the payload",
      len(help_registry.as_json()["help"]), len(help_registry.REGISTRY))

import shutil as _shutil                                          # noqa: E402
_shutil.rmtree(_T, ignore_errors=True)


print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s):")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("the help layer holds: every bubble placed has help behind it, and a "
      "key built at runtime is named rather than guessed at")
