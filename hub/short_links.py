"""A client-facing link, masked under our own domain, with every open counted.

A proposal, a preview or a review link built anywhere in this Hub carries
`smart1.agency` (or worse, an `onrender.com` fallback) in the address bar —
correct, and not what we want in front of a client. Short.io holds four
domains for exactly this: `s1report.co` is the one client-facing links are
masked under by default; `s1dev.co`, `s1leads.co` and `s1snap.co` are the
others this account controls and nothing here invents a fifth. `mask()` is
the one function that reaches Short.io, so a slug, a masking rule or a domain
that changes lands in one place rather than in every caller that built a link.

**The slug is `client/month/year/project`, one segment at a time.** The
client segment goes through `hub.client_key.name_slug()` -- the one
slugifier every other client join in this Hub already keys on -- so a masked
link for a client agrees with every other place that client's slug appears.
`project` has no such precedent: it is not a business name, and running it
through a slugifier built to drop legal suffixes and filler words out of a
company name would mangle an ordinary project title. It gets its own,
smaller one.

**Masking is requested and never assumed to have worked.** Short.io's
`cloaking` keeps the address bar on our domain by serving the destination in
an iframe, which only renders where the destination allows itself to be
framed. Every page this Hub serves carries no `X-Frame-Options` and no CSP
`frame-ancestors` by default (`hub/suite_embed.py`'s own note), so masking a
Hub-hosted proposal or preview works; masking a page on somebody else's site
is the caller's to judge, which is why `mask()` never refuses on the strength
of what it cannot know and the docstring says so rather than promising more
than "when possible" actually covers.

**Nothing is silently overwritten.** A slug already pointing at a different
destination is never quietly repointed -- the rule `hub/client_key.py` and
`hub/domain_links.py` both hold at length: reassigning what a URL means out
from under whoever already has it in an email is worse than refusing. A
numbered suffix is tried instead, because two previews for one client inside
one month is the ordinary case here, not a mistake to stop somebody making.

**The registry is the local half of the truth, not a cache of it.** Short.io
is the only place a click is actually counted; what is stored here
(`jsonstore`-backed, one file, read-modify-write through `update_json()` so
two requests creating a link at once cannot drop one of them) is which link
was made for which destination, so a caller can ask "is there one already"
without spending an API call, and so a click count can be shown without
reaching Short.io on every page load. `clicks()` and `refresh_all()` are the
only functions that touch the network for a count, and `refresh_all()` is
meant to sit behind a button -- the `hub/domain_purchase.py` rule for
anything that reaches a provider: a page nobody asked to refresh must not be
what pays for the request.

**No key is `not_configured`, never a silent fallback to a raw URL that
nobody chose to send instead.** `mask()` says so in its answer
(`{"ok": False, "reason": "not_configured", ...}`) so a caller can decide
what "not masked" means for that screen, rather than this module deciding
quietly on everybody's behalf.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from hub import jsonstore
from hub.client_key import name_slug
from hub.config import settings

API_BASE = "https://api.short.io"
STATS_BASE = "https://api-v2.short.io"
TIMEOUT = 12

# Every domain this Short.io account holds. `mask()` refuses a domain outside
# this list rather than sending it to Short.io and letting the provider be
# the one to say no -- a masked link on a domain nobody here recognises is a
# domain nobody remembers to point DNS at.
DOMAINS = ("s1dev.co", "s1leads.co", "s1report.co", "s1snap.co")
CLIENT_FACING_DOMAIN = "s1report.co"

# A collision on the exact slug gets a numbered suffix rather than being
# refused outright -- bounded, because a slug that is still taken after eight
# tries is not going to resolve itself on a ninth.
_MAX_SUFFIX = 8


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def configured() -> bool:
    return bool(settings.short_io_key)


def why_not() -> str:
    if not settings.short_io_key:
        return ("SHORT_IO_API_KEY is not set, so client-facing links are sent "
                "as the plain Hub URL rather than masked.")
    return ""


def _headers() -> dict:
    return {
        "Authorization": settings.short_io_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _clean(value) -> str:
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# The slug
# ---------------------------------------------------------------------------

def _path_slug(value: str, limit: int = 60) -> str:
    """A URL-path-safe slug for a segment that is not a client name.

    The same shape as the small `_slug()` every module here keeps for its own
    strings (`hub/qr_codes.py`, `hub/ad_copy.py`, ...): lowercase, alnum runs
    joined by a single hyphen, no leading or trailing one.
    """
    out: list[str] = []
    for ch in _clean(value).lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:limit]


def build_path(client: str, project: str, *, when: datetime | None = None) -> str:
    """`client/month/year/project`, each segment slugified on its own.

    `when` defaults to now -- the date the link is made, which for a preview
    or a proposal is the date it is being sent, and is the only date this
    module has any business knowing.
    """
    when = when or datetime.now(timezone.utc)
    client_part = name_slug(client) or "client"
    project_part = _path_slug(project) or "project"
    return f"{client_part}/{when.strftime('%b').lower()}/{when.strftime('%Y')}/{project_part}"


# ---------------------------------------------------------------------------
# The local registry -- one file, read-modify-write through jsonstore so two
# requests minting a link at once cannot drop one of them (the
# hub/jsonstore.py `update_json` rule; this store keeps every record in one
# file precisely because it is small and read as a whole on every list view).
# ---------------------------------------------------------------------------

def _registry_path() -> str:
    return os.path.join(jsonstore.data_dir("short_links"), "links.json")


def _load() -> list[dict]:
    return jsonstore.read_json(_registry_path(), default=[]) or []


def all_links(client: str = "") -> list[dict]:
    """Every masked link, newest first -- optionally narrowed to one client."""
    rows = _load()
    if client:
        key = name_slug(client)
        rows = [r for r in rows if name_slug(r.get("client", "")) == key]
    return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)


def by_path(path: str) -> dict | None:
    path = _clean(path)
    for row in _load():
        if row.get("path") == path:
            return row
    return None


def for_url(original_url: str) -> dict | None:
    """The masked record already pointing at this exact destination, if any.

    A read-only local lookup -- no network -- so a caller that already knows
    the destination (a proposal's own share URL, say) can show the masked
    link on every page load without `mask()`'s own network round trip, which
    only fires the first time a destination is masked.
    """
    url = _clean(original_url)
    if not url:
        return None
    for row in _load():
        if row.get("original_url") == url:
            return row
    return None


def _save_record(record: dict) -> None:
    def _mutate(rows):
        rows = [r for r in (rows or []) if r.get("path") != record["path"]]
        rows.append(record)
        return rows
    jsonstore.update_json(_registry_path(), _mutate, default=[])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

def _fail(reason: str, detail: str, *, original_url: str = "") -> dict:
    return {"ok": False, "reason": reason, "error": detail,
            "original_url": original_url, "short_url": "", "path": "",
            "domain": "", "id": ""}


def _error_detail(resp) -> str:
    try:
        body = resp.json()
        msg = body.get("error") or body.get("message")
        if msg:
            return str(msg)
    except ValueError:
        pass
    return f"Short.io answered {resp.status_code}."


def mask(url: str, *, client: str, project: str, when: datetime | None = None,
         domain: str = "", title: str = "", actor: str = "") -> dict:
    """Mask `url` under our own domain, reusing an existing link if there is one.

    Never raises. Every failure comes back as `{"ok": False, "reason": ...,
    "error": "..."}` so a caller can fall back to the raw URL rather than
    losing the share entirely because Short.io is slow or unreachable --
    the same tri-state shape `hub/qr_codes.py`'s `attribution()` and
    `hub/domain_links.py`'s writers use throughout this codebase.
    """
    url = _clean(url)
    client = _clean(client)
    project = _clean(project)
    domain = _clean(domain) or settings.short_io_domain or CLIENT_FACING_DOMAIN
    title = _clean(title)

    if not configured():
        return _fail("not_configured", why_not(), original_url=url)
    if not url.startswith(("http://", "https://")):
        return _fail("bad_url", "Give a full https:// URL to mask.", original_url=url)
    if not client:
        return _fail("no_client", "A client name is needed to build the slug.",
                     original_url=url)
    if not project:
        return _fail("no_project", "A project name is needed to build the slug.",
                     original_url=url)
    if domain not in DOMAINS:
        return _fail("bad_domain",
                     f"{domain!r} is not one of this account's domains: "
                     f"{', '.join(DOMAINS)}.", original_url=url)

    path = build_path(client, project, when=when)

    # Reuse an existing link for this exact destination. A no-op read of the
    # local registry, so calling mask() again for a link that already exists
    # -- which every re-open of a share panel does -- costs nothing.
    existing = by_path(path)
    if existing and existing.get("original_url") == url and existing.get("domain") == domain:
        return {"ok": True, "reused": True, **existing}

    # A slug already pointing somewhere else is never silently repointed.
    # Suffixed rather than refused outright -- two previews for one client in
    # one month is the ordinary case here.
    attempt_path = path
    for suffix in range(1, _MAX_SUFFIX + 2):
        taken = by_path(attempt_path)
        if not taken or taken.get("original_url") == url:
            break
        attempt_path = f"{path}-{suffix + 1}"
    else:
        return _fail("path_taken",
                     f"{path} and its numbered variants are all already in "
                     f"use for a different link.", original_url=url)
    path = attempt_path

    payload = {
        "domain": domain,
        "originalURL": url,
        "path": path,
        "allowDuplicates": False,
        # Keeps the address bar on our domain -- "masked when possible": it
        # depends on the destination allowing itself to be framed, which
        # every page this Hub serves does by default (hub/suite_embed.py).
        "cloaking": True,
    }
    if title:
        payload["title"] = title[:200]

    try:
        resp = requests.post(f"{API_BASE}/links", json=payload,
                             headers=_headers(), timeout=TIMEOUT)
    except requests.RequestException as exc:
        return _fail("unreachable", f"Short.io could not be reached: {exc}",
                     original_url=url)

    if resp.status_code == 409:
        return _fail("conflict",
                     f"Short.io already has a different link at {domain}/{path}.",
                     original_url=url)
    if resp.status_code not in (200, 201):
        return _fail("refused", _error_detail(resp), original_url=url)

    try:
        data = resp.json()
    except ValueError:
        return _fail("bad_response",
                     "Short.io answered with something that was not JSON.",
                     original_url=url)

    short_url = data.get("secureShortURL") or data.get("shortURL") or f"https://{domain}/{path}"
    link_id = _clean(data.get("idString") or data.get("id"))
    record = {
        "id": link_id,
        "path": _clean(data.get("path")) or path,
        "domain": domain,
        "short_url": short_url,
        "original_url": url,
        "client": client,
        "project": project,
        "title": title,
        "created_at": _now_iso(),
        "created_by": actor,
        "clicks_total": None,
        "clicks_human": None,
        "clicks_checked_at": "",
        "clicks_measured": False,
        "clicks_error": "",
    }
    _save_record(record)

    # Filed under "hub" -- the module this Hub's own shared services already
    # log against (hub/domain_links.py does the same) -- and declared in
    # hub/client_brand.NOT_WORK is not needed for it, because a masked link
    # is a join between an existing document and an address, not a
    # deliverable of its own.
    try:
        from hub import audit
        audit.log("hub", "short_link_created", actor=actor or None, client=client,
                  project=project, domain=domain, path=record["path"],
                  short_url=short_url)
    except Exception:                                   # noqa: BLE001
        pass

    return {"ok": True, "reused": False, **record}


# ---------------------------------------------------------------------------
# Tracking -- how many times it was opened
# ---------------------------------------------------------------------------

def clicks(link_id: str) -> dict:
    """How many times this link has been opened, or why that is not known.

    Tri-state, never a bare number: `{"measured": True, "total": n, "human":
    n}` or `{"measured": False, "reason": "..."}`. Reading a failed call as
    zero opens is the confident-wrong-answer shape this codebase refuses
    throughout -- a client who opened a masked preview twice must not read as
    a client who never opened it because Short.io was slow to answer.
    """
    link_id = _clean(link_id)
    if not link_id:
        return {"measured": False, "reason": "No link id."}
    if not configured():
        return {"measured": False, "reason": why_not()}
    try:
        resp = requests.get(f"{STATS_BASE}/statistics/link/{link_id}",
                            params={"period": "total"}, headers=_headers(),
                            timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"measured": False, "reason": f"Short.io could not be reached: {exc}"}
    if resp.status_code == 401:
        return {"measured": False, "reason": "Short.io refused the API key."}
    if resp.status_code != 200:
        return {"measured": False, "reason": _error_detail(resp)}
    try:
        data = resp.json()
    except ValueError:
        return {"measured": False,
                "reason": "Short.io answered with something that was not JSON."}
    total = data.get("totalClicks")
    if total is None:
        return {"measured": False,
                "reason": "Short.io's answer did not carry a click count."}
    human = data.get("humanClicks")
    try:
        total = int(total)
    except (TypeError, ValueError):
        return {"measured": False,
                "reason": "Short.io's click count was not a number."}
    try:
        human = int(human) if human is not None else None
    except (TypeError, ValueError):
        human = None
    return {"measured": True, "total": total, "human": human}


def refresh_all(limit: int = 40) -> dict:
    """Re-read click counts for the most recently created masked links.

    Behind a button, never on a page load -- the `hub/domain_purchase.py`
    rule for anything that reaches a provider on request. The network calls
    happen before the registry is touched, and the write is a single
    `update_json()` merge, so a slow or half-failed refresh cannot hold the
    registry's file lock open across several outbound requests -- the reason
    `hub/client_brand.py`'s `apply_proposals()` does its own outside calls
    before it ever takes that lock, one document over.

    Bounded, because refreshing hundreds of links one HTTP call apiece on one
    press is the same shape `hub/scheduler.py`'s wall-clock budgets exist to
    avoid.
    """
    rows = sorted(_load(), key=lambda r: r.get("created_at", ""), reverse=True)
    targets = rows[:max(1, int(limit))]
    results = {row.get("path"): clicks(row.get("id", "")) for row in targets}

    def _mutate(current):
        current = list(current or [])
        for row in current:
            stat = results.get(row.get("path"))
            if stat is None:
                continue
            row["clicks_checked_at"] = _now_iso()
            row["clicks_measured"] = bool(stat.get("measured"))
            if stat.get("measured"):
                row["clicks_total"] = stat.get("total")
                row["clicks_human"] = stat.get("human")
                row["clicks_error"] = ""
            else:
                row["clicks_error"] = stat.get("reason", "")
        return current

    jsonstore.update_json(_registry_path(), _mutate, default=[])
    checked = sum(1 for s in results.values() if s.get("measured"))
    return {"checked": checked, "failed": len(results) - checked, "of": len(targets)}
