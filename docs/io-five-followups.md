# IO Builder: five follow-ups

## Phase 1: documents and contact reliability

- Render escaped headings, list items, and bold in landing-page notes in the report, printable HTML, and internal PDF.
- Validate the sales email before saving any editor changes. An empty field cannot silently remove an existing email; type NONE for intentional removal. Canceling leaves answers unchanged.
- The original one-time blank editor observation has no confirmed root cause. The change guards against data loss, and regression coverage exercises the actual editor through an unrelated budget edit and reopening.

## Phase 2: focused intake and completion

- Suggest video/audio completion metrics only for relevant selected products. Keep explicitly chosen KPIs intact.
- Ask call-tracking and thank-you-page questions only when the selected conversions, goals, or products make them relevant. Normalize inapplicable old answers in PDF and submission payloads, without destroying saved answers used by Back.
- Remove repeated campaign-wide creative requests from the per-product lists. Keep shared brand, approval, targeting, and offer requirements once, plus product-specific files/access.
- Display a receipt with the submitted order/client, time, explicit Suite delivery state, and both saved PDF links. The receipt identifies the submitted version; later edits are not represented as already sent. A recorded-only response never claims delivery.

## Validation

- JavaScript tests cover the actual email editor, product/mixed-product KPI suggestions, conditional tracking, stale-answer normalization, receipt states, escaping, safe links, and submission sequencing.
- PDF/download checks: 66 passed. IO Builder checks: 71 passed. Mounted and standalone template rendering passed.
- Browser preview checked formatted notes, empty-email protection, and both receipt states. Rendered the actual ReportLab PDF and visually inspected headings, bullets, emphasis, and escaped source HTML.
- Production test IO 10216 was not resubmitted during this work. New behavior is validated locally and through CI before release.

The initial CI run also exposed two new GPT Ad Builder final/version previews absent from the shared image-preview exception registry. Both deliberately display the selected original creative. Their narrow exception is documented alongside the existing final-ad preview exception; the gallery rule remains enforced.
