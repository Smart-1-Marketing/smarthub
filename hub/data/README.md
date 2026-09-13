# Data files bundled here

## `zip_centroids.csv`

What `hub/zip_geo.py` measures a radius against, instead of asking a model
to guess. 42,354 rows, one per US ZIP Code, each carrying its centroid
(latitude/longitude) and nothing else.

**Source.** Pulled from
`https://raw.githubusercontent.com/midwire/free_zipcode_data/master/all_us_zipcodes.csv`
on 2026-09-11, which is itself assembled from public USPS/Census ZIP Code
data — geographic centroids are facts, not a creative work, so this carries
no license restriction beyond attribution to where it was pulled from. The
original file also carries city, state, county and area code columns;
those are dropped here because `hub/zip_geo.py` only ever needs the
centroid, and a smaller file is a smaller thing to review and commit.

**Checksum, as committed:**
`sha256:6b1584c5a4d5d28599efed18cfd25dda92b7ca267702b6b982db1acda83483c1`
(`shasum -a 256 hub/data/zip_centroids.csv`). If this stops matching without
a commit that says why, something modified the file outside the refresh
script below.

**This is a point-in-time snapshot, not a live feed.** USPS retires and
issues ZIP Codes continuously — slowly enough that this file does not need
refreshing on any schedule, but a newly-issued ZIP Code (a new development,
a new PO Box range) will not appear here until the file is regenerated.
`hub/zip_geo._load()` degrades to an empty table rather than raising if the
file is ever missing or unreadable, and `lookup_radius()` reports that as
"the ZIP Code table could not be read" rather than a silent zero.

**To refresh it:** run `tools/refresh_zip_centroids.py` from the repo root.
It re-pulls the same source, trims it to `zip,lat,lon`, sorts it, and
overwrites this file — printing the new checksum to paste into this note.
Re-run `python3 test_zip_geo.py` afterward: it pins the real Bristol, CT
case (23 ZIP Codes within 10 miles) against whatever ships in this file, so
a bad refresh is caught before it reaches a proposal.
