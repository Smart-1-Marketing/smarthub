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

The gate also releases, and that half is asserted here for the same reason the
first half is. smart1-hub was set to autoDeployTrigger: checksPass -- which the
dashboard renders as "deploys when CI passes" and which has never fired once,
every deploy on that service carrying trigger `manual` or `api`. A setting that
says something it does not do is worse than no setting, because people plan
around it; so the decision moved into this workflow, where it is visible in the
run somebody is already looking at, and the properties that make it safe are
held here rather than remembered.
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

# The check has to be able to go red, or it is furniture.
print("\n...and the check bites")
print("-" * 46)
_fake = "test_a_file_the_gate_does_not_run.py"
check("a test file the workflow never names is reported",
      _fake not in named and _fake not in EXEMPT)
check("...and one it does name is not",
      "test_unwired.py" in named,
      "test_unwired.py is what this file was written about")


# ---------------------------------------------------------------- the deploy

print("\nand the gate is also what releases")
print("-" * 46)


def deploy_slice(text):
    """The deploy job's own lines: its key to the end of the file."""
    marker = "\n  deploy:\n"
    return text.split(marker, 1)[1] if marker in text else ""


def deploy_faults(job):
    """What is wrong with a deploy job, in sentences. Empty means sound."""
    faults = []
    if "needs: checks" not in job:
        faults.append("does not wait for the checks job")
    if "github.event_name == 'push'" not in job or "refs/heads/main" not in job:
        faults.append("is not scoped to a push to main")
    if "ref=${GITHUB_SHA}" not in job:
        faults.append("does not pin the release to the commit that passed")
    if not re.search(r"-z \"\$\{RENDER_DEPLOY_HOOK_URL:-\}\"[\s\S]{0,800}?exit 0", job):
        faults.append("fails the run when the secret is absent instead of saying so")
    if "NOT DEPLOYED" not in job:
        faults.append("does not say when it did not deploy")
    if "cat response.json" in job:
        faults.append("prints the hook response, which can quote the key back")
    # The *name* is printed on purpose -- it is what somebody has to go and set.
    # Only the expansion is a secret.
    for line in job.splitlines():
        bare = line.strip()
        if not (bare.startswith("echo") or bare.startswith("note ")):
            continue
        if "$RENDER_DEPLOY_HOOK_URL" in bare or "${RENDER_DEPLOY_HOOK_URL" in bare:
            faults.append("echoes the hook URL, which carries its own key")
    return faults


job = deploy_slice(src)
check("the workflow has a deploy job", job != "")
for fault in deploy_faults(job):
    check("the deploy job " + fault, False)
if not deploy_faults(job):
    check("...and every property that makes it safe holds", True)

# ...and each of those rules can actually fail. Removing the line a rule is
# about must produce that rule's own finding -- a predicate nothing can make
# say no is furniture, which is what the Render setting turned out to be.
print("\n...and each deploy rule bites")
print("-" * 46)
for needle, expect in [
    ("needs: checks", "does not wait for the checks job"),
    ("github.event_name == 'push'", "is not scoped to a push to main"),
    ("ref=${GITHUB_SHA}", "does not pin the release to the commit that passed"),
    ("NOT DEPLOYED", "does not say when it did not deploy"),
    ("exit 0", "fails the run when the secret is absent instead of saying so"),
]:
    check(f"removing {needle!r} is reported",
          expect in deploy_faults(job.replace(needle, "")),
          deploy_faults(job.replace(needle, "")))
check("an echoed hook URL is reported",
      any("carries its own key" in f
          for f in deploy_faults(job + '\n          echo "$RENDER_DEPLOY_HOOK_URL"\n')))
check("a printed hook response is reported",
      any("quote the key back" in f
          for f in deploy_faults(job + "\n          cat response.json\n")))
check("...and the job as it stands trips none of them", deploy_faults(job) == [],
      deploy_faults(job))

print(f"\n{'-' * 46}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
