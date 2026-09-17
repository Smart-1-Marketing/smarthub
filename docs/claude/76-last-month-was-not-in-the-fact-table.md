# Last month was not in the fact table

Every native pull reads a trailing window — thirty days for the Trade Desk,
Google Ads, StackAdapt and GroundTruth, fourteen for Microsoft Ads and
AudioGo — so the reporting fact table starts the day the first pull ran.
A report for last month, or this month against last, had nothing to read.

## Thirty days further back, through the same pull

Every pull but Amazon DSP computes its window as the `days` before `today`
(`start = today - timedelta(days=...)`), and every one takes `today` as an
argument. So `pull(days=30, today=end)` with `end` the day before the oldest
native day on file reaches thirty days further back — through the same
parser, the same upsert, the same campaign map, the same watermark. Nothing
new was written for those platforms; `modules/reports/backfill.py` only picks
the window and moves a ledger.

The Trade Desk is the exception. MyReports cannot be asked for a window on
the spot: it runs a schedule and delivers a file later. `ttd.pull_window()`
creates a one-off schedule for the window (`ReportDateRange` Custom,
`ReportFrequency` Once, partner-wide like the daily one, on the daily
schedule's template) and answers **pending**; the next run finds it by name,
reads its completed execution, lands the rows and answers with the count.
The window is pending for about a day and the ledger does not move until the
file lands. The nightly watermark is not stamped by a history landing.

Amazon DSP is not wired. Its reports are asynchronous and the report ids are
carried between nightly ticks in the module's own note; a second reader of
that note would collect the night's reports. The History card says so
instead of offering a button that does nothing.

## The ledger, and what "complete" means

`reports/backfill.json`, one entry per platform. `reached` is the first day
of the earliest window pulled, so the next window ends the day before it;
with no entry it is seeded from the fact table's oldest **native** day, not
the oldest day from any source — StackAdapt has CSV rows from 2017, and a
window before those would be a window the API was never asked for.

A window that lands nothing marks the platform **complete**: it has no
history before `reached`, and nightly stops. The button still works, and a
window that lands rows again clears the flag. An error or a pending file
leaves `reached` where it was, so the next run asks for the same window.

## Nightly, and the button

`job_reports_backfill` in `hub/scheduler.py` is checked hourly, runs in
`BACKFILL_WINDOW_HOURS` (4 to 8 AM Eastern, after the 3 AM pull has had its
turn), and pulls one more window for every platform whose `nightly` flag is
on and which is not complete and has not run that day — `last_day` in the
ledger, the run's own day, is what "once a night" is counted in. Outside the
window it says what is waiting. It is on the background lane
(`docs/claude/75`): a window is a full pull's worth of API calls.

The History card on the Reports index has, per platform, the oldest day on
file from any source, how far back the platform's own API has been read, the
next window, the state, the last run, a nightly toggle and **Pull 30 more
days**; and for all platforms at once, the pull and both toggles.
`POST /reports/backfill/<platform>` runs the job with `force=True` for that
platform on the lane, whatever the hour and whatever the flag says.

## What is still ahead

Two things Todd named for the next phase, not done here: the fact table's
indexes for the reports and pacing reads once it carries years rather than
weeks, and a backup of the landed rows so none of this has to be pulled
twice. The database mirror (`hub/jsonstore`) covers the ledger; it does not
cover the fact table.

`test_reports_backfill.py` holds the window, the ledger, the pending file,
the nightly selection and the buttons; `test_reports_ttd.py` runs a window
against the stand-in platform and reads the file back on the third call.
