"""Live health checks for every external API the Hub depends on.

`hub/config.py` answers "is the key set?". This answers the harder and more
useful question: "does the key actually work right now?"

Those are very different failure modes. A key that is set but revoked, a token
that expired, a plan that lapsed, a Cloudinary cloud name that doesn't match —
every one of those passes a configuration check and fails in production. The
suite audits are full of exactly this shape of bug: features that looked fine
and silently degraded.

Each check is cheap and read-only. Nothing here starts an audit, generates an
image, or spends a credit — the checks are chosen specifically so that running
the diagnostics page never costs money. Where a provider has no free ping,
the check is marked `unverified` rather than guessed at.

Every check is wrapped and time-limited. A hanging provider must not hang the
page.
"""
from __future__ import annotations

import concurrent.futures
import os
import time
from dataclasses import dataclass, asdict

import requests

from hub.config import settings

TIMEOUT = 8


@dataclass
class Check:
    key: str
    label: str
    state: str          # ok | warn | error | off | unverified
    detail: str
    ms: int = 0
    required: bool = False
    fix: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _timed(fn):
    start = time.time()
    try:
        res = fn()
    except requests.Timeout:
        res = ("error", f"No response within {TIMEOUT}s.")
    except requests.RequestException as exc:
        res = ("error", f"Could not reach the API ({type(exc).__name__}).")
    except Exception as exc:                # noqa: BLE001
        res = ("error", f"{type(exc).__name__}.")
    return res, int((time.time() - start) * 1000)


def _off(key, label, env, what, required=False) -> Check:
    return Check(key, label, "off", f"Not configured — {what}", 0, required,
                 f"Set {env} in the Render dashboard.")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_openai() -> Check:
    if not settings.openai_key:
        return _off("openai", "OpenAI", "OPENAI_API_KEY",
                    "every AI feature falls back to templates.")
    def go():
        r = requests.get("https://api.openai.com/v1/models",
                         headers={"Authorization": f"Bearer {settings.openai_key}"},
                         timeout=TIMEOUT)
        if r.status_code == 401:
            return ("error", "Key rejected (401). It has been revoked or is wrong.")
        if r.status_code == 429:
            return ("warn", "Rate limited or out of quota (429). Check billing.")
        if not r.ok:
            return ("error", f"HTTP {r.status_code}.")
        ids = {m.get("id") for m in r.json().get("data", [])}
        missing = [m for m in {settings.openai_model, settings.openai_vision_model}
                   if m and m not in ids]
        if missing:
            return ("warn", f"Key works, but this account can't see: "
                            f"{', '.join(missing)}.")
        return ("ok", f"Key valid. {len(ids)} models available.")
    (state, detail), ms = _timed(go)
    return Check("openai", "OpenAI", state, detail, ms,
                 fix="Rotate the key at platform.openai.com if rejected.")


def check_cloudinary() -> Check:
    if not settings.cloudinary_ready:
        return _off("cloudinary", "Cloudinary", "CLOUDINARY_URL",
                    "uploads go to local disk, which is wiped on every redeploy.")
    def go():
        import cloudinary, cloudinary.api
        cloudinary.config(secure=True)
        usage = cloudinary.api.usage()
        cred = usage.get("credits") or {}
        used, limit = cred.get("usage"), cred.get("limit")
        if used is not None and limit:
            pct = round(100 * used / limit)
            state = "warn" if pct >= 80 else "ok"
            return (state, f"Connected. {pct}% of monthly credits used "
                           f"({used:.2f} of {limit}).")
        return ("ok", "Connected.")
    (state, detail), ms = _timed(go)
    return Check("cloudinary", "Cloudinary", state, detail, ms)


def check_brandfetch() -> Check:
    if not settings.brandfetch_key:
        return _off("brandfetch", "Brandfetch", "BRANDFETCH_API_KEY",
                    "logo and brand-colour lookup is disabled.")
    def go():
        # Their own domain — a stable, cheap lookup.
        r = requests.get("https://api.brandfetch.io/v2/brands/brandfetch.com",
                         headers={"Authorization": f"Bearer {settings.brandfetch_key}"},
                         timeout=TIMEOUT)
        if r.status_code in (401, 403):
            return ("error", f"Key rejected ({r.status_code}).")
        if r.status_code == 429:
            return ("warn", "Rate limited (429) — monthly allowance may be spent.")
        if not r.ok:
            return ("error", f"HTTP {r.status_code}.")
        return ("ok", "Key valid.")
    (state, detail), ms = _timed(go)
    return Check("brandfetch", "Brandfetch", state, detail, ms)


def check_insites() -> Check:
    if not settings.insites_key:
        return _off("insites", "Insites", "INSITES_API_KEY",
                    "Site Scans cannot start an audit.")
    # Still no free endpoint to ping — spending a credit to check whether we
    # can spend credits would be a poor trade. But this used to say "verified
    # by the next real scan" and then never look, so it read as unverified
    # forever no matter how many scans had succeeded. A completed scan is that
    # proof, already paid for, so ask the scans table instead of the API.
    def go():
        try:
            from modules.scans.app import Scan, SessionLocal
        except Exception:                               # noqa: BLE001
            return ("unverified", "Key is set. The Scans module isn't loaded "
                                  "here, so no completed audit can confirm it.")
        db = SessionLocal()
        try:
            last = (db.query(Scan)
                    .filter(Scan.status == "complete")
                    .order_by(Scan.completed_at.desc())
                    .first())
            failed = (db.query(Scan)
                      .filter(Scan.status == "error")
                      .order_by(Scan.created_at.desc())
                      .first())
        finally:
            db.close()
        if last is not None and last.completed_at:
            from hub import dates as _dates
            when = _dates.fmt(last.completed_at)
            msg = f"Key works — last audit completed {when} ({last.domain_key})."
            if (failed is not None and failed.created_at
                    and failed.created_at > last.completed_at):
                return ("warn", msg + " A later scan errored; check Site Scans.")
            return ("ok", msg)
        return ("unverified", "Key is set, but no audit has completed yet — "
                              "nothing has proved it end to end.")
    (state, detail), ms = _timed(go)
    return Check("insites", "Insites", state, detail, ms,
                 fix="Run one scan to confirm end to end.")


def check_removebg() -> Check:
    if not settings.remove_bg_key:
        return _off("removebg", "remove.bg", "REMOVE_BG_API_KEY",
                    "AI background removal is disabled (free white-background "
                    "removal still works).")
    def go():
        r = requests.get("https://api.remove.bg/v1.0/account",
                         headers={"X-Api-Key": settings.remove_bg_key},
                         timeout=TIMEOUT)
        if r.status_code in (401, 403):
            return ("error", f"Key rejected ({r.status_code}).")
        if not r.ok:
            return ("error", f"HTTP {r.status_code}.")
        cred = (r.json().get("data") or {}).get("attributes", {}).get("credits", {})
        total = cred.get("total")
        if total is not None:
            if total <= 0:
                return ("error", "No credits remaining.")
            if total < 10:
                return ("warn", f"Only {total} credits left.")
            return ("ok", f"{total} credits remaining.")
        return ("ok", "Key valid.")
    (state, detail), ms = _timed(go)
    return Check("removebg", "remove.bg", state, detail, ms)


def _simple(key, label, env, url, headers=None, params=None, what="",
            value: str = "") -> Check:
    val = (value or os.environ.get(env, "")).strip()
    if not val:
        return _off(key, label, env, what)
    def go():
        r = requests.get(url, headers=headers or {}, params=params or {},
                         timeout=TIMEOUT)
        if r.status_code in (401, 403):
            return ("error", f"Key rejected ({r.status_code}).")
        if r.status_code == 429:
            return ("warn", "Rate limited (429).")
        if not r.ok:
            return ("error", f"HTTP {r.status_code}.")
        return ("ok", "Key valid.")
    (state, detail), ms = _timed(go)
    return Check(key, label, state, detail, ms)


def check_pexels() -> Check:
    return _simple("pexels", "Pexels", "PEXELS_API", value=settings.pexels_key, url=
                   "https://api.pexels.com/v1/search",
                   headers={"Authorization": settings.pexels_key},
                   params={"query": "test", "per_page": 1},
                   what="one of three stock providers is unavailable.")


def check_pixabay() -> Check:
    return _simple("pixabay", "Pixabay", "PIXABAY_API", value=settings.pixabay_key, url=
                   "https://pixabay.com/api/",
                   params={"key": settings.pixabay_key,
                           "q": "test", "per_page": 3},
                   what="one of three stock providers is unavailable.")


def check_unsplash() -> Check:
    return _simple("unsplash", "Unsplash", "UNSPLASH_ACCESS_KEY", value=settings.unsplash_key, url=
                   "https://api.unsplash.com/search/photos",
                   headers={"Authorization":
                            f"Client-ID {settings.unsplash_key}"},
                   params={"query": "test", "per_page": 1},
                   what="one of three stock providers is unavailable.")


def check_google_fonts() -> Check:
    return _simple("google_fonts", "Google Fonts", "GOOGLE_FONTS_API", value=settings.google_fonts_key, url=
                   "https://www.googleapis.com/webfonts/v1/webfonts",
                   params={"key": settings.google_fonts_key,
                           "sort": "popularity"},
                   what="a curated font list is used instead. Optional.")


def check_ghl() -> Check:
    if not (settings.ghl_token and settings.ghl_company_id):
        return _off("ghl", "GoHighLevel (Suite)", "GHL_PRIVATE_TOKEN / GHL_COMPANY_ID",
                    "Suite Panel and the billing audits cannot run.")
    def go():
        r = requests.get("https://services.leadconnectorhq.com/locations/search",
                         headers={"Authorization": f"Bearer {settings.ghl_token}",
                                  "Version": "2021-07-28", "Accept": "application/json"},
                         params={"companyId": settings.ghl_company_id, "limit": 1},
                         timeout=TIMEOUT)
        if r.status_code in (401, 403):
            return ("error", f"Token rejected ({r.status_code}). Private "
                             f"Integration tokens expire — reissue it.")
        if not r.ok:
            return ("error", f"HTTP {r.status_code}.")
        return ("ok", "Token valid.")
    (state, detail), ms = _timed(go)
    return Check("ghl", "GoHighLevel (Suite)", state, detail, ms)


def check_ghl_app() -> Check:
    """The Marketplace app that issues per-sub-account tokens.

    Separate from check_ghl: the agency token can be perfectly healthy while
    every sub-account read (Forms, above all) still fails for want of this.
    """
    from hub import ghl_oauth
    if not ghl_oauth.configured():
        return _off("ghl_app", "Suite sub-account access",
                    "GHL_CLIENT_ID / GHL_CLIENT_SECRET",
                    "Forms and other sub-account data cannot be read.")

    def go():
        st = ghl_oauth.status()
        if not st["connected"]:
            return ("warn", "App not authorised yet — connect once from "
                            "Suite → Status → Connect.")
        try:
            ghl_oauth.agency_token()
        except Exception as exc:  # noqa: BLE001
            return ("error", f"Stored authorisation is not usable: {exc}")
        return ("ok", "Connected; sub-account tokens are minted on demand.")
    (state, detail), ms = _timed(go)
    return Check("ghl_app", "Suite sub-account access", state, detail, ms)


def check_knack() -> Check:
    if not (settings.knack_app_id and settings.knack_api_key):
        return _off("knack", "Knack", "KNACK_APP_ID / KNACK_API_KEY",
                    "the client registry has no source of clients.")
    def go():
        r = requests.get("https://api.knack.com/v1/applications/"
                         f"{settings.knack_app_id}", timeout=TIMEOUT)
        if not r.ok:
            return ("error", f"HTTP {r.status_code}.")
        return ("ok", "Application reachable.")
    (state, detail), ms = _timed(go)
    return Check("knack", "Knack", state, detail, ms)


def check_google_oauth() -> Check:
    if not (os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")):
        return _off("google_oauth", "Google sign-in",
                    "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET",
                    "domain login is unavailable; the shared password is the "
                    "only way in.")
    def go():
        r = requests.get("https://accounts.google.com/.well-known/openid-configuration",
                         timeout=TIMEOUT)
        return ("ok", "Configured. Confirm /auth/google/callback is an "
                      "authorised redirect URI.") if r.ok else \
               ("warn", "Configured, but Google's discovery endpoint didn't respond.")
    (state, detail), ms = _timed(go)
    return Check("google_oauth", "Google sign-in", state, detail, ms)


def check_database() -> Check:
    if not settings.database_url:
        return Check("database", "Database", "warn",
                     "No DATABASE_URL — running on local SQLite, which is lost "
                     "on every redeploy.", 0,
                     fix="Attach a Render Postgres instance and set DATABASE_URL.")
    def go():
        from sqlalchemy import create_engine, text
        eng = create_engine(settings.database_url, pool_pre_ping=True,
                            connect_args={"connect_timeout": TIMEOUT})
        with eng.connect() as cx:
            cx.execute(text("SELECT 1"))
        return ("ok", "Connected.")
    (state, detail), ms = _timed(go)
    return Check("database", "Database", state, detail, ms, required=True)


def check_json_backup() -> Check:
    """Is the JSON on the persistent disk mirrored anywhere backed up?

    Render backs up the managed Postgres. It does not back up the 5 GB disk at
    /var/data, and a recreated disk comes back empty — which for the files that
    are the only copy of something (SEO working files, both OAuth tokens, Fan
    Radio projects, the house client list) is unrecoverable loss that presents
    itself as an empty list rather than as an error. hub/jsonstore.py mirrors
    those writes into the database; this reports whether that is actually
    happening, because a backup nobody has looked at is a belief, not a backup.
    """
    def go():
        from hub import jsonstore
        st = jsonstore.status()
        if not st["ready"]:
            return ("error",
                    "Not mirroring — JSON on the disk is the only copy and the "
                    f"disk is not backed up. {st['error'] or 'No database.'}")
        if st.get("same_disk"):
            return ("error",
                    "The mirror is a SQLite file on the same disk it is meant "
                    "to protect, because DATABASE_URL is not set — so a "
                    "recreated disk destroys the files and the backup together. "
                    "Everything below this line still reports healthy, which is "
                    "why this is an error and not a warning.")
        if st["breaker_open"]:
            return ("warn",
                    "Mirroring is paused after repeated database errors; it "
                    f"retries within a minute. Last error: {st['error']}")
        blobs = st["blobs"]
        if blobs is None:
            return ("unverified", "Could not count what is backed up.")
        if not blobs:
            return ("warn", "Connected, but nothing has been mirrored yet. "
                            "The hourly backup_json job populates it.")
        kb = round((st["bytes"] or 0) / 1024)
        detail = f"{blobs} file{'s' if blobs != 1 else ''} mirrored ({kb} KB)"
        if st["newest"]:
            detail += f", newest {st['newest']}Z"
        if st["declared_caches"]:
            detail += (f". {len(st['declared_caches'])} deliberately excluded "
                       f"as rebuildable.")
        return ("ok", detail + ".")
    (state, detail), ms = _timed(go)
    return Check("json_backup", "JSON backup", state, detail, ms, required=True,
                 fix="Set DATABASE_URL so hub/jsonstore.py can mirror the disk.")


def check_public_base_url() -> Check:
    if not settings.public_base_url:
        return Check("public_base_url", "Public base URL", "error",
                     "Blank. Insites cannot post scan completions back, so every "
                     "scan hangs on 'running' until the poller times out.", 0,
                     required=True,
                     fix="Set PUBLIC_BASE_URL to this service's URL.")
    return Check("public_base_url", "Public base URL", "ok",
                 settings.public_base_url)


CHECKS = [
    check_database, check_json_backup, check_public_base_url,
    check_openai, check_cloudinary,
    check_brandfetch, check_insites, check_removebg, check_pexels,
    check_pixabay, check_unsplash, check_google_fonts, check_ghl,
    check_ghl_app, check_knack, check_google_oauth,
]


def run_all(parallel: bool = True) -> dict:
    """Run every check. Parallel, because fifteen sequential HTTP calls is a
    page nobody waits for."""
    started = time.time()
    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda f: f(), CHECKS))
    else:
        results = [f() for f in CHECKS]

    rows = [r.as_dict() for r in results]
    by_state = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1

    broken = [r for r in rows if r["state"] == "error"]
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "took_ms": int((time.time() - started) * 1000),
        "checks": rows,
        "counts": by_state,
        "healthy": not broken,
        "broken": [r["label"] for r in broken],
        "degraded": [r["label"] for r in rows if r["state"] in ("warn", "off")],
    }
