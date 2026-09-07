"""The single gate runs every check a person runs.

    python3 test_ci_gate.py

CLAUDE.md ends its list of checks with a claim:

    **All of this runs on every pull request** -- `.github/workflows/checks.yml`,
    the single gate. CI runs the same scripts a person runs, so a green run
    means the same thing in both places and no check exists only where nobody
    can reproduce it.

It was not true. Seven of the test files that list names were run by nobody but
a person who thought to type them, and the file making the claim is the same
file listing them -- so a reader had every reason to believe they were gated.
What they hold is not marginal: that nothing is declared and left unwired, that
one tool is tiled once, that four copies of the wait mark agree, that the three
AI proposal paths carry no route to a write.

That is the failure this repo names in a dozen other places, wearing a
workflow: a sweep that has quietly stopped sweeping, reporting a clean bill of
health about the part it still covers. So the claim is asserted rather than
made.

Two directions, because either alone goes stale:

  * a `test_*.py` in the repo that the workflow never invokes; and
  * a step in the workflow naming a file that is not here, which is a step that
    silently does nothing -- several are written `if [ -f x ]; then ... else
    echo "not on this branch"`, which is deliberate and is why the second half
    reads the guard rather than the filename.

`EXEMPT` is the way out, and it carries the reason -- the discipline
`check_stale_json_exemptions()` works to. It is empty, which is the only way
this was worth adding.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent
WORKFLOW = ROOT / ".github" / "workflows" / "checks.yml"

# Test files the gate deliberately does not run, with the reason. Empty on
# purpose: a file here is a check somebody has decided not to gate, and that
# decision should be readable rather than inferred from a workflow diff.
EXEMPT: dict[str, str] = {}

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))


print("\nthe gate runs every check a person runs")
print("-" * 46)

src = WORKFLOW.read_text(encoding="utf-8")
here = {p.name for p in ROOT.glob("test_*.py")}
# This file is the gate's own check; it is added to the workflow with the rest.
named = set(re.findall(r"(test_[a-z0-9_]+\.py)", src))

check("the workflow is where it is expected to be", WORKFLOW.exists(), str(WORKFLOW))
check("and there are test files to gate", len(here) > 50, len(here))

ungated = sorted(f for f in here if f not in named and f not in EXEMPT)
check("every test file in the repo is run by the gate", ungated == [],
      f"{len(ungated)} not invoked anywhere in checks.yml: " + ", ".join(ungated[:10]))

# ...and the other direction. A step naming a file that is not here runs
# nothing; where that is on purpose the step guards on `[ -f ... ]` and says
# so, so only an *unguarded* name is a finding.
phantom = []
for f in sorted(named - here):
    if f in EXEMPT:
        continue
    if re.search(r"if \[ -f " + re.escape(f) + r" \]", src):
        continue        # guarded on purpose, for a branch that predates it
    phantom.append(f)
check("and no step names a file that is not here", phantom == [],
      ", ".join(phantom[:10]))

check("every exemption says why", all(str(v).strip() for v in EXEMPT.values()),
      [k for k, v in EXEMPT.items() if not str(v).strip()])
stale = sorted(f for f in EXEMPT if f not in here)
check("and no exemption outlives the file it exempted", stale == [], stale)

# ---------------------------------------------------------------- no secrets
#
# The gate used to carry a `deploy` job -- the one thing in this workflow that
# held a secret. It was built as the route around a Render webhook that had
# never fired, and it never fired either: RENDER_DEPLOY_HOOK_URL was never set
# as a repository secret, so every run of it refused at the first step, by
# design. Meanwhile the webhook was reconnected under the org and Render's own
# auto-deploy (trigger: commit) has been shipping every push to main on its
# own. So the job was a second deploy path that had never once run, and its
# refusal was the sole reason main read red on every merge -- a permanently
# red gate, which is the check people learn to skip past.
#
# It is gone, and what is asserted in its place is the property that made it
# an exception in the first place: this workflow reaches no third party and
# holds no credential. Everything it runs, a contributor runs on a fresh
# checkout. A job that needs a secret can be added back deliberately -- and
# this check is what makes that a decision somebody makes rather than a drift
# nobody notices, because adding one turns this red.
print("\nand the gate holds no credential")
print("-" * 46)

_secrets = sorted(set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", src)))
# GITHUB_TOKEN is issued to every workflow run by GitHub itself rather than
# stored by anybody, so it is not a credential this repo holds.
_held = [n for n in _secrets if n != "GITHUB_TOKEN"]
check("the workflow reads no stored secret", _held == [],
      "reads " + ", ".join(_held))
check("and there is no second job holding one",
      "\n  deploy:" not in src and "RENDER_DEPLOY_HOOK_URL" not in src,
      "the deploy job is back without this check being reconsidered")

# The check has to be able to go red, or it is furniture.
print("\n...and the check bites")
print("-" * 46)
_fake = "test_a_file_the_gate_does_not_run.py"
check("a test file the workflow never names is reported",
      _fake not in named and _fake not in EXEMPT)
check("...and one it does name is not",
      "test_unwired.py" in named,
      "test_unwired.py is what this file was written about")

print(f"\n{'-' * 46}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
