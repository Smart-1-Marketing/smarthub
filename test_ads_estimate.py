"""Smart 1 Ads — the estimate a client reads, and everything behind it.

    python3 test_ads_estimate.py

House style: no pytest, no new dependencies, a temporary data directory and a
throwaway SQLite database, so it never touches /var/data or the real one. The
composed app is booted through ``wsgi.application`` rather than the module's
own, because the two failures this file exists to catch — the login redirect
and the injected staff chrome — only exist once everything is stacked together.

## Why this file exists

The estimate is the second document in this Hub read by somebody who is not
staff, and the first one they make a spending decision from. Six kinds of
failure sit on it, and every one of them looks fine from inside the Hub:

  1. **It must be reachable without a login.** A client has no Hub account. A
     login redirect here is a lost deal and it is invisible to staff, who are
     always signed in.

  2. **It must carry no staff chrome.** The sidebar, the help layer and the
     feedback tab do not belong on a document sent to a prospect. Checked on
     the response the browser actually receives, because HubBar and the hub's
     own after_request both rewrite HTML they did not write.

  3. **A token must not be guessable, and an unknown one must not be
     distinguishable.** Revoked, deleted and never-existed all answer the same
     404 page: a client-facing URL that says "this one expired" tells somebody
     probing which tokens are real.

  4. **The estimate a client reads and the one the rep approved must be the
     same document.** They are one template, included twice; this asserts the
     sections, the numbers and the caveats survive both renderings.

  5. **An avg CPC is an industry estimate.** It appears on a page somebody
     spends money from, and the number alone is a claim about their account
     that nobody has measured. Every screen showing one must carry the caveat.

  6. **An edit must invalidate an approval.** Approving is a statement about a
     specific document. A tick that survived a budget change would refer to
     something nobody signed off, and the client link renders whatever is
     current.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1adsest_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "ads-estimate-test-secret"
os.environ["PANEL_PASSWORD"] = "ads-estimate-test-password"
os.environ.pop("CLOUDINARY_URL", None)
os.environ.pop("OPENAI_API_KEY", None)          # nothing here may call a model

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def truthy(label, got):
    check(label, bool(got), True)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from werkzeug.test import Client                                   # noqa: E402

from modules.ads_builder import landing_page, spec, store          # noqa: E402
from wsgi import application                                       # noqa: E402

client = Client(application)
MOUNT = "/tools/ads"

CAMPAIGN = {
    "businessName": "Northside Roofing Co",
    "websiteUrl": "https://northsideroofing.example.com/roof-repair",
    "monthlyBudget": 6500,
    "sector": "Home Services / Trades",
    "sectorKey": "homeservices",
    "createdBy": "todd@smart1marketing.com",
    "strategySummary": "Two tightly themed groups split emergency storm repair from planned "
                       "replacement.",
    "costEstimation": {"avgCPC": 11.4, "estimatedMonthlyClicks": 570,
                       "estimatedConversions": 46, "estimatedCPA": 141,
                       "budgetViability": {"status": "HEALTHY", "advice": "Enough headroom."}},
    "intake": spec.normalise_intake({
        "audienceType": "Both",
        "seasonal": True, "seasonalNotes": "March to June, and after the first freeze",
        "locallyOwned": True,
        "productOrService": "Residential roof repair and full replacement",
        "competitors": "Apex Roofing, Gutter Bros",
        "promotion": "Free storm inspection through April",
        "conversionActions": ["calls", "form_submissions", "appointment_bookings"],
        "doNotTarget": "commercial and industrial roofing",
        "phone": "(317) 555-0142",
    }),
    "targetAreas": [
        {"name": "Carmel showroom", "type": "City/ZIP + Radius", "origin": "Carmel, IN",
         "radius": 10, "zips": "46032, 46033"},
        {"name": "Fishers", "type": "City/ZIP + Radius", "origin": "Fishers, IN", "radius": 15},
    ],
    "adGroups": [
        {"name": "Emergency Roof Repair", "theme": "Storm damage", "avgCPC": 13.2,
         "keywords": ["[emergency roof repair]", '"roof leak repair"', "urgent roofer"],
         "ads": {"headlines": ["24/7 Emergency Roofers", "Roof Leak? We Come Today",
                               "Free Storm Damage Check"],
                 "descriptions": ["Out the same day.", "Clear pricing, real warranty."]}},
    ],
    "adAssets": {
        "sitelinks": [{"title": "Free Roof Inspection", "desc1": "Within 24 hours",
                       "desc2": "No obligation", "url": "https://northsideroofing.example.com/inspect"}],
        "callouts": ["Licensed & Insured", "10 Year Warranty"],
        "structuredSnippets": {"header": "Services", "values": ["Repair", "Replacement"]},
    },
    "negativeKeywordVault": {"jobsCareers": ["roofing jobs", "roofer salary"],
                             "freeCheap": ["free roof"]},
    "budgetTiers": {
        "tiers": [
            {"key": "good", "label": "Good", "monthly": 3000, "estimatedClicks": 146,
             "buys": "One tight ad group.", "givesUp": "No testing room.",
             "recommended": False, "belowFloor": False, "blurb": ""},
            {"key": "better", "label": "Better", "monthly": 6500, "estimatedClicks": 317,
             "buys": "The full keyword set.", "givesUp": "", "recommended": True,
             "belowFloor": False, "blurb": ""},
            {"key": "best", "label": "Best", "monthly": 12000, "estimatedClicks": 585,
             "buys": "Full coverage.", "givesUp": "", "recommended": False,
             "belowFloor": False, "blurb": ""},
        ],
        "rationale": "Sized off the sector CPC range.",
    },
    "budgetSource": {"stated": True, "tier": "", "note": ""},
    "editLog": [],
}


def sign_in():
    """A staff session, the way a person gets one."""
    staff = Client(application)
    staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                               "email": "todd@smart1marketing.com"})
    return staff


staff = sign_in()
proposal = store.create_proposal(client_name="Northside Roofing Co",
                                 campaign=dict(CAMPAIGN),
                                 created_by="todd@smart1marketing.com")
PID = proposal["id"]


# ===========================================================================
section("The intake is captured, and unanswered is not the same as no")

intake = CAMPAIGN["intake"]
check("B2B/B2C/Both is kept as given", intake["audienceType"], "Both")
check("a multi-select goal list survives", intake["conversionActions"],
      ["calls", "form_submissions", "appointment_bookings"])
check("an unanswered yes/no stays unanswered rather than becoming False",
      spec.normalise_intake({})["seasonal"], None)
check("...and an explicit no is False, not empty",
      spec.normalise_intake({"seasonal": False})["seasonal"], False)
check("a bogus audience value is dropped rather than printed to a client",
      spec.normalise_intake({"audienceType": "B2Whatever"})["audienceType"], "")
check("an unknown conversion action is dropped",
      spec.normalise_intake({"conversionActions": ["calls", "telepathy"]})["conversionActions"],
      ["calls"])
check("the optional phone is kept", intake["phone"], "(317) 555-0142")

rows = {r["key"]: r for r in spec.sections(CAMPAIGN)}
truthy("the estimate prints the audience", "audience" in rows)
truthy("...the goals", "goals" in rows)
truthy("...the promotion", "promotion" in rows)
truthy("...the exclusions", "exclusions" in rows)
truthy("...and the phone", "phone" in rows)
check("an unanswered question is left off rather than printed as a blank row",
      "local" in {r["key"] for r in spec.sections({"intake": spec.normalise_intake({})})},
      False)

prompt = spec.for_prompt(CAMPAIGN)
truthy("the do-not-target list reaches the model as an instruction",
       "DO NOT TARGET" in prompt and "commercial and industrial roofing" in prompt)
truthy("...and says to keep it out of the POSITIVE keywords too, not only the vault",
       "positive keywords" in prompt)
truthy("seasonality reaches the model with what to do about it",
       "SEASONAL" in prompt and "concentrated" in prompt)
truthy("each conversion action carries its consequence, not just its name",
       "Call extensions" in prompt)


# ===========================================================================
section("Every average CPC is labelled an industry estimate")

check("the caveat is one shared string", spec.CPC_NOTE, "industry estimate")
for path in (f"{MOUNT}/proposal/{PID}", f"{MOUNT}/proposal/{PID}/client"):
    body = staff.get(path).get_data(as_text=True)
    truthy(f"{path} shows an avg CPC",
           "avg cpc" in body.lower() or "cost per click" in body.lower())
    truthy(f"{path} carries the caveat beside it", spec.CPC_NOTE in body)
truthy("the long form is on the client document",
       spec.CPC_NOTE_LONG[:40] in staff.get(f"{MOUNT}/proposal/{PID}/client").get_data(as_text=True))

# The budget analyser publishes the caveat with the numbers, so a screen cannot
# render the CPC range without having been handed the words for it.
budget = staff.get(f"{MOUNT}/api/budget-check?budget=3000&sector=homeservices").get_json()
check("the budget check carries the caveat with its CPCs", budget["cpc_note"], spec.CPC_NOTE)
truthy("...and the long form too", budget["cpc_note_long"])
truthy("the generator's budget panel renders it",
       "cpc_note_long" in staff.get(f"{MOUNT}/").get_data(as_text=True))


# ===========================================================================
section("A client link cannot be made from an unapproved estimate")

r = staff.post(f"{MOUNT}/api/proposals/{PID}/share",
               json={}, headers={"Content-Type": "application/json"})
check("sharing is refused before approval", r.status_code, 400)
check("...and says why in a code a page can branch on",
      r.get_json()["code"], "NOT_APPROVED")

r = staff.post(f"{MOUNT}/api/proposals/{PID}/estimate/approve", json={})
check("approving a freshly generated estimate needs no second press",
      r.get_json()["approved"], True)

r = staff.post(f"{MOUNT}/api/proposals/{PID}/share", json={})
check("...and then the link can be made", r.status_code, 200)
share = r.get_json()["share"]
TOKEN = share["token"]
truthy("the token is long enough not to be guessed", len(TOKEN) >= 24)
check("a new link starts with no answer on it", share["outcome"], "")


# ===========================================================================
section("A client with no Hub login can read it")

anon = Client(application)
r = anon.get(f"{MOUNT}/estimate/{TOKEN}")
check("the estimate is served, not redirected to sign in", r.status_code, 200)
body = r.get_data(as_text=True)

check("no sidebar reaches the client", "s1hub-sb" in body, False)
check("no help layer reaches the client", "hub-help.js" in body, False)
check("no feedback tab reaches the client", "hub-feedback" in body, False)
check("no module tab bar reaches the client", "Live campaigns" in body, False)
check("no internal version tag reaches the client", "vtag" in body, False)
check("it asks not to be indexed", "noindex" in body, True)


# ===========================================================================
section("The client reads the same document the rep approved")

internal = staff.get(f"{MOUNT}/proposal/{PID}/client").get_data(as_text=True)
for needle, what in (
    ("Northside Roofing Co", "the business name"),
    ("Free storm inspection through April", "the promotion"),
    ("commercial and industrial roofing", "the exclusions"),
    ("Carmel showroom", "the first target area"),
    ("Fishers", "the second target area"),
    ("Free Roof Inspection", "the sitelink"),
    ("https://northsideroofing.example.com/inspect", "the sitelink's URL"),
    ("Licensed &amp; Insured", "the callouts"),
    ("emergency roof repair", "the keywords"),
):
    truthy(f"the client link shows {what}", needle in body)
    truthy(f"...and so does the internal preview", needle in internal)

check("match types are hidden from the client", "[emergency roof repair]" in body, False)
check("...and the internal preview hides them too, being the same document",
      "[emergency roof repair]" in internal, False)

truthy("several target areas are each named rather than merged into one line",
       "Carmel showroom" in body and "Fishers" in body)
truthy("reach is labelled an estimate", "without deducting overlap" in body)
truthy("the budget tiers are offered", "Good" in body and "Better" in body and "Best" in body)
truthy("the recommended tier is marked", "Recommended" in body)
truthy("the person who built it is named on the document",
       "todd@smart1marketing.com" in body)


# ===========================================================================
section("Change requests need a name and an email, and are kept")

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/change",
              json={"section": "Budget", "text": "Can we start at $4,000?"})
check("a change with no name is refused", r.status_code, 400)

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/change",
              json={"section": "Budget", "text": "", "name": "Dana", "email": "d@x.com"})
check("an empty change is refused", r.status_code, 400)

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/change",
              json={"section": "Budget", "text": "Can we start at $4,000?",
                    "name": "Dana Whitfield", "email": "dana@northsideroofing.example.com"})
check("a complete change request is accepted", r.status_code, 200)
changes = r.get_json()["changes"]
check("it is kept against the estimate", len(changes), 1)
check("...with the section it was asked about", changes[0]["section"], "Budget")
check("...and who asked", changes[0]["name"], "Dana Whitfield")

anon.post(f"{MOUNT}/estimate/{TOKEN}/change",
          json={"section": "Campaign details", "text": "Drop the Fishers area.",
                "name": "Sam Reed", "email": "sam@northsideroofing.example.com"})
check("a second person's request is kept beside the first, not over it",
      len(store.get_share(TOKEN)["changes"]), 2)
check("...and each keeps its own author",
      {c["name"] for c in store.get_share(TOKEN)["changes"]},
      {"Dana Whitfield", "Sam Reed"})


# ===========================================================================
section("The three answers, and the colour each comes back as")

check("there are exactly three", len(spec.OUTCOMES), 3)
check("approve is green", spec.outcome_colour("approved"), "green")
check("approve-with-changes is yellow", spec.outcome_colour("approved_with_changes"), "yellow")
check("discuss is red", spec.outcome_colour("discuss"), "red")
check("no answer yet is its own colour, not a fourth kind of bad",
      spec.outcome_colour(""), "grey")

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/respond",
              json={"outcome": "sounds-good", "name": "Dana", "email": "d@x.com"})
check("an outcome outside the three is refused", r.status_code, 400)

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/respond",
              json={"outcome": "approved_with_changes", "name": "Dana Whitfield",
                    "email": "dana@northsideroofing.example.com",
                    "note": "Happy, with the budget change above."})
check("a valid answer is accepted", r.status_code, 200)
check("...and comes back with its colour", r.get_json()["colour"], "yellow")

state = store.review_state(PID)
check("the approval hub sees the answer", state["outcome"], "approved_with_changes")
check("...as yellow", state["colour"], "yellow")
check("...and counts the change requests", state["changes"], 2)
check("...and knows the client opened it", state["opened"], True)

hub = staff.get(f"{MOUNT}/approvals").get_data(as_text=True)
truthy("the approval hub renders the bubble", "bub-yellow" in hub)


# ===========================================================================
section("The public page is public, and nothing else is")

# A token reaches its own proposal and no other. It is the only credential on
# this page, so the blast radius of a leaked one has to be exactly one estimate.
other = store.create_proposal(client_name="Someone Else",
                              campaign={"businessName": "Someone Else", "monthlyBudget": 500},
                              created_by="t")
check("a token addresses one proposal only",
      store.get_share(TOKEN)["proposal_id"], PID)

anon2 = Client(application)
for path, verb in ((f"{MOUNT}/api/proposals/{PID}/edit", "edit"),
                   (f"{MOUNT}/api/proposals/{PID}/share", "share"),
                   (f"{MOUNT}/api/proposals/{PID}/estimate/approve", "approve")):
    r = anon2.post(path, json={"monthlyBudget": 999999})
    check(f"a client cannot reach the staff {verb} endpoint", r.status_code, 401)
check("...and nothing they sent was applied",
      store.get_proposal(PID)["campaign"]["monthlyBudget"], 6500)

r = anon2.get(f"{MOUNT}/proposal/{PID}")
check("nor the internal proposal page", r.status_code in (301, 302, 401), True)

# Everything a client types comes back onto a page other people read.
anon.post(f"{MOUNT}/estimate/{TOKEN}/change",
          json={"section": "<img src=x onerror=alert(1)>",
                "text": "<script>alert('x')</script>",
                "name": "<b>Dana</b>", "email": "dana@x.example.com"})
rendered = anon.get(f"{MOUNT}/estimate/{TOKEN}").get_data(as_text=True)
check("a script tag in a change request is escaped, not rendered",
      "<script>alert('x')</script>" in rendered, False)
truthy("...and appears as text", "&lt;script&gt;" in rendered)
check("an event handler in a section name is escaped too",
      "<img src=x onerror" in rendered, False)

anon.post(f"{MOUNT}/estimate/{TOKEN}/change",
          json={"text": "x" * 100000, "name": "n" * 5000, "email": "d@x.example.com"})
last = store.get_share(TOKEN)["changes"][-1]
check("a change request is capped rather than stored whole", len(last["text"]), 4000)
check("...and so is the name", len(last["name"]), 200)


# ===========================================================================
section("A revoked or unknown link is a dead end, and says nothing else")

gone = anon.get(f"{MOUNT}/estimate/not-a-real-token")
check("an unknown token 404s", gone.status_code, 404)
unknown_body = gone.get_data(as_text=True)

store.revoke_share(TOKEN)
revoked = anon.get(f"{MOUNT}/estimate/{TOKEN}")
check("a revoked token 404s too", revoked.status_code, 404)
check("...with the identical page, so probing cannot tell them apart",
      revoked.get_data(as_text=True), unknown_body)

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/respond",
              json={"outcome": "approved", "name": "Dana", "email": "d@x.com"})
check("and a revoked link cannot still be answered", r.status_code, 404)

check("review_state falls back to the last live link",
      store.review_state(PID)["sent"], False)


# ===========================================================================
section("Editing invalidates the approval, and material edits need a re-check")

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit", json={"monthlyBudget": 2000})
check("the budget change is applied", r.status_code, 200)
truthy("...and is described in words", any("2,000" in c for c in r.get_json()["changed"]))
check("...and it now needs re-checking", r.get_json()["needs_recheck"], True)

after = store.get_proposal(PID)["campaign"]
check("the approval was cleared by the edit", (after.get("estimate") or {}).get("approved_at"), "")
check("...and says so rather than just vanishing",
      (after.get("estimate") or {}).get("superseded"), True)
check("the viability line was recomputed against the new budget, not left stale",
      after["costEstimation"]["budgetViability"]["status"] != "HEALTHY", True)

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit", json={"monthlyBudget": 0})
check("a zero budget is refused rather than quoted", r.status_code, 400)

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit", json={"monthlyBudget": "lots"})
check("a non-numeric budget is refused", r.status_code, 400)

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit",
               json={"intake": {"phone": "(317) 555-9999"}})
check("editing only the phone number is not material",
      r.get_json()["needs_recheck"], True)   # the budget edit above is still pending
pending = [e for e in store.get_proposal(PID)["campaign"]["editLog"]
           if e["what"].startswith("Campaign details")]
check("...the phone edit itself is marked immaterial", pending[-1]["material"], False)

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit",
               json={"intake": {"doNotTarget": "commercial, industrial and municipal"}})
material = [e for e in store.get_proposal(PID)["campaign"]["editLog"]
            if "doNotTarget" in e["what"]]
check("changing what must not be targeted IS material", material[-1]["material"], True)


# ===========================================================================
section("Keywords and negatives can be removed before a client sees them")

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit", json={
    "removeKeywords": [{"group": "Emergency Roof Repair", "keyword": "urgent roofer"}],
    "removeNegatives": [{"bucket": "freeCheap", "term": "free roof"}],
})
check("the removals are applied", r.status_code, 200)
campaign = store.get_proposal(PID)["campaign"]
check("the keyword is gone", "urgent roofer" in campaign["adGroups"][0]["keywords"], False)
check("...and the ones beside it are not",
      len(campaign["adGroups"][0]["keywords"]), 2)
check("the negative is gone", campaign["negativeKeywordVault"]["freeCheap"], [])
check("...and the other bucket is untouched",
      len(campaign["negativeKeywordVault"]["jobsCareers"]), 2)
check("removing a negative is always material — it reopens spend",
      [e for e in campaign["editLog"] if "negative" in e["what"]][-1]["material"], True)

r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit",
               json={"removeKeywords": [{"group": "Nope", "keyword": "nothing"}]})
check("a removal that matches nothing changes nothing", r.get_json()["changed"], [])


# ===========================================================================
section("A deployed campaign is no longer editable here")

store.mark_deployed(PID, {"campaign_name": "x", "customer_id": "1", "deployed_at": "",
                          "deployed_by": "t"})
r = staff.post(f"{MOUNT}/api/proposals/{PID}/edit", json={"monthlyBudget": 9000})
check("editing a deployed campaign is refused", r.status_code, 400)
check("...by name", r.get_json()["code"], "ALREADY_DEPLOYED")


# ===========================================================================
section("Conversion points are read off the page, never described")

HTML = """<html><head><title>Roof repair</title><meta name="viewport" content="width=device-width">
</head><body><h1>Emergency roof repair</h1>
<a href="tel:3175550142">Call now</a>
<form action="/lead" method="post"><input type="text" required><input type="email"></form>
<script src="https://embed.tawk.to/abc"></script>
<a href="https://calendly.com/northside">Book</a></body></html>"""

observed = landing_page.observe("https://x.example.com",
                                {"ok": True, "url": "https://x.example.com",
                                 "status": 200, "html": HTML})
kinds = {p["kind"] for p in observed["conversion_points"]}
check("a click-to-call link is found", "calls" in kinds, True)
check("a form is found", "form_submissions" in kinds, True)
check("a chat widget is found by its own signature", "chat_conversations" in kinds, True)
check("a booking tool is found", "appointment_bookings" in kinds, True)
check("the mobile viewport is reported as declared", observed["mobile_viewport"], True)
truthy("every point carries the evidence, not just a claim",
       all(p["evidence"] for p in observed["conversion_points"]))
check("the phone number found IS the evidence",
      [p["evidence"] for p in observed["conversion_points"] if p["kind"] == "calls"],
      ["3175550142"])

missing = landing_page.missing_for(observed, ["calls", "purchases", "directions"])
check("what the client wants and the page cannot do is named",
      {m["kind"] for m in missing}, {"purchases", "directions"})

unread = landing_page.observe("https://x.example.com",
                              {"ok": False, "status": 503, "error": "boom"})
check("a page that could not be read is not measured", unread["measured"], False)
check("...and reports no conversion points rather than zero of them",
      unread["conversion_points"], [])
check("...and the mobile check is None, not False",
      unread["mobile_viewport"], None)
check("...and nothing is claimed to be missing from a page nobody read",
      landing_page.missing_for(unread, ["calls"]), [])
truthy("...and it says NOT MEASURED in as many words", "NOT MEASURED" in unread["note"])

check("a bare word 'chat' is not mistaken for a chat widget",
      "chat_conversations" in {p["kind"] for p in landing_page.observe(
          "https://x", {"ok": True, "html": "<html><body>Chat with us today</body></html>",
                        "status": 200, "url": "https://x"})["conversion_points"]},
      False)


# ===========================================================================
section("Target areas are the Proposal Builder's, not a second copy")

from hub import target_areas                                       # noqa: E402

r = staff.post(f"{MOUNT}/api/areas/preview", json={"areas": CAMPAIGN["targetAreas"]})
data = r.get_json()
check("the preview answers", r.status_code, 200)
check("both areas come back", len(data["areas"]), 2)
check("...labelled the way hub/target_areas labels them",
      data["areas"][0]["label"], target_areas.label(CAMPAIGN["targetAreas"][0]))
check("...and sized the way it sizes them",
      data["areas"][0]["population"],
      target_areas.estimated_population(target_areas.normalize_area(CAMPAIGN["targetAreas"][0])))
truthy("the summary names more than one area", "Fishers" in data["summary"] or
       "+1 more" in data["summary"])

blank = staff.post(f"{MOUNT}/api/areas/preview",
                   json={"areas": [{"type": "Other", "other": "wherever"}]}).get_json()
check("an area that cannot be sized reports None, never 0",
      blank["areas"][0]["population"], None)
truthy("...and the note says that is not measured rather than nothing",
       "not measured" in blank["note"])

gen = staff.get(f"{MOUNT}/").get_data(as_text=True)
truthy("the generator offers more than one area", "Add another area" in gen)
truthy("the generator asks the new questions",
       all(q in gen for q in ("Are they targeting", "seasonal", "locally owned",
                              "competitors", "promotion", "not</em> be targeting")))
truthy("...and the eight conversion goals",
       all(label in gen for _, label, _ in spec.CONVERSION_ACTIONS))
truthy("...and offers the no-budget path", "don't know a budget" in gen)
truthy("...and an optional phone number", 'id="phone"' in gen)


# ===========================================================================
section("The logo is looked up, never guessed at")

from modules.ads_builder import logo as logo_lookup                # noqa: E402

check("a name with no domain yields no domain to look up",
      logo_lookup.domain_of("Northside Roofing"), "")
check("a URL yields its bare domain",
      logo_lookup.domain_of("https://www.northsideroofing.com/roof"), "northsideroofing.com")

no_domain = logo_lookup.from_brandfetch("", "Northside Roofing")
check("Brandfetch is not called without a domain", no_domain["found"], False)
truthy("...and says why", "no domain" in no_domain["note"].lower())

resolved = logo_lookup.resolve("Nobody At All", "https://nothing.example.com")
check("with nothing on file, resolve does not invent a URL", resolved.get("found"), False)
check("...and does not run a billed lookup on its own", "Brandfetch" in str(resolved.get("tried", [])), False)
truthy("...and says what to do next", resolved.get("next"))


# ===========================================================================
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed\n")
sys.exit(1 if _failed else 0)
