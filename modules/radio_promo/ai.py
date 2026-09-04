"""OpenAI work for the Radio Promo tool.

Ported from radio-studio's `src/services/openai.js`. The prompts are the
studio's, near word for word — they are the part that took the tuning, and
changing them would change how the spots read.

Every function raises ``AIError`` with a message fit to show a user; nothing
here leaks a raw exception string or an API key fragment.
"""
from __future__ import annotations

import base64
import json
import os
import re

import requests

from .catalog import (DEFAULT_SLOTS, TONES, budget_line, duration_by_key,
                      structure_for, tone_by_id)

OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
TEXT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")

UA = "Smart1Hub-RadioPromo/1.0 (+marketing script research)"


class AIError(RuntimeError):
    """Something the user can be shown as-is."""


def api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def ready() -> bool:
    return bool(api_key())


def chat_json(system: str, user: str, max_tokens: int = 1800,
              temperature: float = 0.8) -> dict:
    if not ready():
        raise AIError("OpenAI key is not set. Add OPENAI_API_KEY.")
    try:
        res = requests.post(
            f"{OPENAI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key()}",
                     "Content-Type": "application/json"},
            json={"model": TEXT_MODEL, "temperature": temperature,
                  "max_tokens": max_tokens,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=120)
    except requests.RequestException as exc:
        raise AIError(f"Couldn't reach OpenAI ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        # Deliberately not echoing the body — it can contain a key prefix.
        raise AIError(f"OpenAI refused the request (HTTP {res.status_code}).")
    try:
        text = res.json()["choices"][0]["message"]["content"]
        return json.loads(text)
    except (KeyError, IndexError, ValueError) as exc:
        raise AIError("The model returned something that was not valid JSON.") from exc


# ----------------------------------------------------------------- page read
def read_page(url: str, label: str) -> dict:
    """Pull readable text off a page so the model can actually read the site."""
    if not url:
        return {"url": "", "label": label, "ok": False, "text": "",
                "note": "No URL provided."}
    target = url if url.startswith("http") else f"https://{url}"
    try:
        res = requests.get(target, headers={"User-Agent": UA}, timeout=15)
    except requests.RequestException as exc:
        return {"url": url, "label": label, "ok": False, "text": "",
                "note": f"Couldn't reach the page ({exc.__class__.__name__})."}
    if res.status_code >= 400:
        return {"url": url, "label": label, "ok": False, "text": "",
                "note": f"Page returned {res.status_code}."}
    html = res.text
    title = (re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I) or [None, ""])[1]
    desc = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)",
                     html, re.I)
    body = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    body = re.sub(r"<style[\s\S]*?</style>", " ", body, flags=re.I)
    body = re.sub(r"<noscript[\s\S]*?</noscript>", " ", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body).replace("&nbsp;", " ")
    body = re.sub(r"\s+", " ", body).strip()[:7000]
    return {"url": url, "label": label, "ok": True, "title": (title or "").strip(),
            "description": (desc.group(1).strip() if desc else ""), "text": body}


# --------------------------------------------------------------------- brief
def analyse_project(brand: dict, customer: dict, history_hint: list[str] | None = None) -> dict:
    """Read home page + landing page + promotion notes; return a creative brief
    and three recommended tones out of the fifteen."""
    home = read_page(customer.get("home_url", ""), "Home page")
    landing = read_page(customer.get("landing_url", ""), "Landing page")

    tone_menu = "\n".join(f"- {t['id']}: {t['label']} — {t['direction']}" for t in TONES)
    history = ""
    if history_hint:
        history = ("\nTONES THAT HAVE WORKED FOR THIS AGENCY'S CLIENTS BEFORE "
                   "(weigh these up, but only where they genuinely fit): "
                   + ", ".join(history_hint))

    company = brand.get("name") or customer.get("company") or customer.get("client_name") or ""
    result = chat_json(
        "You are a senior radio copy strategist at Smart 1 Marketing, a digital "
        "agency that buys streaming radio on Pandora, Spotify and iHeart. You read "
        "a client's site and their promotion, then brief the creative team. You are "
        "specific and you never invent facts, offers, prices or claims that are not "
        "in the source material. Reply as JSON only.",
        f"""CLIENT
Company: {company}
Industry: {brand.get('industry') or 'unknown'}
Location: {brand.get('location') or 'unknown'}
Brand description: {brand.get('description') or 'n/a'}

HOME PAGE ({home['url'] or 'none'})
{(home.get('title', '') + chr(10) + home.get('description', '') + chr(10) + home['text']) if home['ok'] else home['note']}

LANDING PAGE ({landing['url'] or 'none'})
{(landing.get('title', '') + chr(10) + landing.get('description', '') + chr(10) + landing['text']) if landing['ok'] else landing['note']}

PROMOTION DETAILS FROM THE CLIENT
{customer.get('promotion') or 'None supplied.'}

REQUIRED DISCLAIMER (must be read verbatim inside the spot)
{customer.get('disclaimer') or 'None.'}

TONE MENU
{tone_menu}{history}

Return JSON shaped exactly like:
{{
  "summary": "3-4 sentences on what this business actually does and who buys from them",
  "audience": "one sentence on the listener we are targeting",
  "offer": "the promotion in one plain sentence, or 'No specific offer supplied'",
  "differentiators": ["3-5 short proof points pulled from the pages"],
  "callToAction": "the single action the listener should take",
  "mustSay": ["names, phone numbers, URLs or legal wording that must appear verbatim"],
  "avoid": ["2-4 things the script should not claim or say"],
  "recommendedTones": [{{"toneId":"one of the menu ids","why":"one sentence tied to this client"}}]
}}
Give exactly 3 recommendedTones, best first.""",
        max_tokens=1600)

    result["sources"] = {
        "home": {"url": home["url"], "ok": home["ok"], "note": home.get("note")},
        "landing": {"url": landing["url"], "ok": landing["ok"], "note": landing.get("note")},
    }
    return result


# ------------------------------------------------------------------- scripts
def _slot_schema(lengths: list[dict]) -> str:
    """The JSON the model is asked for, one line per length in play."""
    rows = []
    for d in lengths:
        rows.append(
            f'  "{d["key"]}": {{"script":"the {d["label"]} read, plain spoken text '
            f'only","wordCount":0,"estimatedSeconds":{d["seconds"]},"notes":"one line '
            f'of direction for the voice talent"}}')
    return ",\n".join(rows)


def _slot_beats(lengths: list[dict]) -> str:
    """The beats each read is built on, so the plan reaches the writer.

    The shape of a radio read lived only in this prompt's own prose, which
    meant a rep could not see it and the two descriptions could not be held
    against each other -- a script that had wandered from the plan read exactly
    like one written to it. It is `catalog.STRUCTURE_TEMPLATES` now, the same
    table the copy screen draws its rail from, so the screen and the prompt
    cannot describe different shapes.
    """
    out = []
    for d in lengths:
        beats = " -> ".join(
            f"{b['label']} ({b['start_pct']}-{b['end_pct']}%): {b['guidance']}"
            for b in structure_for(d["key"]))
        out.append(f'{d["label"]} {beats}')
    return "\n".join(out)


# The ceiling the :15/:30 pair has always been written under, and the words
# that pair is allowed. Everything else is scaled against it rather than
# against a token-per-word ratio nobody here can verify: what is known is that
# 1400 has always been enough for 127 words of script plus the envelope.
_PAIR_TOKENS = 1400
_PAIR_WORDS = 127


def _script_tokens(lengths: list[dict]) -> int:
    """Room for every script asked for, in proportion to the words asked for.

    A ceiling sized for the pair truncates a set that also carries a :60, and a
    truncated response arrives with an empty text body -- which every caller
    here reads as its own kind of nothing rather than as the ceiling it is.
    Never below the pair's own ceiling, so no existing job gets less room.
    """
    words = sum(d["high"] for d in lengths) or _PAIR_WORDS
    return max(_PAIR_TOKENS, int(_PAIR_TOKENS * words / _PAIR_WORDS))


def write_scripts(analysis: dict, brand: dict, customer: dict, tone_id: str,
                  revision_note: str = "", previous: dict | None = None,
                  slots: tuple[str, ...] | list[str] | None = None) -> dict:
    """Write every length this project asked for in one call, sharing one hook.

    The lengths are the caller's rather than this function's: a project writing
    a :15/:30 pair and one writing all three both come through here, and the
    JSON shape asked for is built from the slots so the model is never asked
    for a length nobody wants or left to guess at one.
    """
    tone = tone_by_id(tone_id)
    if not tone:
        raise AIError("Unknown tone.")
    disclaimer = str(customer.get("disclaimer") or "").strip()

    keys = tuple(slots or DEFAULT_SLOTS)
    lengths = [duration_by_key(k) for k in keys]
    lengths = [d for d in lengths if d]
    if not lengths:
        raise AIError("No slot lengths to write.")

    revision_block = ""
    if revision_note:
        prev = previous or {}
        drafts = "".join(
            f"{d['label']}: {(prev.get(d['key']) or {}).get('script', '')}\n"
            for d in lengths)
        revision_block = (
            f"\nThe client reviewed the previous draft and asked for this change:\n"
            f"\"{revision_note}\"\n\nPREVIOUS DRAFT\n{drafts}\n"
            "Rewrite every length honoring the request. Keep everything the "
            "client did not object to.")

    disclaimer_block = ""
    if disclaimer:
        disclaimer_block = (
            "\nREQUIRED DISCLAIMER — reproduce word for word as the last thing "
            "before the call to action, in BOTH lengths. It counts toward the "
            f"word budget, so write the rest shorter to make room:\n\"{disclaimer}\"\n")

    company = brand.get("name") or customer.get("company") or customer.get("client_name") or ""
    url = (customer.get("landing_url") or customer.get("home_url") or "").strip()
    phone = (customer.get("phone") or "").strip() if customer.get("include_phone") else ""
    identity_rule = (f'Company name required in every script: "{company}". '
                     f'URL required in every script: "{url}". '
                     + (f'Phone required in every script: "{phone}".' if phone else ""))
    return chat_json(
        "You write streaming-radio commercials for Smart 1 Marketing. Radio is "
        "heard, not read: write for the ear. Rules you never break — the brand "
        "name is said at least twice in a :30 and at least once in a :15; the call "
        "to action is the last thing heard; you never invent an offer, price, "
        "discount, guarantee or statistic that was not supplied; you never write "
        "sound effects the client did not ask for; at a natural read pace "
        f"{budget_line()}. Reply as JSON only.",
        f"""TONE: {tone['label']} — {tone['direction']}

BRIEF
Business: {company}
What they do: {analysis.get('summary', '')}
Listener: {analysis.get('audience', '')}
Offer: {analysis.get('offer', '')}
Proof points: {' | '.join(analysis.get('differentiators') or [])}
Call to action: {analysis.get('callToAction', '')}
Must say verbatim: {' | '.join(analysis.get('mustSay') or []) or 'nothing specific'}
Do not say: {' | '.join(analysis.get('avoid') or []) or 'nothing specific'}
Client's own promotion notes: {customer.get('promotion') or 'none'}
REQUIRED: {identity_rule}
{disclaimer_block}{revision_block}

SHAPE OF EACH READ
{_slot_beats(lengths)}

Return JSON:
{{
  "hook": "the shared opening idea in a few words",
{_slot_schema(lengths)}
}}
The script fields contain only words to be spoken. No labels, no "VO:", no timestamps, no stage directions.""",
        max_tokens=_script_tokens(lengths))


def tighten_script(script: str, seconds: int, trim_words: int, tone_id: str,
                   analysis: dict, customer: dict) -> dict:
    """The read came back over the slot. Cut words without losing the offer,
    the brand name, URL, call to action or any required disclaimer."""
    tone = tone_by_id(tone_id) or {}
    disclaimer = str((customer or {}).get("disclaimer") or "").strip()
    current = len([w for w in re.split(r"\s+", script or "") if w])
    target = max(8, current - trim_words)
    plural = "" if trim_words == 1 else "s"

    return chat_json(
        "You are a radio copy editor. You cut for time. You never drop the brand "
        "name, URL, offer, call to action or a required disclaimer — you cut "
        "adjectives, subordinate clauses and setup instead. Reply as JSON only.",
        f"""This :{seconds} read came in {trim_words} word{plural} too long for the slot.

TONE: {tone.get('label', '')} — keep it.
Brand name and call to action are mandatory: {(analysis or {}).get('callToAction', '')}
Company name that must remain: {customer.get('company') or customer.get('client') or ''}
URL that must remain: {customer.get('landing_url') or customer.get('home_url') or ''}
{f'Phone number that must remain: {customer.get("phone")}' if customer.get('include_phone') and customer.get('phone') else ''}
{f'This disclaimer must survive word for word: "{disclaimer}"' if disclaimer else ''}

CURRENT SCRIPT ({current} words)
{script}

Rewrite it at roughly {target} words. Same meaning, same tone, fewer words.

Return JSON: {{"script":"the tightened read, spoken words only","wordCount":0,"whatWentAndWhy":"one sentence"}}""",
        max_tokens=700)


def suggest_voice_profile(analysis: dict, customer: dict, tone_ids: list[str]) -> dict:
    labels = [tone_by_id(t)["label"] for t in tone_ids if tone_by_id(t)]
    return chat_json(
        "You are a casting director for radio voiceover. You recommend a voice, "
        "not a person. Reply as JSON only.",
        f"""Tones selected: {', '.join(labels)}
Business: {analysis.get('summary') or customer.get('company') or ''}
Listener: {analysis.get('audience', '')}
Offer: {analysis.get('offer', '')}

Return JSON:
{{
  "recommendation": {{"gender":"female|male|neutral|any","age":"young|middle_aged|old|any","accent":"american|british|australian|transatlantic|any","energy":"laid_back|conversational|energetic|explosive","delivery":"announcer|narrator|best_friend|spokesperson|character"}},
  "why": "two sentences on why this voice suits this listener",
  "searchTerms": ["3-6 words a voice library would tag this voice with"]
}}""",
        max_tokens=600)


# -------------------------------------------------------------------- banner
def banner_copy(analysis: dict, brand: dict, customer: dict, tone_id: str) -> dict:
    tone = tone_by_id(tone_id) or {}
    company = brand.get("name") or customer.get("company") or ""
    return chat_json(
        "You write companion banner copy for streaming audio ads. The banner is "
        "small and glanceable: a listener sees it on a phone lock screen for a few "
        "seconds. Reply as JSON only.",
        f"""Tone: {tone.get('label', '')} — {tone.get('direction', '')}
Business: {company}
Offer: {analysis.get('offer') or customer.get('promotion') or ''}
Call to action: {analysis.get('callToAction', '')}

Return JSON:
{{"headline":"4 words maximum","subline":"6 words maximum","cta":"2-3 words, a button label"}}""",
        max_tokens=300)


def banner_art(brand: dict, tone_id: str, headline: str = "") -> dict:
    """Background artwork for the companion banner.

    The image model is told not to render text — the headline, subline, CTA and
    the client's logo are composited afterwards, which keeps type crisp and the
    logo exact.
    """
    if not ready():
        raise AIError("OpenAI key is not set.")
    tone = tone_by_id(tone_id) or {}
    palette = ", ".join((brand.get("colors") or [])[:3]) or "deep navy, warm orange"
    prompt = (
        "A clean abstract background graphic for a streaming-audio companion "
        f"banner. Mood: {tone.get('banner_mood', '')}. Color palette: {palette}. "
        "Composition: strong empty area on the left third for a logo and headline "
        "to be placed later. No text, no letters, no words, no numbers, no logos, "
        "no watermarks, no people's faces. Flat modern advertising art direction, "
        "high contrast, print-clean edges.")
    try:
        res = requests.post(
            f"{OPENAI_BASE}/images/generations",
            headers={"Authorization": f"Bearer {api_key()}",
                     "Content-Type": "application/json"},
            json={"model": IMAGE_MODEL, "prompt": prompt, "size": "1024x1024", "n": 1},
            timeout=180)
    except requests.RequestException as exc:
        raise AIError(f"Couldn't reach the image model ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        raise AIError(f"The image model refused the request (HTTP {res.status_code}).")
    item = (res.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        return {"b64": item["b64_json"], "prompt": prompt, "headline": headline}
    if item.get("url"):
        img = requests.get(item["url"], timeout=60)
        return {"b64": base64.b64encode(img.content).decode(), "prompt": prompt,
                "headline": headline}
    raise AIError("The image model returned no image.")
