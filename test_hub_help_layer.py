"""The hub's own tours and walkthrough buttons: offered where they work.

    python3 test_hub_help_layer.py

Same shape as the other test files here — no pytest, no new dependencies.

## Why this file exists

Two mechanisms decide what the help layer offers on a hub page, and both were
wrong in the same direction: they offered something that could not run.

  1. **`data-screen` is what lets a tour be offered, and `base.html` had
     none.** Seven hub screens — dashboard, Client 360, creative, activity,
     leads, SEO, status — had tour steps written, registered and anchored to
     elements that exist, and not one could ever be reached. `prospect.html`
     and `website_audit.html` work only because they own their own `<body>`
     instead of extending the base template, which is why the gap was
     invisible: the layer plainly worked *somewhere*.

  2. **`data-module` floats the "Walk me through this" button, and it had a
     default.** `{{ hub_demo_module or 'hub' }}` made every *unmapped* hub page
     truthy, and four of the eight mapped entries named a module whose only
     scenario lives on a different page. Fifteen pages offered a Client 360
     walkthrough — "it highlights nothing and Do it for me silently does
     nothing, which is worse than no button", the note `hub-demo.js` already
     carries about Smart 1 Ads.

The module is *derived* now, from where the scenarios actually are, so the
hand-typed half cannot drift. The tour screen is still a table — there is no
mechanical route from "/" to `hub.dashboard` — so it is held against the
registry in **both** directions below, the rule
`check_stale_json_exemptions()` works to: an entry naming a screen with no
tour, and a hub screen with a tour that no path reaches, both fail.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TMP = tempfile.mkdtemp(prefix="s1hubhelp_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "hubhelp-test"
os.environ.setdefault("PANEL_PASSWORD", "hubhelp-test-pw")

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import re                                                        # noqa: E402

import pagecheck                                                 # noqa: E402
import wsgi                                                      # noqa: E402
from werkzeug.test import Client                                 # noqa: E402

from hub import demos, help as H                                 # noqa: E402
from hub import HUB_TOUR_SCREENS                                 # noqa: E402

client = Client(wsgi.application)
pagecheck.sign_in(client)


def body_of(path):
    b = client.get(path, follow_redirects=True).get_data(as_text=True)
    m = re.search(r"<body[^>]*>", b)
    return (m.group(0) if m else ""), b


def attr(tag, name):
    m = re.search(name + r'="([^"]*)"', tag)
    return m.group(1) if m else None


# ------------------------------------------------------------------------
section("1. The tour map names real tours, and reaches every hub tour")

for path, screen in sorted(HUB_TOUR_SCREENS.items()):
    check(f"{path} -> {screen} is a screen with tour steps", H.has_tour(screen))

# The other direction: a hub screen with steps that no path reaches is a tour
# nobody can be offered — which is the whole defect this file is about, and it
# would come back silently the next time somebody registers one.
_hub_screens = {".".join(e.key.split(".")[:-1]) for e in H.REGISTRY
                if getattr(e, "step", None)}
_hub_screens = {s for s in _hub_screens if s.startswith("hub.")}
# There are two ways a hub page can name its screen: this table, or a template
# that owns its own <body> and writes data-screen itself -- which is what
# prospect.html and website_audit.html do, and the only reason the defect this
# file is about was ever invisible. Either counts as reachable; neither
# counting is the failure.
_self_declared = set()
for _t in (ROOT / "hub" / "templates").glob("*.html"):
    for _m in re.finditer(r'data-screen="([^"{]+)"', _t.read_text(encoding="utf-8")):
        _self_declared.add(_m.group(1))
_unreachable = sorted(_hub_screens - set(HUB_TOUR_SCREENS.values())
                      - _self_declared)
check("every hub.* screen with a tour is reachable, by the table or by a "
      "template naming itself", _unreachable, [])
check("and the two that name themselves really do",
      sorted(_self_declared & _hub_screens),
      ["hub.prospect", "hub.website_audit"])

# ------------------------------------------------------------------------
section("2. Every hub tour is actually offered on its page")

for path, screen in sorted(HUB_TOUR_SCREENS.items()):
    tag, _ = body_of(path)
    check(f"{path} names its screen in the body", attr(tag, "data-screen"),
          screen)

# ------------------------------------------------------------------------
section("3. A walkthrough is offered only where one can run")

# The launcher offers the module's FIRST scenario, so a page carrying a module
# whose scenarios all live elsewhere is a button that rings nothing.
_by_module = {}
for s in demos.SCENARIOS:
    _by_module.setdefault(s.module, []).append((s.path or "/").rstrip("/") or "/")

_offenders = []
for path in list(HUB_TOUR_SCREENS) + ["/tools", "/diagnostics", "/creative",
                                      "/sales/leads", "/tools/domains",
                                      "/tools/sites-match", "/seo/webmaster"]:
    tag, _ = body_of(path)
    mod = attr(tag, "data-module") or ""
    if not mod:
        continue
    want = (path.rstrip("/") or "/")
    if want not in _by_module.get(mod, []):
        _offenders.append(f"{path} offers {mod!r}, whose scenarios are "
                          f"{_by_module.get(mod)}")
check("no hub page offers a walkthrough registered for another page",
      _offenders, [])

check("and the default that made every page truthy is gone",
      "hub_demo_module or 'hub'"
      not in (ROOT / "hub/templates/base.html").read_text(encoding="utf-8"))

# ------------------------------------------------------------------------
section("4. The webmaster dashboard explains itself")

_tag, _body = body_of("/seo/webmaster")
check("it declares its screen", attr(_tag, "data-screen"), "hub.webmaster")
for _k in ("hub.webmaster.roster", "hub.webmaster.property",
           "hub.webmaster.attach"):
    check(f"{_k} is registered", any(e.key == _k for e in H.REGISTRY))
    check(f"  and its bubble reaches the served page", _k in _body)
for _t in ("wm-roster", "wm-numbers", "wm-attach"):
    check(f"its tour step rings {_t}, which the page draws", _t in _body)

# A bubble whose key is not registered is removed client-side, so the template
# reads as helped and the screen shows nothing.
_placed = set(re.findall(r"help_dot\('([^']+)'\)",
                         (ROOT / "hub/templates/seo_webmaster.html")
                         .read_text(encoding="utf-8")))
_known = {e.key for e in H.REGISTRY}
check("every key the page places resolves in the registry",
      sorted(_placed - _known), [])
check("and each call is guarded, so a module without the global loses the "
      "icon rather than the page",
      (ROOT / "hub/templates/seo_webmaster.html").read_text(encoding="utf-8")
      .count("if help_dot is defined"), len(_placed))

# ---------------------------------------------------------------- the
# injector, which answers the same question for a page that does not extend
# base.html. It answered it from a hand-typed slug map -- a second description
# of where a scenario lives, and it had drifted in both directions: three
# entries named a module whose only scenario is written for a different page,
# and `qa` matched on the FIRST URL SEGMENT. Measured on the running app that
# put the button on /qa/client-owners and /qa/unattached-images, offering
# qa.billing_audit, whose four targets are 0 of 4 present there.
#
# A sweep rather than the two that were wrong: naming those proves nothing
# about the next page added under a prefix somebody maps.
_written = {(sc.path or "/").rstrip("/") or "/" for sc in demos.SCENARIOS}
_offered_wrongly, _swept = [], 0
for _rule in wsgi.hub_app.url_map.iter_rules():
    _p = str(_rule.rule)
    # Ending the session mid-sweep would return every page after it as the
    # sign-in form -- a sweep that quietly stops sweeping, reporting a clean
    # answer about the part it still covers. Found by measuring rather than
    # guessed: it is `/signout` that does it here, and `/logout` beside it.
    if _p in ("/signout", "/logout", "/signin", "/login"):
        continue
    if "<" in _p or "GET" not in _rule.methods or _p.startswith(("/api", "/static")):
        continue
    try:
        _r = client.get(_p, follow_redirects=True)
    except Exception:
        continue
    if _r.status_code != 200:
        continue
    # Content type, not the body: /hub-demo.js contains the string
    # `data-module` in its own source and is not a page.
    if "text/html" not in (_r.headers.get("Content-Type") or ""):
        continue
    _swept += 1
    _html = _r.get_data(as_text=True)
    _tag = re.search(r"<body[^>]*>", _html)
    _tag = _tag.group(0) if _tag else ""
    _m = re.search(r'data-module="([^"]*)"', _tag)
    _mod = _m.group(1) if _m else ""
    # Judge where the request LANDED, not where it was aimed: /seo/client with
    # no ?name= redirects to /seo, which carries its own module perfectly
    # correctly.
    _landed = getattr(getattr(_r, "request", None), "path", _p) or _p
    _landed = _landed.rstrip("/") or "/"
    # The two ways a page opts out, both of which the launcher itself honours.
    # A sweep that ignores them reports a correctly opted-out page as a
    # finding, and a check with false positives is one somebody switches off.
    _opted_out = ('data-demo="off"' in _tag or "data-demo-start" in _html)
    # A module with no scenario at all draws no button: the launcher returns
    # early on an empty list. Not this check's business, and calling it a
    # finding would start it red over a page that is right today.
    _has_any = any(sc.module == _mod for sc in demos.SCENARIOS)
    if _mod and _has_any and not _opted_out and _landed not in _written:
        _offered_wrongly.append(f"{_p} -> {_mod}")
check("every hub page was swept for a walkthrough button", _swept > 30, True)
# A sweep that signed itself out would report a clean answer about the pages
# it reached before that, which is the shape this whole check exists to catch.
check("and it still held its session at the end",
      client.get("/qa", follow_redirects=True).status_code == 200
      and b"data-module" in client.get("/qa", follow_redirects=True).get_data(),
      True)
check("no page offers a walkthrough written for a different page",
      sorted(_offered_wrongly), [])

# The other direction: a page a walkthrough WAS written for must still carry
# it, or removing the map would have retired the feature rather than aimed it.
for _path, _mod in (("/qa", "qa"), ("/tools/tickets/", "tickets"),
                    ("/tools/calculators/", "calculators"),
                    ("/tools/image-picker/", "image_picker")):
    _t, _ = body_of(_path)
    check(f"{_path} still offers its own walkthrough", attr(_t, "data-module"), _mod)

# client_owners.html declares no module on purpose and CLAUDE.md says why.
# The injector put one back, which is the opt-out being overruled by the
# thing it was opted out of.
_t, _ = body_of("/qa/client-owners")
# Absent and empty are both "no module" to the launcher, which tests the
# attribute for truthiness. The template declares none, so after the fix there
# is no attribute at all -- which is the opt-out being left alone rather than
# overruled with an empty one.
check("a page that opted out of the launcher stays opted out",
      attr(_t, "data-module") or "", "")

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
