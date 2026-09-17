"""The activity log's `client` column, and the boundary its backfill leaves.

    python3 test_activity_client_column.py

No pytest, no new dependencies, a throwaway data directory and SQLite
database.

## Why this file exists

`work_log()` answered one client's work out of a WINDOW: the newest N rows of
the work modules, filtered in Python. `docs/claude/03` said the real fix was a
`client` column and named the condition on it -- **a half-backfilled column is
the same silent truncation with a new boundary**, because a row written before
the column existed would read as "no client" rather than "nobody has looked at
this row yet".

So the column distinguishes the two, and the table says where the backfill is
rather than a watermark kept somewhere else:

  * ``client IS NULL``  -- nobody has looked at this row;
  * ``client = ''``     -- looked at, names no client;
  * ``client = 'acme'`` -- this client's row.

What it holds: the column is written on every insert from the same function
the backfill uses; the backfill is batched, resumable and idempotent;
``backfill_state()`` reports where it has reached; and -- the point of the
whole exercise -- a client whose rows are BELOW the backfill boundary is
reported as not-yet-answerable rather than as a client nothing was ever made
for.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1actclient_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "activity-client-test"

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import audit, client_brand, client_upcoming                 # noqa: E402
from hub import scheduler                                            # noqa: E402

WORK = sorted(client_brand.WORK_KINDS)[0]
OTHER = sorted(client_brand.WORK_KINDS)[1]


# ---------------------------------------------------------------- the key
section("One key, written by the writer and queried by the reader")

check("punctuation and case fall out of the key",
      audit.client_key("Acme Tyre, LLC."), "acmetyrellc")
check("client_brand._norm is that same function -- not a second copy",
      client_brand._norm("Acme Tyre, LLC."), audit.client_key("Acme Tyre, LLC."))
check("CLIENT_KEYS is read from audit, so the column cannot be filled from "
      "one list and searched by another",
      client_brand.CLIENT_KEYS is audit.CLIENT_KEYS)
check("a row naming a client carries its key",
      audit.client_key_of({"client_name": "Acme Tyre"}), "acmetyre")
check("the keys are read in order -- `client` wins over `company`",
      audit.client_key_of({"company": "Beta", "client": "Alpha"}), "alpha")
check("a row naming nobody is EMPTY, never None -- None means nobody has "
      "looked at it yet, which is a different answer",
      audit.client_key_of({"module": "auth"}), "")


# ------------------------------------------------------------ the writes
section("Every row written carries the column")

audit.log(WORK, "made", actor="Todd", client="Oldest Co", detail="the one thing")
for i in range(40):
    audit.log("auth", "signin", actor=f"Person {i}")

state = audit.backfill_state()
check("a table that has only ever been written to is already filled",
      (state["measured"], state["pending"], state["done"]), (True, 0, True))
check("...so the backfill is a no-op on it",
      audit.backfill_clients()["written"], 0)


# --------------------------------------------------------- the narrowing
section("The work log asks a question rather than sweeping a window")

log = client_brand.work_log("Oldest Co")
check("the client's row comes back", log["count"], 1)
check("...complete, because the column covers the whole table", log["complete"])
check("...and it read ONE row to answer, not a window of everybody's",
      log["scanned"], 1)
check("a client with nothing filed is empty and still complete",
      (client_brand.work_log("Nobody Inc")["count"],
       client_brand.work_log("Nobody Inc")["complete"]), (0, True))
check("the query narrows on the key, so punctuation does not lose the client",
      client_brand.work_log("Oldest Co.")["count"], 1)


# ------------------------------------------------------- the backfill gap
section("The boundary a half-finished backfill leaves")

# Rows as they look on a Hub that ran before the column existed: written, then
# the column blanked back to NULL underneath them.
audit.log(OTHER, "made", actor="Todd", client="Older Client", detail="last spring")
with audit._engine.begin() as cx:
    cx.execute(audit._table.update()
               .where(audit._table.c.client == audit.client_key("Older Client"))
               .values(client=None))

state = audit.backfill_state()
check("the state says rows are still unfilled",
      (state["measured"], state["pending"] > 0, state["done"]), (True, True, False))

older = client_brand.work_log("Older Client")
check("their work is NOT found -- the column has not reached it", older["count"], 0)
check("...and the answer does NOT claim to be complete", older["complete"], False)
check("...it says the column is not backfilled yet", older["backfilled"], False)
check("...and names how far back it can speak for", bool(older["horizon"]))

act = client_upcoming.last_activity("Older Client", "", None)
check("so the health pill is NOT measured -- it cannot read 'idle'",
      act["measured"], False)
check("...which is the whole point: a client below the boundary must not read "
      "as one nothing was ever made for",
      "further back" in (act.get("error") or ""))

from hub import prospect                                             # noqa: E402
sec = prospect._work(lead={"company": "Older Client"})
check("the prospect card does not say 'nothing has been produced'",
      sec.get("measured"), False)


# ----------------------------------------------------------- the backfill
section("The backfill: batched, resumable, idempotent")

out = audit.backfill_clients(batch=1)
check("one batch fills one row", out["written"], 1)
check("...and reports it is finished when nothing is left", out["done"], True)
check("running it again writes nothing", audit.backfill_clients()["written"], 0)

older = client_brand.work_log("Older Client")
check("now their work is on the record", older["count"], 1)
check("...and the answer is complete", older["complete"], True)
check("...with the detail intact", older["items"][0]["detail"], "last spring")

act = client_upcoming.last_activity("Older Client", "", None)
check("the health pill is measured again", act["measured"], True)
check("...and reads a real gap rather than 'idle'", act["state"] != "idle")


# ------------------------------------------------------ a row it cannot read
section("A payload that will not parse still gets a key, or the backfill never ends")

with audit._engine.begin() as cx:
    cx.execute(audit._table.insert().values(
        at=audit._when({}), module=WORK, type="made", actor="Todd",
        client=None, payload="{not json at all"))
check("that row is pending", audit.backfill_state()["pending"], 1)
audit.backfill_clients()
check("the backfill gives it a key rather than leaving it NULL for ever -- "
      "one unreadable row must not hold every reader in the incomplete branch",
      audit.backfill_state()["pending"], 0)
check("...and the table reads complete again", audit.backfill_state()["done"], True)


# -------------------------------------------------------------- the job
section("The scheduled job")

res = scheduler.job_backfill_activity_clients(None)
check("a finished backfill is a no-op", (res["done"], res["written"]), (True, 0))
check("the job is registered with its own schedule",
      "backfill_activity_clients" in scheduler.JOBS)
check("...at a short interval, because the card reads incomplete until it ends",
      scheduler.JOBS["backfill_activity_clients"][0] <= 60)


# --------------------------------------------------- work_index still works
section("work_index asks about the whole book, so it passes no client")

idx = client_brand.work_index()
check("the index still buckets by client",
      len(idx["rows"].get(audit.client_key("Oldest Co"), [])), 1)
check("...and reports its own window", sorted(idx),
      ["complete", "error", "horizon", "rows", "scanned"])


# ------------------------------------------------- the migration itself
section("The path a live Hub takes: a table that predates the column")

# In a subprocess with its own database, because this one's table was created
# WITH the column and the question is what happens to one that was not. This
# is the only part of the change that touches a table with production rows in
# it, so it is asserted rather than reasoned about.
import json as _json                                                 # noqa: E402
import subprocess                                                    # noqa: E402

_CREATE = ("CREATE TABLE hub_activity ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT, at DATETIME NOT NULL, "
           "module VARCHAR(80) NOT NULL, type VARCHAR(80) NOT NULL, "
           "actor VARCHAR(60), payload TEXT NOT NULL)")
_INSERT = ("INSERT INTO hub_activity (at, module, type, actor, payload) "
           "VALUES (:at,:m,:t,:a,:p)")

_script = os.path.join(TMP, "migration_probe.py")
with open(_script, "w", encoding="utf-8") as fh:
    fh.write("\n".join([
        "import json, os, sys, tempfile",
        f"sys.path.insert(0, {str(ROOT)!r})",
        "t = tempfile.mkdtemp()",
        "os.environ['HUB_DATA_DIR'] = t",
        "os.environ['AUDIT_LOG_PATH'] = os.path.join(t, 'a.jsonl')",
        "DB = os.path.join(t, 'h.db')",
        "os.environ['DATABASE_URL'] = 'sqlite:///' + DB",
        "os.environ['SECRET_KEY'] = 'x'",
        "import sqlalchemy as sa",
        "eng = sa.create_engine('sqlite:///' + DB)",
        "with eng.begin() as cx:",
        f"    cx.execute(sa.text({_CREATE!r}))",
        "    for i in range(5):",
        f"        cx.execute(sa.text({_INSERT!r}), {{",
        "            'at': '2026-01-01 09:00:00', 'm': " + repr(WORK) + ",",
        "            't': 'made', 'a': 'Todd',",
        "            'p': json.dumps({'module': " + repr(WORK) + ", 'type': 'made',",
        "                             'client': 'Legacy Co',",
        "                             'time': '2026-01-01T09:00:00+00:00',",
        "                             'detail': 'old work'})})",
        "eng.dispose()",
        "from hub import audit, client_brand",
        f"audit.log({WORK!r}, 'made', actor='Todd', client='New Co', detail='today')",
        "eng = sa.create_engine('sqlite:///' + DB)",
        "cols = sorted(c['name'] for c in sa.inspect(eng).get_columns('hub_activity'))",
        "eng.dispose()",
        "before = client_brand.work_log('Legacy Co')",
        "filled = audit.backfill_clients()",
        "after = client_brand.work_log('Legacy Co')",
        "print(json.dumps({'columns': cols,",
        "    'before': {'count': before['count'], 'complete': before['complete'],",
        "               'backfilled': before['backfilled']},",
        "    'written': filled.get('written'),",
        "    'after': {'count': after['count'], 'complete': after['complete']},",
        "    'new_client': client_brand.work_log('New Co')['count']}))",
    ]))

_proc = subprocess.run([sys.executable, _script], capture_output=True,
                       text=True, timeout=300)
try:
    _m = _json.loads(_proc.stdout.strip().splitlines()[-1])
except Exception:                                       # noqa: BLE001
    _m = {"error": (_proc.stderr or _proc.stdout)[-500:]}
    print("    migration probe said:", _m["error"])

check("the column is ALTERed onto a table that did not have it",
      "client" in (_m.get("columns") or []), True)
check("the legacy rows are not found yet -- nobody has looked at them",
      (_m.get("before") or {}).get("count"), 0)
check("...and the answer says so rather than claiming complete",
      ((_m.get("before") or {}).get("complete"),
       (_m.get("before") or {}).get("backfilled")), (False, False))
check("a row written after the ALTER carries its client immediately",
      _m.get("new_client"), 1)
check("the backfill fills every legacy row", _m.get("written"), 5)
check("...and then the client's whole history is on the record, complete",
      ((_m.get("after") or {}).get("count"),
       (_m.get("after") or {}).get("complete")), (5, True))


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
