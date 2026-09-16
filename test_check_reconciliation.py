"""Check Reconciliation: matching, allocation and the QBO payment payload.

    python3 test_check_reconciliation.py

No pytest and no new dependencies — the repo's own harness, so this file can
be registered in `.github/workflows/checks.yml`, the single gate. It began
life as a pytest file under `tests/` with a workflow of its own, and
`test_knack_websites_source.py` asserts there is exactly one workflow for the
same reason CLAUDE.md gives about the ci.yml that was folded in: two gates
disagreeing about what green means is worse than either alone.

Nothing here reaches QuickBooks or OpenAI: these are the pure halves of the
module — name normalization, match scoring, allocation suggestion and the
Payment payload — driven directly.
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

# Pinned BEFORE the import, because the module resolves its root through
# `jsonstore.data_dir()` -- without this the store checks below would write
# into whatever data directory this machine has, which on the live service is
# the real one.
_TMP = tempfile.mkdtemp(prefix="s1-checkrec-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("CHECK_RECONCILIATION_DATA_DIR", None)
# Pinned outright rather than setdefault: a file that owns its HUB_DATA_DIR
# and INHERITS its DATABASE_URL is the shape test_jsonstore.py names -- the
# mirror is keyed relative to the data root, so two runs against one shared
# database meet each other's rows and the second one reads what the first
# wrote. Owning it names the database this file uses instead of taking
# whatever the machine had. It does NOT isolate the phases below from each
# other: they share this URL by design, and because the key is relative a
# second temporary directory is the same key. See the concurrency section.
os.environ["DATABASE_URL"] = (os.environ.get("CHECKREC_TEST_DATABASE_URL")
                              or "sqlite:///" + os.path.join(_TMP, "hub.sqlite3"))
os.environ.setdefault("SECRET_KEY", "fixture-only")

from hub import jsonstore  # noqa: E402
from modules.check_reconciliation import app as cr  # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


print("Name normalization and matching")
check("legal suffixes drop from a normalized name",
      cr._normalize_name("Trasin Corporation"), "trasin")
check("punctuation and LLC drop too",
      cr._normalize_name("N2 Advertising, LLC"), "n2 advertising")
a = {"DisplayName": "Trasin Asphalt and Concrete"}
b = {"DisplayName": "Unrelated Company"}
check("a shared distinctive name outranks an unrelated one",
      cr._score_name("Trasin Corporation", a) > cr._score_name("Trasin Corporation", b))

print("\nAllocation suggestions")
invoices = [{"id": "1", "balance": 24.50, "late_fees": 0}]
result = cr._suggest_allocations(24.50, invoices)
check("an exact invoice balance is suggested whole",
      result["allocations"], [{"invoice_id": "1", "amount": 24.50}])
invoices = [{"id": "1", "balance": 25.24, "late_fees": .74}]
result = cr._suggest_allocations(24.50, invoices)
check("a check matching the pre-late-fee principal is flagged, not silently applied",
      result.get("late_fee_warning"), True)
check("and still points at the invoice it matches",
      result["allocations"][0]["invoice_id"], "1")
invoices = [
    {"id": "a", "balance": 147.00, "late_fees": 0},
    {"id": "b", "balance": 147.00, "late_fees": 0},
    {"id": "c", "balance": 169.00, "late_fees": 0},
    {"id": "d", "balance": 169.00, "late_fees": 0},
]
result = cr._suggest_allocations(632.00, invoices)
check("an exact multi-invoice combination is found", len(result["allocations"]), 4)
check("and its amounts sum to the check",
      round(sum(x["amount"] for x in result["allocations"]), 2), 632.00)

print("\nThe QuickBooks Payment payload")
payload = cr._payment_payload("3176", "2026-08-11", "1234",
                              [{"invoice_id": "99", "amount": 2000.0}],
                              "Cars & Carts Automotive")
check("the customer rides on CustomerRef", payload["CustomerRef"]["value"], "3176")
check("the total is the allocation total", payload["TotalAmt"], 2000.0)
check("the check number becomes PaymentRefNum", payload["PaymentRefNum"], "1234")
check("each line links its invoice",
      payload["Line"][0]["LinkedTxn"][0], {"TxnId": "99", "TxnType": "Invoice"})

print("\nWhere the state lives")
# It held the QuickBooks OAuth tokens, every payer alias the tool has learned,
# the reconciliation records and the audit trail -- in one JSON file under a
# hard-coded /var/data, mirrored by nothing. The disk is outside the database
# backup and does not survive being recreated.
check("the root comes from the shared data directory",
      str(cr._data_root()).startswith(_TMP))
check("not from a hard-coded /var/data",
      "/var/data" in str(cr._data_root()), False)

# Resolved per call rather than captured at import: the constant this replaces
# was read once at module load, so a variable set after that read as applied
# and was not.
_was = os.environ["HUB_DATA_DIR"]
_other = tempfile.mkdtemp(prefix="s1-checkrec-moved-")
os.environ["HUB_DATA_DIR"] = _other
try:
    check("and it is resolved per call, not captured at import",
          str(cr._data_root()).startswith(_other))
finally:
    os.environ["HUB_DATA_DIR"] = _was

check("a fresh install reads the default shape",
      sorted(cr._read_state()), ["aliases", "audit", "checks", "oauth", "version"])


def _remember(state):
    state["aliases"]["trasin"] = {"customer": "Trasin Corporation"}
    return "remembered"


check("_mutate still hands back what the caller returned",
      cr._mutate(_remember), "remembered")
check("and the change is there on the next read",
      cr._read_state()["aliases"]["trasin"]["customer"], "Trasin Corporation")

# The point of the move: the file is no longer the only copy.
check("the state is mirrored into the database",
      jsonstore.key_for(str(cr._data_file())) in jsonstore._mirrored)
check("and is not declared a cache",
      jsonstore.key_for(str(cr._data_file())) in jsonstore._declared_caches,
      False)
# One door onto the file, so there is nowhere left that writes it without
# taking the locks below.
check("there is no second writer beside _mutate",
      hasattr(cr, "_write_state"), False)

# _LOCK is a threading.RLock: it serialises the threads inside one gunicorn
# worker and says nothing about the other one, and this deployment runs two.
# Two reps approving at the same moment each read, each changed their copy and
# each wrote the lot back -- the second write dropping the first allocation,
# which is a QuickBooks payment that was made and is no longer recorded as
# made. Driven rather than read: prose naming a lock is not a lock being taken.
#
# Patched on `_exclusive`, not `exclusive`: `_exclusive = exclusive` is bound
# at import, so `update_json()` holds a reference the public name no longer
# reaches. Patching the public one instead counts zero and reads as "no lock
# was taken" -- a lock check that fails for the wrong reason is one somebody
# deletes.
_taken = {"n": 0}
_real_exclusive = jsonstore._exclusive

import contextlib  # noqa: E402


@contextlib.contextmanager
def _counting(path):
    _taken["n"] += 1
    with _real_exclusive(path):
        yield


jsonstore._exclusive = _counting
try:
    cr._mutate(lambda state: state["audit"].append({"a": 1}))
    check("a change takes the cross-worker lock, not just the local one",
          _taken["n"], 1)
finally:
    jsonstore._exclusive = _real_exclusive

# A mutate that raises leaves the state alone -- an approval that failed
# part-way through may not half-write a payment record.
_before = json.dumps(cr._read_state(), sort_keys=True)


def _explodes(state):
    state["checks"].append({"id": "half-written"})
    raise RuntimeError("the approval failed")


try:
    cr._mutate(_explodes)
except RuntimeError:
    pass            # surfacing is the point; what matters is what was written
check("a mutate that raises writes nothing",
      json.dumps(cr._read_state(), sort_keys=True), _before)

# The shape coercion the old _read_state() did is unchanged.
check("a file of the wrong shape reads back as the default",
      cr._shaped({"aliases": "not a dict", "checks": None})["aliases"], {})
check("and keys it does not know are kept",
      cr._shaped({"extra": 1})["extra"], 1)


# ---------------------------------------------------------------------------
print("\nThe check image is read and not kept")

# A scanned check carries an account number, a routing number and a signature.
# Nothing in this module ever read the stored file back -- no route serves one
# and the OCR works on the uploaded bytes in memory -- so the file was
# write-only: all of the exposure and none of the use.
#
# Driven through the real route rather than read off the source, because the
# claim is about what reaches the disk and prose naming a write is not a write.
cr.app.config["TESTING"] = True
_client = cr.app.test_client()
_seen = {}
_real_extract = cr._extract_check


def _fake_extract(raw, mime):
    _seen["bytes"] = len(raw)
    return {"payer": "Acme", "amount": "10.00", "date": "2026-09-16",
            "check_number": "1234", "confidence": 0.9}


cr._extract_check = _fake_extract
cr._owner_gate_saved = cr._owner_gate
cr._owner_gate = lambda: None          # the gate is asserted elsewhere
try:
    _before = set(p.name for p in cr._upload_dir().glob("*")) if cr._upload_dir().exists() else set()
    _r = _client.post("/api/upload", data={"file": (io.BytesIO(b"fake-check-bytes"), "check.png")},
                      content_type="multipart/form-data")
    check("the upload is accepted", _r.status_code, 200)
    check("...and the OCR still saw the bytes", _seen.get("bytes"), len(b"fake-check-bytes"))
    check("...and records no filename", _r.get_json()["check"]["file"], "")
    _after = set(p.name for p in cr._upload_dir().glob("*")) if cr._upload_dir().exists() else set()
    check("...and wrote nothing to the uploads directory", _after, _before)
finally:
    cr._extract_check = _real_extract
    cr._owner_gate = cr._owner_gate_saved

# The resolver stays, because a check recorded before this change carries a
# filename and deleting that check should still take its file with it.
check("the uploads path is still resolvable for old rows",
      cr._upload_dir().name, "uploads")


# ---------------------------------------------------------------------------
print("\nSweeping the images the old upload path left behind")

_up = cr._upload_dir()
_up.mkdir(parents=True, exist_ok=True)
(_up / "chk_aaa.png").write_bytes(b"scan-one")
(_up / "chk_bbb.jpg").write_bytes(b"scan-two-longer")
(_up / "nested").mkdir(exist_ok=True)            # a directory must survive
(_up / "nested" / "keep.txt").write_bytes(b"not mine to delete")
_res = cr.sweep_legacy_uploads()
check("it removes the images", _res["removed"], 2)
check("...and counts the bytes it freed", _res["bytes"], len(b"scan-one") + len(b"scan-two-longer"))
check("...and the files are actually gone",
      sorted(x.name for x in _up.iterdir()) if _up.exists() else [], ["nested"])
check("...and it does not recurse into a directory",
      (_up / "nested" / "keep.txt").exists(), True)
check("...and nothing failed", _res["failed"], 0)

# Second run is a no-op rather than an error: it runs on every boot, so being
# idempotent is the property that makes having no marker safe.
_again = cr.sweep_legacy_uploads()
check("a second sweep removes nothing", _again["removed"], 0)

# An empty directory is taken away with the files; a missing one is not an
# error, because that is the steady state on every boot after the first.
import shutil as _sh
_sh.rmtree(_up, ignore_errors=True)
_gone = cr.sweep_legacy_uploads()
check("no directory is not a failure", (_gone["removed"], _gone["failed"], _gone["swept"]),
      (0, 0, False))

# It may never raise: a cleanup that stops a worker coming up is worse than the
# files it was cleaning.
_real_dir = cr._upload_dir
cr._upload_dir = lambda: (_ for _ in ()).throw(RuntimeError("disk gone"))
try:
    check("a failure inside it is reported, not raised",
          cr.sweep_legacy_uploads()["removed"], 0)
finally:
    cr._upload_dir = _real_dir

# ---------------------------------------------------------------------------
print("\nTwo workers, which is the only way a lost write shows")

# The check above proves the cross-worker lock is *taken*. This proves nothing
# is *lost*, which is a different claim and the one the store exists for: a
# lock can be entered and still not serialise -- the wrong scope, a lock per
# process, a read that happened before it was held. Counting the call cannot
# see any of that.
#
# It needs two real processes. Threads cannot show it, because `_LOCK`
# serialises them and every assertion passes while two containers quietly
# overwrite each other -- the measurement `hub/leads.py` records as 30 of 60
# leads surviving. Each worker reads, waits inside the mutation, and writes;
# with the read and the write not held together the second silently lands on
# top of the first and one check simply goes.
WORKER = pathlib.Path(_TMP) / "worker.py"
WORKER.write_text(
    "import os, sys, time\n"
    "sys.path.insert(0, os.environ['CR_REPO'])\n"
    "from modules.check_reconciliation import app as cr\n"
    "tag = sys.argv[1]\n"
    "def fn(state):\n"
    "    state['checks'].append({'id': tag})\n"
    "    time.sleep(0.6)\n"
    "    state['oauth']['refresh_token'] = tag\n"
    "cr._mutate(fn)\n"
    "print('done', tag)\n", encoding="utf-8")

_CONC = tempfile.mkdtemp(prefix="s1-checkrec-conc-")
_env = dict(os.environ)
_env["CR_REPO"] = str(pathlib.Path(__file__).resolve().parent)
_env["HUB_DATA_DIR"] = _CONC
_env.pop("CHECK_RECONCILIATION_DATA_DIR", None)
_env["DATABASE_URL"] = (os.environ.get("CHECKREC_TEST_DATABASE_URL")
                        or "sqlite:///" + os.path.join(_CONC, "hub.sqlite3"))
# A fresh HUB_DATA_DIR is not a fresh store, and on Postgres that difference
# is the whole thing. `jsonstore.key_for()` keys the mirror on the path
# RELATIVE TO the data root precisely so a blob survives the root moving --
# so this directory and the one the checks above used produce the same key,
# `check-reconciliation/state.json`, and against a shared database the first
# read here restores the record the upload check created. The run is green on
# SQLite, where each phase gets its own file, and red only on the backend
# production actually uses.
#
# So the key is cleared rather than the directory swapped. `delete_json()`
# takes the file and the mirrored blob together, which is the point -- dropping
# only the file would leave the blob to be restored by the next read.
subprocess.run(
    [sys.executable, "-c",
     "import os, sys\n"
     "sys.path.insert(0, os.environ['CR_REPO'])\n"
     "from hub import jsonstore\n"
     "from modules.check_reconciliation import app as cr\n"
     "jsonstore.delete_json(str(cr._data_file()))\n"],
    env=_env, capture_output=True)
# Warm the store first, so the two children race the data rather than the
# schema the first one to arrive would create.
subprocess.run([sys.executable, str(WORKER), "warm"], env=_env, capture_output=True)
_procs = [subprocess.Popen([sys.executable, str(WORKER), tag], env=_env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
          for tag in ("A", "B")]
_results = [p.communicate() for p in _procs]
check("both workers report success",
      all(b"done" in (out or b"") for out, _ in _results))
_final = subprocess.run(
    [sys.executable, "-c",
     "import os, sys, json\n"
     "sys.path.insert(0, os.environ['CR_REPO'])\n"
     "from modules.check_reconciliation import app as cr\n"
     "print(json.dumps([c['id'] for c in cr._read_state()['checks']]))"],
    env=_env, capture_output=True, text=True)
check("and neither write was lost",
      sorted(json.loads(_final.stdout.strip() or "[]")), ["A", "B", "warm"])
shutil.rmtree(_CONC, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
