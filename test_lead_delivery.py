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

from hub import ghl_contacts, leads, lead_tags

FAILURES = []
CALLS = {"n": 0}


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


class Req:
    """The one thing trusted_source() reads: a headers mapping."""

    def __init__(self, headers):
        self.headers = headers


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


# --- The tags a lead carries, and the links a workflow emails ---------------
#
# Nothing here asserted anything about payload_for() before: requests.post was
# stubbed and the body it was handed never inspected, so the tag tuple could
# change and every test in the repo would pass. Suite workflows trigger on
# those tags now, and the report link is what the workflow emails.
def tag_and_link_checks():
    os.environ["GHL_LEAD_LOCATION_ID"] = "LOC456"
    for name in ("GHL_LEAD_REPORT_URL_FIELD_ID", "GHL_LEAD_PDF_URL_FIELD_ID"):
        os.environ.pop(name, None)

    print("the tags a scan widget lead carries")
    scan = leads.capture("scan_widget", "smart1-home-page",
                         {"name": "Sam Scan", "email": "s@example.com"},
                         pdf_url="https://smart1.agency/scans/r/tok.pdf",
                         meta={"report_url": "https://smart1.agency/scans/r/tok"})
    body = ghl_contacts.payload_for(scan)
    check("tag array", body["tags"], ["smart1-hub", "scan_widget", "smart1-home-page"])
    check("...built by the registry", body["tags"], lead_tags.tags_for(scan))

    print("the tags a website audit lead carries")
    audit = leads.capture("website_audit", "website-audit", {"email": "w@example.com"},
                          meta={"audit_url": "https://smart1.agency/scans/r/aud"})
    check("tag array", ghl_contacts.payload_for(audit)["tags"],
          ["smart1-hub", "website_audit", "website-audit"])

    print("with no custom-field ids the links are held, and that is named")
    check("no customFields in the body", "customFields" in body, False)
    links = ghl_contacts.report_links(scan)
    check("both links are reported as dropped", sorted(links["dropped"]), ["pdf_url", "report_url"])
    check("and none as sent", links["sent"], [])
    responds(200, {"contact": {"id": "CONTACT_SCAN", "new": True}})
    leads.deliver(scan)
    status = leads.route_status([scan])
    check("the panel warns", bool(status["links_warning"]), True)
    check("...naming both variables", "GHL_LEAD_REPORT_URL_FIELD_ID" in status["links_warning"]
          and "GHL_LEAD_PDF_URL_FIELD_ID" in status["links_warning"], True)
    check("...and counting the delivered lead whose link never went",
          "1 delivered lead" in status["links_warning"], True)
    check("...with a title the panel draws", bool(status["links_warning_title"]), True)
    from hub import config as _config
    fresh = _config.Settings()
    row_ = next(r for r in fresh.status() if r["name"] == "Lead report links in Suite")
    check("and the status page carries the same row, amber not red", row_["state"], "warn")

    print("with the ids configured the links go as custom fields")
    os.environ["GHL_LEAD_REPORT_URL_FIELD_ID"] = "cf_report_1"
    os.environ["GHL_LEAD_PDF_URL_FIELD_ID"] = "cf_pdf_1"
    body = ghl_contacts.payload_for(scan)
    check("customFields carry both links", body.get("customFields"), [
        {"id": "cf_report_1", "field_value": "https://smart1.agency/scans/r/tok"},
        {"id": "cf_pdf_1", "field_value": "https://smart1.agency/scans/r/tok.pdf"}])
    check("the audit page link rides under audit_url too",
          ghl_contacts.payload_for(audit).get("customFields"),
          [{"id": "cf_report_1", "field_value": "https://smart1.agency/scans/r/aud"}])
    check("a lead with no link sends no empty field",
          "customFields" in ghl_contacts.payload_for(
              leads.capture("calculators", "IMS", {"email": "c@example.com"})), False)
    check("and the panel stops warning", leads.route_status([scan])["links_warning"], "")
    check("and the status row goes green",
          next(r for r in _config.Settings().status()
               if r["name"] == "Lead report links in Suite")["state"], "ok")

    print("preflight lists the custom fields rather than guessing an id")
    _real_get = requests.get

    def _get(url, **kw):
        if url.endswith("/customFields"):
            return Resp(200, {"customFields": [
                {"id": "cf_report_1", "name": "Report URL", "fieldKey": "contact.report_url",
                 "dataType": "TEXT", "model": "contact"},
                {"id": "cf_opp", "name": "Opp field", "model": "opportunity"}]})
        return Resp(200, {"location": {"id": "LOC456", "name": "Smart 1 Marketing"}})
    requests.get = _get
    try:
        pre = ghl_contacts.preflight()
    finally:
        requests.get = _real_get
    fields = pre.get("custom_fields", {})
    check("contact fields are listed with their ids", [f["id"] for f in fields.get("fields", [])],
          ["cf_report_1"])
    check("an opportunity field is not offered as a contact one",
          any(f["id"] == "cf_opp" for f in fields.get("fields", [])), False)
    bad = next(c for c in pre["checks"] if c["check"].startswith("Report-link"))
    check("a configured id the location does not have is named", bad["ok"], False)
    check("...by variable", "pdf_url" in bad["detail"], True)
    check("and preflight is still ready -- the links are a to-do, not a blocker",
          pre["ready"], True)
    for name in ("GHL_LEAD_REPORT_URL_FIELD_ID", "GHL_LEAD_PDF_URL_FIELD_ID"):
        os.environ.pop(name, None)


# --- Every source tag a call site emits is one the registry knows ------------
#
# Suite workflows trigger on the source tag, so a source invented at a call
# site with no entry here is a lead that sits untriggered while the panel
# reads "delivered". Read from the AST: a source named in prose is not a call.
# Both directions -- a registry entry no call site uses is stale and goes on
# covering whatever is captured under that name next.
#
# `landing` is emitted by the built landing page's own JavaScript
# (hub/landing_render.py), which posts to /api/leads/capture with the source
# in the body, so no Python call site names it; it is declared here.
JS_SOURCES = {"landing": "hub/landing_render.py"}


def _literal_sources():
    found = {}
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        # A module-level NAME = "literal" is read as the literal -- the review
        # route names its tag once and passes the constant, and a sweep that
        # cannot follow that reports a registered tag as one nothing emits.
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("capture_and_deliver", "capture")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id.endswith("leads")):
                continue
            expr = None
            for kw in node.keywords:
                if kw.arg == "source":
                    expr = kw.value
            if expr is None and node.args:
                expr = node.args[0]
            # A literal, or a conditional choosing between literals -- the
            # scan widget picks its source that way. Anything else is a
            # value the sweep cannot read and is named as such.
            leaves = [expr] if not isinstance(expr, ast.IfExp) else [expr.body, expr.orelse]
            leaves = [ast.Constant(consts[l.id]) if isinstance(l, ast.Name) and l.id in consts else l
                      for l in leaves]
            for leaf in leaves:
                if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                    found.setdefault(leaf.value, []).append(f"{path.as_posix()}:{node.lineno}")
                elif leaf is not None:
                    found.setdefault("(not a literal)", []).append(f"{path.as_posix()}:{node.lineno}")
    return found


def source_registry_checks():
    print("every source a call site emits is in hub/lead_tags.py")
    found = _literal_sources()
    check("the sweep found call sites", len(found) > 10, True)
    unknown = sorted(s for s in found
                     if s not in lead_tags.SOURCES and s not in lead_tags.CAPTURE_ONLY
                     and s != "(not a literal)")
    check("sources named at a call site and in neither table", unknown, [])
    # hub/__init__.py's /api/leads/capture forwards whatever the body says,
    # which is how the landing pages' `landing` arrives; that one is read.
    check("the only non-literal source is the public capture route's pass-through",
          [x for x in found.get("(not a literal)", []) if not x.startswith("hub/__init__.py")], [])
    print("and no registry entry has outlived its call site")
    stale = sorted(s for s in list(lead_tags.SOURCES) + list(lead_tags.CAPTURE_ONLY)
                   if s not in found and s not in JS_SOURCES)
    check("registry entries no call site emits", stale, [])
    check("the JS-side source exists where it is said to",
          all(name in pathlib.Path(where).read_text(errors="ignore")
              for name, where in JS_SOURCES.items()), True)
    check("every entry says what it is",
          all(len(v.get("what", "")) > 10 for v in lead_tags.SOURCES.values()), True)
    check("every entry has a workflow slot, None until one is built",
          all("workflow" in v for v in lead_tags.SOURCES.values()), True)
    check("a tag with no workflow is not backed", lead_tags.backed("scan_widget"),
          bool(lead_tags.SOURCES["scan_widget"]["workflow"]))


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


def trusted_source_checks():
    """A landing *server* is not "anyone who finds the URL".

    /api/leads/capture is limited to three an hour per address, which is right
    for a browser and exactly wrong for the five standalone landing apps: every
    lead each of them takes arrives at the Hub from that app's one Render
    egress address, so on a busy afternoon the fourth visitor's lead is refused
    while the visitor sees their report and nothing on any screen says a lead
    was turned away. The token lifts the limit for those callers and does
    nothing else.
    """
    print("an unset token trusts nobody, rather than everybody")
    os.environ.pop(leads.SOURCE_TOKEN_ENV, None)
    check("trusted with no token set", leads.trusted_source(Req({})), False)
    check("trusted with a header and no token set",
          leads.trusted_source(Req({leads.SOURCE_TOKEN_HEADER: "anything"})), False)

    print("and with one set, only the right value is trusted")
    os.environ[leads.SOURCE_TOKEN_ENV] = "s3cret"
    check("no header at all", leads.trusted_source(Req({})), False)
    check("a wrong value", leads.trusted_source(Req({leads.SOURCE_TOKEN_HEADER: "nope"})), False)
    check("a prefix of the right value",
          leads.trusted_source(Req({leads.SOURCE_TOKEN_HEADER: "s3cre"})), False)
    check("the right value", leads.trusted_source(Req({leads.SOURCE_TOKEN_HEADER: "s3cret"})), True)
    # Render stores quotes literally, which has silently broken token matching
    # in this codebase before.
    os.environ[leads.SOURCE_TOKEN_ENV] = '"s3cret"'
    check("quotes Render stored literally are stripped",
          leads.trusted_source(Req({leads.SOURCE_TOKEN_HEADER: "s3cret"})), True)
    os.environ.pop(leads.SOURCE_TOKEN_ENV, None)

    print("the capture route skips the limit for a trusted caller and nobody else")
    src = pathlib.Path("hub/__init__.py").read_text()
    fn = src[src.index("def api_leads_capture"):]
    fn = fn[:fn.index("@app.route", 10)]
    check("it asks whether the caller is trusted", "leads.trusted_source(request)" in fn, True)
    check("and the rate check is what it skips",
          "if trusted else leads.rate_check(ip)" in fn, True)
    # An exemption nobody can see afterwards is one nobody can audit.
    check("the exemption is recorded on the lead", '"trusted_source": True' in fn, True)
    # The caller meeting this is a server, and "wait" is not the fix.
    check("the 429 names the token as the fix", "SOURCE_TOKEN_HEADER" in fn, True)


def meta_tag_checks():
    """Segmentation tags survive the webhook's retirement.

    boat, ski and hvac drove GoHighLevel "Add Tag" workflow actions off
    market_tag / package_tag in the webhook body. Over the Contacts API a tag
    is a field on the contact, so those ride in the lead's meta and are written
    directly -- no workflow in the middle to go missing.
    """
    print("free tags ride beside the controlled ones")
    row = {"source": "boat", "page": "smart1boat.onrender.com",
           "meta": {"tags": ["Boat - Coastal", "Boat - Growth"]}}
    check("the controlled tags come first",
          lead_tags.tags_for(row)[:3],
          [lead_tags.HUB_TAG, "boat", "smart1boat.onrender.com"])
    check("and the segmentation follows",
          lead_tags.tags_for(row)[3:], ["Boat - Coastal", "Boat - Growth"])

    print("a free tag may not impersonate the controlled half")
    # meta is the part of the payload a landing app fills in, so putting a lead
    # into a triggered audience it was never captured for is a mistake to make
    # impossible rather than one to ask people not to make.
    row = {"source": "boat", "meta": {"tags": [lead_tags.HUB_TAG, "ski", "Real Tag"]}}
    check("the hub tag and a source tag are refused",
          lead_tags.tags_for(row), [lead_tags.HUB_TAG, "boat", "Real Tag"])

    print("and the cap drops segmentation rather than the source tag")
    row = {"source": "boat", "page": "p",
           "meta": {"tags": [f"t{i}" for i in range(30)]}}
    tags = lead_tags.tags_for(row)
    check("capped", len(tags), lead_tags.MAX_TAGS)
    check("the source tag survived the cap", tags[1], "boat")

    print("junk in meta costs the junk, never the lead")
    row = {"source": "boat", "meta": {"tags": [None, {}, "  spaced   out  ", 42]}}
    check("cleaned", lead_tags.tags_for(row)[2:], ["spaced out", "42"])
    check("meta that is not a dict at all", lead_tags.tags_for({"source": "boat",
                                                               "meta": "nope"})[1:], ["boat"])

    print("meta is bounded on the way into the store")
    # It arrives from an unauthenticated endpoint and was written verbatim --
    # the one part of a lead row that was uncleaned and unbounded, on a 5 GB
    # disk where every lead is one append.
    # Driven through capture() rather than through the helper: asserting the
    # helper alone passes with the call site reverted, which is the check
    # testing its own copy of the rule instead of the code that runs.
    big = {"tags": ["a", "b"], "trusted_source": True, "long": "y" * 5000}
    big.update({f"k{i}": i for i in range(200)})
    m = leads.capture("x", "/y", {"email": "a@example.com"}, meta=big)["meta"]
    check("keys capped", len(m), leads.META_MAX_KEYS)
    check("values cleaned like a field", len(m["long"]), 400)
    check("a real flag survives as a flag", m["trusted_source"], True)
    check("and tags survive as a list", m["tags"], ["a", "b"])
    check("meta that is not a dict is dropped rather than stored",
          leads.capture("x", "/y", {"email": "a@example.com"}, meta="nope")["meta"], {})


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

    tag_and_link_checks()
    trusted_source_checks()
    meta_tag_checks()
    webhook_source_checks()
    source_registry_checks()

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
