"""The Client 360 record's JavaScript, as files.

`hub/templates/client360.html` carried the whole record's script inline --
5,900 lines, of which only `render()` (the card markup, with its help dots)
needs Jinja. Everything else is plain JavaScript and now lives in
`hub/static/client360-*.js`, one file per feature, so node can lint each
one and a test can read the function it drives from a file rather than
slicing it out of a template by comment markers.

This module is the one list of those files, in load order, and the
cache-buster the template stamps on each `<script src>`: a short hash of
their contents, so a deploy that changes any module changes the URL and no
browser serves the previous record against the new server. The template's
own inline script (run, pick, render and the Jinja constants) loads after
every module and calls into them; nothing here is called at load time.

`source_text()` is for the tests: the template plus every module, in
order, so a check that asks "does the record do X" reads the whole record
and not the third of it that happens to be inline.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache

MODULES = (
    "client360-core.js",
    "client360-cards.js",
    "client360-people.js",
    "client360-audience.js",
    "client360-skills.js",
    "client360-header.js",
    "client360-performance.js",
)

_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(_HERE, "templates", "client360.html")


def module_path(name: str) -> str:
    return os.path.join(_HERE, "static", name)


def module_paths() -> list[str]:
    return [module_path(n) for n in MODULES]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@lru_cache(maxsize=1)
def version() -> str:
    """A short content hash of every module, for the cache-buster.

    Cached for the process: the files change only with a deploy, and a deploy
    is a new process. Never raises -- a module that cannot be read stamps a
    constant so the page still renders and the missing file is a 404 the
    browser reports, not a 500 on the record.
    """
    h = hashlib.sha1()
    try:
        for path in module_paths():
            with open(path, "rb") as fh:
                h.update(fh.read())
    except OSError:
        return "c360"
    return h.hexdigest()[:10]


def source_text() -> str:
    """The template plus every module, in load order -- the whole record."""
    parts = [_read(TEMPLATE)]
    for path in module_paths():
        parts.append("\n/* ==== %s ==== */\n" % os.path.basename(path))
        parts.append(_read(path))
    return "".join(parts)
