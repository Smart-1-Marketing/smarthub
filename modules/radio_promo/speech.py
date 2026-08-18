"""Turn written copy into words a TTS engine reads correctly.

Radio scripts are full of things text-to-speech gets wrong: a phone number
read as one enormous integer, a URL read as gibberish, "$19.99" read as
"dollar nineteen point nine nine". Everything is rewritten into spoken words
*before* it reaches ElevenLabs, and every substitution is reported so the
team can show a client exactly how the spot will be read.

Ported from radio-studio's `src/services/speech.js`. Order matters: project
overrides first, then email before URL (or the domain rule eats the address),
then phone, money, percentages, dates, abbreviations, bare numbers.
"""
from __future__ import annotations

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
        "eighty", "ninety"]

ORDINALS = {1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth",
            9: "ninth", 12: "twelfth", 20: "twentieth", 30: "thirtieth"}

MONTHS = {"jan": "January", "feb": "February", "mar": "March", "apr": "April",
          "may": "May", "jun": "June", "jul": "July", "aug": "August",
          "sep": "September", "sept": "September", "oct": "October",
          "nov": "November", "dec": "December"}

TLD = r"(?:com|net|org|co|io|us|biz|info|shop|agency|studio)"

WORDS_PER_SECOND = 2.6          # natural commercial read pace


def number_to_words(n) -> str:
    try:
        n = abs(int(float(n)))
    except (TypeError, ValueError):
        return str(n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        rest = f" {number_to_words(n % 100)}" if n % 100 else ""
        return f"{ONES[n // 100]} hundred{rest}"
    if n < 1_000_000:
        rest = f" {number_to_words(n % 1000)}" if n % 1000 else ""
        return f"{number_to_words(n // 1000)} thousand{rest}"
    rest = f" {number_to_words(n % 1_000_000)}" if n % 1_000_000 else ""
    return f"{number_to_words(n // 1_000_000)} million{rest}"


def _ordinal(n) -> str:
    n = int(n)
    if n in ORDINALS:
        return ORDINALS[n]
    if n < 20:
        return f"{number_to_words(n)}th"
    if n % 10 == 0:
        return TENS[n // 10][:-1] + "ieth"
    return f"{TENS[n // 10]}-{ORDINALS.get(n % 10) or ONES[n % 10] + 'th'}"


def _digits_to_words(s) -> str:
    return " ".join(ONES[int(d)] if d.isdigit() else d for d in str(s))


def _speak_segment(seg: str) -> str:
    seg = re.sub(r"(\d+)", lambda m: f" {_digits_to_words(m.group(1))} ", seg)
    return re.sub(r"\s+", " ", seg.replace("-", " dash ")).strip()


# Everyday abbreviations, safest first. Overridable per project in the UI.
DEFAULT_ABBREVIATIONS: list[tuple[str, object, int]] = [
    (r"\bAve\.", "Avenue", re.I), (r"\bBlvd\.?", "Boulevard", re.I),
    (r"\bRd\.", "Road", re.I),
    (r"\bDr\.(?=\s+[A-Z][a-z])", "Doctor", 0), (r"\bDr\.", "Drive", 0),
    (r"\bSte\.?\s*(\d+)", lambda m: f"Suite {_digits_to_words(m.group(1))}", re.I),
    # "St." is read as Street, not Saint — the commoner case in an address, and
    # overridable per project when the business really is St. Something.
    (r"\bSt\.", "Street", 0),
    (r"\bHwy\.?", "Highway", re.I), (r"\bMt\.", "Mount", re.I),
    (r"\bApt\.?", "Apartment", re.I), (r"\bJct\.?", "Junction", re.I),
    (r"\bM-F\b", "Monday through Friday", re.I),
    (r"\bMon-Fri\b", "Monday through Friday", re.I),
    (r"\bSat-Sun\b", "Saturday and Sunday", re.I),
    (r"\b24/7\b", "twenty four seven", 0), (r"\bw/", "with", re.I),
    (r"\s&\s", " and ", 0),
    (r"#(\d+)", lambda m: f"number {number_to_words(m.group(1))}", 0),
    (r"\bNo\.\s*(\d+)", lambda m: f"number {number_to_words(m.group(1))}", re.I),
    (r"\bvs\.?\b", "versus", re.I), (r"\betc\.?\b", "and so on", re.I),
    (r"\bASAP\b", "A S A P", 0), (r"\bDIY\b", "D I Y", 0),
    (r"\bHVAC\b", "H VAC", 0),
]


def normalize_for_speech(text: str, pronunciations: list[dict] | None = None) -> dict:
    """Return ``{"spoken": str, "changes": [{"from","to","why"}]}``."""
    out = str(text or "")
    changes: list[dict] = []

    def swap(pattern, replacer, why, flags=0):
        nonlocal out

        def _sub(match):
            found = match.group(0)
            to = replacer(match) if callable(replacer) else replacer
            if found != to:
                changes.append({"from": found, "to": to, "why": why})
            return to

        out = re.sub(pattern, _sub, out, flags=flags)

    # Defensive: the model is told not to write stage directions; strip any.
    swap(r"\s*[\[(](?:SFX|VO|MUSIC|ANNCR)[^\])]*[\])]\s*", " ",
         "stage direction removed", re.I)

    # 1. Project overrides win, so they run first.
    for pron in (pronunciations or []):
        frm = str((pron or {}).get("from") or "").strip()
        if not frm:
            continue
        swap(rf"\b{re.escape(frm)}\b", str(pron.get("to") or ""),
             "your pronunciation", re.I)

    # 2. Email before URL.
    swap(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)\b",
         lambda m: "%s at %s" % (
             _speak_segment(m.group(1).replace(".", " dot ")),
             " dot ".join(_speak_segment(p) for p in m.group(2).split("."))),
         "email address")

    # 3. Web addresses.
    def _url(m):
        host = " dot ".join(_speak_segment(p) for p in m.group(1).split("."))
        path = m.group(2) or ""
        spoken_path = ""
        if path:
            spoken_path = " slash " + " slash ".join(
                _speak_segment(p) for p in path.lstrip("/").split("/") if p)
        prefix = "w w w dot " if re.match(r"^www\.", m.group(0), re.I) else ""
        return f"{prefix}{host}{spoken_path}"

    swap(rf"\b(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.{TLD})(/[^\s,.]*)?",
         _url, "web address", re.I)

    # 4. Phone numbers, digit by digit with a pause between groups.
    swap(r"(?:\+?1[\s.-])?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})\b",
         lambda m: "%s, %s, %s" % (_digits_to_words(m.group(1)),
                                   _digits_to_words(m.group(2)),
                                   _digits_to_words(m.group(3))),
         "phone number")

    # 5. Money.
    def _money(m):
        whole = int(m.group(1).replace(",", ""))
        dollars = f"{number_to_words(whole)} dollar{'' if whole == 1 else 's'}"
        cents = m.group(2)
        if not cents or cents == "00":
            return dollars
        return f"{dollars} and {number_to_words(cents)} cents"

    swap(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?", _money, "price")

    # 6. Percentages.
    def _pct(m):
        n = m.group(1)
        if "." in n:
            whole, frac = n.split(".", 1)
            return f"{number_to_words(whole)} point {_digits_to_words(frac)} percent"
        return f"{number_to_words(n)} percent"

    swap(r"(\d+(?:\.\d+)?)\s?%", _pct, "percentage")

    # 7. Dates.
    swap(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
         lambda m: "%s %s" % (
             MONTHS.get(m.group(1).lower().replace(".", ""), m.group(1)),
             _ordinal(m.group(2))),
         "date", re.I)

    # 8. Everyday abbreviations.
    for pattern, to, flags in DEFAULT_ABBREVIATIONS:
        swap(pattern, to, "abbreviation", flags)

    # 9. Bare standalone numbers left over.
    swap(r"\b(\d{1,3}(?:,\d{3})+|\d{1,3})\b(?!\s?(?:st|nd|rd|th))",
         lambda m: number_to_words(m.group(0).replace(",", "")), "number")

    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out).strip()
    return {"spoken": out, "changes": changes}


# ------------------------------------------------------------------- timing
def count_words(s: str = "") -> int:
    return len([w for w in re.split(r"\s+", str(s or "")) if w])


def estimate_seconds(s: str = "") -> float:
    return round(count_words(s) / WORDS_PER_SECOND, 1)


def grade_duration(seconds: float | None, target: int) -> dict:
    """How far off the clock a finished render is, and whether that matters.

    A read that runs long is never trimmed automatically — trimming clips a
    word. It comes back flagged with how many words to cut instead.
    """
    if not seconds:
        return {"status": "unknown", "label": "Length not measured"}
    over = seconds - target
    if over > 0.4:
        words = max(1, round(over * WORDS_PER_SECOND))
        return {"status": "long", "over": round(over, 1), "trim_words": words,
                "label": f"{seconds:.1f}s — {over:.1f}s over. Roughly {words} "
                         f"word{'' if words == 1 else 's'} too many."}
    if seconds < target - 2.5:
        under = target - seconds
        return {"status": "short", "under": round(under, 1),
                "label": f"{seconds:.1f}s — {under:.1f}s of dead air at the end."}
    return {"status": "good", "label": f"{seconds:.1f}s — lands on the clock."}
