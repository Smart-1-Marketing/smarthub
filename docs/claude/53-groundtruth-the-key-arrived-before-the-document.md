## GroundTruth: the key arrived before the document

`modules/reports/groundtruth.py`, `modules/reports/groundtruth_map.py`,
`/reports/groundtruth-check`, and the *Store visits* tile on the client's
page. `GROUND_TRUTH_API` is set on Render, under exactly that spelling, and
every host that documents the API it unlocks -- api-docs.groundtruth.com,
reporting.groundtruth.com, docs.groundtruth.com, the help center -- is
refused by the Hub's own outbound proxy. What the search index shows of the
document is its vocabulary: campaign, ad group and creative *timeseries*
metric endpoints, by day, by day of week and by time of day. So this is the
AudioGo pattern applied to a platform whose key exists: the origin, the
path, the auth header, the date parameters and the field map are
PLACEHOLDERS in `groundtruth_map.py`, marked as such, overridable by
environment variable spelled the way the key is, and corrected from a check
page that calls the endpoint for yesterday and prints the keys it answered
with. A map that does not resolve is a refusal by name on the watermark,
never a guessed row, because a guess that matched a field of the wrong
meaning would file the wrong number under the right name.

**The origin is the one placeholder that is never used.** `missing()`
counts `GROUND_TRUTH_API_BASE` beside the key, `call()` refuses before a
request is built, and the status line says which half is owed -- *key set;
GROUND_TRUTH_API_BASE unset* is the ordinary state of this deployment, and
"not configured" alone would send somebody to check a key that is set. A
placeholder field name costs a refusal; a placeholder host would hand the
key, in a header, to whoever answers at an address nobody confirmed, on a
job that runs nightly with nobody watching. The check page prints the
guess and says nothing was called.


**Three things a review found, all of which read as a working map.** The key
half of this file is the discipline that a placeholder must be *refused* until
somebody confirms it; the failures were where that refusal did not happen.

* `check_map()` skipped a REQUIRED field whose name had been **blanked**, and
  `_dig(row, None)` answers the whole row. So an operator halfway through
  correcting the map saw *resolved*, and the pull then filed the row's own
  dict repr into `account_id` -- which is part of the fact key. The check page
  was giving a green light to the one thing it exists to refuse. A required
  field with no name is UNRESOLVED now, named by the fact column it leaves
  unfilled since there is no response field to name.
* `store.parse_date()` **raises** on a value it cannot read rather than
  answering None, so the documented per-row skip was unreachable: one
  `09/16/2026` among a thousand good rows threw out of the loop and discarded
  the entire nightly pull. Guarded, it costs that row and counts it.
* `GROUND_TRUTH_API_BASE` had no scheme check, so an `http://` origin sent
  `Authorization: Bearer <key>` in clear, nightly, with nobody watching. An
  origin that cannot carry a credential now counts as owed: the key goes
  nowhere and the sentence says why rather than calling a variable somebody
  can plainly see "unset". Loopback over http is allowed, because that is a
  person testing against a stub on their own machine.

**Visits are the figure the buy is bought for, and they never become
conversions.** A geofencing campaign is sold on store visits -- the
platform's own count of devices that saw the ad and were later observed at
the location -- and the fact table has no column for them, so they ride in
`extras` under their own name, from the pull and from the CSV door alike
(`Visits` is an alias the parser reads now). A visit is an observation the
platform makes about a device and a conversion is an action a person
takes; one figure holding both is a number nobody can explain to the client
whose page it is on. The client's page draws them as their own tile, *Store
visits*, gated on a row that actually carries one -- a geofencing row from
a provider table that reports no visits must not draw a measured nought,
the rule the completes tile already works to -- and the tile reaches
data.json and the PDF through the same list the other tiles do. The window
is thirty days rather than fourteen, because a visit is credited back to
the impression it followed and yesterday's row grows for a week after it is
first read.

**Every call is recorded**, under a `groundtruth` quota row that counts calls
and reads *not measured* against a limit until `GROUND_TRUTH_MONTHLY_LIMIT`
is set, and the marker in `_PROVIDER_MARKERS` is the domain rather than a
host, because the origin is a setting. It joins the nightly native-pull job
under the same completed-platform skip as the others, `/status` carries the
key's row, and `/diagnostics` has a check that reaches no network -- the
origin owed, configured and never pulled, the last pull's own error, or ok
-- since probing a host nobody confirmed on a page load is the one thing
this module exists not to do. `test_reports_groundtruth.py` asserts all of
it, on SQLite and again on Postgres, and the first live pull is where the
transcription is checked: set the origin, open the check page, paste the
real names into the map.

All OpenAI calls go through hub/ai.py with client= or brief=;
/api/integrity enforces it.
