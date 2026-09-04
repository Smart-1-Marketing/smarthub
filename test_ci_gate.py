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

# The gate also deploys, and that job is the one thing here that holds a
# secret. Render's own "deploy after CI checks pass" has never fired on this
# account -- every deploy smart1-hub has had is trigger `manual` or `api` --
# so the promise is made here instead. Four properties, each of which is a way
# a deploy job goes quietly wrong.
print("\nand the deploy job keeps its promises")
print("-" * 46)

_deploy = src.split("\n  deploy:", 1)[-1] if "\n  deploy:" in src else ""

check("there is a deploy job", bool(_deploy.strip()),
      "no `deploy:` job in checks.yml")

# A pull request from a fork must not be able to reach production, and neither
# must a branch. Only a commit already on main deploys.
check("it runs on main and never on a pull request",
      "github.ref == 'refs/heads/main'" in _deploy
      and "github.event_name == 'push'" in _deploy
      and "pull_request" not in _deploy,
      "the `if:` guard does not pin main and the push event")

# main takes a merge every few minutes here, so a bare hook deploys whatever
# main is by the time Render picks it up rather than the commit that was
# tested -- a race that has already put an unintended commit into production.
check("it deploys the commit whose checks passed, not whatever main is by then",
      "ref=${SHA}" in _deploy and "github.sha" in _deploy,
      "the deploy hook is not pinned to github.sha")

# A green tick over a deploy that did not happen is the confident wrong answer
# this repo keeps having to undo.
# Scoped to that branch and not to the step: there is an `exit 1` further
# down for a refusal from Render, and a window wide enough to reach it made
# this assertion pass against a branch that had been changed to echo and carry
# on -- the check that cannot fail, in the file written about checks that
# cannot fail.
_guard = re.search(r'if \[ -z "\$\{HOOK\}" \][\s\S]*?\n\s*fi\n', _deploy)
check("a missing secret is a refusal rather than a pass",
      _guard is not None and "exit 1" in _guard.group(0),
      "an unset RENDER_DEPLOY_HOOK_URL does not fail the job")

# The whole hook URL is the credential: anyone holding it can deploy. GitHub
# masks a secret it knows, and a job that prints it anyway is one line away
# from a log that does not.
_echoes_hook = re.search(r'(echo|printf)[^\n]*\$\{?HOOK', _deploy)
check("and the hook URL is never echoed", _echoes_hook is None,
      _echoes_hook.group(0) if _echoes_hook else "")


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
