"""Prefill the Schema Builder's Business Info step from the client brief.

`hub/schema_questions.py` already answers each of its 35 questions from the
best source it can, but it has never known the client's canonical industry
key, the brand kit's own logo, or the place the scan found on their site --
so a rep confirmed a schema.org `@type` and pasted a logo URL by hand on
every business, even where `hub.industry.resolve_industry()` and
`hub.client_brief.build()` already held the answer.

`prefill()` offers what the brief knows into whatever the business_info
store does not already have -- the same "offered into empty fields only, a
person presses Save" rule `hub/scan_facts.contact_suggestions()` already
works to. A fact from the brief that disagrees with a saved value is a
*note*, never applied: a person's own answer beats anything read off a
page.

`SCHEMA_TYPES` is the canonical-industry-key -> schema.org `@type` table.
Every key `hub.industry.INDUSTRIES` declares has an entry -- `test_unwired`'s
own rule about a table with no third branch, applied to a taxonomy: adding
an industry without adding its type here is a schema silently falling back
to plain `LocalBusiness`.
"""
from __future__ import annotations

from typing import Any

# Canonical industry key -> schema.org @type. One entry per key in
# hub.industry.INDUSTRIES, including "general". Where an industry can be
# read more specifically from the text of its own label (a dentist inside
# "healthcare", a repair shop inside "automotive"), `_refine_type()` below
# narrows it further -- a heuristic on the label rather than a subtype table,
# because hub.industry.py only declares subtypes for "legal" today.
SCHEMA_TYPES: dict[str, str] = {
    "hvac": "HVACBusiness",
    "home_services": "HomeAndConstructionBusiness",
    "home_improvement": "HomeAndConstructionBusiness",
    "construction": "GeneralContractor",
    "solar": "HomeAndConstructionBusiness",
    "automotive": "AutoDealer",
    "marine": "LocalBusiness",
    "rv": "LocalBusiness",
    "restaurant": "Restaurant",
    "retail": "Store",
    "ecommerce": "OnlineStore",
    "legal": "LegalService",
    "healthcare": "MedicalBusiness",
    "senior_care": "MedicalBusiness",
    "fitness": "ExerciseGym",
    "beauty_wellness": "HealthAndBeautyBusiness",
    "real_estate": "RealEstateAgent",
    "financial": "FinancialService",
    "professional_services": "ProfessionalService",
    "technology": "LocalBusiness",
    "manufacturing": "LocalBusiness",
    "education": "EducationalOrganization",
    "recruiting": "LocalBusiness",
    "nonprofit": "NGO",
    "government": "GovernmentOrganization",
    "tourism": "LodgingBusiness",
    "ski": "SportsActivityLocation",
    "events": "LocalBusiness",
    "pets_vet": "VeterinaryCare",
    "general": "LocalBusiness",
}

# legal's subtype table, from hub.industry.INDUSTRIES itself -- "pi" reads
# as an injury attorney rather than the generic legal service.
_LEGAL_SUBTYPE_TYPES = {"pi": "Attorney", "general": "LegalService"}

# Label text that narrows a base type further. Checked only within the
# named industry, so "repair" inside "professional_services" (an IT repair
# shop) is not pulled into AutoRepair by accident.
_LABEL_REFINEMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "healthcare": (("dentist", "Dentist"), ("dental", "Dentist")),
    "automotive": (("repair", "AutoRepair"), ("tire", "AutoRepair"),
                   ("body shop", "AutoRepair")),
}


def schema_type_for(industry_key: str, subtype: str = "", label: str = "") -> str:
    """The schema.org `@type` for a resolved industry.

    `industry_key` is `hub.industry.resolve_industry()`'s own key (or a
    legacy one -- resolved the same way `hub.industry.industry()` does).
    `subtype` is legal's "pi"/"general". `label` is the free-text industry
    label the brief carries, used only to refine an otherwise generic type.
    """
    from hub import industry as _industry

    key = str(industry_key or "general").strip().lower()
    ind = _industry.industry(key)
    key = ind["key"] if ind else "general"

    if key == "legal":
        sub = str(subtype or "").strip().lower()
        if sub in _LEGAL_SUBTYPE_TYPES:
            return _LEGAL_SUBTYPE_TYPES[sub]
        low = str(label or "").lower()
        if "injury" in low:
            return _LEGAL_SUBTYPE_TYPES["pi"]
        return SCHEMA_TYPES.get(key, "LocalBusiness")

    base = SCHEMA_TYPES.get(key, "LocalBusiness")
    low = str(label or "").lower()
    for needle, refined in _LABEL_REFINEMENTS.get(key, ()):
        if needle in low:
            return refined
    return base


def _fact_value(fact: Any) -> Any:
    if isinstance(fact, dict) and fact.get("value") not in (None, ""):
        return fact["value"]
    return None


def prefill(client: str, domain: str = "") -> dict:
    """What the client brief knows about `client` that the Schema Builder's
    business_info store does not already have.

    Never raises -- a brief or a store that cannot be read costs only its
    own half. Returns::

        {"fields": {key: {"value":, "source":}},
         "schema_type": "...",
         "disagreements": [{"field", "saved", "seen", "source"}]}

    `fields` is offered, never applied -- nothing here writes to the store.
    A saved value that disagrees with what the brief found is not offered
    over; it is named in `disagreements` instead, so a rep sees it without
    the prefill silently overwriting their own answer.
    """
    from hub import client_brief

    try:
        from hub import seo
        store = seo.load_store(client) or {}
    except Exception:                                     # noqa: BLE001
        store = {}
    known = dict(store.get("business_info") or {})

    out: dict[str, Any] = {"fields": {}, "schema_type": "LocalBusiness",
                           "disagreements": []}

    try:
        brief = client_brief.build(client, domain)
    except Exception:                                     # noqa: BLE001
        return out

    identity = brief.get("identity") or {}
    location = brief.get("location") or {}
    contact = brief.get("contact") or {}
    brand = brief.get("brand") or {}
    social = brief.get("social") or {}

    def offer(key: str, fact: Any) -> None:
        value = _fact_value(fact)
        if value in (None, ""):
            return
        saved = str(known.get(key) or "").strip()
        if saved:
            if str(value).strip() and str(value).strip() != saved:
                out["disagreements"].append({
                    "field": key, "saved": saved, "seen": value,
                    "source": fact.get("source", "") if isinstance(fact, dict) else "",
                })
            return
        out["fields"][key] = {
            "value": value,
            "source": fact.get("source", "") if isinstance(fact, dict) else "",
        }

    offer("name", identity.get("site_name"))
    offer("url", identity.get("website"))
    offer("phone", contact.get("phone"))
    offer("email", contact.get("email"))
    offer("address", location.get("street"))
    offer("city", location.get("city"))
    offer("state", location.get("state"))
    offer("zip", location.get("zip"))

    # geo -- the place id only, per the work order: never fetch coordinates.
    place = location.get("place_id")
    place_value = _fact_value(place)
    if place_value and not str(known.get("google_maps_place_id") or "").strip():
        out["fields"]["google_maps_place_id"] = {
            "value": place_value,
            "source": place.get("source", "") if isinstance(place, dict) else "",
        }

    # image/logo -- prefer a file we hold over one merely seen on their site.
    logos = brand.get("logos") or []
    primary = next((l for l in logos if isinstance(l, dict) and l.get("origin") == "file"), None) \
        or (logos[0] if logos and isinstance(logos[0], dict) else None)
    if primary and primary.get("url") and not str(known.get("logo") or "").strip():
        src = "brand" if primary.get("origin") == "file" else "scan"
        out["fields"]["logo"] = {"value": primary["url"], "source": src}
        out["fields"].setdefault("image", {"value": primary["url"], "source": src})

    # sameAs -- real links the scan actually found, never invented.
    if isinstance(social, dict) and social.get("found") and not str(known.get("social_profiles") or "").strip():
        same_as = [row.get("link") for row in (social.get("platforms") or [])
                  if row.get("link")]
        if same_as:
            out["fields"]["social_profiles"] = {"value": same_as, "source": "scan"}

    # priceRange / openingHours: nothing in this Hub's scan payload carries
    # either today (no field for opening hours, and price_range is asked of
    # a person, never observed) -- left absent rather than invented, the
    # "absent is not measured" rule. If either is already on the record
    # (`known.get("price_range")` / `known.get("hours")`), the caller shows
    # it exactly as saved; there is nothing here to offer over it.

    industry_key = _fact_value(identity.get("industry_key")) or ""
    industry_label = _fact_value(identity.get("industry_label")) or ""
    industry_subtype = _fact_value(identity.get("industry_subtype")) or ""
    out["schema_type"] = schema_type_for(industry_key, industry_subtype, industry_label)

    # category -- offer the resolved schema.org type as the answer to the
    # "single best schema.org type" question, when nothing better is on file.
    if industry_key and not str(known.get("category") or "").strip():
        out["fields"]["category"] = {"value": out["schema_type"], "source": "scan"}

    return out


def build_localbusiness_jsonld(business_info: dict, industry_key: str = "",
                               subtype: str = "", label: str = "") -> dict:
    """A minimal valid LocalBusiness JSON-LD node from *saved* fields only.

    Never reads the brief -- built purely from what is already in
    `business_info`, so it can never disagree with what a person pressed
    Save on. Used by the schema generator's template fallback and by the
    validator this file's own tests check against.
    """
    bi = business_info or {}
    node: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": schema_type_for(industry_key, subtype, label or bi.get("category", "")),
    }
    if bi.get("name"):
        node["name"] = bi["name"]
    if bi.get("phone"):
        node["telephone"] = bi["phone"]
    if bi.get("email"):
        node["email"] = bi["email"]
    if bi.get("url"):
        node["url"] = bi["url"]
    image = bi.get("image") or bi.get("logo")
    if image:
        node["image"] = image
    addr: dict[str, Any] = {}
    if bi.get("address"):
        addr["streetAddress"] = bi["address"]
    if bi.get("city"):
        addr["addressLocality"] = bi["city"]
    if bi.get("state"):
        addr["addressRegion"] = bi["state"]
    if bi.get("zip"):
        addr["postalCode"] = bi["zip"]
    if addr:
        addr["@type"] = "PostalAddress"
        node["address"] = addr
    if bi.get("google_maps_place_id"):
        node["hasMap"] = ("https://www.google.com/maps/place/?q=place_id:"
                          + str(bi["google_maps_place_id"]))
    if bi.get("price_range"):
        node["priceRange"] = bi["price_range"]
    if bi.get("hours"):
        node["openingHours"] = bi["hours"]
    same_as = bi.get("social_profiles")
    if same_as:
        node["sameAs"] = same_as if isinstance(same_as, list) else [same_as]
    return node
