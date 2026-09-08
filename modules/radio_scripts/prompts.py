"""The prompt this tool sends, and nothing else that decides what comes back.

The Playbook's own podcast work already learned this lesson: a loose prompt
returns national, generic, and occasionally the wrong sport. So the market,
the team and the local shows are stated as facts the copy is *required* to
use, not offered as colour the model may reach for -- and `engine.py` checks
the string actually landed rather than trusting that it did.
"""
from __future__ import annotations

from . import engine_spec as spec


def _shows_line(shows: list[str]) -> str:
    if not shows:
        return "No local show names were supplied. Do not invent one."
    return ("Local shows / programs this audience listens to: "
            + "; ".join(shows) + ". Name at least one of these somewhere in "
            "the three concepts.")


def _offer_line(offer: str) -> str:
    if not offer:
        return ("No offer, price or discount was supplied. Do NOT invent one "
                "-- the tag closes on the phone number and web address only.")
    return f"The offer to close on is exactly this, stated plainly: {offer!r}"


def build_prompt(brief: dict) -> str:
    company = str(brief.get("company") or brief.get("client_name") or "the client").strip()
    market = str(brief.get("market") or "").strip() or "their local market"
    team = str(brief.get("team") or "").strip()
    package = str(brief.get("package") or "").strip()
    shows = [s.strip() for s in (brief.get("local_shows") or []) if str(s).strip()]
    offer = str(brief.get("offer") or "").strip()
    phone = str(brief.get("phone") or "").strip()
    url = str(brief.get("url") or "").strip()
    funnel = brief.get("funnel") or {}

    facts = [f"Client / advertiser: {company}", f"Market: {market}"]
    if team:
        facts.append(f"Sponsorship / team tie-in: {team}. Name it somewhere in "
                     "the three concepts -- this is why the client bought in.")
    if package:
        facts.append(f"Package: {package}")
    for key, label in (("fan_homes", "fan homes reached"),
                       ("reachable_homes", "reachable homes in market"),
                       ("screens", "stadium/venue screens")):
        if funnel.get(key):
            facts.append(f"{label.capitalize()}: {funnel[key]}")
    if phone:
        facts.append(f"Phone number to close on: {phone}")
    if url:
        facts.append(f"Web address to close on, spoken aloud: {url}")

    return "\n".join([
        "You are a senior radio and streaming-audio copywriter for Smart 1 "
        "Marketing, writing spots for local advertisers.",
        "",
        "\n".join(facts),
        _shows_line(shows),
        _offer_line(offer),
        "",
        "Write exactly 3 distinct creative concepts. Each concept is ONE "
        "idea rendered at three lengths -- the :30 is a genuine cutdown of "
        "the :60's own idea, and the :15 a cutdown of that, never a fresh "
        "direction.",
        "",
        "Word counts, counted by splitting on whitespace, are hard limits:",
        f"  :60 -- {spec.WORD_BUDGETS['60'][0]}-{spec.WORD_BUDGETS['60'][1]} words",
        f"  :30 -- {spec.WORD_BUDGETS['30'][0]}-{spec.WORD_BUDGETS['30'][1]} words",
        f"  :15 -- {spec.WORD_BUDGETS['15'][0]}-{spec.WORD_BUDGETS['15'][1]} words",
        "",
        "Never write a superlative that cannot be substantiated -- no "
        "\"#1\", \"best in town\", \"guaranteed\", \"award-winning\" and the "
        "like -- and never state a price, percentage or deadline that was "
        "not supplied above.",
        "",
        "Each concept needs: an idea name (a few words), talent direction "
        "(read style and pace, gender-neutral unless the brief above names "
        "a team or sponsorship voice that implies otherwise), SFX/bed notes, "
        "and a tag block with the advertiser name, the offer text (empty "
        "string if none was supplied), a short call to action, and the "
        "spoken form of the phone number and web address.",
        "",
        "Return STRICT JSON only, no markdown fences, in exactly this shape:",
        '{"concepts": [{"idea": "", "talent_direction": "", "sfx_notes": "", '
        '"scripts": {"60": "", "30": "", "15": ""}, "tag": {"name": "", '
        '"offer": "", "cta": "", "phone_spoken": "", "url_spoken": ""}}]}',
    ])


def build_fix_prompt(brief: dict, misses: list[dict]) -> str:
    """One follow-up call naming exactly which lengths missed their budget.

    A second full generation would drift from the concept a rep may already
    like; this asks only for the fields that failed, against the same facts.
    """
    lines = ["The scripts below missed their word-count budget. Rewrite ONLY "
            "the named length for each, keeping the same idea, the same "
            "advertiser facts, and the same rules against invented prices "
            "and unsubstantiated claims:", ""]
    for miss in misses:
        low, high = spec.WORD_BUDGETS[miss["length"]]
        lines.append(f"- concept {miss['concept_index']}, :{miss['length']} "
                     f"(currently {miss['words']} words, needs {low}-{high}): "
                     f"{miss['text']!r}")
    lines.append("")
    lines.append(build_prompt(brief).split("Return STRICT JSON only")[0])
    lines.append("Return STRICT JSON only, no markdown fences, in exactly "
                "this shape: "
                '{"fixes": [{"concept_index": 0, "length": "60", "text": ""}]}')
    return "\n".join(lines)


__all__ = ["build_prompt", "build_fix_prompt"]
