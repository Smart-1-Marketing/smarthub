"""The native AudioGo pull, config-driven until the Reporting API spec arrives.

AudioGo publishes its Reporting API as a PDF (``audiogo.com/how-to/
audiogo-reporting-api``) that has been requested and has not arrived, so
nothing here knows the API's real shape yet. The endpoint path, the auth
header, the date-range parameters and the response field map all live in
``audiogo_map.py`` as clearly marked placeholders, and this module only
reads them: when the spec lands, the map is corrected and no line here
changes. ``/reports/audiogo-check`` is the page that correction is made
from -- it calls the configured endpoint for yesterday and prints the raw
JSON keys that came back.

``AUDIOGO_API_KEY`` is the key; ``AUDIOGO_API_BASE`` the origin. The key is
never logged, never on a result and never in an error -- ``_redact()``
strips it from any provider message before it leaves this module.

Rows land as ``platform="audiogo"``, ``source="native"``. AudioGo's
completion figure is listens / LTR rather than video completes: it lands in
the fact table's one ``completes`` column (the client tile reads "Listens"
for an audio platform) and every audio figure also rides in ``extras``
under its own name. A CSV export from the AudioGo UI goes through
``parsers/audiogo_csv.py`` and lands as ``source="csv"``.

``pull()`` never raises: a map that does not resolve against what the
platform answered is a refusal by name on the watermark, so ``/reports/``
says which field is missing rather than filing nothing quietly.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import requests

from . import audiogo_map, store

log = logging.getLogger(__name__)

TIMEOUT = 60
DAYS = 14
# Render carries the key as AUDIO_GO_API; the docs said AUDIOGO_API_KEY. Both
# spellings are read, first non-empty wins.
KEY_ENV = ("AUDIOGO_API_KEY", "AUDIO_GO_API", "AUDIO_GO_API_KEY", "AUDIOGO_API")
NOT_CONFIGURED = "not configured: AUDIOGO_API_KEY (or AUDIO_GO_API) unset"


def _first_env(names) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def cfg() -> dict:
    return {"key": _first_env(KEY_ENV), **audiogo_map.config()}


def missing() -> list[str]:
    c = cfg()
    out = []
    if not c["key"]:
        out.append("AUDIOGO_API_KEY")
    if not _first_env(("AUDIOGO_API_BASE", "AUDIO_GO_API_BASE")):
        out.append("AUDIOGO_API_BASE")
    return out


def configured() -> bool:
    return not missing()


def _redact(text: str) -> str:
    key = cfg()["key"]
    s = str(text or "")
    if key and key in s:
        s = s.replace(key, "[AUDIOGO key redacted]")
    return s[:500]


class AudioGoError(Exception):
    """A refusal from the platform, already redacted."""


def headers() -> dict:
    c = cfg()
    return {c["auth_header"]: f"{c['auth_prefix']}{c['key']}", "Accept": "application/json"}


def request_shape(start: date, end: date) -> dict:
    """The call as it would be made -- method, URL, query, header NAME --
    for the check page and the test. Never the key."""
    c = cfg()
    params = {c["date_params"]["start"]: start.isoformat(),
              c["date_params"]["end"]: end.isoformat(), **c["extra_params"]}
    return {"method": c["method"], "url": c["base"] + c["path"], "params": params,
            "auth_header": c["auth_header"]}


# ---------------------------------------------------------------------------
# HTTP -- one seam, so a test can stand in for the platform
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict, params: dict, timeout: int = TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.request(method, url, headers=headers, params=params, timeout=timeout)


def call(start: date, end: date):
    """The configured endpoint for a date range: the decoded JSON body."""
    c = cfg()
    if not c["key"]:
        raise AudioGoError(NOT_CONFIGURED)
    shape = request_shape(start, end)
    try:
        resp = _http(shape["method"], shape["url"], headers=headers(), params=shape["params"])
    except requests.RequestException as exc:
        raise AudioGoError(_redact(f"AudioGo could not be reached: {type(exc).__name__}"))
    if resp.status_code >= 400:
        raise AudioGoError(_redact(f"HTTP {resp.status_code} on {c['path']}: {(resp.text or '')[:200]}"))
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        raise AudioGoError("AudioGo answered with something that is not JSON")


# ---------------------------------------------------------------------------
# Reading the response through the map
# ---------------------------------------------------------------------------

def _dig(obj, path: str | None):
    """A dotted path into nested dicts; None where any step is missing."""
    if path is None or path == "":
        return obj
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def rows_of(body) -> list:
    """The list of row objects the map says the body carries."""
    c = audiogo_map.config()
    rows = _dig(body, c["rows_path"]) if c["rows_path"] else body
    if isinstance(rows, dict):
        # A body keyed by campaign or by day is flattened to its values.
        rows = list(rows.values())
    return [r for r in (rows or []) if isinstance(r, dict)] if isinstance(rows, list) else []


def keys_of(body, limit: int = 60) -> dict:
    """What the response actually holds, for the check page: the top-level
    keys, the row keys the map's rows_path reaches, and one sample row with
    its values -- nothing redacted but the key, which never reaches a
    body."""
    top = sorted(body.keys()) if isinstance(body, dict) else [f"({type(body).__name__})"]
    rows = rows_of(body)
    row_keys: list[str] = []
    for r in rows[:50]:
        for k in r:
            if k not in row_keys:
                row_keys.append(k)
    return {"top_level": top[:limit], "rows": len(rows), "row_keys": row_keys[:limit],
            "sample": rows[0] if rows else None}


def check_map(body) -> dict:
    """Does the field map resolve against this body? ``{"resolved": bool,
    "missing": [field names], "rows": n}``."""
    c = audiogo_map.config()
    rows = rows_of(body)
    if not rows:
        return {"resolved": False, "missing": list(audiogo_map.required_fields(c["fields"])),
                "rows": 0, "why": f"no rows under rows_path {c['rows_path']!r}"}
    sample = rows[0]
    missing = [c["fields"][k] for k in audiogo_map.REQUIRED
               if c["fields"].get(k) and _dig(sample, c["fields"][k]) is None]
    return {"resolved": not missing, "missing": missing, "rows": len(rows), "why": ""}


def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").replace("$", "")) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def to_facts(body, source: str = "native") -> dict:
    """``{"rows": [...], "skipped": n, "error": str}`` from a response body."""
    c = audiogo_map.config()
    f = c["fields"]
    chk = check_map(body)
    if not chk["resolved"]:
        return {"rows": [], "skipped": 0,
                "error": ("the AudioGo field map does not resolve: missing "
                          + ", ".join(chk["missing"]) + (f" ({chk['why']})" if chk.get("why") else "")
                          + " -- correct audiogo_map.py from /reports/audiogo-check")}
    out, skipped = [], 0
    for r in rows_of(body):
        day = store.parse_date(_dig(r, f["date"])) if _dig(r, f["date"]) else None
        acct = str(_dig(r, f["account_id"]) or "").strip()
        cid = str(_dig(r, f["campaign_id"]) or "").strip()
        if day is None or not acct or not cid:
            skipped += 1
            continue
        extras = {}
        if f.get("account_name") and _dig(r, f["account_name"]) is not None:
            extras["advertiser_name"] = str(_dig(r, f["account_name"]))
        for k in ("listens", "completed_listens", "ltr"):
            if f.get(k) and _dig(r, f[k]) is not None:
                extras[k] = _num(_dig(r, f[k])) if k == "ltr" else int(_num(_dig(r, f[k])))
        row = {
            "platform": "audiogo", "source": source, "date": day,
            "account_id": acct, "campaign_id": cid,
            "campaign_name": str(_dig(r, f["campaign_name"]) or "").strip() if f.get("campaign_name") else "",
            "spend": round(_num(_dig(r, f["spend"])) / (c["spend_divisor"] or 1), 2) if f.get("spend") else 0,
            "impressions": int(_num(_dig(r, f["impressions"]))) if f.get("impressions") else 0,
            "clicks": int(_num(_dig(r, f["clicks"]))) if f.get("clicks") else 0,
            "conversions": _num(_dig(r, f["conversions"])) if f.get("conversions") else 0,
            "extras": extras,
        }
        # Listens are the completion figure: completed listens where the
        # platform reports them, else listens.
        if "completed_listens" in extras:
            row["completes"] = extras["completed_listens"]
        elif "listens" in extras:
            row["completes"] = extras["listens"]
        out.append(row)
    return {"rows": out, "skipped": skipped, "error": ""}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def pull(days: int = DAYS, today: date | None = None) -> dict:
    out = {"ok": False, "rows": 0, "skipped": 0, "error": ""}
    miss = missing()
    if miss:
        out["error"] = "not configured: " + ", ".join(miss) + " unset"
        return out
    today = today or date.today()
    start = today - timedelta(days=max(1, int(days)) - 1)
    try:
        body = call(start, today)
        parsed = to_facts(body, source="native")
        if parsed["error"]:
            raise AudioGoError(parsed["error"])
        out["skipped"] = parsed["skipped"]
        out["rows"] = store.upsert_rows(parsed["rows"]) if parsed["rows"] else 0
        out["ok"] = True
        store.record_sync("audiogo", rows=out["rows"], error="", source="native")
    except AudioGoError as exc:
        out["error"] = _redact(str(exc))
        store.record_sync("audiogo", rows=0, error=out["error"], source="native")
    except Exception as exc:                # noqa: BLE001 - one platform, not the job
        log.exception("reports: AudioGo pull failed")
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
        store.record_sync("audiogo", rows=0, error=out["error"], source="native")
    return out


def check(today: date | None = None) -> dict:
    """What /reports/audiogo-check shows: the call as configured, the raw
    keys the endpoint answered with for yesterday, and whether the map
    resolves. Never raises; never carries the key."""
    today = today or date.today()
    day = today - timedelta(days=1)
    out = {"configured": configured(), "missing": missing(), "day": day.isoformat(),
           "request": request_shape(day, day), "map": audiogo_map.config(),
           "answer": None, "resolves": None, "error": ""}
    if not out["configured"]:
        out["error"] = "not configured: " + ", ".join(out["missing"]) + " unset"
        return out
    try:
        body = call(day, day)
        out["answer"] = keys_of(body)
        out["resolves"] = check_map(body)
    except AudioGoError as exc:
        out["error"] = _redact(str(exc))
    except Exception as exc:                # noqa: BLE001
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
    return out


def status() -> dict:
    miss = missing()
    sync = store.sync_status().get("audiogo") or {}
    native = sync if sync.get("source") == "native" else {}
    if miss:
        line = "AudioGo: not configured: " + ", ".join(miss) + " unset"
    else:
        line = "AudioGo: configured (field map is placeholder until the spec lands), last pull " \
               + (native.get("last_run_at") or "never")
        if native.get("error"):
            line += f" -- {native['error']}"
    return {"configured": not miss, "connected": not miss, "missing": miss, "line": line,
            "last_pull": native.get("last_run_at"), "last_rows": native.get("rows"),
            "last_error": native.get("error") or "", "check_page": "/reports/audiogo-check"}
