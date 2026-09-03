"""Turning a landscape master into a vertical one, and saying what it cost.

The transformation itself is one line -- `c_fill,g_auto` against a target
aspect ratio, the same gravity hub/video_library.background_url() has been
delivering page backgrounds with. Everything else in this file exists because
a crop is a *lossy* edit and the tool that performs one should be honest about
what it threw away.

Two things are worth stating plainly, because both are stated in the project's
own commercial-builder notes and neither is obvious from a preview:

  * A 16:9 frame cropped to 9:16 keeps 31% of its width. Anything the director
    put in the outer thirds -- a second person, a product on a table, a
    lower-third super, a logo bug in a corner -- is gone, and `g_auto` chooses
    ONE of the things it finds rather than keeping all of them.
  * A cropped 16:9 is a starting point for social, not a substitute for a spot
    built vertical. YouTube wants sound-on and a five-second brand window;
    social wants sound-off legibility and a two-second hook. Those are
    different edits, and this tool makes the first one cheap, not unnecessary.

So the plan below carries the arithmetic of what is lost, and `preflight()`
puts eyes on three frames before anybody renders.
"""
from __future__ import annotations

from . import config


def ratio_value(ratio: str) -> float:
    w, _, h = str(ratio or "").partition(":")
    try:
        return float(w) / float(h)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def plan(*, source_width: int, source_height: int,
         ratio: str = config.DEFAULT_RATIO,
         mode: str = config.DEFAULT_MODE,
         focus: str = config.DEFAULT_FOCUS,
         mute: bool = False) -> dict:
    """The transformation, the output size, and what the crop costs."""
    # Every option is resolved to a known key BEFORE anything is built from
    # it, and the resolved key is what the plan reports. Echoing back what was
    # asked for is how a plan comes to say "9x16" while delivering 9:16 -- the
    # transformation would have been right and the record of it wrong, which
    # is the harder of the two to notice.
    ratio = ratio if ratio in config.RATIOS else config.DEFAULT_RATIO
    mode = mode if mode in config.MODES else config.DEFAULT_MODE
    focus = focus if focus in config.FOCUS else config.DEFAULT_FOCUS
    spec = config.RATIOS[ratio]
    gravity = config.FOCUS[focus]["gravity"]
    out_w, out_h = spec["w"], spec["h"]

    # ON VIDEO, `g_auto` MUST BE A COMPONENT OF ITS OWN.
    #
    # Cloudinary rejects `w_1080,h_1920,c_fill,g_auto` outright -- "g_auto must
    # be in a transformation component by itself" -- and accepts the identical
    # crop written as `g_auto/w_1080,h_1920,c_fill`. The rule does not apply to
    # images, which is why it is easy to get wrong, and it is verified against
    # the live account rather than taken from documentation: both forms were
    # submitted, the first failed and the second returned a 1080x1920 file.
    #
    # `g_center` is not `g_auto` and stays inline; it needs no detection pass.
    parts, lead = [], ""
    if mode == "blur":
        # `b_blurred` fills the pad with a blurred, blown-up copy of the frame
        # itself. The two numbers are its intensity and its brightness drop --
        # dimmed a little so the backdrop reads as a backdrop and the subject
        # keeps the eye. No gravity: nothing is being chosen between, because
        # nothing is being discarded.
        parts += [f"w_{out_w}", f"h_{out_h}", "c_pad", "b_blurred:400:12"]
    elif gravity.startswith("g_auto"):
        lead = gravity
        parts += [f"w_{out_w}", f"h_{out_h}", "c_fill"]
    else:
        parts += [f"w_{out_w}", f"h_{out_h}", "c_fill", gravity]
    parts += ["q_auto"]
    if mute:
        parts.append("ac_none")
    transformation = ",".join(parts)
    if lead:
        transformation = f"{lead}/{transformation}"

    return {
        "transformation": transformation,
        "ratio": ratio,
        "mode": mode,
        "focus": focus if mode == "crop" else "",
        "width": out_w,
        "height": out_h,
        "loss": _loss(source_width, source_height, ratio, mode),
        "notes": _notes(source_width, source_height, ratio, mode),
    }


def _loss(source_width: int, source_height: int, ratio: str, mode: str) -> dict:
    """How much of the source frame survives the crop.

    Reported as a percentage of frame AREA and, separately, of width -- the
    second is the number that matters to somebody deciding whether a
    two-person shot survives, and the first is the one that sounds dramatic.
    Both, so neither is the only thing said.
    """
    if mode != "crop":
        return {"kept_area_pct": 100, "kept_width_pct": 100, "kept_height_pct": 100}
    src_w, src_h = int(source_width or 0), int(source_height or 0)
    target = ratio_value(ratio)
    if src_w <= 0 or src_h <= 0 or target <= 0:
        return {}
    source = src_w / src_h
    if target < source:            # taller output: width is cut
        kept_w, kept_h = target / source, 1.0
    else:                          # wider output: height is cut
        kept_w, kept_h = 1.0, source / target
    return {
        "kept_area_pct": round(kept_w * kept_h * 100),
        "kept_width_pct": round(kept_w * 100),
        "kept_height_pct": round(kept_h * 100),
    }


def _notes(source_width: int, source_height: int, ratio: str, mode: str) -> list[str]:
    out: list[str] = []
    loss = _loss(source_width, source_height, ratio, mode)
    kept = loss.get("kept_width_pct")
    if mode == "crop" and kept is not None and kept < 60:
        out.append(f"Only {kept}% of the frame's width survives this crop. "
                   f"Anything in the outer edges — a second person, a product "
                   f"on a table, a lower third, a corner logo — is gone, and "
                   f"automatic framing keeps one subject rather than all of "
                   f"them. Check the preview before you save it.")
    if mode == "crop":
        out.append("Automatic framing picks a subject per shot, so a clip "
                   "that cuts between two people will re-frame at each cut. "
                   "That is usually right and occasionally jarring.")
    if mode == "blur":
        out.append("Nothing is cropped, so nothing is lost — but the subject "
                   "now occupies about half the height it did. Fine for a "
                   "wide shot, weak for anything that was already tight.")
    out.append("This is a cutdown of a landscape master, not a spot built "
               "vertical. It is the right first move and the wrong last one: "
               "sound-off legibility and a two-second hook are the social "
               "edit, and they are decisions, not a crop.")
    return out


def preflight_prompt(ratio: str, mode: str) -> str:
    """What the vision pass is asked, given what is about to happen to the frame.

    Asked as a question about the FRAME rather than about the video, because
    it is being shown three stills: a prompt that asks about pacing or audio
    invites an answer the model cannot have.
    """
    keep = "the middle third" if ratio == "9:16" else "the middle of the frame"
    if mode == "blur":
        return ("These are frames from a video that is about to be placed "
                f"whole inside a {ratio} frame, with blurred bars above and "
                "below. For each frame, say in one sentence whether the "
                "subject is large enough to still read at about half this "
                "height, and whether any on-screen text is small enough to "
                "become unreadable. Answer plainly; say 'fine' if it is fine.")
    return ("These are frames from a landscape video that is about to be "
            f"cropped to {ratio}, keeping roughly {keep} and discarding the "
            "rest. For each frame, say in one sentence what would be lost: "
            "name any person, product, logo, or on-screen text sitting away "
            "from the center. Answer plainly; say 'nothing important' if "
            "nothing would be lost.")
