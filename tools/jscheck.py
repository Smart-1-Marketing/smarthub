"""Syntax-check every piece of JavaScript in the repo, including inline blocks.

`node --check hub/static/*.js` covers the standalone files. It does not cover
the JavaScript written directly into Jinja templates, which is where most of
this codebase's browser code actually lives — the diagnostics page, the leads
panel, Client 360. A syntax error there is invisible until somebody opens the
page and a panel silently never renders.

Templates are not valid JavaScript on their own: a block containing `{% if %}`
or `{{ value }}` is Jinja, and Node would reject it for the wrong reason. Those
blocks get the bracket-balance check below instead — string, comment, template
and regex aware, and tolerant of Jinja because it never has to parse it. That
will not catch every syntax error, but it catches the one that actually
happens when a script edits template-literal HTML in place: an unbalanced
brace, or a backtick that never closes. Both take a page from "one broken
panel" to "blank screen", and both are invisible in a diff.

Anything neither check could look at is *reported* as unchecked rather than
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


BS = "\\"

# Keywords that can be followed by a value, so a slash after one starts a
# REGEX, not a division. `prev` is a single character and cannot tell "return"
# from any other identifier ending in "n", so `return /[",\\r\\n]/.test(v)` was
# read as a divide and the quote inside the character class opened a string
# that never closed. One valid line, one "unterminated string", and a checker
# with a false positive is one people learn to ignore — which is the failure
# mode this whole file exists to avoid.
VALUE_KEYWORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
}


def _keyword_before(src, i):
    """True when the token immediately before src[i] is a value keyword."""
    j = i - 1
    while j >= 0 and src[j] in " \t":
        j -= 1
    end = j + 1
    while j >= 0 and (src[j].isalnum() or src[j] == "_" or src[j] == "$"):
        j -= 1
    word = src[j + 1:end]
    # A property access (obj.return) or a longer identifier ending in one of
    # these words is not the keyword.
    if j >= 0 and src[j] == ".":
        return False
    return word in VALUE_KEYWORDS


def check(src, name):
    i, n = 0, len(src)
    stack = []          # ("(" | "[" | "{" | "`" | "${", line)
    line = 1
    prev = ""           # last significant char — tells a regex from a divide

    def in_template():
        return bool(stack) and stack[-1][0] == "`"

    while i < n:
        c = src[i]

        # ---- inside a template literal, only ` and ${ mean anything -------
        if in_template():
            if c == BS:
                i += 2
                continue
            if c == "\n":
                line += 1
                i += 1
                continue
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                stack.append(("${", line))
                i += 2
                continue
            if c == "`":
                stack.pop()
                prev = "`"
                i += 1
                continue
            i += 1
            continue

        if c == "\n":
            line += 1
            i += 1
            continue

        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue

        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue

        # A slash where a value may start is a regex, not division.
        if c == "/" and (prev in "(,=:[!&|?{};" or prev == ""
                         or _keyword_before(src, i)):
            j, ok, in_class = i + 1, False, False
            while j < n:
                if src[j] == BS:
                    j += 2
                    continue
                if src[j] == "\n":
                    break
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    ok = True
                    break
                j += 1
            if ok:
                i, prev = j + 1, "/"
                continue

        if c == '"' or c == "'":
            quote = c
            i += 1
            while i < n:
                if src[i] == BS:
                    i += 2
                    continue
                if src[i] == quote:
                    break
                if src[i] == "\n":
                    return "%s:%d: unterminated string" % (name, line)
                i += 1
            i += 1
            prev = quote
            continue

        if c == "`":
            stack.append(("`", line))
            i += 1
            continue

        if c in "([{":
            stack.append((c, line))
            prev = c
            i += 1
            continue

        if c in ")]}":
            if not stack:
                return "%s:%d: stray '%s'" % (name, line, c)
            # A '}' closing an interpolation hands control back to the template.
            if c == "}" and stack[-1][0] == "${":
                stack.pop()
                prev = "}"
                i += 1
                continue
            opener, opened_at = stack.pop()
            if opener not in "([{" or "([{".index(opener) != ")]}".index(c):
                return "%s:%d: '%s' closes '%s' from line %d" % (
                    name, line, c, opener, opened_at)
            prev = c
            i += 1
            continue

        if not c.isspace():
            prev = c
        i += 1

    if stack:
        opener, opened_at = stack[-1]
        what = "template literal" if opener == "`" else "'%s'" % opener
        return "%s: unclosed %s from line %d" % (name, what, opened_at)
    return None

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
                # Node cannot read this, but the balance checker can: it walks
                # the source character by character and never has to
                # understand a {{ value }} it passes over.
                err = check(block, f"{rel} (inline block {i + 1})")
                skipped += 1
                if err:
                    failures.append(f"{rel} inline block {i + 1}\n{err}")
                else:
                    jinja_skipped.append(f"{rel} block {i + 1}")
                continue
            err = _node_check(block, f"{rel} (inline block {i + 1})")
            checked += 1
            if err:
                failures.append(f"{rel} inline block {i + 1}\n{err}")

    print(f"node-checked {checked} JavaScript block(s); "
          f"balance-checked {skipped} more that carry Jinja syntax")
    if jinja_skipped:
        # Named, not just counted. These passed the balance check, which is
        # real but partial — the number alone would read like full coverage.
        for s in jinja_skipped[:15]:
            print(f"  balance only (Jinja, not node-checked): {s}")
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
