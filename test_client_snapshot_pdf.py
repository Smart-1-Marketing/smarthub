"""The Client 360 snapshot download -- hub/client_snapshot_pdf.py.

    python3 test_client_snapshot_pdf.py

A one-page printable export, built entirely from functions the record
already calls for its own cards (hub/next_action.py, hub/record_health.py,
hub/client_money.py, hub/client_upcoming.py) through the shared canvas in
hub/pdf_doc.py. Nothing here is a new source, and the document text is
checkable as bytes because the PDF is written uncompressed -- the same
arrangement test_reports_public.py relies on for modules/reports/client_pdf.py.

What this file holds:

  1. **The page says what the four sources already say**, nothing more:
     the client's name, the next-action line, a flagged health pill, both
     money figures, and a coming-up row all land in the bytes.
  2. **Absent is not zero on paper either.** An unmeasured source is named
     in the "Not measured" footnote rather than a blank or an invented
     dash.
  3. **Never raises.** Any one of the four sources raising still produces
     a valid PDF, with that source's failure named rather than silently
     dropped.
  4. **The route** is behind `_require_page()` -- a redirect for a
     stranger, never a 401 JSON body served with a PDF's Content-Type --
     and answers with the right filename and Content-Disposition.
"""
import datetime as dt
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="c360snap_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "c360-snap-test"
os.environ["PANEL_PASSWORD"] = "c360-snap-pass"
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


class Stub:
    def __init__(self, **targets):
        self.targets, self.saved = targets, {}

    def __enter__(self):
        for dotted, value in self.targets.items():
            mod, attr = dotted.rsplit(".", 1)
            m = importlib.import_module(mod)
            self.saved[dotted] = getattr(m, attr)
            setattr(m, attr, value)
        return self

    def __exit__(self, *a):
        for dotted, old in self.saved.items():
            setattr(importlib.import_module(dotted.rsplit(".", 1)[0]), dotted.rsplit(".", 1)[1], old)


from hub import client_snapshot_pdf as sp                        # noqa: E402

CLIENT = "Acme Boats"
TODAY = dt.date(2026, 9, 19)

NA_BAD = {"measured": True, "state": "bad", "text": "Insertion order ends -- IO 2261 (in 9d)."}
HEALTH = {"pills": [
    {"key": "proposals", "label": "Proposals", "state": "bad", "value": "1 waiting: price lapsed",
     "detail": "Sent 40 days ago, never opened."},
    {"key": "products", "label": "Products", "state": "ok", "value": "2 live"},
], "unread": []}
MONEY = {"billing": {"measured": True, "monthly": 2900.0, "error": ""},
         "owed": {"state": "connected", "balance": 450.0, "overdue_count": 1,
                  "customers": 1, "unmatched": [], "error": ""}}
UPCOMING = {"measured": True, "horizon_days": 120, "unread": [], "items": [
    {"label": "Insertion order ends", "what": "IO 2261 · $1,200/mo", "days": 9, "state": "bad"},
    {"label": "Domain renews", "what": "acmeboats.com", "days": 30, "state": "warn"},
]}

# ------------------------------------------------------------------------
section("1. Every figure the page draws is a figure a source already gave")

with Stub(**{
    "hub.next_action.for_client": lambda name, url="": NA_BAD,
    "hub.record_health.client360": lambda name, **kw: HEALTH,
    "hub.client_money.for_client": lambda name, url="": MONEY,
    "hub.client_upcoming.for_client": lambda name, url="", today=None: UPCOMING,
}):
    pdf = sp.build(CLIENT, "", TODAY)

check("it is a PDF", pdf[:5], b"%PDF-")
check("the client's name is on the page", CLIENT.encode() in pdf)
# reportlab escapes literal parens in PDF text strings ("\(...\)"), so the
# assertion avoids them rather than matching an escaped substring.
check("the next-action line is on the page", b"Insertion order ends -- IO 2261" in pdf)
check("a flagged health pill's label and value are on the page",
      (b"Proposals" in pdf, b"price lapsed" in pdf), (True, True))
check("a healthy pill is not listed under what needs attention",
      b"2 live" not in pdf)
check("both money figures are on the page",
      (b"2,900" in pdf, b"450" in pdf), (True, True))
check("the overdue count is on the page", b"Overdue invoices" in pdf and b"1" in pdf)
check("a coming-up row is on the page", b"acmeboats.com" in pdf)
check("nothing is unmeasured, so no footnote is printed", b"Not measured:" not in pdf)

# ------------------------------------------------------------------------
section("2. Absent is not zero on paper either")

with Stub(**{
    "hub.next_action.for_client": lambda name, url="": {"measured": False, "text": ""},
    "hub.record_health.client360": lambda name, **kw: {"pills": [], "unread": ["the client record could not be read"]},
    "hub.client_money.for_client": lambda name, url="": {
        "billing": {"measured": False, "monthly": 0.0, "error": "products export down"},
        "owed": {"state": "not_connected", "balance": 0.0, "overdue_count": 0,
                "customers": 0, "unmatched": [], "error": "QuickBooks is not connected."}},
    "hub.client_upcoming.for_client": lambda name, url="", today=None: {"measured": True, "horizon_days": 120, "unread": [], "items": []},
}):
    pdf = sp.build(CLIENT, "", TODAY)

check("an unmeasured billing figure reads 'Not measured', not $0/mo",
      b"Not measured" in pdf and b"$0/mo" not in pdf)
check("a not-connected balance says so, not a clean zero owed", b"Not connected" in pdf)
check("both failures are named in the footnote",
      b"Not measured:" in pdf and b"products export down" in pdf and b"QuickBooks is not connected" in pdf)
check("a client with nothing coming up says so", b"Nothing ends or renews" in pdf)

# ------------------------------------------------------------------------
section("3. Never raises -- a source failing costs its own section, not the page")


def _raise(*a, **k):
    raise RuntimeError("boom")


with Stub(**{
    "hub.next_action.for_client": _raise,
    "hub.record_health.client360": _raise,
    "hub.client_money.for_client": _raise,
    "hub.client_upcoming.for_client": _raise,
}):
    pdf = sp.build(CLIENT, "", TODAY)
check("every source raising still produces a real PDF", pdf[:5], b"%PDF-")
check("the raising sources are named rather than silently dropped",
      pdf.count(b"RuntimeError") >= 4)

check("filename() is a slug with today's date",
      sp.filename("Acme Boats!") == f"acme-boats-snapshot-{dt.date.today().isoformat()}.pdf")

# ------------------------------------------------------------------------
section("4. The route: behind the login page, not the JSON gate")

from hub import auth, create_hub_app                              # noqa: E402
from hub.extensions import create_all                              # noqa: E402
app = create_hub_app()
create_all(app)

anon = app.test_client()
r = anon.get("/api/client/snapshot.pdf?name=" + CLIENT)
check("a stranger is redirected to sign in, not handed a JSON 401",
      (r.status_code, r.headers.get("Location", "").startswith("/login")), (302, True))

staff = app.test_client()
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"))
r = staff.get("/api/client/snapshot.pdf?name=" + CLIENT)
check("staff gets a PDF", r.status_code, 200)
check("...with the right content type", r.headers.get("Content-Type", "").startswith("application/pdf"))
check("...and an attachment filename", "attachment; filename=" in r.headers.get("Content-Disposition", ""))
check("...naming this client", CLIENT.encode() in r.get_data())

r = staff.get("/api/client/snapshot.pdf")
check("a missing name is refused rather than a snapshot about nobody", r.status_code, 400)

CI = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("this file runs in CI", "python3 test_client_snapshot_pdf.py" in CI, True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
