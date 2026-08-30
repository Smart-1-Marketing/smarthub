"""What a spot will cost to make, said before it is made.

Data and arithmetic, no Flask, beside `library_spec.py`, `compliance_spec.py`
and `review_spec.py`: the Start page's preview, the Blueprint panel and the
test all read one description.

## Why the usage page is the wrong place for this

`hub/quotas.py` answers "what did we spend last month", which is the right
question for a bill and the wrong one for a rep about to tick three lengths.
By the time a number appears there the money is gone. The decision that
actually moves it — three lengths or one, AI video or stock, a spokesperson or
a voiceover — is made on the Start page, and nothing there said anything.

## Every figure is a unit count, never a dollar

This deployment holds no published price for HeyGen, Runway or Creatomate:
all three price by plan, and a plan figure nobody set is a number this file
would be inventing. `hub/quotas.py` says the same thing in its own words —
where a provider publishes no ceiling worth citing, none is invented and the
row reads *not measured*.

So an estimate here is in the provider's own unit — clips, seconds of video,
characters of script, renders — and a **dollar figure appears only where the
Hub already holds a real published rate**, which today is OpenAI's image
price and nothing else. That is a smaller claim than a rep might want and it
is the only one that survives being checked.

## It estimates the plan, not the past

Counted from what has been chosen: the lengths, the formats, the production
method, and how many scenes a spot of that length wants. Before a script
exists those are the only facts there are, and they are enough — the shot
count comes from `abcd_service.shot_targets()`, which is the same number the
Blueprint scores against, so the estimate and the thing it estimates cannot
drift.

## What it must never do

**It must not read as a quote.** Nothing here is what the client is charged;
it is what the tools will consume. `hub/rate_card.py` is what a client pays,
and confusing the two on a screen a rep quotes from is the failure
`proposal_spec.py` spends a page on.

**It must not block anything.** A rep who needs a :60 needs a :60. This is a
number beside a choice, never a gate — the `QR_CODE_RULES` rule.
"""

from __future__ import annotations

from .config import VO_WORD_TARGETS, build_sort_key
from .services import abcd_service

# ---------------------------------------------------------------------------
# What each provider bills in, and whether this deployment can price it.
#
# `price_per_unit` is None wherever no published rate is held. That is not a
# gap to fill in with a guess: a plausible dollar figure on a screen a rep
# reads is worse than no figure, because it gets repeated.
# ---------------------------------------------------------------------------
PROVIDERS = {
    "openai_image": {
        "label": "AI stills",
        "unit": "images",
        # The one real published rate the Hub already holds, in
        # hub/quotas.IMAGE_PRICING. Read from there rather than restated, so
        # a price change lands once.
        "price_per_unit": None,      # filled from hub/quotas at call time
        "price_source": "hub/quotas.IMAGE_PRICING",
        "note": "Two options are generated per press, and only one is kept.",
    },
    "runway": {
        "label": "AI video",
        "unit": "seconds of video",
        "price_per_unit": None,
        "price_source": "",
        "note": "Runway bills by duration, and its models return 5- or "
                "10-second clips only — a 4-second shot still costs 5.",
    },
    "heygen": {
        "label": "Spokesperson clips",
        "unit": "clips",
        "price_per_unit": None,
        "price_source": "",
        "note": "One clip per scene the spokesperson appears in.",
    },
    "elevenlabs": {
        "label": "Voiceover",
        "unit": "characters",
        "price_per_unit": None,
        "price_source": "",
        "note": "Billed per character of the script actually sent, so a "
                "longer read costs more than a short one at the same length.",
    },
    "creatomate": {
        "label": "Renders",
        "unit": "renders",
        "price_per_unit": None,
        "price_source": "",
        "note": "One per format. A re-render after a change costs another.",
    },
}

# Roughly five characters a word, which is the figure `VO_WORD_TARGETS` was
# written against. Deliberately approximate and said to be: the real number is
# the script, and this runs before one exists.
CHARS_PER_WORD = 5.5


def _image_price():
    """OpenAI's published image rate, from the one place that holds it.

    Read rather than restated so a price change lands once — and returns None
    rather than a default if that table ever stops carrying the model, because
    a stale price is worse than no price.
    """
    try:
        from hub import quotas
        return quotas.IMAGE_PRICING.get("gpt-image-1")
    except Exception:                                    # noqa: BLE001
        return None


def estimate(lengths, formats=(), method="stock_vo", ai_video=False,
             spokesperson=False):
    """What building this will consume, per provider.

    `lengths` is what the Start page has ticked. `ai_video` and `spokesperson`
    are what the rep intends rather than what exists yet — before a script
    there is nothing else to go on, and the estimate says so.

    Never raises: this runs on a keystroke on the Start page, and an estimate
    that breaks the form it sits in is worse than no estimate.
    """
    try:
        return _estimate(lengths, formats, method, ai_video, spokesperson)
    except Exception as exc:                             # noqa: BLE001
        return {"rows": [], "measured": False, "spots": 0,
                "note": f"The estimate could not be worked out: {exc}",
                "priced": False, "total_usd": None, "caveat": CAVEAT}


CAVEAT = (
    "What the tools will consume, not what the client is charged — the rate "
    "card is what a client pays. Counted from the lengths and formats picked, "
    "before a script exists, so it moves as the spot is built.")


def _estimate(lengths, formats, method, ai_video, spokesperson):
    picked = sorted({int(x) for x in (lengths or []) if int(x) > 0},
                    key=build_sort_key)
    fmt_count = max(1, len(formats or []) or 1)
    if not picked:
        return {"rows": [], "measured": False, "spots": 0,
                "note": "Pick a length to see what it will take to build.",
                "priced": False, "total_usd": None, "caveat": CAVEAT}

    shots = chars = 0
    video_seconds = 0
    for length in picked:
        target = abcd_service.shot_targets(length)
        # The middle of the range the Blueprint scores against, so the
        # estimate and the thing it estimates read the same table.
        per_spot = max(1, round((target["low"] + target["high"]) / 2))
        shots += per_spot
        low, high = VO_WORD_TARGETS.get(length, (0, 0))
        chars += int(round(((low + high) / 2) * CHARS_PER_WORD))
        if ai_video:
            # Runway returns 5s or 10s and nothing between, so a shot is
            # costed at the shortest clip that covers it — the same rule
            # config.runway_duration() applies when one is actually ordered.
            video_seconds += per_spot * 5

    rows = []
    price = _image_price()
    # A still is the input to an AI video clip, so the two go together: the
    # image count is the shot count either way, because "Generate AI" is what
    # produces the frame every shot starts from.
    rows.append(_row("openai_image", shots * 2, price,
                     "Two options per shot, one kept."))
    if ai_video:
        rows.append(_row("runway", video_seconds, None))
    if spokesperson or method in ("ai_spokesperson", "ai_spokesperson_stock"):
        # A spokesperson does not appear in every shot — the method that puts
        # one over stock uses them to open and close.
        clips = len(picked) if method == "ai_spokesperson_stock" else shots
        rows.append(_row("heygen", clips, None))
    rows.append(_row("elevenlabs", chars, None))
    rows.append(_row("creatomate", len(picked) * fmt_count, None,
                     f"{len(picked)} spot(s) × {fmt_count} format(s)."))

    priced = [r for r in rows if r["usd"] is not None]
    total = round(sum(r["usd"] for r in priced), 2) if priced else None
    return {
        "rows": rows,
        "spots": len(picked),
        "measured": True,
        "priced": bool(priced),
        "total_usd": total,
        # Named, never silently omitted: a total that quietly covers two of
        # five rows is the confident low number this whole phase is about.
        "unpriced": [r["label"] for r in rows if r["usd"] is None],
        "note": _note(rows, total),
        "caveat": CAVEAT,
    }


def _row(key, units, price_per_unit, extra=""):
    spec = PROVIDERS[key]
    units = max(0, int(units))
    usd = round(units * price_per_unit, 2) if price_per_unit else None
    return {
        "key": key, "label": spec["label"], "unit": spec["unit"],
        "units": units,
        "usd": usd,
        "price_source": spec["price_source"] if usd is not None else "",
        # Where no rate is held, the row says so in its own words rather than
        # showing a blank a reader fills in with a guess.
        "price_note": ("" if usd is not None else
                       "No published rate on file for this provider, so the "
                       "count is real and the cost is not measured."),
        "note": " ".join(x for x in (spec["note"], extra) if x),
    }


def _note(rows, total):
    unpriced = [r["label"] for r in rows if r["usd"] is None]
    if total is None:
        return ("None of these providers has a published rate on file, so this "
                "is a count of what will be consumed rather than a cost.")
    if unpriced:
        return (f"${total:.2f} covers only what has a published rate. "
                + ", ".join(unpriced)
                + " are counted in their own units and not priced — a total "
                  "that quietly left them out would be the wrong number.")
    return f"About ${total:.2f} across every provider."


def check_spec():
    """Anything here that claims a price it cannot support.

    `library_spec.check_spec()`'s shape, and the thing worth checking on this
    file specifically: a `price_per_unit` written in as a literal would be a
    number somebody typed from memory, and it would be repeated to a client.
    Returns an empty list today.
    """
    problems = []
    for key, spec in PROVIDERS.items():
        if spec["price_per_unit"] is not None:
            problems.append(
                f"{key} carries a hard-coded price; rates belong in one place "
                "and are read from it, or they go stale silently")
        if not spec.get("unit"):
            problems.append(f"{key} does not say what it bills in")
        if not spec.get("label"):
            problems.append(f"{key} has no label")
    return problems
