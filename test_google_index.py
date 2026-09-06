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
  14. a sweep with no request  — the scheduler has no Flask context, and the
                                account table has to be readable anyway
  15. none vs cannot look      — an unreadable list is never an empty one
  16. the Tag Manager pace     — it adapts, or the retry path IS the path
  17. a refused account        — keeps the last sweep's containers, and says so
  18. a login that went silent — is still counted as a login
  19. the sweep is not free    — a redeploy must not re-run a fresh one
  20. a search never sweeps    — /google/api/search reads the stored index,
                                  never Google, whatever a search box asks
  21. never built is honest    — said outright, not swept live to hide it
  22. a manual rebuild cools   — one impatient double-click cannot stack a
                                  second live sweep on the first, whichever
                                  screen asked for it
"""
import json
import os
import shutil
import sys
import threading
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1gindex_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
# Assigned, never setdefault: a fresh HUB_DATA_DIR is not
# isolation on its own. jsonstore keys its mirror *relative to
# the data root* by design -- so a production blob restores
# into a dev checkout -- which means an inherited DATABASE_URL
# (CI's Postgres, or a developer's own) refills this run's
# empty directory with the last run's rows. Owning both is
# what makes "throwaway" true, and what makes the file safe to
# run twice; test_blog_publish.py is the same pattern.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["TOKEN_DB_PATH"] = os.path.join(TMP, "google_tokens.db")
os.environ["SESSION_FILE_DIR"] = os.path.join(TMP, "sessions")

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


section("The scheduled sweep has no request, and must read the accounts anyway")

# The bug this section exists for: google_finder reached its token database
# through flask.g, which only exists inside an application context. The
# scheduler sweeps from a background thread, so every read raised
# RuntimeError inside connected_accounts()'s except and came back as [] —
# and the index announced "No Google accounts are connected" every three
# hours while the accounts sat in the table. Nothing errored, on either side.
from cryptography.fernet import Fernet                   # noqa: E402

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
from modules.google_finder import app as gf              # noqa: E402

with gf.app.app_context():
    gf.save_account("rep@smart1marketing.com", "refresh-token-value")
    check("an account connected in a request reads back inside it",
          [a["email"] for a in gf.connected_accounts()],
          ["rep@smart1marketing.com"])

# The scheduler's world: no application context at all.
check("...and reads back with no application context",
      [a["email"] for a in gf.connected_accounts()],
      ["rep@smart1marketing.com"])

_thread_saw = []
_t = threading.Thread(target=lambda: _thread_saw.extend(gf.connected_accounts()))
_t.start()
_t.join()
check("...and from a background thread, which is how the sweep runs",
      [a["email"] for a in _thread_saw], ["rep@smart1marketing.com"])

# The hub app calls the same sweep from /api/google/rebuild. flask.g exists
# there, but google_finder's teardown does not run for another app's context,
# so the cached connection would leak one handle per rebuild.
from flask import Flask as _Flask                        # noqa: E402
with _Flask("not-google-finder").app_context() as _ctx:
    check("...and under another app's context",
          [a["email"] for a in gf.connected_accounts()],
          ["rep@smart1marketing.com"])
    from flask import g as _g                            # noqa: E402
    check("...without parking a connection on that app's g",
          "db" in _g, False)

accounts, why = gf.connected_accounts_result()
check("a readable list reports no reason", why, "")
check("...and carries the accounts", len(accounts), 1)

# A rotated TOKEN_ENCRYPTION_KEY leaves rows that will not decrypt. Skipping
# them in silence turns a key rotation into "nobody has ever connected one".
_real_key = os.environ["TOKEN_ENCRYPTION_KEY"]
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
gf.TOKEN_ENCRYPTION_KEY = os.environ["TOKEN_ENCRYPTION_KEY"]
accounts, why = gf.connected_accounts_result()
check("a row that will not decrypt is not silently dropped", bool(why), True)
check("...and the reason names the key", "TOKEN_ENCRYPTION_KEY" in why, True)
check("...and the list really is empty, so it must not read as 'none'",
      accounts, [])
os.environ["TOKEN_ENCRYPTION_KEY"] = _real_key
gf.TOKEN_ENCRYPTION_KEY = _real_key

# No key at all is the same shape of answer.
gf.TOKEN_ENCRYPTION_KEY = ""
_, why = gf.connected_accounts_result()
check("an unreadable table is reported, not answered with none", bool(why), True)
gf.TOKEN_ENCRYPTION_KEY = _real_key


section("Nothing connected and could not look are different answers")

from hub import audit as _audit                          # noqa: E402


def _log_types():
    return [e.get("type") for e in _audit.read(limit=500, module="google_index")]


def _build_with(stub):
    sys.modules["gf_app"] = stub
    try:
        return gi.build(force=True)
    finally:
        sys.modules.pop("gf_app", None)


class _Unreadable:
    """Reached Google, swept nothing, and cannot say whether that is right."""

    @staticmethod
    def get_index(force=False):
        return [], []

    @staticmethod
    def connected_accounts_result():
        return [], "RuntimeError: Working outside of application context."

    @staticmethod
    def connected_accounts():                # the silent zero, still available
        return []


class _NoAccounts:
    @staticmethod
    def get_index(force=False):
        return [], []

    @staticmethod
    def connected_accounts_result():
        return [], ""

    @staticmethod
    def connected_accounts():
        return []


_before = _log_types().count("build_failed")
res = _build_with(_Unreadable)
check("an unreadable account list is a failure", res["ok"], False)
check("...and says so", "could not be read" in res["error"], True)
check("...rather than reporting an unconfigured Hub",
      "nothing to index" in res["error"], False)
check("...and is not filed as a skip", res.get("skipped"), None)
check("...and a failure is logged every time it happens",
      _log_types().count("build_failed"), _before + 1)
_build_with(_Unreadable)
check("...including the second time", _log_types().count("build_failed"),
      _before + 2)

res = _build_with(_NoAccounts)
check("genuinely nothing connected is a skip, not a failure",
      res.get("skipped"), True)
check("...and says what to do about it", "/google/login" in res["error"], True)
check("...logged once, when the state starts",
      _log_types().count("build_skipped"), 1)
res = _build_with(_NoAccounts)
check("...and not again on every three-hourly run, for ever",
      _log_types().count("build_skipped"), 1)
check("...while the reason still reaches a reader",
      "No Google accounts" in gi.status()["last_error"], True)
check("...and yesterday's index is not wiped by it",
      gi.status()["resources"], 4)
check("...which still reads as built", gi.status()["never_built"], False)


section("Tag Manager pacing adapts, or the retry path is the normal path")

# The failure this section exists for, measured on the live service: paced at
# a FIXED 0.35s, one sweep of 180 Tag Manager accounts logged a 429 on very
# nearly every first attempt, paid 1s + 2s + 4s of backoff to push most of
# them through, exhausted its retries on 13, and took 440 seconds — while the
# next account started again at 0.35s and rediscovered the same refusal. The
# pacer had no memory, so roughly two and a half requests were spent per
# account and the wasted ones counted against the daily quota exactly as the
# useful ones did.


class _Resp:
    def __init__(self, retry_after=None):
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


class _429(Exception):
    def __init__(self, retry_after=None):
        super().__init__("429 Client Error: Too Many Requests for url: x")
        self.response = _Resp(retry_after)


def _reset_pace():
    gf._gtm_pace.update({"interval": gf._GTM_MIN_INTERVAL, "last": 0.0,
                         "ok_streak": 0, "calls": 0, "throttled": 0})


# time.sleep is replaced so the assertions are about what the pacer DECIDED,
# not about how long this test file takes to run.
_slept = []
_real_sleep = gf.time.sleep
gf.time.sleep = lambda s: _slept.append(s)

_reset_pace()
_start = gf._gtm_pace["interval"]
gf._gtm_throttled(0.0)
check("one 429 widens the pace for everything after it",
      gf._gtm_pace["interval"] > _start, True)
check("...and it is the shared pace, not this call's own",
      gf.gtm_pace_state()["interval"], gf._gtm_pace["interval"])

_reset_pace()
check("Google's own Retry-After beats our guess when it sends one",
      gf._gtm_throttled(9.0), 9.0)
_reset_pace()
# A server that asks for an hour is asking for more than a sweep has.
check("...but a Retry-After longer than the cap is not honored in full",
      gf._gtm_throttled(3600.0) <= max(gf._GTM_RETRY_AFTER_CAP,
                                       gf._GTM_MAX_INTERVAL), True)
_reset_pace()
check("...and a Retry-After we cannot read is not a crash",
      gf._gtm_retry_after(_429("Fri, 01 Jan 2027 00:00:00 GMT")), 0.0)
check("...nor is a 429 carrying no response at all",
      gf._gtm_retry_after(Exception("429")), 0.0)

_reset_pace()
for _ in range(40):
    gf._gtm_throttled(0.0)
check("the widening is bounded, not unbounded",
      gf._gtm_pace["interval"], gf._GTM_MAX_INTERVAL)

# Recovery must be far slower than widening. The other ratio oscillates:
# it speeds up, spends a 429 rediscovering the ceiling, and does it again.
gf._gtm_throttled(0.0)
_wide = gf._gtm_pace["interval"]
gf._gtm_succeeded()
check("one clean call does not undo a refusal",
      gf._gtm_pace["interval"], _wide)
for _ in range(gf._GTM_RECOVER_AFTER):
    gf._gtm_succeeded()
check("...a sustained clean run does", gf._gtm_pace["interval"] < _wide, True)
for _ in range(gf._GTM_RECOVER_AFTER * 40):
    gf._gtm_succeeded()
check("...and never below the floor",
      gf._gtm_pace["interval"], gf._GTM_MIN_INTERVAL)

# The point of all of it: the SECOND account must not repeat the first's
# mistake. Two calls that are both refused once, with the pace carried over.
_reset_pace()
_calls = []


def _fake_get(token, url, params=None):
    _calls.append(url)
    # Refused while the pace is still at the floor; allowed once it widens.
    if gf._gtm_pace["interval"] <= gf._GTM_MIN_INTERVAL:
        raise _429()
    return {"account": []}


_real_google_get = gf.google_get
gf.google_get = _fake_get
try:
    gf.gtm_get("t", "https://tagmanager.googleapis.com/a")
    _first = len(_calls)
    gf.gtm_get("t", "https://tagmanager.googleapis.com/b")
    _second = len(_calls) - _first
finally:
    gf.google_get = _real_google_get
check("a refusal costs the first call a retry", _first, 2)
check("...and the next call does not pay for it again", _second, 1)

gf.time.sleep = _real_sleep


section("A Tag Manager account we were refused keeps its last reading")

# Tag Manager rate-limits hard enough that some accounts will be refused
# however politely we ask. Dropping their containers reports this login owning
# fewer than it does — a smaller number in a complete-looking list, with
# nothing saying a reading is missing. It is the rule knack_products and
# domain_purchase already work to: a failed pull never empties a good snapshot.
gf.time.sleep = lambda s: None
_reset_pace()

_PREV = {"6372951359": [{"platform": "Google Tag Manager", "type": "GTM Container",
                         "name": "Riverside HVAC", "account_id": "6372951359",
                         "resource_id": "GTM-AAA", "domains": ["riversidehvac.com"],
                         "google_login": "adops@smart1marketing.com"}]}


def _gtm_two_accounts(refuse):
    """Two accounts; `refuse` names the ids Tag Manager turns down."""
    def _get(token, url, params=None):
        if url.endswith("/accounts"):
            return {"account": [{"accountId": "6372951359", "path": "accounts/6372951359"},
                                {"accountId": "6366317523", "path": "accounts/6366317523"}]}
        if any(a in url for a in refuse):
            raise _429()
        return {"container": [{"publicId": "GTM-BBB", "containerId": "9",
                               "name": "Buckeye", "domainName": ["buckeyelakewinery.com"]}]}
    return _get


_notes = []
gf.google_get = _gtm_two_accounts(["6372951359"])
try:
    rows = gf.fetch_gtm_items("t", "adops@smart1marketing.com", _notes,
                              previous=_PREV)
finally:
    gf.google_get = _real_google_get
check("a refused account keeps the containers the last sweep found",
      sorted(r["resource_id"] for r in rows), ["GTM-AAA", "GTM-BBB"])
check("...marked as carried over, never merged in quietly",
      [r["resource_id"] for r in rows if r.get("carried_over")], ["GTM-AAA"])
check("...and the fresh one is not marked",
      [r["resource_id"] for r in rows if not r.get("carried_over")], ["GTM-BBB"])
_n = [n for n in _notes if n["platform"] == "Google Tag Manager"][0]
check("...the sweep is partial, which is neither clean nor a hole",
      _n["kind"], "partial")
check("...and a partial sweep is still surfaced as a problem", _n["ok"], False)
check("...saying how many accounts kept an older reading",
      "1 kept the 1 container(s)" in _n["error"], True)

# The other half: refused, and no earlier reading to fall back on. That one
# genuinely costs the index a container and must not read the same way.
_notes = []
gf.google_get = _gtm_two_accounts(["6366317523"])
try:
    rows = gf.fetch_gtm_items("t", "adops@smart1marketing.com", _notes, previous={})
finally:
    gf.google_get = _real_google_get
_n = [n for n in _notes if n["platform"] == "Google Tag Manager"][0]
check("a refusal with nothing to carry is a failure, not a partial",
      _n["kind"], "failed")
check("...and says the containers are missing rather than implying none",
      "missing from this list" in _n["error"], True)

# Nothing refused is still a clean sweep, and `previous` must not leak into it.
_notes = []
gf.google_get = _gtm_two_accounts([])
try:
    rows = gf.fetch_gtm_items("t", "adops@smart1marketing.com", _notes,
                              previous=_PREV)
finally:
    gf.google_get = _real_google_get
check("a clean sweep reports ok",
      [n["kind"] for n in _notes if n["platform"] == "Google Tag Manager"], ["ok"])
check("...and carries nothing forward into it",
      [r for r in rows if r.get("carried_over")], [])
gf.time.sleep = _real_sleep

# The map handed to the sweep must not carry the JOIN forward. client, match
# and match_detail are derived against the client list as it stands now, and a
# stored one carried into the next sweep is a six-hour-old guess promoted to a
# fact — the rule hub/client_key.py states as never store the key.
gi.jsonstore.write_json(gi._path(), {
    "built_at": "2026-08-26T19:26:04+00:00", "accounts": [], "errors": [],
    "items": [{"platform": "Google Tag Manager", "account_id": "1",
               "resource_id": "GTM-AAA", "google_login": "a@b.com",
               "client": "Riverside HVAC", "match": "domain",
               "match_detail": "matched on riversidehvac.com",
               "carried_over": True}]})
_prev = gi._previous_gtm()
check("the carry-forward map is keyed by login then account",
      list(_prev), ["a@b.com"])
check("...and the derived join is stripped on the way out",
      sorted(k for k in _prev["a@b.com"]["1"][0]
             if k in ("client", "match", "match_detail", "carried_over")), [])
check("...while the resource itself survives",
      _prev["a@b.com"]["1"][0]["resource_id"], "GTM-AAA")


section("A login that answered nothing is still a login")


class _OneSilent:
    """Two logins connected; only one of them came back with anything.

    This is the live shape: the activity log read "accounts: 1" on a sweep
    whose own `errors` named a second login that had dropped out entirely,
    because the account list was derived from the returned rows and so shrank
    to fit the answer.
    """

    @staticmethod
    def get_index(force=False, notes=None, previous=None):
        return ([{"platform": "Google Tag Manager", "type": "GTM Container",
                  "name": "Buckeye", "account_id": "1", "resource_id": "GTM-BBB",
                  "domains": ["buckeyelakewinery.com"],
                  "google_login": "adops@smart1marketing.com"}],
                [{"email": "old@smart1marketing.com",
                  "error": "ReauthRequired: token revoked"}])

    @staticmethod
    def connected_accounts_result():
        return ([{"email": "adops@smart1marketing.com", "refresh_token": "x"},
                 {"email": "old@smart1marketing.com", "refresh_token": "y"}], "")


res = _build_with(_OneSilent)
check("both connected logins are counted", len(res["accounts"]), 2)
check("...only one of them answered", res["accounts_answered"],
      ["adops@smart1marketing.com"])
check("...and the silent one is named rather than dropped",
      res["accounts_silent"], ["old@smart1marketing.com"])
st = gi.status()
check("...which a page can read back", st["accounts_silent"],
      ["old@smart1marketing.com"])


class _KeyRotated:
    """One login answered; another row would not decrypt."""

    @staticmethod
    def get_index(force=False, notes=None, previous=None):
        return ([{"platform": "Search Console", "type": "Site",
                  "name": "https://buckeyelakewinery.com/", "account_id": "",
                  "resource_id": "https://buckeyelakewinery.com/",
                  "domains": ["buckeyelakewinery.com"],
                  "google_login": "adops@smart1marketing.com"}], [])

    @staticmethod
    def connected_accounts_result():
        return ([{"email": "adops@smart1marketing.com", "refresh_token": "x"}],
                "1 of 2 stored Google account(s) could not be decrypted — "
                "TOKEN_ENCRYPTION_KEY has changed since they were connected.")


_build_with(_KeyRotated)
check("a rotated key is reported even when the sweep found things",
      "TOKEN_ENCRYPTION_KEY" in gi.status()["accounts_error"], True)


section("The sweep is expensive, so a redeploy must not re-run a fresh one")

# Every scheduler job starts due, so a deploy re-ran this one however recently
# it had finished — and it is 180 rate-limited Tag Manager calls and seven
# minutes. The live service swept at 19:19, deployed at 19:29, and swept the
# identical 180 accounts again at 19:33.
def _hours_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            - timedelta(hours=n)).isoformat(timespec="seconds")


check("a fresh index is not due", gi.due_for_refresh(3600), False)
_saved = gi.load()
gi.jsonstore.write_json(gi._path(),
                        dict(_saved, built_at=_hours_ago(2)))
check("...and is due once it has aged past the window",
      gi.due_for_refresh(3600), True)
gi.jsonstore.write_json(gi._path(), _saved)
gi.jsonstore.write_json(gi._path(), dict(_saved, built_at=""))
check("an index that was never built is always due",
      gi.due_for_refresh(10 ** 9), True)
# Too often is recoverable; never is not.
gi.jsonstore.write_json(gi._path(), dict(_saved, built_at="2099-01-01T00:00:00+00:00"))
check("...and so is one whose timestamp is in the future",
      gi.due_for_refresh(10 ** 9), True)
gi.jsonstore.write_json(gi._path(), _saved)

from hub import scheduler as _sched                      # noqa: E402

_swept = []
_real_build = gi.build
gi.build = lambda force=True: _swept.append(force) or {"ok": True}
try:
    res = _sched.job_refresh_google_index(None)
    check("the scheduled job skips a sweep it does not need", _swept, [])
    check("...and says so with the age, rather than reporting a run",
          "rebuilt" in res.get("skipped", ""), True)
    gi.jsonstore.write_json(gi._path(), dict(gi.load(),
                                             built_at="2020-01-01T00:00:00+00:00"))
    _sched.job_refresh_google_index(None)
    check("...and sweeps when the index really has aged out", _swept, [True])
finally:
    gi.build = _real_build

section("A search never sweeps Google live — it reads the shared index")

# The bug this section exists for: /google/api/search called get_index()
# directly on every request, live, every time — on a cold worker (every
# gunicorn worker after a deploy) that meant a full rate-paced sweep of
# every connected login before the first search could answer. Proven by
# stubbing get_account_index to explode: if the route still answers
# correctly from a pre-populated index, it never touched Google.
gi.jsonstore.write_json(gi._path(), {
    "built_at": _hours_ago(1),
    "items": [{
        "platform": "Google Tag Manager", "name": "Riverside HVAC — Main",
        "account_name": "Riverside HVAC", "account_id": "1",
        "resource_id": "GTM-ABC1", "google_login": "rep@smart1marketing.com",
        "search_extra": "", "domains": [], "client": "Riverside HVAC",
        "match": "domain",
    }],
    "accounts": ["rep@smart1marketing.com"], "errors": [],
})

with gf.app.app_context():
    gf.save_account("rep@smart1marketing.com", "refresh-token-value")

_real_get_account_index = gf.get_account_index


def _must_not_sweep(*a, **k):
    raise AssertionError("a search must never sweep Google live")


gf.get_account_index = _must_not_sweep
gf.CACHE.clear()
web = gf.app.test_client()
try:
    r = web.get("/api/search?q=riverside&platform=all")
    check("a search that hit a live sweep would have raised",
          r.status_code, 200)
    body = r.get_json()
    check("...and instead answers from the stored index",
          [x["resource_id"] for x in body["results"]], ["GTM-ABC1"])
    check("...carrying the stored index's own age",
          body.get("index_age_hours") is not None, True)

    section("Never built is said outright, not swept live to hide it")
    gi.jsonstore.delete_json(gi._path())
    r = web.get("/api/search?q=riverside")
    body = r.get_json()
    check("a never-built index answers rather than sweeping to cover for it",
          r.status_code, 200)
    check("...and says outright that nothing has been swept yet",
          body.get("never_built"), True)
    check("...with no results invented to fill the gap", body.get("results"), [])
finally:
    gf.get_account_index = _real_get_account_index


section("A manual rebuild cools down, whichever screen asked for it")

# google_index.manual_rebuild() is what both /api/google/rebuild (the hub
# route behind /tools/google-match) and /google/api/refresh (Google Finder's
# own "not found? refresh" button) call now, so this is asserted once rather
# than once per screen — two readings of one cooldown is how they'd drift.
gi._last_manual_rebuild = 0.0                                    # noqa: SLF001
check("nothing pending, so no wait", gi.manual_rebuild_wait(), 0.0)
gi.note_manual_rebuild()
check("a rebuild just started leaves a wait", gi.manual_rebuild_wait() > 0, True)

_swept = []
gi.build = lambda force=True: _swept.append(force) or {"ok": True, "resources": 3}
try:
    out = gi.manual_rebuild()
    check("a cooling-down rebuild refuses rather than sweeping",
          out.get("cooling_down"), True)
    check("...and never calls build() at all", _swept, [])
    check("...and names when to try again",
          out.get("retry_after_seconds", 0) > 0, True)

    gi._last_manual_rebuild = 0.0                                # noqa: SLF001
    out = gi.manual_rebuild()
    check("once the cooldown has passed, it sweeps", _swept, [True])
    check("...and reports the build's own result", out.get("resources"), 3)

    check("a second call right after is cooled down again",
          gi.manual_rebuild().get("cooling_down"), True)
    check("...and still did not sweep a second time", _swept, [True])
finally:
    gi.build = _real_build
    gi._last_manual_rebuild = 0.0                                # noqa: SLF001


section("/google/api/refresh sweeps through the shared index, cooled down")

# This used to call get_index(force=True) directly: a live sweep on every
# press that only ever populated this one process's private CACHE, so the
# other gunicorn worker — and every page reading hub.google_index — never
# benefited from it. It goes through google_index.manual_rebuild() now, so
# the result lands in the one shared, cross-worker table.
_swept2 = []
gi.build = lambda force=True: (_swept2.append(force)
                               or {"ok": True, "resources": 7, "errors": []})
try:
    r = web.post("/api/refresh")
    check("a fresh refresh sweeps through the shared build()", _swept2, [True])
    check("...and reports what it swept", r.get_json().get("count"), 7)

    r = web.post("/api/refresh")
    check("an immediate second refresh is cooled down, not a second sweep",
          _swept2, [True])
    check("...and answers 429", r.status_code, 429)
    check("...naming when to try again",
          r.get_json().get("retry_after_seconds", 0) > 0, True)
finally:
    gi.build = _real_build
    gi._last_manual_rebuild = 0.0                                # noqa: SLF001

gi.jsonstore.write_json(gi._path(), _saved)


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
