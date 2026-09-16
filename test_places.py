"""A client's Google Business Profile, read through hub/places.py.

    python3 test_places.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, through the COMPOSED app. Google is never reached: the
wire is stood in for at `requests.get`/`requests.post` where the call's own
error mapping is what is being asserted, and at `hub.places._call` or
`hub.places.details` everywhere else.

What it holds:

  * the key is a setting, the host is a quota row, the check is on
    /diagnostics, the log name is declared as a join rather than work, and
    the nightly job is registered;
  * every way a call fails is named apart -- refused, rate-limited,
    unreachable, an HTTP status -- each is recorded on the usage page, and
    the key never reaches a message;
  * a lookup proposes exactly one listing or none: the only result, or the
    only result whose website is the client's own; two propose neither;
  * a confirmation is keyed on the client's name, matched exactly and
    never on a substring, reads the listing on the press, and logs under
    the client; a read that fails still stores the confirmation and says so;
  * five kinds of nothing come back apart, a rating is never drawn without
    its review count, and the 30-day change is not measured until a reading
    that old exists;
  * one reading per client per day, capped;
  * the nightly sweep reads each confirmed listing once inside the window,
    skips one already read today, stops on a refused key, and names what a
    budget left unread;
  * every route refuses a stranger and answers staff;
  * the Client 360 card's renderer, lifted from the template and driven in
    node, draws each state apart;
  * the client's report page block is absent rather than explained when
    nothing is measured, and the coming-soon promise is gone.
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

TMP = tempfile.mkdtemp(prefix="s1places_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "places-test-secret"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ["GOOGLE_PLACES_API_KEY"] = "places-test-key-AIzaSyFAKE"
os.environ.pop("PANEL_PASSWORD", None)
os.environ.pop("REPORTS_DATABASE_URL", None)

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
from hub import auth, places, quotas, client_brand, scheduler, diagnostics  # noqa: E402
from hub.config import settings                                      # noqa: E402

KEY = os.environ["GOOGLE_PLACES_API_KEY"]
TODAY = date(2026, 9, 14)


def _place(pid="ChIJacme", name="Acme Plumbing", website="https://acme.com",
           rating=4.7, count=213, status="OPERATIONAL", address="1 Main St, Columbus, OH"):
    return {"id": pid, "displayName": {"text": name}, "formattedAddress": address,
            "websiteUri": website, "businessStatus": status, "rating": rating,
            "userRatingCount": count, "googleMapsUri": f"https://maps.google.com/?cid={pid}"}


def entries():
    """Oldest first, read through the module rather than off the file.

    The activity log's backend is the database now, so a test that opens
    AUDIT_LOG_PATH is asserting about a fallback nothing writes to while the
    thing it means to check is somewhere else -- which is a check that passes
    whatever the code does.
    """
    from hub import audit
    return list(reversed(audit.read(limit=500)))


# ---------------------------------------------------------------------------
section("The key, the quota row, the check, the log name and the job")
# ---------------------------------------------------------------------------

check("the key is a setting read through hub/config.py", settings.google_places_key, KEY)
check("...and the module reads it", places.api_key(), KEY)
check("...so the module is configured", places.configured(), True)
check("the Places host is a Google API the usage page can name",
      quotas.google_api_of("https://places.googleapis.com/v1/places:searchText"), "places")
check("...and Place Details too", quotas.google_api_of("https://places.googleapis.com/v1/places/ChIJx"), "places")
check("...with a row in the per-API table, no invented ceiling",
      ("places" in quotas.GOOGLE_APIS, quotas.GOOGLE_APIS["places"][1]), (True, 0))
check("the key check is on /diagnostics", diagnostics.check_places in diagnostics.CHECKS)
check("the log name is declared as a join rather than work", "places" in client_brand.NOT_WORK)
check("the nightly job is registered, ticking hourly",
      (scheduler.JOBS["places_snapshot"][0], scheduler.JOBS["places_snapshot"][1].__name__),
      (60, "job_places_snapshot"))
check("env.example documents the key",
      "GOOGLE_PLACES_API_KEY" in (ROOT / "env.example").read_text(encoding="utf-8"))
check("...and render.yaml carries it", "GOOGLE_PLACES_API_KEY" in (ROOT / "render.yaml").read_text(encoding="utf-8"))
check("review text is not in either field mask -- the Pro SKU and nothing from the Enterprise one",
      "reviews" not in places.SEARCH_MASK and "reviews" not in places.DETAILS_MASK)


# ---------------------------------------------------------------------------
section("The wire: every way a call fails is named, recorded, and never carries the key")
# ---------------------------------------------------------------------------

import requests                                                      # noqa: E402


class _Resp:
    def __init__(self, status, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json
        self.ok = 200 <= status < 300

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


_answers = []
_sent = []
_recorded = []


def _fake_get(url, headers=None, timeout=None, **kw):
    _sent.append(("GET", url, headers))
    a = _answers.pop(0)
    if isinstance(a, Exception):
        raise a
    return a


def _fake_post(url, json=None, headers=None, timeout=None, **kw):
    _sent.append(("POST", url, headers, json))
    a = _answers.pop(0)
    if isinstance(a, Exception):
        raise a
    return a


_real_get, _real_post = requests.get, requests.post
_real_record = quotas.record_google
requests.get, requests.post = _fake_get, _fake_post
quotas.record_google = lambda url, *, module, ok=True, units=1: _recorded.append((url, module, ok))
try:
    _answers[:] = [_Resp(403, {"error": {"message": f"key {KEY} denied"}})]
    data, err = places._call(places.DETAILS_URL.format(place_id="ChIJx"), mask=places.DETAILS_MASK)
    check("403 is refused, naming the fix", (data, err["kind"], "API restrictions" in err["message"]),
          (None, "refused", True))
    check("...recorded against the usage page as a failed Places call",
          _recorded[-1], (places.DETAILS_URL.format(place_id="ChIJx"), "places", False))
    check("...and the key rode in the header, never anywhere else",
          _sent[-1][2]["X-Goog-Api-Key"] == KEY and KEY not in err["message"])
    _answers[:] = [_Resp(429, {})]
    _, err = places._call(places.SEARCH_URL, mask=places.SEARCH_MASK, body={"textQuery": "x"})
    check("429 is rate-limited, apart from refused", err["kind"], "rate_limited")
    _answers[:] = [requests.Timeout()]
    _, err = places._call(places.SEARCH_URL, mask=places.SEARCH_MASK, body={"textQuery": "x"})
    check("a timeout is unreachable, never a bad key", err["kind"], "unreachable")
    check("...and still recorded as a failed call", _recorded[-1][2], False)
    _answers[:] = [_Resp(500, {})]
    _, err = places._call(places.SEARCH_URL, mask=places.SEARCH_MASK, body={"textQuery": "x"})
    check("a 500 is an HTTP status, said as one", (err["kind"], "500" in err["message"]), ("http", True))
    _answers[:] = [_Resp(200, None, bad_json=True)]
    _, err = places._call(places.SEARCH_URL, mask=places.SEARCH_MASK, body={"textQuery": "x"})
    check("a body that is not JSON is refused rather than read as empty", err["kind"], "http")
    _answers[:] = [_Resp(200, {"places": [_place()]})]
    data, err = places._call(places.SEARCH_URL, mask=places.SEARCH_MASK, body={"textQuery": "Acme"})
    check("a 200 answers the data with no error", (err, len(data["places"])), ({}, 1))
    check("...recorded as an ok call", _recorded[-1][2], True)
    check("...with the field mask on the request", _sent[-1][2]["X-Goog-FieldMask"], places.SEARCH_MASK)
    check("...and the body passed through as given", _sent[-1][3], {"textQuery": "Acme"})
finally:
    requests.get, requests.post = _real_get, _real_post
    quotas.record_google = _real_record

check("the module records every call through quotas.record_google, by name",
      "quotas.record_google(" in (ROOT / "hub" / "places.py").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
section("Candidates: one proposal or none, and a person confirms")
# ---------------------------------------------------------------------------

_search = []
_bodies = []
_real_call = places._call


def _stub_call(url, *, mask, body=None):
    _bodies.append(body)
    return _search.pop(0) if _search else ({"places": []}, {})


places._call = _stub_call
try:
    _search[:] = [({"places": [_place()]}, {})]
    c = places.candidates("Acme Plumbing", domain="")
    check("one result is proposed as the only listing", (c["measured"], c["proposed"]), (True, "ChIJacme"))
    check("a search asks for at most MAX_CANDIDATES, by name",
          (_bodies[-1]["textQuery"], _bodies[-1]["pageSize"]), ("Acme Plumbing", places.MAX_CANDIDATES))
    check("...saying why", "only listing" in c["why"])
    _search[:] = [({"places": [_place("ChIJa", "Acme Plumbing", "https://acme-plumbing.net"),
                               _place("ChIJb", "Acme Plumbing", "https://www.acme.com/"),
                               _place("ChIJc", "Acme Plumbing Supply", "https://acmesupply.com")]}, {})]
    c = places.candidates("Acme Plumbing", query="Columbus", domain="acme.com")
    check("several results: the only one whose website is theirs is proposed", c["proposed"], "ChIJb")
    check("...naming the domain", "acme.com" in c["why"])
    check("...the query carried what the rep added", c["query"], "Acme Plumbing Columbus")
    check("...and every row says whether it matched", [r["domain_match"] for r in c["candidates"]],
          [False, True, False])
    _search[:] = [({"places": [_place("ChIJa", website="https://one.com"),
                               _place("ChIJb", website="https://two.com")]}, {})]
    c = places.candidates("Acme Plumbing", domain="acme.com")
    check("several results and none theirs proposes nobody", c["proposed"], None)
    check("...and says to pick or narrow", "narrow" in c["why"])
    _search[:] = [({"places": [_place("ChIJa", website="https://acme.com"),
                               _place("ChIJb", website="https://acme.com/other")]}, {})]
    c = places.candidates("Acme Plumbing", domain="acme.com")
    check("two results carrying their website propose neither", c["proposed"], None)
    check("...naming both", "2 listings" in c["why"])
    _search[:] = [({"places": []}, {})]
    c = places.candidates("Acme Plumbing", domain="acme.com")
    check("no result is measured and proposes nobody", (c["measured"], c["proposed"], c["candidates"]),
          (True, None, []))
    _search[:] = [(None, {"kind": "refused", "message": "Google refused the Places key (403)."})]
    c = places.candidates("Acme Plumbing")
    check("a refused key is not measured, with the kind", (c["measured"], c["kind"]), (False, "refused"))
    c = places.candidates("", query="")
    check("nothing to search for is said rather than sent", c["kind"], "empty")
finally:
    places._call = _real_call

_real_key = places.api_key
places.api_key = lambda: ""
try:
    c = places.candidates("Acme Plumbing")
    check("with no key the lookup is unconfigured, and names the variable",
          (c["kind"], "GOOGLE_PLACES_API_KEY" in c["error"]), ("unconfigured", True))
finally:
    places.api_key = _real_key

d = places.details("not a place id!")
check("a string that is not a place id is refused before any call", d["kind"], "bad_id")


# ---------------------------------------------------------------------------
section("Confirm: keyed on the name, read on the press, logged under the client")
# ---------------------------------------------------------------------------

_details = []
_real_details = places.details


def _fake_details(pid):
    if _details:
        return _details.pop(0)
    return {"measured": True, "error": "", "kind": "",
            "place": places._place_row(_place(pid))}


places.details = _fake_details
try:
    res = places.confirm("Acme Plumbing", "ChIJacme", actor="Todd", today=TODAY)
    check("confirming stores the record and reads the listing on the press",
          (res["ok"], res["reading"]["state"], res["reading"]["rating"], res["reading"]["review_count"]),
          (True, "ok", 4.7, 213))
    check("...keyed on the client's name", sorted(places.all_records()), ["Acme Plumbing"])
    check("...carrying Google's own name and address and who confirmed",
          (res["record"]["name"], res["record"]["address"], res["record"]["confirmed_by"]),
          ("Acme Plumbing", "1 Main St, Columbus, OH", "Todd"))
    check("the record is found on the exact normalized name",
          (places.record("acme plumbing") or {}).get("place_id"), "ChIJacme")
    check("...and never on a substring", places.record("Acme"), None)
    check("...nor a longer name", places.record("Acme Plumbing Supply"), None)
    logged = [e for e in entries() if e.get("module") == "places" and e.get("type") == "place_confirmed"]
    check("the confirmation is in the activity log under the client, with who",
          (len(logged), logged[-1].get("client"), logged[-1].get("actor")), (1, "Acme Plumbing", "Todd"))
    check("...and under the literal module name the work log can read", logged[-1].get("module"), "places")
    check("the 30-day change is not measured on the first day, and says why",
          (res["reading"]["change"]["measured"], "First reading" in res["reading"]["change"]["note"]),
          (False, True))
    places.confirm("Acme Plumbing", "ChIJacme", actor="Todd", today=TODAY)
    check("confirming again the same day replaces the reading rather than stacking one",
          len(places.readings("Acme Plumbing")), 1)
    _details[:] = [{"measured": False, "error": "Google Places did not answer in time.",
                    "kind": "unreachable", "place": None}]
    res3 = places.confirm("Beta LLC", "ChIJbeta", actor="Todd", today=TODAY)
    check("a read that fails still stores the confirmation", (res3["ok"], "Beta LLC" in places.all_records()),
          (True, True))
    check("...and the reading says the read failed rather than drawing nothing",
          (res3["reading"]["state"], res3["reading"]["error"]),
          ("unread", "Google Places did not answer in time."))
    res4 = places.confirm("Gamma Inc", "bad id!", actor="Todd")
    check("a bad place id is refused, nothing stored", (res4["ok"], "Gamma Inc" in places.all_records()),
          (False, False))
    check("a confirmation needs both a client and an id",
          places.confirm("", "ChIJx", actor="Todd")["ok"], False)
finally:
    places.details = _real_details


# ---------------------------------------------------------------------------
section("The reading: five kinds of nothing, and a rating never without its count")
# ---------------------------------------------------------------------------

places.api_key = lambda: ""
try:
    r = places.reading("Acme Plumbing", today=TODAY)
    check("no key is unconfigured, named for staff, and never measured",
          (r["state"], r["measured"], "GOOGLE_PLACES_API_KEY" in r["staff_note"]),
          ("unconfigured", False, True))
    check("...and the client's page gets nothing from it", places.public_view(r), None)
finally:
    places.api_key = _real_key

r = places.reading("Nobody Here", today=TODAY)
check("no listing confirmed is no_place", (r["state"], r["record"]), ("no_place", None))
r = places.reading("Beta LLC", today=TODAY)
check("a listing whose only read failed is unread, with the error",
      (r["state"], r["error"]), ("unread", "Google Places did not answer in time."))
check("...and the client's page gets nothing from it", places.public_view(r), None)

from hub import jsonstore                                            # noqa: E402
jsonstore.update_json(places._places_path(),
                      lambda d: {**(d or {}), "Delta Co": {"place_id": "ChIJdelta", "name": "Delta"}})
r = places.reading("Delta Co", today=TODAY)
check("confirmed and never read is no_snapshot", r["state"], "no_snapshot")

r = places.reading("Acme Plumbing", today=TODAY)
check("a good reading is ok, with the rating, the count, the status and the day",
      (r["state"], r["rating"], r["review_count"], r["status_label"], r["as_of"]),
      ("ok", 4.7, 213, "Open", TODAY.isoformat()))
pv = places.public_view(r)
check("the public view carries the figures and the date and nothing about our tooling",
      (pv["rating"], pv["review_count"], pv["as_of"], "staff_note" in pv, "record" in pv),
      (4.7, 213, TODAY.isoformat(), False, False))
check("card_for is the public view", places.card_for("Acme Plumbing", today=TODAY), pv)

# The 30-day change needs a reading 30 days old: the newest good reading on
# or before the latest minus 30 days, never the one taken a minute ago.
places._append_reading("Acme Plumbing", places._place_row(_place(rating=4.5, count=180)),
                       today=TODAY - timedelta(days=45))
places._append_reading("Acme Plumbing", places._place_row(_place(rating=4.6, count=200)),
                       today=TODAY - timedelta(days=31))
places._append_reading("Acme Plumbing", places._place_row(_place(rating=4.65, count=205)),
                       today=TODAY - timedelta(days=20))
r = places.reading("Acme Plumbing", today=TODAY)
ch = r["change"]
check("with a reading 31 days old the change is measured against it, not the 45-day one",
      (ch["measured"], ch["since"]), (True, (TODAY - timedelta(days=31)).isoformat()))
check("...rating up 0.1, 13 new reviews", (ch["rating_delta"], ch["reviews_delta"]), (0.1, 13))
check("...and the 20-day reading is not the base", ch["base_review_count"], 200)
check("the newest good reading is still the one drawn", (r["rating"], r["as_of"]), (4.7, TODAY.isoformat()))

# A newest read that failed does not hide the last good one, and says so.
places._append_reading("Acme Plumbing", None, today=TODAY + timedelta(days=1), error="HTTP 500")
r = places.reading("Acme Plumbing", today=TODAY + timedelta(days=1))
check("a failed newest read keeps drawing the last good reading",
      (r["state"], r["rating"], r["as_of"]), ("ok", 4.7, TODAY.isoformat()))
check("...and the staff note says the newest failed", "failed" in r["staff_note"] and "HTTP 500" in r["staff_note"])
check("...while the client's page carries no staff note", "staff_note" in (places.public_view(r) or {}), False)
r = places.reading("Acme Plumbing", today=TODAY + timedelta(days=5))
check("an old reading says how old it is", r["note"], "Read 5 days ago.")


# ---------------------------------------------------------------------------
section("One reading per day, and a cap")
# ---------------------------------------------------------------------------

before = len(places.readings("Acme Plumbing"))
places._append_reading("Acme Plumbing", places._place_row(_place(rating=4.8)), today=TODAY)
check("a second reading on one day replaces the first", len(places.readings("Acme Plumbing")), before)
check("...with the newer figures", places.readings("Acme Plumbing")[1]["rating"], 4.8)
for i in range(places.KEEP_READINGS + 20):
    places._append_reading("Cap Co", places._place_row(_place(rating=4.0)), today=TODAY - timedelta(days=i))
check("the readings file is capped, newest kept",
      (len(places.readings("Cap Co")), places.readings("Cap Co")[0]["day"]),
      (places.KEEP_READINGS, TODAY.isoformat()))


# ---------------------------------------------------------------------------
section("The nightly sweep: once inside the window, once per listing, bounded")
# ---------------------------------------------------------------------------

_calls = []


def _counting_details(pid):
    _calls.append(pid)
    if pid == "ChIJbeta":
        return {"measured": False, "error": "Google refused the Places key (403).", "kind": "refused",
                "place": None}
    return {"measured": True, "error": "", "kind": "", "place": places._place_row(_place(pid))}


places.details = _counting_details
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
try:
    jsonstore.delete_json(places._state_path())
    out = places.sweep(force=False, today=date(2026, 9, 15), now=NOW)
    check("never run is due, so the first hourly tick sweeps", out["ran"], True)
    # Three records: Acme Plumbing, Beta LLC (whose read is refused) and
    # Delta Co. Cap Co has readings and no record, so it is not swept. In
    # name order the refused key stops the sweep after Beta, so Delta is
    # left -- and every listing is accounted for one way or another.
    check("...every confirmed listing accounted for: read, failed, already or left",
          (out["clients"], out["read"] + out["failed"] + out["already"] + out["left"]), (3, 3))
    check("a refused key stops the sweep rather than recording the refusal per client",
          (out["failed"], out["left"] >= 1, "Beta LLC" in out["errors"]), (1, True, True))
    st = places.sweep_state()
    check("the state records the run", (st["last_run_at"], st["read"]), (NOW.isoformat(timespec="seconds"), out["read"]))
    check("...so it is not due again inside the window", places.due_for_refresh(NOW + timedelta(hours=2)), False)
    check("...and is due once the window has passed", places.due_for_refresh(NOW + timedelta(days=1)), True)
    check("not due is a sentence, not a call",
          (places.sweep(force=False, now=NOW + timedelta(hours=1))["skipped"], len(_calls)),
          ("Not due yet.", len(_calls)))

    # Already read today is skipped: a restart inside the window must not
    # read the book twice.
    jsonstore.update_json(places._places_path(), lambda d: {k: v for k, v in (d or {}).items() if k != "Beta LLC"})
    n = len(_calls)
    out = places.sweep(force=True, today=date(2026, 9, 15), now=NOW)
    check("a listing already read today is skipped, the rest read",
          (out["already"] >= 1, out["failed"], len(_calls) > n), (True, 0, True))

    # A budget names what it did not reach rather than dropping it.
    tick = [0.0]

    def _clock():
        tick[0] += 100.0
        return tick[0]

    out = places.sweep(force=True, today=date(2026, 9, 16), now=NOW + timedelta(days=1), budget=90, clock=_clock)
    check("past the budget the sweep stops and names what is left",
          (out["ran"], out["left"] >= 1, out["read"] + out["already"] + out["left"]), (True, True, out["clients"]))
finally:
    places.details = _real_details

places.api_key = lambda: ""
try:
    check("unconfigured, the sweep skips by name",
          "GOOGLE_PLACES_API_KEY" in places.sweep(force=True)["skipped"])
finally:
    places.api_key = _real_key

from flask import Flask                                              # noqa: E402
places.details = lambda pid: {"measured": True, "error": "", "kind": "", "place": places._place_row(_place(pid))}
try:
    out = scheduler.JOBS["places_snapshot"][1](Flask("t"))
    check("the scheduler job returns the sweep's own answer", "ran" in out and "skipped" in out)
finally:
    places.details = _real_details


# ---------------------------------------------------------------------------
section("Clear: not this listing")
# ---------------------------------------------------------------------------

res = places.clear("Acme Plumbing", actor="Todd")
check("clearing removes the record", (res["ok"], places.record("Acme Plumbing")), (True, None))
check("...keeps the readings", len(places.readings("Acme Plumbing")) > 0)
check("...and the reading is no_place again", places.reading("Acme Plumbing", today=TODAY)["state"], "no_place")
check("...logged under the client", [e for e in entries() if e.get("type") == "place_cleared"][-1].get("client"),
      "Acme Plumbing")
check("clearing nothing says so", places.clear("Acme Plumbing")["ok"], False)


# ---------------------------------------------------------------------------
section("The routes: a stranger is refused, staff are answered, every POST is a press")
# ---------------------------------------------------------------------------

anon = Client(wsgi.application)
for path, method in (("/api/client/places?name=Acme%20Plumbing", "GET"),
                     ("/api/client/places/lookup", "POST"), ("/api/client/places/confirm", "POST"),
                     ("/api/client/places/refresh", "POST"), ("/api/client/places/clear", "POST")):
    r = anon.open(path, method=method, json={"name": "Acme Plumbing"} if method == "POST" else None)
    check(f"{method} {path.split('?')[0]} refuses a stranger",
          r.status_code in (302, 401) and (r.status_code == 401 or r.headers.get("Location", "").startswith("/login")))

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = staff.get("/api/client/places?name=Acme%20Plumbing")
d = r.get_json()
check("the GET answers the reading with the domain and the sweep beside it",
      (r.status_code, d["state"], "domain" in d, "sweep" in d), (200, "no_place", True, True))
check("a GET with no name is refused", staff.get("/api/client/places").status_code, 400)

places.details = lambda pid: {"measured": True, "error": "", "kind": "", "place": places._place_row(_place(pid))}
_real_cands = places.candidates
places.candidates = lambda name, query="", domain="": {"measured": True, "error": "", "kind": "", "query": name,
                                                      "candidates": [places._place_row(_place())],
                                                      "proposed": "ChIJacme", "why": "the only listing"}
try:
    r = staff.post("/api/client/places/lookup", json={"name": "Acme Plumbing", "query": "Columbus"})
    check("a lookup answers the candidates", (r.status_code, r.get_json()["proposed"]), (200, "ChIJacme"))
    r = staff.post("/api/client/places/confirm", json={"name": "Acme Plumbing", "place_id": "ChIJacme"})
    check("a confirmation stores and reads on the press",
          (r.status_code, r.get_json()["ok"], r.get_json()["reading"]["state"]), (200, True, "ok"))
    check("...under the signed-in person", places.record("Acme Plumbing")["confirmed_by"], "Todd")
    r = staff.post("/api/client/places/refresh", json={"name": "Acme Plumbing"})
    check("a refresh reads now", (r.status_code, r.get_json()["ok"]), (200, True))
    r = staff.post("/api/client/places/confirm", json={"name": "Acme Plumbing"})
    check("a confirmation with no id is refused", r.status_code, 400)
    r = staff.post("/api/client/places/clear", json={"name": "Acme Plumbing"})
    check("a clear answers", (r.status_code, r.get_json()["ok"]), (200, True))
    r = staff.post("/api/client/places/clear", json={"name": "Acme Plumbing"})
    check("...and a second clear is a 404", r.status_code, 404)
    r = staff.post("/api/client/places/refresh", json={"name": "Acme Plumbing"})
    check("a refresh with no listing confirmed is refused", r.status_code, 400)
finally:
    places.details = _real_details
    places.candidates = _real_cands

_src = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
_get_route = _src[_src.index('@app.route("/api/client/places")'):_src.index('@app.route("/api/client/places/lookup"')]
check("the GET route reaches no Google call: reading(), never candidates(), details() or snapshot()",
      "places.reading(" in _get_route and not any(f in _get_route for f in ("candidates(", "details(", "snapshot(")))


# ---------------------------------------------------------------------------
section("The Client 360 card, lifted and driven in node")
# ---------------------------------------------------------------------------

_REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
_a = _REC.find("/* ---- c360 google listing (lifted")
_b = _REC.find("/* ---- end c360 google listing ----")
check("the card's renderer is marked for lifting", 0 < _a < _b)
_SRC = _REC[_a:_b] if 0 < _a < _b else ""
check("the card is drawn on the record and named in the section list",
      'id="c-places"' in _REC and "'google listing'" in _REC and "loadPlaces(name);" in _REC)
check("...with a bubble behind it", "hub.client360.places" in _REC)
_payloads = [
    {"state": "unconfigured", "record": None},
    {"state": "unread", "record": None, "error": "the store could not be read"},
    {"state": "no_place", "record": None},
    {"state": "no_snapshot", "record": {"place_id": "ChIJx", "name": "Acme", "address": "1 Main"}},
    {"state": "unread", "record": {"place_id": "ChIJx", "name": "Acme"}, "error": "HTTP 500"},
    json.loads(json.dumps(dict(places.reading("Acme Plumbing", today=TODAY),
                               record={"place_id": "ChIJacme", "name": "Acme Plumbing",
                                       "maps_url": "https://maps.google.com/?cid=1"},
                               state="ok", rating=4.7, review_count=213, status_label="Open",
                               as_of="2026-09-14",
                               change={"measured": True, "days": 30, "since": "2026-08-14",
                                       "rating_delta": 0.1, "reviews_delta": 13}))),
]
_cands = {"measured": True, "query": "Acme Plumbing Columbus", "proposed": "ChIJb",
          "why": "the only listing whose website is acme.com",
          "candidates": [{"place_id": "ChIJa", "name": "Acme Plumbing", "address": "1 A St", "domain_match": False,
                          "rating": 4.1, "review_count": 20, "status_label": "Open", "domain": "one.com"},
                         {"place_id": "ChIJb", "name": "Acme Plumbing", "address": "2 B St", "domain_match": True,
                          "rating": 4.7, "review_count": 213, "status_label": "Open", "domain": "acme.com"}]}
_driver = ("function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;')"
           ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + _SRC + "\nconst P=" + json.dumps(_payloads) + ";\nconst C=" + json.dumps(_cands) + ";\n"
           "const out=P.map(p=>renderPlaces(p,'Acme Plumbing',null));\n"
           "out.push(renderPlaces({state:'no_place'},'Acme Plumbing',C));\n"
           "out.push(renderPlaces({state:'no_place'},'Acme Plumbing',{measured:true,query:'x',candidates:[],proposed:null,why:'none'}));\n"
           "console.log(JSON.stringify(out));\n")
_r = subprocess.run(["node", "-"], input=_driver, capture_output=True, text=True)
check("the lifted renderer runs on its own", _r.returncode, 0)
if _r.returncode:
    print("          node:", _r.stderr[-400:])
_out = json.loads(_r.stdout or "[]") if _r.returncode == 0 else [""] * 8
_unconf, _unread, _none, _nosnap, _failed_read, _okm, _candm, _nocand = (_out + [""] * 8)[:8]
check("no key: says so and points at System status, never a figure",
      "not set up" in _unconf and "/status" in _unconf and "★" not in _unconf)
check("an unread store is drawn as unread, not as no listing",
      "could not be read" in _unread and "Find their listing" not in _unread)
check("no listing: the search box and the billed lookup, named as one",
      'data-act="lookup"' in _none and "One billed lookup" in _none)
check("confirmed and not read yet says so, with the record", "not read yet" in _nosnap and "Acme" in _nosnap)
check("a failed last read says so and keeps the listing", "last read failed" in _failed_read and "HTTP 500" in _failed_read)
check("a reading draws the rating WITH its review count, the status and the day",
      "4.7 ★" in _okm and "from 213 Google reviews" in _okm and "Open" in _okm and "Read 2026-09-14" in _okm)
check("...the 30-day change", "+0.1 ★" in _okm and "+13 reviews" in _okm and "since 2026-08-14" in _okm)
check("...and the two presses", 'data-act="refresh"' in _okm and 'data-act="clear"' in _okm)
check("candidates: one Confirm per row, the proposed one marked, the matched website marked",
      _candm.count('data-act="confirm"') == 2 and "proposed" in _candm and "their website" in _candm
      and 'data-id="ChIJb"' in _candm)
check("no candidates says so and how to narrow", "returned no listing" in _nocand and "city" in _nocand)


# ---------------------------------------------------------------------------
section("The client's page: absent rather than explained, and the promise is gone")
# ---------------------------------------------------------------------------

from modules.reports import organic                                  # noqa: E402

check("the coming-soon note is gone from the labels", "gbp_note" not in organic.LABELS)
check("...and the section no longer builds it",
      "coming_soon" not in (ROOT / "modules" / "reports" / "organic.py").read_text())
block = {"labels": {}, "analytics": {}, "search": {}, "work": {},
         "gbp": {"label": "Google Business Profile", "measured": False,
                 "staff_note": "No Google listing has been confirmed for this client.", "state": "no_place"}}
check("an unmeasured block is absent from the public view, not explained", organic.public_view(block)["gbp"], None)
block["gbp"] = {"label": "Google Business Profile", "measured": True, "rating": 4.7, "review_count": 213,
                "as_of": "2026-09-14", "change": {"measured": False}, "staff_note": "the newest read failed"}
pub = organic.public_view(block)["gbp"]
check("a measured block reaches the public view without the staff note",
      (pub["rating"], pub["review_count"], "staff_note" in pub), (4.7, 213, False))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
