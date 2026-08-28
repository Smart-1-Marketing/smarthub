"""Automatic quality checks (spec section 13) — run before a project is
allowed to render. Each check returns {"passed": bool, "message": str} so
the storyboard/review UI can render a simple pass/warn list."""

from . import openai_service, abcd_service
from ..config import (VO_WORD_TARGETS, QR_CODE_RULES, OUTPUT_FORMATS, SOCIAL_RULES,
                      qr_eligible, qr_required, qr_default_on, is_social,
                      spec_channels, spec_channel_mode, publishers_refusing_qr,
                      publisher_qr_note)

# ---------------------------------------------------------------------------
# What blocks a render, and what only warns.
#
# This lived in TWO JavaScript files, each with its own hand-kept `ADVISORY`
# set — a second and third copy of a decision the server already had all the
# information to make. Two panels drawing the same finding in different
# colours is the thing `hub/jsonstore.unmirrored_json_writers()` exists to
# stop, one screen along.
#
# Every check carries a `level` now. `_all_passed` means "nothing FAILED", so
# an advisory finding is shown, coloured amber, and does not refuse the
# render — which is what let the QR code become optional without the panel
# quietly reporting it as fine.
# ---------------------------------------------------------------------------
LEVEL_FAIL = "fail"
LEVEL_WARN = "warn"
LEVEL_PASS = "pass"

# Checks that can only ever advise. A recommendation drawn in an open
# finding's colour is how a page of red teaches people to scroll past it.
ADVISORY_CHECKS = {
    "logo_persistence", "brand", "aspect_ratio", "text_safe_area",
    "qr_code", "abcd_pacing", "abcd_brand_window", "publisher_rules",
    "sound_off",
}

# The published creative specification. Imported defensively because this
# module has to keep working when the Hub is not around it (db.py's STANDALONE
# mode), and a missing Hub must cost this one check rather than the whole QC
# panel -- the same shape routes/stock.py uses for the owned video library.
try:
    from hub import creative_specs
except Exception:                                # noqa: BLE001
    creative_specs = None

_FORMAT_SIZES = {f["id"]: (f["width"], f["height"]) for f in OUTPUT_FORMATS}


MIN_SUFFICIENT_WIDTH = 1280  # below this, an asset is flagged as low-resolution for HD delivery


def run_qc(project_dict, client_dict, scenes):
    checks = {}

    checks["timing"] = _check_timing(project_dict, scenes)
    checks["scene_assets"] = _check_scene_assets(scenes)
    checks["voice_fits"] = _check_voice_fits(project_dict)
    checks["cta"] = _check_cta(project_dict, client_dict)
    checks["brand"] = _check_brand(client_dict)
    checks["resolution"] = _check_resolution(scenes)
    checks["aspect_ratio"] = _check_aspect_ratio(scenes, project_dict)
    checks["text_safe_area"] = _check_text_safe_area(project_dict)
    checks["spelling"] = _check_spelling(project_dict, client_dict, scenes)
    checks["qr_code"] = _check_qr_code(project_dict, scenes)
    checks["logo_persistence"] = _check_logo_persistence(project_dict)
    checks["youtube_hook"] = _check_youtube_hook(project_dict, scenes)
    checks["creative_spec"] = _check_creative_spec(project_dict)
    checks["social_hook"] = _check_social_hook(project_dict, scenes)
    checks["sound_off"] = _check_sound_off(project_dict, scenes)
    checks["abcd_pacing"] = _check_pacing(project_dict, scenes)
    checks["abcd_brand_window"] = _check_brand_window(project_dict, scenes)
    checks["publisher_rules"] = _check_publisher_rules(project_dict)

    for key, result in checks.items():
        if key.startswith("_"):
            continue
        result.setdefault(
            "level",
            LEVEL_PASS if result["passed"]
            else (LEVEL_WARN if key in ADVISORY_CHECKS else LEVEL_FAIL))

    # "Nothing failed", not "everything passed". An advisory finding is a
    # finding somebody should read, not a reason to refuse the render.
    checks["_all_passed"] = not any(
        c.get("level") == LEVEL_FAIL for k, c in checks.items() if not k.startswith("_"))
    checks["_warnings"] = sorted(
        k for k, c in checks.items()
        if not k.startswith("_") and c.get("level") == LEVEL_WARN)
    checks["_abcd"] = abcd_service.score(scenes, project_dict.get("length_seconds"),
                                          project_dict.get("platform", "both"))
    return checks


def _check_timing(project_dict, scenes):
    target = project_dict.get("length_seconds", 0)
    if not scenes:
        return {"passed": False, "message": "No scenes in storyboard yet."}
    total = max((s["end"] for s in scenes), default=0)
    ok = abs(total - target) <= 0.5
    return {"passed": ok,
            "message": f"Commercial is {total:.1f} seconds (target {target}s)." if ok else
                       f"Commercial is {total:.1f}s but should be {target}s — scenes don't add up."}


def _check_scene_assets(scenes):
    """Every scene has something to show, and nothing is still being made.

    This is the check that was missing while HeyGen clips were generated and
    never attached: a scene marked "spokesperson" with no asset_url passed
    every other check, and `creatomate_service.build_source` then emitted an
    element with no `source` — a blank segment in a commercial a client
    received, with no error anywhere. An unfinished scene must block the
    render, not render as nothing.
    """
    if not scenes:
        return {"passed": False, "message": "No scenes in storyboard yet."}

    empty, pending, failed, mocked, short = [], [], [], [], []
    for index, scene in enumerate(scenes, start=1):
        meta = scene.get("asset_meta") or {}

        # An AI video job is the same shape as a spokesperson one: generated,
        # then attached later. Unattached, the scene renders as nothing.
        video_job = meta.get("runway_job") or {}
        if video_job and not meta.get("runway_url"):
            if video_job.get("status") == "failed":
                failed.append(index)
            elif video_job.get("_mock"):
                mocked.append(index)
            else:
                pending.append(index)
            continue

        # A 5-second clip on a 7-second scene goes black for two seconds, and
        # nothing upstream notices: the element simply runs out.
        clip = meta.get("clip_seconds")
        scene_len = float(scene.get("end") or 0) - float(scene.get("start") or 0)
        if clip and scene_len - float(clip) > 0.05:
            short.append(index)

        job = meta.get("heygen_job") or {}
        has_presenter = bool(meta.get("spokesperson_url"))
        if job and not has_presenter:
            if job.get("status") == "failed":
                failed.append(index)
            elif job.get("_mock"):
                mocked.append(index)
            else:
                pending.append(index)
            continue
        # A CTA scene is allowed to be text-only — the end card is drawn as an
        # overlay, so it needs no footage behind it.
        if not scene.get("asset_url") and not has_presenter and not scene.get("is_cta"):
            empty.append(index)

    if failed:
        return {"passed": False,
                "message": f"Presenter clip failed on scene(s) {_join(failed)} — regenerate it or "
                           f"pick different footage before rendering."}
    if pending:
        return {"passed": False,
                "message": f"Presenter clip still generating on scene(s) {_join(pending)}. Wait for "
                           f"HeyGen to finish — rendering now would leave those scenes blank."}
    if mocked:
        return {"passed": False,
                "message": f"Scene(s) {_join(mocked)} ran the presenter in mock mode, so no video "
                           f"exists. Set a HeyGen key and regenerate."}
    if empty:
        return {"passed": False,
                "message": f"Scene(s) {_join(empty)} have no footage or presenter on them."}
    if short:
        return {"passed": False,
                "message": f"Scene(s) {_join(short)} run longer than the clip attached to them, "
                           f"so they would go black partway. Shorten the scene or regenerate "
                           f"the clip at a longer length."}
    return {"passed": True, "message": f"All {len(scenes)} scenes have an asset attached."}


def _join(numbers):
    return ", ".join(str(n) for n in numbers)


def _check_voice_fits(project_dict):
    script = project_dict.get("script") or {}
    length = project_dict.get("length_seconds")
    wc = script.get("word_count")
    if wc is None:
        return {"passed": False, "message": "No script generated yet."}
    lo, hi = VO_WORD_TARGETS.get(length, (0, 10_000))
    ok = lo <= wc <= hi
    return {"passed": ok,
            "message": f"Narration is {wc} words (target {lo}-{hi})." if ok else
                       f"Narration is {wc} words — outside the {lo}-{hi} target for :{length:02d}, "
                       f"will likely feel rushed or drag."}


def _check_cta(project_dict, client_dict):
    cta = project_dict.get("cta") or {}
    has_contact = bool(cta.get("website") or cta.get("phone") or client_dict.get("website")
                        or client_dict.get("phone"))
    if project_dict.get("length_seconds") == 5:
        # :05 bumpers are brand recall only — a website/logo is enough, a
        # phone number/QR is explicitly NOT expected per the best-practices
        # brief ("do not include a phone number/QR code here").
        has_contact = bool(cta.get("website") or client_dict.get("website") or client_dict.get("logo_url"))
        return {"passed": has_contact,
                "message": "Logo/website present for brand recall." if has_contact else
                           "No logo or website set — a :05 bumper needs at least one for brand recall."}
    return {"passed": has_contact,
            "message": "Website or phone included in CTA." if has_contact else
                       "No website or phone number set on the CTA scene."}


def _check_brand(client_dict):
    has_logo = bool(client_dict.get("logo_url"))
    return {"passed": has_logo,
            "message": "Client logo on file." if has_logo else
                       "No logo on file for this client — CTA end card will be text-only."}


def _check_resolution(scenes):
    low_res = [s for s in scenes if s.get("asset_meta", {}).get("width")
               and s["asset_meta"]["width"] < MIN_SUFFICIENT_WIDTH]
    ok = len(low_res) == 0
    return {"passed": ok,
            "message": "All assets meet the minimum resolution." if ok else
                       f"{len(low_res)} scene(s) use footage below {MIN_SUFFICIENT_WIDTH}px wide — "
                       f"may look soft on CTV."}


def _check_aspect_ratio(scenes, project_dict):
    # Placeholder heuristic: flag scenes missing asset_meta width/height entirely
    # (can't verify crop safety without it). Real crop-safety check happens
    # client-side in the storyboard editor's preview crop guides.
    missing = [s for s in scenes if s.get("asset_type") in ("stock", "ai_generated")
               and not s.get("asset_meta", {}).get("width")]
    ok = len(missing) == 0
    return {"passed": ok,
            "message": "All footage has known dimensions for crop-safety." if ok else
                       f"{len(missing)} scene(s) missing source dimensions — verify crop before rendering."}


def _check_text_safe_area(project_dict):
    cta = project_dict.get("cta") or {}
    ok = bool(cta.get("style"))
    return {"passed": ok,
            "message": "CTA style selected — text will use the safe-area template." if ok else
                       "No CTA style selected yet; default safe-area template will be used."}


def _check_spelling(project_dict, client_dict, scenes):
    script_text = " ".join((s.get("narration") or "") for s in scenes)
    if not script_text.strip():
        return {"passed": False, "message": "No narration to check yet."}
    issues = openai_service.qc_spelling_check(script_text, client_dict)
    ok = len(issues) == 0
    return {"passed": ok,
            "message": "No spelling/name issues found." if ok else "; ".join(issues)}


def _check_qr_code(project_dict, scenes):
    """The QR code: optional everywhere, and still checked properly when on.

    It used to be REQUIRED on CTV and the check blocked the render without
    one — which made a spot built for Amazon Streaming TV non-compliant by our
    own rule. Amazon supports neither QR interactivity nor click-encouraging
    CTAs; Roku takes interactive overlays and Amazon does not. So the tool was
    insisting on the one element a whole class of the buy will reject.

    Nothing requires one now (`QR_CODE_RULES["required_platforms"]` is empty).
    A missing code is advice; a code that is switched ON and broken is still a
    block, because "optional to include" is not "optional to do properly" — a
    code too small or held too briefly renders on the end card looking exactly
    like one that works.
    """
    length = project_dict.get("length_seconds")
    platform = project_dict.get("platform", "both")
    if not qr_eligible(length):
        return {"passed": True,
                "message": (f"A :{int(length or 0):02d} carries no QR code by design — "
                            "it is too short to scan.")}

    cta = project_dict.get("cta") or {}

    if not cta.get("qr_enabled"):
        # Where a code would have been the default, its absence is worth a
        # note. Where it would not, saying nothing is right: a warning that
        # fires on every social spot is a warning nobody reads.
        if qr_default_on(length, platform):
            return {"passed": False, "level": LEVEL_WARN,
                    "message": ("No QR code on this cut. A CTV viewer cannot click, so "
                                "the only response paths left are the spoken phone "
                                "number and the domain on the end card. That is a "
                                "choice, not a fault — and it is the right choice on "
                                "an Amazon Streaming TV buy, which does not take one.")}
        if is_social(platform):
            return {"passed": True,
                    "message": ("Social spot — no QR code needed. The ad is already "
                                "tappable, and a code asks somebody to scan the phone "
                                "they are holding.")}
        return {"passed": True,
                "message": ("YouTube-only spot — no QR code, and none needed: a "
                            "clickable end screen covers the same job.")}

    # Enabled and broken is a different thing from absent, and stays a block.
    # "Optional to include" is not "optional to do properly": a code too small
    # or held too briefly is a code nobody can scan, printed on the end card as
    # though it worked.
    if not (cta.get("qr_image_url") or cta.get("qr_data_url")):
        return {"passed": False, "level": LEVEL_FAIL,
                "message": "QR code is enabled but hasn't been generated yet."}
    if not cta.get("qr_target_url"):
        # A code with nothing behind it renders as a perfectly scannable
        # square that opens nothing. hub/qr_codes.destination() refuses to
        # invent a destination, so this is where that refusal surfaces.
        return {"passed": False,
                "message": ("QR code is enabled but has no destination. Add a landing "
                            "page on the brief, or a website on the CTA.")}

    cta_scene = next((s for s in scenes if s.get("is_cta")), None)
    hold_seconds = (cta_scene["end"] - cta_scene["start"]) if cta_scene else 0
    min_hold = QR_CODE_RULES["min_duration_seconds"]
    if hold_seconds < min_hold:
        return {"passed": False,
                "message": f"QR code only holds for {hold_seconds:.1f}s on the end card — needs at "
                           f"least {min_hold}s so someone can pull out their phone and scan it."}
    return {"passed": True,
            "message": f"QR code enabled, {QR_CODE_RULES['min_screen_pct']}%+ of frame, holds for "
                       f"{hold_seconds:.1f}s ({cta.get('qr_corner', 'bottom-right')})."}


def _check_logo_persistence(project_dict):
    """Persistent/recurring logo bug so a CTV viewer who looks away mid-spot
    still catches the brand — recommended (not hard-required) for 15/30/60s."""
    length = project_dict.get("length_seconds")
    if not qr_eligible(length):  # same eligibility window as QR (15/30/60)
        return {"passed": True, "message": ":05 bumpers already run the logo full-treatment throughout."}
    cta = project_dict.get("cta") or {}
    ok = bool(cta.get("logo_persistent"))
    return {"passed": ok,
            "message": "Persistent logo bug enabled." if ok else
                       "No persistent logo bug — recommended so viewers who look away mid-spot still "
                       "catch the brand. Enable it in the CTA Builder."}


def _check_youtube_hook(project_dict, scenes):
    """For skippable YouTube ads, the first 5 seconds needs to work as a
    standalone hook or viewers click Skip before the message lands."""
    platform = project_dict.get("platform", "both")
    if platform not in ("youtube", "both"):
        return {"passed": True, "message": "Not running on YouTube — skip-hook check not applicable."}
    if project_dict.get("length_seconds") == 5:
        return {"passed": True, "message": "Whole :05 spot is the hook."}
    if not scenes:
        return {"passed": False, "message": "No scenes yet to evaluate the opening hook."}

    first = scenes[0]
    has_content = bool((first.get("visual_description") or "").strip() or (first.get("narration") or "").strip())
    within_5s = first.get("end", 0) <= 5.5
    ok = has_content and within_5s
    if not has_content:
        return {"passed": False, "message": "First scene has no visual/narration — nothing to hook a "
                                             "skippable-ad viewer in the first 5 seconds."}
    if not within_5s:
        return {"passed": False,
                "message": f"First scene runs to {first.get('end', 0):.1f}s — treat the first 5 seconds "
                           f"as its own mini-hook so viewers don't click Skip before it lands."}
    return {"passed": True, "message": "Opening scene lands within the first 5 seconds — works as a "
                                        "standalone hook for skippable placements."}


# ---------------------------------------------------------------------------
# The published creative specification.
#
# This tool produced finished video for CTV, YouTube and social and never
# checked it against the spec the agency publishes for the people buying that
# inventory. hub/creative_specs.py has held those numbers all along -- read by
# the IO builder's upload manager and by the client galleries -- and asking it
# the same question here means a spot is judged the same way whether it
# arrives through this tool or is attached to an insertion order by hand.
#
# What is judged is the **plan**, not a file: at QC time nothing has been
# rendered. So the dimensions come from the output format the render will be
# submitted at, and the duration from the spot's own length. Those are the two
# things the kit refuses creative over most often, and both are decided before
# a frame exists -- which is the whole reason to ask now rather than after a
# platform has refused the delivery.
# ---------------------------------------------------------------------------
_RANK = {"pass": 0, "warn": 1, "unknown": 2, "fail": 3}


def _best_unit_verdict(channel, width, height, seconds):
    """The kindest verdict this channel can give one planned cut, and the unit.

    "Kindest" is right here and not a fudge: a channel usually sells several
    lengths of the same thing -- YouTube has TrueView and a Bumper -- and a :05
    is a Bumper, not a TrueView that ran short. Judging against every unit and
    keeping the best is how the channel itself would read it. `check()` already
    picks the closest unit *within* a set; this picks between them, which it
    cannot do from a unit_id.
    """
    best = None
    for unit in creative_specs.UNITS:
        if unit.get("channel") != channel or unit.get("kind") != "video":
            continue
        verdict = creative_specs.check(width=width, height=height, fmt="mp4",
                                       duration=float(seconds), unit_id=unit["id"])
        if best is None or _RANK[verdict["result"]] < _RANK[best[0]["result"]]:
            best = (verdict, unit)
    return best


def _label(channel):
    return creative_specs.CHANNEL_LABELS.get(channel, channel)


def _unit_phrase(unit, channel):
    """" as <unit>", or nothing where the unit adds nothing.

    Several channels carry one unit named after the channel, so the obvious
    phrasing produces "runs as Connected TV / OTT on Connected TV / OTT" —
    which reads as a bug in the sentence and costs the rest of it its
    credibility.
    """
    name = unit.get("name") or ""
    return "" if not name or name == _label(channel) else f" as {name}"


def _check_creative_spec(project_dict):
    if creative_specs is None:
        return {"passed": True,
                "message": "Not measured — the creative spec kit is unavailable here."}

    platform = project_dict.get("platform", "both")
    length = project_dict.get("length_seconds") or 0
    formats = project_dict.get("formats") or ["16:9"]
    mode = spec_channel_mode(platform)

    problems, accepted, unmapped = [], [], []

    for fmt in formats:
        channels = spec_channels(platform, fmt)
        if not channels:
            # A crop nobody sells on this buy. Naming it beats judging it
            # against the nearest channel and reporting a verdict about a
            # placement that does not exist -- creative_specs.check() makes
            # the same distinction with its own "unknown" result.
            unmapped.append(fmt)
            continue

        width, height = _FORMAT_SIZES.get(fmt, (0, 0))
        refused, allowed = [], []
        for channel in channels:
            best = _best_unit_verdict(channel, width, height, length)
            if best is None:
                continue
            verdict, unit = best
            if verdict["result"] in ("fail", "warn"):
                refused.append({"channel": _label(channel), "why": verdict["summary"]})
            else:
                allowed.append({"channel": _label(channel), "as": _unit_phrase(unit, channel)})

        if not refused and not allowed:
            unmapped.append(fmt)
            continue

        if mode == "any":
            # Bought per network. One that takes it is a pass, and the ones
            # that would not are named rather than dropped: a rep placing this
            # needs to know where it can go, not merely that somewhere can.
            if allowed:
                accepted.append(f"{fmt} runs on "
                                + ", ".join(a["channel"] + a["as"] for a in allowed))
                for r in refused:
                    accepted.append(f"but not on {r['channel']} — {r['why']}")
            else:
                problems.extend(f"{fmt} on {r['channel']} — {r['why']}" for r in refused)
        else:
            # One file, every channel in the buy. A cut half the buy refuses
            # is not a pass.
            problems.extend(f"{fmt} on {r['channel']} — {r['why']}" for r in refused)
            if allowed:
                accepted.append(f"{fmt} runs on "
                                + ", ".join(a["channel"] + a["as"] for a in allowed))

    where = f" Checked against the spec kit ({creative_specs.SPEC_KIT_URL})."

    if problems:
        return {"passed": False,
                "message": ("This cut is outside the published spec — "
                            + "; ".join(problems) + "." + where)}
    if not accepted:
        return {"passed": True,
                "message": ("Not measured — the kit maps no video unit for "
                            + ", ".join(unmapped) + f" on a {platform} buy, so nothing "
                            "was checked. Check by hand against "
                            + creative_specs.SPEC_KIT_URL + ".")}
    message = "; ".join(accepted) + "."
    if unmapped:
        # Never silently. A format skipped inside a passing check is a format
        # reported as fine when nothing looked at it.
        message += (" " + ", ".join(unmapped) + " was not measured — the kit maps no "
                    "video unit for it on this buy.")
    return {"passed": True, "message": message + where}


# ---------------------------------------------------------------------------
# The two things a social cut has to do that a CTV one does not.
#
# Both are checked here rather than asked for in the prompt, for the reason
# hub/blog_spec.py gives about a client's "never mention" list: a prompt is a
# request, and "the model was told to" is not evidence that it did.
# ---------------------------------------------------------------------------
def _check_social_hook(project_dict, scenes):
    platform = project_dict.get("platform", "both")
    if not is_social(platform):
        return {"passed": True, "message": "Not a social spot — feed-hook check not applicable."}
    if not scenes:
        return {"passed": False, "message": "No scenes yet to evaluate the opening hook."}

    window = SOCIAL_RULES["hook_seconds"]
    first = scenes[0]
    has_content = bool((first.get("visual_description") or "").strip()
                       or (first.get("narration") or "").strip())
    if not has_content:
        return {"passed": False,
                "message": ("The first scene has no visual or narration — there is nothing "
                            "to stop a thumb with.")}
    # A first scene that runs long is the classic CTV habit carried into a
    # feed: a slow establishing shot the viewer never sees the end of.
    if float(first.get("end") or 0) > window + 0.5:
        return {"passed": False,
                "message": (f"The opening scene runs to {float(first.get('end') or 0):.1f}s. In a "
                            f"feed the hook has about {window:g} seconds — cut the opening "
                            f"shorter, or lead with the most arresting moment in the spot.")}
    return {"passed": True,
            "message": f"Opening lands inside the first {window:g} seconds."}


def _check_sound_off(project_dict, scenes):
    """A claim spoken and never shown is a claim a muted feed never hears.

    Only social is checked. CTV plays with sound by design and YouTube's
    in-stream inventory does too, so requiring burned-in text there would be
    a finding on every spot — which is how a check gets switched off.
    """
    platform = project_dict.get("platform", "both")
    if not is_social(platform):
        return {"passed": True, "message": "Not a social spot — sound-off check not applicable."}

    cta = project_dict.get("cta") or {}
    spoken = [s for s in scenes if (s.get("narration") or "").strip() and not s.get("is_cta")]
    if not spoken:
        return {"passed": True, "message": "No spoken narration to caption."}

    # What is genuinely on screen: the end card's own text. Anything else
    # would be a claim about overlays this tool does not yet build, and
    # asserting one is worse than reporting the gap.
    on_screen = any((cta.get(field) or "").strip()
                    for field in ("offer", "headline", "website"))
    if not on_screen:
        return {"passed": False,
                "message": ("Nothing in this spot is written on screen. " 
                            + SOCIAL_RULES["sound_off_note"]
                            + " Fill in the offer or CTA line so the end card carries it.")}
    return {"passed": True,
            "message": ("The end card carries the offer in text as well as narration. "
                        "Check the middle of the spot the same way — "
                        + SOCIAL_RULES["sound_off_note"].lower())}


def spec_preview(platform, length_seconds, formats):
    """The spec verdict for a spot that does not exist yet.

    The same check the QC panel runs, asked at the moment somebody picks a
    length and a platform rather than after they have built the thing. That
    matters here more than it looks: the kit sells Connected TV at 15-30
    seconds, and this tool offers :05 and :60 on every platform — so a rep can
    pick a combination the buy will refuse, spend an afternoon and a pile of
    provider credits on it, and find out at the end. Two of the four lengths
    on the Start page are in that position for a CTV buy.

    Deliberately not a refusal. A :60 CTV spot is a real thing to want — for a
    website, a lobby screen, a sales meeting — and a tool that blocked it
    would be wrong. What it must not do is stay quiet.
    """
    return _check_creative_spec({"platform": platform,
                                 "length_seconds": length_seconds,
                                 "formats": list(formats or ["16:9"])})


# ---------------------------------------------------------------------------
# The published thresholds, as checks.
#
# services/abcd_service.py holds the numbers and names whose they are. These
# two turn the ones that can be measured from a PLAN into QC rows, because
# that is the moment they are still free to fix — a pacing problem found on a
# rendered file is a re-render.
#
# Both are advisory. Google's detector is how YouTube machine-scores a spot,
# not a delivery requirement, and a tool that refused to render a slow :30
# would be inventing a rule nobody publishes.
# ---------------------------------------------------------------------------
def _check_pacing(project_dict, scenes):
    length = project_dict.get("length_seconds") or 0
    if not scenes:
        return {"passed": False, "level": LEVEL_WARN,
                "message": "No shots to measure yet."}
    if int(length) in abcd_service.BUMPER_LENGTHS:
        return {"passed": True,
                "message": (f"A :{int(length):02d} is one or two shots by design — "
                            "pacing thresholds do not apply to a bumper.")}

    result = abcd_service.score(scenes, length, project_dict.get("platform", "both"))
    rows = {r["key"]: r for r in result["rows"]}
    avg = rows.get("avg_shot_seconds") or {}
    cut = rows.get("first_cut_ms") or {}
    targets = abcd_service.shot_targets(length)

    problems = [r["message"] for r in (avg, cut) if r and r.get("measured") and not r["passed"]]
    if problems:
        return {"passed": False, "level": LEVEL_WARN,
                "message": (" ".join(problems)
                            + f" A :{int(length):02d} wants about {targets['low']}-"
                              f"{targets['high']} shots. Source: {avg.get('source', '')}.")}
    return {"passed": True,
            "message": (avg.get("message", "") + " " + cut.get("message", "")).strip()}


def _check_brand_window(project_dict, scenes):
    """How soon the brand or product is described as being on screen.

    Amazon's window (3.0s) is tighter than Google's (5.0s) and a CTV spot is
    judged against the tighter one — passing the looser rule and being refused
    by the buy is the failure mode worth avoiding.

    Measured from what the shots SAY, which is honest about what it is: this
    reads shot descriptions, not pixels. A spot whose shots mention neither
    the brand nor the product is reported as **not measured**, never as a
    pass, because a green tick over a rule nothing could check is the
    confident wrong answer.
    """
    if not scenes:
        return {"passed": False, "level": LEVEL_WARN, "message": "No shots to measure yet."}
    result = abcd_service.score(scenes, project_dict.get("length_seconds"),
                                 project_dict.get("platform", "both"))
    row = next((r for r in result["rows"] if r["key"].startswith("brand_by_seconds")), None)
    if not row:
        return {"passed": True, "message": "Not measured."}
    if not row["measured"]:
        return {"passed": True, "level": LEVEL_WARN,
                "message": (row["message"] + " Name the brand, the product or the "
                            "storefront in an early shot's description and this can "
                            "be checked.")}
    return {"passed": row["passed"],
            "level": LEVEL_PASS if row["passed"] else LEVEL_WARN,
            "message": row["message"] + f" Source: {row['source']}."}


def _check_publisher_rules(project_dict):
    """What the chosen publishers refuse.

    Exists for one case and says so: Amazon Streaming TV supports neither QR
    interactivity nor click-encouraging CTAs, and a rep who switched a code on
    for an Amazon buy had built something Amazon will reject with nothing in
    the tool saying so. Optional is not the same as silent.

    A publisher whose specs say nothing on the point is not evidence of
    permission — it is simply absent here, and absence is reported as nothing
    to say rather than as an all-clear.
    """
    publishers = (project_dict.get("brief") or {}).get("publishers") or []
    if not publishers:
        return {"passed": True,
                "message": ("No publishers named on this buy, so nothing platform-specific "
                            "was checked.")}
    refusing = publishers_refusing_qr(publishers)
    cta = project_dict.get("cta") or {}
    if refusing and cta.get("qr_enabled"):
        return {"passed": False, "level": LEVEL_WARN,
                "message": publisher_qr_note(publishers)}
    if refusing:
        return {"passed": True,
                "message": (", ".join(refusing) + " does not take a QR code, and this cut "
                            "does not carry one.")}
    return {"passed": True,
            "message": "Nothing the named publishers refuse is on this cut."}
