"""What a prospect on somebody else's website is told, from the pre-check to
the report.

    python3 test_scan_run.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

`test_scan_widgets.py` holds the placement admin — the tool a rep opens.
This holds the other half: the three pages a stranger meets on a client's own
site, and the run behind them. modules/scans is the second largest module in
the Hub and the most client-facing thing in it, and nothing exercised that
path. Booting it and pressing its buttons found four, and every one of them
answered a visitor confidently.

  1. **The callback token reached Insites unencoded.** `_callback_url()`
     interpolated `SCANS_CALLBACK_TOKEN` straight into a query string. A `+`
     comes back as a space and an `&` or `#` truncates it, so
     `compare_digest` fails and every callback is refused 403 — and
     `api_callback` deliberately leaves a refused row *running*, so the audit
     we paid for never attaches and nothing anywhere says why. The widget
     path also carried its own copy of those two lines, so the fix would have
     landed in two of the three places a scan is started.

  2. **Both poll loops read `ready` and never `status`.** A run at `error` or
     `unconfigured` will never become ready, and was polled to the ceiling
     and then told it was "still running" / "still working … open the link
     below and it will be there" — on the page a prospect is looking at. The
     status was on that response the whole time and nothing read it: the same
     shape as `campaign_assets`' warning computed and dropped on the way to
     the reader.

  3. **Two client-facing pages promised an email.** *"We'll email your report
     the moment it lands"* and *"open the link in your email when it lands"*.
     There is no mail sender in this Hub — CLAUDE.md says so five times — so
     that was a promise to a stranger that nothing here could keep, and it
     fired on every run that crossed ten minutes rather than only on failures.

  4. **A second unlock rewrote the contact on somebody else's run.** The lead
     is filed once, correctly guarded by `first_unlock` — and the four
     contact fields were written unconditionally beside an `unlocked_at` that
     deliberately was not. So a second post of the same token left the run row
     naming one person and carrying the lead id of another: the run row is the
     evidence of where that lead came from, read by the placement list and
     printed on the report page. Anybody holding the token can post it.

Each assertion below was confirmed red against the code as it stood.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1scanrun_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "scan-run-test-secret"
os.environ["PANEL_PASSWORD"] = "scan-run-test-password"
# The token is deliberately hostile: every character in it is legal in an
# environment variable and changes meaning inside a query string.
CALLBACK_TOKEN = "ab+cd&ef gh#ij"
os.environ["SCANS_CALLBACK_TOKEN"] = CALLBACK_TOKEN
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"

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

SRC = (ROOT / "modules" / "scans" / "app.py").read_text()
TEMPLATES = ROOT / "modules" / "scans" / "templates"


def precheck_result(score=42, n=6):
    """The shape widget.precheck really returns, so teaser() is exercised."""
    findings = [{"label": f"Finding {i}", "state": ("bad" if i % 2 else "good"),
                 "detail": f"detail {i}"} for i in range(n)]
    return {"domain": "example.com", "url": "https://example.com",
            "business": "Example Co", "score": score, "band": "bad",
            "headline": "Close to invisible", "findings": findings,
            "counts": {"good": n // 2, "bad": n - n // 2}}


class FakeLeads:
    """Stands in for hub.leads so nothing leaves the machine."""
    filed = []

    @staticmethod
    def capture_and_deliver(**kw):
        FakeLeads.filed.append(kw)
        return {"lead_id": f"lead-{len(FakeLeads.filed)}"}

    @staticmethod
    def rate_check(ip):
        return True, 0

    @staticmethod
    def client_ip(req):
        return "198.51.100.7"


STARTED = {}


def fake_start_audit(key, on_completion=None, **kw):
    STARTED["on_completion"] = on_completion
    return {"reportId": "insites-1", "status": "queued"}


scans_app.widget.precheck = lambda raw: precheck_result()
scans_app.insites_client.start_audit = fake_start_audit
scans_app.is_configured = lambda: True
scans_app.hub_leads = FakeLeads

client = scans_app.app.test_client()
Session = scans_app.SessionLocal


def run_row(token):
    db = Session()
    try:
        return db.query(widget_state.ScanRun).filter(
            widget_state.ScanRun.token == token).first()
    finally:
        db.close()


def set_scan_status(token, status, public_id=None):
    db = Session()
    try:
        row = db.query(widget_state.ScanRun).filter(
            widget_state.ScanRun.token == token).first()
        row.scan_status = status
        row.scan_public_id = public_id
        db.commit()
    finally:
        db.close()


client.post("/api/widgets", json={"name": "Acme home page", "kind": "aeo"})
client.post("/api/widgets", json={"name": "Acme audit", "kind": "audit"})


# =====================================================================
section("The callback token is a secret somebody typed, not a string we chose")
# =====================================================================

url = scans_app._callback_url("scan_abc")
token_back = parse_qs(urlparse(url).query).get("token") or [""]

check("the token survives the round trip into the callback URL",
      token_back[0], CALLBACK_TOKEN)
check("...which is what compare_digest is then handed",
      token_back[0] == scans_app.CALLBACK_TOKEN, True)
check("the raw token is not sitting in the query string",
      f"token={CALLBACK_TOKEN}" in url, False)
check("the encoded token is on the URL at all", "?token=" in url, True)

# Fails closed at the other end too: `api_callback` refuses every POST with no
# token configured, so the URL must carry no empty `?token=` inviting one.
_real = scans_app.CALLBACK_TOKEN
try:
    scans_app.CALLBACK_TOKEN = ""
    check("no token configured means no token on the URL, not an empty one",
          "token" in (scans_app._callback_url("scan_abc") or ""), False)
finally:
    scans_app.CALLBACK_TOKEN = _real

# No public base URL is local development and there is nowhere to be called
# back on, which is a different answer from a callback with no token on it.
_base = scans_app.PUBLIC_BASE_URL
try:
    scans_app.PUBLIC_BASE_URL = ""
    check("...and no public URL at all is None rather than a relative one",
          scans_app._callback_url("scan_abc"), None)
finally:
    scans_app.PUBLIC_BASE_URL = _base

# One reader. The widget path built the same two lines itself, which is how a
# fix to the line above would otherwise have covered two of three call sites.
builds = len(re.findall(r"scans/api/callback/\{", SRC))
check("only one place composes the callback URL", builds, 1)
check("the widget scan asks that one place for it",
      "on_completion = _callback_url(public_id)" in SRC, True)


# =====================================================================
section("The pre-check shows a teaser and withholds the rest")
# =====================================================================

r = client.post("/api/w/acme-home-page/check", json={"domain": "example.com"})
body = r.get_json()
token = body["token"]
check("the pre-check answers", r.status_code, 200)
check("three findings shown", len(body["result"]["findings"]), 3)
check("the rest are counted, not sent", body["result"]["locked_count"], 3)
check("nothing withheld is serialised into the page",
      "detail 4" in json.dumps(body), False)


# =====================================================================
section("A second unlock does not rewrite the first person's run")
# =====================================================================

first = client.post("/api/w/acme-home-page/unlock", json={
    "token": token, "name": "Bob Prospect", "email": "bob@example.com",
    "phone": "5551234567", "company": "Bob Co"})
check("the first unlock succeeds", first.status_code, 200)
check("one lead is filed", len(FakeLeads.filed), 1)
check("the run row names the person who unlocked it",
      run_row(token).name, "Bob Prospect")

second = client.post("/api/w/acme-home-page/unlock", json={
    "token": token, "name": "Eve Passerby", "email": "eve@example.com",
    "phone": "5559999999", "company": "Eve Co"})
check("a second unlock still returns the report", second.status_code, 200)
check("...and files no second lead", len(FakeLeads.filed), 1)
check("...and does not rename the run", run_row(token).name, "Bob Prospect")
check("...nor re-address it", run_row(token).email, "bob@example.com")
check("...nor re-company it", run_row(token).company, "Bob Co")
check("the lead the run points at is the one that was filed",
      run_row(token).lead_id, "lead-1")
check("the filed lead is the first person's",
      FakeLeads.filed[0]["fields"]["name"], "Bob Prospect")

# The audit placement is one step and each submission is its own row, so this
# question is only asked of the two-step widget.
check("the callback URL Insites was handed carries the real token",
      parse_qs(urlparse(STARTED["on_completion"]).query)["token"][0],
      CALLBACK_TOKEN)


# =====================================================================
section("A run that is over says so, rather than being polled to the ceiling")
# =====================================================================

check("the terminal statuses are named in one place",
      sorted(scans_app.STOPPED_SCAN_STATUSES), ["error", "unconfigured"])

for status in scans_app.STOPPED_SCAN_STATUSES:
    set_scan_status(token, status)
    j = client.get(f"/api/w/acme-home-page/status?token={token}").get_json()
    check(f"{status}: reported as stopped", j["stopped"], True)
    check(f"{status}: never as ready", j["ready"], False)
    check(f"{status}: carries the sentence the page shows", bool(j["message"]), True)
    check(f"{status}: offers no report link", j["report_url"], "")

for status in ("pending", "running", "queued"):
    set_scan_status(token, status)
    j = client.get(f"/api/w/acme-home-page/status?token={token}").get_json()
    check(f"{status}: is not stopped", j["stopped"], False)
    check(f"{status}: is not ready either", j["ready"], False)

# A scan that never started at all is waiting, not broken: `_start_widget_scan`
# writes `unconfigured` for that, and `pending` is a row on its way.
set_scan_status(token, "unconfigured")


# =====================================================================
section("Both pages read the answer, not half of it")
# =====================================================================

for name in ("widget.html", "widget_audit.html"):
    src = (TEMPLATES / name).read_text()
    check(f"{name} polls the status route", "'status'" in src or "/status" in src, True)
    check(f"{name} branches on stopped, not on ready alone",
          "j.stopped" in src, True)
    check(f"{name} prints the server's sentence rather than its own",
          "j.message" in src, True)
    check(f"{name} keeps no second list of which statuses are over",
          "unconfigured" in src, False)


# =====================================================================
section("Nothing client-facing promises an email this Hub cannot send")
# =====================================================================

# A sweep rather than the two that were wrong: a list of the pages we fixed
# proves nothing about the third. Every template this module serves to
# somebody with no Hub login is read.
PUBLIC_TEMPLATES = ["widget.html", "widget_audit.html", "widget_waiting.html",
                    "widget_report.html", "widget_audit_report.html"]
# The backslash is not decoration: the copy this was written against lives in
# a JS string literal as `We\'ll email`, and a class that did not allow one
# read straight past the sentence it was written to find.
# `inbox` is on the list because the copy that was live said "on its way to
# your inbox too" and none of the six alternatives here matched it: the sweep
# passed over a live promise for as long as it existed. A regex can only ever
# say "does this look like a promise"; the question that matters is whether
# the promise is BACKED, and that is asked below against hub/lead_tags.py.
PROMISE = re.compile(
    r"(we\\?[’']?ll email|we will email|email your report|in your email"
    r"|emailed to you|email it to you|inbox|we\\?[’']?ll send|will be sent to you"
    r"|sent to you|check your e-?mail|on its way to you)", re.I)

# Which source tag each public page's lead is captured under. A page may
# promise a message only if a Suite workflow is recorded for that tag in
# hub/lead_tags.py -- there is no mail sender here, so the workflow IS the
# sender, and a promise on a tag with none is one nothing can keep.
TEMPLATE_SOURCE = {
    "widget.html": "scan_widget",
    "widget_waiting.html": "scan_widget",
    "widget_report.html": "scan_widget",
    "widget_audit.html": "website_audit",
    "widget_audit_report.html": "website_audit",
}

_BLOCK_COMMENT = re.compile(r"<!--.*?-->|\{#.*?#\}|/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)


def copy_only(src: str) -> str:
    """What a visitor reads, with what a maintainer reads taken out.

    Prose is not a call site, for the sixth time in this repository — and the
    first run of this sweep proved it by reporting the comment *explaining*
    the fix as the defect it describes. Block comments and whole-line `//`
    ones go; a `//` mid-line is left alone, because that is a URL.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub(" ", src))


from hub import lead_tags                                          # noqa: E402

for name in PUBLIC_TEMPLATES:
    path = TEMPLATES / name
    check(f"{name} is a page this module actually serves", path.exists(), True)
    check(f"{name} names the tag its lead is captured under", name in TEMPLATE_SOURCE, True)
    source = TEMPLATE_SOURCE.get(name, "")
    check(f"...and that tag is in the registry", lead_tags.known(source), True)
    hit = PROMISE.search(copy_only(path.read_text()))
    if lead_tags.backed(source):
        # A workflow is recorded for this tag, so a promise here is one
        # something keeps. Nothing to refuse; say which workflow backs it.
        check(f"{name} may promise a message: {source} is backed by "
              f"{lead_tags.workflow_for(source)!r}", True, True)
    else:
        check(f"{name} promises no message, because no workflow is recorded "
              f"for the {source!r} tag",
              hit.group(0) if hit else None, None)

check("the sweep bites on the copy that was live",
      bool(PROMISE.search("we'll show the full report here, and it's on its "
                          "way to your inbox too.")), True)

check("a comment describing the promise is not the promise",
      PROMISE.search(copy_only(
          "/* we will email it: there is no mail sender here */")), None)
check("...and the sweep still reads the copy beside it",
      bool(PROMISE.search(copy_only(
          "/* a note */ <p>We\u2019ll email your report.</p>"))), True)

# Finding nothing is only meaningful if the sweep can find something.
check("the sweep bites on the copy that was there",
      bool(PROMISE.search("It is taking longer than usual. We'll email "
                          "your report the moment it lands.")), True)


# =====================================================================
section("The waiting page a converted lead lands on")
# =====================================================================

page = client.get(f"/r/{token}").get_data(as_text=True)
check("a stopped run is told the scan could not finish",
      "could not finish" in page, True)
check("...and is not shown a spinner", "Building your report" in page, False)
check("...and the page stops refreshing itself",
      'http-equiv="refresh"' in page, False)

set_scan_status(token, "running")
page = client.get(f"/r/{token}").get_data(as_text=True)
check("a run still going keeps the spinner", "Building your report" in page, True)
check("...and keeps refreshing", 'http-equiv="refresh"' in page, True)
check("...and points at this link rather than an inbox",
      "open it again" in page, True)


# =====================================================================
section("The three pages stay reachable with no Hub login")
# =====================================================================

for path in (f"/w/acme-home-page", f"/w/acme-audit", f"/r/{token}"):
    check(f"{path} answers a stranger", client.get(path).status_code, 200)
check("an invented token is refused", client.get("/r/deadbeef").status_code, 404)
check("...the same way a revoked one would be",
      client.get("/r/deadbeef").status_code,
      client.get("/r/" + "0" * 32).status_code)


# =====================================================================
print(f"\n{'=' * 60}\n{_passed} passed, {_failed} failed\n{'=' * 60}")
sys.exit(1 if _failed else 0)
