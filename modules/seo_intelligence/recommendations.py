"""Evidence-first SEO opportunity rules.

Rules decide *whether* work is worth recommending. AI can later help draft the
work, but it never gets to manufacture the underlying opportunity.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict

QUESTION_PREFIXES = ("how ", "what ", "why ", "when ", "where ", "who ", "which ", "can ", "does ", "do ", "is ", "are ", "should ")


def _score(impressions, position, ctr_gap=0.0, trend=0.0, commercial=1.0, effort=1.0):
    volume = min(35.0, math.log10(max(10.0, impressions)) * 10.0)
    if position <= 3:
        rank = 8
    elif position <= 10:
        rank = 30
    elif position <= 20:
        rank = 24
    elif position <= 40:
        rank = 12
    else:
        rank = 4
    score = volume + rank + min(20, max(0, ctr_gap * 1000)) + min(10, max(-5, trend * 20))
    score *= max(.6, min(1.35, commercial))
    score /= max(.8, min(1.5, effort))
    return max(1, min(100, round(score)))


def _expected_ctr(position):
    # Conservative heuristic, intentionally not presented as an industry fact.
    curve = {1: .25, 2: .15, 3: .10, 4: .075, 5: .06, 6: .05, 7: .042, 8: .036, 9: .031, 10: .027}
    return curve.get(max(1, min(10, round(position))), .02)


def _key(kind, *parts):
    raw = "|".join([kind] + [str(p or "").strip().lower() for p in parts])
    return f"{kind}:{hashlib.sha1(raw.encode()).hexdigest()[:20]}"


def _rec(kind, title, page_url=None, query=None, priority=50, impact="medium", effort="medium", evidence=None, actions=None):
    return {
        "key": _key(kind, page_url, query, title), "kind": kind, "title": title,
        "page_url": page_url, "query": query, "priority_score": priority,
        "impact": impact, "effort": effort, "evidence": evidence or {},
        "actions": actions or [],
    }


def generate(rows, previous_rows=None):
    """Return prioritized opportunities from query+page Search Analytics rows."""
    previous_rows = previous_rows or []
    prev = {}
    for row in previous_rows:
        keys = row.get("keys") or []
        if len(keys) >= 2:
            prev[(keys[0], keys[1])] = row

    by_page = defaultdict(lambda: {"clicks": 0.0, "impressions": 0.0, "weighted_position": 0.0, "queries": []})
    query_pages = defaultdict(list)
    recommendations = []

    for row in rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        query, page = keys[0], keys[1]
        clicks = float(row.get("clicks") or 0)
        impressions = float(row.get("impressions") or 0)
        ctr = float(row.get("ctr") or 0)
        position = float(row.get("position") or 100)
        if impressions <= 0:
            continue
        old = prev.get((query, page), {})
        old_clicks = float(old.get("clicks") or 0)
        trend = (clicks - old_clicks) / max(1.0, old_clicks) if old else 0.0

        page_data = by_page[page]
        page_data["clicks"] += clicks
        page_data["impressions"] += impressions
        page_data["weighted_position"] += position * impressions
        page_data["queries"].append((query, impressions, clicks, ctr, position, trend))
        query_pages[query].append((page, impressions, clicks, ctr, position))

        if 4 <= position <= 20 and impressions >= 100:
            priority = _score(impressions, position, trend=trend)
            recommendations.append(_rec(
                "page_optimization",
                f"Move ‘{query}’ closer to the top results",
                page, query, priority, "high" if priority >= 75 else "medium", "medium",
                {"impressions": round(impressions), "clicks": round(clicks), "ctr": ctr, "position": round(position, 1), "click_trend": round(trend, 3)},
                ["Review search intent", "Strengthen relevant page copy", "Add contextual internal links", "Review title/H1 alignment"],
            ))

        if position <= 10 and impressions >= 250:
            expected = _expected_ctr(position)
            gap = expected - ctr
            if gap >= .012:
                priority = _score(impressions, position, ctr_gap=gap, effort=.85)
                recommendations.append(_rec(
                    "title_meta",
                    f"Improve search-result click-through for ‘{query}’",
                    page, query, priority, "high" if priority >= 75 else "medium", "low",
                    {"impressions": round(impressions), "clicks": round(clicks), "ctr": ctr, "position": round(position, 1), "heuristic_expected_ctr": expected, "ctr_gap": round(gap, 4)},
                    ["Generate title options", "Generate meta description options", "Check title/search-intent match"],
                ))

        if query.lower().startswith(QUESTION_PREFIXES) and impressions >= 75 and position > 3:
            priority = _score(impressions, position, effort=.8)
            recommendations.append(_rec(
                "faq",
                f"Answer the Google query: {query}",
                page, query, priority, "medium", "low",
                {"impressions": round(impressions), "clicks": round(clicks), "position": round(position, 1)},
                ["Generate concise FAQ answer", "Add to the most relevant page", "Review FAQ structured-data eligibility"],
            ))

    # Page-level title/meta opportunities use total page evidence, not one query.
    for page, d in by_page.items():
        if d["impressions"] < 500:
            continue
        avg_pos = d["weighted_position"] / d["impressions"]
        ctr = d["clicks"] / d["impressions"]
        top_queries = sorted(d["queries"], key=lambda x: x[1], reverse=True)[:8]
        if avg_pos <= 12 and ctr < .025:
            priority = _score(d["impressions"], avg_pos, ctr_gap=.025 - ctr, effort=.8)
            recommendations.append(_rec(
                "page_snippet",
                "Rewrite this page’s title/meta around its strongest real Google queries",
                page, top_queries[0][0] if top_queries else None, priority, "high", "low",
                {"impressions": round(d["impressions"]), "clicks": round(d["clicks"]), "ctr": round(ctr, 4), "position": round(avg_pos, 1), "top_queries": [q[0] for q in top_queries]},
                ["Generate evidence-based title", "Generate meta description", "Preserve the page’s primary intent"],
            ))

        # A group of relevant ranking terms on a page is a useful schema review signal.
        if len(top_queries) >= 3 and d["impressions"] >= 300:
            recommendations.append(_rec(
                "schema_review",
                "Review structured data for this high-visibility page",
                page, top_queries[0][0], min(82, _score(d["impressions"], avg_pos, effort=.9)), "medium", "low",
                {"impressions": round(d["impressions"]), "position": round(avg_pos, 1), "top_queries": [q[0] for q in top_queries[:5]]},
                ["Identify page type", "Recommend only Google-supported applicable schema", "Validate generated JSON-LD before publishing"],
            ))

    # Cannibalization: several URLs materially appearing for the same query.
    for query, pages in query_pages.items():
        meaningful = [p for p in pages if p[1] >= 50 and p[4] <= 30]
        unique = {p[0] for p in meaningful}
        if len(unique) >= 2 and sum(p[1] for p in meaningful) >= 200:
            ranked = sorted(meaningful, key=lambda p: (p[4], -p[1]))
            recommendations.append(_rec(
                "cannibalization",
                f"Review {len(unique)} URLs competing for ‘{query}’",
                ranked[0][0], query, min(95, _score(sum(p[1] for p in meaningful), ranked[0][4])), "high", "medium",
                {"urls": [{"url": p[0], "impressions": round(p[1]), "position": round(p[4], 1)} for p in ranked[:6]]},
                ["Choose the intended ranking page", "Differentiate overlapping intent", "Review internal links/canonicals", "Consider consolidation only after human review"],
            ))

    # Content gaps: query clusters sitting outside page one across existing pages.
    cluster = defaultdict(lambda: {"impressions": 0.0, "queries": [], "best_position": 100.0, "pages": set()})
    stop = {"the", "and", "for", "with", "near", "from", "that", "this", "what", "how", "best", "your", "you"}
    for query, pages in query_pages.items():
        tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2 and t not in stop]
        if not tokens:
            continue
        topic = " ".join(tokens[:3])
        for page, imp, _clicks, _ctr, pos in pages:
            if pos > 10:
                c = cluster[topic]
                c["impressions"] += imp
                c["queries"].append(query)
                c["best_position"] = min(c["best_position"], pos)
                c["pages"].add(page)
    for topic, c in cluster.items():
        if c["impressions"] >= 400 and len(set(c["queries"])) >= 2:
            priority = _score(c["impressions"], c["best_position"], effort=1.2)
            recommendations.append(_rec(
                "content_opportunity",
                f"Evaluate dedicated content for: {topic}",
                next(iter(c["pages"]), None), next(iter(c["queries"]), None), priority, "high" if priority >= 75 else "medium", "high",
                {"combined_impressions": round(c["impressions"]), "best_position": round(c["best_position"], 1), "related_queries": list(dict.fromkeys(c["queries"]))[:12], "existing_urls": list(c["pages"])[:6]},
                ["Check whether an existing page should be expanded first", "If intent is distinct, generate content brief", "Plan internal links and avoid keyword cannibalization"],
            ))

    # De-duplicate identical recommendation keys and sort for the agency queue.
    best = {}
    for rec in recommendations:
        cur = best.get(rec["key"])
        if not cur or rec["priority_score"] > cur["priority_score"]:
            best[rec["key"]] = rec
    return sorted(best.values(), key=lambda r: r["priority_score"], reverse=True)
