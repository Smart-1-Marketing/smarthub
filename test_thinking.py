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


# ---------------------------------- the fourth surface: the embedded intake
section("The ad builder's embedded form draws the same two glyphs")

# modules/ad_builder/public/embed.html is the customer-facing intake form,
# served straight off the Node renderer with `frame-ancestors` set so a
# client's marketing site can frame it. It is not one of the proxy's
# PUBLIC_PATTERNS, so a prospect never reaches it through the Hub — which
# means hub-thinking.js is not on it and cannot be, for exactly the reason
# the three scan pages above inline theirs. It carries its own copy, and
# the point of this section is that "its own copy" is not license to draw
# something else: an image generation started from a client's website and
# one started from the build screen are the same call.
EMBED = (ROOT / "modules" / "ad_builder" / "public" / "embed.html").read_text()

ok("the dish is the Hub's own path",
   "M12 12 L12 2.8 A9.2 9.2 0 0 1 20.5 8.6 Z" in EMBED)
ok("and the star is too",
   "M12 3.2 13.6 9.1 19.4 10.7 13.6 12.3" in EMBED)
ok("both sweep at the same 1.9s", EMBED.count("1.9s") >= 2)
ok("reduced motion drops the movement and keeps the mark",
   "prefers-reduced-motion" in EMBED
   and "animation:none" in EMBED.replace("animation: none", "animation:none"))
ok("and a screen reader is told what is running",
   'role="img"' in EMBED and "aria-label" in EMBED)

# The wait that was two captions ping-ponging every 1.8 seconds. Past about
# four seconds that reads as a hung page: the words go round and nothing
# else changes. The elapsed line is what separates a slow answer from a dead
# one, and it is silent until six seconds for the reason the Hub's is.
ok("the caption no longer loops", "AI_CAPTIONS" not in EMBED)
ok("an elapsed line replaces it", "s1Clock(" in EMBED)
ok("silent until six seconds, like the Hub's",
   "S1_SLOW_AT = 6000" in EMBED and "SLOW_AT: 6000" in SCRIPT.replace(
       "SLOW_AT = 6000", "SLOW_AT: 6000"))
ok("and it stops itself when its box leaves the page",
   "isConnected" in EMBED)

# A JS string literal written "\\u2026" is a backslash and then u2026, so the
# customer reads the escape rather than the ellipsis — on the page they are
# looking at while they wait. Four of these were on this form.
ok("no escape sequence reaches the customer as text",
   not re.search(r"\\\\u[0-9a-fA-F]{4}", EMBED))


# ------------------------------------- the build screen, where the wait is
section("The Display Ad Builder's build screen marks its billed calls")

# The staff half of the same tool, and the screen with the longest waits in
# the Hub: two image generations and a copy draft, all billed, all tens of
# seconds. It had no mark of any kind — not a border spinner, not a class the
# upgrader could have found — only a sentence of text that did not change.
# That is the note CLAUDE.md already makes about the QA reports saying
# "Running report…" in two words with no sign it was still going, on the one
# screen where it costs money to wait.
#
# It is a blueprint on the hub app (hub/ad_builder_proxy.register), so the
# hub's own injector reaches it and window.S1Think is there. Guarded anyway,
# every time: the same file is also served straight off the renderer.
BUILD = (ROOT / "modules" / "ad_builder" / "public" / "build.html").read_text()

ok("the build screen asks for the mark", "S1Think" in BUILD)
ok("and degrades to the message alone without it",
   "window.S1Think" in BUILD)
ok("the model's own waits draw the star", "'ai'" in BUILD)
ok("somebody else's server draws the dish", "'scan'" in BUILD)
# Six waits hang off one panel and they are not alike. The kind travels with
# the message rather than every call site being edited again next time.
ok("bgBusy takes the kind rather than assuming one",
   "function bgBusy(what, kind)" in BUILD)
# Every call site, not most of them: a wait left without a kind falls back to
# the arc, which on a billed model call is the glyph for "our own database"
# and reads as the cheap wait it is not. The call spans lines, so the kind is
# looked for between the call and its closing paren rather than on one line.
_calls, _kinded = 0, 0
for _chunk in BUILD.split("bgBusy(")[1:]:
    if _chunk.startswith("what, kind)"):          # the definition itself
        continue
    _calls += 1
    _head = _chunk.split(";")[0]
    if any(k in _head for k in ("'ai'", "'scan'", "'wait'")):
        _kinded += 1
ok("and every one of its callers names one",
   _calls and _kinded == _calls, f"{_kinded} of {_calls}")

# A wait that changes hands halfway. The page is fetched (their server) and
# then a model writes from it (billed) — back to back, and one glyph for both
# says either that the model never started or that a page fetch was billed.
ok("attach() can move the glyph as well as the words",
   "handle.stage = function (text, nextKind)" in SCRIPT)
ok("an unrecognized kind changes nothing",
   "KINDS.indexOf(nextKind) >= 0" in SCRIPT)
ok("the elapsed line is not restarted by the change",
   "box.replaceChild" in SCRIPT and "started = Date.now()" not in
   SCRIPT.split("handle.stage = function (text, nextKind)")[1].split("};")[0])
ok("and the build screen uses it on both of its two-part waits",
   BUILD.count("think.stage(") >= 2)

# ensureLanding() caches on state.landing, so the second draft has no page to
# fetch. Announcing a step that is not happening is the indicator claiming
# what it does not know — the one thing it must never do.
_readings = re.findall(r"var reading = ([^;]+);", BUILD)
ok("both two-part waits decide the reading step the same way",
   len(_readings) == 2, f"found {len(_readings)}")
ok("the reading step is claimed only when there is a page to read",
   BUILD.count("var hasPage = !!state.landingPage;") == 2
   and all(r.strip() == "hasPage && !state.landing" for r in _readings),
   "; ".join(_readings))
# And the copy draft says what it is doing when there is no page at all:
# "writing from what their page says" about a campaign that has none is a
# claim about a page that does not exist, and it is the sentence a rep
# would quote back when the copy turns out to be wrong.
ok("with no landing page it does not claim to have read one",
   "'Writing your ad copy\\u2026'" in BUILD)


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

# Six modules shipped the same three-line say(id, text, kind) helper and the
# same plain-text busy state. Teaching the helper the three kinds moved every
# busy state in one edit each — and is why this list is the helper, not the
# call sites: a seventh added to one of these screens is right by default.
for rel in ("modules/social_planner/templates/index.html",
            "modules/gpt_ads/templates/index.html",
            "modules/radio_promo/templates/index.html",
            "modules/fan_radio/templates/index.html",
            "modules/site_blocks/templates/index.html",
            "modules/landing_ads/templates/index.html"):
    src = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel}'s say() knows the three kinds",
       '"ai","scan","wait"' in src.replace(" ", "")
       or "'ai','scan','wait'" in src.replace(" ", ""))
    ok(f"{rel} hands the label to the mark", "window.S1Think.attach" in src)
    # A wait that is a model and a wait that is somebody else's website are
    # different waits, and a screen that says only "working" has told the
    # reader nothing they could not already see.
    ok(f"{rel} distinguishes at least two kinds",
       len({k for k in ("'ai'", '"ai"', "'scan'", '"scan"') if k in src}) >= 2)

# And the ones that reach the model without going through a `.spin` or a say().
for rel in ("hub/templates/seo_client.html",
            "hub/static/ask-analytics.js",
            "modules/image_creator/static/editor.js",
            "modules/commercial_builder/static/js/blueprint.js",
            "hub/templates/qa_report.html"):
    src = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel} names the kind of wait it is",
       any(k in src for k in ('"ai"', "'ai'", '"scan"', "'scan'")))
    # Guarded, every time: a mark that takes the message down with it is
    # worse than no mark.
    ok(f"{rel} degrades if the script is missing", "window.S1Think" in src)


# ------------------------------- the fifth surface: the pages a client opens
section("The pages a client opens carry the mark too, inlined")

# Every client-facing surface in this Hub is chrome-free by design — CHROMELESS
# in hub/__init__.py and the PUBLIC_PREFIXES each mounted module declares —
# because injecting the staff sidebar, help layer and feedback tab into a
# document a client reads is the failure those lists exist to prevent.
# hub-thinking.js rides in with that chrome, so switching the chrome off
# switched the mark off with it, and nothing anywhere said so: a client
# approving a finished TV cut pressed a button that greyed out and said
# nothing, and a client swiping an idea on a phone got no visible change at
# all. hub/thinking.py is the inline block those pages carry instead.
import importlib
_thinking = importlib.import_module("hub.thinking")
BLOCK = str(_thinking.assets())

ok("the block defines the small sibling and not a second S1Think",
   "window.S1Wait" in BLOCK and "window.S1Think" not in BLOCK)
ok("and refuses to run twice on one page", "if (window.S1Wait) { return; }" in BLOCK)

# Inline is not licence to draw a different thing. A client waiting on a model
# and a rep waiting on the same model are waiting on one thing.
for kind, shape in (
    ("scan", "M12 12 L12 2.8 A9.2 9.2 0 0 1 20.5 8.6 Z"),
    ("ai", "M12 3.2 13.6 9.1 19.4 10.7 13.6 12.3 12 18.2 10.4 12.3 4.6 10.7 10.4 9.1Z"),
):
    ok(f"the {kind} glyph is the Hub's own path",
       shape in _thinking.mark_svg(kind) and shape in SCRIPT)
ok("the wait arc is the same dash pattern",
   'stroke-dasharray="16 38"' in _thinking.mark_svg("wait")
   and '"16 38"' in SCRIPT)
ok("and each sweeps at the speed hub-help.css sets",
   "1.9s linear infinite" in BLOCK and "1.9s linear infinite" in CSS
   and ".9s linear infinite" in BLOCK)
ok("it knows the same three kinds", list(_thinking.KINDS) == ["ai", "scan", "wait"])

# The four rules, which are the script's own and are not relaxed by being
# inlined.
ok("nothing in it may raise", BLOCK.count("catch (e)") >= 4)
ok("busy() returns a handle even when the button is not there",
   "if (!btn) { return handle; }" in BLOCK)
ok("and note() does the same for a status line",
   "if (!host) { return handle; }" in BLOCK)
# done() restores; it writes no word and draws no tick. Whether the call
# succeeded is the caller's answer, and a tick over a failed one is the
# confident wrong answer this codebase keeps undoing.
# Read with the comments taken out, or the check reports the sentence
# explaining the rule as a breach of it — the "prose is not a call site" trap
# tools/spellcheck.py reads the AST to avoid.
CODE = re.sub(r"/\*.*?\*/", " ", BLOCK, flags=re.S)
CODE = re.sub(r"(?m)^\s*//.*$", "", CODE)
ok("done() puts the button back and claims nothing",
   "btn.innerHTML = was;" in CODE
   and "Done" not in CODE and "\u2713" not in CODE and "\u2714" not in CODE)
ok("currentColor, never a palette",
   BLOCK.count("currentColor") >= 6
   and not re.search(r"(?<!&)#[0-9a-fA-F]{3,6}\b", BLOCK))
ok("reduced motion drops the movement and keeps the mark",
   "prefers-reduced-motion" in BLOCK and "animation:none !important" in BLOCK)
ok("and a screen reader is told what is running",
   'aria-live="polite"' in BLOCK or "aria-live" in BLOCK)
ok("a static mark tells one what it is",
   'role="img"' in _thinking.mark_svg("scan")
   and "aria-label" in _thinking.mark_svg("scan"))
# </script> inside a string literal would close the block the script sits in.
ok("the glyph markup cannot close its own script tag", "</script>" not in _thinking.js())

# The global has to be on the environment that renders the page, and a module's
# environment is its own — the first trap this repo names. Both registration
# paths carry it: install_template_helpers() for every mounted module, and
# register_help() for the hub app, where the blueprint-registered Commercial
# Builder renders.
HELPR = (ROOT / "hub" / "help_routes.py").read_text(encoding="utf-8")
ok("every mounted module's environment gets it",
   HELPR.count("_thinking.install(app)") == 2)

# The call sites. Guarded every time, so a module whose environment never
# received the registration loses the mark rather than the page.
CLIENT_PAGES = {
    # the four Social Content pages share one head partial rather than four
    "modules/social_planner/templates/_client_head.html": None,
    "modules/commercial_builder/templates/commercial_review.html": ("c-send", "d-send"),
    "modules/ads_builder/templates/ads_estimate.html": ("modalSend", "respondBtn"),
    "modules/sales_builder/templates/client_proposal.html": ("go",),
}
for rel in CLIENT_PAGES:
    src = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel} asks for the block",
       "s1_wait_assets()" in src)
    ok(f"{rel} guards the call",
       "if s1_wait_assets is defined" in src)

def _code_only(src):
    """The file with its comments taken out.

    Every one of these call sites explains in a comment that it is guarded on
    window.S1Wait, so a check reading the raw text is satisfied by the
    sentence describing the rule and says nothing when the rule goes. That is
    the trap tools/spellcheck.py reads the AST to avoid, and it caught this
    check being silent on a deliberately drifted file before it shipped.
    """
    out = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)          # Jinja
    out = re.sub(r"/\*.*?\*/", " ", out, flags=re.S)          # /* … */
    out = re.sub(r"(?m)^\s*//.*$", "", out)                   # a whole line
    return re.sub(r"(?m)\s//[^\n]*$", "", out)                # a trailing one


for rel in ("modules/social_planner/templates/client_ideas.html",
            "modules/social_planner/templates/client_approve.html",
            "modules/social_planner/templates/client_preferences.html",
            "modules/social_planner/templates/client_request.html",
            "modules/commercial_builder/templates/commercial_review.html",
            "modules/ads_builder/templates/ads_estimate.html",
            "modules/sales_builder/templates/client_proposal.html"):
    src = _code_only((ROOT / rel).read_text(encoding="utf-8"))
    ok(f"{rel} marks its wait", "S1Wait." in src)
    # A mark that takes the answer down with it is worse than no mark.
    ok(f"{rel} degrades if the block did not run", "window.S1Wait" in src)
    # done() is what puts the control back. A wait started and never ended is
    # a button disabled for the life of the page.
    ok(f"{rel} ends every wait it starts",
       src.count(".done()") >= src.count("S1Wait.busy(") + src.count("S1Wait.note("))

# The swipe is the one that had nothing at all: the double-tap guard returned,
# so the second tap was met by a function doing nothing, which reads as broken.
IDEAS = (ROOT / "modules" / "social_planner" / "templates" / "client_ideas.html").read_text()
ok("the swipe holds the other choice while the tap is in flight",
   "other.disabled = true" in IDEAS and "other.disabled = false" in IDEAS)


print()
if _failed:
    print(f"{_failed} FAILED, {_passed} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)
print(f"{_passed} checks passed — one mark, three kinds, both halves of the "
      "app, and nothing claiming an answer it does not have")
shutil.rmtree(TMP, ignore_errors=True)
