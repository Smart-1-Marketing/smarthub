"""hub/schema_prefill.py -- prefill the Schema Builder from the client brief.

    python3 test_schema_prefill.py
"""
import os
import shutil
import sys
import tempfile
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1schemaprefill_test_")
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


from hub import schema_prefill, industry


# ---------------------------------------------------------------------------
section("Every canonical industry key has a SCHEMA_TYPES entry")
missing = [i["key"] for i in industry.INDUSTRIES if i["key"] not in schema_prefill.SCHEMA_TYPES]
check("no key is missing an @type", not missing, missing)
check('"general" maps to LocalBusiness', schema_prefill.SCHEMA_TYPES["general"] == "LocalBusiness")
check('"restaurant" maps to Restaurant', schema_prefill.SCHEMA_TYPES["restaurant"] == "Restaurant")


# ---------------------------------------------------------------------------
section("schema_type_for() refines by label within an industry")
check("legal + injury label -> Attorney",
      schema_prefill.schema_type_for("legal", "", "Personal injury law firm") == "Attorney")
check("legal + no injury -> LegalService",
      schema_prefill.schema_type_for("legal", "", "Bankruptcy attorney") == "LegalService")
check("healthcare + dentist label -> Dentist",
      schema_prefill.schema_type_for("healthcare", "", "Family Dentist") == "Dentist")
check("healthcare + no dentist -> MedicalBusiness",
      schema_prefill.schema_type_for("healthcare", "", "Urgent care clinic") == "MedicalBusiness")
check("automotive + repair label -> AutoRepair",
      schema_prefill.schema_type_for("automotive", "", "Joe's Auto Repair") == "AutoRepair")
check("legacy key resolves like its canonical one",
      schema_prefill.schema_type_for("boat") == schema_prefill.SCHEMA_TYPES["marine"])
check("unknown key falls back to LocalBusiness",
      schema_prefill.schema_type_for("not-a-real-key") == "LocalBusiness")


# ---------------------------------------------------------------------------
section("prefill() offers into empty fields, never overwrites a saved one")

FIXTURE_BRIEF = {
    "client": "Acme Plumbing", "domain": "acmeplumbing.com",
    "identity": {
        "site_name": {"value": "Acme Plumbing Co", "source": "scan"},
        "website": {"value": "acmeplumbing.com", "source": "scan"},
        "industry_key": {"value": "home_services", "source": "scan"},
        "industry_label": {"value": "Plumbing", "source": "scan"},
    },
    "location": {
        "street": {"value": "123 Main St", "source": "scan"},
        "city": {"value": "Indianapolis", "source": "scan"},
        "state": {"value": "IN", "source": "scan"},
        "zip": {"value": "46201", "source": "scan"},
        "place_id": {"value": "ChIJabc123", "source": "scan"},
    },
    "contact": {
        "phone": {"value": "(317) 555-0100", "source": "scan"},
        "email": {"value": "info@acmeplumbing.com", "source": "scan"},
    },
    "brand": {
        "logos": [{"url": "https://cdn.example/logo.png", "origin": "file"}],
    },
    "social": {"found": True, "platforms": [
        {"platform": "Facebook", "link": "https://facebook.com/acmeplumbing"},
    ]},
}


class _FakeSeoStore:
    def __init__(self, business_info):
        self._bi = business_info

    def __call__(self, client):
        return {"business_info": dict(self._bi)}


with mock.patch("hub.client_brief.build", return_value=FIXTURE_BRIEF), \
     mock.patch("hub.seo.load_store", side_effect=_FakeSeoStore({})):
    out = schema_prefill.prefill("Acme Plumbing", "acmeplumbing.com")

check("name offered", out["fields"].get("name", {}).get("value") == "Acme Plumbing Co")
check("phone offered", out["fields"].get("phone", {}).get("value") == "(317) 555-0100")
check("address offered", out["fields"].get("address", {}).get("value") == "123 Main St")
check("geo -- place id stored, no coordinates fetched",
      out["fields"].get("google_maps_place_id", {}).get("value") == "ChIJabc123")
check("no lat/lng key was invented",
      "lat" not in out["fields"] and "lng" not in out["fields"] and "latitude" not in out["fields"])
check("logo offered from the file origin",
      out["fields"].get("logo", {}).get("value") == "https://cdn.example/logo.png")
check("sameAs offered from real social links",
      out["fields"].get("social_profiles", {}).get("value") == ["https://facebook.com/acmeplumbing"])
check("schema type resolved from the canonical industry",
      out["schema_type"] == "HomeAndConstructionBusiness")
check("category offered as the resolved schema type",
      out["fields"].get("category", {}).get("value") == "HomeAndConstructionBusiness")

with mock.patch("hub.client_brief.build", return_value=FIXTURE_BRIEF), \
     mock.patch("hub.seo.load_store",
                side_effect=_FakeSeoStore({"phone": "(317) 555-9999"})):
    out2 = schema_prefill.prefill("Acme Plumbing", "acmeplumbing.com")

check("a saved phone is never overwritten", "phone" not in out2["fields"])
check("a saved value that disagrees is a note, not an apply",
      any(d["field"] == "phone" and d["saved"] == "(317) 555-9999"
          for d in out2["disagreements"]), out2["disagreements"])
check("other empty fields still offered alongside a disagreement",
      out2["fields"].get("email", {}).get("value") == "info@acmeplumbing.com")


# ---------------------------------------------------------------------------
section("prefill() never raises on a broken brief or a broken store")
with mock.patch("hub.client_brief.build", side_effect=RuntimeError("boom")):
    out3 = schema_prefill.prefill("Whatever", "whatever.com")
check("empty fields on a brief that raised", out3["fields"] == {})

with mock.patch("hub.client_brief.build", return_value={}), \
     mock.patch("hub.seo.load_store", side_effect=RuntimeError("nope")):
    out4 = schema_prefill.prefill("Whatever", "")
check("empty fields on a store that raised too", out4["fields"] == {})


# ---------------------------------------------------------------------------
section("build_localbusiness_jsonld() is a valid LocalBusiness node")
bi = {
    "name": "Acme Plumbing Co", "phone": "(317) 555-0100",
    "email": "info@acmeplumbing.com", "url": "https://acmeplumbing.com",
    "address": "123 Main St", "city": "Indianapolis", "state": "IN",
    "zip": "46201", "logo": "https://cdn.example/logo.png",
    "google_maps_place_id": "ChIJabc123",
    "social_profiles": ["https://facebook.com/acmeplumbing"],
}
node = schema_prefill.build_localbusiness_jsonld(bi, "home_services")
check('"@context" is schema.org', node.get("@context") == "https://schema.org")
check('"@type" is a real schema.org type', node.get("@type") == "HomeAndConstructionBusiness")
check("name present", node.get("name") == "Acme Plumbing Co")
check("telephone present (not \"phone\")", node.get("telephone") == "(317) 555-0100")
check("address is a PostalAddress node",
      isinstance(node.get("address"), dict) and node["address"].get("@type") == "PostalAddress")
check("address carries street/city/state/zip",
      node["address"].get("streetAddress") == "123 Main St"
      and node["address"].get("addressLocality") == "Indianapolis"
      and node["address"].get("addressRegion") == "IN"
      and node["address"].get("postalCode") == "46201")
check("hasMap built from the place id, no coordinates",
      "ChIJabc123" in node.get("hasMap", ""))
check("sameAs carries the social links", node.get("sameAs") == bi["social_profiles"])
check("image falls back to the logo", node.get("image") == bi["logo"])

# Never overwritten -- a schema built from an empty business_info still
# validates minimally (just @context/@type), never raises.
empty_node = schema_prefill.build_localbusiness_jsonld({}, "general")
check("an empty business_info still produces a minimal valid node",
      empty_node.get("@context") == "https://schema.org"
      and empty_node.get("@type") == "LocalBusiness")


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
