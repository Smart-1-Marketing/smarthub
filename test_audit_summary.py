"""The figure the model was handed, reported as one it invented.

    python3 test_audit_summary.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
calls OpenAI: `summary()` is driven with the model stubbed, because what is
worth asserting is what this module does with what comes back.

## Why this file exists

`hub/audit_summary.py` writes the two paragraphs at the top of a website
audit a prospect asked for themselves, and grounds every dollar figure in
something that was actually measured — the right rule, since the alternative
is a model inventing what somebody spends on a document about their own
business.

It compared the **formatted string**:

    facts say $2,400   summary writes $2,400      grounded
    facts say $2,400   summary writes $2400       reported as invented
    facts say $2,400   summary writes $2,400.00   reported as invented

Re-typing a figure is the ordinary behaviour of a model handed one. What made
it expensive is everything the module then does about a finding, each of which
is correct on its own:

  * the **whole summary is discarded**, not the figure — editing a number out
    leaves a sentence nobody wrote, the Smart 1 Labs rule;
  * the report **renders nothing**, because `widget_audit_report.html` guards
    on `summary.text`, and that silence is deliberate: a line reading "we
    could not summarize this" is a sentence about our tooling on a document
    about their business;
  * `why` explains it and **no template reads it**;
  * and `for_scan()` **stores the refusal**, on the stated reasoning that it
    "will not change on the next view" — which is true of a real refusal and
    false of this one.

So one dropped comma cost that prospect's audit its opening paragraphs
permanently, for them and for every rep who opened the link, with the only
record in a JSON blob nothing reads.

Compared as an amount now. The rule is not loosened anywhere else: a figure
naming an amount nobody measured is still reported, one that cannot be parsed
is still reported, and rounding is still not tolerated — $2,437 written as
$2,400 is a different amount on a document about somebody's money.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1audsum_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "audit-summary-test-secret"

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


from hub import audit_summary as asum                         # noqa: E402

FACTS = ["They are spending $2,400 a month on Google Ads.",
         "That is $28,800 a year.",
         "No retargeting pixel of any kind is on the site."]


def ungrounded(text, facts=None):
    """What the check reports, and never an exception that ends the run.

    Guarded because this is the fifth time in this session's work that an
    assertion which raises has taken every check after it out of the file,
    turning one broken thing into a run that says nothing. A regression here
    must name itself.
    """
    try:
        return asum._ungrounded_money(text, FACTS if facts is None else facts)
    except Exception as exc:                                  # noqa: BLE001
        return f"raised {type(exc).__name__}"


def value(hit):
    """The amount read from a `$…` string, guarded for the same reason."""
    try:
        return asum._money_value(hit)
    except Exception as exc:                                  # noqa: BLE001
        return f"raised {type(exc).__name__}"


# =====================================================================
section("A measured figure survives being re-typed")
# =====================================================================

check("quoted exactly as given", ungrounded("They spend $2,400 a month."), [])
check("with the comma dropped", ungrounded("They spend $2400 a month."), [])
check("with cents added", ungrounded("They spend $2,400.00 a month."), [])
check("with a space after the sign", ungrounded("They spend $ 2,400."), [])
check("the annualised figure, which was also measured",
      ungrounded("That is $28,800 a year."), [])
check("both of them in one sentence",
      ungrounded("$2,400 a month, or $28,800 a year."), [])
check("and a paragraph with no money in it at all",
      ungrounded("Nobody can find them when they search."), [])


# =====================================================================
section("And an amount nobody measured is still reported")
# =====================================================================
# The rule is not loosened. This is the half that must not be lost to the fix.

check("a figure out of nowhere", ungrounded("They spend $9,999."), ["$9,999"])
check("close, but not the amount measured",
      ungrounded("They spend $2,437."), ["$2,437"])
check("a digit lost", ungrounded("They spend $240."), ["$240"])
check("a digit gained", ungrounded("They spend $24,000."), ["$24,000"])
check("rounded to something we did not measure",
      ungrounded("About $2,000 a month."), ["$2,000"])
check("one grounded and one not, and only the second is named",
      ungrounded("Around $2,400, maybe $12,000."), ["$12,000"])
check("every ungrounded figure is named, not just the first",
      ungrounded("$111 and $222."), ["$111", "$222"])


# =====================================================================
section("A figure that cannot be read is not a figure we measured")
# =====================================================================
# None is the safe answer: unparseable must stay ungrounded rather than
# slipping through as "we could not check it".

check("an unreadable amount is None", value("$1,2,3.4.5"), None)
check("and empty is None", value(""), None)
check("and None itself is None", value(None), None)
check("a plain amount reads", value("$2,400"), 2400.0)
check("with cents", value("$2,400.50"), 2400.5)
check("and with a space", value("$ 2,400"), 2400.0)
check("zero is a real amount, not an absence", value("$0"), 0.0)

# A string the money pattern matches but no amount can be read from must stay
# UNGROUNDED. Treating it as grounded is the safe-looking direction that is
# wrong: it lets through the one thing the rule exists to catch.
check("the pattern matches a figure with no digits in it",
      bool(asum._MONEY.findall("They spend $, a month.")), True)
check("and no amount can be read from it", value("$,"), None)
check("so it is reported rather than passed as measured",
      ungrounded("They spend $, a month."), ["$,"])


# =====================================================================
section("What the whole check does with a discard, end to end")
# =====================================================================
# Driven through summary() with the model stubbed, because the finding is not
# "the regex is wrong" but "a correct summary was thrown away".

# Shaped the way hub/website_audit.spend() actually returns it -- `observed`,
# not `rows`, and `total_excludes` a list. Three drafts of this fixture got a
# key wrong and the assertion failed for a reason that had nothing to do with
# the module: a fixture that does not look like the real thing leaves the rule
# untested, which is the note test_ads_module.py already carries.
DATA = {
    "spend": {
        "measured": True,
        "total": 2400,
        "total_display": "$2,400",
        "counted": 1,
        "total_note": "",
        "total_excludes": ["Meta, which publishes the ads and never the spend"],
        "observed": [{"label": "Google Ads", "value": "$2,400 a month",
                      "measured": True}],
    },
    "opportunities": [{"finding": "No retargeting pixel of any kind is on "
                                  "the site.", "measured": True}],
}


def with_model(reply):
    """Answer the one model call with this text."""
    def _ask(*a, **kw):
        return reply
    asum._ask_model = _ask                      # noqa: SLF001


_real_ask = getattr(asum, "_ask_model", None)
_facts_lines, _excludes = asum._facts(DATA)
check("the facts handed to the model carry the measured spend",
      any("2,400" in ln for ln in _facts_lines), True)
check("and what the total leaves out", bool(_excludes), True)

# The exact shape that used to be thrown away: the measured figure, re-typed.
retyped = ungrounded("They are putting $2400 a month into Google Ads, "
                     "and nothing brings those visitors back.")
check("a summary quoting the measured figure without its comma is kept",
      retyped, [])

# And the shape that must still be thrown away.
invented = ungrounded("They are putting $7,500 a month into Google Ads.")
check("a summary quoting an amount nobody measured is still refused",
      invented, ["$7,500"])


# =====================================================================
section("The other refusals are untouched")
# =====================================================================
# A fix to one grounding rule must not quietly relax the others.

check("a promise is still refused",
      bool(asum._forbidden("We can get you ranking in 30 days.")), True)
# What _forbidden() actually enforces: a discount, a promise on our behalf, a
# guarantee, a timeline, and our own name. Product names are asked for in the
# prompt and deliberately NOT checked in code -- a list of product names would
# have to match ordinary English ("local listings", "display") and a false
# positive there discards a correct summary, which is the exact failure this
# PR exists to fix. Asserted as it is rather than as the docstring's prose
# reads, so the gap is visible rather than assumed away.
check("a guarantee is refused",
      bool(asum._forbidden("Results guaranteed.")), True)
check("a timeline is refused",
      bool(asum._forbidden("Ranking within 30 days.")), True)
check("a discount is refused",
      bool(asum._forbidden("20% off your first month.")), True)
check("our own name is refused",
      bool(asum._forbidden("Smart 1 can help with that.")), True)
check("a product name is NOT checked in code, only asked for in the prompt",
      asum._forbidden("You should buy Local Listings."), [])
check("ordinary prose is not",
      asum._forbidden("Nobody can find them when they search."), [])

# A total quoting no exclusion is a five-figure understatement printed
# confidently, and that check is separate from the money one.
_ex = "Meta, which publishes the ads and never the spend"
check("a total that names what it excludes passes",
      asum._mentions_excludes("They spend $2,400, which leaves out Meta.", _ex),
      True)
check("and one that does not, does not",
      asum._mentions_excludes("They spend $2,400 in total.", _ex), False)


# =====================================================================
section("Nothing in it raises, and no figure is invented by the reader")
# =====================================================================
# summary() is called while a prospect is waiting on their own report.

for junk in (None, "", 0, [], {}, "$", "$$$", "no money here"):
    try:
        asum._ungrounded_money(junk if isinstance(junk, str) else "", FACTS)
        asum._money_value(junk)
        ok = True
    except Exception as exc:                                  # noqa: BLE001
        ok = f"raised {type(exc).__name__}"
    check(f"survives {junk!r}", ok, True)

check("facts that are not a list are survived",
      asum._ungrounded_money("$2,400", None), ["$2,400"])
check("and empty facts ground nothing",
      asum._ungrounded_money("$2,400", []), ["$2,400"])

if _real_ask is not None:
    asum._ask_model = _real_ask                 # noqa: SLF001


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
