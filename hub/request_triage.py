"""The classification sitting in the paragraph somebody already typed.

## What arrives empty

A web ticket (object_107) writes eight fields, two of which — **type of
ticket** and **revision requires billing** — are choices somebody picks. A
campaign support request (object_121) writes twenty-three, including
**Campaign Support** type, **Timeline** and whether it is a rush. In both cases
the person raising the request has already written a paragraph describing
exactly the work being asked for, and in both cases the dropdowns above it
arrive on whatever they were left on.

That is not a form that failed. It is a form asking a person to restate, in a
dropdown, something they have just written in prose — and the thing that
happens then is that the dropdown is skipped and the ticket lands in nobody's
queue. `hub/knack_api.py` records the same failure one level up: twenty
questions became eight answers and twelve blanks, which is why the write set
was cut to what is actually asked for.

## The shape, and why it is the safest one available

This does exactly what `contact_suggestions()` does on the Client 360 contact
strip, and inherits its rules rather than restating them:

**Into the empty fields only.** A value somebody chose is the better source and
is never offered over. The caller says which fields are empty and nothing else
is answered — a rule enforced here rather than in the browser, because a rule
the form keeps while the endpoint breaks it is not a rule.

**Never a default.** Nothing is written by this call. The suggestion is drawn
dotted, and one press keeps it. A wrong ticket type written silently is worse
than a blank one: a blank is visibly unanswered and lands on somebody's desk,
while a confident wrong answer routes the request to the wrong queue and
nothing on any screen says a guess was made.

**Every suggestion is one of Knack's own published choices, verbatim.** The
model is handed the exact option strings that came off the live object, and an
answer that is not one of them is **dropped and counted** — the grounding rule
`hub/site_names_ai.py` applies to a business name read out of a title. This is
not politeness: Knack refuses the *whole record* over one bad choice value, so
a suggestion the schema does not publish would cost the request rather than the
field. `coerce_field()` would catch it at the write; catching it here means it
never reaches a screen and offers somebody a choice that cannot be saved.

**A field it cannot answer is left out**, not filled with the nearest option.
Thirteen rows of a plausible guess is a form somebody stops reading, and one
wrong row in it is the one that gets sent.

## What it is never asked

Not the client, not the insertion order, not a date. Those are identifiers and
commitments: the client is resolved by `hub/client_key.py` exactly or not at
all, the IO comes off the client's own records, and a due date the Hub invented
is a date the campaign team works to — the rule `hub/ad_copy.py` states for the
prefill it does. This answers only questions whose entire answer space is a
list Knack publishes.
"""
from __future__ import annotations

from hub import ai as _hub_ai
from hub.client_key import normalise_name

# The controls whose answer space is a published list. A text box has no list
# to be wrong against, a connection is a record id (never a name — the rule
# `connection_choices()` exists for), and a date is a commitment.
CHOICE_CONTROLS = ("select", "multi", "boolean", "radio")

# Enough words to be describing something. Below this there is nothing to
# classify, and asking anyway spends a call to be told so.
MIN_WORDS = 6

_SYSTEM = (
    "You are helping route a work request inside a marketing agency. You are "
    "given what somebody typed to describe the work, and a set of questions "
    "with the ONLY answers each one accepts.\n\n"
    "Rules you must follow exactly:\n"
    "1. Every answer must be copied EXACTLY from that question's list of "
    "allowed answers. Do not reword one, do not combine two, do not invent "
    "one.\n"
    "2. If the description does not tell you the answer to a question, LEAVE "
    "THAT QUESTION OUT. A missing answer is fine. A plausible guess is not — "
    "somebody will send this without re-reading it.\n"
    "3. Do not infer a budget, a deadline, a client name or a price from the "
    "description.\n"
    "4. `why` is one short clause quoting or paraphrasing the part of the "
    "description that decided it, for a person deciding whether to keep the "
    "suggestion.\n\n"
    'Return JSON: {"answers": [{"key": "<the question key>", "value": '
    '"<one allowed answer, copied exactly>", "why": "<short reason>"}]}'
)


def _fields_for(fields, empty_keys) -> list[dict]:
    """The questions worth asking about: a published list, and still empty."""
    want = {str(k) for k in (empty_keys or [])}
    out = []
    for f in fields or []:
        if str(f.get("key")) not in want:
            continue
        if str(f.get("control")) not in CHOICE_CONTROLS:
            continue
        choices = [str(c) for c in (f.get("choices") or []) if str(c).strip()]
        if f.get("control") == "boolean" and not choices:
            choices = ["Yes", "No"]
        if len(choices) < 2:
            # One choice is not a question, and none is a field whose options
            # Knack did not publish — offering a guess there is exactly the
            # write that costs the record.
            continue
        out.append({"key": str(f.get("key")), "label": str(f.get("label") or f.get("key")),
                    "choices": choices})
    return out


def _match(value: str, choices: list[str]) -> str:
    """The published choice this answer *is*, or "".

    Exact first, then an exactly normalised comparison — punctuation and case
    only. Never a substring and never the nearest: "Revision" against "Revision
    (billable)" is a different answer, and picking one is the guess
    `client_key.resolve()` refuses on a client name, for the same reason.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    for c in choices:
        if raw == c:
            return c
    key = normalise_name(raw)
    if not key:
        return ""
    hits = [c for c in choices if normalise_name(c) == key]
    return hits[0] if len(hits) == 1 else ""


def suggest(text: str, fields, empty_keys, *, module: str = "tickets") -> dict:
    """Propose an answer for each empty choice field. Writes nothing.

    Returns `{"ok", "suggestions", "unusable", "error", "note"}`. `error` rides
    in the payload rather than raising, because "the description says nothing
    about any of these" and "we could not ask" are different answers and only
    the first means there is nothing to offer — the shape
    `connected_accounts_result()` settled in Google Finder.
    """
    body = " ".join(str(text or "").split())
    asking = _fields_for(fields, empty_keys)

    if not asking:
        return {"ok": True, "suggestions": {}, "unusable": 0, "error": "",
                "note": "Nothing left to suggest — every question with a set "
                        "list of answers already has one."}
    if len(body.split()) < MIN_WORDS:
        return {"ok": True, "suggestions": {}, "unusable": 0, "error": "",
                "note": "Describe the work first — there is not enough here "
                        "to classify."}
    if not _hub_ai.ready():
        return {"ok": False, "suggestions": {}, "unusable": 0,
                "error": "OPENAI_API_KEY is not set on the server.",
                "note": ""}

    ask = "\n\n".join(
        f"key: {f['key']}\nquestion: {f['label']}\nallowed answers:\n"
        + "\n".join(f"  - {c}" for c in f["choices"])
        for f in asking)
    try:
        answer = _hub_ai.chat_json(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content":
              f"What somebody typed:\n{body}\n\nQuestions:\n\n{ask}"}],
            module=module, purpose="request_triage",
            max_tokens=900, temperature=0)
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "suggestions": {}, "unusable": 0,
                "error": f"{type(exc).__name__}: {exc}", "note": ""}

    by_key = {f["key"]: f for f in asking}
    out: dict[str, dict] = {}
    unusable = 0
    for row in (answer.get("answers") or []):
        if not isinstance(row, dict):
            continue
        field = by_key.get(str(row.get("key") or ""))
        if not field:
            # An answer to a question we did not ask — a field that is already
            # filled, or one that does not exist. Dropped rather than offered
            # over a value somebody chose.
            unusable += 1
            continue
        value = _match(row.get("value"), field["choices"])
        if not value:
            # Not one of Knack's published choices. Counted, because a prompt
            # that has started inventing option strings is something to see:
            # Knack refuses the whole record over one, so this is the check
            # that keeps a suggestion from costing a request.
            unusable += 1
            continue
        out[field["key"]] = {
            "value": value,
            "why": str(row.get("why") or "").strip()[:220],
            "label": field["label"],
        }

    return {
        "ok": True, "suggestions": out, "unusable": unusable, "error": "",
        "note": (f"{len(out)} suggestion(s) from what you typed."
                 + (f" {unusable} answer(s) were discarded for not being one "
                    "of the options this field accepts." if unusable else "")
                 + (" Nothing has been saved — keep the ones that are right."
                    if out else
                    " The description does not say enough to answer the rest.")),
    }
