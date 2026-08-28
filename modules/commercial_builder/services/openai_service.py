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

from ..config import (VO_WORD_TARGETS, get_structure, DEFAULT_QR_AUDIO_CUE,
                      DEFAULT_SHOT_GRAMMAR, SHOT_SIZES, SHOT_ANGLES, SHOT_MOVES)
from . import abcd_service

_MODEL = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini")
_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")


def _key():
    """The OpenAI key, through the Hub's settings.

    The last direct os.environ key read in this module's services. It is the
    one provider whose name never drifted, but reading it here rather than in
    two places means the next spelling added to hub.config reaches this module
    without a second fix — which is exactly what Pexels needed twice.
    """
    try:
        from hub.config import settings
        if settings.openai_key:
            return settings.openai_key
    except Exception:  # noqa: BLE001 — standalone, or settings failed to build
        pass
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def is_live():
    return bool(_key())


def _client():
    from openai import OpenAI
    return OpenAI(api_key=_key())


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
    # The beats a social spot runs to are not the beats a CTV one runs to —
    # its hook is one beat and it is at zero, because a feed has no pre-roll
    # slot holding the viewer in place. get_structure() decides; passing the
    # platform is what makes it able to.
    beats = get_structure(length_seconds, platform)
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
            targets = abcd_service.shot_targets(length_seconds)
            sizes = "/".join(x["id"] for x in SHOT_SIZES)
            angles = "/".join(x["id"] for x in SHOT_ANGLES)
            moves = "/".join(x["id"] for x in SHOT_MOVES)
            result = _chat_json(
                system=(
                    "You write timed video-commercial scripts for a digital marketing agency "
                    "producing CTV, YouTube and social spots. You are given a selected creative "
                    f"concept, a commercial brief, a target duration of {length_seconds} "
                    f"seconds, a target platform of '{platform}', and a REQUIRED structural "
                    f"blueprint of beats (each with a start/end percentage of the total "
                    f"duration and creative guidance for what that beat must accomplish): "
                    f"{json.dumps(beat_spec)}. "
                    # The change that fixes the pacing. One scene per beat gave a :30 three
                    # shots averaging ten seconds; Google's own detector wants two.
                    f"Produce exactly one BEAT object per beat, and inside each beat produce "
                    f"2-6 SHOTS. Across the whole spot aim for {targets['low']}-"
                    f"{targets['high']} shots in total, because a shot should average about "
                    "2 seconds. Shots inside a beat are contiguous and together exactly fill "
                    "that beat's span; the first shot of the spot starts at 0 and the last "
                    f"ends at exactly {length_seconds}. "
                    "NARRATION IS PER BEAT, not per shot — one line of voiceover for the "
                    "whole beat, so the read is one thought over several pictures. "
                    f"Every shot carries shot grammar: size ({sizes}), angle ({angles}) and "
                    f"move ({moves}). "
                    f"Total voiceover across all beats must land in {lo}-{hi} words — do NOT "
                    f"exceed it.{audio_cue_instruction} Respond as JSON: "
                    '{"beats":[{"beat":"<the beat label>",'
                    '"voiceover":"the narration line for this whole beat, or empty if silent",'
                    '"shots":[{"visual":"one-sentence shot description, no on-screen text '
                    'described here","seconds":2.0,"size":"ms","angle":"eye","move":"static"}]}]}.'
                ),
                user=json.dumps({
                    "concept": concept, "brief": brief, "client": client_profile,
                    "length_seconds": length_seconds, "platform": platform,
                }),
                max_tokens=1200,
            )
            scenes = _shots_from_beats(result, length_seconds, beats)
            script = {"duration": length_seconds, "scenes": scenes}
        except Exception:
            script = _mock_script(concept, length_seconds, brief, client_profile, beats, include_audio_cue)

    wc = _word_count(script["scenes"])
    script["word_count"] = wc
    script["target_range"] = [lo, hi]
    script["within_target"] = lo <= wc <= hi
    return script


def _shots_from_beats(result, length_seconds, beats):
    """Flatten the model's beats-with-shots into the flat scene list.

    A `Scene` row is a SHOT now, not a beat. That is deliberately a change of
    what the row means rather than a new table: the row already carries a
    start, an end, a visual, an asset and `asset_meta["beat"]`, so a shot fits
    it exactly and every screen, picker and QC check downstream keeps working
    on rows rather than being taught about a second level.

    What holds the beat together is `asset_meta`: every shot in a beat carries
    the same `beat` label and `beat_index`, and the Blueprint groups on it. The
    NARRATION sits on the first shot of each beat and the rest are silent —
    that is what "narration is per beat" means once it is on a flat row, and
    it is why the word count still measures the spot and not the shots.

    Timing is recomputed here rather than trusted. The model is asked for
    contiguous shots that fill each beat exactly and it will sometimes hand
    back nine shots that add up to 31.4 seconds; the beat spans are the
    authority, and the shots are laid inside them in proportion to the
    durations asked for.
    """
    raw_beats = result.get("beats") or []
    if not raw_beats:
        # Older shape, or a model that ignored the instruction. Fall back to
        # the flat scene list rather than returning nothing.
        flat = result.get("scenes") or []
        return _normalize_scene_timing(flat, length_seconds, beats)

    scenes = []
    for index, beat in enumerate(beats):
        source = raw_beats[index] if index < len(raw_beats) else {}
        start = round(length_seconds * beat["start_pct"] / 100, 2)
        end = (round(length_seconds * beat["end_pct"] / 100, 2)
               if index < len(beats) - 1 else float(length_seconds))
        span = max(0.1, end - start)

        shots = [sh for sh in (source.get("shots") or []) if isinstance(sh, dict)]
        if not shots:
            # A beat the model gave no shots for is still a beat: one shot
            # filling it beats dropping the beat out of the spot.
            shots = [{"visual": (source.get("visual") or "").strip()}]

        weights = []
        for shot in shots:
            try:
                weights.append(max(0.1, float(shot.get("seconds") or 0)))
            except (TypeError, ValueError):
                weights.append(1.0)
        total = sum(weights) or float(len(shots))

        cursor = start
        for position, (shot, weight) in enumerate(zip(shots, weights)):
            share = span * (weight / total)
            shot_end = end if position == len(shots) - 1 else round(cursor + share, 2)
            scenes.append({
                "start": round(cursor, 2),
                "end": shot_end,
                "beat": beat["label"],
                "beat_index": index,
                # One line of narration for the beat, carried on its first
                # shot. The others are silent by design, not by omission.
                "voiceover": ((source.get("voiceover") or "").strip()
                              if position == 0 else ""),
                "visual": (shot.get("visual") or "").strip(),
                "grammar": _clean_grammar(shot),
            })
            cursor = shot_end
    return scenes


_VALID = {"size": {x["id"] for x in SHOT_SIZES},
          "angle": {x["id"] for x in SHOT_ANGLES},
          "move": {x["id"] for x in SHOT_MOVES}}


def _clean_grammar(shot):
    """Shot grammar, with anything the vocabulary does not know defaulted.

    The lists are closed because a `<select>` and a stock query both read them,
    so a model inventing "medium-wide-ish" has to land somewhere rather than
    reaching either. Defaulting is right here and reporting would not be: this
    is a starting point a person edits, not a measurement.
    """
    out = dict(DEFAULT_SHOT_GRAMMAR)
    for field, allowed in _VALID.items():
        value = str(shot.get(field) or "").strip().lower()
        if value in allowed:
            out[field] = value
    return out


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
    elif length_seconds == 6:
        lines = [
            f"{business}.",
            f"{what}." + (f" {site}." if site else ""),
        ]
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

    # Mock mode builds the same SHAPE as the live path, shots and all. A mock
    # that returned three fat scenes would make every downstream check —
    # pacing, the ABCD panel, the shot grammar controls — untestable without a
    # key, which is exactly the thing this module's mock mode exists to avoid.
    # Enough shots to satisfy the pacing threshold, distributed across the
    # beats that are not the end card. A mock that failed the pacing check it
    # exists to demonstrate would be worse than no mock.
    targets = abcd_service.shot_targets(length_seconds)
    body_beats = max(1, len(beats) - 1)
    per_beat = max(1, -(-max(0, targets["high"] - 1) // body_beats))   # ceil
    grammar_cycle = [
        {"size": "ews", "angle": "eye", "move": "static"},
        {"size": "ms", "angle": "eye", "move": "push"},
        {"size": "cu", "angle": "low", "move": "static"},
        {"size": "ecu", "angle": "eye", "move": "static"},
        {"size": "ws", "angle": "high", "move": "pan"},
    ]

    scenes = []
    shot_no = 0
    for i, beat in enumerate(beats):
        start_s = round(length_seconds * beat["start_pct"] / 100, 2)
        end_s = (round(length_seconds * beat["end_pct"] / 100, 2)
                 if i < len(beats) - 1 else float(length_seconds))
        line = lines[i] if i < len(lines) else ""
        is_close = i == len(beats) - 1
        # The end card is one held shot: the QR rule needs 8 seconds on screen
        # and cutting through it would defeat the only response mechanism the
        # spot has.
        count = 1 if (is_close or length_seconds in abcd_service.BUMPER_LENGTHS) \
            else max(1, per_beat)
        span = max(0.1, end_s - start_s)
        cursor = start_s
        for k in range(count):
            shot_end = end_s if k == count - 1 else round(cursor + span / count, 2)
            if length_seconds == 5:
                visual = ("Hero shot on the product/service with the logo prominent — "
                          "brand recall, not a story.")
            elif is_close:
                visual = "CTA end card with the logo held still"
            else:
                visual = f"Supporting shot {k + 1} for: {line}" if line else "Supporting shot"
            scenes.append({
                "start": round(cursor, 2), "end": shot_end,
                "beat": beat["label"], "beat_index": i,
                "voiceover": line if k == 0 else "",
                "visual": visual,
                "grammar": grammar_cycle[shot_no % len(grammar_cycle)],
            })
            shot_no += 1
            cursor = shot_end
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
# Writing more narration.
#
# The script writer sizes the whole read to VO_WORD_TARGETS and stops. That is
# right for a :15, where the budget is 35 words and every one of them is
# fought over — and it is why a :60 came back thin. A :60 has room for 150
# words and the beat structure gives it four beats to spend them across, but
# nothing in the tool would ever write the extra hundred: a rep who wanted
# more had to type it, and then the word count on the storyboard went red
# because nothing had re-measured.
#
# So expansion is its own call, and it is **budget-aware in code rather than
# in the prompt**. The model is told how many words the spot is currently
# using, what the target is, and how many are left — computed here — because
# a model asked to "write a bit more" writes a bit more regardless of whether
# there were four words of room or forty.
# ---------------------------------------------------------------------------
def narration_budget(scenes, length_seconds):
    """Where this spot stands against its word target.

    `room` is what a longer read may still spend. It floors at zero rather
    than going negative: a spot already over target has no room, and a
    negative number invites a caller to subtract its way into nonsense.
    """
    lo, hi = VO_WORD_TARGETS.get(length_seconds, (65, 75))
    used = sum(len((s.get("narration") or s.get("voiceover") or "").split())
               for s in scenes or [])
    return {"used": used, "target_low": lo, "target_high": hi,
            "room": max(0, hi - used), "under": used < lo, "over": used > hi}


def expand_narration(scenes, length_seconds, brief, client_profile, concept=None,
                     scene_index=None):
    """Write more narration — for one scene, or across the whole spot.

    Returns `{"scenes": [{"order_index", "narration"}], "note": str}`. Every
    line comes back **whole**, not as an addition to append: a fragment handed
    back to be joined onto what is there produces a sentence nobody wrote, and
    the seam is exactly where a voice actor stumbles.

    Refuses, in words, rather than returning the input unchanged when there is
    no room. A button that appears to work and changes nothing is the thing
    being fixed here.
    """
    scenes = list(scenes or [])
    if not scenes:
        return {"scenes": [], "note": "There are no scenes to write narration for yet."}

    budget = narration_budget(scenes, length_seconds)
    if budget["room"] < 5:
        return {"scenes": [], "note": (
            f"This spot is already at {budget['used']} words against a target of "
            f"{budget['target_low']}-{budget['target_high']} for a "
            f":{length_seconds:02d}. There is no room for more narration without "
            f"the read running long — shorten a line first, or build a longer cut.")}

    targets = ([scenes[scene_index]] if scene_index is not None
               and 0 <= scene_index < len(scenes) else scenes)

    if not is_live():
        return {"scenes": [], "note": (
            "Mock mode — no OPENAI_API_KEY is set, so no narration was written. "
            f"There is room for about {budget['room']} more words.")}

    payload = [{"order_index": i,
                "beat": (s.get("asset_meta") or {}).get("beat") or s.get("beat") or "",
                "seconds": round(float(s.get("end") or 0) - float(s.get("start") or 0), 1),
                "visual": s.get("visual_description") or s.get("visual") or "",
                "narration": s.get("narration") or s.get("voiceover") or "",
                "is_cta": bool(s.get("is_cta"))}
               for i, s in enumerate(scenes)
               if scene_index is None or s in targets]

    try:
        result = _chat_json(
            system=(
                "You are extending the voiceover of a video commercial that is already "
                f"written. The spot is {length_seconds} seconds. Its narration currently "
                f"runs {budget['used']} words against a target of {budget['target_low']}-"
                f"{budget['target_high']}, so you have room for roughly {budget['room']} "
                "more words IN TOTAL across every scene you return — do not exceed it. "
                "Rewrite each scene's narration in full, keeping what is already there "
                "intact in meaning and adding to it. A scene has about 2.3 words per "
                "second of comfortable read, so never write more than its own seconds "
                "allow. Do not add a price, a percentage, a phone number or a deadline "
                "that is not already in the brief. Respond as JSON: "
                '{"scenes":[{"order_index":0,"narration":"the full new line"}]}'
            ),
            user=json.dumps({"scenes": payload, "brief": brief,
                             "client": client_profile, "concept": concept or {}}),
            max_tokens=900,
        )
    except Exception as exc:  # noqa: BLE001
        return {"scenes": [], "note": f"The narration could not be written: {exc}"}

    out = []
    for row in result.get("scenes") or []:
        try:
            index = int(row.get("order_index"))
        except (TypeError, ValueError):
            continue
        line = (row.get("narration") or "").strip()
        if line and 0 <= index < len(scenes):
            out.append({"order_index": index, "narration": line})
    if not out:
        return {"scenes": [], "note": "The model returned no usable narration."}
    return {"scenes": out, "note": ""}


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
def _image_result_url(item):
    """The usable URL for one generated image, whichever way it came back.

    This is the line that made "Generate AI" fail. `gpt-image-1` — the default
    image model, and the one this deployment runs — **always** returns
    `b64_json` and never a `url`; only the older `dall-e-*` models return a
    hosted URL, and that URL expires within the hour anyway. The old code read
    `resp.data[0].url` unconditionally, so on this deployment both options came
    back with `url: None`: the picker drew Option A and Option B exactly as it
    would for a success, and clicking either one said "This option failed to
    generate" with nothing anywhere saying why. Two options, and both dead.

    A data URL is the right answer for both shapes. It cannot expire, it needs
    no second round trip, and `choose-ai-option` already mirrors whatever it is
    given into Cloudinary — so the picture that survives is the stored one
    rather than a signed link that 404s next week, which is the trap
    `hub/storage.py` and the HeyGen mirror both exist for.
    """
    b64 = getattr(item, "b64_json", None)
    if b64:
        return f"data:image/png;base64,{b64}"
    return getattr(item, "url", None)


def generate_ai_stills(visual_description, client_profile, option_count=2):
    """Still frames for a scene, as options to choose between.

    "Generate AI" makes a **picture**; "Generate Video" animates the picture it
    made. They are two steps of one job and the storyboard now says so — see
    `write_runway_prompt` below, which is the second half.

    A failed generation is returned as an option carrying its own `error`
    rather than as one collapsed error for the batch: asking for two and
    getting one is a normal outcome (a content refusal on one prompt, a
    timeout on the other), and reporting the whole thing as failed throws away
    the option that worked.
    """
    prompt = (f"Cinematic {visual_description}, realistic commercial advertising photography, "
              f"natural lighting, no text, no watermark, no logos")

    if not is_live():
        return [{"url": f"https://placehold.co/1024x576/333/fff?text=AI+Option+{chr(65+i)}",
                  "prompt": prompt, "_mock": True} for i in range(option_count)]

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001 — the SDK is missing or the key is unusable
        return [{"url": None, "prompt": prompt, "error": str(exc)}
                for _ in range(option_count)]

    options = []
    for index in range(option_count):
        try:
            resp = client.images.generate(model=_IMAGE_MODEL, prompt=prompt,
                                          size="1536x1024", n=1)
            url = _image_result_url(resp.data[0]) if resp.data else None
            if url:
                options.append({"url": url, "prompt": prompt})
            else:
                # A response with no image in it is a different failure from a
                # refused request, and saying "failed to generate" for both
                # sends somebody to check a key that was fine.
                options.append({"url": None, "prompt": prompt,
                                "error": (f"{_IMAGE_MODEL} returned no image data. If this "
                                          f"model is not enabled on the account, set "
                                          f"OPENAI_IMAGE_MODEL to one that is.")})
        except Exception as exc:  # noqa: BLE001
            options.append({"url": None, "prompt": prompt,
                            "error": f"Option {chr(65 + index)}: {exc}"})
    return options


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
