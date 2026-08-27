"""The Smart 1 rate card — one copy, read by both builders.

It lived inside `modules/io_builder/templates/index.html` as a 90-product
JavaScript array. That was fine while the IO builder was the only thing that
priced anything. Now the Proposal Builder quotes numbers too, and two copies
of a rate card is how a proposal promises $4.25 CPM while the IO that follows
charges $5.50 — the customer sees both documents.

So it is extracted to `hub/data/rate_card.json` and served to whoever asks.
The IO template still has its own copy for now; `check_drift()` exists to
catch the day they disagree.

## What a product carries

    label        "OUTREACH — Category"      the unique key
    category     "DISPLAY"                  after the Smart 1 renames
    product      "Category"                 the part reps recognise
    listed_rate  "$4.25 / CPM"              exactly as it appears on the card
    rate_type    CPM | CPV | None           None means a fee or custom quote
    rate_value   4.25                       the number, when there is one
    requirements "Requires Pixel Placement" what the client must provide
    timeline     "2 Business Days"          how long setup takes

`rate_type` of None is the important case: management-fee products
(25% Mgmt Fee), flat monthly fees and "Custom Quote Required" all land there,
and a proposal must not invent a CPM for any of them.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "rate_card.json"

# ---------------------------------------------------------------------------
# The joint minimum rule.
#
# One table, read by the proposal builder and the insertion order alike. It
# used to be two rules that had never been compared: the proposal checked this
# table by category, while the IO derived a floor from each product's listed
# rate (`minBudget` in its template), so the same product could pass one and
# fail the other. A proposal that quotes what the IO then refuses to write is
# the worst of both -- the number has already been in front of the client.
#
# `minimum_for()` is the single answer, and `MINIMUMS_FOR_JS` is what the two
# templates mirror. test_proposal_spec.py asserts the mirrors still agree,
# exactly as it does for the creative classifier and the area helpers.
# ---------------------------------------------------------------------------
MIN_MONTHLY_DEFAULT = 500
MIN_BY_CATEGORY = {
    "OTT": 1500,
    "DIGITAL RADIO": 1000,
    "IP TARGETS": 1000,          # 30,000 impression monthly minimum
    "SEARCH ENGINE OPTIMIZATION": 500,
    # Paid search is bought differently from everything above it. The spend is
    # the client's, billed at 15% management from retail cost, and a genuinely
    # small local campaign -- one town, a handful of exact-match terms -- runs
    # a real test at $400. Holding it to the $500 default turned working
    # campaigns into guardrail blocks on both documents.
    "SEARCH ENGINE MARKETING / PAY PER CLICK": 400,
}

# Per product, where the product's own floor differs from its category's.
MIN_BY_PRODUCT: dict[str, int] = {}


def minimum_for(product: str = "", category: str = "") -> int:
    """The monthly floor for one line, for the proposal and the IO alike.

    Product first, then category, then the default. Naming either is enough --
    the category is looked up from the card when only the product is given, so
    a caller holding one of the two never has to find the other.
    """
    name = str(product or "").strip().lower()
    if name in MIN_BY_PRODUCT:
        return MIN_BY_PRODUCT[name]
    cat = str(category or "").strip()
    if not cat and name:
        cat = str((find(product) or {}).get("category") or "")
    return MIN_BY_CATEGORY.get(cat.upper(), MIN_MONTHLY_DEFAULT)


def minimums_for_js() -> dict:
    """The whole rule, in the shape both templates mirror."""
    return {"default": MIN_MONTHLY_DEFAULT,
            "byCategory": dict(MIN_BY_CATEGORY),
            "byProduct": dict(MIN_BY_PRODUCT)}


@lru_cache(maxsize=1)
def _raw() -> dict:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # An unreadable card and a card with nothing in it look identical from
        # here, and "no products" is a confident, wrong answer — a proposal
        # would quote an empty rate card rather than say it couldn't read one.
        # The reason is recorded so `status()` can state it plainly.
        return {"products": [], "category_rename": {},
                "_error": f"{type(exc).__name__}: {exc}"}


def status() -> dict:
    """Whether the card actually loaded — for /status and the API response."""
    raw = _raw()
    if raw.get("_error"):
        return {"ok": False, "products": 0, "detail":
                f"Rate card not readable at {DATA}. {raw['_error']}"}
    n = len(raw.get("products") or [])
    if not n:
        return {"ok": False, "products": 0,
                "detail": f"Rate card at {DATA} loaded but lists no products."}
    return {"ok": True, "products": n, "detail": f"{n} products loaded."}


@lru_cache(maxsize=1)
def products() -> list[dict]:
    data = _raw()
    renames = data.get("category_rename") or {}
    out = []
    for p in data.get("products") or []:
        cat = renames.get(p.get("category", ""), p.get("category", ""))
        out.append({
            "label": p.get("label", ""),
            "category": cat,
            "product": p.get("product", ""),
            "listed_rate": p.get("listedRate", ""),
            "rate_type": p.get("rateType"),
            "rate_value": p.get("rateValue"),
            "description": p.get("description", ""),
            "requirements": p.get("requirements", ""),
            "timeline": p.get("timeline", ""),
            "min_monthly": MIN_BY_CATEGORY.get(cat.upper(), MIN_MONTHLY_DEFAULT),
        })
    return out


def categories() -> list[str]:
    return sorted({p["category"] for p in products() if p["category"]})


def by_category() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in products():
        out.setdefault(p["category"], []).append(p)
    return out


def find(label: str) -> dict | None:
    """One card product, by label or by product name.

    Exact first, then the card's own name *starting with* what was asked for.
    Several products carry their whole description in the product field --
    "Connected TV - Targeted  - This is played on televisions only  *If you
    require..." -- while every document written here stores the short name a
    rep would recognise. Exact-only therefore missed them, and each miss
    became a silent default: Connected TV took the $500 floor instead of
    OTT's $1,500, and nothing on either document said a lookup had failed.

    The match is one-directional and anchored, which is what keeps it honest.
    A contains-match either way round would let the short generic product
    "Category" swallow any longer phrase containing the word. The IO
    template's `cardLabelFor` uses the same rule, so both ends of the
    hand-off agree on what counts as a match.
    """
    want = (label or "").strip().lower()
    if not want:
        return None
    for p in products():
        if p["label"].lower() == want or p["product"].lower() == want:
            return p
    if len(want) > 3:
        for p in products():
            if p["product"].lower().startswith(want):
                return p
    return None


def search(term: str, limit: int = 20) -> list[dict]:
    """Type-ahead over the card. The IO builder has this; proposals need it
    too — the card is 90 products and nobody browses 19 categories."""
    t = (term or "").strip().lower()
    if not t:
        return products()[:limit]
    scored = []
    for p in products():
        hay = f"{p['product']} {p['category']} {p['description']}".lower()
        if t in p["product"].lower():
            scored.append((0, p))
        elif t in hay:
            scored.append((1, p))
    scored.sort(key=lambda x: x[0])
    return [p for _, p in scored[:limit]]


def estimate_delivery(product: dict, monthly_budget: float) -> dict:
    """What a budget buys, using the card's own rate.

    Returns `units=None` where the product has no CPM or CPV — a management
    fee or a custom quote cannot be turned into impressions, and inventing a
    number there is exactly the kind of confident-looking wrong figure that
    reaches a client and has to be walked back.
    """
    budget = float(monthly_budget or 0)
    rt, rv = product.get("rate_type"), product.get("rate_value")
    if not rt or not rv:
        return {"units": None, "unit_label": "",
                "note": (f"{product.get('listed_rate') or 'Custom quote'} — "
                         f"not an impression-based rate, so delivery isn't "
                         f"estimated here.")}
    if rt == "CPM":
        return {"units": int(budget / float(rv) * 1000),
                "unit_label": "impressions/month",
                "note": f"At the card rate of {product.get('listed_rate')}."}
    if rt == "CPV":
        return {"units": int(budget / float(rv)),
                "unit_label": "views/month",
                "note": f"At the card rate of {product.get('listed_rate')}."}
    return {"units": None, "unit_label": "", "note": ""}


def guardrails(items: list[dict]) -> list[dict]:
    """The same checks the IO builder runs, so a proposal can't promise
    something the IO would refuse to write."""
    out = []
    total = sum(float(i.get("monthly") or 0) for i in items)
    for i in items:
        p = find(i.get("product", "")) or {}
        budget = float(i.get("monthly") or 0)
        minimum = minimum_for(i.get("product", ""),
                              i.get("category") or p.get("category", ""))
        if budget and budget < minimum:
            out.append({
                "level": "block", "product": i.get("product", ""),
                "message": (f"{i.get('product')} is below the {minimum:,.0f} "
                            f"monthly minimum. The IO won't accept this."),
            })
        if p.get("requirements"):
            out.append({
                "level": "note", "product": i.get("product", ""),
                "message": f"Client must provide: {p['requirements']}",
            })
    # The plan floor is the smallest floor any line on it carries, not a flat
    # default. A single $420 paid-search test clears the $400 line rule; a
    # blanket $500 total would then block the same plan for being what it was
    # just told it was allowed to be, and a rep reading two contradictory
    # blocks learns to ignore both.
    if items:
        floor = min(minimum_for(i.get("product", "")) for i in items)
        if total < floor:
            out.append({"level": "block", "product": "",
                        "message": f"Total monthly is below ${floor:,}."})
    if items and not any("mgmt" in (find(i.get("product", "")) or {})
                         .get("listed_rate", "").lower() for i in items):
        out.append({"level": "note", "product": "",
                    "message": "No management-fee product on this plan — "
                               "confirm that's intentional."})
    return out


def check_drift(io_template: str | None = None) -> dict:
    """Has the IO template's embedded copy diverged from this one?

    The template still carries its own array. Until that's removed, this is
    what notices the day the two disagree — rather than a client noticing.
    """
    path = Path(io_template or (Path(__file__).parent.parent /
                "modules" / "io_builder" / "templates" / "index.html"))
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return {"checked": False, "note": "IO template not found."}
    m = re.search(r"const rateCard=(\[.*?\]);\n", src, re.S)
    if not m:
        return {"checked": False, "note": "No rateCard array in the template."}
    try:
        embedded = json.loads(m.group(1))
    except ValueError:
        return {"checked": False, "note": "Embedded rate card wouldn't parse."}
    ours = {p["label"]: p["listed_rate"] for p in products()}
    theirs = {p.get("label", ""): p.get("listedRate", "") for p in embedded}
    diffs = [k for k in set(ours) | set(theirs) if ours.get(k) != theirs.get(k)]
    return {
        "checked": True, "shared": len(ours), "embedded": len(theirs),
        "differences": diffs[:20], "in_sync": not diffs,
        "note": ("Both copies agree." if not diffs else
                 f"{len(diffs)} product(s) differ between the shared card and "
                 f"the IO template. A proposal and its IO will quote different "
                 f"numbers until this is resolved."),
    }


# ---------------------------------------------------------------------------
# Proposal tiers, built from this card
#
# The Proposal Builder used to quote Good/Better/Best from a hardcoded table in
# modules/proposal_builder/industries.py — nine industries, three fixed prices
# each, invented independently of what we actually sell. So a proposal could
# promise a "$2,500 Good package" that mapped to no product on this card, and
# the IO builder would then refuse or restructure it. The client had already
# seen the number by then.
#
# Tiers are therefore assembled from real products at real card rates, and run
# through the same guardrails() the IO enforces. If a tier cannot be built
# within the minimums, that is a fact worth surfacing rather than papering
# over with a rounder number.
# ---------------------------------------------------------------------------

# The channel names the proposal industries use, mapped onto categories on this
# card. A channel with no sensible category is left out rather than pointed at
# something approximate — quoting the wrong product is worse than quoting
# fewer of them.
CHANNEL_CATEGORIES = {
    "geofenced display":        ["LOCATION LOOKBACK", "MOBILE ONLY", "DISPLAY"],
    "connected tv":             ["OTT"],
    "ctv":                      ["OTT"],
    "streaming audio":          ["DIGITAL RADIO"],
    "digital out-of-home":      ["SMART 1 SIGNAGE"],
    "weather-triggered ads":    ["DISPLAY", "OTT"],
    "snow-triggered ads":       ["DISPLAY", "OTT"],
    "mobile retargeting":       ["RETARGETING", "MOBILE ONLY"],
    "website retargeting":      ["RETARGETING"],
    "retargeting":              ["RETARGETING"],
    "youtube":                  ["YOUTUBE"],
    "online video":             ["YOUTUBE", "OTT"],
    "fan-audience display":     ["DATA TARGETED DISPLAY", "DISPLAY"],
    "paid search":              ["SEARCH ENGINE MARKETING / PAY PER CLICK"],
    "search":                   ["SEARCH ENGINE MARKETING / PAY PER CLICK"],
    "seo":                      ["SEARCH ENGINE OPTIMIZATION"],
    "social":                   ["SOCIAL ADS", "META"],
    "meta":                     ["META"],
    "email":                    ["EMAIL MARKETING"],
    "ip targeting":             ["IP TARGETS"],
}

# What each tier is trying to be. The budgets are entry points, not prices —
# the tier costs what its products cost once the minimums are honoured.
TIER_SHAPE = [
    {"name": "Good",   "target": 2500,  "channels": 2,
     "tagline": "Cover the essentials well"},
    {"name": "Better", "target": 5500,  "channels": 3,
     "tagline": "Add reach and frequency"},
    {"name": "Best",   "target": 10000, "channels": 5,
     "tagline": "Full coverage of the market"},
]


def categories_for_channels(channels: list) -> list[str]:
    """Rate-card categories behind a proposal's channel list, in order."""
    out, seen = [], set()
    have = set(categories())
    for ch in channels or []:
        for cat in CHANNEL_CATEGORIES.get(str(ch).strip().lower(), []):
            if cat in have and cat not in seen:
                seen.add(cat)
                out.append(cat)
    return out


def _cheapest_in(category: str) -> dict | None:
    """The product that carries a category at the lowest commitment.

    A proposal names the channel; which specific product fulfils it is a
    trafficking decision. Leading with the lowest minimum keeps a tier
    buildable at its budget rather than blowing it on one line.
    """
    rows = [p for p in products() if p.get("category") == category]
    if not rows:
        return None
    rows.sort(key=lambda p: (float(p.get("rate_value") or 0) or 9e9,
                             len(p.get("label", ""))))
    return rows[0]


def tiers_for(channels: list, budget: float = 0,
              targets: list | None = None) -> list[dict]:
    """Good / Better / Best built from this card for a set of channels.

    Every line is a real product at its card rate, and every tier is checked
    against the same guardrails the IO builder runs, so a tier that cannot be
    written as an insertion order says so instead of being quoted.

    `targets` is what each tier is aiming to cost, and the caller should pass
    the price points its own market already uses. The first version of this
    ignored that and applied one set of targets to everything, which quoted a
    restaurant and a law firm the same $2,500 entry package — flattening a real
    difference in what those markets spend. What the card decides is which
    products fill a tier and what they cost per unit; how much a given industry
    can spend is not something a rate card knows.
    """
    cats = categories_for_channels(channels)
    if not cats:
        return []

    shapes = list(TIER_SHAPE)
    if targets:
        for i, t in enumerate(targets[: len(shapes)]):
            try:
                shapes[i] = {**shapes[i], "target": float(t)}
            except (TypeError, ValueError):
                pass

    out = []
    for shape in shapes:
        picked = cats[: max(1, shape["channels"])]
        if not picked:
            continue
        # Spread the target across the chosen categories, but never below the
        # minimum the IO enforces for each — that minimum is the reason a
        # cheaper "package price" was undeliverable in the first place.
        per = shape["target"] / float(len(picked))
        items, lines, monthly = [], [], 0.0
        for cat in picked:
            p = _cheapest_in(cat)
            if not p:
                continue
            floor = MIN_BY_CATEGORY.get(cat, MIN_MONTHLY_DEFAULT)
            spend = round(max(per, floor), 2)
            monthly += spend
            items.append({"product": p["label"], "monthly": spend})
            lines.append({
                "product": p["label"], "category": cat,
                "monthly": spend, "listed_rate": p.get("listed_rate", ""),
                "delivery": estimate_delivery(p, spend),
                "requirements": p.get("requirements", ""),
            })
        if not lines:
            continue
        checks = guardrails(items)
        out.append({
            "name": shape["name"],
            "tagline": shape["tagline"],
            "price": round(monthly, 2),
            "monthly": round(monthly, 2),
            "annual": round(monthly * 12, 2),
            "lines": lines,
            # What the tier actually buys, in the words the proposal will use.
            "features": [f"{l['product']} — {l['delivery'].get('note') or l['listed_rate']}"
                         for l in lines],
            "guardrails": checks,
            "blocked": any(c["level"] == "block" for c in checks),
        })

    # The recommended tier is the cheapest that clears the client's stated
    # budget, not the middle one by convention.
    if budget:
        for t in out:
            t["recommended"] = t["monthly"] <= float(budget)
    return out


# ---------------------------------------------------------------------------
# Which product a category leads with, and what a client reads it as
#
# `findProduct(category)` used to mean "the first row the card happens to list
# under that heading", and the card's order is the order somebody typed it in.
# That made three wrong answers the proposal shipped with, each of which reads
# as a deliberate recommendation to the client:
#
#   * **Run of Network led DISPLAY.** RON is $3.50 CPM of untargeted
#     inventory. It is a volume add-on to a targeted buy, and it was the
#     display product every awareness and traffic goal recommended first --
#     so the cheapest, least targeted line on the card was what the proposal
#     opened with, on a document arguing that Smart 1 targets precisely.
#     Programmatic (DATA TARGETED DISPLAY -- "Select Tactics", $5.50 CPM,
#     which builds the custom audience and carries retargeting with it) is the
#     go-to, and RON is reachable but never chosen for you.
#
#   * **"Demographic" led LOCATION LOOKBACK.** Four categories carry a product
#     literally called "Demographic" or "Behavioral", so the quote line said
#     *Demographic* where the tactic sold was location lookback -- a client
#     reading it cannot tell which of the four they bought, and neither can
#     the IO. `quote_label()` puts the category in front of the ambiguous
#     names and leaves the self-describing ones alone.
#
# Both are data here rather than rules in the wizard, because the IO reads the
# same card and the two documents must not disagree about what was sold.
# ---------------------------------------------------------------------------

# Never auto-selected. Addable by name -- a rep who wants run-of-network
# volume on top of a targeted buy should have it -- but never the first thing
# a goal recommends.
ADD_ON_ONLY = {
    "ron (run of network)",
    "programmatic - ron (run of network)",
    "programmatic - run of site (ros)",
    "podcasts - run of site (ros)",
}

# A category whose lead product lives under a different heading. Display is
# the only one: the programmatic buy is filed under its own category on the
# card, and it is what a display goal should recommend.
CATEGORY_GOTO = {
    "DISPLAY": "DATA TARGETED DISPLAY",
}

# Product names that identify a tactic but not a channel. On the quote these
# are printed as "<Category> — <Product>".
AMBIGUOUS_PRODUCT_NAMES = {
    "demographic", "behavioral", "behaviorial", "category", "contextual",
    "brand affinity", "advanced audience", "job title", "temperature",
    "ron (run of network)", "trueview", "trueview - targeted", "bumpers",
    "in-store visits", "geo-fence", "geo-fence :: targeted",
    "list provided locations", "programmatic - targeted",
    "programmatic - ron (run of network)", "programmatic - run of site (ros)",
}


def is_add_on(product: str = "") -> bool:
    """Whether this product is a top-up rather than a campaign's lead line."""
    return str(product or "").strip().lower() in ADD_ON_ONLY


def goto_category(category: str = "") -> str:
    """The category a goal should actually recommend, given this one."""
    cat = str(category or "").strip().upper()
    return CATEGORY_GOTO.get(cat, cat)


def default_product(category: str = "") -> dict | None:
    """The product a category leads with — never an add-on.

    Falls back to the first row only when every product under the heading is
    an add-on, because returning nothing would leave the goal with no line at
    all and a silently shorter media plan is worse than a debatable one.
    """
    rows = by_category().get(goto_category(category)) or \
        by_category().get(str(category or "").strip().upper()) or []
    for row in rows:
        if not is_add_on(row.get("product")):
            return row
    return rows[0] if rows else None


def quote_label(product: str = "", category: str = "") -> str:
    """What this line is called on a document a client reads.

    "Location Lookback — Demographic", not "Demographic". A product name that
    already says what channel it is keeps its own name; putting the category
    in front of "Connected TV - Targeted" only makes it longer.
    """
    name = str(product or "").strip()
    cat = str(category or "").strip()
    if not name:
        return cat
    if not cat or name.lower() not in AMBIGUOUS_PRODUCT_NAMES:
        return name
    return f"{cat.title()} — {name}"


# ---------------------------------------------------------------------------
# The listed rate is what Smart 1 pays. The quoted rate is what is sold.
#
# Every rate on the card is the buy-side number, and the builder was quoting
# it straight through -- so a proposal promised the client a $4.25 CPM and the
# delivery table computed impressions at cost, with no margin anywhere in the
# document and nothing saying one was missing. The starting quote is 2x the
# listed rate, editable per line, and the multiplier is applied only where
# there is a rate to multiply:
#
# A management-fee product has `rate_type` of None -- paid search is 15% of
# retail spend, SEO is a monthly fee, a website is a project price. Doubling
# any of those would double a fee that is already the sell price. Those are
# left exactly as the card lists them, which is what "not managed by
# percentage" means in practice: the CPM and CPV lines carry the markup, the
# percentage and flat-fee lines do not.
# ---------------------------------------------------------------------------
SELL_MULTIPLIER = 2.0


def is_marked_up(rate_type: str | None = None) -> bool:
    """Whether a line's rate is a media rate the markup applies to."""
    return str(rate_type or "").strip().upper() in ("CPM", "CPV")


def sell_rate(rate_value, rate_type: str | None = None,
              multiplier: float = SELL_MULTIPLIER):
    """The starting quoted rate for a line. None where there is no rate."""
    if not is_marked_up(rate_type):
        return None
    try:
        value = float(rate_value or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return round(value * float(multiplier or 1), 2)


def rate_rules_for_js() -> dict:
    """The whole of the above, in the shape the two wizards mirror."""
    return {
        "sellMultiplier": SELL_MULTIPLIER,
        "addOnOnly": sorted(ADD_ON_ONLY),
        "categoryGoto": dict(CATEGORY_GOTO),
        "ambiguousNames": sorted(AMBIGUOUS_PRODUCT_NAMES),
    }
