"""Scan Widgets as a tool of its own: the lead count, and pause/edit/delete.

    python3 test_scan_widgets.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

The widget maker was reachable only from inside Site Scans, and once a
placement existed the page said nothing about whether it had ever produced
anything. It now has a tile on /tools under Landing Pages, a Leads column, and
the three actions a list of placements is useless without. Every failure below
is one where a screen would go on looking healthy:

  1. **A check is not a lead.** A public box on somebody's home page is typed
     into by passers-by. The number that matters is the visitor who handed over
     a name, business, email and phone — the same moment the row is written to
     hub/leads. Counting runs would report a placement converting nobody as the
     best one we have.

  2. **A count that cannot be read must not read as nought.** "Nobody has used
     this placement" is the finding somebody deletes a placement over, so a
     database that would not answer says *not measured* on the page and blocks
     the delete.

  3. **A slug re-used is a different placement.** Delete one and create another
     at the same address — the embed code is identical three lines — and the
     old one's leads would otherwise be counted against the new one.

  4. **Deleting a placement must not delete the people.** The leads are real
     prospects already in the Leads panel; this page has no business removing
     the evidence of where they came from.

  5. **An edit edits.** Upserting on whatever the name slugified to meant a
     second placement called the same thing silently replaced the first — same
     address, new headline, and the only sign was a list that did not grow.

  6. **The address never changes.** It is in the embed code already pasted on a
     client's site.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1widget_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "widget-test-secret"
os.environ["PANEL_PASSWORD"] = "widget-test-password"

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


from modules.scans import app as scans_app                    # noqa: E402
from modules.scans import leads as widget_state               # noqa: E402

client = scans_app.app.test_client()
Session = scans_app.SessionLocal


def now():
    return datetime.now(timezone.utc)


def make_placement(slug, name, created=None, active=1):
    db = Session()
    try:
        db.add(widget_state.ScanWidget(
            slug=slug, name=name, tag=slug, active=active,
            created_at=created or (now() - timedelta(days=30))))
        db.commit()
    finally:
        db.close()


def make_run(slug, *, unlocked=False, lead_id="lead-1", created=None):
    """One row of the widget's own state. Unlocked means a lead was captured."""
    db = Session()
    try:
        db.add(widget_state.ScanRun(
            token=widget_state.new_token(), widget_slug=slug, tag=slug,
            domain="example.com", created_at=created or (now() - timedelta(days=1)),
            unlocked_at=(created or now()) if unlocked else None,
            lead_id=lead_id if unlocked else None))
        db.commit()
    finally:
        db.close()


def stats_for(slug):
    db = Session()
    try:
        rows = (db.query(widget_state.ScanWidget)
                  .filter(widget_state.ScanWidget.slug == slug).all())
        stats, err = widget_state.placement_stats_result(db, rows)
        return (stats or {}).get(slug), err
    finally:
        db.close()


# =====================================================================
section("A check is not a lead")
# =====================================================================

make_placement("acme-home", "Acme home page")
for _ in range(4):
    make_run("acme-home")                      # ran the free check, stopped
make_run("acme-home", unlocked=True)
make_run("acme-home", unlocked=True)

st, err = stats_for("acme-home")
check("no error counting a placement", err, "")
check("leads counts the visitors who identified themselves", st["leads"], 2)
check("checks counts everyone who ran it", st["checks"], 6)
check("filed counts the ones hub.leads gave an id", st["filed"], 2)
check("last lead is dated", bool(st["last_lead"]), True)

# _capture_lead writes "" when hub.leads answers without an id. That lead is a
# real person sitting in this table and in nobody's panel, so it is counted
# apart rather than averaged into the total.
make_run("acme-home", unlocked=True, lead_id="")
st, _ = stats_for("acme-home")
check("a lead that never reached the panel still counts as a lead", st["leads"], 3)
check("and is named as unfiled", st["unfiled"], 1)

page = client.get("/widgets")
body = page.get_data(as_text=True)
check("the placements page renders", page.status_code, 200)
check("the lead count is on it", "Acme home page" in body, True)


# =====================================================================
section("A count that could not be read is not nought")
# =====================================================================

class _DeadSession:
    def query(self, *a, **k):
        raise RuntimeError("database is away")


db = Session()
try:
    rows = db.query(widget_state.ScanWidget).all()
finally:
    db.close()
stats, err = widget_state.placement_stats_result(_DeadSession(), rows)
check("a failed read returns no stats at all", stats, None)
check("and says why", "database is away" in err, True)

_real = widget_state.placement_stats_result
widget_state.placement_stats_result = lambda s, p: (None, "database is away")
try:
    body = client.get("/widgets").get_data(as_text=True)
    check("the page says not measured rather than 0", "not measured" in body, True)
    check("and explains itself at the top",
          "couldn&#39;t count" in body or "couldn't count" in body, True)
    # Deleting on a count nobody could read is a decision made on nothing.
    r = client.delete("/api/widgets/acme-home",
                      json={"confirm": True})
    check("delete is refused while the count is unreadable", r.status_code, 503)
finally:
    widget_state.placement_stats_result = _real


# =====================================================================
section("Pause, and what it leaves alone")
# =====================================================================

r = client.post("/api/widgets/acme-home/toggle")
check("pause answers ok", r.status_code, 200)
check("and the placement is paused", r.get_json()["widget"]["active"], False)
check("a paused placement stops serving its page",
      client.get("/w/acme-home").status_code, 404)
st, _ = stats_for("acme-home")
check("pausing keeps every lead it captured", st["leads"], 3)

r = client.post("/api/widgets/acme-home/toggle")
check("resume puts it back", r.get_json()["widget"]["active"], True)
check("and the page answers again", client.get("/w/acme-home").status_code, 200)


# =====================================================================
section("Edit edits; it does not make a second placement")
# =====================================================================

r = client.post("/api/widgets", json={"name": "Acme home page (v2)",
                                      "slug": "acme-home",
                                      "headline": "Can AI find Acme?"})
check("editing answers ok", r.status_code, 200)
w = r.get_json()["widget"]
check("the name changed", w["name"], "Acme home page (v2)")
check("the address did not", w["slug"], "acme-home")

db = Session()
try:
    count = (db.query(widget_state.ScanWidget)
               .filter(widget_state.ScanWidget.slug == "acme-home").count())
    total = db.query(widget_state.ScanWidget).count()
finally:
    db.close()
check("still one row at that address", count, 1)
check("and no second placement was created", total, 1)

r = client.post("/api/widgets", json={"name": "Nothing here", "slug": "ghost"})
check("editing a placement that is gone is refused", r.status_code, 404)

# The old upsert-on-name would have replaced the live placement with this one:
# "Acme home" is what /scans/w/acme-home is already at.
r = client.post("/api/widgets", json={"name": "Acme home"})
check("a new placement whose name collides is refused", r.status_code, 409)
check("and the refusal names the placement holding the address",
      "Acme home page (v2)" in (r.get_json().get("error") or ""), True)
st, _ = stats_for("acme-home")
check("the live placement is untouched by the refusal", st["leads"], 3)


# =====================================================================
section("Delete asks once, and keeps the leads")
# =====================================================================

r = client.delete("/api/widgets/acme-home", json={})
check("a placement with leads is refused first", r.status_code, 409)
j = r.get_json()
check("the refusal names the count", j["leads"], 3)
check("and asks for a confirmation", j["confirm_required"], True)

r = client.delete("/api/widgets/acme-home", json={"confirm": True})
check("confirmed, it deletes", r.status_code, 200)
check("and says what it kept", r.get_json()["leads_kept"], 3)

db = Session()
try:
    gone = (db.query(widget_state.ScanWidget)
              .filter(widget_state.ScanWidget.slug == "acme-home").count())
    runs = (db.query(widget_state.ScanRun)
              .filter(widget_state.ScanRun.widget_slug == "acme-home").count())
finally:
    db.close()
check("the placement is gone", gone, 0)
check("every run it produced is still there", runs, 7)
check("its page stops answering", client.get("/w/acme-home").status_code, 404)


# =====================================================================
section("A re-used address is a different placement")
# =====================================================================

# Same three lines of embed code, so this is the ordinary way it happens: the
# placement is deleted and re-made rather than edited.
r = client.post("/api/widgets", json={"name": "Acme home"})
check("the address can be used again", r.status_code, 200)
check("and it is the same address", r.get_json()["widget"]["slug"], "acme-home")

st, err = stats_for("acme-home")
check("the new placement starts at no leads", st["leads"], 0)
check("and at no checks", st["checks"], 0)

make_run("acme-home", unlocked=True, created=now())
st, _ = stats_for("acme-home")
check("counting only what it has captured itself", st["leads"], 1)


# =====================================================================
section("It is a tool on the Tools page, in Landing Pages")
# =====================================================================

tools_html = (ROOT / "hub" / "templates" / "tools.html").read_text()
start = tools_html.find('<div class="qa-group-label">Landing Pages</div>')
nxt = tools_html.find('<div class="qa-group-label">', start + 10)
tile = tools_html.find('href="/scans/widgets"')
check("the Landing Pages group exists", start > -1, True)
check("the tile is on the page", tile > -1, True)
check("and it is inside that group", start < tile < nxt, True)

# The tile is the only thing that makes a tool visible; CLAUDE.md counts six
# that were invisible for weeks for want of one.
check("the tile is named", "<h3>Scan Widgets</h3>" in tools_html, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
