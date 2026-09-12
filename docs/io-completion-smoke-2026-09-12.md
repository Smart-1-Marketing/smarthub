# IO completion smoke test — September 12, 2026

Completed the production IO Builder in the signed-in in-app browser with synthetic order **10216**, client **SMART1 QA TEST 10216 — DO NOT TRAFFIC**. No campaign launch or media purchase was requested. Test instructions prohibit trafficking, billing, launching, or contacting the synthetic client.

## Result

- Completed the question flow, edited the display budget from $350 to $700, and verified recalculation to 200,000 impressions at $3.50 CPM for October 1–31, 2026 in Ohio.
- The below-minimum budget warning appeared at $350. The intentionally waived management fee required an explicit override.
- At 22:29 UTC, Activity Log recorded `io_submitted` with `delivered: true` for order 10216. The original page also confirmed delivery to Smart 1 Suite with both Cloudinary PDF links.
- Client360 registered the synthetic client and listed order 10216 with the correct dates, $700 amount, and both document links. No duplicate submission was performed.
- Downloaded and inspected both saved PDFs. Both contain order 10216, the restored sales email, correct dates, and $700 monthly/campaign amounts. The client document is two pages; the internal document is three pages.

## Fixes in this change

- Secondary conversions can be left empty; the prompt explicitly explains how to continue without selecting one. Required product selection remains enforced.
- Final submission review uses an accessible page dialog with Keep editing and an explicit submission button. Escape cancels, focus returns to the trigger, duplicate clicks are ignored, and warning overrides still require approval.
- Help bubbles render bold emphasis and line breaks after escaping source HTML.

## Validation

- Browser preview: review layout, cancel, Escape, and approval without network submission.
- `node test_io_media_mix.js`: optional/required answers, review cancel/Escape/duplicate/approval, explicit warning override, both PDFs before submission, escaped help, and existing media-mix checks.
- `test_io_template.py`: mounted and standalone rendering.
- `test_io_media_mix.py`: 13 tests passed.
- `test_io_builder.py`: 71 checks passed.

## Remaining observations

- Internal landing-page analysis prints Markdown heading/emphasis markers literally in the PDF; improve its rendering separately.
- The sales-email editor appeared empty once despite the summary containing the email. Restored the value before saving, and verified it in both final PDFs. No reliable reproduction or root cause established.
- Review automatic secondary KPI suggestions for relevance to the selected product; video/audio completion suggestions appeared during a display-only order.
- Client requirements repeat some creative intake requests; consolidate those prompts in a later content pass.
