"""A scanned business that is not a client becomes a lead.

Running an audit on a website is this Hub saying "somebody thinks this
business is worth looking at". Until this file that thought stopped at the
scans table. A staff scan wrote a row with a domain, a score and a tier, and
nothing else in the Hub ever heard about it:

* the leads panel did not have it, so it was not in the one place that
  answers "who came in, and from where";
* `hub/prospect_queue.py` could not rank it -- and its band 4, *never
  audited*, is the exact mirror image of this gap: a lead with no audit was
  worked, an audit with no lead was not;
* `hub/prospect.py` exists to be "the record a scanned business gets before
  it is a client" and hangs off a **lead id**, so a scanned business with no
  lead had no record to open at all; and
* `_tag_lead_temperature()` in `app.py` has scored every completed audit
  hot/warm/cold since WO-3d and then written it onto `Scan.lead_id`, which
  was a column no staff scan ever filled in.

The widget half was always wired -- a stranger who runs the scan on a
client's own website hands over a name, an email and a phone, and
`app._capture_lead()` files it. The half a rep runs from `/scans`, which is
most of them, was not.

## The rules, and what each one is a way of getting wrong

* **A client is not a lead.** That is the whole of what was asked for, and
  `modules/reports` already wrote the reason down at its own Suite write: a
  client we bill is not a lead, and a row in the leads panel would say
  otherwise. The check is `hub/client_key.resolve()`, which is the one place
  in this Hub that decides whether a name and a domain are somebody we
  already have.

* **"We could not look" is not "they are not a client."** An empty or
  unreadable client registry would otherwise file every client in the
  business as a fresh prospect, in one sweep, into the CRM. So the index is
  read for its own `error` first, a registry that answers with **no clients
  at all** is treated as not having answered, and the scan is left undecided
  with the reason on it. Undecided is revisited: a later refresh of the same
  scan calls this again.

* **One business, one row.** A second lead for a website already in the
  panel is precisely the duplicate `hub/leads.merge_candidates()` exists to
  find, and band 2 of the prospect queue -- *two rows, one business* -- is
  somebody's morning spent working the wrong one. An existing live lead for
  this domain is linked to, never re-filed.

* **A lead nobody can contact is not filed.** The rule
  `hub/website_audit_routes.py` and `modules/ads_builder` both arrived at:
  a contactless lead reads as a live prospect on every count that follows
  it, and -- because `hub/ghl_contacts.upsert()` has nothing to match a
  contact on -- it can never leave the "needs attention" pile either. The
  audit usually detects a phone number, which is a contact point; where it
  detects none the scan says so and a rep can file it by hand with one they
  have, through `POST /scans/api/scans/<id>/lead`.

* **Never raises, and never blocks the scan.** A completed audit is the
  transaction that matters here. Every failure leaves the scan complete and
  the reason recorded, the same way `_seed_brand_review()` and
  `_resolve_industry_from_scan()` do either side of it.

* **The verdict is written down, not inferred.** `Scan.lead_state` and
  `Scan.lead_note` say which of the answers below this scan got and why, on
  the scan itself, because "no lead" reads identically whether it was
  decided or dropped.

## Delivery

`capture_and_deliver`, like every other prospect source -- not `capture()`
alone. There is no third state available: `hub/leads.retry_undelivered()`
sweeps *every* undelivered row on a schedule regardless of its source, so a
capture-only lead reaches Smart 1 Suite within the hour anyway. Filing it
quietly and letting the sweep push it would only mean the write happened
somewhere nobody was looking.
"""
from __future__ import annotations

# The states a scan's lead question can be in. One string per answer, so a
# screen renders the verdict rather than guessing it from an empty lead id.
STATES = {
    "filed":      "Filed as a lead",
    "linked":     "Already a lead",
    "client":     "A client, so not a lead",
    "widget":     "Filed by the widget",
    "no_contact": "No contact details to file a lead with",
    "undecided":  "Not decided yet",
    "off":        "The lead store is not available here",
}

SOURCE = "site_scan"          # hub/lead_tags.py holds the vocabulary


def _clean(value, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _hub_leads():
    """`hub.leads`, or None where this module is running standalone."""
    try:
        from hub import leads
        return leads
    except Exception:                                   # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Is this already a client?
# ---------------------------------------------------------------------------

def customer(name: str = "", domain: str = "") -> dict:
    """`{"measured", "known", "client", "why"}` — is this business on file?

    `measured` is the difference between "not a client" and "we could not
    look", which is the difference between filing one lead and filing the
    whole client list as leads. It is never inferred from an empty answer.
    """
    out = {"measured": False, "known": False, "client": "", "why": ""}
    try:
        from hub import client_key
        index = client_key.alias_index()
    except Exception as exc:                            # noqa: BLE001
        out["why"] = (f"The client list could not be read "
                      f"({type(exc).__name__}), so whether this is a client "
                      f"is not known.")
        return out
    if index.get("error"):
        out["why"] = (f"The client list could not be read ({index['error']}), "
                      f"so whether this is a client is not known.")
        return out
    if not index.get("clients"):
        # An index that answered with nothing in it is not an answer about
        # this business. Reading it as one would file every client in the
        # business as a fresh prospect the first time the registry blinked.
        out["why"] = ("The client list came back with no clients in it at "
                      "all, so it was not used to decide this.")
        return out

    match = client_key.resolve(name=name or "", url=domain or "", index=index)
    out["measured"] = True
    out["known"] = bool(match.get("known"))
    out["client"] = match.get("client") or ""
    if out["known"]:
        out["why"] = (f"{out['client']} is a client on file "
                      f"({match.get('why') or 'matched the client list'}).")
    else:
        out["why"] = "No client on file matches this website or business name."
    return out


# ---------------------------------------------------------------------------
# What the lead carries
# ---------------------------------------------------------------------------

def contact_from(scan, typed: dict | None = None) -> dict:
    """The contact this lead would be filed on.

    Anything typed by the rep wins over what the crawler read off the site.
    `requested_by` is deliberately not read: on a staff scan that is the
    member of staff, and filing the agency's own people as prospects is how
    a CRM stops being one.
    """
    typed = typed or {}
    return {
        "name": _clean(typed.get("name"), 120),
        "email": _clean(typed.get("email"), 160),
        "phone": _clean(typed.get("phone") or getattr(scan, "detected_phone", ""), 40),
        "company": _clean(typed.get("company")
                          or getattr(scan, "business_name", "")
                          or getattr(scan, "detected_name", ""), 160),
    }


def lead_fields(scan, report: dict | None, contact: dict) -> dict:
    """Flat strings only.

    `hub/leads.py` cleans and truncates every value, so a nested one arrives
    in Smart 1 Suite as the repr of a dict — the note `_capture_lead()` in
    `app.py` carries. What goes on is what somebody picking this up would
    want in front of them; the audit itself is one link away.
    """
    fields = {
        "name": contact["name"],
        "email": contact["email"],
        "phone": contact["phone"],
        "company": contact["company"],
        "website": _clean(getattr(scan, "domain_key", ""), 300),
    }
    if getattr(scan, "overall_score", None) is not None:
        fields["audit_score"] = str(scan.overall_score)
    if getattr(scan, "tier", ""):
        fields["audit_tier"] = _clean(scan.tier, 40)
    if getattr(scan, "primary_industry", ""):
        fields["industry"] = _clean(scan.primary_industry, 120)
    if getattr(scan, "detected_address", ""):
        fields["address"] = _clean(scan.detected_address, 300)
    issues = top_issues(report)
    if issues:
        fields["top_issues"] = "; ".join(issues)
    return fields


def top_issues(report: dict | None, limit: int = 3) -> list:
    """The worst findings, in the words the detail page uses for them.

    Read through `site_health.fixes()` rather than out of the raw payload, so
    a rep reading the lead in the CRM and a rep reading the scan in the Hub
    are told the same thing about the same audit.
    """
    if not isinstance(report, dict) or not report:
        return []
    try:
        from . import site_health
        items = site_health.fixes(report).get("items") or []
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for item in items:
        if item.get("severity") != "bad" or not item.get("measured"):
            continue
        label = _clean(item.get("label"), 80)
        count = item.get("count")
        out.append(f"{label} ({count})" if count else label)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def ensure(scan, report: dict | None = None, typed: dict | None = None,
           page: str = "") -> dict:
    """File this scan's business as a lead, or record why it was not.

    Returns `{"state", "lead_id", "note", "filed"}` and writes the same three
    onto the scan row. The caller commits: this is called from inside
    `_apply_report()`, which fills a row its own caller then commits, and a
    commit here would write half a scan.

    Idempotent. A scan that already carries a lead id is left alone, so the
    refresh button, a late callback and the scheduler's sweep over stuck rows
    can all call this on the same scan without filing it twice.
    """
    state, lead_id, note = "", "", ""

    existing_id = _clean(getattr(scan, "lead_id", ""), 32)
    if existing_id:
        return _record(scan, getattr(scan, "lead_state", "") or "filed",
                       existing_id, getattr(scan, "lead_note", "") or "")

    # The widget files its own lead, with the contact details the visitor
    # actually typed, before the audit is even started. A second one here
    # would be the same business twice from one scan.
    if (getattr(scan, "source", "") or "") == "widget":
        return _record(scan, "widget", "",
                       "This scan came from a scan widget, which files the "
                       "visitor's own details as the lead.")

    leads = _hub_leads()
    if leads is None:
        return _record(scan, "off", "",
                       "The shared lead store is not available in this "
                       "deployment, so nothing was filed.")

    domain = _clean(getattr(scan, "domain_key", ""), 255)
    name = _clean(getattr(scan, "business_name", "")
                  or getattr(scan, "detected_name", ""), 300)

    verdict = customer(name=name, domain=domain)
    if not verdict["measured"]:
        return _record(scan, "undecided", "", verdict["why"] +
                       " Nothing was filed; the next refresh of this scan "
                       "asks again.")
    if verdict["known"]:
        return _record(scan, "client", "", verdict["why"] +
                       " Clients are not leads, so none was filed.")

    # One business, one row. A store that cannot be read is not evidence that
    # there is no lead for this site, so it stops here rather than filing a
    # possible duplicate.
    try:
        already = leads.find_by_domain(domain) if domain else None
    except Exception as exc:                            # noqa: BLE001
        return _record(scan, "undecided", "",
                       f"The lead store could not be read "
                       f"({type(exc).__name__}), so nothing was filed rather "
                       f"than risking a second row for this website.")
    if already:
        return _record(scan, "linked", _clean(already.get("id"), 32),
                       "This website was already a lead, so the scan is "
                       "filed against the row that exists rather than a "
                       "second one.")

    contact = contact_from(scan, typed)
    if not (contact["email"] or contact["phone"]):
        return _record(scan, "no_contact", "",
                       "No email address or phone number was found on the "
                       "site, and a lead nobody can contact reads as a live "
                       "prospect on every report that counts it. Add one "
                       "below to file it.")

    try:
        result = leads.capture_and_deliver(
            source=SOURCE,
            page=_clean(page or f"Site scan — {domain or 'website'}", 160),
            fields=lead_fields(scan, report, contact),
            meta={"domain": domain,
                  "scan_public_id": _clean(getattr(scan, "public_id", ""), 40),
                  "scan_source": _clean(getattr(scan, "source", ""), 30),
                  "audit_score": ("" if getattr(scan, "overall_score", None) is None
                                  else str(scan.overall_score)),
                  "report_url": f"/scans/scan/{_clean(getattr(scan, 'public_id', ''), 40)}",
                  "requested_by": _clean(getattr(scan, "requested_by", ""), 160)})
    except Exception as exc:                            # noqa: BLE001
        # hub.leads promises never to raise. If it somehow does, the scan is
        # still complete and the reason is on it to be tried again.
        return _record(scan, "undecided", "",
                       f"The lead could not be filed "
                       f"({type(exc).__name__}: {exc}). The scan is complete; "
                       f"the next refresh tries again.")

    lead_id = _clean(result.get("lead_id"), 32)
    if not lead_id:
        return _record(scan, "undecided", "",
                       "The lead store returned no id, so there is nothing to "
                       "point at. The next refresh tries again.")
    note = ("Filed as a lead. " +
            ("Created in Smart 1 Suite." if result.get("delivered")
             else "Saved here; delivery to Smart 1 Suite is queued."))
    return _record(scan, "filed", lead_id, note)


def _record(scan, state: str, lead_id: str, note: str) -> dict:
    """Write the verdict onto the scan and hand it back to the caller."""
    try:
        scan.lead_id = lead_id or ""
        scan.lead_state = state
        scan.lead_note = _clean(note, 400)
    except Exception:                                   # noqa: BLE001
        pass            # a row without the columns yet still gets an answer
    return {"state": state, "lead_id": lead_id, "note": _clean(note, 400),
            "filed": state == "filed",
            "label": STATES.get(state, state)}
