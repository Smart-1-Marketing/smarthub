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
    live = set(seen)
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


def check_unclamped_limits() -> list[dict]:
    """A limit read straight from the query string with no bounds."""
    out = []
    for rel, src in _sources():
        # The helper's own docstring quotes the bad pattern as the example
        # of what not to write. Flagging it would be the check reporting
        # its own fix as the defect.
        if rel.replace("\\", "/").endswith("hub/webargs.py"):
            continue
        # The same defect arrives on a form field or a JSON body, not just
        # the query string, so all three are covered.
        # Two patterns, because the names mean different things by source.
        # On a query string "items" and "n" are page sizes; in a JSON body
        # they are almost always the payload itself — a proposal's line items,
        # a spot count — and matching those made the check cry wolf on twelve
        # call sites that were never limits. A check nobody trusts is worse
        # than no check.
        pats = (r"""request\.args\.get\(\s*["'](limit|items|per_page|n)["']""",
                r"""(?:request\.form|body|data)\.get\("""
                r"""\s*["'](limit|per_page|page_size)["']""")
        for m in [m for pat in pats for m in re.finditer(pat, src)]:
            # Look BEHIND as well as ahead: the usual clamp wraps the read
            # rather than following it — max(1, min(500, int(request.args...)))
            # — so a forward-only window reports correctly-clamped code as
            # unclamped. A check that cries wolf gets ignored.
            line_start = src.rfind("\n", 0, m.start()) + 1
            window = src[line_start:m.start() + 260]
            if re.search(r"\bmin\(|\bmax\(|clamp", window):
                continue
            out.append({
                "file": rel, "module": _module_of(rel),
                "detail": f"?{m.group(1)}= is used without clamping. "
                          "?limit=-1 was a 500 on Postgres and a full table "
                          "dump on SQLite.",
                "fix": "from hub.webargs import clamp_int — it parses safely "
                       "and clamps both ends. Do not add another local "
                       "min()/max(); that is how two files ended up with "
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

def check_orphan_templates() -> list[dict]:
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

    rendered: set[str] = set()
    for py in ROOT.rglob("*.py"):
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
    for tpl in list(ROOT.glob("*/templates")) + list(ROOT.glob("modules/*/templates")):
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
    for js in ROOT.rglob("*.js"):
        if any(d in js.parts for d in SKIP_DIRS) or "node_modules" in js.parts:
            continue
        for ref in re.findall(r"[\w./-]+\.html",
                              js.read_text(encoding="utf-8", errors="ignore")):
            rendered.add(ref.split("/")[-1])

    out = []
    for f in sorted(files):
        if f.name in rendered:
            continue
        rel = f.relative_to(ROOT).as_posix()
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


def check_provider_key_drift() -> list[dict]:
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
    """
    try:
        from .config import ALIASES
    except Exception:                               # noqa: BLE001
        return []
    if not ALIASES:
        return []
    alias_of = {name: names for names in ALIASES.values() for name in names}

    out, seen = [], set()
    for rel, src in _sources():
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



CHECKS = [
    ("ghl_scope_coverage", "A GHL write with no scope declared for it", "high",
     check_ghl_scope_coverage),
    ("pdf_resource_type", "PDF uploaded as an image type", "high", check_pdf_resource_type),
    ("convert_without_resize", "Converts without resizing", "high", check_convert_without_resize),
    ("untracked_openai", "OpenAI spend not recorded", "medium", check_untracked_openai),
    ("untracked_provider_usage", "ElevenLabs, Cloudinary or Google usage not recorded",
     "medium", check_untracked_provider_usage),
    ("silent_modules", "Modules that never log", "medium", check_silent_modules),
    ("unclamped_limits", "Unclamped query limits", "medium", check_unclamped_limits),
    ("shadowed_routes", "Routes hidden behind a mount", "high", check_shadowed_routes),
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
    ("unbacked_json", "JSON on the disk with no backup", "medium", check_unbacked_json),
    ("stale_json_exemptions", "Unbacked-JSON exemption names a missing file",
     "medium", check_stale_json_exemptions),
    ("creative_medium_drift", "Creative gate lost a rate-card product", "high",
     check_creative_medium_drift),
    ("creative_spec_disagreement", "Creative gate and spec kit disagree", "high",
     check_creative_spec_disagreement),
    ("creative_kit_drift", "Spec numbers differ from the kit we publish", "high",
     check_creative_kit_drift),
    ("creative_kit_coverage",
     "A section of the published kit nobody has declared", "high",
     check_creative_kit_coverage),
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
    ("stale_work_exemptions", "A not-a-deliverable exemption outlived its "
     "call site", "medium", lambda: [
         {"file": "hub/client_brand.py", "module": m,
          "detail": (f"NOT_WORK names {m!r}, which no longer logs against a "
                     "client — the exemption now covers whatever is written "
                     "under that name next"),
          "fix": f"Drop {m!r} from NOT_WORK."}
         for m in _client_brand.stale_work_exemptions()]),
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
