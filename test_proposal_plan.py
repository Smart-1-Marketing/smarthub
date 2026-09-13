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
section("The page writes directions, not JSON")
with open(os.path.join(ROOT, "hub", "templates", "proposal_execution.html"), encoding="utf-8") as fh:
    tpl = fh.read()
check("no task result is printed as a JSON dump", "JSON.stringify(r,null,2)" not in tpl
      and "JSON.stringify(r, null, 2)" not in tpl)
check("the page carries the three plan lists", all(k in tpl for k in ("planLists", "planAccept", "planAdd")))
check("...and the questions", "data-answer" in tpl)
for key in ("proposal_execution.plan.review", "proposal_execution.plan.questions"):
    check(f"the bubble {key} is placed guarded", f"help_dot('{key}') if help_dot is defined" in tpl)
    from hub import help as hub_help
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
