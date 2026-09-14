"""An owner on every plan item, the kickoff document, and the client's page.

    python3 test_proposal_kickoff.py

Same shape as the other module tests here: no pytest, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

The plan on `/proposal-execution` said what had to happen and when, and
nothing said **whose** it was, and the day the proposal was signed the team
still had no page to print for the kickoff call and the client had nothing
to read but the rep's email. Three things close that, and each has a way of
being confidently wrong:

* **An owner on every item, following the client's owner.** The default is
  laid over on read from `hub/client_owner.py`, so a handover of the client
  moves every item that was following them; an owner named on one item is
  the only thing stored (`owner_override`), and the derived `owner` never
  reaches the column. An account the Hub does not know is refused by name.
* **The kickoff document is built from the kept plan** and counts what has
  not been reviewed rather than leaving it off quietly -- a document that
  gets shorter with nothing saying so is the failure a kickoff list exists
  to prevent.
* **The client's page is public, read-only, and carries only the fields.**
  A random token stored on the plan, never derived; revoked, unknown and
  malformed answer the same 404; no dropped item, no item Smart 1 is
  producing, no internal note and no staff email reaches it; and it is
  outside both the login (the blueprint guard) and the chrome (CHROMELESS),
  because a client-facing hub route needs both halves.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-kickoff-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "kickoff-test")
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
import hub.client_owner as client_owner                              # noqa: E402
import hub.proposals as proposals_mod                                # noqa: E402
import hub.auth as auth                                              # noqa: E402
import hub.help as hub_help                                          # noqa: E402
import hub.radio_share as radio_share                                # noqa: E402
from werkzeug.test import Client as WSGIClient                       # noqa: E402

hub_app = wsgi.hub_app
# The package itself, for the proposal-text hook the run reads at analysis
# time -- through sys.modules rather than a second import of a module this
# file already imports by its submodules.
hub_pkg = sys.modules["hub"]


def _read(*parts):
    """A repo file as text -- opened and closed here, so no check leaves a handle open."""
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


CLIENT = "Acme Tyre"
OWNER = "erik@smart1marketing.com"
OTHER = "todd@smart1marketing.com"
TEXT = ("Retargeting display $600\nSocial Posting Outline $125\n"
        "Enhanced SEO + AI Optimization $1,400\nMonthly YouTube Sales Video $100")
_FIX = {"p1": {"id": "p1", "title": "Acme Tyre plan", "filename": "plan.pdf", "kind": "file"}}
proposals_mod.list_proposals = lambda client: list(_FIX.values()) if client == CLIENT else []
hub_pkg._proposal_text_for = lambda client, ident: TEXT


def _plan(run_id):
    return pe.plan_for(pe.get_run(run_id))


def _served(run_id):
    return pe.get_run(run_id).as_dict(full=True)["plan"]


def _items(plan):
    return [it for name in pp.LISTS for it in plan.get(name) or []]


# ---------------------------------------------------------------------------
section("The default owner is the client's, laid over on read")
with hub_app.app_context():
    check("the client starts with nobody", client_owner.owner_of(CLIENT), None)
    run, _created = pe.create_run(CLIENT, "p1", owner="rep@smart1marketing.com", actor="rep")
    RUN_ID = run.id
    plan = _plan(RUN_ID)
    ids = [it["id"] for it in _items(plan)]
    check("a plan with an unowned client carries no owner on any item",
          all(not it.get("owner") for it in _items(_served(RUN_ID))))
    served = _served(RUN_ID)
    check("...and the account list is served beside it for the picker",
          len(served["owners"]["choices"]) > 5 and all(c.get("email") and c.get("name") for c in served["owners"]["choices"]))
    check("...with no owner on the resolved block", served["resolved"]["owner"], {})

    got = client_owner.assign(CLIENT, OWNER, actor="t")
    check("assigning the client an owner on My Clients", got.get("ok") is True)
    served = _served(RUN_ID)
    check("puts that owner on every item, marked as following the client",
          {(it.get("owner"), it.get("owner_source")) for it in _items(served)}, {(OWNER, "client")})
    check("...with the account's name rather than the address",
          served["launch"][0]["owner_label"] != OWNER and "@" not in served["launch"][0]["owner_label"])
    check("...and the resolved block names them",
          served["resolved"]["owner"].get("email") == OWNER and served["resolved"]["owner"].get("source") == "direct")
    check("the stored plan carries no owner at all -- it is derived",
          not any(k in it for it in _items(_plan(RUN_ID)) for k in ("owner", "owner_label", "owner_source")))

# ---------------------------------------------------------------------------
section("An owner named on the item, and every way that is refused")
with hub_app.app_context():
    first, second = ids[0], ids[1]
    pe.update_plan(RUN_ID, {"owners": {first: OTHER}}, actor="rep")
    stored = {it["id"]: it for it in _items(_plan(RUN_ID))}
    check("naming an owner on one item stores owner_override and nothing else",
          stored[first].get("owner_override") == OTHER and "owner" not in stored[first])
    served = {it["id"]: it for it in _items(_served(RUN_ID))}
    check("...and the served item says it was set on the item",
          (served[first]["owner"], served[first]["owner_source"]), (OTHER, "item"))
    check("...while its neighbor still follows the client",
          (served[second]["owner"], served[second]["owner_source"]), (OWNER, "client"))
    check("the run's own history says an owner was set",
          any("1 owner(s) set" in e["message"] for e in pe.events_for_run(RUN_ID, 3)))

    client_owner.assign(CLIENT, OTHER, actor="t")
    served = {it["id"]: it for it in _items(_served(RUN_ID))}
    check("a handover of the client moves the items that were following them",
          served[second]["owner"], OTHER)
    client_owner.assign(CLIENT, OWNER, actor="t")
    served = {it["id"]: it for it in _items(_served(RUN_ID))}
    check("...and leaves the one named by hand where it was",
          (served[first]["owner"], served[first]["owner_source"]), (OTHER, "item"))

    pe.update_plan(RUN_ID, {"owners": {first: ""}}, actor="rep")
    served = {it["id"]: it for it in _items(_served(RUN_ID))}
    check("a blank follows the client's owner again",
          (served[first]["owner"], served[first]["owner_source"]), (OWNER, "client"))
    check("...and the override is gone from the column",
          "owner_override" not in {it["id"]: it for it in _items(_plan(RUN_ID))}[first])

    for label, decisions, words in (
            ("an item the plan does not have", {"owners": {"launch:all:nothing": OTHER}}, "No plan item"),
            ("a value that is not an address", {"owners": {first: "erik"}}, "not an email address"),
            ("an account the Hub does not know", {"owners": {first: "nobody@else.example"}}, "not a Hub account"),
            ("a shape that is not a map", {"owners": [first]}, "owners must map")):
        try:
            pe.update_plan(RUN_ID, decisions, actor="rep")
            got = "accepted"
        except ValueError as exc:
            got = str(exc)
        check(f"{label} is refused by name", words in got, True)
    check("...and a refused press leaves the plan exactly as it was",
          "owner_override" not in {it["id"]: it for it in _items(_plan(RUN_ID))}[first])

    # The table could not be read: a well-formed address is taken as typed
    # rather than every assignment refused over a blip.
    loose = pp.apply_decisions(_plan(RUN_ID), {"owners": {first: "new.hire@smart1marketing.com"}}, known_owners=None)
    check("with no account list to check against, a well-formed address is kept",
          {it["id"]: it for it in _items(loose)}[first].get("owner_override"), "new.hire@smart1marketing.com")
    check("...and the label falls back to the address rather than inventing a name",
          pp.owner_label("new.hire@smart1marketing.com", {}), "new.hire@smart1marketing.com")

    # Superseding carries the override with the verdict.
    old = pp.apply_decisions(_plan(RUN_ID), {"owners": {second: OTHER}, "accept": {second: True}})
    fresh = pp.build_plan(pe.get_run(RUN_ID).analysis(), TEXT, CLIENT, use_ai=False)
    carried = {it["id"]: it for it in _items(pp.carry_forward(fresh, old))}
    check("an owner named on an item survives a re-analysis with the item",
          (carried[second].get("owner_override"), carried[second].get("accepted")), (OTHER, True))

    # The briefs read the owner too.
    pe.update_plan(RUN_ID, {"accept": {i: True for i in ids}, "answers": {"launch_date": "2026-11-02"},
                            "owners": {first: OTHER}}, actor="rep")
    task = next(t for t in pe.tasks_for_run(RUN_ID) if t.adapter == "launch_packet")
    kept = pe._kept_plan_for(pe.get_run(RUN_ID), task)
    check("the kept-plan lines a brief or a packet reads carry the owner",
          any("owner: " in line for name in pp.LISTS for line in kept.get(name) or []))

# ---------------------------------------------------------------------------
section("The kickoff document: built from the kept plan, counting what is not")
with hub_app.app_context():
    plan = _plan(RUN_ID)
    creative_ids = [it["id"] for it in plan["creative"]]
    supply_keys = [q["key"] for q in plan["questions"] if q["key"].startswith("creative_supply:")]
    check("the fixture has a creative supply question to answer", bool(supply_keys))
    # One channel's files come from the client, one item is dropped, one is
    # left unreviewed -- the three states a document has to tell apart.
    dropped_id = creative_ids[-1]
    fresh_row = {"list": "launch", "title": "Confirm the dealer's showroom hours for the ad copy"}
    pe.update_plan(RUN_ID, {"answers": {supply_keys[0]: "client"}, "accept": {dropped_id: False},
                            "add": [fresh_row]}, actor="rep")
    added = next(it for it in _plan(RUN_ID)["launch"] if it["title"] == fresh_row["title"])
    pe.update_plan(RUN_ID, {"accept": {added["id"]: None}}, actor="rep")

    doc = pe.kickoff_document(pe.get_run(RUN_ID), base="http://hub.test/")
    check("the document names the client, the proposal and the launch date",
          (doc["client"], doc["proposal"], bool(doc["launch"]["label"])), (CLIENT, "Acme Tyre plan", True))
    check("...and the client's owner", doc["owner"].get("email"), OWNER)
    check("a dropped item is left off", all(it["id"] != dropped_id for it in doc["creative"]))
    check("...and counted", doc["dropped"] >= 1)
    check("an item nobody has reviewed is left off", all(it["id"] != added["id"] for it in doc["launch_tasks"]))
    check("...and counted rather than silently absent", doc["to_review"], 1)
    check("every item on it carries an owner label", all(it.get("owner_label") for k in ("creative", "launch_tasks", "monthly") for it in doc[k]))
    check("...and the one set by hand carries the name set on it",
          next((it.get("owner_label") for it in doc["creative"] + doc["launch_tasks"] + doc["monthly"] if it["id"] == first), None) == pp.owner_label(OTHER, {c["email"]: c["name"] for c in _served(RUN_ID)["owners"]["choices"]}))
    leads = [int(it.get("lead_days") or 0) for it in doc["launch_tasks"]]
    check("launch tasks are in the order they fall due", leads, sorted(leads, reverse=True))
    check("...each with a due date measured from the launch", all(it.get("due_label") for it in doc["launch_tasks"]))
    check("a channel whose creative the client supplies says so",
          any(ch["supply_label"] == pp.SUPPLY_LABELS["client"] for ch in doc["channels"]))
    check("the monthly promises name their kind", all(it.get("kind_label") for it in doc["monthly"]))
    check("the open questions are the unanswered ones only",
          doc["questions"] and all("launch" not in q["question"].lower() for q in doc["questions"]))
    check("no client link yet reads as none rather than an empty address", doc["client_link"], {})

    staff = WSGIClient(wsgi.application)
    staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"), domain="localhost")
    resp = staff.get(f"/proposal-execution/run/{RUN_ID}/kickoff")
    body = resp.get_data(as_text=True)
    check("the kickoff page serves to staff", resp.status_code, 200)
    check("...naming the client and the sections a kickoff call walks",
          all(s in body for s in (CLIENT, "Creative that has to exist", "Before launch", "Every month", "Still open")))
    check("...with the unreviewed count at the top rather than the item quietly missing",
          "not been reviewed" in body and fresh_row["title"] not in body)
    check("...and a print button", "window.print()" in body)
    check("the print rules hide the Hub's injected chrome", ".s1hub-sb" in body and "@media print" in body)
    anon = WSGIClient(wsgi.application)
    resp = anon.get(f"/proposal-execution/run/{RUN_ID}/kickoff")
    check("a stranger is sent to the login", resp.status_code == 302 and "/login" in resp.headers.get("Location", ""))
    resp = staff.get("/proposal-execution/run/999999/kickoff")
    check("a run that does not exist is a 404 that says so", resp.status_code == 404 and "No such execution run" in resp.get_data(as_text=True))

# ---------------------------------------------------------------------------
section("The client's page: a stored token, the fields only, one 404 for every way it is gone")
with hub_app.app_context():
    check("no link exists until somebody presses", pe.client_link_view(pe.get_run(RUN_ID)), {})
    try:
        pe.revoke_client_link(RUN_ID, actor="rep")
        got = "revoked"
    except ValueError as exc:
        got = str(exc)
    check("revoking one that was never made is refused by name", "No client link" in got)

    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/client-link")
    d = resp.get_json() or {}
    check("the press creates the link", resp.status_code == 200 and d.get("ok") and d["link"]["created"] is True)
    URL = d["link"]["path"]
    TOKEN = URL.rsplit("/", 1)[-1]
    check("...at a random token, never derived", radio_share.is_token(TOKEN))
    check("...built on the host that served the page", d["link"]["url"].startswith("http://localhost/"))
    stored_link = _plan(RUN_ID).get("client_link") or {}
    check("the token is stored on the plan with who made it and when",
          stored_link.get("token") == TOKEN and stored_link.get("created_at") and "created_by" in stored_link)
    check("...and the address is not", "url" not in stored_link and "path" not in stored_link)
    check("the served plan carries the link for the toolbar", d["run"]["plan"]["client_link"].get("url", "").endswith(URL))
    resp2 = staff.post(f"/api/proposal-execution/run/{RUN_ID}/client-link")
    d2 = resp2.get_json() or {}
    check("a second press hands back the live link rather than a second one",
          d2["link"]["created"] is False and d2["link"]["path"] == URL)

    anon = WSGIClient(wsgi.application)
    resp = anon.get(URL)
    page = resp.get_data(as_text=True)
    check("a client with no login opens it", resp.status_code, 200)
    check("...and it says whose it is and what it is for",
          f"{CLIENT}: what we need from you" in page)
    needs = pe.client_needs(pe.get_run(RUN_ID))
    resolved_plan = pe._resolved_plan(pe.get_run(RUN_ID))
    client_files = [it for it in pp.kept_items(resolved_plan, "creative")
                    if it.get("supplier") == "client" and it.get("kind") != "copy"]
    check("the files the client agreed to supply are on it, with the date each is wanted by",
          bool(client_files) and all(f["title"] in page for f in needs["files"]) and all(f.get("due_label") for f in needs["files"]))
    check("...and only those: nothing Smart 1 is producing",
          len(needs["files"]) == len(client_files) and all(f["title"] in {c["title"] for c in client_files} for f in needs["files"]))
    dropped_title = next(it["title"] for it in _plan(RUN_ID)["creative"] if it["id"] == dropped_id)
    shown = {c["title"] for c in client_files}
    ours = [it["title"] for it in resolved_plan["creative"]
            if it.get("accepted") is True and it.get("supplier") != "client" and it["title"] not in shown]
    check("the fixture's dropped item is not also a title the client supplies", dropped_title not in shown)
    check("a dropped item does not reach the client", dropped_title not in page)
    check("...nor an item Smart 1 is producing", bool(ours) and all(t not in page for t in ours))
    check("the questions on it are the client's to answer, in their words",
          needs["questions"] and all(q["key"] == "reporting_cadence" or q["key"].startswith(("launch_date", "creative_supply:")) for q in needs["questions"]))
    check("...and never a budget or a question about our own recipes",
          all(not q["key"].startswith(("budget:", "creative_for:", "ai:")) for q in needs["questions"]) and "$" not in page)
    check("the Smart 1 contact is the client's owner, by name and never by email",
          needs["contact"] == pp.owner_label(OWNER, {c["email"]: c["name"] for c in _served(RUN_ID)["owners"]["choices"]})
          and OWNER not in page and OTHER not in page and "smart1marketing.com" not in page)
    check("nothing internal reaches it", all(s not in page for s in ("found by AI", "to review", "Built from the stored analysis", "owner_override", "rep@")))
    check("the Hub's chrome is not injected into it", "s1hub-sb" not in page and "hub-help" not in page and "hub-crumbs" not in page)
    check("...and it asks not to be indexed", 'name="robots"' in page)
    check("a print button, because a client prints it", "window.print()" in page)

    gone = anon.get("/proposal-execution/needs/" + "x" * 32)
    bad = anon.get("/proposal-execution/needs/not-a-token")
    check("an unknown token is a 404 that says the link is not available",
          gone.status_code == 404 and "no longer available" in gone.get_data(as_text=True))
    check("a malformed one answers identically", (bad.status_code, bad.get_data()), (gone.status_code, gone.get_data()))

    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/client-link/revoke")
    dr = resp.get_json() or {}
    check("revoking answers with the revoked state and no address",
          resp.status_code == 200 and dr["link"]["revoked"] is True and dr["link"]["url"] == "")
    revoked = anon.get(URL)
    check("...and the address the client was sent answers the same 404",
          (revoked.status_code, revoked.get_data()), (gone.status_code, gone.get_data()))
    check("the token stays on the plan as the record one was sent",
          (_plan(RUN_ID).get("client_link") or {}).get("token") == TOKEN and (_plan(RUN_ID)["client_link"].get("revoked_at") or "") != "")
    check("the run's history says so", any("Revoked the client" in e["message"] for e in pe.events_for_run(RUN_ID, 3)))
    resp = staff.post(f"/api/proposal-execution/run/{RUN_ID}/client-link")
    d3 = resp.get_json() or {}
    check("a new press after a revoke mints a different token",
          d3["link"]["created"] is True and d3["link"]["path"] != URL)
    check("...and the old address stays dead", anon.get(URL).status_code, 404)
    doc = pe.kickoff_document(pe.get_run(RUN_ID), base="http://hub.test/")
    check("the kickoff prints the live client link on the host it was asked for",
          doc["client_link"]["url"].startswith("http://hub.test/proposal-execution/needs/"))

    # The store would not answer: a 503, never the 404 a client reads as expired.
    class _Refuses:
        class query:                                     # noqa: N801
            @staticmethod
            def filter(*_a, **_k):
                raise RuntimeError("db down")
        plan_json = pe.ProposalExecutionRun.plan_json
    real = pe.ProposalExecutionRun
    pe.ProposalExecutionRun = _Refuses
    try:
        got = pe.run_for_client_token(TOKEN)
        resp = anon.get(URL)
    finally:
        pe.ProposalExecutionRun = real
    check("a store that would not answer says so rather than answering 404",
          got[0] is None and "could not be read" in got[1] and resp.status_code == 503)
    check("  ...in words a client can act on", "try again" in resp.get_data(as_text=True))

# ---------------------------------------------------------------------------
section("Both halves of public, and the explanations behind the buttons")
guard_src = _read("hub", "proposal_execution_routes.py")
check("the client's page is exempt from the login on the blueprint guard",
      'install_guard(bp, mount="/proposal-execution", public=("/needs/",))' in guard_src)
init_src = _read("hub", "__init__.py")
check("...and from the chrome, in CHROMELESS", '"/proposal-execution/needs/",' in init_src)
guards = _read("test_blueprint_guards.py")
check("...and named with its reason in the anonymous sweep's allowlist",
      '"/proposal-execution/needs/<token>":' in guards)
check("the staff kickoff is under the guarded mount and named nowhere public",
      "/proposal-execution/run/<int:run_id>/kickoff" not in guards and "kickoff" not in init_src)

tpl = _read("hub", "templates", "proposal_execution.html")
keys = ("proposal_execution.plan.owners", "proposal_execution.plan.kickoff", "proposal_execution.plan.client_link")
registered = hub_help.as_json()["help"]
check("the three new bubbles are registered", all(k in registered for k in keys))
check("...and placed on the plan page, guarded like every other helper call",
      all(f"help_dot('{k}') if help_dot is defined else ''" in tpl for k in keys))
check("the picker on each item is drawn from the served account list, never a typed name",
      "p.owners||{}).choices" in tpl and "<select class=\"pex-owner" in tpl and "pex-owner\" type=\"text\"" not in tpl)
check("...and which of the two an item is on is the server's answer",
      "it.owner_source==='item'" in tpl and "owner_source" in _read("hub", "proposal_plan.py"))
needs_tpl = _read("hub", "templates", "proposal_needs.html")
check("the client's template places no staff help and no staff nav",
      "help_dot" not in needs_tpl and "data-help" not in needs_tpl and "sidebar" not in needs_tpl)
check("client_questions knows exactly the three kinds that are the client's to answer",
      sorted(pp.CLIENT_QUESTION_WORDING), ["creative_supply", "launch_date", "reporting_cadence"])
wf = _read(".github", "workflows", "checks.yml")
check("CI runs this file", "python3 test_proposal_kickoff.py" in wf)

# ---------------------------------------------------------------------------
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
