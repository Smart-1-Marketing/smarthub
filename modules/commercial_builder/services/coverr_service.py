"""Coverr Video service — re-exported from hub.coverr.

The real implementation moved to hub/coverr.py once the standalone Video
Search tool (/tools/video-backgrounds) needed the same client this module
already had: two callers keeping their own copy of a provider client is the
exact drift that took Pexels off the air once already (its key was read at
one spelling in one place and fixed only there, twice). Everything is
re-exported under its old name here so this module's existing callers
(routes/stock.py, services/provider_check.py) and test_commercial_providers.py
are unchanged — the same shape modules/radio_promo/voices.py uses to
re-export hub/voice_casting.py."""

from hub import coverr

# Assigned rather than `from hub.coverr import BASE_URL, ...`: an import that
# is never referenced by name in this file reads as unused to a linter that
# does not special-case a re-export, which is exactly what flagged the
# earlier version of this line. An attribute assignment carries the same
# object under the same name -- coverr_service.search is hub.coverr.search,
# identically -- without looking like dead code to anything that checks.
#
# __all__ names every one of them anyway. The assignment alone stopped the
# "unused import" finding and traded it for "unused global variable" on the
# three names that start with an underscore -- this file never calls them,
# only services/provider_check.py does (coverr_service._key(),
# coverr_service._app_id()), and no per-file linter can see across that
# boundary. __all__ is the one signal every static-analysis tool reads the
# same way: this name is the module's public surface, not dead code.
BASE_URL = coverr.BASE_URL
_app_id = coverr._app_id
_key = coverr._key
_mock_results = coverr._mock_results
is_live = coverr.is_live
search = coverr.search

__all__ = ["BASE_URL", "_app_id", "_key", "_mock_results", "is_live", "search"]
