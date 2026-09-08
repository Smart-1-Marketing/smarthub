"""One CTA reading, three screens — the Pickaxe CTA Analyzer, grounded.

The harvested prompt (`prompts_harvested.CTA_ANALYZER`) judges the calls to
action on a page. What keeps that judgment honest is that the page is
**measured first**: `modules/ads_builder/landing_page.observe()` fetches the
page and counts its conversion points off the markup — the fetch that module
was written for, because a model handed a bare URL writes confident
recommendations about a page it has never seen. This module never re-fetches
and never re-parses; it is the one place the observation becomes a prompt, so
the SEO client page, the Website Audit tool and the Landing Page Maker cannot
come to feed the model three different accounts of one page.

Four rules, each a way this goes confidently wrong:

* **A page that could not be read is refused, never reviewed anyway.** The
  refusal carries `measured: False` and the fetch's own reason, so the screen
  says "not measured" rather than drawing a review of nothing.
* **The observation travels beside the judgment.** `observed` carries the
  measured CTAs and conversion points with their evidence, `review` carries
  the model's prose, and no screen has to take the model's word for what is
  on the page — the split `modules/ads_builder/landing_page.py` already
  draws for the estimate.
* **The screen that spends is the screen that is billed.** `SCREENS` is an
  allowlist mapping each caller to the module name its spend is recorded
  under; a name the browser supplies would file usage under anything, so an
  unknown screen is refused by name rather than trusted.
* **Placement and design judgments come from the text alone, and the answer
  says so.** The observation is copy and markup, not a rendered view; the
  note rides on every result rather than being left for each screen to
  remember to add.

Billed per press: every caller is a button, never a page load — the
`hub/brand_lookup.py` rule.
"""
from __future__ import annotations

from hub import ai
from hub import prompts_harvested as hp

# Which screens may ask, and the module each one's spend is recorded under.
# The names are the log/usage names those tools already carry.
SCREENS = {
    "seo": "seo",
    "website_audit": "website_audit",
    "landing_maker": "landing_maker",
}

NOTE = ("Reviewed from the page's copy and measured conversion points, "
        "not a rendered view — placement and design comments are inferences "
        "from the text.")


def _page_block(obs: dict) -> str:
    """The `{page_text}` the prompt is handed: measured facts, then copy.

    The CTAs and conversion points lead, each with the evidence
    `observe()` found, so the model is judging what was measured rather
    than re-deriving it from prose. The raw text follows for context.
    """
    lines = []
    points = obs.get("conversion_points") or []
    if points:
        lines.append("Measured conversion points (counted off the markup):")
        for p in points[:40]:
            evidence = str(p.get("evidence") or "").strip()
            lines.append(f"- {p.get('label')}" + (f" — {evidence}" if evidence else ""))
    else:
        lines.append("Measured conversion points: none were found on the page.")
    heads = [h.get("text", "") for h in (obs.get("headings") or [])[:12] if h.get("text")]
    if heads:
        lines.append("\nHeadings, in order: " + " | ".join(heads))
    title = str(obs.get("title") or "").strip()
    if title:
        lines.append("Page title: " + title)
    lines.append("\nThe page's own copy:\n" + str(obs.get("text") or "")[:6000])
    return "\n".join(lines)


def _slim(obs: dict) -> dict:
    """What a screen draws beside the review — the facts, not the whole fetch."""
    return {
        "url": obs.get("url", ""),
        "status": obs.get("status"),
        "redirected": bool(obs.get("redirected")),
        "title": obs.get("title", ""),
        "conversion_points": (obs.get("conversion_points") or [])[:40],
    }


def review(screen: str, *, url: str, client: str = "",
           industry: str = "") -> dict:
    """Fetch, measure, then ask — or refuse with the reason named.

    Returns ``{"ok": True, "review", "observed", "note"}`` or
    ``{"ok": False, "error", "measured"}`` — `measured` is False only when
    the page itself could not be read, so a caller can tell "the page is
    unreachable" from "the model is unavailable", which send somebody to
    two different places.
    """
    module = SCREENS.get(str(screen or ""))
    if not module:
        return {"ok": False, "measured": None,
                "error": f"Unknown screen {screen!r} — not reviewing, and not "
                         "billing a spend nothing can attribute."}
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "measured": None, "error": "A page URL is required."}

    from modules.ads_builder import landing_page
    obs = landing_page.observe(url)
    if not obs.get("measured"):
        reason = str(obs.get("error") or f"HTTP {obs.get('status')}")
        return {"ok": False, "measured": False,
                "error": f"The page could not be read ({reason}), so its CTAs "
                         "are not measured — nothing was reviewed."}
    if not str(obs.get("text") or "").strip():
        return {"ok": False, "measured": False,
                "error": "The page answered but no copy could be read from "
                         "it, so there is nothing to review."}

    spec = hp.CTA_ANALYZER
    prompt = spec["prompt"].format(
        client=str(client or "").strip() or "not recorded",
        url=obs.get("url") or url,
        industry=str(industry or "").strip() or "not recorded",
        page_text=_page_block(obs),
    )
    try:
        text = ai.chat([{"role": "user", "content": prompt}],
                       module=module, purpose=spec["purpose"],
                       temperature=spec["temperature"], max_tokens=1500)
    except ai.AIUnavailable as exc:
        return {"ok": False, "measured": True,
                "error": f"The page was read but the review could not be "
                         f"written: {exc}"}
    return {"ok": True, "review": text.strip(), "observed": _slim(obs),
            "note": NOTE}
