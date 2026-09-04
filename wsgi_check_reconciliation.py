"""Add Check Reconciliation to the existing SmartHub dispatcher.

The historical composition stays untouched in ``wsgi_core.py``.  Rather than
wrapping that application in a second DispatcherMiddleware (which makes every
existing mount look like a Hub route to linkcheck), this module walks through
the normal middleware wrappers, finds the one existing DispatcherMiddleware,
and adds one mount to it.  The resulting WSGI graph is the same shape SmartHub
has always used: one dispatcher, one login guard, one middleware stack.
"""

import wsgi_core as core
from modules.check_reconciliation import app as checkrec


core._MOUNT_ACTIVE["/tools/check-reconciliation"] = "tools"


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
