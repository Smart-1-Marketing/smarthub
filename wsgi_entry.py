"""SmartHub deployed WSGI entrypoint.

The full historical composition remains in wsgi_core.py.  The accounting
reconciliation mount is layered on top in wsgi_check_reconciliation.py so the
new high-risk QuickBooks write surface stays isolated and owner-gated.
"""
from wsgi_check_reconciliation import application
