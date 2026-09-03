"""Which Smart 1 Suite sub-account a client is, and a token scoped to it.

Two modules now need the same answer — the Commercial Builder, filing a QR
scan against whoever owns it, and the Social Content Planner, pushing a post
into that client's Social Planner — so it is written down once here and read,
rather than carried twice. `modules/commercial_builder/client_link.py` keeps
its own function name and delegates, the way `modules/radio_promo/voices.py`
re-exports `hub/voice_casting.py`: every existing caller is unchanged and
there is still only one description of what the mapping is.

## Where the mapping lives, and why it is read rather than duplicated

`PickerClient.ghl_location_id` — the image picker's client table is the one
place in this Hub that records which Suite sub-account a client is. A second
copy of that mapping is a second thing to keep in step, and posting to the
wrong sub-account publishes one client's content on another client's page,
which is the worst outcome any tool here can produce.

That table carries a website column but is populated by hand, so the join is
made **stricter** rather than looser: `hub/client_key.same_client()`, which is
an exact domain or an exact normalised name and nothing else. A business the
name does not match exactly comes back with no location, and the caller is
told that rather than handed somebody else's.

## Three answers, never two

`state` is `connected`, `not_connected` or `not_measured`, for the reason
`connected_accounts_result()` gives in Google Finder: *this client has no
Suite sub-account on file*, *we could not read the mapping at all* and *the
Suite app is not authorized on this deployment* send somebody to three
different places, and exactly one of them means there is nothing to do. A
`bool` here would collapse all three into "not connected" and read as a
setting somebody had forgotten rather than a table that would not answer.

## Nothing in this file may raise

It is called while saving a form. Every failure resolves to `not_measured`
with the exception named, and no token value is ever carried into a state
dict — those are rendered into pages and pasted into chats, the rule
`services/provider_check.py` works to.
"""
from __future__ import annotations

from hub.client_key import same_client

__all__ = ["location_for", "client_for_location", "token_for",
           "publishing", "SCOPE_PUBLISH"]

# The scope a Social Planner write needs. Named here rather than spelled out
# at each call site so the gate and the request list cannot drift.
# Read from the scope table rather than restated, so a name corrected there
# cannot leave this gate testing a string HighLevel has never heard of. It did
# exactly that until 2026-08-30: both constants named social-media-posting.*,
# which is not a HighLevel scope, so publishing() would have answered "not
# granted" for ever -- including after a consent that granted the real scope.
from hub.ghl_scopes import SCOPE_SOCIAL_WRITE as SCOPE_PUBLISH
from hub.ghl_scopes import SCOPE_SOCIAL_READ as SCOPE_READ


def _answer(state: str, detail: str, **extra) -> dict:
    out = {"state": state, "detail": detail, "location_id": "",
           "connected": state == "connected"}
    out.update(extra)
    return out


def location_for(name: str, url: str = "") -> dict:
    """This client's Suite sub-account id, or which kind of nothing it is.

    Two stores, one reader. `hub/suite_map.py` is where a pairing is recorded
    now; `PickerClient.ghl_location_id` is where it used to be, and the rows
    already there are real. Neither is migrated -- reading both costs one
    pass, and a migration to fix a coupling problem is the worse trade. This
    stays the ONLY function that answers the question, so the two stores
    cannot come to disagree about whose sub-account a client's work goes to.
    """
    if not str(name or "").strip():
        return _answer("not_connected", "No client was named.")

    try:
        from . import suite_map
        found = suite_map.recorded_location(name, url)
    except Exception:                                     # noqa: BLE001
        found = {"state": "not_connected", "location_id": ""}
    if str(found.get("location_id") or "").strip():
        return _answer("connected", "", location_id=found["location_id"],
                       matched_name=name, source="suite_map")
    if found.get("state") == "ambiguous":
        # Two sub-accounts for one client is not a thing to pick between.
        return _answer("not_measured", found.get("detail") or
                       "More than one sub-account is recorded for this client.",
                       candidates=found.get("candidates") or [])
    try:
        from modules.image_picker.models import PickerClient
    except Exception as exc:                              # noqa: BLE001
        return _answer("not_measured",
                       "The client-to-sub-account mapping could not be read "
                       f"({type(exc).__name__}). That is not the same as this "
                       "client having no Suite account.")
    try:
        rows = list(PickerClient.query.all())
    except Exception as exc:                              # noqa: BLE001
        # No application context, or the table is not there yet. Both are
        # "we could not look", never "nobody is connected".
        return _answer("not_measured",
                       "The client-to-sub-account mapping could not be read "
                       f"({type(exc).__name__}).")

    matches = []
    for row in rows:
        location = str(getattr(row, "ghl_location_id", "") or "").strip()
        if not location:
            continue
        if same_client(name, url, getattr(row, "name", "") or "",
                       getattr(row, "website", "") or getattr(row, "url", "") or ""):
            matches.append((location, getattr(row, "name", "") or ""))

    if not matches:
        return _answer("not_connected",
                       "No Smart 1 Suite sub-account is recorded for this "
                       "client. It is set on their row in Client Image "
                       "Uploads.")
    unique = {loc for loc, _ in matches}
    if len(unique) > 1:
        # Two rows claiming one client is exactly the case where guessing
        # publishes to the wrong page. Named, never picked between.
        return _answer("not_measured",
                       "Two client rows name different Suite sub-accounts for "
                       "this business, so which one to post to is not a "
                       "question this Hub can answer. Fix the duplicate in "
                       "Client Image Uploads.",
                       candidates=sorted(unique))
    return _answer("connected", "", location_id=matches[0][0],
                   matched_name=matches[0][1])


def client_for_location(location_id: str) -> dict:
    """Which client a Suite sub-account belongs to — `location_for()` backwards.

    `location_for()` answers "which sub-account is this client", which is what
    a tool asks when it already knows the client. This answers the question a
    *client-facing* page asks: somebody is looking at us from inside sub-account
    X, whose data may they see. `hub/suite_sso.py` is the caller, and there the
    answer is the entire security model — so this is deliberately the strictest
    lookup in this file.

    **Exactly one client, or none.** Two rows recording the same sub-account
    comes back `ambiguous` with both named, never picked between: choosing
    would be choosing whose record a stranger is shown. That is the same
    refusal `location_for()` makes in the other direction, for a much worse
    reason.

    An id that matches nothing is `not_connected` — a setup gap, and it says
    so rather than reading as a client with an empty record.
    """
    location_id = str(location_id or "").strip()
    if not location_id:
        return _answer("not_connected", "No sub-account was named.")

    # hub/suite_map.py first, the picker's own column second -- see
    # location_for() above for why both are read and neither is migrated.
    try:
        from . import suite_map
        found = suite_map.recorded_client(location_id)
    except Exception:                                     # noqa: BLE001
        found = {"state": "not_connected", "client": ""}
    if str(found.get("client") or "").strip():
        return _answer("connected", "", location_id=location_id,
                       client=found["client"], client_url="",
                       source="suite_map")
    if found.get("state") == "ambiguous":
        return _answer("ambiguous", found.get("detail") or
                       "More than one client records this sub-account.",
                       location_id=location_id,
                       candidates=found.get("candidates") or [])
    try:
        from modules.image_picker.models import PickerClient
    except Exception as exc:                              # noqa: BLE001
        return _answer("not_measured",
                       "The client-to-sub-account mapping could not be read "
                       f"({type(exc).__name__}).")
    try:
        rows = list(PickerClient.query.all())
    except Exception as exc:                              # noqa: BLE001
        return _answer("not_measured",
                       "The client-to-sub-account mapping could not be read "
                       f"({type(exc).__name__}).")

    matches = []
    for row in rows:
        recorded = str(getattr(row, "ghl_location_id", "") or "").strip()
        # An exact string match and nothing else. A sub-account id is an
        # opaque identifier, so there is no near-miss worth entertaining and a
        # prefix match here would be a way to reach another client's data.
        if recorded and recorded == location_id:
            matches.append(row)

    if not matches:
        return _answer("not_connected",
                       "No client on file records this Smart 1 Suite "
                       "sub-account. It is set on their row in Client Image "
                       "Uploads.", location_id=location_id)
    names = {str(getattr(r, "name", "") or "").strip() for r in matches}
    if len({n.lower() for n in names if n}) > 1:
        return _answer("ambiguous",
                       "More than one client records this sub-account, so "
                       "whose data this page may show is not a question this "
                       "Hub can answer. Fix the duplicate in Client Image "
                       "Uploads.",
                       location_id=location_id, candidates=sorted(names))
    row = matches[0]
    return _answer("connected", "", location_id=location_id,
                   client=str(getattr(row, "name", "") or "").strip(),
                   client_url=str(getattr(row, "website", "")
                                  or getattr(row, "url", "") or "").strip())


def token_for(name: str, url: str = "") -> dict:
    """A token scoped to this client's sub-account, minted on demand.

    `None` for the token whenever it cannot be had, which is what the module
    contract asks for: a missing token degrades that client's card to "not
    connected" and never 500s. The token itself is on the answer for the
    caller to use and is deliberately absent from `detail`, which reaches a
    page.
    """
    found = location_for(name, url)
    if found["state"] != "connected":
        return dict(found, token=None)
    try:
        from hub import ghl_oauth
    except Exception as exc:                              # noqa: BLE001
        return _answer("not_measured",
                       f"The Suite connection could not be read ({type(exc).__name__}).",
                       token=None, location_id=found["location_id"])
    try:
        token = ghl_oauth.location_token(found["location_id"])
    except Exception as exc:                              # noqa: BLE001
        # A sub-account the marketplace app was never installed on is the
        # ordinary case here and HighLevel says so in the message; it is a
        # setting rather than a fault, so it is not a "not_measured".
        return _answer("not_connected",
                       f"Smart 1 Suite would not issue a token for this "
                       f"client's sub-account ({type(exc).__name__}). Usually "
                       "that means the app is not installed on it.",
                       token=None, location_id=found["location_id"])
    if not token:
        return _answer("not_connected",
                       "Smart 1 Suite returned no token for this client's "
                       "sub-account.", token=None,
                       location_id=found["location_id"])
    return _answer("connected", "", token=token,
                   location_id=found["location_id"],
                   matched_name=found.get("matched_name", ""))


def publishing() -> dict:
    """May anything actually be pushed to Social Planner yet?

    A granted scope list is not the scope list we asked for — `hub/ghl_scopes.py`
    says so at length. HighLevel grants what it recognises at consent and says
    nothing about the rest, so a token that reports Connected can be missing
    `socialplanner/post.write` entirely and every push 401s months later
    looking exactly like a bad token.

    So this is asked *before* a push rather than discovered by one, and it is
    tri-state: granted, not granted, or **not measured** where HighLevel
    omitted the scope field altogether. Not measured is deliberately not
    treated as a refusal — a missing field is not evidence that nothing was
    granted — but it is not treated as permission either, so the caller says
    which it is and a person decides.
    """
    try:
        from hub import ghl_oauth, ghl_scopes
    except Exception as exc:                              # noqa: BLE001
        return {"ready": False, "known": False, "scope": SCOPE_PUBLISH,
                "detail": f"The Suite connection could not be read "
                          f"({type(exc).__name__})."}
    try:
        state = ghl_oauth.status()
    except Exception as exc:                              # noqa: BLE001
        return {"ready": False, "known": False, "scope": SCOPE_PUBLISH,
                "detail": f"The Suite connection could not be read "
                          f"({type(exc).__name__})."}
    if not state.get("connected"):
        return {"ready": False, "known": True, "scope": SCOPE_PUBLISH,
                "detail": state.get("detail") or
                          "Smart 1 Suite is not authorized on this deployment."}
    scopes = state.get("scopes") or ghl_scopes.compare(state.get("scope", ""))
    if not scopes.get("known"):
        return {"ready": False, "known": False, "scope": SCOPE_PUBLISH,
                "detail": "Smart 1 Suite did not report which scopes it "
                          "granted, so whether posts can be pushed is not "
                          "measured. That is not evidence the scope is "
                          "missing — export the CSV meanwhile."}
    granted = set(scopes.get("granted") or [])
    if SCOPE_PUBLISH not in granted:
        return {"ready": False, "known": True, "scope": SCOPE_PUBLISH,
                "detail": "The Suite app has not been consented with "
                          f"{SCOPE_PUBLISH}, so posts cannot be pushed yet. "
                          "It is on the requested list; the agency owner "
                          "re-consenting once is what grants it. The CSV "
                          "export works regardless."}
    return {"ready": True, "known": True, "scope": SCOPE_PUBLISH,
            "readback": SCOPE_READ in granted,
            "detail": "Posts can be pushed to Social Planner."}
