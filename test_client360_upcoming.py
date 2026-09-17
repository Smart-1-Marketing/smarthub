"""Client 360's Coming up card and the Last activity pill -- hub/client_upcoming.py.

    python3 test_client360_upcoming.py

Same shape as the other test files here -- no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database. The renderer is
lifted out of the template and driven in node, test_client360_layout.py's
arrangement.

What it holds:

  1. **Dates are read, never guessed.** An order or domain with no date is
     reported as such, not dropped and not treated as today.
  2. **Exact client match.** Another company's order does not land on this
     record because the names share a word.
  3. **Soonest first, undated last** -- the ones to chase must not vanish
     under the ones we know.
  4. **Never raises.** A source that will not answer names itself in
     `unread` and the rest of the card still draws.
  5. **The Last activity pill** is idle when nothing was ever logged, and
     escalates with the gap; it lands on the health strip beside the others.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="c360up_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "c360-up-test"
os.environ["PANEL_PASSWORD"] = "c360-up-pass"
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import client_upcoming as up          # noqa: E402

TODAY = dt.date(2026, 9, 14)
CLIENT = "Buckeye Lake Winery"

# ------------------------------------------------------------------------
section("1. Orders ending, from fixtures")

_orig_listing = None


def _fake_listing(rows, measured=True, error=""):
    from hub import io_records
    global _orig_listing
    _orig_listing = _orig_listing or io_records.listing
    io_records.listing = lambda client="": {"rows": [r for r in rows if up._norm(r["client"]) == up._norm(client)],
                                            "measured": measured, "error": error}


_fake_listing([
    {"client": CLIENT, "order": "IO-201", "end": "2026-10-01", "monthly": 2500, "line_count": 3},
    {"client": CLIENT, "order": "IO-188", "end": "09/20/2026", "monthly": 900, "line_count": 1},
    {"client": CLIENT, "order": "IO-150", "end": "2026-08-30", "monthly": 400, "line_count": 1},   # ended 15 days ago
    {"client": CLIENT, "order": "IO-100", "end": "2026-03-01", "monthly": 400, "line_count": 1},   # long over
    {"client": CLIENT, "order": "IO-210", "end": "", "monthly": 1200, "line_count": 2},            # no end date
    {"client": CLIENT, "order": "IO-300", "end": "2027-06-01", "monthly": 1200, "line_count": 2},  # beyond horizon
    {"client": "Buckeye Lake Winery Supply", "order": "IO-999", "end": "2026-09-15", "monthly": 5, "line_count": 1},
])
rows, why = up._orders(CLIENT, TODAY)
check("orders within the horizon are listed", sorted(r["what"].split(" ")[0] for r in rows), ["IO-150", "IO-188", "IO-201", "IO-210"])
check("a bare order number gets the prefix, a prefixed one is left alone", (up._io_name("2261"), up._io_name("IO-2261")), ("IO 2261", "IO-2261"))
check("an m/d/Y end date is read", next(r for r in rows if "IO-188" in r["what"])["days"], 6)
check("...and is inside two weeks, so bad", next(r for r in rows if "IO-188" in r["what"])["state"], "bad")
check("an order ending in 17 days is warn", next(r for r in rows if "IO-201" in r["what"])["state"], "warn")
check("a recently ended order stays, flagged", next(r for r in rows if "IO-150" in r["what"])["detail"].startswith("Ended 15 days ago"), True)
check("an order with no end date is reported, not dropped", next(r for r in rows if "IO-210" in r["what"])["state"], "unread")
check("a look-alike client's order does not land here", any("IO-999" in r["what"] for r in rows), False)
check("nothing was unread", why, "")

from hub import io_records  # noqa: E402
io_records.listing = lambda client="": {"rows": [], "measured": False, "error": "The insertion order records could not be read (OSError)."}
rows, why = up._orders(CLIENT, TODAY)
check("a store that will not answer names itself", (rows, "could not be read" in why), ([], True))

# ------------------------------------------------------------------------
section("2. Domains renewing, from a fake snapshot")

from hub import domain_purchase  # noqa: E402
_orig_snap = domain_purchase.snapshot
domain_purchase.snapshot = lambda: {}
rows, why = up._domains(CLIENT, "https://buckeyelakewinery.com", TODAY)
check("no snapshot yet is said, not an empty list pretending", "not been pulled" in why, True)
domain_purchase.snapshot = lambda: {"fetched": "x", "rows": [
    {"domain": "buckeyelakewinery.com", "client": "BLW LLC", "renews": "10/10/2026", "registrar": "GoDaddy", "partner": "WCLT"},
    {"domain": "blw-events.com", "client": CLIENT, "renews": "", "registrar": ""},
    {"domain": "other.com", "client": "Somebody Else", "renews": "09/16/2026"},
    {"domain": "old.com", "client": CLIENT, "renews": "01/01/2026"},
]}
rows, why = up._domains(CLIENT, "https://www.buckeyelakewinery.com/", TODAY)
check("a domain matched by the client's own domain is found even under another name", any(r["what"] == "buckeyelakewinery.com" for r in rows), True)
check("its renewal is 26 days out, so warn", next(r for r in rows if r["what"] == "buckeyelakewinery.com")["state"], "warn")
check("a domain with no renewal date is reported as such", next(r for r in rows if r["what"] == "blw-events.com")["detail"], "Renewal date not on file in Knack.")
check("somebody else's domain stays off the record", any(r["what"] == "other.com" for r in rows), False)
check("a renewal long past is not 'coming up'", any(r["what"] == "old.com" for r in rows), False)
domain_purchase.snapshot = _orig_snap

# ------------------------------------------------------------------------
section("3. for_client(): order, unread, never raises")

domain_purchase.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
io_records.listing = lambda client="": {"rows": [
    {"client": CLIENT, "order": "IO-201", "end": "2026-10-01", "monthly": 2500},
    {"client": CLIENT, "order": "IO-188", "end": "2026-09-20", "monthly": 900},
    {"client": CLIENT, "order": "IO-210", "end": "", "monthly": 1200},
], "measured": True, "error": ""}
d = up.for_client(CLIENT, "https://buckeyelakewinery.com", TODAY)
check("the card is measured even when one source raised", d["measured"], True)
check("soonest first, undated last", [i["what"].split(" ")[0] for i in d["items"]], ["IO-188", "IO-201", "IO-210"])
check("the raising source is named in unread", any("RuntimeError" in u for u in d["unread"]), True)
check("last activity rides along", "activity" in d and d["activity"].get("measured") in (True, False), True)
check("no client is not measured", up.for_client("", "", TODAY)["measured"], False)
domain_purchase.snapshot = _orig_snap

# ------------------------------------------------------------------------
section("4. Last activity: idle, then the gap")

act = up.last_activity("Nobody Ever Inc", "", TODAY)
check("nothing logged is idle, with no days", (act["state"], act["days"]), ("idle", None))

from hub import audit, client_brand  # noqa: E402
kind = next(k for k in client_brand.WORK_KINDS)
audit.log(kind, "made", actor="Todd", client=CLIENT, detail="a thing")
act = up.last_activity(CLIENT, "", dt.date.today())
check("a logged row makes it measured and recent", (act["state"], act["days"]), ("ok", 0))
check("and says who and via what", (act["actor"], bool(act["source"])), ("Todd", True))
act = up.last_activity(CLIENT, "", dt.date.today() + dt.timedelta(days=100))
check("a hundred days later it is bad", act["state"], "bad")

from hub import record_health  # noqa: E402
strip = record_health.client360("Nobody Ever Inc", today=TODAY)
pill = next((p for p in strip["pills"] if p["key"] == "activity"), None)
check("the health strip carries a Last activity pill", bool(pill), True)
check("...idle for a client with nothing logged", (pill or {}).get("state"), "idle")
strip = record_health.client360(CLIENT, today=dt.date.today() + dt.timedelta(days=100))
pill = next((p for p in strip["pills"] if p["key"] == "activity"), None)
check("...and bad with a queue item when the gap is long", (pill or {}).get("state"), "bad")
check("the queue says how long", any("100 days" in q.get("title", "") for q in strip["queue"]), True)

# ------------------------------------------------------------------------
section("5. The route and the card")

from hub import auth, create_hub_app  # noqa: E402
from hub.extensions import create_all  # noqa: E402
app = create_hub_app()
create_all(app)
check("a stranger is refused", app.test_client().get("/api/client/upcoming?name=x").status_code, 401)
staff = app.test_client()
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"))
body = staff.get("/api/client/upcoming?name=" + CLIENT).get_json()
check("the route answers the card's payload", ("items" in body, "activity" in body), (True, True))
check("an unnamed client is not measured", staff.get("/api/client/upcoming").get_json().get("measured"), False)

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    import importlib, os as _os, sys as _sys
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return importlib.import_module("hub.client360_assets").source_text()

REC = _c360_source()
check("the card is emitted right after Orders", REC.find('id="c-orders"') < REC.find('id="c-upcoming"') < REC.find('id="c-ghl"'), True)
check("the section map claims it for Overview", "'coming up'" in REC, True)
check("the loader honors the generation guard",
      REC[REC.find("function loadUpcoming"):].find("if(gen!==c360Generation) return;") > 0, True)
check("it is loaded with the rest of the record", "loadUpcoming(name);" in REC[REC.find("function loadBrandAndWork"):], True)

a = REC.find("/* ---- coming up (lifted")
b = REC.find("/* ---- end coming up ----")
SRC = REC[a:b] if 0 < a < b else ""
check("the renderer is marked for lifting", bool(SRC))
if shutil.which("node") and SRC:
    driver = ("const esc=s=>String(s??'');\n" + SRC + "\nconst out={"
              "bad:renderUpcoming({measured:false,error:'Down'}),"
              "none:renderUpcoming({measured:true,items:[],activity:{measured:true,state:'idle'},horizon_days:120,unread:[]}),"
              "some:renderUpcoming({measured:true,items:[{label:'Insertion order ends',what:'IO-1',days:6,state:'bad',when:'2026-09-20'},{label:'Domain renews',what:'x.com',days:null,state:'unread',detail:'Renewal date not on file in Knack.'}],activity:{measured:true,state:'warn',days:45,kind:'Graphic created',source:'Image Creator',actor:'Jim'},unread:['The domain snapshot has not been pulled yet (it runs nightly).']})"
              "};console.log(JSON.stringify(out));\n")
    r = subprocess.run(["node", "-"], input=driver, capture_output=True, text=True)
    check("the lifted block runs on its own", r.returncode, 0)
    out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("unreadable says so", "Down" in out.get("bad", ""), True)
    check("nothing coming up says so, with the horizon", "next 120 days" in out.get("none", "") and "No Hub tool has logged" in out.get("none", ""), True)
    check("a dated row says how soon; an undated one says so", "in 6 days" in out.get("some", "") and "date not on file" in out.get("some", ""), True)
    check("last activity names the gap and who", "45 days ago" in out.get("some", "") and "Jim" in out.get("some", ""), True)
    check("what could not be read is footnoted", "Not measured:" in out.get("some", ""), True)
else:
    print("  skip  node not available")

CI = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("this file runs in CI", "python3 test_client360_upcoming.py" in CI, True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
