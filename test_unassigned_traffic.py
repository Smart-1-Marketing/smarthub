"""Offline tests for the Unassigned Traffic Resolver.

No Google calls. These assert the diagnostic rules and math separately from
OAuth/Data API plumbing so a wording/UI change cannot hide attribution drift.
Run: python3 test_unassigned_traffic.py
"""
from modules.unassigned_traffic.app import diagnose_row, summarize

passed = failed = 0

def check(label, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("  ok   ", label)
    else:
        failed += 1
        print("  FAIL ", label, "got", repr(got), "want", repr(want))

check("missing attribution",
      diagnose_row({"source": "(not set)", "medium": "(not set)", "sessions": 8})["issue"],
      "missing_attribution")
check("missing medium",
      diagnose_row({"source": "newsletter", "medium": "(not set)", "sessions": 8})["issue"],
      "missing_medium")
check("self referral",
      diagnose_row({"source": "shop.example.com", "medium": "referral", "sessions": 3},
                   "example.com")["issue"],
      "self_referral")
check("payment referral",
      diagnose_row({"source": "checkout.stripe.com", "medium": "referral", "sessions": 3})["issue"],
      "payment_referral")
check("nonstandard medium",
      diagnose_row({"source": "facebook", "medium": "paid-facebook", "sessions": 9})["issue"],
      "nonstandard_medium")
check("paid missing campaign",
      diagnose_row({"source": "google", "medium": "cpc", "campaign": "", "sessions": 11})["issue"],
      "paid_missing_campaign")

out = summarize([
    {"source": "x", "medium": "weird", "sessions": "20"},
    {"source": "", "medium": "", "sessions": "5"},
], 100)
check("unassigned total", out["unassigned_sessions"], 25)
check("unassigned rate", out["unassigned_rate"], 25.0)
check("rows largest first", out["rows"][0]["sessions"], 20)
check("historical warning", "not rewritten" in out["note"], True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
