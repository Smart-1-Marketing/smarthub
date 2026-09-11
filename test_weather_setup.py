"""Weather Trigger Setup: the campaign store, the copy guardrails, the two
blueprints and what an approval actually does.

    python3 test_weather_setup.py

Same shape as the other test files here -- no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="wx_setup_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "wx-setup-test-secret"
os.environ["PANEL_PASSWORD"] = "wx-setup-test-password"
os.environ.pop("OPENAI_API_KEY", None)          # exercise the house fallback

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


from modules.weather_setup import copy as wx_copy           # noqa: E402
from modules.weather_setup import store as wx_store         # noqa: E402
from modules.weather_setup import images as wx_images       # noqa: E402
from modules.weather_setup.app import bp_staff, bp_wx        # noqa: E402


# ---------------------------------------------------------------------------
section("Token and campaign shape")
# ---------------------------------------------------------------------------

t1, t2 = wx_store.new_token(), wx_store.new_token()
check("tokens are long and unguessable", len(t1) >= 32, True)
check("two tokens never collide", t1 != t2, True)
check("a token is not built from the client name", "acme" in t1.lower(), False)

row = wx_store.create(client="Acme Diner", zip_code="46032", created_by="rep@s1m.com")
check("new campaign is a draft", row["status"], "draft")
check("new campaign has no picks", row["picks"], [])
check("new campaign carries the client name", row["client"], "Acme Diner")
check("new campaign records the zip", row["zip_code"], "46032")
check("get() round-trips", wx_store.get(row["token"])["client"], "Acme Diner")
check("get() on a bogus token is None", wx_store.get("not-a-real-token"), None)


# ---------------------------------------------------------------------------
section("Picking triggers: the cap is enforced by the store, not just the picker")
# ---------------------------------------------------------------------------

token = row["token"]
result = wx_store.save_picks(token, ["patio-day", "cold-snap", "snow-day", "wind-chill"])
check("a fourth pick is refused", result["ok"], False)

result = wx_store.save_picks(token, ["patio-day", "cold-snap"])
check("two picks are accepted", result["ok"], True)
check("status moves off draft", result["campaign"]["status"], "awaiting_client")
check("two picks are recorded", len(result["campaign"]["picks"]), 2)
check("slots are numbered from 1", [p["slot"] for p in result["campaign"]["picks"]], [1, 2])

result = wx_store.save_picks(token, ["not-a-real-trigger"])
check("an unknown trigger id is refused", result["ok"], False)

result = wx_store.save_picks("no-such-token", ["patio-day"])
check("picks against a missing campaign are refused", result["ok"], False)


# ---------------------------------------------------------------------------
section("Copy generation: cached once, house fallback with no AI key")
# ---------------------------------------------------------------------------

calls = {"n": 0}
_real_generate = wx_copy.generate_drafts


def _counting_generate(*a, **kw):
    calls["n"] += 1
    return _real_generate(*a, **kw)


wx_copy.generate_drafts = _counting_generate
result = wx_store.get_or_generate_copy(token, "patio-day")
check("copy generation succeeds", result["ok"], True)
check("three angles come back", len(result["drafts"]), 3)
check("falls back to house copy with no OPENAI_API_KEY", result["source"], "house")
check("model was asked once", calls["n"], 1)

second = wx_store.get_or_generate_copy(token, "patio-day")
check("a second request reuses the cached drafts", calls["n"], 1)
check("cached drafts are the same three", len(second["drafts"]), 3)
wx_copy.generate_drafts = _real_generate

bad = wx_store.get_or_generate_copy(token, "heat-wave")
check("copy for an unpicked trigger is refused", bad["ok"], False)


# ---------------------------------------------------------------------------
section("Copy guardrails run on house drafts too")
# ---------------------------------------------------------------------------

promise_draft = {"angle": "Direct", "headline": "Cold Snap",
                 "primary_text": "We'll email you when it's ready."}
guarded = wx_copy._guardrail(dict(promise_draft), "cold-snap")
check("an unbacked promise is replaced", "email" in guarded["primary_text"].lower(), False)
check("the replacement is house-authored", guarded["source"], "house")

offer_draft = {"angle": "Direct", "headline": "Cold Snap", "primary_text": "20% off tonight!"}
guarded2 = wx_copy._guardrail(dict(offer_draft), "cold-snap")
check("a specific offer is flagged for review, not deleted", guarded2["needs_review"], True)
check("the offer text survives (a rep confirms it)", "20%" in guarded2["primary_text"], True)

storm_draft = {"angle": "Invitation", "headline": "Storm Watch",
              "primary_text": "Brave the storm and join us tonight!"}
guarded3 = wx_copy._guardrail(dict(storm_draft), "storm-watch")
check("storm copy that reads as an invitation to drive out is replaced",
     "brave" in guarded3["primary_text"].lower(), False)

clean_draft = {"angle": "Comfort", "headline": "Cold Snap", "primary_text": "Warm up with us."}
guarded4 = wx_copy._guardrail(dict(clean_draft), "cold-snap")
check("clean copy passes through unchanged", guarded4["primary_text"], "Warm up with us.")
check("clean copy needs no review", guarded4.get("needs_review"), False)


# ---------------------------------------------------------------------------
section("Selecting wording and an image")
# ---------------------------------------------------------------------------

wx_store.get_or_generate_copy(token, "cold-snap")
r = wx_store.select_variant(token, "patio-day", 0, notes="keep it short")
check("selecting a variant succeeds", r["ok"], True)
r = wx_store.select_variant(token, "patio-day", 99)
check("an out-of-range variant is refused", r["ok"], False)
r = wx_store.select_variant(token, "not-picked", 0)
check("selecting a variant for an unpicked trigger is refused", r["ok"], False)

fake_asset = {"source": "stock", "provider": "pexels", "provider_id": "p1",
             "provider_url": "https://example.com/p1.jpg",
             "cloudinary_public_id": "weather_setup/p1", "url": "https://example.com/p1.jpg",
             "credit_required": True, "license_json": {"provider": "pexels"}}
r = wx_store.set_asset(token, "patio-day", fake_asset)
check("setting an asset succeeds", r["ok"], True)
r = wx_store.set_asset(token, "not-picked", fake_asset)
check("setting an asset for an unpicked trigger is refused", r["ok"], False)


# ---------------------------------------------------------------------------
section("Approval: readiness, work order numbering, and the three side effects")
# ---------------------------------------------------------------------------

ready, why = wx_store.ready_to_approve(wx_store.get(token))
check("not ready until every pick has wording and an image", ready, False)
check("readiness names what is missing", bool(why), True)

wx_store.select_variant(token, "cold-snap", 0)
wx_store.set_asset(token, "cold-snap", dict(fake_asset, provider_id="p2"))
ready, why = wx_store.ready_to_approve(wx_store.get(token))
check("ready once every pick is complete", ready, True)

logged, delivered = {}, {}
from hub import audit as hub_audit                          # noqa: E402
from hub import leads as hub_leads                          # noqa: E402
_real_log, _real_deliver = hub_audit.log, hub_leads.capture_and_deliver


def _fake_log(module, type_, actor=None, **extra):
    logged["module"] = module
    logged["type"] = type_
    logged.update(extra)


def _fake_deliver(source, page, fields, pdf_url="", client="", meta=None):
    delivered["source"] = source
    delivered["client"] = client
    delivered["meta"] = meta
    return {"ok": True, "lead_id": "x", "delivered": False, "note": ""}


hub_audit.log = _fake_log
hub_leads.capture_and_deliver = _fake_deliver

result = wx_store.approve(token, name="Jamie Rep", ip="203.0.113.5")
check("approval succeeds once ready", result["ok"], True)
check("status becomes approved", result["campaign"]["status"], "approved")
check("work order is numbered WO-00001 for the first one this test run",
     result["work_order"].startswith("WO-"), True)
check("approved_by_name is recorded", result["campaign"]["approved_by_name"], "Jamie Rep")
check("approved_ip is recorded", result["campaign"]["approved_ip"], "203.0.113.5")

check("audit logs under weather_trigger_setup", logged.get("module"), "weather_trigger_setup")
check("audit event is 'approved'", logged.get("type"), "approved")
check("audit carries the client", logged.get("client"), "Acme Diner")
check("audit carries the work order", logged.get("work_order"), result["work_order"])

check("the lead is delivered under the registered source", delivered.get("source"),
     "weather_trigger_setup")
check("the lead is filed against the right client", delivered.get("client"), "Acme Diner")
check("the confirmation link is on the lead", "/wx/" in (delivered.get("meta") or {}).get("report_url", ""), True)

second_wo = wx_store._next_work_order()
check("work order numbers increment", second_wo != result["work_order"], True)

hub_audit.log, hub_leads.capture_and_deliver = _real_log, _real_deliver

again = wx_store.approve(token, name="Someone Else", ip="1.2.3.4")
check("a second approval of the same campaign is refused", again["ok"], False)


# ---------------------------------------------------------------------------
section("A change request re-opens the campaign without editing history")
# ---------------------------------------------------------------------------

r = wx_store.request_change(token)
check("a change request succeeds on an approved campaign", r["ok"], True)
check("status becomes changed", r["campaign"]["status"], "changed")
check("revision increments", r["campaign"]["revision"], 2)
check("the work order is cleared until re-approved", r["campaign"]["work_order"], "")
check("wording selections are cleared for re-review",
     all(p["selected_variant"] is None for p in r["campaign"]["picks"]), True)

r2 = wx_store.request_change(token)
check("a second change request on a non-approved campaign is refused", r2["ok"], False)

draft_row = wx_store.create(client="Never Approved")
r3 = wx_store.request_change(draft_row["token"])
check("a change request on a draft campaign is refused", r3["ok"], False)


# ---------------------------------------------------------------------------
section("The scheduler's trigger-state writer")
# ---------------------------------------------------------------------------

fresh = wx_store.create(client="Weathervane BBQ", zip_code="90210")
wx_store.save_picks(fresh["token"], ["cold-snap"])
wx_store.update_trigger_state(fresh["token"], "cold-snap", active=True, measured=True,
                              detail="high 28F", trigger_state={})
row2 = wx_store.get(fresh["token"])
pick = row2["picks"][0]
check("active is written", pick["active"], True)
check("the detail sentence is kept", pick["condition_detail"], "high 28F")
check("a toggle from unset to active logs an event",
     any(e["kind"] == "trigger_toggled" for e in row2["events"]), True)

_crashed = False
try:
    wx_store.update_trigger_state("gone", "cold-snap", active=True, measured=True,
                                  detail="", trigger_state={})
except Exception:                                            # noqa: BLE001
    _crashed = True
check("a missing campaign never raises", _crashed, False)
check("and nothing was created for it", wx_store.get("gone"), None)

check("approved_campaigns only lists approved/changed campaigns with a zip",
     fresh["token"] in [c["token"] for c in wx_store.approved_campaigns()], False)
check("the approved campaign above is in the list",
     token in [c["token"] for c in wx_store.approved_campaigns()], True)


# ---------------------------------------------------------------------------
section("Registered in the tables that make a client's work attributable")
# ---------------------------------------------------------------------------

from hub import lead_tags                                    # noqa: E402
from hub import client_brand                                  # noqa: E402

check("weather_trigger_setup is a known lead source", lead_tags.known("weather_trigger_setup"), True)
check("no workflow is claimed until one is built in Suite",
     lead_tags.backed("weather_trigger_setup"), False)
check("weather_trigger_setup is in WORK_KINDS", "weather_trigger_setup" in client_brand.WORK_KINDS, True)


# ---------------------------------------------------------------------------
section("Image sources degrade gracefully with nothing configured")
# ---------------------------------------------------------------------------

r = wx_images.search("")
check("an empty query is refused rather than searching everything", bool(r.get("error")), True)
r = wx_images.upload(client="Acme Diner", trigger_id="patio-day", data=b"", filename="x.jpg")
check("an empty upload is refused", r["ok"], False)
r = wx_images.generate("a warm patio at sunset", client="Acme Diner")
check("generate() fails cleanly with no OPENAI_API_KEY configured", r["ok"], False)
sel = wx_images.select(client="Acme Diner", trigger_id="patio-day", image_id="", provider="pexels", url="")
check("select() with no url and no public_id is refused", sel["ok"], False)
sel2 = wx_images.select(client="Acme Diner", trigger_id="patio-day", image_id="g1",
                        provider="gallery", url="https://example.com/g1.jpg",
                        public_id="already/there")
check("a gallery pick needs no re-copy", sel2["ok"], True)
check("a gallery asset is marked already-ours", sel2["asset"]["source"], "gallery")


# ---------------------------------------------------------------------------
section("The public wizard route, end to end over HTTP")
# ---------------------------------------------------------------------------

from flask import Flask                                      # noqa: E402

flask_app = Flask(__name__)
flask_app.register_blueprint(bp_wx, url_prefix="/wx")
client = flask_app.test_client()

fresh2 = wx_store.create(client="Riverside Grill", zip_code="46032")
resp = client.get(f"/wx/{fresh2['token']}")
check("the wizard page loads for a real token", resp.status_code, 200)
check("the client's name is on the page", b"Riverside Grill" in resp.data, True)

resp = client.get("/wx/not-a-real-token")
check("a missing token answers 404", resp.status_code, 404)

resp = client.get(f"/wx/{fresh2['token']}/api/state")
check("api/state answers ok", resp.get_json()["ok"], True)

resp = client.post(f"/wx/{fresh2['token']}/api/picks",
                   json={"trigger_ids": ["patio-day", "cold-snap", "snow-day", "wind-chill"]})
check("the picks API refuses a fourth trigger", resp.status_code, 400)

resp = client.post(f"/wx/{fresh2['token']}/api/picks",
                   json={"trigger_ids": ["patio-day"]})
check("the picks API accepts a valid pick", resp.status_code, 200)

resp = client.post(f"/wx/{fresh2['token']}/api/approve", json={"name": ""})
check("approve refuses with no name", resp.status_code, 400)

resp = client.post(f"/wx/{fresh2['token']}/api/approve", json={"name": "Someone"})
check("approve refuses before the campaign is ready", resp.status_code, 400)

view_count_before = wx_store.get(fresh2["token"])["view_count"]
client.get(f"/wx/{fresh2['token']}")
view_count_after = wx_store.get(fresh2["token"])["view_count"]
check("each open of the wizard bumps the view count",
     view_count_after > view_count_before, True)


# ---------------------------------------------------------------------------
section("The staff blueprint is gated; the public one is not")
# ---------------------------------------------------------------------------

check("bp_staff and bp_wx have distinct blueprint names",
     bp_staff.name != bp_wx.name, True)
check("bp_staff.name is what register_weather_setup checks for", bp_staff.name, "weather_setup_staff")
check("bp_wx.name is what register_weather_setup checks for", bp_wx.name, "weather_setup_wx")
check("blueprint_guard.install() put a before_request on the staff blueprint",
     len(bp_staff.before_request_funcs.get(None, [])) >= 1, True)
check("the public blueprint carries no such gate",
     len(bp_wx.before_request_funcs.get(None, [])), 0)

staff_app = Flask(__name__)
staff_app.register_blueprint(bp_staff, url_prefix="/tools/weather-setup")
staff_client = staff_app.test_client()
resp = staff_client.post("/tools/weather-setup/api/start", json={"client": "Nope"})
check("the staff start route refuses an unauthenticated request", resp.status_code, 401)


# ---------------------------------------------------------------------------
section("A write failure answers JSON, never Flask's stock HTML 500 page")
# ---------------------------------------------------------------------------
# store.create() was the one function in this file that did not honour its
# own module's "nothing here may raise" rule -- a jsonstore.write_json()
# failure (a full disk, a permission problem) propagated straight through
# api_start(), which this blueprint-on-the-hub-app module has no blanket
# exception handler to catch (only wsgi.py's dispatcher-mounted modules get
# one). The rep saw "Could not reach the server" for what was a server-side
# write failure, because fetch().then(r => r.json()) rejects on an HTML
# body. Reproduced end to end here, not just at the unit level, because the
# whole point is what the *response* looks like to that fetch call.

import hub.auth as _auth                                     # noqa: E402

_orig_write_json = wx_store.jsonstore.write_json


def _boom(*_a, **_k):
    raise OSError(28, "No space left on device")


wx_store.jsonstore.write_json = _boom
try:
    check("create() returns None rather than raising",
         wx_store.create(client="Disk Full Diner"), None)
finally:
    wx_store.jsonstore.write_json = _orig_write_json

staff_client.set_cookie(_auth.COOKIE_NAME, _auth.issue_cookie_value("Todd"),
                        domain="localhost")

wx_store.jsonstore.write_json = _boom
try:
    resp = staff_client.post("/tools/weather-setup/api/start",
                             json={"client": "Disk Full Diner"})
    check("api/start answers 503, not a crash", resp.status_code, 503)
    check("...as JSON, so the browser's fetch().json() does not reject",
         resp.headers.get("Content-Type", "").startswith("application/json"), True)
    check("...saying nothing was sent, not the network-error message",
         "reach the server" in (resp.get_json() or {}).get("error", ""), False)
finally:
    wx_store.jsonstore.write_json = _orig_write_json

# Any OTHER exception in the route -- not only the one gap above -- must
# answer the same way, because the safety net is the outer try/except in
# api_start() and the fix must not depend on remembering to guard the next
# call added to this function too.
_orig_create = wx_store.create


def _explode(**_k):
    raise RuntimeError("something unrelated broke")


from modules.weather_setup import app as wx_app               # noqa: E402
wx_app.store.create = _explode
try:
    resp = staff_client.post("/tools/weather-setup/api/start",
                             json={"client": "Anything"})
    check("an unrelated exception is still caught", resp.status_code, 500)
    check("...and still answers as JSON",
         resp.headers.get("Content-Type", "").startswith("application/json"), True)
finally:
    wx_app.store.create = _orig_create


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
