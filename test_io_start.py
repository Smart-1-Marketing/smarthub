"""Starting an insertion order from something that already exists.

    python3 test_io_start.py

Same shape as test_landing_maker.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror, so it never touches
/var/data or the real database.

## Why this file exists

The IO Builder could always be *sent* a campaign — Client 360's convert
button and the Proposal Builder's "To IO" arrive with URL parameters. A rep
who simply opened the tool got a blank interview and retyped a campaign the
Hub was already holding. The start screen makes those routes reachable from
inside the tool, which puts three things under test that were not before:

  1. **The proposal lookup asks the live builder first.** There is one
     Proposal Builder and it keeps quotes in ``quotes``;
     ``modules.proposal_builder.store`` is the retired tool's read-only
     archive. Asking only the archive is how a picker ships empty for every
     proposal written since the consolidation while looking healthy — the
     defect CLAUDE.md records against the landing maker.

  2. **The page calls its own routes under the mount.** This app lives at
     /tools/io. A bare "/api/clients" is a path the *hub* owns, so it would
     not 404 loudly — it would reach a different app. Every own-API call on
     the start screen has to carry the script root.

  3. **The reader does not invent what it can read.** Two defects that only
     mattered once the reader became the front door: a stated monthly total
     written "$8,500 / month" was missed because of the spaces around the
     slash, so the largest *line item* was reported as the budget; and the
     campaign dates were computed from today even when the document stated a
     flight. Both produced a plausible wrong number rather than a blank,
     printed under the heading "From the proposal I read".
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1iostart_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "io-start-test-secret"
os.environ["PANEL_PASSWORD"] = "io-start-test-password"
os.environ.pop("OPENAI_API_KEY", None)          # the pattern reader, not the model

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


section("The proposal lookup asks the live builder, not just the archive")

from hub import campaign_spec as cs                            # noqa: E402

check("from_quote exists on the shared spec", callable(cs.from_quote), True)
# A quote id is numeric; the archive's ids are not. Anything else is not a
# quote and must fall through to the archive rather than raising.
check("a non-numeric id is not a quote", cs.from_quote("abc-123"), None)
check("an id with no row returns nothing", cs.from_quote("99999999"), None)
check("an empty id is refused", cs.from_quote(""), None)

io_src = (ROOT / "modules" / "io_builder" / "app.py").read_text()
spec_in = io_src[io_src.index('def api_spec_in'):io_src.index('def api_proposals')]
check("spec-in tries the live quotes table", "from_quote(pid)" in spec_in, True)
check("and only then the retired archive",
      spec_in.index("from_quote(pid)") < spec_in.index("proposal_builder"), True)
check("a miss in both names both, rather than blaming the archive",
      "either" in spec_in, True)


section("proposals_for lives where both tools can reach it")

from hub import proposals as hub_proposals                      # noqa: E402
from hub import landing_maker as lm                             # noqa: E402

check("it is in hub/proposals.py now",
      callable(hub_proposals.proposals_for), True)
# Re-exported, not copied. Two copies drift, and the drift is invisible
# because each caller still sees a plausible list.
check("the landing maker re-exports the same object",
      lm.proposals_for is hub_proposals.proposals_for, True)
empty = hub_proposals.proposals_for("")
check("no client is a real answer, not an error", empty["count"], 0)
check("and the two kinds stay apart",
      sorted(k for k in empty if k in ("saved", "uploaded")), ["saved", "uploaded"])


section("The reader reads what the document says")

from hub import io_prefill                                      # noqa: E402

# The stated total, written with spaces around the slash. Before the fix this
# matched nothing and the largest line item ($3,200) was reported instead.
spaced = io_prefill.from_proposal("", "Recommended monthly investment: "
                                      "$8,500 / month. Connected TV — $3,200 "
                                      "per month. SEM — $2,400 per month.")
check("a stated total written '$8,500 / month' is read",
      spaced["fields"].get("monthly_budget"), "8,500.00")
check("not the largest line item under it",
      spaced["fields"].get("monthly_budget") == "3,200.00", False)

iso = io_prefill.from_proposal("", "12 month campaign. Flight: 2026-10-01 "
                                   "through 2027-09-30. $5,000 / month")
check("an ISO flight is read from the document",
      (iso["fields"].get("start_date"), iso["fields"].get("end_date")),
      ("2026-10-01", "2027-09-30"))
check("and marked for review, not treated as agreed",
      iso["sources"].get("start_date"), "needs_review")

named = io_prefill.from_proposal("", "12 month campaign. October 1, 2026 "
                                     "through September 30, 2027. $5,000 per month")
check("a named-month flight is read too",
      (named["fields"].get("start_date"), named["fields"].get("end_date")),
      ("2026-10-01", "2027-09-30"))

# Nothing stated is still worth a suggestion -- but it must not appear under
# "From the proposal I read" as though the document said it.
none = io_prefill.from_proposal("", "12 month campaign at $5,000 per month.")
check("with no dates stated a flight is still suggested",
      bool(none["fields"].get("start_date")), True)
check("but it is labeled assumed, not read",
      none["sources"].get("start_date"), "assumed")
check("and the note says so in words",
      "suggested flight" in none["note"], True)
check("a read flight carries no such warning",
      "suggested flight" in iso["note"], False)

# An impossible date parses as text. Passing it on would put 2026-13-45 on an
# insertion order.
bad = io_prefill.from_proposal("", "Flight: 2026-13-45 to 2027-09-30. "
                                   "6 month. $1,000 / month")
check("an impossible date is refused, not forwarded",
      bad["fields"].get("start_date", "") != "2026-13-45", True)
check("and it falls back to a suggestion",
      bad["sources"].get("start_date"), "assumed")


section("The start screen is on the page, and calls its own routes correctly")

page = (ROOT / "modules" / "io_builder" / "templates" / "index.html").read_text()

check("the screen exists", 'id="startScreen"' in page, True)
for label in ("An existing client", "A proposal or previous IO", "Start from scratch"):
    check(f"it offers: {label}", label in page, True)
check("a document can be dropped as well as chosen",
      'id="startDrop"' in page and "dragover" in page, True)
check("the file input accepts what the reader can read",
      ".pdf" in page and ".docx" in page, True)

# One apply path for all three ways in. Three copies would drift, and the
# drift shows up as one entry point quietly filling fewer answers.
check("all three ways in share one apply path",
      page.count("function applyReadPayload") , 1)
check("the spec payload is adapted rather than handled separately",
      "function specToReadPayload" in page, True)

# The mount trap. This app is at /tools/io; a bare /api/clients belongs to the
# hub app and would silently reach the wrong place.
own = re.findall(r'fetch\("(/api/(?:clients|proposals|spec-in)[^"]*)"', page)
check("no own-API call is written without the script root", own, [])
for route in ("/api/clients", "/api/proposals", "/api/spec-in"):
    check(f"{route} carries the script root",
          "{{ request.script_root }}" + route in page, True)
# The reader is a HUB route and must NOT be prefixed -- the opposite mistake.
check("the proposal reader stays unprefixed, as a hub route",
      'fetch("/api/io/from-proposal"' in page, True)
check("and is never given the script root",
      "{{ request.script_root }}/api/io/from-proposal" in page, False)

check("the answer box is hidden until a question is asked",
      'row.style.display = "none"' in page, True)
check("starting from scratch is still one click",
      "function startBlank()" in page, True)


# =====================================================================
# What the proposal left unsettled is ASKED, not printed
#
# The conversion flow used to end its summary with a handful of bullets --
# "• which is the agreed figure?", "• which of these products are actually
# being bought?" -- and then ask what kind of IO this was, for another twenty
# questions. Nobody answered the bullets, because a bullet is not a question.
# Every one of them is a decision the rest of the interview is built on.
# =====================================================================
section("A figure the document gave twice is a question, not a bullet")

from hub import io_prefill                                     # noqa: E402
from hub import product_intake                                 # noqa: E402

fields, sources, found, ask, conflicts = {}, {}, [], [], []
io_prefill._merge_ai(
    {"monthly_budget": 6525.0, "products": [{"name": "Website Retargeting",
                                             "monthly": 500.0}]},
    {"monthly_budget": "4,025.00"}, sources, found, ask, "", conflicts)
check("two figures for one budget become a question", len(conflicts), 1)
check("with both of them offered as answers",
      sorted(conflicts[0]["options"]), ["4,025.00", "6,525.00"])
check("and neither is chosen for the rep", conflicts[0]["suggested"], "")
check("it is no longer printed as a note as well",
      any("agreed figure" in q for q in ask), False)

# The other shape: the pattern pass read a LINE and the model read the total.
fields2, sources2, found2, ask2, conflicts2 = {"monthly_budget": "500.00"}, {}, [], [], []
io_prefill._merge_ai(
    {"monthly_budget": 6525.0,
     "products": [{"name": "Website Retargeting", "monthly": 500.0}]},
    fields2, sources2, found2, ask2, "", conflicts2)
check("a total-versus-line-item disagreement is asked too", len(conflicts2), 1)
check("and the total is the suggestion, since that is what it is",
      conflicts2[0]["suggested"], "6,525.00")


section("A product with its own flight is confirmed, never computed")

flights = io_prefill.flight_questions({
    "start_date": "2026-09-01", "end_date": "2026-12-31",
    "products_detail": [
        {"product": "Website Retargeting", "monthly": 500,
         "start": "2026-09-01", "end": "2026-12-31"},
        {"product": "Enhanced SEO + AI Optimization", "monthly": 1400,
         "start": "2026-09-01", "end": "2026-10-31",
         "dates_note": "September - October 2026"},
        {"product": "Meta In-Market Home Buyers", "monthly": 750,
         "dates_note": "September - November"},
        {"product": "ChatGPT / AI Advertising", "monthly": 400,
         "dates_note": "Beginning November"},
    ]})
check("a product on the campaign's own flight raises nothing",
      any(f["product"].startswith("Website Retargeting") for f in flights), False)
check("a product with different stated dates is confirmed",
      [f["kind"] for f in flights if f["product"].startswith("Enhanced")],
      ["stated"])
# A month with no year is not a date. Turning one into a date here would put a
# launch nobody agreed on an insertion order.
unresolved = [f["product"] for f in flights if f["kind"] == "unresolved"]
check("a window with no year is asked, not parsed", len(unresolved), 2)
check("and the document's own wording is quoted back",
      '"Beginning November"' in
      next(f["question"] for f in flights
           if f["product"].startswith("ChatGPT")), True)
check("nothing invents a date for it",
      [f["start"] or f["end"] for f in flights if f["kind"] == "unresolved"],
      ["", ""])


section("The model matches what the rate card cannot, and never decides")

rows = product_intake.read_products(
    [{"product": "Stadium to Screen", "monthly": 2500},
     {"product": "Monthly YouTube Sales Video", "monthly": 100,
      "dates_note": "months 1-3"}], months=4)
check("a product's own flight survives classification",
      rows[1]["dates_note"], "months 1-3")
# No key on this run, so the matcher is a no-op and the card's own answer
# stands. That is the whole safety property: a model that is off, slow or
# unconfigured costs the ordering of a candidate list and nothing else.
before = [dict(r) for r in rows]
after = product_intake.ai_match(rows)
check("with no model configured the card's matching is untouched",
      [r["status"] for r in after], [r["status"] for r in before])
check("and no product is invented for it",
      [r["product"] for r in after], ["", ""])
# The suggestion is a suggestion. Even when the model answers, `product` stays
# empty until a person picks -- the wrong product on an IO is the error nobody
# catches until it bills.
src = (ROOT / "hub" / "product_intake.py").read_text()
check("ai_match never writes the product field",
      re.search(r'entry\["product"\]\s*=', src), None)
check("only a name the card holds survives", 'rc.find(picked)' in src, True)

# And with a model answering. One of these two names is on the card and one is
# invented; a matcher that cannot tell them apart puts a product code on an
# insertion order that nothing downstream recognises.
from hub import ai, rate_card                                   # noqa: E402
_card_names = [p["product"] for p in rate_card.products()]
# Deliberately a product whose name identifies exactly one row. Several card
# names do not -- "Demographic" is four products at four rates -- and those
# resolve to candidates rather than to a suggestion, which is a different
# behaviour asserted below. Picking one by index quietly picked one of those.
_real = next(n for n in _card_names
             if _card_names.count(n) == 1 and rate_card.find(n) is not None)
_fake = "Totally Made Up Product"
_prompt = {}


def _stub_chat_json(messages, **kw):
    _prompt["text"] = messages[-1]["content"]
    return {"matches": [
        {"quoted": "Stadium to Screen", "product": _real,
         "confidence": "medium", "why": "venue screen inventory"},
        {"quoted": "Monthly YouTube Sales Video", "product": _fake,
         "confidence": "high", "why": "invented"}]}


_saved = (ai.ready, ai.chat_json)
ai.ready, ai.chat_json = (lambda: True), _stub_chat_json
try:
    answered = product_intake.ai_match(product_intake.read_products(
        [{"product": "Stadium to Screen", "monthly": 2500},
         {"product": "Monthly YouTube Sales Video", "monthly": 100}], months=4))
finally:
    ai.ready, ai.chat_json = _saved

check("a real card product becomes the suggestion",
      answered[0]["suggested"], _real)
check("and is offered first, so confirming it is one keypress",
      answered[0]["candidates"][0]["product"], _real)
check("but it is still only a suggestion", answered[0]["product"], "")
check("an invented product is dropped, not shown",
      answered[1].get("suggested"), None)

# A model answer that names a product the card carries more than once. It is
# not an invention and it is not an answer either -- "Demographic" is four
# products at four rates -- so the row keeps the real options rather than
# being dropped or being given a rate nobody quoted.
_ambiguous = next((n for n in _card_names
                   if _card_names.count(n) > 1), "")


def _stub_ambiguous(messages, **kw):
    return {"matches": [{"quoted": "Audience targeting", "product": _ambiguous,
                         "confidence": "high", "why": "audience segments"}]}


ai.ready, ai.chat_json = (lambda: True), _stub_ambiguous
try:
    amb = product_intake.ai_match(product_intake.read_products(
        [{"product": "Audience targeting", "monthly": 2500}], months=4))
finally:
    ai.ready, ai.chat_json = _saved

check("an ambiguous card name is not suggested as though it were one product",
      amb[0].get("suggested"), None)
check("but the real options are offered rather than the row being dropped",
      len(amb[0].get("candidates") or []) > 1, True)
check("and every option offered is that same name at its own rate",
      {c["product"] for c in amb[0]["candidates"]}, {_ambiguous})
check("and its candidate list is the card's own, unchanged",
      _fake in [c["product"] for c in answered[1]["candidates"]], False)
check("the model is given the card to choose from",
      product_intake.short_name(_real) in _prompt.get("text", ""), True)


section("A new IO for an existing client asks what it replaces")

existing = io_prefill.open_ios("")
check("no client named is not an error", existing["ios"], [])
check("and it says so rather than returning nothing", "note" in existing, True)

check("the interview asks which order it replaces",
      'key:"replacesIo"' in page, True)
check("and when that order should stop",
      'key:"replacesIoEnd"' in page, True)
# The end date is never derived from this campaign's start: overlapping on
# purpose is common, and a guessed end date stops a campaign nobody stopped.
check("the end date is asked, not computed",
      "won't assume the day before" in page, True)
# Two things write to special instructions before that question is asked --
# a consulting line's description, and the order this IO replaces. The
# generic handler ASSIGNS, so answering the question threw both away, on the
# field trafficking actually reads.
check("special instructions are appended, not overwritten",
      'state.specialInstructions=[state.specialInstructions,extra].filter(Boolean).join' in page,
      True)
check("and what is already there is shown before it is added to",
      "Already on this order" in page, True)
check("the replacement is visible on the order itself",
      "replaces IO ${state.replacesIo}" in page, True)
check("the replacement reaches the Suite payload",
      '"replaces_io"' in (ROOT / "modules" / "io_builder" / "app.py").read_text(),
      True)


section("Products are taken OFF the IO before anything is asked about them")

check("the roster question exists", 'key:"proposalKeep"' in page, True)
check("everything the reader found starts on the IO",
      "state.proposalKeep=PROPOSAL.intake.map(e=>e.query)" in page, True)
# Dropping a product takes it off the selection, or the media plan carries a
# line for something nobody is buying. Its quoted price survives: that is what
# the document said, and Back has to be able to find it again.
check("a dropped product comes off the selection",
      "state.selected=state.selected.filter(x=>x!==label)" in page, True)
check("but its quoted price is not destroyed",
      "delete PROPOSAL.budgets" in page, False)
# Back restores state and nothing else, so a queue that consumed its own head
# would land the rep one question PAST the one they came back to check.
for ix in ("proposalConflictIx", "proposalFlightIx", "proposalNoteIx"):
    check(f"{ix} is walked on state so Back really goes back",
          f"state.{ix}" in page, True)
check("no queue is consumed by shifting",
      "PROPOSAL.conflicts||[]).shift()" in page, False)
check("and the walk never asks about it",
      "while(i<r.length&&intakeDropped(r[i]))i++" in page, True)
# dropped (decided up front) and skipped (decided during the walk) stay
# tellable apart -- collapsing them loses which questions were answered.
check("dropped and skipped are different things",
      "function intakeDropped" in page and "e.skipped=true" in page, True)


section("The proposal's questions come before the interview's")

order = [page.index('key:"proposalKeep"'), page.index('key:"proposalBudget"'),
         page.index('key:"proposalFlight"'), page.index('key:"intakeMode"'),
         page.index('key:"ioType"')]
check("roster, then money, then flights, then products, then the IO itself",
      order, sorted(order))
check("the reader's own questions are asked rather than listed",
      '(d.ask||[]).forEach(q2=>addMsg("• "+q2))' in page, False)
check("and the answers are kept on the order",
      "state.proposalNotes[ix]=" in page, True)


section("Evergreen creative")

# Every path that added creative already read this checkbox. It did not exist,
# so the optional chain swallowed the null and every asset on every order was
# filed "Evergreen: No" -- on screen and in the internal PDF -- as though
# somebody had answered the question.
check("the checkbox the code has always read now exists",
      'id="creativeEvergreen" type="checkbox"' in page, True)
check("an upload reads it", page.count('getElementById("creativeEvergreen")') >= 3, True)
check("a file attached from the gallery reads it too",
      "evergreen:false," in page, False)
check("the Creative Assets table has its own Evergreen column",
      "<th>Evergreen</th>" in page, True)
check("and says at the foot of it what evergreen obliges",
      "runs whenever no updated creative has been supplied" in page, True)
check("the internal PDF already carried the column",
      "'Evergreen'" in (ROOT / "modules" / "io_builder" / "app.py").read_text(),
      True)


section("The upload manager names the kit, not the storage vendor")

check("the spec check names the 2026 kit",
      "2026 Creative Spec Kit" in page, True)
check("the 2025 label is gone from the upload manager",
      "2025 Creative Spec Kit before it uploads" in page, False)
# The account name is not something a rep can act on, and this panel gets
# screenshotted into chats.
check("the connection panel no longer names Cloudinary",
      "Cloudinary connected" in page, False)
check("it still answers the question the rep has",
      "Upload connection ready" in page, True)
check("the numbers' real provenance is still recorded where they live",
      "S1M CREATIVE SPEC KIT 2025" in
      (ROOT / "hub" / "creative_specs.py").read_text(), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
