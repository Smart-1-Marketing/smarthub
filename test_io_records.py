"""The insertion order as a record, not just a PDF and a webhook.

    python3 test_io_records.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

Submitting an insertion order allocated a number from a Postgres sequence,
built two PDFs into Cloudinary, wrote one line in the activity log, and POSTed
the whole campaign to Smart 1 Suite. **Then it kept nothing.** No orders table,
no list of what had been sent, no way to reopen one, and no answer to "what
have we written for this client" until the campaign appeared in Knack weeks
later.

Three things followed, and all three were live. `hub/io_reconcile.py` had to
be assembled out of the activity log, which rotates. That log line carried an
empty order number on every entry the route had ever written and nothing
noticed, because nothing read it. And it was written at the *top* of the
route — before the request was validated — so a submit refused for missing
documents still logged an order and still registered the client, which the
reconciliation would then have reported as a campaign nobody set up.

What is asserted here is the ways a record quietly stops being one: a
resubmission that becomes a second order, a failed delivery recorded as no
order at all, a first submission re-dated by a later correction, a near name
collecting another company's order, and bookkeeping that can fail the thing it
is bookkeeping for.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-iorecords-")
# Assigned, never setdefault. A fresh directory is not isolation on its
# own: jsonstore keys its mirror *relative to the data root* by design,
# so an inherited DATABASE_URL refills this run's empty directory with
# the last run's rows. Both were setdefault here, which is right when
# neither is set and wrong the moment only the database is -- which is
# what a session-start hook exporting DATABASE_URL now gives every web
# session. This file writes durable rows, so it failed on the second
# run and passed on the first, and CI never saw it because every CI run
# gets a new Postgres.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "iorecords-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")
os.environ.pop("GHL_WEBHOOK_URL", None)

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
from hub import (audit, auth, io_clients, io_reconcile,             # noqa: E402
                 io_records, jsonstore)

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")


def order(number, client="Riverstone Dental", **kw):
    body = {
        "orderNumber": number, "client": client,
        "url": "https://riverstonedental.com", "partner": "TMRG",
        "salesContact": "Todd", "ioType": "New",
        "start": "2026-07-01", "end": "2026-12-31",
        "items": [{"product": "Connected TV - Targeted", "category": "OTT/CTV",
                   "rate": "38.00", "budget": 2500, "campaignBudget": 15000},
                  {"product": "Programmatic - Select Tactics",
                   "budget": 1500, "campaignBudget": 9000}],
        "client_pdf_url": f"https://res.cloudinary.com/x/io-{number}.pdf",
        "internal_pdf_url": f"https://res.cloudinary.com/x/io-{number}-i.pdf",
    }
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
section("One order, written down")

io_records.record(order("10412"), delivered=True, status=200, actor="Todd")
row = io_records.get("10412")
check("the order is on file", bool(row))
check("with who it is for and where the documents are",
      row["client"] == "Riverstone Dental"
      and row["client_pdf"].endswith("io-10412.pdf")
      and row["internal_pdf"].endswith("io-10412-i.pdf"))
check("the money is the order's own arithmetic, not a figure retyped",
      row["monthly"] == 4000.0 and row["campaign_total"] == 24000.0,
      (row["monthly"], row["campaign_total"]))
check("the lines are kept, in the shape a person reads them",
      row["line_count"] == 2
      and row["lines"][0]["product"] == "Connected TV - Targeted"
      and row["lines"][0]["budget"] == 2500.0)
check("the domain is derived, so the record joins on the one thing that "
      "identifies a business exactly",
      row["domain"] == "riverstonedental.com", row.get("domain"))
check("and the whole wizard state is not kept — this is the only copy, on a "
      "disk shared with everything else",
      "targetAreas" not in row and "brandfetch" not in row)

# ---------------------------------------------------------------------------
section("A resubmission is the same order, at a new revision")

first_at = row["submitted_at"]
again = io_records.record(order("10412"), delivered=False,
                          error="Suite refused: 502", status=502, actor="Todd")
check("it updates rather than adding a second row — two rows under one number "
      "is how a client record grows three identical entries",
      again["resubmitted"] is True
      and len(io_records.listing()["rows"]) == 1)
after = io_records.get("10412")
check("the first submission's date survives the correction: an order written "
      "in July must not be dated to the day somebody fixed a typo",
      after["submitted_at"] == first_at)
check("and both attempts are on the row, so the correction is visible without "
      "the record splitting", len(after["submissions"]) == 2)

# ---------------------------------------------------------------------------
section("Three facts about Smart 1 Suite, not one")

suite = after["suite"]
check("the latest attempt failed", suite["delivered"] is False)
check("but Suite still holds an earlier version, which is a different state "
      "from an order that never reached it", suite["ever_delivered"] is True)
check("and when it last landed is kept", bool(suite["delivered_at"]))
check("the refusal itself is carried, rather than an invented diagnosis of it",
      "502" in suite["error"])

io_records.record(order("10413", client="Never Sent Co"), delivered=False,
                  error="GHL_WEBHOOK_URL is not configured", actor="Todd")
check("an order Suite never took at all is still recorded — the client has it "
      "either way, and it is the one that needs chasing",
      io_records.get("10413") is not None
      and io_records.get("10413")["suite"]["ever_delivered"] is False)

# ---------------------------------------------------------------------------
section("Whose order it is")

check("a client's own orders come back",
      [r["order"] for r in io_records.listing("Riverstone Dental")["rows"]]
      == ["10412"])
check("a near name collects nothing — attributing one company's insertion "
      "order to another is the worst outcome available to a client record",
      io_records.listing("Riverstone Dental Supply")["rows"] == [])
check("case and spacing are not a different client",
      len(io_records.listing("  riverstone   dental ")["rows"]) == 1)
check("“nobody has sent one” and “we could not read the store” are different "
      "answers", io_records.listing()["measured"] is True
      and "measured" in io_records.listing("nobody"))
check("an order with no number is refused by name rather than filed under a "
      "blank", io_records.record({"client": "X"})["ok"] is False)
check("and a number that is a path fragment cannot escape the store",
      io_records.key_for("../../etc/passwd") == "etcpasswd",
      io_records.key_for("../../etc/passwd"))

# ---------------------------------------------------------------------------
section("Bookkeeping never fails the thing it is bookkeeping for")

src = open(os.path.join(ROOT, "modules", "io_builder", "app.py"),
           encoding="utf-8").read()
submit = src[src.index('@app.post("/api/submit-io")'):]
submit = submit[:submit.index("def _payload_areas")]
check("the record, the log entry and the client overlay are written in one "
      "place, so they cannot disagree about what was submitted",
      submit.count("def _keep(") == 1
      and "io_records.record(" in submit
      and 'audit.log(' in submit
      and "io_clients.register_from_io(" in submit)
check("and nothing in there is written before the request is validated: a "
      "submit refused for missing documents used to log an order anyway, "
      "which the reconciliation would report as a campaign nobody set up",
      submit.index("client PDF URL and internal PDF URL")
      < submit.index("def _keep("))

# The route proves it rather than the reading proving it.
r = staff.post("/tools/io/api/submit-io",
               json={"orderNumber": "10999", "client": "Nope"})
check("a submit with no documents is refused", r.status_code == 400)
check("and records nothing at all", io_records.get("10999") is None)
check("logs nothing", not [e for e in audit.tail(80, module="io_builder",
                                                 type_="io_submitted")
                          if e.get("order") == "10999"])
check("and registers nobody",
      "nope" not in {k.lower() for k in io_clients.overlay()})

r2 = staff.post("/tools/io/api/submit-io", json=order("10600", client="Live Co"))
check("a submit the deployment cannot deliver still tells the rep why",
      r2.status_code == 500 and "GHL_WEBHOOK_URL" in (r2.get_json() or {}).get("error", ""))
check("and is recorded, because the client has the order whatever Suite did",
      (io_records.get("10600") or {}).get("client") == "Live Co")
logged = [e for e in audit.tail(80, module="io_builder", type_="io_submitted")
          if e.get("order") == "10600"]
check("the activity log finally carries the order number the payload actually "
      "uses", bool(logged), [e.get("order") for e in
                             audit.tail(20, module="io_builder",
                                        type_="io_submitted")])
check("with the partner and the flight beside it, because a chase list needs "
      "to know who to ask and whether the campaign should already be running",
      logged and logged[0].get("partner") == "TMRG"
      and logged[0].get("start") == "2026-07-01")

# ---------------------------------------------------------------------------
section("The reconciliation reads the durable half")

from hub import knack_products                                      # noqa: E402

# Age every record past the grace window. An order submitted this morning is
# deliberately not chased — setting a campaign up is not same-day work — so a
# test that skipped this would assert against the waiting list instead.
_old = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=30)
        ).isoformat(timespec="seconds")
for _row in io_records.listing()["rows"]:
    _row["submitted_at"] = _old
    jsonstore.write_json(io_records._path(_row["order"]), _row)

knack_products._write_cache([{"io": "10412"}])
data = io_reconcile.report()
sources = {r["order"]: r["sources"] for r in data["outstanding"]}
check("orders come from the record store, which does not rotate",
      "order record" in sources.get("10600", []), sources)
check("and the note stops claiming the activity log is the horizon once the "
      "Hub keeps its own records",
      data["has_records"] is True
      and "which rotates" not in io_reconcile.note(data))
check("an order Suite never took is named as reaching neither system",
      data["not_delivered"] >= 1
      and "reached neither system" in io_reconcile.note(data))
check("a number handed out that never became an order is deliberately not "
      "tracked — nobody here asks about gaps in the numbering, and machinery "
      "kept alive for a question nobody puts is machinery to maintain",
      not hasattr(io_records, "note_allocated")
      and not hasattr(io_records, "unused_allocations")
      and "unused_numbers" not in data)

# ---------------------------------------------------------------------------
section("The client's own record answers it")

got = staff.get("/api/client/orders?name=Riverstone%20Dental").get_json()
check("the orders are on the client record", got["measured"] is True
      and [r["order"] for r in got["orders"]] == ["10412"])
check("a near name is not this client's order",
      staff.get("/api/client/orders?name=Riverstone%20Dental%20Supply"
                ).get_json()["orders"] == [])
check("the route is under /api/client/, which suite_embed allowlists — a card "
      "pointed anywhere else renders on every screen except inside the Suite "
      "frame, and fails silently there",
      "/api/client/" in "/api/client/orders")
from hub import suite_embed                                         # noqa: E402
check("and that allowlist really does cover it",
      any(str(p).startswith("/api/client") for p in suite_embed.EMBEDDABLE),
      list(suite_embed.EMBEDDABLE)[:6])
check("an anonymous request is refused — these are client names and money",
      Client(wsgi.application).get("/api/client/orders?name=x").status_code
      in (401, 403))

tpl = open(os.path.join(ROOT, "hub", "templates", "client360.html"),
           encoding="utf-8").read()
check("the card is on the record", 'id="c-orders"' in tpl
      and "function loadOrders" in tpl)
check("it says which kind of empty it is rather than drawing a clean nothing",
      "could not be read" in tpl and "not the same as no orders" in tpl)
check("whether Knack has the campaign is read off data already on the page, "
      "not from a second reconciliation that would come to disagree",
      "__c360ioNums" in tpl and "/api/qa/io-not-in-knack" not in tpl)
check("and it never claims “no campaign yet” when the products card did not "
      "answer — absent data must not read as a finding",
      "instanceof Set" in tpl and "known && !known.has(key)" in tpl)

# ---------------------------------------------------------------------------
section("Nothing here writes to Knack, to Suite or to a quote")

import ast                                                          # noqa: E402
tree = ast.parse(open(os.path.join(ROOT, "hub", "io_records.py"),
                      encoding="utf-8").read())
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add(node.module or "")
        imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
banned = [n for n in imported
          if any(k in n.lower() for k in ("ghl", "suite", "knack", "requests"))]
check("it imports nothing that could reach Knack or Smart 1 Suite", not banned,
      banned)
calls = {n.func.attr for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
check("it writes through jsonstore, so the record outlives the disk Render "
      "does not back up", "write_json" in calls)
check("and never with a bare os.remove, which the mirror would undo",
      "remove" not in calls and "unlink" not in calls)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
