"""Private SmartHub check reconciliation tool for QuickBooks Online.

Workflow:
  upload check -> OCR/extract payer/date/amount -> match QuickBooks customer(s)
  -> review open invoices -> approve allocations -> create QBO Payment(s)
  -> verify balances -> keep an audit trail and remember payer aliases.

The module is mounted at /tools/check-reconciliation and is intentionally
owner-only. It requires a real SmartHub account session; the legacy shared
PANEL_PASSWORD session is not sufficient.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, jsonify, redirect, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024

DATA_ROOT = Path(os.environ.get("CHECK_RECONCILIATION_DATA_DIR", "/var/data/check-reconciliation"))
DATA_FILE = DATA_ROOT / "state.json"
UPLOAD_DIR = DATA_ROOT / "uploads"
_LOCK = threading.RLock()

QBO_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_API_BASE = os.environ.get("QBO_API_BASE", "https://quickbooks.api.intuit.com").rstrip("/")
QBO_MINOR_VERSION = os.environ.get("QBO_MINOR_VERSION", "75")
QBO_SCOPE = "com.intuit.quickbooks.accounting"

DEFAULT_STATE = {
    "version": 1,
    "oauth": {},
    "aliases": {},
    "checks": [],
    "audit": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _read_state() -> dict[str, Any]:
    _ensure_dirs()
    with _LOCK:
        if not DATA_FILE.exists():
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            raw = json.loads(DATA_FILE.read_text("utf-8"))
        except Exception:
            raw = {}
        out = json.loads(json.dumps(DEFAULT_STATE))
        if isinstance(raw, dict):
            out.update(raw)
        for k, default in DEFAULT_STATE.items():
            if not isinstance(out.get(k), type(default)):
                out[k] = json.loads(json.dumps(default))
        return out


def _write_state(state: dict[str, Any]) -> None:
    _ensure_dirs()
    with _LOCK:
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), "utf-8")
        os.replace(tmp, DATA_FILE)


def _mutate(fn):
    with _LOCK:
        state = _read_state()
        result = fn(state)
        _write_state(state)
        return result


def _account_session() -> dict[str, Any]:
    try:
        from hub.users_routes import session_from_environ
        return session_from_environ(request.environ) or {}
    except Exception:
        return {}


def _audit(action: str, **details: Any) -> None:
    actor = _account_session().get("e") or request.environ.get("s1hub.user") or "unknown"
    def apply(state):
        state["audit"].append({"at": _now(), "actor": actor, "action": action, **details})
        state["audit"] = state["audit"][-1500:]
    try:
        _mutate(apply)
    except Exception:
        pass
    try:
        from hub import audit as hub_audit
        hub_audit.log("check_reconciliation", action, actor=actor, **details)
    except Exception:
        pass


def _allowed_emails() -> set[str]:
    raw = os.environ.get("CHECK_RECONCILIATION_ALLOWED_EMAILS", "")
    return {x.strip().lower() for x in re.split(r"[,;\s]+", raw) if x.strip()}


def _owner_gate():
    sess = _account_session()
    email = str(sess.get("e") or "").strip().lower()
    role = str(sess.get("r") or "").lower()
    if not email:
        return ("This tool requires a named SmartHub account. The shared password session is not allowed.", 403)
    allowed = _allowed_emails()
    if not allowed:
        return ("Check Reconciliation is not configured. Set CHECK_RECONCILIATION_ALLOWED_EMAILS on Render.", 503)
    if email not in allowed:
        return ("This accounting tool is restricted to its authorized owner.", 403)
    if role not in {"admin", "super_admin"}:
        return ("This accounting tool requires an Admin SmartHub account.", 403)
    return None


@app.before_request
def _restrict_owner():
    return _owner_gate()


def _fernet() -> Fernet:
    raw = os.environ.get("CHECK_RECONCILIATION_ENCRYPTION_KEY") or os.environ.get("TOKEN_ENCRYPTION_KEY") or ""
    if not raw:
        raise RuntimeError("Set CHECK_RECONCILIATION_ENCRYPTION_KEY (or TOKEN_ENCRYPTION_KEY) before connecting QuickBooks.")
    try:
        return Fernet(raw.encode("ascii"))
    except Exception:
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        return Fernet(derived)


def _enc(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _dec(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise RuntimeError("Saved QuickBooks token cannot be decrypted. Check the encryption key.") from exc


def _qbo_configured() -> bool:
    return bool(os.environ.get("QBO_CLIENT_ID") and os.environ.get("QBO_CLIENT_SECRET") and
                (os.environ.get("QBO_REDIRECT_URI") or request.host_url))


def _redirect_uri() -> str:
    explicit = os.environ.get("QBO_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    return request.host_url.rstrip("/") + request.script_root + "/oauth/callback"


def _oauth_record() -> dict[str, Any]:
    return _read_state().get("oauth") or {}


def _save_oauth(payload: dict[str, Any], realm_id: str) -> None:
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    if not access or not refresh or not realm_id:
        raise RuntimeError("Intuit did not return the required OAuth tokens/company ID.")
    now = int(time.time())
    def apply(state):
        state["oauth"] = {
            "realm_id": realm_id,
            "access_token": _enc(access),
            "refresh_token": _enc(refresh),
            "access_expires_at": now + int(payload.get("expires_in") or 3600) - 60,
            "refresh_expires_at": now + int(payload.get("x_refresh_token_expires_in") or 0),
            "connected_at": _now(),
        }
    _mutate(apply)


def _refresh_access() -> str:
    rec = _oauth_record()
    refresh = _dec(rec.get("refresh_token") or "")
    response = requests.post(
        QBO_TOKEN_URL,
        auth=(os.environ["QBO_CLIENT_ID"], os.environ["QBO_CLIENT_SECRET"]),
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"QuickBooks token refresh failed ({response.status_code}): {response.text[:500]}")
    payload = response.json()
    realm = rec.get("realm_id") or ""
    _save_oauth(payload, realm)
    return str(payload["access_token"])


def _access_token() -> str:
    rec = _oauth_record()
    if not rec.get("access_token"):
        raise RuntimeError("QuickBooks is not connected.")
    if int(rec.get("access_expires_at") or 0) <= int(time.time()) + 30:
        return _refresh_access()
    return _dec(rec["access_token"])


def _qbo(method: str, path: str, *, params=None, payload=None, retry=True) -> dict[str, Any]:
    realm = _oauth_record().get("realm_id")
    if not realm:
        raise RuntimeError("QuickBooks is not connected.")
    url = f"{QBO_API_BASE}/v3/company/{realm}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {_access_token()}", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    params = dict(params or {})
    params.setdefault("minorversion", QBO_MINOR_VERSION)
    response = requests.request(method, url, headers=headers, params=params, json=payload, timeout=45)
    if response.status_code == 401 and retry:
        _refresh_access()
        return _qbo(method, path, params=params, payload=payload, retry=False)
    if not response.ok:
        try:
            detail = json.dumps(response.json())[:1600]
        except Exception:
            detail = response.text[:1600]
        raise RuntimeError(f"QuickBooks API error {response.status_code}: {detail}")
    if not response.content:
        return {}
    return response.json()


def _qbo_query(sql: str) -> list[dict[str, Any]]:
    data = _qbo("GET", "query", params={"query": sql})
    qr = data.get("QueryResponse") or {}
    for key in ("Customer", "Invoice", "Payment", "PaymentMethod"):
        if key in qr:
            rows = qr.get(key) or []
            return rows if isinstance(rows, list) else [rows]
    return []


def _all_customers() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 1
    while start <= 5000:
        batch = _qbo_query(f"select * from Customer where Active = true startposition {start} maxresults 1000")
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    return rows


def _normalize_name(value: str) -> str:
    s = (value or "").lower().replace("&", " and ")
    s = re.sub(r"\b(llc|incorporated|inc|corp|corporation|company|co|ltd|limited|the)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _score_name(payer: str, customer: dict[str, Any]) -> float:
    a = _normalize_name(payer)
    names = [str(customer.get("DisplayName") or ""), str(customer.get("CompanyName") or ""),
             str(customer.get("FullyQualifiedName") or "")]
    best = 0.0
    for raw in names:
        b = _normalize_name(raw)
        if not a or not b:
            continue
        ratio = SequenceMatcher(None, a, b).ratio()
        aset, bset = set(a.split()), set(b.split())
        overlap = len(aset & bset) / max(1, len(aset | bset))
        contains = 1.0 if a in b or b in a else 0.0
        score = ratio * 0.55 + overlap * 0.30 + contains * 0.15
        best = max(best, score)
    return round(best, 4)


def _alias_for(payer: str) -> dict[str, Any] | None:
    key = _normalize_name(payer)
    rec = _read_state().get("aliases", {}).get(key)
    return rec if isinstance(rec, dict) else None


def _save_alias(payer: str, customers: list[dict[str, Any]]) -> None:
    key = _normalize_name(payer)
    if not key:
        return
    values = [{"id": str(c.get("Id") or c.get("id") or ""),
               "name": str(c.get("DisplayName") or c.get("name") or "")} for c in customers]
    values = [v for v in values if v["id"]]
    def apply(state):
        state["aliases"][key] = {"payer": payer, "customers": values, "confirmed_at": _now()}
    _mutate(apply)
    _audit("alias_confirmed", payer=payer, customers=values)


def _find_check(check_id: str, state=None) -> dict[str, Any] | None:
    state = state or _read_state()
    return next((x for x in state["checks"] if x.get("id") == check_id), None)


def _open_invoices(customer_ids: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid in customer_ids:
        safe = cid.replace("'", "")
        rows = _qbo_query(f"select * from Invoice where CustomerRef = '{safe}' orderby TxnDate")
        for inv in rows:
            bal = float(inv.get("Balance") or 0)
            if bal <= 0.0001:
                continue
            late = 0.0
            for line in inv.get("Line") or []:
                text = str(line.get("Description") or "")
                detail = line.get("SalesItemLineDetail") or {}
                item_name = str((detail.get("ItemRef") or {}).get("name") or "")
                if "late fee" in (text + " " + item_name).lower():
                    late += float(line.get("Amount") or 0)
            out.append({
                "id": str(inv.get("Id") or ""),
                "doc_number": str(inv.get("DocNumber") or ""),
                "txn_date": str(inv.get("TxnDate") or ""),
                "due_date": str(inv.get("DueDate") or ""),
                "total": float(inv.get("TotalAmt") or 0),
                "balance": bal,
                "late_fees": round(late, 2),
                "customer_id": str((inv.get("CustomerRef") or {}).get("value") or cid),
                "customer_name": str((inv.get("CustomerRef") or {}).get("name") or ""),
            })
    out.sort(key=lambda x: (x["txn_date"], x["doc_number"]))
    return out


def _suggest_allocations(amount: float, invoices: list[dict[str, Any]]) -> dict[str, Any]:
    cents = int(round(amount * 100))
    if cents <= 0:
        return {"allocations": [], "reason": "No amount"}
    for inv in invoices:
        if int(round(inv["balance"] * 100)) == cents:
            return {"allocations": [{"invoice_id": inv["id"], "amount": amount}], "reason": "Exact invoice balance"}
    for inv in invoices:
        principal = int(round((inv["balance"] - inv.get("late_fees", 0)) * 100))
        if inv.get("late_fees", 0) > 0 and principal == cents:
            return {"allocations": [{"invoice_id": inv["id"], "amount": amount}],
                    "reason": "Matches invoice before late fee", "late_fee_warning": True}
    subset = invoices[:18]
    for size in range(2, min(5, len(subset) + 1)):
        for group in combinations(subset, size):
            total = sum(int(round(x["balance"] * 100)) for x in group)
            if total == cents:
                return {"allocations": [{"invoice_id": x["id"], "amount": x["balance"]} for x in group],
                        "reason": f"Exact combination of {size} invoices"}
    return {"allocations": [], "reason": "Manual allocation needed"}


def _extract_check(raw: bytes, mime: str) -> dict[str, Any]:
    result = {"payer": "", "date": "", "amount": None, "check_number": "", "confidence": "manual"}
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or not mime.startswith("image/"):
        return result
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        prompt = (
            "Read this business check or remittance image. Return ONLY JSON with keys payer, date, amount, "
            "check_number, confidence. payer is the company/person issuing the check, not the payee. "
            "date must be YYYY-MM-DD when visible. amount must be a JSON number without $ or commas. "
            "If a field is uncertain use an empty string/null. confidence is high, medium, or low."
        )
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"), temperature=0, max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
        )
        # A billed call that records nothing is invisible on the usage page,
        # which is the untracked-spend failure hub/quotas.py sweeps for. The
        # chat completions response carries real token counts, so they are
        # recorded; standalone runs (no hub package) lose the row, never the
        # read.
        try:
            from hub import ai as hub_ai
            hub_ai.note_sdk_usage("check_reconciliation", resp, purpose="check_ocr")
        except Exception:
            pass
        text = resp.choices[0].message.content or "{}"
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I).strip()
        parsed = json.loads(text)
        result.update({k: parsed.get(k) for k in result if k in parsed})
    except Exception as exc:
        result["ocr_error"] = str(exc)[:300]
    return result


def _payment_payload(customer_id: str, txn_date: str, check_number: str,
                     allocations: list[dict[str, Any]], payer: str) -> dict[str, Any]:
    total = round(sum(float(x["amount"]) for x in allocations), 2)
    payload: dict[str, Any] = {
        "CustomerRef": {"value": str(customer_id)},
        "TotalAmt": total,
        "TxnDate": txn_date,
        "PrivateNote": f"SmartHub check reconciliation: {payer}"[:4000],
        "Line": [
            {"Amount": round(float(x["amount"]), 2),
             "LinkedTxn": [{"TxnId": str(x["invoice_id"]), "TxnType": "Invoice"}]}
            for x in allocations
        ],
    }
    if check_number:
        payload["PaymentRefNum"] = check_number[:21]
    return payload


def _post_payment_groups(check: dict[str, Any], allocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create customer-grouped Payments without making a partial success retryable.

    A single paper check can span more than one QuickBooks Customer record,
    and QuickBooks requires one Payment per Customer, so this operation cannot
    be atomic. Each successful Payment ID is persisted immediately; if a later
    Customer payment fails, the check is locked in ``partial`` state instead
    of becoming retryable and accidentally posting the first Payment twice.
    """
    customer_ids = {str(a.get("customer_id") or "") for a in allocations}
    if "" in customer_ids:
        raise RuntimeError("Every allocation must name its QuickBooks customer.")
    invoices = {x["id"]: x for x in _open_invoices(list(customer_ids))}
    groups: dict[str, list[dict[str, Any]]] = {}
    # Validate the entire request before the first write to QuickBooks.
    for alloc in allocations:
        iid = str(alloc.get("invoice_id") or "")
        inv = invoices.get(iid)
        if not inv:
            raise RuntimeError(f"Invoice {iid} is no longer open. Refresh before posting.")
        cid = str(alloc.get("customer_id") or inv["customer_id"])
        if cid != inv["customer_id"]:
            raise RuntimeError(f"Invoice {iid} does not belong to selected customer {cid}.")
        amount = round(float(alloc.get("amount") or 0), 2)
        if amount <= 0 or amount - inv["balance"] > 0.005:
            raise RuntimeError(f"Invalid allocation for {inv['doc_number']}: {amount:.2f} against {inv['balance']:.2f} open.")
        groups.setdefault(cid, []).append({"invoice_id": iid, "amount": amount})
    expected = round(float(check.get("amount") or 0), 2)
    allocated = round(sum(x["amount"] for g in groups.values() for x in g), 2)
    if abs(expected - allocated) > 0.005:
        raise RuntimeError(f"Allocations total ${allocated:,.2f}; the check is ${expected:,.2f}. Allocate the full check before posting.")
    results = []
    try:
        for cid, group in groups.items():
            payload = _payment_payload(cid, check["date"], check.get("check_number") or "", group, check.get("payer") or "")
            data = _qbo("POST", "payment", payload=payload)
            payment = data.get("Payment") or {}
            pid = str(payment.get("Id") or "")
            if not pid:
                raise RuntimeError(
                    "QuickBooks accepted the request but did not return a Payment ID. "
                    "Stop and verify QuickBooks before retrying.")
            result = {"customer_id": cid, "payment_id": pid, "amount": payload["TotalAmt"], "allocations": group}
            results.append(result)

            # Persist the external write before attempting another external write.
            def remember(state, posted=result):
                target = _find_check(check["id"], state)
                if not target:
                    raise RuntimeError("Check record disappeared while posting.")
                saved = target.setdefault("payments", [])
                if not any(x.get("payment_id") == posted["payment_id"] for x in saved):
                    saved.append(posted)
                target["status"] = "posting"
                target["posting_updated_at"] = _now()

            _mutate(remember)
    except Exception as exc:
        if results:
            def partial(state):
                target = _find_check(check["id"], state)
                if target:
                    target["status"] = "partial"
                    target["partial_error"] = str(exc)[:800]
                    target["posting_updated_at"] = _now()
            _mutate(partial)
            _audit("payment_partial", check_id=check["id"],
                   payment_ids=[x["payment_id"] for x in results], error=str(exc)[:500])
            raise RuntimeError(
                "QuickBooks posted part of this check before a later customer payment failed. "
                "The successful Payment ID(s) were saved and this check is locked to prevent "
                "a duplicate. Review the partial record before any manual completion. "
                f"Original error: {exc}") from exc
        raise
    return results


def _api_error(exc: Exception, status=400):
    return jsonify({"ok": False, "error": str(exc)}), status


@app.route("/")
def index():
    cfg = {"connected": bool(_oauth_record().get("realm_id")), "configured": _qbo_configured()}
    return Response(_PAGE.replace("__BOOT__", json.dumps(cfg).replace("</", "<\\/")), mimetype="text/html")


@app.route("/oauth/start")
def oauth_start():
    if not _qbo_configured():
        return "Set QBO_CLIENT_ID, QBO_CLIENT_SECRET and QBO_REDIRECT_URI first.", 503
    state_token = secrets.token_urlsafe(24)
    def apply(state):
        state.setdefault("oauth", {})["pending_state"] = _enc(state_token)
        state["oauth"]["pending_state_at"] = int(time.time())
    _mutate(apply)
    params = {"client_id": os.environ["QBO_CLIENT_ID"], "response_type": "code", "scope": QBO_SCOPE,
              "redirect_uri": _redirect_uri(), "state": state_token}
    return redirect(QBO_AUTH_URL + "?" + urlencode(params))


@app.route("/oauth/callback")
def oauth_callback():
    rec = _oauth_record()
    pending = ""
    if rec.get("pending_state"):
        try:
            pending = _dec(rec["pending_state"])
        except Exception:
            pending = ""
    if not pending or not secrets.compare_digest(pending, request.args.get("state", "")):
        return "Invalid or expired OAuth state.", 400
    if int(time.time()) - int(rec.get("pending_state_at") or 0) > 900:
        return "OAuth request expired. Start the QuickBooks connection again.", 400
    code = request.args.get("code", "")
    realm = request.args.get("realmId", "")
    if not code or not realm:
        return "Intuit did not return an authorization code/company ID.", 400
    response = requests.post(
        QBO_TOKEN_URL,
        auth=(os.environ["QBO_CLIENT_ID"], os.environ["QBO_CLIENT_SECRET"]),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri()},
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if not response.ok:
        return f"QuickBooks authorization failed: {response.text[:800]}", 400
    _save_oauth(response.json(), realm)
    _audit("quickbooks_connected", realm_id=realm)
    return redirect(request.script_root + "/?connected=1")


@app.route("/oauth/disconnect", methods=["POST"])
def oauth_disconnect():
    def apply(state):
        state["oauth"] = {}
    _mutate(apply)
    _audit("quickbooks_disconnected")
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    rec = _oauth_record()
    out = {"ok": True, "connected": bool(rec.get("realm_id")), "realm_id": rec.get("realm_id"),
           "configured": _qbo_configured(), "redirect_uri": _redirect_uri()}
    if out["connected"]:
        try:
            company = _qbo("GET", "companyinfo/" + str(rec["realm_id"]))
            out["company_name"] = (company.get("CompanyInfo") or {}).get("CompanyName")
        except Exception as exc:
            out["connection_error"] = str(exc)
    return jsonify(out)


@app.route("/api/checks")
def api_checks():
    state = _read_state()
    checks = sorted(state["checks"], key=lambda x: (x.get("date") or "", x.get("created_at") or ""), reverse=True)
    return jsonify({"ok": True, "checks": checks, "aliases": state["aliases"]})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return _api_error(ValueError("Choose a check image first."))
    raw = file.read()
    if not raw:
        return _api_error(ValueError("The uploaded file is empty."))
    mime = file.mimetype or "application/octet-stream"
    ext = Path(secure_filename(file.filename)).suffix.lower()[:8] or ".bin"
    cid = "chk_" + secrets.token_hex(8)
    path = UPLOAD_DIR / f"{cid}{ext}"
    _ensure_dirs(); path.write_bytes(raw)
    extracted = _extract_check(raw, mime)
    manual = request.form
    payer = (manual.get("payer") or extracted.get("payer") or "").strip()
    date = (manual.get("date") or extracted.get("date") or "").strip()
    amount_raw = manual.get("amount") or extracted.get("amount")
    try:
        amount = round(float(str(amount_raw).replace("$", "").replace(",", "")), 2) if amount_raw not in (None, "") else None
    except Exception:
        amount = None
    check_no = (manual.get("check_number") or extracted.get("check_number") or "").strip()
    rec = {"id": cid, "created_at": _now(), "payer": payer, "date": date, "amount": amount,
           "check_number": check_no, "ocr_confidence": extracted.get("confidence"),
           "ocr_error": extracted.get("ocr_error"), "file": path.name, "status": "new",
           "customer_matches": [], "selected_customers": [], "suggestion": {}, "payments": []}
    def apply(state): state["checks"].append(rec)
    _mutate(apply)
    _audit("check_uploaded", check_id=cid, payer=payer, amount=amount, date=date)
    return jsonify({"ok": True, "check": rec})


@app.route("/api/check/<check_id>", methods=["PATCH"])
def api_update_check(check_id: str):
    body = request.get_json(silent=True) or {}
    allowed = {"payer", "date", "amount", "check_number"}
    def apply(state):
        chk = _find_check(check_id, state)
        if not chk: raise KeyError("Check not found.")
        if chk.get("status") == "posted": raise ValueError("Posted checks are locked.")
        for key in allowed:
            if key in body:
                chk[key] = round(float(body[key]), 2) if key == "amount" and body[key] not in (None, "") else str(body[key]).strip()
        chk["updated_at"] = _now(); return chk
    try:
        return jsonify({"ok": True, "check": _mutate(apply)})
    except Exception as exc:
        return _api_error(exc, 404 if isinstance(exc, KeyError) else 400)


@app.route("/api/check/<check_id>/match", methods=["POST"])
def api_match(check_id: str):
    chk = _find_check(check_id)
    if not chk: return _api_error(KeyError("Check not found."), 404)
    payer = (request.get_json(silent=True) or {}).get("payer") or chk.get("payer") or ""
    if not payer: return _api_error(ValueError("Enter the payer/company name first."))
    try:
        customers = _all_customers(); alias = _alias_for(payer)
        alias_ids = {str(x.get("id")) for x in (alias or {}).get("customers", [])}
        ranked = sorted(((_score_name(payer, c), c) for c in customers), key=lambda x: x[0], reverse=True)[:12]
        matches = [{"id": str(c.get("Id") or ""), "name": str(c.get("DisplayName") or ""),
                    "company": str(c.get("CompanyName") or ""), "score": score,
                    "remembered": str(c.get("Id") or "") in alias_ids} for score, c in ranked]
        if alias_ids:
            by_id = {str(c.get("Id")): c for c in customers}
            for aid in alias_ids:
                if aid in by_id and not any(x["id"] == aid for x in matches):
                    c = by_id[aid]
                    matches.insert(0, {"id": aid, "name": str(c.get("DisplayName") or ""),
                                       "company": str(c.get("CompanyName") or ""), "score": 1.0, "remembered": True})
        def apply(state):
            target = _find_check(check_id, state); target["customer_matches"] = matches
            if alias_ids: target["selected_customers"] = [x for x in matches if x["id"] in alias_ids]
            target["status"] = "matched" if target.get("selected_customers") else "needs_match"
        _mutate(apply)
        return jsonify({"ok": True, "matches": matches, "remembered": (alias or {}).get("customers", [])})
    except Exception as exc:
        return _api_error(exc)


@app.route("/api/check/<check_id>/customers", methods=["POST"])
def api_confirm_customers(check_id: str):
    ids = [str(x) for x in ((request.get_json(silent=True) or {}).get("customer_ids") or []) if str(x)]
    if not ids: return _api_error(ValueError("Choose at least one QuickBooks customer."))
    try:
        allc = {str(c.get("Id")): c for c in _all_customers()}; chosen = [allc[x] for x in ids if x in allc]
        if len(chosen) != len(ids): raise ValueError("One of the selected customers no longer exists in QuickBooks.")
        chk = _find_check(check_id)
        if not chk: raise KeyError("Check not found.")
        _save_alias(chk.get("payer") or "", chosen)
        selected = [{"id": str(c["Id"]), "name": str(c.get("DisplayName") or "")} for c in chosen]
        invoices = _open_invoices(ids); suggestion = _suggest_allocations(float(chk.get("amount") or 0), invoices)
        def apply(state):
            target = _find_check(check_id, state); target["selected_customers"] = selected
            target["invoices"] = invoices; target["suggestion"] = suggestion; target["status"] = "ready"
        _mutate(apply)
        return jsonify({"ok": True, "customers": selected, "invoices": invoices, "suggestion": suggestion})
    except Exception as exc:
        return _api_error(exc, 404 if isinstance(exc, KeyError) else 400)


@app.route("/api/check/<check_id>/refresh-invoices", methods=["POST"])
def api_refresh_invoices(check_id: str):
    chk = _find_check(check_id)
    if not chk: return _api_error(KeyError("Check not found."), 404)
    ids = [str(x.get("id")) for x in chk.get("selected_customers") or []]
    if not ids: return _api_error(ValueError("Match the customer first."))
    try:
        invoices = _open_invoices(ids); suggestion = _suggest_allocations(float(chk.get("amount") or 0), invoices)
        def apply(state):
            target = _find_check(check_id, state); target["invoices"] = invoices; target["suggestion"] = suggestion
        _mutate(apply); return jsonify({"ok": True, "invoices": invoices, "suggestion": suggestion})
    except Exception as exc:
        return _api_error(exc)


@app.route("/api/check/<check_id>/post", methods=["POST"])
def api_post(check_id: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return _api_error(ValueError("Explicit confirmation is required before posting a QuickBooks payment."))
    chk = _find_check(check_id)
    if not chk: return _api_error(KeyError("Check not found."), 404)
    if chk.get("status") == "posted" or chk.get("payments"):
        return _api_error(ValueError("This check has already been posted. Duplicate posting is blocked."), 409)
    if not chk.get("date") or chk.get("amount") in (None, ""):
        return _api_error(ValueError("Check date and amount are required."))
    allocations = body.get("allocations") or []
    if not allocations: return _api_error(ValueError("Allocate the check to at least one open invoice."))
    try:
        check_no = str(chk.get("check_number") or "").strip()
        if check_no:
            for other in _read_state()["checks"]:
                if other.get("id") == check_id or other.get("status") != "posted": continue
                if (str(other.get("check_number") or "").strip() == check_no and other.get("date") == chk.get("date") and
                    abs(float(other.get("amount") or 0) - float(chk.get("amount") or 0)) < .005):
                    raise RuntimeError(f"Check #{check_no} was already posted as {other.get('id')}. Duplicate blocked.")
        results = _post_payment_groups(chk, allocations)
        verify_ids = list({x["customer_id"] for x in results}); remaining = _open_invoices(verify_ids)
        remaining_by_id = {x["id"]: x["balance"] for x in remaining}; verification = []
        for r in results:
            for a in r["allocations"]:
                verification.append({"invoice_id": a["invoice_id"], "remaining_balance": remaining_by_id.get(a["invoice_id"], 0.0)})
        def apply(state):
            target = _find_check(check_id, state); target["payments"] = results; target["verification"] = verification
            target["posted_at"] = _now(); target["status"] = "posted"
        _mutate(apply)
        _audit("payment_posted", check_id=check_id, payer=chk.get("payer"), amount=chk.get("amount"),
               payment_ids=[x["payment_id"] for x in results])
        return jsonify({"ok": True, "payments": results, "verification": verification})
    except Exception as exc:
        _audit("payment_failed", check_id=check_id, error=str(exc)[:500]); return _api_error(exc)


@app.route("/api/check/<check_id>", methods=["DELETE"])
def api_delete_check(check_id: str):
    def apply(state):
        chk = _find_check(check_id, state)
        if not chk: raise KeyError("Check not found.")
        if chk.get("status") == "posted": raise ValueError("Posted check records cannot be deleted from the audit trail.")
        state["checks"] = [x for x in state["checks"] if x.get("id") != check_id]; return chk
    try:
        chk = _mutate(apply)
        try: (UPLOAD_DIR / str(chk.get("file") or "")).unlink(missing_ok=True)
        except Exception: pass
        _audit("check_deleted", check_id=check_id); return jsonify({"ok": True})
    except Exception as exc:
        return _api_error(exc, 404 if isinstance(exc, KeyError) else 400)


@app.route("/api/audit")
def api_audit():
    return jsonify({"ok": True, "audit": list(reversed(_read_state()["audit"][-300:]))})


_PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Check Reconciliation · Smart 1 Hub</title><style>
:root{--navy:#13294b;--bg:#f4f7fb;--card:#fff;--text:#172033;--muted:#677489;--line:#dfe5ee;--green:#147a45;--red:#b42318;--amber:#9a6700}*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px/1.45 Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--text)}main{max-width:1220px;margin:auto;padding:28px 22px 70px}.head{display:flex;gap:18px;align-items:flex-start;justify-content:space-between;margin-bottom:20px}.head h1{margin:0;font-size:28px;color:var(--navy)}.sub{color:var(--muted);max-width:760px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:14px;box-shadow:0 1px 2px #1018280d}.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.btn{border:1px solid #b8c4d5;border-radius:9px;padding:9px 13px;background:white;color:var(--navy);font-weight:650;cursor:pointer}.btn.primary{background:var(--navy);color:white;border-color:var(--navy)}.btn.danger{color:var(--red)}.btn:disabled{opacity:.45}.pill{display:inline-block;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:700;background:#e9eef6;color:#44546c}.pill.good{background:#e8f5ed;color:var(--green)}.pill.warn{background:#fff4d6;color:var(--amber)}.pill.bad{background:#feeceb;color:var(--red)}input,select{border:1px solid #bdc7d6;border-radius:8px;padding:8px 10px;background:white;font:inherit;min-height:38px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px}.f{grid-column:span 3}.f.wide{grid-column:span 6}.f label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:4px}.f input{width:100%}.check{border-left:5px solid #ccd5e2}.check.posted{border-left-color:#28a164}.titleline{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}.money{font-size:20px;font-weight:800;color:var(--navy)}table{width:100%;border-collapse:collapse;margin-top:10px}th,td{padding:9px 8px;text-align:left;border-bottom:1px solid #e8edf3}th{font-size:12px;color:var(--muted)}td.num,th.num{text-align:right}.match{display:flex;gap:8px;align-items:center;margin:7px 0}.match input{min-height:auto}.muted{color:var(--muted)}.error{color:var(--red);white-space:pre-wrap}.success{color:var(--green)}.warning{padding:10px 12px;border-radius:8px;background:#fff7df;color:#795700;margin-top:10px}.empty{text-align:center;color:var(--muted);padding:35px}.spinner{display:none}.busy .spinner{display:inline}.alloc{width:110px;text-align:right}.modal{position:fixed;inset:0;background:#08182c99;display:none;align-items:center;justify-content:center;padding:20px;z-index:100}.modal.open{display:flex}.modal .box{background:white;border-radius:14px;max-width:680px;width:100%;padding:20px;max-height:90vh;overflow:auto}@media(max-width:760px){main{padding:18px 12px}.head{display:block}.f,.f.wide{grid-column:span 12}.card{padding:14px}table{font-size:12px}}
</style></head><body><main><div class="head"><div><h1>Check Reconciliation</h1><div class="sub">Upload a check, match the payer to QuickBooks, allocate it to open invoices, then approve the actual QuickBooks payment. Confirmed payer matches are remembered for future checks.</div></div><div id="qbo"></div></div><div class="card"><form id="upload" class="grid"><div class="f wide"><label>Check image</label><input type="file" name="file" accept="image/*,.pdf" required></div><div class="f"><label>Payer override</label><input name="payer"></div><div class="f"><label>Amount override</label><input name="amount" inputmode="decimal"></div><div class="f"><label>Date override</label><input type="date" name="date"></div><div class="f"><label>Check # override</label><input name="check_number"></div><div class="f wide"><button class="btn primary" type="submit">Upload & read check</button> <span class="spinner">Reading…</span><div id="uploadMsg"></div></div></form></div><div class="toolbar" style="margin:16px 0"><button class="btn" onclick="loadChecks()">Refresh</button><select id="filter" onchange="render()"><option value="all">All checks</option><option value="new">Needs work</option><option value="posted">Posted</option></select><span id="summary" class="muted"></span></div><div id="checks"></div></main><div class="modal" id="confirm"><div class="box"><h2>Post payment to QuickBooks?</h2><p id="confirmText"></p><div class="warning">This creates a real QuickBooks Payment transaction and changes A/R. It cannot be a preview.</div><div class="toolbar" style="margin-top:16px"><button class="btn primary" id="confirmPost">Yes — post payment</button><button class="btn" onclick="closeConfirm()">Cancel</button></div><div id="postMsg"></div></div></div>
<script>const BOOT=__BOOT__;let data={checks:[],aliases:{}};const $=s=>document.querySelector(s), money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(n||0));async function api(url,opt={}){let r=await fetch(url,{headers:{'Accept':'application/json',...(opt.body instanceof FormData?{}:{'Content-Type':'application/json'})},...opt});let j=await r.json().catch(()=>({error:'Unexpected server response'}));if(!r.ok||j.ok===false)throw new Error(j.error||`Request failed ${r.status}`);return j}async function qboStatus(){try{let s=await api('api/status');$('#qbo').innerHTML=s.connected?`<span class="pill good">QuickBooks: ${esc(s.company_name||s.realm_id)}</span> <button class="btn" onclick="disconnectQbo()">Disconnect</button>`:`<a class="btn primary" href="oauth/start">Connect QuickBooks</a>${s.configured?'':' <span class="pill warn">API keys needed</span>'}`}catch(e){$('#qbo').innerHTML=`<span class="pill bad">${esc(e.message)}</span>`}}async function disconnectQbo(){if(!confirm('Disconnect QuickBooks from this tool?'))return;await api('oauth/disconnect',{method:'POST',body:'{}'});qboStatus()}$('#upload').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);e.target.classList.add('busy');$('#uploadMsg').textContent='';try{let r=await api('api/upload',{method:'POST',body:f});e.target.reset();await loadChecks();setTimeout(()=>document.getElementById(r.check.id)?.scrollIntoView({behavior:'smooth'}),50)}catch(err){$('#uploadMsg').innerHTML=`<div class="error">${esc(err.message)}</div>`}finally{e.target.classList.remove('busy')}};async function loadChecks(){try{data=await api('api/checks');render()}catch(e){$('#checks').innerHTML=`<div class="card error">${esc(e.message)}</div>`}}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function render(){let f=$('#filter').value;let rows=data.checks.filter(c=>f==='all'||(f==='posted'?c.status==='posted':c.status!=='posted'));$('#summary').textContent=`${data.checks.length} checks · ${data.checks.filter(x=>x.status==='posted').length} posted`;$('#checks').innerHTML=rows.length?rows.map(card).join(''):'<div class="card empty">No checks in this view.</div>'}function card(c){let posted=c.status==='posted';let matches=(c.customer_matches||[]).map(m=>`<label class="match"><input type="checkbox" class="cust-${c.id}" value="${esc(m.id)}" ${(c.selected_customers||[]).some(x=>x.id===m.id)?'checked':''} ${posted?'disabled':''}><span><b>${esc(m.name)}</b>${m.company&&m.company!==m.name?` · ${esc(m.company)}`:''} <span class="pill ${m.remembered?'good':''}">${m.remembered?'remembered':Math.round((m.score||0)*100)+'%'}</span></span></label>`).join('');let invoices=c.invoices||[];let sugg=new Map(((c.suggestion||{}).allocations||[]).map(a=>[a.invoice_id,a.amount]));let invHtml=invoices.length?`<table><thead><tr><th>Invoice</th><th>Date</th><th>Customer</th><th class="num">Open</th><th class="num">Late fee</th><th class="num">Apply</th></tr></thead><tbody>${invoices.map(i=>`<tr><td>${esc(i.doc_number||i.id)}</td><td>${esc(i.txn_date)}</td><td>${esc(i.customer_name)}</td><td class="num">${money(i.balance)}</td><td class="num">${i.late_fees?`<span class="pill warn">${money(i.late_fees)}</span>`:'—'}</td><td class="num"><input class="alloc alloc-${c.id}" data-invoice="${esc(i.id)}" data-customer="${esc(i.customer_id)}" data-doc="${esc(i.doc_number)}" value="${sugg.has(i.id)?Number(sugg.get(i.id)).toFixed(2):''}" ${posted?'disabled':''}></td></tr>`).join('')}</tbody></table>`:'<div class="muted" style="margin-top:10px">No open invoices loaded.</div>`;let pay=(c.payments||[]).map(p=>`QB Payment ${esc(p.payment_id)} · ${money(p.amount)}`).join('<br>');return `<section class="card check ${posted?'posted':''}" id="${esc(c.id)}"><div class="titleline"><div><b>${esc(c.payer||'Unnamed payer')}</b> <span class="pill ${posted?'good':c.status==='ready'?'good':'warn'}">${esc(c.status)}</span><div class="muted">${esc(c.date||'No date')}${c.check_number?` · Check #${esc(c.check_number)}`:''}</div></div><div class="money">${c.amount==null?'—':money(c.amount)}</div></div>${c.ocr_error?`<div class="warning">AI read failed: ${esc(c.ocr_error)}. Enter the fields manually.</div>`:''}<div class="grid" style="margin-top:12px"><div class="f wide"><label>Payer</label><input id="payer-${c.id}" value="${esc(c.payer)}" ${posted?'disabled':''}></div><div class="f"><label>Date</label><input type="date" id="date-${c.id}" value="${esc(c.date)}" ${posted?'disabled':''}></div><div class="f"><label>Amount</label><input id="amount-${c.id}" value="${c.amount??''}" ${posted?'disabled':''}></div></div><div class="toolbar" style="margin-top:10px">${posted?'':`<button class="btn" onclick="saveCheck('${c.id}')">Save fields</button><button class="btn" onclick="match('${c.id}')">Find QuickBooks client</button><button class="btn danger" onclick="delCheck('${c.id}')">Delete</button>`}</div>${posted?`<div class="success" style="margin-top:12px"><b>Posted</b><br>${pay||''}</div>`:`${matches?`<div style="margin-top:14px"><b>Customer match</b>${matches}<button class="btn" onclick="confirmCustomers('${c.id}')">Confirm selected client(s) & remember</button></div>`:''}${invHtml}${c.suggestion?.reason?`<div class="muted" style="margin-top:8px">Suggestion: ${esc(c.suggestion.reason)}</div>`:''}${c.suggestion?.late_fee_warning?'<div class="warning">The check matches the invoice before a later-added late fee. Posting the payment will leave the late fee open so it can be reviewed/corrected separately.</div>':''}${invoices.length?`<div class="toolbar" style="margin-top:12px"><button class="btn" onclick="refreshInvoices('${c.id}')">Refresh invoices</button><button class="btn primary" onclick="preparePost('${c.id}')">Approve & post payment</button></div>`:''}`}</section>`}async function saveCheck(id){try{await api(`api/check/${id}`,{method:'PATCH',body:JSON.stringify({payer:$(`#payer-${id}`).value,date:$(`#date-${id}`).value,amount:$(`#amount-${id}`).value})});await loadChecks()}catch(e){alert(e.message)}}async function match(id){try{await saveCheck(id);await api(`api/check/${id}/match`,{method:'POST',body:'{}'});await loadChecks()}catch(e){alert(e.message)}}async function confirmCustomers(id){let ids=[...document.querySelectorAll(`.cust-${id}:checked`)].map(x=>x.value);try{await api(`api/check/${id}/customers`,{method:'POST',body:JSON.stringify({customer_ids:ids})});await loadChecks()}catch(e){alert(e.message)}}async function refreshInvoices(id){try{await api(`api/check/${id}/refresh-invoices`,{method:'POST',body:'{}'});await loadChecks()}catch(e){alert(e.message)}}async function delCheck(id){if(!confirm('Delete this unposted check from the queue?'))return;try{await api(`api/check/${id}`,{method:'DELETE'});await loadChecks()}catch(e){alert(e.message)}}let pending=null;function preparePost(id){let c=data.checks.find(x=>x.id===id);let alloc=[...document.querySelectorAll(`.alloc-${id}`)].map(x=>({invoice_id:x.dataset.invoice,customer_id:x.dataset.customer,doc:x.dataset.doc,amount:Number(x.value||0)})).filter(x=>x.amount>0);let total=alloc.reduce((s,x)=>s+x.amount,0);if(Math.abs(total-Number(c.amount||0))>.005){alert(`Allocation is ${money(total)} but check is ${money(c.amount)}.`);return}pending={id,alloc};$('#confirmText').innerHTML=`Post <b>${money(total)}</b> from <b>${esc(c.payer)}</b> dated <b>${esc(c.date)}</b> to ${alloc.length} invoice allocation(s)?<br><br>${alloc.map(x=>`${esc(x.doc||x.invoice_id)} — ${money(x.amount)}`).join('<br>')}`;$('#postMsg').textContent='';$('#confirm').classList.add('open')}function closeConfirm(){pending=null;$('#confirm').classList.remove('open')}$('#confirmPost').onclick=async()=>{if(!pending)return;let b=$('#confirmPost');b.disabled=true;$('#postMsg').textContent='Posting…';try{let r=await api(`api/check/${pending.id}/post`,{method:'POST',body:JSON.stringify({confirm:true,allocations:pending.alloc})});$('#postMsg').innerHTML=`<div class="success">Posted ${r.payments.map(x=>'QB Payment '+esc(x.payment_id)).join(', ')}</div>`;setTimeout(async()=>{closeConfirm();await loadChecks()},900)}catch(e){$('#postMsg').innerHTML=`<div class="error">${esc(e.message)}</div>`}finally{b.disabled=false}};qboStatus();loadChecks();</script></body></html>'''

if __name__ == "__main__":
    app.run("0.0.0.0", int(os.environ.get("PORT", "8000")), debug=True)
