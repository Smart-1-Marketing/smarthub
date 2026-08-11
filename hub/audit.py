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
