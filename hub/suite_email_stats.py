"""A client's own email campaigns, read from their Smart 1 Suite sub-account:
what was sent, how many were delivered, opened, clicked, bounced and
unsubscribed, as the Suite answered last night.

Everything runs on a **sub-account token** minted by
``hub.suite_accounts.token_for(client)`` -- the Email Creator rule
(modules/skills360/suite_email.py): the location id comes from the
client's own Suite link, never from an env var, so one client's numbers
cannot land under another's name. The Smart 1 credential that sends
display-ad proofs (hub/ad_proof_email.py) is not used here and must not
be: that reads Smart 1's own account, and this reads the client's.

Two wire calls, and which is the source of truth:

* **The campaign list, with its statistics.** ``GET /emails/schedule``
  with ``showStats=true`` -- HighLevel's published spec
  (apps/emails.json) says the flag returns delivered, opened and clicked
  counts and revenue per campaign, on ``emails/schedule.readonly``. One
  call per sub-account, up to a hundred campaigns. The spec does not
  name the statistics' field names, so ``_stats_of()`` reads the row
  tolerantly -- a ``stats``/``statistics`` object or the counts at the
  top level -- and keeps the raw statistics beside the reading so a
  mis-read key is fixed from what was stored, not re-pulled.
* **Per-campaign statistics.** ``GET /emails/locations/{loc}/campaigns/
  stats/email-campaigns/{id}`` on ``emails/stats.readonly`` -- the
  unified statistics API HighLevel's changelog announced for agency
  reporting. It is not in the published spec snapshot this Hub reads,
  so it is asked only for a campaign the list carried no statistics
  for, under a per-sweep cap, and a 404 there is recorded as "not
  offered" rather than as a failure.

Five rules, each a way this goes confidently wrong:

* **A reading is taken once a night, never on a page load.** The
  client's report page is opened by clients, and every read is a call
  against the sub-account's rate limit. ``sweep()`` runs on the
  scheduler's hourly tick and reads each linked client once inside the
  nightly window; every page reads the stored reading and prints its
  date. ``snapshot()`` on its own is a **button** and never a GET.
* **Not linked is not measured, never zero.** "No Suite sub-account is
  linked", "the app has not been consented with the scope", "the read
  failed" and "read last night" are four states a card draws apart --
  ``reading()`` names which -- because "no campaigns" printed over a
  401 would tell a rep the client never emailed anyone.
* **A missing scope is said in words before the first 401.** The two
  scopes are on hub/ghl_scopes.py's requested list; until the agency
  owner re-consents, ``scope_state()`` reports them missing and the card
  says so. HighLevel grants what it recognises and says nothing about
  the rest, so this is asked rather than discovered.
* **Every row is checked against the location it was asked for.** A
  campaign whose ``locationId`` names another sub-account is dropped
  and counted, the hub/client_email.py rule -- a reporting read that
  displays another client's campaign is worse than one that fails.
* **Only sent campaigns count.** Drafts, folders and scheduled-but-
  unsent rows are listed by the Suite and dropped here: a draft with
  zero opens is not a campaign nobody opened.

Stored through ``hub/jsonstore.py`` rather than a table, the places rule:
the readers are three Flask apps and a background thread. Keyed on the
client's **name**, the rule every overlay here works to.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timezone

import requests

log = logging.getLogger(__name__)

API_BASE = os.environ.get("GHL_API_BASE", "https://services.leadconnectorhq.com")
API_VERSION = os.environ.get("GHL_API_VERSION", "2021-07-28")
STATS_VERSION = "v3"           # what the unified statistics endpoint takes
TIMEOUT = 25
PAGE = 100                     # the list endpoint's maximum
MAX_PAGES = 5                  # 500 campaigns per sub-account per read
STATS_CAP = 25                 # per-campaign statistics calls per read
KEEP_READINGS = 60
REFRESH_HOUR_ENV = "SUITE_EMAIL_REFRESH_HOUR"
SWEEP_BUDGET_SECONDS = 120
MODULE = "suite_email"

SCOPE_LIST = "emails/schedule.readonly"
SCOPE_STATS = "emails/stats.readonly"
NEEDED_SCOPES = (SCOPE_LIST, SCOPE_STATS)

STATES = ("ok", "no_snapshot", "not_linked", "no_scope", "unread", "empty")
# Schedule statuses that mean the Suite has sent (or is sending) the
# campaign. Everything else -- draft, pause, cancelled, retry -- is not a
# send and is not reported.
SENT_STATUSES = ("complete", "active", "resend-scheduled")

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class SuiteEmailStatsError(RuntimeError):
    """Message is safe to show a rep -- never contains the token."""


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------

def _headers(token: str, version: str = API_VERSION) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": version,
            "Accept": "application/json"}


def _call(token: str, path: str, *, params: dict | None = None,
          version: str = API_VERSION, scope_hint: str = "") -> tuple[dict | None, dict]:
    """``(data, error)`` -- exactly one of the two is populated. ``error``
    carries ``kind``: ``refused`` (401/403, the scope), ``missing`` (404),
    ``rate_limited`` (429), ``unreachable``, ``http``, ``unreadable``."""
    try:
        r = requests.get(f"{API_BASE}{path}", headers=_headers(token, version),
                         params=params or None, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return None, {"kind": "unreachable",
                      "error": f"Couldn't reach Smart 1 Suite ({type(exc).__name__})."}
    if r.status_code in (401, 403):
        # Never the body: HighLevel errors have carried token fragments.
        why = (f" It needs the {scope_hint} scope, which takes an agency re-consent of the Hub app."
               if scope_hint else "")
        return None, {"kind": "refused",
                      "error": f"Smart 1 Suite rejected the read ({r.status_code}).{why}"}
    if r.status_code == 404:
        return None, {"kind": "missing", "error": f"Smart 1 Suite has no {path.split('/')[2] or 'resource'} here (404)."}
    if r.status_code == 429:
        return None, {"kind": "rate_limited", "error": "Smart 1 Suite is rate-limiting reads; try later."}
    if not r.ok:
        return None, {"kind": "http", "error": f"Smart 1 Suite returned HTTP {r.status_code} for {path}."}
    try:
        data = r.json()
    except ValueError:
        return None, {"kind": "unreadable", "error": "Smart 1 Suite returned an unreadable response."}
    if not isinstance(data, dict):
        return None, {"kind": "unreadable", "error": "Smart 1 Suite returned an unexpected response."}
    return data, {}


# ---------------------------------------------------------------------------
# Reading a row
# ---------------------------------------------------------------------------

_COUNT_KEYS = {
    # our name: the keys HighLevel has been seen to use, first match wins
    "sent": ("sent", "sentCount", "totalSent", "total", "recipients"),
    "delivered": ("delivered", "deliveredCount", "totalDelivered"),
    "opened": ("opened", "openedCount", "uniqueOpened", "opens", "totalOpened"),
    "clicked": ("clicked", "clickedCount", "uniqueClicked", "clicks", "totalClicked"),
    "bounced": ("bounced", "bouncedCount", "bounces", "totalBounced", "failed"),
    "unsubscribed": ("unsubscribed", "unsubscribedCount", "unsubscribes", "totalUnsubscribed"),
    "complained": ("complained", "complainedCount", "spam", "spamReported", "complaints"),
}
_REVENUE_KEYS = ("revenue", "totalRevenue", "orderValue")


def _int(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _stats_of(row: dict) -> tuple[dict, dict]:
    """``(normalised, raw)``. The raw statistics object is kept beside the
    normalised counts because the spec does not name its keys."""
    raw = None
    for k in ("stats", "statistics", "campaignStats", "emailStats"):
        if isinstance(row.get(k), dict):
            raw = row[k]
            break
    src = raw if raw is not None else row
    out = {}
    for ours, theirs in _COUNT_KEYS.items():
        val = None
        for k in theirs:
            if k in src:
                val = _int(src.get(k))
                if val is not None:
                    break
        out[ours] = val
    rev = None
    for k in _REVENUE_KEYS:
        if k in src:
            try:
                rev = float(src.get(k))
            except (TypeError, ValueError):
                rev = None
            if rev is not None:
                break
    out["revenue"] = rev
    return out, (dict(raw) if isinstance(raw, dict) else {})


def _rate(num, den):
    if num is None or not den:
        return None
    return round(100.0 * num / den, 1)


def _campaign_row(row: dict, location_id: str) -> dict | None:
    """One campaign as the store keeps it, or None for a row that is not
    a sent campaign of this sub-account."""
    if not isinstance(row, dict):
        return None
    loc = str(row.get("locationId") or "")
    if loc and loc != location_id:
        return None
    cid = str(row.get("_id") or row.get("id") or "").strip()
    if not cid:
        return None
    if row.get("deleted") or row.get("archived"):
        return None
    if row.get("childCount") and not row.get("campaignType"):
        return None                                      # a folder
    status = str(row.get("status") or "").strip().lower()
    stats, raw = _stats_of(row)
    return {
        "id": cid,
        "name": str(row.get("name") or "").strip() or "Untitled campaign",
        "subject": str(row.get("subject") or row.get("emailSubject") or "").strip(),
        "status": status,
        "sent_at": str(row.get("sentAt") or row.get("scheduledAt") or row.get("startDate")
                       or row.get("updatedAt") or ""),
        "created_at": str(row.get("createdAt") or ""),
        "campaign_type": str(row.get("campaignType") or ""),
        "has_stats": any(v is not None for k, v in stats.items() if k != "revenue"),
        "stats_source": "list" if any(v is not None for k, v in stats.items() if k != "revenue") else "",
        **stats,
        "raw_stats": raw,
    }


def _finish(c: dict) -> dict:
    base = c.get("delivered") if c.get("delivered") is not None else c.get("sent")
    c["open_rate"] = _rate(c.get("opened"), base)
    c["click_rate"] = _rate(c.get("clicked"), base)
    return c


# ---------------------------------------------------------------------------
# The two reads
# ---------------------------------------------------------------------------

def list_campaigns(token: str, location_id: str) -> tuple[list[dict], dict]:
    """Every sent campaign on the sub-account with the statistics the list
    carries. ``(rows, error)``; on an error the rows read so far are kept."""
    rows, dropped, seen = [], 0, set()
    for page in range(MAX_PAGES):
        data, err = _call(token, "/emails/schedule",
                          params={"locationId": location_id, "limit": PAGE, "offset": page * PAGE,
                                  "campaignsOnly": "true", "showStats": "true"},
                          scope_hint=SCOPE_LIST)
        if err:
            return rows, err
        items = data.get("schedules")
        if not isinstance(items, list):
            return rows, {"kind": "unreadable", "error": "Smart 1 Suite did not return a campaign list."}
        for item in items:
            c = _campaign_row(item, location_id)
            if c is None:
                dropped += 1
                continue
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            rows.append(c)
        if len(items) < PAGE:
            break
    return rows, ({"dropped": dropped} if dropped else {})


def campaign_stats(token: str, location_id: str, campaign_id: str) -> tuple[dict | None, dict]:
    """The unified statistics endpoint for one campaign, Version v3."""
    if not _ID_RE.match(campaign_id or "") or not _ID_RE.match(location_id or ""):
        return None, {"kind": "unreadable", "error": "A campaign or location id was not usable."}
    data, err = _call(token, f"/emails/locations/{location_id}/campaigns/stats/email-campaigns/{campaign_id}",
                      version=STATS_VERSION, scope_hint=SCOPE_STATS)
    if err:
        return None, err
    stats, raw = _stats_of(data)
    stats["raw_stats"] = raw or {k: v for k, v in data.items() if k != "traceId"}
    return stats, {}


def read(token: str, location_id: str) -> dict:
    """The whole read for one sub-account: the list, then per-campaign
    statistics for what the list did not carry, under the cap.
    ``{"ok", "error", "kind", "campaigns", "notes"}``."""
    out = {"ok": False, "error": "", "kind": "", "campaigns": [], "notes": []}
    rows, err = list_campaigns(token, location_id)
    if err.get("kind"):
        out.update({"error": err["error"], "kind": err["kind"]})
        return out
    if err.get("dropped"):
        out["notes"].append(f"{err['dropped']} row(s) that were not sent campaigns of this sub-account were left out.")
    sent = [c for c in rows if c["status"] in SENT_STATUSES]
    asked = 0
    stats_offered = None
    for c in sent:
        if c["has_stats"] or asked >= STATS_CAP or stats_offered is False:
            continue
        asked += 1
        st, serr = campaign_stats(token, location_id, c["id"])
        if serr:
            if serr.get("kind") == "missing":
                stats_offered = False
                out["notes"].append("The per-campaign statistics endpoint is not offered on this sub-account; "
                                    "the counts are what the campaign list carries.")
            elif serr.get("kind") == "refused":
                stats_offered = False
                out["notes"].append(serr["error"])
            continue
        c.update({k: v for k, v in st.items() if k != "raw_stats"})
        c["raw_stats"] = st.get("raw_stats") or {}
        c["has_stats"] = any(st.get(k) is not None for k in _COUNT_KEYS)
        c["stats_source"] = "stats" if c["has_stats"] else ""
    left = sum(1 for c in sent if not c["has_stats"])
    if left and asked >= STATS_CAP:
        out["notes"].append(f"{left} campaign(s) still have no statistics after the per-read cap; the next read continues.")
    out["campaigns"] = sorted((_finish(c) for c in sent), key=lambda c: c.get("sent_at") or "", reverse=True)
    out["ok"] = True
    return out


# ---------------------------------------------------------------------------
# The account and the scope
# ---------------------------------------------------------------------------

def account(client: str, url: str = "") -> dict:
    """The client's sub-account and a token for it, or why not."""
    try:
        from hub import suite_accounts
        return suite_accounts.token_for(client, url)
    except Exception as exc:                               # noqa: BLE001
        return {"state": "not_measured", "detail": f"The Suite link could not be read ({type(exc).__name__}).",
                "location_id": "", "token": None, "connected": False}


def scope_state() -> dict:
    """Are the two scopes consented? Tri-state: ``known`` False means the
    consented list could not be read, which is not a refusal."""
    try:
        from hub import ghl_oauth, ghl_scopes
        st = ghl_oauth.status()
        if not st.get("connected"):
            return {"known": True, "missing": list(NEEDED_SCOPES), "connected": False,
                    "detail": st.get("detail") or "Smart 1 Suite is not authorized on this deployment."}
        cmp = st.get("scopes") or ghl_scopes.compare(st.get("scope", ""))
        if not cmp.get("known"):
            return {"known": False, "missing": [], "connected": True,
                    "detail": "The consented scope list could not be read; a read will say."}
        granted = set(cmp.get("granted") or [])
        missing = [s for s in NEEDED_SCOPES if s not in granted]
        return {"known": True, "missing": missing, "connected": True,
                "detail": ("" if not missing else
                           "The Hub app has not been consented with " + ", ".join(missing)
                           + ". They are on the requested list; the agency owner re-consenting once grants them.")}
    except Exception as exc:                               # noqa: BLE001
        return {"known": False, "missing": [], "connected": None,
                "detail": f"The app's scopes could not be read ({type(exc).__name__})."}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def _root() -> str:
    from hub import jsonstore
    return jsonstore.data_dir(MODULE)


def _state_path() -> str:
    return os.path.join(_root(), "sweep.json")


def _slug(name: str) -> str:
    try:
        from hub.client_key import name_slug
        s = name_slug(name)
        if s:
            return s
    except Exception:                                      # noqa: BLE001
        pass
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "client"


def _readings_path(name: str) -> str:
    d = os.path.join(_root(), "readings")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _slug(name) + ".json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(event: str, name: str, actor: str, **extra) -> None:
    try:
        from hub import audit
        audit.log(MODULE, event, actor=actor or "system", client=name, **extra)
    except Exception:                                      # noqa: BLE001 - a log line is not the write
        pass


def readings(name: str) -> list[dict]:
    """Every stored reading for a client, newest first."""
    from hub import jsonstore
    rows = jsonstore.read_json(_readings_path(name), default=[]) or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("day")]
    rows.sort(key=lambda r: r["day"], reverse=True)
    return rows


def _append_reading(name: str, *, location_id: str, campaigns: list[dict] | None,
                    notes: list[str], today: date | None = None, error: str = "",
                    kind: str = "") -> dict:
    """One reading per client per day: a second on the same day replaces
    the first, so a Refresh press does not stack rows."""
    from hub import jsonstore
    day = (today or date.today()).isoformat()
    row = {"day": day, "fetched_at": _now_iso(), "location_id": location_id,
           "error": error or "", "kind": kind or "", "notes": list(notes or [])}
    if campaigns is not None:
        row["campaigns"] = campaigns

    def mutate(rows):
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("day") != day]
        rows.append(row)
        rows.sort(key=lambda r: r.get("day") or "", reverse=True)
        return rows[:KEEP_READINGS]

    jsonstore.update_json(_readings_path(name), mutate)
    return row


def snapshot(name: str, *, today: date | None = None, actor: str = "") -> dict:
    """Read the client's sub-account now and store the reading. A button
    and the nightly sweep, never a page load. ``{"ok", "error", "kind",
    "reading"}``."""
    acct = account(name)
    if not acct.get("token"):
        return {"ok": False, "kind": acct.get("state") or "not_connected",
                "error": acct.get("detail") or "This client has no linked Smart 1 Suite sub-account.",
                "reading": reading(name, today=today)}
    loc = acct["location_id"]
    res = read(acct["token"], loc)
    if not res["ok"]:
        _append_reading(name, location_id=loc, campaigns=None, notes=res["notes"],
                        today=today, error=res["error"], kind=res["kind"])
        _log("read_failed", name, actor, location_id=loc, kind=res["kind"])
        return {"ok": False, "error": res["error"], "kind": res["kind"],
                "reading": reading(name, today=today)}
    _append_reading(name, location_id=loc, campaigns=res["campaigns"], notes=res["notes"], today=today)
    _log("read", name, actor, location_id=loc, campaigns=len(res["campaigns"]))
    return {"ok": True, "error": "", "kind": "", "reading": reading(name, today=today)}


# ---------------------------------------------------------------------------
# What a screen reads
# ---------------------------------------------------------------------------

def _good(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("error") and isinstance(r.get("campaigns"), list)]


def _totals(campaigns: list[dict], days: int, today: date) -> dict:
    """Sums over campaigns sent in the last ``days`` days -- arithmetic on
    the stored rows and nothing else."""
    since = today.toordinal() - days
    keys = ("sent", "delivered", "opened", "clicked", "bounced", "unsubscribed")
    out = {k: 0 for k in keys}
    out.update({"campaigns": 0, "measured": 0, "days": days})
    for c in campaigns:
        d = _day_of(c.get("sent_at"))
        if d is None or d.toordinal() < since:
            continue
        out["campaigns"] += 1
        if c.get("has_stats"):
            out["measured"] += 1
            for k in keys:
                if c.get(k) is not None:
                    out[k] += c[k]
    base = out["delivered"] or out["sent"]
    out["open_rate"] = _rate(out["opened"], base) if out["measured"] else None
    out["click_rate"] = _rate(out["clicked"], base) if out["measured"] else None
    return out


def _day_of(s) -> date | None:
    s = str(s or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def reading(name: str, *, today: date | None = None) -> dict:
    """The card's whole answer for one client. ``state`` is one of
    ``STATES``: ``ok`` carries campaigns, ``empty`` a read that found no
    sent campaign, the others say which kind of nothing this is.
    ``staff_note`` is for the staff screens and never a client's page;
    ``public_view()`` strips it."""
    today = today or date.today()
    out = {"measured": False, "state": "", "account": {}, "scopes": scope_state(),
           "campaigns": [], "as_of": None, "readings": 0, "totals": None,
           "notes": [], "note": "", "staff_note": "", "error": ""}
    try:
        from hub import suite_accounts
        acct = suite_accounts.location_for(name)
    except Exception as exc:                               # noqa: BLE001
        acct = {"state": "not_measured", "detail": f"The Suite link could not be read ({type(exc).__name__}).",
                "location_id": ""}
    out["account"] = {"state": acct.get("state"), "location_id": acct.get("location_id") or "",
                      "detail": acct.get("detail") or ""}
    if acct.get("state") != "connected":
        out["state"] = "not_linked"
        out["staff_note"] = acct.get("detail") or "No Smart 1 Suite sub-account is linked to this client."
        return out
    if out["scopes"].get("known") and out["scopes"].get("missing"):
        out["state"] = "no_scope"
        out["staff_note"] = out["scopes"]["detail"]
        # A reading taken before the scope went missing is still shown below.
    rows = readings(name)
    out["readings"] = len(rows)
    good = _good(rows)
    if not rows:
        if out["state"] != "no_scope":
            out["state"] = "no_snapshot"
            out["staff_note"] = "Linked and not read yet. The nightly read will take it, or press Refresh reading."
        return out
    if not good:
        if out["state"] != "no_scope":
            out["state"] = "unread"
        out["error"] = str(rows[0].get("error") or "the sub-account could not be read")
        out["staff_note"] = f"The last read failed: {out['error']}"
        return out
    latest = good[0]
    camps = [dict(c) for c in latest.get("campaigns") or []]
    for c in camps:
        c.pop("raw_stats", None)
    out.update({"measured": True, "state": "ok" if camps else "empty",
                "campaigns": camps, "as_of": latest["day"], "notes": list(latest.get("notes") or []),
                "totals": {"30": _totals(camps, 30, today), "90": _totals(camps, 90, today)}})
    if rows[0] is not latest and rows[0].get("error"):
        out["staff_note"] = (f"The newest read ({rows[0]['day']}) failed: {rows[0]['error']}; "
                             f"showing the reading from {latest['day']}.")
    age = (today - date.fromisoformat(latest["day"])).days
    if age > 2:
        out["note"] = f"Read {age} days ago."
    if not camps:
        out["staff_note"] = out["staff_note"] or "The sub-account has no sent email campaign."
    return out


PUBLIC_KEYS = ("name", "subject", "sent_at", "sent", "delivered", "opened", "clicked",
               "bounced", "unsubscribed", "open_rate", "click_rate", "has_stats")


def public_view(r: dict | None, *, limit: int = 12) -> dict | None:
    """What a client's page may carry: the campaigns' own names, dates and
    counts, the period totals, and nothing about our tooling or the
    account. None unless there is a measured reading with a campaign."""
    if not r or r.get("state") != "ok":
        return None
    camps = [{k: c.get(k) for k in PUBLIC_KEYS} for c in (r.get("campaigns") or [])[:limit]]
    return {"measured": True, "as_of": r["as_of"], "campaigns": camps,
            "more": max(0, len(r.get("campaigns") or []) - limit),
            "totals": r.get("totals") or {}}


def card_for(name: str, *, today: date | None = None) -> dict | None:
    try:
        return public_view(reading(name, today=today))
    except Exception:                                      # noqa: BLE001 - the page must render
        log.exception("suite_email_stats: card_for failed")
        return None


# ---------------------------------------------------------------------------
# The nightly sweep
# ---------------------------------------------------------------------------

def linked_clients() -> list[str]:
    """Every client with a recorded Suite sub-account, from the one reader
    of that mapping."""
    names: set[str] = set()
    try:
        from hub import suite_map
        for r in suite_map.links():
            n = str(r.get("client") or "").strip()
            if n and str(r.get("location_id") or "").strip():
                names.add(n)
    except Exception:                                      # noqa: BLE001
        pass
    # The picker's own column is the older store hub/suite_accounts reads
    # too; neither is migrated, so both are walked.
    try:
        from modules.image_picker.models import PickerClient
        rows = list(PickerClient.query.all())
    except Exception:                                      # noqa: BLE001
        rows = []
    for row in rows:
        if str(getattr(row, "ghl_location_id", "") or "").strip():
            n = str(getattr(row, "name", "") or "").strip()
            if n:
                names.add(n)
    return sorted(names, key=str.casefold)


def _state() -> dict:
    from hub import jsonstore
    data = jsonstore.read_json(_state_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def due_for_refresh(now: datetime | None = None) -> bool:
    from hub import nightly
    now = now or datetime.now(timezone.utc)
    st = _state()
    last = None
    if st.get("last_run_at"):
        try:
            last = datetime.fromisoformat(str(st["last_run_at"]))
        except ValueError:
            last = None
    return nightly.due(last, env_var=REFRESH_HOUR_ENV, now=now)


def sweep(*, force: bool = False, today: date | None = None,
          now: datetime | None = None, budget: float | None = None,
          clock=None, names: list[str] | None = None) -> dict:
    """Read every linked client's sub-account once, inside the nightly
    window. The youtube.sweep shape: hourly tick, the module decides, a
    client already read today skipped, a wall-clock budget, and a stop
    on a refusal that would repeat for every client."""
    from hub import jsonstore
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    clock = clock or time.monotonic
    budget = SWEEP_BUDGET_SECONDS if budget is None else float(budget)
    out = {"ran": False, "skipped": "", "clients": 0, "read": 0, "failed": 0,
           "already": 0, "left": 0, "errors": {}}
    sc = scope_state()
    if sc.get("known") and sc.get("missing"):
        out["skipped"] = sc["detail"]
        return out
    if not force and not due_for_refresh(now):
        out["skipped"] = "Not due yet."
        return out
    names = sorted(names) if names is not None else linked_clients()
    out["clients"] = len(names)
    started = clock()
    for i, name in enumerate(names):
        if clock() - started > budget:
            out["left"] = len(names) - i
            break
        rows = readings(name)
        if rows and rows[0].get("day") == today.isoformat() and not rows[0].get("error"):
            out["already"] += 1
            continue
        res = snapshot(name, today=today)
        if res["ok"]:
            out["read"] += 1
        else:
            out["failed"] += 1
            out["errors"][name] = res.get("error") or "unread"
            if res.get("kind") in ("refused", "rate_limited"):
                # A scope the agency token lacks is lacking for every
                # sub-account; a rate limit is spent for all of them.
                out["left"] = len(names) - i - 1
                break
    out["ran"] = True
    state = {"last_run_at": now.isoformat(timespec="seconds"), "read": out["read"],
             "failed": out["failed"], "already": out["already"], "left": out["left"],
             "clients": out["clients"]}
    try:
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                                      # noqa: BLE001 - a note is not the sweep
        pass
    return out


def sweep_state() -> dict:
    st = _state()
    return {"last_run_at": st.get("last_run_at"), "read": st.get("read"),
            "failed": st.get("failed"), "left": st.get("left"), "clients": st.get("clients"),
            "scopes": scope_state()}
