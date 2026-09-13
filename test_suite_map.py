"""Which Suite sub-account belongs to which client.

The mapping used to live on image_picker_clients.ghl_location_id, a column
that exists because somebody provisioned an upload gallery. With the app on
several hundred sub-accounts that couples two unrelated facts, so it has its
own store -- and the old column is still READ, because five rows carry one
and a migration to fix a coupling problem is a worse trade than a fallback.

What is asserted here is the set of ways this goes quietly wrong: a proposal
matched on a substring, two candidates picked between, a sub-account taken
from the client who already holds it, and a read that failed reported as a
book with nobody in it.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="suitemap-")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "suite-map-test")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hub import suite_map                                     # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(t):
    print(f"\n{t}\n{'-' * len(t)}")


# ------------------------------------------------------------- the store
section("A pairing is recorded, and a conflicting one is refused")

check("linking succeeds", suite_map.link("Icon Solar", "loc-icon", "todd")["ok"], True)
check("and it records who", suite_map.links()[0]["by"], "todd")

# The load-bearing refusal. Silently taking the newer answer is how one
# client's posts reach another client's page.
res = suite_map.link("Northgate Dental", "loc-icon", "todd")
check("a sub-account already held by another client is refused", res["ok"], False)
check("and the refusal names who holds it", "Icon Solar" in res.get("detail", ""), True)
check("the original pairing is untouched",
      suite_map.recorded_client("loc-icon")["client"], "Icon Solar")

check("re-linking the same client to the same account is fine",
      suite_map.link("Icon Solar", "loc-icon", "todd")["ok"], True)
check("unlinking works", suite_map.unlink("Icon Solar")["ok"], True)
check("and it is gone",
      suite_map.recorded_location("Icon Solar")["state"], suite_map.NOT_CONNECTED)


# ------------------------------------- a multi-location client holds several
section("A client may hold several locations, not just one")

# Healthy Pets: one Knack client record, several storefronts, several Suite
# sub-accounts -- what used to be refused as "ambiguous" for ever.
check("the first location a client is given", suite_map.link(
    "Healthy Pets", "loc-hp-carmel", "todd")["ok"], True)
check("becomes primary automatically",
      suite_map.client_locations("Healthy Pets")[0]["primary"], True)

check("a second location for the same client is accepted, not refused",
      suite_map.link("Healthy Pets", "loc-hp-fishers", "todd")["ok"], True)
locs = suite_map.client_locations("Healthy Pets")
check("both are on file", {l["location_id"] for l in locs},
      {"loc-hp-carmel", "loc-hp-fishers"})
check("primary is still the first one, unchanged by attaching a second",
      next(l for l in locs if l["primary"])["location_id"], "loc-hp-carmel")

found = suite_map.recorded_location("Healthy Pets")
check("a single-target reader gets the primary, never 'ambiguous'",
      found["state"], suite_map.CONNECTED)
check("and it is the primary one", found["location_id"], "loc-hp-carmel")
check("with the rest named for a caller that wants to show them",
      found["others"], ["loc-hp-fishers"])

check("changing primary is its own act, not a side effect of attaching",
      suite_map.set_primary("Healthy Pets", "loc-hp-fishers")["ok"], True)
check("and it takes", suite_map.recorded_location("Healthy Pets")["location_id"],
      "loc-hp-fishers")
check("setting a location that isn't recorded is refused",
      suite_map.set_primary("Healthy Pets", "loc-nope")["ok"], False)

check("a second client cannot claim a location the first already holds",
      suite_map.link("Some Other Clinic", "loc-hp-carmel", "todd")["ok"], False)

check("removing one location leaves the other",
      suite_map.unlink("Healthy Pets", "loc-hp-carmel")["ok"], True)
locs = suite_map.client_locations("Healthy Pets")
check("one remains", [l["location_id"] for l in locs], ["loc-hp-fishers"])
check("and it is primary, since somebody has to be",
      locs[0]["primary"], True)

check("unlinking with no location_id clears everything, the old single-account way",
      suite_map.unlink("Healthy Pets")["ok"], True)
check("nothing is left", suite_map.client_locations("Healthy Pets"), [])
check("a client nobody has ever recorded is not measured as connected",
      suite_map.recorded_location("Healthy Pets")["state"], suite_map.NOT_CONNECTED)


# --------------------------------------------------- one reader, two stores
section("suite_accounts is the one reader, and it consults both")

# The integration that makes this module more than a library: everything in
# the Hub that needs a sub-account -- the forms card, the Social Planner
# push, token_for() -- goes through suite_accounts.location_for(). If a
# pairing recorded here does not reach that function it has bought nothing.
import hub.suite_accounts as _sa                              # noqa: E402

suite_map.link("Icon Solar", "loc-icon", "todd")
got = _sa.location_for("Icon Solar")
check("a pairing recorded here reaches the Hub's one reader",
      got.get("location_id"), "loc-icon")
check("and the answer says which store it came from",
      got.get("source"), "suite_map")
check("the reverse direction too",
      _sa.client_for_location("loc-icon").get("client"), "Icon Solar")

# A client in neither store gets a "nothing recorded" answer rather than a
# wrong one. not_measured is allowed here because the picker table does not
# exist in this environment, and "we could not look" is a true answer -- the
# distinction the module is built around.
check("a client neither store knows is not connected",
      _sa.location_for("Nobody At All").get("state") in
      ("not_connected", "not_measured"), True)
suite_map.unlink("Icon Solar")


# ------------------------------------------------------------ the matcher
section("A proposal is exact, or it is not a proposal")

CLIENTS = [
    {"name": "Icon Solar", "url": "https://iconsolar.com"},
    {"name": "Monogram Homes", "url": "https://www.monogramhomes.net/"},
    {"name": "Riverside HVAC", "url": ""},
    {"name": "Riverside HVAC Supply", "url": ""},
    # Two client records on one domain -- the ambiguity that actually occurs,
    # and the one hub/client_key.py says must propose neither.
    {"name": "Cirilla's", "url": "https://sharedco.com"},
    {"name": "Cirilla's North", "url": "https://sharedco.com"},
    {"name": "Harbour Point Vets", "url": "https://harbourpointvets.com"},
]
LOCATIONS = [
    # Matches on domain, trailing slash and www and all.
    {"id": "loc-mono", "name": "Monogram Homes",
     "website": "https://www.monogramhomes.net/"},
    # No website: falls back to an exact name.
    {"id": "loc-harbour", "name": "Harbour Point Vets", "website": ""},
    # An exact name that only one client answers to. "Riverside HVAC Supply"
    # is a different normalised name, so this is not ambiguous -- it is the
    # substring rule not firing, which is the point.
    {"id": "loc-river", "name": "Riverside HVAC", "website": ""},
    # Two client records share this domain, so neither may be proposed.
    {"id": "loc-shared", "name": "Shared Co", "website": "https://sharedco.com"},
    # A substring of a real client. Must NOT propose Icon Solar.
    {"id": "loc-iconsupply", "name": "Icon Solar Supply Co", "website": ""},
    # Nothing at all.
    {"id": "loc-unknown", "name": "Someone Else Entirely", "website": ""},
]

import hub.clients_registry as _reg                           # noqa: E402
_reg.all_clients = lambda refresh=False: list(CLIENTS)        # type: ignore[assignment]
suite_map.fetch_locations = lambda: (list(LOCATIONS), "")     # type: ignore[assignment]

out = suite_map.proposals()
check("it measured", out["measured"], True)
by_loc = {p["location_id"]: p for p in out["proposals"]}

check("a domain match is proposed", by_loc.get("loc-mono", {}).get("client"),
      "Monogram Homes")
check("and says it matched on the domain",
      by_loc.get("loc-mono", {}).get("matched_on"), "domain")
check("an exact name with no website is proposed",
      by_loc.get("loc-harbour", {}).get("client"), "Harbour Point Vets")

# The rule that keeps one client's account off another's record.
check("a substring is never proposed", "loc-iconsupply" in by_loc, False)
check("and it lands in unmatched rather than vanishing",
      "loc-iconsupply" in {u["location_id"] for u in out["unmatched"]}, True)

amb = {a["location_id"]: a for a in out["ambiguous"]}
check("an exact name matching one client is proposed",
      by_loc.get("loc-river", {}).get("client"), "Riverside HVAC")
check("two clients on one domain propose neither", "loc-shared" in by_loc, False)
check("and both are named",
      amb.get("loc-shared", {}).get("candidates"),
      ["Cirilla's", "Cirilla's North"])

check("a sub-account nothing matches is unmatched",
      "loc-unknown" in {u["location_id"] for u in out["unmatched"]}, True)
check("every location is accounted for",
      len(out["proposals"]) + len(out["ambiguous"]) + len(out["unmatched"])
      + out["linked"], len(LOCATIONS))

# Nothing may have been written by looking.
check("proposing wrote nothing", suite_map.links(), [])


# ------------------------------------------------- what a failed read says
section("A read that failed is never a book with nobody in it")

suite_map.fetch_locations = lambda: ([], "Suite refused the request.")  # type: ignore[assignment]
out = suite_map.proposals()
check("an unreadable location list is not measured", out["measured"], False)
check("and it says why", "refused" in out["error"], True)
check("and proposes nothing", out["proposals"], [])

suite_map.fetch_locations = lambda: (list(LOCATIONS), "")     # type: ignore[assignment]
_reg.all_clients = lambda refresh=False: []                   # type: ignore[assignment]
out = suite_map.proposals()
check("an empty client list is not measured either", out["measured"], False)
check("because every sub-account would read as unmatched",
      "unmatched" in out["error"], True)
_reg.all_clients = lambda refresh=False: list(CLIENTS)        # type: ignore[assignment]


# ------------------------------------------------------------ bulk accept
section("A screenful reports every row, not one number")

res = suite_map.accept_many([
    {"client": "Monogram Homes", "location_id": "loc-mono"},
    {"client": "Harbour Point Vets", "location_id": "loc-harbour"},
    {"client": "Someone Else", "location_id": "loc-mono"},   # already taken
], by="todd")
check("the good rows land", res["linked"], 2)
check("the conflicting row is refused rather than dropped", res["refused"], 1)
check("and every row carries its own outcome", len(res["results"]), 3)
check("the refusal explains itself",
      "already recorded" in
      next(r for r in res["results"] if not r["ok"])["detail"], True)

# An accepted pairing leaves the proposal list.
out = suite_map.proposals()
check("accepted sub-accounts are counted as linked, not re-proposed",
      out["linked"], 2)
check("and are no longer proposed",
      "loc-mono" in {p["location_id"] for p in out["proposals"]}, False)


# ------------------------------------------------------- the review screen
section("The screen that records 282 of these, one screenful at a time")

# Booted late and deliberately: everything above drives the library, and what
# is asserted here is that a pairing recorded THROUGH THE ROUTE reaches the
# same store. A screen that writes somewhere else has bought nothing.
os.environ.setdefault("PANEL_PASSWORD", "suite-map-test-password")
from hub import auth                                          # noqa: E402
from wsgi import hub_app as APP                                # noqa: E402

anon = APP.test_client()
check("the page redirects a stranger to the login",
      anon.get("/tools/suite-match").status_code, 302)
check("the recorded list refuses a stranger",
      anon.get("/api/suite/map").status_code, 401)
for path in ("/api/suite/proposals", "/api/suite/link", "/api/suite/unlink",
             "/api/suite/primary"):
    check(f"{path} refuses a stranger's write",
          anon.post(path, json={}).status_code, 401)
check("and Client 360's read of it refuses a stranger too",
      anon.get("/api/client/suite-locations?name=Acme").status_code, 401)

signed = APP.test_client()
signed.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Tester"))
check("the page renders", signed.get("/tools/suite-match").status_code, 200)

# Finding is a POST, for the reason domain_purchase's refresh is: it walks
# every sub-account in the company against HighLevel's own rate limit, and a
# GET that does that is one a reload or a prefetch fires unasked.
check("finding sub-accounts is not a GET",
      signed.get("/api/suite/proposals").status_code, 405)

body = signed.post("/api/suite/proposals").get_json()
check("the route answers with the same reading the library gives",
      body["measured"], True)
check("and proposes the same rows",
      {p["location_id"] for p in body["proposals"]},
      {p["location_id"] for p in suite_map.proposals()["proposals"]})

before = len(suite_map.links())
one = signed.post("/api/suite/link",
                  json={"client": "Northgate Dental",
                        "location_id": "loc-north"}).get_json()
check("one pairing records over the route", one["ok"], True)
check("and it reaches the store the readers use",
      suite_map.recorded_client("loc-north")["client"], "Northgate Dental")

# The refusal has to survive the route as well: a rule the library keeps
# while the endpoint breaks it is not a rule.
clash = signed.post("/api/suite/link",
                    json={"client": "Somebody Else",
                          "location_id": "loc-north"}).get_json()
check("a sub-account already held is refused over the route too",
      clash["ok"], False)
check("and the refusal names who holds it",
      "Northgate Dental" in clash.get("detail", ""), True)

bulk = signed.post("/api/suite/link", json={"pairs": [
    {"client": "Cirilla's", "location_id": "loc-cir"},
    {"client": "Yet Another", "location_id": "loc-north"},   # already taken
]}).get_json()
check("a screenful reports what landed", bulk["linked"], 1)
check("and what was refused rather than one number", bulk["refused"], 1)
check("with a row per pair", len(bulk["results"]), 2)

listing = signed.get("/api/suite/map").get_json()
check("the recorded list counts what is there",
      listing["count"], before + 2)
check("and the count matches the rows it sent",
      len(listing["links"]), listing["count"])

check("unlinking answers", signed.post("/api/suite/unlink",
      json={"client": "Northgate Dental"}).get_json()["ok"], True)
check("and the pairing is gone",
      suite_map.recorded_client("loc-north")["state"],
      suite_map.NOT_CONNECTED)
check("unlinking a client with none says so, rather than reporting success",
      signed.post("/api/suite/unlink",
                  json={"client": "Nobody At All"}).get_json()["ok"], False)

# Client 360's Suite Account card, and its "make primary" control -- the
# fix for the disconnect where attaching there never reached this store.
signed.post("/api/suite/link", json={"client": "Healthy Pets",
                                     "location_id": "loc-hp-a"})
signed.post("/api/suite/link", json={"client": "Healthy Pets",
                                     "location_id": "loc-hp-b"})
c360 = signed.get("/api/client/suite-locations?name=Healthy Pets").get_json()
check("Client 360 reads both locations",
      {l["location_id"] for l in c360["locations"]},
      {"loc-hp-a", "loc-hp-b"})
check("and it is the same store the automated readers use",
      next(l["primary"] for l in c360["locations"]
          if l["location_id"] == "loc-hp-a"), True)

pri = signed.post("/api/suite/primary",
                  json={"client": "Healthy Pets", "location_id": "loc-hp-b"}).get_json()
check("the route sets which one is primary", pri["ok"], True)
check("and it reaches the reader every caller goes through",
      _sa.location_for("Healthy Pets")["location_id"], "loc-hp-b")


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
