"""Regenerate hub/data/zip_centroids.csv from its public source.

    python3 tools/refresh_zip_centroids.py

`hub/zip_geo.py` measures a radius against this file instead of asking a
model to guess — see `hub/data/README.md` for why and for what it is a
snapshot of. This script re-pulls the same source, trims it to the three
columns that module actually reads, sorts it, and overwrites the bundled
file. It touches nothing else: no database, no environment variable, no
network call other than the one fetch.

Run `python3 test_zip_geo.py` immediately after refreshing — it pins the
real, reported bug (Bristol, CT at 10 miles returns 23 ZIP Codes) against
whatever ships in the file, so a bad pull or a truncated download is caught
here rather than in a proposal. Paste the printed checksum into
`hub/data/README.md`.
"""
import csv
import hashlib
import io
import os
import sys
import urllib.request

SOURCE_URL = ("https://raw.githubusercontent.com/midwire/"
              "free_zipcode_data/master/all_us_zipcodes.csv")
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "hub", "data", "zip_centroids.csv")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "smarthub-zip-refresh/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def trim(raw_csv: str) -> list[tuple[str, str, str]]:
    """(zip, lat, lon) triples, deduplicated on ZIP, sorted ascending.

    Same rules `hub/zip_geo.py` applies on read: a five-digit ZIP with a
    parseable lat/lon or it is dropped, silently — the source carries a
    handful of malformed rows and a bad refresh must not carry them forward
    as something that then fails to load in production.
    """
    rows: dict[str, tuple[str, str]] = {}
    for row in csv.DictReader(io.StringIO(raw_csv)):
        z = (row.get("code") or "").strip().zfill(5)
        lat = (row.get("lat") or "").strip()
        lon = (row.get("lon") or "").strip()
        if len(z) != 5 or not z.isdigit() or not lat or not lon:
            continue
        try:
            float(lat)
            float(lon)
        except ValueError:
            continue
        rows[z] = (lat, lon)
    return [(z, lat, lon) for z, (lat, lon) in sorted(rows.items())]


def main() -> int:
    print(f"Fetching {SOURCE_URL} ...")
    raw = fetch(SOURCE_URL)
    trimmed = trim(raw)
    if len(trimmed) < 40000:
        # The live table has held ~42,000 rows for years; a source that
        # answers with far fewer is truncated or has changed shape, and
        # writing it over the good file would be worse than refusing.
        print(f"Refusing to write: only {len(trimmed)} rows parsed, "
              f"expected roughly 42,000. Source may have changed shape.")
        return 1

    with open(OUT_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zip", "lat", "lon"])
        w.writerows(trimmed)

    checksum = hashlib.sha256(open(OUT_PATH, "rb").read()).hexdigest()
    print(f"Wrote {len(trimmed)} rows to {OUT_PATH}")
    print(f"sha256:{checksum}")
    print("Paste that checksum into hub/data/README.md, then run "
          "python3 test_zip_geo.py to confirm it still measures correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
