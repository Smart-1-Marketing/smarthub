# A half-hour job on the shared thread stalled every job behind it

The day the Trade Desk pull was finally configured, it did not run. The
scheduler was alive — the one-minute jobs were writing their rows — and the
nightly ledger said the pull was due. What the audit log showed was every
pass since the morning's deploys ending at `invoice_links`, with nothing
after it for eighteen minutes, and then a redeploy starting the pass again.

The job after `invoice_links` is the Google account index sweep. Its
docstring said seven minutes. The audit log said 1,650 to 2,140 seconds a
run — twenty-eight to thirty-six minutes on this login's 180 Tag Manager
accounts — and it ran every three hours. `hub/scheduler.py` runs its jobs one
after another on one thread, so for that half hour, eight times a day, every
job queued behind it waited: the reports pull, the pacing snapshot, the
one-minute creative sweeps. A deploy mid-sweep threw the pass away, and the
next boot started the half hour again from the top. Four deploys in an
afternoon meant the reports pull never got its turn at all.

## The background lane

`BACKGROUND_JOBS` in `hub/scheduler.py` names the long jobs — the Google
sweep and the reports pull. The loop starts each on its own thread and moves
on to the next job in the same tick. One thread per job: a start while the
last run is still going is refused, so a job never overlaps itself, whatever
pressed the button. `status()` reports `running` and `running_since` for
them, the Diagnostics panel draws a running pill and disables the button,
and `run_now` returns at once with `started` and a note rather than holding
a request open for the sweep.

A Render deploy still ends a run mid-way — the thread dies with the worker.
That is why every job is written to be safe to run again, and why the Google
sweep is no longer "every boot".

## The sweep runs overnight

`google_index` is checked hourly and sweeps once, between 1 and 6 AM
Eastern (`SWEEP_WINDOW_HOURS`), when the index is older than
`SWEEP_MIN_AGE_HOURS`. Outside the window the job says so and returns, with
the index's age and a pointer to the button; inside it a fresh index is left
alone, so a deploy at 5 AM after the 3 AM sweep does not sweep again. An
index that has never been built is built at once whatever the hour —
Client 360 reads it. **Run now** on Diagnostics passes `force=True` and
sweeps immediately, for the day that cannot wait until tonight.

## The Refresh buttons on the Reports index

Every connected native pull has a **Refresh now** button, and the card a
**Refresh all now**. `POST /reports/refresh/<platform>` (or `all`) calls
`scheduler.refresh_native()`, which runs the same `reports_native` job the
loop runs at 3 AM, narrowed to the platforms asked for, on the background
lane, under the app `start()` stashed — on whichever worker served the
click, since a one-off pull needs no leader. The 3 AM ledger is not touched:
a person refreshing at noon does not stand in for the night's run.

The note lives on the shared disk (`reports/refresh.json`, `durable=False`)
rather than in the per-process `_state`, so the worker that did not serve
the click still shows who refreshed what and when, and a refresh a deploy
killed reads as **stalled** at its start time (older than
`REFRESH_STALE_MINUTES` with no finish) rather than as running for ever. A
second click while one is running is refused, naming who started it.

`test_scheduler_health.py` holds the lane, the gate and the note;
`test_reports_ttd.py` clicks the button against the stand-in platform and
reads the note back; `test_google_index.py` sets the clock to 2 PM and 3 AM.
