""""What needs doing" rows for the SEO client page, from the scan itself.

`hub/seo.py`'s `record_health()` already draws a queue -- schema pages
remaining, blogs overdue, FAQs waiting, llms.txt gone stale -- built from
this Hub's own trackers. It never reads the audit's own fix counts: a site
with forty images missing alt text and six pages with no title tag opened
the SEO client page showing nothing to do, because none of that had a
tracker of its own.

`rows()` is that other half, read straight from `hub.client_brief.build()`'s
`seo` section rather than a second dotted-path walk of the same Insites
payload (`hub/client_context.py`'s own "2a" block explains at length what a
second reader of the same fields costs). Each row names the tool that
clears it and links into the section that does. `record_health()` folds
these into its own `queue` so the page needs no second card.

Absent is not zero: a client with no scan on file gets `[]`, and the page
must read that as "not measured", never as "nothing outstanding" --
`measured_for()` is the companion answer that tells the two apart.
"""
from __future__ import annotations

# Each entry: (brief key inside "seo", label, section, tool, link builder).
# `count_missing_schema_items` is `hub/client_brief.py`'s own `missing_schema_items`
# -- the same Insites field, dotted-path name kept in hub/scan_facts.py alone.
_COUNT_ROWS = (
    ("images_no_alt_count", "image{s} missing alt text", "warn", "alt",
     "SEO Image Pipeline", "/tools/seo-images/"),
    ("images_to_optimise_count", "image{s} that could be optimized", "info", "images",
     "SEO Image Pipeline", "/tools/seo-images/"),
    ("pages_missing_title_count", "page{s} missing a title tag", "bad", "titles",
     "SEO client page", "/seo/client"),
    ("pages_missing_description_count", "page{s} missing a meta description", "bad", "descriptions",
     "SEO client page", "/seo/client"),
    ("pages_missing_h1_count", "page{s} missing an H1", "warn", "headings",
     "SEO client page", "/seo/client"),
    ("missing_schema_items", "missing schema item{s}", "warn", "schema",
     "Schema Builder", "/seo/client"),
)

# Boolean checks: measured False is the finding; measured True or absent is
# fine and produces no row. Each carries its own severity, because "no
# sitemap" (search engines cannot find every page) is not the same weight as
# "no Open Graph tags" (a social share looks plain).
_FLAG_ROWS = (
    ("has_sitemap", "No sitemap was found", "bad", "sitemap",
     "SEO client page", "/seo/client"),
    ("has_og_tags", "No Open Graph tags on the site", "info", "og",
     "SEO client page", "/seo/client"),
    ("is_voice_search_optimised", "Not optimized for voice search", "info", "voice",
     "SEO client page", "/seo/client"),
)


def _plural(label: str, n: int) -> str:
    return label.format(s="s are" if n != 1 else " is")


def _link(base: str, client: str) -> str:
    if base == "/seo/client":
        from urllib.parse import quote
        return f"{base}?name={quote(client)}"
    return base


def _broken_links(client: str, domain: str) -> int | None:
    """The scan's own broken-link count -- the same field
    `hub/upsell.py`'s `_traffic()` reads off the identical audit."""
    try:
        from hub import scan_facts
        from hub.website_audit import _get, _n                # noqa: PLC2701
        report, _meta, err = scan_facts.latest_report(domain or client)
        if err or not report:
            return None
        return _n(_get(report, "broken_links.num_broken_links"))
    except Exception:                                     # noqa: BLE001
        return None


def broken_link_count(client: str, domain: str = "") -> int | None:
    """Public wrapper -- `hub/proposal_scan_insights.py` reuses this rather
    than re-reading the audit."""
    return _broken_links(client, domain)


def measured_for(client: str, domain: str = "") -> bool:
    """Was there anything to measure this queue from at all?

    A client whose scan never ran, or one whose only scan errored, gets
    `rows() == []` for the same reason a fresh business has nothing
    outstanding — and the two must not read alike. This is `True` the
    moment `hub.client_brief.build()`'s `seo` section carries anything,
    whatever the individual counts turn out to be.
    """
    try:
        from hub import client_brief
        brief = client_brief.build(client, domain)
    except Exception:                                     # noqa: BLE001
        return False
    seo = brief.get("seo") or {}
    return seo.get("measured") is not False and bool(seo)


def rows(client: str, domain: str = "") -> list[dict]:
    """Severity-striped rows for the SEO client page's queue.

    Each row: {"level": "bad"|"warn"|"info", "section": str, "title": str,
    "detail": str, "tool": str, "link": str}. Never raises; a brief that
    could not be built, or a "seo" section the scan never measured, returns
    `[]` -- callers use `measured_for()` to tell that apart from a client
    with genuinely nothing outstanding.
    """
    client = str(client or "").strip()
    if not client:
        return []
    try:
        from hub import client_brief
        brief = client_brief.build(client, domain)
    except Exception:                                     # noqa: BLE001
        return []

    seo = brief.get("seo") or {}
    if seo.get("measured") is False or not seo:
        return []

    def val(key):
        fact = seo.get(key)
        return fact.get("value") if isinstance(fact, dict) else None

    out: list[dict] = []
    for key, label, level, section, tool, base in _COUNT_ROWS:
        n = val(key)
        if not isinstance(n, (int, float)) or n <= 0:
            continue
        n = int(n)
        out.append({
            "level": level, "section": section,
            "title": f"{n} {_plural(label, n)}",
            "detail": f"Clear these in {tool}.",
            "tool": tool, "link": _link(base, client),
        })

    for key, title, level, section, tool, base in _FLAG_ROWS:
        v = val(key)
        if v is False:
            out.append({
                "level": level, "section": section, "title": title,
                "detail": f"Fix this from {tool}.",
                "tool": tool, "link": _link(base, client),
            })

    broken = _broken_links(client, domain)
    if broken:
        out.append({
            "level": "bad", "section": "broken_links",
            "title": f"{broken} broken link{'s' if broken != 1 else ''} on the site",
            "detail": "From the last audit.",
            "tool": "SEO client page", "link": _link("/seo/client", client),
        })

    # llms.txt staleness and blogs overdue are already this Hub's own
    # trackers -- read the same way `hub/seo.py`'s record_health() already
    # does, rather than a second copy of either rule.
    try:
        from . import seo as _seo, llms_hosting as _lh
        store = _seo.load_store(client) or {}
        pub = _lh.published(client)
        if pub:
            days = _seo._days_since(pub.get("at", ""))           # noqa: PLC2701
            if days is not None and days >= _seo.LLMS_STALE_DAYS:
                out.append({
                    "level": "info", "section": "llms",
                    "title": f"llms.txt is {days} days old",
                    "detail": "Worth rebuilding if pages have been added since.",
                    "tool": "SEO client page", "link": _link("/seo/client", client),
                })
        blogs = _seo.blogs_health(store, store.get("blogs") or {})
        if blogs.get("overdue"):
            n = blogs["overdue"]
            out.append({
                "level": "bad", "section": "blogs",
                "title": f"{n} blog post{'s are' if n != 1 else ' is'} past due",
                "detail": "Planned, and not marked posted on the site.",
                "tool": "SEO client page", "link": _link("/seo/client", client),
            })
    except Exception:                                     # noqa: BLE001
        pass

    order = {"bad": 0, "warn": 1, "info": 2}
    out.sort(key=lambda r: order.get(r["level"], 3))
    return out


def topic_ideas(client: str, domain: str = "") -> dict:
    """Suggested blog/FAQ topics from the scan's own keyword findings.

    AI-assisted, never auto-published: these are candidate titles pulled
    straight from `organic_search.best_keyword_opportunities` and
    `.target_keywords_detail`, offered as a starting point for the FAQ
    Builder or the blog planner -- a person still writes and approves
    whatever comes of them.
    """
    client = str(client or "").strip()
    out = {"topics": [], "measured": False}
    if not client:
        return out
    try:
        from hub import client_brief
        brief = client_brief.build(client, domain)
    except Exception:                                     # noqa: BLE001
        return out

    seo = brief.get("seo") or {}
    if seo.get("measured") is False:
        return out

    topics: list[str] = []
    for key in ("best_keyword_opportunities", "target_keywords"):
        fact = seo.get(key)
        value = fact.get("value") if isinstance(fact, dict) else None
        if not value:
            continue
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in topics:
                topics.append(part)
    out["topics"] = topics[:10]
    out["measured"] = bool(topics)
    return out
