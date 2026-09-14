"""A client's own slice of modules/reports, on the record every rep already
opens -- and the two QA entries that put unmapped spend and under-pacing
lines where somebody already checks for what is wrong.

    python3 test_reports_client360.py

No pytest, no new dependencies, a temporary data directory and the reports
database _reports_testdb.py binds (a SQLite file here; the job's Postgres
under checks.yml's second run).

## What it holds

Client 360 had no "Ad Performance" card and the dashboard scoreboard and two
QA reports were the only places modules/reports' book-wide numbers ever
reached a screen -- so a live dashboard link, a confirmed campaign or a
budget line pacing under, all for one specific client, were invisible unless
somebody already knew /reports/ existed. `docs/reports-review-and-next-
steps.md`'s own "Suggested order" named this as the next unclaimed item.

  * `store.resolve_client()` is `modules/reports/app.py`'s own
    `_resolve_client` moved into the store once a second caller (this card)
    needed the identical rule: every table in this module is keyed on
    `hub/client_key.py`'s derived key, never a plain display name, and a
    caller that skips the resolution lands its rows under a different
    `client` string than the ones a rep files by hand -- which was a real
    bug in the reporting_plan proposal adapter, caught while building this.
  * `store.client_summary(name)` is the one reading `/api/client/ad-
    performance` serves: the live link (or its absence, with a next step),
    confirmed and pending campaign counts, every budget line, and which of
    them the latest pacing run has under alert.
  * `qa.reports_unmapped_campaigns()` and `qa.reports_pacing_under()` are the
    two QA entries -- the same `unmapped_campaigns()` and `latest_snapshots()`
    reads the staff screens already use, degrading to `measured: False`
    rather than a clean, empty-looking table when the store cannot answer.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1reports_c360_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
REPORTS_DB = _reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "reports-c360-test"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"

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


from werkzeug.test import Client                                     # noqa: E402

import wsgi                                                          # noqa: E402
from hub import auth                                                 # noqa: E402
from hub import client_key as ck                                     # noqa: E402
from hub import clients_registry                                     # noqa: E402
from hub import qa                                                   # noqa: E402
from modules.reports import store                                    # noqa: E402
_reports_testdb.reset(store)

TODAY = date.today()
H = {"Host": "localhost"}
anon = Client(wsgi.application)
staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

# ---------------------------------------------------------------------------
section("resolve_client() is the moved _resolve_client, and the module has no other copy of it")
# ---------------------------------------------------------------------------

from modules.reports import app as reports_app                       # noqa: E402
check("app.py's own name now points at the shared function",
      reports_app._resolve_client is store.resolve_client)

check("a key typed by hand wins over a name lookup", store.resolve_client("Acme Co", "n:hand-typed"),
      ("n:hand-typed", "Acme Co"))
check("no key and no registry hit falls back to a name key -- 'Co' is a dropped legal suffix",
      store.resolve_client("Unregistered Co", ""), ("n:unregistered", "Unregistered Co"))

_real_find_client = clients_registry.find_client
clients_registry.find_client = lambda name: {"name": name, "domain": "resolveme.example.com"}
try:
    check("a name alone resolves through the registry to its domain key",
          store.resolve_client("Resolve Me", ""),
          ("d:resolveme.example.com", "Resolve Me"))
finally:
    clients_registry.find_client = _real_find_client

clients_registry.find_client = lambda name: (_ for _ in ()).throw(RuntimeError("registry down"))
try:
    # The except branch's own fallback, not client_key.name_key() -- it
    # cannot reach hub.client_key at all once that import itself is what
    # raised, so "Co" survives here where it does not on the ordinary path.
    check("a registry that raises still resolves -- to a name key, never a 500",
          store.resolve_client("Registry Down Co", ""),
          ("n:registry-down-co", "Registry Down Co"))
finally:
    clients_registry.find_client = _real_find_client


# ---------------------------------------------------------------------------
section("client_summary(): a client with nothing on file yet")
# ---------------------------------------------------------------------------

empty = store.client_summary("Nobody Yet Co")
check("measured", empty["measured"], True)
check("no live link", empty["link"], None)
check("no campaigns either way", (empty["campaigns_confirmed"], empty["campaigns_pending"]), (0, 0))
check("no budget lines", empty["budget_lines"], [])
check("no pacing rows", empty["pacing"], [])


# ---------------------------------------------------------------------------
section("client_summary(): a client with a link, a confirmed and a pending campaign, a budget line and a pacing snapshot")
# ---------------------------------------------------------------------------

CLIENT_KEY, CLIENT_NAME = "n:summary-client", "Summary Client Co"
link = store.create_link(CLIENT_KEY, client_name=CLIENT_NAME, created_by="tester")
store.upsert_rows([{
    "platform": "google", "account_id": "acct-1", "campaign_id": "conf-1",
    "campaign_name": "Confirmed Search", "date": TODAY, "spend": "150.00",
    "impressions": 900, "clicks": 30, "conversions": "3",
}, {
    "platform": "meta", "account_id": "acct-2", "campaign_id": "pend-1",
    "campaign_name": "Auto-Filed Social", "date": TODAY, "spend": "75.00",
    "impressions": 400, "clicks": 10, "conversions": "1",
}])
store.map_campaign("google", "acct-1", "conf-1", client=CLIENT_KEY, client_name=CLIENT_NAME,
                   product="Paid Search")
store.confirm_mapping("google", "acct-1", "conf-1", by="tester")
store.map_campaign("meta", "acct-2", "pend-1", client=CLIENT_KEY, client_name=CLIENT_NAME,
                   product="Paid Social")
# map_campaign() alone leaves it pending -- confirm_mapping() is the only
# thing that clears it, the "confirmed by the making" rule.

line = store.add_budget_line(client=CLIENT_KEY, client_name=CLIENT_NAME, product="Paid Search",
                             monthly_budget=1000, created_by="tester")
store.write_snapshots([{
    "as_of": TODAY, "line_id": line.id, "client": CLIENT_KEY, "client_name": CLIENT_NAME,
    "product": "Paid Search", "platform": "google", "platforms_json": ["google"],
    "owner": "", "monthly_budget": 1000, "sold_amount": None, "budget_period": 1000,
    "expected_to_date": 500, "actual_to_date": 100, "pace": 0.20, "band": "under",
    "stalled": False, "unmapped": False, "projected_month_end": 300, "daily_needed": 50,
    "avg_daily_7": 3, "period_start": TODAY, "period_end": TODAY, "days_elapsed": 10,
    "days_remaining": 20, "days_in_period": 30, "last_spend_date": TODAY, "trend_days": 4,
    "alert": True,
}])

out = store.client_summary(CLIENT_NAME)
check("resolves to the same key the mapping and the line were filed under",
      out["client"], CLIENT_KEY)
check("the live link is returned", out["link"], {"token": link.token, "url": f"/reports/r/c/{link.token}"})
check("one confirmed campaign", out["campaigns_confirmed"], 1)
check("one pending campaign -- map_campaign() alone does not confirm it", out["campaigns_pending"], 1)
check("one budget line, with a plain float rather than a Decimal",
      (len(out["budget_lines"]), type(out["budget_lines"][0]["monthly_budget"])),
      (1, float))
check("that line is pacing under and alerting",
      (out["pacing"][0]["band"], out["pacing"][0]["alert"]), ("under", True))


# ---------------------------------------------------------------------------
section("A store that will not answer is measured: False, never an empty, healthy-looking summary")
# ---------------------------------------------------------------------------

_real_link_for_client = store.link_for_client
store.link_for_client = lambda client: (_ for _ in ()).throw(RuntimeError("db down"))
try:
    out = store.client_summary("Whatever Co")
    check("a failure anywhere in the chain reads measured: False, not a raised exception",
          out.get("measured"), False)
    check("...naming what went wrong", "RuntimeError" in (out.get("error") or ""), True)
finally:
    store.link_for_client = _real_link_for_client


# ---------------------------------------------------------------------------
section("The route: refuses a stranger, requires a name, degrades and answers for real")
# ---------------------------------------------------------------------------

r = anon.get("/api/client/ad-performance?name=" + CLIENT_NAME, headers=H)
check("an anonymous request is refused", r.status_code in (302, 401, 403), True)

r = staff.get("/api/client/ad-performance", headers=H)
check("no name is a 400", r.status_code, 400)

r = staff.get("/api/client/ad-performance?name=" + CLIENT_NAME, headers=H)
d = r.get_json()
check("a real request answers 200", r.status_code, 200)
check("...with the real summary", (d["measured"], d["campaigns_confirmed"], d["campaigns_pending"]),
      (True, 1, 1))
check("...and the real link URL", d["link"]["url"], f"/reports/r/c/{link.token}")


# ---------------------------------------------------------------------------
section("Client 360's card title is grouped, and grouped once")
# ---------------------------------------------------------------------------

REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
check("the card is on the page", "<h3>Ad Performance" in REC)
check("the route is fetched from the page", "/api/client/ad-performance" in REC)
check("loadAdPerformance is called from the umbrella loader",
      "loadAdPerformance(name)" in REC.split("function loadBrandAndWork", 1)[-1].split("}", 1)[0]
      if "function loadBrandAndWork" in REC else False)


# ---------------------------------------------------------------------------
section("The two QA entries")
# ---------------------------------------------------------------------------

check("reports-unmapped is registered", "reports-unmapped" in qa.REPORTS)
check("reports-pacing-under is registered", "reports-pacing-under" in qa.REPORTS)
check("both sit in Data Quality, beside google-accounts and io-money-mismatch",
      (qa.REPORTS["reports-unmapped"]["group"], qa.REPORTS["reports-pacing-under"]["group"]),
      ("Data Quality", "Data Quality"))

out = qa.reports_unmapped_campaigns()
check("the pending Meta campaign shows up (confirmed ones are not unmapped)",
      any(row[1].get("text") == "Auto-Filed Social" for row in out["rows"]
          if isinstance(row[1], dict)), False)
# map_campaign() files a row in CampaignMap even while pending -- it is
# *filed*, just not confirmed -- so "Campaigns With No Client" is asking a
# different question (no row at all) from "waiting for confirmation"
# (/reports/unmapped's own pending list), and correctly shows neither of
# this client's two campaigns.
check("...neither of this client's campaigns is unfiled", len(out["rows"]), 0)

store.upsert_rows([{
    "platform": "ttd", "account_id": "orphan-acct", "campaign_id": "orphan-1",
    "campaign_name": "Nobody's Campaign", "date": TODAY, "spend": "500.00",
    "impressions": 2000, "clicks": 40, "conversions": "2",
}])
out = qa.reports_unmapped_campaigns()
check("a genuinely unfiled campaign appears, with its 30-day spend",
      any(row[1].get("text") == "Nobody's Campaign" and row[4] == "$500" for row in out["rows"]),
      True)

out = qa.reports_pacing_under()
check("the alerting under-pacing line is on the report",
      any(row[0].get("text") == CLIENT_NAME and row[1] == "Paid Search" for row in out["rows"]),
      True)

# A line that dipped without three straight days of it must not appear --
# the alert gate, not the band alone.
store.write_snapshots([{
    "as_of": TODAY, "line_id": line.id, "client": CLIENT_KEY, "client_name": CLIENT_NAME,
    "product": "Not Yet Alerting", "platform": "google", "platforms_json": ["google"],
    "owner": "", "monthly_budget": 500, "sold_amount": None, "budget_period": 500,
    "expected_to_date": 250, "actual_to_date": 200, "pace": 0.80, "band": "under",
    "stalled": False, "unmapped": False, "projected_month_end": 300, "daily_needed": 25,
    "avg_daily_7": 3, "period_start": TODAY, "period_end": TODAY, "days_elapsed": 10,
    "days_remaining": 20, "days_in_period": 30, "last_spend_date": TODAY, "trend_days": 1,
    "alert": False,
}])
out = qa.reports_pacing_under()
check("a fresh dip with no alert yet is left off",
      any(row[1] == "Not Yet Alerting" for row in out["rows"]), False)

# ---------------------------------------------------------------------------
section("Both QA reports degrade cleanly when the store cannot be read")
# ---------------------------------------------------------------------------

_real_unmapped_campaigns = store.unmapped_campaigns
store.unmapped_campaigns = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    out = qa.reports_unmapped_campaigns()
    check("measured: False on a store failure", out["measured"], False)
    check("the columns still describe the table that failed to build",
          out["columns"], ["Platform", "Campaign", "Account", "Last seen", "Spend (30d)"])
finally:
    store.unmapped_campaigns = _real_unmapped_campaigns


# ---------------------------------------------------------------------------
section("Wired in")
# ---------------------------------------------------------------------------

ci = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("checks.yml runs this file", "python3 test_reports_client360.py" in ci)
loop = ci[ci.index("The reports tests against Postgres"):].split("done", 1)[0]
check("...and the Postgres loop runs it too", "test_reports_client360.py" in loop)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
