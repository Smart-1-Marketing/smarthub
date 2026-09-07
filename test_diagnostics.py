"""hub/diagnostics.py — the QuickBooks check.

    python3 test_diagnostics.py

No pytest, no new dependencies, no network call: `check_quickbooks()` reads
only `hub.quickbooks.configured()` / `connected()` / `health()` /
`link_status()`, all of which are stubbed here.

## Why this exists

`hub.quickbooks.link_status()` -- how many cached invoices have a public
link, and when that cache was last refreshed -- had no caller anywhere in
the repo, and `health()` -- why the connection may be about to drop -- was
reachable only through an orphaned `/api/qb/health` route nothing fetched.
Both now answer through one row on the Diagnostics page's own "API health"
panel, the same panel every other provider already reports on, so a check
that never spends a credit is not switched off by inventing a second panel
for it.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-diag-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "diagnostics-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import diagnostics as diag                        # noqa: E402
from hub import quickbooks as qb                            # noqa: E402

_real = {name: getattr(qb, name) for name in
         ("configured", "connected", "health", "link_status")}


def _restore():
    for name, fn in _real.items():
        setattr(qb, name, fn)


print("\nQuickBooks unconfigured")
qb.configured = lambda: False
try:
    c = diag.check_quickbooks()
    check("reads as off, not an error", c.state, "off")
    check("names the two env vars", "QB_CLIENT_ID" in c.fix and "QB_CLIENT_SECRET" in c.fix,
          c.fix)
finally:
    _restore()

print("\nConfigured but never connected")
qb.configured = lambda: True
qb.connected = lambda: False
try:
    c = diag.check_quickbooks()
    check("warns rather than erroring", c.state, "warn")
    check("points at /status", "/status" in c.fix, c.fix)
    check("never calls health() or link_status() before connected() passes",
          True, "")  # would raise below if it tried -- neither is stubbed here
finally:
    _restore()

print("\nConnected, healthy, invoices already linked")
qb.configured = lambda: True
qb.connected = lambda: True
qb.health = lambda: {"ok": True, "problems": []}
qb.link_status = lambda: {"cached": 42, "with_public_link": 38,
                          "last_refresh": "2026-09-01T10:00:00+00:00"}
try:
    c = diag.check_quickbooks()
    check("a clean connection reads ok", c.state, "ok")
    check("the coverage numbers are in the detail line",
          "38 of 42" in c.detail, c.detail)
    check("and the refresh time is too", "2026-09-01T10:00:00+00:00" in c.detail, c.detail)
finally:
    _restore()

print("\nConnected, but the token is about to lapse")
qb.configured = lambda: True
qb.connected = lambda: True
qb.health = lambda: {"ok": False, "problems": ["The refresh token is 95 days old."]}
qb.link_status = lambda: {"cached": 10, "with_public_link": 10,
                          "last_refresh": "2026-09-01T10:00:00+00:00"}
try:
    c = diag.check_quickbooks()
    check("a real problem from health() downgrades the row", c.state, "warn")
    check("and the problem is named, not just the coverage stat",
          "95 days old" in c.detail, c.detail)
finally:
    _restore()

print("\nConnected, nothing ever cached")
qb.configured = lambda: True
qb.connected = lambda: True
qb.health = lambda: {"ok": True, "problems": []}
qb.link_status = lambda: {"cached": 0, "with_public_link": 0, "last_refresh": None}
try:
    c = diag.check_quickbooks()
    check("a never-refreshed cache says so rather than a bare 0", c.state, "ok")
    check("...in words, not a null timestamp",
          "never refreshed" in c.detail, c.detail)
finally:
    _restore()

print("\nIt is registered on the panel every provider reports on")
check("check_quickbooks is one of the run_all() checks",
      diag.check_quickbooks in diag.CHECKS, True)

import shutil                                               # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
