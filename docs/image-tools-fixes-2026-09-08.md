# Image tool fixes and provider checks

## Changes

- Normalize EXIF orientation before crop validation and resizing. A displayed 600 × 800 portrait resized to width 300 now returns 300 × 400, with and without optimization.
- Preserve the minimum image-size safeguard, but report when an output exceeds its requested byte target. Both downloads and gallery saves show the warning. Output dimensions and activity-log dimensions describe the actual compressed file.
- Validate quality and target size with field-specific messages.
- Replace raw Image Creator OpenAI errors with actionable connection, request, usage-limit, and availability messages. Empty/malformed results are not reported as successful generations.
- Count remove.bg free previews separately from paid cutouts, including the usage ledger, activity log, result flags, and completion message. Cached results count as neither a new paid call nor a new preview.

## Live provider checks

Exercised through the signed-in Smart Hub UI on September 7–8, 2026, before deployment of these changes:

| Provider | Request | Observed result |
| --- | --- | --- |
| OpenAI Images | One blue mug on a plain gray background | Generated image appeared on Image Creator canvas |
| Pexels | Search: blue ceramic coffee mug | Results returned with provider attribution |
| Unsplash | Same search | Results returned with provider attribution |
| Pixabay | Same search | Results returned with provider attribution |
| remove.bg | One test image, Preview resolution | Transparent 500 × 500 PNG, approximately 87 KB |

The remove.bg check exposed an additional accounting bug: the old app called the preview “1 credit used.” Its code counted every non-cached success as paid, even when the request selected the free preview mode. The correction above is covered with preview, paid, and cache-hit regressions. See the [remove.bg API documentation](https://www.remove.bg/api).

No test image was published to a client gallery or saved as a project. Brandfetch/logo testing could not be completed because the browser connection timed out. Cloudinary uploads were not tested. These results verify the listed requests, not every provider capability.

## Automated coverage

- `test_image_pdf_optimizers.py`: existing optimizer behavior, including animation and conversion.
- `test_image_tools.py`: orientation, upright crops, target-result metadata, friendly validation, mocked provider failure/success responses, and preview/paid/cache accounting.
- `test_image_optimizer_ui.js`: eight download and gallery scenarios, executing the real browser form handler.

All three are included in the repository CI gate. Local PDF compression coverage still depends on Ghostscript/qPDF being installed; CI installs them.
