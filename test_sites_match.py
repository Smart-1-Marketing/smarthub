"""hub/sites_match.py and hub/client_urls.py — test harness.

    python3 test_sites_match.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Every external reader is stubbed, so it needs no
Knack, Simvoly or Cloudinary credentials and reaches no third party.

## What is worth asserting

Both halves of this page attach a client to a URL, and both fail in the same
direction: a wrong link looks exactly like a right one, and every report keyed
on domain then agrees — confidently — about the wrong company.

  * **Only live sites are matched.** An EXPIRED or cancelled Simvoly project is
    not a website anybody can visit and its domain is usually repointed, so
    matching a client to one hands them somebody else's site. What is skipped
    has to be counted and named, because "we checked 1,200 projects" and "we
    checked the 380 that are live" are different claims.

  * **A file host is not a website.** Run against this deployment's real
    product export, every single click-thru domain was res.cloudinary.com,
    drive.google.com, dropbox.com or we.tl — where the creative was delivered
    from, not where the campaign points. Without the gate this tool would have
    proposed Cloudinary as the website of thirty-three different clients.

  * **Names match exactly or not at all**, the rule client_key.resolve()
    exists to enforce and the one the billing audit broke.

  * **A Simvoly project name is not the business's name.** 548 of this
    deployment's 1,021 projects begin with a media partner ("TMRG - JWS
    Pottery"), 249 are placeholders naming a person rather than a company
    ("Anna's Website"), and matching on the raw string found 42 of them. The
    prefix has to come off, the trailing "2026 Refresh" has to come off, and
    the partner's own name must never be proposed as the client — every one of
    FabLocal's thirty-seven SERVPRO franchises contains the string "FabLocal",
    and the old substring rule scored that at 0.92, top of the list.

  * **A placeholder is named, not matched.** A fuzzy pass over "Anna's
    Website" eventually finds an Anna and attaches a stranger's site to her.

  * **A name two clients answer to proposes neither.** Both are shown. Picking
    one is the guess that files one company's website under another.

  * **A source that could not be read says so.** "Knack is down" and "Knack has
    nothing for them" must never look alike — only one of them means the client
    really has no website anywhere in our data.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-sitesmatch-")
# Set, not setdefault: this file always gets its own throwaway mirror, so it is
# safe to re-run in a job whose DATABASE_URL is already a real Postgres.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "sites-match-test")
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


from hub import client_urls, sites_match                        # noqa: E402


# ---------------------------------------------------------------------------
section("Only live sites are matched")
# ---------------------------------------------------------------------------
check("an active project is live",
      sites_match.is_active({"status": "ACTIVE", "lifecycle_state": ""}))
check("an expired one is not",
      not sites_match.is_active({"status": "EXPIRED", "lifecycle_state": ""}))
check("a trial is not a website we can attribute",
      not sites_match.is_active({"status": "TRIAL", "lifecycle_state": ""}))
check("cancelled beats an ACTIVE status upstream — Simvoly cannot express it",
      not sites_match.is_active({"status": "ACTIVE", "lifecycle_state": "CANCELLED"}))
check("so does suspended",
      not sites_match.is_active({"status": "ACTIVE", "lifecycle_state": "SUSPENDED"}))
check("a row with no status at all is not assumed live",
      not sites_match.is_active({}))
check("and the reason is in words, not a blank",
      sites_match.inactive_reason({}) == "No status recorded",
      sites_match.inactive_reason({}))
check("a cancelled row says cancelled, not expired",
      sites_match.inactive_reason({"status": "EXPIRED",
                                   "lifecycle_state": "CANCELLED"}) == "Cancelled")

PROJECTS = [
    {"project_id": "1", "name": "Riverstone Heating", "status": "ACTIVE",
     "lifecycle_state": "", "domain": "riverstoneheating.example",
     "internal_client_name": ""},
    {"project_id": "2", "name": "Old Client Site", "status": "EXPIRED",
     "lifecycle_state": "", "domain": "expired-and-repointed.example",
     "internal_client_name": ""},
    {"project_id": "3", "name": "Cancelled Co", "status": "ACTIVE",
     "lifecycle_state": "CANCELLED", "domain": "cancelled.example",
     "internal_client_name": ""},
    {"project_id": "4", "name": "Trial Site", "status": "TRIAL",
     "lifecycle_state": "", "domain": "trial.example",
     "internal_client_name": ""},
    {"project_id": "5", "name": "Linked Already", "status": "ACTIVE",
     "lifecycle_state": "", "domain": "linked.example",
     "internal_client_name": "Linked Already Inc"},
    {"project_id": "6", "name": "Parked", "status": "ACTIVE",
     "lifecycle_state": "", "domain": "something.simvoly.com",
     "internal_client_name": ""},
]

CLIENTS = [
    {"name": "Riverstone Heating LLC", "url": "https://riverstoneheating.example",
     "domain": "riverstoneheating.example", "live": True, "running_count": 2,
     "product_count": 3, "source": "knack", "slug": "riverstone-heating-llc"},
    {"name": "Expired And Repointed", "url": "https://expired-and-repointed.example",
     "domain": "expired-and-repointed.example", "live": False, "running_count": 0,
     "product_count": 1, "source": "knack", "slug": "expired-and-repointed"},
    {"name": "Buckeye Lake Marina", "url": "", "domain": "", "live": True,
     "running_count": 1, "product_count": 1, "source": "knack",
     "slug": "buckeye-lake-marina"},
    {"name": "Nowhere Services", "url": "", "domain": "", "live": False,
     "running_count": 0, "product_count": 1, "source": "knack",
     "slug": "nowhere-services"},
]

sites_match._site_rows = lambda: list(PROJECTS)                 # noqa: SLF001
sites_match._hub_clients = lambda: list(CLIENTS)                # noqa: SLF001

live = sites_match.suggest()
check("live-only is the default", live["active_only"] is True)
check("only the live, unlinked, real-domain projects are checked",
      live["checked"] == 3, live["checked"])
check("the dead ones are counted rather than dropped",
      live["skipped_inactive"] == 3, live["skipped_inactive"])
check("and broken down by why", sorted(live["skipped_by_reason"]) ==
      ["Cancelled", "Expired", "Trial"], live["skipped_by_reason"])
check("the note says the scan was narrowed",
      "expired, trial, cancelled or suspended" in live["note"], live["note"])
matched_domains = {m["domain"] for m in live["suggested"]}
check("the live site matches its client",
      "riverstoneheating.example" in matched_domains, matched_domains)
check("the expired site's domain is never proposed",
      "expired-and-repointed.example" not in matched_domains, matched_domains)
check("a project already linked is left alone", live["already_linked"] == 1)
check("a platform domain still counts as no real website",
      live["no_domain_count"] == 1, live["no_domain"])

everything = sites_match.suggest(active_only=False)
check("asking for everything checks every project, live or not",
      everything["checked"] == len(PROJECTS), everything["checked"])
check("...and says so rather than implying the same scan",
      "including expired, trial and cancelled" in everything["note"])
check("the expired domain is only matchable when explicitly asked for",
      "expired-and-repointed.example" in
      {m["domain"] for m in everything["suggested"]})


# ---------------------------------------------------------------------------
section("A Simvoly project name is not the business's name")
# ---------------------------------------------------------------------------
from hub import site_names                                       # noqa: E402

check("the media partner comes off the front",
      "JWS Pottery" in [c["name"] for c in site_names.candidates("TMRG - JWS Pottery")],
      site_names.candidates("TMRG - JWS Pottery"))
check("...and the whole name is still offered, most conservative first",
      site_names.candidates("TMRG - JWS Pottery")[0]["name"] == "TMRG - JWS Pottery")
check("every candidate says how it was derived",
      all(c["why"] for c in site_names.candidates("TMRG - JWS Pottery")))
check("a trailing job marker comes off the end",
      "Helena Valley Addiction Services" in
      [c["name"] for c in site_names.candidates(
          "TMRG - Helena Valley Addiction Services - 2026 Refresh")])
check("a year on the end is a marker too",
      "Exec Air" in [c["name"] for c in site_names.candidates("TMRC - Exec Air 2025")],
      site_names.candidates("TMRC - Exec Air 2025"))
check("trimming does not cut a word off mid-name",
      site_names.best_name("Elsie Consulting - Main Site") == "Elsie Consulting",
      site_names.best_name("Elsie Consulting - Main Site"))
check("a remainder that is only a label is never a candidate",
      not any(c["name"].lower() in ("main site", "landing page", "2026 refresh")
              for c in site_names.candidates("Elsie Consulting - Main Site")
              + site_names.candidates("Moto - Fina Med Spa - Landing Page")),
      site_names.candidates("Moto - Fina Med Spa - Landing Page"))
check("a name with no separator is left alone",
      [c["name"] for c in site_names.candidates("Holiday Bazaar")] == ["Holiday Bazaar"])

check("a trial named after a person names nobody",
      site_names.is_placeholder("Anna's Website"))
check("...including one named after an email address",
      site_names.is_placeholder("chatita521@yahoo.com's Website"))
check("a test project names nobody", site_names.is_placeholder("S1M Test"))
check("and it says so in words, never just False",
      "trial project" in site_names.is_placeholder("Anna's Website"),
      site_names.is_placeholder("Anna's Website"))
check("a placeholder produces no candidates at all",
      site_names.candidates("Anna's Website") == [])
check("a real business is not called a placeholder",
      not site_names.is_placeholder("TMRG - Colby Landscaping"))
check("...nor is one whose name happens to end in s",
      not site_names.is_placeholder("Brothers That Just Do Gutters"))

BOOK = site_names.index_names([
    ("JWS Pottery", "the Knack website registry"),
    ("FabLocal", "the Knack website registry"),
    ("SERVPRO of Northeast San Antonio", "the Knack website registry"),
    ("SERVPRO Southwest San Antonio", "the Knack website registry"),
    ("Smitty's Fireplace Shop", "the Knack website registry"),
    ("Riverside HVAC", "the Hub client registry"),
    ("Riverside HVAC", "the Knack website registry"),
])
hits = site_names.exact_matches("TMRG - JWS Pottery", BOOK)
check("the business behind the partner prefix matches exactly",
      [h["client"] for h in hits] == ["JWS Pottery"], hits)
check("...and the match says which reading of the name did it",
      hits[0]["matched"] == "JWS Pottery", hits)
check("the media partner is never proposed as the client",
      "FabLocal" not in [h["client"] for h in site_names.exact_matches(
          "FabLocal -  SERVPRO of Southwest San Antonio", BOOK)])
check("the right franchise is the exact match, not the neighboring one",
      [h["client"] for h in site_names.exact_matches(
          "FabLocal -  SERVPRO of Southwest San Antonio", BOOK)]
      == ["SERVPRO Southwest San Antonio"],
      site_names.exact_matches("FabLocal -  SERVPRO of Southwest San Antonio", BOOK))
check("one client filed twice is offered once, not as an ambiguity",
      len(site_names.exact_matches("Riverside HVAC LLC", BOOK)) == 1,
      site_names.exact_matches("Riverside HVAC LLC", BOOK))

# Two client records, two different companies, one normalised name — which is
# the shape this deployment actually has: an old record and a re-filed one.
TWO = site_names.index_names([("Riverside HVAC LLC", "a"),
                              ("Riverside HVAC, Inc.", "b")])
check("two different clients answering to one name is an ambiguity",
      len(site_names.exact_matches("TMRG - Riverside HVAC", TWO)) == 2,
      site_names.exact_matches("TMRG - Riverside HVAC", TWO))

near = site_names.near_matches("TMRG - Smitty's Fireplace", BOOK)
check("a near miss is offered", [n["client"] for n in near] == ["Smitty's Fireplace Shop"], near)
check("...with the score in the reason", "%" in near[0]["why"], near[0])
check("a placeholder is never fuzzy-matched",
      site_names.near_matches("Anna's Website", BOOK) == [])
check("a substring is not a match — Acme is not Acme Plumbing",
      site_names.exact_matches("Acme", site_names.index_names(
          [("Acme Plumbing", "a")])) == [])
check("...and does not clear the near threshold either",
      site_names.near_matches("Acme", site_names.index_names(
          [("Acme Plumbing", "a")])) == [])


# ---------------------------------------------------------------------------
section("The scorer does not rank a substring above a resemblance")
# ---------------------------------------------------------------------------
# This is the rule client_key.py exists to enforce, and knack_websites scored
# a containment at a flat 0.92 — above almost every genuine similarity. On the
# real portfolio export that put the *media partner* top of the list on 39 of
# 242 suggested rows: accepting one files a client's website under FabLocal.
from hub import knack_websites                                   # noqa: E402

check("a partner prefix does not score 0.92 against the partner",
      knack_websites._similar(                                   # noqa: SLF001
          "FabLocal -  SERVPRO of Southwest San Antonio", "FabLocal") < 0.6,
      knack_websites._similar("FabLocal -  SERVPRO of Southwest San Antonio",
                              "FabLocal"))
check("Acme does not score 0.92 against Acme Plumbing",
      knack_websites._similar("Acme", "Acme Plumbing") < 0.72,
      knack_websites._similar("Acme", "Acme Plumbing"))
check("a genuine containment still clears the suggestion threshold",
      knack_websites._similar("Smitty's Fireplace",
                              "Smitty's Fireplace Shop") >= 0.72,
      knack_websites._similar("Smitty's Fireplace", "Smitty's Fireplace Shop"))
check("the same name either way round is still 1.0",
      knack_websites._similar("Riverside HVAC, LLC", "The Riverside HVAC Co.") == 1.0)
check("words are kept as words — ab cd is not abcd",
      knack_websites._norm_name("ab cd") != knack_websites._norm_name("abcd"))


# ---------------------------------------------------------------------------
section("A file host is not a client's website")
# ---------------------------------------------------------------------------
for bad in ("res.cloudinary.com", "drive.google.com", "we.tl", "dropbox.com",
            "s1mformstackfiles.s3.amazonaws.com", "facebook.com",
            "linktr.ee", "bit.ly", "yelp.com", "smart1marketing.com"):
    check(f"{bad} is never proposed as a website",
          not client_urls.looks_like_a_website(bad))
for good in ("riverstoneheating.com", "fresno.waterdamagesvcs.com",
             "buckeyelakemarina.net"):
    check(f"{good} is a plausible website",
          client_urls.looks_like_a_website(good))
check("something with no dot in it is not a domain",
      not client_urls.looks_like_a_website("localhost"))
check("and neither is a blank", not client_urls.looks_like_a_website(""))


# ---------------------------------------------------------------------------
section("Finding a URL for a client who has none")
# ---------------------------------------------------------------------------
# Every reader is stubbed: this asserts the joining, the ranking and the
# reporting, none of which need a live Knack to be wrong.
def _fake_products(found):
    client_urls._add(found, "Buckeye Lake Marina", "https://buckeyelakemarina.example/lp/x",
                     "product_clickthru", "display_url on a live product")
    client_urls._add(found, "Buckeye Lake Marina", "https://res.cloudinary.com/s1m/video",
                     "product_clickthru", "display_url on a live product")
    client_urls._add(found, "Riverstone Heating LLC", "https://riverstoneheating.example",
                     "product_clickthru")
    return {"rows": 3, "note": "stub"}


def _fake_websites(found):
    # The same client under a differently-punctuated name: it has to join.
    client_urls._add(found, "Buckeye Lake Marina, LLC", "buckeyelakemarina.example",
                     "knack_website")
    # A different business whose name merely contains the first one's.
    client_urls._add(found, "Buckeye Lake Marina Supplies", "someoneelse.example",
                     "knack_website")
    return {"rows": 2}


def _broken(found):
    raise RuntimeError("Knack timed out")


client_urls._READERS = (                                        # noqa: SLF001
    ("product_clickthru", _fake_products),
    ("knack_website", _fake_websites),
    ("simvoly", _broken),
)

import hub.clients_registry as registry                          # noqa: E402
registry.all_clients = lambda refresh=False: list(CLIENTS)

report = client_urls.missing()
by_client = {c["client"]: c for c in report["clients"]}

check("clients that already have a URL are not in the list",
      "Riverstone Heating LLC" not in by_client, list(by_client))
check("the count of who is missing one is reported",
      report["without_url"] == 2, report["without_url"])

marina = by_client.get("Buckeye Lake Marina")
check("a client with no URL gets candidates", bool(marina and marina["candidates"]))
top = marina["candidates"][0]
check("the domain two sources agree on ranks first",
      top["domain"] == "buckeyelakemarina.example", top)
check("...and is called strong rather than possible",
      top["confidence"] == "strong", top)
check("the evidence names both sources", len(top["sources"]) == 2, top["sources"])
check("the Cloudinary sighting never became a candidate",
      not any(c["domain"] == "res.cloudinary.com" for c in marina["candidates"]),
      marina["candidates"])
check("...and is reported as rejected rather than silently dropped",
      any(r["domain"] == "res.cloudinary.com" for r in report["rejected_domains"]),
      report["rejected_domains"])
check("the note explains where those went",
      "file host" in report["note"], report["note"])
check("a differently-named business is not folded into this client",
      not any(c["domain"] == "someoneelse.example" for c in marina["candidates"]),
      marina["candidates"])

nowhere = by_client.get("Nowhere Services")
check("a client nothing knows about gets an empty list, not a guess",
      nowhere is not None and nowhere["candidates"] == [], nowhere)

sources = {s["source"]: s for s in report["sources"]}
check("a reader that raised is reported as not read",
      sources["simvoly"]["ok"] is False, sources["simvoly"])
check("...by name, with the reason", "Knack timed out" in sources["simvoly"]["error"])
check("and the note says the totals are a floor",
      "floor, not a total" in report["note"], report["note"])
check("the live client sorts above the dormant one",
      [c["client"] for c in report["clients"]][0] == "Buckeye Lake Marina",
      [c["client"] for c in report["clients"]])


# ---------------------------------------------------------------------------
section("Accepting one is a human act, and reversible")
# ---------------------------------------------------------------------------
bad = client_urls.accept("Buckeye Lake Marina", "not a url at all")
check("a value that is not a URL is refused", not bad["ok"], bad)
check("...with a reason worth reading", "not a URL" in bad.get("error", ""), bad)
check("an empty client name is refused",
      not client_urls.accept("", "https://example.com")["ok"])

out = client_urls.accept("Buckeye Lake Marina", "buckeyelakemarina.example",
                         source="knack_website", actor="Todd")
check("a real one is stored", out["ok"] and out["row"]["domain"] == "buckeyelakemarina.example")
check("with who accepted it", out["row"]["accepted_by"] == "Todd")
check("and when", bool(out["row"]["accepted_at"]))
check("it reads back from the overlay",
      client_urls.overlay().get("buckeye lake marina", {}).get("domain")
      == "buckeyelakemarina.example", client_urls.overlay())

# The registry applies it on read, without relabelling the client.
import importlib                                                 # noqa: E402
registry = importlib.reload(registry)
registry.knack_data.products = lambda: [                         # noqa: SLF001
    {"client": "Buckeye Lake Marina", "product": "SEO", "status": "Live"}]
registry.knack_data.websites = lambda: []
registry.house_clients = lambda: []
rows = {r["name"]: r for r in registry.all_clients(refresh=True)}
marina_row = rows.get("Buckeye Lake Marina")
check("the accepted URL reaches the client registry",
      marina_row and marina_row["url"] == "https://buckeyelakemarina.example",
      marina_row)
check("labeled as discovered rather than presented as filed data",
      marina_row.get("url_source") == "discovered", marina_row)
check("and filling in a website does not make a Knack client one of ours",
      marina_row["is_house"] is False and marina_row["source"] == "knack",
      marina_row)

check("clearing it works", client_urls.clear("Buckeye Lake Marina")["ok"])
check("clearing it twice says so rather than pretending",
      not client_urls.clear("Buckeye Lake Marina")["ok"])
rows = {r["name"]: r for r in registry.all_clients(refresh=True)}
check("and the registry goes back to having no URL for them",
      rows["Buckeye Lake Marina"]["url"] == "",
      rows["Buckeye Lake Marina"])


# ---------------------------------------------------------------------------
section("The routes exist under the hub app, not a mount")
# ---------------------------------------------------------------------------
# /tools/sites-match is a hub route, and CLAUDE.md's first trap is a hub route
# written under a mounted prefix — it would 404 with nothing looking wrong.
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                                  "name": "T"})
    for path in ("/tools/sites-match", "/api/sites-match",
                 "/api/sites-match?include_inactive=1", "/api/client-urls"):
        check(f"{path} answers", composed.get(path).status_code == 200)
    check("accept rejects a bad URL through the route too",
          composed.post("/api/client-urls/accept",
                        json={"client": "X", "url": "nope"}).get_json()["ok"] is False)
except Exception as exc:                                        # noqa: BLE001
    check("the composed app boots with these routes", False, exc)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
