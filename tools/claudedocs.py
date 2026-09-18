#!/usr/bin/env python3
"""The `docs/claude/` index, derived from the directory rather than typed.

    python3 tools/claudedocs.py            # check; non-zero if it is stale
    python3 tools/claudedocs.py --write    # regenerate the table

`docs/claude/README.md` is the index `CLAUDE.md` tells every session to open
before editing a module. It was maintained by hand — each change adding a write-up
appended one row — and that shape has two faults, both measured rather than
supposed.

**The line counts drift, silently and in one direction.** A row is written when
a file is added and never touched again, so every later edit to that file
leaves the number behind. Five of sixty-seven were wrong when this was written,
and `56-verifying-a-change.md` — the file the root guide points at for what
each test guards — read **679 against an actual 988**. Nobody was going to
notice: the index renders perfectly well with a wrong number in it.

**And appending to one list conflicts with every concurrent change.** Two
branches that each add a write-up both add a row at the end of the same table,
which is a textual conflict every time. One change in this repo hit it three
times in a single evening, each round-trip costing a full CI run.

Resolving one is **mechanical**: take either side, run `--write`, and the
result is correct by construction rather than by whoever merged it getting the
ordering and the counts right by hand. The check then catches a resolution that
was botched anyway.

**And the conflict itself is gone now**, which this file previously said
nothing keeping a browsable table in one place could manage. `.gitattributes`
gives this file `merge=union`, so a merge takes both sides' rows instead of
stopping. The table comes out duplicated and out of order -- which is fine,
because it is DERIVED: the check below exits 1 naming the file, and `--write`
regenerates it from the directory.

That trade is worth making for a reason beyond the delay. **A conflicted pull
request produces no CI at all**: GitHub runs `pull_request` workflows against a
merge commit it cannot build for a conflicted branch, so the symptom is silence
rather than a message saying the branch conflicts. It cost one change two
pushes and a manual `workflow_dispatch` to diagnose. A union merge turns that
silence into a gated check failure that names the file and the fix.

Both halves are measured in `test_claude_docs_index.py` rather than reasoned
about: the same two-branch case conflicts under the default driver and merges
under union, and the check goes red on the duplicate row union leaves.

**The numbering is the other half.** Two branches also both take the next free
`NN-`, so both land a file at that number and the second one renames. Caught
here at pull-request time, with both names printed, rather than at merge time
when the fix is a rebase.

Reads the directory and the files; writes nothing unless asked. No new
dependencies — stdlib only, like the rest of `tools/`.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "claude"
INDEX = DOCS / "README.md"

#: The table header the generated rows go under. Everything above it in the
#: file is prose somebody wrote and this never touches.
HEADER = "| File | Topic | Lines |"
RULE = "|---|---|---|"

ROW = re.compile(r"\|\s*\[`(?P<file>[^`]+)`\]\([^)]+\)\s*\|"
                 r"\s*(?P<topic>.*?)\s*\|\s*(?P<lines>\d+)\s*\|\s*$")
#: `28a-` and `28b-` are real: a write-up split in two after the fact,
#: deliberately kept beside 28 rather than renumbered to the end of the
#: list, where the cross-references that say "above" would have been wrong.
NUMBERED = re.compile(r"^(\d+)([a-z]?)-")


HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")


def title_of(path: pathlib.Path) -> str:
    """The file's own first heading, at whatever level it is written.

    Taken from the file rather than from the index, so the two cannot come to
    disagree — which is the whole point. A file with no heading is reported
    rather than given one: inventing a title here would put a name in the index
    that appears nowhere in the file it names.

    **Any level, not `# `.** The first draft of this insisted on an H1 and
    reported fifty-seven files as having no heading. They have one — the older
    write-ups were `##` sections of the root `CLAUDE.md` before it was split,
    and they kept the level they were written at. A check that is stricter than
    the repo is a check that reports the repo as broken, and the thing it would
    have prompted (rewriting fifty-seven headings to satisfy a tool) is worse
    than the state it was complaining about.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        m = HEADING.match(line)
        if m and m.group(1):
            return m.group(1)
    return ""


def sort_key(name: str):
    """Numeric prefix first, so 9 sorts before 10 rather than after it.

    The letter suffix sorts within its number, which is what puts `28a` and
    `28b` after `28` rather than at the end of the list — where a plain string
    sort leaves them, and where every cross-reference saying "above" is wrong.
    """
    m = NUMBERED.match(name)
    if not m:
        return (10 ** 6, "", name)
    return (int(m.group(1)), m.group(2), name)


def entries() -> tuple[list[dict], list[str]]:
    """(rows the index should carry, problems that stop it being generated)."""
    problems: list[str] = []
    files = sorted((p for p in DOCS.glob("*.md") if p.name != "README.md"),
                   key=lambda p: sort_key(p.name))

    # Two files at one number. Both branches picked the next free one, and the
    # collision is invisible until a merge -- so it is named here, with both
    # files, while renaming one is still cheap.
    #
    # Keyed on the number AND its letter suffix, because `28`, `28a` and `28b`
    # are three deliberate files and not one collision. The first draft keyed
    # on the number alone and reported them as a duplicate -- the same mistake
    # as `title_of` insisting on an H1, and the same lesson: a check that is
    # stricter than the repo reports the repo as broken, and the change it
    # prompts is worse than what it complained about.
    by_number: dict[str, list[str]] = {}
    for p in files:
        m = NUMBERED.match(p.name)
        if m:
            by_number.setdefault(m.group(1) + m.group(2), []).append(p.name)
    for number, names in sorted(by_number.items()):
        if len(names) > 1:
            problems.append(
                f"two files are numbered {number}: " + ", ".join(names)
                + " — rename the later one to the next free number and update "
                  "any reference to it.")

    rows = []
    for p in files:
        topic = title_of(p)
        if not topic:
            problems.append(f"{p.name} has no heading, so the index has no "
                            f"title to carry. Give it one.")
            continue
        rows.append({"file": p.name, "topic": topic,
                     "lines": len(p.read_text(encoding="utf-8").splitlines())})
    return rows, problems


def render(rows: list[dict]) -> list[str]:
    return [f"| [`{r['file']}`]({r['file']}) | {r['topic']} | {r['lines']} |"
            for r in rows]


def current() -> tuple[list[str], list[dict]]:
    """(the file's lines, the rows it currently carries)."""
    lines = INDEX.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines:
        m = ROW.match(line)
        if m:
            rows.append({"file": m.group("file"), "topic": m.group("topic"),
                         "lines": int(m.group("lines"))})
    return lines, rows


def rewrite(rows: list[dict]) -> str:
    lines, _ = current()
    try:
        head = lines.index(HEADER)
    except ValueError:
        raise SystemExit(f"{INDEX} has no `{HEADER}` line, so there is no "
                         f"table to regenerate. Add one, or fix the header if "
                         f"it was reworded.")
    # Everything above the header is prose; everything from it down is ours.
    # Any non-row line below the header is dropped, and that is deliberate --
    # a stray line inside a generated table is the kind of thing that survives
    # for months because it renders.
    return "\n".join(lines[:head] + [HEADER, RULE] + render(rows)) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="regenerate the table instead of checking it")
    args = ap.parse_args()

    if not INDEX.is_file():
        print(f"{INDEX} does not exist.")
        return 1

    rows, problems = entries()
    for p in problems:
        print(f"  problem  {p}")

    if args.write:
        if problems:
            print("\nNot written: fix the problems above first. Generating an "
                  "index over a directory this confused would record the "
                  "confusion rather than report it.")
            return 1
        INDEX.write_text(rewrite(rows), encoding="utf-8")
        print(f"{INDEX.relative_to(ROOT)} regenerated — {len(rows)} entries.")
        return 0

    _, have = current()
    want = rows
    stale = []
    have_by_file = {r["file"]: r for r in have}
    want_by_file = {r["file"]: r for r in want}
    for name in sorted(set(have_by_file) | set(want_by_file), key=sort_key):
        h, w = have_by_file.get(name), want_by_file.get(name)
        if h is None:
            stale.append(f"{name} is not in the index")
        elif w is None:
            stale.append(f"{name} is in the index and not in the directory")
        elif h["topic"] != w["topic"]:
            stale.append(f"{name} is indexed as {h['topic']!r} and its heading "
                         f"reads {w['topic']!r}")
        elif h["lines"] != w["lines"]:
            stale.append(f"{name} is indexed at {h['lines']} lines and is "
                         f"{w['lines']}")
    # Order matters as much as content: an index sorted by hand drifts out of
    # the order the cross-references ("above", "below") are written against.
    if not stale and [r["file"] for r in have] != [r["file"] for r in want]:
        stale.append("the rows are not in numeric order")

    for s in stale:
        print(f"  stale    {s}")
    if stale or problems:
        print(f"\n{len(stale) + len(problems)} problem(s). "
              f"Run `python3 tools/claudedocs.py --write` to regenerate the "
              f"index, then commit it.")
        return 1
    print(f"docs/claude/README.md matches the directory — {len(want)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
