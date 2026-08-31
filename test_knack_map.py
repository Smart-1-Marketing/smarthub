"""Every Knack object and field this Hub knows about, and who owns each one.

    python3 test_knack_map.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

Knack is the system of record, this Hub reaches into it from nine modules, and
there was no one description of what it thinks each object and field is — so
"which mappings have we confirmed, and which are still somebody's assumption?"
could only be answered by reading nine files. That question matters more the
moment more is pushed into Knack: a field id pinned to the wrong column writes
into the wrong place on a live record, and Knack refuses the **whole** record
over one bad value, so an unconfirmed mapping costs the write rather than the
field.

What is asserted here is the ways a record like this quietly stops being one:
a second copy of the ids that drifts from the modules that own them, a
confirmation that survives the field being repinned, a clean-looking page over
a builder nobody could read, and a "map" that turns out to be able to write.
"""
import ast
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-knackmap-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "knackmap-test")
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


from werkzeug.test import Client                                    # noqa: E402
import wsgi                                                         # noqa: E402
from hub import auth, knack_api, knack_map, knack_products, qa      # noqa: E402
from hub import knack_websites                                      # noqa: E402

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")

# ---------------------------------------------------------------------------
section("The map is read from the modules that own the ids, never restated")

src = open(os.path.join(ROOT, "hub", "knack_map.py"), encoding="utf-8").read()
tree = ast.parse(src)
# `field_<digits>` and nothing looser: "field_ids" is the name of the
# accessor this file reads *through*, and matching it would report the fix as
# the defect — the rule the config drift check works to.
_FIELD_ID = __import__("re").compile(r"^field_\d+$")
literals = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _FIELD_ID.match(n.value)]
check("it carries no field id of its own — a second copy is the drift the "
      "ALIASES table and the two rate cards have each paid for",
      not literals, literals[:5])

rows = knack_map.fields()
by = {(r["object"], r["key"]): r for r in rows}
check("object_107's mapping is the one knack_api publishes",
      all(by[("object_107", k)]["field"] == v
          for k, v in knack_api.field_ids().items()))
check("object_121's is SUPPORT_FIELDS",
      all(by[("object_121", k)]["field"] == v
          for k, v in knack_api.SUPPORT_FIELDS.items()))
check("object_153's is knack_websites.FIELDS",
      all(by[("object_153", k)]["field"] == v
          for k, v in knack_websites.FIELDS.items()))
check("object_135's is read off knack_products' own constants",
      by[("object_135", "monthly_cost")]["field"] == knack_products.F_MONTHLY_COST
      and by[("object_135", "io_number")]["field"] == knack_products.F_IO_NUM)
check("including the three the campaign-asset queue is gated on",
      by[("object_135", "assets_flag")]["field"] == knack_products.F_ASSETS_FLAG)

# The load-bearing property: repin a field and the map follows without an edit.
_real = knack_products.F_MONTHLY_COST
try:
    knack_products.F_MONTHLY_COST = "field_99999"
    moved = {(r["object"], r["key"]): r for r in knack_map.fields()}
    check("a field repinned in its owning module moves here with no edit to "
          "this one", moved[("object_135", "monthly_cost")]["field"] == "field_99999")
finally:
    knack_products.F_MONTHLY_COST = _real

# ---------------------------------------------------------------------------
section("Pinned and matched-by-label are not the same claim")

summary = knack_map.summary()
check("the one object still matched by label is named rather than listed as "
      "though it were mapped",
      summary["unpinned_objects"] == ["object_140"], summary["unpinned_objects"])
check("and it is a write target, which is why it matters",
      "knack_api.create_campaign_request"
      in knack_map.OBJECTS["object_140"]["writes"])
check("every write path in the Hub is on the record",
      set(summary["write_paths"]) >= {
          "knack_api.create_ticket", "knack_api.update_ticket",
          "knack_api.create_campaign_request", "knack_api.set_dashboard_url",
          "knack_websites.update_record", "knack_websites.set_analytics_ids"},
      summary["write_paths"])
check("a field the Hub writes is marked apart from one it only reads — one is "
      "a mapping that has to be right before the next push",
      by[("object_153", "registrar")]["written"] is True
      and by[("object_107", "assigner")]["written"] is False)

# ---------------------------------------------------------------------------
section("A confirmation is a person, a date and a field")

field = by[("object_153", "registrar")]["field"]

# Start this file's own rows from a known state, and never assert on the
# totals. `jsonstore` keys its mirror *relative to the data root*, so
# hub/knack_map_confirmed.json is one key however many temporary data
# directories there are — and CI runs every suite against one shared Postgres,
# so a confirmation an earlier run of this very file made is restored into
# this one. That is the mirror doing its job; a test that read the whole book
# as its own would be the thing that was wrong.
for _obj, _key in (("object_153", "registrar"), ("object_107", "billable")):
    knack_map.unconfirm(_obj, _key)
_fresh = {(r["object"], r["key"]): r for r in knack_map.fields()}
check("the row this file is about starts unconfirmed",
      _fresh[("object_153", "registrar")]["confirmed"] is False)
check("an object or a field left out is refused rather than stored",
      knack_map.confirm("", "registrar", field)["ok"] is False
      and knack_map.confirm("object_153", "registrar", "")["ok"] is False)

got = knack_map.confirm("object_153", "registrar", field, actor="Harness",
                        note="checked in the builder")
check("confirming records who and when", got["ok"] and got["row"]["by"] == "Harness"
      and got["row"]["at"])
after = {(r["object"], r["key"]): r for r in knack_map.fields()}
check("and the row says so", after[("object_153", "registrar")]["confirmed"] is True
      and after[("object_153", "registrar")]["confirmed_by"] == "Harness")

check("a field is confirmed once for the object, so every tool that reads it "
      "inherits that rather than checking it four times",
      len([r for r in knack_map.fields()
           if r["object"] == "object_153" and r["key"] == "registrar"]) == 1)

# The one way this record could become worse than having none.
knack_map.confirm("object_153", "registrar", "field_00000", actor="Harness")
stale = {(r["object"], r["key"]): r for r in knack_map.fields()}
row = stale[("object_153", "registrar")]
check("a confirmation given for an id the code no longer pins is retired, not "
      "carried silently onto a different column",
      row["confirmed"] is False and row["superseded"] is True
      and row["was_confirmed_as"] == "field_00000")
check("and the page counts it as superseded rather than as unconfirmed",
      knack_map.summary()["superseded"] >= 1)

check("a confirmation can be taken back",
      knack_map.unconfirm("object_153", "registrar") is True)
check("and taking back one that was never given says so rather than reporting "
      "a clean success",
      knack_map.unconfirm("object_153", "registrar") is False)

# ---------------------------------------------------------------------------
section("Without Knack it is not measured, and says which half is missing")

checked = knack_map.verify()
check("the live check refuses rather than drawing ticks nobody earned",
      checked["measured"] is False and checked["rows"] == [])
check("and names why", "not configured" in (checked.get("error") or ""),
      checked.get("error"))

report = qa.knack_field_map()
check("the report is still measured, because the map itself is the answer it "
      "exists to give — reporting the whole thing unmeasurable would hide the "
      "record somebody is meant to work down",
      report["measured"] is True and len(report["rows"]) > 100)
check("but it says what is missing rather than implying it checked",
      "not configured" in report["note"]
      and "not measured" in str(report["rows"]))
check("and names the count that is the actual to-do list",
      "have not been confirmed" in report["note"])

# ---------------------------------------------------------------------------
section("Nothing here writes to Knack")

calls = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        calls.add(node.func.attr)
check("no create, update or delete of a Knack record — it reads the schema "
      "and writes one Hub-side note",
      not ({"post", "put", "delete", "create_ticket", "update_ticket",
            "update_record", "create_campaign_request"} & calls),
      sorted(calls))
check("its only durable write is through jsonstore", "write_json" in calls)

names = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        names.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        names.add(node.module or "")
check("and it does not import requests, so it could not reach an API directly "
      "even by accident", "requests" not in names, sorted(names))

# ---------------------------------------------------------------------------
section("The report is reachable, and a row can be confirmed from it")

check("registered under a name the QA page can run",
      "knack-field-map" in qa.REPORTS
      and qa.REPORTS["knack-field-map"]["fn"] is qa.knack_field_map)
check("the page renders", staff.get("/qa/knack-field-map").status_code == 200)

tpl = open(os.path.join(ROOT, "hub", "templates", "qa_report.html"),
           encoding="utf-8").read()
check("the confirm control is on the row rather than on a separate screen",
      "c.knack_confirm" in tpl and "qa-kmap-go" in tpl)
check("and a confirmation can be undone from the same row",
      "qa-kmap-undo" in tpl)
check("the press names the id it is confirming, so nobody ticks a field "
      "without seeing which column it is",
      "parts[1] + ' = ' + parts[2]" in tpl)

hub_src = open(os.path.join(ROOT, "hub", "__init__.py"), encoding="utf-8").read()
route = hub_src[hub_src.index('"/api/qa/knack-map/<action>"'):]
route = route[:route.index("@app.route", 10)]
check("the write is behind the API gate", "_require_api()" in route)
check("it records who pressed it", "current_user()" in route)
check("and drops the day's stored copy, or the row is still there on the next "
      "open and the button reads as having done nothing",
      'qa.forget("knack-field-map")' in route)

res = staff.post("/api/qa/knack-map/confirm",
                 json={"object": "object_107", "key": "billable",
                       "field": knack_api.field_ids()["billable"]})
check("a staff press confirms", res.status_code == 200
      and (res.get_json() or {}).get("ok") is True)
check("an unknown action is refused by name",
      staff.post("/api/qa/knack-map/nonsense", json={}).status_code == 400)
check("and an anonymous request cannot confirm anything — this is the record "
      "that gates writing to Knack",
      Client(wsgi.application).post(
          "/api/qa/knack-map/confirm", json={}).status_code in (401, 403))

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
