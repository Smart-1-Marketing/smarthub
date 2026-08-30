"""Every product on the rate card, on a proposal that renders.

    python3 test_rate_card_coverage.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

The card is 90 products across 19 categories, and the tests around it check
the rules — `sell_rate()` is 2x, an ambiguous name resolves to nothing, a fee
reports no impressions — against the rows somebody thought to write down. That
is exactly the set that was already right. Four products were quoted at cost
for months (#207) with every screen internally consistent, and the label bleed
that gated three IP display products as video was found the same way: by a
person building one and looking at it, not by an assertion naming it.

So this builds the proposals instead. Eight campaigns, one per family of the
card, between them buying every product on it, each saved through the running
app and rendered as a PDF and a Word file — plus a ninth holding every product
name that lives under more than one category, which is where the card is
actually dangerous and which no single-family proposal can reach.

**The campaigns are derived from the card, never listed.** A product added
tomorrow lands in whichever campaign claims its category and is exercised
without anybody editing this file — the `test_menu_layout.py` arrangement, for
the reason that file gives: a hand-written list proves the halves agree about
the rows somebody remembered. A new *category* is the case that cannot be
derived, so it fails here by name rather than being silently skipped, which is
the sweep-that-stops-sweeping failure `test_blueprint_guards.py` records.

Both HUB_DATA_DIR and DATABASE_URL are assigned rather than setdefault, the
`test_blog_publish.py` pattern that `test_jsonstore.py` pins: a fresh
directory in front of an inherited database is an empty disk in front of a
full mirror. Nothing here needs Sites Admin, so forcing SQLite costs this
file no coverage.
"""
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-card-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "card-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 66)


from werkzeug.test import Client                                    # noqa: E402
import wsgi                                                         # noqa: E402
from hub import auth                                                # noqa: E402
from hub import rate_card as rc                                     # noqa: E402
from hub import creative_needs as cn                                # noqa: E402

B = sys.modules.get("salesb_app")
if B is None:                                   # pragma: no cover - mount failed
    from modules.sales_builder import app as B

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")
M = "/sales/builder"

CARD = rc.products()
CATEGORIES = {p["category"] for p in CARD}

# The eight families a rep actually sells from. Categories, never products:
# the plan for each campaign is whatever the card currently files under them.
CAMPAIGNS = [
    ("Riverstone Dental", "dental",
     ["DATA TARGETED DISPLAY", "DISPLAY", "RETARGETING"]),
    ("Northgate Auto Group", "automotive",
     ["OTT", "YOUTUBE", "SOCIAL ADS - VIDEO"]),
    ("Harbor Point Brewing", "restaurant",
     ["DIGITAL RADIO"]),
    ("Vance Legal", "legal",
     ["SEARCH ENGINE MARKETING / PAY PER CLICK",
      "SEARCH ENGINE OPTIMIZATION"]),
    ("Bloom & Vine Florist", "retail",
     ["META", "SOCIAL MEDIA MANAGEMENT"]),
    ("Summit Roofing", "home_services",
     ["IP TARGETS", "LOCATION LOOKBACK", "MOBILE ONLY"]),
    ("Lakeside Clinic", "medical",
     ["WEB DEVELOPMENT", "CREATIVE / DESIGN SERVICES"]),
    ("Copperfield Realty", "real_estate",
     ["EMAIL MARKETING", "SMART 1 SIGNAGE", "ADD-ON PRODUCT"]),
]


def plan_line(p):
    """One media-plan line for a card row, at or above its own minimum."""
    try:
        floor = float(rc.minimum_for(p["product"], p["category"]) or 0)
    except Exception:                                   # noqa: BLE001
        floor = 0.0
    line = {"category": p["category"], "product": p["product"],
            "dollars": max(floor, 1500.0 if p.get("rate_type") else 500.0)}
    if p.get("rate_type"):
        line["rate"] = p["rate_type"]
        line["rateValue"] = p.get("rate_value")
    return line


def quote_state(client, industry, rows, **extra):
    state = {"client": client, "clientUrl": "https://example.test",
             "salesperson": "Harness", "months": 6,
             "objectives": ["Lead Generation"], "kpis": ["Cost per lead"],
             "industry": industry,
             "areas": [{"type": "radius", "origin": "Carmel, IN",
                        "radius": 15}],
             "items": [plan_line(p) for p in rows]}
    state["budget"] = sum(i["dollars"] for i in state["items"]) or 5000
    state.update(extra)
    return state


def pdf_text(data):
    from pypdf import PdfReader
    import io as _io
    return "\n".join(pg.extract_text() or ""
                     for pg in PdfReader(_io.BytesIO(data)).pages)


# ---------------------------------------------------------------------------
section("the campaigns cover the card, and are told when they stop")
# ---------------------------------------------------------------------------
claimed = [c for _, _, cats in CAMPAIGNS for c in cats]
check("no two campaigns claim the same category",
      len(claimed) == len(set(claimed)),
      [c for c in claimed if claimed.count(c) > 1])

# The half that cannot be derived. A product added to a category somebody
# already sells is picked up silently and correctly; a whole new category is
# invisible unless it is refused here, and an uncovered category means the
# products in it are on no proposal while this file still reports a pass.
check("every category on the card is claimed by a campaign",
      CATEGORIES - set(claimed) == set(),
      "unclaimed: " + ", ".join(sorted(CATEGORIES - set(claimed))))
check("and no campaign claims a category the card no longer has",
      set(claimed) - CATEGORIES == set(),
      "gone: " + ", ".join(sorted(set(claimed) - CATEGORIES)))

# ---------------------------------------------------------------------------
section("eight proposals, every product on one of them")
# ---------------------------------------------------------------------------
used = set()
for client, industry, cats in CAMPAIGNS:
    rows = [p for p in CARD if p["category"] in cats]
    for p in rows:
        used.add((p["product"], p["category"]))
    state = quote_state(client, industry, rows)

    created = staff.post(M + "/api/quotes", json={"data": state})
    if created.status_code != 200:              # pragma: no cover
        check(f"{client}: the quote saves", False, created.status_code)
        continue
    qid = created.get_json()["quote"]["id"]

    cost = B.campaign_cost(state)
    pdf = staff.get(f"{M}/api/quotes/{qid}/pdf")
    docx = staff.get(f"{M}/api/quotes/{qid}/docx")
    check(f"{client}: {len(rows)} products quote, render a PDF and a Word file",
          pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
          and docx.status_code == 200 and docx.data[:2] == b"PK",
          (pdf.status_code, docx.status_code))

    # Recurring and one-time are never added together, whatever is on the plan.
    check(f"{client}: the campaign total is the terms plus the one-time",
          abs(cost["campaign"]
              - (cost["recurring"] * cost["months"] + cost["one_time"])) < 0.01,
          cost)

    # The insertion order bills what the proposal quoted -- the $2,250-a-month
    # gap between the document a client signs and the order that bills them.
    lines = sum(float(i.get("dollars") or 0) for i in state["items"])
    check(f"{client}: the plan's lines total what the proposal costs",
          abs(lines - (cost["recurring"] + cost["one_time"])) < 0.01,
          (lines, cost["recurring"] + cost["one_time"]))

    text = pdf_text(pdf.data)
    flat = re.sub(r"\s+", " ", text)
    absent = []
    for p in rows:
        label = rc.quote_label(p["product"], p["category"]) or p["product"]
        stem = re.split(r"[-—:(]", label)[0].strip()[:24]
        if stem and stem.lower() not in flat.lower():
            absent.append(label)
    check(f"{client}: every product on the plan is named in the document",
          not absent, absent[:4])

    check(f"{client}: the campaign total is printed",
          "${:,.0f}".format(cost["campaign"]) in flat,
          "${:,.0f}".format(cost["campaign"]))

    # A float that was never formatted is the shape of "250.0 — not an
    # impression-based rate" reaching a client.
    check(f"{client}: no unformatted float reaches the client",
          not re.findall(r"\b\d+\.0\b(?!\d)", flat),
          re.findall(r"\b\d+\.0\b(?!\d)", flat)[:5])

    # Our own pricing sheet is never named on a document a client reads.
    check(f"{client}: the rate card is not named to the client",
          not any(s in flat for s in ("rate card", "Rate Card", "card rate")))

    # `ok` on this payload is the gate's own answer -- whether every gated
    # medium has been told what the creative is -- and not whether the request
    # succeeded, which is `r.ok` and is what the wizard's api() helper reads.
    # Nobody has answered these plans, so the useful assertion is that the
    # route asks about exactly the mediums the module gates, rather than that
    # it is satisfied.
    gate = staff.post(M + "/api/creative-check", json={"data": state})
    asked = {m["medium"] for m in (gate.get_json().get("media") or [])}
    check(f"{client}: the creative gate asks about every gated medium on the "
          f"plan and no others",
          gate.status_code == 200 and asked == set(cn.gated_media(state)),
          (gate.status_code, sorted(asked), sorted(cn.gated_media(state))))

check("all %d products on the card were bought by one of them" % len(CARD),
      len(used) == len(CARD),
      sorted(c + " / " + p for p, c in
             {(x["product"], x["category"]) for x in CARD} - used)[:6])

# ---------------------------------------------------------------------------
section("every product, one at a time")
# ---------------------------------------------------------------------------
unresolved, mispriced, invented, silent = [], [], [], []
for p in CARD:
    prod, cat = p["product"], p["category"]
    who = cat + " / " + prod
    row = rc.find(prod, cat)
    if not row:
        unresolved.append(who)
        continue
    rt = row.get("rate_type")
    sell = rc.sell_rate(row.get("rate_value"), rt)
    if rt in ("CPM", "CPV"):
        if not sell or abs(sell - row["rate_value"] * rc.SELL_MULTIPLIER) > 0.01:
            mispriced.append("%s (listed %s, sold %s)"
                             % (who, row.get("rate_value"), sell))
        if not rc.estimate_delivery(row, 1500.0).get("units"):
            silent.append(who)
    else:
        if sell:
            mispriced.append("%s is a fee marked up to %s" % (who, sell))
        if rc.estimate_delivery(row, 1500.0).get("units"):
            invented.append(who)

check("every product resolves on its own name and category",
      not unresolved, unresolved[:5])
check("every CPM and CPV product is sold at %sx the listed rate, and every "
      "fee at what it lists" % rc.SELL_MULTIPLIER, not mispriced, mispriced[:5])
check("every rate-bearing product reports the units it buys",
      not silent, silent[:5])
check("and no management fee or custom quote invents impressions",
      not invented, invented[:5])

# The two readings of what medium a product is -- whether to ask for creative,
# and what to ask for -- held to the module's own authority, which knows which
# pairs are both right (Pinterest, which the kit maps nothing for by name).
check("medium_of and the spec kit agree across the whole card",
      cn.spec_disagreements() == [], cn.spec_disagreements()[:3])

# ---------------------------------------------------------------------------
section("the ninth proposal: every name that means two products")
# ---------------------------------------------------------------------------
# "Behavioral" is $4.25 display, $4.00 mobile and $7.50 location lookback.
# Split across the eight above, each is quoted alone and correctly; on one
# plan, which is an ordinary multi-tactic buy, the question is whether each
# line keeps its own rate or collapses onto another's.
by_name = {}
for p in CARD:
    by_name.setdefault(p["product"], []).append(p)
shared = {n: v for n, v in by_name.items() if len(v) > 1}
check("the card still has names living under more than one category",
      len(shared) >= 4, sorted(shared))

mixed = [p for v in shared.values() for p in v]
state = quote_state("Meridian Home Services", "home_services", mixed)
qid = staff.post(M + "/api/quotes", json={"data": state}).get_json()["quote"]["id"]
pdf = staff.get(f"{M}/api/quotes/{qid}/pdf")
check("a plan holding all of them renders",
      pdf.status_code == 200 and pdf.data[:4] == b"%PDF", pdf.status_code)
flat = re.sub(r"\s+", " ", pdf_text(pdf.data))

collapsed, unlabelled = [], []
for name, rows in shared.items():
    for p in rows:
        row = rc.find(name, p["category"])
        alone = rc.find(name)
        if row and alone and row.get("rate_value") != alone.get("rate_value"):
            collapsed.append(name)                  # find(name) priced it
        label = rc.quote_label(name, p["category"])
        if name.lower() in rc.AMBIGUOUS_PRODUCT_NAMES and label == name:
            unlabelled.append(p["category"] + " / " + name)
check("a name that could mean two products resolves to neither on its own",
      not collapsed, sorted(set(collapsed)))
check("and each is quoted with its category in front of it, so a client can "
      "tell which of them they bought", not unlabelled, unlabelled[:4])

for name in sorted(shared):
    check("  %s offers its candidates rather than picking one" % name[:38],
          rc.find(name) is None and len(rc.candidates(name)) >= 2,
          (rc.find(name) is not None, len(rc.candidates(name))))

rates = sorted({rc.sell_rate(p.get("rate_value"), p.get("rate_type"))
                for p in mixed if p.get("rate_type")})
missing_rate = [r for r in rates if "${:,.2f}".format(r) not in flat]
check("every distinct quoted rate on that plan is printed, rather than one "
      "standing in for the others", not missing_rate, missing_rate)

# ---------------------------------------------------------------------------
section("the card's own edges")
# ---------------------------------------------------------------------------
# RON is a $3.50 volume top-up to a targeted buy, and it led DISPLAY on every
# awareness goal because it happened to be the first row typed under it.
led_by_addon = [c for c in sorted(CATEGORIES)
                if rc.goto_category(c) and rc.is_add_on(rc.goto_category(c))]
check("no category leads with an add-on-only product",
      not led_by_addon, led_by_addon)
check("and the add-on-only list is not empty, so that check asked something",
      len(rc.ADD_ON_ONLY) >= 1, sorted(rc.ADD_ON_ONLY))

custom = [p for p in CARD
          if "custom quote" in str(p.get("listed_rate", "")).lower()]
state = quote_state("Anchor Industrial", "other", custom)
qid = staff.post(M + "/api/quotes", json={"data": state}).get_json()["quote"]["id"]
pdf = staff.get(f"{M}/api/quotes/{qid}/pdf")
check("a plan of nothing but custom-quote products still renders",
      pdf.status_code == 200 and pdf.data[:4] == b"%PDF" and len(custom) >= 5,
      (pdf.status_code, len(custom)))

# A one-time build is not a monthly charge, and the old sum(dollars) said it
# was. Both halves of a Smart 1 Site line, which is exactly that pair.
site = {"client": "Anchor Industrial", "months": 6, "budget": 500,
        "objectives": ["Awareness"], "kpis": ["Impressions"],
        "items": [{"category": "WEB DEVELOPMENT",
                   "product": "Smart 1 Site / 2-5 pages",
                   "dollars": 349.50, "basis": "one_time"},
                  {"category": "WEB DEVELOPMENT",
                   "product": "Monthly Website Hosting & Maintenance "
                              "(standard site)", "dollars": 75.0}]}
cost = B.campaign_cost(site)
check("a one-time build and a monthly hosting line are kept apart",
      (cost["one_time"], cost["recurring"]) == (349.50, 75.0), cost)
check("and the campaign is the hosting for its term plus the build once",
      abs(cost["campaign"] - (75.0 * 6 + 349.50)) < 0.01, cost["campaign"])

# ---------------------------------------------------------------------------
print("\n" + "-" * 66)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
