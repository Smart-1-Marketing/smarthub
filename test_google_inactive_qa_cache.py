"""The inactive-accounts scan does not re-check a resource it just checked.

    python3 test_google_inactive_qa_cache.py

No pytest, no new dependencies, a throwaway data directory -- the same shape
as every other test file here. Nothing reaches Google: `_ga_properties`,
`_ga_activity`, `_ga_measurement_ids`, `_gtm_accounts`, `_gtm_containers`
and `_live_tags` are all stubbed, and every call is counted.

## What this is asserting

A GA4 property or GTM container checked inside RESOURCE_STALE_HOURS is read
back out of google_inactive_qa_resource_cache.json on the next scan rather
than spending another live call on an answer that cannot have changed --
that is the whole of "held in a table so we don't have to perform the same
scan over and over". `full=True` is the separate "Full rescan" control that
ignores the cache entirely.

**A cached GTM container's *verdict* is still live.** Its own live-tags
fetch is skipped, but whether it reads active or inactive is recomputed
against this scan's own by_mid map every time -- a GA4 property coming back
to life must be reflected on every container linked to it without either
side needing to be re-fetched from Google.

**Previously-inactive or never-seen resources are checked before
previously-active ones.** With the worker pool pinned to one at a time,
that ordering is deterministic and directly observable in the order
resources are classified.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-googleaccess-cache-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["HUB_LEADS_FILE"] = os.path.join(_TMP, "leads.jsonl")
os.environ.setdefault("SECRET_KEY", "google-access-cache-test")
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


from modules.google_access import qa_inactive as qa  # noqa: E402

LOGIN = "adops@example.com"


class _FakeFinder:
    def refresh_access_token(self, login, refresh):
        return "tok"


qa._finder = _FakeFinder

# Two GA4 properties (one dead, one live) and one GTM container whose live
# tag references the dead property -- so it starts out classified inactive
# too, purely by linkage.
PROPERTIES = [
    {"account": "Acme", "account_id": "1", "name": "dead.example.com", "property_id": "p-dead"},
    {"account": "Acme", "account_id": "1", "name": "live.example.com", "property_id": "p-live"},
]
ACTIVITY = {"p-dead": {"events": 0, "sessions": 0}, "p-live": {"events": 40, "sessions": 12}}
MIDS = {"p-dead": {"G-DEAD0001"}, "p-live": {"G-LIVE0001"}}
GTM_ACCOUNTS = [{"accountId": "9001", "name": "Tag Manager"}]
GTM_CONTAINERS = [{"containerId": "c-linked", "name": "Main site", "publicId": "GTM-XXXX"}]
GTM_TAGS = [{"parameter": [{"key": "measurementId", "value": "G-DEAD0001"}]}]

calls = {"ga4_properties": 0, "ga4_activity": 0, "ga4_mids": 0,
         "gtm_accounts": 0, "gtm_containers": 0, "gtm_live_tags": 0}
order: list[str] = []


def _reset_counts():
    for k in calls:
        calls[k] = 0
    order.clear()


def _fake_ga_properties(token):
    calls["ga4_properties"] += 1
    return list(PROPERTIES)


def _fake_ga_activity(token, property_id):
    calls["ga4_activity"] += 1
    order.append(f"activity:{property_id}")
    return dict(ACTIVITY[property_id])


def _fake_ga_measurement_ids(token, property_id):
    calls["ga4_mids"] += 1
    return set(MIDS[property_id])


def _fake_gtm_accounts(token):
    calls["gtm_accounts"] += 1
    return list(GTM_ACCOUNTS)


def _fake_gtm_containers(token, account_id):
    calls["gtm_containers"] += 1
    return list(GTM_CONTAINERS)


def _fake_live_tags(token, account_id, container_id):
    calls["gtm_live_tags"] += 1
    order.append(f"live_tags:{container_id}")
    return list(GTM_TAGS), ""


qa._ga_properties = _fake_ga_properties
qa._ga_activity = _fake_ga_activity
qa._ga_measurement_ids = _fake_ga_measurement_ids
qa._gtm_accounts = _fake_gtm_accounts
qa._gtm_containers = _fake_gtm_containers
qa._live_tags = _fake_live_tags


def _scan(resource_cache, new_cache, full=False, cutoff_iso=""):
    return qa._scan_login(LOGIN, "refresh", resource_cache=resource_cache,
                           new_cache=new_cache, full=full, cutoff_iso=cutoff_iso)


import datetime as dt  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)
FRESH_CUTOFF = (NOW - dt.timedelta(hours=qa.RESOURCE_STALE_HOURS)).isoformat(timespec="seconds")
EXPIRED_CUTOFF = (NOW + dt.timedelta(hours=1)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
section("A first scan checks everything and populates the cache")
# ---------------------------------------------------------------------------
_reset_counts()
cache1: dict = {}
inactive, review, active = _scan({}, cache1, cutoff_iso=FRESH_CUTOFF)
check("both properties were checked live", calls["ga4_activity"] == 2, calls)
check("both properties' measurement ids were read", calls["ga4_mids"] == 2, calls)
check("the container's live tags were read once", calls["gtm_live_tags"] == 1, calls)
check("the dead property is inactive", any(r["resource"] == "p-dead" for r in inactive))
check("the live property is active", any(r["resource"] == "p-live" for r in active))
check("the linked container reads inactive, by linkage",
      any(r["resource"] == "c-linked" and r["status"] == "inactive"
          for r in (inactive + review + active)))
check("the cache now holds both GA4 properties",
      qa._skip_key("GA4", LOGIN, "p-dead") in cache1
      and qa._skip_key("GA4", LOGIN, "p-live") in cache1)
check("...and the GTM container", qa._skip_key("GTM", LOGIN, "c-linked") in cache1)
check("a cached GA4 entry carries its measurement ids for next time",
      cache1[qa._skip_key("GA4", LOGIN, "p-live")]["measurement_ids"] == ["G-LIVE0001"])


# ---------------------------------------------------------------------------
section("A second scan inside the staleness window checks nothing again")
# ---------------------------------------------------------------------------
_reset_counts()
cache2: dict = {}
inactive2, review2, active2 = _scan(cache1, cache2, cutoff_iso=FRESH_CUTOFF)
check("the properties list is still asked for (cheap, finds new resources)",
      calls["ga4_properties"] == 1, calls)
check("but no property activity call was repeated", calls["ga4_activity"] == 0, calls)
check("...nor a measurement-id lookup", calls["ga4_mids"] == 0, calls)
check("the container list is still asked for", calls["gtm_containers"] == 1, calls)
check("but its live tags were not re-fetched", calls["gtm_live_tags"] == 0, calls)
check("the classification came out identical from cache alone",
      {r["resource"] for r in inactive2} == {r["resource"] for r in inactive}
      and {r["resource"] for r in active2} == {r["resource"] for r in active})
check("the replayed cache entry keeps its original checked_at rather than resetting it",
      cache2[qa._skip_key("GA4", LOGIN, "p-live")]["checked_at"]
      == cache1[qa._skip_key("GA4", LOGIN, "p-live")]["checked_at"])


# ---------------------------------------------------------------------------
section("full=True ignores the cache and checks live again")
# ---------------------------------------------------------------------------
_reset_counts()
cache3: dict = {}
_scan(cache1, cache3, full=True, cutoff_iso=FRESH_CUTOFF)
check("every property was checked live despite a warm cache", calls["ga4_activity"] == 2, calls)
check("every container's live tags were re-fetched", calls["gtm_live_tags"] == 1, calls)


# ---------------------------------------------------------------------------
section("A GA4 property outliving the staleness window is checked again")
# ---------------------------------------------------------------------------
_reset_counts()
cache4: dict = {}
_scan(cache1, cache4, cutoff_iso=EXPIRED_CUTOFF)
check("a cutoff in the future makes every cached entry stale",
      calls["ga4_activity"] == 2, calls)


# ---------------------------------------------------------------------------
section("A cached GTM container's verdict tracks a property that came back")
# ---------------------------------------------------------------------------
# The container itself is still cache-fresh (its own live tags are not
# re-fetched) but the property it links to is forced stale, comes back
# with activity this time, and the container must read active without
# Tag Manager ever being asked about it again.
_reset_counts()
cache5 = dict(cache1)
del cache5[qa._skip_key("GA4", LOGIN, "p-dead")]          # force that one property stale
ACTIVITY["p-dead"] = {"events": 5, "sessions": 3}          # ...and now it has traffic
try:
    cache6: dict = {}
    inactive5, review5, active5 = _scan(cache5, cache6, cutoff_iso=FRESH_CUTOFF)
    check("the revived property was checked live", calls["ga4_activity"] == 1, calls)
    check("the container's own live tags were not re-fetched", calls["gtm_live_tags"] == 0, calls)
    check("the container now reads active, from cache alone",
          any(r["resource"] == "c-linked" and r["status"] == "active"
              for r in (inactive5 + review5 + active5)),
          [r["status"] for r in (inactive5 + review5 + active5) if r["resource"] == "c-linked"])
finally:
    ACTIVITY["p-dead"] = {"events": 0, "sessions": 0}      # restore for later sections


# ---------------------------------------------------------------------------
section("A previously-active property is checked after a never-seen one")
# ---------------------------------------------------------------------------
_reset_counts()
qa.GA4_WORKERS = 1  # pin to one worker so submission order is observable
try:
    cache7 = {qa._skip_key("GA4", LOGIN, "p-live"): {**cache1[qa._skip_key("GA4", LOGIN, "p-live")],
                                                       "checked_at": "2000-01-01T00:00:00+00:00"}}
    cache8: dict = {}
    _scan(cache7, cache8, cutoff_iso=FRESH_CUTOFF)
    check("both properties still needed a fresh check (cache entry forced stale)",
          calls["ga4_activity"] == 2, calls)
    check("the never-cached-inactive property is queued ahead of the known-active one",
          order.index("activity:p-dead") < order.index("activity:p-live"), order)
finally:
    qa.GA4_WORKERS = 6


# ---------------------------------------------------------------------------
section("A resource cache entry survives its own round trip through storage")
# ---------------------------------------------------------------------------
qa._save_resource_cache(cache1)
reloaded = qa._resource_cache()
check("the saved cache reads back as a dict", isinstance(reloaded, dict))
check("...with the same two GA4 keys",
      {qa._skip_key("GA4", LOGIN, "p-dead"), qa._skip_key("GA4", LOGIN, "p-live")}
      <= set(reloaded))


# ---------------------------------------------------------------------------
section("A deploy mid-scan does not erase resources not yet reached")
# ---------------------------------------------------------------------------
# This Hub redeploys on every merge to main, often several times an hour --
# the process a scan is running on can be killed mid-flight at any point.
# _run_scan() used to save the resource cache exactly once, after every
# login had been covered, so an interrupted run threw away everything it had
# already checked. It saves after each login now; the fix that matters is
# that the *incremental* save merges onto the cache as it stood before this
# run rather than replacing it outright -- new_cache only holds the logins
# reached so far, and writing that wholesale after login A would erase a
# perfectly good cached entry for login B, which the run has not reached yet
# and may never reach if the next deploy lands first.
LOGIN_A, LOGIN_B = "a@example.com", "b@example.com"


class _TwoLoginFinder:
    def connected_accounts_result(self):
        return ([{"email": LOGIN_A, "refresh_token": "ra", "status": "ACTIVE"},
                  {"email": LOGIN_B, "refresh_token": "rb", "status": "ACTIVE"}], "")


saves: list[dict] = []
_real_save_resource_cache = qa._save_resource_cache


def _recording_save(data):
    saves.append(dict(data))
    _real_save_resource_cache(data)


def _fake_scan_login_for_run(login, refresh, on_progress=None, resource_cache=None,
                              new_cache=None, full=False, cutoff_iso=""):
    # A minimal stand-in for the real _scan_login: it writes one GA4 entry
    # into new_cache for whichever login called it, the same contract the
    # real function honours.
    if new_cache is not None:
        new_cache[qa._skip_key("GA4", login, "p-only")] = {
            "kind": "GA4", "login": login, "account": "Acme", "account_id": "1",
            "name": f"{login}-site", "resource": "p-only", "public_id": "",
            "events": 0, "sessions": 0, "status": "inactive",
            "reason": "0 events and 0 sessions", "measurement_ids": [],
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
    return [], [], []


# Seed the on-disk cache with an entry for login B, as if it had been
# checked recently by an earlier, separately-completed run.
b_key = qa._skip_key("GA4", LOGIN_B, "pre-existing")
qa._save_resource_cache({b_key: {
    "kind": "GA4", "login": LOGIN_B, "account": "Acme", "account_id": "1",
    "name": "b-site", "resource": "pre-existing", "public_id": "",
    "events": 12, "sessions": 4, "status": "active", "reason": "Activity detected",
    "measurement_ids": [], "checked_at": NOW.isoformat(timespec="seconds"),
}})

_real_finder, _real_scan_login = qa._finder, qa._scan_login
qa._finder = _TwoLoginFinder
qa._scan_login = _fake_scan_login_for_run
qa._save_resource_cache = _recording_save
try:
    qa._run_scan(full=False)
finally:
    qa._finder, qa._scan_login = _real_finder, _real_scan_login
    qa._save_resource_cache = _real_save_resource_cache

check("the scan saved the cache more than once (once per login, not only at the end)",
      len(saves) >= 2, len(saves))
check("the save taken right after login A still carries login B's untouched entry",
      b_key in saves[0], saves[0])
check("...because it had not been reached yet when that save happened",
      qa._skip_key("GA4", LOGIN_B, "p-only") not in saves[0], saves[0])
check("the final save, after both logins, carries both logins' own findings",
      qa._skip_key("GA4", LOGIN_A, "p-only") in saves[-1]
      and qa._skip_key("GA4", LOGIN_B, "p-only") in saves[-1], saves[-1])
check("...and login B's pre-existing entry is gone from the final save "
      "(the self-pruning wholesale replace, once every login is covered)",
      b_key not in saves[-1], saves[-1])


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
