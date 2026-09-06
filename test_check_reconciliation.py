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
import sys

from modules.check_reconciliation import app as cr

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

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
