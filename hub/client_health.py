"""
Smart 1 Hub — one client, everything outstanding on them, on one screen.

Client 360 answers *what do we know about this client*. It answers it in
nineteen cards, each reading its own source and each perfectly capable of
being empty for its own good reason — and nowhere on it does anything say
**what needs doing**. So the six things that actually stall a campaign were
spread across six screens: a clarification sitting on an insertion order in
Knack, a spot nobody has made, a proposal the client never opened, a price
about to lapse, an IO ending in three weeks, a dashboard that was never built.
Each has a report of its own, each report is a list of every client at once,
and none of them was the answer to "what is on my desk this morning".

`hub/client_owner.py` says whose desk. This says what is on it.

## What an issue is

A **finding plus what it costs**, never a task somebody typed. Everything
here is read: the Knack product rows, the creative audit, the proposal store,
the review rounds, the latest website audit. Nothing is entered, so nothing
can go stale by being forgotten — the list is what the systems say today.

That is also the constraint. This report can only be as honest as the sources
under it, so every one of them is asked separately and **a source that could
not be read is named rather than counted as nothing**. A client with a
QuickBooks-shaped hole in their row must not read as a client with nothing
outstanding: that is the confident wrong answer this codebase keeps having to
undo, and here it would send a rep away from the one client who needed them.

## Six rules

**Sorted by how much is outstanding, worst first.** Not by name, not by
billing. The question is which client needs an hour today, and a list sorted
alphabetically answers a different one.

**A count is never a link to a page that cannot show it.** Every issue carries
the screen it is fixed on, and that screen is the one that already exists —
the campaign-assets list, the proposal builder, the stale-creative audit. This
page finds the work; it does not become a fourteenth place to do it.

**Ignore and Done are recorded, reversible and never silent.** A row that
disappears cannot be told from a report that failed to load, so both go into
their own section under the client with who marked them and when, and one
press puts either back. `hub/creative_evergreen.py` gives the reason at
length.

**A Done mark is about the issue as it stood.** `fingerprint()` is a digest of
what the issue actually said, so an asset ask that changes retires the mark
and the row says it was **superseded** rather than vanishing or standing.
"Nobody has looked" and "somebody looked at a different ask" are different
situations and only the second has a name to go back to — the rule
`modules/commercial_builder/compliance_spec.py` arrived at for a sign-off.

**Marks and owners are applied on read, never baked into the cache.** The
report is held for the day and there are two gunicorn workers, so a mark
folded into a cached payload is a button that appears to do nothing to
whichever worker did not take it — `hub/creative_evergreen.py` had to undo
exactly that with a five-minute cache, and a day-long one puts a much longer
fuse on it.

**A note is somebody's sentence about a client, and it says whose.** Kept
here rather than posted anywhere: Client 360's own notes card is the client
record, and this is a working note about the chase. A note nobody can
attribute is one nobody can follow up.

Stored through `hub/jsonstore.py`. Nothing in this module raises out of a
route.
"""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import date, datetime, timezone

from flask import Blueprint, jsonify, render_template, request

from hub import jsonstore

bp = Blueprint("client_health", __name__)

# An insertion order ending inside this many days needs a renewal conversation
# now: the paperwork, the creative and the client's own approval all have to
# happen before the flight ends, and a report that raises it the week it
# expires has told somebody about a deadline they have already missed.
RENEWAL_DAYS = 45

# How stale a website reading may be before the traffic figures on this page
# stop being worth quoting. Read from `hub/website_audit.py` rather than
# restated, so the two screens cannot disagree about what "current" means.
try:                                                    # pragma: no cover
    from hub.website_audit import STALE_DAYS as AUDIT_STALE_DAYS
except Exception:                                       # noqa: BLE001
    AUDIT_STALE_DAYS = 60

MAX_NOTE = 1000
MAX_NOTES_PER_CLIENT = 200
CACHE_NAME = "client-health"

_LOCK = threading.Lock()


# ===========================================================================
# The kinds of issue, as data
# ===========================================================================
#
# One table, read by the reading, the page and the test alike. `where` is the
# screen the issue is fixed on and is never this page: the whole point is that
# the work already has somewhere to happen and nobody could find which client
# it was about.

ISSUE_KINDS = {
    "clarification": {
        "label": "Clarification needed",
        "blurb": "An insertion order line is waiting on an answer before it "
                 "can be trafficked.",
        "where": "Campaign Assets Needed",
        "href": "/tools/campaign-assets",
    },
    "assets_needed": {
        "label": "Creative outstanding",
        "blurb": "The campaign is waiting on artwork or a spot that has not "
                 "arrived.",
        "where": "Campaign Assets Needed",
        "href": "/tools/campaign-assets",
    },
    "no_creative": {
        "label": "No creative on file",
        "blurb": "We are running products for this client and nothing we can "
                 "see was ever made for them.",
        "where": "Stale Creative",
        "href": "/qa/stale-creative",
    },
    "stale_creative": {
        "label": "Creative has gone stale",
        "blurb": "It has been longer than the audit's own threshold since "
                 "anything new was made.",
        "where": "Stale Creative",
        "href": "/qa/stale-creative",
    },
    "io_renewal": {
        "label": "Insertion order ending",
        "blurb": "The flight ends soon and nothing later is on the book, so "
                 "the renewal conversation has to happen now.",
        "where": "Client 360",
        "href": "/client360",
    },
    "no_dashboard": {
        "label": "No reporting dashboard",
        "blurb": "Live products with no dashboard link on file, so the "
                 "client has nothing to look at.",
        "where": "No Dashboards",
        "href": "/qa/no-dashboards",
    },
    "proposal_unopened": {
        "label": "Proposal never opened",
        "blurb": "The link went out and nobody has looked at it — which is a "
                 "different job from being ignored.",
        "where": "Proposal Builder",
        "href": "/sales/builder/?focus=unopened",
    },
    "proposal_waiting": {
        "label": "Proposal read, no answer",
        "blurb": "They opened it and said nothing.",
        "where": "Proposal Builder",
        "href": "/sales/builder/?focus=waiting",
    },
    "proposal_expiring": {
        "label": "Price about to lapse",
        "blurb": "The quote's validity window closes within the week.",
        "where": "Proposal Builder",
        "href": "/sales/builder/?focus=expiring",
    },
    "proposal_expired": {
        "label": "Price has lapsed",
        "blurb": "They cannot accept it until it is re-quoted.",
        "where": "Proposal Builder",
        "href": "/sales/builder/?focus=expired",
    },
    "proposal_to_convert": {
        "label": "Approved, no insertion order",
        "blurb": "They said yes and nobody has written the order.",
        "where": "Proposal Builder",
        "href": "/sales/builder/?focus=to_convert",
    },
    "proof_waiting": {
        "label": "Proof answered, not acted on",
        "blurb": "The client replied to a review round and no cut has been "
                 "filed since.",
        "where": "Commercial Builder",
        "href": "/tools/commercial/",
    },
    "audit_never": {
        "label": "Website never audited",
        "blurb": "Nothing has read their site, so there is no traffic health "
                 "on this record at all.",
        "where": "Site Scans",
        "href": "/scans/",
    },
    "audit_stale": {
        "label": "Website reading is out of date",
        "blurb": f"The last audit is over {AUDIT_STALE_DAYS} days old, so the "
                 "figures below describe a site that may have changed.",
        "where": "Site Scans",
        "href": "/scans/",
    },
    "no_website": {
        "label": "No website on file",
        "blurb": "Without a domain this client cannot be joined to a scan, a "
                 "brand lookup or anything else keyed on one.",
        "where": "Match Sites to Clients",
        "href": "/tools/sites-match",
    },
}

# What a mark can say. Deliberately three states and not two: "we are not
# going to do anything about this" and "this is done" are different claims
# about the same row, and folding them into one Dismiss button loses the only
# one of the two that anybody would want to audit later.
MARK_STATES = ("open", "ignored", "done")


# ===========================================================================
# Marks and notes — the overlay
# ===========================================================================

def _marks_path() -> str:
    return os.path.join(jsonstore.data_dir("client_health"), "marks.json")


def _notes_path() -> str:
    return os.path.join(jsonstore.data_dir("client_health"), "notes.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_key(name: str) -> str:
    """The join key for a client name — exact, normalised, never a substring.

    The same derivation `hub/client_owner.py` uses, and for the same reason:
    a mark filed under a substring match is one client's decision showing on
    another client's row.
    """
    try:
        from hub.client_key import normalise_name
        out = normalise_name(name)
        if out:
            return out
    except Exception:                                   # noqa: BLE001
        pass
    return str(name or "").strip().casefold()


def fingerprint(*parts) -> str:
    """A short digest of what an issue actually said.

    A Done mark is a statement about the issue as it stood. Keyed on the
    issue's identity alone, "the banners have arrived" would go on covering a
    campaign that has since asked for three more — so the mark carries this,
    and a changed issue reports the mark as **superseded** rather than
    standing over something nobody has looked at.

    Deliberately over the evidence and not the whole row: the row carries a
    link and a label written in this file, and rewording a label here is our
    edit rather than the client's campaign changing. It must not retire every
    mark on the book.
    """
    raw = "␟".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _load(path: str, holder: str) -> list[dict]:
    rows = jsonstore.read_json(path, default=None)
    if isinstance(rows, dict):
        rows = rows.get(holder)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def marks() -> dict[str, dict]:
    """`{"<client key>|<issue key>": mark}`. Never raises."""
    try:
        rows = _load(_marks_path(), "marks")
    except Exception:                                   # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        ck, ik = _client_key(row.get("client")), str(row.get("issue") or "")
        if ck and ik:
            out[f"{ck}|{ik}"] = row
    return out


def set_mark(client: str, issue: str, state: str, *, actor: str = "",
             note: str = "", seen: str = "") -> dict:
    """Ignore an issue, mark it done, or put it back. Never raises.

    `seen` is the fingerprint the reader was looking at. It is stored rather
    than recomputed, because the whole value of the mark is that it is about
    the row somebody actually read.
    """
    name = str(client or "").strip()
    key = str(issue or "").strip()
    state = str(state or "").strip().lower()
    if not name:
        return {"ok": False, "error": "No client named."}
    if not key:
        return {"ok": False, "error": "No issue named."}
    if state not in MARK_STATES:
        return {"ok": False,
                "error": f"State must be one of {', '.join(MARK_STATES)}."}

    ck = _client_key(name)
    try:
        with _LOCK:
            rows = _load(_marks_path(), "marks")
            keep = [r for r in rows
                    if not (_client_key(r.get("client")) == ck
                            and str(r.get("issue") or "") == key)]
            if state != "open":
                keep.append({
                    "client": name, "issue": key, "state": state,
                    "seen": str(seen or "")[:32],
                    "note": str(note or "")[:MAX_NOTE],
                    "by": str(actor or "")[:120], "at": _now(),
                })
            if not jsonstore.write_json(_marks_path(), {"marks": keep}, indent=2):
                return {"ok": False,
                        "error": "The mark could not be saved. Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"The mark could not be saved: {exc}"[:200]}
    return {"ok": True, "client": name, "issue": key, "state": state}


def notes(client: str = "") -> list[dict]:
    """Notes, newest first — for one client, or every one. Never raises."""
    try:
        rows = _load(_notes_path(), "notes")
    except Exception:                                   # noqa: BLE001
        return []
    if client:
        ck = _client_key(client)
        rows = [r for r in rows if _client_key(r.get("client")) == ck]
    return sorted(rows, key=lambda r: str(r.get("at") or ""), reverse=True)


def notes_by_client() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in notes():
        ck = _client_key(row.get("client"))
        if ck:
            out.setdefault(ck, []).append(row)
    return out


def add_note(client: str, text: str, *, actor: str = "") -> dict:
    """Write one note against a client. Never raises."""
    name = str(client or "").strip()
    body = str(text or "").strip()
    if not name:
        return {"ok": False, "error": "No client named."}
    if not body:
        return {"ok": False, "error": "The note is empty."}
    row = {"client": name, "text": body[:MAX_NOTE],
           "by": str(actor or "")[:120], "at": _now(),
           "id": fingerprint(name, body, _now())}
    try:
        with _LOCK:
            rows = _load(_notes_path(), "notes")
            rows.append(row)
            # Bounded per client rather than overall: a busy account must not
            # push a quiet one's history off the end, which is what a single
            # global cap does — and what was dropped is said out loud, since
            # a note that goes missing quietly is the thing notes exist
            # against.
            ck = _client_key(name)
            mine = [r for r in rows if _client_key(r.get("client")) == ck]
            dropped = 0
            if len(mine) > MAX_NOTES_PER_CLIENT:
                mine.sort(key=lambda r: str(r.get("at") or ""))
                cut = {id(r) for r in mine[:len(mine) - MAX_NOTES_PER_CLIENT]}
                dropped = len(cut)
                rows = [r for r in rows if id(r) not in cut]
            if not jsonstore.write_json(_notes_path(), {"notes": rows}, indent=2):
                return {"ok": False,
                        "error": "The note could not be saved. Nothing changed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"The note could not be saved: {exc}"[:200]}
    out = {"ok": True, "note": row}
    if dropped:
        out["dropped"] = dropped
        out["warning"] = (f"{dropped} of the oldest notes on this client were "
                          f"dropped — the cap is {MAX_NOTES_PER_CLIENT}.")
    return out


def delete_note(note_id: str) -> dict:
    """Remove one note. Never raises."""
    nid = str(note_id or "").strip()
    if not nid:
        return {"ok": False, "error": "No note named."}
    try:
        with _LOCK:
            rows = _load(_notes_path(), "notes")
            keep = [r for r in rows if str(r.get("id") or "") != nid]
            if len(keep) == len(rows):
                return {"ok": True, "already": True}
            if not jsonstore.write_json(_notes_path(), {"notes": keep}, indent=2):
                return {"ok": False, "error": "The note could not be removed."}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"The note could not be removed: {exc}"[:200]}
    return {"ok": True}


# ===========================================================================
# Reading the sources
# ===========================================================================
#
# Every source is asked in its own function, every one of them returns
# `(answer, error)`, and a failure costs that source and nothing else. The
# rule is `hub/prospect.py`'s: the audit is worth reading when the proposal
# store is down and the proposals are worth reading when Insites is, so no
# source may take the page with it — and none of them may come back as a
# clean nothing either.

def _iso(value) -> str:
    return str(value or "")[:10]


def _issue(kind: str, subject: str, title: str, detail: str, *,
           link: str = "", at: str = "") -> dict:
    """One outstanding thing, with the screen it is fixed on already on it.

    `subject` is what the issue is *about* — an IO number, a campaign, a quote
    id, the domain — and it is what makes the key stable. Numbering the issues
    instead would move every mark the first time a client's list changed
    length.
    """
    meta = ISSUE_KINDS.get(kind) or {}
    return {
        "kind": kind,
        "key": f"{kind}:{subject}" if subject else kind,
        "subject": subject,
        "label": meta.get("label") or kind.replace("_", " ").title(),
        "blurb": meta.get("blurb") or "",
        "title": title,
        "detail": detail,
        "where": meta.get("where") or "",
        "link": link or meta.get("href") or "",
        "at": at,
        "fingerprint": fingerprint(kind, subject, detail),
    }


def _products() -> tuple[dict, str]:
    try:
        from hub import qa
        return qa.client_groups(), ""
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]


def _registry() -> tuple[dict, str]:
    """`{client key: registry row}` — where the domain and the URL come from."""
    try:
        from hub import clients_registry
        rows = clients_registry.all_clients()
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]
    out = {}
    for r in rows or ():
        key = _client_key(r.get("name"))
        if key:
            out.setdefault(key, r)
    return out, ""


def _asset_asks() -> tuple[dict, str]:
    """`{client key: [campaigns]}` still waiting on an answer or on artwork."""
    try:
        from hub import campaign_assets
        data = campaign_assets.report(scope="open")
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]
    if not data.get("measured"):
        # The rows on file cannot answer the question, which is not the same
        # as nothing being outstanding — `hub/campaign_assets.py` makes the
        # point that a cache written before those two fields existed answers
        # "no" to them on every row, about every client at once.
        return {}, (data.get("note")
                    or "The product rows on file cannot answer whether "
                       "anything is outstanding.")
    out: dict[str, list] = {}
    for c in data.get("campaigns") or ():
        key = _client_key(c.get("client"))
        if key:
            out.setdefault(key, []).append(c)
    return out, ""


def _creative() -> tuple[dict, str]:
    """`{stale-creative match key: row}` — how long since we made anything."""
    try:
        from hub import stale_creative
        return stale_creative.by_client()
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]


def _creative_key(name: str) -> str:
    try:
        from hub import stale_creative
        return stale_creative.match_key(name)
    except Exception:                                   # noqa: BLE001
        return ""


def _pipeline() -> tuple[dict, str]:
    """`{client key: [proposal cards]}` from the one pipeline reading."""
    try:
        from hub import sales_status
        data = sales_status.by_client()
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]
    if not data.get("measured"):
        return {}, data.get("error") or "The proposal store did not answer."
    out: dict[str, list] = {}
    for name, cards in (data.get("clients") or {}).items():
        key = _client_key(name)
        if key:
            out.setdefault(key, []).extend(cards)
    return out, ""


def _proofs() -> tuple[dict, str]:
    """`{client key: [live review rounds]}`, each saying whether it is answered.

    The Commercial Builder's own `/api/reviews/waiting` decides what "waiting
    on us" means, and its two helpers are imported rather than reimplemented:
    a second reading of *has this round been acted on* would put a client's
    reply on this page after somebody had already dealt with it, or drop the
    one they are actually waiting for. `_acted_on()` is a time comparison for
    a stated reason and this page must not lose it.
    """
    try:
        from modules.commercial_builder.models import (
            Client, CommercialProject, ReviewShare)
        from modules.commercial_builder.routes.review import _acted_on, _filed_at
        from modules.commercial_builder import review_spec
    except Exception as exc:                            # noqa: BLE001
        return {}, f"The Commercial Builder did not load: {type(exc).__name__}"

    try:
        shares = ReviewShare.query.filter_by(revoked=False).all()
        filed = _filed_at()
        out: dict[str, list] = {}
        for share in shares:
            project = CommercialProject.query.get(share.project_id)
            if project is None:
                continue
            if _acted_on(share, filed.get(project.id)):
                continue
            decisions = [d.to_dict() for d in share.decisions.all()]
            verdict = review_spec.verdict(decisions)
            comments = share.comments.count()
            answered = bool(verdict.get("answered")) or bool(comments)
            client = Client.query.get(project.client_id)
            key = _client_key(client.name if client else "")
            if not key:
                continue
            out.setdefault(key, []).append({
                "project_id": project.id,
                "title": project.title or "Commercial",
                "round_no": share.round_no or 1,
                # Answered or still out with them. Both are carried and the
                # build decides: a round the client has replied to is work,
                # and one still with them is a fact about where the spot has
                # got to. Counting the second as an issue would put every
                # rep's whole review book on their list of things to do.
                "answered": answered,
                "outcome": verdict.get("outcome") or "",
                "comments": comments,
                "sent_at": (share.created_at.isoformat()
                            if share.created_at else ""),
            })
        return out, ""
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]


def _audits(domains) -> tuple[dict, str]:
    try:
        from hub import upsell
        return upsell.audits_for(domains)
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]


# ===========================================================================
# The build
# ===========================================================================

def _traffic_block(audit: dict | None, *, readable: bool = True,
                   has_domain: bool = True) -> dict:
    """What the last website reading says, or which kind of nothing it is.

    Five states, not two. *No domain to read one by*, *never audited*,
    *audited and out of date*, *the scans table would not answer* and *a
    current reading* are five different things to do about a client, and only
    the last is a number worth quoting. Every figure is left out where the
    plan did not measure it rather than printed as a zero — a zero here reads
    as a claim about the client's business instead of about our audit.

    `readable` is whether the scans table answered at all, and it is the case
    this function exists for: an unreadable table hands every client an empty
    audit, and calling that "nobody has audited this website" would be the
    whole book accused of something our own reading could not check.
    """
    if not has_domain:
        return {"measured": False, "state": "no_domain", "figures": [],
                "note": "There is no domain on file to read a website by."}
    if not readable:
        return {"measured": False, "state": "unreadable", "figures": [],
                "note": "The site audits could not be read, so how this "
                        "client is doing is not measured — which is not the "
                        "same as never having been audited."}
    if not audit:
        return {"measured": False, "state": "never", "figures": [],
                "note": "Nobody has audited this website yet."}
    age = audit.get("age") or {}
    traffic = audit.get("traffic") or {}
    figures = []

    def fig(label, value, note=""):
        if value is None:
            return
        figures.append({"label": label, "value": value, "note": note})

    score = audit.get("score")
    if score is not None:
        fig("Overall score", f"{score}")
    organic = traffic.get("organic_monthly")
    if organic is not None:
        fig("Organic visits, monthly", f"{organic:,.0f}",
            "a third-party estimate, not a measured figure")
    keywords = traffic.get("keywords")
    if keywords is not None:
        fig("Keywords ranked for", f"{keywords:,.0f}")
    paid = traffic.get("paid_monthly_visits")
    if paid is not None:
        fig("Visits from their ads, monthly", f"{paid:,.0f}",
            "a third-party estimate, not a measured figure")
    speed = traffic.get("mobile_speed")
    if speed is not None:
        fig("Mobile speed", f"{speed:,.0f}")
    rating = traffic.get("review_rating")
    if rating is not None:
        count = traffic.get("review_count")
        fig("Google rating",
            f"{rating} ★" + (f" ({count:,.0f})" if count else ""))
    broken = traffic.get("broken_links")
    if broken is not None:
        fig("Broken links", f"{broken:,.0f}")

    stale = bool(age.get("stale"))
    return {
        "measured": True,
        "state": "stale" if stale else "current",
        "figures": figures,
        "when": audit.get("when") or "",
        "age_days": age.get("age_days"),
        "stale": stale,
        "note": age.get("note") or "",
        "scan_url": (f"/scans/scan/{audit['public_id']}"
                     if audit.get("public_id") else ""),
    }


def build(today: date | None = None) -> dict:
    """Every client worth a row, with everything outstanding on them.

    Which clients get a row: the ones we are working for **now** (a live
    product, or billing this month) plus anyone somebody has been assigned,
    however quiet. A book of every client we have ever had would bury the
    hundred that matter under four hundred that do not; assigning a dormant
    client is somebody saying they want to see it anyway, which this must not
    then throw away.
    """
    today = today or date.today()
    sources: dict[str, dict] = {}

    def source(name: str, error: str, note: str = "") -> None:
        sources[name] = {"measured": not error, "error": error, "note": note}

    groups, err_products = _products()
    source("products", err_products)
    registry, err_registry = _registry()
    source("registry", err_registry)
    asks, err_asks = _asset_asks()
    source("campaign_assets", err_asks)
    creative, err_creative = _creative()
    source("creative", err_creative)
    pipeline, err_pipeline = _pipeline()
    source("proposals", err_pipeline)
    proofs, err_proofs = _proofs()
    source("proofs", err_proofs)

    try:
        from hub import qa as _qa
        is_active = _qa.is_active
    except Exception:                                   # noqa: BLE001
        def is_active(g):                               # noqa: ANN001, ANN202
            return bool(g.get("live")) or bool(g.get("thisM"))

    owned = {}
    try:
        from hub import client_owner
        owned = client_owner.owners()
    except Exception:                                   # noqa: BLE001
        owned = {}

    # ---- who gets a row
    names: dict[str, str] = {}                          # key -> display name
    for name, g in (groups or {}).items():
        if is_active(g):
            key = _client_key(name)
            if key:
                names.setdefault(key, name)
    for key, row in owned.items():
        name = str(row.get("client") or "").strip()
        if key and name:
            names.setdefault(key, name)

    # ---- the audits, in one statement rather than one query per client
    domains: dict[str, str] = {}
    for key, name in names.items():
        reg = registry.get(key) or {}
        dom = str(reg.get("domain") or "").strip()
        if not dom:
            try:
                from hub.client_context import canonical_domain
                dom = canonical_domain(reg.get("url") or "")
            except Exception:                           # noqa: BLE001
                dom = ""
        if dom:
            domains[key] = dom
    audits, err_audits = _audits(sorted(set(domains.values())))
    source("audits", err_audits)

    rows = []
    for key, name in names.items():
        g = (groups or {}).get(name) or {}
        reg = registry.get(key) or {}
        domain = domains.get(key, "")
        issues: list[dict] = []

        # --- Knack: clarifications and outstanding artwork -----------------
        for camp in (asks.get(key) or ()):
            label = camp.get("campaign") or camp.get("io") or "campaign"
            for prod in camp.get("products") or ():
                if prod.get("clarification"):
                    issues.append(_issue(
                        "clarification",
                        f"{camp.get('io') or ''}/{prod.get('product_num') or prod.get('product') or ''}",
                        f"{label} — {prod.get('product') or 'product'}",
                        prod["clarification"]))
                if prod.get("assets"):
                    issues.append(_issue(
                        "assets_needed",
                        f"{camp.get('io') or ''}/{prod.get('product_num') or prod.get('product') or ''}",
                        f"{label} — {prod.get('product') or 'product'}",
                        prod["assets"]))

        # --- creative ------------------------------------------------------
        cre = creative.get(_creative_key(name)) or {}
        if cre and not cre.get("evergreen"):
            if cre.get("group") == "never":
                issues.append(_issue(
                    "no_creative", "all",
                    "Nothing on file",
                    "No creative for this client was found in any of the "
                    "stores the audit reads.",
                    link="/qa/stale-creative"))
            elif cre.get("group", "").startswith("over_"):
                days = cre.get("days_since")
                issues.append(_issue(
                    "stale_creative", "last",
                    f"{days} days since the last creative"
                    if days is not None else "Overdue for new creative",
                    f"Last was {cre.get('last_source') or 'unknown'} on "
                    f"{cre.get('last_upload') or 'a date we could not read'}.",
                    at=cre.get("last_upload") or ""))

        # --- an insertion order running out --------------------------------
        # The latest end date on the book decides. A client with a flight
        # ending next week and another running to December is not up for
        # renewal, and raising it would be a chase nobody needs to make.
        last_end = g.get("last_end")
        if isinstance(last_end, date) and g.get("live"):
            days_left = (last_end - today).days
            if 0 <= days_left <= RENEWAL_DAYS:
                ios = sorted({str(r.get("io") or "").strip()
                              for r in g.get("live") or ()
                              if str(r.get("io") or "").strip()})
                issues.append(_issue(
                    "io_renewal", last_end.isoformat(),
                    f"Ends {last_end.isoformat()} — {days_left} day"
                    f"{'' if days_left == 1 else 's'} left",
                    ("Insertion order " + ", ".join(ios) if ios
                     else "No insertion order number on the live lines."),
                    link="/client360?q=" + name.replace(" ", "+"),
                    at=last_end.isoformat()))

        # --- no dashboard ---------------------------------------------------
        if g.get("live") and not g.get("has_dash"):
            issues.append(_issue(
                "no_dashboard", "live",
                f"{len(g['live'])} live product"
                f"{'' if len(g['live']) == 1 else 's'}, no dashboard link",
                "Nothing on the product records points at a report the "
                "client can open."))

        # --- the pipeline ----------------------------------------------------
        cards = pipeline.get(key) or []
        for card in cards:
            signal = str(card.get("signal") or "")
            kind = f"proposal_{signal}" if signal else ""
            if kind not in ISSUE_KINDS:
                continue
            quote = card.get("quote") or f"#{card.get('id')}"
            detail = {
                "unopened": f"Sent {_iso(card.get('sent_at'))} and not opened.",
                "waiting": f"Opened {card.get('opens') or 0} time"
                           f"{'' if card.get('opens') == 1 else 's'}, "
                           "no answer yet.",
                "expiring": f"Lapses {card.get('expires_on') or 'soon'} "
                            f"({card.get('days_left')} days).",
                "expired": f"Lapsed {card.get('expires_on') or ''}.".strip(),
                "to_convert": ("Approved with no insertion order written."
                               + (f" {card.get('gaps')} gaps to fill."
                                  if card.get("gaps") else "")),
            }.get(signal, "")
            issues.append(_issue(
                kind, str(card.get("id") or quote),
                f"Proposal {quote}".strip(), detail,
                link=card.get("url") or ""))

        # --- proofs ----------------------------------------------------------
        rounds = proofs.get(key) or []
        for rnd in rounds:
            if not rnd.get("answered"):
                continue
            issues.append(_issue(
                "proof_waiting", str(rnd.get("project_id") or ""),
                f"{rnd.get('title')} — round {rnd.get('round_no')}",
                (f"{rnd.get('outcome') or 'answered'}"
                 + (f", {rnd['comments']} comment"
                    f"{'' if rnd['comments'] == 1 else 's'}"
                    if rnd.get("comments") else "")
                 + ". No cut has been filed since."),
                link=f"/tools/commercial/projects/{rnd.get('project_id')}/preview",
                at=rnd.get("sent_at") or ""))

        # --- the website reading ---------------------------------------------
        # A source that would not answer raises nothing at all. An unreadable
        # scans table would otherwise put "never audited" on every client at
        # once, and a client book that could not be read would put "no website
        # on file" on all of them — a page of findings about our own reading,
        # printed as findings about the clients.
        audits_ok = sources["audits"]["measured"]
        registry_ok = sources["registry"]["measured"]
        audit = audits.get(domain) if domain else None
        traffic = _traffic_block(audit, readable=audits_ok,
                                 has_domain=bool(domain))
        if not domain:
            if registry_ok:
                issues.append(_issue(
                    "no_website", "domain", "No domain on file",
                    "Nothing joins this client to a site scan, so there is no "
                    "traffic health to read."))
        elif not audit:
            if audits_ok:
                issues.append(_issue(
                    "audit_never", domain, domain,
                    "This website has never been audited.",
                    link="/tools/website-audit?domain=" + domain))
        elif traffic.get("stale"):
            issues.append(_issue(
                "audit_stale", domain,
                f"{domain} — read {traffic.get('age_days')} days ago",
                traffic.get("note") or "",
                link=traffic.get("scan_url") or "",
                at=_iso(traffic.get("when"))))

        partners = sorted(g.get("partners") or ())
        sales = sorted(g.get("sales") or ())
        rows.append({
            "client": name,
            "key": key,
            "domain": domain,
            "url": str(reg.get("url") or ""),
            "partner": partners[0] if partners else "",
            "other_partners": partners[1:],
            "sales": sales[0] if sales else "",
            "live_products": len(g.get("live") or ()),
            "monthly": round(float(g.get("live_total") or 0.0), 2),
            "traffic": traffic,
            "engagement": {
                "proposal_opens": sum(int(c.get("opens") or 0) for c in cards),
                "open_proposals": len(cards),
                "proof_rounds_out": sum(1 for r in rounds
                                        if not r.get("answered")),
                "proof_rounds_answered": sum(1 for r in rounds
                                             if r.get("answered")),
                "measured": bool(sources["proposals"]["measured"]),
            },
            "issues": issues,
            "c360": "/client360?q=" + name.replace(" ", "+"),
        })

    measured = bool(sources["products"]["measured"]
                    and sources["registry"]["measured"])
    return {
        "measured": measured,
        "generated_at": _now(),
        "today": today.isoformat(),
        "sources": sources,
        "rows": rows,
        "renewal_days": RENEWAL_DAYS,
        "stale_days": AUDIT_STALE_DAYS,
        "note": _build_note(rows, sources),
    }


def _build_note(rows: list, sources: dict) -> str:
    """What this run could and could not see.

    Named rather than counted: a source that failed makes every client it
    would have spoken for look clear, and a page that does not say so is
    reporting a hole as good news.
    """
    parts = [f"{len(rows)} clients read."]
    blind = [k.replace("_", " ") for k, v in sources.items()
             if not v.get("measured")]
    if blind:
        parts.append("Not measured this run: " + ", ".join(sorted(blind))
                     + " — anything those would have raised is missing from "
                       "every row, not absent from it.")
    return " ".join(parts)


# ===========================================================================
# Serving it: the build once a day, the overlay on every read
# ===========================================================================

def cached(force: bool = False) -> dict:
    """Today's run. Built on the first ask, read by every ask after it.

    A run that could not read the products or the client list is never stored
    as the day's answer — `hub/report_cache.py` states the rule and this is
    the case it was written for: "we could not look" frozen into the shape of
    "there is nothing on your desk" would stand until tomorrow, on the one
    page somebody opens to find out what to do today.
    """
    # The import is guarded and the call is not, the arrangement
    # `hub/stale_creative._cached()` arrived at: a build that fails must fail
    # once and reach the caller, not be quietly run a second time by a bare
    # `except` that cannot tell "report_cache is missing" from "Knack refused".
    try:
        from hub import report_cache
    except Exception:                                   # noqa: BLE001
        report_cache = None
    if report_cache is None:
        out = build()
        out.setdefault("cache", {"cached": False, "from_cache": False,
                                 "ran": True, "line": "Not cached."})
        return out
    return report_cache.serve(CACHE_NAME, build, force=bool(force))


def _apply_overlay(data: dict, *, owner_index: dict, mark_index: dict,
                   note_index: dict, user_index: dict) -> list[dict]:
    """Owners, marks and notes onto today's run — on read, never into it.

    Nothing is mutated in place: the cached payload is shared between this
    request, the next one and the other worker, and writing an owner into it
    would hand the next reader an answer taken before the assignment they
    just made.
    """
    out = []
    for row in data.get("rows") or ():
        key = row.get("key") or ""
        assignment = owner_index.get(key) or {}
        email = str(assignment.get("email") or "").lower()
        copy = dict(row)
        copy["owner"] = {
            "email": email,
            "name": (user_index.get(email) or {}).get("name") or email,
            # An owner whose account is gone is named as that rather than
            # read as unassigned: the client has somebody's name on it and
            # nobody behind it, which is the state a handover has to find.
            "known": bool(email and email in user_index),
            "at": str(assignment.get("at") or ""),
            "by": str(assignment.get("by") or ""),
        }
        open_issues, handled = [], []
        for issue in row.get("issues") or ():
            mark = mark_index.get(f"{key}|{issue['key']}")
            if not mark:
                open_issues.append(issue)
                continue
            seen = str(mark.get("seen") or "")
            superseded = bool(seen) and seen != issue.get("fingerprint")
            entry = dict(issue)
            entry["mark"] = {
                "state": str(mark.get("state") or ""),
                "by": str(mark.get("by") or ""),
                "at": str(mark.get("at") or ""),
                "note": str(mark.get("note") or ""),
                "superseded": superseded,
            }
            if superseded:
                # It has changed since somebody signed it off, so it is open
                # again — and it says why, rather than reappearing as if
                # nobody had ever looked at it.
                open_issues.append(entry)
            else:
                handled.append(entry)
        copy["issues"] = open_issues
        copy["handled"] = handled
        copy["issue_count"] = len(open_issues)
        copy["handled_count"] = len(handled)
        copy["notes"] = note_index.get(key) or []
        copy["note_count"] = len(copy["notes"])
        out.append(copy)
    return out


def report(*, owner: str = "", scope: str = "", q: str = "",
           force: bool = False) -> dict:
    """The report one person opens, filtered and sorted.

    The build is cached and the **filter runs per request**: `owner=` and a
    search box are not cache keys, for the reason `hub/report_cache.py` gives
    — a free-text search types one file per keystroke onto a 5 GB disk.

    `scope` is `mine` (the default when an owner is named), `all`, or
    `unassigned`. Each is a real question: what is on my desk, what is on
    everybody's, and what is on nobody's.
    """
    data = cached(force=force)
    try:
        from hub import client_owner
        owner_index = client_owner.owners()
        users, user_error = client_owner.assignable_users()
    except Exception as exc:                            # noqa: BLE001
        owner_index, users = {}, []
        user_error = f"{type(exc).__name__}: {exc}"[:200]
    user_index = {u["email"]: u for u in users}

    rows = _apply_overlay(data, owner_index=owner_index,
                          mark_index=marks(), note_index=notes_by_client(),
                          user_index=user_index)

    owner = str(owner or "").strip().lower()
    scope = (str(scope or "").strip().lower()
             or ("mine" if owner else "all"))
    if scope not in ("mine", "all", "unassigned"):
        scope = "all"

    total = len(rows)
    if scope == "mine" and owner:
        shown = [r for r in rows if r["owner"]["email"] == owner]
    elif scope == "unassigned":
        shown = [r for r in rows if not r["owner"]["email"]]
    else:
        shown = list(rows)

    needle = str(q or "").strip().lower()
    if needle:
        shown = [r for r in shown
                 if needle in r["client"].lower()
                 or needle in (r.get("domain") or "").lower()
                 or needle in (r.get("partner") or "").lower()]

    # Most outstanding first. Ties break on the money, because between two
    # clients with three issues each the larger campaign is the one to open
    # first, and then on the name so the order is stable between runs.
    shown.sort(key=lambda r: (-r["issue_count"], -(r.get("monthly") or 0),
                              r["client"].lower()))

    counts_by_kind: dict[str, int] = {}
    for r in shown:
        for issue in r["issues"]:
            counts_by_kind[issue["kind"]] = counts_by_kind.get(issue["kind"], 0) + 1

    out = dict(data)
    out["rows"] = shown
    out["scope"] = scope
    out["owner"] = owner
    out["q"] = q
    out["users"] = users
    out["user_error"] = user_error
    out["kinds"] = ISSUE_KINDS
    out["counts"] = {
        "clients_total": total,
        "clients_shown": len(shown),
        "clients_with_issues": sum(1 for r in shown if r["issue_count"]),
        "issues": sum(r["issue_count"] for r in shown),
        "handled": sum(r["handled_count"] for r in shown),
        "unassigned": sum(1 for r in rows if not r["owner"]["email"]),
        "mine": sum(1 for r in rows
                    if owner and r["owner"]["email"] == owner),
        "by_kind": counts_by_kind,
    }
    # Which kind of empty this is. "Nothing is outstanding on your clients"
    # and "nobody has assigned you a client yet" render identically as a
    # nought, and only the second is somebody's to fix.
    if scope == "mine" and not shown:
        out["empty_reason"] = ("no_clients" if not out["counts"]["mine"]
                               else "nothing_outstanding")
    elif not shown:
        out["empty_reason"] = "nothing_outstanding" if total else "no_clients"
    else:
        out["empty_reason"] = ""
    return out


def scoreboard(owner: str = "") -> dict:
    """The compact answer the dashboard card draws.

    Reads the same run as the page rather than counting again — two screens
    answering "what is on my desk" separately is how they come to disagree in
    front of the same person, which is the /api/db/structure versus
    /api/integrity trap.
    """
    try:
        data = report(owner=owner, scope="mine" if owner else "unassigned")
    except Exception as exc:                            # noqa: BLE001
        return {"measured": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "url": "/my-clients"}
    counts = data.get("counts") or {}
    top = [{"client": r["client"], "issues": r["issue_count"],
            "url": "/my-clients?client=" + r["client"].replace(" ", "+")}
           for r in (data.get("rows") or [])[:4] if r["issue_count"]]
    return {
        "measured": bool(data.get("measured")),
        "error": "",
        "scope": data.get("scope"),
        "clients": counts.get("clients_shown", 0),
        "with_issues": counts.get("clients_with_issues", 0),
        "issues": counts.get("issues", 0),
        "unassigned": counts.get("unassigned", 0),
        "empty_reason": data.get("empty_reason", ""),
        "top": top,
        "cache": data.get("cache") or {},
        "url": "/my-clients",
        "note": data.get("note") or "",
    }


# ===========================================================================
# Routes
# ===========================================================================
#
# One gate on the blueprint rather than one per view — `hub/blueprint_guard.py`
# exists because this Hub has paid three times for the other arrangement, and
# these routes name every client, what is wrong with each and who owns them.

try:                                                    # pragma: no cover
    from hub import blueprint_guard
    blueprint_guard.install(bp)
except Exception:                                       # noqa: BLE001
    pass


def _actor() -> str:
    """The signed-in name, or "" for the shared-password session."""
    try:
        from hub import current_user
        return current_user() or ""
    except Exception:                                   # noqa: BLE001
        return ""


def viewer() -> dict:
    """Who is reading this, as an account rather than as a name.

    The Hub's own session cookie carries a **display name** and nothing else,
    and two people on this roster share a first name — so "my clients" cannot
    be answered from it. `current_account()` re-reads the row and is the only
    thing here that knows an email.

    A `PANEL_PASSWORD` session has no account at all. It is not given
    somebody's book on a name match: "Shared login" is a true statement about
    the session and a useless one in a field whose whole value is whose it is,
    which is the refusal `hub/ad_copy.py` makes one form along. It is told so
    and shown the whole book instead.
    """
    name = _actor()
    try:
        from hub.users_routes import current_account
        account = current_account()
    except Exception as exc:                            # noqa: BLE001
        return {"name": name, "email": "", "shared": False,
                "error": f"The account could not be read: {type(exc).__name__}"}
    if account is None:
        return {"name": name, "email": "", "shared": True, "error": ""}
    return {"name": account.name or name, "email": (account.email or "").lower(),
            "shared": False, "error": ""}


def _json_error(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


@bp.route("/my-clients")
def my_clients_page():
    me = viewer()
    return render_template("client_health.html", me=me,
                           kinds=ISSUE_KINDS, renewal_days=RENEWAL_DAYS,
                           stale_days=AUDIT_STALE_DAYS)


@bp.route("/api/my-clients")
def api_my_clients():
    me = viewer()
    scope = (request.args.get("scope") or "").strip().lower()
    owner = (request.args.get("owner") or "").strip().lower()
    # An owner named on the URL is how an admin looks at somebody else's book.
    # With nothing named it is the reader's own — unless there is no account
    # behind the session, in which case there is no "own" to show and the page
    # is told which of the two it is looking at.
    if not owner and not me["shared"]:
        owner = me["email"]
    if me["shared"] and not owner and not scope:
        scope = "all"
    try:
        data = report(owner=owner, scope=scope,
                      q=(request.args.get("q") or ""))
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"measured": False, "rows": [], "counts": {},
                        "error": f"{type(exc).__name__}: {exc}"[:300]})
    data["me"] = me
    return jsonify(data)


@bp.route("/api/my-clients/refresh", methods=["POST"])
def api_my_clients_refresh():
    """Run it again now. A POST, never a GET.

    A GET that rebuilds is one a reload, a prefetch or a link preview fires
    without anybody asking, and this build walks the products, the creative
    audit, the proposal store and a batch of website audits.
    """
    me = viewer()
    owner = (request.args.get("owner") or "").strip().lower() or me["email"]
    try:
        data = report(owner=owner,
                      scope=(request.args.get("scope") or "").strip().lower(),
                      q=(request.args.get("q") or ""), force=True)
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"measured": False, "rows": [], "counts": {},
                        "error": f"{type(exc).__name__}: {exc}"[:300]})
    data["me"] = me
    return jsonify(data)


@bp.route("/api/my-clients/scoreboard")
def api_my_clients_scoreboard():
    me = viewer()
    out = scoreboard(owner="" if me["shared"] else me["email"])
    out["me"] = me
    return jsonify(out)


@bp.route("/api/my-clients/mark", methods=["POST"])
def api_my_clients_mark():
    body = request.get_json(silent=True) or {}
    result = set_mark(
        str(body.get("client") or ""), str(body.get("issue") or ""),
        str(body.get("state") or ""), actor=_actor(),
        note=str(body.get("note") or ""), seen=str(body.get("seen") or ""))
    if not result.get("ok"):
        return _json_error(result.get("error") or "The mark was not saved.")
    _log("client_health_mark",
         detail=f"{result['client']} · {result['issue']} -> {result['state']}")
    return jsonify(result)


@bp.route("/api/my-clients/note", methods=["POST"])
def api_my_clients_note():
    body = request.get_json(silent=True) or {}
    result = add_note(str(body.get("client") or ""),
                      str(body.get("text") or ""), actor=_actor())
    if not result.get("ok"):
        return _json_error(result.get("error") or "The note was not saved.")
    _log("client_health_note", detail=str(body.get("client") or ""))
    return jsonify(result)


@bp.route("/api/my-clients/note/delete", methods=["POST"])
def api_my_clients_note_delete():
    body = request.get_json(silent=True) or {}
    result = delete_note(str(body.get("id") or ""))
    if not result.get("ok"):
        return _json_error(result.get("error") or "The note was not removed.")
    return jsonify(result)


# ---- assigning ------------------------------------------------------------

@bp.route("/qa/client-owners")
def client_owners_page():
    return render_template("client_owners.html", me=viewer())


@bp.route("/api/client-owners")
def api_client_owners():
    """Who owns what, plus the two ways of selecting a group of clients."""
    from hub import client_owner
    try:
        data = client_owner.summary()
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"measured": False, "rows": [], "counts": {},
                        "error": f"{type(exc).__name__}: {exc}"[:300]})
    users, user_error = client_owner.assignable_users()
    partners, partner_error = client_owner.clients_by_partner()
    data["users"] = users
    data["user_error"] = user_error
    # Sorted by how many clients each partner carries, biggest first: the
    # partner-level control exists for the ones with thirty franchises on
    # them, and an alphabetical list buries those under the ones with one.
    data["partners"] = sorted(
        ({"partner": name, "clients": clients, "count": len(clients)}
         for name, clients in partners.items()),
        key=lambda p: (-p["count"], p["partner"].lower()))
    data["partner_error"] = partner_error
    return jsonify(data)


@bp.route("/api/client-owners/assign", methods=["POST"])
def api_client_owners_assign():
    from hub import client_owner
    body = request.get_json(silent=True) or {}
    clients = body.get("clients")
    if isinstance(clients, str):
        clients = [clients]
    email = str(body.get("email") or "")
    result = client_owner.assign_many(clients or [], email, actor=_actor(),
                                      note=str(body.get("note") or ""))
    if not result.get("ok"):
        # The whole result, not a bare message: every row carries its own
        # reason and a 400 that drops them is the one-number answer this
        # module refuses one function along.
        return jsonify(result), 400
    _log("client_owner_assigned",
         detail=f"{result['assigned']} clients -> {result['email']}")
    return jsonify(result)


@bp.route("/api/client-owners/unassign", methods=["POST"])
def api_client_owners_unassign():
    from hub import client_owner
    body = request.get_json(silent=True) or {}
    clients = body.get("clients")
    if isinstance(clients, str):
        clients = [clients]
    result = client_owner.unassign_many(clients or [], actor=_actor())
    if not result.get("ok"):
        return jsonify(result), 400
    _log("client_owner_cleared", detail=f"{result['cleared']} clients")
    return jsonify(result)


# ---- the one client, from Client 360 --------------------------------------

@bp.route("/api/client/owner")
def api_client_owner():
    """Who owns one client. Reachable from inside the Suite frame.

    `/api/client/` is what `hub/suite_embed.EMBEDDABLE` allowlists, so a card
    on Client 360 pointed anywhere else renders on every screen except the one
    it is framed in — the half-broken embed that file exists to prevent.
    """
    from hub import client_owner
    name = (request.args.get("client") or "").strip()
    if not name:
        return _json_error("A client is required.")
    row = client_owner.owner_of(name) or {}
    users, user_error = client_owner.assignable_users()
    index = {u["email"]: u for u in users}
    email = client_owner.normalise_email(row.get("email"))
    return jsonify({
        "ok": True, "client": name, "email": email,
        "owner": client_owner.display_name(email, index) if email else "",
        "known": bool(email and email in index),
        "at": str(row.get("at") or ""), "by": str(row.get("by") or ""),
        "users": users, "user_error": user_error,
    })


@bp.route("/api/client/owner/set", methods=["POST"])
def api_client_owner_set():
    """Assign or clear the owner of one client.

    A separate path from the read on purpose: the embed companion cookie is
    accepted for GET and HEAD only, so a write from inside the Suite frame is
    refused — which is the stated consequence of that design rather than a
    fault here, and the card says so instead of failing silently on save.
    """
    from hub import client_owner
    body = request.get_json(silent=True) or {}
    name = str(body.get("client") or "").strip()
    email = str(body.get("email") or "").strip()
    if not name:
        return _json_error("A client is required.")
    if email:
        result = client_owner.assign(name, email, actor=_actor(),
                                     note=str(body.get("note") or ""))
    else:
        result = client_owner.unassign(name, actor=_actor())
    if not result.get("ok"):
        return _json_error(result.get("error") or "Nothing was saved.")
    _log("client_owner_assigned" if email else "client_owner_cleared",
         detail=f"{name} -> {email or '(nobody)'}")
    return jsonify(result)


def _log(event: str, **extra) -> None:
    """`audit.log()`'s first positional is the module. Never raises."""
    try:
        from hub import audit
        audit.log("client_health", event, actor=_actor(), **extra)
    except Exception:                                   # noqa: BLE001
        pass


def register_client_health(app):
    app.register_blueprint(bp)
    return app
