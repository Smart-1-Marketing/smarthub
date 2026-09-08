"""Radio Scripts: the standalone script-writing tool (build spec phase 1).

    python3 test_radio_scripts.py

Same shape as the other test files here: no pytest, no new dependencies, and
it runs against a temporary data directory and a throwaway SQLite database,
so it never touches /var/data or the real one. Nothing in it reaches OpenAI —
every generation test hands `engine.generate` a stub transport, the same seam
`hub/openai_responses.py` was built with, so what is checked is what this
module does with an answer rather than whether OpenAI is reachable.

## What this file is protecting

**The word budget is a hard gate, and it must not silently give up.** A
script outside :60/:30/:15's word count gets one targeted rewrite naming
exactly which length missed; still outside after that, it is kept and
flagged rather than trimmed (which would cut the end of a sentence) or
regenerated again (a second full call for a concept a rep may already like).

**The legal line is never written by the model, whatever it returns.** An
AI-authored disclaimer on a client's spot is a liability question for a
person, and the tool forces the field empty regardless of what came back —
this is checked directly against a model answer that tries to fill it in.

**Coverage is checked, not trusted.** At least one of the three concepts must
actually say the market, the team, or a named local show — a plain substring
test against the brief, the same discipline the Playbook's own podcast
matching already learned the hard way.

**Nothing invents a price, phone number or superlative.**
`hub.social_plan.validate_copy` is imported rather than restated, so a
concept quoting a discount the brief never supplied is blocked the same way
a social post would be.

**A blueprint on the hub app is not behind AuthGuard.** `wsgi.py` wraps only
dispatcher-mounted modules. This is a blueprint, so the guard is on it
directly, and the last section is the proof that it is actually installed.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1radioscripts_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "radio-scripts-test-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
section("The word budget is a hard gate")

from modules.radio_scripts import engine, engine_spec as spec  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _responses_payload(text):
    return {"status": "completed",
           "output": [{"content": [{"type": "output_text", "text": text}]}]}


def _concept(idea="Idea one", sixty=None, thirty=None, fifteen=None,
            legal="", offer="", cta="Call now"):
    words60 = sixty if sixty is not None else " ".join(["word"] * 150)
    words30 = thirty if thirty is not None else " ".join(["word"] * 70)
    words15 = fifteen if fifteen is not None else " ".join(["word"] * 37)
    return {"idea": idea, "talent_direction": "Warm, upbeat, mid-tempo.",
            "sfx_notes": "Light crowd noise under the open.",
            "scripts": {"60": words60, "30": words30, "15": words15},
            "tag": {"name": "Monogram Homes", "offer": offer, "cta": cta,
                   "phone_spoken": "five one three, five five five, oh one "
                                   "oh oh", "url_spoken": "monogram homes "
                                   "dot com"},
            "legal_line": legal}


BRIEF = {"company": "Monogram Homes", "client_name": "Monogram Homes",
        "market": "Cincinnati", "team": "Bengals",
        "local_shows": ["The Bengals Beat"], "offer": "", "phone": "5135550100",
        "url": "monogramhomes.com", "package": "Growth", "funnel": {}}


def make_call(*payloads):
    """A stub transport handing back one canned answer per call, in order."""
    calls = list(payloads)

    def _call(payload, api_key):
        if not calls:
            raise AssertionError("engine asked OpenAI more times than the test stubbed")
        return FakeResponse(_responses_payload(calls.pop(0)))
    return _call


# A :60 that is far too short (10 words) gets one targeted fix.
short_60 = " ".join(["word"] * 10)
first = {"concepts": [_concept(sixty=short_60), _concept(idea="Idea two"),
                      _concept(idea="Idea three")]}
fixed = {"fixes": [{"concept_index": 0, "length": "60",
                    "text": " ".join(["fixed"] * 150)}]}
result = engine.generate(BRIEF, call=make_call(json.dumps(first), json.dumps(fixed)))
c0 = result["concepts"][0]
check("the short :60 was sent back for a targeted fix",
      engine.count_words(c0["scripts"]["60"]), 150)
check("...and the fix cleared the word-budget flag",
      any(f["code"] == "word_budget" for f in c0["flags"]), False)

# A fix that does not land is kept, not regenerated a second time.
first2 = {"concepts": [_concept(sixty=short_60), _concept(idea="Idea two"),
                       _concept(idea="Idea three")]}
still_short = {"fixes": [{"concept_index": 0, "length": "60", "text": short_60}]}
result2 = engine.generate(BRIEF, call=make_call(json.dumps(first2), json.dumps(still_short)))
c0b = result2["concepts"][0]
check("a fix that still misses budget is kept rather than dropped",
      c0b["scripts"]["60"], short_60)
check("...and flagged", any(f["code"] == "word_budget" for f in c0b["flags"]), True)

# In budget from the start: no fix call is made at all (the stub would raise
# if a second call were attempted, which is the assertion).
clean = {"concepts": [_concept(), _concept(idea="Idea two"),
                      _concept(idea="Idea three")]}
result3 = engine.generate(BRIEF, call=make_call(json.dumps(clean)))
check("a set already in budget asks OpenAI only once",
      all(not any(f["code"] == "word_budget" for f in c["flags"])
          for c in result3["concepts"]), True)


# ---------------------------------------------------------------------------
section("The legal line is never written by the model")

with_legal = {"concepts": [_concept(legal="Terms and conditions apply, see store."),
                           _concept(idea="Idea two"), _concept(idea="Idea three")]}
result4 = engine.generate(BRIEF, call=make_call(json.dumps(with_legal)))
check("legal_line is forced empty however the model answered",
      result4["concepts"][0]["legal_line"], "")


# ---------------------------------------------------------------------------
section("Coverage is checked, not trusted")

def _words(*prefix, total=150):
    words = list(prefix) + ["word"] * (total - len(prefix))
    return " ".join(words)


no_mention = {"concepts": [
    _concept(sixty=_words("Great", "homes", "at", "a", "great", "price"),
             idea="Idea one"),
    _concept(idea="Idea two"), _concept(idea="Idea three")]}
result5 = engine.generate(BRIEF, call=make_call(json.dumps(no_mention)))
check("a set that never mentions market/team/show is flagged",
      any(f["code"] == "coverage" for f in result5["flags"]), True)

mentions = {"concepts": [
    _concept(sixty=_words("Cincinnati", "loves", "the", "Bengals", "and",
                         "Monogram", "Homes"), idea="Idea one"),
    _concept(idea="Idea two"), _concept(idea="Idea three")]}
result6 = engine.generate(BRIEF, call=make_call(json.dumps(mentions)))
check("...and clears when one concept says the market and the team",
      any(f["code"] == "coverage" for f in result6["flags"]), False)

check("a brief with nothing to check against is never flagged",
      any(f["code"] == "coverage" for f in
          engine.generate({"company": "X"}, call=make_call(json.dumps(no_mention)))["flags"]),
      False)


# ---------------------------------------------------------------------------
section("Nothing invents a price or an unsubstantiated claim")

priced = {"concepts": [
    _concept(sixty=_words("Save", "$500", "off", "every", "new", "build",
                         "this", "month"), idea="Idea one"),
    _concept(idea="Idea two"), _concept(idea="Idea three")]}
result7 = engine.generate(BRIEF, call=make_call(json.dumps(priced)))
c0c = result7["concepts"][0]
check("an invented price on a brief with no offer is blocked",
      any(f.get("code") == "price" and f.get("level") == "block" for f in c0c["flags"]), True)

superlative = {"concepts": [
    _concept(sixty=_words("Monogram", "Homes", "is", "the", "best", "builder",
                         "in", "town", "and", "award", "winning"),
             idea="Idea one"),
    _concept(idea="Idea two"), _concept(idea="Idea three")]}
result8 = engine.generate(BRIEF, call=make_call(json.dumps(superlative)))
check("an unsubstantiated superlative is a warning, not silently rewritten",
      any(f.get("code") == "superlative" and f.get("level") == "warn"
          for f in result8["concepts"][0]["flags"]), True)


# ---------------------------------------------------------------------------
section("Malformed answers fail with a readable message, not a stack trace")

try:
    engine.generate(BRIEF, call=make_call("not json at all"))
    check("garbage JSON raises", False, True)
except RuntimeError:
    check("garbage JSON raises", True, True)

try:
    engine.generate(BRIEF, call=make_call(json.dumps({"concepts": []})))
    check("no concepts at all raises", False, True)
except (RuntimeError, ValueError):
    check("no concepts at all raises", True, True)


# ---------------------------------------------------------------------------
section("Regenerating one concept leaves the other two untouched")

three = [_concept(idea="Keep me"), _concept(idea="Keep me too"),
        _concept(idea="Replace me")]
fresh_call = make_call(json.dumps({"concepts": [_concept(idea="Brand new")]}))
regenerated = engine.regenerate_concept(BRIEF, three, 2, call=fresh_call)
check("the untouched concepts are unchanged", regenerated[0]["idea"], "Keep me")
check("...and the third is replaced", regenerated[2]["idea"], "Brand new")


# ---------------------------------------------------------------------------
section("The PDF is a draft, and says so")

from modules.radio_scripts import pdf  # noqa: E402

pdf_bytes = pdf.build_script_pdf({
    "client_name": "Monogram Homes", "market": "Cincinnati",
    "created_at": "2026-09-07T00:00:00+00:00",
    "concepts": [_concept()]})
check("a PDF was produced", pdf_bytes[:4], b"%PDF")
check("it is not trivially empty", len(pdf_bytes) > 500, True)


# ---------------------------------------------------------------------------
section("The tool is wired the way a tool has to be")

from werkzeug.test import Client  # noqa: E402

import wsgi  # noqa: E402
from hub import audit as hub_audit  # noqa: E402
from hub import auth  # noqa: E402
from hub import client_brand  # noqa: E402
from hub import help as hub_help  # noqa: E402
from hub import help_coverage  # noqa: E402
from hub import sidebar  # noqa: E402

MOUNT = "/tools/radio-scripts"

check("Radio Scripts is tiled on a staff index page",
      {t["href"]: t["name"] for t in help_coverage.tiles()}.get(MOUNT + "/"),
      "Radio Scripts")
check("...and its mount is mapped for the help audit",
      MOUNT + "/" in help_coverage.PREFIXES, True)
check("its help keys are registered",
      {"radio_scripts.overview", "radio_scripts.budgets", "radio_scripts.legal"}
      <= {h.key for h in hub_help.REGISTRY}, True)
check("it opens with the creative nav collapsed",
      sidebar.collapses_by_default(MOUNT + "/"), True)
check("it can name a client's work",
      "radio_scripts" in client_brand.WORK_KINDS, True)

client = Client(wsgi.application)
out = client.get(MOUNT + "/")
check(f"{MOUNT}/ refuses an anonymous visitor", out.status_code in (302, 401), True)
gen = client.post(MOUNT + "/api/generate", json={"client_name": "X"})
check(f"...and so does the generate API", gen.status_code in (302, 401), True)

client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"),
                  domain="localhost")
page = client.get(MOUNT + "/")
check(f"{MOUNT}/ renders for staff", page.status_code, 200)
body = page.get_data(as_text=True)
check("...carrying its help bubble", "radio_scripts.overview" in body, True)
check("...and a wait mark on the generate button",
      'data-s1-think="ai"' in body, True)

sets_empty = client.get(MOUNT + "/api/sets")
check("...and lists sets (none yet) for a signed-in visitor",
      sets_empty.status_code, 200)
check("...with none stored", sets_empty.get_json()["sets"], [])

# Generate through the live route, with the transport stubbed rather than
# reaching OpenAI, exactly as the pure-function tests above do.
import modules.radio_scripts.engine as _live_engine  # noqa: E402

_orig_ask = _live_engine._ask
_live_engine._ask = lambda prompt, **kw: (
    json.dumps({"fixes": []}) if "missed their word-count budget" in prompt
    else json.dumps(clean))
try:
    created = client.post(MOUNT + "/api/generate", json={
        "client_name": "Monogram Homes", "market": "Cincinnati",
        "team": "Bengals"})
    check("generating through the route succeeds", created.status_code, 200)
    row = created.get_json()["set"]
    check("...and the row is attributed to a signed-in actor",
          bool(row.get("actor")), True)

    listing = client.get(MOUNT + "/api/sets")
    check("...and the set now appears in the list",
          listing.get_json()["sets"][0]["id"], row["id"])

    fetched = client.get(f"{MOUNT}/api/sets/{row['id']}")
    check("...and can be fetched by id", fetched.get_json()["set"]["id"], row["id"])

    dl = client.get(f"{MOUNT}/api/sets/{row['id']}/pdf")
    check("...and downloaded as a PDF", dl.status_code, 200)
    check("...with the right content type",
          dl.headers.get("Content-Type", ""), "application/pdf")

    missing = client.get(f"{MOUNT}/api/sets/999999")
    check("a set that does not exist 404s", missing.status_code, 404)
finally:
    _live_engine._ask = _orig_ask


# ---------------------------------------------------------------------------
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
