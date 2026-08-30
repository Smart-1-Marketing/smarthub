"""Two paragraphs a prospect reads, built only from what was measured.

## The half that stopped at a table

`modules/scans/templates/widget_audit_report.html` is 175 lines of grouped
tables: a headline, ten collapsible sections, a score. Underneath it sits
everything needed to say why any of it matters — `OPPORTUNITIES` is 26 rules
that each carry a **measured finding** and **what it costs them** ("no
retargeting pixel of any kind is on the site — every visitor who leaves is gone
for good"), and `spend()` leads with what the business is already putting into
Google and Meta.

The rep-facing side of the same audit already feeds a model, through
`discovery_answers()` into the proposal's friction section. The client-facing
half — the report a stranger opens after typing their website into a widget on
somebody else's site — stopped at the tables, so a prospect got a score and a
list and no sentence telling them what any of it means for their business.

## Bound to what was measured, and nothing else

This is the `hub/analytics_ask.py` arrangement: the model is handed facts and
asked only for the joining-up. Four rules, each a way a summary becomes a
confident wrong claim about somebody's business on the one document that is
supposed to earn the call:

**It may cite only what fired.** The prompt carries the `spend()` figures that
came back `measured`, and only the `OPPORTUNITIES` rows whose rule actually
matched. A finding that did not fire is not in the prompt at all, so there is
nothing for the model to soften into "you may also want to consider".

**A total that excluded something says so.** Meta publishes the ads and never
the spend; Google's transparency centre publishes the fact of display and no
figure. `total_excludes` names what is missing from the number, and a paragraph
quoting the total without that line is a five-figure understatement printed
confidently. It is required in the prompt and checked on the way back.

**Nothing is invented and nothing is promised.** No product names, no prices,
no timelines, no "we can" — this is a document a prospect reads before anybody
has quoted them anything, and a promise here is one somebody else has to keep.
`_forbidden()` reads what comes back for those, and a summary that breaks the
rule is **discarded rather than patched**: the Smart 1 Labs precedent in
`hub/proposal_spec.py`, which throws copy away rather than paraphrasing it into
something nobody wrote.

**A summary it cannot ground is left out.** With nothing measured there is no
summary, and the report renders exactly as it did before — tables and no
narrative. An empty section is not softened into "we could not find much",
which is a sentence about a failed scan dressed as a finding about a business.

## One call per audit, ever

`summary()` is pure: it takes a payload and returns text, storing nothing.
`for_scan()` is the one that spends, and it spends **once per scan** — the
first time that report is opened — and every later view reads the stored text.
A prospect refreshes the page; a rep opens it to check the link; the report is
mailed and opened on a phone. Without the store each of those is a billed call
for a paragraph that cannot have changed, because the audit behind it is a
finished scan.

Stored through `jsonstore` under `data_dir("audit_summaries")`, so it survives
the disk being recreated: the Render disk is not backed up, and this is paid
prose. Keyed on the scan's `public_id`, which is the audit — a re-scan is a new
public_id and gets its own summary rather than inheriting a paragraph about
last month's site.
"""
from __future__ import annotations

import os
import re

from hub import ai as _hub_ai
from hub import jsonstore

# Things a summary for a prospect must not contain. Each is a promise
# somebody would have to keep, on a document written before anybody has spoken
# to them.
_FORBIDDEN = (
    (re.compile(r"\b\d+\s*%\s*(off|discount)", re.I), "a discount"),
    (re.compile(r"\bwe (can|will|would|guarantee|promise)\b", re.I),
     "a promise on our behalf"),
    (re.compile(r"\bguarantee(d|s)?\b", re.I), "a guarantee"),
    (re.compile(r"\bwithin \d+\s*(day|week|month)", re.I), "a timeline"),
    (re.compile(r"\bSmart 1\b", re.I), "our own name"),
)

# Money, which is the one that cannot be a flat ban. The summary is *supposed*
# to lead with what they are already spending, and that figure has a dollar
# sign on it — so a rule that refuses every "$" refuses the correct answer,
# which is how a check comes to be switched off (the note `hub/qr_codes.py`
# makes about a QR warning that fires on every social spot).
#
# So a money figure is allowed when it is one of the figures we measured, and
# refused when it is not: the grounding rule `hub/name_reading.py` applies to a
# name, applied to a number. An invented price is the thing to catch, and an
# invented price is by definition one that was not in the facts.
_MONEY = re.compile(r"\$\s?[\d,]+(?:\.\d+)?")


def _ungrounded_money(text: str, facts: list[str]) -> list[str]:
    """Money in the summary that was not in what we measured."""
    known = set()
    for line in facts or ():
        for hit in _MONEY.findall(line):
            known.add(hit.replace(" ", ""))
    return [m for m in _MONEY.findall(text or "")
            if m.replace(" ", "") not in known]

_SYSTEM = (
    "You are writing two short paragraphs at the top of a website audit that a "
    "small business owner has just asked for. They typed their website into a "
    "form and this report came back. They have not spoken to anybody.\n\n"
    "You are given ONLY facts that were actually measured about their site. "
    "Write what those facts mean for their business.\n\n"
    "Rules you must follow exactly:\n"
    "1. Use ONLY the facts given. Do not add a fact, a number, a competitor, "
    "an industry average or an assumption about their business.\n"
    "2. If a spend total is given with a note about what it leaves out, that "
    "note must appear in your text. A total that quietly excludes something is "
    "worse than no total.\n"
    "3. No prices, no discounts, no timelines, no promises, no product names, "
    "and do not name the agency. Nobody has quoted them anything yet.\n"
    "4. Write to the owner, in plain words, second person. Two paragraphs, at "
    "most about 110 words in total. No heading, no bullet list, no sign-off.\n"
    "5. Lead with what is already happening — what they are spending, or what "
    "is already working — before what is missing. This is a business somebody "
    "built, not a list of faults.\n\n"
    'Return JSON: {"summary": "<the two paragraphs, separated by a blank '
    'line>"}'
)


def _facts(data: dict) -> tuple[list[str], str]:
    """The measured facts, as lines for the prompt, and the excludes note.

    Only what fired. A finding that did not match is absent from the prompt
    entirely rather than being included with a flag — there is then nothing to
    soften into "you may also want to consider".
    """
    lines: list[str] = []
    excludes = ""

    sp = (data.get("spend") or {}) if isinstance(data.get("spend"), dict) else {}
    if sp.get("measured"):
        for row in sp.get("observed") or []:
            if not isinstance(row, dict) or not row.get("measured"):
                continue
            label = str(row.get("label") or "").strip()
            value = str(row.get("value") or row.get("display") or "").strip()
            if label and value:
                lines.append(f"- {label}: {value}")
        if sp.get("total_display") and sp.get("counted"):
            note = str(sp.get("total_note") or "").strip()
            lines.append(f"- Estimated monthly advertising spend we can see: "
                         f"{sp['total_display']}" + (f" ({note})" if note else ""))
        excluded = sp.get("total_excludes") or []
        if excluded:
            excludes = ("That total leaves out " + ", ".join(
                str(x) for x in excluded) + ".")
            lines.append("- IMPORTANT, and you must say this in your text: "
                         + excludes)
        for row in sp.get("earned") or []:
            if isinstance(row, dict) and row.get("value"):
                lines.append(f"- {row.get('label', '')}: {row['value']}")

    for opp in (data.get("opportunities") or []):
        if not isinstance(opp, dict):
            continue
        finding = str(opp.get("finding") or "").strip()
        means = str(opp.get("means") or "").strip()
        if finding:
            lines.append(f"- {finding}" + (f" {means}" if means else ""))

    return lines, excludes


def _forbidden(text: str) -> list[str]:
    """What a summary contains that it must not. Named, not stripped."""
    found = []
    for pattern, what in _FORBIDDEN:
        if pattern.search(text or ""):
            found.append(what)
    return found


def summary(data: dict) -> dict:
    """Two grounded paragraphs, or a reason there are none. Never raises.

    `(text, why)` in spirit: an empty summary always says which kind of empty
    it is, because "there was nothing measured", "the key is not set" and "the
    model wrote something it should not have" are three situations and only the
    first is a fact about the business.
    """
    try:
        if not isinstance(data, dict) or not data.get("measured"):
            return {"text": "", "measured": False, "why":
                    "Nothing was measured about this site, so there is nothing "
                    "to summarize."}
        lines, excludes = _facts(data)
        if len(lines) < 2:
            # One fact is not a summary; it is the fact, and the tables below
            # already carry it better than a paragraph would.
            return {"text": "", "measured": False, "why":
                    "Too little came back to be worth summarizing — the "
                    "sections below carry what there is."}
        if not _hub_ai.ready():
            return {"text": "", "measured": False, "why":
                    "OPENAI_API_KEY is not set, so no summary was written."}

        answer = _hub_ai.chat_json(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content":
              "What was measured about " + str(data.get("domain") or "this site")
              + ":\n" + "\n".join(lines)}],
            module="website_audit", purpose="audit_summary",
            max_tokens=500, temperature=0.4)
        text = str(answer.get("summary") or "").strip()
        if not text:
            return {"text": "", "measured": False, "why":
                    "The model returned nothing."}

        bad = _forbidden(text)
        # A figure nobody measured. Named as what it is rather than folded in
        # with the promises: "you invented a number" and "you promised
        # something" are different mistakes.
        invented = _ungrounded_money(text, lines)
        if invented:
            return {"text": "", "measured": False, "why":
                    f"The summary was discarded: it quoted {invented[0]}, "
                    "which is not one of the figures measured about this site."}
        if bad:
            # Discarded, not patched. Editing a promise out of a paragraph
            # leaves a sentence nobody wrote, which is the Smart 1 Labs rule
            # in hub/proposal_spec.py: copy that breaks a standing directive
            # is thrown away rather than paraphrased.
            return {"text": "", "measured": False, "why":
                    "The summary was discarded: it contained "
                    + ", ".join(bad) + ", which this document must not carry."}
        if excludes and not _mentions_excludes(text, excludes):
            # A total quoted without what it leaves out is a five-figure
            # understatement printed confidently. Required in the prompt AND
            # checked here, because a prompt is a request.
            return {"text": "", "measured": False, "why":
                    "The summary was discarded: it quoted a spend total "
                    "without saying what that total leaves out."}

        try:
            from hub.proposal_spec import clean_ai_text
            text = clean_ai_text(text)
        except Exception:                                   # noqa: BLE001
            pass
        return {"text": text, "measured": True, "why": ""}
    except Exception as exc:                                # noqa: BLE001
        # A summary that breaks the report it sits on top of is worse than no
        # summary. The tables render exactly as they did before.
        return {"text": "", "measured": False,
                "why": f"The summary could not be written "
                       f"({type(exc).__name__})."}


def _mentions_excludes(text: str, excludes: str) -> bool:
    """Does the summary carry the caveat the total needs?

    Matched on the *things* named rather than the sentence, because the model
    is asked to write in its own words and requiring the string back would
    reject a paragraph that said it perfectly well.
    """
    body = (text or "").lower()
    names = [w.strip().lower() for w in
             re.split(r",| and ", excludes.replace("That total leaves out ", "")
                      .rstrip(".")) if w.strip()]
    if not names:
        return True
    return any(n and n in body for n in names)


# ---------------------------------------------------------------------------
# One call per audit
# ---------------------------------------------------------------------------

_STORE = "summaries.json"


def _store_path() -> str:
    """`data_dir()`, never a relative path — the trap CLAUDE.md names about
    `os.environ.get("HUB_DATA_DIR", "data")`."""
    return os.path.join(jsonstore.data_dir("audit_summaries"), _STORE)


def stored() -> dict:
    """Every summary written so far. Never raises."""
    try:
        data = jsonstore.read_json(_store_path(), default={}) or {}
        return data if isinstance(data, dict) else {}
    except Exception:                                       # noqa: BLE001
        return {}


def for_scan(public_id: str, data: dict, *, write: bool = True) -> dict:
    """The summary for one scan, written once and read thereafter.

    `write=False` reads without ever spending — for a screen that wants to
    show the summary if there is one and must not bill for a page load.

    Keyed on the scan's `public_id` rather than the domain: a re-scan is a new
    audit and deserves its own paragraph, and keying on the domain would hand
    a rebuilt site last month's summary with nothing saying so.
    """
    key = str(public_id or "").strip()
    if not key:
        return {"text": "", "measured": False,
                "why": "No scan id, so nothing could be stored or looked up."}
    book = stored()
    row = book.get(key)
    if isinstance(row, dict) and (row.get("text") or row.get("why")):
        return {**row, "cached": True}
    if not write:
        return {"text": "", "measured": False, "cached": False,
                "why": "No summary has been written for this audit yet."}

    out = summary(data)
    # The refusal is stored too, deliberately. "Nothing was measured" and "the
    # model wrote a promise and it was discarded" are answers about this audit
    # and will not change on the next view — storing only the successes would
    # re-spend a call on every open of a report that legitimately has none.
    # What is *not* stored is a missing key or a provider failure, which are
    # about the deployment rather than the audit and are fixed by somebody.
    transient = ("OPENAI_API_KEY" in (out.get("why") or "")
                 or "could not be written" in (out.get("why") or ""))
    if not transient:
        book[key] = {"text": out.get("text", ""),
                     "measured": bool(out.get("measured")),
                     "why": out.get("why", "")}
        try:
            jsonstore.write_json(_store_path(), book)
        except Exception:                                   # noqa: BLE001
            pass
    return {**out, "cached": False}


def forget(public_id: str = "") -> int:
    """Drop one summary, or all of them. Returns how many went.

    Through `jsonstore.delete_json` where the whole file goes, never a bare
    `os.remove`: removing only the file leaves the database copy to be
    restored by the next read, so the delete undoes itself.
    """
    book = stored()
    if not public_id:
        n = len(book)
        try:
            jsonstore.delete_json(_store_path())
        except Exception:                                   # noqa: BLE001
            return 0
        return n
    if public_id not in book:
        return 0
    book.pop(public_id, None)
    try:
        jsonstore.write_json(_store_path(), book)
    except Exception:                                       # noqa: BLE001
        return 0
    return 1
