"""The next-action line at the top of Client 360 -- test harness.

    python3 test_next_action.py

hub/next_action.py orders what three cards Client 360 already fetches --
the health strip, Coming up and Pipeline & leads -- have already flagged,
worst first, into one sentence. It reads no new source and decides nothing
itself: an "IO ending" is `bad` because hub/client_upcoming.py already said
so, and this only orders what is already `bad` above what is already `warn`.

What this file holds, worst first:

  * The priority order itself: a dated deadline already `bad` beats a cold
    pipeline beats a `bad` health-queue item beats a `warn` deadline beats a
    `warn` health-queue item beats the email skill not being send-ready.
  * A client with nothing outstanding gets a real sentence saying so, in the
    `ok` state -- never silence and never the `bad`/`warn` states used for
    that a card's other pill.
  * If none of the three sources measured anything at all, the line answers
    `measured: False` rather than reading as "nothing to do".
  * The email-skill signal is read from the skill's own stored record --
    never a fresh probe -- and only fires when the skill is switched on.
  * The renderer, driven in node the way test_client360_health.py drives
    its own lifted block.
  * The route is under /api/client/ and refuses a stranger.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1nba_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "next-action-test"
os.environ["PANEL_PASSWORD"] = "next-action-pass"
os.environ["REPORT_CACHE"] = "off"

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


from hub import next_action as na                                # noqa: E402

# ------------------------------------------------------------ 1. priority order
section("1. Priority: worst-first, and each judgment is somebody else's")

IO_BAD = {"measured": True, "items": [
    {"kind": "io", "label": "Insertion order ends", "what": "IO 2261 · $1,200/mo",
     "days": 9, "state": "bad", "href": "/tools/io/"}]}
DOMAIN_WARN = {"measured": True, "items": [
    {"kind": "domain", "label": "Domain renews", "what": "acme.com",
     "days": 30, "state": "warn", "href": "/tools/seo-images/house"}]}
QUEUE_BAD = {"pills": [], "queue": [
    {"level": "bad", "section": "overview", "title": "1 proposal needs chasing"}]}
QUEUE_WARN = {"pills": [], "queue": [
    {"level": "warn", "section": "overview", "title": "Nothing done for this client in 95 days"}]}
PIPE_COLD = {"state": "connected", "totals": {"stale": 3}, "stale_days": 30,
             "suite_url": "/suite/?manage=loc1"}
PIPE_CLEAN = {"state": "connected", "totals": {"stale": 0}}
SKILLS_NOT_READY = {"email": {"active": True, "check": {"ready": False, "detail": "no from-address"}}}
SKILLS_READY = {"email": {"active": True, "check": {"ready": True}}}

out = na.pick("Acme Boats", health=QUEUE_WARN, upcoming=IO_BAD, pipeline=PIPE_COLD)
check("a bad deadline beats a cold pipeline and a warn queue item",
      (out["state"], "ends" in out["text"].lower()), ("bad", True))

out = na.pick("Acme Boats", health=QUEUE_BAD, upcoming=DOMAIN_WARN, pipeline=PIPE_COLD)
check("a cold pipeline beats a bad queue item and a warn deadline",
      "gone cold" in out["text"], True)
check("...and it is warn, not bad -- the pipeline card's own severity",
      out["state"], "warn")

out = na.pick("Acme Boats", health=QUEUE_BAD, upcoming=DOMAIN_WARN, pipeline=PIPE_CLEAN)
check("with no cold pipeline, a bad queue item beats a warn deadline",
      out["text"], "1 proposal needs chasing")
check("...in the bad state", out["state"], "bad")

out = na.pick("Acme Boats", health={}, upcoming=DOMAIN_WARN, pipeline=PIPE_CLEAN)
check("a warn deadline surfaces once nothing worse is on file",
      "Domain renews" in out["text"], True)

out = na.pick("Acme Boats", health=QUEUE_WARN, upcoming={}, pipeline=PIPE_CLEAN)
check("a warn queue item surfaces beneath a warn deadline",
      out["text"], "Nothing done for this client in 95 days")

out = na.pick("Acme Boats", health={}, upcoming={}, pipeline=PIPE_CLEAN, skills=SKILLS_NOT_READY)
check("the email skill's own stored check is the last resort",
      "Email Creator" in out["text"] and "not ready to send" in out["text"], True)
check("...at warn, never bad -- it is a setup gap, not a deadline",
      out["state"], "warn")

out = na.pick("Acme Boats", health={}, upcoming={}, pipeline=PIPE_CLEAN, skills=SKILLS_READY)
check("a ready email skill raises nothing", out["text"], "Nothing urgent on file for Acme Boats.")
check("...drawn as ok, a real state and not silence", out["state"], "ok")
check("...and it is measured -- an idle line is still an answer", out["measured"], True)

out = na.pick("", health=QUEUE_BAD)
check("no client name is refused rather than guessed at", out["measured"], False)

# ---------------------------------------------------- 2. wholly unmeasured
section("2. Nothing measured is its own answer, not 'nothing to do'")

out = na.pick("Nobody Co", health={}, upcoming={}, pipeline={})
check("three empty sources read as unmeasured", out["measured"], False)
check("...and no text is invented", out["text"], "")

out = na.pick("Nobody Co", health={"pills": []}, upcoming={}, pipeline={})
check("a health strip that answered at all counts as measured",
      out["measured"], True)

# --------------------------------------------------------------- 3. the wiring
section("3. for_client() wires the three sources and never raises")

import importlib                                                 # noqa: E402


class Stub:
    def __init__(self, **targets):
        self.targets, self.saved = targets, {}

    def __enter__(self):
        for dotted, value in self.targets.items():
            mod, attr = dotted.rsplit(".", 1)
            m = importlib.import_module(mod)
            self.saved[dotted] = getattr(m, attr)
            setattr(m, attr, value)
        return self

    def __exit__(self, *a):
        for dotted, old in self.saved.items():
            mod, attr = dotted.rsplit(".", 1)
            setattr(importlib.import_module(mod), attr, old)


with Stub(**{
    "hub.record_health.client360": lambda name, **kw: QUEUE_BAD,
    "hub.client_upcoming.for_client": lambda name, url, today=None: {"measured": True, "items": []},
    "hub.suite_pipeline.for_client": lambda name, url, **kw: PIPE_CLEAN,
}):
    with Stub(**{"modules.skills360.store.get": lambda name: {"skills": {}}}):
        out = na.for_client("Acme Boats")
check("for_client threads the three sources through to pick()",
      out["text"], "1 proposal needs chasing")


def _raise(*a, **k):
    raise RuntimeError("boom")


with Stub(**{
    "hub.record_health.client360": _raise,
    "hub.client_upcoming.for_client": _raise,
    "hub.suite_pipeline.for_client": _raise,
}):
    out = na.for_client("Acme Boats")
check("every source raising still answers rather than crashing the page",
      out["measured"], False)

# ------------------------------------------------------------ 4. the renderer
section("4. The renderer, in node")

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()

REC = _c360_source()
a = REC.find("/* ---- c360 next-action (lifted")
b_ = REC.find("/* ---- end c360 next-action ----")
SRC = REC[a:b_] if a > -1 and b_ > a else ""
check("the next-action block is still marked for lifting", bool(SRC))
check("the container is on the record", 'id="c360NextAction"' in REC)
check("the record fetches it from /api/client/", "/api/client/next-action?name=" in REC)
check("...loaded alongside the health strip", "loadNextAction(name)" in REC)

ESC = REC[REC.find("const esc="):REC.find("\n", REC.find("const esc="))]
script = ESC + "\n" + SRC + "\n" + f"""
const withHref = renderNextAction({json.dumps({"measured": True, "state": "bad", "text": "Ends in 9d.", "href": "/tools/io/"})});
const withGo = renderNextAction({json.dumps({"measured": True, "state": "warn", "text": "Cold leads.", "go": "overview"})});
const idle = renderNextAction({json.dumps({"measured": True, "state": "ok", "text": "Nothing urgent on file for X."})});
const unmeasured = renderNextAction({json.dumps({"measured": False, "text": ""})});
const escaped = renderNextAction({json.dumps({"measured": True, "state": "bad", "text": "<script>bad</script>", "go": "overview"})});
console.log(JSON.stringify({{withHref, withGo, idle, unmeasured, escaped}}));
"""
node = subprocess.run(["node", "-e", script], capture_output=True, text=True)
check("the lifted block runs on its own", node.returncode, 0)
try:
    drawn = json.loads(node.stdout.strip().splitlines()[-1])
except Exception:                                                # noqa: BLE001
    drawn = {}
check("a line with an href draws a link", drawn.get("withHref", "").startswith('<a class="c360-nba bad"'))
check("a line with no href draws a button carrying data-go",
      'data-go="overview"' in drawn.get("withGo", ""))
check("an ok line draws in the ok state", 'class="c360-nba ok"' in drawn.get("idle", ""))
check("an unmeasured line draws nothing at all", drawn.get("unmeasured", ""), "")
check("the text is escaped", "&lt;script&gt;" in drawn.get("escaped", ""))

# -------------------------------------------------------------- 5. the route
section("5. The route is under /api/client/ and refuses a stranger")

from wsgi import application                                      # noqa: E402
from werkzeug.test import Client                                  # noqa: E402

anon = Client(application)
r = anon.get("/api/client/next-action?name=Acme%20Boats")
check("anonymous is refused", r.status_code, 401)

staff = Client(application)
staff.post("/login", data={"password": "next-action-pass"})
r = staff.get("/api/client/next-action?name=Acme%20Boats")
check("staff gets a line", r.status_code, 200)
body = r.get_json() or {}
check("...answering measured/text consistently either way",
      bool(body.get("text")) == bool(body.get("measured")), True)
check("...and it never claims 'bad' or 'warn' about a client with no data on file",
      body.get("state") not in ("bad", "warn"), True)

r = staff.get("/api/client/next-action")
check("a missing name is a 400, not a line about nobody", r.status_code, 400)

from hub import suite_embed                                       # noqa: E402
check("the path is one the Suite frame may fetch",
      suite_embed.embeddable("/api/client/next-action"), True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
