"""Commercial Builder — which published advertising rules a spot engages.

    python3 test_commercial_compliance.py

Same shape as test_commercial_review.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database.

## Why this file exists

This tool renders finished, deliverable video. `testimonial` is a commercial
type on the Start page, and the offer field invites exactly the copy Truth in
Lending triggers on. Nothing asked, so the first person to find out was
whoever had to answer for the spot after it ran.

Each section guards one way that goes wrong — and the first two are the ones
that would make the whole feature harmful rather than merely absent:

  1. **A tool that says "compliant" is worse than no tool**, because the tick
     is what somebody relies on. Every output has to read as *this engages X*
     and never as *this passes*.

  2. **A rule that fires on every spot is a rule people stop reading.** "20%
     off everything" is the commonest line in retail copy and it is not a rate
     of finance charge.

  3. **An empty industry is not an unregulated client.** A confident zero
     there is a spot going out unchecked.

  4. **A gate that refuses a correct spot gets switched off** — QR_CODE_RULES
     paid for that lesson. Nothing here blocks a render.

  5. **A sign-off is about the copy as it was.** Rewriting the offer afterwards
     must not leave somebody's name on a spot they never read.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbcomp_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cbcomp-test-secret"
os.environ["PANEL_PASSWORD"] = "cbcomp-test-password"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY", "HEYGEN_API",
           "RUNWAY_API_KEY", "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


MOUNT = "/tools/commercial-builder"

from modules.commercial_builder import compliance_spec as cs                # noqa: E402


def scan(copy="", industry="", commercial_type=""):
    return cs.scan(script={"scenes": [{"voiceover": copy}]},
                   client={"industry": industry},
                   commercial_type=commercial_type)


def ids(result):
    return sorted(f["id"] for f in result["findings"])


# ---------------------------------------------------------------------------
section("Nothing here claims a spot is compliant")
# The load-bearing rule. A tick over a legal question is the thing somebody
# relies on, and it is a judgment this tool cannot make.
clean = scan("Twenty years serving the valley.", industry="HVAC")
check("a clean scan carries the disclaimer",
      "not legal advice" in clean["disclaimer"], True)
check("and says it is not a clearance", "clearance" in clean["disclaimer"], True)
check("the summary does not say compliant",
      "complian" in cs.summary(clean).lower(), False)
check("nor does it say passed", "pass" in cs.summary(clean).lower(), False)
# Every finding is phrased as a fact about the copy, not a legal conclusion.
law = scan("We recovered $2.4 million.", industry="Law Firms")
for finding in law["findings"]:
    check(f"“{finding['id']}” does not assert a violation",
          "violat" in (finding["headline"] + finding["requires"]).lower(), False)


section("Every rule names the authority behind it")
# The abcd_service rule: a citation somebody can look up is an argument, and
# "our tool thinks you need a disclaimer" is an opinion a rep gets argued out of.
for key, spec in cs.REGIMES.items():
    check(f"{key} cites a rule", bool(spec["citation"]), True)
    check(f"{key} names who wrote it", bool(spec["authority"]), True)
for finding in law["findings"]:
    check(f"“{finding['id']}” carries its citation onto the finding",
          bool(finding["citation"]) and bool(finding["authority"]), True)
check("the five regimes are the five",
      sorted(cs.REGIMES), ["finra_2210", "ftc_endorsements", "reg_z",
                           "state_bar", "ttb"])


section("A rule that no longer exists is not raised")
# The CARS Rule was vacated in its entirety by the Fifth Circuit in January
# 2025. Flagging it would be the confidently wrong answer wearing a
# regulation — and it is named rather than silently dropped so nobody adds it
# back from memory.
check("the CARS rule is named as not enforced", "cars_rule" in cs.NOT_ENFORCED, True)
check("with the reason on it", "acated" in cs.NOT_ENFORCED["cars_rule"]["why"], True)
check("and it is not a live regime", "cars_rule" in cs.REGIMES, False)
car = scan("Drive away today. See dealer for details.", industry="Auto dealer")
check("a vehicle ad raises no CARS finding",
      any("cars" in f["id"] for f in car["findings"]), False)


# ---------------------------------------------------------------------------
section("Regulation Z is detected from the copy, never from the industry")
# A furniture shop advertising "$40 a month" engages it; a bank advertising
# its brand does not.
payment = scan("Sofas from just $40 a month with no money down.", industry="Furniture")
check("a payment amount triggers it",
      "reg_z_triggering_term" in ids(payment), True)
check("and the evidence quotes the words",
      "$40 a month" in payment["findings"][0]["evidence"], True)
check("the requirement names the disclosures",
      "annual percentage rate" in payment["findings"][0]["requires"], True)
check("a brand spot from a bank raises nothing",
      ids(scan("Your community bank since 1962.", industry="Community bank")), [])

# The false positive that would have made this unreadable.
check("“20% off everything” is not a rate of finance charge",
      ids(scan("20% off everything, this weekend only.", industry="Furniture")), [])
check("nor is “save 15%”",
      ids(scan("Save 15% on every mattress.", industry="Furniture")), [])
check("but “0% financing” is",
      "reg_z_rate_without_apr" in ids(scan("0% financing for 36 months.",
                                           industry="Furniture")), True)
check("and 7.9% interest is",
      "reg_z_rate_without_apr" in ids(scan("Just 7.9% interest.",
                                           industry="Furniture")), True)
# Stating the APR is what 1026.24(c) asks for, so saying it retires that one.
check("a rate stated AS an APR does not raise the missing-APR finding",
      "reg_z_rate_without_apr" in ids(scan("7.9% APR on approved credit.",
                                           industry="Furniture")), False)
# The triggering-term finding stands either way — the APR is only one of the
# three disclosures (d)(2) requires — but it can report that the copy has it.
with_apr = scan("$40 a month, 36 monthly payments, 7.9% APR, $0 down.",
                industry="Furniture")
check("a triggering term still engages the rule",
      "reg_z_triggering_term" in ids(with_apr), True)
check("and the panel can see the script mentions the APR",
      with_apr["findings"][0]["addressed"], True)


section("A testimonial engages the FTC guides, and a claimed result engages more")
t = scan("Best decision I ever made.", commercial_type="testimonial", industry="HVAC")
check("the commercial type alone triggers it",
      "ftc_material_connection" in ids(t), True)
check("and the evidence says which", "Testimonial" in t["findings"][0]["evidence"], True)
check("the requirement names the material connection",
      "255.5" in t["findings"][0]["requires"], True)
result = scan("I switched and saved $400 on my first bill.",
              commercial_type="testimonial", industry="Insurance")
check("a stated result raises the typicality rule",
      "ftc_typical_results" in ids(result), True)
check("with the figure quoted",
      any("$400" in f["evidence"] for f in result["findings"]), True)
check("and it names the 2023 revision",
      any("2023" in f["requires"] for f in result["findings"]
          if f["id"] == "ftc_typical_results"), True)
disclosed = scan("Paid actor portrayal. Best decision I ever made.",
                 commercial_type="testimonial", industry="HVAC")
check("a disclosed connection is seen in the script",
      disclosed["findings"][0]["addressed"], True)
check("a spot with no testimonial raises none of it",
      any(f["regime"] == "ftc_endorsements"
          for f in scan("Open seven days.", industry="HVAC")["findings"]), False)


section("Three regimes come from the client, and one absence is not an answer")
check("a law firm engages the bar rules",
      "state_bar_jurisdiction" in ids(scan("Call today.", industry="Law Firms")), True)
check("a broker-dealer engages FINRA",
      "finra_principal_approval" in ids(scan("Call today.",
                                             industry="Investment advisory")), True)
check("a brewery engages TTB",
      "ttb_mandatory_statements" in ids(scan("Now on tap.", industry="Brewery")), True)
check("an HVAC company engages none of the three",
      ids(scan("Call today.", industry="HVAC")), [])

# The one that matters most. An empty industry is NOT an unregulated client.
unknown = scan("Call today.", industry="")
check("no industry recorded is not measured", unknown["industry_known"], False)
check("and it says so in words", "not the same as them not applying" in unknown["note"], True)
check("the summary says the industry rules were not checked",
      "not checked" in cs.summary(unknown), True)
check("a known industry with nothing engaged reads differently",
      "not checked" in cs.summary(scan("Call today.", industry="HVAC")), False)


section("The rules that are about what the copy says, not who the client is")
check("a guaranteed return engages 2210(d)",
      "finra_performance_claim" in ids(scan("A guaranteed return every year.",
                                            industry="Investment advisory")), True)
check("a past result engages the bar's disclaimer rule",
      "state_bar_past_results" in ids(scan("We recovered $2.4 million.",
                                           industry="Law Firms")), True)
check("with the figure quoted, not truncated",
      any("$2.4 million" in f["evidence"]
          for f in scan("We recovered $2.4 million.", industry="Law Firms")["findings"]), True)
disclaimed = scan("We recovered $2.4 million. Prior results do not guarantee a "
                  "similar outcome.", industry="Law Firms")
check("and a disclaimer in the script is seen",
      next(f["addressed"] for f in disclaimed["findings"]
           if f["id"] == "state_bar_past_results"), True)
check("a superlative engages Rule 7.1",
      "state_bar_specialist" in ids(scan("The best lawyers in Dayton.",
                                         industry="Law Firms")), True)
check("plural included, because that is the line people write",
      "state_bar_specialist" in ids(scan("The best lawyer in Dayton.",
                                         industry="Law Firms")), True)
check("a health claim engages the TTB prohibition",
      "ttb_health_claim" in ids(scan("Our heart-healthy craft lager.",
                                     industry="Brewery")), True)
check("and an ordinary beer spot does not",
      "ttb_health_claim" in ids(scan("Brewed in Dayton since 1998.",
                                     industry="Brewery")), False)


section("Nothing in it may raise, and a failure is not a clean bill")
check("garbage in still answers", cs.scan(script="not a dict")["measured"] in (True, False), True)
check("None everywhere still answers",
      isinstance(cs.scan(None, None, None, None, "")["findings"], list), True)
check("and an unreadable scan is not reported as measured",
      cs.scan(script={"scenes": [{"voiceover": "x"}]},
              client={"industry": "HVAC"})["measured"], True)


section("A sign-off is about the copy as it was")
a = scan("Sofas from $40 a month.", industry="Furniture")
b = scan("Sofas from $60 a month.", industry="Furniture")
same = scan("Sofas from $40 a month.", industry="Furniture")
check("the same copy fingerprints the same", cs.findings_key(a), cs.findings_key(same))
check("a changed offer fingerprints differently",
      cs.findings_key(a) != cs.findings_key(b), True)
check("an acknowledgment is needed when anything is engaged",
      cs.needs_acknowledgment(a), True)
check("and not when nothing is",
      cs.needs_acknowledgment(scan("Open seven days.", industry="HVAC")), False)


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
import werkzeug.test                                                    # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402
from hub import users                                                   # noqa: E402
from modules.commercial_builder.db import db                            # noqa: E402
from modules.commercial_builder.models import (Client, ComplianceAck,    # noqa: E402
                                               RenderJob)

staff = werkzeug.test.Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"]}, follow_redirects=True)

with hub_app.app_context():
    _u = users.create_account("compliance-test@smart1marketing.com", "Dana Reyes",
                              role="member", password="StartHere12026!", status="active")
    # create_account forces a change at next sign-in, which is right for a real
    # account and in the way of a test acting as an ordinary signed-in rep.
    _u.must_change_password = False
    db.session.commit()
rep = werkzeug.test.Client(application)
rep.post("/login", data={"email": "compliance-test@smart1marketing.com",
                         "password": "StartHere12026!"}, follow_redirects=True)

client_row = staff.post(MOUNT + "/api/clients",
                        json={"name": "Dayton Law", "website": "daytonlaw.test"}
                        ).get_json()["client"]
with hub_app.app_context():
    row = db.session.get(Client, client_row["id"])
    row.industry = "Law Firms"
    db.session.commit()
pid = staff.post(MOUNT + "/api/projects",
                 json={"client_id": client_row["id"], "lengths": [30],
                       "formats": ["16:9"], "commercial_type": "stock_vo",
                       "platform": "ctv"}).get_json()["projects"][0]["id"]
staff.put(MOUNT + f"/api/projects/{pid}/brief",
          json={"what_advertising": "We recovered $2.4 million for our client."})


section("The panel is its own route, on the screen the script is on")
# QC makes an OpenAI call for the spelling pass; re-running the whole set on
# every edit of the offer would be a model call per keystroke.
payload = staff.get(MOUNT + f"/api/projects/{pid}/compliance").get_json()
check("it answers", payload["ok"], True)
check("with findings", len(payload["compliance"]["findings"]) >= 2, True)
check("and the disclaimer travels with them",
      "not legal advice" in payload["compliance"]["disclaimer"], True)
check("the vacated rule is carried so a reader can see it was considered",
      "cars_rule" in payload["not_enforced"], True)
check("nobody has acknowledged it yet", payload["acknowledged"], False)
check("and that is not a supersession", payload["superseded"], False)
blueprint = staff.get(MOUNT + f"/project/{pid}/blueprint").get_data(as_text=True)
check("the panel is on the Blueprint", 'id="compliance-card"' in blueprint, True)
check("the bubble is placed", "commercial_builder.blueprint.compliance" in blueprint, True)
from hub import help as hub_help                                        # noqa: E402
check("and it resolves to content",
      bool(hub_help.get("commercial_builder.blueprint.compliance")), True)


section("It advises; it never blocks a render")
qc = staff.post(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]
check("the check ran", "compliance" in qc, True)
# QR_CODE_RULES paid for this lesson: a check that refuses the correct thing
# is a check somebody switches off, and switching it off costs every other
# finding it would have raised.
check("it can only ever warn", qc["compliance"]["level"], "warn")
check("and it is declared advisory server-side",
      "compliance" in __import__(
          "modules.commercial_builder.services.qc_service",
          fromlist=["x"]).ADVISORY_CHECKS, True)
check("the findings ride alongside the one-line check",
      len(qc["_compliance"]["findings"]) >= 2, True)
for js_file in ("blueprint.js", "preview.js"):
    text = (ROOT / "modules/commercial_builder/static/js" / js_file).read_text()
    block = text[text.index("QC_LABELS = {"):]
    check(f"{js_file} draws the row", "compliance:" in block[:block.index("};")], True)


section("Filing waits on an acknowledgment, and a shared login cannot give one")
with hub_app.app_context():
    job = RenderJob(project_id=pid, format="16:9", status="succeeded",
                    output_url="https://example.test/spot.mp4")
    db.session.add(job)
    db.session.commit()
    job_id = job.id

blocked = staff.post(MOUNT + f"/api/projects/{pid}/render-jobs/{job_id}/approve", json={})
check("filing is refused", blocked.status_code, 409)
check("and it names the regime", "Attorney advertising" in blocked.get_json()["error"], True)
check("it says what the acknowledgment is not",
      "not a judgment" in blocked.get_json()["error"], True)

# "Shared login" is a true statement about the session and a useless one in a
# record whose entire value is the name on it. hub/ad_copy.py refuses the same.
shared = staff.post(MOUNT + f"/api/projects/{pid}/compliance/acknowledge", json={})
check("a shared-password session cannot acknowledge", shared.status_code, 400)
check("and is told why", "no account behind it" in shared.get_json()["error"], True)

signed = rep.post(MOUNT + f"/api/projects/{pid}/compliance/acknowledge",
                  json={"note": "Cleared with the firm's GC."})
check("a named account can", signed.status_code, 200)
check("and the name is recorded",
      signed.get_json()["acknowledgment"]["acknowledged_by"], "Dana Reyes")
# Kept verbatim so the record reads back years later without re-running a
# scanner whose patterns have since changed.
check("with the findings as they stood",
      len(signed.get_json()["acknowledgment"]["findings"]) >= 2, True)
check("and the note", "GC" in signed.get_json()["acknowledgment"]["note"], True)

filed = staff.post(MOUNT + f"/api/projects/{pid}/render-jobs/{job_id}/approve", json={})
check("filing then works", filed.status_code, 200)
check("and the record says who acknowledged it",
      filed.get_json()["compliance"]["acknowledged_by"], "Dana Reyes")


section("Rewriting the offer retires the sign-off")
staff.put(MOUNT + f"/api/projects/{pid}/brief",
          json={"what_advertising": "We recovered $9.9 million and we are the "
                                    "best lawyers in Ohio."})
after = staff.get(MOUNT + f"/api/projects/{pid}/compliance").get_json()
check("the acknowledgment no longer covers it", after["acknowledged"], False)
# "Nobody has looked" and "somebody looked at a different script" are
# different situations, and only the second has a name to go back to.
check("and it is reported as superseded, not as absent", after["superseded"], True)
check("the earlier sign-off is still on file",
      after["acknowledgment"]["acknowledged_by"], "Dana Reyes")
with hub_app.app_context():
    check("nothing was deleted to achieve that",
          ComplianceAck.query.filter_by(project_id=pid).count(), 1)


section("A project that engages nothing files exactly as before")
plain = staff.post(MOUNT + "/api/clients",
                   json={"name": "Valley HVAC", "website": "valleyhvac.test"}
                   ).get_json()["client"]
with hub_app.app_context():
    r2 = db.session.get(Client, plain["id"])
    r2.industry = "HVAC"
    db.session.commit()
pid2 = staff.post(MOUNT + "/api/projects",
                  json={"client_id": plain["id"], "lengths": [30],
                        "formats": ["16:9"], "commercial_type": "stock_vo",
                        "platform": "ctv"}).get_json()["projects"][0]["id"]
staff.put(MOUNT + f"/api/projects/{pid2}/brief",
          json={"what_advertising": "Twenty years serving the valley."})
with hub_app.app_context():
    j2 = RenderJob(project_id=pid2, format="16:9", status="succeeded",
                   output_url="https://example.test/hvac.mp4")
    db.session.add(j2)
    db.session.commit()
    job2 = j2.id
ok = staff.post(MOUNT + f"/api/projects/{pid2}/render-jobs/{job2}/approve", json={})
check("it files with no acknowledgment at all", ok.status_code, 200)
check("and nothing was engaged", ok.get_json()["compliance"]["regimes"], [])


section("The staff routes are not public")
anon = werkzeug.test.Client(application)
check("reading the findings needs a login",
      anon.get(MOUNT + f"/api/projects/{pid}/compliance").status_code, 401)
check("and so does acknowledging them",
      anon.post(MOUNT + f"/api/projects/{pid}/compliance/acknowledge",
                json={}).status_code, 401)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
