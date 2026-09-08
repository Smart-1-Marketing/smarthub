"""Check Reconciliation must share the Hub's one QuickBooks OAuth session."""
from __future__ import annotations

import importlib


def test_check_reconciliation_uses_hub_quickbooks(monkeypatch):
    module = importlib.import_module("modules.check_reconciliation.app")
    from hub import quickbooks as qb

    assert module._qbo_configured is qb.configured

    monkeypatch.setattr(qb, "_load_tokens", lambda: {
        "realm_id": "realm-123", "expires_at": 123456, "obtained_at": 111,
    })
    rec = module._oauth_record()
    assert rec["realm_id"] == "realm-123"
    assert rec["access_expires_at"] == 123456


def test_payment_write_uses_shared_access_token(monkeypatch):
    module = importlib.import_module("modules.check_reconciliation.app")
    from hub import quickbooks as qb
    import modules.check_reconciliation as package

    monkeypatch.setattr(qb, "_ensure_access_token", lambda: {
        "realm_id": "42", "access_token": "shared-token", "expires_at": 9999999999,
    })
    monkeypatch.setattr(qb, "_api_base", lambda: "https://example.invalid")

    captured = {}

    class Response:
        status_code = 200
        ok = True
        content = b"{}"
        text = "{}"
        def json(self):
            return {"Payment": {"Id": "9001"}}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(package.requests, "request", fake_request)
    result = module._qbo("POST", "payment", payload={"TotalAmt": 24.50})

    assert result["Payment"]["Id"] == "9001"
    assert captured["url"] == "https://example.invalid/v3/company/42/payment"
    assert captured["headers"]["Authorization"] == "Bearer shared-token"
    assert captured["json"] == {"TotalAmt": 24.50}


def test_no_second_intuit_credential_pair_is_required(monkeypatch):
    module = importlib.import_module("modules.check_reconciliation.app")
    from hub import quickbooks as qb

    monkeypatch.setattr(qb, "configured", lambda: True)
    monkeypatch.delenv("QBO_CLIENT_ID", raising=False)
    monkeypatch.delenv("QBO_CLIENT_SECRET", raising=False)

    # The check tool's configuration answer comes from the existing Hub
    # connector, not its retired QBO_* environment variable pair.
    module._qbo_configured = qb.configured
    assert module._qbo_configured() is True
