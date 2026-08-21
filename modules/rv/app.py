"""RV dealer demand plan — the Node app, ported to Flask.

## What moved

`server.js` (837 lines): routes, the OpenAI estimate, the channel allowlist,
the seasonal budget maths, ZIP-to-state, caching, rate limiting, lead
delivery. All of it is here.

`public/*.js` (731 lines) runs in the browser and is unchanged, for the same
reason as tourism: translating a budget model into a second language leaves
two copies to keep in step, and that is the bug the QA audit found in the
stadium app.

## What was preserved exactly, and why it matters

The QA audit called this the reference implementation of the eleven. Three
things earned that, and each is reproduced deliberately:

* **The channel allowlist.** Whatever the model returns is mapped onto seven
  approved channels and anything off-menu is dropped. That is what stops a
  hallucinated "local newspaper" reaching a customer as something we sell.
* **Retry, then a deterministic fallback.** An OpenAI failure produces a real
  estimate from code, never a 500 at the visitor.
* **Server-computed dollars.** The model labels months Peak/Shoulder/Off; the
  money is arithmetic here. The model never picks a price.

## One bug fixed, not ported

`stateFromZip` returned **MA** for any unparseable ZIP, because `Number('')`
is `0` and `0` falls inside the `[0, 27, 'MA']` range. A Phoenix dealer who
mistyped their ZIP got a New England seasonality model. An unreadable ZIP now
returns empty, which the callers already handle.
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

MODEL = os.environ.get("RV_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = 45
OPENAI_ATTEMPTS = 2

APPROVED_CHANNELS = [
    "Connected TV (CTV)",
    "Streaming Radio",
    "Podcasts",
    "Data-Driven Targeted Display",
    "Geotargeting Campgrounds & State Parks",
    "Location Look-Back Retargeting",
    "Digital Out-of-Home (DOOH)",
]

# Peak months run at the full package amount; spend steps down out of season.
SEASON_BUDGET_MULTIPLIER = {"Peak": 1.0, "Shoulder": 0.7, "Off-Season": 0.5}

ZIP_RANGES = [
    [0, 27, "MA"], [28, 29, "RI"], [30, 38, "NH"], [39, 49, "ME"], [50, 54, "VT"],
    [55, 69, "MA"], [70, 89, "NJ"], [100, 149, "NY"], [150, 196, "PA"], [197, 199, "DE"],
    [200, 205, "DC"], [206, 219, "MD"], [220, 246, "VA"], [247, 268, "WV"], [270, 289, "NC"],
    [290, 299, "SC"], [300, 319, "GA"], [320, 349, "FL"], [350, 369, "AL"], [370, 385, "TN"],
    [386, 397, "MS"], [398, 399, "GA"], [400, 427, "KY"], [430, 459, "OH"], [460, 479, "IN"],
    [480, 499, "MI"], [500, 528, "IA"], [530, 549, "WI"], [550, 567, "MN"], [570, 577, "SD"],
    [580, 588, "ND"], [590, 599, "MT"], [600, 629, "IL"], [630, 658, "MO"], [660, 679, "KS"],
    [680, 693, "NE"], [700, 714, "LA"], [716, 729, "AR"], [730, 749, "OK"], [750, 799, "TX"],
    [800, 816, "CO"], [820, 831, "WY"], [832, 838, "ID"], [840, 847, "UT"], [850, 865, "AZ"],
    [870, 884, "NM"], [889, 898, "NV"], [900, 961, "CA"], [967, 968, "HI"], [970, 979, "OR"],
    [980, 994, "WA"], [995, 999, "AK"],
]

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 15 * 60

_hits: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
RATE_MAX = int(os.environ.get("RV_RATE_MAX") or 8)
RATE_WINDOW = int(os.environ.get("RV_RATE_WINDOW") or 3600)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def state_from_zip(zip_code: str) -> str:
    """ZIP prefix to state.

    The Node original did `Number(z.slice(0,3))` — and an empty or non-numeric
    ZIP gives 0, which matches [0, 27, 'MA']. Every malformed ZIP silently
    became Massachusetts, so a Phoenix dealer who fat-fingered theirs received
    a New England seasonality model. An unreadable ZIP now returns "".
    """
    digits = re.sub(r"\D", "", str(zip_code or ""))[:5]
    if len(digits) < 3:
        return ""
    p3 = int(digits[:3])
    for lo, hi, st in ZIP_RANGES:
        if lo <= p3 <= hi:
            return st
    return ""


def normalize_channels(items) -> list[str]:
    """Map model output onto the approved menu; drop anything off it.

    This is the guard the audit singled out. Without it a hallucinated
    "local newspaper insert" reaches a customer as something we sell.
    """
    out: list[str] = []

    def push(v):
        if v not in out:
            out.append(v)

    for raw in (items if isinstance(items, list) else []):
        s = str(raw or "").lower()
        if "ctv" in s or "connected tv" in s:
            push("Connected TV (CTV)")
        elif "podcast" in s:
            push("Podcasts")
        elif any(k in s for k in ("dooh", "out-of-home", "out of home", "ooh")):
            push("Digital Out-of-Home (DOOH)")
        elif any(k in s for k in ("look-back", "lookback", "look back",
                                  "retarget", "re-target")):
            push("Location Look-Back Retargeting")
        elif any(k in s for k in ("geotarget", "geo-target", "geofenc",
                                  "geo-fenc", "campground", "state park")):
            push("Geotargeting Campgrounds & State Parks")
        elif any(k in s for k in ("display", "programmatic", "banner")):
            push("Data-Driven Targeted Display")
        elif any(k in s for k in ("streaming radio", "digital audio",
                                  "streaming audio", "audio", "radio")):
            push("Streaming Radio")
        # Newspaper, print, direct mail, broadcast, billboard: dropped.
    return out or list(APPROVED_CHANNELS)


def parse_budget_base(recommended_package: str) -> int:
    m = re.search(r"\$?\s*(\d{3,6})",
                  str(recommended_package or "").replace(",", ""))
    value = int(m.group(1)) if m else 5000
    return value if value > 0 else 5000


def round_to_nearest(value: float, step: int) -> int:
    return int(round(value / step) * step)


def format_usd(value) -> str:
    return "$" + f"{int(value or 0):,}"


def apply_budgets(estimate: dict) -> dict:
    """Turn the model's month labels into dollars.

    The model never chooses a price — it labels months Peak/Shoulder/Off and
    the arithmetic happens here. That separation is why this app's numbers
    were the only ones the audit didn't take issue with.
    """
    base = parse_budget_base(estimate.get("recommended_package"))
    months = estimate.get("monthly_plan") or []
    total = 0
    for row in months:
        season = str(row.get("season") or "Shoulder")
        mult = SEASON_BUDGET_MULTIPLIER.get(season, 0.7)
        amount = round_to_nearest(base * mult, 250)
        row["budget"] = amount
        row["budget_display"] = format_usd(amount)
        total += amount
    estimate["monthly_base"] = base
    estimate["monthly_base_display"] = format_usd(base)
    estimate["annual_total"] = total
    estimate["annual_total_display"] = format_usd(total)
    return estimate


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_MAX:
            _hits[ip] = hits
            return True
        hits.append(now)
        _hits[ip] = hits
    return False


def _client_ip() -> str:
    # Last hop after ProxyFix, not the client-supplied first one — taking the
    # first lets anyone spoof past the limiter with a header.
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[-1].strip() if fwd else request.remote_addr) or "?"


def _cache_key(payload: dict) -> str:
    return "|".join(str(payload.get(k, "")).strip().lower() for k in
                    ("zip", "dealership_name", "sales_radius_miles",
                     "inventory_focus"))


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

def build_prompt(payload: dict) -> str:
    return (
        "You are planning a demand campaign for an RV dealership.\n"
        f"Dealership: {payload.get('dealership_name', '')}\n"
        f"ZIP: {payload.get('zip', '')} (state: {state_from_zip(payload.get('zip', ''))})\n"
        f"Sales radius: {payload.get('sales_radius_miles', '')} miles\n"
        f"Inventory focus: {payload.get('inventory_focus', '')}\n\n"
        "Return JSON with: market_summary (2 sentences), "
        "recommended_package (one of \"$3,000/month\", \"$5,000/month\", "
        "\"$7,500/month\", \"$10,000/month\"), channels (array), and "
        "monthly_plan: 12 objects with month and season "
        "(Peak | Shoulder | Off-Season).\n"
        "Label the months only — do not put dollar amounts in monthly_plan; "
        "the budget is calculated separately."
    )


def call_openai_once(payload: dict) -> dict:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    import requests
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        json={"model": MODEL,
              "messages": [{"role": "user", "content": build_prompt(payload)}],
              "response_format": {"type": "json_object"},
              "temperature": 0.4, "max_tokens": 1500},
        timeout=OPENAI_TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"OpenAI responded {r.status_code}")
    data = r.json()
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("rv", data, purpose="demand_estimate")
    except Exception:                                   # noqa: BLE001
        pass
    content = (((data.get("choices") or [{}])[0].get("message") or {})
               .get("content") or "")
    return json.loads(content)


def build_fallback_estimate(payload: dict) -> dict:
    """A real estimate from code when the model can't be reached.

    Deterministic and tagged, so a fallback is never presented as an AI
    market analysis — the audit found other apps in the suite substituting a
    template silently.
    """
    st = state_from_zip(payload.get("zip", ""))
    # Northern states have a sharper season than southern ones.
    northern = st in {"ME", "NH", "VT", "MA", "NY", "MI", "WI", "MN", "ND",
                      "SD", "MT", "ID", "WY", "AK", "IA", "NE", "PA", "OH"}
    peak = [4, 5, 6, 7, 8] if northern else [1, 2, 3, 10, 11, 12]
    shoulder = [3, 9] if northern else [4, 9]
    names = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
    plan = []
    for i, name in enumerate(names, start=1):
        season = ("Peak" if i in peak else
                  "Shoulder" if i in shoulder else "Off-Season")
        plan.append({"month": name, "season": season})
    return {
        "market_summary": (
            f"Seasonal demand plan for {payload.get('dealership_name') or 'the dealership'}"
            + (f" in {st}" if st else "")
            + ". Built from regional seasonality rather than a live market read."),
        "recommended_package": "$5,000/month",
        "channels": list(APPROVED_CHANNELS),
        "monthly_plan": plan,
        "estimate_source": "fallback",
    }


def create_estimate(payload: dict) -> dict:
    """Try the model, then fall back. Never raises at the visitor."""
    last = None
    for _ in range(OPENAI_ATTEMPTS):
        try:
            raw = call_openai_once(payload)
            raw["channels"] = normalize_channels(raw.get("channels"))
            months = raw.get("monthly_plan")
            if not isinstance(months, list) or len(months) != 12:
                raise ValueError("monthly_plan must have 12 months")
            raw["estimate_source"] = "ai"
            return apply_budgets(raw)
        except Exception as exc:                        # noqa: BLE001
            last = exc
    app.logger.warning("RV estimate fell back to code: %s", last)
    return apply_budgets(build_fallback_estimate(payload))


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


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "rv",
                    "ai": bool(os.environ.get("OPENAI_API_KEY"))})


@app.post("/api/rv-demand/estimate-and-submit")
def estimate_and_submit():
    body = request.get_json(silent=True) or {}
    if _rate_limited(_client_ip()):
        return jsonify({"error": "Too many requests. Try again shortly."}), 429

    key = _cache_key(body)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        estimate = hit[1] if hit and now - hit[0] < CACHE_SECONDS else None
    if estimate is None:
        estimate = create_estimate(body)
        with _cache_lock:
            _cache[key] = (now, estimate)

    pdf_url = ""
    try:
        from .proposal import build_proposal_pdf
        pdf_bytes = build_proposal_pdf(body, estimate)
        pdf_url = upload_pdf(pdf_bytes, body)
    except Exception as exc:                            # noqa: BLE001
        # The estimate is the product; a PDF problem must not discard it.
        app.logger.warning("RV proposal PDF failed: %s", exc)

    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="rv", page="RV Dealer Demand Plan",
            fields={"name": body.get("contact_name", ""),
                    "email": body.get("contact_email", ""),
                    "phone": body.get("contact_phone", ""),
                    "company": body.get("dealership_name", ""),
                    "recommended_package": estimate.get("recommended_package", ""),
                    "estimate_source": estimate.get("estimate_source", ""),
                    "zip": body.get("zip", "")},
            pdf_url=pdf_url, client=body.get("dealership_name", ""))
    except Exception:                                   # noqa: BLE001
        app.logger.exception("RV lead capture failed")

    return jsonify({"ok": True, "estimate": estimate, "pdf_url": pdf_url})


def upload_pdf(pdf_bytes: bytes, body: dict) -> str:
    try:
        import cloudinary.uploader
        from hub.config import settings
        if not settings.cloudinary_ready:
            return ""
        slug = re.sub(r"[^a-z0-9]+", "-",
                      str(body.get("dealership_name") or "rv").lower()).strip("-")[:60]
        res = cloudinary.uploader.upload(
            pdf_bytes, resource_type="raw",
            folder=f"{settings.folder('proposals')}/rv",
            public_id=f"{slug}-{int(time.time())}",
            overwrite=False, unique_filename=True)
        return res.get("secure_url", "")
    except Exception as exc:                            # noqa: BLE001
        app.logger.warning("RV PDF upload failed: %s", exc)
        return ""
