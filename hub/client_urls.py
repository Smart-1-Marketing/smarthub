"""Find a website for a client who has none, out of the data we already hold.

`hub/client_context.url_audit()` has been able to say *which* clients have no
URL for a while. That is the easy half of the problem and the useless half: a
client with no URL cannot be joined to a scan, a brand lookup, a Simvoly
project or anything else keyed on domain — they are invisible to every
cross-tool report — and a list of names nobody can act on does not change that.

Their URL is almost never actually missing. It is in a different table:

* the **click-thru on their live products** is by definition a page on their
  site, and `hub/knack_products.scan_domains()` already extracts it;
* the **Knack website registry** (object_153) carries a domain per client;
* a **Simvoly project** we host for them has one;
* a **site scan** somebody ran was run against a URL;
* a **Google access request** recorded their website in order to ask for
  access to it.

So this reads all five, groups what they say by canonical domain, and proposes
the answer with its evidence attached. It writes nothing on its own.

Three rules, each of which is a way to attach the wrong website to a client
— which is worse than attaching none, because every downstream report then
agrees confidently about the wrong company.

**Names match exactly or not at all.** `client_key.normalise_name()` decides,
so "Riverside HVAC, LLC" and "The Riverside HVAC Co." are one client and
"Riverside Plumbing" is not. No substring, no fuzzy pass — that is the rule
`client_key.resolve()` exists to enforce and the one the billing audit broke.

**Agreement is the confidence.** One source saying a domain is a *suggestion*.
Two independent sources saying the same domain is close to proof, and the
proposal says which sources and what they were. A human still accepts it.

**A source that could not be read says so.** An unreachable Knack or a missing
table is reported by name in `sources`, never as "no candidates found" — those
are different answers and only one of them means "this client really has no
website anywhere in our data".

## Accepting one

`accept()` writes a small overlay file through `hub.jsonstore`, keyed by the
normalised client name, and `clients_registry.all_clients()` applies it on read
to clients that still have no URL of their own. It is deliberately an overlay
rather than an edit: Knack is the source of truth for a client record and this
Hub does not write to it, so the day the real record gains a URL, that one wins
and the overlay stops being consulted. The row carries who accepted it and
when, because a URL nobody can trace is how the guesses get in.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from hub import jsonstore
from hub.client_context import canonical_domain
from hub.client_key import normalise_name

# Ordered strongest first. `weight` breaks ties when two domains have the same
# number of sources behind them; it is not a score anyone sees.
SOURCES: dict[str, dict] = {
    "product_clickthru": {
        "label": "Live product click-thru",
        "weight": 5,
        "why": "The destination URL on a campaign we are running for them "
               "right now. It is their site by definition.",
    },
    "knack_website": {
        "label": "Knack website registry",
        "weight": 4,
        "why": "object_153 records a domain against the client.",
    },
    "simvoly": {
        "label": "Smart 1 Sites project",
        "weight": 4,
        "why": "A live Simvoly project already linked to this client.",
    },
    "scan": {
        "label": "Site scan",
        "weight": 3,
        "why": "Somebody ran an Insites audit against this URL for them.",
    },
    "google_access": {
        "label": "Google access request",
        "weight": 3,
        "why": "The website recorded when we asked for GA4/GSC access.",
    },
}

MAX_CANDIDATES_PER_CLIENT = 6

# ---------------------------------------------------------------------------
# Domains that are never a client's website
# ---------------------------------------------------------------------------
# This is not a nicety. Run against this deployment's product export, *every
# single* click-thru domain came back as a file host:
#
#     33  res.cloudinary.com      22  drive.google.com
#      2  we.tl                    2  dropbox.com
#      1  s1mformstackfiles.s3.amazonaws.com
#
# Those are where the creative was delivered from, not where the campaign
# points. Without this list the tool would have proposed res.cloudinary.com as
# the website of thirty-three different clients, a human would have accepted
# one or two because the row looked plausible, and every report keyed on domain
# would then have agreed — confidently — that several unrelated companies are
# the same business.
#
# Social profiles and marketplace listings are excluded for a softer reason:
# a Facebook page is a real presence and genuinely all some clients have, but
# it is not a site we can scan, audit or match a Simvoly project against, and
# filing it as "the website" makes a client look covered when they are not.
# They are counted and named in `rejected`, so this reads as a finding rather
# than as silence.
NOT_A_WEBSITE = {
    # file and asset hosting
    "cloudinary.com", "res.cloudinary.com", "drive.google.com",
    "docs.google.com", "dropbox.com", "we.tl", "wetransfer.com",
    "amazonaws.com", "s3.amazonaws.com", "box.com", "onedrive.live.com",
    "sharepoint.com", "canva.com", "figma.com", "vimeo.com",
    # link shorteners and landing-page middlemen
    "bit.ly", "tinyurl.com", "linktr.ee", "lnk.bio", "rebrand.ly",
    "mailchi.mp", "forms.gle", "formstack.com", "hubs.ly", "qrco.de",
    # social, review and marketplace profiles
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "youtu.be", "tiktok.com", "pinterest.com",
    "yelp.com", "google.com", "g.page", "goo.gl", "maps.app.goo.gl",
    "nextdoor.com", "angi.com", "homeadvisor.com", "thumbtack.com",
    "indeed.com", "ziprecruiter.com", "eventbrite.com", "etsy.com",
    "amazon.com", "ebay.com", "doordash.com", "grubhub.com", "opentable.com",
    # our own
    "smart1marketing.com", "smart1sites.com", "smart1hub.onrender.com",
}


def looks_like_a_website(domain: str) -> bool:
    """Could this domain be the client's own site?

    Subdomain-aware: `res.cloudinary.com` and `anything.s3.amazonaws.com` are
    both rejected by the registrable name, because the list would otherwise
    have to enumerate every bucket anyone has ever used.
    """
    d = str(domain or "").strip().lower().lstrip(".")
    if not d or "." not in d:
        return False
    for bad in NOT_A_WEBSITE:
        if d == bad or d.endswith("." + bad):
            return False
    return True


# ---------------------------------------------------------------------------
# The overlay store
# ---------------------------------------------------------------------------
def _store_path() -> str:
    return os.path.join(jsonstore.data_dir("clients"), "discovered_urls.json")


def overlay() -> dict:
    """{normalised name: row}. Never raises — a caller is mid-render."""
    rows = jsonstore.read_json(_store_path(), default={})
    return rows if isinstance(rows, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def accept(name: str, url: str, *, source: str = "", actor: str = "") -> dict:
    """Record a URL for a client who had none. Returns the stored row."""
    key = normalise_name(name)
    if not key:
        return {"ok": False, "error": "That client name is empty."}
    domain = canonical_domain(url)
    if not domain:
        return {"ok": False, "error": f"“{url}” is not a URL we can read."}
    row = {
        "client": str(name).strip()[:200],
        "url": url if str(url).startswith("http") else "https://" + domain,
        "domain": domain,
        "source": str(source or "")[:40],
        "accepted_by": str(actor or "")[:120],
        "accepted_at": _now(),
    }
    rows = overlay()
    rows[key] = row
    jsonstore.write_json(_store_path(), rows, indent=1)
    _forget_registry_cache()
    return {"ok": True, "row": row}


def clear(name: str) -> dict:
    key = normalise_name(name)
    rows = overlay()
    if key not in rows:
        return {"ok": False, "error": "Nothing was recorded for that client."}
    rows.pop(key, None)
    jsonstore.write_json(_store_path(), rows, indent=1)
    _forget_registry_cache()
    return {"ok": True}


def _forget_registry_cache() -> None:
    """The registry caches for a minute; an accepted URL must show at once."""
    try:
        from hub import clients_registry
        clients_registry._cache["at"] = 0          # noqa: SLF001 - same package
    except Exception:                                       # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# The sources
# ---------------------------------------------------------------------------
# Sightings thrown away for pointing at somewhere that is not a website, kept
# so the report can say so out loud instead of coming back empty.
_rejected: dict[str, int] = {}


def _add(found: dict, name: str, raw_url: str, source: str, detail: str = "") -> None:
    """File one (client, domain) sighting. Silent on anything unusable."""
    key = normalise_name(name)
    domain = canonical_domain(raw_url)
    if not key or not domain:
        return
    if not looks_like_a_website(domain):
        _rejected[domain] = _rejected.get(domain, 0) + 1
        return
    per_client = found.setdefault(key, {})
    entry = per_client.setdefault(domain, {
        "domain": domain,
        "url": raw_url if str(raw_url).startswith("http") else "https://" + domain,
        "sources": [], "details": [],
    })
    if source not in entry["sources"]:
        entry["sources"].append(source)
    if detail and detail not in entry["details"]:
        entry["details"].append(detail[:200])


def _from_products(found: dict) -> dict:
    """Click-thru URLs on running products. One call covers every client."""
    from hub import knack_products
    data = knack_products.scan_domains()
    for row in data.get("domains") or []:
        _add(found, row.get("client", ""), row.get("domain", ""),
             "product_clickthru",
             f"{row.get('from_field', 'url')} on a live product"
             + (" (deep link reduced to the host)"
                if row.get("was_deep_link") else ""))
    return {"rows": len(data.get("domains") or []),
            "note": f"read from {data.get('source', 'Knack')}"}


def _from_knack_websites(found: dict) -> dict:
    from hub import knack_websites
    rows = knack_websites.rows()
    for row in rows:
        _add(found, row.get("client_name") or row.get("organization") or "",
             row.get("domain") or "", "knack_website",
             (row.get("platform") or "") and f"platform: {row['platform']}")
    return {"rows": len(rows), "note": "object_153"}


def _from_simvoly(found: dict) -> dict:
    """Live Simvoly projects, by the client name already written on them.

    Active only, for the reason `sites_match.is_active` gives: an expired
    project's domain is often repointed, and proposing it here would hand a
    client somebody else's website.
    """
    from hub.sites_match import _is_platform, _site_rows, is_active
    rows = [r for r in _site_rows() if is_active(r)]
    used = 0
    for row in rows:
        name = str(row.get("internal_client_name") or "").strip()
        if not name:
            continue                       # unlinked projects are the other tool
        domain = canonical_domain(row.get("domain") or row.get("url") or "")
        if not domain or _is_platform(domain):
            continue
        used += 1
        _add(found, name, domain, "simvoly",
             f"project {row.get('project_id', '')}".strip())
    return {"rows": len(rows), "used": used, "note": "live projects only"}


def _from_table(found: dict, store_name: str, source: str) -> dict:
    """One of the module tables `hub/client_key` already knows how to read.

    Through that module's own reader rather than fresh SQL here: it handles a
    table that does not exist yet, a column a migration never added, and the
    engine being unavailable, and every one of those is a real state on this
    deployment.
    """
    from hub.client_key import _STORES, _read_store          # noqa: SLF001
    store = next((s for s in _STORES if s["module"] == store_name), None)
    if store is None:
        return {"rows": 0, "error": f"{store_name} is not a known store."}
    rows, err, truncated = _read_store(store, 4000)
    if err:
        return {"rows": 0, "error": err}
    for row in rows:
        _add(found, row.get(store["name_col"]) or "",
             (row.get(store["url_col"]) if store["url_col"] else "")
             or (row.get(store["domain_col"]) if store["domain_col"] else "")
             or "", source)
    return {"rows": len(rows), "truncated": truncated}


_READERS = (
    ("product_clickthru", _from_products),
    ("knack_website", _from_knack_websites),
    ("simvoly", _from_simvoly),
    ("scan", lambda f: _from_table(f, "scans", "scan")),
    ("google_access", lambda f: _from_table(f, "google_access", "google_access")),
)


def gather(only: str = "") -> tuple[dict, list[dict]]:
    """Everything every source knows, as {client key: {domain: sighting}}.

    Returns the status of each source beside it. A source that failed is
    reported by name — "we could not read Knack" and "Knack has nothing for
    them" must never look alike.
    """
    found: dict[str, dict] = {}
    _rejected.clear()
    status = []
    for key, reader in _READERS:
        if only and only != key:
            continue
        spec = SOURCES[key]
        try:
            info = reader(found) or {}
            # A reader that *returns* an error (a table that does not exist
            # yet, a column a migration never added) is as unread as one that
            # raised. Reporting it as ok would make "nothing found" and "not
            # looked at" the same answer, which is the failure this whole
            # module is against.
            status.append({"source": key, "label": spec["label"],
                           "ok": not info.get("error"), **info})
        except Exception as exc:                            # noqa: BLE001
            status.append({"source": key, "label": spec["label"], "ok": False,
                           "error": f"{type(exc).__name__}: {exc}"[:200]})
    return found, status


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def _rank(candidates: list[dict]) -> list[dict]:
    """Most-corroborated first, strongest source breaking the tie."""
    def score(c):
        best = max((SOURCES.get(s, {}).get("weight", 0) for s in c["sources"]),
                   default=0)
        return (len(c["sources"]), best)
    return sorted(candidates, key=score, reverse=True)


def missing(limit: int = 2000, include_found: bool = False) -> dict:
    """Every client with no URL, and what the rest of the Hub knows about them.

    `include_found` returns the clients we already have a URL for as well, so
    the page can say how much of the registry this covers rather than only
    showing the gap.
    """
    try:
        from hub import clients_registry
        clients = clients_registry.all_clients()
    except Exception as exc:                                # noqa: BLE001
        return {"error": f"The client registry is unreadable ({type(exc).__name__}).",
                "clients": [], "sources": []}

    found, status = gather()
    accepted = overlay()

    rows, with_url, solved = [], 0, 0
    examined = clients[:limit]
    for c in examined:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        key = normalise_name(name)
        has_url = bool(canonical_domain(c.get("url") or c.get("domain") or ""))
        if has_url:
            with_url += 1
            if not include_found:
                continue

        candidates = _rank(list((found.get(key) or {}).values()))
        for cand in candidates:
            cand["labels"] = [SOURCES[s]["label"] for s in cand["sources"]
                              if s in SOURCES]
            cand["confidence"] = "strong" if len(cand["sources"]) > 1 else "possible"
            cand["why"] = (
                f"{len(cand['sources'])} independent sources agree on this "
                "domain: " + ", ".join(cand["labels"]) + "."
                if len(cand["sources"]) > 1 else
                (SOURCES[cand["sources"][0]]["why"] if cand["sources"] else ""))
        candidates = candidates[:MAX_CANDIDATES_PER_CLIENT]
        if candidates and not has_url:
            solved += 1

        rows.append({
            "client": name,
            "slug": c.get("slug", ""),
            "live": bool(c.get("live")),
            "running_count": c.get("running_count", 0),
            "product_count": c.get("product_count", 0),
            "source": c.get("source", ""),
            "has_url": has_url,
            "url": c.get("url", ""),
            "accepted": accepted.get(key) or None,
            "candidates": candidates,
        })

    # Clients we are actively running work for come first: a live client with
    # no website is costing us a report every week, and a dormant one is not.
    rows.sort(key=lambda r: (-int(bool(r["candidates"])), -int(r["live"]),
                             -r["running_count"], r["client"].lower()))

    unreadable = [s for s in status if not s.get("ok")]
    note = ("Nothing has been written. A URL attached to the wrong client is "
            "worse than none — every report keyed on domain would then agree, "
            "confidently, about the wrong company. Accept the ones you "
            "recognise.")
    if unreadable:
        note += (" " + ", ".join(s["label"] for s in unreadable) +
                 " could not be read, so this is a floor, not a total.")
    if len(clients) > len(examined):
        note += (f" Only the first {len(examined)} of {len(clients)} clients "
                 "were checked.")

    without = [r for r in rows if not r["has_url"]]
    rejected = sorted(_rejected.items(), key=lambda kv: -kv[1])[:12]
    if rejected:
        note += (" " + sum(n for _, n in rejected).__str__() +
                 " sighting(s) pointed at a file host, a shortener or a social "
                 "profile rather than a website (" +
                 ", ".join(f"{d} ×{n}" for d, n in rejected[:4]) +
                 ") and were left out — those are where creative was delivered "
                 "from, not the client's site.")

    return {
        "clients_on_file": len(clients),
        "checked": len(examined),
        "rejected_domains": [{"domain": d, "count": n} for d, n in rejected],
        "with_url": with_url,
        "without_url": len(without),
        "with_candidate": solved,
        "no_candidate": len(without) - solved,
        "clients": rows,
        "sources": status,
        "sources_unreadable": len(unreadable),
        "accepted_count": len(accepted),
        "note": note,
    }
