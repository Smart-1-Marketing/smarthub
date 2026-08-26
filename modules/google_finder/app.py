import json
import os
import random
import secrets
import sqlite3
import threading
import time
import re
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from flask import (Flask, redirect, render_template, request, session,
                   url_for, jsonify, g, current_app, has_app_context)
from flask_session import Session
from cryptography.fernet import Fernet, InvalidToken
from werkzeug.middleware.proxy_fix import ProxyFix
from hub.webargs import clamp_int

# Activity logging — so this tool's output appears on the client's
# 360 record. This module reads client Google properties, and work that isn't logged is work
# nobody can point to later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("google_finder")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("google-account-finder")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config.update(
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=os.environ.get("SESSION_FILE_DIR", "/tmp/smart1-google-finder-sessions"),
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,
)
Session(app)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "900"))
TOKEN_DB_PATH = os.environ.get("TOKEN_DB_PATH", "/var/data/google_tokens.db")
TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

ALLOWED_EMAILS = {
    x.strip().lower()
    for x in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if x.strip()
}

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/business.manage",
    "https://www.googleapis.com/auth/webmasters",
]

CACHE = {}


def _fernet():
    if not TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(TOKEN_ENCRYPTION_KEY.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def _connect():
    """Open a connection to the token database and make sure it has tables."""
    db_dir = os.path.dirname(TOKEN_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(TOKEN_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    init_db(conn)
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


@contextmanager
def _db():
    """A connection to the token database, with or without a request.

    ``get_db()`` caches on ``flask.g`` and is closed by ``close_db`` when the
    request ends. That is right for a route and unusable anywhere else, and
    the account table is read from two places that are not routes: the
    scheduler's google_index sweep runs in a background thread with no
    application context at all, and /api/google/rebuild calls the same sweep
    under the *hub* app's context. Outside a context every read raised
    RuntimeError inside connected_accounts()'s except and came back as an
    empty list, so the sweep concluded "No Google accounts are connected"
    every three hours while the accounts sat in the table untouched — a
    confident wrong answer with a live tool behind it.

    The ``g`` cache is used only when the context in play is *this* app's.
    Under the hub app's context ``g`` would take the connection and this
    app's teardown would never run to close it, which leaks one sqlite handle
    per rebuild.

    Anything reaching this table goes through here rather than through
    get_db(), so the next function added to this module does not have to
    know which of the two worlds it will be called from.
    """
    own = False
    try:
        own = has_app_context() and current_app._get_current_object() is app
    except Exception:                                   # noqa: BLE001
        own = False
    if own:
        yield get_db()
        return
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_accounts (
            email TEXT PRIMARY KEY,
            refresh_token_enc TEXT NOT NULL,
            status TEXT DEFAULT 'ACTIVE',
            connected_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            summary_title TEXT NOT NULL,
            property_id TEXT NOT NULL,
            google_login TEXT NOT NULL,
            report_data TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            notification_email TEXT NOT NULL,
            frequency TEXT NOT NULL,
            ghl_webhook_url TEXT,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(report_id) REFERENCES saved_reports(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gtm_change_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_login TEXT NOT NULL,
            account_id TEXT NOT NULL,
            container_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()


def connected_accounts_result():
    """The connected accounts, and what stopped us reading them.

    Returns ``(accounts, error)``. A caller that cannot tell "there are none"
    from "we could not look" reports an empty book as a clean answer — which
    is exactly how the Google index came to announce, every three hours, that
    no account was connected while one was.

    Two ways this list comes back short without anything erroring, so both
    are named rather than dropped:

      * the table could not be read at all (no application context, no
        TOKEN_ENCRYPTION_KEY, a database that is not there);
      * a stored row would not decrypt, which means TOKEN_ENCRYPTION_KEY has
        changed since it was written. Skipping those in silence turns a
        rotated key into "nobody has ever connected an account".
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT email, refresh_token_enc, status FROM google_accounts ORDER BY email"
            ).fetchall()
        f = _fernet()
    except Exception as exc:                            # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:200]

    accounts, undecryptable = [], 0
    for row in rows:
        try:
            token = f.decrypt(row["refresh_token_enc"].encode("utf-8")).decode("utf-8")
        except InvalidToken:
            undecryptable += 1
            continue
        accounts.append({"email": row["email"], "refresh_token": token, "status": row["status"]})

    error = ""
    if undecryptable:
        error = (f"{undecryptable} of {len(rows)} stored Google account(s) "
                 f"could not be decrypted — TOKEN_ENCRYPTION_KEY has changed "
                 f"since they were connected, and they need reconnecting.")
    return accounts, error


def connected_accounts():
    """Just the list, for the twenty routes that render it.

    Use connected_accounts_result() anywhere the difference between "none"
    and "could not read" changes what you would report.
    """
    return connected_accounts_result()[0]


def save_account(email, refresh_token):
    now = int(time.time())
    token_enc = _fernet().encrypt(refresh_token.encode("utf-8")).decode("utf-8")
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO google_accounts (email, refresh_token_enc, status, connected_at, updated_at)
            VALUES (?, ?, 'ACTIVE', ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                refresh_token_enc=excluded.refresh_token_enc,
                status='ACTIVE',
                updated_at=excluded.updated_at
            """,
            (email.lower(), token_enc, now, now),
        )
        conn.commit()


def mark_account_reauth(email):
    with _db() as conn:
        conn.execute("UPDATE google_accounts SET status='REAUTH_REQUIRED' WHERE email=?", (email.lower(),))
        conn.commit()


def delete_account(email):
    with _db() as conn:
        conn.execute("DELETE FROM google_accounts WHERE email = ?", (email.lower(),))
        conn.commit()


def is_allowed(email):
    return not ALLOWED_EMAILS or email.lower() in ALLOWED_EMAILS


class ReauthRequired(RuntimeError):
    """The account's refresh token is dead — only the user can fix it, by
    signing in again. Kept distinct from a genuine server fault so routes can
    answer 401 with a reconnect link instead of a 500 nobody can action."""

    def __init__(self, email):
        self.email = email
        super().__init__(
            f"Authentication token for {email} was revoked or expired. "
            "Re-login required.")


def refresh_access_token(email, refresh_token):
    try:
        r = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        # Counted too. Refreshes cost nothing, but the count is the only
        # signal that something is refreshing far more often than tokens
        # expire — /diagnostics alone refreshes up to eight per page load.
        _note_google("https://oauth2.googleapis.com/token", ok=r.ok)
        r.raise_for_status()
        return r.json()["access_token"]
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code in (400, 401):
            mark_account_reauth(email)
            raise ReauthRequired(email) from exc
        raise


def sanitize_regex(pattern):
    if not pattern:
        return ""
    try:
        re.compile(pattern)
        return pattern
    except re.error:
        return re.escape(pattern)


def _note_google(url, ok=True):
    """Count one Google API call against the daily quota shown on /diagnostics.

    Filed by URL, not by caller: this one helper is used against GA4, Tag
    Manager, Search Console and Business Profile, and a per-module count could
    never say which of those is close to its ceiling.

    Called whatever the response was. A 429 or a 403 still consumed a request
    against the quota — those are in fact the calls most worth counting, since
    they are what a spent quota looks like.
    """
    try:
        from hub import quotas as _q
        _q.record_google(url, module="google_finder", ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def google_get(access_token, url, params=None):
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=12,
    )
    _note_google(url, ok=r.ok)
    r.raise_for_status()
    return r.json()


def google_post(access_token, url, json_body=None):
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json=json_body or {},
        timeout=15,
    )
    _note_google(url, ok=r.ok)
    r.raise_for_status()
    return r.json()


def google_put(access_token, url, json_body=None):
    r = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json=json_body or {},
        timeout=15,
    )
    _note_google(url, ok=r.ok)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------------
# What each sweep actually managed
# ---------------------------------------------------------------------------
# Every fetcher below swallows its own exception and returns an empty list.
# That is right — one platform failing must not cost the other three — and it
# is also how "this login has no Tag Manager containers" and "Tag Manager
# refused us" came to look identical on every screen that reads this index.
# A login consented before a scope was added to SCOPES keeps the grant it was
# given: Google does not widen an existing refresh token, so the call 403s for
# ever and the page shows nothing, with nothing anywhere saying why.
#
# So each fetcher now files a note. `kind` is the answer to "why is this
# empty", and the four are genuinely different situations:
#
#     ok        we asked and this is what there is (count may be 0)
#     refused   Google said no — almost always a scope this token never got,
#               or an API not enabled on the Cloud project. Reconnecting the
#               login re-consents, because the connect URL forces prompt=consent.
#     failed    we could not ask — a network error, a bad token
#     disabled  we did not ask, because this deployment switched it off
#
# A note with count 0 and kind "ok" is a real answer. A note with kind
# "refused" is not, and must never be rendered as a clean nothing.
def _note(notes, email, platform, kind, count=0, error=""):
    if notes is None:
        return
    notes.append({"email": email, "platform": platform, "kind": kind,
                  "ok": kind == "ok", "count": count,
                  "error": str(error)[:300]})


def _refused(exc) -> bool:
    """A 401/403 is a permission answer, not an outage.

    Told apart because they need opposite things from a person: a refusal is
    "reconnect this login" and an outage is "try again later", and calling
    the first one a failure sends somebody to check their network.
    """
    text = str(exc)
    return "401" in text or "403" in text or "insufficient" in text.lower() \
        or "permission" in text.lower() or "forbidden" in text.lower()


def fetch_ga_items(access_token, google_login, notes=None):
    items = []
    token = None
    try:
        while True:
            params = {"pageSize": 200}
            if token:
                params["pageToken"] = token
            data = google_get(
                access_token,
                "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
                params=params,
            )
            for acct in data.get("accountSummaries", []):
                account_resource = acct.get("account", "")
                account_id = account_resource.split("/")[-1] if account_resource else ""
                account_name = acct.get("displayName", "")
                for prop in acct.get("propertySummaries", []):
                    property_resource = prop.get("property", "")
                    property_id = property_resource.split("/")[-1] if property_resource else ""
                    property_name = prop.get("displayName", "")
                    items.append({
                        "platform": "Google Analytics",
                        "type": "GA4 Property",
                        "name": property_name,
                        "account_name": account_name,
                        "account_id": account_id,
                        "resource_id": property_id,
                        # A GA4 property summary carries no website URL at
                        # all, so this is empty by nature and not by omission.
                        # hub/google_index.py reports a name match on these as
                        # weaker for exactly that reason.
                        "domains": [],
                        "search_extra": f"{property_name} {account_name} {property_id}",
                        "google_login": google_login,
                        "open_url": f"https://analytics.google.com/analytics/web/#/p{property_id}" if property_id else "",
                    })
            token = data.get("nextPageToken")
            if not token:
                break
        _note(notes, google_login, "Google Analytics", "ok", len(items))
    except Exception as exc:
        logger.warning("Failed fetching GA4 items for %s: %s", google_login, exc)
        _note(notes, google_login, "Google Analytics",
              "refused" if _refused(exc) else "failed", len(items), exc)
    return items


# Tag Manager enforces a low per-user request rate — far lower than Analytics.
# With 150+ GTM accounts on one login, firing container requests back to back
# means Google returns 429 for nearly all of them and every container is
# skipped. That is why containers never appeared in the Hub: not missing
# access, just rate limiting we weren't handling.
_GTM_MIN_INTERVAL = float(os.environ.get("GTM_MIN_INTERVAL_SECONDS") or 0.35)
_GTM_MAX_RETRIES = int(os.environ.get("GTM_MAX_RETRIES") or 4)
_gtm_last_call = [0.0]
# gunicorn runs --threads 8, so the pacing below was being read and written by
# eight threads at once: each could see the same "last call" timestamp, each
# decide no wait was needed, and all eight fire together — which is the burst
# the pacing exists to prevent. The lock is held across the sleep on purpose.
# Serialising Tag Manager calls is the entire point; a lock that let them
# overlap would pace nothing.
#
# Two WORKERS are a separate problem a lock cannot solve, and the fix for that
# is not here: hub/scheduler.py sweeps under the leader lock, so only one
# worker builds the index and only one paces itself against Google.
_gtm_lock = threading.Lock()


def gtm_get(access_token, url, params=None):
    """GET against Tag Manager, paced and retried on 429.

    Spacing every call is what actually fixes this — retrying alone still
    sends the first burst too fast and just moves the failures later.
    """
    for attempt in range(_GTM_MAX_RETRIES):
        with _gtm_lock:
            gap = time.time() - _gtm_last_call[0]
            if gap < _GTM_MIN_INTERVAL:
                time.sleep(_GTM_MIN_INTERVAL - gap)
            _gtm_last_call[0] = time.time()
        try:
            return google_get(access_token, url, params=params)
        except Exception as exc:                        # noqa: BLE001
            is_429 = "429" in str(exc) or "Too Many Requests" in str(exc)
            if not is_429 or attempt == _GTM_MAX_RETRIES - 1:
                raise
            # Exponential backoff with a little jitter, so parallel workers
            # don't retry in lockstep and re-create the burst.
            wait = (2 ** attempt) + random.uniform(0, 0.4)
            logger.info("GTM rate limited, waiting %.1fs (attempt %d/%d)",
                        wait, attempt + 1, _GTM_MAX_RETRIES)
            time.sleep(wait)
    return {}


def fetch_gtm_items(access_token, google_login, notes=None):
    items = []
    token = None
    accounts = []

    try:
        while True:
            params = {}
            if token:
                params["pageToken"] = token
            data = gtm_get(access_token, "https://tagmanager.googleapis.com/tagmanager/v2/accounts", params=params)
            accounts.extend(data.get("account", []))
            token = data.get("nextPageToken")
            if not token:
                break
    except Exception as exc:
        logger.warning("Failed fetching GTM accounts for %s: %s", google_login, exc)
        _note(notes, google_login, "Google Tag Manager",
              "refused" if _refused(exc) else "failed", 0, exc)
        return items

    skipped: list[str] = []
    for acct in accounts:
        account_id = acct.get("accountId", "")
        account_name = acct.get("name", "")
        parent = acct.get("path") or f"accounts/{account_id}"
        ctoken = None

        while True:
            params = {}
            if ctoken:
                params["pageToken"] = ctoken

            try:
                data = gtm_get(
                    access_token,
                    f"https://tagmanager.googleapis.com/tagmanager/v2/{parent}/containers",
                    params=params,
                )
                for c in data.get("container", []):
                    public_id = c.get("publicId", "")
                    container_id = c.get("containerId", "")
                    container_name = c.get("name", "")
                    domains = " ".join(c.get("domainName", []) or [])
                    items.append({
                        "platform": "Google Tag Manager",
                        "type": "GTM Container",
                        "name": container_name,
                        "account_name": account_name,
                        "account_id": account_id,
                        "resource_id": public_id or container_id,
                        "internal_container_id": container_id,
                        # Explicit, rather than left to be scraped back out of
                        # search_extra: these domains are how a container gets
                        # joined to a client, and a join key hidden inside a
                        # free-text search blob is a join key nothing can rely
                        # on.
                        "domains": list(c.get("domainName") or []),
                        "search_extra": f"{domains} {container_name} {public_id}",
                        "google_login": google_login,
                        "open_url": f"https://tagmanager.google.com/#/container/accounts/{account_id}/containers/{container_id}" if account_id and container_id else "",
                    })
                ctoken = data.get("nextPageToken")
            except Exception as exc:
                # One line per failure buried the signal in 150 lines of noise.
                # Count them and report once at the end instead.
                skipped.append(account_id)
                if len(skipped) <= 3:
                    logger.warning("Skipping containers for GTM account %s (%s): %s",
                                   account_id, google_login, exc)
                break

            if not ctoken:
                break

    if skipped:
        # One summary line instead of 150 near-identical warnings — the
        # volume was hiding the GMB 403 further down the same log.
        logger.warning(
            "GTM: %d of %d accounts skipped for %s after retries. "
            "Raise GTM_MIN_INTERVAL_SECONDS if this persists.",
            len(skipped), len(accounts), google_login)
        # A partial sweep is not a complete one. Without this the containers
        # under the skipped accounts are simply absent, and the page reads as
        # though this login owns fewer than it does.
        _note(notes, google_login, "Google Tag Manager", "failed", len(items),
              f"{len(skipped)} of {len(accounts)} Tag Manager account(s) were "
              f"skipped after retries — their containers are missing from "
              f"this list. Tag Manager rate-limits hard; raise "
              f"GTM_MIN_INTERVAL_SECONDS if it keeps happening.")
    else:
        _note(notes, google_login, "Google Tag Manager", "ok", len(items))
    return items


# The Business Profile APIs need per-project access granted by Google, on top
# of the OAuth scope. Until that's approved every call returns 403, once per
# connected account per sweep — noise that buries real errors in the deploy
# log. Off by default; flip it on when Google approves.
GMB_ENABLED = (os.environ.get("GOOGLE_GMB_ENABLED", "").strip().lower()
               in {"1", "true", "yes", "on"})


def fetch_gmb_items(access_token, google_login, notes=None):
    if not GMB_ENABLED:
        # Not a failure and not an empty book: nobody asked. The variable is
        # named so the answer to "why is there no Business Profile here" is on
        # the screen rather than in this file.
        _note(notes, google_login, "Google Business Profile", "disabled", 0,
              "GOOGLE_GMB_ENABLED is not set on this deployment.")
        return []
    items = []
    try:
        acct_data = google_get(
            access_token,
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
        )
        accounts = acct_data.get("accounts", [])

        for acct in accounts:
            account_resource = acct.get("name", "")
            account_id = account_resource.split("/")[-1] if account_resource else ""
            account_name = acct.get("accountName", "Google Business Profile")

            try:
                loc_data = google_get(
                    access_token,
                    f"https://mybusinessbusinessinformation.googleapis.com/v1/{account_resource}/locations",
                    params={"readMask": "name,title,websiteUri,storefrontAddress,locationState"}
                )
                for loc in loc_data.get("locations", []):
                    loc_resource = loc.get("name", "")
                    loc_id = loc_resource.split("/")[-1] if loc_resource else ""
                    loc_title = loc.get("title", "(Unnamed Location)")
                    website_url = loc.get("websiteUri", "")
                    
                    loc_state = loc.get("locationState", {})
                    is_verified = loc_state.get("isVerified", False)
                    has_pending = loc_state.get("hasPendingVerification", False)
                    
                    if is_verified:
                        claimed_status = "Claimed & Verified"
                    elif has_pending:
                        claimed_status = "Verification Pending"
                    else:
                        claimed_status = "Unclaimed / Unverified"

                    address_data = loc.get("storefrontAddress", {})
                    address_str = " ".join(address_data.get("addressLines", []) or [])

                    items.append({
                        "platform": "Google Business Profile",
                        "type": f"GMB Listing ({claimed_status})",
                        "name": loc_title,
                        "account_name": account_name,
                        "account_id": account_id,
                        "resource_id": loc_id,
                        "domains": [website_url] if website_url else [],
                        "search_extra": f"{website_url} {address_str} {claimed_status}".strip(),
                        "google_login": google_login,
                        "open_url": f"https://business.google.com/dashboard/l/{loc_id}" if loc_id else "https://business.google.com/",
                    })
            except Exception as loc_exc:
                logger.warning("Failed fetching locations for GMB account %s (%s): %s", account_id, google_login, loc_exc)
                _note(notes, google_login, "Google Business Profile",
                      "refused" if _refused(loc_exc) else "failed", len(items),
                      f"account {account_id}: {loc_exc}")
        if not any(n.get("email") == google_login
                   and n.get("platform") == "Google Business Profile"
                   for n in (notes or [])):
            _note(notes, google_login, "Google Business Profile", "ok", len(items))
    except Exception as exc:
        logger.warning("Failed fetching GMB accounts for %s: %s", google_login, exc)
        # The usual answer here is a 403 that has nothing to do with the
        # OAuth scope: the Business Profile APIs need per-project access
        # granted by Google on top of it, so a login can be perfectly
        # connected and still see none of its listings.
        _note(notes, google_login, "Google Business Profile",
              "refused" if _refused(exc) else "failed", len(items), exc)

    return items


def fetch_gsc_items(access_token, google_login, notes=None):
    items = []
    try:
        data = google_get(access_token, "https://www.googleapis.com/webmasters/v3/sites")
        entries = data.get("siteEntry", [])
        for site in entries:
            site_url = site.get("siteUrl", "")
            permission = site.get("permissionLevel", "")
            clean_domain = site_url.replace("sc-domain:", "").replace("https://", "").replace("http://", "").strip("/")
            
            items.append({
                "platform": "Search Console",
                "type": "GSC Property",
                "name": site_url,
                "account_name": f"Permission: {permission}",
                "account_id": site_url,
                "resource_id": site_url,
                # A Search Console property IS a domain, which makes these
                # the hardest join in the index.
                "domains": [clean_domain] if clean_domain else [],
                "search_extra": f"{clean_domain} {permission}",
                "google_login": google_login,
                "open_url": f"https://search.google.com/search-console?resource_id={urlencode({'': site_url})[1:]}",
            })
        _note(notes, google_login, "Search Console", "ok", len(items))
    except Exception as exc:
        logger.warning("Failed fetching Search Console sites for %s: %s", google_login, exc)
        _note(notes, google_login, "Search Console",
              "refused" if _refused(exc) else "failed", len(items), exc)

    return items


def get_account_index(account, force=False, notes=None):
    email = account["email"].lower()
    cached = CACHE.get(email, {"expires": 0, "items": [], "notes": []})
    now = time.time()
    if not force and cached["expires"] > now:
        # The notes are cached with the items, or a cached sweep would report
        # nothing about itself and a refusal would vanish for an hour.
        if notes is not None:
            notes.extend(cached.get("notes") or [])
        return cached["items"]

    mine: list = []
    access_token = refresh_access_token(email, account["refresh_token"])
    # Four separate calls rather than one expression, so a platform that
    # raises where its own handler cannot catch it costs its own list and not
    # the other three's.
    items = []
    for fetch in (fetch_ga_items, fetch_gtm_items, fetch_gmb_items,
                  fetch_gsc_items):
        try:
            items.extend(fetch(access_token, email, mine))
        except Exception as exc:                        # noqa: BLE001
            logger.warning("%s raised for %s: %s", fetch.__name__, email, exc)
            _note(mine, email, fetch.__name__, "failed", 0, exc)
    CACHE[email] = {"expires": now + CACHE_SECONDS, "items": items,
                    "notes": mine}
    if notes is not None:
        notes.extend(mine)
    return items


def get_index(force=False, notes=None):
    """Every resource across every connected login.

    `notes` is filled with one row per login per platform when a list is
    passed: what was asked, what came back, and why it is empty when it is.
    Callers that do not care pass nothing and behave exactly as before.
    """
    items = []
    errors = []
    for account in connected_accounts():
        email = account.get("email", "unknown")
        try:
            items.extend(get_account_index(account, force=force, notes=notes))
        except Exception as exc:
            errors.append({"email": email, "error": str(exc)})
            _note(notes, email, "all", "failed", 0, exc)
    return items, errors


def generate_ga4_ai_analysis(p1_name, p2_name, m1, m2, dimension_breakdown, tone="positive", preset="executive"):
    sessions_p1 = m1.get("sessions", 0)
    sessions_p2 = m2.get("sessions", 0)
    engaged_p1 = m1.get("engagedSessions", 0)
    engaged_p2 = m2.get("engagedSessions", 0)
    conversions_p1 = m1.get("keyEvents", 0)
    conversions_p2 = m2.get("keyEvents", 0)

    if sessions_p2 > 0:
        sess_change = ((sessions_p1 - sessions_p2) / sessions_p2) * 100
        verdict = f"Traffic changed by **{sess_change:+.1f}%** during {p1_name} compared to {p2_name}."
    else:
        sess_change = 100.0 if sessions_p1 > 0 else 0.0
        verdict = f"Selected period recorded **{sessions_p1:,}** sessions (Baseline prior period recorded {sessions_p2:,} sessions)."

    prefix = "📊 Executive Brief" if preset == "executive" else ("🛒 E-Commerce Audit" if preset == "ecommerce" else "📍 Local Presence Review")
    summary_tone = f"{prefix}: {('Positive Highlights' if tone == 'positive' else 'Optimization Areas')}"
    insights = [verdict]

    eng_rate_p1 = (engaged_p1 / sessions_p1 * 100) if sessions_p1 > 0 else 0
    eng_rate_p2 = (engaged_p2 / sessions_p2 * 100) if sessions_p2 > 0 else 0

    if tone == "positive":
        insights.append(
            f"⚡ **Engagement Highlights:** Maintained **{eng_rate_p1:.1f}%** engagement rate with **{engaged_p1:,}** engaged user sessions."
        )
    else:
        insights.append(
            f"⚠️ **Engagement Check:** Engagement rate sits at **{eng_rate_p1:.1f}%** (vs **{eng_rate_p2:.1f}%** in prior period). Focus on high-bounce pages."
        )

    if dimension_breakdown:
        top_gainer = max(dimension_breakdown, key=lambda x: x.get("session_diff", 0), default=None)
        top_loser = min(dimension_breakdown, key=lambda x: x.get("session_diff", 0), default=None)

        if tone == "positive" and top_gainer and top_gainer.get("session_diff", 0) > 0:
            insights.append(
                f"🚀 **Top Growth Driver:** `{top_gainer['name']}` delivered **+{top_gainer['session_diff']:,}** additional sessions "
                f"({top_gainer['p2_sessions']:,} → {top_gainer['p1_sessions']:,})."
            )
        elif tone == "troubling" and top_loser and top_loser.get("session_diff", 0) < 0:
            insights.append(
                f"📉 **Major Traffic Loss:** `{top_loser['name']}` lost **{top_loser['session_diff']:,}** sessions "
                f"({top_loser['p2_sessions']:,} → {top_loser['p1_sessions']:,}). Re-evaluate campaign budgeting."
            )

    c_rate_p1 = (conversions_p1 / sessions_p1 * 100) if sessions_p1 > 0 else 0
    c_rate_p2 = (conversions_p2 / sessions_p2 * 100) if sessions_p2 > 0 else 0
    if tone == "positive":
        insights.append(
            f"🎯 **Key Events:** Generated **{conversions_p1:,}** key event conversions (**{c_rate_p1:.2f}%** conversion rate)."
        )
    else:
        insights.append(
            f"🎯 **Conversion Trend:** Conversion rate is **{c_rate_p1:.2f}%** vs **{c_rate_p2:.2f}%** in prior period ({conversions_p1:,} vs {conversions_p2:,} key events)."
        )

    return {
        "status": summary_tone,
        "sess_change_pct": round(sess_change, 1),
        "insights": insights,
    }


# ROUTE PAGE HANDLERS
@app.route("/")
def index():
    return render_template("index.html", accounts=connected_accounts(), current_page="home")

@app.route("/ga-tools")
def view_ga_tools():
    return render_template("ga_tools.html", accounts=connected_accounts(), current_page="ga")

@app.route("/gtm-tools")
def view_gtm_tools():
    return render_template("gtm_tools.html", accounts=connected_accounts(), current_page="gtm")

@app.route("/webmaster-tools")
def view_webmaster_tools():
    return render_template("webmaster_tools.html", accounts=connected_accounts(), current_page="webmaster")

@app.route("/gmb-tools")
def view_gmb_tools():
    return render_template("gmb_tools.html", accounts=connected_accounts(), current_page="gmb")

@app.route("/history")
def view_history_page():
    return render_template("history.html", accounts=connected_accounts(), current_page="history")


@app.route("/login")
def login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "Google OAuth environment variables are not configured.", 500

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    redirect_uri = url_for("oauth_callback", _external=True, _scheme="https")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.route("/oauth2callback")
def oauth_callback():
    try:
        google_error = request.args.get("error")
        if google_error:
            desc = request.args.get("error_description", "Google declined authorization.")
            logger.error("OAuth callback returned Google error: %s - %s", google_error, desc)
            return f"OAuth authorization failed: {google_error}. {desc}", 400

        expected_state = session.get("oauth_state")
        received_state = request.args.get("state")
        if not expected_state or received_state != expected_state:
            logger.error("OAuth state mismatch. expected_present=%s received_present=%s", bool(expected_state), bool(received_state))
            return "OAuth state validation failed. Start again from Connect Google Account.", 400

        code = request.args.get("code")
        if not code:
            return "Google returned to the callback without an authorization code.", 400

        redirect_uri = url_for("oauth_callback", _external=True, _scheme="https")
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        if not token_resp.ok:
            return f"Google token exchange failed (HTTP {token_resp.status_code}).", 500

        token = token_resp.json()
        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        if not access_token:
            return "Google did not return an access token.", 500

        userinfo = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if not userinfo.ok:
            return f"Google login succeeded, but profile lookup failed.", 500

        email = userinfo.json().get("email", "").lower()
        if not email or not is_allowed(email):
            return "This Google account is not allowed to connect to this finder.", 403

        existing = next((a for a in connected_accounts() if a.get("email", "").lower() == email), None)
        if not refresh_token and not existing:
            return "Google did not issue a refresh token. Remove this app from your Google account permissions, then reconnect.", 400

        if refresh_token:
            save_account(email, refresh_token)

        session.pop("oauth_state", None)
        CACHE.pop(email, None)
        return redirect(url_for("index"))
    except Exception as exc:
        logger.exception("Unhandled OAuth callback error")
        return f"Authentication failed: {exc}", 500


@app.route("/disconnect/<path:email>", methods=["POST"])
def disconnect(email):
    email = email.lower()
    delete_account(email)
    CACHE.pop(email, None)
    return jsonify({"ok": True})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/search")
def api_search():
    if not connected_accounts():
        return jsonify({"error": "No Google accounts connected."}), 401

    q = (request.args.get("q") or "").strip()
    platform = (request.args.get("platform") or "all").lower()
    if not q:
        return jsonify({"results": [], "errors": []})

    indexed, errors = get_index()
    results = []
    seen = set()

    for item in indexed:
        if platform == "analytics" and item["platform"] not in ["Google Analytics", "Google Tag Manager"]:
            continue
        if platform == "gtm" and item["platform"] != "Google Tag Manager":
            continue
        if platform == "gmb" and item["platform"] != "Google Business Profile":
            continue
        if platform == "gsc" and item["platform"] != "Search Console":
            continue

        haystack = " ".join([
            item.get("name", ""), item.get("account_name", ""), item.get("account_id", ""),
            item.get("resource_id", ""), item.get("search_extra", ""), item.get("google_login", "")
        ]).lower()
        if q.lower() in haystack:
            key = (item.get("platform"), item.get("account_id"), item.get("resource_id"), item.get("google_login"))
            if key not in seen:
                seen.add(key)
                results.append(item)

    results.sort(key=lambda x: (x.get("name", "").lower(), x.get("google_login", "")))
    return jsonify({"results": results[:200], "errors": errors})


@app.route("/api/gtm/inspect", methods=["POST"])
def api_gtm_inspect():
    data = request.json or {}
    account_id = data.get("account_id", "").strip()
    container_id = data.get("container_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()

    if not account_id or not container_id or not google_login:
        return jsonify({"error": "Missing GTM Account ID, Container ID, or Login Email."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        ws_url = f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{account_id}/containers/{container_id}/workspaces"
        workspaces = google_get(access_token, ws_url).get("workspace", [])
        if not workspaces:
            return jsonify({"error": "No active workspaces found in container."}), 404
            
        ws_path = workspaces[0]["path"]

        tags = google_get(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/tags").get("tag", [])
        triggers = google_get(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/triggers").get("trigger", [])
        variables = google_get(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/variables").get("variable", [])

        tag_list = [{"name": t.get("name"), "type": t.get("type"), "paused": t.get("paused", False)} for t in tags]
        trigger_list = [{"name": tr.get("name"), "type": tr.get("type")} for tr in triggers]
        var_list = [{"name": v.get("name"), "type": v.get("type")} for v in variables]

        return jsonify({
            "account_id": account_id,
            "container_id": container_id,
            "tags": tag_list,
            "triggers": trigger_list,
            "variables": var_list
        })
    except Exception as exc:
        logger.warning("Failed GTM workspace inspection: %s", exc)
        return jsonify({"error": f"GTM Inspection failed: {exc}"}), 500


@app.route("/api/gtm/generate-events", methods=["POST"])
def gtm_generate_events():
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        suggested_events = []

        for idx, form in enumerate(soup.find_all("form")):
            form_id = form.get("id") or form.get("name") or f"form_{idx+1}"
            suggested_events.append({
                "event_name": "generate_lead",
                "tag_name": f"GA4 Event - Form Submit ({form_id})",
                "trigger_type": "FORM_SUBMISSION",
                "selector": f"form#{form_id}" if form.get("id") else "form",
                "description": f"Fires when the lead submission form ({form_id}) is completed."
            })

        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            text = link.text.strip()[:30] or "Link"
            if href.startswith("tel:"):
                suggested_events.append({
                    "event_name": "click_to_call",
                    "tag_name": f"GA4 Event - Call ({text})",
                    "trigger_type": "LINK_CLICK",
                    "selector": f'a[href^="tel:"]',
                    "description": f"Fires when a user clicks the phone number link: {href}"
                })
            elif href.startswith("mailto:"):
                suggested_events.append({
                    "event_name": "click_to_email",
                    "tag_name": f"GA4 Event - Email ({text})",
                    "trigger_type": "LINK_CLICK",
                    "selector": f'a[href^="mailto:"]',
                    "description": f"Fires when a user clicks the email link: {href}"
                })

        return jsonify({"url": url, "suggested_events": suggested_events[:10]})
    except Exception as exc:
        logger.warning("Failed scraping site for GTM events: %s", exc)
        return jsonify({"error": f"Failed to analyze web page: {exc}"}), 500


@app.route("/api/gtm/deploy-event", methods=["POST"])
def gtm_deploy_event():
    data = request.json or {}
    account_id = data.get("account_id", "").strip()
    container_id = data.get("container_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()
    event_payload = data.get("event", {})

    if not account_id or not container_id or not google_login or not event_payload:
        return jsonify({"error": "Missing GTM credentials or Event payload."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        ws_url = f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{account_id}/containers/{container_id}/workspaces"
        workspaces = google_get(access_token, ws_url).get("workspace", [])
        if not workspaces:
            return jsonify({"error": "No active workspaces found in container."}), 404
        ws_path = workspaces[0]["path"]

        trigger_body = {
            "name": f"Trigger - {event_payload.get('tag_name')}",
            "type": "linkClick" if event_payload.get("trigger_type") == "LINK_CLICK" else "formSubmission"
        }
        created_trigger = google_post(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/triggers", trigger_body)

        tag_body = {
            "name": event_payload.get("tag_name"),
            "type": "gaawe",
            "firingTriggerId": [created_trigger["triggerId"]],
            "parameter": [
                {"type": "TEMPLATE", "key": "eventName", "value": event_payload.get("event_name")}
            ]
        }
        created_tag = google_post(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/tags", tag_body)

        now = int(time.time())
        with _db() as conn:
            conn.execute(
                """
                INSERT INTO gtm_change_logs (google_login, account_id, container_id, action_type, tag_name, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    google_login,
                    account_id,
                    container_id,
                    "TAG_DEPLOYED",
                    event_payload.get("tag_name"),
                    json.dumps({
                        "event_name": event_payload.get("event_name"),
                        "trigger_type": event_payload.get("trigger_type"),
                        "created_tag_id": created_tag.get("tagId"),
                        "created_trigger_id": created_trigger.get("triggerId")
                    }),
                    now
                )
            )
            conn.commit()

            return jsonify({"ok": True, "created_tag_id": created_tag["tagId"], "created_trigger_id": created_trigger["triggerId"]})
    except Exception as exc:
        logger.warning("Failed deploying tag to GTM: %s", exc)
        return jsonify({"error": f"GTM Tag deployment failed: {exc}"}), 500


@app.route("/api/gtm/deploy-pixel", methods=["POST"])
def gtm_deploy_pixel():
    data = request.json or {}
    account_id = data.get("account_id", "").strip()
    container_id = data.get("container_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()
    tag_name = data.get("tag_name", "").strip()
    pixel_code = data.get("pixel_code", "").strip()

    if not account_id or not container_id or not google_login or not tag_name or not pixel_code:
        return jsonify({"error": "Account ID, Container ID, Login Email, Tag Name, and Pixel Code are required."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        ws_url = f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{account_id}/containers/{container_id}/workspaces"
        workspaces = google_get(access_token, ws_url).get("workspace", [])
        if not workspaces:
            return jsonify({"error": "No active workspaces found in container."}), 404
        ws_path = workspaces[0]["path"]

        triggers = google_get(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/triggers").get("trigger", [])
        all_pages_trigger = next((tr for tr in triggers if tr.get("type") == "pageview"), None)
        
        trigger_id_list = [all_pages_trigger["triggerId"]] if all_pages_trigger else []

        tag_body = {
            "name": tag_name,
            "type": "html",
            "firingTriggerId": trigger_id_list,
            "parameter": [
                {"type": "TEMPLATE", "key": "html", "value": pixel_code},
                {"type": "BOOLEAN", "key": "supportDocumentWrite", "value": "false"}
            ]
        }
        
        created_tag = google_post(access_token, f"https://tagmanager.googleapis.com/tagmanager/v2/{ws_path}/tags", tag_body)

        now = int(time.time())
        with _db() as conn:
            conn.execute(
                """
                INSERT INTO gtm_change_logs (google_login, account_id, container_id, action_type, tag_name, details, created_at)
                VALUES (?, ?, ?, 'PIXEL_DEPLOYED', ?, ?, ?)
                """,
                (
                    google_login,
                    account_id,
                    container_id,
                    tag_name,
                    json.dumps({"created_tag_id": created_tag.get("tagId"), "code_length": len(pixel_code)}),
                    now
                )
            )
            conn.commit()

            return jsonify({"ok": True, "created_tag_id": created_tag["tagId"], "message": f"Successfully deployed custom pixel '{tag_name}' to GTM container {container_id}."})
    except Exception as exc:
        logger.warning("Failed deploying custom pixel to GTM: %s", exc)
        return jsonify({"error": f"Pixel deployment failed: {exc}"}), 500


@app.route("/api/gsc/bulk-add", methods=["POST"])
def api_gsc_bulk_add():
    data = request.json or {}
    google_login = data.get("google_login", "").strip().lower()
    entries = data.get("entries", [])

    if not google_login or not entries:
        return jsonify({"error": "Missing login email or domain list."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    results = []
    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        for entry in entries:
            site_url = entry.get("site_url", "").strip()
            sitemap_url = entry.get("sitemap_url", "").strip()
            
            if not site_url.startswith("http") and not site_url.startswith("sc-domain:"):
                site_url = f"https://{site_url.strip('/')}/"

            encoded_site = urlencode({'': site_url})[1:]
            url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}"
            
            try:
                google_put(access_token, url)
                status = "Added Property"
                
                if sitemap_url:
                    encoded_sitemap = urlencode({'': sitemap_url})[1:]
                    sm_url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/sitemaps/{encoded_sitemap}"
                    google_put(access_token, sm_url)
                    status += " & Submitted Sitemap"
                results.append({"site_url": site_url, "status": "SUCCESS", "details": status})
            except Exception as e:
                results.append({"site_url": site_url, "status": "FAILED", "details": str(e)})

        return jsonify({"ok": True, "results": results})
    except Exception as exc:
        return jsonify({"error": f"Bulk Search Console operation failed: {exc}"}), 500


@app.route("/api/ga4/anomalies", methods=["POST"])
def api_ga4_anomalies():
    data = request.json or {}
    property_id = data.get("property_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()

    if not property_id or not google_login:
        return jsonify({"error": "Missing Property ID or Login Email."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
        
        req_body = {
            "dateRanges": [
                {"startDate": "7daysAgo", "endDate": "yesterday"},
                {"startDate": "14daysAgo", "endDate": "8daysAgo"}
            ],
            "metrics": [{"name": "sessions"}, {"name": "keyEvents"}]
        }
        report = google_post(access_token, url, req_body)
        rows = report.get("rows", [])
        
        if not rows:
            return jsonify({"anomalies": []})

        recent_sess = int(rows[0]["metricValues"][0]["value"])
        prior_sess = int(rows[0]["metricValues"][1]["value"]) if len(rows[0]["metricValues"]) > 1 else 0

        anomalies = []
        if prior_sess > 0:
            diff_pct = ((recent_sess - prior_sess) / prior_sess) * 100
            if abs(diff_pct) >= 20.0:
                flag_type = "SPIKE" if diff_pct > 0 else "DROP"
                anomalies.append({
                    "metric": "Sessions",
                    "type": flag_type,
                    "change_pct": round(diff_pct, 1),
                    "message": f"Traffic {flag_type.lower()} of {diff_pct:+.1f}% detected over the last 7 days ({recent_sess:,} vs {prior_sess:,} sessions)."
                })

        return jsonify({"anomalies": anomalies})
    except Exception as exc:
        return jsonify({"error": f"Anomaly detection failed: {exc}"}), 500


# What counts as an AI engine sending traffic. One definition: the SEO client
# page and the Webmaster dashboard both filter on this, and if they each kept
# their own copy the two pages would quietly disagree about the same client.
AI_SOURCE_REGEX = (r"chatgpt|chat\.openai|openai\.com|perplexity|gemini\.google|"
                   r"bard\.google|copilot|edgeservices|claude\.ai|anthropic|"
                   r"deepseek|you\.com|poe\.com|meta\.ai|mistral|grok|x\.ai")

# Organic is the GA4 channel group, not a source pattern — same reason.
ORGANIC_CHANNEL = "Organic Search"


_SNAP_METRICS = [{"name": "totalUsers"}, {"name": "sessions"},
                 {"name": "engagementRate"}, {"name": "eventCount"},
                 {"name": "averageSessionDuration"}]


def _snap_vals(row):
    v = row.get("metricValues", [])

    def g(i, cast=float):
        try:
            return cast(v[i]["value"])
        except (IndexError, KeyError, TypeError, ValueError):
            return 0

    return {"users": int(g(0)), "sessions": int(g(1)),
            "engagement_rate": round(g(2) * 100, 1),
            "events": int(g(3)), "avg_session_seconds": round(g(4))}


_SNAP_ZERO = {"users": 0, "sessions": 0, "engagement_rate": 0,
              "events": 0, "avg_session_seconds": 0}


def _range_index(row, dim_count):
    """Which date range this row belongs to.

    GA4 appends the range as an extra dimension value when more than one is
    asked for. Reading it by position breaks the moment a range returns no
    rows, so the date_range_N tag is what decides and position is the
    fallback."""
    dvals = [d.get("value", "") for d in row.get("dimensionValues", [])]
    for val in dvals:
        if str(val).startswith("date_range_"):
            try:
                return int(str(val).rsplit("_", 1)[1]), dvals[:dim_count]
            except ValueError:
                break
    return 0, dvals[:dim_count]


def _change(now, was):
    """Percent change, or None when there is nothing to measure against.

    Not 100%: a first month is not growth of one hundred per cent, and
    reporting it that way puts a client with three sessions at the top of
    anything sorted by improvement."""
    out = {}
    for key in ("users", "sessions", "engagement_rate", "events",
                "avg_session_seconds"):
        before = was.get(key) or 0
        after = now.get(key) or 0
        out[key] = round((after - before) / before * 100, 1) if before else None
    return out


def _snapshot_ranges(data):
    """The two periods a snapshot compares, from the request or by default.

    The default is month to date against the same days of last month, not
    against all of last month. Comparing three days to a full month reports a
    collapse that has not happened."""
    import datetime as _dt

    def clean(v):
        try:
            return _dt.date.fromisoformat(str(v or "")[:10]).isoformat()
        except ValueError:
            return None

    start, end = clean(data.get("start")), clean(data.get("end"))
    cstart, cend = clean(data.get("compare_start")), clean(data.get("compare_end"))

    if start and end:
        if end < start:
            start, end = end, start
        if cstart and cend:
            if cend < cstart:
                cstart, cend = cend, cstart
        else:
            # No comparison given: take the period immediately before, of the
            # same length, so the two are the same size.
            s = _dt.date.fromisoformat(start)
            e = _dt.date.fromisoformat(end)
            span = (e - s).days
            cend = (s - _dt.timedelta(days=1)).isoformat()
            cstart = (s - _dt.timedelta(days=span + 1)).isoformat()
        return ([{"startDate": start, "endDate": end, "name": "current"},
                 {"startDate": cstart, "endDate": cend, "name": "previous"}],
                "custom")

    mtd, same, _full = _month_ranges()
    return ([{"startDate": mtd["startDate"], "endDate": mtd["endDate"], "name": "current"},
             {"startDate": same["startDate"], "endDate": same["endDate"], "name": "previous"}],
            "mtd")


@app.route("/api/ga4/seo-snapshot", methods=["POST"])
def api_ga4_seo_snapshot():
    """Organic and AI-engine traffic for one property, over two periods.

    Used by the Hub's SEO client page. It used to answer for a single period —
    month to date — which meant the page could show numbers but never say
    whether they were good ones. Both periods come back with the change
    already worked out, so the page and anything else that reads this cannot
    arrive at different percentages from the same figures.

    Defaults to month to date against the same days of last month. Pass
    start/end (and optionally compare_start/compare_end) for any other pair;
    without an explicit comparison the period immediately before, of the same
    length, is used.
    """
    data = request.json or {}
    property_id = str(data.get("property_id", "")).strip()
    google_login = str(data.get("google_login", "")).strip().lower()
    if not property_id or not google_login:
        return jsonify({"error": "Missing Property ID or Login Email."}), 400
    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    ranges, mode = _snapshot_ranges(data)

    organic_req = {
        "dateRanges": ranges,
        "metrics": _SNAP_METRICS,
        "dimensionFilter": {"filter": {
            "fieldName": "sessionDefaultChannelGroup",
            "stringFilter": {"matchType": "EXACT", "value": ORGANIC_CHANNEL}}},
    }
    ai_req = {
        "dateRanges": ranges,
        "dimensions": [{"name": "sessionSource"}],
        "metrics": _SNAP_METRICS,
        "dimensionFilter": {"filter": {
            "fieldName": "sessionSource",
            "stringFilter": {"matchType": "PARTIAL_REGEXP",
                             "value": AI_SOURCE_REGEX, "caseSensitive": False}}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 25,
    }

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
    except ReauthRequired as exc:
        return jsonify({"error": str(exc), "needs_reauth": True,
                        "reconnect_url": "/google/login"}), 401

    url = ("https://analyticsdata.googleapis.com/v1beta/properties/"
           f"{property_id}:batchRunReports")
    try:
        got = google_post(access_token, url, {"requests": [organic_req, ai_req]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"GA4 snapshot failed: {exc}"}), 502

    reports = got.get("reports") or []

    # ---- organic: one figure per period ---------------------------------
    organic, organic_prev = dict(_SNAP_ZERO), dict(_SNAP_ZERO)
    for row in (reports[0] if reports else {}).get("rows") or []:
        which, _ = _range_index(row, 0)
        if which == 0:
            organic = _snap_vals(row)
        elif which == 1:
            organic_prev = _snap_vals(row)

    # ---- AI: per source, per period --------------------------------------
    by_source: dict = {}
    for row in (reports[1] if len(reports) > 1 else {}).get("rows") or []:
        which, dims = _range_index(row, 1)
        src = (dims[0] if dims else "") or "(not set)"
        slot = by_source.setdefault(src, {"source": src, "cur": dict(_SNAP_ZERO),
                                          "prev": dict(_SNAP_ZERO)})
        slot["cur" if which == 0 else "prev"] = _snap_vals(row)

    def totalled(pick):
        """Sum sessions/users/events; average the rates by session weight.

        An unweighted mean of engagement rates says a source with two
        sessions counts as much as one with two thousand."""
        tot = {"users": 0, "sessions": 0, "events": 0,
               "engagement_rate": 0.0, "avg_session_seconds": 0.0}
        for slot in by_source.values():
            m = slot[pick]
            tot["users"] += m["users"]
            tot["sessions"] += m["sessions"]
            tot["events"] += m["events"]
            tot["engagement_rate"] += m["engagement_rate"] * m["sessions"]
            tot["avg_session_seconds"] += m["avg_session_seconds"] * m["sessions"]
        if tot["sessions"]:
            tot["engagement_rate"] = round(tot["engagement_rate"] / tot["sessions"], 1)
            tot["avg_session_seconds"] = round(tot["avg_session_seconds"] / tot["sessions"])
        else:
            tot["engagement_rate"] = 0
            tot["avg_session_seconds"] = 0
        return tot

    ai_total, ai_total_prev = totalled("cur"), totalled("prev")

    ai_sources = []
    for slot in sorted(by_source.values(),
                       key=lambda s: -s["cur"]["sessions"]):
        item = dict(slot["cur"])
        item["source"] = slot["source"]
        item["previous"] = slot["prev"]
        item["change"] = _change(slot["cur"], slot["prev"])
        ai_sources.append(item)

    return jsonify({
        "mode": mode,
        "period": {"start": ranges[0]["startDate"], "end": ranges[0]["endDate"]},
        "compare": {"start": ranges[1]["startDate"], "end": ranges[1]["endDate"]},
        # kept so an older caller still finds the field it expects
        "month_start": ranges[0]["startDate"],
        "organic": organic,
        "organic_previous": organic_prev,
        "organic_change": _change(organic, organic_prev),
        "ai_sources": ai_sources,
        "ai_total": ai_total,
        "ai_total_previous": ai_total_prev,
        "ai_total_change": _change(ai_total, ai_total_prev),
    })


def _month_ranges(today=None):
    """Month to date, the same days of last month, and all of last month.

    Three ranges rather than two because "this month so far vs all of last
    month" is not a comparison — on the 3rd it reports that every client
    collapsed. The dashboard shows the full previous month because that is the
    number people put in a report, and works the arrow off the same days of
    it, which is the number that actually means something.
    """
    import datetime as _dt
    today = today or _dt.date.today()
    this_first = today.replace(day=1)
    prev_last = this_first - _dt.timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    # Feb has no 30th: a month-end day clamps to the shorter month's last day.
    same_end = prev_first + _dt.timedelta(days=min(today.day, prev_last.day) - 1)
    return [
        {"startDate": this_first.isoformat(), "endDate": today.isoformat()},
        {"startDate": prev_first.isoformat(), "endDate": same_end.isoformat()},
        {"startDate": prev_first.isoformat(), "endDate": prev_last.isoformat()},
    ]


_WM_METRICS = [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "keyEvents"}]

# One row of the dashboard is worth two GA4 calls at most, so results are held
# briefly: a page of 40 clients reloaded twice in a minute should not be 80
# round trips. Per worker, which means a miss costs one extra fetch and never
# a wrong number — the key carries the day, so it cannot serve yesterday.
_WM_CACHE: dict = {}
_WM_TTL = 900


def _wm_cache_get(key):
    import time
    hit = _WM_CACHE.get(key)
    if hit and time.time() - hit[0] < _WM_TTL:
        return hit[1]
    return None


def _wm_cache_put(key, value):
    import time
    if len(_WM_CACHE) > 500:
        _WM_CACHE.clear()
    _WM_CACHE[key] = (time.time(), value)


def _wm_series(report, ranges):
    """Fold GA4's one-row-per-date-range answer into three named buckets.

    With several date ranges and no dimensions, GA4 returns a row per range
    tagged date_range_N. Reading them positionally works right up until a
    range returns nothing and the rows shift, so the tag is what is trusted
    and position is only the fallback.
    """
    names = ("mtd", "same_last_month", "last_month")
    out = {n: {"sessions": 0, "users": 0, "key_events": 0} for n in names}
    for i, row in enumerate(report.get("rows") or []):
        which = i
        for dv in row.get("dimensionValues") or []:
            val = str(dv.get("value") or "")
            if val.startswith("date_range_"):
                try:
                    which = int(val.rsplit("_", 1)[1])
                except ValueError:
                    pass
                break
        if which >= len(names):
            continue
        vals = row.get("metricValues") or []

        def num(idx):
            try:
                return int(float(vals[idx]["value"]))
            except (IndexError, KeyError, TypeError, ValueError):
                return 0

        out[names[which]] = {"sessions": num(0), "users": num(1),
                             "key_events": num(2)}
    return out


@app.route("/api/ga4/webmaster-row", methods=["POST"])
def api_ga4_webmaster_row():
    """Organic and AI-engine traffic for one property, three periods each.

    Powers one row of the Hub's Webmaster Report Dashboard. Both filters come
    from the constants above, so this and the SEO client page cannot drift on
    what "organic" or "an AI engine" means.
    """
    data = request.json or {}
    property_id = str(data.get("property_id", "")).strip()
    google_login = str(data.get("google_login", "")).strip().lower()
    if not property_id or not google_login:
        return jsonify({"error": "Missing Property ID or Login Email."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    ranges = _month_ranges()
    cache_key = (property_id, google_login, ranges[0]["endDate"])
    hit = _wm_cache_get(cache_key)
    if hit is not None:
        return jsonify(dict(hit, cached=True))

    organic_req = {
        "dateRanges": ranges,
        "metrics": _WM_METRICS,
        "dimensionFilter": {"filter": {
            "fieldName": "sessionDefaultChannelGroup",
            "stringFilter": {"matchType": "EXACT", "value": ORGANIC_CHANNEL}}},
    }
    ai_req = {
        "dateRanges": ranges,
        "metrics": _WM_METRICS,
        "dimensionFilter": {"filter": {
            "fieldName": "sessionSource",
            "stringFilter": {"matchType": "PARTIAL_REGEXP",
                             "value": AI_SOURCE_REGEX, "caseSensitive": False}}},
    }

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
    except ReauthRequired as exc:
        return jsonify({"error": str(exc), "needs_reauth": True,
                        "reconnect_url": "/google/login"}), 401

    url = ("https://analyticsdata.googleapis.com/v1beta/properties/"
           f"{property_id}:batchRunReports")
    try:
        # One HTTP call for both reports. A dashboard of forty clients is forty
        # round trips this way instead of eighty.
        got = google_post(access_token, url, {"requests": [organic_req, ai_req]})
        reports = got.get("reports") or []
        payload = {
            "property_id": property_id,
            "periods": {"mtd": ranges[0], "same_last_month": ranges[1],
                        "last_month": ranges[2]},
            "organic": _wm_series(reports[0] if len(reports) > 0 else {}, ranges),
            "ai": _wm_series(reports[1] if len(reports) > 1 else {}, ranges),
        }
    except Exception as exc:  # noqa: BLE001
        # Say which property failed. "GA4 request failed" against a table of
        # forty rows is not something anyone can act on.
        return jsonify({"error": f"GA4 request failed for property "
                                 f"{property_id}: {exc}"}), 502

    _wm_cache_put(cache_key, payload)
    return jsonify(payload)


@app.route("/api/ga4/monthly-summary", methods=["POST"])
def api_ga4_monthly_summary():
    """Last full month vs the month before: totals + top source/mediums.
    Powers the Client 360 traffic summary card."""
    data = request.json or {}
    property_id = str(data.get("property_id", "")).strip()
    google_login = str(data.get("google_login", "")).strip().lower()
    if not property_id or not google_login:
        return jsonify({"error": "Missing Property ID or Login Email."}), 400
    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    from datetime import date, timedelta
    first_this = date.today().replace(day=1)
    last_end = first_this - timedelta(days=1)          # end of last month
    last_start = last_end.replace(day=1)
    prev_end = last_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)

    METRICS = ["sessions", "engagedSessions", "activeUsers",
               "userEngagementDuration", "eventCount", "keyEvents"]

    def parse_metrics(row):
        out = {}
        for i, name in enumerate(METRICS):
            try:
                out[name] = float(row.get("metricValues", [])[i].get("value") or 0)
            except (IndexError, ValueError, AttributeError):
                out[name] = 0.0
        return out

    def derive(m):
        """The five displayed metrics from the raw pull."""
        users = m["activeUsers"] or 0
        return {
            "sessions": int(m["sessions"]),
            "engaged_sessions": int(m["engagedSessions"]),
            "avg_engagement_seconds": round(m["userEngagementDuration"] / users) if users else 0,
            "events": int(m["eventCount"]),
            "key_events": int(m["keyEvents"]),
        }

    def pct(cur, prev):
        if not prev:
            return None if not cur else 100.0
        return round((cur - prev) / prev * 100, 1)

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
        ranges = [{"startDate": last_start.isoformat(), "endDate": last_end.isoformat()},
                  {"startDate": prev_start.isoformat(), "endDate": prev_end.isoformat()}]
        mreq = [{"name": n} for n in METRICS]

        # ---- totals (two date ranges -> implicit dateRange dimension) ----
        totals_rows = google_post(access_token, url, {
            "dateRanges": ranges, "metrics": mreq}).get("rows", [])
        cur_raw = prev_raw = {n: 0.0 for n in METRICS}
        for row in totals_rows:
            tag = "".join(d.get("value", "") for d in row.get("dimensionValues", []))
            if "date_range_1" in tag:
                prev_raw = parse_metrics(row)
            else:
                cur_raw = parse_metrics(row)
        cur, prev = derive(cur_raw), derive(prev_raw)
        deltas = {k: pct(cur[k], prev[k]) for k in cur}

        # ---- breakdown by source/medium, both ranges merged ----
        br_rows = google_post(access_token, url, {
            "dateRanges": ranges,
            "dimensions": [{"name": "sessionSourceMedium"}],
            "metrics": mreq,
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            "limit": 60,
        }).get("rows", [])
        by_src = {}
        for row in br_rows:
            dims = row.get("dimensionValues", [])
            src = dims[0].get("value", "") if dims else ""
            tag = "".join(d.get("value", "") for d in dims[1:])
            rec = by_src.setdefault(src, {"cur": None, "prev": None})
            if "date_range_1" in tag:
                rec["prev"] = parse_metrics(row)
            else:
                rec["cur"] = parse_metrics(row)
        breakdown = []
        for src, rec in by_src.items():
            c = derive(rec["cur"] or {n: 0.0 for n in METRICS})
            p = derive(rec["prev"] or {n: 0.0 for n in METRICS})
            breakdown.append({"source_medium": src, "current": c, "previous": p,
                              "delta_sessions": pct(c["sessions"], p["sessions"])})
        breakdown.sort(key=lambda b: -b["current"]["sessions"])
        breakdown = breakdown[:10]

        # ---- positive executive summary ----
        label_cur = last_start.strftime("%B %Y")
        label_prev = prev_start.strftime("%B %Y")
        names = {"sessions": "sessions", "engaged_sessions": "engaged sessions",
                 "avg_engagement_seconds": "average engagement time",
                 "events": "total events", "key_events": "key events"}
        ups = [(k, deltas[k]) for k in names if deltas.get(k) is not None and deltas[k] > 0]
        downs = [(k, deltas[k]) for k in names if deltas.get(k) is not None and deltas[k] < 0]
        ups.sort(key=lambda t: -t[1])
        parts = []
        if ups:
            lead = ", ".join(f"{names[k]} up {v:+.1f}%".replace("+", "") for k, v in ups[:3])
            parts.append(f"{label_cur} showed solid momentum: {lead} versus {label_prev}.")
        else:
            parts.append(f"{label_cur} held steady against {label_prev}, keeping a consistent "
                         "baseline of visitors engaging with the site.")
        if cur["key_events"]:
            parts.append(f"The site drove {cur['key_events']:,} key events from "
                         f"{cur['sessions']:,} sessions.")
        elif cur["sessions"]:
            parts.append(f"The site welcomed {cur['sessions']:,} sessions with "
                         f"{cur['engaged_sessions']:,} of them actively engaged.")
        if downs:
            soft = ", ".join(names[k] for k, _ in downs[:2])
            parts.append(f"Continued optimization is focused on {soft} to build on this momentum.")
        summary = " ".join(parts)

        return jsonify({"period": {"current": label_cur, "previous": label_prev},
                        "summary": summary, "current": cur, "previous": prev,
                        "deltas": deltas, "breakdown": breakdown})
    except ReauthRequired as exc:
        # Not a server fault: the token is dead and only a fresh sign-in fixes
        # it. 401 + a reconnect link so the page can offer the one useful
        # action, instead of a 500 that just reads as "broken".
        return jsonify({"error": str(exc), "needs_reauth": True,
                        "reconnect_url": "/google/login",
                        "account": exc.email}), 401
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"GA4 monthly summary failed: {exc}"}), 500


@app.route("/api/ga4/ask", methods=["POST"])
def api_ga4_ask():
    """A plain-English question about a property, answered from GA4.

    This used to match keywords — "device" meant group by device, anything
    else meant source/medium, always the last 30 days. "How did conversions do
    in July versus June?" and "which cities converted best last quarter?" both
    returned the same 30-day source/medium table, which is worse than refusing:
    it answers confidently with the wrong report.

    Now the model plans the report and hub.analytics_ask validates the plan
    field by field before anything reaches Google, so a name it invented
    becomes "I don't know how to ask that" rather than a 400 or, worse, a
    plausible table of the wrong thing. The property id comes from the caller
    and never from the question.
    """
    from hub import analytics_ask

    data = request.json or {}
    property_id = str(data.get("property_id", "")).strip()
    google_login = str(data.get("google_login", "")).strip().lower()
    question = str(data.get("question", "")).strip()
    history = data.get("history") if isinstance(data.get("history"), list) else []

    if not property_id or not google_login or not question:
        return jsonify({"error": "Missing Property ID, Login Email, or Question."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        planned = analytics_ask.plan(
            question, history=history,
            property_label=str(data.get("property_label") or ""))
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("analytics ask: planning failed: %s", exc)
        return jsonify({"error": "The analytics assistant is unavailable right "
                                 "now. Try again shortly."}), 503

    # A question with no defensible default gets asked back rather than
    # guessed at.
    if planned.get("clarify"):
        return jsonify({"clarify": planned["clarify"], "question": question})

    req = planned["request"]
    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        url = (f"https://analyticsdata.googleapis.com/v1beta/"
               f"properties/{property_id}:runReport")
        report = google_post(access_token, url, req)
    except ReauthRequired as exc:
        return jsonify({"error": str(exc), "needs_reauth": True,
                        "reconnect_url": "/google/login",
                        "account": exc.email}), 401
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("analytics ask: GA4 call failed: %s", exc)
        return jsonify({"error": "Google refused that report. It may be a "
                                 "measure this property doesn't collect."}), 502

    shaped = analytics_ask.shape(report, req)
    answer = analytics_ask.narrate(question, planned["title"], shaped)

    try:
        from hub import audit as _audit_mod
        _audit_mod.log("google_finder", "analytics_ask",
                       question=question[:160], rows=shaped["row_count"])
    except Exception:                                   # noqa: BLE001
        pass

    return jsonify({
        "title": planned["title"],
        "explain": planned["explain"],
        "answer": answer,
        "result": shaped,
        # Returned so the page can show what was actually run. A number nobody
        # can trace back to a query is a number nobody trusts.
        "query": {"metrics": [m["name"] for m in req["metrics"]],
                  "dimensions": [d["name"] for d in req.get("dimensions", [])],
                  "dateRanges": req["dateRanges"], "limit": req["limit"]},
    })


@app.route("/api/ga4/ask/catalogue")
def api_ga4_ask_catalogue():
    """What can be asked for — used by the page for its examples."""
    from hub import analytics_ask
    return jsonify(analytics_ask.catalogue())


@app.route("/api/ga4/channels", methods=["POST"])
def api_ga4_channels():
    data = request.json or {}
    property_id = data.get("property_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()

    if not property_id or not google_login:
        return jsonify({"channels": []})

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"channels": []})

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
        req_body = {
            "dateRanges": [{"startDate": "30daysAgo", "endDate": "yesterday"}],
            "dimensions": [{"name": "sessionSourceMedium"}],
            "metrics": [{"name": "sessions"}],
            "limit": 50
        }
        report = google_post(access_token, url, req_body)
        channels = [row["dimensionValues"][0]["value"] for row in report.get("rows", [])]
        return jsonify({"channels": sorted(channels)})
    except Exception as exc:
        logger.warning("Failed fetching GA4 channels for property %s: %s", property_id, exc)
        return jsonify({"channels": []})


@app.route("/api/ga4/compare", methods=["POST"])
def api_ga4_compare():
    data = request.json or {}
    property_id = data.get("property_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()
    period_type = data.get("period_type", "previous_period")
    scope_type = data.get("scope_type", "site")
    page_path = sanitize_regex(data.get("page_path", "").strip())
    source_medium = data.get("source_medium", "").strip()
    tone = data.get("tone", "positive")
    preset = data.get("preset", "executive")
    limit = clamp_int(data.get("limit"), 15, 1, 100)

    p1_start_str = data.get("p1_start")
    p1_end_str = data.get("p1_end")

    if not property_id or not google_login:
        return jsonify({"error": "Missing GA4 Property ID or Google Login."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
    except Exception as exc:
        return jsonify({"error": f"Failed to authenticate {google_login}: {exc}"}), 401

    today = datetime.utcnow().date()
    if not p1_start_str or not p1_end_str:
        first_this_month = today.replace(day=1)
        p1_end_dt = first_this_month - timedelta(days=1)
        p1_start_dt = p1_end_dt.replace(day=1)
    else:
        p1_start_dt = datetime.strptime(p1_start_str, "%Y-%m-%d").date()
        p1_end_dt = datetime.strptime(p1_end_str, "%Y-%m-%d").date()

    num_days = (p1_end_dt - p1_start_dt).days + 1

    if period_type == "previous_period":
        p2_end_dt = p1_start_dt - timedelta(days=1)
        p2_start_dt = p2_end_dt - timedelta(days=num_days - 1)
        p2_start = p2_start_dt.strftime("%Y-%m-%d")
        p2_end = p2_end_dt.strftime("%Y-%m-%d")
        p2_label = f"Prior {num_days} Days ({p2_start} to {p2_end})"
    elif period_type == "previous_year":
        p2_start = p1_start_dt.replace(year=p1_start_dt.year - 1).strftime("%Y-%m-%d")
        p2_end = p1_end_dt.replace(year=p1_end_dt.year - 1).strftime("%Y-%m-%d")
        p2_label = f"Previous Year ({p2_start} to {p2_end})"
    else:
        p2_start = data.get("p2_start", "")
        p2_end = data.get("p2_end", "")
        p2_label = f"Custom Period ({p2_start} to {p2_end})"

    p1_start = p1_start_dt.strftime("%Y-%m-%d")
    p1_end = p1_end_dt.strftime("%Y-%m-%d")
    p1_label = f"Selected Period ({p1_start} to {p1_end})"

    dimension_name = "pagePath" if scope_type in ["page", "multiple"] else "sessionSourceMedium"
    expressions = []

    if scope_type == "page" and page_path:
        expressions.append({
            "filter": {
                "fieldName": "pagePath",
                "stringFilter": {"matchType": "EXACT", "value": page_path}
            }
        })
    elif scope_type == "multiple" and page_path:
        expressions.append({
            "filter": {
                "fieldName": "pagePath",
                "stringFilter": {"matchType": "PARTIAL_REGEXP", "value": page_path}
            }
        })

    if source_medium:
        expressions.append({
            "filter": {
                "fieldName": "sessionSourceMedium",
                "stringFilter": {"matchType": "CONTAINS", "value": source_medium}
            }
        })

    dimension_filter = None
    if len(expressions) == 1:
        dimension_filter = expressions[0]["filter"]
    elif len(expressions) > 1:
        dimension_filter = {"andGroup": {"expressions": expressions}}

    def query_ga4_range(start, end):
        req_body = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": dimension_name}],
            "metrics": [
                {"name": "sessions"},
                {"name": "activeUsers"},
                {"name": "engagedSessions"},
                {"name": "userEngagementDuration"},
                {"name": "eventCount"},
                {"name": "keyEvents"}
            ],
            "limit": limit
        }
        if dimension_filter:
            req_body["dimensionFilter"] = dimension_filter
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
        return google_post(access_token, url, req_body)

    try:
        rep1 = query_ga4_range(p1_start, p1_end)
        rep2 = query_ga4_range(p2_start, p2_end)
    except Exception as exc:
        return jsonify({"error": f"GA4 Data API call failed: {exc}"}), 500

    m1 = {"sessions": 0, "activeUsers": 0, "engagedSessions": 0, "userEngagementDuration": 0, "eventCount": 0, "keyEvents": 0}
    m2 = {"sessions": 0, "activeUsers": 0, "engagedSessions": 0, "userEngagementDuration": 0, "eventCount": 0, "keyEvents": 0}
    breakdown_map = {}

    for row in rep1.get("rows", []):
        dim_val = row["dimensionValues"][0]["value"]
        sess = int(row["metricValues"][0]["value"])
        users = int(row["metricValues"][1]["value"])
        eng_sess = int(row["metricValues"][2]["value"])
        eng_time = float(row["metricValues"][3]["value"])
        events = int(row["metricValues"][4]["value"])
        convs = int(row["metricValues"][5]["value"])

        m1["sessions"] += sess
        m1["activeUsers"] += users
        m1["engagedSessions"] += eng_sess
        m1["userEngagementDuration"] += eng_time
        m1["eventCount"] += events
        m1["keyEvents"] += convs

        if dim_val not in breakdown_map:
            breakdown_map[dim_val] = {
                "name": dim_val, 
                "p1_sessions": 0, "p2_sessions": 0,
                "p1_engaged": 0, "p2_engaged": 0,
                "p1_time": 0, "p2_time": 0,
                "p1_events": 0, "p2_events": 0,
                "p1_convs": 0, "p2_convs": 0
            }
        breakdown_map[dim_val]["p1_sessions"] += sess
        breakdown_map[dim_val]["p1_engaged"] += eng_sess
        breakdown_map[dim_val]["p1_time"] += eng_time
        breakdown_map[dim_val]["p1_events"] += events
        breakdown_map[dim_val]["p1_convs"] += convs

    for row in rep2.get("rows", []):
        dim_val = row["dimensionValues"][0]["value"]
        sess = int(row["metricValues"][0]["value"])
        users = int(row["metricValues"][1]["value"])
        eng_sess = int(row["metricValues"][2]["value"])
        eng_time = float(row["metricValues"][3]["value"])
        events = int(row["metricValues"][4]["value"])
        convs = int(row["metricValues"][5]["value"])

        m2["sessions"] += sess
        m2["activeUsers"] += users
        m2["engagedSessions"] += eng_sess
        m2["userEngagementDuration"] += eng_time
        m2["eventCount"] += events
        m2["keyEvents"] += convs

        if dim_val not in breakdown_map:
            breakdown_map[dim_val] = {
                "name": dim_val, 
                "p1_sessions": 0, "p2_sessions": 0,
                "p1_engaged": 0, "p2_engaged": 0,
                "p1_time": 0, "p2_time": 0,
                "p1_events": 0, "p2_events": 0,
                "p1_convs": 0, "p2_convs": 0
            }
        breakdown_map[dim_val]["p2_sessions"] += sess
        breakdown_map[dim_val]["p2_engaged"] += eng_sess
        breakdown_map[dim_val]["p2_time"] += eng_time
        breakdown_map[dim_val]["p2_events"] += events
        breakdown_map[dim_val]["p2_convs"] += convs

    breakdown_list = []
    for k, v in breakdown_map.items():
        v["session_diff"] = v["p1_sessions"] - v["p2_sessions"]
        v["p1_avg_time_str"] = f"{int(v['p1_time'] / v['p1_sessions'])}s" if v["p1_sessions"] > 0 else "0s"
        v["p2_avg_time_str"] = f"{int(v['p2_time'] / v['p2_sessions'])}s" if v["p2_sessions"] > 0 else "0s"
        breakdown_list.append(v)

    ai_result = generate_ga4_ai_analysis(p1_label, p2_label, m1, m2, breakdown_list, tone=tone, preset=preset)

    return jsonify({
        "property_id": property_id,
        "p1_label": p1_label,
        "p2_label": p2_label,
        "metrics_p1": m1,
        "metrics_p2": m2,
        "breakdown": sorted(breakdown_list, key=lambda x: abs(x["session_diff"]), reverse=True)[:limit],
        "ai_analysis": ai_result
    })


@app.route("/api/reports/save", methods=["POST"])
def save_report():
    data = request.json or {}
    customer_name = data.get("customer_name", "").strip()
    summary_title = data.get("summary_title", "").strip()
    property_id = data.get("property_id", "").strip()
    google_login = data.get("google_login", "").strip()
    report_data = data.get("report_data", {})

    if not customer_name or not summary_title or not report_data:
        return jsonify({"error": "Customer name, summary title, and report payload are required."}), 400

    now = int(time.time())
    with _db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO saved_reports (customer_name, summary_title, property_id, google_login, report_data, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (customer_name, summary_title, property_id, google_login, json.dumps(report_data), now)
        )
        report_id = cursor.lastrowid
        conn.commit()

        return jsonify({"ok": True, "report_id": report_id, "message": "Report successfully saved."})


@app.route("/api/reports/search", methods=["GET"])
def search_reports():
    q = (request.args.get("q") or "").strip().lower()
    with _db() as conn:
        if q:
            rows = conn.execute(
                """
                SELECT id, customer_name, summary_title, property_id, google_login, created_at 
                FROM saved_reports 
                WHERE LOWER(customer_name) LIKE ? OR LOWER(summary_title) LIKE ? OR LOWER(property_id) LIKE ?
                ORDER BY created_at DESC
                """,
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, customer_name, summary_title, property_id, google_login, created_at FROM saved_reports ORDER BY created_at DESC LIMIT 50"
            ).fetchall()

        reports = [dict(row) for row in rows]
        return jsonify({"reports": reports})


@app.route("/api/gtm/logs/search", methods=["GET"])
def search_gtm_logs():
    q = (request.args.get("q") or "").strip().lower()
    container_id = (request.args.get("container_id") or "").strip()

    with _db() as conn:
        if container_id and q:
            rows = conn.execute(
                """
                SELECT * FROM gtm_change_logs 
                WHERE container_id = ? AND (LOWER(tag_name) LIKE ? OR LOWER(google_login) LIKE ? OR LOWER(action_type) LIKE ?)
                ORDER BY created_at DESC LIMIT 100
                """,
                (container_id, f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        elif container_id:
            rows = conn.execute(
                "SELECT * FROM gtm_change_logs WHERE container_id = ? ORDER BY created_at DESC LIMIT 100",
                (container_id,)
            ).fetchall()
        elif q:
            rows = conn.execute(
                """
                SELECT * FROM gtm_change_logs 
                WHERE LOWER(tag_name) LIKE ? OR LOWER(google_login) LIKE ? OR LOWER(container_id) LIKE ? OR LOWER(action_type) LIKE ?
                ORDER BY created_at DESC LIMIT 100
                """,
                (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM gtm_change_logs ORDER BY created_at DESC LIMIT 100").fetchall()

        logs = []
        for row in rows:
            d = dict(row)
            try:
                d["details"] = json.loads(d["details"])
            except Exception:
                pass
            logs.append(d)

        return jsonify({"logs": logs})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if not connected_accounts():
        return jsonify({"error": "No Google accounts connected."}), 401
    items, errors = get_index(force=True)
    return jsonify({"ok": not errors, "count": len(items), "errors": errors})


@app.route("/health")
def health():
    checks = {
        "google_client_id_configured": bool(GOOGLE_CLIENT_ID),
        "google_client_secret_configured": bool(GOOGLE_CLIENT_SECRET),
        "token_encryption_key_configured": bool(TOKEN_ENCRYPTION_KEY),
        "token_db_path": TOKEN_DB_PATH,
        "connected_account_count": len(connected_accounts()),
    }
    checks["ok"] = all([
        checks["google_client_id_configured"],
        checks["google_client_secret_configured"],
        checks["token_encryption_key_configured"],
    ])
    return jsonify(checks), (200 if checks["ok"] else 500)


@app.route("/debug/accounts")
def debug_accounts():
    diagnostics = []
    try:
        accounts = connected_accounts()
        if not accounts:
            return jsonify({"connected_account_count": 0, "diagnostics": [], "status": "No connected accounts found."})

        for acc in accounts:
            email = acc.get("email", "unknown")
            info = {"email": email, "refresh_token_present": bool(acc.get("refresh_token"))}
            
            try:
                access_token = refresh_access_token(email, acc["refresh_token"])
                info["token_refresh_status"] = "SUCCESS"
            except Exception as exc:
                info["token_refresh_status"] = f"FAILED: {exc}"
                diagnostics.append(info)
                continue

            try:
                google_get(access_token, "https://analyticsadmin.googleapis.com/v1beta/accountSummaries", params={"pageSize": 1})
                info["ga4_api_status"] = "SUCCESS"
            except Exception as exc:
                info["ga4_api_status"] = f"FAILED: {exc}"

            try:
                google_get(access_token, "https://tagmanager.googleapis.com/tagmanager/v2/accounts")
                info["gtm_api_status"] = "SUCCESS"
            except Exception as exc:
                info["gtm_api_status"] = f"FAILED: {exc}"

            try:
                google_get(access_token, "https://www.googleapis.com/webmasters/v3/sites")
                info["gsc_api_status"] = "SUCCESS"
            except Exception as exc:
                info["gsc_api_status"] = f"FAILED: {exc}"

            diagnostics.append(info)

        return jsonify({"connected_account_count": len(accounts), "diagnostics": diagnostics})
    except Exception as exc:
        return jsonify({"connected_account_count": 0, "diagnostics": [], "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
