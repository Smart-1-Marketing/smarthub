"""Commercial Builder — the client review link.

    python3 test_commercial_review.py

Same shape as test_commercial_wizard.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

A rendered commercial was approved by a **rep** pressing Approve & file. The
client saw it when the account manager emailed an MP4 or a Cloudinary link,
replied with three changes in the body of an email, and somebody retyped them
into a storyboard. So nothing recorded which cut the client approved, who at
the client approved it, or what they asked for on the round before — which is
fine right up until a client says "we never signed off on that".

Each section below guards one way that goes quietly wrong:

  1. **A client-facing page behind a login is a login form in front of
     somebody with no account.** This module is a BLUEPRINT on the hub app
     rather than a dispatcher-mounted one, so `wsgi.py`'s `PUBLIC_PREFIXES` —
     which does exactly this for `modules/ads_builder` and `modules/scans` —
     never sees it. Both halves have to be written out separately, and a page
     exempted from the login but not from the chrome arrives at the client
     wearing the staff sidebar.

  2. **Two answers cannot express the answer most sign-offs actually are.**
     "Yes, but fix the phone number" forced into approve-or-reject goes to
     whichever end is nearest, and both are wrong.

  3. **A link gets forwarded, and then two people answer it.** Taking the
     latest answer lets a colleague's "looks good" overwrite the compliance
     officer's "you cannot say that", after which the cut ships.

  4. **A cut the client refused must not reach their library.** Filing is what
     makes it a deliverable and puts it on their 360 record.

  5. **A round cap that stops the CLIENT pushes the whole conversation back
     into email**, where none of this is recorded.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbrev_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cbrev-test-secret"
os.environ["PANEL_PASSWORD"] = "cbrev-test-password"
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


# ---------------------------------------------------------------------------
# 1. The decision model, with no app in front of it
# ---------------------------------------------------------------------------
from modules.commercial_builder import review_spec                      # noqa: E402


section("Three answers, and no-answer-yet is not a fourth kind of bad")
check("approved is offered", "approved" in review_spec.OUTCOME_KEYS, True)
# The middle one is what most sign-offs actually are. modules/ads_builder
# settled the same point for the paid-search estimate.
check("so is approved with changes",
      "approved_with_changes" in review_spec.OUTCOME_KEYS, True)
check("and changes required", "changes_required" in review_spec.OUTCOME_KEYS, True)
check("exactly three", len(review_spec.OUTCOME_KEYS), 3)
none = review_spec.verdict([])
check("nobody has answered is its own state", none["outcome"], "")
check("drawn gray, not red", none["color"], "gray")
check("and blocks nothing", none["blocks_filing"], False)
# "Not sent", "sent and ignored" and "they said no" are three situations.
check("and it is not a rejection", none["wants_another_round"], False)


section("The most restrictive answer wins, however many people replied")
mixed = review_spec.verdict([
    {"outcome": "approved", "reviewer_name": "Ann"},
    {"outcome": "changes_required", "reviewer_name": "Bob"},
    {"outcome": "approved_with_changes", "reviewer_name": "Cass"},
])
check("a refusal beats two approvals", mixed["outcome"], "changes_required")
check("and the panel is told they disagreed", mixed["conflicting"], True)
check("named as who raised it", mixed["by"], "Bob")
check("every answer is still counted", mixed["answered"], 3)
# Order must not decide it: a second reviewer's "looks good" arriving later
# cannot soften the first one's refusal.
reversed_order = review_spec.verdict([
    {"outcome": "changes_required", "reviewer_name": "Bob"},
    {"outcome": "approved", "reviewer_name": "Ann"},
])
check("recency does not decide it", reversed_order["outcome"], "changes_required")
softer = review_spec.verdict([{"outcome": "approved", "reviewer_name": "Ann"},
                              {"outcome": "approved_with_changes", "reviewer_name": "Cass"}])
check("with-changes beats a plain approval", softer["outcome"], "approved_with_changes")
check("one person agreeing with themselves is not a conflict",
      review_spec.verdict([{"outcome": "approved"}])["conflicting"], False)
# A row written before a key existed must not silently block a delivery.
check("an unknown answer is ignored, not treated as a refusal",
      review_spec.verdict([{"outcome": "sort_of"}])["outcome"], "")


section("Only a refusal blocks the delivery")
check("a refusal does", review_spec.verdict(
    [{"outcome": "changes_required"}])["blocks_filing"], True)
# Blocking this would teach people to answer "approved" to get past the gate.
check("approved with changes does not", review_spec.verdict(
    [{"outcome": "approved_with_changes"}])["blocks_filing"], False)
check("nor a plain approval", review_spec.verdict(
    [{"outcome": "approved"}])["blocks_filing"], False)


section("A timecode, or an honest absence of one")
check("seconds become a timecode", review_spec.timecode(12), "0:12")
check("past a minute too", review_spec.timecode(75), "1:15")
# A note about the music is not at a timestamp, and rendering it as 0:00 files
# every general comment at the first frame.
check("no point in the cut is empty, never 0:00", review_spec.timecode(None), "")
check("and so is nonsense", review_spec.timecode("banana"), "")
check("a comment keeps its own point",
      review_spec.clean_comment({"at_seconds": "12.456"})["at_seconds"], 12.46)
check("and a comment with none says so",
      review_spec.clean_comment({"text": "louder"})["at_seconds"], None)


section("Rounds are counted to four, and the fifth is flagged rather than refused")
check("round one is drawn against the cap",
      review_spec.round_state(1)["label"], "Round 1 of 4")
check("and nothing is said to the client that early",
      review_spec.round_state(1)["client_note"], "")
check("the last round says so, so they ask for everything at once",
      bool(review_spec.round_state(4)["client_note"]), True)
check("four is not over", review_spec.round_state(4)["over"], False)
check("five is", review_spec.round_state(5)["over"], True)
check("and it is a flag rather than a refusal",
      bool(review_spec.round_state(5)["note"]), True)


# ---------------------------------------------------------------------------
# 2. The app: both doors
# ---------------------------------------------------------------------------
import werkzeug.test                                                    # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402
from modules.commercial_builder.db import db                            # noqa: E402
from modules.commercial_builder.models import RenderJob                 # noqa: E402

staff = werkzeug.test.Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"]}, follow_redirects=True)
anon = werkzeug.test.Client(application)


def sj(path, body=None, method="post"):
    fn = getattr(staff, method)
    return fn(MOUNT + path, json=body if body is not None else {})


client_row = sj("/api/clients", {"name": "Acme Heating", "website": "acme.example"}).get_json()["client"]
pid = sj("/api/projects", {"client_id": client_row["id"], "lengths": [30],
                           "formats": ["16:9"], "commercial_type": "stock_vo",
                           "platform": "ctv"}).get_json()["projects"][0]["id"]


section("A link with nothing to watch is refused, not served empty")
nothing = sj(f"/api/projects/{pid}/reviews")
check("it is refused", nothing.status_code, 400)
# The client opens it, sees nothing, and the rep finds out days later.
check("and says to render first", "render" in nothing.get_json()["error"].lower(), True)

with hub_app.app_context():
    job = RenderJob(project_id=pid, format="16:9", status="succeeded",
                    output_url="https://example.test/spot.mp4")
    db.session.add(job)
    # A mock render reports success and produces no file. It must never reach
    # a client: an empty player is a page they cannot answer and cannot report.
    db.session.add(RenderJob(project_id=pid, format="9:16", status="succeeded",
                             output_url=""))
    db.session.commit()
    job_id = job.id

sent = sj(f"/api/projects/{pid}/reviews", {"message": "Here is the :30."}).get_json()["review"]
token = sent["token"]
check("a link is issued", bool(token), True)
check("on round one", sent["round_state"]["label"], "Round 1 of 4")
check("as an absolute URL somebody can paste",
      sent["url"].startswith("http") and "/review/" in sent["url"], True)


section("The client's page needs no login, and wears none of our chrome")
page = anon.get(f"{MOUNT}/review/{token}")
body = page.get_data(as_text=True)
check("it opens with no session at all", page.status_code, 200)
# Both halves. The login exemption is on the blueprint's own guard because
# wsgi.py's PUBLIC_PREFIXES never sees a blueprint-registered module; the
# chrome exemption is CHROMELESS in hub/__init__.py. Either one missing is a
# page the client cannot use or should not see.
check("no staff sidebar", "s1hub-sb" in body, False)
check("no help layer", "hub-help.js" in body, False)
check("no feedback tab", "s1hub-feedback" in body, False)
check("the cut is on it", "spot.mp4" in body, True)
check("and the round is said out loud", "Round 1 of 4" in body, True)
# Asserted on the markup rather than on any occurrence of the string: the
# page's own script carries a comment mentioning 9:16, and a raw-text match
# reports that as a rendered cut. hub/blog_spec.scan_forbidden() strips the
# HTML before matching for the same reason.
check("a render with no file is not offered", 'data-format="9:16"' in body, False)
check("and no cut tabs are drawn for one cut", 'id="cut-tabs"' in body, False)
# It is opened from an email on somebody else's machine, and it carries a
# client's unreleased creative.
check("and it asks not to be indexed", 'name="robots"' in body, True)

section("The staff half is not public, and the client half is not staff")
check("the review list needs a login",
      anon.get(f"{MOUNT}/api/projects/{pid}/reviews").status_code, 401)
check("so does issuing a link",
      anon.post(f"{MOUNT}/api/projects/{pid}/reviews", json={}).status_code, 401)
# The guard matches /review/ with its trailing slash, so a route that merely
# starts with the same letters is not accidentally public.
check("the wizard is still guarded",
      anon.get(f"{MOUNT}/project/{pid}/preview").status_code in (301, 302), True)
check("and so is the dashboard", anon.get(MOUNT + "/").status_code in (301, 302), True)

section("Revoked, deleted and never-existed all answer the same 404")
# A client-facing URL that says "this link expired" tells somebody probing
# which tokens are real. modules/ads_builder settled this for the estimate.
check("a token that never existed", anon.get(f"{MOUNT}/review/nope").status_code, 404)
check("and posting to one", anon.post(f"{MOUNT}/review/nope/decide",
                                      json={"outcome": "approved"}).status_code, 404)


section("An answer with nobody's name on it is refused")
check("no name at all", anon.post(f"{MOUNT}/review/{token}/decide",
                                  json={"outcome": "approved"}).status_code, 400)
check("a name but no email", anon.post(
    f"{MOUNT}/review/{token}/decide",
    json={"outcome": "approved", "name": "Ann"}).status_code, 400)
check("and an answer that is not one of the three", anon.post(
    f"{MOUNT}/review/{token}/decide",
    json={"outcome": "maybe", "name": "Ann", "email": "a@x.test"}).status_code, 400)
# A note is worth having from somebody who will not give an email; a sign-off
# is not, because it is the thing somebody is held to later.
check("a note needs a name too", anon.post(
    f"{MOUNT}/review/{token}/comment", json={"text": "louder"}).status_code, 400)
check("but not an email", anon.post(
    f"{MOUNT}/review/{token}/comment",
    json={"text": "louder", "name": "Bob"}).status_code, 200)


section("Two people answer one link, and the refusal stands")
first = anon.post(f"{MOUNT}/review/{token}/decide",
                  json={"outcome": "approved", "name": "Ann",
                        "email": "ann@acme.test"}).get_json()
check("the first answer is recorded", first["verdict"]["outcome"], "approved")
second = anon.post(f"{MOUNT}/review/{token}/decide",
                   json={"outcome": "changes_required", "name": "Bob",
                         "email": "bob@acme.test", "note": "Old phone number"}).get_json()
check("the second does not overwrite it", second["verdict"]["answered"], 2)
check("and the refusal is what stands", second["verdict"]["outcome"], "changes_required")
# A reviewer who pressed the wrong button must be able to correct it, and must
# not be able to correct a colleague.
again = anon.post(f"{MOUNT}/review/{token}/decide",
                  json={"outcome": "approved_with_changes", "name": "Bob",
                        "email": "bob@acme.test"}).get_json()
check("changing your own answer replaces it", again["verdict"]["answered"], 2)
check("and the verdict follows", again["verdict"]["outcome"], "approved_with_changes")


section("A comment carries the point in the cut it was left at")
made = anon.post(f"{MOUNT}/review/{token}/comment",
                 json={"text": "This number is our old one", "name": "Bob",
                       "at_seconds": 12.4, "format": "16:9"}).get_json()["comment"]
check("the timecode is rendered", made["timecode"], "0:12")
check("and the cut it was left on is kept", made["format"], "16:9")
whole = anon.post(f"{MOUNT}/review/{token}/comment",
                  json={"text": "Music is too loud", "name": "Bob"}).get_json()["comment"]
check("a note about the whole spot has no timecode", whole["timecode"], "")
check("and is not filed at the first frame", whole["at_seconds"], None)


section("A cut the client refused does not reach their library")
anon.post(f"{MOUNT}/review/{token}/decide",
          json={"outcome": "changes_required", "name": "Bob",
                "email": "bob@acme.test", "note": "Old phone number"})
blocked = staff.post(f"{MOUNT}/api/projects/{pid}/render-jobs/{job_id}/approve", json={})
check("filing is refused", blocked.status_code, 409)
check("and it names who asked", "Bob" in blocked.get_json()["error"], True)
# Not a wall. A rep who has settled it on the phone must not be stuck behind a
# rule the client has already moved past — but the override is recorded.
check("it says it can be overridden", blocked.get_json()["can_override"], True)
forced = staff.post(f"{MOUNT}/api/projects/{pid}/render-jobs/{job_id}/approve",
                    json={"override": True})
check("filing anyway works", forced.status_code, 200)
check("and is flagged as exactly that",
      forced.get_json()["filed_over_client_objection"], True)


section("A project nobody sent for review files exactly as it did before")
pid2 = sj("/api/projects", {"client_id": client_row["id"], "lengths": [15],
                            "formats": ["16:9"], "commercial_type": "stock_vo",
                            "platform": "ctv"}).get_json()["projects"][0]["id"]
with hub_app.app_context():
    j2 = RenderJob(project_id=pid2, format="16:9", status="succeeded",
                   output_url="https://example.test/fifteen.mp4")
    db.session.add(j2)
    db.session.commit()
    job2_id = j2.id
# Internal-only sign-off is still how most of these are built, and the gate
# must not have quietly made a review compulsory.
plain = staff.post(f"{MOUNT}/api/projects/{pid2}/render-jobs/{job2_id}/approve", json={})
check("it files", plain.status_code, 200)
check("with no client verdict claimed",
      plain.get_json()["client_verdict"]["outcome"], "")
check("and nothing flagged", plain.get_json()["filed_over_client_objection"], False)


section("A new round is a new link, and the old one stops answering")
round2 = sj(f"/api/projects/{pid}/reviews").get_json()["review"]
check("the round advances", round2["round_state"]["label"], "Round 2 of 4")
check("with a different token", round2["token"] != token, True)
# A client working from an old email must not be able to answer about a cut
# that has been replaced — and the old round's answers are still on file.
check("the old link stops answering",
      anon.get(f"{MOUNT}/review/{token}").status_code, 404)
listing = staff.get(f"{MOUNT}/api/projects/{pid}/reviews").get_json()
check("both rounds are kept", len(listing["reviews"]), 2)
check("round one's answers survive it",
      any(r["decisions"] for r in listing["reviews"]), True)
check("and the live round is the one reported as current",
      listing["current"]["token"], round2["token"])
check("which nobody has answered yet", listing["standing"]["outcome"], "")


section("A fifth round is flagged, and the client is served anyway")
for _ in range(3):
    sj(f"/api/projects/{pid}/reviews")
fifth = sj(f"/api/projects/{pid}/reviews").get_json()["review"]
check("it is issued", fifth["round_state"]["round"], 6)
check("and marked as past the cap", fifth["round_state"]["over"], True)
# Turning the client away is what pushes the whole conversation back into
# email, where none of this is recorded.
check("the client's page still opens",
      anon.get(f"{MOUNT}/review/{fifth['token']}").status_code, 200)
over_page = anon.get(f"{MOUNT}/review/{fifth['token']}").get_data(as_text=True)
check("and says nothing to them about rounds running long",
      "worth a conversation" in over_page, False)


section("Opening it is counted, because ignored and unopened are different")
before = staff.get(f"{MOUNT}/api/projects/{pid}/reviews").get_json()["current"]["opened_count"]
anon.get(f"{MOUNT}/review/{fifth['token']}")
after = staff.get(f"{MOUNT}/api/projects/{pid}/reviews").get_json()["current"]["opened_count"]
check("the count moves", after > before, True)


section("The rep's panel is on the screen the render is on")
preview = staff.get(f"{MOUNT}/project/{pid}/preview").get_data(as_text=True)
check("the panel is there", 'id="review-card"' in preview, True)
check("with somewhere to put the link", 'id="review-link"' in preview, True)
# There is no mail sender in the Hub. A panel implying we email it is a
# promise nothing here can keep — hub/user_directory.py made the same point
# about a forgotten-password form that flagged an admin nobody watches.
check("and it says the sending is yours to do",
      "no mail sender" in preview, True)
check("the bubble is placed", "commercial_builder.preview.review" in preview, True)
from hub import help as hub_help                                        # noqa: E402
check("and it resolves to content",
      bool(hub_help.get("commercial_builder.preview.review")), True)


section("An answer reaches somebody, because nothing here sends mail")
# A client answering used to reach the activity log and nothing else, so the
# rep found out by opening the spot and looking. `hub/social_content.py` made
# the same call: put it where people already look.
inbox = staff.get(f"{MOUNT}/api/reviews/waiting").get_json()
check("the inbox answers", inbox["ok"], True)
check("and says it measured something", inbox["measured"], True)
# `pid`'s live round is the sixth and nobody has answered it, so it is out
# with the client — and it is NOT read as handled by the earlier filing:
# a round sent after a cut was filed is a live question again, and reading
# it the other way drops exactly the round somebody is waiting on.
check("a round sent after a filing is not read as handled",
      pid in {r["project_id"] for r in inbox["out_with_clients"]}, True)
anon.post(f"{MOUNT}/review/{fifth['token']}/decide",
          json={"outcome": "changes_required", "name": "Eve",
                "email": "eve@acme.test", "note": "One more thing"})
inbox = staff.get(f"{MOUNT}/api/reviews/waiting").get_json()
waiting_ids = {r["project_id"] for r in inbox["waiting"]}
check("the spot the client answered is waiting", pid in waiting_ids, True)
# `pid2` was approved and filed with nobody asked, so it is not in either
# queue: it was never sent, and it has been acted on.
check("a spot nobody sent is not in the queue", pid2 in waiting_ids, False)
row = next(r for r in inbox["waiting"] if r["project_id"] == pid)
check("it names the client", row["client"], "Acme Heating")
check("and who to ring about it", row["by"], "Eve")
check("and what they said", row["outcome"], "changes_required")
check("and which round", row["round_no"] >= 1, True)
check("and opens the spot rather than a screen to filter",
      f"/project/{pid}/" in row["url"], True)
check("every count comes with the line that says what it means",
      bool(inbox["line"]), True)

# A round that is out and silent is waiting on the CLIENT, and counting it
# beside the answers is how a queue stops being read.
pid3 = sj("/api/projects", {"client_id": client_row["id"], "lengths": [30],
                            "formats": ["16:9"], "commercial_type": "stock_vo",
                            "platform": "ctv"}).get_json()["projects"][0]["id"]
with hub_app.app_context():
    j3 = RenderJob(project_id=pid3, format="16:9", status="succeeded",
                   output_url="https://example.test/third.mp4")
    db.session.add(j3)
    db.session.commit()
    job3_id = j3.id
sent3 = sj(f"/api/projects/{pid3}/reviews").get_json()["review"]
inbox = staff.get(f"{MOUNT}/api/reviews/waiting").get_json()
out_ids = {r["project_id"] for r in inbox["out_with_clients"]}
check("a silent round is with the client, not with us", pid3 in out_ids, True)
check("and not counted as an answer",
      pid3 not in {r["project_id"] for r in inbox["waiting"]}, True)

# A client who left notes and pressed no button has answered. Dropping them
# because no decision row exists loses exactly the reply somebody needed.
anon.post(f"{MOUNT}/review/{sent3['token']}/comment",
          json={"text": "The logo is cropped", "name": "Dee"})
inbox = staff.get(f"{MOUNT}/api/reviews/waiting").get_json()
notes_row = next((r for r in inbox["waiting"] if r["project_id"] == pid3), None)
check("notes with no decision still count as answered", bool(notes_row), True)
check("and the note count is carried", notes_row["comments"], 1)
check("with no outcome claimed for them", notes_row["outcome"], "")

# Filing is acting on it. A spot that has been filed leaves the queue.
staff.post(f"{MOUNT}/api/projects/{pid3}/render-jobs/{job3_id}/approve",
           json={"override": True})
inbox = staff.get(f"{MOUNT}/api/reviews/waiting").get_json()
check("a filed spot leaves the queue",
      pid3 in {r["project_id"] for r in inbox["waiting"]}, False)


section("The four empties, and only one of them is somebody's to fix")
check("nothing ever sent says so in words",
      review_spec.inbox([])["state"], "never_sent")
check("rather than reading as all quiet",
      "sent to a client" in review_spec.inbox([])["line"], True)
check("out with clients is its own state",
      review_spec.inbox([{"answered": 0, "comments": 0}])["state"], "out_with_clients")
check("and everything handled is another",
      review_spec.inbox([{"answered": 1, "filed": True}])["state"], "all_handled")
# "Nobody has answered" and "we could not look" are different answers and only
# the first means there is nothing to do.
unread = review_spec.inbox_unmeasured("the table would not answer")
check("a failed read is not a clean zero", unread["measured"], False)
check("and says what happened", "could not be read" in unread["line"], True)
check("without claiming a queue", unread["waiting_count"], 0)


section("The dashboard draws it, and the bubble resolves")
dash = staff.get(MOUNT + "/").get_data(as_text=True)
check("the card is on the dashboard", 'id="cb-review-inbox"' in dash, True)
check("it reads the route that exists",
      "/tools/commercial-builder/api/reviews/waiting" in dash, True)
check("the bubble is placed", "commercial_builder.dashboard.reviews" in dash, True)
check("and it resolves to content",
      bool(__import__("hub.help", fromlist=["help"]).get(
          "commercial_builder.dashboard.reviews")), True)
check("the inbox is staff-only", anon.get(f"{MOUNT}/api/reviews/waiting").status_code, 401)


section("The spec module is not shadowed by the route module")
# `__init__.py` does `from .routes import (..., review)`, which binds the name
# `review` ON THE PACKAGE. A spec module called `review.py` beside it is then
# invisible to `from . import review` in any sibling — nothing errors at
# import, and the first call to a function that is not there is where it
# surfaces. It cost the filing gate a 500 before the rename.
import modules.commercial_builder as _cb                                # noqa: E402
check("the spec answers to its own name", hasattr(_cb.review_spec, "verdict"), True)
check("and the package's `review` is the routes module",
      hasattr(_cb.review, "bp"), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
