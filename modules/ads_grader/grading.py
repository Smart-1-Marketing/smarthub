"""The score, and the read-only Google Ads access that produces it.

**The detectors are `modules/ads_builder/optimization.py`'s.** Zero-conversion
spend, high click cost against the account's own baseline, weak schedule slots,
keywords spending on nothing, and the two account diagnostics — all already
written, already tested, and already what a rep sees when they scan a client we
manage. A prospect's grader running a second implementation of them would give
a business one score before they signed and a different one after.

**The OAuth here is a fourth pattern, and it is deliberately none of the three
this Hub already has.** `google_finder` holds a staff member's durable refresh
token; `ads_builder` uses the agency's own standing MCC credential;
`google_access` is `access_type=online` and single-request but points the
opposite way — it grants US access to a client's property. This is the
reverse: a stranger grants read-only access to their own Ads account, we read
it inside one request, and the token is **never written anywhere**. It is a
local variable in the callback and it goes out of scope with the request.

Three more rules.

**The developer token is per app, not per account.** Once a stranger grants
`adwords`, the agency's own approved developer token covers reading their
account, so this needs no new credential — and it spends from the same daily
operation budget every other Google Ads call here does.

**A manager account is not a graded account.** listAccessibleCustomers returns
whatever the signed-in user can reach, including MCCs and accounts with nothing
in them. Each is read and the ones with no campaign data are **named** rather
than silently dropped, because "you have no active campaigns" is the single
most useful thing this tool can tell some of the people who run it.

**A weight nobody published is ours and says so.** `WEIGHTS` is house
guidance, marked as such the way `services/abcd_service.HOUSE_LEGIBILITY` is:
Google publishes an optimization score and it is not this, so presenting a
number of our own under Google's name would be borrowing an authority we do
not have.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import requests

from modules.ads_builder import google_ads, optimization

log = logging.getLogger(__name__)

ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
TIMEOUT = 45

# Bounded: one grader run is six GAQL queries per account against a daily
# operation budget the agency's own deploys share, and a prospect signed in on
# a manager login can reach hundreds of accounts. What is left out is named.
MAX_ACCOUNTS_GRADED = 3

# Ours, not Google's. Each finding category costs the score what a rep would
# say it costs, and the whole table is on the report so a reader can check the
# arithmetic rather than being asked to trust a number.
WEIGHTS = {
    "diagnostics": 25,
    "search_terms": 8,
    "keyword_pauses": 8,
    "click_costs": 6,
    "keywords": 3,
    "schedule": 3,
    "google": 2,
}
WEIGHT_SOURCE = "house"
WEIGHT_NOTE = ("This score is Smart 1's, not Google's. Google publishes its own "
               "optimization score and it measures something different — this "
               "one weighs what we would raise on a call. Every finding behind "
               "it is listed below, so the number can be checked rather than "
               "taken on trust.")

GRADES = ((90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F"))


class GraderError(RuntimeError):
    """Something a prospect can be told, in words about their account."""


def configured() -> tuple[bool, list[str]]:
    c = google_ads.cfg()
    missing = [name for name, value in (
        ("GOOGLE_ADS_CLIENT_ID", c["client_id"]),
        ("GOOGLE_ADS_CLIENT_SECRET", c["client_secret"]),
        ("GOOGLE_ADS_DEVELOPER_TOKEN", c["developer_token"]),
    ) if not value]
    return (not missing), missing


def redirect_uri() -> str:
    """The exact string this flow's callback is registered under.

    From PUBLIC_BASE_URL and never the browser's own host: Google matches a
    redirect URI exactly, and a service answering on two hostnames -- which is
    Render's default -- would otherwise send a string that is registered on
    one of them and not the other. That failure lands at a consent screen in
    front of a prospect, which is the reason hub/oauth_redirects.py exists.
    """
    from hub.config import public_base_origin
    return (public_base_origin() or "").rstrip("/") + "/tools/ads-grader/oauth/callback"


def auth_url(state: str) -> str:
    ok, missing = configured()
    if not ok:
        raise GraderError("This tool is not configured yet: " + ", ".join(missing))
    return OAUTH_AUTH_URL + "?" + urlencode({
        "client_id": google_ads.cfg()["client_id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": ADS_SCOPE,
        # ONLINE, not offline. There is no refresh token to want: the grant is
        # used inside one request and thrown away, and asking for one we would
        # not keep is asking a stranger for more than the job needs.
        "access_type": "online",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    })


def exchange_code(code: str) -> str:
    """Authorization code in, access token out — returned, never stored.

    Deliberately not `google_ads.exchange_code()`: that one writes into a
    module-level cache the staff paths read, so a prospect's token would
    become the credential the agency's own next call went out under.
    """
    c = google_ads.cfg()
    resp = requests.post(OAUTH_TOKEN_URL, data={
        "code": code, "client_id": c["client_id"],
        "client_secret": c["client_secret"], "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    }, timeout=TIMEOUT)
    if not resp.ok:
        raise GraderError("Google did not accept that sign-in. Please try again.")
    token = str((resp.json() or {}).get("access_token") or "")
    if not token:
        raise GraderError("Google returned no access token.")
    return token


def _headers(token: str) -> dict:
    c = google_ads.cfg()
    head = {"Authorization": f"Bearer {token}",
            "developer-token": c["developer_token"],
            "Content-Type": "application/json"}
    return head


def accessible_customers(token: str) -> list[str]:
    resp = requests.get(f"{google_ads.base_url()}/customers:listAccessibleCustomers",
                        headers=_headers(token), timeout=TIMEOUT)
    if not resp.ok:
        raise GraderError(_google_message(resp))
    return [google_ads.digits(name.split("/")[-1])
            for name in ((resp.json() or {}).get("resourceNames") or [])]


def _search(token: str, customer_id: str, query: str) -> list[dict]:
    """One GAQL page, on a visitor's own grant.

    A single page: the grader reads a 30-day summary, not an export, and a
    prospect's account with ten thousand keywords must not turn one press into
    a hundred requests against a shared daily quota.
    """
    resp = requests.post(
        f"{google_ads.base_url()}/customers/{customer_id}/googleAds:search",
        headers=_headers(token), json={"query": query, "pageSize": 1000},
        timeout=TIMEOUT)
    _record(resp.ok)
    if not resp.ok:
        raise GraderError(_google_message(resp))
    return (resp.json() or {}).get("results") or []


def _record(ok: bool) -> None:
    try:
        from hub import quotas
        quotas.record_google(f"{google_ads.base_url()}/googleAds:search",
                             module="ads_grader", ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def _google_message(resp) -> str:
    try:
        payload = resp.json()
        errors = (payload.get("error") or {}).get("details") or []
        for detail in errors:
            for err in detail.get("errors") or []:
                if err.get("message"):
                    return str(err["message"])[:300]
        return str((payload.get("error") or {}).get("message") or resp.text[:300])
    except Exception:                                    # noqa: BLE001
        return f"Google refused the request (HTTP {resp.status_code})."


def grade_account(token: str, customer_id: str,
                  date_range: str = "LAST_30_DAYS") -> dict:
    """Read one account and score it, using the scanner's own detectors."""
    queries = {
        "summary": optimization.SUMMARY_QUERY,
        "campaigns": optimization.CAMPAIGNS_QUERY.format(date_range=date_range),
        "search_terms": optimization.SEARCH_TERMS_QUERY.format(date_range=date_range),
        "keywords": optimization.KEYWORDS_QUERY.format(date_range=date_range),
        "schedule": optimization.SCHEDULE_QUERY.format(date_range=date_range),
    }
    datasets, errors = {}, {}
    for name, query in queries.items():
        try:
            datasets[name] = _search(token, customer_id, query)
        except GraderError as exc:
            # One section refusing is a section, not the account: the same
            # isolation scan_account() works to.
            datasets[name] = []
            errors[name] = str(exc)
    if len(errors) == len(queries):
        raise GraderError(next(iter(errors.values())))

    scan = optimization.analyse_rows(customer_id, date_range, datasets, errors)
    return {**scan, **score(scan)}


def score(scan: dict) -> dict:
    """Turn the scanner's findings into a number, showing the arithmetic."""
    deductions, by_category = [], {}
    for item in scan.get("items") or []:
        weight = WEIGHTS.get(item.get("category"), 2)
        if item.get("severity") == "medium":
            weight = max(1, round(weight * 0.6))
        elif item.get("severity") == "low":
            weight = max(1, round(weight * 0.3))
        by_category[item.get("category")] = by_category.get(item.get("category"), 0) + weight
    total = sum(by_category.values())
    value = max(0, min(100, 100 - total))
    for key, cost in sorted(by_category.items(), key=lambda kv: -kv[1]):
        deductions.append({"category": key, "points": cost,
                           "label": key.replace("_", " ").capitalize()})

    letter = next(g for floor, g in GRADES if value >= floor)
    totals = scan.get("totals") or {}
    return {
        "score": value, "grade": letter, "deductions": deductions,
        "weight_source": WEIGHT_SOURCE, "weight_note": WEIGHT_NOTE,
        # The three findings worth a phone call, in the order the scanner
        # already ranked them. Never a product name: what a business can check
        # is the measurement, and what they argue with is the pitch.
        "top_issues": [{"title": i.get("title", ""), "why": i.get("why", ""),
                        "severity": i.get("severity", "")}
                       for i in (scan.get("items") or [])
                       if i.get("severity") == "high"][:5],
        "spend": totals.get("cost"),
        "clicks": totals.get("clicks"),
        "conversions": totals.get("conversions"),
        # A section Google refused is named rather than scored as clean: a
        # grade computed over half an account is a confident wrong answer.
        "measured": not scan.get("errors"),
        "not_measured": sorted((scan.get("errors") or {}).keys()),
    }
