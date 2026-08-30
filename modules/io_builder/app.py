import time
import tempfile
import json
import re
import threading
from datetime import datetime, timezone
import os
import logging
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS
import cloudinary
import cloudinary.utils
import cloudinary.uploader
import cloudinary.api
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv

from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak, KeepTogether
from reportlab.lib.utils import ImageReader
from xml.sax.saxutils import escape as xml_escape

load_dotenv()


def _hub_settings():
    """Hub settings, read at call time so a key added on Render lands on the
    next request rather than the next deploy.

    Wrapped: this module is also runnable on its own, where hub is not
    importable, and a diagnostics route must not 500 because of that.
    """
    try:
        from hub.config import settings
        return settings
    except Exception:                                 # noqa: BLE001
        class _Fallback:
            cloudinary_url = (os.getenv("CLOUDINARY_URL") or "").strip()
            brandfetch_key = (os.getenv("BRANDFETCH_API")
                              or os.getenv("BRANDFETCH_API_KEY") or "").strip()
            openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        return _Fallback()

from functools import wraps
from collections import defaultdict, deque

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Optional server-side error monitoring (Sentry). Activates only when SENTRY_DSN
# is set; a no-op otherwise. Never breaks the app if the package is missing.
try:
    _sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if _sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.0)
        logger.info("Sentry error monitoring enabled.")
except Exception as _sentry_exc:  # pragma: no cover
    logging.getLogger(__name__).warning("Sentry not initialized: %s", _sentry_exc)

# Lightweight in-memory rate limiter for the OpenAI-backed endpoints so a scraped
# URL can't run up the OpenAI bill. Per-process (fine as a basic guard); generous
# by default so normal rep usage is never blocked.
_RATE_BUCKETS = defaultdict(deque)
_RATE_LOCK = threading.Lock()
_AI_RATE_MAX = int(os.getenv("AI_RATE_MAX", "20"))
_AI_RATE_WINDOW = int(os.getenv("AI_RATE_WINDOW_SECONDS", "60"))

def _rate_limited(max_calls, per_seconds):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            now = time.time()
            fwd = request.headers.get("X-Forwarded-For", "") or (request.remote_addr or "unknown")
            ip = fwd.split(",")[0].strip() or "unknown"
            key = f"{fn.__name__}:{ip}"
            with _RATE_LOCK:
                dq = _RATE_BUCKETS[key]
                while dq and now - dq[0] > per_seconds:
                    dq.popleft()
                if len(dq) >= max_calls:
                    return jsonify({"error": "Too many requests. Please wait a moment and try again."}), 429
                dq.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return deco

# Cross-origin support. The IO builder page can be embedded on another domain
# (e.g. test.smart1marketing.com) while the API runs on Render, which makes the
# browser's /api calls cross-origin and blocks them ("Failed to fetch") unless
# the server returns CORS headers and answers preflight (OPTIONS) requests.
# ALLOWED_ORIGINS: "*" (default) or a comma-separated list of allowed origins.
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
if _allowed_origins_env in ("", "*"):
    _cors_origins = "*"
else:
    _cors_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
CORS(app, resources={r"/api/*": {"origins": _cors_origins}}, methods=["GET", "POST", "OPTIONS"])

# Set CLOUDINARY_URL in the environment. Never place the API secret in browser JavaScript.
cloudinary.config(secure=True)

@app.get('/health')
def health():
    template_path = BASE_DIR / 'templates' / 'index.html'
    return jsonify({
        'status': 'ok',
        'template_exists': template_path.exists(),
        'template_path': str(template_path),
        'cloudinary_configured': bool(_hub_settings().cloudinary_url),
        'brandfetch_configured': bool(_hub_settings().brandfetch_key),
        'openai_configured': bool(_hub_settings().openai_key),
        'order_counter_storage': 'cloudinary' if _cloudinary_is_configured() else 'temporary',
    })


def _own_api_paths():
    """This app's own /api/* routes, as plain prefixes for the page to use.

    The page rewrites /api/* fetches onto the mount prefix, because its own
    routes live at /tools/io/api/* once DispatcherMiddleware mounts it. That
    rewrite has to know which /api/ paths are OURS: the page also calls Hub
    routes that live at the site root (/api/io/from-proposal reads a proposal,
    /api/help draws the help layer), and prefixing those sent them to this app,
    which has no such route -- so reading a proposal 500'd and every help
    bubble on the page died with it. Derived from the route table rather than
    hand-listed, so a new route cannot be forgotten here.
    """
    out = set()
    for rule in app.url_map.iter_rules():
        path = str(rule.rule)
        if not path.startswith('/api/'):
            continue
        cut = path.find('<')                    # /api/thing/<id> -> /api/thing/
        out.add(path[:cut] if cut > 0 else path)
    return sorted(out)

@app.get('/')
def index():
    template_path = BASE_DIR / 'templates' / 'index.html'
    if not template_path.exists():
        logger.error('Missing template: %s', template_path)
        return (
            'SMART1 Campaign Builder deployment is missing templates/index.html. '
            'Upload the templates folder beside app.py and leave Render Root Directory blank.',
            500,
        )
    return render_template('index.html', own_api_paths=_own_api_paths())

@app.get('/api/cloudinary-config')
def cloudinary_config():
    cfg = cloudinary.config()
    if not cfg.cloud_name or not cfg.api_key or not cfg.api_secret:
        return jsonify({'error': 'Cloudinary is not configured'}), 503
    return jsonify({'cloud_name': cfg.cloud_name})



def _extract_brandfetch(payload, requested_domain):
    logos = payload.get('logos') or []
    logo_url = ''
    for logo in logos:
        for fmt in (logo.get('formats') or []):
            src = fmt.get('src')
            if src:
                logo_url = src
                break
        if logo_url:
            break
    colors = []
    for color in payload.get('colors') or []:
        value = color.get('hex') or color.get('value')
        if value and value not in colors:
            colors.append(value)
    fonts = []
    for font in payload.get('fonts') or []:
        name = font.get('name') or font.get('family')
        if name and name not in fonts:
            fonts.append(name)
    links = []
    for link in payload.get('links') or []:
        if isinstance(link, dict):
            links.append({'name': link.get('name') or link.get('type') or '', 'url': link.get('url') or ''})
        elif isinstance(link, str):
            links.append({'name': '', 'url': link})
    company = payload.get('company') or {}
    return {
        'status': 'loaded',
        'name': payload.get('name') or company.get('name') or '',
        'domain': payload.get('domain') or requested_domain,
        'description': payload.get('description') or company.get('description') or '',
        'logo': logo_url,
        'colors': colors[:12],
        'fonts': fonts[:12],
        'links': links[:20],
        'company': company,
        'brand_id': payload.get('id') or '',
        'claimed': payload.get('claimed'),
        'quality_score': payload.get('qualityScore') or payload.get('quality_score'),
    }

@app.get('/api/brandfetch')
def brandfetch_lookup():
    # Through hub.config, which accepts BRANDFETCH_API too — the spelling the
    # rest of this deployment's provider keys use. Read one way here, the
    # lookup answered "not configured" over a key Smart 1 Ads was using.
    api_key = _hub_settings().brandfetch_key
    client_id = os.getenv('BRANDFETCH_CLIENT_ID', '').strip()
    if not api_key:
        return jsonify({'error': 'Brandfetch is not configured'}), 503
    domain = (request.args.get('domain') or '').strip().lower()
    if '://' in domain:
        domain = urlparse(domain).hostname or ''
    domain = domain.removeprefix('www.').split('/')[0]
    if not domain or '.' not in domain:
        return jsonify({'error': 'A valid website domain is required'}), 400
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    if client_id:
        headers['X-Client-Id'] = client_id
    try:
        response = requests.get(f'https://api.brandfetch.io/v2/brands/domain/{domain}', headers=headers, timeout=20)
        if response.status_code == 404:
            return jsonify({'error': f'No Brandfetch profile was found for {domain}'}), 404
        response.raise_for_status()
        return jsonify(_extract_brandfetch(response.json(), domain))
    except requests.RequestException as exc:
        detail = ''
        if getattr(exc, 'response', None) is not None:
            detail = (exc.response.text or '')[:300]
        return jsonify({'error': 'Brandfetch request failed', 'detail': detail}), 502



def _extract_response_text(result):
    parts = []
    for item in result.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text") and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts).strip()

def _openai_response(prompt, max_output_tokens=6000):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OpenAI is not configured. Add OPENAI_API_KEY in Render.")
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        "input": prompt,
        "tools": [{"type": "web_search"}],
        "max_output_tokens": max_output_tokens,
    }
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    # Record the spend so it shows in the Hub's cost tracking. Migrated on the
    # way past, per the rule in CLAUDE.md — this module was already being
    # edited, so it moves onto the shared plumbing rather than staying
    # invisible.
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("io_builder", response.json(),
                           purpose="business_description")
    except Exception:  # noqa: BLE001
        pass
    return _extract_response_text(response.json())

@app.post('/api/generate-business-description')
@_rate_limited(_AI_RATE_MAX, _AI_RATE_WINDOW)
def generate_business_description():
    data = request.get_json(force=True) or {}
    urls = [str(u).strip() for u in (data.get('urls') or []) if str(u).strip()]
    if not urls:
        return jsonify({'error': 'At least one website URL is required'}), 400
    client = str(data.get('client') or '').strip()
    industry = str(data.get('industry') or '').strip()
    brand = data.get('brandfetch') or {}
    # The rules live in hub.business_description so the Proposal Builder writes
    # descriptions to the same Google Business Profile standard this route
    # already met -- it previously had a looser prompt of its own, and the same
    # client could end up with two different descriptions.
    from hub import business_description as _desc
    from hub import target_areas as _ta
    areas = _ta.normalize(data.get('targetAreas')) or _ta.from_legacy(data)
    prompt = _desc.prompt_for(urls, client=client, industry=industry,
                              areas=areas, brandfetch=brand,
                              geo=str(data.get('geo') or '').strip())
    try:
        description = _openai_response(prompt, max_output_tokens=5000)
        if not description:
            return jsonify({'error': 'OpenAI returned no description'}), 502
        return jsonify({'description': description,
                        'warnings': _desc.check(description)})
    except Exception as exc:
        detail = ''
        if getattr(exc, 'response', None) is not None:
            detail = (exc.response.text or '')[:500]
        return jsonify({'error': 'OpenAI description request failed', 'detail': detail or str(exc)}), 502

@app.get('/api/creative-specs')
def creative_specs_catalogue():
    """The whole 2025 spec kit, for the browser to render.

    Served rather than duplicated in JavaScript: the builder used to carry its
    own approximation of these tables, which drifted from the kit and had no
    way of being corrected in one place.
    """
    from hub import creative_specs
    return jsonify(creative_specs.catalogue())


@app.post('/api/creative-check')
def creative_check():
    """Judge one file against the kit. Pass/fail, with the reasons."""
    from hub import creative_specs
    d = request.get_json(silent=True) or {}

    def _int(v):
        try:
            return int(float(v or 0))
        except (TypeError, ValueError):
            return 0

    duration = None
    if d.get('duration') not in (None, ''):
        try:
            duration = float(d['duration'])
        except (TypeError, ValueError):
            duration = None

    verdict = creative_specs.check(
        width=_int(d.get('width')), height=_int(d.get('height')),
        size_bytes=_int(d.get('bytes')), fmt=str(d.get('format') or ''),
        duration=duration, product=str(d.get('product') or ''),
        category=str(d.get('category') or ''),
        unit_id=str(d.get('unit_id') or ''))
    verdict['tags'] = creative_specs.tags_for(
        verdict, product=str(d.get('product') or ''),
        client=str(d.get('client') or ''),
        width=_int(d.get('width')), height=_int(d.get('height')),
        size_bytes=_int(d.get('bytes')))
    return jsonify(verdict)


@app.post('/api/cloudinary-signature')
def cloudinary_signature():
    cfg = cloudinary.config()
    if not cfg.cloud_name or not cfg.api_key or not cfg.api_secret:
        return jsonify({'error': 'Cloudinary is not configured'}), 503
    data = request.get_json(force=True) or {}
    allowed = {'timestamp', 'folder', 'tags', 'context'}
    params = {k: data[k] for k in allowed if k in data and data[k] not in (None, '')}
    if 'timestamp' not in params:
        return jsonify({'error': 'timestamp is required'}), 400
    signature = cloudinary.utils.api_sign_request(params, cfg.api_secret)
    return jsonify({'signature': signature, 'api_key': cfg.api_key, 'cloud_name': cfg.cloud_name})




_ORDER_LOCK = threading.Lock()

def _cloudinary_counter_public_id():
    return os.environ.get(
        "ORDER_COUNTER_CLOUDINARY_ID",
        "smart1_system/order_counter.json"
    ).strip()

def _cloudinary_is_configured():
    cfg = cloudinary.config()
    return bool(cfg.cloud_name and cfg.api_key and cfg.api_secret)

ORDER_COUNTER_BASE = 10199  # first allocated order is BASE + 1 = 10200

def _is_not_found(exc):
    message = str(exc).lower()
    return "not found" in message or "404" in message or "resource not found" in message

def _read_cloudinary_order_counter():
    """Return the last allocated order number.

    Reads the value from the asset's *context metadata* via the Admin API, which
    does not require raw-file delivery (raw/PDF delivery is disabled by default on
    many Cloudinary accounts and is the usual reason the old body-fetch approach
    failed). Falls back to fetching the JSON body only if context is unavailable.
    Raises on a genuine not-found so the caller can treat it as the first run.
    """
    public_id = _cloudinary_counter_public_id()
    resource = cloudinary.api.resource(public_id, resource_type="raw", type="upload", context=True)
    context = (resource.get("context") or {})
    custom = context.get("custom") or context  # Admin API nests under "custom"
    value = custom.get("last_order_number") if isinstance(custom, dict) else None
    if value not in (None, ""):
        return int(value)
    # Fallback: try to read the file body (works only if raw delivery is enabled).
    url = resource.get("secure_url")
    if url:
        response = requests.get(url, params={"cb": str(int(time.time() * 1000))}, timeout=20,
                                headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        return int(response.json().get("last_order_number", ORDER_COUNTER_BASE))
    return ORDER_COUNTER_BASE

def _write_cloudinary_order_counter(number):
    """Persist the counter both as context metadata (delivery-independent) and as
    the file body. Raises if the upload itself fails so the caller can fall back."""
    public_id = _cloudinary_counter_public_id()
    payload = json.dumps({
        "last_order_number": int(number),
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
        handle.write(payload)
        temp_path = handle.name
    try:
        # Through hub.storage. Same public_id, context and tags — this is the
        # order-number counter, so where it lives must not change. The
        # temporary file is no longer needed: put() takes bytes.
        from hub import storage
        storage.put("io_builder", f"{public_id}.json",
                    payload.encode("utf-8"),
                    public_id=public_id, overwrite=True,
                    context={"last_order_number": str(int(number))},
                    tags=["smart1_system", "smart1_order_counter"])
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

def _temporary_counter_path():
    return Path(os.environ.get(
        "ORDER_COUNTER_FALLBACK_FILE",
        "/tmp/smart1_order_counter.json"
    ))

def _read_temp_counter():
    path = _temporary_counter_path()
    if path.exists():
        try:
            return int(json.loads(path.read_text(encoding="utf-8")).get("last_order_number", ORDER_COUNTER_BASE))
        except Exception:
            logger.exception("Unable to read temporary order counter")
    return ORDER_COUNTER_BASE

def _write_temp_counter(number):
    path = _temporary_counter_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_order_number": int(number)}), encoding="utf-8")
    except Exception:
        logger.exception("Unable to write temporary order counter")

def _postgres_order_number():
    """Take the next order number from a Postgres sequence.

    `threading.Lock` only holds inside one process. Gunicorn runs two workers,
    so two reps clicking "New IO" at the same moment could each read the same
    counter and get the same number — the exact risk flagged as open in the
    handoff. A sequence is atomic across every worker and every connection,
    which no amount of application-level locking can be.

    Returns None when there's no Postgres, so the Cloudinary path still runs.
    """
    try:
        from hub.extensions import db
        from sqlalchemy import text
        engine = db.engine
        if not engine.dialect.name.startswith("postgres"):
            return None
        with engine.connect() as cx:
            cx.execute(text(
                "CREATE SEQUENCE IF NOT EXISTS io_order_number_seq "
                "START WITH 1000 INCREMENT BY 1"))
            cx.commit()
            val = cx.execute(text("SELECT nextval('io_order_number_seq')")).scalar()
            cx.commit()
        return str(int(val))
    except Exception:                                   # noqa: BLE001
        return None


def _next_order_number():
    """Return (order_number, storage, warning). Never raises for storage reasons —
    the salesperson always receives a usable number so the IO can proceed."""
    # Postgres first: it's the only option that's genuinely atomic across
    # workers. Cloudinary remains the fallback so a Hub without a database
    # still issues numbers.
    seq = _postgres_order_number()
    if seq:
        return seq, "postgres sequence", ""

    with _ORDER_LOCK:
        if _cloudinary_is_configured():
            # Determine the current value from Cloudinary, tolerating a missing asset.
            try:
                current = _read_cloudinary_order_counter()
            except Exception as exc:
                if _is_not_found(exc):
                    current = ORDER_COUNTER_BASE  # first ever allocation -> 10200
                else:
                    logger.warning("Cloudinary counter read failed, using temporary storage: %s", exc)
                    current = None  # signal read failure

            if current is not None:
                # Keep the temp mirror in step so a later Cloudinary outage stays continuous.
                current = max(current, _read_temp_counter())
                next_number = current + 1
                try:
                    _write_cloudinary_order_counter(next_number)
                    _write_temp_counter(next_number)
                    return str(next_number), "cloudinary", ""
                except Exception as exc:
                    logger.warning("Cloudinary counter write failed, using temporary storage: %s", exc)
                    _write_temp_counter(next_number)
                    return (str(next_number), "temporary",
                            "The order number was issued but could not be saved to Cloudinary, "
                            "so it is being tracked in temporary storage. Verify it is unique before finalizing.")

        # No Cloudinary (or read failed): use the temporary counter.
        next_number = _read_temp_counter() + 1
        _write_temp_counter(next_number)
        warning = ("The order number is using temporary storage and may reset after a "
                   "server restart. Confirm it is unique before finalizing the IO.")
        return str(next_number), "temporary", warning

@app.get("/api/spec-in")
def api_spec_in():
    """Load a campaign spec to start from — a proposal, a client, or a renewal.

    This is what makes the two tools one workflow: the IO doesn't re-ask what
    the proposal already established, it loads it and asks only what's still
    unconfirmed.
    """
    from flask import request as _rq
    pid = (_rq.args.get("spec") or "").strip()
    client = (_rq.args.get("client") or "").strip()
    mode = (_rq.args.get("mode") or "").strip()
    try:
        from hub.campaign_spec import (CampaignSpec, from_client, from_last_io,
                                       from_quote, to_io_payload)
        if pid:
            # The live builder's quotes table FIRST. That is where every
            # proposal written since the consolidation lives; the module
            # below is the retired tool's read-only archive. Asking only the
            # archive is how this shipped looking healthy while finding
            # nothing for any recent proposal -- the same defect the landing
            # maker had, named in CLAUDE.md.
            spec = from_quote(pid)
            if spec is not None:
                return jsonify(to_io_payload(spec))
            from modules.proposal_builder import store as pstore
            rec = pstore.get_proposal(pid) or {}
            spec_d = rec.get("spec")
            if not spec_d:
                return jsonify({"error": "No proposal with that id, in either "
                                         "the current builder or the archive."}), 404
            return jsonify(to_io_payload(CampaignSpec.from_dict(spec_d)))
        if mode == "renewal" and client:
            spec = from_last_io(client)
            if not spec:
                return jsonify({"error": "No previous IO for that client."}), 404
            return jsonify(to_io_payload(spec))
        if client:
            return jsonify(to_io_payload(from_client(client)))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Couldn't load that ({type(exc).__name__})."}), 200
    return jsonify({"fields": {}, "products": [], "ask": []})


@app.get("/api/clients")
def api_clients():
    """Clients from the Hub's registry, for the picker.

    This is what the iframe could never do: the standalone app had no idea
    who our clients were, so every IO started by typing a name that may or
    may not match what Knack holds.
    """
    q = (request.args.get("q") or "").strip()
    try:
        from hub import clients_registry
        rows = (clients_registry.search_clients(q, limit=20) if q
                else clients_registry.all_clients()[:20])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"clients": [], "error": f"{type(exc).__name__}"}), 200
    return jsonify({"clients": [{
        "name": c.get("name", ""), "domain": c.get("domain", ""),
        "products": c.get("product_count", 0),
    } for c in rows if c.get("name")]})


@app.get("/api/proposals")
def api_proposals():
    """Every proposal we hold for one client, for the start screen.

    Both kinds, kept apart: a quote built in the Proposal Builder has a
    number and a status this Hub owns, an uploaded proposal is a file with a
    date on it. Flattening them means inventing values for half the columns.

    Client first, because "which proposal?" is only answerable once you know
    whose, and a global list gets longer every week.
    """
    client = (request.args.get("client") or "").strip()
    if not client:
        return jsonify({"saved": [], "uploaded": [], "count": 0,
                        "note": "Pick a client first."})
    try:
        from hub.proposals import proposals_for
        return jsonify(proposals_for(client))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"saved": [], "uploaded": [], "count": 0,
                        "note": f"Couldn't read proposals ({type(exc).__name__})."}), 200


@app.get("/api/client-context")
def api_client_context():
    """Everything the Hub knows about a client, to prefill the form."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"fields": {}})
    try:
        from hub.client_context import context
        return jsonify(context(name))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"fields": {}, "error": f"{type(exc).__name__}"}), 200


@app.post("/api/client-upload-link")
def api_client_upload_link():
    """The link this client uploads their own creative through.

    An IO that says "creative is being supplied" is exactly the moment
    somebody needs one, and until now the answer was to open Client Image
    Uploads in another tab, find or add the client, and copy a link out of a
    row — so it got made by whoever remembered the tool existed, and the
    assets arrived by email instead.

    A POST because it can create a gallery. The matching is the picker's own
    and is deliberately strict: exactly one gallery or none, never a
    substring, because a link that collects one client's photographs into
    another client's gallery is worse than having no link at all.
    """
    body = request.get_json(silent=True) or {}
    try:
        from modules.image_picker import provisioning
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"Client Image Uploads is unavailable: {exc}"}), 200
    out = provisioning.link_for(
        str(body.get("client") or body.get("name") or ""),
        str(body.get("url") or ""),
        create=bool(body.get("create")),
        # host_url, not url_root: this app is mounted at /tools/io, so
        # url_root carries that mount and the picker path would be pasted
        # onto the end of it.
        base=request.host_url,
    )
    return jsonify(out)


@app.post("/api/next-order-number")
def next_order_number():
    try:
        order_number, storage, warning = _next_order_number()
        return jsonify({
            "ok": True,
            "order_number": order_number,
            "storage": storage,
            "warning": warning,
        })
    except Exception as exc:
        # Last-resort safety net: even on an unexpected error, hand back a usable
        # timestamp-based number so the salesperson is never fully blocked.
        logger.exception("Order number allocation failed")
        fallback = str(ORDER_COUNTER_BASE + 1 + int(time.time()) % 100000)
        return jsonify({
            "ok": True,
            "order_number": fallback,
            "storage": "fallback",
            "warning": "Order number storage is unavailable; a temporary number was generated. "
                       "Confirm it is unique before finalizing the IO.",
        })

def _safe_filename(value):
    cleaned = ''.join(ch if ch.isalnum() or ch in (' ', '-', '_') else ' ' for ch in str(value or '')).strip()
    return ' '.join(cleaned.split()) or 'Client'


def _fetch_image_bytes(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return BytesIO(r.content)
    except Exception:
        return None


def _p(text, style):
    return Paragraph(xml_escape(str(text or '')).replace('\n', '<br/>'), style)


def _upload_link_for(data):
    """The client's creative upload link, for a document or the webhook.

    Reads only — `create=False`. Building a PDF must never create a gallery:
    a document is generated repeatedly, often twice in a row for the client
    and internal versions, and a side effect that fires on each of those is
    not one anybody asked for. The wizard creates the gallery explicitly and
    puts the link in `clientUploadUrl`; this is the fallback so an IO for a
    client who already has a gallery carries the link whether or not somebody
    pressed the button.
    """
    given = str((data or {}).get('clientUploadUrl') or '').strip()
    if given:
        return given
    try:
        from modules.image_picker import provisioning
        out = provisioning.link_for(str((data or {}).get('client') or ''),
                                    str((data or {}).get('url') or ''),
                                    create=False)
        return out.get('share_url') or ''
    except Exception:  # noqa: BLE001
        return ''      # a missing link is a missing row, never a broken PDF


def _build_requirements_pdf(data, doc_type):
    client = _safe_filename(data.get('client'))
    order_number = _safe_filename(data.get('orderNumber') or 'No Order')
    title = f'S1M Internal - {order_number} - {client}' if doc_type == 'internal' else f'S1M - {order_number} - {client}'
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.5*inch, bottomMargin=0.5*inch, title=title)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='S1Title', parent=styles['Title'], textColor=colors.HexColor('#14284b'), fontSize=20, leading=24, spaceAfter=12))
    styles.add(ParagraphStyle(name='S1H2', parent=styles['Heading2'], textColor=colors.HexColor('#14284b'), fontSize=13, leading=16, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name='S1Body', parent=styles['BodyText'], fontSize=9, leading=12, spaceAfter=5))
    styles.add(ParagraphStyle(name='S1Small', parent=styles['BodyText'], fontSize=7.5, leading=9.5, textColor=colors.HexColor('#53657a')))
    story=[Paragraph(xml_escape(title), styles['S1Title'])]
    story.append(_p('Campaign and Product Requirements', styles['S1Body']))
    meta=[
        ['Order Number', data.get('orderNumber','')],
        ['Smart 1 Contact', f"{data.get('salesContact','')} - {data.get('salesEmail','')}"],
        ['Business Website', data.get('url','')],
        ['Campaign Dates', f"{data.get('start','')} to {data.get('end','')}" if data.get('sameDates') else 'Dates vary by product'],
        ['Creative', data.get('creativeSource','To be confirmed')],
        ['Monthly Spend', data.get('monthlySpendFormatted','')],
        ['Total Campaign Budget', data.get('totalCampaignBudgetFormatted','')],
    ]
    # The link the client sends their own creative through. On BOTH documents:
    # the customer version so the client can upload without anybody emailing
    # them a link, and the internal one so whoever chases the assets has the
    # same address rather than making a second gallery.
    _upload_url = _upload_link_for(data)
    if _upload_url:
        meta.append(['Send Us Your Creative',
                     Paragraph(f'<link href="{xml_escape(_upload_url)}">'
                               f'{xml_escape(_upload_url)}</link>', styles['S1Body'])])

    # Geography was missing from this table entirely -- the IO said what was
    # being bought and never where it was running.
    from hub import target_areas as _ta
    areas = _ta.normalize(data.get('targetAreas')) or _ta.from_legacy(data)
    if areas:
        meta.append(['Target Area' if len(areas) == 1 else f'Target Areas ({len(areas)})',
                     _p('\n'.join(_ta.names(areas)), styles['S1Body'])])
    elif data.get('geo'):
        meta.append(['Target Area', _p(data.get('geo'), styles['S1Body'])])
    t=Table(meta, colWidths=[1.45*inch, 5.55*inch])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#eef3f8')),('TEXTCOLOR',(0,0),(0,-1),colors.HexColor('#14284b')),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#d5dee9')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    story += [t, Spacer(1,10)]

    if areas:
        story.append(Paragraph('Target Areas', styles['S1H2']))
        area_rows = [['Target area', 'Type', 'ZIP Codes / notes']]
        for area in areas:
            zips = _ta.zip_list(area.get('zips'))
            detail = ', '.join(zips) if zips else (area.get('notes') or '')
            if not detail:
                # Said plainly rather than left blank: a radius area with no
                # ZIP list cannot be trafficked, and a blank cell reads as
                # "nothing to do here".
                detail = ('ZIP list to be added before trafficking'
                          if area['type'] == _ta.RADIUS else '—')
            area_rows.append([_p(_ta.label(area), styles['S1Small']),
                              _p(area['type'], styles['S1Small']),
                              _p(detail, styles['S1Small'])])
        at = Table(area_rows, colWidths=[2.4*inch, 1.5*inch, 3.1*inch], repeatRows=1)
        at.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#14284b')),
                                ('TEXTCOLOR',(0,0),(-1,0),colors.white),
                                ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                                ('FONTSIZE',(0,0),(-1,-1),7.5),
                                ('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#d5dee9')),
                                ('VALIGN',(0,0),(-1,-1),'TOP'),
                                ('TOPPADDING',(0,0),(-1,-1),4),
                                ('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        story += [at, Spacer(1,10)]

    # Brandfetch section near beginning
    b=data.get('brandfetch') or {}
    if any([b.get('name'), b.get('description'), b.get('logo'), b.get('colors'), b.get('links')]):
        story.append(Paragraph('Brand Information', styles['S1H2']))
        brand_rows=[]
        logo_flow=''
        logo_url=b.get('logo')
        logo_bytes=_fetch_image_bytes(logo_url)
        if logo_bytes:
            try:
                img=RLImage(logo_bytes, width=1.2*inch, height=0.8*inch, kind='proportional')
                logo_flow=[img, Paragraph(f'<link href="{xml_escape(logo_url)}">Open full logo</link>', styles['S1Small'])]
            except Exception:
                logo_flow=_p(b.get('logo'), styles['S1Small'])
        elif b.get('logo'):
            logo_flow=_p(b.get('logo'), styles['S1Small'])
        if logo_flow: brand_rows.append(['Logo', logo_flow])
        if b.get('name'): brand_rows.append(['Brand Name', _p(b.get('name'), styles['S1Body'])])
        if b.get('domain'): brand_rows.append(['Domain', _p(b.get('domain'), styles['S1Body'])])
        if b.get('description'): brand_rows.append(['Description', _p(b.get('description'), styles['S1Body'])])
        cols=[]
        for c in b.get('colors') or []:
            try:
                sw=Table([['']], colWidths=[0.18*inch], rowHeights=[0.18*inch]); sw.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),colors.HexColor(c)),('BOX',(0,0),(0,0),0.5,colors.grey)]))
                cols.append([sw, _p(c, styles['S1Small'])])
            except Exception:
                cols.append([_p(c, styles['S1Small'])])
        if cols:
            color_table=Table(cols, colWidths=[0.6*inch]*len(cols))
            color_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ALIGN',(0,0),(-1,-1),'CENTER')]))
            brand_rows.append(['Colors', color_table])
        if b.get('fonts'): brand_rows.append(['Fonts', _p(', '.join(b.get('fonts') or []), styles['S1Body'])])
        links=[]
        for link in b.get('links') or []:
            if isinstance(link, dict):
                url=link.get('url',''); name=link.get('name') or url
            else: url=str(link); name=url
            if url: links.append(f'<link href="{xml_escape(url)}">{xml_escape(name)}</link>')
        if links: brand_rows.append(['Brand Links', Paragraph('<br/>'.join(links), styles['S1Small'])])
        bt=Table(brand_rows, colWidths=[1.1*inch,5.9*inch])
        bt.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#d5dee9')),('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f3f6f9')),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story += [bt, Spacer(1,10)]

    # Uploaded creative section with thumbnails
    assets=data.get('creativeAssets') or []
    if assets or _upload_url:
        story.append(Paragraph('Creative Assets', styles['S1H2']))
    if _upload_url:
        story.append(Paragraph(
            'Anything still to come can be uploaded here, and lands straight in '
            f'this client&#39;s gallery: <link href="{xml_escape(_upload_url)}">'
            f'{xml_escape(_upload_url)}</link>', styles['S1Small']))
        story.append(Spacer(1, 6))
    if assets:
        rows=[['Preview','Product','File / Status','Evergreen','Asset Link']]
        for a in assets:
            preview='-'
            url=a.get('url','')
            rtype=(a.get('resourceType') or '').lower()
            fname=(a.get('fileName') or '').lower()
            if url and (rtype=='image' or fname.endswith(('.jpg','.jpeg','.png','.gif','.webp'))):
                ib=_fetch_image_bytes(url)
                if ib:
                    try: preview=RLImage(ib,width=0.65*inch,height=0.5*inch,kind='proportional')
                    except Exception: preview='Image'
            link=Paragraph(f'<link href="{xml_escape(url)}">Open asset</link>' if url else '-', styles['S1Small'])
            product=a.get('productLabel') or a.get('product') or ''
            rows.append([preview,_p(product,styles['S1Small']),_p(f"{a.get('fileName','')}\n{a.get('status','')}",styles['S1Small']),_p('Yes' if a.get('evergreen') else 'No',styles['S1Small']),link])
        at=Table(rows,colWidths=[0.65*inch,1.45*inch,2.25*inch,0.65*inch,1.8*inch],repeatRows=1)
        at.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#14284b')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#d5dee9')),('FONTSIZE',(0,0),(-1,-1),7.5),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        story += [at, Spacer(1,10)]


    if doc_type == 'internal':
        landing_reviews = data.get('landingPageReviews') or []
        if landing_reviews:
            story.append(Paragraph('Landing Page Review — Internal Needs', styles['S1H2']))
            for item in landing_reviews:
                story.append(_p(f"{item.get('product','Landing page')}: {item.get('url','')}", styles['S1Small']))
                story.append(_p(item.get('review',''), styles['S1Body']))

        warnings = data.get('internalWarnings') or []
        if warnings:
            story.append(Paragraph('Internal Warnings', styles['S1H2']))
            for warning in warnings:
                story.append(_p('⚠ ' + str(warning), styles['S1Body']))


    guardrails = data.get('guardrailWarnings') or []
    if guardrails and doc_type == 'internal':
        story.append(Paragraph('Campaign Guardrail Warnings', styles['S1H2']))
        for warning in guardrails:
            story.append(_p('⚠ ' + str(warning.get('message') if isinstance(warning, dict) else warning), styles['S1Body']))

    tracking = data.get('trackingPlan') or {}
    if tracking:
        story.append(Paragraph('Tracking Plan', styles['S1H2']))
        tracking_rows = [['Primary conversion', tracking.get('primaryConversion','')],
                         ['Secondary conversions', ', '.join(tracking.get('secondaryConversions') or [])],
                         ['GA4 installed', tracking.get('ga4','')],
                         ['Google Tag Manager installed', tracking.get('gtm','')],
                         ['Call tracking required', tracking.get('callTracking','')],
                         ['Thank-you page available', tracking.get('thankYouPage','')],
                         ['Offline conversion import', tracking.get('offlineImport','')],
                         ['Tracking verifier', tracking.get('verifier','')]]
        tt=Table(tracking_rows,colWidths=[1.8*inch,4.9*inch])
        tt.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#d5dee9')),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('VALIGN',(0,0),(-1,-1),'TOP'),('FONTSIZE',(0,0),(-1,-1),8)]))
        story.append(tt)
        story.append(Spacer(1,10))

    mix = data.get('mediaMixRecommendation') or {}
    if mix and doc_type == 'internal':
        story.append(Paragraph('AI Media-Mix Recommendation', styles['S1H2']))
        story.append(_p(mix.get('summary',''), styles['S1Body']))
        story.append(_p('Primary product: ' + str(mix.get('primary_product','')), styles['S1Body']))
        story.append(_p('Supporting products: ' + ', '.join(mix.get('supporting_products') or []), styles['S1Body']))
        story.append(_p('Suggested test budget: ' + str(mix.get('suggested_test_budget','')), styles['S1Body']))
        story.append(_p('Minimum run length: ' + str(mix.get('minimum_run_length','')), styles['S1Body']))

    if doc_type == 'client':
        story.append(Paragraph('What We Need From You', styles['S1H2']))
        for line in data.get('customerRequirements') or []:
            story.append(_p('• '+line, styles['S1Body']))
    else:
        story.append(Paragraph('Internal Product Requirements', styles['S1H2']))
        for section in data.get('internalRequirements') or []:
            story.append(Paragraph(xml_escape(section.get('title','Product')), styles['S1H2']))
            for item in section.get('items') or []:
                story.append(_p('• '+item, styles['S1Body']))
    doc.build(story)
    return buf.getvalue(), title


def _store_requirements_pdf(data, doc_type):
    """Build the PDF and upload it to Cloudinary. Returns (result, title).

    When SECURE_INTERNAL_PDF is enabled, the *internal* PDF (which contains fees,
    margins, and strategy) is uploaded as an authenticated asset and the returned
    secure_url is a signed, delivery URL — so a guessed public URL can't leak
    pricing. Requires PDF delivery enabled on the Cloudinary account. Default off."""
    pdf_bytes, title = _build_requirements_pdf(data, doc_type)
    client = _safe_filename(data.get('client'))
    start = _safe_filename(data.get('start') or 'no start date')
    folder = f"smart1_campaigns/{client}/{start}/documents"
    secure_internal = os.getenv("SECURE_INTERNAL_PDF", "").strip().lower() in ("1", "true", "yes", "on")
    delivery_type = 'authenticated' if (secure_internal and doc_type == 'internal') else 'upload'
    # resource_type='raw', not 'image'. A PDF uploaded as an image type only
    # DELIVERS if "PDF and ZIP files delivery" is enabled on the Cloudinary
    # account — it is off by default, and a link that 403s for the client is
    # indistinguishable from a broken tool. This was the documented open
    # caveat, and the same mistake caused 403s on the restaurant and stadium
    # report links elsewhere in the suite.
    #
    # Nothing here transforms the PDF (no thumbnails, no page extraction), so
    # raw costs nothing and always delivers.
    result = cloudinary.uploader.upload(
        BytesIO(pdf_bytes),
        resource_type='raw',
        type=delivery_type,
        public_id=title,
        folder=folder,
        overwrite=True,
        unique_filename=False,
        tags=[client, start, 'smart1_requirements_pdf', doc_type],
    )
    # One Cloudinary operation, for the credit estimate on /diagnostics.
    try:
        from hub import quotas as _q
        _q.record_asset(module="io_builder", nbytes=len(pdf_bytes),
                        detail=result.get("public_id", ""))
    except Exception:                         # noqa: BLE001
        pass
    if delivery_type == 'authenticated':
        # Return a signed delivery URL so authorized systems (e.g. the GHL workflow) can still fetch it.
        try:
            # Must match the resource_type it was uploaded with, or the
            # signature is valid for a path that holds nothing.
            signed_url, _opts = cloudinary.utils.cloudinary_url(
                result.get('public_id'), resource_type='raw',
                type='authenticated', sign_url=True, secure=True,
            )
            if signed_url:
                result['secure_url'] = signed_url
        except Exception:
            logger.warning("Unable to sign authenticated internal PDF URL; returning default.")
    return result, title

@app.post('/api/generate-requirements-pdf')
def generate_requirements_pdf():
    cfg = cloudinary.config()
    if not cfg.cloud_name or not cfg.api_key or not cfg.api_secret:
        return jsonify({'error': 'Cloudinary is not configured'}), 503
    data = request.get_json(force=True) or {}
    doc_type = str(data.get('documentType') or 'client').lower()
    if doc_type not in ('client','internal'):
        return jsonify({'error':'documentType must be client or internal'}), 400
    try:
        result, title = _store_requirements_pdf(data, doc_type)
        return jsonify({'url': result.get('secure_url'), 'public_id': result.get('public_id'), 'filename': title + '.pdf'})
    except Exception as exc:
        logger.exception('PDF generation failed')
        return jsonify({'error':'PDF generation failed','detail':str(exc)}), 500


def _generate_named_pdf(doc_type):
    """Shared handler for the client/internal PDF routes used by Submit Finished IO.
    Returns the ok/url shape the front end expects."""
    cfg = cloudinary.config()
    if not cfg.cloud_name or not cfg.api_key or not cfg.api_secret:
        return jsonify({'ok': False, 'error': 'Cloudinary is not configured'}), 503
    data = request.get_json(force=True) or {}
    try:
        result, title = _store_requirements_pdf(data, doc_type)
        url = result.get('secure_url')
        return jsonify({
            'ok': True,
            'url': url,
            'secure_url': url,
            'public_id': result.get('public_id'),
            'filename': title + '.pdf',
        })
    except Exception as exc:
        logger.exception('%s PDF generation failed', doc_type)
        return jsonify({'ok': False, 'error': f'{doc_type} PDF generation failed', 'detail': str(exc)}), 500

@app.post('/api/download-requirements-pdf')
def download_requirements_pdf():
    """Return the PDF bytes directly as a download. Does NOT depend on Cloudinary
    (Cloudinary PDF/raw delivery is disabled by default on many accounts), so the
    Customer/Internal PDF buttons work regardless of Cloudinary delivery settings."""
    data = request.get_json(force=True) or {}
    doc_type = str(data.get('documentType') or 'client').lower()
    if doc_type not in ('client', 'internal'):
        return jsonify({'error': 'documentType must be client or internal'}), 400
    try:
        pdf_bytes, title = _build_requirements_pdf(data, doc_type)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=(title + '.pdf'),
        )
    except Exception as exc:
        logger.exception('PDF download failed')
        return jsonify({'error': 'PDF generation failed', 'detail': str(exc)}), 500

@app.post('/api/generate-client-pdf')
def generate_client_pdf():
    return _generate_named_pdf('client')

@app.post('/api/generate-internal-pdf')
def generate_internal_pdf():
    return _generate_named_pdf('internal')


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    logger.exception('Unhandled application error')
    return jsonify({'error': 'Internal server error', 'type': type(exc).__name__, 'message': str(exc)}), 500


@app.post("/api/submit-io")
def submit_io():
    """Send a completed IO record to Smart 1 Suite / GoHighLevel."""
    # Record it against the client so the IO shows in "Work for this client".
    try:
        from hub import audit
        _body = request.get_json(silent=True) or {}
        audit.log("io_builder", "io_submitted",
                  client=str(_body.get("client") or _body.get("client_name") or ""),
                  order=str(_body.get("order_number") or ""))
    except Exception:  # noqa: BLE001
        pass

    # An IO for a business nobody has a record of leaves them invisible on
    # Client 360 -- the page reads Knack products and website records, and a
    # brand-new client has neither until the campaign is set up. So the order
    # registers them, and ONLY when they are genuinely new: an IO for a client
    # who already resolves writes nothing, because a second row under a name
    # that already exists is how one company becomes two on every report keyed
    # on a client. Hub-side overlay, never a write to Knack -- the day the real
    # record appears it wins. See hub/io_clients.py.
    try:
        from hub import io_clients
        _b = request.get_json(silent=True) or {}
        io_clients.register_from_io(_b)
    except Exception:  # noqa: BLE001
        pass           # an IO must never fail to submit over its own bookkeeping

    webhook_url = os.environ.get("GHL_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return jsonify({"ok": False, "error": "GHL_WEBHOOK_URL is not configured on the server."}), 500

    data = request.get_json(silent=True) or {}
    client_pdf_url = str(data.get("client_pdf_url") or "").strip()
    internal_pdf_url = str(data.get("internal_pdf_url") or "").strip()

    if not client_pdf_url or not internal_pdf_url:
        return jsonify({
            "ok": False,
            "error": "Both the client PDF URL and internal PDF URL are required before the IO can be submitted."
        }), 400

    # Compute opportunity-friendly summary fields so GoHighLevel can map an
    # Opportunity Name and Lead Value without digging into nested campaign_data.
    def _num(value):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
    items = data.get("items", []) or []
    monthly_budget = round(sum(_num(i.get("budget")) for i in items if isinstance(i, dict)), 2)
    total_campaign_budget = round(sum(_num(i.get("campaignBudget")) for i in items if isinstance(i, dict)), 2)
    order_number = data.get("orderNumber") or ""
    opportunity_name = " - ".join([str(p) for p in [
        data.get("client"), data.get("ioType"),
        (f"Order {order_number}" if order_number else None)
    ] if p])

    # Keep the full record, while also exposing commonly mapped fields at the top level.
    payload = {
        "event": "smart1_io_completed",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "order_number": order_number,
        "opportunity_name": opportunity_name,
        "lead_value": total_campaign_budget,
        "monthly_budget": monthly_budget,
        "total_campaign_budget": total_campaign_budget,
        "io_type": data.get("ioType"),
        "client_name": data.get("client"),
        "client_website": data.get("url"),
        "client_contact_name": data.get("clientContactName"),
        "client_contact_email": data.get("clientContactEmail"),
        "client_contact_phone": data.get("clientContactPhone"),
        "sales_contact": data.get("salesContact"),
        "sales_contact_email": data.get("salesEmail"),
        "media_partner": data.get("partner"),
        "campaign_start_date": data.get("start"),
        "campaign_end_date": data.get("end"),
        "campaign_goals": data.get("objectives", []),
        "kpis": data.get("kpis", []),
        # geographic_target stays a single string -- a GoHighLevel workflow
        # already reads it -- and now holds every area rather than the first.
        # target_areas is the structured list for anything that can use it.
        "geographic_target": _payload_geo(data),
        "target_areas": _payload_areas(data),
        "audiences": data.get("audiences", []),
        "income_targets": data.get("incomes", data.get("income", [])),
        "dayparting": data.get("dayparting"),
        "creative_source": data.get("creativeSource"),
        # The address the client uploads their own creative through, so a
        # Suite automation can put it in a chase email or a task rather than
        # somebody hunting for it in another tool.
        "client_upload_url": _upload_link_for(data),
        "exclusions_negative_keywords": data.get("exclusions"),
        "landing_page_mode": data.get("landingPageMode"),
        "shared_landing_page": data.get("landingPage"),
        "products": data.get("items", []),
        "creative_assets": data.get("creativeUploads", []),
        "brandfetch": data.get("brandfetch", {}),
        "client_pdf_url": client_pdf_url,
        "internal_pdf_url": internal_pdf_url,
        "cloudinary_documents": data.get("documents", {}),
        "management_fee": data.get("managementFee"),
        "creative_fee": data.get("creativeFee"),
        "tracking_plan": data.get("trackingPlan", {}),
        "guardrail_warnings": data.get("guardrailWarnings", []),
        "media_mix_recommendation": data.get("mediaMixRecommendation", {}),
        "naming_conventions": data.get("naming", {}),
        "campaign_owner": data.get("campaignOwner"),
        # Which order this one supersedes, and the date that one stops. Both
        # at the top level rather than only inside campaign_data: a Suite
        # automation that has to close the old opportunity should not have to
        # dig for the number, and an end date nobody read is an order that
        # keeps billing.
        "replaces_io": data.get("replacesIo") or "",
        "replaces_io_end": data.get("replacesIoEnd") or "",
        "campaign_data": data
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=30)
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Webhook request failed: {exc}"}), 502

    if response.status_code >= 400:
        return jsonify({
            "ok": False,
            "error": "Smart 1 Suite webhook returned an error.",
            "status_code": response.status_code,
            "response": response.text[:1000]
        }), 502

    return jsonify({
        "ok": True,
        "status_code": response.status_code,
        "message": "The completed IO was sent to Smart 1 Suite.",
        "client_pdf_url": client_pdf_url,
        "internal_pdf_url": internal_pdf_url
    })


def _payload_areas(data):
    from hub import target_areas as _ta
    return _ta.normalize(data.get("targetAreas")) or _ta.from_legacy(data)


def _payload_geo(data):
    from hub import target_areas as _ta
    areas = _payload_areas(data)
    return _ta.to_legacy_geo(areas) or str(data.get("geo") or "")


@app.post('/api/zipcodes-in-radius')
@_rate_limited(_AI_RATE_MAX, _AI_RATE_WINDOW)
def zipcodes_in_radius():
    data = request.get_json(force=True) or {}
    origin = str(data.get('origin') or '').strip()
    radius = str(data.get('radius') or '').strip()
    if not origin or not radius:
        return jsonify({'error': 'Origin city/ZIP and radius are required'}), 400
    prompt = (
        f'Find the complete list of United States ZIP Codes whose geographic polygon is fully or partially touched by a {radius}-mile radius '
        f'centered on {origin}. Include a ZIP Code whenever any portion of that ZIP Code area intersects the radius, not only when its centroid is inside. '
        'Use current authoritative geographic sources where possible. Return only five-digit ZIP Codes, comma-separated, sorted ascending, with no commentary. '
        'Be exhaustive and do not intentionally omit any matching ZIP Code. If the exact boundary cannot be verified, include plausible boundary-touching ZIP Codes rather than omitting them.'
    )
    try:
        text = _openai_response(prompt, max_output_tokens=12000)
        zips = sorted(set(re.findall(r'\b\d{5}\b', text)))
        if not zips:
            return jsonify({'error': 'No ZIP Codes were returned'}), 502
        return jsonify({
            'zipcodes': ', '.join(zips),
            'count': len(zips),
            'warning': 'AI-assisted ZIP-radius results should be reviewed before trafficking because ZIP boundaries and radius intersections can change.'
        })
    except Exception as exc:
        detail = ''
        if getattr(exc, 'response', None) is not None:
            detail = (exc.response.text or '')[:500]
        return jsonify({'error': 'ZIP-radius lookup failed', 'detail': detail or str(exc)}), 502

@app.post('/api/review-landing-page')
@_rate_limited(_AI_RATE_MAX, _AI_RATE_WINDOW)
def review_landing_page():
    data = request.get_json(force=True) or {}
    url = str(data.get('url') or '').strip()
    client = str(data.get('client') or '').strip()
    product = str(data.get('product') or 'Shared campaign landing page').strip()
    objectives = data.get('objectives') or []
    if not url:
        return jsonify({'error': 'Landing-page URL is required'}), 400
    prompt = (
        f'Review this campaign landing page: {url}\n'
        f'Client: {client}\nProduct or use: {product}\nCampaign goals: {", ".join(map(str, objectives))}\n'
        'Visit the page and evaluate it as a conversion-focused landing page. Determine whether it has a clear primary call to action above the fold and throughout the page. '
        'Review message match, headline clarity, offer clarity, forms, phone calls, buttons, mobile usability, page speed signals, trust indicators, testimonials, privacy language, '
        'tracking readiness, distractions, and whether the conversion action is easy to complete. '
        'Return a concise internal trafficking note with these headings: CTA Status, Strengths, Required Fixes Before Launch, Recommended Improvements, Tracking Checks. '
        'Be specific and practical. If the page cannot be accessed, say so clearly.'
    )
    try:
        review = _openai_response(prompt, max_output_tokens=5000)
        return jsonify({'review': review, 'url': url, 'product': product})
    except Exception as exc:
        detail = ''
        if getattr(exc, 'response', None) is not None:
            detail = (exc.response.text or '')[:500]
        return jsonify({'error': 'Landing-page review failed', 'detail': detail or str(exc)}), 502



@app.post('/api/media-mix-recommendation')
@_rate_limited(_AI_RATE_MAX, _AI_RATE_WINDOW)
def media_mix_recommendation():
    data = request.get_json(force=True) or {}
    prompt = (
        "Act as a senior digital media strategist. Review the campaign intake below and recommend a practical media mix. "
        "Base the recommendation on goals, industry, geography, total and monthly budget, campaign duration, audience, available creative, "
        "landing-page quality, and the products available in the supplied rate-card list. Avoid inventing products. "
        "Return strict JSON with these keys: summary, primary_product, supporting_products, excluded_products, suggested_allocations, "
        "suggested_test_budget, minimum_run_length, rationale, warnings. "
        "suggested_allocations must be an array of objects with product, monthly_budget, percent, reason. "
        "supporting_products, excluded_products, and warnings must be arrays. Keep the advice concise and operational.\n\n"
        + json.dumps(data, ensure_ascii=False)
    )
    try:
        text = _openai_response(prompt, max_output_tokens=6000)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', cleaned, flags=re.I|re.S)
        result = json.loads(cleaned)
        return jsonify({'ok': True, 'recommendation': result})
    except json.JSONDecodeError:
        return jsonify({'ok': True, 'recommendation': {
            'summary': text if 'text' in locals() else '',
            'primary_product': '',
            'supporting_products': [],
            'excluded_products': [],
            'suggested_allocations': [],
            'suggested_test_budget': '',
            'minimum_run_length': '',
            'rationale': '',
            'warnings': ['AI returned a narrative recommendation instead of structured JSON.']
        }})
    except Exception as exc:
        detail = ''
        if getattr(exc, 'response', None) is not None:
            detail = (exc.response.text or '')[:500]
        return jsonify({'ok': False, 'error': 'Media-mix recommendation failed', 'detail': detail or str(exc)}), 502



# ---------------------------------------------------------------------------
# Unfinished insertion orders
#
# The browser keeps its own copy in localStorage and always did; this is the
# half that survives the browser. Somebody interrupted on their laptop and
# picking the IO up on a different machine had no draft at all before this,
# and -- worse -- nothing on the start screen said an unfinished one existed,
# so it was simply started again from the top. See hub/drafts.py.
#
# Nothing in here may fail the form it is insuring: every route answers 200
# with what happened in the body, so an autosave that could not write costs
# the server copy and never the IO somebody is in the middle of.
# ---------------------------------------------------------------------------

def _signed_in_as():
    """Who is using the builder, from the Hub session.

    `AuthGuard` puts the display name on the WSGI environ before Flask sees
    the request -- the only place it exists for a dispatcher-mounted module,
    which has no Hub app context and no `flask.g` of its own. Empty is a real
    answer (the module runs standalone in the tests, and behind the shared
    password in an emergency) and reads as "not recorded" on the list rather
    than as somebody's name.
    """
    try:
        return str(request.environ.get("s1hub.user") or "").strip()[:160]
    except Exception:                                   # noqa: BLE001
        return ""


def _drafts():
    from hub import drafts
    return drafts


def _draft_title(state):
    """What the row says, from whatever the rep has filled in so far.

    An IO is identified by its client and its order number, and the ordinary
    case for a draft is that neither is there yet -- so this degrades rather
    than refusing, and an untitled draft still appears on the list. A row
    somebody cannot recognize is still better than a draft nobody knows about.
    """
    if not isinstance(state, dict):
        return ""
    # The browser wraps its own state -- {state, index, currentProduct} -- so
    # the answers are one level down. Unwrapped here rather than at the route,
    # because the fallback has to work for whatever posts the draft, and a
    # title read off the wrapper is "Untitled" on every row.
    if isinstance(state.get("state"), dict):
        state = state["state"]
    client = str(state.get("client") or state.get("client_name") or "").strip()
    order = str(state.get("orderNumber") or state.get("order_number") or "").strip()
    if client and order:
        return f"{client} - IO {order}"
    return client or (f"IO {order}" if order else "")


@app.post("/api/draft")
def api_draft_save():
    body = request.get_json(silent=True) or {}
    try:
        out = _drafts().save(
            "io",
            str(body.get("id") or ""),
            owner=_signed_in_as(),
            title=str(body.get("title") or "") or _draft_title(body.get("state")),
            step=body.get("step") or 0,
            state=body.get("state") or {},
        )
    except Exception as exc:                            # noqa: BLE001
        logger.warning("IO draft save failed: %s", exc)
        return jsonify({"ok": False, "id": "", "dropped": [],
                        "error": "not saved on the server"}), 200
    return jsonify(out), 200


@app.get("/api/drafts")
def api_draft_list():
    try:
        rows = _drafts().listing("io", owner=_signed_in_as())
    except Exception as exc:                            # noqa: BLE001
        # "nobody has an unfinished IO" and "we could not look" are different
        # answers, and only the first means there is nothing to pick up.
        return jsonify({"drafts": [], "measured": False,
                        "error": f"{type(exc).__name__}"}), 200
    return jsonify({"drafts": rows, "measured": True, "error": ""}), 200


@app.get("/api/draft/<draft_id>")
def api_draft_get(draft_id):
    try:
        row = _drafts().get(draft_id)
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}"}), 200
    if not row or row.get("kind") != "io":
        return jsonify({"ok": False, "error": "No such draft."}), 404
    return jsonify({"ok": True, "draft": row}), 200


@app.delete("/api/draft/<draft_id>")
def api_draft_delete(draft_id):
    try:
        row = _drafts().get(draft_id)
        if not row or row.get("kind") != "io":
            # Deleted, never existed and belongs to the other builder all
            # answer the same thing: there is nothing here to discard.
            return jsonify({"ok": True, "deleted": False}), 200
        return jsonify({"ok": True, "deleted": _drafts().delete(draft_id)}), 200
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "deleted": False,
                        "error": f"{type(exc).__name__}"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8000')), debug=False)
