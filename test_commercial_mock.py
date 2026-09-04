"""Commercial Builder — the mock marker that reached the browser and died.

    python3 test_commercial_mock.py

Every provider in this module degrades to mock data rather than erroring,
which is right: a missing key must not stop a rep laying a spot out. What it
also does is make a misnamed key invisible. Concepts come back from a
template, the casting list is placeholder voices, stock search returns
placehold.co images labelled like footage — all of it looking exactly like
work, on the one screen where somebody is deciding whether the creative is
any good.

The server has been saying so the whole time. `/concepts`, `/script`,
`/voices` and `/generate-ai` answer `live: false`; the Runway and HeyGen
status routes answer `mock: true`. Not one line of this module's JavaScript
read any of it — the mark was written, sent over the wire, and dropped by
the last consumer, which is the shape RECORD_HOOK, io_creative, manifest()
and thumb_url() each had, one step further out.

What this file holds:

  1. The six routes the front-end table names really do report it, driven
     with every key unset. A table naming a route that never fires is one
     nobody can trust, and this is the only thing that stops it drifting
     into one.
  2. The three deliberately left out are left out for the stated reason, so
     their absence stays a decision rather than an oversight.
  3. api() is where the detection hangs, not each caller — the next route
     added here is covered without anybody remembering.
  4. None of it can reach a client.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="s1cbmock_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(_TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
# Assigned, never setdefault: a fresh directory is not isolation on its own,
# and an inherited DATABASE_URL refills it with the last run's rows.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "cbmock-test-secret"
os.environ["PANEL_PASSWORD"] = "cbmock-test-password"
# The whole point: every provider off.
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY", "HEYGEN_API",
           "RUNWAY_API_KEY", "CREATOMATE_API_KEY", "CLOUDINARY_URL",
           "PEXELS_API", "PIXABAY_API", "COVERR_API"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


import wsgi                                                          # noqa: E402
from werkzeug.test import Client                                     # noqa: E402
from werkzeug.wrappers import Response                               # noqa: E402

M = "/tools/commercial-builder"
JS = (ROOT / "modules/commercial_builder/static/js/common.js").read_text(encoding="utf-8")

C = Client(wsgi.application, Response)
C.post("/login", data={"password": "cbmock-test-password"}, follow_redirects=True)


def J(r):
    try:
        return json.loads(r.data)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
section("The six routes the table names really do report it")
# =====================================================================
# Driven rather than asserted from the source: the table is a claim about
# what the server does, and only the server can settle it.
_cid = J(C.post(f"{M}/api/clients", json={"name": "Mock Co", "industry": "home_services"}))["client"]["id"]
_pid = J(C.post(f"{M}/api/projects", json={
    "client_id": _cid, "title": "Mock", "commercial_type": "promo_sale",
    "lengths": [30], "platform": "ctv", "formats": ["16:9"]}))["projects"][0]["id"]
C.put(f"{M}/api/projects/{_pid}/brief", json={"what_advertising": "Spring tune-up"})

_con = J(C.post(f"{M}/api/projects/{_pid}/concepts"))
check("/concepts reports live: false with no key", _con.get("live"), False)
check("...and still returns concepts rather than failing", len(_con.get("concepts") or []) > 0, True)

C.post(f"{M}/api/projects/{_pid}/select-concept", json={"concept_id": _con["concepts"][0]["id"]})
_scr = J(C.post(f"{M}/api/projects/{_pid}/script"))
check("/script reports live: false", _scr.get("live"), False)

_voi = J(C.get(f"{M}/api/voices"))
check("/voices reports live: false", _voi.get("live"), False)
check("...and each placeholder voice is marked _mock",
      all(v.get("_mock") for v in (_voi.get("voices") or [])), True)

_scenes = J(C.get(f"{M}/api/projects/{_pid}/scenes")).get("scenes") or []
check("the script produced scenes to work with", len(_scenes) > 0, True)
_sid = _scenes[0]["id"] if _scenes else None
if _sid:
    _ai = J(C.post(f"{M}/api/projects/{_pid}/scenes/{_sid}/generate-ai", json={}))
    check("/generate-ai reports live: false", _ai.get("live"), False)

# The two status routes answer `mock` rather than `live`. Read from the
# source: reaching them needs a provider job id, which mock mode cannot mint
# without the very call under test.
check("the Runway status route carries the mock flag",
      '"mock": status.get("_mock", False)' in
      (ROOT / "modules/commercial_builder/routes/assets.py").read_text(encoding="utf-8"), True)
check("the HeyGen status route carries the mock flag",
      '"mock": status.get("_mock", False)' in
      (ROOT / "modules/commercial_builder/routes/heygen.py").read_text(encoding="utf-8"), True)

# ---------------------------------------------------------------------------
section("Stock reports differently, and is read on its own terms")
# =====================================================================
# A per-source map rather than one flag. Every source off means the results
# are placehold.co standing in for footage -- the mock a rep is most likely
# to drag onto a scene believing it is real.
_stock = J(C.get(f"{M}/api/stock/search?q=plumber"))
check("stock answers a per-provider map, not `live`", _stock.get("live"), None)
check("...with every source off when no key is set",
      all(v is False for v in (_stock.get("providers") or {}).values()), True)
check("and the front-end reads that shape rather than only `live`",
      "data.providers" in JS, True)

# ---------------------------------------------------------------------------
section("What is left out is left out on purpose")
# =====================================================================
# NOT_ENFORCED / NOT_REQUESTED / _KIT_UNREAD's rule: a thing deliberately
# absent is named with its reason, so nobody adds it back from memory or
# reads the gap as an oversight.
for _name in ("/render", "/voiceover/full"):
    check(f"{_name} is named in the comment as deliberately absent", _name in JS, True)
check("...and the reason given is that they carry no such key",
      "carry no such key" in " ".join(JS.split()), True)
check("the render is covered by approve_render refusing a mock instead",
      "approve_render refuses" in JS, True)

_render = J(C.post(f"{M}/api/projects/{_pid}/render", json={"formats": ["16:9"]}))
check("...and /render genuinely carries neither flag, as the comment says",
      (_render.get("live"), _render.get("mock")), (None, None))

# ---------------------------------------------------------------------------
section("It hangs off api(), so the next route is covered without an edit")
# =====================================================================
check("api() calls the detector", re.search(r"noteMock\(path, data\);\s*\n\s*return data;", JS) is not None, True)
check("...and it is the only call site",
      JS.count("noteMock(path, data)") - JS.count("function noteMock(path, data)"), 1)
check("nothing in the detector may raise", JS.count("catch (e)") >= 2, True)
check("the note is amber, not red", 'className = "cb-note"' in JS, True)
check("...and never claims a step the server did not report",
      "data.live !== false && data.mock !== true" in JS, True)

# ---------------------------------------------------------------------------
section("None of it can reach a client")
# =====================================================================
# commercial_review.html deliberately does not extend _layout.html, which is
# the only template that loads common.js. Asserted rather than trusted: a
# staff note on the page a client signs a spot off on is an internal note in
# front of a customer.
_layout_users = [p.name for p in (ROOT / "modules/commercial_builder/templates").glob("*.html")
                 if "common.js" in p.read_text(encoding="utf-8")]
check("only the staff layout loads common.js", _layout_users, ["_layout.html"])
check("the client's review page does not extend it",
      "{% extends" in (ROOT / "modules/commercial_builder/templates/commercial_review.html")
      .read_text(encoding="utf-8"), False)

from modules.commercial_builder.db import db                         # noqa: E402
from modules.commercial_builder.models import RenderJob              # noqa: E402
_app = wsgi.application
while _app is not None and not hasattr(_app, "url_map"):
    _app = getattr(_app, "app", None) or getattr(_app, "wsgi_app", None)
with _app.app_context():
    db.session.add(RenderJob(project_id=_pid, status="succeeded", format="16:9",
                             output_url="https://res.cloudinary.com/demo/video/upload/s.mp4"))
    db.session.commit()
_tok = (J(C.post(f"{M}/api/projects/{_pid}/reviews", json={})).get("review") or {}).get("token")
_anon = Client(wsgi.application, Response)
_body = _anon.get(f"{M}/review/{_tok}").data.decode("utf8", "replace")
check("a client with no login can open the review", _tok is not None, True)
check("...and the page carries none of this", "common.js" in _body, False)
check("...nor the note itself", "cb-mock-note" in _body, False)

print("\n" + "-" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
