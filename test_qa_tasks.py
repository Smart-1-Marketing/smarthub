"""QA Tasks: assigning a review, answering it, and getting the answer back.

    python3 test_qa_tasks.py

No pytest and no new dependencies, like every other test file here. It runs
against a temporary data directory **and** a throwaway SQLite database — both,
because `HUB_DATA_DIR` in front of an inherited `DATABASE_URL` is the one
combination `test_jsonstore.py` names: an empty disk in front of a full
mirror, which passes on the first run and fails on every run after.

## What is worth asserting here

The state machine, because it is the whole feature. Four states and two of
them are "somebody is waiting" — collapse those and the queue stops being able
to say whose move it is, which is the only question anybody opens it with.

The refusals, because each one is a way this becomes a record nobody can
trust: a stranger answering somebody else's task, an assignee closing their
own review, a shared-password session filing work under nobody's name.

And the wiring, because every one of these has cost a feature in this repo. A
blueprint that does not guard itself answers 200 to anyone with the URL. A
tool with no tile is invisible. A dropdown restated in a second list goes
stale the week a tool is added. A dashboard card that draws a zero over a
table it could not read is the confident wrong answer this codebase keeps
undoing.
"""
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="qa-tasks-test-")
os.environ["HUB_DATA_DIR"] = _TMP
# Set together, never one of the two: see the module docstring.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.sqlite3")
os.environ.setdefault("SECRET_KEY", "qa-tasks-test-secret-key")
os.environ.setdefault("ALLOWED_LOGIN_DOMAINS", "smart1marketing.com")

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}\n         got:  {got!r}\n         want: {want!r}")


def check_true(label, got):
    check(label, bool(got), True)


print("\nQA Tasks\n" + "=" * 60)

# ---------------------------------------------------------------------------
# The state machine and the refusals
# ---------------------------------------------------------------------------
from hub import create_hub_app                                   # noqa: E402
from hub.extensions import create_all, db                        # noqa: E402

app = create_hub_app()
create_all(app)

with app.app_context():
    from hub import qa_tasks
    from hub.users import User, _now

    def _account(email, name, role="member"):
        row = User.query.filter_by(email=email).first()
        if row is None:
            row = User(email=email, name=name, role=role, status="active",
                       password_hash="x", session_epoch=1, created_at=_now())
            db.session.add(row)
            db.session.commit()
        return row

    boss = _account("todd@smart1marketing.com", "Todd Swickard", "super_admin")
    rev = _account("reviewer@smart1marketing.com", "Reviewer Person")
    other = _account("bystander@smart1marketing.com", "Bystander Person")

    print("\n-- what can be reviewed --")
    groups = qa_tasks.targets()
    labels = [i["label"] for g in groups for i in g["items"]]
    keys = [i["key"] for g in groups for i in g["items"]]
    check_true("the catalog is not empty", len(labels) > 10)
    # Read from the nav and the tiles rather than restated: a hand-typed list
    # is a list that goes stale the week a tool is added.
    check_true("it carries a page from the nav", "Client 360" in labels)
    check_true("it carries a tool from a tile", any(
        "Image" in name for name in labels))
    # A dropdown that cannot hold the answer is worse than a text box.
    check("'something else' is last", groups[-1]["items"][0]["key"], "other")
    check("...and there is exactly one of it", keys.count("other"), 1)

    print("\n-- assigning --")
    try:
        qa_tasks.create(target_key="", target_other="", instructions="check it",
                        assigned_to_email=rev.email, due_on="",
                        actor_email=boss.email, actor_name=boss.name)
        check("a task with no target is refused", "created", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("a task with no target is refused", "Pick the" in str(exc))

    try:
        qa_tasks.create(target_key="other", target_other="A page",
                        instructions="", assigned_to_email=rev.email, due_on="",
                        actor_email=boss.email, actor_name=boss.name)
        check("a task with no instructions is refused", "created", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("a task with no instructions is refused",
                   "what you'd like" in str(exc))

    try:
        qa_tasks.create(target_key="other", target_other="A page",
                        instructions="look at it",
                        assigned_to_email="nobody@smart1marketing.com",
                        due_on="", actor_email=boss.email, actor_name=boss.name)
        check("an unknown assignee is refused", "created", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("an unknown assignee is refused", "active Hub account" in str(exc))

    # The need-by date is optional, which is what Todd asked for: required, it
    # gets filled in with a guess.
    task = qa_tasks.create(
        target_key="other", target_other="The proposal PDF",
        instructions="Run a real client through it and tell me what breaks.",
        assigned_to_email=rev.email, due_on="",
        actor_email=boss.email, actor_name=boss.name)
    check("a task with no need-by date is created", task.status, qa_tasks.OPEN)
    check("...and its need-by date is empty", task.due_on, None)
    # The date stamp Todd asked for.
    check_true("...and it is date-stamped", bool(task.created_at))

    dated = qa_tasks.create(
        target_key="other", target_other="The IO Builder",
        instructions="Build one end to end.", assigned_to_email=rev.email,
        due_on="2020-01-01", actor_email=boss.email, actor_name=boss.name)
    check_true("a need-by date is read", dated.due_on is not None)
    # Past the date, and not complete: overdue is derived, never stored, so it
    # cannot go stale the way a written flag would.
    check("a past need-by date reads as overdue", dated.overdue, True)

    print("\n-- answering --")
    try:
        qa_tasks.respond(task.id, body="not mine to answer",
                         actor_email=other.email, actor_name=other.name)
        check("a stranger cannot answer", "answered", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("a stranger cannot answer", "somebody else" in str(exc))

    try:
        qa_tasks.respond(task.id, body="", actor_email=rev.email,
                         actor_name=rev.name)
        check("an empty answer is refused", "answered", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("an empty answer is refused", "Write something" in str(exc))

    qa_tasks.respond(task.id, body="Step 4 loses the budget.",
                     actor_email=rev.email, actor_name=rev.name)
    db.session.refresh(task)
    check("an answer moves it to the assigner", task.status, qa_tasks.ANSWERED)

    # Which kind of post it is comes from who is posting, never from a flag
    # the caller passes: two callers deciding is two places the state machine
    # lives.
    qa_tasks.respond(task.id, body="Which budget field?",
                     actor_email=boss.email, actor_name=boss.name)
    db.session.refresh(task)
    check("asking for more moves it back", task.status, qa_tasks.NEEDS_MORE)

    print("\n-- attachments --")
    too_big = b"x" * (qa_tasks.MAX_ATTACHMENT_BYTES + 1)
    try:
        qa_tasks.respond(task.id, body="", actor_email=rev.email,
                         actor_name=rev.name, file_name="huge.bin",
                         file_bytes=too_big)
        check("an oversized attachment is refused", "stored", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("an oversized attachment is refused", "the limit is" in str(exc))

    post = qa_tasks.respond(task.id, body="", actor_email=rev.email,
                            actor_name=rev.name, file_name="findings.csv",
                            file_type="text/csv", file_bytes=b"a,b\n1,2\n")
    found = qa_tasks.attachment(post.id)
    check_true("an attachment reads back", found is not None)
    check("...with its own bytes", found[0], b"a,b\n1,2\n")
    check("...and its own name", found[1], "findings.csv")
    # A file with no text is a real answer: somebody sends a spreadsheet.
    db.session.refresh(task)
    check("a file-only answer still moves it", task.status, qa_tasks.ANSWERED)

    print("\n-- closing --")
    try:
        qa_tasks.complete(task.id, actor_email=rev.email)
        check("the reviewer cannot close their own review", "closed", "refused")
    except qa_tasks.QaTaskError as exc:
        check_true("the reviewer cannot close their own review",
                   "who raised this" in str(exc))

    qa_tasks.complete(task.id, actor_email=boss.email)
    db.session.refresh(task)
    check("the assigner closes it", task.status, qa_tasks.COMPLETE)
    check_true("...and it is stamped", bool(task.completed_at))

    # Nothing is deleted, ever: "what did we ask people to check before the
    # last release" has to be answerable a year from now.
    qa_tasks.reopen(task.id, actor_email=boss.email)
    db.session.refresh(task)
    check("reopening puts it back to the reviewer", task.status,
          qa_tasks.NEEDS_MORE)
    check("...and clears the completion stamp", task.completed_at, None)

    print("\n-- the two queues --")
    mine = qa_tasks.for_person(rev.email)
    check_true("the reviewer's list is measured", mine["measured"])
    todo = [t["target_label"] for t in mine["to_do"]]
    check_true("...and holds the reopened task", "The proposal PDF" in todo)
    check_true("...and the one nobody has answered", "The IO Builder" in todo)
    check("...and nothing is waiting on their reply",
          len(mine["waiting_on_you"]), 0)

    theirs = qa_tasks.for_person(boss.email)
    check("the assigner has nothing to do", len(theirs["to_do"]), 0)
    check_true("...and sees what they raised",
               len(theirs["raised_by_you"]) >= 2)

    # A session with no account behind it is told so rather than being shown
    # somebody's book on a name match.
    shared = qa_tasks.for_person("")
    check("a shared-password session is not measured", shared["measured"], False)
    check_true("...and says why", "no account" in shared["error"])

    print("\n-- new since you last looked --")
    fresh = qa_tasks.create(
        target_key="other", target_other="The dashboard",
        instructions="Does the QA card read right?",
        assigned_to_email=rev.email, due_on="",
        actor_email=boss.email, actor_name=boss.name)
    check("a new task is unread for the reviewer",
          fresh.unread_for(rev.email), True)
    # The person who raised it has, by definition, seen it.
    check("...and read for whoever raised it",
          fresh.unread_for(boss.email), False)
    qa_tasks.mark_seen(fresh.id, actor_email=rev.email)
    db.session.refresh(fresh)
    check("opening it clears the mark", fresh.unread_for(rev.email), False)

    print("\n-- the whole-team board --")
    board = qa_tasks.board()
    check_true("the board is measured", board["measured"])
    check_true("...and holds only open work",
               all(t["status"] != qa_tasks.COMPLETE for t in board["tasks"]))

    print("\n-- one sentence, every screen --")
    line = qa_tasks.summary_line({"to_do": 2, "overdue": 1, "waiting_on_you": 1})
    check_true("it names both queues",
               "2 reviews to do" in line and "1 answer" in line)
    check_true("...and the overdue count", "past the need-by date" in line)
    check("nothing waiting says so",
          qa_tasks.summary_line({"to_do": 0, "waiting_on_you": 0}),
          "Nothing is waiting on you.")

# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------
print("\n-- the login gate --")
# A blueprint on the hub app never passes through wsgi.py's AuthGuard, and the
# hub app has no blanket gate: this repo has paid for that four times.
client = app.test_client()
for path in ("/qa-tasks", "/api/qa-tasks", "/api/qa-tasks/board",
             "/api/qa-tasks/summary", "/api/qa-tasks/new"):
    resp = client.get(path)
    check(f"{path} refuses a stranger", resp.status_code in (301, 302, 401), True)
resp = client.post("/api/qa-tasks", json={})
check("POST /api/qa-tasks refuses a stranger", resp.status_code, 401)

print("\n-- the tile, the nav and the trail --")
from hub import qa as qa_reports                                 # noqa: E402
from hub.sidebar import visible_items                            # noqa: E402

tiles = {meta["href"]: meta["title"] for _g, _k, meta in qa_reports.EXTRAS}
check("it is tiled on QA Reports", tiles.get("/qa-tasks"), "QA Tasks")
nav = {row[1]: row[3] for row in visible_items(is_admin=False)}
# Anyone can assign, so a nav entry only admins see would put the queue behind
# the door the people answering it cannot open.
check("it is in the nav for a General account", nav.get("/qa-tasks"), "QA Tasks")

crumbs = (ROOT / "hub" / "static" / "hub-crumbs.js").read_text(encoding="utf-8")
check_true('the trail names it', '"qa-tasks": "QA Tasks"' in crumbs)
check_true('...and offers back the index it is tiled on',
           '"qa-tasks": ["/qa", "QA Reports"]' in crumbs)

print("\n-- the dashboard card --")
dash = (ROOT / "hub" / "templates" / "dashboard.html").read_text(encoding="utf-8")
check_true("the card is on the dashboard", 'id="qatasks"' in dash)
check_true("...and reads the shared summary", "/api/qa-tasks/summary" in dash)
# Todd asked for it above the recent activity list, and a card that draws a
# zero over a table it could not read is the confident wrong answer this
# codebase keeps undoing.
check("...above recent activity",
      dash.index('id="qatasks"') < dash.index('id="mini-activity"'), True)
check_true("...and branches on measured", "d.measured===false" in dash)

print("\n-- the sign-in reminder --")
nudge = (ROOT / "hub" / "static" / "hub-qa-nudge.js").read_text(encoding="utf-8")
check_true("it is loaded on hub pages",
           "/hub-qa-nudge.js" in (ROOT / "hub" / "templates" / "base.html")
           .read_text(encoding="utf-8"))
check_true("it is served from the root",
           "hub-qa-nudge.js" in (ROOT / "hub" / "help_routes.py")
           .read_text(encoding="utf-8"))
# A confetti cannon inside somebody else's panel is not a feature, and neither
# is a staff to-do list.
check_true("it is silent inside an iframe", "framed()" in nudge)
# Once a day, marked when SHOWN rather than when dismissed.
check_true("it marks itself when shown", "mark(key)" in nudge)
# A card that appears every morning to say there is nothing to do is a card
# people close without reading.
check_true("it says nothing when nothing is waiting",
           "if (!counts.to_do && !counts.waiting_on_you) { return; }" in nudge)
check_true("...and never draws a zero over a table it could not read",
           "d.measured === false" in nudge)

print("\n" + "=" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
