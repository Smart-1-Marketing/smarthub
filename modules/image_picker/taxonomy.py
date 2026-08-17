"""
Industry -> topics / services taxonomy for the Image Picker.

The whole point of this module is that a client should never have to think up a
stock-photo search term. They pick their industry once (usually it is already
set on their client record), then tap a topic or a service, and they get a grid
of photos that actually look like their business.

Each topic/service carries a list of *curated* provider queries. We fan those
out and interleave the results, so a single tap on "Emergency AC repair" pulls
from three or four different phrasings rather than one literal string that would
return the same twelve pictures every provider has.

`negative` terms are soft-filtered out of results by matching against provider
tags/alt text. Stock libraries are full of near-misses (searching "roofing"
returns a lot of thatched cottages), and a client browsing on their phone should
not have to scroll past them.
"""

from __future__ import annotations

import re

from typing import Any

# Terms filtered out of every industry's results. Stock libraries return a lot of
# whiteboard-and-lightbulb filler for any business query.
GLOBAL_NEGATIVE = [
    "lightbulb idea",
    "wooden blocks",
    "jigsaw puzzle piece",
    "clip art",
    "vector illustration",
    "3d render abstract",
]


def _t(key: str, label: str, queries: list[str], negative: list[str] | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "queries": queries, "negative": negative or []}


INDUSTRIES: list[dict[str, Any]] = [
    {
        "key": "hvac",
        "label": "Heating & Air",
        "icon": "🌡️",
        "aliases": ["hvac", "heating", "air conditioning", "furnace", "ac"],
        "topics": [
            _t("summer_heat", "Beat the heat", [
                "hot summer home interior fan",
                "family cool comfortable living room",
                "sun beating down on suburban house",
            ]),
            _t("winter_cold", "Winter comfort", [
                "cozy warm family home winter",
                "snow on suburban house roof",
                "thermostat winter heating home",
            ]),
            _t("emergency", "Emergency call", [
                "hvac technician van arriving house",
                "worried homeowner phone call repair",
                "service truck night emergency",
            ]),
            _t("savings", "Lower bills", [
                "energy efficient home thermostat",
                "utility bill kitchen table savings",
                "smart thermostat wall hand",
            ]),
            _t("trust", "Trusted local crew", [
                "friendly technician homeowner handshake",
                "uniformed service technician smiling",
                "family home exterior suburban trust",
            ]),
        ],
        "services": [
            _t("ac_repair", "AC repair", [
                "air conditioner repair technician outdoor unit",
                "hvac technician gauges refrigerant",
                "condenser unit maintenance hands tools",
            ], negative=["car air conditioning", "automotive"]),
            _t("ac_install", "AC installation", [
                "new air conditioning unit installation home",
                "hvac installers carrying condenser",
                "outdoor ac condenser new install",
            ]),
            _t("furnace", "Furnace & heating", [
                "furnace repair technician basement",
                "gas furnace flame inspection",
                "boiler heating system technician",
            ]),
            _t("ductwork", "Duct cleaning", [
                "air duct cleaning vent technician",
                "hvac ductwork ceiling installation",
                "air vent register wall home",
            ]),
            _t("maintenance", "Tune-up & maintenance", [
                "hvac maintenance checklist technician clipboard",
                "air filter replacement hands home",
                "technician inspecting hvac system",
            ]),
            _t("iaq", "Indoor air quality", [
                "air purifier living room clean home",
                "clean fresh air home interior sunlight",
                "humidifier home indoor air",
            ]),
        ],
    },
    {
        "key": "restaurant",
        "label": "Restaurant & Food",
        "icon": "🍽️",
        "aliases": ["restaurant", "cafe", "bar", "food", "diner", "pizzeria"],
        "topics": [
            _t("happy_hour", "Happy hour", [
                "cocktails bar happy hour friends",
                "craft beer flight bar counter",
                "bartender pouring drink evening",
            ]),
            _t("family_night", "Family night", [
                "family eating dinner restaurant table",
                "kids meal restaurant family smiling",
                "multigenerational family dining out",
            ]),
            _t("date_night", "Date night", [
                "couple dining candlelit restaurant",
                "romantic dinner table wine two",
                "warm restaurant interior evening ambience",
            ]),
            _t("weekend_brunch", "Weekend brunch", [
                "brunch table eggs coffee friends",
                "pancakes mimosa brunch spread",
                "sunny cafe brunch table overhead",
            ]),
            _t("seasonal", "Seasonal specials", [
                "seasonal autumn food plating restaurant",
                "holiday dinner table festive spread",
                "summer patio dining restaurant",
            ]),
            _t("behind_scenes", "Behind the scenes", [
                "chef plating dish kitchen",
                "busy restaurant kitchen line cooks",
                "hands preparing fresh ingredients kitchen",
            ]),
        ],
        "services": [
            _t("dine_in", "Dine in", [
                "restaurant dining room interior warm",
                "server delivering plates to table",
                "full restaurant guests dining",
            ]),
            _t("takeout", "Takeout & delivery", [
                "takeout containers food bag counter",
                "food delivery handoff doorstep",
                "to go order restaurant counter",
            ]),
            _t("catering", "Catering", [
                "catering buffet spread event",
                "catered event food table guests",
                "chef serving catering tray",
            ]),
            _t("private_events", "Private events", [
                "private dining room event restaurant",
                "celebration dinner party long table",
                "banquet hall set tables event",
            ]),
            _t("patio", "Patio & outdoor", [
                "restaurant patio outdoor seating string lights",
                "sidewalk cafe tables sunny",
                "rooftop bar evening city",
            ]),
        ],
    },
    {
        "key": "auto",
        "label": "Auto Dealer & Service",
        "icon": "🚗",
        "aliases": ["auto", "car dealer", "dealership", "automotive", "mechanic"],
        "topics": [
            _t("new_arrival", "New arrivals", [
                "new cars dealership lot rows",
                "showroom car new vehicle",
                "car keys handed over dealership",
            ]),
            _t("family_car", "Family vehicles", [
                "family loading suv kids",
                "family road trip car packed",
                "parents children minivan driveway",
            ]),
            _t("sale_event", "Sales event", [
                "car dealership lot flags sale",
                "salesperson customer car lot",
                "car showroom bright interior",
            ]),
            _t("trust_service", "Service you trust", [
                "mechanic explaining to customer garage",
                "auto technician clipboard vehicle",
                "clean auto service bay",
            ]),
        ],
        "services": [
            _t("sales", "Vehicle sales", [
                "car dealership showroom vehicles",
                "customer test drive car",
                "signing car purchase paperwork",
            ]),
            _t("service_repair", "Service & repair", [
                "auto mechanic working under car",
                "car on lift repair shop",
                "engine bay mechanic tools",
            ]),
            _t("tires", "Tires & alignment", [
                "tire change mechanic garage",
                "new tires stacked shop",
                "wheel alignment machine shop",
            ]),
            _t("body_shop", "Collision & body", [
                "auto body shop paint repair",
                "car dent repair technician",
                "collision repair shop vehicle",
            ]),
            _t("detailing", "Detailing", [
                "car detailing polishing paint",
                "clean car interior detail vacuum",
                "shiny detailed car close up",
            ]),
        ],
    },
    {
        "key": "marine",
        "label": "Boat & Marine",
        "icon": "⛵",
        "aliases": ["boat", "marine", "marina", "yacht", "watercraft"],
        "topics": [
            _t("family_water", "Family on the water", [
                "family boating lake summer day",
                "kids tubing behind boat",
                "family swimming off boat",
            ]),
            _t("sunset", "Sunset cruising", [
                "boat sunset water golden hour",
                "couple boat sunset cruise",
                "marina sunset boats docked",
            ]),
            _t("fishing", "Fishing days", [
                "fishing boat morning water",
                "angler casting from boat",
                "fishing rods boat deck lake",
            ]),
            _t("season_open", "Season kickoff", [
                "boat launch ramp spring",
                "marina busy summer boats",
                "boat show display dock",
            ]),
        ],
        "services": [
            _t("boat_sales", "Boat sales", [
                "boat dealership showroom boats",
                "new boat on trailer dealership",
                "pontoon boat dock display",
            ]),
            _t("service", "Marine service", [
                "boat engine mechanic repair",
                "outboard motor service technician",
                "boat hull maintenance work",
            ]),
            _t("storage", "Storage & winterization", [
                "boats stored winter shrink wrap",
                "boat storage facility racks",
                "boat covered winter marina",
            ]),
            _t("rentals", "Rentals & charters", [
                "boat rental dock customers",
                "charter boat group day trip",
                "jet ski rental water",
            ]),
        ],
    },
    {
        "key": "rv",
        "label": "RV & Powersports",
        "icon": "🚐",
        "aliases": ["rv", "camper", "motorhome", "trailer", "powersports"],
        "topics": [
            _t("open_road", "The open road", [
                "rv driving scenic highway mountains",
                "motorhome road trip desert road",
                "camper van winding road sunset",
            ]),
            _t("campfire", "Campfire nights", [
                "family campfire rv campsite evening",
                "camping chairs fire rv night",
                "rv awning lights campsite dusk",
            ]),
            _t("family_trip", "Family trips", [
                "family rv camping kids outdoors",
                "family cooking outside camper",
                "kids playing rv campsite",
            ]),
            _t("upgrade", "Time to upgrade", [
                "rv dealership lot rows campers",
                "couple touring rv interior",
                "new travel trailer showroom",
            ]),
        ],
        "services": [
            _t("rv_sales", "RV sales", [
                "rv dealership lot motorhomes",
                "travel trailer showroom interior",
                "customer touring rv salesperson",
            ]),
            _t("rv_service", "RV service", [
                "rv repair technician working",
                "rv service bay maintenance",
                "camper roof sealant repair",
            ]),
            _t("parts", "Parts & accessories", [
                "rv accessories store shelves",
                "camping gear rv supplies",
                "rv hookup power connection",
            ]),
            _t("rv_rentals", "Rentals", [
                "rv rental pickup customers",
                "camper van rental road trip",
                "motorhome keys handover",
            ]),
        ],
    },
    {
        "key": "legal",
        "label": "Legal",
        "icon": "⚖️",
        "aliases": ["legal", "attorney", "law firm", "lawyer"],
        "topics": [
            _t("consult", "Free consultation", [
                "attorney meeting client office desk",
                "lawyer listening client consultation",
                "handshake law office meeting",
            ]),
            _t("advocacy", "Someone in your corner", [
                "attorney client supportive conversation",
                "lawyer reassuring client office",
                "legal team discussing case",
            ]),
            _t("results", "Proven results", [
                "courthouse steps exterior columns",
                "law books gavel desk",
                "attorney courtroom professional",
            ]),
            _t("local", "Local & approachable", [
                "law office reception welcoming",
                "professional attorney portrait office",
                "small law firm team office",
            ]),
        ],
        "services": [
            _t("personal_injury", "Personal injury", [
                "car accident scene road damage",
                "injured person medical consultation",
                "insurance claim paperwork accident",
            ], negative=["gore", "blood"]),
            _t("family_law", "Family law", [
                "family law mediation meeting",
                "parent child hands support",
                "divorce paperwork desk pen",
            ]),
            _t("estate", "Estate & wills", [
                "will estate document signing pen",
                "senior couple meeting advisor",
                "family legacy home paperwork",
            ]),
            _t("criminal", "Criminal defense", [
                "courthouse exterior justice building",
                "attorney desk case files",
                "legal consultation serious meeting",
            ]),
            _t("business_law", "Business law", [
                "business contract signing meeting",
                "corporate attorney boardroom",
                "legal documents office desk",
            ]),
            _t("immigration", "Immigration", [
                "immigration paperwork passport documents",
                "family citizenship celebration",
                "consultation office multicultural clients",
            ]),
        ],
    },
    {
        "key": "home_services",
        "label": "Home Services",
        "icon": "🔧",
        "aliases": ["plumbing", "electrical", "roofing", "handyman", "contractor", "home services"],
        "topics": [
            _t("emergency", "Emergency service", [
                "service van arriving house urgent",
                "homeowner phone call plumber",
                "water leak emergency home",
            ]),
            _t("curb_appeal", "Curb appeal", [
                "beautiful suburban home exterior",
                "well maintained house front yard",
                "new roof clean house exterior",
            ]),
            _t("trust", "Licensed & insured", [
                "uniformed contractor homeowner doorway",
                "technician id badge friendly",
                "contractor shaking hands homeowner",
            ]),
            _t("seasonal_prep", "Seasonal prep", [
                "gutter cleaning autumn leaves home",
                "winterizing home exterior pipes",
                "spring home maintenance yard",
            ]),
        ],
        "services": [
            _t("plumbing", "Plumbing", [
                "plumber fixing sink pipe wrench",
                "plumber under kitchen sink",
                "water heater installation plumber",
            ]),
            _t("electrical", "Electrical", [
                "electrician working electrical panel",
                "electrician wiring outlet home",
                "electrical panel breaker box hands",
            ]),
            _t("roofing", "Roofing", [
                "roofer installing shingles roof",
                "roof replacement crew house",
                "new asphalt shingle roof house",
            ], negative=["thatched", "cottage roof straw"]),
            _t("remodel", "Remodeling", [
                "kitchen remodel construction progress",
                "bathroom renovation contractor",
                "home renovation tools drywall",
            ]),
            _t("landscaping", "Landscaping & lawn", [
                "landscaper mowing green lawn",
                "landscaped front yard garden home",
                "lawn care crew equipment",
            ]),
            _t("pest", "Pest control", [
                "pest control technician spraying home",
                "exterminator uniform house exterior",
                "pest inspection technician crawlspace",
            ], negative=["insect macro closeup"]),
        ],
    },
    {
        "key": "medical",
        "label": "Medical & Dental",
        "icon": "🩺",
        "aliases": ["medical", "dental", "dentist", "clinic", "healthcare", "chiropractic"],
        "topics": [
            _t("new_patients", "New patients welcome", [
                "friendly clinic reception welcoming patient",
                "doctor greeting patient handshake",
                "modern clinic waiting room bright",
            ]),
            _t("family_care", "Family care", [
                "pediatrician child checkup smiling",
                "family doctor visit parent child",
                "multigenerational family health",
            ]),
            _t("confidence", "Confidence & comfort", [
                "patient smiling after treatment",
                "confident smile portrait natural",
                "relaxed patient dental chair",
            ]),
            _t("technology", "Modern technology", [
                "modern medical equipment clinic",
                "digital dental scanner technology",
                "clean modern exam room",
            ]),
        ],
        "services": [
            _t("general_dentistry", "General dentistry", [
                "dentist examining patient teeth",
                "dental hygienist cleaning patient",
                "dental office chair modern",
            ]),
            _t("cosmetic", "Cosmetic & whitening", [
                "bright white smile close up",
                "cosmetic dentistry before after smile",
                "confident smiling person portrait",
            ]),
            _t("ortho", "Orthodontics", [
                "teen braces smiling orthodontist",
                "clear aligners hands dental",
                "orthodontist adjusting braces patient",
            ]),
            _t("urgent_care", "Urgent care", [
                "urgent care clinic entrance",
                "nurse treating patient clinic",
                "medical exam room doctor patient",
            ]),
            _t("chiro_pt", "Chiropractic & PT", [
                "chiropractor adjusting patient back",
                "physical therapist assisting patient exercise",
                "rehabilitation therapy session clinic",
            ]),
        ],
    },
    {
        "key": "fitness",
        "label": "Fitness & Wellness",
        "icon": "💪",
        "aliases": ["gym", "fitness", "yoga", "wellness", "spa", "salon"],
        "topics": [
            _t("new_year", "Fresh start", [
                "person starting workout gym determined",
                "morning run sunrise motivation",
                "new gym member first day",
            ]),
            _t("community", "Community", [
                "group fitness class smiling",
                "gym community high five",
                "yoga class group studio",
            ]),
            _t("transformation", "Results", [
                "personal trainer coaching client",
                "strong athlete lifting weights",
                "fitness progress measuring",
            ]),
            _t("selfcare", "Self care", [
                "spa relaxation treatment calm",
                "massage therapy peaceful room",
                "wellness meditation calm space",
            ]),
        ],
        "services": [
            _t("membership", "Gym membership", [
                "modern gym interior equipment",
                "person using gym equipment",
                "gym front desk member checkin",
            ]),
            _t("personal_training", "Personal training", [
                "personal trainer client one on one",
                "trainer coaching form weights",
                "trainer stopwatch client workout",
            ]),
            _t("classes", "Group classes", [
                "spin class studio group",
                "yoga class instructor studio",
                "hiit class group training",
            ]),
            _t("salon_spa", "Salon & spa", [
                "hair salon stylist client chair",
                "spa treatment room candles",
                "manicure salon hands treatment",
            ]),
        ],
    },
    {
        "key": "real_estate",
        "label": "Real Estate",
        "icon": "🏡",
        "aliases": ["real estate", "realtor", "mortgage", "property", "broker"],
        "topics": [
            _t("just_listed", "Just listed", [
                "for sale sign front yard house",
                "beautiful home exterior listing",
                "modern home curb appeal daytime",
            ]),
            _t("just_sold", "Just sold", [
                "sold sign house front yard",
                "couple keys new home celebration",
                "family moving into new house",
            ]),
            _t("first_home", "First-time buyers", [
                "young couple touring home",
                "first time homebuyers keys",
                "couple moving boxes new home",
            ]),
            _t("market_update", "Market update", [
                "neighborhood aerial suburban homes",
                "city skyline residential buildings",
                "housing market charts desk",
            ]),
        ],
        "services": [
            _t("buying", "Buying", [
                "realtor showing home to buyers",
                "open house tour interior",
                "home tour kitchen buyers",
            ]),
            _t("selling", "Selling", [
                "staged living room home for sale",
                "realtor photographing home listing",
                "home staging bright interior",
            ]),
            _t("mortgage", "Mortgage & lending", [
                "mortgage paperwork signing desk",
                "loan officer meeting clients",
                "calculator home loan documents",
            ]),
            _t("commercial", "Commercial property", [
                "commercial office building exterior",
                "retail storefront property",
                "warehouse industrial property exterior",
            ]),
        ],
    },
    {
        "key": "recruiting",
        "label": "Recruiting & Staffing",
        "icon": "👔",
        "aliases": ["recruiting", "staffing", "hiring", "hr", "talent"],
        "topics": [
            _t("now_hiring", "Now hiring", [
                "now hiring sign business window",
                "diverse team working together office",
                "job interview handshake welcome",
            ]),
            _t("career_growth", "Career growth", [
                "professional celebrating promotion office",
                "mentor coaching employee",
                "team meeting collaboration bright office",
            ]),
            _t("culture", "Great place to work", [
                "happy diverse coworkers laughing office",
                "team lunch break office culture",
                "coworkers collaborating whiteboard",
            ]),
            _t("skilled_trades", "Skilled trades", [
                "welder workshop skilled trade",
                "construction worker site portrait",
                "warehouse worker forklift operating",
            ]),
        ],
        "services": [
            _t("direct_hire", "Direct hire", [
                "job interview office professional",
                "resume review desk hiring manager",
                "new employee onboarding welcome",
            ]),
            _t("temp", "Temporary staffing", [
                "warehouse team working shift",
                "temporary staff working retail",
                "workers clocking in shift",
            ]),
            _t("healthcare_staffing", "Healthcare staffing", [
                "nurse hospital corridor smiling",
                "medical staff team hospital",
                "caregiver assisting patient",
            ]),
            _t("industrial", "Industrial & warehouse", [
                "warehouse workers logistics",
                "manufacturing plant workers line",
                "forklift warehouse operator",
            ]),
        ],
    },
    {
        "key": "tourism",
        "label": "Tourism & Attractions",
        "icon": "🎡",
        "aliases": ["tourism", "attraction", "museum", "resort", "hotel", "ski", "winery"],
        "topics": [
            _t("family_day", "Family day out", [
                "family enjoying attraction day out",
                "kids laughing outdoor activity",
                "family exploring together outdoors",
            ]),
            _t("seasonal_event", "Seasonal event", [
                "autumn festival pumpkins family",
                "holiday lights winter event",
                "summer festival crowd outdoor",
            ]),
            _t("group_visits", "Groups & field trips", [
                "school group tour museum",
                "tour group guide outdoors",
                "group activity outdoor adventure",
            ]),
            _t("weekend_getaway", "Weekend getaway", [
                "couple weekend getaway scenic",
                "resort pool relaxing guests",
                "scenic overlook travelers viewpoint",
            ]),
        ],
        "services": [
            _t("tickets", "Tickets & admission", [
                "ticket booth entrance attraction",
                "visitors entering attraction gate",
                "wristband admission event",
            ]),
            _t("lodging", "Lodging", [
                "hotel room bright comfortable",
                "resort lobby welcoming",
                "cabin lodge exterior scenic",
            ]),
            _t("outdoor_rec", "Outdoor recreation", [
                "hiking trail scenic forest",
                "kayaking lake summer",
                "zipline adventure forest",
            ]),
            _t("winter_sports", "Winter sports", [
                "skiing slope mountain sunny",
                "snowboarder powder mountain",
                "ski lodge snow exterior",
            ]),
            _t("events_venue", "Events & weddings", [
                "wedding venue outdoor setup",
                "event venue decorated tables",
                "celebration venue string lights",
            ]),
        ],
    },
    {
        "key": "professional",
        "label": "Professional Services",
        "icon": "📊",
        "aliases": ["accounting", "cpa", "insurance", "financial", "consulting", "bookkeeping"],
        "topics": [
            _t("tax_season", "Tax season", [
                "tax paperwork calculator desk",
                "accountant meeting client documents",
                "tax filing laptop desk organized",
            ]),
            _t("planning", "Planning ahead", [
                "financial advisor couple meeting",
                "retirement planning documents couple",
                "charts planning meeting desk",
            ]),
            _t("peace_of_mind", "Peace of mind", [
                "family home insurance protection concept",
                "relaxed couple reviewing finances",
                "advisor reassuring client meeting",
            ]),
            _t("small_business", "For small business", [
                "small business owner shop counter",
                "entrepreneur working laptop shop",
                "local business owner portrait",
            ]),
        ],
        "services": [
            _t("accounting", "Accounting & tax", [
                "accountant working spreadsheets desk",
                "bookkeeping receipts calculator",
                "tax preparation office meeting",
            ]),
            _t("insurance", "Insurance", [
                "insurance agent client meeting home",
                "insurance policy documents signing",
                "family protection insurance concept",
            ]),
            _t("financial_planning", "Financial planning", [
                "financial planning charts meeting",
                "investment advisor client discussion",
                "retirement savings planning couple",
            ]),
            _t("consulting", "Consulting", [
                "business consultant presenting team",
                "strategy meeting whiteboard professionals",
                "consultant client discussion office",
            ]),
        ],
    },
    {
        "key": "retail",
        "label": "Retail & E-commerce",
        "icon": "🛍️",
        "aliases": ["retail", "shop", "store", "boutique", "ecommerce"],
        "topics": [
            _t("new_arrivals", "New arrivals", [
                "boutique storefront display new",
                "retail shelves styled products",
                "shopper browsing boutique rack",
            ]),
            _t("sale", "Sale & promotions", [
                "sale sign shop window",
                "shopping bags hands store",
                "retail checkout counter customer",
            ]),
            _t("shop_local", "Shop local", [
                "local main street shops daytime",
                "small shop owner storefront",
                "farmers market local vendor",
            ]),
            _t("holiday", "Holiday shopping", [
                "holiday shopping bags festive street",
                "gift wrapping counter holiday",
                "festive shop window display",
            ]),
        ],
        "services": [
            _t("in_store", "In-store", [
                "retail store interior displays",
                "customer shopping browsing store",
                "store associate helping customer",
            ]),
            _t("online", "Online orders", [
                "online shopping laptop credit card",
                "packing orders small business shipping",
                "delivery package doorstep",
            ]),
            _t("curbside", "Curbside pickup", [
                "curbside pickup order handoff car",
                "store pickup counter order",
                "employee loading order into car",
            ]),
        ],
    },
    {
        "key": "general",
        "label": "General Business",
        "icon": "🏢",
        "aliases": ["general", "business", "other", "b2b"],
        "topics": [
            _t("team", "Our team", [
                "diverse professional team portrait office",
                "coworkers collaborating meeting",
                "team standing together business",
            ]),
            _t("customer_service", "Customer service", [
                "friendly customer service representative headset",
                "employee helping customer counter",
                "support team office smiling",
            ]),
            _t("local_business", "Local business", [
                "local business storefront street",
                "small business owner working",
                "main street small town businesses",
            ]),
            _t("growth", "Growth & results", [
                "business growth meeting charts",
                "team celebrating success office",
                "professional presenting results",
            ]),
        ],
        "services": [
            _t("consultation", "Free consultation", [
                "consultation meeting two people desk",
                "phone consultation professional office",
                "video call meeting professional",
            ]),
            _t("estimates", "Free estimates", [
                "contractor estimate clipboard customer",
                "quote paperwork handshake",
                "professional measuring giving quote",
            ]),
            _t("office", "Our location", [
                "modern office reception exterior",
                "business storefront entrance sign",
                "welcoming office interior bright",
            ]),
        ],
    },
]

INDUSTRY_BY_KEY = {i["key"]: i for i in INDUSTRIES}


def industry(key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    return INDUSTRY_BY_KEY.get(str(key).strip().lower())


def guess_industry(text: str | None) -> str:
    """Best-effort map of a free-text industry string onto an industry key.

    Client records carry industry as whatever someone typed into Knack, so this
    has to cope with "HVAC Contractor", "Heating & Cooling", "Boat Dealership".

    Two rules, both learned from getting it wrong:

    * Match on **word boundaries**. Plain substring matching made "Roofing
      Contractor" an HVAC client, because "ac" is inside "contractor".
    * When several aliases match, the **earliest** one wins. English puts the
      specific noun first, so "Boat Dealership" is a boat business and
      "Auto Dealership" is a car business, even though both contain "dealership".
      Longer alias breaks a positional tie.

    Falls back to `general`, which is a usable picker rather than an error.
    """
    if not text:
        return "general"
    t = str(text).strip().lower()
    if not t:
        return "general"

    for ind in INDUSTRIES:
        if t == ind["key"] or t == ind["label"].lower():
            return ind["key"]

    best: tuple[int, int, str] | None = None   # (position, -len, key)
    for ind in INDUSTRIES:
        for alias in ind["aliases"]:
            m = re.search(r"\b" + re.escape(alias) + r"\b", t)
            if not m:
                continue
            candidate = (m.start(), -len(alias), ind["key"])
            if best is None or candidate < best:
                best = candidate
    return best[2] if best else "general"


def collection(industry_key: str, kind: str, coll_key: str) -> dict[str, Any] | None:
    """Look up one topic or service inside an industry. `kind` is topics|services."""
    ind = industry(industry_key)
    if not ind or kind not in ("topics", "services"):
        return None
    for c in ind[kind]:
        if c["key"] == coll_key:
            return c
    return None


def public_industries() -> list[dict[str, Any]]:
    """Taxonomy shaped for the browser: no query strings leak to the client.

    The curated queries are the part of this tool that took work; there is no
    reason to ship them to the page where they would be copied or scraped.
    """
    out = []
    for ind in INDUSTRIES:
        out.append({
            "key": ind["key"],
            "label": ind["label"],
            "icon": ind["icon"],
            "topics": [{"key": c["key"], "label": c["label"]} for c in ind["topics"]],
            "services": [{"key": c["key"], "label": c["label"]} for c in ind["services"]],
        })
    return out
