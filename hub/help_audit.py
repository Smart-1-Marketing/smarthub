"""Which help bubbles resolve, and which of them explain nothing.

## The failure this reports

`hub/help.py` is the registry and `hub-help.js` is what draws it. A template
asks for a bubble by key -- `{{ help_dot('social.planner') }}` in Jinja, or
`data-help="…"` on an element a script writes -- and a key the registry does
not hold is **removed client-side** rather than left as a dead "?". That is
the right thing to do on the page and it is exactly what makes the mistake
invisible: the template reads as helped, the screen shows nothing, no console
error, no failed request, and nothing anywhere reports it.

`hub/help.py`'s own note says a bubble whose key is missing "reads as helped
from the template and shows nothing on the page", and `test_ads_explainer.py`
asserts it for Smart 1 Ads alone. Three other tools had placed a bubble on
their own title -- Website Blocks, the Social Content Planner and Video
Search -- with no entry behind any of them. Video Search's template even
carries a comment saying the key must not be renamed *because renaming would
orphan the bubble*, protecting a key that pointed at nothing.

## Three answers, because three things are true of a key

**Placed and registered** is the ordinary case and is not reported.

**Placed and not registered** is the finding: somebody wrote the bubble and
the help text was never written, or the key was typed wrong. One of the two,
and both show the reader nothing.

**Registered and never placed** is deliberately *not* a finding on its own. A
tour step needs no bubble -- it is anchored by a selector, not by a
`help_dot` -- and a key can be **built at runtime**: the Proposal Builder's
reach panel writes ``data-help="sales_builder.areas.${key}"`` from a loop, so
its four entries are placed by a string this cannot resolve. Calling those
dead would be the mistake `tools/linkcheck.py` already refuses to make about
a URL built by concatenation: they are *listed* as built at runtime and not
verified, rather than guessed at in either direction.
"""
from __future__ import annotations

import os
import re

# `help_dot('key')` in a Jinja template, and `data-help="key"` on an element.
# Both are real: the reach panel on the Proposal Builder's areas step uses the
# second, so a scan for the first alone calls four live entries dead.
_PATTERNS = (
    re.compile(r"help_dot\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"""data-help=["']([^"']+)["']"""),
)

# A key with an interpolation in it is assembled while the page runs and
# cannot be resolved from the source.
_BUILT = ("${", "{{", "+")

_EXTS = (".html", ".js")


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources(root: str | None = None):
    base = root or _root()
    for folder in ("hub", "modules"):
        top = os.path.join(base, folder)
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames
                           if d not in ("_attic", "node_modules", ".git")]
            for name in filenames:
                if name.endswith(_EXTS):
                    yield os.path.join(dirpath, name)


def placements(root: str | None = None) -> tuple[dict, dict]:
    """(literal key -> files, runtime-built key -> files).

    Never raises: a file that cannot be read costs its own keys and not the
    audit, because a check that falls over is one nobody runs.
    """
    base = root or _root()
    literal: dict[str, set] = {}
    runtime: dict[str, set] = {}
    for path in _sources(base):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(path, base)
        for rx in _PATTERNS:
            for m in rx.finditer(src):
                key = m.group(1)
                bucket = runtime if any(t in key for t in _BUILT) else literal
                bucket.setdefault(key, set()).add(rel)
    return literal, runtime


def audit(root: str | None = None) -> dict:
    """What resolves, what does not, and what could not be checked."""
    from . import help as help_registry

    known = {h.key: h for h in help_registry.REGISTRY}
    literal, runtime = placements(root)

    # The prefix a runtime-built key can reach: everything before the first
    # interpolation. `sales_builder.areas.${key}` covers `sales_builder.areas.`.
    prefixes = set()
    for key in runtime:
        head = re.split(r"\$\{|\{\{|\+", key)[0].strip()
        if head:
            prefixes.add(head)

    missing = sorted(set(literal) - set(known))
    placed_or_covered = set(literal) | {
        k for k in known if any(k.startswith(p) for p in prefixes)}
    unplaced = sorted(
        k for k, h in known.items()
        if k not in placed_or_covered and h.step is None)

    return {
        "registry": len(known),
        "placed": len(literal),
        # The finding: a bubble the reader is shown nothing for.
        "missing": [{"key": k, "files": sorted(literal[k])} for k in missing],
        # Not a finding. Named rather than resolved, the way linkcheck names a
        # URL built by concatenation.
        "runtime": [{"key": k, "files": sorted(runtime[k])}
                    for k in sorted(runtime)],
        "runtime_covers": sorted(
            k for k in known if any(k.startswith(p) for p in prefixes)),
        # Also not a finding on its own — see the module docstring.
        "unplaced": unplaced,
        "measured": True,
    }


def check_dead_bubbles(root: str | None = None) -> list[dict]:
    """/api/integrity's reading: one row per bubble that explains nothing."""
    try:
        data = audit(root)
    except Exception as exc:                            # noqa: BLE001
        return [{"file": "-", "module": "help",
                 "detail": f"Check failed: {type(exc).__name__}", "fix": ""}]
    out = []
    for row in data["missing"]:
        for rel in row["files"]:
            out.append({
                "file": rel,
                "module": row["key"].split(".")[0],
                "detail": (f"places help_dot({row['key']!r}), which is not in "
                           "hub/help.py — the bubble is removed client-side, "
                           "so the template reads as helped and the screen "
                           "shows nothing"),
                "fix": (f"Add {row['key']!r} to hub/help.REGISTRY, or correct "
                        "the key to one that is there."),
            })
    return out
