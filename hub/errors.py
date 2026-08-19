"""Hub-wide error log — every 500, traceback and module failure lands here so
System Status can show what actually went wrong.

JSONL file on the persistent disk (survives deploys); capped read, newest first.
"""
import datetime
import json
import os
import threading
import traceback as _tb

_lock = threading.Lock()
MAX_BYTES = 2 * 1024 * 1024      # rotate at ~2 MB so the log never grows unbounded


def _path() -> str:
    override = os.environ.get("ERROR_LOG_PATH")
    if override:
        return override
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "hub-errors.log.jsonl")


def log(source: str, message: str, path: str = "", status: int = 0,
        trace: str = "", actor: str = ""):
    entry = {
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "message": str(message)[:500],
        "path": path,
        "status": status,
        "trace": str(trace)[:4000],
        "actor": actor or "",
    }
    try:
        p = _path()
        with _lock:
            if os.path.exists(p) and os.path.getsize(p) > MAX_BYTES:
                # keep the newest half on rotation
                with open(p, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                with open(p, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[len(lines) // 2:])
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def log_exception(source: str, exc: BaseException, path: str = "", actor: str = ""):
    log(source, f"{type(exc).__name__}: {exc}", path=path, status=500,
        trace="".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
        actor=actor)


def read(limit: int = 100) -> list[dict]:
    try:
        with open(_path(), encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit * 2:]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def clear():
    try:
        os.remove(_path())
    except OSError:
        pass


class ErrorMirror:
    """WSGI middleware: records any 5xx response (path + status + a snippet of
    the body when it's text) without altering the response."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        state = {}

        def sr(status, headers, exc_info=None):
            state["status"] = status
            state["ctype"] = next((v for k, v in headers if k.lower() == "content-type"), "")
            return start_response(status, headers, exc_info)

        path = environ.get("PATH_INFO", "")
        try:
            chunks = []
            for chunk in self.app(environ, sr):
                chunks.append(chunk)
            status = state.get("status", "")
            if status[:1] == "5":
                snippet = ""
                if "text" in state.get("ctype", "") or "json" in state.get("ctype", ""):
                    try:
                        import re
                        body = b"".join(chunks[:4]).decode("utf-8", "replace")
                        snippet = re.sub(r"<[^>]+>", " ", body)
                        snippet = " ".join(snippet.split())[:600]
                    except Exception:  # noqa: BLE001
                        snippet = ""
                log("http", snippet or f"HTTP {status}", path=path,
                    status=int(status.split()[0]))
            return chunks
        except Exception as exc:  # noqa: BLE001 — log then let the server 500
            log_exception("wsgi", exc, path=path)
            raise
