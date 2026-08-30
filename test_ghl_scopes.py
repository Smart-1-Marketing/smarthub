"""hub/ghl_scopes.py — the Marketplace app's scope set, and the granted diff.

    python3 test_ghl_scopes.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

The scope set is a decision that costs an agency re-consent to change, and
every way of getting it wrong is quiet:

  1.  a write call site with no scope   — the feature runs on the agency Private
                                          Integration Token today and 401s the
                                          day it moves onto a location token
  2.  granted is not requested          — HighLevel grants what it recognises
                                          and says nothing about the rest, so a
                                          healthy token can be half a token
  3.  a missing scope must name a
      feature                           — "1 scope missing" sends nobody
                                          anywhere; "Social Planner cannot
                                          publish" does
  4.  verified and unverified missing
      scopes have different causes      — one is a permission to grant, the
                                          other is a typo of ours, and sending
                                          someone to re-consent for a typo
                                          wastes the one manual step this
                                          module exists to stop repeating
  5.  no scope list is not zero scopes  — HighLevel has been seen to omit the
                                          field; reporting "all missing" would
                                          condemn an install that is fine
  6.  the override replaces             — GHL_OAUTH_SCOPES is the mid-incident
                                          knob; silently re-adding a scope the
                                          operator removed makes it untrustworthy
  7.  omissions are named               — a scope left out on purpose is a
                                          decision, and reads as one
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ghlscopes_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.pop("GHL_OAUTH_SCOPES", None)
# hub/ghl_oauth.py reads these at import and status() short-circuits to
# "not configured" without them, so they are pinned before the import
# below. Nothing here reaches HighLevel.
os.environ["GHL_CLIENT_ID"] = "test-app-id-abc123"
os.environ["GHL_CLIENT_SECRET"] = "test-secret"

from hub import ghl_scopes  # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ------------------------------------------------------- 1. the set is whole
section("Every GHL write path in the Hub has a scope declared for it")

names = ghl_scopes.requested_names()

# The version of this that enumerated five known files could only re-confirm
# what somebody had already thought of, and two call sites slipped past it
# within months. So the tree is walked instead: every file that names the
# HighLevel host and performs a write must be claimed by some scope.
undeclared = ghl_scopes.undeclared_writes()
check("every GHL write call site in the tree is declared", undeclared, [])
check("no scope declares a file that no longer exists",
      ghl_scopes.stale_declarations(), [])

# The discovery has to actually find things — an empty result would satisfy the
# two checks above while proving nothing, which is the way this kind of check
# usually rots.
sites = ghl_scopes.write_call_sites()
check("the scan finds real call sites rather than nothing",
      len(sites) >= 5, True)
for known in ("hub/ghl_contacts.py", "hub/qa.py",
              "modules/social_planner/suite_client.py"):
    check(f"the scan sees {known}", known in sites, True)

# hub/suite_opportunity.py writes via requests.request(method, ...) with the
# verb in a variable — the idiom most likely to be copied into the next
# module. A hint list blind to it would give false comfort.
check("a variable-method write is still detected",
      "hub/suite_opportunity.py" in sites, True)

# The exemptions are named with a reason, never silently skipped.
for path, reason in ghl_scopes.WRITE_EXEMPT.items():
    check(f"{path} is exempt with a stated reason", len(reason.strip()) > 40, True)
check("the OAuth module is the exemption that matters",
      "hub/ghl_oauth.py" in ghl_scopes.WRITE_EXEMPT, True)

for path, scope in (("hub/ghl_contacts.py", "contacts.write"),
                    ("hub/suite_opportunity.py", "opportunities.write"),
                    ("hub/ghl_blog.py", "blogs/post.write"),
                    ("modules/image_picker/ghl.py", "medias.write"),
                    ("modules/social_planner/suite_client.py",
                     "social-media-posting.write")):
    check(f"{path} is covered by {scope}", scope in names, True)
    entry = ghl_scopes.by_name(scope)
    check(f"{scope} names {path} as a caller",
          bool(entry and path in entry.needed_by), True)

check("the eight read scopes the app already runs on are still asked for",
      all(n in names for n in (
          "locations.readonly", "forms.readonly", "forms/submissions.readonly",
          "contacts.readonly", "opportunities.readonly", "calendars.readonly",
          "conversations.readonly", "users.readonly")), True)

check("no scope is requested twice", len(names), len(set(names)))

# The blog scopes are shaped blogs/<thing>.<verb>, not blogs.<verb>. Getting
# that wrong is a whole feature lost to a plausible-looking string.
check("the blog scopes keep their slash form",
      [n for n in names if n.startswith("blogs")],
      ["blogs/list.readonly", "blogs/author.readonly", "blogs/category.readonly",
       "blogs/check-slug.readonly", "blogs/post.write", "blogs/post-update.write"])


# --------------------------------------------------- 2. every scope explains
section("A scope that cannot say what it buys cannot be reported on")

for s in ghl_scopes.REQUESTED:
    check(f"{s.name} names a feature", bool(s.feature.strip()), True)
    check(f"{s.name} names at least one call site", bool(s.needed_by), True)


# -------------------------------------------------------- 3. the granted diff
section("Granted is diffed against requested, and named by feature")

full = ghl_scopes.compare(" ".join(names))
check("a full grant reports nothing missing", full["missing"], [])
check("a full grant blocks no feature", full["blocked"], [])
check("a full grant is known", full["known"], True)

# Drop the one Social Planner is waiting on.
partial = ghl_scopes.compare(
    " ".join(n for n in names if n != "social-media-posting.write"))
check("the missing scope is listed",
      partial["missing"], ["social-media-posting.write"])
check("and the feature it costs is named, not just the string",
      any("Social Planner" in f for f in partial["blocked"]), True)
check("the row is not reported as complete", bool(partial["missing"]), True)


# ------------------------------------- 4. verified and unverified differ
section("A missing scope we have used before is a different problem")

# contacts.write is transcribed from hub/ghl_contacts.py, which writes every
# Hub lead with it. If that comes back missing the string is fine and the
# grant is not.
missing_known = ghl_scopes.compare(
    " ".join(n for n in names if n != "contacts.write"))
check("a proven string lands in missing_verified",
      missing_known["missing_verified"], ["contacts.write"])
check("and not in missing_unverified",
      missing_known["missing_unverified"], [])
check("the advice is to grant it, not to re-spell it",
      "Marketplace" in missing_known["detail"], True)

missing_guess = ghl_scopes.compare(
    " ".join(n for n in names if n != "medias.write"))
check("an unconfirmed string lands in missing_unverified",
      missing_guess["missing_unverified"], ["medias.write"])
check("and the advice is to check the spelling first",
      "wrong" in missing_guess["detail"], True)

check("a scope granted but never asked for is surfaced",
      ghl_scopes.compare(" ".join(names) + " snapshots.readonly")["unexpected"],
      ["snapshots.readonly"])


# ------------------------------------------ 5. absent is not zero
section("No scope list reads as not measured, never as nothing granted")

for empty in ("", None, []):
    blank = ghl_scopes.compare(empty)
    check(f"{empty!r} is reported as unknown", blank["known"], False)
    check(f"{empty!r} does not claim every scope is missing",
          blank["missing"], None)
    check(f"{empty!r} blocks no feature on no evidence", blank["blocked"], [])
    check(f"{empty!r} says so in words",
          "not measured" in blank["detail"], True)


# ------------------------------------------------- 6. the override replaces
section("GHL_OAUTH_SCOPES replaces the set rather than adding to it")

os.environ["GHL_OAUTH_SCOPES"] = "contacts.readonly locations.readonly"
check("the override wins outright",
      ghl_scopes.requested_names(), ["contacts.readonly", "locations.readonly"])
check("a scope the operator removed does not come back",
      "contacts.write" in ghl_scopes.requested_names(), False)

os.environ["GHL_OAUTH_SCOPES"] = "contacts.readonly, locations.readonly\ncontacts.write"
check("commas and newlines are accepted from a hand-edited variable",
      ghl_scopes.requested_names(),
      ["contacts.readonly", "locations.readonly", "contacts.write"])

os.environ["GHL_OAUTH_SCOPES"] = "  "
check("a blank override falls back to the table rather than asking for nothing",
      ghl_scopes.requested_names(), names)
os.environ.pop("GHL_OAUTH_SCOPES", None)

check("the authorize URL carries the whole set",
      ghl_scopes.scope_string(), " ".join(names))


# ------------------------------------------------- 7. omissions are on record
section("A scope left out on purpose is named with its reason")

omitted = dict(ghl_scopes.NOT_REQUESTED)
check("locations.write is deliberately absent", "locations.write" in omitted, True)
check("and it is not quietly requested anyway",
      "locations.write" in names, False)
for name, reason in ghl_scopes.NOT_REQUESTED:
    check(f"{name} gives a reason", len(reason.strip()) > 40, True)


# ------------------------------------------------------- 8. status() reports it
section("status() carries the diff, so the panel does not recompute it")

from hub import ghl_oauth  # noqa: E402

ghl_oauth._save({
    "access_token": "tok", "refresh_token": "ref", "company_id": "co",
    "scope": " ".join(n for n in names if n != "social-media-posting.write"),
    "expires_at": __import__("time").time() + 3600,
})
st = ghl_oauth.status()
check("status reports connected", st["connected"], True)
check("but not scope-complete", st["scopes_complete"], False)
check("and the detail names the shortfall",
      "not granted" in st["detail"], True)
check("the blocked feature travels with it",
      any("Social Planner" in f for f in st["scopes"]["blocked"]), True)

ghl_oauth.disconnect()
check("disconnect clears the record", ghl_oauth.connected(), False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
