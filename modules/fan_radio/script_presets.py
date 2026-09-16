"""Compatibility exports for the shared reusable-script library.

The library is `hub/radio_presets.py` now, so the Radio Ad Creator offers the
same saved reads rather than a second copy of them. It moved because this was
the only implementation and **no screen in either tool reached it** — four
default reads, a store, a route and a test, and nothing a rep could press.

Re-exported under the names this module answered to, the same arrangement
`delivery.py` uses over `hub/radio_delivery.py`. Presets saved under this
module's own file are still read: see the shared module for why they are read
rather than migrated.
"""
from hub.radio_presets import (DEFAULTS, MAX_NAME, MAX_ROWS,  # noqa: F401
                               MAX_SCRIPT, PLACEHOLDER, custom, fill,
                               generalize, library, save)
