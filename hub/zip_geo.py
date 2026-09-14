"""Which ZIP Codes a radius touches — measured, not guessed.

Both the Proposal Builder and the IO Builder used to ask a model to
enumerate every ZIP Code inside a radius, with a web-search tool attached and
a prompt asking it to "be exhaustive." A model is the wrong tool for a
geometry problem it has no way to check its own answer against, and it fails
in the direction that is hardest to notice: a plausible-looking, wildly wrong
count that reads as a real list. A 10-mile radius on Bristol, CT (which holds
roughly 22 ZIP Codes) came back with 2,985 — two orders of magnitude off,
sorted, comma-separated, and indistinguishable on the screen from a correct
answer.

This module answers the same question by measuring it: a bundled table of
every US ZIP Code's centroid (``hub/data/zip_centroids.csv``, ~42,000 rows)
and the great-circle distance from the origin. It is deterministic, free,
and instant, which a model call asking for "authoritative geographic
sources" was never going to be.

**What it is not.** A ZIP Code is an irregular polygon and this measures the
distance to its centroid, so a ZIP whose centroid sits just outside the
radius but whose area reaches into it is missed, and one whose centroid is
just inside but whose area extends beyond the line is still included. That
is the same approximation every centroid-based radius tool makes, and it is
named on the result rather than left implied — the warning says centroid,
not boundary, and a rep can still edit the list by hand.
"""

from __future__ import annotations

import csv
import math
import os
import threading

_CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "zip_centroids.csv")
_EARTH_RADIUS_MILES = 3958.8

_lock = threading.Lock()
_centroids: list[tuple[str, float, float]] | None = None


def _load() -> list[tuple[str, float, float]]:
    """The bundled ZIP centroid table, read once and cached in memory."""
    global _centroids
    if _centroids is not None:
        return _centroids
    with _lock:
        if _centroids is not None:
            return _centroids
        rows: list[tuple[str, float, float]] = []
        try:
            with open(_CSV_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        rows.append((row["zip"], float(row["lat"]), float(row["lon"])))
                    except (KeyError, TypeError, ValueError):
                        continue
        except OSError:
            rows = []
        _centroids = rows
        return _centroids


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    return 2 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(min(1.0, a)))


def zips_within_radius(lat: float, lon: float, miles: float) -> list[str]:
    """Every ZIP Code whose centroid is within ``miles`` of (lat, lon).

    Sorted ascending, five-digit strings. Empty if the centroid table could
    not be read — never a guess dressed up as a list.
    """
    table = _load()
    if not table or miles <= 0:
        return []
    found = [z for z, clat, clon in table
             if _haversine_miles(lat, lon, clat, clon) <= miles]
    return sorted(found)


CENTROID_WARNING = (
    "ZIP Codes are matched by distance from each ZIP's centroid to the "
    "origin, not by whether the ZIP's boundary crosses the radius line — "
    "review the edges before trafficking."
)


def lookup_radius(origin: str, radius_miles) -> dict:
    """The ZIP Codes a radius around ``origin`` touches, or why not.

    Shared by both intake tools so a fix to how this is measured lands once.
    Geocoding goes through ``hub.target_map.geocode`` — imported lazily
    because that module imports ``hub.target_areas`` at load time and this
    one must not create a cycle with it.
    """
    origin = str(origin or "").strip()
    try:
        radius = float(radius_miles)
    except (TypeError, ValueError):
        radius = 0
    if not origin or radius <= 0:
        return {"ok": False, "error": "An origin and a radius are required."}

    from hub import target_map as _map
    place = _map.geocode(origin)
    if not place:
        return {"ok": False,
                "error": f"Could not find \"{origin}\" as a US city or ZIP "
                         f"Code. Check the spelling, or enter the ZIP Codes "
                         f"by hand."}

    zips = zips_within_radius(place["lat"], place["lon"], radius)
    if not zips:
        if not _load():
            return {"ok": False,
                     "error": "The ZIP Code table could not be read on this "
                              "deployment. Enter the ZIP Codes by hand."}
        return {"ok": False,
                "error": f"No ZIP Codes have a centroid within {radius:g} "
                         f"miles of {place.get('label') or origin}. Widen "
                         f"the radius, or enter the ZIP Codes by hand."}
    return {"ok": True, "zipcodes": zips, "count": len(zips),
            "origin_label": place.get("label") or origin,
            "warning": CENTROID_WARNING}
