"""Serialize Suite delivery per order and replay completed request receipts.

An interrupted external write is deliberately not retried automatically: an
HTTP timeout cannot tell us whether Suite created the opportunity.

## This is a lock, not a store, and that is why it moved

The two tables here are not records anybody reads. They are how a duplicate
opportunity is kept out of Smart 1 Suite: `attempts` reserves an order before
the external write, so a second request for the same order is refused rather
than sent, and `receipts` replays a completed response so a retry returns the
first answer instead of delivering again.

They were a SQLite file on the Render disk, and `BEGIN IMMEDIATE` took that
file's database-wide write lock. **That serialises the two gunicorn workers
sharing one file and says nothing whatever about a second instance** -- and
the two halves of a zero-downtime deploy are two instances, each with its own
disk and its own file. So the reservation that exists to stop a duplicate
delivery was not held across the one event it most needed to be held across.
Losing the disk lost the receipts too, which turns a later retry of a
completed order back into a delivery.

On the shared database the lock is a transaction-scoped Postgres advisory
lock keyed on the order. It is released when the transaction ends, it is held
across every instance sharing the database, and it is **narrower** than what
it replaces: two reps sending two different orders no longer wait on each
other, which the database-wide file lock made them do.
"""
import hashlib
import json
import logging
import struct
from contextlib import contextmanager
from functools import wraps

from flask import jsonify, make_response, request

from hub import dbshim

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS attempts (order_id TEXT PRIMARY KEY,"
    " request_id TEXT, fingerprint TEXT, state TEXT, response TEXT,"
    " code INTEGER)",
    "CREATE TABLE IF NOT EXISTS receipts (order_id TEXT, request_id TEXT,"
    " fingerprint TEXT, response TEXT, code INTEGER,"
    " PRIMARY KEY(order_id, request_id))",
)

_schema_ready = False


def _order_key(order: str) -> int:
    """A stable signed 64-bit key for one order, for the advisory lock.

    Derived here rather than with Postgres's `hashtext()`, which is an
    internal function with no compatibility promise -- a lock key that
    silently changes meaning between server versions would stop two callers
    excluding each other while every screen looked fine.
    """
    digest = hashlib.sha256(order.encode("utf-8")).digest()
    return struct.unpack(">q", digest[:8])[0]


@contextmanager
def _connect():
    global _schema_ready
    if not _schema_ready:
        # Under the advisory lock: CREATE TABLE IF NOT EXISTS is not atomic
        # against a second worker, which on Postgres is a duplicate key on
        # pg_type_typname_nsp_index rather than a no-op.
        from hub import extensions

        def _create():
            with dbshim.connect() as con:
                for statement in _SCHEMA:
                    con.execute(statement)

        err = extensions.create_all_sql(_create, retry=False)
        if err:
            raise RuntimeError(f"the delivery tables could not be created: {err}")
        _schema_ready = True
    with dbshim.connect() as db:
        yield db


def _lock_order(db, order: str) -> None:
    """Hold one order against every other worker **and every other instance**.

    `BEGIN IMMEDIATE` is what this replaces on Postgres, and the difference is
    the whole point of the move: SQLite's write lock covers one file, so it
    never spanned the two instances a zero-downtime deploy runs. The advisory
    lock is scoped to the transaction and released when it ends, including on
    a crash, so a worker that dies holding it does not wedge the order.

    The SQLite branch is the fallback, and there `BEGIN IMMEDIATE` is still
    the right answer -- one file, one process pool.
    """
    if db.dialect.startswith("postgres"):
        db.execute("SELECT pg_advisory_xact_lock(?)", (_order_key(order),))
    else:
        db.execute("BEGIN IMMEDIATE")


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
                _lock_order(db, order)
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
                # Named columns and an explicit upsert: INSERT OR REPLACE is
                # SQLite's own spelling and Postgres has no such statement.
                # ON CONFLICT DO UPDATE is the one form both take.
                db.execute(
                    "INSERT INTO attempts (order_id,request_id,fingerprint,state,response,code)"
                    " VALUES (?,?,?,'pending',NULL,NULL)"
                    " ON CONFLICT (order_id) DO UPDATE SET"
                    " request_id=excluded.request_id,"
                    " fingerprint=excluded.fingerprint,"
                    " state='pending', response=NULL, code=NULL",
                    (order, token, fingerprint))
            response = make_response(fn(*args, **kwargs))
            body = response.get_json(silent=True) or {}
            # A recorded-only response confirms that no external delivery
            # happened. It can safely be retried after configuration/contact
            # details are corrected. A gateway failure is not that assurance.
            state = "complete" if body.get("ok") and body.get("delivered_to_suite") else "retryable" if response.status_code < 500 else "uncertain"
            with _connect() as db:
                db.execute("UPDATE attempts SET state=?,response=?,code=? WHERE order_id=? AND request_id=?", (state, json.dumps(body), response.status_code, order, token))
                if state == "complete":
                    # DO NOTHING rather than a bare insert: a duplicate here
                    # used to raise, get caught by the handler below and answer
                    # 503 about a delivery that had in fact succeeded.
                    db.execute(
                        "INSERT INTO receipts (order_id,request_id,fingerprint,response,code)"
                        " VALUES (?,?,?,?,?)"
                        " ON CONFLICT (order_id, request_id) DO NOTHING",
                        (order, token, fingerprint, json.dumps(body), response.status_code))
            return response
        except Exception:
            logging.getLogger(__name__).exception("IO delivery reservation or receipt failed")
            # Never send if reservation cannot be persisted. If the external
            # write already ran, its pending reservation protects later retries.
            return jsonify(ok=False, error="Delivery could not be safely confirmed. Your PDFs are saved; check the order in Smart 1 Suite before retrying."), 503
    return wrapped
