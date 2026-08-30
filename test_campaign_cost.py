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
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
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
section("the insertion order bills what the proposal costs")
# ---------------------------------------------------------------------------
# Everything above holds the server's own readings together. What sends the
# order is `ioDataPayload()`, in the browser, off `lineCampaign()` and
# `lineForIO()` -- a second reading of the same arithmetic, and the one the
# client is actually billed from. The assertion above it is that `lineForIO`
# *mentions* `sellRateOf`, which is source text: it would still pass with the
# term clamp dropped, or a one-time line multiplied by the flight. That is the
# $2,250-a-month gap this file exists to close, one layer further down and
# un-asserted.
#
# So the browser half is run in node against the server's, the arrangement
# test_proposal_targeting.py uses on the area step and test_target_areas.py on
# the label helpers -- lifted out of the page rather than restated here, or the
# copy in the test becomes a third thing to keep in step.

import subprocess as _sub                                          # noqa: E402

_SRC = "\n".join(m.group(1) for m in re.finditer(
    r"<script>(.*?)</script>", tpl, re.S))


def _lift(token):
    """One top-level function, out of the page, as written."""
    i = _SRC.index(token)
    ends = [j for j in (_SRC.find("\nfunction ", i + 10),
                        _SRC.find("\nconst ", i + 10),
                        _SRC.find("\n/*", i + 10)) if j > 0]
    return _SRC[i:min(ends)] + "\n"


# The shapes that go wrong quietly. A plain plan agrees under almost any bug;
# a part-term line, a one-time line beside a monthly one, a term longer than
# the flight, a fee with no rate at all and a rep's own quoted rate are where
# the two readings part company.
_PLANS = [
    ("a plain six-month plan",
     {"months": 6, "items": [
         {"category": "DISPLAY", "product": "Category", "rate": "CPM",
          "rateValue": 4.25, "dollars": 2000},
         {"category": "OTT", "product": "Connected TV - Targeted", "rate": "CPM",
          "rateValue": 35.0, "dollars": 3000}]}),
    ("a one-time line beside a monthly one",
     {"months": 6, "items": [
         {"category": "WEB DEVELOPMENT", "product": "Smart 1 Site / 2-5 pages",
          "dollars": 349.50, "basis": "one_time"},
         {"category": "WEB DEVELOPMENT", "product": "Monthly Website Hosting",
          "dollars": 75.0}]}),
    ("a line bought for three months of a six-month flight",
     {"months": 6, "items": [
         {"category": "DISPLAY", "product": "Category", "rate": "CPM",
          "rateValue": 4.25, "dollars": 2000, "termMonths": 3}]}),
    ("a term longer than the flight, which the flight caps",
     {"months": 3, "items": [
         {"category": "DISPLAY", "product": "Category", "rate": "CPM",
          "rateValue": 4.25, "dollars": 2000, "termMonths": 12}]}),
    ("a management fee, which has no rate to quote",
     {"months": 6, "items": [
         {"category": "MANAGEMENT", "product": "Management Fee", "dollars": 500},
         {"category": "DISPLAY", "product": "Category", "rate": "CPM",
          "rateValue": 4.25, "dollars": 1500}]}),
    ("a rep's own quoted rate, which wins over the 2x start",
     {"months": 6, "items": [
         {"category": "DISPLAY", "product": "Category", "rate": "CPM",
          "rateValue": 4.25, "dollars": 2000, "sellRate": 6.75}]}),
]

_harness = (
    _lift("function isMarkedUp") + _lift("function startingRate")
    + _lift("function sellRateOf") + _lift("function lineCampaign")
    + _lift("function lineForIO")
    # The multiplier is served by /api/config rather than mirrored, so the
    # harness hands over the server's own value the way the page is handed it.
    + "function rateRules(){return {sellMultiplier:%s};}\n" % B.hub_rate_card.SELL_MULTIPLIER
    + "function money(n){return '$'+(+n||0).toFixed(2);}\n"
    + "const PLANS=" + json.dumps([p for _, p in _PLANS]) + ";\n"
    + """
console.log(JSON.stringify(PLANS.map(st=>{
  const items=(st.items||[]).map(i=>lineForIO(i,st.months||1,true));
  return {monthly:items.reduce((s,i)=>s+(i.budget||0),0),
          total:items.reduce((s,i)=>s+(i.campaignBudget||0),0),
          lines:items.map(i=>({budget:i.budget,campaignBudget:i.campaignBudget,
                               termMonths:i.termMonths,listedRate:i.listedRate}))};
})));
""")
_js = os.path.join(_TMP, "ioseam.js")
open(_js, "w", encoding="utf-8").write(_harness)
try:
    _browser = json.loads(_sub.run(["node", _js], capture_output=True, text=True,
                                   timeout=60, check=True).stdout)
    for (_name, _state), _b in zip(_PLANS, _browser):
        _c = B.campaign_cost(_state)
        check("%s: the IO's monthly is the proposal's recurring" % _name,
              abs(_b["monthly"] - _c["recurring"]) < 0.01,
              (_b["monthly"], _c["recurring"]))
        check("%s: and its campaign total is the proposal's" % _name,
              abs(_b["total"] - _c["campaign"]) < 0.01,
              (_b["total"], _c["campaign"]))
        # Per line, because two lines can be wrong in opposite directions and
        # still add up -- which is a total that agrees about a plan neither
        # document describes.
        for _item, _ln in zip(_state["items"], _b["lines"]):
            _want = B._sell_rate(_item)
            _got = _ln["listedRate"] or ""
            check("  %s is billed at the rate it was quoted at"
                  % _item["product"][:34],
                  ("Managed/flat" in _got) if _want is None
                  else ("%.2f" % _want) in _got,
                  (_got, _want))
except FileNotFoundError:                       # pragma: no cover
    print("  skip node is not installed — the IO payload is unchecked")
except _sub.CalledProcessError as _exc:         # pragma: no cover
    check("the IO payload builds", False, _exc.stderr[:400])


# ---------------------------------------------------------------------------
section("what the plan is warned about, on both halves at once")
# ---------------------------------------------------------------------------
# `guardrailsJs()` was fixed to read the card's own per-product minimum and its
# comment says "paid search is now $400 in one place and every document reads
# it there". That was true of the screen a rep edits on and false of
# `compute_guardrails()`, which held every SEARCH ENGINE MARKETING line to a
# flat $1,500 -- so it warned about valid $500 and $1,000 search buys, quoting
# a figure that is not that product's minimum, and said nothing at all about a
# $500 Connected TV line whose real floor is $1,500 and which the IO refuses.
#
# Wrong in both directions, and the server's is the reading that rides the
# quote payload into the proposals list, the dashboard nudges and
# `ioDataPayload()`'s `guardrailWarnings` -- so the insertion order carried the
# stale answer while the wizard showed the right one.

_GUARD_PLANS = [
    ("a $500 Connected TV line, which the IO refuses under $1,500",
     {"months": 6, "budget": 500, "items": [
         {"category": "OTT",
          "product": "Connected TV - Targeted  - This is played on a TV",
          "dollars": 500}]}),
    ("a $500 paid search line, which the card sells from $400",
     {"months": 6, "budget": 500, "items": [
         {"category": "SEARCH ENGINE MARKETING / PAY PER CLICK",
          "product": "Pay Per Click", "dollars": 500}]}),
    ("a $600 IP Targeted Display line, floor $1,000",
     {"months": 6, "budget": 600, "items": [
         {"category": "IP TARGETS", "product": "IP Targeted Display - New Movers",
          "dollars": 600}]}),
    ("a healthy plan, which is warned about nothing",
     {"months": 6, "budget": 5000, "items": [
         {"category": "DISPLAY", "product": "Category", "dollars": 2000},
         {"category": "OTT",
          "product": "Connected TV - Targeted  - This is played on a TV",
          "dollars": 3000}]}),
    ("a two-month flight",
     {"months": 2, "budget": 2000, "items": [
         {"category": "DISPLAY", "product": "Category", "dollars": 2000}]}),
    ("five thin products",
     {"months": 6, "budget": 2000, "items": [
         {"category": "DISPLAY", "product": "Category", "dollars": 400}
         for _ in range(5)]}),
    ("Smart 1 building the creative with no fee on the plan",
     {"months": 6, "budget": 2000, "creativeSource": "Smart 1", "items": [
         {"category": "DISPLAY", "product": "Category", "dollars": 2000}]}),
    # A one-time build is not a monthly buy. Judged against a monthly floor,
    # an ordinary website-only proposal reported that the IO would refuse it.
    ("a one-time website build, which has no monthly floor to be under",
     {"months": 6, "budget": 350, "items": [
         {"category": "WEB DEVELOPMENT", "product": "Smart 1 Site / 2-5 pages",
          "dollars": 349.5, "basis": "one_time"}]}),
]

_mins = B.hub_rate_card.minimums_for_js()
_gharness = (
    _lift("function money") + _lift("function minimumFor")
    + _lift("function guardrailsJs")
    + "const MIN_BY_PRODUCT=%s,MIN_BY_CATEGORY=%s,MIN_MONTHLY_DEFAULT=%s;\n"
    % (json.dumps(_mins.get("byProduct", {})), json.dumps(_mins.get("byCategory", {})),
       json.dumps(_mins.get("default", B.hub_rate_card.MIN_MONTHLY_DEFAULT)))
    + "const PLANS=" + json.dumps([p for _, p in _GUARD_PLANS]) + ";\n"
    + "console.log(JSON.stringify(PLANS.map(st=>{S=st;S.items=st.items||[];"
      "return guardrailsJs();})));\n")
_gjs = os.path.join(_TMP, "guards.js")
open(_gjs, "w", encoding="utf-8").write("var S;\n" + _gharness)
try:
    _gb = json.loads(_sub.run(["node", _gjs], capture_output=True, text=True,
                              timeout=60, check=True).stdout)
    for (_name, _state), _wiz in zip(_GUARD_PLANS, _gb):
        check("%s: the server and the wizard say the same thing" % _name,
              B.compute_guardrails(_state) == _wiz,
              (B.compute_guardrails(_state), _wiz))
except FileNotFoundError:                       # pragma: no cover
    print("  skip node is not installed — the guardrails are unchecked")
except _sub.CalledProcessError as _exc:         # pragma: no cover
    check("the wizard's guardrails run", False, _exc.stderr[:400])

# The minimum is the card's, per product, rather than a figure written here --
# so a product whose floor moves on the card moves in both documents.
check("a paid search line at $500 is not warned about, because the card sells "
      "it from $400 and the flat $1,500 rule was never that product's minimum",
      B.compute_guardrails(_GUARD_PLANS[1][1]) == [])
check("and a $500 Connected TV line is, because $1,500 is",
      any("1,500" in w for w in B.compute_guardrails(_GUARD_PLANS[0][1])),
      B.compute_guardrails(_GUARD_PLANS[0][1]))
check("neither half writes a minimum of its own",
      "1500" not in re.sub(r"COMP_CONFIRM_UNDER=1500", "",
                           tpl.split("function guardrailsJs")[1][:900]))


# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
