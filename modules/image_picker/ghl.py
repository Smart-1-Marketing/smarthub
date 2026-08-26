"""
Push a saved image into the client's Smart 1 Suite (GoHighLevel) media library.

Auth: either a per-client location token stored on the client record, or the
agency-level `GHL_PRIVATE_TOKEN` already configured for the billing audit,
scoped with `altType=location` + `altId=<locationId>`.

Two upload paths:

  hosted  -- hand GHL the Cloudinary URL and let it fetch. Preferred: no bytes
             through the Hub, and what lands in Suite is already the resized
             WebP derivative.
  direct  -- stream the bytes ourselves as multipart. Fallback for when the
             hosted form is rejected.

>>> VERIFY ON FIRST LIVE RUN <<<
This is written against GoHighLevel's documented Medias API
(`POST /medias/upload-file`, `Version: 2021-07-28`). It has been exercised
against a mocked transport only, the same caveat the Suite billing audit
carried for the SaaS Configurator endpoints. Confirm on the first real upload:
  * that the hosted form (`hosted=true` + `fileUrl`) is accepted, and what the
    response field for the new file id/url is actually called;
  * that `altType`/`altId` belong in the form body for your token type;
  * whether your token needs `medias.write` added.
`probe()` below does exactly this against one location and reports what came
back, so the first live check is one button press rather than a debug session.
Failures here are recorded on the image row and never lose the Cloudinary copy.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE = os.environ.get("GHL_API_BASE", "https://services.leadconnectorhq.com").rstrip("/")
API_VERSION = os.environ.get("GHL_API_VERSION", "2021-07-28")
HTTP_TIMEOUT = float(os.environ.get("GHL_HTTP_TIMEOUT", "30"))
MAX_DIRECT_BYTES = 25 * 1024 * 1024


class GHLError(RuntimeError):
    """Raised with a message safe to store, never shown raw to a client."""


def agency_token() -> str:
    """The agency token, under whichever name it is set.

    This deployment sets both GHL_PRIVATE_TOKEN and SMART1SUITE_PRIVATE_TOKEN,
    and hub/ghl_contacts.py — the one write path for the whole Hub — reads
    either. Reading one of them here meant a Hub whose Suite integration worked
    everywhere else reported the picker's media library as unconfigured.
    """
    try:
        from hub.config import settings
        return (settings.ghl_token or "").strip()
    except Exception:                                 # noqa: BLE001
        return (os.environ.get("GHL_PRIVATE_TOKEN")
                or os.environ.get("SMART1SUITE_PRIVATE_TOKEN") or "").strip()


def configured() -> bool:
    return bool(agency_token())


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Version": API_VERSION,
        "Accept": "application/json",
    }


def _resolve_token(client) -> str:
    tok = (getattr(client, "ghl_location_token", None) or "").strip()
    return tok or agency_token()


def _describe(resp: requests.Response) -> str:
    """Short, storable failure description. Never includes the token."""
    body = ""
    try:
        body = str(resp.json())[:300]
    except Exception:  # noqa: BLE001
        body = (resp.text or "")[:300]
    return f"HTTP {resp.status_code}: {body}"


def _extract(payload: Any) -> dict[str, str]:
    """Pull file id + url out of the response without assuming one shape.

    GHL's docs are inconsistent across media endpoints about whether the object
    is at the root or nested, so we look in both rather than hard-coding a path
    that might be wrong.
    """
    if not isinstance(payload, dict):
        return {"file_id": "", "url": ""}
    node = payload
    for key in ("file", "media", "data"):
        inner = payload.get(key)
        if isinstance(inner, dict):
            node = inner
            break
    file_id = ""
    for k in ("fileId", "id", "_id", "mediaId"):
        if node.get(k):
            file_id = str(node[k])
            break
    url = ""
    for k in ("url", "fileUrl", "link", "publicUrl"):
        if node.get(k):
            url = str(node[k])
            break
    return {"file_id": file_id, "url": url}


def upload_hosted(
    *,
    token: str,
    location_id: str,
    file_url: str,
    name: str,
    parent_id: str | None = None,
) -> dict[str, str]:
    """Ask GHL to fetch the file from a URL (the Cloudinary delivery URL)."""
    form: dict[str, Any] = {
        "hosted": "true",
        "fileUrl": file_url,
        "name": name,
    }
    if location_id:
        form["altType"] = "location"
        form["altId"] = location_id
    if parent_id:
        form["parentId"] = parent_id

    resp = requests.post(
        f"{BASE}/medias/upload-file",
        headers=_headers(token),
        data=form,
        timeout=HTTP_TIMEOUT,
    )
    if not resp.ok:
        raise GHLError(_describe(resp))
    try:
        return _extract(resp.json())
    except ValueError:
        raise GHLError("GHL returned a non-JSON response to upload-file") from None


def upload_direct(
    *,
    token: str,
    location_id: str,
    file_url: str,
    name: str,
    parent_id: str | None = None,
) -> dict[str, str]:
    """Download the file and stream it to GHL as multipart."""
    src = requests.get(file_url, timeout=HTTP_TIMEOUT, stream=True)
    if not src.ok:
        raise GHLError(f"could not fetch image for upload (HTTP {src.status_code})")

    declared = int(src.headers.get("Content-Length") or 0)
    if declared and declared > MAX_DIRECT_BYTES:
        raise GHLError("image is too large to relay to GHL")

    blob = b""
    for chunk in src.iter_content(64 * 1024):
        blob += chunk
        if len(blob) > MAX_DIRECT_BYTES:
            raise GHLError("image is too large to relay to GHL")

    content_type = src.headers.get("Content-Type", "image/webp").split(";")[0]
    form: dict[str, Any] = {"name": name}
    if location_id:
        form["altType"] = "location"
        form["altId"] = location_id
    if parent_id:
        form["parentId"] = parent_id

    resp = requests.post(
        f"{BASE}/medias/upload-file",
        headers=_headers(token),
        data=form,
        files={"file": (name, blob, content_type)},
        timeout=HTTP_TIMEOUT,
    )
    if not resp.ok:
        raise GHLError(_describe(resp))
    try:
        return _extract(resp.json())
    except ValueError:
        raise GHLError("GHL returned a non-JSON response to upload-file") from None


def push_image(client, *, file_url: str, name: str) -> dict[str, str]:
    """Send one image to a client's Suite media library.

    Returns {"status": sent|skipped|error, "file_id", "url", "error"}.
    Never raises -- a Suite failure must not undo a successful Cloudinary save.
    """
    if not getattr(client, "ghl_enabled", True):
        return {"status": "skipped", "file_id": "", "url": "", "error": "Suite sync is off for this client"}

    location_id = (getattr(client, "ghl_location_id", None) or "").strip()
    if not location_id:
        return {"status": "skipped", "file_id": "", "url": "",
                "error": "No Suite location ID on this client"}

    token = _resolve_token(client)
    if not token:
        return {"status": "skipped", "file_id": "", "url": "",
                "error": "No Suite API token configured"}

    parent_id = (os.environ.get("GHL_MEDIA_PARENT_ID") or "").strip() or None

    try:
        got = upload_hosted(token=token, location_id=location_id,
                            file_url=file_url, name=name, parent_id=parent_id)
        return {"status": "sent", "file_id": got["file_id"], "url": got["url"], "error": ""}
    except GHLError as exc:
        log.warning("GHL hosted upload failed for location %s: %s", location_id, exc)
        first = str(exc)

    try:
        got = upload_direct(token=token, location_id=location_id,
                            file_url=file_url, name=name, parent_id=parent_id)
        return {"status": "sent", "file_id": got["file_id"], "url": got["url"], "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "file_id": "", "url": "",
                "error": f"hosted: {first} | direct: {exc}"[:900]}


def probe(location_id: str, token: str | None = None) -> dict[str, Any]:
    """One-shot connectivity check against a location's media library.

    Read-only: lists files rather than uploading. Use this to confirm the token
    and location before letting a client loose on the picker.
    """
    token = (token or agency_token()).strip()
    if not token:
        return {"ok": False, "error": "No Suite API token configured"}
    if not location_id:
        return {"ok": False, "error": "No location ID given"}
    try:
        resp = requests.get(
            f"{BASE}/medias/files",
            headers=_headers(token),
            params={"altType": "location", "altId": location_id, "limit": 1, "sortBy": "createdAt",
                    "sortOrder": "desc"},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}

    if not resp.ok:
        return {"ok": False, "status": resp.status_code, "error": _describe(resp)}
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "error": "GHL returned a non-JSON response"}
    files = payload.get("files") or payload.get("data") or []
    return {"ok": True, "file_count_sample": len(files) if isinstance(files, list) else 0,
            "response_keys": sorted(payload.keys()) if isinstance(payload, dict) else []}
