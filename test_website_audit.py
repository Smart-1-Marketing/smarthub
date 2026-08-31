"""The Website Audit: the spend block, the widget, the lead, and merging.

    python3 test_website_audit.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

An Insites audit carries 440 fields and four screens now read it: the client
record, this tool, a widget on somebody else's website, and the Proposal
Builder. Every failure below is one where all four would go on looking
healthy while saying something false about a business's money.

  1. **A total is only a total when every part of it was measured.** Facebook
     and display are observed as *running* with no spend published for either,
     so a monthly total that quietly counted them as zero would report a
     business spending $6,000 as spending $2,400 — in a clean, confident row,
     on the page a rep quotes from.

  2. **Arithmetic shows its working.** Annualising a third-party estimate is
     our multiplication; a cost per visit is their two numbers divided. Both
     are printed with the sum beside them, because the figures about a
     client's own money are the ones they check hardest.

  3. **What they told us never merges with what was observed.** Where the two
     disagree the disagreement is the finding, and folding one into the other
     destroys the only evidence of it.

  4. **Sixty days.** A reading older than that describes a site that may have
     been rebuilt since, and carries no sign of its own age once it is quoted
     from. A date that cannot be read is *not measured*, never zero — zero
     reads as "scanned today" on the one screen that decides whether to spend
     a credit.

  5. **The same figures must not appear twice under two headings.** This
     module prints its own spend block and drops scan_facts' one; two panels
     answering one question differently is how a reader learns to believe
     neither.

  6. **Absent is not a finding.** Every opportunity rule tests `is True` or
     `is False`, so a plan that does not check for pixels cannot produce
     "no retargeting pixel found".

  7. **A placement's kind is fixed at creation.** It is in the embed code
     already pasted on a client's website, and swapping it changes what a
     visitor is asked without anybody visiting the page.

  8. **Merging keeps everything.** The survivor's own values win, the merged
     rows are kept rather than deleted, and two Suite contacts stay two Suite
     contacts — merging here does not undo a delivery, and saying so is the
     only honest answer.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1audit_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "audit-test-secret"
os.environ["PANEL_PASSWORD"] = "audit-test-password"
os.environ["HUB_LEADS_FILE"] = os.path.join(DISK, "leads.jsonl")

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


from hub import website_audit as wa                           # noqa: E402


def figs(block):
    return {f["label"]: f for f in block["observed"]}


# =====================================================================
section("The questions all feed something, and the spend group has one home")
# =====================================================================

check("check_spec is clean", wa.check_spec(), [])
check("a customer is asked fewer questions than a rep",
      len(wa.questions("customer")) < len(wa.questions("staff")), True)
check("every customer question is one the staff form also asks",
      all(q["key"] in {s["key"] for s in wa.questions("staff")}
          for q in wa.questions("customer")), True)


# =====================================================================
section("What they are already spending")
# =====================================================================

FULL = {
    "paid_search": {"has_adwords_spend": True, "average_adspend": 2400,
                    "average_adtraffic": 800},
    "facebook_ads": {"fb_ads_currently_active": 6,
                     "fb_ad_library_url": "https://facebook.com/ads"},
    "display_ads": {"uses_display_ads": True},
    "organic_search": {"average_monthly_traffic": 1200,
                       "num_keywords_ranked_for": 310},
}

sp = wa.spend(FULL)
rows = figs(sp)
check("the monthly estimate is carried",
      rows["Google Ads, estimated monthly"]["value"], "$2,400")
check("annual is the monthly times twelve",
      rows["Google Ads, estimated annually"]["value"], "$28,800")
check("and it says that is our multiplication",
      "× 12" in rows["Google Ads, estimated annually"]["why"], True)
check("cost per visit is their spend over their visits",
      rows["Implied cost per visit"]["value"], "$3.00")
check("and it names the two numbers",
      "÷" in rows["Implied cost per visit"]["why"], True)

check("the total is only what carries a number", sp["total"], 2400)
check("paid social is counted as running, not as money",
      rows["Facebook / Instagram ads live now"]["measured"], False)
check("display too", rows["Display advertising"]["measured"], False)
check("and both are named as left out of the total",
      len(sp["total_excludes"]), 2)
check("the total says so in words",
      "leaves out" in sp["total_note"], True)

# What organic would cost is their own numbers multiplied, or nothing at all.
check("organic is priced only from their own cost per visit",
      "$3,600" in sp["earned_note"], True)

no_cpc = wa.spend({"organic_search": {"average_monthly_traffic": 1200}})
check("with no campaign of their own it is not measured",
      "not measured" in no_cpc["earned_note"], True)
check("and no sector average is invented",
      "$" in no_cpc["earned_note"], False)


# =====================================================================
section("Running is not the same as spending, and neither is silence")
# =====================================================================

running_only = wa.spend({"paid_search": {"has_adwords_spend": True}})
r = figs(running_only)
check("Google Ads running with no figure is not measured",
      r["Google Ads"]["measured"], False)
check("so there is no total at all", running_only["total"], None)
check("and it is named as the reason there is not",
      "Google Ads" in running_only["total_excludes"][0], True)

not_running = wa.spend({"paid_search": {"has_adwords_spend": False}})
check("not running is an answer", figs(not_running)["Google Ads"]["value"],
      "Not running")
check("and it is a measured one", figs(not_running)["Google Ads"]["measured"], True)

silent = wa.spend({})
check("nothing at all reads as not measured", silent["measured"], False)
check("and says it is not a business spending nothing",
      "not a business spending nothing" in silent["note"], True)


# =====================================================================
section("What they told us sits beside what was seen, never inside it")
# =====================================================================

told = wa.spend(FULL, {"monthly_budget": "$5,000 - $10,000"})
check("the band is carried as stated", told["stated"]["band"], "$5,000 - $10,000")
check("the observed total is untouched by it", told["total"], 2400)
check("and the gap is the finding",
      "somewhere this audit cannot see" in told["stated"]["finding"], True)

quiet = wa.spend(FULL, {"monthly_budget": "Rather not say"})
check("declining to say is an answer, not a blank",
      "chose not to say" in quiet["stated"]["note"], True)
check("and it invents no figure", quiet["stated"]["midpoint"], None)

close = wa.spend(FULL, {"monthly_budget": "$1,000 - $2,500"})
check("a band that broadly agrees with the estimate raises nothing",
      "finding" in close["stated"], False)
under = wa.spend(FULL, {"monthly_budget": "Under $1,000"})
check("an estimate well above what they said is still worth asking about",
      "worth asking about" in under["stated"]["finding"], True)


# =====================================================================
section("How old the reading is")
# =====================================================================

def stamp(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)
            ).strftime("%Y-%m-%d %H:%M:%S")


check("a fresh reading is not stale", wa.staleness(stamp(3))["stale"], False)
check("sixty days is still inside the line", wa.staleness(stamp(60))["stale"], False)
check("sixty-one is over it", wa.staleness(stamp(61))["stale"], True)
check("and the age is on it", wa.staleness(stamp(61))["age_days"], 61)
check("an unreadable date is not measured",
      wa.staleness("who knows")["measured"], False)
check("and is never reported as nought days",
      wa.staleness("who knows")["age_days"], None)
check("the limit travels with it", wa.staleness(stamp(3))["limit_days"],
      wa.STALE_DAYS)


# =====================================================================
section("Findings carry their evidence, and absent is not a finding")
# =====================================================================

keys = lambda report: {o["key"] for o in wa.opportunities(report)}   # noqa: E731

check("a missing pixel is a finding",
      "no_retargeting" in keys({"retargeting": {"has_facebook_pixel": False,
                                                "has_google_pixel": False}}), True)
check("a plan that did not check for one is not",
      "no_retargeting" in keys({}), False)
check("half an answer is not a finding either",
      "no_retargeting" in keys({"retargeting": {"has_facebook_pixel": False}}), False)
check("an unclaimed listing is a finding",
      "gbp_unclaimed" in keys({"google_business_profile": {
          "is_listing_found": True, "is_listing_claimed": False}}), True)
check("a claimed one is not",
      "gbp_unclaimed" in keys({"google_business_profile": {
          "is_listing_found": True, "is_listing_claimed": True}}), False)

found = wa.opportunities({"analytics": {"has_analytics": False}})
check("every finding says what it costs them", bool(found[0]["means"]), True)
check("and names what it sells", found[0]["sells"], "Analytics setup")


# =====================================================================
section("Discovery answers the audit can already fill in")
# =====================================================================

from hub import current_marketing                             # noqa: E402

answers = wa.discovery_answers(FULL)
known = {q["key"] for q in current_marketing.QUESTIONS}
check("every answer maps onto a question the proposal actually asks",
      all(a["key"] in known for a in answers), True)
check("every answer carries its evidence",
      all(a["evidence"] for a in answers), True)
check("paid search comes back from the spend",
      next(a["answer"] for a in answers if a["key"] == "paidSearch"), "yes")
check("paid social comes back from the ad library",
      next(a["answer"] for a in answers if a["key"] == "paidSocial"), "yes")
check("a question the audit cannot speak to is left out, not answered unknown",
      all(a["answer"] in ("yes", "no") for a in answers), True)
check("an audit with nothing in it answers nothing", wa.discovery_answers({}), [])
check("and says that is not measured rather than all-no",
      "not measured" in wa.discovery_note([]), True)


# =====================================================================
section("What a scan hands the lead store")
# =====================================================================

payload = {"domain": "acme.com", "score": 62,
           "spend": wa.spend(FULL, {"monthly_budget": "Under $1,000"}),
           "opportunities": wa.opportunities({"analytics": {"has_analytics": False}}),
           "intake": {"goal": "Phone calls", "services": "Plumbing"}}
fields = wa.lead_fields(payload, {"name": "Jo", "email": "jo@acme.com",
                                  "phone": "3175550142", "company": "Acme"})
check("every field is a flat string a CRM can hold",
      all(isinstance(v, str) for v in fields.values()), True)
check("the website is the audited domain", fields["website"], "acme.com")
check("the estimate goes on it",
      fields["estimated_ad_spend_monthly"], "$2,400")
check("so does what they said themselves",
      fields["stated_marketing_budget"], "Under $1,000")
check("and what they told us they want", fields["goal"], "Phone calls")


# =====================================================================
section("The audit reads one scan, and prints the spend once")
# =====================================================================

from hub import scan_facts                                    # noqa: E402
# The scan row goes in through the module that owns the table, not through
# hand-written SQL: a second description of what a scan row is drifts the day
# a column is added, and the test then passes against a table the live app
# would not recognise.
from modules.scans import app as scans_app                    # noqa: E402

REPORT = dict(FULL)
REPORT.update({
    "google_business_profile": {"is_listing_found": True,
                                "is_listing_claimed": False,
                                "review_count": 8, "review_rating": 4.6},
    "analytics": {"has_analytics": True, "analytics_tool": "GA4"},
    "meta": {"detected_name": "Acme Plumbing"},
})

_db = scans_app.SessionLocal()
try:
    _db.add(scans_app.Scan(
        public_id="sc123", domain_key="acme.com", input_url="acme.com",
        overall_score=62, tier="Bronze", status="complete",
        raw_report=json.dumps(REPORT),
        created_at=datetime.now(timezone.utc) - timedelta(days=10),
        completed_at=datetime.now(timezone.utc) - timedelta(days=10)))
    _db.commit()
finally:
    _db.close()

report, meta, err = scan_facts.latest_report("acme.com")
check("latest_report finds the scan", meta.get("public_id"), "sc123")
check("and carries no error", err, "")
check("the audit payload has it too", wa.audit("acme.com")["public_id"], "sc123")

full = wa.audit("acme.com", intake={"monthly_budget": "Over $10,000"})
check("the audit is found", full["found"], True)
check("its spend block is this module's own",
      full["spend"]["total"], 2400)
check("the reference groups do not repeat it",
      any(g["title"] == wa.SPEND_GROUP_TITLE for g in full["groups"]), False)
check("but the other groups are still there", len(full["groups"]) > 3, True)
check("the age travels with it", full["age"]["age_days"], 10)
check("what they told us reaches the spend block",
      full["spend"]["stated"]["band"], "Over $10,000")

missing = wa.audit("nobody-scanned-this.com")
check("a domain nobody has scanned is found=False", missing["found"], False)
check("and carries no error, because that is not an error", missing["error"], "")
check("no domain at all says so", wa.audit("")["note"].startswith("No website"), True)


# =====================================================================
section("What the Proposal Builder is handed")
# =====================================================================

pre = wa.proposal_prefill(full)
check("the discovery answers come through as answers",
      pre["mkt"]["paidSearch"], "yes")
check("each with the evidence beside it",
      bool(pre["evidence"]["paidSearch"]), True)
check("the age comes with it, so a stale answer can say so",
      pre["age"]["age_days"], 10)
check("and it says nothing is applied until somebody presses",
      "offered, not applied" in pre["note"], True)

stale_pre = wa.proposal_prefill({"found": True, "domain": "acme.com",
                                 "age": wa.staleness(stamp(200)),
                                 "discovery": [], "intake": {}})
check("a stale audit still prefills rather than refusing",
      stale_pre["found"], True)
check("with the staleness on it", stale_pre["age"]["stale"], True)


# =====================================================================
section("The tool is reachable, guarded, and on the Tools page")
# =====================================================================

from hub import create_hub_app                              # noqa: E402

hub = create_hub_app()
c = hub.test_client()

r = c.get("/tools/website-audit")
check("the page refuses an anonymous visitor", r.status_code, 302)
check("and sends them to sign in", "/login" in r.headers.get("Location", ""), True)

r = c.get("/api/website-audit?domain=acme.com",
          headers={"Accept": "application/json"})
check("the API refuses one too", r.status_code, 401)
r = c.post("/api/website-audit/lead", json={"domain": "acme.com"},
           headers={"Accept": "application/json"})
check("including the write route", r.status_code, 401)

# Signed in the way the Hub actually signs somebody in: the signed cookie the
# guard reads. A session key would be a second description of what "signed in"
# means, and the one this tool is guarded by is the cookie.
from hub import auth as hub_auth                               # noqa: E402

c.set_cookie(hub_auth.COOKIE_NAME,
             hub_auth.issue_cookie_value("tester@smart1marketing.com"))

r = c.get("/api/website-audit/spec")
check("signed in, the spec answers", r.status_code, 200)
check("and hands over the intake", bool(r.get_json()["questions"]), True)

r = c.post("/api/website-audit/lead",
           json={"domain": "acme.com", "contact": {"name": "Jo", "company": "Acme"}})
check("a lead with no way to contact them is refused", r.status_code, 400)
check("by name", "email address or a phone number" in r.get_json()["error"], True)

tools_html = (ROOT / "hub" / "templates" / "tools.html").read_text()
start = tools_html.find('<div class="qa-group-label">Sales</div>')
nxt = tools_html.find('<div class="qa-group-label">', start + 10)
tile = tools_html.find('href="/tools/website-audit"')
check("the tile is on the Tools page", tile > -1, True)
check("in the Sales group", start < tile < nxt, True)

sidebar = (ROOT / "hub" / "sidebar.py").read_text()
check("and it has a sidebar entry", '"/tools/website-audit"' in sidebar, True)


# =====================================================================
section("One reading of one audit, on every screen that shows it")
# =====================================================================

# Client 360 was the last screen on the thin reading: five collapsed reference
# rows about what a business spends, where every other screen shows the total,
# the annualised figure and what is left out of it. Same client, same audit,
# two answers depending on which record you opened.

r = c.get("/api/client/audit?domain=acme.com")
check("the client record has the same audit behind it", r.status_code, 200)
same = r.get_json()
check("and it is the same reading, not a second description",
      same["spend"]["total"], full["spend"]["total"])
check("with the same findings", [o["key"] for o in same["opportunities"]],
      [o["key"] for o in full["opportunities"]])
check("the thin group is dropped there too, so the figures appear once",
      any(g["title"] == wa.SPEND_GROUP_TITLE for g in same["groups"]), False)

r = c.get("/api/client/audit?domain=acme.com", headers={"Accept": "application/json"})
check("it is behind the same login as the rest of the record", r.status_code, 200)

anon = hub.test_client()
r = anon.get("/api/client/audit?domain=acme.com",
             headers={"Accept": "application/json"})
check("and refuses an anonymous request", r.status_code in (302, 401), True)

# The card is framed inside Smart 1 Suite. `/api/website-audit` is not on the
# embed allowlist and `/api/client/` is, so a card pointed at the audit tool's
# own blueprint would render everywhere except inside the frame -- the
# half-broken embed hub/suite_embed.py exists to prevent.
from hub import suite_embed                                   # noqa: E402

check("the client route is reachable from inside the Suite frame",
      any("/api/client/audit".startswith(p) for p in suite_embed.EMBEDDABLE), True)
check("and the audit tool's own route is deliberately not",
      any("/api/website-audit".startswith(p) for p in suite_embed.EMBEDDABLE), False)

c360 = (ROOT / "hub" / "templates" / "client360.html").read_text()
check("Client 360 reads the client route rather than the tool's",
      "/api/client/audit?domain=" in c360, True)
check("and no longer reads the thin one",
      "/api/client/scan-facts" in c360, False)
check("the spend card is on the record", 'id="c-spend"' in c360, True)
# One definition and three call sites -- the no-website path, the answer and
# the failure. Every exit from that fetch has to draw the card, or it spins
# for ever on the one the reference card has already reported.
check("drawn from the same payload as the reference card, so neither can "
      "contradict the other", c360.count("drawSpend(") - 1, 3)
check("the no-website path draws it", "drawSpend(null, '')" in c360, True)
check("so does the answer", "drawSpend(d, dom)" in c360, True)
check("and so does the failure", "drawSpend(null, dom)" in c360, True)


# =====================================================================
section("The widget: a second kind of placement, not a second widget")
# =====================================================================

from modules.scans import leads as widget_state               # noqa: E402

check("a row written before the column existed reads as the original kind",
      widget_state.kind_of(None), "aeo")
check("so does anything unrecognized", widget_state.kind_of("something"), "aeo")
check("the audit kind is its own", widget_state.kind_of("audit"), "audit")
check("each kind carries its own wording",
      widget_state.defaults_for("audit")["headline"]
      != widget_state.defaults_for("aeo")["headline"], True)

contact, intake, errors = widget_state.validate_audit({
    "name": "Jo Brand", "company": "Acme Plumbing", "email": "jo@acme.com",
    "phone": "317 555 0142",
    "intake": {"goal": "Phone calls", "services": "Drains and boilers",
               "areas": "Carmel, IN"}})
check("a complete audit request validates", errors, {})
check("and keeps only what was answered",
      set(intake), {"goal", "services", "areas"})

_, _, errors = widget_state.validate_audit({
    "name": "Jo Brand", "company": "Acme", "email": "jo@acme.com",
    "phone": "3175550142", "intake": {"goal": "Phone calls"}})
check("a required question left blank is named",
      sorted(k for k in errors if k.startswith("intake.")),
      ["intake.areas", "intake.services"])

_, _, errors = widget_state.validate_audit({
    "name": "Jo", "company": "Acme", "email": "nope", "phone": "12",
    "intake": {"goal": "x", "services": "y", "areas": "z"}})
check("the contact rules are still the ones the other widget uses",
      sorted(errors), ["email", "phone"])

sc = scans_app.app.test_client()
r = sc.post("/api/widgets", json={"name": "Acme audit", "kind": "audit",
                                  "new_slug": "acme-audit"})
check("an audit placement can be created", r.status_code, 200)
check("and it knows what it is", r.get_json()["widget"]["kind"], "audit")

r = sc.post("/api/widgets", json={"name": "Acme audit", "slug": "acme-audit",
                                  "kind": "aeo"})
check("its kind cannot be switched under a live embed", r.status_code, 409)
check("and the refusal says why",
      "different form" in r.get_json()["error"], True)

r = sc.get("/w/acme-audit")
check("the placement serves its own page", r.status_code, 200)
body = r.get_data(as_text=True)
check("which asks the business about itself",
      "About the business" in body, True)
check("and asks for one contact, once",
      body.count('id="uemail"'), 1)

r = sc.post("/api/w/acme-audit/audit", json={
    "domain": "acme.com", "name": "Jo Brand", "company": "Acme Plumbing",
    "email": "jo@acme.com", "phone": "3175550142",
    "intake": {"goal": "Phone calls", "services": "Drains", "areas": "Carmel"}})
check("a complete request is accepted", r.status_code, 200)
token = r.get_json().get("token")
check("and hands back a report address", bool(token), True)

r = sc.post("/api/w/acme-audit/audit", json={"domain": "acme.com",
                                             "name": "Jo", "email": "jo@acme.com",
                                             "phone": "3175550142",
                                             "company": "Acme"})
check("an incomplete one is refused with the fields named", r.status_code, 400)

r = sc.get(f"/r/{token}")
check("the report page answers", r.status_code, 200)
page = r.get_data(as_text=True)
check("leading with what they are already spending",
      page.find("already spending") < page.find("would fix first"), True)
check("and it does not hand over the discovery mapping",
      "paidSearch" in page, False)

r = sc.get(f"/r/{token}.pdf")
check("the audit has no PDF, and says where the document is",
      r.status_code, 404)
check("by name", "print it from there" in r.get_data(as_text=True), True)

r = sc.get("/api/w/acme-audit/audit")
check("the audit endpoint is a POST and nothing else", r.status_code, 405)


# =====================================================================
section("Every audit is a lead, and the leads can merge")
# =====================================================================

from hub import leads                                         # noqa: E402

# A prospect of this section's own. The widget request further up filed a real
# lead for jo@acme.com, and grouping it in here would make the assertion about
# the fixture rather than about the grouping.
first = leads.capture("website_audit", "widget",
                      {"name": "Dana Roe", "email": "dana@bellows.com",
                       "phone": "3175550188", "company": "Bellows Heating",
                       "website": "bellows.com"})
second = leads.capture("scan_widget", "smart1-home",
                       {"name": "", "email": "dana@bellows.com", "phone": "",
                        "company": "Bellows Heating LLC", "website": "bellows.com"})
third = leads.capture("landing_ads", "hvac",
                      {"name": "Sam", "email": "sam@elsewhere.com",
                       "phone": "3175550001", "company": "Elsewhere Ltd"})

dupes = leads.merge_candidates()
emails = [g for g in dupes["certain"] if g["why"] == "email"]
check("two rows with one email address are grouped as certain",
      len(emails), 1)
check("and the group holds exactly those two", len(emails[0]["leads"]), 2)
check("a third business is not in it",
      third["id"] in {l["id"] for g in dupes["certain"] for l in g["leads"]}, False)
check("a name-only resemblance is a possible, never a certain",
      all(g["why"] != "company name" for g in dupes["certain"]), True)
check("the store read cleanly", dupes["error"], "")

merged = leads.merge(first["id"], [second["id"]], actor="tester")
check("the merge is accepted", merged["ok"], True)
row = merged["lead"]
check("the survivor keeps its own company name", row["company"], "Bellows Heating")
check("and its own name", row["name"], "Dana Roe")
check("the absorbed row is recorded rather than lost",
      row["merged_ids"][0]["id"], second["id"])
check("where it came from is kept too", row["also_from"],
      ["scan_widget / smart1-home"])

listing = leads.listing(days=30)
ids = {l["id"] for l in listing["leads"]}
check("the merged row leaves the panel", second["id"] in ids, False)
check("the survivor stays on it", first["id"] in ids, True)
check("and the panel counts the merge", listing["merged"], 1)

again = leads.merge(first["id"], [second["id"]], actor="tester")
check("merging the same row twice is refused", again["ok"], False)
check("because its values are already inside another one",
      "count it twice" in again["error"], True)

check("a row cannot be merged into itself",
      leads.merge(first["id"], [first["id"]])["ok"], False)

# Blanks are filled from the donor, and a donor's own values never win.
blank = leads.capture("website_audit", "tool",
                      {"email": "pat@third.com", "company": "Third Co"})
filled = leads.capture("scan_widget", "home",
                       {"name": "Pat Lee", "phone": "3175550009",
                        "email": "pat@third.com", "company": "Third Co Ltd"})
res = leads.merge(blank["id"], [filled["id"]], actor="tester")
check("an empty field is filled from the other row", res["lead"]["name"], "Pat Lee")
check("and one that was already answered is not",
      res["lead"]["company"], "Third Co")

# Two delivered rows mean two Suite contacts, and that stays true.
a = leads.capture("website_audit", "tool", {"email": "kim@fourth.com",
                                            "company": "Fourth"})
b = leads.capture("scan_widget", "home", {"email": "kim@fourth.com",
                                          "company": "Fourth"})
for row_id, cid in ((a["id"], "contact-a"), (b["id"], "contact-b")):
    stored = next(r for r in leads._read_all() if r["id"] == row_id)
    stored["contact_id"] = cid
    stored["delivered"] = True
    leads._update(stored)
res = leads.merge(a["id"], [b["id"]], actor="tester")
check("both Suite contacts are kept", res["lead"]["contact_ids"],
      ["contact-a", "contact-b"])
check("and the panel is told there are two",
      "does not merge those" in res["suite_note"], True)

# Two different clients is one company's enquiry attributed to another.
x = leads.capture("website_audit", "tool", {"email": "a@x.com"}, client="Client One")
y = leads.capture("website_audit", "tool", {"email": "a@x.com"}, client="Client Two")
res = leads.merge(x["id"], [y["id"]])
check("merging across two clients is refused", res["ok"], False)
check("and names both", "Client One" in res["error"] and "Client Two" in res["error"],
      True)

r = c.post("/api/leads/merge", json={"into": first["id"], "from": ["nope"]})
check("the merge route reports a lead it cannot find", r.status_code, 400)
r = c.get("/api/leads/duplicates")
check("and the duplicates route answers", r.status_code, 200)

# A survivor with no date must not swallow the donors' dates: min() over a
# list containing "" is "", and the earliest arrival — the field a follow-up
# queue sorts on — was quietly discarded exactly when the survivor needed it.
old_dt = "2024-01-05T09:00:00+00:00"
nodate = leads.capture("website_audit", "tool", {"email": "lee@fifth.com"})
dated = leads.capture("scan_widget", "home", {"email": "lee@fifth.com"})
for row_id, created in ((nodate["id"], ""), (dated["id"], old_dt)):
    stored = next(r for r in leads._read_all() if r["id"] == row_id)
    stored["created"] = created
    leads._update(stored)
res = leads.merge(nodate["id"], [dated["id"]], actor="tester")
check("a dateless survivor takes the donor's arrival date",
      res["lead"]["created"], old_dt)

# The window the label claims is the window the rows cover. The route allows
# up to 730 days and the cutoff used to stop at 365 while echoing the raw
# number — a wrong label over right rows.
aged = leads.capture("website_audit", "tool", {"email": "old@sixth.com"})
stored = next(r for r in leads._read_all() if r["id"] == aged["id"])
stored["created"] = (datetime.now(timezone.utc)
                     - timedelta(days=400)).isoformat(timespec="seconds")
leads._update(stored)
in_two_years = {l["id"] for l in leads.listing(days=730)["leads"]}
in_one_year = {l["id"] for l in leads.listing(days=365)["leads"]}
check("a 400-day-old lead is inside the 730-day window",
      aged["id"] in in_two_years, True)
check("and outside the 365-day one", aged["id"] in in_one_year, False)
check("an absurd window is reported clamped, not echoed",
      leads.listing(days=9999)["days"], 730)

# "With a report" counts what the Report column offers: the website audit
# produces a page rather than a PDF, and counting only pdf_url said "N with
# a report" over a table plainly offering more.
before = leads.listing(days=7)["with_report"]
leads.capture("website_audit", "tool", {"email": "aud@seventh.com"},
              meta={"audit_url": "https://smart1.agency/scans/r/tok123"})
check("a lead whose report is an audit page counts as having a report",
      leads.listing(days=7)["with_report"], before + 1)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
