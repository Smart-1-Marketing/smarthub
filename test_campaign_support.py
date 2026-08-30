"""The Campaign Support request: the ids, the form built from them, the write.

object_121 has carried twenty-three fields for years and the Hub sent four of
them. The insertion order, the due date, the kind of support asked for, the
pixel URL, the timeline, the rush and its reason, who to notify and the notes
all arrived blank on every request Client 360 and the dashboard raised, and
the campaign team filled them in by going back and asking — which is the same
state the web ticket was in before TICKET_FIELDS was pinned.

Five things this protects, each of which has already gone wrong once in this
codebase or is one rename away from going wrong:

* **The ids are the ones the campaign team gave us.** Pinned, not matched by
  label — a renamed label breaks label matching silently, which is why the
  Issue column on the Accounting report came back empty. A typo in a pinned id
  has to fail here rather than in Knack.
* **Every field on that list is reachable.** A field can be pinned, be
  writable, and still never be asked for: that is precisely the state this
  object was in. So each must be in the write set *and* in the drawing order.
* **Every option comes off the live object.** A dropdown's choices and a
  connection's records are Knack's, not ours. A form that guesses one writes a
  value Knack refuses — and Knack refuses the whole record rather than the
  field, so a guessed choice costs the request.
* **Nothing is dropped in silence.** A choice this object does not publish, a
  connection handed a name matching no record, and a file field handed a
  filename each come back named in `rejected`. A request created with half its
  fields missing must not read as a clean success.
* **The change request still works.** object_140 has no pinned ids and is
  still matched by label; both kinds come out of one builder, so the support
  work must not have quietly changed what a change request writes.

Run directly: ``python3 test_campaign_support.py``. No pytest, no network —
the requests seam is stubbed, so this needs no Knack credentials and touches
nothing real.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("KNACK_APP_ID", "test-app")
os.environ.setdefault("KNACK_API_KEY", "test-key")

from hub import knack_api

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
    ("field_2593", "Insertion Order",                        "insertion_order"),
    ("field_1859", "Due Date",                               "due_date"),
    ("field_1818", "Campaign Support",                       "support_type"),
    ("field_2851", "URL for Pixel to Add",                   "pixel_url"),
    ("field_1819", "Describe Your Campaign Support Issue",   "description"),
    ("field_1820", "Uploaded Files",                         "uploaded_files"),
    ("field_1867", "Submitted Date",                         "submitted_date"),
    ("field_2595", "Submitted By",                           "submitted_by"),
    ("field_2596", "Media Partner",                          "media_partner"),
    ("field_2597", "Client",                                 "client"),
    ("field_2780", "Timeline",                               "timeline"),
    ("field_2608", "Partner Contact",                        "partner_contact"),
    ("field_2609", "Client Contact",                         "client_contact"),
    ("field_2610", "Notify Client?",                         "notify_client"),
    ("field_2611", "Notify Partner?",                        "notify_partner"),
    ("field_2612", "Notes",                                  "notes"),
    ("field_2613", "IO#",                                    "io_number"),
    ("field_2614", "Campaign",                               "campaign"),
    ("field_2786", "Rush",                                   "rush"),
    ("field_2787", "Reason for Rush",                        "rush_reason"),
    ("field_2793", "IO Product",                             "io_product"),
    ("field_3347", "IOP Status",                             "iop_status"),
    ("field_3419", "Product",                                "product"),
]

SUPPORT_CHOICES = ["Pixel", "Creative swap", "Reporting", "Pacing", "Other"]
TIMELINE_CHOICES = ["Today", "This week", "This month"]
IOP_CHOICES = ["New", "In Progress", "Complete"]

IOS = [{"id": "a" * 24, "field_70": "IO 4821 — Riverside HVAC"},
       {"id": "b" * 24, "field_70": "IO 4822 — Riverside HVAC"}]
PARTNERS = [{"id": "c" * 24, "field_71": "Cumulus Media"},
            {"id": "d" * 24, "field_71": "iHeart"}]
CUSTOMERS = [{"id": "e" * 24, "field_72": "Riverside HVAC"},
             {"id": "f" * 24, "field_72": "Riverside HVAC LLC"}]
PRODUCTS = [{"id": "1" * 24, "field_73": "Connected TV"},
            {"id": "2" * 24, "field_73": "Targeted Display"}]


def _f(key, label, ftype, **extra):
    d = {"key": key, "label": label, "type": ftype, "required": False}
    d.update(extra)
    return d


SCHEMA_121 = [
    _f("field_2593", "Insertion Order", "connection",
       relationship={"object": "object_135", "has": "one"}),
    _f("field_1859", "Due Date", "date_time"),
    _f("field_1818", "Campaign Support", "multiple_choice",
       format={"options": SUPPORT_CHOICES, "type": "multi"}),
    _f("field_2851", "URL for Pixel to Add", "short_text"),
    _f("field_1819", "Describe Your Campaign Support Issue", "paragraph_text",
       required=True),
    _f("field_1820", "Uploaded Files", "file"),
    _f("field_1867", "Submitted Date", "date_time"),
    _f("field_2595", "Submitted By", "short_text"),
    _f("field_2596", "Media Partner", "connection",
       relationship={"object": "object_55", "has": "one"}),
    _f("field_2597", "Client", "connection",
       relationship={"object": "object_50", "has": "one"}),
    _f("field_2780", "Timeline", "multiple_choice",
       format={"options": TIMELINE_CHOICES, "type": "single"}),
    _f("field_2608", "Partner Contact", "short_text"),
    _f("field_2609", "Client Contact", "short_text"),
    _f("field_2610", "Notify Client?", "boolean"),
    _f("field_2611", "Notify Partner?", "boolean"),
    _f("field_2612", "Notes", "paragraph_text"),
    _f("field_2613", "IO#", "short_text"),
    _f("field_2614", "Campaign", "short_text"),
    _f("field_2786", "Rush", "boolean"),
    _f("field_2787", "Reason for Rush", "paragraph_text"),
    _f("field_2793", "IO Product", "connection",
       relationship={"object": "object_136", "has": "one"}),
    _f("field_3347", "IOP Status", "multiple_choice",
       format={"options": IOP_CHOICES, "type": "single"}),
    _f("field_3419", "Product", "short_text"),
]

# object_140 keeps its label matching — nobody has pinned it.
SCHEMA_140 = [
    _f("field_900", "Request Name", "short_text"),
    _f("field_901", "Describe the change", "paragraph_text"),
    _f("field_902", "Client", "short_text"),
    _f("field_903", "Campaign", "short_text"),
    _f("field_904", "IO Number", "short_text"),
    _f("field_905", "Requested By", "short_text"),
]


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


SENT = {"post": None, "url": None}
IDENTS = {"object_135": "field_70", "object_55": "field_71",
          "object_50": "field_72", "object_136": "field_73"}
RECORDS = {"object_135": IOS, "object_55": PARTNERS,
           "object_50": CUSTOMERS, "object_136": PRODUCTS}


class FakeRequests:
    """Knack, as far as this module is concerned."""

    RequestException = Exception

    @staticmethod
    def get(url, **kw):
        if url.endswith("/objects/object_121/fields"):
            return Resp(200, {"fields": SCHEMA_121})
        if url.endswith("/objects/object_140/fields"):
            return Resp(200, {"fields": SCHEMA_140})
        for obj, ident in IDENTS.items():
            if url.endswith(f"/objects/{obj}"):
                return Resp(200, {"object": {"identifier": ident}})
            if url.endswith(f"/objects/{obj}/records"):
                return Resp(200, {"records": RECORDS[obj]})
        return Resp(404, {})

    @staticmethod
    def post(url, **kw):
        SENT["post"] = kw.get("json")
        SENT["url"] = url
        return Resp(200, {"id": "9" * 24, **(kw.get("json") or {})})


knack_api.requests = FakeRequests


def reset():
    knack_api._schema_cache.clear()
    SENT["post"] = SENT["url"] = None


def main():
    print("the pinned ids are the ids the campaign team gave us")
    for fid, label, key in GIVEN:
        check(f"{label} ({key})", knack_api.SUPPORT_FIELDS.get(key), fid)
    check("no id is pinned twice",
          len(set(knack_api.SUPPORT_FIELDS.values())),
          len(knack_api.SUPPORT_FIELDS))
    check("and the list is the whole of it",
          sorted(knack_api.SUPPORT_FIELDS), sorted(k for _, _, k in GIVEN))
    ok("every key has a fallback label so a form can name it before Knack answers",
       all(k in knack_api.SUPPORT_LABELS for k in knack_api.SUPPORT_FIELDS))

    print()
    print("every one of them is reachable from a form")
    drawn = {k for _, keys in knack_api.SUPPORT_GROUPS for k in keys}
    for fid, label, key in GIVEN:
        ok(f"{label} is drawn", key in drawn or key in knack_api.SUPPORT_NOT_ASKED,
           "in neither SUPPORT_GROUPS nor SUPPORT_NOT_ASKED")
    check("the write set is the list without Uploaded Files",
          sorted(knack_api.SUPPORT_CREATE_FIELDS),
          sorted(k for _, _, k in GIVEN if k != "uploaded_files"))
    ok("Submitted By is written and not asked twice",
       "submitted_by" in knack_api.SUPPORT_CREATE_FIELDS
       and "submitted_by" in knack_api.SUPPORT_NOT_ASKED)

    print()
    print("an environment override moves an id without a release")
    os.environ["KNACK_SUPPORT_TIMELINE"] = "field_9999"
    try:
        check("timeline follows the variable",
              knack_api.support_field_ids().get("timeline"), "field_9999")
    finally:
        del os.environ["KNACK_SUPPORT_TIMELINE"]
    check("and goes back when it is unset",
          knack_api.support_field_ids().get("timeline"), "field_2780")

    print()
    print("the form is built from the live object, not from a guess")
    reset()
    fields = knack_api.campaign_form_fields("support")
    by_key = {f["key"]: f for f in fields}
    ok("every field the team named is on the form",
       {k for _, _, k in GIVEN if k not in knack_api.SUPPORT_NOT_ASKED}
       <= set(by_key), f"missing: {sorted({k for _, _, k in GIVEN} - set(by_key))}")
    ok("and Submitted By is not asked for twice",
       "submitted_by" not in by_key)
    check("Campaign Support offers Knack's own choices",
          by_key["support_type"]["choices"], SUPPORT_CHOICES)
    check("and takes more than one of them", by_key["support_type"]["control"], "multi")
    check("Timeline is a single-choice dropdown", by_key["timeline"]["control"], "select")
    check("with Knack's own choices", by_key["timeline"]["choices"], TIMELINE_CHOICES)
    check("IOP Status likewise", by_key["iop_status"]["choices"], IOP_CHOICES)
    check("Insertion Order is a picker", by_key["insertion_order"]["control"], "connection")
    check("offering real record ids",
          [c["label"] for c in by_key["insertion_order"]["choices"]],
          ["IO 4821 — Riverside HVAC", "IO 4822 — Riverside HVAC"])
    check("Media Partner too",
          [c["label"] for c in by_key["media_partner"]["choices"]],
          ["Cumulus Media", "iHeart"])
    check("Notify Client? is yes/no", by_key["notify_client"]["control"], "boolean")
    check("Due Date is a date", by_key["due_date"]["control"], "date")
    check("the issue is a text area", by_key["description"]["control"], "textarea")
    ok("and Knack's own required flag travels with it",
       by_key["description"]["required"] is True)
    check("IO# has nothing published, so it is a text box",
          by_key["io_number"]["control"], "text")

    print()
    print("...and Uploaded Files is drawn without pretending to be writable")
    check("it is on the form", by_key["uploaded_files"]["control"], "file")
    check("marked unwritable", by_key["uploaded_files"]["writable"], False)
    ok("and says where files actually go",
       "Knack" in by_key["uploaded_files"]["hint"])
    ok("it is not in the write set",
       "uploaded_files" not in knack_api.SUPPORT_CREATE_FIELDS)

    print()
    print("a support request carries what the form collected")
    reset()
    rec = knack_api.create_campaign_request(
        "support", "Riverside HVAC", "Connected TV", "4821",
        "Pixel not firing", "The conversion pixel stopped reporting.",
        author="todd", requested_by="Todd",
        values={"insertion_order": "IO 4821 — Riverside HVAC",
                "media_partner": "Cumulus Media",
                "support_type": ["Pixel", "Reporting"],
                "pixel_url": "https://riversidehvac.com/thanks",
                "due_date": "09/04/2026", "timeline": "This week",
                "rush": "yes", "rush_reason": "Flight starts Monday",
                "partner_contact": "Dana", "client_contact": "Sam",
                "notify_client": "yes", "notify_partner": "no",
                "notes": "They have already cleared cache.",
                "io_product": "Connected TV", "product": "Connected TV",
                "iop_status": "New", "submitted_date": "08/27/2026"})
    sent = SENT["post"]
    check("it went to the support object", SENT["url"].endswith("/objects/object_121/records"), True)
    check("insertion order, as a record id", sent.get("field_2593"), "a" * 24)
    check("media partner, likewise", sent.get("field_2596"), "c" * 24)
    check("client, resolved exactly", sent.get("field_2597"), "e" * 24)
    check("IO product", sent.get("field_2793"), "1" * 24)
    check("campaign support, both answers", sent.get("field_1818"), ["Pixel", "Reporting"])
    check("URL for pixel to add", sent.get("field_2851"), "https://riversidehvac.com/thanks")
    check("due date", sent.get("field_1859"), "09/04/2026")
    check("timeline", sent.get("field_2780"), "This week")
    check("rush", sent.get("field_2786"), True)
    check("reason for rush", sent.get("field_2787"), "Flight starts Monday")
    check("partner contact", sent.get("field_2608"), "Dana")
    check("client contact", sent.get("field_2609"), "Sam")
    check("notify client", sent.get("field_2610"), True)
    check("notify partner", sent.get("field_2611"), False)
    check("notes", sent.get("field_2612"), "They have already cleared cache.")
    check("IO#", sent.get("field_2613"), "4821")
    check("campaign", sent.get("field_2614"), "Connected TV")
    check("product", sent.get("field_3419"), "Connected TV")
    check("IOP status", sent.get("field_3347"), "New")
    check("submitted by", sent.get("field_2595"), "Todd")
    check("submitted date", sent.get("field_1867"), "08/27/2026")
    ok("the subject leads the issue, because this object has no subject field",
       str(sent.get("field_1819")).startswith("Pixel not firing"))
    ok("with the body and who sent it under it",
       "stopped reporting" in str(sent.get("field_1819"))
       and "todd" in str(sent.get("field_1819")))
    check("nothing was refused", rec.get("rejected"), [])
    check("and what was written is reported back — every writable field",
          len(rec.get("written") or []),
          len(knack_api.SUPPORT_CREATE_FIELDS))

    print()
    print("a value Knack would refuse is refused here, by name")
    reset()
    rec = knack_api.create_campaign_request(
        "support", "Riverside HVAC", "", "",
        "Pixel not firing", "Body",
        values={"timeline": "Whenever",                 # not one of its choices
                "media_partner": "Cumulus",             # matches no record
                "uploaded_files": "screenshot.png",     # a file field
                "notify_client": "maybe",               # not a yes or a no
                "made_up": "x"})                        # not a field at all
    refused = " | ".join(rec.get("rejected") or [])
    check("five refusals", len(rec.get("rejected") or []), 5)
    ok("the bad choice is named", "Timeline" in refused and "Whenever" in refused)
    ok("the unmatched connection is named", "Media Partner" in refused)
    ok("the file field says where files go", "uploaded" in refused.lower())
    ok("the bad boolean is named", "Notify Client?" in refused)
    ok("and a key this object has no field for", "made_up" in refused)
    ok("the request was still created — Knack refuses a record over one bad "
       "value, so the rest goes", bool(SENT["post"]))
    ok("with the issue on it", bool((SENT["post"] or {}).get("field_1819")))
    ok("and nothing was written for the refused fields",
       not any(k in (SENT["post"] or {})
               for k in ("field_2780", "field_2596", "field_1820", "field_2610")))

    print()
    print("a field that is not writable cannot be written by asking for it")
    reset()
    payload, rejected = knack_api._campaign_payload(
        "support", {"uploaded_files": "a.png"})
    check("nothing in the payload", payload, {})
    check("and it said why", len(rejected), 1)

    print()
    print("the change request is untouched — object_140 has no pinned ids")
    reset()
    info = knack_api.campaign_field_map("change")
    check("it still matches by label", info["object"], "object_140")
    check("subject", (info["map"].get("title") or {}).get("field"), "field_900")
    check("details", (info["map"].get("description") or {}).get("field"), "field_901")
    reset()
    rec = knack_api.create_campaign_request(
        "change", "Riverside HVAC", "Connected TV", "4821",
        "Raise the budget", "To $4,000 a month.", author="todd",
        requested_by="Todd")
    sent = SENT["post"]
    check("it went to the change object", SENT["url"].endswith("/objects/object_140/records"), True)
    check("subject", sent.get("field_900"), "Raise the budget")
    check("client", sent.get("field_902"), "Riverside HVAC")
    check("campaign", sent.get("field_903"), "Connected TV")
    check("IO number", sent.get("field_904"), "4821")
    check("requested by", sent.get("field_905"), "Todd")
    ok("details carry the body and who sent it",
       "$4,000" in str(sent.get("field_901")) and "todd" in str(sent.get("field_901")))
    check("nothing was refused", rec.get("rejected"), [])
    reset()
    change_fields = knack_api.campaign_form_fields("change")
    ok("and the change form comes back in the same shape",
       {"key", "control", "choices", "writable"} <= set(change_fields[0]))

    print()
    print("the form and the write agree about what a support request is")
    reset()
    form_keys = {f["key"] for f in knack_api.campaign_form_fields("support")}
    for key in knack_api.SUPPORT_CREATE_FIELDS:
        if key in knack_api.SUPPORT_NOT_ASKED:
            continue
        ok(f"{key} is drawn as well as written", key in form_keys,
           "in the write set and on no screen — the state this object was in")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
