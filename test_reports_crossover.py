"""The crossover: the client reads the Smart 1 product, never the platform.

    python3 test_reports_crossover.py

No pytest, no new dependencies, a throwaway SQLite reports database,
through the composed app for the public page (its PUBLIC_PREFIXES are a
mount's, and only the composed app exercises them) and the staff page.

What it holds:

  * the product catalog: every media category on the Hub's rate card maps
    to a product, every platform has a default product, and no product
    name is a vendor's;
  * two platforms mapped to one product are ONE bar on the client's page
    and one figure in data.json, summing both; the table is product x
    campaign with the campaign's DISPLAY name, and no public row carries a
    platform key;
  * display_name defaults to the campaign name with the vendor words
    stripped, an S1M-shaped name shows its trailing part, staff can
    override it, and an override that still names a vendor is said;
  * THE HARD RULE: for a client mapped across the Trade Desk, StackAdapt,
    AudioGo and Google Ads -- with investment on, with a campaign named
    after its vendor, with the organic section's "Google search" wording
    on the page -- the public HTML, data.json and the PDF bytes contain
    none of products.FORBIDDEN, and the allowed phrases do pass;
  * a blank product falls back to the generic per-platform label on the
    client's page and is flagged on the staff page as "no product set";
  * the auto-mapper files a name with no product segment under the
    platform's default product and says so on the row.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_xover_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["REPORTS_DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "reports.sqlite3")
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-xover-test"
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
from hub import auth                                                 # noqa: E402
from modules.reports import automap, client_view, organic, products, store  # noqa: E402

TODAY = date.today()
D1 = TODAY - timedelta(days=1) if TODAY.day > 1 else TODAY
CLIENT = "d:riversidedental.test"
NAME = "Riverside Dental"


# ------------------------------------------------------------ the catalog
section("The product catalog")

check("the catalog names no vendor", products.forbidden_hits(" ".join(products.PRODUCTS)), [])
check("every platform has a default product", sorted(products.DEFAULT_PRODUCT_FOR_PLATFORM), sorted(store.PLATFORMS))
check("...each of which is in the catalog",
      [p for p in products.DEFAULT_PRODUCT_FOR_PLATFORM.values() if p not in products.PRODUCTS], [])
rc = products.rate_card_products()
check("the Hub's rate card is readable", rc["available"], True, note=rc.get("error"))
check("...and every one of its categories maps to a product (or to nothing on purpose)", rc["unmapped"], [])
check("...with every mapped name in the catalog",
      [n for n in rc["mapped"].values() if n and n not in products.PRODUCTS], [])
check("the catalog is what the picker offers", products.catalog()[:3], ["Streaming TV", "Streaming Audio", "Online Video"])
check("a typed product is matched to the catalog case-insensitively", products.normalize("paid search"), "Paid Search")
check("...and free text is kept as typed", products.normalize("Custom Radio Bundle"), "Custom Radio Bundle")
check("the allowed phrases are documented with reasons",
      all(products.ALLOWED.get(k) for k in ("Google search", "Facebook & Instagram")))
check("...and none of them trips the rule",
      [k for k in products.ALLOWED if products.forbidden_hits(k)], [])
check("the rule is word-bounded for ttd and bing",
      (products.forbidden_hits("battd bingo"), products.forbidden_hits("TTD Bing")),
      ([], ["bing", "ttd"]))
check("...and catches the vendor names and the word platform",
      products.forbidden_hits("The Trade Desk, StackAdapt, AudioGO, Google Ads, our platforms"),
      ["audiogo", "google ads", "platform", "stackadapt", "trade desk"])


# ----------------------------------------------------------- display names
section("Display names strip the vendor")

for raw, want in (("Fall Wine Weekends - TTD CTV", "Fall Wine Weekends - CTV"),
                  ("StackAdapt Display :: Q4", "Display :: Q4"),
                  ("Google Ads - Brand terms", "Brand terms"),
                  ("Bing | Plumbers", "Plumbers"),
                  ("AudioGo Fall spots", "Fall spots"),
                  ("Winery near me", "Winery near me"),
                  ("The Trade Desk", "")):
    check(f"{raw!r} -> {want!r}", products.strip_vendor_words(raw), want)
check("a name that was nothing but the vendor falls back to the product",
      products.default_display_name("TTD", "Streaming TV"), "Streaming TV")
check("an S1M-shaped name shows its trailing part, never the client key",
      products.default_display_name("S1M | d:acme.com | Streaming TV | Spring push", "Streaming TV"), "Spring push")
check("...and the product when there is no trailing part",
      products.default_display_name("S1M | d:acme.com | Streaming TV", "Streaming TV"), "Streaming TV")


# ------------------------------------------------------------ the facts
# One client across four platforms. ttd and stackadapt both sold as
# Streaming TV; audiogo as Streaming Audio; google as Paid Search. The
# campaign names leak every vendor they can.
store.upsert_rows([
    {"platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1", "date": D1,
     "campaign_name": "Riverside - The Trade Desk CTV", "spend": "100.00",
     "impressions": 600_000, "clicks": 300, "completes": 500_000, "source": "native"},
    {"platform": "stackadapt", "account_id": "sa-1", "campaign_id": "s-1", "date": D1,
     "campaign_name": "StackAdapt CTV - Riverside", "spend": "50.00",
     "impressions": 400_000, "clicks": 200, "completes": 300_000, "source": "native"},
    {"platform": "audiogo", "account_id": "ag-1", "campaign_id": "a-1", "date": D1,
     "campaign_name": "AudioGo :: Fall spots", "spend": "30.00",
     "impressions": 90_000, "clicks": 10, "completes": 80_000, "source": "native"},
    {"platform": "google", "account_id": "123", "campaign_id": "g-1", "date": D1,
     "campaign_name": "Google Ads - Dentist near me", "spend": "40.00",
     "impressions": 20_000, "clicks": 2_400, "conversions": 60, "source": "native"},
])
store.map_campaign("ttd", "adv-1", "t-1", client=CLIENT, client_name=NAME, product="Streaming TV", mapped_by="Todd")
store.map_campaign("stackadapt", "sa-1", "s-1", client=CLIENT, client_name=NAME, product="streaming tv", mapped_by="Todd")
store.map_campaign("audiogo", "ag-1", "a-1", client=CLIENT, client_name=NAME, product="Streaming Audio", mapped_by="Todd")
store.map_campaign("google", "123", "g-1", client=CLIENT, client_name=NAME, product="Paid Search", mapped_by="Todd")
store.set_markup("ttd", cpm="18.00", updated_by="Todd")
store.set_markup("stackadapt", cpm="12.00", updated_by="Todd")
store.set_markup("audiogo", markup="0.30", updated_by="Todd")
store.set_markup("google", markup="0.25", updated_by="Todd")
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")
store.update_link(link.token, show_spend=True, view_json={"rep_name": "Todd", "rep_email": "todd@example.test"})
link = store.get_link(link.token)


# -------------------------------------------------------- one product, one bar
section("Two platforms sold as one product are one bar")

agg = client_view.build(link, "mtd", TODAY)
by = {p["product"]: p for p in agg["products"]}
check("three products, not four platforms", sorted(by), ["Paid Search", "Streaming Audio", "Streaming TV"])
check("Streaming TV sums the Trade Desk and StackAdapt campaigns",
      (by["Streaming TV"]["impressions"], by["Streaming TV"]["clicks"]), (1_000_000, 500))
check("...and its completes", by["Streaming TV"]["completes"], 800_000)
check("...and its investment is both platforms priced by their own rule: 600 x $18 + 400 x $12",
      by["Streaming TV"]["investment"], "$15,600.00")
check("Streaming Audio is priced by the audio markup: $30 x 1.30", by["Streaming Audio"]["investment"], "$39.00")
check("...with listens as its completion", (by["Streaming Audio"]["completes"], by["Streaming Audio"]["completion_kind"]), (80_000, "audio"))
check("the total is the three products", agg["investment"]["total"], "$15,689.00")
check("no product row carries a platform key", not any("platform" in p for p in agg["products"]))
check("...and the answer has no platforms key at all", "platforms" not in agg)
table = {(r["product"], r["campaign"]) for r in agg["table"]}
check("the table is product x campaign, with vendor-stripped display names",
      table, {("Streaming TV", "Riverside - CTV"), ("Streaming TV", "CTV - Riverside"),
              ("Streaming Audio", "Fall spots"), ("Paid Search", "Dentist near me")})
check("...and no table row carries a platform key", not any("platform" in r for r in agg["table"]))
check("the completion tile covers video and audio", [t for t in agg["tiles"] if t["key"] == "completes"][0]["label"],
      "Video & audio ads completed")
check("the bar share is against the biggest product", by["Streaming TV"]["share"], 100)

# One unpriced platform inside a product makes the product delivery only.
store.clear_markup("stackadapt")
agg2 = client_view.build(link, "mtd", TODAY)
tv = [p for p in agg2["products"] if p["product"] == "Streaming TV"][0]
check("one unpriced platform inside a product makes the product delivery only, never a smaller figure",
      (tv["investment"], agg2["investment"]["delivery_only"]), (None, ["Streaming TV"]))
check("...and the total excludes it", agg2["investment"]["total"], "$89.00")
store.set_markup("stackadapt", cpm="12.00", updated_by="Todd")


# ------------------------------------------------------- the hard rule
section("THE HARD RULE: no vendor, and no 'platform', on anything the client receives")

# The organic section on, with a stubbed GA4/GSC so "Google search" and
# "Google Analytics" wording reaches the page -- the allowed phrases.
organic.gate = lambda client_key, client_name="": {
    "gated": True, "product": True, "ga4": True, "gsc": True, "property_id": "p1",
    "google_login": "adops@example.test", "gsc_site": "sc-domain:riversidedental.test",
    "gsc_login": "adops@example.test", "site_url": "https://riversidedental.test",
    "domain": "riversidedental.test", "name": NAME, "products": ["Search Engine Optimization"],
    "why": [], "errors": []}
organic._ga4 = lambda g, rng, today: {"measured": True, "current": {"sessions": 100, "users": 90, "engaged": 60, "key_events": 5},
                                     "previous": {"sessions": 80, "users": 70, "engaged": 50, "key_events": 4},
                                     "change": {"sessions": 25.0, "users": 28.6, "engaged": 20.0, "key_events": 25.0},
                                     "previous_period": {"start": "", "end": ""}, "trend": []}
organic._gsc = lambda g, rng: {"connected": True, "measured": True, "site": "x",
                               "current": {"clicks": 10, "impressions": 200, "ctr": 5.0, "position": 8.1},
                               "previous": {"clicks": 8, "impressions": 150, "ctr": 5.3, "position": 9.0},
                               "change": {"clicks": 25.0, "impressions": 33.3},
                               "queries": [{"key": "dentist", "clicks": 5, "impressions": 100, "ctr": 5.0, "position": 3.0}],
                               "pages": []}
organic._work = lambda g, rng: {"rows": [{"key": "blogs", "label": "Blog posts published", "count": 2}],
                                "latest_score": 81, "latest_scan_at": "", "errors": [], "measured": True}
client_view.forget(link.token)

anon = Client(wsgi.application)
URL = f"/reports/r/c/{link.token}"
r = anon.get(URL)
html = r.get_data(as_text=True)
check("the page opens for a stranger", r.status_code, 200)
check("...with the organic section, so 'Google search' is on it", "Google search" in html and "Organic search" in html)
check("...and Investment on", "$15,689.00" in html)
check("...and every vendor-named campaign shown by its display name",
      all(s in html for s in ("Riverside - CTV", "CTV - Riverside", "Fall spots", "Dentist near me")))
check("the HTML carries none of the forbidden words", products.forbidden_hits(html), [])
r = anon.get(URL + "/data.json")
body = r.get_data(as_text=True)
data = r.get_json()
check("data.json carries none either", products.forbidden_hits(body), [])
check("...and no 'platform' key anywhere in it, at any depth",
      "platform" not in body.lower())
check("...products, not platforms, keyed by product",
      sorted(p["product"] for p in data["products"]), ["Paid Search", "Streaming Audio", "Streaming TV"])
r = anon.get(URL + ".pdf")
pdf = r.get_data()
check("the PDF answers", (r.status_code, pdf[:5]), (200, b"%PDF-"))
check("...and its bytes carry none of the forbidden words", products.forbidden_hits(pdf), [])
check("...while carrying the product figures the page does", b"$15,689.00" in pdf and b"Streaming TV" in pdf)
check("the page and the PDF say 'product', never 'channel' or 'platform'",
      "Product detail" in html and b"Product detail" in pdf)
check("the allowed phrase 'Google search' is on the page and passes", "Google search" in html)
# With products named (the link's opt-in) "Google Analytics" reaches the page and still passes.
store.update_link(link.token, view_json={"name_products": True, "rep_name": "Todd"})
client_view.forget(link.token)
html2 = anon.get(URL).get_data(as_text=True)
check("naming Google's products is allowed and still passes the rule",
      "Google Analytics" in html2 and products.forbidden_hits(html2) == [])
store.update_link(link.token, view_json={"rep_name": "Todd"})
client_view.forget(link.token)

# The rule is a sweep over the rendered output, not over a template: the
# public template's own text is held to it too, so a wording change is
# caught before a client sees it.
pub = (ROOT / "modules" / "reports" / "templates" / "reports_client_public.html").read_text(encoding="utf-8")
pub_body = re.sub(r"\{#.*?#\}", "", pub, flags=re.S)
check("the public template's own copy carries none of the words (comments aside)",
      products.forbidden_hits(pub_body), [])
import ast                                                           # noqa: E402
pdf_tree = ast.parse((ROOT / "modules" / "reports" / "client_pdf.py").read_text(encoding="utf-8"))
pdf_strings = [n.value for n in ast.walk(pdf_tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
pdf_strings = [v for v in pdf_strings if v is not ast.get_docstring(pdf_tree) and "\n" not in v.strip()]
check("nor does the PDF's copy (its string literals, docstrings aside)",
      products.forbidden_hits(" ".join(pdf_strings)), [])


# ------------------------------------------------ staff override of a name
section("Staff override the display name; a leak is said")

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = staff.post(f"/reports/client/{CLIENT}/campaign", data={
    "platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1",
    "display_name": "Streaming TV - Fall push", "product": "Streaming TV"})
check("saving an override redirects back saved", "saved=campaign" in r.headers.get("Location", ""))
check("...and the client's page reads it at once",
      "Streaming TV - Fall push" in anon.get(URL).get_data(as_text=True))
r = staff.post(f"/reports/client/{CLIENT}/campaign", data={
    "platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1",
    "display_name": "TTD Fall push", "product": "Streaming TV"})
check("an override that still names a vendor is saved AND said",
      "still+names+a+vendor" in r.headers.get("Location", "") and "ttd" in r.headers.get("Location", ""))
r = staff.post(f"/reports/client/{CLIENT}/campaign", data={
    "platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1", "display_name": "", "product": "Streaming TV"})
check("an empty box goes back to the vendor-stripped default",
      store.mapped_campaigns_for(CLIENT)[0]["display_name"] if store.mapped_campaigns_for(CLIENT)[0]["platform"] == "ttd"
      else [m for m in store.mapped_campaigns_for(CLIENT) if m["platform"] == "ttd"][0]["display_name"],
      "Riverside - CTV")
r = staff.post(f"/reports/client/{CLIENT}/campaign", data={
    "platform": "meta", "account_id": "x", "campaign_id": "y", "display_name": "z"})
check("a campaign not mapped to this client is refused", "not+mapped" in r.headers.get("Location", ""))
entries = [json.loads(l) for l in Path(os.environ["AUDIT_LOG_PATH"]).read_text().splitlines() if l.strip()]
check("the override reached the activity log under the client",
      [e.get("client") for e in entries if e.get("type") == "campaign_display_saved"][:1], [NAME])
check("the staff page offers the product picker and the display-name box on every row",
      staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True).count('name="display_name"'), 4)


# ------------------------------------------------------- a blank product
section("A blank product falls back and is flagged")

store.upsert_rows([{"platform": "meta", "account_id": "act_1", "campaign_id": "m-1", "date": D1,
                    "campaign_name": "Meta Ads - Implants", "spend": "20.00",
                    "impressions": 5_000, "clicks": 50, "source": "native"}])
store.map_campaign("meta", "act_1", "m-1", client=CLIENT, client_name=NAME, product="", mapped_by="Todd")
client_view.forget(link.token)
data = anon.get(URL + "/data.json").get_json()
labels = sorted(p["product"] for p in data["products"])
check("the blank product shows under the generic per-platform label",
      "Social (Facebook & Instagram)" in labels)
check("...which still passes the rule", products.forbidden_hits(json.dumps(data)), [])
check("...with the vendor stripped from its campaign name",
      [r["campaign"] for r in data["table"] if r["product"] == "Social (Facebook & Instagram)"], ["Implants"])
page = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("the staff page flags it", "no product set" in page and "generic label" in page)
check("...counting one", "1 mapped campaign ha" in page)
check("blank_products() names it", [m["campaign_id"] for m in client_view.blank_products(CLIENT)], ["m-1"])
store.update_link(link.token, view_json={"platform_labels": {"meta": "Social Ads"}})
client_view.forget(link.token)
data = anon.get(URL + "/data.json").get_json()
check("the link's own label override is the fallback where one is set",
      "Social Ads" in [p["product"] for p in data["products"]])
page = staff.get("/reports/unmapped").get_data(as_text=True)
check("the unmapped queue offers the catalog as a datalist", 'id="s1-products"' in page
      and 'list="s1-products"' in (ROOT / "modules" / "reports" / "templates" / "reports_unmapped.html").read_text())
check("...and the recently-mapped list flags the blank", "no product set" in page)


# -------------------------------------------------- automap default product
section("The auto-mapper fills in the platform's default product")

from hub import clients_registry                                      # noqa: E402
clients_registry.all_clients = lambda: [{"name": "Acme Co", "url": "https://acme.test", "key": "d:acme.test"}]
store.upsert_rows([{"platform": "audiogo", "account_id": "ag-2", "campaign_id": "a-2", "date": D1,
                    "campaign_name": "S1M | d:acme.test", "spend": "1", "impressions": 10, "clicks": 0, "source": "native"}])
out = automap.run(actor="test")
check("a two-segment name is filed", out["mapped"], 1)
row = [m for m in store.mapped_campaigns() if m["campaign_id"] == "a-2"][0]
check("...under the platform's default product", row["product"], "Streaming Audio")
check("...and the row says the default was used", row["auto_rule"], "name_v1+default_product")
check("...with a display name that is the product rather than the key", row["display_name"], "Streaming Audio")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
