"""The web ticket fields: the ids, the form built from them, and the writes.

Four things this protects, each of which has already gone wrong once in this
codebase or is one rename away from going wrong:

* **The ids are the ones the web team gave us.** They were pinned precisely
  because label matching broke silently when a label was renamed — so a typo
  in a pinned id has to fail here, not in Knack.
* **Every field on that list is reachable.** A field can be pinned, be
  writable, and still never be asked for: that is exactly the state Client 360
  was in, with the ids in the module and four boxes on the form. So each must
  be in the write set *and* in the drawing order.
* **Nothing else is written.** Eight fields, not the twenty this object
  carries. The rest stay pinned so they can be read and shown, and a form that
  asked for them anyway would be twenty questions for eight answers.
* **A value Knack would refuse is refused here, by name.** Knack rejects the
  whole record over one bad dropdown value. Caught here it costs the field and
  the caller is told; not caught, it costs the ticket.

Run directly: ``python3 test_web_tickets.py``. No pytest, no network — the
requests seam is stubbed, so this needs no Knack credentials and touches
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
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")


def ok(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        FAILURES.append(f"{label}{(': ' + detail) if detail else ''}")


# --------------------------------------------------------------------------
# The list the web team gave us, verbatim. This is the fixture the pinned ids
# are checked against — if Knack is restructured, this list changes first.
# --------------------------------------------------------------------------
GIVEN = [
    ("field_1895", "Ticket Title",              "title"),
    ("field_1784", "Client Organization",       "client"),
    ("field_1785", "Media Partner",             "media_partner"),
    ("field_2965", "Client Website URL",        "website"),
    ("field_2973", "Type of Ticket",            "type"),
    ("field_3160", "Revision Requires Billing", "billable"),
    ("field_1923", "Describe the changes",      "description"),
    ("field_1696", "Are you ready to submit?",  "ready_to_submit"),
]

# Pinned so they can be read and shown on the ticket list, and deliberately
# not written by the Hub. Asking for a field nobody fills is the failure this
# file exists to catch, in both directions.
READ_ONLY = [
    # Named on the list, then taken back off the form. The id stays pinned.
    ("field_2481", "partner_contact"),
    ("field_1761", "notify_partner"), ("field_3099", "web_services"),
    ("field_1675", "new_website_url"), ("field_3262", "build_website"),
    ("field_3264", "hosting_maint"), ("field_3266", "hourly_maint"),
    ("field_3263", "purchase_domain"), ("field_3265", "hosting_only"),
    ("field_3267", "ecommerce"), ("field_2968", "pause_reason"),
    ("field_2969", "cancel_reason"), ("field_2970", "cancel_date"),
    ("field_3010", "resume_date"), ("field_1657", "status"),
    ("field_1729", "developer"),
]

TYPE_CHOICES = ["New Website", "Website Change", "Pause", "Cancellation"]
SERVICE_CHOICES = ["Design", "Copywriting", "SEO", "Hosting"]

PARTNERS = [{"id": "a" * 24, "field_9": "Cumulus Media"},
            {"id": "b" * 24, "field_9": "iHeart"}]
CLIENTS = [{"id": "c" * 24, "field_8": "Riverside HVAC"},
           {"id": "d" * 24, "field_8": "Riverside HVAC LLC"}]


def _f(key, label, ftype, **extra):
    d = {"key": key, "label": label, "type": ftype, "required": False}
    d.update(extra)
    return d


SCHEMA = [
    _f("field_1895", "Ticket Title", "short_text", required=True),
    _f("field_1784", "Client Organization", "connection",
       relationship={"object": "object_50", "has": "one"}),
    _f("field_1785", "Media Partner", "connection",
       relationship={"object": "object_55", "has": "one"}),
    _f("field_2481", "Partner Contact", "short_text"),
    _f("field_1761", "Should Partner receive Notifications?", "boolean"),
    _f("field_2965", "Client Website URL", "short_text"),
    _f("field_2973", "Type of Ticket", "multiple_choice",
       format={"options": TYPE_CHOICES, "type": "single"}),
    _f("field_3160", "Revision Requires Billing", "multiple_choice",
       format={"options": ["Yes", "No"], "type": "single"}),
    _f("field_2968", "Pause Reason", "paragraph_text"),
    _f("field_2969", "Cancellation Reason", "paragraph_text"),
    _f("field_2970", "Cancellation Date", "date_time"),
    _f("field_3010", "Resume Date", "date_time"),
    _f("field_3099", "Web Services", "multiple_choice",
       format={"options": SERVICE_CHOICES, "type": "multi"}),
    _f("field_1675", "New Website URL", "short_text"),
    _f("field_3262", "Build Website", "boolean"),
    _f("field_3264", "Hosting and Maintenance", "boolean"),
    _f("field_3266", "Hourly Maintenance", "boolean"),
    _f("field_3263", "Purchase Domain", "boolean"),
    _f("field_3265", "Hosting Only", "boolean"),
    _f("field_3267", "Ecommerce", "boolean"),
    _f("field_1923", "Describe the changes", "paragraph_text"),
    _f("field_1696", "Are you ready to submit?", "multiple_choice",
       format={"options": ["Yes", "No, saving for later"], "type": "single"}),
    _f("field_1653", "Assigner", "short_text"),
    _f("field_1657", "Status", "multiple_choice",
       format={"options": ["Open", "In Progress", "Complete"], "type": "single"}),
    _f("field_1729", "Developer", "short_text"),
    _f("field_1000", "Date Created", "date_time"),
    _f("field_1001", "Requested By", "short_text"),
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


SENT = {"post": None, "put": None}


class FakeRequests:
    """Knack, as far as this module is concerned."""

    RequestException = Exception

    @staticmethod
    def get(url, **kw):
        if url.endswith("/objects/object_107/fields"):
            return Resp(200, {"fields": SCHEMA})
        if url.endswith("/objects/object_55"):
            return Resp(200, {"object": {"identifier": "field_9"}})
        if url.endswith("/objects/object_50"):
            return Resp(200, {"object": {"identifier": "field_8"}})
        if url.endswith("/objects/object_55/records"):
            return Resp(200, {"records": PARTNERS})
        if url.endswith("/objects/object_50/records"):
            return Resp(200, {"records": CLIENTS})
        if url.endswith("/objects/object_107/records"):
            return Resp(200, {"records": [{
                "id": "e" * 24,
                "field_1895": "Homepage banner swap",
                "field_1923": "Swap the hero image",
                "field_2965": "riversidehvac.com",
                "field_1657": "Open", "field_1657_raw": "Open",
                "field_1000": "01/15/2026",
                "field_2973": "Website Change", "field_2973_raw": "Website Change",
                "field_3160": "Yes", "field_3160_raw": "Yes",
                "field_1784": '<span>Riverside HVAC</span>',
                "field_1784_raw": [{"id": "c" * 24, "identifier": "Riverside HVAC"}],
                "field_3099_raw": ["Design", "SEO"],
                "field_3099": "Design, SEO",
                "field_3262_raw": True,
            }]})
        return Resp(404, {})

    @staticmethod
    def post(url, **kw):
        SENT["post"] = kw.get("json")
        return Resp(200, {"id": "f" * 24, **(kw.get("json") or {})})

    @staticmethod
    def put(url, **kw):
        SENT["put"] = kw.get("json")
        return Resp(200, {"id": "e" * 24})


knack_api.requests = FakeRequests


def reset():
    knack_api._schema_cache.clear()
    SENT["post"] = SENT["put"] = None


def main():
    print("the pinned ids are the ids the web team gave us")
    for fid, label, key in GIVEN:
        check(f"{label} ({key})", knack_api.TICKET_FIELDS.get(key), fid)
    check("no id is pinned twice",
          len(set(knack_api.TICKET_FIELDS.values())), len(knack_api.TICKET_FIELDS))

    print()
    print("every one of them is reachable from a form")
    drawn = {k for _, keys in knack_api.TICKET_GROUPS for k in keys}
    for fid, label, key in GIVEN:
        ok(f"{label} is written on create", key in knack_api.TICKET_CREATE_FIELDS,
           "missing from TICKET_CREATE_FIELDS")
        ok(f"{label} is drawn", key in drawn, "missing from TICKET_GROUPS")
    check("the create set is exactly the list",
          sorted(knack_api.TICKET_CREATE_FIELDS), sorted(k for _, _, k in GIVEN))
    check("and Manage edits it without Title",
          sorted(knack_api.TICKET_MANAGE_FIELDS),
          sorted(k for _, _, k in GIVEN if k != "title"))
    ok("every writable key has a label",
       all(k in knack_api.TICKET_LABELS for k in knack_api.TICKET_CREATE_FIELDS))

    print()
    print("the rest of the object stays readable and unwritten")
    for fid, key in READ_ONLY:
        ok(f"{key} is still pinned", knack_api.TICKET_FIELDS.get(key) == fid,
           f"expected {fid}, got {knack_api.TICKET_FIELDS.get(key)}")
        ok(f"{key} is not written", key not in knack_api.TICKET_CREATE_FIELDS
           and key not in knack_api.TICKET_MANAGE_FIELDS,
           "it is in a write set — the form would ask for it")

    print()
    print("the form is built from the live object, not from a guess")
    reset()
    fields = knack_api.ticket_form_fields("create")
    by_key = {f["key"]: f for f in fields}
    check("eight fields, no more", len(fields), 8)
    check("Type of Ticket is a dropdown", by_key["type"]["control"], "select")
    check("with Knack's own choices", by_key["type"]["choices"], TYPE_CHOICES)
    check("Media Partner is a picker", by_key["media_partner"]["control"], "connection")
    check("offering real record ids",
          [c["label"] for c in by_key["media_partner"]["choices"]],
          ["Cumulus Media", "iHeart"])
    check("Describe the changes is a text area", by_key["description"]["control"], "textarea")
    check("the submit gate offers Knack's answers",
          by_key["ready_to_submit"]["choices"], ["Yes", "No, saving for later"])
    check("and it is drawn last", fields[-1]["key"], "ready_to_submit")
    ok("every create field is drawn",
       {f["key"] for f in fields} == set(knack_api.TICKET_CREATE_FIELDS))
    ok("Title is not editable in Manage",
       "title" not in {f["key"] for f in knack_api.ticket_form_fields("manage")})

    print()
    print("a ticket carries what the form collected")
    reset()
    rec = knack_api.create_ticket(
        "Riverside HVAC", "riversidehvac.com", "Homepage banner swap",
        "Swap the hero image", author="todd", requested_by="Todd",
        values={"type": "Website Change", "billable": "Yes",
                "media_partner": "Cumulus Media", "ready_to_submit": "Yes"})
    sent = SENT["post"]
    check("ticket title", sent.get("field_1895"), "Homepage banner swap")
    check("client website URL", sent.get("field_2965"), "riversidehvac.com")
    check("type of ticket", sent.get("field_2973"), "Website Change")
    check("revision requires billing", sent.get("field_3160"), "Yes")
    check("are you ready to submit", sent.get("field_1696"), "Yes")
    ok("describe the changes carries the body and who sent it",
       "Swap the hero image" in str(sent.get("field_1923"))
       and "todd" in str(sent.get("field_1923")))
    check("nothing was refused", rec.get("rejected"), [])

    print("...and the attribution nobody types")
    check("assigner", sent.get("field_1653"), "Todd")
    check("requested by", sent.get("field_1001"), "Todd")

    print()
    print("a connection is written as a record id, never as a name")
    check("media partner", sent.get("field_1785"), "a" * 24)
    check("client organization", sent.get("field_1784"), "c" * 24)

    print("...and a name that matches two records is refused, not guessed")
    reset()
    rec = knack_api.create_ticket("Riverside", "riversidehvac.com", "Ambiguous",
                                  "body", values={"client": "Riverside"})
    ok("client not written", "field_1784" not in (SENT["post"] or {}))
    ok("and said so", any("Client Organization" in r for r in rec.get("rejected") or []),
       str(rec.get("rejected")))

    print()
    print("a value Knack would refuse costs the field, not the ticket")
    reset()
    rec = knack_api.create_ticket("Riverside HVAC", "riversidehvac.com",
                                  "Bad dropdown", "body",
                                  values={"type": "Something Else",
                                          "billable": "No"})
    ok("the ticket was still created", bool(rec.get("id")))
    ok("the bad choice never reached Knack", "field_2973" not in (SENT["post"] or {}))
    check("the good field did", (SENT["post"] or {}).get("field_3160"), "No")
    ok("and the refusal names the field and the value",
       any("Type of Ticket" in r and "Something Else" in r
           for r in rec.get("rejected") or []), str(rec.get("rejected")))

    print()
    print("a field off the list is refused rather than written")
    reset()
    rec = knack_api.create_ticket("Riverside HVAC", "riversidehvac.com",
                                  "Off the list", "body",
                                  values={"web_services": ["Design"],
                                          "build_website": "yes",
                                          "new_website_url": "https://x.example.com"})
    for fid in ("field_3099", "field_3262", "field_1675"):
        ok(f"{fid} not written", fid not in (SENT["post"] or {}))
    check("all three said so", len(rec.get("rejected") or []), 3)

    print()
    print("Manage writes only what Manage may write")
    reset()
    res = knack_api.update_ticket("e" * 24, {
        "type": "Pause", "billable": "No", "ready_to_submit": "Yes",
        "title": "renamed", "status": "Complete", "id": "nope"})
    check("type of ticket", (SENT["put"] or {}).get("field_2973"), "Pause")
    check("revision requires billing", (SENT["put"] or {}).get("field_3160"), "No")
    check("are you ready to submit", (SENT["put"] or {}).get("field_1696"), "Yes")
    ok("the title was not renamed", "field_1895" not in (SENT["put"] or {}))
    ok("the status was not overwritten", "field_1657" not in (SENT["put"] or {}))
    ok("and neither was anything unknown", "nope" not in json.dumps(SENT["put"] or {}))
    ok("all three refusals are reported", len(res.get("rejected") or []) == 3,
       str(res.get("rejected")))
    check("the update succeeded", res.get("ok"), True)

    print()
    print("a listed ticket opens the form on what it already holds")
    reset()
    rows = knack_api.list_tickets("Riverside HVAC", "riversidehvac.com")
    vals = rows[0]["values"]
    check("the connection prefills as an id", vals["client"], "c" * 24)
    check("the dropdown as its choice", vals["type"], "Website Change")
    ok("and nothing off the list is offered for editing",
       not ({"web_services", "build_website", "status"} & set(vals)))
    check("but the table can still show the type", rows[0]["shown"]["type"], "Website Change")
    check("and the status Knack set", rows[0]["status"], "Open")

    print()
    print("the audit module at /tools/tickets reads the same ids")
    # Two maps of one object is the duplication CLAUDE.md flagged. There is one
    # now: the audit module's names, the shared module's ids. This asserts the
    # translation, so a pinned id that moves cannot leave the report reading a
    # field that no longer means what its column says.
    from modules.tickets import config as tickets_config
    ids = knack_api.field_ids()
    check("the audit maps the same object",
          tickets_config.TICKETS_OBJECT, knack_api.TICKETS_OBJECT)
    for mine, theirs in tickets_config._SHARED.items():
        ok(f"{mine} is {theirs}",
           tickets_config.CONFIRMED_FIELDS.get(mine) == ids.get(theirs),
           f"{tickets_config.CONFIRMED_FIELDS.get(mine)} != {ids.get(theirs)}")
    ok("and it holds nothing the shared map does not",
       set(tickets_config.CONFIRMED_FIELDS.values()) <= set(ids.values()))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("the eight web ticket fields hold: pinned, drawn, and written as Knack wants them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
