"""Stadium to Screen — the Node app, ported to Flask.

## What moved

`server.js` (392 lines): the config endpoint, the AI-matched recommendation
lists, lead capture and the playbook PDF. `lib/pdf.js` (429 lines) is rebuilt
in reportlab as `pdf.py`.

`public/*.js` stays in the browser, as with tourism and RV. The market model
(`data.js`, 481 lines of DMA populations, teams and pricing) is the thing this
app *is*; translating it into a second language would leave two copies to keep
in step.

## The drift this app is famous for

The QA audit found the page quoting $4,500 and the PDF $6,500 for the same
named package — moving in both directions, so it read as bait-and-switch. That
has since been fixed upstream: both tables now read 2500/4500/6500.

But the model still exists in **three** copies — inlined in `index.html`,
again in `public/data.js`, and again in `lib/pdf.js`. They agree today. The
port removes one of those: the Python side reads the pricing from `data.js`
rather than restating it, so a change there reaches the PDF.

## Fixed on the way, not carried

The pdfkit version set a bottom margin of 70 while drawing footers at y=752,
below it — so PDFKit appended a page per footer. The audit measured **9 pages,
6 of them blank**, with counters reading "1 / 3" on physical page 5. The
reportlab rebuild draws footers inside the margin.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

PUBLIC = Path(__file__).parent / "public"
app = Flask(__name__, static_folder=None)

MODEL = os.environ.get("STADIUM_OPENAI_MODEL") or os.environ.get(
    "OPENAI_MODEL", "gpt-4o-mini")

_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
RATE_MAX = int(os.environ.get("STADIUM_RATE_MAX") or 10)
RATE_WINDOW = 3600


# ---------------------------------------------------------------------------
# Pricing, read from the browser's own model
# ---------------------------------------------------------------------------

_pricing_cache: dict | None = None


def pricing() -> dict:
    """Parse PRICING out of public/data.js.

    Read rather than restated. A fourth copy of this table in Python is a
    fourth chance for the page and the PDF to disagree — which is the defect
    this app is known for.
    """
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache
    fallback = {"audio": {"local": 2500, "regional": 4500, "national": 6500},
                "ctv": {"local": 2900, "regional": 4800, "national": 7500},
                "both": {"local": 4000, "regional": 7500, "national": 10000}}
    try:
        src = (PUBLIC / "data.js").read_text(encoding="utf-8")
        block = re.search(r"const PRICING\s*=\s*\{(.*?)\};", src, re.S)
        if not block:
            _pricing_cache = fallback
            return _pricing_cache
        out: dict[str, dict] = {}
        for focus, body in re.findall(r"(\w+)\s*:\s*\{([^}]*)\}", block.group(1)):
            out[focus] = {k: int(v) for k, v in
                          re.findall(r"(\w+)\s*:\s*(\d+)", body)}
        _pricing_cache = out or fallback
    except Exception:                                   # noqa: BLE001
        _pricing_cache = fallback
    return _pricing_cache


def price_for(focus: str, scope: str) -> int:
    table = pricing()
    return (table.get(focus) or table.get("audio", {})).get(
        scope or "local", 2500)


FOCUS_LABEL = {"audio": "Streaming Audio", "ctv": "Connected TV",
               "both": "Audio + Connected TV"}
SCOPE_LABEL = {"local": "Local", "regional": "Regional", "national": "National"}


def recommend_package(focus: str, scope: str) -> dict:
    amount = price_for(focus, scope)
    return {
        "name": f"{FOCUS_LABEL.get(focus, focus)} · {SCOPE_LABEL.get(scope, scope)}",
        "price": "$" + f"{amount:,}" + "/mo",
        "amount": amount,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/<path:filename>")
def public_files(filename):
    safe = os.path.normpath(filename).lstrip("./")
    if safe.startswith("..") or not (PUBLIC / safe).exists():
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(PUBLIC, safe)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "stadium",
                    "ai": bool(os.environ.get("OPENAI_API_KEY")),
                    "pricing_source": "public/data.js"})


@app.get("/api/config")
def config():
    """What the page needs to know at boot.

    `backend` is returned explicitly. The Node version shipped
    `BACKEND_URL = ""`, so every lead POST resolved against the host page's
    own origin, 404'd, and the widget printed "Thanks — the full breakdown is
    unlocked" anyway. Every lead in that configuration was lost silently.
    """
    return jsonify({
        "backend": "",              # same origin — the Hub serves this page
        "ai": bool(os.environ.get("OPENAI_API_KEY")),
        "calendar_url": os.environ.get("STADIUM_CALENDAR_URL", ""),
    })


def _limited(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_MAX:
            _rate[ip] = hits
            return True
        hits.append(now)
        _rate[ip] = hits
    return False


def _ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[-1].strip() if fwd else request.remote_addr) or "?"


# This page is public — no Hub login — and /api/recommendations spends OpenAI
# credit per call, so without a ceiling the bill is set by whoever finds the
# URL. Same limits and same shared implementation as the other landing pages,
# so the rule lives in one place rather than being re-derived here.
ANALYZE_LIMIT = int(os.environ.get("LANDING_ANALYZE_LIMIT", "6"))
PARTIAL_LIMIT = int(os.environ.get("LANDING_PARTIAL_LIMIT", "30"))


def _rate_limited(bucket: str, limit: int) -> bool:
    try:
        from hub import leads as _hub_leads
        return _hub_leads.rate_limited(bucket, request, limit)
    except Exception:          # noqa: BLE001 — a guard must never 500 the page
        return False


@app.post("/api/recommendations")
def recommendations():
    if _rate_limited("analyze", ANALYZE_LIMIT):
        return jsonify({"ok": False,
                        "error": "Too many requests from this connection. "
                                 "Please wait a few minutes and try again."}), 429
    """AI-matched shows and apps for a team's market.

    The Node version fetched this **before** the contact form was shown, from
    an open endpoint — so the marquee content was free to anyone who called
    it. It now requires contact details, which is what the form is for.
    """
    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"error": "Contact details are required first."}), 403
    if _limited(_ip()):
        return jsonify({"error": "Too many requests. Try again shortly."}), 429

    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    team = str(body.get("team") or "").strip()
    market = str(body.get("market") or "").strip()
    if not key:
        return jsonify(_fallback_recommendations(team, market))
    try:
        import requests as _rq
        r = _rq.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "response_format": {"type": "json_object"},
                  "temperature": 0.3, "max_tokens": 900,
                  "messages": [{"role": "user", "content":
                    f"Return JSON with 'podcasts' (7 real sports podcasts, at "
                    f"least 3 local to {market} or covering {team}, each "
                    f"{{name, local: true|false}}) and 'apps' (6 streaming "
                    f"apps or networks carrying {team} games). Real shows "
                    f"only — do not invent titles."}]},
            timeout=40)
        r.raise_for_status()
        data = r.json()
        try:
            from hub import ai as _hub_ai
            _hub_ai.note_usage("stadium", data, purpose="recommendations")
        except Exception:                               # noqa: BLE001
            pass
        content = json.loads(
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}")
        pods = [p for p in (content.get("podcasts") or []) if p.get("name")]
        if sum(1 for p in pods if p.get("local")) < 3:
            # The original prompt asked for local shows and got Bill Simmons
            # and an NBA podcast for an NFL team. If the model ignores it,
            # fall back rather than presenting national shows as local.
            return jsonify(_fallback_recommendations(team, market))
        return jsonify({"podcasts": pods, "apps": content.get("apps") or [],
                        "source": "ai"})
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("Stadium recommendations fell back: %s", exc)
        return jsonify(_fallback_recommendations(team, market))


def _fallback_recommendations(team: str, market: str) -> dict:
    return {
        "podcasts": [{"name": f"{market} sports radio flagship show", "local": True},
                     {"name": f"{team} team-official podcast", "local": True},
                     {"name": f"{market} morning sports show", "local": True},
                     {"name": "Pardon My Take", "local": False},
                     {"name": "The Rich Eisen Show", "local": False}],
        "apps": ["Hulu Live", "YouTube TV", "ESPN", "Paramount+", "Peacock",
                 "Prime Video"],
        "source": "fallback",
        "note": "Curated list — the AI match wasn't available.",
    }


@app.post("/api/partial-lead")
def partial_lead():
    if _rate_limited("partial", PARTIAL_LIMIT):
        return jsonify({"ok": True})     # silent, like a honeypot

    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"ok": True, "skipped": "no contact details"})
    _capture(body, partial=True)
    return jsonify({"ok": True})


@app.post("/api/lead")
def lead():
    if _rate_limited("lead", PARTIAL_LIMIT):
        return jsonify({"ok": True})     # silent, like a honeypot

    """Capture the lead and return the playbook.

    The Node version caught a delivery failure and still printed
    "Your report is ready" with no PDF. Delivery can't fail that way now —
    the lead is stored before anything is sent.
    """
    body = request.get_json(silent=True) or {}
    if not (body.get("email") or body.get("phone")):
        return jsonify({"error": "An email or phone number is required."}), 400
    pdf_url = ""
    try:
        from .pdf import build_playbook
        pdf_bytes = build_playbook(body, recommend_package(
            str(body.get("focus") or "audio"), str(body.get("scope") or "local")))
        pdf_url = _upload(pdf_bytes, body)
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("Stadium playbook failed: %s", exc)
    _capture(body, pdf_url=pdf_url)
    return jsonify({"ok": True, "pdf_url": pdf_url,
                    "note": ("" if pdf_url else
                             "Your playbook is being prepared — we have your "
                             "details and will send it through.")})


@app.post("/api/playbook")
def playbook():
    if _rate_limited("playbook", ANALYZE_LIMIT):
        return jsonify({"ok": False, "error": "Too many requests from this connection. Please wait a few minutes and try again."}), 429

    """Build and stream the PDF only. No CRM, no upload.

    Kept from the Node version deliberately: the download can't be broken by
    an integration failure elsewhere.
    """
    from flask import send_file
    from io import BytesIO
    body = request.get_json(silent=True) or {}
    from .pdf import build_playbook
    data = build_playbook(body, recommend_package(
        str(body.get("focus") or "audio"), str(body.get("scope") or "local")))
    return send_file(BytesIO(data), mimetype="application/pdf",
                     as_attachment=True,
                     download_name="stadium-to-screen-playbook.pdf")


def _capture(body: dict, pdf_url: str = "", partial: bool = False) -> None:
    try:
        from hub import leads as hub_leads
        pkg = recommend_package(str(body.get("focus") or "audio"),
                                str(body.get("scope") or "local"))
        hub_leads.capture_and_deliver(
            source="stadium",
            page="Stadium to Screen" + (" (partial)" if partial else ""),
            fields={"name": body.get("name", ""), "email": body.get("email", ""),
                    "phone": body.get("phone", ""),
                    "company": body.get("company", ""),
                    "team": body.get("team", ""), "market": body.get("market", ""),
                    "package": pkg["name"], "monthly": pkg["price"]},
            pdf_url=pdf_url, client=body.get("company", ""))
        # Attribute the plan to the client, so the work appears on their 360
        # record rather than only in the lead panel — the same call the other
        # eight landing pages make.
        try:
            from hub import audit as _audit
            _audit.log("stadium", "stadium_report",
                       client=body.get("company", "") or None)
        except Exception:  # noqa: BLE001 — logging never breaks a lead
            pass
    except Exception:                                   # noqa: BLE001
        app.logger.exception("Stadium lead capture failed")


def _upload(pdf_bytes: bytes, body: dict) -> str:
    try:
        import cloudinary.uploader
        from hub.config import settings
        if not settings.cloudinary_ready:
            return ""
        slug = re.sub(r"[^a-z0-9]+", "-",
                      str(body.get("company") or "playbook").lower()).strip("-")[:60]
        # Through hub.storage like every other module: same folder and id, so
        # nothing moves, but the resource type comes from the filename rather
        # than being asserted here — the mistake that has 403'd PDF links
        # three times in this codebase.
        from hub import storage
        folder = f"{settings.folder('proposals')}/stadium"
        pid = f"{folder}/{slug}-{int(time.time())}"
        return storage.put("proposals", f"{pid}.pdf", pdf_bytes,
                           folder=folder, public_id=pid, overwrite=False).url
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("Stadium PDF upload failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Embedding on smart1marketing.com
# ---------------------------------------------------------------------------
#
# /embed and /embed.js, from the one shared implementation in hub/embed.py --
# see that module for why the marketing site frames this form rather than
# carrying a copy of it. Registered last, because install() frames whichever
# view answers "/" and that route has to exist first.
#
# Guarded: a landing page that will not import because its embed helper is
# missing is a worse outcome than a landing page nobody can embed. Recorded,
# because a swallowed exception is how /signup 404'd for a day with no clue.
try:
    from hub import embed as _s1_embed

    _s1_embed.install(app, 'Smart 1 Marketing — Football Audio & Video Playbook', default_height=2400)
except Exception as _exc_embed:                         # noqa: BLE001
    app.logger.warning("Embed routes unavailable: %s", _exc_embed)
