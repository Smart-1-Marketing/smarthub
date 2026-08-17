"""Page Image Optimizer — a Smart 1 Hub tool.

Reads a live page, measures every image on it, and optimises the ones you pick
in batches of five: resize to the SEO cap, convert to WebP, AI-written filename
and alt text, then straight into the client's SEO images.

Built on the same pieces as the SEO Image Pipeline (same Cloudinary folder,
same context keys, same resize-then-WebP order), so the two tools feed one
gallery. The difference is where the images come from: that tool takes uploads,
this one takes a URL.
"""

from .app import bp, register  # noqa: F401

__all__ = ["bp", "register"]
