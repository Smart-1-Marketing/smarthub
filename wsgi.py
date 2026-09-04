"""SmartHub deployed WSGI entrypoint.

The full historical SmartHub composition is kept in wsgi_core.py. The private
QuickBooks Check Reconciliation mount is layered on top by
wsgi_check_reconciliation.py.
"""
from wsgi_check_reconciliation import application
