"""Automatic quality checks (spec section 13) — run before a project is
allowed to render. Each check returns {"passed": bool, "message": str} so
the storyboard/review UI can render a simple pass/warn list."""

from . import openai_service
from ..config import VO_WORD_TARGETS, QR_CODE_RULES, qr_eligible


MIN_SUFFICIENT_WIDTH = 1280  # below this, an asset is flagged as low-resolution for HD delivery


def run_qc(project_dict, client_dict, scenes):
    checks = {}

    checks["timing"] = _check_timing(project_dict, scenes)
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

    checks["_all_passed"] = all(c["passed"] for k, c in checks.items() if k != "_all_passed")
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
    """QR is 'essential for CTV where clicks aren't possible' per the
    best-practices brief — required for CTV/Both platforms on 15/30/60s
    spots, held on screen long enough to actually scan. Not applicable to
    :05 bumpers (excluded from qr_eligible)."""
    length = project_dict.get("length_seconds")
    platform = project_dict.get("platform", "both")
    if not qr_eligible(length):
        return {"passed": True, "message": ":05 bumpers don't carry a QR code by design."}

    cta = project_dict.get("cta") or {}
    requires_qr = platform in ("ctv", "both")
    if not requires_qr:
        # YouTube-only spots can lean on clickable end screens instead.
        ok = True
        msg = "YouTube-only spot — QR optional (clickable end screen covers the CTV use case)."
        if cta.get("qr_enabled"):
            msg = "QR code enabled."
        return {"passed": ok, "message": msg}

    if not cta.get("qr_enabled"):
        return {"passed": False,
                "message": "No QR code enabled — required for CTV since viewers can't click the ad. "
                           "Turn it on in the CTA Builder."}
    if not (cta.get("qr_image_url") or cta.get("qr_data_url")):
        return {"passed": False, "message": "QR code is enabled but hasn't been generated yet."}

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
