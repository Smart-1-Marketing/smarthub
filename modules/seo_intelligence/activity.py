"""Who changed a client's Search Console, and when.

Two of this module's routes reach into somebody else's Google property and
change it -- `submit_sitemap` tells Google to crawl a file, `delete_sitemap`
withdraws one -- and a third records which property belongs to which client,
which is the join every later read here is built on. None of the three wrote
anything down, so the least attributable actions in this module were the ones
that touch a client's own account. That is the failure `hub/audit.py` already
names about deploying a tag into a Tag Manager container we do not own.

`log()` is a wrapper rather than each call site naming the module, so the
route added next month cannot file its work under a different string. It
never raises: an action Google accepted must not be reported as a failure
because a line could not be written about it.

Called only where the provider has already answered. A sitemap Google refused
is not written down as one we submitted -- the `approve_render` rule, one
provider over.
"""
from __future__ import annotations


def log(event: str, **extra) -> None:
    try:
        from hub import audit
        audit.log("seo_intelligence", event, **extra)
    except Exception:                                 # noqa: BLE001
        pass
