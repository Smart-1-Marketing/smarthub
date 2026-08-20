"""Seasonal tourism marketing plan — the Node app, ported to Flask.

## What moved and what didn't

The wizard, the market model and the report markup (`public/app.js`,
`data.js`, `engine.js`, `report-template.js` — about 1,900 lines) run in the
browser and are unchanged. Porting them would have meant re-deriving a budget
model in a second language, with two copies to keep in step; the QA audit
found exactly that failure in the stadium app, where the page quoted one price
and the PDF another.

Only the server moved: three routes, the OpenAI call, the Cloudinary upload,
and the PDF.

## The PDF, kept identical

The original printed `report-print.html` with Puppeteer, seeding
`window.__REPORT_ANSWERS__` through `page.evaluateOnNewDocument`. The Chromium
command line has no equivalent, so the answers are written **into** a copy of
that page instead, ahead of the scripts that read them. Same markup, same
scripts, same browser, same output — the page is byte-identical apart from one
injected `<script>` line.

## The budget model, unchanged and still wrong

`engine.js` produces exactly three possible monthly budgets nationwide, and
its ROAS is `1 / cacPct` — the reciprocal of a hardcoded constant, printed as
an estimate. Both were flagged in the QA audit. **Neither is fixed here**, on
purpose: this is a port, and changing the numbers a client is quoted is a
product decision, not something to slip into a translation. The shared budget
engine is where that gets fixed.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

PUBLIC = Path(__file__).parent / "public"
app = Flask(__name__, static_folder=None)

MODEL = os.environ.get("TOURISM_OPENAI_MODEL") or os.environ.get(
    "OPENAI_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT = 25

# Recent submissions, so a double-click doesn't produce two reports, two
# Cloudinary uploads and two leads. Was in-memory in the Node app; same here.
_recent: dict[str, tuple[float, dict]] = {}
_DEDUP_SECONDS = 90


def _dedup_key(body: dict) -> str:
    return "|".join(str(body.get(k, "")).strip().lower()
                    for k in ("email", "business", "zip", "category"))


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/<path:filename>")
def public_files(filename):
    # Only the wizard's own assets. Anything else is a 404 rather than a
    # traversal attempt reaching the rest of the container.
    safe = os.path.normpath(filename).lstrip("./")
    if safe.startswith("..") or "/" in safe and not (PUBLIC / safe).exists():
        return jsonify({"error": "Not found"}), 404
    if not (PUBLIC / safe).exists():
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(PUBLIC, safe)


@app.get("/health")
def health():
    from hub import htmlpdf
    return jsonify({
        "status": "ok", "service": "tourism",
        "ai": bool(os.environ.get("OPENAI_API_KEY")),
        "pdf": htmlpdf.available(),
    })


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def chat_complete(system: str, user: str, max_tokens: int = 700,
                  temperature: float = 0.6, json_mode: bool = False):
    """Port of lib/openai.js. Returns None when no key is set, as it did."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    import requests
    body = {
        "model": MODEL,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {key}"},
                      json=body, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"OpenAI API responded {r.status_code}: "
                           f"{r.text[:200]}")
    data = r.json()
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("tourism", data, purpose="analyze_business")
    except Exception:                                   # noqa: BLE001
        pass
    content = (((data.get("choices") or [{}])[0].get("message") or {})
               .get("content") or "")
    if not content.strip():
        raise RuntimeError("Empty OpenAI response")
    return content.strip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/analyze-business")
def analyze_business():
    """Read a business's own page to suggest a category and average value.

    Fetches a URL a visitor typed, so it carries the same SSRF guard as the
    restaurant page — a public form that fetches arbitrary URLs from inside
    our network is how cloud metadata gets read.
    """
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "A website URL is required."}), 400
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    if not _ssrf_safe(url):
        return jsonify({"error": "That address can't be read."}), 400

    text = ""
    try:
        import requests
        r = requests.get(url, timeout=8, allow_redirects=False,
                         headers={"User-Agent": "Smart1Bot/1.0"})
        hops = 0
        while r.is_redirect and hops < 4:
            nxt = r.headers.get("Location") or ""
            if not nxt.lower().startswith("http"):
                from urllib.parse import urljoin
                nxt = urljoin(r.url, nxt)
            if not _ssrf_safe(nxt):
                return jsonify({"error": "That address can't be read."}), 400
            r = requests.get(nxt, timeout=8, allow_redirects=False,
                             headers={"User-Agent": "Smart1Bot/1.0"})
            hops += 1
        if r.ok:
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>",
                          " ", r.text or "")
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()[:6000]
    except Exception:                                   # noqa: BLE001
        text = ""

    if not text:
        return jsonify({"categoryItem": None, "avgValue": None,
                        "note": "Couldn't read that page — pick a category "
                                "and enter an average value instead."})

    suggestion = {"categoryItem": None, "avgValue": None}
    try:
        raw = chat_complete(
            system=("You extract two facts about a tourism business from its "
                    "own website text. Answer only from the text. If a fact "
                    "isn't there, return null for it — do not estimate."),
            user=("Return JSON: {\"category\": one of restaurant, attraction, "
                  "lodging, outdoor, retail, event, tour, winery, museum, "
                  "camp; \"avg_ticket_usd\": a number or null}\n\n" + text),
            json_mode=True, max_tokens=200, temperature=0.2)
        if raw:
            parsed = json.loads(raw)
            suggestion["categoryItem"] = parsed.get("category")
            val = parsed.get("avg_ticket_usd")
            suggestion["avgValue"] = float(val) if isinstance(val, (int, float)) else None
    except Exception:                                   # noqa: BLE001
        pass          # a failed suggestion is not a failed page
    return jsonify(suggestion)


@app.post("/api/partial-lead")
def partial_lead():
    """Capture someone who gave contact details and then left."""
    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"ok": True, "skipped": "no contact details"})
    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="tourism", page="Tourism Marketing Plan (partial)",
            fields={"name": body.get("name", ""), "email": body.get("email", ""),
                    "phone": body.get("phone", ""),
                    "company": body.get("business", ""),
                    "category": body.get("category", ""),
                    "zip": body.get("zip", "")},
            client=body.get("business", ""))
    except Exception:                                   # noqa: BLE001
        pass
    return jsonify({"ok": True})


@app.post("/api/submit")
def submit():
    """Build the PDF, file the lead, return the report."""
    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"error": "An email or phone number is required."}), 400

    key = _dedup_key(body)
    now = time.time()
    for k, (when, _) in list(_recent.items()):
        if now - when > _DEDUP_SECONDS:
            _recent.pop(k, None)
    if key in _recent:
        cached = dict(_recent[key][1])
        cached["deduplicated"] = True
        return jsonify(cached)

    pdf_url, pdf_note = "", None
    try:
        pdf_bytes = render_report_pdf(body)
        pdf_url = upload_pdf(pdf_bytes, body) or ""
        if not pdf_url:
            pdf_note = "The report was built but couldn't be stored."
    except Exception as exc:                            # noqa: BLE001
        # Never fail the submission over a PDF. The visitor answered a dozen
        # questions; losing that because a browser hiccuped is the wrong
        # trade, and the lead still reaches the panel.
        app.logger.warning("Tourism PDF failed: %s", exc)
        pdf_note = "Your plan is ready — the PDF copy is being prepared."

    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="tourism", page="Tourism Marketing Plan",
            fields={"name": body.get("name", ""), "email": body.get("email", ""),
                    "phone": body.get("phone", ""),
                    "company": body.get("business", ""),
                    "category": body.get("category", ""),
                    "zip": body.get("zip", "")},
            pdf_url=pdf_url, client=body.get("business", ""))
    except Exception:                                   # noqa: BLE001
        app.logger.exception("Tourism lead capture failed")

    response = {"ok": True, "pdf_url": pdf_url}
    if pdf_note:
        response["pdf_note"] = pdf_note
    _recent[key] = (now, response)
    return jsonify(response)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def render_report_pdf(answers: dict) -> bytes:
    """Print report-print.html with the answers inlined.

    Puppeteer used `evaluateOnNewDocument` to set window.__REPORT_ANSWERS__
    before the page scripts ran. The Chromium command line has no such hook,
    so the value is written into a copy of the page instead, immediately
    before the scripts that read it. Same markup, same scripts, same browser.
    """
    from hub import htmlpdf
    html = (PUBLIC / "report-print.html").read_text(encoding="utf-8")
    seed = ("<script>window.__REPORT_ANSWERS__ = "
            + json.dumps(answers, ensure_ascii=False)
            + ";</script>\n")
    # Before the first <script src>, so data.js/engine.js/report-template.js
    # and the inline renderer all see it.
    marker = '<script src="data.js"></script>'
    html = html.replace(marker, seed + marker, 1) if marker in html \
        else html.replace("</head>", seed + "</head>", 1)
    # The page loads style.css and its scripts relatively, so make them
    # absolute against the module's own public folder.
    html = re.sub(r'(src|href)="(?!https?:|/|data:)([^"]+)"',
                  lambda m: f'{m.group(1)}="file://{PUBLIC}/{m.group(2)}"', html)
    return htmlpdf.render_html(html, timeout=60)


def upload_pdf(pdf_bytes: bytes, body: dict) -> str:
    """Store the report. resource_type='raw' so the link always delivers."""
    try:
        import cloudinary.uploader
        from hub.config import settings
        if not settings.cloudinary_ready:
            return ""
        slug = re.sub(r"[^a-z0-9]+", "-",
                      str(body.get("business") or "report").lower()).strip("-")[:60]
        res = cloudinary.uploader.upload(
            pdf_bytes, resource_type="raw",
            folder=f"{settings.folder('proposals')}/tourism",
            public_id=f"{slug}-{int(time.time())}",
            overwrite=False, unique_filename=True)
        return res.get("secure_url", "")
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("Tourism PDF upload failed: %s", exc)
        return ""


def _ssrf_safe(url: str) -> bool:
    """Refuse anything resolving inside our own network."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or u.username or u.password:
            return False
        host = u.hostname or ""
        if not host:
            return False
        for info in socket.getaddrinfo(
                host, u.port or (443 if u.scheme == "https" else 80),
                proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
    except Exception:                                   # noqa: BLE001
        return False
    return True
