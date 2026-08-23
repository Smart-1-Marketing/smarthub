"""Run the JS balance check over the inline <script> blocks in HTML.

Most of this app's front end lives inside Jinja templates rather than .js
files, so checking only hub/static and modules/*/static misses the majority of
it. Jinja expressions are blanked rather than removed so that line numbers
still line up with the file you have to open.

    python tools/checktemplates.py
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from jscheck import check

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
