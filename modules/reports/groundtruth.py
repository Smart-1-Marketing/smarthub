"""The native GroundTruth pull: the key arrived before the document.

``GROUND_TRUTH_API`` is set on Render, under exactly that spelling, and the
API it unlocks is documented at api-docs.groundtruth.com -- a host the
Hub's own environment cannot reach, like every other GroundTruth host. So
this module is config-driven on the AudioGo pattern: the API origin, the
endpoint path, the auth header, the date-range parameters and the response
field map all live in ``groundtruth_map.py`` as clearly marked
placeholders, and this module only reads them. When the document can be
read, or the check page shows what the endpoint actually answers, the map
is corrected and no line here changes. ``/reports/groundtruth-check`` is
the page that correction is made from.

**The key is sent nowhere until a person names the host.** ``missing()``
counts ``GROUND_TRUTH_API_BASE`` beside the key, so a deployment that has
the key and not the origin reads *not configured* on ``/reports/`` and
``/status``, with the variable named, and ``call()`` refuses before any
request is built. A placeholder field name costs a refusal by name; a
placeholder host would hand the key, in a header, to whoever answers at an
address nobody confirmed -- and the pull runs nightly with nobody
watching.

Rows land as ``platform="groundtruth"``, ``source="native"``. **Visits are
the figure a geofencing buy is bought for**, and GroundTruth attributes
them for days after the impression -- a device seen at the store on
Thursday is credited to Monday's ad -- so the window is thirty days, the
same re-read Google and StackAdapt get, and a day read again a week later
carries more of them. They ride in ``extras`` under their own name and are
never folded into ``conversions``; the client's page draws them as a tile
of their own. The key is never logged, never on a result and never in an
error: ``_redact()`` strips it from any provider message before it leaves
this module.

Every call is recorded under the ``groundtruth`` quota row, so the usage
page can say what the pull and the check page spent; ``pull()`` never
raises, and a map that does not resolve against what the platform answered
is a refusal by name on the watermark rather than nothing filed quietly.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import requests

from . import groundtruth_map, store

log = logging.getLogger(__name__)

TIMEOUT = 60
# Thirty days, not fourteen: a visit is credited back to the impression it
# followed, so yesterday's row grows for a week after it is first read.
DAYS = 30
KEY_ENV = "GROUND_TRUTH_API"
BASE_ENV = "GROUND_TRUTH_API_BASE"
CHECK_PAGE = "/reports/groundtruth-check"


def _key() -> str:
    """The key, read at call time through hub/config.py under exactly the
    spelling set on Render -- no GROUNDTRUTH_API_KEY twin, because ALIASES
    is only spellings in use. Standalone, os.environ is the same read."""
    try:
        from hub import config as _config
        return _config._s(KEY_ENV)
    except Exception:                                   # noqa: BLE001 - standalone
        return (os.environ.get(KEY_ENV) or "").strip()


def cfg() -> dict:
    return {"key": _key(), **groundtruth_map.config()}


def base_problem(base: str = "") -> str:
    """Why this origin cannot be sent the key, or "".

    The key travels in an Authorization header on a job that runs nightly
    with nobody watching, so the origin has to be one that encrypts it. An
    ``http://`` value is a set variable that looks configured on every screen
    and puts a credential on the wire in clear -- refused here by name rather
    than discovered in a packet capture. Loopback is allowed, because that is
    somebody testing against a stub on their own machine.
    """
    value = (base or cfg()["base"] or "").strip()
    if not value:
        return ""
    if value.startswith("https://"):
        return ""
    host = value.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if value.startswith("http://") and host in ("localhost", "127.0.0.1", "::1"):
        return ""
    if value.startswith("http://"):
        return ("is http://, which would send the key in clear on a nightly "
                "unattended job. GroundTruth is reached over https.")
    return "has no scheme; it is an origin such as https://api.example.com"


def not_configured_line() -> str:
    """One sentence for what is owed, and never "unset" about a variable that
    is set. An origin that is set and cannot carry a credential is a
    correction, not an omission, and saying "unset" sends somebody to look at
    a variable they can plainly see has a value in it."""
    c = cfg()
    unset = [KEY_ENV] if not c["key"] else []
    problems = []
    if not c["base"]:
        unset.append(BASE_ENV)
    else:
        problem = base_problem(c["base"])
        if problem:
            problems.append(f"{BASE_ENV} {problem}")
    if not problems:
        # The ordinary sentence, unchanged: everything owed is simply unset.
        return "not configured: " + ", ".join(unset) + " unset"
    return "not configured: " + "; ".join(
        ([", ".join(unset) + " unset"] if unset else []) + problems)


def missing() -> list[str]:
    """What has to be set before anything is called: the key, and the
    origin -- named apart, because a deployment with the key and no origin
    is the ordinary one and its sentence has to say which half is owed.

    An origin that is set and cannot carry a credential counts as owed too:
    the key is sent nowhere until it is corrected."""
    c = cfg()
    out = []
    if not c["key"]:
        out.append(KEY_ENV)
    if not c["base"] or base_problem(c["base"]):
        out.append(BASE_ENV)
    return out


def configured() -> bool:
    return not missing()


def _redact(text: str) -> str:
    key = _key()
    s = str(text or "")
    if key and key in s:
        s = s.replace(key, "[GROUND_TRUTH_API redacted]")
    return s[:500]


class GroundTruthError(Exception):
    """A refusal from the platform, already redacted."""


def headers() -> dict:
    c = cfg()
    return {c["auth_header"]: f"{c['auth_prefix']}{c['key']}", "Accept": "application/json"}


def request_shape(start: date, end: date) -> dict:
    """The call as it would be made -- method, URL, query, header NAME --
    for the check page and the test. Never the key. With no origin set the
    URL is drawn from the placeholder default and ``base_set`` says so, so
    the check page can print the guess without ``call()`` ever using it."""
    c = cfg()
    params = {c["date_params"]["start"]: start.isoformat(),
              c["date_params"]["end"]: end.isoformat(), **c["extra_params"]}
    base = c["base"] or c["base_default"]
    return {"method": c["method"], "url": base + c["path"], "params": params,
            "auth_header": c["auth_header"], "base_set": bool(c["base"])}


# ---------------------------------------------------------------------------
# HTTP -- one seam, so a test can stand in for the platform
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict, params: dict, timeout: int = TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.request(method, url, headers=headers, params=params, timeout=timeout)


def _record(url: str, *, ok: bool) -> None:
    """One row under the groundtruth quota; a recorder that raises must
    never cost the pull."""
    try:
        from hub import quotas
        quotas.record_groundtruth(url, module="reports", api="report", ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def call(start: date, end: date):
    """The configured endpoint for a date range: the decoded JSON body.
    Refuses by name, before anything is built, while the key or the origin
    is unset."""
    miss = missing()
    if miss:
        raise GroundTruthError(not_configured_line())
    c = cfg()
    shape = request_shape(start, end)
    try:
        resp = _http(shape["method"], shape["url"], headers=headers(), params=shape["params"])
    except requests.RequestException as exc:
        _record(shape["url"], ok=False)
        raise GroundTruthError(_redact(f"GroundTruth could not be reached: {type(exc).__name__}"))
    _record(shape["url"], ok=resp.status_code < 400)
    if resp.status_code >= 400:
        raise GroundTruthError(_redact(f"HTTP {resp.status_code} on {c['path']}: {(resp.text or '')[:200]}"))
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        raise GroundTruthError("GroundTruth answered with something that is not JSON")


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
    c = groundtruth_map.config()
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
    c = groundtruth_map.config()
    rows = rows_of(body)
    if not rows:
        return {"resolved": False, "missing": list(groundtruth_map.required_fields(c["fields"])),
                "rows": 0, "why": f"no rows under rows_path {c['rows_path']!r}"}
    sample = rows[0]
    # A REQUIRED field whose name has been blanked is UNRESOLVED, not skipped.
    # Skipping it read as resolved, and _dig(row, None) answers the whole row
    # -- so the check page gave a green light to a map that then filed the
    # row's own dict repr into account_id, which is part of the fact key.
    # Named by the fact column here, since there is no response field to name.
    missing_ = []
    for k in groundtruth_map.REQUIRED:
        name = c["fields"].get(k)
        if not name:
            missing_.append(f"{k} (no response field is named for it)")
        elif _dig(sample, name) is None:
            missing_.append(name)
    return {"resolved": not missing_, "missing": missing_, "rows": len(rows), "why": ""}


def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").replace("$", "").replace("%", "")) \
            if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def to_facts(body, source: str = "native") -> dict:
    """``{"rows": [...], "skipped": n, "error": str}`` from a response body.
    Visits and the visit rate ride in extras under their own names; nothing
    here writes a visit into conversions or completes."""
    c = groundtruth_map.config()
    f = c["fields"]
    chk = check_map(body)
    if not chk["resolved"]:
        return {"rows": [], "skipped": 0,
                "error": ("the GroundTruth field map does not resolve: missing "
                          + ", ".join(chk["missing"]) + (f" ({chk['why']})" if chk.get("why") else "")
                          + f" -- correct groundtruth_map.py from {CHECK_PAGE}")}
    out, skipped = [], 0
    for r in rows_of(body):
        try:
            day = store.parse_date(_dig(r, f["date"])) if _dig(r, f["date"]) else None
        except ValueError:
            # store.parse_date raises on a value it cannot read rather than
            # answering None, so the skip two lines down was unreachable: one
            # "09/16/2026" among a thousand good rows threw out of this loop
            # and discarded the whole nightly pull with it.
            day = None
        acct = str(_dig(r, f["account_id"]) or "").strip()
        cid = str(_dig(r, f["campaign_id"]) or "").strip()
        if day is None or not acct or not cid:
            skipped += 1
            continue
        extras = {}
        if f.get("account_name") and _dig(r, f["account_name"]) is not None:
            extras["advertiser_name"] = str(_dig(r, f["account_name"]))
        if f.get("visits") and _dig(r, f["visits"]) is not None:
            extras["visits"] = int(_num(_dig(r, f["visits"])))
        if f.get("visit_rate") and _dig(r, f["visit_rate"]) is not None:
            extras["visit_rate"] = _num(_dig(r, f["visit_rate"]))
        out.append({
            "platform": "groundtruth", "source": source, "date": day,
            "account_id": acct, "campaign_id": cid,
            "campaign_name": str(_dig(r, f["campaign_name"]) or "").strip() if f.get("campaign_name") else "",
            "spend": round(_num(_dig(r, f["spend"])) / (c["spend_divisor"] or 1), 2) if f.get("spend") else 0,
            "impressions": int(_num(_dig(r, f["impressions"]))) if f.get("impressions") else 0,
            "clicks": int(_num(_dig(r, f["clicks"]))) if f.get("clicks") else 0,
            "conversions": _num(_dig(r, f["conversions"])) if f.get("conversions") else 0,
            "extras": extras,
        })
    return {"rows": out, "skipped": skipped, "error": ""}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def pull(days: int = DAYS, today: date | None = None) -> dict:
    out = {"ok": False, "rows": 0, "skipped": 0, "error": ""}
    miss = missing()
    if miss:
        out["error"] = not_configured_line()
        return out
    today = today or date.today()
    start = today - timedelta(days=max(1, int(days)) - 1)
    try:
        body = call(start, today)
        parsed = to_facts(body, source="native")
        if parsed["error"]:
            raise GroundTruthError(parsed["error"])
        out["skipped"] = parsed["skipped"]
        out["rows"] = store.upsert_rows(parsed["rows"]) if parsed["rows"] else 0
        out["ok"] = True
        store.record_sync("groundtruth", rows=out["rows"], error="", source="native")
    except GroundTruthError as exc:
        out["error"] = _redact(str(exc))
        store.record_sync("groundtruth", rows=0, error=out["error"], source="native")
    except Exception as exc:                # noqa: BLE001 - one platform, not the job
        log.exception("reports: GroundTruth pull failed")
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
        store.record_sync("groundtruth", rows=0, error=out["error"], source="native")
    return out


def check(today: date | None = None) -> dict:
    """What /reports/groundtruth-check shows: the call as configured, the
    raw keys the endpoint answered with for yesterday, and whether the map
    resolves. Never raises; never carries the key; calls nothing while the
    origin is unset."""
    today = today or date.today()
    day = today - timedelta(days=1)
    out = {"configured": configured(), "missing": missing(), "day": day.isoformat(),
           "request": request_shape(day, day), "map": groundtruth_map.config(),
           "answer": None, "resolves": None, "error": ""}
    if not out["configured"]:
        out["error"] = not_configured_line()
        return out
    try:
        body = call(day, day)
        out["answer"] = keys_of(body)
        out["resolves"] = check_map(body)
    except GroundTruthError as exc:
        out["error"] = _redact(str(exc))
    except Exception as exc:                # noqa: BLE001
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
    return out


def status() -> dict:
    """The line /reports/ prints. Three states rather than two, because a
    deployment holding the key and not the origin is the ordinary one and
    "not configured" alone would send somebody to check a key that is
    set."""
    miss = missing()
    sync = store.sync_status().get("groundtruth") or {}
    native = sync if sync.get("source") == "native" else {}
    if miss == [BASE_ENV]:
        line = (f"GroundTruth: key set; {BASE_ENV} unset -- the API origin is not known here "
                f"and the key is sent nowhere until it is. Set it and confirm the field map on {CHECK_PAGE}.")
    elif miss:
        line = "GroundTruth: " + not_configured_line()
    else:
        line = (f"GroundTruth: configured (field map is placeholder until confirmed on {CHECK_PAGE}), "
                "last pull " + (native.get("last_run_at") or "never"))
        if native.get("error"):
            line += f" -- {native['error']}"
    return {"configured": not miss, "connected": not miss, "missing": miss, "line": line,
            "last_pull": native.get("last_run_at"), "last_rows": native.get("rows"),
            "last_error": native.get("error") or "", "check_page": CHECK_PAGE}
