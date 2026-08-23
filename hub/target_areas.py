"""Target areas — one campaign, several places.

## Why this exists

Both intake tools were built as though a campaign runs in exactly one place.
The Proposal Builder holds ``geoType`` / ``geo`` / ``radius``; the IO Builder
holds ``geoType`` / ``geoOrigin`` / ``geoRadius`` / ``geoDMA`` / ``geoOther`` /
``geoZipcodes``. Both are single-valued, and both flatten to one string before
anything downstream reads them.

That is wrong for most of what we actually sell. A dealer group with four
rooftops, a franchise with six trade areas and a law firm covering three DMAs
are all one campaign with several target areas, and the only way to express
that today is to type them all into one box:

    "Carmel IN + 10mi, Fishers IN + 10mi, Noblesville IN + 15mi"

which reads fine to a person and is unusable to everything else — the reach
estimate sizes it as one area, the IO's ZIP list belongs to whichever origin
was typed first, and nothing can price or traffic a single location.

## The shape

An area is a dict, and a campaign carries a *list* of them::

    {"id": "a1b2c3d4",
     "name": "Carmel showroom",        # what the rep calls this location
     "type": "City/ZIP + Radius",      # one of TYPES
     "origin": "Carmel, IN",           # city or ZIP the radius starts from
     "radius": 10,                     # miles, radius type only
     "dma": "", "state": "", "other": "",
     "zips": "46032, 46033",           # comma-separated, radius/other types
     "population": 0,                  # only when stated rather than derived
     "notes": ""}

Deliberately:

  * **The name is the point.** "Carmel showroom" is what a rep says on a call
    and what a client recognises on the proposal. A bare "Carmel, IN + 10 mi"
    row cannot be talked about.
  * **Nothing is required except a type.** Areas are captured while a deal is
    still vague. An area with a name and nothing else is legitimate and must
    round-trip rather than being dropped on save.
  * **Legacy state converts both ways.** ``from_legacy`` reads either tool's
    old single-geo fields; ``to_legacy_geo`` writes the one-string form back.
    Every saved quote and every IO in the database predates this module, and
    the Suite webhook's ``geographic_target`` is a single string that a
    GoHighLevel workflow already reads. Neither may break.
  * **Population is estimated, never invented.** ``estimated_population``
    returns ``None`` for an area it cannot size, and callers must say "not
    measured" rather than showing a confident zero.
"""
from __future__ import annotations

import math
import re
import uuid

# The types both tools offer. The first two spellings are what the Proposal
# Builder and the IO Builder each shipped; they mean the same thing, so
# `normalize` folds them together rather than making the difference visible.
RADIUS = "City/ZIP + Radius"
DMA = "DMA"
STATEWIDE = "Statewide"
NATIONAL = "National"
OTHER = "Other"

TYPES = (RADIUS, DMA, STATEWIDE, NATIONAL, OTHER)

_TYPE_ALIASES = {
    "city/zip code + radius": RADIUS,       # IO Builder's spelling
    "city/zip + radius": RADIUS,            # Proposal Builder's spelling
    "city + radius": RADIUS,
    "zip + radius": RADIUS,
    "radius": RADIUS,
    "dma": DMA,
    "statewide": STATEWIDE,
    "state": STATEWIDE,
    "national": NATIONAL,
    "nationwide": NATIONAL,
    "other": OTHER,
}

# ---------------------------------------------------------------------------
# People per square mile inside a radius.
#
# This was one number, 900, and it read low on every campaign anyone ran. 900
# is roughly a whole county's average, and a radius is not a county: a rep
# draws it on a city or a ZIP, deliberately centred on where the people are.
# A 10-mile ring around a city centre was being sized at 283,000 when the real
# figure is closer to two-thirds of a million, and a reach panel that
# under-reports by that much gets stopped believing.
#
# One constant cannot serve both ends of the range either. A number right for
# 10 miles is far too high for 50, because the bigger circle keeps reaching
# into land nobody lives on. So density falls as the radius grows.
#
# Deliberately a step function rather than a curve. The wizard mirrors this
# arithmetic in JavaScript so the reach panel updates live, and the last time
# these two disagreed it was over floating-point -- 3.14159 against Math.PI,
# then int() against Math.round. Steps and integers cannot drift that way:
# either side reads the same table and gets the same answer exactly.
# test_target_areas.py asserts they do.
#
# These are estimates and are labelled as such wherever they appear. The AI
# re-estimate sizes each area for real; this is the fallback that has to be
# defensible on its own.
_DENSITY_BY_RADIUS = (
    (5, 2600),      # a downtown or a dense inner suburb
    (10, 2100),     # city plus its first ring -- the common case
    (25, 1400),     # the metro, reaching the outer suburbs
    (50, 800),      # the metro plus the rural counties around it
)
_DENSITY_BEYOND = 500   # past 50 miles it is mostly countryside


def density_for(radius: float) -> int:
    """People per square mile to assume inside a radius of this size."""
    for limit, density in _DENSITY_BY_RADIUS:
        if radius <= limit:
            return density
    return _DENSITY_BEYOND


def density_table() -> dict:
    """The whole assumption, for the wizard's mirror and for the help text."""
    return {"steps": [list(row) for row in _DENSITY_BY_RADIUS],
            "beyond": _DENSITY_BEYOND}
_DMA_POPULATION = 1_200_000
_STATE_POPULATION = 6_500_000
_US_POPULATION = 335_000_000

MAX_AREAS = 25          # a campaign, not a mailing list


def _clean(value) -> str:
    return str(value or "").strip()


def _num(value, default=0.0) -> float:
    """A number from whatever a form sent — "$1,200", "10 mi", "" all work."""
    raw = re.sub(r"[^0-9.]", "", str(value if value is not None else ""))
    try:
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


def canonical_type(value) -> str:
    """One of TYPES, from any spelling either tool has used."""
    raw = _clean(value).lower()
    if not raw:
        return RADIUS
    return _TYPE_ALIASES.get(raw, RADIUS if raw not in TYPES else raw)


def zip_list(value) -> list[str]:
    """The five-digit ZIP Codes in a string, de-duplicated, in order.

    The IO Builder's AI ZIP lookup returns a long comma-separated string that
    a rep may then hand-edit, so this parses rather than trusts: a stray word
    or a doubled comma must not become a ZIP.
    """
    seen, out = set(), []
    for z in re.findall(r"\b\d{5}\b", str(value or "")):
        if z not in seen:
            seen.add(z)
            out.append(z)
    return out


def blank_area(name: str = "") -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "name": _clean(name),
        "type": RADIUS,
        "origin": "",
        "radius": 10,
        "dma": "",
        "state": "",
        "other": "",
        "zips": "",
        "population": 0,
        "notes": "",
    }


def normalize_area(raw) -> dict:
    """One area, in the canonical shape, from anything close to it.

    Accepts a bare string ("Carmel, IN") as well as a dict, because both
    tools' AI drafts return geography as prose and the alternative is every
    call site guessing.
    """
    if isinstance(raw, str):
        raw = {"name": raw, "origin": raw, "type": RADIUS}
    if not isinstance(raw, dict):
        raw = {}
    area = blank_area()
    area["id"] = _clean(raw.get("id")) or area["id"]
    area["type"] = canonical_type(raw.get("type") or raw.get("geoType"))
    area["origin"] = _clean(raw.get("origin") or raw.get("geoOrigin") or raw.get("geo"))
    area["dma"] = _clean(raw.get("dma") or raw.get("geoDMA"))
    area["state"] = _clean(raw.get("state"))
    area["other"] = _clean(raw.get("other") or raw.get("geoOther"))
    area["zips"] = ", ".join(zip_list(raw.get("zips") or raw.get("geoZipcodes")))
    area["notes"] = _clean(raw.get("notes"))[:500]

    radius = _num(raw.get("radius") if raw.get("radius") is not None else raw.get("geoRadius"), 0)
    area["radius"] = int(radius) if radius > 0 else 10

    population = _num(raw.get("population"), 0)
    area["population"] = int(population) if population > 0 else 0

    # A name is what makes an area referable, so derive one when the rep did
    # not type it. Only for an area that names somewhere, though: `describe`
    # falls back to the default radius, so naming an otherwise-blank area
    # would turn it into a "10-mile radius" row nobody entered — and one that
    # `is_empty` would then be unable to recognise as blank.
    area["name"] = _clean(raw.get("name"))
    if not area["name"] and not is_empty(area):
        area["name"] = describe(area)
    return area


def normalize(areas) -> list[dict]:
    """A campaign's areas, canonical, de-duplicated and bounded."""
    if isinstance(areas, (str, dict)):
        areas = [areas]
    if not isinstance(areas, (list, tuple)):
        return []
    out, seen = [], set()
    for raw in areas:
        area = normalize_area(raw)
        if is_empty(area):
            continue
        key = (area["type"], area["origin"].lower(), area["dma"].lower(),
               area["state"].lower(), area["other"].lower(), int(area["radius"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(area)
        if len(out) >= MAX_AREAS:
            break
    return out


def is_empty(area: dict) -> bool:
    """True when an area names nowhere.

    A name alone is enough to keep — a rep who typed "North stores" and moved
    on has told us something, and silently dropping it on save is how a target
    area disappears between the proposal and the IO.
    """
    if not isinstance(area, dict):
        return True
    kind = canonical_type(area.get("type"))
    if kind in (STATEWIDE, NATIONAL):
        return False
    return not any(_clean(area.get(k)) for k in
                   ("name", "origin", "dma", "state", "other", "zips"))


def describe(area: dict) -> str:
    """The geography of one area, as a person would say it."""
    if not isinstance(area, dict):
        return ""
    kind = canonical_type(area.get("type"))
    if kind == RADIUS:
        origin = _clean(area.get("origin"))
        radius = int(_num(area.get("radius"), 0)) or 0
        if origin and radius:
            return f"{origin} + {radius}-mile radius"
        return origin or (f"{radius}-mile radius" if radius else "")
    if kind == DMA:
        dma = _clean(area.get("dma"))
        return f"{dma} DMA" if dma and "dma" not in dma.lower() else dma
    if kind == STATEWIDE:
        state = _clean(area.get("state"))
        return f"{state} (statewide)" if state else "Statewide"
    if kind == NATIONAL:
        return "National"
    return _clean(area.get("other")) or _clean(area.get("origin"))


def label(area: dict) -> str:
    """Name plus geography, for a list row or a PDF line.

    The name is dropped when it only repeats the geography, which is what
    `normalize_area` fills in when the rep did not name the area — otherwise
    this reads as "Carmel, IN — Carmel, IN + 10-mile radius".
    """
    geography = describe(area)
    name = _clean((area or {}).get("name"))
    if not name or not geography:
        return geography or name
    if name == geography or geography.lower().startswith(name.lower()):
        return geography
    return f"{name} — {geography}"


def summary(areas, limit: int = 2) -> str:
    """A one-line summary for a list column or the proposal's meta table."""
    rows = normalize(areas)
    if not rows:
        return ""
    shown = [label(a) for a in rows[:max(1, limit)]]
    rest = len(rows) - len(shown)
    return " · ".join(shown) + (f" · +{rest} more" if rest > 0 else "")


def names(areas) -> list[str]:
    return [label(a) for a in normalize(areas)]


def all_zips(areas) -> list[str]:
    seen, out = set(), []
    for area in normalize(areas):
        for z in zip_list(area.get("zips")):
            if z not in seen:
                seen.add(z)
                out.append(z)
    return out


def estimated_population(area: dict):
    """Roughly how many people an area covers, or None when unknowable.

    None rather than 0: an area we cannot size must read as "not measured" on
    screen. A zero looks like an answer, and this codebase has already paid
    for that once.
    """
    if not isinstance(area, dict):
        return None
    stated = int(_num(area.get("population"), 0))
    if stated > 0:
        return stated
    kind = canonical_type(area.get("type"))
    if kind == RADIUS:
        radius = _num(area.get("radius"), 0)
        if radius <= 0:
            return None
        # math.pi, not a truncated literal: the wizard sizes the same area in
        # JavaScript with Math.PI, and a rounded constant here made the PDF
        # disagree with the screen the rep quoted from. Two people out of
        # 636,000 is nothing; a proposal that contradicts the tool is not.
        # round, not int: the wizard uses Math.round for the same sum, and
        # truncating here put the PDF one person below the screen the rep
        # quoted from. Trivial in itself, and exactly the kind of drift that
        # makes someone stop trusting both numbers.
        return round(math.pi * radius * radius * density_for(radius))
    if kind == DMA:
        return _DMA_POPULATION if _clean(area.get("dma")) else None
    if kind == STATEWIDE:
        return _STATE_POPULATION
    if kind == NATIONAL:
        return _US_POPULATION
    return None


def total_population(areas):
    """Combined reach, or None when no area could be sized.

    Overlapping areas are *not* deducted. Two 10-mile radii five miles apart
    share most of their population, and this returns the sum of both — which
    is why the number is labelled an estimate everywhere it is shown and why
    the AI re-estimate exists. Pretending to de-duplicate would be a guess
    wearing a precise number's clothes.
    """
    sized = [p for p in (estimated_population(a) for a in normalize(areas)) if p]
    return sum(sized) if sized else None


def unsized(areas) -> list[str]:
    """Areas that could not be sized — named, so the UI can say which."""
    return [label(a) for a in normalize(areas) if estimated_population(a) is None]


# ---------------------------------------------------------------- legacy ---
def from_legacy(state) -> list[dict]:
    """One area from either tool's old single-geo fields.

    Every quote and IO saved before this module has its geography in these
    fields and nowhere else, so this runs on load rather than as a migration:
    a record that is never re-opened is never rewritten, and it still reads
    correctly when it is.
    """
    if not isinstance(state, dict):
        return []
    existing = state.get("targetAreas") or state.get("target_areas") or state.get("areas")
    if existing:
        return normalize(existing)

    kind = canonical_type(state.get("geoType"))
    area = blank_area()
    area["type"] = kind
    area["zips"] = ", ".join(zip_list(state.get("geoZipcodes")))
    area["radius"] = int(_num(state.get("geoRadius") or state.get("radius"), 0)) or 10
    area["population"] = int(_num(state.get("population"), 0))
    area["dma"] = _clean(state.get("geoDMA"))
    area["other"] = _clean(state.get("geoOther"))

    # `geo` is the display string both tools built; `geoOrigin` is the IO's
    # raw origin. Prefer the raw one — `geo` has the radius and ZIP list
    # already baked into it and would round-trip as "Carmel, IN + 10-mile
    # radius + 10-mile radius".
    origin = _clean(state.get("geoOrigin"))
    if not origin:
        raw_geo = _clean(state.get("geo"))
        origin = raw_geo.split("|")[0].split(" + ")[0].strip()
    area["origin"] = origin
    if kind == STATEWIDE:
        area["state"] = origin or _clean(state.get("geoOther"))
    if kind == OTHER and not area["other"]:
        area["other"] = origin
    # Emptiness is judged before the name is derived. `describe` on a record
    # with no geography still returns "10-mile radius" from the default
    # radius, and naming the area first would make that phantom look real —
    # every quote that never reached the geography step would load with a
    # target area nobody entered.
    if is_empty(area):
        return []
    area["name"] = describe(area)
    return [area]


def to_legacy_geo(areas) -> str:
    """The single geography string the IO PDF and the Suite webhook expect.

    ``geographic_target`` is read by a GoHighLevel workflow that predates
    multi-area campaigns. It keeps receiving one string; a multi-area campaign
    sends all of them joined, which is strictly more than it used to get.
    """
    rows = normalize(areas)
    if not rows:
        return ""
    parts = []
    for area in rows:
        text = label(area)
        zips = zip_list(area.get("zips"))
        if zips:
            text += f" | ZIP Codes: {', '.join(zips)}"
        parts.append(text)
    return "  ||  ".join(parts)


def apply_to_legacy(state: dict, areas) -> dict:
    """Write the old single-geo fields back from the area list.

    The IO's PDF, its guardrails and its campaign-naming helper all read
    ``geoType`` / ``geoOrigin`` / ``geo``. Rather than chase every reader, the
    first area keeps those fields populated so anything not yet area-aware
    still sees a correct — if partial — campaign, and the summary line says
    how many more there are.
    """
    rows = normalize(areas)
    if not isinstance(state, dict):
        return state
    state["targetAreas"] = rows
    if not rows:
        return state
    first = rows[0]
    state["geoType"] = first["type"]
    state["geoOrigin"] = first["origin"]
    state["geoRadius"] = first["radius"]
    state["geoDMA"] = first["dma"]
    state["geoOther"] = first["other"]
    state["geoZipcodes"] = ", ".join(all_zips(rows))
    state["radius"] = first["radius"]
    state["geo"] = to_legacy_geo(rows)
    return state


def for_prompt(areas) -> str:
    """Areas as a compact line for an AI prompt.

    Named areas rather than one merged string: a model asked to write about
    "Carmel, Fishers, Noblesville" writes about a region, while one given
    three named areas writes about three places, which is what a multi-location
    client is buying.
    """
    rows = normalize(areas)
    if not rows:
        return ""
    out = []
    for area in rows:
        text = label(area)
        population = estimated_population(area)
        if population:
            text += f" (~{population:,} people)"
        if area.get("notes"):
            text += f" — {area['notes']}"
        out.append(text)
    return "; ".join(out)
