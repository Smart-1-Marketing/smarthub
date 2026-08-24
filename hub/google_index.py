"""One index of every Google resource we can reach, and the client it belongs to.

## The problem this solves

Four things wanted the same answer and none of them could get it cheaply.

Client 360 asks "what Google plumbing does this client have?" by calling
`/google/api/search?q=<name>` — which, on a cold worker, sweeps every connected
login across four Google APIs before it can answer. The GTM half of that sweep
is deliberately paced at a third of a second per call because Tag Manager rate
limits hard, so with 150+ GTM accounts the page is waiting on the better part
of a minute of sleep the Hub asked for itself. That is the 360's slowness, and
it is not Google being slow.

QA wanted a list of every account and who it maps to, and there was nowhere to
read one from.

The Google tool pages wanted to pre-fill a property or a container for a
client, and had no lookup to do it with.

And `hub/analytics_ids._live_google()` tried to answer it by reading an
``items`` key off `connected_accounts()` — a key that function has never
returned. The loop body never ran, so the Google half of every GA/GTM
comparison came back blank and every client read as *recorded_only*: "in
Knack, no Google access — request access", for clients we plainly had access
to. Nothing errored. That is the failure this file exists to stop repeating.

## What it is

A single sweep, run on a schedule by one worker, joined to clients once, and
written through `hub/jsonstore.py` so it survives a deploy and both gunicorn
workers read the same copy. Every page then reads a dictionary.

## How a resource is joined to a client

In strict order, and the winning rule is recorded on every row, because a
mapping nobody can explain is a mapping nobody will trust:

    attached    a human attached it on the client record. Beats everything.
    domain      the resource carries a URL, and that domain is the client's.
    name        exact normalised name match through hub/client_key.py.
    unmapped    none of the above — listed, with candidates where there are any.

Two rules from CLAUDE.md are load-bearing here:

  * **The URL is the join key, not the name.** A GSC property *is* a URL and a
    GTM container carries its domains, so those join hard. A GA4 property
    summary carries no URL at all — it can only ever be name-matched, and rows
    say so rather than implying the same confidence as a domain hit.
  * **Never match on a substring.** "Riverside HVAC" must not absorb
    "Riverside HVAC LLC". `client_key.resolve()` matches exactly or reports
    the candidates and matches nothing.

## What it deliberately does not do

It does not decide that an unmapped resource belongs to nobody. Unmapped means
*this index could not tell*, and the QA report shows those first — they are the
actionable half. An empty index reports "never built", never "no accounts".
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

from hub import jsonstore

# The sweep is the expensive thing, so the index is deliberately allowed to be
# old rather than rebuilt on demand. Six hours: a GA4 property created this
# morning is findable this afternoon, and the daily Tag Manager quota is never
# troubled by four sweeps.
DEFAULT_MAX_AGE = 6 * 3600


def _path() -> str:
    return os.path.join(jsonstore.data_dir("google"), "account_index.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def load() -> dict:
    """The stored index. Never raises, and never invents an empty one.

    `built_at` empty means the sweep has never run — which reads very
    differently from "swept, found nothing", and the pages say so.
    """
    data = jsonstore.read_json(_path(), default=None)
    if not isinstance(data, dict):
        return {"built_at": "", "items": [], "accounts": [], "errors": [],
                "last_attempt": "", "last_error": "", "never_built": True}
    data.setdefault("items", [])
    data.setdefault("accounts", [])
    data.setdefault("errors", [])
    data.setdefault("last_attempt", "")
    data.setdefault("last_error", "")
    data["never_built"] = not data.get("built_at")
    return data


def age_seconds() -> float | None:
    """How old the index is, or None if it has never been built."""
    built = load().get("built_at") or ""
    if not built:
        return None
    try:
        then = datetime.fromisoformat(built)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def is_stale(max_age: int = DEFAULT_MAX_AGE) -> bool:
    age = age_seconds()
    return age is None or age > max_age


def status() -> dict:
    """Enough for /diagnostics and the QA report header to be honest."""
    data = load()
    age = age_seconds()
    items = data.get("items") or []
    mapped = [i for i in items if i.get("client")]
    return {
        "built_at": data.get("built_at") or None,
        "never_built": bool(data.get("never_built")),
        "last_attempt": data.get("last_attempt") or None,
        "last_error": data.get("last_error") or "",
        "age_seconds": age,
        "age_hours": round(age / 3600, 1) if age is not None else None,
        "stale": is_stale(),
        "resources": len(items),
        "mapped": len(mapped),
        "unmapped": len(items) - len(mapped),
        "accounts": data.get("accounts") or [],
        "errors": data.get("errors") or [],
        "by_platform": _count(items, "platform"),
        "by_match": _count(mapped, "match"),
    }


def _count(items: list, key: str) -> dict:
    out: dict[str, int] = {}
    for i in items:
        out[str(i.get(key) or "—")] = out.get(str(i.get(key) or "—"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------

def _domains_of(item: dict) -> list[str]:
    """Every domain a resource carries, canonicalised.

    Reads the explicit `domains` list the fetchers now provide and falls back
    to scraping `search_extra`, because an index written before that field
    existed is still on the disk and must not silently stop joining.
    """
    from hub.client_context import canonical_domain
    raw = list(item.get("domains") or [])
    if not raw:
        blob = " ".join(str(item.get(k) or "") for k in
                        ("search_extra", "resource_id", "name"))
        raw = re.findall(r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+", blob.lower())
    out = []
    for d in raw:
        c = canonical_domain(d)
        # A bare TLD or a Google-owned host is not a client's domain, and
        # letting one through maps half the book to whoever owns it.
        if c and "." in c and not c.endswith(("google.com", "googleapis.com",
                                              "gstatic.com", "blogspot.com")):
            out.append(c)
    return sorted(set(out))


def _registry_clients() -> list[str]:
    """Every client name the registry knows. Empty rather than raising."""
    try:
        from hub import clients_registry
        return [str(r.get("name") or "").strip()
                for r in (clients_registry.all_clients() or [])
                if str(r.get("name") or "").strip()]
    except Exception:                                   # noqa: BLE001
        return []


def _attachment_map() -> dict[str, str]:
    """resource id -> client, from what a human attached by hand.

    Somebody clicking "attach this property to this client" is the strongest
    evidence there is, and it is the only rule here allowed to beat a domain.
    The ids are lower-cased because a GTM public id is written GTM-ABC123 in
    one place and gtm-abc123 in another, and a case-sensitive lookup would
    drop the attachment without saying so.
    """
    out: dict[str, str] = {}
    try:
        from hub import seo
    except Exception:                                   # noqa: BLE001
        return out
    for name in _registry_clients():
        try:
            links = seo.get_links(name) or {}
        except Exception:                               # noqa: BLE001
            continue
        for _kind, items in links.items():
            for it in (items or []):
                if isinstance(it, str):
                    out[it.strip().lower()] = name
                    continue
                if not isinstance(it, dict):
                    continue
                for key in ("resource_id", "id", "property_id", "container_id",
                            "public_id", "measurement_id"):
                    val = str(it.get(key) or "").strip().lower()
                    if val:
                        out[val] = name
    return out


def _client_by_domain() -> dict[str, str]:
    """canonical domain -> client name.

    Straight from client_key's alias index, which is the registry's own
    domain map — not a second copy assembled here. A domain two clients share
    is left out entirely rather than awarded to whichever came first: that is
    precisely the guess the billing audit used to make.
    """
    try:
        from hub import client_key
        idx = client_key.alias_index()
    except Exception:                                   # noqa: BLE001
        return {}
    conflicts = set(idx.get("domain_conflicts") or {})
    return {dom: entry.get("name", "")
            for dom, entry in (idx.get("by_domain") or {}).items()
            if dom and dom not in conflicts and entry.get("name")}


def match_item(item: dict, *, attachments: dict, by_domain: dict) -> dict:
    """Which client this resource belongs to, and on what evidence.

    Returns the item with `client`, `match` and `match_detail` set. `client`
    empty means unmapped — which is a finding, not a failure.
    """
    out = dict(item)
    out["domains"] = _domains_of(item)
    rid = str(item.get("resource_id") or "").strip().lower()

    # 1. A human said so.
    if rid and rid in attachments:
        out["client"] = attachments[rid]
        out["match"] = "attached"
        out["match_detail"] = "Attached to this client on the client record."
        return out

    # 2. The domain. The join key of record.
    for d in out["domains"]:
        if d in by_domain:
            out["client"] = by_domain[d]
            out["match"] = "domain"
            out["match_detail"] = f"Domain {d} is on this client's record."
            return out

    # 3. An exact name. resolve() offers a near match only when exactly one
    #    client can be meant; anything ambiguous comes back unmatched with the
    #    candidates, and that is what gets shown rather than a guess.
    label = str(item.get("name") or "").strip()
    if label:
        try:
            from hub import client_key
            hit = client_key.resolve(name=label)
        except Exception:                               # noqa: BLE001
            hit = None
        # resolve() always fills `client` — with the raw input when nothing
        # matched — so `known` is the test, not truthiness of the name. And
        # only an exact match counts here: "probable" is a fuzzy hit, and a
        # fuzzy hit written into a stored index becomes a fact nobody
        # re-examines.
        if hit and hit.get("known") and hit.get("confidence") == "exact":
            out["client"] = hit["client"]
            out["match"] = "name"
            out["match_detail"] = (
                "Matched on an exact name. No URL on this resource — a GA4 "
                "property carries none — so this is weaker than a domain match."
            )
            return out
        if hit and hit.get("candidates"):
            out["client"] = ""
            out["match"] = ""
            out["candidates"] = list(hit["candidates"])[:5]
            out["match_detail"] = (
                "More than one client could be meant, so nothing was matched.")
            return out

    out["client"] = ""
    out["match"] = ""
    out["match_detail"] = (
        "No domain on this resource matched a client, and the name did not "
        "match exactly." if out["domains"] else
        "This resource carries no URL and its name matched no client exactly.")
    return out


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def build(force: bool = True) -> dict:
    """Sweep Google, join to clients, and persist.

    Called by the scheduler under the leader lock, so exactly one worker
    sweeps. A page never calls this — a page reads load().
    """
    started = time.time()
    items, errors, accounts = [], [], []

    def failed(reason: str) -> dict:
        """Record a failed attempt where a reader can see it.

        A build that fails silently leaves the index reading "never built"
        for ever with nothing saying why — which is the same confident blank
        this module exists to stop. The failure is written next to the index
        so status() can report it, and it deliberately does NOT overwrite a
        good index with an empty one: yesterday's accounts, clearly labelled
        stale, beat no accounts at all.
        """
        try:
            prev = load()
            prev.pop("never_built", None)
            prev["last_error"] = reason
            prev["last_attempt"] = _now()
            jsonstore.write_json(_path(), prev)
        except Exception:                               # noqa: BLE001
            pass
        try:
            from hub import audit
            audit.log("google_index", "build_failed", detail=reason[:120])
        except Exception:                               # noqa: BLE001
            pass
        return {"ok": False, "error": reason}

    try:
        import sys
        gf = sys.modules.get("gf_app")
        if gf is None:
            from modules.google_finder import app as gf
    except Exception as exc:                            # noqa: BLE001
        return failed(f"Google Finder is not loaded ({type(exc).__name__}), "
                      f"so there is nothing to sweep.")

    try:
        raw, errors = gf.get_index(force=force)
        accounts = sorted({str(i.get("google_login") or "") for i in raw} - {""})
    except Exception as exc:                            # noqa: BLE001
        return failed(f"{type(exc).__name__} while sweeping Google: {exc}"[:300])

    # A sweep that reached Google and came back with nothing is worth saying
    # out loud rather than storing as an empty success.
    if not raw and not errors:
        try:
            connected = len(gf.connected_accounts() or [])
        except Exception:                               # noqa: BLE001
            connected = 0
        if not connected:
            return failed("No Google accounts are connected, so there is "
                          "nothing to index. Connect one at /google/login.")

    attachments = _attachment_map()
    by_domain = _client_by_domain()
    for it in raw:
        try:
            items.append(match_item(it, attachments=attachments,
                                    by_domain=by_domain))
        except Exception:                               # noqa: BLE001
            # One unmappable resource must not cost the other 2,000.
            items.append(dict(it, client="", match="",
                              match_detail="Could not be matched."))

    payload = {
        "built_at": _now(),
        "last_attempt": _now(),
        "last_error": "",                # a good build clears the last failure
        "took_seconds": round(time.time() - started, 1),
        "items": items,
        "accounts": accounts,
        "errors": errors,
    }
    jsonstore.write_json(_path(), payload)
    try:
        from hub import audit
        audit.log("google_index", "build", resources=len(items),
                  mapped=len([i for i in items if i.get("client")]),
                  accounts=len(accounts), seconds=payload["took_seconds"])
    except Exception:                                   # noqa: BLE001
        pass
    return {"ok": True, **{k: v for k, v in payload.items() if k != "items"},
            "resources": len(items),
            "mapped": len([i for i in items if i.get("client")])}


# ---------------------------------------------------------------------------
# What the pages ask for
# ---------------------------------------------------------------------------

PLATFORM_KEYS = {
    "Google Analytics": "ga4",
    "Google Tag Manager": "gtm",
    "Search Console": "gsc",
    "Google Business Profile": "gbp",
}


def for_client(name: str = "", url: str = "") -> dict:
    """Every Google resource joined to one client, grouped by platform.

    This is what Client 360 and the tool lookups read. It is a dictionary
    scan, not four API sweeps — which is the whole point.
    """
    from hub.client_context import canonical_domain
    from hub import client_key

    data = load()
    want_key = client_key.client_key(name, url)
    want_domain = canonical_domain(url)
    want_name = client_key.normalise_name(name) if name else ""

    groups: dict[str, list] = {k: [] for k in PLATFORM_KEYS.values()}
    for it in data.get("items") or []:
        client = str(it.get("client") or "")
        hit = False
        if client and want_name and client_key.normalise_name(client) == want_name:
            hit = True
        elif client and want_key and client_key.client_key(client) == want_key:
            hit = True
        elif want_domain and want_domain in (it.get("domains") or []):
            # A resource carrying the client's own domain belongs to them even
            # if the index could not name the client — the domain IS the key.
            hit = True
        if hit:
            groups.setdefault(PLATFORM_KEYS.get(it.get("platform"), "other"),
                              []).append(it)

    return {
        "client": name, "url": url,
        "built_at": data.get("built_at") or None,
        "never_built": bool(data.get("never_built")),
        "stale": is_stale(),
        "total": sum(len(v) for v in groups.values()),
        **groups,
    }


def rows(include_mapped: bool = True) -> list[dict]:
    """One row per Google resource, for the QA report.

    Unmapped first: those are the rows somebody has to do something about.
    """
    out = []
    for it in load().get("items") or []:
        if not include_mapped and it.get("client"):
            continue
        out.append({
            "platform": it.get("platform") or "",
            "type": it.get("type") or "",
            "name": it.get("name") or "",
            "resource_id": it.get("resource_id") or "",
            "account_name": it.get("account_name") or "",
            "google_login": it.get("google_login") or "",
            "domains": it.get("domains") or [],
            "client": it.get("client") or "",
            "match": it.get("match") or "",
            "match_detail": it.get("match_detail") or "",
            "candidates": it.get("candidates") or [],
            "open_url": it.get("open_url") or "",
        })
    out.sort(key=lambda r: (bool(r["client"]), r["platform"], r["name"].lower()))
    return out


def unmapped() -> list[dict]:
    return [r for r in rows() if not r["client"]]


def clients_missing(platform_key: str) -> list[str]:
    """Clients in the index with nothing on one platform.

    Answers "who has no GA4?" from the index rather than from a live sweep,
    which is what the existing no-analytics and no-gtm QA reports were paying
    for on every load.
    """
    have, seen = set(), set()
    for it in load().get("items") or []:
        c = str(it.get("client") or "")
        if not c:
            continue
        seen.add(c)
        if PLATFORM_KEYS.get(it.get("platform")) == platform_key:
            have.add(c)
    return sorted(seen - have, key=str.lower)
