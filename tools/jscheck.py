"""Syntax-check every piece of JavaScript in the repo, including inline blocks.

`node --check hub/static/*.js` covers the standalone files. It does not cover
the JavaScript written directly into Jinja templates, which is where most of
this codebase's browser code actually lives — the diagnostics page, the leads
panel, Client 360. A syntax error there is invisible until somebody opens the
page and a panel silently never renders.

Templates are not valid JavaScript on their own: a block containing `{% if %}`
or `{{ value }}` is Jinja, and Node would reject it for the wrong reason. So
blocks carrying Jinja syntax are skipped and *reported* as skipped rather than
quietly passed — an unchecked file counted as a pass is the failure mode this
repo keeps writing checks against.

Exits non-zero on the first real syntax error, so it can gate a release.

    python3 tools/jscheck.py
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"_attic", "__pycache__", "node_modules", ".git", "dist", "build"}

# A <script> with one of these types holds data or a client-side template, not
# JavaScript. Feeding those to node reports a syntax error in something that
# was never meant to parse.
NON_JS_TYPES = {"text/template", "text/x-template", "text/html", "application/json",
                "application/ld+json", "text/plain", "text/x-handlebars-template"}


def _skip(path: pathlib.Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


class _ScriptFinder(HTMLParser):
    """Inline <script> bodies, found by parsing rather than by pattern.

    The regex version of this matched the literal text `<script>` sitting
    inside a textarea's placeholder attribute and reported a syntax error in a
    hint string. An HTML parser knows the difference between a tag and a
    quoted attribute value that happens to contain one, which is the whole
    reason to use one.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._depth = 0
        self._keep = False

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        self._depth += 1
        a = {k.lower(): (v or "") for k, v in attrs}
        # External scripts have no inline body; non-JS types are not JavaScript.
        self._keep = ("src" not in a
                      and a.get("type", "").split(";")[0].strip().lower()
                      not in NON_JS_TYPES)

    def handle_data(self, data):
        if self._depth and self._keep and data.strip():
            self.blocks.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._depth:
            self._depth -= 1
            self._keep = False


def _node_check(source: str, label: str) -> str:
    """"" if it parses, else the error text."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(source)
        tmp = fh.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        if r.returncode == 0:
            return ""
        # The temp path in the error is noise; the label is what locates it.
        return (r.stderr or r.stdout).replace(tmp, label).strip()
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)


def main() -> int:
    if subprocess.run(["node", "--version"], capture_output=True).returncode != 0:
        print("node is not available, so no JavaScript was checked.")
        return 1

    checked = skipped = 0
    failures: list[str] = []

    for path in sorted(ROOT.rglob("*.js")):
        if _skip(path.relative_to(ROOT)):
            continue
        rel = path.relative_to(ROOT).as_posix()
        err = _node_check(path.read_text(encoding="utf-8", errors="ignore"), rel)
        checked += 1
        if err:
            failures.append(f"{rel}\n{err}")

    jinja_skipped: list[str] = []
    for path in sorted(ROOT.rglob("*.html")):
        if _skip(path.relative_to(ROOT)):
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            html = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        finder = _ScriptFinder()
        try:
            finder.feed(html)
        except Exception:                                 # noqa: BLE001
            # Unparseable markup: report it rather than counting it as clean.
            failures.append(f"{rel}\ncould not be parsed as HTML")
            continue
        for i, block in enumerate(finder.blocks):
            if not block.strip():
                continue
            if "{%" in block or "{{" in block:
                skipped += 1
                jinja_skipped.append(f"{rel} block {i + 1}")
                continue
            err = _node_check(block, f"{rel} (inline block {i + 1})")
            checked += 1
            if err:
                failures.append(f"{rel} inline block {i + 1}\n{err}")

    print(f"checked {checked} JavaScript block(s); "
          f"skipped {skipped} containing Jinja syntax")
    if jinja_skipped:
        # Named, not just counted: these are genuinely unchecked, and the
        # number alone reads like coverage it does not have.
        for s in jinja_skipped[:15]:
            print(f"  not checked (Jinja): {s}")
        if len(jinja_skipped) > 15:
            print(f"  …and {len(jinja_skipped) - 15} more")

    if failures:
        print(f"\n{len(failures)} JavaScript syntax error(s):\n")
        for f in failures:
            print(f + "\n")
        return 1
    print("no JavaScript syntax errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
