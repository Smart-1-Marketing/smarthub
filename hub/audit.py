"""Suite-wide append-only activity log (JSONL).

Every module writes through here so the Hub has ONE attributed history:
logins, GHL account create/delete, etc.  Point AUDIT_LOG_PATH at a file on
the Render persistent disk (/var/data) so history survives deploys.
"""
import json
import os
import threading
from datetime import datetime, timezone

_lock = threading.Lock()


def _path() -> str:
    """Where the activity log lives.

    `AUDIT_LOG_PATH` still wins -- it names one file rather than a root, so it
    is the more specific answer. Everything else defers to
    `jsonstore.data_root()`, which is *the* place that decides where persistent
    files live and whose own docstring names this failure: "every module had
    its own copy of this expression. They all agreed, which is luck rather
    than design: the moment one of them disagreed, its files would land
    somewhere the backup sweep never looks."

    This was that copy, and it did disagree -- on `HUB_DATA_DIR`, which
    data_root() reads first and this did not read at all. Nothing moves on
    Render, where HUB_DATA_DIR is unset and /var/data is mounted; what changes
    is a test that sets it, which used to be handed the real shared log.

    Never raises: this is the log, and a log that can break a boot is worse
    than one in the wrong place.
    """
    p = os.environ.get("AUDIT_LOG_PATH")
    if p:
        return p
    try:
        from . import jsonstore
        return os.path.join(jsonstore.data_root(), "hub-audit.log.jsonl")
    except Exception:  # noqa: BLE001 — fall back to the expression it replaced
        if os.path.isdir("/var/data"):
            return "/var/data/hub-audit.log.jsonl"
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hub-audit.log.jsonl")


def log(module: str, type_: str, actor: str | None = None, **extra) -> None:
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "module": module,
        "type": type_,
    }
    if actor:
        entry["actor"] = str(actor)[:60]
    entry.update({k: v for k, v in extra.items() if v is not None})
    try:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # best-effort — never break the action because logging failed


def read(limit: int = 300, module: str | None = None) -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if module and e.get("module") != module:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# v7 additions — module contract, rotation, and read performance.
#
# Two problems this closes:
#
#   1. Every module had copy-pasted the same defensive wrapper:
#          try: from hub import audit as hub_audit
#          except Exception: hub_audit = None
#      ...and ads_builder went further, doing getattr(hub_audit, fn_name) to
#      guess the function name. That defensiveness existed because Scans once
#      called hub_audit.record(), which does not exist, inside a bare
#      `except: pass` — so no scan reached /activity for the module's entire
#      life, silently. A named contract removes the guessing.
#
#   2. read() loaded the whole JSONL into memory and reversed it. Fine at a few
#      thousand rows, not at a few million. It now tails the file.
# ---------------------------------------------------------------------------

_REGISTERED: dict[str, str] = {}


def for_module(name: str, actor_fn=None):
    """Return a logger bound to one module.

        log = audit.for_module("scans", actor_name)
        log("scan_started", domain=domain)

    Registers the module so /health can report anything that never logs.
    """
    _REGISTERED.setdefault(name, "registered")

    def _log(type_: str, **extra) -> None:
        actor = None
        if actor_fn is not None:
            try:
                actor = actor_fn()
            except Exception:               # noqa: BLE001
                actor = None
        log(name, type_, actor=actor, **extra)

    return _log


# A module whose activity is filed under a name that is not its directory's,
# and whose logging therefore lives outside that directory.
#
# The Display Ad Builder is the only one, and it is the only one because it is
# the only module here that is not Python: `modules/ad_builder` is a TypeScript
# renderer, and its Hub-side half — the client join, the proxy, the audit
# entries — is hub/ad_builder_link.py and hub/ad_builder_proxy.py. Everything
# it writes is filed under "display_ads", the name on the tile, on the
# blueprint, on every help key and on every lead it has ever captured.
#
# It is declared rather than renamed. Renaming the log name to match the
# directory would orphan every entry already written and every Client 360 card
# reading them, to make a static check happy about a string.
#
# hub/integrity.py's silent-module check reads this, so a module listed here is
# looked for by the name it actually logs under, anywhere in the tree.
LOG_NAMES: dict[str, str] = {
    "ad_builder": "display_ads",
    # modules/utm_builder logs under `utm`. Unlike the entry above this is not
    # a module written in another language -- it is simply a shorter name
    # somebody chose -- and it went undeclared, so `client_brand.WORK_KINDS`
    # was keyed on the directory name instead and `work_log()` dropped every
    # row the tool wrote. Declared rather than renamed for the same reason as
    # display_ads: the rows already on disk carry `utm`, and renaming the call
    # site to match a table would orphan all of them to make a string tidy.
    "utm_builder": "utm",
}

# A module that deliberately writes no activity row, and why.
#
# This exists because of the way the silent-module check used to be satisfied.
# It asked whether the *string* "for_module(" appeared in a module's source --
# so seven modules that imported a logger, bound it to a name, wrapped it in a
# no-op fallback and then called it nowhere all read as modules that log.
# Every one of them had a comment above the import saying why logging mattered
# there. pdf_optimizer's said "work that isn't logged is work nobody can point
# to later"; page_image_optimizer's and sites_admin's said "an unattributable
# change to a client's account is one nobody can explain later". All three
# were true, and none of them wrote a row. That is the declared-but-unwired
# integration point this codebase has now found in RECORD_HOOK, io_creative,
# manifest(), thumb_url(), mark_pushed() and check_limits() -- wearing the
# activity log.
#
# The check reads a CALL now, through the AST, so an import can no longer
# silence it. Which leaves the honest remainder: a module whose work genuinely
# does not belong in the activity log. That is a decision, so it is written
# down here with its reason rather than left as a dangling import that
# happens to keep a check quiet -- and integrity.py fails on an entry naming a
# module that no longer exists, the rule check_stale_json_exemptions() works
# to, because an exemption that outlives what it exempted goes on covering
# whatever is written at that path next.
NO_ACTIVITY: dict[str, str] = {
    "calculators": (
        "What this module produces is a LEAD, not client work: a stranger on "
        "somebody else's website types into a public estimate box. Those go "
        "through hub/leads.py, which is the one store, delivery and panel for "
        "a prospect -- the rule modules/scans/leads.py gives at length. An "
        "activity row per public estimate would file hundreds of strangers "
        "into a log whose whole purpose is what we did for a CLIENT, and "
        "would put a prospect on a client 360 record they belong to no part "
        "of. The staff-facing internal calculator deliberately stores nothing "
        "at all, so there is nothing there to attribute either."
    ),
}


def registered_modules() -> list[str]:
    return sorted(_REGISTERED)


def silent_modules(expected: list[str]) -> list[str]:
    """Modules that were expected to log and never have. Boot-time check."""
    seen = {e.get("module") for e in read(limit=5000)}
    return sorted(m for m in expected if m not in seen)


def tail(limit: int = 300, module: str | None = None,
         type_: str | None = None) -> list[dict]:
    """read(), but without loading the entire log into memory."""
    path = _path()
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    # ~400 bytes/row; read generously then filter.
    window = min(size, max(limit, 100) * 800 + 65536)
    try:
        with open(path, "rb") as fh:
            fh.seek(size - window)
            chunk = fh.read().decode("utf-8", "ignore")
    except OSError:
        return []
    lines = chunk.splitlines()
    if window < size and lines:
        lines = lines[1:]                   # drop the partial first row
    out = []
    for ln in reversed(lines):
        if not ln.strip():
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if module and e.get("module") != module:
            continue
        if type_ and e.get("type") != type_:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def _route_methods(fn) -> set:
    """The HTTP methods a Flask view is registered for, from its decorators."""
    import ast as _ast
    methods, is_route = set(), False
    for d in fn.decorator_list:
        if not (isinstance(d, _ast.Call) and isinstance(d.func, _ast.Attribute)):
            continue
        if d.func.attr == "route":
            is_route = True
            named = False
            for kw in d.keywords:
                if kw.arg == "methods":
                    methods |= {e.value.upper() for e in kw.value.elts
                                if isinstance(e, _ast.Constant)}
                    named = True
            if not named:
                methods.add("GET")
        elif d.func.attr in ("get", "post", "put", "delete", "patch"):
            is_route = True
            methods.add(d.func.attr.upper())
    return methods if is_route else set()


def write_route_attribution(source: str) -> dict:
    """Which of a module's write routes record who did the work, and which do not.

    ``/api/integrity``'s silent-module check asks whether a module logs **at
    all**, and one call site satisfies it — the same shape as the check that
    read the *string* ``for_module(`` and counted the binding. So a module can
    be loudly attributable about a quarter of its work and pass: Sites Admin
    recorded deleting a client's website and not creating one, and Google
    Finder recorded deploying a tag and not deploying a pixel into the same
    container. This is that question asked one level finer.

    Read through the **AST**, never by matching text: the two modules this was
    written for both name ``_audit`` in comments explaining why it had gone
    uncalled, and a check that matches the explanation reports the fix as the
    defect — the rule ``hub/config.py``'s drift check gives at length.

    Returns ``{"logs": [...], "silent": [...], "declared": {name: reason}}``.
    A module declares the writes that deliberately record nothing in its own
    ``HOUSEKEEPING_ROUTES``, so the remainder is a decision somebody made
    rather than one nobody noticed — and an entry naming a route that is gone,
    or one that has since started logging, is a caller's to reject.
    """
    import ast as _ast
    tree = _ast.parse(source)
    logs, silent, declared = [], [], {}

    # The module's own wrapper, resolved from its **definition** rather than
    # guessed from its name. `_audit` and `log` were hard-coded, and
    # `modules/seo_images` calls its wrapper `_log` — so a module that records
    # seven of its writes read as recording none, which is a check inventing
    # findings rather than missing them, and the fastest way to have one
    # switched off. `check_work_kinds()` had to learn the same lesson: a bare
    # `log()` is a module's own wrapper whose first argument is the event, and
    # counting only a direct `audit.log(...)` dropped four modules entirely.
    #
    # A wrapper is a function in this file that itself reaches the shared
    # logger, however it is spelled — `audit.log(...)`, `hub_audit.log(...)`,
    # or a name bound from `audit.for_module(...)`.
    #
    # And **reaching it through another wrapper is still reaching it**, which
    # is the half the first version left out: it counted a function calling
    # `audit.log(...)` by attribute and stopped, so a module that binds
    # `_cb_log = audit.for_module(...)` and then wraps *that* in a helper had
    # every route calling the helper reported silent. Four routes read that
    # way — the Commercial Builder's `submit_render`, `send_for_review` and
    # `client_decide`, and `image_audit.api_image_attach_many` — every one of
    # them recording its work perfectly well. That is a check inventing
    # findings rather than missing them, the failure the paragraph above
    # already names once, and it is worse than a gap here: a module triaged
    # on this answer would declare a logging route as housekeeping.
    #
    # So the set is closed rather than gathered in one pass. It terminates
    # because a pass that adds nothing stops it, and a function is added at
    # most once — mutual recursion between two helpers settles rather than
    # spinning.
    wrappers = {"_audit", "log"}
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            v = node.value
            if (isinstance(v, _ast.Call) and isinstance(v.func, _ast.Attribute)
                    and v.func.attr == "for_module"):
                for t in node.targets:
                    if isinstance(t, _ast.Name):
                        wrappers.add(t.id)

    def _reaches_logger(fn) -> bool:
        for inner in _ast.walk(fn):
            if not isinstance(inner, _ast.Call):
                continue
            f = inner.func
            if (isinstance(f, _ast.Attribute) and f.attr == "log"
                    and "audit" in getattr(f.value, "id", "").lower()):
                return True
            if isinstance(f, _ast.Name) and f.id in wrappers:
                return True
        return False

    defs = [n for n in _ast.walk(tree)
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))]
    grew = True
    while grew:
        grew = False
        for node in defs:
            if node.name not in wrappers and _reaches_logger(node):
                wrappers.add(node.name)
                grew = True

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign) and any(
                getattr(t, "id", "") == "HOUSEKEEPING_ROUTES" for t in node.targets):
            if isinstance(node.value, _ast.Dict):
                declared = {k.value: v.value
                            for k, v in zip(node.value.keys, node.value.values)
                            if isinstance(k, _ast.Constant)
                            and isinstance(v, _ast.Constant)}

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.FunctionDef):
            continue
        if not (_route_methods(node) & {"POST", "PUT", "DELETE", "PATCH"}):
            continue
        writes_a_row = any(
            isinstance(c, _ast.Call) and (
                (isinstance(c.func, _ast.Name) and c.func.id in wrappers)
                or (isinstance(c.func, _ast.Attribute) and c.func.attr == "log"
                    and "audit" in getattr(c.func.value, "id", "").lower()))
            for c in _ast.walk(node))
        (logs if writes_a_row else silent).append(node.name)

    return {"logs": sorted(logs), "silent": sorted(silent), "declared": declared}


def rotate(max_mb: int = 64, keep: int = 5) -> bool:
    """Roll the log when it gets large. Called nightly by the maintenance job.

    An append-only file with no rotation is a slow-motion outage: it fills the
    1 GB Render disk that also holds uploaded assets.
    """
    path = _path()
    try:
        if os.path.getsize(path) < max_mb * 1024 * 1024:
            return False
    except OSError:
        return False
    with _lock:
        for i in range(keep - 1, 0, -1):
            older, newer = f"{path}.{i}", f"{path}.{i-1}" if i > 1 else path
            if os.path.exists(newer):
                try:
                    os.replace(newer, older)
                except OSError:
                    pass
        try:
            open(path, "w").close()
        except OSError:
            return False
    return True
