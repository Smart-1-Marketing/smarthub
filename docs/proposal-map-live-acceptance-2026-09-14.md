# Proposal map pagination live acceptance

## Deployment and live findings

PR #607 merged as `7ce977b27af8b800c4d6fb9a96275e6a3b5eefad`. Main `32f485ad8b1cadbf1c2dafe8d6a3685f755b2a41` contains it; application CI run `34901782933` and CodeQL run `34901782626` passed. Render deployment `dep-dak704qjnfac73etuks0` became live at 22:08:47 UTC on September 14, 2026.

Exported the existing fictional Q-10215 (quote ID 16, revision 4, draft, QA TEST — Fictional HVAC — DO NOT SEND) after deployment through its Generate / open PDF control. Inspected all seven pages in Chrome's PDF viewer:

- Page 2 leaves section 05, Audience & Market Strategy, without opening content; its map starts on page 3. This fails live pagination acceptance despite the earlier local text-only fixture passing.
- Other section headings inspected remain with opening content. All seven pages show the validity footer; the final page has body content, including Next Steps and ZIP Codes Targeted.
- Page 1 labels $9,000 as the three-month media/services subtotal. Page 6 shows $10,132 total including licensing, with $3,734 first month. The drawer confirms $3,000/month media, $199/month licensing, and $535 setup.
- The Word export link returned Chrome's ERR_BLOCKED_BY_CLIENT page. No new Word file was acquired or visually accepted. This is recorded as browser access failure, not a demonstrated Word generator defect.

No client link, message, approval, IO, or paid generation was created. Existing proposal content was not edited.

## Map heading fix

The map helper returned a standalone leading Spacer followed by KeepTogether(map). The heading's keep-with-next chain therefore stopped at the spacer and left the actual map on the following page.

The helper now connects the spacer and map/caption flowables in a single keep-with-next chain. This includes a preceding heading without nesting KeepTogether wrappers. The map/caption relationship is preserved.

The new regression renders an actual image through the real map helper at a constrained page boundary, then requires the heading and image to be on the same page. Before the fix: page 1 heading/no image, page 2 image/no heading. After the fix: page 2 contains both.

This follow-up requires its own CI, deployment, and fresh live PDF retest before pagination acceptance can be closed. Word live inspection remains separately open.
