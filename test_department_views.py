"""Department Views: a curated shortlist per department, an overlay on top of
the sidebar rather than a second permission system.

    python3 test_department_views.py

Runs against a temporary data directory **and** a throwaway SQLite database,
both set together -- see `test_qa_tasks.py`'s docstring for why one without
the other is the one combination that quietly reads yesterday's rows.

What is worth asserting here: block validation refuses what would otherwise
be stored and shown to a whole department (a `javascript:` href, a blank
label, more than the cap); deleting a department actually un-assigns the
people on it rather than leaving a dangling id; a roster read that cannot
reach the account table says so rather than drawing an empty company; and the
wiring -- the editor is behind Utilities, reading your own view is not, and
the blueprint is actually guarded rather than answering 200 to a stranger.
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="dept-views-test-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.sqlite3")
os.environ.setdefault("SECRET_KEY", "dept-views-test-secret-key")
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


print("\nDepartment Views\n" + "=" * 60)

from hub import create_hub_app                                   # noqa: E402
from hub.extensions import create_all, db                        # noqa: E402

app = create_hub_app()
create_all(app)

# ---------------------------------------------------------------------------
# Access wiring -- the editor is Utilities, reading your own view is not
# ---------------------------------------------------------------------------
print("\n-- who the gate covers --")
from hub import access                                           # noqa: E402

check_true("the editor page is a Utilities path",
           access.is_utility("/views/manage"))
check_true("the admin API is a Utilities path",
           access.is_utility("/api/department-views/admin/list"))
check("reading your own view is NOT gated",
      access.is_utility("/views"), False)
check("browsing a colleague's view is NOT gated",
      access.is_utility("/views/creative"), False)
check("/api/department-views/mine is NOT gated",
      access.is_utility("/api/department-views/mine"), False)

print("\n-- the nav entries --")
from hub import sidebar                                          # noqa: E402

general_nav = [row[1] for row in sidebar.visible_items(is_admin=False)]
admin_nav = [row[1] for row in sidebar.visible_items(is_admin=True)]
check_true("My View is in the nav for everyone", "/views" in general_nav)
check("the editor is hidden from a General account",
      "/views/manage" in general_nav, False)
check_true("the editor is in the nav for an admin", "/views/manage" in admin_nav)

# ---------------------------------------------------------------------------
# The data layer
# ---------------------------------------------------------------------------
with app.app_context():
    from hub import department_views as dv
    from hub.users import User, _now

    def _account(email, name, role="member"):
        row = User.query.filter_by(email=email).first()
        if row is None:
            row = User(email=email, name=name, role=role, status="active",
                       password_hash="x", session_epoch=1, created_at=_now())
            db.session.add(row)
            db.session.commit()
        return row

    todd = _account("todd@smart1marketing.com", "Todd Swickard", "super_admin")
    rep = _account("rep@smart1marketing.com", "A Rep")

    print("\n-- creating a department --")
    sales = dv.create_department("Sales", "The pipeline and the leads.",
                                  actor_email=todd.email)
    check("the id is a slug", sales["id"], "sales")
    check("the blocks start empty", sales["blocks"], [])

    try:
        dv.create_department("   ")
        check_true("a blank name is refused", False)
    except dv.DepartmentViewError:
        check_true("a blank name is refused", True)

    creative = dv.create_department("Creative")
    check("a second department gets its own slug", creative["id"], "creative")

    print("\n-- block validation --")
    kept, dropped = dv.save_blocks(sales["id"], [
        {"type": "tile", "label": "Proposal Builder", "href": "/sales/builder/"},
        {"type": "tile", "label": "Steal my cookies", "href": "javascript:alert(1)"},
        {"type": "tile", "label": "", "href": "/sales/leads"},
        {"type": "note", "note": "Quotes over $10k need a second look."},
        {"type": "nonsense"},
    ])
    check("only the real tile and the note survive", len(kept), 2)
    check("the javascript:, blank-label and unknown-type blocks were dropped",
          dropped, 3)
    check_true("a relative href is kept as-is",
               any(b.get("href") == "/sales/builder/" for b in kept))

    over_cap = [{"type": "note", "note": f"note {i}"} for i in range(dv.MAX_BLOCKS + 10)]
    kept2, dropped2 = dv.save_blocks(sales["id"], over_cap)
    check("the block list is capped", len(kept2), dv.MAX_BLOCKS)
    check_true("the overflow is reported as dropped, not silently lost",
               dropped2 >= 10)

    print("\n-- assignment --")
    dv.set_assignment(rep.email, sales["id"], actor_email=todd.email)
    check("the rep is on Sales", dv.assignment_for(rep.email), sales["id"])
    counts = dv.assignment_counts()
    check("the count reflects it", counts.get(sales["id"]), 1)

    try:
        dv.set_assignment(rep.email, "does-not-exist")
        check_true("assigning to an unknown department is refused", False)
    except dv.DepartmentViewError:
        check_true("assigning to an unknown department is refused", True)

    print("\n-- deleting a department un-assigns its people --")
    unassigned = dv.delete_department(sales["id"], actor_email=todd.email)
    check("one person was un-assigned", unassigned, 1)
    check("the department is gone", dv.get_department(sales["id"]), None)
    check("the rep is no longer assigned anywhere",
          dv.assignment_for(rep.email), None)

    print("\n-- the roster reads real accounts --")
    rows, error = dv.roster()
    check("no error reading the roster", error, "")
    emails = {r["email"] for r in rows}
    check_true("todd is on it", todd.email in emails)
    check_true("the rep is on it", rep.email in emails)

    print("\n-- the catalog reuses QA Tasks' own picker --")
    groups = dv.catalog()
    check_true("it returns at least one group", len(groups) > 0)

    dv.delete_department(creative["id"], actor_email=todd.email)

# ---------------------------------------------------------------------------
# Routes -- nothing here answers a stranger
# ---------------------------------------------------------------------------
print("\n-- the routes --")
from hub import auth                                             # noqa: E402

anon = app.test_client()
check("/views redirects a stranger to sign in",
      anon.get("/views").status_code, 302)
check("/views/manage redirects a stranger to sign in",
      anon.get("/views/manage").status_code, 302)
check("the mine API refuses a stranger",
      anon.get("/api/department-views/mine").status_code, 401)
check("the admin API refuses a stranger",
      anon.get("/api/department-views/admin/list").status_code, 401)

signed = app.test_client()
signed.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Tester"))
check("My View renders once signed in", signed.get("/views").status_code, 200)
check("the editor renders once signed in",
      signed.get("/views/manage").status_code, 200)
body = signed.get("/api/department-views/mine").get_json()
check_true("the mine API answers ok", body.get("ok"))
check("nobody is assigned to this fresh session", body.get("department"), None)

with app.app_context():
    unread = dv.list_departments()
    check("no departments are left dangling from the test run", unread, [])

print(f"\n{'=' * 60}\n{_passed} passed, {_failed} failed\n")
sys.exit(1 if _failed else 0)
