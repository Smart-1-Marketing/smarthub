"""Quote numbering, draft deletion, and one name that must not come back.

    python3 test_quote_numbers.py

House style: no pytest, no new dependencies, a temporary data directory and a
throwaway SQLite database, so it never touches /var/data or the real one.

## Why this file exists

Three separate things, each of which fails quietly rather than loudly:

  1. **An uploaded proposal had no number.** Its only identity was a 16-hex
     uuid, and the Proposal Builder printed the literal word "Uploaded" in the
     Quote # column. Half the pipeline could not be referred to out loud.
     Uploaded proposals now carry a U- number AND stay labelled as uploaded —
     a number that says where the document came from, not one that disguises
     it as a quote this tool wrote.

  2. **There was no way to delete a draft.** No DELETE route existed at all.
     Now there is one, and it takes drafts only: a Sent quote is one a client
     has read and a Converted one is the paper trail behind a live campaign,
     so those are refused by the SERVER, not merely hidden in the UI.

  3. **A staff member's name shipped as a placeholder** on the radio promo
     form, which put one person's name in front of every script written in
     that tool. It is asserted gone here because a placeholder is exactly the
     sort of thing that gets pasted back in by a later edit.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1quotes_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "quotes-test-secret"
os.environ["PANEL_PASSWORD"] = "quotes-test-password"
os.environ.pop("CLOUDINARY_URL", None)          # exercise the on-disk path

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


from werkzeug.test import Client                                # noqa: E402
from wsgi import application                                    # noqa: E402
from hub import auth, proposals                                 # noqa: E402

http = Client(application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test"), domain="localhost")


# ------------------------------------------------- 1. uploaded quote numbers
section("An uploaded proposal gets a quote number")

first = proposals.add_proposal("Riverside HVAC", "summer.pdf", b"%PDF-1.4 one",
                               date_sent="2026-03-01", title="Summer", actor="Test")
second = proposals.add_proposal("Riverside HVAC", "fall.pdf", b"%PDF-1.4 two",
                                date_sent="2026-06-01", title="Fall", actor="Test")

check("an upload is numbered at all", bool(first.get("quote_number")), True)
check("in the U- series, so the number itself says it was uploaded",
      first["quote_number"].startswith("U-"), True)
check("starting at U-10200, parallel to the Q-10200 quote series",
      first["quote_number"], "U-10200")
check("and the next one increments", second["quote_number"], "U-10201")
check("numbers are unique",
      len({first["quote_number"], second["quote_number"]}), 2)

# The number is an addition, not a replacement: the record must still say
# plainly that it came from outside this tool.
check("the record still marks itself uploaded", first.get("source"), "uploaded")
check("a U- number is never mistaken for a built quote's Q- number",
      first["quote_number"].startswith("Q-"), False)


section("A proposal uploaded before numbering existed gets one on read")

items = proposals.list_proposals("Riverside HVAC")
for item in items:
    item.pop("quote_number", None)
proposals._write("Riverside HVAC", items)
check("the fixture really has no numbers",
      [i.get("quote_number") for i in
       proposals.list_proposals("Riverside HVAC", backfill=False)], [None, None])

back = proposals.list_proposals("Riverside HVAC")
check("reading the list numbers them", all(i.get("quote_number") for i in back), True)

# Oldest document, lowest number — a series that runs backwards against the
# dates would be worse than no series.
by_date = sorted(back, key=lambda i: i["date_sent"])
check("oldest gets the lower number",
      by_date[0]["quote_number"] < by_date[-1]["quote_number"], True)

# Written back, not recomputed: a number that changed on every page load
# would be useless as a reference.
persisted = [i["quote_number"] for i in
             proposals.list_proposals("Riverside HVAC", backfill=False)]
check("the backfill persists", all(persisted), True)
check("and the numbers are stable across reads",
      [i["quote_number"] for i in proposals.list_proposals("Riverside HVAC")],
      persisted)

third = proposals.add_proposal("Acme Roofing", "acme.pdf", b"%PDF-1.4 three",
                               date_sent="2026-07-01", title="Acme", actor="Test")
check("the counter does not restart per client",
      third["quote_number"] in persisted, False)


# ------------------------------------------------------ 2. deleting a draft
section("A draft can be deleted; anything else cannot")

r = http.post("/sales/builder/api/quotes", json={"data": {"client": "Riverside HVAC"}})
check("a quote is created", r.status_code, 200)
draft = r.get_json()["quote"]
check("as a draft", draft["status"], "Draft")
check("with a Q- number", draft["quote_number"].startswith("Q-"), True)

r = http.delete(f"/sales/builder/api/quotes/{draft['id']}")
check("the draft deletes", r.status_code, 200)
check("and reports which one went", r.get_json()["deleted"], draft["quote_number"])
check("it is really gone",
      http.get(f"/sales/builder/api/quotes/{draft['id']}").status_code, 404)

r = http.post("/sales/builder/api/quotes", json={"data": {"client": "Acme Roofing"}})
sent = r.get_json()["quote"]
http.put(f"/sales/builder/api/quotes/{sent['id']}", json={"status": "Sent"})
r = http.delete(f"/sales/builder/api/quotes/{sent['id']}")
# Refused by the SERVER. Hiding the button would leave the route open to
# anyone who found it, and a sent quote is a document a client has read.
check("a sent quote is refused", r.status_code, 409)
check("with a reason that says what to do instead",
      "Lost or Expired" in r.get_json()["error"], True)
check("and it survives",
      http.get(f"/sales/builder/api/quotes/{sent['id']}").status_code, 200)

for status in ("Approved", "Converted", "Lost", "Expired"):
    http.put(f"/sales/builder/api/quotes/{sent['id']}", json={"status": status})
    check(f"a {status.lower()} quote is refused too",
          http.delete(f"/sales/builder/api/quotes/{sent['id']}").status_code, 409)

check("deleting a quote that does not exist is a 404, not a 500",
      http.delete("/sales/builder/api/quotes/999999").status_code, 404)


section("The delete control is offered only where the server allows it")

page = http.get("/sales/builder/").get_data(as_text=True)
check("the page carries a delete handler", "function deleteQuote" in page, True)
check("the row icon is drawn for drafts only",
      "x.status==='Draft'?`<button class=\"ic\" title=\"Delete this draft\"" in page, True)
check("and it warns before deleting", "cannot be undone" in page, True)


# --------------------------------------------------------- 3. the name
section("One name does not come back")

# A real person's name as a placeholder reads as a suggestion. This is a
# regression test, not a style check: the field is optional and needs no
# example, and a later edit re-adding one would go unnoticed.
hits = []
for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
        continue
    if path.name == Path(__file__).name:
        continue
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        continue
    if "Cordle" in text:
        hits.append(str(path.relative_to(ROOT)))
check("the name appears nowhere in the repo", hits, [])

promo = http.get("/tools/radio-promo/").get_data(as_text=True)
check("the radio promo page still renders", bool(promo.strip()), True)
check("the team member field is still there", 'id="team_member"' in promo, True)
check("and carries no name placeholder",
      'id="team_member" placeholder=' in promo.replace("\n", " "), False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
