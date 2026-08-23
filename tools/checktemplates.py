"""Balance-check the inline <script> blocks that tools/jscheck.py has to skip.

jscheck hands every file and every inline block to `node --check` -- the real
parser -- but it *skips* any block containing `{% %}` or `{{ }}`, because Jinja
is not JavaScript and Node would reject it for the wrong reason. It says so
rather than counting them as passes, which is correct and also leaves most of
this codebase's browser code unchecked: the front end largely lives inside
Jinja templates.

This is what checks those. Jinja expressions are blanked to same-width filler
so line numbers still line up with the file you have to open, and what is left
gets a bracket/string/template/regex balance check. Weaker than a real parser,
but it is the difference between reading those blocks and not reading them.

The balance checker lives here rather than in jscheck because jscheck no longer
has one -- it shells out to node now, and this is its only remaining caller.
Importing `check` from it raised ImportError, so this file did not run at all.

    python tools/checktemplates.py
"""
import re
import sys
import pathlib


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


SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def templates():
    seen = set()
    # Not just templates/. A third of this app's inline JavaScript lives in
    # static HTML a module serves directly — modules/*/public/, a couple of
    # static/ directories, and hub/partner_pages/ — and none of it was
    # checked. modules/suite_panel/public/index.html alone carries 500 lines
    # of it. The name of the tool is now slightly wrong; the coverage is not.
    for pattern in ("hub/templates/**/*.html", "hub/partner_pages/**/*.html",
                    "modules/**/templates/**/*.html",
                    "modules/**/public/**/*.html",
                    "modules/**/static/**/*.html"):
        for path in pathlib.Path(".").glob(pattern):
            if "_attic" in path.parts or path in seen:
                continue
            seen.add(path)
            yield path


def inline_js(raw):
    """The script blocks, with Jinja blanked to same-length filler.

    Replacing {{ x }} with a token of the same width keeps every later line on
    the line number it actually occupies in the file."""
    body = "\n".join(SCRIPT.findall(raw))
    body = re.sub(r"\{\{.*?\}\}", lambda m: "0" * len(m.group(0)), body, flags=re.S)
    body = re.sub(r"\{%.*?%\}", lambda m: " " * len(m.group(0)), body, flags=re.S)
    return body


def main():
    failures = 0
    checked = 0
    for path in sorted(templates()):
        raw = path.read_text(encoding="utf-8", errors="replace")
        body = inline_js(raw)
        if not body.strip():
            continue
        checked += 1
        err = check(body, str(path))
        if err:
            failures += 1
            print("FAIL  " + err)
    print("%d templates with inline script checked, %d failing" % (checked, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
