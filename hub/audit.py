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
    p = os.environ.get("AUDIT_LOG_PATH")
    if p:
        return p
    # Prefer the persistent disk when mounted, fall back to local ./data.
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
LOG_NAMES: dict[str, str] = {"ad_builder": "display_ads"}


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
