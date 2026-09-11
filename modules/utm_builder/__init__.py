"""Smart 1 Hub — UTM Builder package.

The Unassigned Traffic Resolver is intentionally exposed from this already-
mounted attribution tool. That keeps the resolver behind the same Hub login
and avoids adding another DispatcherMiddleware mount just to diagnose the
traffic that the UTM Builder is meant to prevent.
"""
from . import app as _utm_app
from modules.unassigned_traffic import app as _resolver


def _install_resolver_routes() -> None:
    app = _utm_app.app
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    registrations = (
        ("/unassigned-traffic/", "unassigned_traffic_page", _resolver.page),
        ("/unassigned-traffic/api/clients", "unassigned_traffic_clients", _resolver.api_clients),
        ("/unassigned-traffic/api/analyze", "unassigned_traffic_analyze", _resolver.api_analyze),
    )
    for rule, endpoint, view in registrations:
        if rule not in rules:
            app.add_url_rule(rule, endpoint=endpoint, view_func=view, methods=["GET"])


_install_resolver_routes()
