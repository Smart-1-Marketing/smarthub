"""Match Smart 1 Sites projects to Hub clients — by domain, then by name.

`project_meta.internal_client_name` is what links a Simvoly project to a client
in Knack, and it is filled in by hand. With over a thousand projects that means
it is mostly empty, so the Sites margin report has no revenue to compare cost
against, and no cross-tool report can see a client's website.

Nothing was doing the obvious thing: `websites.domain` is already on every
project, and the client registry already has a URL. A domain is an exact
identifier where a name is not — "Riverside HVAC" and "Riverside HVAC LLC" are
different strings for one company, but riverside-hvac.com is one company.

This proposes matches; it does not apply them silently. A wrong
`internal_client_name` is worse than a blank one — it attributes revenue to the
wrong client and the billing audits then disagree with each other for reasons
nobody can trace. So `suggest()` is read-only and `apply()` takes an explicit
list of the matches a human accepted.

## The name pass, and why it needed one

Domain is the join wherever there is one. What was left over was most of the
book: on this deployment's own portfolio export, 1,021 projects produced 450
with no domain we hold a record of, and the fallback — an exact normalised
match on the project name — found almost none of them, because **a Simvoly
project name is not the business's name**. 548 of the 1,021 begin with a media
partner ("TMRG - JWS Pottery", "FabLocal -  SERVPRO of Fresno NW"), 249 are
placeholders that identify nobody ("Anna's Website", "S1M Test"), and a good
number carry a trailing marker describing the job rather than the client
("Helena Valley Addiction Services - 2026 Refresh").

`hub/site_names.py` reads those three shapes, and the same export then matched
**304 projects exactly and offered a candidate for 61 more**, with nothing
ambiguous. Two rules it brings with it, both the ones this codebase keeps
having to re-learn:

* A project whose name is a placeholder is **named as one**, never matched
  loosely. A fuzzy pass over "Anna's Website" eventually finds an Anna and
  attaches a stranger's site to her.
* A candidate name matching **two** clients proposes neither. Attributing one
  company's website to another is the worst thing this tool can do, and the
  ambiguity is shown rather than resolved.

The book it matches against is the Hub client registry *and* the Knack website
registry (object_153) together, because most of the clients still unmatched
here are the ones the Hub's own registry holds no URL for.
"""
from __future__ import annotations

from hub import site_names
from hub.client_context import canonical_domain

# Simvoly's own hosting domains. A project still parked on one of these has no
# real domain yet, so matching on it would join every unlaunched site together.
PLATFORM_DOMAINS = {
    "simvoly.com", "simvolysite.com", "smart1sites.com", "mysimvoly.com",
}


def _is_platform(domain: str) -> bool:
    d = (domain or "").lower()
    return any(d == p or d.endswith("." + p) for p in PLATFORM_DOMAINS)


# ---------------------------------------------------------------------------
# Which sites count
# ---------------------------------------------------------------------------
# Simvoly gives a project one of three statuses, and Sites Admin keeps its own
# lifecycle beside it for the two things Simvoly cannot express — a site we
# suspended and one the client cancelled, both of which are EXPIRED upstream.
#
# Only ACTIVE is a live website. An EXPIRED project's domain has usually been
# repointed, parked or picked up by somebody else, so matching a client to it
# attributes revenue to a site that is not serving anything — and a TRIAL is a
# site that may never launch. Both used to be matched exactly like a live one,
# which is how a cancelled client's domain could end up linked to whoever owns
# that domain now.
#
# The skipped ones are counted and reported rather than dropped: "we checked
# 1,200 projects" and "we checked the 380 that are live" are different claims
# and the page has to be able to say which one it is making.
LIVE_STATUSES = {"ACTIVE"}
DEAD_LIFECYCLES = {"CANCELLED", "SUSPENDED"}


def is_active(row: dict) -> bool:
    """Is this project a live website right now?"""
    status = str((row or {}).get("status") or "").strip().upper()
    lifecycle = str((row or {}).get("lifecycle_state") or "").strip().upper()
    if lifecycle in DEAD_LIFECYCLES:
        return False
    return status in LIVE_STATUSES


def inactive_reason(row: dict) -> str:
    """Why a project was skipped, in words. Never "unknown" without saying so."""
    status = str((row or {}).get("status") or "").strip().upper()
    lifecycle = str((row or {}).get("lifecycle_state") or "").strip().upper()
    if lifecycle in DEAD_LIFECYCLES:
        return lifecycle.title()
    if not status:
        return "No status recorded"
    return status.title()


def _norm_name(name: str) -> str:
    """Normalise a business name for a fallback comparison.

    Was a local regex; now the shared one in hub/client_key — by way of
    `hub/site_names.py`, which is where the name pass lives. The local version
    ran the words together — "ab cd" and "abcd" normalised alike — and dropped
    a different set of suffixes than the billing audit's copy did, so the two
    reports could disagree about whether two names were the same company.
    Kept as the one place this module says what a normalised name is.
    """
    from hub.client_key import normalise_name
    return normalise_name(name)


def _hub_clients() -> list[dict]:
    try:
        from hub import clients_registry
        return clients_registry.all_clients()
    except Exception:                                   # noqa: BLE001
        return []


# Why the last read of the project list came back short, if it did. The read
# swallows its own exceptions and returns what it has, which is right for a
# page that can still show the rest and wrong for anything that stores the
# answer: an empty list from a Sites module that will not start reads exactly
# like an account with no projects in it. `hub/sites_billing.py` already
# carries a note about this swallow. Kept per process, set on every read.
_SITES = {"error": ""}


def sites_error() -> str:
    """The reason the last `_site_rows()` came back short, or ""."""
    return _SITES["error"]


def _site_rows() -> list[dict]:
    """Every Simvoly project with its domain and current client link."""
    try:
        from modules.sites_admin import db as sdb
    except Exception as exc:                            # noqa: BLE001
        _SITES["error"] = (f"The Sites module could not be loaded "
                           f"({type(exc).__name__}), so no Simvoly project "
                           f"was read.")
        return []
    # query_projects is paginated and returns (rows, total). Page through it
    # rather than asking for one huge page — the count is over a thousand.
    out, page, per_page = [], 1, 200
    try:
        while True:
            rows, total = sdb.query_projects(page=page, per_page=per_page)
            out.extend(r for r in rows if isinstance(r, dict))
            if len(out) >= total or not rows or page > 30:
                break
            page += 1
    except Exception as exc:                            # noqa: BLE001
        _SITES["error"] = (f"{type(exc).__name__} while paging the Simvoly "
                           f"projects, so this is a floor, not a total.")
        return out
    _SITES["error"] = ""
    return out


def suggest(limit: int = 2000, active_only: bool = True, *,
            cached: bool = True) -> dict:
    """Propose a client for every unmatched *live* site. Changes nothing.

    `active_only` defaults to True: an expired or cancelled project is not a
    website anybody can visit, and linking a client to one puts their name on a
    domain that may now belong to someone else. Pass False to see everything —
    the count of what that adds is in `skipped_inactive` either way, so the
    page can offer it rather than hiding it.

    The whole pass — the Simvoly portfolio, object_153, the name index and a
    ratio per unmatched project — is held for the day and re-run on Refresh.
    `apply()` and `domain_links.attach()` drop it, so a project somebody has
    just matched is gone from the list rather than being proposed again.
    """
    if cached:
        from hub import report_cache
        return report_cache.serve(
            "sites-match", lambda: suggest(limit, active_only, cached=False),
            params=f"live={int(bool(active_only))}")
    clients = _hub_clients()
    by_domain, name_pairs = {}, []
    for c in clients:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        d = canonical_domain(c.get("url") or c.get("domain") or "")
        if d and not _is_platform(d):
            by_domain.setdefault(d, name)
        name_pairs.append((name, "the Hub client registry"))

    # object_153, read once. Each entry is {domain: {client, record_id, why}}
    # and a record carrying a domain but nobody's name is deliberately absent:
    # that is an orphan for the orphan list, not a match.
    knack_by_domain: dict[str, dict] = {}
    knack_error = ""
    try:
        from hub import knack_websites
        for r in knack_websites.rows():
            if r["domain"] and r["has_client"] and not _is_platform(r["domain"]):
                knack_by_domain.setdefault(r["domain"], {
                    "client": r["client"], "record_id": r["id"],
                    "why": "The Knack website registry (object_153) records "
                           f"this domain against “{r['client']}”."})
            # A registry record with no domain is no use to the domain pass
            # and is exactly what the name pass is for: it is a client whose
            # website we have never joined to anything.
            for nm in (r.get("client_name"), r.get("organization")):
                if nm:
                    name_pairs.append((nm, "the Knack website registry"))
    except Exception as exc:                            # noqa: BLE001
        knack_error = f"{type(exc).__name__}: {exc}"[:160]

    # One book of names, from both registries, built once. A per-project
    # rebuild over a thousand projects is what made the old matcher slow.
    name_book = site_names.index_names(name_pairs)

    all_rows = _site_rows()[:limit]
    if active_only:
        rows = [r for r in all_rows if is_active(r)]
    else:
        rows = all_rows
    skipped = [r for r in all_rows if r not in rows] if active_only else []
    skipped_by_reason: dict[str, int] = {}
    for r in skipped:
        reason = inactive_reason(r)
        skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

    matched, already, no_domain, unmatched = [], [], [], []
    named_nobody = 0

    def _name_maybes(site: str, dom: str = "") -> list[dict]:
        """Candidate clients for one project, by name — merged, best first.

        Two readers of the same book. `site_names.near_matches()` works from
        the candidate names a project title can be about, which is what finds
        a business behind a media-partner prefix; `knack_websites.suggest_for`
        adds the evidence a name cannot carry — a domain whose stem is the
        same as the registry's. Merged on the client name so one client is
        offered once, keeping whichever reading scored higher.
        """
        best: dict[str, dict] = {}
        for m in site_names.near_matches(site, name_book):
            best[m["client"]] = {"client": m["client"], "score": m["score"],
                                 "why": m["why"], "knack_domain": "",
                                 "platform": "", "hm_fee": 0}
        # An exact hit on a candidate name outranks every resemblance, and it
        # is written last so it wins the merge.
        for m in site_names.exact_matches(site, name_book):
            best[m["client"]] = {"client": m["client"], "score": 1.0,
                                 "why": m["why"], "knack_domain": "",
                                 "platform": "", "hm_fee": 0}
        try:
            from hub import knack_websites
            # The cleaned name, not the raw project title: comparing
            # "FabLocal -  SERVPRO of Southwest San Antonio" against the
            # registry scores the wrong SERVPRO franchise above the right one,
            # because half of what is being compared is the media partner.
            for cand in knack_websites.suggest_for(
                    site_names.best_name(site) or site, dom):
                nm = cand.get("client_name") or cand.get("organization")
                if not nm:
                    continue
                prev = best.get(nm)
                if prev and prev["score"] >= cand["score"]:
                    continue
                best[nm] = {
                    "client": nm, "score": cand["score"], "why": cand["why"],
                    "knack_domain": cand.get("domain", ""),
                    "platform": cand.get("platform", ""),
                    "hm_fee": cand.get("hm_fee", 0)}
        except Exception:                               # noqa: BLE001
            pass
        return sorted(best.values(), key=lambda c: -c["score"])[:8]

    for r in rows:
        pid = str(r.get("project_id") or r.get("id") or "")
        site = (r.get("name") or r.get("site") or "").strip()
        current = (r.get("internal_client_name") or "").strip()
        raw = r.get("domain") or r.get("url") or r.get("subdomain") or ""
        dom = canonical_domain(raw)
        # Why this project's name is about nobody, if it is. Checked once per
        # project and carried into whichever bucket the project lands in: a
        # placeholder is the reason a row has no candidates, and a row that
        # simply says "no candidate" reads as a failure of the matcher.
        placeholder = site_names.is_placeholder(site)
        if placeholder:
            named_nobody += 1

        if current:
            already.append({"project_id": pid, "site": site,
                            "client": current, "domain": dom})
            continue
        if not dom or _is_platform(dom):
            # No domain to match on — which is precisely when the name is all
            # there is. These used to be listed and offered nothing at all.
            no_domain.append({"project_id": pid, "site": site,
                              "domain": raw or "",
                              "placeholder": placeholder,
                              "maybe": [] if placeholder
                              else _name_maybes(site),
                              "why": "Still on a platform domain — no real "
                                     "website to match on yet."
                                     if _is_platform(dom) else
                                     "No domain recorded."})
            continue

        hit = by_domain.get(dom)
        if hit:
            matched.append({"project_id": pid, "site": site, "domain": dom,
                            "client": hit, "confidence": "domain",
                            "why": "Exact domain match against the client "
                                   "registry."})
            continue
        # The Knack website registry, on the domain. object_153 pairs a URL
        # domain (field_3111) with the client organisation (field_2924) or the
        # client (field_3112), which is an exact identifier in the same way a
        # registry domain is — and it covers the clients the Hub's own registry
        # has no URL for, which is most of the ones still unmatched here.
        reg = knack_by_domain.get(dom)
        if reg:
            matched.append({"project_id": pid, "site": site, "domain": dom,
                            "client": reg["client"], "confidence": "domain",
                            "source": "knack_registry",
                            "record_id": reg.get("record_id", ""),
                            "why": reg["why"]})
            continue
        # Fall back to the name, reported separately because it is a guess and
        # should be eyeballed before being applied. Not the raw project name:
        # "TMRG - JWS Pottery" is a media partner, a business and nothing that
        # normalises to a client, which is why `site_names` derives the
        # candidates first and each match says which one of them it hit.
        hits = [] if placeholder else site_names.exact_matches(site, name_book)
        if len(hits) == 1:
            matched.append({"project_id": pid, "site": site, "domain": dom,
                            "client": hits[0]["client"], "confidence": "name",
                            "matched_on": hits[0]["matched"],
                            "source": hits[0].get("source", ""),
                            "why": hits[0]["why"] + " Check before applying — "
                                   "a name is not an identifier."})
            continue
        if len(hits) > 1:
            # Two clients answer to this name. Picking one is the guess that
            # files one company's website under another, so both are offered
            # and neither is proposed.
            unmatched.append({
                "project_id": pid, "site": site, "domain": dom,
                "placeholder": "",
                "ambiguous": True,
                "why": f"{len(hits)} clients are filed under that name, so "
                       "this one is not being guessed at.",
                "maybe": [{"client": h["client"], "score": h["score"],
                           "why": h["why"], "knack_domain": "",
                           "platform": "", "hm_fee": 0} for h in hits]})
            continue
        # Nothing exact — offer the near misses so a human can confirm rather
        # than the site staying orphaned for ever.
        unmatched.append({"project_id": pid, "site": site, "domain": dom,
                          "placeholder": placeholder,
                          "why": ("This project name is " + placeholder
                                  + ", so it was not matched on."
                                  if placeholder else ""),
                          "maybe": [] if placeholder
                          else _name_maybes(site, dom)})

    by_conf = {"domain": 0, "name": 0}
    for m in matched:
        by_conf[m["confidence"]] += 1

    return {
        "checked": len(rows),
        "active_only": active_only,
        "skipped_inactive": len(skipped),
        "skipped_by_reason": skipped_by_reason,
        "already_linked": len(already),
        "suggested": matched,
        "suggested_count": len(matched),
        "by_confidence": by_conf,
        "no_domain": no_domain,
        "no_domain_count": len(no_domain),
        "no_domain_with_suggestions": sum(1 for u in no_domain
                                          if u.get("maybe")),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "with_suggestions": sum(1 for u in unmatched if u.get("maybe")),
        "ambiguous_count": sum(1 for u in unmatched if u.get("ambiguous")),
        # Counted rather than left to read as a matcher that found nothing:
        # a project called "Anna's Website" or "S1M Test" names no business,
        # and on this deployment's own export 249 of 1,021 projects are one.
        "named_nobody": named_nobody,
        "clients_with_domain": len(by_domain),
        "registry_domains": len(knack_by_domain),
        "registry_names": len(name_book),
        # Named rather than counted as zero: "Knack is down" and "Knack knows
        # none of these domains" must never look alike.
        "registry_error": knack_error,
        # The same distinction for the other side of the match. A Sites module
        # that will not start returns no projects, which is a complete-looking
        # "nothing to match" — and held for the day it would stand until
        # tomorrow. `measured: False` keeps it out of the cache entirely.
        "sites_error": sites_error(),
        "measured": not sites_error(),
        "note": "Nothing has been changed. A wrong internal_client_name is "
                "worse than a blank one — it attributes revenue to the wrong "
                "client and makes the billing audits disagree. Review these, "
                "then apply the ones you accept."
                + (f" Only live sites were checked; {len(skipped)} expired, "
                   f"trial, cancelled or suspended project(s) were left out."
                   if active_only and skipped else
                   " Every project was checked, including expired, trial and "
                   "cancelled ones — those domains may no longer be the "
                   "client's." if not active_only else ""),
    }


def apply(matches: list[dict], actor: str = "", force: bool = False) -> dict:
    """Write accepted matches. Takes an explicit list, never the whole set.

    A match is not one write. `internal_client_name` on the Simvoly project is
    what the margin report reads, but a rep who matched a site here and then
    opened Client 360 used to find it still saying "No website record matched"
    — the join was real and invisible everywhere except the tool that made it.
    So each accepted match goes through `hub/domain_links.attach()`, which
    writes the Hub's client registry, the client's 360 record, the project and
    the Knack website record, and reports each one separately.

    A match with no domain (a name-confidence one on a project whose domain we
    could not read) still writes the project, because that is the only system
    that can be keyed on a project id.
    """
    saved, failed, reports = [], [], []
    have_sites = True
    try:
        from modules.sites_admin import db as sdb
    except Exception as exc:                            # noqa: BLE001
        sdb, have_sites = None, False
        sites_error = f"Sites module unavailable ({type(exc).__name__})."

    for m in matches or []:
        pid = str(m.get("project_id") or "").strip()
        client = str(m.get("client") or "").strip()
        domain = canonical_domain(m.get("domain") or "")
        if not pid or not client:
            failed.append({"project_id": pid, "error": "missing id or client"})
            continue
        if domain:
            from hub.domain_links import attach
            rep = attach(domain, client, actor=actor, force=force)
            reports.append(rep)
            if rep.get("ok"):
                saved.append({"project_id": pid, "client": client,
                              "domain": domain})
            else:
                failed.append({"project_id": pid,
                               "error": rep.get("error")
                               or "nothing could be written — see the detail"})
            continue
        if not have_sites:
            failed.append({"project_id": pid, "error": sites_error})
            continue
        try:
            sdb.save_meta(pid, internal_client_name=client)
            saved.append({"project_id": pid, "client": client, "domain": ""})
        except Exception as exc:                        # noqa: BLE001
            failed.append({"project_id": pid, "error": type(exc).__name__})

    if saved:
        # A matched project has left this list. `attach()` drops these too,
        # but the no-domain branch above never reaches it — and that is
        # exactly the row a person came here to close.
        try:
            from hub import report_cache
            report_cache.invalidate("sites-match", "orphan-urls", "client-urls")
        except Exception:                               # noqa: BLE001
            pass
    try:
        from hub import audit
        audit.log("sites_admin", "clients_matched", actor=actor or None,
                  saved=len(saved), failed=len(failed))
    except Exception:                                   # noqa: BLE001
        pass
    partial = [r for r in reports if r.get("skipped")]
    return {"saved": len(saved), "failed": failed, "items": saved,
            "reports": reports,
            # Named rather than folded into the count: "linked" and "linked in
            # two of four systems" are different outcomes, and one tick for
            # both is how a rep learns not to trust the tick.
            "note": (f"{len(saved)} match(es) written."
                     + (f" {len(partial)} of them could not be written "
                        "everywhere — open the detail to see which system and "
                        "why." if partial else "")
                     + (f" {len(failed)} failed." if failed else ""))}
