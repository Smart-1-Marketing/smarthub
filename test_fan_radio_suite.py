"""Fan Radio's finished work reaches Smart 1 Suite, and only when it should.

The last difference between the two radio builders that nobody had recorded a
decision about. The Radio Ad Creator could file an approved spot as a client's
opportunity since it was ported; Fan Radio ended at the share link and an
optional notify ping, so a finished football spot reached the CRM where the
calls, the texts and the pipeline live only if somebody pasted a link in by
hand.

What this holds, and why each one is an assertion rather than a comment:

* **One contact write path, not a third webhook.** The Radio Ad Creator posts
  its own payload to `GHL_OPPORTUNITY_WEBHOOK_URL`.
  `modules/commercial_builder/routes/suite.py` faced the same choice and wrote
  down its refusal: `hub/ghl_contacts.py` is "one token, one location id and
  one contact write path for the whole Hub", and a second raw webhook there
  "would be a third answer to how do we reach GoHighLevel". So this asserts
  Fan Radio goes through `hub/suite_opportunity.push_proposal` and that it
  reads no webhook variable of its own -- the check that stops the fourth copy
  being written later.

* **What the CLIENT approved, not what a rep thought was ready.** This is the
  one place Fan Radio is the better of the two: the Radio Ad Creator gates on a
  staff `approve-spot` press and Fan Radio has the customer's own decision off
  the share page, with their name and the time on it. So an unapproved spot is
  refused, and no staff approval is added -- a second, weaker gate in front of
  a stronger one.

* **The finished mix where a bed was chosen.** `public_view()` falls back to
  the raw read so a client has something to hear while a bed is composed, which
  is right for review and wrong for delivery: sending the naked read as final
  delivers a commercial nobody made. Held back and named.

* **An address a salesperson can open.** A local render is stored relative and
  served under this module's mount. Absolutized against `PUBLIC_BASE_URL`, and
  where that is unset the spot is held rather than delivered with a URL that
  resolves to nothing.

* **A second press revises rather than opening a second opportunity**, and a
  refusal is recorded as a refusal -- "nobody pushed this", "Suite refused it"
  and "Suite has it" are three states, and collapsing the middle one makes the
  button read as never pressed.

    python3 test_fan_radio_suite.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1frsuite_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)
os.environ["HUB_DATA_DIR"] = DISK
# Both, deliberately -- a fresh data directory in front of an inherited
# DATABASE_URL is refilled from the last run's mirror.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "fr-suite-test-secret"
os.environ["PANEL_PASSWORD"] = "fr-suite-test-password"
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY",
           "CLOUDINARY_URL", "GHL_OPPORTUNITY_WEBHOOK_URL",
           "FAN_RADIO_NOTIFY_URL"):
    os.environ.pop(_k, None)

import wsgi                                                       # noqa: E402

from werkzeug.test import Client                                   # noqa: E402

from modules.fan_radio import app as fan_app                       # noqa: E402
from modules.fan_radio import store as fan_store                   # noqa: E402
from modules.fan_radio import suite                                # noqa: E402

PASS = FAIL = 0
MOUNT = fan_app.MOUNT


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


client = Client(wsgi.application)
client.post("/login", data={"password": os.environ["PANEL_PASSWORD"]},
            follow_redirects=True)


def project(spots, **over):
    row = fan_store.create(dict({
        "scope": "client", "client": "Ridgeline Tire",
        "company": "Ridgeline Tire", "home_url": "ridgelinetire.com",
    }, **over), "tester")
    row = fan_store.load(row["id"])
    row["spots"] = spots
    row["voice"] = {"name": "Reed", "voice_id": "v1"}
    fan_store.save(row)
    return row


def spot(sid, **over):
    return dict({"id": sid, "daypart": "gameday", "seconds": 30,
                 "outcome": "neutral", "script": "Ridgeline Tire has you set.",
                 "status": "approved", "audio_url": "https://res.cloudinary.com/x/a.mp3",
                 "audio_seconds": 29.6, "decided_by": "Dana at Ridgeline",
                 "decided_at": "2026-09-16T12:00:00+00:00"}, **over)


def state(pid):
    return client.get(f"{MOUNT}/api/projects/{pid}/suite").get_json()


def push(pid, **body):
    return client.post(f"{MOUNT}/api/projects/{pid}/suite", json=body)


# A push_proposal stand-in. Every test that pushes goes through this, so what
# Fan Radio hands the shared function is asserted directly rather than inferred
# from whatever Suite would have done with it.
class Recorder:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


def pushing(result):
    rec = Recorder(result)
    return rec, mock.patch.object(fan_app.suite_opportunity,
                                  "push_proposal", rec)


CONFIGURED = mock.patch.object(fan_app.suite_opportunity, "configured",
                               return_value=True)


# ===========================================================================
section("One contact write path, not a third webhook")
# ===========================================================================
_src = (ROOT / "modules" / "fan_radio" / "suite.py").read_text(encoding="utf-8")
_app_src = (ROOT / "modules" / "fan_radio" / "app.py").read_text(encoding="utf-8")


def _env_names_read(source: str) -> set:
    """Every environment variable this source actually reads.

    Read off the AST rather than matched as text, because **prose is not a
    call site**: `modules/fan_radio/suite.py` explains at length why it does
    *not* post to `GHL_OPPORTUNITY_WEBHOOK_URL`, and a substring search reports
    that explanation as the defect -- the trap `tools/spellcheck.py` and
    `/api/integrity`'s own client-work check both had to learn.
    """
    import ast
    names = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        target = ""
        if isinstance(func, ast.Attribute):
            target = func.attr
        elif isinstance(func, ast.Name):
            target = func.id
        if target not in {"get", "getenv", "environ"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
    return names


_read = _env_names_read(_src) | _env_names_read(_app_src)
check("Fan Radio reads no GoHighLevel webhook variable of its own",
      sorted(n for n in _read if "GHL" in n), [])
check("and posts to no hook itself",
      "requests.post" in _src, False)
# The explanation is in the module, and the check above is not fooled by it.
check("though it does explain in prose why it does not",
      "GHL_OPPORTUNITY_WEBHOOK_URL" in _src, True)
check("it goes through the Hub's shared push instead",
      "suite_opportunity.push_proposal" in _app_src, True)
# The three-shape answer is the reason that function exists; flattening it is
# what turns "no contact yet" into what looks like an outage.
check("and the shared push is the one the Commercial Builder uses",
      hasattr(fan_app.suite_opportunity, "push_proposal"), True)


# ===========================================================================
section("What the client approved, and nothing else")
# ===========================================================================
_pending = project([spot("a", status="pending")])
with CONFIGURED:
    _s = state(_pending["id"])
check("a spot the client has not approved is not going anywhere",
      _s["can_push"], False)
check("and the reason names the share page rather than a staff button",
      "Approve" in _s["blockers"][0] and "client" in _s["blockers"][0], True)
with CONFIGURED:
    _r = push(_pending["id"])
check("the push is refused with the same reason", _r.status_code, 422)

# No staff approval was added: the customer's decision is the record.
check("there is no staff approve route to bypass the client with",
      any("approve-spot" in str(r) for r in fan_app.app.url_map.iter_rules()),
      False)

_ok = project([spot("a")])
with CONFIGURED:
    _s = state(_ok["id"])
check("an approved spot is ready to file", _s["can_push"], True)
check("and it carries who approved it",
      _s["spots"]["ready"][0]["approved_by"], "Dana at Ridgeline")


# ===========================================================================
section("A spec spot never opens an opportunity")
# ===========================================================================
_spec = project([spot("a")], scope="spec", client="")
with CONFIGURED:
    _s = state(_spec["id"])
check("a spec spot cannot be filed", _s["can_push"], False)
check("and says why — the pipeline is the thing being protected",
      "pollutes the pipeline" in _s["blockers"][0], True)
with CONFIGURED:
    _r = push(_spec["id"])
check("the route refuses it too", _r.status_code, 422)


# ===========================================================================
section("The finished mix, never the naked read behind it")
# ===========================================================================
# `public_view()` falls back to the raw read on purpose, for review. Delivery
# must not: a bed chosen with no saved mix is an unfinished commercial.
_mixed = project([
    spot("with-bed", bed={"audio_url": "https://x/bed.mp3"},
         mix={"audio_url": "https://x/mix.mp3", "seconds": 30.1}),
    spot("bed-no-mix", daypart="pregame", seconds=15,
         bed={"audio_url": "https://x/bed.mp3"}),
    spot("straight", daypart="postgame"),
    spot("stale", daypart="gameday", seconds=15, audio_stale=True),
])
_rows = suite.units(fan_store.load(_mixed["id"]), MOUNT)
check("the mix is what a mixed spot delivers",
      [(r["id"], r["is_mix"]) for r in _rows["ready"] if r["id"] == "with-bed"],
      [("with-bed", True)])
check("a straight read with no bed is final audio in its own right",
      [r["id"] for r in _rows["ready"]].count("straight"), 1)
_held = {h["id"]: h["why"] for h in _rows["held"]}
check("a bed with no saved mix is held back rather than sent raw",
      _held.get("bed-no-mix"), suite.HELD_UNMIXED)
check("and a read of wording the script has moved past is held too",
      _held.get("stale"), suite.HELD_STALE)
check("so exactly the two finished spots are going",
      sorted(r["id"] for r in _rows["ready"]), ["straight", "with-bed"])


# ===========================================================================
section("An address a salesperson can actually open")
# ===========================================================================
check("a provider URL is already absolute",
      suite.absolute_audio("https://res.cloudinary.com/x/a.mp3", MOUNT),
      "https://res.cloudinary.com/x/a.mp3")
check("a local render is absolutized under this module's mount",
      suite.absolute_audio("audio/a.mp3", MOUNT),
      "https://smart1.agency/tools/fan-radio/audio/a.mp3")
check("and a path that already carries the mount is not given it twice",
      suite.absolute_audio("/tools/fan-radio/audio/a.mp3", MOUNT),
      "https://smart1.agency/tools/fan-radio/audio/a.mp3")

_base = os.environ.pop("PUBLIC_BASE_URL")
_local = project([spot("local", audio_url="audio/local.mp3")])
_rows = suite.units(fan_store.load(_local["id"]), MOUNT)
check("with no public base there is no address to send",
      ([r["id"] for r in _rows["ready"]],
       [h["why"] for h in _rows["held"]]), ([], [suite.HELD_NO_URL]))
check("which is named as a setting to fix, not a mystery",
      "PUBLIC_BASE_URL" in suite.HELD_NO_URL, True)
os.environ["PUBLIC_BASE_URL"] = _base


# ===========================================================================
section("What the salesperson reads on the opportunity")
# ===========================================================================
_ready = suite.units(fan_store.load(_mixed["id"]), MOUNT)["ready"]
_note = suite.note_lines(fan_store.load(_mixed["id"]), _ready)
_joined = "\n".join(_note)
check("the note names the client and the count",
      _note[0], "Fan Radio spots delivered: Ridgeline Tire")
check("it says the client approved them, not that we finished them",
      "approved by the client" in _note[1], True)
check("the voice is named", "Voice: Reed" in _joined, True)
check("every delivered spot's audio is in it",
      all(r["audio_url"] in _joined for r in _ready), True)
check("and its script, because a link that rots still leaves the words",
      "Ridgeline Tire has you set." in _joined, True)
check("a mixed spot and a straight read are distinguished",
      ("With music" in _joined, "Straight read" in _joined), (True, True))
# Named lines rather than a JSON blob: this lands on the record a salesperson
# opens, and a plan that comes back as JSON is a plan nobody reads.
check("nothing here is a JSON blob", "{" in _joined, False)


# ===========================================================================
section("Pushing twice revises one opportunity")
# ===========================================================================
_twice = project([spot("a")])
_rec, _patch = pushing({"ok": True, "opportunity_id": "opp-1",
                        "contact": {"id": "c-1", "name": "Dana"},
                        "created": False})
with CONFIGURED, _patch:
    _first = push(_twice["id"])
check("the first press is accepted", _first.status_code, 200)
check("and nothing was handed an opportunity id yet",
      _rec.calls[0]["opportunity_id"], "")
check("the client, not the company, is who it is filed against",
      _rec.calls[0]["client"], "Ridgeline Tire")
check("and it names Fan Radio as the source",
      _rec.calls[0]["source"], "Smart 1 Hub — Fan Radio")
check("the opportunity is kept on the project",
      fan_store.load(_twice["id"])["suite"]["opportunity_id"], "opp-1")

_rec2, _patch2 = pushing({"ok": True, "opportunity_id": "opp-1",
                          "contact": {"id": "c-1", "name": "Dana"}})
with CONFIGURED, _patch2:
    push(_twice["id"])
check("so the second press revises that one rather than opening a second",
      _rec2.calls[0]["opportunity_id"], "opp-1")


# ===========================================================================
section("A refusal is recorded as a refusal")
# ===========================================================================
_refused = project([spot("a")])
_rec3, _patch3 = pushing({"ok": False, "needs_contact": True,
                          "reason": "No Smart 1 Suite contact matches "
                                    "Ridgeline Tire.",
                          "suggest": {"name": "", "email": "", "phone": ""}})
with CONFIGURED, _patch3:
    _r = push(_refused["id"])
check("a missing contact is a 422, not an outage", _r.status_code, 422)
_body = _r.get_json()
check("and it is flagged as fixable from the same screen",
      _body["needs_contact"], True)
_row = fan_store.load(_refused["id"])["suite"]
check("the refusal is written down rather than leaving the button unpressed",
      (_row["ok"], _row["needs_contact"]), (False, True))
check("with the reason Suite gave",
      "No Smart 1 Suite contact" in _row["reason"], True)
check("and who pressed it", bool(_row["pushed_by"]), True)
# A contact typed into the panel reaches the shared push, which creates both.
_rec4, _patch4 = pushing({"ok": True, "opportunity_id": "opp-2",
                          "contact": {"id": "c-2", "name": "Dana"},
                          "created": True})
with CONFIGURED, _patch4:
    _r = push(_refused["id"], contact={"name": "Dana", "email": "d@x.com"})
check("supplying one is accepted", _r.status_code, 200)
check("and it is handed to the shared push verbatim",
      _rec4.calls[0]["contact"], {"name": "Dana", "email": "d@x.com"})
check("the page is told a contact was created",
      _r.get_json()["created"], True)

# Suite refusing for its own reasons is a different answer again.
_rec5, _patch5 = pushing({"ok": False, "reason": "Suite said no."})
with CONFIGURED, _patch5:
    _r = push(project([spot("a")])["id"])
check("a Suite-side refusal is a 502, not a 422", _r.status_code, 502)
check("and is not flagged as a contact problem",
      _r.get_json()["needs_contact"], False)


# ===========================================================================
section("Unconfigured is its own answer, and never a silent pass")
# ===========================================================================
with mock.patch.object(fan_app.suite_opportunity, "configured",
                       return_value=False):
    _s = state(project([spot("a")])["id"])
check("an unconfigured Suite cannot be pushed to", _s["can_push"], False)
check("and the panel says what is unset rather than going quiet",
      isinstance(_s["problems"], list), True)
check("a project that does not exist 404s",
      client.get(f"{MOUNT}/api/projects/nope/suite").status_code, 404)


# ===========================================================================
section("And a rep can reach it")
# ===========================================================================
_page = client.get(f"{MOUNT}/").get_data(as_text=True)
check("the builder draws the Suite panel", 'id="suiteBody"' in _page, True)
check("with a button to press", 'id="suitePush"' in _page, True)
check("and the contact fields the fixable refusal needs",
      'id="suiteName"' in _page and 'id="suiteEmail"' in _page, True)
check("it reads the state rather than assuming it",
      "loadSuite()" in _page, True)


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    print("\nwhat the client approved goes to the Suite, through the Hub's one "
          "contact write path, and a refusal is recorded as a refusal")
sys.exit(1 if FAIL else 0)
