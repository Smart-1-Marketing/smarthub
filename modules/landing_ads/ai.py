"""The copy engine for Landing Ads.

One read of the page produces a shared brief; every format is then written
from that brief rather than from the raw HTML, so a Google headline and a
:30 radio read make the same promise. Nothing is invented — the prompts all
carry the same instruction the Radio Promo prompts carry, because an ad that
promises an offer the landing page does not honour is worse than no ad.

The OpenAI plumbing is Radio Promo's (`modules.radio_promo.ai`) so there is
one place to change the model, the timeout and the error handling. If that
module ever fails to import, a local copy of `chat_json` takes over rather
than taking this tool down with it.
"""
from __future__ import annotations

import json
import os
import re

import requests

from .catalog import (BANNER_SIZES, RSA_LIMITS, SOCIAL_LIMITS)

try:                                                   # shared plumbing
    from modules.radio_promo.ai import AIError, chat_json, read_page, ready
except Exception:                                      # noqa: BLE001 — standalone fallback
    OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    TEXT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

    class AIError(RuntimeError):
        pass

    def ready() -> bool:
        return bool((os.environ.get("OPENAI_API_KEY") or "").strip())

    def chat_json(system, user, max_tokens=1800, temperature=0.8):
        if not ready():
            raise AIError("OpenAI key is not set. Add OPENAI_API_KEY.")
        res = requests.post(
            f"{OPENAI_BASE}/chat/completions",
            headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"].strip(),
                     "Content-Type": "application/json"},
            json={"model": TEXT_MODEL, "temperature": temperature,
                  "max_tokens": max_tokens,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=120)
        if res.status_code >= 400:
            raise AIError(f"OpenAI refused the request (HTTP {res.status_code}).")
        try:
            return json.loads(res.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as exc:
            raise AIError("The model returned something that was not valid JSON.") from exc

    def read_page(url, label):
        if not url:
            return {"url": "", "label": label, "ok": False, "text": "",
                    "note": "No URL provided."}
        try:
            res = requests.get(url if url.startswith("http") else "https://" + url,
                               headers={"User-Agent": "Smart1Hub-LandingAds/1.0"},
                               timeout=15)
        except requests.RequestException as exc:
            return {"url": url, "label": label, "ok": False, "text": "",
                    "note": f"Couldn't reach the page ({exc.__class__.__name__})."}
        body = re.sub(r"<(script|style|noscript)[\s\S]*?</\1>", " ", res.text, flags=re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        return {"url": url, "label": label, "ok": res.status_code < 400,
                "text": re.sub(r"\s+", " ", body).strip()[:7000], "title": "",
                "description": "", "note": ""}


AGENCY = ("You write advertising for Smart 1 Marketing, a digital agency in "
          "Columbus, Ohio. You never invent an offer, price, guarantee, "
          "statistic or claim that is not in the source material — the ad has to "
          "be honored by the page it clicks through to.")


# --------------------------------------------------------------------- brief
def brief_from_page(url: str, page_meta: dict, objective: str, notes: str,
                    audience_override: str = "") -> dict:
    page = read_page(url, "Landing page")
    if not page["ok"]:
        raise AIError(f"Couldn't read {url or 'that page'} — {page.get('note') or 'no content came back'}.")
    return chat_json(
        AGENCY + " You read a landing page and brief the ad team. Reply as JSON only.",
        f"""LANDING PAGE ({page['url']})
{page['text']}

WHAT WE KNOW ALREADY
Product: {page_meta.get('name') or 'unknown'}
Vertical: {page_meta.get('industry') or 'unknown'}
Who the page is for: {audience_override or page_meta.get('audience') or 'read it off the page'}
Campaign objective: {objective}
Notes from the team: {notes or 'none'}

Return JSON:
{{
  "product": "what this page is actually offering, one plain sentence",
  "audience": "who should see the ad, one sentence",
  "promise": "the single strongest promise the page makes",
  "proof": ["3-5 short proof points taken from the page"],
  "objections": ["2-4 things a skeptical reader would push back on"],
  "cta": "the exact action the page asks for",
  "keywords": ["8-12 search terms this audience would actually type"],
  "avoid": ["2-4 claims the ads must not make"]
}}""",
        max_tokens=1400) | {"source_url": page["url"]}


# ------------------------------------------------------------------ formats
def _flag(items: list[str], limit: int) -> list[dict]:
    return [{"text": t, "chars": len(t), "over": len(t) > limit} for t in items]


def google_rsa(brief: dict, objective: str) -> dict:
    out = chat_json(
        AGENCY + " You write Google responsive search ads. Reply as JSON only.",
        f"""Product: {brief.get('product')}
Audience: {brief.get('audience')}
Promise: {brief.get('promise')}
Proof: {' | '.join(brief.get('proof') or [])}
Call to action: {brief.get('cta')}
Objective: {objective}
Do not claim: {' | '.join(brief.get('avoid') or []) or 'nothing specific'}

HARD LIMITS — count the characters, spaces included. Headlines {RSA_LIMITS['headline']}
characters or fewer. Descriptions {RSA_LIMITS['description']} or fewer. Callouts
{RSA_LIMITS['callout']} or fewer. Sitelink text {RSA_LIMITS['sitelink']} or fewer.
Anything longer is unusable, so write shorter rather than cleverer.

Give a genuine spread: some headlines lead with the offer, some with the
problem, some with proof, some with the call to action. No two headlines should
say the same thing in different words.

Return JSON:
{{
  "headlines": ["15 headlines"],
  "descriptions": ["4 descriptions"],
  "callouts": ["6 callouts"],
  "sitelinks": [{{"text":"","description":"one line, 35 characters or fewer"}}],
  "keywords": [{{"keyword":"","match":"exact|phrase|broad"}}],
  "negatives": ["6 negative keywords that would waste spend here"]
}}
Give 4 sitelinks and 12 keywords.""",
        max_tokens=2000)
    return {
        "headlines": _flag([str(h) for h in (out.get("headlines") or [])][:15],
                           RSA_LIMITS["headline"]),
        "descriptions": _flag([str(d) for d in (out.get("descriptions") or [])][:4],
                              RSA_LIMITS["description"]),
        "callouts": _flag([str(c) for c in (out.get("callouts") or [])][:6],
                          RSA_LIMITS["callout"]),
        "sitelinks": out.get("sitelinks") or [],
        "keywords": out.get("keywords") or [],
        "negatives": out.get("negatives") or [],
    }


def paid_social(brief: dict, objective: str) -> dict:
    out = chat_json(
        AGENCY + " You write paid social ads for Meta and TikTok. Meta rewards a "
        "clear first line; TikTok rewards sounding like a person rather than a "
        "brand. Reply as JSON only.",
        f"""Product: {brief.get('product')}
Audience: {brief.get('audience')}
Promise: {brief.get('promise')}
Proof: {' | '.join(brief.get('proof') or [])}
Objections to handle: {' | '.join(brief.get('objections') or [])}
Call to action: {brief.get('cta')}
Objective: {objective}
Do not claim: {' | '.join(brief.get('avoid') or []) or 'nothing specific'}

Three angles per platform, each materially different — not three rewordings.
Keep Meta primary text under {SOCIAL_LIMITS['meta']['primary']} characters and
TikTok under {SOCIAL_LIMITS['tiktok']['primary']}; headlines under 40 either way.

Return JSON:
{{
  "meta": [{{"angle":"two or three words","primary":"","headline":"","description":"","hook":"the first line as a scroll-stopper"}}],
  "tiktok": [{{"angle":"","primary":"","headline":"","description":"","hook":"spoken opening line for the first 2 seconds"}}],
  "creative_notes": ["3-5 notes on what the image or video should show"]
}}""",
        max_tokens=2000)
    for platform in ("meta", "tiktok"):
        limits = SOCIAL_LIMITS[platform]
        for ad in (out.get(platform) or []):
            ad["primary_chars"] = len(str(ad.get("primary") or ""))
            ad["primary_over"] = ad["primary_chars"] > limits["primary"]
            ad["headline_chars"] = len(str(ad.get("headline") or ""))
            ad["headline_over"] = ad["headline_chars"] > limits["headline"]
    return out


def display(brief: dict, objective: str, sizes: list[dict] | None = None) -> dict:
    sizes = sizes or BANNER_SIZES
    listed = ", ".join(f"{s['w']}x{s['h']} ({s['name']})" for s in sizes)
    out = chat_json(
        AGENCY + " You art-direct display banners. A banner is read in about a "
        "second: one idea, one number, one action. Reply as JSON only.",
        f"""Product: {brief.get('product')}
Audience: {brief.get('audience')}
Promise: {brief.get('promise')}
Call to action: {brief.get('cta')}
Objective: {objective}
Do not claim: {' | '.join(brief.get('avoid') or []) or 'nothing specific'}

SIZES: {listed}
Wide short sizes (728x90, 320x50, 970x250) get a shorter headline and no
subhead. Tall and square sizes can carry a subhead.

Return JSON:
{{
  "concepts": [{{"name":"", "headline":"6 words maximum", "subhead":"8 words maximum or empty",
                 "cta":"2-3 words", "art_direction":"one sentence describing the background image",
                 "sizes":["300x250","1080x1080"]}}],
  "palette_note": "one line on color, tied to the brand where the page shows one"
}}
Give 3 concepts and cover every size listed at least once.""",
        max_tokens=1600)
    return out


def streaming_audio(brief: dict, objective: str, tone_hint: str = "") -> dict:
    """:15 and :30 to the same word budgets Radio Promo enforces, so a script
    written here can be dropped straight into that tool and recorded."""
    return chat_json(
        AGENCY + " You write streaming-radio commercials. Radio is heard, not "
        "read: write for the ear. The brand name is said at least twice in a :30 "
        "and at least once in a :15; the call to action is the last thing heard; "
        "a :15 runs 35-42 words and a :30 runs 70-85 words at a natural read "
        "pace. Reply as JSON only.",
        f"""Product: {brief.get('product')}
Audience: {brief.get('audience')}
Promise: {brief.get('promise')}
Proof: {' | '.join(brief.get('proof') or [])}
Call to action: {brief.get('cta')}
Objective: {objective}
Tone: {tone_hint or 'pick what suits this audience and say which you picked'}
Do not claim: {' | '.join(brief.get('avoid') or []) or 'nothing specific'}

Return JSON:
{{
  "tone":"the tone you wrote in",
  "fifteen":{{"script":"spoken words only","wordCount":0}},
  "thirty":{{"script":"spoken words only","wordCount":0}},
  "companion_banner":{{"headline":"4 words maximum","subline":"6 words maximum","cta":"2-3 words"}}
}}
No labels, no "VO:", no timestamps, no stage directions.""",
        max_tokens=1200)
