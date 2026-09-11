"""The one OpenAI call a campaign gets -- WO-CS8 item 3.

"Generate all drafts" is ONE campaign-level script generation, then every
asset's own brief is DERIVED from it rather than asked for again. This is
that one call: it reads the campaign's own offer/CTA and whatever the
client's Brand Kit already knows, and returns the handful of short lines
every seed template's variables actually need (headline, subheadline,
body, offer, cta) -- never a full per-scene script, because a campaign has
no scenes of its own; each asset's storyboard is a different template with
a different beat count, and it is `binder.bind()` -- called once per asset,
with no further model call -- that turns this shared brief into one.
"""
from __future__ import annotations

import json

_SYSTEM_PROMPT = (
    "You write short, punchy commercial ad copy for a local business. "
    "Return strict JSON with exactly these keys: headline, subheadline, "
    "body, offer, cta. Each value is a single short line of copy, never a "
    "paragraph -- these are read aloud or shown on screen for a few "
    "seconds. Use ONLY the offer and CTA given; never invent a price, a "
    "percentage, or a deadline that was not supplied."
)

_FIELDS = ("headline", "subheadline", "body", "offer", "cta")


def generate_campaign_brief(campaign) -> dict:
    """{headline, subheadline, body, offer, cta}, every value a plain
    string. Raises `hub.ai.AIUnavailable` (or whatever the provider layer
    raises) rather than returning a partial or invented answer -- the
    caller decides what "the model did not answer" means for a batch of
    assets, this function never guesses on its own."""
    from hub import ai

    client = campaign.client_name or ""
    kit = {}
    if client:
        try:
            from . import brand_ext
            kit = brand_ext.kit(client, "")
        except Exception:                                  # noqa: BLE001
            kit = {}

    facts = {
        "business_name": kit.get("name") or client or "Smart 1 Marketing",
        "offer": campaign.offer or "",
        "cta": campaign.cta or "",
        "what_they_do": (kit.get("description") or "")[:400],
    }
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(facts)},
    ]
    raw = ai.chat_json(messages, module="creative_studio", purpose="campaign_draft")
    return {k: str(raw.get(k) or "").strip() for k in _FIELDS}
