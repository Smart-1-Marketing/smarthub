"""Integration hygiene — the audit that stops old bugs coming back.

Consolidating twenty modules onto the shared services is a large change with,
right now, no bug at the end of it: every image path already caps the longest
edge before converting, and no PDF is uploaded with an image resource type. So
rewriting working code would be risk without payoff.

What is worth doing is making the invariants *checkable*, because every one of
these was a real, shipped defect at some point and each came back the moment a
new module was written without knowing about it:

  * **PDF uploaded as an image type** — Cloudinary accepts it and then refuses
    to deliver it, so the upload succeeds, no fallback fires, and the
    customer's download link 403s.
  * **Converting without resizing** — WebP alone leaves a 6000px camera photo
    at 6000px. The cap has to come first.
  * **Modules that never write to the activity log** — Scans called a function
    that did not exist, inside a bare except, and no scan reached /activity for
    the life of the module.
  * **OpenAI called outside the shared client** — spend that never appears in
    the cost estimate, so the number quietly understates the bill.
  * **Unclamped list limits** — `?limit=-1` was a 500 on Postgres and a full
    table dump on SQLite.
  * **JSON written to the disk with no copy in the database** — the disk is
    not in the database backup and comes back empty if it is recreated, and a
    module reading an empty file looks like a module with nothing in it.

Read-only and cheap: it reads source, never runs it, and touches no API.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re

# The bubble audit lives in its own module rather than here: it is read by
# /api/integrity and by test_help_layer.py, and two copies of "which keys
# resolve" is the drift a second reader always becomes.
from . import help_audit as _help_audit
# The work-log table and the two checks over it. Beside the audit above for
# the same reason: one reading of "which module names count", read by
# /api/integrity and by test_client_images.py.
from . import client_brand as _client_brand
# The client-brief AI-caller sweep: which files reach OpenAI without going
# through hub.ai, so the client brief is never injected and no usage row is
# written. Beside client_brand for the same reason -- one reading, read by
# /api/integrity and by test_ai_callers.py.
from . import client_brief as _client_brief

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Third-party code is not ours to fix, and a local virtualenv sits inside the
# repo — without these, the scan reports the openai package itself for "spend
# not recorded" and buries the findings that are actually actionable.
SKIP_DIRS = {"_attic", "__pycache__", ".git", "node_modules",
             ".venv", "venv", "env", "site-packages", ".tox", "build", "dist"}
# Files that legitimately mention these patterns without doing the thing.
SELF = {"hub/integrity.py", "hub/storage.py", "hub/images.py", "hub/ai.py",
        "hub/quotas.py", "hub/diagnostics.py", "hub/demo.py", "hub/demos.py"}


def _sources():
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SELF:
            continue
        try:
            yield rel, p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def _module_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "modules" and len(parts) > 1:
        return parts[1]
    return parts[0] if parts[0] != "hub" else rel


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_pdf_resource_type() -> list[dict]:
    """A PDF or DOC uploaded as resource_type="image" cannot be delivered."""
    out = []
    for rel, src in _sources():
        for m in re.finditer(r'resource_type\s*=\s*"image"', src):
            window = src[max(0, m.start() - 700):m.start() + 200].lower()
            if any(k in window for k in (".pdf", "'pdf'", '"pdf"', "docx", "proposal")):
                out.append({
                    "file": rel, "module": _module_of(rel),
                    "detail": "An upload near a PDF/DOC path uses "
                              'resource_type="image". Cloudinary will accept it '
                              "and then refuse to deliver it — the link 403s.",
                    "fix": 'Use "raw", or route it through hub.storage.put(), '
                           "which derives the type from the file.",
                })
    return out


def check_convert_without_resize() -> list[dict]:
    """Converting to WebP without capping the longest edge shrinks nothing."""
    out = []
    for rel, src in _sources():
        # Only a real Pillow save counts. `format="webp"` as a Cloudinary
        # upload kwarg is a delivery instruction, not a local conversion, and
        # flagging it produced a false positive on page_image_optimizer.
        converts = re.search(r'\.save\(\s*\w+\s*,\s*["\']WEBP["\']', src, re.I)
        if not converts:
            continue
        # An export/download path renders at the size the user explicitly
        # chose (1x/2x/3x), so capping it would override their choice. That is
        # correct behaviour, not a missing resize — image_creator's export was
        # the other false positive.
        if re.search(r"as_attachment|send_file|attachment;|Content-Disposition", src, re.I):
            continue
        caps = re.search(r"\.thumbnail\(|max_edge|MAX_EDGE|\.resize\(|LANCZOS"
                         r"|c_limit|crop.*limit", src, re.I)
        if not caps:
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": "Converts to WebP but never caps the longest edge. "
                          "A 6000px camera photo stays 6000px and stays huge.",
                "fix": "Cap the edge first — hub.images.optimise() does it in "
                       "the right order, with EXIF rotation applied before the cap.",
            })
    return out


def check_untracked_openai() -> list[dict]:
    """OpenAI calls that bypass hub.ai, so their spend never reaches /diagnostics."""
    out = []
    markers = ("/v1/chat/completions", "/v1/responses", "/v1/images/generations",
               "chat.completions.create", "responses.create")
    for rel, src in _sources():
        if not any(m in src for m in markers):
            continue
        if ("hub.ai" in src or "from hub import ai" in src
                or "from . import ai" in src):
            continue
        out.append({
            "file": rel, "module": _module_of(rel),
            "detail": "Calls OpenAI without recording usage, so this spend is "
                      "invisible in the cost estimate.",
            "fix": "Add hub.ai.note_usage() (raw HTTP) or note_sdk_usage() "
                   "(OpenAI SDK) after the response — one line, no logic change.",
        })
    return out


def check_untracked_provider_usage() -> list[dict]:
    """ElevenLabs, Cloudinary or Google called without recording the usage.

    The same failure as check_untracked_openai above, for the three providers
    added later. A call site that spends an allowance without recording it
    does not make the usage page wrong by a little — it makes it wrong by
    however much that call site spends, silently, and in the reassuring
    direction.

    The detection lives in hub/quotas.py beside the markers it is looking for,
    so this and the "blind spots" list on /diagnostics cannot drift apart.
    """
    try:
        from hub.quotas import untracked_provider_calls
    except Exception as exc:                            # noqa: BLE001
        return [{"file": "hub/quotas.py", "module": "hub",
                 "detail": f"Usage tracking could not be read "
                           f"({type(exc).__name__}), so this check did not run.",
                 "fix": "Fix the import error in hub/quotas.py."}]
    out = []
    for provider, rows in untracked_provider_calls(force=True).items():
        for row in rows:
            out.append(dict(row, provider=provider))
    return out


def _calls_the_logger(src: str) -> bool:
    """Does this source actually *call* the activity logger?

    The AST, not the text, and a call rather than an import -- the two ways
    this went wrong in opposite directions.

    It read `"for_module(" in src` before, which is satisfied by binding the
    logger and never using it. Seven modules did exactly that: imported it,
    assigned it, wrapped it in a no-op fallback, wrote a comment above the
    import explaining why attribution mattered, and called it nowhere. The
    check reported all seven as modules that log.

    Reading the text the other way is the mistake hub/config.py's drift check
    and hub/image_audit.py's producer check each name: several files here
    explain this very trap in prose, so a substring match reports the
    explanation of the fix as the defect.

    Two shapes count, because both are in use:
      audit.log("mod", "thing")          -- the direct call
      log = audit.for_module("mod"); log("thing")   -- the bound logger
    A file that only binds counts for nothing; a file that only calls a name
    bound in *another* file of the same module still counts, because the
    module is what is being asked about.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False

    bound: set[str] = set()
    direct = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value.id if isinstance(func.value, ast.Name) else ""
        if func.attr == "log" and "audit" in owner.lower():
            direct = True
        elif func.attr == "for_module":
            for parent in ast.walk(tree):
                if isinstance(parent, ast.Assign) and parent.value is node:
                    bound.update(t.id for t in parent.targets
                                 if isinstance(t, ast.Name))
    if direct:
        return True

    # A bound logger only counts once something calls it. Names bound in this
    # file are checked here; a module whose binding and call sit in different
    # files is covered because seen[mod] is an OR across the module's files.
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    if bound & called:
        return True
    # A call to a conventionally-named module logger whose binding is in a
    # sibling file (modules/scans and modules/msa are shaped that way).
    return bool(called & {"_audit", "_log", "_cb_log"})


def check_silent_modules() -> list[dict]:
    """A module that never writes to the activity log is unauditable.

    The question is whether the module's work is attributable, not whether a
    string appears inside its own folder — and those came apart on the one
    module that is not Python. `modules/ad_builder` is a TypeScript renderer
    whose Hub-side half lives in hub/ad_builder_link.py and
    hub/ad_builder_proxy.py and files everything under "display_ads"; the check
    looked in the directory, found one maintenance script, and reported a
    module that logs as a module that does not. A finding nobody can act on
    without renaming a log name is a finding people learn to scroll past.

    So the name a module logs under comes from hub/audit.LOG_NAMES, and the
    search covers hub/ as well as the module's own files.
    """
    try:
        from .audit import LOG_NAMES, NO_ACTIVITY
    except Exception:                               # noqa: BLE001
        LOG_NAMES, NO_ACTIVITY = {}, {}

    out = []
    seen: dict[str, bool] = {}
    everything = list(_sources())
    for rel, src in everything:
        if not rel.startswith("modules/"):
            continue
        mod = _module_of(rel)
        if mod.endswith(".py"):        # modules/__init__.py is not a module
            continue
        seen[mod] = seen.get(mod, False) or _calls_the_logger(src)

    # A module whose logging is written elsewhere — declared, and then actually
    # looked for, so a declaration alone cannot silence this.
    for mod in [m for m, logs in seen.items() if not logs]:
        name = LOG_NAMES.get(mod)
        if not name:
            continue
        needles = (f'audit.log("{name}"', f"audit.log('{name}'",
                   f'for_module("{name}")', f"for_module('{name}')")
        seen[mod] = any(n in src for rel, src in everything
                        if rel.startswith("hub/") for n in needles)

    for mod, logs in sorted(seen.items()):
        if logs or mod in NO_ACTIVITY:
            continue
        out.append({
            "file": f"modules/{mod}/", "module": mod,
            "detail": "Never writes to the activity log, so nothing this "
                      "module does is attributable.",
            "fix": "log = audit.for_module(\"" + mod + "\") and call it on "
                   "the actions that matter. Binding it is not enough -- this "
                   "check reads a call. If it logs under another name or from "
                   "outside its own directory, declare that in hub/audit.py's "
                   "LOG_NAMES; if it genuinely has nothing to log, declare it "
                   "in NO_ACTIVITY with the reason.",
        })

    # An exemption that outlives what it exempted goes on covering whatever is
    # written at that path next -- check_stale_json_exemptions()'s rule, and
    # the reason NO_ACTIVITY is a table rather than a habit.
    # A module directory can be real and hold no .py file at all --
    # modules/hf_render_service is a Node/TypeScript render backend, and
    # `seen` above is built only from `_sources()`'s `*.py` glob. Reading
    # `set(seen)` alone as "the modules that still exist" reported that one
    # as stale the moment it was declared: a real, on-disk directory read as
    # gone because nothing in it is Python. A directory is live if it is
    # there, whatever language it is written in.
    live = set(seen)
    modules_dir = ROOT / "modules"
    if modules_dir.is_dir():
        live.update(p.name for p in modules_dir.iterdir()
                    if p.is_dir() and p.name not in SKIP_DIRS)
    for mod in sorted(NO_ACTIVITY):
        if mod in live:
            continue
        out.append({
            "file": "hub/audit.py", "module": mod,
            "detail": f"NO_ACTIVITY exempts {mod!r} from the activity log and "
                      f"there is no such module any more.",
            "fix": f"Drop {mod!r} from hub/audit.NO_ACTIVITY. Left there it "
                   f"silently exempts whatever is written at that path next.",
        })
    return out


def check_write_route_attribution() -> list[dict]:
    """A module that has triaged its writes, and one that has slipped since.

    check_silent_modules() asks whether a module logs **at all**, and one call
    site satisfies it — so a module can be loudly attributable about a quarter
    of its work and pass. `hub.audit.write_route_attribution()` is the same
    question asked one level finer, per write route rather than per module,
    and until now it was exercised only by `test_write_attribution.py`: a
    regression in one of the modules that already declared
    `HOUSEKEEPING_ROUTES`, or a fifth module built the same way, was invisible
    to this continuous sweep unless somebody remembered to run that script by
    hand.

    Scoped to modules that have opted in by declaring `HOUSEKEEPING_ROUTES` —
    never a repo-wide gate. The same walk over every module that logs finds
    about 229 silent write routes across 34 files, most of them genuinely
    housekeeping (autosaves, drafts, previews), and a check landing with 229
    findings nobody can act on is the one people learn to skip. A module that
    has not declared the table is not asked the finer question here at all —
    `stale_work_exemptions()`'s rule, one check up: declaring the table,
    however small, is what asks to be held to it.
    """
    try:
        from . import audit
    except Exception:                               # noqa: BLE001
        return []

    by_module: dict[str, list[tuple[str, str]]] = {}
    for rel, src in _sources():
        if not rel.startswith("modules/"):
            continue
        mod = _module_of(rel)
        if mod.endswith(".py"):        # modules/__init__.py is not a module
            continue
        by_module.setdefault(mod, []).append((rel, src))

    out = []
    for mod, files in sorted(by_module.items()):
        declared: dict[str, str] = {}
        logs: set[str] = set()
        silent: set[str] = set()
        for _rel, src in files:
            try:
                walk = audit.write_route_attribution(src)
            except SyntaxError:
                continue
            declared.update(walk["declared"])
            logs.update(walk["logs"])
            silent.update(walk["silent"])
        if not declared:
            continue  # not triaged — the backlog this check deliberately skips

        for fn in sorted(silent - declared.keys()):
            out.append({
                "file": f"modules/{mod}/", "module": mod,
                "detail": f"{fn}() writes and records nothing, and is not in "
                          f"{mod}'s own HOUSEKEEPING_ROUTES.",
                "fix": "Log who did it, or add the route to "
                       "HOUSEKEEPING_ROUTES with the reason it needs none.",
            })

        known = logs | silent
        for fn, reason in sorted(declared.items()):
            if not str(reason).strip():
                out.append({
                    "file": f"modules/{mod}/", "module": mod,
                    "detail": f"HOUSEKEEPING_ROUTES declares {fn!r} with no "
                              "reason.",
                    "fix": f"Say why {fn!r} needs no attribution, or drop it.",
                })
            elif fn not in known:
                out.append({
                    "file": f"modules/{mod}/", "module": mod,
                    "detail": f"HOUSEKEEPING_ROUTES declares {fn!r}, and no "
                              "such write route exists any more.",
                    "fix": f"Drop {fn!r} from HOUSEKEEPING_ROUTES — left "
                           "there it goes on exempting whatever is written "
                           "at that name next.",
                })
            elif fn in logs:
                out.append({
                    "file": f"modules/{mod}/", "module": mod,
                    "detail": f"HOUSEKEEPING_ROUTES declares {fn!r} as "
                              "housekeeping, and it now logs who did it.",
                    "fix": f"Drop {fn!r} from HOUSEKEEPING_ROUTES — the "
                           "declaration has outlived the reason it existed.",
                })
    return out


def _reads_request_value(node) -> bool:
    """Does this expression reach request.args / .values / .form?"""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute)
                and sub.attr in ("args", "values", "form")
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "request"):
            return True
    return False


def _enclosing_call(tree, node, names) -> bool:
    """Is `node` inside a call to one of `names` in the same file?"""
    for outer in ast.walk(tree):
        if (isinstance(outer, ast.Call) and isinstance(outer.func, ast.Name)
                and outer.func.id in names
                and any(x is node for x in ast.walk(outer))):
            return True
    return False


def check_unclamped_limits() -> list[dict]:
    """A caller's number parsed or bounded by hand.

    Read from the **AST**, and that is the fix rather than a tidy-up. The
    text version matched `request.args.get("limit")` and then skipped any
    window containing `min(`, `max(` or `clamp` — a guard against crying
    wolf that made it blind to two of the three faults `hub/webargs.py` was
    written to end:

    * `min(int(request.args.get("limit") or 12), 50)` contains `min(`, so it
      was skipped — and an upper bound with no lower one over a `[:limit]`
      is the *second* fault, the one that returns everything but the last
      five rows as a clean answer;
    * `max(1, min(1000, int(...)))` contains both, so it was skipped — and
      with no `try` around it, `?limit=abc` is the *first* fault, a 500.

    It also needed `hub/webargs.py` exempted by name, because that file's
    docstring quotes the bad pattern to explain it. Prose is not a call
    site, for the fifth time in this file, and the AST does not need telling.

    Two questions now, each narrow enough to have no false positives, and
    both empty on the day this went in:

    1. a bare `int()` over a caller's value **outside a try** — the 500;
    2. a `min()` over a caller's value with no `max()` or `clamp_int()`
       around it — the wrong answer.
    """
    out = []
    for rel, src in _sources():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if hasattr(sub, "lineno"):
                        guarded.add(sub.lineno)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if (node.func.id == "int" and node.args
                    and _reads_request_value(node.args[0])
                    and node.lineno not in guarded):
                out.append({
                    "file": rel, "module": _module_of(rel), "line": node.lineno,
                    "detail": "int() over a value the caller controls, outside "
                              "a try. ?limit=abc is a 500 and the traceback "
                              "names a line about pagination.",
                    "fix": "from hub.webargs import clamp_int — it never "
                           "raises and clamps both ends.",
                })
            elif (node.func.id == "min" and _reads_request_value(node)
                  and not _enclosing_call(tree, node, {"max", "clamp_int"})):
                out.append({
                    "file": rel, "module": _module_of(rel), "line": node.lineno,
                    "detail": "A ceiling on a caller's number and no floor. "
                              "?limit=-1 reaches rows[:-1], which returns "
                              "everything except the last row as a clean "
                              "answer with nothing saying anything was wrong.",
                    "fix": "from hub.webargs import clamp_int — it clamps "
                           "both ends. Do not add another local min()/max(); "
                           "that is how two files ended up with "
                           "max(1, max(1, min(...))).",
                })
    return out


def check_bare_except_pass() -> list[dict]:
    """`except: pass` with no logging — how the Scans audit bug hid for weeks."""
    out = []
    pattern = re.compile(r"except[^\n:]*:\s*\n\s*pass\b")
    for rel, src in _sources():
        hits = len(pattern.findall(src))
        if hits >= 6:
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": f"{hits} silent `except: pass` blocks. Scans called a "
                          "function that did not exist inside one of these, and "
                          "no scan reached the activity log for the module's "
                          "entire life.",
                "fix": "Log the exception, or narrow the except to what you "
                       "actually expect. Silence is only safe when the failure "
                       "genuinely does not matter.",
            })
    return out



# A `limit` a caller may pass to say "all of them". Written down here so the
# check and the code it reads agree about what uncapped looks like.
CAPPED_READ_EXEMPT = {
    # ("path.py", line-anchoring call name): why the bound is correct.
    ("modules/social_planner/ideas.py", "for_client"):
        "pending_count() passes limit=None, which is the uncapped path.",
}


def _capped_reads(trees: dict) -> dict:
    """``{module rel: {name: limit default}}`` for every function that takes a
    `limit` AND applies it. A parameter nothing slices with is not a cap."""
    out: dict = {}
    for rel, tree in trees.items():
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if "limit" not in [a.arg for a in n.args.args + n.args.kwonlyargs]:
                continue
            body = ast.unparse(n)
            if not (".limit(" in body or "[:limit]" in body or "[:max(" in body
                    or "[:clamp_int(" in body):
                continue
            args, default = n.args, "?"
            allargs = args.args + args.kwonlyargs
            defaults = ([None] * (len(args.args) - len(args.defaults))
                        + list(args.defaults) + list(args.kw_defaults))
            for a, d in zip(allargs, defaults):
                if a.arg == "limit":
                    default = d.value if isinstance(d, ast.Constant) else "?"
            out.setdefault(rel, {})[n.name] = default
    return out


def _module_rel(dotted: str, rel: str, trees: dict):
    """A dotted import to a repo-relative path, or None."""
    for c in (dotted.replace(".", "/") + ".py",
              dotted.replace(".", "/") + "/__init__.py"):
        if c in trees:
            return c
    tail = dotted.split(".")[-1]
    here = "/".join(rel.split("/")[:-1])
    for c in (f"{here}/{tail}.py", f"{here}/{tail}/__init__.py"):
        if c in trees:
            return c
    hits = [r for r in trees if r.endswith(f"/{tail}.py") or r == f"{tail}.py"]
    return hits[0] if len(hits) == 1 else None


def check_capped_read_misuse() -> list[dict]:
    """A bounded read that is COUNTED, or searched for one particular row.

    The defect class docs/claude/03 calls "a capped global read filtered in
    Python", found by hand four times across `modules/reports`, the Ads
    Builder and `hub/`. Every instance had the same shape and a different
    disguise -- a cap spent on the wrong noun, a cap behind a `* 10`
    multiplier, a cap the callee silently clamped tighter than the caller
    asked for, a cap under a check whose whole job was catching silence.

    Two questions, each narrow enough to have had no false positives once the
    names resolved, and both empty on the day this went in:

    1. **a count over a capped read** -- `len()` or `sum()` over a function
       that stops at `limit`. The count stops there too and goes on being
       printed as the total: the Ads Builder's Approval hub badge counted the
       open proposals among the newest 200 OF ANY STATUS, so an open draft
       older than that said the queue was empty while somebody waited on us.
    2. **a by-key search or index over a capped read** -- `next(... if ...)`
       or a dict comprehension keyed off one. Past the cap it answers "no such
       row" about a row that exists, and every caller reads that as a fact.

    **Resolved, not name-matched.** A first pass keyed on the bare function
    name and reported five findings that were two different functions sharing
    one name -- `hub/proposals.list_proposals(client)` takes no limit at all
    and collided with `ads_builder.store.list_proposals(limit=200)`. Prose is
    not a call site, for the sixth time in this file, and neither is a name.

    **`limit=None` is the uncapped path**, whether it is the default or
    written at the call site, because that is how a reader says "all of them"
    out loud. **`limit=floor + 1` against a `>= floor` comparison** is the
    bounded-threshold idiom and is correct: it stops at one more row than it
    needs to decide.
    """
    trees: dict = {}
    for rel, src in _sources():
        try:
            trees[rel] = ast.parse(src)
        except SyntaxError:
            continue
    return capped_read_findings(trees)


def capped_read_findings(trees: dict) -> list[dict]:
    """The findings for a ``{rel: tree}`` map. Split out from the check so it
    can be driven over a handful of synthetic modules -- a sweep that cannot
    be shown to find one is a sweep asserting about nothing."""
    capped = _capped_reads(trees)
    # A test that counts a capped read is ASSERTING the cap, which is the
    # opposite of this defect -- test_reports_map_reads.py reproduces every
    # one of these on purpose rather than asserting it from the source. Read
    # them for the definitions, never report them as call sites.
    trees = {rel: t for rel, t in trees.items()
             if not rel.split("/")[-1].startswith("test_")}
    if not capped:
        return []

    out = []
    for rel, tree in trees.items():
        direct, aliases = {}, {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                m = _module_rel(n.module, rel, trees)
                for a in n.names:
                    if m:
                        direct[a.asname or a.name] = m
                    aliases[a.asname or a.name] = _module_rel(
                        f"{n.module}.{a.name}", rel, trees) or m
            elif isinstance(n, ast.ImportFrom) and n.level:
                here = "/".join(rel.split("/")[:-1]).replace("/", ".")
                for a in n.names:
                    base = f"{n.module}.{a.name}" if n.module else a.name
                    aliases[a.asname or a.name] = _module_rel(
                        f"{here}.{base}", rel, trees)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    aliases[a.asname or a.name.split(".")[0]] = _module_rel(
                        a.name, rel, trees)

        def target(call):
            f = call.func
            if isinstance(f, ast.Name):
                for where in (rel, direct.get(f.id)):
                    if where and f.id in capped.get(where, {}):
                        return where, f.id, capped[where][f.id]
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                where = aliases.get(f.value.id)
                if where and f.attr in capped.get(where, {}):
                    return where, f.attr, capped[where][f.attr]
            return None

        def note(call, lineno, shape, fix):
            hit = target(call)
            if not hit:
                return
            where, name, default = hit
            if (rel, name) in CAPPED_READ_EXEMPT or (where, name) in CAPPED_READ_EXEMPT:
                return
            passed = None
            for kw in call.keywords:
                if kw.arg == "limit":
                    passed = kw.value
            if isinstance(passed, ast.Constant) and passed.value is None:
                return                              # the uncapped path, said out loud
            if passed is None and default is None:
                return                              # uncapped by default
            if isinstance(passed, ast.BinOp) and isinstance(passed.op, ast.Add):
                return                              # the floor + 1 threshold idiom
            out.append({
                "file": rel, "line": lineno,
                "detail": f"{shape} {name}() stops at a limit "
                          f"(defined in {where}), so this answer stops there too "
                          f"and reads as though nothing more exists.",
                "fix": fix,
            })

        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id in ("len", "sum") and n.args:
                    a = n.args[0]
                    if isinstance(a, ast.Call):
                        note(a, n.lineno, "This counts", COUNT_FIX)
                    if isinstance(a, (ast.GeneratorExp, ast.ListComp)):
                        for g in a.generators:
                            if isinstance(g.iter, ast.Call):
                                note(g.iter, n.lineno, "This counts", COUNT_FIX)
                if (n.func.id == "next" and n.args
                        and isinstance(n.args[0], (ast.GeneratorExp, ast.ListComp))):
                    for g in n.args[0].generators:
                        if isinstance(g.iter, ast.Call) and g.ifs:
                            note(g.iter, n.lineno, "This searches", KEY_FIX)
            if isinstance(n, ast.DictComp):
                for g in n.generators:
                    if isinstance(g.iter, ast.Call):
                        note(g.iter, n.lineno, "This indexes", KEY_FIX)
    return out


COUNT_FIX = ("Count it in the store -- a COUNT in SQL, or a read that takes "
             "limit=None. modules/reports/store.budget_line_count() and "
             "ads_builder.store.open_proposal_count() are the worked examples.")
KEY_FIX = ("Look it up by its key in the store rather than sweeping a page of "
           "rows. modules/reports/store.campaign_map() and "
           "ads_builder.store.deployed_account() are the worked examples.")


# A `.first()` whose filter cannot name one row. Written down here so the
# check and the code it reads agree about what has been looked at and settled.
UNORDERED_FIRST_EXEMPT: dict[tuple, str] = {
    # ("path.py", line-anchoring model): why an arbitrary row is the answer.
}

_MODEL_BASES = {"Model", "Base", "DeclarativeBase"}


def _model_unique_keys(trees: dict) -> tuple[dict, set]:
    """``({model: [frozenset(attrs), ...]}, {every model name})``.

    The attribute names, not the column names: `filter_by()` is written in
    attributes and `UniqueConstraint` in columns, and `Column("query", ...)`
    under a different attribute makes those two different words for one thing
    -- which `SEORecommendation` really does, for the reason
    check_shadowed_model_query() exists.
    """
    keys: dict = {}
    names: set = set()
    for rel, tree in trees.items():
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            if not any((isinstance(b, ast.Name) and b.id in _MODEL_BASES)
                       or (isinstance(b, ast.Attribute) and b.attr in _MODEL_BASES)
                       for b in cls.bases):
                continue
            names.add(cls.name)
            pk, found, by_column = [], [], {}
            for st in cls.body:
                if not (isinstance(st, ast.Assign) and len(st.targets) == 1
                        and isinstance(st.targets[0], ast.Name)):
                    continue
                attr, value = st.targets[0].id, st.value
                if attr == "__table_args__":
                    for c in ast.walk(value):
                        if isinstance(c, ast.Call) and (
                                getattr(c.func, "attr", "") == "UniqueConstraint"
                                or getattr(c.func, "id", "") == "UniqueConstraint"):
                            cols = [a.value for a in c.args
                                    if isinstance(a, ast.Constant)
                                    and isinstance(a.value, str)]
                            if cols:
                                found.append(cols)
                    continue
                # `Column(...)` and `db.Column(...)` are the same declaration.
                # Reading only the second is how a first pass at this check
                # reported `User.email` as unconstrained -- hub/users.py spells
                # it bare -- and would have sent somebody to "fix" the sign-in
                # lookup that was right all along.
                if not (isinstance(value, ast.Call)
                        and (getattr(value.func, "id", "") == "Column"
                             or getattr(value.func, "attr", "") == "Column")):
                    continue
                dbname = attr
                if value.args and isinstance(value.args[0], ast.Constant) \
                        and isinstance(value.args[0].value, str):
                    dbname = value.args[0].value
                by_column[dbname] = attr
                kw = {k.arg: k.value for k in value.keywords}
                for flag, into in (("primary_key", pk), ("unique", found)):
                    node = kw.get(flag)
                    if isinstance(node, ast.Constant) and node.value is True:
                        into.append(attr) if flag == "primary_key" else into.append([attr])
            if pk:
                found.append(pk)
            if found:
                keys[cls.name] = [frozenset(by_column.get(c, c) for c in k)
                                  for k in found]
    return keys, names


def _module_aliases(tree) -> dict:
    """``{name as written: class name}`` for this module.

    Two indirections, both of which left a real model unnamed: `from ... import
    Scene as CbScene` (four call sites in modules/creative_studio), and
    `q = CsTemplate.query.filter_by(...)` followed by `q.filter_by(...).first()`
    (three more in binder.py), where the model is only ever written on the
    line that built the variable.
    """
    out: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.asname:
                    out[a.asname] = a.name
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            for c in ast.walk(n.value):
                if isinstance(c, ast.Attribute) and c.attr == "query" \
                        and isinstance(c.value, ast.Name):
                    out.setdefault(n.targets[0].id, c.value.id)
                    break
    return out


def _ordered_bindings(tree) -> set:
    """Names bound to a query that already carries an `order_by`.

    `q = Model.query.filter_by(...).order_by(...)` then `q.first()` three lines
    down is ordered, and reading only the chain under `.first()` cannot see it
    -- which made this check report `binder.py`'s three template picks as
    unordered on the commit that ordered them. A check that reports a correct
    idiom is one people learn to scroll past.
    """
    out = set()
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)):
            continue
        if any(getattr(c, "attr", "") == "order_by" for c in ast.walk(n.value)):
            out.add(n.targets[0].id)
    return out


def _chain_root(call: ast.Call):
    """The name a query chain is built on, if it is built on one."""
    cur = call.func.value
    while isinstance(cur, (ast.Call, ast.Attribute, ast.Subscript)):
        cur = cur.func if isinstance(cur, ast.Call) else cur.value
    return cur.id if isinstance(cur, ast.Name) else None


def _is_core_query(call: ast.Call) -> bool:
    """A Core `select()`/`text()` run through a connection, not an ORM query.

    Out of this check's scope rather than unread by it: there is no model to
    carry a unique key, and `conn.execute(select(t).where(...)).first()` is a
    different question. Counted apart so the coverage line below stays a
    statement about ORM reads, which is what it claims to be.
    """
    for n in ast.walk(call):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("select", "text"):
            return True
        if isinstance(n, ast.Attribute) and n.attr in ("mappings", "scalars"):
            return True
    return False


def _query_shape(call: ast.Call, aliases: dict | None = None) -> tuple:
    """``(model, {attrs compared for equality})`` for the chain under a call."""
    aliases = aliases or {}
    model, cols = None, set()
    cur = call.func.value
    while isinstance(cur, (ast.Call, ast.Attribute, ast.Subscript)):
        if isinstance(cur, ast.Call):
            name = getattr(cur.func, "attr", "") or getattr(cur.func, "id", "")
            if name == "filter_by":
                cols |= {k.arg for k in cur.keywords if k.arg}
            elif name == "filter":
                for arg in cur.args:
                    for c in ast.walk(arg):
                        if (isinstance(c, ast.Compare) and len(c.ops) == 1
                                and isinstance(c.ops[0], ast.Eq)
                                and isinstance(c.left, ast.Attribute)):
                            cols.add(c.left.attr)
                            owner = c.left.value
                            model = model or (owner.id if isinstance(owner, ast.Name)
                                              else getattr(owner, "attr", None))
            elif name == "query":
                for arg in cur.args:
                    if isinstance(arg, ast.Name):
                        model = model or arg.id
                    elif isinstance(arg, ast.Attribute):
                        # `query(ProductionTake.id)` selects one column off a
                        # model. The owner is the model; `.attr` is "id", and
                        # taking it named two call sites after a column.
                        owner = arg.value
                        model = model or (owner.id if isinstance(owner, ast.Name)
                                          else arg.attr)
            cur = cur.func
        elif isinstance(cur, ast.Subscript):
            cur = cur.value
        else:
            if cur.attr == "query":
                owner = cur.value
                model = model or (owner.id if isinstance(owner, ast.Name)
                                  else getattr(owner, "attr", None))
            cur = cur.value
    if model is None and isinstance(cur, ast.Name):
        # The chain ends in a bare name: `q = Model.query.filter_by(...)` on an
        # earlier line and `q.first()` here. Without this the variable is where
        # the model's name stops being written down, and the call reads as
        # unresolvable.
        model = cur.id
    while model in aliases and aliases[model] != model:
        model = aliases[model]
    return model, cols


def _kept(tree, parents: dict, call: ast.Call) -> bool:
    """Does anything read a FIELD of the row, or is any row as good as any?

    `if Approval.query.filter_by(...).first():` asks whether one exists, and
    the answer does not depend on which. So does `existing = ...` followed by
    `if existing: skip` -- the name is a step, not a use, and reading only the
    expression around the call reported three of those as findings.
    """
    node, seen = parents.get(call), 0
    while node is not None and seen < 4:
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.BoolOp, ast.UnaryOp)):
            return False
        if isinstance(node, ast.Compare) and any(
                isinstance(o, (ast.Is, ast.IsNot)) for o in node.ops):
            return False
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "bool":
            return False
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not targets:
                return True
            return any(_used_as_a_row(tree, parents, name) for name in targets)
        if isinstance(node, ast.Return):
            return True
        node, seen = parents.get(node), seen + 1
    return True


def _used_as_a_row(tree, parents: dict, name: str) -> bool:
    """Is this name ever used as the row, rather than only tested for one?

    Reading a field off it is the obvious way, and it is not the only one:
    `exact = q.filter_by(...).first()` followed by `return exact` keeps the row
    without ever writing `exact.anything`. A first version looked only for
    attribute access and so read three of `binder.py`'s template picks as
    existence checks -- a false negative in the direction that produces an
    empty findings list, which is the direction that looks like success.
    """
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Name) and n.id == name
                and isinstance(n.ctx, ast.Load)):
            continue
        parent = parents.get(n)
        if isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.BoolOp,
                               ast.UnaryOp, ast.Assert)):
            continue                     # `if row:` -- a test, not a use
        if isinstance(parent, ast.Compare) and any(
                isinstance(o, (ast.Is, ast.IsNot)) for o in parent.ops):
            continue                     # `row is None`
        if isinstance(parent, ast.Call) and getattr(parent.func, "id", "") == "bool":
            continue
        return True
    return False


def check_unordered_first() -> list[dict]:
    """`.first()` on a filter that cannot name one row, with no ordering.

    SQL has no default order. `.first()` after a filter that matches two rows
    returns whichever the planner hands back, and the same call can answer
    differently on Postgres as the table grows or the plan changes -- while
    SQLite, where everything local runs, obliges by usually returning the
    lowest rowid. That is the family docs/claude/56 already records twice: a
    forward foreign key SQLite resolves lazily, and a SELECT alias in HAVING.
    Both were invisible in development by construction.

    It went in with two, and neither was theoretical:

      * `hub/industry.py` mirrored a client's resolved industry onto the Image
        Picker's row for that name. `image_picker_clients.name` is not unique
        -- only `slug` is, and a second gallery for one name is given `-2`
        rather than refused -- so it wrote one row and left the other. In
        production: `marco-island-rental` on "general" and
        `marco-island-rental-2` on "tourism", one client's industry answering
        two ways depending on which row a reader landed on. The caller's own
        comment one line up says every write here "lands on both".
      * `modules/sales_builder` read "has this revision been accepted" and then
        inserted, with nothing unique on (quote_id, revision). Two requests
        inside that gap -- a double-click on a public share link -- both read
        nothing and both insert, and the panel then reports whichever row came
        back as who agreed to the price.

    **An existence check is not a finding.** `if Approval.query...first():`
    asks whether one exists and any row answers it, including through a name:
    `existing = ...` then `if existing:` is the same question spelled over two
    lines, and a first pass that read only the expression around the call
    reported three of those.

    **What it could not read is reported too, as its own finding.** This is
    the whole discipline of the file and it is here because this check's own
    prototype twice announced a clean repository while resolving almost none
    of the call sites -- once because it only understood `db.Column(...)` and
    not the bare `Column(...)` hub/users.py uses, so `User.email` read as
    unconstrained; once because it only followed `filter_by` and skipped every
    `.filter(Model.col == v)` in `modules/scans`. Both times the output was an
    empty list, which is exactly what a clean repository produces. A sweep that
    cannot say how much it swept is not evidence, so coverage is a number this
    check reports rather than a property somebody hopes it has.
    """
    trees: dict = {}
    for rel, src in _sources():
        try:
            trees[rel] = ast.parse(src)
        except SyntaxError:
            continue
    return unordered_first_findings(trees)


def unordered_first_findings(trees: dict) -> list[dict]:
    """The findings for a ``{rel: tree}`` map, split out so the check can be
    driven over synthetic modules -- a sweep that cannot be shown to find one
    is a sweep asserting about nothing."""
    keys, known = _model_unique_keys(trees)
    out, total, read, core = [], 0, 0, 0
    unreadable: list[tuple] = []

    for rel, tree in trees.items():
        if pathlib.Path(rel).name.startswith("test_"):
            continue
        parents: dict = {}
        for n in ast.walk(tree):
            for child in ast.iter_child_nodes(n):
                parents[child] = n
        aliases = _module_aliases(tree)
        ordered = _ordered_bindings(tree)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "first"):
                continue
            total += 1
            if any(getattr(a, "attr", "") == "order_by" for a in ast.walk(n)) \
                    or _chain_root(n) in ordered:
                read += 1
                continue
            # Asked BEFORE anything else, because it settles the question
            # whichever row comes back. A relationship query
            # (`project.render_jobs.filter(...).first()`) names no model this
            # pass can resolve, and four of those sat in the coverage line as
            # though something were unknown about them -- when the code reads
            # `bool(... or ...)` and any row is the same answer.
            if not _kept(tree, parents, n):
                read += 1
                continue
            if _is_core_query(n):
                core += 1
                continue
            model, cols = _query_shape(n, aliases)
            if model not in known:
                unreadable.append((rel, n.lineno, model or "no model named"))
                continue
            read += 1
            model_keys = keys.get(model)
            if not model_keys:
                unreadable.append((rel, n.lineno, f"{model}: declares no unique key"))
                continue
            if any(k <= cols for k in model_keys):
                continue
            if (rel, model) in UNORDERED_FIRST_EXEMPT:
                continue
            named = ", ".join(sorted(cols)) or "nothing"
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": f"{rel}:{n.lineno} keeps the row from "
                          f"{model}.first() filtered on {named}, which covers "
                          f"no unique key on {model}. SQL has no default "
                          f"order, so past the first matching row this returns "
                          f"whichever one the planner hands back, and can "
                          f"answer differently on Postgres than it does on the "
                          f"SQLite everything local runs against.",
                "fix": "Say which row you mean -- order_by() a column that "
                       "breaks the tie -- or make the filter name one, with a "
                       "unique constraint on the columns it uses. If every "
                       "match should be written or read, take them all: "
                       "hub/industry._mirror_picker() does that now. If any "
                       "row genuinely answers the question, ask it as an "
                       "existence check rather than keeping the row.",
            })

    # Coverage, as a finding rather than a comment. An empty findings list is
    # worth exactly as much as the fraction of call sites behind it.
    if unreadable:
        shown = "; ".join(f"{r}:{ln} ({why})" for r, ln, why in unreadable[:6])
        out.append({
            "file": "hub/integrity.py", "module": "hub",
            "detail": f"{len(unreadable)} of {total} `.first()` call sites "
                      f"could not be resolved to a model with a declared "
                      f"unique key, so this check says nothing about them: "
                      f"{shown}{'; ...' if len(unreadable) > 6 else ''}. "
                      f"{read} of {total} were read, and {core} are Core "
                      f"queries with no model to carry a key. A clean result covering "
                      f"none of the repository reads exactly like a clean "
                      f"repository, which is how this check's own first two "
                      f"drafts passed.",
            "fix": "Either teach _query_shape() the chain shape, declare the "
                   "unique key on the model so it can be checked, or -- where "
                   "an arbitrary row is genuinely the answer -- order the read "
                   "so it stops being arbitrary. This entry is not a defect in "
                   "the code it names; it is the check reporting its own reach.",
        })
    return out

def check_shadowed_routes() -> list[dict]:
    """Hub routes hidden behind a mounted module's prefix.

    DispatcherMiddleware routes purely by URL prefix, so anything under
    /sites, /scans, /google and the rest goes to that module — a route
    registered on the hub app at /sites/match is never reached and 404s. This
    has now caught three separate features (Tickets, bulk scan, Match Sites),
    which is enough times to make it a check rather than a lesson.
    """
    import re
    try:
        src = (ROOT / "wsgi.py").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    mounts = sorted(set(re.findall(r'"(/[a-z0-9/_-]+)":\s*_mount', src)))
    if not mounts:
        return []
    hub_src = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8", errors="ignore")
    out = []
    for m_route in re.finditer(r'@app\.route\(\s*"([^"]+)"', hub_src):
        path = m_route.group(1)
        for m in mounts:
            if path == m or path.startswith(m + "/"):
                out.append({
                    "file": "hub/__init__.py", "module": "hub",
                    "detail": f"{path} is registered on the hub app but sits "
                              f"under the mounted prefix {m}, so the request "
                              f"never reaches it — it 404s.",
                    "fix": f"Move it outside {m} (for example /tools/…), or "
                           f"register it inside that module instead.",
                })
                break
    return out


def _base_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def check_shadowed_model_query(sources=None) -> list[dict]:
    """A Flask-SQLAlchemy model with a mapped attribute named `query`.

    `db.Model` carries the `query` descriptor every `Model.query.filter_by()`
    in this Hub reads, and a mapped attribute of that name on a subclass
    shadows it for that one class: `Thing.query` answers the column's
    InstrumentedAttribute, and `.filter_by()` on it raises AttributeError.
    `SEORecommendation` did exactly that -- a Search Console *search term* is
    the obvious thing to call `query` -- so every weekly refresh rolled back
    inside `_save_recommendations()` and the action queue answered 500, while
    the model imported, the table was created and every screen looked fine.
    Nothing errors until the first read, and the first read was in a
    background job whose result nobody drew.

    The column may keep its name: `db.Column("query", ...)` under any other
    attribute maps the same table with no migration. It reads the AST rather
    than the text, because the fix is explained in prose beside the column it
    fixes; and it is scoped to subclasses of a base spelled `Model`, since a
    classic declarative `Base` carries no `query` descriptor to shadow and a
    column of that name there is fine. `query_class` is deliberately not on
    the list -- assigning one is how Flask-SQLAlchemy is *told* to use a
    custom query, which is a decision rather than a collision.
    """
    out = []
    for rel, src in (sources if sources is not None else _sources()):
        if not rel.endswith(".py") or "Model" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(_base_name(b) == "Model" for b in node.bases):
                continue
            for stmt in node.body:
                targets = []
                if isinstance(stmt, ast.Assign):
                    targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    targets = [stmt.target]
                for t in targets:
                    if t.id != "query":
                        continue
                    out.append({
                        "file": rel, "module": _module_of(rel),
                        "line": stmt.lineno,
                        "detail": f"{node.name}.query is a mapped attribute, so "
                                  f"it hides Flask-SQLAlchemy's Model.query on "
                                  f"that class: `{node.name}.query.filter_by(...)` "
                                  f"raises AttributeError, and every reader of "
                                  f"it fails at the first read rather than at "
                                  f"import.",
                        "fix": "Rename the attribute and keep the column name: "
                               "`search_query = db.Column(\"query\", ...)` "
                               "maps the same table with no migration. Then "
                               "read the row through the new attribute.",
                    })
    return out


def check_template_collisions() -> list[dict]:
    """Two blueprints offering a template of the same name.

    A blueprint-registered module shares the hub app's Jinja environment, and
    that environment resolves a bare name by searching the hub's own templates
    first and then each blueprint's folder **in registration order**. So a
    module asking for "index.html" does not necessarily get its own: it gets
    whichever was registered first, and the loser renders somebody else's page
    against its own variables. This is the blueprint twin of the mount trap in
    check_shadowed_routes — the module is wired correctly and still shows the
    wrong thing.

    Both failure modes have now happened here at once. Calculators and Page
    Image Optimizer each shipped a plain `index.html`; calculators registers
    first, so /tools/page-images/ rendered the calculator index and 500'd on
    `'delivery' is undefined` — loud, and at least findable. Calculators also
    shipped a `leads.html`, which the hub's own `leads.html` outranks, so
    /tools/calculators/leads answered **200** with the Hub's leads page in it.
    Nothing errored, every template was valid and every link resolved.

    The fix is a name nobody else can claim: `tickets_*`, `picker_*` and
    `commercial_*` already do this, which is why those five modules were never
    caught by it.
    """
    import re
    roots: list[tuple[str, pathlib.Path]] = [("hub", ROOT / "hub" / "templates")]
    for pkg in sorted((ROOT / "modules").glob("*")):
        if not pkg.is_dir() or pkg.name in SKIP_DIRS:
            continue
        tpl = pkg / "templates"
        if not tpl.is_dir():
            continue
        # Only a module registered onto the hub app shares its Jinja
        # environment. A dispatcher-mounted module builds its own Flask app
        # and its own loader, so an identical name there collides with
        # nothing — that separation is the whole point of the mount.
        src = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in pkg.rglob("*.py")
            if "__pycache__" not in p.parts
        )
        if not re.search(r"\bapp\.register_blueprint\(", src):
            continue
        roots.append((pkg.name, tpl))

    seen: dict[str, list[str]] = {}
    for owner, tpl in roots:
        for f in tpl.rglob("*.html"):
            seen.setdefault(f.relative_to(tpl).as_posix(), []).append(owner)

    out = []
    for name, owners in sorted(seen.items()):
        if len(owners) < 2:
            continue
        mods = [o for o in owners if o != "hub"]
        # The hub's own folder is searched before every blueprint, so when it
        # holds a copy the winner is known. Between two blueprints it is
        # registration order in wsgi.py, which this check does not read —
        # naming a winner it cannot know is the kind of confident wrong answer
        # the report exists to catch.
        if "hub" in owners:
            resolves = "the hub's own copy, so " + " and ".join(mods) + " never render theirs"
        else:
            resolves = ("whichever of them wsgi.py registers first, so the rest "
                        "never render theirs")
        out.append({
            "file": f"modules/{mods[0]}/templates/{name}",
            "module": mods[0],
            "detail": f"{name} is offered by {', '.join(owners)}, which all share "
                      f"the hub app's Jinja environment. render_template("
                      f"\"{name}\") resolves to {resolves} — a 500 if the "
                      f"variables differ, and the wrong page in silence if "
                      f"they do not.",
            "fix": f"Give each one a name of its own — {mods[0]}_{name} — the "
                   f"way tickets_*, picker_* and commercial_* already do.",
        })
    return out

#: What reads as a secret in a field's id, name or placeholder.
#:
#: Matched against the identifier split into WORDS rather than as a substring
#: or with `\b`, and both of those were tried first:
#:
#:   * a plain substring reports `compass` and `bypass_cache`;
#:   * `\bpass\b` fixes those and then MISSES `api_token` and `client_secret`,
#:     because `_` is a word character — there is no boundary inside them —
#:     and misses `wpPass` too, because camelCase has no boundary either.
#:
#: Splitting on non-letters and on a lower-to-upper transition gets both:
#: `wpPass` -> wp, pass; `api_token` -> api, token; `compass` -> compass.
#: `pw` is here because a field called `umPwValue` was found only by the word
#: "password" in its PLACEHOLDER, and the fourth one -- the admin's "Set a
#: password" box -- says "Leave blank and the Hub generates one" and so was
#: matched by nothing. Leaning on the wording of a sentence somebody may edit
#: is not a check. Across every template it newly matched exactly that field,
#: which was a real finding rather than a false one.
SECRET_WORDS = frozenset({"password", "passwd", "pass", "pw", "secret",
                          "token", "apikey", "credential", "credentials"})

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")

#: Types that are not a text box, so masking does not apply. `password` is here
#: because it is the fix, and the rest because a checkbox named "save_token" is
#: not a place anybody types one.
NON_TEXT_INPUT = frozenset({"password", "checkbox", "hidden", "radio",
                            "submit", "button", "file", "range", "color"})

_INPUT_TAG = re.compile(r"<input\b[^>]*>", re.I)


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', tag, re.I)
    return m.group(1) if m else ""


def _looks_secret(ident: str) -> bool:
    """Whether this id/name/placeholder names a credential.

    `api key` and `api_key` reach the same word here because the split drops
    the separator and the pair is rejoined -- otherwise the one spelling
    somebody used would decide whether the field is checked.
    """
    words = [w.lower() for w in _WORD_SPLIT.split(ident) if w]
    if any(w in SECRET_WORDS for w in words):
        return True
    return any(a == "api" and b == "key"
               for a, b in zip(words, words[1:]))


def check_unmasked_secret_fields(root=None) -> list[dict]:
    """A credential typed into a box that shows it.

    Every one of the Hub's own *sign-in* fields was already `type="password"`,
    and `modules/skills360` even keys masking off a declared `f.secret`. Four
    were not, and the pattern in what they held is the point:

      * `seo_client.html` `su_pass` — the **client's own website login**;
      * `seo_client.html` `wpPass` — their WordPress application password;
      * `users_admin.html` `umAddPw` — the starting password an admin types for
        somebody else, which had no `type` at all and so defaulted to text;
      * `users_admin.html` `umPwValue` — the same, on the "Set a password" box.

    The passwords people type for THEMSELVES were protected and the ones they
    type for other people were on screen. Neither of the last two was found by
    reading the page; the third came from this check and the fourth from
    widening its word list, which is the argument for having one.

    Masking is not encryption and this does not pretend otherwise -- the value
    is sealed at rest by `hub/cms_credentials.py`. What `type="password"` stops
    is the shoulder, the screen share, the recorded call and the browser
    offering to remember it as an ordinary field.

    Read from the markup rather than from a rendered page, so a field built by
    JavaScript inside a template is covered too -- `wpPass` is one, and a check
    that only saw server-rendered HTML would have missed it.
    """
    # `root` is for the tests, and it is the whole reason they can be trusted:
    # a check whose finding path is never taken has not been shown to fail, and
    # this repo is fixed. A test that reimplemented the filtering to get at it
    # would assert against a COPY -- the first draft of test_secret_fields.py
    # did exactly that, and a mutation that made an untyped input count as safe
    # left every check green. The same argument as
    # `jsonstore.unmirrored_json_writers(root)`, and the same signature.
    import pathlib as _pl
    base = _pl.Path(root) if root else _pl.Path(ROOT)
    roots = [base / "hub" / "templates"]
    roots += sorted((base / "modules").glob("*/templates"))
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.html")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = f.relative_to(base).as_posix()
            for tag in _INPUT_TAG.findall(text):
                ident = " ".join((_attr(tag, "id"), _attr(tag, "name"),
                                  _attr(tag, "placeholder")))
                if not _looks_secret(ident):
                    continue
                kind = _attr(tag, "type").lower()
                if kind in NON_TEXT_INPUT:
                    continue
                # A type the template computes -- skills360 writes
                # `${f.secret?'type="password"':''}` -- is not something this
                # can read, and guessing would report the file that already
                # got it right. Only a literal non-password type, or none at
                # all, counts.
                if "${" in kind or "{{" in kind or "{%" in kind:
                    continue
                line = text[:text.index(tag)].count("\n") + 1
                out.append({
                    "file": rel, "module": _module_of_template(rel), "line": line,
                    "detail": f"{rel}:{line} takes a credential in an input "
                              f"that is {'type=' + kind if kind else 'untyped, so it defaults to text'}"
                              f", so it is readable on screen — over a "
                              f"shoulder, in a screen share, and in a recorded "
                              f"call.",
                    "fix": 'Give it type="password". That is about the screen '
                           'rather than about storage: the value is sealed at '
                           'rest by hub/cms_credentials.py, and masking is '
                           'what stops it being read off the page.',
                })
    return out


def _module_of_template(rel: str) -> str:
    parts = rel.split("/")
    return parts[1] if parts[0] == "modules" and len(parts) > 1 else "hub"


def check_orphan_templates(root=None) -> list[dict]:
    """A template nothing renders.

    A page that exists is not a page anybody can reach — this file already
    makes that point about the partner tiles, where `partner.available()` sat
    written with no caller while the dashboard offered four links and a
    promise. A template is the same failure with nothing at all to notice it:
    it is valid Jinja, `tools/pagecheck.py` never requests it because no route
    serves it, and `tools/linkcheck.py` names one only when it *also* finds a
    broken `url_for` inside — so an orphan whose links happen to resolve is
    invisible to every check here.

    What that costs is not disk. `modules/sites_admin/templates/site_detail.html`
    was rendered by nothing and was restyled anyway in the sweep that made
    Sites read like the rest of the Hub: real effort spent on a page no
    request can produce. And `modules/google_finder/templates/reports.html`
    was byte-identical to `gtm_logs.html` apart from its `<title>` — a
    copy-paste nobody finished, sitting beside live `/api/reports/save` and
    `/api/reports/search` routes with no screen in front of them. Reading the
    directory, both looked like features.

    **A computed name is still a render.** `modules/scans` picks between
    `widget.html` and `widget_audit.html` with a conditional and hands the
    result to `render_template`, so a check reading only the literal arguments
    of a render call reports the two most client-facing pages in that module
    as dead. Both are therefore matched as **string constants anywhere in the
    source**, which is looser than a call site on purpose: the cost of missing
    an orphan is a file nobody deletes, and the cost of naming a live page is
    somebody deleting it.

    A partial (`_scan_mark.html`) is reached by `include` rather than by a
    route, and `base.html` by `extends`, so both are read out of the templates
    themselves rather than assumed.

    Only Jinja is in scope: `modules/ad_builder/src/templates` holds the ad
    renderer's layout JSON, which is TypeScript's and never reaches
    `render_template`, so it is not one of the folders walked.
    """
    import re

    # `root` is the repository to read, defaulting to this one. It exists so a
    # test can drive this check against a throwaway tree instead of writing
    # probe files into the repo it is checking -- which is what this file's own
    # test used to do, and which raced `tools/preflight.py`: the sweep listed
    # hub/_integrity_orphan_caller.py and the test deleted it before the sweep
    # read it, so `compile every module` failed with FileNotFoundError on a
    # file that had never been committed. Same shape as
    # check_unmasked_secret_fields(root=...) and check_ai_callers(root).
    root = pathlib.Path(root) if root is not None else ROOT

    rendered: set[str] = set()
    for py in root.rglob("*.py"):
        if any(d in py.parts for d in SKIP_DIRS) or "__pycache__" in py.parts:
            continue
        # A test naming a template is not a route rendering it. This is the
        # rule check_provider_key_drift() works to one step over: a docstring
        # explaining a fix is not a call site, and neither is an assertion
        # about a file. Left in, a test that merely mentions an orphan keeps
        # it hidden for ever -- which is not hypothetical, since the sweep
        # that restyled the dead site_detail.html added a test naming it.
        if py.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        # Every string constant, not only a render_template() argument: the
        # name may be chosen in a conditional and passed in a variable.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.endswith(".html"):
                    rendered.add(node.value.split("/")[-1])

    files: list[pathlib.Path] = []
    for tpl in list(root.glob("*/templates")) + list(root.glob("modules/*/templates")):
        if any(d in tpl.parts for d in SKIP_DIRS):
            continue
        files.extend(tpl.rglob("*.html"))

    # A template reached by {% extends %}, {% include %}, {% import %} or
    # {% from %} has no route of its own and is not an orphan. Read out of
    # the templates rather than assumed, so a partial added tomorrow needs no
    # entry anywhere.
    #
    # Any .html name appearing in a template or a script counts too, for the
    # same reason the Python side reads every string constant: a page linked
    # to by name, or fetched by one, is reached. Erring loose is deliberate —
    # missing an orphan costs a file nobody deletes, and naming a live page
    # costs the page.
    for f in files:
        src = f.read_text(encoding="utf-8", errors="ignore")
        for ref in re.findall(r"{%-?\s*(?:extends|include|import|from)\s+[\'\"]([^\'\"]+)", src):
            # ...but not its own name, the guard the bare-.html pass below has
            # always had. Without it a template that documents its own include
            # line makes itself invisible to this check -- which is what
            # `_scorecard_stale_creative.html` did: its first line is a Jinja
            # comment reading `drop {% include "_scorecard_stale_creative.html" %}
            # into the dashboard`, so it registered itself as rendered and sat
            # there included by nothing while this check reported no orphans.
            if ref.split("/")[-1] != f.name:
                rendered.add(ref.split("/")[-1])
        for ref in re.findall(r"[\w./-]+\.html", src):
            if ref.split("/")[-1] != f.name:            # not its own name
                rendered.add(ref.split("/")[-1])
    for js in root.rglob("*.js"):
        if any(d in js.parts for d in SKIP_DIRS) or "node_modules" in js.parts:
            continue
        for ref in re.findall(r"[\w./-]+\.html",
                              js.read_text(encoding="utf-8", errors="ignore")):
            rendered.add(ref.split("/")[-1])

    out = []
    for f in sorted(files):
        if f.name in rendered:
            continue
        rel = f.relative_to(root).as_posix()
        out.append({
            "file": rel,
            "module": rel.split("/")[1] if rel.startswith("modules/") else "hub",
            "detail": f"{f.name} is named nowhere in this repository, so no "
                      f"route can render it and no request can produce it. It "
                      f"still reads as a feature in the directory, and it is "
                      f"still edited by sweeps that touch every template in "
                      f"the folder.",
            "fix": "Delete it, or give it the route it was written for — and "
                   "if a route was intended, check what the page actually "
                   "contains first: one of these was a copy of the page next "
                   "to it under a different title.",
        })
    return out


def check_shared_services() -> list[dict]:
    """Modules still doing Cloudinary, image work or settings themselves.

    Not a defect on its own — these all work. It is here so the migration is
    visible and shrinking, rather than something everyone means to get to. The
    rule is to move a module onto the shared code when you are already editing
    it for another reason.
    """
    # An image editor using PIL is not a module that should have used the
    # shared optimiser — cropping, rotating and resizing to an exact width are
    # operations hub.images does not offer and should not. Flagging it forever
    # produces a finding nobody can action, which is how a report stops being
    # read at all.
    editors = {"modules/image_optimizer/app.py"}

    out = []
    for rel, src in _sources():
        if not rel.startswith("modules/") or rel in editors:
            continue
        mod = _module_of(rel)
        uses_shared = ("hub.storage" in src or "hub.images" in src
                       or "from hub.config import" in src
                       or "from hub import config" in src)
        if uses_shared:
            continue
        own = []
        if "cloudinary.config(" in src:
            own.append("its own Cloudinary setup")
        if "thumbnail(" in src or "LANCZOS" in src:
            own.append("its own image resizing")
        if not own:
            continue
        out.append({
            "file": rel, "module": mod,
            "detail": f"{mod} has {' and '.join(own)} rather than using "
                      f"hub/storage.py and hub/images.py. A fix to the shared "
                      f"code will not reach it.",
            "fix": "Move it across next time you are editing this module for "
                   "another reason — not as a separate project.",
        })
    return out


def check_unbacked_json() -> list[dict]:
    """JSON written straight to the disk, with no copy in the database.

    Render's managed Postgres is backed up. The 5 GB disk mounted at /var/data
    is not, and an empty one is what comes back from a plan change, a region
    move or a resize. For a cache that is a non-event; for a file that is the
    only copy of something it is unrecoverable loss, and — because the module
    keeps working perfectly on an empty file — loss that announces itself as
    "the list is empty" rather than as an error.

    ``hub/jsonstore.py`` closes that by mirroring each write into the database
    and restoring on a miss. This check lists what has not moved across yet, so
    the remainder is a visible, shrinking number rather than something everyone
    means to get to. It is not a defect on its own: a module here works exactly
    as it always has, right up until the disk is recreated.
    """
    # The rule itself lives in hub/jsonstore.py, and this reads it rather than
    # keeping a copy. It kept one until /api/db/structure and this check
    # disagreed on the same Diagnostics page — that one exempted build scripts
    # and repo tooling and this one did not, so the panel reported a file
    # ad_builder does not write to the data disk directly above an audit that
    # had found nothing. Two answers to one question is worse than either.
    from . import jsonstore
    out = []
    for hit in jsonstore.unmirrored_json_writers(ROOT):
        rel, mod = hit["file"], hit["module"]
        if rel in SELF:
            continue
        out.append({
            "file": rel, "module": mod,
            "detail": f"{mod} writes JSON to the persistent disk without a "
                      f"copy in the database. The disk is outside the database "
                      f"backup and does not survive being recreated, so if "
                      f"this file is the only copy of something, it is "
                      f"unrecoverable.",
            "fix": "Read and write through hub/jsonstore.py — read_json / "
                   "write_json / delete_json — which keeps the same atomic "
                   "write and adds the mirror. If the file is genuinely "
                   "rebuildable, pass durable=False and say why.",
        })
    return out


# Key names that mean a credential, and the reason the list is this short.
# A name here is one whose value grants access to something if a person reads
# it. `token` and `secret` alone are deliberately NOT here: a share token in
# `hub/radio_share.py` is stored exactly so a customer's link keeps working,
# and a check that reports it is a check people switch off. What is listed is
# what nobody can defend having in a backup in the clear.
CREDENTIAL_KEYS = (
    "password", "passwd", "app_password", "application_password",
    "api_key", "apikey", "client_secret", "refresh_token",
    "private_key", "secret_key",
)

# Files under the data root this check does not read, with the reason. Empty,
# which is the only way it was worth adding -- and `check_stale_json_exemptions`
# above is there because an exemption outliving its file is the one finding
# that fails in the wrong direction.
CREDENTIALS_EXEMPT: dict[str, str] = {}


def _credential_strings(node, path="") -> list[str]:
    """Every credential-shaped key holding a bare non-empty string, by path.

    **A sealed credential is a dict**, and that is what makes this precise
    rather than a name search. `hub/cms_credentials.py` stores
    `{"enc": true, "data": "<Fernet token>"}`, so a sealed record is skipped
    by the shape of its value and never by being named in a list somebody has
    to maintain. What is left -- a credential-shaped key whose value is a
    plain string -- is a password somebody can read.
    """
    out = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            name = str(key).strip().lower()
            if (name in CREDENTIAL_KEYS and isinstance(value, str)
                    and value.strip()):
                out.append(here)
            else:
                out.extend(_credential_strings(value, here))
    elif isinstance(node, list):
        # Indexed rather than flattened: "the third website record" is what
        # somebody needs in order to go and look at it.
        for i, item in enumerate(node):
            out.extend(_credential_strings(item, f"{path}[{i}]"))
    return out


# Files allowed their own Fernet, with the reason. `hub/keyring.py` IS the key
# ring, so it is not drift; check_reconciliation reads its own
# CHECK_RECONCILIATION_ENCRYPTION_KEY rather than the shared one, which is a
# deliberate separation and not a sixth copy of the same key.
KEYRING_EXEMPT = {
    "hub/keyring.py": "this is the key ring",
    "modules/check_reconciliation/app.py":
        "seals under its own CHECK_RECONCILIATION_ENCRYPTION_KEY, deliberately "
        "separate from the shared one",
}


def check_tested_but_unwired() -> list[dict]:
    """A public function whose only callers are its own tests.

    `test_unwired.py` exists for "declared and never wired", which it opens by
    calling the single failure this codebase has paid for most often. It could
    not see this shape: it counts every identifier-shaped word in the repo, and
    a test file is part of the repo -- so a function called five times from
    `test_x.py` and nowhere else reads as thoroughly wired.

    `keyring.needs_reseal()` is what found it. It shipped with a test proving
    it worked and no caller at all, and what it does is finish a key rotation:
    without it the old key can never be dropped, so the rotation survives
    forever instead of ending. The test was green the whole time.

    **The list is empty, and that is what raised it to medium.** It went in
    at low with 21 findings behind it, on this repo's rule that a check red on
    the day it is switched on is a check people learn to ignore. A function
    here is not a defect on its own -- several are a named reading of a table,
    kept deliberately -- so the backlog was worked one at a time: three
    deleted as a second reading of something live, one wired to the diagnostic
    its own docstring named, and the rest declared in
    `test_unwired.TEST_ONLY_ALLOW` under the categories `ALLOW` already
    argues. At zero the next finding is the only finding rather than the
    twenty-third, which is where low would bury it.
    """
    import collections
    prod, tests = collections.Counter(), collections.Counter()
    word = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if path.is_dir() or any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix not in (".py", ".html", ".js", ".ts", ".json",
                               ".yml", ".yaml"):
            continue
        try:
            found = word.findall(path.read_text(encoding="utf-8",
                                                errors="ignore"))
        except OSError:
            continue
        (tests if rel.name.startswith("test_") else prod).update(found)

    # Both: a name in `ALLOW` is referenced nowhere at all and is that
    # check's finding rather than this one's, so reporting it here would be
    # two checks naming one function.
    allow = _unwired_allow() | _test_only_allow()
    defined: dict[str, list[str]] = {}
    for rel, src in _sources():
        base = os.path.basename(rel)
        if base.startswith("test_") or rel.split("/")[0] == "tools":
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # Undecorated only, the same rule test_unwired.py works to: a
            # route or a CLI command is called by its framework and named
            # nowhere, which is normal rather than a finding.
            if node.name.startswith("_") or node.decorator_list:
                continue
            defined.setdefault(node.name, []).append(rel)

    out = []
    for name, places in sorted(defined.items()):
        # Referenced no more often than it is defined = no call site anywhere
        # outside its own `def`. A test reference on top of that is what makes
        # it this finding rather than the one test_unwired.py already reports.
        if prod[name] > len(places) or not tests[name]:
            continue
        for rel in places:
            if f"{rel}:{name}" in allow:
                continue
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": f"{name}() in {rel} is called by its tests and by "
                          f"nothing else. A green test over a function no "
                          f"caller reaches proves the function works and not "
                          f"that anything uses it.",
                "fix": "Wire it where it belongs, delete it, or add "
                       f"'{rel}:{name}' to test_unwired.TEST_ONLY_ALLOW with "
                       "the reason it is kept — the allowlist for THIS "
                       "question, which is not ALLOW: everything here is "
                       "referenced by its own test, so an entry in ALLOW "
                       "reads to that check as one that has outlived what it "
                       "exempted.",
            })
    return out


def _test_only_allow() -> set:
    """`test_unwired.TEST_ONLY_ALLOW`'s keys.

    A SECOND dict rather than more entries in `ALLOW`, and this function
    exists because that distinction is load-bearing: `ALLOW` is for a name
    referenced nowhere in the repo at all, and every function *this* check
    finds is referenced -- by its own test. Sharing one dict satisfied this
    check and turned `test_unwired.py`'s own stale half red on all of them,
    which is why this backlog sat at twenty-two with the fix text pointing at
    the wrong list. One list answering two questions can be stale-checked on
    neither.
    """
    return _allow_keys("TEST_ONLY_ALLOW")


def _unwired_allow() -> set:
    """`test_unwired.ALLOW`'s keys, read from the file rather than copied.

    Two lists of what is deliberately unwired would drift, and the one that
    drifts silently is the one in the checker -- `check_unbacked_json` says
    the same about keeping its rule in `hub/jsonstore.py`.
    """
    return _allow_keys("ALLOW")


def _allow_keys(dict_name: str) -> set:
    """The keys of one dict in `test_unwired.py`, read rather than copied."""
    path = ROOT / "test_unwired.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == dict_name for t in node.targets):
            continue
        keys = set()
        for key in getattr(node.value, "keys", []):
            try:
                keys.add(ast.literal_eval(key))
            except Exception:                               # noqa: BLE001
                continue
        return keys
    return set()


def check_own_fernet(sources=None) -> list[dict]:
    """A module building its own single-key Fernet instead of the key ring.

    Seven files in this repo each wrote `Fernet(key)`, six of them over the
    same `TOKEN_ENCRYPTION_KEY`. Every one takes exactly one key, which is the
    part that matters: **it made that variable unrotatable.** Changing it --
    after a leak or a staff departure, which is exactly when it must change --
    locked out every client's WordPress application password, every client's
    own website login, the GoHighLevel tokens and the Google tokens at the
    same moment, with no way back but re-consenting and re-entering each one
    by hand.

    `hub/keyring.py` takes an ordered list through `MultiFernet`: the newest
    seals, any of them opens. This lists what has not moved across yet, so the
    remainder is a visible, shrinking number rather than something everybody
    means to get to.

    Low severity, and deliberately so: a module here works exactly as it always
    has. What it cannot do is survive a key rotation, and the rotation is the
    event nobody schedules.

    `sources` is an iterable of (relative path, source text) pairs, defaulting
    to this repository, so the test that proves this check still bites can hand
    it a module rather than write one into modules/ and delete it again.
    """
    out = []
    for rel, src in (sources if sources is not None else _sources()):
        if rel in SELF or rel in KEYRING_EXEMPT:
            continue
        # A test builds a key to make a fixture -- it seals nothing that
        # outlives the run, and a rotation cannot cost it anything. Reported
        # here it would be noise, and noise is how a low-severity check stops
        # being read.
        if os.path.basename(rel).startswith("test_"):
            continue
        if not re.search(r"\bFernet\s*\(", src):
            continue
        # A file that already reads the ring is fine even if it names Fernet
        # in prose -- prose is not a call site, which this repo has paid for
        # before.
        if "keyring" in src and not re.search(r"^\s*(from|import).*fernet",
                                              src, re.I | re.M):
            continue
        out.append({
            "file": rel, "module": _module_of(rel),
            "detail": f"{_module_of(rel)} builds its own Fernet rather than "
                      f"reading hub/keyring.py. That is a single key, so "
                      f"rotating TOKEN_ENCRYPTION_KEY makes everything it has "
                      f"sealed unreadable at once, with no way back but "
                      f"re-entering each credential by hand.",
            "fix": "Seal and open through hub/keyring.py — keyring.seal() and "
                   "keyring.unseal(), which take an ordered list of keys so "
                   "the newest seals and any of them opens. hub/cms_credentials.py "
                   "and hub/ghl_oauth.py are the worked examples; the stored "
                   "shape does not change, so nothing needs migrating.",
        })
    return out


def check_plaintext_credentials() -> list[dict]:
    """A credential sitting in a durable store as a readable string.

    The check above asks whether a JSON store is mirrored into the database.
    This asks the opposite question about the same files, and the SEO store is
    why it exists: `setup.password` -- a client's real login to their real
    website -- was written to `data/seo/<client>.json` as a plain string, and
    it was **properly mirrored**, so `check_unbacked_json` was satisfied and
    silent. Being mirrored is what made it worse: every one of those passwords
    went verbatim into Postgres and into every database backup taken since.

    So a store passing every other check here can still be the worst file on
    the disk, and nothing asked.

    **Read off the disk, not out of the source.** A dataflow rule would have
    missed the defect it was written for: that password was not assigned under
    a literal key but copied in a loop over a tuple of field names, which no
    reasonable AST rule catches without reporting half the login routes too.
    The files themselves cannot be wrong about what is in them.

    Which means this finds nothing in CI, where the data root is empty, and
    speaks on `/api/integrity` against the real disk -- the one place the
    question has an answer. That is the trade: a check that cannot be green
    for the wrong reason, in exchange for one that a pull request cannot
    prove. `tools/integritycheck.py` says so when it reports it.

    High severity, unlike the two backup checks above, which say in as many
    words that a module they list "works exactly as it always has". This one
    does not have that defence. A readable password in a backup is not a risk
    of a future failure; it is the failure, already shipped, for as long as
    the file sits there.
    """
    from . import jsonstore
    out = []
    try:
        root = jsonstore.data_root()
        if not os.path.isdir(root):
            return out
    except Exception:                                       # noqa: BLE001
        # No data root here at all -- an ordinary CI checkout. Reported as
        # nothing found, which is true: there is nothing to find.
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if rel in CREDENTIALS_EXEMPT:
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:                               # noqa: BLE001
                # Unreadable or not JSON. Skipped rather than reported: this
                # check answers one question and "that file is malformed" is
                # not it.
                continue
            for where in _credential_strings(data):
                out.append({
                    "file": os.path.join("data", rel), "module": rel.split(os.sep)[0],
                    "detail": f"{rel} holds a credential at {where} as a "
                              f"readable string. This store is mirrored into "
                              f"Postgres, so that value is in the database and "
                              f"in every backup taken since it was written.",
                    "fix": "Seal it through hub/cms_credentials.py, which "
                           "encrypts under TOKEN_ENCRYPTION_KEY and keeps the "
                           "three states apart, and drop the plaintext key in "
                           "the same save. hub/seo.py's seal_site_login() and "
                           "seal_all_site_logins() are the worked example, "
                           "including the rule that a deployment with no key "
                           "must not move it at all.",
                })
    return out


def check_disk_sqlite_stores() -> list[dict]:
    """Modules that open their own SQLite database on the data disk.

    The same risk as the check above and invisible to it: that one asks what
    JSON is written without a mirror, and a SQLite file is not JSON. So for as
    long as this page asked only the JSON question, a whole database on the
    disk was outside every check on it. Two were --
    ``modules/google_finder/app.py`` holding Google OAuth refresh tokens, and
    ``modules/io_builder/submission_attempts.py`` holding the receipts that
    stop a retried Suite delivery creating a second opportunity against a real
    insertion order -- and both were found by a person grepping rather than by
    anything here.

    Not a defect on its own, for the reason the JSON check gives: a module
    listed here works exactly as it always has, right up until the disk is
    recreated.
    """
    from . import jsonstore
    out = []
    for hit in jsonstore.disk_sqlite_stores(ROOT):
        rel, mod = hit["file"], hit["module"]
        if rel in SELF:
            continue
        out.append({
            "file": rel, "module": mod, "line": hit["line"],
            "detail": f"{mod} opens a SQLite database directly on the "
                      f"persistent disk. The disk is outside the database "
                      f"backup and does not survive being recreated, so "
                      f"whatever those tables hold is unrecoverable — and the "
                      f"unbacked-JSON check cannot see it, because a SQLite "
                      f"file is not JSON.",
            "fix": "Move the tables onto the shared engine in "
                   "hub/extensions.py, which is the Postgres everything else "
                   "is backed up with. modules/smartforecast/db.py is the "
                   "worked example: a sqlite3-shaped API over that engine, so "
                   "the module's SQL stays the SQL it had.",
        })
    return out


def check_stale_sqlite_exemptions() -> list[dict]:
    """Exemptions from the check above that no longer name a real file.

    The reason ``check_stale_json_exemptions`` gives, for the second list: a
    path left in after its file is deleted goes on covering whatever is
    written there next, and the audit stays green while doing it.
    """
    from . import jsonstore
    return [{
        "file": rel, "module": "hub",
        "detail": f"hub/jsonstore.py exempts {rel} from the disk-SQLite "
                  f"check, and that path no longer exists. The entry now "
                  f"covers anything written there next.",
        "fix": "Drop the entry from jsonstore.DISK_SQLITE_EXEMPT, or point it "
               "at the path the code moved to.",
    } for rel in jsonstore.stale_sqlite_exemptions(ROOT)]


def check_disk_binary_stores() -> list[dict]:
    """Modules that write bytes to the data disk with nothing else holding them.

    The third question on this page, and the one the other two could not
    reach. The JSON check asks what JSON is written without a mirror; the
    SQLite check asks what opens a database. Neither can see a module that
    writes a .webp, a .pdf or an .mp3 — which is most of what this suite
    actually produces for a client.

    The exemptions carry the weight here, because almost every binary write in
    this repo is already fine and says why: a scratch file inside a tempfile
    context, a cache rebuildable from a URL that is kept, or a Cloudinary-first
    write whose disk copy is reached only when the upload could not happen and
    is served by a route that module owns. What is left after those is a store
    with no second copy anywhere.

    Not a defect on its own, for the reason both checks above give: a module
    listed here works exactly as it always has, right up until the disk is
    recreated — or until this service runs more than one instance, which a
    disk currently prevents and is the point of removing it.
    """
    from . import jsonstore
    out = []
    for hit in jsonstore.disk_binary_writers(ROOT):
        rel, mod = hit["file"], hit["module"]
        if rel in SELF:
            continue
        out.append({
            "file": rel, "module": mod, "line": hit["line"],
            "detail": f"{mod} writes bytes to the persistent disk with "
                      f"{hit['how']}, and nothing else holds a copy. The disk "
                      f"is outside the database backup, does not survive being "
                      f"recreated, and is local to one instance — so these "
                      f"bytes are unreachable from any other instance and "
                      f"unrecoverable if the disk goes. Neither the "
                      f"unbacked-JSON check nor the disk-SQLite check can see "
                      f"this: it is neither.",
            "fix": "Send the bytes through hub/storage.py, which puts them in "
                   "Cloudinary and is what this repo uses for binary. Where "
                   "they are genuinely rebuildable or never outlive the "
                   "request, say so in jsonstore.DISK_BINARY_EXEMPT with a "
                   "reason that names what losing them would cost.",
        })
    return out


def check_stale_binary_exemptions() -> list[dict]:
    """Exemptions from the check above that no longer name a real file.

    The reason its two siblings give: a path left in after its file is deleted
    goes on covering whatever is written there next, and the audit stays green
    while doing it.
    """
    from . import jsonstore
    return [{
        "file": rel, "module": "hub",
        "detail": f"hub/jsonstore.py exempts {rel} from the disk-binary "
                  f"check, and that path no longer exists. The entry now "
                  f"covers anything written there next.",
        "fix": "Drop the entry from jsonstore.DISK_BINARY_EXEMPT, or point it "
               "at the path the code moved to.",
    } for rel in jsonstore.stale_binary_exemptions(ROOT)]


def check_stale_json_exemptions() -> list[dict]:
    """Exemptions from the check above that no longer name a real file.

    The exemption list is the one part of an audit that fails silently in the
    wrong direction: every other finding here is something appearing that
    should not, and this is something disappearing that should. A path left in
    the list after its file is deleted goes on covering whatever is written at
    that path next, and the audit stays green while doing it.

    This one started green — every entry named a file that existed — which is
    the only way it is worth having. The list it replaced did not: it named
    ``ui_check.py`` and two modules that had moved to append-only JSONL and so
    had not matched ``json.dump(`` for some time.
    """
    from . import jsonstore
    return [{
        "file": rel, "module": "hub",
        "detail": f"hub/jsonstore.py exempts {rel} from the unbacked-JSON "
                  f"check, and that path no longer exists. The entry now "
                  f"covers anything written there next.",
        "fix": "Drop the entry from jsonstore.UNMIRRORED_EXEMPT, or point it "
               "at the path the code moved to.",
    } for rel in jsonstore.stale_exemptions(ROOT)]


def check_creative_kit_drift() -> list[dict]:
    """Where the transcribed spec numbers and the kit we publish disagree.

    `hub/creative_specs.py` transcribes the S1M CREATIVE SPEC KIT on purpose:
    a table fetched live changes what a check says with no diff to point at.
    What that never covered is the transcription going stale, which it had --
    Half Page judged at 150 KB against a published 250 KB, 970x250 still
    called "Rising Star" after the IAB retired the programme, and a
    smartphone banner allowed three times the published weight.

    Every one of those is a file refused that the client was told to send, or
    accepted that they were told not to, and both are silent: the kit and the
    verdict are each internally consistent. The page ships in this repo, so
    this is checkable rather than remembered.
    """
    try:
        from . import creative_specs
        rows = creative_specs.kit_drift()
    except Exception:                                   # noqa: BLE001
        return []
    return [{
        "file": "hub/creative_specs.py", "module": "io_builder",
        "detail": r["detail"],
        "fix": "Correct the unit in hub/creative_specs.py to match "
               "hub/partner_pages/creative-specs.html, which is the kit the "
               "client is actually sent. Keep the unit's id: tags_for() has "
               "written it onto delivered creative in Cloudinary.",
    } for r in rows]


def check_creative_kit_names() -> list[dict]:
    """Unit names we ask a client for that the published kit no longer sells.

    `check_creative_kit_drift` compares numbers, and it can only read the three
    sections whose table is Unit / Dimensions / weight. The social sections
    publish a different table — but its first column is a **format name**, and
    a name is what the requirement line prints at the client.

    X is the case this was written for: its 2025 model named eight formats and
    not one of them is a format X still sells. "Website Card" and "Direct
    Message Card" are retired, and the mobile/desktop pairs modelled a split
    the kit says in as many words is gone. So a client was asked to supply four
    things that do not exist, and two of them twice — silently, because every
    name was a real format's name once and nothing errors.

    Only the channels declared transcribed against 2026 are checked; the rest
    are a named backlog on `kit_coverage()`, because a check that is red on the
    day it is written is one somebody switches off.
    """
    try:
        from . import creative_specs
        rows = creative_specs.kit_name_drift()
    except Exception:                                   # noqa: BLE001
        return []
    return [{
        "file": "hub/creative_specs.py", "module": "io_builder",
        "detail": r["detail"],
        "fix": "Rename the unit to what hub/partner_pages/creative-specs.html "
               "calls it, or move it to RETIRED_UNITS with what replaced it. "
               "Keep the unit's id either way: tags_for() has written it onto "
               "delivered creative in Cloudinary.",
    } for r in rows]


def check_creative_kit_coverage() -> list[dict]:
    """Sections of the published kit nobody has declared one way or the other.

    `check_creative_kit_drift` compares the numbers; this asks the question one
    step earlier — is every section of the page one somebody has looked at. It
    was covering three sections of twenty-three and answering "no drift", which
    is a clean bill of health about seven per cent of the thing it audits. The
    page in the repo is now the 2026 kit and says on itself that twenty formats
    were updated and three added, against a transcription taken from 2025.

    The twenty are declared with their reasons, so this starts empty. What it
    catches is the next rebuild: a section added to the page is otherwise
    silently outside every check here, for ever, with the panel green — which
    is the failure the report itself is about.
    """
    try:
        from . import creative_specs
        cov = creative_specs.kit_coverage()
    except Exception:                                   # noqa: BLE001
        return []
    if not cov.get("measured"):
        # A page that cannot be read is not a page with nothing new in it.
        return [{
            "file": "hub/creative_specs.py", "module": "io_builder",
            "detail": f"The published kit could not be read, so its coverage "
                      f"is not measured ({cov.get('error')}).",
            "fix": "Restore hub/partner_pages/creative-specs.html, which is "
                   "the kit the client is sent and what this check reads.",
        }]
    out = []
    for sid in cov.get("undeclared", []):
        out.append({
            "file": "hub/creative_specs.py", "module": "io_builder",
            "detail": f"The published kit carries a section \"{sid}\" that "
                      f"nothing in the transcription accounts for.",
            "fix": "Transcribe it into UNITS and add it to _KIT_SECTIONS, or "
                   "declare it in _KIT_UNREAD (the table shape cannot be "
                   "parsed) or _KIT_NOT_MODELLED (we sell it and hold no unit "
                   "for it) with the reason. A section nobody has declared is "
                   "one no check here can see.",
        })
    for sid in cov.get("stale", []):
        out.append({
            "file": "hub/creative_specs.py", "module": "io_builder",
            "detail": f"\"{sid}\" is declared here and is no longer a section "
                      f"of the published kit.",
            "fix": "Drop the declaration. An exemption that outlives what it "
                   "exempted goes on excusing whatever is published under that "
                   "id next.",
        })
    return out


def check_creative_spec_disagreement() -> list[dict]:
    """Products the creative gate and the spec kit read as different mediums.

    Two readings of one question: `creative_needs.medium_of()` decides whether
    to ask a client for creative, and `creative_specs.channels_for_product()`
    decides what to ask for. They drifted apart on 25 of 90 products, in both
    directions — display products gated as video, and whole categories (mobile
    display, email, signage) gated as nothing at all — and every one of them
    was silent, because each screen is internally consistent on its own.

    High severity for the same reason `creative_medium_drift` is: the failure
    is a launch date, and nothing anywhere looks wrong until the files arrive.
    """
    try:
        from . import creative_needs
        rows = creative_needs.spec_disagreements()
    except Exception:                                   # noqa: BLE001
        return []
    return [{
        "file": "hub/creative_needs.py", "module": "sales_builder",
        "detail": f'"{r["product"]}" under {r["category"]} is {r["gate"]} to the '
                  f'creative gate and {"/".join(r["kit"])} to the spec kit. One '
                  f'decides whether the client is asked for creative and the '
                  f'other what they are asked for, so the rep is asked for one '
                  f'thing and judged against another.',
        "fix": "Reconcile CATEGORY_MEDIUM/EXPLICIT_MEDIUM in hub/creative_needs.py "
               "with _PRODUCT_CHANNELS in hub/creative_specs.py, or name the pair "
               "in SPEC_AGREE_EXEMPT with the reason both readings are right.",
    } for r in rows]


def check_creative_medium_drift() -> list[dict]:
    """Rate-card products the creative gate names by hand, that no longer exist.

    Four programmatic *video* products sit under the DISPLAY category beside
    banner inventory, and three of the four have names that identify nothing:
    "Programmatic - Targeted" is $17.00 CPM video, while "Category" next to it
    is $4.25 CPM display. `hub/creative_needs.py` therefore names them.

    If one is renamed on the card, that lookup stops matching and the product
    quietly falls back to the keyword guess — which reads it as display. The
    plan would then price a video buy and never ask whether a spot exists,
    which is the exact failure the gate was built to prevent, and it would
    look completely healthy on screen.
    """
    try:
        from . import creative_needs
        missing = creative_needs.card_drift()
    except Exception:                                   # noqa: BLE001
        return []
    return [{
        "file": "hub/creative_needs.py", "module": "sales_builder",
        "detail": f'The creative gate treats "{name}" as video, but no product '
                  f'by that name is on the rate card any more. It is now being '
                  f'classified by keyword instead, which reads it as display — '
                  f'so a plan containing it will be priced without anyone being '
                  f'asked whether a spot exists.',
        "fix": "Update EXPLICIT_MEDIUM in hub/creative_needs.py to the product's "
               "new name on the card.",
    } for name in missing]


def _env_names_read(src: str) -> set[str]:
    """Environment variable names a file genuinely reads.

    Parsed rather than matched, because the pattern is quoted in prose all over
    this codebase: the comment above pexels_service's `_key()` explains that it
    used to read ``os.environ["PEXELS_API_KEY"]``, and a regex reported the
    explanation of the fix as the defect. A check that flags a file for
    describing the bug it no longer has is a check people learn to skip.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out: set[str] = set()

    def _is_environ(node) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "environ"
                and isinstance(node.value, ast.Name) and node.value.id == "os")

    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            target = node.slice
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "setdefault", "pop"}
                and _is_environ(node.func.value)
                and node.args):
            target = node.args[0]
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getenv"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.args):
            # os.getenv is the same read spelled differently, and it is how
            # modules/sites_admin reached SECRET_KEY past a check that only
            # knew os.environ.
            target = node.args[0]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            out.add(target.value)
    return out


def check_provider_key_drift(sources=None) -> list[dict]:
    """A module reading one spelling of a key that is set under another.

    This is the defect that took the Commercial Builder's stock video search
    off the air without anything reporting it: pexels_service.py read
    os.environ["PEXELS_API_KEY"], Render sets PEXELS_API, so is_live() said
    False and every search silently returned placeholder images labelled like
    real footage. hub/config.py already accepts both spellings — the module
    just never asked it.

    The alias groups come from `hub.config.ALIASES` itself, so a provider added
    to config is covered by this check the same day, and a check that has
    drifted from the settings it is policing cannot happen. It used to
    regex the `_first("A", "B")` calls out of config's source instead, which
    held right up until those calls were replaced by a table — at which point
    the check found no groups, reported nothing, and read as a clean bill of
    health. Importing the table means the same edit cannot silence it twice.

    Two things it deliberately does *not* flag:

      * A read that lists **every** spelling in the group. That is what a
        fallback beneath `from hub.config import settings` looks like, and it
        resolves exactly what config would. Flagging it teaches people to
        ignore the check.
      * Anything outside hub/ and modules/ — the test files set these variables
        rather than reading them.

    A file that imports config and *still* reads one spelling is flagged, which
    is the case the old file-level skip hid: modules/image_creator/assets.py
    routed its font key through settings and its Brandfetch key through
    os.environ on the next screen up, and the skip covered the second because
    of the first.

    `sources` is an iterable of (relative path, source text) pairs, defaulting
    to this repository. It is here for the same reason
    check_shadowed_model_query() takes one: a test proving that prose is not a
    call site needs a file that reads the wrong spelling, and the only place to
    put one used to be the repo itself -- which raced any concurrent sweep and
    left a probe behind if the run died.
    """
    try:
        from .config import ALIASES
    except Exception:                               # noqa: BLE001
        return []
    if not ALIASES:
        return []
    alias_of = {name: names for names in ALIASES.values() for name in names}

    out, seen = [], set()
    for rel, src in (sources if sources is not None else _sources()):
        if not (rel.startswith("modules/") or rel.startswith("hub/")):
            continue
        read_here = _env_names_read(src)
        for name in sorted(read_here):
            names = alias_of.get(name)
            if not names or (rel, name) in seen:
                continue
            # The whole group present is a fallback, not a drift.
            if set(names) <= read_here:
                seen.update((rel, n) for n in names)
                continue
            seen.add((rel, name))
            others = [n for n in names if n != name and n not in read_here]
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": f"{rel} reads {name} directly. The same setting is "
                          f"also spelled {', '.join(others)}, and hub/config.py "
                          f"accepts all of them. If this deployment sets one of "
                          f"the others, this module reports the key as missing "
                          f"and degrades silently.",
                "fix": "Read it through hub.config.settings instead of "
                       "os.environ, so every spelling in use resolves. If it "
                       "genuinely cannot import config, read every name in the "
                       "group rather than one.",
            })
    return out


def check_ghl_scope_coverage() -> list[dict]:
    """A file that writes to HighLevel with no scope declared for it.

    High, for the same reason `provider_key_drift` is: every finding it can
    produce is silent by construction. The write runs on the agency Private
    Integration Token today and works, so nothing looks wrong — right up until
    that call moves onto a per-sub-account token, where the scope was never
    consented to. Then it 401s for every client at once, looking exactly like
    a bad token, and the fix is not a code change but an agency re-consent that
    somebody has to sit through.

    The check exists because the hand-written version could not work. The test
    enumerated five known call sites, so it re-confirmed what somebody had
    already thought of and could never find the sixth — and within a few months
    two had slipped past it: hub/qa.py grew an opportunity-status write, and
    the Social Planner's posting moved from app.py into suite_client.py while
    the table went on naming app.py.

    It asserts the weak invariant on purpose: the file must be named in *some*
    scope's `needed_by`, not that the right scope was chosen. Inferring the
    scope from an endpoint is where false positives come from, and a check
    people learn to ignore is worse than no check. Being named is enough to
    guarantee somebody looked at it.
    """
    try:
        from . import ghl_scopes
    except Exception:                               # noqa: BLE001
        return []

    out = []
    for rel in ghl_scopes.undeclared_writes():
        out.append({
            "file": rel, "module": _module_of(rel),
            "detail": "Writes to the HighLevel API but no scope in "
                      "hub/ghl_scopes.py names it. On the agency token this "
                      "works; on a per-sub-account token it 401s for every "
                      "client, because the scope was never consented to.",
            "fix": "Add the file to the `needed_by` of the scope its write "
                   "needs, or to WRITE_EXEMPT with the reason it needs none.",
        })
    for rel in ghl_scopes.stale_declarations():
        out.append({
            "file": rel, "module": _module_of(rel),
            "detail": "hub/ghl_scopes.py declares this file as a caller, but "
                      "it no longer exists. A declaration pointing at a "
                      "deleted file reads as coverage and is not.",
            "fix": "Point the scope's `needed_by` at the file that took over "
                   "the call, or drop the entry.",
        })
    return out



def check_ghl_scope_names() -> list[dict]:
    """A scope this Hub asks for that HighLevel's console does not list.

    Three of the nineteen were wrong when the console's own list was finally
    read: two named a `social-media-posting.*` family that does not exist, and
    `forms/submissions.readonly` was inherited from the original DEFAULT_SCOPES
    and never questioned because it was already in the code.

    None of them is visible before consent. HighLevel grants what it recognises
    and says nothing about the rest, so a made-up name is silently dropped --
    and the worst of the three was also the string hub/suite_accounts.py gated
    the Social Planner push on, which would have reported "not granted" for
    ever, including after a consent that granted the real scope.

    Advisory rather than blocking: AVAILABLE is a snapshot of a list HighLevel
    publishes no endpoint for and extends as it ships features, so a name
    missing from it means "not in the list we captured", never "does not
    exist". A finding is a prompt to re-read the console, not a broken build.
    """
    try:
        from . import ghl_scopes
    except Exception:                               # noqa: BLE001
        return []
    return [{
        "file": "hub/ghl_scopes.py", "module": "hub",
        "detail": f"{name!r} is requested but is not in the scope list captured "
                  f"from HighLevel's console on {ghl_scopes.AVAILABLE_CAPTURED}. "
                  "A name HighLevel does not recognize is dropped at consent "
                  "without an error.",
        "fix": "Check the name against the app's scope picker. If HighLevel has "
               "added it since, add it to AVAILABLE with the new capture date.",
    } for name in ghl_scopes.unknown_requested()]


def check_flask_g_in_schedulable() -> list[dict]:
    """A scheduler job that imports flask.g will fail at runtime.

    The scheduler runs jobs on a background thread with an app context but
    no request context, so ``flask.g`` (which is request-scoped) raises a
    ``RuntimeError``. This check catches the regression before it ships:
    any ``.py`` file that both (a) is imported by a ``job_*`` function in
    ``hub/scheduler.py`` and (b) imports ``g`` from ``flask``.
    """
    sched_path = ROOT / "hub" / "scheduler.py"
    if not sched_path.exists():
        return []
    try:
        sched_src = sched_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    imported_modules: set[str] = set()
    for m in re.finditer(r"def\s+job_\w+.*?(?=\ndef\s|\Z)", sched_src, re.S):
        body = m.group()
        for imp in re.finditer(r"from\s+([\w.]+)\s+import", body):
            imported_modules.add(imp.group(1))
        for imp in re.finditer(r"import\s+([\w.]+)", body):
            imported_modules.add(imp.group(1))

    out = []
    for rel, src in _sources():
        mod_path = rel.replace("/", ".").replace(".py", "")
        if not any(mod_path == m or mod_path.startswith(m + ".")
                   or m.startswith(mod_path + ".") for m in imported_modules):
            continue
        if re.search(r"from\s+flask\s+import\s+[^#\n]*\bg\b", src):
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": f"{rel} imports flask.g and is reachable from a "
                          "scheduler job. flask.g requires a request context "
                          "that background jobs do not have — this will raise "
                          "RuntimeError at runtime.",
                "fix": "Pass what you need through the function arguments, or "
                       "read it from the app config / database instead of g.",
            })
    return out


def check_cross_module_url_for() -> list[dict]:
    """A mounted module's template calling url_for for an endpoint outside it.

    DispatcherMiddleware gives each mounted module its own Flask app with its
    own route table, so a url-for call naming a hub endpoint inside a mounted
    module's template raises BuildError at render time. This has caught three
    features historically.
    """
    try:
        wsgi_src = (ROOT / "wsgi.py").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    mounts: dict[str, str] = {}
    for m in re.finditer(r'"(/[a-z0-9/_-]+)":\s*_mount\(\s*(\w+)\.app', wsgi_src):
        prefix, var = m.group(1), m.group(2)
        mounts[prefix] = var

    module_dirs: dict[str, str] = {}
    for prefix, var in mounts.items():
        mod_match = re.search(
            rf"\b{re.escape(var)}\b\s*=.*?modules[./](\w+)", wsgi_src)
        if mod_match:
            module_dirs[prefix] = mod_match.group(1)

    if not module_dirs:
        return []

    out = []
    for prefix, mod_dir in module_dirs.items():
        tpl_dir = ROOT / "modules" / mod_dir / "templates"
        if not tpl_dir.is_dir():
            continue
        for tpl in tpl_dir.rglob("*.html"):
            try:
                html = tpl.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for call in re.finditer(r"url_for\(\s*['\"]([^'\"]+)['\"]", html):
                endpoint = call.group(1)
                if "." in endpoint:
                    bp = endpoint.split(".")[0]
                    if bp == mod_dir or bp == "static":
                        continue
                    out.append({
                        "file": tpl.relative_to(ROOT).as_posix(),
                        "module": mod_dir,
                        "detail": f"url_for('{endpoint}') references blueprint "
                                  f"'{bp}', which is not part of the mounted "
                                  f"module at {prefix}. This will raise "
                                  f"BuildError at render time.",
                        "fix": f"Use a hard-coded path or move the route into "
                               f"the {mod_dir} module.",
                    })
    return out


CHECKS = [
    ("ghl_scope_names", "A GHL scope name the console does not list", "medium",
     check_ghl_scope_names),
    ("ghl_scope_coverage", "A GHL write with no scope declared for it", "high",
     check_ghl_scope_coverage),
    ("pdf_resource_type", "PDF uploaded as an image type", "high", check_pdf_resource_type),
    ("convert_without_resize", "Converts without resizing", "high", check_convert_without_resize),
    ("untracked_openai", "OpenAI spend not recorded", "medium", check_untracked_openai),
    ("untracked_provider_usage", "ElevenLabs, Cloudinary or Google usage not recorded",
     "medium", check_untracked_provider_usage),
    ("silent_modules", "Modules that never log", "medium", check_silent_modules),
    ("write_route_attribution",
     "A triaged module's write route logs nothing", "medium",
     check_write_route_attribution),
    ("unclamped_limits", "Unclamped query limits", "medium", check_unclamped_limits),
    # Medium: it is the wrong FIGURE rather than a broken page, which is the
    # kind this repo has paid most for -- four rounds of finding these by
    # hand, each one a number on a screen that read as measured. It went in
    # with two findings, both fixed in the same change, so it starts empty.
    ("capped_read_misuse", "A capped read counted, or searched by key",
     "medium", check_capped_read_misuse),
    # Medium, and the coverage line it can emit is the point of it: this went
    # in with two live findings and a check that had twice reported a clean
    # repository while reading almost none of it.
    ("unordered_first", "A .first() that cannot name one row",
     "medium", check_unordered_first),
    ("shadowed_routes", "Routes hidden behind a mount", "high", check_shadowed_routes),
    # High: the model imports, the table is created, and every reader of it
    # 500s or rolls back at the first read. It went in at zero, with the one
    # finding it was written for fixed in the same change.
    ("shadowed_model_query", "A model attribute that hides Model.query", "high",
     check_shadowed_model_query),
    ("template_collisions", "Two blueprints, one template name", "high",
     check_template_collisions),
    ("bare_except_pass", "Silent exception handling", "low", check_bare_except_pass),
    ("shared_services", "Not yet on shared services", "low", check_shared_services),
    # Low: an orphan template costs nobody a broken page -- it costs the
    # effort spent editing one, and the feature somebody thinks is there
    # because the directory says so. It went in with three findings, which
    # were deleted in the same change, so it starts empty.
    ("orphan_templates", "A template nothing renders", "low",
     check_orphan_templates),
    ("unmasked_secret_fields", "A credential typed into a box that shows it",
     "medium", check_unmasked_secret_fields),
    ("unbacked_json", "JSON on the disk with no backup", "medium", check_unbacked_json),
    ("stale_json_exemptions", "Unbacked-JSON exemption names a missing file",
     "medium", check_stale_json_exemptions),
    ("disk_sqlite", "A SQLite database on the disk with no backup", "medium",
     check_disk_sqlite_stores),
    # High, and for a different reason from the two backup checks beside it:
    # those say a module they list works exactly as it always has. A readable
    # password in a backup is not a risk of a later failure, it is the failure,
    # already shipped, for as long as the file sits there.
    ("plaintext_credentials", "A credential stored as readable text", "high",
     check_plaintext_credentials),
    # Low for the reason the backup checks are: a module listed here works
    # exactly as it always has. What it cannot do is survive a key rotation.
    ("own_fernet", "A module sealing with its own single key", "low",
     check_own_fernet),
    # Medium now, and the sentence it replaces said "low, and it must stay
    # low: it went in with 21 findings behind it, and a check red on the day
    # it is switched on is a check people learn to ignore." That argument was
    # right and its premise is gone -- the list is empty, so the next finding
    # is the only finding rather than the twenty-third, and low is where it
    # would be buried. Deliberately NOT high: high fails the run, and the
    # correct answer to several of these is an allowlist entry with a reason,
    # which is a judgment somebody makes rather than a build somebody unblocks
    # at speed. The `provider_key_drift` note asked for exactly this raise
    # once its own list was empty.
    ("tested_but_unwired", "A function only its own tests call", "medium",
     check_tested_but_unwired),
    ("stale_sqlite_exemptions", "Disk-SQLite exemption names a missing file",
     "medium", check_stale_sqlite_exemptions),
    ("disk_binary", "Bytes on the disk with no copy anywhere else", "medium",
     check_disk_binary_stores),
    ("stale_binary_exemptions", "Disk-binary exemption names a missing file",
     "medium", check_stale_binary_exemptions),
    ("creative_medium_drift", "Creative gate lost a rate-card product", "high",
     check_creative_medium_drift),
    ("creative_spec_disagreement", "Creative gate and spec kit disagree", "high",
     check_creative_spec_disagreement),
    ("creative_kit_drift", "Spec numbers differ from the kit we publish", "high",
     check_creative_kit_drift),
    ("creative_kit_coverage",
     "A section of the published kit nobody has declared", "high",
     check_creative_kit_coverage),
    ("creative_kit_names",
     "A unit named after a format the kit no longer sells", "high",
     check_creative_kit_names),
    # High, as the note that stood here asked for once the list was empty. It
    # went in at medium with seven pre-existing findings it did not cause,
    # because a check switched on red is a check somebody turns off; the list
    # is now empty, hub/ is covered as well as modules/, and os.getenv is read
    # the same as os.environ. Every finding it can produce is a key that IS
    # configured being reported as missing, which is silent by construction —
    # the tool degrades to mock data, the screen looks healthy, and nobody
    # finds out until a client is waiting on the output. That is worth a red
    # build, and the fix is one line at the call site.
    ("provider_key_drift", "Provider key read under one spelling only", "high",
     check_provider_key_drift),
    # Medium, and green the day it went in. A bubble whose key is not in the
    # registry is removed client-side rather than left as a dead "?" -- right
    # for the page, and exactly what makes the mistake invisible: the template
    # reads as helped, the screen shows nothing, and nothing errors at either
    # end. Three tools had one on their own title. Not high, because the page
    # still works and nobody is waiting on output; not low, because the whole
    # help layer is opt-in and a screen that opted in and got nothing is
    # indistinguishable from one that never tried.
    ("dead_help_bubbles", "A help bubble with no help behind it", "medium",
     _help_audit.check_dead_bubbles),
    # High, and green the day it went in. This is the one failure in this file
    # that has recurred five times: work_log() skips a module WORK_KINDS
    # cannot name, so a client who has just had display ads built / an ad copy
    # request raised / a website audit run / an insertion order written reads
    # as a client nobody has done any work for. Every screen is complete, the
    # log row is present, the client record is confidently empty, and nothing
    # errors at any of the three. Each of the five was found by somebody
    # opening one client's record and noticing, which is not a way of finding
    # the sixth -- and the fix is one line.
    ("unnamed_client_work", "Client work the record cannot name", "high",
     _client_brand.check_work_kinds),
    # High, and it went in green. 14 call sites reached OpenAI directly
    # before this; each one skipped the client brief (so it wrote generic,
    # plausible copy for a business the Hub had files on) and skipped the
    # usage log (so its spend was invisible on the usage page) at once. A
    # finding here is a call site with both defects, not one -- so the fix
    # is never "record the spend" alone.
    ("ai_callers_unwrapped", "A call to OpenAI that skips hub.ai", "high",
     _client_brief.check_ai_callers),
    # The same question from the other end, and the half nothing was asking.
    # check_work_kinds() finds a name that logs against a client and the table
    # cannot name. This finds a name the table *does* know whose rows can
    # never carry a client at all -- because the call site puts it under a key
    # work_log() does not read (`detail=`), or because the table is keyed on
    # the module directory while the module logs under a different name.
    #
    # High, and it went in green. Both shapes were live: every tracked-link
    # batch and every cut-out saved against a client was written to the log,
    # kept, and dropped before the record it was written for. Nothing errored
    # at any point -- the tool's own screens complete, the row on disk, the
    # client record confidently empty, which is the failure this whole corner
    # of the codebase keeps having to undo.
    ("client_attribution", "Work filed under a key the record cannot read",
     "high", _client_brand.check_client_attribution),
    ("stale_work_exemptions", "A not-a-deliverable exemption outlived its "
     "call site", "medium", lambda: [
         {"file": "hub/client_brand.py", "module": m,
          "detail": (f"NOT_WORK names {m!r}, which no longer logs against a "
                     "client — the exemption now covers whatever is written "
                     "under that name next"),
          "fix": f"Drop {m!r} from NOT_WORK."}
         for m in _client_brand.stale_work_exemptions()]),
    ("flask_g_in_schedulable",
     "A scheduler job imports flask.g (no request context)", "high",
     check_flask_g_in_schedulable),
    ("cross_module_url_for",
     "A mounted module's template calls url_for outside its app", "high",
     check_cross_module_url_for),
]


def run() -> dict:
    groups, total = [], 0
    for key, label, severity, fn in CHECKS:
        try:
            findings = fn()
        except Exception as exc:                        # noqa: BLE001
            findings = [{"file": "-", "module": "-",
                         "detail": f"Check failed: {type(exc).__name__}",
                         "fix": ""}]
        total += len(findings)
        groups.append({"key": key, "label": label, "severity": severity,
                       "count": len(findings), "findings": findings,
                       "state": "ok" if not findings else severity})
    return {
        "groups": groups,
        "total": total,
        "clean": total == 0,
        "note": "Static read of the source. Every pattern here was a real "
                "shipped defect at least once — the check exists so it cannot "
                "return unnoticed in a module written later.",
    }
