"""The client answers their questions on their own page.

    python3 test_proposal_client_answers.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

The page a client reads at `/proposal-execution/needs/<token>` listed the
questions that were theirs to answer -- the launch date, who produces each
channel's creative, how often they want a report -- and ended with "reply
to your Smart 1 contact". So the answer came back in an email, somebody
retyped it onto the plan, and the question the page had asked stayed open
on every screen until they did. The page takes the answers now, and every
rule on it is a way that goes wrong quietly:

* **What the client posts is a proposal, never the plan's own answer.** It
  lands in `plan["client_answers"]` with their name on it; `plan["answers"]`
  moves only when a person presses *Use their answer*. A value posted at a
  token anybody holding the link can post to must not move the due date on
  every task by arriving.
* **Only the client's keys, and only the offered choices.** A budget, or
  what the model was unsure of, is ours to answer; a key that is not on
  `client_answerable()` is refused by name, and so is a choice that was
  not offered.
* **A name is required**, because an answer nobody can attribute is one
  nobody can ring back about; the email is optional.
* **Handing a file back is the same key answered `smart1`**, offered only
  on a kept item the client had agreed to supply and not yet delivered.
* **Revoked, unknown and malformed answer the same 404 on the POST as on
  the page**, a store that would not answer is a 503, and nothing internal
  reaches the page -- it loads no script, and no staff email is on it.
* **A reply read by nothing is the form-field failure**, so a pending
  answer is counted on the plan's summary, beside the question with the
  press that takes it, on the kickoff document, on Client 360 and in the
  `plan_review` issue on My Clients -- and it follows the question onto a
  superseding run.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-client-answers-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "client-answers-test")
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
import hub.client_health as client_health                            # noqa: E402
import hub.proposals as proposals_mod                                # noqa: E402
import hub.auth as auth                                              # noqa: E402
import hub.help as hub_help                                          # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
hub_pkg = sys.modules["hub"]


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


CLIENT = "Acme Tyre"
OWNER = "erik@smart1marketing.com"
TEXT = ("Retargeting display $600\nSocial Posting Outline $125\n"
        "Enhanced SEO + AI Optimization $1,400\nMonthly YouTube Sales Video $100")
_FIX = {"p1": {"id": "p1", "title": "Acme Tyre plan", "filename": "plan.pdf", "kind": "file"}}
proposals_mod.list_proposals = lambda client: list(_FIX.values()) if client == CLIENT else []
hub_pkg._proposal_text_for = lambda client, ident: TEXT


def _plan(run_id):
    return pe.plan_for(pe.get_run(run_id))


def _served(run_id):
    return pe.get_run(run_id).as_dict(full=True)["plan"]


staff = WSGIClient(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")
anon = WSGIClient(wsgi.application)


# ---------------------------------------------------------------------------
section("The page offers the client's questions as a form, and nothing else")
with hub_app.app_context():
    run, _created = pe.create_run(CLIENT, "p1", owner=OWNER, actor="rep")
    RUN_ID = run.id
    plan = _plan(RUN_ID)
    supply_key = next(q["key"] for q in plan["questions"] if q["key"].startswith("creative_supply:"))
    CHANNEL = supply_key.split(":", 1)[1]
    asked = {q["key"] for q in pp.client_questions(plan)}
    check("the launch date, the report cadence and a supply question are the client's to answer",
          {"launch_date", "reporting_cadence", supply_key} <= asked)
    check("and the budget question is not", any(k.startswith("budget:") for k in asked), False)
    answerable = pp.client_answerable(plan)
    check("client_answerable() is the same set while nothing is kept", set(answerable) == asked)
    check("the supply question offers exactly the three supply values",
          set(answerable[supply_key]["options"]), {"smart1", "client", "mixed"})
    check("the cadence question offers the plan's own cadence values",
          set(answerable["reporting_cadence"]["options"]), {"monthly", "weekly", "quarterly", "none"})
    check("a date question carries no choices", answerable["launch_date"]["options"], [])
    rows = {q["key"]: q for q in pp.client_questions(plan)}
    check("the supply choices are worded for the person being asked",
          [o["label"] for o in rows[supply_key]["options"]],
          ["Smart 1 produces it", "Our team is supplying it", "Some of each"])
    check("a client-worded label maps to the same stored value",
          [o["value"] for o in rows[supply_key]["options"]], ["smart1", "client", "mixed"])

    pe.create_client_link(RUN_ID, actor="rep", base="http://hub.test/")
    TOKEN = _plan(RUN_ID)["client_link"]["token"]
    URL = f"/proposal-execution/needs/{TOKEN}"

r = anon.get(URL)
body = r.get_data(as_text=True)
check("the client's page opens with no login", r.status_code, 200)
check("it is a form posting back to the same address",
      '<form class="need-form" method="post"' in body)
check("the launch date is a date field", 'type="date" name="a:launch_date"' in body)
check("the cadence is a select", 'name="a:reporting_cadence"' in body and "<select" in body)
check("the client's supply question is offered in their words",
      "Our team is supplying it" in body and "The client is supplying it" not in body)
check("a name is asked for", 'name="name" required' in body)
check("the page loads no script -- a stranger's page on somebody else's site",
      "<script" in body.lower(), False)
check("no staff email reaches it", "@smart1marketing.com" in body, False)
check("nothing has been answered yet, so nothing is listed as told", "What you have told us" in body, False)

# ---------------------------------------------------------------------------
section("A refusal says why, and records nothing")
r = anon.post(URL, data={"name": "", "a:launch_date": "2026-11-02"})
check("no name is refused", r.status_code, 400)
check("and the page says so in words", "tell us your name" in r.get_data(as_text=True))
with hub_app.app_context():
    check("nothing was recorded", _plan(RUN_ID).get("client_answers") or {}, {})
r = anon.post(URL, data={"name": "Pat Client"})
check("nothing filled in is refused rather than thanked", r.status_code, 400)
check("saying what to do", "answer at least one question" in r.get_data(as_text=True))
r = anon.post(URL, data={"name": "Pat Client", "a:launch_date": "2026-11-02", "a:budget:display": "$5,000"})
check("a key that is not the client's is refused by name", r.status_code, 400)
check("naming the key", "budget:display" in r.get_data(as_text=True))
with hub_app.app_context():
    check("and the whole post was refused -- the launch date did not land either",
          _plan(RUN_ID).get("client_answers") or {}, {})
r = anon.post(URL, data={"name": "Pat Client", "a:reporting_cadence": "hourly"})
check("a choice that was not offered is refused", r.status_code, 400)
r = anon.post(URL, data={"name": "Pat Client", "email": "not-an-address", "a:launch_date": "2026-11-02"})
check("an email that is not one is refused", r.status_code, 400)
check("with what they typed kept on the form", 'value="not-an-address"' in r.get_data(as_text=True))
check("and the name they typed too", 'value="Pat Client"' in r.get_data(as_text=True))
with hub_app.app_context():
    check("the plan's own answers were never touched by any of it", _plan(RUN_ID).get("answers") or {}, {})

# ---------------------------------------------------------------------------
section("An answer lands beside the plan, with a name on it, and not in it")
r = anon.post(URL, data={"name": "Pat Client", "email": "pat@acmetyre.example",
                         "a:launch_date": "2026-11-02", "a:reporting_cadence": "weekly",
                         "a:" + supply_key: "client"})
check("a good post redirects rather than re-rendering, so a refresh cannot post twice", r.status_code, 303)
check("to the same page with thanks on it", r.headers.get("Location", "").endswith(URL + "?thanks=1"))
r = anon.get(URL + "?thanks=1")
body = r.get_data(as_text=True)
check("the page thanks them", "Thank you" in body and "we have your answers" in body)
check("and lists what they told us, in their words",
      "What you have told us" in body and "Our team is supplying it" in body and "Weekly" in body)
check("as received and not yet on the plan", "received; being put on the plan" in body)
check("with their own answer preselected in the question that is still open",
      'value="2026-11-02"' in body and 'value="weekly" selected' in body)
with hub_app.app_context():
    plan = _plan(RUN_ID)
    said = plan.get("client_answers") or {}
    check("three answers recorded", sorted(said), sorted(["launch_date", "reporting_cadence", supply_key]))
    check("each with the client's name and the day", said["launch_date"]["by"], "Pat Client")
    check("and the email where given", said["launch_date"]["email"], "pat@acmetyre.example")
    check("the plan's own answers are untouched", plan.get("answers") or {}, {})
    check("so the launch date is still unanswered as far as the work is concerned",
          pp.resolve(plan)["resolved"]["launch_date"], "")
    check("the summary counts three to confirm", plan["summary"]["client_answers_pending"], 3)
    served = _served(RUN_ID)
    q = next(x for x in served["questions"] if x["key"] == "launch_date")
    check("the served question carries the client's proposal", q.get("client_proposed", {}).get("value"), "2026-11-02")
    check("marked not taken", q["client_proposed"]["taken"], False)
    check("with who said it", q["client_proposed"]["by"], "Pat Client")
    q2 = next(x for x in served["questions"] if x["key"] == supply_key)
    check("a choice is labeled in words for the rep", q2["client_proposed"]["label"], "the client supplies it")
    ev = pe.events_for_run(RUN_ID)[0]
    check("the activity strip says the client answered", "The client answered 3 question(s)" in ev["message"])
    check("as the client, not as a rep", ev.get("actor"), "client")
    doc = pe.kickoff_document(pe.get_run(RUN_ID), base="http://hub.test/")
    open_q = {x["question"]: x for x in doc["questions"]}
    launch_q = next(v for k, v in open_q.items() if "launch" in k.lower())
    check("the kickoff document carries what the client says on the open question",
          launch_q["client_says"], "2026-11-02")
    check("with their name", launch_q["client_by"], "Pat Client")
    summary_ = pe.plan_summary_for_client(CLIENT)["runs"][0]
    check("the record's summary counts the pending answers", summary_["client_answers_pending"], 3)
    issues = client_health._plan_issues([summary_])
    review = next(i for i in issues if i["kind"] == "plan_review")
    check("and My Clients' review issue names them", "3 answers from the client to confirm" in review["detail"])

# ---------------------------------------------------------------------------
section("A person takes the answer with one press, and it reads as taken")
r = staff.post(f"/api/proposal-execution/run/{RUN_ID}/plan", json={"answers": {"launch_date": "2026-11-02"}})
check("the staff press is the ordinary plan route", r.status_code, 200)
d = r.get_json()
q = next(x for x in d["run"]["plan"]["questions"] if x["key"] == "launch_date")
check("the plan now carries the date", q["answer"], "2026-11-02")
check("and the client's proposal reads as taken", q["client_proposed"]["taken"], True)
check("two still to confirm", d["run"]["plan"]["summary"]["client_answers_pending"], 2)
with hub_app.app_context():
    doc = pe.kickoff_document(pe.get_run(RUN_ID), base="http://hub.test/")
    check("the kickoff no longer lists the launch date as open",
          any("launch" in x["question"].lower() for x in doc["questions"]), False)
r = anon.get(URL)
body = r.get_data(as_text=True)
check("the client's page says that one is on the plan", "on the plan" in body)
check("and no longer asks it", 'name="a:launch_date"' in body, False)
check("while the ones not taken are still asked, preselected", 'value="weekly" selected' in body)

# ---------------------------------------------------------------------------
section("Handing a file back is the supply key answered smart1, on a kept item only")
with hub_app.app_context():
    plan = _plan(RUN_ID)
    items = [it for it in plan["creative"] if it.get("channel") == CHANNEL and it.get("kind") != "copy"]
    check("the fixture has creative on that channel", len(items) > 0)
    check("before anything is kept, the client cannot hand it back",
          pp.client_answerable(plan).get(supply_key, {}).get("handback"), False)
    pe.update_plan(RUN_ID, {"answers": {supply_key: "client"},
                            "accept": {it["id"]: True for it in items}}, actor="rep")
    plan = _plan(RUN_ID)
    check("with the supply taken as the client's and the items kept, the key is answerable as a hand-back",
          pp.client_answerable(plan)[supply_key],
          {"type": "choice", "options": ["smart1", "client", "mixed"], "handback": True})
    needs = pe.client_needs(pe.get_run(RUN_ID), base="http://hub.test/")
    check("their page lists the files and offers the hand-back on each",
          [f["handback"] for f in needs["files"]], [True] * len(needs["files"]))
    check("and the supply question is no longer asked as open",
          any(q["key"] == supply_key for q in needs["questions"]), False)
r = anon.get(URL)
body = r.get_data(as_text=True)
check("the page draws the tick", f'name="h:{CHANNEL}"' in body and "have Smart 1 produce this instead" in body)
r = anon.post(URL, data={"name": "Pat Client", f"h:{CHANNEL}": "1"})
check("ticking it posts", r.status_code, 303)
with hub_app.app_context():
    said = _plan(RUN_ID)["client_answers"]
    check("and records the same key answered smart1", said[supply_key]["value"], "smart1")
    check("the plan's own answer is still the client -- a person confirms the hand-back",
          _plan(RUN_ID)["answers"][supply_key], "client")
    served = _served(RUN_ID)
    q = next(x for x in served["questions"] if x["key"] == supply_key)
    check("the rep sees the hand-back as a proposal", q["client_proposed"]["label"], "Smart 1 produces it")
    check("not taken", q["client_proposed"]["taken"], False)

# ---------------------------------------------------------------------------
section("What the client answered follows the question onto a superseding run")
with hub_app.app_context():
    old = _plan(RUN_ID)
    carried = pp.carry_forward(pp.build_plan(pe.get_run(RUN_ID).analysis(), TEXT, CLIENT, use_ai=False), old)
    check("the client's answers come across", sorted(carried.get("client_answers") or {}), sorted(old["client_answers"]))
    check("and only for questions still asked",
          set(carried["client_answers"]) <= {q["key"] for q in carried["questions"]})

# ---------------------------------------------------------------------------
section("Revoked, unknown and malformed answer one 404; a store that will not answer, 503")
r = anon.post("/proposal-execution/needs/not-a-token", data={"name": "x", "a:launch_date": "2026-11-02"})
check("a malformed token is 404 on the POST", r.status_code, 404)
with hub_app.app_context():
    from hub.radio_share import new_token
    other = new_token()
r = anon.post(f"/proposal-execution/needs/{other}", data={"name": "x", "a:launch_date": "2026-11-02"})
check("an unknown token is 404 on the POST", r.status_code, 404)
_real = pe.run_for_client_token
pe.run_for_client_token = lambda token: (None, "The plans could not be read (OperationalError).")
try:
    r = anon.post(URL, data={"name": "x", "a:launch_date": "2026-11-02"})
    check("a store that would not answer is 503, not 404 -- a client meeting a 404 concludes the link expired",
          r.status_code, 503)
    check("and the page says to try again", "try again" in r.get_data(as_text=True))
finally:
    pe.run_for_client_token = _real
with hub_app.app_context():
    pe.revoke_client_link(RUN_ID, actor="rep")
r = anon.post(URL, data={"name": "x", "a:launch_date": "2026-11-02"})
check("a revoked token is 404 on the POST", r.status_code, 404)
with hub_app.app_context():
    check("and recorded nothing", _plan(RUN_ID)["client_answers"].get("launch_date", {}).get("by"), "Pat Client")

# ---------------------------------------------------------------------------
section("A client's answer is a date or a known word, never free text")
# The calendar control only ever posts ISO, and a form is a courtesy to
# somebody typing rather than a rule: a hand-made POST at the public link
# used to land whatever it carried in `client_answers`, and from there in
# the staff plan page's own `onclick`. Two halves, both asserted, because
# they cover different rows -- the server half is what stops a value like
# this landing from now on, and the template half is what makes a value
# stored before the fix harmless.
PAYLOAD = "x');alert(document.cookie);//"
with hub_app.app_context():
    pe.create_client_link(RUN_ID, actor="rep", base="http://hub.test/")     # the old one is revoked
    TOKEN = _plan(RUN_ID)["client_link"]["token"]
    URL = f"/proposal-execution/needs/{TOKEN}"
    # The rep took the launch date onto the plan above, which closes the
    # question to the client; clear it so the date is theirs to answer again.
    pe.update_plan(RUN_ID, {"answers": {"launch_date": ""}}, actor="rep")
    check("the launch date is open to the client again", "launch_date" in pp.client_answerable(_plan(RUN_ID)))
    was = dict(_plan(RUN_ID)["client_answers"].get("launch_date") or {})
r = anon.post(URL, data={"name": "Mallory", "a:launch_date": PAYLOAD})
check("a launch date that is not a date is refused", r.status_code, 400)
check("in words about the calendar", "could not be read" in r.get_data(as_text=True))
with hub_app.app_context():
    check("and nothing landed", _plan(RUN_ID)["client_answers"].get("launch_date"), was)
r = anon.post(URL, data={"name": "Pat Client", "a:launch_date": "Nov 3, 2026"})
check("a date typed by hand is read", r.status_code, 303)
with hub_app.app_context():
    check("and stored as ISO however it was written",
          _plan(RUN_ID)["client_answers"]["launch_date"]["value"], "2026-11-03")
r = anon.post(URL, data={"name": "Pat Client", "a:launch_date": PAYLOAD + " 2026-11-04"})
check("a date with a payload around it is a date", r.status_code, 303)
with hub_app.app_context():
    check("and only the date is kept", _plan(RUN_ID)["client_answers"]["launch_date"]["value"], "2026-11-04")
    rules = pp.client_answerable(_plan(RUN_ID))
    check("every key a client may answer is a date or a choice", {v.get("type") for v in rules.values()} <= {"date", "choice"})
    check("and a choice key carries its choices", all(v["options"] for v in rules.values() if v["type"] == "choice"))
for rule in ({"type": "text"}, {"options": []}, {}):
    try:
        pp._client_value("k", "anything", rule)
        check(f"a key of neither kind is refused ({rule})", False)
    except ValueError as exc:
        check(f"a key of neither kind is refused ({rule})", "not a question the client can answer" in str(exc))
check("a refusal quotes a long key capped rather than whole", len(pp._said("k" * 500)) < 80)
check("and a short one whole", pp._said("budget:display"), "'budget:display'")

import json as _json                                                 # noqa: E402
import re as _re                                                     # noqa: E402
import subprocess as _sp                                             # noqa: E402
tmpl = _read("hub", "templates", "proposal_execution.html")
_esc = _re.search(r"const esc=s=>String\(s\?\?''\)\.replace\(/\[&<>\"'\]/g,c=>\(\{[^}]*\}\[c\]\)\);", tmpl)
_cs = _re.search(r"function clientSaid\(q\)\{.*?Use their answer</button></div>`\}", tmpl, _re.S)
check("the page's esc and clientSaid can be lifted for node", bool(_esc and _cs))
if _esc and _cs:
    # A value stored before the server half existed, drawn by the page's
    # own function. `esc` turns a quote into &#39;, which an HTML attribute
    # decodes back into a quote before the JS engine reads it -- so a
    # client value inside a JS string in an onclick is a client value that
    # can close the string. It rides on a data attribute now, where esc is
    # the right escape.
    script = (_esc.group(0) + "\n" + _cs.group(0) + "\n"
              + "const q={key:'launch_date',client_proposed:{value:" + _json.dumps(PAYLOAD)
              + ",by:'Pat',at:'2026-09-19',taken:false}};"
              + "const html=clientSaid(q);const m=/onclick=\"([^\"]*)\"/.exec(html);"
              + "console.log(JSON.stringify({onclick:m?m[1]:'',html:html}));")
    got = _sp.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    check("node ran the page's own function", got.returncode, 0)
    drawn = _json.loads(got.stdout.strip() or "{}") if got.returncode == 0 else {}
    onclick = drawn.get("onclick", "")
    check("the press carries no client value inside a JS string", "alert" not in onclick and "&#39;" not in onclick)
    check("it reads the value off the button's own data attribute", "this.dataset" in onclick)
    check("where esc is the right escape", 'data-value="x&#39;);alert(document.cookie);//"' in drawn.get("html", ""))

# ---------------------------------------------------------------------------
section("A post on one worker cannot drop a decision made on the other")
# `plan_json` is one blob written by read-modify-write. The other worker is
# a second SQLAlchemy session on the same database: it writes a decision
# after this session has loaded the run and before the client's post is
# recorded, which is the interleaving two gunicorn workers produce.
from sqlalchemy.orm import Session as _Session                       # noqa: E402
with hub_app.app_context():
    stale = pe.get_run(RUN_ID)
    _ = stale.plan_json                                  # loaded here, and about to go stale
    with _Session(pe.db.engine) as other:
        row = other.get(pe.ProposalExecutionRun, RUN_ID)
        moved = pp.apply_decisions(pe._loads(row.plan_json, {}),
                                   {"add": [{"list": "launch", "title": "Decided on the other worker"}]})
        row.plan_json = pe._dumps(moved)
        other.commit()
    pe.record_client_answers(stale, {"reporting_cadence": "monthly"}, name="Pat Client")
    plan = _plan(RUN_ID)
    check("the other worker's item survived the client's post",
          any(it.get("title") == "Decided on the other worker" for it in plan["launch"]))
    check("and the client's answer landed", plan["client_answers"]["reporting_cadence"]["value"], "monthly")
    # And the other way round: a client answer written on the other worker
    # survives a staff press on this one.
    stale = pe.get_run(RUN_ID)
    _ = stale.plan_json
    with _Session(pe.db.engine) as other:
        row = other.get(pe.ProposalExecutionRun, RUN_ID)
        moved, _n = pp.record_client_answers(pe._loads(row.plan_json, {}),
                                             {"reporting_cadence": "quarterly"}, name="Other Worker")
        row.plan_json = pe._dumps(moved)
        other.commit()
    pe.update_plan(RUN_ID, {"answers": {"launch_date": "2026-11-05"}}, actor="rep")
    plan = _plan(RUN_ID)
    check("the client answer from the other worker survived the staff press",
          plan["client_answers"]["reporting_cadence"]["by"], "Other Worker")
    check("and the staff answer landed", plan["answers"]["launch_date"], "2026-11-05")
    check("the write path re-reads the row under its lock",
          "populate_existing().with_for_update()" in _read("hub", "proposal_execution.py"))

# ---------------------------------------------------------------------------
section("The link the client holds opens the plan that replaced the one it was minted for")
with hub_app.app_context():
    new_run, created = pe.create_run(CLIENT, "p1", owner=OWNER, actor="rep", force=True)
    NEW_ID = new_run.id
    check("re-analyzing superseded the run", (created, pe.get_run(RUN_ID).state), (True, pe.RUN_SUPERSEDED))
    check("the token was carried onto the new plan", (_plan(NEW_ID).get("client_link") or {}).get("token"), TOKEN)
    found, err = pe.run_for_client_token(TOKEN)
    check("and the link resolves to the live run, not the superseded one",
          (found.id if found else None, err), (NEW_ID, ""))
r = anon.get(URL)
check("the client's page still opens at the address they were sent", r.status_code, 200)
r = anon.post(URL, data={"name": "Pat Client", "a:reporting_cadence": "weekly"})
check("and a post lands", r.status_code, 303)
with hub_app.app_context():
    check("on the live run", _plan(NEW_ID)["client_answers"]["reporting_cadence"]["by"], "Pat Client")
    # A link minted before the token was carried: the superseded run holds
    # it and the live one does not. It is followed to the live run.
    plan = _plan(NEW_ID)
    plan.pop("client_link", None)
    run = pe.get_run(NEW_ID)
    run.plan_json = pe._dumps(plan)
    pe.db.session.commit()
    found, _err = pe.run_for_client_token(TOKEN)
    check("a token only the superseded run holds is followed to the live run", found.id if found else None, NEW_ID)
    check("while the live run says it has no link of its own", pe.client_link_view(run), {})
    # Revoking on the newest holder takes every older copy with it.
    plan["client_link"] = dict(_plan(RUN_ID)["client_link"])
    run.plan_json = pe._dumps(plan)
    pe.db.session.commit()
    pe.revoke_client_link(NEW_ID, actor="rep")
    check("revoking on the live run takes the older copy with it -- the newest holder decides",
          pe.run_for_client_token(TOKEN), (None, ""))
    check("even though the superseded run's own copy still reads live",
          bool(_plan(RUN_ID)["client_link"].get("revoked_at")), False)
r = anon.get(URL)
check("so the address is 404", r.status_code, 404)
with hub_app.app_context():
    # A chain that ends nowhere live is the same 404, never a stale plan.
    plan = _plan(NEW_ID)
    plan["client_link"].pop("revoked_at", None)
    plan["client_link"].pop("revoked_by", None)
    run = pe.get_run(NEW_ID)
    run.plan_json = pe._dumps(plan)
    run.state = pe.RUN_SUPERSEDED
    pe.db.session.commit()
    check("a chain that ends nowhere live is 404", pe.run_for_client_token(TOKEN), (None, ""))
    run.state = pe.RUN_DRAFT
    pe.db.session.commit()
    check("and live again once the run is", (pe.run_for_client_token(TOKEN)[0] or run).id, NEW_ID)

# ---------------------------------------------------------------------------
section("A script holding the link cannot post for ever")
import hub.leads as leads_mod                                        # noqa: E402
from hub import proposal_execution_routes as routes                  # noqa: E402
leads_mod._hits.clear()
check("the ceiling is above what a person does and below what a script would",
      3 < routes.CLIENT_POST_LIMIT <= 100 and routes.CLIENT_POST_IP_LIMIT >= routes.CLIENT_POST_LIMIT)
codes = {anon.post(URL, data={"name": "Pat Client", "a:reporting_cadence": "weekly"}).status_code
         for _ in range(routes.CLIENT_POST_LIMIT)}
check("every post inside the allowance lands", codes, {303})
with hub_app.app_context():
    before_limit = dict(_plan(NEW_ID)["client_answers"]["reporting_cadence"])
r = anon.post(URL, data={"name": "Pat Client", "a:reporting_cadence": "monthly"})
check("the next is refused", r.status_code, 429)
check("in words, naming the way round it", "Smart 1 contact" in r.get_data(as_text=True))
with hub_app.app_context():
    check("and recorded nothing", _plan(NEW_ID)["client_answers"]["reporting_cadence"], before_limit)
r = anon.get(URL)
check("reading the page is not limited", r.status_code, 200)
leads_mod._hits.clear()
_rl = leads_mod.rate_limited
leads_mod.rate_limited = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no limiter"))
try:
    r = anon.post(URL, data={"name": "Pat Client", "a:reporting_cadence": "monthly"})
    check("a limiter that cannot answer costs nothing but itself", r.status_code, 303)
finally:
    leads_mod.rate_limited = _rl

# ---------------------------------------------------------------------------
section("Both halves of public, and the help behind the new copy")
src = _read("hub", "proposal_execution_routes.py")
check("the POST route sits under the guard's public prefix",
      '@bp.post("/proposal-execution/needs/<token>")' in src and 'public=("/needs/",)' in src)
check("the chrome exemption covers it", '"/proposal-execution/needs/",' in _read("hub", "__init__.py"))
guards = _read("test_blueprint_guards.py")
check("the write sweep names it with its reason",
      '"/proposal-execution/needs/<token>": "the client answering' in guards)
tmpl = _read("hub", "templates", "proposal_execution.html")
for key in ("proposal_execution.overview.autostart", "proposal_execution.plan.questions"):
    check(f"{key} is registered", hub_help.get(key) is not None)
check("the plan page places the autostart bubble", "proposal_execution.overview.autostart" in tmpl)
check("the questions help says what a client answer is",
      "Use their answer" in getattr(hub_help.get("proposal_execution.plan.questions"), "body", ""))
check("the client link help says they can answer",
      "answer those questions" in getattr(hub_help.get("proposal_execution.plan.client_link"), "body", ""))
check("the rep's press is on the page", "useClientAnswer(" in tmpl and "Use their answer" in tmpl)
check("the summary line counts them", "client_answers_pending" in tmpl)
check("Client 360 lists them as to-do", "client_answers_pending" in _read("hub", "templates", "client360.html"))
check("the kickoff document prints them", "client_says" in _read("hub", "templates", "proposal_kickoff.html"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
