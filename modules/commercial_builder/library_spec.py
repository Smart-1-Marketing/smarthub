"""What a spot IS, what a category needs, and how those reach the model.

Data and arithmetic, no Flask, beside `review_spec.py`, `compliance_spec.py`
and `services/abcd_service.py`. The Brief screen, the concept prompt, the
stock queries, the QC readiness check and the test all read one description.

## The field that was two questions

`config.COMMERCIAL_TYPES` is a single-select mixing two unrelated decisions:

    stock_vo, ai_spokesperson, ai_spokesperson_stock   <- HOW it gets made
    promo_sale, product_spotlight, testimonial,        <- WHAT it is
    weather_triggered, seasonal

So "an AI spokesperson testimonial" is unsayable — picking the production
method silently discards the narrative, and picking the narrative silently
discards the method. Whichever a rep chose, the concept prompt was told half
of what they had decided.

**`commercial_type` keeps its meaning and its column.** `create_all()` adds no
column to an existing table, so splitting it in place would mean a
`production_method` that exists on every local SQLite run and is silently
absent on the live Postgres. It is also read by `compliance_spec` (a
`testimonial` engages 16 CFR 255) and by `openai_service`, and renaming it
would orphan every project already saved.

The archetype lives in the **brief JSON** instead, and `LEGACY_ARCHETYPE` maps
the five narrative values onto it — so a project saved before this reads as
the archetype it always was, and nothing is migrated. The `from_legacy()`
pattern `hub/target_areas.py` uses for the old single-geo fields.

## Twelve archetypes, and each one says what it costs

An archetype is not a label on a dropdown; it is a promise about what the
client has to supply. A testimonial needs a real customer who will say a real
thing on camera. A before-and-after needs the before, which nobody photographs
because at the time it was just a Tuesday. Naming that on the screen where the
spot is chosen is the entire value — discovering it at the shoot is a launch
date nobody hits, which is the failure `hub/creative_needs.py` exists to stop
one medium earlier.

Each also names which published rules it tends to engage, read by nothing
here — `compliance_spec.py` scans the finished copy and is the authority — but
printed while the archetype is being picked, because "a testimonial brings the
FTC's endorsement guides with it" is worth knowing before the script exists
rather than after.

## Twelve packs, keyed on the industries the Hub already has

A pack is **creative** data: what a hook sounds like in this category, what
proof looks like, the words a stock search actually needs, the shape of the
offer, what falls flat. That is different data from `hub/industries.py`, which
carries the **media plan** — tiers, channels, seasonal triggers — and is read
by the Proposal Builder.

Different data, same clients. So where an industry exists in both, it is the
**same id**, and `test_commercial_library.py` asserts it: two taxonomies for
one client is how the same business gets described two ways depending on which
tool somebody opened, which is the year the two proposal builders cost.

**An industry with no pack is generic, and says so.** Guessing a pack from a
name nobody matched would put a restaurant's vocabulary on a machine shop, and
a wrong pack is worse than none because it reads as research. `pack_for()` is
tri-state: matched, generic-because-unmatched, or generic-because-nothing-was
-recorded.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# How it gets made. Derived from `commercial_type`, which stays exactly as it
# is — this is a reading of that column, not a replacement for it.
# ---------------------------------------------------------------------------
PRODUCTION_METHODS = {
    "stock_vo": {
        "label": "Stock footage and voiceover",
        "note": "No shoot and no spokesperson. The fastest route, and the one "
                "most of these take.",
    },
    "ai_spokesperson": {
        "label": "AI spokesperson",
        "note": "A rendered presenter delivers the script to camera.",
    },
    "ai_spokesperson_stock": {
        "label": "AI spokesperson over stock",
        "note": "A presenter opens and closes; stock carries the middle.",
    },
}

# Everything else in COMMERCIAL_TYPES is a narrative choice rather than a
# method, and defaults the production method to stock footage — which is what
# those spots were being built as anyway.
DEFAULT_METHOD = "stock_vo"


def production_method(commercial_type):
    """How a spot gets made, from the column that holds both answers.

    Returns the method key. A narrative value (`testimonial`, `promo_sale`)
    resolves to the default rather than to nothing, because that is what those
    projects have always actually been.
    """
    key = str(commercial_type or "")
    return key if key in PRODUCTION_METHODS else DEFAULT_METHOD


# ---------------------------------------------------------------------------
# What the spot IS. Twelve, and each one names what it needs from the client.
# ---------------------------------------------------------------------------
# `needs` is the load-bearing field: `readiness()` turns it into findings on
# the Blueprint, so an archetype nobody can supply is caught while it is still
# free to change rather than at the shoot.
ARCHETYPES = {
    "problem_solution": {
        "label": "Problem → solution",
        "what": "Open on the thing that is going wrong, then be the answer.",
        "best_for": "Services somebody calls in an emergency, and anything "
                    "whose value is obvious the moment the problem is named.",
        "beat_emphasis": "The hook carries the problem; the middle is the fix.",
        "needs": [],
        "bad_at": "A category where the problem is boring or embarrassing to "
                  "show. Nobody watches their own leak twice.",
        "engages": [],
        "stock": ["frustrated homeowner", "technician arriving", "problem solved"],
    },
    "offer_led": {
        "label": "Offer-led",
        "what": "The offer is the whole spot. Say it, prove it, repeat it.",
        "best_for": "A dated promotion, a seasonal sale, a limited allocation.",
        "beat_emphasis": "The offer lands in the first three seconds and again "
                         "on the end card.",
        "needs": [
            {"key": "offer", "question": "What exactly is the offer, in the "
                                         "client's own words?",
             "why": "An offer-led spot with a vague offer is a brand spot that "
                    "sounds like a sale."},
        ],
        "bad_at": "Building a brand. It buys this month and nothing after it.",
        # Named here so it is known while the archetype is picked, not after
        # the script exists. compliance_spec.py is the authority on the copy.
        "engages": ["reg_z"],
        "stock": ["storefront", "product close-up", "hands exchanging"],
    },
    "testimonial": {
        "label": "Testimonial",
        "what": "A real customer says the thing we would otherwise have to "
                "claim ourselves.",
        "best_for": "High-consideration purchases, and any category where the "
                    "buyer's real question is whether to trust anybody.",
        "beat_emphasis": "One person, one story, no narrator arguing over them.",
        "needs": [
            {"key": "testimonial_source", "question": "Which customer, and have "
                                                      "they agreed?",
             "why": "A testimonial nobody has actually secured is a shoot that "
                    "does not happen and a launch date that moves."},
        ],
        "bad_at": "Speed. Securing a real customer is the long pole, every time.",
        "engages": ["ftc_endorsements"],
        "stock": ["person speaking to camera", "candid interview", "workplace"],
    },
    "founder_story": {
        "label": "Founder or owner story",
        "what": "The person who started it says why, on camera.",
        "best_for": "Family businesses, long-established local firms, anywhere "
                    "the owner IS the brand.",
        "beat_emphasis": "One voice throughout. A narrator over an owner's "
                         "story is two people telling it.",
        "needs": [
            {"key": "founder_available", "question": "Will the owner appear, "
                                                     "and can they be recorded?",
             "why": "This archetype has no fallback: without the founder it is "
                    "a different spot, not a smaller one."},
        ],
        "bad_at": "A franchise or a multi-location client, where the owner is "
                  "not who the customer meets.",
        "engages": [],
        "stock": ["owner in the workplace", "hands at work", "team at work"],
    },
    "demonstration": {
        "label": "Demonstration",
        "what": "Show the thing working. No argument required.",
        "best_for": "Products with a visible mechanism, and services whose "
                    "process is the reassurance.",
        "beat_emphasis": "The middle is the longest beat — the demonstration "
                         "needs room to be believed.",
        "needs": [
            {"key": "demo_footage", "question": "Do we have footage of the "
                                                "actual product or process?",
             "why": "Stock footage of somebody else's product is the one thing "
                    "a demonstration cannot fake."},
        ],
        "bad_at": "Anything that happens over weeks, or inside a computer.",
        "engages": [],
        "stock": ["product in use", "process close-up", "step by step"],
    },
    "before_after": {
        "label": "Before and after",
        "what": "Two frames do the entire argument.",
        "best_for": "Home services, restoration, landscaping, anything "
                    "cosmetic.",
        "beat_emphasis": "The reveal is the hook, not the payoff — a feed and a "
                         "TV pod both leave before a slow build.",
        "needs": [
            {"key": "before_footage", "question": "Do we have the BEFORE?",
             "why": "Nobody photographs the before, because at the time it was "
                    "just a Tuesday. This is the commonest reason this "
                    "archetype gets abandoned two weeks in."},
        ],
        "bad_at": "A service whose result is invisible — insurance, "
                  "accountancy, most professional services.",
        "engages": [],
        "stock": ["before and after", "transformation", "renovation"],
    },
    "vignette": {
        "label": "Day in the life",
        "what": "A short scene the viewer recognizes, with the brand inside it.",
        "best_for": "Brand building, and categories where the purchase is "
                    "emotional rather than reasoned.",
        "beat_emphasis": "Realistic and relatable settings — the element Amazon "
                         "measured the largest lift on.",
        "needs": [],
        "bad_at": "Selling a dated offer. It builds warmth and does not close.",
        "engages": [],
        "stock": ["family at home", "morning routine", "local neighborhood"],
    },
    "comparison": {
        "label": "Us versus the alternative",
        "what": "Name what the buyer is choosing between, and win it.",
        "best_for": "Crowded categories where the client is genuinely better on "
                    "something specific.",
        "beat_emphasis": "One point of difference, held long enough to land. "
                         "Three is a list nobody remembers.",
        "needs": [
            {"key": "comparison_basis", "question": "What is the specific claim, "
                                                    "and what substantiates it?",
             "why": "A comparative claim is the one a competitor complains "
                    "about, and it has to be supportable before it is written."},
        ],
        "bad_at": "A market leader. Naming a rival gives them the airtime.",
        "engages": ["ftc_endorsements"],
        "stock": ["side by side", "choosing", "comparison chart"],
    },
    "local_pride": {
        "label": "Local pride",
        "what": "We are from here, and here is what that means.",
        "best_for": "Independents against national chains, and any client whose "
                    "competition is a call center in another state.",
        "beat_emphasis": "Named places and real landmarks. A generic Main "
                         "Street reads as stock, because it is.",
        "needs": [
            {"key": "local_specifics", "question": "Which town, and what "
                                                   "landmarks may we name?",
             "why": "This archetype is worth nothing generic — the whole claim "
                    "is specificity."},
        ],
        "bad_at": "A regional or national buy, where the specificity that makes "
                  "it work in one market makes it irrelevant in the rest.",
        "engages": [],
        "stock": ["local landmark", "small town main street", "community event"],
    },
    "seasonal_urgency": {
        "label": "Seasonal urgency",
        "what": "The window is closing, and the window is real.",
        "best_for": "Weather-driven trades, seasonal inventory, anything with a "
                    "genuine deadline.",
        "beat_emphasis": "The deadline is stated twice — once spoken, once on "
                         "screen, because half the audience is muted.",
        "needs": [
            {"key": "deadline", "question": "What is the actual date, and is it "
                                            "real?",
             "why": "An invented deadline is the claim a client gets called on, "
                    "and a spot that outlives its own date runs as a lie."},
        ],
        "bad_at": "An always-on campaign. It expires, and somebody has to "
                  "remember to pull it.",
        "engages": ["reg_z"],
        "stock": ["seasonal weather", "calendar", "busy season"],
    },
    "category_education": {
        "label": "What most people do not know",
        "what": "Teach one thing, and be the people who knew it.",
        "best_for": "Considered purchases, regulated categories, and anywhere "
                    "the buyer is nervous rather than uninterested.",
        "beat_emphasis": "One fact. A spot that teaches three teaches none.",
        "needs": [
            {"key": "education_fact", "question": "What is the one fact, and "
                                                  "where does it come from?",
             "why": "An educational spot is only as good as the fact, and a "
                    "wrong one is the thing a competitor screenshots."},
        ],
        "bad_at": "Impulse categories, and any spot under fifteen seconds.",
        "engages": [],
        "stock": ["explaining", "diagram", "expert at work"],
    },
    "recruitment": {
        "label": "We are hiring",
        "what": "The audience is a future employee, not a customer.",
        "best_for": "Trades and shift work, where the hiring problem is bigger "
                    "than the sales problem.",
        "beat_emphasis": "Pay, shift and location early — that is what a "
                         "candidate is actually scanning for.",
        "needs": [
            {"key": "roles", "question": "Which roles, and what may we say about "
                                         "pay?",
             "why": "A recruitment spot with no role and no number is a brand "
                    "spot aimed at the wrong audience."},
        ],
        "bad_at": "Doing double duty. A spot that recruits and sells does "
                  "neither, and the media plan targets two different people.",
        "engages": [],
        "stock": ["team at work", "hiring", "workplace culture"],
    },
}

# The five narrative values of `commercial_type`, read as the archetype they
# always were. A project saved before this existed reads correctly and nothing
# is migrated -- `hub/target_areas.from_legacy()`'s rule.
LEGACY_ARCHETYPE = {
    "promo_sale": "offer_led",
    "product_spotlight": "demonstration",
    "testimonial": "testimonial",
    "weather_triggered": "seasonal_urgency",
    "seasonal": "seasonal_urgency",
}

DEFAULT_ARCHETYPE = "problem_solution"

# Every brief field any archetype asks for. Derived rather than typed out, so
# an archetype that gains a `need` next month is saved by the route without
# anybody remembering to widen a list — the failure `TICKET_CREATE_FIELDS`
# describes, where a field is pinned, writable, and on no screen.
NEED_KEYS = tuple(sorted({need["key"]
                          for spec in ARCHETYPES.values()
                          for need in spec.get("needs", [])}))


def archetype_for(brief=None, commercial_type=""):
    """Which archetype this spot is, and where that answer came from.

    Returns `(key, source)` where source is `chosen`, `legacy` or `default`.
    Named rather than collapsed, because "a rep picked this" and "we inferred
    it from a column that meant two things" are different confidences, and the
    screen says which.
    """
    chosen = str((brief or {}).get("archetype") or "")
    if chosen in ARCHETYPES:
        return chosen, "chosen"
    legacy = LEGACY_ARCHETYPE.get(str(commercial_type or ""))
    if legacy:
        return legacy, "legacy"
    return DEFAULT_ARCHETYPE, "default"


# ---------------------------------------------------------------------------
# What a category needs. Keyed on `hub/industries.py`'s ids where they overlap.
# ---------------------------------------------------------------------------
# `SHARED_WITH_HUB` is the set that must match exactly; the rest are categories
# the Commercial Builder sees and the Proposal Builder has no page for.
SHARED_WITH_HUB = ("tourism", "boat", "legal", "recruit", "restaurant", "rv",
                   "ski", "stadium")

INDUSTRY_PACKS = {
    "hvac": {
        "label": "Heating and cooling",
        "match": ("hvac", "heating", "cooling", "air conditioning", "furnace",
                  "plumbing", "heat pump"),
        "hooks": ["The day it stops working is the day it is 96 degrees.",
                  "Nobody thinks about the furnace until January."],
        "proof": "Response time, licensed technicians, how long they have been "
                 "in the county.",
        "offers": ["A tune-up at a named price before a dated deadline",
                   "Financing on a replacement system",
                   "No overtime charge on nights and weekends"],
        "cta": "A phone number, held long enough to dial. This is a category "
               "where people call rather than click.",
        "stock": ["hvac technician", "air conditioner unit", "furnace repair",
                  "thermostat", "uncomfortable hot house"],
        "avoid": "Stock of a technician in an immaculate white shirt. It reads "
                 "as an ad and not as a trade.",
        "archetypes": ["problem_solution", "seasonal_urgency", "offer_led"],
    },
    "solar": {
        "label": "Solar and home energy",
        "match": ("solar", "photovoltaic", "renewable energy", "home energy"),
        "hooks": ["Your bill goes up every year whether you use more or not.",
                  "The roof is already there."],
        "proof": "Installed local systems, the warranty, what the payback "
                 "period actually is.",
        "offers": ["A free assessment of the roof",
                   "Financing with a monthly figure",
                   "A dated incentive window"],
        "cta": "A booked assessment. The sale is long, so the ask is a visit.",
        "stock": ["solar panels roof", "installer at work", "family home "
                  "exterior", "electricity bill"],
        "avoid": "Promising a saving figure. The number depends on the house, "
                 "and a spot that states one is a claim the client owns.",
        # Financing copy here is the ordinary case, not the exception.
        "archetypes": ["category_education", "offer_led", "testimonial"],
    },
    "legal": {
        "label": "Law firms",
        "match": ("law", "lawyer", "attorney", "legal", "litigation"),
        "hooks": ["The insurance company has had lawyers since the day it "
                  "opened.",
                  "Most people call us after they have already given a "
                  "statement."],
        "proof": "Years in practice, the county courts they appear in, the "
                 "named lawyer who takes the call.",
        "offers": ["A free consultation",
                   "No fee unless we recover"],
        "cta": "A phone number and a named person. Anonymous firms do not get "
               "called.",
        "stock": ["law office", "handshake consultation", "courthouse exterior",
                  "person on the phone worried"],
        "avoid": "Gavels and scales of justice. Every firm in the market has "
                 "used them, so the spot looks like all the others.",
        "archetypes": ["category_education", "testimonial", "local_pride"],
    },
    "restaurant": {
        "label": "Restaurants and food service",
        "match": ("restaurant", "food", "dining", "cafe", "bar and grill",
                  "pizzeria", "catering"),
        "hooks": ["Food on screen, in the first second, filling the frame.",
                  "The thing they are known for, not the whole menu."],
        "proof": "The dish itself, the room when it is full, how long they "
                 "have been on that corner.",
        "offers": ["A daypart deal on a named day",
                   "A new dish or a seasonal menu",
                   "Something for the first visit"],
        "cta": "Directions or a booking. Nobody memorizes a restaurant's "
               "phone number.",
        "stock": ["food close up", "restaurant interior busy", "chef plating",
                  "friends eating together"],
        "avoid": "A menu montage. Eleven dishes in four seconds sells none of "
                 "them, and the one they came for went past too fast.",
        "archetypes": ["vignette", "offer_led", "local_pride"],
    },
    "boat": {
        "label": "Boat dealers",
        "match": ("boat", "marine", "marina", "watercraft", "pontoon"),
        "hooks": ["The season is shorter than you think.",
                  "The weekend you missed is not coming back."],
        "proof": "The service department, the brands carried, how long the "
                 "family has run it.",
        "offers": ["Boat show pricing before a dated deadline",
                   "Financing with a monthly figure",
                   "Winter storage booked early"],
        "cta": "A visit to the lot. This is a category people want to stand "
               "next to.",
        "stock": ["boat on lake", "family on boat", "marina dock", "water "
                  "skiing"],
        "avoid": "Open ocean footage for an inland market. It looks like a "
                 "different sport to somebody who boats on a reservoir.",
        "archetypes": ["seasonal_urgency", "vignette", "offer_led"],
    },
    "rv": {
        "label": "RV dealers",
        "match": ("rv", "recreational vehicle", "camper", "motorhome",
                  "travel trailer"),
        "hooks": ["Camping season opens whether you are ready or not.",
                  "The one thing nobody regrets buying."],
        "proof": "Service bays, the brands, what a trade-in is worth.",
        "offers": ["Show pricing before a date",
                   "Financing with a monthly figure",
                   "Consignment and trade-in"],
        "cta": "A visit. Nobody buys one of these off a phone.",
        "stock": ["rv campground", "family camping", "motorhome road",
                  "campfire evening"],
        "avoid": "National-park footage the client's customers cannot reach in "
                 "a weekend.",
        "archetypes": ["seasonal_urgency", "vignette", "offer_led"],
    },
    "ski": {
        "label": "Ski resorts",
        "match": ("ski", "snowboard", "mountain resort", "snow"),
        "hooks": ["It snowed last night.",
                  "The pass costs less than four day tickets."],
        "proof": "Conditions, lifts running, how far it is from the feeder "
                 "metro.",
        "offers": ["A pass-sale window",
                   "Midweek pricing",
                   "A learn-to-ski package"],
        "cta": "Buy the pass, or check conditions. Both are online.",
        "stock": ["skiing powder", "chairlift", "mountain lodge", "snowfall"],
        "avoid": "Footage of a bigger mountain than the one they run. The "
                 "first visit is then a disappointment.",
        "archetypes": ["seasonal_urgency", "vignette", "offer_led"],
    },
    "tourism": {
        "label": "Tourism and attractions",
        "match": ("tourism", "attraction", "museum", "visitor", "destination",
                  "hotel", "resort"),
        "hooks": ["A weekend, not a holiday. It is closer than they think.",
                  "The thing they cannot see anywhere else."],
        "proof": "The one attraction that is genuinely unique, and how long "
                 "the drive is.",
        "offers": ["A dated event or season",
                   "A package with somewhere to stay",
                   "Free admission for children"],
        "cta": "Plan the visit — a site, not a phone call.",
        "stock": ["family day out", "scenic landmark", "summer festival",
                  "road trip"],
        "avoid": "A montage of everything. One reason to come beats nine.",
        "archetypes": ["vignette", "seasonal_urgency", "local_pride"],
    },
    "recruit": {
        "label": "Recruitment and hiring",
        "match": ("recruit", "hiring", "staffing", "employment", "careers"),
        "hooks": ["Pay, shift and location. In that order, in the first three "
                  "seconds.",
                  "The reason people stay, not the reason they apply."],
        "proof": "What people actually earn, what the shift is, how long the "
                 "team has been there.",
        "offers": ["A signing bonus",
                   "A named starting rate",
                   "Shifts that suit a second job"],
        "cta": "Apply — and the route has to be short. A candidate on a phone "
               "will not fill in six pages.",
        "stock": ["team at work", "warehouse workers", "handshake hiring",
                  "workplace culture"],
        "avoid": "Stock of an office when the job is on a floor. Candidates "
                 "notice immediately.",
        "archetypes": ["recruitment", "founder_story", "vignette"],
    },
    "stadium": {
        "label": "Sports and fan audiences",
        "match": ("sports", "stadium", "team", "athletics", "fan"),
        "hooks": ["Talk to them as a fan, not as a demographic.",
                  "Game day, said on game day."],
        "proof": "The association itself. Being the official anything is the "
                 "proof.",
        "offers": ["A game-day promotion",
                   "A tie-in to a result",
                   "Season-long ticket giveaways"],
        "cta": "Whatever the sponsorship agreement actually permits — check it "
               "before writing it.",
        "stock": ["stadium crowd", "fans celebrating", "sports bar", "game day "
                  "tailgate"],
        "avoid": "Using league or team marks without the rights. That is a "
                 "contract question and not a creative one.",
        "archetypes": ["vignette", "local_pride", "offer_led"],
    },
    "medical_dental": {
        "label": "Medical and dental practices",
        "match": ("dental", "dentist", "medical", "clinic", "orthodont",
                  "chiropract", "physician", "health"),
        "hooks": ["Most people put this off for years.",
                  "The part they are actually afraid of is not the part they "
                  "think."],
        "proof": "The practitioners, how long they have practiced, what the "
                 "first visit is actually like.",
        "offers": ["A new-patient exam at a named price",
                   "Same-day appointments",
                   "What insurance is taken"],
        "cta": "Book an appointment. Anxiety is the barrier, so the ask is a "
               "small one.",
        "stock": ["dental office", "friendly practitioner", "patient "
                  "consultation", "modern clinic"],
        "avoid": "Outcome claims and any before-and-after that implies a "
                 "typical result. Categories here carry their own advertising "
                 "rules on top of the FTC's.",
        "archetypes": ["category_education", "testimonial", "founder_story"],
    },
    "home_services": {
        "label": "Home services and trades",
        "match": ("roofing", "landscap", "remodel", "contractor", "pest",
                  "cleaning", "flooring", "windows", "restoration", "handyman"),
        "hooks": ["It is the thing they have been meaning to deal with.",
                  "The estimate is free and the delay is not."],
        "proof": "Finished work in streets people recognize, licensing, how "
                 "long a job takes.",
        "offers": ["A free estimate",
                   "A dated seasonal price",
                   "Financing on a large job"],
        "cta": "Book the estimate. The sale happens at the house, not on the "
               "screen.",
        "stock": ["roofer at work", "landscaped garden", "home exterior",
                  "contractor with homeowner"],
        "avoid": "A finished house that is plainly not local. Architecture "
                 "gives it away and the spot stops being about them.",
        "archetypes": ["before_after", "problem_solution", "local_pride"],
    },
}

GENERIC_PACK = {
    "label": "No category pack",
    "hooks": [],
    "proof": "",
    "offers": [],
    "cta": "",
    "stock": [],
    "avoid": "",
    "archetypes": ["problem_solution", "offer_led", "vignette"],
}


def pack_for(industry):
    """The creative pack for this client's category, and whether one matched.

    Returns `(key, pack, state)` with state one of `matched`, `unmatched` or
    `not_recorded`. Three answers rather than two, because a pack guessed from
    a name nobody matched puts a restaurant's vocabulary on a machine shop —
    and a wrong pack is worse than none, since it reads as research somebody
    did rather than as a gap.
    """
    text = str(industry or "").strip().lower()
    if not text:
        return "", dict(GENERIC_PACK), "not_recorded"
    for key, pack in INDUSTRY_PACKS.items():
        if any(word in text for word in pack["match"]):
            return key, pack, "matched"
    return "", dict(GENERIC_PACK), "unmatched"


# ---------------------------------------------------------------------------
# What the choice actually changes
# ---------------------------------------------------------------------------
def readiness(archetype_key, brief=None, client=None):
    """What this archetype needs that the brief does not yet have.

    Advisory, always. An archetype nobody can supply is a launch date that
    moves, and the whole value is saying so while it is still free to change —
    but a rep may well have the customer lined up and not have typed it here,
    so this asks rather than refuses. `hub/creative_needs.py`'s posture, one
    medium later.
    """
    spec = ARCHETYPES.get(str(archetype_key or ""), {})
    brief = brief or {}
    gaps = []
    for need in spec.get("needs", []):
        value = str(brief.get(need["key"]) or "").strip()
        if not value:
            gaps.append({"key": need["key"], "question": need["question"],
                         "why": need["why"]})
    return {
        "archetype": archetype_key,
        "label": spec.get("label", ""),
        "gaps": gaps,
        "ready": not gaps,
        # An archetype with nothing to supply is genuinely ready, and says so
        # rather than printing an empty checklist that reads as unfinished.
        "note": ("" if gaps else
                 "Nothing outstanding — this archetype needs nothing from the "
                 "client that the brief does not already carry."),
    }


def prompt_guidance(archetype_key, industry):
    """What the model is told, rather than what a rep picked.

    `hub/current_marketing.for_prompt()`'s rule: a model handed "B2B" writes
    B2B-flavored adjectives, and a model told what to DO about it writes a
    different script. So this hands over the beat emphasis, the hooks and the
    stock vocabulary — never the label on its own.

    The pack's state travels with it, because a model told a category it does
    not have is a model inventing one.
    """
    spec = ARCHETYPES.get(str(archetype_key or ""), ARCHETYPES[DEFAULT_ARCHETYPE])
    pack_key, pack, state = pack_for(industry)
    return {
        "archetype": spec["label"],
        "structure": spec["beat_emphasis"],
        "avoid_because": spec["bad_at"],
        "category": pack["label"] if state == "matched" else "",
        "category_hooks": list(pack["hooks"]),
        "category_proof": pack["proof"],
        "category_cta": pack["cta"],
        "category_avoid": pack["avoid"],
        "stock_vocabulary": list(spec["stock"]) + list(pack["stock"]),
        "category_state": state,
        # Said out loud so a screen can report it: writing to a category we did
        # not match is a generic spot, and calling that a category pack is the
        # confident wrong answer.
        "category_note": {
            "matched": "",
            "unmatched": ("This client's industry matched no category pack, so "
                          "the guidance is generic. That is different from the "
                          "category having nothing to say."),
            "not_recorded": ("This client has no industry recorded, so no "
                             "category guidance was used at all."),
        }[state],
    }


def suggested_archetypes(industry):
    """Which archetypes suit this category, in the pack's own order.

    A suggestion and never a filter: an unusual spot for a category is often
    the reason it works, and a picker that hides nine of twelve makes that
    impossible. `hub/voice_casting.match_quality()`'s rule.
    """
    _key, pack, state = pack_for(industry)
    return {"keys": [k for k in pack["archetypes"] if k in ARCHETYPES],
            "state": state}


def check_spec():
    """Anything in this file that changes nothing, named.

    `hub/current_marketing.unanswered_keys()`'s rule: this shipped four
    discovery questions read by nothing, so a rep could answer all four and the
    document came out identical. Returns an empty list today, which is the only
    way it was worth adding.
    """
    problems = []
    for key, spec in ARCHETYPES.items():
        for field in ("label", "what", "best_for", "beat_emphasis", "bad_at"):
            if not str(spec.get(field) or "").strip():
                problems.append(f"archetype {key} has no {field}")
        if not spec.get("stock"):
            problems.append(f"archetype {key} contributes no stock vocabulary")
        for need in spec.get("needs", []):
            if not need.get("question") or not need.get("why"):
                problems.append(f"archetype {key} need {need.get('key')} "
                                "does not say what it is for")
    for key, pack in INDUSTRY_PACKS.items():
        if not pack.get("match"):
            problems.append(f"pack {key} can never be matched to a client")
        for field in ("label", "proof", "cta", "avoid"):
            if not str(pack.get(field) or "").strip():
                problems.append(f"pack {key} has no {field}")
        for field in ("hooks", "offers", "stock", "archetypes"):
            if not pack.get(field):
                problems.append(f"pack {key} has an empty {field}")
        for name in pack.get("archetypes", []):
            if name not in ARCHETYPES:
                problems.append(f"pack {key} suggests unknown archetype {name}")
    for legacy, name in LEGACY_ARCHETYPE.items():
        if name not in ARCHETYPES:
            problems.append(f"legacy type {legacy} maps to unknown archetype {name}")
    return problems
