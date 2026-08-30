"""The Ad Copy Request: the ids, the object it lives on, and the prefill.

Ad Copy used to be a Campaign Change Request with its subject pre-written —
four boxes, and a rep retyping the client, the campaign, the current order
number and the media partner out of the record on the screen behind them.
Five things this protects, each of which is a way this codebase has already
been confidently wrong:

* **The ids are the ones the campaign team gave us.** Pinned for the reason
  hub/knack_api.py gives at length: label matching broke silently when a
  label was renamed, and nothing said so.
* **The object is discovered, never guessed.** Nobody pinned its number, and
  falling back to a plausible object writes ad copy requests where nobody is
  reading them — which looks exactly like a form that worked.
* **Exactly one candidate, or none.** A client with two campaigns gets a
  dropdown, not the first one. A plausible value on a form nobody re-reads is
  worse than a blank.
* **Nothing is invented.** The due date and the deadline stay empty; the
  submitted date is the clock and the status is whatever Knack itself
  publishes as the default.
* **An empty answer says which kind of empty it is.** "No orders on file",
  "we could not read the client list" and "this session has no account, so
  there is no email address" are three situations, not one blank.

Run directly: ``python3 test_ad_copy.py``. No pytest, no network — the
requests seam is stubbed, so this needs no Knack credentials and touches
nothing real.
"""
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("KNACK_APP_ID", "test-app")
os.environ.setdefault("KNACK_API_KEY", "test-key")
os.environ.pop("KNACK_AD_COPY_OBJECT", None)

from hub import ad_copy, knack_api

FAILURES = []


def check(label, got, want):
    ok_ = got == want
    print(f"  {'ok  ' if ok_ else 'FAIL'}  {label}: {got!r}")
    if not ok_:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


def ok(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}"
          f"{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        FAILURES.append(f"{label}{(': ' + detail) if detail else ''}")


# --------------------------------------------------------------------------
# The list the campaign team gave us, verbatim. This is the fixture the pinned
# ids are checked against — if Knack is restructured, this list changes first.
# --------------------------------------------------------------------------
GIVEN = [
    ("field_1804", "Seller Name",                              "seller"),
    ("field_1805", "Confirmation Email Address",               "confirm_email"),
    ("field_1806", "Client Name",                              "client"),
    ("field_1807", "Campaign Name",                            "campaign"),
    ("field_1808", "Current Order Number",                     "order_number"),
    ("field_1809", "Change for What?",                         "change_for"),
    ("field_1810", "When Should This Change?",                 "when"),
    ("field_1811", "Is The URL Changing?",                     "url_changing"),
    ("field_1812", "Is there Something Else we need to know?", "anything_else"),
    ("field_1813", "Uploaded Files",                           "files"),
    ("field_1851", "Media Partner",                            "media_partner"),
    ("field_1853", "Due Date",                                 "due_date"),
    ("field_1854", "Status",                                   "status"),
    ("field_1866", "Submitted Date",                           "submitted"),
]

AD_COPY_OBJECT = "object_144"
CHANGE_CHOICES = ["Ad Copy", "Landing Page", "Offer", "Creative"]
STATUS_CHOICES = ["New", "In Progress", "Complete"]

CLIENTS = [{"id": "c" * 24, "field_8": "Riverside HVAC"},
           {"id": "d" * 24, "field_8": "Riverside HVAC Supply"}]


def _f(key, label, ftype, **extra):
    d = {"key": key, "label": label, "type": ftype, "required": False}
    d.update(extra)
    return d


SCHEMA = [
    _f("field_1804", "Seller Name", "short_text"),
    _f("field_1805", "Confirmation Email Address", "email"),
    _f("field_1806", "Client Name", "connection",
       relationship={"object": "object_50", "has": "one"}),
    _f("field_1807", "Campaign Name", "short_text"),
    _f("field_1808", "Current Order Number", "short_text"),
    _f("field_1809", "Change for What?", "multiple_choice",
       format={"options": CHANGE_CHOICES, "type": "single"}, required=True),
    _f("field_1810", "When Should This Change?", "short_text"),
    _f("field_1811", "Is The URL Changing?", "multiple_choice",
       format={"options": ["Yes", "No"], "type": "single"}),
    _f("field_1812", "Is there Something Else we need to know?", "paragraph_text"),
    _f("field_1813", "Uploaded Files", "file"),
    _f("field_1851", "Media Partner", "short_text"),
    _f("field_1853", "Due Date", "date_time"),
    _f("field_1854", "Status", "multiple_choice",
       format={"options": STATUS_CHOICES, "type": "single", "default": "New"}),
    _f("field_1866", "Submitted Date", "date_time"),
]

# Another object in the same app, so discovery has to actually look rather
# than take the first thing it is offered.
OTHER_SCHEMA = [_f("field_9001", "Something Else", "short_text")]


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

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


SENT = {"post": None}
READS = {"fields": []}


class FakeRequests:
    """Knack, as far as this module is concerned."""

    RequestException = Exception
    OBJECT_LIST = [{"key": "object_140"}, {"key": "object_121"},
                   {"key": "object_107"}, {"key": AD_COPY_OBJECT}]

    @staticmethod
    def get(url, **kw):
        if url.endswith("/objects"):
            return Resp(200, {"objects": FakeRequests.OBJECT_LIST})
        m = re.search(r"/objects/(object_\d+)/fields$", url)
        if m:
            READS["fields"].append(m.group(1))
            if m.group(1) == AD_COPY_OBJECT:
                return Resp(200, {"fields": SCHEMA})
            return Resp(200, {"fields": OTHER_SCHEMA})
        if url.endswith("/objects/object_50"):
            return Resp(200, {"object": {"identifier": "field_8"}})
        if url.endswith("/objects/object_50/records"):
            return Resp(200, {"records": CLIENTS})
        return Resp(404, {})

    @staticmethod
    def post(url, **kw):
        SENT["post"] = {"url": url, "json": kw.get("json")}
        return Resp(200, {"id": "f" * 24, **(kw.get("json") or {})})


knack_api.requests = FakeRequests


def reset():
    knack_api._schema_cache.clear()
    ad_copy.forget()
    READS["fields"] = []
    SENT["post"] = None


# --------------------------------------------------------------------------
# The client's own insertion orders, as knack_data.search_client returns them.
# --------------------------------------------------------------------------
def _orders(rows, client="Riverside HVAC", websites=None):
    return [{"client": client, "products": rows,
             "websites": websites or [{"domain": "riversidehvac.com",
                                       "liveUrl": "https://riversidehvac.com"}]}]


ONE_ORDER = _orders([
    {"product": "Connected TV", "campaign": "Spring Tune-Up", "io": "4821",
     "status": "Live", "partner": "Cumulus Media", "sales": "Dana Reid"},
])

TWO_ORDERS = _orders([
    {"product": "Connected TV", "campaign": "Spring Tune-Up", "io": "4821",
     "status": "Live", "partner": "Cumulus Media", "sales": "Dana Reid"},
    {"product": "Display", "campaign": "Fall Furnace", "io": "5013",
     "status": "Live", "partner": "iHeart", "sales": "Dana Reid"},
])


def use_orders(groups, error=""):
    from hub import knack_data

    def fake(q, limit=8):
        if error:
            raise RuntimeError(error)
        return groups
    knack_data.search_client = fake


print("\n=== The ids the campaign team gave us ===")
for fid, label, key in GIVEN:
    check(f"{label} -> {key}", ad_copy.AD_COPY_FIELDS.get(key), fid)
    check(f"{key} label", ad_copy.AD_COPY_LABELS.get(key), label)
check("no extra fields pinned", len(ad_copy.AD_COPY_FIELDS), len(GIVEN))

print("\n=== Every field is reachable, and the file field is not written ===")
reset()
fields = ad_copy.form_fields(AD_COPY_OBJECT)
drawn = [f["key"] for f in fields]
for _fid, label, key in GIVEN:
    ok(f"{label} is drawn", key in drawn,
       "pinned, writable and never on the form is the exact state this "
       "replaces")
check("files is not writable", "files" in ad_copy.AD_COPY_CREATE_FIELDS, False)
check("everything else is writable",
      sorted(ad_copy.AD_COPY_CREATE_FIELDS),
      sorted(k for _f, _l, k in GIVEN if k != "files"))
ok("nothing lands under Other", "Other" not in {f["group"] for f in fields},
   "a key in the write set and missing from AD_COPY_GROUPS is appended there")
check("the file field draws as a file, not a text box",
      [f["control"] for f in fields if f["key"] == "files"], ["file"])
check("Change for What? offers Knack's own choices",
      [f["choices"] for f in fields if f["key"] == "change_for"],
      [CHANGE_CHOICES])

print("\n=== The object is discovered, never guessed ===")
reset()
obj, why = ad_copy.resolve()
check("found the object carrying field_1804", obj, AD_COPY_OBJECT)
check("no reason to report", why, "")
ok("it actually looked at more than one object", len(READS["fields"]) > 1,
   f"read fields for {READS['fields']}")

reset()
FakeRequests.OBJECT_LIST = [{"key": "object_140"}, {"key": "object_121"}]
obj, why = ad_copy.resolve()
check("an object it cannot find is not substituted", obj, "")
ok("and the reason names the way out", "KNACK_AD_COPY_OBJECT" in why, why)
FakeRequests.OBJECT_LIST = [{"key": "object_140"}, {"key": "object_121"},
                            {"key": "object_107"}, {"key": AD_COPY_OBJECT}]

reset()
os.environ["KNACK_AD_COPY_OBJECT"] = "object_999"
check("a pinned object wins outright", ad_copy.resolve()[0], "object_999")
ok("and pinning costs no discovery read", not READS["fields"])
del os.environ["KNACK_AD_COPY_OBJECT"]

reset()
FakeRequests.OBJECT_LIST = [{"key": AD_COPY_OBJECT, "fields": SCHEMA}]
check("fields published inline are read from there",
      ad_copy.resolve()[0], AD_COPY_OBJECT)
check("and cost no per-object read", READS["fields"], [])
FakeRequests.OBJECT_LIST = [{"key": "object_140"}, {"key": "object_121"},
                            {"key": "object_107"}, {"key": AD_COPY_OBJECT}]

print("\n=== What the client's own orders fill in ===")
reset()
use_orders(ONE_ORDER)
d = ad_copy.form("Riverside HVAC", user_name="Todd Smart",
                 user_email="todd@smart1marketing.com")
v = d["values"]
check("seller is the signed-in account", v.get("seller"), "Todd Smart")
check("confirmation address is the account's",
      v.get("confirm_email"), "todd@smart1marketing.com")
check("the client resolves to exactly one record id", v.get("client"), "c" * 24)
check("one campaign is filled in", v.get("campaign"), "Spring Tune-Up")
check("so is its order number", v.get("order_number"), "4821")
check("so is its media partner", v.get("media_partner"), "Cumulus Media")
check("submitted is today", v.get("submitted"), date.today().strftime("%m/%d/%Y"))
check("status opens on Knack's own default", v.get("status"), "New")
ok("the due date is left alone", not v.get("due_date"),
   "there is no source for it here, and a date the Hub made up is a date "
   "somebody works to")
ok("so is the deadline", not v.get("when"))
ok("and so is the change itself", not v.get("change_for"))
check("the client's websites are offered", d["options"]["urls"],
      ["https://riversidehvac.com", "riversidehvac.com"])

print("\n=== Two candidates is a dropdown, not the first one ===")
reset()
use_orders(TWO_ORDERS)
d = ad_copy.form("Riverside HVAC", user_name="Todd Smart",
                 user_email="todd@smart1marketing.com")
v, notes = d["values"], " | ".join(d["notes"])
ok("no campaign is chosen", not v.get("campaign"))
ok("no order number is chosen", not v.get("order_number"))
ok("no media partner is chosen", not v.get("media_partner"))
ok("and each says how many there are", "this client has 2" in notes, notes)
check("both campaigns are offered", d["options"]["campaigns"],
      ["Fall Furnace", "Spring Tune-Up"])
check("newest insertion order first",
      [o["io"] for o in d["options"]["orders"]], ["5013", "4821"])

print("\n=== Four kinds of empty, and each says which ===")
reset()
use_orders(ONE_ORDER)
d = ad_copy.form("Riverside HVAC", user_name="", user_email="")
ok("a shared-password session is told why there is no address",
   any("no account behind it" in n for n in d["notes"]), str(d["notes"]))
check("and the seller falls back to the rep on this client's own orders",
      d["values"].get("seller"), "Dana Reid")

reset()
use_orders(_orders([]))
d = ad_copy.form("Riverside HVAC", user_name="", user_email="")
ok("with no account and no rep, the seller is left blank and named",
   not d["values"].get("seller")
   and any("Seller Name" in n for n in d["notes"]), str(d["notes"]))
src = open("hub/__init__.py", encoding="utf-8").read()
ok("the route never puts the shared-login marker in Seller Name",
   'user_name=(getattr(acct, "name", "") or "")' in src,
   "current_user() answers \u201cShared login\u201d, which is true about the "
   "session and wrong in a box the campaign team reads as a person")

reset()
use_orders([], error="Knack timed out")
d = ad_copy.form("Riverside HVAC", user_name="Todd Smart",
                 user_email="todd@smart1marketing.com")
ok("could not look is not the same as nothing to find",
   any("could not be read" in n for n in d["notes"]), str(d["notes"]))
ok("and it does not read as a client with no orders",
   not any("no insertion orders" in n for n in d["notes"]), str(d["notes"]))

reset()
use_orders(_orders([]))
d = ad_copy.form("Riverside HVAC", user_name="Todd Smart",
                 user_email="todd@smart1marketing.com")
ok("a client with no orders says so",
   any("no insertion orders" in n for n in d["notes"]), str(d["notes"]))

print("\n=== A near name is not a name ===")
reset()
use_orders(_orders([], client="Riverside HVAC Roofing"))
d = ad_copy.form("Riverside HVAC Roofing", user_name="Todd Smart",
                 user_email="todd@smart1marketing.com")
ok("an unmatched client is left for the rep", not d["values"].get("client"),
   "attributing one company's ad copy to another is the worst outcome here")
ok("and it says which name found nothing",
   any("Riverside HVAC Roofing" in n for n in d["notes"]), str(d["notes"]))

print("\n=== What a write carries, and what it refuses ===")
reset()
use_orders(ONE_ORDER)
rec = ad_copy.create("Riverside HVAC", {
    "client": "c" * 24,
    "seller": "Todd Smart",
    "confirm_email": "todd@smart1marketing.com",
    "campaign": "Spring Tune-Up",
    "order_number": "4821",
    "change_for": "Ad Copy",
    "media_partner": "Cumulus Media",
    "submitted": "08/27/2026",
    "status": "New",
    # Refused, each for its own reason.
    "files": "banner.zip",
    "url_changing": "Maybe",
}, author="Todd Smart")
sent = (SENT["post"] or {}).get("json") or {}
check("posted to the discovered object", (SENT["post"] or {}).get("url"),
      f"{knack_api.BASE}/objects/{AD_COPY_OBJECT}/records")
check("the client went as a record id", sent.get("field_1806"), "c" * 24)
check("the campaign went", sent.get("field_1807"), "Spring Tune-Up")
check("the change went as one of Knack's own choices",
      sent.get("field_1809"), "Ad Copy")
ok("the file field was not written", "field_1813" not in sent)
ok("a file is refused by name, not silently dropped",
   any("field" in r.lower() or "file" in r.lower()
       for r in rec["rejected"] if "Uploaded Files" in r), str(rec["rejected"]))
ok("a choice Knack would refuse is refused here",
   any("Is The URL Changing?" in r and "Maybe" in r for r in rec["rejected"]),
   str(rec["rejected"]))
ok("and the rest of the record still went", len(rec["written"]) >= 8,
   str(rec["written"]))

print("\n=== The shared-login marker never reaches Seller Name ===")
reset()
use_orders(_orders([]))
rec = ad_copy.create("Riverside HVAC", {"client": "c" * 24},
                     author="Shared login")
sent = (SENT["post"] or {}).get("json") or {}
ok("a request from a shared-password session carries no seller",
   "field_1804" not in sent,
   "the prefill refuses that marker; a rule the form keeps and the write "
   "breaks is not a rule")
reset()
rec = ad_copy.create("Riverside HVAC", {"client": "c" * 24}, author="Todd Smart")
sent = (SENT["post"] or {}).get("json") or {}
check("but a real name is still written without being asked for",
      sent.get("field_1804"), "Todd Smart")


print("\n=== A request filed against nobody is refused ===")
reset()
try:
    ad_copy.create("Riverside HVAC", {"campaign": "Spring Tune-Up"},
                   author="Todd Smart")
    ok("a client that did not resolve stops the write", False,
       "it was created anyway")
except RuntimeError as exc:
    ok("a client that did not resolve stops the write", True)
    ok("and says so", "Client Name" in str(exc), str(exc))

print("\n=== The request lands on the client's own record ===")
from hub import client_brand
ok("ad_copy is a name the work log can say",
   "ad_copy" in client_brand.WORK_KINDS,
   "work_log() skips a module it cannot name, and a skipped module reads on "
   "the record as a client nobody has done any work for")
src = open("hub/__init__.py", encoding="utf-8").read()
ok("and the route logs under it, not under hub",
   'audit.log("ad_copy", "request_created"' in src)
ok("with the client, so it reaches Client 360",
   re.search(r'audit\.log\("ad_copy".{0,200}?client=client', src, re.S) is not None)


print("\n=== The form is the same wherever it is opened ===")
PAGES = {
    "hub/templates/dashboard.html": "the dashboard's Ad Copy Request tile",
    "hub/templates/client360.html": "the client record's Products & IOs card",
}
for path, where in PAGES.items():
    src = open(path, encoding="utf-8").read()
    ok(f"{where} loads the drawer", "/knack-form.js" in src)
    ok(f"{where} loads the form", "/ad-copy.js" in src)
    ok(f"{where} opens it", "AdCopyRequest" in src)
    ok(f"{where} no longer files ad copy as a campaign change",
       "'Ad copy request'" not in src,
       "Ad Copy is its own object with fourteen fields; the change request "
       "asked four questions")

js = open("hub/static/ad-copy.js", encoding="utf-8").read()
ok("the form has no second copy of the prefill rules",
   "search_client" not in js and "/api/client/ad-copy/fields" in js,
   "it draws what the server decided — target areas and the creative "
   "classifier each carry a mirror already, and each needs a test proving "
   "the halves agree")
ok("the drawer is read at call time, not captured at load",
   "var KF = function () { return window.KnackForm; }" in js,
   "a page that loads the two scripts in the other order would capture "
   "undefined and fail with no clue why")
ok("picking a campaign writes into the box rather than redrawing the row",
   "addEventListener('change'" in js and "drawAreas" not in js,
   "a container that re-renders while somebody is typing into it eats what "
   "they typed — the Smart 1 Ads target-area trap")

for path in ("hub/static/web-ticket.js", "hub/static/campaign-request.js",
             "hub/static/ad-copy.js"):
    src = open(path, encoding="utf-8").read()
    ok(f"{os.path.basename(path)} draws through KnackForm",
       "KnackForm" in src,
       "two copies of what control a Knack dropdown needs is two copies to "
       "keep in step")

print("\n=== This client's own answers are offered on the fields ===")
reset()
use_orders(TWO_ORDERS)
d = ad_copy.form("Riverside HVAC", user_name="Todd Smart",
                 user_email="todd@smart1marketing.com")
by_key = {f["key"]: f for f in d["fields"]}
check("the campaign box offers this client's campaigns",
      by_key["campaign"].get("suggest"), ["Fall Furnace", "Spring Tune-Up"])
check("the order number box offers their IO numbers",
      by_key["order_number"].get("suggest"), ["5013", "4821"])
check("the media partner box offers their partners",
      by_key["media_partner"].get("suggest"), ["iHeart", "Cumulus Media"])
ok("the due date says it is blank on purpose",
   "on purpose" in (by_key["due_date"].get("hint") or ""),
   by_key["due_date"].get("hint") or "")
ok("and the file field says where files actually go",
   "Knack" in (by_key["files"].get("hint") or ""),
   by_key["files"].get("hint") or "")

# --------------------------------------------------------------------------
# The control three forms share, and the one that went without it
# --------------------------------------------------------------------------
print("\nThe triage control, on all three forms")
print("-" * 60)

ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


KF = _read("hub/static/knack-form.js")
ADJS = _read("hub/static/ad-copy.js")
WTJS = _read("hub/static/web-ticket.js")
CSJS = _read("hub/static/campaign-request.js")
HUBPY = _read("hub/__init__.py")

# knack-form.js draws the web ticket, campaign support and ad copy. The
# control was written once precisely so a third form would get it without
# being edited — and ad copy went a release without it, because only the
# browser half was shared and the route knew two kinds.
for name, src in (("web ticket", WTJS), ("campaign request", CSJS),
                  ("ad copy", ADJS)):
    ok(f"the {name} form draws the triage control", "triageButton(" in src)

# Exercised rather than grepped for: the failure this closes is that the
# browser half was shared and the route half knew two kinds, which reading
# either file on its own cannot see.
import tempfile
os.environ.setdefault("SECRET_KEY", "ad-copy-test-key-0123456789")
os.environ.setdefault("PANEL_PASSWORD", "x")
os.environ.setdefault("HUB_DATA_DIR", tempfile.mkdtemp())
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp()[1]
import hub as _hub
from hub import auth as _auth, request_triage as _triage

_app = _hub.create_hub_app()
_client = _app.test_client()
_client.set_cookie(_auth.COOKIE_NAME, _auth.issue_cookie_value("Tester"),
                   domain="localhost")

_seen = {}


def _stub_suggest(text, fields, empty_keys, *, module="tickets"):
    _seen["module"] = module
    _seen["fields"] = fields
    _seen["text"] = text
    return {"ok": True, "suggestions": {}, "unusable": 0, "error": "", "note": ""}


_orig_suggest = _triage.suggest
_orig_fields = ad_copy.form_fields
_triage.suggest = _stub_suggest
ad_copy.form_fields = lambda obj="": [{"key": "url_changing", "control": "boolean"}]
# resolve() would otherwise walk a Knack that is not there.
ad_copy._cache["object"] = ("object_99", "")


def _post(kind, text="the landing page URL is changing to /summer"):
    r = _client.post("/api/client/requests/triage",
                     json={"kind": kind, "text": text, "empty": ["url_changing"]})
    return r.status_code, r.get_json() or {}


try:
    _code, _body = _post("adcopy")
    ok("the route answers for the ad copy form", _code == 200 and _body.get("ok"),
       f"{_code} {_body.get('error')}")
    check("and reads that object's own fields",
          [f["key"] for f in _seen.get("fields", [])], ["url_changing"])
    check("and bills the call to ad copy", _seen.get("module"), "ad_copy")

    # It used to read `if ticket ... else campaign`, so any other spelling
    # answered with the campaign change form's dropdowns against an ad copy
    # request's prose -- a button that half works.
    _seen.clear()
    _code, _body = _post("adcopyy")
    ok("a form it does not know is refused by name",
       _code == 200 and not _body.get("ok")
       and "adcopyy" in (_body.get("error") or ""),
       _body.get("error") or "")
    ok("and nothing was read for it", "module" not in _seen)
finally:
    _triage.suggest = _orig_suggest
    ad_copy.form_fields = _orig_fields
    ad_copy.forget()

# The four control names were written out in two places: hub/request_triage's
# CHOICE_CONTROLS and a bare list inside emptyChoiceKeys. A control added on
# one side and not the other means the button offers a field the server will
# not answer for, or the other way round, with nothing on screen saying so.
from hub.request_triage import CHOICE_CONTROLS
_js = re.search(r"var CHOICE_CONTROLS = \[([^\]]+)\]", KF)
# Exactly one occurrence, and it is the declaration: a second is a function
# carrying its own copy, which is what emptyChoiceKeys had.
ok("the browser has one list of choice controls, not one per function",
   bool(_js) and KF.count("['select', 'multi', 'boolean', 'radio']") == 1
   and "var CHOICE_CONTROLS = ['select', 'multi', 'boolean', 'radio']" in KF)
if _js:
    _names = tuple(x.strip().strip("'\"") for x in _js.group(1).split(","))
    check("and it agrees with the server's", _names, tuple(CHOICE_CONTROLS))

# Its own comment has always said this. The code drew the button on every
# form and only said "no" once somebody had pressed it.
ok("the button is not drawn where there is no choice field to fill",
   "if (!hasChoiceField(fields)) return;" in KF)
ok("but stays drawn when the choice fields merely happen to be answered",
   "emptyChoiceKeys" in KF and "already has one" in KF)

# An ad copy request splits what is being asked for across two boxes, and
# the deadline or the URL change is as likely to be in the second.
ok("the ad copy form reads both of its free-text boxes",
   "'change_for', 'anything_else'" in ADJS)
ok("and the control accepts one key or several",
   "Array.isArray(textKey)" in KF)
# Those two keys have to be fields this object actually has, or the button
# reads an element that is not there and sends an empty description.
for _k in ("change_for", "anything_else"):
    ok(f"{_k} is a real ad copy field", _k in ad_copy.AD_COPY_FIELDS)

# The tag hub/ai.py bills the call under. Left at "tickets" for all three,
# every triage call in this Hub reads as the ticket form's on the page that
# says what the models cost.
for _tag in ('"tickets"', '"campaign_support"', '"ad_copy"'):
    ok(f"triage bills its own tool ({_tag.strip(chr(34))})",
       _tag in HUBPY.split("READERS = {")[1].split("}")[0])


print("\n" + ("-" * 60))
if FAILURES:
    print(f"{len(FAILURES)} failure(s):")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("all ad copy checks passed.")
