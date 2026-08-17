"""Smart 1 Hub -- Image Picker.

A client picks stock photos by industry, topic or service; saving pushes them
into their Cloudinary gallery and their Smart 1 Suite (GoHighLevel) media
library in one step.

Mount with:

    from modules.image_picker import register_image_picker
    register_image_picker(app)
"""

from .app import bp, register_image_picker  # noqa: F401

__all__ = ["bp", "register_image_picker"]
