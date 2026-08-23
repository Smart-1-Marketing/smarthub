"""One client identity, and the join that was missing between the modules.

## The finding this closes

`/api/db/structure` has reported the same high-severity risk since it was
written, and it was right:

    modules/scans          domain_key    a normalised hostname
    modules/ads_builder    client_name   free-text business name
    modules/google_access  client_name   free-text name, plus an optional
                                         hub_client_id nobody fills in
    modules/image_picker   name / slug   its own table and its own integer
                                         ids, plus the same optional
                                         hub_client_id

Each store is internally consistent. None of them agrees with any other.
"Riverside HVAC" in Ads, "Riverside HVAC LLC" in Google Access and
riverside-hvac.com in Scans are one company with three records and nothing
that says so. That is why the billing audit calls clients unmatched that are
plainly on file — and, worse, why it sometimes matches one to the wrong
record: the old fallback took the *first* name that contained the other as a
substring, so "Acme" landed on whichever of Acme Plumbing, Acme Roofing and
Acme Electric happened to come out of the dict first.

## Derived, not stored — and why

There is no new `clients` table and no `client_key` column here. Two reasons,
both already learned in this codebase:

* `create_all()` creates missing *tables*. It never adds a column to a table
  that already exists, so a new column would be silently absent on the live
  Postgres while every local test passed.
* A stored key is a second copy of the answer. It starts drifting the moment
  somebody renames a client in Knack, and then there are four identities
  instead of three.

So the key is *derived on read*, in one place, from the columns the rows
already have. Nothing to migrate, nothing to sync, and a client renamed in
Knack is re-joined on the next request.

## What a key is

    d:riverside-hvac.com     domain-backed. Exact: one domain is one company.
    n:riverside-hvac         name-backed.   A best effort, and marked as one.

The domain is the join key wherever there is one, for the reason
`hub/client_context.canonical_domain()` exists: a domain is an identifier and
a name is not. A name key is offered only so that a record with no URL on file
still has *something* to group by — `crosswalk()` reports those separately
rather than letting them look joined.

## The rule about fuzzy matching

`resolve()` never guesses silently. Domain and exact normalised name are
matches. A near match is returned only with `allow_fuzzy=True`, only when
exactly one candidate exists, and always labelled `confidence="probable"`.
Where several candidates match it returns *no* match and lists them, because
an ambiguous match reported as a fact is precisely the false alarm this module
exists to stop.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone

from hub.client_context import canonical_domain

# Suffixes and filler words that differ between systems for the same company.
# Deliberately NOT in this set: "group", "services", "solutions". Dropping
# those merges "Riverside Group" into "Riverside", which are routinely two
# different accounts — the drop list is only allowed to remove words that
# cannot distinguish two businesses.
_DROP_WORDS = {
    "inc", "llc", "l", "ltd", "co", "corp", "corporation", "company",
    "incorporated", "limited", "lp", "llp", "pllc", "pc", "plc", "dba",
    "the", "of", "and",
}

_INDEX_TTL_SECONDS = 120
_index_lock = threading.Lock()
_index_cache: dict = {"at": 0.0, "value": None}


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def normalise_name(name: str) -> str:
    """A business name reduced to what actually identifies the business.

    Lowercased, punctuation removed, legal suffixes and filler words dropped,
    single-spaced. "Riverside HVAC, LLC" and "The Riverside HVAC Co."
    both become "riverside hvac".

    Words are kept as words rather than being run together: "ab cd" and "abcd"
    are different businesses, and a normaliser that squashes the space says
    they are the same one.
    """
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    words = [w for w in s.split() if w and w not in _DROP_WORDS]
    return " ".join(words)


def name_slug(name: str) -> str:
    """The hyphenated form of a normalised name, for use inside a key."""
    return re.sub(r"[^a-z0-9]+", "-", normalise_name(name)).strip("-")


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def domain_key(value: str) -> str:
    """`d:example.com` from anything holding a URL, or "" if it holds none."""
    d = canonical_domain(value)
    return f"d:{d}" if d else ""


def name_key(name: str) -> str:
    """`n:example-co` from a business name, or "" if the name is empty."""
    s = name_slug(name)
    return f"n:{s}" if s else ""


def client_key(name: str = "", url: str = "") -> str:
    """The best key available from a name and a URL, domain first.

    This is the whole contract: given whatever a module happens to hold, this
    returns the string that means "this client" everywhere in the Hub.
    """
    return domain_key(url) or name_key(name)


def key_kind(key: str) -> str:
    """"domain", "name", or "" — how much a key can be trusted."""
    if key.startswith("d:"):
        return "domain"
    if key.startswith("n:"):
        return "name"
    return ""


def key_label(key: str) -> str:
    """The human-readable half of a key, for display."""
    return key.split(":", 1)[1] if ":" in key else key


# ---------------------------------------------------------------------------
# The registry index
# ---------------------------------------------------------------------------

def alias_index(refresh: bool = False) -> dict:
    """Every name and domain the client registry knows, pointing at one key.

    Short-cached: `clients_registry.all_clients()` is itself cached, but this
    walks every row to build three maps and is called once per resolve in a
    loop over hundreds of billing rows.

    Never raises. An index that could not be built resolves nothing, which
    leaves every caller reporting "unmatched" — the honest answer — rather
    than crashing a report.
    """
    with _index_lock:
        cached = _index_cache["value"]
        if cached is not None and not refresh \
                and time.time() - _index_cache["at"] < _INDEX_TTL_SECONDS:
            return cached

    by_domain: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    entries: dict[str, dict] = {}
    domain_conflicts: dict[str, list] = {}
    error = ""

    try:
        from hub import clients_registry
        rows = clients_registry.all_clients()
    except Exception as exc:                              # noqa: BLE001
        rows, error = [], f"{type(exc).__name__}: {exc}"

    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        dom = canonical_domain(row.get("url") or row.get("domain") or "")
        key = (f"d:{dom}" if dom else "") or name_key(name)
        if not key:
            continue
        products = row.get("product_count") or 0
        entry = entries.get(key)
        if entry is None:
            entry = entries[key] = {
                "key": key, "name": name, "domain": dom,
                "source": row.get("source") or "",
                "is_house": bool(row.get("is_house")),
                "products": products, "names": [name],
                "_candidates": [(products, name)],
            }
        else:
            if name not in entry["names"]:
                entry["names"].append(name)
                entry["_candidates"].append((products, name))
            entry["products"] = max(entry["products"], products)
            if dom:
                # More than one registry record on one domain. This is the
                # duplicate the module exists to surface, so it is recorded
                # rather than merged away silently. (The first version of this
                # check compared object identity against the entry it had just
                # created for the same key, so it could never fire and reported
                # zero conflicts against data that plainly had them.)
                domain_conflicts[dom] = list(entry["names"])
        if dom:
            by_domain.setdefault(dom, entry)
        norm = normalise_name(name)
        if norm:
            by_name.setdefault(norm, entry)

    # Pick the display name deterministically. Several records under one key is
    # normal — a franchise with a location record per store, or a reseller
    # filing each campaign separately — and whichever came first out of the
    # registry is not an answer. Most products wins (that is the parent
    # account), then the shortest name, then alphabetically.
    for entry in entries.values():
        cands = entry.pop("_candidates", None) or []
        if len(cands) > 1:
            entry["name"] = min(cands, key=lambda c: (-c[0], len(c[1]), c[1]))[1]

    index = {
        "by_domain": by_domain,
        "by_name": by_name,
        "entries": entries,
        "domain_conflicts": {d: sorted(set(n)) for d, n in domain_conflicts.items()},
        "clients": len(entries),
        "error": error,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _index_lock:
        _index_cache["value"] = index
        _index_cache["at"] = time.time()
    return index


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve(name: str = "", url: str = "", *, allow_fuzzy: bool = False,
            index: dict | None = None) -> dict:
    """Who is this, from whatever the caller happens to hold.

    Returns a dict rather than raising or returning None, so a caller can
    always show *something*: an unmatched client still gets a usable key and
    an explicit `known: False`.

    `matched_on` says which evidence won — "domain", "name", "name~" (a fuzzy
    match) or "" (nothing matched). `confidence` is "exact", "probable" or
    "unmatched", and a caller that shows a match to a human should show the
    confidence with it.
    """
    idx = index if index is not None else alias_index()
    raw_name = str(name or "").strip()
    dom = canonical_domain(url) or canonical_domain(raw_name)
    norm = normalise_name(raw_name)

    out = {
        "input": {"name": raw_name, "url": str(url or "").strip()},
        "domain": dom,
        "key": "",
        "client": raw_name,
        "matched_on": "",
        "confidence": "unmatched",
        "known": False,
        "candidates": [],
        "why": "",
    }

    # 1. Domain. One domain is one company, so this needs no corroboration.
    if dom:
        hit = idx["by_domain"].get(dom)
        out["key"] = f"d:{dom}"
        if hit:
            out.update(client=hit["name"], matched_on="domain",
                       confidence="exact", known=True,
                       why="Exact domain match against the client registry.")
            if len(hit["names"]) > 1:
                out["candidates"] = list(hit["names"])
                out["why"] += (f" {len(hit['names'])} client records share this "
                               "domain. That is one company with several "
                               "records — a franchise filed per location, or a "
                               "duplicate — so anything totalled per client "
                               "should total across all of them.")
            return out

    # 2. Exact normalised name. "Riverside HVAC, LLC" == "The Riverside HVAC Co".
    if norm:
        hit = idx["by_name"].get(norm)
        if hit:
            out.update(key=hit["key"], client=hit["name"], matched_on="name",
                       confidence="exact", known=True,
                       domain=hit["domain"] or dom,
                       why="Business names match once legal suffixes and "
                           "punctuation are ignored.")
            return out

    # 3. A near match, only when asked for and only when there is exactly one.
    #    Several candidates means we do not know, and saying so is the point:
    #    picking the first one is how a client's billing gets attributed to
    #    their namesake down the road.
    if allow_fuzzy and norm:
        near = [e for n, e in idx["by_name"].items()
                if n != norm and (n.startswith(norm + " ") or norm.startswith(n + " ")
                                  or f" {norm} " in f" {n} ")]
        seen, unique = set(), []
        for e in near:
            if e["key"] not in seen:
                seen.add(e["key"])
                unique.append(e)
        if len(unique) == 1:
            hit = unique[0]
            out.update(key=hit["key"], client=hit["name"], matched_on="name~",
                       confidence="probable", known=True,
                       domain=hit["domain"] or dom,
                       why=f"Only one client registry name contains "
                           f"“{raw_name}”. Worth an eyeball before it is "
                           f"treated as fact.")
            return out
        if len(unique) > 1:
            out["candidates"] = [e["name"] for e in unique][:8]
            out["why"] = (f"{len(unique)} clients could be “{raw_name}”. "
                          "Reporting no match rather than guessing — a wrong "
                          "match is worse than a blank one.")
            out["key"] = out["key"] or name_key(raw_name)
            return out

    out["key"] = out["key"] or name_key(raw_name)
    out["why"] = out["why"] or ("Nothing in the client registry matches this "
                                "name or domain.")
    return out


def same_client(a_name: str = "", a_url: str = "",
                b_name: str = "", b_url: str = "", *,
                index: dict | None = None) -> bool:
    """Are these two records the same client?

    Keys match, or the normalised names match. The second test matters because
    a key is only as good as the evidence behind it: a record with a URL keys
    on its domain and a record with only a name keys on the name, so two rows
    for one client can hold different keys purely because one of them knows
    more. Comparing the names as well catches that without loosening anything
    — this is exact normalised equality, never a substring.
    """
    idx = index if index is not None else alias_index()
    ka = resolve(name=a_name, url=a_url, index=idx)["key"]
    kb = resolve(name=b_name, url=b_url, index=idx)["key"]
    if ka and kb and ka == kb:
        return True
    na, nb = normalise_name(a_name), normalise_name(b_name)
    return bool(na and na == nb)


# ---------------------------------------------------------------------------
# The crosswalk — the join itself
# ---------------------------------------------------------------------------

# Read straight from the shared engine rather than by importing each module.
# Every one of these tables lives in the same Postgres behind the same pool
# (hub/extensions.py), so this needs no module import — which means the
# crosswalk still reports accurately when a module fails to import, the same
# property that makes structure_report() worth reading.
#
# Table and column names are constants in this file, never caller input.
_STORES = (
    {"module": "scans", "table": "scans", "label": "site scan",
     "name_col": "business_name", "url_col": "input_url",
     "domain_col": "domain_key", "id_col": "public_id",
     "order_col": "created_at"},
    {"module": "ads_builder", "table": "ads_proposals", "label": "ad proposal",
     "name_col": "client_name", "url_col": "", "domain_col": "",
     "id_col": "public_id", "order_col": "created_at"},
    {"module": "google_access", "table": "ga_access_request",
     "label": "access request",
     "name_col": "client_name", "url_col": "website", "domain_col": "",
     "id_col": "id", "order_col": "created_at"},
    {"module": "image_picker", "table": "image_picker_clients",
     "label": "image gallery",
     "name_col": "name", "url_col": "", "domain_col": "",
     "id_col": "slug", "order_col": "created_at"},
)


def _read_store(store: dict, limit: int) -> tuple[list[dict], str, bool]:
    """(rows, error, truncated) for one module's client-bearing table."""
    try:
        from sqlalchemy import inspect as sa_inspect, text
        from hub.extensions import shared_engine
        engine = shared_engine()
        insp = sa_inspect(engine)
        if not insp.has_table(store["table"]):
            return [], "table not created yet", False
        have = {c["name"] for c in insp.get_columns(store["table"])}
    except Exception as exc:                              # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}", False

    wanted = [store["id_col"], store["name_col"], store["url_col"],
              store["domain_col"]]
    cols = [c for c in wanted if c and c in have]
    if not cols:
        return [], "no client column on this table", False
    order = store["order_col"] if store["order_col"] in have else store["id_col"]

    sql = (f"SELECT {', '.join(cols)} FROM {store['table']} "
           f"ORDER BY {order} DESC LIMIT :n")
    try:
        with engine.connect() as conn:
            found = conn.execute(text(sql), {"n": limit + 1}).mappings().all()
    except Exception as exc:                              # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}", False

    truncated = len(found) > limit
    return [dict(r) for r in found[:limit]], "", truncated


def crosswalk(limit_per_store: int = 2000, max_clients: int = 400) -> dict:
    """Every client record in every module, grouped by one key.

    This is the join that does not exist in the data. It answers the two
    questions the modules cannot answer between them: which records are the
    same client, and which records can never be joined to anything because
    they carry a name and no URL.
    """
    idx = alias_index()
    clients: dict[str, dict] = {}
    stores: list[dict] = []
    unjoinable: list[dict] = []

    def bucket(key: str, name: str, domain: str) -> dict:
        rec = clients.setdefault(key, {
            "key": key, "kind": key_kind(key), "client": name or key_label(key),
            "domain": domain, "names": [], "modules": {}, "registry": False,
        })
        if name and name not in rec["names"]:
            rec["names"].append(name)
        if domain and not rec["domain"]:
            rec["domain"] = domain
        return rec

    # The registry first, so a client the Hub officially knows about owns the
    # display name and the module rows attach underneath it.
    for key, entry in idx["entries"].items():
        rec = bucket(key, entry["name"], entry["domain"])
        rec["registry"] = True
        rec["source"] = entry.get("source") or ""
        rec["products"] = entry.get("products") or 0
        for n in entry.get("names") or []:
            if n not in rec["names"]:
                rec["names"].append(n)

    for store in _STORES:
        rows, err, truncated = _read_store(store, limit_per_store)
        resolved = unmatched = 0
        for row in rows:
            name = str(row.get(store["name_col"]) or "").strip()
            url = str(row.get(store["url_col"]) or "") if store["url_col"] else ""
            dom = canonical_domain(row.get(store["domain_col"]) or "") \
                if store["domain_col"] else ""
            hit = resolve(name=name, url=url or dom, index=idx)
            key = hit["key"] or (f"d:{dom}" if dom else "")
            if not key:
                continue
            rec = bucket(key, hit["client"] if hit["known"] else name,
                         hit["domain"] or dom)
            mod = rec["modules"].setdefault(store["module"], [])
            if len(mod) < 25:
                mod.append({"id": str(row.get(store["id_col"]) or ""),
                            "as": name or "(unnamed)"})
            if hit["known"]:
                resolved += 1
            else:
                unmatched += 1
                if key_kind(key) == "name":
                    unjoinable.append({
                        "module": store["module"], "label": store["label"],
                        "id": str(row.get(store["id_col"]) or ""),
                        "as": name or "(unnamed)", "key": key,
                    })
        stores.append({
            "module": store["module"], "table": store["table"],
            "rows": len(rows), "resolved": resolved, "unmatched": unmatched,
            "truncated": truncated, "error": err,
            "key_column": store["domain_col"] or store["name_col"],
        })

    # A client that appears under more than one name, or in more than one
    # module, is the thing worth looking at. Everything else is working.
    # "More than one name" is not automatically an error: a franchise with a
    # record per location legitimately looks exactly like a duplicate. Both are
    # worth seeing and neither is worth asserting, so they are listed, not
    # judged.
    records = list(clients.values())
    for r in records:
        r["module_count"] = len(r["modules"])
        r["record_count"] = sum(len(v) for v in r["modules"].values())
        r["duplicate_names"] = len(r["names"]) > 1

    duplicates = sorted((r for r in records if r["duplicate_names"]),
                        key=lambda r: -len(r["names"]))
    joined = sorted((r for r in records if r["module_count"] > 1),
                    key=lambda r: -r["module_count"])

    # De-duplicate the unjoinable list by key: fifty ad proposals for one
    # unknown client is one problem, not fifty.
    by_key: dict[str, dict] = {}
    for u in unjoinable:
        e = by_key.setdefault(u["key"], {"key": u["key"], "as": u["as"],
                                         "modules": {}, "records": 0})
        e["modules"].setdefault(u["module"], 0)
        e["modules"][u["module"]] += 1
        e["records"] += 1
    orphans = sorted(by_key.values(), key=lambda e: -e["records"])

    return {
        "keys": len(records),
        "domain_keyed": sum(1 for r in records if r["kind"] == "domain"),
        "name_keyed": sum(1 for r in records if r["kind"] == "name"),
        "in_more_than_one_module": len(joined),
        "duplicate_names": len(duplicates),
        "stores": stores,
        "clients": joined[:max_clients],
        "duplicates": duplicates[:max_clients],
        "orphans": orphans[:max_clients],
        "registry_domain_conflicts": [
            {"domain": d, "clients": names}
            for d, names in sorted(idx["domain_conflicts"].items())
        ],
        "index_error": idx.get("error") or "",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": ("Keys are derived on read from the columns each module "
                 "already has — nothing is stored, so a client renamed in "
                 "Knack is re-joined on the next request. A d: key is a "
                 "domain and is exact. An n: key is a name with no URL on "
                 "file behind it: it groups records that look alike, and it "
                 "cannot be joined to a scan or anything else keyed on "
                 "domain. Those are listed under “orphans”. Several names on "
                 "one key means several client records share one website: "
                 "usually a franchise filed per location, sometimes a genuine "
                 "duplicate. They are listed rather than judged."),
    }


def for_client(name: str = "", url: str = "", limit_per_store: int = 200) -> dict:
    """Every module record belonging to one client. The Client 360 view."""
    idx = alias_index()
    who = resolve(name=name, url=url, index=idx)
    key = who["key"]
    found: dict[str, list] = {}
    errors: dict[str, str] = {}

    if not key:
        return {"key": "", "found": {}, "resolved": who,
                "note": "Nothing identifiable was passed in."}

    for store in _STORES:
        rows, err, _ = _read_store(store, limit_per_store)
        if err:
            errors[store["module"]] = err
            continue
        for row in rows:
            rname = str(row.get(store["name_col"]) or "").strip()
            rurl = str(row.get(store["url_col"]) or "") if store["url_col"] else ""
            rdom = canonical_domain(row.get(store["domain_col"]) or "") \
                if store["domain_col"] else ""
            # same_client, not a bare key comparison: a module row that has a
            # URL keys on its domain while one that has only a name keys on
            # the name, so two records for one client can hold different keys
            # simply because one of them knows more.
            if not same_client(name, url, rname, rurl or rdom, index=idx):
                continue
            found.setdefault(store["module"], []).append({
                "id": str(row.get(store["id_col"]) or ""),
                "as": rname or "(unnamed)", "label": store["label"],
            })

    return {
        "key": key,
        "kind": key_kind(key),
        "client": who["client"],
        "domain": who["domain"],
        "resolved": who,
        "found": found,
        "records": sum(len(v) for v in found.values()),
        "errors": errors,
        "note": ("Joined on the derived client key, so a record filed under a "
                 "different spelling of the name still appears — provided it "
                 "has a URL on file. One filed under a name alone can only be "
                 "matched to another name."),
    }
