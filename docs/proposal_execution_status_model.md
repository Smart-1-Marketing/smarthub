# Proposal Execution status model

Task states: `draft`, `needs_input`, `ready`, `queued`, `running`, `blocked`, `needs_approval`, `changes_requested`, `approved`, `scheduled`, `live`, `completed`, `failed`, `cancelled`.

The important distinction is between an artifact being generated and a campaign being active. Background generation can end at `needs_approval`. A human launch packet can be `approved` and still not be `live`. Only an explicit recorded external action moves a handoff to `scheduled`, `live`, or `completed`.

Unrelated branches continue when one task is blocked, needs approval, or fails. Dependency checks apply only to descendants of that task.
