"""hub/zip_geo.py — the ZIP-radius lookup, tested against the real data file.

    python3 test_zip_geo.py

Same shape as the other standalone test files: no pytest, no new
dependencies, and this one touches no database at all — it is pure geometry
over a bundled CSV.

## Why this file exists

The Proposal Builder's and IO Builder's "Find the ZIP Codes this radius
touches" button used to ask a model to enumerate them, with a web-search tool
attached. For a 10-mile radius around Bristol, CT — which holds about 22 ZIP
Codes — it came back with 2,985. `hub/zip_geo.py` replaced that with a
measurement: geocode the origin, then walk a bundled table of ~42,000 US ZIP
Code centroids (`hub/data/zip_centroids.csv`) and keep whatever is within the
radius.

**The regression this file exists to catch is a silent one.** The obvious way
to test this module is to compute the "expected" answer by calling the same
function being tested — which proves the function agrees with itself and
proves nothing about whether the *data* is still good. A future edit to
`zip_centroids.csv` (a bad refresh, a truncated file, a corrupted row) could
change what Bristol, CT returns and every test built that way would still
pass, because it would recompute a new "expected" value from the same broken
file. So the number that matters — the real, reported bug and its fix — is
pinned here as a literal: 23 ZIP Codes within 10 miles of Bristol, CT
(41.681198, -72.939577), against the file as it ships in this repo. If that
ever stops being true, this is the one place that says so.

The rest of the file is the ordinary edges: a radius of zero or less, a
missing or unreadable centroid file, a malformed row inside an otherwise good
one, and the two states `lookup_radius` can answer with when there is nothing
to measure against.
"""
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


# ---------------------------------------------------------------------------
section("the real bug, pinned against the real, shipped data file")
# ---------------------------------------------------------------------------
from hub import zip_geo                                            # noqa: E402

# Bristol, CT — the case from the report. Not computed from the function
# under test: a literal, so a future change to the data file that quietly
# breaks this case fails here rather than silently recomputing a new answer.
_BRISTOL = (41.681198, -72.939577)
_bristol_zips = zip_geo.zips_within_radius(*_BRISTOL, 10)
check("Bristol, CT at 10 miles returns 23 ZIP Codes, not 2,985",
      len(_bristol_zips) == 23, len(_bristol_zips))
check("and it is the real neighborhood, not just the right count",
      {"06010", "06013", "06032"} <= set(_bristol_zips), _bristol_zips)
check("sorted ascending, five-digit strings",
      _bristol_zips == sorted(_bristol_zips)
      and all(len(z) == 5 and z.isdigit() for z in _bristol_zips))

# A radius one tenth the size returns strictly fewer ZIP Codes, not the same
# list — catches a lookup that silently ignores the radius argument.
_small = zip_geo.zips_within_radius(*_BRISTOL, 1)
check("a smaller radius returns a strict subset",
      set(_small) < set(_bristol_zips) and len(_small) < len(_bristol_zips),
      (_small, len(_bristol_zips)))
check("the origin's own ZIP Code is always in its own radius",
      "06010" in _small, _small)


# ---------------------------------------------------------------------------
section("a radius of zero or less measures nothing")
# ---------------------------------------------------------------------------
check("a zero radius returns no ZIP Codes",
      zip_geo.zips_within_radius(*_BRISTOL, 0) == [])
check("a negative radius returns no ZIP Codes",
      zip_geo.zips_within_radius(*_BRISTOL, -5) == [])


# ---------------------------------------------------------------------------
section("a missing or unreadable centroid file is empty, not an exception")
# ---------------------------------------------------------------------------
_real_path = zip_geo._CSV_PATH
_real_centroids = zip_geo._centroids
try:
    zip_geo._centroids = None
    zip_geo._CSV_PATH = "/nonexistent/path/does-not-exist.csv"
    check("a missing file returns an empty table rather than raising",
          zip_geo._load() == [])
    check("and the radius lookup returns nothing rather than raising",
          zip_geo.zips_within_radius(*_BRISTOL, 10) == [])
finally:
    zip_geo._CSV_PATH = _real_path
    zip_geo._centroids = _real_centroids


# ---------------------------------------------------------------------------
section("a malformed row costs itself, not the rest of the file")
# ---------------------------------------------------------------------------
_tmp_dir = tempfile.mkdtemp(prefix="s1-zipgeo-")
_bad_csv = os.path.join(_tmp_dir, "zip_centroids.csv")
with open(_bad_csv, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["zip", "lat", "lon"])
    w.writerow(["06010", "41.681198", "-72.939577"])   # good
    w.writerow(["notazip", "not-a-number", "-72.9"])   # unparseable lat
    w.writerow(["06013"])                              # missing columns
    w.writerow(["06032", "41.7", "-72.9"])              # good

try:
    zip_geo._centroids = None
    zip_geo._CSV_PATH = _bad_csv
    loaded = zip_geo._load()
    check("the two good rows load", len(loaded) == 2, loaded)
    check("the two bad rows are dropped rather than raising or corrupting the rest",
          {z for z, _, _ in loaded} == {"06010", "06032"}, loaded)
finally:
    zip_geo._CSV_PATH = _real_path
    zip_geo._centroids = _real_centroids


# ---------------------------------------------------------------------------
section("lookup_radius: what it needs, and the two empties it can answer with")
# ---------------------------------------------------------------------------
_real_geocode = None
from hub import target_map as tmap                                 # noqa: E402


def _stub_found(origin):
    return {"lat": _BRISTOL[0], "lon": _BRISTOL[1], "label": "Bristol, CT",
            "source": "postal code"}


def _stub_not_found(origin):
    return None


_real_geocode = tmap.geocode
try:
    check("an empty origin is refused before any lookup runs",
          zip_geo.lookup_radius("", "10")["ok"] is False)
    check("an empty radius is refused the same way",
          zip_geo.lookup_radius("Bristol, CT", "")["ok"] is False)
    check("a radius that cannot parse as a number is refused, not crashed",
          zip_geo.lookup_radius("Bristol, CT", "not-a-number")["ok"] is False)

    tmap.geocode = _stub_not_found
    r = zip_geo.lookup_radius("Nowheresville, ZZ", "10")
    check("an origin the geocoder cannot place names the origin",
          r["ok"] is False and "Nowheresville" in r["error"], r)

    tmap.geocode = _stub_found
    r = zip_geo.lookup_radius("Bristol, CT", "10")
    check("a real origin and radius return the measured list",
          r["ok"] is True and r["count"] == 23, r)
    check("and carries the centroid-distance warning rather than an AI one",
          "centroid" in r["warning"] and "AI" not in r["warning"], r)
    check("and the origin's resolved label rides along",
          r["origin_label"] == "Bristol, CT", r)
finally:
    tmap.geocode = _real_geocode


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
