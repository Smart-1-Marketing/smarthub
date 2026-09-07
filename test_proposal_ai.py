"""The Proposal Builder's three AI buttons, against a model that misbehaves.

    python3 test_proposal_ai.py

Same shape as the other test files: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror.

## Why this file exists

`test_io_builder.py` drove IO Builder's four AI buttons through a stubbed
model that refuses, that gets cut short, and that answers cleanly — and it
found real bugs doing it. `hub/openai_responses.py`'s own docstring says the
Proposal Builder's copy of that call was "found and fixed" first, and
CLAUDE.md repeats the claim. Nothing had actually driven the Proposal
Builder's three AI routes (`/api/ai/draft-proposal`, `/api/ai/rewrite`,
`/api/ai/draft-sections`) through the same scenarios — the only assertion
anywhere was a static one, that the source imports `hub.openai_responses`
and builds no `web_search` payload of its own. A source import proves the
route *can* reach the shared reader; it proves nothing about what the route
does with what comes back.

Reading `/api/ai/rewrite` for this pass is what turned up the gap
`hub/openai_responses.py` was changed for: a model call that returns an
empty string without raising made it all the way to
`{"ok": true, "text": ""}` — a client-facing proposal section replaced with
nothing, reported as a successful rewrite. Both the shared fix and this
route's own second guard on the same invariant are exercised here, from the
route inward, not from the shared reader outward.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1pbai_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "proposal-ai-test-secret"
os.environ["OPENAI_API_KEY"] = "proposal-ai-test-key"

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import modules.sales_builder.app as builder                        # noqa: E402
client = builder.app.test_client()
_real_ai = builder._openai_response

SECTION_STATE = {"client": "Riverside HVAC", "industry": "Home Services",
                 "sections": [{"id": "intro", "kind": "text", "title": "Introduction",
                               "enabled": True, "body": "Existing copy."}]}


# ---------------------------------------------------------------------------
section("A clean answer works, on all three buttons")
# ---------------------------------------------------------------------------
def stub_clean(prompt, max_output_tokens=6000, search=False):
    if "STRICT JSON" in prompt and "sections" in prompt:
        return '{"sections": [{"id": "intro", "body": "New, better copy."}]}'
    if "STRICT JSON" in prompt:
        return ('{"client": "Riverside HVAC", "url": "", "industry": "Home Services", '
                '"objectives": ["Lead Generation"], "geoType": "City/ZIP + Radius", '
                '"geo": "Carmel, IN", "budget": 5000, "months": 6, "categories": [], '
                '"rationale": "A steady lead-gen buy."}')
    return "A clearer, friendlier version of that paragraph."


builder._openai_response = stub_clean
try:
    r = client.post("/api/ai/draft-proposal",
                    json={"prompt": "HVAC company wants leads", "categories": []})
    check("draft-proposal returns ok", r.get_json().get("ok"), True)
    check("  with a parsed draft", (r.get_json().get("draft") or {}).get("client"),
          "Riverside HVAC")

    r = client.post("/api/ai/rewrite",
                    json={"text": "Old copy.", "instruction": "punch it up",
                         "section": "intro", "data": SECTION_STATE})
    check("rewrite returns ok", r.get_json().get("ok"), True)
    check("  with the rewritten text",
          r.get_json().get("text"), "A clearer, friendlier version of that paragraph.")

    r = client.post("/api/ai/draft-sections", json={"data": SECTION_STATE})
    check("draft-sections returns ok", r.get_json().get("ok"), True)
    check("  with the section written",
          r.get_json().get("sections", {}).get("intro"), "New, better copy.")
finally:
    builder._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("A refusal or a cut-short answer is refused back, not swallowed")
# ---------------------------------------------------------------------------
def stub_raises(prompt, max_output_tokens=6000, search=False):
    raise RuntimeError("The model stopped before it answered (max output "
                       "tokens). Nothing was returned to show.")


builder._openai_response = stub_raises
try:
    for path, body in (
        ("/api/ai/draft-proposal", {"prompt": "x", "categories": []}),
        ("/api/ai/rewrite", {"text": "Old copy.", "section": "intro", "data": SECTION_STATE}),
        ("/api/ai/draft-sections", {"data": SECTION_STATE}),
    ):
        r = client.post(path, json=body)
        check(f"{path} answers 502 rather than raising", r.status_code, 502)
        check("  carrying the model's own reason",
              "stopped before it answered" in json.dumps(r.get_json()))
finally:
    builder._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("The gap this file exists for: an empty answer with no exception")
# ---------------------------------------------------------------------------
# hub/openai_responses.ask() no longer lets this happen for real -- it raises
# on any empty text now, whatever the API's reported status. This stubs
# *below* that guard, at the same seam test_io_builder.py already stubs, to
# prove ai_rewrite() also refuses on its own rather than depending on nothing
# upstream of it ever changing. Two guards on one invariant, not one.
def stub_empty(prompt, max_output_tokens=6000, search=False):
    return ""


builder._openai_response = stub_empty
try:
    r = client.post("/api/ai/rewrite",
                    json={"text": "Real client-facing copy.", "section": "intro",
                         "data": SECTION_STATE})
    check("an empty rewrite is refused rather than accepted", r.status_code, 502)
    body = r.get_json()
    check("  and says so rather than answering ok:true with nothing in it",
          body.get("ok"), False)
    check("  naming the emptiness rather than a generic failure",
          "no rewritten text" in (body.get("error") or ""))

    # draft-sections is naturally safe here: an empty string is not valid
    # JSON, so _json_from_ai() raises before any section could be blanked.
    r = client.post("/api/ai/draft-sections", json={"data": SECTION_STATE})
    check("draft-sections is refused too, for the same underlying reason",
          r.status_code, 502)
    check("  and blanks no section on the way",
          "intro" not in (r.get_json().get("sections") or {}))

    r = client.post("/api/ai/draft-proposal", json={"prompt": "x", "categories": []})
    check("draft-proposal is refused as well", r.status_code, 502)
finally:
    builder._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("A rewrite that breaks a standing rule keeps the client's own text")
# ---------------------------------------------------------------------------
def stub_breaks_rule(prompt, max_output_tokens=6000, search=False):
    return "Ask about our Smart 1 Labs pricing for a guaranteed 10x ROI."


builder._openai_response = stub_breaks_rule
try:
    r = client.post("/api/ai/rewrite",
                    json={"text": "The client's real, unbroken copy.",
                         "section": "intro", "data": SECTION_STATE})
    body = r.get_json()
    check("a rewrite that breaks a rule still answers ok", body.get("ok"), True)
    check("  but hands back the original text, not the broken rewrite",
          body.get("text"), "The client's real, unbroken copy.")
    check("  and says why", bool(body.get("warnings")))
finally:
    builder._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("A rewrite needs text to work from")
# ---------------------------------------------------------------------------
r = client.post("/api/ai/rewrite", json={"text": "", "section": "intro", "data": {}})
check("an empty starting text is refused before any model call is made",
      r.status_code, 400)

# ensure_sections() seeds a full outline from hub.proposal_spec whenever
# "sections" is empty or absent, so an empty proposal is not how to reach
# zero *writable* sections -- naming a section id nothing has is.
r = client.post("/api/ai/draft-sections",
                json={"data": {"sections": []}, "section": "not-a-real-section-id"})
check("asking for a section that does not exist is refused before any model call",
      r.status_code, 400)


print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
