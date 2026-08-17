"""Configuration for the Page Image Optimizer.

Everything here is read from the environment so the tool inherits whatever the
Hub already has configured. The only genuinely new variables are the two
PAGE_IMAGES_* ones at the bottom, and both have sensible defaults.
"""

import os


def _int(name, default):
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


# --- Shared with the SEO Image Pipeline (already set in the Hub) -------------
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")
SEO_IMAGES_FOLDER = os.environ.get("SEO_IMAGES_FOLDER", "smart1-seo-images")
SEO_IMAGES_MAX_EDGE = _int("SEO_IMAGES_MAX_EDGE", 2400)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

# --- New, both optional ------------------------------------------------------
# How many images one batch holds. The spec is five; it is configurable so a
# future "do ten at a time" request is a env change, not a code change.
PAGE_IMAGES_BATCH_SIZE = _int("PAGE_IMAGES_BATCH_SIZE", 5)
# How long a scan/batch lives in server memory before it is dropped, minutes.
PAGE_IMAGES_TTL_MINUTES = _int("PAGE_IMAGES_TTL_MINUTES", 45)

# --- Fetch limits (not exposed as config on purpose) -------------------------
PAGE_FETCH_TIMEOUT = 15          # seconds, page HTML
IMAGE_FETCH_TIMEOUT = 20         # seconds, one image
MAX_PAGE_BYTES = 5 * 1024 * 1024        # 5 MB of HTML is already absurd
MAX_IMAGE_BYTES = 25 * 1024 * 1024      # refuse to pull anything larger
MAX_CANDIDATES = 80              # cap on images analysed from one page
PROBE_BYTES = 96 * 1024          # enough to read dimensions from a header
PREVIEW_MAX_EDGE = 480           # preview served to the review screen
ALT_MAX_CHARS = 125              # matches the SEO Image Pipeline counter

USER_AGENT = (
    "Mozilla/5.0 (compatible; Smart1Hub-ImageOptimizer/1.0; "
    "+https://smart1marketing.com)"
)
