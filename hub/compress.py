"""Response gzip compression, read from one place rather than two.

There are exactly two spots in this Hub that already hold a complete response
body in memory as bytes before it goes out: the hub app's own chrome injector
(`hub/__init__.py`'s `_inject_sidebar_response`) and `HubBar` in `wsgi.py`,
which buffers every dispatcher-mounted module's HTML response whole in order
to splice the sidebar and script tags into it. Compressing is a few more
lines at each of those, not a second full pass over the response -- and it is
one function rather than two so the two call sites cannot quietly diverge on
what counts as compressible, which is the drift this codebase keeps finding
and undoing everywhere else.

Stdlib `gzip` only. `flask-compress` would need adding to requirements.txt
for something the standard library already does in one call, and this repo's
own rule is no new dependency unless genuinely unavoidable.
"""
from __future__ import annotations

import gzip

# Compressing an already-compressed or binary payload wastes CPU for no
# byte savings -- a JPEG or PDF re-gzipped is usually the same size or
# larger, not smaller, because there is no redundancy left to squeeze out.
# Text is the entire win here: HTML (every page, carrying the sidebar and
# chrome this Hub injects into all of them), JSON (every API response) and
# the handful of CSS/JS this app serves through routes rather than as
# static files with their own cache headers.
_COMPRESSIBLE_PREFIXES = (
    "text/",
    "application/json",
    "application/javascript",
)

# Below this, gzip's own header and footer (18 bytes minimum, more once
# framing is counted) can cost more than they save, and the CPU spent
# compressing a response nobody would ever notice the size of is wasted.
_MIN_BYTES = 500


def compress(body: bytes, content_type: str, accept_encoding: str) -> tuple[bytes, bool]:
    """Gzip `body` if it is worth it and the caller said it can read it.

    Returns `(possibly-compressed body, whether it was compressed)`. Never
    raises -- a body that fails to compress ships uncompressed rather than
    costing the page, the same rule every other guard in this Hub follows
    for something that must not be allowed to break what it is decorating.
    """
    if not body or len(body) < _MIN_BYTES:
        return body, False
    if "gzip" not in (accept_encoding or "").lower():
        return body, False
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if not any(ctype.startswith(p) for p in _COMPRESSIBLE_PREFIXES):
        return body, False
    try:
        return gzip.compress(body, compresslevel=6), True
    except Exception:  # noqa: BLE001 -- ship uncompressed rather than 500
        return body, False


def add_vary(existing: str) -> str:
    """Fold Accept-Encoding into an existing Vary header rather than replace it.

    A cache reading Vary decides whether a stored response may be reused for
    a different request; overwriting rather than appending would let a
    shared cache serve one visitor's gzipped page to the next visitor's
    browser regardless of what that browser said it could read.
    """
    parts = [p.strip() for p in (existing or "").split(",") if p.strip()]
    if "Accept-Encoding" not in parts:
        parts.append("Accept-Encoding")
    return ", ".join(parts)
