# Guided Display Ad workflow

Authorized scope: implement all eight usability suggestions and supporting reliability improvements; test and deploy. Preserve unrelated shared-checkout work. Work branch: codex/guided-display-ad-workflow.

## Acceptance checklist

- [x] Client 360 Create display ads action, prefilled identity/website/verified email and brand defaults.
- [x] Brief → Design → Review → Send navigation with a primary next action and advanced settings collapsed.
- [x] Brief captures goal, offer, destination and purchased platforms with explicit defaults.
- [x] Review puts problem sizes first, counts readiness, and links directly to copy/crop/replacement editing.
- [x] Serialized autosave, visible failures/retry, local draft restoration and existing conflict recovery.
- [x] Guided GHL proof email preview with verified recipient, subject, body, immutable proof link and duplicate-send protection; no real test email.
- [x] Public immutable versioned client proof with approve/request changes per size and stale-artwork refusal.
- [x] Campaign timeline and next responsible party, including proof send and client decisions.
- [x] Recoverable review/render/packaging jobs and duplicate-delivery protection.
- [x] Fictional campaign regression, email failure/concurrency, autosave and email-screen interaction tests.
- [ ] Final CI checks, merge, deployment and live verification.

Existing functionality to reuse: Hub ad_builder_link start_project and client filing; Node jobs disk recovery; campaign revision and artifact fingerprints; contact-sheet capture; Client 360 GHL contact mapping; existing proof/download routing. Direct email sending is newly authorized to implement, but recipient-specific real email sending is not authorized for testing.
