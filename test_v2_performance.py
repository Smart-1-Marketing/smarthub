"""get_client_performance: the ad-performance read Ask SmartHub answers from.

    python3 test_v2_performance.py

No pytest. A throwaway reports database -- the SQLite file by default, the
job's Postgres when REPORTS_TEST_DATABASE_URL is set, which is how
checks.yml runs this file a second time. Every date is fixed and every
figure is asserted to the cent.

What it holds, and every one of these is a way to be confidently wrong:

  * two spellings of one client, rows filed under both, summed ONCE -- the
    failure that doubles a client's spend;
  * a pending mapping counted and named but never summed, which is
    store.facts_for's rule and has to survive this layer;
  * a compare window with no rows: every delta null, no move flags, and the
    note says so -- never a first month reported as an infinite rise;
  * the flag floors surviving real data: the CTR drop needs 500 impressions
    on both sides, the CPA multiple needs three conversions;
  * roas null, with a note naming the platform, because no sync writes a
    conversion value;
  * an unknown platform or product refused BY NAME with no query run;
  * the pacing block equal to pacing.compute(client=) field for field -- the
    "two engines" bug must not come back;
  * a quarantined day surfacing in quarantined_days and in the note;
  * nothing raising: a store that will not answer is a payload, not a 500;
  * the tool registered in both catalogs it has to be in, with the read-only
    annotations, and reachable at the role Ask SmartHub grants.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1v2perf_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                              # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["SECRET_KEY"] = "v2-performance-test"

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


from mcp_gateway import v2_tools                                    # noqa: E402
from modules.reports import flags as flag_rules                     # noqa: E402
from modules.reports import pacing as pacing_mod                    # noqa: E402
from modules.reports import store                                   # noqa: E402

_reports_testdb.reset(store)

# ---------------------------------------------------------------------------
# One client, filed under two spellings, on a fixed clock.
# ---------------------------------------------------------------------------
CLIENT = "Quality Air Columbus"
KEY = "n:quality-air-columbus"          # what hub/client_key.py mints
NAME_KEY = CLIENT                        # what the proposal adapter files under

IDENTITY = {"known": True, "client": CLIENT, "client_key": KEY,
            "domain": "qualityaircolumbus.com", "matched_on": "exact",
            "confidence": "exact", "candidates": [], "why": ""}

# Today is fixed so "this month" is a known 15 days. The fact rows sit in
# September; the compare window is the 15 days before it.
TODAY = date(2026, 9, 16)
WINDOW = [date(2026, 9, d) for d in range(1, 16)]
PRIOR = [date(2026, 8, d) for d in range(17, 32)]


def rows_for(days, campaign_id, name, *, platform="google", account="111",
             spend=0.0, imps=0, clicks=0, convs=0.0):
    """One campaign's rows, the same figures each day of a window."""
    return [{"platform": platform, "account_id": account,
             "campaign_id": campaign_id, "campaign_name": name, "date": d,
             "spend": spend, "impressions": imps, "clicks": clicks,
             "conversions": convs, "source": "native"} for d in days]


written = []
# Brand: healthy, converting, under both spellings' campaigns.
written += rows_for(WINDOW, "c-brand", "Brand - Columbus",
                    spend=40.0, imps=4000, clicks=120, convs=4.0)
written += rows_for(PRIOR, "c-brand", "Brand - Columbus",
                    spend=40.0, imps=4000, clicks=120, convs=4.0)
# Generic: dear per conversion -- 4x the account -- and enough conversions.
written += rows_for(WINDOW, "c-generic", "Generic - HVAC",
                    spend=60.0, imps=3000, clicks=60, convs=0.4)
written += rows_for(PRIOR, "c-generic", "Generic - HVAC",
                    spend=60.0, imps=3000, clicks=60, convs=0.4)
# Fell off a cliff: CTR halved, on plenty of impressions both sides.
written += rows_for(WINDOW, "c-cliff", "Retarget - Columbus",
                    spend=20.0, imps=6000, clicks=30, convs=1.0)
written += rows_for(PRIOR, "c-cliff", "Retarget - Columbus",
                    spend=20.0, imps=6000, clicks=90, convs=1.0)
# Dead: spend, no conversions, on a platform that reports them.
written += rows_for(WINDOW, "c-dead", "Competitor terms",
                    spend=30.0, imps=900, clicks=20, convs=0.0)
# Pending: filed but never confirmed, so its spend reaches no figure.
written += rows_for(WINDOW, "c-pending", "Spring promo",
                    spend=500.0, imps=90000, clicks=900, convs=90.0)
# A second spelling's campaign, on a second account, under the NAME key.
written += rows_for(WINDOW, "c-video", "Streaming - Columbus",
                    platform="ttd", account="222",
                    spend=25.0, imps=20000, clicks=10, convs=0.0)

store.upsert_rows(written, screen=False)

for cid, product in (("c-brand", "Paid Search"), ("c-generic", "Paid Search"),
                     ("c-cliff", "Retargeting"), ("c-dead", "Paid Search")):
    store.map_campaign("google", "111", cid, client=KEY, client_name=CLIENT,
                       product=product, mapped_by="tester")
    store.confirm_mapping("google", "111", cid, by="tester")
# The second spelling: the display-name key the proposal adapter writes.
store.map_campaign("ttd", "222", "c-video", client=NAME_KEY, client_name=CLIENT,
                   product="Streaming TV", mapped_by="tester")
store.confirm_mapping("ttd", "222", "c-video", by="tester")
# Filed by the auto-mapper and never confirmed.
store.map_campaign("google", "111", "c-pending", client=KEY, client_name=CLIENT,
                   product="Paid Search", mapped_by=store.AUTO_MAPPED_BY,
                   auto_rule="name")

store.add_budget_line(client=KEY, product="Paid Search", monthly_budget=Decimal("6500"),
                      platform="google", flight_start=date(2026, 9, 1),
                      flight_end=date(2026, 9, 30), sold_amount=Decimal("8100"),
                      created_by="tester")


def run(**kw):
    """The tool, with identity resolution stubbed to this one client."""
    kw.setdefault("client_name", CLIENT)
    with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
         patch("modules.reports.pacing.date", wraps=date) as clock:
        clock.today.return_value = TODAY
        return v2_tools.client_performance(**kw)


def at(period="this_month", **kw):
    with patch("hub.periods.date", wraps=date) as clock:
        clock.today.return_value = TODAY
        return run(period=period, **kw)


section("Two spellings of one client, summed once")
out = at()
check("the read succeeded", (out["found"], out["available"]), (True, True))
check("state is ok", out["state"], "ok")
check("both spellings' keys were read",
      KEY in out["keys"] and NAME_KEY in out["keys"])
check("both platforms are in the totals",
      sorted(r["platform"] for r in out["by_platform"]), ["google", "ttd"])
# Brand 40 + Generic 60 + Cliff 20 + Dead 30 = 150/day google, 25/day ttd,
# over 15 days, and the pending 500/day is not in it.
check("spend is summed once, not twice", out["totals"]["spend"], 2625.0)
check("...google's share", next(r for r in out["by_platform"]
                               if r["platform"] == "google")["spend"], 2250.0)
check("...and the second spelling's", next(r for r in out["by_platform"]
                                           if r["platform"] == "ttd")["spend"], 375.0)
check("the campaign table carries every confirmed campaign",
      sorted(r["campaign"] for r in out["campaigns"]),
      ["Brand - Columbus", "Competitor terms", "Generic - HVAC",
       "Retarget - Columbus", "Streaming - Columbus"])

section("The window is the one that was asked for, and it is echoed back")
check("this month runs to yesterday",
      (out["window"]["start"], out["window"]["end"]), ("2026-09-01", "2026-09-15"))
check("...15 days", out["window"]["days"], 15)
check("...with the label the answer quotes",
      out["window"]["label"], "Sep 1 - Sep 15, 2026 (this month to date)")
check("the compare window is the 15 days before it",
      (out["compare"]["start"], out["compare"]["end"]), ("2026-08-17", "2026-08-31"))
check("...and names its mode", out["compare"]["mode"], "previous_period")
check("data_through is the latest fact date", out["data_through"], "2026-09-15")

section("Derived metrics, and a ratio with no denominator")
totals = out["totals"]
check("impressions", totals["impressions"], 15 * (4000 + 3000 + 6000 + 900 + 20000))
check("CTR is a percentage to two places", totals["ctr"],
      round(15 * 240 / (15 * 33900) * 100, 2))
check("CPC", totals["cpc"], round(2625.0 / (15 * 240), 2))
check("CPA is spend over conversions", totals["cpa"],
      round(2625.0 / (15 * 5.4), 2))
ttd = next(r for r in out["by_platform"] if r["platform"] == "ttd")
check("a platform with no conversions has no CPA", ttd["cpa"], None)
check("...and it is null, never zero", "cpa" in ttd and ttd["cpa"] is None)

section("A pending mapping is counted and named, never summed")
check("one campaign is pending", out["pending_campaigns"], 1)
check("...and is named", out["pending_campaign_names"], ["Spring promo"])
check("...its spend is not in the totals", 500.0 * 15 not in
      [r["spend"] for r in out["campaigns"]])
check("...and the note says so", "waiting for confirmation" in out["note"])
check("the confirmed count is the other five", out["campaigns_confirmed"], 5)

section("roas: null with a note, because no sync writes a conversion value")
check("roas is null", totals["roas"], None)
check("...and never zero", totals.get("roas") is None)
check("the note names the platforms", "conversion value not reported by"
      in totals.get("roas_note", ""))
check("Google Ads is named in it", "Google Ads" in totals.get("roas_note", ""))

section("The flags are computed here, with their floors intact")
codes = sorted({f["code"] for f in out["flags"]})
by_campaign = {r["campaign"]: r["flags"] for r in out["campaigns"]}
check("the CTR cliff is flagged", "ctr_drop_gt_20" in by_campaign["Retarget - Columbus"])
check("...on a campaign with 90,000 impressions in both windows",
      next(r for r in out["campaigns"]
           if r["campaign"] == "Retarget - Columbus")["impressions"], 90000)
check("the dear campaign is flagged on its multiple",
      "cpa_gt_2x_account" in by_campaign["Generic - HVAC"])
check("...and the healthy one is not",
      by_campaign["Brand - Columbus"], [])
check("spend with no conversions is flagged",
      "spend_no_conversions" in by_campaign["Competitor terms"])
check("...but not on a platform that reports no conversions at all",
      by_campaign["Streaming - Columbus"], [])
check("...which the payload names", out["conversion_platforms"], ["google"])
check("every flag carries a sentence to lift verbatim",
      all(f.get("text") for f in out["flags"]))
check("the thresholds the rules used are echoed",
      out["thresholds"], flag_rules.thresholds())

section("A compare window with no rows: null deltas, and no move flags")
# last_90 reaches back before anything was filed, so its compare window is
# empty -- a client's first period, which must not read as an infinite rise.
early = at("last_90")
check("the window itself has rows", early["totals"]["spend"] > 0)
check("every delta is null",
      set(early["totals"]["delta"].values()), {None})
check("no move flag fired",
      [f for f in early["flags"] if f["code"] == "metric_move_gt_15"], [])
check("...and the note says the comparison could not be made",
      "comparison window" in early["note"])
check("a campaign's deltas are null too",
      set(early["campaigns"][0]["delta"].values()), {None})

section("compare=none asks for no comparison at all")
alone = at(compare="none")
check("there is no compare window", alone["compare"], None)
check("...the delta block is null", alone["totals"]["delta"], None)
check("...and no move flag fired",
      [f for f in alone["flags"] if f["code"] == "metric_move_gt_15"], [])

section("A real move IS flagged, and quotes its own figure")
moved = at("last_7")           # Sep 9-15 vs Sep 2-8: identical, so nothing moves
check("identical windows move nothing",
      [f for f in moved["flags"] if f["code"] == "metric_move_gt_15"], [])
# The cliff campaign's CTR really did fall between the two windows.
check("the CTR drop is measured, not guessed",
      next(r for r in out["campaigns"]
           if r["campaign"] == "Retarget - Columbus")["delta"]["ctr"], -66.67)

section("Filters are matched exactly, and a miss is refused by name")
bad = at(platform="google_ads")
check("an unknown platform is refused", bad["error"], "unknown platform")
check("...it is not available", bad["available"], False)
check("...and the real platform keys are offered",
      "google" in [p["platform"] for p in bad["platforms"]])
check("...with their labels", "Google Ads" in [p["label"] for p in bad["platforms"]])
check("no totals were computed", "totals" not in bad)
badp = at(product="Paid Searching")
check("an unknown product is refused", badp["error"], "unknown product")
check("...and the catalog is offered", "Paid Search" in badp["products"])
only = at(platform="google")
check("a real platform filter narrows the read", only["totals"]["spend"], 2250.0)
check("...and is echoed", only["filters"]["platform"], "google")
byprod = at(product="Streaming TV")
check("a real product filter narrows it too", byprod["totals"]["spend"], 375.0)

section("An unknown period is refused before any query runs")
nope = at("last_month_ish")
check("it is refused", nope["available"], False)
check("...by reason", nope["reason"], "invalid_period")
check("...offering the periods that work", "this_month" in nope["periods"])
check("...and the compares", "previous_period" in nope["compares"])
check("no totals were computed", "totals" not in nope)

section("Pacing is the board's own rows, never a second engine")
with patch("modules.reports.pacing.date", wraps=date) as clock:
    clock.today.return_value = TODAY
    board = pacing_mod.compute(client=KEY)
check("the board has this client's line", len(board), 1)
line = out["pacing"][0]
check("one line comes back", len(out["pacing"]), 1)
for field in ("line_id", "client", "product", "band", "days_remaining",
              "days_elapsed", "trend_days", "alert", "unmapped", "stalled"):
    check(f"...{field} is the board's own", line[field], board[0][field])
for field in ("monthly_budget", "expected_to_date", "actual_to_date",
              "projected_month_end", "avg_daily_7"):
    check(f"...{field} matches to the cent",
          line[field], round(float(board[0][field]), 2))
check("the band label is the board's vocabulary",
      line["band_label"], pacing_mod.BAND_LABELS[board[0]["band"]][1])
# The line covers Paid Search on google -- Brand, Generic and the dead
# campaign -- and NOT the Retargeting campaign, which is a different product.
check("utilization is the line's own spend over its monthly budget",
      line["utilization_pct"], round(15 * 130 / 6500.0 * 100, 1))
check("the line carries its own CPA for the flag rules",
      line["line_cpa"] is not None)

section("Prorated margin, against the sold figure and not the whole month")
check("the monthly sold figure rides beside it", totals["sold_month"], 8100.0)
check("sold is prorated to the 15 days read",
      totals["sold_prorated"], round(8100.0 / 30 * 15, 2))
check("...and margin is measured against the prorated figure",
      totals["margin_pct"],
      round((4050.0 - 2625.0) / 4050.0 * 100, 1))

section("A quarantined day is reported, so the answer can say it is incomplete")
store.upsert_rows([{"platform": "google", "account_id": "111",
                    "campaign_id": "c-brand", "campaign_name": "Brand - Columbus",
                    "date": date(2026, 9, 14), "spend": 99999.0,
                    "impressions": 10, "clicks": 20, "conversions": 0,
                    "source": "native"}], today=TODAY)
held = at()
check("the day is held", held["quarantined_days"], 1)
check("...and the note says the figures are incomplete",
      "quarantine" in held["note"] and "incomplete" in held["note"])

section("Nothing filed, and nothing this period, are different answers")
blank = {"known": True, "client": "Nobody At All", "client_key": "n:nobody-at-all",
         "domain": "", "matched_on": "exact", "confidence": "exact",
         "candidates": [], "why": ""}
with patch.object(v2_tools, "resolve_identity", return_value=blank), \
     patch("hub.periods.date", wraps=date) as clock:
    clock.today.return_value = TODAY
    empty = v2_tools.client_performance("Nobody At All", period="this_month")
check("a client with nothing filed says so", empty["state"], "nothing_filed")
check("...and still answers", (empty["found"], empty["available"]), (True, True))
check("...with zero totals, not null ones", empty["totals"]["spend"], 0.0)
check("...and no invented ratios", empty["totals"]["cpa"], None)
gone = at("last_year")
check("a window with nothing in it is 'nothing this period'",
      gone["state"], "nothing_this_period")

section("An unknown client is not found, and nothing is read")
unknown = {"known": False, "client": "", "client_key": "", "domain": "",
           "matched_on": "", "confidence": "unmatched",
           "candidates": ["Quality Air Columbus"], "why": ""}
with patch.object(v2_tools, "resolve_identity", return_value=unknown):
    miss = v2_tools.client_performance("Qualty Ar")
check("not found", miss["found"], False)
check("...names what was asked for", miss["requested"], "Qualty Ar")
check("...and says the identity is ambiguous", "ambiguous" in miss["message"])

section("Nothing raises: a store that will not answer is a payload")
with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
     patch.object(store, "facts_for", side_effect=RuntimeError("database is gone")):
    broke = v2_tools.client_performance(CLIENT, period="this_month")
check("it still answers", broke["found"], True)
check("...as unavailable", broke["available"], False)
check("...naming the failure kind, not the connector's words",
      broke["error"].startswith("RuntimeError"))
with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
     patch.object(pacing_mod, "compute", side_effect=RuntimeError("board is gone")), \
     patch("hub.periods.date", wraps=date) as clock:
    clock.today.return_value = TODAY
    partial = v2_tools.client_performance(CLIENT, period="this_month")
check("a pacing board that blinked does not lose the spend table",
      partial["totals"]["spend"], 2625.0)
check("...the pacing block is absent, not empty", partial["pacing"], None)
check("...and the note says so", "pacing board could not be read" in partial["note"])

section("The tool is registered in both catalogs it has to be in")
from hub import ask_smarthub                                        # noqa: E402
check("Ask SmartHub carries it", "get_client_performance" in ask_smarthub.TOOLS)
tool = ask_smarthub.TOOLS["get_client_performance"]
check("...at staff level", tool.roles, ask_smarthub.STAFF)
check("...pointing at this function", tool.fn, v2_tools.client_performance)
check("...with every argument named", tool.arguments,
      ("client_name", "period", "compare", "platform", "product",
       "start_date", "end_date", "limit"))
check("...and a member can reach it",
      "get_client_performance" in ask_smarthub.allowed_tools("member"))


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kw):
        def wrap(fn):
            self.tools[fn.__name__] = kw
            return fn
        return wrap


fake = _FakeMCP()
v2_tools.register(fake)
check("the MCP server lists it", "get_client_performance" in fake.tools)
check("...with a title", fake.tools["get_client_performance"]["title"],
      "Get client ad performance")
check("...and the read-only annotations",
      fake.tools["get_client_performance"]["annotations"],
      v2_tools.READ_ONLY_TOOL_ANNOTATIONS)

section("Every read is audited under module mcp")
from hub import audit                                                # noqa: E402
mine = [r for r in audit.read(limit=500, module="mcp")
        if r.get("tool") == "get_client_performance"]
check("the reads were logged", len(mine) > 0)
check("...under module mcp", {r["module"] for r in mine}, {"mcp"})
check("...carrying the client", CLIENT in {r.get("client") for r in mine})
check("...and the period that was read",
      "this_month" in {r.get("period") for r in mine})
check("a refusal is audited with its status",
      "unknown_platform" in {r.get("status") for r in mine})

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
