"""Structured OpenAI calls that write a small set of copy lines from a
brief, never a full per-scene script -- WO-CS8 item 3 and WO-CS9 item 3.

"Generate all drafts" (WO-CS8) is ONE campaign-level call: it reads the
campaign's own offer/CTA and whatever the client's Brand Kit already
knows, and returns the handful of short lines every seed template's
variables actually need (headline, subheadline, body, offer, cta) --
because a campaign has no scenes of its own; each asset's storyboard is a
different template with a different beat count, and it is `binder.bind()`
-- called once per asset, with no further model call -- that turns this
shared brief into one.

"Create Weather Set" (WO-CS9) is the same shape one level down: ONE call
per project returns a variant of headline/offer/cta for every weather
condition the project's own industry pack has not suppressed.
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


_WEATHER_SYSTEM_PROMPT = (
    "You write short commercial ad copy variants for a local business, one "
    "per weather condition. Return strict JSON keyed by condition, each "
    "value an object with headline, offer, cta -- short lines, never a "
    "paragraph. Use ONLY the base offer/CTA given, adapted to the "
    "condition's own angle; never invent a price, a percentage or a "
    "deadline that was not supplied. For the 'severe' condition specifically: "
    "write a safety-first message with NO offer and NO call to action to "
    "buy anything -- severe weather copy is never a sales pitch."
)


def generate_weather_variants(project, pack: dict) -> dict:
    """{condition: {headline, offer, cta}} for every condition
    `pack["weather_copy"]` names -- WO-CS9 item 3. One call for the whole
    set, the same "one call, several derived answers" shape
    `generate_campaign_brief` already uses, so a project with all seven
    conditions open costs one billed request rather than seven.

    The severe rule is enforced here in code, not only asked for in the
    prompt: `offer` is blanked for `severe` regardless of what the model
    returns, the same "a prompt is a request, not a guarantee" reasoning
    this codebase applies to Reg Z and every other compliance guardrail --
    a model that ignores the instruction must not be the last word on
    whether severe-weather copy sells something.
    """
    from hub import ai

    client = project.client_name or ""
    kit = {}
    if client:
        try:
            from . import brand_ext
            kit = brand_ext.kit(client, "")
        except Exception:                                  # noqa: BLE001
            kit = {}

    conditions = list((pack or {}).get("weather_copy") or {})
    if not conditions:
        return {}

    facts = {
        "business_name": kit.get("name") or client or "Smart 1 Marketing",
        "base_offer": (project.brief or {}).get("offer") or "",
        "base_cta": (project.brief or {}).get("cta") or "",
        "conditions": {c: pack["weather_copy"][c] for c in conditions},
    }
    messages = [
        {"role": "system", "content": _WEATHER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(facts)},
    ]
    raw = ai.chat_json(messages, module="creative_studio", purpose="weather_set")

    out = {}
    for condition in conditions:
        row = raw.get(condition) or {}
        out[condition] = {
            "headline": str(row.get("headline") or "").strip(),
            "offer": "" if condition == "severe" else str(row.get("offer") or "").strip(),
            "cta": str(row.get("cta") or "").strip(),
        }
    return out
