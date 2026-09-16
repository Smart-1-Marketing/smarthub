"""The GA4 breakdown and the optimization-sweep read Ask SmartHub answers from.

    python3 test_v2_insights.py

No pytest. GA4 is reached through a stubbed transport -- the point here is the
request this tool builds and the rows it publishes, not Google's wire format,
which hub/analytics_ask.py already owns and test_analytics_ranges.py already
covers. The sweep half runs against a throwaway ads_builder database.

What it holds:

  * the breakdown chooses the dimensions, and campaign asks for TWO of them,
    never three -- analytics_ask.validate caps a request at two and drops the
    rest, so a third would have keyed the table on something other than what
    the caller was told;
  * a named period resolves to ISO dates, GA4's own relative tokens still
    pass through untouched, and an unknown period or breakdown is refused by
    name with no call made;
  * source and medium come back apart, the conversion rate is null rather
    than zero with no sessions, and the tagging flags are the same
    modules/reports/flags.py rules the ad-performance tool uses;
  * the sweep read: both spellings of a client, the account state taken from
    hub/ads_status.py rather than re-derived, a failed scan that is not
    reported as clean, and the blob opened once for a named client;
  * Microsoft Ads answers "not built" rather than an empty finding list,
    because an empty list from a sweep that never ran reads as a clean
    account;
  * both tools registered in both catalogs, read-only, and audited.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1v2insights_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "v2-insights-test"

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


from hub import ads_status                                          # noqa: E402
from mcp_gateway import v2_tools                                    # noqa: E402
from modules.ads_builder import store as ads_store                  # noqa: E402

CLIENT = "Quality Air Columbus"
IDENTITY = {"known": True, "client": CLIENT, "client_key": "n:quality-air-columbus",
            "domain": "qualityaircolumbus.com", "matched_on": "exact",
            "confidence": "exact", "candidates": [], "why": ""}
TODAY = date(2026, 9, 16)

# ---------------------------------------------------------------------------
# GA4, over a stubbed transport. The report is shaped exactly as GA4 shapes
# one: a comparison arrives as EXTRA ROWS carrying the range as the last
# dimension, not as extra columns.
# ---------------------------------------------------------------------------
PROPERTY = {"resource_id": "440011", "name": "Quality Air - GA4",
            "google_login": "ops@smart1.agency"}

_sent = {}


def ga4_report(dim_rows):
    """Rows for whatever metrics the tool asked for, in the order it asked."""
    metrics = [m["name"] for m in _sent["request"]["metrics"]]
    rows = []
    for dims, values in dim_rows:
        rows.append({
            "dimensionValues": [{"value": d} for d in dims],
            "metricValues": [{"value": str(values.get(m, 0))} for m in metrics],
        })
    return {"rows": rows}


_rows_to_send = []


def _google_post(token, url, request):
    _sent["request"], _sent["url"] = request, url
    return ga4_report(_rows_to_send)


# Installed once and left in place. `patch.dict(sys.modules, ...)` restores
# the exact snapshot it took, which DELETES anything imported inside the
# block -- and SQLAlchemy, imported lazily by the tool, does not survive
# being unregistered and re-imported.
_finder = type("F", (), {
    "connected_accounts": staticmethod(
        lambda: [{"email": "ops@smart1.agency", "refresh_token": "r"}]),
    "refresh_access_token": staticmethod(lambda login, token: "access"),
    "google_post": staticmethod(_google_post)})
sys.modules["modules.google_finder"] = type("M", (), {"app": _finder})
sys.modules["modules.google_finder.app"] = _finder


def run_ga4(report_rows, **kw):
    global _rows_to_send
    kw.setdefault("client_name", CLIENT)
    _rows_to_send = report_rows
    with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
         patch.object(v2_tools, "_ga4_selection",
                      return_value=(PROPERTY, {"ga4": [PROPERTY], "built_at": "2026-09-16"})), \
         patch("hub.periods.date", wraps=date) as clock:
        clock.today.return_value = TODAY
        return v2_tools.client_ga4_summary(**kw)


section("The breakdown chooses the dimensions, and never more than two")
out = run_ga4([(["Paid Search"], {"sessions": 1000, "keyEvents": 30})])
check("channel is the default",
      _sent["request"]["dimensions"], [{"name": "sessionDefaultChannelGroup"}])
check("...and is echoed back", out["breakdown"], "channel")
run_ga4([(["google / cpc"], {"sessions": 500})], breakdown="source_medium")
check("source_medium asks for the combined dimension",
      _sent["request"]["dimensions"], [{"name": "sessionSourceMedium"}])
run_ga4([(["Spring Sale", "google / cpc"], {"sessions": 500})], breakdown="campaign")
check("campaign asks for exactly two dimensions",
      [d["name"] for d in _sent["request"]["dimensions"]],
      ["sessionCampaignName", "sessionSourceMedium"])
check("...which is what validate() will actually send",
      len(_sent["request"]["dimensions"]) <= 2)
check("every breakdown is declared", sorted(v2_tools.GA4_BREAKDOWNS),
      ["campaign", "channel", "source_medium"])

section("A named period resolves to dates the model never wrote")
run_ga4([(["Paid Search"], {"sessions": 10})], period="this_month")
check("the current range is this month to yesterday",
      _sent["request"]["dateRanges"][0],
      {"startDate": "2026-09-01", "endDate": "2026-09-15", "name": "Current"})
check("...and the comparison is the 15 days before it",
      _sent["request"]["dateRanges"][1],
      {"startDate": "2026-08-17", "endDate": "2026-08-31", "name": "Comparison"})
out = run_ga4([(["Paid Search"], {"sessions": 10})], period="last_month")
check("the window is echoed with its label",
      out["window"]["label"], "Aug 1 - Aug 31, 2026 (last month)")
check("...and the compare names its mode", out["compare"]["mode"], "previous_period")
run_ga4([(["Paid Search"], {"sessions": 10})], period="last_7", compare="none")
check("compare=none sends one range", len(_sent["request"]["dateRanges"]), 1)
run_ga4([(["Paid Search"], {"sessions": 10})], period="this_month",
        compare="same_period_last_year")
check("year-over-year shifts the window back",
      _sent["request"]["dateRanges"][1]["startDate"], "2025-09-01")

section("Explicit dates are a custom period, and GA4's own tokens still work")
run_ga4([(["Paid Search"], {"sessions": 10})],
        start_date="2026-07-01", end_date="2026-07-31")
check("ISO dates are honored",
      _sent["request"]["dateRanges"][0]["startDate"], "2026-07-01")
check("...and get a previous period of the same length",
      _sent["request"]["dateRanges"][1],
      {"startDate": "2026-05-31", "endDate": "2026-06-30", "name": "Comparison"})
run_ga4([(["Paid Search"], {"sessions": 10})],
        start_date="28daysAgo", end_date="yesterday")
check("a relative token an older MCP client learned still passes through",
      _sent["request"]["dateRanges"][0]["startDate"], "28daysAgo")
out = run_ga4([(["Paid Search"], {"sessions": 10})],
              start_date="2026-07-01", end_date="2026-07-31",
              compare_start="2026-06-01", compare_end="2026-06-30")
check("explicit comparison dates win over the named mode",
      _sent["request"]["dateRanges"][1]["startDate"], "2026-06-01")

section("A bad period or breakdown is refused by name, with no call made")
_sent.clear()
bad = run_ga4([], breakdown="by_vibes")
check("an unknown breakdown is refused", bad["error"], "unknown breakdown")
check("...offering the ones that work", "source_medium" in bad["breakdowns"])
check("...and nothing was sent to Google", "request" not in _sent)
bad = run_ga4([], period="last_fortnight")
check("an unknown period is refused", bad["reason"], "invalid_date_range")
check("...offering the periods that work", "this_month" in bad["periods"])
check("...and nothing was sent to Google", "request" not in _sent)
bad = run_ga4([], compare_start="2026-06-01")
check("half a comparison is refused", bad["reason"], "invalid_date_range")
check("...saying both are needed", "Both comparison dates" in bad["message"])

section("The rows a table draws")
out = run_ga4([
    (["google / cpc"], {"sessions": 1000, "totalUsers": 800, "newUsers": 600,
                        "engagedSessions": 400, "engagementRate": 40.0,
                        "averageSessionDuration": 61.5, "keyEvents": 10}),
    (["(direct) / (none)"], {"sessions": 200, "keyEvents": 20}),
    (["newsletter / email"], {"sessions": 0, "keyEvents": 0}),
], breakdown="source_medium", compare="none")
rows = {r["label"]: r for r in out["rows"]}
check("source and medium come back apart",
      (rows["google / cpc"]["source"], rows["google / cpc"]["medium"]),
      ("google", "cpc"))
check("every metric is carried", rows["google / cpc"]["engagedSessions"], 400.0)
check("the conversion rate is computed here", rows["google / cpc"]["conv_rate"], 1.0)
check("...and is null, not zero, with no sessions",
      rows["newsletter / email"]["conv_rate"], None)
check("conversions read from keyEvents", rows["(direct) / (none)"]["conversions"], 20.0)
check("totals carry a conversion rate too", out["totals"]["conv_rate"],
      round(30 / 1200 * 100, 2))
check("with no comparison, the delta block is null",
      rows["google / cpc"]["delta"], None)

section("The GA4 flags are the reports module's own rules")
codes = sorted({f["code"] for f in out["flags"]})
check("direct/(none) at 17% is not a tagging problem",
      "direct_none_gt_40" not in codes)
heavy = run_ga4([
    (["(direct) / (none)"], {"sessions": 900, "keyEvents": 9}),
    (["google / cpc"], {"sessions": 100, "keyEvents": 5}),
], breakdown="source_medium", compare="none")
check("...but at 90% it is",
      "direct_none_gt_40" in {f["code"] for f in heavy["flags"]})
split = run_ga4([
    (["fb / paid"], {"sessions": 90, "keyEvents": 1}),
    (["facebook / paid"], {"sessions": 80, "keyEvents": 1}),
], breakdown="source_medium", compare="none")
check("one source tagged two ways is flagged",
      "utm_case_variants" in {f["code"] for f in split["flags"]})
check("the thresholds are echoed so an answer can quote them",
      split["thresholds"]["direct_none_share_pct"], 40.0)
check("every flag carries a sentence", all(f.get("text") for f in split["flags"]))

section("An unmapped property is still a refusal, not an empty report")
with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
     patch.object(v2_tools, "_ga4_selection", return_value=(None, {"ga4": []})):
    none = v2_tools.client_ga4_summary(CLIENT, period="this_month")
check("it is not available", none["available"], False)
check("...and says a property has to be chosen",
      none["reason"], "property_selection_required")

# ---------------------------------------------------------------------------
# The optimization sweep.
# ---------------------------------------------------------------------------
section("The sweep read: one client's accounts, and what was actually recorded")
RESULT = {"item_count": 4, "items": [
    {"id": "st-1", "source": "smart1", "category": "search_terms",
     "severity": "high", "title": "Wasted spend on 'free hvac'",
     "why": "31 clicks, no conversions, $184.20 spent.",
     "next_step": "Add it as a negative keyword.",
     "data": {"name": "Generic - HVAC", "cost": 184.2}},
    {"id": "cpc-1", "source": "smart1", "category": "click_costs",
     "severity": "high", "title": "High click cost in Competitor",
     "why": "Average CPC is $14.10 on 40 clicks.",
     "next_step": "Review match types.", "data": {"name": "Competitor", "cost": 564.0}},
    {"id": "kw-1", "source": "smart1", "category": "keywords",
     "severity": "medium", "title": "Broad match with no negatives",
     "why": "Three campaigns.", "next_step": "Review.", "data": {}},
    {"id": "g-1", "source": "google", "category": "recommendations",
     "severity": "low", "title": "Add sitelinks", "why": "Google suggests it.",
     "next_step": "Review.", "data": {}},
]}


def deployed(rows):
    return patch.object(ads_store, "deployed_accounts", return_value=rows)


def latest(rows):
    return patch.object(ads_store, "latest_optimization_runs", return_value=rows)


def one_run(row):
    return patch.object(ads_store, "latest_optimization_run", return_value=row)


FRESH = datetime.now(timezone.utc).isoformat()
STALE = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
RUN = {"customer_id": "7788990011", "client_name": CLIENT, "scanned_at": FRESH,
       "date_range": "LAST_30_DAYS", "item_count": 4, "high_severity_count": 2,
       "error": "", "measured": True}


def run_findings(accounts, runs, blob=None, **kw):
    kw.setdefault("client_name", CLIENT)
    with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
         deployed(accounts), latest(runs), \
         one_run(blob if blob is not None else {**RUN, "result": RESULT}):
        return v2_tools.client_ads_findings(**kw)


ACCOUNTS = [{"customer_id": "7788990011", "client_name": CLIENT},
            {"customer_id": "1122334455", "client_name": "Somebody Else"}]

out = run_findings(ACCOUNTS, [RUN])
check("only this client's account is read", out["account_count"], 1)
check("...by its customer id", out["accounts"][0]["customer_id"], "7788990011")
check("the state comes from hub/ads_status.py",
      out["accounts"][0]["state"], "attention")
check("...with that module's own label",
      out["accounts"][0]["state_label"], ads_status.STATES["attention"])
check("when it last scanned is carried", out["accounts"][0]["scanned_at"], FRESH)
check("...and over what window", out["accounts"][0]["date_range"], "LAST_30_DAYS")
check("high severity findings only, by default",
      [f["severity"] for f in out["accounts"][0]["findings"]], ["high", "high"])
check("...two of them", out["high_severity_count"], 2)
check("a finding names its campaign",
      out["accounts"][0]["findings"][0]["campaign"], "Generic - HVAC")
check("...its kind, in words",
      out["accounts"][0]["findings"][0]["kind_label"], "Wasted spend on search terms")
check("...the sweep's own detail, unchanged",
      out["accounts"][0]["findings"][0]["detail"],
      "31 clicks, no conversions, $184.20 spent.")
check("...and a link to the page that shows it",
      out["accounts"][0]["findings"][0]["url"],
      "/tools/ads/optimization?customer_id=7788990011")
check("the sweep records no monetary estimate, so none is invented",
      out["accounts"][0]["findings"][0]["estimated_monthly"], None)
check("...the campaign's spend over the scan window rides under its own name",
      out["accounts"][0]["findings"][0]["scan_window_spend"], 184.2)

section("Severity is a filter, not a summary")
every = run_findings(ACCOUNTS, [RUN], severity="all")
check("all four come back", len(every["accounts"][0]["findings"]), 4)
check("...worst first",
      [f["severity"] for f in every["accounts"][0]["findings"]],
      ["high", "high", "medium", "low"])
mid = run_findings(ACCOUNTS, [RUN], severity="medium")
check("one severity narrows it", len(mid["accounts"][0]["findings"]), 1)
bad = run_findings(ACCOUNTS, [RUN], severity="urgent")
check("an unknown severity is refused", bad["error"], "unknown severity")
check("...offering the ones that work", "high" in bad["severities"])
capped = run_findings(ACCOUNTS, [RUN], severity="all", limit=2)
check("a limit caps the list", len(capped["accounts"][0]["findings"]), 2)
check("...and says how many are not shown",
      capped["accounts"][0]["findings_omitted"], 2)

section("A scan that failed is not a clean account")
failed = {**RUN, "error": "AuthError: the account is no longer accessible",
          "high_severity_count": 0}
out = run_findings(ACCOUNTS, [failed])
check("the state is failed", out["accounts"][0]["state"], "failed")
check("...the blob is never opened", out["accounts"][0]["findings"], [])
check("...and the failure is carried",
      "AuthError" in out["accounts"][0]["error"])
out = run_findings(ACCOUNTS, [{**RUN, "scanned_at": STALE}])
check("an old reading says so, rather than reporting its findings as current",
      out["accounts"][0]["state"], "stale")
out = run_findings(ACCOUNTS, [])
check("an account never scanned is 'never'", out["accounts"][0]["state"], "never")
check("...and has no findings", out["accounts"][0]["findings"], [])
out = run_findings([], [])
check("a client with no deployed account says so, not 'clean'",
      "no account to reach" in out["note"])
check("...and lists none", out["accounts"], [])

section("Microsoft Ads answers 'not built', never an empty finding list")
bing = run_findings(ACCOUNTS, [RUN], platform="bing")
check("it is not available", bing["available"], False)
check("...by reason", bing["reason"], "sweep_not_built")
check("...saying exactly that", bing["error"], "Microsoft Ads sweep not built")
check("...and explaining why", "no optimization sweep analyses it" in bing["message"])
check("...with no findings list at all", bing["accounts"], [])
bad = run_findings(ACCOUNTS, [RUN], platform="tiktok")
check("a platform this tool cannot answer for is refused",
      bad["error"], "unknown platform")

section("Nothing raises")
with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY), \
     patch.object(ads_store, "deployed_accounts",
                  side_effect=RuntimeError("database is gone")):
    broke = v2_tools.client_ads_findings(CLIENT)
check("a store that will not answer is a payload", broke["found"], True)
check("...as unavailable", broke["available"], False)
check("...naming the failure kind", broke["error"].startswith("RuntimeError"))
unknown = {"known": False, "client": "", "client_key": "", "domain": "",
           "matched_on": "", "confidence": "unmatched", "candidates": [], "why": ""}
with patch.object(v2_tools, "resolve_identity", return_value=unknown):
    miss = v2_tools.client_ads_findings("Nobody")
check("an unknown client is not found", miss["found"], False)

section("Both tools are registered in both catalogs")
from hub import ask_smarthub                                        # noqa: E402
for name, fn, args in (
        ("get_client_ads_findings", v2_tools.client_ads_findings,
         ("client_name", "platform", "severity", "limit")),
        ("get_client_ga4_summary", v2_tools.client_ga4_summary,
         ("client_name", "property_id", "period", "compare", "breakdown",
          "start_date", "end_date", "compare_start", "compare_end", "limit"))):
    tool = ask_smarthub.TOOLS[name]
    check(f"Ask SmartHub carries {name}", tool.fn, fn)
    check(f"...with every argument named", tool.arguments, args)
    check(f"...at staff level", tool.roles, ask_smarthub.STAFF)


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
check("the MCP server lists all three new read tools",
      all(n in fake.tools for n in ("get_client_performance",
                                    "get_client_ads_findings",
                                    "get_client_ga4_summary")))
check("...every one of them read-only",
      {tuple(sorted(v["annotations"].items())) for v in fake.tools.values()},
      {tuple(sorted(v2_tools.READ_ONLY_TOOL_ANNOTATIONS.items()))})
check("...and the findings tool has its title",
      fake.tools["get_client_ads_findings"]["title"],
      "Get client ad optimization findings")

section("The planner is told to name a period, never to compute one")
check("the prompt names the period argument",
      "use the period argument" in ask_smarthub.plan.__doc__ if
      ask_smarthub.plan.__doc__ else True)
src = (ROOT / "hub" / "ask_smarthub.py").read_text(encoding="utf-8")
check("...and says never to compute dates", "Never compute dates yourself" in src)
check("...and that flags are facts, not the model's to add",
      "do not add flags of your own" in src)

section("Every read is audited under module mcp")
from hub import audit                                               # noqa: E402
rows = [r for r in audit.read(limit=500, module="mcp")]
tools = {r.get("tool") for r in rows}
check("the GA4 reads were logged", "get_client_ga4_summary" in tools)
check("the sweep reads were logged", "get_client_ads_findings" in tools)
check("...carrying the breakdown that was read",
      "source_medium" in {r.get("breakdown") for r in rows})
check("...and the not-built refusal carries its status",
      "not_built" in {r.get("status") for r in rows})

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
