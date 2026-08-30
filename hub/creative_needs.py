"""Creative for video and audio — asked before it is assumed.

## The problem this closes

The builder would happily put Connected TV or programmatic digital radio into
a media plan and price it, without anyone establishing that a video or an
audio spot exists to run. That is the most expensive silent assumption in the
whole flow: the campaign is sold, the insertion order is signed, and then
somebody discovers there is no :30 — at which point the choice is a delayed
launch or Smart 1 absorbing a production cost nobody quoted.

Display is not the same problem. A standard set of six banner sizes is $250
off the rate card and is produced routinely. A video or audio spot is a
different order of work, so those two mediums are gated:

    1. Do they have existing creative for this medium?
       yes  -> keep going, nothing else to decide.
       no   -> 2.

    2. Who pays for it?
       client -> the production fee goes on the proposal.
       comp   -> Smart 1 absorbs it. 3.

    3. Is that medium's campaign spend under $1,500?
       yes  -> confirm it. Explicitly, once, with the number shown.

That last check is the point of the module. Comping production on a $10,000
Connected TV flight is a reasonable cost of winning the business. Comping the
same production on a $600 test is Smart 1 paying to run someone's campaign,
and it is the kind of decision that is only ever obvious in hindsight — so it
is put in front of the rep while the quote is still open, with the actual
number next to it.

Nothing here blocks. A rep who confirms the comp gets the comp; the answer is
recorded on the quote and carried onto the insertion order so the trafficking
team knows what was agreed and who agreed it.
"""
from __future__ import annotations

# The two mediums that need an answer, and what production costs when Smart 1
# builds it. Video and audio production are quoted per project — they are
# "Custom Creative / Design Project" on the rate card — so the figures here are
# the standard starting points a rep can override, not card rates.
VIDEO = "video"
AUDIO = "audio"
DISPLAY = "display"
RETARGETING = "retargeting"
SOCIAL = "social"
EMAIL = "email"
DOOH = "dooh"
OTHER = "other"

# Display and retargeting joined the gate after the paragraph above turned out
# to be half right. A standard set of six banners genuinely is a $250 line and
# genuinely is produced routinely -- and none of that answers the question,
# which is whether anybody has *asked*. A display plan reached the insertion
# order with the creative box empty exactly as often as a CTV one did; it
# simply cost $250 and a week rather than a shoot, so it was discovered at
# trafficking instead of at launch and nobody called it a failure.
#
# Retargeting is separated from display rather than folded into it because it
# is a different set of files in practice: the same six sizes carrying the
# offer that brings somebody back, not the one that introduced the brand. A
# plan that has both and answers once has answered for one of them.
#
# What keeps this from becoming noise is the confirmation threshold below,
# which is per medium. Display creative pays for itself far lower down than a
# video shoot does, so a comped set of banners is questioned at $500 rather
# than at $1,500 -- a warning that fires on every plan is a warning nobody
# reads, which is the note hub/qr_codes.py makes about QR on social.
# Paid social joined last, and it was the largest hole of the three. A
# Meta-only plan returned *nothing* from `gated_media()`: six real buys --
# Awareness, Targeted, Programmatic Paid Social, Retargeting, Leads and
# Boosted Posts -- each with three to seven units published in the spec kit,
# and the Creative step never mentioned any of them. The tempting reading is
# that paid social is usually a boosted post the client already has, and that
# is exactly the assumption this module exists to stop making: the gate's job
# is to ask, and "they probably have something" is what leaves a launch date
# resting on nobody having checked.
#
# Email and digital out-of-home joined for the reason display did, one step
# further on. Both sell creative with published specs -- the kit has an Email
# Creative and an HTML package, and four billboard sizes -- and neither was
# ever asked for, because `medium_of` answered OTHER for every product under
# EMAIL MARKETING and SMART 1 SIGNAGE and an ungated medium is one the
# Creative step never mentions. A signage buy reached the insertion order with
# nobody having established that artwork exists, exactly as a CTV buy used to.
GATED = (VIDEO, AUDIO, DISPLAY, RETARGETING, EMAIL, DOOH, SOCIAL)

MEDIUM_LABEL = {
    VIDEO: "Video (CTV, streaming, YouTube, pre-roll)",
    AUDIO: "Audio (digital radio, podcasts, streaming audio)",
    DISPLAY: "Display (banners, native, IP targeted, geo-fenced)",
    RETARGETING: "Retargeting (the banners that bring them back)",
    EMAIL: "Email (the creative that goes in the send)",
    DOOH: "Signage (digital out-of-home and indoor screens)",
    SOCIAL: "Paid social",
    OTHER: "Other",
}

# Below this, a comped production is questioned rather than assumed. Per
# medium, across the whole campaign — not per month — because production is a
# one-time cost and a three-month flight amortises it three ways.
COMP_CONFIRM_UNDER = 1500

# Per medium, where the medium's own economics differ from the default above.
# Display is $250 of design on the card, so the campaign at which comping it
# stops being sensible is far lower than the one for a shoot.
# Email creative is the card's own "Email Template Creation" at $150, so it is
# questioned lower still. Signage artwork is a custom quote and is left at the
# default rather than given a number nobody published.
# Social is the card's "Social Media Ad Creation per platform" at $35, so the
# campaign at which comping it stops being sensible is lower again -- low
# enough that the confirmation is in practice never raised, which is the right
# outcome for a $35 line rather than a threshold nobody would act on.
COMP_CONFIRM_BY_MEDIUM = {DISPLAY: 500, RETARGETING: 500, EMAIL: 400, SOCIAL: 100}

# Starting points for production when Smart 1 builds it. Overridable on the
# quote; a custom shoot is a custom quote. Display and retargeting are the
# card's own "Standard Set of 6 Ad Creation" at $250 rather than a number
# invented here -- the same rule the video figures follow, one step further.
TYPICAL_PRODUCTION = {VIDEO: 750, AUDIO: 250, DISPLAY: 250, RETARGETING: 250,
                      EMAIL: 150, DOOH: 250, SOCIAL: 35}


def confirm_under(medium: str) -> int:
    """The campaign size below which comping this medium is questioned."""
    return COMP_CONFIRM_BY_MEDIUM.get(medium, COMP_CONFIRM_UNDER)

HAS = "has"            # the client already has creative for this medium
CLIENT_PAYS = "client"  # the client pays Smart 1 to produce it
COMP = "comp"           # Smart 1 absorbs the production cost

ANSWERS = (HAS, CLIENT_PAYS, COMP)


# Rate-card products whose names do not say what they are.
#
# Four programmatic *video* products are filed under the same OUTREACH →
# DISPLAY category as banner inventory, and three of their four names contain
# no word that identifies them: "Programmatic - Targeted" is $17.00 CPM video,
# while "Category" in the same category is $4.25 CPM display. Guessing from
# the category would price a video buy and never ask whether a video exists —
# precisely the failure this module is for, so they are named.
#
# `card_drift()` reports any entry here that no longer matches a product on the
# card, so a renamed product surfaces as a discrepancy rather than silently
# reverting to a keyword guess.
EXPLICIT_MEDIUM = {
    "programmatic - ron (run of network)": VIDEO,       # $14.00 CPM
    "programmatic - targeted": VIDEO,                   # $17.00 CPM
    "premium: non-skippable": VIDEO,                    # $23.00 CPM
    "premium native video": VIDEO,                      # $26.00 CPM
    # ...and one running the other way. The card files this under a heading
    # called SOCIAL ADS - VIDEO, and the heading is what the keyword pass
    # reads, so a product whose own name is "Display & Text Ads" was gated as
    # video -- asking a client for a spot to run text ads. The other products
    # under that heading are left alone: the heading is right about them, and
    # reclassifying a generic "Paid Social Media Advertising" on our own
    # reading of which platforms are video-first would be inventing.
    "linkedin - display & text ads - budget based - no impression guarantee": SOCIAL,
}

# ...except under DIGITAL RADIO, where "Programmatic - Targeted" is the
# $18.00 CPM audio buy. The category disambiguates these two, so it wins.
CATEGORY_MEDIUM = {
    "digital radio": AUDIO,
    "ott": VIDEO,
    "youtube": VIDEO,
    # Its own category on the card, and its own set of files in practice.
    "retargeting": RETARGETING,
    # ...and its opposite: "Select Tactics - Comes with Retargeting" is the
    # programmatic display buy, whose name lists retargeting as one of the
    # tactics it includes. Read off the name alone it becomes a retargeting
    # line, and the display half of the plan then never gets asked for
    # banners at all.
    "data targeted display": DISPLAY,
    "programmatic campaign": DISPLAY,
    # These three answered OTHER, which is not a medium -- it is the gate
    # never being asked. The spec kit has known what they are the whole time
    # (`creative_specs.channels_for_product` returns mobile_display, email and
    # dooh for them), so the two halves of one question were disagreeing:
    # one decides whether we ask for creative, the other what we ask for.
    "mobile only": DISPLAY,
    "email marketing": EMAIL,
    "smart 1 signage": DOOH,
}


def _text(item) -> str:
    """The words that describe the product itself.

    `label` is deliberately not among them. It is "<category heading> —
    <product>", and a heading is a statement about a *section* of the card
    rather than about the product: the card files four IP-targeting products
    under a heading called "Display & Video", so the word "video" appeared in
    the label of "IP Targeted Display - New Movers" -- whose own description
    reads "deliver display ads" -- and the video test below runs first. All
    three IP display products were gated as video, which asks a client for a
    TV spot to run a banner buy. Category and product are both here already,
    so the label contributed nothing else.
    """
    if isinstance(item, dict):
        return " ".join(str(item.get(k, "")) for k in
                        ("category", "product", "description")).lower()
    return str(item or "").lower()


def _field(item, key) -> str:
    return str((item or {}).get(key, "") if isinstance(item, dict) else "").strip().lower()


def medium_of(item) -> str:
    """Which medium a line item is, from its category and product name.

    Category alone is not enough, and neither is the product name — see
    EXPLICIT_MEDIUM. Both are consulted, category first where it is decisive.
    """
    category = _field(item, "category")
    if category in CATEGORY_MEDIUM:
        return CATEGORY_MEDIUM[category]

    product = _field(item, "product")
    if product in EXPLICIT_MEDIUM:
        return EXPLICIT_MEDIUM[product]

    text = _text(item)
    if any(w in text for w in ("radio", "podcast", "audio", "spotify",
                               "pandora", "iheart")):
        return AUDIO
    if any(w in text for w in ("video", "ott", "youtube", "trueview", "bumper",
                               "connected tv", "advanced tv", "ctv", "pre-roll",
                               "preroll", "non-skippable")):
        return VIDEO
    if any(w in text for w in ("facebook", "instagram", "meta", "tiktok", "tik tok",
                               "linkedin", "snapchat", "pinterest", "twitter",
                               "social")):
        return SOCIAL
    if "retarget" in text:
        return RETARGETING
    if any(w in text for w in ("display", "banner", "geo-fence",
                               "geofence", "outreach", "native", "location lookback",
                               "ip target", "select tactics")):
        return DISPLAY
    return OTHER


# Which medium a spec-kit channel's creative belongs to.
#
# `creative_specs.channels_for_product()` answers *what* to ask a client for;
# `medium_of()` above answers *whether* to ask at all. Two readings of one
# question, and they were disagreeing on 25 of the 90 products on the card --
# in both directions. Products under MOBILE ONLY, EMAIL MARKETING and SMART 1
# SIGNAGE were OTHER here and had perfectly good channels there, so the gate
# never mentioned them; and three IP-targeted *display* products were video
# here and display there, so the gate asked for a spot while the kit asked
# for banners.
#
# This table is what lets `spec_disagreements()` say so. It is deliberately
# not consulted by `medium_of` itself: the wizard mirrors that function in
# JavaScript, and reaching into the kit's twenty-entry regex list would make
# a third mirror of the same fact -- the cost this codebase has already paid
# twice. The tables stay separate and are held together by a check.
SPEC_CHANNEL_MEDIUM = {
    "desktop_display": DISPLAY, "mobile_display": DISPLAY,
    "tablet_display": DISPLAY, "native_display": DISPLAY,
    "standard_video": VIDEO, "youtube": VIDEO, "ctv": VIDEO,
    "native_video": VIDEO,
    "digital_radio": AUDIO,
    "email": EMAIL,
    "dooh": DOOH,
    "facebook": SOCIAL, "instagram": SOCIAL, "facebook_video": SOCIAL,
    "facebook_carousel": SOCIAL, "stories": SOCIAL, "x": SOCIAL,
    "linkedin": SOCIAL, "snapchat": SOCIAL, "tiktok": SOCIAL,
    "gpt_ads": OTHER,
}

# Products where the two readings differ and both are right, so a check that
# reported them would be a check people learn to skim. Each says why.
SPEC_AGREE_EXEMPT = {
    # The buy is the retargeting line -- its own category, its own budget --
    # while the files it runs are Instagram and Stories units. Different
    # questions, both answered correctly.
    ("retargeting", "social"),
    # And its display twin: retargeting creative *is* banners. The gate keeps
    # it apart from display because it is a different set of the same sizes --
    # the offer that brings somebody back, not the one that introduced the
    # brand -- which is a fact about who supplies the files, not about which
    # units they are judged against.
    ("retargeting", "display"),
    # A creative-production line item is not a media buy that needs creative
    # supplied for it. The kit still knows how to judge an email if one is
    # uploaded; the gate is right not to ask the client for one.
    ("other", "email"),
    # A social buy sold as video: the gate wants a video, the kit judges it
    # against that platform's units. Both true.
    ("video", "social"),
}


def spec_disagreements() -> list[dict]:
    """Products the creative gate and the spec kit read differently.

    Empty is the only acceptable answer, and it started that way -- which is
    the only way this was worth adding. A disagreement here is silent from
    both ends: each screen is internally consistent and the rep is asked for
    one thing and judged against another.
    """
    try:
        from . import creative_specs, rate_card
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for product in rate_card.products():
        channels = creative_specs.channels_for_product(
            product.get("product", ""), product.get("category", ""))
        if not channels:
            continue
        kit = {SPEC_CHANNEL_MEDIUM.get(c) for c in channels} - {None}
        gate = medium_of(product)
        if gate in kit or not kit:
            continue
        if any((gate, k) in SPEC_AGREE_EXEMPT for k in kit):
            continue
        out.append({"product": product.get("product", ""),
                    "category": product.get("category", ""),
                    "gate": gate, "kit": sorted(kit)})
    return out


def card_drift() -> list[str]:
    """EXPLICIT_MEDIUM entries that no longer match a rate-card product.

    A renamed product would otherwise fall through to the keyword guess and
    quietly stop being treated as video — the plan would price it and never
    ask for a spot. Surfaced rather than assumed; `/api/integrity` reads this.
    """
    try:
        from . import rate_card
        known = {str(p.get("product", "")).strip().lower() for p in rate_card.products()}
    except Exception:                                   # noqa: BLE001
        return []
    if not known:
        return []
    return sorted(name for name in EXPLICIT_MEDIUM if name not in known)


def _months(state) -> int:
    try:
        return max(1, int(state.get("months") or 1))
    except (TypeError, ValueError):
        return 1


def medium_spend(state, medium: str) -> float:
    """What this campaign spends on one medium, across the whole flight.

    Every line read its own basis and term, because this figure decides two
    things and was wrong for both. It multiplied *every* line by the flight,
    so a $1,500 one-time production read as $9,000 of campaign spend and a
    line bought for three months of six read as double -- an inflated number
    printed on the client's creative section, and, worse, one that quietly
    switches the comp confirmation off: `evaluate()` asks for a confirmation
    only where the spend is *below* the threshold, which is exactly the small
    campaign the question exists for.
    """
    state = state or {}
    months = _months(state)
    total = 0.0
    for item in state.get("items") or []:
        if medium_of(item) != medium:
            continue
        try:
            dollars = float(item.get("dollars") or 0)
        except (TypeError, ValueError):
            continue
        if str(item.get("basis") or "monthly") == "one_time":
            total += dollars
            continue
        try:
            term = int(item.get("termMonths") or months)
        except (TypeError, ValueError):
            term = months
        total += dollars * max(1, min(months, term))
    return round(total, 2)


def gated_media(state) -> list[str]:
    """The gated mediums actually present in this media plan."""
    state = state or {}
    present = {medium_of(i) for i in (state.get("items") or [])}
    return [m for m in GATED if m in present]


def needs_confirmation(state, medium: str) -> bool:
    """Whether comping production on this medium should be questioned."""
    return medium_spend(state, medium) < COMP_CONFIRM_UNDER


def _decision(state, medium: str) -> dict:
    return dict((state.get("creativePlan") or {}).get(medium) or {})


def evaluate(state) -> dict:
    """What the creative gate has and has not been told, per medium.

    The shape the wizard, the gap check and the IO hand-off all read:

        {"media": [ {medium, label, spend, answer, resolved, needs_confirm,
                     confirmed, fee, question, warning} ... ],
         "unresolved": ["video"],          # still need an answer
         "fees": 750.0,                    # production going on the proposal
         "comped": ["audio"],              # Smart 1 is absorbing these
         "ok": False}
    """
    state = state or {}
    rows, unresolved, comped = [], [], []
    fees = 0.0

    for medium in gated_media(state):
        decision = _decision(state, medium)
        answer = decision.get("answer") if decision.get("answer") in ANSWERS else ""
        spend = medium_spend(state, medium)
        threshold = confirm_under(medium)
        confirm_needed = spend < threshold
        # A comp confirmed on a $1,400 buy is not a comp confirmed on the $300
        # one it just became. `confirmed_at` records the spend the rep was
        # looking at; the confirmation lapses if the budget has since fallen.
        confirmed = bool(decision.get("confirmed"))
        try:
            confirmed_at = float(decision.get("confirmed_at") or 0)
        except (TypeError, ValueError):
            confirmed_at = 0.0
        if confirmed and confirmed_at and spend < confirmed_at:
            confirmed = False

        fee = 0.0
        if answer == CLIENT_PAYS:
            try:
                fee = float(decision.get("fee") or TYPICAL_PRODUCTION.get(medium, 0))
            except (TypeError, ValueError):
                fee = float(TYPICAL_PRODUCTION.get(medium, 0))
            fees += fee

        resolved = bool(answer) and not (answer == COMP and confirm_needed
                                         and not confirmed)
        if not resolved:
            unresolved.append(medium)
        if answer == COMP and resolved:
            comped.append(medium)

        warning = ""
        if answer == COMP and confirm_needed:
            warning = (f"Smart 1 is producing {MEDIUM_LABEL[medium].split(' (')[0].lower()} "
                       f"creative at no charge on a "
                       f"{'$%s' % f'{spend:,.0f}'} {MEDIUM_LABEL[medium].split(' (')[0].lower()} "
                       f"campaign — under the ${threshold:,} where a comp "
                       f"usually pays for itself.")

        rows.append({
            "medium": medium,
            "label": MEDIUM_LABEL[medium],
            "spend": spend,
            "answer": answer,
            "resolved": resolved,
            "needs_confirm": confirm_needed,
            "confirmed": confirmed,
            "confirmed_at": confirmed_at,
            "fee": fee,
            "threshold": threshold,
            "units": required_units(state, medium),
            "question": question_for(medium, answer, spend),
            "warning": warning,
        })

    return {"media": rows, "unresolved": unresolved, "fees": round(fees, 2),
            "comped": comped, "ok": not unresolved}


def question_for(medium: str, answer: str = "", spend: float = 0.0) -> str:
    """The question still outstanding for this medium, in plain words."""
    name = MEDIUM_LABEL.get(medium, medium).split(" (")[0].lower()
    if not answer:
        return f"Does the client already have {name} creative we can run?"
    if answer == COMP and spend < confirm_under(medium):
        return (f"This is a ${spend:,.0f} {name} campaign. Are you sure you want to "
                f"comp the production?")
    return ""


def summary_line(state) -> str:
    """One line for the proposal and the IO: who is providing what."""
    result = evaluate(state)
    if not result["media"]:
        return ""
    parts = []
    for row in result["media"]:
        name = row["label"].split(" (")[0]
        if row["answer"] == HAS:
            parts.append(f"{name}: client supplies finished creative")
        elif row["answer"] == CLIENT_PAYS:
            parts.append(f"{name}: Smart 1 produces (${row['fee']:,.0f})")
        elif row["answer"] == COMP:
            parts.append(f"{name}: Smart 1 produces at no charge")
        else:
            # Never quietly assume one. An unanswered gate is a stated gap.
            parts.append(f"{name}: creative source not yet confirmed")
    return " · ".join(parts)


def gaps(state) -> list[dict]:
    """Outstanding creative answers, in the gap-check shape the builder uses."""
    out = []
    for row in evaluate(state)["media"]:
        if row["resolved"]:
            continue
        name = row["label"].split(" (")[0].lower()
        if not row["answer"]:
            # "A spot" is a video and audio word. Banners are not spots, and
            # the gate covers them now.
            file_word = ("banners" if row["medium"] in (DISPLAY, RETARGETING)
                         else "spot")
            label = (f"Whether the client has {name} creative — the plan runs "
                     f"{name} and nothing has said the {file_word} exist"
                     f"{'' if file_word == 'banners' else 's'}")
        else:
            label = (f"Confirmation that Smart 1 comps the {name} production on a "
                     f"${row['spend']:,.0f} campaign")
        out.append({"key": f"creative_{row['medium']}", "label": label})
    return out


# ---------------------------------------------------------------------------
# What "creative" actually means for this line — from the IO's own spec kit
#
# The gate asked whether a spot exists and stopped there, which is the whole
# question for video and audio and only half of it for display: "do you have
# banners" has no answer until somebody says which sizes. A client who hands
# over a 300x250 and nothing else has answered yes and blocked the buy, and
# the discovery happens at trafficking.
#
# hub/creative_specs.py already holds the answer -- it is the S1M CREATIVE
# SPEC KIT the IO's upload manager checks every delivered file against, and it
# already maps display, retargeting, geo-fencing, IP targeting and location
# lookback onto the desktop, mobile and tablet units. So this reads that
# rather than restating it: two lists of banner sizes is how the proposal
# comes to promise a set the IO then refuses.
#
# A product the kit maps no unit for is **not measured**, never an empty list
# presented as "nothing needed" -- the rule creative_specs itself works to for
# a format it has no unit for.
# ---------------------------------------------------------------------------
def required_units(state, medium: str) -> dict:
    """The spec-kit units the lines of one medium need, with their sizes.

    {"units": [{"id","label","sizes"}], "products": [...], "measured": bool,
     "note": "..."} -- `measured` false where the kit maps nothing, so a
    screen can say so instead of drawing an empty requirement.
    """
    state = state or {}
    products = [item for item in (state.get("items") or [])
                if medium_of(item) == medium]
    if not products:
        return {"units": [], "products": [], "measured": False,
                "note": "No lines of this medium on the plan."}

    try:
        from . import creative_specs
    except Exception as exc:                            # noqa: BLE001
        return {"units": [], "products": [str(p.get("product") or "") for p in products],
                "measured": False,
                "note": f"The creative spec kit could not be read ({exc}). "
                        f"Sizes not measured."}

    seen, units = set(), []
    unmapped = []
    for item in products:
        rows = creative_specs.units_for_product(
            str(item.get("product") or ""), str(item.get("category") or ""))
        if not rows:
            unmapped.append(str(item.get("product") or item.get("category") or ""))
            continue
        for unit in rows:
            if unit["id"] in seen:
                continue
            seen.add(unit["id"])
            try:
                pairs = creative_specs._sizes_of(unit)
            except Exception:                           # noqa: BLE001
                pairs = [unit.get("size")] if unit.get("size") else []
            duration = unit.get("duration") or ()
            units.append({
                "id": unit["id"],
                "label": unit.get("name") or unit["id"],
                "kind": unit.get("kind") or "image",
                "channel": creative_specs.CHANNEL_LABELS.get(
                    unit.get("channel", ""), unit.get("channel", "")),
                "sizes": [f"{w}x{h}" for w, h in pairs if w and h],
                "formats": [str(f).upper() for f in (unit.get("formats") or [])],
                "seconds": list(duration) if len(duration) == 2 else [],
            })

    note = ""
    if unmapped:
        note = ("The spec kit maps no unit for " + ", ".join(sorted(set(unmapped)))
                + " — sizes for those are not measured here.")
    return {"units": units,
            "products": [str(p.get("product") or "") for p in products],
            "measured": bool(units), "note": note,
            "source": getattr(creative_specs, "SPEC_KIT_URL", "")}


def _describe_unit(unit) -> str:
    """One spec-kit unit in the terms it is actually specified in.

    An audio spot has no pixel size -- it has a length and a bitrate -- and
    listing the sizes alone made the audio row read "300x250", which is the
    *optional companion banner* presented as the whole requirement. A client
    reading that sends a banner and no spot, which is the confident wrong
    answer this codebase keeps having to undo. So each unit is described by
    what it is: an image by its size, a video or audio by its length and
    format.
    """
    kind = unit.get("kind") or "image"
    seconds = unit.get("seconds") or []
    length = (f"{seconds[0]}–{seconds[1]}s" if len(seconds) == 2
              and seconds[0] != seconds[1] else
              (f"{seconds[0]}s" if seconds else ""))
    fmt = "/".join(unit.get("formats") or [])
    if kind in ("video", "audio"):
        bits = [b for b in (", ".join(unit.get("sizes") or []), fmt, length) if b]
        return f"{unit['label']} ({', '.join(bits)})" if bits else unit["label"]
    if unit.get("sizes"):
        return ", ".join(unit["sizes"])
    return unit["label"]


def units_line(state, medium: str) -> str:
    """What one medium needs, in one line for a rep or a client document."""
    result = required_units(state, medium)
    if not result["measured"]:
        return result.get("note") or "Sizes not measured."

    # Banner units are listed as a run of sizes, because there are nine of
    # them and nine labels is a wall. Anything else is described.
    images = [u for u in result["units"] if (u.get("kind") or "image") == "image"]
    others = [u for u in result["units"] if (u.get("kind") or "image") != "image"]
    # Desktop, mobile and tablet each carry their own "HTML5 package" unit,
    # so describing all three printed the same words three times.
    described = list(dict.fromkeys(_describe_unit(u) for u in others))
    sizes = list(dict.fromkeys(sz for u in images for sz in u["sizes"]))

    # Lead with what the ask actually is. A display buy is a set of banner
    # sizes, and the HTML5 package is another way to deliver the same set --
    # named first it reads as an extra thing to produce. A radio buy is a
    # spot, and the 300x250 beside it is the optional companion; named first
    # it reads as the whole requirement, which is how somebody sends a banner
    # and no audio.
    if not sizes:
        parts = described
    elif len(sizes) == 1 and described:
        parts = described + [f"plus a companion banner: {sizes[0]}"]
    elif described:
        parts = [", ".join(sizes)] + [f"or {d}" for d in described]
    else:
        parts = [", ".join(sizes)]
    if not parts:
        return result.get("note") or "Sizes not measured."
    return " · ".join(parts)
