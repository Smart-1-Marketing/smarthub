"""Thin shim: the algorithm now lives in hub/image_compress.py.

Kept so this module's callers and its public names are unchanged, and so the
Image Optimizer keeps working exactly as it did. Anything new should import
hub.image_compress directly.
"""
from hub.image_compress import (            # noqa: F401
    SUPPORTED_EXTENSIONS,
    TARGET_BYTES_DEFAULT,
    OptimizationResult,
    optimize_gif,
    optimize_image,
    optimize_jpeg,
    optimize_png,
)
