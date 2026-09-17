"""The docs/claude index generator, driven against directories it will meet.

    python3 test_claude_docs_index.py

Same shape as the other test files here — no pytest, no new dependencies.

## Why this file exists

`tools/claudedocs.py` runs in the gate against **this** repo, which is clean.
So every one of its failure paths — a duplicate number, a file with no
heading, a count that has drifted — is unreachable in CI, and a check whose
failure path is never taken is a check that has not been shown to fail. That is
the same trap `docs/claude/47` and `test_ci_gate.py`'s own history record: an
assertion that cannot go red reads exactly like one that is passing.

Both mistakes this tool made while it was being written are pinned here, and
they are the same mistake twice: **the check was stricter than the repo.**

  1. `title_of()` required a `# ` heading and reported **fifty-seven** files as
     having none. They have headings — the older write-ups were `##` sections
     of the root `CLAUDE.md` before it was split, and they kept that level.
  2. The duplicate-number check keyed on the number alone, so `28`, `28a` and
     `28b` — three deliberate files — came back as one collision.

In both cases the tool would have prompted a change (rewrite 57 headings;
renumber two files and every cross-reference to them) that is worse than what
it was complaining about. So both are assertions rather than memories.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TOOL = ROOT / "tools" / "claudedocs.py"

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def fake_repo(files: dict, index_rows: list[str] | None = None) -> Path:
    """A throwaway tree shaped like the real one, with tools/claudedocs.py in it.

    The tool resolves its paths from its own location, so it is copied in
    rather than imported — which also means what is exercised is the file the
    gate runs, not a function reached around it.
    """
    base = Path(tempfile.mkdtemp(prefix="claudedocs_"))
    (base / "tools").mkdir()
    shutil.copy(TOOL, base / "tools" / "claudedocs.py")
    docs = base / "docs" / "claude"
    docs.mkdir(parents=True)
    for name, body in files.items():
        (docs / name).write_text(body, encoding="utf-8")
    rows = index_rows if index_rows is not None else []
    (docs / "README.md").write_text(
        "# Index\n\nprose that is not ours to touch\n\n"
        "| File | Topic | Lines |\n|---|---|---|\n" + "".join(rows),
        encoding="utf-8")
    return base


def run(base: Path, *args):
    r = subprocess.run([sys.executable, "tools/claudedocs.py", *args],
                       cwd=base, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def row(name, topic, lines):
    return f"| [`{name}`]({name}) | {topic} | {lines} |\n"


# ---------------------------------------------------------------------------
section("1. A heading at any level, because fifty-seven files use ##")
# The mistake that made the first draft useless. Every file below has a
# heading; only one of them has it at the level the first draft demanded.
base = fake_repo({
    "01-one.md": "# One\n\nbody\n",
    "02-two.md": "## Two\n\nbody\n",
    "03-three.md": "### Three\n\nbody\n",
})
code, out = run(base, "--write")
check("it writes rather than refusing", code, 0)
written = (base / "docs" / "claude" / "README.md").read_text()
check("the H1 file is indexed", "| One |" in written, True)
check("...and so is the H2 one", "| Two |" in written, True)
check("...and the H3 one", "| Three |" in written, True)
check("nothing is reported as headingless", "no heading" in out, False)
shutil.rmtree(base, ignore_errors=True)

section("2. A file with genuinely no heading is reported, not invented for")
# The other side of the same rule: the tool may not put a title in the index
# that appears nowhere in the file it names.
base = fake_repo({"01-one.md": "just a paragraph, no heading at all\n"})
code, out = run(base)
check("the check fails", code, 1)
check("...naming the file", "01-one.md" in out, True)
check("...and saying what is wrong", "no heading" in out, True)
code, out = run(base, "--write")
check("and --write refuses rather than inventing one", code, 1)
check("...saying so", "Not written" in out, True)
shutil.rmtree(base, ignore_errors=True)

section("3. Two branches taking the same number")
# The collision that cost three rounds on one pull request. Caught while
# renaming is cheap, rather than at the merge.
base = fake_repo({
    "01-one.md": "# One\n",
    "02-mine.md": "# Mine\n",
    "02-theirs.md": "# Theirs\n",
})
code, out = run(base)
check("the check fails", code, 1)
check("...saying they share a number", "numbered 02" in out, True)
check("...and naming both files",
      "02-mine.md" in out and "02-theirs.md" in out, True)
check("--write refuses over a directory in that state", run(base, "--write")[0], 1)
shutil.rmtree(base, ignore_errors=True)

section("4. A letter suffix is a deliberate file, not a collision")
# 28, 28a and 28b are three write-ups: one split in two after the fact and
# kept beside its parent, because renumbering to the end of the list would
# have made every cross-reference saying "above" wrong.
base = fake_repo({
    "28-parent.md": "# Parent\n",
    "28a-first.md": "# First\n",
    "28b-second.md": "# Second\n",
})
code, out = run(base, "--write")
check("all three are accepted", code, 0)
check("...with no duplicate reported", "numbered" in out, False)
lines = [l for l in (base / "docs" / "claude" / "README.md").read_text().splitlines()
         if l.startswith("| [`")]
check("and they sort parent, a, b",
      [l.split("`")[1] for l in lines],
      ["28-parent.md", "28a-first.md", "28b-second.md"])
shutil.rmtree(base, ignore_errors=True)

section("5. Numeric order, not string order")
# The reason the index is generated rather than appended to: a hand-kept list
# drifts out of the order its own cross-references are written against.
base = fake_repo({f"{n}-f.md": f"# F{n}\n" for n in (2, 9, 10, 100)})
run(base, "--write")
lines = [l for l in (base / "docs" / "claude" / "README.md").read_text().splitlines()
         if l.startswith("| [`")]
check("9 sorts before 10, and 10 before 100",
      [l.split("`")[1] for l in lines],
      ["2-f.md", "9-f.md", "10-f.md", "100-f.md"])
shutil.rmtree(base, ignore_errors=True)

section("6. The drift it was written for")
# Five of sixty-seven rows in the real index were wrong when this was written,
# and 56-verifying-a-change.md read 679 against an actual 988. A row is typed
# when a file is added and never touched again.
base = fake_repo({"01-one.md": "# One\n\nline\nline\n"},
                 index_rows=[row("01-one.md", "One", 2)])
code, out = run(base)
check("a stale count fails the check", code, 1)
check("...saying both numbers", "indexed at 2 lines and is 4" in out, True)
run(base, "--write")
check("and --write corrects it",
      "| One | 4 |" in (base / "docs" / "claude" / "README.md").read_text(), True)
check("...leaving it green", run(base)[0], 0)
shutil.rmtree(base, ignore_errors=True)

section("7. A title edited in the file, and left behind in the index")
base = fake_repo({"01-one.md": "# Renamed\n"},
                 index_rows=[row("01-one.md", "Old name", 1)])
code, out = run(base)
check("a drifted title fails too", code, 1)
check("...quoting both", "'Old name'" in out and "'Renamed'" in out, True)
shutil.rmtree(base, ignore_errors=True)

section("8. A file added, and a row left behind")
base = fake_repo({"01-one.md": "# One\n", "02-two.md": "# Two\n"},
                 index_rows=[row("01-one.md", "One", 1)])
code, out = run(base)
check("an unindexed file is reported", "02-two.md is not in the index" in out, True)
base2 = fake_repo({"01-one.md": "# One\n"},
                  index_rows=[row("01-one.md", "One", 1),
                              row("99-gone.md", "Gone", 1)])
code2, out2 = run(base2)
check("and a row for a file that is gone is too",
      "99-gone.md is in the index and not in the directory" in out2, True)
shutil.rmtree(base, ignore_errors=True)
shutil.rmtree(base2, ignore_errors=True)

section("9. The prose above the table is not the tool's to rewrite")
# The index opens with four sentences somebody wrote about how to read it.
# A generator that owned the whole file would eat them on its first run.
base = fake_repo({"01-one.md": "# One\n"})
run(base, "--write")
after = (base / "docs" / "claude" / "README.md").read_text()
check("the heading survives", after.startswith("# Index"), True)
check("and the prose with it", "prose that is not ours to touch" in after, True)
shutil.rmtree(base, ignore_errors=True)

section("10. It is idempotent, so a second run is not a second diff")
base = fake_repo({"01-one.md": "# One\n", "02-two.md": "## Two\n\nx\n"})
run(base, "--write")
once = (base / "docs" / "claude" / "README.md").read_text()
run(base, "--write")
check("a second --write changes nothing",
      (base / "docs" / "claude" / "README.md").read_text(), once)
check("and the check is green", run(base)[0], 0)
shutil.rmtree(base, ignore_errors=True)

section("11. Against the real repo, which is what the gate runs")
code, out = run(ROOT)
check("the committed index matches the directory", code, 0)
check("...and it says how many it checked", "entries" in out, True)
check("it is in the preflight sweep",
      "claudedocs" in (ROOT / "tools" / "preflight.py").read_text(), True)

print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
