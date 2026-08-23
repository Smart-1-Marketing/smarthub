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
SOCIAL = "social"
OTHER = "other"

GATED = (VIDEO, AUDIO)

MEDIUM_LABEL = {
    VIDEO: "Video (CTV, streaming, YouTube, pre-roll)",
    AUDIO: "Audio (digital radio, podcasts, streaming audio)",
    DISPLAY: "Display",
    SOCIAL: "Paid social",
    OTHER: "Other",
}

# Below this, a comped production is questioned rather than assumed. Per
# medium, across the whole campaign — not per month — because production is a
# one-time cost and a three-month flight amortises it three ways.
COMP_CONFIRM_UNDER = 1500

# Starting points for production when Smart 1 builds it. Overridable on the
# quote; a custom shoot is a custom quote.
TYPICAL_PRODUCTION = {VIDEO: 750, AUDIO: 250}

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
}

# ...except under DIGITAL RADIO, where "Programmatic - Targeted" is the
# $18.00 CPM audio buy. The category disambiguates these two, so it wins.
CATEGORY_MEDIUM = {
    "digital radio": AUDIO,
    "ott": VIDEO,
    "youtube": VIDEO,
}


def _text(item) -> str:
    if isinstance(item, dict):
        return " ".join(str(item.get(k, "")) for k in
                        ("category", "product", "label", "description")).lower()
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
    if any(w in text for w in ("display", "banner", "retarget", "geo-fence",
                               "geofence", "outreach", "native", "location lookback",
                               "ip target")):
        return DISPLAY
    return OTHER


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
    """What this campaign spends on one medium, across the whole flight."""
    state = state or {}
    months = _months(state)
    total = 0.0
    for item in state.get("items") or []:
        if medium_of(item) != medium:
            continue
        try:
            total += float(item.get("dollars") or 0)
        except (TypeError, ValueError):
            continue
    return round(total * months, 2)


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
        confirm_needed = spend < COMP_CONFIRM_UNDER
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
                       f"campaign — under the ${COMP_CONFIRM_UNDER:,} where a comp "
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
    if answer == COMP and spend < COMP_CONFIRM_UNDER:
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
            label = (f"Whether the client has {name} creative — the plan plays "
                     f"{name} and nothing has said a spot exists")
        else:
            label = (f"Confirmation that Smart 1 comps the {name} production on a "
                     f"${row['spend']:,.0f} campaign")
        out.append({"key": f"creative_{row['medium']}", "label": label})
    return out
