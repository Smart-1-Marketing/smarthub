"""Offline tests for the Unassigned Traffic Resolver.

No Google calls. These assert the diagnostic rules, exact-vs-capped math, and
that the resolver is actually reachable through SmartHub's mounted UTM tool.
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

rows = [
    {"source": "x", "medium": "weird", "sessions": "20"},
    {"source": "", "medium": "", "sessions": "5"},
]
out = summarize(rows, 100)
check("detail sum can be the total", out["unassigned_sessions"], 25)
check("unassigned rate", out["unassigned_rate"], 25.0)
check("rows largest first", out["rows"][0]["sessions"], 20)
check("full coverage", out["diagnostic_coverage_pct"], 100.0)
check("full coverage is not limited", out["detail_limited"], False)

# The Data API totals query can say 40 sessions while the capped detail table
# only contains the largest 25. The headline must stay 40, not silently become
# the sum of the visible rows.
capped = summarize(rows, 100, unassigned_sessions=40)
check("GA4 exact total wins over detail sum", capped["unassigned_sessions"], 40)
check("rate uses exact total", capped["unassigned_rate"], 40.0)
check("diagnosed rows remain honest", capped["diagnosed_sessions"], 25)
check("coverage names the gap", capped["diagnostic_coverage_pct"], 62.5)
check("capped detail is labelled", capped["detail_limited"], True)
check("historical warning", "not rewritten" in capped["note"], True)

# The central WSGI file already mounts /tools/utm. The package wires this
# related attribution diagnostic under that mount, so it is reachable without
# adding a second application mount.
import modules.utm_builder as utm_package  # noqa: E402
rules = {r.rule for r in utm_package._utm_app.app.url_map.iter_rules()}
check("resolver page is wired", "/unassigned-traffic/" in rules, True)
check("resolver client API is wired", "/unassigned-traffic/api/clients" in rules, True)
check("resolver analysis API is wired", "/unassigned-traffic/api/analyze" in rules, True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
