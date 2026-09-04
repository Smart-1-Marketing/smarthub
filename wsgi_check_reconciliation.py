"""Check Reconciliation mount layered over the normal SmartHub WSGI app.

This file exists so the accounting tool can be isolated from the much larger
composition module. The deployed entrypoint remains wsgi:application; wsgi.py
imports the normal composition and adds this one private mount.
"""
from werkzeug.middleware.dispatcher import DispatcherMiddleware

import wsgi_core as core
from modules.check_reconciliation import app as checkrec

core._MOUNT_ACTIVE["/tools/check-reconciliation"] = "tools"
application = DispatcherMiddleware(core.application, {
    "/tools/check-reconciliation": core._mount(
        checkrec.app, "/tools/check-reconciliation"
    ),
})
