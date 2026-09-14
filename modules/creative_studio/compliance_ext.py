"""The compliance scanner -- WO-CS10 item 4.

`modules.commercial_builder.compliance_spec.scan()` already covers Reg Z
(trigger terms), the FTC's endorsement guides, FINRA, attorney advertising
(state_bar) and TTB -- five of the build spec's own list, already published
as data with a citation on every finding, already read by nothing else than
Commercial Builder's own QC panel. This module reads it rather than
restating it, the same cross-module reuse `campaign_generation.py` and
`review_routes.py` already use for `generation.py` and `review_spec.py`.

What that scanner does not cover is two of the spec's own copy-quality
checks -- superlatives and unnamed-competitor language -- which are not tied
to any one regulated industry the way Reg Z or TTB are, so they run on
EVERY scan rather than being gated on an industry match. `_finding()` builds
them in the same shape `compliance_spec._finding()` does (regime/regime_
label/authority/citation/headline/requires/evidence/addressed), so a screen
can render one merged list without knowing which module answered which row.

Two of the spec's own named pack gates -- `eeoc` and `fair_housing` -- are
declared in `NOT_MODELED` with the reason, rather than built from citations
nobody here has verified. `compliance_spec.NOT_ENFORCED` states the same
rule about the vacated CARS Rule: a check this codebase is not confident in
is named as absent, never guessed at.
"""
from __future__ import annotations

import re

NOT_MODELED = {
    "eeoc": {
        "label": "EEOC (recruitment advertising)",
        "why": ("Age, sex, race, national-origin and disability language in "
                "job advertising is federally regulated (Title VII, ADEA, "
                "ADA) and the specifics turn on the actual wording used, "
                "not a keyword list this file could publish responsibly. "
                "Not modeled -- a recruitment spot's own copy should still "
                "go past a human who can read it for this."),
    },
    "fair_housing": {
        "label": "Fair Housing Act (housing advertising)",
        "why": ("42 U.S.C. 3604(c) reaches the same way, for the same "
                "reason: real estate and rental advertising copy needs a "
                "human read, not a keyword list. Not modeled."),
    },
}

# FTC deception law generally (15 U.S.C. Sec. 45) is the umbrella authority
# for a claim nobody can substantiate -- "best", "#1", "lowest" read as
# unqualified superlatives unless the spot can back them up, which this
# scanner has no way to check. Matched on a whole word so "bestseller" or
# "lowest-maintenance" (a compound adjective, not a claim) do not fire.
_SUPERLATIVE_RE = re.compile(
    r"\b(best|#\s?1|number\s+one|lowest|cheapest|guaranteed|fastest|"
    r"most\s+trusted)\b", re.IGNORECASE)

# "your competitor" / "the other guys" / "other companies" -- unnamed rather
# than a named brand, which is the FTC's own line between ordinary
# comparative advertising (naming a competitor and backing the claim) and a
# vague dig nobody can verify.
_COMPETITOR_RE = re.compile(
    r"\b(the\s+other\s+guys|other\s+companies|our\s+competitors?|"
    r"unlike\s+(?:the|our|other)\b)", re.IGNORECASE)


def _finding(rule_id, regime_label, headline, requires, evidence):
    return {
        "id": rule_id, "regime": rule_id, "regime_label": regime_label,
        "authority": "FTC (general deception standard)",
        "citation": "15 U.S.C. Sec. 45",
        "headline": headline, "requires": requires,
        "evidence": evidence, "addressed": None,
    }


def _text_of(script=None, brief=None, cta=None) -> str:
    """The same fields `compliance_spec._text_of` reads, kept local rather
    than imported -- that function is underscore-private, and copying five
    lines here is cheaper and safer than exporting a helper from a module
    this one only borrows one public function from."""
    parts = []
    for scene in (script or {}).get("scenes", []) or []:
        for key in ("voiceover", "visual", "on_screen_text", "text", "narration"):
            value = (scene or {}).get(key)
            if value:
                parts.append(str(value))
    for key in ("what_advertising", "offer", "key_message", "disclaimer",
               "legal_line", "notes"):
        value = (brief or {}).get(key)
        if value:
            parts.append(str(value))
    for key in ("headline", "subhead", "offer", "disclaimer", "legal_line"):
        value = (cta or {}).get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def scan_copy_quality(text: str) -> list[dict]:
    """Superlatives and unnamed-competitor language -- run on every scan,
    never gated on an industry the way the regulated regimes are, because
    an unqualified "best in town" is a finding on a plumber's spot exactly
    as much as a lawyer's."""
    out = []
    hit = _SUPERLATIVE_RE.search(text or "")
    if hit:
        out.append(_finding(
            "superlative", "Unsubstantiated superlative",
            "This copy makes an absolute claim.",
            "A claim like \"best\", \"#1\" or \"guaranteed\" needs something "
            "behind it -- an award, a ranking, a documented guarantee -- or "
            "it reads as deceptive under the FTC's general standard.",
            f"“{hit.group(0)}”"))
    hit = _COMPETITOR_RE.search(text or "")
    if hit:
        out.append(_finding(
            "unnamed_competitor", "Unnamed-competitor language",
            "This copy refers to a competitor without naming one.",
            "Comparative advertising is allowed when it names who it is "
            "comparing against and can back the claim; a vague dig at "
            "\"the other guys\" cannot be checked and reads as unfair to "
            "whoever it is about.",
            f"“{hit.group(0)}”"))
    return out


def scan(script=None, brief=None, cta=None, client=None, commercial_type="") -> dict:
    """The merged finding list -- the five regulated regimes plus the two
    copy-quality checks above, in one list a screen can render without
    knowing which module answered which row. Never raises, for the same
    reason `compliance_spec.scan()` never does: a scanning bug must not take
    down the panel it reports on."""
    try:
        from modules.commercial_builder import compliance_spec
        base = compliance_spec.scan(script=script, brief=brief, cta=cta,
                                    client=client, commercial_type=commercial_type)
    except Exception as exc:                                # noqa: BLE001
        base = {"findings": [], "regimes": [], "industry_known": False,
                "measured": False,
                "note": f"The compliance scan could not run: {exc}"}
    findings = list(base.get("findings") or [])
    findings.extend(scan_copy_quality(_text_of(script, brief, cta)))
    out = dict(base)
    out["findings"] = findings
    out["not_modeled"] = NOT_MODELED
    return out
