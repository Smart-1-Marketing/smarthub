"""The client's live dashboard, its link, the spend rule, the PDF and the push.

    python3 test_reports_public.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, through the COMPOSED app -- wsgi.application -- because the
public half of this is a mount's PUBLIC_PREFIXES handed to AuthGuard and
HubBar, and only the composed app exercises either.

What it holds, the spend rule first because it must not be wrong:

  * pricing.client_price(): the link's own override, then the platform
    markup, then None; markup bills raw x (1 + markup), a CPM bills
    impressions / 1000 x cpm;
  * an unknown token 404s; a replaced token renders the replaced page and
    NOT a 404;
  * with show_spend off, neither the page nor data.json carries a money
    figure, the strings "spend" and "cost" appear nowhere in either body,
    case-insensitively, and neither does "Investment";
  * with show_spend on and a client markup the figure is raw x (1 +
    markup); with a CPM override it is impressions / 1000 x cpm; with no
    rule anywhere the platform shows delivery only and an activity row says
    which;
  * the PDF renders and its bytes carry the same Investment figure the page
    does;
  * the page wears no Hub chrome; the internal /reports/client page is
    refused anonymously and the link routes with it;
  * the push writes the URL onto the client's Suite contact through
    ghl_contacts.upsert with the primary contact's email, never through the
    lead book, and refuses by name when there is no email.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_public_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-public-test"
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
from modules.reports import app as reports_app                       # noqa: E402
from modules.reports import client_pdf, client_view, pricing, store  # noqa: E402
_reports_testdb.reset(store)

TODAY = date.today()
D1 = TODAY - timedelta(days=1) if TODAY.day > 1 else TODAY
CLIENT = "d:buckeyelakewinery.com"
NAME = "Buckeye Lake Winery"


def entries():
    from hub import audit
    return list(reversed(audit.read(limit=2000)))


# ---------------------------------------------------------------- facts
# Two platforms mapped to the client, this month. ttd at 1,000,000
# impressions / $100 raw; google at 20,000 impressions / $40 raw.
store.upsert_rows([
    {"platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1",
     "campaign_name": "Fall Wine Weekends - CTV", "date": D1, "spend": "100.00",
     "impressions": 1_000_000, "clicks": 1120, "completes": 900_000, "source": "windsor"},
    {"platform": "google", "account_id": "123", "campaign_id": "g-1",
     "campaign_name": "Winery near me", "date": D1, "spend": "40.00",
     "impressions": 20_000, "clicks": 2430, "conversions": 76, "source": "windsor"},
    # A campaign nobody has mapped: it must not reach the client's page.
    {"platform": "meta", "account_id": "act_9", "campaign_id": "m-9",
     "campaign_name": "Somebody else's", "date": D1, "spend": "999.00",
     "impressions": 5, "clicks": 1, "source": "windsor"},
])
store.map_campaign("ttd", "adv-1", "t-1", client=CLIENT, client_name=NAME,
                   product="Streaming TV", mapped_by="Todd")
store.map_campaign("google", "123", "g-1", client=CLIENT, client_name=NAME,
                   product="Paid Search", mapped_by="Todd")


# --------------------------------------------------------- the spend rule
section("The spend rule: pricing.client_price()")

check("no rule anywhere is None", pricing.client_price("ttd", "100", 1_000_000, None), None)
store.set_markup("google", markup="0.25", updated_by="Todd")
check("the platform markup bills raw x (1 + markup)",
      pricing.client_price("google", "40.00", 20_000, None), Decimal("50.00"))
store.set_markup("ttd", cpm="18.00", updated_by="Todd")
check("a platform CPM bills impressions / 1000 x cpm, ignoring raw spend",
      pricing.client_price("ttd", "100.00", 1_000_000, None), Decimal("18000.00"))
link_dict = {"markup_json": {"google": {"markup": "0.35"}, "ttd": {"cpm": "20.00"}}}
check("the link's own markup wins over the platform's",
      pricing.client_price("google", "40.00", 20_000, link_dict), Decimal("54.00"))
check("...and its CPM does too",
      pricing.client_price("ttd", "100.00", 1_000_000, link_dict), Decimal("20000.00"))
check("a link with no override falls back to the platform rule",
      pricing.client_price("google", "40.00", 20_000, {"markup_json": {}}), Decimal("50.00"))
check("half-up at cents", pricing.client_price("google", "0.02", 0, {"markup_json": {"google": {"markup": "0.125"}}}),
      Decimal("0.02"))
store.clear_markup("google")
store.clear_markup("ttd")
check("clearing the platform rule takes the figure with it",
      pricing.client_price("google", "40.00", 20_000, {"markup_json": {}}), None)


# ------------------------------------------------------------- the link
section("The link")

check("the module declares the public prefix", reports_app.PUBLIC_PREFIXES, ("/r/c/",))
check("wsgi.py read it from the module", wsgi._REPORTS_PUBLIC, ("/r/c/",))
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")
check("a link is minted with an unguessable token", len(link.token) >= 30)
check("...enabled, and investment off by default", (link.enabled, link.show_spend), (True, False))
check("...and it is the client's live link", store.link_for_client(CLIENT).token, link.token)

anon = Client(wsgi.application)
URL = f"/reports/r/c/{link.token}"

r = anon.get("/reports/r/c/not-a-real-token")
check("an unknown token 404s", r.status_code, 404)
check("...and so does its PDF", anon.get("/reports/r/c/not-a-real-token.pdf").status_code, 404)
check("...and its data", anon.get("/reports/r/c/not-a-real-token/data.json").status_code, 404)


# ------------------------------------------------ investment off (default)
section("With investment off there is no money on the page")

FORBIDDEN = re.compile(r"spend|cost", re.IGNORECASE)
MONEY = re.compile(r"\$\s?\d")

r = anon.get(URL)
html = r.get_data(as_text=True)
check("the page opens for a stranger", r.status_code, 200)
check("...naming the client", NAME in html)
check("...with impressions and clicks tiles",
      "People reached (impressions)" in html and "Clicks to your site" in html)
check("...and the completion tile, because ttd is mapped", "Video ads completed" in html)
check("...but no leads tile, because no suite campaign is mapped", "Leads &amp; bookings" not in html)
check("...the Smart 1 product for ttd, never the vendor's",
      "Streaming TV" in html and "Trade Desk" not in html)
check("...the unmapped campaign is not on it", "Somebody else" not in html)
check("no money figure anywhere on the page", not MONEY.search(html), note=MONEY.findall(html)[:3])
check("neither 'spend' nor 'cost' anywhere in the body", not FORBIDDEN.search(html),
      note=sorted(set(FORBIDDEN.findall(html)))[:5])
check("and not the word Investment", "Investment" not in html)
check("no Hub chrome on it", "s1hub-sb" not in html and "hub-help" not in html)
check("noindex", "noindex" in (r.headers.get("X-Robots-Tag") or ""))
check("the period selector, the trend and the table are there",
      "Last 90 days" in html and 'id="trend"' in html and "Product detail" in html)
check("the PDF button and one mailto", 'href="mailto:' in html and ".pdf?period=" in html)
check("the Updated ... ET footer", "Updated " in html and " ET" in html)
check("a bad period key falls back to month to date",
      "month to date" in anon.get(URL + "?period=nonsense").get_data(as_text=True))

r = anon.get(URL + "/data.json")
data = r.get_json()
body = r.get_data(as_text=True)
check("data.json answers", r.status_code, 200)
check("...with no money figure", not MONEY.search(body))
check("...and neither 'spend' nor 'cost' in any key or value", not FORBIDDEN.search(body),
      note=sorted(set(FORBIDDEN.findall(body)))[:5])
check("...and no investment key", "investment" not in data and not any("investment" in p for p in data["products"]))
check("...the product totals are the mapped campaigns' only, by product and never by platform",
      {p["product"]: p["impressions"] for p in data["products"]}, {"Streaming TV": 1_000_000, "Paid Search": 20_000})
check("...and no row carries a platform key", not any("platform" in p for p in data["products"] + data["table"]))
check("...twelve months of trend", len(data["trend"]), 12)
check("...only the last of which is the month in progress",
      [t["partial"] for t in data["trend"]], [False] * 11 + [True])
check("the page draws the partial month dashed and hollow, and says so",
      'stroke-dasharray="4 4"' in html and '<g fill="#fff" stroke="#2a78d6"' in html
      and "(month to date)" in html and "current month to date" in html)
check("the Smart 1 fallback logo is a chip, not an image that can 404",
      'class="logo s1"' in html and "<img" not in html.split("</header>")[0])
check("...ending this month with this month's impressions",
      data["trend"][-1]["impressions"], 1_020_000)
check("the view was counted", store.get_link(link.token).view_count >= 1)


# ------------------------------------------------------------ investment on
section("With investment on, the figure is the spend rule's and nothing else")

store.update_link(link.token, show_spend=True,
                  markup_json={"google": {"markup": "0.35"}}, view_json={})
client_view.forget(link.token)
store.set_markup("ttd", cpm="18.00", updated_by="Todd")

r = anon.get(URL + "/data.json")
data = r.get_json()
by = {p["product"]: p for p in data["products"]}
check("google is raw x (1 + the client's markup): $40 x 1.35",
      by["Paid Search"]["investment"], "$54.00")
check("ttd is impressions / 1000 x the platform CPM: 1,000 x $18",
      by["Streaming TV"]["investment"], "$18,000.00")
check("the total is their sum", data["investment"]["total"], "$18,054.00")
check("an Investment tile is on the page", [t for t in data["tiles"] if t["key"] == "investment"][0]["display"],
      "$18,054.00")
check("the raw figures are in no key of the answer",
      "40.00" not in json.dumps(data) and "100.00" not in json.dumps(data))
check("...and 'spend' is still nowhere in the body", not FORBIDDEN.search(r.get_data(as_text=True)))

html = anon.get(URL).get_data(as_text=True)
check("the page prints Investment", "Investment" in html and "$18,054.00" in html)
check("...and still never the word spend", not FORBIDDEN.search(html))

# A CPM override on the link beats the platform CPM.
store.update_link(link.token, markup_json={"google": {"markup": "0.35"}, "ttd": {"cpm": "20.00"}})
client_view.forget(link.token)
data = anon.get(URL + "/data.json").get_json()
check("a link CPM override: 1,000 x $20", [p for p in data["products"] if p["product"] == "Streaming TV"][0]["investment"],
      "$20,000.00")

# No rule anywhere for a platform: delivery only, and an activity row.
store.update_link(link.token, markup_json={"google": {"markup": "0.35"}})
store.clear_markup("ttd")
client_view.forget(link.token)
before = len([e for e in entries() if e.get("action") == "reports_markup_missing"])
data = anon.get(URL + "/data.json").get_json()
ttd = [p for p in data["products"] if p["product"] == "Streaming TV"][0]
check("with no rule anywhere ttd's product shows delivery only", ttd["investment"], None)
check("...still with its delivery figures", ttd["impressions"], 1_000_000)
check("...named on the answer by product", data["investment"]["delivery_only"], ["Streaming TV"])
check("...and the total is google's alone", data["investment"]["total"], "$54.00")
missing = [e for e in entries() if e.get("action") == "reports_markup_missing"]
check("an activity row says which platform needs a rule",
      len(missing) > before and missing[-1].get("platform") == "ttd" and missing[-1].get("client") == NAME)
html = anon.get(URL).get_data(as_text=True)
check("the page says delivery only for it", "delivery only" in html)


# ---------------------------------------------------------------- the PDF
section("The PDF")

store.set_markup("ttd", cpm="18.00", updated_by="Todd")
client_view.forget(link.token)
page = anon.get(URL).get_data(as_text=True)
r = anon.get(URL + ".pdf")
check("the PDF answers", r.status_code, 200)
check("...as a PDF", r.headers.get("Content-Type", "").startswith("application/pdf"))
pdf = r.get_data()
check("...that is one", pdf[:5], b"%PDF-")
check("the page and the PDF carry the same Investment total",
      "$18,054.00" in page and b"$18,054.00" in pdf)
check("...and the same per-platform figure", b"$54.00" in pdf)
check("the PDF names the client", NAME.encode() in pdf)
check("...and never the word spend", not FORBIDDEN.search(pdf.decode("latin-1")))
check("...and it says the current month is to date", b"to date" in pdf)
agg = client_view.aggregate(store.get_link(link.token), "mtd")
check("client_pdf.build() takes the aggregate as is", client_pdf.build(agg)[:5], b"%PDF-")

# ---- cached like the page. The PDF used to be rebuilt on every request
# while the page beside it was served from cache: a client refreshing the
# download was a reportlab render each time, on a route a stranger can
# reach with no login. Same key as the aggregate, so it moves when the
# page moves and never serves numbers the page has stopped showing.
_real_build = client_pdf.build
_builds = []


def _counting_build(agg):
    _builds.append(1)
    return _real_build(agg)


client_pdf.build = _counting_build
try:
    client_view.forget(link.token)
    first = anon.get(URL + ".pdf").get_data()
    second = anon.get(URL + ".pdf").get_data()
    check("the second request is served from the cache, not rebuilt", len(_builds), 1)
    check("...byte for byte", first == second)
    anon.get(URL + ".pdf?period=last_month")
    check("a different period is its own entry", len(_builds), 2)
    store.set_markup("ttd", cpm="19.00", updated_by="Todd")
    anon.get(URL + ".pdf")
    check("a markup saved on /reports/markup reaches the PDF with no forget(), like the page",
          len(_builds), 3)
    store.set_markup("ttd", cpm="18.00", updated_by="Todd")
    anon.get(URL + ".pdf")
    client_view.forget(link.token)
    anon.get(URL + ".pdf")
    check("forget() drops the PDF with the aggregate", len(_builds), 5)
    check("client_view.pdf_bytes is what the route reads",
          client_view.pdf_bytes(store.get_link(link.token), "mtd")[:5], b"%PDF-")
finally:
    client_pdf.build = _real_build
    client_view.forget(link.token)


# ------------------------------------------------------- a replaced link
section("A replaced link")

old = link.token
link2 = store.create_link(CLIENT, client_name=NAME, created_by="Todd")
check("regenerating mints a new token", link2.token != old)
check("...carrying the settings over", (link2.show_spend, link2.markups), (True, {"google": {"markup": "0.3500"}}))
check("...and disabling the old row", store.get_link(old).enabled, False)
check("...so there is one live link", store.link_for_client(CLIENT).token, link2.token)
r = anon.get(f"/reports/r/c/{old}")
check("the old token renders the replaced page, not a 404", r.status_code, 410)
check("...saying so", "replaced" in r.get_data(as_text=True) and "Smart 1 rep" in r.get_data(as_text=True))
check("...with no numbers on it", "impressions" not in r.get_data(as_text=True).lower())
check("...and its PDF the same", anon.get(f"/reports/r/c/{old}.pdf").status_code, 410)
check("the new token opens", anon.get(f"/reports/r/c/{link2.token}").status_code, 200)


# ------------------------------------------- a pricing change reaches the page
section("A markup saved elsewhere reaches the page without forget()")

# The cache key carries the pricing rule's version and the client's
# mappings, so a change on one gunicorn worker is a miss on the other --
# forget() is per process and could only ever empty one of the two.
store.update_link(link2.token, show_spend=True, markup_json={}, view_json={})
store.set_markup("ttd", cpm="18.00", updated_by="Todd")
warm = client_view.aggregate(link2, "mtd")
store.set_markup("ttd", cpm="20.00", updated_by="Todd")
cold = client_view.aggregate(link2, "mtd")
tv = lambda a: {p["product"]: p for p in a["products"]}["Streaming TV"]["investment"]  # noqa: E731
check("a CPM changed on /reports/markup is on the next read, with no forget() anywhere",
      (tv(warm), tv(cold)), ("$18,000.00", "$20,000.00"))
_m = store.mapped_campaigns_for(CLIENT)[0]
store.set_display(_m["platform"], _m["account_id"], _m["campaign_id"], display_name="Renamed by staff")
check("...and so is a display name corrected on the staff page",
      any(r["campaign"] == "Renamed by staff" for r in client_view.aggregate(link2, "mtd")["table"]))
store.set_markup("ttd", cpm="18.00", updated_by="Todd")

# ----------------------------------------- tiles are gated on the period
section("A tile is drawn for what ran in the period, never for what was ever mapped")

OLD = "n:old-video-client"
store.upsert_rows([{"platform": "ttd", "account_id": "adv-9", "campaign_id": "old-1",
                    "campaign_name": "old", "date": date(2025, 1, 15), "spend": 10, "impressions": 100,
                    "clicks": 1, "conversions": 0, "completes": 80, "source": "native"}])
store.map_campaign("ttd", "adv-9", "old-1", client=OLD, client_name="Old Video Client",
                   product="Streaming TV", mapped_by="Todd")
store.map_campaign("suite", "loc-9", "forms", client=OLD, client_name="Old Video Client",
                   product="Smart 1 Suite", mapped_by="Todd")
old_link = store.create_link(OLD, client_name="Old Video Client", created_by="Todd")
old_tiles = [t["key"] for t in client_view.aggregate(old_link, "mtd")["tiles"]]
check("a video platform mapped but silent this month draws no completion tile",
      "completes" not in old_tiles)
check("...and a Suite mapping with no outcome rows draws no leads tile", "leads" not in old_tiles)
check("...while the reach tiles are still there", old_tiles[:2], ["impressions", "clicks"])

# ----------------------------------------------------------- rate limit
section("The rate limit")

reports_app.PUBLIC_LIMIT, _saved = 3, reports_app.PUBLIC_LIMIT
reports_app._PUBLIC_HITS.clear()
codes = [anon.get(f"/reports/r/c/{link2.token}", headers={"X-Forwarded-For": "203.0.113.9"}).status_code
         for _ in range(5)]
check("past the limit an address is refused with a 429", codes, [200, 200, 200, 429, 429])
reports_app.PUBLIC_LIMIT = _saved
reports_app._PUBLIC_HITS.clear()


# --------------------------------------------------------- staff routes
section("The internal view is staff-only")

paths = [f"/reports/client/{CLIENT}"]
for p in paths:
    r = anon.get(p)
    check(f"GET {p} is refused anonymously",
          r.status_code in (302, 401) and (r.status_code == 401 or r.headers.get("Location", "").startswith("/login")))
for p in (f"/reports/client/{CLIENT}/link", f"/reports/client/{CLIENT}/push"):
    r = anon.post(p, data={"action": "create"})
    check(f"POST {p} is refused anonymously", r.status_code in (302, 401))

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = staff.get(f"/reports/client/{CLIENT}")
page = r.get_data(as_text=True)
check("staff get the internal view", r.status_code, 200)
check("...with raw spend, staff only", "Raw spend" in page and "$100.00" in page)
check("...and no blank-product warning, because both mappings carry one", "no product set" not in page)
check("...beside what the client sees", "Client sees" in page and "$18,000.00" in page)
check("...the mapped campaigns", "t-1" in page and "g-1" in page)
check("...the public URL", f"https://hub.example.test/reports/r/c/{link2.token}" in page)
check("...the four buttons", all(s in page for s in ("Regenerate", "Copy URL", "Push to Smart 1 Suite", "Save settings")))
check("...wearing the Hub chrome", "s1hub-sb" in page)
check("...the settings table with the two columns",
      "Markup % (this client)" in page and "Fixed CPM (this client)" in page)
check("...no pacing without a budget line", "No budget lines" in page)
check("the index lists the client", NAME in staff.get("/reports/").get_data(as_text=True))

store.add_budget_line(client=CLIENT, client_name=NAME, product="Streaming TV",
                      platform="ttd", monthly_budget="3000", created_by="Todd")
pacing = client_view.pacing(CLIENT, TODAY)
check("a budget line paces against days elapsed",
      pacing and pacing[0]["expected"] == (Decimal("3000") * TODAY.day / Decimal(
          __import__("calendar").monthrange(TODAY.year, TODAY.month)[1])).quantize(Decimal("0.01")))
check("...in one of the board's five bands", pacing[0]["band"] in ("under", "on", "over", "stalled", "unmapped"))
# One engine: the staff client page reads pacing.compute() for this client
# rather than a second reading that filtered by platform alone.
from modules.reports import pacing as pacing_mod                     # noqa: E402
board_rows = {r["line_id"]: r for r in pacing_mod.compute(TODAY, client=CLIENT)}
check("the client page's pacing rows are the board's own, line for line",
      {p["line_id"]: (p["spent"], p["expected"], p["band"]) for p in pacing},
      {i: (r["actual_to_date"], r["expected_to_date"], r["band"]) for i, r in board_rows.items()})
check("...and compute(client=) narrows to that client alone",
      all(r["client"] == CLIENT for r in board_rows.values()) and bool(board_rows))
page = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("...drawn as a bar on the page", 'class="fill ' in page and "expected so far" in page)

# Saving settings through the form.
r = staff.post(f"/reports/client/{CLIENT}/link", data={
    "action": "save", "show_spend": "1", "link_markup_google": "40",
    "label_ttd": "Streaming TV", "hide_meta": "1", "rep_name": "Todd Swickard",
    "rep_email": "todd@example.test"})
check("saving settings redirects back", r.status_code, 302)
saved = store.link_for_client(CLIENT)
check("...with the markup as a fraction", saved.markups.get("google"), {"markup": "0.4000"})
check("...the label, the hidden platform and the rep",
      (saved.view.get("platform_labels"), saved.view.get("hidden_platforms"), saved.view.get("rep_name")),
      ({"ttd": "Streaming TV"}, ["meta"], "Todd Swickard"))
html = anon.get(f"/reports/r/c/{link2.token}").get_data(as_text=True)
check("the public page reads them at once", "Todd Swickard" in html and "mailto:todd@example.test" in html)
r = staff.post(f"/reports/client/{CLIENT}/link", data={
    "action": "save", "link_markup_google": "40", "link_cpm_google": "5"})
check("both boxes on one platform is refused", "not+both" in r.headers.get("Location", ""))
r = staff.post(f"/reports/client/{CLIENT}/link", data={
    "action": "save", "show_spend": "1", "link_markup_google": "1500"})
check("a per-link markup over the ceiling is refused by name, at the same door as the platform rule",
      "outside+the+0-300%" in r.headers.get("Location", ""))
r = staff.post(f"/reports/client/{CLIENT}/link", data={
    "action": "save", "show_spend": "1", "link_cpm_ttd": "9999"})
check("...and a per-link CPM over it", "$0-$250" in r.headers.get("Location", ""))
check("...with the link's settings exactly as they were",
      store.link_for_client(CLIENT).markups.get("google"), {"markup": "0.4000"})
check("the settings and the link writes reached the activity log under the client",
      "report_link_settings" in {e.get("type") for e in entries() if e.get("client") == NAME})
staff.post(f"/reports/client/{CLIENT}/link", data={"action": "regenerate"})
check("regenerating from the page logs it under the client",
      [e for e in entries() if e.get("type") == "report_link_regenerated"][-1].get("client"), NAME)
link2 = store.link_for_client(CLIENT)


# ----------------------------------------------------------------- push
section("Pushing the link to Smart 1 Suite")

from hub import ghl_contacts, seo as hub_seo                          # noqa: E402

calls = []


def fake_upsert(row):
    calls.append(row)
    return {"ok": True, "contact_id": "c_123", "error": "", "was_new": False}


ghl_contacts.upsert = fake_upsert
ghl_contacts.configured = lambda: True
ghl_contacts.report_field_ids = lambda: {"report_url": "fld_report", "pdf_url": ""}
hub_seo.get_profile = lambda client: {"contacts": []}

r = staff.post(f"/reports/client/{CLIENT}/push")
check("with no primary contact email the push refuses by name",
      "No+primary+contact+email" in r.headers.get("Location", ""))
check("...and wrote nothing", calls, [])

hub_seo.get_profile = lambda client: {"contacts": [
    {"name": "Pat Owner", "email": "pat@buckeyelakewinery.com", "phone": "", "primary": True}]}
r = staff.post(f"/reports/client/{CLIENT}/push")
check("with one it upserts the contact", len(calls), 1)
check("...matching on the primary contact's email", calls[0]["fields"]["email"], "pat@buckeyelakewinery.com")
check("...carrying the public URL as the report link",
      calls[0]["meta"]["report_url"], f"https://hub.example.test/reports/r/c/{link2.token}")
check("...under the reports source, not as a lead", calls[0]["source"], "reports")
check("...and says so", "saved=pushed" in r.headers.get("Location", ""))
pushed = [e for e in entries() if e.get("type") == "report_link_pushed"]
check("...with an activity row under the client", bool(pushed) and pushed[-1].get("client") == NAME)
check("it never wrote a lead row",
      not (Path(os.environ["HUB_DATA_DIR"]) / "leads.json").exists()
      and "hub.leads" not in (ROOT / "modules" / "reports" / "app.py").read_text().split("def client_push")[1])

ghl_contacts.report_field_ids = lambda: {"report_url": "", "pdf_url": ""}
r = staff.post(f"/reports/client/{CLIENT}/push")
check("with no report-URL field configured it refuses by name",
      "GHL_LEAD_REPORT_URL_FIELD_ID" in r.headers.get("Location", ""))


# --------------------------------------------------------------- copy
section("What the copy says")

for tpl in ("reports_client_public.html", "reports_client.html"):
    src = (ROOT / "modules" / "reports" / "templates" / tpl).read_text(encoding="utf-8")
    check(f"{tpl} never says GoHighLevel", not re.search(r"gohighlevel|highlevel|\bghl\b", src, re.I))
pub = (ROOT / "modules" / "reports" / "templates" / "reports_client_public.html").read_text(encoding="utf-8")
check("the public template never says spend or cost", not FORBIDDEN.search(pub),
      note=sorted(set(FORBIDDEN.findall(pub))))
check("the public template carries no fetch, so it needs no wait mark",
      not re.search(r"\bfetch\s*\(|sendBeacon", pub))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
