"""The Smart 1 Suite control panel: the guard on creating, and the record of deleting.

    python3 test_suite_panel.py

Same shape as test_io_builder.py: no pytest, no new dependencies, a temporary
data directory and its own audit log, so it never touches /var/data or the
real one. Nothing here reaches GoHighLevel — every call is stubbed.

## Why this file exists

This module creates and deletes clients' GoHighLevel sub-accounts and had no
test of its own. Booting it and pressing its buttons found three, and the
caller got a clean answer every time.

  1. **The double-submit guard worked on one worker in two.** `_idem` was a
     dict in memory and gunicorn runs two workers, so a resubmitted key that
     landed on the worker which had not seen the first one found nothing
     cached and **created a second sub-account** — the `_state`-is-per-process
     trap CLAUDE.md names for the scheduler, on the route where it costs a
     duplicate client account and cannot be undone from this panel.

  2. **And it was written after the work, so it never covered a double-click
     at all.** `idem_get` read at the top and `idem_set` wrote once the
     account existed, so two requests arriving together both found nothing and
     both created one — which is exactly the shape a double-submit is. The key
     is claimed *before* the work now, with `O_EXCL` on the shared disk so the
     claim is atomic between workers.

  3. **The duplicate check could fail and say nothing.** GHL 500s on
     `/locations/search`, the route logged a warning and returned a clean 201,
     so a rep could not tell "there is no account of this name" from "we could
     not look" — the `connected_accounts_result()` rule, on the one check that
     exists to stop a client getting two accounts. Worse, the confirm-and-
     resubmit path switches the check off, so on the retry both guards were
     down at once.

  4. **The record of a deletion was whatever the caller typed.** The activity
     entry carried `?name=` from the query string, never checked against the
     account being deleted: delete `loc_9` while passing another company's
     name and that is what the log said, and omitting the parameter recorded
     an empty one. It is the only record that the deletion happened.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1suite_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "suite-panel-test-secret"
os.environ["GHL_PRIVATE_TOKEN"] = "pit-test-token"
os.environ["GHL_COMPANY_ID"] = "co_test"

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


import modules.suite_panel.app as sp                                # noqa: E402
from hub import audit                                              # noqa: E402

client = sp.app.test_client()
SRC = (ROOT / "modules" / "suite_panel" / "app.py").read_text()


class Resp:
    """Enough of a requests.Response for the module's `ghl()` to read."""

    def __init__(self, code, body):
        self.status_code, self._b = code, body
        self.text, self.ok = json.dumps(body), code < 400

    def json(self):
        return self._b


CALLS = []
SEARCH = {"mode": "empty"}
LOCATION = {"name": "Icon Solar Supply"}


def stub(method, url, **kw):
    CALLS.append((method, url))
    if "/locations/search" in url:
        if SEARCH["mode"] == "down":
            return Resp(500, {"message": "GHL is having a moment"})
        if SEARCH["mode"] == "match":
            return Resp(200, {"locations": [{"id": "loc_old", "name": "Icon Solar"}]})
        return Resp(200, {"locations": []})
    if url.endswith("/locations/") and method == "POST":
        return Resp(201, {"id": "loc_new"})
    if method == "DELETE":
        return Resp(200, {"deleted": True})
    if "/locations/" in url and method == "GET":
        if LOCATION is None:
            return Resp(404, {"message": "no such location"})
        return Resp(200, dict(LOCATION))
    return Resp(200, {"ok": True})


sp.requests.request = stub


def creates():
    return [c for c in CALLS if c[0] == "POST" and c[1].endswith("/locations/")]


def deleted_entries():
    return [e for e in audit.tail(limit=200, module="suite")
            if e.get("type") == "account_deleted"]


# ---------------------------------------------------------------------------
section("The claim is taken before the work, not written after it")
# ---------------------------------------------------------------------------
CALLS.clear()
SEARCH["mode"] = "empty"
BODY = {"name": "Icon Solar", "idempotencyKey": "key-one"}

first = client.post("/api/locations", json=BODY)
check("the first submission creates the account", first.status_code, 201)
check("  and made exactly one create call", len(creates()), 1)

CALLS.clear()
again = client.post("/api/locations", json=BODY)
check("the same key replays the answer", again.status_code, 201)
check("  and creates nothing a second time", creates(), [])
check("  with the same location, not a new one",
      again.get_json().get("locationId"), first.get_json().get("locationId"))

# The failure that was live: gunicorn runs two workers, and the one that never
# saw the first submit held an empty dict. The claim is a file on the shared
# data disk, so wiping this process's memory changes nothing.
sp._idem.clear()
CALLS.clear()
other_worker = client.post("/api/locations", json=BODY)
check("a worker that never saw the first submit still replays it",
      other_worker.status_code, 201)
check("  and does not create a second sub-account for the client", creates(), [])

# A double-click is two requests in flight at once, which is the case the old
# write-after-the-work guard could not see at all.
CALLS.clear()
sp.idem_claim("key-two")                       # the twin, mid-flight
inflight = client.post("/api/locations",
                       json={"name": "Buckeye Marina", "idempotencyKey": "key-two"})
check("a submission whose twin is still running is refused",
      inflight.status_code, 409)
check("  by name, so the screen can say what happened",
      inflight.get_json().get("error"), "in_progress")
check("  and creates nothing", creates(), [])

# A create that failed must not hold the key for five minutes: the rep's next
# press is a new attempt, not a duplicate of one that never happened.
CALLS.clear()
sp.requests.request = lambda m, u, **k: Resp(500, {"message": "GHL fell over"})
dead = client.post("/api/locations", json={"name": "Fell Over Ltd",
                                           "idempotencyKey": "key-three"})
check("a create that failed is reported", dead.status_code, 500)
sp.requests.request = stub
CALLS.clear()
retry = client.post("/api/locations", json={"name": "Fell Over Ltd",
                                            "idempotencyKey": "key-three"})
check("  and the next press is a fresh attempt rather than a replay",
      retry.status_code == 201 and len(creates()) == 1, True)

# A request refused before anything happened must not burn the key either.
CALLS.clear()
blank = client.post("/api/locations", json={"name": "  ", "idempotencyKey": "key-four"})
check("a nameless request is refused", blank.status_code, 400)
named = client.post("/api/locations", json={"name": "Named At Last",
                                            "idempotencyKey": "key-four"})
check("  and the same key then works, because nothing had happened",
      named.status_code, 201)


# ---------------------------------------------------------------------------
section('"No duplicate" and "we could not look" are different answers')
# ---------------------------------------------------------------------------
CALLS.clear()
SEARCH["mode"] = "empty"
clear = client.post("/api/locations", json={"name": "Clear Co",
                                            "idempotencyKey": "dup-clear"})
check("a check that ran and found nothing says so",
      clear.get_json().get("duplicateCheck"), "clear")
check("  and carries no warning", "duplicateCheckWarning" in clear.get_json(), False)

SEARCH["mode"] = "down"
CALLS.clear()
unmeasured = client.post("/api/locations", json={"name": "Blind Co",
                                                 "idempotencyKey": "dup-blind"})
check("a check that could not run still creates the account",
      unmeasured.status_code, 201)
check("  but is named as not measured rather than as clear",
      unmeasured.get_json().get("duplicateCheck"), "not_measured")
check("  in the answer, not only in a log nobody is reading",
      "could not run" in (unmeasured.get_json().get("duplicateCheckWarning") or ""))
check("  and the activity entry records which kind of check it got",
      [e.get("duplicateCheck") for e in audit.tail(limit=200, module="suite")
       if e.get("type") == "account_created" and e.get("name") == "Blind Co"],
      ["not_measured"])

SEARCH["mode"] = "match"
CALLS.clear()
dup = client.post("/api/locations", json={"name": "Icon Solar",
                                          "idempotencyKey": "dup-hit"})
check("a real duplicate is refused", dup.status_code, 409)
check("  and creates nothing", creates(), [])
check("  naming the account that already exists",
      (dup.get_json().get("duplicates") or [{}])[0].get("id"), "loc_old")
# The rep confirms and resubmits, which is the same key: a refusal must not
# have claimed it, or the confirmed submission replays the refusal for ever.
confirmed = client.post("/api/locations", json={"name": "Icon Solar",
                                                "confirmDuplicate": True,
                                                "idempotencyKey": "dup-hit"})
check("  and confirming goes through rather than replaying the refusal",
      confirmed.status_code, 201)
check("  with the skipped check said out loud",
      confirmed.get_json().get("duplicateCheck"), "skipped")


# ---------------------------------------------------------------------------
section("A deletion is recorded against the account it deleted")
# ---------------------------------------------------------------------------
SEARCH["mode"] = "empty"
r = client.delete("/api/locations/loc_9?name=Totally+Different+Ltd")
check("the deletion goes through", r.status_code, 200)
entry = deleted_entries()[0]
check("  and the record carries the name GHL holds",
      entry.get("name"), "Icon Solar Supply")
check("  never the one the caller typed",
      entry.get("claimedName"), "Totally Different Ltd")
check("  saying which of the two it is",
      entry.get("nameSource"), "confirmed")
check("  and the answer tells the screen the same thing",
      r.get_json().get("name"), "Icon Solar Supply")

# A read we could not make does not stop the deletion -- GHL is the authority
# on whether it can happen -- but the name is then not a fact.
LOCATION = None
r = client.delete("/api/locations/loc_10?name=Guessed+Name+Ltd")
check("an account we could not read is still deleted", r.status_code, 200)
entry = deleted_entries()[0]
check("  and the record says the name was not confirmed",
      entry.get("nameSource"), "not confirmed")
check("  rather than recording the claim as fact", entry.get("name"), None)
LOCATION = {"name": "Icon Solar Supply"}

# A deletion that did not happen is not a deletion.
_before = len(deleted_entries())
sp.requests.request = lambda m, u, **k: (Resp(200, dict(LOCATION)) if m == "GET"
                                         else Resp(500, {"message": "refused"}))
r = client.delete("/api/locations/loc_11")
check("a refused deletion is reported", r.status_code, 500)
check("  and is not written down as one", len(deleted_entries()), _before)
sp.requests.request = stub


# ---------------------------------------------------------------------------
section("The guard cannot quietly go back to being per-process")
# ---------------------------------------------------------------------------
check("the claim is taken before the work rather than written after it",
      SRC.index("state, rec = idem_claim(idem_key)")
      < SRC.index('location = ghl("/locations/", method="POST"'))
check("and it is on the shared data disk, not only in this process",
      "jsonstore.data_dir(" in SRC and "O_EXCL" in SRC)
check("the in-memory dict survives only as the fallback for no disk at all",
      SRC.count("_idem[key]") > 0 and "except OSError" in SRC)

# The paging values a caller sends are clamped rather than handed upstream.
# Driven rather than matched against the source: this asserted the literal
# `max(lo, min(hi, int(`, which is the implementation restated in the test --
# a third thing to keep in step, and it failed the day that expression became
# a call to hub/webargs.py's shared clamp, on a change that made the code
# better rather than worse. What matters is the answer the upstream API gets.
for _raw, _want in (("abc", "20"), ("-3", "1"), ("99999", "500"),
                    ("37", "37"), ("10.9", "10"), ("", "20")):
    with sp.app.test_request_context("/?limit=" + _raw):
        _got = sp._page_arg("limit", 20, 1, 500)
        check(f"?limit={_raw or '(empty)'} reaches GHL as {_want}",
              _got == _want)
        # Still a string: what this returns is forwarded as a query parameter.
        check(f"...and as a string", isinstance(_got, str))

print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
