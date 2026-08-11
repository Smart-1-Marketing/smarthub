import json
import os
import secrets
import sqlite3
import time
import re
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from flask import Flask, redirect, render_template, request, session, url_for, jsonify, g
from flask_session import Session
from cryptography.fernet import Fernet, InvalidToken
from werkzeug.middleware.proxy_fix import ProxyFix

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


def get_db():
    if "db" not in g:
        db_dir = os.path.dirname(TOKEN_DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        g.db = sqlite3.connect(TOKEN_DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL;")
        init_db(g.db)
    return g.db


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


def connected_accounts():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT email, refresh_token_enc, status FROM google_accounts ORDER BY email"
        ).fetchall()
        f = _fernet()
        accounts = []
        for row in rows:
            try:
                token = f.decrypt(row["refresh_token_enc"].encode("utf-8")).decode("utf-8")
            except InvalidToken:
                continue
            accounts.append({"email": row["email"], "refresh_token": token, "status": row["status"]})
        return accounts
    except Exception:
        return []


def save_account(email, refresh_token):
    now = int(time.time())
    token_enc = _fernet().encrypt(refresh_token.encode("utf-8")).decode("utf-8")
    conn = get_db()
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
    conn = get_db()
    conn.execute("UPDATE google_accounts SET status='REAUTH_REQUIRED' WHERE email=?", (email.lower(),))
    conn.commit()


def delete_account(email):
    conn = get_db()
    conn.execute("DELETE FROM google_accounts WHERE email = ?", (email.lower(),))
    conn.commit()


def is_allowed(email):
    return not ALLOWED_EMAILS or email.lower() in ALLOWED_EMAILS


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
        r.raise_for_status()
        return r.json()["access_token"]
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code in (400, 401):
            mark_account_reauth(email)
            raise RuntimeError(f"Authentication token for {email} was revoked or expired. Re-login required.") from exc
        raise


def sanitize_regex(pattern):
    if not pattern:
        return ""
    try:
        re.compile(pattern)
        return pattern
    except re.error:
        return re.escape(pattern)


def google_get(access_token, url, params=None):
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=12,
    )
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
    r.raise_for_status()
    return r.text


def fetch_ga_items(access_token, google_login):
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
                        "search_extra": f"{property_name} {account_name} {property_id}",
                        "google_login": google_login,
                        "open_url": f"https://analytics.google.com/analytics/web/#/p{property_id}" if property_id else "",
                    })
            token = data.get("nextPageToken")
            if not token:
                break
    except Exception as exc:
        logger.warning("Failed fetching GA4 items for %s: %s", google_login, exc)
    return items


def fetch_gtm_items(access_token, google_login):
    items = []
    token = None
    accounts = []

    try:
        while True:
            params = {}
            if token:
                params["pageToken"] = token
            data = google_get(access_token, "https://tagmanager.googleapis.com/tagmanager/v2/accounts", params=params)
            accounts.extend(data.get("account", []))
            token = data.get("nextPageToken")
            if not token:
                break
    except Exception as exc:
        logger.warning("Failed fetching GTM accounts for %s: %s", google_login, exc)
        return items

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
                data = google_get(
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
                        "search_extra": f"{domains} {container_name} {public_id}",
                        "google_login": google_login,
                        "open_url": f"https://tagmanager.google.com/#/container/accounts/{account_id}/containers/{container_id}" if account_id and container_id else "",
                    })
                ctoken = data.get("nextPageToken")
            except Exception as exc:
                logger.warning("Skipping container for GTM account %s (%s): %s", account_id, google_login, exc)
                break

            if not ctoken:
                break

    return items


def fetch_gmb_items(access_token, google_login):
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
                        "search_extra": f"{website_url} {address_str} {claimed_status}".strip(),
                        "google_login": google_login,
                        "open_url": f"https://business.google.com/dashboard/l/{loc_id}" if loc_id else "https://business.google.com/",
                    })
            except Exception as loc_exc:
                logger.warning("Failed fetching locations for GMB account %s (%s): %s", account_id, google_login, loc_exc)

    except Exception as exc:
        logger.warning("Failed fetching GMB accounts for %s: %s", google_login, exc)

    return items


def fetch_gsc_items(access_token, google_login):
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
                "search_extra": f"{clean_domain} {permission}",
                "google_login": google_login,
                "open_url": f"https://search.google.com/search-console?resource_id={urlencode({'': site_url})[1:]}",
            })
    except Exception as exc:
        logger.warning("Failed fetching Search Console sites for %s: %s", google_login, exc)

    return items


def get_account_index(account, force=False):
    email = account["email"].lower()
    cached = CACHE.get(email, {"expires": 0, "items": []})
    now = time.time()
    if not force and cached["expires"] > now:
        return cached["items"]

    access_token = refresh_access_token(email, account["refresh_token"])
    items = (
        fetch_ga_items(access_token, email)
        + fetch_gtm_items(access_token, email)
        + fetch_gmb_items(access_token, email)
        + fetch_gsc_items(access_token, email)
    )
    CACHE[email] = {"expires": now + CACHE_SECONDS, "items": items}
    return items


def get_index(force=False):
    items = []
    errors = []
    for account in connected_accounts():
        try:
            items.extend(get_account_index(account, force=force))
        except Exception as exc:
            errors.append({"email": account.get("email", "unknown"), "error": str(exc)})
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
        conn = get_db()
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
        conn = get_db()
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


@app.route("/api/ga4/ask", methods=["POST"])
def api_ga4_ask():
    data = request.json or {}
    property_id = data.get("property_id", "").strip()
    google_login = data.get("google_login", "").strip().lower()
    question = data.get("question", "").strip().lower()

    if not property_id or not google_login or not question:
        return jsonify({"error": "Missing Property ID, Login Email, or Question."}), 400

    account = next((a for a in connected_accounts() if a["email"] == google_login), None)
    if not account:
        return jsonify({"error": f"Account {google_login} is not connected."}), 404

    try:
        access_token = refresh_access_token(google_login, account["refresh_token"])
        
        if "device" in question or "mobile" in question or "desktop" in question:
            dimension = "deviceCategory"
        elif "landing" in question or "page" in question or "url" in question:
            dimension = "pagePath"
        elif "city" in question or "location" in question or "geo" in question:
            dimension = "city"
        elif "country" in question:
            dimension = "country"
        else:
            dimension = "sessionSourceMedium"

        req_body = {
            "dateRanges": [{"startDate": "30daysAgo", "endDate": "yesterday"}],
            "dimensions": [{"name": dimension}],
            "metrics": [
                {"name": "sessions"},
                {"name": "activeUsers"},
                {"name": "keyEvents"}
            ],
            "limit": 10
        }

        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
        report = google_post(access_token, url, req_body)

        rows = report.get("rows", [])
        if not rows:
            return jsonify({"answer": "No traffic records were found for that query in the last 30 days."})

        results = []
        for r in rows:
            dim_val = r["dimensionValues"][0]["value"]
            sess = int(r["metricValues"][0]["value"])
            users = int(r["metricValues"][1]["value"])
            convs = int(r["metricValues"][2]["value"])
            results.append(f"• **{dim_val}**: {sess:,} sessions ({users:,} users, {convs:,} key events)")

        answer_text = f"**Query Results (Last 30 Days by {dimension}):**\n" + "\n".join(results)
        return jsonify({"answer": answer_text})
    except Exception as exc:
        logger.warning("Ask Analytics failed: %s", exc)
        return jsonify({"error": f"Failed to execute question lookup: {exc}"}), 500


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
    limit = int(data.get("limit", 15))

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
    conn = get_db()
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
    conn = get_db()
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

    conn = get_db()
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
