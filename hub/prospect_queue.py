"""Which prospect to call today, and why.

`hub/prospect.py` gave a scanned business a record worth opening. Nothing said
*which* one to open. The Leads panel is a flat table sorted by date whose five
figures are all about **delivery** — confirmed in Suite, not yet in Suite,
needs attention — and none about whether anybody is working the lead. Every QA
report beside this one is about clients; not one was about a prospect.

That is the failure this codebase has written up twice already: *a stale list
that can only be read is a list nobody works* (`hub/stale_creative.py`), and
*a report nobody thinks to look for is a report nobody works* (the QA
reshuffle). A record nobody is told to open is the same thing one step later.

## What decides the order

The bands are the work, in the order it has to happen — not a score. A ranking
number nobody can reproduce is a ranking nobody trusts, so each row says which
band it is in and why, and the bands run:

1. **Not in the CRM at all.** The lead never reached Smart 1 Suite, so it is
   invisible to every follow-up that lives there. Nothing else about the
   prospect matters until that is fixed.
2. **Two rows, one business.** Working one of a pair wastes the call and files
   the answer against the row nobody opens. Merge first.
3. **Audited and nothing quoted.** The evidence is sitting there and no
   proposal exists. This is the band the whole audit pipeline was built to
   fill.
4. **Never audited.** Came in and nobody has looked at their website. One
   credit turns them into the band above.
5. **Quoted and waiting.** Oldest first, because those are the ones going
   cold.

## What it deliberately does not read

**Smart 1 Suite.** Reading the stage would cost an HTTP call per prospect, and
a report that makes several hundred outbound calls on its first open of the
day is one somebody turns off — the note `services/provider_check.py` makes
about eight calls on a page load, several hundred times over. The queue ranks
on what the Hub already holds for nothing; the prospect record is where the
stage is read, one prospect at a time, and the note says so rather than
leaving somebody to wonder why the pipeline is not on it.

**A converted prospect.** They are a client now and Client 360 is their
record. They are counted in the note rather than listed, because a queue that
silently drops rows cannot be told from one that failed to read them.
"""
from __future__ import annotations

# The window. A queue over two years of leads is not a queue, and a prospect
# nobody has touched in three months is a different conversation from one that
# came in on Friday. Widened by the caller where somebody genuinely wants the
# long tail.
DEFAULT_DAYS = 90


def _norm(name: str) -> str:
    try:
        from hub.client_key import normalise_name
        return normalise_name(name or "")
    except Exception:                                       # noqa: BLE001
        import re
        return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _quoted_clients() -> tuple[set, str]:
    """`(normalised names with a proposal on file, error)`.

    Read across every client in one call rather than per prospect: asking
    `list_proposals` once per lead is a file open per lead on the path of a
    page load, which is the shape `all_proposals` was written to replace.
    """
    try:
        from hub import proposals
        rows = proposals.all_proposals(limit=500) or []
    except Exception as exc:                                # noqa: BLE001
        return set(), f"{type(exc).__name__}: {exc}"[:200]
    return {_norm(r.get("client") or "") for r in rows if r.get("client")}, ""


def _duplicate_ids() -> tuple[set, str]:
    """Ids that appear in a proposed merge group, and why that matters here.

    Only `certain` counts. A name-only resemblance is worth an eyeball and is
    not a reason to stop somebody making a call — putting those in the "merge
    first" band would fill it with pairs nobody should merge.
    """
    try:
        from hub import leads
        found = leads.merge_candidates(days=730)
    except Exception as exc:                                # noqa: BLE001
        return set(), f"{type(exc).__name__}: {exc}"[:200]
    if found.get("error"):
        return set(), found["error"]
    ids = set()
    for group in found.get("certain") or []:
        for row in group.get("leads") or []:
            if row.get("id"):
                ids.add(row["id"])
    return ids, ""


def build(days: int = DEFAULT_DAYS) -> dict:
    """The queue. Returns the QA report shape plus `measured`."""
    from hub import leads, prospect, upsell, website_audit

    try:
        listing = leads.listing(days=days)
        rows_in = listing.get("leads") or []
    except Exception as exc:                                # noqa: BLE001
        return _unmeasured(f"The lead store could not be read "
                           f"({type(exc).__name__}: {exc}), so who to chase is "
                           f"not measured — which is not the same as nobody.")

    converted = [r for r in rows_in if r.get("converted_at")]
    live = [r for r in rows_in if not r.get("converted_at")]

    # `prospect._lead_domain` is the one place a lead's domain is decided --
    # six landing pages and two widgets each name the website differently, and
    # a second copy of that resolution here would disagree with the record
    # page about which site a prospect has.
    domains = {r.get("id"): prospect._lead_domain(r) for r in live}   # noqa: PLC2701
    audits, audit_error = upsell.audits_for(d for d in domains.values() if d)
    quoted, quote_error = _quoted_clients()
    dupes, dupe_error = _duplicate_ids()

    undelivered, merge_first, ready, unaudited, waiting = [], [], [], [], []
    for r in live:
        dom = domains.get(r.get("id")) or ""
        found = audits.get(dom) if dom else None
        item = {
            "lead": r, "domain": dom, "audit": found,
            "findings": (found or {}).get("findings") or [],
            "age_days": _age(r.get("created")),
            "has_quote": _norm(r.get("company") or "") in quoted,
        }
        if not r.get("contact_id") and not r.get("delivered"):
            undelivered.append(item)
        elif r.get("id") in dupes:
            merge_first.append(item)
        elif item["has_quote"]:
            waiting.append(item)
        elif found is not None:
            ready.append(item)
        else:
            unaudited.append(item)

    # Oldest first where the wait is the problem; most to sell first where the
    # question is what to open.
    undelivered.sort(key=lambda i: -(i["age_days"] or 0))
    merge_first.sort(key=lambda i: -(i["age_days"] or 0))
    ready.sort(key=lambda i: (-len(i["findings"]), -(i["age_days"] or 0)))
    unaudited.sort(key=lambda i: -(i["age_days"] or 0))
    waiting.sort(key=lambda i: -(i["age_days"] or 0))

    out_rows, styles = [], []

    def band(text, tone, n):
        out_rows.append([{"group": True, "tone": tone, "text": f"{text} ({n})"},
                         "", "", "", ""])
        styles.append(None)

    bands = [
        (undelivered, "Not in Smart 1 Suite — nothing else reaches them", "now"),
        (merge_first, "Two rows, one business — merge before working them", "now"),
        (ready, "Audited and nothing quoted", "now"),
        (unaudited, "Never audited — one credit turns these into the band above",
         "soon"),
        (waiting, "Quoted and waiting, longest first", ""),
    ]
    for items, label, tone in bands:
        if not items:
            continue
        band(label, tone, len(items))
        for item in items:
            out_rows.append(_row(item, website_audit))
            styles.append(None)

    return {
        "columns": ["Prospect", "Came in", "What we know", "Next step", ""],
        "rows": out_rows,
        "row_styles": styles,
        "measured": True,
        # On the payload rather than recomputed by whoever wants a headline
        # figure. The dashboard reads this run through the day cache, so the
        # tile and the report cannot answer "how many are waiting" differently
        # -- the `/api/db/structure` versus `/api/integrity` trap, where two
        # checks asking one question contradicted each other on one panel.
        "counts": {
            "prospects": len(live),
            "converted": len(converted),
            "undelivered": len(undelivered),
            "merge_first": len(merge_first),
            "ready": len(ready),
            "unaudited": len(unaudited),
            "waiting": len(waiting),
            "days": days,
        },
        "note": _note(len(live), len(converted), len(undelivered),
                      len(merge_first), len(ready), len(unaudited),
                      len(waiting), days,
                      [e for e in (audit_error, quote_error, dupe_error) if e]),
    }


def scoreboard() -> dict:
    """The headline figures, for the dashboard.

    Read from the **cached** report rather than rebuilt: the dashboard loads
    on every visit, and this walk reads the lead store, a batch of audits, the
    proposal store and the merge candidates. `hub/social_status.py` states the
    same rule -- a number that costs a page load is a number somebody turns
    off -- and reading the same run is also what stops the tile and the report
    disagreeing about how many are waiting.

    Never raises. A report that could not be built answers `measured: False`
    with the reason, because "nobody to chase" and "we could not look" are
    different answers and only the first is good news.
    """
    try:
        from hub import qa
        out = qa.run_cached("prospect-queue")
    except Exception as exc:                                # noqa: BLE001
        return {"measured": False,
                "error": f"The prospect queue could not be read "
                         f"({type(exc).__name__}).",
                "counts": {}, "url": URL}
    if not out.get("measured", True):
        return {"measured": False, "error": out.get("note") or "Not measured.",
                "counts": {}, "url": URL}
    counts = out.get("counts") or {}
    return {"measured": True, "error": "", "counts": counts, "url": URL,
            "cache": out.get("cache") or {},
            "line": _headline(counts)}


URL = "/qa/prospect-queue"


def _headline(c: dict) -> str:
    """One sentence under the figures, and every zero says which kind it is.

    "Nobody is waiting to be called" and "no lead has come in for three
    months" render identically as a nought, and only the second is something
    to do about the top of the funnel rather than the middle of it.
    """
    if not c.get("prospects"):
        return (f"No prospect has come in in the last {c.get('days', DEFAULT_DAYS)} "
                f"days — that is the top of the funnel, not the queue.")
    parts = []
    if c.get("ready"):
        parts.append(f"{c['ready']} audited and unquoted")
    if c.get("unaudited"):
        parts.append(f"{c['unaudited']} never audited")
    if c.get("undelivered"):
        parts.append(f"{c['undelivered']} not in Smart 1 Suite")
    if not parts:
        return (f"{c['prospects']} prospects, all of them quoted and waiting — "
                f"nothing needs starting, only chasing.")
    return ", ".join(parts) + " — open the queue for the order to work them in."


def _age(created) -> int | None:
    from datetime import datetime, timezone
    try:
        when = datetime.fromisoformat(str(created or ""))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - when).days)


def _row(item: dict, website_audit) -> list:
    lead = item["lead"]
    who = (lead.get("company") or lead.get("name")
           or lead.get("email") or "this prospect")
    name_cell = {"href": f"/prospect/{lead.get('id')}", "text": who}

    age = item["age_days"]
    came = ({"text": f"{age}d ago", "title": str(lead.get("created") or "")}
            if age is not None else {"muted": True, "text": "date not measured"})

    known = []
    if item["findings"]:
        known.append(f"{len(item['findings'])} findings: "
                     + item["findings"][0]["finding"])
    elif item["audit"] is not None:
        known.append("Audited, nothing on our list came back")
    elif item["domain"]:
        known.append("Never audited")
    else:
        known.append("No website on this lead, so nothing to audit")
    if item["audit"] is not None:
        stale = ((item["audit"].get("age") or {}).get("stale"))
        if stale:
            known.append(f"(reading is over {website_audit.STALE_DAYS} days old)")
    src = f"{lead.get('source') or '?'} / {lead.get('page') or '?'}"
    known_cell = {"text": " ".join(known), "title": f"Came in from {src}"}

    if not lead.get("contact_id") and not lead.get("delivered"):
        step = {"text": "Retry the delivery — this prospect is in no CRM",
                "title": str(lead.get("last_error") or "")}
    elif item["has_quote"]:
        step = {"muted": True, "text": "Chase the proposal"}
    elif item["audit"] is not None:
        step = {"text": "Quote it — the evidence is on the record"}
    elif item["domain"]:
        step = {"text": "Audit the website"}
    else:
        step = {"muted": True, "text": "Find their website first"}

    actions = {"actions": []}
    if item["domain"] and item["audit"] is None:
        actions["actions"].append(
            {"label": "Audit", "action": "upsell_rescan", "client": item["domain"],
             "confirm": (f"Run an audit of {item['domain']}?\n\nThis spends one "
                         f"Insites credit and takes a few minutes.")})
    return [name_cell, came, known_cell, step, actions]


def _n(count: int, singular: str, plural: str = "") -> str:
    """"1 prospect has" / "4 prospects have". This is copy a rep reads, and a
    report that says "1 have been audited" reads as one nobody proof-read."""
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _note(live, converted, undelivered, merge_first, ready, unaudited,
          waiting, days, errors) -> str:
    bits = [_n(live, "prospect") + f" from the last {days} days who "
            + ("has" if live == 1 else "have") + " not become a client."]
    if converted:
        bits.append(_n(converted, "more") + " converted and "
                    + ("is" if converted == 1 else "are")
                    + " on Client 360 rather than here.")
    if ready:
        bits.append(f"{ready} " + ("has" if ready == 1 else "have")
                    + " been audited with nothing quoted — that is the band "
                      "to work first.")
    if unaudited:
        bits.append(f"{unaudited} " + ("has" if unaudited == 1 else "have")
                    + " never been audited, which is a credit each and not a "
                      "verdict on them.")
    if undelivered:
        bits.append(f"{undelivered} never reached Smart 1 Suite, so no "
                    f"follow-up that lives there can see "
                    + ("them." if undelivered != 1 else "it."))
    if merge_first:
        bits.append(_n(merge_first, "row") + " "
                    + ("is" if merge_first == 1 else "are")
                    + " the same business arriving twice.")
    bits.append("The pipeline stage is deliberately not on this page: reading "
                "it costs one call to Smart 1 Suite per prospect. Open a "
                "prospect and the record reads it for that one.")
    if errors:
        bits.append("Not measured: " + "; ".join(errors)
                    + " — some rows may be in the wrong band because of it.")
    return " ".join(bits)


def _unmeasured(note: str) -> dict:
    return {"columns": ["Prospect", "Came in", "What we know", "Next step", ""],
            "rows": [], "row_styles": [], "measured": False, "note": note}
