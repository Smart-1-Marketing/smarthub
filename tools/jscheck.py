"""Bracket balance for JavaScript: string, comment, template and regex aware.

There is no node on the machine this gets developed on, so this stands in for
`node --check`. It will not catch every syntax error, but it catches the one
that actually happens when a script edits template-literal HTML in place: an
unbalanced brace, or a backtick that never closes. Both of those take a page
from "one broken panel" to "blank screen", and both are invisible to a diff.

    python tools/jscheck.py hub/static/*.js

Templates need real nesting, not a counter. `${items.map(x => `<li>${x}</li>`)}`
puts a template inside an interpolation inside a template, and an object
literal inside `${...}` has braces of its own. A first version of this counted
`${` and `}` and reported half this codebase as broken, which is worse than no
checker: it trains you to ignore it. So the template marker goes on the same
stack as every other bracket.
"""
import sys

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


if __name__ == "__main__":
    failed = 0
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            err = check(fh.read(), path)
        if err:
            failed = 1
            print("FAIL  " + err)
        else:
            print("OK    " + path)
    sys.exit(failed)
