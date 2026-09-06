"""The Social Planner Post API, and the four things that must be true first.

Smart 1 Suite already owns scheduling and publishing — multi-platform posting,
best-time suggestions, recurring queues, RSS-to-post. This Hub does not
rebuild any of it. This file is the pipe: it maps one slot from a plan into
the shape Suite expects and hands it over.

## Why the CSV is still the default path

Pushing needs `socialplanner/post.write` on the marketplace app, and
**requested is not granted**. HighLevel grants the scopes it recognises at
consent and says nothing about the rest, so a Suite connection that reports
Connected can be missing this scope entirely and every push 401s later looking
exactly like a bad token. `hub/suite_accounts.publishing()` asks that question
*before* a push rather than discovering it with one, and the answer is
tri-state: granted, not granted, or not measured because HighLevel omitted the
scope list — which is not evidence the scope is missing and is not permission
either.

Until it is granted, the whole drafting pipeline still earns its keep through
`social_plan.planner_csv()` under Suite's Bulk Upload, and every screen says
which of the two routes it is offering rather than showing a push button that
fails at the moment somebody is waiting on it.

## The endpoints are transcribed, and overridable

They are written down here from the build spec rather than fetched, the rule
`hub/creative_specs.py` works to: a table pulled live changes what the code
does with no diff to point at. They have also never been exercised against
the live API, because nothing has been able to — so the collection path is
one environment variable (`SOCIAL_SUITE_POST_PATH`) rather than an edit, the
same arrangement `hub/knack_api.py` gives its pinned field ids, and a
corrected path is a setting rather than a deploy.

## Four rules on a push

**Ask, then push.** The scope gate and the client's own sub-account are both
checked first, and each refusal names what is missing and where it is fixed.

**A failed push leaves the post approved.** Never `scheduled`, never
`published`. A client-approved post that quietly reads as scheduled is gone,
and the queue says it is handled — the one outcome the whole approval flow
exists to prevent. The error is kept on the slot and the retry is a button in
the staff queue, never automatic: a flaky response is exactly the case where
the write may well have landed, and an automatic retry there is a double post
on somebody's page.

**Nothing here raises.** Every call returns `{ok, ...}` with a sentence for a
screen; the caller decides what the post's status becomes.

**No token value ever reaches a result.** These are rendered into pages and
pasted into chats — `services/provider_check.py`'s rule.
"""
from __future__ import annotations

import os

from hub import social_content
from hub.suite_accounts import SCOPE_PUBLISH, SCOPE_READ, publishing, token_for

# Suite's API host, shared with the rest of the Hub's HighLevel calls so a
# staging host is one variable for all of them.
API_BASE = (os.environ.get("GHL_API_BASE")
            or "https://services.leadconnectorhq.com").rstrip("/")
API_VERSION = os.environ.get("GHL_API_VERSION", "2021-07-28")

# Transcribed from the build spec. `{location}` is filled with the client's
# sub-account id; a deployment whose Suite publishes these under a different
# path sets SOCIAL_SUITE_POST_PATH rather than waiting for a code change.
POSTS_PATH = os.environ.get("SOCIAL_SUITE_POST_PATH") or "/social-planner/posts"

TIMEOUT = 25

# What Suite is asked to post to, per channel. The planner's channel keys are
# the Hub's own; these are the platform names the API takes. One table, so a
# channel added to hub/social_plan.py that has no mapping here is *named*
# rather than silently dropped from the push — the failure `registry
# .acceptPlatforms()` was written about in the ad builder, where one filter
# line turned a Meta buy into a set of Google banners with nothing saying so.
PLATFORMS: dict[str, str] = {
    "facebook": "facebook",
    "instagram": "instagram",
    "google_business": "google",
    "linkedin": "linkedin",
    "tiktok": "tiktok",
    "x": "twitter",
    "pinterest": "pinterest",
}


def _fail(detail: str, **extra) -> dict:
    return dict({"ok": False, "error": detail, "ghl_post_id": ""}, **extra)


def platforms_for(channels) -> tuple[list[str], list[str]]:
    """(what Suite will be asked for, what has no mapping here).

    The second half is returned rather than dropped: a plan built for five
    channels and pushed to four is a client whose Instagram simply never got
    anything, and the count on screen would still say five.
    """
    wanted, unmapped = [], []
    for channel in (channels or []):
        name = PLATFORMS.get(str(channel))
        if name and name not in wanted:
            wanted.append(name)
        elif not name:
            unmapped.append(str(channel))
    return wanted, unmapped


def post_payload(batch: dict, slot: dict, location_id: str) -> dict:
    """One slot in the shape Suite expects.

    Platform-specific fields are passed through as-is rather than
    reinterpreted — this Hub decides what a post *says*, and Suite decides
    what each network does with it.
    """
    from hub import social_plan
    platforms, _ = platforms_for(slot.get("channels"))
    media = [u for u in [slot.get("image_url")] if u]
    when = f"{slot.get('date', '')}T{slot.get('time', '09:15')}:00"
    return {
        "locationId": location_id,
        "type": "post",
        "accountIds": [],                 # Suite resolves the connected pages
        "platforms": platforms,
        "summary": social_plan.post_text(slot),
        "media": [{"url": u} for u in media],
        "scheduleDate": when,
        "followUpComment": "",
        "tags": ["smart1-hub", batch.get("month", "")],
        "source": "smart1-hub",
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": API_VERSION,
            "Accept": "application/json", "Content-Type": "application/json"}


def preflight(client: str, url: str = "") -> dict:
    """Everything that has to be true before a push, asked once.

    A screen calls this to decide what to *offer*, so it never reaches Suite
    with a post attached and never bills anybody a failed attempt to find out
    whether a button should have been drawn.
    """
    scope = publishing()
    account = token_for(client, url)
    ready = bool(scope.get("ready")) and account["state"] == "connected"
    if ready:
        detail = "Approved posts can be pushed straight into this client's " \
                 "Social Planner."
    elif not scope.get("ready"):
        detail = scope.get("detail", "")
    else:
        detail = account.get("detail", "")
    return {"ready": ready, "scope": scope, "account_state": account["state"],
            "location_id": account.get("location_id", ""),
            # Never the token, and never anything derived from it.
            "detail": detail,
            "fallback": "The CSV export loads into Social Planner's Bulk "
                        "Upload meanwhile — the plan is the same either way."}


def push(batch: dict, slot: dict, client: str, url: str = "") -> dict:
    """Send one approved post. Never raises; never claims more than it knows."""
    scope = publishing()
    if not scope.get("ready"):
        return _fail(scope.get("detail", "Pushing is not available yet."),
                     blocked_by="scope")
    account = token_for(client, url)
    if account["state"] != "connected" or not account.get("token"):
        return _fail(account.get("detail", "This client has no Suite "
                                           "sub-account on file."),
                     blocked_by="account")

    platforms, unmapped = platforms_for(slot.get("channels"))
    if not platforms:
        return _fail("None of this post's channels maps to a network Suite "
                     "publishes to" +
                     (f" ({', '.join(unmapped)})." if unmapped else "."),
                     blocked_by="channels")

    payload = post_payload(batch, slot, account["location_id"])
    try:
        import requests
        response = requests.post(f"{API_BASE}{POSTS_PATH}",
                                 json=payload, headers=_headers(account["token"]),
                                 timeout=TIMEOUT)
    except Exception as exc:                              # noqa: BLE001
        return _fail(f"Smart 1 Suite could not be reached "
                     f"({type(exc).__name__}). The post is still approved and "
                     "nothing was scheduled — retry it from the queue.",
                     blocked_by="network", unmapped=unmapped)

    if response.status_code in (401, 403):
        # Never echoes the body: HighLevel errors have included token
        # fragments, and this reaches a screen. A granted-but-wrong-scope
        # token and a bad one both land here looking identical, which is why
        # the message names the scope rather than guessing at the cause.
        return _fail(f"Smart 1 Suite rejected the request "
                     f"({response.status_code}). The token is missing "
                     f"{SCOPE_PUBLISH}, or it was revoked after being granted.",
                     blocked_by="suite", status=response.status_code,
                     unmapped=unmapped)
    try:
        data = response.json() if response.text else {}
    except ValueError:
        data = {}
    if not response.ok:
        message = data.get("message") or data.get("error") or f"HTTP {response.status_code}"
        if isinstance(message, list):
            message = ", ".join(str(m) for m in message)
        return _fail(f"Smart 1 Suite refused the post: {message}. It is still "
                     "approved — nothing was scheduled.",
                     blocked_by="suite", status=response.status_code,
                     unmapped=unmapped)

    post_id = str((data.get("post") or data).get("id")
                  or (data.get("post") or data).get("_id") or "")
    if not post_id:
        # A 200 with nothing identifying the post is not a success we can act
        # on: without an id nothing can ever read the status back or edit it,
        # and calling it scheduled would leave a post nobody can find.
        return _fail("Smart 1 Suite accepted the post but returned no id for "
                     "it, so it cannot be tracked or edited from here. Check "
                     "Social Planner before retrying — a retry would post "
                     "twice.", blocked_by="no_id", unmapped=unmapped)
    return {"ok": True, "ghl_post_id": post_id, "error": "",
            "unmapped": unmapped,
            "platforms": platforms}


def fetch(post_id: str, client: str, url: str = "") -> dict:
    """Read one post back — what Suite says happened to it.

    This is what turns `pushed` into `scheduled` or `published`. It is the
    *status route's* job to write that back, not the browser's, for the reason
    the Commercial Builder's HeyGen clip gives: a job whose only observer was
    a tab somebody closed is a job whose result is lost.
    """
    account = token_for(client, url)
    if account["state"] != "connected" or not account.get("token"):
        return _fail(account.get("detail", ""), blocked_by="account")
    try:
        import requests
        response = requests.get(f"{API_BASE}{POSTS_PATH}/{post_id}",
                                headers=_headers(account["token"]), timeout=TIMEOUT)
        data = response.json() if response.text else {}
    except Exception as exc:                              # noqa: BLE001
        return _fail(f"Smart 1 Suite could not be reached ({type(exc).__name__}).")
    if response.status_code in (401, 403):
        return _fail(f"Smart 1 Suite rejected the request "
                     f"({response.status_code}). The token is missing "
                     f"{SCOPE_READ}, or it was revoked after being granted.")
    if not response.ok:
        return _fail(f"Smart 1 Suite would not answer for that post "
                     f"(HTTP {response.status_code}).")
    body = data.get("post") if isinstance(data.get("post"), dict) else data
    state = str(body.get("status") or "").lower()
    known = {"published": "published", "posted": "published",
             "scheduled": "scheduled", "draft": "pushed",
             "error": "failed", "failed": "failed"}
    return {"ok": True, "status": known.get(state, "pushed"),
            "suite_status": state, "post": body}


def performance(client: str, url: str = "", *, limit: int = 100) -> dict:
    """What Suite reports about this client's posts.

    Returns `(rows, error)` in dict form rather than a bare list, because
    *this client has posted nothing* and *we could not read the numbers* are
    different answers and only the first one means there is nothing to report.
    Nothing here computes a figure Suite did not return: a reach number this
    Hub invented would sit on a client report looking exactly like a measured
    one.
    """
    account = token_for(client, url)
    if account["state"] != "connected" or not account.get("token"):
        return {"ok": False, "measured": False, "rows": [],
                "error": account.get("detail", "")}
    if not publishing().get("readback"):
        return {"ok": False, "measured": False, "rows": [],
                "error": f"The Suite app has not been consented with "
                         f"{SCOPE_READ}, so what was published cannot be read "
                         "back yet."}
    try:
        import requests
        response = requests.get(
            f"{API_BASE}{POSTS_PATH}",
            params={"locationId": account["location_id"], "limit": int(limit)},
            headers=_headers(account["token"]), timeout=TIMEOUT)
        data = response.json() if response.text else {}
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "measured": False, "rows": [],
                "error": f"Smart 1 Suite could not be reached "
                         f"({type(exc).__name__})."}
    if response.status_code in (401, 403):
        return {"ok": False, "measured": False, "rows": [],
                "error": f"Smart 1 Suite rejected the request "
                         f"({response.status_code}). The token is missing "
                         f"{SCOPE_READ}, or it was revoked after being "
                         "granted."}
    if not response.ok:
        return {"ok": False, "measured": False, "rows": [],
                "error": f"Smart 1 Suite would not answer (HTTP "
                         f"{response.status_code})."}
    rows = data.get("posts") if isinstance(data.get("posts"), list) else []
    return {"ok": True, "measured": True, "rows": rows[:limit], "error": ""}


def apply_push_result(slot: dict, result: dict) -> dict:
    """Write a push's outcome onto the slot, and refuse to overstate it.

    The one rule this enforces rather than requests: a slot may not land on a
    status in `social_content.NEVER_ON_FAILURE` off a failed push. The guard
    is here, in the one place both the push route and the retry go through,
    rather than at each call site — a rule two of three callers keep is not a
    rule.
    """
    if result.get("ok") and result.get("ghl_post_id"):
        slot["ghl_post_id"] = str(result["ghl_post_id"])[:120]
        slot["delivery"] = "pushed"
        slot["delivery_error"] = ""
    else:
        slot["delivery"] = "approved"
        slot["delivery_error"] = str(result.get("error") or "")[:600]
        if slot.get("delivery") in social_content.NEVER_ON_FAILURE:
            slot["delivery"] = "approved"
    return slot
