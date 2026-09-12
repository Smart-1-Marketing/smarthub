"""Ad copy drafts for a chosen trigger.

Three drafts, one per angle, generated **once** when a trigger is picked and
cached on the campaign record — never regenerated on page load, because the
client must not see different words on a refresh and the approval has to be
of a fixed artifact.

Three guardrails run over whatever comes back, AI-written or house-authored
alike, the same shape `hub/proposal_spec.client_safe()` and
`hub/blog_spec.scan_forbidden()` already use: a prompt is a request, and
"the model was told not to" is not evidence that it did not.

* **No unbacked promise.** Copy that promises the reader a follow-up message
  — a text, an email, a confirmation — is refused unless the source tag it
  captures a lead under has a workflow registered in `hub/lead_tags.py`.
  `weather_trigger_setup` does not today, so any such promise is dropped.
* **No fabricated specifics.** A price, a discount or an hour of operation
  is not invented; a draft that reads as carrying one is flagged rather than
  silently rewritten, because inventing a *different* wrong answer is worse
  than leaving the placeholder visible for a rep to confirm before launch.
* **Storms are not a promotion.** Any alert-driven trigger's copy — not
  only `storm-watch`, but `hvac`'s `storm-power-risk` and `retail`'s
  `storm-prep` too — is checked against a small blocklist: no jokes, no
  urgency language that could read as encouraging someone to drive in a
  warned area.

**The angle is written to the vertical, not to a swapped-in name.** A
restaurant ad invites ("come sit outside"), an HVAC ad warns or reminds
("book this before it fails"), a retail ad is a purchase trigger ("stock
up before it's gone"), an auto-service ad names the specific system a
condition puts under strain ("get your battery checked before it strands
you"), a landscaping ad is a service reminder like HVAC's but keyed to the
season's task ("book your spring cleanup"), and a pool/spa ad is the same
shape again but keyed to chemical balance and opening/closing rather than
an appliance. One generic house template with the business name dropped in
would answer a hard-freeze ad with "The weather's right for Acme Heating &
Air" — grammatical, and wrong for what the ad is for — so
`_house_draft_restaurant()`, `_house_draft_hvac()`, `_house_draft_retail()`,
`_house_draft_auto()`, `_house_draft_landscaping()` and
`_house_draft_pool_spa()` are six separate templates per angle, and
`_house_draft()` dispatches on `Trigger.vertical` rather than guessing from
the trigger's tags. The model prompt carries the same split, through
`_PROMPT_CONTEXT`.
"""
from __future__ import annotations

import re

from hub import lead_tags
from hub.weather_triggers import TRIGGERS

LEAD_SOURCE = "weather_trigger_setup"

ANGLES = ("Direct", "Comfort", "Invitation")

_PROMISE_RE = re.compile(
    r"we('| wi)ll (email|text|message|send you)|confirmation (email|text)|"
    r"reply to confirm|you('| wi)ll (get|receive) a (text|email|message)",
    re.IGNORECASE)

_OFFER_RE = re.compile(r"\$\d|\d+%\s*off|\bfree\b|\bbogo\b", re.IGNORECASE)

_STORM_BLOCKLIST = (
    "brave the storm", "storm party", "drive through the storm",
    "worth the risk", "don't let the storm stop you", "storm's coming, so are we",
    "race the storm", "storm special",
)


# What the reader of a draft is being asked to do differs by vertical: a
# restaurant ad is an invitation ("come sit outside"), an HVAC ad is a
# warning or a reminder ("book this before it fails"). Each house-draft
# branch below writes to its own vertical's psychology rather than one
# generic template with the business name swapped in — a swapped-name
# template is what produced "The weather's right for Acme Heating & Air"
# on a hard-freeze ad before this branch existed.
_URGENT_TAGS = ("emergency", "safety", "backup")


def _clean(text: str, limit: int = 220) -> str:
    return " ".join(str(text or "").split())[:limit]


def _house_draft_restaurant(trig, name: str, angle: str) -> tuple[str, str]:
    by_angle = {
        "Direct": (f"{trig.name} at {name}",
                  f"{trig.condition_label} — {name} is open and ready."),
        "Comfort": (f"Come warm up at {name}" if "cold" in trig.tags or
                    "comfort" in trig.tags or trig.id in ("cold-snap", "wind-chill", "snow-day")
                    else f"The weather's right for {name}",
                    f"{trig.reason}"),
        "Invitation": (f"{name} is calling your name",
                       f"{trig.reason} Stop by today."),
    }
    return by_angle.get(angle, by_angle["Direct"])


def _house_draft_hvac(trig, name: str, angle: str) -> tuple[str, str]:
    urgent = any(t in _URGENT_TAGS for t in trig.tags)
    by_angle = {
        "Direct": (f"{trig.name} — {name}",
                  f"{trig.condition_label}. {name} has appointments today."),
        "Comfort": ((f"Don't wait for a breakdown, {name}" if urgent
                    else f"Get ahead of it with {name}"),
                    f"{trig.reason}"),
        "Invitation": (f"{name} is ready when you are",
                       f"{trig.reason} Schedule your visit today."),
    }
    return by_angle.get(angle, by_angle["Direct"])


def _house_draft_retail(trig, name: str, angle: str) -> tuple[str, str]:
    # Neither an invitation nor a service reminder -- a purchase trigger,
    # closer to a stock-up call. "stock-up" and "emergency" tags get the
    # urgent framing; everything else (a mood lift, a seasonal browse) gets
    # the softer one.
    stock_up = "stock-up" in trig.tags or "emergency" in trig.tags
    by_angle = {
        "Direct": (f"{trig.name} at {name}",
                  f"{trig.condition_label} — {name} has what you need today."),
        "Comfort": ((f"Stock up before it's gone, {name}" if stock_up
                    else f"New for your home at {name}"),
                    f"{trig.reason}"),
        "Invitation": (f"{name} is ready for this weather",
                       f"{trig.reason} Stop by or shop online today."),
    }
    return by_angle.get(angle, by_angle["Direct"])


def _house_draft_auto(trig, name: str, angle: str) -> tuple[str, str]:
    # Between HVAC's warning and retail's stock-up call: a car does not
    # fail on a comfortable day, so the copy names the specific system a
    # condition puts under strain rather than inviting anyone anywhere.
    urgent = any(t in _URGENT_TAGS for t in trig.tags)
    by_angle = {
        "Direct": (f"{trig.name} — {name}",
                  f"{trig.condition_label}. {name} has appointments today."),
        "Comfort": ((f"Don't get stranded, {name}" if urgent
                    else f"Get ahead of it with {name}"),
                    f"{trig.reason}"),
        "Invitation": (f"{name} is ready when you are",
                       f"{trig.reason} Schedule your visit today."),
    }
    return by_angle.get(angle, by_angle["Direct"])


def _house_draft_landscaping(trig, name: str, angle: str) -> tuple[str, str]:
    # A service reminder like HVAC's, but keyed to the season's task rather
    # than an appliance under strain: book the mow, the cleanup, the
    # winterizing visit before the weather makes it urgent.
    urgent = any(t in _URGENT_TAGS for t in trig.tags) or trig.cadence == "alert_driven"
    by_angle = {
        "Direct": (f"{trig.name} — {name}",
                  f"{trig.condition_label}. {name} has appointments today."),
        "Comfort": ((f"Get it checked before it's a bigger job, {name}" if urgent
                    else f"Book it before the season gets away, {name}"),
                    f"{trig.reason}"),
        "Invitation": (f"{name} is ready when you are",
                       f"{trig.reason} Schedule your visit today."),
    }
    return by_angle.get(angle, by_angle["Direct"])


def _house_draft_pool_spa(trig, name: str, angle: str) -> tuple[str, str]:
    # The same service-reminder shape again, keyed to chemical balance and
    # opening/closing rather than an appliance under strain.
    urgent = any(t in _URGENT_TAGS for t in trig.tags) or trig.cadence == "alert_driven"
    by_angle = {
        "Direct": (f"{trig.name} — {name}",
                  f"{trig.condition_label}. {name} has appointments today."),
        "Comfort": ((f"Don't let it get out of balance, {name}" if urgent
                    else f"Get ahead of it with {name}"),
                    f"{trig.reason}"),
        "Invitation": (f"{name} is ready when you are",
                       f"{trig.reason} Schedule your visit today."),
    }
    return by_angle.get(angle, by_angle["Direct"])


_HOUSE_DRAFT_BY_VERTICAL = {
    "restaurant": _house_draft_restaurant,
    "hvac": _house_draft_hvac,
    "retail": _house_draft_retail,
    "auto": _house_draft_auto,
    "landscaping": _house_draft_landscaping,
    "pool_spa": _house_draft_pool_spa,
}

_FALLBACK_NAME = {"restaurant": "your table", "hvac": "your business",
                  "retail": "your store", "auto": "your shop",
                  "landscaping": "your business", "pool_spa": "your business"}


def _house_draft(trigger_id: str, client_name: str, angle: str) -> dict:
    """A deterministic, no-model draft. Always available, always safe."""
    trig = TRIGGERS[trigger_id]
    fallback_name = _FALLBACK_NAME.get(trig.vertical, "your business")
    name = client_name or fallback_name
    write = _HOUSE_DRAFT_BY_VERTICAL.get(trig.vertical, _house_draft_restaurant)
    headline, primary = write(trig, name, angle)
    return {"angle": angle, "headline": _clean(headline, 40),
            "primary_text": _clean(primary, 125), "source": "house"}


def _guardrail(draft: dict, trigger_id: str) -> dict:
    """Runs both hard checks over one draft. Never raises; always returns
    a draft, with flags set rather than the text silently rewritten."""
    text = f"{draft.get('headline', '')} {draft.get('primary_text', '')}"

    if _PROMISE_RE.search(text) and not lead_tags.backed(LEAD_SOURCE):
        # Refused rather than rewritten in place: a model asked to remove a
        # promise usually just rewords it, and this needs to actually be
        # gone. The house draft carries no such language by construction.
        replacement = _house_draft(trigger_id, "", draft.get("angle", "Direct"))
        replacement["angle"] = draft.get("angle", "Direct")
        replacement["source"] = "house"
        replacement["note"] = ("The generated draft promised a follow-up "
                               "message with no workflow behind it, so it "
                               "was replaced.")
        return replacement

    draft["needs_review"] = bool(_OFFER_RE.search(text))
    if draft["needs_review"]:
        draft.setdefault("note", "This draft carries a specific offer or "
                                 "price — confirm it before launch.")

    # Checked by cadence rather than by this one id, because storm-watch is
    # not the only alert-driven trigger any more: hvac's storm-power-risk
    # carries the identical reasoning ("never run as an invitation to be
    # outside in it") and needs the identical blocklist. Keying this on
    # `trigger_id == "storm-watch"` would have left the second one unchecked
    # the day it was added -- the same class of miss `check_work_kinds()`
    # exists to catch when a second module logs under a wrapper nobody
    # taught the walk to resolve.
    if TRIGGERS[trigger_id].cadence == "alert_driven":
        lowered = text.lower()
        if any(phrase in lowered for phrase in _STORM_BLOCKLIST):
            replacement = _house_draft(trigger_id, "", draft.get("angle", "Direct"))
            replacement["note"] = ("The generated draft read as an invitation "
                                   "to go out during a severe alert, so it "
                                   "was replaced.")
            return replacement
    return draft


# What to call the business in the prompt, and what a per-trigger "notes"
# field is asking about -- both read differently by vertical, and a
# restaurant-flavoured prompt sent for an HVAC trigger is how a model comes
# back describing "tonight's specials" on a furnace-repair ad.
_PROMPT_CONTEXT = {
    "restaurant": {"noun": "restaurant", "notes_label": "Menu or voice notes"},
    "hvac": {"noun": "HVAC / home comfort company", "notes_label": "Service notes"},
    "retail": {"noun": "retail / home goods store", "notes_label": "Inventory or promo notes"},
    "auto": {"noun": "auto repair / service shop", "notes_label": "Service notes"},
    "landscaping": {"noun": "landscaping / lawn care company", "notes_label": "Service notes"},
    "pool_spa": {"noun": "pool & spa service company", "notes_label": "Service notes"},
}


def generate_drafts(trigger_id: str, client_name: str, menu_note: str = "") -> dict:
    """Three angled drafts for one trigger. Never raises.

    Tries the model once; on any failure, or where the model is not
    configured, falls back to a house-authored draft per angle. Every draft
    — model or house — is run through the same guardrails, because "the
    model was told not to" is not evidence that it did not.
    """
    if trigger_id not in TRIGGERS:
        return {"drafts": [], "source": "none",
                "error": f"Unknown trigger {trigger_id!r}."}
    trig = TRIGGERS[trigger_id]
    ctx = _PROMPT_CONTEXT.get(trig.vertical, _PROMPT_CONTEXT["restaurant"])

    drafts: list[dict] = []
    source = "house"
    try:
        from hub import ai as hub_ai
        prompt = (
            f"Write three short ad drafts for a {ctx['noun']}, one per angle: "
            f"{', '.join(ANGLES)}. The weather condition triggering this ad is "
            f"\"{trig.name}\" ({trig.condition_label}). The business is "
            f"\"{client_name or ('the ' + ctx['noun'])}\". "
            f"{(ctx['notes_label'] + ': ' + menu_note) if menu_note else ''} "
            "Each draft needs a headline (<=40 characters) and primary text "
            "(<=125 characters). Never invent a price, a discount, an hour of "
            "operation, or a specific claim about the premises that was not "
            "given to you. Never promise the reader a follow-up text, email "
            "or message. Reply as JSON: "
            '{"drafts": [{"angle": "...", "headline": "...", '
            '"primary_text": "..."}]}')
        raw = hub_ai.chat(
            [{"role": "user", "content": prompt}],
            module="weather_setup", purpose="ad_drafts", json_mode=True,
            max_tokens=600)
        import json
        parsed = json.loads(raw)
        for angle in ANGLES:
            row = next((d for d in parsed.get("drafts", [])
                       if str(d.get("angle", "")).strip() == angle), None)
            if row and row.get("headline") and row.get("primary_text"):
                drafts.append({"angle": angle,
                               "headline": _clean(row["headline"], 40),
                               "primary_text": _clean(row["primary_text"], 125),
                               "source": "ai"})
        if len(drafts) == len(ANGLES):
            source = "ai"
        else:
            drafts = []
    except Exception:                                      # noqa: BLE001
        drafts = []

    if not drafts:
        drafts = [_house_draft(trigger_id, client_name, a) for a in ANGLES]
        source = "house"

    drafts = [_guardrail(d, trigger_id) for d in drafts]
    return {"drafts": drafts, "source": source}
