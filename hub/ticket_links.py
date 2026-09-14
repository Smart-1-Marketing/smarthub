"""Confirmed links between a Client 360 client and a ticket's own name.

`hub/knack_api.list_tickets()` only ever finds a ticket whose "Client
Organization" field **contains** one of the client's known aliases
(`hub/client_groups.member_names()`). A ticket filed under a name that is
none of those — a media partner's own spelling, a location's own name, a
typo, an old name kept from before a rebrand — is invisible, and an empty
ticket list on Client 360 looks exactly like a client who genuinely has no
tickets. There has never been a fuzzy fallback and nothing to search: the rep
either recognises the absence as a naming problem, or, more often, correctly
assumes the client just has none.

Two ways in, and both are read-only against Knack. `suggest_for()` fuzzy-
scores every distinct "Client Organization" string already on the ticket
object against this client's own name — the same `SequenceMatcher` over
`hub.client_key.normalise_name()` this codebase already uses in
`hub/knack_websites.py`, `hub/domain_renewals.py` and `hub/site_names.py`,
and for the reason `knack_websites._similar()`'s own docstring gives at
length: a naive substring rule once scored a media partner above the real
client on 39 of 242 rows, so this never scores on containment alone.
`search()` is the same pool asked a different question — a plain
case-insensitive match, for when a rep already has a rough idea what the
ticket was filed under and wants to find it rather than wait on a score
against the client's own name.

Neither writes anything, and neither is offered as a fact. `link()` is the
confirm a person presses: it records that one Knack "Client Organization"
string belongs to this client, in a Hub-side overlay
(`hub/client_urls.py`'s `discovered_urls.json` shape) that is read alongside
`client_groups.member_names()` on every later lookup. It is additive — a
client with a location filed under a second name needs both kept — never a
rename of anything in Knack, and never the client's *derived* key, for the
reason `hub/client_key.py` gives at length: a client renamed in Knack must
re-join on the next request rather than carry a stale copy forward.
"""
import os
from datetime import datetime, timezone

from hub import jsonstore
from hub.client_key import normalise_name


def _store_path() -> str:
    return os.path.join(jsonstore.data_dir("clients"), "ticket_links.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def overlay() -> dict:
    """{normalized client name: row}. Never raises — a caller is mid-render."""
    rows = jsonstore.read_json(_store_path(), default={})
    return rows if isinstance(rows, dict) else {}


def linked_orgs(name: str) -> list[str]:
    """Confirmed ticket "Client Organization" strings for this client."""
    key = normalise_name(name)
    if not key:
        return []
    row = overlay().get(key)
    return list(row.get("orgs") or []) if isinstance(row, dict) else []


def link(name: str, org: str, *, actor: str = "") -> dict:
    """Confirm that a ticket "Client Organization" string is this client's.

    Additive: confirming a second organization string does not replace the
    first, because a client whose tickets are split across a location name
    and an umbrella name needs both kept.
    """
    key = normalise_name(name)
    org = str(org or "").strip()
    if not key:
        return {"ok": False, "error": "That client name is empty."}
    if not org:
        return {"ok": False, "error": "Nothing was chosen to link."}
    rows = overlay()
    row = rows.get(key) if isinstance(rows.get(key), dict) else {}
    orgs = list(row.get("orgs") or [])
    if org not in orgs:
        orgs.append(org)
    rows[key] = {"client": name, "orgs": orgs, "linked_by": actor,
                 "linked_at": _now()}
    jsonstore.write_json(_store_path(), rows, indent=1)
    _forget_cache()
    return {"ok": True, "orgs": orgs}


def unlink(name: str, org: str = "") -> dict:
    """Undo one linked organization string, or every one for a client."""
    key = normalise_name(name)
    rows = overlay()
    if key not in rows or not isinstance(rows.get(key), dict):
        return {"ok": False, "error": "Nothing was recorded for that client."}
    org = str(org or "").strip()
    if org:
        current = list(rows[key].get("orgs") or [])
        orgs = [o for o in current if o != org]
        if len(orgs) == len(current):
            return {"ok": False,
                    "error": f"“{org}” wasn't linked to that client."}
        if orgs:
            rows[key]["orgs"] = orgs
        else:
            rows.pop(key, None)
    else:
        rows.pop(key, None)
    jsonstore.write_json(_store_path(), rows, indent=1)
    _forget_cache()
    return {"ok": True}


def _forget_cache() -> None:
    try:
        from hub import report_cache
        report_cache.invalidate("web-tickets")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# The pool: distinct "Client Organization" strings already on the ticket
# object, read fresh on every call and never on a page load — this is a real
# Knack pull, and both `suggest_for()` and `search()` are reached only from a
# button a rep presses, never from `loadTickets()`'s own automatic fetch.
# ---------------------------------------------------------------------------

def _org_pool(limit: int = 1000) -> list[dict]:
    """Every distinct "Client Organization" value on the ticket object, with
    how many tickets carry it and the most recent title, so a candidate reads
    as more than a bare string.

    One page of up to `limit` tickets — the same single-page shape
    `hub.knack_api.people_names()` already uses for a small object: this one
    is not sized for real pagination, and a search that pages through
    thousands of records to answer one query is a search nobody presses
    twice. Never raises: a Knack outage costs this feature nothing more, and
    an empty pool reads as "found nothing" rather than a 500.
    """
    from hub import knack_api
    if not knack_api.configured():
        return []
    m = knack_api.field_map()
    if not m.get("client"):
        return []
    # Through knack_api's own `requests`, not a fresh import of the module --
    # that seam is what test_web_tickets.py stubs, and a second import here
    # would bypass it and reach the real network in every other test in
    # that file too.
    try:
        r = knack_api.requests.get(
            f"{knack_api.BASE}/objects/{knack_api.TICKETS_OBJECT}/records",
            headers=knack_api._headers(),
            params={"rows_per_page": int(limit),
                    "sort_field": m.get("date") or m["client"],
                    "sort_order": "desc"},
            timeout=25)
        r.raise_for_status()
        records = (r.json() or {}).get("records", [])
    except Exception:  # noqa: BLE001
        return []
    pool: dict[str, dict] = {}
    for rec in records:
        org = knack_api._plain(rec.get(m["client"])).strip() if m["client"] else ""
        if not org:
            continue
        row = pool.setdefault(org, {"org": org, "count": 0, "title": ""})
        row["count"] += 1
        if not row["title"] and m.get("title"):
            row["title"] = knack_api._plain(rec.get(m["title"]))
    return list(pool.values())


def suggest_for(name: str, threshold: float = 0.72, limit: int = 5) -> list[dict]:
    """Ranked candidates for a client whose known aliases found nothing.

    Deliberately the same conservative scorer `hub.knack_websites._similar()`
    uses, for the same reason: an automatic match here would file a stranger's
    ticket history onto this client's record, which is worse than the "no web
    tickets" state it replaces. A candidate is never applied by this
    function — it is a suggestion a person confirms with `link()`.
    """
    from difflib import SequenceMatcher
    key = normalise_name(name)
    if not key:
        return []
    already = set(linked_orgs(name))
    out = []
    for row in _org_pool():
        if row["org"] in already:
            continue
        score = SequenceMatcher(None, key, normalise_name(row["org"])).ratio()
        if score >= threshold:
            out.append({**row, "score": round(score, 3)})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def search(query: str, limit: int = 20) -> list[dict]:
    """Every "Client Organization" on the ticket object containing `query`.

    For when a rep already knows roughly what the ticket was filed under — a
    media partner's name, an old spelling, a location — and wants to find it
    directly rather than wait on a fuzzy score against the client's own name.
    """
    q = str(query or "").strip().lower()
    if not q:
        return []
    return sorted((r for r in _org_pool() if q in r["org"].lower()),
                  key=lambda r: r["org"].lower())[:limit]
