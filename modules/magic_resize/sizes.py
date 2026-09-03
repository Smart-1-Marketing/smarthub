"""The sizes a design is resized into, and where each number comes from.

This module deliberately holds **no dimension the spec kit already publishes**.
``hub/creative_specs.py`` is the transcription of the S1M CREATIVE SPEC KIT and
``kit_drift()`` holds it against the published page; ``modules/ad_builder`` has
its renderer's own copy for the sizes it renders; and
``modules/image_creator.CANVAS_PRESETS`` is a canvas picker. That is already
three descriptions of one fact, agreeing by luck rather than by construction —
the drift ``hub/storage.py`` and the two rate cards exist to stop. A fourth
would be the one that goes stale, because it is the one no check reads.

So a size mapped by the kit carries a ``unit`` id and nothing else: its width,
its height and its weight ceiling are read from the kit at import, and a
resize is judged by ``creative_specs.check()`` rather than by a limit restated
here.

**A unit the kit no longer publishes is dropped and named, never guessed at.**
It goes into ``UNRESOLVED`` and ``check_kit_alignment()`` reports it, which is
the answer this codebase gives everywhere a source it depends on stops
answering: a size we cannot verify is one we must not silently deliver
against, and a fallback dimension carried here to keep the bundle whole is the
second copy this file exists to refuse.

**A size the kit maps no unit for is ours, and says so.** Four of them are:
the 336x280 and 320x100 IAB units the kit does not weigh, and Google's two
Responsive Display asset sizes, which are an asset pool rather than a banner
(§7 — they are judged on Google's own 5 MB image ceiling, not on a display
weight). Each carries ``source: "house"`` and the reason, the rule
``HOUSE_LEGIBILITY`` in ``services/abcd_service.py`` works to: house guidance
wearing the kit's name is what a client eventually checks.
"""
from __future__ import annotations

from typing import Any

try:                                                   # pragma: no cover
    from hub import creative_specs
except Exception:                                      # noqa: BLE001
    creative_specs = None                              # type: ignore[assignment]

MB = 1024 * 1024

# Google publishes 5 MB per Responsive Display image asset (JPG/PNG, no GIF).
# It is not in the kit — the kit sells display units, and an RDA asset is a
# pool Google composes its own layout around — so it is named here with its
# source rather than folded in as though the kit had said it.
_RDA_MAX_BYTES = 5 * MB
_RDA_SOURCE = ("Google's published ceiling for a Responsive Display Ad image "
               "asset: 5 MB, JPG or PNG, no GIF. Not a spec-kit unit — an RDA "
               "asset is a pool Google lays out itself, not a banner.")

# id -> declaration. `unit` names a hub.creative_specs unit and is the whole
# of what this file says about that size's dimensions and weight. `w`/`h`
# appear ONLY on a house size, where nothing else here publishes one.
_DECLARED: list[dict[str, Any]] = [
    # ---- Display, sized and weighed by the kit ---------------------------
    {"id": "med_rect", "unit": "medium_rectangle", "family": "square_medium"},
    {"id": "leaderboard", "unit": "leaderboard", "family": "leaderboard"},
    {"id": "wide_sky", "unit": "wide_skyscraper", "family": "skyscraper"},
    {"id": "half_page", "unit": "half_page", "family": "skyscraper"},
    {"id": "billboard", "unit": "rising_star", "family": "leaderboard"},
    {"id": "mobile_banner", "unit": "mobile_banner_320", "family": "leaderboard"},

    # ---- Display, ours ---------------------------------------------------
    {"id": "large_rect", "w": 336, "h": 280, "family": "square_medium",
     "label": "Large Rectangle", "source": "house",
     "reason": "An IAB unit the 2026 kit does not weigh. Sized here, and a "
               "delivered file is reported as **not measured** rather than "
               "judged against the Medium Rectangle it merely resembles."},
    {"id": "mobile_lg_banner", "w": 320, "h": 100, "family": "leaderboard",
     "label": "Large Mobile Banner", "source": "house",
     "reason": "An IAB unit the 2026 kit does not weigh. The kit weighs the "
               "320x50 Smartphone Banner and stops there."},

    # ---- Google Responsive Display asset pool ----------------------------
    {"id": "rda_landscape", "w": 1200, "h": 628, "family": "square_medium",
     "label": "Responsive Display (landscape)", "source": "house",
     "max_bytes": _RDA_MAX_BYTES, "weight_source": _RDA_SOURCE,
     "reason": "An asset Google composes its own layout around rather than a "
               "banner it serves whole. Flagged apart from the display "
               "bundle for that reason."},
    {"id": "rda_square", "w": 1200, "h": 1200, "family": "square_medium",
     "label": "Responsive Display (square)", "source": "house",
     "max_bytes": _RDA_MAX_BYTES, "weight_source": _RDA_SOURCE,
     "reason": "As above — an RDA asset, not a served banner."},

    # ---- Social, sized by the kit ---------------------------------------
    {"id": "social_square", "unit": "facebook_image", "family": "square_medium"},
    # The kit publishes Stories by ratio (9:16) and not by size, so the build
    # size is ours against the kit's ratio — the shape `gpt_ads_square` uses:
    # the ratio is the requirement and the resolution is the recommendation.
    {"id": "social_story", "w": 1080, "h": 1920, "family": "story_portrait",
     "label": "Story / Reel", "source": "house",
     "kit_ratio": "stories_image",
     "reason": "The kit publishes Stories by ratio (9:16) rather than by "
               "size. 1080x1920 is the build size; the ratio is the rule."},
    {"id": "social_portrait", "w": 1080, "h": 1350, "family": "story_portrait",
     "label": "Portrait feed", "source": "house",
     "kit_ratio": "instagram_image",
     "reason": "4:5 is inside the ratio range the kit publishes for an "
               "Instagram display unit, and the kit's own build size for it "
               "is square. The portrait resolution is ours."},
]

BUNDLES: dict[str, dict[str, Any]] = {
    "display_standard": {
        "label": "Display Standard (IAB)",
        "sizes": ["med_rect", "large_rect", "leaderboard", "wide_sky",
                  "half_page", "billboard", "mobile_banner",
                  "mobile_lg_banner"],
    },
    "social": {
        "label": "Social",
        "sizes": ["social_square", "social_portrait", "social_story"],
    },
    "google_full": {
        "label": "Google (display + responsive assets)",
        # Named rather than spread from display_standard at import: a bundle
        # that quietly gains a size because another bundle did is a set
        # nobody chose.
        "sizes": ["med_rect", "large_rect", "leaderboard", "wide_sky",
                  "half_page", "billboard", "mobile_banner",
                  "mobile_lg_banner", "rda_landscape", "rda_square"],
    },
    "custom": {"label": "Custom sizes", "sizes": []},
}


def _kit_unit(unit_id: str) -> dict | None:
    if creative_specs is None:
        return None
    by_id = getattr(creative_specs, "BY_ID", {}) or {}
    unit = by_id.get(unit_id)
    return unit if isinstance(unit, dict) else None


def _pair(v: Any) -> tuple[int, int] | None:
    if isinstance(v, (tuple, list)) and len(v) == 2:
        try:
            return int(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
    return None


def _kit_size(unit: dict) -> tuple[tuple[int, int], str] | None:
    """The kit's own build size for a unit, and which field gave it.

    Three fields, because the kit specifies three ways and collapsing them
    loses which is a rule and which is a recommendation. `size` is a fixed
    unit. `sizes` is a unit sold at several device sizes — the first is the
    one built to. `min_size` is a **recommended** build size beside a
    required ratio, which is how every social unit is published: the ratio is
    the rule and the resolution is the recommendation, the distinction
    `gpt_ads_square` already draws. All three are the kit's number rather
    than ours, which is the only thing this file cares about.
    """
    pair = _pair(unit.get("size"))
    if pair:
        return pair, "size"
    sizes = unit.get("sizes")
    if isinstance(sizes, (tuple, list)) and sizes:
        pair = _pair(sizes[0])
        if pair:
            return pair, "sizes"
    pair = _pair(unit.get("min_size"))
    if pair:
        return pair, "min_size"
    return None


def _build() -> tuple[dict[str, dict], list[dict]]:
    sizes: dict[str, dict] = {}
    unresolved: list[dict] = []
    for row in _DECLARED:
        entry = dict(row)
        unit_id = entry.get("unit", "")
        if unit_id:
            unit = _kit_unit(unit_id)
            found = _kit_size(unit) if unit else None
            if not unit or not found:
                unresolved.append({
                    "id": entry["id"], "unit": unit_id,
                    "reason": ("the spec kit no longer maps this unit"
                               if not unit else
                               "the spec kit unit publishes no build size"),
                })
                continue
            (entry["w"], entry["h"]), entry["sized_by"] = found
            entry.setdefault("label", unit.get("name") or entry["id"])
            entry["source"] = "kit"
            entry["max_bytes"] = unit.get("max_bytes")
            entry["formats"] = list(unit.get("formats") or [])
        else:
            entry.setdefault("formats", ["jpg", "jpeg", "png"])
        entry["ratio"] = (entry["w"] / entry["h"]) if entry.get("h") else 0.0
        sizes[entry["id"]] = entry
    return sizes, unresolved


PLATFORM_SIZES, UNRESOLVED = _build()


def get(size_id: str) -> dict | None:
    return PLATFORM_SIZES.get(size_id)


def bundle_sizes(bundle: str) -> list[dict]:
    """The resolvable sizes in a bundle, in the order the bundle names them.

    A bundle naming a size the kit no longer maps comes back **short**, and
    `UNRESOLVED` is what says so — a set of the right size and the wrong
    contents is the failure one step on from a set that is simply wrong.
    """
    names = (BUNDLES.get(bundle) or {}).get("sizes") or []
    return [PLATFORM_SIZES[n] for n in names if n in PLATFORM_SIZES]


def check_kit_alignment() -> dict:
    """Report sizes this module could not resolve against the kit.

    Not measured is its own answer: with `hub.creative_specs` unimportable
    there is nothing to align against, and reporting "no drift" would be a
    clean bill of health about a question nobody asked.
    """
    if creative_specs is None:
        return {"measured": False, "unresolved": [],
                "note": "The spec kit could not be read, so nothing was "
                        "checked against it."}
    return {
        "measured": True,
        "unresolved": list(UNRESOLVED),
        "note": ("Every size the kit publishes is read from it. "
                 f"{len(UNRESOLVED)} could not be resolved."),
    }


def house_sizes() -> list[dict]:
    """The sizes whose dimensions are ours rather than the kit's."""
    return [s for s in PLATFORM_SIZES.values() if s.get("source") == "house"]
