"""Is the Google Ads API actually ready, and ready for *what*.

``google_ads.connection_status()`` answers whether the four environment
variables are set and whether somebody has authorized a Google login. That is
the question a settings page asks. It is not the question a rep about to
deploy a client's campaign asks, and the two have different answers far more
often than is comfortable:

* A developer token can be **set, valid, and refused** for the call being
  made, because the token carries an *access tier* and the tiers differ in
  what they may call. A new token is granted **Explorer** access
  automatically: production accounts, 2,880 operations a day, and no keyword
  planning at all. Basic access is applied for and reviewed. So
  ``developer_token: True`` and "we can measure a CPC" are unrelated claims.
* The Hub's login can be **connected** and reach **no accounts at all**,
  because reaching a client's account is a separate act — the client (or their
  agency) accepts a link invitation from our manager account, and until they
  do the API answers with an empty customer list rather than an error.
* An account can be **linked and unusable**: a manager link that is still
  PENDING, an account that is cancelled, or a currency that does not match the
  budget somebody quoted.

Every one of those looks like a working configuration on a settings page and
fails at the moment a rep is standing in front of a client. So this module
asks the questions in the order they bite, and returns a **named checklist**
rather than a boolean: "not ready" is useless, and "not ready: the client has
not accepted the link invitation sent on 3 March" is a phone call.

Two rules it keeps:

* **A check that could not run is not a check that failed.** Google being
  unreachable, a timeout, or no token at all are each their own state and none
  of them is a red cross — the same rule ``services/provider_check.py`` keeps
  for the Commercial Builder's providers, for the same reason: sending
  somebody to rotate a key that was fine wastes the one manual step this
  whole thing exists to remove.
* **Nothing here is called on page load.** Every function makes outbound calls
  against a daily operation cap that the deploy itself also has to fit inside.
  They sit behind buttons.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from . import google_ads, keyword_plan
from .google_ads import GoogleAdsError, digits

# Google does not publish the developer token's access tier over the API —
# there is no field to read and no endpoint to ask. The tier is only ever
# observable from what a call refuses, which is exactly what
# ``keyword_plan.planning_available()`` probes. So the tier is recorded here
# from two sources, and the page says which it is going on: a value someone
# typed on Settings is a *claim*, and a probe is an *observation*.
TIER_SETTING = "google_ads_access_level"
PROBE_SETTING = "google_ads_planning_probe"

STATES = {
    "ok":           "ready",
    "blocked":      "blocked",
    "not_measured": "not measured",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def declared_tier(store=None) -> dict:
    """The access tier as recorded, and where that record came from.

    ``GOOGLE_ADS_ACCESS_LEVEL`` wins over the stored setting for the same
    reason ``GOOGLE_ADS_REFRESH_TOKEN`` does: it is the copy that survives a
    redeploy. Neither is evidence — both are somebody's claim — so the source
    travels with the value and a probe result outranks both on the page.
    """
    env = (os.environ.get("GOOGLE_ADS_ACCESS_LEVEL") or "").strip().lower()
    stored = ""
    if store:
        try:
            stored = (store.get_setting(TIER_SETTING) or "").strip().lower()
        except Exception:                                    # noqa: BLE001
            stored = ""
    tier = env or stored
    if tier not in keyword_plan.TIER_LABELS:
        tier = ""
    return {
        "tier": tier,
        "label": keyword_plan.TIER_LABELS.get(tier, "Not recorded"),
        "note": keyword_plan.TIER_NOTES.get(
            tier, "Nobody has recorded which access tier this token holds, and "
                  "Google does not publish it over the API. Run the planning "
                  "check to find out."),
        "source": "environment" if env else ("hub settings" if stored else "none"),
        "measured": False,
    }


def record_probe(store, result: dict) -> None:
    """Keep the last planning probe so a page can show it without re-running.

    Stored, not derived, because the probe costs an operation and a page that
    re-ran it on every visit would spend the daily cap on rendering. It is
    stamped, and the page shows the stamp — an observation from three weeks
    ago is still an observation, but the reader gets to decide that.
    """
    if not store:
        return
    try:
        import json
        store.set_setting(PROBE_SETTING, json.dumps({**result, "at": _now()}))
    except Exception:                                        # noqa: BLE001
        pass


def last_probe(store=None) -> dict:
    if not store:
        return {}
    try:
        import json
        raw = store.get_setting(PROBE_SETTING) or ""
        return json.loads(raw) if raw else {}
    except Exception:                                        # noqa: BLE001
        return {}


def check_planning(store=None) -> dict:
    """Probe the planning services and remember the answer."""
    result = keyword_plan.planning_available(store)
    record_probe(store, result)
    return result


def tier(store=None) -> dict:
    """The best answer available about the access tier.

    A probe that got a real answer outranks anything typed on Settings: the
    point of this module is that a claim about a credential and the
    credential's behaviour are different things.
    """
    out = declared_tier(store)
    probe = last_probe(store)
    if not probe:
        return out
    if probe.get("available"):
        return {**out, "tier": "basic", "label": keyword_plan.TIER_LABELS["basic"],
                "note": "Keyword planning answered, so this token holds Basic "
                        "access or better.",
                "source": "observed", "measured": True, "at": probe.get("at")}
    if probe.get("state") == "tier_too_low":
        return {**out, "tier": "explorer",
                "label": keyword_plan.TIER_LABELS["explorer"],
                "note": probe.get("detail") or keyword_plan.TIER_NOTES["explorer"],
                "source": "observed", "measured": True, "at": probe.get("at")}
    return {**out, "probe_state": probe.get("state"),
            "probe_detail": probe.get("detail"), "at": probe.get("at")}


# ------------------------------------------------------------------ the ladder
def _check(key, label, state, detail, fix="") -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail, "fix": fix}


def preflight(store=None, customer_id=None, proposal=None) -> dict:
    """Everything that has to be true before a campaign can be deployed.

    Ordered by what blocks what, so the first red row is the one to act on
    rather than the reader having to work that out. Each row is one of three
    states — ready, blocked, or *not measured* — and the last of those is
    never rendered as a failure.
    """
    rows = []
    status = google_ads.connection_status(store)

    # 1. Credentials.
    if status["configured"]:
        rows.append(_check("credentials", "API credentials set", "ok",
                           "Client id, secret and developer token are all set."))
    else:
        rows.append(_check(
            "credentials", "API credentials set", "blocked",
            "Missing: " + ", ".join(status["missing"]),
            "Set these on Render. The developer token is applied for in the "
            "manager account under Tools → API Center."))

    # 2. Authorisation.
    if status["connected"]:
        rows.append(_check("oauth", "Google account authorized", "ok",
                           f"Refresh token from {status['refresh_token_source']}."))
    else:
        rows.append(_check("oauth", "Google account authorized", "blocked",
                           "Nobody has connected a Google login yet.",
                           "Settings → Connect Google Ads."))

    # 3. The tier. Never a cross when it has not been established: an
    #    unrecorded tier is unknown, and unknown is not broken.
    t = tier(store)
    if t.get("measured") and t["tier"] in ("basic", "standard"):
        rows.append(_check("tier", "Developer token access tier", "ok", t["note"]))
    elif t.get("measured"):
        rows.append(_check(
            "tier", "Developer token access tier", "blocked", t["note"],
            "Apply for Basic access. Deploying still works at Explorer access "
            "— it is the measured CPC that does not."))
    else:
        rows.append(_check("tier", "Developer token access tier", "not_measured",
                           t["note"], "Run the planning check on Settings."))

    # 4. Can we reach any account at all, and this one in particular. A client
    #    who has not accepted the manager link shows up here as an account we
    #    cannot see — not as an error, and not as a bad token.
    if not status["connected"] or not status["configured"]:
        rows.append(_check("account", "Client account reachable", "not_measured",
                           "Cannot check until the API is configured and authorized."))
    else:
        try:
            reachable = google_ads.list_accessible_customers(store)
        except GoogleAdsError as exc:
            reachable = None
            rows.append(_check("account", "Client account reachable", "not_measured",
                               f"Google could not be reached: {exc.message}",
                               "Try again; this is not evidence of a bad key."))
        if reachable is not None:
            wanted = digits(customer_id)
            if not reachable:
                rows.append(_check(
                    "account", "Client account reachable", "blocked",
                    "This login can reach no Google Ads accounts.",
                    "Send a manager link invitation from our MCC and have the "
                    "client accept it in their own Google Ads account."))
            elif wanted and wanted not in reachable:
                rows.append(_check(
                    "account", "Client account reachable", "blocked",
                    f"{google_ads.format_customer_id(wanted)} is not among the "
                    f"{len(reachable)} account(s) this login can reach.",
                    "Usually the link invitation has been sent and not yet "
                    "accepted. Check the client's Google Ads account under "
                    "Admin → Access and security → Managers."))
            else:
                rows.append(_check(
                    "account", "Client account reachable", "ok",
                    f"{len(reachable)} account(s) reachable"
                    + (f", including {google_ads.format_customer_id(wanted)}."
                       if wanted else ".")))

    # 5. The Hub's own approval ladder. Deploying is the last rung, and the
    #    two below it are the ones this module cannot see from Google.
    if proposal is not None:
        campaign = proposal.get("campaign") or {}
        estimate = campaign.get("estimate") or {}
        if estimate.get("approved_at") and not estimate.get("superseded"):
            rows.append(_check("estimate", "Estimate approved internally", "ok",
                               f"Approved by {estimate.get('approved_by') or 'a rep'}."))
        else:
            rows.append(_check(
                "estimate", "Estimate approved internally", "blocked",
                "The estimate is not approved, or an edit superseded the "
                "approval.", "Approve it on the proposal page."))

        pending = [e["what"] for e in (campaign.get("editLog") or [])
                   if e.get("material") and not e.get("rechecked")]
        if pending:
            rows.append(_check(
                "recheck", "Material edits re-checked", "blocked",
                "Changed since the last review: " + "; ".join(pending[:4]),
                "Press approve to send it back through the review first."))
        else:
            rows.append(_check("recheck", "Material edits re-checked", "ok",
                               "No unreviewed material edits."))

        if proposal.get("status") == "APPROVED":
            rows.append(_check("status", "Proposal approved for build", "ok",
                               "Status is APPROVED."))
        elif proposal.get("status") == "DEPLOYED":
            rows.append(_check("status", "Proposal approved for build", "blocked",
                               "This proposal has already been deployed.",
                               "Deploying again would create a second campaign."))
        else:
            rows.append(_check(
                "status", "Proposal approved for build", "blocked",
                f"Status is {proposal.get('status') or 'unknown'}, not APPROVED.",
                "Set it to Approved in the Approval Hub."))

        # The client's own answer. Deliberately not a blocker: a rep may build
        # a campaign a client approved by phone. It is shown because "we never
        # sent it" and "they asked to talk first" are worth seeing on the
        # screen where somebody is about to spend their money.
        review = proposal.get("review") or {}
        if review.get("outcome") == "approved":
            rows.append(_check("client", "Client answered", "ok",
                               f"{review.get('reviewer') or 'The client'} approved it."))
        elif review.get("outcome"):
            rows.append(_check("client", "Client answered", "not_measured",
                               f"Client answered: {review.get('outcome')}. "
                               "Not a blocker, but worth reading first."))
        else:
            rows.append(_check("client", "Client answered", "not_measured",
                               "No client response recorded against this estimate."))

    blocked = [r for r in rows if r["state"] == "blocked"]
    unknown = [r for r in rows if r["state"] == "not_measured"]
    return {
        "checks": rows,
        "ready": not blocked,
        "blocked": [r["label"] for r in blocked],
        "unknown": [r["label"] for r in unknown],
        # Said in words: an all-green preflight with three unmeasured rows is
        # not the same claim as an all-green preflight.
        "note": ("Every check passed."
                 if not blocked and not unknown else
                 f"{len(blocked)} blocking, {len(unknown)} not measured."),
        "at": _now(),
    }
