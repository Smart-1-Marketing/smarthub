"""Radio Scripts — the client review link (build spec WO-3).

    python3 test_radio_review.py

Same shape as `test_commercial_review.py`, which this is ported from: no
pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one.

## Why this file exists

A rep generates three concepts and, until this, the only way a client heard
them was a phone read-through or a pasted script in an email — so nothing
recorded which concept the client picked, who at the client approved it, or
what they asked changed on the round before. That is fine until somebody
asks "did the client actually sign off on this wording", and there is
nothing to show them.

Each section below guards one way that goes quietly wrong:

  1. **A client-facing page behind a login is a login form in front of
     somebody with no account.** `modules/radio_scripts` is a BLUEPRINT on
     the hub app rather than a dispatcher-mounted one, so `wsgi.py`'s
     `PUBLIC_PREFIXES` — which does exactly this for `modules/ads_builder`
     and `modules/scans` — never sees it. Both halves (the login guard's
     `public=` and `hub/__init__.py`'s `CHROMELESS`) have to be written out
     separately, and a page exempted from the login but not from the chrome
     arrives at the client wearing the staff sidebar.

  2. **Two answers cannot express the answer most sign-offs actually are.**
     "Yes, but drop the discount line" forced into approve-or-reject goes to
     whichever end is nearest, and both are wrong.

  3. **A link gets forwarded, and then two people answer it.** Taking the
     latest answer lets a colleague's "sounds good" overwrite the first
     reviewer's refusal, after which the wrong script goes to production.

  4. **A comment has to be able to name which of nine scripts it is about**
     — a set carries three concepts at three lengths each, and "the :15 is
     too pushy" means nothing without knowing which :15.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1rsrev_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "rsrev-test-secret"
os.environ["PANEL_PASSWORD"] = "rsrev-test-password"
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")

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


MOUNT = "/tools/radio-scripts"


# ---------------------------------------------------------------------------
# 1. The decision model, with no app in front of it
# ---------------------------------------------------------------------------
from modules.radio_scripts import review_spec                           # noqa: E402


section("Three answers, and no-answer-yet is not a fourth kind of bad")
check("approved is offered", "approved" in review_spec.OUTCOME_KEYS, True)
check("so is approved with changes",
      "approved_with_changes" in review_spec.OUTCOME_KEYS, True)
check("and changes required", "changes_required" in review_spec.OUTCOME_KEYS, True)
check("exactly three", len(review_spec.OUTCOME_KEYS), 3)
none = review_spec.verdict([])
check("nobody has answered is its own state", none["outcome"], "")
check("drawn gray, not red", none["color"], "gray")
check("and it is not a rejection", none["wants_another_round"], False)


section("The most restrictive answer wins, however many people replied")
mixed = review_spec.verdict([
    {"outcome": "approved", "reviewer_name": "Ann"},
    {"outcome": "changes_required", "reviewer_name": "Bob"},
    {"outcome": "approved_with_changes", "reviewer_name": "Cass"},
])
check("changes required beats the other two", mixed["outcome"], "changes_required")
check("named by who raised it first", mixed["by"], "Bob")
check("and the disagreement is flagged", mixed["conflicting"], True)
agree = review_spec.verdict([{"outcome": "approved", "reviewer_name": "Ann"},
                             {"outcome": "approved", "reviewer_name": "Bob"}])
check("agreement is not flagged as conflicting", agree["conflicting"], False)
check("a row with no outcome key is ignored, not a refusal",
      review_spec.verdict([{"reviewer_name": "Ann"}])["outcome"], "")


section("A comment names which of nine scripts it is about")
scoped = review_spec.clean_comment({"text": "Too pushy", "name": "Ann",
                                    "concept_index": "1", "length_key": "30"})
check("the concept survives as an int", scoped["concept_index"], 1)
check("and the length as its string key", scoped["length_key"], "30")
check("Concept 2 in words", review_spec.concept_label(1), "Concept 2")
check(":30 in words", review_spec.length_label("30"), ":30")
whole_set = review_spec.clean_comment({"text": "Great overall", "name": "Ann"})
check("a general comment names no concept", whole_set["concept_index"], None)
check("no timecode-equivalent label for it", review_spec.concept_label(None), "")
check("an out-of-range index is clamped rather than trusted",
      review_spec.clean_comment({"text": "x", "concept_index": 99})["concept_index"], 2)
check("an unknown length key is dropped",
      review_spec.clean_comment({"text": "x", "length_key": "45"})["length_key"], "")


section("An empty text field never becomes a comment")
check("blank text is refused by the caller reading an empty string",
      review_spec.clean_comment({"name": "Ann"})["text"], "")


section("All three outcomes need a name; nothing else does")
check("approved needs one", review_spec.decision_requires_name("approved"), True)
check("changes_required needs one", review_spec.decision_requires_name("changes_required"), True)
check("an unrecognized answer needs nothing — is_outcome refuses it first",
      review_spec.decision_requires_name("maybe"), False)


# ---------------------------------------------------------------------------
# 2. The app: both doors
# ---------------------------------------------------------------------------
import werkzeug.test                                                    # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402
from modules.radio_scripts.db import db                                 # noqa: E402
from modules.radio_scripts.models import RadioScriptSet                 # noqa: E402
from modules.radio_scripts import api as rs_api                         # noqa: E402

staff = werkzeug.test.Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"]}, follow_redirects=True)
anon = werkzeug.test.Client(application)


def sj(path, body=None, method="post"):
    fn = getattr(staff, method)
    return fn(MOUNT + path, json=body if body is not None else {})


def _concept(idea="Idea one"):
    words60 = " ".join(["word"] * 150)
    words30 = " ".join(["word"] * 70)
    words15 = " ".join(["word"] * 37)
    return {"idea": idea, "talent_direction": "Warm, upbeat.",
            "sfx_notes": "", "scripts": {"60": words60, "30": words30, "15": words15},
            "tag": {"name": "Monogram Homes", "offer": "", "cta": "Call now",
                   "phone_spoken": "five one three", "url_spoken": "monogramhomes dot com"},
            "legal_line": ""}


CLEAN = {"concepts": [_concept(), _concept("Idea two"), _concept("Idea three")]}
BRIEF = {"company": "Monogram Homes", "client_name": "Monogram Homes",
        "market": "Cincinnati", "team": "Bengals", "local_shows": [],
        "offer": "", "phone": "", "url": "", "package": "", "funnel": {}}


def _make_call(*payloads):
    calls = list(payloads)

    def _call(payload, api_key):
        class _Resp:
            status_code = 200

            def json(self):
                return {"status": "completed",
                        "output": [{"content": [{"type": "output_text",
                                                 "text": calls.pop(0)}]}]}
        return _Resp()
    return _call


with hub_app.app_context():
    row = rs_api.create_set(BRIEF, call=_make_call(json.dumps(CLEAN)), actor="Test")
    set_id = row.id

    empty = RadioScriptSet(client_name="Nothing Yet", brief_json="{}",
                           concepts_json="[]", flags_json="[]", actor="Test")
    db.session.add(empty)
    db.session.commit()
    empty_id = empty.id


section("A link with nothing to review is refused, not served empty")
nothing = sj(f"/api/sets/{empty_id}/reviews")
check("it is refused", nothing.status_code, 400)
check("and says to generate first",
      "generate" in nothing.get_json()["error"].lower(), True)
check("a set that does not exist is refused the same way",
      sj("/api/sets/999999/reviews").status_code, 404)

sent = sj(f"/api/sets/{set_id}/reviews", {"message": "Here is the first pass."}).get_json()["review"]
token = sent["token"]
check("a link is issued", bool(token), True)
check("on round one", sent["round"], 1)
check("as an absolute URL somebody can paste",
      sent["url"].startswith("http") and "/review/" in sent["url"], True)


section("The client's page needs no login, and wears none of our chrome")
page = anon.get(f"{MOUNT}/review/{token}")
body = page.get_data(as_text=True)
check("it opens with no session at all", page.status_code, 200)
check("no staff sidebar", "s1hub-sb" in body, False)
check("no help layer", "hub-help.js" in body, False)
check("no feedback tab", "s1hub-feedback" in body, False)
# The consequence of being chrome-free, which had to be answered rather than
# accepted: hub-thinking.js rides in with the chrome, so a client pressing
# Send on a finished set would watch the button gray out and say nothing.
# hub/thinking.py inlines the mark instead.
check("but the mark that says something is running does reach it",
      ".s1w-mark{" in body and "window.S1Wait = {" in body, True)
check("the concept is on it", "Idea one" in body, True)
check("and the round is said out loud", "Round 1" in body, True)
check("and it asks not to be indexed", 'name="robots"' in body, True)


section("The staff half is not public, and the client half is not staff")
check("the review list needs a login",
      anon.get(f"{MOUNT}/api/sets/{set_id}/reviews").status_code, 401)
check("so does issuing a link",
      anon.post(f"{MOUNT}/api/sets/{set_id}/reviews", json={}).status_code, 401)
check("so does revoking one",
      anon.post(f"{MOUNT}/api/sets/{set_id}/reviews/{sent['id']}/revoke",
                json={}).status_code, 401)
check("the tool page itself is still guarded", anon.get(MOUNT + "/").status_code in (301, 302), True)


section("Revoked, deleted and never-existed all answer the same 404")
check("a token that never existed", anon.get(f"{MOUNT}/review/nope").status_code, 404)
check("and posting to one", anon.post(f"{MOUNT}/review/nope/decide",
                                      json={"outcome": "approved"}).status_code, 404)
check("and a comment to one", anon.post(f"{MOUNT}/review/nope/comment",
                                        json={"text": "x", "name": "A"}).status_code, 404)


section("An answer with nobody's name on it is refused")
check("no name at all", anon.post(f"{MOUNT}/review/{token}/decide",
                                  json={"outcome": "approved"}).status_code, 400)
check("a name but no email", anon.post(
    f"{MOUNT}/review/{token}/decide",
    json={"outcome": "approved", "name": "Ann"}).status_code, 400)
check("and an answer that is not one of the three", anon.post(
    f"{MOUNT}/review/{token}/decide",
    json={"outcome": "maybe", "name": "Ann", "email": "a@x.test"}).status_code, 400)
check("a note needs a name too", anon.post(
    f"{MOUNT}/review/{token}/comment", json={"text": "too pushy"}).status_code, 400)
check("but not an email", anon.post(
    f"{MOUNT}/review/{token}/comment",
    json={"text": "too pushy", "name": "Bob"}).status_code, 200)
check("and not any text at all", anon.post(
    f"{MOUNT}/review/{token}/comment", json={"name": "Bob"}).status_code, 400)


section("Two people answer one link, and the refusal stands")
first = anon.post(f"{MOUNT}/review/{token}/decide",
                  json={"outcome": "approved", "name": "Ann",
                        "email": "ann@acme.test"}).get_json()
check("the first answer is recorded", first["verdict"]["outcome"], "approved")
second = anon.post(f"{MOUNT}/review/{token}/decide",
                   json={"outcome": "changes_required", "name": "Bob",
                         "email": "bob@acme.test", "note": "Drop the discount line"}).get_json()
check("the second does not overwrite it", second["verdict"]["answered"], 2)
check("and the refusal is what stands", second["verdict"]["outcome"], "changes_required")
again = anon.post(f"{MOUNT}/review/{token}/decide",
                  json={"outcome": "approved_with_changes", "name": "Bob",
                        "email": "bob@acme.test"}).get_json()
check("changing your own answer replaces it", again["verdict"]["answered"], 2)
check("and the verdict follows", again["verdict"]["outcome"], "approved_with_changes")


section("A comment carries which script it was left against")
made = anon.post(f"{MOUNT}/review/{token}/comment",
                 json={"text": "Too pushy", "name": "Bob",
                       "concept_index": 1, "length_key": "15"}).get_json()["comment"]
check("the concept is named", made["concept_label"], "Concept 2")
check("and the length", made["length_label"], ":15")
general = anon.post(f"{MOUNT}/review/{token}/comment",
                    json={"text": "Great overall", "name": "Bob"}).get_json()["comment"]
check("a general note names neither", general["concept_label"], "")


section("A new round is a new link, and the old one stops answering")
round2 = sj(f"/api/sets/{set_id}/reviews").get_json()["review"]
check("the round advances", round2["round"], 2)
check("with a different token", round2["token"] != token, True)
check("the old link stops answering",
      anon.get(f"{MOUNT}/review/{token}").status_code, 404)
listing = staff.get(f"{MOUNT}/api/sets/{set_id}/reviews").get_json()
check("both rounds are kept", len(listing["reviews"]), 2)
check("round one's answers survive it",
      any(r["decisions"] for r in listing["reviews"]), True)
check("and the live round is the one reported as current",
      listing["current"]["token"], round2["token"])
check("which nobody has answered yet", listing["standing"]["outcome"], "")


section("Revoking keeps the record and switches off the link")
revoked = sj(f"/api/sets/{set_id}/reviews/{round2['id']}/revoke", method="post").get_json()
check("revoking is recorded", revoked["review"]["revoked"], True)
check("and the client's page 404s",
      anon.get(f"{MOUNT}/review/{round2['token']}").status_code, 404)


section("Opening it is counted, because ignored and unopened are different")
round3 = sj(f"/api/sets/{set_id}/reviews").get_json()["review"]
before = staff.get(f"{MOUNT}/api/sets/{set_id}/reviews").get_json()["current"]["opened_count"]
anon.get(f"{MOUNT}/review/{round3['token']}")
after = staff.get(f"{MOUNT}/api/sets/{set_id}/reviews").get_json()["current"]["opened_count"]
check("the count moves", after > before, True)


section("CHROMELESS names the review path in source, not just in behavior")
# The chrome-free body above proves the exemption works; this proves it is
# the *named* entry rather than an accident of some other rule matching —
# `hub/__init__.py`'s own CHROMELESS tuple is a local inside create_hub_app()
# and is not importable, so it is read from source the way
# test_blueprint_guards.py already reads hand-maintained inventories.
hub_src = (ROOT / "hub" / "__init__.py").read_text()
check("the review prefix is a literal in CHROMELESS",
      '"/tools/radio-scripts/review/"' in hub_src, True)


section("The review spec module is not shadowed by the routes module")
# modules/commercial_builder's own docstring names this trap: __init__.py
# doing `from . import review` binds `review` on the package, so a sibling's
# `from . import review` can silently resolve to the routes module instead
# of review_spec. Confirmed the two stay distinct here.
import modules.radio_scripts as rs_pkg                                  # noqa: E402
check("the package's own `review` name is the routes module",
      hasattr(rs_pkg.review, "attach"), True)
check("review_spec is reachable under its own name, not shadowed",
      hasattr(review_spec, "verdict"), True)


print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    sys.exit(1)
