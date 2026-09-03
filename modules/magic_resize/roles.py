"""What each object on the design *is*, which is what makes a resize work.

Scaling everything proportionally is fine between neighboring aspect ratios
and produces nonsense between distant ones: a square design squeezed into a
728x90 leaderboard gives eight illegibly thin elements rather than a
leaderboard. What separates the two is knowing that this object is the logo
and that one is the call to action, so a leaderboard can put the logo left and
flow the copy beside it instead of shrinking a stack.

Three rules, and each is a way a role goes quietly wrong.

**A role is inferred from how the object arrived, never from what it looks
like.** An object added through the Logo panel is a logo; one added through
Backgrounds is a background. Guessing from geometry — "the biggest text is
the headline" — is right most of the time and silently wrong on the design
where it matters, and a wrong role does not error: it produces a
plausible-looking leaderboard with the disclaimer where the headline goes.

**Ask once rather than guess, and only where a guess would be load-bearing.**
A CTA is a button-shaped group and there is no reliable way to tell it from
any other group, so `needs_ask()` names it and the editor asks. Asking about
every object is a form nobody fills in; asking about none is the guess above.

**Nothing is ever inferred into `cta_button`.** It is the one role a
compliance check requires, so a guess there means QC ticking a box about an
object nobody confirmed — the confident wrong answer, on the check.
"""
from __future__ import annotations

BACKGROUND = "background"
LOGO = "logo"
HEADLINE = "headline"
SUBHEADLINE = "subheadline"
BODY = "body"
CTA = "cta_button"
PRODUCT = "product_image"
DISCLAIMER = "disclaimer"
DECORATIVE = "decorative"
UNSET = ""

# Ordered as they are read on a finished ad rather than alphabetically: the
# dropdown is read top to bottom by somebody looking at the design.
ROLES: list[dict[str, str]] = [
    {"key": BACKGROUND, "label": "Background",
     "hint": "Fills the frame. Scaled to cover, never re-anchored."},
    {"key": LOGO, "label": "Logo",
     "hint": "The client's mark. Every frame is checked for one."},
    {"key": HEADLINE, "label": "Headline",
     "hint": "The line the ad is read for."},
    {"key": SUBHEADLINE, "label": "Supporting line", "hint": ""},
    {"key": BODY, "label": "Body copy", "hint": ""},
    {"key": PRODUCT, "label": "Product image",
     "hint": "The photograph or render the ad is about."},
    {"key": CTA, "label": "Call to action",
     "hint": "The button. Every frame is checked for one, and for whether "
             "anything is covering it."},
    {"key": DISCLAIMER, "label": "Disclaimer",
     "hint": "Legal or rate copy. Never dropped to make a layout fit."},
    {"key": DECORATIVE, "label": "Decorative",
     "hint": "A rule, a flourish, a shape behind something else. Left out "
             "of the overlap check, because overlapping is its job."},
]

ROLE_KEYS = [r["key"] for r in ROLES]

# What a frame must have for QC to pass. House rules — no platform publishes
# either — so they are named as ours wherever a verdict is drawn.
REQUIRED = [LOGO, CTA]

# The panel an object was added through, mapped to what that makes it. This
# is the whole of the automatic inference: it reads provenance, not pixels.
PANEL_ROLE = {
    "logo": LOGO,
    "background": BACKGROUND,
    "backgrounds": BACKGROUND,
    "photo": PRODUCT,
    "photos": PRODUCT,
    "upload": PRODUCT,
    "ai": PRODUCT,
    "icon": DECORATIVE,
    "icons": DECORATIVE,
    "shape": DECORATIVE,
    "shapes": DECORATIVE,
}


def is_role(value: str) -> bool:
    return value in ROLE_KEYS


def infer(*, panel: str = "", kind: str = "", is_first_text: bool = False,
          existing_roles: list[str] | None = None) -> str:
    """The role an object gets when it is added, or "" for ask-me-later.

    Returns UNSET rather than a guess wherever provenance does not settle it.
    An unset role is visible on the frame and in QC; a wrong one is not.
    """
    existing = list(existing_roles or [])
    panel_key = (panel or "").strip().lower()
    if panel_key in PANEL_ROLE:
        role = PANEL_ROLE[panel_key]
        # Only one background, and only one logo. A second object from the
        # same panel is that panel's object again, not a replacement for the
        # first — filing it as one would take the first out of QC's sight.
        if role in (BACKGROUND, LOGO) and role in existing:
            return DECORATIVE if role == BACKGROUND else UNSET
        return role
    if (kind or "").lower() in ("text", "textbox", "i-text"):
        if is_first_text and HEADLINE not in existing:
            return HEADLINE
        if HEADLINE in existing and SUBHEADLINE not in existing:
            return SUBHEADLINE
        return UNSET
    return UNSET


def needs_ask(role: str, kind: str = "") -> bool:
    """Whether the editor should ask about this object once.

    A group with no role is the button-shaped case the docstring names — the
    one place a guess would reach a compliance check — so it is asked about.
    Anything else unset is left to the role dropdown: a modal per object is a
    tool people stop using.
    """
    if role:
        return False
    return (kind or "").lower() in ("group", "rect", "path")


def missing_required(roles: list[str]) -> list[str]:
    present = set(roles or [])
    return [r for r in REQUIRED if r not in present]


def label_for(role: str) -> str:
    for row in ROLES:
        if row["key"] == role:
            return row["label"]
    return "Not set"
