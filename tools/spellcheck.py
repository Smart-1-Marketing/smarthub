#!/usr/bin/env python3
"""American English, over the copy this Hub puts on a screen.

    python tools/spellcheck.py

Smart 1 sells in the US. `modules/scans/reports.py` has said so in code for a
while -- it normalises the British copy Insites writes into its callback
before any of it reaches a client -- and everything the Hub wrote itself was
drifting the other way: "colour", "behaviour", "licence", "organisation",
"analyse" and "centre" across forty templates, a proposal a client reads, and
the help layer.

## Why this is a check and not a one-off pass

A one-off pass is undone by the next feature. The words come back one
template at a time and nobody notices, because a British spelling is not a
defect in any way a test or a linter can see: the page renders, the link
resolves, the copy is correct English. This runs in CI beside jscheck and
linkcheck so a new "colour" is a failing build rather than something a client
finds.

## What it reads, and what it deliberately does not

**Python is scanned as string literals only**, through the AST. A full-text
pass would report `from .images import optimise`, `client_key.normalise_name`
and ~30 other shared function names, which is a rename of the codebase
dressed up as a copy change. What a person reads is in the literals.
Docstrings are skipped for the same reason comments are: they are for
whoever opens the file.

**Four shapes are not copy**, and each has cost something here:

* a word touching `_` is a snake_case identifier or an external field name.
  `colour_scheme`, `primary_text_colour` and `pages_analysed` are Insites'
  spellings in Insites' payload, and renaming them reads back on Client 360
  as a site with no palette and no page count.
* a word immediately followed by `(` is a function being called or defined.
* a word touching `/` is a URL path segment. A route already in a browser's
  history is not a spelling anybody reads.
* anything in `ALLOW` below, which is per file *and* per word, with the
  reason written down. A blanket per-file exemption goes stale the way
  `check_stale_json_exemptions()` describes: it outlives what it exempted and
  then covers whatever is written at that path next.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The British spelling, and what it should be. Stems are lookahead-guarded:
# a bare "analys" -> "analyz" also rewrites "analysis" into "analyzis".
WORDS: list[tuple[str, str]] = [
    ("centred", "centered"), ("centring", "centering"), ("centre", "center"),
    ("colour", "color"), ("behaviour", "behavior"), ("favour", "favor"),
    ("honour", "honor"), ("neighbour", "neighbor"), ("endeavour", "endeavor"),
    ("organis(?=e|a|ing|ed)", "organiz"),
    ("recognis(?=e|a|ing|ed)", "recogniz"),
    ("summaris(?=e|a|ing|ed)", "summariz"),
    ("authoris(?=e|a|ing|ed)", "authoriz"),
    ("categoris(?=e|a|ing|ed)", "categoriz"),
    ("personalis(?=e|a|ing|ed)", "personaliz"),
    ("prioritis(?=e|a|ing|ed)", "prioritiz"),
    ("utilis(?=e|a|ing|ed)", "utiliz"),
    ("customis(?=e|a|ing|ed)", "customiz"),
    ("specialis(?=e|a|ing|ed)", "specializ"),
    ("capitalis(?=e|a|ing|ed)", "capitaliz"),
    ("standardis(?=e|a|ing|ed)", "standardiz"),
    ("centralis(?=e|a|ing|ed)", "centraliz"),
    ("maximis(?=e|a|ing|ed)", "maximiz"),
    ("minimis(?=e|a|ing|ed)", "minimiz"),
    ("apologis(?=e|a|ing|ed)", "apologiz"),
    ("realis(?=e|a|ing|ed)", "realiz"),
    ("optimis(?=e|a|ing|ed)", "optimiz"),
    ("normalis(?=e|a|ing|ed)", "normaliz"),
    # "analyses" is the plural noun far more often than the verb here.
    (r"analys(?=e(?!s\b)|ing|er)", "analyz"),
    ("catalogu(?=e|ing)", "catalog"),
    ("licence", "license"),
    ("enquir", "inquir"),
    (r"\bgrey\b", "gray"), ("greyscale", "grayscale"), ("greyed", "grayed"),
    ("judgement", "judgment"), ("ageing", "aging"),
    (r"\bwhilst\b", "while"), (r"\bamongst\b", "among"),
    # Whole-word: `aria-labelledby` is an ARIA attribute, not a misspelling.
    (r"\bmodelled\b", "modeled"), (r"\bmodelling\b", "modeling"),
    (r"\blabelled\b", "labeled"), (r"\blabelling\b", "labeling"),
    (r"\btravelled\b", "traveled"), (r"\btravelling\b", "traveling"),
    (r"\bsignalling\b", "signaling"),
    (r"fulfil(?!l)", "fulfill"),
    ("practis(?=e|ing)", "practic"),
    ("defence", "defense"), ("offence", "offense"),
    ("programme", "program"), ("artefact", "artifact"),
    ("sceptic", "skeptic"), ("acknowledgement", "acknowledgment"),
    ("instalment", "installment"), ("manoeuvr", "maneuver"),
    (r"\bmould", "mold"), ("plough", "plow"), ("cheque", "check"),
]
COMPILED = [(re.compile(p, re.I), r) for p, r in WORDS]

# Per file *and* per word, each with the reason. Never a bare file name.
ALLOW: dict[str, set[str]] = {
    # The British -> American table itself, and the copy explaining it.
    # Rewriting it turns every rule into ("color", "color") and the Insites
    # copy we lift stays British with the normaliser reporting a clean pass.
    "modules/scans/reports.py": {"*"},
    # The "#enquire" anchor, built from the landing-page goal id, is in every
    # page already saved -- renaming it takes the form off each one. The label
    # a visitor reads says "inquiry"; only the anchor keeps the old spelling.
    "hub/landing_render.py": {"enquire"},
    "test_landing_maker.py": {"enquire"},
    # These are what those checks look *for*. A pass over the copy converted
    # test_display_ads.py's own word list once, and the check then reported
    # every correct "color" on the builder's pages as a finding.
    "test_display_ads.py": {"colour", "centre", "centred"},
    "test_spelling.py": {"*"},
    "tools/spellcheck.py": {"*"},
}

SKIP_DIRS = {"_attic", "node_modules", ".git", ".venv", "venv", "dist", "build"}
SKIP_SUBSTRINGS = ("clients_app/static",)   # a built React bundle, not source

# A Python string that is one bare lowercase token, or a dotted path of them,
# is a dict key, a stored id, a tag or an external field path --
# `"catalogue"`, `"enquire"`, `"colourful"`, `"grey"`,
# `"display_ads.ad_transparency_centre_url"` -- and not something anybody
# reads. Renaming those is a data migration wearing a copy change: the
# landing-page goal id `enquire` is in every page already saved, `colourful`
# is a Cloudinary tag on every clip indexed before today, and the dotted ones
# are Insites' own spellings inside Insites' own payload, where correcting
# them reads back on Client 360 as a site with no ad transparency link at all.
# Copy has spaces or capitals in it, so this costs the check nothing real.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


def _rel(p: pathlib.Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _skipped(p: pathlib.Path) -> bool:
    if any(part in SKIP_DIRS for part in p.parts):
        return True
    rel = _rel(p)
    if any(s in rel for s in SKIP_SUBSTRINGS):
        return True
    return p.name.endswith((".min.js", ".min.css"))


def _allowed(rel: str, word: str) -> bool:
    words = ALLOW.get(rel)
    if not words:
        return False
    return "*" in words or word.lower() in words


def findings_in(text: str, rel: str, line_offset: int = 1) -> list[tuple[int, str]]:
    """Every British spelling in ``text`` that is copy rather than code."""
    out: list[tuple[int, str]] = []
    # By start offset: "centred" is matched by its own rule and by the
    # "centre" rule behind it, and reporting one word twice makes a count
    # nobody can reconcile with the page.
    seen: set[int] = set()
    for rx, _repl in COMPILED:
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            ws, we = s, e
            while ws > 0 and text[ws - 1].isalpha():
                ws -= 1
            while we < len(text) and text[we].isalpha():
                we += 1
            before = text[ws - 1] if ws else ""
            after = text[we] if we < len(text) else ""
            if before == "_" or after == "_":
                continue                       # a snake_case or API field name
            if after == "(":
                continue                       # a function call or definition
            if before == "/" or after == "/":
                continue                       # a URL path segment
            word = text[ws:we]
            if _allowed(rel, word) or ws in seen:
                continue
            seen.add(ws)
            out.append((line_offset + text.count("\n", 0, ws), word))
    return out


# ---------------------------------------------------------------- TypeScript

# A TS/JS token that is code rather than copy, in the two shapes the Python
# rule's `_IDENTIFIER` does not cover because they only occur here.
#
# `_TS_INTERP` is a `${...}` span inside a template literal. Its contents are
# expressions -- `${step.colours}` is sharp's own option name -- and reading
# them as copy reports an external API's spelling as a defect, which is the
# `colour_scheme` rule one language over.
_TS_INTERP = re.compile(r"\$\{[^{}]*\}")


def _ts_strings(src: str) -> list[tuple[int, str]]:
    """Every string literal in a TS/JS source, as (offset, text).

    The equivalent of reading Python through its AST, done with a scanner
    because there is no TypeScript parser here and adding one would put a
    node dependency inside a check that has to run wherever Python does.

    What it has to get right is that comments and code are NOT copy. This
    module is ten thousand lines of TypeScript carrying `rasterise`,
    `normalise`, `optimise` and sharp's own `colours` option, none of which is
    a word on a screen -- a full-text pass over it would be a rename of the
    renderer dressed up as a copy change, the exact thing the Python rule
    exists to avoid.

    One ambiguity is handled explicitly: a `/` starts a regex literal or a
    division depending on what came before it, and reading a regex as division
    would leave the scanner inside a string it never entered. The previous
    significant character decides, which is the same rule a JS tokenizer uses.
    """
    out: list[tuple[int, str]] = []
    i, n = 0, len(src)
    prev = ""                      # last significant char, for the regex test
    while i < n:
        c = src[i]
        # comments -- for whoever opens the file, never for a screen
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        # a regex literal, skipped whole: it is a pattern, not a sentence
        if c == "/" and prev in "(,=:[!&|?{};+-*%~^" + "\n":
            j, esc, cls = i + 1, False, False
            while j < n:
                d = src[j]
                if esc:
                    esc = False
                elif d == "\\":
                    esc = True
                elif d == "[":
                    cls = True
                elif d == "]":
                    cls = False
                elif d == "/" and not cls:
                    break
                elif d == "\n":
                    break
                j += 1
            i = j + 1
            prev = "/"
            continue
        if c in "'\"`":
            quote, j, esc, buf, start = c, i + 1, False, [], i + 1
            while j < n:
                d = src[j]
                if esc:
                    esc = False
                elif d == "\\":
                    esc = True
                elif d == quote:
                    break
                elif d == "\n" and quote != "`":
                    break              # an unterminated quote is not a string
                buf.append(d)
                j += 1
            body = "".join(buf)
            if quote == "`":
                # Blank the interpolations to same-width filler so offsets --
                # and therefore line numbers -- still line up, the trick
                # tools/checktemplates.py uses on Jinja.
                body = _TS_INTERP.sub(lambda m: " " * len(m.group(0)), body)
            out.append((start, body))
            i = j + 1
            prev = quote
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


def _ts_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    rel = _rel(path)
    out: list[tuple[int, str]] = []
    for offset, body in _ts_strings(src):
        # The same rule the Python literals follow: one bare lowercase token is
        # a key, a stored id or an enum value, not a sentence. `focal:
        # 'centre'` and `background: 'grey'` are values the renderer switches
        # on and that saved concepts already carry -- renaming those is a data
        # migration wearing a copy change.
        if _IDENTIFIER.match(body.strip()):
            continue
        # The line the LITERAL starts on, handed to findings_in as the base so
        # what comes back is the line the WORD is on. proof.ts renders its
        # whole page from one 400-line template literal, and reporting every
        # finding in it at the line the backtick opens on is a report nobody
        # can act on -- which is why the interpolations above are blanked to
        # same-width filler rather than removed.
        base = src.count("\n", 0, offset) + 1
        out.extend(findings_in(body, rel, base))
    return out


def _python_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            if (n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)
                    and isinstance(n.body[0].value.value, str)):
                docs.add((n.body[0].value.lineno, n.body[0].value.col_offset))
    rel = _rel(path)
    out: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        if (n.lineno, n.col_offset) in docs:
            continue
        if _IDENTIFIER.match(n.value):
            continue
        out.extend(findings_in(n.value, rel, n.lineno))
    return out


def scan() -> dict[str, list[tuple[int, str]]]:
    """{path: [(line, word), ...]} for everything a person reads."""
    found: dict[str, list[tuple[int, str]]] = {}
    for pattern in ("*.html", "*.js", "*.css"):
        for p in sorted(ROOT.rglob(pattern)):
            if _skipped(p):
                continue
            hits = findings_in(p.read_text(encoding="utf-8", errors="ignore"),
                               _rel(p))
            if hits:
                found[_rel(p)] = hits
    for p in sorted(ROOT.rglob("*.py")):
        if _skipped(p):
            continue
        hits = _python_literals(p)
        if hits:
            found[_rel(p)] = hits
    # TypeScript, string literals only, for the same reason Python is.
    #
    # It was outside this check entirely until now, and the one module that is
    # not Python is the one that renders a client-facing page from `.ts` -- so
    # the proof a client opens and the panel a rep reads were the only screens
    # in the Hub no spelling rule applied to. Nothing reported that: the page
    # renders, the link resolves, and the English is correct.
    for p in sorted(ROOT.rglob("*.ts")):
        if _skipped(p) or p.name.endswith(".d.ts"):
            continue
        hits = _ts_literals(p)
        if hits:
            found[_rel(p)] = hits
    return found


def stale_allowances() -> list[str]:
    """Entries in ALLOW naming a file that is gone, or a word it no longer has.

    An exemption that outlives what it exempted goes on covering whatever is
    written at that path next, with the check staying green while it does it
    -- the failure `jsonstore.check_stale_json_exemptions()` names.
    """
    stale = []
    for rel, words in sorted(ALLOW.items()):
        path = ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (no such file)")
            continue
        if "*" in words:
            continue
        src = path.read_text(encoding="utf-8", errors="ignore").lower()
        for w in sorted(words):
            if w not in src:
                stale.append(f"{rel}: \"{w}\" is no longer in the file")
    return stale


def main() -> int:
    found = scan()
    stale = stale_allowances()
    total = sum(len(v) for v in found.values())
    for rel, hits in sorted(found.items()):
        for line, word in hits:
            print(f"  {rel}:{line}: {word}")
    for s in stale:
        print(f"  stale exemption: {s}")
    if total:
        print(f"\n{total} British spelling(s) in copy. American English, "
              f"per hub/../tools/spellcheck.py.")
    if stale:
        print(f"{len(stale)} stale exemption(s) in ALLOW.")
    if not total and not stale:
        print("no British spellings in copy.")
    return 1 if (total or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
