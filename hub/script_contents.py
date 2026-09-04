"""Does the finished copy actually say the things it must?

Two builders in this Hub write a script and then have to answer the same
question before anybody records it: **is the business named, is the address
somebody is being sent to actually in the copy, and is the phone number there
when we said it would be.** Radio Promo answered it — `_required_script_gaps`,
a regex pass over the written script, refusing the write with a 422 rather
than trusting the prompt. The Commercial Builder did not: `_check_cta` asked
whether a website or phone was *set on the client record*, which is a
different question with a much happier answer. A spot whose CTA scene was
deleted, or whose end card carries a headline and nothing else, passed that
check with the client's phone number sitting in a database column.

So the rule lives here and both read it, the way `hub/voice_casting.py` is
read by the two tools that cast a read. The next fix to how a phone number is
recognised lands once.

## Spoken or shown, and why that distinction is the whole port

Radio has one channel. If the read does not say the URL, nobody gets the URL,
so `shown` is empty for it and everything must be in the words.

A television commercial has two. The end card carries the website and the
phone in type, held long enough to read, and a spot that shows them without
saying them is not defective — it is most well-made spots. So `check()` takes
`spoken` and `shown` separately and a fact carried in **either** is carried.
Folding them into one haystack would have been simpler and would have made
this check pass on a radio script that merely had a website in a database
field, which is the failure being fixed.

## A fact nobody supplied is not a gap

The commonest way a check like this goes wrong is reporting an absence it
invented. No phone number on file means there is nothing to be missing — that
row is `not_supplied`, never a gap, and a caller that prints gaps prints
nothing for it. `measured` is false only when there is no copy to read at all,
because "this script omits the phone number" and "there is no script yet" are
different sentences and only the first is somebody's to fix.

## Matched loosely enough to stop crying wolf, and no looser

Three real false positives are closed here, each of which rejected correct
copy and sent a writer round the loop again:

* **A legal suffix is not part of the read.** A client recorded as "Acme
  Plumbing, LLC" is called "Acme Plumbing" in every script anybody would
  write, and the old exact-substring test refused it.
* **A phone number is a number, not a string.** Recorded `(317) 555-0142` and
  written `317-555-0142` are the same phone number and were two different
  strings. It is compared on its digits.
* **A hand-edited script may already be written the way it is read.** These
  scripts go through a pronunciation pass, so "acme dot com" is a legitimate
  spelling of the URL in a script somebody has tightened by hand.

What is deliberately *not* loosened is the URL's path. A landing page at
`acme.com/spring` is where the campaign points; a script that says only
`acme.com` sends the listener somewhere else, and accepting the bare domain
would let exactly that through. The whole address is required, minus only the
protocol and a trailing slash, which nobody reads aloud either way.
"""

from __future__ import annotations

import re

# The three non-negotiables, in the order a script says them. `why` is the
# consequence rather than the rule, because a writer argues with a rule and
# acts on a consequence.
NON_NEGOTIABLES = (
    {"key": "company", "label": "the business name",
     "why": "A spot nobody can name is a spot nobody can act on."},
    {"key": "url", "label": "the web address",
     "why": "The address is where the campaign sends people; without it the "
            "spot has no destination."},
    {"key": "phone", "label": "the phone number",
     "why": "The phone number was asked for on this project, so a script "
            "without it is missing the response mechanism it was built around."},
)

_KEYS = tuple(item["key"] for item in NON_NEGOTIABLES)

# Company forms nobody says out loud. Stripped from the *needle* only — a
# script is free to say "LLC" and this still matches.
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:llc|l\.l\.c|inc|inc\.|incorporated|corp|corporation|co|ltd|"
    r"limited|llp|pllc|pc|plc|gmbh)\.?$", re.I)

# How the pronunciation pass writes a separator. Read in reverse here so a
# script already written the way it is read still matches the address it came
# from — see hub docs above.
_SPOKEN_SEPARATORS = (
    (r"\s+dot\s+", "."), (r"\s+slash\s+", "/"),
    (r"\s+dash\s+", "-"), (r"\s+at\s+", "@"),
)

# The inverse of the digit-by-digit reading a phone number gets. Ten words,
# kept beside the matcher that needs them: modules/radio_promo/speech.py owns
# the forward direction for a different job (handing text to a TTS engine),
# and neither is a rule the other can be derived from.
_SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _haystack(text: str) -> str:
    """Lowercased copy with spoken separators put back as punctuation."""
    out = re.sub(r"\s+", " ", str(text or "")).lower()
    for pattern, char in _SPOKEN_SEPARATORS:
        out = re.sub(pattern, char, out)
    return out


def _squash(text: str) -> str:
    """Letters and digits only — punctuation and spacing carry no meaning here."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _digits(text: str) -> str:
    """Every digit in the copy, including any written out as words."""
    spelled = re.sub(
        r"\b(" + "|".join(_SPOKEN_DIGITS) + r")\b",
        lambda m: _SPOKEN_DIGITS[m.group(1)], str(text or "").lower())
    return re.sub(r"\D+", "", spelled)


def _company_needle(value: str) -> str:
    core = _LEGAL_SUFFIX.sub("", str(value or "").strip())
    return _squash(core or value)


def _url_needle(value: str) -> str:
    bare = re.sub(r"^\s*[a-z]+://", "", str(value or "").strip(), flags=re.I)
    return _squash(bare.rstrip("/"))


def says_company(copy: str, company: str) -> bool:
    needle = _company_needle(company)
    return bool(needle) and needle in _squash(_haystack(copy))


def says_url(copy: str, url: str) -> bool:
    needle = _url_needle(url)
    if not needle:
        return False
    hay = _squash(_haystack(copy))
    if needle in hay:
        return True
    # A "www." nobody wrote is not a missing address.
    return needle.startswith("www") and needle[3:] in hay


def says_phone(copy: str, phone: str) -> bool:
    needle = _digits(phone)
    if not needle:
        return False
    hay = _digits(copy)
    if needle in hay:
        return True
    # A number recorded with its country code and read without it, or the
    # other way about, is the same number.
    if len(needle) == 11 and needle.startswith("1"):
        return needle[1:] in hay
    return len(needle) == 10 and ("1" + needle) in hay


_MATCHERS = {"company": says_company, "url": says_url, "phone": says_phone}


def check(facts: dict, spoken: str = "", shown: str = "", require=None) -> dict:
    """Which non-negotiables the copy carries, and which it leaves out.

    ``facts`` names what this project actually has — ``company``, ``url`` and
    ``phone``; anything absent or blank is reported as ``not_supplied`` rather
    than as a gap, because there is nothing there to be missing.

    ``spoken`` is what the read says. ``shown`` is what the viewer sees on
    screen, and is empty for a medium that has no screen: a fact carried in
    either is carried. ``require`` narrows the set for a project that has
    deliberately left one out — a spot built with no phone response, say — and
    defaults to every fact that was supplied.
    """
    facts = facts or {}
    wanted = tuple(require) if require is not None else _KEYS
    copy = f"{spoken or ''}\n{shown or ''}"
    measured = bool(str(spoken or "").strip() or str(shown or "").strip())

    gaps, carried, not_supplied = [], [], []
    for item in NON_NEGOTIABLES:
        key = item["key"]
        if key not in wanted:
            continue
        value = str(facts.get(key) or "").strip()
        if not value:
            not_supplied.append(dict(item, value=""))
            continue
        row = dict(item, value=value)
        if not measured:
            # Nothing to read. Reporting these as omissions would be the
            # check inventing findings about copy that does not exist yet.
            continue
        if _MATCHERS[key](copy, value):
            row["where"] = ("spoken" if _MATCHERS[key](spoken or "", value)
                            else "shown")
            carried.append(row)
        else:
            gaps.append(row)

    return {"measured": measured, "gaps": gaps, "carried": carried,
            "not_supplied": not_supplied}


def gap_labels(result: dict) -> list:
    """Just the labels, for a caller assembling a sentence."""
    return [g["label"] for g in (result or {}).get("gaps") or []]


def sentence(result: dict) -> str:
    """One line naming what is missing, or empty where nothing is.

    Empty rather than a reassurance, so a caller can test it: a message on
    every pass is a message nobody reads.
    """
    labels = gap_labels(result)
    if not labels:
        return ""
    if len(labels) == 1:
        return f"The copy never says {labels[0]}."
    return "The copy never says " + ", ".join(labels[:-1]) + f" or {labels[-1]}."
