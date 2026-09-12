# Image background providers

The Background Remover and Image Creator logo cleanup now use Cloudinary for transparent cutouts. The Background Remover also offers Replace background with AI, using the existing shared OpenAI image-edit connection with the source image attached. It does not silently substitute a generated image for a cutout.

Users can upload photos or paste a saved image link from this Hub's Cloudinary account. Remote inputs are restricted to that account's HTTPS image deliveries; redirects, other hosts and other accounts are rejected. Original images are normalized upright and validated before processing. Cloudinary source uploads use content-based names under `smart1-cutout-sources`; completed results use the existing short-lived shared cache. The cache distinguishes operations, prompts and OpenAI models. Changing the output resize reuses the same processed image.

The UI no longer offers remove.bg free previews or claims a one-credit price. It reports completed service processing and cached reuse; normal service-plan charges apply. Cloudinary background-removal transformations have special billing, so operation counts are not quoted as exact credits or dollars. OpenAI editing uses the Hub's shared usage log. AI may alter subject details; users are prompted to review faces, labels and logos.

Cloudinary processing responses (423) are retried against the same derived image with a bounded wait. Unreadable results, opaque or fully transparent cutouts, account failures and timeouts do not become successful cached results. The UI processes a batch one image per request and retains individual errors and successes.

## Verification

- `python test_background_providers.py`: provider contracts, waiting/failure behavior, input/output validation, orientation, saved-link restrictions, cache separation, logo integration and OpenAI edit usage accounting.
- `python test_image_tools.py`: existing orientation, compression, friendly errors and updated provider/cache accounting.
- `python test_utm_bg_tools.py`: mounted tool integration, save dimensions, shared cache and request limits.
- Live Cloudinary tests on September 12, 2026 used the labeled QA image from project `proj_65998d2fc58d`. Both requests returned PNGs with alpha; the photo-only input is the appropriate cutout input because a bordered composition can leave the inner photograph as the foreground. Use the original photo without a border.

References: [Cloudinary background removal](https://cloudinary.com/documentation/background_removal), [OpenAI image editing](https://developers.openai.com/api/docs/guides/image-generation).
