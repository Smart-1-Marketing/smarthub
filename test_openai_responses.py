"""hub/openai_responses.py -- the one OpenAI Responses reader, on its own.

    python3 test_openai_responses.py

Same shape as the other test files: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror.

## Why this file exists

This module's own docstring names three ways of failing and says each is
handled once here so "the next fix to it should land once." Nothing tested
that claim directly -- `test_io_builder.py` stubs `io._openai_response`
itself, one layer above this file, so every one of its checks is really
about what IO Builder's four call sites do with an exception that has
already been raised. The transport-level branching inside `ask()` -- what a
`400` with the search tool attached actually does, what an `incomplete`
status raises, what a non-JSON body raises -- had no test standing in front
of it at all.

**The fourth failure mode.** The three named in the docstring were "the
hosted tool", "a refusal" and "an answer cut short (status: incomplete)".
Reading the Proposal Builder's `/api/ai/rewrite` closely for this pass turned
up a fourth: a `status: "completed"` response whose output is empty is a
real shape the API can return, and `ask()` only refused an empty answer when
the status was specifically `"incomplete"`. Read that way, a rewrite came
back `{"ok": true, "text": ""}` -- a client-facing proposal section silently
blanked and reported as a successful rewrite, which is exactly the failure
this module's docstring already says "two of them read as success" about,
one shape further along. `ask()` now refuses on any empty text rather than
only the one status value, so every existing caller's `except Exception`
handling -- already written for the other three failures -- catches this
one the same way with no changes needed anywhere it is called from.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1openai_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "openai-responses-test-secret"
os.environ["OPENAI_API_KEY"] = "openai-responses-test-key"

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


from hub import openai_responses as r                               # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text or ""

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def completed(text, status="completed", extra=None):
    body = {"status": status,
            "output": [{"content": [{"type": "output_text", "text": text}]}]
                      if text else [],
            **(extra or {})}
    return FakeResponse(200, body)


# ---------------------------------------------------------------------------
section("An ordinary answer comes back as text")
# ---------------------------------------------------------------------------
calls = []


def send_ok(payload, api_key):
    calls.append(payload)
    return completed("46032, 46033")


check("the text is returned",
      r.ask("ZIPs near Carmel", module="test", call=send_ok), "46032, 46033")
check("the model and prompt travel in the payload",
      calls[0]["input"], "ZIPs near Carmel")
check("no api key is required by the caller, only the environment",
      "Authorization" not in calls[0])

calls.clear()
r.ask("hello", module="test", call=send_ok, search=True)
check("search=True attaches the hosted tool",
      calls[0].get("tools"), [{"type": "web_search"}])
calls.clear()
r.ask("hello", module="test", call=send_ok)
check("search=False (the default) attaches nothing",
      "tools" in calls[0], False)


# ---------------------------------------------------------------------------
section("The hosted tool's refusal is not the end of the answer")
# ---------------------------------------------------------------------------
attempts = []


def send_refuses_tool(payload, api_key):
    attempts.append(dict(payload))
    if payload.get("tools"):
        return FakeResponse(400, {"error": {"message": "web_search is not "
                                            "supported by this model"}})
    return completed("46032")


attempts.clear()
check("a 400 with the tool attached falls back and still answers",
      r.ask("zips", module="test", call=send_refuses_tool, search=True), "46032")
check("exactly two attempts were made -- with the tool, then without",
      len(attempts), 2)
check("the first attempt carried the tool", bool(attempts[0].get("tools")))
check("the second did not", "tools" in attempts[1], False)

attempts.clear()


def send_400_no_search(payload, api_key):
    attempts.append(payload)
    return FakeResponse(400, {"error": {"message": "bad request"}})


try:
    r.ask("hello", module="test", call=send_400_no_search)
    check("a 400 with no search requested still raises", False)
except RuntimeError as exc:
    check("a 400 with no search requested still raises", True)
    check("  naming the API's own message", "bad request" in str(exc))
check("and only one attempt was made -- retrying would not change a plain 400",
      len(attempts), 1)


# ---------------------------------------------------------------------------
section("A refusal carries the API's own sentence")
# ---------------------------------------------------------------------------
for code, body, text, wanted in (
    (401, {"error": {"message": "Incorrect API key provided"}}, "", "Incorrect API key provided"),
    (429, {"error": {"message": "Rate limit reached"}}, "", "Rate limit reached"),
    (500, None, "internal server error", "internal server error"),
):
    def send_error(payload, api_key, _code=code, _body=body, _text=text):
        return FakeResponse(_code, _body, _text)
    try:
        r.ask("hello", module="test", call=send_error)
        check(f"a {code} raises", False)
    except RuntimeError as exc:
        check(f"a {code} raises", True)
        check(f"  naming the reason ({wanted!r})", wanted in str(exc))
        check(f"  and the status code", str(code) in str(exc))

try:
    r.ask("hello", module="test",
         call=lambda p, k: FakeResponse(200, None, "not json at all"))
    check("a 200 that is not JSON raises", False)
except RuntimeError as exc:
    check("a 200 that is not JSON raises", True)
    check("  saying so rather than a bare traceback",
         "could not be read as JSON" in str(exc))


# ---------------------------------------------------------------------------
section("An answer cut short is said to be that")
# ---------------------------------------------------------------------------
def send_incomplete(payload, api_key):
    return completed("", status="incomplete",
                     extra={"incomplete_details": {"reason": "max_output_tokens"}})


try:
    r.ask("hello", module="test", call=send_incomplete)
    check("an incomplete, empty answer refuses rather than returning ''", False)
except RuntimeError as exc:
    check("an incomplete, empty answer refuses rather than returning ''", True)
    check("  and names why the model stopped",
         "stopped before it answered (max output tokens)" in str(exc))


# ---------------------------------------------------------------------------
section("Empty is never an answer, whatever the reported status")
# ---------------------------------------------------------------------------
# The gap this file exists to close: a status of "completed" with nothing in
# the output is a real shape, and the old guard only fired on "incomplete" --
# so this returned "" as a successful answer, which is how a Proposal
# Builder rewrite came back {"ok": true, "text": ""} and silently blanked a
# client-facing section while reporting a success.
def send_empty_but_completed(payload, api_key):
    return completed("")


try:
    r.ask("hello", module="test", call=send_empty_but_completed)
    check("a 'completed' response with no output text still refuses", False)
except RuntimeError as exc:
    check("a 'completed' response with no output text still refuses", True)
    check("  and does not claim the model stopped mid-answer",
         "stopped before it answered" not in str(exc))
    check("  naming the status it actually got",
         "completed" in str(exc))


def send_no_status(payload, api_key):
    return FakeResponse(200, {"output": []})


try:
    r.ask("hello", module="test", call=send_no_status)
    check("a response with no status field at all still refuses on empty text", False)
except RuntimeError:
    check("a response with no status field at all still refuses on empty text", True)


# ---------------------------------------------------------------------------
section("No key, no call")
# ---------------------------------------------------------------------------
_real_key = os.environ.pop("OPENAI_API_KEY", None)
never_called = []
try:
    r.ask("hello", module="test", call=lambda p, k: never_called.append(1))
    check("with no key, ask() refuses", False)
except RuntimeError as exc:
    check("with no key, ask() refuses", True)
    check("  before ever reaching the transport", never_called, [])
    check("  naming the variable to set", "OPENAI_API_KEY" in str(exc))
finally:
    if _real_key is not None:
        os.environ["OPENAI_API_KEY"] = _real_key
    else:
        os.environ["OPENAI_API_KEY"] = "test-key-for-remaining-checks"


# ---------------------------------------------------------------------------
section("Spend is recorded, and a broken recorder costs nothing")
# ---------------------------------------------------------------------------
from hub import ai as hub_ai                                        # noqa: E402
_real_note_usage = hub_ai.note_usage
_recorded = []
hub_ai.note_usage = lambda module, data, **kw: _recorded.append((module, kw.get("purpose")))
try:
    r.ask("hello", module="sales_builder", purpose="quote", call=send_ok)
    check("the module and purpose are recorded for the usage page",
          _recorded, [("sales_builder", "quote")])

    def _raises(*a, **k):
        raise RuntimeError("usage db is down")
    hub_ai.note_usage = _raises
    check("a broken usage recorder does not cost the caller its answer",
          r.ask("hello", module="test", call=send_ok), "46032, 46033")
finally:
    hub_ai.note_usage = _real_note_usage


# ---------------------------------------------------------------------------
section("The two builders share this reader rather than each answering it "
       "for themselves")
# ---------------------------------------------------------------------------
IO_SRC = (ROOT / "modules" / "io_builder" / "app.py").read_text()
SB_SRC = (ROOT / "modules" / "sales_builder" / "app.py").read_text()
RECOMPOSE_SRC = (ROOT / "modules" / "magic_resize" / "recompose.py").read_text()
check("the IO Builder reads it", "openai_responses" in IO_SRC)
check("the Proposal Builder reads it", "openai_responses" in SB_SRC)
check("and so does Magic Resize's layout proposal", "openai_responses" in RECOMPOSE_SRC)


print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
