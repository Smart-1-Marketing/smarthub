"""One description of what a record page looks like, and three pages using it.

    python3 test_detail_ui.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway database, so it never
touches /var/data or the real one.

## Why this file exists

The SEO client page carried the Hub's record-page look as ~90 lines of `.seoc-*`
rules inside its own template, so the three module screens beside it each grew
their own: Sites Admin in near-navy with a branded header bar of its own, the
Suite panel with a second one, and the client lookup in the old near-black and
lime green. Four screens of one product, three palettes.

That is a design problem and it is also a drift problem, which is the half a
test can hold. The primitives are in hub/static/hub-detail.css now, declared
once under both the `s1d-` names the modules use and the `seoc-` names the SEO
page already used — so what this file asserts is that the one description is
still one:

  * the shared sheet is **reachable and linked from both halves of the app** —
    hub pages get it from base.html, the twenty mounted modules from wsgi.py's
    HubBar, and a sheet linked from only one of those is a look that stops at
    the module boundary with nothing saying so;

  * the SEO page **does not restate** the rules it handed over. A copy left
    behind is not a broken page, it is a page that silently stops matching the
    others the next time one of them is edited;

  * the three pages **carry the shared class names**, and no longer carry the
    branded header bars that made them read as separate products;

  * the client lookup's override sheet is **scoped**. Its class names are
    ordinary words — .kpi, .badge, .tabs, .search — and an unscoped `.badge`
    rule would restyle a module nobody was thinking about;

  * `status_class()` returns the **shared pill vocabulary**, and an unknown
    status is gray rather than red: a status this app has never seen is not a
    bad status.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ui_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))
os.environ.setdefault("SECRET_KEY", "detail-ui-test-secret")

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


SHEET = (ROOT / "hub" / "static" / "hub-detail.css").read_text(encoding="utf-8")
BASE = (ROOT / "hub" / "templates" / "base.html").read_text(encoding="utf-8")
WSGI = (ROOT / "wsgi.py").read_text(encoding="utf-8")
SEO = (ROOT / "hub" / "templates" / "seo_client.html").read_text(encoding="utf-8")


# ------------------------------------------------- 1. one description of it
section("The look is one stylesheet, reached from both halves of the app")

check("hub/static/hub-detail.css exists", bool(SHEET.strip()), True)
check("the hub's own pages link it", "/assets/hub-detail.css" in BASE, True)
# The two halves: a sheet linked only from base.html reaches no mounted module,
# and one injected only by HubBar reaches none of the hub's own pages.
check("and HubBar injects it into every mounted module",
      "/assets/hub-detail.css" in WSGI, True)

# Every primitive is declared for both vocabularies in the same rule. Two rules
# with the same body is the drift this file exists to stop.
for s1d, seoc in (("s1d-head", "seoc-head"), ("s1d-btn", "seoc-btn"),
                  ("s1d-card", "seoc-card"), ("s1d-kv", "seoc-kv"),
                  ("s1d-cardhead", "seoc-cardhead"), ("s1d-muted", "seoc-muted"),
                  ("s1d-tile", "seoc-tile"), ("s1d-pill", "seoc-pill"),
                  ("s1d-crumb", "seoc-crumb")):
    check(f".{s1d} and .{seoc} are declared together",
          f".{s1d}, .{seoc}" in SHEET or f".{s1d}, ." in SHEET and f".{seoc}" in SHEET, True)


# ------------------------------------- 2. the page it came from let go of it
section("The SEO client page no longer restates what it handed over")

# The exact declarations that moved. Left behind, the page keeps working and
# quietly stops matching the modules the moment either copy is edited.
for gone in (
    ".seoc-btn{border:0;background:#2563eb",
    ".seoc-card h3{margin:0 0 10px",
    ".seoc-kv{display:grid;grid-template-columns:150px 1fr",
    ".seoc-cardhead{display:flex;justify-content:space-between",
    ".seoc-muted{color:#64748b",
    ".seoc-tile{background:#f8fafc",
    "table.seoc-t{width:100%",
    ".seoc-tablehead{display:flex",
):
    check(f"{gone[:34]}… is not restated", gone in SEO, False)

# It still uses them, which is the point — the look did not leave the page.
for used in ("seoc-head", "seoc-card", "seoc-kv", "seoc-btn", "seoc-muted"):
    check(f"the page still renders with .{used}", used in SEO, True)


# ------------------------------------------------- 3. the three pages adopt it
section("Sites, Suite and Clients read as the same product")

SITES_BASE = (ROOT / "modules" / "sites_admin" / "templates" / "base.html").read_text(encoding="utf-8")
SITES_CSS = (ROOT / "modules" / "sites_admin" / "static" / "styles.css").read_text(encoding="utf-8")
SUITE = (ROOT / "modules" / "suite_panel" / "public" / "index.html").read_text(encoding="utf-8")

# A second branded bar beside the Hub's own sidebar is chrome twice, and is
# what made each of these read as a separate product.
check("Sites no longer ships its own branded header bar",
      "Smart 1 Sites Admin" in SITES_BASE, False)
check("...and its stylesheet no longer styles one",
      "header{height:62px" in SITES_CSS.replace(" ", ""), False)
check("Suite no longer ships one either", "<header>" in SUITE, False)
check("...nor styles one", "header .brand" in SUITE, False)

# What each of them does still need: a second level of navigation.
check("Sites keeps its sections, as the shared tab strip",
      "s1d-subnav" in SITES_BASE, True)
check("Suite keeps its tabs, as the same strip", "tabs s1d-subnav" in SUITE, True)
# The script drives those tabs and has written `active` since they were an
# underline bar. The stylesheet accommodates the page, not the other way round.
check("and the strip answers to the class the script actually writes",
      ".s1d-subnav button.active" in SHEET, True)

for name, src in (("Sites dashboard", ROOT / "modules/sites_admin/templates/dashboard.html"),
                  ("Sites project detail", ROOT / "modules/sites_admin/templates/project_detail.html"),
                  ("Sites inventory", ROOT / "modules/sites_admin/templates/inventory.html"),
                  ("Sites packages", ROOT / "modules/sites_admin/templates/packages.html")):
    body = src.read_text(encoding="utf-8")
    check(f"{name} opens with the shared page head", "s1d-head" in body, True)
    check(f"{name} uses the shared card", "s1d-card" in body, True)
    # The classes each page used to carry its own version of.
    for old in ('class="card"', 'class="titlebar"', 'class="primary"', 'class="metric"'):
        check(f"  {name} dropped {old}", old in body, False)

check("Suite uses the shared card", SUITE.count("s1d-card") > 0, True)
check("Suite uses the shared button", SUITE.count("s1d-btn") > 0, True)
for old in ("btn-primary", "btn-ghost", "btn-danger", 'class="card"'):
    check(f"  Suite dropped {old}", old in SUITE, False)


# ------------------------------------------------ 4. the prebuilt bundle
section("The client lookup is restyled without being rebuilt")

CLIENTS = (ROOT / "hub" / "static" / "clients-theme.css").read_text(encoding="utf-8")
HUBPY = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")

check("the override sheet exists", bool(CLIENTS.strip()), True)
check("and /clients links it", "/assets/clients-theme.css" in HUBPY, True)
_snip = HUBPY[HUBPY.index("snippet = ("):]
_snip = _snip[:_snip.index(")\n")]
check("...after theme.css in the same snippet, so equal rules here win",
      _snip.index("/assets/theme.css") < _snip.index("/assets/clients-theme.css"), True)
check("the body carries the class the sheet is scoped to",
      'class="s1-clients"' in HUBPY, True)

# Every rule scoped. .badge, .kpi, .tabs and .search are ordinary words, and an
# unscoped rule for one of them restyles a module nobody was thinking about.
unscoped = [ln.strip() for ln in CLIENTS.splitlines()
            if ln.strip().startswith(".") and "{" in ln]
check("no rule in it is unscoped", unscoped, [])
selectors = [ln.strip() for ln in CLIENTS.splitlines() if "{" in ln and not ln.strip().startswith(("/*", "*"))]
check("and every selector that opens a rule starts at the body class",
      [s for s in selectors if not s.startswith("body.s1-clients")], [])

# The bundle is prebuilt: it must not have been edited, because there is no
# source in this repo to regenerate it from.
bundle = ROOT / "clients_app" / "static" / "css" / "main.ac5ad018.css"
check("the compiled bundle still declares the palette this overrides",
      "--s1-green:#7ac043" in bundle.read_text(encoding="utf-8"), True)


# --------------------------------------------- 5. one vocabulary for state
section("A status pill says the same thing everywhere")

# wsgi first: modules/sites_admin/app.py imports its own `config` module,
# which is only importable once the composed app has put that directory on
# sys.path. Importing it directly is how a test comes to fail for a reason
# that has nothing to do with what it is asserting.
import wsgi  # noqa: E402,F401
from modules.sites_admin.app import status_class  # noqa: E402

check("ACTIVE is the shared ok", status_class("ACTIVE"), "ok")
check("TRIAL is the shared warn", status_class("TRIAL"), "warn")
check("EXPIRED is the shared bad", status_class("EXPIRED"), "bad")
# The one that matters: an unrecognized status is not a failing one.
check("a status we do not recognize is gray, not red", status_class("PROVISIONING"), "")
check("and so is no status at all", status_class(None), "")
for mod in ("ok", "warn", "bad"):
    check(f"  .s1d-pill.{mod} is a class the shared sheet defines",
          f".s1d-pill.{mod}" in SHEET, True)


# ------------------------------------------------------ 6. it all renders
section("The pages render, with the chrome and the stylesheet on them")

from werkzeug.test import Client  # noqa: E402

from hub import auth  # noqa: E402

client = Client(wsgi.application)
client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

check("the shared sheet is served", client.get("/assets/hub-detail.css").status_code, 200)
check("and the client override sheet too", client.get("/assets/clients-theme.css").status_code, 200)

for path, want in (("/suite/", "s1d-card"), ("/clients", "s1-clients")):
    r = client.get(path)
    body = r.get_data(as_text=True)
    check(f"{path} renders", r.status_code, 200)
    check(f"  it carries {want}", want in body, True)
    # The Hub's own sidebar, which is why the modules' branded bars had to go.
    check("  and the Hub sidebar is on it", "s1hub-sb" in body, True)

# Sites needs Postgres to start and serves a 503 fallback without one, so this
# asserts what is true either way rather than skipping: the module is mounted,
# and whatever it answers carries the shared stylesheet from HubBar.
r = client.get("/sites/")
body = r.get_data(as_text=True)
check("/sites/ answers", r.status_code in (200, 503), True)
check("  with the shared stylesheet injected", "hub-detail.css" in body, True)
if r.status_code == 200:
    for want in ("s1d-subnav", "s1d-head", "s1d-tile", "s1d-t"):
        check(f"  and it renders {want}", want in body, True)
else:
    print("  note  /sites/ served its 503 fallback (no Postgres here); "
          "the templates are asserted above and by pagecheck under CI's Postgres")



# ============================================================ the wider sweep
section("Every staff module reads from the one description")

# The three code paths a stylesheet has to travel to reach the whole Hub.
# base.html covers the hub's own pages, wsgi.py's HubBar covers the twenty
# dispatcher-mounted modules, and the hub app's own injector covers the
# blueprints registered on it. A sheet added to two of the three reaches
# neither Google Access, the Image Picker, Page Image Optimizer, Tickets, the
# Calculators, Video Search nor the Commercial Builder -- which is exactly what
# happened: adopting the shared look on those pages did nothing at all until
# the third injector carried it. hub-thinking.js names this same split.
check("base.html links it (the hub's own pages)", "/assets/hub-detail.css" in BASE, True)
check("HubBar injects it (the mounted modules)", "/assets/hub-detail.css" in WSGI, True)
check("and the hub app injects it (the blueprints)",
      "/assets/hub-detail.css" in HUBPY, True)

# Google Finder's nav was hand-copied into six templates, two of which had
# drifted to a different comment header. One partial now, active from the path.
GF = ROOT / "modules" / "google_finder" / "templates"
check("Google Finder has one nav, not six",
      (GF / "_nav.html").exists(), True)
check("...and no template still carries a copy of it",
      any("nav-anchor-bar" in p.read_text(encoding="utf-8") for p in GF.glob("*.html")), False)
check("...marked active from the request, not passed in by each view",
      "request.path" in (GF / "_nav.html").read_text(encoding="utf-8"), True)

# Site Scans spoke three dialects across five staff templates.
SCANS = ROOT / "modules" / "scans" / "templates"
for fn in ("scans.html", "bulk.html", "scan_detail.html", "widgets.html"):
    body = (SCANS / fn).read_text(encoding="utf-8")
    check(f"scans/{fn} adopts the page layer", "s1d-page" in body, True)
# Read the :root block, not the file. Prose is not a declaration -- the note
# left in that template explains the cyan it no longer uses, and the widget's
# own default accent colour for the *client-facing* embed is still that value,
# correctly. The same distinction tools/spellcheck.py makes when it reads the
# AST rather than matching text.
import re as _re  # noqa: E402
_wid = (SCANS / "widgets.html").read_text(encoding="utf-8")
_root = _re.search(r":root\s*\{[^}]*\}", _wid)
check("Scan Widgets no longer declares the off-palette navy and cyan",
      bool(_root) and ("#0A2240" in _root.group(0) or "#009ED2" in _root.group(0)), False)
check("...and declares the Hub's own values instead",
      bool(_root) and "#1a2e58" in _root.group(0), True)

# The client-facing half of that module is deliberately untouched: those pages
# are served to a stranger on somebody else's website.
for fn in ("widget.html", "widget_audit.html", "widget_report.html",
           "widget_waiting.html", "_scan_mark.html"):
    body = (SCANS / fn).read_text(encoding="utf-8")
    check(f"scans/{fn} is left alone -- a prospect sees it", "s1d-page" in body, False)

# Everything else that adopted the layer.
ADOPTED = [
    "modules/ads_builder/templates/ads_base.html",
    "modules/seo_images/templates/index.html",
    "modules/seo_images/templates/gallery.html",
    "modules/seo_images/templates/house.html",
    "modules/social_planner/templates/index.html",
    "modules/social_planner/templates/staff_requests.html",
    "modules/stock_photos/templates/stock_photos.html",
    "modules/utm_builder/templates/index.html",
    "modules/site_blocks/templates/index.html",
    "modules/gpt_ads/templates/index.html",
    "modules/landing_ads/templates/index.html",
    "modules/bg_remover/templates/index.html",
    "modules/radio_promo/templates/index.html",
    "modules/radio_promo/templates/library.html",
    "modules/fan_radio/templates/index.html",
    "modules/fan_radio/templates/library.html",
    "modules/page_image_optimizer/templates/page_images.html",
    "modules/google_access/templates/_admin_base.html",
]
for rel in ADOPTED:
    body = (ROOT / rel).read_text(encoding="utf-8")
    name = rel.split("/")[1]
    check(f"{name} adopts the page layer", "s1d-page" in body, True)
    # The rules it handed over. Left behind they are a second description that
    # nobody can tell is dead, and the next person edits the wrong one.
    for gone in (".card {", ".card{"):
        if gone in body:
            check(f"  {name} no longer restates .card", False, True)
            break
    else:
        check(f"  {name} no longer restates .card", True, True)

# The client-facing templates in those same modules, untouched.
for rel in ("modules/social_planner/templates/client_approve.html",
            "modules/social_planner/templates/client_ideas.html",
            "modules/ads_builder/templates/ads_estimate.html",
            "modules/sales_builder/templates/client_proposal.html",
            "modules/calculators/templates/calculators_calculator.html",
            "modules/google_access/templates/connect.html"):
    p2 = ROOT / rel
    if not p2.exists():
        continue
    check(f"{rel.split('/')[-1]} is left alone -- a client opens it",
          "s1d-page" in p2.read_text(encoding="utf-8"), False)

# The page layer must not touch the Hub's own injected controls. hub-help.js
# renders a help bubble as a <button>, so a bare `button` rule turned every
# help dot on every adopting module into a pill at once.
check("the page layer excludes the help, tour and demo controls",
      all(t in SHEET for t in ('s1-help', 's1-tour', 's1-demo')), True)


# ------------------------------- 7. adopting by name, where the layer is wrong
section("A module with a vocabulary of its own takes the primitives, not the layer")

SALES = (ROOT / "modules/sales_builder/templates/index.html").read_text(encoding="utf-8")

# The finding was chrome twice: this module is mounted inside the Hub, so the
# page already carries the Hub's sidebar, and a sticky navy bar reading
# SMART 1 SALES BUILDER above it is what made the Proposal Builder read as a
# separate product standing beside Client 360.
check("the Proposal Builder no longer ships a branded topbar",
      ".topbar{" in SALES, False)
check("...nor the brand lockup that sat on it", 'class="brand"' in SALES, False)

# What it genuinely needs survives: four views are a second level of navigation.
check("the four views are the shared tab strip", "topnav s1d-subnav" in SALES, True)
# nav() selects on the id and writes the class, so both are the page's to name.
check('...keeping the id nav() selects on', 'id="topnav"' in SALES, True)
check("...and the class nav() writes", "'on'" in SALES or '"on"' in SALES, True)
check("the strip answers to that class", ".s1d-subnav a.on" in SHEET, True)

# The rep's name is the attribution written onto every proposal built here, so
# it is a control rather than decoration -- and it sits in the strip, which is
# why the page button rule has to leave a sub-nav button alone.
check("the rep chip is a control in the strip", 'class="repchip"' in SALES, True)
check("and a sub-nav button is not styled as a page button",
      ".s1d-subnav *" in SHEET, True)

# The stat row is the Hub's tile. Only the category stripe stays local.
check("the dashboard numbers are the shared tile", "s1d-tile" in SALES, True)
check("...and the module's own stat card is gone", ".stat{" in SALES, False)
# The Proposal Builder writes the label above the number rather than under it.
check("the tile takes a label written above the number",
      ".s1d-tile .lab" in SHEET, True)

# And what it deliberately does NOT take. `.s1d-page button` carries three
# :not()s, so it outranks every one of this module's six single-class button
# names: taking the element layer would have rendered Back, Cancel and every
# secondary button as solid brand blue, on a page where "Back" and "Convert to
# IO" would then look like the same offer.
# Asked of the class attributes rather than of the text: the reason it is not
# taken is written in a comment in that template, and a check a file's own
# explanation of itself can fail is one somebody deletes.
def _wears(html, cls):
    return any(cls in a.split() for a in _re.findall(r'class="([^"]*)"', html))


check("it does not take the page element layer", _wears(SALES, "s1d-page"), False)
for name in ("btn-primary", "btn-gold", "btn-line", "btn-ghost", "btn-good", "btn-back"):
    check(f"...so .{name} still means what the page says it means",
          "." + name + "{" in SALES, True)

# The other half: a page with no vocabulary to lose takes the layer happily.
PROJ = (ROOT / "modules/image_creator/templates/projects.html").read_text(encoding="utf-8")
check("the image projects list takes the layer", "s1d-page" in PROJ, True)
check("...and the shared page head", "s1d-head" in PROJ, True)
check("...and the shared button", "s1d-btn" in PROJ, True)
check("...and no longer styles its own", ".btn{" in PROJ, False)
# A gallery tile is a thumbnail with a name under it, which is a different
# thing from the card the rest of the Hub means by that word -- and `.card` is
# in the shared page layer, so the two would have collided.
check("the gallery tile is renamed, not left to collide with the shared card",
      ".proj{" in PROJ and 'class="card"' not in PROJ, True)

# The editor is a full-height canvas workbench with its own toolbar and tool
# rail -- the shape the Hub collapses its sidebar for. It adopts none of this.
EDITOR = (ROOT / "modules/image_creator/templates/index.html").read_text(encoding="utf-8")
check("the canvas editor is left alone", _wears(EDITOR, "s1d-page"), False)


# ------------------------------------ 8. the last two second branded bars
section("Chrome twice, on the two tools that still shipped it")

CBL = (ROOT / "modules/commercial_builder/templates/_layout.html").read_text(encoding="utf-8")
CBC = (ROOT / "modules/commercial_builder/static/css/commercial-builder.css").read_text(encoding="utf-8")
IOB = (ROOT / "modules/io_builder/templates/index.html").read_text(encoding="utf-8")

# Both are the Proposal Builder's finding again: a bar carrying the tool's own
# name, above the Hub's sidebar that is already on the page.
#
# Read as markup and as rules rather than as text: the reason each bar is gone
# is written in a comment in the file it left, and a check a file's own
# explanation of itself can fail is one somebody deletes.
check("the Commercial Builder no longer ships a branded bar",
      _wears(CBL, "cb-topbar"), False)
check("...and its stylesheet no longer draws one",
      bool(_re.search(r"^\.cb-topbar[a-z-]*\{", CBC, _re.M)), False)
check("...nor the brand lockup that sat on it",
      bool(_re.search(r"^\.cb-brand[a-z-]*\{", CBC, _re.M)), False)
check("the IO Builder no longer ships one either", "<header>" in IOB, False)
check("...and its stylesheet no longer draws one",
      "header{background:var(--navy)" in IOB, False)

# What each genuinely needed survives.
check("the Commercial Builder's two views are the shared strip",
      "cb-topnav s1d-subnav" in CBL, True)
check("...marked from the request, not passed in by each view",
      "request.endpoint" in CBL, True)
# A wizard step has its own stepper; lighting up Dashboard on step four says
# somebody is somewhere they are not. Only these two views mark anything.
check("...and only the two views it names can mark anything",
      sorted(set(_re.findall(r"_here == '(\w+)'", CBL))), ["dashboard", "library"])
# A full-bleed strip at the top of the viewport is the branded bar again
# wearing the shared classes, over the Hub's own breadcrumb.
check("the strip sits inside the page container",
      CBL.index('<main class="cb-main">') < CBL.index('<nav class="cb-topnav'), True)

# The IO Builder is one screen, so there is no strip to put in its place --
# what it needed was the icon rail CLAUDE.md has always described it as having.
check("the IO Builder asks for the icon rail",
      'data-s1hub-collapse="1"' in IOB, True)
check("...and the height math that followed the bar is gone",
      "61px" in IOB, False)
# The tile, the sidebar and Client 360 all call it the IO Builder.
check("...and the tab is named what the Hub calls the tool",
      "<title>IO Builder" in IOB, True)
# No walkthrough is registered for it, and offering a tour that does not exist
# is the silence Smart 1 Ads shipped on Settings and Live campaigns.
check("...and it claims no walkthrough it does not have",
      "data-module" in IOB, False)

# A button in the strip keeps its own look. The Commercial Builder paid for
# this once inside its own sheet; the shared strip would have done it again,
# and this time the module could not have answered -- hub-detail.css is
# injected after a module's own stylesheet and wins every tie.
check("the strip leaves an element that names itself a button alone",
      '.s1d-subnav a:not([class*="btn"])' in SHEET, True)
check("...so the module no longer restates the fix",
      ".cb-topnav a.cb-btn{" in CBC, False)


# ------------------------------------------ the failure markup cannot show
section("The wrapper still contains the page")

# A stray </div> closes the wrapper early. Nothing errors: the page renders,
# every link resolves, pagecheck passes -- and every rule scoped to .s1d-page
# silently stops applying to everything below the break. That is what one
# extra </div> did to Smart 1 Ads, and it is not visible in the diff.
from html.parser import HTMLParser  # noqa: E402

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


class _Nest(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = self.script = 0
        self.mark = None
        self.inside = 0

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        if tag == "script":
            self.script += 1
        self.depth += 1
        if self.script:
            return
        toks = (dict(attrs).get("class") or "").split()
        if self.mark is None and "s1d-page" in toks:
            self.mark = self.depth
        elif self.mark is not None and self.depth > self.mark:
            if {"card", "s1d-card", "s1d-tile", "s1d-subnav"} & set(toks):
                self.inside += 1

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if tag == "script" and self.script:
            self.script -= 1
        if self.mark is not None and self.depth == self.mark:
            self.mark = None
        self.depth = max(0, self.depth - 1)


WRAPPED = ["/tools/ads/", "/tools/ads/approvals", "/tools/ads/settings",
           "/scans/", "/scans/bulk", "/scans/widgets",
           "/tools/seo-images/", "/tools/seo-images/house", "/tools/social/",
           "/tools/stock-photos/", "/tools/utm/", "/tools/site-blocks/",
           "/tools/gpt-ads/", "/tools/landing-ads/", "/tools/bg-remover/",
           "/tools/radio-promo/", "/tools/fan-radio/", "/tools/page-images/",
           "/tools/google-access/"]
for path in WRAPPED:
    r = client.get(path)
    body = r.get_data(as_text=True)
    if r.status_code != 200:
        check(f"{path} renders", r.status_code, 200)
        continue
    n = _Nest()
    n.feed(body)
    check(f"{path}: the wrapper still holds the page", n.inside > 0, True)

# And the sheet reaches the blueprint modules at all, which is the half that
# was silently missing.
for path in ("/tools/google-access/", "/tools/page-images/", "/tools/calculators/"):
    body = client.get(path).get_data(as_text=True)
    check(f"{path} receives the shared stylesheet", "hub-detail.css" in body, True)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
