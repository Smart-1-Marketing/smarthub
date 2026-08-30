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
    """Whether a real code can be generated at all.

    Not asked by the CTA route, and it should not be: `generate_qr()` returns
    the reason *with* the result, so the panel says why there is no code on
    the one response that knows -- and a separate pre-check is a second
    reading of one question, which is how the two come to disagree. Kept as
    this module's own predicate for a screen that wants to say so before
    anybody presses.
    """
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
        # This used to fail soft with a placehold.co image of the letters
        # "QR" so the CTA builder would not break. That is the one failure
        # this must not have: a picture that reads as a QR code and scans to
        # nothing goes onto the end card of a CTV spot, where the code is the
        # only response mechanism there is, and nobody proof-reads the thing
        # that scans — the rule `hub/qr_codes.py` refuses to invent a
        # destination for. It also defeated the QC check that exists for
        # exactly this: `_check_qr_code` blocks a code that is enabled and
        # not generated, and a truthy placeholder walked straight past it.
        #
        # So: no image, and the reason named. `hub/qr_codes.py`'s own rule —
        # nothing is invented — and "not measured, never a zero".
        return {"data_url": None, "bytes_io": None,
                "error": ("The qrcode package is not installed on this "
                          "deployment, so no code could be generated. "
                          "`qrcode[pil]` is in requirements.txt.")}

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
