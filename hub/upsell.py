"""What we could sell each client, out of audits we have already paid for.

`hub/website_audit.py` turns one Insites audit into findings that carry their
own evidence — *no retargeting pixel of any kind is on the site*, *their Google
listing is unclaimed*, *still on Universal Analytics*. It fires for a prospect
and for one client at a time on the audit tool, and for nobody else. Several
hundred clients have been audited and nothing has ever read the answer across
the book, so the upsell conversation that data exists to start was being had
from memory or not at all.

This is that read. Every rule in it is a way the report could be confidently
wrong about a client who pays us:

* **The finding leads and the product follows.** "Their Google listing is
  unclaimed — anybody can edit the hours and the phone number" survives being
  read out to the client; "they should buy Local Listings" is what a rep gets
  argued with over. `OPPORTUNITIES` is already that shape and is read as-is.

* **Coverage is the honest half of the report.** A client nobody has audited is
  **not measured**, never a clean bill, and a client whose reading is over
  `STALE_DAYS` old is named as stale rather than counted as current. Without
  that this is a report that gets quieter the worse our coverage gets, which is
  the exact direction a sales report must not fail in.

* **Recorded and observed are different claims, and the disagreement is the
  finding.** The two reports next door — *Clients Without Analytics* and
  *Clients Without GTM* — read what we have **attached**: a property on the
  website record, an account someone linked. This reads what is **on the
  site**. A client can have a property attached and no tag on the page, or a
  tag we have never attached. Folding the two together destroys the only
  evidence of it, so they are carried side by side and the gap is its own row —
  the point `hub/analytics_ids.py` makes about a recorded GA id against an
  observed one.

* **One query per batch, not one per client.** `scan_facts` reads the newest
  audit for one domain; asking it several hundred times is several hundred
  round trips and several hundred JSON blobs held at once. `audits_for()`
  takes the newest complete scan for a chunk of domains in one statement and
  discards each payload as soon as the findings are out of it.

* **Nothing here spends a credit.** Reading is free. Rescanning is a button on
  the row, and it goes to the module that owns scans.

* **A report that could not look says so.** `measured` is False when the scans
  table cannot be read, which is what stops `hub/report_cache.py` freezing
  "we could not look" into the shape of "there is nothing to sell" for the
  rest of the day.
"""
from __future__ import annotations

import json
from typing import Iterable

# Reading one audit at a time is what this module exists not to do, so the
# chunk is generous. Small enough that one statement's parameter list stays
# well inside SQLite's default 999-variable ceiling.
CHUNK = 200


def _chunks(items: list, size: int = CHUNK) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def audits_for(domains: Iterable[str]) -> tuple[dict, str]:
    """`({domain: {...}}, error)` — the newest complete audit of each domain.

    A pair rather than a bare dict, for the reason `connected_accounts_result`
    gives in Google Finder: "nobody has audited these" and "we could not read
    the scans table" are different answers and only the first says anything
    about the clients.

    The payload is parsed, reduced to what the report needs and **thrown
    away**: several hundred 440-field audits held at once is tens of megabytes
    for a table of a dozen columns.
    """
    from hub.client_context import canonical_domain
    from hub import website_audit

    wanted = []
    for d in domains:
        key = canonical_domain(d or "")
        if key and key not in wanted:
            wanted.append(key)
    if not wanted:
        return {}, ""

    try:
        from sqlalchemy import inspect as sa_inspect, text
        from hub.extensions import shared_engine
        engine = shared_engine()
        if not sa_inspect(engine).has_table("scans"):
            return {}, "no scan table yet"
    except Exception as exc:                                # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:300]

    out: dict[str, dict] = {}
    try:
        with engine.connect() as conn:
            for chunk in _chunks(wanted):
                names = {f"d{i}": key for i, key in enumerate(chunk)}
                placeholders = ", ".join(f":{n}" for n in names)
                sql = (
                    "SELECT s.domain_key, s.public_id, s.overall_score, "
                    "s.raw_report, s.completed_at, s.created_at "
                    "FROM scans s JOIN ("
                    "  SELECT domain_key, MAX(id) AS mid FROM scans "
                    f"  WHERE status = 'complete' AND domain_key IN ({placeholders}) "
                    "  GROUP BY domain_key"
                    ") m ON s.id = m.mid")
                for row in conn.execute(text(sql), names).mappings():
                    try:
                        report = json.loads(row.get("raw_report") or "{}")
                    except (TypeError, ValueError):
                        report = {}
                    if not isinstance(report, dict):
                        report = {}
                    when = str(row.get("completed_at")
                               or row.get("created_at") or "")[:19].replace("T", " ")
                    out[row["domain_key"]] = {
                        "public_id": row.get("public_id") or "",
                        "score": row.get("overall_score"),
                        "when": when,
                        "age": website_audit.staleness(when),
                        "findings": website_audit.opportunities(report),
                        "observed": _observed(report),
                    }
                    # The blob goes out of scope here on purpose.
    except Exception as exc:                                # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:300]
    return out, ""


def _observed(report: dict) -> dict:
    """The handful of tri-state facts the recorded/observed comparison needs.

    Tri-state throughout: a plan that does not run a check answers None, and
    None must never be read as "no". That is the difference between telling a
    rep the tag is missing and telling them nobody looked.
    """
    from hub.website_audit import _b, _get                   # noqa: PLC2701
    return {
        "analytics": _b(_get(report, "analytics.has_analytics")),
        "gtm": _b(_get(report, "google_ads_readiness.has_google_tag")),
        "universal_ga": _b(_get(report, "analytics.uses_universal_ga")),
    }


# ==========================================================================
# The report
# ==========================================================================

def build(active_days: int = 60) -> dict:
    """Every active client, what their audit says, and how current it is.

    Returns the QA report shape — columns, rows, note — plus `measured`, which
    is what keeps a failed read out of the day's cache.
    """
    from hub import qa, website_audit
    from hub.client_context import canonical_domain

    try:
        groups = qa._client_groups()                         # noqa: PLC2701
    except Exception as exc:                                 # noqa: BLE001
        return _unmeasured(f"The client list could not be read "
                           f"({type(exc).__name__}: {exc}).")

    clients = []
    for name in sorted(groups, key=str.lower):
        g = groups[name]
        if not qa._active_within(g, active_days):            # noqa: PLC2701
            continue
        try:
            cov = qa._google_coverage(name, g)               # noqa: PLC2701
        except Exception:                                    # noqa: BLE001
            cov = {"has_ga": None, "has_gtm": None, "domain": ""}
        clients.append((name, g, cov))

    if not clients:
        return _unmeasured("No client is running a product, so there is "
                           "nobody to read an audit for. That is a client "
                           "list this report could not use rather than a book "
                           "with nothing to sell.")

    audits, error = audits_for(c[2].get("domain") or "" for c in clients)
    if error:
        return _unmeasured(f"The site audits could not be read ({error}), so "
                           f"what we could sell is not measured — which is "
                           f"not the same as nothing to sell.")

    current, stale, never, no_site = [], [], [], []
    for name, g, cov in clients:
        domain = canonical_domain(cov.get("domain") or "")
        monthly = g.get("live_total") or g.get("this_total") or 0
        base = {"name": name, "g": g, "cov": cov, "domain": domain,
                "monthly": monthly}
        if not domain:
            no_site.append(base)
            continue
        found = audits.get(domain)
        if found is None:
            never.append(base)
            continue
        base.update(found)
        base["disagreements"] = _disagreements(cov, found.get("observed") or {})
        (stale if (found.get("age") or {}).get("stale") else current).append(base)

    current.sort(key=lambda r: (-len(r["findings"]), -float(r["monthly"] or 0),
                                r["name"].lower()))
    stale.sort(key=lambda r: (-len(r["findings"]), -float(r["monthly"] or 0),
                              r["name"].lower()))
    never.sort(key=lambda r: (-float(r["monthly"] or 0), r["name"].lower()))
    no_site.sort(key=lambda r: r["name"].lower())

    rows, styles = [], []

    def band(text, tone, n):
        rows.append([{"group": True, "tone": tone, "text": f"{text} ({n})"},
                     "", "", "", "", ""])
        styles.append(None)

    sellable = [r for r in current if r["findings"] or r["disagreements"]]
    clean = [r for r in current if not (r["findings"] or r["disagreements"])]

    if sellable:
        band("Audited and something to sell", "now", len(sellable))
        for r in sellable:
            rows.append(_row(r))
            styles.append(None)
    if stale:
        band(f"Reading is over {website_audit.STALE_DAYS} days old — "
             f"rescan before quoting", "soon", len(stale))
        for r in stale:
            rows.append(_row(r, stale=True))
            styles.append(None)
    if never:
        band("Never audited — not measured, not a clean bill", "soon", len(never))
        for r in never:
            rows.append(_row(r, never=True))
            styles.append(None)
    if no_site:
        band("No website on file, so nothing to audit", "", len(no_site))
        for r in no_site:
            rows.append(_row(r, no_site=True))
            styles.append(None)
    if clean:
        band("Audited, nothing on our list came back", "", len(clean))
        for r in clean:
            rows.append(_row(r))
            styles.append(None)

    measured_n = len(current) + len(stale)
    return {
        "columns": ["Client", "Website", "What the audit found", "Read",
                    "Monthly", ""],
        "rows": rows,
        "row_styles": styles,
        "measured": True,
        "note": _note(len(clients), measured_n, len(stale), len(never),
                      len(no_site), len(sellable), active_days),
    }


def _row(r: dict, *, stale: bool = False, never: bool = False,
         no_site: bool = False) -> list:
    from hub import qa
    name = r["name"]
    site = ({"href": f"https://{r['domain']}", "text": r["domain"]}
            if r["domain"] else {"muted": True, "text": "none on file"})

    if never:
        found = {"muted": True,
                 "text": "Not measured — nobody has audited this site"}
    elif no_site:
        found = {"muted": True,
                 "text": "Not measured — no website to audit"}
    else:
        parts = [f["finding"] for f in r.get("findings") or []]
        parts += [d["text"] for d in r.get("disagreements") or []]
        found = ({"text": " ".join(parts), "title": _sells(r)}
                 if parts else
                 {"muted": True, "text": "Nothing on our list came back — on a "
                                         "plan that skips a check that is not "
                                         "measured rather than clean"})

    if never or no_site:
        read = {"muted": True, "text": "—"}
    else:
        age = r.get("age") or {}
        read = ({"pill": True, "text": f"{age.get('age_days')}d",
                 "title": age.get("note") or ""}
                if age.get("measured") else
                {"muted": True, "text": "date not measured"})

    actions = {"actions": []}
    if r["domain"]:
        actions["actions"].append(
            {"label": "Audit" if (never or stale) else "Re-audit",
             "action": "upsell_rescan", "client": r["domain"],
             "confirm": (f"Run a fresh audit of {r['domain']}?\n\nThis spends "
                         f"one Insites credit and takes a few minutes.")})
    return [qa._c360_link(name), site, found, read,                # noqa: PLC2701
            qa._money(r["monthly"]), actions]                      # noqa: PLC2701


def _sells(r: dict) -> str:
    products = []
    for f in r.get("findings") or []:
        if f.get("sells") and f["sells"] not in products:
            products.append(f["sells"])
    return ("Points at: " + ", ".join(products)) if products else ""


def _disagreements(recorded: dict, observed: dict) -> list[dict]:
    """Where what we have on file and what is on the site do not agree.

    Never folded into the findings above: those say what the site is missing,
    and these say that our own record and the site contradict each other,
    which sends somebody somewhere different. Both directions are reported —
    a tag on the site we have never attached is a property somebody else may
    be administering, and that is worth knowing before the renewal.

    Tri-state on both sides. `None` observed is a check the plan did not run
    and produces nothing at all, because "we did not look" printed as a
    disagreement is the confident wrong answer this whole report avoids.
    """
    out = []
    # The recorded side is `_google_coverage`'s own spelling -- `has_ga`, not
    # `has_analytics`. Guessing the key reads every client as "no disagreement"
    # and the whole comparison is silently dead, which is what a first pass
    # here did.
    pairs = (("analytics", "a Google Analytics property", "has_ga"),
             ("gtm", "a Tag Manager container", "has_gtm"))
    for key, label, rec_key in pairs:
        seen = observed.get(key)
        if seen is None:
            continue
        have = recorded.get(rec_key)
        if have and seen is False:
            out.append({"key": key, "text":
                        f"We have {label} on file and none is on the site."})
        elif have is False and seen is True:
            out.append({"key": key, "text":
                        f"{label.capitalize()} is on the site and we have "
                        f"none on file — somebody else may be administering "
                        f"it."})
    if observed.get("universal_ga") is True:
        out.append({"key": "universal_ga", "text":
                    "Still on Universal Analytics, which stopped collecting "
                    "in 2023 — whatever they are reading is not this year's "
                    "traffic."})
    return out


def _note(total: int, measured: int, stale: int, never: int, no_site: int,
          sellable: int, active_days: int) -> str:
    bits = [f"{total} clients with a product running in the last "
            f"{active_days} days.",
            f"{measured} have been audited, {sellable} of those with "
            f"something on our list."]
    gaps = []
    if stale:
        gaps.append(f"{stale} whose reading has gone stale")
    if never:
        gaps.append(f"{never} nobody has audited")
    if no_site:
        gaps.append(f"{no_site} with no website on file")
    if gaps:
        bits.append("Not measured: " + ", ".join(gaps)
                    + " — that is coverage we do not have, not clients with "
                      "nothing to sell.")
    bits.append("Every line is what was read off the client's own website. "
                "The finding is what survives being read out to them; the "
                "product it points at is on the tooltip.")
    return " ".join(bits)


def _unmeasured(note: str) -> dict:
    """A report that could not look. `measured: False` keeps it out of the
    day's cache, so connecting the source an hour later is not lost until
    tomorrow."""
    return {"columns": ["Client", "Website", "What the audit found", "Read",
                        "Monthly", ""],
            "rows": [], "row_styles": [], "measured": False, "note": note}
