"""The fixed set of scene compositions a template's scenes are built from.

CLAUDE.md's own account of this Hub is explicit about why this is a small
closed list rather than something an admin free-types per scene: "Layouts...
are the small fixed set of Creatomate compositions the coder builds once...
Every template is a sequence of layouts with durations and slot bindings.
That is what keeps 'add a template' a data operation." A layout is code (it
will eventually become a Creatomate composition, in WO-CS5); a template is
data that picks from the layouts that exist.

Each layout declares:

  * ``slots`` -- the background-type content it can hold (image, video,
    product, or none). A scene's ``background`` layer value is either a
    literal URL, a ``{{variable}}`` reference, or ``"slot:image"`` /
    ``"slot:video"`` meaning "the storyboard editor fills this in" -- the
    shape CLAUDE.md's example template JSON already uses.
  * ``layers`` -- which text/graphic layer keys this layout accepts, from
    ``LAYER_KEYS`` below. A scene's layer dict is validated against this at
    save time so a layout cannot be handed a layer it has nowhere to draw.
  * ``animations`` / ``transitions`` -- the allowed subset of
    ``ANIMATIONS`` / ``TRANSITIONS`` for this layout. A full-bleed hook and
    a static end card do not want the same motion vocabulary.
  * ``aspect_ratios`` -- which of the Hub's supported ratios this
    composition actually works at. ``vertical_caption`` is 9:16 only; most
    others work everywhere.

Nothing here builds a Creatomate source yet -- that is WO-CS5. This is the
vocabulary the gallery, the admin scene editor and (later) the renderer all
read, so a layout added here becomes selectable everywhere at once.
"""
from __future__ import annotations

# Every layer key any layout may offer. A layout's own ``layers`` tuple is a
# subset of these; the label is what the admin scene editor and the (future)
# storyboard editor show a person rather than the raw key.
LAYER_KEYS: dict[str, str] = {
    "background": "Background image or video",
    "headline": "Headline",
    "subheadline": "Subheadline",
    "body": "Body / proof text",
    "offer": "Offer",
    "cta": "Call to action",
    "logo": "Logo",
    "phone": "Phone number",
    "website": "Website",
}

# A layer whose value starts with "slot:" is filled in by whoever is
# building the project rather than resolved from a variable -- these are the
# recognised slot kinds. Anything else naming a slot is refused at save time.
SLOT_KINDS = ("image", "video", "product")

ANIMATIONS = ("none", "fade", "slide_up", "slide_left", "zoom_in")
TRANSITIONS = ("cut", "dissolve", "wipe")
TEXT_POSITIONS = ("top_left", "top_center", "top_right", "center",
                  "bottom_left", "bottom_center", "bottom_right")
ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:5")

# Pixel dimensions for every aspect a variation may target -- WO-CS7.
# "1200x628" is not a ratio a video ever renders at; it is the one still
# image size this module produces (the link/display frame in §1), kept in
# the same table so callers ask one function for a size rather than two.
ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "1200x628": (1200, 628),
}

# What each aspect's own platform reserves for its own UI, in percent of the
# frame from each edge. 9:16's numbers are Instagram/TikTok/Shorts' own
# caption-and-button strip -- content placed in the top 14% or bottom 35% is
# not cropped, it is *covered*, by chrome this module never draws and cannot
# see. The other three aspects have no such overlay (CTV, YouTube and a feed
# square show the whole frame), so their margins are a plain legibility
# inset instead of a platform constraint, and are named as `source: "house"`
# for the reason `services/abcd_service.py` keeps its own house numbers out
# of a table of published platform rules -- CLAUDE.md's rule, applied here
# rather than restated.
SAFE_ZONES: dict[str, dict] = {
    "9:16": {"top": 14, "bottom": 35, "side": 6, "source": "platform"},
    "1:1": {"top": 8, "bottom": 12, "side": 8, "source": "house"},
    "4:5": {"top": 8, "bottom": 15, "side": 8, "source": "house"},
    "16:9": {"top": 8, "bottom": 10, "side": 8, "source": "house"},
}

# Font sizes in vmin -- Creatomate's own unit, already used by the one
# composition WO-CS5 shipped (`_cta_overlay_elements`'s 8vmin/6vmin CTV
# split). vmin is the minimum of the frame's own width and height, which is
# 1080 for every aspect this table names except 16:9 (1080 tall) -- so one
# size table reads consistently across aspects without a second, per-aspect
# multiplier to keep in step.
_SIZES = {"sm": "5vmin", "md": "6vmin", "lg": "7.5vmin", "xl": "9vmin"}


def _text(*, x: str = "50%", y: str, width: str = "80%", size: str = "md",
         weight: str = "800", align: str = "center", panel: bool = False) -> dict:
    """One text layer's position -- the vocabulary every layout's
    ``variants`` table is built from, so a font size or an anchor rule
    changed here changes every layout at once rather than needing to be
    re-typed per aspect.

    ``panel`` adds a translucent backing plate behind the text -- proven
    Creatomate fields (`background_color`, `background_padding`) already in
    use for the QR code overlay in `creatomate_service.py`, reused here
    rather than a second guess at what the account accepts. It is what
    keeps light-coloured type readable over a bright frame without pinning
    every template to a dark scrim baked into the footage itself.
    """
    anchor_x = {"center": "50%", "left": "0%", "right": "100%"}.get(align, "50%")
    spec = {
        "x": x, "y": y, "width": width, "x_anchor": anchor_x, "y_anchor": "50%",
        "font_size": _SIZES.get(size, _SIZES["md"]), "font_weight": weight,
        "text_alignment": align,
    }
    if panel:
        spec["background_color"] = "rgba(10,12,16,0.45)"
        spec["background_padding"] = "3%"
    return spec


def _logo(*, x: str = "50%", y: str = "20%", width: str = "22%") -> dict:
    return {"x": x, "y": y, "width": width, "x_anchor": "50%", "y_anchor": "50%"}


def _panel(*, x: str, y: str = "50%", width: str, height: str = "100%",
          fill: str = "#12151c") -> dict:
    """A solid region for a text-only or split-frame layout to sit on --
    `type: "shape"` with a fill colour, never an image element with no
    `source`, which is what a layout with no background slot produced
    before this file computed anything for it at all."""
    return {"x": x, "y": y, "width": width, "height": height,
           "x_anchor": "50%", "y_anchor": "50%", "fill_color": fill}


LAYOUTS: dict[str, dict] = {
    "hook_fullbleed": {
        "label": "Hook — full bleed",
        "description": "A full-frame background with one bold line over it. "
                        "The opening beat of almost every commercial template.",
        "slots": ("image", "video"),
        "layers": ("background", "headline"),
        "animations": ANIMATIONS,
        "transitions": TRANSITIONS,
        "aspect_ratios": ASPECT_RATIOS,
        "beat_role": "hook",
        "variants": {
            "16:9": {"headline": _text(y="45%", width="70%", size="lg")},
            "9:16": {"headline": _text(y="45%", width="84%", size="lg")},
            "1:1": {"headline": _text(y="45%", width="80%", size="lg")},
            "4:5": {"headline": _text(y="42%", width="82%", size="lg")},
        },
    },
    "problem_split": {
        "label": "Problem — split frame",
        "description": "Background on one half, a headline and the logo on "
                        "the other. The second beat: what the client solves.",
        "slots": ("image", "video"),
        "layers": ("background", "headline", "logo"),
        "animations": ANIMATIONS,
        "transitions": TRANSITIONS,
        "aspect_ratios": ("16:9", "1:1", "4:5"),
        "beat_role": "solution",
        # The background element is always full-bleed (`build_source`'s own
        # per-scene element covers the whole canvas) -- the split is drawn
        # as an opaque panel over the right side of it, never a second,
        # cropped background element, so nothing here depends on Creatomate
        # supporting two competing "fill the frame" elements on one scene.
        "variants": {
            "16:9": {
                "_panel": _panel(x="77.5%", width="45%"),
                "logo": _logo(x="77.5%", y="32%", width="16%"),
                "headline": _text(x="77.5%", y="56%", width="28%", size="md"),
            },
            "1:1": {
                "_panel": _panel(x="75%", width="50%"),
                "logo": _logo(x="75%", y="30%", width="20%"),
                "headline": _text(x="75%", y="56%", width="32%", size="sm"),
            },
            "4:5": {
                "_panel": _panel(x="75%", width="50%"),
                "logo": _logo(x="75%", y="28%", width="20%"),
                "headline": _text(x="75%", y="54%", width="32%", size="sm"),
            },
        },
    },
    "proof_lower_third": {
        "label": "Proof — lower third",
        "description": "Background full-frame with a lower-third bar for a "
                        "testimonial line, a stat, or a certification.",
        "slots": ("image", "video"),
        "layers": ("background", "body"),
        "animations": ANIMATIONS,
        "transitions": TRANSITIONS,
        "aspect_ratios": ASPECT_RATIOS,
        "beat_role": "solution",
        "variants": {
            # A true bottom-of-frame bar is broadcast convention and is
            # safe on 16:9/1:1/4:5, which no platform draws its own chrome
            # over. 9:16 moves the identical bar UP into the caption-safe
            # middle band instead -- lower relative to the headline above
            # it, never lower than SAFE_ZONES["9:16"]["bottom"] allows,
            # because a proof line sitting where a Reels caption sits is
            # not proof anybody reads, it is proof a platform's own UI is
            # covering.
            "16:9": {"body": _text(y="85%", width="76%", size="sm", panel=True)},
            "9:16": {"body": _text(y="58%", width="84%", size="sm", panel=True)},
            "1:1": {"body": _text(y="86%", width="80%", size="sm", panel=True)},
            "4:5": {"body": _text(y="82%", width="82%", size="sm", panel=True)},
        },
    },
    "offer_card": {
        "label": "Offer card",
        "description": "A branded card carrying the offer and a call to "
                        "action -- no background image, so the offer is the "
                        "only thing on screen.",
        "slots": (),
        "layers": ("headline", "offer", "cta"),
        "animations": ANIMATIONS,
        "transitions": TRANSITIONS,
        "aspect_ratios": ASPECT_RATIOS,
        "beat_role": "solution",
        "variants": {
            "16:9": {"headline": _text(y="32%", size="md"),
                    "offer": _text(y="52%", size="xl"),
                    "cta": _text(y="72%", size="sm")},
            "9:16": {"headline": _text(y="32%", width="84%", size="md"),
                    "offer": _text(y="48%", width="84%", size="xl"),
                    "cta": _text(y="60%", width="84%", size="sm")},
            "1:1": {"headline": _text(y="28%", size="md"),
                   "offer": _text(y="50%", size="xl"),
                   "cta": _text(y="74%", size="sm")},
            "4:5": {"headline": _text(y="28%", size="md"),
                   "offer": _text(y="50%", size="xl"),
                   "cta": _text(y="76%", size="sm")},
        },
    },
    "end_card": {
        "label": "End card",
        "description": "Logo, phone, website and a call to action on a "
                        "branded background -- the closing beat.",
        "slots": ("image",),
        "layers": ("background", "logo", "phone", "website", "cta"),
        "animations": ("none", "fade"),
        "transitions": ("cut", "dissolve"),
        "aspect_ratios": ASPECT_RATIOS,
        "beat_role": "close",
        # Same shape `_cta_overlay_elements` drew before this file existed --
        # logo, the CTA line, phone|website -- except every value now comes
        # from THIS project's own resolved Brand Kit and template variables
        # rather than `CommercialProject.cta`, which nothing sets for a
        # template-bound project and which read empty on every end card
        # Creative Studio has ever rendered.
        "variants": {
            "16:9": {"logo": _logo(y="20%", width="16%"),
                    "cta": _text(y="50%", size="lg"),
                    "phone": _text(x="35%", y="76%", width="30%", size="sm"),
                    "website": _text(x="65%", y="76%", width="30%", size="sm")},
            "9:16": {"logo": _logo(y="22%", width="24%"),
                    "cta": _text(y="42%", width="84%", size="lg"),
                    "phone": _text(y="54%", width="84%", size="sm"),
                    "website": _text(y="60%", width="84%", size="sm")},
            "1:1": {"logo": _logo(y="20%", width="20%"),
                   "cta": _text(y="46%", size="md"),
                   "phone": _text(x="35%", y="70%", width="30%", size="sm"),
                   "website": _text(x="65%", y="70%", width="30%", size="sm")},
            "4:5": {"logo": _logo(y="18%", width="20%"),
                   "cta": _text(y="44%", size="md"),
                   "phone": _text(x="35%", y="72%", width="30%", size="sm"),
                   "website": _text(x="65%", y="72%", width="30%", size="sm")},
        },
    },
    "logo_reveal": {
        "label": "Logo reveal",
        "description": "The logo alone, animated in. Used as a short "
                        "intro or outro bumper rather than inside a longer "
                        "commercial.",
        "slots": (),
        "layers": ("logo",),
        "animations": ("fade", "zoom_in", "slide_up"),
        "transitions": ("cut", "dissolve"),
        "aspect_ratios": ASPECT_RATIOS,
        "beat_role": "close",
        "variants": {
            "16:9": {"logo": _logo(y="50%", width="30%")},
            "9:16": {"logo": _logo(y="50%", width="46%")},
            "1:1": {"logo": _logo(y="50%", width="40%")},
            "4:5": {"logo": _logo(y="50%", width="42%")},
        },
    },
    "social_follow": {
        "label": "Social follow card",
        "description": "Logo and a call to action asking for a follow or a "
                        "visit -- the social-platform sibling of the end "
                        "card, without the phone number a feed post has no "
                        "use for.",
        "slots": ("image",),
        "layers": ("background", "logo", "cta"),
        "animations": ANIMATIONS,
        "transitions": TRANSITIONS,
        "aspect_ratios": ("1:1", "9:16", "4:5"),
        "beat_role": "close",
        "variants": {
            "9:16": {"logo": _logo(y="24%", width="26%"),
                    "cta": _text(y="48%", width="84%", size="md")},
            "1:1": {"logo": _logo(y="22%", width="22%"),
                   "cta": _text(y="48%", size="md")},
            "4:5": {"logo": _logo(y="20%", width="22%"),
                   "cta": _text(y="46%", size="md")},
        },
    },
    "vertical_caption": {
        "label": "Vertical caption",
        "description": "Full-frame vertical background with a caption strip "
                        "-- the UGC / social-first shape, 9:16 only.",
        "slots": ("image", "video"),
        "layers": ("background", "headline", "body"),
        "animations": ANIMATIONS,
        "transitions": TRANSITIONS,
        "aspect_ratios": ("9:16",),
        "beat_role": "solution",
        "variants": {
            "9:16": {"headline": _text(y="20%", width="84%", size="md", panel=True),
                    "body": _text(y="58%", width="84%", size="sm", panel=True)},
        },
    },
}


def layout(key: str) -> dict | None:
    return LAYOUTS.get(key)


def layers_for(key: str) -> tuple[str, ...]:
    spec = LAYOUTS.get(key) or {}
    return spec.get("layers", ())


def validate_layers(layout_key: str, layers: dict) -> list[str]:
    """Layer keys a scene's ``layers`` dict carries that its layout does not
    accept. Empty means the scene is valid. Never raises -- an unknown
    layout key is reported as "every layer is invalid" via an empty allowed
    set, which is itself the finding worth surfacing."""
    allowed = set(layers_for(layout_key))
    return [k for k in (layers or {}) if k not in allowed]


def beat_role(layout_key: str) -> str:
    """"hook" / "solution" / "close" -- which part of the commercial's arc
    this layout plays, read by WO-CS8's cutdown logic to decide which
    scenes a :15 keeps from a :30. Every layout not naming one defaults to
    "solution": a scene neither the opening line nor the closing card is
    the body of the spot, whatever its own composition looks like."""
    spec = LAYOUTS.get(layout_key) or {}
    return spec.get("beat_role", "solution")


def variant_for(layout_key: str, aspect_ratio: str) -> dict:
    """This layout's composition at one aspect, or the layout's own first
    supported aspect's composition if the exact one was never authored --
    never an empty result for a layout that supports the aspect at all.
    `aspect_ratios` is the contract those two must agree on, and
    `test_creative_studio.py` holds them to it."""
    spec = LAYOUTS.get(layout_key) or {}
    variants = spec.get("variants") or {}
    if aspect_ratio in variants:
        return variants[aspect_ratio]
    supported = spec.get("aspect_ratios") or ()
    fallback = supported[0] if supported else None
    return variants.get(fallback, {})


def elements_for(layout_key: str, aspect_ratio: str, layer_values: dict, *,
                 logo_url: str = "", phone: str = "", website: str = "") -> list[dict]:
    """The Creatomate overlay elements for one scene, at one aspect --
    positioned text and logo, built from already-resolved values.

    ``layer_values`` is a scene's own resolved ``layers`` dict (the shape
    `binder._scene_specs()` builds: ``{key: {"value": str, ...}}``);
    ``logo_url``/``phone``/``website`` are passed separately because those
    three are read off the *project's* resolved variables rather than a
    per-scene layer override -- a phone number is the client's, not this
    scene's. A layer with nothing to show contributes nothing: an empty
    string is not "draw an empty text box", the same reading
    `hub/scan_facts.py` gives a field nobody filled in.

    Keys in the composition starting with ``_`` are structural (the
    split-frame panel) and are drawn unconditionally -- they carry no
    resolved value to be empty.
    """
    values = {
        "logo": logo_url, "phone": phone, "website": website,
    }
    composition = variant_for(layout_key, aspect_ratio)
    elements: list[dict] = []
    for key, position in composition.items():
        if key.startswith("_"):
            elements.append({"type": "shape", **position})
            continue
        value = values.get(key)
        if value is None:
            value = (layer_values.get(key) or {}).get("value") or ""
        if not str(value).strip():
            continue
        if key == "logo":
            elements.append({"type": "image", "source": value, **position})
        else:
            elements.append({"type": "text", "text": value, **position})
    return elements


def _pct(value) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def check_safe_zone(layout_key: str, aspect_ratio: str, layer_values: dict, *,
                    logo_url: str = "", phone: str = "", website: str = "") -> list[dict]:
    """Which of this layout's text elements, at this aspect, sit outside
    the aspect's own safe margins -- WO-CS7 item 5. Advisory: this names
    what is outside the zone, it never refuses to build the composition,
    the `QR_CODE_RULES` rule CLAUDE.md gives about a check that refuses the
    correct thing being a check somebody switches off.

    Returns ``[]`` when nothing is outside the zone, never when the layout
    or aspect is unrecognized -- an empty finding list must mean "checked
    and clean," not "could not check," so an unknown pair is reported by
    name instead of silently passing.
    """
    zone = SAFE_ZONES.get(aspect_ratio)
    if zone is None:
        return [{"layer": "*", "reason": f"no safe-zone rule for {aspect_ratio!r}"}]
    elements = elements_for(layout_key, aspect_ratio, layer_values,
                            logo_url=logo_url, phone=phone, website=website)
    findings = []
    for el in elements:
        if el.get("type") == "shape":
            # A structural panel (the split-frame background) is meant to
            # reach the frame edge -- it is not information a platform's
            # own UI can obscure, so it is not what this check is for.
            continue
        label = el.get("text") or el.get("source") or el.get("type", "")
        label = (str(label)[:40] + "…") if len(str(label)) > 40 else str(label)
        y = _pct(el.get("y"))
        if y is not None:
            if y < zone["top"]:
                findings.append({"layer": label,
                                 "reason": f"sits above the top {zone['top']}% safe margin"})
            elif y > 100 - zone["bottom"]:
                findings.append({"layer": label,
                                 "reason": f"sits below the bottom {zone['bottom']}% safe margin"})
        x = _pct(el.get("x"))
        width = _pct(el.get("width")) or 0
        if x is not None:
            left = x - width / 2 if el.get("x_anchor") == "50%" else x
            right = x + width / 2 if el.get("x_anchor") == "50%" else x + width
            if left < zone["side"]:
                findings.append({"layer": label,
                                 "reason": f"crosses the {zone['side']}% side margin"})
            elif right > 100 - zone["side"]:
                findings.append({"layer": label,
                                 "reason": f"crosses the {zone['side']}% side margin"})
    return findings
