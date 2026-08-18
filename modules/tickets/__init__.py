"""Web Tickets audit module for Smart 1 Hub."""
from .app import bp, register_tickets  # noqa: F401

# Activity logging — so this tool's output appears on the client's
# 360 record. This module tracks client website requests, and work that isn't logged is work
# nobody can point to later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("tickets")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


__all__ = ["bp", "register_tickets"]
