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
ROOT = os.path.dirname(os.path.abspath(__file__))
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
                    if "help_dot(" in line and "is defined" not in line:
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

# The five that did. Named rather than merely counted: the point is that these
# screens can now be walked, not that a number went down.
for _key in ("seo.faq_and_schema", "qa.stale_creative", "landing_ads.from_page",
             "tickets.triage", "qa.billing_audit"):
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
    # The twenty-three tools nobody has written help for are NOT this finding.
    # "Nobody wrote it" and "it is written under another name" are different
    # jobs, and reporting the first as the second is a list somebody
    # re-triages on every run.
    ok("a tool that genuinely has no help is not reported as mislabeled",
       "gpt_ads" not in [m["prefix"] for m in _bit], str(_bit))
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

_staff = Client(wsgi.application)
_staff.post("/login", data={"password": "test"})
_r = _staff.get("/api/help/coverage")
check("and a signed-in reader gets the coverage", _r.status_code, 200)
ok("which names what is missing rather than answering none",
   len(_r.get_json()["missing"]) > 0,
   "an empty answer here is what the hand-typed list used to give")

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
