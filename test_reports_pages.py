"""The Reports pages: refused to a stranger, served to staff, and wired in.

    python3 test_reports_pages.py

Same shape as the other test files: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database.

What it holds:

  * every route under /reports refuses a request with no Hub session -- read
    off the module's own URL map rather than a list typed here, so a route
    added next month is covered without anybody remembering;
  * a signed-in member of staff gets every page rendered, with the data the
    store holds on it;
  * the forms write what they say: a mapping lands in the store AND in the
    activity log under the client's name, a markup with both boxes filled is
    refused whole, and a budget line is added;
  * the module is in the nav, tiled once, mounted from wsgi.py with its
    PUBLIC_PREFIXES read from the module, and its sidebar entry resolves.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_pages_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["SECRET_KEY"] = "reports-pages-test"
os.environ["PANEL_PASSWORD"] = "test"

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from werkzeug.test import Client                                    # noqa: E402

import wsgi                                                         # noqa: E402
from hub import auth                                                # noqa: E402
from modules.reports import app as reports_app                      # noqa: E402
from modules.reports import store                                   # noqa: E402
_reports_testdb.reset(store)

# Something for the pages to show.
store.upsert_rows([
    {"platform": "google", "account_id": "123", "campaign_id": "g-1",
     "campaign_name": "Search - Brand", "date": "2026-09-01", "spend": "40",
     "impressions": 500, "clicks": 20, "source": "windsor"},
    {"platform": "meta", "account_id": "act_9", "campaign_id": "m-1",
     "campaign_name": "Leads", "date": "2026-09-01", "spend": "75",
     "impressions": 900, "clicks": 12, "source": "csv"},
])


# ------------------------------------------------------ the module itself
section("The module is declared the way scans is")

check("it declares PUBLIC_PREFIXES", hasattr(reports_app, "PUBLIC_PREFIXES"))
check("...and only the client's own page is public", reports_app.PUBLIC_PREFIXES, ("/r/c/",))


def _dispatcher(app):
    """The layer of the stack that holds the mounts -- unwrapped rather than
    assumed at a depth, the way test_blueprint_guards.py reads it."""
    seen = set()
    while app is not None and id(app) not in seen:
        seen.add(id(app))
        if hasattr(app, "mounts"):
            return app
        app = getattr(app, "app", None) or getattr(app, "wsgi_app", None)
    return None


_disp = _dispatcher(wsgi.application)
check("wsgi.py mounts it at /reports", bool(_disp) and "/reports" in _disp.mounts)
check("wsgi.py reads the public list from the module rather than restating it",
      wsgi._REPORTS_PUBLIC, reports_app.PUBLIC_PREFIXES)
check("the mount is named in the active map", wsgi._MOUNT_ACTIVE.get("/reports"), "reports")
check("the module carries the shared guard rather than its own copy",
      "blueprint_guard" in (ROOT / "modules" / "reports" / "app.py").read_text(encoding="utf-8"))

# Every route the module serves, with its variables filled in. Read off the
# URL map so a route added later is swept without an edit here.
ROUTES = []
for rule in reports_app.app.url_map.iter_rules():
    if rule.endpoint == "static":
        continue
    path = rule.rule
    for arg in rule.arguments:
        path = path.replace(f"<{arg}>", "x").replace(f"<path:{arg}>", "x").replace(f"<int:{arg}>", "1")
    methods = sorted(m for m in rule.methods if m in ("GET", "POST"))
    ROUTES.append((path, methods))
check("the module serves the staff screens, the picker's search and the client's page",
      sorted({p for p, _ in ROUTES}),
      sorted(["/", "/unmapped", "/unmapped/confirm", "/unmapped/refuse",
              "/markup", "/budgets", "/budgets/1", "/provider-check", "/audiogo-check",
              "/pacing", "/pacing.csv", "/cost", "/cost.csv",
              "/api/clients", "/health", "/client/x", "/client/x/campaign", "/client/x/link", "/client/x/push",
              "/r/c/x", "/r/c/x.pdf", "/r/c/x/data.json"]))
# The public trio is asserted by test_reports_public.py; here every OTHER
# route must refuse a stranger.
ROUTES = [(p, m) for p, m in ROUTES if not p.startswith("/r/c/")]


# ------------------------------------------------------------- a stranger
section("A stranger is refused on every route")

anon = Client(wsgi.application)
for path, methods in sorted(ROUTES):
    for method in methods:
        r = anon.open("/reports" + path, method=method)
        check(f"{method} /reports{path} -> {r.status_code}",
              r.status_code in (302, 401) and
              (r.status_code == 401 or r.headers.get("Location", "").startswith("/login")))
check("a redirect carries the page back as next",
      anon.get("/reports/unmapped").headers.get("Location"), "/login?next=/reports/unmapped")
check("a JSON caller gets a 401 it can read",
      anon.get("/reports/api/clients?q=a").status_code, 401)


# -------------------------------------------------------------- and staff
section("A signed-in member of staff gets every page")

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

r = staff.get("/reports/")
body = r.get_data(as_text=True)
check("the landing page renders", r.status_code, 200)
check("...listing every platform", all(
    label in body for label in ("Google Ads", "Meta", "The Trade Desk", "Smart 1 Suite")))
check("...with the unmapped count", ">2<" in body.replace("\n", ""))
check("...and the links onward", all(
    href in body for href in ('href="/reports/unmapped"', 'href="/reports/budgets"',
                              'href="/reports/markup"')))
check("...wearing the Hub chrome", "s1hub-sb" in body)
check("...and the Reports entry is the one lit",
      'class="s1hub-item s1hub-on" href="/reports/"' in body)
check("it says which database it is on", "dedicated reports database" in body)

r = staff.get("/reports/unmapped")
body = r.get_data(as_text=True)
check("the unmapped queue renders", r.status_code, 200)
check("...naming both campaigns", "Search - Brand" in body and "Leads" in body)
check("...biggest spend first", body.index("Leads") < body.index("Search - Brand"))
check("...with a picker on each row", body.count('name="client_name"'), 2)
check("...and the rename hint",
      "S1M | &lt;ClientKey&gt; | &lt;Product&gt; | &lt;anything&gt;" in body)
check("...and the picker script once", body.count("s1reports:client") >= 1)

r = staff.get("/reports/markup")
body = r.get_data(as_text=True)
check("the markup page renders", r.status_code, 200)
check("...with the two columns labeled as the work order says",
      "<th>Markup %</th>" in body and "<th>Fixed CPM</th>" in body)
check("...one row per platform", body.count('name="markup_'), 13)

r = staff.get("/reports/budgets")
check("the budgets page renders", r.status_code, 200)
check("...with the form", 'name="monthly_budget"' in r.get_data(as_text=True))

r = staff.get("/reports/api/clients?q=zz")
check("the picker search answers JSON", r.status_code, 200)
check("...with a clients list", isinstance(r.get_json().get("clients"), list))
check("?limit is clamped rather than trusted",
      staff.get("/reports/api/clients?q=a&limit=abc").status_code, 200)
check("the health probe answers", staff.get("/reports/health").get_json()["ok"], True)


# -------------------------------------------------------------- the forms
section("The forms write what they say")

r = staff.post("/reports/unmapped", data={
    "platform": "meta", "account_id": "act_9", "campaign_id": "m-1",
    "campaign_name": "Leads", "client_name": "Acme Co", "client_key": "d:acme.com",
    "product": "Social"}, follow_redirects=True)
body = r.get_data(as_text=True)
check("mapping a campaign lands back on the queue", r.status_code, 200)
check("...saying it is filed", "m-1 is filed" in body)
check("...and the campaign has left the list", body.count('name="client_name"'), 1)
check("...and is on the recently-mapped list", "Acme Co" in body and "d:acme.com" in body)
check("the store agrees", store.mapped_campaigns()[0]["client"], "d:acme.com")

with open(os.environ["AUDIT_LOG_PATH"], encoding="utf-8") as fh:
    entries = [json.loads(ln) for ln in fh if ln.strip()]
mapped = [e for e in entries if e.get("module") == "reports" and e["type"] == "campaign_mapped"]
check("the mapping is in the activity log", len(mapped), 1)
check("...under the client's name, so it lands on their record",
      mapped[0].get("client"), "Acme Co")
check("...with who did it", mapped[0].get("actor"), "Todd")
check("...and the key beside the name", mapped[0].get("client_key"), "d:acme.com")

r = staff.post("/reports/unmapped", data={
    "platform": "google", "account_id": "123", "campaign_id": "g-1",
    "client_name": "", "client_key": ""}, follow_redirects=True)
check("a mapping with no client is refused with a reason",
      "Pick a client first" in r.get_data(as_text=True))
check("...and nothing was written", len(store.mapped_campaigns()), 1)

r = staff.post("/reports/markup", data={"markup_google": "15", "cpm_google": "12.5",
                                        "markup_meta": "10"}, follow_redirects=True)
check("a row with both set is refused",
      "not both" in r.get_data(as_text=True))
check("...and nothing was written for any platform, including the good row",
      [m["platform"] for m in store.markups() if m["markup"] is not None], [])
r = staff.post("/reports/markup", data={"markup_google": "15", "cpm_ttd": "12.5"},
               follow_redirects=True)
check("a clean form saves", "Markups saved" in r.get_data(as_text=True))
have = {m["platform"]: m for m in store.markups()}
check("15 on the screen is 0.15 in the column", str(have["google"]["markup"]), "0.1500")
check("...and reads back as 15.00 on the screen",
      'value="15.00"' in r.get_data(as_text=True))
check("a CPM is a CPM", str(have["ttd"]["cpm"]), "12.50")
r = staff.post("/reports/markup", data={"markup_google": "abc"}, follow_redirects=True)
check("a non-number is refused with the platform named",
      "Google Ads" in r.get_data(as_text=True) and "not a number" in r.get_data(as_text=True))
staff.post("/reports/markup", data={"cpm_ttd": "12.5"}, follow_redirects=True)
check("a blank row clears the platform",
      [m["platform"] for m in store.markups() if m["markup"] is not None or m["cpm"] is not None],
      ["ttd"])

r = staff.post("/reports/budgets", data={
    "client_name": "Acme Co", "client_key": "d:acme.com", "product": "CTV",
    "platform": "ttd", "monthly_budget": "2,500", "flight_start": "2026-09-01",
    "flight_end": "2026-12-31", "notes": "Q4"}, follow_redirects=True)
body = r.get_data(as_text=True)
check("a budget line with a thousands comma is refused rather than misread",
      "not a number" in body)
r = staff.post("/reports/budgets", data={
    "client_name": "Acme Co", "client_key": "d:acme.com", "product": "CTV",
    "platform": "ttd", "monthly_budget": "2500", "flight_start": "2026-09-01",
    "flight_end": "2026-12-31", "notes": "Q4"}, follow_redirects=True)
body = r.get_data(as_text=True)
check("a budget line is added", "Budget line #" in body and "$2,500.00" in body)
check("...marked manual", "Manual" in body)
check("...and the store agrees", store.budget_lines()[0]["product"], "CTV")
with open(os.environ["AUDIT_LOG_PATH"], encoding="utf-8") as fh:
    entries = [json.loads(ln) for ln in fh if ln.strip()]
check("the budget is in the activity log under the client",
      [e.get("client") for e in entries if e.get("type") == "budget_added"], ["Acme Co"])


# --------------------------------------------------------------- wired in
section("It is in the nav, tiled once, and named on the crumb trail")

from hub import sidebar                                             # noqa: E402
check("the sidebar has a Reports entry",
      any(row[0] == "reports" and row[1] == "/reports/" for row in sidebar._ITEMS))
tools = (ROOT / "hub" / "templates" / "tools.html").read_text(encoding="utf-8")
creative = (ROOT / "hub" / "templates" / "creative.html").read_text(encoding="utf-8")
check("it is tiled on Client Tools", tools.count('href="/reports/"'), 1)
check("...and not a second time on Creative", 'href="/reports/"' not in creative)
crumbs = (ROOT / "hub" / "static" / "hub-crumbs.js").read_text(encoding="utf-8")
check("the crumb trail can name it", '"reports": "Reports"' in crumbs)
from hub import help_coverage                                       # noqa: E402
check("the help coverage sweep knows which module the tile is",
      help_coverage.PREFIXES.get("/reports/"), "reports")
from hub import client_brand                                        # noqa: E402
check("a mapping is declared a join rather than client work",
      "reports" in client_brand.NOT_WORK)
for f in ("env.example", "render.yaml"):
    check(f"{f} documents REPORTS_DATABASE_URL and says why",
          "REPORTS_DATABASE_URL" in (ROOT / f).read_text(encoding="utf-8")
          and "1 GB" in (ROOT / f).read_text(encoding="utf-8"))


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
