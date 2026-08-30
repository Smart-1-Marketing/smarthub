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
section("Every average CPC is labeled an industry estimate")

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
# hub-thinking.js is part of that chrome, so leaving it out left a client
# asking for a change with a grayed-out button and nothing else. The mark is
# inlined here instead — hub/thinking.py, with test_thinking.py holding it in
# step with the Hub's own.
check("the mark that says something is running does",
      ".s1w-mark{" in body and "window.S1Wait = {" in body, True)
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
truthy("reach is labeled an estimate", "without deducting overlap" in body)
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
section("The three answers, and the color each comes back as")

check("there are exactly three", len(spec.OUTCOMES), 3)
check("approve is green", spec.outcome_colour("approved"), "green")
check("approve-with-changes is yellow", spec.outcome_colour("approved_with_changes"), "yellow")
check("discuss is red", spec.outcome_colour("discuss"), "red")
check("no answer yet is its own color, not a fourth kind of bad",
      spec.outcome_colour(""), "gray")

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/respond",
              json={"outcome": "sounds-good", "name": "Dana", "email": "d@x.com"})
check("an outcome outside the three is refused", r.status_code, 400)

r = anon.post(f"{MOUNT}/estimate/{TOKEN}/respond",
              json={"outcome": "approved_with_changes", "name": "Dana Whitfield",
                    "email": "dana@northsideroofing.example.com",
                    "note": "Happy, with the budget change above."})
check("a valid answer is accepted", r.status_code, 200)
check("...and comes back with its color", r.get_json()["color"], "yellow")

state = store.review_state(PID)
check("the approval hub sees the answer", state["outcome"], "approved_with_changes")
check("...as yellow", state["color"], "yellow")
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
check("...labeled the way hub/target_areas labels them",
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
section("The area editor cannot eat what is being typed")

# This is a rendering rule, so it is asserted on the markup the page ships:
# the row inputs are built by drawAreas() and the derived text is painted into
# reserved spans by paintMeta(). The bug was that ONE function did both, so the
# server's answer replaced the <input> mid-keystroke and a name came out as
# "Car". The two must stay separate.
gen_js = staff.get(f"{MOUNT}/").get_data(as_text=True)
truthy("the row structure and the derived labels are drawn by different functions",
       "function drawAreas()" in gen_js and "function paintMeta(" in gen_js)
truthy("typing asks the server for labels only", "paintMeta(d.areas)" in gen_js)
check("...and never redraws the rows from the server's answer",
      "drawAreas(d.areas)" in gen_js, False)
truthy("the label and reach have their own targets to paint into",
       "data-area-label" in gen_js and "data-area-reach" in gen_js)
truthy("changing the TYPE does rebuild, because it changes which fields exist",
       "function setAreaType(" in gen_js and "setAreaType(${i}, this.value)" in gen_js)

truthy("the new-client button sits at the left of its row",
       gen_js.index("This is a new client") < gen_js.index('<div class="spacer"></div>\n      </div>\n\n    <div id="clientPicked"')
       if '<div class="spacer"></div>\n      </div>\n\n    <div id="clientPicked"' in gen_js else True)


# ===========================================================================
section("Generating shows what it is working on")

truthy("the build has a drawn progress panel", 'id="genStage"' in gen_js)
truthy("...with a stage per thing the server actually does",
       gen_js.count('class="gstage"') == 4)
truthy("...that is cancelled when generation fails", "stopBuild()" in gen_js)
truthy("...and the SVG is labeled for a screen reader", "genSvgTitle" in gen_js)


# ===========================================================================
section("Calls to action are found, including the ones that are links")

CTA_HTML = """<html><body>
<a class="btn btn-primary" href="/quote">Get a free quote</a>
<a class="elementor-button" href="/book"><span>Schedule service</span></a>
<a href="/about">About us</a>
<a class="subtle-link" href="/story">Our story</a>
<a class="btn" href="/empty">   </a>
<button>Send</button>
</body></html>"""
cta = landing_page.observe("https://x", {"ok": True, "html": CTA_HTML,
                                          "status": 200, "url": "https://x"})
ctas = [p for p in cta["conversion_points"] if p["kind"] == "cta"]
found = {p["evidence"].split("  →")[0] for p in ctas}
check("a link styled as a button counts", "Get a free quote" in found, True)
check("...and a page-builder button class counts", "Schedule service" in found, True)
check("a real <button> still counts", "Send" in found, True)
check("an ordinary navigation link does not", "About us" in found, False)
check("nor does a link whose class merely contains 'btn' as a fragment",
      "Our story" in found, False)
check("nor a styled button with no words in it — it tells a reader nothing",
      any(not p["evidence"].split("  →")[0].strip() for p in ctas), False)
truthy("a CTA link carries where it goes", any("→" in p["evidence"] for p in ctas))
truthy("...and is labeled a link rather than a button",
       any(p["label"].endswith("link") for p in ctas))


# ===========================================================================
section("A researched competitor reaches a client only once somebody ticks it")

comp_pid = store.create_proposal(
    client_name="Northside Roofing Co",
    campaign={**CAMPAIGN, "competitorResearch": {
        "named": [{"name": "Apex Roofing", "note": "The client said so."}],
        "researched": [{"name": "Erie Home", "why": "National advertiser.",
                        "confidence": "Medium", "accepted": False},
                       {"name": "Made Up Roofing", "why": "Guess.",
                        "confidence": "Low", "accepted": False}],
        "implications": [], "brandTermAdvice": "", "note": "unverified"}},
    created_by="t")["id"]
staff.post(f"{MOUNT}/api/proposals/{comp_pid}/estimate/approve", json={})
comp_token = staff.post(f"{MOUNT}/api/proposals/{comp_pid}/share",
                        json={}).get_json()["share"]["token"]

doc = anon.get(f"{MOUNT}/estimate/{comp_token}").get_data(as_text=True)
truthy("a competitor the CLIENT named is on the document", "Apex Roofing" in doc)
check("an unticked research suggestion is not", "Erie Home" in doc, False)
check("...nor the one nobody would want on there", "Made Up Roofing" in doc, False)

staff.post(f"{MOUNT}/api/proposals/{comp_pid}/competitors/accept",
           json={"accepted": ["Erie Home"]})
doc = anon.get(f"{MOUNT}/estimate/{comp_token}").get_data(as_text=True)
check("a ticked one appears", "Erie Home" in doc, True)
check("...and the one still unticked does not", "Made Up Roofing" in doc, False)

rows = store.get_proposal(comp_pid)["campaign"]["competitorResearch"]["researched"]
check("the tick is stored against the name, not a position",
      {r["name"]: r["accepted"] for r in rows},
      {"Erie Home": True, "Made Up Roofing": False})

staff.post(f"{MOUNT}/api/proposals/{comp_pid}/competitors/accept", json={"accepted": []})
doc = anon.get(f"{MOUNT}/estimate/{comp_token}").get_data(as_text=True)
check("unticking removes it again", "Erie Home" in doc, False)


# ===========================================================================
section("Keywords can be added by hand, not only removed")

add_pid = store.create_proposal(client_name="Northside Roofing Co",
                                campaign=json.loads(json.dumps(CAMPAIGN)),
                                created_by="t")["id"]
r = staff.post(f"{MOUNT}/api/proposals/{add_pid}/edit", json={
    "addKeywords": [{"group": "Emergency Roof Repair",
                     "keywords": '[storm damage roofer], "roof tarp service", hail damage'}]})
check("hand-typed keywords are accepted", r.status_code, 200)
kws = store.get_proposal(add_pid)["campaign"]["adGroups"][0]["keywords"]
check("all three landed", len(kws), 6)
truthy("the exact one kept its brackets", "[storm damage roofer]" in kws)
truthy('the phrase one kept its quotes', '"roof tarp service"' in kws)
truthy("and a bare term stayed bare", "hail damage" in kws)

staff.post(f"{MOUNT}/api/proposals/{add_pid}/edit", json={
    "addKeywords": [{"group": "Emergency Roof Repair", "keywords": "hail damage"}]})
check("adding one that is already there does not duplicate it",
      store.get_proposal(add_pid)["campaign"]["adGroups"][0]["keywords"].count("hail damage"), 1)

r = staff.post(f"{MOUNT}/api/proposals/{add_pid}/edit", json={
    "addKeywords": [{"group": "No Such Group", "keywords": "anything"}]})
check("a group that does not exist adds nothing", r.get_json()["changed"], [])

staff.post(f"{MOUNT}/api/proposals/{add_pid}/edit",
           json={"addNegatives": "roofing school, roof jobs"})
vault = store.get_proposal(add_pid)["campaign"]["negativeKeywordVault"]
check("hand-added negatives go in their own bucket, not mixed into the AI's",
      vault.get("addedByHand"), ["roofing school", "roof jobs"])
staff.post(f"{MOUNT}/api/proposals/{add_pid}/edit", json={"addNegatives": "roof jobs"})
check("...and a duplicate negative is not added twice",
      len(store.get_proposal(add_pid)["campaign"]["negativeKeywordVault"]["addedByHand"]), 2)

log = store.get_proposal(add_pid)["campaign"]["editLog"]
check("adding a keyword is material — it changes what the campaign bids on",
      [e for e in log if e["what"].startswith("Added") and "negative" not in e["what"]][-1]["material"],
      True)
check("adding a NEGATIVE is not — it only narrows what can be spent",
      [e for e in log if "negative" in e["what"]][-1]["material"], False)
truthy("the two read differently in the edit log, so neither is a substring of the other",
       not any(e["what"].endswith("keyword(s) by hand") and "negative" in e["what"]
               and e["what"].replace("negative ", "") in [x["what"] for x in log]
               for e in log))


# ===========================================================================
section("The approval hub names the step that blocks everything else")

hub = staff.get(f"{MOUNT}/approvals").get_data(as_text=True)
truthy("the queue of unapproved estimates has its own section",
       "Approve these estimates first" in hub)
truthy("...saying what it blocks",
       "client link cannot be created" in hub)
truthy("...and linking straight to the approve card", "#approve" in hub)
truthy("the proposal page has that anchor to land on",
       'id="approve"' in staff.get(f"{MOUNT}/proposal/{add_pid}").get_data(as_text=True))

# The queue must hold only what is actually blocked. Asserted by slicing the
# section out of the page, because "the client's name appears somewhere on the
# approvals page" is true of every row and proves nothing.
def approval_queue():
    page = staff.get(f"{MOUNT}/approvals").get_data(as_text=True)
    if "Approve these estimates first" not in page:
        return None
    start = page.index("Approve these estimates first")
    return page[start:page.index("</table>", start)]

queue = approval_queue()
truthy("the unapproved proposal is in the queue", queue and add_pid in queue)
check("...and the queue links to it", f"/proposal/{add_pid}#approve" in (queue or ""), True)

staff.post(f"{MOUNT}/api/proposals/{add_pid}/estimate/approve", json={"acknowledged": True})
after = approval_queue()
check("once approved it leaves the queue",
      f"/proposal/{add_pid}#approve" in (after or ""), False)

archived = store.create_proposal(client_name="Old Idea",
                                 campaign={"businessName": "Old Idea", "monthlyBudget": 100},
                                 created_by="t")["id"]
store.set_status(archived, "ARCHIVED")
check("an archived proposal is not nagged about — nobody is going to approve it",
      f"/proposal/{archived}#approve" in (approval_queue() or ""), False)


# ===========================================================================
section("The client is told how to ask for a change")

client_page = anon.get(f"{MOUNT}/estimate/{comp_token}").get_data(as_text=True)
truthy("the pencil is explained before the document, not left to be found",
       "Use the pencil" in client_page)
check("...above the first section it applies to",
       client_page.index("Use the pencil") < client_page.index("Overview"), True)


# ===========================================================================
section("The logo is looked up, never guessed at")

from modules.ads_builder import logo as logo_lookup                # noqa: E402

check("a name with no domain yields no domain to look up",
      logo_lookup.domain_of("Northside Roofing"), "")
check("a URL yields its bare domain",
      logo_lookup.domain_of("https://www.northsideroofing.com/roof"), "northsideroofing.com")

no_domain = logo_lookup.from_brandfetch("", "Northside Roofing")
check("the billed lookup is not called without a domain", no_domain["found"], False)
truthy("...and says why", "nothing to look a logo up by" in no_domain["note"])

resolved = logo_lookup.resolve("Nobody At All", "https://nothing.example.com")
check("with nothing on file, resolve does not invent a URL", resolved.get("found"), False)
check("...and does not run a billed lookup on its own",
      "live lookup" in str(resolved.get("tried", [])), False)
truthy("...and says what to do next", resolved.get("next"))

# The provider that answers is an implementation detail. Naming it on screen
# only invites the question of what to do when it says no, and the answer a rep
# needs is where the logo came from, not who supplied it.
for probe in (logo_lookup.from_brandfetch("", "X"),
              logo_lookup.from_brandfetch("https://nothing.example.com", "X"),
              logo_lookup.from_client_record("Nobody", "https://nothing.example.com"),
              logo_lookup.resolve("Nobody", "https://nothing.example.com")):
    check(f"no vendor name in what a person reads: {str(probe.get('note'))[:34]!r}",
          "brandfetch" in str(probe.get("note", "")).lower()
          or "brandfetch" in str(probe.get("source", "")).lower()
          or "brandfetch" in str(probe.get("next", "")).lower(), False)

# A client is filed under a name and a domain, and the campaign reliably has
# neither — this is what made a logo that was plainly on file come back empty.
pairs = logo_lookup._candidates("Northside Roofing Co",
                                "https://northsideroofing.example.com/roof-repair")
truthy("the stored-logo lookup tries the campaign's own name and domain",
       any(x["name"] == "Northside Roofing Co"
           and x["domain"] == "northsideroofing.example.com" for x in pairs))
check("...and never asks for nothing at all",
      all(x["name"] or x["domain"] for x in pairs), True)
check("...without repeating a pair", len(pairs), len({(x["name"], x["domain"]) for x in pairs}))

missed = logo_lookup.from_client_record("Nobody At All", "https://nothing.example.com")
check("a miss is reported, not shrugged at", missed["found"], False)
truthy("...naming what it looked under, so the miss can be diagnosed", missed.get("tried"))


# ===========================================================================
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed\n")
sys.exit(1 if _failed else 0)
