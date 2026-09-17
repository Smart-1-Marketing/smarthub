"""hub/qb_contacts.py -- the QuickBooks billing contact, filed on the record.

    python3 test_qb_contacts.py

Same shape as the other test files here -- no pytest, no new dependencies,
a throwaway data directory and database so nothing touches /var/data.

What it holds, in the order the module states its rules:

  * The window is Sunday 2pm Eastern, weekly: a Tuesday is due against a
    Saturday run and not against a Sunday-evening one; never-run is due;
    a run in the future is due.
  * The merge never writes over a typed value, fills only blanks on a
    match, refreshes the row it filed itself, files under `accounting`,
    needs no name, and skips a customer with nothing to file.
  * sync_client reads the attached customers through a stubbed QuickBooks:
    a first run adds, a second is unchanged, a refusal is `ok: False` and
    stops the sweep; the only contact on a record becomes primary.
  * The scheduler carries the job, hourly, deciding inside.
  * The contact levels Client 360 offers are the ones asked for.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1qbcontacts_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import qb_contacts, seo, scheduler, quickbooks  # noqa: E402

UTC = timezone.utc

# ---------------------------------------------------------------------------
section("The window: Sunday 2pm Eastern, weekly")
# 2026-09-13 is a Sunday. 14:00 EDT == 18:00 UTC.
sun_before = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)      # 1pm ET Sunday
sun_after = datetime(2026, 9, 13, 18, 30, tzinfo=UTC)      # 2:30pm ET Sunday
tue = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
check("a Tuesday's last window is the Sunday before, 2pm ET",
      qb_contacts.last_window(tue).astimezone(UTC) == datetime(2026, 9, 13, 18, 0, tzinfo=UTC),
      qb_contacts.last_window(tue))
check("Sunday 1pm ET has not reached this week's window yet",
      qb_contacts.last_window(sun_before).astimezone(UTC) == datetime(2026, 9, 6, 18, 0, tzinfo=UTC))
check("Sunday 2:30pm ET has",
      qb_contacts.last_window(sun_after).astimezone(UTC) == datetime(2026, 9, 13, 18, 0, tzinfo=UTC))
check("never run is due", qb_contacts.due(None, now=tue) is True)
check("a Saturday run is due on Tuesday",
      qb_contacts.due(datetime(2026, 9, 12, 12, 0, tzinfo=UTC), now=tue) is True)
check("a Sunday-evening run is not due on Tuesday",
      qb_contacts.due(datetime(2026, 9, 13, 20, 0, tzinfo=UTC), now=tue) is False)
check("a run in the future is due (a clock that moved)",
      qb_contacts.due(datetime(2026, 9, 20, 12, 0, tzinfo=UTC), now=tue) is True)
with mock.patch.dict(os.environ, {"QB_CONTACTS_SYNC_HOUR": "9"}):
    check("the hour follows its env override",
          qb_contacts.last_window(tue, hour=9).astimezone(UTC).hour == 13
          and qb_contacts.due(datetime(2026, 9, 13, 12, 0, tzinfo=UTC), now=tue) is True)

# ---------------------------------------------------------------------------
section("merge_contact: the rules, on their own")
found = {"id": "42", "name": "Pat Jones", "email": "AP@Client.com", "phone": "614-555-0100"}
rows, out = qb_contacts.merge_contact([], found)
check("a new contact is added", out == "added" and len(rows) == 1)
check("...under the accounting role, not primary",
      rows[0]["role"] == "accounting" and rows[0]["primary"] is False)
check("...marked as QuickBooks' own row",
      rows[0]["source"] == "quickbooks" and rows[0]["source_id"] == "42")
rows2, out2 = qb_contacts.merge_contact(rows, found)
check("the same customer again is unchanged", out2 == "unchanged" and rows2 == rows)
rows3, out3 = qb_contacts.merge_contact(rows, dict(found, phone="614-555-0199"))
check("a changed phone on QuickBooks' own row is refreshed",
      out3 == "updated" and rows3[0]["phone"] == "614-555-0199")

typed = [{"name": "Patricia Jones", "email": "ap@client.com", "phone": "", "primary": True,
          "role": "owner", "source": "", "source_id": ""}]
rows4, out4 = qb_contacts.merge_contact(typed, found)
check("a typed contact with the same email is matched, not duplicated",
      out4 == "updated" and len(rows4) == 1)
check("...its blank phone is filled", rows4[0]["phone"] == "614-555-0100")
check("...its typed name is never overwritten", rows4[0]["name"] == "Patricia Jones")
check("...its typed role is kept", rows4[0]["role"] == "owner" and rows4[0]["primary"] is True)
rows5, out5 = qb_contacts.merge_contact([{"name": "Pat Jones", "email": "", "phone": "",
                                         "primary": True, "role": "", "source": "", "source_id": ""}],
                                        {"id": "7", "name": "pat jones", "email": "", "phone": "555"})
check("with no email, a name match fills the phone and files the role",
      out5 == "updated" and rows5[0]["phone"] == "555" and rows5[0]["role"] == "accounting")
rows6, out6 = qb_contacts.merge_contact([], {"id": "9", "name": "", "email": "billing@x.com", "phone": ""})
check("a customer with no person name is still filed by its email",
      out6 == "added" and rows6[0]["email"] == "billing@x.com" and rows6[0]["name"] == "")
rows7, out7 = qb_contacts.merge_contact([], {"id": "9", "name": "", "email": "", "phone": ""})
check("a customer with nothing to file is skipped", out7 == "skipped" and rows7 == [])

# ---------------------------------------------------------------------------
section("quickbooks.customer_contact reads the person, not the company")
raw = {"Id": "42", "DisplayName": "Acme Widgets", "CompanyName": "Acme Widgets",
       "GivenName": "Pat", "FamilyName": "Jones",
       "PrimaryEmailAddr": {"Address": "ap@acme.com"},
       "PrimaryPhone": {"FreeFormNumber": "614-555-0100"}}
with mock.patch.object(quickbooks, "_query", return_value={"Customer": [raw]}):
    got = quickbooks.customer_contact("42")
check("name, email and phone come off the customer",
      got == {"id": "42", "customer": "Acme Widgets", "company": "Acme Widgets",
              "name": "Pat Jones", "email": "ap@acme.com", "phone": "614-555-0100"}, got)
raw2 = {"Id": "43", "DisplayName": "Acme Widgets", "CompanyName": "Acme Widgets",
        "Mobile": {"FreeFormNumber": "614-555-0101"}}
with mock.patch.object(quickbooks, "_query", return_value={"Customer": [raw2]}):
    got2 = quickbooks.customer_contact("43")
check("a company with no person leaves the name blank and takes the mobile",
      got2["name"] == "" and got2["phone"] == "614-555-0101" and got2["email"] == "")
raw3 = {"Id": "44", "DisplayName": "Jane Doe", "CompanyName": "Doe Bakery"}
with mock.patch.object(quickbooks, "_query", return_value={"Customer": [raw3]}):
    got3 = quickbooks.customer_contact("44")
check("a DisplayName that is a person, not the company, is the name", got3["name"] == "Jane Doe")
with mock.patch.object(quickbooks, "_query", return_value={"Customer": []}):
    check("an id that matches nothing is None", quickbooks.customer_contact("0") is None)

# ---------------------------------------------------------------------------
section("sync_client against a stubbed QuickBooks")
CLIENT = "Acme Widgets"
seo.set_link(CLIENT, "qb", {"id": "42", "name": "Acme Widgets"})
check("the client is on the sweep's list", CLIENT in qb_contacts.clients_with_customers())
check("a client with no customer attached is not",
      "Nobody Inc" not in qb_contacts.clients_with_customers())

with mock.patch.object(quickbooks, "configured", return_value=True), \
     mock.patch.object(quickbooks, "connected", return_value=True), \
     mock.patch.object(quickbooks, "customer_contact",
                       return_value={"id": "42", "name": "Pat Jones", "email": "ap@acme.com",
                                     "phone": "614-555-0100"}):
    res = qb_contacts.sync_client(CLIENT, actor="test")
check("first run files one contact", res["ok"] and res["added"] == 1, res)
prof = seo.get_profile(CLIENT)
check("the record now carries it, under accounting",
      len(prof["contacts"]) == 1 and prof["contacts"][0]["role"] == "accounting")
check("...and, as the only contact, it is the primary", prof["contacts"][0]["primary"] is True)
with mock.patch.object(quickbooks, "configured", return_value=True), \
     mock.patch.object(quickbooks, "connected", return_value=True), \
     mock.patch.object(quickbooks, "customer_contact",
                       return_value={"id": "42", "name": "Pat Jones", "email": "ap@acme.com",
                                     "phone": "614-555-0100"}):
    res2 = qb_contacts.sync_client(CLIENT, actor="test")
check("second run changes nothing", res2["ok"] and res2["unchanged"] == 1 and res2["added"] == 0, res2)
check("...and adds no second row", len(seo.get_profile(CLIENT)["contacts"]) == 1)

with mock.patch.object(quickbooks, "configured", return_value=True), \
     mock.patch.object(quickbooks, "connected", return_value=True), \
     mock.patch.object(quickbooks, "customer_contact", side_effect=RuntimeError("401 token expired")):
    res3 = qb_contacts.sync_client(CLIENT, actor="test")
check("a refusal is ok: False, refused, with the reason",
      res3["ok"] is False and res3["refused"] and "token expired" in res3["errors"][0], res3)
with mock.patch.object(quickbooks, "configured", return_value=False):
    res4 = qb_contacts.sync_client(CLIENT, actor="test")
check("an unconfigured QuickBooks is named, not a silent zero",
      res4["ok"] is False and "not connected" in res4["errors"][0])
check("a client with no customer attached is ok with nothing to do",
      qb_contacts.sync_client("Nobody Inc")["customers"] == 0)

# ---------------------------------------------------------------------------
section("sweep: decides inside the window, records its run")
with mock.patch.object(quickbooks, "configured", return_value=True), \
     mock.patch.object(quickbooks, "connected", return_value=True), \
     mock.patch.object(quickbooks, "customer_contact",
                       return_value={"id": "42", "name": "Pat Jones", "email": "ap@acme.com",
                                     "phone": "614-555-0100"}):
    forced = qb_contacts.sweep(force=True, now=tue)
    check("a forced sweep runs over the attached clients",
          forced["ran"] and forced["clients"] >= 1, forced)
    check("...and records when", qb_contacts.sweep_state()["last_run_at"] == tue.isoformat(timespec="seconds"))
    again = qb_contacts.sweep(now=datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
    check("the next tick inside the same week is not due", not again["ran"] and again["skipped"] == "Not due yet.")
    nxt = qb_contacts.sweep(now=datetime(2026, 9, 20, 19, 0, tzinfo=UTC))
    check("the following Sunday afternoon is", nxt["ran"] is True, nxt)
with mock.patch.object(quickbooks, "configured", return_value=False):
    off = qb_contacts.sweep(force=True, now=tue)
check("an unconnected QuickBooks skips with the reason",
      not off["ran"] and "not connected" in off["skipped"])

# ---------------------------------------------------------------------------
section("The scheduler carries it, hourly, deciding inside")
every, fn, desc = scheduler.JOBS["qb_contacts"]
check("hourly tick", every == 60)
check("the job function", fn.__name__ == "job_qb_contacts")
check("the description says when", "Sunday" in desc)

# ---------------------------------------------------------------------------
section("The contact levels, and one primary")
keys = [k for k, _ in seo.CONTACT_ROLES]
for want in ("communicate", "owner", "accounting", "reporting", "consultant", "do_not_email", "other"):
    check(f"level {want} is offered", want in keys)
cleaned = seo.clean_contacts([
    {"name": "A", "email": "a@x.com", "primary": True, "role": "owner"},
    {"name": "B", "email": "b@x.com", "primary": True, "role": "Do Not Email"},
    {"name": "", "email": "", "phone": ""},
    {"name": "C", "email": "c@x.com", "role": "nonsense"},
])
check("blank rows are dropped", len(cleaned) == 3)
check("exactly one primary survives", [c["primary"] for c in cleaned] == [True, False, False])
check("a role is normalized to its key", cleaned[1]["role"] == "do_not_email")
check("an unknown role is blank", cleaned[2]["role"] == "")

SRC = open(os.path.join(ROOT, "hub", "__init__.py"), encoding="utf-8").read()
check("the record has a button route for the same pass",
      '@app.route("/api/client/profile/qb-sync", methods=["POST"])' in SRC)
check("the profile route hands the levels to the modal", '"contact_roles"' in SRC)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
