"""GA and GTM identifiers, from Knack AND Google — and whether they agree.

Two sources exist and neither is sufficient alone:

  * **Knack (object_153)** records what we believe the client's GA and GTM
    accounts to be. It's there whether or not anyone has connected the Google
    account, which is why the Hub can show `GTM-TG6FPR8M` for Icon Solar even
    though the live lookup finds nothing.
  * **Google** reports what we can actually reach right now. It's authoritative
    about access — if it isn't here, we cannot administer it — but it only
    covers accounts somebody has connected.

An earlier version treated these as alternatives, preferring whichever
answered first. That threw away the most useful fact of all: **when the two
disagree.** A recorded container that doesn't match the one Google shows means
either the site is running the wrong container or the record is stale, and
both are worth knowing. So nothing is discarded; the comparison is the output.

Six states, each meaning something different operationally:

    match           recorded and reachable, same id     nothing to do
    mismatch        both known, genuinely different     investigate
    not_comparable  both known, different ID SPACES     nothing to act on
    recorded_only   in Knack, no Google access          request access
    live_only       reachable, not recorded in Knack    update Knack
    missing         neither                             set one up

`not_comparable` is the one that had to be added, and its absence was the
expensive kind of bug. GA has two identifiers for one property -- the
measurement id `G-XXXXXXX` that goes on the site and into Knack, and the
numeric property id that is all a GA4 property summary returns -- so the
comparison forced a verdict between two things that were never comparable and
answered **mismatch** every time. On this deployment's own registry that is
all 166 recorded GA ids; `match` was unreachable for GA entirely. The red pill
on the client record and the "reporting may be pointed at the wrong property"
report were both about correctly-recorded properties we administer.

Which is what the note under `_norm_ga` had already warned about in the
abstract: a false mismatch "is worse than no check at all because it trains
people to ignore the warning."
"""
from __future__ import annotations

import re


def _norm_ga(v: str) -> str:
    """Normalise a GA identifier for comparison.

    GA ids appear as `G-ABC123`, `properties/123456`, `UA-123456-1` and bare
    numeric property ids depending on where they came from. Comparing raw
    strings reports false mismatches, which is worse than no check at all
    because it trains people to ignore the warning.
    """
    s = str(v or "").strip().upper()
    if not s:
        return ""
    s = s.replace("PROPERTIES/", "").replace("ACCOUNTS/", "")
    m = re.search(r"\b(G-[A-Z0-9]+)\b", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(UA-\d+-\d+)\b", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6,})\b", s)          # bare property id
    return m.group(1) if m else s


def _norm_gtm(v: str) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"\b(GTM-[A-Z0-9]+)\b", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6,})\b", s)          # container/account number
    return m.group(1) if m else s


def _ga_space(v: str) -> str:
    """Which GA identifier space a value is in, or "" if it is in none.

    GA has two identifiers for one property and they are not interchangeable:

      * the **measurement id**, `G-XXXXXXX` -- the one on the site, in the GTM
        tag and on every report, and therefore the one a person types into
        Knack. On this deployment's own 610-row website registry, all 166
        recorded GA ids are one of these (159) or a legacy `UA-` id (7). Not
        one is a property id.
      * the **property id**, a bare number -- which is what Google returns,
        because a GA4 property summary carries no measurement id at all.
        `modules/google_finder/app.py` splits it out of `properties/<id>` and
        the comment beside it says as much.

    So `_state()` compared `G-ABC123XYZ` against `284729103`, found them
    different, and answered **mismatch** -- for every GA row where both sides
    were known, which is the ordinary, correct case. `match` was unreachable.
    Client 360 drew a red pill and the advice "the site is running a container
    we don't administer, or the Knack record is out of date. Worth resolving
    -- reports built on the wrong property are silently wrong", about a
    property we administer perfectly well; `audit_all()` collected every one
    of them into a report whose premise is that each entry means somebody's
    reporting may be pointed at the wrong place.

    This module's own docstring already says why that is worse than nothing:
    a false mismatch "trains people to ignore the warning". GTM never had it,
    because google_finder deliberately stores the public `GTM-` id *because
    that is what Knack records* -- the GA half was written to a different
    standard and nothing compared the two.
    """
    s = str(v or "").strip().upper()
    if not s:
        return ""
    if re.search(r"\bG-[A-Z0-9]+\b", s):
        return "measurement"
    if re.search(r"\bUA-\d+-\d+\b", s):
        return "ua"
    if re.search(r"\b\d{6,}\b", s):
        return "property"
    return "other"


def _gtm_space(v: str) -> str:
    """Which GTM identifier space a value is in.

    The same two-space problem, one platform over and quieter. google_finder
    stores `public_id or container_id` -- the public `GTM-XXXX` id where the
    API returns one, *because that is what Knack records*, and the numeric
    container id where it does not. So the fallback lands in the other space
    and produces the identical false mismatch; it is rarer than the GA one
    only because publicId is usually present, which is a reason to expect it
    rather than a reason to leave it.
    """
    s = str(v or "").strip().upper()
    if not s:
        return ""
    if re.search(r"\bGTM-[A-Z0-9]+\b", s):
        return "public"
    if re.search(r"\b\d{6,}\b", s):
        return "numeric"
    return "other"


# Per platform, the two spaces that describe one thing in different terms. A
# pair drawn from one of these sets is not comparable; anything else is a
# genuine difference.
#
# A legacy UA id is deliberately NOT in the GA set. Universal Analytics
# stopped processing in 2023, so a recorded UA id against a live GA4 property
# is a real finding -- the record is genuinely stale -- and it must go on
# saying mismatch.
_SAME_THING = {
    "ga": {"measurement", "property"},
    "gtm": {"public", "numeric"},
}
_SPACES = {"ga": _ga_space, "gtm": _gtm_space}


def _state(recorded: str, live: str, norm, platform: str = "") -> str:
    r, l = norm(recorded), norm(live)
    if r and l:
        if r == l:
            return "match"
        space, same = _SPACES.get(platform), _SAME_THING.get(platform)
        # .get on both: a platform named in one table and not the other must
        # degrade to the old verdict rather than raise. This is called from a
        # client record, where a KeyError costs the whole card.
        if space and same and {space(recorded), space(live)} == same:
            # Two names for one thing, and nothing here can tell whether they
            # name the SAME one. Saying so is the answer; judging it either
            # way is inventing one.
            return "not_comparable"
        return "mismatch"
    if r:
        return "recorded_only"
    if l:
        return "live_only"
    return "missing"


_ADVICE = {
    "match": "Recorded and reachable, and they agree.",
    "mismatch": ("The recorded id and the one we can reach are different. "
                 "Either the site is running a container we don't administer, "
                 "or the Knack record is out of date. Worth resolving — "
                 "reports built on the wrong property are silently wrong."),
    "recorded_only": ("Recorded in Knack but no connected Google account can "
                      "reach it. We can reference it, but we can't administer "
                      "it or pull data from it. Request access from the client."),
    "live_only": ("We have access but it isn't recorded in Knack, so nothing "
                  "else in the Hub knows about it. Add it to the website "
                  "record."),
    "missing": "Neither recorded nor reachable.",
    "not_comparable": (
        "These are two names for one property or container and cannot be "
        "compared here: Knack holds the measurement id (the G- one on the "
        "site and in the tag), and Google reports the numeric property id, "
        "because a GA4 property summary carries no measurement id at all. "
        "They may well be the same property. Recording the property id "
        "beside the measurement id on the website record is what would "
        "settle it."),
}


def _live_google(client: str, domain: str) -> dict:
    """What the connected Google accounts can actually see for this client.

    ## This used to return nothing, always

    It looped `for item in acct.get("items")` over the rows from
    `google_finder.connected_accounts()` — which returns `{email,
    refresh_token, status}` and has never carried an `items` key. The loop
    body never executed. Every call returned blank ids with `error` empty, so
    `compare()` below read *recorded_only* — "in Knack, no Google access,
    request access" — for every client, including the ones whose GA4 property
    we were administering that afternoon. Nothing raised; the page just
    quietly said the wrong thing about the entire book.

    It now reads hub/google_index.py, which is the sweep already joined to
    clients. That is also what makes this cheap enough to call from a page:
    the previous shape, had it worked, would have had to sweep four Google
    APIs per client.
    """
    out = {"ga": "", "gtm": "", "ga_name": "", "gtm_name": "",
           "accounts_connected": 0, "error": ""}
    try:
        from hub import google_index
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"Google index unavailable ({type(exc).__name__})."
        return out

    try:
        found = google_index.for_client(client, domain)
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"Could not read the Google index ({type(exc).__name__})."
        return out

    out["accounts_connected"] = len(google_index.load().get("accounts") or [])

    # An index that has never been built is not the same as a client with no
    # Google presence, and saying so is the whole lesson of the bug above.
    if found.get("never_built"):
        out["error"] = ("The Google account index has not been built yet, so "
                        "nothing can be said about live access. It is rebuilt "
                        "on a schedule, or now from Match Google Accounts "
                        "(/tools/google-match).")
        return out

    for item in found.get("ga4") or []:
        out["ga"] = str(item.get("resource_id") or "")
        out["ga_name"] = str(item.get("name") or "")
        break
    for item in found.get("gtm") or []:
        # The public GTM-XXXX id, not the numeric one: that is what is
        # recorded in Knack and what _norm_gtm compares against.
        out["gtm"] = str(item.get("resource_id") or "")
        out["gtm_name"] = str(item.get("name") or "")
        break
    if found.get("stale"):
        out["error"] = ("The Google index is stale, so this reflects the last "
                        "successful sweep rather than right now.")
    return out


def compare(client: str, domain: str = "") -> dict:
    """Both sources, side by side, with what the difference means."""
    recorded = {"ga": "", "gtm": "", "found": False}
    try:
        from hub.knack_websites import enrich
        reg = enrich(client, domain)
        if reg.get("found"):
            recorded = {"ga": reg.get("ga_account", ""),
                        "gtm": reg.get("gtm_account", ""), "found": True}
            domain = domain or reg.get("domain", "")
    except Exception:                                   # noqa: BLE001
        pass

    live = _live_google(client, domain)

    ga_state = _state(recorded["ga"], live["ga"], _norm_ga, "ga")
    gtm_state = _state(recorded["gtm"], live["gtm"], _norm_gtm, "gtm")

    def row(kind, state, rec, liv, live_name):
        return {
            "kind": kind, "state": state,
            "recorded": rec, "live": liv, "live_name": live_name,
            # Show the recorded value when we have nothing live: it's still
            # the answer to "what's on their site", just not one we can touch.
            "display": rec or liv or "",
            "have_access": bool(liv),
            "advice": _ADVICE[state],
        }

    rows = [row("GA4", ga_state, recorded["ga"], live["ga"], live["ga_name"]),
            row("GTM", gtm_state, recorded["gtm"], live["gtm"], live["gtm_name"])]

    problems = [r for r in rows if r["state"] in ("mismatch", "recorded_only")]
    return {
        "client": client, "domain": domain,
        "in_knack": recorded["found"],
        "google_accounts_connected": live["accounts_connected"],
        "google_error": live["error"],
        "rows": rows,
        "needs_attention": len(problems),
        "note": ("Both sources are shown deliberately. Knack records what the "
                 "client uses; Google shows what we can actually administer. "
                 "Neither replaces the other, and where they disagree that is "
                 "itself the finding."),
    }


# Which bucket of the audit each state lands in. One table rather than an
# if/elif chain, so the client record and the book-wide report cannot come to
# disagree about whether a state is a finding -- and so a state added later
# without an entry here is *dropped loudly* by the test rather than silently
# counted as a mismatch, which is how not_comparable's absence read for every
# GA row in the book.
_BUCKETS = {
    "match": "in_agreement",
    "mismatch": "mismatched",
    "recorded_only": "no_access",
    "not_comparable": "not_comparable",
    # live_only and missing are neither an agreement nor a finding this report
    # is about: they are covered by the two "no access" and "set one up"
    # columns on the client record instead.
    "live_only": "",
    "missing": "",
}


def bucket_for(state: str) -> str:
    """Which audit bucket a state belongs in, or "" for neither."""
    return _BUCKETS.get(str(state or ""), "")


def audit_all(limit: int = 400) -> dict:
    """Every client where the two sources disagree, or we lack access.

    This is the report worth having: a mismatch means somebody's analytics
    reporting may be pointed at the wrong property, and nothing else surfaces
    that.
    """
    try:
        from hub import clients_registry
        clients = clients_registry.all_clients()[:limit]
    except Exception:                                   # noqa: BLE001
        clients = []
    buckets = {b: [] for b in set(_BUCKETS.values()) - {"in_agreement"}}
    ok = 0
    for c in clients:
        name = c.get("name") or ""
        if not name:
            continue
        try:
            r = compare(name, c.get("domain") or c.get("url") or "")
        except Exception:                               # noqa: BLE001
            continue
        for row in r["rows"]:
            bucket = bucket_for(row["state"])
            if bucket == "in_agreement":
                ok += 1
            elif bucket:
                buckets[bucket].append({"client": name, **row})
    return {"checked": len(clients), "in_agreement": ok,
            "mismatched": buckets["mismatched"],
            "no_access": buckets["no_access"],
            "not_comparable": buckets["not_comparable"],
            "note": "A mismatch usually means reporting is pointed at a "
                    "property we don't administer, or the Knack record is "
                    "stale. Either way the numbers can't be trusted until "
                    "it's resolved."}
