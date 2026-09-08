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
* **Storms are not a promotion.** `storm-watch` copy is checked against a
  small blocklist — no jokes, no urgency language that could read as
  encouraging someone to drive in a warned area.
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


def _clean(text: str, limit: int = 220) -> str:
    return " ".join(str(text or "").split())[:limit]


def _house_draft(trigger_id: str, client_name: str, angle: str) -> dict:
    """A deterministic, no-model draft. Always available, always safe."""
    trig = TRIGGERS[trigger_id]
    name = client_name or "your table"
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
    headline, primary = by_angle.get(angle, by_angle["Direct"])
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

    if TRIGGERS[trigger_id].id == "storm-watch":
        lowered = text.lower()
        if any(phrase in lowered for phrase in _STORM_BLOCKLIST):
            replacement = _house_draft(trigger_id, "", draft.get("angle", "Direct"))
            replacement["note"] = ("The generated draft read as an invitation "
                                   "to go out in a warned storm, so it was "
                                   "replaced.")
            return replacement
    return draft


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

    drafts: list[dict] = []
    source = "house"
    try:
        from hub import ai as hub_ai
        prompt = (
            f"Write three short ad drafts for a restaurant, one per angle: "
            f"{', '.join(ANGLES)}. The weather condition triggering this ad is "
            f"\"{trig.name}\" ({trig.condition_label}). The restaurant is "
            f"\"{client_name or 'the restaurant'}\". "
            f"{('Menu or voice notes: ' + menu_note) if menu_note else ''} "
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
