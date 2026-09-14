"""Which Smart 1 Suite sub-account belongs to which client.

Every location-scoped thing the Hub can do -- reading a client's Forms,
pushing their Social Planner posts, minting a token at all -- needs this one
fact, and until it is recorded the feature has no answer for that client.

## Why its own store

It lived on ``image_picker_clients.ghl_location_id``, a hand-typed column on
the image picker's client table. That was fine while the mapping was
incidental: a client got a row there because somebody provisioned them an
upload gallery, and the sub-account id was one more field on it.

With the Marketplace app installed across several hundred sub-accounts it is
the wrong home, because it couples **"this client has a Suite sub-account"**
to **"somebody made them an upload gallery"**. Mapping the book through that
column would mean creating hundreds of gallery rows as a side effect nobody
asked for -- and `modules/image_picker/provisioning.py` is explicit that
creating a gallery is asked for, never assumed.

**The old column is still read, and nothing is migrated.** A row recorded
there goes on answering, exactly as `audit.LOG_NAMES` and
`video_library.TAG_ALIASES` keep matching a spelling already on disk rather
than rewriting what is stored. `location_for()` prefers this store and falls
back to the column, so the five rows that carry one keep working and nobody
has to move them.

## The rules, each a way to be quietly wrong

**A location belongs to one client, and a client may hold several
locations.** Two clients claiming one sub-account makes "whose data may this
person see" unanswerable, which is the worst outcome any tool here can
produce, and stays refused and named -- the refusal `hub/suite_accounts.py`
makes in `client_for_location()`. The other direction used to be refused the
same way, on the reasoning that two sub-accounts for one client makes "where
do we post" unanswerable -- and that reasoning was wrong about the shape of
the book: a multi-location business (Healthy Pets, with a Suite sub-account
per storefront) is not an error to refuse, it is the ordinary case, and a
client that can never record a second location has no way to represent one.
So a client may hold several, and exactly one is **primary** -- the answer
`location_for()` and everything built on it (`token_for()`, the Forms card,
the Social Planner push) reads when it needs a single target. The first
location linked becomes primary automatically; a rep changes it with
`set_primary()`. Attaching a second location never silently changes which
one is primary -- that is a choice, not a side effect of a search result.

**A proposal matches exactly or not at all.** Canonical domain first, then an
exact normalised name, through `hub/client_key.py`. Never a substring:
"Riverside HVAC" must not collect "Riverside HVAC Supply", and mapping one
client's sub-account to another publishes their content on somebody else's
page.

**Two candidates propose neither**, and name both. Picking between them is
picking whose account a client's work lands in.

**Nothing is written by looking.** `proposals()` reads and returns
candidates; `link()` is the press. A mapping this consequential is not made
by a page load.

**The client is stored by name, never the derived key** -- the
`hub/client_key.py` rule, so a client renamed in Knack re-joins on the next
read rather than leaving a stale key behind.

**Nothing is written to Suite or to Knack.** This is a Hub overlay:
unlinking leaves both systems exactly as they were.
"""
from __future__ import annotations

import time

STORE = "suite_map.json"

# What a lookup can answer. Kept as strings rather than booleans because
# "nobody recorded it" and "we could not look" send somebody to different
# places, and neither is "this client has no sub-account".
CONNECTED = "connected"
NOT_CONNECTED = "not_connected"
NOT_MEASURED = "not_measured"
AMBIGUOUS = "ambiguous"


def _path() -> str:
    import os
    from . import jsonstore
    return os.path.join(jsonstore.data_dir("suite"), STORE)


def _read() -> dict:
    from . import jsonstore
    blob = jsonstore.read_json(_path(), default=None)
    if not isinstance(blob, dict):
        return {"links": {}}
    links = blob.get("links")
    return {"links": links if isinstance(links, dict) else {}}


def _write(blob: dict) -> None:
    from . import jsonstore
    jsonstore.write_json(_path(), blob)


def _norm(name: str) -> str:
    return str(name or "").strip().lower()


def _entries(rec) -> list[dict]:
    """One client's stored record, normalized to a list of location entries.

    Reads two shapes. The one written from here on is `{"client":...,
    "locations": [{"location_id","by","at","primary"}, ...]}` -- a client may
    hold several. Everything written before a client could hold more than one
    is the single-record shape `{"client","location_id","by","at"}`, and it is
    read as-is rather than migrated, the way `audit.LOG_NAMES` and
    `video_library.TAG_ALIASES` go on matching a spelling already on disk.
    """
    if not isinstance(rec, dict):
        return []
    locs = rec.get("locations")
    if isinstance(locs, list):
        return [dict(l) for l in locs
                if isinstance(l, dict) and str(l.get("location_id") or "").strip()]
    loc = str(rec.get("location_id") or "").strip()
    if loc:
        return [{"location_id": loc, "by": rec.get("by", ""),
                 "at": rec.get("at", 0), "primary": True}]
    return []


def links() -> list[dict]:
    """Every recorded pairing, newest first. One row per client per location."""
    rows = []
    for key, rec in (_read()["links"] or {}).items():
        client_name = str((rec or {}).get("client") or key)
        for e in _entries(rec):
            rows.append({"client": client_name,
                         "location_id": str(e["location_id"]).strip(),
                         "by": str(e.get("by") or ""),
                         "at": e.get("at") or 0,
                         "primary": bool(e.get("primary"))})
    rows.sort(key=lambda r: -(r["at"] or 0))
    return rows


def client_locations(client: str) -> list[dict]:
    """Every location recorded for one client, primary first."""
    blob = _read()
    rec = blob["links"].get(_norm(client))
    entries = _entries(rec)
    entries.sort(key=lambda e: (0 if e.get("primary") else 1, -(e.get("at") or 0)))
    return entries


def link(client: str, location_id: str, by: str = "") -> dict:
    """Record one pairing. Refuses only a location already claimed elsewhere.

    A client may already hold other locations -- a multi-location business is
    the ordinary case here, not an error -- so this adds to what is on file
    rather than replacing it. The refusal that remains is the one that
    matters: a sub-account already recorded against a *different* client is
    either a mistake in this press or a mistake in the earlier one, and
    quietly taking the newer answer is how one client's posts reach another
    client's page with nothing on any screen saying so. The first location a
    client is given becomes primary automatically -- some answer has to be
    "the" location for token_for() and the Social Planner push, and the first
    one recorded is the least arbitrary default there is.
    """
    client = str(client or "").strip()
    location_id = str(location_id or "").strip()
    if not client:
        return {"ok": False, "detail": "No client was named."}
    if not location_id:
        return {"ok": False, "detail": "No sub-account was named."}

    blob = _read()
    key = _norm(client)
    for other_key, rec in (blob["links"] or {}).items():
        if other_key == key:
            continue
        for e in _entries(rec):
            if e["location_id"] == location_id:
                owner = (rec or {}).get("client") or other_key
                return {"ok": False, "conflict": "location",
                        "detail": f"That sub-account is already recorded against "
                                  f"{owner}. Unlink it there first if this one "
                                  f"is right."}

    entries = _entries(blob["links"].get(key))
    if any(e["location_id"] == location_id for e in entries):
        return {"ok": True, "client": client, "location_id": location_id,
                "already": True}
    entries.append({"location_id": location_id, "by": str(by or ""),
                    "at": int(time.time()), "primary": not entries})
    blob["links"][key] = {"client": client, "locations": entries}
    _write(blob)
    return {"ok": True, "client": client, "location_id": location_id}


def set_primary(client: str, location_id: str) -> dict:
    """Which of a client's several locations is "the" one automated single-
    target consumers (token_for(), the Social Planner push) use. Never a side
    effect of attaching a second one -- that is a choice a rep makes."""
    client = str(client or "").strip()
    location_id = str(location_id or "").strip()
    blob = _read()
    key = _norm(client)
    entries = _entries(blob["links"].get(key))
    if not any(e["location_id"] == location_id for e in entries):
        return {"ok": False,
                "detail": "That sub-account is not recorded for this client."}
    for e in entries:
        e["primary"] = (e["location_id"] == location_id)
    blob["links"][key] = {"client": client, "locations": entries}
    _write(blob)
    return {"ok": True, "client": client, "location_id": location_id}


def unlink(client: str, location_id: str = "") -> dict:
    """Remove one location (or, with no `location_id`, every location this
    client holds -- the old single-account behavior)."""
    blob = _read()
    key = _norm(client)
    rec = blob["links"].get(key)
    entries = _entries(rec)
    if not entries:
        return {"ok": False, "detail": "That client has no sub-account recorded."}
    location_id = str(location_id or "").strip()
    if location_id:
        kept = [e for e in entries if e["location_id"] != location_id]
        if len(kept) == len(entries):
            return {"ok": False,
                    "detail": "That sub-account is not recorded for this client."}
    else:
        kept = []
    if kept and not any(e.get("primary") for e in kept):
        kept[0]["primary"] = True
    if kept:
        blob["links"][key] = {"client": (rec or {}).get("client") or client,
                              "locations": kept}
    else:
        blob["links"].pop(key, None)
    _write(blob)
    return {"ok": True, "client": client}


# ------------------------------------------------------------------ lookups
#
# These read THIS STORE ONLY, and are deliberately not a second
# `location_for()`. `hub/suite_accounts.py` is the one reader every caller
# already goes through -- the forms card, the Social Planner push,
# `token_for()` -- and it consults these first and its own older column
# second. Two functions answering "which sub-account is this client" is how
# they come to disagree, which on this particular question means one client's
# work landing in another client's account.


def recorded_location(client: str, url: str = "") -> dict:
    """This store's answer for a single-target caller, or nothing.

    A client holding several locations is not ambiguous any more -- it is a
    multi-location business, and refusing to answer forever is not a service
    to it. The **primary** location is what a single-target caller (a token,
    a Social Planner push) gets; `others` carries the rest for a caller that
    wants to show or choose among them, the way Client 360's Suite Account
    card does.
    """
    from . import client_key
    client = str(client or "").strip()
    if not client:
        return {"state": NOT_CONNECTED, "location_id": ""}
    hits = [r for r in links()
            if client_key.same_client(client, url, r["client"], "")]
    if not hits:
        return {"state": NOT_CONNECTED, "location_id": ""}
    ids = sorted({r["location_id"] for r in hits})
    primary = next((r for r in hits if r.get("primary")), hits[0])
    others = [i for i in ids if i != primary["location_id"]]
    return {"state": CONNECTED, "location_id": primary["location_id"],
           "others": others}


def recorded_client(location_id: str) -> dict:
    """The reverse. Exact string equality on an opaque id and nothing else --
    there is no near miss worth entertaining, and a prefix match here would be
    a way to reach another client's data."""
    location_id = str(location_id or "").strip()
    if not location_id:
        return {"state": NOT_CONNECTED, "client": ""}
    hits = [r for r in links() if r["location_id"] == location_id]
    names = sorted({r["client"] for r in hits}, key=_norm)
    if len({_norm(n) for n in names}) > 1:
        return {"state": AMBIGUOUS, "client": "", "candidates": names,
                "detail": "More than one client records this sub-account."}
    if names:
        return {"state": CONNECTED, "client": names[0]}
    return {"state": NOT_CONNECTED, "client": ""}


# ------------------------------------------------------- the sub-account list

API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
PAGE = 100
MAX_PAGES = 40          # 4,000 sub-accounts; a ceiling, not an expectation


def fetch_locations() -> tuple[list[dict], str]:
    """Every sub-account in the company, with the website each publishes.

    Deliberately not `ghl_oauth.installed_locations()`, which answers a
    different question -- which sub-accounts the app can mint a token for --
    and carries no website. A domain is the only thing in a location record
    that identifies a business exactly, so without it every proposal would
    fall back to a name match and the safest rule here would be unavailable.

    Returns `(rows, error)`: an empty list with no error means the company
    genuinely has none, which is not the same as a read that failed.
    """
    import requests
    from . import ghl_oauth

    try:
        token, company = ghl_oauth.agency_token(), ghl_oauth.company_id()
    except Exception as exc:                                  # noqa: BLE001
        return [], (f"The Smart 1 Suite connection could not be read "
                    f"({type(exc).__name__}).")
    if not company:
        return [], "No Smart 1 Suite company id is configured."

    out, skip = [], 0
    for _ in range(MAX_PAGES):
        try:
            r = requests.get(
                f"{API_BASE}/locations/search",
                params={"companyId": company, "limit": str(PAGE),
                        "skip": str(skip), "order": "asc"},
                headers={"Authorization": f"Bearer {token}",
                         "Version": API_VERSION, "Accept": "application/json"},
                timeout=30)
        except Exception as exc:                              # noqa: BLE001
            return out, f"Smart 1 Suite could not be reached ({type(exc).__name__})."
        if r.status_code in (401, 403):
            return out, ("Smart 1 Suite refused the request. The app may not "
                         "have been granted locations.readonly.")
        if not r.ok:
            return out, f"Smart 1 Suite returned HTTP {r.status_code}."
        try:
            page = (r.json() or {}).get("locations") or []
        except ValueError:
            return out, "Smart 1 Suite returned something that is not JSON."
        out.extend(page)
        if len(page) < PAGE:
            return out, ""
        skip += PAGE
    # Bounded, and never in silence: an undercount here reads as a shorter
    # book rather than as a page we stopped fetching.
    return out, (f"Stopped after {MAX_PAGES * PAGE} sub-accounts; there may "
                 f"be more that are not listed here.")


def _site_of(loc: dict) -> str:
    for key in ("website", "domain", "url"):
        v = str(loc.get(key) or "").strip()
        if v:
            return v
    return ""


def proposals() -> dict:
    """Candidate client/sub-account pairs, and everything left over.

    Nothing here writes. Every pairing is offered with the evidence that
    produced it, and a person presses.
    """
    from . import client_key, clients_registry

    locations, err = fetch_locations()
    if err and not locations:
        return {"measured": False, "error": err, "proposals": [],
                "ambiguous": [], "unmatched": [], "linked": 0}

    try:
        clients = clients_registry.all_clients()
    except Exception as exc:                                  # noqa: BLE001
        return {"measured": False,
                "error": f"The client list could not be read "
                         f"({type(exc).__name__}). Without it there is "
                         f"nothing to match against.",
                "proposals": [], "ambiguous": [], "unmatched": [],
                "linked": 0}
    if not clients:
        return {"measured": False,
                "error": "The client list came back empty, so every "
                         "sub-account would read as unmatched.",
                "proposals": [], "ambiguous": [], "unmatched": [],
                "linked": 0}

    # A client or a location already recorded is out of the running entirely:
    # re-proposing a pairing somebody has already made is how a settled
    # decision gets quietly changed.
    taken_loc = {r["location_id"] for r in links()}
    taken_client = {_norm(r["client"]) for r in links()}

    by_domain: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for c in clients:
        name = str(c.get("name") or "").strip()
        if not name or _norm(name) in taken_client:
            continue
        dom = client_key.domain_key(str(c.get("url") or c.get("domain") or ""))
        if dom:
            by_domain.setdefault(dom, []).append(c)
        nm = client_key.normalise_name(name)
        if nm:
            by_name.setdefault(nm, []).append(c)

    out, ambiguous, unmatched = [], [], []
    linked = 0
    for loc in locations:
        loc_id = str(loc.get("id") or loc.get("_id") or "").strip()
        if not loc_id:
            continue
        if loc_id in taken_loc:
            linked += 1
            continue
        loc_name = str(loc.get("name") or "").strip()
        site = _site_of(loc)
        row = {"location_id": loc_id, "location_name": loc_name,
               "website": site}

        dom = client_key.domain_key(site)
        hits = by_domain.get(dom) or [] if dom else []
        how = "domain"
        if not hits:
            hits = by_name.get(client_key.normalise_name(loc_name)) or []
            how = "name"
        names = sorted({str(h.get("name") or "").strip() for h in hits})

        if len(names) > 1:
            ambiguous.append({**row, "candidates": names, "matched_on": how})
        elif names:
            out.append({**row, "client": names[0], "matched_on": how,
                        "evidence": dom if how == "domain" else loc_name})
        else:
            unmatched.append(row)

    return {"measured": True, "error": err, "proposals": out,
            "ambiguous": ambiguous, "unmatched": unmatched,
            "linked": linked, "locations": len(locations),
            "clients": len(clients)}


def accept_many(pairs: list[dict], by: str = "") -> dict:
    """Record a screenful. Every row reports its own outcome.

    One number back would hide the two that were refused -- the rule
    `client_urls.accept_many()` works to.
    """
    results = []
    for p in pairs or []:
        if not isinstance(p, dict):
            continue
        res = link(str(p.get("client") or ""), str(p.get("location_id") or ""), by)
        results.append({"client": p.get("client"), "location_id": p.get("location_id"),
                        **res})
    return {"linked": sum(1 for r in results if r.get("ok")),
            "refused": sum(1 for r in results if not r.get("ok")),
            "results": results}
