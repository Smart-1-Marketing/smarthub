"""The native CallRail pull: a client's phone calls, by source, by day.

CallRail is call tracking, not media. The key (``CALLRAIL_API_KEY``) sees
the agency's accounts, each account holds one company per client, and
every inbound call is a record carrying the source that drove it. This
module reads those records for a window of days and files one fact row
per (company, source, day) as ``platform="callrail"``, ``source="native"``
-- spend, impressions and clicks all zero, because a call is an OUTCOME,
and the counts in ``extras`` under their own names: ``calls``,
``answered``, ``missed``, ``voicemail``, ``first_time_calls``,
``good_leads``, ``duration_seconds``. Never ``conversions``: a call folded
into the conversions a media platform reports is a number nobody can
explain to the client whose page it is on, and the client's page draws
calls as their own tile instead (``client_view.py``).

It is config-driven on the GroundTruth pattern (``docs/claude/53``): the
origin, the paths, the auth header, the parameter names and the field map
all live in ``callrail_map.py`` as clearly marked placeholders transcribed
from a reference the Hub's own environment cannot reach, and this module
only reads them. ``/reports/callrail-check`` is the page a correction is
made from: it lists the accounts the key sees and prints the raw keys of
one page of yesterday's calls, the caller's own details masked.

**The key is sent nowhere until a person names the host.** ``missing()``
counts ``CALLRAIL_API_BASE`` beside the key, so a deployment that has the
key and not the origin reads *not configured* on ``/reports/`` and
``/status`` with the variable named, and ``call()`` refuses before any
request is built. The pull runs nightly with nobody watching, and a
request to a host nobody confirmed carries the key in its header.

**Reading is paged, and a read that stopped short says so.** Calls come
in pages of up to 250; the pull follows ``total_pages`` up to
``MAX_PAGES`` per account and, past that, files what it read and stamps
the watermark with the account it stopped on rather than reporting a
clean night on a partial month. Calls read and none filed is a failure
by name, not an ``ok`` with zero rows (the StackAdapt finding in
``docs/claude/49``). Outbound calls are counted apart and never filed.

The key is never logged, never on a result and never in an error:
``_redact()`` strips it from any provider message before it leaves this
module. Every call is recorded under the ``callrail`` quota row.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta

import requests

from . import callrail_map, store

log = logging.getLogger(__name__)

TIMEOUT = 60
# Thirty days: a call does not restate, but the platform's own lead marking
# (good_lead) is a person's press days after the call, and a day read
# again carries it.
DAYS = 30
# Pages per account per pull, a ceiling on a nightly job's own time: 200
# pages of 250 is fifty thousand calls in a month for one account.
MAX_PAGES = 200
# Rows the map is judged against. The question is whether a NAME is real,
# and a name that is real is answered to by some call in a page of them.
MAP_SAMPLE = 50
KEY_ENV = "CALLRAIL_API_KEY"
BASE_ENV = "CALLRAIL_API_BASE"
ACCOUNT_ENV = "CALLRAIL_ACCOUNT_ID"
CHECK_PAGE = "/reports/callrail-check"
PLATFORM = "callrail"

# Keys on a call record that are about the caller rather than the call:
# masked on the check page, which is a staff screen and still not a place
# to print a stranger's phone number. Matched as substrings of the key.
PRIVATE_KEY_PARTS = ("phone", "customer", "recording", "transcri", "note", "email",
                     "waveform", "keypad", "highlight", "agent")


def _key() -> str:
    """The key, read at call time through hub/config.py under exactly the
    spelling set on Render -- no CALLRAIL_KEY twin, because ALIASES is only
    spellings in use. Standalone, os.environ is the same read."""
    try:
        from hub import config as _config
        return _config._s(KEY_ENV)
    except Exception:                                   # noqa: BLE001 - standalone
        return (os.environ.get(KEY_ENV) or "").strip()


def cfg() -> dict:
    return {"key": _key(), **callrail_map.config()}


def base_problem(base: str = "") -> str:
    """Why this origin cannot be sent the key, or "" -- the GroundTruth
    rule: the key travels in a header on a nightly unattended job, so the
    origin has to be one that encrypts it. Loopback over http is somebody
    testing against a stub on their own machine, and is allowed."""
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
                "unattended job. CallRail is reached over https.")
    return "has no scheme; it is an origin such as https://api.example.com"


def not_configured_line() -> str:
    """One sentence for what is owed, and never "unset" about a variable
    that is set."""
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
        return "not configured: " + ", ".join(unset) + " unset"
    return "not configured: " + "; ".join(
        ([", ".join(unset) + " unset"] if unset else []) + problems)


def missing() -> list[str]:
    """What has to be set before anything is called: the key, and the
    origin -- named apart, because a deployment with the key and no origin
    is the ordinary one and its sentence has to say which half is owed."""
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
        s = s.replace(key, f"[{KEY_ENV} redacted]")
    return s[:500]


def _redact_deep(obj):
    """``_redact`` through a nested structure, for anything this module
    hands a template. The map is configuration and holds ``{key}`` rather
    than the key -- but an override is a person typing a value into Render,
    and ``CALLRAIL_AUTH_FORMAT`` set to the finished header rather than the
    pattern would otherwise print the key onto a staff screen."""
    if isinstance(obj, str):
        return _redact(obj)
    if isinstance(obj, dict):
        return {k: _redact_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_deep(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_deep(v) for v in obj)
    return obj


class CallRailError(Exception):
    """A refusal from the platform, already redacted."""


def headers() -> dict:
    c = cfg()
    fmt = c["auth_format"] if "{key}" in c["auth_format"] else c["auth_format"] + "{key}"
    return {c["auth_header"]: fmt.replace("{key}", c["key"]), "Accept": "application/json"}


def accounts_request_shape() -> dict:
    """The accounts call as it would be made -- never the key. With no
    origin set the URL is drawn from the placeholder default and
    ``base_set`` says so."""
    c = cfg()
    base = c["base"] or c["base_default"]
    return {"method": c["method"], "url": base + c["accounts_path"], "params": {},
            "auth_header": c["auth_header"], "base_set": bool(c["base"])}


def request_shape(account_id: str, start: date, end: date, *, page: int = 1,
                  per_page: int | None = None) -> dict:
    """One page of calls for one account as it would be asked for -- method,
    URL, query, header NAME -- for the pull, the check page and the test.
    Never the key."""
    c = cfg()
    base = c["base"] or c["base_default"]
    path = c["calls_path"].replace("{account_id}", str(account_id or "{account_id}"))
    params = {c["date_params"]["start"]: start.isoformat(),
              c["date_params"]["end"]: end.isoformat(),
              c["page_params"]["page"]: int(page),
              c["page_params"]["per_page"]: int(per_page or c["per_page"])}
    if c["request_fields"]:
        params[c["fields_param"]] = c["request_fields"]
    return {"method": c["method"], "url": base + path, "params": params,
            "auth_header": c["auth_header"], "base_set": bool(c["base"])}


# ---------------------------------------------------------------------------
# HTTP -- one seam, so a test can stand in for the platform
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict, params: dict, timeout: int = TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.request(method, url, headers=headers, params=params, timeout=timeout)


def _record(url: str, *, api: str, ok: bool) -> None:
    """One row under the callrail quota; a recorder that raises must never
    cost the pull."""
    try:
        from hub import quotas
        quotas.record_callrail(url, module="reports", api=api, ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def call(shape: dict, *, api: str):
    """One configured request: the decoded JSON body. Refuses by name,
    before anything is built, while the key or the origin is unset."""
    if missing():
        raise CallRailError(not_configured_line())
    try:
        resp = _http(shape["method"], shape["url"], headers=headers(), params=shape["params"])
    except requests.RequestException as exc:
        _record(shape["url"], api=api, ok=False)
        raise CallRailError(_redact(f"CallRail could not be reached: {type(exc).__name__}"))
    _record(shape["url"], api=api, ok=resp.status_code < 400)
    if resp.status_code >= 400:
        path = shape["url"].split("://", 1)[-1].split("/", 1)[-1]
        raise CallRailError(_redact(f"HTTP {resp.status_code} on /{path}: {(resp.text or '')[:200]}"))
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        raise CallRailError("CallRail answered with something that is not JSON")


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


def _has(obj, path: str | None) -> bool:
    """Does this row carry this path at all -- a key that is present and
    ``null`` included? A name nobody answers to is absent from every row;
    a field the platform simply did not fill on one call is present and
    empty, and ``_dig`` renders both as ``None``. Telling them apart is
    the whole of what ``check_map`` is asking."""
    if path is None or path == "":
        return False
    cur = obj
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _list_under(body, path: str) -> list:
    rows = _dig(body, path) if path else body
    if isinstance(rows, dict):
        rows = list(rows.values())
    return [r for r in (rows or []) if isinstance(r, dict)] if isinstance(rows, list) else []


def rows_of(body) -> list:
    """The call records the map says the body carries."""
    return _list_under(body, callrail_map.config()["rows_path"])


def accounts_of(body) -> list[dict]:
    """``[{"id", "name"}]`` from the accounts body, through the map."""
    c = callrail_map.config()
    out = []
    for r in _list_under(body, c["accounts_rows_path"]):
        aid = str(_dig(r, c["account_fields"]["id"]) or "").strip()
        if aid:
            out.append({"id": aid, "name": str(_dig(r, c["account_fields"]["name"]) or "").strip()})
    return out


def total_pages_of(body) -> int:
    c = callrail_map.config()
    try:
        return max(1, int(_dig(body, c["total_pages_path"]) or 1))
    except (TypeError, ValueError):
        return 1


def _private(key: str) -> bool:
    k = str(key).lower()
    return any(part in k for part in PRIVATE_KEY_PARTS)


def mask_row(row: dict) -> dict:
    """A call record with the caller's own details replaced, for the check
    page: the key names stay, the values about a person do not."""
    return {k: ("[masked]" if _private(k) and v not in (None, "", False) else v)
            for k, v in row.items()}


def keys_of(body, limit: int = 80) -> dict:
    """What the response actually holds, for the check page: the top-level
    keys, the row keys the map's rows_path reaches, and one sample row
    with the caller's details masked."""
    top = sorted(body.keys()) if isinstance(body, dict) else [f"({type(body).__name__})"]
    rows = rows_of(body)
    row_keys: list[str] = []
    for r in rows[:50]:
        for k in r:
            if k not in row_keys:
                row_keys.append(k)
    return {"top_level": top[:limit], "rows": len(rows), "row_keys": row_keys[:limit],
            "total_pages": total_pages_of(body) if isinstance(body, dict) else None,
            "sample": mask_row(rows[0]) if rows else None}


def check_map(rows: list) -> dict:
    """Does the field map resolve against these call records? ``{"resolved":
    bool, "missing": [field names], "rows": n}``. A REQUIRED field whose
    name has been blanked is UNRESOLVED and named by the fact column it
    leaves unfilled -- the GroundTruth review's first finding."""
    c = callrail_map.config()
    if not rows:
        return {"resolved": False, "missing": list(callrail_map.required_fields(c["fields"])),
                "rows": 0, "why": f"no rows under rows_path {c['rows_path']!r}"}
    sample = rows[:MAP_SAMPLE]
    missing_ = []
    empty_ = []
    for k in callrail_map.REQUIRED:
        name = c["fields"].get(k)
        if not name:
            missing_.append(f"{k} (no response field is named for it)")
        elif not any(_has(r, name) for r in sample):
            missing_.append(name)
        elif all(_dig(r, name) in (None, "") for r in sample):
            empty_.append(name)
    why = ""
    if empty_ and not missing_:
        why = ("answered to, and empty on every call read: "
               + ", ".join(empty_) + " -- the name is real and the platform filled "
               "nothing in, so check it is the field you meant")
    return {"resolved": not missing_, "missing": missing_, "rows": len(rows),
            "sampled": len(sample), "empty": empty_, "why": why}


def _num(value) -> float:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "t", "y")
    return bool(value)


def slug(text: str) -> str:
    """A source name as a campaign id: lowercase, one hyphen between words,
    never empty. ``Google Ads`` and ``google ads`` are one source."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return (s or "no-source")[:120]


def to_facts(rows: list, source: str = "native") -> dict:
    """``{"rows": [...], "skipped": n, "outbound": n, "error": str}`` from
    call records: one fact row per (company, source, day), counts in
    extras under their own names, nothing in conversions."""
    c = callrail_map.config()
    f = c["fields"]
    chk = check_map(rows)
    if rows and not chk["resolved"]:
        return {"rows": [], "skipped": 0, "outbound": 0,
                "error": ("the CallRail field map does not resolve: missing "
                          + ", ".join(chk["missing"]) + (f" ({chk['why']})" if chk.get("why") else "")
                          + f" -- correct callrail_map.py from {CHECK_PAGE}")}
    agg: dict[tuple, dict] = {}
    skipped = outbound = 0
    for r in rows:
        raw_day = _dig(r, f["date"])
        try:
            day = store.parse_date(str(raw_day)[:10]) if raw_day not in (None, "") else None
        except ValueError:
            day = None
        acct = str(_dig(r, f["account_id"]) or "").strip()
        source_name = str(_dig(r, f["campaign_id"]) or "").strip()
        if day is None or not acct:
            skipped += 1
            continue
        if f.get("direction"):
            direction = _dig(r, f["direction"])
            if direction is not None and str(direction).strip().lower() not in c["inbound_values"]:
                outbound += 1
                continue
        cid = slug(source_name)
        key = (acct, cid, day)
        a = agg.setdefault(key, {"calls": 0, "answered": 0, "missed": 0, "voicemail": 0,
                                 "first_time_calls": 0, "good_leads": 0, "duration_seconds": 0,
                                 "source": source_name or "(no source)", "company": ""})
        a["calls"] += 1
        answered = _truthy(_dig(r, f["answered"])) if f.get("answered") else None
        voicemail = _truthy(_dig(r, f["voicemail"])) if f.get("voicemail") else None
        if answered:
            a["answered"] += 1
        if voicemail:
            a["voicemail"] += 1
        if answered is False and not voicemail:
            a["missed"] += 1
        if f.get("first_call") and _truthy(_dig(r, f["first_call"])):
            a["first_time_calls"] += 1
        if f.get("lead_status") and str(_dig(r, f["lead_status"]) or "").strip().lower() in c["good_lead_values"]:
            a["good_leads"] += 1
        if f.get("duration"):
            a["duration_seconds"] += int(_num(_dig(r, f["duration"])))
        if f.get("account_name") and _dig(r, f["account_name"]) is not None and not a["company"]:
            a["company"] = str(_dig(r, f["account_name"])).strip()
    out = []
    for (acct, cid, day), a in sorted(agg.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        extras = {"calls": a["calls"], "source": a["source"]}
        if f.get("answered"):
            extras["answered"] = a["answered"]
            extras["missed"] = a["missed"]
        if f.get("voicemail"):
            extras["voicemail"] = a["voicemail"]
        if f.get("first_call"):
            extras["first_time_calls"] = a["first_time_calls"]
        if f.get("lead_status"):
            extras["good_leads"] = a["good_leads"]
        if f.get("duration"):
            extras["duration_seconds"] = a["duration_seconds"]
        if a["company"]:
            extras["advertiser_name"] = a["company"]
        out.append({
            "platform": PLATFORM, "source": source, "date": day,
            "account_id": acct, "campaign_id": cid, "campaign_name": a["source"],
            "spend": 0, "impressions": 0, "clicks": 0, "conversions": 0,
            "extras": extras,
        })
    return {"rows": out, "skipped": skipped, "outbound": outbound, "error": ""}


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------

def accounts() -> list[dict]:
    """The accounts the key can see, or the one CALLRAIL_ACCOUNT_ID pins
    (no call is made for a pinned account)."""
    c = cfg()
    if c["account_id"]:
        return [{"id": c["account_id"], "name": ""}]
    body = call(accounts_request_shape(), api="accounts")
    return accounts_of(body)


def read_calls(account_id: str, start: date, end: date) -> tuple[list, bool]:
    """Every call record for one account in the window, page by page.
    ``(rows, complete)``: ``complete`` is False when MAX_PAGES stopped the
    read short, and what was read is still returned."""
    rows: list = []
    page, pages = 1, 1
    while page <= pages:
        if page > MAX_PAGES:
            return rows, False
        body = call(request_shape(account_id, start, end, page=page), api="calls")
        rows.extend(rows_of(body))
        pages = total_pages_of(body)
        page += 1
    return rows, True


def pull(days: int = DAYS, today: date | None = None) -> dict:
    out = {"ok": False, "rows": 0, "skipped": 0, "outbound": 0, "calls": 0,
           "accounts": 0, "error": ""}
    if missing():
        out["error"] = not_configured_line()
        return out
    today = today or date.today()
    start = today - timedelta(days=max(1, int(days)) - 1)
    try:
        accts = accounts()
        out["accounts"] = len(accts)
        if not accts:
            raise CallRailError("the key sees no CallRail account; nothing to read")
        records: list = []
        short: list[str] = []
        for a in accts:
            rows, complete = read_calls(a["id"], start, today)
            records.extend(rows)
            if not complete:
                short.append(a["id"])
        out["calls"] = len(records)
        parsed = to_facts(records, source="native")
        if parsed["error"]:
            raise CallRailError(parsed["error"])
        out["skipped"], out["outbound"] = parsed["skipped"], parsed["outbound"]
        if records and not parsed["rows"] and parsed["skipped"]:
            # Records read and none filed is a failure by name, never an
            # ok with zero rows: the StackAdapt finding, one platform over.
            raise CallRailError(f"{len(records)} calls read and none filed: every one was "
                                f"skipped for a missing day or company -- see {CHECK_PAGE}")
        out["rows"] = store.upsert_rows(parsed["rows"]) if parsed["rows"] else 0
        if short:
            out["error"] = (f"read stopped at {MAX_PAGES} pages for account "
                            + ", ".join(short) + "; what was read is filed and the rest "
                            "of the window is not")
            store.record_sync(PLATFORM, rows=out["rows"], error=out["error"], source="native")
            return out
        out["ok"] = True
        store.record_sync(PLATFORM, rows=out["rows"], error="", source="native")
    except CallRailError as exc:
        out["error"] = _redact(str(exc))
        store.record_sync(PLATFORM, rows=out["rows"], error=out["error"], source="native")
    except Exception as exc:                # noqa: BLE001 - one platform, not the job
        log.exception("reports: CallRail pull failed")
        out["error"] = _redact(f"{type(exc).__name__}: {exc}")
        store.record_sync(PLATFORM, rows=out["rows"], error=out["error"], source="native")
    return out


def check(today: date | None = None) -> dict:
    """What /reports/callrail-check shows: the accounts the key sees, one
    page of yesterday's calls for the first (or pinned) account with the
    raw keys it answered with, and whether the map resolves. Never raises;
    never carries the key; calls nothing while the origin is unset."""
    today = today or date.today()
    day = today - timedelta(days=1)
    c = cfg()
    out = {"configured": configured(), "missing": missing(), "day": day.isoformat(),
           "accounts_request": accounts_request_shape(),
           "request": request_shape(c["account_id"] or "", day, day, per_page=25),
           "pinned": c["account_id"], "map": _redact_deep(callrail_map.config()),
           "accounts": None, "answer": None, "resolves": None, "error": ""}
    if not out["configured"]:
        out["error"] = not_configured_line()
        return out
    try:
        accts = accounts()
        out["accounts"] = accts
        if not accts:
            out["error"] = "the key sees no CallRail account, so no calls page was asked for"
            return out
        target = accts[0]["id"]
        out["request"] = request_shape(target, day, day, per_page=25)
        body = call(out["request"], api="calls")
        out["answer"] = keys_of(body)
        out["resolves"] = check_map(rows_of(body))
    except CallRailError as exc:
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
    sync = store.sync_status().get(PLATFORM) or {}
    native = sync if sync.get("source") == "native" else {}
    if miss == [BASE_ENV]:
        line = (f"CallRail: key set; {BASE_ENV} unset -- the API origin is not confirmed here "
                f"and the key is sent nowhere until it is. Set it and confirm the field map on {CHECK_PAGE}.")
    elif miss:
        line = "CallRail: " + not_configured_line()
    else:
        line = (f"CallRail: configured (field map is placeholder until confirmed on {CHECK_PAGE}), "
                "last pull " + (native.get("last_run_at") or "never"))
        if native.get("error"):
            line += f" -- {native['error']}"
    return {"configured": not miss, "connected": not miss, "missing": miss, "line": line,
            "last_pull": native.get("last_run_at"), "last_rows": native.get("rows"),
            "last_error": native.get("error") or "", "check_page": CHECK_PAGE}
