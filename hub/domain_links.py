"""Attaching a domain to a client — in every system that keys on one.

Matching a site to a client used to write `internal_client_name` on a Simvoly
project and stop there. That is one of four places the join has to land, so a
rep who matched a site on the Match Clients page found Client 360 still saying
"No website record matched" and the Knack website record still carrying nobody's
name. The match was real and invisible, which is the same as not having made it.

So there is one function. `attach(domain, client)` writes:

1. the **Hub's client overlay** (`hub/client_urls.py`), so the client stops
   being one of the ones with no URL and every domain-keyed report can find
   them — additive, because a client has landing pages as well as a website;
2. the **Client 360 attachment** (`hub/seo.set_link`), so the website shows on
   the client's record rather than only in the tool that matched it;
3. **Smart 1 Sites** — `internal_client_name` on every live Simvoly project
   serving that domain, which is what the margin report reads;
4. **Knack object_153** — the client onto the website record for that domain,
   through `knack_websites.attach_client`, which is the only one of the four
   that is a write to somebody else's source of truth.

Each is reported separately, and each failure is reported by name. "Attached"
and "attached in three of four places" are different outcomes and a single tick
for both is how a rep learns not to trust the tick. Nothing here is silent, and
nothing here is a guess:

* **A project already linked to somebody else is not relinked** unless the
  caller passes `force`. A wrong `internal_client_name` attributes revenue to
  the wrong client, and quietly overwriting one is worse than refusing to.
* **A name written into a Knack connection is refused, not guessed.** That is
  `knack_api.coerce_field`'s rule and this inherits it.
* **A domain that is not a website is refused here too**, for the reason
  `client_urls.NOT_A_WEBSITE` gives at length: attaching res.cloudinary.com to
  a client makes every report agree, confidently, about the wrong company.

## Orphans

`orphans()` is the other direction: every URL the Hub holds that has no client
on it at all. Same four systems, read rather than written — a website record
with no organisation, a live Simvoly project with no internal client name, a
site scan and a Google access request nobody ever attached to a client. A
source that could not be read is named, never counted as zero.
"""
from __future__ import annotations

from hub.client_context import canonical_domain
from hub.client_urls import looks_like_a_website

# What each system is called on the screen. One place, because "Smart 1 Sites"
# and "Simvoly" being the same thing is confusing enough already.
SYSTEMS = {
    "hub": "Hub client registry",
    "c360": "Client 360 record",
    "sites": "Smart 1 Sites project",
    "knack": "Knack website registry (object_153)",
}


def _wrote(report: dict, system: str, detail: str) -> None:
    report["written"].append({"system": system, "label": SYSTEMS[system],
                              "detail": detail})


def _skipped(report: dict, system: str, why: str) -> None:
    report["skipped"].append({"system": system, "label": SYSTEMS[system],
                              "why": why})


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------
def attach(domain: str, client: str, *, actor: str = "", url: str = "",
           force: bool = False) -> dict:
    """Attach one domain to one client, everywhere it belongs."""
    client = str(client or "").strip()
    dom = canonical_domain(url or domain)
    report = {"domain": dom, "client": client, "written": [], "skipped": [],
              "ok": False}
    if not client:
        return {**report, "error": "No client was chosen."}
    if not dom:
        return {**report, "error": f"“{url or domain}” is not a URL we can read."}
    if not looks_like_a_website(dom):
        return {**report,
                "error": f"{dom} is a file host, a shortener or a social "
                         "profile — not a website we can attach to a client."}
    full = url if str(url).startswith("http") else "https://" + dom

    # 1. the Hub's own overlay
    try:
        from hub import client_urls
        out = client_urls.accept(client, full, source="attached", actor=actor)
        if out.get("ok"):
            _wrote(report, "hub", f"{dom} recorded against {client} "
                                  f"({len(out.get('sites') or [])} site(s) on file).")
        else:
            _skipped(report, "hub", out.get("error", "could not be recorded"))
    except Exception as exc:                            # noqa: BLE001
        _skipped(report, "hub", f"{type(exc).__name__}: {exc}"[:160])

    # 2. Client 360 — the attachment the client record itself reads
    try:
        from hub import seo
        seo.set_link(client, "website", {"name": client, "domain": dom,
                                         "liveUrl": full})
        _wrote(report, "c360", f"{dom} attached to the client record.")
    except Exception as exc:                            # noqa: BLE001
        _skipped(report, "c360", f"{type(exc).__name__}: {exc}"[:160])

    # 3. Smart 1 Sites
    report_sites = _write_sites(dom, client, force=force)
    for line in report_sites["written"]:
        _wrote(report, "sites", line)
    for line in report_sites["skipped"]:
        _skipped(report, "sites", line)

    # 4. Knack object_153
    try:
        from hub import knack_websites
        rec = knack_websites.record_for_domain(dom)
        if not rec:
            _skipped(report, "knack",
                     f"No website record in object_153 carries {dom}, so "
                     "there is nothing to write the client onto.")
        elif rec.get("has_client") and not force:
            _skipped(report, "knack",
                     f"That record already names “{rec['client']}”. Nothing "
                     "was overwritten.")
        elif not knack_websites.configured():
            _skipped(report, "knack",
                     "KNACK_APP_ID / KNACK_API_KEY are not set on this "
                     "deployment, so Knack cannot be written to.")
        else:
            out = knack_websites.attach_client(rec["id"], client, actor=actor)
            if out.get("ok"):
                _wrote(report, "knack",
                       f"Client written onto website record {rec['id']}."
                       + (f" Refused: {'; '.join(out['rejected'])}."
                          if out.get("rejected") else ""))
            else:
                _skipped(report, "knack",
                         out.get("error", "the write did not go through")
                         + (f" ({'; '.join(out['rejected'])})"
                            if out.get("rejected") else ""))
    except Exception as exc:                            # noqa: BLE001
        _skipped(report, "knack", f"{type(exc).__name__}: {exc}"[:160])

    report["ok"] = bool(report["written"])
    report["note"] = _note(report)
    if report["ok"]:
        # This domain has an owner now, so it is off the orphan list, off the
        # "clients with no website" list and matched on the Sites page. All
        # three are held for the day; left cached, the row somebody has just
        # closed is still there on the next open and the button reads as
        # having done nothing.
        try:
            from hub import report_cache
            report_cache.invalidate("orphan-urls", "client-urls", "sites-match")
        except Exception:                               # noqa: BLE001
            pass
    try:
        from hub import audit
        audit.log("hub", "domain_attached", actor=actor or None, client=client,
                  detail=dom, wrote=len(report["written"]),
                  skipped=len(report["skipped"]))
    except Exception:                                   # noqa: BLE001
        pass
    return report


def _note(report: dict) -> str:
    wrote = len(report["written"])
    if not wrote:
        return ("Nothing was written. Every system either had no record for "
                "this domain or refused the write — see below.")
    note = (f"{report['domain']} is now attached to {report['client']} in "
            f"{wrote} of {len(SYSTEMS)} systems.")
    if report["skipped"]:
        note += (" The rest are listed with the reason: a system that could "
                 "not be written to must not read as one that was.")
    return note


def _write_sites(domain: str, client: str, force: bool = False) -> dict:
    """internal_client_name on every Simvoly project serving this domain."""
    out = {"written": [], "skipped": []}
    try:
        from hub.sites_match import _site_rows, is_active     # noqa: SLF001
        from modules.sites_admin import db as sdb
    except Exception as exc:                            # noqa: BLE001
        out["skipped"].append(f"Sites Admin is unavailable ({type(exc).__name__}).")
        return out

    hits = [r for r in _site_rows()
            if canonical_domain(r.get("domain") or r.get("url") or "") == domain]
    if not hits:
        out["skipped"].append(
            f"No Simvoly project serves {domain}, so there is no project to "
            "put the client name on.")
        return out
    for r in hits:
        pid = str(r.get("project_id") or r.get("id") or "")
        current = str(r.get("internal_client_name") or "").strip()
        if current and not force:
            if current.strip().lower() == client.strip().lower():
                out["written"].append(f"Project {pid} already named {client}.")
            else:
                out["skipped"].append(
                    f"Project {pid} is already linked to “{current}”. Nothing "
                    "was overwritten — a wrong internal_client_name "
                    "attributes revenue to the wrong client.")
            continue
        if not is_active(r):
            out["skipped"].append(
                f"Project {pid} is not live ({r.get('status') or 'no status'}), "
                "so its domain may no longer be this client's.")
            continue
        try:
            sdb.save_meta(pid, internal_client_name=client)
            out["written"].append(f"Project {pid} linked to {client}.")
        except Exception as exc:                        # noqa: BLE001
            out["skipped"].append(f"Project {pid}: {type(exc).__name__}")
    return out


def attach_many(links, *, actor: str = "", force: bool = False) -> dict:
    """Attach a list of {domain, client} at once, each with its own outcome."""
    done, failed = [], []
    for item in links or []:
        rep = attach(str((item or {}).get("domain") or ""),
                     str((item or {}).get("client") or ""),
                     actor=actor, url=str((item or {}).get("url") or ""),
                     force=force)
        (done if rep.get("ok") else failed).append(rep)
    return {"ok": bool(done), "attached": len(done), "items": done,
            "failed": failed,
            "note": (f"{len(done)} domain(s) attached."
                     + (f" {len(failed)} could not be." if failed else ""))}


# ---------------------------------------------------------------------------
# Orphans
# ---------------------------------------------------------------------------
def _known_domains() -> set:
    """Domains that already belong to a client, so are not orphans."""
    known = set()
    try:
        from hub import clients_registry
        for c in clients_registry.all_clients():
            d = canonical_domain(c.get("url") or c.get("domain") or "")
            if d:
                known.add(d)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from hub import client_urls
        for row in client_urls.overlay().values():
            for site in client_urls.sites_of(row):
                if site.get("domain"):
                    known.add(site["domain"])
    except Exception:                                   # noqa: BLE001
        pass
    return known


def _orphans_knack(add) -> dict:
    """Website records carrying a domain and nobody's name.

    Deliberately not `knack_websites.orphan_rows()`, which filters the file
    hosts out itself: everything a source offers goes through `add()` so the
    rejects are counted and named on the page. A sighting dropped in silence
    reads as a source that had nothing, and those are different answers.
    """
    from hub import knack_websites
    rows = knack_websites.rows()
    for r in rows:
        if r.get("domain") and not r.get("has_client"):
            add(r["domain"], "knack", f"website record {r['id']}",
                record_id=r["id"], url=r.get("production_url", ""))
    return {"rows": len(rows), "note": "object_153"}


def _orphans_sites(add) -> dict:
    from hub.sites_match import _is_platform, _site_rows, is_active  # noqa: SLF001
    rows = _site_rows()
    live = 0
    for r in rows:
        if not is_active(r):
            continue
        live += 1
        if str(r.get("internal_client_name") or "").strip():
            continue
        dom = canonical_domain(r.get("domain") or r.get("url") or "")
        if not dom or _is_platform(dom):
            continue
        add(dom, "sites", f"project {r.get('project_id', '')}".strip(),
            record_id=str(r.get("project_id") or ""))
    return {"rows": len(rows), "used": live, "note": "live projects only"}


def _orphans_store(add, store_name: str, system: str, label: str) -> dict:
    """A module table read through client_key's reader, which handles a table
    that does not exist yet and a column a migration never added."""
    from hub.client_key import (_STORES, _read_store,             # noqa: SLF001
                                alias_index, resolve)
    store = next((s for s in _STORES if s["module"] == store_name), None)
    if store is None:
        return {"rows": 0, "error": f"{store_name} is not a known store."}
    rows, err, _ = _read_store(store, 4000)
    if err:
        return {"rows": 0, "error": err}
    # Built once. resolve() builds the alias index when it is not given one,
    # and doing that per row is a full re-read of every client per scan row.
    idx = alias_index()
    for row in rows:
        name = str(row.get(store["name_col"]) or "").strip()
        raw = ((row.get(store["url_col"]) if store["url_col"] else "")
               or (row.get(store["domain_col"]) if store["domain_col"] else "")
               or "")
        dom = canonical_domain(raw)
        if not dom:
            continue
        # A row that names a client we know is not an orphan — the URL has an
        # owner, it is simply filed under a name rather than a domain.
        if name and resolve(name=name, url=dom, index=idx).get("known"):
            continue
        add(dom, system, f"{label} {row.get(store['id_col']) or ''}".strip()
            + (f" — typed as “{name}”" if name else ""),
            record_id=str(row.get(store["id_col"]) or ""))
    return {"rows": len(rows)}


_ORPHAN_READERS = (
    ("knack", "Knack website registry (object_153)", _orphans_knack),
    ("sites", "Smart 1 Sites projects",
     _orphans_sites),
    ("scan", "Site scans",
     lambda add: _orphans_store(add, "scans", "scan", "scan")),
    ("google_access", "Google access requests",
     lambda add: _orphans_store(add, "google_access", "google_access",
                                "request")),
)

ORPHAN_LABELS = {
    "knack": "Knack website registry", "sites": "Smart 1 Sites project",
    "scan": "Site scan", "google_access": "Google access request",
}


def orphans(q: str = "", limit: int = 400) -> dict:
    """Every URL in the Hub with no client attached to it.

    Grouped by canonical domain, so one domain seen in three systems is one row
    with three pieces of evidence rather than three rows a person has to
    reconcile by eye.

    The reading of the four systems is cached for the day (`orphan-urls`); the
    search box and the page cut run over that on every request. Splitting it
    that way is the point — `q=acme` and `q=acm` are two cache files and a
    search box types one per keystroke, so what is stored is the answer to the
    question the page asks on open, and typing filters it.
    """
    book = _orphan_book()
    rows = list(book["domains"])
    needle = str(q or "").strip().lower()
    if needle:
        rows = [r for r in rows if needle in r["domain"].lower()
                or any(needle in s["detail"].lower() for s in r["sightings"])]
    total = len(rows)
    return {**book, "q": q, "count": total, "shown": min(total, limit),
            "domains": rows[:limit]}


def _orphan_book() -> dict:
    """The four systems read, deduped and sorted — today's copy or a new one."""
    try:
        from hub import report_cache
    except Exception:                                   # noqa: BLE001
        return _read_orphans()
    return report_cache.serve("orphan-urls", _read_orphans)


def _read_orphans() -> dict:
    found: dict[str, dict] = {}
    rejected: dict[str, int] = {}
    known = _known_domains()

    def add(domain: str, system: str, detail: str, record_id: str = "",
            url: str = "") -> None:
        d = canonical_domain(domain)
        if not d or d in known:
            return
        if not looks_like_a_website(d):
            rejected[d] = rejected.get(d, 0) + 1
            return
        row = found.setdefault(d, {"domain": d, "url": url or ("https://" + d),
                                   "sources": [], "sightings": []})
        if system not in row["sources"]:
            row["sources"].append(system)
        if len(row["sightings"]) < 8:
            row["sightings"].append({"system": system,
                                     "label": ORPHAN_LABELS.get(system, system),
                                     "detail": detail, "record_id": record_id})

    status = []
    for key, label, reader in _ORPHAN_READERS:
        try:
            info = reader(add) or {}
            status.append({"source": key, "label": label,
                           "ok": not info.get("error"), **info})
        except Exception as exc:                        # noqa: BLE001
            status.append({"source": key, "label": label, "ok": False,
                           "error": f"{type(exc).__name__}: {exc}"[:200]})

    rows = list(found.values())
    # Most-corroborated first: a domain three systems know about is the one
    # most worth someone's attention. Sorted here rather than after the
    # search, so the order is the same whatever is typed into the box.
    rows.sort(key=lambda r: (-len(r["sources"]), r["domain"]))

    unreadable = [s for s in status if not s.get("ok")]
    note = ("Every URL the Hub holds that no client is attached to. Attaching "
            "one writes it to the client registry, the client's 360 record, "
            "the Simvoly project and the Knack website record — each reported "
            "separately.")
    if unreadable:
        note += (" " + ", ".join(s["label"] for s in unreadable) +
                 " could not be read, so this is a floor, not a total.")
    if rejected:
        note += (f" {sum(rejected.values())} sighting(s) pointed at a file "
                 "host, a shortener or a social profile rather than a website "
                 "and were left out.")

    return {
        "domains": rows,
        # A read where every one of the four systems refused is not "no
        # orphans" — it is no reading at all, and report_cache must not store
        # it as the day's answer.
        "measured": bool(status) and len(unreadable) < len(status),
        "sources": status, "sources_unreadable": len(unreadable),
        "rejected_domains": [{"domain": d, "count": n}
                             for d, n in sorted(rejected.items(),
                                                key=lambda kv: -kv[1])[:12]],
        "note": note,
    }
