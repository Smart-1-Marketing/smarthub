"""The business description — one prompt, one set of rules.

The IO Builder's `/api/generate-business-description` carries the only
instructions we have that a description must actually satisfy: Google Business
Profile's "from the business" rules, roughly 500-750 characters, third person,
no URLs, no phone numbers, no prices, no promotional language, and — the part
that matters most — nothing invented. Those instructions came from getting
descriptions rejected.

The Proposal Builder wrote its own, looser prompt. So the same client could
get one description on the proposal and a different one on the IO, and only
the IO's followed the rules that make it publishable. Worse, the proposal
builder's route did not exist: the button called a path on the IO app that has
never had it, so the fallback placeholder ran every single time.

Both tools now call `prompt_for()` here, so the rules land once. It also knows
about multiple target areas — "the areas or locations it serves" is a Google
requirement, and a three-location client whose description names one location
is a description that under-sells them.
"""
from __future__ import annotations

from . import target_areas as _areas

# Roughly what Google accepts. Stated in the prompt rather than trimmed after
# the fact: a description cut to length mid-sentence reads worse than a short
# one, and this is copy a client will publish.
MIN_CHARS = 500
MAX_CHARS = 750


def _clean(value) -> str:
    return str(value or "").strip()


def prompt_for(urls, client: str = "", industry: str = "", areas=None,
               brandfetch=None, geo: str = "") -> str:
    """The prompt both builders send. `areas` is a target-area list.

    `geo` remains accepted for the single-geography callers that predate
    `hub.target_areas`; it is used only when no areas are supplied.
    """
    urls = [_clean(u) for u in (urls or []) if _clean(u)]
    brand = brandfetch if isinstance(brandfetch, dict) else {}
    served = _areas.for_prompt(areas) or _clean(geo)
    area_rows = _areas.normalize(areas)

    # Multi-area clients get an explicit instruction rather than a longer
    # list, because a model handed several place names tends to pick one and
    # write about that market.
    coverage = ""
    if len(area_rows) > 1:
        coverage = (
            f'This business serves {len(area_rows)} distinct target areas '
            f'({_areas.summary(area_rows, limit=len(area_rows))}). Describe the '
            'service area in a way that covers all of them — name the areas '
            'if it reads naturally, or describe the region they form. Do not '
            'write as though the business serves only one of them.\n'
        )

    return (
        'Research this business carefully using its official website and any clearly authoritative pages linked from it: '
        + ', '.join(urls) + '.\n'
        'Write a customer-facing business description suitable for a Google Business Profile "from the business" section. '
        f'Requirements: write in third person about the business; keep it to roughly {MIN_CHARS} to {MAX_CHARS} characters '
        f'(Google allows up to {MAX_CHARS}); '
        'clearly describe what the business offers, the products or services provided, who it serves, the areas or locations it serves, '
        'and what makes it a good choice, using natural language a prospective customer would find helpful. '
        'Follow Google Business Profile content rules: do NOT include URLs or website links, phone numbers, prices, promotional or sales language '
        '(no "call now", "best", "#1", discounts, or offers), special characters, or ALL-CAPS gimmicks. Keep it factual and professional. '
        'Do not invent unsupported claims, awards, service areas, years in business, or capabilities. '
        'Do not include citations or raw URLs.\n'
        + coverage +
        f'Known intake details:\nClient name: {_clean(client)}\nIndustry: {_clean(industry)}\nGeographic target: {served}\n'
        f'Brandfetch description: {_clean(brand.get("description"))}\n'
        'Return only the finished Google Business Profile description.'
    )


def check(description: str) -> list[str]:
    """What about a finished description would get it rejected.

    Advisory, not a gate. The model follows the rules nearly always; when it
    does not, a rep who is about to paste this into Google should be told
    which rule it broke rather than finding out from Google days later.
    """
    import re

    text = _clean(description)
    if not text:
        return []
    problems = []
    if len(text) > MAX_CHARS:
        problems.append(f"{len(text)} characters — Google's limit is {MAX_CHARS}.")
    if re.search(r'https?://|www\.|\.com\b|\.net\b|\.org\b', text, re.I):
        problems.append("Contains a website address — Google rejects URLs here.")
    if re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', text):
        problems.append("Contains a phone number — not allowed in this section.")
    if re.search(r'\$\s?\d', text):
        problems.append("Contains a price — not allowed in this section.")
    promo = re.findall(r'\b(call now|best|#1|number one|cheapest|discount|sale|'
                       r'free quote|guaranteed|act now|limited time)\b', text, re.I)
    if promo:
        problems.append("Promotional language Google flags: "
                        + ", ".join(sorted({p.lower() for p in promo})) + ".")
    return problems
