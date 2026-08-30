"""Assigning a client to a person, and the report that puts on their desk.

    python3 test_client_owners.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

Every report in this Hub answered a question about the whole book. Nothing
recorded whose desk a client was on, so "what needs doing on my clients" was
six screens somebody had to open one at a time and read several hundred rows
of. `hub/client_owner.py` records the owner and `hub/client_health.py` reads
what is outstanding. Every assertion below is a way one of those two goes
quietly wrong — a screen that still looks healthy while the answer is
nonsense:

  1. **A client has one owner, and reassigning says who held it before.** Two
     owners makes "whose client is this?" unanswerable, which is the whole
     question the record exists to answer.

  2. **Exact or not at all.** "Riverside HVAC" must never collect "Riverside
     HVAC Supply" — the `hub/client_key.py` rule, and here it would put one
     company's book on another rep's screen and read as a clean assignment.

  3. **A bulk assignment reports every row's own outcome**, and writes the
     file once rather than once per client: a partner here carries eighty-seven
     of them and `jsonstore` mirrors every write into the database.

  4. **Partner assignment is a selection, not a stored rule.** Nothing on disk
     may say "everything of this partner belongs to X" — a rule would silently
     claim next year's clients for somebody who may have left.

  5. **An owner whose account is gone is named, never read as unassigned.**

  6. **Marks and owners are applied on read**, so an assignment made in one
     gunicorn worker is honoured by the other rather than waiting for a cache.

  7. **A Done mark is about the issue as it stood**, so a changed issue
     reports the mark superseded instead of standing over something nobody
     has read.

  8. **A source that could not be read is named**, never counted as nothing —
     a client with a hole in their row must not read as a client with nothing
     outstanding.

  9. **Every route is behind the login.** These name every client, what is
     wrong with each and who owns them.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1owner_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "owner-test-secret-key"
os.environ["PANEL_PASSWORD"] = "owner-test-password"

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(name):
    print(f"\n{name}")


from hub import auth, client_health, client_owner, create_hub_app   # noqa: E402

# One app, one application context, held for the whole file. `assignable_users`
# reads the account table through Flask-SQLAlchemy, which needs one -- and
# calling it without one is not a hypothetical: it is what a scheduled job
# would do, and the fallback below is what answers there.
APP = create_hub_app()
_CTX = APP.app_context()
_CTX.push()


# ---------------------------------------------------------------------------
section("One owner per client, and a handover that says who held it")
# ---------------------------------------------------------------------------

check("an unassigned client has no owner",
      client_owner.owner_of("Riverside HVAC"), None)

r = client_owner.assign("Riverside HVAC", "aimee@smart1marketing.com",
                        actor="Tester")
check("assigning answers ok", r["ok"], True)
check("and is not a reassignment", r["reassigned"], False)
check("the owner reads back",
      client_owner.owner_of("Riverside HVAC")["email"],
      "aimee@smart1marketing.com")

r = client_owner.assign("Riverside HVAC", "erik@smart1marketing.com",
                        actor="Tester")
check("reassigning is allowed", r["ok"], True)
check("and names who held it", r["previous"], "aimee@smart1marketing.com")
check("one row per client, never two",
      len([x for x in client_owner.assignments()
           if x["client"] == "Riverside HVAC"]), 1)
check("the newest decision is the one that stands",
      client_owner.owner_of("Riverside HVAC")["email"],
      "erik@smart1marketing.com")

# The rule this module would pay for hardest. A substring match here files one
# company's whole book under another rep.
check("a longer name is a different client",
      client_owner.owner_of("Riverside HVAC Supply"), None)
check("and punctuation and a legal suffix are not",
      client_owner.owner_of("The Riverside HVAC, LLC")["email"],
      "erik@smart1marketing.com")

check("an address with no @ is refused",
      client_owner.assign("Riverside HVAC", "erik")["ok"], False)
check("and refusing changes nothing",
      client_owner.owner_of("Riverside HVAC")["email"],
      "erik@smart1marketing.com")
check("a client with no name is refused",
      client_owner.assign("", "erik@smart1marketing.com")["ok"], False)

check("clients_for lists what somebody holds",
      client_owner.clients_for("erik@smart1marketing.com"), ["Riverside HVAC"])
check("and nothing for anybody else",
      client_owner.clients_for("aimee@smart1marketing.com"), [])

check("unassigning answers ok",
      client_owner.unassign("Riverside HVAC")["ok"], True)
check("a second press is not a failure",
      client_owner.unassign("Riverside HVAC")["already"], True)
check("and the client is free again",
      client_owner.owner_of("Riverside HVAC"), None)


# ---------------------------------------------------------------------------
section("A bulk assignment reports every row, and writes once")
# ---------------------------------------------------------------------------

many = ["Acme Plumbing", "Acme Roofing", "Acme Electric"]
r = client_owner.assign_many(many, "aimee@smart1marketing.com", actor="Tester")
check("all three land", r["assigned"], 3)
check("and each has its own row", len(r["results"]), 3)
check("in the order they were sent",
      [x["client"] for x in r["results"]], many)

# One number back would hide the two that failed.
r = client_owner.assign_many(["Good Co", "Other Co", ""], "nope", actor="Tester")
check("a bad address refuses every row", r["assigned"], 0)
check("and every row carries its own reason", len(r["results"]), 2)
check("naming it", sorted({x["error"] for x in r["results"]}),
      ["nope is not an email address."])
check("an unnamed row is dropped from the selection rather than written",
      r["skipped"], 1)

r = client_owner.assign_many(["Acme Plumbing", "acme plumbing ", "Acme Roofing"],
                             "erik@smart1marketing.com")
check("a name repeated in the selection is written once", r["assigned"], 2)
check("and the duplicate is counted as skipped", r["skipped"], 1)

r = client_owner.assign_many([], "erik@smart1marketing.com")
check("an empty selection is refused rather than silently doing nothing",
      (r["ok"], bool(r["error"])), (False, True))

r = client_owner.unassign_many(many)
check("clearing a selection reports each row", len(r["results"]), 3)
check("and clears them all", r["cleared"], 3)
check("leaving nobody holding anything", client_owner.owners(), {})


# ---------------------------------------------------------------------------
section("Partner assignment is a selection, never a stored rule")
# ---------------------------------------------------------------------------
#
# A stored rule would quietly claim next year's clients for whoever was on the
# screen this year -- including somebody who has since left -- with nothing on
# any record saying the assignment had been made by a rule nobody remembers.

import json                                                      # noqa: E402

client_owner.assign_many(["Moto Client A", "Moto Client B"],
                         "aimee@smart1marketing.com", actor="Tester")
raw = json.loads(Path(client_owner._path()).read_text())
check("what is stored is one row per client",
      sorted(x["client"] for x in raw["owners"]),
      ["Moto Client A", "Moto Client B"])
check("and nothing anywhere names a partner",
      any("partner" in json.dumps(x).lower() for x in raw["owners"]), False)
check("every row says who assigned it",
      sorted({x["by"] for x in raw["owners"]}), ["Tester"])
check("and when", all(len(x["at"]) > 10 for x in raw["owners"]), True)
client_owner.unassign_many(["Moto Client A", "Moto Client B"])


# ---------------------------------------------------------------------------
section("An owner whose account is gone is named, not read as unassigned")
# ---------------------------------------------------------------------------

book = [{"name": "Held By A Ghost"}, {"name": "Held By Nobody"}]
client_owner.assign("Held By A Ghost", "someone.who.left@smart1marketing.com")
_real_users = client_owner.assignable_users
client_owner.assignable_users = lambda: ([
    {"email": "aimee@smart1marketing.com", "name": "Aimee Tacey",
     "role": "member", "status": "active", "active": True, "source": "accounts"}
], "")
try:
    summary = client_owner.summary(book)
    row = [r for r in summary["rows"] if r["client"] == "Held By A Ghost"][0]
    check("the client still reads as owned", bool(row["email"]), True)
    check("but the account is named as unknown", row["known"], False)
    check("and the address is printed rather than a made-up name",
          row["owner"], "someone.who.left@smart1marketing.com")
    check("the summary counts it apart", summary["counts"]["unknown_owners"], 1)
    check("it is not counted as unassigned", summary["counts"]["unassigned"], 1)
finally:
    client_owner.assignable_users = _real_users
client_owner.unassign("Held By A Ghost")

# A registry that could not be read must not report every client as assigned.
_real_summary_source = client_owner.summary
check("a client book that could not be read is not measured",
      client_owner.summary([])["counts"]["clients"], 0)


# ---------------------------------------------------------------------------
section("assignable_users answers, and says which list answered")
# ---------------------------------------------------------------------------

users, err = client_owner.assignable_users()
check("somebody can be assigned to", bool(users), True)
check("with no error", err, "")
check("every row carries an email",
      all("@" in u["email"] for u in users), True)
check("and says which list it came from",
      sorted({u["source"] for u in users}), ["accounts"])

# "Nobody has an account" and "we could not read the accounts" are different
# answers, and only the second is somebody's to fix.
import hub.users as _users_mod                                   # noqa: E402
_real_query = _users_mod.User.query
try:
    class _Boom:
        def order_by(self, *a, **k):
            raise RuntimeError("the accounts table is gone")
    _users_mod.User.query = _Boom()
    users, err = client_owner.assignable_users()
    check("an unreadable account table falls back to the roster",
          bool(users), True)
    check("and says so", "roster" in err or "could not be read" in err, True)
    check("naming the fallback on every row",
          sorted({u["source"] for u in users}), ["roster"])
finally:
    _users_mod.User.query = _real_query


# ---------------------------------------------------------------------------
section("Issues: the kinds are a table, and every one is reachable")
# ---------------------------------------------------------------------------

check("every issue kind has a label and a screen it is fixed on",
      sorted(k for k, v in client_health.ISSUE_KINDS.items()
             if not (v.get("label") and v.get("where") and v.get("href"))), [])
# The whole point is that the work already has somewhere to happen.
check("and that screen is never this report",
      sorted(k for k, v in client_health.ISSUE_KINDS.items()
             if v["href"].startswith("/my-clients")), [])

issue = client_health._issue("assets_needed", "IO-1/PROD-2",
                             "Spring campaign", "Waiting on six banners.")
check("an issue's key names what it is about, never its position",
      issue["key"], "assets_needed:IO-1/PROD-2")
check("it carries the screen it is fixed on", issue["where"],
      "Campaign Assets Needed")
check("and a fingerprint of what it actually said",
      issue["fingerprint"],
      client_health.fingerprint("assets_needed", "IO-1/PROD-2",
                                "Waiting on six banners."))
check("a changed detail changes the fingerprint",
      client_health._issue("assets_needed", "IO-1/PROD-2", "Spring campaign",
                           "Waiting on nine banners.")["fingerprint"]
      != issue["fingerprint"], True)
check("a reworded label does not",
      client_health._issue("assets_needed", "IO-1/PROD-2", "A different title",
                           "Waiting on six banners.")["fingerprint"],
      issue["fingerprint"])


# ---------------------------------------------------------------------------
section("Marks and notes, applied on read")
# ---------------------------------------------------------------------------

FAKE = {
    "measured": True, "generated_at": "", "today": "2026-08-30",
    "sources": {"products": {"measured": True, "error": "", "note": ""},
                "registry": {"measured": True, "error": "", "note": ""}},
    "rows": [{
        "client": "Marked Co", "key": client_health._client_key("Marked Co"),
        "domain": "markedco.com", "url": "", "partner": "Moto",
        "other_partners": [], "sales": "", "live_products": 2, "monthly": 4000,
        "traffic": {"measured": False, "state": "never", "figures": [],
                    "note": "Nobody has audited this website yet."},
        "engagement": {"measured": True, "proposal_opens": 0,
                       "open_proposals": 0, "proof_rounds_out": 0,
                       "proof_rounds_answered": 0},
        "issues": [issue,
                   client_health._issue("no_dashboard", "live",
                                        "2 live products, no dashboard link",
                                        "Nothing points at a report.")],
        "c360": "/client360?q=Marked+Co",
    }],
    "renewal_days": 45, "stale_days": 60, "note": "",
}

_real_cached = client_health.cached
client_health.cached = lambda force=False: FAKE
try:
    out = client_health.report(scope="all")
    row = out["rows"][0]
    check("both issues start open", row["issue_count"], 2)
    check("and nothing is handled", row["handled_count"], 0)
    check("the client has no owner yet", row["owner"]["email"], "")

    # The overlay is read on every request, never baked into the cached run:
    # two gunicorn workers, and a mark folded into a cached payload is a
    # button that appears to do nothing to whichever worker did not take it.
    client_owner.assign("Marked Co", "aimee@smart1marketing.com", actor="Tester")
    out = client_health.report(scope="all")
    check("an assignment shows on the very next read without a rebuild",
          out["rows"][0]["owner"]["email"], "aimee@smart1marketing.com")

    r = client_health.set_mark("Marked Co", issue["key"], "done",
                               actor="Tester", seen=issue["fingerprint"])
    check("marking one done answers ok", r["ok"], True)
    out = client_health.report(scope="all")
    row = out["rows"][0]
    check("it leaves the open list", row["issue_count"], 1)
    check("and appears under handled rather than vanishing",
          row["handled_count"], 1)
    check("carrying who marked it", row["handled"][0]["mark"]["by"], "Tester")
    check("and what state it is in", row["handled"][0]["mark"]["state"], "done")
    check("it is not superseded", row["handled"][0]["mark"]["superseded"], False)

    # A Done mark is a statement about the issue as it stood.
    FAKE["rows"][0]["issues"][0] = client_health._issue(
        "assets_needed", "IO-1/PROD-2", "Spring campaign",
        "Waiting on nine banners.")
    out = client_health.report(scope="all")
    row = out["rows"][0]
    check("a changed issue is open again", row["issue_count"], 2)
    check("rather than staying handled", row["handled_count"], 0)
    marked = [i for i in row["issues"] if i.get("mark")][0]
    check("and it says it was superseded", marked["mark"]["superseded"], True)
    check("naming who had signed it off", marked["mark"]["by"], "Tester")

    check("putting it back is a state of its own",
          client_health.set_mark("Marked Co", issue["key"], "open")["ok"], True)
    check("and clears the mark",
          client_health.report(scope="all")["rows"][0]["handled_count"], 0)
    check("an unknown state is refused",
          client_health.set_mark("Marked Co", issue["key"], "maybe")["ok"],
          False)

    # Ignore and Done are different claims about the same row.
    check("ignoring is its own state",
          client_health.set_mark("Marked Co", issue["key"], "ignored",
                                 actor="Tester")["ok"], True)
    check("and reads back as ignored",
          client_health.report(scope="all")["rows"][0]["handled"][0]["mark"]["state"],
          "ignored")
    client_health.set_mark("Marked Co", issue["key"], "open")

    # Notes.
    check("an empty note is refused",
          client_health.add_note("Marked Co", "  ")["ok"], False)
    check("a note saves",
          client_health.add_note("Marked Co", "Called them Tuesday.",
                                 actor="Tester")["ok"], True)
    row = client_health.report(scope="all")["rows"][0]
    check("and reaches the row", row["note_count"], 1)
    check("with the words", row["notes"][0]["text"], "Called them Tuesday.")
    check("and whose it is", row["notes"][0]["by"], "Tester")
    check("removing it answers ok",
          client_health.delete_note(row["notes"][0]["id"])["ok"], True)
    check("and it is gone",
          client_health.report(scope="all")["rows"][0]["note_count"], 0)

    # Sorting and scoping.
    FAKE["rows"].append({
        **FAKE["rows"][0], "client": "Quiet Co",
        "key": client_health._client_key("Quiet Co"), "issues": [], "monthly": 0,
    })
    out = client_health.report(scope="all")
    check("the client with the most outstanding leads",
          [r["client"] for r in out["rows"]], ["Marked Co", "Quiet Co"])
    out = client_health.report(owner="aimee@smart1marketing.com", scope="mine")
    check("mine shows only what is assigned",
          [r["client"] for r in out["rows"]], ["Marked Co"])
    out = client_health.report(scope="unassigned")
    check("and nobody's shows the rest",
          [r["client"] for r in out["rows"]], ["Quiet Co"])
    out = client_health.report(owner="nobody@smart1marketing.com", scope="mine")
    check("a book with nothing in it says which kind of empty it is",
          out["empty_reason"], "no_clients")
    out = client_health.report(scope="all", q="quiet")
    check("the search filters per request", len(out["rows"]), 1)
    client_owner.unassign("Marked Co")
finally:
    client_health.cached = _real_cached


# ---------------------------------------------------------------------------
section("A source that could not be read is named, never counted as nothing")
# ---------------------------------------------------------------------------

BLIND = dict(FAKE)
BLIND["sources"] = {
    "products": {"measured": True, "error": "", "note": ""},
    "registry": {"measured": True, "error": "", "note": ""},
    "campaign_assets": {"measured": False, "error": "Knack refused", "note": ""},
}
BLIND["note"] = client_health._build_note(BLIND["rows"], BLIND["sources"])
check("the note names the blind source",
      "campaign assets" in BLIND["note"], True)
check("and says the rows are missing what it would have raised",
      "missing from every row" in BLIND["note"], True)

# A source that would not answer must raise nothing about the client. An
# unreadable scans table putting "never audited" on every row is a page of
# findings about our own reading, printed as findings about the clients.
_stub_sources = {}


def _no_audits(domains):
    return {}, "no scan table yet"


_real_audits = client_health._audits
_real_registry = client_health._registry
client_health._audits = _no_audits
try:
    built = client_health.build()
    kinds = {i["kind"] for r in built["rows"] for i in r["issues"]}
    check("an unreadable scans table raises no 'never audited'",
          "audit_never" in kinds, False)
    check("nor 'out of date'", "audit_stale" in kinds, False)
    check("and the source is named instead",
          built["sources"]["audits"]["measured"], False)
    states = {r["traffic"]["state"] for r in built["rows"] if r["domain"]}
    check("the traffic block says unreadable rather than never",
          states <= {"unreadable"}, True)
finally:
    client_health._audits = _real_audits

client_health._registry = lambda: ({}, "the client list is down")
try:
    built = client_health.build()
    kinds = {i["kind"] for r in built["rows"] for i in r["issues"]}
    check("an unreadable client list raises no 'no website on file'",
          "no_website" in kinds, False)
    check("and the run is not measured", built["measured"], False)
finally:
    client_health._registry = _real_registry

check("a run that could not read the products is not measured",
      client_health._build_note([], {"products": {"measured": False,
                                                  "error": "down"}}),
      "0 clients read. Not measured this run: products — anything those would "
      "have raised is missing from every row, not absent from it.")

# Every reader answers (answer, error): "nobody has one" and "we could not
# look" are different answers and only the first says anything about a client.
for name in ("_products", "_registry", "_asset_asks", "_creative",
             "_pipeline", "_proofs"):
    fn = getattr(client_health, name)
    check(f"{name} answers a pair", fn.__annotations__.get("return"),
          "tuple[dict, str]")


# ---------------------------------------------------------------------------
section("Traffic health says which kind of nothing it is")
# ---------------------------------------------------------------------------

t = client_health._traffic_block(None)
check("no audit is 'never', not zero", (t["measured"], t["state"]),
      (False, "never"))
check("and there are no figures to misread", t["figures"], [])

# The state this function exists for. An unreadable scans table hands every
# client an empty audit, and calling that "nobody has audited this website"
# accuses the whole book of something our own reading could not check.
t = client_health._traffic_block(None, readable=False)
check("a scans table that would not answer is not 'never audited'",
      t["state"], "unreadable")
check("and says so in words", "not measured" in t["note"], True)
t = client_health._traffic_block(None, has_domain=False)
check("and no domain at all is its own answer", t["state"], "no_domain")

t = client_health._traffic_block({
    "score": 61, "when": "2026-08-20", "public_id": "abc123",
    "age": {"age_days": 10, "stale": False, "note": "Read 10 days ago."},
    "traffic": {"organic_monthly": 1400, "keywords": None,
                "paid_monthly_visits": None, "mobile_speed": 48,
                "review_rating": None, "review_count": None,
                "broken_links": 0}})
labels = [f["label"] for f in t["figures"]]
check("a measured figure is printed",
      "Organic visits, monthly" in labels, True)
check("a check the plan did not run is left out rather than shown as zero",
      "Keywords ranked for" in labels, False)
check("but a real zero is kept", "Broken links" in labels, True)
check("a current reading says so", t["state"], "current")
check("and links to the audit", t["scan_url"], "/scans/scan/abc123")

t = client_health._traffic_block({
    "score": 61, "when": "2026-01-01", "public_id": "",
    "age": {"age_days": 240, "stale": True, "note": "over the 60-day mark"},
    "traffic": {}})
check("an old reading is named stale", t["state"], "stale")


# ---------------------------------------------------------------------------
section("Nothing answers a stranger, and nothing raises out of a route")
# ---------------------------------------------------------------------------

app = APP
anon = app.test_client()
for path in ("/my-clients", "/qa/client-owners"):
    check(f"{path} redirects a stranger to the login",
          anon.get(path).status_code, 302)
for path in ("/api/my-clients", "/api/my-clients/scoreboard",
             "/api/client-owners", "/api/client/owner?client=Acme"):
    check(f"{path} refuses a stranger", anon.get(path).status_code, 401)
for path in ("/api/my-clients/mark", "/api/my-clients/note",
             "/api/my-clients/note/delete", "/api/my-clients/refresh",
             "/api/client-owners/assign", "/api/client-owners/unassign",
             "/api/client/owner/set"):
    check(f"{path} refuses a stranger's write",
          anon.post(path, json={}).status_code, 401)

signed = app.test_client()
signed.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Tester"))
check("the report page renders", signed.get("/my-clients").status_code, 200)
check("the assignment page renders",
      signed.get("/qa/client-owners").status_code, 200)

body = signed.get("/api/client-owners").get_json()
check("the assignment API answers", body["measured"], True)
check("with somebody to assign to", bool(body["users"]), True)
check("and the partners to select by", isinstance(body["partners"], list), True)

# The shared-password session has no account behind it, so there is no "my"
# to answer -- "Shared login" is a true statement about the session and a
# useless one in a field whose whole value is whose it is.
body = signed.get("/api/my-clients").get_json()
check("a shared-password session is told it has no book",
      body["me"]["shared"], True)
check("and is shown everybody's instead", body["scope"], "all")

# A build that raises must reach the caller as a failure, not as an empty
# desk: a quiet page over a failed read is the one answer this must never
# give.
_real = client_health.report
client_health.report = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    out = signed.get("/api/my-clients").get_json()
    check("a report that raises answers not-measured", out["measured"], False)
    check("and says why", "boom" in out["error"], True)
    check("with no rows that could be read as a clear desk", out["rows"], [])
finally:
    client_health.report = _real

check("a mark with no client is refused",
      signed.post("/api/my-clients/mark",
                  json={"issue": "x", "state": "done"}).status_code, 400)
check("an assignment with no clients is refused",
      signed.post("/api/client-owners/assign",
                  json={"clients": [], "email": "a@b.com"}).status_code, 400)
r = signed.post("/api/client-owners/assign",
                json={"clients": ["A Co", "B Co"], "email": "nope"})
check("and a refused bulk write still carries every row's reason",
      len(r.get_json()["results"]), 2)


# ---------------------------------------------------------------------------
section("The tiles, the nav and the dashboard card exist")
# ---------------------------------------------------------------------------
#
# A tool with no tile is invisible -- this repository counts six that were.

from hub import qa as _qa                                         # noqa: E402

tiles = {key: meta for _group, key, meta in _qa.EXTRAS}
for key, href in (("my-clients", "/my-clients"),
                  ("client-owners", "/qa/client-owners")):
    check(f"{key} is tiled on /qa", tiles.get(key, {}).get("href"), href)

from hub.sidebar import _ITEMS                                    # noqa: E402
check("My Clients is in the nav",
      [i[1] for i in _ITEMS if i[0] == "myclients"], ["/my-clients"])

dash = (ROOT / "hub" / "templates" / "dashboard.html").read_text()
check("the dashboard card fetches the scoreboard",
      "/api/my-clients/scoreboard" in dash, True)
check("and says which kind of empty a nought is",
      "no_clients" in dash, True)

c360 = (ROOT / "hub" / "templates" / "client360.html").read_text()
check("Client 360 reads the owner from an embeddable path",
      "/api/client/owner?client=" in c360, True)

from hub.suite_embed import EMBEDDABLE                            # noqa: E402
check("which /api/client/ is on", "/api/client/" in EMBEDDABLE, True)

# A bubble whose key is not registered is removed client-side, so the template
# reads as helped and the screen shows nothing.
from hub.help import REGISTRY                                     # noqa: E402
keys = {h.key for h in REGISTRY}
for page in ("client_health.html", "client_owners.html"):
    src = (ROOT / "hub" / "templates" / page).read_text()
    import re
    placed = set(re.findall(r"help_dot\('([^']+)'\)", src))
    check(f"{page} places at least one bubble", bool(placed), True)
    check(f"and every key it places is registered", sorted(placed - keys), [])
    # Offering a tour that does not exist is the silence Smart 1 Ads shipped.
    check(f"{page} names no screen with no tour behind it",
          'data-screen' in src, False)


print(f"\n{_passed} passed, {_failed} failed")
_CTX.pop()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
