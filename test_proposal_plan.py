"""Proposal Execution — the plan a person reads, not the JSON a tool returned.

    python3 test_proposal_plan.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

Analyzing a proposal produced a task graph and a board, and three things a
person actually wants on the day the proposal is signed were nowhere on it:
the creative that has to exist, the one-time tasks before launch, and the
monthly promises the client will notice when they stop. And what the board
did show of a finished task was its result dict printed as JSON in a <pre>
-- a page of braces to somebody who wanted to know what to do next.

`hub/proposal_plan.py` builds the three lists, asks about what the proposal
does not say rather than guessing, and takes a person's review: keep, drop,
add, answer. This asserts the rules it is built on.

* Creative sizes come from the spec kit through the same reader the
  Proposal Builder's gate uses; nothing here retypes a banner size.
* The lists exist with no model at all, and the model's additions must
  quote the proposal -- a quote the text does not contain is kept and
  marked, never trusted and never silently dropped.
* Nothing arrives accepted. A rule item can be dropped but not removed;
  only an item a person added can be removed. An unknown id, list or
  question is refused by name and nothing is half-applied.
* A superseded run's review follows the items still proposed, and the
  items a person added come across whole.
* A run analyzed before plans existed gets one built from its stored
  analysis on first read rather than a page drawing "nothing to do".
* The page renders a task's result as directions -- summary, headings,
  bullets, a link to the tool -- and never as a JSON dump. The renderer is
  lifted out of the template and driven in node, so a copy restated here
  is not a third thing to keep in step.
* A quote built in the Proposal Builder is read as data: the channels are
  its rate-card lines, the budgets its dollars, the creative the gate's own
  reading of its products, and the start date, the supplier and the
  reporting cadence arrive as answers marked as the quote's. Picking the
  PDF it was filed as reads the quote it was rendered from, and another
  client's quote is refused as not found.
* An answer is read by the work: the launch date becomes a due date on
  every task, a supplier marks every creative item of its channel, the
  cadence lands on the report tasks, and the brief and the packet carry
  them. Derived on read and stored nowhere.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-propplan-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "propplan-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")
os.environ.pop("OPENAI_API_KEY", None)

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print(f"  FAIL " + label + f"\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print("\n" + title)
    print("-" * 62)


import wsgi                                                          # noqa: E402
import hub.proposal_execution as pe                                  # noqa: E402
import hub.proposal_plan as pp                                       # noqa: E402
import hub.extensions as hub_extensions                              # noqa: E402
import hub.proposals as proposals_mod                                # noqa: E402
import hub as hub_pkg                                                # noqa: E402
import hub.auth as auth                                              # noqa: E402
import hub.creative_needs as creative_needs                          # noqa: E402
import hub.creative_specs as creative_specs                          # noqa: E402
import hub.help as hub_help                                          # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
db = hub_extensions.db

TEXT = """
MONOGRAM HOMES 2026 YEAR-END MARKETING PLAN
$2,250 CURRENT MONTHLY FOUNDATION
September & October
Website Retargeting $500
Paid Search $550
Enhanced SEO + AI Optimization $1,400
Stadium to Screen $2,500
Meta In-Market Home Buyers $750
YouTube In-Market Home Buyers
Social Posting Outline $125
Monthly YouTube Sales Video $100
ChatGPT / AI Advertising $400
Ohio State Buckeyes · Columbus, OH DMA · Ohio Stadium
3 custom audio commercials (:15 / :30)
Clickable 300x250 companion banners
Monthly delivery, completion and banner-click reporting
"""
CLIENT = "Monogram Homes"

analysis, method = pe.analyze_text(TEXT, CLIENT)
check("the fixture parses without a model", method, "heuristic")

# ---------------------------------------------------------------------------
section("The three lists exist with no model, and creative comes from the kit")
plan = pp.build_plan(analysis, TEXT, CLIENT)
for name in pp.LISTS:
    check(f"{name}: something is proposed", len(plan[name]) > 0)
check("the plan says it was built from the rules alone", plan["source"], "rules")
check("...and says in words that the model was not asked",
      any("OPENAI_API_KEY" in n for n in plan["notes"]))
check("every item arrives unreviewed, never accepted on the analysis's say-so",
      all(it["accepted"] is None for n in pp.LISTS for it in plan[n]))
check("every item carries an id, a title and a source",
      all(it.get("id") and it.get("title") and it.get("source") for n in pp.LISTS for it in plan[n]))

by_title = {it["title"]: it for it in plan["creative"]}
banner = by_title.get("Retargeting banner set") or {}
display_line = creative_needs.units_line({"items": [{"product": "Website Retargeting"}]},
                                         creative_needs.RETARGETING)
check("the retargeting banner set is the kit's own size run, word for word",
      banner.get("detail"), display_line)
check("...and is a file somebody has to supply, not copy", banner.get("kind"), "image")
audio = next((it for it in plan["creative"] if it["channel"] == "stadium_audio" and it["kind"] == "audio"), None)
check("the stadium buy asks for an audio spot", audio is not None)
companion = next((it for it in plan["creative"] if it["channel"] == "stadium_audio" and it["kind"] == "image"), None)
check("...and its companion banner", companion is not None)
radio_unit = creative_specs.BY_ID["radio_companion"]
w, h = radio_unit["size"]
check("...whose size is the kit's", f"{w}x{h}" in (companion or {}).get("detail", ""))
check("paid search's creative is ad copy", any(it["kind"] == "copy" and it["channel"] == "paid_search" for it in plan["creative"]))
check("a YouTube in-market buy asks for one spot, not the kit's six formats",
      sum(1 for it in plan["creative"] if it["channel"] == "youtube_ads"), 1)

with open(os.path.join(ROOT, "hub", "proposal_plan.py"), encoding="utf-8") as fh:
    src = fh.read()
code_only = re.sub(r'"""[\s\S]*?"""', "", src)
code_only = "\n".join(l for l in code_only.splitlines() if not l.strip().startswith("#"))
check("no banner size is typed into the plan module -- the kit is the one source",
      re.findall(r"\b\d{3,4}x\d{2,4}\b", code_only), [])

# ---------------------------------------------------------------------------
section("What the proposal does not say is asked, with the reason; what it says is answered")
qkeys = {q["key"]: q for q in plan["questions"]}
check("no start date in the text -> the launch date is asked", "launch_date" in qkeys)
check("a channel with no dollar amount beside it -> its budget is asked",
      "budget:youtube_ads" in qkeys)
check("a channel with a budget is not asked for one", "budget:retargeting" not in qkeys)
check("every question says why it is being asked",
      all(str(q.get("why") or "").strip() for q in plan["questions"]))
check("the supplier is asked for a channel with files to supply", "creative_supply:retargeting" in qkeys)
check("...and not for a channel whose creative is only copy", "creative_supply:paid_search" not in qkeys)
video_q = qkeys.get("creative_supply:youtube_video") or {}
check("a product that includes production is answered from the product, not asked",
      (video_q.get("answer"), video_q.get("from_text")), ("smart1", True))
check("the text mentions reporting, so the cadence is not asked", "reporting_cadence" not in qkeys)

no_report = pp.build_plan(analysis, TEXT.replace("reporting", "delivery"), CLIENT)
check("...and is asked when it does not", any(q["key"] == "reporting_cadence" for q in no_report["questions"]))

# ---------------------------------------------------------------------------
section("The model's additions quote the proposal, or are marked")
_calls = []


def fake_ask(prompt, **kw):
    _calls.append(kw)
    return json.dumps({
        "creative": [
            {"title": "Produce three custom audio commercials at :15 and :30", "detail": "",
             "channel_key": "stadium_audio", "evidence": "3 custom audio commercials (:15 / :30)"},
            {"title": "Design a 30-second CTV spot", "detail": "",
             "channel_key": "stadium_audio", "evidence": "a 30-second connected TV commercial"},
        ],
        "launch": [
            # The same title as a rule item: folded into it, never a duplicate.
            {"title": "Confirm conversion tracking is in place before any spend starts", "detail": "",
             "channel_key": "", "evidence": "no such line"},
        ],
        "monthly": [
            {"title": "Send the monthly delivery, completion and banner-click report", "detail": "",
             "channel_key": "stadium_audio", "evidence": "Monthly delivery, completion and banner-click reporting"},
        ],
        "supply": [
            {"channel_key": "stadium_audio", "who": "smart1", "evidence": "3 custom audio commercials (:15 / :30)"},
            {"channel_key": "meta", "who": "client", "evidence": "the client will provide the carousel photos"},
        ],
        "unclear": [{"question": "Which communities does the Meta carousel feature?",
                     "why": "The proposal names the carousel and no community."}],
    })


ai_plan = pp.build_plan(analysis, TEXT, CLIENT, ask=fake_ask)
check("the model is asked under this module's own name, so the spend is filed right",
      _calls and _calls[0].get("module"), "proposal_execution")
check("the plan says the model contributed", ai_plan["source"], "rules+ai")
ai_items = [it for n in pp.LISTS for it in ai_plan[n] if it["source"] == "ai"]
grounded = next((it for it in ai_items if "three custom audio" in it["title"]), None)
check("an item whose quote is in the proposal is kept and grounded",
      grounded is not None and grounded["grounded"], True)
ungrounded = next((it for it in ai_items if "CTV spot" in it["title"]), None)
check("an item whose quote is NOT in the proposal is kept...", ungrounded is not None)
check("...and marked rather than trusted", (ungrounded or {}).get("grounded"), False)
check("...and still arrives unreviewed", (ungrounded or {}).get("accepted"), None)
dup_titles = [it["title"] for it in ai_plan["launch"]
              if it["title"] == "Confirm conversion tracking is in place before any spend starts"]
check("an AI item with a rule item's title is folded in, not listed twice", len(dup_titles), 1)
check("the plan's headline counts the unverified ones", ai_plan["summary"]["unverified"], 1)
aq = {q["key"]: q for q in ai_plan["questions"]}
stadium_q = aq.get("creative_supply:stadium_audio") or {}
check("a supplier the model quoted from a real line answers the question from the text",
      (stadium_q.get("answer"), stadium_q.get("from_text")), ("smart1", True))
meta_q = aq.get("creative_supply:meta") or {}
check("a supplier the model could not quote is still asked",
      (meta_q.get("answer"), meta_q.get("from_text")), ("", False))
check("the model's open question joins the list, with its reason",
      any(q["question"].startswith("Which communities") and q["why"] for q in ai_plan["questions"]))


def broken_ask(prompt, **kw):
    raise RuntimeError("boom")


broken = pp.build_plan(analysis, TEXT, CLIENT, ask=broken_ask)
check("a model that fails costs the specific promises, never the plan",
      all(len(broken[n]) == len(plan[n]) for n in pp.LISTS))
check("...and the note names it", any("RuntimeError" in n for n in broken["notes"]))

# ---------------------------------------------------------------------------
section("A person's review: keep, drop, add, remove, answer -- refused by name, never half-applied")
first = plan["creative"][0]["id"]
reviewed = pp.apply_decisions(plan, {"accept": {first: True}})
check("keeping an item sets it", next(it for it in reviewed["creative"] if it["id"] == first)["accepted"], True)
reviewed = pp.apply_decisions(reviewed, {"accept": {first: False}})
check("dropping an item keeps it on the list, marked", next(it for it in reviewed["creative"] if it["id"] == first)["accepted"], False)
check("...so the count of dropped says so", reviewed["summary"]["lists"]["creative"]["dropped"], 1)
reviewed = pp.apply_decisions(reviewed, {"add": [{"list": "monthly", "title": "Call the client on the first of the month"}]})
manual = next(it for it in reviewed["monthly"] if it["source"] == "manual")
check("an added item is the person's own and arrives kept", manual["accepted"], True)
try:
    pp.apply_decisions(reviewed, {"remove": [first]})
    refused = ""
except ValueError as exc:
    refused = str(exc)
check("a rule item cannot be removed -- only marked not needed", "not needed" in refused)
removed = pp.apply_decisions(reviewed, {"remove": [manual["id"]]})
check("an added item can be removed", all(it["id"] != manual["id"] for it in removed["monthly"]))
for bad, label in (({"accept": {"no-such-id": True}}, "an unknown item id"),
                   ({"add": [{"list": "weekly", "title": "x"}]}, "an unknown list"),
                   ({"add": [{"list": "launch", "title": "  "}]}, "an empty title"),
                   ({"answers": {"not_a_question": "x"}}, "an unknown question")):
    try:
        pp.apply_decisions(reviewed, bad)
        check(f"{label} is refused", False)
    except ValueError:
        check(f"{label} is refused", True)
before = json.dumps(reviewed, sort_keys=True)
try:
    pp.apply_decisions(reviewed, {"accept": {first: True}, "remove": ["no-such-id"]})
except ValueError:
    pass  # the refusal is the point; what is asserted is the plan beneath it, next line
check("a refused decision leaves the plan exactly as it was", json.dumps(reviewed, sort_keys=True), before)
answered = pp.apply_decisions(reviewed, {"answers": {"launch_date": "October 1"}})
check("an answer is stored against its question", answered["answers"].get("launch_date"), "October 1")
q = next(q for q in answered["questions"] if q["key"] == "launch_date")
check("...and the question carries it", (q["answer"], q["from_text"]), ("October 1", False))
check("...and the open-question count falls", answered["summary"]["open_questions"], plan["summary"]["open_questions"] - 1)
cleared = pp.apply_decisions(answered, {"answers": {"launch_date": ""}})
check("a blanked answer is removed rather than stored as empty", "launch_date" not in cleared["answers"])

# ---------------------------------------------------------------------------
section("Superseding carries the review forward with the items still proposed")
old = pp.apply_decisions(plan, {"accept": {first: False},
                                "add": [{"list": "launch", "title": "Walk the site with the client"}],
                                "answers": {"launch_date": "October 1"}})
fresh = pp.build_plan(analysis, TEXT, CLIENT)
carried = pp.carry_forward(fresh, old)
check("a verdict follows an item still proposed", next(it for it in carried["creative"] if it["id"] == first)["accepted"], False)
check("an item the person added comes across whole",
      any(it["title"] == "Walk the site with the client" and it["source"] == "manual" for it in carried["launch"]))
check("an answer follows a question still asked", carried["answers"].get("launch_date"), "October 1")
check("an item never reviewed stays unreviewed",
      any(it["accepted"] is None for it in carried["creative"]))

# ---------------------------------------------------------------------------
section("Through the run: stored, served, and built after the fact for an older run")
_FIX = {"p1": {"id": "p1", "title": "Plan", "filename": "plan.pdf", "kind": "file"}}
proposals_mod.list_proposals = lambda client: list(_FIX.values()) if client == CLIENT else []
hub_pkg._proposal_text_for = lambda client, ident: TEXT

with hub_app.app_context():
    run, created = pe.create_run(CLIENT, "p1", owner="rep@smart1marketing.com", actor="rep")
    RUN_ID = run.id
    full = run.as_dict(full=True)
    check("the run carries its plan", bool((full.get("plan") or {}).get("creative")))
    check("...built at analysis time, not on read", bool(run.plan().get("summary")))
    ev = pe.events_for_run(run.id, 3)
    check("the analysis event says what the plan holds in words",
          any("creative item" in e["message"] and "question" in e["message"] for e in ev))
    first_id = full["plan"]["creative"][0]["id"]
    pe.update_plan(run.id, {"accept": {first_id: True},
                            "add": [{"list": "creative", "title": "Lobby poster for the model home"}]},
                   actor="rep")
    again = pe.get_run(run.id).plan()
    check("a decision made through the run sticks", next(it for it in again["creative"] if it["id"] == first_id)["accepted"], True)
    task = next(t for t in pe.tasks_for_run(run.id) if t.task_key == "retargeting_creative")
    kept = pe._kept_plan_for(run, task)
    unreviewed = [it["title"] for it in again["creative"] if it["accepted"] is None]
    check("there are items nobody has reviewed yet", len(unreviewed) > 0)
    check("a working brief is handed what was kept -- the ticked item and the one the person added",
          sorted(k.split(" — ")[0] for k in kept["creative"]),
          sorted(["Retargeting banner set", "Lobby poster for the model home"]))
    check("...and nothing a person has not reviewed",
          all(all(not k.startswith(u) for u in unreviewed) for k in kept["creative"]))

    # A run written before the column existed: wipe its plan and read it back.
    run.plan_json = "{}"
    db.session.commit()
    late = pe.plan_for(pe.get_run(run.id))
    check("an older run gets a plan built from its analysis on first read", bool(late.get("creative")))
    check("...says so", any("after the fact" in n for n in late.get("notes", [])))
    check("...and it is stored so a review of it sticks", bool(pe.get_run(run.id).plan().get("creative")))

    staff = WSGIClient(wsgi.application)
    staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")
    resp = staff.post(f"/api/proposal-execution/run/{run.id}/plan",
                      json={"add": [{"list": "monthly", "title": "Text the sales team the numbers"}]})
    check("the plan route answers", resp.status_code, 200)
    body = resp.get_json()
    check("...with the whole run, plan included",
          any(it["title"] == "Text the sales team the numbers" for it in body["run"]["plan"]["monthly"]))
    resp = staff.post(f"/api/proposal-execution/run/{run.id}/plan", json={"remove": [first_id]})
    check("a refused decision is a 400 with the reason, not a 500", resp.status_code, 400)
    check("...naming what to do instead", "not needed" in (resp.get_json() or {}).get("error", ""))
    anon = WSGIClient(wsgi.application)
    resp = anon.post(f"/api/proposal-execution/run/{run.id}/plan", json={})
    check("a stranger is refused", resp.status_code in (302, 401, 403))

    try:
        pe.add_missing_columns()
        pe.add_missing_columns()
        ok = True
    except Exception:                                                # noqa: BLE001
        ok = False
    check("the late column is applied without raising, once or twice", ok)
    check("plan_json is one of the late columns the live database is given",
          "plan_json" in pe._LATE_COLUMNS)

# ---------------------------------------------------------------------------
section("A quote built in the Hub is read as data, not as a PDF")
import modules.sales_builder.app as builder                         # noqa: E402
import hub.proposal_quote_facts as qf                               # noqa: E402
import hub.rate_card as rate_card                                   # noqa: E402

QUOTE_CLIENT = "Riverstone Dental"
QUOTE_STATE = {
    "client": QUOTE_CLIENT, "months": 6, "budget": 8000, "startDate": "2026-11-02",
    "objectives": ["Lead Generation"], "kpis": ["Cost per lead"],
    "landingUrl": "https://riverstonedental.com/new-patients",
    "trackingPlan": {"primaryConversion": "Appointment request form", "ga4": "G-1"},
    "targetAreas": [{"type": "radius", "name": "Dublin, OH", "radius": 10}],
    # The creative gate's own answers: display exists, Smart 1 produces video for a fee.
    "creativePlan": {"video": {"answer": "client", "fee": 750}, "display": {"answer": "has"}},
    "sections": [{"id": "reporting", "title": "Reporting, Optimization & Transparency", "enabled": True,
                  "body": "You will receive a **monthly** performance report and a call to walk through it."}],
    "items": [
        {"category": "DISPLAY", "product": "Category", "rate": "CPM", "rateValue": 4.25, "dollars": 2000},
        # A video product filed under the card's DISPLAY heading.
        {"category": "DISPLAY", "product": "Programmatic - Targeted", "rate": "CPM", "dollars": 800},
        {"category": "OTT", "product": "Connected TV - Targeted  - This is played on televisions only", "dollars": 3000},
        {"category": "SEARCH ENGINE MARKETING / PAY PER CLICK", "product": "Pay Per Click", "dollars": 1000},
        # Filed under the card's SOCIAL ADS - VIDEO heading, which the gate reads as video.
        {"category": "SOCIAL ADS - VIDEO", "product": "Snapchat - Paid Social Media Advertising", "dollars": 500},
        {"category": "CREATIVE / DESIGN SERVICES", "product": "Standard Set of 6 Ad Creation", "dollars": 250, "basis": "one_time"},
        {"category": "ADD-ON PRODUCT", "product": "1st Phone Number - with area code", "dollars": 25},
        {"category": "MANAGEMENT", "product": "Management Fee", "dollars": 500},
    ],
}
bdb = builder.SessionLocal()
try:
    qrow = builder.Quote(quote_number="Q-TEST-0001", status="Sent", client=QUOTE_CLIENT,
                         website="https://riverstonedental.com", data=json.dumps(QUOTE_STATE),
                         monthly_budget=7300, months=6,
                         products_summary="Display · Connected TV · Paid Search", revision=1)
    bdb.add(qrow)
    bdb.commit()
    QID = qrow.id
finally:
    bdb.close()

_PDF = {"id": "recX", "title": "Riverstone PDF", "filename": "riverstone.pdf", "kind": "pdf"}
proposals_mod.list_proposals = lambda client: ([_PDF] if client == QUOTE_CLIENT
                                               else list(_FIX.values()) if client == CLIENT else [])
hub_pkg._proposal_text_for = lambda client, ident: ("Riverstone Dental proposal. Display $2,000."
                                                    if ident == "recX" else TEXT)

with open(os.path.join(ROOT, "hub", "proposal_quote_facts.py"), encoding="utf-8") as fh:
    qsrc = fh.read()
qcode = re.sub(r'"""[\s\S]*?"""', "", qsrc)
qcode = "\n".join(l for l in qcode.splitlines() if not l.strip().startswith("#"))
check("no banner size is typed into the quote reader either", re.findall(r"\b\d{3,4}x\d{2,4}\b", qcode), [])

unplaced = []
for prod in rate_card.products():
    key = qf.channel_for_item(prod)
    if key and key not in qf.QUOTE_CHANNELS:
        unplaced.append((prod.get("category"), prod.get("product"), key))
    if not key and str(prod.get("category") or "").upper() not in qf._NOT_A_CHANNEL:
        unplaced.append((prod.get("category"), prod.get("product"), "nothing"))
check("every product on the real rate card lands on a channel or a category named as not one",
      unplaced, [])
check("a video product under the display heading is a video channel",
      qf.channel_for_item({"category": "DISPLAY", "product": "Programmatic - Targeted"}), "ctv")
check("...and a banner product beside it is display",
      qf.channel_for_item({"category": "DISPLAY", "product": "Category"}), "display")
check("a Meta retargeting line is a Meta campaign, a website retargeting line is retargeting",
      (qf.channel_for_item({"category": "RETARGETING", "product": "Facebook / Instagram Retargeting"}),
       qf.channel_for_item({"category": "RETARGETING", "product": "Website Retargeting"})),
      ("meta", "retargeting"))
check("a fee is not a channel", qf.channel_for_item({"category": "MANAGEMENT", "product": "Management Fee"}), "")

with hub_app.app_context():
    choices = pe.proposal_choices(QUOTE_CLIENT)
    qc = [c for c in choices if c.get("kind") == "quote"]
    check("the saved quote is offered to start a run from",
          len(qc) == 1 and qc[0]["id"] == f"quote:{QID}")
    check("...with its number on it", (qc or [{}])[0].get("quote_number"), "Q-TEST-0001")
    check("...beside the uploaded document, which is still offered", any(c.get("id") == "recX" for c in choices))

    qrun, created = pe.create_run(QUOTE_CLIENT, f"quote:{QID}", owner="rep@smart1marketing.com", actor="rep")
    a = qrun.analysis()
    check("the run is read from the quote", a.get("analysis_method"), "quote")
    check("the channels are the rate-card lines, exactly",
          [c["key"] for c in a["channels"]], ["display", "ctv", "paid_search", "paid_social"])
    by = {c["key"]: c for c in a["channels"]}
    check("a channel's budget is its lines' dollars, not a regex over prose", by["display"]["budgets"], ["$2,000/mo"])
    check("two lines of one family add up, and the video product under the display heading lands on video",
          by["ctv"]["budgets"], ["$3,800/mo"])
    check("the start date is the flight date", a.get("flight_dates"), ["2026-11-02"])
    inputs = qrun.inputs()
    check("the quote's landing page and conversion goal prefill the shared inputs",
          (inputs.get("landing_url"), inputs.get("conversion_goal")),
          ("https://riverstonedental.com/new-patients", "Appointment request form"))
    check("...and its target areas the geography", "Dublin" in (inputs.get("target_geography") or ""))
    qplan = qrun.plan()
    qq = {q["key"]: q for q in qplan["questions"]}
    check("the launch date is answered from the quote and marked as the quote's",
          (qq["launch_date"]["answer"], qq["launch_date"]["from_text"], qq["launch_date"]["source_label"]),
          ("2026-11-02", True, "quote"))
    check("who supplies the display creative comes from the quote's creative step",
          ((qq.get("creative_supply:display") or {}).get("answer"),
           (qq.get("creative_supply:display") or {}).get("source_label")), ("client", "quote"))
    check("...and a priced production answer means Smart 1 produces the video",
          (qq.get("creative_supply:ctv") or {}).get("answer"), "smart1")
    check("a cadence the Reporting section states is the quote's answer",
          ((qq.get("reporting_cadence") or {}).get("answer"), (qq.get("reporting_cadence") or {}).get("from_text")),
          ("monthly", True))
    check("no budget is asked for -- every line carries one", not any(k.startswith("budget:") for k in qq))
    check("no channel is one this Hub has no recipe for",
          not any(k.startswith("creative_for:") for k in qq) and not any("no recipe" in n for n in qplan["notes"]))
    banner = next((it for it in qplan["creative"] if it["channel"] == "display" and it["kind"] == "image"), {})
    want_line = creative_needs.units_line({"items": [{"category": "DISPLAY", "product": "Category"}]},
                                          creative_needs.DISPLAY)
    check("the display creative is the kit's reading of the quote's own lines", banner.get("detail"), want_line)
    ctv = [it for it in qplan["creative"] if it["channel"] == "ctv"]
    check("a connected TV buy asks for the kit's spot", any("Connected TV" in it["title"] for it in ctv))
    snap = [it for it in qplan["creative"] if it["channel"] == "paid_social" and it["kind"] != "copy"]
    check("a Snapchat line filed under the card's video heading still gets Snapchat's own units",
          len(snap) > 0 and all(it["title"].startswith("Paid Social:") for it in snap))
    check("a production line on the quote becomes a launch task",
          any(it["title"].startswith("Produce the Standard Set of 6") for it in qplan["launch"]))
    check("...and a phone number an add-on to set up",
          any(it["title"].startswith("Set up 1st Phone Number") for it in qplan["launch"]))
    check("a fee line is not a task", not any("Management Fee" in it["title"] for it in qplan["launch"]))

    bdb = builder.SessionLocal()
    try:
        bdb.get(builder.Quote, QID).client_filed_as = "recX@1"
        bdb.commit()
    finally:
        bdb.close()
    choices = pe.proposal_choices(QUOTE_CLIENT)
    check("once the quote is filed as a PDF, the PDF is not offered a second time",
          not any(c.get("id") == "recX" for c in choices))
    run2, _created2 = pe.create_run(QUOTE_CLIENT, "recX", owner="rep", actor="rep", force=True)
    RUN2_ID = run2.id
    check("picking the filed PDF reads the quote it was rendered from",
          (run2.analysis().get("analysis_method"), run2.proposal_id), ("quote", f"quote:{QID}"))
    check("...and supersedes the earlier run of the same quote", pe.get_run(qrun.id).state, pe.RUN_SUPERSEDED)
    try:
        pe.create_run("Somebody Else", f"quote:{QID}", owner="x", actor="x", force=True)
        refused = False
    except ValueError as exc:
        refused = "could not be found" in str(exc)
    check("another client's quote is refused as not found, never read onto this record", refused)
    run2.plan_json = "{}"
    db.session.commit()
    late = pe.plan_for(pe.get_run(run2.id))
    check("a quote run rebuilt after the fact re-reads the quote, not a PDF",
          bool(late.get("creative")) and any(q["key"] == "launch_date" and q["answer"] == "2026-11-02"
                                             for q in late["questions"]))

# ---------------------------------------------------------------------------
section("An answer is read by the work, not filed beside it")
with hub_app.app_context():
    run = pe.get_run(RUN_ID)
    # The plan was rebuilt after the fact above, so nothing on it is kept yet.
    banner_id = next(it["id"] for it in run.plan()["creative"] if it["title"] == "Retargeting banner set")
    pe.update_plan(run.id, {"accept": {banner_id: True},
                            "answers": {"launch_date": "2026-10-01",
                                        "creative_supply:retargeting": "client"}}, actor="rep")
    served = pe.get_run(run.id).as_dict(full=True)["plan"]
    stored = pe.get_run(run.id).plan()
    ban = next(it for it in served["creative"] if it["title"] == "Retargeting banner set")
    check("a supplier answer marks every creative item of its channel", ban.get("supplier_label"), "the client supplies it")
    check("...and creative is wanted two weeks before launch",
          (ban.get("due"), "14 days before launch" in (ban.get("due_label") or "")), ("2026-09-17", True))
    pixel = next(it for it in served["launch"] if it["title"].startswith("Confirm the retargeting pixel"))
    check("a launch task carries its own lead time from the launch date", pixel.get("due"), "2026-09-17")
    confirm = next(it for it in served["launch"] if it["title"].startswith("Confirm the signed proposal"))
    check("...and one with none is due on launch day",
          (confirm.get("due"), "(launch day)" in (confirm.get("due_label") or "")), ("2026-10-01", True))
    report = next(it for it in served["monthly"] if it["title"].startswith("Send the client the monthly performance report"))
    check("a monthly task says the first month it is due", report.get("due_label"), "first due November 2026")
    check("the served plan says what it resolved", served["resolved"]["launch_date_label"], "Oct 1")
    check("nothing derived is stored -- the column carries answers, not dates",
          all("due" not in it and "supplier" not in it and "cadence" not in it
              for n in pp.LISTS for it in stored[n]) and "resolved" not in stored)

    # The cadence, on the run whose document asked about it: the quote's own
    # Reporting section said monthly, and a person can still say otherwise.
    pe.update_plan(RUN2_ID, {"answers": {"reporting_cadence": "weekly"}}, actor="rep")
    qserved = pe.get_run(RUN2_ID).as_dict(full=True)["plan"]
    qreport = next(it for it in qserved["monthly"] if it["title"].startswith("Send the client the monthly performance report"))
    check("the cadence lands on the report tasks", qreport.get("cadence_label"), "every week")
    check("...measured from the quote's own start date", qreport.get("due_label"), "first due December 2026")
    qpacing = next(it for it in qserved["monthly"] if it["title"].startswith("Check that spend is pacing"))
    check("...and not on a task that is not a report", "cadence" not in qpacing)
    search_task = next(t for t in pe.tasks_for_run(RUN2_ID) if t.task_key == "paid_search_activation")
    qpacket = pe._launch_runner(pe.get_run(RUN2_ID), search_task)
    check("a packet on the quote run carries the cadence and the quote's budget for its channel",
          (qpacket.get("reporting_cadence"), qpacket.get("budget")), ("every week", "$1,000/mo"))

    task = next(t for t in pe.tasks_for_run(run.id) if t.task_key == "retargeting_creative")
    kept = pe._kept_plan_for(run, task)
    check("a brief is handed the answers for its channel and the run",
          kept["answers"].get("When does the campaign launch?") == "2026-10-01"
          and kept["answers"].get("Who is supplying the Website Retargeting creative?") == "the client supplies it")
    check("...and not another channel's", not any("Stadium" in k for k in kept["answers"]))
    check("a kept item reads with its date and its supplier",
          any("in hand by Sep 17" in k and "the client supplies it" in k for k in kept["creative"]))
    activation = next(t for t in pe.tasks_for_run(run.id) if t.task_key == "retargeting_activation")
    packet = pe._launch_runner(run, activation)
    check("the launch packet carries the launch date and the supplier as their own lines",
          (packet.get("launch_date"), packet.get("creative_supply")), ("Oct 1", "the client supplies it"))
    check("...and the channel's budget", packet.get("budget"), "$500")
    check("...and says nothing about a cadence nobody was asked for", "reporting_cadence" not in packet)

    import hub.openai_responses as oai                              # noqa: E402
    captured = {}
    real_ask = oai.ask

    def _fake_ask(prompt, **kw):
        captured["prompt"] = prompt
        return '{"summary": "ok", "deliverables": ["x"]}'
    oai.ask = _fake_ask
    os.environ["OPENAI_API_KEY"] = "test-key"
    try:
        brief = pe._brief_runner(run, task)
    finally:
        oai.ask = real_ask
        os.environ.pop("OPENAI_API_KEY", None)
    check("the working brief's prompt carries the answers as fact",
          "Answers the team gave" in captured.get("prompt", "") and "2026-10-01" in captured.get("prompt", ""))
    check("...and the model's draft is what comes back", brief.get("generated_by"), "openai")

    pe.update_plan(run.id, {"answers": {"launch_date": "sometime in October"}}, actor="rep")
    served = pe.get_run(run.id).as_dict(full=True)["plan"]
    check("a launch date nothing can read costs the due dates, not the plan -- and says so",
          served["resolved"]["unreadable_launch_date"] is True and not any(it.get("due") for it in served["launch"]))

for raw, want in (("10/01/2026", "2026-10-01"), ("October 1, 2026", "2026-10-01"),
                  ("Oct 1st 2026", "2026-10-01"), ("2026-10-01T00:00:00", "2026-10-01"),
                  ("", None), ("soon", None)):
    got = pp.parse_day(raw)
    check(f"parse_day({raw!r})", got.isoformat() if got else None, want)

# ---------------------------------------------------------------------------
section("The page writes directions, not JSON")
with open(os.path.join(ROOT, "hub", "templates", "proposal_execution.html"), encoding="utf-8") as fh:
    tpl = fh.read()
check("the picker groups quotes apart from uploaded documents", 'optgroup label="Built in the Proposal Builder' in tpl)
check("the page knows a run read from a quote", "read from the quote built in the Proposal Builder" in tpl)
check("a question says which document answered it", "Answered from the ${esc(q.source_label" in tpl)
check("an item shows its due date and its supplier", "it.due_label" in tpl and "it.supplier_label" in tpl)
check("no task result is printed as a JSON dump", "JSON.stringify(r,null,2)" not in tpl
      and "JSON.stringify(r, null, 2)" not in tpl)
check("the page carries the three plan lists", all(k in tpl for k in ("planLists", "planAccept", "planAdd")))
check("...and the questions", "data-answer" in tpl)
for key in ("proposal_execution.plan.review", "proposal_execution.plan.questions"):
    check(f"the bubble {key} is placed guarded", f"help_dot('{key}') if help_dot is defined" in tpl)
    check(f"...and has an entry behind it", any(h.key == key for h in hub_help.REGISTRY))

m = re.search(r"// --- result renderer.*?\n(.*?)// --- end result renderer ---", tpl, re.S)
check("the result renderer is marked for lifting", m is not None)
SRC = m.group(1) if m else ""
ESC = "const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
sample = {
    "summary": "Prepared the retargeting creative brief from the proposal.",
    "deliverables": ["Six banner sizes", "One HTML5 package"],
    "checklist": ["Confirm proposal scope and budget.", "Use shared campaign inputs."],
    "qa": ["Destination and CTA match the plan."],
    "artifact_url": "/tools/utm?batch=7",
    "inputs": {"landing_url": "https://example.com", "primary_cta": "Book a tour"},
    "channel": {"key": "retargeting", "name": "Website Retargeting", "budgets": ["$500"]},
    "upstream": [{"task": "Retargeting plan", "state": "approved"}],
    "generated_by": "template",
    "handoff": True,
    "creative_needed": ["Retargeting banner set — 728x90, 300x250"],
    "launch_date": "Oct 1",
    "creative_supply": "the client supplies it",
}
driver = ESC + SRC + "\nconsole.log(renderResult(" + json.dumps(sample) + "));"
r = subprocess.run(["node", "-e", driver], capture_output=True, text=True)
check("the lifted renderer runs on its own", r.returncode, 0)
if r.returncode:
    print("   " + (r.stderr or "").strip()[:400])
html = r.stdout if r.returncode == 0 else ""
check("the summary leads", html.startswith("<p>Prepared the retargeting creative brief"))
check("a list becomes bullets under a heading a person would use",
      "<h5>What to produce</h5><ul><li>Six banner sizes</li>" in html)
check("the kept creative is headed as such", "<h5>Creative needed</h5>" in html)
check("the answers read as their own lines, in words",
      "<b>Launch date:</b> Oct 1" in html and "<b>Who supplies the creative:</b> the client supplies it" in html)
check("the tool link is offered as a link", 'href="/tools/utm?batch=7"' in html)
check("the template fallback is said in words", "AI was not available" in html)
check("the plumbing is behind a fold, not on the page",
      "<details><summary>Technical details</summary>" in html and "landing_url" not in html.split("<details>")[0])
check("no brace-and-quote JSON reaches the reader", '{"' not in html and '":' not in html)
check("the handoff flag is not printed as a field", "Handoff" not in html)
empty = subprocess.run(["node", "-e", ESC + SRC + "\nconsole.log(renderResult({}));"],
                       capture_output=True, text=True)
check("an empty result says so rather than drawing nothing", "nothing to read yet" in empty.stdout)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
