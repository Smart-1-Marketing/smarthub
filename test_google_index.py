"""hub/google_index.py — the Google account/client map.

    python3 test_google_index.py

No pytest, no new dependencies, and it runs against a temporary data
directory and a throwaway SQLite mirror, so it never touches /var/data or the
real index.

## Why this file exists

Every way this can be wrong is silent. A mapping that is merely *plausible*
still renders: the 360 page shows a GA4 property, somebody quotes a traffic
number from it, and nothing anywhere says it belonged to a different client
with a similar name. The whole value of the index is that a mapping can be
explained, so each check below is a way it could map something it should not —
or fail to say that it could not decide.

  1.  domain beats name        — the URL is the join key, not the name
  2.  attached beats domain    — a human decision outranks a derived one
  3.  no substring matching    — "Riverside HVAC" must not absorb "… LLC"
  4.  ambiguity maps nothing   — and lists the candidates instead
  5.  GA4 has no URL           — name-matched, and the row says it is weaker
  6.  Google's own hosts       — never treated as a client domain
  7.  shared domains           — a domain two clients share maps to neither
  8.  never built ≠ empty      — the difference the old bug turned into a lie
  9.  stale is reported        — an old sweep is not presented as current
  10. for_client is exact      — no bleed between similarly named clients
  11. the QA rows              — unmapped first, and each carries its reason
  12. _live_google is fixed    — the bug that made every client read the same
  13. the round trip           — build → persist → load survives a new process
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1gindex_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))

from hub import google_index as gi                       # noqa: E402

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


# A fixed registry, so the assertions are about the join and not about
# whichever clients happen to be in Knack today.
BY_DOMAIN = {
    "riversidehvac.com": "Riverside HVAC",
    "buckeyelakewinery.com": "Buckeye Lake Winery",
}


def match(item, attachments=None):
    return gi.match_item(item, attachments=attachments or {}, by_domain=BY_DOMAIN)


# ------------------------------------------------------- 1-2. precedence
section("The URL is the join key, and a human beats the URL")
gtm = {"platform": "Google Tag Manager", "name": "Some Other Company",
       "resource_id": "GTM-ABC123", "domains": ["www.riversidehvac.com"]}
m = match(gtm)
# The container is NAMED after a different company and carries the client's
# domain. The domain wins, which is the rule that stops name drift mattering.
check("domain wins over a misleading name", m["client"], "Riverside HVAC")
check("and says so", m["match"], "domain")
check("attachment outranks the domain",
      match(gtm, {"gtm-abc123": "Buckeye Lake Winery"})["client"],
      "Buckeye Lake Winery")
check("...and records that a human decided it",
      match(gtm, {"gtm-abc123": "Buckeye Lake Winery"})["match"], "attached")
# Ids are written GTM-ABC123 in one place and gtm-abc123 in another.
check("attachment lookup is case-insensitive",
      match(gtm, {"GTM-ABC123".lower(): "Buckeye Lake Winery"})["match"],
      "attached")


# --------------------------------------------------------- 3-4. no guessing
section("A near-miss maps nothing rather than guessing")
# There is no client called exactly this. The old billing audit took the first
# Knack name *containing* the term, which is how "Acme" became whichever of
# Acme Plumbing, Acme Roofing and Acme Electric came out of a dict first.
near = {"platform": "Google Analytics", "name": "Riverside", "resource_id": "1"}
check("a substring does not match", match(near)["client"], "")
check("and neither does a longer name",
      match({"platform": "Google Analytics", "name": "Riverside HVAC LLC",
             "resource_id": "2"})["client"], "")


class _Ambiguous:
    """Stands in for client_key.resolve() when two clients could be meant."""
    @staticmethod
    def resolve(name="", url="", **kw):
        return {"client": name, "known": False, "confidence": "unmatched",
                "candidates": ["Acme Plumbing", "Acme Roofing"]}


def with_resolver(fake, fn):
    import hub.client_key as ck
    real = ck.resolve
    ck.resolve = fake.resolve
    try:
        return fn()
    finally:
        ck.resolve = real


amb = with_resolver(_Ambiguous, lambda: match(
    {"platform": "Google Analytics", "name": "Acme", "resource_id": "3"}))
check("an ambiguous name maps to nobody", amb["client"], "")
check("but the candidates are shown", amb["candidates"],
      ["Acme Plumbing", "Acme Roofing"])
check("and the row explains itself",
      "more than one" in amb["match_detail"].lower(), True)


class _Exact:
    """Stands in for an exact registry hit on a fixture client.

    The fixture clients are not in the real Knack registry, so without this
    the name-match cases would be asserting what today's client list happens
    to contain rather than what the join rule does.
    """
    @staticmethod
    def resolve(name="", url="", **kw):
        if name in BY_DOMAIN.values():
            return {"client": name, "known": True, "confidence": "exact",
                    "candidates": []}
        return {"client": name, "known": False, "confidence": "unmatched",
                "candidates": []}


class _Fuzzy:
    @staticmethod
    def resolve(name="", url="", **kw):
        return {"client": "Riverside HVAC", "known": True,
                "confidence": "probable", "candidates": []}


fuzzy = with_resolver(_Fuzzy, lambda: match(
    {"platform": "Google Analytics", "name": "Riverside HVAC Inc",
     "resource_id": "4"}))
# A "probable" match written into a stored index becomes a fact nobody
# re-examines, so only "exact" is allowed to map.
check("a fuzzy match is not written into the index", fuzzy["client"], "")


# ------------------------------------------------------------ 5-7. domains
section("GA4 carries no URL, and not every URL is a client's")
ga = {"platform": "Google Analytics", "name": "Riverside HVAC",
      "resource_id": "312345678", "domains": []}
m = with_resolver(_Exact, lambda: match(ga))
check("a GA4 property can only be name-matched", m["match"], "name")
check("and the row says that is weaker",
      "weaker than a domain" in m["match_detail"], True)

check("google's own hosts are never a client domain",
      gi._domains_of({"search_extra": "analytics.google.com tagmanager.google.com"}),
      [])
check("a real domain in search_extra is still found",
      gi._domains_of({"search_extra": "buckeyelakewinery.com siteOwner"}),
      ["buckeyelakewinery.com"])
check("an explicit domains field wins over scraping",
      gi._domains_of({"domains": ["https://WWW.Riversidehvac.com/contact"],
                      "search_extra": "somethingelse.com"}),
      ["riversidehvac.com"])

# A domain two clients share cannot identify either of them.
import hub.client_key as _ck                             # noqa: E402
_real_alias = _ck.alias_index
_ck.alias_index = lambda refresh=False: {
    "by_domain": {"shared.com": {"name": "First Client"},
                  "solo.com": {"name": "Solo Client"}},
    "domain_conflicts": {"shared.com": ["First Client", "Second Client"]},
}
try:
    built = gi._client_by_domain()
finally:
    _ck.alias_index = _real_alias
check("a contested domain maps to nobody", "shared.com" in built, False)
check("an uncontested one still maps", built.get("solo.com"), "Solo Client")


# ------------------------------------------------- 8-9. absent vs. empty
section("Never built is not the same as nothing found")
st = gi.status()
check("an unbuilt index says so", st["never_built"], True)
check("and does not claim zero resources as a finding", st["resources"], 0)
check("age is unknown, not zero", st["age_seconds"], None)
check("an unbuilt index counts as stale", st["stale"], True)
check("for_client passes never_built through",
      gi.for_client("Riverside HVAC")["never_built"], True)


# --------------------------------------------- 10-13. storage and readers
section("Built, persisted, and read back the way the pages read it")
from hub import jsonstore                                # noqa: E402
payload = {
    "built_at": "2026-08-24T09:00:00+00:00",
    "accounts": ["adops@smart1marketing.com"],
    "errors": [],
    "items": [
        dict(match(gtm), google_login="adops@smart1marketing.com"),
        dict(with_resolver(_Exact, lambda: match(ga)),
             google_login="adops@smart1marketing.com"),
        dict(match({"platform": "Search Console",
                    "name": "sc-domain:buckeyelakewinery.com",
                    "resource_id": "sc-domain:buckeyelakewinery.com",
                    "domains": ["buckeyelakewinery.com"]}),
             google_login="adops@smart1marketing.com"),
        dict(match({"platform": "Google Analytics", "name": "Nobody At All",
                    "resource_id": "999"}),
             google_login="adops@smart1marketing.com"),
    ],
}
jsonstore.write_json(gi._path(), payload)

st = gi.status()
check("resources counted", st["resources"], 4)
check("mapped counted", st["mapped"], 3)
check("unmapped counted", st["unmapped"], 1)
check("no longer reads as never built", st["never_built"], False)
# Built in the past and older than the six-hour window.
check("an old index is reported stale, not current", gi.is_stale(max_age=1), True)

found = gi.for_client("Riverside HVAC")
check("the client's GA4 is found", len(found["ga4"]), 1)
check("and their GTM", len(found["gtm"]), 1)
check("and nothing belonging to anyone else", found["total"], 2)
check("a different client gets their own", gi.for_client("Buckeye Lake Winery")["total"], 1)
check("matching by domain alone works",
      gi.for_client("", "https://buckeyelakewinery.com/")["total"], 1)
check("an unknown client gets nothing, not everything",
      gi.for_client("Someone Not In The Index")["total"], 0)

rows = gi.rows()
check("unmapped rows sort first", bool(rows[0]["client"]), False)
check("every row carries its reason",
      all(r["match_detail"] for r in rows), True)
check("unmapped() is just those rows", len(gi.unmapped()), 1)
check("clients_missing finds the gap",
      gi.clients_missing("gtm"), ["Buckeye Lake Winery"])

section("The analytics_ids bug stays fixed")
# The original read acct["items"] off connected_accounts(), which returns
# {email, refresh_token, status}. The loop never ran, so every client reported
# "recorded_only — request access" no matter what access we had.
from hub import analytics_ids                            # noqa: E402
live = analytics_ids._live_google("Riverside HVAC", "riversidehvac.com")
check("the GA4 id comes back", live["ga"], "312345678")
check("the GTM id comes back", live["gtm"], "GTM-ABC123")
check("connected accounts are counted", live["accounts_connected"], 1)
check("a client with nothing gets blanks, not another client's ids",
      analytics_ids._live_google("Someone Else", "")["ga"], "")

section("A page never triggers a sweep")
# build() is the only thing that talks to Google, and only the scheduler calls
# it. If a reader ever reaches get_index() again, this fails.
import inspect                                           # noqa: E402
for fn in (gi.for_client, gi.rows, gi.status, gi.load, gi.unmapped):
    src = inspect.getsource(fn)
    check(f"{fn.__name__}() makes no Google call",
          "get_index" in src or "gf." in src, False)
check("build() is the one that does",
      "get_index" in inspect.getsource(gi.build), True)
from hub import scheduler                                # noqa: E402
check("and the scheduler is what runs it",
      "google_index" in scheduler.JOBS, True)


section("An empty report says which kind of empty it is")
# The bug this guards: qa_report.html renders ANY report with no rows as
# "Nothing to report — all clear ✓" and returns before it reaches the note.
# So a report that could not look was showing a green tick saying the
# opposite. Reports that cannot look must set `unavailable`, which the page
# renders instead of the all-clear.
from hub import qa                                       # noqa: E402
from hub import jsonstore as _js                         # noqa: E402

_js.write_json(gi._path(), {"built_at": "", "items": [], "accounts": [],
                            "errors": []})
rep = qa.google_accounts()
check("a never-built index does not return bare empty rows",
      bool(rep.get("unavailable")), True)
check("...and never claims all-clear", rep["rows"], [])
check("...and offers a way to fix it",
      rep["unavailable"]["action_post"], "/api/google/rebuild")
check("...and says it is not the same as having no accounts",
      "NOT the same" in rep["unavailable"]["message"], True)

# Built, swept, and genuinely found nothing is also not all-clear.
_js.write_json(gi._path(), {"built_at": "2026-08-24T09:00:00+00:00",
                            "items": [], "accounts": [], "errors": []})
rep = qa.google_accounts()
check("a built-but-empty index is flagged too",
      bool(rep.get("unavailable")), True)
check("...and says it is worth investigating",
      "investigating" in rep["unavailable"]["message"], True)

# With rows, the normal table comes back and nothing is flagged.
_js.write_json(gi._path(), payload)
rep = qa.google_accounts()
check("a populated index renders a table", len(rep["rows"]), 4)
check("...and sets no unavailable flag", rep.get("unavailable"), None)


section("A failed build leaves a reason, and does not wipe a good index")
good = dict(payload)
_js.write_json(gi._path(), good)


class _Broken:
    @staticmethod
    def get_index(force=False):
        raise RuntimeError("Google said no")

    @staticmethod
    def connected_accounts():
        return [{"email": "a@b.com"}]


import sys                                              # noqa: E402
sys.modules["gf_app"] = _Broken
try:
    res = gi.build(force=True)
finally:
    sys.modules.pop("gf_app", None)

check("the build reports failure", res["ok"], False)
check("and names the cause", "RuntimeError" in res["error"], True)
st = gi.status()
check("the reason is stored where a reader sees it",
      "Google said no" in st["last_error"], True)
check("the attempt is timestamped", bool(st["last_attempt"]), True)
# Yesterday's accounts, clearly labelled stale, beat no accounts at all.
check("a failed sweep does NOT wipe the previous index", st["resources"], 4)
check("...which still reads as built", st["never_built"], False)
rep = qa.google_accounts()
check("so the report still lists them", len(rep["rows"]), 4)


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
