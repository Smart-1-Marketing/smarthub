"""The derived health strip on a Client 360 record.

`hub/seo.record_health()` answers "what is outstanding on this client" for
the SEO record, from that module's own stores -- the schema pages, the blog
plan, the alt-text scan, the FAQ pages and the llms.txt record -- and the
SEO client page draws it as a strip of pills, each a fact rather than a tick.
Client 360 had no such strip. It has nineteen cards, a row of status pills
in its header that are badges rather than findings, and a Site Health card
fed by the latest scan; nothing on it said what needs doing.

A 360 record spans more than SEO: products and their terms, billing, the
website and its audit, the Google accounts recorded against it, open
proposals, and the outstanding work `/my-clients` already computes. So this
is its own reading rather than the SEO one reused, and it lives beside it:
the SEO half IS `seo.record_health()`, called here rather than restated,
and the blogs rule is `seo.blogs_health()`, which both strips read so the
two screens cannot disagree about who is behind.

Four rules, all this codebase's own:

  * **Nothing here may raise.** Every source is asked in its own function
    and a source that cannot be read is NAMED in `unread` and drawn as a
    pill in its own state, never as a clean one -- "no proposals are open"
    and "the proposal store would not answer" are different answers.
  * **Absent is not clear.** Each pill carries `measured`, and a pill that
    was not measured renders in a state of its own; a client with nothing
    on file reads as unknown, never as healthy. The whole payload carries
    `measured` too, and it is False when any source refused.
  * **Every pill links where the work is.** `go` names the rail section on
    the record (`C360_SECTIONS` in client360.html); the SEO pills carry an
    `href` to the SEO record's own section instead, because that is where
    a blog is marked posted and a schema page is built.
  * **Nothing is written.** This reads, and it reads what the cards on the
    page already read, through the same functions, so the strip and the
    card beneath it cannot quote two different numbers.
"""
from __future__ import annotations

import datetime as _dt

# Days ahead within which an ending product is "ending soon". House
# guidance, not a published figure: three weeks is the renewal conversation.
ENDING_SOON_DAYS = 21

# Where each pill sends somebody. Keys are C360_SECTIONS keys in
# hub/templates/client360.html; test_client360_health.py holds the two lists
# against each other so a pill cannot point at a section that is not there.
SECTIONS = {
    "products": "overview", "billing": "billing", "site": "website",
    "google": "google", "proposals": "overview", "work": "work",
}

STATES = ("ok", "warn", "bad", "idle", "unread")


def _pill(key: str, label: str, state: str, value: str, *, measured: bool,
          detail: str = "", go: str = "", href: str = "") -> dict:
    assert state in STATES
    return {"key": key, "label": label, "state": state, "value": value,
            "measured": measured, "detail": detail,
            "go": go or SECTIONS.get(key, ""), "href": href}


def _unread(key: str, label: str, why: str, *, href: str = "") -> dict:
    return _pill(key, label, "unread", "Could not read", measured=False,
                 detail=why, href=href)


def _parse_mdy(s: str) -> _dt.date | None:
    """Knack's m/d/Y, through the reader hub/seo.py already has."""
    from hub import seo
    try:
        return seo._parse_mdY(s)                 # noqa: SLF001 -- the one reader
    except Exception:                            # noqa: BLE001
        return None


# ----------------------------------------------------------------- sources
def _group(name: str, group: dict | None) -> tuple[dict | None, str]:
    """The Knack group Client 360 itself draws, or why it could not be read."""
    if group is not None:
        return group, ""
    try:
        from hub import knack_data
        groups = knack_data.search_client(name)
    except Exception as exc:                     # noqa: BLE001
        return None, f"the client record could not be read ({type(exc).__name__})"
    want = name.strip().lower()
    for g in groups:
        if str(g.get("client") or "").strip().lower() == want:
            return g, ""
    if not groups:
        # Nobody on file and a book that could not be read look identical
        # from here, and only the first is a fact about the client.
        try:
            why = knack_data.products_error()
        except Exception:                        # noqa: BLE001
            why = ""
        if why:
            return None, why
    return (groups[0] if groups else {}), ""


def _products(g: dict | None, err: str, today: _dt.date) -> tuple[dict, dict, list]:
    if err:
        return _unread("products", "Products", err), _unread("billing", "Billing", err), []
    products = list((g or {}).get("products") or [])
    queue = []
    if not products:
        idle = "Nothing on file" + (" — new business" if (g or {}).get("io_only") else "")
        return (_pill("products", "Products", "idle", idle, measured=True,
                      detail="Knack has no products for this client."),
                _pill("billing", "Billing", "idle", "Nothing on file", measured=True),
                queue)
    live = [p for p in products if str(p.get("status") or "").lower() == "live"]
    ending = []
    for p in live:
        end = _parse_mdy(str(p.get("end") or ""))
        if end and 0 <= (end - today).days <= ENDING_SOON_DAYS:
            ending.append((p, (end - today).days))
    if not live:
        prod = _pill("products", "Products", "warn", f"None live of {len(products)}",
                     measured=True, detail="Every product on file has ended or is not marked live.")
        queue.append({"level": "warn", "section": "overview",
                      "title": "No live product on file",
                      "detail": f"{len(products)} on record, none of them running."})
    elif ending:
        soonest = min(d for _, d in ending)
        prod = _pill("products", "Products", "warn",
                     f"{len(live)} live · {len(ending)} ending in {soonest}d",
                     measured=True,
                     detail=f"{len(ending)} live product{'s' if len(ending) != 1 else ''} "
                            f"end within {ENDING_SOON_DAYS} days.")
        queue.append({"level": "warn", "section": "overview",
                      "title": f"{len(ending)} product{'s' if len(ending) != 1 else ''} "
                               f"ending within {ENDING_SOON_DAYS} days",
                      "detail": "; ".join(f"{p.get('product') or 'product'} in {d} days"
                                          for p, d in ending[:4])})
    else:
        prod = _pill("products", "Products", "ok", f"{len(live)} live", measured=True)

    monthly = float((g or {}).get("billing_monthly") or 0)
    if monthly > 0:
        bill = _pill("billing", "Billing", "ok", f"${monthly:,.0f}/mo active", measured=True,
                     detail="Active monthly billing on the products Knack holds.")
    elif live:
        bill = _pill("billing", "Billing", "warn", "Live, $0/mo on file", measured=True,
                     detail="Products are live but no monthly amount is recorded against them.")
        queue.append({"level": "warn", "section": "billing",
                      "title": "Live products with no monthly amount on file",
                      "detail": "The billing figure on the record reads $0."})
    else:
        bill = _pill("billing", "Billing", "idle", "Nothing active", measured=True)
    return prod, bill, queue


def _domain(g: dict | None) -> str:
    from hub.client_context import canonical_domain
    for w in (g or {}).get("websites") or []:
        d = canonical_domain(w.get("liveUrl") or w.get("domain") or "")
        if d:
            return d
    return ""


def _site(domain: str, g_err: str) -> tuple[dict, list]:
    from hub import upsell, website_audit
    if g_err:
        return _unread("site", "Website audit", g_err), []
    if not domain:
        return _pill("site", "Website audit", "idle", "No website on file", measured=True,
                     detail="Nothing to audit until a website is attached."), []
    audits, err = upsell.audits_for([domain])
    if err:
        return _unread("site", "Website audit", f"the scans table would not answer ({err})"), []
    a = audits.get(domain)
    if not a:
        return (_pill("site", "Website audit", "warn", "Never audited", measured=True,
                      detail=f"No completed scan of {domain}."),
                [{"level": "warn", "section": "website", "title": "Website never audited",
                  "detail": f"{domain} has no completed scan."}])
    age = a.get("age") or {}
    score = a.get("score")
    score_txt = f"score {score:g}" if isinstance(score, (int, float)) else "no score"
    if age.get("measured") is False:
        return _pill("site", "Website audit", "warn", f"{score_txt}, age not measured",
                     measured=True, detail=age.get("note", "")), []
    if age.get("stale"):
        return (_pill("site", "Website audit", "warn",
                      f"{score_txt}, {age['age_days']} days old", measured=True,
                      detail=age.get("note", "")),
                [{"level": "warn", "section": "website", "title": "Website audit is stale",
                  "detail": age.get("note", ""), "age_days": age.get("age_days")}])
    return _pill("site", "Website audit", "ok", f"{score_txt}, {age['age_days']}d ago",
                 measured=True, detail=age.get("note", "")), []


def _google(g: dict | None, g_err: str) -> dict:
    """What is RECORDED against the website record -- not what is on the page."""
    if g_err:
        return _unread("google", "Google", g_err)
    webs = list((g or {}).get("websites") or [])
    if not webs:
        return _pill("google", "Google", "idle", "No website on file", measured=True)
    has_ga = any(str(w.get("ga") or "").strip() for w in webs)
    has_gtm = any(str(w.get("gtm") or "").strip() for w in webs)
    if has_ga and has_gtm:
        return _pill("google", "Google", "ok", "GA and GTM recorded", measured=True,
                     detail="Recorded on the website record; whether the tags are on the page is the audit's question.")
    missing = [n for n, ok in (("GA", has_ga), ("GTM", has_gtm)) if not ok]
    return _pill("google", "Google", "warn", "No " + " or ".join(missing) + " recorded",
                 measured=True,
                 detail="Nothing recorded on the website record for " + " or ".join(missing) + ".")


_SIGNAL_WORDS = {
    "unopened": "not opened", "waiting": "read, no answer", "expiring": "price lapsing",
    "expired": "price lapsed", "to_convert": "won, no IO",
}


def _proposals(name: str) -> tuple[dict, list]:
    from hub import sales_status
    try:
        book = sales_status.by_client()
    except Exception as exc:                     # noqa: BLE001
        return _unread("proposals", "Proposals", f"{type(exc).__name__}"), []
    if not book.get("measured"):
        return _unread("proposals", "Proposals",
                       book.get("error") or "the proposal store would not answer"), []
    key = name.strip().lower()
    cards = next((v for k, v in (book.get("clients") or {}).items()
                  if str(k).strip().lower() == key), [])
    if not cards:
        return _pill("proposals", "Proposals", "idle", "Nothing open", measured=True,
                     detail="No open quote is waiting on this client."), []
    signals = sorted({_SIGNAL_WORDS.get(c.get("signal"), c.get("signal") or "") for c in cards})
    state = "bad" if any(c.get("signal") in ("expired", "to_convert") for c in cards) else "warn"
    n = len(cards)
    return (_pill("proposals", "Proposals", state, f"{n} waiting: " + ", ".join(s for s in signals if s),
                  measured=True),
            [{"level": state, "section": "overview",
              "title": f"{n} proposal{'s' if n != 1 else ''} need{'s' if n == 1 else ''} chasing",
              "detail": ", ".join(s for s in signals if s)}])


def _work(name: str) -> tuple[dict, list]:
    from hub import client_health
    try:
        res = client_health.issues_for_client(name)
    except Exception as exc:                     # noqa: BLE001
        return _unread("work", "Outstanding", f"{type(exc).__name__}"), []
    if not res.get("ok") or not res.get("measured"):
        return _unread("work", "Outstanding", res.get("error") or res.get("warning")
                       or "the outstanding-work report would not answer"), []
    n = int(res.get("issue_count") or 0)
    blind = res.get("missing_sources") or []
    suffix = f" · {len(blind)} source{'s' if len(blind) != 1 else ''} not read" if blind else ""
    if n:
        return (_pill("work", "Outstanding", "warn", f"{n} issue{'s' if n != 1 else ''}{suffix}",
                      measured=True, detail=res.get("warning") or ""),
                [{"level": "warn", "section": "work",
                  "title": f"{n} outstanding issue{'s' if n != 1 else ''} on /my-clients",
                  "detail": "; ".join(str(i.get("title") or i.get("kind") or "")
                                      for i in (res.get("issues") or [])[:4])}])
    if blind:
        return _pill("work", "Outstanding", "warn", "None found" + suffix, measured=True,
                     detail=res.get("warning") or "Some sources could not be read, so this is not a clean bill."), []
    return _pill("work", "Outstanding", "ok", "Nothing outstanding", measured=True), []


def _seo(name: str, today: str = "") -> tuple[list, list, list]:
    """The SEO pills, read through hub/seo -- the same functions the SEO
    record itself draws. Returns (pills, queue, unread).

    `today` is client360's own day, carried down rather than left to the wall
    clock. Without it the product pills answer to the injected date and the
    blogs pill answers to whatever the clock says, so one strip holds two
    ideas of what day it is -- which is invisible except on the day they
    differ, and reads as the blogs rule being wrong rather than unfixed.
    """
    from hub import seo
    from urllib.parse import quote
    href = "/seo/client?name=" + quote(name)
    try:
        base = seo._client_base(name)            # noqa: SLF001
    except Exception as exc:                     # noqa: BLE001
        why = f"the SEO book could not be read ({type(exc).__name__})"
        return [_unread("seo", "SEO", why, href=href + "#schema"),
                _unread("blogs", "Blogs", why, href=href + "#blogs")], [], [why]
    sells_seo = bool(base.get("products"))
    sells_blogs = bool(base.get("blogs"))
    try:
        store = seo.load_store(name)
    except Exception as exc:                     # noqa: BLE001
        why = f"the SEO store could not be read ({type(exc).__name__})"
        return [_unread("seo", "SEO", why, href=href + "#schema"),
                _unread("blogs", "Blogs", why, href=href + "#blogs")], [], [why]
    has_store = bool(store.get("pages") or store.get("sitemap")
                     or (store.get("blogs") or {}).get("posts"))
    if not sells_seo and not has_store:
        return [_pill("seo", "SEO", "idle", "Not sold", measured=True,
                      detail="No live SEO product on the book and no SEO record.",
                      href=href + "#record"),
                _pill("blogs", "Blogs", "idle", "No blogs product", measured=True,
                      href=href + "#blogs")], [], []
    h = seo.record_health(name, store, sells=sells_blogs, today=today)
    sch, b = h.get("schema") or {}, h.get("blogs") or {}
    if sch.get("total") is not None:
        seo_pill = _pill("seo", "SEO", "warn" if sch.get("remaining") else "ok",
                         f"schema {sch.get('built', 0)} of {sch['total']} pages",
                         measured=True, href=href + "#schema")
    elif sch.get("measured"):
        seo_pill = _pill("seo", "SEO", "idle", f"schema {sch.get('built', 0)} built, no sitemap",
                         measured=True, href=href + "#schema")
    else:
        seo_pill = _pill("seo", "SEO", "idle", "Sold, not started", measured=True,
                         detail="No sitemap read and no schema built yet.", href=href + "#schema")

    state = {"behind": "bad", "none": "warn", "current": "ok", "not_sold": "idle"}.get(b.get("state"), "idle")
    if b.get("overdue"):
        value = f"{b['overdue']} overdue"
    elif b.get("state") == "current" and b.get("plan_exhausted"):
        state, value = "warn", "plan has run out"
    elif b.get("state") == "current":
        value = "up to date"
    else:
        value = str(b.get("label") or "—").lower()
    detail = b.get("rule", "")
    if b.get("cadence_source") == "not recorded" and b.get("state") not in ("not_sold",):
        detail = ("No cadence is recorded in the SEO setup, so only planned dates "
                  "are judged. " + detail)
    if b.get("last_posted"):
        detail += (f" Last post marked posted was planned for {b['last_posted']}; "
                   "no publish date is recorded, so that is the planned date.")
    blogs_pill = _pill("blogs", "Blogs", state, value, measured=True, detail=detail,
                       href=href + "#blogs")
    queue = [dict(q, section="seo", href=href + "#" + str(q.get("section") or "record"))
             for q in (h.get("queue") or [])]
    return [seo_pill, blogs_pill], queue, list(h.get("unread") or [])


# ------------------------------------------------------------------ the strip
def client360(name: str, *, group: dict | None = None,
              today: _dt.date | None = None) -> dict:
    """The strip for one client. Never raises; every source names its own failure."""
    name = str(name or "").strip()
    today = today or _dt.date.today()
    if not name:
        return {"ok": False, "error": "A client is required.", "pills": [],
                "queue": [], "unread": [], "measured": False}
    pills: list[dict] = []
    queue: list[dict] = []
    unread: list[str] = []

    g, g_err = _group(name, group)
    if g_err:
        unread.append(g_err)
    prod, bill, q = _products(g, g_err, today)
    pills += [prod, bill]
    queue += q

    try:
        domain = _domain(g)
    except Exception:                            # noqa: BLE001
        domain = ""
    try:
        site, q = _site(domain, g_err)
    except Exception as exc:                     # noqa: BLE001
        site, q = _unread("site", "Website audit", type(exc).__name__), []
    pills.append(site)
    queue += q
    if site["state"] == "unread":
        unread.append(site["detail"])

    pills.append(_google(g, g_err))

    prop, q = _proposals(name)
    pills.append(prop)
    queue += q
    if prop["state"] == "unread":
        unread.append("proposals: " + prop["detail"])

    work, q = _work(name)
    pills.append(work)
    queue += q
    if work["state"] == "unread":
        unread.append("outstanding work: " + work["detail"])

    try:
        seo_pills, q, seo_unread = _seo(name, today.isoformat())
    except Exception as exc:                     # noqa: BLE001
        seo_pills, q, seo_unread = [_unread("seo", "SEO", type(exc).__name__),
                                    _unread("blogs", "Blogs", type(exc).__name__)], [], [type(exc).__name__]
    pills += seo_pills
    queue += q
    unread += seo_unread

    order = {"bad": 0, "warn": 1, "info": 2}
    queue.sort(key=lambda x: order.get(x.get("level"), 3))
    return {
        "ok": True, "client": name,
        "pills": pills, "queue": queue, "unread": unread,
        "measured": not any(p["state"] == "unread" for p in pills),
        "domain": domain,
    }
