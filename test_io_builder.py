"""QA on the IO Builder itself — the model calls, and what a refused order files.

    python3 test_io_builder.py

Same shape as test_io_start.py: no pytest, no new dependencies, a temporary
data directory, a throwaway SQLite mirror and its own audit log, so it never
touches /var/data or the real one.

## Why this file exists

The IO Builder had no test of its own. Booting it and pressing its buttons
found two things, and both were the same kind of failure — a fix that landed
in one of two copies, and a record written before the thing it records had
happened.

  1. **Every AI button in the tool was dead, four different ways.** This
     module carried its own copy of the OpenAI Responses call, and the copy
     attached the hosted ``web_search`` tool to **every** request — the ZIP
     lookup that genuinely searches, and the three that have nothing to look
     up. Whether a hosted tool is available depends on the model, which is
     ``OPENAI_MODEL`` and is a 4o-class model on this deployment rather than
     the ``gpt-5-mini`` default written here, and a model that refuses the
     tool refuses the whole request. CLAUDE.md records that exact diagnosis
     against the Proposal Builder, whose copy was fixed; this one was never
     touched. There is one reader now, `hub/openai_responses.py`, and both
     builders read it.

  2. **A truncated answer was reported four ways, none of them the truth,
     and twice as a success.** Reasoning tokens count against
     ``max_output_tokens``, so an answer cut short arrives with an empty text
     body. The ZIP lookup called that "No ZIP Codes were returned" and the
     description "OpenAI returned no description" — wrong, but at least a
     failure. The landing-page review answered **200 with an empty review**,
     which the wizard stored and the internal PDF printed under a heading;
     and the media mix answered **200** with every field blank under a
     warning blaming the model for replying in prose.

  3. **The landing review asked a model to visit a page.** No model here can,
     so the answer was either a confident review of a page nobody had looked
     at or a paragraph about not being able to reach the site — and it is
     printed on the internal PDF, which is what whoever traffics the campaign
     reads. `modules/ads_builder/landing_page.py` fetches the page and counts
     the conversion points off the markup, and the Proposal Builder already
     reads it; so does this one now.

  4. **An IO the route refused was filed as submitted work.** The activity
     entry and the client registration ran at the *top* of the submit route,
     before the request was even validated, so a submit refused for missing
     documents still logged an order and still registered the client —
     `hub/io_reconcile.py` reads those entries, so an order nobody ever sent
     became a row on somebody's chase list. `hub/io_records.py` closed that
     independently while this sweep was running, and its `_keep()` is the
     answer kept here: nothing is written before the documents gate, and past
     it the order is recorded whether or not Suite took it, flagged either
     way. What this file adds is the assertion that the line stays there.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1io_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "io-builder-test-secret"
os.environ["OPENAI_API_KEY"] = "io-builder-test-key"
os.environ.pop("OPENAI_MODEL", None)

_passed, _failed = 0, 0


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


import modules.io_builder.app as io                                # noqa: E402
from hub import (audit, io_clients, io_records,                     # noqa: E402
                 openai_responses as responses)

client = io.app.test_client()
IO_SRC = (ROOT / "modules" / "io_builder" / "app.py").read_text()
SB_SRC = (ROOT / "modules" / "sales_builder" / "app.py").read_text()


class Resp:
    """Enough of a requests.Response for the reader to read."""

    def __init__(self, code, body):
        self.status_code, self._body = code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


ANSWER = {"status": "completed",
          "output": [{"content": [{"type": "output_text",
                                   "text": "46032, 46033"}]}]}
TRUNCATED = {"status": "incomplete",
             "incomplete_details": {"reason": "max_output_tokens"},
             "output": []}


# ---------------------------------------------------------------------------
section("One reader of the model call, for both builders")
# ---------------------------------------------------------------------------
check("the reader is shared rather than owned by either builder",
      callable(responses.ask))
check("the IO Builder reads it", "openai_responses" in IO_SRC)
check("and so does the Proposal Builder", "openai_responses" in SB_SRC)
# The copy is the bug: a hand-written payload in either module is how one of
# them comes to attach the tool again. Read through the **AST**, not the text —
# both files now explain this trap in prose, and a check that matches the
# explanation reports the fix as the defect (hub/config.py's drift rule).
import ast                                                        # noqa: E402


def _literals(src):
    return {n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


_io_lit, _sb_lit = _literals(IO_SRC), _literals(SB_SRC)
check("neither builder builds its own web_search payload any more",
      "web_search" not in _io_lit and "web_search" not in _sb_lit)
check("and neither reaches the endpoint on its own",
      not any("v1/responses" in v for v in _io_lit | _sb_lit))


# ---------------------------------------------------------------------------
section("The hosted tool is asked for, and its refusal is not the answer")
# ---------------------------------------------------------------------------
CALLS = []


def refuses_the_tool(payload, api_key):
    # A copy: the retry edits the payload it was handed, so a stub holding the
    # live reference records what the call ended up as rather than what it was.
    CALLS.append(json.loads(json.dumps(payload)))
    if "tools" in payload:
        return Resp(400, {"error": {"message":
                                    "Hosted tool 'web_search' is not supported."}})
    return Resp(200, ANSWER)


_real_post = responses.post
responses.post = refuses_the_tool
try:
    text = responses.ask("anything", module="io_builder", max_output_tokens=100)
    check("an ordinary call carries no search tool at all", "tools" not in CALLS[0])
    CALLS.clear()
    text = responses.ask("anything", module="io_builder", max_output_tokens=100,
                         search=True)
    check("a call that asks for search asks for it", "tools" in CALLS[0])
    check("and falls back without it rather than losing the answer",
          len(CALLS) == 2 and "tools" not in CALLS[1] and "46032" in text)

    CALLS.clear()
    responses.post = lambda p, k: (CALLS.append(dict(p)),
                                   Resp(401, {"error": {"message":
                                                        "Incorrect API key provided."}}))[1]
    try:
        responses.ask("anything", module="io_builder", search=True)
        check("a refused call raises", False)
    except RuntimeError as exc:
        check("and says what the API said, not just the status line",
              "Incorrect API key" in str(exc))
    check("a bad key is not asked the same question twice — only a 400 is the tool",
          len(CALLS), 1)

    responses.post = lambda p, k: Resp(200, TRUNCATED)
    try:
        responses.ask("anything", module="io_builder")
        check("an answer cut short raises", False)
    except RuntimeError as exc:
        check("and is named as that rather than as an empty answer",
              "max output tokens" in str(exc))
finally:
    responses.post = _real_post


# ---------------------------------------------------------------------------
section("The four AI buttons, against a model that will not take the tool")
# ---------------------------------------------------------------------------
# The landing review fetches the page, so it is exercised in its own section
# below: here are the three that only ask the model.
ASKED = []


def stub_ai(prompt, max_output_tokens=6000, search=False, purpose="io_builder"):
    ASKED.append({"purpose": purpose, "search": search, "prompt": prompt})
    return '{"summary": "Lead with display", "primary_product": "Display"}' \
        if purpose == "media_mix" else "46032, 46033, 46074"


_real_ai = io._openai_response
io._openai_response = stub_ai
try:
    z = client.post("/api/zipcodes-in-radius",
                    json={"origin": "Carmel, IN", "radius": "10"}).get_json()
    check("the ZIP lookup returns the list", z.get("count"), 3)
    check("and is the one call in this module that asks for live search",
          [a["search"] for a in ASKED if a["purpose"] == "zip_radius"], [True])

    ASKED.clear()
    d = client.post("/api/generate-business-description",
                    json={"urls": ["https://acme.example"]}).get_json()
    check("the business description comes back", bool(d.get("description")))
    check("and does not ask for search", ASKED[0]["search"], False)

    ASKED.clear()
    m = client.post("/api/media-mix-recommendation", json={"client": "Acme"}).get_json()
    check("the media mix comes back as structured JSON",
          (m.get("recommendation") or {}).get("primary_product"), "Display")
    check("and does not ask for search", ASKED[0]["search"], False)

    # Every call in this module used to be filed under "business_description",
    # so the usage page could not tell a billed ZIP lookup from a billed
    # landing-page review.
    ASKED.clear()
    client.post("/api/zipcodes-in-radius", json={"origin": "Carmel, IN", "radius": "5"})
    client.post("/api/generate-business-description", json={"urls": ["https://a.example"]})
    client.post("/api/media-mix-recommendation", json={"client": "Acme"})
    check("each button files its spend under its own purpose",
          sorted(a["purpose"] for a in ASKED),
          ["business_description", "media_mix", "zip_radius"])
finally:
    io._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("A truncated answer never reads as a success")
# ---------------------------------------------------------------------------
def cut_short(prompt, max_output_tokens=6000, search=False, purpose="io_builder"):
    raise RuntimeError("The model stopped before it answered (max output tokens). "
                       "Nothing was returned to show.")


io._openai_response = cut_short
try:
    for path, body in (("/api/zipcodes-in-radius", {"origin": "Carmel, IN", "radius": "10"}),
                       ("/api/generate-business-description", {"urls": ["https://a.example"]}),
                       ("/api/media-mix-recommendation", {"client": "Acme"})):
        r = client.post(path, json=body)
        check(f"{path} refuses rather than answering emptily", r.status_code, 502)
        check("  and carries the reason the model gave",
              "stopped before it answered" in json.dumps(r.get_json()))
finally:
    io._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("The landing-page review reads the page rather than asking a model to")
# ---------------------------------------------------------------------------
from modules.ads_builder import landing_page as lp                 # noqa: E402

check("the headings line has one home, beside the shape it describes",
      callable(lp.headings_line))
check("and the Proposal Builder reads it rather than keeping a copy",
      "from modules.ads_builder.landing_page import headings_line" in SB_SRC)

_review_src = IO_SRC[IO_SRC.index("def review_landing_page"):
                     IO_SRC.index("def media_mix_recommendation")]
check("the review no longer asks a model to visit anything",
      "Visit the page" not in _review_src)
check("it reads the page through the module that already does it",
      "landing_page" in _review_src and "observe(url)" in _review_src)

OBSERVED = {
    "measured": True, "url": "https://acme.example/", "title": "Acme Roofing",
    "meta_description": "Roof repair in Carmel", "mobile_viewport": True,
    "headings": [{"level": "h1", "text": "Roof repair in Carmel"}],
    "conversion_points": [{"kind": "phone", "label": "Click-to-call",
                           "evidence": "tel:+13175550142"}],
    "text": "Call us for a free estimate.",
}
_real_observe = lp.observe
PROMPTS = []


def graded(prompt, max_output_tokens=6000, search=False, purpose="io_builder"):
    PROMPTS.append(prompt)
    return "CTA Status: one click-to-call."


lp.observe = lambda url, fetched=None: dict(OBSERVED)
io._openai_response = graded
try:
    r = client.post("/api/review-landing-page",
                    json={"url": "https://acme.example", "client": "Acme"})
    body = r.get_json()
    check("a page that was read is reviewed", r.status_code, 200)
    check("the reading is kept beside the judgment, not folded into it",
          (body.get("observed") or {}).get("measured"))
    check("and the model is handed what was actually on the page",
          "tel:+13175550142" in PROMPTS[0] and "free estimate" in PROMPTS[0])
    check("and told not to describe anything that is not in it",
          "do not contradict it" in PROMPTS[0])

    # A model that fails costs the judgment, never the reading: what was found
    # on the page is the checkable half and is worth showing on its own.
    io._openai_response = cut_short
    r = client.post("/api/review-landing-page", json={"url": "https://acme.example"})
    check("a failed review is a failure", r.status_code, 502)
    check("and the page reading survives it",
          (r.get_json().get("observed") or {}).get("measured"))

    # An empty review stored as a success is a heading with nothing under it
    # on the internal PDF — a page nobody had anything to say about, rather
    # than a review that never happened.
    io._openai_response = lambda *a, **k: "   "
    r = client.post("/api/review-landing-page", json={"url": "https://acme.example"})
    check("an empty review is refused rather than filed", r.status_code, 502)

    # Asking the model anyway is how a review of a 404 gets written onto a
    # trafficking document.
    lp.observe = lambda url, fetched=None: {"measured": False,
                                            "error": "The site answered 404."}
    io._openai_response = graded
    PROMPTS.clear()
    r = client.post("/api/review-landing-page", json={"url": "https://acme.example"})
    check("a page that could not be read is not reviewed anyway", r.status_code, 502)
    check("  and the model was never asked", PROMPTS, [])
    check("  and the reason names the site rather than our tooling",
          "404" in json.dumps(r.get_json()))
finally:
    lp.observe = _real_observe
    io._openai_response = _real_ai


# ---------------------------------------------------------------------------
section("Where the line falls between an order and an attempt")
# ---------------------------------------------------------------------------
# `_keep()` is `main`'s answer to the half of this the QA sweep also found:
# the activity entry, the client overlay and the order record all used to be
# written at the *top* of the route, before the request was even validated.
# What is asserted here is where the line now falls. Past the documents gate
# the client has an order whatever Suite goes on to do with it, so a refusal
# is recorded and flagged rather than dropped -- "delivered" and "built, and
# Suite refused it" are two things to do, not one. Before that gate nothing
# was built and nothing went, so there is nothing to record.
def submitted():
    return [e for e in audit.tail(limit=200, module="io_builder")
            if e.get("type") == "io_submitted"]


def registered():
    return sorted(io_clients.overlay().keys())


COMPLETE = {"client": "Never Sent LLC", "orderNumber": "99001",
            "salesContact": "Todd", "start": "2026-09-01",
            "items": [{"budget": 1000}],
            "client_pdf_url": "https://x/c.pdf",
            "internal_pdf_url": "https://x/i.pdf"}

# submit_io() reaches Suite through hub.suite_opportunity.push_proposal(),
# imported fresh inside the route the way every hub.* dependency in this file
# is -- so it is stubbed the way modules/commercial_builder/routes/suite.py's
# own tests already stub the same function: overwrite the attribute on the
# `hub` package itself, which is what a function-local `from hub import X`
# actually reads once X has been imported once. sys.modules rather than a
# second `import hub` alongside the `from hub import (...)` above.
from hub import suite_opportunity as _real_suite_opportunity        # noqa: E402
_hub_pkg = sys.modules["hub"]
_push_calls = []


class _StubSuite:
    configured = staticmethod(lambda: True)
    status = staticmethod(lambda: {"ok": True, "problems": []})

    @staticmethod
    def push_proposal(**kwargs):
        _push_calls.append(kwargs)
        return _StubSuite.answer


_StubSuite.answer = {"ok": False, "reason": "down"}
_hub_pkg.suite_opportunity = _StubSuite
try:
    # 1. The rep pressed Submit before generating both PDFs. Nothing was
    #    built and nothing went, so nothing is written down -- otherwise
    #    hub/io_reconcile.py chases a campaign for an order nobody placed.
    half = dict(COMPLETE)
    half.pop("internal_pdf_url")
    r = client.post("/api/submit-io", json=half)
    check("a submission with one PDF missing is refused", r.status_code, 400)
    check("  and files no order", submitted(), [])
    check("  and registers no client", registered(), [])
    check("  and writes no order record",
          io_records.get("99001") is None)
    check("  and Suite is never even asked", _push_calls, [])

    # 2. The documents exist and Suite refuses. The client has an order.
    r = client.post("/api/submit-io", json=COMPLETE)
    check("a Suite push that refuses is refused back", r.status_code, 502)
    rec = io_records.get("99001") or {}
    check("  but the order is written down anyway", rec.get("order"), "99001")
    check("  marked as one Suite has never taken",
          (rec.get("suite") or {}).get("ever_delivered"), False)
    check("  and the activity entry says so rather than reading as delivered",
          [e.get("delivered") for e in submitted()], [False])

    # 3. It went.
    _StubSuite.answer = {"ok": True, "opportunity_id": "opp_1",
                         "contact": {"id": "c_1"}, "created": True}
    r = client.post("/api/submit-io", json=COMPLETE)
    check("an order Suite took is accepted", r.status_code, 200)
    check("  with the opportunity id back on the response",
          r.get_json().get("suite_opportunity_id"), "opp_1")
    rec = io_records.get("99001") or {}
    check("  and the same order is updated rather than filed twice",
          (rec.get("suite") or {}).get("ever_delivered"), True)
    check("  the opportunity id is kept on the Hub's own record",
          rec.get("suite_opportunity_id"), "opp_1")
    rows = submitted()
    check("  the newest entry is the delivered one",
          rows[0].get("delivered"), True)
    check("  under the key the browser actually posts",
          rows[0].get("order"), "99001")
    check("  with what a chase list needs beside the number",
          [rows[0].get("client"), rows[0].get("start"), rows[0].get("monthly")],
          ["Never Sent LLC", "2026-09-01", 1000.0])
    check("  and the client registered", registered(), ["never sent"])

    # 4. A correction is sent. It must update opp_1, not open a second deal --
    #    GoHighLevel has no natural key for "the opportunity this IO made",
    #    so the Hub's own record of the last successful push is what supplies
    #    it back.
    client.post("/api/submit-io", json=COMPLETE)
    check("  a resubmission is handed back its own opportunity id",
          _push_calls[-1].get("opportunity_id"), "opp_1")

    # 5. Suite has no contact to attach the opportunity to. Not an error --
    #    the order is still recorded and both PDFs still exist.
    _StubSuite.answer = {"ok": False, "needs_contact": True,
                         "reason": "No Smart 1 Suite contact matches this client."}
    r = client.post("/api/submit-io", json=COMPLETE)
    check("a Suite push needing a contact is not a failure", r.status_code, 200)
    check("  and says so rather than reading as delivered",
          r.get_json().get("delivered_to_suite"), False)
    check("  naming what is missing",
          "contact" in r.get_json().get("message", ""))

    # 6. Suite is not configured at all -- the same graceful, non-blocking
    #    shape the retired webhook's "unconfigured" branch had.
    _StubSuite.configured = staticmethod(lambda: False)
    _StubSuite.status = staticmethod(
        lambda: {"ok": False, "problems": ["No Suite token — set GHL_PRIVATE_TOKEN."]})
    r = client.post("/api/submit-io", json=COMPLETE)
    check("an unconfigured Suite still finishes the submit", r.status_code, 200)
    check("  recorded, not delivered",
          r.get_json().get("delivered_to_suite"), False)
    check("  naming the missing credential",
          "GHL_PRIVATE_TOKEN" in r.get_json().get("message", ""))
finally:
    _hub_pkg.suite_opportunity = _real_suite_opportunity


# ---------------------------------------------------------------------------
section("The rest of the tool still answers")
# ---------------------------------------------------------------------------
check("the page renders", client.get("/").status_code, 200)
check("health reports what is configured",
      client.get("/health").get_json().get("status"), "ok")
check("the spec kit is served rather than restated in JavaScript",
      "channels" in client.get("/api/creative-specs").get_json())
check("a client with nothing asked for is a real answer",
      client.get("/api/spec-in").get_json(), {"fields": {}, "products": [], "ask": []})
check("an unfinished IO can be listed",
      client.get("/api/drafts").get_json().get("measured"))
check("a draft nobody saved is not found",
      client.get("/api/draft/nope").status_code, 404)

# The order counter goes through hub.storage, and no longer writes a temporary
# file on the way past that nothing ever read.
_counter = IO_SRC[IO_SRC.index("def _write_cloudinary_order_counter"):
                  IO_SRC.index("def _temporary_counter_path")]
check("the counter write goes through hub.storage", "storage.put(" in _counter)
check("and writes no temp file nothing reads", "NamedTemporaryFile" not in _counter)

# Every /api/ path the page rewrites onto the mount is derived from the route
# table, so a route added here cannot be forgotten and sent to the hub app.
_own = io._own_api_paths()
check("the page's own API list is derived rather than hand-written",
      "/api/submit-io" in _own and "/api/review-landing-page" in _own)
check("and does not claim the hub's routes", "/api/io/" in _own, False)

print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
