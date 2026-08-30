"""Prospect 360 — the record a scanned business gets before it is a client.

Client 360 answers "what is going on with this client" by joining eleven
sources onto a Knack record. A prospect has no Knack record, so before this
they had no page at all: a website audit produced a row in a flat table with a
name, an email and a delivery pill, and everything that made the prospect worth
calling — what they are already spending, what the audit found, the proposal
somebody drafted, the mock-up they were sent — was scattered across four tools
and one CRM with nothing joining them up.

**The lead id is the record.** Not the domain and not the company name: a
prospect is very often a business with no website on file and a name typed by
whoever took the call, and both of those change. `hub/leads.py` already
allocates an id, already survives a merge, and is already what the Suite
contact is filed against, so it is the key everything here hangs off.

## What this module owns, and what it deliberately does not

The dividing line is the one thing worth getting right, because getting it
wrong produces two pipelines:

* **Smart 1 Suite owns the working state.** The stage, the owner, the notes and
  the conversation live there — that is the CRM, it is where the calls and the
  texts already are, and a stage stored here as well would be two systems
  answering "where has this got to" differently with nothing on either screen
  saying which to believe. So this module *reads* stage, owner and notes
  through `hub/suite_opportunity.py`, renders them, and **never writes a
  stage**. A note typed on this page is posted to the Suite contact, so it
  lands in the one place somebody reading the history will look.

* **The Hub owns the evidence.** The audit and its history, the proposals, the
  assets, and the activity log of what we have actually done. None of that
  exists in Suite and none of it should be copied there.

## Rules, each of which is a way this page could lie

* **Every section answers `(rows, measured, error)` in spirit.** A Suite that
  will not answer is *not measured*, never an empty pipeline — "nobody has
  moved this prospect anywhere" is what somebody stops chasing a deal over.
  `_section()` is the one shape, so no card can invent its own kind of empty.

* **A section that fails costs only itself.** The audit is worth reading when
  Suite is down, and the notes are worth reading when Insites is. Each source
  is caught by name.

* **Nothing here reaches a provider more than once per load**, and nothing
  here spends a credit. Running an audit is a button on the record that posts
  to the module that owns scans.

* **Converting is a link, not a creation.** A client in this Hub is a business
  with a product in Knack, which is what billing reads. `convert()` ties the
  prospect to an account that already exists and carries the assets across; it
  never writes a client record, the rule `hub/leads.mark_converted` states at
  length.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

MAX_ASSET_MB = int(os.environ.get("PROSPECT_ASSET_MAX_MB") or 25)
STORE = "prospects"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _section(rows=None, *, measured: bool = True, error: str = "",
             note: str = "", **extra) -> dict:
    """One card's worth of answer, in the one shape every card uses.

    `measured` is the difference between "there is nothing here" and "we could
    not look", and it is a key rather than an inference from an empty list
    because those two render differently on every card and a renderer that has
    to guess gets it wrong on the one that matters.
    """
    out = {"rows": rows if rows is not None else [],
           "measured": bool(measured) and not error,
           "error": error, "note": note}
    out.update(extra)
    return out


def _caught(fn, what: str, **kw):
    """Run a source; turn any failure into a named, non-fatal `_section`."""
    try:
        return fn()
    except Exception as exc:                                # noqa: BLE001
        return _section(measured=False,
                        error=f"{type(exc).__name__}: {exc}"[:300],
                        note=f"{what} could not be read, so this is not "
                             f"measured rather than empty.", **kw)


# ==========================================================================
# Assets
# ==========================================================================
#
# A prospect collects things before they are a client: the mock-up they were
# sent, a screenshot of the competitor they complained about, the rate sheet
# they emailed over, the signed page. They lived in somebody's inbox.
#
# Stored through `hub/storage.py` and indexed through `hub/jsonstore.py`, for
# the reasons CLAUDE.md gives at length: a module that uploads to Cloudinary
# its own way is a module the next fix has to be applied to twice, and JSON
# written outside jsonstore is on a disk that is not backed up.

def _index_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir(STORE), "assets.json")


def _all_assets() -> dict:
    from hub import jsonstore
    data = jsonstore.read_json(_index_path(), default={})
    return data if isinstance(data, dict) else {}


def assets_for(lead_id: str) -> list[dict]:
    rows = _all_assets().get(str(lead_id or ""), [])
    return [r for r in rows if isinstance(r, dict) and not r.get("deleted")]


def add_asset(lead_id: str, filename: str, data: bytes, *,
              label: str = "", actor: str = "") -> dict:
    """Store one file against a prospect. Returns `{ok, asset|error}`.

    Two rules. The **size is checked before the upload**, not after, because a
    refusal after a 40 MB round trip is a refusal the person waited for. And a
    failed upload is reported **by name** rather than as a generic save
    failure: "Cloudinary is not configured" and "that file is too big" send
    somebody to two different places.
    """
    lead_id = str(lead_id or "").strip()
    if not lead_id:
        return {"ok": False, "error": "No prospect to file this against."}
    if not data:
        return {"ok": False, "error": "That file is empty."}
    if len(data) > MAX_ASSET_MB * 1024 * 1024:
        return {"ok": False,
                "error": f"That file is {len(data) // 1048576} MB and the "
                         f"limit is {MAX_ASSET_MB} MB."}
    try:
        from hub import storage
        stored = storage.put(STORE, filename, data, subpath=lead_id)
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False,
                "error": f"That could not be stored ({type(exc).__name__}: "
                         f"{exc})."[:300]}

    row = {
        "id": uuid.uuid4().hex[:12],
        "filename": filename[:200],
        "label": str(label or "").strip()[:200],
        "url": stored.url,
        "public_id": stored.public_id,
        "resource_type": stored.resource_type,
        "bytes": stored.bytes,
        "backend": stored.backend,
        "added": _now(),
        "added_by": str(actor or "")[:120],
    }
    from hub import jsonstore
    rows = _all_assets()
    rows.setdefault(lead_id, []).append(row)
    jsonstore.write_json(_index_path(), rows)
    _log("asset_added", lead=lead_id, actor=actor, detail=row["filename"])
    return {"ok": True, "asset": row}


def delete_asset(lead_id: str, asset_id: str, actor: str = "") -> dict:
    """Remove a file from a prospect record.

    The stored copy goes with it and the two outcomes are **counted apart**,
    the rule `hub/domain_links.py` gives: one tick covering both is how
    somebody learns not to trust the tick. The index row is marked rather than
    dropped, so a delete somebody regrets is still readable.
    """
    from hub import jsonstore
    lead_id, asset_id = str(lead_id or ""), str(asset_id or "")
    rows = _all_assets()
    row = next((r for r in rows.get(lead_id, [])
                if isinstance(r, dict) and r.get("id") == asset_id
                and not r.get("deleted")), None)
    if row is None:
        return {"ok": False, "error": "That file is not on this record."}
    removed, why = False, ""
    try:
        from hub import storage
        removed = storage.delete(STORE, row.get("public_id") or "",
                                 row.get("resource_type") or "raw")
    except Exception as exc:                                # noqa: BLE001
        why = f"{type(exc).__name__}: {exc}"[:200]
    row["deleted"] = _now()
    row["deleted_by"] = str(actor or "")[:120]
    row["stored_copy_removed"] = bool(removed)
    jsonstore.write_json(_index_path(), rows)
    _log("asset_deleted", lead=lead_id, actor=actor,
         detail=row.get("filename") or "")
    return {"ok": True, "removed_from_record": True,
            "stored_copy_removed": bool(removed),
            "note": ("Removed from the record and deleted from storage."
                     if removed else
                     "Removed from the record. The stored copy could not be "
                     "deleted" + (f" ({why})" if why else "") + ", so it may "
                     "still exist behind its own link.")}


def _log(event: str, **extra):
    try:
        from hub import audit
        audit.log("prospect", event, **extra)
    except Exception:                                       # noqa: BLE001
        pass


# ==========================================================================
# Scan history
# ==========================================================================

def scan_history(domain: str, limit: int = 8) -> dict:
    """Every audit ever run on this website, newest first.

    Read through the shared engine rather than by importing the mounted scans
    module — that module has its own session and teardown hung off `flask.g`,
    and reaching into it from a hub route is the trap CLAUDE.md names at
    length.
    """
    from hub.client_context import canonical_domain
    key = canonical_domain(domain or "")
    if not key:
        return _section(measured=True,
                        note="No website on file, so there is nothing to have "
                             "scanned. Add one and the audit can run.")
    try:
        from sqlalchemy import inspect as sa_inspect, text
        from hub.extensions import shared_engine
        engine = shared_engine()
        if not sa_inspect(engine).has_table("scans"):
            return _section(measured=False,
                            error="no scan table yet",
                            note="Nothing has been scanned on this deployment "
                                 "yet, so this is not measured.")
        sql = ("SELECT public_id, overall_score, tier, status, source, "
               "created_at, completed_at FROM scans WHERE domain_key = :k "
               "ORDER BY id DESC LIMIT :n")
        with engine.connect() as conn:
            found = conn.execute(text(sql),
                                 {"k": key, "n": max(1, limit)}).mappings().all()
    except Exception as exc:                                # noqa: BLE001
        return _section(measured=False, error=f"{type(exc).__name__}: {exc}"[:300],
                        note="The scan history could not be read, so it is not "
                             "measured rather than empty.")
    rows = []
    for r in found:
        when = str(r.get("completed_at") or r.get("created_at") or "")
        rows.append({
            "public_id": r.get("public_id") or "",
            "score": r.get("overall_score"),
            "tier": r.get("tier") or "",
            "status": r.get("status") or "",
            "source": r.get("source") or "",
            "when": when[:19].replace("T", " "),
            "url": f"/scans/scan/{r.get('public_id')}" if r.get("public_id") else "",
        })
    return _section(rows, measured=True, domain=key,
                    note=("" if rows else
                          "This website has never been audited. Running one "
                          "spends a credit and takes a few minutes."))


# ==========================================================================
# What Smart 1 Suite says about them
# ==========================================================================

def suite_state(lead: dict) -> dict:
    """Stage, owner and notes — read from the CRM, never decided here.

    Four different empties, and telling them apart is the whole point:

    * **Not configured** — this deployment has no Suite token, so there is no
      pipeline to read. Not a prospect nobody has moved.
    * **Not delivered** — the lead never reached Suite, so there is no contact
      to read. That is a delivery problem with its own retry button, and
      saying "no stage" about it sends somebody to the wrong screen.
    * **Could not be read** — a scope, a timeout, a rotated token.
    * **Read, and there is no opportunity yet** — the only one of the four
      that means somebody should open a deal.
    """
    contact_id = str((lead or {}).get("contact_id") or "").strip()
    try:
        from hub import suite_opportunity as suite
    except Exception as exc:                                # noqa: BLE001
        return _section(measured=False, error=f"{type(exc).__name__}",
                        state="unavailable",
                        note="The Suite integration could not be loaded.")
    if not suite.configured():
        return _section(measured=False, state="unconfigured",
                        note="Smart 1 Suite is not configured on this "
                             "deployment, so where this prospect has got to "
                             "is not measured — it is not a prospect nobody "
                             "has moved.")
    if not contact_id:
        delivered = bool((lead or {}).get("delivered"))
        return _section(
            measured=False,
            state="undelivered",
            note=("This lead reached Suite before contact ids were recorded, "
                  "so there is nothing to read it back by."
                  if delivered else
                  "This lead has not reached Smart 1 Suite yet, so it has no "
                  "contact and no stage. Retry the delivery from the Leads "
                  "panel and this fills in."))

    out = {"state": "read", "contact_id": contact_id}
    try:
        out["contact"] = suite.contact_snapshot(contact_id)
    except Exception as exc:                                # noqa: BLE001
        return _section(measured=False, state="error",
                        error=f"{type(exc).__name__}: {exc}"[:300],
                        note="Smart 1 Suite could not be read, so the stage "
                             "and the owner are not measured.")
    # The deals and the notes are separate calls, so one failing must not cost
    # the other -- and neither costs the contact we already have.
    try:
        out["opportunities"] = suite.opportunities_for(contact_id)
        out["opportunities_measured"] = True
        out["opportunities_error"] = ""
    except Exception as exc:                                # noqa: BLE001
        out["opportunities"] = []
        out["opportunities_measured"] = False
        out["opportunities_error"] = f"{type(exc).__name__}: {exc}"[:300]
    try:
        out["notes"] = suite.notes_for(contact_id)
        out["notes_measured"] = True
        out["notes_error"] = ""
    except Exception as exc:                                # noqa: BLE001
        out["notes"] = []
        out["notes_measured"] = False
        out["notes_error"] = f"{type(exc).__name__}: {exc}"[:300]

    opps = out["opportunities"]
    if out["opportunities_measured"] and not opps:
        out["note"] = ("Read, and there is no deal open against this contact "
                       "yet — this is the one empty that means somebody should "
                       "open one.")
    return _section(opps, measured=True, **out)


# ==========================================================================
# The record
# ==========================================================================

def _lead_domain(lead: dict) -> str:
    from hub.client_context import canonical_domain
    for value in (lead.get("website"),
                  (lead.get("fields") or {}).get("website"),
                  (lead.get("meta") or {}).get("domain")):
        if value:
            key = canonical_domain(str(value))
            if key:
                return key
    return ""


def _proposals(lead: dict) -> dict:
    """Proposals filed against this prospect's business name.

    Matched on the company name and nothing looser, because a proposal
    attributed to the wrong business is the worst thing this record can do.
    A prospect with no company name on the row gets *not measured* rather
    than every proposal in the store.
    """
    company = str(lead.get("company") or lead.get("client") or "").strip()
    if not company:
        return _section(measured=True,
                        note="No business name on this lead, so there is "
                             "nothing to look a proposal up by.")
    from hub import proposals as store
    rows = store.list_proposals(company) or []
    return _section(rows, measured=True, client=company,
                    note=("" if rows else
                          f"No proposal has been filed against “{company}” "
                          f"yet."))


def _work(lead: dict) -> dict:
    """What the Hub has actually done for this prospect."""
    company = str(lead.get("company") or lead.get("client") or "").strip()
    if not company:
        return _section(measured=True,
                        note="No business name on this lead, so nothing can "
                             "be attributed to it yet.")
    from hub.client_brand import work_log
    # work_log answers a dict, not a list: `items` is the rows and `note`
    # names the one thing it cannot see -- a tool that does not write to the
    # activity log at all.
    found = work_log(company, limit=40) or {}
    rows = found.get("items") or []
    return _section(rows, measured=True, by_source=found.get("by_source") or {},
                    note=(found.get("note") or "") if rows else
                         "Nothing has been produced for this prospect yet.")


def _duplicates(lead: dict) -> dict:
    """Other rows in the Leads panel that look like this same prospect."""
    from hub import leads
    found = leads.merge_candidates(days=730)
    if found.get("error"):
        return _section(measured=False, error=found["error"],
                        note="Whether this prospect is in the panel twice is "
                             "not measured.")
    mine = []
    for group in (found.get("certain") or []) + (found.get("possible") or []):
        ids = [row.get("id") for row in group.get("leads") or []]
        if lead.get("id") in ids:
            mine.append({"why": group.get("why"), "evidence": group.get("evidence"),
                         "leads": [r for r in group["leads"]
                                   if r.get("id") != lead.get("id")]})
    return _section(mine, measured=True,
                    note=("" if mine else
                          "Nothing else in the panel looks like this business."))


def record(lead_id: str) -> dict:
    """Everything about one prospect, section by section.

    Never raises. A prospect nobody can find answers `found: False` rather
    than 404ing inside a template, because this id is in browser history and a
    merged row resolves to its survivor.
    """
    from hub import leads
    lead = leads.get(lead_id)
    if lead is None:
        return {"found": False, "id": str(lead_id or ""),
                "note": "There is no lead with that id. It may have been "
                        "captured outside the period the panel keeps, or the "
                        "link may be from before a merge that has since been "
                        "undone."}

    domain = _lead_domain(lead)
    out = {
        "found": True,
        "id": lead.get("id"),
        "asked_for": str(lead_id or ""),
        "merged_from": lead.get("merged_ids") or [],
        "lead": {
            "name": lead.get("name") or "",
            "company": lead.get("company") or "",
            "email": lead.get("email") or "",
            "phone": lead.get("phone") or "",
            "website": lead.get("website") or (lead.get("fields") or {}).get("website") or "",
            "domain": domain,
            "created": lead.get("created") or "",
            "first_seen": lead.get("first_seen") or lead.get("created") or "",
            "source": lead.get("source") or "",
            "page": lead.get("page") or "",
            "also_from": lead.get("also_from") or [],
            "delivered": bool(lead.get("delivered")),
            "contact_id": lead.get("contact_id") or "",
            "contact_ids": lead.get("contact_ids") or
                           ([lead["contact_id"]] if lead.get("contact_id") else []),
            "last_error": lead.get("last_error") or "",
            "retryable": lead.get("retryable", True),
            "client": lead.get("client") or "",
            "converted_at": lead.get("converted_at") or "",
            "pdf_url": lead.get("pdf_url") or "",
            "audit_url": (lead.get("meta") or {}).get("audit_url") or "",
            "report_url": (lead.get("meta") or {}).get("report_url") or "",
            "fields": lead.get("fields") or {},
        },
    }

    # The audit, first, and the same reading the Website Audit tool shows --
    # a second description of it would drift, and the two screens would then
    # disagree about a business's own money.
    out["audit"] = _caught(
        lambda: _audit_section(lead, domain), "The website audit")
    out["scans"] = _caught(lambda: scan_history(domain), "The scan history")
    out["suite"] = _caught(lambda: suite_state(lead), "Smart 1 Suite")
    out["proposals"] = _caught(lambda: _proposals(lead), "The proposal store")
    out["work"] = _caught(lambda: _work(lead), "The activity log")
    out["assets"] = _section(assets_for(lead.get("id")), measured=True,
                             note="Anything collected for this prospect "
                                  "before they are a client.")
    out["duplicates"] = _caught(lambda: _duplicates(lead), "The lead store")
    out["timeline"] = _timeline(out)
    return out


def _audit_section(lead: dict, domain: str) -> dict:
    if not domain:
        return _section(measured=True, found=False,
                        note="No website on this lead, so there is nothing to "
                             "audit. Add one and the audit can run.")
    from hub import website_audit
    payload = website_audit.audit(domain)
    return _section(measured=bool(payload.get("found")),
                    error=payload.get("error") or "",
                    found=bool(payload.get("found")),
                    note=payload.get("note") or "",
                    audit=payload)


def _timeline(rec: dict) -> dict:
    """One list of what has happened, newest first.

    Assembled from the sections that were actually measured. A source that
    could not be read contributes nothing and is **named**, rather than
    quietly shortening the history — a timeline that is missing the week
    somebody is asking about, with nothing saying so, is worse than no
    timeline.
    """
    events, missing = [], []

    def add(when, kind, text, link=""):
        if when:
            events.append({"when": str(when)[:19].replace("T", " "),
                           "kind": kind, "text": text, "link": link})

    lead = rec.get("lead") or {}
    add(lead.get("first_seen"), "lead",
        f"Came in from {lead.get('source') or 'a form'}"
        + (f" ({lead.get('page')})" if lead.get("page") else ""))
    for row in rec.get("merged_from") or []:
        add(row.get("created"), "lead",
            f"Also came in from {row.get('source') or '?'}"
            + (f" ({row.get('page')})" if row.get("page") else "")
            + " — merged into this record")
    if lead.get("converted_at"):
        add(lead["converted_at"], "client",
            f"Became a client: {lead.get('client') or ''}")

    scans = rec.get("scans") or {}
    if scans.get("measured"):
        for row in scans.get("rows") or []:
            add(row.get("when"), "scan",
                f"Website audited"
                + (f" — scored {row['score']}" if row.get("score") is not None else "")
                + (f" ({row['status']})" if row.get("status") not in ("complete", "") else ""),
                row.get("url") or "")
    else:
        missing.append("the scan history")

    props = rec.get("proposals") or {}
    if props.get("measured"):
        for row in props.get("rows") or []:
            label = " ".join(x for x in [
                "Proposal", row.get("quote_number") or "",
                f"({row.get('title')})" if row.get("title") else "",
                "—", row.get("status") or "sent"] if x)
            add(row.get("date_sent") or row.get("uploaded_at"), "proposal",
                label, row.get("url") or "")
    else:
        missing.append("the proposal store")

    work = rec.get("work") or {}
    if work.get("measured"):
        for row in work.get("rows") or []:
            add(row.get("when"), "work",
                " — ".join(x for x in [row.get("kind"), row.get("source"),
                                       row.get("detail")] if x))
    else:
        missing.append("the activity log")

    for row in (rec.get("assets") or {}).get("rows") or []:
        add(row.get("added"), "asset",
            f"{row.get('label') or row.get('filename')} added", row.get("url") or "")

    suite = rec.get("suite") or {}
    if suite.get("measured"):
        for note in suite.get("notes") or []:
            add(note.get("created"), "note", (note.get("body") or "")[:200])
        for opp in suite.get("opportunities") or []:
            add(opp.get("updated"), "deal",
                f"{opp.get('name') or 'Deal'} — "
                + (opp.get("stage") or "stage not measured")
                + (f" ({opp.get('status')})" if opp.get("status") else ""))
    else:
        missing.append("Smart 1 Suite")

    events.sort(key=lambda e: e["when"], reverse=True)
    return _section(events, measured=True, incomplete=missing,
                    note=("" if not missing else
                          "This history is incomplete: " + ", ".join(missing)
                          + " could not be read, so anything from "
                          + ("that source is" if len(missing) == 1
                             else "those sources is") + " missing from it."))


# ==========================================================================
# Becoming a client
# ==========================================================================

def convert(lead_id: str, client: str, actor: str = "") -> dict:
    """Tie the prospect to the client account they became, assets and all.

    The account itself is created in Knack — that is what billing reads — so
    this refuses a name the registry does not know rather than inventing a
    client the Hub shows and no invoice ever mentions. What it adds over
    `leads.mark_converted` is the carry-across: the assets collected against
    the prospect are re-filed under the client so opening the new record does
    not read as a client nobody has done anything for.
    """
    from hub import clients_registry, leads
    client = str(client or "").strip()
    lead = leads.get(lead_id)
    if lead is None:
        return {"ok": False, "error": "That prospect could not be found."}
    if not client:
        return {"ok": False, "error": "Name the client account this became."}
    known = clients_registry.find_client(client)
    if known is None:
        return {"ok": False,
                "error": f"“{client}” is not in the client registry. Create "
                         f"the account in Knack first — that is what billing "
                         f"reads — then convert this prospect to it."}
    name = str(known.get("name") or client)
    row = leads.mark_converted(lead.get("id"), name, actor=actor)
    if row is None:
        return {"ok": False, "error": "That prospect could not be updated."}

    # The assets move by being *named* against the client, not by being
    # re-uploaded: the bytes are already in storage and a second copy is a
    # second thing to keep in step.
    carried = 0
    try:
        from hub import jsonstore
        rows = _all_assets()
        for asset in rows.get(str(lead.get("id")), []):
            if isinstance(asset, dict) and not asset.get("deleted"):
                asset["client"] = name
                carried += 1
        if carried:
            jsonstore.write_json(_index_path(), rows)
    except Exception:                                       # noqa: BLE001
        carried = -1                       # reported, never silently zero

    _log("converted", lead=lead.get("id"), client=name, actor=actor)
    return {"ok": True, "client": name, "lead": row, "assets_carried": carried,
            "note": (f"Filed against {name}."
                     + (f" {carried} file{'' if carried == 1 else 's'} carried "
                        f"across." if carried > 0 else
                        " The files on this record could not be re-filed, so "
                        "they stay on the prospect record."
                        if carried < 0 else ""))}
