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
import json
import os
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
# wrote. Owning both takes it out of that shape entirely.
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

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
