"""One confirmed audience per client, and the four readers of it.

    python3 test_audience_finder.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

The Audience Finder is the second Pickaxe that earns a live call, and its
per-client half (hub/audience_spec.py) is read from four places. Every
failure below is one where all four would go on looking healthy:

  1. **Nothing is written by proposing.** The model proposes, a person
     ticks, and confirm() is the only write — the competitor-research
     shape. A propose that stored anything would make a billed button a
     silent write to a client's record.

  2. **A failed lookup is an error, never an empty audience.** The Pickaxe
     falls back to the Hub's own model with the source named, and both
     failing answers ok: False — "we could not look" and "there is no
     audience" send a rep to different places.

  3. **A typed value wins.** AD_COPY's {audience} prefill reads the
     confirmed audience only when the campaign typed nothing — the overlay
     rule. Read the other way, a confirmation made months ago would
     silently overrule the rep on this campaign.

  4. **One reading of the reply parse.** The Proposal Builder's
     /api/find-audiences imports parse_reply and candidates from
     hub/audience_spec.py rather than keeping the copies it started with —
     two readings of one Pickaxe reply drift the day either is edited.

  5. **Confirm and clear are different verbs.** An empty list is refused by
     name rather than read as a clear, because "save what is ticked" and
     "take this off the record" are different statements.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1aud_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "aud-test-secret"
os.environ["PANEL_PASSWORD"] = "aud-test-password"

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


from hub import audience_spec as spec                          # noqa: E402


# =====================================================================
section("1. The reply parse, and the tick-gated candidate shaping")
# =====================================================================

reply = ("Here are audiences worth targeting:\n"
         "- In-market vehicle buyers\n"
         "• Auto-intenders\n"
         "2) Recent Movers\n"
         "\n"
         "A very long line " + "x" * 130 + "\n"
         "Homeowners")
names = spec.parse_reply(reply)
check("a bullet, a dot and a number are all stripped",
      names[:3], ["In-market vehicle buyers", "Auto-intenders", "Recent Movers"])
check("a heading line is left out",
      any(n.endswith(":") for n in names), False)
check("an over-long line is left out",
      any(len(n) > spec.NAME_MAX for n in names), False)

rows = spec.candidates(["Homeowners", "homeowners", "Parents", "", "Pet Owners"],
                       existing=["Parents"])
check("candidates dedupe case-insensitively against themselves",
      [r["name"] for r in rows], ["Homeowners", "Pet Owners"])
check("everything arrives accepted: False — ticked by a person or not at all",
      all(r["accepted"] is False for r in rows), True)
check("the batch is capped at the Pickaxe's own twenty",
      len(spec.candidates([f"Segment {i}" for i in range(40)])), 20)


# =====================================================================
section("2. The store: confirm, read, clear")
# =====================================================================

CLIENT = "Acme Plumbing"

before = spec.get(CLIENT)
check("an unconfirmed client reads picked: False", before["picked"], False)
check("with no audiences", before["audiences"], [])
check("and for_prompt is empty — the caller says 'not provided' in its "
      "own words", spec.for_prompt(CLIENT), "")

r = spec.confirm(CLIENT, [], actor="todd@smart1marketing.com")
check("confirming nothing is refused", r["ok"], False)
check("and the refusal names Clear as the other verb",
      "Clear" in r["error"], True)
check("a nameless confirm is refused",
      spec.confirm("", ["Homeowners"])["ok"], False)

r = spec.confirm(CLIENT, ["Homeowners", "  Recent Movers ", "homeowners"],
                 target="plumbing; emergency repair",
                 source="pickaxe", actor="todd@smart1marketing.com")
check("a real confirm succeeds", r["ok"], True)
row = spec.get(CLIENT)
check("deduped and trimmed on the way in",
      row["audiences"], ["Homeowners", "Recent Movers"])
check("who confirmed it is recorded",
      row["updated_by"], "todd@smart1marketing.com")
check("and when", bool(row["updated_at"]), True)
check("the target it was found against travels with it",
      row["target"], "plumbing; emergency repair")
check("for_prompt is the segments as one line",
      spec.for_prompt(CLIENT), "Homeowners, Recent Movers")

r = spec.confirm(CLIENT, ["Pet Owners"], actor="todd@smart1marketing.com")
check("a second confirm replaces rather than appends — the confirmed "
      "audience is the current answer",
      spec.get(CLIENT)["audiences"], ["Pet Owners"])

r = spec.confirm(CLIENT, [f"Segment {i}" for i in range(20)])
check("a confirm past the cap keeps the first dozen",
      len(spec.get(CLIENT)["audiences"]), spec.MAX_SEGMENTS)
check("and says so rather than dropping the rest in silence",
      "not saved" in r.get("note", ""), True)

r = spec.clear(CLIENT, actor="todd@smart1marketing.com")
check("clear always succeeds", r["ok"], True)
check("and the record reads unconfirmed again",
      spec.get(CLIENT)["picked"], False)


# =====================================================================
section("3. Propose: the Pickaxe, the labeled fallback, and no silent write")
# =====================================================================

from hub import ai as hub_ai                                   # noqa: E402
from hub import pickaxe as hub_pickaxe                         # noqa: E402
from hub.pickaxe_registry import AUDIENCE_FINDER               # noqa: E402

check("no client named is refused before anything is asked",
      spec.propose("")["ok"], False)

calls = {}


def fake_ask(pickaxe_id, **kw):
    calls["id"] = pickaxe_id
    calls["kw"] = kw
    return "- Homeowners\n- Recent Movers\n- Pet Owners"


_real_ask = hub_pickaxe.ask
hub_pickaxe.ask = fake_ask
try:
    spec.confirm(CLIENT, ["Pet Owners"], actor="t")
    res = spec.propose(CLIENT, sells="tankless water heaters")
finally:
    hub_pickaxe.ask = _real_ask

check("the Pickaxe path answers with its source named", res["source"], "pickaxe")
check("and the note says it is the agency catalog",
      "agency audience catalog" in res["note"], True)
check("the right Pickaxe was asked", calls["id"], AUDIENCE_FINDER["pickaxe_id"])
target_field = AUDIENCE_FINDER["fields"]["target"]
check("what the rep typed reached the Pickaxe's target field",
      "tankless water heaters" in calls["kw"]["inputs"][target_field], True)
check("a segment already confirmed is not offered again",
      [r["name"] for r in res["audiences"]], ["Homeowners", "Recent Movers"])
check("everything arrives unaccepted",
      all(r["accepted"] is False for r in res["audiences"]), True)
check("proposing wrote nothing — confirm is the only write",
      spec.get(CLIENT)["audiences"], ["Pet Owners"])

ai_calls = {}


def fail_ask(*a, **kw):
    raise hub_pickaxe.PickaxeUnavailable("PICKAXE_API_KEY is not set.")


def fake_chat(messages, **kw):
    ai_calls["kw"] = kw
    ai_calls["prompt"] = messages[0]["content"]
    return json.dumps({"audiences": ["Homeowners", "DIY Researchers"]})


hub_pickaxe.ask = fail_ask
_real_chat = hub_ai.chat
hub_ai.chat = fake_chat
try:
    res = spec.propose(CLIENT, sells="tankless water heaters")
finally:
    hub_pickaxe.ask = _real_ask
    hub_ai.chat = _real_chat

check("with the Pickaxe down the Hub's own AI answers", res["source"], "ai")
check("and the note says which one wrote it — a catalog answer and general "
      "knowledge are different confidences",
      "could not be reached" in res["note"], True)
check("the fallback is filed under its own purpose so spend reads per tool",
      ai_calls["kw"]["purpose"], "audience_finder_fallback")
check("and still excludes what is already confirmed",
      [r["name"] for r in res["audiences"]], ["Homeowners", "DIY Researchers"])


def fail_chat(*a, **kw):
    raise RuntimeError("model down")


hub_pickaxe.ask = fail_ask
hub_ai.chat = fail_chat
try:
    res = spec.propose(CLIENT, sells="x")
finally:
    hub_pickaxe.ask = _real_ask
    hub_ai.chat = _real_chat

check("both failing is an error, never an empty audience", res["ok"], False)
check("and it says nothing changed on the record",
      "nothing has been changed" in res["error"].lower(), True)

spec.clear(CLIENT)


# =====================================================================
section("4. The four readers")
# =====================================================================

# --- Reader 1: AD_COPY's {audience} prefill ---------------------------
from modules.ads_builder import copy_ideas                     # noqa: E402

spec.confirm("Riverside HVAC", ["Homeowners", "Recent Movers"], actor="t")
pf = copy_ideas.prefill_ad_copy({"businessName": "Riverside HVAC"})
check("the confirmed audience fills an untyped campaign's blank",
      pf["audience"], "Homeowners, Recent Movers")
pf = copy_ideas.prefill_ad_copy({"businessName": "Riverside HVAC",
                                 "targetAudience": "fleet managers"})
check("a value typed on the campaign wins — the overlay rule",
      pf["audience"], "fleet managers")
pf = copy_ideas.prefill_ad_copy({"businessName": "Nobody Known"})
check("an unconfirmed client still reads 'not provided', never an invented one",
      pf["audience"].startswith("not provided"), True)
spec.clear("Riverside HVAC")

# --- Reader 2: the Proposal Builder ----------------------------------
sb_app = (ROOT / "modules" / "sales_builder" / "app.py").read_text(encoding="utf-8")
check("the proposal route reads the shared parse rather than a copy",
      "from hub.audience_spec import parse_reply as _parse_audience_reply"
      in sb_app, True)
check("and no second definition of it survives",
      "def _parse_audience_reply" in sb_app, False)
check("its candidate shaping is the shared one too",
      "_audience_candidates(names, existing)" in sb_app, True)

sb_html = (ROOT / "modules" / "sales_builder" / "templates" /
           "index.html").read_text(encoding="utf-8")
check("the audience step carries the on-file offer",
      'id="audOnFileWrap"' in sb_html, True)
check("drawn when the step draws", "drawAudOnFile();" in sb_html, True)
check("it reads the hub route", "'/api/client/audience?name='" in sb_html, True)
check("and adding is a press, never automatic",
      "nothing is added by itself" in sb_html.lower(), True)

# --- Reader 3: the IO Builder ----------------------------------------
io_html = (ROOT / "modules" / "io_builder" / "templates" /
           "index.html").read_text(encoding="utf-8")
check("the IO builder looks the confirmed audience up when the client is known",
      "function loadConfirmedAudience(client)" in io_html, True)
check("its segments join the audiences question's options",
      "state.confirmedAudience||[]" in io_html.replace("(", "").replace(")", "")
      or "...(state.confirmedAudience||[])" in io_html, True)
check("with a line saying where they came from",
      "On file from Client 360" in io_html, True)

# --- Reader 4: the Client 360 card -----------------------------------
c360 = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
check("the record draws a Target audience card", 'id="c-audience"' in c360, True)
check("loaded with the rest of the record", "loadAudience(name);" in c360, True)
check("Find is a button that says it is billed",
      "This call is billed, which is why it is a button" in c360, True)
check("a read that failed never renders as 'none confirmed'",
      "not the same as no audience being confirmed" in c360, True)

from hub import help as hub_help                               # noqa: E402

check("the card's help bubble has an entry behind it",
      bool(hub_help.get("hub.client360.audience")), True)


# =====================================================================
section("5. The routes: guarded, and the verbs kept apart")
# =====================================================================

from hub import create_hub_app                                 # noqa: E402

app = create_hub_app()
anon = app.test_client()

r = anon.get("/api/client/audience?name=Acme",
             headers={"Accept": "application/json"})
check("the read refuses an anonymous request", r.status_code in (302, 401), True)
r = anon.post("/api/client/audience/find", json={"name": "Acme"},
              headers={"Accept": "application/json"})
check("so does the billed button", r.status_code in (302, 401), True)
r = anon.post("/api/client/audience", json={"name": "Acme", "audiences": ["x"]},
              headers={"Accept": "application/json"})
check("and the write", r.status_code in (302, 401), True)

from hub import auth as hub_auth                               # noqa: E402

c = app.test_client()
c.set_cookie(hub_auth.COOKIE_NAME,
             hub_auth.issue_cookie_value("tester@smart1marketing.com"))

r = c.get("/api/client/audience")
check("a nameless read is refused rather than filed under one key",
      r.status_code, 400)

r = c.get("/api/client/audience?name=Fresh Client")
check("signed in, an unconfirmed client answers", r.status_code, 200)
check("as picked: False", r.get_json()["picked"], False)

r = c.post("/api/client/audience",
           json={"name": "Fresh Client", "audiences": []})
check("an empty confirm is a 400, not a clear", r.status_code, 400)

r = c.post("/api/client/audience",
           json={"name": "Fresh Client",
                 "audiences": ["Homeowners", "Recent Movers"],
                 "target": "roofing", "source": "pickaxe"})
check("a confirm lands", r.status_code, 200)
d = r.get_json()["audience"]
check("with the segments", d["audiences"], ["Homeowners", "Recent Movers"])
check("and the signed-in account on it",
      d["updated_by"], "tester@smart1marketing.com")

import hub.audience_spec as spec_mod                           # noqa: E402

_real_propose = spec_mod.propose
spec_mod.propose = lambda *a, **kw: {"ok": True, "audiences": [],
                                     "source": "pickaxe", "target": "t",
                                     "note": "n"}
try:
    r = c.post("/api/client/audience/find", json={"name": "Fresh Client"})
    check("find answers through audience_spec.propose", r.status_code, 200)
finally:
    spec_mod.propose = _real_propose

spec_mod.propose = lambda *a, **kw: {"ok": False,
                                     "error": "The audience research did not run",
                                     "detail": "x"}
try:
    r = c.post("/api/client/audience/find", json={"name": "Fresh Client"})
    check("a research failure is a 502, never an empty success",
          r.status_code, 502)
finally:
    spec_mod.propose = _real_propose

r = c.post("/api/client/audience", json={"name": "Fresh Client", "clear": True})
check("clear is its own verb and succeeds", r.status_code, 200)
check("after which the record reads unconfirmed",
      c.get("/api/client/audience?name=Fresh Client").get_json()["picked"],
      False)


# =====================================================================
print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    sys.exit(1)
print("  All checks passed.")
