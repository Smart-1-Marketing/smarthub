"""The insertion orders we sent, against the campaigns Knack actually has.

    python3 test_io_reconcile.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

Submitting an insertion order logged it, registered the client when nobody had
heard of them, and POSTed it to Smart 1 Suite. Then the Hub's involvement
ended. **Nothing ever checked that the campaign was set up** — and an order
whose products were never written into Knack looks exactly like one that was:
the log says it went, the `io_clients` overlay still stands in for a record
that never arrived, and Client 360 goes on saying the cards are empty because
there is nothing to read. Nobody is billed, nothing is trafficked, and the
first person to find out is whoever eventually asks why a client we wrote an
order for has no products.

Underneath it was a defect that would have made the report useless on the day
it shipped. `submit_io()` logged `order=_body.get("order_number")` against a
payload whose key is `orderNumber`, so **every `io_submitted` entry the route
has ever written carries an empty order number** — while the
`client_registered` entry beside it, written through `io_clients.register_from_io`,
read the real key and got it right. Two readers of one payload, and the wrong
one is the record a reconciliation depends on. Nothing errored at either end.

What is asserted here is the ways a reconciliation goes confidently wrong: a
stale source read as proof that nothing was trafficked, an order judged against
a snapshot taken before it existed, a fresh order chased on the day it was
written, a mark that a second worker cannot see, and a permanent red row on a
report whose whole job is to say what to act on this week.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-ioreconcile-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "ioreconcile-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")

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


from hub import audit, io_reconcile, knack_products, qa      # noqa: E402

NOW = datetime.now(timezone.utc)


def log_order(order, client, days_ago, start="", partner="", monthly=0):
    """Write an io_submitted entry and back-date it."""
    audit.log("io_builder", "io_submitted", actor="Harness", client=client,
              order=order, partner=partner or None, start=start or None,
              monthly=monthly or None)
    path = os.environ["AUDIT_LOG_PATH"]
    lines = open(path, encoding="utf-8").read().splitlines()
    entry = json.loads(lines[-1])
    entry["time"] = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    lines[-1] = json.dumps(entry)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def orders_in(data):
    return [r["order"] for r in data["outstanding"]]


def row_for(data, order):
    """One outstanding row by its order number.

    CI runs every suite against one shared Postgres, so a converted quote left
    behind by another file is an order this report legitimately finds. Nothing
    here may assert a position in the list or a count of the whole book — only
    the rows this file put there.
    """
    return next((r for r in data["outstanding"] if r["order"] == order), None)


# ---------------------------------------------------------------------------
section("The defect underneath: which key the submit route logs")

src = open(os.path.join(ROOT, "modules", "io_builder", "app.py"),
           encoding="utf-8").read()
submit = src[src.index('@app.post("/api/submit-io")'):]
submit = submit[:submit.index("webhook_url = os.environ")]
check("submit_io logs the payload's own key, `orderNumber` — reading "
      "`order_number` alone wrote a blank on every entry ever logged",
      'get("orderNumber")' in submit)
check("and still accepts the snake_case spelling, so an older caller is not "
      "silently dropped", 'get("order_number")' in submit)

state = open(os.path.join(ROOT, "modules", "io_builder", "templates",
                          "index.html"), encoding="utf-8").read()
check("the key asserted here is the one the wizard actually stores — a test "
      "written against a spelling nobody posts proves nothing",
      'orderNumber:""' in state)

# The other reader of the same payload, which had it right all along.
from hub import io_clients                                    # noqa: E402
io_clients_src = open(os.path.join(ROOT, "hub", "io_clients.py"),
                      encoding="utf-8").read()
check("io_clients.register_from_io reads the same key, so the two readers of "
      "one payload now agree",
      'p.get("orderNumber")' in io_clients_src)

# ---------------------------------------------------------------------------
section("A source that could not be read is never a clean list")

# No product cache at all, and no Knack credentials: rows() falls through to
# the committed export, whose rows are the raw Knack records and carry no IO
# number at all.
log_order("10401", "Landed Co", 40, start="2026-07-01", partner="TMRG",
          monthly=4000)
before = io_reconcile.report()
check("with no live product read the report refuses rather than reporting "
      "every order as never trafficked",
      before["measured"] is False, before.get("knack_source"))
check("and it says which source answered and why that is not good enough",
      "nothing refreshes" in (before.get("error") or ""), before.get("error"))
check("nothing is listed as outstanding on a run that could not measure",
      before["outstanding"] == [])

rep = qa.io_reconcile_report()
check("the QA report carries measured:False, so report_cache never freezes "
      "“we could not look” into the shape of “there is nothing to see”",
      rep.get("measured") is False and rep["rows"] == [])

from hub import report_cache                                  # noqa: E402
check("and report_cache agrees that is not the day's answer",
      report_cache.is_answer(rep) is False)

# ---------------------------------------------------------------------------
section("What landed, and what did not")

log_order("10402", "Dropped Co", 40, start="2026-07-01", partner="FabLocal",
          monthly=2500)
log_order("10403", "Late Co", 40, start="2026-12-01", monthly=900)
log_order("", "No Number Co", 40)
log_order("10404", "Fresh Co", 1, start="2026-09-15", monthly=1000)

# A live-looking read: one of the four orders has a campaign against it.
knack_products._write_cache([{"io": "10401", "client": "Landed Co"},
                             {"io": "", "client": "Something Else"}])

data = io_reconcile.report()
check("a live product read is measured", data["measured"] is True,
      data.get("error"))
check("an order Knack has a campaign for is not a finding",
      "10401" not in orders_in(data))
check("an order Knack has never heard of is", "10402" in orders_in(data))
check("an order submitted this morning is not late — setting a campaign up is "
      "not same-day work",
      "10404" not in orders_in(data) and data["waiting"] >= 1)
check("an order with no number is its own finding, not a missing campaign: "
      "there is nothing to look up for it",
      [r["client"] for r in data["unreconcilable"]] == ["No Number Co"]
      and "" not in orders_in(data))

# ---------------------------------------------------------------------------
section("Already running is a different urgency from merely late")

check("an order whose flight has started is marked as running",
      (row_for(data, "10402") or {}).get("started") is True
      and (row_for(data, "10403") or {}).get("started") is False
      and data["running"] >= 1)
check("and sorts above one that is merely late",
      orders_in(data).index("10402") < orders_in(data).index("10403"),
      orders_in(data))
check("the note says so in words, because “late to be set up” and "
      "“should be live right now” are two different conversations",
      "should be running now" in io_reconcile.note(data))
rep_rows = qa.io_reconcile_report()
by_order = {r[0]: st for r, st in zip(rep_rows["rows"],
                                      rep_rows["row_styles"])
            if isinstance(r[0], str)}
check("the row is drawn red and the merely-late one amber — a page of red is "
      "a page people scroll past",
      by_order.get("10402") == "bad" and by_order.get("10403") == "warn",
      {k: v for k, v in by_order.items() if k in ("10402", "10403")})

# ---------------------------------------------------------------------------
section("An order newer than the snapshot is not a finding about the order")

# A cache read a fortnight ago: order 10402 was submitted 40 days back and is
# a fair finding, but anything submitted since the read could not appear in it
# however long ago it was sent.
log_order("10405", "After The Read Co", 10, start="2026-08-20")
knack_products._write_cache([{"io": "10401"}])

# Age the cache by rewriting the stamp it records, so rows() reports it as a
# read taken a fortnight ago rather than one taken just now.
cache_file = knack_products._cache_path()
blob = json.loads(open(cache_file, encoding="utf-8").read())
blob["fetched"] = (NOW - timedelta(days=14)).timestamp()
open(cache_file, "w", encoding="utf-8").write(json.dumps(blob))

aged = io_reconcile.report()
check("the aged cache is still a live Knack read, not a refusal",
      aged["measured"] is True and aged["knack_source"].startswith("knack"),
      aged.get("knack_source"))
check("an order submitted after the products were read is not judged against "
      "them", "10405" not in orders_in(aged) and aged["after_read"] >= 1,
      aged.get("after_read"))
check("and it is said out loud rather than quietly dropped",
      "after the products were last read" in io_reconcile.note(aged))
check("an order older than the read is still judged — a stale cache is a real "
      "read of an earlier day, not a reason to refuse the whole report",
      "10402" in orders_in(aged))

# ---------------------------------------------------------------------------
section("Settling a row that is never going to appear")

knack_products._write_cache([{"io": "10401"}])
before_settle = orders_in(io_reconcile.report())
check("the row is on the list to begin with", "10403" in before_settle)

bad = io_reconcile.settle("10403", "not-a-reason")
check("a reason this does not offer is refused by name rather than stored",
      bad.get("ok") is False and "not-a-reason" in (bad.get("error") or ""))
check("and an order with no number cannot be settled — there is nothing to "
      "settle it under", io_reconcile.settle("", "cancelled").get("ok") is False)

ok = io_reconcile.settle("10403", "cancelled", "client pulled out", "Todd")
check("settling records who and when, because a mark nobody can attribute is "
      "one nobody can revisit",
      ok["row"]["by"] == "Todd" and ok["row"]["at"] and
      ok["row"]["reason"] == "cancelled")

after = io_reconcile.report()
check("the row leaves the outstanding list", "10403" not in orders_in(after))
check("and is listed under its own heading rather than disappearing — a list "
      "that quietly gets shorter cannot be told from one that failed to load",
      [r["order"] for r in after["settled"]] == ["10403"])
check("the note counts it", "settled as never expected" in io_reconcile.note(after))
check("and a book with nothing outstanding but something settled does not "
      "claim every order has a campaign — a settle mark is the opposite of "
      "one",
      "either" in io_reconcile.note({"measured": True, "checked": 1,
                                     "outstanding": [],
                                     "settled": [{"order": "1"}]}))

check("putting it back works", io_reconcile.unsettle("10403") is True
      and "10403" in orders_in(io_reconcile.report()))
check("and un-settling something that was never settled says so rather than "
      "reporting a clean success", io_reconcile.unsettle("10403") is False)

# The mark is applied on read, not baked into the rows. Two gunicorn workers:
# a mark folded into a cached payload is one the other worker goes on
# ignoring until its own copy expires, which is a button that appears to do
# nothing.
mod_src = open(os.path.join(ROOT, "hub", "io_reconcile.py"),
               encoding="utf-8").read()
import ast                                                    # noqa: E402
tree = ast.parse(mod_src)
report_fn = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "report")
calls = {n.func.id for n in ast.walk(report_fn)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("report() reads the settle marks itself on every run rather than being "
      "handed a list somebody cached", "settled" in calls, sorted(calls))

# ---------------------------------------------------------------------------
section("Nothing here writes to Knack, to Suite or to a quote")

# Read from the AST rather than the text: this module's own docstring names
# `io_clients.py` and Smart 1 Suite as the things it does not touch, and a
# check that matched prose would report the explanation as the defect — the
# rule test_sales_status.py already works to.
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ""
        imported.update(f"{base}.{a.name}" for a in node.names)
        imported.add(base)
banned = [n for n in imported
          if any(k in n.lower() for k in ("ghl", "suite", "knack_api",
                                          "requests"))]
check("it imports nothing that could write to Suite or to Knack", not banned,
      banned)
writes = {n.func.attr for n in ast.walk(tree)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
check("the only durable write it makes is its own settle overlay, through "
      "jsonstore", "write_json" in writes and "commit" not in writes)

qa_src = open(os.path.join(ROOT, "hub", "qa.py"), encoding="utf-8").read()
qa_fn = qa_src[qa_src.index("def io_reconcile_report("):]
qa_fn = qa_fn[:qa_fn.index("\n# Whole tools rather than")]
check("the QA report reads and renders and does not settle anything by "
      "arriving", ".settle(" not in qa_fn and ".unsettle(" not in qa_fn)

# ---------------------------------------------------------------------------
section("The report is reachable, and the row can be fixed from it")

check("it is registered under a name the QA page can run",
      "io-not-in-knack" in qa.REPORTS
      and qa.REPORTS["io-not-in-knack"]["fn"] is qa.io_reconcile_report)
check("its title names the finding rather than the process",
      qa.REPORTS["io-not-in-knack"]["title"] == "Orders With No Campaign")

tpl = open(os.path.join(ROOT, "hub", "templates", "qa_report.html"),
           encoding="utf-8").read()
check("the settle control is drawn from the payload's own reason list, so "
      "what a screen offers cannot drift from what the write accepts",
      "data.settle_reasons" in tpl and "c.io_settle" in tpl)
check("and there is a way back for a row settled by mistake",
      "c.io_unsettle" in tpl and "Put back" in tpl)

hub_src = open(os.path.join(ROOT, "hub", "__init__.py"), encoding="utf-8").read()
route = hub_src[hub_src.index('"/api/qa/io-reconcile/<action>"'):]
route = route[:route.index('@app.route("/api/qa/dashboard-skips")')]
check("the write is behind the API gate", "_require_api()" in route)
check("it records who pressed it", "current_user()" in route)
check("and it drops the day's stored copy of the report, or the row is still "
      "there on the next open and the button reads as having done nothing",
      'qa.forget("io-not-in-knack")' in route)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
