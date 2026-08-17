"""Find the *actual* broken links and images on a site.

The Insites audit tells you there are seven broken links. It does not tell
you which seven — the payload carries counts, not URLs. That's fine for a
score and useless for a fix list, so this walks the site itself and produces
the addresses.

How it works:

1. Start from the pages Insites already discovered (``page_count.pages_
   discovered``); fall back to crawling from the homepage if that's absent.
2. Fetch each page and pull every ``<a href>`` and ``<img src>`` (plus
   ``srcset`` candidates, which are the ones that usually rot unnoticed).
3. Check each distinct target once. ``HEAD`` first, ``GET`` on the servers
   that don't answer HEAD properly.
4. Report anything that answers 400+ or won't connect, with the page it was
   found on.

Deliberate limits, because this runs against a customer's live site:

* hard caps on pages, links and wall-clock time — a big site returns a
  partial answer rather than running forever, and says so;
* one polite worker pool, a real user agent, and no repeat requests;
* external links that answer 403/429 are reported as *blocked*, not broken —
  plenty of hosts refuse any automated request, and calling that a fault
  would send someone chasing a link that works fine in a browser.

Nothing here raises: a failure comes back as an ``error`` string on the
result so the caller can show it.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import requests

MAX_PAGES = 25          # pages fetched and parsed
MAX_TARGETS = 400       # distinct URLs checked
PAGE_TIMEOUT = 12       # seconds for one page fetch
CHECK_TIMEOUT = 10      # seconds for one link check
WORKERS = 8
DEADLINE = 150          # overall wall-clock ceiling, seconds

USER_AGENT = ("Mozilla/5.0 (compatible; Smart1HubLinkCheck/1.0; "
              "+https://smart1marketing.com)")

# Schemes that are never a broken link — they're not links to a page at all.
_SKIP_SCHEMES = ("mailto:", "tel:", "sms:", "javascript:", "data:", "#",
                 "callto:", "whatsapp:", "fax:")


class _Extractor(HTMLParser):
    """Pull hrefs and image sources out of one page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if href:
                self.links.append(href)
        elif tag in ("img", "source"):
            src = (a.get("src") or a.get("data-src") or "").strip()
            if src:
                self.images.append(src)
            # srcset: "a.jpg 1x, b.jpg 2x" — the wide variants are the ones
            # that quietly go missing after a theme change.
            for candidate in (a.get("srcset") or "").split(","):
                url = candidate.strip().split(" ")[0].strip()
                if url:
                    self.images.append(url)


def _normalise(base: str, raw: str) -> str | None:
    """Absolute, fragment-free URL — or None if it isn't checkable."""
    raw = (raw or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if any(low.startswith(s) for s in _SKIP_SCHEMES):
        return None
    try:
        absolute = urljoin(base, raw)
        parts = urlparse(absolute)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunparse(parts._replace(fragment=""))


def _same_site(url: str, host: str) -> bool:
    try:
        net = urlparse(url).netloc.lower()
    except ValueError:
        return False
    net = net.split(":")[0]
    if net.startswith("www."):
        net = net[4:]
    return net == host or net.endswith("." + host)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT,
                      "Accept": "*/*",
                      "Accept-Language": "en-US,en;q=0.9"})
    return s


def _check_one(session: requests.Session, url: str) -> dict:
    """One URL -> {status, ok, blocked, reason}."""
    try:
        r = session.head(url, timeout=CHECK_TIMEOUT, allow_redirects=True)
        # A fair number of servers mishandle HEAD (405, 501, or a bare 404
        # for a page that exists). Confirm anything unhappy with a real GET
        # before calling it broken.
        if r.status_code >= 400 or r.status_code in (405, 501):
            r = session.get(url, timeout=CHECK_TIMEOUT, allow_redirects=True,
                            stream=True)
            r.close()
        code = r.status_code
        return {"status": code, "ok": code < 400,
                "blocked": code in (401, 403, 429),
                "reason": "" if code < 400 else f"HTTP {code}"}
    except requests.exceptions.SSLError:
        return {"status": None, "ok": False, "blocked": False,
                "reason": "SSL certificate error"}
    except requests.exceptions.Timeout:
        return {"status": None, "ok": False, "blocked": False,
                "reason": f"No response in {CHECK_TIMEOUT}s"}
    except requests.exceptions.ConnectionError:
        return {"status": None, "ok": False, "blocked": False,
                "reason": "Could not connect / host not found"}
    except requests.RequestException as exc:
        return {"status": None, "ok": False, "blocked": False,
                "reason": type(exc).__name__}


def run(domain: str, pages: list[str] | None = None) -> dict:
    """Check a site and return the broken links and images.

    ``domain`` is a bare host (``example.com``). ``pages`` is the discovered
    page list from the audit; when empty we crawl same-site links from the
    homepage until MAX_PAGES.
    """
    started = time.time()
    host = (domain or "").strip().lower().lstrip("/")
    if host.startswith("http"):
        host = urlparse(host).netloc
    host = host.split("/")[0].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return {"error": "No domain to check.", "broken": [],
                "pages_checked": 0, "links_checked": 0}

    home = f"https://{host}/"
    queue: list[str] = []
    seen_pages: set[str] = set()
    for p in (pages or []):
        u = _normalise(home, str(p))
        if u and _same_site(u, host) and u not in seen_pages:
            seen_pages.add(u)
            queue.append(u)
    if not queue:
        queue = [home]
        seen_pages.add(home)
    crawling = not pages          # only follow new links when we weren't given a list

    session = _session()
    # target url -> list of (source page, kind)
    targets: dict[str, list[tuple[str, str]]] = {}
    pages_checked = 0
    truncated = False
    page_errors: list[dict] = []

    i = 0
    while i < len(queue) and pages_checked < MAX_PAGES:
        if time.time() - started > DEADLINE * 0.5:
            truncated = True
            break
        page = queue[i]
        i += 1
        try:
            r = session.get(page, timeout=PAGE_TIMEOUT, allow_redirects=True)
            pages_checked += 1
            if r.status_code >= 400:
                page_errors.append({"url": page, "status": r.status_code})
                continue
            if "html" not in (r.headers.get("Content-Type") or "").lower():
                continue
            parser = _Extractor()
            parser.feed(r.text)
        except requests.RequestException as exc:
            pages_checked += 1
            page_errors.append({"url": page, "status": None,
                                "reason": type(exc).__name__})
            continue
        except Exception:                      # noqa: BLE001 — malformed HTML
            continue

        for raw in parser.links:
            u = _normalise(page, raw)
            if not u:
                continue
            if len(targets) < MAX_TARGETS or u in targets:
                targets.setdefault(u, []).append((page, "link"))
            else:
                truncated = True
            if (crawling and _same_site(u, host) and u not in seen_pages
                    and len(queue) < MAX_PAGES):
                seen_pages.add(u)
                queue.append(u)
        for raw in parser.images:
            u = _normalise(page, raw)
            if not u:
                continue
            if len(targets) < MAX_TARGETS or u in targets:
                targets.setdefault(u, []).append((page, "image"))
            else:
                truncated = True

    if len(queue) > pages_checked:
        truncated = True

    # ---- check every distinct target once -------------------------------
    results: dict[str, dict] = {}
    urls = list(targets.keys())

    def worker(u: str):
        if time.time() - started > DEADLINE:
            return u, None
        return u, _check_one(session, u)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for u, res in pool.map(worker, urls):
                if res is None:
                    truncated = True
                else:
                    results[u] = res
    except Exception as exc:                   # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "broken": [],
                "pages_checked": pages_checked, "links_checked": len(results)}

    broken: list[dict] = []
    blocked: list[dict] = []
    for u, res in results.items():
        if res["ok"]:
            continue
        sources = targets.get(u, [])
        kind = "image" if any(k == "image" for _, k in sources) else "link"
        row = {
            "url": u,
            "kind": kind,
            "status": res["status"],
            "reason": res["reason"],
            "external": not _same_site(u, host),
            "found_on": sorted({src for src, _ in sources})[:5],
            "occurrences": len(sources),
        }
        # An external host that answers 403/429 is refusing the checker, not
        # serving a dead page. Listing it as broken sends someone chasing a
        # link that works perfectly in a browser.
        if res["blocked"] and row["external"]:
            row["reason"] = f"HTTP {res['status']} — host refuses automated checks"
            blocked.append(row)
        else:
            broken.append(row)

    broken.sort(key=lambda r: (r["external"], r["kind"] != "image",
                               -r["occurrences"]))

    return {
        "error": "",
        "broken": broken,
        "blocked": blocked,
        "page_errors": page_errors,
        "pages_checked": pages_checked,
        "links_checked": len(results),
        "broken_count": len(broken),
        "broken_images": sum(1 for r in broken if r["kind"] == "image"),
        "broken_links": sum(1 for r in broken if r["kind"] == "link"),
        "truncated": truncated,
        "limits": {"max_pages": MAX_PAGES, "max_targets": MAX_TARGETS},
        "seconds": round(time.time() - started, 1),
    }


def to_csv(result: dict) -> str:
    """The broken list as CSV — what actually gets handed to whoever fixes it."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Type", "URL", "Status", "Problem", "Scope", "Times used",
                "Found on"])
    for r in result.get("broken", []):
        w.writerow([r["kind"], r["url"], r["status"] or "", r["reason"],
                    "external" if r["external"] else "this site",
                    r["occurrences"], " | ".join(r["found_on"])])
    return buf.getvalue()


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def csv_filename(domain: str) -> str:
    return f"{_SAFE.sub('-', domain or 'site').strip('-')}-broken-links.csv"
