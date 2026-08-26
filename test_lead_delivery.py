"""Delivery invariants for leads going into Smart 1 Suite.

The one property worth protecting here is that **a lead is never written
twice**. Everything else in this file exists to hold that line:

* there is exactly one route — the Suite API. The inbound webhook is retired,
  and a leftover HUB_LEAD_WEBHOOK_URL is not a second route and not a
  fallback;
* **no module posts a lead at an inbound webhook URL.** Six landing pages used
  to fall back to one when ``hub.leads`` raised, and four of them posted their
  abandoned-form partial lead straight there and nowhere else — so the panel
  never saw it, and ``sendBeacon`` on ``pagehide`` meant nobody was watching
  when it failed. Both are the same bug from either end: a second contact
  while the Suite trigger is live, and a silent hole once it is not. The
  source check below is what stops one coming back, because neither failure
  shows on any screen;
* a row that already carries a contact id is not re-sent;
* a timeout — where we genuinely cannot know whether the write landed — is
  retried through upsert, which matches the existing contact rather than
  adding another.

Run directly: ``python test_lead_delivery.py``. No pytest, no network — the
requests seam is stubbed, so this is safe to run anywhere and does not need
Suite credentials.
"""
import ast
import json
import os
import pathlib
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


# --- Nothing posts a lead at an inbound webhook ----------------------------
#
# Env names that hold an inbound GoHighLevel webhook URL. Posting a lead to one
# is the retired route: while the Suite trigger is live it writes the second
# contact hub/leads.py exists to prevent, and once the trigger is off it writes
# nothing at all, with a 200 and no log either side.
LEAD_WEBHOOK_ENVS = ("GHL_WEBHOOK_URL", "SMART1_WEBHOOK_URL",
                     "HUB_LEAD_WEBHOOK_URL", "CALCULATORS_LEAD_WEBHOOK_URL")

# Reading one of those names is fine where the point is to *report* that a
# value is still set so somebody clears it — that is the whole job of the
# panel's warning. Each entry says why, so a new one has to be argued for
# rather than appended. A file not on this list must not name them at all.
READS_ALLOWED = {
    "hub/leads.py":
        "reads HUB_LEAD_WEBHOOK_URL only to say it is still set and should go",
    "hub/config.py":
        "names the retired variable in the settings report",
    "modules/calculators/store.py":
        "reports CALCULATORS_LEAD_WEBHOOK_URL as a value to clear; never posts to it",
    "modules/calculators/__init__.py":
        "an empty config default, so the report above has a key to read",
    "modules/io_builder/app.py":
        "insertion orders, not leads: a different Suite workflow, and it "
        "refuses with a named error when the variable is unset rather than "
        "returning a quiet 200",
}

# Posting to something *named* like a webhook. Same allowance, same reason.
POSTS_ALLOWED = {"modules/io_builder/app.py"}


def _sources():
    for base in ("hub", "modules"):
        for path in sorted(pathlib.Path(base).rglob("*.py")):
            if "_attic" in path.parts:
                continue
            yield path


def webhook_source_checks():
    """Fail on any module that reads or posts to an inbound-webhook URL."""
    stray_reads, stray_posts = [], []
    for path in _sources():
        rel = path.as_posix()
        text = path.read_text(errors="ignore")
        if any(name in text for name in LEAD_WEBHOOK_ENVS) and rel not in READS_ALLOWED:
            stray_reads.append(rel)
        if rel in POSTS_ALLOWED:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:                             # not our problem here
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "post" and node.args):
                continue
            target = ast.dump(node.args[0])
            if "webhook" in target.lower() or "WEBHOOK" in target:
                stray_posts.append(f"{rel}:{node.lineno}")

    print("no module reads an inbound lead-webhook URL")
    check("files naming one outside the reporting allowlist", stray_reads, [])
    print("no module posts a lead to a webhook URL")
    check("posts to a webhook-named target", stray_posts, [])


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
    status = leads.route_status()
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

    webhook_source_checks()

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
