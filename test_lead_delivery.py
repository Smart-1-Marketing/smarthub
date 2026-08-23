"""Delivery invariants for leads going into Smart 1 Suite.

The one property worth protecting here is that **a lead is never written
twice**. Everything else in this file exists to hold that line:

* there is exactly one route — the Suite API. The inbound webhook is retired,
  and a leftover HUB_LEAD_WEBHOOK_URL is not a second route and not a
  fallback;
* a row that already carries a contact id is not re-sent;
* a timeout — where we genuinely cannot know whether the write landed — is
  retried through upsert, which matches the existing contact rather than
  adding another.

Run directly: ``python test_lead_delivery.py``. No pytest, no network — the
requests seam is stubbed, so this is safe to run anywhere and does not need
Suite credentials.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Point the store somewhere disposable BEFORE importing hub.leads. Without
# this the test writes real-looking leads into the live panel.
os.environ["HUB_LEADS_FILE"] = "/tmp/hub_lead_delivery_test/leads.jsonl"
if os.path.exists(os.environ["HUB_LEADS_FILE"]):
    os.remove(os.environ["HUB_LEADS_FILE"])

import requests

from hub import ghl_contacts, leads

FAILURES = []
CALLS = {"n": 0}


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    @property
    def text(self):
        return json.dumps(self._b)

    def json(self):
        return self._b


def responds(code, body):
    def _post(url, **kw):
        CALLS["n"] += 1
        return Resp(code, body)
    requests.post = _post


def main():
    os.environ["GHL_PRIVATE_TOKEN"] = "pit-test-token"
    os.environ["GHL_COMPANY_ID"] = "COMPANY123"

    print("a companyId is refused where a locationId belongs")
    os.environ["GHL_LEAD_LOCATION_ID"] = "COMPANY123"
    check("configured with location == company", ghl_contacts.configured(), False)

    print("a real location id enables API delivery")
    os.environ["GHL_LEAD_LOCATION_ID"] = "LOC456"
    check("delivery mode", leads.delivery_mode(), "api")

    print("a leftover webhook URL is not a second route")
    os.environ["HUB_LEAD_WEBHOOK_URL"] = "https://hooks.example/x"
    check("delivery mode with the webhook also set", leads.delivery_mode(), "api")
    status = leads._route_status()
    check("panel asks for it to be cleared", bool(status["route_warning"]), True)
    check("the ask is titled", bool(status["route_warning_title"]), True)

    print("and it is not a fallback when the API is unconfigured")
    # Every name ghl_contacts will accept as a location, so an inherited one
    # in the developer's shell can't quietly keep API delivery switched on.
    saved = {n: os.environ.pop(n) for n in ghl_contacts.LOCATION_ENV
             if n in os.environ}
    responds(200, {"contact": {"id": "SHOULD_NOT_BE_REACHED"}})
    before = CALLS["n"]
    check("delivery mode with only the webhook set", leads.delivery_mode(), "none")
    row0 = leads.deliver(leads.capture("x", "/y", {"email": "w@example.com"}))
    check("delivered", row0["delivered"], False)
    check("requests made", CALLS["n"] - before, 0)
    check("says the webhook is retired", "retired" in row0["last_error"], True)
    os.environ.update(saved)
    check("delivery mode once the location is back", leads.delivery_mode(), "api")

    print("a successful upsert records the contact id")
    responds(200, {"contact": {"id": "CONTACT_ABC", "new": True}})
    row = leads.capture("landing_ads", "/hvac",
                        {"name": "Jane Doe", "email": "j@example.com"})
    row = leads.deliver(row)
    check("delivered", row["delivered"], True)
    check("contact id", row["contact_id"], "CONTACT_ABC")
    check("write calls", CALLS["n"], 1)

    print("retrying a delivered lead does not write it again")
    leads.deliver(row)
    check("write calls after retry", CALLS["n"], 1)

    print("a timeout leaves the lead queued, not failed and not duplicated")
    def _timeout(url, **kw):
        CALLS["n"] += 1
        raise requests.exceptions.Timeout("timed out")
    requests.post = _timeout
    row2 = leads.capture("calculators", "/roi", {"email": "t@example.com"})
    row2 = leads.deliver(row2)
    check("delivered after timeout", row2["delivered"], False)
    check("still retryable", row2.get("retryable", True), True)

    print("the retry after a timeout matches the existing contact")
    responds(200, {"contact": {"id": "CONTACT_ABC", "new": False}})
    row2 = leads.deliver(row2)
    check("contact id", row2["contact_id"], "CONTACT_ABC")
    check("was a new contact", row2["contact_new"], False)

    print("a 2xx with no contact id is not a delivery")
    requests.post = lambda url, **kw: Resp(200, {"ok": True})
    row3 = leads.deliver(leads.capture("x", "/y", {"email": "n@example.com"}))
    check("delivered", row3["delivered"], False)

    print("an auth failure is not retried on a timer")
    requests.post = lambda url, **kw: Resp(401, {"message": "unauthorized"})
    row4 = leads.deliver(leads.capture("x", "/y", {"email": "a@example.com"}))
    check("retryable", row4["retryable"], False)

    print("a lead with no email or phone cannot be matched, and says so")
    row5 = leads.deliver(leads.capture("x", "/y", {"name": "No Contact Details"}))
    check("retryable", row5["retryable"], False)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all delivery invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
