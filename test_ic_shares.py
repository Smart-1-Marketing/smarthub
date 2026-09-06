"""The client review link — modules/image_creator/share_store.py and
review_spec.py, ported from the pattern modules/ads_builder and
modules/commercial_builder already use for theirs.

    python3 test_ic_shares.py

No pytest, no new dependencies, a throwaway SQLite database and its own data
directory.

Covers:

* token creation, and that a new round revokes the previous live one;
* the four approval states (approved / approved with changes / changes
  required / no answer yet) and that the most restrictive answer wins;
* the round cap flagging a fifth round rather than refusing the client;
* that PUBLIC_PREFIXES actually bypasses AuthGuard for /review/ and
  nothing else on this module's mount.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1icshares_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "ic-shares-test-secret")

from modules.image_creator import review_spec  # noqa: E402
from modules.image_creator import share_store as store  # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


print("\nclient review link")
print("-" * 46)

check("the store boots cleanly against a throwaway SQLite database",
     store.DB_BOOT_ERROR, "")

# --------------------------------------------------------------- minting
pid = "proj_abc123"
share1 = store.create_share(pid, created_by="Todd", message="Take a look",
                            variants=[{"label": "300x250", "url": "https://x/1.png"}])
check("a token is issued", len(share1["token"]) > 20, True)
check("round starts at 1", share1["round"], 1)
check("not revoked on creation", share1["revoked"], False)
check("the message is carried", share1["message"], "Take a look")
check("the variant list is stored on the round", share1["variants"][0]["url"], "https://x/1.png")

fetched = store.get_share(share1["token"])
check("get_share reads it back", fetched["project_id"], pid)

# ------------------------------------------------------------ a new round
share2 = store.create_share(pid, created_by="Todd", variants=[])
check("round increments", share2["round"], 2)
check("the new round is live", store.get_share(share2["token"])["revoked"], False)
check("the previous round is revoked -- a link that has been answered stays "
     "the record of that answer, and reusing it would overwrite it",
     store.get_share(share1["token"])["revoked"], True)

rows = store.list_shares(pid)
check("list_shares returns both rounds, newest first", [r["round"] for r in rows], [2, 1])

# ------------------------------------------------------- revoking by hand
share3 = store.create_share(pid, created_by="Todd")
ok = store.revoke_share(pid, share3["id"])
check("revoke_share works", ok, True)
check("revoking a share on the wrong project id is refused",
     store.revoke_share("some-other-project", share3["id"]), False)

# ----------------------------------------------------------- the 4 states
check("no answer yet is its own state, not a fourth kind of bad",
     review_spec.verdict([])["outcome"], review_spec.NO_ANSWER)
check("...and it draws gray", review_spec.verdict([])["color"], "gray")

approved = [{"outcome": "approved", "reviewer_name": "Alice"}]
check("a single approval resolves to approved", review_spec.verdict(approved)["outcome"], "approved")

conflicting = [{"outcome": "approved", "reviewer_name": "Alice"},
              {"outcome": "changes_required", "reviewer_name": "Bob"}]
v = review_spec.verdict(conflicting)
check("changes_required beats approved -- the most restrictive answer wins",
     v["outcome"], "changes_required")
check("...and it is reported as a conflict", v["conflicting"], True)
check("...naming the person who raised it", v["by"], "Bob")

approved_with_changes_then_approved = [
    {"outcome": "approved_with_changes", "reviewer_name": "Alice"},
    {"outcome": "approved", "reviewer_name": "Bob"}]
check("approved-with-changes beats a plain approval too",
     review_spec.verdict(approved_with_changes_then_approved)["outcome"],
     "approved_with_changes")

check("changes_required blocks filing", review_spec.verdict(conflicting)["blocks_filing"], True)
check("a plain approval does not block filing", review_spec.verdict(approved)["blocks_filing"], False)
check("approved_with_changes does not block filing either -- blocking it would "
     "teach people to answer 'approved' to get past the gate",
     review_spec.verdict([{"outcome": "approved_with_changes",
                          "reviewer_name": "A"}])["blocks_filing"], False)

check("a row from before these outcomes existed is ignored, not a refusal",
     review_spec.verdict([{"outcome": "some_old_value"}])["outcome"], review_spec.NO_ANSWER)

# -------------------------------------------------------------- decisions
pid2 = "proj_xyz789"
share = store.create_share(pid2, created_by="Todd", variants=[{"label": "A", "url": "https://x/a.png"}])
token = share["token"]

updated = store.record_decision(token, "approved_with_changes", "Alice", "alice@client.com", "Move the logo")
check("a decision is recorded", len(updated["decisions"]), 1)
check("...with the note", updated["decisions"][0]["note"], "Move the logo")

updated = store.record_decision(token, "approved", "Alice", "alice@client.com", "")
check("answering again from the same email REPLACES that reviewer's answer",
     len(updated["decisions"]), 1)
check("...with the new outcome", updated["decisions"][0]["outcome"], "approved")

updated = store.record_decision(token, "changes_required", "Bob", "bob@client.com", "Wrong phone number")
check("a second reviewer's answer is a second row, not an overwrite",
     len(updated["decisions"]), 2)
final = review_spec.verdict(updated["decisions"])
check("the resolved verdict is still the most restrictive of the two",
     final["outcome"], "changes_required")

# A comment on its own does not decide anything.
updated = store.add_comment(token, "Can we try a bigger logo?", "Carol", "carol@client.com")
check("a comment is recorded separately from a decision", len(updated["comments"]), 1)
check("...and decisions are untouched by it", len(updated["decisions"]), 2)

check("acting on a revoked or unknown token returns None, not a KeyError",
     store.record_decision("not-a-real-token", "approved", "X", "x@x.com"), None)
check("...same for a comment", store.add_comment("not-a-real-token", "hi", "X", "x@x.com"), None)

# ------------------------------------------------------------- open count
before = store.get_share(token)["opened_count"]
store.note_opened(token)
store.note_opened(token)
after = store.get_share(token)["opened_count"]
check("opening the link increments its own counter", after, before + 2)

# ------------------------------------------------------------- round cap
check("round 4 of 4 is not flagged as over", review_spec.round_state(4)["over"], False)
check("round 5 is flagged as over the cap", review_spec.round_state(5)["over"], True)
check("...but the client note only appears on the LAST scheduled round",
     review_spec.round_state(4)["client_note"] != "", True)
check("...not on round 1", review_spec.round_state(1)["client_note"], "")
check("a round over the cap is served exactly as before -- flagged, not refused",
     "note" in review_spec.round_state(5) and review_spec.round_state(5)["note"] != "", True)

# ------------------------------------------------------------------ inbox
rows = [
    {"answered": True, "comments": 0, "filed": False},   # waiting on us
    {"answered": False, "comments": 0, "filed": False},  # out with the client
    {"answered": False, "comments": 3, "filed": False},  # a comment IS an answer
    {"answered": True, "comments": 0, "filed": True},    # filed -- not waiting on anybody
]
inbox = review_spec.inbox(rows)
check("inbox splits waiting-on-us from out-with-clients", inbox["waiting_count"], 2)
check("...and a filed row drops out of both", inbox["out_count"], 1)
check("state reflects that something is waiting", inbox["state"], "waiting")

check("nothing ever sent is a different empty from nothing waiting",
     review_spec.inbox([])["state"], "never_sent")
check("a failed read is measured=False, never a confident zero",
     review_spec.inbox_unmeasured("boom")["measured"], False)

# ----------------------------------------------- PUBLIC_PREFIXES wiring
from modules.image_creator import app as ic_app  # noqa: E402
check("the module declares /review/ as its only public prefix",
     ic_app.PUBLIC_PREFIXES, ("/review/",))

import wsgi  # noqa: E402
wsgi_src = (ROOT / "wsgi.py").read_text()
check("wsgi.py reads PUBLIC_PREFIXES from the module rather than restating it",
     "_IMGCREATOR_PUBLIC = tuple(getattr(imgcreator" in wsgi_src, True)
check("...and hands it to the image-creator mount",
     'public_prefixes=_IMGCREATOR_PUBLIC) if imgcreator else imgcreator_fb' in wsgi_src, True)

guarded = wsgi.AuthGuard(ic_app.app, "/tools/image-creator",
                         public_prefixes=wsgi._IMGCREATOR_PUBLIC)
from werkzeug.test import Client  # noqa: E402
client = Client(guarded)

r = client.get(f"/review/{token}")
check("an anonymous GET to /review/<token> is NOT redirected to login",
     r.status_code, 200)

r = client.post(f"/review/{token}/decide", json={"outcome": "approved"})
check("an anonymous POST to /review/<token>/decide is NOT refused by AuthGuard",
     r.status_code in (200, 400), True)

r = client.get("/")
check("an anonymous GET to the staff editor on the SAME mount is redirected to login",
     r.status_code, 302)

r = client.get("/api/projects")
check("an anonymous GET to a staff API on the SAME mount is refused (401, JSON)",
     r.status_code, 401)

r = client.post("/api/projects/proj_abc123/reviews", json={})
check("an anonymous POST to the staff review-mint route is refused (401, JSON)",
     r.status_code, 401)

print("-" * 46)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
