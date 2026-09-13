# Commercial production recovery and history

The production panel appears above the existing wizard pages. It reads saved
state every 15 seconds while the tab is visible, distinguishes media creation,
storage, render progress and problems, and links to the relevant editing step.
An old render is described as a saved cut, never as proof the current script was
rendered. Script approval is not inferred from the presence of scenes.

## Background recovery

The existing Hub scheduler runs `commercial_recovery` every minute in production.
It checks existing HeyGen and render IDs and retries saving completed presenter
clips. It never submits a new paid generation, approves a cut or files it for a
client. Jobs with ambiguous submissions and no provider ID require an operator
to check HeyGen; terminal provider failures are not automatically resubmitted.

Each batch handles at most eight jobs and checks a 45-second budget between
provider calls. A single provider call can run beyond that budget until its own
timeout. Database leases expire after ten minutes if a worker stops. Persistent
backoff grows to 30 minutes after repeated failures; new and least-recently-checked
jobs take priority so stalled jobs cannot monopolize the queue.

`HUB_SCHEDULER=0` disables background work. The production panel reports disabled
or unavailable scheduling, with manual status checks still available. The existing
`flask commercial-recover-presenters --limit 8` command uses the same queue and
respects its leases and retry times. Scheduler diagnostics expose batch results.

## Take history

New `cb_production_takes` and `cb_recovery_attempts` tables are created by the
existing startup table setup; no columns are added to existing production tables.
History records previous and newly saved scene script text, scene narration,
completed presenter takes and full narration tracks in the same transaction as
the edit. The presenter reservation also preserves older clips before its atomic
update. Repeated identical snapshots are deduplicated; transient storage failures
are not recorded as usable presenter takes.

The history panel compares saved and current versions, previews audio/video, and
restores only within the original project and scene. Restoring retains the replaced
version. A changed current version or active presenter job blocks restoration.
Media that no longer matches the script remains stale and must pass the existing
timing and media checks. Presenter restoration includes its footage placement.

History starts with edits and generation after this release. Earlier overwritten
media cannot be reconstructed. Deleted scene text remains available to copy,
but deleted scenes are not recreated automatically. Deleting a project removes
its history. Script restoration changes scene text, not the whole scene order or
timing. Restoring a take does not restore an old client approval.

Validation: `python test_commercial_reliability.py` includes synthetic-provider
tests for recovery, leases, fairness, history, conflicting edits and restoration.
`python test_scheduler_health.py` covers scheduler diagnostics.
