"""Fan Radio — what the voice actually reads.

The written script and the spoken script are not the same document. A TTS
engine reads "614-536-0768" as a number, "Rt. 4" as "arty four", and "$99"
as "dollar ninety nine". This pass rewrites the copy for the ear and keeps
the original for the page, so the client approves the words they'll hear.

Same rules as Radio Promo, so a script moved between the two tools is read
identically. Per-project overrides win over everything.
"""
from __future__ import annotations

import re

# (pattern, replacement, flags)
RULES: list[tuple[str, str, int]] = [
    # "St." is Street far more often than Saint in an address; overridable.
    (r"\bSt\.", "Street", 0),
    (r"\bSte\.?\b", "Suite", re.I),
    (r"\bHwy\.?", "Highway", re.I),
    (r"\bMt\.", "Mount", re.I),
    (r"\bRt\.?\s", "Route ", re.I),
    (r"\bRd\.", "Road", re.I),
    (r"\bAve\.", "Avenue", re.I),
    (r"\bBlvd\.", "Boulevard", re.I),
    (r"\bDr\.\s(?=[A-Z][a-z])", "Doctor ", 0),
    (r"\bMon\b", "Monday", 0),
    (r"\bSat\b", "Saturday", 0),
    (r"\bSun\b(?!\s*(shine|ny))", "Sunday", 0),
    (r"\b24/7\b", "twenty four seven", re.I),
    (r"\bM-F\b", "Monday through Friday", re.I),
    (r"&", " and ", 0),
    (r"\bw/\b", "with", re.I),
    (r"\bNo\.\s*(\d)", r"number \1", re.I),
    (r"\b(\d+)\s*%", r"\1 percent", 0),
    (r"\bOH\b", "Ohio", 0),
]


def _spell_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return match.group(0)
    groups = [digits[:3], digits[3:6], digits[6:]]
    return ". ".join(" ".join(g) for g in groups) + "."


def _money(match: re.Match) -> str:
    whole = match.group(1).replace(",", "")
    cents = match.group(2)
    if cents in (None, "", "00"):
        return f"{whole} dollars"
    if cents == "99":
        return f"{whole} ninety nine"
    return f"{whole} dollars {cents}"


def normalize_for_speech(text: str,
                         overrides: list[dict] | None = None) -> dict:
    """Return {'spoken', 'changes'} — the read copy plus what was changed.

    ``overrides`` is a list of ``{"from": ..., "to": ...}`` from the project,
    applied first so a business genuinely called "St. Mary's Grill" wins.
    """
    original = str(text or "")
    spoken = original
    changes: list[dict] = []

    for ov in (overrides or []):
        frm = str(ov.get("from") or "").strip()
        to = str(ov.get("to") or "").strip()
        if not frm:
            continue
        rx = re.compile(re.escape(frm), re.I)
        if rx.search(spoken):
            spoken = rx.sub(to, spoken)
            changes.append({"from": frm, "to": to, "rule": "project"})

    phone_rx = re.compile(r"\b(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")
    for m in list(phone_rx.finditer(spoken)):
        changes.append({"from": m.group(0), "to": _spell_phone(m),
                        "rule": "phone"})
    spoken = phone_rx.sub(_spell_phone, spoken)

    money_rx = re.compile(r"\$\s?([\d,]+)(?:\.(\d{2}))?")
    for m in list(money_rx.finditer(spoken)):
        changes.append({"from": m.group(0), "to": _money(m), "rule": "money"})
    spoken = money_rx.sub(_money, spoken)

    for pat, repl, flags in RULES:
        rx = re.compile(pat, flags)
        m = rx.search(spoken)
        if m:
            changes.append({"from": m.group(0), "to": rx.sub(repl, m.group(0)),
                            "rule": "abbrev"})
            spoken = rx.sub(repl, spoken)

    spoken = re.sub(r"\s{2,}", " ", spoken).strip()
    return {"spoken": spoken, "changes": changes}
