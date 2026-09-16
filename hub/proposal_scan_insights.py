"""Two blocks a proposal can carry automatically, from the client's own scan.

**Where you stand** -- the review count, rating and organic reach a
competitive picture is built from, read from `hub.client_brief.build()`
rather than a second walk of the same Insites payload
(`hub/client_context.py`'s own "2a" block explains at length what a second
reader of the same fields costs). Each line is printed only when its own
fields were measured -- an absent fact is left out, never guessed at.

**The SEO & AEO scope** -- small, medium or large, derived in code from the
audit's own fix counts (missing titles, descriptions, H1s, schema items,
alt text, broken links) rather than judged. `scope_for()` shows the
arithmetic behind the tier on the same line it names it, the
`services/provider_check.py` rule that a figure printed with no working is
one nobody can check.

Read first: `hub/website_audit.py` (the spend arithmetic this mirrors the
shape of), `hub/client_brief.py`, `hub/rate_card.py`
(`hub/data/rate_card.json` carries the new, deliberately-placeholder
"SEO & AEO Scope Package" line this scope maps onto).
"""
from __future__ import annotations


def _num(fact):
    if isinstance(fact, dict) and fact.get("value") is not None:
        return fact["value"]
    return None


def where_you_stand(client: str, domain: str = "") -> dict:
    """Competitive-standing lines for a proposal, each shown only when its
    own facts were measured.

    Returns {"lines": [str, ...], "measured": bool}. `measured` is False
    only when nothing at all could be built -- a scan that never ran, or a
    domain with nothing on file -- never a placeholder line invented to
    fill the space. Never raises.
    """
    out = {"lines": [], "measured": False}
    try:
        from hub import client_brief
        brief = client_brief.build(client, domain)
    except Exception:                                     # noqa: BLE001
        return out

    proof = brief.get("proof") or {}
    seo = brief.get("seo") or {}

    review_count = _num(proof.get("review_count"))
    rating = _num(proof.get("review_rating"))
    if review_count is not None and rating is not None:
        out["lines"].append(f"You have {int(review_count)} Google reviews at "
                            f"{rating}★.")
        out["measured"] = True

    more_pct = _num(proof.get("competitors_more_reviews_pct"))
    if more_pct is not None:
        out["lines"].append(f"{more_pct:g}% of local competitors have more "
                            "reviews than you.")
        out["measured"] = True

    traffic = _num(seo.get("average_monthly_traffic"))
    keywords = _num(seo.get("num_keywords_ranked_for"))
    if traffic is not None and keywords is not None:
        out["lines"].append(
            f"Your site draws an estimated {int(traffic):,} organic visits "
            f"a month, ranking for {int(keywords)} keywords.")
        out["measured"] = True
    elif traffic is not None:
        out["lines"].append(
            f"Your site draws an estimated {int(traffic):,} organic visits a month.")
        out["measured"] = True
    elif keywords is not None:
        out["lines"].append(f"Your site ranks for {int(keywords)} keywords.")
        out["measured"] = True

    return out


# House thresholds, not a platform's -- named and shown on the derivation
# line rather than left implicit, the `services/abcd_service.HOUSE_LEGIBILITY`
# rule. A count nobody can act on ("47 things wrong") is a wall; three bands
# with the counts behind them is a scope.
SCOPE_SMALL_MAX = 10
SCOPE_MEDIUM_MAX = 30

_COUNT_LABELS = {
    "pages_missing_title_count": "pages missing a title",
    "pages_missing_description_count": "pages missing a description",
    "pages_missing_h1_count": "pages missing an H1",
    "missing_schema_items": "missing schema items",
    "images_no_alt_count": "images with no alt text",
    "broken_links_count": "broken links",
}


def scope_for(client: str, domain: str = "") -> dict:
    """small/medium/large SEO & AEO scope, derived from the audit's own fix
    counts.

    The pricing-model rule: this computes in code and shows the arithmetic;
    nothing here asks a model to judge the scope. Returns
    {"tier": "small"|"medium"|"large"|None, "total": int|None,
    "counts": {...}, "derivation": [str, ...], "note": str, "measured": bool}.
    `tier` is `None` when nothing could be measured — never defaulted to a
    tier, which would be a confident wrong scope rather than an admission
    the audit has not run. Never raises.
    """
    out = {"tier": None, "total": None, "counts": {}, "derivation": [],
           "note": "", "measured": False}
    try:
        from hub import client_brief
        brief = client_brief.build(client, domain)
    except Exception:                                     # noqa: BLE001
        return out

    seo = brief.get("seo") or {}
    counts: dict[str, int] = {}
    for key in ("pages_missing_title_count", "pages_missing_description_count",
                "pages_missing_h1_count", "missing_schema_items",
                "images_no_alt_count"):
        n = _num(seo.get(key))
        if n is not None:
            counts[key] = int(n)

    # Broken links -- the same field `hub/seo_queue.py` (WO-3b) already
    # reads off this identical audit, read the same way rather than a
    # second scan of the same payload.
    try:
        from hub import seo_queue
        broken = seo_queue.broken_link_count(client, domain)
        if broken is not None:
            counts["broken_links_count"] = int(broken)
    except Exception:                                     # noqa: BLE001
        pass

    if not counts:
        return out

    out["measured"] = True
    out["counts"] = counts
    total = sum(counts.values())
    out["total"] = total
    if total <= SCOPE_SMALL_MAX:
        tier = "small"
    elif total <= SCOPE_MEDIUM_MAX:
        tier = "medium"
    else:
        tier = "large"
    out["tier"] = tier

    derivation = [f"{v} {_COUNT_LABELS.get(k, k)}" for k, v in counts.items() if v]
    out["derivation"] = derivation
    out["note"] = (
        f"{total} fix{'es' if total != 1 else ''} found — " + ", ".join(derivation)
        + f" → {tier} scope (small ≤{SCOPE_SMALL_MAX}, "
          f"medium ≤{SCOPE_MEDIUM_MAX}, large above).")
    return out
