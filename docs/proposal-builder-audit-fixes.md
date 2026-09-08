# Proposal Builder → IO audit fixes

Implemented locally following the live proposal-builder audit. Not deployed.

## Changes

- Budget allocation honors IO product minimums and preserves exact totals. Smaller packages drop unaffordable channels instead of selling invalid line items. One-time charges are preserved separately.
- Recommended matches the entered plan. Selecting another package updates the plan. Reopening the review does not scale it a second time, and switching back restores the larger option's channels.
- Package impression totals are labeled per campaign in the browser and PDF.
- IO review and payload include platform licensing, consulting and production. One-time plan charges are identified so conversion does not count them twice. Conversion checks its total against the proposal before requesting an order number.
- Creative source, quoted production and the primary KPI carry forward. Conversion captures typed answers before reviewing, validates required tracking details and rejects reversed dates.
- Failed AI sections persist, can be retried individually and block sharing/delivery. Manually edited sections clear their failure marker; content checks still apply. Creative source and product minimums are also checked server-side.
- Conversion preflight rejects a changed quote using revision and modification timestamp before issuing an IO number.
- Unknown discovery answers no longer imply missing services. Internal price language is removed from retargeting recommendations and checked in generated copy.
- Business descriptions require retrieved website text. Placeholder websites and insufficient evidence stop generation. Campaign targets are not treated as confirmed service areas.
- Proposal prompts use selected audiences and forbid invented credentials, income filters and production commitments. Default sections no longer invent an absent CRM or add industry audiences. Home Services no longer matches the RV substring.
- AI ZIP results are labeled unverified. The rep records a geographic source and verification before conversion. Editing the origin, radius, ZIPs, exclusions or source invalidates verification; its provenance survives normalization into the IO.
- Actual tracked sending drives awaiting-response counts, rather than a manually assigned Sent status or internal filing.
- PDF opening reserves the window before saving, checks generation errors and provides an explicit generated-file link. A failed save stops delivery/export.
- Choice cards and discovery answers have keyboard controls; conversion labels are associated with inputs. Step ticks require visiting and continuing through the step.

## Verification

- `test_proposal_integrity.js`: exact-cent/minimum allocations, typed answer capture, campaign delivery arithmetic, package switching and render stability, complete IO investment, and syntax of all inline JavaScript.
- `test_proposal_integrity.py`: 12 focused checks for discovery, prompt evidence, readiness, industry matching and ZIP provenance.
- `test_proposal_handoff.py`: isolated Flask/SQLite checks of share, delivery and conversion gates, stale revisions, ZIP verification, default copy and one-time charge provenance. Provider requests are disabled.
- Changed Python files compile; targeted Git whitespace checks pass.
- Local page, configuration, dashboard, quote list and static assets returned HTTP 200. Browser automation timed out before a complete visual walkthrough.

## Remaining verification and follow-up

- Re-test a complete live AI draft, both exports and final IO generation in staging before deployment. No client message or real IO was issued during implementation.
- The broad existing proposal-spec and campaign-cost scripts stalled and were stopped; they are not reported as passing. Focused regression coverage is provided above.
- ZIP lookup still needs an authoritative geographic dataset for automatic verification. The implemented source/verification gate prevents treating an AI list as verified geography; it does not establish geographic accuracy.
- A full mobile/accessibility pass and a larger wizard layout redesign remain separate work. These changes improve the existing flow and its integrity.
- Existing unrelated workspace modifications were preserved. Deploy the new `proposal-integrity.js` asset with the template and Python changes.
