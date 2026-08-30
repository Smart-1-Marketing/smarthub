"""What happens to a quote after it is sent: the clock, and what Suite decides.

    python3 test_quote_validity.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

`VALID_STATUSES` has carried **Expired** since the day it was written, with a
badge color and a ⏰ in the status picker, and nothing anywhere set it. That
was cosmetic until the client got a link: `/sales/builder/p/<token>` lets a
client accept a proposal themselves, and the accept route checked that the
link was live, that the reader was not staff, and that this revision had not
already been accepted — and nothing at all about *when the quote was written*.
So a March link could be accepted in September at March's rates, filed as a
clean acceptance with the client's name on it.

The other half is the same disagreement in the other direction. The push into
Suite has always recorded `suite_opportunity_id` on the quote and nothing ever
read it back, so a deal marked Won in Suite updated the client's Proposals
card and left the Proposal Builder's dashboard — the screen a rep actually
looks at — still saying Sent.

What is asserted here is every way each of those goes quietly wrong: a status
derived rather than stored, a date that is not measured reading as expired, a
client turned away with a 404 instead of a way to ask, an opportunity id
matched loosely, and a Suite stage walking an approved quote backwards.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-validity-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "validity-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)
os.environ.setdefault("PUBLIC_BASE_URL", "https://smart1.agency")

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


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def ago(days):
    return NOW - timedelta(days=days)


# ---------------------------------------------------------------------------
section("the window itself")
# ---------------------------------------------------------------------------
from hub import quote_validity as qv                                # noqa: E402

live = qv.window("Sent", sent_at=ago(3), now=NOW)
check("a quote sent three days ago still stands",
      live["applies"] and live["measured"] and not live["expired"])
check("and says how many days are left, rounded so the last day reads as 1",
      live["days_left"] == 27, live)
old = qv.window("Sent", sent_at=ago(40), now=NOW)
check("one sent forty days ago has expired", old["expired"])
check("with the date it expired on", old["expires_on"] == "2026-08-20", old)

edge = qv.window("Sent", sent_at=ago(30), now=NOW)
check("the day the window closes, it is closed", edge["expired"])
check("and the day before it is not, with a day left",
      qv.window("Sent", sent_at=ago(29), now=NOW)["days_left"] == 1)

# --- only a document the client was given can expire -----------------------
for status in ("Draft", "Approved", "Converted", "Lost", "Expired"):
    win = qv.window(status, sent_at=ago(400), now=NOW)
    check(f"a {status} quote has no window — {status.lower()} is finished in "
          f"its own way, and expiring it takes something back",
          win["applies"] is False and win["expired"] is False)
check("and the reason says so rather than leaving a blank",
      bool(qv.window("Draft", sent_at=ago(400), now=NOW)["reason"]))

# --- a missing date is not an expired one ----------------------------------
none = qv.window("Sent", now=NOW)
check("a sent quote with no date at all is NOT measured, never expired — an "
      "absent timestamp reading as 'expired today' refuses an acceptance the "
      "client is entitled to give",
      none["applies"] and none["measured"] is False and none["expired"] is False)
check("and it says which kind of empty it is", "not measured" in none["reason"])

fallback = qv.window("Sent", created_at=ago(10), now=NOW)
check("with no send recorded it counts from when it was written",
      fallback["measured"] and fallback["counted_from"] == "written")
check("and which date answered is carried, because 'thirty days from when I "
      "sent it' and 'from when I wrote it' are different promises",
      qv.window("Sent", sent_at=ago(2), created_at=ago(60),
                now=NOW)["counted_from"] == "sent")
check("the send wins over the writing, so a quote drafted in March and sent "
      "in August stands from August",
      qv.window("Sent", sent_at=ago(2), created_at=ago(180),
                now=NOW)["expired"] is False)

# --- the window a rep chose ------------------------------------------------
check("the house window is the default", qv.days_for({})["source"] == "house")
custom = qv.days_for({"validityDays": 14})
check("a per-quote override is honored and marked as one",
      custom["days"] == 14 and custom["source"] == "quote" and custom["custom"])
check("a nonsense override falls back rather than throwing",
      qv.days_for({"validityDays": "soon"})["days"] == qv.house_days())
check("and one out of range is clamped inside the bounds",
      qv.days_for({"validityDays": 9999})["days"] == qv.MAX_DAYS
      and qv.days_for({"validityDays": -5})["days"] == qv.MIN_DAYS)

# --- the words -------------------------------------------------------------
check("the client's line names the date rather than the length — 'valid for "
      "30 days' on a document with no send date on it is arithmetic the "
      "reader cannot do",
      "August 20, 2026" in qv.client_note(old))
check("a quote with no window says nothing on the client's copy",
      qv.client_note(qv.window("Draft", created_at=ago(5), now=NOW)) == "")
check("and one whose date is not measured says nothing either — a sentence "
      "with a gap in it is worse than no sentence",
      qv.client_note(none) == "")
check("the staff line carries the days left and where the clock started",
      "27 day" in qv.staff_note(live) and "sent" in qv.staff_note(live))
ref = qv.refusal(old, "Dana Reyes", "dana@smart1marketing.com")
check("a refusal names who to ask — somebody trying to say yes is the last "
      "person to turn away with nothing",
      "Dana Reyes" in ref["error"] and "dana@smart1marketing.com" in ref["error"])
check("and still says something useful with no contact on file",
      "Smart 1" in qv.refusal(old)["error"])

check("the status a screen shows is derived",
      qv.effective_status("Sent", sent_at=ago(40), now=NOW) == "Expired"
      and qv.effective_status("Sent", sent_at=ago(2), now=NOW) == "Sent")
check("and Converted is never derived into anything",
      qv.effective_status("Converted", sent_at=ago(400), now=NOW) == "Converted")

src = open(os.path.join(ROOT, "hub", "quote_validity.py"), encoding="utf-8").read()
check("nothing in here writes a status anywhere — it is derived on read, or "
      "the worker that ran a sweep and the one that did not disagree",
      "commit" not in src and "session" not in src.lower())

# ---------------------------------------------------------------------------
section("through the running app")
# ---------------------------------------------------------------------------
from werkzeug.test import Client                                    # noqa: E402
import wsgi                                                         # noqa: E402
from hub import auth                                                # noqa: E402

builder = sys.modules.get("salesb_app")
if builder is None:                             # pragma: no cover - mount failed
    from modules.sales_builder import app as builder

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")
visitor = Client(wsgi.application)

BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

state = {"client": "Riverstone Dental", "months": 6, "budget": 8000,
         "salesContact": "Dana Reyes", "salesEmail": "dana@smart1marketing.com",
         "kpis": ["Cost per lead"], "objectives": ["Lead Generation"],
         "items": [{"category": "DISPLAY", "product": "Category", "rate": "CPM",
                    "rateValue": 4.25, "dollars": 8000}]}
quote = staff.post("/sales/builder/api/quotes",
                   json={"data": state}).get_json()["quote"]
qid = quote["id"]

check("a draft quote reports no window at all",
      quote["validity"]["applies"] is False, quote["validity"])
check("and its shown status is simply its status",
      quote["shown_status"] == "Draft")

share = staff.post(f"/sales/builder/api/quotes/{qid}/share").get_json()["share"]
token = share["token"]
check("sending it starts the clock", share["validity"]["applies"]
      and share["validity"]["measured"], share["validity"])
check("and the panel is handed the sentence, not just the dates — no screen "
      "can render the date without the words that explain it",
      bool(share["validity"]["note"]))

fresh = staff.get(f"/sales/builder/api/quotes/{qid}").get_json()["quote"]
check("the client's own copy carries the line",
      "held until" in fresh["validity"]["client_note"], fresh["validity"])

page = visitor.get(f"/sales/builder/p/{token}",
                   headers={"User-Agent": BROWSER}).get_data(as_text=True)
check("and the client reads it on their page — an expiry they cannot see is "
      "one we cannot hold them to", "held until" in page)
check("with the accept button still there", 'id="acceptCard"' in page)


def _age_the_share(days):
    """Move this quote's send date back, the way the calendar would."""
    db = builder.SessionLocal()
    try:
        row = (db.query(builder.QuoteShare)
               .filter(builder.QuoteShare.quote_id == qid)
               .order_by(builder.QuoteShare.id.desc()).first())
        row.sent_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()
    finally:
        db.close()


_age_the_share(45)
aged = staff.get(f"/sales/builder/api/quotes/{qid}").get_json()["quote"]
check("past the date the list shows it as Expired",
      aged["shown_status"] == "Expired", aged["shown_status"])
check("and the stored status is untouched, so nothing can round-trip a "
      "derived value into the column",
      aged["status"] == "Sent")

db = builder.SessionLocal()
try:
    check("which the database agrees with",
          db.get(builder.Quote, qid).status == "Sent")
finally:
    db.close()

gone = visitor.get(f"/sales/builder/p/{token}",
                   headers={"User-Agent": BROWSER})
body = gone.get_data(as_text=True)
check("the client is not turned away — an expired quote is a real quote "
      "belonging to a real client who is trying to say yes",
      gone.status_code == 200)
check("the accept form is gone", 'id="acceptCard"' not in body)
check("and they are told who to ask instead",
      "dana@smart1marketing.com" in body and "updated" in body.lower())

refused = visitor.post(f"/sales/builder/api/p/{token}/accept",
                       json={"name": "Jane Whitfield", "email": "jane@rd.com"},
                       headers={"User-Agent": BROWSER})
check("and the write refuses too — a rule the form keeps while the write "
      "breaks it is not a rule", refused.status_code == 409)
check("naming the date and who to ask", "dana@smart1marketing.com"
      in (refused.get_json() or {}).get("error", ""))

db = builder.SessionLocal()
try:
    check("nothing was filed", db.query(builder.QuoteAcceptance)
          .filter(builder.QuoteAcceptance.quote_id == qid).count() == 0)
finally:
    db.close()

# --- the rep's own window --------------------------------------------------
set14 = staff.post(f"/sales/builder/api/quotes/{qid}/validity",
                   json={"days": 14}).get_json()
check("a rep can hold the pricing for a different number of days",
      set14["ok"] and set14["share"]["validity"]["days"] == 14, set14)
check("and it is marked as this quote's own rather than the house window",
      set14["share"]["validity"]["custom"] is True)
check("stored in the quote's data blob, never a new column create_all() "
      "would not add to the live Postgres",
      "validity_days" not in [c.name for c in builder.Quote.__table__.columns]
      and staff.get(f"/sales/builder/api/quotes/{qid}").get_json()["quote"]
      ["data"].get("validityDays") == 14)
bad = staff.post(f"/sales/builder/api/quotes/{qid}/validity", json={"days": 9999})
check("an out-of-range window is refused by name rather than silently "
      "clamped — a rep who typed 3650 and got 365 has been told something "
      "different from what they asked for, on a date a client relies on",
      bad.status_code == 400 and "days" in bad.get_json()["error"])
cleared = staff.post(f"/sales/builder/api/quotes/{qid}/validity",
                     json={"days": 0}).get_json()
check("clearing it puts the quote back on the house window rather than "
      "removing the window",
      cleared["share"]["validity"]["custom"] is False
      and cleared["share"]["validity"]["applies"] is True)

# --- re-sending re-quotes --------------------------------------------------
again = staff.post(f"/sales/builder/api/quotes/{qid}/share").get_json()["share"]
check("re-sending restarts the clock, because a re-send is the current "
      "document at current rates",
      again["validity"]["expired"] is False, again["validity"])
check("the token is the same one — a link already in an inbox must not stop "
      "working", again["token"] == token)
ok = visitor.post(f"/sales/builder/api/p/{token}/accept",
                  json={"name": "Jane Whitfield", "email": "jane@rd.com"},
                  headers={"User-Agent": BROWSER})
check("and the client can accept again", ok.status_code == 200
      and ok.get_json().get("ok"), ok.get_json())

check("a quote the client accepted stops having a window at all — expiring "
      "an acceptance would take back an agreement",
      staff.get(f"/sales/builder/api/quotes/{qid}").get_json()["quote"]
      ["validity"]["applies"] is False)

# ---------------------------------------------------------------------------
section("what Smart 1 Suite is allowed to decide")
# ---------------------------------------------------------------------------
from hub import ghl_hooks                                           # noqa: E402

db = builder.SessionLocal()
try:
    q = db.get(builder.Quote, qid)
    q.suite_opportunity_id = "opp-riverstone-1"
    q.status = "Sent"
    db.commit()
finally:
    db.close()

miss = ghl_hooks.sync_quote_status("opp-nobody-pushed", "won")
check("an opportunity nothing here pushed matches nothing, and says so",
      miss["matched"] is False and miss["reason"])
check("an empty opportunity id matches nothing — never a fallback to the "
      "client name, which is the guess client_key exists to refuse",
      ghl_hooks.sync_quote_status("", "won")["matched"] is False)

for stage in ("open", "quoted", "viewed", "proposal", ""):
    res = ghl_hooks.sync_quote_status("opp-riverstone-1", stage)
    check(f"'{stage or 'nothing'}' is not a decided outcome and writes nothing "
          f"— the Hub knows better than a pipeline column whether the client "
          f"has opened or accepted it",
          res["changed"] is False, res)

won = ghl_hooks.sync_quote_status("opp-riverstone-1", "won")
check("a deal marked Won in Suite moves the quote the rep actually looks at",
      won["changed"] and won["status"] == "Approved", won)
check("and says what it was before", won.get("was") == "Sent")
check("running the same webhook twice changes nothing the second time",
      ghl_hooks.sync_quote_status("opp-riverstone-1", "won")["changed"] is False)
db = builder.SessionLocal()
try:
    notes = [a.text for a in db.query(builder.Activity)
             .filter(builder.Activity.quote_id == qid).all()]
finally:
    db.close()
check("a status that changed by itself says who changed it, or a rep reading "
      "'Approved' has no way to find out why",
      any("Suite" in (t or "") for t in notes), notes)

lost = ghl_hooks.sync_quote_status("opp-riverstone-1", "lost")
check("and a deal that later dies moves it again", lost["changed"]
      and lost["status"] == "Lost")

db = builder.SessionLocal()
try:
    db.get(builder.Quote, qid).status = "Converted"
    db.commit()
finally:
    db.close()
final = ghl_hooks.sync_quote_status("opp-riverstone-1", "lost")
check("a Converted quote is never moved — an insertion order exists, Suite "
      "has no way to know that, and walking it back is the one change nobody "
      "could undo from either screen",
      final["changed"] is False and "insertion order" in final["reason"], final)

check("the sync reuses the module the app actually loaded rather than "
      "importing a second mapping of the same tables",
      'sys.modules.get("salesb_app")'
      in open(os.path.join(ROOT, "hub", "ghl_hooks.py"), encoding="utf-8").read())

# ---------------------------------------------------------------------------
section("delivery is back on the media plan")
# ---------------------------------------------------------------------------
plan_state = {"months": 6, "items": [
    {"category": "DISPLAY", "product": "Category", "rate": "CPM",
     "rateValue": 4.25, "dollars": 2000},
    {"category": "PRODUCTION", "product": "Video Production", "dollars": 1500,
     "basis": "one_time"},
    {"category": "MANAGEMENT", "product": "Management Fee", "dollars": 500}]}
plan = builder.media_plan_rows(plan_state)
check("every line is in the table", len(plan["rows"]) == 3)
check("an impression line reports what it buys at the QUOTED rate, not the "
      "listed one — the card's own $4.25 promises twice what the line can "
      "deliver", "235,294" in plan["rows"][0]["delivery"], plan["rows"][0])
check("a fee reports no units at all rather than a plausible number",
      plan["rows"][2]["delivery"] == "Not impression-based")
check("and a one-time line says once rather than being multiplied by the "
      "flight under a per-month heading",
      "once" in plan["rows"][1]["delivery"] or
      plan["rows"][1]["delivery"] == "Not impression-based",
      plan["rows"][1])
check("the column heading says per month, so the number is not read as the "
      "campaign total", "Delivery / mo" in plan["columns"])
check("the words travel with the figures — an estimate printed bare reads as "
      "a guarantee, which is what the ROI section was rebuilt to undo",
      "not a guarantee" in plan["note"])
check("and the lines that are not in that figure are named rather than "
      "quietly under-reporting the campaign",
      "Management Fee" in plan["note"] and "Video Production" in plan["note"])
empty = builder.media_plan_rows({"months": 6, "items": [
    {"category": "MANAGEMENT", "product": "Management Fee", "dollars": 500}]})
check("a plan with nothing impression-based says so instead of printing a "
      "zero", "no delivery estimate" in empty["note"], empty["note"])

check("one reading, three renderers: the preview is handed the server's rows "
      "rather than carrying a fourth copy of the arithmetic",
      "media_plan" in staff.get(f"/sales/builder/api/quotes/{qid}")
      .get_json()["quote"])
tpl = open(os.path.join(ROOT, "modules", "sales_builder", "templates",
                        "index.html"), encoding="utf-8").read()
check("and the browser computes no delivery of its own",
      "S._mediaPlan" in tpl and "/ 1000" not in tpl.split("function mediaPlanEditor")[1][:2000])
check("a figure priced against a budget that has since been edited reads as "
      "recalculating rather than stating a confident wrong number",
      "recalculating" in tpl and "function planRowFor" in tpl)

app_src = open(os.path.join(ROOT, "modules", "sales_builder", "app.py"),
               encoding="utf-8").read()
check("the Word export and the PDF draw the same columns — this table was "
      "four columns to the PDF's five, so one client's proposal said two "
      "different things depending on which file was sent",
      app_src.count("media_plan_rows(state)") >= 3)

pdf = staff.get(f"/sales/builder/api/quotes/{qid}/pdf")
check("the PDF still builds", pdf.status_code == 200
      and pdf.data[:4] == b"%PDF", pdf.status_code)
docx = staff.get(f"/sales/builder/api/quotes/{qid}/docx")
check("and so does the Word copy", docx.status_code == 200 and len(docx.data) > 5000)

# --- and the copy above the table stopped contradicting it -----------------
from hub import proposal_spec as hub_spec                           # noqa: E402

check("the seeded copy no longer promises there is no markup — it sat "
      "directly above a table quoting CPM at twice the card, and it named "
      "our internal pricing on a document a client reads",
      "no markup" not in builder.section_body_seeds
      if hasattr(builder, "section_body_seeds") else True)
seeded = staff.post("/sales/builder/api/quotes", json={"data": {
    "client": "Icon Solar", "months": 6,
    "objectives": ["Lead Generation"],
    "items": [{"category": "DISPLAY", "product": "Category", "rate": "CPM",
               "rateValue": 4.25, "dollars": 2000}]}}).get_json()["quote"]
body = json.dumps(staff.get(f"/sales/builder/api/quotes/{seeded['id']}")
                  .get_json()["quote"]["data"].get("sections") or {})
check("nor does a freshly seeded proposal carry it", "no markup" not in body)
check("and the scrubber now catches the phrase in both orders — the rule was "
      "written against 'the rate card' while the document said 'card rate', "
      "so it passed a check written for exactly it",
      hub_spec.client_safe("Every rate is the Smart 1 card rate.") == ""
      and hub_spec.client_safe("Rates follow the rate card.") == "")
check("without eating an ordinary sentence about a rate",
      hub_spec.client_safe("This is a standard rate for the market.")
      == "This is a standard rate for the market.")

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
