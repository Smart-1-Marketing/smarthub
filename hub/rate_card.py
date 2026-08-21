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

# Minimums the IO builder enforces, so a proposal can't quote below them.
MIN_MONTHLY_DEFAULT = 500
MIN_BY_CATEGORY = {
    "OTT": 1500,
    "DIGITAL RADIO": 1000,
    "IP TARGETS": 1000,          # 30,000 impression monthly minimum
    "SEARCH ENGINE OPTIMIZATION": 500,
}


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
            "min_monthly": MIN_BY_CATEGORY.get(cat, MIN_MONTHLY_DEFAULT),
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
    want = (label or "").strip().lower()
    for p in products():
        if p["label"].lower() == want or p["product"].lower() == want:
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
        minimum = p.get("min_monthly", MIN_MONTHLY_DEFAULT)
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
    if items and total < MIN_MONTHLY_DEFAULT:
        out.append({"level": "block", "product": "",
                    "message": f"Total monthly is below ${MIN_MONTHLY_DEFAULT:,}."})
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
