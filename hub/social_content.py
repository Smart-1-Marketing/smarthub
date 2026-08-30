"""Smart 1 Hub — the social content spec: intake, ideas and the push contract.

`hub/social_plan.py` says what a *post* is — the channels, the post-type mix,
the calendar arithmetic and the copy checks. This says what everything
*around* a post is: where the ask came from, who asked, whether two locations
asked for the same week, what a client is being offered to swipe on, and what
has to be true before anything reaches Smart 1 Suite.

It is data and arithmetic with no Flask in it, read by the module, the
client-facing pages and `test_social_content.py` alike — the arrangement
`hub/proposal_spec.py` and `hub/social_plan.py` already work to, so changing
what a request *is* is one edit rather than four.

## Why this sits beside the planner rather than in a module of its own

Because a second module is a second description of what a post is. The
Proposal Builder was two tools for one job for a year and the same client got
quoted two ways depending on which one a rep opened; this file exists so that
cannot happen to social. A month drafted by a strategist, an idea a client
liked and a photograph a location manager emailed in all converge on the same
slot, in the same batch, going out through the same export or the same push.

## The client structure this is built around

A client account is one Suite sub-account and one social presence. It can have
several physical **locations**, each with a person who wants something posted.
Today those arrive as separate emails and nothing sees them against each
other. A location here is therefore a Hub-only organizing idea: it sorts and
attributes a request, and it never gets its own posting destination, because
one shared page is exactly what the client has.

## Four rules, each a way to be confidently wrong

**A duplicate flag is advisory and never acts.** Two locations asking for the
same week is as often two real asks as it is one ask twice, and the system
cannot tell. It flags, a person decides, and nothing is auto-merged or
auto-declined.

**A client's own words are authorization.** `hub/social_plan.py` blocks a
price, a percentage, a deadline or a phone number that nobody supplied,
because a model that invents "$50 off through Friday" gets the client a phone
call about an offer they never made. A location manager typing "$50 off
through Friday" into the request form *is* the supply — so a promoted
request carries its own copy into the facts the checks are run against, or
the tool blocks the client's own offer and reads as broken.

**A turnaround time is measured or it is not promised.** The confirmation
screen wants to say "ready to review by X". A number nobody has measured is a
commitment made on the client's behalf, so `turnaround_note()` answers from
the requests actually triaged and says *not measured* until there are some.

**Nothing here publishes.** Every path — intake, ideas, the agent — produces
a draft. A human is the step before anything is pushed, and the push itself
is gated on a scope the agency has not yet consented to (§`suite_client`).
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

# The copy checks are the planner's. Imported rather than restated: same
# failure mode, so the next fix to those patterns lands once.
from hub.social_plan import (BANNED_PHRASES, DEADLINE_RE, MONEY_RE, PHONE_RE,
                             PLACEHOLDER_RE, SUPERLATIVE_RE, validate_copy)

__all__ = [
    "REQUEST_TYPES", "REQUEST_STATUSES", "REQUEST_FLOW", "DATE_MODES",
    "POST_STATUSES", "IDEA_TAGS", "GUARDRAILS",
    "batch_size", "explore_ratio", "duplicate_window_days",
    "request_type_label", "status_label", "idea_tag_label",
    "duplicate_flags", "is_overdue", "request_window", "windows_overlap",
    "tag_weight", "apply_response", "idea_mix", "authorized_text",
    "turnaround_note", "check_spec",
]


# =====================================================================
# Settings
#
# Read at call time, never at import. A module that reads a setting at import
# is one where adding the variable to Render changes nothing until the next
# deploy — the failure the Commercial Builder's ElevenLabs key had, where
# every screen stayed healthy and the voice track was silent.
# =====================================================================
def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(str(os.environ.get(name) or "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def batch_size() -> int:
    """Ideas offered to a client in one batch. 5-8 is this spec's default and
    is a judgment rather than a measurement — five is a glance, twenty is a
    form, and a batch nobody finishes teaches people to ignore the next one."""
    return _env_int("SOCIAL_SUGGESTION_BATCH_SIZE", 6, 3, 12)


def explore_ratio() -> float:
    """The share of a batch spent on tags this client has never answered on.

    Without it the mix converges on whatever they liked first and the tool
    stops being able to learn that they would also have liked something else.
    80/20 is a reasonable default, not a measured one, and it says so here
    rather than on a screen that would read as a finding.
    """
    return _env_float("SOCIAL_TAG_EXPLORE_RATIO", 0.2, 0.0, 0.6)


def duplicate_window_days() -> int:
    """How near two requested dates have to be to raise the flag."""
    return _env_int("SOCIAL_REQUEST_DUPLICATE_WINDOW_DAYS", 2, 0, 30)


# =====================================================================
# What a request is
# =====================================================================
REQUEST_TYPES: dict[str, dict] = {
    "post": {
        "label": "A post",
        "help": "Something to put up — a photo, an update, a bit of news.",
    },
    "promo": {
        "label": "A promotion or offer",
        "help": "A discount, a bundle, a limited-time deal.",
        # A promo is the one type whose copy routinely carries a price and a
        # deadline. Both are blocking flags when a model writes them and
        # neither is when the client does — see authorized_text().
        "carries_offer": True,
    },
    "event": {
        "label": "An event",
        "help": "An open day, a sponsorship, a stand at a show.",
        "carries_offer": True,
    },
    "review_response": {
        "label": "Responding to a review",
        "help": "Something said about us online that we want to answer.",
    },
    "other": {"label": "Something else", "help": "Tell us in the notes."},
}

DATE_MODES: dict[str, str] = {
    "asap": "As soon as you can",
    "specific_date": "On a particular day",
    "date_range": "Some time in a window",
}

# The order is the flow, and the flow is read off this tuple rather than
# written out again in a template: a stepper with its own hand-typed copy is
# how the Commercial Builder came to say "4. Storyboard" on step five.
REQUEST_FLOW = ("new", "triaged", "scheduled", "posted")

REQUEST_STATUSES: dict[str, dict] = {
    "new": {"label": "New", "tone": "amber",
            "help": "Nobody has looked at this yet."},
    "triaged": {"label": "Triaged", "tone": "blue",
                "help": "Read, and a draft is being built for it."},
    "scheduled": {"label": "Scheduled", "tone": "blue",
                  "help": "The post it became has a date on it."},
    "posted": {"label": "Posted", "tone": "green",
               "help": "It went out."},
    "declined": {"label": "Declined", "tone": "gray",
                 "help": "We are not doing this one, and the reason is on it."},
    "duplicate": {"label": "Duplicate", "tone": "gray",
                  "help": "Somebody confirmed this is the same ask as another."},
}

# A request that has not reached one of these is still somebody's to answer.
OPEN_STATUSES = ("new", "triaged")

# ---------------------------------------------------------------- posts
#
# The planner's own slot statuses (empty / drafted / edited / approved) say how
# far a slot is through *drafting*. These say how far a post is through
# *delivery*, which is a different axis and deliberately a separate word: a
# slot can be approved by a strategist and still be nowhere near a client.
POST_STATUSES: dict[str, dict] = {
    "idea": {"label": "Idea", "tone": "gray"},
    "drafting": {"label": "Being written", "tone": "gray"},
    "pending_client_approval": {"label": "With the client", "tone": "amber"},
    "approved": {"label": "Client approved", "tone": "green"},
    "changes_requested": {"label": "Changes asked for", "tone": "amber"},
    "rejected": {"label": "Client said no", "tone": "gray"},
    "pushed": {"label": "Sent to Suite", "tone": "blue"},
    "scheduled": {"label": "Scheduled in Suite", "tone": "blue"},
    "published": {"label": "Published", "tone": "green"},
    "failed": {"label": "Push failed", "tone": "red"},
}

# The one status a failed push may never become. A post the client approved
# that quietly reads as scheduled is the failure §2 of the spec is written
# about: it is gone, and the queue says it is handled.
NEVER_ON_FAILURE = ("scheduled", "published")


# =====================================================================
# What an idea is
# =====================================================================
IDEA_TAGS: dict[str, dict] = {
    "testimonial":      {"label": "A customer's own words",
                         "prompt": "a short quote or story from a happy customer"},
    "promo":            {"label": "An offer",
                         "prompt": "a current offer, discount or bundle"},
    "educational":      {"label": "Explaining something",
                         "prompt": "a question customers actually ask, answered plainly"},
    "seasonal":         {"label": "Something about the time of year",
                         "prompt": "the season, the weather or a dated hook"},
    "team_spotlight":   {"label": "Somebody who works here",
                         "prompt": "one member of staff and what they do"},
    "behind_the_scenes": {"label": "How the work gets done",
                          "prompt": "the part of the job customers never see"},
    "review_response":  {"label": "Answering a review",
                         "prompt": "a public thank-you or reply to feedback"},
    "announcement":     {"label": "News",
                         "prompt": "an opening, a hire, an award, a new service"},
    "evergreen":        {"label": "Always true",
                         "prompt": "something worth saying in any month"},
}


GUARDRAILS: tuple[str, ...] = (
    "Intake, the suggestion engine and the internal agent all produce ideas "
    "and drafts. A person is always the step before anything is pushed.",
    "No performance number is invented. What is shown is what Suite's API "
    "returned, and a figure we could not read reads as not measured.",
    "AI copy and AI images are one option among several and are never "
    "substituted for a human source that exists.",
    "A duplicate flag is advisory. Nothing is auto-merged and nothing is "
    "auto-declined.",
    "A failed push leaves the post approved, never scheduled, and says so in "
    "the staff queue with the error on it.",
    "Client-facing links are signed, carry no login, and never draw the staff "
    "sidebar or help layer.",
)


# =====================================================================
# Labels
# =====================================================================
def request_type_label(key: str) -> str:
    return (REQUEST_TYPES.get(key) or {}).get("label", "") or (key or "").replace("_", " ").title()


def status_label(key: str) -> str:
    return (REQUEST_STATUSES.get(key) or {}).get("label", "") or (key or "").title()


def post_status_label(key: str) -> str:
    return (POST_STATUSES.get(key) or {}).get("label", "") or (key or "").title()


def idea_tag_label(key: str) -> str:
    return (IDEA_TAGS.get(key) or {}).get("label", "") or (key or "").replace("_", " ").title()


# =====================================================================
# Dates — the overdue and duplicate flags
# =====================================================================
def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def request_window(req: dict) -> tuple[date, date] | None:
    """The days a request is asking to be live on.

    ASAP has no date on it and is deliberately **not** treated as today: a
    request that says "whenever" is not overdue the moment it is submitted,
    and pretending it names today would flag every one of them by tomorrow —
    which is how a flag stops being read.
    """
    mode = str(req.get("requested_date_mode") or "").strip()
    start = _as_date(req.get("requested_date_start"))
    end = _as_date(req.get("requested_date_end"))
    if mode == "asap" or (start is None and end is None):
        return None
    if start is None:
        start = end
    if end is None or end < start:
        end = start
    return (start, end)


def windows_overlap(a: tuple[date, date] | None, b: tuple[date, date] | None,
                    slack_days: int | None = None) -> bool:
    """Do two requested windows land near enough to be worth a second look?"""
    if not a or not b:
        return False
    slack = duplicate_window_days() if slack_days is None else max(0, int(slack_days))
    pad = timedelta(days=slack)
    return (a[0] - pad) <= b[1] and (b[0] - pad) <= a[1]


def is_overdue(req: dict, today: date | None = None) -> bool:
    """The requested date has passed and it is still not scheduled.

    A declined or duplicated request is not overdue — it has been answered,
    and leaving it red is how somebody stops trusting the colour.
    """
    if str(req.get("status") or "") not in OPEN_STATUSES:
        return False
    window = request_window(req)
    if not window:
        return False
    return window[1] < (today or date.today())


def duplicate_flags(requests: list[dict], slack_days: int | None = None) -> dict[str, list[str]]:
    """Which open requests overlap another open request's window.

    Two rules, and both are the difference between a flag people read and one
    they switch off:

      * **Within one client only.** Two clients wanting the same Friday is not
        a coincidence worth reporting; it is a Friday.
      * **Open requests only.** A request already scheduled is not a duplicate
        of anything — it is the thing the other one is a duplicate *of*, and
        flagging both leaves a person deciding between two amber rows where
        one of them is already done.

    A plain date-overlap test on purpose. Fuzzy text matching on a location
    manager's own wording would produce a confident wrong pairing, and the
    whole point is that a human reads both and decides.
    """
    from hub.client_key import normalise_name

    open_rows = [r for r in requests
                 if str(r.get("status") or "new") in OPEN_STATUSES and r.get("id")]
    by_client: dict[str, list[dict]] = {}
    for row in open_rows:
        key = normalise_name(str(row.get("client") or ""))
        if key:
            by_client.setdefault(key, []).append(row)

    out: dict[str, list[str]] = {}
    for rows in by_client.values():
        windows = {r["id"]: request_window(r) for r in rows}
        for i, row in enumerate(rows):
            hits = []
            for other in rows[i + 1:]:
                if windows_overlap(windows[row["id"]], windows[other["id"]], slack_days):
                    hits.append(other["id"])
                    out.setdefault(other["id"], []).append(row["id"])
            if hits:
                out.setdefault(row["id"], []).extend(hits)
    return {k: sorted(set(v)) for k, v in out.items()}


# =====================================================================
# Turnaround — measured, or not promised
# =====================================================================
def turnaround_note(requests: list[dict], *, now: datetime | None = None) -> dict:
    """What the confirmation screen may honestly say about timing.

    Open item 1 in the spec asks whether to commit to a number. The answer
    this codebase keeps arriving at: a figure nobody has measured is a
    commitment made on the client's behalf, so it is measured from the
    requests actually triaged or it is not offered. `measured` is False and
    `line` says so until there is history, rather than a plausible "two
    working days" nobody has checked.
    """
    now = now or datetime.now(timezone.utc)
    hours: list[float] = []
    for row in requests:
        made, done = _stamp(row.get("created_at")), _stamp(row.get("triaged_at"))
        if made and done and done >= made:
            hours.append((done - made).total_seconds() / 3600.0)
    if len(hours) < 3:
        return {"measured": False, "samples": len(hours), "hours": None,
                "line": "We'll pick this up as soon as we've read it. "
                        "(We don't quote a turnaround time yet — there isn't "
                        "enough history behind it to mean anything.)"}
    hours.sort()
    typical = hours[len(hours) // 2]
    if typical < 24:
        span = f"about {max(1, round(typical))} hour" + ("s" if round(typical) != 1 else "")
    else:
        days = max(1, round(typical / 24))
        span = f"about {days} working day" + ("s" if days != 1 else "")
    return {"measured": True, "samples": len(hours), "hours": round(typical, 1),
            "line": f"Requests like this have typically been picked up in "
                    f"{span}. That's what has actually happened, not a promise."}


def _stamp(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# =====================================================================
# Tag weights — the whole of the "learning"
# =====================================================================
def tag_weight(liked: int, passed: int) -> float:
    """liked / (liked + passed + 1).

    Deliberately not a model. It is one line, it is inspectable, a person can
    reproduce it in their head from the two counts printed beside it, and it
    only ever influences the *order and mix* of what is offered — never the
    content of anything, which is the guardrail that makes it safe to be this
    crude. The +1 is what stops a single like reading as a certainty.
    """
    liked = max(0, int(liked or 0))
    passed = max(0, int(passed or 0))
    return round(liked / (liked + passed + 1), 4)


def apply_response(weights: dict, tag: str, response: str) -> dict:
    """Fold one Like/Pass into the stored counts and recompute the weight."""
    if tag not in IDEA_TAGS or response not in ("liked", "passed"):
        return weights
    row = dict(weights.get(tag) or {})
    row["liked_count"] = int(row.get("liked_count") or 0) + (1 if response == "liked" else 0)
    row["passed_count"] = int(row.get("passed_count") or 0) + (1 if response == "passed" else 0)
    row["weight"] = tag_weight(row["liked_count"], row["passed_count"])
    row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = dict(weights)
    out[tag] = row
    return out


def idea_mix(weights: dict | None, *, size: int | None = None,
             wanted: list[str] | None = None,
             explore: float | None = None) -> list[str]:
    """Which tags the next batch is drawn from, strongest first.

    An epsilon-style split rather than bandit arithmetic: most of the batch
    goes to what this client has liked, and a fixed share goes to tags they
    have never answered on at all. Two things it will not do:

      * **Never return an empty list.** A new client has no weights and every
        tag is untried; coming back with nothing from a question that is
        perfectly answerable is the failure `match_quality()` names in
        `hub/voice_casting.py`.
      * **Never drop a tag the client asked for.** `topics_wanted` on the
        preference form is somebody saying what they want in as many words,
        and a weighting that outvotes it is the tool ignoring the one signal
        that was not inferred.
    """
    size = max(1, int(size or batch_size()))
    share = explore_ratio() if explore is None else max(0.0, min(0.6, float(explore)))
    weights = weights if isinstance(weights, dict) else {}
    wanted = [t for t in (wanted or []) if t in IDEA_TAGS]

    tried, untried = [], []
    for tag in IDEA_TAGS:
        row = weights.get(tag) or {}
        seen = int(row.get("liked_count") or 0) + int(row.get("passed_count") or 0)
        (tried if seen else untried).append(tag)

    tried.sort(key=lambda t: (-float((weights.get(t) or {}).get("weight") or 0.0), t))

    explore_slots = min(len(untried), int(round(size * share))) if untried else 0
    if untried and share > 0 and explore_slots == 0:
        explore_slots = 1                       # a share that rounds to nothing is not a share

    out: list[str] = []
    for tag in wanted:                          # asked for beats inferred
        if tag not in out:
            out.append(tag)
    for tag in untried[:explore_slots]:
        if tag not in out:
            out.append(tag)
    for tag in tried + untried:
        if len(out) >= size:
            break
        if tag not in out:
            out.append(tag)
    return out[:size]


# =====================================================================
# What the copy checks are allowed to take as authorized
# =====================================================================
def authorized_text(*sources) -> str:
    """Everything a person supplied, joined for the copy checks to match on.

    This is the load-bearing half of intake. `social_plan.validate_copy()`
    blocks a price, a percentage, a phone number or a deadline that appears
    in copy and in none of the facts a human typed — the check that stops a
    model inventing an offer. A location manager who writes "$50 off through
    Friday" into the request form has supplied exactly that fact, so their own
    words go into the allowed set when the request becomes a post. Without
    this the tool blocks the client's own offer, on the client's own request,
    and reads to a strategist as broken rather than as careful.

    Notes are included for the same reason and asset filenames are not: a
    filename is not somebody saying something.
    """
    parts = []
    for source in sources:
        if not source:
            continue
        if isinstance(source, dict):
            for key in ("copy_suggestion", "notes", "offers", "must_include",
                        "promote", "standing_notes", "topics_wanted"):
                value = source.get(key)
                if isinstance(value, (list, tuple)):
                    parts.extend(str(v) for v in value if str(v).strip())
                elif value:
                    parts.append(str(value))
        elif isinstance(source, (list, tuple)):
            parts.extend(str(v) for v in source if str(v).strip())
        else:
            parts.append(str(source))
    return "\n".join(p.strip() for p in parts if p and p.strip())


def request_facts(req: dict, brief: dict | None = None) -> dict:
    """The facts dict `social_plan.validate_copy()` is run against for a post
    that came from a client request. The request's own words are added to the
    batch's brief rather than replacing it — a standing "never mention" list
    is still a standing list."""
    facts = dict(brief or {})
    supplied = authorized_text(req)
    if supplied:
        facts["notes"] = "\n".join(x for x in (facts.get("notes") or "", supplied) if x)
    return facts


# =====================================================================
# Self-check — the same shape as blog_spec.check_spec()
# =====================================================================
def check_spec() -> list[str]:
    """Findings about this file itself, surfaced on /diagnostics.

    Every one of these is a way the module goes quietly wrong rather than
    loudly: a flow step with no status behind it renders a column that can
    never fill, and a tag with no prompt reaches the model as a bare word.
    """
    out: list[str] = []
    for step in REQUEST_FLOW:
        if step not in REQUEST_STATUSES:
            out.append(f"REQUEST_FLOW names {step!r}, which REQUEST_STATUSES "
                       "does not describe — the queue would draw a column "
                       "nothing can ever be in.")
    for key, row in REQUEST_TYPES.items():
        if not row.get("label") or not row.get("help"):
            out.append(f"Request type {key!r} has no label or no help text; "
                       "the intake form would draw a blank option.")
    for key, row in IDEA_TAGS.items():
        if not row.get("prompt"):
            out.append(f"Idea tag {key!r} carries no prompt, so the model "
                       "would be handed the bare key as an instruction.")
    for status in NEVER_ON_FAILURE:
        if status not in POST_STATUSES:
            out.append(f"NEVER_ON_FAILURE names {status!r}, which is not a "
                       "post status — the push guard would compare against "
                       "a value nothing can hold.")
    # The copy checks are imported rather than restated. If that import ever
    # becomes a local copy this says so, because two sets of patterns is how
    # a fix lands in one of them.
    for pattern in (MONEY_RE, PHONE_RE, DEADLINE_RE, SUPERLATIVE_RE,
                    PLACEHOLDER_RE):
        if not hasattr(pattern, "search"):
            out.append("A copy-check pattern is no longer a compiled regex; "
                       "hub/social_plan.py is what owns those.")
    if not BANNED_PHRASES:
        out.append("BANNED_PHRASES came through empty — the Smart 1 Labs rule "
                   "is enforced rather than requested, and an empty list "
                   "enforces nothing.")
    if not callable(validate_copy):
        out.append("validate_copy is not callable; the intake path would "
                   "accept copy nothing had checked.")
    return out
