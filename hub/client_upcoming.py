"""What is coming up for one client, and when we last did anything for them.

The "Coming up" card on Client 360 and the *Last activity* pill on its health
strip. Three sources, each already on the record in some other shape, none of
which said *when*:

  * **Insertion orders end.** `hub/io_records.py` holds every order this Hub
    sent, with its flight end. An order ending in three weeks is a renewal
    conversation that has to happen this week, and the Orders card lists them
    newest-sent-first rather than soonest-ending-first.
  * **Domains renew.** `hub/domain_purchase.py`'s nightly snapshot knows which
    domains we bought and when each renews; the renewal calendar reads it by
    month, across every client. Here it is read for one.
  * **Nothing has happened.** `hub/client_brand.work_log()` is the activity
    feed; its newest row is the last thing anybody did for this client, and
    the gap since is the honest churn signal.

Rules, the same ones hub/record_health.py works to:

**Never raises.** Every source names its own failure and the answer carries
on. A card that dies because the domain snapshot has not run yet is a card
that dies on every fresh deploy.

**Not measured is an answer.** A domain with a renewal date nobody typed is
reported as "renewal date not on file", not dropped: dropping it is how a
domain lapses.

**Exact client match.** IO records by `hub/client_key.normalise_name`, domains
by normalised client name *or* the client's own domain -- a substring pass
would put one company's order on another's record (io_records' own rule).
"""
from __future__ import annotations

import datetime as _dt
import re

__all__ = ["for_client", "last_activity", "HORIZON_DAYS"]

HORIZON_DAYS = 120          # what "coming up" means; ended orders keep 30 days
ENDED_KEEP_DAYS = 30


def _norm(name: str) -> str:
    try:
        from hub.client_key import normalise_name
        return normalise_name(str(name or ""))
    except Exception:                                      # noqa: BLE001
        return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _domain(value: str) -> str:
    try:
        from hub.client_context import canonical_domain
        return canonical_domain(value or "")
    except Exception:                                      # noqa: BLE001
        return str(value or "").lower().replace("https://", "").replace("http://", "").split("/")[0]


def _date(value) -> _dt.date | None:
    """ISO, m/d/Y, Knack's dict shape -- or None, never a guess."""
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, dict):
        value = value.get("date") or value.get("iso_timestamp") or ""
    s = str(value or "").strip().split("T")[0]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _days(today: _dt.date, when: _dt.date | None) -> int | None:
    return (when - today).days if when else None


def _state(days: int | None) -> str:
    """bad = past or inside two weeks; warn = inside the horizon; ok = further."""
    if days is None:
        return "unread"
    if days <= 14:
        return "bad"
    if days <= 45:
        return "warn"
    return "ok"


def _io_name(order) -> str:
    """"IO-2261" stays as typed; a bare "2261" gets the prefix."""
    o = str(order or "").strip()
    return o if not o or o.upper().startswith("IO") else f"IO {o}"


# ------------------------------------------------------------- sources
def _orders(name: str, today: _dt.date) -> tuple[list[dict], str]:
    try:
        from hub import io_records
        book = io_records.listing(name)
    except Exception as exc:                               # noqa: BLE001
        return [], f"Insertion orders could not be read ({type(exc).__name__})."
    if not book.get("measured"):
        return [], book.get("error") or "Insertion orders could not be read."
    out = []
    for r in book.get("rows") or []:
        end = _date(r.get("end"))
        days = _days(today, end)
        if days is not None and days < -ENDED_KEEP_DAYS:
            continue                                        # long over: not "coming up"
        if days is not None and days > HORIZON_DAYS:
            continue
        out.append({"kind": "io", "label": "Insertion order ends",
                    "what": _io_name(r.get("order")) + (f" · ${float(r.get('monthly') or 0):,.0f}/mo" if r.get("monthly") else ""),
                    "when": end.isoformat() if end else "", "days": days,
                    "state": _state(days) if end else "unread",
                    "detail": ("" if end else "No end date on the order.")
                              or (f"Ended {-days} days ago -- renew or close it out." if days is not None and days < 0 else ""),
                    "href": "/tools/io/", "lines": int(r.get("line_count") or 0)})
    return out, ""


def _domains(name: str, url: str, today: _dt.date) -> tuple[list[dict], str]:
    try:
        from hub import domain_purchase
        snap = domain_purchase.snapshot()
    except Exception as exc:                               # noqa: BLE001
        return [], f"The domain snapshot could not be read ({type(exc).__name__})."
    if not snap:
        return [], "The domain snapshot has not been pulled yet (it runs nightly)."
    want, dom = _norm(name), _domain(url)
    out = []
    for r in snap.get("rows") or []:
        same = (_norm(r.get("client")) == want) or (dom and _domain(r.get("domain")) == dom)
        if not same:
            continue
        renews = _date(r.get("renews"))
        days = _days(today, renews)
        if days is not None and (days > HORIZON_DAYS or days < -ENDED_KEEP_DAYS):
            continue
        out.append({"kind": "domain", "label": "Domain renews",
                    "what": r.get("domain") or "", "when": renews.isoformat() if renews else "",
                    "days": days, "state": _state(days) if renews else "unread",
                    "detail": ("Renewal date not on file in Knack." if not renews
                               else (f"Passed {-days} days ago -- check it was renewed and billed." if days < 0
                                     else (f"via {r.get('registrar')}" if r.get("registrar") else ""))),
                    "href": "/tools/seo-images/house", "partner": r.get("partner") or ""})
    return out, ""


def last_activity(name: str, url: str = "", today: _dt.date | None = None) -> dict:
    """The newest thing anybody did for this client, and the gap since.

    ``{"when", "days", "kind", "actor", "state", "measured"}``; ``state`` is
    ok under 30 days, warn under 90, bad beyond, idle when nothing was ever
    logged -- which is its own answer, not a zero.
    """
    today = today or _dt.date.today()
    try:
        from hub.client_brand import work_log
        from hub import client_groups
        also = client_groups.member_names(name, url)
        log = work_log(name, 1, also=also)
    except Exception as exc:                               # noqa: BLE001
        return {"measured": False, "error": f"The activity log could not be read ({type(exc).__name__})."}
    rows = (log.get("items") if isinstance(log, dict) else log) or []
    if not rows:
        # "Nothing was ever made for this client" and "nothing in the window I
        # could see" are different answers, and only the first is `idle`.
        # work_log() reaches the end of the log when `complete`; short of that
        # the oldest row it saw is `horizon`, and claiming idle there turns a
        # long-standing client whose last deliverable predates the window into
        # one who reads as though nothing has ever been done -- and quietly
        # replaces the 90-day churn warning they had earned with "Nothing
        # logged", which nobody chases.
        info = log if isinstance(log, dict) else {}
        if info.get("error"):
            return {"measured": False, "error": info["error"]}
        if not info.get("complete", True):
            since = str(info.get("horizon") or "")[:10]
            return {"measured": False,
                    "error": ("nothing logged for this client back to "
                              + since + ", and the log goes further back than "
                              "this read reached") if since else
                             "the activity log could not be read back far enough"}
        return {"measured": True, "when": "", "days": None, "state": "idle", "kind": "", "actor": ""}
    top = rows[0]
    when = _date(top.get("when"))
    days = (today - when).days if when else None
    state = "idle" if days is None else ("ok" if days < 30 else "warn" if days < 90 else "bad")
    return {"measured": True, "when": when.isoformat() if when else "", "days": days, "state": state,
            "kind": top.get("kind") or "", "source": top.get("source") or "", "actor": top.get("actor") or ""}


# ------------------------------------------------------------- the card
def for_client(name: str, url: str = "", today: _dt.date | None = None) -> dict:
    name = str(name or "").strip()
    today = today or _dt.date.today()
    if not name:
        return {"measured": False, "error": "No client was named.", "items": []}
    unread: list[str] = []
    items: list[dict] = []
    for fn, args in ((_orders, (name, today)), (_domains, (name, url, today))):
        try:
            rows, why = fn(*args)
        except Exception as exc:                           # noqa: BLE001
            rows, why = [], f"{fn.__name__.strip('_')}: {type(exc).__name__}"
        items += rows
        if why:
            unread.append(why)
    # Soonest first; undated last, because "we do not know when" is the one
    # to chase and it must not vanish under the ones we do know.
    items.sort(key=lambda i: (i["days"] is None, i["days"] if i["days"] is not None else 0))
    return {"measured": True, "client": name, "today": today.isoformat(),
            "horizon_days": HORIZON_DAYS, "items": items, "unread": unread,
            "activity": last_activity(name, url, today)}
