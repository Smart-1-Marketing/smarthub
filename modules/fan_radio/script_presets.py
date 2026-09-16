"""Compatibility exports for the shared reusable-script library.

The library is `hub/radio_presets.py` now, so the Radio Ad Creator offers the
same saved reads rather than a second copy of them. It moved because this was
the only implementation and **no screen in either tool reached it** — four
default reads, a store, a route and a test, and nothing a rep could press.

Re-exported under the names this module answered to, the same arrangement
`delivery.py` uses over `hub/radio_delivery.py`. Presets saved under this
module's own file are still read: see the shared module for why they are read
rather than migrated.

`__all__` rather than a `# noqa` is deliberate. Every name below is unused
*here* by construction — re-exporting them is the whole job — and a
suppression comment says "ignore this warning" where `__all__` says which
names are the public surface. A reader and a linter both need the second one.
"""
from hub.radio_presets import (DEFAULTS, MAX_NAME, MAX_ROWS, MAX_SCRIPT,
                               PLACEHOLDER, custom, fill, generalize, library,
                               save)

__all__ = ["DEFAULTS", "MAX_NAME", "MAX_ROWS", "MAX_SCRIPT", "PLACEHOLDER",
           "custom", "fill", "generalize", "library", "save"]
