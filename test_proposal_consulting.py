"""The one line a proposal can sell that the rate card does not name.

    python3 test_proposal_consulting.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

`hub/product_intake.CONSULTING` has defined "Consulting & Strategic Services"
all along, and its own docstring says what it is for: a line "used when a
proposal committed to something the rate card does not name, so the commitment
reaches the insertion order rather than being dropped for lack of a product
code."

The proposal could not make that commitment. Every push into the plan came
from a rate-card row, the card has no consulting product, and so the catch-all
was reachable only from the IO's *intake* — the path for a proposal uploaded
as a file, read back and classified. A rep selling strategy work either left
it off the quote and added it at IO time, so the client signed a document that
never mentioned it, or did not sell it.

What is asserted here is the ways that fix goes quietly wrong:

* **the card stays the wholesale card.** `product_intake.py` refuses to add
  this row to `data/rate_card.json` and is right — `check_drift()` holds that
  file against the IO template's embedded copy, and a product invented inside
  it would make both of them lie.
* **the join is a string, and it is exact.** The IO recognises the catch-all
  by its product name. A hand-typed copy in the wizard is a line the insertion
  order silently drops the day either end is edited.
* **a description is required, because the product name is not distinguishing.**
  Every consulting line ever quoted prints the same product string, so with no
  description the client reads a price against nothing and trafficking reads a
  line it cannot action. That is `question_for()`'s own rule, not a second one.
* **and the description has to survive both journeys** — onto the client's
  media plan, and onto the insertion order. The IO's intake already carried it
  to special instructions; the conversion path sent no instructions at all.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-consult-")
# Assigned rather than setdefault, and that is the whole of it: `jsonstore`
# keys its mirror *relative to the data root*, so a fresh HUB_DATA_DIR in
# front of an inherited DATABASE_URL is an empty disk over a full mirror --
# the file looks isolated, the directory really is empty, and the second run
# reads the first one's writes. This file never boots the composed app, so
# nothing here needs the shared database that shape exists to keep.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "consulting-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")

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
    print("-" * 62)


from hub import creative_needs, product_intake, rate_card          # noqa: E402
import modules.sales_builder.app as sb                             # noqa: E402

WIZARD = os.path.join(ROOT, "modules", "sales_builder", "templates", "index.html")
IO_TPL = os.path.join(ROOT, "modules", "io_builder", "templates", "index.html")
wiz = pathlib.Path(WIZARD).read_text(encoding="utf-8")
io_tpl = pathlib.Path(IO_TPL).read_text(encoding="utf-8")

C = product_intake.CONSULTING
DESC = "Quarterly brand strategy workshop, two days on site."
DISPLAY = sb.CONSULTING_DISPLAY


def line(**over):
    row = {"product": C["product"], "category": C["category"],
           "label": C["category"] + " — " + sb.CONSULTING_DISPLAY,
           "basis": "monthly", "termMonths": 6, "dollars": 5000,
           "description": DESC}
    row.update(over)
    return row


# ---------------------------------------------------------------------------
section("The card stays the wholesale card")

card_products = {str(p.get("product") or "") for p in rate_card.products()}
check("no consulting product was added to the rate card",
      C["product"] not in card_products)

# The path is rate_card's own, not a second guess at where the card lives.
raw = pathlib.Path(rate_card.DATA).read_text(encoding="utf-8")
check("the wholesale card file does not name it",
      C["product"] not in raw)

drift = rate_card.check_drift()
check("the two card copies still agree",
      drift.get("in_sync") is True, drift.get("differences"))

# The wizard's own embedded card is a third copy of the same wholesale list.
m = re.search(r"const rateCard=\[(.*?)\n\];", wiz, re.S)
check("the wizard's embedded card was found", bool(m))
check("and it does not carry the consulting line either",
      bool(m) and C["product"] not in m.group(1))

# ---------------------------------------------------------------------------
section("The join is a string, and it is exact")

io_const = re.search(r'const CONSULTING_PRODUCT\s*=\s*"([^"]+)"', io_tpl)
check("the IO declares the catch-all product name", bool(io_const))
check("and it is byte-identical to product_intake's",
      bool(io_const) and io_const.group(1) == C["product"],
      io_const.group(1) if io_const else "")

spec = sb.consulting_spec()
check("consulting_spec() serves that same product string",
      spec.get("product") == C["product"], spec.get("product"))
check("with the category the IO sets on such a line",
      spec.get("category") == C["category"] == "ADD-ON PRODUCT", spec.get("category"))

# Served, not mirrored: the wizard must carry no copy of its own. Read as
# CODE rather than as text -- prose is not a call site, and the first run of
# this check duly reported the comment *explaining* the served definition as
# the hand-typed copy it warns against. test_scan_run.py's `copy_only()` was
# written for that exact failure and this is the same rule -- restated rather
# than imported, and deliberately: that file has no __main__ guard, so
# importing it runs its whole suite here, which would make this file's result
# depend on an unrelated one's. Two short regexes is the smaller cost.
_BLOCK_COMMENT = re.compile(r"<!--.*?-->|\{#.*?#\}|/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
wiz_code = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub(" ", wiz))
check("the wizard hand-types the product string nowhere",
      wiz_code.count(C["product"]) == 0,
      "found %d literal copies in code" % wiz_code.count(C["product"]))
check("it reads the served definition instead",
      "CFG.consulting" in wiz)

# ---------------------------------------------------------------------------
section("consulting_spec() follows the one definition")

real = product_intake.CONSULTING
try:
    product_intake.CONSULTING = dict(real, product="Renamed Strategy Work")
    moved = sb.consulting_spec()
    check("renaming CONSULTING moves the served spec",
          moved.get("product") == "Renamed Strategy Work", moved.get("product"))
    # The label is built from the DISPLAY name, not the product string --
    # that is the whole point of the rename, so it must not follow a rename
    # of the join. What must follow is `product`, asserted above.
    check("the label keeps following the display name, not the join",
          str(moved.get("label") or "").endswith(sb.CONSULTING_DISPLAY),
          moved.get("label"))
finally:
    product_intake.CONSULTING = real
check("the definition was put back",
      sb.consulting_spec().get("product") == C["product"])

# ---------------------------------------------------------------------------
section("A description is required, and it is question_for()'s rule")

blank = {"items": [line(description="")]}
un = sb.consulting_unresolved(blank)
check("a consulting line with no description is unresolved", len(un) == 1)
check("and the question asked is about the description",
      bool(un) and "Describe what" in un[0].get("question", ""),
      un[0].get("question", "") if un else "")
# The ordering trap: question_for() asks basis and term before description, so
# a line whose basis is on the screen must not come back asking for it.
check("not about the basis, which the plan already carries",
      bool(un) and "monthly cost or a one-time cost" not in un[0].get("question", ""),
      un[0].get("question", "") if un else "")

check("a described line is resolved",
      sb.consulting_unresolved({"items": [line()]}) == [])
check("a whitespace-only description does not count as one",
      len(sb.consulting_unresolved({"items": [line(description="   ")]})) == 1)
check("a one-time consulting line is asked the same question",
      len(sb.consulting_unresolved(
          {"items": [line(basis="one_time", description="")]})) == 1)
check("a rate-card line is never asked",
      sb.consulting_unresolved(
          {"items": [{"product": "Category", "category": "OUTREACH"}]}) == [])

check("is_consulting() reads the product string",
      sb.is_consulting({"product": C["product"]})
      and not sb.is_consulting({"product": "Category"}))

# ---------------------------------------------------------------------------
section("It reaches the client's media plan")

state = {"months": 6, "items": [line(), {
    "product": "Category", "category": "OUTREACH", "label": "OUTREACH — Category",
    "rate": "CPM", "rateValue": 4.25, "sellRate": 8.5,
    "basis": "monthly", "dollars": 3000}]}
plan = sb.media_plan_rows(state)
rows = {r.get("product", ""): r for r in plan.get("rows") or []}
consult = [r for k, r in rows.items() if DISPLAY in k]
check("the consulting line is on the media plan", len(consult) == 1)
check("carrying its description", bool(consult) and consult[0].get("description") == DESC,
      consult[0].get("description") if consult else "")
check("a card line carries none",
      all(not r.get("description") for k, r in rows.items() if DISPLAY not in k))
check("and it reports no impressions rather than a plausible number",
      bool(consult) and consult[0].get("delivery") == "Not impression-based",
      consult[0].get("delivery") if consult else "")

# ---------------------------------------------------------------------------
section("Two consulting products, and a client can tell them apart")

# main's #316 sells a monthly RETAINER (state["consulting"] -- Suite coaching,
# priced from hours, recurring beside the licence). This is the ENGAGEMENT.
# Both are real products; what must never happen is a client reading two
# charges under names they cannot separate.
check("the engagement reads under its own name",
      DISPLAY == "Strategy Engagement", DISPLAY)
check("which is not the retainer's name",
      "Consulting & Strategy" not in DISPLAY, DISPLAY)

# The rename is presentation ONLY. The product string is the join -- the IO
# matches the catch-all on it exactly -- so it must not have moved with it.
check("the product string underneath is untouched",
      sb.consulting_spec()["product"] == C["product"] == "Consulting & Strategic Services")
check("and still matches the IO byte-for-byte",
      bool(io_const) and io_const.group(1) == sb.consulting_spec()["product"])
check("the label a plan row carries uses the display name",
      sb.consulting_spec()["label"].endswith(DISPLAY),
      sb.consulting_spec().get("label"))
check("is_consulting() still keys on the product string, not the display name",
      sb.is_consulting({"product": C["product"]})
      and not sb.is_consulting({"product": DISPLAY}))

# The client's media plan reads the engagement; the IO still gets the join.
_row = [r for k, r in
        {r.get("product", ""): r for r in
         sb.media_plan_rows({"months": 6, "items": [line()]}).get("rows") or []}.items()]
check("the media plan row prints the display name",
      bool(_row) and _row[0].get("product") == DISPLAY,
      _row[0].get("product") if _row else "")
check("and never the raw product string",
      bool(_row) and _row[0].get("product") != C["product"])

# ---------------------------------------------------------------------------
section("It is not a channel, and it needs no creative")

check("gated_media() asks for no creative for a consulting plan",
      creative_needs.gated_media({"items": [line()]}) == [],
      creative_needs.gated_media({"items": [line()]}))
check("a real media line still gates",
      creative_needs.gated_media(
          {"items": [{"product": "Connected TV - Targeted", "category": "OTT",
                      "dollars": 5000}]}) == ["video"])
check("channel_lines() leaves it out of Recommended Channel Strategy",
      [i.get("product") for i in sb.channel_lines(state)] == ["Category"],
      [i.get("product") for i in sb.channel_lines(state)])

# ---------------------------------------------------------------------------
section("The description survives the journey to the insertion order")

check("lineForIO() carries it for a consulting line",
      "row.consulting=true" in wiz and "row.description=" in wiz)
check("the conversion sends special instructions at all",
      "specialInstructions:consultingInstructions(items)" in wiz)
check("and the internal PDF gets it under its own product heading",
      'i.consulting&&i.description?["Engagement: "+i.description]' in wiz)
check("built in the shape the IO's own intake appends to",
      "state.specialInstructions" in io_tpl
      and "function consultingInstructions" in wiz)

# The browser half, driven rather than read: the function is lifted out of the
# page and run in node, the arrangement test_menu_layout.py uses over
# hub-crumbs.js -- a copy restated here would be a third thing to keep in step.
def _lift(name: str) -> str:
    """One function, from `function name(` to the line its body closes on.

    Brace-counted rather than regex-matched to the first newline: this body
    spans four lines, and a pattern that stopped at the first one lifted a
    fragment node could parse and could not run.
    """
    start = wiz.index("function %s(" % name)
    depth, i, opened = 0, start, False
    while i < len(wiz):
        if wiz[i] == "{":
            depth += 1
            opened = True
        elif wiz[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return wiz[start:i + 1]
        i += 1
    raise AssertionError("could not lift %s" % name)


js = """
const CFG = {consulting: %s};
function isConsultingLine(i){
 return !!(CFG.consulting&&(i||{}).product===CFG.consulting.product);}
%s
const rows = [
  {product: CFG.consulting.product, consulting: true, description: %s},
  {product: "Category"},
];
console.log(JSON.stringify({
  line: consultingInstructions(rows),
  none: consultingInstructions([{product: "Category"}]),
  flags: [isConsultingLine({product: CFG.consulting.product}),
          isConsultingLine({product: "Category"})],
}));
""" % (json.dumps(sb.consulting_spec()),
       _lift("consultingInstructions"),
       json.dumps(DESC))

try:
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True,
                         timeout=30)
    got = json.loads(out.stdout.strip() or "{}")
except Exception as exc:                                    # noqa: BLE001
    got = {}
    print("  (node unavailable: %s)" % exc)

if got:
    check("the instruction names the product and the engagement",
          got.get("line") == "%s: %s" % (C["product"], DESC), got.get("line"))
    check("a plan with no consulting line sends nothing",
          got.get("none") == "", repr(got.get("none")))
    check("isConsultingLine matches on the served product string",
          got.get("flags") == [True, False], got.get("flags"))
else:
    check("node was available to drive the browser half", False,
          "install node, or this half is unverified")

# ---------------------------------------------------------------------------
section("Priced by formula, not by a slice of the media budget")

check("the baseline is $300 and the per-product step is $50 — the literal "
      "numbers asked for, not merely two sides agreeing with each other",
      sb.CONSULTING_BASE == 300 and sb.CONSULTING_PER_PRODUCT == 50,
      (sb.CONSULTING_BASE, sb.CONSULTING_PER_PRODUCT))
check("consulting_spec() serves that same formula, so the browser never "
      "hand-types a second copy of it",
      spec.get("base") == sb.CONSULTING_BASE
      and spec.get("per_product") == sb.CONSULTING_PER_PRODUCT,
      spec)

js2 = """
const CFG = {consulting: %s};
function isConsultingLine(i){
 return !!(CFG.consulting&&(i||{}).product===CFG.consulting.product);}
%s
console.log(JSON.stringify([0, 1, 2, 5].map(consultingPrice)));
""" % (json.dumps(sb.consulting_spec()), _lift("consultingPrice"))

try:
    out2 = subprocess.run(["node", "-e", js2], capture_output=True, text=True,
                          timeout=30)
    got2 = json.loads(out2.stdout.strip() or "[]")
except Exception as exc:                                    # noqa: BLE001
    got2 = []
    print("  (node unavailable: %s)" % exc)

if got2:
    py = [sb.consulting_price(n) for n in (0, 1, 2, 5)]
    check("the browser's consultingPrice() agrees with Python's, across "
          "several plan sizes — the check both sides are held to",
          got2 == py, (got2, py))
else:
    check("node was available to drive the pricing formula", False,
          "install node, or this half is unverified")

# ---------------------------------------------------------------------------
section("The control refuses rather than adding a blank line")

check("the wizard offers a control for it",
      "function addConsulting()" in wiz and 'id="addConsult"' in wiz)
check("and refuses an empty description by name",
      "only thing that says what was sold" in wiz)
check("the plan step warns about a line left undescribed",
      "Strategy engagement" in wiz and "meaning nothing" in wiz)
check("and the control says which of the two consulting products this is not",
      "retainer quoted on the Investment step" in wiz)

print("\n" + "=" * 62)
print("%d passed, %d failed" % (PASS, FAIL))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
