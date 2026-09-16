"""A client's email campaigns, read from their Suite sub-account through
hub/suite_email_stats.py.

    python3 test_suite_email_stats.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, through the COMPOSED app. Smart 1 Suite is never reached:
the wire is stood in for at `requests.get` where the call's own error
mapping is what is being asserted, and at `hub.suite_email_stats._call`
everywhere else; the sub-account and the consented scopes are stood in for
at the two readers every screen goes through.

What it holds:

  * the two scopes are requested, are HighLevel's own console names, and
    name this module; the nightly job is registered; the card has a
    bubble and the section a place on the page;
  * every way a call fails is named apart -- refused with the scope,
    missing, rate-limited, unreachable, an HTTP status -- and no message
    carries the token;
  * a campaign row is read tolerantly: statistics in a `stats` object or
    at the top level, a folder, a draft and another sub-account's row are
    dropped, and the raw statistics are kept beside the reading;
  * the per-campaign statistics endpoint is asked only for what the list
    carried none for, under a cap, and a 404 there is "not offered";
  * five kinds of nothing come back apart -- not linked, no scope, not
    read yet, could not read, no sent campaign -- and a missing scope is
    said in words before the first call;
  * one reading per client per day, capped; the 30/90-day totals are
    arithmetic on the stored rows and count only campaigns with counts;
  * the nightly sweep does not run while a scope is missing, reads each
    linked client once inside the window, skips one already read today,
    stops on a refusal, and names what a budget left unread;
  * every route refuses a stranger and answers staff, the GET never
    reaching the Suite;
  * the Client 360 card's renderer, lifted from the template and driven
    in node, draws each state apart;
  * the client's report page carries the section only for a client with
    a live email product AND a linked, read sub-account -- on the page,
    in data.json and in the PDF, with no staff note and no vendor word --
    and the staff page prints the gate.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1suiteemail_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
_reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "suite-email-test-secret"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ["GHL_CLIENT_ID"] = "test-app-id-abc123"
os.environ["GHL_CLIENT_SECRET"] = "test-secret"
os.environ.pop("GHL_OAUTH_SCOPES", None)
os.environ.pop("PANEL_PASSWORD", None)

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


import wsgi                                                          # noqa: E402
from werkzeug.test import Client                                     # noqa: E402
from hub import auth, ghl_scopes, jsonstore, scheduler, suite_accounts, suite_email_stats as se  # noqa: E402
from modules.reports import products, store                          # noqa: E402

NAME = "Schmidt's Sausage"
LOC = "locSchmidt0001abcd"
TOKEN = "tok-secret-value-never-shown"
TODAY = date(2026, 9, 16)


def entries():
    """This module's activity rows, oldest first, read through the module.

    The activity log's backend is the database now (hub/audit.py), so a test
    that opens AUDIT_LOG_PATH asserts about a fallback nothing writes to --
    a check that passes whatever the code does.
    """
    from hub import audit
    return [e for e in reversed(audit.read(limit=500))
            if e.get("module") == se.MODULE]


# The two readers every screen goes through, stood in for.
_ACCT = {"state": "connected", "location_id": LOC, "token": TOKEN, "detail": "", "connected": True,
         "matched_name": NAME}
_LINKED = {"state": "connected", "location_id": LOC, "detail": "", "connected": True}
_SCOPES = {"known": True, "missing": [], "connected": True, "detail": ""}
_real_token_for, _real_location_for = suite_accounts.token_for, suite_accounts.location_for
_real_scope_state = se.scope_state
suite_accounts.token_for = lambda name, url="": dict(_ACCT) if name == NAME else {"state": "not_connected", "location_id": "", "token": None, "detail": "No client on file records this Smart 1 Suite sub-account.", "connected": False}
suite_accounts.location_for = lambda name, url="": dict(_LINKED) if name == NAME else {"state": "not_connected", "location_id": "", "detail": "No client on file records this Smart 1 Suite sub-account.", "connected": False}
se.scope_state = lambda: dict(_SCOPES)


def _row(cid="camp01", name="September specials", status="complete", stats=None, top=False, **extra):
    r = {"_id": cid, "id": cid, "name": name, "status": status, "locationId": LOC, "campaignType": "regular",
         "childCount": 0, "deleted": False, "archived": False, "subject": f"{name}!",
         "sentAt": "2026-09-10T14:00:00.000Z", "createdAt": "2026-09-09T10:00:00.000Z", "updatedAt": "2026-09-10T14:05:00.000Z"}
    r.update(extra)
    if stats is not None:
        if top:
            r.update(stats)
        else:
            r["stats"] = stats
    return r


STATS = {"sent": 1200, "delivered": 1180, "opened": 400, "clicked": 61, "bounced": 20, "unsubscribed": 3, "revenue": 0}


# ---------------------------------------------------------------------------
section("The scopes, the job, the bubble and the page")
# ---------------------------------------------------------------------------

names = ghl_scopes.requested_names()
check("both email read scopes are requested", all(s in names for s in se.NEEDED_SCOPES))
check("...and are HighLevel's own console names", [s for s in se.NEEDED_SCOPES if not ghl_scopes.known(s)], [])
check("...each naming this module as its call site",
      all("hub/suite_email_stats.py" in (ghl_scopes.by_name(s) or ghl_scopes.Scope("", "", (), False)).needed_by for s in se.NEEDED_SCOPES))
check("...unverified until an agency owner re-consents", all(not ghl_scopes.by_name(s).verified for s in se.NEEDED_SCOPES))
check("the nightly job is registered on the hourly tick",
      "suite_email_snapshot" in scheduler.JOBS and scheduler.JOBS["suite_email_snapshot"][0] == 60)
from hub import help as hub_help                                     # noqa: E402
check("the card has a bubble", any(h.key == "hub.client360.suite_email" for h in hub_help.REGISTRY))
check("the sweep's hour is its own setting", se.REFRESH_HOUR_ENV, "SUITE_EMAIL_REFRESH_HOUR")


# ---------------------------------------------------------------------------
section("The wire: every way a call fails is named, and never carries the token")
# ---------------------------------------------------------------------------

import requests                                                      # noqa: E402


class _Resp:
    def __init__(self, status, body=None, text=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


_wire = {"resp": None, "calls": []}
_real_get = requests.get


def _fake_get(url, headers=None, params=None, timeout=None, **kw):
    _wire["calls"].append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
    r = _wire["resp"]
    if isinstance(r, Exception):
        raise r
    return r


requests.get = _fake_get
try:
    _wire["resp"] = _Resp(401, {"message": f"bad token {TOKEN}"})
    data, err = se._call(TOKEN, "/emails/schedule", params={"locationId": LOC}, scope_hint=se.SCOPE_LIST)
    check("401 is refused, naming the scope and the re-consent", (data, err["kind"], se.SCOPE_LIST in err["error"], "re-consent" in err["error"]), (None, "refused", True, True))
    check("...and never the body's token fragment", TOKEN not in err["error"])
    check("the call carried the bearer, the version and the location", (_wire["calls"][-1]["headers"]["Authorization"], _wire["calls"][-1]["headers"]["Version"], _wire["calls"][-1]["params"]["locationId"]), (f"Bearer {TOKEN}", se.API_VERSION, LOC))
    _wire["resp"] = _Resp(404, {"message": "not found"})
    check("404 is missing", se._call(TOKEN, "/emails/locations/x/campaigns/stats/email-campaigns/y")[1]["kind"], "missing")
    _wire["resp"] = _Resp(429, {})
    check("429 is rate-limited", se._call(TOKEN, "/emails/schedule")[1]["kind"], "rate_limited")
    _wire["resp"] = _Resp(500, {})
    check("500 is an HTTP status", se._call(TOKEN, "/emails/schedule")[1]["kind"], "http")
    _wire["resp"] = requests.ConnectionError("boom")
    check("a dropped connection is unreachable", se._call(TOKEN, "/emails/schedule")[1]["kind"], "unreachable")
    _wire["resp"] = _Resp(200, None, text="<html>")
    check("a non-JSON body is unreadable", se._call(TOKEN, "/emails/schedule")[1]["kind"], "unreadable")
    _wire["resp"] = _Resp(200, {"schedules": [], "total": []})
    check("a good answer comes back whole", se._call(TOKEN, "/emails/schedule")[0], {"schedules": [], "total": []})
    _wire["resp"] = _Resp(200, {"ok": True})
    se.campaign_stats(TOKEN, LOC, "camp01")
    check("the statistics endpoint is asked with Version v3 on its own path",
          (_wire["calls"][-1]["headers"]["Version"], _wire["calls"][-1]["url"].endswith(f"/emails/locations/{LOC}/campaigns/stats/email-campaigns/camp01")), ("v3", True))
    check("...and refuses an id it cannot put on a path", se.campaign_stats(TOKEN, LOC, "../x")[1]["kind"], "unreadable")
finally:
    requests.get = _real_get


# ---------------------------------------------------------------------------
section("Reading a row: tolerant of where the counts sit, strict about whose row it is")
# ---------------------------------------------------------------------------

c = se._campaign_row(_row(stats=STATS), LOC)
check("counts in a stats object are read", (c["delivered"], c["opened"], c["clicked"], c["has_stats"], c["stats_source"]), (1180, 400, 61, True, "list"))
check("...with the raw statistics kept beside them", c["raw_stats"], STATS)
c = se._campaign_row(_row(stats={"deliveredCount": 50, "openedCount": 10, "clickedCount": 2}, top=True), LOC)
check("counts at the top level under HighLevel's other spellings are read too", (c["delivered"], c["opened"], c["clicked"]), (50, 10, 2))
c = se._campaign_row(_row(), LOC)
check("a row with no counts is kept and says so", (c["has_stats"], c["delivered"], c["stats_source"]), (False, None, ""))
check("another sub-account's row is dropped", se._campaign_row(_row(locationId="locSomebodyElse00"), LOC), None)
check("a folder is dropped", se._campaign_row(_row(childCount=3, campaignType=""), LOC), None)
check("a deleted row is dropped", se._campaign_row(_row(deleted=True), LOC), None)
check("the subject, the send time and the type ride along",
      (se._campaign_row(_row(), LOC)["subject"], se._campaign_row(_row(), LOC)["sent_at"][:10]), ("September specials!", "2026-09-10"))
c = se._finish(se._campaign_row(_row(stats=STATS), LOC))
check("rates are over delivered, one decimal", (c["open_rate"], c["click_rate"]), (33.9, 5.2))
c = se._finish(se._campaign_row(_row(stats={"sent": 100, "opened": 25}), LOC))
check("...or over sent when delivered is not given", (c["open_rate"], c["click_rate"]), (25.0, None))


# ---------------------------------------------------------------------------
section("The read: the list, then statistics for what it lacked, under a cap")
# ---------------------------------------------------------------------------

_book = {"list": [], "stats": {}, "stats_kind": "", "list_kind": "", "asked": []}
_real_call = se._call


def _fake_call(token, path, *, params=None, version=se.API_VERSION, scope_hint=""):
    if path == "/emails/schedule":
        if _book["list_kind"]:
            return None, {"kind": _book["list_kind"], "error": f"Smart 1 Suite rejected the read (403). It needs the {scope_hint} scope."}
        off = int((params or {}).get("offset") or 0)
        return {"schedules": _book["list"][off:off + se.PAGE], "total": []}, {}
    cid = path.rsplit("/", 1)[-1]
    _book["asked"].append(cid)
    if _book["stats_kind"]:
        return None, {"kind": _book["stats_kind"], "error": "Smart 1 Suite has no stats here (404)."}
    st = _book["stats"].get(cid)
    return ({"stats": st}, {}) if st else (None, {"kind": "missing", "error": "no"})


se._call = _fake_call
_book["list"] = [_row("c1", "One", stats=STATS), _row("c2", "Two"), _row("c3", "Draft", status="draft"),
                 _row("c4", "Three", sentAt="2026-07-01T00:00:00Z"), _row("c5", "Other", locationId="locOther000000000")]
_book["stats"] = {"c2": {"delivered": 300, "opened": 90, "clicked": 9}}
res = se.read(TOKEN, LOC)
check("the read keeps the sent campaigns of this sub-account only, newest first",
      [c["id"] for c in res["campaigns"]], ["c1", "c2", "c4"])
check("...asked the statistics endpoint only for what the list lacked", _book["asked"], ["c2", "c4"])
byid = {c["id"]: c for c in res["campaigns"]}
check("...filled the one it answered, marked from stats", (byid["c2"]["delivered"], byid["c2"]["has_stats"], byid["c2"]["stats_source"], byid["c2"]["open_rate"]), (300, True, "stats", 30.0))
check("...left the one it did not without counts", (byid["c4"]["has_stats"], byid["c4"]["delivered"]), (False, None))
check("...and said the other sub-account's row was left out", any("left out" in n for n in res["notes"]))
_book["asked"], _book["stats_kind"] = [], "missing"
res = se.read(TOKEN, LOC)
check("a 404 from the statistics endpoint is 'not offered' and stops asking", (len(_book["asked"]), any("not offered" in n for n in res["notes"]), res["ok"]), (1, True, True))
_book["stats_kind"] = ""
_book["list"] = [_row(f"m{i}", f"M{i}") for i in range(se.STATS_CAP + 5)]
_book["stats"], _book["asked"] = {f"m{i}": {"delivered": 10} for i in range(se.STATS_CAP + 5)}, []
res = se.read(TOKEN, LOC)
check("the per-read cap holds, and what is left is named", (len(_book["asked"]), any("cap" in n for n in res["notes"])), (se.STATS_CAP, True))
_book["list_kind"] = "refused"
res = se.read(TOKEN, LOC)
check("a refused list is the read's answer, with the scope named", (res["ok"], res["kind"], se.SCOPE_LIST in res["error"]), (False, "refused", True))
_book["list_kind"] = ""


# ---------------------------------------------------------------------------
section("The reading: five kinds of nothing, said apart")
# ---------------------------------------------------------------------------

r = se.reading("Nobody Co", today=TODAY)
check("not linked says so and never a figure", (r["state"], r["measured"], r["campaigns"]), ("not_linked", False, []))
_SCOPES.update({"missing": [se.SCOPE_LIST], "detail": "The Hub app has not been consented with emails/schedule.readonly."})
r = se.reading(NAME, today=TODAY)
check("a missing scope is said in words before any call", (r["state"], "consented" in r["staff_note"]), ("no_scope", True))
_SCOPES.update({"missing": [], "detail": ""})
r = se.reading(NAME, today=TODAY)
check("linked and never read is not read yet", (r["state"], "not read yet" in r["staff_note"]), ("no_snapshot", True))
_book["list"] = [_row("c1", "One", stats=STATS), _row("c2", "Two", stats={"delivered": 300, "opened": 90, "clicked": 9}, sentAt="2026-08-30T00:00:00Z"),
                 _row("c4", "Old", stats={"delivered": 100, "opened": 10, "clicked": 1}, sentAt="2026-06-20T00:00:00Z"),
                 _row("c9", "Blank", sentAt="2026-09-01T00:00:00Z")]
_book["stats"] = {}
out = se.snapshot(NAME, today=TODAY, actor="Todd")
check("a snapshot reads and stores under the client", (out["ok"], out["reading"]["state"], out["reading"]["as_of"]), (True, "ok", TODAY.isoformat()))
check("...logged under the signed-in person", (entries()[-1]["actor"], entries()[-1]["client"], entries()[-1]["campaigns"]), ("Todd", NAME, 4))
r = out["reading"]
t30, t90 = r["totals"]["30"], r["totals"]["90"]
check("30-day totals sum the campaigns sent inside the window, counting only those with counts",
      (t30["campaigns"], t30["measured"], t30["delivered"], t30["opened"], t30["open_rate"]), (3, 2, 1480, 490, 33.1))
check("90-day totals reach the older one", (t90["campaigns"], t90["measured"], t90["delivered"]), (4, 3, 1580))
check("the card's campaigns carry no raw statistics", all("raw_stats" not in c for c in r["campaigns"]))
check("...while the store keeps them", se.readings(NAME)[0]["campaigns"][0]["raw_stats"], STATS)
_book["list_kind"] = "http"
out = se.snapshot(NAME, today=TODAY)
_book["list_kind"] = ""
check("a failed read on the same day replaces the day's row and the reading says so",
      (out["ok"], len(se.readings(NAME)), out["reading"]["state"], "last read failed" in out["reading"]["staff_note"]), (False, 1, "unread", True))
se.snapshot(NAME, today=TODAY)
_book["list_kind"] = "http"
out = se.snapshot(NAME, today=TODAY + timedelta(days=1))
_book["list_kind"] = ""
check("a failed read the day after keeps yesterday's reading on the card, with the failure named",
      (out["reading"]["state"], out["reading"]["as_of"], "showing the reading from" in out["reading"]["staff_note"]), ("ok", TODAY.isoformat(), True))
_book["list"] = []
out = se.snapshot(NAME, today=TODAY + timedelta(days=1))
check("a read that finds no sent campaign is empty, never a nought over a refusal", (out["ok"], out["reading"]["state"], out["reading"]["measured"]), (True, "empty", True))
for i in range(se.KEEP_READINGS + 5):
    se._append_reading(NAME, location_id=LOC, campaigns=[], notes=[], today=TODAY - timedelta(days=i))
check("readings are capped", len(se.readings(NAME)), se.KEEP_READINGS)
_ACCT_saved = dict(_ACCT)
suite_accounts.token_for = lambda name, url="": {"state": "not_connected", "location_id": "", "token": None, "detail": "the app is not installed on it", "connected": False}
out = se.snapshot(NAME, today=TODAY)
check("a sub-account that issues no token is said as such, no reading taken", (out["ok"], out["kind"], "not installed" in out["error"]), (False, "not_connected", True))
suite_accounts.token_for = lambda name, url="": dict(_ACCT) if name == NAME else {"state": "not_connected", "location_id": "", "token": None, "detail": "", "connected": False}


# ---------------------------------------------------------------------------
section("The nightly sweep: not while a scope is missing, once per client, bounded")
# ---------------------------------------------------------------------------

jsonstore.delete_json(se._readings_path(NAME))
jsonstore.delete_json(se._state_path())
_SCOPES.update({"missing": [se.SCOPE_STATS], "detail": "missing emails/stats.readonly"})
out = se.sweep(force=True, today=TODAY, names=[NAME])
check("a missing scope stops the sweep before a call", (out["ran"], "missing" in out["skipped"]), (False, True))
_SCOPES.update({"missing": [], "detail": ""})
_book["list"] = [_row("c1", "One", stats=STATS)]
NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)
out = se.sweep(force=True, today=TODAY, now=NOW, names=[NAME, "Nobody Co"])
check("the sweep reads the linked client and names the one that issues no token",
      (out["ran"], out["read"], out["failed"], "Nobody Co" in out["errors"]), (True, 1, 1, True))
out = se.sweep(force=True, today=TODAY, now=NOW, names=[NAME])
check("a client already read today is skipped", (out["already"], out["read"]), (1, 0))
out = se.sweep(force=False, today=TODAY, now=NOW + timedelta(hours=1), names=[NAME])
check("the hourly tick returns without a call until the window passes", out["skipped"], "Not due yet.")
check("...and is due again the next morning", se.due_for_refresh(NOW + timedelta(days=1)), True)
# Clients are walked in name order: Nobody Co, then the client, then Third.
_ticks = iter([0, 0, 0, 500, 500, 500])
out = se.sweep(force=True, today=TODAY + timedelta(days=1), now=NOW + timedelta(days=1), names=[NAME, "Nobody Co", "Third Co"], budget=100, clock=lambda: next(_ticks))
check("a spent budget names what it left", (out["read"], out["failed"], out["left"]), (1, 1, 1), note=out["errors"])
_book["list_kind"] = "refused"
jsonstore.delete_json(se._readings_path(NAME))
out = se.sweep(force=True, today=TODAY + timedelta(days=2), now=NOW + timedelta(days=2), names=[NAME, "Zed Co"])
_book["list_kind"] = ""
check("a refusal stops the sweep rather than repeating it for every client", (out["failed"], out["left"]), (1, 1), note=out["errors"])
check("the state is written for /status", se.sweep_state()["last_run_at"] is not None)
check("the linked-client list reads the mapping and never raises", isinstance(se.linked_clients(), list))


# ---------------------------------------------------------------------------
section("The routes: a stranger is refused, staff are answered, the POST is a press")
# ---------------------------------------------------------------------------

anon = Client(wsgi.application)
for path, method in (("/api/client/suite-email?name=x", "GET"), ("/api/client/suite-email/refresh", "POST")):
    r = anon.open(path, method=method, json={"name": NAME} if method == "POST" else None)
    check(f"{method} {path.split('?')[0]} refuses a stranger",
          r.status_code in (302, 401) and (r.status_code == 401 or r.headers.get("Location", "").startswith("/login")))

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
jsonstore.delete_json(se._readings_path(NAME))
r = staff.get("/api/client/suite-email?name=" + NAME.replace("'", "%27").replace(" ", "%20"))
d = r.get_json()
check("the GET answers the reading with the sweep beside it", (r.status_code, d["state"], "sweep" in d, "scopes" in d), (200, "no_snapshot", True, True))
check("a GET with no name is refused", staff.get("/api/client/suite-email").status_code, 400)
_book["list"] = [_row("c1", "One", stats=STATS)]
r = staff.post("/api/client/suite-email/refresh", json={"name": NAME})
check("a refresh reads now", (r.status_code, r.get_json()["ok"], r.get_json()["reading"]["state"]), (200, True, "ok"))
r = staff.post("/api/client/suite-email/refresh", json={"name": "Nobody Co"})
check("a refresh for an unlinked client is refused in words", (r.status_code, r.get_json()["ok"]), (400, False))
_book["list_kind"] = "refused"
r = staff.post("/api/client/suite-email/refresh", json={"name": NAME})
_book["list_kind"] = ""
check("a refused read answers 502 with the scope named", (r.status_code, se.SCOPE_LIST in r.get_json()["error"]), (502, True))
_src = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
_get_route = _src[_src.index('@app.route("/api/client/suite-email")'):_src.index('@app.route("/api/client/suite-email/refresh"')]
check("the GET route reaches no Suite call: reading(), never snapshot() or read()",
      "suite_email_stats.reading(" in _get_route and not any(f in _get_route for f in ("snapshot(", ".read(")))


# ---------------------------------------------------------------------------
section("The Client 360 card, lifted and driven in node")
# ---------------------------------------------------------------------------

_REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
_a = _REC.find("/* ---- c360 email campaigns (lifted")
_b = _REC.find("/* ---- end c360 email campaigns ----")
check("the card's renderer is marked for lifting", 0 < _a < _b)
_SRC = _REC[_a:_b] if 0 < _a < _b else ""
check("the card is drawn on the record and named in the section list",
      'id="c-suite-email"' in _REC and "'email campaigns'" in _REC and "loadSuiteEmail(name);" in _REC)
check("...with a bubble behind it", "hub.client360.suite_email" in _REC)
_book["list"] = [_row("c1", "September specials", stats=STATS)]
se.snapshot(NAME, today=TODAY)
_ok = se.reading(NAME, today=TODAY)
_ok["scopes"] = dict(_SCOPES)
_payloads = [
    {"state": "not_linked", "scopes": _SCOPES},
    {"state": "no_scope", "measured": False, "scopes": {"known": True, "missing": [se.SCOPE_LIST], "detail": "The Hub app has not been consented with emails/schedule.readonly."}},
    {"state": "unread", "error": "the store could not be read", "scopes": _SCOPES},
    {"state": "no_snapshot", "scopes": _SCOPES},
    {"state": "empty", "measured": True, "as_of": "2026-09-16", "campaigns": [], "notes": [], "scopes": _SCOPES},
    _ok,
]
_driver = ("function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;')"
           ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + _SRC + "\nconst P=" + json.dumps(_payloads) + ";\n"
           "console.log(JSON.stringify(P.map(p=>renderSuiteEmail(p,'Schmidt'))));\n")
_r = subprocess.run(["node", "-"], input=_driver, capture_output=True, text=True)
check("the lifted renderer runs on its own", _r.returncode, 0)
if _r.returncode:
    print("          node:", _r.stderr[-400:])
_out = json.loads(_r.stdout or "[]") if _r.returncode == 0 else [""] * 6
_nl, _ns, _un, _nsnap, _empty, _okm = (_out + [""] * 6)[:6]
check("not linked points at the Suite card, never a figure", "No Smart 1 Suite sub-account" in _nl and "Attach" in _nl and "Refresh" not in _nl)
check("a missing scope is said in the scope's words", "consented" in _ns and "emails/schedule.readonly" in _ns)
check("an unread store is drawn as unread, not as nothing sent", "could not be read" in _un and "having sent nothing" in _un)
check("linked and not read yet says so, with the press", "not read yet" in _nsnap and 'data-act="refresh"' in _nsnap)
check("no sent campaign says so with the day", "no sent email campaign" in _empty and "2026-09-16" in _empty)
check("a reading draws the totals, the table and the day",
      "campaigns, last 30 days" in _okm and "1,180" in _okm and "33.9%" in _okm and "September specials" in _okm and "Read " + TODAY.isoformat() in _okm)
check("...and the press", 'data-act="refresh"' in _okm)
check("the token never reaches the card's markup", TOKEN not in _okm)


# ---------------------------------------------------------------------------
section("The client's report page: gated on an email product and a linked account, absent otherwise")
# ---------------------------------------------------------------------------

from modules.reports import client_view, suite_email as em_section    # noqa: E402
_reports_testdb.reset(store)
CLIENT = "d:schmidthaus.com"
D1 = date.today() - timedelta(days=1)
store.upsert_rows([{"platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1", "campaign_name": "Fall",
                    "date": D1, "spend": "100.00", "impressions": 1_000_000, "clicks": 1120, "source": "windsor"}])
store.map_campaign("ttd", "adv-1", "t-1", client=CLIENT, client_name=NAME, product="Streaming TV", mapped_by="Todd")
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")

BOOK = {"rows": []}
em_section._product_rows = lambda: BOOK["rows"]
em_section._running = lambda row: row.get("status") != "Complete"
g = em_section.gate(CLIENT, NAME)
check("no product: off, the account linked, saying why", (g["gated"], g["product"], g["account"], g["why"]), (False, False, True, ["no live email product on the client's book"]))
BOOK["rows"] = [{"client": NAME, "product": "TrueView", "status": "Live"}]
check("a video buy is not an email product", em_section.gate(CLIENT, NAME)["product"], False)
BOOK["rows"] = [{"client": "schmidt's sausage", "product": "Email Marketing", "status": "Live"}]
g = em_section.gate(CLIENT, NAME)
check("an email product is, matched on the exact normalized name", (g["product"], g["products"], g["gated"]), (True, ["Email Marketing"], True))
BOOK["rows"] = [{"client": "Schmidt's Sausage Haus", "product": "Email Marketing", "status": "Live"}]
check("...and never on a substring", em_section.gate(CLIENT, NAME)["product"], False)
BOOK["rows"] = [{"client": NAME, "product": "Email Marketing", "status": "Complete"}]
check("...nor on a finished row", em_section.gate(CLIENT, NAME)["product"], False)
BOOK["rows"] = [{"client": NAME, "product": "Email Marketing", "status": "Live"}]

anon = Client(wsgi.application)
jsonstore.delete_json(se._readings_path(NAME))
client_view.forget(link.token)
html = anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True)
check("gated in and not read yet: the page has no email section", "Email campaigns" not in html)
check("...and data.json carries none, absent rather than explained", anon.get(f"/reports/r/c/{link.token}/data.json").get_json().get("email"), None)
g = em_section.staff_gate(CLIENT, NAME)
check("...the staff gate says it is waiting on a reading", (g["gated"], g["read"], "not read yet" in g["read_note"]), (True, False, True))

_book["list"] = [_row("c1", "September specials", stats=STATS, sentAt=(date.today() - timedelta(days=3)).isoformat() + "T12:00:00Z"),
                 _row("c9", "Blank", sentAt=(date.today() - timedelta(days=5)).isoformat() + "T12:00:00Z")]
se.snapshot(NAME)
g = em_section.staff_gate(CLIENT, NAME)
check("linked and read: the gate is on, naming the day", (g["gated"], g["account"], g["read"], g["as_of"]), (True, True, True, date.today().isoformat()))
client_view.forget(link.token)
r = anon.get(f"/reports/r/c/{link.token}")
html = r.get_data(as_text=True)
check("the page opens with the email section", (r.status_code, "Email campaigns" in html), (200, True))
check("...below the YouTube slot and above the CTA", html.index('id="email-h"') < html.index("Want more from this campaign?"))
check("...with the totals and the campaign's own name", "1,180" in html and "33.9%" in html and "September specials" in html and "September specials!" in html)
check("...a campaign without counts said as such", "counts not yet reported" in html)
check("...the day it was read", "Read " + date.today().isoformat() in html)
check("...never a staff note, a location id, a token or a vendor word",
      "staff" not in html.lower() and LOC not in html and TOKEN not in html and "Suite" not in html and products.forbidden_hits(html) == [])
data = anon.get(f"/reports/r/c/{link.token}/data.json").get_json()
check("data.json carries the reading with no staff note and no state",
      (data["email"]["totals"]["30"]["delivered"], len(data["email"]["campaigns"]), "staff_note" in data["email"], "state" in data["email"], LOC in json.dumps(data)),
      (1180, 2, False, False, False))
pdf = anon.get(f"/reports/r/c/{link.token}.pdf").get_data()
check("the PDF carries the section too", b"Email campaigns" in pdf and b"1,180" in pdf and b"Read " in pdf)

r = staff.get(f"/reports/client/{CLIENT}")
shtml = r.get_data(as_text=True)
check("the staff page prints the email gate as on", (r.status_code, "Email section on" in shtml), (200, True))
suite_accounts.location_for = lambda name, url="": {"state": "not_connected", "location_id": "", "detail": "none", "connected": False}
client_view.forget(link.token)
shtml = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("...and off, with the notice naming where to link one, once the account is gone",
      "Email section off" in shtml and "no linked Smart 1 Suite sub-account" in shtml and "Client 360" in shtml)
check("the client's page has no email section again", "Email campaigns" not in anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True))
suite_accounts.location_for = lambda name, url="": dict(_LINKED) if name == NAME else {"state": "not_connected", "location_id": "", "detail": "", "connected": False}
_SCOPES.update({"missing": [se.SCOPE_STATS], "detail": "missing"})
shtml = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("a missing scope is printed on the staff row", "scope missing" in shtml and se.SCOPE_STATS in shtml)
_SCOPES.update({"missing": [], "detail": ""})

se._call = _real_call
se.scope_state = _real_scope_state
suite_accounts.token_for, suite_accounts.location_for = _real_token_for, _real_location_for
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
