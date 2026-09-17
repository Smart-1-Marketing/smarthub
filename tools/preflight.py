#!/usr/bin/env python3
"""Everything a person should run before pushing, as one command.

    python3 tools/preflight.py              # the sweep
    python3 tools/preflight.py --merge      # the sweep plus the merge-only checks
    python3 tools/preflight.py --list       # what it would run, and nothing else

CLAUDE.md and ``docs/claude/56-verifying-a-change.md`` already name these
checks. What neither could do is make "I ran the checks" mean the same thing
twice, and the gap is not hypothetical -- every one of the three checks in
the MERGE group below is here because it caught something that a hand-assembled
sweep had just missed:

* **The MCP gateway's tests are not root-level test_*.py scripts.** They are
  unittest modules with their own workflow and their own SDK, so a sweep of
  ``test_*.py`` runs right past them. A branch adding two V2 tools passed its
  author's whole sweep and then failed ``mcp_gateway/test_v2.py``, whose tool
  set is deliberately CLOSED.

* **`git add -A` after a merge stages conflict markers as content** and marks
  the file resolved. A grep for markers scoped to the directories somebody
  expected them in is a grep with a hole in it: markers went into
  ``.github/workflows/checks.yml`` that way, and the whole ``checks`` workflow
  then failed instantly on unparseable YAML -- while ``smoke`` and CodeQL went
  green, which reads exactly like a passing build.

* **A broken line-continuation inside a workflow's `run:` block is valid
  YAML.** It parses, the job starts, and it silently runs a shorter list of
  files than it names. So the loops are executed with their commands stubbed,
  and what they reach is compared against what they list. (A file DELETED from
  the list is a different question -- ``test_ci_gate.py`` already answers it by
  failing when a test file has no step at all.)

Exit status is the number of failing checks, so ``&&`` works. Nothing here is
new logic -- it runs the same tools CI runs, in the same order, and prints
what each one did.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The static sweep, in the order docs/claude/56 lists it. A check that needs
# node or a browser is here too: CI has them, and a local run that skips them
# is a local run that cannot say the gate will pass.
SWEEP = [
    ("compile every module", [sys.executable, "-c", (
        "import ast,pathlib;[ast.parse(p.read_text(errors='ignore')) "
        "for p in pathlib.Path('.').rglob('*.py') if '_attic' not in p.parts]")]),
    ("jscheck", [sys.executable, "tools/jscheck.py"]),
    ("checktemplates", [sys.executable, "tools/checktemplates.py"]),
    ("linkcheck", [sys.executable, "tools/linkcheck.py"]),
    ("pagecheck", [sys.executable, "tools/pagecheck.py"]),
    ("menucheck", [sys.executable, "tools/menucheck.py"]),
    ("integritycheck", [sys.executable, "tools/integritycheck.py"]),
    ("spellcheck", [sys.executable, "tools/spellcheck.py"]),
    ("claude docs index", [sys.executable, "tools/claudedocs.py"]),
    ("ci gate", [sys.executable, "test_ci_gate.py"]),
]

# The gateway's own tests. Separate because they need the MCP SDK the Hub's
# runtime deliberately does not install -- `pip install -r
# mcp_gateway/requirements.txt` -- and because nothing else runs them.
GATEWAY = [
    ("mcp_gateway.test_server", [sys.executable, "-m", "unittest",
                                 "mcp_gateway.test_server"]),
    ("mcp_gateway.test_v2", [sys.executable, "-m", "unittest",
                             "mcp_gateway.test_v2"]),
    ("test_ask_smarthub", [sys.executable, "-m", "unittest", "test_ask_smarthub"]),
]

GATEWAY_ENV = {"MCP_API_TOKEN": "ci-test-token-not-for-production",
               "HUB_DATA_DIR": "/tmp/smarthub-mcp-ci"}

MARKER_RE = r"^(<<<<<<< |>>>>>>> |={7}$)"


def _run(label: str, argv: list[str], env: dict | None = None) -> tuple[bool, str]:
    started = time.monotonic()
    merged = {**os.environ, **(env or {})}
    try:
        done = subprocess.run(argv, cwd=ROOT, env=merged, capture_output=True,
                              text=True, timeout=1800)
    except FileNotFoundError:
        return False, "the command is not on this machine"
    except subprocess.TimeoutExpired:
        return False, "timed out after 30 minutes"
    took = time.monotonic() - started
    if done.returncode == 0:
        tail = (done.stdout or "").strip().splitlines()
        return True, f"{took:5.1f}s  " + (tail[-1][:88] if tail else "")
    body = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
    return False, f"{took:5.1f}s  exit {done.returncode}: " + (
        " / ".join(line.strip() for line in body[-3:])[:200] if body else "no output")


# ---------------------------------------------------------------------------
# The three merge-only checks. Each one is cheap and each one has caught a
# real push; see the module docstring for what.
# ---------------------------------------------------------------------------

def conflict_markers() -> tuple[bool, str]:
    """Markers anywhere in the INDEX -- never a guessed subset of it.

    `git ls-files` rather than a directory walk, so a vendored bundle that
    happens to contain the literal string is not a false alarm and a tracked
    file outside the directories somebody expected is not a miss.
    """
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                            capture_output=True, text=True)
    if listed.returncode != 0:
        return False, "git ls-files failed; is this a work tree?"
    hits = subprocess.run(["xargs", "-0", "grep", "-lE", MARKER_RE],
                          cwd=ROOT, input=listed.stdout,
                          capture_output=True, text=True)
    files = [f for f in hits.stdout.splitlines() if f and "node_modules" not in f]
    if files:
        return False, "conflict markers in: " + ", ".join(files[:5])
    return True, "none in any tracked file"


def workflows_parse() -> tuple[bool, str]:
    """Every workflow is YAML a runner can read.

    A workflow that does not parse fails INSTANTLY with no jobs, which on a
    pull request looks like the workflow simply not being required.
    """
    try:
        import yaml
    except ImportError:
        return False, "PyYAML is not installed, so the workflows were not parsed"
    files = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    if not files:
        return False, "no workflows found"
    for path in files:
        try:
            yaml.safe_load(path.read_text())
        except Exception as exc:                        # noqa: BLE001
            return False, f"{path.name}: {type(exc).__name__}"
    return True, f"{len(files)} workflow(s) parse"


def workflow_loops() -> tuple[bool, str]:
    """Run every multi-line `run:` that loops over files, with the command
    stubbed, and prove it still names as many as it did.

    A backslash lost from a line continuation leaves valid YAML that runs a
    shorter list, silently. Counting the names in the source and counting what
    the shell actually iterates are two different questions, and only the
    second one is the answer.
    """
    try:
        import yaml
    except ImportError:
        return False, "PyYAML is not installed"
    checked = 0
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        spec = yaml.safe_load(path.read_text()) or {}
        for job in (spec.get("jobs") or {}).values():
            for step in (job.get("steps") or []):
                run = step.get("run") or ""
                if "for f in" not in run or "\n" not in run:
                    continue
                listed = set()
                for group in re.findall(r"for\s+\w+\s+in\s+(.*?);?\s*do", run, re.S):
                    listed |= {w for w in group.split() if w.endswith(".py")}
                if not listed:
                    continue
                stub = run.replace('python3 "$f"', 'echo "$f"')
                stub = stub.replace("python3 $f", 'echo "$f"')
                done = subprocess.run(["bash", "-c", stub], cwd=ROOT,
                                      capture_output=True, text=True, timeout=120)
                if done.returncode != 0:
                    return False, f"{path.name}: the loop in {step.get('name')!r} will not run"
                # Both sides are sets of names, so this catches a file dropped
                # from the MIDDLE of the list -- which any count-against-count
                # comparison is blind to.
                iterated = {w for w in done.stdout.split() if w.endswith(".py")}
                missing = sorted(listed - iterated)
                if missing:
                    return False, (f"{path.name}: {step.get('name')!r} lists "
                                   f"{len(listed)} files but never reaches "
                                   + ", ".join(missing[:4]))
                checked += 1
    return True, f"{checked} file loop(s) iterate every file they name"


MERGE_CHECKS = [
    ("conflict markers", conflict_markers),
    ("workflows parse", workflows_parse),
    ("workflow file loops", workflow_loops),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--merge", action="store_true",
                    help="also run the merge-only checks (markers, workflow YAML, loops)")
    ap.add_argument("--only-merge", action="store_true",
                    help="run ONLY the merge-only checks; skips the sweep")
    ap.add_argument("--skip-gateway", action="store_true",
                    help="skip the MCP gateway tests (they need mcp_gateway/requirements.txt)")
    ap.add_argument("--list", action="store_true", help="print what would run and stop")
    args = ap.parse_args()

    plan: list[tuple[str, object]] = []
    if not args.only_merge:
        plan += [("sweep", item) for item in SWEEP]
        if not args.skip_gateway:
            plan += [("gateway", item) for item in GATEWAY]
    if args.merge or args.only_merge:
        plan += [("merge", item) for item in MERGE_CHECKS]

    if args.list:
        for group, item in plan:
            print(f"  {group:8} {item[0]}")
        return 0

    failed = []
    group_now = ""
    for group, item in plan:
        if group != group_now:
            group_now = group
            print(f"\n{group}\n{'-' * len(group)}")
        label = item[0]
        print(f"  {label:34} ", end="", flush=True)
        if group == "merge":
            ok, note = item[1]()
        else:
            ok, note = _run(label, item[1],
                            GATEWAY_ENV if group == "gateway" else None)
        print(("ok    " if ok else "FAIL  ") + note)
        if not ok:
            failed.append(label)

    print()
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        if any(f.startswith("mcp_gateway") for f in failed):
            print("  the gateway tests need: pip install -r mcp_gateway/requirements.txt")
    else:
        print(f"all {len(plan)} check(s) passed")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
