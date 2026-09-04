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

# `help_dot('key')` in a Jinja template, `data-help="key"` on an element, and
# `help:"key"` on a screen the page's own renderer draws.
#
# All three are real. The reach panel on the Proposal Builder's areas step
# uses the second, so a scan for the first alone calls four live entries
# dead. And that same builder's fourteen wizard steps are JavaScript objects
# whose one renderer turns `help:"…"` into the `data-help` span -- a literal
# key in the template reached through a variable, which is neither a Jinja
# call nor an attribute and would otherwise read as thirteen registered keys
# nobody had placed. It is still a *literal*: the key is written in the file
# and can be resolved from it, unlike the runtime-assembled kind below.
_PATTERNS = (
    re.compile(r"help_dot\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"""data-help=["']([^"']+)["']"""),
    re.compile(r"""\bhelp:\s*["']([\w.]+)["']"""),
)

# A key with an interpolation in it is assembled while the page runs and
# cannot be resolved from the source.
_BUILT = ("${", "{{", "+")

# The other half of that, and it does not show up inside the captured key.
# A template literal puts the interpolation between the attribute's own
# quotes -- `data-help="${k}"` -- so `_BUILT` sees it. Plain JS concatenation
# puts it *outside* them:
#
#     '<span data-help="hub.prospect.'+esc(key)+'"></span>'
#
# and the pattern stops at that inner quote, capturing `hub.prospect.` -- a
# prefix with no `+` in it, reported as a key nobody registered. Which is the
# mistake `tools/linkcheck.py` refuses to make about a URL built by
# concatenation: it is not a dead bubble, it is a bubble this cannot resolve.
# The evidence is the character after the quote the match stopped at.
_JOINS = ("+",)

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


def _spellings(kind: str, name: str) -> tuple:
    """How a walkthrough's target would actually be written in markup.

    `demo_targets()` used to credit a target with a bare `name in everything`,
    which any file containing the word satisfies. `data-demo='unmatched'` read
    as anchored because the word "unmatched" appears in another tool's prose,
    and `data-demo='client-name'` because something, somewhere, has a class of
    that name — so twenty-two steps that drive nothing read as anchored, and
    two whole walkthroughs read as working while every driving step in them
    resolved to no element at all. The attribute has to be there, not the word.
    """
    # data-tour belongs here for the same reason data-demo does: `_needs()`
    # reads a `[data-tour='…']` selector and `_SEL_TOUR` was added for it, and
    # this map was not -- so every step anchored that way asked for no spelling
    # at all and `_found()` answered False on an empty tuple. Seven steps
    # across the two Smart 1 Ads walkthroughs read as driving nothing while
    # their hooks sat in the templates, which is the false positive that gets a
    # check switched off, and it takes the real findings with it.
    attr = {"data-demo": "data-demo", "data-tour": "data-tour",
            "id": "id", "name": "name"}.get(kind)
    if not attr:
        return ()
    return (f'{attr}="{name}"', f"{attr}='{name}'")


def _module_of(path: str, base: str) -> str:
    """Which tool a file belongs to, for the `elsewhere` reading.

    `modules/<name>/...` is that module; anything under `hub/` is the hub
    itself, which is the module name the hub's own scenarios carry. Anything
    else belongs to no tool and is left out rather than guessed at.
    """
    rel = os.path.relpath(path, base).replace(os.sep, "/")
    if rel.startswith("modules/"):
        parts = rel.split("/")
        return parts[1] if len(parts) > 2 else ""
    if rel.startswith("hub/"):
        return "hub"
    return ""


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
                built = (any(t in key for t in _BUILT)
                         or src[m.end():m.end() + 1] in _JOINS)
                bucket = runtime if built else literal
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


# ---------------------------------------------------------------- the demo
# The third layer, and the one that fails hardest. `hub/demos.py` drives a
# tool's real screen -- filling its real fields, clicking its real buttons --
# and every step names the element to act on. A step whose element is not
# there used to hide the ring and, on "Do it for me", return without doing or
# saying anything: the learner presses a button that promises to fill a field
# in, and nothing happens. CLAUDE.md names that failure for Smart 1 Ads'
# scenario; it is the norm rather than the exception, and nothing measured it.
#
# `hub-demo.js` says so on the step now. This is the other half: the same
# question asked of the whole book at once, so the scenarios written against
# a screen that has since been rebuilt are a list somebody can work down
# rather than something a learner discovers one step at a time.

# What a selector needs to exist. Only the shapes demos.py actually uses:
# an id, a [data-demo='…'] hook, a [name='…'] field. A speculative pattern
# costs nothing to write and a great deal to police -- hub/config.py's
# ALIASES rule, on a different shelf.
_SEL_ID = re.compile(r"#([A-Za-z0-9_-]+)")
_SEL_DATA = re.compile(r"""\[data-demo=["']([^"']+)["']\]""")
_SEL_NAME = re.compile(r"""\[name=["']([^"']+)["']\]""")
# A tour step is anchored by `data-tour`, and a walkthrough step may be too --
# `hub/help.py`'s steps use it and seven of Smart 1 Ads' driving steps name one.
# Left out, 39 anchors in this repo were tested by nothing: renaming one out
# from under the step that drives it changed no count anywhere.
_SEL_TOUR = re.compile(r"""\[data-tour=["']([^"']+)["']\]""")

# A hook can be **derived** rather than typed, exactly as a help key can. The
# QA index writes `data-demo="qa-report-{{ key }}"` once for every report it
# lists, so a scenario naming a report added next month is anchored without
# that template being edited again -- the reason `card()` on the prospect
# record takes one key rather than nine call sites doing it.
#
# A plain substring search then finds no `qa-report-ghl-billing-no-products`
# anywhere and calls the step dead, which is the guess `tools/linkcheck.py`
# refuses to make about a URL built by concatenation, and which this module
# already refuses to make about a help key. What is knowable from the source
# is the **literal prefix** in front of the interpolation, so that is what is
# collected, and a target starting with one reads as built at runtime rather
# than as missing.
_DEMO_BUILT = re.compile(
    r"""data-demo=["']([A-Za-z0-9_-]{3,})(?:\{\{|\{%|\$\{|["']\s*\+)""")


def _runtime_demo_prefixes(sources: str) -> list[str]:
    """Literal prefixes of every `data-demo` assembled while the page runs.

    At least three characters, because a bare `data-demo="{{ x }}"` names no
    prefix at all and one that matched everything would switch the check off
    -- the failure `hub/config.py`'s ALIASES rule describes, wearing a hook.
    """
    return sorted(set(_DEMO_BUILT.findall(sources)))


def _needs(selector: str) -> list[tuple[str, str]]:
    out = []
    for m in _SEL_ID.finditer(selector or ""):
        out.append(("id", m.group(1)))
    for m in _SEL_DATA.finditer(selector or ""):
        out.append(("data-demo", m.group(1)))
    for m in _SEL_NAME.finditer(selector or ""):
        out.append(("name", m.group(1)))
    for m in _SEL_TOUR.finditer(selector or ""):
        out.append(("data-tour", m.group(1)))
    return out


def demo_targets(root: str | None = None) -> dict:
    """Every walkthrough step, and whether its target exists anywhere.

    Deliberately **anywhere** rather than on the scenario's own page. A
    walkthrough drives a screen whose markup half a dozen scripts write, so
    tying a target to one template would report a hook that is drawn at
    runtime as missing -- the guess `tools/linkcheck.py` refuses to make about
    a URL built by concatenation. A target that appears in no file at all is
    missing beyond argument; one that appears somewhere is *not verified*,
    and this says which it is rather than implying it surveyed the pages.
    """
    from . import demos

    base = root or _root()
    blob = []
    for path in _sources(base):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                blob.append(fh.read())
        except OSError:
            continue
    everything = "\n".join(blob)

    # The same text again, per module, so a target can be told from one that
    # exists only in a different tool. See `elsewhere` below.
    per_module: dict = {}
    for path in _sources(base):
        mod = _module_of(path, base)
        if not mod:
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                per_module.setdefault(mod, []).append(fh.read())
        except OSError:
            continue
    per_module = {k: "\n".join(v) for k, v in per_module.items()}

    built = _runtime_demo_prefixes(everything)

    def _found(kind: str, name: str) -> bool:
        if any(sp in everything for sp in _spellings(kind, name)):
            return True
        if kind == "data-demo" and any(name.startswith(p) for p in built):
            on_prefix.add(name)
            return True
        return False

    # Accepted on a prefix rather than found whole. Named rather than folded
    # into the anchored count, because "we found this hook" and "a template
    # builds hooks that start this way" are different claims and only the
    # first was actually verified.
    on_prefix: set = set()

    rows, steps, missing, untestable = [], 0, 0, []
    strays: list = []
    for scenario in demos.SCENARIOS:
        gone, vague, testable = [], [], 0
        here = per_module.get(getattr(scenario, "module", ""), "")
        for i, step in enumerate(getattr(scenario, "steps", []), 1):
            selector = getattr(step, "selector", "") or ""
            if not selector:
                continue
            steps += 1
            wants = _needs(selector)
            # A selector carrying nothing that identifies an element -- an
            # `input[type='file']` -- is not a step this check has verified;
            # it is one it cannot speak to. Counted as anchored, it was a tick
            # over a question nobody asked, and it is the reason
            # `client360.proposal` cleared the floor below while driving
            # nothing: three dead hooks and one selector that matches a file
            # input on any page in the Hub.
            if not wants:
                vague.append({"step": i, "title": getattr(step, "title", ""),
                              "selector": selector})
                untestable.append(f"{getattr(scenario, 'key', '')}: {selector}")
                continue
            testable += 1
            absent = [f"{kind} {name!r}" for kind, name in wants
                      if not _found(kind, name)]
            if absent:
                missing += 1
                gone.append({"step": i, "title": getattr(step, "title", ""),
                             "selector": selector, "absent": absent})
            elif here and not any(
                    sp in here for kind, name in wants
                    for sp in _spellings(kind, name)):
                # A selector naming nothing this can look for never reaches
                # here -- the untestable branch above takes it, which is what
                # the `and wants` guard this used to carry was for. Reaching
                # this branch means `wants` is non-empty, so `not any([])`
                # cannot fire and report an unlookable selector as anchored in
                # the wrong place.
                #
                # Found, but nowhere in the tool the walkthrough drives. The
                # "anywhere" reading above is deliberate — a screen's markup is
                # written by half a dozen scripts and tying a target to one
                # template would report a runtime-drawn hook as missing — but
                # "anywhere" also credits a step whose only match is in a
                # different tool, and that step drives nothing when the
                # walkthrough runs. It is not counted as missing, because the
                # element may still be drawn here at runtime; it is named, the
                # way a target accepted on a prefix is.
                strays.append({"scenario": getattr(scenario, "key", ""),
                               "module": getattr(scenario, "module", ""),
                               "step": i, "title": getattr(step, "title", ""),
                               "selector": selector})
        rows.append({
            "key": getattr(scenario, "key", ""),
            "module": getattr(scenario, "module", ""),
            "title": getattr(scenario, "title", ""),
            "steps": len(getattr(scenario, "steps", [])),
            "unanchored": gone,
            "untestable": vague,
            # A scenario every one of whose driving steps is missing does not
            # drive anything at all -- a different thing from one with a step
            # or two out of date, and the only one worth retiring rather than
            # repairing. Measured against the steps this check can actually
            # speak to: a selector it cannot test is not evidence that the
            # scenario drives something, so it neither clears this floor nor
            # -- where every step is one -- asserts the scenario is dead.
            "dead": bool(gone) and len(gone) == testable,
        })
    return {
        "scenarios": len(rows),
        "steps": steps,
        "unanchored": missing,
        "rows": [r for r in rows if r["unanchored"] or r["untestable"]],
        # "nothing missing" and "nothing we could look at" must not render
        # alike, so a scenario carrying an untestable step is not called clean.
        "clean": [r["key"] for r in rows
                  if not r["unanchored"] and not r["untestable"]],
        "untestable": sorted(untestable),
        "runtime": sorted(on_prefix),
        "runtime_prefixes": built,
        # Anchored, but not in the module the scenario drives.
        "elsewhere": strays,
        "measured": True,
    }
