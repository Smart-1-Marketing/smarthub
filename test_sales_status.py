"""The pipeline, on the page everybody opens.

    python3 test_sales_status.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

Three phases of work gave the Hub real knowledge about every proposal — who
opened it and how many times, whether the pricing still stands, whether the
client accepted, what the campaign costs — and all of it was readable only
inside the Proposal Builder. The Hub dashboard, the page everyone opens first,
carried eleven KPIs about *live* business and not one figure about pipeline.

`hub/social_status.py` answers the same shape next door and its note applies
word for word: there is no mailer in this Hub, so the honest route is putting
it where people already look.

What is asserted here is the way a scoreboard quietly goes wrong: a failure
that reads as a clean zero, a zero that does not say which kind of zero it is,
two screens answering one question differently, a count that links to a page
which cannot show it, and — the one that would make the whole card a liability
— counting a job that is not a job.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-salesboard-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "salesboard-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 62)


from werkzeug.test import Client                                    # noqa: E402
import wsgi                                                         # noqa: E402
from hub import auth, sales_status                                  # noqa: E402

B = sys.modules.get("salesb_app")
if B is None:                                   # pragma: no cover - mount failed
    from modules.sales_builder import app as B

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")
stranger = Client(wsgi.application)

# ---------------------------------------------------------------------------
section("an empty book says which kind of empty it is")
# ---------------------------------------------------------------------------
empty = sales_status.scoreboard()
check("it answers before anything exists", empty["measured"] is True)
check("with no open proposals, it says so rather than drawing a nought",
      empty["open_count"] == 0 and "No open proposals" in empty["line"], empty["line"])


def quote(name, budget=4000):
    state = {"client": name, "months": 6, "budget": budget,
             "objectives": ["Lead Generation"],
             "items": [{"category": "DISPLAY", "product": "Category",
                        "rate": "CPM", "rateValue": 4.25, "dollars": budget}]}
    return staff.post("/sales/builder/api/quotes",
                      json={"data": state}).get_json()["quote"]


draft = quote("Zeta Drafting", 1000)
board = sales_status.scoreboard()
check("a quote nobody has sent is open and is not waiting on anybody",
      board["open_count"] == 1 and board["attention"] == 0)
check("and the line says no link has been sent, which is a different empty "
      "from 'nothing is waiting'",
      "no client link has been sent" in board["line"], board["line"])

# ---------------------------------------------------------------------------
section("five signals, five different jobs")
# ---------------------------------------------------------------------------
alpha = quote("Alpha Dental", 4000)      # sent, never opened
beta = quote("Beta Roofing", 2500)       # sent, read, no answer
gamma = quote("Gamma HVAC", 6000)        # sent, pricing lapsed
delta = quote("Delta Law", 3000)         # accepted, no IO
eps = quote("Epsilon Auto", 1500)        # sent, expiring this week
for q in (alpha, beta, gamma, eps):
    staff.post(f"/sales/builder/api/quotes/{q['id']}/share")

db = B.SessionLocal()
try:
    db.add(B.QuoteView(quote_id=beta["id"], token="t", revision=1))
    (db.query(B.QuoteShare).filter(B.QuoteShare.quote_id == gamma["id"])
     .first().sent_at) = datetime.now(timezone.utc) - timedelta(days=40)
    (db.query(B.QuoteShare).filter(B.QuoteShare.quote_id == eps["id"])
     .first().sent_at) = datetime.now(timezone.utc) - timedelta(days=26)
    db.get(B.Quote, delta["id"]).status = "Approved"
    db.commit()
finally:
    db.close()

board = sales_status.scoreboard()
c = board["counts"]
check("sent and never opened is its own count — the link never reached them, "
      "which is a different job from being ignored",
      c["unopened"] == 1 and alpha["id"] in board["ids"]["unopened"], c)
check("read with no answer is its own count", c["waiting"] == 1
      and beta["id"] in board["ids"]["waiting"])
check("pricing lapsed is its own count", c["expired"] == 1
      and gamma["id"] in board["ids"]["expired"])
check("expiring within the week is its own count, before the client tries to "
      "accept and cannot", c["expiring"] == 1
      and eps["id"] in board["ids"]["expiring"])
check("accepted with no insertion order is its own count",
      c["to_convert"] == 1 and delta["id"] in board["ids"]["to_convert"])
check("and they are not folded into one 'needs attention' number, which is a "
      "figure nobody can act on", board["attention"] == 5 and len(c) == 5)

check("a quote is in exactly one bucket — a lapsed one is not also counted "
      "as unanswered",
      sum(len(v) for v in board["ids"].values()) == 5
      and len({i for v in board["ids"].values() for i in v}) == 5)
check("the pipeline is what the plans total, from the quote's own column",
      board["pipeline_monthly"] == 1000 + 4000 + 2500 + 6000 + 3000 + 1500,
      board["pipeline_monthly"])
check("the most at risk is first — a lapsed price is the only one of these "
      "that actively stops a client saying yes",
      board["rows"][0]["client"] == "Gamma HVAC", board["rows"][0])
check("the row that needs converting carries how many answers the IO still "
      "needs, which is what a rep wants to know before opening it",
      any(r.get("gaps") for r in board["rows"] if r["client"] == "Delta Law"))
check("and the line names each job rather than a total",
      "lapsed" in board["line"] and "never opened" in board["line"]
      and "no insertion order" in board["line"], board["line"])

# --- what is NOT a job -----------------------------------------------------
db = B.SessionLocal()
try:
    db.get(B.Quote, gamma["id"]).status = "Lost"
    db.get(B.Quote, alpha["id"]).status = "Converted"
    db.commit()
finally:
    db.close()
after = sales_status.scoreboard()
check("a Lost quote leaves the book — counting it would make every figure "
      "grow forever and none of it actionable",
      after["counts"]["expired"] == 0)
check("and so does a Converted one", after["counts"]["unopened"] == 0)
db = B.SessionLocal()
try:
    db.get(B.Quote, gamma["id"]).status = "Sent"
    db.get(B.Quote, alpha["id"]).status = "Sent"
    db.commit()
finally:
    db.close()

# A client who accepted is not somebody to chase.
staff.post(f"/sales/builder/api/quotes/{beta['id']}/share")
visitor = Client(wsgi.application)
token = staff.get(f"/sales/builder/api/quotes/{beta['id']}/share").get_json()["share"]["token"]
visitor.post(f"/sales/builder/api/p/{token}/accept",
             json={"name": "Jane Whitfield", "email": "jane@beta.com"},
             headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/120 Safari/537"})
board = sales_status.scoreboard()
check("a client who said yes stops being somebody to chase and becomes "
      "somebody to write an order for",
      beta["id"] not in board["ids"]["waiting"]
      and beta["id"] in board["ids"]["to_convert"], board["ids"])

# ---------------------------------------------------------------------------
section("nothing here may raise, and nothing is written")
# ---------------------------------------------------------------------------
src = open(os.path.join(ROOT, "hub", "sales_status.py"), encoding="utf-8").read()
# Read the AST, not the text: the module's own docstring names ghl_hooks.py
# as the precedent it follows, and a check that reads prose as a call site
# reports the explanation as the defect — the rule tools/spellcheck.py works
# to.
import ast as _ast                                                  # noqa: E402
_tree = _ast.parse(src)
_calls = {getattr(n.func, "attr", getattr(n.func, "id", ""))
          for n in _ast.walk(_tree) if isinstance(n, _ast.Call)}
_imports = {a.name for n in _ast.walk(_tree)
            if isinstance(n, _ast.Import) for a in n.names}
_imports |= {(n.module or "") + "." + a.name for n in _ast.walk(_tree)
             if isinstance(n, _ast.ImportFrom) for a in n.names}
check("it is a reading: it commits nothing, and imports nothing that could "
      "write to a quote or to Smart 1 Suite",
      "commit" not in _calls
      and not any("ghl" in i.lower() or "suite" in i.lower() for i in _imports),
      sorted(_imports))
check("it reuses the module the app loaded rather than importing a second "
      "mapping of the same tables", 'sys.modules.get("salesb_app")' in src)

import hub.sales_status as ss                                       # noqa: E402
_real = ss._module
ss._module = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
broken = ss.scoreboard()
ss._module = _real
check("a store it cannot read is NOT measured rather than a clean zero — "
      "'nothing is waiting' and 'we could not look' are different answers "
      "and only the first means there is nothing to do",
      broken["measured"] is False and broken["error"], broken)

# ---------------------------------------------------------------------------
section("through the running app")
# ---------------------------------------------------------------------------
api = staff.get("/api/sales/scoreboard")
check("the dashboard's own route answers", api.status_code == 200
      and api.get_json()["measured"] is True)
check("and is refused to somebody with no session",
      stranger.get("/api/sales/scoreboard").status_code in (302, 401, 403))

from hub import access                                              # noqa: E402
check("it is NOT a Utilities path — a figure everybody sees served by a path "
      "most accounts are refused renders a confident nothing for eleven of "
      "the fourteen, which is what /api/status did on this very page",
      access.is_utility("/api/sales/scoreboard") is False)

page = staff.get("/").get_data(as_text=True)
check("the dashboard carries the card", 'id="salesboard"' in page)
check("it fetches the shared reading", "/api/sales/scoreboard" in page)
check("and sits with the other counts rather than in Utilities",
      page.index('id="salesboard"') < page.index('id="mini-status"'))

built = staff.get("/sales/builder/api/dashboard").get_json()
check("the Proposal Builder's own dashboard is handed the same reading, so "
      "two screens cannot answer 'what needs chasing' differently",
      built["pipeline"]["counts"] == sales_status.scoreboard()["counts"],
      built.get("pipeline"))

tpl = open(os.path.join(ROOT, "modules", "sales_builder", "templates",
                        "index.html"), encoding="utf-8").read()
check("a count links to the rows it counted, not to a tool the reader then "
      "has to filter", "focus=" in json.dumps(sales_status.scoreboard()["urls"])
      and "function openFocus" in tpl)
check("the focused list says what it is showing and how to leave it",
      "function focusBanner" in tpl and "Show all" in tpl)
check("and an empty bucket leaves the whole list rather than an empty table "
      "that reads as a book with nothing in it",
      "FOCUS=null;drawRows();" in tpl)
check("both list views carry the banner — an id would have put it on "
      "whichever screen was written first",
      tpl.count('class="focusBanner"') == 2)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
