"""QR code generation for the CTA Builder.

Per the CTV/YouTube best-practices brief: a QR code is essential for CTV
(no clicks on a TV remote), high-contrast, at least 15% of the screen, and
held on screen at least 8-10 seconds so someone can actually pull out their
phone and scan it (see config.QR_CODE_RULES).

Unlike the other services in this module, QR generation needs no external
API or key — the `qrcode` package renders locally — so this is "live"
whenever the dependency is installed, with no mock/live split.
"""

import base64
from io import BytesIO

try:
    import qrcode
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def is_available():
    return _AVAILABLE


def generate_qr(url, box_size=12, border=2):
    """
    Returns {"data_url": "data:image/png;base64,...", "bytes_io": BytesIO}
    High-contrast (pure black/white) per the brief's "high-contrast" note —
    deliberately not tinted to brand colors, since low-contrast QR codes
    fail to scan from a couch.
    """
    if not url:
        return {"data_url": None, "bytes_io": None}

    if not _AVAILABLE:
        # Extremely small dependency (pure Python + Pillow); if it's somehow
        # missing, fail soft with a placeholder so the CTA builder doesn't break.
        return {"data_url": "https://placehold.co/400x400/000000/ffffff?text=QR", "bytes_io": None,
                "_mock": True}

    target = url if url.startswith(("http://", "https://")) else f"https://{url}"
    qr = qrcode.QRCode(box_size=box_size, border=border,
                        error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(target)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {"data_url": f"data:image/png;base64,{b64}", "bytes_io": BytesIO(buf.getvalue()), "target": target}
