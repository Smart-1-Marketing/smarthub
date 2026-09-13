# Proposal sales flow improvements

Implements the six recommendations approved after live QA of Q-10215.

1. Dashboard, detail drawer, and package cards display the server-calculated all-in investment, with recurring media/services, licensing/consulting, one-time production/setup, and first-month breakdowns. PDF and Word cover figures explicitly identify media/services subtotals.
2. Review has separate delivery and IO readiness checklists. Actions open the relevant proposal step or focus the exact handoff field. Existing export, pricing, geography and conversion checks remain in place.
3. Prepare IO carries the selected package forward, lets sellers save handoff details without issuing an order, shows added/changed details and scope changes since recorded approval, and asks the seller to review client agreement before issuance. New approval snapshots are stored with the existing quote data; older quotes explicitly report that no snapshot is available.
4. A single save status reports Saving, Saved, or a failure with Retry. The footer identifies the quote without a competing autosaving claim.
5. Package cards have keyboard-accessible Select package controls and a distinct selected state. Smart 1's recommendation and selection for the proposal are labeled separately from client approval.
6. Five phases—Discovery, Strategy, Investment, Proposal, Handoff—replace the 14-button rail. The detailed-step selector retains the existing forward-navigation gates and backward access.

## Verification

- JavaScript regression tests cover package allocation/switching, complete IO investment, serialized saves, failed Finish, server investment display, readiness links and handoff comparison.
- Python regression tests cover approval snapshot preservation, scope changes, actionable checklist routing, list/package investment arithmetic, and existing export/conversion guards.
- All 25 focused Python tests passed. The broader targeting script passed its initial checks but could not boot the composed app because this Windows runtime failed to load the SQLite DLL; the Linux CI run must complete that coverage.
- Repository JavaScript syntax and template checks passed before final UI polish; final inline JavaScript regression check also passed.
- Local GPT-browser walkthrough used only a fictional, isolated SQLite proposal. Confirmed all-in dashboard values, phase navigation, contact checklist focus, saving handoff details without an IO, before/after handoff review, Essential selection, and the separate recommendation label.
- The walkthrough exposed nested table disclosure and an overlapping recommendation badge; both were corrected. Local standalone preview has no Hub favicon route; that unrelated preview-only request returned an error.
- No client messages were sent and no IO number was issued by browser testing.

CI, merge, deployment and production smoke verification are pending. No database schema migration is required.
