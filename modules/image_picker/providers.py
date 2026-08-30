"""Free stock photo providers for the client picker -- Pexels, Pixabay, Unsplash.

This file used to carry its own copy of the three adapters, the fan-out, the
cache and the Unsplash download ping. It is now a thin delegation to
`hub/stock_search.py`, which is the shared implementation.

That is the opportunistic-migration rule in `CLAUDE.md`, and this module is the
case it was written about: the same three adapters existed here and in
`modules/image_creator/photo_search.py`, and when the Pexels key turned out to
be spelled `PEXELS_API` rather than `PEXELS_API_KEY` the fix had to be made
twice. Keeping a third copy for the stock photo search tool would have made it
three times. The shared version also reads its keys through `hub/config.py` at
call time rather than `os.environ` at import, which is what the high-severity
`provider_key_drift` check exists to enforce.

**The picker deliberately searches the three web providers only.** It is the
client-facing gallery -- a client opens it on a token link and picks photos for
their own site -- and the Hub's own library folders ("General Stock Photos",
"Smart 1 Ads") are internal agency stock. Those belong in the staff tool at
`/tools/stock-photos`, not in front of a client. Passing `sources` explicitly
here rather than taking the shared default means a source added to the shared
module later cannot silently appear in a client's picker.

The normalised result shape is unchanged, so callers and the gallery template
need no edit.
"""

from __future__ import annotations

from typing import Any

from hub.stock_search import (            # noqa: F401  (re-exported surface)
    THIRD_PARTY,
    any_provider_configured,
    configured_providers,
    search_pexels,
    search_pixabay,
    search_unsplash,
    trigger_unsplash_download,
)
from hub import stock_search as _shared


def search(
    queries: list[str],
    *,
    per_page: int = 12,
    page: int = 1,
    orientation: str | None = None,
    negatives: list[str] | None = None,
    limit: int = 36,
    seed: int | None = None,
) -> dict[str, Any]:
    """Search the three web providers for every query, in parallel.

    Returns `{"results": [...], "providers": {name: ok|error|off}, "cached":
    bool}` -- the shape this module has always returned. The shared module
    reports under `sources` and covers our own library too; both are narrowed
    to the three providers here so the picker's contract does not move.
    """
    found = _shared.search(
        queries,
        sources=list(THIRD_PARTY),
        per_page=per_page,
        page=page,
        orientation=orientation,
        negatives=negatives,
        limit=limit,
        seed=seed,
    )
    statuses = found.get("sources", {}) or {}
    return {
        "results": found.get("results", []),
        # "idle" is a shared-module state meaning configured but not searched
        # this time. No provider can be idle here -- all three are always asked
        # for -- but mapping it to "off" rather than passing it through keeps
        # this dict to the three values the picker's template knows.
        "providers": {p: ("off" if statuses.get(p) in (None, "off", "idle")
                          else statuses[p]) for p in THIRD_PARTY},
        "cached": bool(found.get("cached")),
    }
