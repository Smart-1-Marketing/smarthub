"""Deterministic Media Intelligence: fingerprints, quality, and search text.

AI is not used here. The scheduler can safely run this before the vision sweep,
which gives that sweep a content hash and prevents paying to understand an
unchanged image twice.
"""
from __future__ import annotations

import hashlib
import io
import math
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from PIL import Image, ImageFilter, ImageOps, ImageStat
from sqlalchemy import select

from .models import (
    ImageDescription, MediaAssetDetail, MediaSearchDocument, SavedImage, session,
)
from .platform import detail_for

BATCH = 40
BUDGET_SECONDS = 45
MAX_BYTES = 25 * 1024 * 1024
MAX_ATTEMPTS = 3
SEARCH_VERSION = "media-metadata-v1"
FINGERPRINT_VERSION = "sha256+dhash64-v1"


def _download(asset: SavedImage) -> bytes:
    """Fetch only the canonical Cloudinary image, with a hard byte ceiling."""
    url = str(asset.cloudinary_url or "").strip()
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != "res.cloudinary.com"
            or "/image/upload/" not in parsed.path):
        raise ValueError("asset is not a canonical Cloudinary image")
    if asset.bytes and asset.bytes > MAX_BYTES:
        raise ValueError("asset exceeds the fingerprint byte limit")
    response = requests.get(url, stream=True, allow_redirects=False,
                            timeout=(5, 20))
    response.raise_for_status()
    declared = int(response.headers.get("Content-Length") or 0)
    if declared > MAX_BYTES:
        raise ValueError("asset exceeds the fingerprint byte limit")
    chunks, total = [], 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_BYTES:
            raise ValueError("asset exceeds the fingerprint byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _dhash(image: Image.Image) -> str:
    gray = ImageOps.exif_transpose(image).convert("L").resize(
        (9, 8), Image.Resampling.LANCZOS)
    values = list(gray.getdata())
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | int(values[y * 9 + x] > values[y * 9 + x + 1])
    return f"{bits:016x}"


def hamming_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def _quality(image: Image.Image) -> dict:
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    megapixels = width * height / 1_000_000
    if megapixels >= 8:
        resolution = 45
    elif megapixels >= 3:
        resolution = 38
    elif megapixels >= 1.5:
        resolution = 30
    elif megapixels >= .8:
        resolution = 20
    else:
        resolution = max(4, round(megapixels * 20))

    sample = image.copy()
    sample.thumbnail((640, 640), Image.Resampling.LANCZOS)
    luminance = sample.convert("L")
    mean = ImageStat.Stat(luminance).mean[0]
    deviation = ImageStat.Stat(luminance).stddev[0]
    edges = luminance.filter(ImageFilter.FIND_EDGES)
    edge_variance = ImageStat.Stat(edges).var[0]
    sharpness = min(25, max(2, round(math.sqrt(max(edge_variance, 0)) * 1.8)))
    exposure = max(2, round(15 - abs(mean - 130) / 11))
    range_score = min(15, max(2, round(deviation / 3)))
    quality = max(0, min(100, resolution + sharpness + exposure + range_score))
    orientation = ("landscape" if width / max(height, 1) > 1.08 else
                   "portrait" if width / max(height, 1) < .92 else "square")
    return {"quality_score": quality, "orientation": orientation,
            "width": width, "height": height,
            "website_score": min(100, quality + (8 if orientation == "landscape" else 0)),
            "social_score": min(100, quality + (6 if orientation in {"portrait", "square"} else 0)),
            "advertising_score": quality,
            "hero_score": min(100, quality + (12 if orientation == "landscape" and width >= 1600 else 0))}


def inspect_bytes(data: bytes) -> dict:
    """Pure, testable fingerprint and quality result for one image."""
    if not data:
        raise ValueError("image is empty")
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        result = _quality(image)
        result.update(duplicate_hash=hashlib.sha256(data).hexdigest(),
                      perceptual_hash=_dhash(image),
                      mime_type=Image.MIME.get(image.format, ""),
                      fingerprint_version=FINGERPRINT_VERSION)
        return result


def _search_text(asset: SavedImage, detail: MediaAssetDetail,
                 observation: ImageDescription | None) -> str:
    values = [
        asset.filename, asset.alt_text, asset.provider, asset.collection_label,
        asset.project_name, asset.io_number, asset.product_number,
        detail.asset_type, detail.caption, detail.ai_description,
        detail.ai_category, detail.ai_tags, detail.subjects,
        detail.service_product, detail.seo_filename_suggestion,
        detail.orientation, detail.indoor_outdoor, detail.brand_asset_type,
        detail.rights_status,
        observation.description if observation else "",
        observation.tags if observation else "",
        observation.alt_suggestion if observation else "",
    ]
    return " ".join(" ".join(str(v or "").lower().split()) for v in values if v)


def index_asset(db, asset: SavedImage, detail: MediaAssetDetail,
                observation: ImageDescription | None = None) -> MediaSearchDocument:
    document = db.execute(select(MediaSearchDocument).where(
        MediaSearchDocument.asset_id == asset.id)).scalar_one_or_none()
    if document is None:
        document = MediaSearchDocument(asset_id=asset.id)
        db.add(document)
    document.search_text = _search_text(asset, detail, observation)
    document.search_version = SEARCH_VERSION
    document.indexed_at = datetime.now(timezone.utc)
    return document


def _duplicate_of(db, asset: SavedImage, detail: MediaAssetDetail) -> int | None:
    candidates = db.execute(
        select(SavedImage.id, MediaAssetDetail)
        .join(MediaAssetDetail, MediaAssetDetail.asset_id == SavedImage.id)
        .where(SavedImage.client_id == asset.client_id,
               SavedImage.id != asset.id,
               SavedImage.id < asset.id)
        .order_by(SavedImage.id)
    ).all()
    for candidate_id, other in candidates:
        if detail.duplicate_hash and other.duplicate_hash == detail.duplicate_hash:
            return candidate_id
    closest = [(hamming_distance(detail.perceptual_hash, other.perceptual_hash), candidate_id)
               for candidate_id, other in candidates if other.perceptual_hash]
    if closest:
        distance, candidate_id = min(closest)
        if distance <= 5:
            return candidate_id
    return None


def inspect_asset(db, asset: SavedImage, *, downloader=_download) -> dict:
    detail = detail_for(db, asset, create=True)
    observation = db.execute(select(ImageDescription).where(
        ImageDescription.image_id == asset.id)).scalar_one_or_none()
    if (asset.resource_type or "image") != "image" or asset.external:
        detail.fingerprint_state = "not_applicable"
        index_asset(db, asset, detail, observation)
        return {"indexed": True, "fingerprinted": False, "skipped": True}
    try:
        result = inspect_bytes(downloader(asset))
    except Exception as exc:  # noqa: BLE001 - one bad asset cannot stop a batch
        detail.fingerprint_attempts = int(detail.fingerprint_attempts or 0) + 1
        detail.fingerprint_error = f"{type(exc).__name__}: {exc}"[:400]
        detail.fingerprint_state = ("given_up" if detail.fingerprint_attempts >= MAX_ATTEMPTS
                                    else "pending")
        index_asset(db, asset, detail, observation)
        return {"indexed": True, "fingerprinted": False,
                "gave_up": detail.fingerprint_state == "given_up",
                "error": detail.fingerprint_error}

    for field in ("duplicate_hash", "perceptual_hash", "mime_type", "orientation",
                  "quality_score", "website_score", "social_score",
                  "advertising_score", "hero_score"):
        setattr(detail, field, result[field])
    if not asset.width:
        asset.width = result["width"]
    if not asset.height:
        asset.height = result["height"]
    detail.duplicate_of = _duplicate_of(db, asset, detail)
    detail.fingerprint_state = "complete"
    detail.fingerprint_attempts = 0
    detail.fingerprint_error = None
    detail.updated_at = datetime.now(timezone.utc)
    index_asset(db, asset, detail, observation)
    return {"indexed": True, "fingerprinted": True,
            "duplicate": bool(detail.duplicate_of), "asset_id": asset.id}


def sweep(limit: int = BATCH, *, max_seconds: int = BUDGET_SECONDS,
          actor: str = "scheduler") -> dict:
    """Bounded, idempotent pass over assets missing Phase 2 intelligence."""
    started = time.time()
    fingerprinted = duplicates = indexed = failed = gave_up = 0
    with session() as db:
        details = {d.asset_id: d for d in db.execute(select(MediaAssetDetail)).scalars().all()}
        documents = {d.asset_id: d for d in db.execute(select(MediaSearchDocument)).scalars().all()}
        assets = db.execute(select(SavedImage).order_by(SavedImage.created_at.asc())).scalars().all()
        todo = []
        for asset in assets:
            detail = details.get(asset.id)
            document = documents.get(asset.id)
            needs_fingerprint = ((asset.resource_type or "image") == "image"
                                 and not asset.external and
                                 (detail is None or detail.fingerprint_state not in
                                  {"complete", "given_up"}))
            needs_index = document is None or document.search_version != SEARCH_VERSION
            if needs_fingerprint or needs_index:
                todo.append(asset)
            if len(todo) >= max(1, int(limit)):
                break
        for asset in todo:
            if time.time() - started > max_seconds:
                break
            result = inspect_asset(db, asset)
            indexed += int(result.get("indexed", False))
            fingerprinted += int(result.get("fingerprinted", False))
            duplicates += int(result.get("duplicate", False))
            failed += int(bool(result.get("error")) and not result.get("gave_up"))
            gave_up += int(result.get("gave_up", False))
            db.commit()
    return {"ok": True, "fingerprinted": fingerprinted, "duplicates": duplicates,
            "indexed": indexed, "failed": failed, "gave_up": gave_up,
            "seconds": round(time.time() - started, 1), "actor": actor}


def semantic_status(db, asset_ids: list[int]) -> dict:
    if not asset_ids:
        return {"indexed": 0, "embedded": 0, "ready": False,
                "search_version": SEARCH_VERSION}
    rows = db.execute(select(MediaSearchDocument).where(
        MediaSearchDocument.asset_id.in_(asset_ids))).scalars().all()
    embedded = sum(1 for row in rows if row.embedding_json)
    return {"indexed": len(rows), "embedded": embedded,
            "ready": bool(rows) and embedded == len(rows),
            "search_version": SEARCH_VERSION}
