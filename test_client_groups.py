"""hub/client_groups.py — one company, several client records.

    python3 test_client_groups.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Every external reader is stubbed, so it needs no
Knack, QuickBooks or Cloudinary credentials and reaches no third party.

## What is worth asserting

Grouping merges one company's records into another's page. Every way that can
go wrong is quiet:

  * **A wrong member is a wrong bill.** Members resolve by canonical domain or
    exact normalised name, never a substring — "Riverside HVAC" must not
    collect "Riverside HVAC Supply". This is `client_key.resolve()`'s rule and
    the one the billing audit broke.

  * **A double count reads exactly like a real number.** A product filed under
    the organisation name is found under the parent *and* the member; merged
    twice it doubles the "Active billing" figure in the header, and nothing on
    the page says so.

  * **The group must read the same from either end.** A relationship visible
    only from the parent means half the staff see it and half do not, which is
    the situation grouping exists to end.

  * **Two groups must never claim one client.** "Whose bill is this on?" needs
    one answer, and an aggregate built from an arbitrary pick of two is a
    number nobody can reproduce.

  * **Merged rows keep their own name.** The group is a billing relationship,
    not a rename: work done for Fast Fingerprints has to keep reading as Fast
    Fingerprints' work on National Background Check's record — and a proposal
    merged in from another member has to be written back to *that* record.

  * **Removing the group changes no client record.** This is a Hub overlay;
    Knack owns the client records and nothing here writes to them.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-clientgroups-")
# Set, not setdefault: this file always gets its own throwaway mirror, so it is
# safe to re-run in a job whose DATABASE_URL is already a real Postgres.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
# audit.py falls back to <repo>/data when this is unset, which both pollutes
# the checkout and makes the work-log assertions below depend on whatever a
# previous run happened to leave there.
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")
os.environ.setdefault("SECRET_KEY", "client-groups-test")
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


from hub import client_brand, client_groups, knack_data            # noqa: E402

PARENT = "National Background Check"
MEMBER = "Fast Fingerprints"
OTHER = "Riverside HVAC Supply"


def reset():
    for g in list(client_groups.groups()):
        client_groups.remove_member(str((g.get("parent") or {}).get("name") or ""))


# ---------------------------------------------------------------------------
section("An ungrouped client is unchanged")
# ---------------------------------------------------------------------------
reset()
r = client_groups.roster(PARENT)
check("roster answers rather than returning None", isinstance(r, dict))
check("and says it is not grouped", r["grouped"] is False)
check("names holds only this client, so callers need no branch",
      r["names"] == [PARENT], r["names"])
check("there is nothing to merge", r["others"] == [])


# ---------------------------------------------------------------------------
section("Grouping reads the same from either end")
# ---------------------------------------------------------------------------
res = client_groups.add_member(PARENT, MEMBER, actor="test@smart1")
check("the member attaches", res.get("ok") is True, res)

from_parent = client_groups.roster(PARENT)
from_member = client_groups.roster(MEMBER)
check("the parent's record shows the group", from_parent["grouped"] is True)
check("so does the member's", from_member["grouped"] is True)
check("both name the same parent",
      from_parent["parent"]["name"] == from_member["parent"]["name"] == PARENT)
check("the parent knows it is the parent", from_parent["is_parent"] is True)
check("the member knows it is not", from_member["is_parent"] is False)
check("each roster puts the record on screen first",
      from_parent["names"][0] == PARENT and from_member["names"][0] == MEMBER,
      (from_parent["names"], from_member["names"]))
check("and both cover the whole group",
      sorted(from_parent["names"]) == sorted(from_member["names"]) == sorted([PARENT, MEMBER]))
check("attaching the same member twice is not an error and does not duplicate",
      client_groups.add_member(PARENT, MEMBER).get("ok") is True
      and len(client_groups.roster(PARENT)["names"]) == 2)


# ---------------------------------------------------------------------------
section("Members match exactly, never on a substring")
# ---------------------------------------------------------------------------
check("legal suffixes and punctuation are ignored",
      client_groups.same_record("Fast Fingerprints, LLC", "", MEMBER, ""))
check("a longer name that merely contains this one is a different company",
      not client_groups.same_record("Riverside HVAC", "", OTHER, ""))
check("so the roster does not pick it up",
      client_groups.roster(OTHER)["grouped"] is False)
check("a shared canonical domain is a match, scheme and www and all",
      client_groups.same_record("Anything", "https://WWW.Example.com/about?x=1",
                                "Something Else", "example.com"))
check("grouping the same record under itself is refused, not silently stored",
      "error" in client_groups.add_member(PARENT, "The National Background Check Co."))


# ---------------------------------------------------------------------------
section("Two groups must never claim one client")
# ---------------------------------------------------------------------------
res = client_groups.add_member(OTHER, MEMBER)
check("a client already in a group cannot be attached to a second one",
      "error" in res, res)
check("and the refusal names the group that already holds them",
      PARENT in res.get("error", ""), res.get("error"))
check("nothing was written by the refusal",
      client_groups.roster(MEMBER)["parent"]["name"] == PARENT)


# ---------------------------------------------------------------------------
section("Merging counts a shared row once, and labels what it merged")
# ---------------------------------------------------------------------------
rows_a = [{"io": "1001", "monthly": 500}, {"io": "1002", "monthly": 250}]
rows_b = [{"io": "1002", "monthly": 250}, {"io": "1003", "monthly": 100}]
key = lambda r: str(r.get("io"))                                  # noqa: E731
out, seen = client_groups.merge_rows(rows_a, key, into=[])
client_groups.merge_rows(rows_b, key, member=MEMBER, into=out, seen=seen)
check("the row both records carry appears once",
      [r["io"] for r in out] == ["1001", "1002", "1003"], [r["io"] for r in out])
check("so the total is not doubled",
      sum(r["monthly"] for r in out) == 850)
check("a genuinely merged row says which record it came from",
      out[2].get("member") == MEMBER)
check("a row that was already there is not relabelled as somebody else's",
      out[1].get("member") is None)


# ---------------------------------------------------------------------------
section("Client 360 reads products, creative and websites across the group")
# ---------------------------------------------------------------------------
PRODUCTS = [
    {"client": PARENT, "organization": PARENT, "product": "Programmatic - Targeted",
     "campaign": "Spring", "io": "5001", "status": "Live", "monthly": "1,000",
     "start": "2026-01-01", "creative_urls": ["https://drive.google.com/file/d/aaa/view"]},
    # Filed under the member's own name.
    {"client": MEMBER, "organization": PARENT, "product": "Display - Category",
     "campaign": "Prints", "io": "5002", "status": "Live", "monthly": "400",
     "start": "2026-02-01", "creative_urls": ["https://drive.google.com/file/d/bbb/view"]},
    # The trap: filed under the member but carrying the parent as organisation,
    # so a loose match finds it twice.
    {"client": MEMBER, "organization": PARENT, "product": "Radio",
     "campaign": "Drive", "io": "5003", "status": "Live", "monthly": "600",
     "start": "2026-03-01"},
    {"client": OTHER, "organization": OTHER, "product": "Display - Category",
     "campaign": "Nope", "io": "9999", "status": "Live", "monthly": "10,000",
     "start": "2026-01-01"},
]
WEBSITES = [
    {"name": PARENT, "domain": "nationalbackgroundcheck.example", "platform": "Smart 1"},
    {"name": MEMBER, "domain": "fastfingerprints.example", "platform": "WordPress"},
    {"name": OTHER, "domain": "riversidehvacsupply.example", "platform": "WordPress"},
]

knack_data.products = lambda: list(PRODUCTS)                       # type: ignore[assignment]
knack_data.websites = lambda: list(WEBSITES)                       # type: ignore[assignment]
knack_data._product_source = lambda: (list(PRODUCTS), "export", None)  # type: ignore[assignment]

def find_group(q, client):
    """The group for one client out of a search that returns several.

    Searching the parent's name legitimately returns both records: a product
    filed under the member's name with the parent as its organisation belongs
    to the member. Client 360 shows that as a picker, so the test picks the
    same way rather than assuming an order.
    """
    return next((g for g in knack_data.search_client(q)
                 if str(g["client"]).lower() == client.lower()), None)


groups = knack_data.search_client(PARENT)
check("the parent's record is found", bool(groups), groups)
g = find_group(PARENT, PARENT)
check("and is one of the records the search offers", g is not None)
ios = sorted(str(p.get("io")) for p in g["products"])
check("it carries the member's insertion orders too",
      ios == ["5001", "5002", "5003"], ios)
check("and none of them twice", len(g["products"]) == 3)
check("the member's rows say whose they are",
      {p["io"]: p.get("member") for p in g["products"]}
      == {"5001": None, "5002": MEMBER, "5003": MEMBER},
      {p["io"]: p.get("member") for p in g["products"]})
check("the billing total is the group's, counted once",
      g["billing_monthly"] == 2000, g["billing_monthly"])
check("an unrelated client's IO is nowhere near it",
      "9999" not in ios)
check("creative merges too", len(g["creative"]) == 2, g["creative"])
check("and the member's file keeps its own name",
      [c.get("member") for c in g["creative"] if c["io"] == "5002"] == [MEMBER],
      g["creative"])
check("the member's website record comes across",
      "fastfingerprints.example" in [w.get("domain") for w in g["websites"]])
check("the page is told it is showing a group",
      g["group"]["grouped"] is True and g["group"]["parent"]["name"] == PARENT)

member_view = find_group(MEMBER, MEMBER)
check("opening the member shows the same three IOs",
      sorted(str(p.get("io")) for p in member_view["products"]) == ["5001", "5002", "5003"])
check("and labels the parent's rows as the parent's",
      {p["io"]: p.get("member") for p in member_view["products"]}["5001"] == PARENT)

other_view = find_group(OTHER, OTHER)
check("an ungrouped client is untouched by any of it",
      [str(p["io"]) for p in other_view["products"]] == ["9999"]
      and other_view["group"]["grouped"] is False)


# ---------------------------------------------------------------------------
section("The work log reads across the group and keeps the names apart")
# ---------------------------------------------------------------------------
from hub import audit                                              # noqa: E402
mod = next(iter(client_brand.WORK_KINDS))
audit.log(mod, "made_something", actor="t", client=PARENT, detail="parent work")
audit.log(mod, "made_something", actor="t", client=MEMBER, detail="member work")
audit.log(mod, "made_something", actor="t", client=OTHER, detail="unrelated work")

wl = client_brand.work_log(PARENT, 50, also=client_groups.member_names(PARENT))
details = sorted(i["detail"] for i in wl["items"])
check("both members' work is on the record",
      details == ["member work", "parent work"], details)
check("the merged row says which member did it",
      {i["detail"]: i.get("member") for i in wl["items"]}
      == {"parent work": None, "member work": MEMBER},
      {i["detail"]: i.get("member") for i in wl["items"]})
check("an unrelated client's work is not swept in", "unrelated work" not in details)
check("without a group the log is exactly what it always was",
      [i["detail"] for i in client_brand.work_log(PARENT, 50)["items"]] == ["parent work"])


# ---------------------------------------------------------------------------
section("Ungrouping restores both records and writes nothing to Knack")
# ---------------------------------------------------------------------------
res = client_groups.remove_member(MEMBER)
check("the member detaches", res.get("ok") is True, res)
check("that was the last member, so the group is gone",
      client_groups.roster(PARENT)["grouped"] is False)
check("and the parent's record is back to its own IO",
      [str(p["io"]) for p in find_group(PARENT, PARENT)["products"]] == ["5001"])
check("the member's record is back to its own two",
      sorted(str(p["io"]) for p in find_group(MEMBER, MEMBER)["products"])
      == ["5002", "5003"])

client_groups.add_member(PARENT, MEMBER)
client_groups.add_member(PARENT, OTHER)
check("a parent can hold several members",
      len(client_groups.roster(PARENT)["names"]) == 3)
res = client_groups.remove_member(PARENT)
check("removing the parent dissolves the group rather than promoting a member",
      res.get("dissolved") is True, res)
check("so no member is left holding a bill nobody chose",
      client_groups.roster(MEMBER)["grouped"] is False
      and client_groups.roster(OTHER)["grouped"] is False)
check("removing a client that is not in a group says so",
      "error" in client_groups.remove_member(MEMBER))


# ---------------------------------------------------------------------------
section("The store is durable, and holds names rather than derived keys")
# ---------------------------------------------------------------------------
reset()
client_groups.add_member(PARENT, MEMBER, member_url="fastfingerprints.example")
raw = open(client_groups._path(), encoding="utf-8").read()
check("the group is written through jsonstore, under the data dir",
      client_groups._path().startswith(os.path.realpath(_TMP))
      or client_groups._path().startswith(_TMP), client_groups._path())
check("what is stored is the name a person typed", MEMBER in raw)
check("no derived client_key is stored — it is re-derived on every read, so a "
      "client renamed in Knack re-joins rather than leaving a stale copy",
      '"d:' not in raw and '"n:' not in raw)
check("a member grouped with a URL still resolves by that URL",
      client_groups.roster("Whoever", "https://fastfingerprints.example/")["grouped"] is True)


# ---------------------------------------------------------------------------
section("The routes exist under the hub app, not a mount")
# ---------------------------------------------------------------------------
# CLAUDE.md's first trap is a hub route written under a mounted prefix — it
# would 404 with nothing on the page looking wrong.
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"], "name": "T"})

    reset()
    r = composed.get("/api/client/group?name=" + PARENT.replace(" ", "%20"))
    check("/api/client/group answers", r.status_code == 200)
    check("and reports an ungrouped client honestly",
          r.get_json()["roster"]["grouped"] is False)

    r = composed.post("/api/client/group/add",
                      json={"parent": PARENT, "member": MEMBER})
    check("/api/client/group/add attaches", r.status_code == 200 and r.get_json()["ok"])
    r = composed.get("/api/client/group?name=" + MEMBER.replace(" ", "%20"))
    check("the member's roster shows the group through the route too",
          r.get_json()["roster"]["parent"]["name"] == PARENT)

    r = composed.post("/api/client/group/add", json={"parent": OTHER, "member": MEMBER})
    check("a second group over one client is refused with a 400, not a silent 200",
          r.status_code == 400 and "error" in r.get_json(), r.status_code)

    check("Client 360 itself still renders", composed.get("/client360").status_code == 200)
    check("and /api/c360 carries the group with it",
          composed.get("/api/c360?q=" + PARENT.replace(" ", "%20"))
          .get_json()["groups"][0]["group"]["grouped"] is True)

    r = composed.post("/api/client/group/remove", json={"client": MEMBER})
    check("/api/client/group/remove detaches", r.status_code == 200 and r.get_json()["ok"])
    check("removing a client that is not grouped is a 400 with a reason",
          composed.post("/api/client/group/remove",
                        json={"client": MEMBER}).status_code == 400)
except Exception as exc:                                          # noqa: BLE001
    check("the composed app boots with these routes", False, exc)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
