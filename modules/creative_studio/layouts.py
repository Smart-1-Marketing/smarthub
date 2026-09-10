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
