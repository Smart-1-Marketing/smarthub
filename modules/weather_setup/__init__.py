"""Weather Trigger Setup — a lead becomes a client-approved, weather-driven
ad campaign.

Two blueprints, one module. `bp_wx`, mounted at `/wx` with no login gate, is
the short link a rep hands a prospect: `smart1.agency/wx/<token>` walks them
through picking up to three weather triggers, approving the copy and the
image for each, and getting a work order number back. `bp_staff`, mounted at
`/tools/weather-setup` and gated by `hub/blueprint_guard.py` the way
Commercial Builder and the two HyperFrames tools are, is where a rep starts
one from an existing lead.

See `hub/weather_triggers.py` for the trigger vocabulary and
`docs/weather-trigger-setup.md` for the full spec this was built against.
"""
from __future__ import annotations

from .app import PUBLIC_MOUNT, STAFF_MOUNT, bp_staff, bp_wx

__all__ = ["register_weather_setup", "PUBLIC_MOUNT", "STAFF_MOUNT",
           "bp_staff", "bp_wx"]


def register_weather_setup(app) -> None:
    if "weather_setup_staff" not in app.blueprints:
        app.register_blueprint(bp_staff, url_prefix=STAFF_MOUNT)
    if "weather_setup_wx" not in app.blueprints:
        app.register_blueprint(bp_wx, url_prefix=PUBLIC_MOUNT)
