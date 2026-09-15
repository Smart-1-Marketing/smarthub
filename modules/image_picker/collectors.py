"""Phase 3 collection connectors for the canonical client Media Library.

Facebook, Instagram, and client uploads use the existing signed Cloudinary
widget. Website collection is server-side, queued, bounded, same-site, and
records every imported image through ``filing.file_asset``.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from sqlalchemy import or_, select

from . import cloudinary_sink, filing, upload_sources
from .models import MediaImportRun, PickerClient, SavedImage, session
from .platform import detail_for

USER_AGENT = "SmartHubMediaCollector/1.0 (+https://smart1marketing.com)"
MAX_PAGES = 12
MAX_ASSETS = 80
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 50


def connector_catalog() -> list[dict]:
    enabled = set(upload_sources.enabled())
    storage_ready = cloudinary_sink.configured()
    return [
        {"key": "website", "label": "Existing Website", "mode": "queued",
         "available": storage_ready,
         "description": "Crawl public pages and copy their images into the library."},
        {"key": "facebook", "label": "Facebook", "mode": "upload_widget",
         "available": storage_ready and "facebook" in enabled,
         "description": "The client signs in through the existing upload portal."},
        {"key": "instagram", "label": "Instagram", "mode": "upload_widget",
         "available": storage_ready and "instagram" in enabled,
         "description": "The client signs in through the existing upload portal."},
        {"key": "client_upload", "label": "Client Upload", "mode": "upload_widget",
         "available": storage_ready,
         "description": "Signed uploads from files, phone, camera, or URL."},
    ]


def _normal_site_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Website URL is required.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Website URL must use HTTP or HTTPS.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Website URL contains an invalid port.") from exc
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        raise ValueError("Website URL contains unsupported connection details.")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("Website URL must be publicly reachable.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Website URL must be publicly reachable.")
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host + ((":" + str(port)) if port else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))


def _assert_public_url(value: str, *, resolver=socket.getaddrinfo) -> str:
    url = _normal_site_url(value)
    host = urlsplit(url).hostname or ""
    try:
        answers = resolver(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Website host could not be resolved.") from exc
    addresses = {row[4][0] for row in answers}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Website URL resolves to a private or reserved address.")
    return url


def _fetch_text(url: str) -> tuple[str, str, str]:
    current = url
    for _ in range(4):
        current = _assert_public_url(current)
        with requests.get(current, headers={"User-Agent": USER_AGENT}, stream=True,
                          allow_redirects=False, timeout=(5, 15)) as response:
            if response.is_redirect:
                target = response.headers.get("Location")
                if not target:
                    raise ValueError("Website returned an empty redirect.")
                current = urljoin(current, target)
                continue
            response.raise_for_status()
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_PAGE_BYTES:
                raise ValueError("Website page exceeds the crawl size limit.")
            chunks, total = [], 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_PAGE_BYTES:
                    raise ValueError("Website page exceeds the crawl size limit.")
                chunks.append(chunk)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            encoding = response.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace"), content_type, current
    raise ValueError("Website redirected too many times.")


def _same_site(url: str, host: str) -> bool:
    candidate = urlsplit(url).hostname or ""
    host = host.lower()
    candidate = candidate.lower()
    if host.startswith("www."):
        host = host[4:]
    if candidate.startswith("www."):
        candidate = candidate[4:]
    return candidate == host


def discover_website(root_url: str, *, fetcher=_fetch_text,
                     max_pages: int = MAX_PAGES, max_assets: int = MAX_ASSETS) -> list[dict]:
    """Return unique image candidates without storing or mutating anything."""
    root = _normal_site_url(root_url)
    origin = urlsplit(root)
    site_root = f"{origin.scheme}://{origin.netloc}"
    robots = RobotFileParser()
    robots.set_url(site_root + "/robots.txt")
    try:
        text, _, _ = fetcher(robots.url)
        robots.parse(text.splitlines())
    except Exception:  # an unreachable robots file imposes no published rule
        robots.parse([])

    queue = [root]
    try:
        xml, _, _ = fetcher(site_root + "/sitemap.xml")
        parsed = ElementTree.fromstring(xml)
        for node in parsed.iter():
            if node.tag.rsplit("}", 1)[-1] == "loc" and node.text:
                candidate = urljoin(root, node.text.strip())
                if _same_site(candidate, origin.hostname or "") and candidate not in queue:
                    queue.append(candidate)
    except Exception:
        pass

    visited, seen_images, found = set(), set(), []
    successful_pages = 0
    while queue and len(visited) < max(1, max_pages) and len(found) < max(1, max_assets):
        page = queue.pop(0)
        if page in visited or not robots.can_fetch(USER_AGENT, page):
            continue
        visited.add(page)
        try:
            html, content_type, final_url = fetcher(page)
        except Exception:
            continue
        if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
            continue
        if not _same_site(final_url, origin.hostname or ""):
            continue
        successful_pages += 1
        soup = BeautifulSoup(html, "html.parser")
        image_values: list[tuple[str, str]] = []
        for meta in soup.select('meta[property="og:image"], meta[name="twitter:image"]'):
            image_values.append((meta.get("content") or "", ""))
        for node in soup.select("img,source"):
            alt = node.get("alt") or ""
            for attr in ("src", "data-src", "data-lazy-src"):
                if node.get(attr):
                    image_values.append((node.get(attr), alt))
            if node.get("srcset"):
                image_values.append((node.get("srcset").split(",")[-1].strip().split(" ")[0], alt))
        for raw, alt in image_values:
            image_url = urljoin(final_url, str(raw or "").strip())
            parsed_image = urlsplit(image_url)
            if parsed_image.scheme not in {"http", "https"} or not parsed_image.hostname:
                continue
            image_url = urlunsplit((parsed_image.scheme, parsed_image.netloc,
                                    parsed_image.path, parsed_image.query, ""))
            if image_url in seen_images or parsed_image.path.lower().endswith(".svg"):
                continue
            seen_images.add(image_url)
            found.append({"url": image_url, "page_url": final_url,
                          "alt": " ".join(str(alt).split())[:500]})
            if len(found) >= max_assets:
                break
        for anchor in soup.select("a[href]"):
            linked = urljoin(final_url, anchor.get("href"))
            parsed_link = urlsplit(linked)
            clean = urlunsplit((parsed_link.scheme, parsed_link.netloc,
                                parsed_link.path or "/", parsed_link.query, ""))
            if (_same_site(clean, origin.hostname or "") and clean not in visited
                    and clean not in queue):
                queue.append(clean)
    if not successful_pages:
        raise ValueError("No public HTML page could be read from that website.")
    return found


def queue_website(db, client: PickerClient, url: str, *, actor: str = "") -> tuple[MediaImportRun, bool]:
    normalized = _normal_site_url(url)
    existing = db.execute(select(MediaImportRun).where(
        MediaImportRun.client_id == client.id,
        MediaImportRun.connector == "website",
        MediaImportRun.source_url == normalized,
        MediaImportRun.state.in_(["pending", "running"]),
    )).scalar_one_or_none()
    if existing:
        return existing, False
    run = MediaImportRun(client_id=client.id, connector="website",
                         source_url=normalized,
                         source_account=urlsplit(normalized).hostname,
                         requested_by=str(actor or "")[:200] or None)
    db.add(run)
    db.commit()
    return run, True


def _default_upload(**kwargs):
    return cloudinary_sink.upload_from_url(**kwargs)


def process_website_run(run_id: int | None = None, *, fetcher=_fetch_text,
                        uploader=_default_upload, filer=None,
                        url_validator=_assert_public_url,
                        max_seconds: int = MAX_SECONDS) -> dict:
    """Process the oldest queued website run; one bad image never stops it."""
    started = time.time()
    db = session()
    query = select(MediaImportRun).where(MediaImportRun.connector == "website")
    if run_id is None:
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        query = query.where(or_(
            MediaImportRun.state == "pending",
            (MediaImportRun.state == "running") & (MediaImportRun.started_at < stale),
        )).order_by(MediaImportRun.created_at)
    else:
        query = query.where(MediaImportRun.id == int(run_id))
    run = db.execute(query).scalars().first()
    if run is None:
        return {"ok": True, "processed": False, "reason": "no pending website import"}
    client = db.get(PickerClient, run.client_id)
    if client is None:
        run.state, run.last_error = "failed", "Client library no longer exists."
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"ok": False, "processed": True, "error": run.last_error}

    run.state, run.started_at, run.last_error = "running", datetime.now(timezone.utc), None
    db.commit()
    try:
        candidates = discover_website(run.source_url or "", fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001
        run.state, run.last_error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"ok": False, "processed": True, "error": run.last_error}

    run.discovered = len(candidates)
    folder = f"{client.folder()}/website"
    existing = set(db.execute(select(SavedImage.provider_image_id).where(
        SavedImage.client_id == client.id, SavedImage.provider == "website"
    )).scalars().all())
    for candidate in candidates:
        if time.time() - started > max_seconds:
            run.last_error = "Import time budget reached; rerun to collect remaining images."
            break
        source_id = "sha256:" + hashlib.sha256(candidate["url"].encode("utf-8")).hexdigest()
        if source_id in existing:
            run.skipped += 1
            continue
        try:
            url_validator(candidate["url"])
            digest = source_id.split(":", 1)[1][:20]
            stored = uploader(image_url=candidate["url"], folder=folder,
                              public_id=f"website-{digest}",
                              context={"source": "website", "page": candidate["page_url"]},
                              tags=["website_import", client.slug])
            filename = unquote(PurePosixPath(urlsplit(candidate["url"]).path).name)[:300]
            file_kwargs = dict(
                client_name=client.name, public_id=stored["public_id"],
                url=stored.get("delivery_url") or stored["secure_url"],
                source_id=source_id, source_url=candidate["url"],
                kind="website", label="Existing Website", provider="website",
                filename=filename or f"website-{digest}.jpg", alt=candidate["alt"],
                width=stored.get("width"), height=stored.get("height"),
                size_bytes=stored.get("bytes"), saved_by=run.requested_by or "scheduler",
                push_to_suite=False, tool="media_collector", folder=folder)
            result = (filing.file_asset(**file_kwargs) if filer is None
                      else filer(**file_kwargs))
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "asset filing failed")
            if result.get("duplicate"):
                run.skipped += 1
            else:
                run.imported += 1
                existing.add(source_id)
                image_id = (result.get("image") or {}).get("id")
                asset = db.get(SavedImage, image_id) if image_id else None
                if asset:
                    detail = detail_for(db, asset, create=True)
                    detail.source_account = run.source_account
                    detail.source_post_url = candidate["page_url"]
                    detail.original_alt = candidate["alt"] or None
        except Exception as exc:  # noqa: BLE001
            run.failed += 1
            run.last_error = f"{type(exc).__name__}: {exc}"[:500]
        db.commit()

    run.finished_at = datetime.now(timezone.utc)
    run.state = ("partial" if run.last_error and (run.imported or run.skipped) else
                 "failed" if run.last_error or (run.failed and not run.imported)
                 else "completed")
    db.commit()
    return {"ok": run.state in {"completed", "partial"}, "processed": True,
            "run_id": run.id, "state": run.state, "discovered": run.discovered,
            "imported": run.imported, "skipped": run.skipped, "failed": run.failed,
            "seconds": round(time.time() - started, 1)}


def recent_runs(db, client_id: int, limit: int = 20) -> list[dict]:
    rows = db.execute(select(MediaImportRun).where(
        MediaImportRun.client_id == client_id
    ).order_by(MediaImportRun.created_at.desc()).limit(limit)).scalars().all()
    return [{"id": row.id, "connector": row.connector, "source_url": row.source_url or "",
             "source_account": row.source_account or "", "state": row.state,
             "discovered": row.discovered, "imported": row.imported,
             "skipped": row.skipped, "failed": row.failed,
             "error": row.last_error or "",
             "created_at": row.created_at.isoformat() if row.created_at else "",
             "finished_at": row.finished_at.isoformat() if row.finished_at else ""}
            for row in rows]
