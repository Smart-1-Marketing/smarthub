"""The organic-search section of a client's report: the gate, and what is said.

    python3 test_reports_seo.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, through the COMPOSED app. Google is stood in for at the
pieces modules/reports/organic.py reads: the SEO product book, the client's
attachments, the Google index, and the Google Finder module's own GA4 and
Search Console calls (sys.modules["gf_app"], the way the Hub reaches it).

What it holds:

  * the gate, all four combinations of SEO product x linked analytics, and
    Search Console deliberately not part of it;
  * the public page and data.json carry the Organic section only when gated
    in, with the client-facing labels and never "GA4" or "Search Console"
    by default -- and with them when the link says name_products;
  * a client without Search Console reads a neutral sentence; the staff
    page names the product and the fix;
  * "what we did" counts only rows that exist -- no zero for a thing the
    client did not buy;
  * the PDF carries the organic figures;
  * the staff page prints the gate row, and the link-analytics notice for
    an SEO client with nothing linked;
  * the public body still carries no spend word and no money.
"""
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_seo_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["REPORTS_DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "reports.sqlite3")
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-seo-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ.pop("PANEL_PASSWORD", None)

_passed = _failed = 0


def check(label, got, want=True, note=None):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}"
              + (f"\n          note: {note!r}" if note is not None else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from werkzeug.test import Client                                     # noqa: E402

import wsgi                                                          # noqa: E402
from hub import auth, seo as hub_seo                                 # noqa: E402
from modules.reports import client_pdf, client_view, organic, store  # noqa: E402

TODAY = date.today()
D1 = TODAY - timedelta(days=1) if TODAY.day > 1 else TODAY
CLIENT = "d:buckeyelakewinery.com"
NAME = "Buckeye Lake Winery"
FORBIDDEN = re.compile(r"spend|cost", re.IGNORECASE)
MONEY = re.compile(r"\$\s?\d")

# One ads campaign so the page has its ads sections above the organic one.
store.upsert_rows([{"platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1",
                    "campaign_name": "Fall Wine Weekends", "date": D1, "spend": "100.00",
                    "impressions": 1_000_000, "clicks": 1120, "source": "windsor"}])
store.map_campaign("ttd", "adv-1", "t-1", client=CLIENT, client_name=NAME,
                   product="Streaming TV", mapped_by="Todd")
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")

# ------------------------------------------------------------ the stand-ins
SEO = {"rows": [], "links": {}, "index": {}}


def fake_seo_rows():
    return SEO["rows"]


def fake_links(name):
    return SEO["links"].get(name, {})


def fake_index(name, url):
    return SEO["index"]


organic._seo_rows = fake_seo_rows
organic._links = fake_links
organic._index_for = fake_index


class FakeGF:
    """Google Finder's importable pieces, recording what was asked."""
    calls = []
    token_error = ""

    def organic_filter(self):
        return {"filter": {"fieldName": "sessionDefaultChannelGroup",
                           "stringFilter": {"matchType": "EXACT", "value": "Organic Search"}}}

    def account_token(self, login):
        self.calls.append(("token", login))
        return ("tok-" + login, "") if not self.token_error else ("", self.token_error)

    def ga4_batch_run_reports(self, token, property_id, reqs):
        self.calls.append(("ga4", property_id, reqs))
        ym = f"{TODAY.year:04d}{TODAY.month:02d}"
        return [
            {"rows": [
                {"dimensionValues": [{"value": "current"}],
                 "metricValues": [{"value": "1200"}, {"value": "900"}, {"value": "700"}, {"value": "31"}]},
                {"dimensionValues": [{"value": "previous"}],
                 "metricValues": [{"value": "1000"}, {"value": "800"}, {"value": "600"}, {"value": "25"}]},
            ]},
            {"rows": [{"dimensionValues": [{"value": ym}], "metricValues": [{"value": "1200"}]}]},
        ]

    def gsc_search_analytics(self, token, site, body):
        self.calls.append(("gsc", site, body))
        dims = body.get("dimensions") or []
        if not dims:
            return {"rows": [{"clicks": 340, "impressions": 21000, "ctr": 0.0162, "position": 12.4}]}
        if dims == ["query"]:
            return {"rows": [{"keys": ["winery near me"], "clicks": 120, "impressions": 4000, "ctr": 0.03, "position": 3.2},
                             {"keys": ["buckeye lake wine"], "clicks": 80, "impressions": 900, "ctr": 0.09, "position": 1.4}]}
        return {"rows": [{"keys": ["https://buckeyelakewinery.com/"], "clicks": 200, "impressions": 9000,
                          "ctr": 0.022, "position": 5.0}]}


gf = FakeGF()
sys.modules["gf_app"] = gf


# ------------------------------------------------------------------ gate
section("The gate: SEO product x linked analytics")

g = organic.gate(CLIENT, NAME)
check("no product, no analytics: off", (g["gated"], g["product"], g["ga4"]), (False, False, False))
check("...saying both", len(g["why"]), 2)

SEO["rows"] = [{"client": NAME, "products": ["SEO - Local"], "url": "https://buckeyelakewinery.com"}]
g = organic.gate(CLIENT, NAME)
check("product, no analytics: off, naming the missing link",
      (g["gated"], g["product"], g["ga4"], g["why"]),
      (False, True, False, ["no Google Analytics property is linked to the client"]))

SEO["rows"] = []
SEO["links"] = {NAME: {"analytics": [{"resource_id": "123456", "google_login": "adops@example.test"}]}}
g = organic.gate(CLIENT, NAME)
check("analytics, no product: off -- we did nothing for them",
      (g["gated"], g["product"], g["ga4"]), (False, False, True))

SEO["rows"] = [{"client": "buckeye lake winery, llc", "products": ["SEO - Local"], "url": "https://buckeyelakewinery.com"}]
g = organic.gate(CLIENT, NAME)
check("the product matches on the exact normalized name", g["product"], True)
SEO["rows"] = [{"client": "Buckeye Lake Winery Supply", "products": ["SEO"], "url": ""}]
check("...and never on a substring", organic.gate(CLIENT, NAME)["product"], False)
SEO["rows"] = [{"client": NAME, "products": ["SEO - Local", "SEO - Blogs"], "url": "https://buckeyelakewinery.com"}]
g = organic.gate(CLIENT, NAME)
check("both: on", g["gated"], True)
check("...carrying the property and the login that reads it",
      (g["property_id"], g["google_login"]), ("123456", "adops@example.test"))
check("...and Search Console is not part of the gate", g["gsc"], False)
check("...the products are named for the staff row", g["products"], ["SEO - Local", "SEO - Blogs"])

SEO["links"] = {NAME: {"analytics": [{"resource_id": "123456"}]}}
g = organic.gate(CLIENT, NAME)
check("a property with no login to read it does not gate in",
      (g["gated"], "no connected login" in " ".join(g["why"])), (False, True))

SEO["links"] = {}
SEO["index"] = {"ga4": [{"resource_id": "777", "google_login": "adops@example.test"}],
                "gsc": [{"resource_id": "sc-domain:buckeyelakewinery.com", "google_login": "adops@example.test"}]}
g = organic.gate(CLIENT, NAME)
check("with nothing attached the Google index answers", (g["gated"], g["property_id"], g["gsc_site"]),
      (True, "777", "sc-domain:buckeyelakewinery.com"))

# From here: attached analytics, no Search Console.
SEO["index"] = {}
SEO["links"] = {NAME: {"analytics": [{"resource_id": "123456", "google_login": "adops@example.test"}]}}


# --------------------------------------------------------- the public page
section("The public page, gated in, without Search Console")

anon = Client(wsgi.application)
client_view.forget(link.token)
r = anon.get(f"/reports/r/c/{link.token}")
html = r.get_data(as_text=True)
check("the page opens", r.status_code, 200)
check("the Organic search section is on it", "Organic search" in html)
check("...below the ads sections and above the CTA",
      html.index("Where your reach came from") < html.index('id="organic-h"') < html.index("Want more from this campaign?"))
check("...with the client-facing labels",
      all(s in html for s in ("Visits from Google search", "Engaged visits", "Key actions taken")))
check("...and the figures", "1,200" in html and "+20.0% vs the period before" in html)
check("...a twelve-month trend of its own", 'id="organic-trend"' in html)
check("...the Search Console sub-block says so, neutrally", organic.GSC_MISSING_CLIENT in html)
check("...never naming GA4 or Search Console", not re.search(r"\bGA4\b|Search Console", html))
check("...and the coming-soon card", "Google Business Profile" in html and "Coming soon" in html)
check("no spend word and no money on the page", not FORBIDDEN.search(html) and not MONEY.search(html),
      note=sorted(set(FORBIDDEN.findall(html)))[:5])
check("the organic request filtered on Organic Search and named its two ranges",
      [c for c in gf.calls if c[0] == "ga4"][0][2][0]["dateRanges"][0]["name"], "current")
check("...for the linked property", [c for c in gf.calls if c[0] == "ga4"][0][1], "123456")

r = anon.get(f"/reports/r/c/{link.token}/data.json")
data = r.get_json()
check("data.json carries the organic block", bool(data.get("organic")))
check("...with the analytics figures", data["organic"]["analytics"]["current"]["sessions"], 1200)
check("...and the change", data["organic"]["analytics"]["change"]["key_events"], 24.0)
check("...search not connected, with the client wording and no staff note",
      (data["organic"]["search"]["connected"], data["organic"]["search"]["note"],
       "staff_note" in data["organic"]["search"]), (False, organic.GSC_MISSING_CLIENT, False))
check("...and no spend word in it", not FORBIDDEN.search(r.get_data(as_text=True)))


# ------------------------------------------------------------- with GSC
section("With Search Console, and what we did")

SEO["links"][NAME]["gsc"] = [{"resource_id": "sc-domain:buckeyelakewinery.com", "google_login": "adops@example.test"}]
hub_store = hub_seo.load_store(NAME)
hub_store["blogs"] = {"posts": [
    {"title": "Harvest", "date": TODAY.replace(day=1).isoformat(), "posted": True},
    {"title": "Next", "date": (TODAY + timedelta(days=40)).isoformat(), "posted": False},
    {"title": "Old", "date": (TODAY - timedelta(days=200)).isoformat(), "posted": True},
]}
hub_store["pages"] = {"https://buckeyelakewinery.com/": {"created": TODAY.isoformat(), "approved": True},
                      "https://buckeyelakewinery.com/tours": {"created": "2020-01-01", "approved": True}}
hub_seo.save_store(NAME, hub_store)
client_view.forget(link.token)
gf.calls.clear()
r = anon.get(f"/reports/r/c/{link.token}")
html = r.get_data(as_text=True)
check("the search block prints clicks, impressions, CTR and position",
      all(s in html for s in ("Clicks from Google search", "Searches that showed your site",
                              "Average position", "21,000", "12.4")))
check("...the top queries and pages", "winery near me" in html and "buckeyelakewinery.com/" in html)
check("...four Search Console calls: two totals, queries, pages",
      [c[2].get("dimensions") for c in gf.calls if c[0] == "gsc"], [[], [], ["query"], ["page"]])
check("...for the property Search Console names", {c[1] for c in gf.calls if c[0] == "gsc"},
      {"sc-domain:buckeyelakewinery.com"})
data = anon.get(f"/reports/r/c/{link.token}/data.json").get_json()
work = data["organic"]["work"]
check("what we did counts the blog post published this period, not the old one",
      [r_ for r_ in work["rows"] if r_["key"] == "blogs"][0]["count"], 1)
check("...and the schema page built this period", [r_ for r_ in work["rows"] if r_["key"] == "schema"][0]["count"], 1)
check("...no FAQ row, because none exist, and no scans row, because none ran",
      {r_["key"] for r_ in work["rows"]}, {"blogs", "schema"})
check("the page prints them", "Blog posts published" in html and "Pages given structured data" in html)
check("still no GA4 / Search Console by name", not re.search(r"\bGA4\b|Search Console", html))
check("...and still no spend word", not FORBIDDEN.search(html))

# The PDF carries the same figures.
r = anon.get(f"/reports/r/c/{link.token}.pdf")
pdf = r.get_data()
check("the PDF answers", r.status_code == 200 and pdf[:5] == b"%PDF-")
check("...with the organic section and its figures",
      all(s in pdf for s in (b"Organic search", b"Visits from Google search", b"1,200", b"21,000", b"Average position")))
check("...and the work rows", b"Blog posts published" in pdf)
check("...never the product names", b"GA4" not in pdf and b"Search Console" not in pdf)
check("client_pdf.build() takes the aggregate as is", client_pdf.build(client_view.aggregate(link, "mtd"))[:5], b"%PDF-")

# Naming the products is the link's decision.
store.update_link(link.token, view_json={"name_products": True})
link = store.get_link(link.token)
client_view.forget(link.token)
html = anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True)
check("with name_products on, the products are named", "Search Console" in html and "Google Analytics 4" in html)
store.update_link(link.token, view_json={})
link = store.get_link(link.token)
client_view.forget(link.token)


# ------------------------------------------------------------- gated out
section("Gated out, the section is simply absent")

SEO["rows"] = []
client_view.forget(link.token)
html = anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True)
check("no SEO product: no organic section", "Organic search" not in html and 'id="organic-h"' not in html)
check("...and not in data.json", anon.get(f"/reports/r/c/{link.token}/data.json").get_json()["organic"], None)
SEO["rows"] = [{"client": NAME, "products": ["SEO - Local"], "url": "https://buckeyelakewinery.com"}]
SEO["links"] = {}
client_view.forget(link.token)
html = anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True)
check("SEO product with no analytics linked: the public page omits it, silently",
      "Organic search" not in html and "Link analytics" not in html)

# A Google that refuses costs the section's figures, never the page.
SEO["links"] = {NAME: {"analytics": [{"resource_id": "123456", "google_login": "adops@example.test"}]}}
gf.token_error = "adops@example.test needs to sign in to Google again."
client_view.forget(link.token)
r = anon.get(f"/reports/r/c/{link.token}")
html = r.get_data(as_text=True)
check("a Google refusal still renders the page", r.status_code, 200)
check("...with the section saying visit data could not be read rather than zeros",
      "Organic search" in html and organic.GA4_MISSING_CLIENT in html and "1,200" not in html)
# The staff half never reaches the client: the account it names is a Hub
# login, and "needs to sign in" is an instruction to somebody with one.
check("...and neither the staff login nor its instruction is on the client's page",
      "adops@example.test" not in html and "needs to sign in" not in html)
check("...nor in data.json", "adops@example.test" not in anon.get(f"/reports/r/c/{link.token}/data.json").get_data(as_text=True))
check("...while the block itself still carries it for staff, under staff_note",
      "adops@example.test" in (organic._ga4({"google_login": "adops@example.test", "property_id": "123456"},
                                          client_view.period_range("mtd"), date.today()).get("staff_note") or ""))
gf.token_error = ""


# ------------------------------------------------------------ staff page
section("The staff page: the gate row and the notice")

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
SEO["links"] = {}
page = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("an SEO client with nothing linked sees the link-analytics notice",
      "Link analytics on the SEO client page" in page)
check("...pointing at where analytics ids are attached", "/seo/client?name=Buckeye%20Lake%20Winery" in page)
check("...and the settings row reads product yes, analytics no",
      "SEO product: <b>yes</b>" in page and "analytics linked: <b>no</b>" in page)
SEO["links"] = {NAME: {"analytics": [{"resource_id": "123456", "google_login": "adops@example.test"}]}}
page = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("linked, the notice is gone and the row says the section is on",
      "Link analytics on the SEO client page" not in page and "Organic search section on" in page)
check("...naming the property", "property 123456" in page)
check("...and Search Console missing, with the fix", "Search Console: <b>no</b>" in page and organic.GSC_MISSING_STAFF in page)
check("...with the name-products checkbox", 'name="name_products"' in page)
r = staff.post(f"/reports/client/{CLIENT}/link", data={"action": "save", "name_products": "1"})
check("saving it stores the flag", store.link_for_client(CLIENT).view.get("name_products"), True)

# The staff wording stays off the public block.
blk = organic.section(link, client_view.period_range("mtd", TODAY), TODAY)
check("the block carries a staff note for search", bool(blk and blk["search"].get("staff_note")))
check("...that public_view strips", "staff_note" not in organic.public_view(blk)["search"])

# Every label a client reads is American English and names no product.
for k, v in organic.LABELS.items():
    check(f"label {k!r} names no product", not re.search(r"\bGA4\b|Search Console", v))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
