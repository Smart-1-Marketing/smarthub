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

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
