"""Add Check Reconciliation to the existing SmartHub dispatcher.

The historical composition stays untouched in ``wsgi_core.py``. Rather than
wrapping that application in a second DispatcherMiddleware (which makes every
existing mount look like a Hub route to linkcheck), this module walks through
the normal middleware wrappers, finds the one existing DispatcherMiddleware,
and adds one mount to it. The resulting WSGI graph is the same shape SmartHub
has always used: one dispatcher, one login guard, one middleware stack.

There is one extra accounting safeguard here. A single paper check can span
more than one QuickBooks Customer record. QuickBooks requires one Payment per
Customer, so that operation cannot be atomic. The safer posting function below
persists each successful Payment ID immediately. If a later Customer payment
fails, the check is locked in ``partial`` state instead of becoming retryable
and accidentally posting the first Payment twice.
"""

import wsgi_core as core
from modules.check_reconciliation import app as checkrec


core._MOUNT_ACTIVE["/tools/check-reconciliation"] = "tools"


def _safe_post_payment_groups(check, allocations):
    """Create customer-grouped Payments without making a partial success retryable."""
    customer_ids = {str(a.get("customer_id") or "") for a in allocations}
    if "" in customer_ids:
        raise RuntimeError("Every allocation must name its QuickBooks customer.")

    invoices = {x["id"]: x for x in checkrec._open_invoices(list(customer_ids))}
    groups = {}
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
            raise RuntimeError(
                f"Invalid allocation for {inv['doc_number']}: {amount:.2f} "
                f"against {inv['balance']:.2f} open."
            )
        groups.setdefault(cid, []).append({"invoice_id": iid, "amount": amount})

    expected = round(float(check.get("amount") or 0), 2)
    allocated = round(sum(x["amount"] for group in groups.values() for x in group), 2)
    if abs(expected - allocated) > 0.005:
        raise RuntimeError(
            f"Allocations total ${allocated:,.2f}; the check is ${expected:,.2f}. "
            "Allocate the full check before posting."
        )

    results = []
    try:
        for cid, group in groups.items():
            payload = checkrec._payment_payload(
                cid,
                check["date"],
                check.get("check_number") or "",
                group,
                check.get("payer") or "",
            )
            data = checkrec._qbo("POST", "payment", payload=payload)
            payment = data.get("Payment") or {}
            pid = str(payment.get("Id") or "")
            if not pid:
                raise RuntimeError(
                    "QuickBooks accepted the request but did not return a Payment ID. "
                    "Stop and verify QuickBooks before retrying."
                )
            result = {
                "customer_id": cid,
                "payment_id": pid,
                "amount": payload["TotalAmt"],
                "allocations": group,
            }
            results.append(result)

            # Persist the external write before attempting another external write.
            def remember(state, posted=result):
                target = checkrec._find_check(check["id"], state)
                if not target:
                    raise RuntimeError("Check record disappeared while posting.")
                saved = target.setdefault("payments", [])
                if not any(x.get("payment_id") == posted["payment_id"] for x in saved):
                    saved.append(posted)
                target["status"] = "posting"
                target["posting_updated_at"] = checkrec._now()

            checkrec._mutate(remember)
    except Exception as exc:
        if results:
            def partial(state):
                target = checkrec._find_check(check["id"], state)
                if target:
                    target["status"] = "partial"
                    target["partial_error"] = str(exc)[:800]
                    target["posting_updated_at"] = checkrec._now()
            checkrec._mutate(partial)
            checkrec._audit(
                "payment_partial",
                check_id=check["id"],
                payment_ids=[x["payment_id"] for x in results],
                error=str(exc)[:500],
            )
            raise RuntimeError(
                "QuickBooks posted part of this check before a later customer payment failed. "
                "The successful Payment ID(s) were saved and this check is locked to prevent "
                "a duplicate. Review the partial record before any manual completion. "
                f"Original error: {exc}"
            ) from exc
        raise
    return results


# The route in the module resolves this global at request time, so replacing it
# here gives the live module the partial-write protection without duplicating
# any of the UI or OAuth implementation.
checkrec._post_payment_groups = _safe_post_payment_groups


def _dispatcher(application):
    """Return SmartHub's existing DispatcherMiddleware through its wrappers."""
    current = application
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if hasattr(current, "mounts") and isinstance(getattr(current, "mounts"), dict):
            return current
        current = getattr(current, "app", None)
    raise RuntimeError("SmartHub DispatcherMiddleware was not found")


_dispatcher(core.application).mounts["/tools/check-reconciliation"] = core._mount(
    checkrec.app, "/tools/check-reconciliation"
)

application = core.application
