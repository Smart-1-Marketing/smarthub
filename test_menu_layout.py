"""The Creative and Client Tools menus, and the internal calculators.

    python3 test_menu_layout.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

Three failures, all of which look like a working page.

**A tool with no tile is invisible.** This codebase counts six that were, for
weeks. Reorganising two index pages is precisely the operation that loses one:
the tiles are hand-authored anchors in two templates, a tile moved between them
is a cut and a paste, and a paste that did not happen leaves a tool that still
boots, still answers, still passes linkcheck, and that nobody can reach. So
every tool named in the reshuffle is asserted to be tiled *somewhere* — and the
ones that moved are asserted to have left the page they moved off, because a
tile in two places is a tile that gets updated in one.

**The internal calculator computes the same numbers and must capture nothing.**
It exists because the public copy's gate is right in front of a prospect and is
pure friction on our own screen — and because a rep who typed something into
that form to get past it wrote a contact into the leads panel that reads exactly
like a real prospect. If /internal ever wrote a row, the panel would fill with
staff sizing buys for clients we already have, and nothing on any screen would
say so.

**A blueprint on the hub app is not behind AuthGuard.** `wsgi.py` wraps only
dispatcher-mounted modules, and the hub app has no blanket gate of its own, so
`/tools/calculators/leads` — real people's names, emails and phone numbers —
answered 200 to anyone with the URL. The guard now sits on the blueprint rather
than on each view, the arrangement modules/commercial_builder arrived at for the
same reason; what this file holds is both halves of it, because a guard that
also refuses the embedded calculator is a broken embed on a client's website.
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1menu_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))
os.environ.setdefault("SECRET_KEY", "menu-layout-test-secret")

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


CREATIVE = (ROOT / "hub" / "templates" / "creative.html").read_text(encoding="utf-8")
TOOLS = (ROOT / "hub" / "templates" / "tools.html").read_text(encoding="utf-8")


def group_span(src, label):
    """The slice of a template between one group label and the next.

    A tile being *on the page* is not the question — the question is which
    group it is in, and a tile that drifted into the group below it is exactly
    as hard to find as one that is missing.
    """
    start = src.find(f'<div class="qa-group-label">{label}</div>')
    if start < 0:
        return ""
    nxt = src.find('<div class="qa-group-label">', start + 10)
    return src[start:nxt if nxt > -1 else len(src)]


# ------------------------------------------------ 1. Creative, by category
section("Creative is grouped by what you are making")

IMAGES = [
    ("Display Ad Builder", "/tools/display-ads/_hub/start"),
    ("Image Creator", "/tools/image-creator/"),
    ("Image Optimizer &amp; Resizer", "/tools/image/"),
    ("Background Remover", "/tools/bg-remover/"),
    ("Page Image Optimizer", "/tools/page-images/"),
    ("Client Image Uploads", "/tools/image-picker/"),
    ("SEO Image Pipeline", "/tools/seo-images/"),
    ("Landing Page Ads", "/tools/landing-ads/"),
]
VIDEOS = [("Commercial Builder", "/tools/commercial-builder/"),
          ("Video Search", "/tools/video-backgrounds/")]
AUDIO = [("Radio Promo", "/tools/radio-promo/"),
         ("Fan Radio", "/tools/fan-radio/")]

for label, tiles in (("Images", IMAGES), ("Videos", VIDEOS), ("Audio", AUDIO)):
    span = group_span(CREATIVE, label)
    check(f"the {label} group exists", bool(span), True)
    for name, href in tiles:
        check(f"  {name} is in {label}",
              f"<h3>{name}</h3>" in span and f'href="{href}"' in span, True)

# Not in the reshuffle's list and deliberately kept: it is where the imagery
# every image tool starts from comes from, and test_stock_search.py asserts the
# tile too. Dropping a tile to match a list exactly is how a tool goes dark.
check("Stock Photo Search kept its tile rather than being dropped",
      "<h3>Stock Photo Search</h3>" in group_span(CREATIVE, "Images"), True)

check("the tiles are four across", 'class="tool-tiles compact"' in CREATIVE, True)
# Headline only. A <p> inside a tile is the description the reshuffle removed;
# the group blurbs outside the tile grid are a different thing and stay.
tile_bodies = CREATIVE.split('<a class="tool-tile"')[1:]
check("and no tile carries a description",
      any("<p>" in body.split("</a>")[0] for body in tile_bodies), False)

for name in ("GPT Ads Builder", "Social Content Planner"):
    check(f"{name} has left Creative", f"<h3>{name}</h3>" in CREATIVE, False)


# -------------------------------------- 1b. Every creative tool hides the menu
section("Every creative tool opens with the nav as an icon rail")

# A creative tool is a workbench and the nav is 224px of a laptop the work
# needs. The list that decides it is hub/sidebar.CREATIVE_PREFIXES, read by all
# three renderers of the nav -- the hub app's injector, HubBar for the twenty
# mounted modules, and the hub_sidebar global base.html calls.
#
# This asserts the two lists agree *in both directions*, because each way of
# drifting is its own quiet failure. A tile with no prefix is a creative tool
# that opens with a nav nobody asked for while every tool beside it behaves
# differently. A prefix with no tile is a rail on a page nobody calls creative,
# which reads as the menu breaking.
from hub.sidebar import CREATIVE_PREFIXES, collapses_by_default  # noqa: E402

tiled = sorted({("/" + h.strip("/")) for h in
                re.findall(r'<a class="tool-tile" href="([^"]+)"', CREATIVE)})
for href in tiled:
    check(f"  {href} hides the menu", collapses_by_default(href), True)

# The other direction. A prefix is claimed by a tile when the tile's path is
# the prefix or sits underneath it -- /tools/display-ads/_hub/start is the
# Display Ad Builder's tile and /tools/display-ads is the tool.
for prefix in CREATIVE_PREFIXES:
    claimed = any(h == prefix or h.startswith(prefix + "/") for h in tiled)
    check(f"  {prefix} is a tool tiled on Creative", claimed, True)

# The index itself is not a workbench, and neither is anything outside the list.
check("/creative itself keeps its menu", collapses_by_default("/creative"), False)
check("Client 360 keeps its menu", collapses_by_default("/client360"), False)
check("and the Proposal Builder still asks for the rail its own way",
      'data-s1hub-collapse="1"' in
      (ROOT / "modules" / "sales_builder" / "templates" / "index.html")
      .read_text(encoding="utf-8"), True)
# Segment-matched, not startswith: /tools/image must not claim a tool that
# merely begins with those letters.
check("a neighboring path is not swept in",
      collapses_by_default("/tools/imagery-report"), False)


# -------------------------------------------------- 2. Client Tools, regrouped
section("Client Tools is grouped the same way")

SALES = [("Proposal Builder", "/sales/builder/"), ("IO Builder", "/tools/io/"),
         ("Landing Page Maker", "/sales/landing"),
         ("Master Services Agreement", "/msa/"), ("PDF Optimizer", "/tools/pdf/")]
MEDIA = [("Smart 1 Ads", "/tools/ads/"),
         ("Digital Audio Calculator", "/tools/calculators/internal/digital-audio"),
         ("Connected TV Calculator", "/tools/calculators/internal/ctv"),
         ("DOOH Calculator", "/tools/calculators/internal/dooh"),
         ("IMS Calculator", "/tools/calculators/internal/trade")]
CONTENT = [("GPT Ads Builder", "/tools/gpt-ads/"),
           ("Social Content Planner", "/tools/social/")]

for label, tiles in (("Sales", SALES), ("Media Tools", MEDIA), ("Content", CONTENT)):
    span = group_span(TOOLS, label)
    check(f"the {label} group exists", bool(span), True)
    for name, href in tiles:
        check(f"  {name} is in {label}",
              f"<h3>{name}</h3>" in span and f'href="{href}"' in span, True)

# The gated, publishable ones. They are a landing page that does arithmetic,
# which is why they sit with the landing pages and not beside the internal copies.
check("Media Calculators moved to Landing Pages",
      '<h3>Media Calculators</h3>' in group_span(TOOLS, "Landing Pages"), True)

check("the tiles are four across", 'class="tool-tiles compact"' in TOOLS, True)
tool_bodies = TOOLS.split('<a class="tool-tile"')[1:]
check("and no tile carries a description",
      any("<p>" in body.split("</a>")[0] for body in tool_bodies), False)

# Moved to QA. Left here as well, each would be two tiles for one report and
# only one of them would be maintained.
for name in ("Scan All Clients", "Match Google Accounts", "Match Sites to Clients",
             "Web Tickets", "Domain Renewals", "Campaign Assets Needed",
             "Display Ad Builder"):
    check(f"{name} has left Client Tools", f"<h3>{name}</h3>" in TOOLS, False)

check("and the Client Work group went with them, rather than standing empty",
      '<div class="qa-group-label">Client Work</div>' in TOOLS, False)


# ------------------------------------------------------ 3. they landed on QA
section("What left Client Tools arrived on QA Reports")

# The composed app, not the hub app on its own. Half of what moved to QA is a
# dispatcher-mounted module (/scans/bulk), so a hub-app-only client would report
# the link dead when it is fine -- and mount shadowing, the trap this codebase
# has hit three times, is invisible from either app alone.
from werkzeug.test import Client  # noqa: E402

import wsgi  # noqa: E402
from hub import auth  # noqa: E402

client = Client(wsgi.application)
client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

qa = client.get("/qa")
check("/qa renders", qa.status_code, 200)
qa_body = qa.get_data(as_text=True)

MOVED = [("Scan All Clients", "/scans/bulk"),
         ("Match Sites to Clients", "/tools/sites-match"),
         ("Match Google Accounts", "/tools/google-match"),
         ("Campaign Assets Needed", "/tools/campaign-assets"),
         ("Domain Renewals", "/tools/domains"),
         ("Web Tickets", "/tools/tickets/")]
for name, href in MOVED:
    check(f"{name} is on QA", name in qa_body and f'href="{href}"' in qa_body, True)

# Each keeps its own URL. Moving a tile must not move a page: every Client 360
# crumb, every bookmark and every link in this repo still points at these.
for _, href in MOVED:
    check(f"{href} still answers where it always did",
          client.get(href, follow_redirects=False).status_code in (200, 302, 303), True)


# --------------------------------------- 4. the internal calculator captures nothing
section("The internal calculator computes the same numbers and stores nothing")

from modules.calculators import catalog, store  # noqa: E402

for slug in ("digital-audio", "ctv", "dooh", "trade"):
    r = client.get(f"/tools/calculators/internal/{slug}")
    check(f"/internal/{slug} renders", r.status_code, 200)
    body = r.get_data(as_text=True)
    check(f"  it says it is the internal one", "no lead created" in body, True)
    check(f"  and it draws no contact form",
          'id="gateForm"' in body or 'name="email"' in body, False)

before = len(store.leads(limit=1000, only_unlocked=False))
run = client.post("/tools/calculators/internal/digital-audio/run",
                  json={"inputs": {"budget": 3000, "weeks": 4, "tier": "22",
                                   "frequency": 6, "placement": "Streaming Audio",
                                   "radius": "25 miles", "goal": "Awareness"}})
check("the run answers", run.status_code, 200)
data = run.get_json()
check("  ok", data["ok"], True)
check("  with the headline metrics", len(data["metrics"]) > 0, True)
# The whole point: the detail the public path withholds arrives in the same
# response, with no contact traded for it.
check("  and the full plan in the same response",
      bool(data["detail"].get("summary")) and bool(data["detail"].get("table")), True)
check("  including the next steps", len(data["detail"].get("next_steps") or []), 3)

check("nothing was written to the leads table",
      len(store.leads(limit=1000, only_unlocked=False)), before)

# Same compute function, so the numbers cannot drift from the client-facing copy.
direct = catalog.run("digital-audio", {"budget": 3000, "weeks": 4, "tier": "22",
                                       "frequency": 6, "placement": "Streaming Audio",
                                       "radius": "25 miles", "goal": "Awareness"})
check("the numbers are the public calculator's numbers",
      data["metrics"], direct["metrics"])

check("a bad input is refused in words, not with a 500",
      client.post("/tools/calculators/internal/digital-audio/run",
                  json={"inputs": {"budget": 0}}).status_code, 400)
check("an unknown calculator is a 404",
      client.get("/tools/calculators/internal/nope").status_code, 404)

# Every calculator in the catalogue has a page, tile or no tile: one that
# exists and cannot be opened is the worse of the two failures.
for calc in catalog.all_calculators():
    check(f"{calc['slug']} has an internal page",
          client.get(f"/tools/calculators/internal/{calc['slug']}").status_code, 200)


# --------------------------------------------- 5. staff only, embed untouched
section("The staff routes are behind the login and the embed is not")

anon = Client(wsgi.application)
STAFF = ("/tools/calculators/", "/tools/calculators/leads",
         "/tools/calculators/internal/ctv")
for path in STAFF:
    r = anon.get(path, follow_redirects=False)
    check(f"{path} refuses an anonymous request", r.status_code in (301, 302, 303), True)
    check(f"  ...by sending them to sign in", "/login" in r.headers.get("Location", ""), True)

r = anon.post("/tools/calculators/internal/ctv/run", json={"inputs": {}})
check("so does the internal run route", r.status_code in (301, 302, 303), True)

# The other half, and the one that breaks a client's website if it goes wrong:
# the embedded calculator has no Hub session and must never be asked for one.
for path in ("/tools/calculators/c/trade", "/tools/calculators/embed/trade",
             "/tools/calculators/embed.js"):
    check(f"{path} is still public", anon.get(path).status_code, 200)
check("and so is its estimate API",
      anon.post("/tools/calculators/api/trade/estimate",
                json={"inputs": {"cash": 5000, "trade": 5000}}).status_code in (200, 400), True)

# The internal route is deliberately not under /api/: that prefix is public by
# declaration, so a route added there is a route outside the login — and this
# one answers with the plan the public path withholds.
from modules import calculators as _calc  # noqa: E402
check("the internal prefix is not on the module's public list",
      any("/internal".startswith(p) or p.startswith("/internal")
          for p in _calc.PUBLIC_PREFIXES), False)

from hub import suite_embed as embed  # noqa: E402
check("and it is not framable from another domain",
      embed.embeddable("/tools/calculators/internal/ctv"), False)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
