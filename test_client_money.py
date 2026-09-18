"""Client 360's Account value card -- hub/client_money.py.

    python3 test_client_money.py

Same shape as the other Client 360 card tests -- no pytest, no new
dependencies, a temporary data directory and a throwaway SQLite database.
The renderer is lifted out of the record's script modules and driven in
node, test_client360_upcoming.py's arrangement.

What it holds:

  1. **Nothing here is a new source.** The monthly total is the same
     billing_monthly hub/record_health.py's Billing pill already reads off
     hub/knack_data.search_client(); the balance is the same
     hub.quickbooks.lookup_for_clients() call the Invoices card already
     makes for `?client=`.
  2. **Absent is not zero.** A product book that could not be read leaves
     billing unmeasured rather than $0/mo; QuickBooks not configured or not
     connected is its own state, never "nothing owed".
  3. **A member with no matched QuickBooks customer is named**, not folded
     into the total -- the identical `unmatched` list the Invoices card
     already carries.
  4. **A grouped client is asked once for billing**, not once per member --
     hub/knack_data.py already merges every other member's live products
     onto whichever member's name is searched, and summing per member
     would double what a group with two members owes.
  5. **Never raises.** Either half naming its own failure never costs the
     other half its answer.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="c360money_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "c360-money-test"
os.environ["PANEL_PASSWORD"] = "c360-money-pass"
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


import importlib                                                  # noqa: E402

from hub import client_money as cm                                # noqa: E402

CLIENT = "Buckeye Lake Winery"


class Stub:
    def __init__(self, **targets):
        self.targets, self.saved = targets, {}

    def __enter__(self):
        for dotted, value in self.targets.items():
            mod, attr = dotted.rsplit(".", 1)
            m = importlib.import_module(mod)
            self.saved[dotted] = getattr(m, attr)
            setattr(m, attr, value)
        return self

    def __exit__(self, *a):
        for dotted, old in self.saved.items():
            setattr(importlib.import_module(dotted.rsplit(".", 1)[0]), dotted.rsplit(".", 1)[1], old)


# ------------------------------------------------------------------------
section("1. Billing: the group's own already-merged monthly total")

with Stub(**{"hub.knack_data.search_client": lambda q, limit=8: [
    {"client": "Somebody Else", "billing_monthly": 40000.0},
    {"client": CLIENT, "billing_monthly": 2900.0},
]}):
    out = cm._billing(CLIENT)
check("the exact client's own total is read, not the first row back",
      (out["measured"], out["monthly"]), (True, 2900.0))

with Stub(**{"hub.knack_data.search_client": lambda q, limit=8: []}):
    with Stub(**{"hub.knack_data.products_error": lambda: "the products export could not be read"}):
        out = cm._billing(CLIENT)
check("an unreadable product book is unmeasured, not $0/mo",
      (out["measured"], "could not be read" in out["error"]), (False, True))

with Stub(**{"hub.knack_data.search_client": lambda q, limit=8: []}):
    with Stub(**{"hub.knack_data.products_error": lambda: ""}):
        out = cm._billing("Nobody On File")
check("a client genuinely on nobody's book reads as $0/mo, not unread",
      (out["measured"], out["monthly"]), (True, 0.0))


def _raise(*a, **k):
    raise RuntimeError("boom")


with Stub(**{"hub.knack_data.search_client": _raise}):
    out = cm._billing(CLIENT)
check("a raising product book is caught and named",
      (out["measured"], "RuntimeError" in out["error"]), (False, True))

# ------------------------------------------------------------------------
section("2. Owed: the same lookup the Invoices card runs")


def _fake_lookup(configured=True, connected=True, customers=None, unmatched=None, errors=None):
    return lambda entries: {"configured": configured, "connected": connected,
                             "customers": customers or [], "unmatched": unmatched or [],
                             "errors": errors or []}


with Stub(**{
    "hub.quickbooks.lookup_for_clients": _fake_lookup(customers=[
        {"id": "1", "balance": 450.0, "invoices": [{"status": "Overdue"}, {"status": "Paid"}]},
        {"id": "2", "balance": 0.0, "invoices": [{"status": "Paid"}]},
    ], unmatched=["A Group Member"]),
    "hub.seo.get_links": lambda name: {"qb": []},
    "hub.client_groups.member_names": lambda name, url="": [name, "A Group Member"],
}):
    out = cm._owed(CLIENT, "")
check("balances sum across the matched customers", out["balance"], 450.0)
check("overdue is counted per invoice, not per customer", out["overdue_count"], 1)
check("a member with no QuickBooks customer is named, not folded in",
      out["unmatched"], ["A Group Member"])
check("connected reads as connected", out["state"], "connected")

with Stub(**{
    "hub.quickbooks.lookup_for_clients": _fake_lookup(configured=False),
    "hub.seo.get_links": lambda name: {"qb": []},
    "hub.client_groups.member_names": lambda name, url="": [name],
}):
    out = cm._owed(CLIENT, "")
check("not configured reads as not_connected, never as $0 owed",
      (out["state"], out["balance"]), ("not_connected", 0.0))

with Stub(**{
    "hub.quickbooks.lookup_for_clients": _fake_lookup(errors=[{"client": CLIENT, "error": "QuickBooks timed out"}]),
    "hub.seo.get_links": lambda name: {"qb": []},
    "hub.client_groups.member_names": lambda name, url="": [name],
}):
    out = cm._owed(CLIENT, "")
check("a lookup that names an error is not_measured, not a clean zero",
      (out["state"], "timed out" in out["error"]), ("not_measured", True))

with Stub(**{"hub.quickbooks.lookup_for_clients": _raise, "hub.seo.get_links": _raise}):
    out = cm._owed(CLIENT, "")
check("a raising lookup is caught and named",
      (out["state"], "RuntimeError" in out["error"]), ("not_measured", True))

# ------------------------------------------------------------------------
section("3. for_client(): both halves, never raises, no client refused")

with Stub(**{
    "hub.client_money._billing": lambda name: {"measured": True, "monthly": 2900.0, "error": ""},
    "hub.client_money._owed": lambda name, url: {"state": "connected", "balance": 450.0,
                                                 "overdue_count": 1, "customers": 1,
                                                 "unmatched": [], "error": ""},
}):
    out = cm.for_client(CLIENT)
check("both halves ride together", (out["billing"]["monthly"], out["owed"]["balance"]), (2900.0, 450.0))
check("measured when either half answered", out["measured"], True)

check("no client name is refused rather than guessed at", cm.for_client("")["measured"], False)

# ------------------------------------------------------------------------
section("4. The route and the card")

from hub import auth, create_hub_app                              # noqa: E402
from hub.extensions import create_all                              # noqa: E402
app = create_hub_app()
create_all(app)
check("a stranger is refused", app.test_client().get("/api/client/money?name=x").status_code, 401)
staff = app.test_client()
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"))
body = staff.get("/api/client/money?name=" + CLIENT).get_json()
check("the route answers the card's payload", ("billing" in body, "owed" in body), (True, True))
check("an unnamed client is not measured", staff.get("/api/client/money").get_json().get("measured"), False)

from hub import suite_embed                                        # noqa: E402
check("the path is one the Suite frame may fetch", suite_embed.embeddable("/api/client/money"), True)


def _c360_source():
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()


REC = _c360_source()
check("the card is emitted after Products", REC.find('id="c360Stage"') != -1 and
      REC.find('>Products') < REC.find('id="c-money"') < REC.find('id="c-orders"'), True)
check("the section map claims it for Overview", "'account value'" in REC, True)
check("the loader honors the generation guard",
      REC[REC.find("function loadMoney"):].find("if(gen!==c360Generation) return;") > 0, True)
check("it is loaded with the rest of the record", "loadMoney(name);" in REC[REC.find("function loadBrandAndWork"):], True)

a = REC.find("/* ---- account value (lifted")
b = REC.find("/* ---- end account value ----")
SRC = REC[a:b] if 0 < a < b else ""
check("the renderer is marked for lifting", bool(SRC))
if shutil.which("node") and SRC:
    scstat = REC[REC.find("function scStat"):REC.find("\n}", REC.find("function scStat")) + 2]
    driver = ("const esc=s=>String(s??'');\n" + scstat + "\n" + SRC + "\nconst out={"
              "ok:renderMoney({billing:{measured:true,monthly:2900},owed:{state:'connected',balance:450,overdue_count:1}}),"
              "notConn:renderMoney({billing:{measured:true,monthly:0},owed:{state:'not_connected',error:'QuickBooks is not connected.'}}),"
              "unread:renderMoney({billing:{measured:false,error:'Down'},owed:{state:'not_measured',error:'Down too'}}),"
              "unmatched:renderMoney({billing:{measured:true,monthly:100},owed:{state:'connected',balance:0,overdue_count:0,unmatched:['A Group Member']}})"
              "};console.log(JSON.stringify(out));\n")
    r = subprocess.run(["node", "-"], input=driver, capture_output=True, text=True)
    check("the lifted block runs on its own", r.returncode, 0)
    out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("a measured account shows both figures", "2,900" in out.get("ok", "") and "450" in out.get("ok", ""), True)
    check("not connected is said, not read as nothing owed",
          "Not connected" in out.get("notConn", ""), True)
    check("an unreadable pair says so on both halves",
          "Down" in out.get("unread", "") and "Down too" in out.get("unread", ""), True)
    check("an unmatched member is named rather than silently zero",
          "A Group Member" in out.get("unmatched", ""), True)
else:
    print("  skip  node not available")

CI = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("this file runs in CI", "python3 test_client_money.py" in CI, True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
