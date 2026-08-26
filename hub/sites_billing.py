"""Which Smart 1 Sites are billed for hosting, and which hosting bills no site.

Three products in QuickBooks pay for a website we host — **Monthly Web
Hosting**, **Monthly Website Hosting & Maintenance** and **Website
Maintenance** — and nothing joined them to the sites they pay for. The two
questions that follow have therefore never had an answer:

* **which live sites are not being billed** — a website sitting on our plan,
  costing us its platform fee every month, with nothing invoiced against it;
* **which sites are being billed and are not active** — an expired or
  cancelled project still invoiced monthly, which is somebody's money going out
  for a site that serves nothing.

Neither is visible from either end. QuickBooks knows the charge and not the
site; Sites Admin knows the site and not the charge; and the only string
connecting them is the **description on the invoice line**, which a person
typed — usually a domain, sometimes the business name, sometimes neither.

## What "matched" is allowed to mean

The join is the description, and a description is free text, so this module is
mostly a set of refusals:

* **The client registry is read, and read exactly.** A QuickBooks customer
  name that names no project can still resolve to a Knack client whose website
  is on one — which is how a project titled "Legacy Build 2019", with the
  client field never filled in, gets joined to the charge that pays for it.
  `client_key.resolve()` is called with `allow_fuzzy` off, so a name the
  registry can only guess at resolves to nothing. A registry that could not be
  read costs that one rule and is **named on the page**, because "the customer
  name matched nothing" and "we could not check the registry" are different
  claims about the same empty cell.

* **A domain is a join; a name is a comparison.** A domain in the description
  identifies exactly one project. A business name is matched **exactly on the
  normalised form** through `hub/client_key.normalise_name` — never a
  substring, for the reason that file gives at length: "Riverside HVAC" must
  not collect "Riverside HVAC Supply", and attributing one company's hosting
  bill to another company's website is the worst outcome available here.

* **A resemblance is a suggestion and never a match.** `hub/site_names.py`'s
  near-match pass is run, and what it finds is printed as *possible* beside a
  row that still counts as unmatched. A fuzzy hit folded into the totals is a
  fact nobody re-examines; a fuzzy hit printed as a question is a row somebody
  can settle in two seconds.

* **A name that matches projects filed under two different clients matches
  neither.** Both are named instead.

* **A domain the description names and we hold no site for is its own answer.**
  "We could not read the description" and "the description names
  example.com and we host no such site" are different findings, and only the
  second one means the charge is for hosting somebody else does.

* **A product name that matches no QuickBooks item is not a product with no
  charges.** This is the silent zero this whole report could quietly become:
  rename "Website Maintenance" in QuickBooks and every site on the book reads
  as unbilled, in a clean confident table, with nothing saying why. The
  catalogue is read first and the three names are checked against it; if none
  of them resolves, the report says **not measured** rather than reporting that
  nothing is billed.

* **A file host is not a website.** `hub/client_urls.looks_like_a_website`
  rejects the Cloudinary, Google Drive and social URLs that turn up in invoice
  descriptions, and the Simvoly platform domains are rejected with them — a
  project still parked on `something.simvoly.com` has no real domain, and
  joining on one would file every unlaunched site together.

## Lapsed is not unbilled, and stopped is not overbilled

Both halves need the clock, not just presence:

* A live site whose last hosting charge was eight months ago is not the same
  finding as one that has never been billed at all, so they are separate rows
  saying which.
* An inactive site is only a finding while the billing is **current**. A
  cancelled project whose last charge was last year is a project that was
  cancelled and stopped being billed, which is the system working. Flagging it
  would bury the ones still being charged today.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

from hub import site_names
from hub.client_context import canonical_domain
from hub import client_key
from hub.client_key import normalise_name
from hub.client_urls import looks_like_a_website
from hub.sites_match import PLATFORM_DOMAINS, inactive_reason, is_active

# The three QuickBooks products that pay for a site we host. Held as data
# because two things read them: the report, and the catalogue check that says
# whether QuickBooks still calls them this.
HOSTING_PRODUCTS = (
    "Monthly Web Hosting",
    "Monthly Website Hosting & Maintenance",
    "Website Maintenance",
)

# How far back invoices are read. A year rather than a quarter because an
# annual hosting plan invoiced last November is billing, and a three-month
# window would report the site it pays for as unbilled.
LOOKBACK_MONTHS = 12

# How recent a charge has to be to count as *current* billing. Three months,
# not one: these products are invoiced monthly and quarterly depending on the
# client, and a monthly invoice that has not been raised yet this month is not
# a lapse. Every row prints its own last-billed date, so a reader is never left
# guessing which side of the line a site sits on.
RECENT_MONTHS = 3


def lookback_months() -> int:
    try:
        return max(1, min(36, int(os.environ.get("SITES_BILLING_MONTHS") or LOOKBACK_MONTHS)))
    except (TypeError, ValueError):
        return LOOKBACK_MONTHS


# ---------------------------------------------------------------------------
# Product names
# ---------------------------------------------------------------------------
def _norm_item(name: str) -> str:
    """A product name in a form two spellings of it agree on.

    "&" and "and" are the same word here, case is not a distinction, and
    QuickBooks item names arrive with a parent prefix ("Services:Monthly Web
    Hosting") when the item sits in a category — that last one is why a plain
    equality test found nothing on a book where every item is categorised.
    """
    s = str(name or "").strip().lower()
    s = s.rsplit(":", 1)[-1].strip()        # drop a QuickBooks category prefix
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_HOSTING_KEYS = {_norm_item(p): p for p in HOSTING_PRODUCTS}


def is_hosting_item(name: str) -> str:
    """The hosting product this item name *is*, or "" — never a near miss."""
    return _HOSTING_KEYS.get(_norm_item(name), "")


def catalogue_check(items) -> dict:
    """Which of the three products QuickBooks still has, and under what name.

    Returns ``{"found": {product: [item names]}, "missing": [products],
    "similar": [item names]}``. Two of the three matter:

    * `missing` — a product QuickBooks no longer has under that name cannot
      produce a single charge, so every site it pays for reads as unbilled and
      the table looks like a finding rather than a broken filter.
    * `similar` — a product whose name *contains* one of the three ("Monthly
      Web Hosting - Annual") and is therefore **not counted**. Matching it
      would be the substring rule `hub/client_key.py` exists to refuse, and
      dropping it in silence is how a whole tier of hosting revenue goes
      missing from a report that looks complete. It is named instead, and
      somebody decides.
    """
    found: dict[str, list[str]] = {p: [] for p in HOSTING_PRODUCTS}
    similar: list[str] = []
    for it in items or ():
        raw = str((it or {}).get("name") or "")
        hit = is_hosting_item(raw)
        if hit:
            found[hit].append(raw)
            continue
        norm = _norm_item(raw)
        if norm and any(k in norm for k in _HOSTING_KEYS):
            similar.append(raw)
    return {"found": {k: v for k, v in found.items() if v},
            "missing": [p for p in HOSTING_PRODUCTS if not found[p]],
            "similar": sorted(set(similar))}


# ---------------------------------------------------------------------------
# Reading a domain out of free text
# ---------------------------------------------------------------------------
_EMAIL = re.compile(r"\S+@\S+")
# The path is matched and thrown away rather than left for the next pass:
# "acme.com/index.html" otherwise yields acme.com AND index.html, because
# "html" is a four-letter last label and every domain test in this codebase
# accepts one. A file name read as a domain joins a hosting charge to nothing
# and reports the charge as naming a site we do not hold.
_DOMAINISH = re.compile(
    r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)(?:/\S*)?", re.I)

# Last labels that are file types rather than top-level domains. A short list
# on purpose: the alternative is a TLD table this repo would have to keep in
# step with IANA, and being wrong in that direction only costs a suggestion,
# while being wrong in this one costs a wrong join.
_NOT_A_TLD = {
    "html", "htm", "php", "asp", "aspx", "jsp", "js", "css", "json", "xml",
    "txt", "csv", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip",
    "jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "mp3", "mp4", "mov",
}


def _is_platform(domain: str) -> bool:
    d = (domain or "").lower()
    return any(d == p or d.endswith("." + p) for p in PLATFORM_DOMAINS)


def domains_in(text: str) -> list[str]:
    """Every domain in a line of free text that could be a client's website.

    Emails are stripped **before** the scan rather than filtered after it:
    "billing@acme.com" contains the string "acme.com", and a reader that finds
    it there has joined a hosting charge to a website on the strength of
    somebody's email address.

    A file host, a social profile and a Simvoly platform domain are all
    rejected — the first two by `hub/client_urls.looks_like_a_website`, which
    already carries the list this codebase learned the hard way, and the third
    because an unlaunched project's domain identifies the platform rather than
    the business.
    """
    raw = _EMAIL.sub(" ", str(text or ""))
    out, seen = [], set()
    for m in _DOMAINISH.finditer(raw):
        d = canonical_domain(m.group(1))
        if not d or d in seen:
            continue
        seen.add(d)
        if d.rsplit(".", 1)[-1] in _NOT_A_TLD:
            continue
        if _is_platform(d) or not looks_like_a_website(d):
            continue
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# The book of sites
# ---------------------------------------------------------------------------
def _read_projects() -> tuple[list[dict], str]:
    """Every Simvoly project, or the reason we could not read them.

    A pair, not a bare list, for the reason `connected_accounts_result()` gives
    in Google Finder: *there are no sites* and *we could not look* are
    different answers, and only the first one means every hosting charge on the
    book is unexplained. `hub/sites_match._site_rows()` swallows this and
    returns [] — right for a suggestion page that degrades to no suggestions,
    wrong for a report whose empty state is a finding.
    """
    try:
        from modules.sites_admin import db as sdb
    except Exception as exc:                                # noqa: BLE001
        return [], f"Sites Admin is not available here ({type(exc).__name__}: {exc})."
    out, page, per_page = [], 1, 200
    try:
        while True:
            rows, total = sdb.query_projects(page=page, per_page=per_page)
            out.extend(r for r in rows if isinstance(r, dict))
            if len(out) >= total or not rows or page > 30:
                break
            page += 1
    except Exception as exc:                                # noqa: BLE001
        return out, f"Could not read the Smart 1 Sites projects ({type(exc).__name__}: {exc})."
    return out, ""


def _site_row(row: dict) -> dict:
    name = str(row.get("name") or "").strip()
    client = str(row.get("internal_client_name") or "").strip()
    domain = canonical_domain(row.get("domain") or "")
    if domain and _is_platform(domain):
        domain = ""                     # a platform domain identifies nobody
    pid = str(row.get("project_id") or "")
    return {
        "project_id": pid,
        "name": name,
        "client": client,
        # The business name read off the project TITLE, kept apart from the
        # client recorded on it rather than falling back to it. Both are
        # indexed: a project titled "TMRG - Acme Plumbing" and filed under
        # "Acme Plumbing Co LLC" answers to either string, and — more to the
        # point — a title naming one business while the project is filed under
        # another is the ambiguity `_one_client()` exists to refuse. Folding
        # the two into one field made that check unreachable.
        "business_name": site_names.best_name(name) or "",
        "domain": domain,
        "raw_domain": str(row.get("domain") or "").strip(),
        "status": str(row.get("status") or "").strip(),
        "lifecycle": str(row.get("lifecycle_state") or "").strip(),
        "active": is_active(row),
        "reason": inactive_reason(row),
        "href": f"/sites/projects/{pid}" if pid else "",
    }


def site_index(rows=None) -> dict:
    """The sites, and the three ways a hosting line can name one.

    ``by_domain`` is the join. ``by_client`` and ``by_name`` are lists, because
    one client legitimately has several sites — the shop, the microsite, the
    campaign page — and a hosting charge naming only the business could be
    paying for any of them. Which is itself a finding, and `report()` says so
    rather than picking one.
    """
    err = ""
    if rows is None:
        rows, err = _read_projects()
    sites = [_site_row(r) for r in rows or ()]
    by_domain: dict[str, list[dict]] = {}
    by_client: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    pairs: list[tuple[str, str]] = []
    for s in sites:
        if s["domain"]:
            by_domain.setdefault(s["domain"], []).append(s)
        if s["client"]:
            key = normalise_name(s["client"])
            if key:
                by_client.setdefault(key, []).append(s)
                pairs.append((s["client"], s["project_id"]))
        if s["business_name"]:
            key = normalise_name(s["business_name"])
            if key:
                by_name.setdefault(key, []).append(s)
                pairs.append((s["business_name"], s["project_id"]))
    return {"sites": sites, "by_domain": by_domain, "by_client": by_client,
            "by_name": by_name, "book": site_names.index_names(pairs),
            # Filled in by report(). Kept out of here because building it is a
            # Knack read and site_index() is called by the tests with fixtures.
            "alias": None,
            "error": err}


def _one_client(projects: list[dict]) -> bool:
    """Do these projects all belong to one client?

    Two projects sharing a normalised name but filed under different clients
    are two different businesses, and a charge that names that string names
    neither of them. Projects with no client recorded do not contradict
    anybody, so they are not counted as a second answer.
    """
    named = {normalise_name(p["client"]) for p in projects if p["client"]}
    return len(named) <= 1


# ---------------------------------------------------------------------------
# Matching one hosting line to a site
# ---------------------------------------------------------------------------
def match_line(line: dict, index: dict) -> dict:
    """Which site this hosting charge is for, and on what evidence.

    Strongest evidence first, and it stops at the first answer:

    1. a domain in the **line description** that is one of our sites;
    2. a domain **elsewhere on the invoice** — a description-only line under
       the item, or the customer memo. Same strength of identifier, weaker
       provenance, so it is labelled differently;
    3. the QuickBooks **customer name**, exactly, against the client recorded
       on a project;
    4. the same name against the **business name derived from the project
       title** — `hub/site_names.py`, because a Simvoly project is called
       "TMRG - JWS Pottery" and not "JWS Pottery";
    5. the **client registry** — the customer name resolved exactly through
       `hub/client_key.resolve()` to a Knack client, and that client's website
       looked up among the sites. This is the rule that finds the ones the
       other four cannot: a project whose Simvoly title is "Legacy Build 2019"
       and whose client was never filled in still carries the domain, and the
       registry is what turns a customer name into that domain;
    6. a domain in the description that we hold **no site for** — an answer,
       not a failure;
    7. a **resemblance**, which is returned as `possible` and is deliberately
       not a match. The caller still counts the line as unmatched.
    """
    desc = str(line.get("description") or "")
    cust = str(line.get("customer") or "")

    for d in domains_in(desc):
        hit = index["by_domain"].get(d)
        if hit:
            return {"kind": "domain", "projects": hit, "domain": d,
                    "why": f"the description names {d}"}

    for d in domains_in(line.get("invoice_text") or ""):
        hit = index["by_domain"].get(d)
        if hit:
            return {"kind": "invoice_domain", "projects": hit, "domain": d,
                    "why": f"{d} is named elsewhere on the invoice, not on this line"}

    key = normalise_name(cust)
    if key:
        for field, kind, label in (("by_client", "client", "the client recorded on the project"),
                                   ("by_name", "name", "the business name in the project title")):
            hit = index[field].get(key)
            if not hit:
                continue
            if not _one_client(hit):
                names = sorted({p["client"] for p in hit if p["client"]})
                return {"kind": "ambiguous", "projects": [], "domain": "",
                        "why": (f"“{cust}” matches {len(hit)} projects filed under "
                                f"different clients ({', '.join(names[:3])}) — "
                                "matching one of them would file this charge "
                                "against the wrong company")}
            return {"kind": kind, "projects": hit, "domain": "",
                    "why": f"the customer name matches {label}, exactly"}

    # 5. The client registry, which knows names and domains this book does
    #    not. A QuickBooks customer filed as "Acme Plumbing Inc" resolves to
    #    the Knack client "Acme Plumbing", whose website is acmeplumbing.com —
    #    and that domain is on a project whose Simvoly title mentions neither
    #    string. Exact only: `allow_fuzzy` stays off, so a name the registry
    #    can only guess at resolves to nothing, which is the answer.
    alias = index.get("alias")
    if key and alias:
        try:
            res = client_key.resolve(cust, index=alias)
        except Exception:                                   # noqa: BLE001
            res = None
        if res and res.get("known") and res.get("confidence") == "exact":
            dom = res.get("domain") or ""
            hit = index["by_domain"].get(dom) if dom else None
            if hit:
                return {"kind": "registry", "projects": hit, "domain": dom,
                        "why": (f"“{cust}” is {res['client']} in the client "
                                f"registry, whose website is {dom}")}
            rkey = normalise_name(res.get("client") or "")
            for field, kind in (("by_client", "client"), ("by_name", "name")):
                hit = index[field].get(rkey) if rkey and rkey != key else None
                if hit and _one_client(hit):
                    return {"kind": kind, "projects": hit, "domain": "",
                            "why": (f"“{cust}” is {res['client']} in the client "
                                    f"registry, which is the name on the project")}

    stray = domains_in(desc) or domains_in(line.get("invoice_text") or "")
    if stray:
        return {"kind": "domain_not_ours", "projects": [], "domain": stray[0],
                "why": (f"the description names {stray[0]} and we hold no "
                        f"Smart 1 Sites project on that domain")}

    near = []
    if cust:
        try:
            near = site_names.near_matches(cust, index["book"])
        except Exception:                                   # noqa: BLE001
            near = []
    if near:
        best = near[0]
        return {"kind": "possible", "projects": [], "domain": "",
                "why": (f"nothing matched. Possible: “{best['client']}” "
                        f"({int(best['score'] * 100)}%) — confirm before acting"),
                "suggestion": best["client"]}

    return {"kind": "none", "projects": [], "domain": "",
            "why": ("nothing in the description, the invoice or the customer "
                    "name names a site we host")}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def _month_keys(months: int, today: _dt.date) -> list[str]:
    first = today.replace(day=1)
    keys = []
    for _ in range(months):
        keys.append(first.strftime("%Y-%m"))
        first = (first - _dt.timedelta(days=1)).replace(day=1)
    return keys


def _since(months: int, today: _dt.date) -> str:
    first = today.replace(day=1)
    for _ in range(months - 1):
        first = (first - _dt.timedelta(days=1)).replace(day=1)
    return first.isoformat()


def report(lines=None, projects=None, items=None, today=None,
           months: int | None = None) -> dict:
    """Every site against every hosting charge.

    `lines`, `projects` and `items` are the three reads, injectable so the test
    harness needs no QuickBooks company and no Sites Admin database. Left out,
    they are fetched.

    Nothing here raises for a missing source. `error` says QuickBooks could not
    be read, `sites_error` says Sites Admin could not, and either one means the
    report is **not measured** rather than clear — the caller renders it that
    way.
    """
    today = today or _dt.date.today()
    months = months or lookback_months()
    out: dict = {
        "months": months, "recent_months": RECENT_MONTHS,
        "since": _since(months, today),
        "products": list(HOSTING_PRODUCTS),
        "catalogue": {"found": {}, "missing": list(HOSTING_PRODUCTS),
                      "similar": []},
        "error": "", "sites_error": "", "catalogue_error": "",
        "registry_error": "",
        "billed_inactive": [], "unbilled": [], "lapsed": [],
        "unmatched": [], "short": [], "ok": [],
        "counts": {},
    }

    # ---- the catalogue, first. A filter that matches no product is not a
    # book with nothing billed, and this is the check that tells them apart.
    if items is None:
        try:
            from . import quickbooks as qb
            items = qb.items()
        except Exception as exc:                            # noqa: BLE001
            items = None
            out["catalogue_error"] = str(exc)
    if items is not None:
        out["catalogue"] = catalogue_check(items)

    # ---- the invoice lines
    if lines is None:
        try:
            from . import quickbooks as qb
            lines = qb.invoice_lines_since(out["since"])
        except Exception as exc:                            # noqa: BLE001
            out["error"] = str(exc)
            lines = []

    index = site_index(projects)
    # Built once for the whole run rather than once per charge: it walks every
    # client the registry knows. alias_index() never raises and reports its own
    # failure, so a registry we could not read costs the fifth matching rule
    # and nothing else.
    index["alias"] = client_key.alias_index()
    out["registry_error"] = (index["alias"] or {}).get("error") or ""
    out["sites_error"] = index["error"]
    out["site_count"] = len(index["sites"])
    out["active_count"] = sum(1 for s in index["sites"] if s["active"])

    recent = set(_month_keys(RECENT_MONTHS, today))

    hosting = []
    for ln in lines or ():
        product = is_hosting_item(ln.get("item"))
        if not product:
            continue
        row = dict(ln)
        row["product"] = product
        row["recent"] = str(ln.get("date") or "")[:7] in recent
        hosting.append(row)
    out["line_count"] = len(hosting)

    by_project: dict[str, dict] = {}
    for ln in hosting:
        m = match_line(ln, index)
        ln["match"] = m
        if not m["projects"]:
            out["unmatched"].append(ln)
            continue
        for p in m["projects"]:
            slot = by_project.setdefault(p["project_id"], {"site": p, "lines": []})
            slot["lines"].append(ln)

    # ---- a customer whose charges cannot cover their live sites.
    # Only where the join was the *name*: a charge that names a domain is
    # about that one site and says nothing about the others.
    per_customer: dict[str, dict] = {}
    for ln in hosting:
        m = ln.get("match") or {}
        if m.get("kind") not in ("client", "name") or not ln.get("recent"):
            continue
        slot = per_customer.setdefault(str(ln.get("customer") or ""), {
            "customer": ln.get("customer") or "", "lines": [], "sites": {}})
        slot["lines"].append(ln)
        for p in m["projects"]:
            if p["active"]:
                slot["sites"][p["project_id"]] = p
    for slot in per_customer.values():
        if len(slot["sites"]) > len(slot["lines"]):
            out["short"].append({
                "customer": slot["customer"],
                "lines": len(slot["lines"]),
                "sites": sorted(slot["sites"].values(), key=lambda s: s["name"].lower()),
                "amount": round(sum(l["amount"] for l in slot["lines"]), 2),
            })
    out["short"].sort(key=lambda r: -(len(r["sites"]) - r["lines"]))

    # ---- every site, against what was charged for it
    for site in index["sites"]:
        slot = by_project.get(site["project_id"])
        charges = sorted(slot["lines"], key=lambda l: str(l.get("date") or "")) if slot else []
        last = charges[-1] if charges else None
        rec = {
            "site": site,
            "lines": charges,
            "last_date": str(last.get("date") or "") if last else "",
            "last_amount": float(last.get("amount") or 0) if last else 0.0,
            "product": last.get("product") if last else "",
            "customer": last.get("customer") if last else "",
            "customer_id": last.get("customer_id") if last else "",
            "description": str(last.get("description") or "") if last else "",
            "link": last.get("link") if last else "",
            "why": (last.get("match") or {}).get("why", "") if last else "",
            "current": any(l.get("recent") for l in charges),
        }
        if site["active"]:
            if rec["current"]:
                out["ok"].append(rec)
            elif charges:
                out["lapsed"].append(rec)
            else:
                out["unbilled"].append(rec)
        elif rec["current"]:
            # An inactive site whose last charge is old was cancelled and
            # stopped being billed, which is the system working. Only current
            # billing against a dead site is a finding.
            out["billed_inactive"].append(rec)

    out["billed_inactive"].sort(key=lambda r: -r["last_amount"])
    out["unbilled"].sort(key=lambda r: r["site"]["name"].lower())
    out["lapsed"].sort(key=lambda r: r["last_date"])
    out["ok"].sort(key=lambda r: r["site"]["name"].lower())
    out["unmatched"].sort(key=lambda l: (str(l.get("customer") or "").lower(),
                                         str(l.get("date") or "")))

    out["counts"] = {
        "sites": len(index["sites"]),
        "active": out["active_count"],
        "lines": len(hosting),
        "billed_inactive": len(out["billed_inactive"]),
        "unbilled": len(out["unbilled"]),
        "lapsed": len(out["lapsed"]),
        "unmatched": len(out["unmatched"]),
        "short": len(out["short"]),
        "ok": len(out["ok"]),
    }
    return out


def unavailable(rep: dict) -> str:
    """Why this report could not be run, or "".

    Three ways it cannot answer, and all three would otherwise render as a
    table of findings that happens to be empty or complete:

      * QuickBooks could not be read at all;
      * Sites Admin could not be read, so there is no book to compare against;
      * **none of the three products exists in QuickBooks under these names**,
        which makes every site on the book read as unbilled.
    """
    if rep.get("error"):
        return ("QuickBooks could not be read, so nothing here is measured: "
                + rep["error"])
    if rep.get("sites_error"):
        return ("The Smart 1 Sites projects could not be read, so there is "
                "nothing to compare the invoices against: " + rep["sites_error"])
    if not rep.get("site_count"):
        return ("Smart 1 Sites returned no projects at all. That is not the "
                "same as no site being billed — it means the site list is "
                "empty or unreadable, and every hosting charge below would "
                "read as matching nothing.")
    if rep.get("catalogue_error"):
        # "We asked and QuickBooks does not have these products" and "we could
        # not ask" are different answers, and the first one is an accusation
        # about somebody's catalogue. Say which happened.
        return ("The QuickBooks product catalogue could not be read, so this "
                "report cannot confirm that it is filtering on products that "
                "still exist — and a filter that matches nothing looks exactly "
                "like a book with nothing billed: " + rep["catalogue_error"])
    cat = rep.get("catalogue") or {}
    if len(cat.get("missing") or []) == len(HOSTING_PRODUCTS):
        return ("None of the three hosting products exists in QuickBooks under "
                "the names this report looks for — "
                + ", ".join(f"“{p}”" for p in HOSTING_PRODUCTS)
                + ". Every site would read as unbilled, which would be a "
                  "property of the filter and not of the book.")
    return ""
