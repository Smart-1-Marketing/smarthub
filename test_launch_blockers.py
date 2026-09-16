"""hub/launch_blockers.py -- launch-readiness blockers from the client brief.

    python3 test_launch_blockers.py
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="s1launchblockers_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}  {detail}")


def section(title):
    print(f"\n== {title} ==")


from hub import launch_blockers


def _fact(v, source="scan"):
    return {"value": v, "source": source}


# ---------------------------------------------------------------------------
section("Everything green -> no blockers")
GREEN = {"digital": {
    "analytics_tool": _fact("GA4"),
    "uses_universal_ga": _fact(False),
    "has_google_tag": _fact(True),
    "uses_consent_mode_v2": _fact(True),
    "has_facebook_pixel": _fact(True),
    "has_google_pixel": _fact(True),
}}
check("no blockers on a clean brief", launch_blockers.find(GREEN) == [])


# ---------------------------------------------------------------------------
section("Several real blockers, each named with its reason")
SEVERAL = {"digital": {
    "analytics_tool": _fact("GA4"),
    "uses_universal_ga": _fact(True),
    "has_google_tag": _fact(False),
    "uses_consent_mode_v2": _fact(False),
    "has_facebook_pixel": _fact(True),
    "has_google_pixel": _fact(False),
}}
blockers = launch_blockers.find(SEVERAL)
codes = {b["code"] for b in blockers}
check("universal_ga, no_google_tag, no_consent_mode and no_google_pixel all found",
      codes == {"universal_ga", "no_google_tag", "no_consent_mode", "no_google_pixel"}, codes)
check("the pixel that is present (facebook) is not flagged",
      "no_facebook_pixel" not in codes, codes)
check("every blocker carries a label and a why",
      all(b.get("label") and b.get("why") for b in blockers))


# ---------------------------------------------------------------------------
section("A field the scan never measured is never a blocker")
UNMEASURED = {"digital": {
    "analytics_tool": _fact("GA4"),
    # has_google_tag, uses_consent_mode_v2, the pixels: none present at all.
}}
check("only what was measured and failed counts",
      launch_blockers.find(UNMEASURED) == [])


# ---------------------------------------------------------------------------
section("No analytics tool at all is its own blocker")
NO_ANALYTICS = {"digital": {"analytics_tool": _fact("")}}
codes2 = {b["code"] for b in launch_blockers.find(NO_ANALYTICS)}
check("no_analytics found on an empty analytics_tool", "no_analytics" in codes2, codes2)


# ---------------------------------------------------------------------------
section("A whole-section not-measured digital block finds nothing")
check("digital.measured is False -> []",
      launch_blockers.find({"digital": {"measured": False}}) == [])
check("no brief at all -> []", launch_blockers.find({}) == [])
check("not a dict -> []", launch_blockers.find(None) == [])


# ---------------------------------------------------------------------------
section("ticket_body() lists every blocker in the order found")
body = launch_blockers.ticket_body("Acme Plumbing", blockers)
check("client named in the body", "Acme Plumbing" in body)
check("every blocker's label appears", all(b["label"] in body for b in blockers))
check("empty blockers -> empty body", launch_blockers.ticket_body("Acme", []) == "")


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
