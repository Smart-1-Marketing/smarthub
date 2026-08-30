"""The QuickBooks side of the domain renewal calendar.

Knack knows which domains we bought and when each one renews
(`hub/domain_purchase.py`). QuickBooks knows which of those we actually
invoiced, because every one of them is a line on an invoice carrying the
product **Website Domain Renewal**. Nothing joined the two, so "did we bill
this renewal?" was a tick somebody remembered to make, and "we billed a
renewal for a domain that is in no record here" was a question nobody could
ask at all.

This module reads the QuickBooks side and matches it up. Everything in it is
built around one awkward fact: **the client is not the customer**. A domain
renewal is invoiced to the media partner — one invoice to The Montana Radio
Group carries five renewals for five different businesses — so the only place
the client appears is the free-text line description, typed by a person, in
whatever shape that day suggested:

    syrons-market.com/<TAB>Syrons
    Foreman Mechanical Services, LLC - foremanmechanical.com
    www.topsdigitalmarketing.com<TAB>TOPS Marketing
    http://friendsofbridges.org/ - Annual renewal
    Brett Thoft Foundation -  brettthoftfoundation.com

So the rules:

**The domain in the description is the join key, never the name.** It is the
one thing in that string that identifies a business exactly, and it is present
in almost every one of them. `hub/client_context.canonical_domain()` decides
what it means, the same as everywhere else in this Hub — `www.`, a scheme and
a trailing slash are noise, and `.org/` and `.org` are one domain.

**A name matches exactly or not at all, and a near match is a suggestion.**
The rule `hub/client_key.py` gives at length. A charge attributed to the wrong
client's domain record is worse than one attributed to nobody: it marks a
renewal billed that was not, and hides a real one from the year-end
reconciliation. So a near name is offered with its score, `confidence` says
"probable", and it is not treated as fact until a person confirms it — which
writes into the links overlay below and outranks everything.

**"Annual renewal" is not a name.** The remainder after the domain is often a
label rather than a business — the same rule `hub/site_names.py` applies to
"Main Site". A label-only remainder is dropped rather than matched loosely,
because a fuzzy pass over "Annual renewal" eventually finds somebody.

**The item is matched on its normalised name, never a substring.** This
company files the service as `Website Hosting:Website Domain Renewal`. A
report asking for "Website Domain Renewal" — which is what it is called on the
invoice and what anybody would type — must not come back empty because of a
parent nobody knew about, and must not match "Website Domain Renewal - Annual"
either, which is a different product at a different price.
`quickbooks.line_item_matches()` is the shared rule the Sites Billing report
uses. `QB_DOMAIN_RENEWAL_ITEM` and `QB_DOMAIN_RENEWAL_ITEM_ID` override the
name, so a renamed product is one environment variable rather than a hunt.

**The cache is rebuildable and says so.** A year of invoices is a large read,
so the extracted lines are cached — `durable=False`, and what rebuilds it is
the next refresh from QuickBooks, which is one button. Nothing here is a
record of anything; QuickBooks is.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

from hub import jsonstore

# The product as QuickBooks files it. The leaf is what is matched, so either
# spelling of the full path works.
#
# Read at **call time**, not at import. A module constant assigned from
# `os.environ` at import is the Commercial Builder's trap: the variable is set
# on Render, nothing changes, and every screen still looks healthy because the
# value it captured is a plausible default. It also makes the override
# untestable without reloading the module.
ITEM_NAME_DEFAULT = "Website Domain Renewal"


def item_name() -> str:
    return (os.environ.get("QB_DOMAIN_RENEWAL_ITEM") or "").strip() \
        or ITEM_NAME_DEFAULT


def item_id() -> str:
    return (os.environ.get("QB_DOMAIN_RENEWAL_ITEM_ID") or "").strip()

CACHE_SECONDS = 6 * 3600

# How far a charge may sit from the renewal billing date and still be that
# renewal. Invoices for a month's renewals go out on one day near the end of
# it, and the Knack date is when billing was *due*, so they rarely land on the
# same day. Wider than a month so a late invoice still counts; far short of a
# year, so last year's charge can never be read as this year's.
WINDOW_DAYS = 45

# A remainder that identifies nobody. Matching one of these loosely finds a
# client eventually, and the client it finds is not the one on the invoice.
_LABEL_ONLY = {
    "annual renewal", "annual", "renewal", "renewals", "renew", "renewed",
    "domain", "domains", "domain renewal", "domain name", "domain name renewal",
    "website", "website domain", "website domain renewal", "web",
    "hosting", "annual domain renewal", "yearly renewal", "yearly",
    "renewal fee", "domain fee", "fee", "na", "n a", "tbd",
}

_SEPARATORS = " \t\r\n-–—,:;/|·•"


# ---------------------------------------------------------------------------
# Reading the description
# ---------------------------------------------------------------------------
def parse_description(text: str) -> dict:
    """The domain and the business name out of one line description.

    The domain comes from `hub/client_urls.domains_in()` — the shared reader
    the Sites Billing report uses, which already refuses an email address, a
    file extension read as a top-level domain, a file host and a platform
    domain. A second copy here would be a second set of those judgements, and
    the two reports would eventually disagree about the same string.

    The name is what is left once every URL-shaped span is taken out. Nothing
    is invented: a description with no domain reports no domain rather than
    guessing one from the name, and a remainder that is only a label reports
    no name.
    """
    from hub.client_urls import domains_in, strip_domains
    raw = str(text or "")
    flat = raw.replace(" ", " ")
    found = domains_in(flat)
    domain = found[0] if found else ""

    name = ""
    # Split on a tab, a newline, a pipe — and on a *spaced* dash, which is how
    # half of these separate the business from the rest. Spaced on purpose:
    # "Syrons-Market" is one word and cutting it in half loses the business.
    for chunk in re.split(r"[\t\r\n|]+|\s+[-\u2013\u2014]+\s+",
                          strip_domains(flat)):
        cleaned = re.sub(r"\s+", " ", chunk.strip(_SEPARATORS)).strip()
        cleaned = cleaned.strip(_SEPARATORS).strip()
        if not cleaned or _is_label(cleaned):
            continue
        # A remainder that is only digits or a date is a reference, not a name.
        if not re.search(r"[a-z]{2}", cleaned, re.I):
            continue
        if len(cleaned) > len(name):
            name = cleaned
    return {"raw": raw.strip(), "domain": domain, "name": name}


def _is_label(text: str) -> bool:
    """Is this remainder a label rather than a business?

    Matched after the year and the filler are taken out, so "Annual renewal
    for 2026" is refused as firmly as "Annual renewal" — a label with a date
    on it identifies exactly as few businesses as the label alone.
    """
    s = str(text or "").lower()
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s)
    s = re.sub(r"[^a-z ]+", " ", s)
    s = " ".join(w for w in s.split() if w not in {"for", "the", "of", "and"})
    return not s or s in _LABEL_ONLY


def _norm(name: str) -> str:
    from hub.client_key import normalise_name
    return normalise_name(name)


def _charge(ln: dict) -> dict:
    """One line from the shared reader, in the shape this report reads.

    `item` there is `item_name` here and the rest is passed through. A thin
    rename rather than a second reader: what this report needs that the other
    does not is the *line id*, which is now carried by the shared one because
    a confirmed match has to point at one line of an invoice that holds five.
    """
    return {"invoice_id": str(ln.get("invoice_id") or ""),
            "line_id": str(ln.get("line_id") or ""),
            "doc_number": ln.get("doc_number") or "",
            "date": ln.get("date") or "",
            "customer": ln.get("customer") or "",
            "customer_id": ln.get("customer_id") or "",
            "description": str(ln.get("description") or ""),
            "amount": float(ln.get("amount") or 0),
            "item_id": str(ln.get("item_id") or ""),
            "item_name": ln.get("item") or "",
            "link": ln.get("link") or ""}


def charge_key(line: dict) -> str:
    """One charge's stable identity: the invoice and the line inside it."""
    return f"{line.get('invoice_id', '')}:{line.get('line_id', '')}"


# ---------------------------------------------------------------------------
# The confirmed-match overlay
# ---------------------------------------------------------------------------
#
# A person confirming "this charge is that domain" is the only fact in this
# module. It outranks every rule below it and survives a re-read of
# QuickBooks, so a name matched by hand once is not asked about every month.
def _links_path() -> str:
    return os.path.join(jsonstore.data_dir("domains"), "qb_renewal_links.json")


def links_store() -> dict:
    rows = jsonstore.read_json(_links_path(), default={})
    return rows if isinstance(rows, dict) else {}


def link_charge(key: str, record_id: str, *, actor: str = "",
                domain: str = "") -> dict:
    """Attach one QuickBooks charge to one website record, or clear it.

    An empty `record_id` clears the link rather than storing a blank one — a
    stored empty match would read as "somebody looked at this and it belongs
    to nobody", which is a different claim from "nobody has looked".
    """
    k = str(key or "").strip()
    if not k or ":" not in k:
        return {"ok": False, "error": "No charge to attach."}
    rows = links_store()
    rid = str(record_id or "").strip()
    if rid:
        rows[k] = {"record_id": rid, "domain": str(domain or "")[:200],
                   "by": str(actor or "")[:120],
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        rows.pop(k, None)
    jsonstore.write_json(_links_path(), rows, indent=1)
    return {"ok": True, "key": k, "record_id": rid, "row": rows.get(k)}


# ---------------------------------------------------------------------------
# Reading QuickBooks
# ---------------------------------------------------------------------------
def _cache_path(year: int) -> str:
    return os.path.join(jsonstore.data_dir("domains"), f"qb_renewals_{year}.json")


def charges(year: int | None = None, *, refresh: bool = False,
            ttl: int = CACHE_SECONDS) -> dict:
    """Every Website Domain Renewal line QuickBooks holds for one year.

    Never raises, and never reports an empty year as a fact it cannot support:
    `error` carries why the read failed and every caller prints it rather than
    drawing a clean nothing. "QuickBooks says we billed no renewals" and "we
    could not ask QuickBooks" are different answers.
    """
    yr = int(year or date.today().year)
    path = _cache_path(yr)
    cached = jsonstore.read_json(path, default=None)
    if (not refresh and isinstance(cached, dict) and cached.get("lines") is not None
            and time.time() - float(cached.get("at") or 0) < ttl):
        return {**_shape(cached), "cached": True}

    from hub import quickbooks
    if not quickbooks.configured():
        return _shape({"lines": (cached or {}).get("lines") or [], "at": 0,
                       "error": "QuickBooks is not configured on this "
                                "deployment (QB_CLIENT_ID / QB_CLIENT_SECRET), "
                                "so nothing was read."})
    if not quickbooks.connected():
        return _shape({"lines": (cached or {}).get("lines") or [], "at": 0,
                       "error": "QuickBooks is not connected. Connect it from "
                                "System Status; nothing was read."})
    try:
        # `invoice_lines_since()` is the Sites Billing report's reader, given
        # an end date. QuickBooks' query language cannot filter on a line, so
        # both reports pull the period whole and sift; two readers doing that
        # is two descriptions of one operation, and the second one to be
        # written is always the one that misses a field.
        want = item_name()
        wid = item_id()
        lines = [
            _charge(ln) for ln in quickbooks.invoice_lines_since(
                f"{yr:04d}-01-01", f"{yr:04d}-12-31")
            if quickbooks.line_item_matches(ln.get("item"), ln.get("item_id"),
                                            item_name=want, item_id=wid)
        ]
    except Exception as exc:                            # noqa: BLE001
        # The stale cache is still shown, labelled with its own age, because a
        # last-known list beats a blank page — but the error travels with it.
        return _shape({"lines": (cached or {}).get("lines") or [],
                       "at": (cached or {}).get("at") or 0,
                       "error": f"QuickBooks could not be read "
                                f"({type(exc).__name__}: {exc})."})

    payload = {"lines": lines, "at": time.time(), "error": "", "year": yr}
    # durable=False: this is a copy of QuickBooks and nothing else. What
    # rebuilds it is the next refresh, which is one button on /tools/domains.
    jsonstore.write_json(path, payload, durable=False, indent=1)
    return _shape(payload)


def _shape(payload: dict) -> dict:
    at = float(payload.get("at") or 0)
    return {
        "lines": payload.get("lines") or [],
        "error": payload.get("error") or "",
        "fetched_at": (datetime.fromtimestamp(at, tz=timezone.utc)
                       .isoformat(timespec="seconds") if at else ""),
        "age_hours": round((time.time() - at) / 3600, 1) if at else None,
        "cached": False,
        "item": item_name(),
    }


# ---------------------------------------------------------------------------
# Matching a charge to a website record
# ---------------------------------------------------------------------------
def _index(rows: list[dict]) -> dict:
    by_domain: dict = {}
    by_name: dict = {}
    for r in rows or []:
        d = str(r.get("domain") or "").strip().lower()
        if d:
            by_domain.setdefault(d, []).append(r)
        for nm in (r.get("client_name"), r.get("organization"), r.get("client")):
            k = _norm(nm or "")
            if k:
                hits = by_name.setdefault(k, [])
                if r not in hits:
                    hits.append(r)
    return {"by_domain": by_domain, "by_name": by_name}


def _pick(rows: list[dict]) -> tuple[dict, str]:
    """One record out of several on the same domain, and what to say about it."""
    if len(rows) == 1:
        return rows[0], ""
    from hub.domain_purchase import is_ours
    ours = [r for r in rows if is_ours(r)]
    if len(ours) == 1:
        return ours[0], (f"{len(rows)} website records carry this domain; the "
                         "one flagged as bought by us was used.")
    return rows[0], (f"{len(rows)} website records carry this domain — they are "
                     "one company with several records, so anything totalled "
                     "per client should total across all of them.")


def match_charges(lines: list[dict], rows: list[dict],
                  links: dict | None = None,
                  readings: dict | None = None) -> list[dict]:
    """Join each QuickBooks charge to the website record it is for.

    `matched_on` is the evidence — "linked" (a person said so), "domain",
    "name", "name~" (a near name, a suggestion), "read" / "read~" (a business
    a model read out of the description, once every rule above had failed) or
    "" — and `confidence` is "confirmed", "exact", "probable" or "unmatched".
    A caller showing one of these to a person shows the confidence with it.

    `readings` is `hub/invoice_names.readings()`, read once by the caller: this
    walks every charge in the year, and a lookup per row is a file read per row
    with a database restore behind each miss. Absent, nothing is read and the
    behaviour is exactly what it was.
    """
    idx = _index(rows)
    links = links_store() if links is None else links
    readings = {} if readings is None else readings
    by_id = {str(r.get("id") or ""): r for r in rows or []}
    out = []
    for line in lines or []:
        parsed = parse_description(line.get("description"))
        row = {**line, "key": charge_key(line), "parsed": parsed,
               "domain": parsed["domain"], "described_name": parsed["name"],
               "record_id": "", "client": "", "partner": "",
               "record_domain": "", "matched_on": "", "confidence": "unmatched",
               "candidates": [], "why": "", "is_ours": None,
               # What a model read out of the description, where one of the
               # rules above did not answer. Empty on every row the rules
               # settled, which is most of them.
               "read_name": ""}

        hit = None
        pinned = links.get(row["key"]) or {}
        if pinned.get("record_id") and pinned["record_id"] in by_id:
            hit = by_id[pinned["record_id"]]
            row.update(matched_on="linked", confidence="confirmed",
                       why=f"Matched by {pinned.get('by') or 'someone'}"
                           + (f" on {str(pinned.get('at'))[:10]}"
                              if pinned.get("at") else "") + ".")
        elif pinned.get("record_id"):
            row["why"] = ("This charge was matched to a website record that is "
                          "no longer in the registry, so it is unmatched again "
                          "rather than pointing at nothing.")

        if hit is None and parsed["domain"]:
            same = idx["by_domain"].get(parsed["domain"]) or []
            if same:
                hit, note = _pick(same)
                row.update(matched_on="domain", confidence="exact",
                           why=("The description carries this domain. " + note).strip())

        if hit is None and parsed["name"]:
            key = _norm(parsed["name"])
            same = idx["by_name"].get(key) or []
            if same:
                hit, note = _pick(same)
                row.update(matched_on="name", confidence="exact",
                           why=("The client named in the description matches "
                                "this record's client exactly. " + note).strip())

        if hit is None and parsed["name"]:
            near = _near(parsed["name"], idx["by_name"])
            if len(near) == 1:
                hit = near[0]["row"]
                row.update(matched_on="name~", confidence="probable",
                           why=f"“{parsed['name']}” looks like "
                               f"“{near[0]['name']}” ({near[0]['pct']}%). A "
                               f"suggestion — confirm it before it counts as "
                               f"billed.")
            elif len(near) > 1:
                row["candidates"] = [n["name"] for n in near][:6]
                row["why"] = (f"{len(near)} clients could be "
                              f"“{parsed['name']}”. Reporting no match rather "
                              f"than guessing — a wrong match marks somebody "
                              f"else's renewal billed.")

        # Last, and only where every rule above has failed: the business a
        # model read out of the description. It resolves through the same two
        # passes the parsed name does — exact on the normalized form, then a
        # single near match — so `client_key`'s rules still decide and the
        # model has never seen the registry.
        #
        # It can never be better than `probable`, whatever it matched on. A
        # parsed name is what the description says; a read name is what a model
        # thought the description meant, and `domain_purchase.year_to_date()`
        # counts a probable charge as having no record here, in both
        # directions, until somebody presses link_charge(). That is the whole
        # reason this is safe to add to a report about money.
        if hit is None and readings:
            read_name = ""
            try:
                from hub import invoice_names
                read_name = invoice_names.business_in(
                    line.get("description") or "", readings)
            except Exception:                           # noqa: BLE001
                read_name = ""
            if read_name and _norm(read_name) != _norm(parsed["name"] or ""):
                same = idx["by_name"].get(_norm(read_name)) or []
                if same:
                    hit, note = _pick(same)
                    row.update(matched_on="read", confidence="probable",
                               read_name=read_name,
                               why=(f"Nothing in the description matched. Read "
                                    f"as “{read_name}”, which is this record's "
                                    f"client exactly — a suggestion, not a "
                                    f"match. Confirm it before it counts as "
                                    f"billed. " + note).strip())
                else:
                    near = _near(read_name, idx["by_name"])
                    if len(near) == 1:
                        hit = near[0]["row"]
                        row.update(matched_on="read~", confidence="probable",
                                   read_name=read_name,
                                   why=(f"Nothing in the description matched. "
                                        f"Read as “{read_name}”, which looks "
                                        f"like “{near[0]['name']}” "
                                        f"({near[0]['pct']}%). A suggestion — "
                                        f"confirm it before it counts as "
                                        f"billed."))
                    elif len(near) > 1:
                        row["candidates"] = [n["name"] for n in near][:6]
                        row["read_name"] = read_name
                        row["why"] = (f"Read as “{read_name}”, and "
                                      f"{len(near)} clients could be that. "
                                      f"Reporting no match rather than "
                                      f"guessing.")

        if hit is not None:
            from hub.domain_purchase import is_ours
            row.update(record_id=str(hit.get("id") or ""),
                       client=hit.get("client") or "",
                       partner=hit.get("media_partner") or "",
                       record_domain=hit.get("domain") or "",
                       is_ours=is_ours(hit))
        elif not row["why"]:
            row["why"] = ("Nothing in the website registry carries this domain "
                          "or this client name."
                          if parsed["domain"] or parsed["name"] else
                          "The description names neither a domain nor a "
                          "business, so there is nothing to match on.")
        out.append(row)
    return out


def _near(name: str, by_name: dict, threshold: float = 0.86) -> list[dict]:
    """Registry names that resemble this one. Suggestions, never facts."""
    key = _norm(name)
    if not key or len(key) < 4:
        return []
    best: dict = {}
    for other, hits in by_name.items():
        if other == key:
            continue
        score = SequenceMatcher(None, key, other).ratio()
        if score < threshold:
            continue
        for r in hits:
            rid = str(r.get("id") or "")
            prev = best.get(rid)
            if prev and prev["score"] >= score:
                continue
            best[rid] = {"row": r, "name": r.get("client") or other,
                         "score": score, "pct": int(score * 100)}
    return sorted(best.values(), key=lambda x: -x["score"])[:6]


def by_record(matched: list[dict]) -> dict:
    """{record_id: [charges]} for everything that matched a record."""
    out: dict = {}
    for c in matched or []:
        rid = c.get("record_id")
        if rid:
            out.setdefault(rid, []).append(c)
    return out


def within_window(charge_date: str, renewal: date | None,
                  days: int = WINDOW_DAYS) -> bool:
    """Is this charge the one that billed that renewal?

    A domain renews every year and is charged once. Without a window, last
    year's invoice would mark this year's renewal billed — the same confident
    wrong answer the billed tick was rebuilt to avoid.
    """
    if not renewal:
        return False
    try:
        d = date.fromisoformat(str(charge_date or "")[:10])
    except ValueError:
        return False
    return abs((d - renewal).days) <= days
