"""The source adapters, and the one contract they share.

Every adapter exposes two functions:

    probe(lat, lon, location_type) -> list[dict]
        Discovery. What exists near this point, each candidate as a source
        proposal: {"key", "adapter", "label", "config", "found", "detail",
        "distance_mi"}. Runs concurrently in the builder; never raises.

    fetch(config) -> dict
        The scheduled pull for one provisioned source, returning the
        normalized payload the tiles read. Raises SourceError on any failure
        so the store can record it against the source -- a failure that comes
        back looking like data is the one shape this may not take.

That symmetry is what makes the Cam Builder possible: discovery is simply
every adapter's probe run at once.

The registry below maps the adapter name stored on a `camhub_sources` row to
its module. A name not in it is refused by name rather than silently skipped.
"""
from __future__ import annotations

from importlib import import_module

from .http import SourceError, get_json, get_text  # noqa: F401 -- re-exported

ADAPTERS = ("nws", "usgs", "beachguard", "ndbc", "coops", "astro", "scrape")


def adapter(name: str):
    """The adapter module for a stored name. Raises SourceError for a stranger."""
    if name not in ADAPTERS:
        raise SourceError(f"unknown adapter '{name}'")
    return import_module(f"{__name__}.{name}")


def fetch(name: str, config: dict) -> dict:
    return adapter(name).fetch(dict(config or {}))


def probe_all(lat: float, lon: float, location_type: str = "inland_lake",
              names=ADAPTERS) -> list[dict]:
    """Every adapter's probe, concurrently. Never raises: an adapter that
    fails contributes one "not found" row naming the error."""
    from concurrent.futures import ThreadPoolExecutor
    results: list[dict] = []

    def run(name):
        try:
            return adapter(name).probe(lat, lon, location_type)
        except Exception as exc:  # noqa: BLE001 -- one probe must not sink the run
            return [{"key": name, "adapter": name, "label": name, "found": False,
                     "config": {}, "detail": f"probe failed: {type(exc).__name__}: {exc}"}]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for rows in pool.map(run, list(names)):
            results.extend(rows or [])
    return results
