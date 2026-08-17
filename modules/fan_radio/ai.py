"""Fan Radio — the writing.

Two calls to OpenAI, both plain `requests` against chat completions so the
module adds no dependency the Hub doesn't already carry.

1. ``read_brief`` — reads the client's own site once and turns it into the
   facts a spot needs: who they are, what the offer is, what must be said
   verbatim, what to avoid.
2. ``write_spot`` — writes one script for one daypart at one length, to the
   word budget, with the trademark rules in the system prompt *and* enforced
   afterwards by ``phrases.scan``. Belt and braces on purpose: a model that
   has read a million football ads will reach for a club nickname unless
   something stops it twice.

When there's no key, or the call fails, the module returns a plainly
labelled template instead of raising — but it sets ``ai: False`` on the
result and the UI shows it. The Suite audit found three apps silently
swapping a hardcoded template in for a failed AI call with no signal to
anybody; that is the one failure mode not repeated here.
"""
from __future__ import annotations

import json
import os
import re

import requests

from . import catalog, phrases

API_URL = "https://api.openai.com/v1/chat/completions"


def model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def ready() -> bool:
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())


class AIUnavailable(RuntimeError):
    """Raised internally; callers fall back to a labelled template."""


def _chat(system: str, user: str, timeout: int = 60,
          max_tokens: int = 1200) -> dict:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise AIUnavailable("OPENAI_API_KEY is not set.")
    try:
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": model(), "temperature": 0.8,
                  "max_tokens": max_tokens,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=timeout)
    except requests.RequestException as exc:
        raise AIUnavailable(f"Couldn't reach OpenAI ({exc.__class__.__name__}).")
    if r.status_code != 200:
        # Deliberately does not carry the provider body forward — the audit
        # found an API key prefix reaching a public page this way.
        raise AIUnavailable(f"OpenAI returned {r.status_code}.")
    try:
        try:  # record spend so /diagnostics doesn't under-report
            from hub import ai as _hub_ai
            _hub_ai.note_usage("fan_radio", r.json(), purpose="script")
        except Exception:  # noqa: BLE001
            pass
        body = r.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise AIUnavailable("The model's answer was cut short. Try again.")
        return json.loads(choice["message"]["content"])
    except (KeyError, IndexError, ValueError):
        raise AIUnavailable("OpenAI sent back something unreadable.")


# ------------------------------------------------------------------ brief
_BRIEF_SYSTEM = """You read a local business's own website and extract only \
what a 15 or 30 second radio commercial needs. You never invent a fact, an \
offer, a price, a phone number or a claim that is not on the page. If \
something is not there, leave it out.

Return JSON:
{"summary": "one sentence on what they do",
 "audience": "who buys from them",
 "offer": "the current promotion if one is stated, else empty string",
 "differentiators": ["3-5 short phrases"],
 "callToAction": "what a listener should do",
 "mustSay": ["exact strings that must appear verbatim: phone, address, url"],
 "avoid": ["claims or words to keep out"],
 "recommendedTones": [{"toneId": "...", "why": "..."}]}

toneId must be one of the ids given to you."""


def read_brief(company: str, url: str, page_text: str,
               notes: str = "") -> dict:
    tone_ids = ", ".join(catalog.TONE_IDS)
    user = (f"Business: {company}\nWebsite: {url}\n"
            f"Available toneIds: {tone_ids}\n"
            f"Notes from the account manager: {notes or '(none)'}\n\n"
            f"Page text:\n{page_text[:9000]}")
    try:
        out = _chat(_BRIEF_SYSTEM, user, max_tokens=900)
        out["ai"] = True
        return out
    except AIUnavailable as exc:
        return {
            "ai": False,
            "ai_reason": str(exc),
            "summary": f"{company} — fill this in; the site read didn't run.",
            "audience": "", "offer": "",
            "differentiators": [], "callToAction": "",
            "mustSay": [], "avoid": [],
            "recommendedTones": [{"toneId": "warm", "why": "safe default"}],
        }


# ------------------------------------------------------------------ spots
_SPOT_SYSTEM = """You write local radio commercials with a football feel for \
businesses that are NOT affiliated with any team, league, school or \
broadcaster.

HARD RULES — a script breaking any of these is rejected:
1. Never name or allude to a specific team, club, mascot, league, \
conference, bowl, championship event, broadcast package or school. No \
nicknames, no fan slogans, no chants, no traditions, no player or coach \
names, no stadium names.
2. Never imply sponsorship, partnership, endorsement or official status.
3. Use only generic football language — the sport, not anybody's brand.
4. Say every string in mustSay exactly as written, once each.
5. Write to the word budget you are given. Count your words.

Write for the ear: short sentences, one idea each, contractions, the way a \
person actually talks. No stage directions, no "SFX", no speaker labels, no \
timestamps — just the words the voice reads.

Return JSON:
{"script": "the words only",
 "hook": "the opening line, repeated",
 "phrasesUsed": ["which of the supplied football phrases you used"],
 "notes": "one line on the read: pace, emphasis"}"""


def _spot_user(brief: dict, dp: dict, seconds: int, tone: dict,
               outcome: str, safe_bank: list[str], banned: list[str],
               steer: str = "") -> str:
    b = catalog.budget(seconds)
    lines = [
        f"LENGTH: {b['label']} — write {b['min']} to {b['max']} words. This "
        f"is the whole job; a script outside that range is unusable.",
        f"DAYPART: {dp['label']} — airs {dp['when']}.",
        f"WHAT THIS SPOT IS DOING: {dp['job']}",
        f"ANGLE: {dp['angle']}",
        f"CALL TO ACTION SHAPE: {dp['cta_shape']}",
        f"TONE: {tone['label']} — {tone['hint']}",
        "",
        f"BUSINESS: {brief.get('summary', '')}",
        f"AUDIENCE: {brief.get('audience', '')}",
        f"OFFER: {brief.get('offer') or '(no stated offer — sell the business)'}",
        f"WHAT MAKES THEM DIFFERENT: {'; '.join(brief.get('differentiators') or []) or '(none given)'}",
        f"CALL TO ACTION: {brief.get('callToAction', '')}",
        f"MUST SAY VERBATIM: {'; '.join(brief.get('mustSay') or []) or '(nothing)'}",
        f"AVOID: {'; '.join(brief.get('avoid') or []) or '(nothing)'}",
        "",
        "FOOTBALL LANGUAGE YOU MAY USE (pick two or three, don't cram them "
        "all in): " + ", ".join(safe_bank),
    ]
    if banned:
        lines += ["",
                  "NEVER SAY THESE — they identify a team and will get the "
                  "spot pulled: " + ", ".join(banned)]
    if outcome == "neutral":
        lines += ["",
                  "RESULT-NEUTRAL: this is recorded days before it airs. It "
                  "cannot say or imply how the game went — no win, no loss, "
                  "no streak, no score."]
    elif outcome == "win":
        lines += ["", "Assume the weekend went well. Keep it warm, not "
                      "gloating, and still name no team."]
    elif outcome == "loss":
        lines += ["", "Assume the weekend went badly. Commiserate lightly, "
                      "no mockery, and still name no team."]
    if steer:
        lines += ["", f"EXTRA DIRECTION FROM THE ACCOUNT MANAGER: {steer}"]
    return "\n".join(lines)


def write_spot(brief: dict, daypart_id: str, seconds: int, tone_id: str,
               outcome: str = "neutral", banned: list[str] | None = None,
               steer: str = "", attempts: int = 2) -> dict:
    """Write one spot, re-asking once if the trademark scan finds a hit."""
    dp = catalog.daypart(daypart_id)
    tone = catalog.tone(tone_id)
    bank = phrases.suggest(daypart_id, 10)
    banned = banned or []
    user = _spot_user(brief, dp, seconds, tone, outcome, bank, banned, steer)

    last_err = ""
    for attempt in range(max(1, attempts)):
        try:
            out = _chat(_SPOT_SYSTEM, user, max_tokens=700)
        except AIUnavailable as exc:
            last_err = str(exc)
            break
        script = str(out.get("script") or "").strip()
        check = phrases.scan(script, banned, daypart_id, outcome)
        if check["clean"] or attempt == attempts - 1:
            out.update({"script": script, "ai": True, "attempts": attempt + 1})
            return out
        # One targeted re-ask naming exactly what it broke.
        hits = ", ".join(h["term"] for h in check["blocked"])
        user += (f"\n\nYour last draft used: {hits}. That is a trademark and "
                 f"cannot air. Rewrite it completely without those words, "
                 f"same length, same job.")

    return {
        "ai": False,
        "ai_reason": last_err or "The writer couldn't produce a clean script.",
        "script": _template(brief, dp, seconds, outcome),
        "hook": "", "phrasesUsed": [], "attempts": attempts,
        "notes": "Template — not written by the AI. Edit before sending.",
    }


def tighten(script: str, seconds: int, cut: int, tone_id: str,
            banned: list[str] | None = None) -> dict:
    """Cut N words without losing the must-says. Never truncates."""
    b = catalog.budget(seconds)
    system = ("You tighten radio scripts. Return JSON "
              '{"script": "...", "whatWentAndWhy": "one line"}. '
              "Keep every phone number, address, price and web address "
              "exactly as written. Never name a team, league or school. "
              "Never cut the call to action.")
    user = (f"Cut about {cut} word(s) to land between {b['min']} and "
            f"{b['max']} words for a {b['label']} read. Tone: "
            f"{catalog.tone(tone_id)['label']}.\n\nScript:\n{script}")
    try:
        out = _chat(system, user, max_tokens=600)
        out["ai"] = True
        return out
    except AIUnavailable as exc:
        return {"ai": False, "ai_reason": str(exc), "script": script,
                "whatWentAndWhy": "Couldn't reach the writer — script "
                                  "returned unchanged rather than trimmed, "
                                  "because trimming clips a word."}


def voice_profile(brief: dict, tone_id: str) -> dict:
    """What kind of voice this read wants — fed to the ElevenLabs matcher."""
    system = ("You cast voice-over. Return JSON {\"recommendation\": "
              "{\"gender\": \"male|female|any\", \"age\": \"young|middle_aged|old\", "
              "\"accent\": \"american|british|australian|any\", "
              "\"energy\": \"laid_back|conversational|energetic|explosive\", "
              "\"delivery\": \"announcer|narrator|best_friend|spokesperson|character\"}, "
              "\"why\": \"one line\", \"searchTerms\": [\"...\"]}")
    user = (f"Business: {brief.get('summary', '')}\n"
            f"Audience: {brief.get('audience', '')}\n"
            f"Tone: {catalog.tone(tone_id)['label']} — "
            f"{catalog.tone(tone_id)['hint']}\n"
            f"This is a football-themed local radio spot.")
    try:
        out = _chat(system, user, max_tokens=400)
        out["ai"] = True
        return out
    except AIUnavailable as exc:
        return {"ai": False, "ai_reason": str(exc),
                "recommendation": {"gender": "any", "age": "middle_aged",
                                   "accent": "american",
                                   "energy": "energetic",
                                   "delivery": "announcer"},
                "why": "Default casting — the AI call didn't run.",
                "searchTerms": ["commercial", "announcer"]}


# --------------------------------------------------------------- fallback
def _template(brief: dict, dp: dict, seconds: int, outcome: str) -> str:
    """A plainly-labelled starting point. Never presented as AI output."""
    name = re.sub(r"\s+", " ", str(brief.get("summary") or "Your business")).strip()
    cta = brief.get("callToAction") or "Stop in this week."
    must = (brief.get("mustSay") or [""])[0]
    opens = {
        "pregame": "Before kickoff this weekend, get it handled.",
        "gameday": "It's gameday — and we're open.",
        "postgame": "However it ended, the week starts over tomorrow.",
    }
    body = {15: "{open} {name}. {cta} {must}",
            30: ("{open} {name}. {cta} No fuss, no waiting, and you'll be "
                 "back before the coin toss. {must}")}
    text = body.get(int(seconds), body[30]).format(
        open=opens.get(dp["id"], opens["gameday"]),
        name=name, cta=cta, must=must)
    return re.sub(r"\s{2,}", " ", text).strip()
