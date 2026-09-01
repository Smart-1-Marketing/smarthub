"""Two names for one property, and the verdict that forced them apart.

    python3 test_analytics_ids.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one.

## Why this file exists

`hub/analytics_ids.py` compares the GA and GTM identifiers recorded in Knack
against the ones a connected Google account can actually reach, and its own
docstring says why that has to be done carefully: comparing raw strings
"reports false mismatches, which is worse than no check at all because it
trains people to ignore the warning."

It then reported a false mismatch on every GA row it could see.

GA has **two identifiers for one property** and they are not interchangeable:

  * the **measurement id** `G-XXXXXXX` — on the site, in the GTM tag, on every
    report, and therefore what a person types into Knack;
  * the **property id**, a bare number — which is all Google returns, because
    a GA4 property summary carries no measurement id at all.
    `modules/google_finder/app.py` splits it out of `properties/<id>`, and the
    comment beside it says the summary carries no URL either.

`_state()` normalised both, found `G-ABC123XYZ != 284729103`, and answered
**mismatch**. Measured against this deployment's sanitized website
registry — 610 records — every one of the 166 recorded GA ids is a `G-`
measurement id (159) or a legacy `UA-` id (7), and **not one is a property
id**. So for GA the verdict could only ever be `mismatch` or `recorded_only`:
`match` was unreachable.

What that produced, on two screens:

  * **Client 360** drew a red pill reading *mismatch* with the advice "the
    site is running a container we don't administer, or the Knack record is
    out of date … reports built on the wrong property are silently wrong" —
    about a property we administer perfectly well, correctly recorded.
  * **`audit_all()`** collected every one of them into a list whose stated
    premise is that each entry means somebody's reporting may be pointed at
    the wrong place, while `in_agreement` counted only `match` and so could
    never count a GA row at all. Overstating the problems and understating
    the agreement at the same time.

`not_comparable` is the answer, because that is what is true: nothing here can
tell whether the two names refer to the same property, and judging it either
way invents an answer. The same shape as every other tri-state in this
codebase — *not measured* is an answer.

**GTM had the same hole, quieter.** google_finder stores `public_id or
container_id`, so where the API returns no publicId the value lands in the
numeric space and produces the identical false mismatch. It is rarer, which
is a reason to expect it rather than a reason to leave it, so the rule is
applied per platform rather than special-cased for GA.

**And what must keep saying mismatch** is asserted just as hard, because a
fix that silences the real findings with the false one is worse than the bug:
two different measurement ids, two different property ids, two different
containers, and a legacy `UA-` id against a live GA4 property — Universal
Analytics stopped processing in 2023, so that record is genuinely stale.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1anaids_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "analytics-ids-test-secret"

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import analytics_ids as ai                          # noqa: E402


def ga(recorded, live):
    return ai._state(recorded, live, ai._norm_ga, "ga")


def gtm(recorded, live):
    return ai._state(recorded, live, ai._norm_gtm, "gtm")


# =====================================================================
section("The two GA identifier spaces are not a disagreement")
# =====================================================================

check("a measurement id against a property id cannot be compared",
      ga("G-ABC123XYZ", "284729103"), "not_comparable")
check("and it reads the same the other way round",
      ga("284729103", "G-ABC123XYZ"), "not_comparable")
check("a fully-qualified property resource still resolves and matches",
      ga("properties/284729103", "284729103"), "match")
check("the same measurement id on both sides is a match",
      ga("G-ABC123XYZ", "G-ABC123XYZ"), "match")
check("and the same property id is too", ga("284729103", "284729103"), "match")

# The advice has to explain the state rather than restate the verdict: this is
# the line a rep reads on the client record instead of a red flag.
advice = ai._ADVICE["not_comparable"]
check("the advice names both identifiers",
      "measurement id" in advice and "property id" in advice, True)
check("and says why Google cannot supply the other one",
      "carries no measurement id" in advice, True)
check("and says what would settle it rather than leaving it hanging",
      "settle it" in advice, True)
check("and does not tell anybody to investigate",
      "investigate" in advice.lower() or "worth resolving" in advice.lower(),
      False)


# =====================================================================
section("What must keep saying mismatch")
# =====================================================================
# A fix that silences the real findings along with the false one is worse
# than the bug it replaces.

check("two different measurement ids", ga("G-AAA111", "G-BBB222"), "mismatch")
check("two different property ids", ga("284729103", "999888777"), "mismatch")
check("a legacy UA id against a live GA4 property",
      ga("UA-12345-1", "284729103"), "mismatch")
check("two different UA ids", ga("UA-1-1", "UA-2-1"), "mismatch")
check("two different containers", gtm("GTM-AAAA", "GTM-BBBB"), "mismatch")
check("two different numeric containers", gtm("6112233", "9988776"), "mismatch")


# =====================================================================
section("GTM has the same two spaces, and the same answer")
# =====================================================================
# google_finder stores `public_id or container_id`, so the fallback lands in
# the numeric space. Rarer than the GA case, not different from it.

check("a public container id against the numeric fallback",
      gtm("GTM-TG6FPR8M", "6112233"), "not_comparable")
check("the same public id is still a match",
      gtm("GTM-TG6FPR8M", "GTM-TG6FPR8M"), "match")
check("the rule is per platform rather than special-cased for GA",
      sorted(ai._SAME_THING), ["ga", "gtm"])
check("and a platform nobody declared falls through to the old verdict",
      ai._state("G-AAA", "284729103", ai._norm_ga, ""), "mismatch")


# =====================================================================
section("The other four states are unchanged")
# =====================================================================

check("recorded, nothing reachable", ga("G-ABC123", ""), "recorded_only")
check("reachable, nothing recorded", ga("", "284729103"), "live_only")
check("neither", ga("", ""), "missing")
check("blank strings are not an id", ga("   ", "  "), "missing")
for state in ("match", "mismatch", "recorded_only", "live_only", "missing",
              "not_comparable"):
    check(f"{state} has advice written for it", bool(ai._ADVICE.get(state)), True)


# =====================================================================
section("Measured against the registry this deployment actually has")
# =====================================================================
# The claim is not "this could happen" but "this happened on every GA row we
# hold", so it is asserted against the sanitized fixture used by CI.

export = ROOT / "tests" / "fixtures" / "clients" / "websites.json"
if not export.exists():
    print("  skip  no websites export in this checkout")
else:
    rows = json.loads(export.read_text()).get("records") or []
    check("the fixture has registry rows in it", len(rows) > 0, True)
    shapes = {"measurement": 0, "ua": 0, "property": 0, "other": 0}
    for r in rows:
        v = str(r.get("ga") or "").strip()
        if v:
            shapes[ai._ga_space(v)] = shapes.get(ai._ga_space(v), 0) + 1
    recorded = sum(shapes.values())
    check("GA ids are represented in the fixture", recorded > 0, True)
    # The load-bearing claim: not one recorded GA id is in the space Google
    # returns, so the comparison could never have found a match.
    check("and none of them is a property id", shapes.get("property", 0), 0)
    # The remainder is a data-quality tail rather than an identifier space --
    # this export has one `ga` field holding an email address, which is a bad
    # record and rightly still reads as a mismatch. Named rather than asserted
    # away, so the count moving is a change somebody looks at.
    check("the rest are measurement ids, legacy UA ids, or not an id at all",
          shapes.get("measurement", 0) + shapes.get("ua", 0)
          + shapes.get("other", 0), recorded)

    # The bug, stated as arithmetic: against a live property id, the old rule
    # answered mismatch for every recorded GA id in the book.
    live = "284729103"
    old = sum(1 for r in rows
              if str(r.get("ga") or "").strip()
              and ai._state(str(r["ga"]).strip(), live, ai._norm_ga) == "mismatch")
    new = sum(1 for r in rows
              if str(r.get("ga") or "").strip()
              and ai._state(str(r["ga"]).strip(), live, ai._norm_ga, "ga")
              == "mismatch")
    check("the old rule called every recorded GA id a mismatch", old, recorded)
    check("the new one keeps only what is genuinely wrong: the stale UA "
          "records and the field holding something that is not a GA id",
          new, shapes.get("ua", 0) + shapes.get("other", 0))
    check("and that is fewer than it was", new < old, True)


# =====================================================================
section("The audit counts them apart, and the record draws them apart")
# =====================================================================

# Driven through the real mapping rather than asserted about the source: a
# first draft checked that the string "not_comparable.append" appeared in
# audit_all, which stayed true when the branch that reaches it was disabled.
check("a not-comparable row is counted in its own bucket",
      ai.bucket_for("not_comparable"), "not_comparable")
check("and never as a mismatch",
      ai.bucket_for("not_comparable") == ai.bucket_for("mismatch"), False)
check("a genuine mismatch is still a finding",
      ai.bucket_for("mismatch"), "mismatched")
check("no access is still its own column",
      ai.bucket_for("recorded_only"), "no_access")
check("only match counts as agreement",
      [s for s in ("match", "mismatch", "not_comparable", "recorded_only",
                   "live_only", "missing")
       if ai.bucket_for(s) == "in_agreement"], ["match"])
check("and a state nobody mapped is dropped rather than guessed at",
      ai.bucket_for("invented_later"), "")

# Every state _state() can return must have a bucket decided for it, or a
# state added later is silently counted as neither.
_states = {ai._state(a, b, ai._norm_ga, "ga")
           for a, b in (("G-A", "G-A"), ("G-A", "G-B"), ("G-A", "284729103"),
                        ("G-A", ""), ("", "284729103"), ("", ""))}
check("every state the comparison produces is in the bucket table",
      sorted(s for s in _states if s not in ai._BUCKETS), [])

# needs_attention decides whether the card reads as having something to do,
# so a state that means "nothing to act on" must not raise it.
import inspect                                               # noqa: E402
_problems_line = inspect.getsource(ai.compare).split("problems =")[1].split("\n")[0]
check("a not-comparable row does not make the card read as needing attention",
      "not_comparable" in _problems_line, False)
check("while a real mismatch still does", "mismatch" in _problems_line, True)

tpl = (ROOT / "hub" / "templates" / "client360.html").read_text()
check("the client record has a pill style for the state",
      "not_comparable:" in tpl, True)
check("and it is not the red one mismatch uses",
      re.search(r"not_comparable:'#feeceb", tpl) is None, True)


# =====================================================================
section("Nothing here reaches Google, and nothing raises")
# =====================================================================
# compare() is called from a client record, so a source that will not answer
# must cost the card and never the page.

for bad in (None, "", 0, [], {}, "  "):
    try:
        ai._norm_ga(bad)
        ai._norm_gtm(bad)
        ai._ga_space(bad)
        ai._gtm_space(bad)
        ok = True
    except Exception as exc:                                 # noqa: BLE001
        ok = f"raised {type(exc).__name__}"
    check(f"the readers survive {bad!r}", ok, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
