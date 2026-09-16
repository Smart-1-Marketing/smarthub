"""hub/industry.py -- the one canonical taxonomy, resolved and written down.

    python3 test_industry.py

Same shape as the other test files here -- no pytest, no new dependencies.
Runs against a throwaway data directory so nothing here touches the real
`/var/data` or a shared database.

No Knack in this pass: there is no "knack" tier in resolve_industry()'s
precedence, and the remaining tiers keep the confidences they were written
with (0.8 / 0.7 / 0.7 / 0.3 / 0.0) rather than being renumbered to fill the
gap Knack's 0.9 tier left.
"""
import os
import shutil
import sys
import tempfile
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1industry_test_")
os.environ["HUB_DATA_DIR"] = TMP
# Both, deliberately: jsonstore mirrors every write into the database, and a
# fresh HUB_DATA_DIR in front of an inherited DATABASE_URL is refilled with
# whatever a previous run last wrote for the same client names (CLAUDE.md's
# own trap, in "A test's throwaway data directory is the same trap wearing a
# harness"). Own directory, own database, or this file is flaky by rerun.
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
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import industry, seo, audit                                # noqa: E402


def _fresh_client(name: str):
    path = os.path.join(seo._store_base(), seo.slugify(name) + ".json")  # noqa: SLF001
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
section("resolve() substring regression -- Roofing Contractor is not HVAC")
check('resolve("Roofing Contractor") == home_services',
      industry.resolve("Roofing Contractor")[0] == "home_services",
      industry.resolve("Roofing Contractor"))

section("resolve() -- earliest alias wins, boat vs auto dealership")
check('resolve("Boat Dealership") == marine',
      industry.resolve("Boat Dealership")[0] == "marine")
check('resolve("Auto Dealership") == automotive',
      industry.resolve("Auto Dealership")[0] == "automotive")

section("resolve() -- subtype")
check('resolve("Personal Injury Attorney") == (legal, pi)',
      industry.resolve("Personal Injury Attorney") == ("legal", "pi"),
      industry.resolve("Personal Injury Attorney"))

section("resolve() -- government and legacy spellings")
check('resolve("City of Dublin") == government',
      industry.resolve("City of Dublin")[0] == "government")
check('resolve("boat") == marine (legacy key)',
      industry.resolve("boat")[0] == "marine")
check('resolve("auto") == automotive (legacy key)',
      industry.resolve("auto")[0] == "automotive")

section("resolve() -- unknown text falls back to general")
check('resolve("") == general', industry.resolve("")[0] == "general")
check('resolve(None) == general', industry.resolve(None)[0] == "general")
check('resolve("Widgets Inc") == general', industry.resolve("Widgets Inc")[0] == "general")


# ---------------------------------------------------------------------------
section("resolve_industry() precedence, remaining (non-Knack) tiers")
_fresh_client("Fixture Restaurant Co")
report = {
    "meta": {"primary_industry": "Restaurant"},
    "google_business_profile": {"gmb_industries": "Legal Services"},
}
with mock.patch("hub.scan_facts.latest_report", return_value=(report, {}, "")):
    got = industry.resolve_industry(client="Fixture Restaurant Co", domain="fixture.test")
check("scan_primary (0.8) outranks gbp (0.7)", got["source"] == "scan_primary", got)
check("and lands on the scan's own key", got["key"] == "restaurant", got)
check("confidence is 0.8", got["confidence"] == 0.8)

_fresh_client("Fixture Legal Only")
report2 = {"google_business_profile": {"gmb_industries": "Law Firm"}}
with mock.patch("hub.scan_facts.latest_report", return_value=(report2, {}, "")):
    got2 = industry.resolve_industry(client="Fixture Legal Only", domain="fixture2.test")
check("gbp used when scan_primary/scan_sectors are silent", got2["source"] == "gbp", got2)
check("and resolves to legal", got2["key"] == "legal", got2)

_fresh_client("Riverside HVAC")
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    got3 = industry.resolve_industry(client="Riverside HVAC")
check("falls back to the client's own name", got3["source"] == "name", got3)
check("and resolves hvac from the name", got3["key"] == "hvac", got3)

_fresh_client("Totally Unknown Business LLC")
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    got4 = industry.resolve_industry(client="Totally Unknown Business LLC")
check("nothing resolves -> general at confidence 0.0",
      got4 == {"key": "general", "subtype": "", "source": "general",
               "confidence": 0.0, "evidence": ""}, got4)


# ---------------------------------------------------------------------------
section("A manual key survives a lower-precedence re-resolve")
client = "Fixture Manual Co"
_fresh_client(client)
industry.set_manual(client, "hvac", actor="todd")
before = audit.read(limit=1, module="hub", type_="industry_resolved")
with mock.patch("hub.scan_facts.latest_report",
                return_value=({"meta": {"primary_industry": "Restaurant"}}, {}, "")):
    result = industry.resolve_industry(client=client, domain="fixture-manual.test")
    wrote = industry.write_industry(client, result)
check("manual tier answers, restaurant never seen", result["source"] == "manual")
check("write_industry refuses to overwrite manual", wrote is False)
after = audit.read(limit=1, module="hub", type_="industry_resolved")
check("no new industry_resolved row was logged for the refused write",
      before == after)

section("A higher-precedence source rewrites and logs")
client2 = "Fixture Rewrite Co"
_fresh_client(client2)
industry.write_industry(client2, {"key": "restaurant", "subtype": "",
                                  "source": "name", "confidence": 0.3,
                                  "evidence": "Fixture Rewrite Co"})
stored_before = industry._stored_industry(client2)                  # noqa: SLF001
wrote2 = industry.write_industry(client2, {"key": "legal", "subtype": "",
                                           "source": "scan_primary",
                                           "confidence": 0.8, "evidence": "Law Firm"},
                                 actor="scan")
stored_after = industry._stored_industry(client2)                    # noqa: SLF001
check("stored source starts at name", stored_before.get("source") == "name")
check("a scan_primary result over it is written", wrote2 is True)
check("the stored key moved", stored_after.get("key") == "legal", stored_after)
rows = audit.read(limit=5, module="hub", type_="industry_resolved")
match = next((r for r in rows if r.get("client") == client2), None)
check("exactly one industry_resolved row logged, carrying previous=",
      match is not None and match.get("previous") == "restaurant", match)

section("A client with no sources writes general at confidence 0")
client3 = "Fixture Nothing Co Xyz"
_fresh_client(client3)
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    result3 = industry.resolve_industry(client=client3)
    industry.write_industry(client3, result3)
stored3 = industry._stored_industry(client3)                         # noqa: SLF001
check("stored as general", stored3.get("key") == "general")
check("confidence recorded as 0.0", stored3.get("confidence") == 0.0)
diag = industry.diagnostics_summary()
check("diagnostics is measured", diag.get("measured") is True)
check("that client appears on the general list",
      client3 in diag.get("general_clients", []), diag.get("general_clients"))


# ---------------------------------------------------------------------------
section("industry() accepts a legacy key")
check('industry("boat") is the marine dict', (industry.industry("boat") or {}).get("key") == "marine")
check('industry("nonsense") is None', industry.industry("nonsense") is None)


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
