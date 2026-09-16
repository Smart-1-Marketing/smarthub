"""modules/scans/score.py and hub.leads.tag_temperature -- WO-3d.

    python3 test_lead_score.py

Same shape as the other test files here -- no pytest, no new dependencies.
"""
import os
import shutil
import sys
import tempfile
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1leadscore_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}  {detail}")


def section(title):
    print(f"\n== {title} ==")


from modules.scans import score
from hub import leads


# ---------------------------------------------------------------------------
section("lead_temperature() bands three fixture reports correctly")

HOT_REPORT = {
    "overall_score": 25,
    "paid_search": {"average_adspend": 1500, "has_adwords_spend": True},
    "google_business_profile": {"review_count": 40},
}
temp, reasons = score.lead_temperature(HOT_REPORT)
check("badly-broken + real spend -> hot", temp == "hot", (temp, reasons))
check("reasons name the score", any("25" in r for r in reasons), reasons)

WARM_REPORT = {
    "overall_score": 55,
    "paid_search": {"average_adspend": None, "has_adwords_spend": False},
    "reviews": {"total_reviews_count": 4},
}
temp2, reasons2 = score.lead_temperature(WARM_REPORT)
check("mid score + few reviews -> warm", temp2 == "warm", (temp2, reasons2))
check("reasons name the low review count", any("4" in r for r in reasons2), reasons2)

COLD_REPORT = {
    "overall_score": 88,
    "paid_search": {"average_adspend": None, "has_adwords_spend": False},
    "reviews": {"total_reviews_count": 120},
    "facebook_ads": {"fb_ads_currently_active": False},
    "built_by_us": {"is_own_vendor": False},
}
temp3, reasons3 = score.lead_temperature(COLD_REPORT)
check("healthy site, no signals -> cold", temp3 == "cold", (temp3, reasons3))


# ---------------------------------------------------------------------------
section("A report with no paid_search block scores on the rest and says so")
NO_PAID_SEARCH = {
    "overall_score": 30,
    "google_business_profile": {"review_count": 2},
}
temp4, reasons4 = score.lead_temperature(NO_PAID_SEARCH)
check("still bands on the score alone", temp4 == "hot", (temp4, reasons4))
check("says spend was not measured",
      any("not measured" in r.lower() for r in reasons4), reasons4)


# ---------------------------------------------------------------------------
section("lead_temperature() never raises on a broken/empty report")
temp5, reasons5 = score.lead_temperature({})
check("empty report -> cold, empty-ish reasons", temp5 == "cold")
temp6, reasons6 = score.lead_temperature(None)
check("None report -> cold, no crash", temp6 == "cold")


# ---------------------------------------------------------------------------
section("hub.leads.tag_temperature() tags a stored lead, never re-delivers")

row = leads.capture("scan_widget", "aeo-check", {
    "name": "Jamie Rep", "email": "jamie@example.com", "phone": "3175550100",
    "company": "Acme Plumbing",
})
lead_id = row["id"]

with mock.patch("hub.ghl_contacts.upsert") as mocked_upsert:
    updated = leads.tag_temperature(lead_id, "hot", ["Overall audit score is 25."])
check("returns the updated row", updated is not None and updated["id"] == lead_id)
check("scan_hot tag written into meta.tags",
      "scan_hot" in (updated.get("meta") or {}).get("tags", []), updated.get("meta"))
check("reasons stored on the row",
      updated["meta"]["scan_temperature_reasons"] == ["Overall audit score is 25."])
check("no contact id yet -> ghl_contacts.upsert is NOT called (no re-delivery)",
      not mocked_upsert.called)

fetched = leads.get(lead_id)
check("the stored row itself carries the tag",
      "scan_hot" in (fetched.get("meta") or {}).get("tags", []))

# A rescan replaces the temperature tag rather than stacking a second one.
with mock.patch("hub.ghl_contacts.upsert"):
    leads.tag_temperature(lead_id, "cold", ["Healthy site."])
after = leads.get(lead_id)
tags = after["meta"]["tags"]
check("only one scan_* temperature tag survives a rescan",
      tags.count("scan_cold") == 1 and "scan_hot" not in tags, tags)


# ---------------------------------------------------------------------------
section("Once delivered, tagging pushes the update through ghl_contacts.upsert")
row2 = leads.capture("scan_widget", "aeo-check", {
    "name": "Pat Rep", "email": "pat@example.com",
})
row2["contact_id"] = "ghl-contact-123"
leads._update(row2)                                       # noqa: SLF001

with mock.patch("hub.ghl_contacts.upsert", return_value={"ok": True, "contact_id": "ghl-contact-123"}) as mocked:
    leads.tag_temperature(row2["id"], "warm", ["Few reviews."])
check("upsert called exactly once to push the tag update", mocked.call_count == 1)
check("never went through capture_and_deliver again (no new lead row)",
      len([r for r in leads._read_all() if r.get("email") == "pat@example.com"]) == 1)  # noqa: SLF001


# ---------------------------------------------------------------------------
section("Unknown lead id, or a bad temperature, is refused rather than guessed")
check("unknown lead id -> None", leads.tag_temperature("no-such-id", "hot", []) is None)
check("bad temperature word -> None", leads.tag_temperature(lead_id, "lukewarm", []) is None)


# ---------------------------------------------------------------------------
section("The optional Suite note field degrades gracefully when unset")
from hub import ghl_contacts
check("scan_score_field_id() is empty by default (declared, not required)",
      ghl_contacts.scan_score_field_id() == "")
payload_row = leads.get(lead_id)
with mock.patch.dict(os.environ, {"GHL_LEAD_LOCATION_ID": "loc_test"}):
    body = ghl_contacts.payload_for(payload_row)
    with mock.patch.dict(os.environ, {"GHL_SCAN_SCORE_NOTE_FIELD_ID": "field_999"}):
        body2 = ghl_contacts.payload_for(payload_row)
check("no customFields entry invented for an unset field id",
      not any("scan" in str(cf).lower() for cf in body.get("customFields", [])))
check("the note field is used once configured",
      any(cf.get("id") == "field_999" for cf in body2.get("customFields", [])), body2)


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
