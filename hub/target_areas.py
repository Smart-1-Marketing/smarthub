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
        # The exception as it was typed, and the rule parsed out of it. Both
        # are kept: the sentence is what a person reads back and corrects, the
        # rule is what the ZIP list is actually filtered by, and storing only
        # one of them means either the screen cannot show what was asked for
        # or the filter has to re-parse prose on every read.
        "zipException": "",
        "zipRule": None,
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
    # Re-parsed on every normalise rather than trusted from the record: a
    # stored rule written before a spelling was recognised would go on being
    # unapplied for ever, and re-reading the sentence is what lets a fix here
    # reach quotes somebody saved last month.
    area["zipException"] = _clean(raw.get("zipException"))[:300]
    area["zipRule"] = parse_zip_rule(area["zipException"])

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
        # The exception is part of what makes an area itself. The same
        # radius run twice under two different restrictions -- the New Jersey
        # half of a Philadelphia buy and the Pennsylvania half -- is two
        # areas, and without this the second collapses into the first and
        # half the campaign disappears between the proposal and the IO.
        key = (area["type"], area["origin"].lower(), area["dma"].lower(),
               area["state"].lower(), area["other"].lower(), int(area["radius"]),
               area["zipException"].lower())
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
    """Every ZIP a campaign runs in, after each area's own exception.

    This is what the IO's ZIP field and the Suite webhook read, so it has to
    be the running list rather than the found one. A rule that narrowed the
    proposal and not the insertion order would be worse than no rule at all:
    the document a client signed and the campaign that was trafficked would
    disagree, with both looking correct on their own.
    """
    seen, out = set(), []
    for area in normalize(areas):
        for z in area_zips(area)["kept"]:
            if z not in seen:
                seen.add(z)
                out.append(z)
    return out


def dropped_zips(areas) -> list[str]:
    """Every ZIP an exception removed, so a screen can say what it cost."""
    seen, out = set(), []
    for area in normalize(areas):
        for z in area_zips(area)["dropped"]:
            if z not in seen:
                seen.add(z)
                out.append(z)
    return out


def zip_exceptions(areas) -> list[dict]:
    """The exceptions in force, one per area that has one.

    Includes the ones that could not be read: an exception somebody typed and
    the system did not apply is the single most important row here, and
    dropping it from this list is how it becomes invisible again.
    """
    out = []
    for area in normalize(areas):
        text = str(area.get("zipException") or "").strip()
        if not text:
            continue
        result = area_zips(area)
        out.append({"area": label(area), "text": text,
                    "understood": bool((result.get("rule") or {}).get("understood")),
                    "applied": result["applied"],
                    "kept": len(result["kept"]), "dropped": len(result["dropped"]),
                    "note": result["note"]})
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
        result = area_zips(area)
        if result["kept"]:
            text += f" | ZIP Codes: {', '.join(result['kept'])}"
        # The exception rides on the one string the IO PDF and the Suite
        # webhook actually read. Without it, a campaign restricted to one
        # state arrives looking like a plain radius and nobody trafficking it
        # has any way to know a rule was ever applied.
        if result["applied"] and result["dropped"]:
            text += (f" | Exception: {str(area.get('zipException') or '').strip()} "
                     f"({len(result['dropped'])} ZIP Codes excluded)")
        elif str(area.get("zipException") or "").strip() and not result["applied"]:
            text += (f" | Exception NOT applied: "
                     f"{str(area.get('zipException') or '').strip()}")
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


# ---------------------------------------------------------------------------
# ZIP exceptions — "only New Jersey ZIP Codes"
#
# A radius does not stop at a state line and a campaign frequently does. A
# 25-mile radius on Philadelphia is half of New Jersey; a client licensed in
# one state, a franchise with a protected territory, a dealer whose
# registration only works one side of the river -- all of them describe the
# same exception, in words, to a rep, who then either deleted a hundred ZIPs
# by hand or shipped the list as it came back.
#
# Both failures are silent. Nothing in either document said the list was
# supposed to be narrower, so the proposal quoted reach the client could not
# use and the insertion order trafficked the campaign into a state nobody was
# allowed to sell in -- and the only record of the restriction was a sentence
# in an email.
#
# So the exception is a field. It is written the way it is said, parsed into
# a rule the ZIP list is actually filtered by, printed on the proposal and
# carried to the IO. Three rules on it:
#
#   * **A rule that could not be understood is never silently ignored.**
#     `parse_zip_rule` returns `understood: False` with the text kept, and
#     every screen shows the sentence with "not applied" next to it. A
#     restriction that reads as saved and does nothing is worse than one
#     nobody typed.
#   * **Filtering never invents a ZIP.** It only removes, from the list the
#     lookup returned. "Only New Jersey" on a radius that touches no New
#     Jersey ZIP leaves nothing, and that empty result is reported as an
#     empty result rather than falling back to the unfiltered list.
#   * **The original list is kept.** Loosening a rule, or correcting a typo
#     in it, must not mean running the radius lookup again -- that call is
#     billed and slow, and the second answer would differ from the first.
# ---------------------------------------------------------------------------

# USPS ZIP prefix ranges, by state. Prefixes rather than ranges of full ZIPs
# because the first three digits are the sectional centre and never straddle a
# state line; a five-digit range table would need maintaining every time the
# Postal Service opens a facility. Ranges are inclusive on both ends.
STATE_ZIP_PREFIXES: dict[str, list[tuple[int, int]]] = {
    "AL": [(350, 369)], "AK": [(995, 999)], "AZ": [(850, 850), (852, 853), (855, 857), (859, 860), (863, 865)],
    "AR": [(716, 729), (755, 755)], "CA": [(900, 908), (910, 928), (930, 961)],
    "CO": [(800, 816)], "CT": [(60, 69)], "DE": [(197, 199)], "DC": [(200, 205), (569, 569)],
    "FL": [(320, 339), (341, 342), (344, 344), (346, 347), (349, 349)],
    "GA": [(300, 319), (398, 399)], "HI": [(967, 968)], "ID": [(832, 838)],
    "IL": [(600, 629)], "IN": [(460, 479)], "IA": [(500, 528)],
    "KS": [(660, 679)], "KY": [(400, 427)], "LA": [(700, 701), (703, 708), (710, 714)],
    "ME": [(39, 49)], "MD": [(206, 219)], "MA": [(10, 27), (55, 55)],
    "MI": [(480, 499)], "MN": [(550, 567)], "MS": [(386, 397)],
    "MO": [(630, 658)], "MT": [(590, 599)], "NE": [(680, 693)],
    "NV": [(889, 898)], "NH": [(30, 38), (3800, 3899)], "NJ": [(70, 89)],
    "NM": [(870, 884)], "NY": [(5, 5), (6, 6), (10, 14), (100, 149)],
    "NC": [(270, 289)], "ND": [(580, 588)], "OH": [(430, 459)],
    "OK": [(730, 731), (734, 749)], "OR": [(970, 979)],
    "PA": [(150, 196)], "RI": [(28, 29)], "SC": [(290, 299)],
    "SD": [(570, 577)], "TN": [(370, 385)], "TX": [(750, 799), (885, 885)],
    "UT": [(840, 847)], "VT": [(50, 54), (56, 59)],
    "VA": [(201, 201), (220, 246)], "WA": [(980, 994)],
    "WV": [(247, 268)], "WI": [(530, 549)], "WY": [(820, 831)],
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

# "only", "exclude" and the ways each is actually written. Order matters:
# "everything except New Jersey" must not match the "only" family first.
_EXCLUDE_WORDS = ("exclude", "excluding", "except", "not in", "no ", "without",
                  "outside", "drop", "remove", "leave out", "avoid")
_ONLY_WORDS = ("only", "just", "limit to", "restrict to", "confined to",
               "inside", "within", "keep")


def zip_state(zipcode: str) -> str:
    """Which state a five-digit ZIP belongs to, or "" if none matches."""
    digits = re.sub(r"\D", "", str(zipcode or ""))[:5]
    if len(digits) != 5:
        return ""
    prefix = int(digits[:3])
    for state, ranges in STATE_ZIP_PREFIXES.items():
        for low, high in ranges:
            if low <= prefix <= high:
                return state
    return ""


def _states_in(text: str) -> list[str]:
    """State codes named anywhere in a sentence, longest name first.

    Full names are matched before abbreviations, or "Washington" inside
    "Washington DC" resolves to WA — and a two-letter code is matched only as
    a whole word, or the "OR" in "New Jersey or Delaware" becomes Oregon.
    """
    lowered = f" {str(text or '').lower()} "
    found, consumed = [], lowered
    for name in sorted(STATE_NAMES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", consumed):
            code = STATE_NAMES[name]
            if code not in found:
                found.append(code)
            consumed = re.sub(rf"(?<![a-z]){re.escape(name)}(?![a-z])", " ", consumed)
    for code in STATE_ZIP_PREFIXES:
        if re.search(rf"\b{code.lower()}\b", consumed) and code not in found:
            found.append(code)
    return found


def parse_zip_rule(text) -> dict:
    """A ZIP exception written in words, as a rule the list can be filtered by.

    {"mode": "only"|"exclude"|"", "states": [...], "zips": [...],
     "text": "...", "understood": bool, "note": "..."}
    """
    raw = _clean(text)
    rule = {"mode": "", "states": [], "zips": [], "text": raw,
            "understood": False, "note": ""}
    if not raw:
        rule["note"] = "No exception — every ZIP the radius returns is used."
        rule["understood"] = True
        return rule

    lowered = raw.lower()
    explicit = zip_list(raw)
    exclude = any(word in lowered for word in _EXCLUDE_WORDS)
    only = any(word in lowered for word in _ONLY_WORDS)
    # "Everything except X" carries both families of word. Exclusion wins,
    # because the exception is the half that narrows the list.
    mode = "exclude" if exclude else ("only" if only else "")

    states = _states_in(raw)
    # A bare state name with no verb at all ("New Jersey ZIP codes") is the
    # way this gets typed most often, and it means only.
    if not mode and (states or explicit):
        mode = "only"

    if not mode or not (states or explicit):
        rule["note"] = ("This exception was not understood, so it has not been "
                        "applied — the full ZIP list is being used. Write it as "
                        "\"only New Jersey ZIP codes\" or \"exclude 46032, "
                        "46033\".")
        return rule

    rule.update({"mode": mode, "states": states, "zips": explicit,
                 "understood": True})
    rule["note"] = describe_zip_rule(rule)
    return rule


def describe_zip_rule(rule) -> str:
    """The rule in one plain sentence, for a document or a screen."""
    rule = rule or {}
    if not rule.get("understood") or not rule.get("mode"):
        return rule.get("note") or ""
    parts = []
    if rule.get("states"):
        parts.append(", ".join(rule["states"]))
    if rule.get("zips"):
        count = len(rule["zips"])
        parts.append(f"{count} named ZIP Code{'' if count == 1 else 's'}")
    listed = " and ".join(parts)
    if rule["mode"] == "only":
        return f"Restricted to {listed}. Every other ZIP the radius returns is dropped."
    return f"{listed} excluded. Every other ZIP the radius returns is kept."


def apply_zip_rule(zips, rule) -> dict:
    """Filter a ZIP list by a parsed rule.

    {"kept": [...], "dropped": [...], "applied": bool, "note": "..."} — the
    dropped list is returned rather than discarded, because "we found 240 and
    are running 90" is the claim, and a screen showing only the 90 cannot
    make it.
    """
    all_zips = zip_list(zips) if not isinstance(zips, list) else [
        z for z in zips if re.fullmatch(r"\d{5}", str(z))]
    rule = rule or {}
    if not rule.get("understood") or not rule.get("mode"):
        return {"kept": all_zips, "dropped": [], "applied": False,
                "note": rule.get("note") or ""}

    states = {s.upper() for s in rule.get("states") or []}
    named = set(rule.get("zips") or [])
    kept, dropped = [], []
    for code in all_zips:
        matches = code in named or (bool(states) and zip_state(code) in states)
        target = kept if (matches if rule["mode"] == "only" else not matches) else dropped
        target.append(code)

    note = describe_zip_rule(rule)
    if dropped:
        note += (f" {len(dropped)} of {len(all_zips)} ZIP Code"
                 f"{'' if len(all_zips) == 1 else 's'} dropped.")
    elif all_zips:
        note += " Nothing was dropped — every ZIP already satisfied it."
    if all_zips and not kept:
        note += (" Nothing is left: this radius returns no ZIP Code that "
                 "satisfies the exception. Widen the radius or the rule.")
    return {"kept": kept, "dropped": dropped, "applied": True, "note": note}


def area_zips(area) -> dict:
    """One area's ZIP list after its own exception, with what it cost.

    The single reading of "which ZIPs does this area actually run in",
    so the reach estimate, the proposal's ZIP section and the insertion order
    cannot disagree about it.
    """
    area = area if isinstance(area, dict) else {}
    rule = area.get("zipRule")
    if not isinstance(rule, dict):
        rule = parse_zip_rule(area.get("zipException") or area.get("zipRule"))
    result = apply_zip_rule(area.get("zips"), rule)
    result["rule"] = rule
    return result


def running_zips(areas) -> list[str]:
    """Every ZIP a campaign actually runs in, exceptions applied."""
    seen, out = set(), []
    for area in normalize(areas):
        for code in area_zips(area)["kept"]:
            if code not in seen:
                seen.add(code)
                out.append(code)
    return out


# ---------------------------------------------------------------------------
# Pasting a list of locations instead of typing each one into a box
#
# A campaign with eleven rooftops is eleven trips through the area editor --
# name, type, origin, radius, save, add another -- and the list the rep is
# copying from already exists: it is in the email, the spreadsheet or the
# client's own store locator. So the paste is read here rather than being
# retyped, and every rule below is about the two ways that goes wrong.
#
#   * **Nothing is dropped in silence.** A line this cannot read comes back
#     in `skipped` with the reason, because a paste of twelve lines that
#     quietly produces nine areas is a campaign missing three locations that
#     nobody can see is missing. The same rule `knack_websites.py` applies to
#     a value Knack would refuse.
#   * **Every line says how it was read.** "Carmel, IN 10mi" and
#     "Carmel, IN" are not the same instruction, and the second one gets a
#     radius this module chose. `rows` carries that sentence per line so the
#     screen can show it before anything is added -- a paste that silently
#     assumes ten miles on eight lines is eight quiet decisions.
#   * **Nothing is invented.** An unreadable fragment is never turned into a
#     radius around a guess at what it might have meant, the rule
#     `modules/ads_builder/logo.py` works to.
# ---------------------------------------------------------------------------
_PASTE_FIELD_SPLIT = re.compile(r"\t+|\s*\|\s*")
_RADIUS_IN_TEXT = re.compile(
    r"(?:\+\s*)?(\d{1,3}(?:\.\d+)?)\s*(?:mi\b|mi\.|miles?\b|mile\b|-?\s*mile\s*radius)",
    re.I)
_NATIONAL_WORDS = ("national", "nationwide", "nationally", "usa", "u.s.",
                   "united states", "coast to coast")
_STATEWIDE_WORDS = ("statewide", "state wide", "whole state", "entire state",
                    "state of")

# A header row pasted along with the rows. Recognised so it does not come
# back as a skipped line the rep has to read and dismiss on every paste.
_HEADER_WORDS = ("location", "locations", "city", "cities", "market",
                 "markets", "store", "stores", "address", "radius", "zip",
                 "zips", "zip code", "zip codes", "name", "area", "areas",
                 "target", "targets", "dma", "state", "notes", "note")

MAX_PASTE_LINES = 200       # a paste, not an import


def _looks_like_header(fields: list[str]) -> bool:
    """A pasted spreadsheet's first row, rather than a location."""
    if not fields or len(fields) < 2:
        return False
    return all(f.strip().lower() in _HEADER_WORDS for f in fields if f.strip())


def _radius_from(text: str) -> tuple[str, int]:
    """A radius stated inside a line, and the line with it taken out."""
    match = _RADIUS_IN_TEXT.search(text)
    if not match:
        return text, 0
    radius = int(_num(match.group(1), 0))
    rest = (text[:match.start()] + " " + text[match.end():])
    rest = re.sub(r"\s*[+,;/-]\s*$", "", rest.strip())
    rest = re.sub(r"^\s*[+,;/-]\s*", "", rest)
    return rest.strip(), radius


def _geography_from(text: str) -> tuple[dict, str] | tuple[None, str]:
    """One line of pasted geography, as an area and a sentence saying how.

    Returns ``(None, reason)`` where the line names nowhere: a fragment left
    over from a spreadsheet, a phone number, a page of notes. The reason is
    shown to the rep rather than being counted, because "we read nine of your
    twelve lines" is only useful with the three named.
    """
    raw = _clean(text)
    if not raw:
        return None, "the line is empty"
    lowered = raw.lower()

    # National first: "nationwide" contains no city and every other branch
    # below would read it as one.
    if any(w in lowered for w in _NATIONAL_WORDS) and len(raw) <= 40:
        area = blank_area("National")
        area["type"] = NATIONAL
        return area, "read as a national campaign"

    zips = zip_list(raw)
    words_left = re.sub(r"\b\d{5}\b", " ", raw)
    words_left = re.sub(r"[,;/|]+", " ", words_left).strip()
    if len(zips) >= 2 and not words_left:
        area = blank_area(f"{len(zips)} ZIP Codes")
        area["type"] = OTHER
        area["other"] = f"{len(zips)} ZIP Codes"
        area["zips"] = ", ".join(zips)
        return area, f"read as a list of {len(zips)} ZIP Codes"

    if re.search(r"\bdma\b", lowered):
        dma = re.sub(r"\bdma\b", " ", raw, flags=re.I)
        dma = re.sub(r"\s{2,}", " ", dma).strip(" ,-–—")
        if not dma:
            return None, "a DMA was named without saying which one"
        area = blank_area(f"{dma} DMA")
        area["type"] = DMA
        area["dma"] = dma
        return area, f"read as the {dma} DMA"

    if any(w in lowered for w in _STATEWIDE_WORDS):
        states = _states_in(raw)
        rest = re.sub(r"statewide|state wide|whole state|entire state|state of",
                      " ", raw, flags=re.I)
        rest = re.sub(r"\s{2,}", " ", rest).strip(" ,-–—:")
        state = rest or (states[0] if states else "")
        if not state:
            return None, "statewide was asked for without saying which state"
        area = blank_area(f"{state} (statewide)")
        area["type"] = STATEWIDE
        area["state"] = state
        return area, f"read as statewide — {state}"

    # A bare state name on its own line means that state, not a radius drawn
    # on a city that happens to share the name.
    if lowered in STATE_NAMES:
        pretty = raw.strip().title()
        area = blank_area(f"{pretty} (statewide)")
        area["type"] = STATEWIDE
        area["state"] = pretty
        return area, f"read as statewide — {pretty}"

    origin, radius = _radius_from(raw)
    origin = origin.strip(" ,-–—:;")
    if not origin:
        return None, "no city or ZIP Code in the line to draw a radius on"
    # Something has to identify a place, and a place is short. "Carmel, IN"
    # and "1200 Main St, Carmel IN" are five words between them; a line
    # somebody typed into the wrong box is a sentence. A comma alone is not
    # evidence -- prose has commas in it, which is how "not a place at all,
    # just a sentence somebody typed into the wrong box" became a target area
    # with a ten-mile radius drawn on it.
    if len(origin.split()) > 6 and not zip_list(origin):
        return None, "this reads as a note rather than a place"
    if not (zip_list(origin) or "," in origin or _states_in(origin)
            or len(origin.split()) <= 4):
        return None, "this reads as a note rather than a place"
    area = blank_area()
    area["type"] = RADIUS
    area["origin"] = origin
    if radius:
        area["radius"] = radius
        return area, f"read as {origin} + {radius}-mile radius"
    area["radius"] = blank_area()["radius"]
    return area, (f"read as {origin} — no radius was stated, so "
                  f"{area['radius']} miles is assumed. Change it on the area.")


def parse_paste(text, existing=None) -> dict:
    """A pasted block of locations, as target areas.

    One line per location. A line may be just the place ("Carmel, IN 10mi"),
    or tab- or pipe-separated fields the way a spreadsheet pastes:

        Carmel showroom | Carmel, IN | 10
        Fishers showroom | Fishers, IN | 10 | weekends only
        Indianapolis DMA
        46032, 46033, 46074

    ``existing`` is whatever the campaign already holds, so a location
    already on it is reported as a duplicate rather than added twice --
    pasting the same list after adding one by hand is the ordinary case.

        {"areas": [...], "rows": [{"line", "read", "duplicate"}],
         "skipped": [{"line", "reason"}], "note": "..."}
    """
    raw = str(text or "")
    lines = [ln for chunk in raw.split("\n") for ln in chunk.split(";")]
    lines = [ln.strip() for ln in lines]
    out: dict = {"areas": [], "rows": [], "skipped": [], "note": ""}

    seen = set()
    for area in normalize(existing):
        seen.add((canonical_type(area["type"]), describe(area).lower()))

    over = 0
    for line in lines:
        if not line:
            continue
        fields = [f.strip() for f in _PASTE_FIELD_SPLIT.split(line) if f.strip()]
        if _looks_like_header(fields):
            continue
        if len(out["areas"]) >= MAX_AREAS:
            over += 1
            continue
        if len(out["rows"]) + len(out["skipped"]) >= MAX_PASTE_LINES:
            over += 1
            continue

        name, notes, geography = "", "", line
        if len(fields) > 1:
            # A bare number in its own column is the radius, wherever the
            # spreadsheet happens to put it -- a "Radius" column with a
            # "Notes" column after it is the common shape, and reading only
            # the last field files the radius into the notes and quietly
            # falls back to the default.
            trailing_radius = ""
            for ix in range(len(fields) - 1, 0, -1):
                if re.fullmatch(r"\d{1,3}(?:\.\d+)?", fields[ix] or ""):
                    trailing_radius = fields.pop(ix)
                    break
            if len(fields) >= 3:
                name, geography, notes = fields[0], fields[1], " ".join(fields[2:])
            elif len(fields) == 2:
                name, geography = fields[0], fields[1]
            else:
                geography = fields[0]
            if trailing_radius and not _RADIUS_IN_TEXT.search(geography):
                geography = f"{geography} {trailing_radius} miles"

        area, read = _geography_from(geography)
        if area is None:
            out["skipped"].append({"line": line, "reason": read})
            continue
        if name and name.lower() != geography.lower():
            area["name"] = name[:200]
        if notes:
            area["notes"] = notes[:500]

        key = (canonical_type(area["type"]), describe(area).lower())
        if key in seen:
            out["rows"].append({"line": line, "read": read, "duplicate": True})
            continue
        seen.add(key)
        out["areas"].append(area)
        out["rows"].append({"line": line, "read": read, "duplicate": False})

    added = len(out["areas"])
    dupes = sum(1 for r in out["rows"] if r["duplicate"])
    bits = [f"{added} target area{'' if added == 1 else 's'} read"]
    if dupes:
        bits.append(f"{dupes} already on this campaign")
    if out["skipped"]:
        bits.append(f"{len(out['skipped'])} line"
                    f"{'' if len(out['skipped']) == 1 else 's'} not understood")
    if over:
        bits.append(f"{over} beyond the {MAX_AREAS}-area limit were not read")
    out["note"] = " · ".join(bits)
    if not added and not dupes and not out["skipped"]:
        out["note"] = "Nothing in that paste looked like a location."
    return out


# ---------------------------------------------------------------------------
# The same paste, for the competitors and venues inside those areas
#
# `targetsOfInterest` is three boxes a row and a campaign routinely names a
# dozen of them, which is the same retyping problem one level in. Deliberately
# a separate reader rather than a mode on the one above: a competitor line is
# a business and an address, an area line is a geography and a radius, and one
# parser trying to be both would read "Riverside Dental, 1200 Main St" as a
# city called Riverside Dental.
# ---------------------------------------------------------------------------
PLACE_KINDS = ("competitor", "venue", "place")

# A street address, as opposed to a business name. Enough to tell the two
# apart in a "Name, address" line -- nothing here geocodes and nothing
# invents an address that was not typed.
_STREET_RE = re.compile(
    r"\b\d+\s+[\w.'-]+(\s+[\w.'-]+)*\s+"
    r"(st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|ct|"
    r"court|pkwy|parkway|hwy|highway|pl|place|ter|terrace|cir|circle|"
    r"suite|ste|unit)\b", re.I)


def parse_places(text, kind: str = "competitor", existing=None) -> dict:
    """A pasted list of competitors, venues or places, as target rows.

        Riverside Dental | 1200 Main St, Carmel, IN 46032 | their implants
        Lucas Oil Stadium, 500 S Capitol Ave, Indianapolis, IN

    The first field is always the name. An address is taken only where the
    line plainly holds one -- a row with no address is legitimate and common
    (conquesting by brand and behaviour needs no location), so guessing one
    from a business name is the one thing this must never do.

        {"places": [{"kind", "name", "address", "note"}],
         "rows": [...], "skipped": [...], "note": "..."}
    """
    kind = kind if kind in PLACE_KINDS else "competitor"
    raw = str(text or "")
    out: dict = {"places": [], "rows": [], "skipped": [], "note": ""}

    seen = {str((r or {}).get("name") or "").strip().lower()
            for r in (existing or []) if isinstance(r, dict)}
    seen.discard("")

    for line in [ln.strip() for ln in raw.split("\n")]:
        if not line:
            continue
        if len(out["rows"]) + len(out["skipped"]) >= MAX_PASTE_LINES:
            break
        fields = [f.strip() for f in _PASTE_FIELD_SPLIT.split(line) if f.strip()]
        if _looks_like_header(fields):
            continue
        name = address = note = ""
        if len(fields) >= 2:
            name = fields[0]
            address = fields[1] if _STREET_RE.search(fields[1]) else ""
            rest = fields[2:] if address else fields[1:]
            note = " ".join(rest)[:200]
        else:
            # One field: "Name, 1200 Main St, Carmel IN" or just a name. The
            # address starts at the first comma-separated part that looks
            # like a street, so a business with a comma in its own name
            # ("Smith, Jones & Co") keeps it.
            parts = [p.strip() for p in line.split(",")]
            cut = next((i for i, p in enumerate(parts) if _STREET_RE.search(p)), None)
            if cut is None or cut == 0:
                name = line
            else:
                name = ", ".join(parts[:cut]).strip()
                address = ", ".join(parts[cut:]).strip()
        name = name.strip(" ,-–—")[:200]
        if not name:
            out["skipped"].append({"line": line, "reason": "no name in the line"})
            continue
        if name.lower() in seen:
            out["rows"].append({"line": line, "name": name, "duplicate": True,
                                "read": "already on this campaign"})
            continue
        seen.add(name.lower())
        out["places"].append({"kind": kind, "name": name,
                              "address": address[:200], "note": note})
        out["rows"].append({"line": line, "name": name, "duplicate": False,
                            "read": (f"{name} — geo-fenceable at {address}"
                                     if address else
                                     f"{name} — no address, so brand and "
                                     "behaviour only")})

    added = len(out["places"])
    dupes = sum(1 for r in out["rows"] if r["duplicate"])
    fenceable = sum(1 for p in out["places"] if p["address"])
    bits = [f"{added} read"]
    if fenceable:
        bits.append(f"{fenceable} with an address we can fence")
    if dupes:
        bits.append(f"{dupes} already named")
    if out["skipped"]:
        bits.append(f"{len(out['skipped'])} not understood")
    out["note"] = " · ".join(bits) if (added or dupes or out["skipped"]) \
        else "Nothing in that paste looked like a name."
    return out
