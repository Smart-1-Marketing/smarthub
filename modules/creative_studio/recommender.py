"""The archetype recommender -- WO-CS10 item 3.

"Concepts today asks a blank model for '3 materially different concepts'."
Commercial Builder already has the data this needs: `library_spec.ARCHETYPES`
holds all twelve, `library_spec.suggested_archetypes(industry)` already ranks
them in a category's own preferred order (the "pack default" half of the
build spec's own words), and `hub/commercial_builder/config.py`'s `PLATFORMS`
names the same four ids (`ctv`/`youtube`/`social`/`both`) the build spec's own
"CTV -> price_offer/problem_solution; social -> vignette/ugc; YouTube ->
problem_solution/testimonial" line is written against.

What is genuinely new here is the third factor -- platform fit -- combined
with the pack's own ordering into one ranked list. There is no `weight`
column anywhere in this codebase and none is invented: `PLATFORM_FIT` below
is the platform half of that ranking, read from `library_spec.ARCHETYPES`'s
own twelve keys so an archetype renamed there cannot silently go unmatched
here.

Two of the spec's own archetype names -- "price_offer" and "ugc" -- do not
exist as archetype keys in `library_spec.ARCHETYPES` (which has twelve:
problem_solution, offer_led, testimonial, founder_story, demonstration,
before_after, vignette, comparison, local_pride, seasonal_urgency,
category_education, recruitment). `offer_led` is that vocabulary's own name
for "leads with the offer" -- read as `price_offer`. There is no dedicated
"user-generated content" archetype; `vignette` (an unstaged, lived-in moment)
and `testimonial` (a customer's own voice) are the two closest to what "ugc"
names on a feed, and both are kept in `PLATFORM_FIT["social"]` rather than
inventing a thirteenth archetype this file would be the only reader of.
"""
from __future__ import annotations

PLATFORM_FIT: dict[str, tuple[str, ...]] = {
    "ctv": ("problem_solution", "offer_led"),
    "youtube": ("problem_solution", "testimonial"),
    "social": ("vignette", "testimonial"),
    "both": ("problem_solution", "offer_led"),
}


def rank_archetypes(industry: str, platform: str = "") -> list[dict]:
    """The top three archetypes for this industry and platform, each
    carrying `key`, `label` and `reasons` -- so a screen (or a prompt) can
    say WHY an archetype was suggested rather than presenting a ranking
    nobody can question.

    Never raises: `library_spec` failing to import must not cost concept
    generation the ability to run at all, only the seeding -- the same
    "advisory, never a gate" rule this codebase applies to every recommender
    and compliance check it has.
    """
    try:
        from modules.commercial_builder import library_spec
    except Exception:                                       # noqa: BLE001
        return []

    pack_keys = list(library_spec.suggested_archetypes(industry)["keys"])
    fit_keys = PLATFORM_FIT.get(platform, ())
    all_keys = [k for k in library_spec.ARCHETYPES if k]

    def score(key: str) -> int:
        s = 0
        if key in pack_keys:
            # Earlier in the pack's own order scores higher.
            s += 2 * (len(pack_keys) - pack_keys.index(key))
        if key in fit_keys:
            s += 5
        return s

    ranked = sorted(all_keys, key=lambda k: (-score(k), all_keys.index(k)))
    out = []
    for key in ranked[:3]:
        reasons = []
        if key in pack_keys:
            reasons.append("this industry's own default")
        if key in fit_keys:
            reasons.append("fits the platform")
        if not reasons:
            reasons.append("filling out the set")
        out.append({"key": key, "label": library_spec.ARCHETYPES[key]["label"],
                    "reasons": reasons})
    return out
