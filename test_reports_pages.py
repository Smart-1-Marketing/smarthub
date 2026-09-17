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
import re
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
      sorted(["/", "/unmapped", "/unmapped/alias/forget", "/unmapped/confirm", "/unmapped/confirm-many", "/unmapped/refuse",
              "/markup", "/budgets", "/budgets/1", "/provider-check", "/provider-check/x", "/provider-check/confirm",
              "/provider-check/withdraw", "/audiogo-check", "/groundtruth-check", "/amazon-check",
              "/callrail-check",
              "/quarantine", "/quarantine/decide",
              "/reconcile", "/reconcile/run", "/refresh/x", "/backfill/x", "/backfill/x/nightly",
              "/pacing", "/pacing.csv", "/cost", "/cost.csv",
              "/api/clients", "/health", "/client/x", "/client/x/campaign", "/client/x/link", "/client/x/push",
              "/client/x/summary", "/client/x/summary/draft",
              "/r/c/x", "/r/c/x.pdf", "/r/c/x/data.json", "/upload"]))
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
      'class="s1hub-leaf s1hub-on" href="/reports/"' in body
      and 'class="s1hub-dept-row s1hub-on"' in body)
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

# The likeness beside each row: the registry stub knows Acme Co, and the
# Google campaign is named like nobody.
from hub import clients_registry as _reg                                 # noqa: E402
_reg_all = _reg.all_clients
_reg.all_clients = lambda refresh=False: [
    {"name": "Acme Co", "slug": "acme-co", "url": "https://acme.com", "domain": "acme.com", "key": "d:acme.com"},
    {"name": "Zeta Dental", "slug": "zeta-dental", "url": "", "domain": "", "key": "n:zeta-dental"},
]
store.upsert_rows([{"platform": "ttd", "account_id": "t-1", "campaign_id": "t-acme",
                    "campaign_name": "Acme Co | CTV | Q4", "date": "2026-09-01", "spend": "10",
                    "impressions": 100, "clicks": 1, "source": "csv"}])
body = staff.get("/reports/unmapped").get_data(as_text=True)
check("a row named like a client opens its picker on that client",
      'value="Acme Co"' in body and 'name="client_key" value="d:acme.com"' in body)
check("...with the likeness said in words", "the campaign name contains the client" in body)
check("...as a chip that can be changed", 'data-key="d:acme.com"' in body and "Looks like:" in body)
check("a row named like nobody says so", "No client's name looks like this one." in body)
check("...and opens on nobody", body.count('name="client_key" value=""') >= 2)
_reg.all_clients = lambda refresh=False: (_ for _ in ()).throw(RuntimeError("knack down"))
body = staff.get("/reports/unmapped").get_data(as_text=True)
check("an unreadable registry is said on the page, not an empty likeness",
      "Suggestions not available" in body and "No client's name looks like" not in body)
_reg.all_clients = _reg_all

r = staff.get("/reports/markup")
body = r.get_data(as_text=True)
check("the markup page renders", r.status_code, 200)
check("...with the two columns labeled as the work order says",
      "<th>Markup %</th>" in body and "<th>Fixed CPM</th>" in body)
check("...one row per platform", body.count('name="markup_'), 14)

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
# Two pickers left: the Google campaign and the Trade Desk one the likeness
# check above added.
check("...and the campaign has left the list", body.count('name="client_name"'), 2)
check("...and is on the recently-mapped list", "Acme Co" in body and "d:acme.com" in body)
check("the store agrees", store.mapped_campaigns()[0]["client"], "d:acme.com")

# Through the module: the activity log's backend is the database now, so
# reading AUDIT_LOG_PATH reads a fallback nothing writes to.
from hub import audit as _audit                                  # noqa: E402
entries = list(reversed(_audit.read(limit=2000)))
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

# Move: a proposal the auto-mapper filed under the wrong client is filed
# under the right one by the same press, confirmed by the person's press,
# from the queue and from the client's page alike.
store.map_campaign("ttd", "t-1", "t-acme", client="n:zeta-dental", client_name="Zeta Dental",
                   product="Streaming TV", mapped_by="auto", auto_rule="fuzzy_v1+name_product",
                   campaign_name="Acme Co | CTV | Q4")
body = staff.get("/reports/unmapped").get_data(as_text=True)
check("a pending row offers Move", 'placeholder="Move to another client…"' in body)
check("...and says it was filed by likeness", "by likeness" in body)
r = staff.post("/reports/unmapped", data={
    "platform": "ttd", "account_id": "t-1", "campaign_id": "t-acme",
    "campaign_name": "Acme Co | CTV | Q4", "client_name": "Acme Co", "client_key": "d:acme.com",
    "product": "Streaming TV"}, follow_redirects=True)
body = r.get_data(as_text=True)
check("moving it lands back on the queue saying so", r.status_code == 200 and "Moved." in body)
moved = store.campaign_map("ttd", "t-1", "t-acme")
check("...filed under the picked client, confirmed by the press",
      (moved["client"], moved["mapped_by"], moved["pending"], moved["product"]),
      ("d:acme.com", "Todd", False, "Streaming TV"))
entries = list(reversed(_audit.read(limit=2000)))
mv = [e for e in entries if e.get("module") == "reports" and e["type"] == "campaign_mapped"
      and e.get("campaign_id") == "t-acme"]
check("...and the activity row says where it came from",
      bool(mv) and "moved from Zeta Dental, which the auto-mapper had proposed" in mv[0]["detail"])
store.map_campaign("ttd", "t-1", "t-acme", client="n:zeta-dental", client_name="Zeta Dental",
                   product="Streaming TV", mapped_by="auto", auto_rule="fuzzy_v1",
                   campaign_name="Acme Co | CTV | Q4")
body = staff.get("/reports/client/n:zeta-dental").get_data(as_text=True)
check("the client's page offers Move on a pending row", 'placeholder="Move to another client…"' in body)
check("...with the picker script", "s1reports:client" in body)
r = staff.post("/reports/unmapped", data={
    "platform": "ttd", "account_id": "t-1", "campaign_id": "t-acme",
    "campaign_name": "Acme Co | CTV | Q4", "client_name": "Acme Co", "client_key": "d:acme.com",
    "product": "Streaming TV", "back": "client", "client": "n:zeta-dental"})
check("moving from the client's page goes back to that page",
      r.status_code == 302 and r.headers["Location"].endswith("/reports/client/n:zeta-dental?saved=moved"))
check("...and the campaign is off it", store.campaign_map("ttd", "t-1", "t-acme")["client"], "d:acme.com")
# One client's queue, and confirming in bulk: two proposals filed under
# Acme Co, a third under Zeta Dental, ticked two at a time.
_reg.all_clients = lambda refresh=False: [
    {"name": "Acme Co", "slug": "acme-co", "url": "https://acme.com", "domain": "acme.com", "key": "d:acme.com"},
    {"name": "Zeta Dental", "slug": "zeta-dental", "url": "", "domain": "", "key": "n:zeta-dental"}]
store.upsert_rows([
    {"platform": "meta", "account_id": "act_b", "campaign_id": "b-1", "campaign_name": "Acme Co | CTV | Q4",
     "date": "2026-09-02", "spend": "3", "impressions": 30, "clicks": 1, "source": "csv"},
    {"platform": "meta", "account_id": "act_b", "campaign_id": "b-2", "campaign_name": "Acme Co | Leads",
     "date": "2026-09-02", "spend": "3", "impressions": 30, "clicks": 1, "source": "csv"},
    {"platform": "meta", "account_id": "act_z", "campaign_id": "z-1", "campaign_name": "Zeta Dental | Reels",
     "date": "2026-09-02", "spend": "3", "impressions": 30, "clicks": 1, "source": "csv"},
    {"platform": "meta", "account_id": "act_z", "campaign_id": "z-free", "campaign_name": "Zeta Dental - OTT push",
     "date": "2026-09-02", "spend": "3", "impressions": 30, "clicks": 1, "source": "csv"},
])
for _p, _a, _c, _n, _cl, _cn in (("meta", "act_b", "b-1", "Acme Co | CTV | Q4", "d:acme.com", "Acme Co"),
                                  ("meta", "act_b", "b-2", "Acme Co | Leads", "d:acme.com", "Acme Co"),
                                  ("meta", "act_z", "z-1", "Zeta Dental | Reels", "n:zeta-dental", "Zeta Dental")):
    store.map_campaign(_p, _a, _c, client=_cl, client_name=_cn, product="Paid Social", mapped_by="auto",
                       auto_rule="fuzzy_v1", campaign_name=_n)
body = staff.get("/reports/unmapped?client=d:acme.com").get_data(as_text=True)
tick = lambda p, a, c: f'value="{p}|{a}|{c}"'
check("one client's queue shows their proposals only",
      body.count('name="keys" value="') == 2 and tick("meta", "act_b", "b-1") in body
      and tick("meta", "act_z", "z-1") not in body)
check("...and the unmapped campaigns that look like theirs, not the others",
      "Showing <b>Acme Co</b>'s queue" in body and "Zeta Dental - OTT push" not in body)
body = staff.get("/reports/unmapped?client=n:zeta-dental").get_data(as_text=True)
check("...the other client's, theirs", "Zeta Dental - OTT push" in body and tick("meta", "act_z", "z-1") in body
      and tick("meta", "act_b", "b-2") not in body)
check("the product box opens on what the name says, and says so",
      'value="Streaming TV" title="the name says &#39;ott&#39;"' in body
      and "Product: the name says &#39;ott&#39;; check it." in body)
body = staff.get("/reports/unmapped").get_data(as_text=True)
check("the whole queue has the bulk form with a tick per proposal",
      'id="confirm-many"' in body and body.count('name="keys" value="') == 3)
check("...each tick saying whether its evidence was exact", body.count('data-sure="1"') == 3)
r = staff.post("/reports/unmapped/confirm-many", data={"keys": []}, follow_redirects=True)
check("an empty press is refused", "Tick at least one" in r.get_data(as_text=True))
r = staff.post("/reports/unmapped/confirm-many", data={
    "keys": ["meta|act_b|b-1", "meta|act_b|b-2", "meta|act_zz|nope", "garbage"], "client": "d:acme.com"},
    follow_redirects=True)
body = r.get_data(as_text=True)
check("confirming in bulk confirms each ticked proposal", r.status_code == 200 and "Confirmed 2 campaigns." in body)
check("...and names what it could not", "2 not confirmed" in body and "nope" in body)
check("...back on the same client's queue", "Showing <b>Acme Co</b>" in body and body.count('name="keys" value="') == 0)
check("the store agrees", [store.campaign_map("meta", "act_b", c)["pending"] for c in ("b-1", "b-2")], [False, False])
check("...and the third is still waiting", store.campaign_map("meta", "act_z", "z-1")["pending"], True)
entries = list(reversed(_audit.read(limit=3000)))
_bulk = [e for e in entries if e.get("type") == "campaign_confirmed" and e.get("campaign_id") in ("b-1", "b-2")]
check("each confirmation is its own activity row, saying it was a batch",
      len(_bulk) == 2 and all("in a batch of 4" in e["detail"] and e.get("actor") == "Todd" for e in _bulk))
_db = store.SessionLocal()
try:
    _db.query(store.CampaignMap).filter(store.CampaignMap.campaign_id.in_(["b-1", "b-2", "z-1"])).delete(synchronize_session=False)
    _db.query(store.AdPerfDaily).filter(store.AdPerfDaily.campaign_id.in_(["b-1", "b-2", "z-1", "z-free"])).delete(synchronize_session=False)
    _db.commit()
finally:
    _db.close()
_reg.all_clients = _reg_all

# Mapping and moving t-acme taught nothing: its name carried the client's
# own name. A campaign whose name calls the client something else does.
store.upsert_rows([{"platform": "ttd", "account_id": "t-9", "campaign_id": "t-nick",
                    "campaign_name": "ACO - Search - 2026", "date": "2026-09-02", "spend": "3",
                    "impressions": 30, "clicks": 1, "source": "csv"}])
check("nothing was learned from a name that carried the client's own name", store.campaign_aliases(), [])
r = staff.post("/reports/unmapped", data={
    "platform": "ttd", "account_id": "t-9", "campaign_id": "t-nick",
    "campaign_name": "ACO - Search - 2026", "client_name": "Acme Co", "client_key": "d:acme.com",
    "product": "Paid Search"}, follow_redirects=True)
body = r.get_data(as_text=True)
_al = store.campaign_aliases()
check("mapping a campaign by hand teaches what it calls the client",
      [(a["alias"], a["client"], a["count"], a["learned_by"]) for a in _al], [("aco", "d:acme.com", 1, "Todd")])
check("...and the queue lists it, as a suggestion until taught again",
      "1 learned name" in body and "<b>aco</b>" in body and "suggests only" in body)
r = staff.post("/reports/unmapped/alias/forget", data={"alias": "aco", "client": "d:acme.com",
                                                        "client_name": "Acme Co"}, follow_redirects=True)
check("Forget drops it", r.status_code == 200 and "Forgotten." in r.get_data(as_text=True)
      and store.campaign_aliases() == [])
check("forgetting an alias not on file says so",
      "not on file" in staff.post("/reports/unmapped/alias/forget", data={"alias": "zzz", "client": "d:acme.com"},
                                  follow_redirects=True).get_data(as_text=True))
_db = store.SessionLocal()
try:
    _db.query(store.CampaignMap).filter(store.CampaignMap.campaign_id == "t-nick").delete(synchronize_session=False)
    _db.query(store.AdPerfDaily).filter(store.AdPerfDaily.campaign_id == "t-nick").delete(synchronize_session=False)
    _db.commit()
finally:
    _db.close()

# The account is Acme Co's now (one confirmed campaign on it), so a new
# campaign on the same account opens on Acme Co with the account's reason.
store.upsert_rows([{"platform": "ttd", "account_id": "t-1", "campaign_id": "t-next",
                    "campaign_name": "Holiday push", "date": "2026-09-02", "spend": "3",
                    "impressions": 30, "clicks": 1, "source": "csv"}])
_reg.all_clients = lambda refresh=False: [
    {"name": "Acme Co", "slug": "acme-co", "url": "https://acme.com", "domain": "acme.com", "key": "d:acme.com"}]
body = staff.get("/reports/unmapped").get_data(as_text=True)
check("a new campaign on a client's account says so under the account id",
      "1 other campaign on this ad account is confirmed as theirs (Acme Co)" in body)
_reg.all_clients = _reg_all

# Out of the book again, so the counts the checks below expect are the
# ones the fixture at the top seeded.
store.refuse_mapping("ttd", "t-1", "t-acme", by="Todd")
_db = store.SessionLocal()
try:
    _db.query(store.AdPerfDaily).filter(store.AdPerfDaily.campaign_id.in_(["t-acme", "t-next"])).delete(synchronize_session=False)
    _db.query(store.MapRefusal).filter(store.MapRefusal.campaign_id == "t-acme").delete()
    _db.commit()
finally:
    _db.close()
check("(cleared for the checks below)",
      (store.campaign_map("ttd", "t-1", "t-acme"), store.unmapped_count()), (None, 1))

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

# ---- the bounds. No platform publishes a ceiling, so these are ours, and
# the slip they exist for is 1500 typed for 15 reaching every client page
# that reads the platform's rule at once.
r = staff.post("/reports/markup", data={"markup_google": "1500", "cpm_ttd": "12.5"},
               follow_redirects=True)
body = r.get_data(as_text=True)
check("1500 typed for 15 is refused by name, with the ceiling",
      "1500%" in body and "outside the 0-300%" in body and "15 means 15%" in body)
check("...and nothing was written",
      [m["platform"] for m in store.markups() if m["markup"] is not None], [])
r = staff.post("/reports/markup", data={"cpm_ttd": "9999"}, follow_redirects=True)
body = r.get_data(as_text=True)
check("a $9,999 CPM is refused by name", "$9,999.00" in body and "$0-$250" in body)
check("the ttd CPM is what it was", str(store.markups()[0]["cpm"]), "12.50")
try:
    store.set_markup("google", markup="15", updated_by="Todd")   # a FRACTION of 15 is 1500%
    check("the store's own door refuses a markup over the ceiling", False)
except ValueError as exc:
    check("the store's own door refuses a markup over the ceiling",
          "outside the 0-300%" in str(exc))
check("...and one at the ceiling is accepted",
      str(store.set_markup("google", markup="3", updated_by="Todd").markup), "3.0000")
store.clear_markup("google")
check("the bounds say whose they are", store.BOUNDS_SOURCE, "house")
check("the page says the ceilings in words",
      "above 300%" in staff.get("/reports/markup").get_data(as_text=True))

# ---- what a change reaches, said before it is saved. A platform rule is
# global: saving one moves the Investment figure on every client page that
# reads it, live, with nothing on those pages saying so.
meta_label = store.platform_label("meta")
lk = store.create_link("d:acme.com", client_name="Acme Co", created_by="Todd")
store.update_link(lk.token, show_spend=True)
pages = store.pages_on_platform_rule()
check("Acme's page reads the meta rule: a confirmed meta campaign, Investment shown, no override",
      [p["client_name"] for p in pages["meta"]], ["Acme Co"])
check("...and every platform is a key, empty for the ones no page reads",
      sorted(p for p, v in pages.items() if v), ["meta"])
body = staff.get("/reports/markup").get_data(as_text=True)
check("the markup page says so on the meta row", "1 showing Investment" in body)
r = staff.post("/reports/markup", data={"markup_meta": "20", "cpm_ttd": "12.5"})
body = " ".join(r.get_data(as_text=True).split())
check("a change to a rule a client page reads is not saved on the first press",
      r.status_code, 200)
check("...it names the platform, the change and who reads it",
      f"<b>{meta_label}</b>: at cost &rarr; 20%" in body
      and "read by 1 client page showing Investment (Acme Co)" in body)
check("...with a Save anyway that re-posts the same values",
      'name="confirm" value="1"' in body and 'name="markup_meta" value="20"' in body)
check("...and nothing was written",
      [m["platform"] for m in store.markups() if m["markup"] is not None], [])
r = staff.post("/reports/markup", data={"markup_meta": "20", "cpm_ttd": "12.5", "confirm": "1"},
               follow_redirects=True)
check("confirmed, it saves", "Markups saved" in r.get_data(as_text=True))
check("...as 0.20 in the column",
      str({m["platform"]: m for m in store.markups()}["meta"]["markup"]), "0.2000")
r = staff.post("/reports/markup", data={"markup_meta": "20", "cpm_ttd": "12.5", "markup_google": "10"},
               follow_redirects=True)
check("a change reaching no client page saves on the first press, and an unchanged rule is not re-asked",
      "Markups saved" in r.get_data(as_text=True)
      and str({m["platform"]: m for m in store.markups()}["google"]["markup"]) == "0.1000")
store.update_link(lk.token, markup_json={"meta": {"markup": "0.30"}})
check("a link carrying its own override for the platform no longer reads the rule",
      store.pages_on_platform_rule()["meta"], [])
store.update_link(lk.token, markup_json={}, show_spend=False)
check("a page hiding Investment is not counted either -- the change does not reach it",
      store.pages_on_platform_rule()["meta"], [])
r = staff.post("/reports/markup", data={"markup_meta": "25", "cpm_ttd": "12.5", "markup_google": "10"},
               follow_redirects=True)
check("...so a change to that rule now saves on the first press",
      "Markups saved" in r.get_data(as_text=True))
try:
    store.update_link(lk.token, markup_json={"meta": {"cpm": "9999"}})
    check("the per-link override goes through the same CPM door", False)
except ValueError as exc:
    check("the per-link override goes through the same CPM door", "$0-$250" in str(exc))
staff.post("/reports/markup", data={"cpm_ttd": "12.5"}, follow_redirects=True)
# Retire the link: the sections below were written against a store where
# Acme has no live link yet, and a link left live here would read on the
# Client 360 card as one the adapter never minted.
_db = store.SessionLocal()
try:
    _row = _db.get(store.ReportLink, lk.id)
    _row.enabled = False
    _db.commit()
finally:
    _db.close()

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
# Through the module: the activity log's backend is the database now, so
# reading AUDIT_LOG_PATH reads a fallback nothing writes to.
from hub import audit as _audit                                  # noqa: E402
entries = list(reversed(_audit.read(limit=2000)))
check("the budget is in the activity log under the client",
      [e.get("client") for e in entries if e.get("type") == "budget_added"], ["Acme Co"])


# ------------------------------------------------ the Client 360 card
section("Client 360's ad-performance card: four kinds of nothing, kept apart")
# modules/reports/client_card.py behind /api/client/ad-performance. What is
# worth asserting is what the card says when there is nothing to show,
# because "the store would not answer", "nothing is filed", "everything
# filed is waiting for a person" and "spent nothing this month" are four
# situations and only the last means there is nothing to do.
from datetime import date as _date                                  # noqa: E402
from modules.reports import client_card, client_view                # noqa: E402

_today = _date.today().isoformat()
# A row dated today, so the month-to-date window holds it whatever month the
# suite runs in.
store.upsert_rows([
    {"platform": "meta", "account_id": "act_9", "campaign_id": "m-1",
     "campaign_name": "Leads", "date": _today, "spend": "75",
     "impressions": 900, "clicks": 12, "source": "csv"},
])
check("the card's API refuses a stranger",
      Client(wsgi.application).get("/api/client/ad-performance?name=Acme").status_code, 401)


def _card(name):
    r = staff.get("/api/client/ad-performance?name=" + name)
    check(f"/api/client/ad-performance answers 200 for {name!r}", r.status_code, 200)
    return json.loads(r.get_data(as_text=True))


d = _card("Nobody%20Here")
check("a client nothing is filed under is measured, and says nothing is filed",
      (d["measured"], d["state"]), (True, "no_campaigns"))
check("...and the staff link opens the registry's key for them, so the first "
      "mapping lands where the card will read it",
      d["staff_url"].startswith("/reports/client/n%3A"))

# Acme Co: the forms section above mapped m-1 to d:acme.com by hand (which
# confirms it) and added a CTV line on The Trade Desk.
d = _card("Acme%20Co")
check("a client with a confirmed campaign and spend this month is ok",
      (d["measured"], d["state"]), (True, "ok"))
check("...read under the key the mapping was filed with, found from the display name",
      "d:acme.com" in d["keys"] and d["primary"] == "d:acme.com")
check("...and the staff link opens that key", d["staff_url"], "/reports/client/d%3Aacme.com")
check("...counting the confirmed campaign and no pending one",
      (d["campaigns"]["confirmed"], d["campaigns"]["pending"]), (1, 0))
# The figure is the store's own sum over the month-to-date window -- the
# same reader the client's page uses -- so the card cannot disagree with it,
# and the assertion does not depend on which month the suite runs in.
_rng = client_view.period_range("mtd", _date.today())
_mine = store.facts_for("d:acme.com", _rng["start"], _rng["end"])
check("...with this month's spend on the platform, named the way Reports names it",
      [(t["label"], t["raw_spend"], t["impressions"]) for t in d["totals"]],
      [("Meta", float(sum(f["spend"] for f in _mine)), sum(f["impressions"] for f in _mine))])
check("...and that window holds the row dated today", any(f["date"].isoformat() == _today for f in _mine))
check("...unpriced where no markup or CPM is set, never a smaller number",
      (d["totals"][0]["client_price"], d["billed_total"]), (None, None))
check("...and the sold line on the pacing board, unmapped because nothing "
      "on The Trade Desk is filed under them",
      [(p["product"], p["band"]) for p in d["pacing"]], [("CTV", "unmapped")])
check("...held rows counted, not None", d["held"], 0)
check("...feeds measured for the platforms the client rides on",
      isinstance(d["feeds"], list))
check("...and no live link yet", d["link"], None)

# Beta LLC: filed from the campaign name by the auto-mapper, nobody has
# confirmed it. The whole point of the state.
store.map_campaign("google", "123", "g-1", client="n:beta-llc", client_name="Beta LLC",
                   product="Paid Search", mapped_by="auto", auto_rule="name")
d = _card("Beta%20LLC")
check("a client whose only campaigns are pending is all_pending, not empty",
      (d["state"], d["campaigns"]["pending"], d["campaigns"]["confirmed"]), ("all_pending", 1, 0))
check("...and nothing reaches the totals", d["totals"], [])

# Gamma Inc: confirmed, and the only spend is in a month long gone.
store.upsert_rows([
    {"platform": "ttd", "account_id": "adv_1", "campaign_id": "t-1",
     "campaign_name": "CTV Q1", "date": "2026-01-05", "spend": "300",
     "impressions": 9000, "clicks": 3, "source": "windsor"},
])
store.map_campaign("ttd", "adv_1", "t-1", client="n:gamma-inc", client_name="Gamma Inc",
                   product="CTV", mapped_by="Todd")
d = _card("Gamma%20Inc")
check("a confirmed client with no spend this period says so, not 'nothing filed'",
      (d["state"], d["campaigns"]["confirmed"]), ("nothing_this_period", 1))

# The store would not answer: measured False with the reason, never a
# healthy-looking empty. Both mapping readers are broken, not one -- the
# card reads by client through campaign_maps_for, and a guard aimed at
# whichever function it used last is a guard that lapses the next time
# that changes.
_real = (store.mapped_campaigns, store.campaign_maps_for)


def _down(*a, **k):
    raise RuntimeError("down")


store.mapped_campaigns = store.campaign_maps_for = _down
try:
    d = client_card.summary(["Acme Co"])
finally:
    store.mapped_campaigns, store.campaign_maps_for = _real
check("a store that will not answer is not measured",
      (d["measured"], d["state"]), (False, "unread"))
check("...and the sentence names the store", "could not be read" in d["error"])

# Every spelling the store may file a client under is read: the display
# name (hub/proposal_adapters/reports.py files there), the name key, and
# the key whose stored display name matches exactly.
keys = client_card.candidate_keys(["Acme Co"], filed=[("d:acme.com", "Acme Co"),
                                                      ("d:acme-supply.com", "Acme Co Supply")])
check("the display name itself is a candidate", "Acme Co" in keys)
check("the name key is a candidate", "n:acme" in keys or "n:acme-co" in keys)
check("a key filed under exactly this display name is a candidate", "d:acme.com" in keys)
check("...and one filed under a name that merely contains it is not",
      "d:acme-supply.com" not in keys)

# The renderer, lifted from the template and driven in node: a copy restated
# here would be a third thing to keep in step.
import subprocess                                                   # noqa: E402
_REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
_a = _REC.find("/* ---- c360 ad performance (lifted")
_b = _REC.find("/* ---- end c360 ad performance ----")
check("the card's renderer is marked for lifting", 0 < _a < _b)
_SRC = _REC[_a:_b] if 0 < _a < _b else ""
_payloads = [
    {"measured": False, "error": "the ad-performance store could not be read (OperationalError)"},
    json.loads(staff.get("/api/client/ad-performance?name=Nobody%20Here").get_data(as_text=True)),
    json.loads(staff.get("/api/client/ad-performance?name=Beta%20LLC").get_data(as_text=True)),
    json.loads(staff.get("/api/client/ad-performance?name=Gamma%20Inc").get_data(as_text=True)),
    json.loads(staff.get("/api/client/ad-performance?name=Acme%20Co").get_data(as_text=True)),
]
_payloads.append(dict(_payloads[-1], held=None, feeds=None))
_driver = ("function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;')"
           ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + _SRC + "\nconst P=" + json.dumps(_payloads) + ";\n"
           "console.log(JSON.stringify(P.map(p=>renderAdPerformance(p,'Acme Co'))));\n")
_r = subprocess.run(["node", "-"], input=_driver, capture_output=True, text=True)
check("the lifted renderer runs on its own", _r.returncode, 0)
if _r.returncode:
    print("          node:", _r.stderr[-300:])
_out = json.loads(_r.stdout or "[]") if _r.returncode == 0 else [""] * 6
_unread, _none, _pend, _quiet, _ok, _partial = (_out + [""] * 6)[:6]
check("an unread store is drawn as unread, not as empty",
      "could not be read" in _unread and "No advertising campaign" not in _unread)
check("nothing filed says so and points at the unmapped queue",
      "No advertising campaign is filed" in _none and "/reports/unmapped" in _none)
check("...and never draws a spend table", "<table>" not in _none)
check("all pending says a person has to confirm",
      "waiting for a person to confirm" in _pend and "<table>" not in _pend)
check("a quiet month says so, with the confirmed count above it",
      "No spend is recorded" in _quiet and "1 confirmed campaign" in _quiet)
_spent = "$" + format(int(round(sum(f["spend"] for f in _mine))), ",")
check("the ok card draws the table with the spend and the unpriced billed cell",
      "<table>" in _ok and _spent in _ok and "not priced" in _ok)
check("...the sold line's band from the board", "CTV: Unmapped" in _ok)
check("...and where to create the link", "create one in Reports" in _ok)
check("a quarantine that would not answer is said, not drawn as nought held",
      "Quarantine could not be read" in _partial and "Feed health could not be read" in _partial)
check("...and the ok card with everything measured says neither",
      "could not be read" not in _ok)


# ------------------------------------------------- the two QA reports
section("The two QA reports read the same store, and say when they could not")
# hub/qa.py's Campaigns With No Client and Lines Pacing Under. Beside the
# generic sweep in test_qa_reports.py (which runs them against an empty
# store) this is what each says with rows behind it, and that a store which
# will not answer is not measured rather than an empty, all-clear table.
from hub import qa as hub_qa                                        # noqa: E402
from modules.reports import pacing as reports_pacing                # noqa: E402
from datetime import timedelta as _td                               # noqa: E402

# A campaign nobody has filed, spending this month, on a platform with no
# other rows: the "filed under nobody" queue.
store.upsert_rows([
    {"platform": "bing", "account_id": "b-77", "campaign_id": "x-9",
     "campaign_name": "Search - Generic", "date": _today, "spend": "12",
     "impressions": 300, "clicks": 4, "source": "windsor"},
])
r = hub_qa.reports_unmapped()
check("Campaigns With No Client is measured with rows behind it",
      r.get("measured") is not False and len(r["rows"]) >= 2)
_cells = [c for row in r["rows"] for c in row]
_heads = [c["text"] for c in _cells if isinstance(c, dict) and c.get("group")]
check("...with the two queues headed apart",
      any(h.startswith("Filed under nobody") for h in _heads)
      and any(h.startswith("Filed from the campaign name") for h in _heads))
_names = [row[1] for row in r["rows"] if not (isinstance(row[0], dict) and row[0].get("group"))]
check("...the unfiled campaign on it", "Search - Generic" in _names)
check("...and the pending one, waiting for a person", "Search - Brand" in _names)
_waits = [row[5] for row in r["rows"] if isinstance(row[5], dict) and row[5].get("href")]
check("...every waiting-on cell links to the queue where it is worked",
      _waits and all(w["href"].startswith("/reports/unmapped") for w in _waits))
check("...and the pending row names whose confirmation it waits on",
      any("Beta LLC" in w["text"] for w in _waits))
check("...one cell per column on every row",
      all(len(row) == len(r["columns"]) for row in r["rows"]))
check("...and the note counts both queues", "1 campaign carrying spend" in r["note"]
      and "1 is filed from the campaign name" in r["note"])
_real = store.unmapped_campaigns
store.unmapped_campaigns = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
try:
    r = hub_qa.reports_unmapped()
finally:
    store.unmapped_campaigns = _real
check("a store that will not answer is not measured, never an all-clear table",
      (r.get("measured"), r["rows"], "could not be read" in r["note"]), (False, [], True))

# Lines Pacing Under: nothing has run, so nothing has a reading.
r = hub_qa.reports_pacing_under()
check("before any pacing run the report is not measured",
      (r.get("measured"), "has not run" in r["note"]), (False, True))
# A Social line on Meta for Acme, sized so the confirmed Meta campaign is
# far under pace on any day of any month the suite runs in.
store.add_budget_line(client="d:acme.com", client_name="Acme Co", product="Social",
                      platform="meta", monthly_budget="30000",
                      flight_start=_date.today().replace(day=1).isoformat(),
                      flight_end=(_date.today() + _td(days=400)).isoformat(),
                      created_by="test")
# Spend on the two completed days before today as well: with only today's
# row the line reads as stalled (no spend yesterday or the day before,
# mid-flight), and what this report is asked to show is under.
store.upsert_rows([
    {"platform": "meta", "account_id": "act_9", "campaign_id": "m-1",
     "campaign_name": "Leads", "date": (_date.today() - _td(days=n)).isoformat(),
     "spend": "5", "impressions": 60, "clicks": 1, "source": "csv"}
    for n in (1, 2)
])
_run = reports_pacing.run(_date.today(), actor="test")
check("the pacing run wrote a snapshot per line", _run["written"] >= 2)
r = hub_qa.reports_pacing_under()
check("after a run the report is measured", r.get("measured") is not False)
_behind = [row for row in r["rows"]]
check("the under-pace Social line is on it",
      any(row[1] == "Social" and row[0]["text"] == "Acme Co" for row in _behind))
check("...and the unmapped CTV line is not -- that is the other report's finding",
      not any(row[1] == "CTV" for row in _behind))
_pill = next((row[6] for row in _behind if row[1] == "Social"), {})
check("...the pace is a pill carrying the ratio", _pill.get("pill") in ("warn", "bad")
      and "×" in _pill.get("text", ""))
check("...the client cell opens Client 360",
      _behind[0][0]["href"].startswith("/client360?q="))
check("...one cell per column on every row",
      all(len(row) == len(r["columns"]) for row in r["rows"]))
check("...and the note says what is not on the list",
      "1 with no campaign filed" in r["note"] and "over pace" in r["note"])
check("...and where the run came from", "on the latest run" in r["note"])
_real = store.latest_snapshots
store.latest_snapshots = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
try:
    r = hub_qa.reports_pacing_under()
finally:
    store.latest_snapshots = _real
check("a store that will not answer is not measured here either",
      (r.get("measured"), "could not be read" in r["note"]), (False, True))
check("both are tiled on /qa under a group",
      (hub_qa.REPORTS["reports-unmapped"]["group"], hub_qa.REPORTS["reports-pacing-under"]["group"]),
      ("Data Quality", "Clients"))


# --------------------------------------------------------- the CSV door
section("A CSV export lands through the one door, as csv, and says what it did")
import io as _io                                                    # noqa: E402

CSV = ("Date,Advertiser ID,Advertiser,Campaign ID,Campaign,Spend,Impressions,Clicks\n"
       f"{_today},adv-5,Acme Co,c-51,Awareness,10.50,1000,30\n"
       f"{_today},adv-5,Acme Co,c-52,Retargeting,4.00,20,90\n")


def _upload(platform, data, name="export.csv", client=None):
    return (client or staff).post(
        "/reports/upload",
        data={"platform": platform, "file": (_io.BytesIO(data), name)},
        content_type="multipart/form-data", follow_redirects=True)


check("a stranger cannot upload",
      Client(wsgi.application).post("/reports/upload", data={"platform": "linkedin"}).status_code in (302, 401))
r = _upload("linkedin", CSV.encode("utf-8"), "li-sept.csv")
body = r.get_data(as_text=True)
check("the upload lands back on the index", r.status_code, 200)
check("...saying what was written, held and skipped",
      "li-sept.csv: 1 campaign-day written for LinkedIn" in body
      and "1 held in quarantine" in body)
_rows = [p for p in store.platform_status() if p["platform"] == "linkedin"][0]
check("the rows are in the fact table for that platform", _rows["rows"], 1)
check("...and the watermark says csv wrote them, not the feed", _rows["sync_source"], "csv")
check("...so the platform is not read as natively current", store.native_is_current("linkedin"), False)
_held = [h for h in __import__("modules.reports.quarantine", fromlist=["held"]).held()
         if h["platform"] == "linkedin"]
check("the row with more clicks than impressions is held, not filed",
      [(h["campaign_id"], h["rule"]) for h in _held], [("c-52", "clicks_over_impressions")])
_un = [u["campaign_id"] for u in store.unmapped_campaigns() if u["platform"] == "linkedin"]
check("the written campaign is on the unmapped queue, filed under nobody", _un, ["c-51"])
# Through the module: the activity log's backend is the database now, so
# reading AUDIT_LOG_PATH reads a fallback nothing writes to.
from hub import audit as _audit                                  # noqa: E402
entries = list(reversed(_audit.read(limit=2000)))
_up = [e for e in entries if e.get("module") == "reports" and e["type"] == "csv_uploaded"]
check("the upload is in the activity log with who did it and what landed",
      (len(_up), _up[-1].get("actor"), _up[-1].get("rows"), _up[-1].get("quarantined"))
      if _up else None, (1, "Todd", 1, 1))

r = _upload("nope", CSV.encode("utf-8"))
check("an unknown platform is refused by name", "Unknown platform" in r.get_data(as_text=True))
r = staff.post("/reports/upload", data={"platform": "linkedin"}, content_type="multipart/form-data",
               follow_redirects=True)
check("no file is refused in words", "Choose a CSV file" in r.get_data(as_text=True))
r = _upload("linkedin", b"Foo,Bar\n1,2\n", "odd.csv")
check("a file without the key columns is refused naming the columns it has",
      "does not carry" in r.get_data(as_text=True) and "Foo, Bar" in r.get_data(as_text=True))
r = _upload("linkedin", b"Date,Advertiser ID,Campaign ID\n,,\n", "blank.csv")
check("a file with no usable row is refused rather than reported as written",
      "carried no usable row" in r.get_data(as_text=True))
_cap = reports_app.MAX_UPLOAD_BYTES
reports_app.MAX_UPLOAD_BYTES = 16
try:
    r = _upload("linkedin", CSV.encode("utf-8"), "huge.csv")
finally:
    reports_app.MAX_UPLOAD_BYTES = _cap
check("a file over the cap is refused by name, and the cap is not silently read anyway",
      "huge.csv is over" in r.get_data(as_text=True))
check("...and nothing from it landed",
      [p for p in store.platform_status() if p["platform"] == "linkedin"][0]["rows"], 1)
r = _upload("linkedin", CSV.encode("utf-8"), "li-sept.csv")
check("the same file twice replaces rather than doubles",
      [p for p in store.platform_status() if p["platform"] == "linkedin"][0]["rows"], 1)
check("the form is on the index for every platform",
      staff.get("/reports/").get_data(as_text=True).count('<option value="') >= len(store.PLATFORMS))


# --------------------------------------------------------------- wired in
section("It is in the nav, tiled once, and named on the crumb trail")

section("what the code-quality analyzer reads as JavaScript")
# CodeQL extracts every {{ ... }} in an HTML file as a JavaScript expression
# and reads a Jinja filter as a pipe: {{ a|b - 3 }} is the call (b - 3)(a),
# reported as invoking a number -- on the PR, on every push, for ever. Two
# lines here did that (the "and N more" count and the pacing bar's width) and
# both moved into Python, which is where arithmetic belongs anyway. A filter
# followed by an operator inside a placeholder is the shape; a filter with
# parentheses ('%.2f'|format(x)) or on its own ([a, b]|min) is not read as one,
# and a placeholder the JavaScript parser rejects outright -- a Python
# conditional, `x if c else y` -- is not read at all, so one carrying ` if ` is
# skipped here for the same reason the analyzer skips it.
_PIPE_ARITH = re.compile(r"\{\{[^}]*\|\s*[A-Za-z_]+\s*[-+*/%][^}]*\}\}")
def _read_as_call(placeholder):
    return bool(_PIPE_ARITH.search(placeholder)) and " if " not in placeholder
_offenders = []
for _t in sorted((ROOT / "modules" / "reports" / "templates").glob("*.html")):
    for _n, _line in enumerate(_t.read_text(encoding="utf-8").splitlines(), 1):
        for _m in re.finditer(r"\{\{[^}]*\}\}", _line):
            if _read_as_call(_m.group(0)):
                _offenders.append(f"{_t.name}:{_n} {_m.group(0)}")
check("no reports template applies an operator to a filter inside a placeholder",
      _offenders, [])
check("...and the sweep can see the shape",
      _read_as_call("{{ held|length - 3 }}") and
      _read_as_call("{{ [r.pace * 100, 200]|min / 2 }}") and
      _read_as_call("{{ '%.2f'|format(o.markup|float * 100) }}"))
check("...and skips what the parser never reads",
      not _read_as_call("{{ '%.2f'|format(t.rule.markup * 100) }}") and
      not _read_as_call("{{ [b.pct, 100]|min }}") and
      not _read_as_call("{{ '%.2f'|format(o.markup|float * 100) if o.markup is defined else '' }}"))

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
