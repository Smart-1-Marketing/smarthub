"""
OpenAI service — the "creative brain" of the Commercial Builder.

Per the product spec, OpenAI's job is strictly creative planning: it never
touches pixels or audio. It decides:
  - what a client's brand profile probably is (from their website)
  - three materially different concepts for a brief
  - a timed scene-by-scene script that respects the VO word-count targets
  - optimized stock-search queries for a scene description
  - final spelling/brand-name QC pass

Every public function degrades to deterministic mock data when
OPENAI_API_KEY is not set, so the rest of the Commercial Builder (storyboard
editor, QC panel, render pipeline) is fully clickable/demoable without live
keys — matching the "degrades gracefully" pattern already used elsewhere in
Smart 1 Hub (see v1.6.0 handoff).
"""

import json
import os
import re

from ..config import VO_WORD_TARGETS, get_structure, DEFAULT_QR_AUDIO_CUE

_MODEL = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini")
_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")


def is_live():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _chat_json(system, user, max_tokens=1500):
    """Call the Chat Completions API and parse a JSON object response."""
    client = _client()
    resp = client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=0.8,
    )
    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_sdk_usage("commercial_builder", resp, purpose="script")
    except Exception:  # noqa: BLE001
        pass
    return json.loads(resp.choices[0].message.content)


# ---------------------------------------------------------------------------
# 2. Client Brand Profile — analyze a website to pre-populate the profile
# ---------------------------------------------------------------------------
def analyze_website(url):
    """Best-effort brand-profile extraction from a client's website."""
    page_text = ""
    try:
        import requests
        r = requests.get(url if url.startswith("http") else f"https://{url}", timeout=8,
                          headers={"User-Agent": "Mozilla/5.0 (Smart1CreativeHub/1.0)"})
        page_text = re.sub(r"<[^>]+>", " ", r.text)[:6000]
    except Exception:
        page_text = ""

    if not is_live():
        return _mock_brand_profile(url)

    try:
        result = _chat_json(
            system=(
                "You extract a marketing brand profile from raw website text for a digital "
                "marketing agency (Smart 1 Marketing) building video commercials. Respond with "
                "a JSON object with keys: business_name, tagline, industry, service_area, "
                "brand_voice (1-2 sentences describing tone), suggested_cta, phone, address. "
                "Use empty string for anything not confidently found. Never invent a phone "
                "number or address that isn't in the text."
            ),
            user=f"Website URL: {url}\n\nPage text:\n{page_text or '(could not fetch page)'}",
            max_tokens=600,
        )
        return result
    except Exception as e:
        profile = _mock_brand_profile(url)
        profile["_error"] = str(e)
        return profile


def _mock_brand_profile(url):
    domain = re.sub(r"^https?://", "", url or "").split("/")[0].replace("www.", "")
    name = domain.split(".")[0].replace("-", " ").title() if domain else "New Client"
    return {
        "business_name": name, "tagline": "", "industry": "", "service_area": "",
        "brand_voice": "Friendly and professional.", "suggested_cta": "Contact us today",
        "phone": "", "address": "", "_mock": True,
    }


# ---------------------------------------------------------------------------
# 3. Commercial Brief -> Generate Concepts
# ---------------------------------------------------------------------------
def generate_concepts(brief, client_profile, commercial_type):
    if not is_live():
        return _mock_concepts(brief)

    try:
        result = _chat_json(
            system=(
                "You are a senior copywriter/creative director for a digital marketing agency "
                "building :05-:60 second video commercials. Given a client brand profile and a "
                "commercial brief, generate exactly 3 MATERIALLY DIFFERENT creative concepts "
                "(different emotional angle or narrative structure, not just reworded copy). "
                'Respond as JSON: {"concepts":[{"title":"...","angle":"...","summary":"..."}, '
                '... exactly 3 items]}. "angle" is a short label like \'Problem -> service -> '
                "offer -> CTA\' or \'Lifestyle / family-oriented\'. \"summary\" is 1-2 sentences."
            ),
            user=json.dumps({
                "client": client_profile, "brief": brief, "commercial_type": commercial_type,
            }),
            max_tokens=700,
        )
        concepts = result.get("concepts", [])[:3]
        for i, c in enumerate(concepts):
            c["id"] = f"concept_{i + 1}"
        return concepts or _mock_concepts(brief)
    except Exception:
        return _mock_concepts(brief)


def _mock_concepts(brief):
    what = brief.get("what_advertising") or "this offer"
    return [
        {"id": "concept_1", "title": "Beat the Heat" if "ac" in what.lower() or "cool" in what.lower()
            else "The Direct Approach", "angle": "Problem -> service -> offer -> CTA",
         "summary": f"Opens on the pain point, introduces the solution, then presents {what} "
                    f"with a clear call to action."},
        {"id": "concept_2", "title": "Don't Wait", "angle": "Preventative / urgency angle",
         "summary": f"Leads with urgency and consequence of inaction, positions {what} as the "
                    f"smart move before it's too late."},
        {"id": "concept_3", "title": "Life Made Easier", "angle": "Lifestyle / family-oriented angle",
         "summary": f"Shows the everyday benefit for a family/customer, then ties it back to "
                    f"{what} as an easy win."},
    ]


# ---------------------------------------------------------------------------
# 4. AI creates the timed script
# ---------------------------------------------------------------------------
def generate_script(concept, length_seconds, brief, client_profile, platform="both", qr_enabled=None):
    """
    Follows the duration-specific structural blueprint from the CTV/YouTube
    best-practices brief (config.STRUCTURE_TEMPLATES) instead of an even
    scene split: a :30 gets a Hook (0-5s) / Value (5-20s) / Close (20-30s),
    a :05 stays a single hero+logo beat with no phone/QR mention at all, etc.

    Returns:
      {
        "duration": 30,
        "scenes": [{"start":0,"end":5,"visual":"...","voiceover":"...","beat":"The Hook"}, ...],
        "word_count": 71,
        "target_range": [65, 75],
        "within_target": true,
      }
    """
    lo, hi = VO_WORD_TARGETS.get(length_seconds, (65, 75))
    beats = get_structure(length_seconds)
    if qr_enabled is None:
        qr_enabled = length_seconds in (15, 30, 60)
    include_audio_cue = qr_enabled and length_seconds >= 15

    if not is_live():
        script = _mock_script(concept, length_seconds, brief, client_profile, beats, include_audio_cue)
    else:
        try:
            beat_spec = [{"label": b["label"], "start_pct": b["start_pct"], "end_pct": b["end_pct"],
                          "guidance": b["guidance"]} for b in beats]
            audio_cue_instruction = (
                f' The final beat must end with this spoken cue, adapted naturally to fit: '
                f'"{DEFAULT_QR_AUDIO_CUE}"' if include_audio_cue else
                " Do not mention a phone number or QR code anywhere — this length doesn't carry them."
                if length_seconds == 5 else ""
            )
            result = _chat_json(
                system=(
                    "You write timed video-commercial scripts for a digital marketing agency "
                    "producing CTV and YouTube spots. You are given a selected creative concept, "
                    f"a commercial brief, a target duration of {length_seconds} seconds, a target "
                    f"platform of '{platform}', and a REQUIRED structural blueprint of beats "
                    f"(each with a start/end percentage of the total duration and creative "
                    f"guidance for what that beat must accomplish): {json.dumps(beat_spec)}. "
                    f"Produce exactly one scene per beat, using the beat's start_pct/end_pct "
                    f"(of {length_seconds}s) to compute start/end in seconds — contiguous, no "
                    f"gaps or overlaps, first scene starts at 0, last scene ends at exactly "
                    f"{length_seconds}. Total voiceover across all scenes must land in "
                    f"{lo}-{hi} words — do NOT wildly exceed it.{audio_cue_instruction} Respond "
                    'as JSON: {"scenes":[{"beat":"<the beat label>","start":0,"end":5,"visual":'
                    '"one-sentence shot description, no text overlays described here",'
                    '"voiceover":"narration line for this beat, or empty string if silent"}]}.'
                ),
                user=json.dumps({
                    "concept": concept, "brief": brief, "client": client_profile,
                    "length_seconds": length_seconds, "platform": platform,
                }),
                max_tokens=1200,
            )
            scenes = result.get("scenes", [])
            scenes = _normalize_scene_timing(scenes, length_seconds, beats)
            script = {"duration": length_seconds, "scenes": scenes}
        except Exception:
            script = _mock_script(concept, length_seconds, brief, client_profile, beats, include_audio_cue)

    wc = _word_count(script["scenes"])
    script["word_count"] = wc
    script["target_range"] = [lo, hi]
    script["within_target"] = lo <= wc <= hi
    return script


def _normalize_scene_timing(scenes, length_seconds, beats):
    """Force scenes onto the beat structure's start/end percentages (falls
    back to an even split if the model returned a different scene count)."""
    if not scenes:
        return scenes
    if len(scenes) != len(beats):
        # model didn't follow the beat count — fall back to an even split
        # rather than silently mismatching beats to scenes.
        n = len(scenes)
        out = []
        for i, s in enumerate(scenes):
            start = round(i * length_seconds / n, 1)
            end = round((i + 1) * length_seconds / n, 1) if i < n - 1 else float(length_seconds)
            out.append({"start": start, "end": end, "beat": s.get("beat", ""),
                        "visual": (s.get("visual") or "").strip(), "voiceover": (s.get("voiceover") or "").strip()})
        return out

    out = []
    for i, (s, beat) in enumerate(zip(scenes, beats)):
        start = round(length_seconds * beat["start_pct"] / 100, 1)
        end = round(length_seconds * beat["end_pct"] / 100, 1) if i < len(beats) - 1 else float(length_seconds)
        out.append({
            "start": start, "end": end, "beat": beat["label"],
            "visual": (s.get("visual") or "").strip(), "voiceover": (s.get("voiceover") or "").strip(),
        })
    return out


def _word_count(scenes):
    return sum(len((s.get("voiceover") or "").split()) for s in scenes)


def _mock_script(concept, length_seconds, brief, client_profile, beats, include_audio_cue):
    business = client_profile.get("business_name") or client_profile.get("name") or "This business"
    what = brief.get("what_advertising") or "our latest offer"
    cta = brief.get("primary_cta") or "Call today"
    phone = brief.get("phone") or client_profile.get("phone") or ""
    site = brief.get("landing_page") or client_profile.get("website") or ""

    if length_seconds == 5:
        # Pure brand recall — no story, no phone, no QR, per the brief.
        lines = [f"{business} — {what}."]
    elif length_seconds == 15:
        lines = [
            f"Still putting off {what.lower()}?",
            f"{business} makes it simple, fast, and worry-free.",
            f"{business}. {site or phone}." + (f" {DEFAULT_QR_AUDIO_CUE}" if include_audio_cue else ""),
        ]
    elif length_seconds == 60:
        lines = [
            f"Every homeowner eventually faces the same question: who do you trust with {what.lower()}?",
            f"{business} has built a reputation on showing up on time, doing it right, and treating "
            f"your home like our own — that's why neighbors keep calling us back.",
            f"Right now, take advantage of {what}. Our team is standing by, and this offer won't last.",
            f"{business}. {site or phone}." + (f" {DEFAULT_QR_AUDIO_CUE}" if include_audio_cue else ""),
        ]
    else:  # 30
        lines = [
            f"When it's time to think about {what.lower()}, most people don't know where to start.",
            f"{business} makes it simple, fast, and worry-free — right now you can take advantage "
            f"of {what}.",
            f"{business}. {site or phone}." + (f" {DEFAULT_QR_AUDIO_CUE}" if include_audio_cue else ""),
        ]

    n = min(len(beats), len(lines)) or 1
    scenes = []
    for i in range(len(beats)):
        beat = beats[i]
        start = round(length_seconds * beat["start_pct"] / 100, 1)
        end = round(length_seconds * beat["end_pct"] / 100, 1) if i < len(beats) - 1 else float(length_seconds)
        line = lines[i] if i < len(lines) else ""
        is_close = i == len(beats) - 1
        if length_seconds == 5:
            visual = "Hero shot on the product/service with the logo prominent — brand recall, not a story."
        elif is_close:
            visual = "CTA end card"
        else:
            visual = f"Supporting b-roll for: {line}"
        scenes.append({
            "start": start, "end": end, "beat": beat["label"],
            "visual": visual,
            "voiceover": line,
        })
    return {"duration": length_seconds, "scenes": scenes}


# ---------------------------------------------------------------------------
# 5. Storyboard Editor — regenerate a single scene without touching the rest
# ---------------------------------------------------------------------------
def regenerate_scene_content(concept, duration_seconds, brief, client_profile):
    """Rewrites one scene's visual + narration in isolation (used by the
    storyboard's 'Regenerate' button). Unlike generate_script(), this does
    NOT run the full beat structure — it just needs one shot description
    and a narration line sized to fit the scene's own duration."""
    target_words = max(3, round(duration_seconds * 2.3))  # ~2.3 words/sec comfortable VO pace

    if not is_live():
        what = brief.get("what_advertising") or "this offer"
        business = client_profile.get("business_name") or client_profile.get("name") or "This business"
        return {"visual": f"Supporting b-roll for {what}",
                "voiceover": f"{business} — {what}."[: max(20, target_words * 7)]}

    try:
        result = _chat_json(
            system=(
                f"Write ONE video-commercial scene: a shot description and a narration line sized "
                f"to roughly {target_words} words (the scene is {duration_seconds:.1f} seconds "
                f'long). Respond as JSON: {{"visual":"one-sentence shot description, no on-screen '
                'text described here","voiceover":"the narration line, or empty string if silent"}'
            ),
            user=json.dumps({"concept": concept, "brief": brief, "client": client_profile}),
            max_tokens=250,
        )
        return {"visual": (result.get("visual") or "").strip(), "voiceover": (result.get("voiceover") or "").strip()}
    except Exception:
        what = brief.get("what_advertising") or "this offer"
        return {"visual": f"Supporting b-roll for {what}", "voiceover": ""}


# ---------------------------------------------------------------------------
# 6. Universal Stock Video Search — query expansion
# ---------------------------------------------------------------------------
def expand_stock_queries(visual_description):
    if not is_live():
        return [visual_description]
    try:
        result = _chat_json(
            system=(
                "Convert a video commercial scene description into 2-3 short, concrete stock "
                "video search queries (3-6 words each) likely to return good results on Pexels/"
                'Pixabay. Respond as JSON: {"queries":["...","...","..."]}'
            ),
            user=visual_description,
            max_tokens=200,
        )
        queries = result.get("queries") or [visual_description]
        return queries[:3]
    except Exception:
        return [visual_description]


# ---------------------------------------------------------------------------
# 7. AI Generate button — Runway prompt writer (V1.5, service used once Runway lands)
# ---------------------------------------------------------------------------
def write_runway_prompt(visual_description, client_profile):
    base = (f"Cinematic {visual_description}, realistic commercial advertising photography, "
            f"natural lighting, no text, no watermark")
    if not is_live():
        return base
    try:
        result = _chat_json(
            system=(
                "Turn a short scene description into one vivid Runway text-to-video prompt "
                "(1-2 sentences) for a commercial advertising shot. No on-screen text, no "
                'watermarks, no logos. Respond as JSON: {"prompt":"..."}'
            ),
            user=visual_description,
            max_tokens=150,
        )
        return result.get("prompt") or base
    except Exception:
        return base


# ---------------------------------------------------------------------------
# 7. AI Generate button — interim still-frame generation until Runway (V1.5)
# ---------------------------------------------------------------------------
def generate_ai_stills(visual_description, client_profile, option_count=2):
    """
    V1 does not include Runway (that's V1.5 per the API roster), so 'Generate
    AI Footage' produces still frames via OpenAI Images as an interim option
    — still useful for CTA cards, static b-roll-style compositions, or as a
    placeholder while Runway access is being set up. Swap this out for
    services/runway_service.py (true video generation) in V1.5 without
    changing the route contract: it already returns a list of {url, prompt}
    options for the user to pick from, same as Runway will.
    """
    prompt = (f"Cinematic {visual_description}, realistic commercial advertising photography, "
              f"natural lighting, no text, no watermark, no logos")

    if not is_live():
        return [{"url": f"https://placehold.co/1024x576/333/fff?text=AI+Option+{chr(65+i)}",
                  "prompt": prompt, "_mock": True} for i in range(option_count)]

    try:
        client = _client()
        options = []
        for _ in range(option_count):
            resp = client.images.generate(model=_IMAGE_MODEL, prompt=prompt, size="1536x1024", n=1)
            options.append({"url": resp.data[0].url, "prompt": prompt})
        return options
    except Exception as e:
        return [{"url": None, "prompt": prompt, "error": str(e)}]


# ---------------------------------------------------------------------------
# 13. Automatic quality checks — spelling / brand-name pass
# ---------------------------------------------------------------------------
def qc_spelling_check(script_text, client_profile):
    """Lightweight heuristic pass; upgraded to an LLM pass when OPENAI_API_KEY is set."""
    issues = []
    business_name = (client_profile.get("business_name") or client_profile.get("name") or "").strip()
    if business_name and business_name.lower() not in script_text.lower():
        issues.append(f"Business name '{business_name}' does not appear in the script.")

    if not is_live():
        return issues

    try:
        result = _chat_json(
            system=(
                "Proofread this video commercial script for spelling errors and misspelled "
                "business/place names only (ignore stylistic choices). Respond as JSON: "
                '{"issues":["short description of each issue found"]}. Empty list if clean.'
            ),
            user=json.dumps({"script": script_text, "client": client_profile}),
            max_tokens=300,
        )
        issues.extend(result.get("issues", []))
    except Exception:
        pass
    return issues
