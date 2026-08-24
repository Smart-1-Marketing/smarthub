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
check("but it is labelled assumed, not read",
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


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
