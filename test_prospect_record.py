"""Prospect 360: the record a scanned business gets before it is a client.

    python3 test_prospect_record.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

A website audit produced a row in a flat table: a name, an email and a
delivery pill. Everything that made the prospect worth calling was scattered
across four tools and one CRM. This is the record that joins them up, and
every failure below is one where the page would go on looking healthy while
telling somebody something false about a prospect.

  1. **Four empties, and only one of them means "chase this".** Suite not
     configured, the lead never delivered, Suite refused the read, and Suite
     read fine and there is no deal yet are four different situations. Three
     of them are *not measured*; collapsing them into "no stage" sends
     somebody to the wrong screen or, worse, makes them stop chasing.

  2. **A section that fails costs only itself.** The audit is worth reading
     when Suite is down and the notes are worth reading when Insites is.

  3. **A timeline that quietly loses a week is worse than no timeline.** What
     could not be read is *named* on it rather than shortening it in silence.

  4. **The Hub never decides a stage.** Suite is the CRM. A note typed here is
     written onto the Suite contact, and a prospect with no contact is refused
     by name rather than having the note kept in a second notebook only this
     Hub can read.

  5. **Deleting a file reports two outcomes, not one.** The record row and the
     stored copy are separate things and one tick covering both is how
     somebody learns not to trust the tick.

  6. **Converting is a link, never a creation.** A client is a business with a
     product in Knack; inventing one here produces an account the Hub shows
     and no invoice ever mentions.

  7. **A merged lead's link still works.** The id is in browser history, and
     it has to resolve to the survivor rather than to a record that has been
     folded into another one.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1prospect_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "prospect-test-secret"
os.environ["PANEL_PASSWORD"] = "prospect-test-password"
os.environ["HUB_LEADS_FILE"] = os.path.join(DISK, "leads.jsonl")
# No Cloudinary in a test, so hub/storage falls back to disk. That is the
# path worth exercising anyway: it is what a deployment with the key missing
# actually does, and the record must not pretend the file went to the cloud.
os.environ.pop("CLOUDINARY_URL", None)

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


from hub import leads, prospect                              # noqa: E402


# =====================================================================
section("A lead resolves to a record, and a merged one to its survivor")
# =====================================================================

alpha = leads.capture("website_audit", "website-audit",
                      {"name": "Dana Roe", "email": "dana@bellows.com",
                       "phone": "3175550188", "company": "Bellows Heating",
                       "website": "bellows.com", "audit_score": "62"},
                      meta={"domain": "bellows.com",
                            "audit_url": "/scans/scan/sc1"})
beta = leads.capture("scan_widget", "smart1-home",
                     {"email": "dana@bellows.com", "company": "Bellows Heating"})

check("a lead is found by its id", leads.get(alpha["id"])["id"], alpha["id"])
check("an id nobody has is None", leads.get("nope"), None)
check("and so is an empty one", leads.get(""), None)

leads.merge(alpha["id"], [beta["id"]], actor="tester")
check("the merged row's own link resolves to the survivor",
      leads.get(beta["id"])["id"], alpha["id"])
check("and the survivor still resolves to itself",
      leads.get(alpha["id"])["id"], alpha["id"])

# A cycle is a bug, not a shape to trust: the walk has a ceiling so a record
# request cannot hang on one.
rows = leads._read_all()
for r in rows:
    if r["id"] == alpha["id"]:
        r["merged_into"] = beta["id"]
leads._rewrite(rows)
check("a merge cycle answers None rather than hanging",
      leads.get(alpha["id"]), None)
for r in rows:
    if r["id"] == alpha["id"]:
        r.pop("merged_into")
leads._rewrite(rows)


# =====================================================================
section("Every section says which kind of empty it is")
# =====================================================================

rec = prospect.record(alpha["id"])
check("the record is found", rec["found"], True)
SECTIONS = ["audit", "scans", "suite", "proposals", "work", "assets",
            "duplicates", "timeline"]
check("every card is present", sorted(k for k in SECTIONS if k in rec),
      sorted(SECTIONS))
check("and every one of them says whether it was measured",
      all("measured" in rec[k] and "note" in rec[k] for k in SECTIONS), True)
check("a section carrying an error is never also 'measured'",
      all(not (rec[k]["error"] and rec[k]["measured"]) for k in SECTIONS), True)

check("with no scan table, the audit is not measured",
      rec["audit"]["measured"], False)
check("and the scan history says so rather than showing none",
      rec["scans"]["measured"], False)
check("a source that failed does not cost the ones that did not",
      rec["proposals"]["measured"] and rec["duplicates"]["measured"], True)

missing = prospect.record("nothing-like-this")
check("a prospect nobody has is answered, not raised", missing["found"], False)
check("and the answer says why", bool(missing["note"]), True)


# =====================================================================
section("Where it has got to is read from Suite, and never decided here")
# =====================================================================

state = rec["suite"]
check("with Suite unconfigured this is not measured", state["measured"], False)
check("and it says which kind of nothing that is", state["state"], "unconfigured")
check("in words a reader can act on",
      "not a prospect nobody has moved" in state["note"], True)


class _Suite:
    """A stand-in for hub/suite_opportunity, so the four empties can be
    exercised without a Suite to talk to."""

    def __init__(self, *, configured=True, contact=None, opps=None,
                 notes=None, raises=None):
        self._configured = configured
        self._contact = contact or {"id": "c1", "name": "Dana Roe", "tags": ["audit"]}
        self._opps = opps
        self._notes = notes
        self._raises = raises or {}

    def configured(self):
        return self._configured

    def _maybe(self, key):
        if key in self._raises:
            raise RuntimeError(self._raises[key])

    def contact_snapshot(self, cid):
        self._maybe("contact")
        return dict(self._contact, id=cid)

    def opportunities_for(self, cid):
        self._maybe("opps")
        return list(self._opps or [])

    def notes_for(self, cid, limit=20):
        self._maybe("notes")
        return list(self._notes or [])


def with_suite(stub, lead):
    """Run suite_state against a stand-in.

    Both the package attribute and sys.modules are swapped: `from hub import
    suite_opportunity` reads the attribute off the already-imported package,
    so patching sys.modules alone changes nothing and the test would quietly
    exercise the real module instead of the stub.
    """
    import hub
    real_mod = sys.modules.get("hub.suite_opportunity")
    real_attr = getattr(hub, "suite_opportunity", None)
    sys.modules["hub.suite_opportunity"] = stub
    hub.suite_opportunity = stub
    try:
        return prospect.suite_state(lead)
    finally:
        if real_mod is not None:
            sys.modules["hub.suite_opportunity"] = real_mod
        else:
            sys.modules.pop("hub.suite_opportunity", None)
        if real_attr is not None:
            hub.suite_opportunity = real_attr
        else:
            delattr(hub, "suite_opportunity")


undelivered = with_suite(_Suite(), {"contact_id": "", "delivered": False})
check("a lead that never reached Suite is not measured either",
      undelivered["measured"], False)
check("and is named as a delivery problem", undelivered["state"], "undelivered")
check("pointing at the screen that fixes it",
      "Leads panel" in undelivered["note"], True)

refused = with_suite(_Suite(raises={"contact": "403 Forbidden"}),
                     {"contact_id": "c1"})
check("a Suite that refuses is not measured", refused["measured"], False)
check("and is a different answer again", refused["state"], "error")
check("carrying what actually happened", "403" in refused["error"], True)

empty = with_suite(_Suite(opps=[], notes=[]), {"contact_id": "c1"})
check("read, with no deal open, IS measured", empty["measured"], True)
check("and that is the one empty that means open one",
      "should" in empty["note"], True)

live = with_suite(_Suite(
    opps=[{"id": "o1", "name": "Bellows — Marketing", "stage": "Proposal sent",
           "pipeline": "Sales", "stage_measured": True, "status": "open",
           "value": 4200, "updated": "2026-08-20 10:00:00"}],
    notes=[{"id": "n1", "body": "Called, wants CTV numbers",
            "created": "2026-08-21 09:00:00"}]), {"contact_id": "c1"})
check("a real deal comes through", live["rows"][0]["stage"], "Proposal sent")
check("with the notes beside it", live["notes"][0]["body"][:6], "Called")
check("both marked measured",
      live["opportunities_measured"] and live["notes_measured"], True)

half = with_suite(_Suite(opps=[{"id": "o1"}], raises={"notes": "timeout"}),
                  {"contact_id": "c1"})
check("notes failing does not cost the deals",
      half["opportunities_measured"], True)
check("and the notes say so on their own",
      half["notes_measured"], False)


# =====================================================================
section("Files on a prospect")
# =====================================================================

added = prospect.add_asset(alpha["id"], "mockup.png", b"x" * 2048,
                           label="Home page mock-up", actor="tester")
check("a file can be filed against a prospect", added["ok"], True)
check("it records how it was stored", added["asset"]["backend"], "disk")
check("and who added it", added["asset"]["added_by"], "tester")
check("it is on the record", len(prospect.assets_for(alpha["id"])), 1)

check("an empty file is refused",
      prospect.add_asset(alpha["id"], "x.png", b"")["ok"], False)
big = prospect.add_asset(alpha["id"], "big.bin",
                         b"x" * ((prospect.MAX_ASSET_MB + 1) * 1024 * 1024))
check("an oversized one is refused", big["ok"], False)
check("with the limit named in the refusal",
      str(prospect.MAX_ASSET_MB) in big["error"], True)
check("a file with no prospect is refused",
      prospect.add_asset("", "x.png", b"xx")["ok"], False)

gone = prospect.delete_asset(alpha["id"], added["asset"]["id"], actor="tester")
check("deleting answers ok", gone["ok"], True)
check("and reports the record and the stored copy apart",
      sorted(k for k in gone
             if k in ("removed_from_record", "stored_copy_removed")),
      ["removed_from_record", "stored_copy_removed"])
check("the note says which of the two happened",
      "stored copy" in gone["note"] or "deleted from storage" in gone["note"], True)
check("the row leaves the record", prospect.assets_for(alpha["id"]), [])
check("but is kept in the file rather than dropped",
      any(r.get("deleted") for r in
          prospect._all_assets().get(alpha["id"], [])), True)
check("deleting it twice is refused",
      prospect.delete_asset(alpha["id"], added["asset"]["id"])["ok"], False)

# Its own folder, not the client tree: a prospect has no client key yet, and
# filing them together is how one company's assets land on another's record.
from hub.config import settings                              # noqa: E402
check("prospect files have a folder of their own",
      settings.folder("prospects") != settings.folder("proposals"), True)


# =====================================================================
section("The timeline names what it could not read")
# =====================================================================

tl = rec["timeline"]
check("the timeline is measured even when its sources are not",
      tl["measured"], True)
check("it names the sources it could not read",
      "the scan history" in tl["incomplete"], True)
check("and says so in words on the page",
      "incomplete" in tl["note"], True)
check("what it does have is newest first",
      tl["rows"] == sorted(tl["rows"], key=lambda e: e["when"], reverse=True), True)
check("the lead's own arrival is on it",
      any(e["kind"] == "lead" for e in tl["rows"]), True)
check("including the row that was merged in",
      sum(1 for e in tl["rows"] if e["kind"] == "lead") >= 2, True)


# =====================================================================
section("Becoming a client is a link, never a creation")
# =====================================================================

nope = prospect.convert(alpha["id"], "A Business Nobody Holds")
check("an unknown client is refused", nope["ok"], False)
check("and named, with where to create it",
      "Knack" in nope["error"], True)
check("a blank name is refused too",
      prospect.convert(alpha["id"], "")["ok"], False)
check("so is an unknown prospect",
      prospect.convert("nope", "Anything")["ok"], False)

import hub.clients_registry as registry                       # noqa: E402
_real_find = registry.find_client
registry.find_client = lambda name: ({"name": "Bellows Heating Co"}
                                     if "bellows" in name.lower() else None)
try:
    prospect.add_asset(alpha["id"], "quote.pdf", b"y" * 512, actor="tester")
    done = prospect.convert(alpha["id"], "bellows heating co", actor="tester")
    check("a real client converts", done["ok"], True)
    check("under the registry's own spelling of the name",
          done["client"], "Bellows Heating Co")
    check("and the files move across with it", done["assets_carried"], 1)
    check("the file now names the client",
          prospect.assets_for(alpha["id"])[0]["client"], "Bellows Heating Co")
    check("the lead records what it became",
          leads.get(alpha["id"])["client"], "Bellows Heating Co")
finally:
    registry.find_client = _real_find


# =====================================================================
section("The routes: guarded, and the note goes where the history is")
# =====================================================================

from hub import auth as hub_auth, create_hub_app             # noqa: E402

app = create_hub_app()
c = app.test_client()

r = c.get(f"/prospect/{alpha['id']}")
check("the page refuses an anonymous visitor", r.status_code, 302)
check("and sends them to sign in", "/login" in r.headers.get("Location", ""), True)
r = c.get(f"/api/prospect/{alpha['id']}", headers={"Accept": "application/json"})
check("the API refuses one too", r.status_code, 401)
r = c.post(f"/api/prospect/{alpha['id']}/note", json={"note": "hi"},
           headers={"Accept": "application/json"})
check("so does every write", r.status_code, 401)

c.set_cookie(hub_auth.COOKIE_NAME,
             hub_auth.issue_cookie_value("tester@smart1marketing.com"))

r = c.get(f"/prospect/{alpha['id']}")
check("signed in, the page renders", r.status_code, 200)
r = c.get(f"/api/prospect/{alpha['id']}")
check("and the payload answers", r.get_json()["found"], True)

r = c.post(f"/api/prospect/{alpha['id']}/note", json={"note": ""})
check("a note with no words is refused", r.status_code, 400)
r = c.post(f"/api/prospect/{alpha['id']}/note", json={"note": "Called them"})
check("a note on a prospect with no Suite contact is refused", r.status_code, 409)
check("by name, rather than kept in a second notebook",
      "nowhere to go" in r.get_json()["error"], True)

r = c.post("/api/prospect/nobody/note", json={"note": "x"})
check("a note on a prospect nobody has is a 404", r.status_code, 404)


# =====================================================================
section("The Leads panel opens the record, and shows what the scan produced")
# =====================================================================

panel = (ROOT / "hub" / "templates" / "leads.html").read_text()
check("the name cell links into the record",
      "/prospect/'+esc(l.id" in panel, True)
check("the report cell is its own function", "function reportCell(" in panel, True)
check("which reads the audit link a website-audit lead carries",
      "meta.audit_url" in panel, True)
check("as well as the PDF the other widget produces",
      "l.pdf_url" in panel, True)

listing = leads.listing(days=30)
row = next(r for r in listing["leads"] if r["id"] == alpha["id"])
check("the panel row carries the meta the cell reads",
      row["meta"]["audit_url"], "/scans/scan/sc1")


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
