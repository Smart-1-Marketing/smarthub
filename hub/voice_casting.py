"""Casting a voice by what it should sound like, rather than by its id.

Two tools in this Hub hand a script to ElevenLabs. The Radio Promo builder
asks a person what the read should sound like -- voice, age, accent, energy,
delivery -- ranks the account's voices against that answer and offers three
with a preview on each. The Commercial Builder asked nothing and rendered a
flat `<select>` of every voice in the account, in whatever order ElevenLabs
returned them, with no preview: the same provider, the same account, the same
question, answered two different ways depending on which tool you opened.

So the characteristics and the scoring live here and both read them. That is
the rule the rest of this Hub already works to (`hub/target_areas.py`,
`hub/proposal_spec.py`, `hub/social_plan.py`): the next fix to how a voice is
matched lands once.

What is deliberately NOT here is the render. `modules/radio_promo/voices.py`
measures its output through ElevenLabs' `with-timestamps` endpoint because a
radio slot is sold by the second and a read that runs 31 seconds is unusable;
the Commercial Builder's service estimates instead. Those are two different
requirements, not one duplicated one, and folding them together would make
the timed one pay for the estimate or the estimated one claim a measurement it
never took.

## The scoring is a ranking, never a filter

`match()` returns every voice it was given, ordered, with the reasons it
scored. It does not drop the ones that matched nothing, and that is on
purpose: an account whose voices carry no labels at all -- which is every
account with cloned voices on it -- would otherwise come back empty from a
question that was answered perfectly well, and "ElevenLabs has no voices like
that" and "these voices carry no labels to match on" are different answers.
`match_quality()` says which of the two happened so a screen can print it.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# The question a person is asked. Each entry is one row of controls; `options`
# are what that row offers. "any" is offered explicitly rather than left as
# the empty default, because a picker with no neutral option makes somebody
# pick something they do not mean.
# ---------------------------------------------------------------------------
CHARACTERISTICS: list[dict[str, Any]] = [
    {"id": "gender", "label": "Voice", "help": "Perceived voice type.", "options": [
        {"id": "female", "label": "Female"}, {"id": "male", "label": "Male"},
        {"id": "neutral", "label": "Neutral"}, {"id": "any", "label": "No preference"}]},
    {"id": "age", "label": "Age", "help": "How old the read should sound.", "options": [
        {"id": "young", "label": "Young adult"}, {"id": "middle_aged", "label": "Middle aged"},
        {"id": "old", "label": "Mature"}, {"id": "any", "label": "No preference"}]},
    {"id": "accent", "label": "Accent", "help": "", "options": [
        {"id": "american", "label": "American"}, {"id": "british", "label": "British"},
        {"id": "australian", "label": "Australian"},
        {"id": "transatlantic", "label": "Transatlantic"},
        {"id": "any", "label": "No preference"}]},
    {"id": "energy", "label": "Energy", "help": "", "options": [
        {"id": "laid_back", "label": "Laid back"},
        {"id": "conversational", "label": "Conversational"},
        {"id": "energetic", "label": "Energetic"},
        {"id": "explosive", "label": "Explosive"}]},
    {"id": "delivery", "label": "Delivery", "help": "", "options": [
        {"id": "announcer", "label": "Announcer"}, {"id": "narrator", "label": "Narrator"},
        {"id": "best_friend", "label": "Best friend"},
        {"id": "spokesperson", "label": "Spokesperson"},
        {"id": "character", "label": "Character"}]},
]

CHARACTERISTIC_IDS = [c["id"] for c in CHARACTERISTICS]

# ElevenLabs labels its voices with one accent word and we ask with another,
# so the two are reconciled here rather than at the call site.
ACCENT_ALIASES = {
    "american": ["american", "us", "usa", "transatlantic"],
    "british": ["british", "english", "uk", "received"],
    "australian": ["australian", "aussie"],
    "transatlantic": ["transatlantic", "american", "british"],
}

# Energy and delivery are not label fields at all -- they are words that turn
# up in a voice's description and use-case text, so they are matched against
# the whole bag rather than against one key.
ENERGY_WORDS = {
    "laid_back": ["calm", "relaxed", "soothing", "soft", "chill", "gentle", "meditative"],
    "conversational": ["conversational", "casual", "natural", "friendly", "warm"],
    "energetic": ["energetic", "upbeat", "excited", "confident", "expressive"],
    "explosive": ["intense", "powerful", "shouty", "dramatic", "strong", "energetic"],
}
DELIVERY_WORDS = {
    "announcer": ["announcer", "commercial", "advertisement", "broadcast", "promo"],
    "narrator": ["narration", "narrator", "audiobook", "documentary"],
    "best_friend": ["conversational", "casual", "friendly", "social media"],
    "spokesperson": ["commercial", "advertisement", "professional", "corporate", "news"],
    "character": ["characters", "animation", "video games", "character"],
}

# What `style` to send ElevenLabs for each energy. It is here rather than in
# either module because a read cast as "explosive" and then rendered at the
# default style is cast for nothing.
STYLE_BY_ENERGY = {"laid_back": 0.15, "conversational": 0.3,
                   "energetic": 0.55, "explosive": 0.75}

DEFAULT_WANT = {"gender": "any", "age": "any", "accent": "any",
                "energy": "conversational", "delivery": "spokesperson"}


def _norm(value) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def style_for(energy: str) -> float:
    return STYLE_BY_ENERGY.get(energy, STYLE_BY_ENERGY["conversational"])


def normalise_want(want: dict | None) -> dict:
    """Only the characteristics this module knows, with the rest defaulted.

    A want carrying a key nothing scores is silently ignored today; passing it
    through would make `match_quality()` report a preference that had no
    effect on the ranking, which is the confident wrong answer this codebase
    keeps having to undo.
    """
    want = want or {}
    out = dict(DEFAULT_WANT)
    for key in CHARACTERISTIC_IDS:
        value = want.get(key)
        if value:
            out[key] = str(value)
    terms = want.get("search_terms") or []
    if isinstance(terms, str):
        terms = [terms]
    out["search_terms"] = [str(t) for t in terms if str(t).strip()][:8]
    return out


def _bag(voice: dict) -> str:
    labels = voice.get("labels") or {}
    return _norm(" ".join(str(x) for x in [
        labels.get("description"), labels.get("use_case"), labels.get("usecase"),
        labels.get("descriptive"), voice.get("description"), voice.get("name")]))


def score(voice: dict, want: dict) -> tuple[int, list[str]]:
    """How well one voice answers the want, and which parts of it did so.

    The reasons are the point. A ranked list with no reasons is the tool
    asserting a match, and the person picking has no way to tell a voice that
    matched on four characteristics from one that came top of a list where
    nothing matched at all.
    """
    labels = voice.get("labels") or {}
    bag = _bag(voice)
    total, reasons = 0, []

    want_gender = want.get("gender")
    if want_gender and want_gender != "any":
        if _norm(labels.get("gender")) == _norm(want_gender):
            total += 5
            reasons.append(want_gender)
        elif labels.get("gender"):
            # A voice labelled the other way is actively wrong, not merely
            # unmatched -- otherwise an unlabelled voice ranks below it.
            total -= 4

    want_age = want.get("age")
    if want_age and want_age != "any" and _norm(labels.get("age")) == _norm(want_age):
        total += 3
        reasons.append(str(labels.get("age")).replace("_", " "))

    want_accent = want.get("accent")
    if want_accent and want_accent != "any":
        aliases = ACCENT_ALIASES.get(want_accent, [want_accent])
        if any(_norm(a) in _norm(labels.get("accent")) for a in aliases):
            total += 3
            reasons.append(labels.get("accent"))

    for key, table in (("energy", ENERGY_WORDS), ("delivery", DELIVERY_WORDS)):
        picked = want.get(key)
        if picked:
            hits = [w for w in table.get(picked, []) if _norm(w) in bag]
            total += min(len(hits), 2) * 2
            if hits:
                reasons.append(str(picked).replace("_", " "))

    for term in want.get("search_terms") or []:
        if _norm(term) and _norm(term) in bag:
            total += 1

    if "advertisement" in bag or "commercial" in bag:
        total += 1

    seen, unique = set(), []
    for reason in reasons:
        if reason and reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return total, unique


def shape(voice: dict, points: int = 0, reasons: list | None = None,
          custom: bool = False) -> dict:
    """One voice in the shape both screens render."""
    labels = voice.get("labels") or {}
    return {"voice_id": voice.get("voice_id"), "name": voice.get("name"),
            "preview_url": voice.get("preview_url"),
            "accent": labels.get("accent", ""), "age": labels.get("age", ""),
            "gender": labels.get("gender", ""),
            "descriptor": labels.get("description") or labels.get("descriptive") or "",
            "use_case": labels.get("use_case") or labels.get("usecase") or "",
            "match_reasons": reasons or (["added by ID"] if custom else []),
            "score": points, "custom": custom}


def match(voices: list[dict], want: dict | None, count: int = 3) -> list[dict]:
    """The best `count` voices for the want, best first.

    Takes the voice list rather than fetching it: the two callers cache and
    authenticate against ElevenLabs differently, and a shared function that
    reached the network would have to pick one of their two answers about what
    to do when it refuses.
    """
    want = normalise_want(want)
    ranked = []
    for voice in voices or []:
        points, reasons = score(voice, want)
        ranked.append((points, reasons, voice))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return [shape(voice, points, reasons)
            for points, reasons, voice in ranked[:max(int(count or 1), 1)]]


def match_quality(matched: list[dict], total_voices: int) -> str:
    """Why a ranking looks the way it does, in words a screen can print.

    Three situations produce a list nobody asked for and only the middle one
    is something to act on: no voices at all in the account, voices that carry
    no labels so nothing could be matched, and a genuine ranking.
    """
    if not total_voices:
        return ("No voices came back from ElevenLabs. Check the key, and that "
                "the account has voices in its library.")
    if not matched:
        return "Nothing was ranked."
    if all(not v.get("match_reasons") for v in matched):
        return ("None of these voices carry the labels we match on, so this is "
                "the account's own order rather than a ranking. Preview them.")
    top = matched[0]
    if not top.get("match_reasons"):
        return ("The closest voices matched nothing you asked for — preview them "
                "before casting.")
    return "Ranked on " + ", ".join(top["match_reasons"]) + "."


# ---------------------------------------------------------------------------
# The question, with the scoring shown.
#
# A picker built from CHARACTERISTICS alone gives somebody five dropdowns and
# no idea what any answer will do. What the ranking actually does is match
# words against the description and use-case text ElevenLabs publishes on each
# voice -- so the honest way to make that pickable is to show the words.
# "Announcer" is not a mood here; it is a search for "announcer, commercial,
# broadcast, promo" in the voice's own labels, and a screen that says so lets
# somebody pick a different one when none of those words fit their client.
#
# Energy carries a number as well, because it is the one characteristic that
# does more than rank: STYLE_BY_ENERGY is sent to ElevenLabs as the `style`
# setting on the render, so the choice changes the read and not just the
# shortlist. That is what makes an amplitude worth drawing for it -- and why
# nothing is drawn for gender, age or accent, where a glyph would assert
# something the tool does not know.
# ---------------------------------------------------------------------------
def characteristics_detail() -> list[dict]:
    """CHARACTERISTICS, with what each option matches on and what it sends."""
    out = []
    for row in CHARACTERISTICS:
        options = []
        for option in row["options"]:
            entry = dict(option)
            if row["id"] == "energy":
                entry["matches"] = list(ENERGY_WORDS.get(option["id"], []))
                # 0..1, and it is literally the `style` value on the render.
                entry["style"] = STYLE_BY_ENERGY.get(option["id"])
            elif row["id"] == "delivery":
                entry["matches"] = list(DELIVERY_WORDS.get(option["id"], []))
            elif row["id"] == "accent" and option["id"] != "any":
                entry["matches"] = list(ACCENT_ALIASES.get(option["id"], []))
            else:
                entry["matches"] = []
            options.append(entry)
        out.append({"id": row["id"], "label": row["label"], "help": row.get("help", ""),
                    "options": options,
                    # Only energy and delivery are scored from free text; the
                    # rest read one published label field. Saying which is how
                    # somebody understands why an unlabelled account ranks flat.
                    "scored_on": ("description text" if row["id"] in ("energy", "delivery")
                                  else "the voice's published labels")})
    return out


def asked_count(want: dict | None) -> int:
    """How many characteristics were actually asked for.

    "any" is not a question, so a voice that matched two things out of two
    asked has matched everything -- and reporting it as "2 of 5" would read as
    a poor result. This is the denominator a screen should print.
    """
    want = normalise_want(want)
    return sum(1 for key in CHARACTERISTIC_IDS if want.get(key) not in (None, "", "any"))
