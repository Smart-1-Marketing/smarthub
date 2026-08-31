"""Every OAuth scope the Marketplace app asks for, and what each one costs when
it is missing.

## Why this is a table and not a string

`DEFAULT_SCOPES` was one space-joined literal in `hub/ghl_oauth.py`: eight
read-only scopes and no write scope at all. Two things follow from that, and
both are the kind of quiet wrongness this codebase keeps having to undo.

**Adding a scope costs a re-consent.** A location token inherits whatever the
agency token was granted, so a scope missing here is missing for every
sub-account, for ever, until an agency owner sits through the consent screen
again. That makes the scope set a decision to be made *once, before the
install* — not something to extend the week a feature turns out to need it.
`modules/social_planner` is the standing example: it stops at a CSV because
`social-media-posting.write` was never asked for.

**HighLevel grants what it recognises and says nothing about the rest.** A
scope string it does not know is not an error — consent succeeds, a token comes
back, `status()` says *Connected*, and the one endpoint that needed it 401s
months later looking exactly like a bad token. The Suite panel printed the
granted list verbatim, which reads as confirmation and is nothing of the kind:
a list of eight scopes looks identical whether you asked for eight or fourteen.
So `compare()` diffs granted against requested and names the difference by the
**feature it costs**, not by the string. "Social Planner cannot publish" is
actionable; "1 scope missing" is not.

## What `verified` means

`verified=True` is a scope string this deployment has actually authenticated
with — transcribed from working code, not from documentation:

* the eight read scopes the app already requests and the panel already runs on;
* `contacts.write`, which every lead in `hub/ghl_contacts.py` is written with;
* the six blog scopes `hub/ghl_blog.py` publishes through.

`verified=False` is our best reading of the string, unconfirmed against
HighLevel's published list. It is still requested — asking for a scope that
does not exist costs nothing, and *not* asking costs a re-consent — but
`compare()` reports an unverified scope that came back missing differently from
a verified one, because the likely cause differs. A verified scope missing
means the app was not granted it (fix it on the app's own scope list). An
unverified one missing usually means the string is wrong (fix it here).

That distinction is the whole point of the file. Without it the first install
produces a list of missing scopes with no way to tell a HighLevel problem from
a typo of ours.

## What is deliberately not requested

See `NOT_REQUESTED`. A scope left out on purpose is named with its reason, so
the next person to read a 401 knows whether they are looking at an oversight or
at a decision.
"""
from __future__ import annotations

import os
from typing import NamedTuple


class Scope(NamedTuple):
    """One scope, and what stops working without it."""

    name: str
    feature: str          # named in the report when this scope is missing
    needed_by: tuple      # call sites, so the test can check none is orphaned
    verified: bool        # have we authenticated with this exact string?


# --------------------------------------------------------------------------
# Reads — the set the app already requests, all proven in production.
# --------------------------------------------------------------------------
# Named so callers gate on the table rather than restating the string.
# hub/suite_accounts.py held its own copy and drifted onto a non-existent name.
SCOPE_SOCIAL_WRITE = "socialplanner/post.write"
SCOPE_SOCIAL_READ = "socialplanner/post.readonly"

READ: tuple[Scope, ...] = (
    Scope("locations.readonly", "Sub-account lookup, and every domain-keyed join",
          ("modules/suite_panel/app.py", "hub/ghl_contacts.py", "hub/diagnostics.py"), True),
    # One scope, two endpoints. There is no forms/submissions scope in the
    # console; this Hub asked for one for months and it was marked known-good
    # purely because it was already in the original DEFAULT_SCOPES.
    # hub/ghl_forms.py's own docstring said forms.readonly all along.
    Scope("forms.readonly", "Forms on a sub-account, and their submission counts",
          ("hub/ghl_forms.py",), True),

    Scope("contacts.readonly", "Finding the contact a proposal is filed against",
          ("modules/suite_panel/app.py", "hub/suite_opportunity.py"), True),
    Scope("opportunities.readonly", "Pipeline discovery, and the opportunity list",
          ("modules/suite_panel/app.py", "hub/suite_opportunity.py"), True),
    Scope("calendars.readonly", "Calendar counts in the sub-account analytics panel",
          ("modules/suite_panel/app.py",), True),
    Scope("conversations.readonly", "Conversation counts in the same panel",
          ("modules/suite_panel/app.py",), True),
    Scope("users.readonly", "Who is on a sub-account",
          ("modules/suite_panel/app.py",), True),
)

# --------------------------------------------------------------------------
# Writes — everything below currently runs on the agency Private Integration
# Token. Requesting them here is what lets a per-sub-account token do the same
# work, which is the only reason the Marketplace app exists.
# --------------------------------------------------------------------------
WRITE: tuple[Scope, ...] = (
    Scope("contacts.write", "Lead delivery — every Hub form writes a contact",
          ("hub/ghl_contacts.py", "hub/suite_opportunity.py"), True),
    # hub/qa.py joined this months after the table was written: the accounting
    # QA report moves an opportunity's stage with PUT /opportunities/{id}/status.
    # Nothing named it until the coverage check below started discovering call
    # sites rather than re-reading a hand-written list.
    Scope("opportunities.write", "Filing a delivered proposal as an opportunity, "
          "and moving an accounting request's stage",
          ("hub/suite_opportunity.py", "hub/qa.py"), True),
    Scope("medias.write", "Pushing a client's images into their Suite media library",
          ("modules/image_picker/ghl.py", "modules/suite_panel/app.py"), True),

    # The blog set. hub/ghl_blog.py names all six in its own docstring and
    # publishes through them today, so the strings are transcribed rather than
    # guessed — note the shape is `blogs/<thing>.<verb>`, not `blogs.<verb>`.
    Scope("blogs/list.readonly", "Finding the client's blog site",
          ("hub/ghl_blog.py",), True),
    Scope("blogs/author.readonly", "A post requires an author id",
          ("hub/ghl_blog.py",), True),
    Scope("blogs/category.readonly", "A post requires at least one category",
          ("hub/ghl_blog.py",), True),
    Scope("blogs/check-slug.readonly", "Not colliding with an existing post's slug",
          ("hub/ghl_blog.py",), True),
    Scope("blogs/post.write", "Publishing a generated blog post",
          ("hub/ghl_blog.py",), True),
    Scope("blogs/post-update.write", "Editing a published post instead of duplicating it",
          ("hub/ghl_blog.py",), True),

    # The one this whole exercise is for. Social Planner ends at a CSV without
    # it; see modules/social_planner/app.py. Unverified, and deliberately asked
    # for anyway — the cost of a wrong string here is a line in the missing
    # report, and the cost of omitting it is another agency re-consent.
    # suite_client.py is where the POST actually happens; app.py orchestrates.
    # The table named only app.py while the pipe moved beneath it, which reads
    # as coverage and is not.
    # The family is socialplanner/<thing>.<verb>. It was social-media-posting.*
    # here until HighLevel's own console list was read on 2026-08-30 -- a name
    # that does not exist, which hub/suite_accounts.py was also gating the push
    # on, so publishing() would have answered "not granted" for ever, including
    # after a consent that granted the real scope.
    Scope(SCOPE_SOCIAL_WRITE, "Social Planner publishing instead of exporting a CSV",
          ("modules/social_planner/app.py",
           "modules/social_planner/suite_client.py"), True),
    Scope(SCOPE_SOCIAL_READ, "Reading back what Social Planner scheduled",
          ("modules/social_planner/app.py",), True),
)

REQUESTED: tuple[Scope, ...] = READ + WRITE

# --------------------------------------------------------------------------
# Left out on purpose. Each of these has a live call site running on the agency
# Private Integration Token, so the omission is a decision and not an oversight
# — which is exactly why it is written down rather than simply absent.
# --------------------------------------------------------------------------
NOT_REQUESTED: tuple[tuple[str, str], ...] = (
    ("locations.write",
     "Creating and deleting sub-accounts stays on the agency token. A "
     "location-scoped token that can delete its own location is a blast radius "
     "nothing in the Hub needs; modules/suite_panel/app.py does this "
     "deliberately as the agency."),
    ("snapshots.readonly",
     "Snapshots are an agency-level concept — a sub-account token would not "
     "widen what modules/suite_panel/app.py can already read."),
    ("workflows.readonly",
     "docs/ghl-nurture-automations.md establishes that the Workflows API is "
     "read-only with no create path, so a scope buys visibility into "
     "automations nobody builds from here. Add it when something reads them."),
)


# --------------------------------------------------------------------------
# Every scope HighLevel's own console offers, transcribed from the app's scope
# picker on 2026-08-30. 97 entries.
#
# This exists because "is that a real scope name?" was, until now, a question
# nobody here could answer. The set was assembled from what our own modules had
# authenticated with plus best readings of the rest, and three of nineteen were
# wrong -- including one inherited from the original DEFAULT_SCOPES and marked
# as known-good because it was already in the code. Membership of this list is
# now what `known()` means, so the answer comes from HighLevel rather than from
# our own confidence.
#
# It is a **snapshot**, not a live read: HighLevel publishes no endpoint for it
# and adds scopes as it ships features (agent-studio, voice-ai and brand-boards
# are all recent). A name absent from here is therefore "not in the list we
# captured", never "does not exist" -- which is why unknown_requested() is a
# finding to look at rather than an automatic failure.
# --------------------------------------------------------------------------
AVAILABLE: frozenset = frozenset({
    # Ad Publishing
    "adPublishing.readonly", "adPublishing.write",
    # AI Agent Studio
    "agent-studio.readonly", "agent-studio.write",
    # Associations
    "associations.write", "associations.readonly",
    "associations/relation.readonly", "associations/relation.write",
    # Blogs
    "blogs/post.write", "blogs/post-update.write", "blogs/check-slug.readonly",
    "blogs/category.readonly", "blogs/author.readonly", "blogs/list.readonly",
    # Brand Boards
    "brand-boards/design-kit.readonly", "brand-boards/design-kit.write",
    "brand-boards/voices.readonly", "brand-boards/voices.write",
    # Businesses
    "businesses.readonly", "businesses.write",
    # Calendars
    "calendars.readonly",
    # Campaigns
    "campaigns.readonly",
    # Chat Widget
    "chat-widget.readonly", "chat-widget.write",
    # Companies
    "companies.readonly",
    # Contacts
    "contacts.readonly", "contacts.write",
    # Conversations
    "conversations.readonly",
    # Custom Fields
    "locations/customFields.readonly", "locations/customFields.write",
    # Custom Menus
    "custom-menu-link.readonly", "custom-menu-link.write",
    # Developer Marketplace
    "charges.readonly", "charges.write",
    "marketplace-installer-details.readonly",
    "marketplace-external-auth-migration.write",
    # Emails
    "emails/builder.write", "emails/builder.readonly",
    "emails/schedule.readonly", "emails/schedule.write",
    "emails/templates.readonly", "emails/templates.write",
    "emails/campaigns.readonly", "emails/campaigns.write",
    "emails/stats.readonly",
    # Files
    "files.readonly",
    # Forms  -- note there is NO forms/submissions scope; forms.readonly covers
    # both the form list and its submissions.
    "forms.readonly", "forms.write",
    # Knowledge Base
    "knowledge-bases.write", "knowledge-bases.readonly",
    # Lc Email
    "lc-email.readonly",
    # Locations
    "locations.write", "locations.readonly",
    "locations/customValues.readonly", "locations/customValues.write",
    "locations/tasks.readonly", "locations/tasks.write",
    "recurring-tasks.readonly", "recurring-tasks.write",
    "locations/tags.readonly", "locations/tags.write",
    "locations/templates.readonly",
    # Medias -- write only; there is no medias.readonly
    "medias.write",
    # Oauth
    "oauth.write", "oauth.readonly",
    # Objects
    "objects/schema.readonly", "objects/schema.write",
    "objects/record.readonly", "objects/record.write",
    # Opportunities
    "opportunities.readonly", "opportunities.write",
    # Social Planner -- the family is socialplanner/<thing>.<verb>, NOT
    # social-media-posting.*, which is what this Hub asked for until today.
    "socialplanner/oauth.readonly", "socialplanner/oauth.write",
    "socialplanner/post.readonly", "socialplanner/post.write",
    "socialplanner/account.readonly", "socialplanner/account.write",
    "socialplanner/csv.readonly", "socialplanner/csv.write",
    "socialplanner/category.readonly", "socialplanner/category.write",
    "socialplanner/tag.readonly", "socialplanner/tag.write",
    "socialplanner/statistics.readonly",
    "socialplanner/comments.readonly", "socialplanner/comments.write",
    "socialplanner/watermarks.readonly", "socialplanner/watermarks.write",
    # Twilio Account -- note ".read", not ".readonly"
    "twilioaccount.read",
    # Users
    "users.readonly",
    # Voice AI
    "voice-ai-dashboard.readonly",
    "voice-ai-agents.readonly", "voice-ai-agents.write",
    "voice-ai-agent-goals.readonly", "voice-ai-agent-goals.write",
    # Wordpress
    "wordpress.site.readonly",
    # Workflows
    "workflows.readonly",
})

AVAILABLE_CAPTURED = "2026-08-30"


def known(name: str) -> bool:
    """Does HighLevel's own console list this scope?

    The replacement for a hand-kept `verified` flag. That flag meant "somebody
    was confident", and confidence is what got `forms/submissions.readonly`
    into the set and kept it there.
    """
    return name in AVAILABLE


def unknown_requested() -> list[str]:
    """Requested scopes HighLevel's console does not list.

    Would have caught all three of the errors found on 2026-08-30 at the moment
    they were written, rather than at consent.
    """
    return [n for n in requested_names() if n not in AVAILABLE]


# --------------------------------------------------------------------------
# Which files actually write to HighLevel
#
# `needed_by` above is documentation, and documentation drifts. Two entries had
# already gone stale within a few months of this file being written: hub/qa.py
# grew a `PUT /opportunities/{id}/status` that no scope named, and the Social
# Planner's real posting pipe moved from app.py into suite_client.py while the
# table went on naming app.py. Neither was caught, because the test enumerated
# five known call sites by hand — so it could only ever re-confirm what
# somebody had already thought of.
#
# The invariant below is deliberately the weak one: every file that performs a
# GHL write must be named in *some* scope's `needed_by`. It does not try to
# infer which scope a given endpoint needs — that inference is where the false
# positives live, and a check people learn to ignore is worse than no check.
# Being named is enough to guarantee somebody looked.
# --------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files that reach the API host but perform no scoped write, with the reason.
# Named rather than silently skipped, for the same reason NOT_REQUESTED is.
WRITE_EXEMPT: dict[str, str] = {
    "hub/ghl_oauth.py":
        "POSTs the OAuth token and locationToken endpoints, which authenticate "
        "the app itself rather than acting on a sub-account. No scope governs "
        "them — they are what issues the scopes.",
    "hub/ghl_scopes.py":
        "This file. It describes the scopes and calls nothing.",
}

_HOST_HINT = ("leadconnectorhq", "GHL_API_BASE")
_WRITE_HINT = (
    'method="POST"', "method='POST'", 'method="PUT"', "method='PUT'",
    'method="PATCH"', "method='PATCH'", 'method="DELETE"', "method='DELETE'",
    "requests.post(", "requests.put(", "requests.patch(", "requests.delete(",
    "session.post(", "session.put(", "session.delete(",
    # The dominant idiom in this codebase: requests.request(method, ...) with
    # the verb in a variable. hub/suite_opportunity.py and
    # modules/suite_panel/app.py both write that way, so a hint list without it
    # misses the shape most likely to be copied into the next module — and a
    # check blind to the common case gives false comfort rather than none.
    # It over-matches a file that only ever passes "GET"; that file being named
    # in the table costs one line and proves somebody looked.
    "requests.request(",
)


def _tree_sources():
    """Every module and hub source file, as (relative path, text).

    Tests are skipped: they build fixtures and assert on payload shapes, so a
    write verb in one is not a call site anybody has to hold a scope for.
    """
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("_attic", "node_modules", "__pycache__", ".git")]
        for name in filenames:
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, _ROOT).replace(os.sep, "/")
            if not (rel.startswith("hub/") or rel.startswith("modules/")):
                continue
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    yield rel, fh.read()
            except OSError:
                continue


def write_call_sites() -> list[str]:
    """Files that name the HighLevel API host and perform a write against it."""
    found = []
    for rel, text in _tree_sources():
        if rel in WRITE_EXEMPT:
            continue
        if not any(h in text for h in _HOST_HINT):
            continue
        if not any(w in text for w in _WRITE_HINT):
            continue
        found.append(rel)
    return sorted(found)


def declared_files() -> set[str]:
    """Every file named by any scope in the table."""
    out: set[str] = set()
    for s in REQUESTED:
        out.update(s.needed_by)
    return out


def undeclared_writes() -> list[str]:
    """GHL write call sites that no scope claims.

    The failure this catches is silent by construction. The call runs on the
    agency Private Integration Token today and works, so nothing is broken —
    right up until it moves onto a location token, where the scope was never
    consented to and it 401s looking exactly like a bad token, for every client
    at once.
    """
    declared = declared_files()
    return [f for f in write_call_sites() if f not in declared]


def stale_declarations() -> list[str]:
    """Files named in `needed_by` that no longer exist.

    A renamed or deleted call site leaves an entry that reads as coverage and
    is not, which is how social-media-posting.write came to name app.py after
    the posting moved into suite_client.py.
    """
    return sorted(f for f in declared_files()
                  if not os.path.exists(os.path.join(_ROOT, f)))


# ------------------------------------------------------------------ requested
def requested_names() -> list[str]:
    """The scope strings to ask for, honouring the environment override.

    `GHL_OAUTH_SCOPES` replaces the set outright rather than adding to it: an
    override that silently kept a scope the operator removed would make the
    variable untrustworthy, and this is the one knob available mid-incident.
    """
    override = (os.environ.get("GHL_OAUTH_SCOPES") or "").strip()
    if override:
        return _split(override)
    return [s.name for s in REQUESTED]


def scope_string() -> str:
    return " ".join(requested_names())


def _split(raw: str) -> list[str]:
    """Scopes arrive space-separated, but a hand-edited env var arrives however
    it was typed. Commas and newlines are accepted, and order is preserved so a
    report reads the way the operator wrote it."""
    out, seen = [], set()
    for part in (raw or "").replace(",", " ").split():
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return out


def by_name(name: str) -> Scope | None:
    for s in REQUESTED:
        if s.name == name:
            return s
    return None


# ------------------------------------------------------------------- compare
def compare(granted: str | list[str] | None) -> dict:
    """Diff what HighLevel granted against what we asked for.

    `granted` is the `scope` field off the token response. HighLevel has been
    seen to omit it entirely; that is **not** evidence of nothing being granted,
    so it comes back as `known: False` and every count is None rather than
    zero. A missing-scope report that reads "all 20 missing" because the field
    was absent would send someone to re-consent an install that is fine.
    """
    if isinstance(granted, str):
        have = _split(granted)
    elif granted:
        have = _split(" ".join(granted))
    else:
        have = []

    asked = requested_names()

    if not have:
        return {
            "known": False,
            "granted": [],
            "requested": asked,
            "missing": None,
            "missing_verified": None,
            "missing_unverified": None,
            "unexpected": None,
            "blocked": [],
            "detail": "HighLevel did not report a scope list, so what was "
                      "granted is not measured. It is not evidence that "
                      "nothing was granted.",
        }

    have_set = set(have)
    missing = [n for n in asked if n not in have_set]
    unexpected = [n for n in have if n not in set(asked)]

    verified, unverified = [], []
    for name in missing:
        s = by_name(name)
        (verified if (s and s.verified) else unverified).append(name)

    blocked = []
    for name in missing:
        s = by_name(name)
        if s and s.feature not in blocked:
            blocked.append(s.feature)

    return {
        "known": True,
        "granted": have,
        "requested": asked,
        "missing": missing,
        "missing_verified": verified,
        "missing_unverified": unverified,
        "unexpected": unexpected,
        "blocked": blocked,
        "detail": _detail(missing, verified, unverified),
    }


def _detail(missing: list[str], verified: list[str], unverified: list[str]) -> str:
    if not missing:
        return "Every requested scope was granted."
    bits = [f"{len(missing)} requested scope(s) were not granted."]
    if verified:
        bits.append(
            "Known-good string(s) missing — add them to the app's scope list "
            "in the Marketplace and re-consent: " + ", ".join(verified) + ".")
    if unverified:
        bits.append(
            "Unconfirmed string(s) missing — most likely the scope name is "
            "wrong rather than withheld; check it against HighLevel's list "
            "before re-consenting: " + ", ".join(unverified) + ".")
    return " ".join(bits)
