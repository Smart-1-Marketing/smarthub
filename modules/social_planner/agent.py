"""The internal optimization agent — staff-facing, and it never talks to a client.

`ideas.py` asks the client what they want more of. This reads what the Hub
already knows and tells a strategist what is off: a platform the client has
that nothing is planned for, a location whose requests have dried up, another
flooding the queue faster than anybody can promise a turnaround for, and what
the numbers say — or, far more often on this deployment, that there are no
numbers and why.

## Every finding carries its evidence, and none of them is a product name

`hub/website_audit.py` settles this: "no retargeting pixel of any kind is on
the site" survives being read out; "they need our retargeting product" is what
a rep gets argued with over. Same here. A finding says what was measured,
where it was measured, and what it costs — the recommendation is a
consequence a person draws, and the note says which of our screens to act on
it from rather than which of our tools sold it.

## Absent is never zero

Four of the five inputs can be unavailable on any given day and each has its
own kind of nothing. A client with no site scan, a Suite that will not answer
and a client nobody has ever sent a request for are three different states,
and rendering all three as an empty findings list is the failure this file is
supposed to catch rather than commit. `signals()` therefore returns a state
per input, and `notes()` produces a finding only where something was actually
measured.

## Nothing here writes, and nothing here publishes

It reads and reports. The idea contribution it computes is a list of *tags*
handed to `ideas.generate()`, which a client still swipes on and a strategist
still promotes. There is no path from this file to a post going out.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from hub import social_content, social_plan

QUIET_DAYS = 60          # a location that has sent nothing for this long
BUSY_MULTIPLE = 3.0      # ...or is sending this many times the client's average


def _not_measured(detail: str) -> dict:
    return {"state": "not_measured", "detail": detail}


# ------------------------------------------------------------------ inputs
def _scan_signal(client: str, url: str) -> dict:
    """Which social platforms the client's own website shows.

    Read through the shared engine rather than by importing the mounted
    module's request-scoped helpers — the `flask.g` trap that made the Google
    sweep report an empty book from a background thread.
    """
    try:
        from modules.scans.app import latest_payload_for_domain
        from modules.scans.reports import social_profiles
    except Exception as exc:                              # noqa: BLE001
        return _not_measured(f"The site scans could not be read "
                             f"({type(exc).__name__}).")
    from hub.client_context import canonical_domain
    domain = canonical_domain(url) if url else ""
    if not domain:
        return _not_measured("This client has no website on file, so there is "
                             "nothing to read their profiles off.")
    try:
        payload = latest_payload_for_domain(domain)
    except Exception as exc:                              # noqa: BLE001
        return _not_measured(f"The site scans could not be read "
                             f"({type(exc).__name__}).")
    if not payload:
        return {"state": "none", "profiles": {},
                "detail": "No site audit on file for this domain yet."}
    try:
        profiles = social_profiles(payload) or {}
    except Exception as exc:                              # noqa: BLE001
        return _not_measured(f"The audit would not parse ({type(exc).__name__}).")
    return {"state": "ok", "profiles": profiles,
            "detail": f"{len(profiles)} profile(s) found on their own site."}


def _request_signal(client: str, url: str) -> dict:
    from . import intake
    try:
        rows = intake.for_client(client, url)
    except Exception as exc:                              # noqa: BLE001
        return _not_measured(f"The request queue could not be read "
                             f"({type(exc).__name__}).")
    if not rows:
        return {"state": "none", "rows": [], "by_location": [],
                "detail": "Nobody at this client has sent a request yet."}
    return {"state": "ok", "rows": rows,
            "by_location": intake.summary(client, url)["by_location"],
            "detail": f"{len(rows)} request(s) on file."}


def _performance_signal(client: str, url: str) -> dict:
    """What Suite reports. On this deployment: nothing, and it says why.

    A rollup built on invented numbers is worse than no rollup, so this
    returns the reason rather than a zero — `SocialPerformanceSnapshot` only
    ever holds what the API actually returned, and there is nothing to hold
    until the read-back scope is granted.
    """
    from . import suite_client
    try:
        result = suite_client.performance(client, url, limit=100)
    except Exception as exc:                              # noqa: BLE001
        return _not_measured(f"Smart 1 Suite could not be asked "
                             f"({type(exc).__name__}).")
    if not result.get("measured"):
        return _not_measured(result.get("error") or
                             "Smart 1 Suite did not report any performance.")
    return {"state": "ok", "rows": result.get("rows") or [],
            "detail": f"{len(result.get('rows') or [])} post(s) read back."}


def _plan_signal(client: str, url: str) -> dict:
    """The channels this client's most recent plan is actually built for."""
    try:
        from . import app as module_app
        rows = [r for r in module_app._read_index()          # noqa: SLF001
                if str(r.get("client") or "").strip().lower()
                == str(client or "").strip().lower()]
    except Exception as exc:                              # noqa: BLE001
        return _not_measured(f"The plan index could not be read "
                             f"({type(exc).__name__}).")
    if not rows:
        return {"state": "none", "channels": [], "latest": None,
                "detail": "No month has been planned for this client yet."}
    latest = rows[0]
    return {"state": "ok", "channels": list(latest.get("channels") or []),
            "latest": latest,
            "detail": f"Latest plan: {latest.get('month') or 'undated'}."}


def signals(client: str, url: str = "") -> dict:
    return {"scan": _scan_signal(client, url),
            "requests": _request_signal(client, url),
            "performance": _performance_signal(client, url),
            "plan": _plan_signal(client, url)}


# ------------------------------------------------------------------ findings
# The Insites profile keys, mapped onto the planner's own channel keys. A
# platform found on their site that maps to no channel we can plan for is
# named rather than dropped — "they have a YouTube and we do not plan video
# here" is a real answer and an empty list is not.
PROFILE_CHANNELS = {"facebook": "facebook", "instagram": "instagram",
                    "linkedin": "linkedin", "tiktok": "tiktok",
                    "pinterest": "pinterest", "twitter": "x", "x": "x"}


def notes(client: str, url: str = "", data: dict | None = None) -> list[dict]:
    """Plain-language findings, most actionable first.

    `level` is `warn` or `note`, never `alert`: a page of red is a page people
    scroll past, which is the note `hub/templates/diagnostics.html` already
    carries about a resolved finding in an open finding's colour.
    """
    data = data or signals(client, url)
    out: list[dict] = []

    scan, plan = data["scan"], data["plan"]
    if scan["state"] == "ok" and plan["state"] == "ok":
        planned = set(plan["channels"])
        have = {PROFILE_CHANNELS.get(k) for k in (scan.get("profiles") or {})}
        unplanned = sorted(c for c in have if c and c not in planned)
        if unplanned:
            labels = ", ".join(social_plan.channel_label(c) for c in unplanned)
            out.append({"level": "warn", "key": "stale_platform",
                        "title": f"{labels} is on their website and on no plan",
                        "detail": "Their own site links to it, so customers "
                                  "find it and it has not been posted to from "
                                  "here. Add the channel on the next month's "
                                  "grid, or say out loud that it is not ours "
                                  "to run.",
                        "where": "The channel row on a new plan."})
        no_profile = sorted(c for c in planned
                            if c not in have and c in PROFILE_CHANNELS.values())
        if no_profile:
            labels = ", ".join(social_plan.channel_label(c) for c in no_profile)
            out.append({"level": "note", "key": "unseen_platform",
                        "title": f"{labels} is planned and their site does not link to it",
                        "detail": "The audit reads their own pages, so this is "
                                  "either a profile they have not linked or "
                                  "one that does not exist. Worth confirming "
                                  "before a month is built for it.",
                        "where": "Their site audit."})

    requests = data["requests"]
    if requests["state"] == "ok":
        rows = requests["rows"]
        today = date.today()
        overdue = [r for r in rows if r.get("overdue")]
        if overdue:
            out.append({"level": "warn", "key": "overdue_requests",
                        "title": f"{len(overdue)} request(s) are past the date "
                                 "they were asked for",
                        "detail": "Each one names the location and the person "
                                  "who sent it. A request whose day has gone "
                                  "is the one that costs us something.",
                        "where": "The requests queue for this client."})
        dupes = [r for r in rows if r.get("possible_duplicate_of")]
        if dupes:
            out.append({"level": "note", "key": "possible_duplicates",
                        "title": f"{len(dupes)} request(s) overlap another "
                                 "location's week",
                        "detail": "Flagged on the dates alone. It is as often "
                                  "two real asks as one ask twice, so nothing "
                                  "has been merged — somebody reads both.",
                        "where": "The requests queue."})
        out.extend(_location_notes(rows, today))

    if data["performance"]["state"] != "ok":
        out.append({"level": "note", "key": "performance_not_measured",
                    "title": "What worked is not measured for this client",
                    "detail": data["performance"].get("detail", ""),
                    "where": "Smart 1 Suite's connection, on the Suite panel."})
    return out


def _location_notes(rows: list[dict], today: date) -> list[dict]:
    """A shop that has gone quiet, and one flooding the queue.

    Both are worth knowing and only one of them is a problem. The quiet one is
    a nudge — somebody has stopped sending photographs and nobody noticed. The
    busy one is the reason a turnaround promise made to the client as a whole
    is about to be broken for everybody else.
    """
    per: dict[str, dict] = {}
    for row in rows:
        label = row.get("location_label") or "Not said"
        item = per.setdefault(label, {"label": label, "count": 0, "last": ""})
        item["count"] += 1
        stamp = str(row.get("created_at") or "")[:10]
        if stamp > item["last"]:
            item["last"] = stamp
    if len(per) < 2:
        # One location is not a distribution. Reporting it as quiet or busy
        # against an average of itself is arithmetic with no content.
        return []
    values = [i["count"] for i in per.values()]
    average = sum(values) / len(values)
    out = []
    for item in sorted(per.values(), key=lambda r: -r["count"]):
        if item["count"] >= average * BUSY_MULTIPLE and item["count"] >= 3:
            out.append({"level": "note", "key": "busy_location",
                        "title": f"{item['label']} sends most of this client's requests",
                        "detail": f"{item['count']} of "
                                  f"{sum(values)}. Worth knowing before a "
                                  "turnaround time is promised to the account.",
                        "where": "The requests queue, filtered to that location."})
        last = item["last"]
        if last:
            try:
                gone = (today - date.fromisoformat(last)).days
            except ValueError:
                gone = 0
            if gone >= QUIET_DAYS:
                out.append({"level": "note", "key": "quiet_location",
                            "title": f"{item['label']} has sent nothing for "
                                     f"{gone} days",
                            "detail": "They have sent things before, so the "
                                      "link works and somebody has stopped "
                                      "using it. Usually a person changed.",
                            "where": "The location's contact, on the "
                                     "locations list."})
    return out


# ------------------------------------------------------------------ what worked
def what_worked(client: str, url: str = "") -> dict:
    """The monthly note, grouped by idea tag.

    Two sources, kept apart because they answer different questions and only
    one of them is measured: what the *client* said they liked (their swipes,
    which is an opinion and is ours to read), and what actually performed
    (Suite's numbers, which are a measurement and are usually absent). A note
    that merged them would let a client's preference read as a result.
    """
    from . import ideas
    table = ideas.weight_table(client, url)
    answered = [r for r in table if r["answered"]]
    performance = _performance_signal(client, url)
    return {
        "preference": {
            "measured": bool(answered),
            "rows": answered[:5],
            "line": ("Most liked: " +
                     ", ".join(f"{r['label']} ({r['liked']} of {r['answered']})"
                               for r in answered[:3]))
            if answered else "Nobody at this client has swiped on an idea yet, "
                             "so there is no stated preference to report.",
        },
        "performance": {
            "measured": performance["state"] == "ok",
            "detail": performance.get("detail", ""),
            "rows": performance.get("rows", []),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def idea_tags_for(client: str, url: str = "") -> list[str]:
    """This agent's contribution to the next idea batch.

    It is a list of tags and nothing else — the agent never writes a title.
    Where a platform is going unposted, the tags that suit an introduction to
    it lead; otherwise it is the client's own weighting, which is what
    `social_content.idea_mix()` already computes.
    """
    from . import ideas
    data = signals(client, url)
    extra: list[str] = []
    for note in notes(client, url, data):
        if note["key"] == "stale_platform":
            extra += ["announcement", "behind_the_scenes"]
        if note["key"] == "overdue_requests":
            # A backlog does not need more ideas on top of it.
            return []
    prefs = ideas.preferences(client, url)
    return social_content.idea_mix(prefs.get("weights"),
                                   wanted=list(prefs.get("topics_wanted") or []) + extra)
