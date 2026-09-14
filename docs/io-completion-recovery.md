# IO completion and recovery

The completion check runs the actual JavaScript editor, calculations, PDF
payloads and submission flow against Flask and ReportLab. Only external
Cloudinary/Suite delivery is replaced. It covers an interrupted PDF upload,
restored draft, confirmed receipt replay, changed answers, an edit during PDF
generation, and updates to the same stored order and Suite opportunity.

PDF URLs are reused only when their saved input matches current answers.
Delivery keeps a stable request and a durable per-order reservation on the
shared Hub data disk (`io_delivery/attempts.sqlite3`). Multiple workers cannot
send the same order concurrently. Confirmed receipts remain replayable even
after later revisions. Recorded-only responses can be retried, and keep the
draft; ambiguous Suite failures remain blocked for administrator reconciliation
instead of blindly creating another opportunity. There is no automatic expiry
or browser override for an uncertain external write. Older API clients without
a request ID retain their existing behavior.

Draft status distinguishes local saves, last account saves, and failed saves.
Late responses cannot resurrect cleared drafts or attach an older draft to a
new order. Starting another order continues to preserve the old server draft.

The pre-submit checklist links required fields, dates/budgets, fees and pending
creative checks to their editor controls. AI prompts separate supplied facts,
assumptions and recommendations, and ask only unanswered questions.

Validation: `test_io_completion.py`, `test_io_delivery.py`,
`test_io_recovery_ui.js`, existing IO media-mix, PDF, builder and template checks.
All new checks run in the existing CI gate. Tests use temporary stores and
synthetic data; they never submit a production IO.
