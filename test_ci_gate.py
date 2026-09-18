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
import ast
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
def named_tests(source):
    """Keep relative directories so nested tests are checked at their real path."""
    return set(re.findall(r"((?:[A-Za-z0-9_.-]+/)*test_[a-z0-9_]+\.py)", source))


named = named_tests(src)

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
    if (ROOT / f).is_file():
        continue
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

# ------------------------------------------- no test writes into the repo
#
# A test that plants a fixture file in the working tree and deletes it in a
# `finally` is two failures waiting:
#
#   * it races every concurrent sweep. `tools/preflight.py`'s "compile every
#     module" step listed `hub/_integrity_orphan_caller.py` and read it after
#     test_env_config.py had deleted it, and failed with FileNotFoundError on a
#     file that had never been committed. Nothing about that error names the
#     test that caused it, and re-running serially makes it disappear -- which
#     is the shape of a defect that gets called a flake for a year.
#   * it survives a run that dies. SIGKILL, a failed assertion outside the
#     try, a debugger -- any of them leaves the probe in the working tree, and
#     the next `git status` shows an untracked file in hub/ that nobody wrote.
#
# Three files did this and all three had the same excuse, which was a real one:
# the check under test walked the repository and took no argument, so a probe
# had nowhere else to go. That is the thing to fix -- `check_orphan_templates`,
# `check_provider_key_drift`, `check_own_fernet` and this file's own
# `unwired()` now take a root or a list of sources, the way
# `check_ai_callers(root)` and `check_shadowed_model_query(sources=)` always
# did -- so the fixture is a temporary directory or a string, and the repo is
# never touched.
#
# Read by AST rather than by text, because the paragraph you are reading names
# `write_text` and `ROOT` in the same file as the check. A name is repository-
# rooted if it is derived from `__file__`, transitively: the write is almost
# never on `ROOT` itself but on `_probe = ROOT / "hub" / "x.py"` three lines
# later. `.replace` is deliberately not a write attribute -- `str.replace` is
# everywhere in these files, and a check with thirteen false findings in it is
# a check nobody reads.
print("\nno test writes into the repository it is checking")
print("-" * 50)

WRITE_ATTRS = {"write_text", "write_bytes", "mkdir", "touch", "unlink",
               "rmdir", "rename", "symlink_to", "hardlink_to"}
TEMP_CALLS = {"mkdtemp", "TemporaryDirectory", "gettempdir", "mkstemp",
              "NamedTemporaryFile"}

# Tests allowed to write into the tree, with the reason. Empty on purpose --
# the same discipline EXEMPT above and check_stale_json_exemptions() work to.
WRITES_EXEMPT: dict[str, str] = {}


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _temp(node):
    return any((isinstance(n, ast.Attribute) and n.attr in TEMP_CALLS)
               or (isinstance(n, ast.Name) and n.id in TEMP_CALLS)
               for n in ast.walk(node))


def _bindings(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and n.value is not None:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    yield t.id, n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None \
                and isinstance(n.target, ast.Name):
            yield n.target.id, n.value


def repo_rooted(tree):
    """Names bound to a path inside the repository, transitively."""
    pairs = list(_bindings(tree))
    rooted = {name for name, val in pairs
              if any(isinstance(n, ast.Name) and n.id == "__file__"
                     for n in ast.walk(val))}
    for _ in range(len(pairs) + 1):          # to a fixed point
        grew = False
        for name, val in pairs:
            if name not in rooted and not _temp(val) and _names(val) & rooted:
                rooted.add(name)
                grew = True
        if not grew:
            break
    # ...and a name rebound to a temporary path is no longer the repository.
    for name, val in pairs:
        if name in rooted and _temp(val):
            rooted.discard(name)
    return rooted


def repo_writes(source):
    """(line, what) for every write this source aims at its own repository."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    rooted = repo_rooted(tree)
    if not rooted:
        return []
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in WRITE_ATTRS:
            if _names(f.value) & rooted:
                out.add((node.lineno, f.attr + "()"))
        elif isinstance(f, ast.Name) and f.id == "open" and len(node.args) >= 2:
            mode = node.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                    and any(c in mode.value for c in "wax") \
                    and _names(node.args[0]) & rooted:
                out.add((node.lineno, "open() for writing"))
    return sorted(out)


writers = []
for _p in sorted(ROOT.glob("test_*.py")):
    if _p.name in WRITES_EXEMPT:
        continue
    for _line, _what in repo_writes(_p.read_text(encoding="utf-8",
                                                 errors="ignore")):
        writers.append(f"{_p.name}:{_line} {_what}")
check("no test plants a file in the working tree", writers == [],
      "; ".join(writers[:8]))

check("every exemption says why",
      all(str(v).strip() for v in WRITES_EXEMPT.values()),
      [k for k, v in WRITES_EXEMPT.items() if not str(v).strip()])
_stale_writes = sorted(f for f in WRITES_EXEMPT if f not in here)
check("and no exemption outlives the file it exempted",
      _stale_writes == [], _stale_writes)

# An empty finding list is only worth something if the reading behind it can be
# shown to find the thing. All four shapes, on source that is never written
# anywhere -- including the two that made it miss the real cases first time:
# the write is on a name derived from ROOT rather than on ROOT, and `.replace`
# is a string method.
_FIXTURES = [
    ("a write straight onto the root", True,
     'import pathlib\nROOT = pathlib.Path(__file__).parent\n'
     '(ROOT / "x.py").write_text("y")\n'),
    ("a write onto a name derived from it", True,
     'import pathlib\nROOT = pathlib.Path(__file__).parent\n'
     'probe = ROOT / "hub" / "x.py"\nprobe.write_text("y")\n'),
    ("the delete that follows it", True,
     'import pathlib\nROOT = pathlib.Path(__file__).parent\n'
     'probe = ROOT / "hub" / "x.py"\nprobe.unlink(missing_ok=True)\n'),
    ("an open() for writing under it", True,
     'import os\nROOT = os.path.dirname(__file__)\n'
     'p = os.path.join(ROOT, "x.py")\nopen(p, "w").write("y")\n'),
    ("a read of the repository is not a write", False,
     'import pathlib\nROOT = pathlib.Path(__file__).parent\n'
     'src = (ROOT / "hub" / "app.py").read_text()\n'),
    ("nor is str.replace on what it read", False,
     'import pathlib\nROOT = pathlib.Path(__file__).parent\n'
     'src = (ROOT / "hub" / "app.py").read_text()\n'
     'out = src.replace("a", "b")\n'),
    ("a temporary directory is not the repository", False,
     'import pathlib, tempfile\nROOT = pathlib.Path(__file__).parent\n'
     'tmp = pathlib.Path(tempfile.mkdtemp())\n'
     '(tmp / "x.py").write_text("y")\n'),
    ("...even when it was derived from the repository's own name", False,
     'import pathlib, tempfile\nROOT = pathlib.Path(__file__).parent\n'
     'tmp = pathlib.Path(tempfile.mkdtemp(prefix=ROOT.name))\n'
     '(tmp / "x.py").write_text("y")\n'),
]
for _label, _want, _src in _FIXTURES:
    check(_label, bool(repo_writes(_src)) is _want, repo_writes(_src))


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

# ---------------------------------------------------------------------------
# The gate tests the Python the image ships, and builds the image at all.
#
# `checks.yml` installs requirements.txt onto an Ubuntu runner with
# setup-python. That is not what deploys. What deploys is the Dockerfile -- a
# different base, apt packages, Node 20, three `npm ci` runs and two TypeScript
# builds -- and nothing in this repo built it, so "the single gate" did not
# cover the artifact.
#
# The version half is the sharper one. With an open PR moving the base from
# `python:3.12-slim` to `3.14-slim` and setup-python pinned to 3.12, a green
# run on that PR is evidence that 3.12 still works and says NOTHING about the
# 3.14 the container would run. Bumping one without the other is a skew no
# screen reports, so it is refused here instead.
print("\nand it tests the Python the image ships")
print("-" * 46)

_dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
_from = re.search(r"^FROM\s+python:(\d+)\.(\d+)", _dockerfile, re.M)
check("the Dockerfile pins a Python version", bool(_from),
      "no `FROM python:X.Y` line — this check reads that line and nothing else")
_setup = re.search(r"python-version:\s*['\"]?(\d+)\.(\d+)", src)
check("and the workflow pins one for setup-python", bool(_setup),
      "no `python-version:` in checks.yml")
if _from and _setup:
    _img = f"{_from.group(1)}.{_from.group(2)}"
    _ci = f"{_setup.group(1)}.{_setup.group(2)}"
    check("and they are the same version", _img == _ci,
          f"the image ships Python {_img} and the gate tests on {_ci}; a green "
          f"run on {_ci} is not evidence about {_img}")

# Building it is the other half: a version they agree on is still a version
# nobody has built. Path-filtered on purpose -- the expensive layers all sit
# before `COPY . .`, so a Python-only change cannot break the build -- and
# asserted here so the filter cannot quietly stop naming the files that can.
check("there is a job that builds the image", "\n  image:" in src,
      "nothing builds the Dockerfile, so the artifact that deploys is ungated")
check("...and it really runs docker build", "docker build -t smarthub-ci" in src)
check("...and proves the app imports inside it", "import wsgi" in src)
# Sliced on the job's own line rather than on "  image:", which also matches
# the postgres SERVICE's `image:` key ninety lines in -- the first draft did
# that and reported `docker-start.sh` as missing from a filter it is in. The
# check was reading the wrong region of the file, which is the same defect as
# a check that is stricter than the repo: it reports the repo as broken.
_image_job = src[src.index("\n  image:"):]
for _p in ("Dockerfile", "requirements", "docker-start", "package"):
    check(f"...and rebuilds when {_p} changes", _p in _image_job,
          f"{_p} is not in the path filter, so a change to it would skip the build")

# The check has to be able to go red, or it is furniture.
print("\n...and the check bites")
print("-" * 46)
_fake = "test_a_file_the_gate_does_not_run.py"
check("a test file the workflow never names is reported",
      _fake not in named and _fake not in EXEMPT)
check("...and one it does name is not",
      "test_unwired.py" in named,
      "test_unwired.py is what this file was written about")
check("nested test references retain their directories",
      named_tests("python -m pytest tests/test_present.py tests/test_missing.py")
      == {"tests/test_present.py", "tests/test_missing.py"})

print(f"\n{'-' * 46}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
