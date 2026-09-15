"""GTM: default to inactive, and the direct on-page tag check.

    python3 test_google_inactive_qa_sitecheck.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway data directory, so it never touches /var/data or the real one.

## What this is asserting

**Inactive is the default for a GTM container now; confirmed GA4 activity
is the only override.** Whether the linked GA4 property is itself visible
to the login, readable, or separately flagged active/inactive is not a
gating condition any more -- a container with no positively-confirmed
traffic reads inactive, whatever the reason it could not be confirmed.
Needs Review is left for a scan that genuinely could not run.

**The site check is a direct answer to a different question**: is the
container's own tag actually on a real page, regardless of what (if
anything) is configured inside it. It fetches a page's raw HTML and looks
for the container's public ID -- no Google API involved, and no gating on
GA4 at all. It is a person's own per-container action, so a result is
persisted keyed on (login, resource) and reattached to the matching GTM
row on every later scan, rather than only showing up on the screen that
ran the check.

**The URL suggestion is a suggestion, never a decision.** It offers a
domain from an exact client-registry match on the GTM account's own name
-- never a substring, never auto-applied to anything -- and the person
checking still picks the URL that is actually fetched.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-googleaccess-sitecheck-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["HUB_LEADS_FILE"] = os.path.join(_TMP, "leads.jsonl")
os.environ.setdefault("SECRET_KEY", "google-access-sitecheck-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


from flask import Flask  # noqa: E402

from modules.google_access import qa_inactive as qa  # noqa: E402

LOGIN = "adops@example.com"


class _FakeFinder:
    def refresh_access_token(self, login, refresh):
        return "tok"


qa._finder = _FakeFinder

app = Flask(__name__)


def _scan(properties, activity, mids, gtm_tags):
    qa._ga_properties = lambda token: list(properties)
    qa._ga_activity = lambda token, pid: dict(activity.get(pid, {"events": 0, "sessions": 0}))
    qa._ga_measurement_ids = lambda token, pid: set(mids.get(pid, set()))
    qa._gtm_accounts = lambda token: [{"accountId": "9001", "name": "Tag Manager"}]
    qa._gtm_containers = lambda token, account_id: [
        {"containerId": "c1", "name": "Main site", "publicId": "GTM-XXXX"}]
    qa._live_tags = lambda token, account_id, container_id: (list(gtm_tags), "")
    return qa._scan_login(LOGIN, "refresh", cutoff_iso="", full=True)


def _find(rows, resource):
    return next((r for r in rows if r["resource"] == resource), None)


# ---------------------------------------------------------------------------
section("A GTM container with no GA4 tag at all reads inactive, not review")
# ---------------------------------------------------------------------------
inactive, review, active = _scan([], {}, {}, [{"parameter": [{"key": "other", "value": "x"}]}])
row = _find(inactive, "c1") or _find(review, "c1") or _find(active, "c1")
check("it landed in inactive", any(r["resource"] == "c1" for r in inactive), inactive)
check("...never in review", not any(r["resource"] == "c1" for r in review), review)
check("the reason says there was nothing to check", "no GA4 measurement ID" in (row or {}).get("reason", ""), row)


# ---------------------------------------------------------------------------
section("A GA4 tag whose property is not visible to this login reads inactive")
# ---------------------------------------------------------------------------
inactive2, review2, active2 = _scan(
    [], {}, {}, [{"parameter": [{"key": "measurementId", "value": "G-GHOST01"}]}])
check("it landed in inactive", any(r["resource"] == "c1" for r in inactive2), inactive2)
check("...never in review", not any(r["resource"] == "c1" for r in review2), review2)


# ---------------------------------------------------------------------------
section("A GA4 tag whose property could not be read (review) still reads GTM inactive")
# ---------------------------------------------------------------------------
def _erroring_activity(token, pid):
    raise __import__("requests").HTTPError()


qa._ga_activity = _erroring_activity
properties = [{"account": "Acme", "account_id": "1", "name": "acme.com", "property_id": "p1"}]
qa._ga_properties = lambda token: list(properties)
qa._ga_measurement_ids = lambda token, pid: {"G-ERR0001"}
qa._gtm_accounts = lambda token: [{"accountId": "9001", "name": "Tag Manager"}]
qa._gtm_containers = lambda token, account_id: [
    {"containerId": "c1", "name": "Main site", "publicId": "GTM-XXXX"}]
qa._live_tags = lambda token, account_id, container_id: (
    [{"parameter": [{"key": "measurementId", "value": "G-ERR0001"}]}], "")
inactive3, review3, active3 = qa._scan_login(LOGIN, "refresh", cutoff_iso="", full=True)
check("the GA4 property itself is in review (could not be read)",
      any(r["resource"] == "p1" for r in review3), review3)
check("the linked GTM container reads inactive anyway",
      any(r["resource"] == "c1" for r in inactive3), inactive3)
check("...never in review", not any(r["resource"] == "c1" for r in review3), review3)


# ---------------------------------------------------------------------------
section("Confirmed GA4 activity is still the one way in to active")
# ---------------------------------------------------------------------------
inactive4, review4, active4 = _scan(
    [{"account": "Acme", "account_id": "1", "name": "acme.com", "property_id": "p-live"}],
    {"p-live": {"events": 5, "sessions": 2}},
    {"p-live": {"G-LIVE0001"}},
    [{"parameter": [{"key": "measurementId", "value": "G-LIVE0001"}]}],
)
check("the container reads active", any(r["resource"] == "c1" for r in active4), active4)


# ---------------------------------------------------------------------------
section("A published container with genuine no-activity GA4 still reads inactive")
# ---------------------------------------------------------------------------
inactive5, review5, active5 = _scan(
    [{"account": "Acme", "account_id": "1", "name": "dead.example.com", "property_id": "p-dead"}],
    {"p-dead": {"events": 0, "sessions": 0}},
    {"p-dead": {"G-DEAD0001"}},
    [{"parameter": [{"key": "measurementId", "value": "G-DEAD0001"}]}],
)
check("the container reads inactive", any(r["resource"] == "c1" for r in inactive5), inactive5)


# ---------------------------------------------------------------------------
section("_fetch_page_html: found, not found, and could not fetch")
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, ok=True, status=200, url="https://example.com/", content=b"", encoding="utf-8"):
        self.ok, self.status_code, self.url = ok, status, url
        self.content, self.encoding = content, encoding


import requests as _requests  # noqa: E402


def _fake_get_found(url, timeout=None, allow_redirects=None, headers=None):
    return _Resp(content=b"<html><head><script src='https://www.googletagmanager.com/gtm.js?id=GTM-XXXX'></script></head></html>")


def _fake_get_notfound(url, timeout=None, allow_redirects=None, headers=None):
    return _Resp(content=b"<html><head></head><body>nothing here</body></html>")


def _fake_get_error(url, timeout=None, allow_redirects=None, headers=None):
    raise _requests.RequestException("boom")


qa.requests.get = _fake_get_found
got = qa._fetch_page_html("https://example.com")
check("a matching container id is read back as ok", got["ok"] and "GTM-XXXX" in got["html"], got)

qa.requests.get = _fake_get_notfound
got2 = qa._fetch_page_html("https://example.com")
check("a page with none of it still fetches cleanly", got2["ok"] and "GTM-XXXX" not in got2["html"], got2)

qa.requests.get = _fake_get_error
got3 = qa._fetch_page_html("https://example.com")
check("a network failure is reported rather than raised", not got3["ok"] and got3["error"], got3)


# ---------------------------------------------------------------------------
section("POST /api/gtm/site-check: found, not found, persisted, reattached on scan")
# ---------------------------------------------------------------------------
qa.requests.get = _fake_get_found
with app.app_context():
    with app.test_request_context(
            "/api/gtm/site-check", method="POST",
            json={"login": LOGIN, "resource": "c1", "account_id": "9001",
                  "public_id": "GTM-XXXX", "url": "example.com"}):
        resp = qa.api_gtm_site_check.__wrapped__()
    payload = resp.get_json()
check("found is True when the id is in the page", payload.get("found") is True, payload)
check("the scheme was filled in", payload.get("url", "").startswith("https://"), payload)

stored = qa._site_checks()
key = qa._skip_key("GTM", LOGIN, "c1")
check("the result is persisted keyed on login+resource", key in stored, stored)

qa.requests.get = _fake_get_notfound
with app.app_context():
    with app.test_request_context(
            "/api/gtm/site-check", method="POST",
            json={"login": LOGIN, "resource": "c1", "account_id": "9001",
                  "public_id": "GTM-XXXX", "url": "https://example.com"}):
        resp2 = qa.api_gtm_site_check.__wrapped__()
    payload2 = resp2.get_json()
check("a second check overwrites rather than accumulating", payload2.get("found") is False, payload2)
check("...and the store reflects the newer result",
      qa._site_checks()[key]["found"] is False, qa._site_checks()[key])

# A scan run after the check re-attaches the persisted result to the
# matching GTM row -- the whole point of persisting it rather than only
# updating the one screen that ran the check. _with_site_check is applied
# inside _run_scan() itself, so this drives the real thing end to end
# (with _scan_login faked out) rather than calling _scan_login directly.
class _OneLoginFinder:
    def connected_accounts_result(self):
        return ([{"email": LOGIN, "refresh_token": "r", "status": "ACTIVE"}], "")


def _fake_scan_login_no_site_info(login, refresh, on_progress=None, **_ignored):
    row = {"kind": "GTM", "login": login, "account": "Tag Manager", "account_id": "9001",
           "name": "Main site", "resource": "c1", "public_id": "GTM-XXXX",
           "events": None, "sessions": None, "reason": "Published container has no tags",
           "status": "inactive"}
    return [row], [], []


_real_finder2, _real_scan_login2 = qa._finder, qa._scan_login
qa._finder, qa._scan_login = _OneLoginFinder, _fake_scan_login_no_site_info
try:
    with app.app_context():
        payload6 = qa._run_scan(full=False)
finally:
    qa._finder, qa._scan_login = _real_finder2, _real_scan_login2
row6 = _find(payload6.get("inactive") or [], "c1")
check("the persisted site_check reattaches to the GTM row on the next scan",
      row6 is not None and row6.get("site_check", {}).get("found") is False, row6)

with app.app_context():
    with app.test_request_context(
            "/api/gtm/site-check", method="POST", json={"login": LOGIN, "resource": "c1"}):
        result3 = qa.api_gtm_site_check.__wrapped__()
status3 = result3[1] if isinstance(result3, tuple) else result3.status_code
check("missing public_id/url is refused, not guessed at", status3 == 400, result3)


# ---------------------------------------------------------------------------
section("POST /api/gtm/resolve-url: a suggestion, never a decision")
# ---------------------------------------------------------------------------
class _FakeClientKey:
    @staticmethod
    def resolve(name="", url="", **kw):
        if name == "Acme Plumbing":
            return {"known": True, "domain": "acmeplumbing.com", "client": "Acme Plumbing, LLC",
                     "confidence": "exact"}
        return {"known": False, "domain": "", "client": name, "confidence": "unmatched"}


from hub import client_key as _real_client_key  # noqa: E402
_real_resolve = _real_client_key.resolve
_real_client_key.resolve = _FakeClientKey.resolve
try:
    with app.app_context():
        with app.test_request_context(
                "/api/gtm/resolve-url", method="POST", json={"account": "Acme Plumbing"}):
            r1 = qa.api_gtm_resolve_url.__wrapped__()
        d1 = r1.get_json()
        with app.test_request_context(
                "/api/gtm/resolve-url", method="POST", json={"account": "Nobody Knows This Business"}):
            r2 = qa.api_gtm_resolve_url.__wrapped__()
        d2 = r2.get_json()
finally:
    _real_client_key.resolve = _real_resolve

check("an exact registry match suggests that client's domain",
      d1.get("known") and d1.get("suggested_url") == "https://acmeplumbing.com", d1)
check("an unmatched account name suggests nothing",
      not d2.get("known") and not d2.get("suggested_url"), d2)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
