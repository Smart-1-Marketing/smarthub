"""A client's YouTube channel, read through hub/youtube.py.

    python3 test_youtube.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, through the COMPOSED app. YouTube is never reached: the
wire is stood in for at `requests.get` where the call's own error mapping
is what is being asserted, and at `hub.youtube._call` or
`hub.youtube.details` everywhere else.

What it holds:

  * the key is a setting that falls back to the Places key and says which
    answered, the host is a quota row metered in units, the check is on
    /diagnostics, the log name is declared as a join rather than work, the
    nightly job is registered, and "YouTube" is an allowed word on the
    client's page with its reason;
  * every way a call fails is named apart -- refused, quota, unreachable,
    an HTTP status -- each is recorded on the usage page with the units it
    spent, and the key never reaches a message or the recorded URL;
  * a pasted string is read as a channel id, a handle, a user address, a
    non-channel YouTube address or words, and a link resolves for one unit
    before a search spends a hundred;
  * a lookup proposes exactly one channel or none: the channel a link
    named, the only result, or the only result titled exactly with the
    client's name; two propose neither;
  * a confirmation is keyed on the client's name, matched exactly and
    never on a substring, reads the channel on the press, and logs under
    the client; a read that fails still stores the confirmation and says so;
  * five kinds of nothing come back apart, a hidden subscriber count is
    hidden and never zero, and the 30-day change is not measured until a
    reading that old exists;
  * one reading per client per day, capped;
  * the nightly sweep reads each confirmed channel once inside the window,
    skips one already read today, stops on a refused key or a spent quota,
    and names what a budget left unread;
  * every route refuses a stranger and answers staff;
  * the Client 360 card's renderer, lifted from the template and driven in
    node, draws each state apart;
  * the client's report page carries the section only for a client with a
    live video or social product AND a confirmed, read channel -- on the
    page, in data.json and in the PDF, with no staff note and no vendor
    word -- and the staff page prints the gate.
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

TMP = tempfile.mkdtemp(prefix="s1youtube_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
import _reports_testdb                                               # noqa: E402
_reports_testdb.bind(TMP)
os.environ["REPORTS_PROVIDER_SCHEMA"] = ""
os.environ["SECRET_KEY"] = "youtube-test-secret"
os.environ["PUBLIC_BASE_URL"] = "https://hub.example.test"
# Only the Places key: the YouTube module falls back to it (the same
# Cloud project) and the fallback is what this deployment runs on.
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
from hub import auth, youtube, quotas, client_brand, scheduler, diagnostics  # noqa: E402
from hub.config import settings                                      # noqa: E402
from modules.reports import products, store                          # noqa: E402

KEY = os.environ["GOOGLE_PLACES_API_KEY"]
TODAY = date(2026, 9, 14)
CID_A = "UCaaaaaaaaaaaaaaaaaaaaaa"
CID_B = "UCbbbbbbbbbbbbbbbbbbbbbb"
CID_C = "UCcccccccccccccccccccccc"


def _item(cid=CID_A, title="Acme Plumbing", handle="@acmeplumbing", subs="340", views="12000",
          videos="18", hidden=False):
    st = {"viewCount": views, "videoCount": videos, "hiddenSubscriberCount": hidden}
    if not hidden:
        st["subscriberCount"] = subs
    return {"id": cid, "snippet": {"title": title, "customUrl": handle, "publishedAt": "2019-03-01T00:00:00Z",
                                   "thumbnails": {"default": {"url": f"https://yt.example/{cid}.jpg"}}},
            "statistics": st}


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
section("The key and its fallback, the quota row, the check, the log name and the job")
# ---------------------------------------------------------------------------

check("with only the Places key set, the module reads it", youtube.api_key(), KEY)
check("...and says which variable answered", youtube.key_source(), "GOOGLE_PLACES_API_KEY")
check("...so the module is configured", youtube.configured(), True)
check("the YouTube key is a setting read through hub/config.py", hasattr(settings, "youtube_key"))
object.__setattr__(settings, "youtube_key", "own-youtube-key")
try:
    check("a YouTube key of its own wins over the Places key",
          (youtube.api_key(), youtube.key_source()), ("own-youtube-key", "YOUTUBE_API_KEY"))
finally:
    object.__setattr__(settings, "youtube_key", "")
check("the YouTube host is a Google API the usage page can name",
      quotas.google_api_of("https://youtube.googleapis.com/youtube/v3/channels"), "youtube")
check("...with a row in the per-API table citing Google's published 10,000 units",
      ("youtube" in quotas.GOOGLE_APIS, quotas.GOOGLE_APIS["youtube"][1]), (True, 10000))
check("...and the row says it is in units, not requests", "units" in quotas.GOOGLE_APIS["youtube"][3])
check("a search is a hundred units and a channel read is one, by name",
      (youtube.SEARCH_UNITS, youtube.LIST_UNITS), (100, 1))
check("the key check is on /diagnostics", diagnostics.check_youtube in diagnostics.CHECKS)
check("the log name is declared as a join rather than work", "youtube" in client_brand.NOT_WORK)
check("the nightly job is registered, ticking hourly",
      (scheduler.JOBS["youtube_snapshot"][0], scheduler.JOBS["youtube_snapshot"][1].__name__),
      (60, "job_youtube_snapshot"))
check("env.example documents the key", "YOUTUBE_API_KEY" in (ROOT / "env.example").read_text(encoding="utf-8"))
check("...and render.yaml carries it", "YOUTUBE_API_KEY" in (ROOT / "render.yaml").read_text(encoding="utf-8"))
check("'YouTube' is allowed on the client's page, with its reason, and is not a forbidden word",
      ("YouTube" in products.ALLOWED, products.forbidden_hits("YouTube channel")), (True, []))
check("only snippet and statistics are asked for", youtube.CHANNEL_PARTS, "snippet,statistics")


# ---------------------------------------------------------------------------
section("The wire: every way a call fails is named, recorded in units, and never carries the key")
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


def _fake_get(url, params=None, timeout=None, **kw):
    _sent.append((url, dict(params or {})))
    a = _answers.pop(0)
    if isinstance(a, Exception):
        raise a
    return a


def _err(reason, message="denied"):
    return {"error": {"code": 403, "message": message, "errors": [{"reason": reason}]}}


_real_get = requests.get
_real_record = quotas.record_google
requests.get = _fake_get
quotas.record_google = lambda url, *, module, ok=True, units=1: _recorded.append((url, module, ok, units))
try:
    _answers[:] = [_Resp(403, _err("accessNotConfigured", f"key {KEY} has no access"))]
    data, err = youtube._call(youtube.CHANNELS_URL, {"part": "id", "id": CID_A}, units=1)
    check("403 is refused, naming the reason, the variable read and the fix",
          (data, err["kind"], "accessNotConfigured" in err["message"], "GOOGLE_PLACES_API_KEY" in err["message"],
           "API restrictions" in err["message"]), (None, "refused", True, True, True))
    check("...recorded against the usage page as a failed one-unit call",
          _recorded[-1], (youtube.CHANNELS_URL, "youtube", False, 1))
    check("...and the key rode in the query params, never in the message or the recorded URL",
          _sent[-1][1]["key"] == KEY and KEY not in err["message"] and KEY not in _recorded[-1][0])
    _answers[:] = [_Resp(400, _err("keyInvalid", "API key not valid"))]
    _, err = youtube._call(youtube.CHANNELS_URL, {"part": "id", "id": CID_A}, units=1)
    check("a 400 keyInvalid is refused, not an HTTP status", err["kind"], "refused")
    _answers[:] = [_Resp(403, _err("quotaExceeded"))]
    _, err = youtube._call(youtube.SEARCH_URL, {"q": "x"}, units=100)
    check("403 quotaExceeded is rate-limited, apart from refused", err["kind"], "rate_limited")
    check("...recorded as a failed hundred-unit call", _recorded[-1][2:], (False, 100))
    _answers[:] = [_Resp(429, {})]
    _, err = youtube._call(youtube.SEARCH_URL, {"q": "x"}, units=100)
    check("429 is rate-limited too", err["kind"], "rate_limited")
    _answers[:] = [requests.Timeout()]
    _, err = youtube._call(youtube.SEARCH_URL, {"q": "x"}, units=100)
    check("a timeout is unreachable, never a bad key", err["kind"], "unreachable")
    check("...and still recorded as a failed call", _recorded[-1][2], False)
    _answers[:] = [_Resp(500, {})]
    _, err = youtube._call(youtube.SEARCH_URL, {"q": "x"}, units=100)
    check("a 500 is an HTTP status, said as one", (err["kind"], "500" in err["message"]), ("http", True))
    _answers[:] = [_Resp(200, None, bad_json=True)]
    _, err = youtube._call(youtube.SEARCH_URL, {"q": "x"}, units=100)
    check("a body that is not JSON is refused rather than read as empty", err["kind"], "http")
    _answers[:] = [_Resp(200, {"items": [_item()]})]
    data, err = youtube._call(youtube.CHANNELS_URL, {"part": "snippet,statistics", "id": CID_A}, units=1)
    check("a 200 answers the data with no error", (err, len(data["items"])), ({}, 1))
    check("...recorded as an ok one-unit call", _recorded[-1][2:], (True, 1))
    check("...with the params passed through beside the key", _sent[-1][1]["id"], CID_A)
finally:
    requests.get = _real_get
    quotas.record_google = _real_record

check("the module records every call through quotas.record_google, by name",
      "quotas.record_google(" in (ROOT / "hub" / "youtube.py").read_text(encoding="utf-8"))
row = youtube._channel_row(_item(hidden=True))
check("a hidden subscriber count is None and flagged, never zero",
      (row["subscribers"], row["subscribers_hidden"], row["views"]), (None, True, 12000))
check("the channel's own address is its handle, else its id",
      (youtube._channel_row(_item())["url"], youtube._channel_row(_item(handle=""))["url"]),
      ("https://www.youtube.com/@acmeplumbing", f"https://www.youtube.com/channel/{CID_A}"))


# ---------------------------------------------------------------------------
section("What a pasted string names")
# ---------------------------------------------------------------------------

check("a channel id", youtube.parse_ref(CID_A), ("id", CID_A))
check("...inside a channel address", youtube.parse_ref(f"https://www.youtube.com/channel/{CID_A}?x=1"), ("id", CID_A))
check("a handle address", youtube.parse_ref("https://youtube.com/@AcmePlumbing/videos"), ("handle", "@AcmePlumbing"))
check("a bare handle", youtube.parse_ref("@acme"), ("handle", "@acme"))
check("a user address", youtube.parse_ref("https://www.youtube.com/user/acmeplumb"), ("user", "acmeplumb"))
check("a legacy custom address is read as a handle", youtube.parse_ref("youtube.com/c/AcmePlumbing"), ("handle", "@AcmePlumbing"))
check("a video address is a YouTube address the API cannot resolve to a channel",
      youtube.parse_ref("https://www.youtube.com/watch?v=dQw4w9WgXcQ")[0], "url")
check("...and so is a short link", youtube.parse_ref("https://youtu.be/dQw4w9WgXcQ")[0], "url")
check("words are a search", youtube.parse_ref("Acme Plumbing Columbus"), ("search", "Acme Plumbing Columbus"))
check("nothing is nothing", youtube.parse_ref("  "), ("", ""))


# ---------------------------------------------------------------------------
section("Candidates: a link first, a search second, one proposal or none")
# ---------------------------------------------------------------------------

_calls = []
_real_call = youtube._call
_book = {CID_A: _item(CID_A, "Acme Plumbing", "@acmeplumbing"),
         CID_B: _item(CID_B, "Acme Plumbing Supply", "@acmesupply"),
         CID_C: _item(CID_C, "Acme Plumbing", "@acmeplumbingtx")}
_search_ids = []
_wire_error = None


def _stub_call(url, params, *, units):
    _calls.append((url, dict(params), units))
    if _wire_error:
        return None, dict(_wire_error)
    if url == youtube.SEARCH_URL:
        return {"items": [{"id": {"channelId": c}} for c in _search_ids]}, {}
    if params.get("forHandle"):
        want = params["forHandle"].lower()
        hits = [i for i in _book.values() if i["snippet"]["customUrl"].lower() == want]
        return {"items": hits[:1]}, {}
    if params.get("forUsername"):
        return {"items": []}, {}
    ids = str(params.get("id") or "").split(",")
    return {"items": [_book[c] for c in ids if c in _book]}, {}


youtube._call = _stub_call
try:
    c = youtube.candidates("Acme Plumbing", hint="https://www.youtube.com/@acmeplumbing")
    check("the link on the record resolves for one unit and is proposed",
          (c["measured"], c["proposed"], c["units"], c["how"]), (True, CID_A, 1, "link"))
    check("...saying so", "link on their record" in c["why"] and "@acmeplumbing" in c["why"])
    check("...with no search made", [u for u, _p, _n in _calls if u == youtube.SEARCH_URL], [])
    _calls[:] = []
    c = youtube.candidates("Acme Plumbing", query=f"https://youtube.com/channel/{CID_C}", hint="https://youtube.com/@acmeplumbing")
    check("a link the rep gives wins over the record's", (c["proposed"], c["units"]), (CID_C, 1))
    check("...saying it was the link they gave", "link you gave" in c["why"])
    _calls[:] = []
    c = youtube.candidates("Acme Plumbing", query="@nobodyhere")
    check("a link the rep gives that resolves nothing says so and does not search",
          (c["measured"], c["proposed"], c["candidates"], "no channel answers" in c["why"],
           [u for u, _p, _n in _calls if u == youtube.SEARCH_URL]), (True, None, [], True, []))
    c = youtube.candidates("Acme Plumbing", query="https://www.youtube.com/watch?v=abc")
    check("a video address is refused in words, with no call at all",
          (c["measured"], c["proposed"], "not a channel" in c["why"], c["units"]), (True, None, True, 0))

    _search_ids[:] = [CID_A]
    _calls[:] = []
    c = youtube.candidates("Acme Plumbing")
    check("with no link, a search: one result is proposed as the only channel",
          (c["proposed"], c["how"], c["units"]), (CID_A, "search", 101))
    check("...the search asked for channels carrying the name, at most MAX_CANDIDATES",
          (_calls[0][1]["type"], _calls[0][1]["q"], _calls[0][1]["maxResults"]), ("channel", "Acme Plumbing", youtube.MAX_CANDIDATES))
    check("...and one channels.list for the figures", (_calls[1][0], _calls[1][2]), (youtube.CHANNELS_URL, 1))
    _search_ids[:] = [CID_B, CID_A]
    c = youtube.candidates("Acme Plumbing", query="Columbus")
    check("several results: the only one titled exactly with the client's name is proposed", c["proposed"], CID_A)
    check("...the search carried what the rep typed", c["query"], "Acme Plumbing Columbus")
    check("...every row says whether its title matched", [(r["channel_id"], r["name_match"]) for r in c["candidates"]],
          [(CID_B, False), (CID_A, True)])
    check("...and a candidate carries the figures a rep picks on",
          (c["candidates"][1]["subscribers"], c["candidates"][1]["handle"]), (340, "@acmeplumbing"))
    _search_ids[:] = [CID_A, CID_C]
    c = youtube.candidates("Acme Plumbing")
    check("two results carrying the client's exact name propose neither", c["proposed"], None)
    check("...naming both", "2 channels" in c["why"])
    _search_ids[:] = [CID_B]
    c = youtube.candidates("Acme Plumbing", query="supply")
    check("one result is proposed even when its title differs", c["proposed"], CID_B)
    _search_ids[:] = [CID_B, CID_B]
    c = youtube.candidates("Acme Plumbing")
    check("a duplicate search hit is one candidate", len(c["candidates"]), 1)
    _search_ids[:] = []
    c = youtube.candidates("Acme Plumbing")
    check("no result is measured and proposes nobody", (c["measured"], c["proposed"], c["candidates"]), (True, None, []))
    _search_ids[:] = [CID_A]
    c = youtube.candidates("Acme Plumbing", hint="https://youtube.com/@gonehandle")
    check("a record link that resolves nothing falls through to the search and says so",
          (c["proposed"], c["how"], "link on their record" in c["why"] and "searched by name" in c["why"]),
          (CID_A, "search", True))
    c = youtube.candidates("Acme Plumbing", query="Columbus", hint="https://youtube.com/@acmeplumbing")
    check("words typed beside a linked record are a request to search, not to re-read the link",
          (c["how"], c["query"]), ("search", "Acme Plumbing Columbus"))
    _wire_error = {"kind": "refused", "message": "Google refused the YouTube key (403)."}
    c = youtube.candidates("Acme Plumbing")
    check("a refused key is not measured, with the kind", (c["measured"], c["kind"]), (False, "refused"))
    _wire_error = None
    c = youtube.candidates("", query="")
    check("nothing to search for is said rather than sent", c["kind"], "empty")
finally:
    youtube._call = _real_call

_real_key = youtube._key
youtube._key = lambda: ("", "")
try:
    c = youtube.candidates("Acme Plumbing")
    check("with no key the lookup is unconfigured, naming both variables",
          (c["kind"], "YOUTUBE_API_KEY" in c["error"] and "GOOGLE_PLACES_API_KEY" in c["error"]), ("unconfigured", True))
finally:
    youtube._key = _real_key

d = youtube.details("not a channel id!")
check("a string that is not a channel id is refused before any call", d["kind"], "bad_id")
youtube._call = lambda url, params, *, units: ({"items": []}, {})
try:
    check("an id nothing answers to is not found, said as that", youtube.details(CID_A)["kind"], "not_found")
finally:
    youtube._call = _real_call


# ---------------------------------------------------------------------------
section("Confirm: keyed on the name, read on the press, logged under the client")
# ---------------------------------------------------------------------------

_details = []
_real_details = youtube.details


def _fake_details(cid):
    if _details:
        return _details.pop(0)
    return {"measured": True, "error": "", "kind": "", "channel": youtube._channel_row(_book.get(cid) or _item(cid))}


youtube.details = _fake_details
try:
    res = youtube.confirm("Acme Plumbing", CID_A, actor="Todd", today=TODAY)
    check("confirming stores the record and reads the channel on the press",
          (res["ok"], res["reading"]["state"], res["reading"]["subscribers"], res["reading"]["views"],
           res["reading"]["videos"]), (True, "ok", 340, 12000, 18))
    check("...keyed on the client's name", sorted(youtube.all_records()), ["Acme Plumbing"])
    check("...carrying the channel's own title, handle, address and who confirmed",
          (res["record"]["title"], res["record"]["handle"], res["record"]["url"], res["record"]["confirmed_by"]),
          ("Acme Plumbing", "@acmeplumbing", "https://www.youtube.com/@acmeplumbing", "Todd"))
    check("the record is found on the exact normalized name", (youtube.record("acme plumbing") or {}).get("channel_id"), CID_A)
    check("...and never on a substring", youtube.record("Acme"), None)
    check("...nor a longer name", youtube.record("Acme Plumbing Supply"), None)
    logged = [e for e in entries() if e.get("module") == "youtube" and e.get("type") == "channel_confirmed"]
    check("the confirmation is in the activity log under the client, with who",
          (len(logged), logged[-1].get("client"), logged[-1].get("actor")), (1, "Acme Plumbing", "Todd"))
    check("the 30-day change is not measured on the first day, and says why",
          (res["reading"]["change"]["measured"], "First reading" in res["reading"]["change"]["note"]), (False, True))
    youtube.confirm("Acme Plumbing", CID_A, actor="Todd", today=TODAY)
    check("confirming again the same day replaces the reading rather than stacking one",
          len(youtube.readings("Acme Plumbing")), 1)
    _details[:] = [{"measured": False, "error": "YouTube did not answer in time.", "kind": "unreachable", "channel": None}]
    res3 = youtube.confirm("Beta LLC", CID_B, actor="Todd", today=TODAY)
    check("a read that fails still stores the confirmation", (res3["ok"], "Beta LLC" in youtube.all_records()), (True, True))
    check("...and the reading says the read failed rather than drawing nothing",
          (res3["reading"]["state"], res3["reading"]["error"]), ("unread", "YouTube did not answer in time."))
    _details[:] = [{"measured": True, "error": "", "kind": "", "channel": youtube._channel_row(_item(CID_C, "Hidden Co", "@hiddenco", hidden=True))}]
    res4 = youtube.confirm("Hidden Co", CID_C, actor="Todd", today=TODAY)
    check("a channel hiding its subscriber count is read: hidden, never zero, views beside it",
          (res4["reading"]["state"], res4["reading"]["subscribers"], res4["reading"]["subscribers_hidden"], res4["reading"]["views"]),
          ("ok", None, True, 12000))
    res5 = youtube.confirm("Gamma Inc", "bad id!", actor="Todd")
    check("a bad channel id is refused, nothing stored", (res5["ok"], "Gamma Inc" in youtube.all_records()), (False, False))
    check("a confirmation needs both a client and an id", youtube.confirm("", CID_A, actor="Todd")["ok"], False)
finally:
    youtube.details = _real_details


# ---------------------------------------------------------------------------
section("The reading: five kinds of nothing, and a hidden count never a zero")
# ---------------------------------------------------------------------------

youtube._key = lambda: ("", "")
try:
    r = youtube.reading("Acme Plumbing", today=TODAY)
    check("no key is unconfigured, named for staff, and never measured",
          (r["state"], r["measured"], "YOUTUBE_API_KEY" in r["staff_note"]), ("unconfigured", False, True))
    check("...and the client's page gets nothing from it", youtube.public_view(r), None)
finally:
    youtube._key = _real_key

r = youtube.reading("Nobody Here", today=TODAY)
check("no channel confirmed is no_channel", (r["state"], r["record"]), ("no_channel", None))
r = youtube.reading("Beta LLC", today=TODAY)
check("a channel whose only read failed is unread, with the error", (r["state"], r["error"]), ("unread", "YouTube did not answer in time."))
check("...and the client's page gets nothing from it", youtube.public_view(r), None)

from hub import jsonstore                                            # noqa: E402
jsonstore.update_json(youtube._channels_path(),
                      lambda d: {**(d or {}), "Delta Co": {"channel_id": CID_B, "title": "Delta"}})
r = youtube.reading("Delta Co", today=TODAY)
check("confirmed and never read is no_snapshot", r["state"], "no_snapshot")

r = youtube.reading("Acme Plumbing", today=TODAY)
check("a good reading is ok, with the three counts and the day",
      (r["state"], r["subscribers"], r["views"], r["videos"], r["as_of"], r["key_source"]),
      ("ok", 340, 12000, 18, TODAY.isoformat(), "GOOGLE_PLACES_API_KEY"))
pv = youtube.public_view(r)
check("the public view carries the figures, the channel's own name and link, the date, and nothing about our tooling",
      (pv["subscribers"], pv["views"], pv["title"], pv["url"], pv["as_of"], "staff_note" in pv, "record" in pv, "key_source" in pv),
      (340, 12000, "Acme Plumbing", "https://www.youtube.com/@acmeplumbing", TODAY.isoformat(), False, False, False))
check("card_for is the public view", youtube.card_for("Acme Plumbing", today=TODAY), pv)
r = youtube.reading("Hidden Co", today=TODAY)
check("a hidden count reads as ok and hidden, with the views beside it",
      (r["state"], r["subscribers"], r["subscribers_hidden"], r["views"]), ("ok", None, True, 12000))
check("...and the public view keeps the flag", youtube.public_view(r)["subscribers_hidden"], True)

# The 30-day change needs a reading 30 days old.
youtube._append_reading("Acme Plumbing", youtube._channel_row(_item(subs="300", views="10000", videos="15")), today=TODAY - timedelta(days=45))
youtube._append_reading("Acme Plumbing", youtube._channel_row(_item(subs="320", views="11000", videos="16")), today=TODAY - timedelta(days=31))
youtube._append_reading("Acme Plumbing", youtube._channel_row(_item(subs="330", views="11500", videos="17")), today=TODAY - timedelta(days=20))
r = youtube.reading("Acme Plumbing", today=TODAY)
ch = r["change"]
check("with a reading 31 days old the change is measured against it, not the 45-day one",
      (ch["measured"], ch["since"]), (True, (TODAY - timedelta(days=31)).isoformat()))
check("...20 subscribers, 1,000 views and 2 videos gained",
      (ch["subscribers_delta"], ch["views_delta"], ch["videos_delta"]), (20, 1000, 2))
check("...and the 20-day reading is not the base", ch["base_views"], 11000)
check("the newest good reading is still the one drawn", (r["views"], r["as_of"]), (12000, TODAY.isoformat()))
youtube._append_reading("Hidden Co", youtube._channel_row(_item(CID_C, hidden=True, views="9000")), today=TODAY - timedelta(days=31))
ch = youtube.reading("Hidden Co", today=TODAY)["change"]
check("a hidden count has no subscriber delta and still a views delta", (ch["subscribers_delta"], ch["views_delta"]), (None, 3000))

youtube._append_reading("Acme Plumbing", None, today=TODAY + timedelta(days=1), error="HTTP 500")
r = youtube.reading("Acme Plumbing", today=TODAY + timedelta(days=1))
check("a failed newest read keeps drawing the last good reading", (r["state"], r["views"], r["as_of"]), ("ok", 12000, TODAY.isoformat()))
check("...and the staff note says the newest failed", "failed" in r["staff_note"] and "HTTP 500" in r["staff_note"])
check("...while the client's page carries no staff note", "staff_note" in (youtube.public_view(r) or {}), False)
r = youtube.reading("Acme Plumbing", today=TODAY + timedelta(days=5))
check("an old reading says how old it is", r["note"], "Read 5 days ago.")


# ---------------------------------------------------------------------------
section("One reading per day, and a cap")
# ---------------------------------------------------------------------------

before = len(youtube.readings("Acme Plumbing"))
youtube._append_reading("Acme Plumbing", youtube._channel_row(_item(subs="345")), today=TODAY)
check("a second reading on one day replaces the first", len(youtube.readings("Acme Plumbing")), before)
check("...with the newer figures", youtube.readings("Acme Plumbing")[1]["subscribers"], 345)
for i in range(youtube.KEEP_READINGS + 20):
    youtube._append_reading("Cap Co", youtube._channel_row(_item()), today=TODAY - timedelta(days=i))
check("the readings file is capped, newest kept",
      (len(youtube.readings("Cap Co")), youtube.readings("Cap Co")[0]["day"]), (youtube.KEEP_READINGS, TODAY.isoformat()))


# ---------------------------------------------------------------------------
section("The nightly sweep: once inside the window, once per channel, bounded")
# ---------------------------------------------------------------------------

_read = []


def _counting_details(cid):
    _read.append(cid)
    if cid == CID_B:
        return {"measured": False, "error": "Google refused the YouTube key (403).", "kind": "refused", "channel": None}
    return {"measured": True, "error": "", "kind": "", "channel": youtube._channel_row(_book.get(cid) or _item(cid))}


youtube.details = _counting_details
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
try:
    jsonstore.delete_json(youtube._state_path())
    out = youtube.sweep(force=False, today=date(2026, 9, 15), now=NOW)
    check("never run is due, so the first hourly tick sweeps", out["ran"], True)
    # Four records: Acme Plumbing, Beta LLC (refused), Delta Co (also CID_B,
    # refused) and Hidden Co. Cap Co has readings and no record. In name
    # order the refused key stops the sweep at Beta.
    check("...every confirmed channel accounted for: read, failed, already or left",
          (out["clients"], out["read"] + out["failed"] + out["already"] + out["left"]), (4, 4))
    check("a refused key stops the sweep rather than recording the refusal per client",
          (out["failed"], out["left"] >= 1, "Beta LLC" in out["errors"]), (1, True, True))
    st = youtube.sweep_state()
    check("the state records the run, and which key it ran on",
          (st["last_run_at"], st["read"], st["key_source"]), (NOW.isoformat(timespec="seconds"), out["read"], "GOOGLE_PLACES_API_KEY"))
    check("...so it is not due again inside the window", youtube.due_for_refresh(NOW + timedelta(hours=2)), False)
    check("...and is due once the window has passed", youtube.due_for_refresh(NOW + timedelta(days=1)), True)
    n = len(_read)
    check("not due is a sentence, not a call",
          (youtube.sweep(force=False, now=NOW + timedelta(hours=1))["skipped"], len(_read)), ("Not due yet.", n))

    jsonstore.update_json(youtube._channels_path(), lambda d: {k: v for k, v in (d or {}).items() if k not in ("Beta LLC", "Delta Co")})
    n = len(_read)
    out = youtube.sweep(force=True, today=date(2026, 9, 15), now=NOW)
    check("a channel already read today is skipped, the rest read", (out["already"] >= 1, out["failed"], len(_read) > n), (True, 0, True))

    tick = [0.0]

    def _clock():
        tick[0] += 100.0
        return tick[0]

    out = youtube.sweep(force=True, today=date(2026, 9, 16), now=NOW + timedelta(days=1), budget=90, clock=_clock)
    check("past the budget the sweep stops and names what is left",
          (out["ran"], out["left"] >= 1, out["read"] + out["already"] + out["left"]), (True, True, out["clients"]))

    # A spent quota is spent for every client: stop, like a refused key.
    youtube.details = lambda cid: {"measured": False, "kind": "rate_limited", "channel": None,
                                   "error": "YouTube refused for quota (quotaExceeded)."}
    out = youtube.sweep(force=True, today=date(2026, 9, 17), now=NOW + timedelta(days=2))
    check("a spent quota stops the sweep after the first refusal", (out["failed"], out["left"]), (1, out["clients"] - 1))
finally:
    youtube.details = _real_details

youtube._key = lambda: ("", "")
try:
    check("unconfigured, the sweep skips by name", "YOUTUBE_API_KEY" in youtube.sweep(force=True)["skipped"])
finally:
    youtube._key = _real_key

from flask import Flask                                              # noqa: E402
youtube.details = lambda cid: {"measured": True, "error": "", "kind": "", "channel": youtube._channel_row(_item(cid))}
try:
    out = scheduler.JOBS["youtube_snapshot"][1](Flask("t"))
    check("the scheduler job returns the sweep's own answer", "ran" in out and "skipped" in out)
finally:
    youtube.details = _real_details


# ---------------------------------------------------------------------------
section("Clear: not this channel")
# ---------------------------------------------------------------------------

res = youtube.clear("Acme Plumbing", actor="Todd")
check("clearing removes the record", (res["ok"], youtube.record("Acme Plumbing")), (True, None))
check("...keeps the readings", len(youtube.readings("Acme Plumbing")) > 0)
check("...and the reading is no_channel again", youtube.reading("Acme Plumbing", today=TODAY)["state"], "no_channel")
check("...logged under the client", [e for e in entries() if e.get("type") == "channel_cleared"][-1].get("client"), "Acme Plumbing")
check("clearing nothing says so", youtube.clear("Acme Plumbing")["ok"], False)


# ---------------------------------------------------------------------------
section("The routes: a stranger is refused, staff are answered, every POST is a press")
# ---------------------------------------------------------------------------

anon = Client(wsgi.application)
for path, method in (("/api/client/youtube?name=Acme%20Plumbing", "GET"),
                     ("/api/client/youtube/lookup", "POST"), ("/api/client/youtube/confirm", "POST"),
                     ("/api/client/youtube/refresh", "POST"), ("/api/client/youtube/clear", "POST")):
    r = anon.open(path, method=method, json={"name": "Acme Plumbing"} if method == "POST" else None)
    check(f"{method} {path.split('?')[0]} refuses a stranger",
          r.status_code in (302, 401) and (r.status_code == 401 or r.headers.get("Location", "").startswith("/login")))

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")
r = staff.get("/api/client/youtube?name=Acme%20Plumbing")
d = r.get_json()
check("the GET answers the reading with the record's hint and the sweep beside it",
      (r.status_code, d["state"], "hint" in d, "sweep" in d, d["key_source"]), (200, "no_channel", True, True, "GOOGLE_PLACES_API_KEY"))
check("a GET with no name is refused", staff.get("/api/client/youtube").status_code, 400)

from hub import seo as hub_seo                                       # noqa: E402
hub_seo.set_social("Acme Plumbing", {"youtube": "https://www.youtube.com/@acmeplumbing"})
_hint = staff.get("/api/client/youtube?name=Acme%20Plumbing").get_json().get("hint")
check("the hint is the YouTube link on the client's SEO record", _hint, "https://www.youtube.com/@acmeplumbing")

youtube.details = lambda cid: {"measured": True, "error": "", "kind": "", "channel": youtube._channel_row(_book.get(cid) or _item(cid))}
_real_cands = youtube.candidates
_asked = []
youtube.candidates = lambda name, query="", hint="": (_asked.append((name, query, hint)) or
    {"measured": True, "error": "", "kind": "", "query": name, "hint": hint, "how": "search", "units": 101,
     "candidates": [youtube._channel_row(_item())], "proposed": CID_A, "why": "the only channel"})
try:
    r = staff.post("/api/client/youtube/lookup", json={"name": "Acme Plumbing", "query": "Columbus"})
    check("a lookup answers the candidates", (r.status_code, r.get_json()["proposed"]), (200, CID_A))
    check("...handing the module the record's link as the hint",
          _asked[-1], ("Acme Plumbing", "Columbus", _hint or ""))
    r = staff.post("/api/client/youtube/confirm", json={"name": "Acme Plumbing", "channel_id": CID_A})
    check("a confirmation stores and reads on the press", (r.status_code, r.get_json()["ok"], r.get_json()["reading"]["state"]), (200, True, "ok"))
    check("...under the signed-in person", youtube.record("Acme Plumbing")["confirmed_by"], "Todd")
    r = staff.post("/api/client/youtube/refresh", json={"name": "Acme Plumbing"})
    check("a refresh reads now", (r.status_code, r.get_json()["ok"]), (200, True))
    r = staff.post("/api/client/youtube/confirm", json={"name": "Acme Plumbing"})
    check("a confirmation with no id is refused", r.status_code, 400)
    r = staff.post("/api/client/youtube/clear", json={"name": "Acme Plumbing"})
    check("a clear answers", (r.status_code, r.get_json()["ok"]), (200, True))
    r = staff.post("/api/client/youtube/clear", json={"name": "Acme Plumbing"})
    check("...and a second clear is a 404", r.status_code, 404)
    r = staff.post("/api/client/youtube/refresh", json={"name": "Acme Plumbing"})
    check("a refresh with no channel confirmed is refused", r.status_code, 400)
finally:
    youtube.details = _real_details
    youtube.candidates = _real_cands

_src = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
_get_route = _src[_src.index('@app.route("/api/client/youtube")'):_src.index('@app.route("/api/client/youtube/lookup"')]
check("the GET route reaches no YouTube call: reading(), never candidates(), details() or snapshot()",
      "youtube.reading(" in _get_route and not any(f in _get_route for f in ("candidates(", "details(", "snapshot(")))


# ---------------------------------------------------------------------------
section("The Client 360 card, lifted and driven in node")
# ---------------------------------------------------------------------------

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    import importlib, os as _os, sys as _sys
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return importlib.import_module("hub.client360_assets").source_text()

_REC = _c360_source()
_a = _REC.find("/* ---- c360 youtube channel (lifted")
_b = _REC.find("/* ---- end c360 youtube channel ----")
check("the card's renderer is marked for lifting", 0 < _a < _b)
_SRC = _REC[_a:_b] if 0 < _a < _b else ""
check("the card is drawn on the record and named in the section list",
      'id="c-youtube"' in _REC and "'youtube channel'" in _REC and "loadYouTube(name);" in _REC)
check("...with a bubble behind it", "hub.client360.youtube" in _REC)
_payloads = [
    {"state": "unconfigured", "record": None},
    {"state": "unread", "record": None, "error": "the store could not be read"},
    {"state": "no_channel", "record": None, "hint": "https://www.youtube.com/@acmeplumbing"},
    {"state": "no_snapshot", "record": {"channel_id": CID_A, "title": "Acme", "handle": "@acme"}},
    {"state": "unread", "record": {"channel_id": CID_A, "title": "Acme"}, "error": "HTTP 500"},
    {"state": "ok", "record": {"channel_id": CID_A, "title": "Acme Plumbing", "handle": "@acmeplumbing",
                               "url": "https://www.youtube.com/@acmeplumbing", "confirmed_by": "Todd", "confirmed_at": "2026-09-14T10:00:00"},
     "subscribers": 340, "subscribers_hidden": False, "views": 12000, "videos": 18, "as_of": "2026-09-14",
     "change": {"measured": True, "days": 30, "since": "2026-08-14", "subscribers_delta": 20, "views_delta": 1000, "videos_delta": 0}},
    {"state": "ok", "record": {"channel_id": CID_C, "title": "Hidden Co"}, "subscribers": None, "subscribers_hidden": True,
     "views": 9000, "videos": 4, "as_of": "2026-09-14", "change": {"measured": False, "note": "First reading 2026-09-14; the 30-day change needs a reading from 30 days back."}},
]
_cands = {"measured": True, "query": "Acme Plumbing Columbus", "proposed": CID_A, "how": "search",
          "why": "the only channel titled exactly 'Acme Plumbing'",
          "candidates": [{"channel_id": CID_B, "title": "Acme Plumbing Supply", "handle": "@acmesupply", "name_match": False,
                          "subscribers": 20, "subscribers_hidden": False, "views": 500, "videos": 3, "url": "https://www.youtube.com/@acmesupply"},
                         {"channel_id": CID_A, "title": "Acme Plumbing", "handle": "@acmeplumbing", "name_match": True,
                          "subscribers": 340, "subscribers_hidden": False, "views": 12000, "videos": 18, "url": "https://www.youtube.com/@acmeplumbing"}]}
_driver = ("function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;')"
           ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
           + _SRC + "\nconst P=" + json.dumps(_payloads) + ";\nconst C=" + json.dumps(_cands) + ";\n"
           "const out=P.map(p=>renderYouTube(p,'Acme Plumbing',null));\n"
           "out.push(renderYouTube({state:'no_channel'},'Acme Plumbing',C));\n"
           "out.push(renderYouTube({state:'no_channel'},'Acme Plumbing',{measured:true,query:'x',candidates:[],proposed:null,why:''}));\n"
           "console.log(JSON.stringify(out));\n")
_r = subprocess.run(["node", "-"], input=_driver, capture_output=True, text=True)
check("the lifted renderer runs on its own", _r.returncode, 0)
if _r.returncode:
    print("          node:", _r.stderr[-400:])
_out = json.loads(_r.stdout or "[]") if _r.returncode == 0 else [""] * 9
_unconf, _unread, _none, _nosnap, _failed_read, _okm, _hidden, _candm, _nocand = (_out + [""] * 9)[:9]
check("no key: says so and points at System status, never a figure",
      "not set up" in _unconf and "/status" in _unconf and "subscribers" not in _unconf)
check("an unread store is drawn as unread, not as no channel",
      "could not be read" in _unread and "Find their channel" not in _unread)
check("no channel: the box, the lookup, the record's link named, and what a lookup costs",
      'data-act="lookup"' in _none and "Their record links" in _none and "@acmeplumbing" in _none
      and "one quota unit" in _none and "a hundred" in _none)
check("confirmed and not read yet says so, with the record", "not read yet" in _nosnap and "@acme" in _nosnap)
check("a failed last read says so and keeps the channel", "last read failed" in _failed_read and "HTTP 500" in _failed_read)
check("a reading draws the three counts and the day",
      "340" in _okm and "12,000" in _okm and "18" in _okm and "Read 2026-09-14" in _okm)
check("...the 30-day change, a zero said as no change", "+20 subscribers" in _okm and "+1,000 views" in _okm
      and "no change in videos" in _okm and "since 2026-08-14" in _okm)
check("...the channel's own address and who confirmed", "Open on YouTube" in _okm and "confirmed by Todd" in _okm)
check("...and the two presses", 'data-act="refresh"' in _okm and 'data-act="clear"' in _okm)
check("a hidden subscriber count is drawn as hidden, never as 0", "hidden" in _hidden and ">0<" not in _hidden and "9,000" in _hidden)
check("candidates: one Confirm per row, the proposed one marked, the exact name marked",
      _candm.count('data-act="confirm"') == 2 and "proposed" in _candm and "their exact name" in _candm and f'data-id="{CID_A}"' in _candm)
check("no candidates says so and how to narrow", "returned no channel" in _nocand and "link" in _nocand)


# ---------------------------------------------------------------------------
section("The client's report page: gated on a product and a confirmed channel, absent otherwise")
# ---------------------------------------------------------------------------

from modules.reports import client_view, youtube as yt_section       # noqa: E402
_reports_testdb.reset(store)
NAME = "Acme Plumbing"
CLIENT = "d:acme.com"
D1 = date.today() - timedelta(days=1)
store.upsert_rows([{"platform": "ttd", "account_id": "adv-1", "campaign_id": "t-1", "campaign_name": "Fall",
                    "date": D1, "spend": "100.00", "impressions": 1_000_000, "clicks": 1120, "source": "windsor"}])
store.map_campaign("ttd", "adv-1", "t-1", client=CLIENT, client_name=NAME, product="Streaming TV", mapped_by="Todd")
link = store.create_link(CLIENT, client_name=NAME, created_by="Todd")

BOOK = {"rows": []}
yt_section._product_rows = lambda: BOOK["rows"]
yt_section._running = lambda row: row.get("status") != "Complete"

g = yt_section.gate(CLIENT, NAME)
check("no product, no channel: off, saying both", (g["gated"], g["product"], g["channel"], len(g["why"])), (False, False, False, 2))
BOOK["rows"] = [{"client": NAME, "product": "SEO - Local", "status": "Live"}]
check("an SEO product is not a video or social product", yt_section.gate(CLIENT, NAME)["product"], False)
BOOK["rows"] = [{"client": "acme plumbing, llc", "product": "TrueView", "status": "Live"}]
g = yt_section.gate(CLIENT, NAME)
check("a TrueView buy is, matched on the exact normalized name", (g["product"], g["products"]), (True, ["TrueView"]))
BOOK["rows"] = [{"client": "Acme Plumbing Supply", "product": "TrueView", "status": "Live"}]
check("...and never on a substring", yt_section.gate(CLIENT, NAME)["product"], False)
BOOK["rows"] = [{"client": NAME, "product": "TrueView", "status": "Complete"}]
check("...nor on a finished row", yt_section.gate(CLIENT, NAME)["product"], False)
BOOK["rows"] = [{"client": NAME, "product": "Social Media Management", "status": "Live"},
                {"client": NAME, "product": "Connected TV - Targeted", "status": "Live"}]
g = yt_section.gate(CLIENT, NAME)
check("a social retainer and a video buy both count, named for the staff row",
      (g["product"], g["products"], g["gated"], g["channel"]), (True, ["Social Media Management", "Connected TV - Targeted"], False, False))
check("...with the channel the only thing missing", g["why"], ["no YouTube channel has been confirmed for the client"])

anon = Client(wsgi.application)
client_view.forget(link.token)
html = anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True)
check("with no channel confirmed the page has no channel section", "YouTube channel" not in html)
data = anon.get(f"/reports/r/c/{link.token}/data.json").get_json()
check("...and data.json carries none, absent rather than explained", data.get("youtube"), None)

# The sections above left this client readings dated past today (the
# sweep was driven through the 17th), and the newest good reading is what
# the page draws; start the page from a clean file so its date is today's.
jsonstore.delete_json(youtube._readings_path(NAME))
youtube.details = lambda cid: {"measured": True, "error": "", "kind": "", "channel": youtube._channel_row(_item(cid))}
try:
    youtube.confirm(NAME, CID_A, actor="Todd")
finally:
    youtube.details = _real_details
g = yt_section.staff_gate(CLIENT, NAME)
check("confirmed and read: the gate is on, naming the channel and the day",
      (g["gated"], g["channel"], g["read"], g["channel_title"], g["as_of"]), (True, True, True, "Acme Plumbing", date.today().isoformat()))
client_view.forget(link.token)
r = anon.get(f"/reports/r/c/{link.token}")
html = r.get_data(as_text=True)
check("the page opens with the channel section", (r.status_code, "YouTube channel" in html), (200, True))
check("...below the organic slot and above the CTA", html.index('id="youtube-h"') < html.index("Want more from this campaign?"))
check("...with the three counts and the client's own channel named and linked",
      "340" in html and "12,000" in html and "Acme Plumbing" in html and "@acmeplumbing" in html and "See the channel" in html)
check("...the 30-day change not yet measured, said so", "30-day change not yet measured" in html)
check("...the day it was read", "Read " + date.today().isoformat() in html)
check("...never a staff note, a key name or a vendor word",
      "staff" not in html.lower() and "API_KEY" not in html and products.forbidden_hits(html) == [])
data = anon.get(f"/reports/r/c/{link.token}/data.json").get_json()
check("data.json carries the reading with no staff note and no state",
      (data["youtube"]["subscribers"], data["youtube"]["views"], "staff_note" in data["youtube"], "state" in data["youtube"]),
      (340, 12000, False, False))
pdf = anon.get(f"/reports/r/c/{link.token}.pdf").get_data()
check("the PDF carries the section too", b"YouTube channel" in pdf and b"12,000" in pdf and b"Read " in pdf)

# A hidden count on the page and the PDF reads as hidden, never as 0.
youtube._append_reading(NAME, youtube._channel_row(_item(CID_A, hidden=True, views="12000", videos="18")))
client_view.forget(link.token)
html = anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True)
check("a hidden subscriber count on the page is said as hidden", "hidden by the channel" in html and "does not publish this" in html)
pdf = anon.get(f"/reports/r/c/{link.token}.pdf").get_data()
check("...and on the PDF", b"hidden by the channel" in pdf)

# The staff page prints the gate.
r = staff.get(f"/reports/client/{CLIENT}")
shtml = r.get_data(as_text=True)
check("the staff page prints the channel gate as on", (r.status_code, "Channel section on" in shtml), (200, True))
youtube.clear(NAME, actor="Todd")
client_view.forget(link.token)
shtml = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("...and off, with the notice naming where to confirm one, once the channel is cleared",
      "Channel section off" in shtml and "no confirmed YouTube channel" in shtml and "Client 360" in shtml)
check("the client's page has no channel section again", "YouTube channel" not in anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True))

# A confirmed channel with no reading is gated in and still absent.
jsonstore.update_json(youtube._channels_path(), lambda d: {**(d or {}), NAME: {"channel_id": CID_A, "title": "Acme Plumbing"}})
jsonstore.delete_json(youtube._readings_path(NAME))
g = yt_section.staff_gate(CLIENT, NAME)
check("confirmed and unread: gated in, read no, the reason on the staff row", (g["gated"], g["read"], "not been read" in g["read_note"]), (True, False, True))
blk = yt_section.section(link, date.today())
check("...the section says so for staff and the public view drops it",
      (blk["measured"], blk["state"], yt_section.public_view(blk)), (False, "no_snapshot", None))
client_view.forget(link.token)
check("...so the client's page has no section", "YouTube channel" not in anon.get(f"/reports/r/c/{link.token}").get_data(as_text=True))
shtml = staff.get(f"/reports/client/{CLIENT}").get_data(as_text=True)
check("...and the staff page says it is waiting on a reading", "waiting on a reading" in shtml)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
