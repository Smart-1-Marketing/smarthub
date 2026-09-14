"""Serialize Suite delivery per order and replay completed request receipts.

SQLite lives on the Hub data disk and coordinates the service's workers.
An interrupted external write is deliberately not retried automatically: an
HTTP timeout cannot tell us whether Suite created the opportunity.
"""
import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from functools import wraps

from flask import jsonify, make_response, request


@contextmanager
def _connect():
    from hub.jsonstore import data_dir
    db = sqlite3.connect(os.path.join(data_dir("io_delivery"), "attempts.sqlite3"), timeout=10)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (order_id TEXT PRIMARY KEY, request_id TEXT, fingerprint TEXT, state TEXT, response TEXT, code INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (order_id TEXT, request_id TEXT, fingerprint TEXT, response TEXT, code INTEGER, PRIMARY KEY(order_id, request_id))")
            yield db
    finally:
        db.close()


def protected_delivery(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        data = request.get_json(silent=True) or {}
        # Older clients retain their existing behavior; new clients always
        # supply a request id and must have an allocated order number.
        token = data.get("submissionRequestId") if isinstance(data, dict) else None
        if not token:
            return fn(*args, **kwargs)
        order = str(data.get("orderNumber") or "").strip()
        if not order or not isinstance(token, str) or len(token) > 100:
            return jsonify(ok=False, error="An order number and valid submission request are required."), 400
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            with _connect() as db:
                db.execute("BEGIN IMMEDIATE")
                receipt = db.execute("SELECT fingerprint,response,code FROM receipts WHERE order_id=? AND request_id=?", (order, token)).fetchone()
                if receipt:
                    if receipt[0] != fingerprint:
                        return jsonify(ok=False, error="This completed request changed. Review the updated order before sending."), 409
                    return jsonify(json.loads(receipt[1])), receipt[2]
                prior = db.execute("SELECT request_id,fingerprint,state,response,code FROM attempts WHERE order_id=?", (order,)).fetchone()
                if prior:
                    same = prior[0] == token
                    if same and prior[1] != fingerprint:
                        return jsonify(ok=False, error="This submission changed while delivery was in progress. Review the saved order before retrying."), 409
                    if prior[2] in ("pending", "uncertain"):
                        return jsonify(ok=False, error="Delivery is in progress or its result is uncertain. If it does not finish, ask your administrator to reconcile this order in Smart 1 Suite before resending. Automatic retry was stopped to prevent duplicates."), 409
                    if same and prior[2] == "complete":
                        return jsonify(json.loads(prior[3])), prior[4]
                db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?,'pending',NULL,NULL)", (order, token, fingerprint))
            response = make_response(fn(*args, **kwargs))
            body = response.get_json(silent=True) or {}
            # A recorded-only response confirms that no external delivery
            # happened. It can safely be retried after configuration/contact
            # details are corrected. A gateway failure is not that assurance.
            state = "complete" if body.get("ok") and body.get("delivered_to_suite") else "retryable" if response.status_code < 500 else "uncertain"
            with _connect() as db:
                db.execute("UPDATE attempts SET state=?,response=?,code=? WHERE order_id=? AND request_id=?", (state, json.dumps(body), response.status_code, order, token))
                if state == "complete":
                    db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)", (order, token, fingerprint, json.dumps(body), response.status_code))
            return response
        except Exception:
            logging.getLogger(__name__).exception("IO delivery reservation or receipt failed")
            # Never send if reservation cannot be persisted. If the external
            # write already ran, its pending reservation protects later retries.
            return jsonify(ok=False, error="Delivery could not be safely confirmed. Your PDFs are saved; check the order in Smart 1 Suite before retrying."), 503
    return wrapped
