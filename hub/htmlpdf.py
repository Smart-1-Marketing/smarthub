"""HTML to PDF, rendered by the same browser the Node apps used.

Several landing pages build their report as an HTML template and print it with
headless Chromium. Rebuilding those documents in reportlab would change a PDF
clients already receive, so the browser comes with them — see the Dockerfile
for what that costs.

## Why a subprocess rather than Playwright

Playwright is another dependency, and it wants to manage its own browser
download. Chromium's own headless print-to-pdf does the same job through a
command line that has been stable for years, and it needs nothing installed
beyond the browser already in the image.

## Sandbox

`--no-sandbox` is required: Chromium's sandbox needs kernel capabilities a
container doesn't grant, and without the flag it exits immediately. That is
safe here **only because the HTML is ours** — it is a template this codebase
renders, not a page a visitor supplied. Passing user-supplied HTML or a
user-supplied URL through this would be handing a stranger a browser inside
our network; `render_url` therefore refuses anything that isn't local.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 60


class PdfUnavailable(RuntimeError):
    """No browser in this environment. Callers should degrade, not crash."""


def chrome_binary() -> str | None:
    """The Chromium to use, or None if this environment has none."""
    explicit = (os.environ.get("CHROME_BIN") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    for name in ("chromium", "chromium-browser", "google-chrome",
                 "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    return chrome_binary() is not None


def render_html(html: str, *, timeout: int = DEFAULT_TIMEOUT,
                landscape: bool = False, scale: float = 1.0) -> bytes:
    """Print a string of HTML to PDF bytes.

    The HTML is written to a temp file rather than passed as a data: URL —
    data URLs have length limits that a real report will exceed, and the
    failure is silent truncation rather than an error.
    """
    binary = chrome_binary()
    if not binary:
        raise PdfUnavailable(
            "No Chromium in this environment, so an HTML-rendered PDF can't be "
            "produced. The Docker image installs one; a local run without it "
            "should fall back rather than fail.")

    tmpdir = tempfile.mkdtemp(prefix="s1pdf-")
    src = os.path.join(tmpdir, "report.html")
    out = os.path.join(tmpdir, "report.pdf")
    try:
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(html)
        cmd = [
            binary, "--headless=new", "--disable-gpu",
            # Required in a container; safe only because this HTML is ours.
            "--no-sandbox", "--disable-dev-shm-usage",
            "--run-all-compositor-stages-before-draw",
            # Without this the browser waits on the network and the PDF is
            # produced before webfonts and images have painted.
            "--virtual-time-budget=8000",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out}",
        ]
        if landscape:
            cmd.append("--landscape")
        if scale and scale != 1.0:
            cmd.append(f"--force-device-scale-factor={scale}")
        cmd.append("file://" + src)

        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            detail = (proc.stderr or b"").decode("utf-8", "ignore")[-400:]
            raise PdfUnavailable(f"Chromium produced no PDF. {detail}")
        with open(out, "rb") as fh:
            data = fh.read()
        if not data.startswith(b"%PDF"):
            # A truncated or HTML error page must not be handed to a client
            # as a PDF — it downloads, fails to open, and looks like our fault.
            raise PdfUnavailable("Chromium returned something that isn't a PDF.")
        return data
    except subprocess.TimeoutExpired as exc:
        raise PdfUnavailable(f"PDF rendering timed out after {timeout}s.") from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def render_url(url: str, **kw) -> bytes:
    """Print a page the Hub itself serves.

    Deliberately restricted to our own origin. Rendering an arbitrary URL
    would hand a visitor a browser running inside our network, with access to
    anything the container can reach.
    """
    host = (urlparse(url).hostname or "").lower()
    allowed = {"localhost", "127.0.0.1", "::1"}
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if base:
        allowed.add((urlparse(base).hostname or "").lower())
    if host not in allowed:
        raise PdfUnavailable(
            "Only pages this Hub serves can be rendered. Pass the HTML "
            "directly with render_html() instead.")
    binary = chrome_binary()
    if not binary:
        raise PdfUnavailable("No Chromium in this environment.")
    tmpdir = tempfile.mkdtemp(prefix="s1pdf-")
    out = os.path.join(tmpdir, "page.pdf")
    try:
        subprocess.run(
            [binary, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-dev-shm-usage", "--virtual-time-budget=8000",
             "--no-pdf-header-footer", f"--print-to-pdf={out}", url],
            capture_output=True, timeout=kw.get("timeout", DEFAULT_TIMEOUT))
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise PdfUnavailable("Chromium produced no PDF for that page.")
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def status() -> dict:
    """For Diagnostics — is the browser actually there?"""
    binary = chrome_binary()
    version = ""
    if binary:
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True,
                                  timeout=10)
            version = (proc.stdout or b"").decode("utf-8", "ignore").strip()
        except Exception:                               # noqa: BLE001
            version = "present, but wouldn't report a version"
    return {
        "available": bool(binary),
        "binary": binary or "",
        "version": version,
        "note": ("Ready — HTML-rendered PDFs will match the originals."
                 if binary else
                 "No Chromium. Landing pages whose PDF is an HTML template "
                 "will fall back or fail; the Docker image installs one, so "
                 "this is expected only on a local run."),
    }
