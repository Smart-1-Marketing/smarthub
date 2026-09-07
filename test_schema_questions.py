"""A reviewer answers "none", and is told the question still needs an answer.

    python3 test_schema_questions.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches OpenAI: the one model call is stubbed, because what is worth
asserting is what this module does around it.

## Why this file exists

`hub/schema_questions.py` asks 35 questions and refuses to let a schema be
approved while any is unanswered. Its docstring is emphatic about why:

> An empty field is honest; a plausible guess is not. Structured data is
> consumed by machines that treat it as fact … **The block is the feature.**

No test named it, and the block could not be cleared for the commonest honest
answer there is.

**"none" was read as nothing.** `_blank()` treats `n/a`, `none`, `unknown`
and `-` as an unfilled field, which is right for a value coming off a
*record* and exactly wrong for one a person types into *"does the business
hold any licenses?"*. So a reviewer answered `awards: none`, it was stored,
read back as blank, and marked NEED ANSWER again — approval still blocked, by
a question they had answered, for ever. Awards, licenses, associations and a
slogan are all questions whose true answer for most small businesses is
"none".

And the first fix for it did not work, which is worth recording: `_lookup()`
returned the typed answer correctly and **the line immediately after re-tested
it** with the strict rule, because that call site cannot know the value came
from a person. One reading now — `val is None`.

**The panel contradicted itself.** The GET builds with AI and reported
`can_approve` true on the strength of inferences; the POST beside it called
`can_approve()`, which re-derives with `use_ai=False` and turns every
inference back into a NEED ANSWER. So the same screen said *"Every question
answered. Ready to approve."* in green on load and *"N still marked NEED
ANSWER, so approval stays blocked."* in red on save. Two readings of one
question on one panel.

**And two confidence levels were always zero, though one of them should not
have been.** The docstring promises each question is answered "from the
Hub's own records first, then the client's website, then a web search". Web
search genuinely is not built, and `search: 0` on `by_confidence` correctly
read as *the web has nothing on this business* rather than *nothing ever
looked* -- `NOT_BUILT` names it. Reading the client's own website had
**already half happened**: `hub.client_context.context()` merges
`hub.scan_facts.contact_observed()` -- a crawler's reading of the client's
home page -- into its own answer, tracked under `sources[field] = "scan"`,
and `build()` folded that indiscriminately into `known`, reporting a fact
read off somebody's home page as "Already on the client record." The site
tier is now what it was always meant to be: separated out by the provenance
`context()` was tracking the whole time.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1schemaq_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "schema-questions-test-secret"

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


from hub import schema_questions as SQ                          # noqa: E402
import hub.seo as _seo                                          # noqa: E402

STORE: dict = {}
_seo.load_store = lambda client: STORE
_seo.save_store = lambda client, s: None


def reset(**business):
    STORE.clear()
    STORE["business_info"] = business or {"name": "Acme Tyre",
                                          "industry": "Tyre fitting"}


def built(use_ai=False):
    """Guarded: a regression must name itself rather than ending the run."""
    try:
        return SQ.build("Acme Tyre", use_ai=use_ai)
    except Exception as exc:                                    # noqa: BLE001
        return {"raised": f"{type(exc).__name__}: {exc}"}


def row(d, key):
    return next((r for r in d.get("questions", []) if r["key"] == key), {})


# =====================================================================
section('"none" is an answer to "has it won any awards?"')
# =====================================================================
reset()
SQ.save_answers("Acme Tyre", {"awards": "none", "licenses": "None",
                              "associations": "n/a", "slogan": "-",
                              "employees": "unknown"})
d = built()
for key, typed in (("awards", "none"), ("licenses", "None"),
                   ("associations", "n/a"), ("slogan", "-"),
                   ("employees", "unknown")):
    r = row(d, key)
    check(f"{key} answered {typed!r} stays answered", r.get("answer"), typed)
    check(f"...and is not marked as needing one", r.get("confidence"), "known")

check("so none of them blocks approval",
      [k for k in ("awards", "licenses", "associations", "slogan", "employees")
       if k in d.get("need_keys", [])], [])

# Genuinely nothing is still genuinely nothing.
reset()
SQ.save_answers("Acme Tyre", {"awards": "   ", "licenses": ""})
d = built()
check("whitespace is not an answer", row(d, "awards").get("confidence"),
      "needed")
check("and neither is an empty string",
      row(d, "licenses").get("confidence"), "needed")
check("saving an empty value clears it rather than storing it",
      "awards" in (STORE.get("answers") or {}), False)

# NEED ANSWER itself is never an answer, however it arrives.
reset()
STORE["answers"] = {"awards": SQ.NEED}
check("the placeholder is not mistaken for an answer",
      row(built(), "awards").get("confidence"), "needed")

# The junk list still applies to a value off the RECORD, where "n/a" means
# nobody filled the field in -- the opposite of what it means when a person
# types it into a question.
reset(name="Acme Tyre", industry="Tyre fitting", slogan="n/a")
check("n/a on the client record is an unfilled field",
      row(built(), "slogan").get("confidence"), "needed")
check("_blank reads the two the same way only when nobody typed it",
      [SQ._blank("none"), SQ._blank("none", typed=True)], [True, False])


# =====================================================================
section("One reading of whether it can be approved")
# =====================================================================
reset()
SQ._ask_ai = lambda client, known, unknown: {
    k: "an inference" for k, _q, _g in unknown}

with_ai = built(use_ai=True)
gate = SQ.can_approve("Acme Tyre")
check("the panel and the save agree",
      with_ai.get("can_approve"), gate.get("can_approve"))
check("and neither approves on an inference nobody has checked",
      with_ai.get("can_approve"), False)
check("the inference is still shown, so it can be checked",
      row(with_ai, "awards").get("answer"), "an inference")
check("marked as one", row(with_ai, "awards").get("confidence"), "ai")
check("and counted as blocking",
      "awards" in with_ai.get("need_keys", []), True)
check("the note says which kind of blocking it is",
      "not yet checked" in with_ai.get("note", ""), True)

# Saving an AI answer is the check. It becomes one a person typed.
SQ.save_answers("Acme Tyre", {r["key"]: r["answer"]
                              for r in with_ai["questions"]})
after = built()
check("once saved, nothing is left blocking", after.get("need_answer"), 0)
check("and it can be approved", after.get("can_approve"), True)
check("with the panel and the save still agreeing",
      SQ.can_approve("Acme Tyre").get("can_approve"), True)
check("and the note says so",
      after.get("note"), "Every question answered. Ready to approve.")

# A model that answers nothing leaves the block exactly where it was.
reset()
SQ._ask_ai = lambda client, known, unknown: {}
none_found = built(use_ai=True)
check("a silent model does not unblock anything",
      none_found.get("can_approve"), False)
check("and every row says a human is needed",
      row(none_found, "awards").get("confidence"), "needed")


# =====================================================================
section("A source that was still reported as zero")
# =====================================================================
reset()
d = built()
check("site is counted now that it is built",
      sorted(d.get("by_confidence", {})), ["ai", "known", "needed", "site"])
check("only the level that is genuinely unbuilt is named",
      sorted(d.get("not_built", {})), ["search"])
check("and the reason is about us, not about the client",
      "not built" in d["not_built"]["search"], True)


# =====================================================================
section("Reading the client's own website, and telling it apart from the record")
# =====================================================================
# context() already merges hub.scan_facts.contact_observed() into its own
# "fields" -- a crawler's reading of the client's home page, tracked under
# `sources[field] = "scan"` -- and build() used to fold that straight into
# `known` next to genuine Knack answers, reporting both as "Already on the
# client record." A fact read off their website is a different claim.
import hub.client_context as _cc                                # noqa: E402
_real_context = _cc.context

reset(name="", industry="")
_cc.context = lambda client, domain="": {
    "fields": {"business_name": "Acme Tyre & Auto", "phone": "555-0100",
              "industry": "Tyre fitting", "observed_at": "2026-08-01"},
    "sources": {"business_name": "scan", "phone": "scan", "industry": "knack"},
}
try:
    d = built()
    check("a scan-sourced field reads as site confidence, not known",
          row(d, "legal_name").get("confidence"), "site")
    check("...naming the client's own website as where it came from",
          "client's own website" in row(d, "legal_name").get("source", ""), True)
    check("...with the scan date, since a sighting with no date reads as today's",
          "2026-08-01" in row(d, "legal_name").get("source", ""), True)
    check("phone reads the same way", row(d, "phone").get("confidence"), "site")
    check("a Knack-sourced field in the same response still reads as known",
          row(d, "category").get("confidence"), "known")
    check("a site answer does not block approval",
          "legal_name" in d.get("need_keys", []), False)
finally:
    _cc.context = _real_context

# A person's own answer, or a genuine client-record value, must still win
# over a site sighting of the same field -- the overlay rule this codebase
# applies everywhere: a value someone typed or already recorded beats one a
# crawler observed.
reset(name="Acme Tyre", industry="Tyre fitting")
_cc.context = lambda client, domain="": {
    "fields": {"business_name": "A Different Name Entirely", "observed_at": ""},
    "sources": {"business_name": "scan"},
}
try:
    check("the client record's own name is not overridden by a site sighting",
          row(built(), "legal_name").get("answer"), "Acme Tyre")
    check("...and it is reported as known, not site",
          row(built(), "legal_name").get("confidence"), "known")
finally:
    _cc.context = _real_context

# context() itself can fail to reach anything -- no Knack, no scan on file --
# and build() must not raise for want of a site answer.
reset()
_cc.context = lambda client, domain="": (_ for _ in ()).throw(RuntimeError("boom"))
try:
    d = built()
    check("a context() that raises costs nothing but the site tier",
          "raised" in d, False)
finally:
    _cc.context = _real_context


# =====================================================================
section("What the block is for")
# =====================================================================
# A question that does not apply must not raise one: asking a law firm about
# reservations blocks approval for something nobody wants answered, which is
# how people learn to override the block.
reset(name="Wilson Legal", industry="Attorney")
legal = built()
check("a law firm is not asked about reservations",
      row(legal, "reservations"), {})
reset(name="Nonna's", industry="Restaurant")
food = built()
check("a restaurant is", bool(row(food, "reservations")), True)

check("an unanswered question blocks", built().get("can_approve"), False)
check("and says how many", built().get("need_answer") > 0, True)
check("the blocking list is the questions, for a person to read",
      all(isinstance(q, str) and q.endswith("?")
          for q in SQ.can_approve("Acme Tyre").get("blocking", [])), True)
check("capped, because a wall of thirty is not a list",
      len(SQ.can_approve("Acme Tyre").get("blocking", [])) <= 10, True)

# A store that has never been written is not an error.
STORE.clear()
check("a client with no record at all still answers",
      built().get("can_approve"), False)
check("without raising", "raised" in built(), False)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
