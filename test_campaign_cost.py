"""One number for what the campaign costs.

    python3 test_campaign_cost.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

A proposal used to carry three different monthly figures and hand a fourth to
the insertion order. `summarize_into()` took `monthly_budget` from the
selected package or from `state["budget"]` — the number typed on the Budget
step, which is what the *client asked for* — while the media plan totalled the
lines actually being bought and `ioDataPayload()` billed those same lines. A
plan edited down from $8,000 to $5,750 produced:

  * a cover reading **$8,000 / mo, $48,000 total**,
  * a media mix totalling **$5,750**,
  * an investment summary quoting **$8,000** of media, and
  * an insertion order for **$5,750**.

$2,250 a month between the document a client signs and the order that bills
them, over a six-month flight, with every screen internally consistent and
nothing erroring anywhere — which is why it survived.

`campaign_cost()` is the one reading now, and the working budget follows the
plan. What is asserted here is that every figure agrees, that recurring and
one-time are never added together, that a line bought for part of the flight
is not charged for all of it, and that what the client asked for is kept
rather than overwritten.
"""
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-cost-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "cost-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 62)


from werkzeug.test import Client                                    # noqa: E402
import wsgi                                                         # noqa: E402
from hub import auth                                                # noqa: E402

B = sys.modules.get("salesb_app")
if B is None:                                   # pragma: no cover - mount failed
    from modules.sales_builder import app as B

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")

# The exact shape that produced four different numbers: a stated budget of
# $8,000 and a plan totalling $5,500 a month plus a $1,500 one-time shoot.
STATE = {"client": "Riverstone Dental", "months": 6, "budget": 8000,
         "budgetAsked": 8000, "objectives": ["Lead Generation"],
         "kpis": ["Cost per lead"],
         "items": [
             {"category": "DISPLAY", "product": "Category", "rate": "CPM",
              "rateValue": 4.25, "dollars": 2000},
             {"category": "OTT/CTV", "product": "Demographic", "rate": "CPM",
              "rateValue": 38.0, "dollars": 3000},
             {"category": "PRODUCTION", "product": "Video Production",
              "dollars": 1500, "basis": "one_time"},
             {"category": "MANAGEMENT", "product": "Management Fee",
              "dollars": 500}]}

# ---------------------------------------------------------------------------
section("what the campaign costs")
# ---------------------------------------------------------------------------
cost = B.campaign_cost(STATE)
check("the recurring figure is the lines that recur, and only those",
      cost["recurring"] == 5500.0, cost)
check("a one-time line is its own number, never added to the monthly one — "
      "a $1,500 shoot is not $1,500 a month",
      cost["one_time"] == 1500.0)
check("the campaign total is the terms plus the one-time",
      cost["campaign"] == 5500.0 * 6 + 1500.0, cost["campaign"])
check("and what the client asked for is kept whatever the plan became",
      cost["stated"] == 8000.0 and cost["differs_from_stated"] is True)

part = dict(STATE)
part["items"] = [dict(STATE["items"][0], termMonths=3)]
check("a line bought for three months of a six-month flight is charged for "
      "three", B.campaign_cost(part)["campaign"] == 6000.0,
      B.campaign_cost(part))

empty = B.campaign_cost({"months": 6, "budget": 8000})
check("a quote with no plan yet still answers, with the ask",
      empty["has_plan"] is False and empty["stated"] == 8000.0
      and empty["recurring"] == 0.0)
check("and does not claim the plan differs from anything",
      empty["differs_from_stated"] is False)

# ---------------------------------------------------------------------------
section("every screen prints the same number")
# ---------------------------------------------------------------------------
quote = staff.post("/sales/builder/api/quotes",
                   json={"data": STATE}).get_json()["quote"]
qid = quote["id"]
plan = B.media_plan_rows(STATE)

check("the list and the cover read the plan, not the stated budget",
      quote["monthly_budget"] == 5500, quote["monthly_budget"])
check("and the campaign total with it",
      quote["total_budget"] == 34500, quote["total_budget"])
check("the media plan's own total is the same figure — this table said "
      "$5,750 while the summary said $5,500 recurring, because a one-time "
      "line was being spread across the flight under a Monthly heading",
      plan["monthly_total"] == 5500.0 and plan["campaign_total"] == 34500.0,
      (plan["monthly_total"], plan["campaign_total"]))
one_time_row = [r for r in plan["rows"] if r["basis"] == "one_time"][0]
check("the one-time row says so instead of showing a monthly twelfth of "
      "itself", one_time_row["monthly"] is None
      and one_time_row["monthly_label"] == "One-time", one_time_row)
check("while its campaign column carries the whole cost",
      one_time_row["campaign"] == 1500.0)

db = B.SessionLocal()
try:
    inv = B.investment_lines(STATE, db.get(B.Quote, qid))
finally:
    db.close()
media = [l for l in inv["lines"] if l["kind"] == "media"][0]
check("the investment summary's recurring line is the same number again",
      media["amount"] == 5500.0, media)
check("and it is no longer called Media spend, because a management fee is "
      "not media", "media & services" in media["label"].lower(), media["label"])
setup = [l for l in inv["lines"] if l["recurs"] == "One-time"]
check("a one-time line on the plan is its own row, named",
      any("Video Production" in l["label"] for l in setup), setup)
check("the campaign total is not recurring × months — a line bought for part "
      "of the flight is not charged for all of it, and the plan's one-time "
      "rows are already inside it",
      inv["campaign_total"] == 34500.0 + 599.0 * 6, inv["campaign_total"])
check("first month is everything that lands in month one",
      inv["first_month"] == 5500.0 + 599.0 + 1500.0, inv["first_month"])

# --- the document itself ---------------------------------------------------
pdf = staff.get(f"/sales/builder/api/quotes/{qid}/pdf")
check("the PDF builds", pdf.status_code == 200 and pdf.data[:4] == b"%PDF")
try:
    from pypdf import PdfReader
    import io as _io
    text = "\n".join(p.extract_text() or ""
                     for p in PdfReader(_io.BytesIO(pdf.data)).pages)
    flat = re.sub(r"\s+", " ", text)
    check("the cover no longer quotes the budget the client asked for",
          "$8,000" not in flat, [w for w in flat.split() if "8,000" in w])
    check("it says $5,500 a month, with the scope named",
          "Monthly campaign investment $5,500" in flat, flat[:0] or "")
    check("the media plan prints a campaign total row",
          "Campaign total" in flat)
    check("and the investment summary says what its own total includes",
          "including licensing" in flat)
except ImportError:                             # pragma: no cover
    check("pypdf available to read the PDF back", False, "pypdf missing")

docx = staff.get(f"/sales/builder/api/quotes/{qid}/docx")
check("the Word copy builds too", docx.status_code == 200 and len(docx.data) > 5000)

# ---------------------------------------------------------------------------
section("the browser half")
# ---------------------------------------------------------------------------
tpl = open(os.path.join(ROOT, "modules", "sales_builder", "templates",
                        "index.html"), encoding="utf-8").read()
check("the budget follows the plan", "function syncBudgetToPlan" in tpl)
check("from both editors, or the number depends on which screen the rep "
      "happened to edit the plan from",
      tpl.count("syncBudgetToPlan()") >= 5, tpl.count("syncBudgetToPlan()"))
check("changing whether a line recurs moves it too",
      "syncBudgetToPlan()" in tpl.split("function setBasis")[1][:400])
check("the browser's own recurring total excludes one-time lines",
      "function planRecurring" in tpl and '!=="one_time"' in
      tpl.split("function planRecurring")[1][:220])
check("the recommended package IS the plan rather than the plan rounded to "
      "the nearest $250", "t.mult===1?(planRecurring()" in tpl)
check("what the client asked for is recorded and never overwritten",
      "S.budgetAsked=+v||0" in tpl and "budgetAsked" in
      tpl.split("function syncBudgetToPlan")[1][:600])
check("and the Budget step says when the two have parted company, or a rep "
      "returning to it reads a number they never typed",
      "function budgetDriftNote" in tpl and "asked for" in
      tpl.split("function budgetDriftNote")[1][:900])
check("the preview draws the server's investment summary rather than a "
      "fourth copy of the arithmetic",
      "S._invest" in tpl and "const media=S.items.reduce" not in tpl)
check("and says so while it is stale rather than printing a stale total",
      "Recalculating the investment summary" in tpl)

check("the insertion order is handed the quoted rate, not the card's own — "
      "an IO reading CPM 4.25 for a line quoted at $8.50 is the buy-side "
      "number on the document that bills the campaign",
      "sellRateOf(i)" in tpl.split("function lineForIO")[1][:700])
check("and a management fee as an amount, which is what its field asks for",
      "function managementFeeForIO" in tpl
      and "managementFee:managementFeeForIO()" in tpl)
check("with NONE where there are no fee lines — a real answer, and the word "
      "the IO expects",
      '"NONE"' in tpl.split("function managementFeeForIO")[1][:700])
# The comment above managementFeeForIO() quotes the old value, and a check
# that reads prose as a call site reports the explanation of a fix as the
# defect — the rule tools/spellcheck.py works to. Read the option list.
fee_q = tpl.split('convFld("Management fee confirmed?"')[1][:220]
check("nothing offers the rate card as an answer any more",
      "rate card" not in fee_q, fee_q)

app_src = open(os.path.join(ROOT, "modules", "sales_builder", "app.py"),
               encoding="utf-8").read()
check("and one server-side reading feeds the list, the summary and the plan",
      app_src.count("campaign_cost(state)") >= 4,
      app_src.count("campaign_cost(state)"))

# ---------------------------------------------------------------------------
section("two figures the same mistake was hiding")
# ---------------------------------------------------------------------------
from hub import creative_needs as hub_creative                      # noqa: E402

spend = hub_creative.medium_spend(STATE, "video")
check("a medium's campaign spend counts a one-time production once — it "
      "multiplied every line by the flight, so a $1,500 shoot read as $9,000",
      spend == 3000.0 * 6 + 1500.0, spend)
short = hub_creative.medium_spend(
    {"months": 6, "items": [dict(STATE["items"][1], termMonths=3)]}, "video")
check("and a line bought for three months of six is not charged for six",
      short == 9000.0, short)
check("which matters because the comp confirmation is only asked BELOW the "
      "threshold — an inflated spend switches the question off on exactly "
      "the small campaign it exists for",
      hub_creative.needs_confirmation({"months": 6, "items": [
          {"category": "OTT", "product": "Demographic", "rate": "CPM",
           "rateValue": 38.0, "dollars": 200}]}, "video") is True)
check("the browser's mirror was corrected with it",
      '(i.basis||"monthly")==="one_time"' in
      tpl.split("function mediumSpend")[1][:700])

chan = B.channel_lines(STATE)
names = [c.get("product") for c in chan]
check("the channel table lists channels — a production line and a management "
      "fee are not channels, and 'Video Production — top of funnel, builds "
      "awareness and trust on the screens the household already watches' was "
      "printed on a document a client reads",
      names == ["Category", "Demographic"], names)
check("and the preview filters the same way rather than keeping its own list",
      "function channelLines" in tpl and "channelLines()" in tpl)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
