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


# ---------------------------------------------------------------------------
section("One category string: a custom label, and the profile kept in step")
client4 = "Fixture Buckeye Lake Xyz"
_fresh_client(client4)
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    industry.write_industry(client4, industry.resolve_industry(client=client4))
check("the scan-less record starts on general",
      industry._stored_industry(client4).get("key") == "general")        # noqa: SLF001
check("...and the profile's category reads General Business with it",
      seo.get_profile(client4)["category"] == "General Business")
check("a typed-your-own label is saved",
      industry.set_manual(client4, "", actor="todd", custom_label="Winery") is True)
stored4 = industry._stored_industry(client4)                          # noqa: SLF001
check("the wording is kept", stored4.get("custom_label") == "Winery", stored4)
check("and resolved onto the nearest canonical key underneath (winery -> restaurant)",
      stored4.get("key") == "restaurant", stored4)
check("it is a manual pick", stored4.get("source") == "manual")
check("display_label is the wording", industry.display_label(client4) == "Winery")
check("the profile's category is the same string",
      seo.get_profile(client4)["category"] == "Winery")
check("a re-resolve does not move it",
      industry.write_industry(client4, {"key": "hvac", "source": "scan_primary"}) is False)

# The other direction: Client Info's field is typed into.
seo.set_profile(client4, {"category": "HVAC"}, actor="todd")
stored4b = industry._stored_industry(client4)                         # noqa: SLF001
check("a typed profile category becomes the industry pick",
      stored4b.get("key") == "hvac" and stored4b.get("source") == "manual", stored4b)
check("...a label that is a canonical name keeps no custom wording",
      stored4b.get("custom_label") == "")
check("...and both screens read HVAC",
      industry.display_label(client4) == "HVAC" and seo.get_profile(client4)["category"] == "HVAC")
seo.set_profile(client4, {"category": "hvac"}, actor="todd")
check("re-saving the same category (case aside) changes nothing",
      industry._stored_industry(client4).get("resolved_at") == stored4b.get("resolved_at"))  # noqa: SLF001
seo.set_profile(client4, {"category": ""}, actor="todd")
check("an empty category submission is not a pick",
      industry._stored_industry(client4).get("key") == "hvac")           # noqa: SLF001
industry.set_manual(client4, "general", actor="todd")
check("General Business picked on purpose reads General Business, not the old typed text",
      industry.display_label(client4) == "General Business"
      and seo.get_profile(client4)["category"] == "General Business")

# A record nobody has touched, whose profile was typed before this field
# existed, keeps the typed wording rather than reading General Business.
client5 = "Fixture Typed Before Xyz"
_fresh_client(client5)
store5 = seo.load_store(client5)
store5.setdefault("profile", {})["category"] = "Custom Cabinetry"
seo.save_store(client5, store5)
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    industry.write_industry(client5, industry.resolve_industry(client=client5))
check("an untouched general record shows the typed wording",
      industry.display_label(client5) == "Custom Cabinetry"
      and seo.get_profile(client5)["category"] == "Custom Cabinetry")
# The scan is the point of truth until somebody picks by hand.
client6 = "Fixture Scanned Later Xyz"
_fresh_client(client6)
store6 = seo.load_store(client6)
store6.setdefault("profile", {})["category"] = "Something Typed"
seo.save_store(client6, store6)
industry.write_industry(client6, {"key": "legal", "source": "scan_primary",
                                  "confidence": 0.8, "evidence": "Law Firm"})
check("a scan-resolved industry lands on the profile category too",
      seo.get_profile(client6)["category"] == "Legal"
      and industry.display_label(client6) == "Legal")


# ---------------------------------------------------------------------------
section("An alias with its own word: a winery reads Winery, files under restaurant")
check('alias_label("Buckeye Lake Winery Inc") == "Winery"',
      industry.alias_label("Buckeye Lake Winery Inc") == "Winery")
check('alias_label("Acme HVAC") == ""', industry.alias_label("Acme HVAC") == "")
client7 = "Fixture Buckeye Lake Winery Inc Xyz"
_fresh_client(client7)
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    res7 = industry.resolve_industry(client=client7)
check("the name tier resolves restaurant, carrying the alias's label",
      res7["key"] == "restaurant" and res7.get("label") == "Winery", res7)
industry.write_industry(client7, res7)
check("the record reads Winery on both screens without a manual pick",
      industry.display_label(client7) == "Winery"
      and seo.get_profile(client7)["category"] == "Winery")
check("and the Image Picker's key is still the taxonomy's",
      industry._stored_industry(client7).get("key") == "restaurant")        # noqa: SLF001
check("a manual pick of a canonical key clears the alias wording",
      industry.set_manual(client7, "tourism", actor="todd")
      and industry.display_label(client7) == "Tourism")

section("The nightly job gets a turn on a deploy-heavy day")
from hub import scheduler as _sched                                   # noqa: E402
_order = list(_sched.JOBS)
check("industry_resolve runs ahead of the slow provider sweeps",
      _order.index("industry_resolve") < _order.index("knack_products")
      and _order.index("industry_resolve") < _order.index("google_index"),
      _order)
check("...and after the lead retry, which is a person waiting",
      _order.index("retry_leads") < _order.index("industry_resolve"))

section("A general reading is asked again every night")
client8 = "Fixture Nothing Yet Xyz"
_fresh_client(client8)
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    industry.write_industry(client8, industry.resolve_industry(client=client8))
check("stored as general today", industry._stored_industry(client8).get("key") == "general")  # noqa: SLF001
check("...and due for a resweep tomorrow, not in 30 days",
      industry.due_for_resweep(client8) is True)
check("a real reading from today is not due",
      industry.due_for_resweep(client7) is False)


# ---------------------------------------------------------------------------
section("The sweep passes the domain from the SEO store to the resolver")

client_dom = "Fixture Domain Co Xyz"
_fresh_client(client_dom)
store_dom = seo.load_store(client_dom)
store_dom["site_url"] = "https://www.domainco.com/about"
seo.save_store(client_dom, store_dom)
check("_client_domain reads site_url",
      industry._client_domain(client_dom) == "domainco.com")            # noqa: SLF001

_fresh_client("Fixture Attached Web Xyz")
store_att = seo.load_store("Fixture Attached Web Xyz")
store_att.setdefault("attached", {})["website"] = [
    {"name": "Fixture Attached Web Xyz", "domain": "attachedweb.com"}
]
seo.save_store("Fixture Attached Web Xyz", store_att)
check("_client_domain reads attached.website",
      industry._client_domain("Fixture Attached Web Xyz") == "attachedweb.com")  # noqa: SLF001

check("_client_domain is empty when nothing is on file",
      industry._client_domain("Fixture Nothing Yet Xyz") == "")         # noqa: SLF001

# Verify sweep() actually passes the domain to resolve_industry
_fresh_client("Fixture Sweep Domain Xyz")
store_sw = seo.load_store("Fixture Sweep Domain Xyz")
store_sw["site_url"] = "https://sweeptest.example.com"
seo.save_store("Fixture Sweep Domain Xyz", store_sw)
_resolve_calls = []
_orig_resolve = industry.resolve_industry
def _spy_resolve(**kw):
    _resolve_calls.append(kw)
    return _orig_resolve(**kw)
with mock.patch.object(industry, "resolve_industry", side_effect=_spy_resolve):
    with mock.patch.object(industry, "_client_universe",
                           return_value=["Fixture Sweep Domain Xyz"]):
        industry.sweep(limit=10)
check("sweep passed the domain to resolve_industry",
      any(c.get("domain") == "sweeptest.example.com" for c in _resolve_calls),
      _resolve_calls)


# ---------------------------------------------------------------------------
section("Ambiguous name-tier aliases are skipped")

# "bar" inside "Bar Lazy H Percherons" is a horse ranch, not a restaurant.
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "")):
    got_bar = industry.resolve_industry(client="Bar Lazy H Percherons")
check("'Bar Lazy H Percherons' does not resolve to restaurant from the name",
      got_bar["key"] == "general", got_bar)

# But a scan that says "bar" is still valid.
report_bar = {"meta": {"primary_industry": "Bar"}}
with mock.patch("hub.scan_facts.latest_report", return_value=(report_bar, {}, "")):
    got_bar2 = industry.resolve_industry(client="Bar Lazy H Percherons")
check("a scan with 'Bar' still resolves restaurant",
      got_bar2["key"] == "restaurant" and got_bar2["source"] == "scan_primary",
      got_bar2)

with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "")):
    got_county = industry.resolve_industry(client="Tri County Disposal")
check("'Tri County Disposal' does not resolve to government from the name",
      got_county["key"] == "general", got_county)

# A real restaurant name still resolves fine.
with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "")):
    got_real = industry.resolve_industry(client="Joe's Pizza Palace")
check("a real restaurant name still resolves",
      got_real["key"] == "restaurant" and got_real["source"] == "name", got_real)


# ---------------------------------------------------------------------------
section("Legacy typed categories normalize")

check('industry("auto_broker") -> automotive',
      (industry.industry("auto_broker") or {}).get("key") == "automotive")
check('industry("builder") -> real_estate',
      (industry.industry("builder") or {}).get("key") == "real_estate")



# ---------------------------------------------------------------------------
section("The picker mirror writes every row with that name, not one of them")
# ---------------------------------------------------------------------------
# `image_picker_clients.name` carries no unique constraint -- only `slug` does,
# and a second gallery for the same name is given `-2` rather than refused, so
# two rows sharing a name is the designed behaviour. This mirror read them with
# `.first()` on an unordered query and wrote the industry onto whichever came
# back, leaving the other as it was.
#
# Production carried exactly that: `marco-island-rental` on "general" and
# `marco-island-rental-2` on "tourism", one client answering two ways depending
# on which row a reader landed on. Nothing reported it, and it does not settle
# itself -- the next write picks arbitrarily again.
#
# Asserted against the picker's real tables, because the defect is in which
# rows the query returns and a stub would have agreed with either behaviour.
from modules.image_picker import models as picker_models            # noqa: E402

picker_models.init_db()
_db = picker_models.session()
try:
    for _slug in ("marco-island-rental", "marco-island-rental-2"):
        _db.add(picker_models.PickerClient(
            name="Marco Island Rental", slug=_slug, kind="prospect",
            industry_key="general"))
    # ...and one that must not be touched: the mirror matches on the exact
    # name, which is the rule CLAUDE.md states for client matching.
    _db.add(picker_models.PickerClient(name="Marco Island Rentals",
                                       slug="marco-island-rentals",
                                       kind="prospect", industry_key="general"))
    _db.commit()
finally:
    _db.close()


def _keys(name):
    db = picker_models.session()
    try:
        return [r.industry_key for r in
                db.query(picker_models.PickerClient)
                  .filter(picker_models.PickerClient.name == name)
                  .order_by(picker_models.PickerClient.id).all()]
    finally:
        db.close()


check("two rows can share a name — the slug is what is unique",
      len(_keys("Marco Island Rental")), 2)

_changed = industry._mirror_picker("Marco Island Rental", "tourism")  # noqa: SLF001
check("both rows are written, so the client's industry reads the same "
      "whichever row a reader lands on",
      _keys("Marco Island Rental"), ["tourism", "tourism"])
check("and it says how many it changed, so an ambiguity can be recorded",
      _changed, 2)
check("a different client whose name merely starts the same is untouched",
      _keys("Marco Island Rentals"), ["general"])

# Running it again is not a second write: the rows already say tourism.
check("a second run changes nothing — the rows already say tourism",
      industry._mirror_picker("Marco Island Rental", "tourism") == 0)  # noqa: SLF001

# And the half-written state production was actually in converges, rather than
# moving the disagreement to the other row.
_db = picker_models.session()
try:
    _row = (_db.query(picker_models.PickerClient)
            .filter(picker_models.PickerClient.slug == "marco-island-rental").one())
    _row.industry_key = "general"
    _db.commit()
finally:
    _db.close()
check("the exact state production carried is the starting point",
      _keys("Marco Island Rental"), ["general", "tourism"])
check("...and one run settles it instead of picking a side",
      (industry._mirror_picker("Marco Island Rental", "tourism"),  # noqa: SLF001
       _keys("Marco Island Rental"))[1], ["tourism", "tourism"])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
