# CamHub hardening: CSV formulas, portal rotation, and the outbox in /health

CamHub's six sprints are shipped; this pass hardens what is running.
Nothing here is new capability; every change closes a real hazard or
puts a real state on a screen that read as green when it wasn't.

## What ships

- **CSV formula-injection guard.** A sponsor named `=SUM(A1)`, or one
  whose name starts with `+`, `-`, `@`, a tab or a carriage return, could
  execute a spreadsheet formula the moment a sponsor opens the CSV in
  Excel or Sheets (CWE-1236). `reports.csv_for_sponsor` now routes every
  person-supplied cell through `_csv_safe`, which prefixes a single
  quote on any cell that leads with one of those characters. `csv.writer`
  quotes commas and newlines; it does not defend against this.
- **Portal token rotation.** A new `portal_rotated_at` column on
  `camhub_sponsors`. Every portal token carries the second it was
  minted; `portal.read()` rejects a token whose issue time predates
  `portal_rotated_at`. A staff `/sponsors/<id>/rotate-portal` POST sets
  the cutoff to now (second precision, to keep the fresh mint from
  losing a race with itself) and hands the operator a fresh token.
  Rotation is what closes the "the sponsor forwarded their link to a
  competitor" case without touching the sponsor row.
- **Portal boot-error and rotated-link page.** `cam_missing.html` speaks
  to visitors of the live cam page; a sponsor whose link has just been
  rotated needs the message written for them. New `portal_unavailable.html`
  covers the two portal error paths (database down, token rotated or
  expired) with copy that names the portal and tells the reader that
  reload will fix the first case and that Smart 1 will send a fresh link
  for the second.
- **The outbox in `/health`.** The health endpoint now reads the top of
  the outbox and the most recent rollup timestamp: `reports` names the
  last twenty rows by status, and `rollup_age_minutes` is how long ago
  the rollup last wrote. A rollup stuck a week ago is exactly the state
  the reports screen exists to make visible; `/health` reports it too so
  a rep can look at one endpoint and know the whole pipeline.
- **The daily chart's zero-peak case** is now covered by a test.
  `_round_ceiling(0)` already returned zero without erroring and the
  chart drew a valid PNG, but a test protects the invariant against a
  future rewrite.

## Departures worth naming

The rotation cutoff is a wall-clock timestamp on the sponsor row rather
than a nonce or a per-token record. A nonce means keeping a per-sponsor
set of valid tokens, which is a database write on every mint; a
timestamp cutoff is one write on rotation and every read is a single
scalar compare. The one-second granularity of `itsdangerous`'s signed
timestamp gets handled by truncating the cutoff to the second before
storing it, so a freshly-minted token from the same second reads as
strictly not-earlier and stays valid.

`_csv_safe` prefixes with a single `'` character (the standard OWASP
recommendation) rather than wrapping the whole cell in quotes. Excel
strips the leading quote when it displays the cell, so a sponsor named
`=SUM(A1:A9)` reads as `=SUM(A1:A9)` on the page, still not-executable.
A cell wrapped in double quotes reads as a quoted string and confuses
column headers.

`/health` returns 503 when `reports.failed > 0`, in addition to the
existing "any source red" rule. That is a stricter contract: a single
failed report row on the outbox flags the whole tool. The alternative
was to only fail on source red and let a stalled outbox stay silent,
which is exactly the state a monitoring endpoint exists to catch.

## Render environment

Nothing. The rotation cutoff is a column on an already-present table
(migrations run through `create_all_metadata`); the CSV guard is
pure-Python; the portal error page is a template file; the outbox
status on `/health` reads the same tables the reports screen already
reads.
