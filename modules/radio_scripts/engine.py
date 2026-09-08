"""Writing the scripts: one call, one budget-driven fix pass, then flags.

Nothing here trusts the model further than it has to. `hub/openai_responses.py`
is the one reader of the Responses API -- the reason two builders each shipped
the identical "the hosted tool refuses the whole request" bug is that each
carried its own copy of this call, and the fix landed in one of them. This
module is a third caller of the same shared reader rather than a fourth copy.

Three things are enforced in code rather than merely asked for in the prompt,
because a prompt is a request and "the model was told to" is not evidence
that it did:

* **The word budget is a hard gate.** A script outside `engine_spec.WORD_BUDGETS`
  is sent back for ONE targeted rewrite, naming exactly which length missed
  and by how much. Still outside budget after that, it is kept and flagged --
  never silently trimmed, which would cut the end of a sentence, and never
  regenerated a second time, which would cost a second call for a script a
  rep may prefer to hand-edit anyway.

* **The legal line is never written by the model.** It is forced empty here
  regardless of what came back, because an AI-written disclaimer on a
  homebuilder's spot is a liability question for a person, not a guess for a
  model to make.

* **Market/team/show coverage and invented claims are checked, not trusted.**
  Coverage (does at least one concept actually say the market, the team, or a
  named local show) is a plain substring test against the brief -- the same
  discipline the Playbook's podcast matching already learned the hard way.
  Price, phone, deadline, placeholder and superlative checks are
  `hub.social_plan.validate_copy`, imported rather than restated, because a
  second regex for "does this claim a price nobody supplied" is the drift
  this codebase keeps having to undo.
"""
from __future__ import annotations

import json
import re

from hub import openai_responses as _responses

from . import engine_spec as spec
from . import prompts


def _openai_call(payload, api_key):
    """The transport, kept as this module's own name so a test can stand in
    front of it without reaching the network."""
    return _responses.post(payload, api_key)


def _ask(prompt: str, *, max_output_tokens: int = 4000, call=None) -> str:
    return _responses.ask(prompt, module="radio_scripts", purpose="script_draft",
                          max_output_tokens=max_output_tokens,
                          call=call or _openai_call)


def _json_from_ai(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    return json.loads(cleaned)


def count_words(text: str) -> int:
    return len(str(text or "").split())


def _empty_concept() -> dict:
    return {"idea": "", "talent_direction": "", "sfx_notes": "",
            "scripts": {k: "" for k in spec.LENGTHS},
            "tag": {"name": "", "offer": "", "cta": "",
                    "phone_spoken": "", "url_spoken": ""},
            "legal_line": "", "legal_line_label": "Supplied by client",
            "flags": []}


def _coerce_concepts(data: dict) -> list[dict]:
    """The model's answer, read defensively rather than trusted whole.

    Raises with a message a caller can show, rather than letting a KeyError
    or a TypeError from three levels of nested access read as "the AI is
    broken" with no way to tell what actually came back malformed.
    """
    raw = data.get("concepts")
    if not isinstance(raw, list) or not raw:
        raise ValueError("The model did not return any concepts.")
    out = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        concept = _empty_concept()
        concept["idea"] = str(item.get("idea") or "").strip()[:200]
        concept["talent_direction"] = str(item.get("talent_direction") or "").strip()[:600]
        concept["sfx_notes"] = str(item.get("sfx_notes") or "").strip()[:600]
        scripts = item.get("scripts") or {}
        for length in spec.LENGTHS:
            concept["scripts"][length] = str(scripts.get(length) or "").strip()
        tag = item.get("tag") or {}
        for key in ("name", "offer", "cta", "phone_spoken", "url_spoken"):
            concept["tag"][key] = str(tag.get(key) or "").strip()[:300]
        # The legal line is never taken from the model, whatever it wrote --
        # see the module docstring. Forced here rather than merely ignored,
        # so a caller reading concept["legal_line"] can never see AI text.
        concept["legal_line"] = ""
        out.append(concept)
    if not out:
        raise ValueError("The model returned no readable concepts.")
    return out


def _budget_misses(concepts: list[dict]) -> list[dict]:
    misses = []
    for i, concept in enumerate(concepts):
        for length in spec.LENGTHS:
            text = concept["scripts"].get(length, "")
            words = count_words(text)
            low, high = spec.WORD_BUDGETS[length]
            if text and not (low <= words <= high):
                misses.append({"concept_index": i, "length": length,
                              "words": words, "text": text})
    return misses


def _apply_fixes(concepts: list[dict], fixes: list[dict]) -> None:
    for fix in fixes or []:
        try:
            idx = int(fix.get("concept_index"))
            length = str(fix.get("length") or "")
        except (TypeError, ValueError):
            continue
        if length not in spec.LENGTHS or not (0 <= idx < len(concepts)):
            continue
        text = str(fix.get("text") or "").strip()
        if text:
            concepts[idx]["scripts"][length] = text


def _coverage_terms(brief: dict) -> list[str]:
    terms = [str(brief.get("market") or "").strip(),
             str(brief.get("team") or "").strip()]
    terms += [str(s).strip() for s in (brief.get("local_shows") or [])]
    return [t for t in terms if t]


def _coverage_ok(concepts: list[dict], brief: dict) -> bool:
    terms = _coverage_terms(brief)
    if not terms:
        # Nothing was supplied to check coverage against -- the brief itself
        # carried no market, team or show name, so there is no claim this
        # rule can make about the copy.
        return True
    combined = " ".join(
        concept.get("scripts", {}).get(length, "")
        for concept in concepts for length in spec.LENGTHS).lower()
    return any(term.lower() in combined for term in terms)


def _facts_for(brief: dict) -> dict:
    """What `hub.social_plan.validate_copy` treats as already authorized --
    a claim traced back to something a person actually typed in the brief."""
    return {"offers": str(brief.get("offer") or ""),
           "phone": str(brief.get("phone") or ""),
           "url": str(brief.get("url") or ""),
           "notes": " ".join(_coverage_terms(brief))}


def _annotate_flags(concepts: list[dict], brief: dict, misses_after_fix: list[dict]) -> None:
    from hub.social_plan import validate_copy

    still_over = {(m["concept_index"], m["length"]) for m in misses_after_fix}
    facts = _facts_for(brief)
    for i, concept in enumerate(concepts):
        flags = []
        for length in spec.LENGTHS:
            text = concept["scripts"].get(length, "")
            if not text:
                continue
            if (i, length) in still_over:
                low, high = spec.WORD_BUDGETS[length]
                flags.append({"level": "warn", "code": "word_budget",
                             "length": length,
                             "message": f":{length} is {count_words(text)} words "
                                        f"against a {low}-{high} budget."})
            for flag in validate_copy(text, facts=facts):
                flags.append({**flag, "length": length})
        concept["flags"] = flags


def generate(brief: dict, *, call=None) -> dict:
    """One brief in, three concepts out. Never raises past a readable message."""
    prompt = prompts.build_prompt(brief)
    text = _ask(prompt, call=call)
    try:
        data = _json_from_ai(text)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("The model's answer could not be read as JSON.") from exc
    concepts = _coerce_concepts(data)

    misses = _budget_misses(concepts)
    if misses:
        try:
            fix_text = _ask(prompts.build_fix_prompt(brief, misses),
                            max_output_tokens=2000, call=call)
            fix_data = _json_from_ai(fix_text)
            _apply_fixes(concepts, fix_data.get("fixes") or [])
        except (RuntimeError, ValueError, TypeError):
            # A failed fix pass is not a failed generation -- the original
            # scripts stand and are flagged below rather than the whole
            # set being thrown away over one follow-up call.
            pass
    misses_after = _budget_misses(concepts)

    _annotate_flags(concepts, brief, misses_after)

    top_flags = []
    if not _coverage_ok(concepts, brief):
        top_flags.append({"level": "warn", "code": "coverage",
                          "message": "None of the three concepts mention the "
                                     "market, the team or a local show — "
                                     "check they landed."})

    return {"concepts": concepts, "flags": top_flags}


def regenerate_concept(brief: dict, concepts: list[dict], index: int,
                       *, call=None) -> list[dict]:
    """Redo one concept in place, leaving the other two untouched."""
    if not (0 <= index < len(concepts)):
        raise ValueError("No such concept.")
    result = generate(brief, call=call)
    fresh = result["concepts"][0] if result["concepts"] else _empty_concept()
    concepts = list(concepts)
    concepts[index] = fresh
    return concepts


__all__ = ["generate", "regenerate_concept", "count_words"]
