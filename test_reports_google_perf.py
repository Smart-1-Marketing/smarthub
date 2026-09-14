"""The native Google Ads pull: the query, micros, and the not-connected path.

    python3 test_reports_google_perf.py

No pytest, no new dependencies, a throwaway SQLite reports database, and
Google stubbed at the two seams the module reads through --
``google_ads.search`` and ``google_ads.list_client_accounts`` -- so nothing
here reaches the network.

What it holds:

  * not connected (no refresh token / no developer token, this
    deployment's state) is a clean sentence naming the settings screen,
    never an exception, and stamps no watermark over the provider's;
  * the GAQL string names campaign, segments.date and the five metrics
    over an explicit BETWEEN window, and excludes removed campaigns;
  * cost_micros is divided by a million on the way in, once;
  * every non-manager account is queried, a manager and a refused root are
    skipped, and a refused account is isolated and named;
  * the rows land as platform google / source native and stamp the native
    watermark, so the provider normalize then skips google for a day;
  * the index prints the not-connected sentence.
"""
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_gperf_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-gperf-test"
for k in ("GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID",
          "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REDIRECT_URI", "GOOGLE_CLIENT_ID",
          "GOOGLE_CLIENT_SECRET", "GOOGLE_ADS_DAILY_QUOTA"):
    os.environ.pop(k, None)

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


from modules.ads_builder import google_ads                           # noqa: E402
from modules.reports import google_ads_perf as perf, normalize, store  # noqa: E402
_reports_testdb.reset(store)


# ----------------------------------------------------------- not connected
section("Not connected is a sentence")

st = perf.status()
check("not connected on this deployment's state", st["connected"], False)
check("...the line says so and where to fix it",
      st["line"].startswith("Google Ads: not connected — Connect Google Ads in /tools/ads/settings"),
      note=st["line"])
res = perf.pull()
check("a pull returns the clean message", res["error"], perf.NOT_CONNECTED)
check("...without landing anything", (res["ok"], res["rows"]), (False, 0))
check("...and stamps no watermark over the provider's", store.sync_status().get("google"), None)


# ---------------------------------------------------------------- GAQL
section("The query")

q = perf.gaql(date(2026, 8, 30), date(2026, 9, 6))
check("it selects the campaign and the day",
      all(f in q for f in ("campaign.id", "campaign.name", "segments.date")))
check("...and the five metrics",
      all(f in q for f in ("metrics.cost_micros", "metrics.impressions", "metrics.clicks",
                           "metrics.conversions", "metrics.video_views")))
check("...the channel type, which tells a YouTube buy from a search one",
      "campaign.advertising_channel_type" in q)
check("...and the video completion rate, for the completes tile",
      "metrics.video_quartile_p100_rate" in q)
check("...from campaign over an explicit window",
      "FROM campaign" in q and "segments.date BETWEEN '2026-08-30' AND '2026-09-06'" in q)
check("...excluding removed campaigns", "campaign.status != 'REMOVED'" in q)
check("the module has one query and reads it from gaql()",
      (ROOT / "modules" / "reports" / "google_ads_perf.py").read_text().count("SELECT campaign.id"), 1)


# --------------------------------------------------------------- micros
section("Cost is micros, divided once")

facts = perf._facts("123", [
    {"campaign": {"id": "c1", "name": "Winery near me"}, "segments": {"date": "2026-09-05"},
     "metrics": {"costMicros": "12345678", "impressions": "2000", "clicks": "35",
                 "conversions": "3.5", "videoViews": "0"}},
    {"campaign": {"id": "c1", "name": "Winery near me"}, "segments": {},
     "metrics": {"costMicros": "1"}},
], google_ads.micros)
check("one fact per dated row; a row with no day is dropped", len(facts), 1)
check("cost_micros becomes dollars at cents", facts[0]["spend"], 12.35)
check("...through google_ads.micros()", google_ads.micros("12345678"), 12.345678)
check("...platform google, source native", (facts[0]["platform"], facts[0]["source"]), ("google", "native"))
check("...keyed on the account and the campaign", (facts[0]["account_id"], facts[0]["campaign_id"]), ("123", "c1"))
check("...with the counts as integers and conversions as a number",
      (facts[0]["impressions"], facts[0]["clicks"], facts[0]["conversions"]), (2000, 35, 3.5))
check("a row naming no channel carries no extras", "extras" not in facts[0])
check("...and no completes: absent is not measured, never zero", "completes" not in facts[0])

# The channel type rides on the row, and the completes are the p100 rate
# times impressions -- for a video campaign. A search campaign's rate is
# zero, and "0 completes" on it would be a measurement of a metric that
# does not apply, which the tile would then draw as a nought.
facts = perf._facts("123", [
    {"campaign": {"id": "v1", "name": "Brand :30", "advertisingChannelType": "VIDEO"},
     "segments": {"date": "2026-09-05"},
     "metrics": {"costMicros": "50000000", "impressions": "10000", "clicks": "40",
                 "conversions": "0", "videoViews": "6000", "videoQuartileP100Rate": "0.4123"}},
    {"campaign": {"id": "s1", "name": "Roof repair", "advertisingChannelType": "SEARCH"},
     "segments": {"date": "2026-09-05"},
     "metrics": {"costMicros": "50000000", "impressions": "10000", "clicks": "400",
                 "conversions": "12", "videoViews": "0", "videoQuartileP100Rate": "0"}},
    {"campaign": {"id": "p1", "name": "PMax", "advertisingChannelType": "PERFORMANCE_MAX"},
     "segments": {"date": "2026-09-05"},
     "metrics": {"costMicros": "50000000", "impressions": "10000", "clicks": "100",
                 "conversions": "3", "videoViews": "250", "videoQuartileP100Rate": "0.02"}},
], google_ads.micros)
by = {f["campaign_id"]: f for f in facts}
check("the channel type is carried on the row", by["v1"]["extras"], {"channel_type": "VIDEO"})
check("a video campaign's completes are the rate x impressions, rounded", by["v1"]["completes"], 4123)
check("a search campaign carries its channel and no completes",
      (by["s1"]["extras"]["channel_type"], "completes" in by["s1"]), ("SEARCH", False))
check("a campaign of another kind that served video views still counts them",
      (by["p1"]["extras"]["channel_type"], by["p1"]["completes"]), ("PERFORMANCE_MAX", 200))

# What a campaign with no product in its name is filed under.
from modules.reports import products as _products                    # noqa: E402
check("a VIDEO campaign defaults to Online Video, not the platform's Paid Search",
      _products.default_for("google", "VIDEO"), "Online Video")
check("...SEARCH to Paid Search, DISPLAY to Programmatic Display",
      (_products.default_for("google", "SEARCH"), _products.default_for("google", "DISPLAY")),
      ("Paid Search", "Programmatic Display"))
check("a channel the table does not map takes the platform default, and says it was not the channel",
      (_products.default_for("google", "PERFORMANCE_MAX"), _products.channel_decided("google", "PERFORMANCE_MAX")),
      ("Paid Search", False))
check("...and the channel says when it decided", _products.channel_decided("google", "VIDEO"), True)
check("another platform ignores a channel type", _products.default_for("ttd", "VIDEO"), "Streaming TV")
check("every channel product is a catalog product",
      [p for p in _products.GOOGLE_CHANNEL_PRODUCTS.values() if p not in _products.PRODUCTS], [])


# -------------------------------------------------------- a connected pull
section("A connected pull, against a stand-in Google")

calls = []


def fake_status(store=None):
    return {"configured": True, "connected": True, "deploy_ready": True, "missing": []}


def fake_accounts(store=None, *, force=False):
    return [
        {"id": "111", "name": "Smart 1 MCC", "is_manager": True, "level": 0, "manager_id": "111"},
        {"id": "222", "name": "Buckeye Lake Winery", "is_manager": False, "level": 1, "manager_id": "111"},
        {"id": "333", "name": "Acme Roofing", "is_manager": False, "level": 1, "manager_id": "111"},
        {"id": "444", "name": "Unreachable root", "is_manager": True, "level": 0,
         "manager_id": "444", "error": "refused"},
    ]


def fake_search(customer_id, query, *, store=None, login_customer_id=None):
    calls.append((customer_id, login_customer_id, query))
    if customer_id == "333":
        raise google_ads.GoogleAdsError("CUSTOMER_NOT_ENABLED", status=403)
    return [
        {"campaign": {"id": "g-1", "name": "Winery near me", "advertisingChannelType": "SEARCH"},
         "segments": {"date": "2026-09-05"},
         "metrics": {"costMicros": "40000000", "impressions": "20000", "clicks": "2430",
                     "conversions": "76", "videoViews": "0", "videoQuartileP100Rate": "0"}},
        {"campaign": {"id": "g-1", "name": "Winery near me", "advertisingChannelType": "SEARCH"},
         "segments": {"date": "2026-09-06"},
         "metrics": {"costMicros": "41000000", "impressions": "21000", "clicks": "2500",
                     "conversions": "80", "videoViews": "0", "videoQuartileP100Rate": "0"}},
        # A YouTube campaign named in the rename shape with NO product
        # segment: what the auto-mapper files it under is the whole point.
        {"campaign": {"id": "g-2", "name": "S1M | Buckeye Lake Winery | | Harvest :15",
                      "advertisingChannelType": "VIDEO"},
         "segments": {"date": "2026-09-06"},
         "metrics": {"costMicros": "30000000", "impressions": "50000", "clicks": "120",
                     "conversions": "2", "videoViews": "30000", "videoQuartileP100Rate": "0.25"}},
    ]


google_ads.connection_status = fake_status
google_ads.list_client_accounts = fake_accounts
google_ads.search = fake_search

res = perf.pull(days=7, today=date(2026, 9, 6))
check("the pull lands", res["ok"], True, note=res)
check("...three rows for the one account that answered", (res["rows"], res["accounts"]), (3, 1))
check("...the managers were skipped", sorted(res["skipped"]), ["111", "444"])
check("...and the refused account is isolated and named",
      "333" in res["errors"] and "CUSTOMER_NOT_ENABLED" in res["errors"]["333"])
check("every query went under the MCC as the login customer",
      [(c[0], c[1]) for c in calls], [("222", "111"), ("333", "111")])
check("...over the trailing seven days", "BETWEEN '2026-08-30' AND '2026-09-06'" in calls[0][2])

db = store.SessionLocal()
try:
    row = db.get(store.AdPerfDaily, ("google", "222", "g-1", date(2026, 9, 5)))
    landed = (float(row.spend), row.impressions, row.clicks, float(row.conversions), row.source)
finally:
    db.close()
check("a row landed as dollars, native", landed, (40.0, 20000, 2430, 76.0, "native"))
wm = store.sync_status()["google"]
check("the watermark is native, naming the refused account",
      (wm["source"], wm["rows"], "333" in wm["error"]), ("native", 3, True))
check("...so the normalize skips google for a day", store.native_is_current("google"), True)
out = normalize.run(today=date(2026, 9, 6))
check("...and says so", out["google"].get("native"), True)

st = perf.status()
check("the status line now says connected with the last pull", st["line"].startswith("Google Ads: connected, last pull 20"))

# The YouTube campaign: its completes landed, the queue carries its channel
# and opens on Online Video, and the auto-mapper files it under Online
# Video from the channel type -- not under Paid Search from the platform.
db = store.SessionLocal()
try:
    vrow = db.get(store.AdPerfDaily, ("google", "222", "g-2", date(2026, 9, 6)))
    landed_v = (vrow.completes, vrow.video_views, (vrow.extras_json or {}).get("channel_type"))
finally:
    db.close()
check("the video campaign's completes and channel landed", landed_v, (12500, 30000, "VIDEO"))
queue = {r["campaign_id"]: r for r in store.unmapped_campaigns(days=3650)}
check("the unmapped queue carries the channel type",
      (queue["g-2"]["channel_type"], queue["g-1"]["channel_type"]), ("VIDEO", "SEARCH"))
check("...and the product box opens on the channel's product",
      (queue["g-2"]["default_product"], queue["g-1"]["default_product"]), ("Online Video", "Paid Search"))
from hub import clients_registry                                     # noqa: E402
from modules.reports import automap                                  # noqa: E402
_real_all = clients_registry.all_clients
clients_registry.all_clients = lambda refresh=False: [
    {"name": "Buckeye Lake Winery", "slug": "buckeye-lake-winery", "url": "", "domain": "",
     "key": "n:buckeye-lake-winery"}]
try:
    am = automap.run(actor="test")
finally:
    clients_registry.all_clients = _real_all
check("the auto-mapper filed the YouTube campaign", am["mapped"], 1)
m = next(x for x in store.mapped_campaigns(limit=100) if x["campaign_id"] == "g-2")
check("...under Online Video, from the channel type, and the rule says so",
      (m["product"], m["auto_rule"]), ("Online Video", "name_v1+channel_product"))

# The completes reach the client's page as the "Video ads completed" tile
# for the YouTube campaign, and a search-only client gets no such tile --
# a measured nought about a product they are not running.
from modules.reports import client_view                              # noqa: E402
store.map_campaign("google", "222", "g-2", client="n:buckeye-lake-winery",
                   client_name="Buckeye Lake Winery", product="Online Video", mapped_by="Todd")
store.map_campaign("google", "222", "g-1", client="n:acme-search", client_name="Acme Search",
                   product="Paid Search", mapped_by="Todd")
lk_v = store.create_link("n:buckeye-lake-winery", client_name="Buckeye Lake Winery", created_by="Todd")
lk_s = store.create_link("n:acme-search", client_name="Acme Search", created_by="Todd")
agg_v = client_view.build(store.get_link(lk_v.token), "2026-09", date(2026, 9, 6))
agg_s = client_view.build(store.get_link(lk_s.token), "2026-09", date(2026, 9, 6))
tiles_v = {t["key"]: t for t in agg_v.get("tiles") or []}
tiles_s = {t["key"]: t for t in agg_s.get("tiles") or []}
check("the YouTube client's page draws the completes tile from Google's rate x impressions",
      (tiles_v.get("completes") or {}).get("value"), 12500)
check("...labeled as video", (tiles_v.get("completes") or {}).get("label"), "Video ads completed")
check("the search-only client's page draws no completes tile at all", "completes" in tiles_s, False)
prod_v = next((r for r in agg_v.get("products") or [] if r.get("product") == "Online Video"), {})
check("...and the product row carries them", (prod_v.get("completes"), prod_v.get("completion_kind")), (12500, "video"))

# The quota: an exhausted allowance stops the pull by name; an unpublished one does not.
from hub import quotas                                               # noqa: E402

quotas.ads_headroom = lambda rows=None: {"measured": True, "exhausted": True, "used_today": 15000,
                                         "daily_quota": 15000}
res = perf.pull(today=date(2026, 9, 6))
check("an exhausted operation budget refuses by name", "budget is used up" in res["error"])
quotas.ads_headroom = lambda rows=None: {"measured": False, "exhausted": False}
calls.clear()
res = perf.pull(today=date(2026, 9, 6))
check("an unpublished ceiling is not measured and does not stop the pull", res["ok"] and len(calls) == 2)


# ---------------------------------------------------------- the index
section("The Reports index")

from werkzeug.test import Client                                     # noqa: E402

from hub import auth                                                 # noqa: E402
from modules.reports import app as reports_app                       # noqa: E402

google_ads.connection_status = lambda store=None: {"configured": False, "connected": False,
                                                   "deploy_ready": False,
                                                   "missing": ["GOOGLE_ADS_DEVELOPER_TOKEN"]}
c = Client(reports_app.app)
c.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
page = c.get("/").get_data(as_text=True)
check("the page prints the not-connected sentence",
      "Google Ads: not connected — Connect Google Ads in /tools/ads/settings" in page)
check("...naming what is unset", "GOOGLE_ADS_DEVELOPER_TOKEN" in page)
check("...and no traceback", "Traceback" not in page)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
