"""The mark that says a scan or a model is running.

    python3 test_thinking.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so it
never touches /var/data or the real one.

## Why this file exists

`.spin` was defined seven times across this repo and `.spinner` twice more,
each a 2px border arc at a slightly different size in a slightly different
gray. That is the drift `hub/storage.py` and `hub/images.py` exist to stop,
wearing a spinner — and every failure this file checks for is one that leaves
a page looking exactly as healthy as it did before:

  1.  the script is not served      — a 404 on /hub-thinking.js costs the
                                      animation everywhere at once, and the
                                      only symptom is a spinner that stopped
                                      being fun
  2.  it reaches hub pages and not  — HubBar covers the twenty mounted modules
      the twenty mounted modules,     and base.html covers the hub's own; a
      or the reverse                  script wired into one half is a feature
                                      that works on the screen it was tested
                                      on and nowhere else. This is the
                                      `no_crawl` trap: an after_request on the
                                      hub app would have missed every module
  3.  the animation is defined      — the glyph is drawn by the script and
      somewhere the script's         moved by hub-help.css. Those two are
      readers never load             injected by different code paths, so a
                                      page can get one without the other and
                                      show a mark that never moves
  4.  the vocabulary is written     — three kinds, in one file. A Python copy
      down twice                      would need a test proving the halves
                                      agree, which is the cost
                                      `test_target_areas.py` already pays
                                      twice over
  5.  reduced motion loses the      — the setting asks for less movement, not
      whole indicator                 less information, and a wait that goes
                                      invisible for those readers is the
                                      feature failing exactly where it was
                                      needed
  6.  the client-facing scan pages  — those three are served to a stranger on
      drift away from it              somebody else's website and cannot load
                                      a Hub script, so they carry the glyph
                                      inline. Inline is not licence to draw a
                                      different one
  7.  a mark is drawn where the     — .done() must not write "Done" or a tick:
      call failed                     whether the thing succeeded is the
                                      caller's answer, and a tick over a
                                      failed call is the confident wrong
                                      answer this codebase keeps undoing
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1think_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "thinking-test-secret"
os.environ["PANEL_PASSWORD"] = "thinking-test-shared"

from werkzeug.test import Client                                    # noqa: E402

from wsgi import application                                        # noqa: E402

_passed = _failed = 0


def ok(label, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}{('  — ' + detail) if detail else ''}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


SCRIPT = (ROOT / "hub" / "static" / "hub-thinking.js").read_text(encoding="utf-8")
CSS = (ROOT / "hub" / "static" / "hub-help.css").read_text(encoding="utf-8")

client = Client(application)


def get(path, **kw):
    return client.get(path, **kw)


def login():
    return client.post("/login", data={"password": "thinking-test-shared"},
                       follow_redirects=True)


# --------------------------------------------------------------- it is served
section("The script is reachable, from the root, like the scripts beside it")

_r = get("/hub-thinking.js")
ok("/hub-thinking.js answers 200", _r.status_code == 200, str(_r.status_code))
_body = _r.get_data(as_text=True)
ok("and it is the file, not a login page", "S1Think" in _body)
ok("served as JavaScript",
   "javascript" in (_r.headers.get("Content-Type") or ""),
   _r.headers.get("Content-Type", ""))

# Root-level, not under a mount: it is loaded by base.html *and* injected into
# twenty modules whose mounts all differ, so any other address would be wrong
# for nineteen of them.
ok("the stylesheet that moves it is reachable too",
   get("/hub-help.css").status_code == 200)


# ------------------------------------------------- both halves of the app
section("It reaches hub pages and mounted modules — the two are wired apart")

login()

_dash = get("/", follow_redirects=True).get_data(as_text=True)
ok("a hub page loads it (base.html)", "/hub-thinking.js" in _dash)

# The mounted modules get their chrome from HubBar in wsgi.py, which is a
# different code path from the hub app's own after_request. A script added to
# one and not the other is the failure hub/no_crawl.py names: it works on the
# page somebody tested and on none of the twenty they did not.
_mounted = get("/tools/seo-images/", follow_redirects=True)
ok("a dispatcher-mounted module gets it from HubBar",
   "/hub-thinking.js" in _mounted.get_data(as_text=True),
   f"status {_mounted.status_code}")

# And the blueprint-registered half, which is injected by hub/__init__.py
# rather than by HubBar — a third code path, and the one that already had to
# be told about hub-crumbs.js separately.
_bp = get("/tools/calculators/", follow_redirects=True)
ok("a blueprint-registered module gets it from the hub injector",
   "/hub-thinking.js" in _bp.get_data(as_text=True),
   f"status {_bp.status_code}")

_src = (ROOT / "wsgi.py").read_text(encoding="utf-8")
_hub = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
ok("HubBar names it", "hub-thinking.js" in _src)
ok("the hub app's injector names it", "hub-thinking.js" in _hub)


# ------------------------------------------------------- glyph and animation
section("The glyph is drawn here and moved there, and both halves arrive")

# Three kinds, and each has to draw something the other two do not — a kind
# that renders the same mark is a distinction the reader cannot see, which is
# the same as not having made it.
PARTS = {"ai": ("glyphAI", ("s1-think-star", "s1-think-tw")),
         "scan": ("glyphScan", ("s1-think-sweep", "s1-think-ping")),
         "wait": ("glyphWait", ("s1-think-arc",))}
for kind, (fn, parts) in PARTS.items():
    ok(f"{kind} is a kind the script knows", f'"{kind}"' in SCRIPT)
    body = SCRIPT[SCRIPT.index("function " + fn + "("):]
    body = body[:body.index("\n  }")]
    for part in parts:
        ok(f"{kind} draws {part}", part in body)
    for other, (_fn, others) in PARTS.items():
        if other == kind:
            continue
        for part in others:
            ok(f"{kind} does not draw {other}'s {part}", part not in body)

# Each glyph's moving parts are classed in the script and animated in the CSS.
# They are in two files on purpose — the CSS reaches a page that failed to run
# the script, so a static mark is drawn rather than an empty box — which is
# also how they come to disagree.
for part in ("s1-think-star", "s1-think-tw", "s1-think-sweep",
             "s1-think-ping", "s1-think-arc"):
    ok(f"{part} is drawn by the script", part in SCRIPT)
    ok(f"{part} is animated by the stylesheet",
       re.search(r"\.%s\b[^{]*\{[^}]*animation" % re.escape(part), CSS) is not None)

# Forty modules and no shared stylesheet between them. Inheriting the
# surrounding text color is the only way one glyph reads on a white card, a
# navy button and a dark landing page without any of them being edited — and a
# single hex literal in a glyph is that guarantee gone for one of the three.
for _kind, (_fn, _parts) in PARTS.items():
    _body = SCRIPT[SCRIPT.index("function " + _fn + "("):]
    _body = _body[:_body.index("\n  }")]
    ok(f"{_kind} is drawn in currentColor", "currentColor" in _body)
    ok(f"{_kind} names no color of its own",
       re.search(r"#[0-9a-fA-F]{3,8}\b", _body) is None)

# A spinner is invisible to a screen reader; the sentence beside it is the
# whole message, so the row has to be announced.
ok('the row is role="status"', 'role", "status"' in SCRIPT
   or '"role", "status"' in SCRIPT)
ok('and aria-live="polite"', "aria-live" in SCRIPT)
ok("the mark itself is hidden from the reader, being decoration",
   '"aria-hidden": "true"' in SCRIPT)


# ---------------------------------------------------------- reduced motion
section("Reduced motion drops the movement and keeps the mark")

_rm = CSS[CSS.index("prefers-reduced-motion") if "prefers-reduced-motion" in CSS else 0:]
_block = re.search(
    r"@media \(prefers-reduced-motion:reduce\)\{[^@]*?s1-think-star[^@]*?\}\s*\}",
    CSS, re.S)
ok("there is a reduced-motion block for the mark", _block is not None)
if _block:
    text = _block.group(0)
    ok("it stops the animation", "animation:none" in text)
    # display:none here would be the feature failing exactly where it was
    # needed: the reader still has to be told something is running.
    ok("and never hides the glyph", "display:none" not in text)


# ------------------------------------------------------- one vocabulary only
section("The kinds are written down once")

# A Python copy would need a test proving the two halves agree — the cost
# test_target_areas.py already pays for the area helpers and the creative
# classifier pays again. Three is where that stops being worth it.
_py_mirror = []
for path in list((ROOT / "hub").rglob("*.py")) + list((ROOT / "modules").rglob("*.py")):
    if "_attic" in path.parts:
        continue
    src = path.read_text(encoding="utf-8", errors="replace")
    if "s1-think-star" in src or "s1-think-sweep" in src:
        _py_mirror.append(str(path.relative_to(ROOT)))
ok("no Python file carries a second copy of the glyph", not _py_mirror,
   ", ".join(_py_mirror))


# --------------------------------------------------- it upgrades what exists
section("It upgrades the spinners that are already on the page")

# Fifty call sites across twenty modules already write `<span class="spin">`.
# Rewriting all of them is a large change with no feature at the end of it;
# upgrading them in place is what makes the next improvement land once.
ok("it looks for .spin", '".spin' in SCRIPT)
ok("and .spinner", ".spinner" in SCRIPT)
# The border those nine stylesheets draw has to be turned off, or the arc
# spins underneath the glyph.
ok("the old border is neutralised by the stylesheet",
   re.search(r"\.s1-think\{[^}]*border:0 !important", CSS) is not None)
# Client 360, the SEO client page and half the tools draw their panels from a
# fetch: a single pass at load upgrades the shell and misses everything drawn
# after it. hub-help.js already learnt this about its bubbles.
ok("it re-runs on late-rendered content", "MutationObserver" in SCRIPT)
ok("debounced, so a busy panel does not run it per mutation",
   "setTimeout" in SCRIPT and "clearTimeout" in SCRIPT)


# --------------------------------------------------------- what it must not do
section("What it refuses to claim")

_done = SCRIPT[SCRIPT.index("handle.done = function"):]
_done = _done[:_done.index("return handle;")]
# Whether the call succeeded is the caller's answer. A tick drawn here over a
# failed one is a wrong answer that looks exactly like a right one.
for word in ("Done", "Complete", "✓", "Success"):
    ok(f"done() does not write {word!r}", word not in _done)

# An indicator that breaks the page it is reporting on is worse than none.
ok("every entry point is wrapped", SCRIPT.count("catch (e)") >= 6)
ok("attach() returns a handle even with nothing to attach to",
   "if (!host || !host.appendChild) return handle;" in SCRIPT)
# A caller that ends a wait by writing over the panel has not done anything
# wrong — half this Hub draws its panels that way — so the timer has to notice.
ok("the elapsed timer stops itself when its box leaves the page",
   "isConnected === false" in SCRIPT)


# ------------------------------------------- the three client-facing pages
section("The scan pages a prospect sees carry the same glyph, inline")

# These three are served to a stranger on somebody else's website. A Hub
# script on them would be a new outbound dependency on a page whose whole job
# is to load, so the glyph is inlined — once, in a macro the three import,
# rather than as a fourth, fifth and sixth copy of the border spinner they
# each carried.
MACRO = (ROOT / "modules" / "scans" / "templates" / "_scan_mark.html").read_text()
for page in ("widget.html", "widget_audit.html", "widget_waiting.html"):
    src = (ROOT / "modules" / "scans" / "templates" / page).read_text()
    ok(f"{page} imports the shared mark", "_scan_mark.html" in src)
    ok(f"{page} no longer defines its own spinner", ".spin {" not in src)

# Inline is not licence to draw a different thing. A prospect who starts a
# scan on a client's site and a rep who starts one from Site Scans are waiting
# on the identical thing, and it should not look like two features.
for shape in ("M12 12 L12 2.8 A9.2 9.2 0 0 1 20.5 8.6 Z",):
    ok("the dish is the same path as the Hub's own", shape in MACRO and shape in SCRIPT)
ok("and sweeps at the same speed",
   "1.9s linear infinite" in MACRO and "1.9s linear infinite" in CSS)
ok("the inline copy honors reduced motion too",
   "prefers-reduced-motion" in MACRO)
ok("and tells a screen reader what is running",
   'role="img"' in MACRO and "aria-label" in MACRO)


# ------------------------------------------- the screens that say which wait
section("The screens that know which kind of wait they are, say so")

MARKED = {
    "modules/sales_builder/templates/index.html": ("ai", "scan"),
    "modules/ads_builder/templates/ads_proposal.html": ("ai", "scan"),
    "modules/ads_builder/templates/ads_generator.html": ("ai",),
    "hub/templates/client360.html": ("scan",),
}
for rel, kinds in MARKED.items():
    src = (ROOT / rel).read_text(encoding="utf-8")
    for kind in kinds:
        ok(f"{rel} marks a {kind} wait", f'data-s1-think="{kind}"' in src)

# These four call the model directly rather than through a `.spin`, so they
# name the kind in the call.
for rel in ("modules/social_planner/templates/index.html",
            "modules/gpt_ads/templates/index.html",
            "hub/templates/seo_client.html",
            "hub/static/ask-analytics.js"):
    src = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel} asks for the AI mark", '"ai"' in src or "'ai'" in src)
    # Guarded, every time: a mark that takes the message down with it is
    # worse than no mark.
    ok(f"{rel} degrades if the script is missing", "window.S1Think" in src)


print()
if _failed:
    print(f"{_failed} FAILED, {_passed} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)
print(f"{_passed} checks passed — one mark, three kinds, both halves of the "
      "app, and nothing claiming an answer it does not have")
shutil.rmtree(TMP, ignore_errors=True)
