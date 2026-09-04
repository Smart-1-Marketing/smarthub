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

from hub.coverr import BASE_URL, _app_id, _key, _mock_results, is_live, search  # noqa: F401
