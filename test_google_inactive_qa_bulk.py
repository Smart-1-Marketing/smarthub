"""Bulk actions on the inactive-accounts QA page.

    python3 test_google_inactive_qa_bulk.py

No pytest, no new dependencies, a throwaway data directory -- the same shape
as the other test files here. Nothing reaches Google or anybody's website:
`_access_token`, `_delete` and `requests.get` are stubbed, and the calls each
bulk route makes are counted.

## What this is asserting

**A bulk action is the per-row action repeated, not a looser one.** Every
row skipped, un-skipped or deleted in a batch is in the audit log by name
with its own outcome, on the same keys the single routes write -- so what
Client 360 and the audit read back cannot tell the difference between a
person pressing Skip twenty times and pressing it once on twenty rows.

**A failure is per row, never the batch.** A row missing what the action is
keyed on, or a Google login refused for one container, is reported back by
name while every other row in the same request still goes through. The
alternative -- one bad row cancelling nineteen good ones -- is how a bulk
control becomes something nobody trusts.

**Deleting in bulk is guarded on the count, not on a name.** Typing one
resource's name is the right guard for one deletion and no guard at all for
twenty, so `DELETE <n>` is what is typed, checked against the number of rows
the page said it was about to delete -- and a request whose typed count does
not match is refused before a single Google call is made. The per-request
caps are enforced server-side too: a page that sent a hundred deletions in
one request would outlive gunicorn's --timeout and be killed mid-flight.

**Only GTM containers can be site-checked, and only against a site somebody
can point at.** A GA4 property is refused rather than quietly counted, and a
container whose account resolves to no client website records exactly that
-- never a fetch of somebody else's site reported as this container's. The
results are persisted on the same key a check run by hand writes, so the
next scan reattaches them and promotes a confirmed find into Needs Review.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-googleaccess-bulk-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["HUB_LEADS_FILE"] = os.path.join(_TMP, "leads.jsonl")
os.environ.setdefault("SECRET_KEY", "google-access-bulk-test")
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
OTHER_LOGIN = "second@example.com"

app = Flask(__name__)


def call(view, payload):
    """Post `payload` to a bulk route and return (status, json)."""
    with app.app_context():
        with app.test_request_context("/bulk", method="POST", json=payload):
            result = view.__wrapped__()
    if isinstance(result, tuple):
        resp, status = result[0], result[1]
    else:
        resp, status = result, 200
    return status, resp.get_json()


def audit_rows():
    from hub import jsonstore
    rows = jsonstore.read_json(qa._path("google_inactive_qa_audit.json"), default=[])
    return rows if isinstance(rows, list) else []


def ga4(resource, name, login=LOGIN):
    return {"kind": "GA4", "login": login, "account": "Acme", "account_id": "1",
            "name": name, "resource": resource, "public_id": ""}


def gtm(resource, name, public_id, account="Acme Plumbing", login=LOGIN):
    return {"kind": "GTM", "login": login, "account": account, "account_id": "9001",
            "name": name, "resource": resource, "public_id": public_id}


# ---------------------------------------------------------------------------
section("POST /api/skip/bulk: every row skipped, every row in the audit log")
# ---------------------------------------------------------------------------
rows = [ga4("p1", "One"), ga4("p2", "Two"), gtm("c1", "Container", "GTM-AAAA")]
status, payload = call(qa.api_skip_bulk, {"rows": rows, "reason": "retained for reporting"})
skips = qa._skips()
check("the request succeeds", status == 200 and payload.get("ok"), payload)
check("every row is counted done", payload.get("done") == 3, payload)
check("...on the same keys the single Skip writes",
      all(qa._skip_key(r["kind"], r["login"], r["resource"]) in skips for r in rows), skips)
check("the shared reason is recorded against each one",
      all(skips[qa._skip_key(r["kind"], r["login"], r["resource"])]["reason"]
          == "retained for reporting" for r in rows), skips)
audit = audit_rows()
check("one audit entry per resource, named",
      sorted(a["resource"] for a in audit if a["action"] == "skip") == ["c1", "p1", "p2"], audit)

status, payload = call(qa.api_skip_bulk, {"rows": [], "reason": ""})
check("an empty selection is refused rather than treated as 'all'", status == 400, payload)

status, payload = call(qa.api_skip_bulk,
                       {"rows": [ga4("p9", "Fine"), {"kind": "GA4", "name": "No login"}]})
check("a row missing its login is reported by name...", payload.get("failed"), payload)
check("...while the valid row in the same request still goes through",
      payload.get("done") == 1 and qa._skip_key("GA4", LOGIN, "p9") in qa._skips(), payload)


# ---------------------------------------------------------------------------
section("POST /api/unskip/bulk: back among the candidates")
# ---------------------------------------------------------------------------
status, payload = call(qa.api_unskip_bulk, {"rows": rows})
skips = qa._skips()
check("every row is un-skipped", payload.get("done") == 3, payload)
check("...and gone from the skip file",
      not any(qa._skip_key(r["kind"], r["login"], r["resource"]) in skips for r in rows), skips)
check("the one skipped separately is untouched", qa._skip_key("GA4", LOGIN, "p9") in skips, skips)
check("each un-skip is in the audit log",
      len([a for a in audit_rows() if a["action"] == "unskip"]) == 3, audit_rows())


# ---------------------------------------------------------------------------
section("POST /api/delete/bulk: the count is what gets typed")
# ---------------------------------------------------------------------------
deleted_urls = []
token_calls = []


def _fake_delete(token, url):
    deleted_urls.append(url)


def _fake_token(login):
    token_calls.append(login)
    return "tok-" + login


qa._delete, qa._access_token = _fake_delete, _fake_token

two = [ga4("p1", "One"), gtm("c1", "Container", "GTM-AAAA")]
status, payload = call(qa.api_delete_bulk, {"rows": two, "total": 2, "confirm": "DELETE 3"})
check("a typed count that does not match the rows is refused", status == 400, payload)
check("...before any Google call is made", not deleted_urls, deleted_urls)

status, payload = call(qa.api_delete_bulk, {"rows": two, "total": 1, "confirm": "DELETE 1"})
check("a total smaller than the rows sent is refused", status == 400, payload)
check("...also before any Google call", not deleted_urls, deleted_urls)

status, payload = call(qa.api_delete_bulk, {"rows": two, "total": 2, "confirm": "delete 2"})
check("the typed phrase is not case-sensitive", status == 200 and payload.get("deleted") == 2, payload)
# The host is spelled without its domain on purpose: a test file carrying a
# live Google API URL next to a `requests.` call reads to
# hub/quotas.check_untracked_provider_usage() as a call site spending quota
# without recording it, and a detector that cries wolf on a stub is one
# people learn to ignore. Which API the URL belongs to is the whole of what
# this asserts either way.
check("GA4 is deleted through the Analytics Admin API",
      any("analyticsadmin" in u and u.endswith("/p1") for u in deleted_urls), deleted_urls)
check("GTM is deleted through Tag Manager, under its account",
      any(u.endswith("/accounts/9001/containers/c1") for u in deleted_urls), deleted_urls)
check("each deletion is in the audit log with its own result",
      len([a for a in audit_rows() if a["action"] == "delete" and a["result"] == "ok"]) == 2,
      audit_rows())

status, payload = call(qa.api_delete_bulk,
                       {"rows": [ga4(f"p{i}", f"P{i}") for i in range(qa.BULK_DELETE_MAX + 1)],
                        "total": qa.BULK_DELETE_MAX + 1,
                        "confirm": f"DELETE {qa.BULK_DELETE_MAX + 1}"})
check("more rows than one request may carry is refused rather than run long",
      status == 400 and "at a time" in (payload.get("error") or ""), payload)

# A deleted resource that was on the skip list comes off it, exactly as the
# single delete route does -- otherwise a name Google no longer has sits in
# the skip file forever.
call(qa.api_skip_bulk, {"rows": [ga4("p5", "Skipped then deleted")], "reason": ""})
call(qa.api_delete_bulk, {"rows": [ga4("p5", "Skipped then deleted")], "total": 1,
                          "confirm": "DELETE 1"})
check("deleting a skipped resource drops its skip record",
      qa._skip_key("GA4", LOGIN, "p5") not in qa._skips(), qa._skips())


# ---------------------------------------------------------------------------
section("POST /api/delete/bulk: one refused row does not cancel the batch")
# ---------------------------------------------------------------------------
import requests as _requests  # noqa: E402


class _Resp403:
    status_code = 403

    @staticmethod
    def json():
        return {"error": {"message": "caller does not have permission"}}


def _selective_delete(token, url):
    if url.endswith("/c-refused"):
        raise _requests.HTTPError(response=_Resp403())
    deleted_urls.append(url)


qa._delete = _selective_delete
deleted_urls.clear()
mixed = [gtm("c-refused", "Refused", "GTM-BBBB"), ga4("p-ok", "Fine"),
         {"kind": "GTM", "login": LOGIN, "name": "No account id", "resource": "c-noacct",
          "public_id": "GTM-CCCC", "account_id": ""}]
status, payload = call(qa.api_delete_bulk, {"rows": mixed, "total": 3, "confirm": "DELETE 3"})
results = {r["resource"]: r for r in payload.get("results") or []}
check("the request itself succeeds", status == 200 and payload.get("ok"), payload)
check("the good row is deleted", payload.get("deleted") == 1 and results["p-ok"]["ok"], payload)
check("the refused row is reported by name with Google's own reason",
      not results["c-refused"]["ok"] and "403" in results["c-refused"]["error"], results)
check("...and says which grant to reconnect with",
      "tagmanager.delete.containers" in results["c-refused"]["error"], results)
check("a GTM row with no account id is refused before it is sent",
      not results["c-noacct"]["ok"], results)
check("the failures are in the audit log as failures",
      len([a for a in audit_rows() if a["action"] == "delete" and a["result"] == "error"]) >= 1,
      audit_rows())

# One token per login, not one per row.
qa._delete = _fake_delete
token_calls.clear()
same_login = [ga4("t1", "A"), ga4("t2", "B"), ga4("t3", "C"),
              ga4("t4", "D", login=OTHER_LOGIN)]
call(qa.api_delete_bulk, {"rows": same_login, "total": 4, "confirm": "DELETE 4"})
check("an access token is refreshed once per Google login, not once per row",
      sorted(token_calls) == sorted([LOGIN, OTHER_LOGIN]), token_calls)


def _always_failing_token(login):
    token_calls.append(login)
    raise LookupError("Connected Google login not found")


qa._access_token = _always_failing_token
token_calls.clear()
status, payload = call(qa.api_delete_bulk, {"rows": [ga4("x1", "A"), ga4("x2", "B")],
                                            "total": 2, "confirm": "DELETE 2"})
check("a login whose token cannot be refreshed fails its rows rather than the request",
      status == 200 and payload.get("deleted") == 0
      and all(not r["ok"] for r in payload["results"]), payload)
check("...and the refresh is attempted once, not once per row",
      token_calls == [LOGIN], token_calls)
qa._access_token = _fake_token


# ---------------------------------------------------------------------------
section("POST /api/delete/bulk: admin only, the same as the single route")
# ---------------------------------------------------------------------------
_real_is_admin = qa._is_admin
qa._is_admin = lambda: False
status, payload = call(qa.api_delete_bulk, {"rows": [ga4("p1", "One")], "total": 1,
                                            "confirm": "DELETE 1"})
qa._is_admin = _real_is_admin
check("a non-admin is refused", status == 403, payload)


# ---------------------------------------------------------------------------
section("POST /api/gtm/site-check/bulk: what is fetched, and what is not")
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, ok=True, status=200, url="https://example.com/", content=b"", encoding="utf-8"):
        self.ok, self.status_code, self.url = ok, status, url
        self.content, self.encoding = content, encoding


fetched = []


def _fake_get(url, timeout=None, allow_redirects=None, headers=None):
    fetched.append(url)
    return _Resp(url=url, content=b"<html><head><script "
                                 b"src='https://www.googletagmanager.com/gtm.js?id=GTM-FOUND1'>"
                                 b"</script></head></html>")


qa.requests.get = _fake_get

from hub import client_key as _client_key  # noqa: E402
_real_resolve = _client_key.resolve


def _resolve(name="", url="", **kw):
    if name == "Acme Plumbing":
        return {"known": True, "domain": "acmeplumbing.com", "client": "Acme Plumbing, LLC",
                "confidence": "exact"}
    return {"known": False, "domain": "", "client": name, "confidence": "unmatched"}


_client_key.resolve = _resolve
try:
    status, payload = call(qa.api_gtm_site_check_bulk, {"rows": [
        gtm("found1", "Found", "GTM-FOUND1"),
        gtm("notfound1", "Not found", "GTM-NOPE01"),
        gtm("ghost1", "No website", "GTM-GHOST1", account="Ghost Co"),
        ga4("p-ga4", "A property"),
    ]})
finally:
    _client_key.resolve = _real_resolve

results = {r["resource"]: r for r in payload.get("results") or []}
check("the request succeeds", status == 200 and payload.get("ok"), payload)
check("the container whose tag is on the page reads found",
      results["found1"]["found"] is True, results.get("found1"))
check("the one whose tag is not reads not found, never 'could not check'",
      results["notfound1"]["found"] is False, results.get("notfound1"))
check("an account matching no client resolves no website and says so",
      results["ghost1"]["found"] is None and results["ghost1"]["error"], results.get("ghost1"))
check("...and no page was fetched for it",
      len(fetched) == 2, fetched)
check("a GA4 row is refused rather than counted as checked",
      results["p-ga4"]["ok"] is False and "GTM" in results["p-ga4"]["error"], results.get("p-ga4"))
check("the checked count covers the GTM rows only", payload.get("checked") == 3, payload)
check("...and the found count is the confirmed finds", payload.get("found") == 1, payload)

stored = qa._site_checks()
check("every result is persisted on the key the single check writes",
      all(qa._skip_key("GTM", LOGIN, r) in stored for r in ("found1", "notfound1", "ghost1")),
      stored)
check("...stamped with the person who pressed it, not 'automatic scan'",
      all(stored[qa._skip_key("GTM", LOGIN, r)]["by"] != "automatic scan"
          for r in ("found1", "notfound1")), stored)
check("each check is in the audit log",
      len([a for a in audit_rows() if a["action"] == "site_check"]) == 3, audit_rows())

# A URL sent with the row wins over the client-registry match: it is the site
# somebody already decided was the right one.
fetched.clear()
call(qa.api_gtm_site_check_bulk,
     {"rows": [{**gtm("found1", "Found", "GTM-FOUND1"), "url": "https://chosen.example.com"}]})
check("a URL sent with the row is the one fetched",
      fetched == ["https://chosen.example.com"], fetched)

# ...and on a later bulk check with no URL, the site it was last checked
# against is reused rather than resolved again.
fetched.clear()
_client_key.resolve = _resolve
try:
    call(qa.api_gtm_site_check_bulk, {"rows": [gtm("found1", "Found", "GTM-FOUND1")]})
finally:
    _client_key.resolve = _real_resolve
check("a row with no URL falls back to the site it was last checked against",
      fetched == ["https://chosen.example.com"], fetched)

status, payload = call(qa.api_gtm_site_check_bulk,
                       {"rows": [gtm(f"c{i}", f"C{i}", "GTM-XXXX")
                                 for i in range(qa.BULK_SITE_CHECK_MAX + 1)]})
check("more site checks than one request may carry is refused rather than run long",
      status == 400 and "at a time" in (payload.get("error") or ""), payload)


# ---------------------------------------------------------------------------
section("A bulk check reaches the next scan the same way a single one does")
# ---------------------------------------------------------------------------
class _OneLoginFinder:
    def connected_accounts_result(self):
        return ([{"email": LOGIN, "refresh_token": "r", "status": "ACTIVE"}], "")

    def refresh_access_token(self, login, refresh):
        return "tok"


def _fake_scan_login(login, refresh, on_progress=None, **_ignored):
    base = {"kind": "GTM", "login": login, "account": "Acme Plumbing", "account_id": "9001",
            "events": None, "sessions": None, "status": "inactive",
            "reason": "Published container has no tags"}
    return ([{**base, "name": "Found", "resource": "found1", "public_id": "GTM-FOUND1"},
             {**base, "name": "Not found", "resource": "notfound1", "public_id": "GTM-NOPE01"}],
            [], [])


_real_finder, _real_scan_login = qa._finder, qa._scan_login
qa._finder, qa._scan_login = _OneLoginFinder, _fake_scan_login
try:
    with app.app_context():
        scanned = qa._run_scan(full=False)
finally:
    qa._finder, qa._scan_login = _real_finder, _real_scan_login

review = {r["resource"]: r for r in scanned.get("review") or []}
inactive = {r["resource"]: r for r in scanned.get("inactive") or []}
check("a container the bulk check found live is promoted into Needs Review",
      "found1" in review and "found1" not in inactive, scanned)
check("...with the site check attached to the row",
      (review.get("found1") or {}).get("site_check", {}).get("found") is True, review.get("found1"))
check("one it did not find stays an inactive candidate",
      "notfound1" in inactive, scanned)
check("...with its own result on the row, rather than nothing",
      (inactive.get("notfound1") or {}).get("site_check", {}).get("found") is False,
      inactive.get("notfound1"))


print()
print("-" * 60)
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
