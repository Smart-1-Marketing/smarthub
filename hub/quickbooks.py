"""QuickBooks Online connector for the Hub.

One-time OAuth connect (like the Google module), tokens persisted on the
Render disk, then Client 360 can look up a client's QuickBooks customer and
their recent invoices — each deep-linked into QuickBooks Online.

Env:
  QB_CLIENT_ID / QB_CLIENT_SECRET  — from your app at developer.intuit.com
  QB_ENVIRONMENT                   — "production" (default) or "sandbox"
  QB_REDIRECT_URI                  — optional override; defaults to
                                     https://<hub-host>/qb/callback
Intuit rotates refresh tokens on every refresh, so the stored token file is
rewritten each time; refresh tokens live ~100 days of inactivity, and any
Hub use within that window keeps the connection alive indefinitely.
"""
import base64
import json
import os
import threading
import time

import requests
from itsdangerous import BadSignature, URLSafeTimedSerializer

AUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"

_lock = threading.Lock()


def _env(name, default=""):
    return os.environ.get(name, default)


def _api_base():
    if _env("QB_ENVIRONMENT", "production").lower().startswith("sand"):
        return "https://sandbox-quickbooks.api.intuit.com"
    return "https://quickbooks.api.intuit.com"


def configured() -> bool:
    return bool(_env("QB_CLIENT_ID") and _env("QB_CLIENT_SECRET"))


def _token_path() -> str:
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "quickbooks_tokens.json")


def _load_tokens():
    try:
        with open(_token_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_tokens(tok):
    with _lock:
        with open(_token_path(), "w", encoding="utf-8") as fh:
            json.dump(tok, fh)


def disconnect():
    try:
        os.remove(_token_path())
    except OSError:
        pass


def connected() -> bool:
    tok = _load_tokens()
    return bool(tok and tok.get("refresh_token") and tok.get("realm_id"))


def _state_serializer():
    secret = _env("SECRET_KEY") or _env("SESSION_SECRET") or "s1hub"
    return URLSafeTimedSerializer(secret, salt="s1hub-qb-oauth")


def redirect_uri(request) -> str:
    override = _env("QB_REDIRECT_URI")
    if override:
        return override
    root = request.url_root
    if root.startswith("http://") and "localhost" not in root and "127.0.0.1" not in root:
        root = "https://" + root[len("http://"):]
    return root.rstrip("/") + "/qb/callback"


def authorize_url(request) -> str:
    from urllib.parse import urlencode
    state = _state_serializer().dumps({"n": 1})
    return AUTH_BASE + "?" + urlencode({
        "client_id": _env("QB_CLIENT_ID"),
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": redirect_uri(request),
        "state": state,
    })


def _basic_auth():
    raw = f"{_env('QB_CLIENT_ID')}:{_env('QB_CLIENT_SECRET')}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def handle_callback(request) -> tuple[bool, str]:
    """Exchange the auth code; returns (ok, message)."""
    try:
        _state_serializer().loads(request.args.get("state", ""), max_age=600)
    except BadSignature:
        return False, "OAuth state check failed — try connecting again."
    code = request.args.get("code")
    realm_id = request.args.get("realmId")
    if not code or not realm_id:
        return False, "QuickBooks did not return an authorization code."
    r = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(request),
    }, headers={"Authorization": _basic_auth(), "Accept": "application/json"}, timeout=20)
    if not r.ok:
        return False, f"Token exchange failed (HTTP {r.status_code}): {r.text[:200]}"
    tok = r.json()
    _save_tokens({
        "access_token": tok.get("access_token"),
        "refresh_token": tok.get("refresh_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        "realm_id": realm_id,
    })
    return True, "QuickBooks connected."


def _ensure_access_token():
    tok = _load_tokens()
    if not tok:
        raise RuntimeError("QuickBooks is not connected.")
    if time.time() < tok.get("expires_at", 0) - 120:
        return tok
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": tok.get("refresh_token", ""),
    }, headers={"Authorization": _basic_auth(), "Accept": "application/json"}, timeout=20)
    if not r.ok:
        raise RuntimeError(f"QuickBooks token refresh failed (HTTP {r.status_code}) — reconnect from System Status.")
    fresh = r.json()
    tok.update({
        "access_token": fresh.get("access_token"),
        # Intuit rotates refresh tokens — always persist the new one.
        "refresh_token": fresh.get("refresh_token") or tok.get("refresh_token"),
        "expires_at": time.time() + int(fresh.get("expires_in", 3600)),
    })
    _save_tokens(tok)
    return tok


def _query(sql: str):
    tok = _ensure_access_token()
    r = requests.get(
        f"{_api_base()}/v3/company/{tok['realm_id']}/query",
        params={"query": sql, "minorversion": "70"},
        headers={"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json"},
        timeout=20,
    )
    if not r.ok:
        if r.status_code == 403 and ("3100" in r.text or "ApplicationAuthorizationFailed" in r.text):
            raise RuntimeError(
                "QuickBooks rejected the app for this company (error 3100). This almost always "
                "means the keys and environment don't match: you connected with the app's "
                "DEVELOPMENT keys while QB_ENVIRONMENT=production (or vice versa). In "
                "developer.intuit.com open your app -> Keys & credentials, copy the PRODUCTION "
                "Client ID/Secret into QB_CLIENT_ID / QB_CLIENT_SECRET, redeploy, then "
                "Disconnect and Connect QuickBooks again from System Status."
            )
        raise RuntimeError(f"QuickBooks query failed (HTTP {r.status_code}): {r.text[:200]}")
    return (r.json() or {}).get("QueryResponse", {})


def _esc(s: str) -> str:
    return str(s or "").replace("'", "\\'")


def customer_link(cid) -> str:
    return f"https://app.qbo.intuit.com/app/customerdetail?nameId={cid}"


def invoice_link(iid) -> str:
    return f"https://app.qbo.intuit.com/app/invoice?txnId={iid}"


def _customer_dict(c: dict) -> dict:
    created = ((c.get("MetaData") or {}).get("CreateTime") or "")[:10]
    since_label, years = "", None
    if created:
        import datetime as _dt
        try:
            d = _dt.date.fromisoformat(created)
            years = round((_dt.date.today() - d).days / 365.25, 1)
            since_label = d.strftime("%b %Y")
        except ValueError:
            pass
    return {
        "id": c.get("Id"),
        "name": c.get("DisplayName"),
        "balance": c.get("Balance", 0),
        "link": customer_link(c.get("Id")),
        "customer_since": created,
        "customer_since_label": since_label,
        "customer_years": years,
    }


def find_customers(q: str, limit: int = 5) -> list[dict]:
    rows = _query(
        f"SELECT * FROM Customer "
        f"WHERE DisplayName LIKE '%{_esc(q)}%' MAXRESULTS {int(limit)}"
    ).get("Customer", [])
    return [_customer_dict(c) for c in rows]


def customer_by_id(customer_id) -> dict | None:
    rows = _query(
        f"SELECT * FROM Customer WHERE Id = '{_esc(customer_id)}'"
    ).get("Customer", [])
    return _customer_dict(rows[0]) if rows else None


def invoices_for_customer(customer_id, limit: int = 8) -> list[dict]:
    rows = _query(
        f"SELECT Id, DocNumber, TxnDate, DueDate, TotalAmt, Balance FROM Invoice "
        f"WHERE CustomerRef = '{_esc(customer_id)}' "
        f"ORDERBY TxnDate DESC MAXRESULTS {int(limit)}"
    ).get("Invoice", [])
    import datetime as _dt
    today = _dt.date.today().isoformat()
    out = []
    for inv in rows:
        balance = float(inv.get("Balance") or 0)
        due = inv.get("DueDate") or ""
        if balance <= 0:
            status = "Paid"
        elif due and due < today:
            status = "Overdue"
        else:
            status = "Open"
        out.append({
            "id": inv.get("Id"),
            "doc_number": inv.get("DocNumber"),
            "date": inv.get("TxnDate"),
            "due_date": due,
            "total": inv.get("TotalAmt", 0),
            "balance": balance,
            "status": status,
            "link": invoice_link(inv.get("Id")),
        })
    return out


def _query_all(sql_base: str, page_size: int = 1000):
    """Run a QBO query with pagination (STARTPOSITION loop)."""
    rows, start = [], 1
    while True:
        resp = _query(f"{sql_base} STARTPOSITION {start} MAXRESULTS {page_size}")
        batch = None
        for v in resp.values():
            if isinstance(v, list):
                batch = v
                break
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
        if start > 20000:  # safety cap
            break
    return rows


def invoices_since(date_iso: str) -> list[dict]:
    """All invoices on/after date_iso: customer name, date, total."""
    rows = _query_all(
        f"SELECT Id, TotalAmt, TxnDate, CustomerRef FROM Invoice "
        f"WHERE TxnDate >= '{_esc(date_iso)}' ORDERBY TxnDate"
    )
    out = []
    for inv in rows:
        ref = inv.get("CustomerRef") or {}
        out.append({
            "id": inv.get("Id"),
            "customer_id": ref.get("value"),
            "customer": ref.get("name") or "",
            "date": inv.get("TxnDate") or "",
            "total": float(inv.get("TotalAmt") or 0),
            "link": invoice_link(inv.get("Id")),
        })
    return out


def monthly_totals_by_customer(months: int = 4) -> dict:
    """{customer_name: {"id": qbid, "months": {"YYYY-MM": total}}} for the
    current month and the (months-1) prior months."""
    import datetime as _dt
    today = _dt.date.today()
    first = today.replace(day=1)
    for _ in range(months - 1):
        first = (first - _dt.timedelta(days=1)).replace(day=1)
    invoices = invoices_since(first.isoformat())
    out: dict = {}
    for inv in invoices:
        name = inv["customer"]
        if not name:
            continue
        ym = inv["date"][:7]
        rec = out.setdefault(name, {"id": inv.get("customer_id"), "months": {}})
        rec["months"][ym] = round(rec["months"].get(ym, 0) + inv["total"], 2)
    return out


def lookup(q: str, customer_id=None) -> dict:
    """Customer match(es) for a client name — or one attached customer by id —
    each with recent invoices."""
    if not configured():
        return {"configured": False, "connected": False, "customers": []}
    if not connected():
        return {"configured": True, "connected": False, "customers": []}
    if customer_id:
        c = customer_by_id(customer_id)
        customers = [c] if c else []
    else:
        customers = find_customers(q, limit=3)
    for c in customers:
        try:
            c["invoices"] = invoices_for_customer(c["id"])
        except RuntimeError as exc:
            c["invoices"] = []
            c["error"] = str(exc)
    return {"configured": True, "connected": True, "customers": customers}
