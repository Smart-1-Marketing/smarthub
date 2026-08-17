"""Fetch a page, find every image on it, and measure them.

No BeautifulSoup: the Hub's requirements.txt is deliberately unchanged, so this
uses html.parser from the standard library.
"""

import io
import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from PIL import Image

from . import settings

# Formats we can meaningfully re-encode. SVG is already vector and GIF may be
# animated, so both are listed but never offered as optimisation candidates.
RASTER_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
SKIP_EXT = {".svg", ".gif", ".ico"}


class ScanError(Exception):
    """Raised with a message that is safe to show the user."""


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #

def _resolved_addresses(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ScanError(f"Can't resolve {host}. Check the address and try again.")
    return {info[4][0] for info in infos}


def safe_url(raw, *, what="page"):
    """Normalise a URL and refuse anything that points inside the network."""
    raw = (raw or "").strip()
    if not raw:
        raise ScanError(f"Enter a {what} address.")
    if "://" not in raw:
        raw = "https://" + raw

    parts = urlparse(raw)
    if parts.scheme not in ("http", "https"):
        raise ScanError("Only http and https addresses can be read.")
    if not parts.hostname:
        raise ScanError("That address is missing a domain name.")
    if parts.username or parts.password:
        raise ScanError("Remove the username and password from the address.")

    for addr in _resolved_addresses(parts.hostname):
        ip = ipaddress.ip_address(addr)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ScanError(
                f"{parts.hostname} resolves to an internal address, so it can't be read."
            )

    return urlunparse(parts._replace(fragment=""))


def _get(url, *, timeout, stream=False):
    """GET with redirects re-checked against the SSRF guard at every hop."""
    current = url
    session = requests.Session()
    session.max_redirects = 5
    headers = {"User-Agent": settings.USER_AGENT, "Accept": "*/*"}
    for _ in range(6):
        resp = session.get(current, headers=headers, timeout=timeout,
                           stream=stream, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            resp.close()
            if not location:
                raise ScanError("The page redirected to nowhere.")
            current = safe_url(urljoin(current, location))
            continue
        return resp
    raise ScanError("Too many redirects.")


# --------------------------------------------------------------------------- #
# HTML parsing
# --------------------------------------------------------------------------- #

class _PageParser(HTMLParser):
    """Collects images plus enough surrounding text to describe them well."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []          # raw dicts, resolved later
        self.title = ""
        self.description = ""
        self.h1 = ""
        self.social_images = []
        self._capture = None      # "title" | "heading" | "figcaption"
        self._buffer = []
        self._heading = ""        # nearest preceding heading text
        self._figcaption_for = None

    # -- helpers -----------------------------------------------------------
    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        return text

    def _add(self, src, attrs, kind):
        if not src:
            return
        self.images.append({
            "src": src,
            "alt": (attrs.get("alt") or "").strip(),
            "title_attr": (attrs.get("title") or "").strip(),
            "width_attr": attrs.get("width") or "",
            "height_attr": attrs.get("height") or "",
            "loading": attrs.get("loading") or "",
            "kind": kind,
            "nearest_heading": self._heading,
            "caption": "",
        })

    # -- HTMLParser API ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag == "title":
            self._capture = "title"
        elif tag in ("h1", "h2", "h3"):
            self._capture = "heading"
        elif tag == "figcaption":
            self._capture = "figcaption"
            self._figcaption_for = len(self.images) - 1

        elif tag == "meta":
            name = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content", "").strip()
            if name == "description" and not self.description:
                self.description = content
            elif name in ("og:image", "og:image:secure_url", "twitter:image") and content:
                self.social_images.append(content)

        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            best = _largest_from_srcset(a.get("srcset") or a.get("data-srcset") or "")
            self._add(best or src, a, "img")

        elif tag == "source":
            best = _largest_from_srcset(a.get("srcset") or a.get("data-srcset") or "")
            if best:
                self._add(best, a, "picture")

        style = a.get("style", "")
        if "background-image" in style:
            for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", style):
                self._add(url, {}, "background")

    def handle_endtag(self, tag):
        if self._capture == "title" and tag == "title":
            self.title = self._flush()
            self._capture = None
        elif self._capture == "heading" and tag in ("h1", "h2", "h3"):
            text = self._flush()
            if text:
                self._heading = text
                if tag == "h1" and not self.h1:
                    self.h1 = text
            self._capture = None
        elif self._capture == "figcaption" and tag == "figcaption":
            text = self._flush()
            if text and self._figcaption_for is not None and self._figcaption_for >= 0:
                self.images[self._figcaption_for]["caption"] = text
            self._capture = None
            self._figcaption_for = None

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)


def _largest_from_srcset(srcset):
    """Pick the highest-resolution candidate out of a srcset attribute."""
    best, best_weight = "", -1.0
    for part in (srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0]
        weight = 1.0
        if len(bits) > 1:
            descriptor = bits[1].lower()
            try:
                if descriptor.endswith("w"):
                    weight = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    weight = float(descriptor[:-1]) * 1000
            except ValueError:
                weight = 1.0
        if weight > best_weight:
            best, best_weight = url, weight
    return best


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #

def _ext_of(url):
    path = urlparse(url).path.lower()
    match = re.search(r"(\.[a-z0-9]{1,5})$", path)
    return match.group(1) if match else ""


def _probe(url):
    """Read just enough of an image to learn its real size and dimensions."""
    out = {"bytes": 0, "width": 0, "height": 0, "format": "", "error": ""}
    try:
        resp = _get(url, timeout=settings.IMAGE_FETCH_TIMEOUT, stream=True)
        if resp.status_code >= 400:
            out["error"] = f"HTTP {resp.status_code}"
            resp.close()
            return out

        declared = resp.headers.get("content-length")
        if declared and declared.isdigit():
            out["bytes"] = int(declared)

        chunks, total = [], 0
        for chunk in resp.iter_content(16384):
            chunks.append(chunk)
            total += len(chunk)
            if total >= settings.PROBE_BYTES:
                break
        resp.close()

        if not out["bytes"]:
            out["bytes"] = total  # small file, we read all of it

        head = b"".join(chunks)
        try:
            with Image.open(io.BytesIO(head)) as im:
                out["width"], out["height"] = im.size
                out["format"] = (im.format or "").lower()
        except Exception:
            out["error"] = out["error"] or "Not a readable image"
    except ScanError as exc:
        out["error"] = str(exc)
    except requests.RequestException:
        out["error"] = "Couldn't be downloaded"
    return out


def _estimate_saving(item):
    """Rough, honest estimate of what optimisation will do to this file."""
    size = item["bytes"]
    if not size or not item["width"]:
        return 0
    max_edge = settings.SEO_IMAGES_MAX_EDGE
    longest = max(item["width"], item["height"])
    scale = min(1.0, max_edge / longest) if longest else 1.0
    pixel_factor = scale * scale
    # WebP at quality:auto lands roughly 30% under JPEG, further under PNG.
    fmt_factor = {"png": 0.35, "bmp": 0.15, "tiff": 0.15}.get(item["format"], 0.70)
    if item["format"] == "webp":
        fmt_factor = 0.95
    projected = size * pixel_factor * fmt_factor
    return max(0, int(size - projected))


def scan_page(page_url):
    """Return page context plus every analysed image found on it."""
    url = safe_url(page_url)
    try:
        resp = _get(url, timeout=settings.PAGE_FETCH_TIMEOUT, stream=True)
    except requests.RequestException:
        raise ScanError("That page couldn't be reached. Check the address is public.")

    if resp.status_code >= 400:
        resp.close()
        raise ScanError(f"That page returned HTTP {resp.status_code}.")

    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype and "xml" not in ctype:
        resp.close()
        raise ScanError("That address isn't a web page.")

    body, total = [], 0
    for chunk in resp.iter_content(65536):
        body.append(chunk)
        total += len(chunk)
        if total >= settings.MAX_PAGE_BYTES:
            break
    resp.close()
    html = b"".join(body).decode(resp.encoding or "utf-8", errors="replace")

    parser = _PageParser()
    parser.feed(html)
    parser.close()

    for social in parser.social_images:
        parser.images.append({
            "src": social, "alt": "", "title_attr": "", "width_attr": "",
            "height_attr": "", "loading": "", "kind": "social",
            "nearest_heading": parser.h1 or parser.title, "caption": "",
        })

    # Resolve, de-duplicate, drop the things we can never optimise.
    seen, candidates = set(), []
    for raw in parser.images:
        src = (raw["src"] or "").strip()
        if not src or src.startswith("data:"):
            continue
        try:
            absolute = safe_url(urljoin(url, src), what="image")
        except ScanError:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)

        ext = _ext_of(absolute)
        raw["url"] = absolute
        raw["ext"] = ext
        raw["skip_reason"] = ""
        if ext in SKIP_EXT:
            raw["skip_reason"] = {
                ".svg": "Vector file — already as small as it gets",
                ".gif": "GIF — may be animated, left alone",
                ".ico": "Site icon",
            }[ext]
        candidates.append(raw)
        if len(candidates) >= settings.MAX_CANDIDATES:
            break

    measurable = [c for c in candidates if not c["skip_reason"]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: _probe(c["url"]), measurable))
    for item, probe in zip(measurable, results):
        item.update(probe)

    for item in candidates:
        item.setdefault("bytes", 0)
        item.setdefault("width", 0)
        item.setdefault("height", 0)
        item.setdefault("format", "")
        item.setdefault("error", "")
        if not item["skip_reason"]:
            if item["error"]:
                item["skip_reason"] = item["error"]
            elif item["width"] and max(item["width"], item["height"]) <= 2:
                item["skip_reason"] = "Tracking pixel"
            elif item["bytes"] > settings.MAX_IMAGE_BYTES:
                item["skip_reason"] = "Larger than 25 MB"
        item["saving"] = 0 if item["skip_reason"] else _estimate_saving(item)
        item["optimizable"] = not item["skip_reason"]

    # Biggest win first — that is the order someone actually wants to work in.
    candidates.sort(key=lambda c: (not c["optimizable"], -c["saving"], -c["bytes"]))

    return {
        "url": url,
        "title": parser.title,
        "description": parser.description,
        "h1": parser.h1,
        "images": candidates,
    }
