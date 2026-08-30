"""American English, over the copy this Hub puts on a screen.

    python3 test_spelling.py

Same shape as the other test files: no pytest, no new dependencies.

## Why this file exists

`tools/spellcheck.py` is the check, and a check that can be silenced by an
edit somewhere else is worse than no check -- the failure `hub/config.py`'s
ALIASES table describes at length, where the drift check regexed a shape that
stopped existing, found no groups, and read as a clean bill of health with
every module still drifting.

So this file does not assert the check is green and stop. It hands the
matcher a sentence that plainly drifts and requires it to say so, hands it the
four shapes that are code rather than copy and requires silence on each, and
asserts that nothing in `ALLOW` has outlived the thing it exempted.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import spellcheck  # noqa: E402

_passed = _failed = 0


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


def words(text, rel="some/file.html"):
    return [w for _line, w in spellcheck.findings_in(text, rel)]


# ---------------------------------------------------------- 1. it still bites
section("A sentence that plainly drifts is reported")

check("colour", words("Pick the brand colour you want.") == ["colour"],)
check("behaviour", words("browsing behaviour only") == ["behaviour"])
check("organisation", words("the organisation on file") == ["organisation"])
check("licence", words("a licence they do not hold") == ["licence"])
check("centre", words("the call centre") == ["centre"])
check("analyse", words("we could not analyse the page") == ["analyse"])
check("grey", words("no answer yet stays grey") == ["grey"])
check("enquiry", words("Is a general enquiry the goal?") == ["enquiry"])
check("catalogue", words("the QuickBooks catalogue") == ["catalogue"])
check("whilst", words("whilst the campaign runs") == ["whilst"])
check("several at once", len(words("Colour and behaviour, whilst centred")) == 4,
      True)

# The words that merely look British. Reporting these is how a check gets
# switched off -- the rule hub/config.py's ALIASES table gives about adding a
# spelling nobody has ever used.
section("A word that is already American is left alone")

for ok in ("analysis", "psychoanalysis", "realistic", "specialist",
           "the analyses show", "center", "color", "gray", "license",
           "program", "aging report"):
    check(f'"{ok}"', words(ok) == [], True)


# ------------------------------------------------- 2. code is not copy
section("The four shapes that are code rather than copy")

check("an external field name is not a misspelling",
      words("colour_scheme.primary_text_colour") == [])
check("...nor is one at the end of a path",
      words("pages_analysed") == [])
check("a function being called is not a misspelling",
      words("centreScale(img, true)") == [])
check("...nor one being defined",
      words("function summarise() {") == [])
check("a URL path segment is not a misspelling",
      words("fetch(`${MOUNT}/api/proposals/1/analyse/landing-page`)") == [])
check("an ARIA attribute is not a misspelling",
      words('<div aria-labelledby="vtitle">') == [])
check("but the copy beside it still is",
      words('<div aria-labelledby="t">Brand colour</div>') == ["colour"])

# A bare lowercase token in Python is a key, a stored id or a tag. Renaming
# one is a data migration wearing a copy change: "enquire" is the goal id in
# every landing page already saved, and "colourful" is a Cloudinary tag on
# every clip indexed before the copy was settled.
check("a bare lowercase token is a key, not a sentence",
      spellcheck._IDENTIFIER.match("catalogue") is not None)
check("...and a sentence is not a key",
      spellcheck._IDENTIFIER.match("General enquiry") is None)
check("...nor is a capitalized label",
      spellcheck._IDENTIFIER.match("Colour") is None)


# ------------------------------------------------- 3. the exemptions are alive
section("Nothing in ALLOW has outlived what it exempted")

stale = spellcheck.stale_allowances()
check("no stale exemption", stale, [])
check("every exemption names its words rather than the whole file",
      sorted(k for k, v in spellcheck.ALLOW.items() if "*" in v),
      ["modules/scans/reports.py", "test_spelling.py",
       "tools/spellcheck.py"])


# --------------------------------------------------- 3b. and it reads TypeScript
section("TypeScript is read the way Python is: literals, not code")

# The one module that is not Python renders a page a CLIENT reads out of
# TypeScript, and was outside this check entirely -- the proof footer said
# "Colours may vary slightly" and nothing here could see it. What made it safe
# to add is the rule the Python side already follows: string literals only. A
# full-text pass over ten thousand lines of renderer would report `rasterise`,
# `normalise`, `optimise` and sharp's own `colours` option, which is a rename
# of the module dressed up as a copy change.

SAMPLE = "\n".join([
    "// A comment about the brand colour, for whoever opens the file.",
    "/* And a block one about behaviour. */",
    "import { optimise } from './images';",
    "const focal = 'centre';",
    "const key = { colours: 4, dither: 0.8 };",
    "function normalise(x) { return rasterise(x); }",
    "const shown = 'Pick the brand colour you want.';",
    "const many = `",
    "  a sentence about behaviour here",
    "`;",
    "const interp = `${step.colours} colors`;",
    "const pattern = /colour|centre/;",
])


def ts_words(src, rel="some/file.ts"):
    """What the TypeScript path would report, word by word."""
    out = []
    for offset, body in spellcheck._ts_strings(src):
        if spellcheck._IDENTIFIER.match(body.strip()):
            continue
        base = src.count("\n", 0, offset) + 1
        out.extend(spellcheck.findings_in(body, rel, base))
    return out


hits = ts_words(SAMPLE)
got = sorted(w.lower() for _, w in hits)

check("a sentence in a literal is reported", "colour" in got)
check("...and one inside a template literal", "behaviour" in got)
check("nothing else is", got, ["behaviour", "colour"])
# Each of those four is a way a full-text pass turns a copy check into a
# refactor of somebody else's module.
check("a line comment is not copy", got.count("colour") == 1)
check("an imported function is not copy", "optimise" not in got)
check("a declared function is not copy", "normalise" not in got)
check("an object key is not copy", "colours" not in got)
check("a stored value is not copy", "centre" not in got)
check("a regex pattern is not copy", "centre" not in got)

# The line reported is the WORD's, not the line the literal opens on. proof.ts
# draws its whole page from one 400-line template literal, so a report that put
# every finding on the backtick is one nobody can act on.
multi = "const x = `\nalpha\nthe brand colour\n`;\n"
check("the line is the word's, not the literal's",
      [ln for ln, _ in ts_words(multi)], [3])

# And .ts is in the sweep rather than merely supported: a reader that nothing
# calls is the failure test_unwired.py exists for.
check("scan() reads .ts",
      'rglob("*.ts")' in Path(spellcheck.__file__).read_text())
check("a .d.ts is left out, being declarations rather than copy",
      '.d.ts' in Path(spellcheck.__file__).read_text())


# ------------------------------------------------------------ 4. and it is green
section("The Hub's own copy")

found = spellcheck.scan()
detail = "; ".join(f"{f}:{ln}: {w}"
                   for f, hits in sorted(found.items()) for ln, w in hits)
check("no British spellings in anything a person reads", detail, "")


print("\n" + "-" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
