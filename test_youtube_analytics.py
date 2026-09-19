"""A client's YouTube channel, the OAuth half: watch time, subscribers
gained and traffic sources, read through hub/youtube_analytics.py.

    python3 test_youtube_analytics.py

No pytest, no new dependencies, a temporary data directory and a
throwaway SQLite database, through the COMPOSED app. YouTube Analytics is
never reached: the wire is stood in for at `modules.youtube_studio.youtube
.access_token` / `.record_google_request` everywhere the module's own
error mapping is not what is being asserted.

What it holds:

  * the join is the channel id, never the client's name -- each tool
    spells a client's name its own way, and matching by name is exactly
    the "two spellings of one client" failure this codebase has paid for
    twice already;
  * a confirmed channel with no youtube_studio connection reads
    not_connected, never a failure, and a client's page carries nothing
    for this half at all -- a card announcing "not connected" on a
    document about their business is a sentence about our tooling;
  * five kinds of nothing come back apart, and only ok reaches
    public_view();
  * one reading per client per day, capped, never a delta between two of
    our own readings -- the API answers the period directly;
  * the nightly sweep reads only channels with a connection, skips one
    already read today, and is under a wall-clock budget;
  * the quota row and the host mapping are on the usage page, in the
    Analytics API's own bucket, separate from the Data API v3's;
  * the nightly job is registered, ticking hourly;
  * modules/reports/youtube.py's gate and section carry the Analytics
    half beside the keyed one, never gating on it, and public_view() drops
    it along with everything else when nothing was measured;
  * the /api/client/youtube routes carry it under "analytics", and the
    refresh route refreshes both halves behind one button;
  * the Client 360 card's renderer, lifted and driven in node, draws each
    state apart and renders nothing at all when the key is absent.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ytanalytics_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
_reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "youtube-analytics-test-secret"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
os.environ["GOOGLE_PLACES_API_KEY"] = "places-test-key-AIzaSyFAKE"
os.environ.pop("YOUTUBE_API_KEY", None)
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
from hub import auth, youtube, youtube_analytics as ya, quotas, scheduler  # noqa: E402
from modules.youtube_studio import store as studio_store             # noqa: E402
from modules.reports import youtube as reports_youtube               # noqa: E402

TODAY = date(2026, 9, 14)
CID_A = "UCaaaaaaaaaaaaaaaaaaaaaa"
CID_B = "UCbbbbbbbbbbbbbbbbbbbbbb"

with wsgi.hub_app.app_context():
    pass  # boot has already created tables


def _item(cid=CID_A, title="Acme Plumbing", handle="@acmeplumbing"):
    return {"id": cid, "snippet": {"title": title, "customUrl": handle, "publishedAt": "2019-03-01T00:00:00Z",
                                   "thumbnails": {"default": {"url": f"https://yt.example/{cid}.jpg"}}},
            "statistics": {"viewCount": "12000", "videoCount": "18", "subscriberCount": "340"}}


youtube.details = lambda cid: {"measured": True, "error": "", "kind": "",
                               "channel": youtube._channel_row(_item(cid))}
youtube.confirm("Acme Plumbing", CID_A, actor="Todd", today=TODAY)


# ---------------------------------------------------------------------------
section("The join is the channel id, never the client's name")
# ---------------------------------------------------------------------------

check("with no youtube_studio connection at all, nothing is found",
      ya.connection_for(CID_A), None)

studio_store.update(lambda data: studio_store.client(data, "Acme Plumbing, LLC")["channels"]
                     .setdefault(CID_A, {}).update(
                         id=CID_A, title="Acme Plumbing", refresh_token="fake-encrypted-refresh-token",
                         connected_at="2026-09-01T00:00:00"))

conn = ya.connection_for(CID_A)
check("a channel with a connection is found, by id alone",
      (conn is not None, conn and conn["studio_name"]), (True, "Acme Plumbing, LLC"))
check("...even though the studio's own name spelling differs from ours "
      "('Acme Plumbing, LLC' vs 'Acme Plumbing') -- the join never compares names", True)
check("available() agrees", ya.available(CID_A), True)
check("a channel id with no connection finds nothing", ya.connection_for(CID_B), None)

studio_store.update(lambda data: studio_store.client(data, "Someone Else Entirely")["channels"]
                     .setdefault(CID_B, {}).update(id=CID_B, refresh_token=""))
check("a channel row with no refresh_token is not a connection", ya.connection_for(CID_B), None)


# ---------------------------------------------------------------------------
section("Five kinds of nothing, and only ok reaches a client's page")
# ---------------------------------------------------------------------------

check("no confirmed channel at all: no_channel",
      ya.reading("Nobody's Client", today=TODAY)["state"], "no_channel")

youtube.confirm("Unconnected Co", CID_B, actor="Todd", today=TODAY)
r = ya.reading("Unconnected Co", today=TODAY)
check("confirmed, no youtube_studio connection: not_connected, never a failure",
      (r["state"], r["measured"]), ("not_connected", False))
check("...says where to connect it", "/tools/youtube/" in r["staff_note"])
check("not_connected does not reach the client's page", ya.public_view(r), None)

r = ya.reading("Acme Plumbing", today=TODAY)
check("connected and never read: no_reading", r["state"], "no_reading")
check("no_reading does not reach the client's page", ya.public_view(r), None)

_real_fetch = ya._fetch
ya._fetch = lambda name, cid: (None, {"kind": "refused", "message": "needs reconnecting"})
try:
    out = ya.snapshot("Acme Plumbing", today=TODAY)
    check("a failed fetch is stored as an error and reported, not silently dropped",
          (out["ok"], out["error"]), (False, "needs reconnecting"))
    check("...and reading() says unread with the same message",
          ya.reading("Acme Plumbing", today=TODAY)["state"], "unread")
    check("unread does not reach the client's page",
          ya.public_view(ya.reading("Acme Plumbing", today=TODAY)), None)
finally:
    ya._fetch = _real_fetch

GOOD_DATA = {"views": 5100, "watch_minutes": 8420.5, "subscribers_gained": 38, "subscribers_lost": 6,
             "sources": [{"source": "YT_SEARCH", "label": "YouTube search", "views": 2000},
                        {"source": "EXT_URL", "label": "Links from other websites", "views": 900},
                        {"source": "SUBSCRIBER", "label": "Subscription feed", "views": 800},
                        {"source": "RELATED_VIDEO", "label": "Suggested videos", "views": 500}],
             "start": "2026-08-18", "end": "2026-09-14"}
ya._fetch = lambda name, cid: (dict(GOOD_DATA), {})
try:
    out = ya.snapshot("Acme Plumbing", today=TODAY)
    check("a good fetch reads ok", out["ok"], True)
    r = ya.reading("Acme Plumbing", today=TODAY)
    check("...state ok, the period figures carried through",
          (r["state"], r["watch_minutes"], r["subscribers_gained"], r["subscribers_lost"]),
          ("ok", 8420.5, 38, 6))
    check("...and net is arithmetic on gained/lost, not a second call",
          r["subscribers_net"], 32)
    check("...traffic sources capped and ranked as the fetch returned them",
          len(r["sources"]), 4)
    pv = ya.public_view(r)
    check("public_view carries the figures", pv["watch_minutes"], 8420.5)
    check("...and nothing about our tooling", "staff_note" in pv, False)
    check("...and nothing about the youtube_studio connection", "connection" in pv, False)
finally:
    ya._fetch = _real_fetch

check("one reading per client per day -- the good snapshot above replaced "
      "the failed one from the same day rather than stacking beside it",
      len(ya.readings("Acme Plumbing")), 1)


# ---------------------------------------------------------------------------
section("The nightly sweep reads only connected channels")
# ---------------------------------------------------------------------------

youtube.confirm("Third Client No Connection", "UCcccccccccccccccccccccc", actor="Todd", today=TODAY)
ya._fetch = lambda name, cid: (dict(GOOD_DATA), {})
try:
    out = ya.sweep(force=True, today=TODAY + timedelta(days=1),
                   now=datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc))
    check("the sweep's client count is only the connected ones",
          out["clients"], 1)  # only Acme Plumbing (CID_A) has a connection
    check("...and the two unconnected confirmed channels are counted apart, not as a failure",
          out["not_connected"], 2)
    check("...the connected one was read", out["read"], 1)
finally:
    ya._fetch = _real_fetch

out = ya.sweep(force=True, today=TODAY + timedelta(days=1),
               now=datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc))
check("a channel already read today (with the stub restored, this call "
      "would fail if it tried a real fetch) is skipped, never re-fetched",
      out["already"], 1)

from flask import Flask                                              # noqa: E402
ya._fetch = lambda name, cid: (dict(GOOD_DATA), {})
try:
    out = scheduler.JOBS["youtube_analytics_snapshot"][1](Flask("t"))
    check("the scheduler job returns the sweep's own answer", "ran" in out)
    check("...registered ticking hourly",
          (scheduler.JOBS["youtube_analytics_snapshot"][0],
           scheduler.JOBS["youtube_analytics_snapshot"][1].__name__),
          (60, "job_youtube_analytics_snapshot"))
finally:
    ya._fetch = _real_fetch


# ---------------------------------------------------------------------------
section("The quota row and the host, in the Analytics API's own bucket")
# ---------------------------------------------------------------------------

check("youtubeanalytics.googleapis.com is its own bucket, apart from the Data API v3's",
      quotas.google_api_of("https://youtubeanalytics.googleapis.com/v2/reports"), "youtube_analytics")
check("...and the Data API v3's own host is unaffected",
      quotas.google_api_of("https://youtube.googleapis.com/youtube/v3/channels"), "youtube")
check("...with a row in the per-API table naming it a separate quota",
      "youtube_analytics" in quotas.GOOGLE_APIS)


# ---------------------------------------------------------------------------
section("modules/reports/youtube.py: the Analytics half rides beside the gate, never gates it")
# ---------------------------------------------------------------------------

_real_rows = reports_youtube._product_rows
reports_youtube._product_rows = lambda: [
    {"client": "Acme Plumbing", "product": "YouTube TrueView", "tactics": "video pre-roll",
     "status": "live", "start": "2026-01-01", "end": "2099-01-01"},
]
_real_running = reports_youtube._running
reports_youtube._running = lambda row: True
try:
    g = reports_youtube.gate("d:acmeplumbing.example.com", "Acme Plumbing")
    check("gated in on the product and the confirmed channel", g["gated"], True)
    check("...and carries the analytics state alongside it, staff-only",
          "analytics_state" not in g, True)  # staff_gate adds it, gate() itself does not

    sg = reports_youtube.staff_gate("d:acmeplumbing.example.com", "Acme Plumbing")
    check("staff_gate carries the Analytics state", sg.get("analytics_state"), "ok")

    class _Link:
        client = "d:acmeplumbing.example.com"
        client_name = "Acme Plumbing"

    block = reports_youtube.section(_Link(), TODAY, gate_=g)
    check("the section carries the keyed counts and the Analytics half together",
          (block["measured"], block.get("analytics") is not None), (True, True))
    check("...the analytics sub-block has the period figures",
          block["analytics"]["watch_minutes"], 8420.5)

    pv = reports_youtube.public_view(block)
    check("public_view keeps analytics when it is measured", pv.get("analytics") is not None, True)
    check("...and never the staff note key", "analytics_staff_note" in pv, False)

    # Now the same client, but with the connection pointing at nothing (as
    # if the channel had been cleared and re-confirmed to one with no
    # connection): the Analytics half must not gate the keyed section off.
    ya_reading_real = ya.reading
    ya.reading = lambda name, today=None: {"measured": False, "state": "not_connected",
                                            "staff_note": "not connected"}
    try:
        block2 = reports_youtube.section(_Link(), TODAY, gate_=g)
        check("with the Analytics half unavailable, the keyed section is still on",
              block2["measured"], True)
        check("...and analytics is simply absent", block2.get("analytics"), None)
        pv2 = reports_youtube.public_view(block2)
        check("...never reaching the client's page as a key at all",
              "analytics" in pv2, False)
    finally:
        ya.reading = ya_reading_real
finally:
    reports_youtube._product_rows = _real_rows
    reports_youtube._running = _real_running


# ---------------------------------------------------------------------------
section("The /api/client/youtube routes carry the Analytics half")
# ---------------------------------------------------------------------------

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

r = staff.get("/api/client/youtube?name=Acme%20Plumbing")
d = r.get_json()
check("the GET carries an analytics key alongside the keyed reading",
      (r.status_code, "analytics" in d, d["analytics"]["state"]), (200, True, "ok"))

r = staff.get("/api/client/youtube?name=Unconnected%20Co")
d = r.get_json()
check("...not_connected for a confirmed channel with no youtube_studio connection",
      d["analytics"]["state"], "not_connected")

_real_studio_fetch = ya._fetch
ya._fetch = lambda name, cid: (dict(GOOD_DATA), {})
try:
    r = staff.post("/api/client/youtube/refresh", json={"name": "Acme Plumbing"})
    d = r.get_json()
    check("one refresh press refreshes both halves",
          (r.status_code, d["ok"], d["analytics"]["ok"]), (200, True, True))
finally:
    ya._fetch = _real_studio_fetch

anon = Client(wsgi.application)
r = anon.get("/api/client/youtube?name=Acme%20Plumbing")
check("a stranger is refused here too, the shape it always was",
      r.status_code in (302, 401), True)


# ---------------------------------------------------------------------------
section("The Client 360 card's Analytics sub-block, lifted and driven in node")
# ---------------------------------------------------------------------------

def _c360_source():
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()


_REC = _c360_source()
_a = _REC.find("/* ---- c360 youtube channel (lifted")
_b = _REC.find("/* ---- end c360 youtube channel ----")
check("the card block (now carrying renderYtAnalytics too) is still marked for lifting",
      0 < _a < _b)
_SRC = _REC[_a:_b] if 0 < _a < _b else ""
check("renderYtAnalytics is inside the lifted block", "function renderYtAnalytics" in _SRC)
check("the second, previously-colliding YouTube Studio card has its own id now",
      'id="c-youtube-studio"' in _REC and 'id="c-youtube"' in _REC
      and _REC.count('id="c-youtube"') == 1)

_analytics_payloads = [
    None,
    {"state": "not_connected"},
    {"state": "no_reading"},
    {"state": "unread", "error": "needs reconnecting"},
    {"state": "ok", "watch_minutes": 8420.5, "subscribers_net": 32, "views": 5100,
     "sources": [{"label": "YouTube search", "views": 2000}, {"label": "Links from other websites", "views": 900}],
     "start": "2026-08-18", "end": "2026-09-14", "as_of": "2026-09-14"},
]
_driver = ("function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;')"
           ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + _SRC + "\nconst A=" + json.dumps(_analytics_payloads) + ";\n"
           "console.log(JSON.stringify(A.map(renderYtAnalytics)));\n")
_r = subprocess.run(["node", "-"], input=_driver, capture_output=True, text=True)
check("renderYtAnalytics runs on its own in node", _r.returncode, 0, note=_r.stderr)
_out = json.loads(_r.stdout or "[]") if _r.returncode == 0 else []
check("all five payloads rendered", len(_out), 5)
check("no key/undefined state renders nothing", _out[0], "")
check("not_connected names YouTube Studio", "YouTube Studio" in _out[1])
check("no_reading says so plainly", "not been read yet" in _out[2])
check("unread carries the error", "needs reconnecting" in _out[3])
check("ok draws the watch-time figure", "8,421" in _out[4] or "8420" in _out[4] or "8,420" in _out[4])
check("...and the top traffic sources", "YouTube search" in _out[4] and "Links from other websites" in _out[4])


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
