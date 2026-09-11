"""Every OpenAI call site routes through hub.ai.

    python3 test_ai_callers.py

Same shape as the other test files here — no pytest, no new dependencies.

`hub.client_brief.check_ai_callers()` walks `hub/` and `modules/` and flags
any file that imports `openai`, builds an `OpenAI(...)` client, calls
`.chat.completions.create` / `.responses.create` / `.images.generate`, or
names `/v1/chat/completions` or `api.openai.com` in a string literal, outside
`hub/ai.py` and the small, reasoned `ALLOW` list. A file that slips past this
gets no client brief injected and writes no usage row — the exact gap
`hub/client_brief.py` exists to close.

This is the sweep for it: a test naming the fourteen call sites this work
order fixed proves nothing about the fifteenth. It is registered in
`test_ci_gate.py` so a new direct call fails CI.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1aicallers_test_")

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub.client_brief import check_ai_callers, ALLOW               # noqa: E402


# ---------------------------------------------------------------------------
section("The repository, as it stands")
# ---------------------------------------------------------------------------

live_findings = check_ai_callers()
check("no file in hub/ or modules/ calls OpenAI directly, outside ALLOW",
      [f["file"] for f in live_findings if not f.get("allow_entry")], [])

check("every ALLOW entry carries a reason",
      [f["file"] for f in live_findings if f.get("allow_entry")], [])


# ---------------------------------------------------------------------------
section("hub/ai.py and hub/client_brief.py are exempt by name, not by luck")
# ---------------------------------------------------------------------------

check("hub/ai.py is in ALLOW", "hub/ai.py" in ALLOW, True)
check("hub/client_brief.py is in ALLOW", "hub/client_brief.py" in ALLOW, True)


# ---------------------------------------------------------------------------
section("A fixture that calls OpenAI directly is flagged")
# ---------------------------------------------------------------------------

FIXTURE_ROOT = os.path.join(TMP, "fixture_bad")
os.makedirs(os.path.join(FIXTURE_ROOT, "hub"), exist_ok=True)
os.makedirs(os.path.join(FIXTURE_ROOT, "modules", "widget"), exist_ok=True)

BAD_RAW_HTTP = '''
import os
import requests

def write_copy(prompt):
    key = os.environ.get("OPENAI_API_KEY")
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": "gpt-4o-mini", "messages": [
                          {"role": "user", "content": prompt}]})
    return r.json()
'''

BAD_SDK = '''
from openai import OpenAI

def write_copy(prompt):
    client = OpenAI(api_key="x")
    resp = client.chat.completions.create(model="gpt-4o-mini",
                                          messages=[{"role": "user", "content": prompt}])
    return resp.choices[0].message.content
'''

BAD_IMAGE = '''
from openai import OpenAI

def make_image(prompt):
    client = OpenAI(api_key="x")
    return client.images.generate(model="gpt-image-1", prompt=prompt)
'''

with open(os.path.join(FIXTURE_ROOT, "modules", "widget", "app.py"), "w") as fh:
    fh.write(BAD_RAW_HTTP)

findings = check_ai_callers(FIXTURE_ROOT)
check("a raw requests.post to /v1/chat/completions is flagged",
      [f["file"] for f in findings], ["modules/widget/app.py"])

with open(os.path.join(FIXTURE_ROOT, "modules", "widget", "app.py"), "w") as fh:
    fh.write(BAD_SDK)
findings = check_ai_callers(FIXTURE_ROOT)
check("an OpenAI() SDK client with .chat.completions.create is flagged",
      [f["file"] for f in findings], ["modules/widget/app.py"])

with open(os.path.join(FIXTURE_ROOT, "modules", "widget", "app.py"), "w") as fh:
    fh.write(BAD_IMAGE)
findings = check_ai_callers(FIXTURE_ROOT)
check("an OpenAI() SDK client with .images.generate is flagged",
      [f["file"] for f in findings], ["modules/widget/app.py"])


# ---------------------------------------------------------------------------
section("The same file, once it imports hub.ai instead, clears")
# ---------------------------------------------------------------------------

GOOD = '''
from hub import ai as hub_ai

def write_copy(prompt, client):
    return hub_ai.chat([{"role": "user", "content": prompt}],
                       module="widget", purpose="copy", client=client)
'''

with open(os.path.join(FIXTURE_ROOT, "modules", "widget", "app.py"), "w") as fh:
    fh.write(GOOD)
findings = check_ai_callers(FIXTURE_ROOT)
check("a file that only calls hub.ai.chat() is clean", findings, [])


# ---------------------------------------------------------------------------
section("A docstring explaining the fix is not a call site")
# ---------------------------------------------------------------------------

PROSE_ONLY = '''
"""This module used to post straight to api.openai.com/v1/chat/completions
and call OpenAI(api_key=...).chat.completions.create(...) directly. It now
routes through hub.ai instead."""

from hub import ai as hub_ai


def write_copy(prompt, client):
    return hub_ai.chat([{"role": "user", "content": prompt}],
                       module="widget", purpose="copy", client=client)
'''
with open(os.path.join(FIXTURE_ROOT, "modules", "widget", "app.py"), "w") as fh:
    fh.write(PROSE_ONLY)
findings = check_ai_callers(FIXTURE_ROOT)
check("a docstring quoting the old call, with no real call site, is clean",
      findings, [])


# ---------------------------------------------------------------------------
section("A top-level test fixture string is not scanned")
# ---------------------------------------------------------------------------
# check_ai_callers() walks only hub/ and modules/, the same two folders
# client_brand._log_call_sites() walks — a fixture string embedded in a
# top-level test_*.py file (this repo's own test_api_usage.py does exactly
# this, to test a different check) must not be reported.

os.makedirs(os.path.join(FIXTURE_ROOT, "hub"), exist_ok=True)
with open(os.path.join(FIXTURE_ROOT, "test_something.py"), "w") as fh:
    fh.write('FIXTURE = "requests.post(\'https://api.openai.com/v1/chat/completions\')"')
with open(os.path.join(FIXTURE_ROOT, "hub", "unrelated.py"), "w") as fh:
    fh.write("x = 1\n")
findings = check_ai_callers(FIXTURE_ROOT)
check("a top-level test_*.py fixture string is not scanned", findings, [])


# ---------------------------------------------------------------------------
section("An ALLOW entry with no reason is itself a finding")
# ---------------------------------------------------------------------------

_saved_allow = dict(ALLOW)
try:
    ALLOW["hub/some_file.py"] = ""
    findings = check_ai_callers(FIXTURE_ROOT)
    check("an empty-reason ALLOW entry is flagged",
          [f["file"] for f in findings if f.get("allow_entry")],
          ["hub/some_file.py"])
finally:
    ALLOW.clear()
    ALLOW.update(_saved_allow)


shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
