"""Moved to `hub.industries` when the two proposal builders were consolidated.

Kept as a re-export so this module's archive endpoints (which still read
proposals saved against these industry keys) keep working without a second
copy of the table drifting out of step with the shared one.
"""
from hub.industries import (  # noqa: F401
    BLOCKS_SCHEMA,
    INDUSTRIES,
    fallback_blocks,
    industry_list,
)
