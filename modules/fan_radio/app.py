"""Smart 1 Hub — Fan Radio.

Football-themed :15 and :30 radio spots for local advertisers, in three
dayparts — Pre-Game Prep, Game Day, Post-Game — plus a customer-facing page
where the client listens, approves, or asks for changes.

Three things make this more than a script generator:

* **Nobody's trademark leaves the building.** Every script is scanned
  against league, club, bowl, school and fan-slogan marks before it can be
  delivered, and the project's own team context is added to that block list
  rather than into the copy. A blocked hit fails QC; the writer is re-asked
  once, naming exactly what it broke.
* **Post-game spots are result-neutral by default.** A spot booked for
  after the final whistle is written and voiced days earlier. It cannot know
  the score, so copy that quietly assumes one is flagged — with optional
  "if it went well" / "if it didn't" alternates for the station to swap in.
* **The client approves in one place.** One share link per project, random
  token, no login: scripts, audio, approve or comment per spot. Feedback is
  written to disk before anything else happens, and lands back in the
  builder against the spot it belongs to.

Mounted at ``/tools/fan-radio``. Everything is behind the Hub login except
``/r/…``, ``/api/public/…`` and ``/audio/…``, which the customer uses.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import requests
from flask import (Flask, jsonify, render_template, request, send_file)

from . import ai, catalog, phrases, speech, store, voices

try:
    from hub import audit as hub_audit
except Exception:                                     # noqa: BLE001
    hub_audit = None

try:
    from hub import clients_registry
except Exception:                                     # noqa: BLE001
    clients_registry = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["JSON_SORT_KEYS"] = False

MAX_SPOTS = 12
_rate_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


# ------------------------------------------------------------------ utils
def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def _log(event: str, **extra):
    if hub_audit is not None:
        try:
            hub_audit.log("fan_radio", event, actor=actor_name(), **extra)
        except Exception:                             # noqa: BLE001
            pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                 # noqa: BLE001
        return ""


def client_ip() -> str:
    """ProxyFix(x_for=1) is applied at the WSGI root, so remote_addr is
    already the real client. Reading X-Forwarded-For's *first* hop here —
    the mistake the Suite audit found in three apps — would let anyone
    spoof past the limiter with one header."""
    return request.remote_addr or "unknown"


def rate_limited(bucket: str, limit: int, window: int = 60) -> bool:
    key = f"{bucket}:{client_ip()}"
    cutoff = time.time() - window
    with _rate_lock:
        hits = [t for t in _hits.get(key, []) if t > cutoff]
        if len(hits) >= limit:
            _hits[key] = hits
            return True
        hits.append(time.time())
        _hits[key] = hits
        if len(_hits) > 5000:                          # cheap sweep
            for k in list(_hits):
                if not [t for t in _hits[k] if t > cutoff]:
                    _hits.pop(k, None)
    return False


def fail(message: str, code: int = 400):
    """Customer-safe error. No provider bodies, no tracebacks — the audit
    found an API key prefix reaching a public lead page that way."""
    return jsonify({"ok": False, "error": message}), code


def banned_terms(project: dict) -> list[str]:
    extra = phrases.extra_blocked(project.get("team_context") or "")
    return sorted(set(extra + [str(b).lower() for b in (project.get("banned") or []) if b]))


def read_site(url: str) -> tuple[str, str]:
    """Fetch one page of text. Returns (text, note)."""
    url = str(url or "").strip()
    if not url:
        return "", "No website given — the brief is from your notes only."
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    if (not host or "." not in host or host in ("localhost",)
            or re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.)", host)
            or host.endswith(".internal") or host.endswith(".local")):
        return "", "That address can't be read from here."
    try:
        res = requests.get(url, timeout=20, allow_redirects=True,
                           headers={"User-Agent": "Smart1Hub-FanRadio/1.0"})
        if res.status_code != 200:
            return "", f"The site returned {res.status_code}; brief written from your notes."
        html = res.text[:400_000]
    except requests.RequestException:
        return "", "Couldn't reach the site; brief written from your notes."
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s{2,}", " ", soup.get_text(" ")).strip()
    except Exception:                                 # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:12000], ""


def decorate(project: dict, spot: dict) -> dict:
    """Recompute the derived fields a spot is judged on."""
    spot["grade"] = catalog.grade(spot.get("script") or "",
                                  spot.get("seconds") or 30)
    spot["scan"] = phrases.scan(spot.get("script") or "",
                                banned_terms(project),
                                spot.get("daypart") or "",
                                spot.get("outcome") or "neutral")
    spoken = speech.normalize_for_speech(spot.get("script") or "",
                                         project.get("pronunciation"))
    spot["spoken"] = spoken["spoken"]
    spot["speech_changes"] = spoken["changes"]
    return spot


def public_view(project: dict) -> dict:
    """Exactly what the customer page is allowed to see. Built by picking
    fields out, not by deleting them — a denylist forgets a field the day
    somebody adds one."""
    share = project.get("share") or {}
    spots = []
    for spot in project.get("spots") or []:
        if not spot.get("script"):
            continue
        if spot.get("hidden"):
            continue
        dp = catalog.daypart(spot.get("daypart") or "")
        spots.append({
            "id": spot.get("id"),
            "daypart": spot.get("daypart"),
            "daypart_label": dp["label"],
            "daypart_when": dp["when"],
            "seconds": spot.get("seconds"),
            "length_label": catalog.budget(spot.get("seconds") or 30)["label"],
            "outcome": spot.get("outcome") or "neutral",
            "script": spot.get("script"),
            "audio_url": spot.get("audio_url") or "",
            "audio_seconds": spot.get("audio_seconds") or 0,
            "voice_name": (project.get("voice") or {}).get("name") or "",
            "status": spot.get("status") or "pending",
            "comments": [f for f in (project.get("feedback") or [])
                         if f.get("spot_id") == spot.get("id")],
        })
    spots.sort(key=lambda s: (
        {"pregame": 0, "gameday": 1, "postgame": 2}.get(s["daypart"], 9),
        s["seconds"] or 0))
    return {
        "company": project.get("company") or "",
        "headline": share.get("headline") or "",
        "intro": share.get("intro") or "",
        "cta_label": share.get("cta_label") or "",
        "cta_url": share.get("cta_url") or "",
        "spots": spots,
        "general": [f for f in (project.get("feedback") or [])
                    if not f.get("spot_id")],
    }


# =====================================================================
# Pages
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html", version=_version(),
                           dayparts=catalog.DAYPARTS, tones=catalog.TONES,
                           outcomes=catalog.OUTCOMES,
                           client=request.args.get("client", ""))


@app.route("/library")
def library():
    return render_template("library.html", version=_version())


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": _version(),
                    "ai": ai.ready(), "voice": voices.ready(),
                    "cloudinary": store.cloudinary_ready(),
                    "projects": len(store.index())})


# =====================================================================
# Reference data
# =====================================================================
@app.route("/api/catalog")
def api_catalog():
    return jsonify({
        "ok": True,
        "dayparts": catalog.DAYPARTS,
        "lengths": [{"seconds": s, **catalog.LENGTHS[s]} for s in catalog.LENGTH_IDS],
        "tones": catalog.TONES,
        "outcomes": catalog.OUTCOMES,
        "safe_phrases": phrases.SAFE_PHRASES,
        "advisory": phrases.ADVISORY,
        "blocked_count": len(phrases.BLOCKED),
        "ai": ai.ready(), "voice": voices.ready(),
    })


@app.route("/api/clients")
def api_clients():
    q = (request.args.get("q") or "").strip()
    if clients_registry is None:
        return jsonify({"ok": True, "clients": [], "registry": False})
    try:
        rows = clients_registry.search_clients(q, limit=12) if q else \
            clients_registry.all_clients()[:12]
    except Exception:                                 # noqa: BLE001
        return jsonify({"ok": True, "clients": [], "registry": False})
    return jsonify({"ok": True, "registry": True, "clients": [
        {"name": r.get("name"), "slug": r.get("slug"),
         "url": r.get("url") or "", "products": r.get("products") or 0}
        for r in rows]})


@app.route("/api/phrase-check", methods=["POST"])
def api_phrase_check():
    body = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "scan": phrases.scan(
        body.get("text") or "",
        [str(b).lower() for b in (body.get("banned") or [])],
        body.get("daypart") or "", body.get("outcome") or "neutral")})


# =====================================================================
# Projects
# =====================================================================
@app.route("/api/projects", methods=["GET"])
def api_list():
    scope = (request.args.get("scope") or "").strip()
    q = (request.args.get("q") or "").strip().lower()
    rows = store.index()
    if scope in ("spec", "client"):
        rows = [r for r in rows if r.get("scope") == scope]
    if q:
        rows = [r for r in rows
                if q in f"{r.get('company','')} {r.get('client','')}".lower()]
    return jsonify({"ok": True, "count": len(rows), "projects": rows[:300]})


@app.route("/api/projects", methods=["POST"])
def api_create():
    body = request.get_json(silent=True) or {}
    company = str(body.get("company") or "").strip()
    if not company:
        return fail("Who is this for? Enter the business name.")
    if body.get("scope") == "client" and not str(body.get("client") or "").strip():
        return fail("Pick a client, or file it as a spec spot.")
    if rate_limited("create", 30, 300):
        return fail("That's a lot of projects at once — give it a minute.", 429)

    project = store.create({
        "scope": body.get("scope") or "spec",
        "client": str(body.get("client") or "").strip(),
        "company": company,
        "home_url": str(body.get("home_url") or "").strip(),
        "promotion": str(body.get("promotion") or "").strip(),
        "notes": str(body.get("notes") or "").strip(),
        "team_context": str(body.get("team_context") or "").strip(),
        "tone": body.get("tone") if body.get("tone") in catalog.TONE_IDS else "warm",
    }, actor_name())

    text, note = read_site(project["home_url"])
    brief = ai.read_brief(company, project["home_url"], text,
                          " ".join(x for x in [project["promotion"],
                                               project["notes"]] if x))
    if note:
        brief["site_note"] = note
    if project["promotion"] and not brief.get("offer"):
        brief["offer"] = project["promotion"]
    project["brief"] = brief
    store.save(project)
    _log("project_created", project=project["id"], company=company,
         scope=project["scope"])
    return jsonify({"ok": True, "project": project,
                    "banned": banned_terms(project)})


@app.route("/api/projects/<pid>", methods=["GET"])
def api_get(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    store.sort_spots(project)
    for spot in project["spots"]:
        decorate(project, spot)
    return jsonify({"ok": True, "project": project,
                    "banned": banned_terms(project),
                    "share_url": share_url(project)})


@app.route("/api/projects/<pid>", methods=["POST"])
def api_update(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    body = request.get_json(silent=True) or {}
    for key in ("company", "home_url", "promotion", "notes", "team_context",
                "client", "scope"):
        if key in body:
            project[key] = str(body[key] or "").strip()
    if body.get("tone") in catalog.TONE_IDS:
        project["tone"] = body["tone"]
    if isinstance(body.get("banned"), list):
        project["banned"] = [str(b).strip().lower()
                             for b in body["banned"] if str(b).strip()][:60]
    if isinstance(body.get("pronunciation"), list):
        project["pronunciation"] = [
            {"from": str(p.get("from") or "")[:80],
             "to": str(p.get("to") or "")[:80]}
            for p in body["pronunciation"]
            if str(p.get("from") or "").strip()][:40]
    if isinstance(body.get("brief"), dict):
        project["brief"].update(body["brief"])
    store.save(project)
    return jsonify({"ok": True, "project": project,
                    "banned": banned_terms(project)})


@app.route("/api/projects/<pid>", methods=["DELETE"])
def api_delete(pid):
    if not store.delete(pid):
        return fail("No project with that id.", 404)
    _log("project_deleted", project=pid)
    return jsonify({"ok": True})


# =====================================================================
# Writing
# =====================================================================
@app.route("/api/projects/<pid>/write", methods=["POST"])
def api_write(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    if rate_limited("write", 20, 300):
        return fail("Too many writes in a row — give it a minute.", 429)

    body = request.get_json(silent=True) or {}
    slots = body.get("slots")
    if not isinstance(slots, list) or not slots:
        slots = catalog.default_slots()
    slots = slots[:MAX_SPOTS]
    tone_id = body.get("tone") if body.get("tone") in catalog.TONE_IDS \
        else project.get("tone", "warm")
    steer = str(body.get("steer") or "")[:600]
    replace = bool(body.get("replace"))
    banned = banned_terms(project)

    if replace:
        project["spots"] = []

    written, failures = [], []
    for slot in slots:
        dp = slot.get("daypart")
        seconds = int(slot.get("seconds") or 30)
        outcome = slot.get("outcome") or "neutral"
        if dp not in catalog.DAYPART_IDS or seconds not in catalog.LENGTH_IDS \
                or outcome not in catalog.OUTCOME_IDS:
            continue
        if dp != "postgame":
            outcome = "neutral"
        out = ai.write_spot(project["brief"], dp, seconds, tone_id, outcome,
                            banned, steer)
        spot = {
            "id": store.spot_id(), "daypart": dp, "seconds": seconds,
            "outcome": outcome, "tone": tone_id,
            "script": out.get("script") or "", "hook": out.get("hook") or "",
            "notes": out.get("notes") or "", "ai": bool(out.get("ai")),
            "ai_reason": out.get("ai_reason") or "",
            "phrases_used": out.get("phrasesUsed") or [],
            "status": "draft", "versions": [],
        }
        decorate(project, spot)
        store.push_version(spot, "first draft", actor_name())
        store.upsert_spot(project, spot)
        written.append(spot)
        if not spot["ai"]:
            failures.append(f"{dp} {seconds}s")

    store.sort_spots(project)
    store.save(project)
    _log("spots_written", project=pid, count=len(written),
         template_fallbacks=len(failures))

    payload = {"ok": True, "project": project, "written": len(written)}
    if failures:
        payload["warning"] = ("The writer didn't run for: "
                              + ", ".join(failures)
                              + ". Those are templates, not AI copy — edit "
                                "them before you send anything.")
    return jsonify(payload)


@app.route("/api/projects/<pid>/spots/<sid>/rewrite", methods=["POST"])
def api_rewrite(pid, sid):
    project = store.load(pid)
    spot = store.get_spot(project, sid) if project else None
    if not spot:
        return fail("No spot with that id.", 404)
    if rate_limited("write", 20, 300):
        return fail("Too many writes in a row — give it a minute.", 429)
    body = request.get_json(silent=True) or {}
    tone_id = body.get("tone") if body.get("tone") in catalog.TONE_IDS \
        else spot.get("tone") or project.get("tone", "warm")
    out = ai.write_spot(project["brief"], spot["daypart"], spot["seconds"],
                        tone_id, spot.get("outcome") or "neutral",
                        banned_terms(project), str(body.get("steer") or "")[:600])
    spot.update({"script": out.get("script") or spot.get("script"),
                 "hook": out.get("hook") or "", "notes": out.get("notes") or "",
                 "tone": tone_id, "ai": bool(out.get("ai")),
                 "ai_reason": out.get("ai_reason") or "",
                 "phrases_used": out.get("phrasesUsed") or [],
                 "hand_edited": False, "status": "draft"})
    decorate(project, spot)
    store.push_version(spot, "rewritten", actor_name())
    store.save(project)
    return jsonify({"ok": True, "spot": spot,
                    "warning": None if spot["ai"] else
                    "Template, not AI copy — the writer didn't run."})


@app.route("/api/projects/<pid>/spots/<sid>/tighten", methods=["POST"])
def api_tighten(pid, sid):
    project = store.load(pid)
    spot = store.get_spot(project, sid) if project else None
    if not spot:
        return fail("No spot with that id.", 404)
    grade = catalog.grade(spot.get("script") or "", spot.get("seconds") or 30)
    if grade["state"] != "long":
        return fail("That one's already on the clock.")
    out = ai.tighten(spot["script"], spot["seconds"], grade["delta"],
                     spot.get("tone") or "warm", banned_terms(project))
    if not out.get("ai"):
        return fail(out.get("ai_reason") or "Couldn't tighten it just now.", 503)
    spot["script"] = out.get("script") or spot["script"]
    spot["tighten_note"] = out.get("whatWentAndWhy") or ""
    spot["status"] = "draft"
    decorate(project, spot)
    store.push_version(spot, "tightened", actor_name())
    store.save(project)
    return jsonify({"ok": True, "spot": spot})


@app.route("/api/projects/<pid>/spots/<sid>", methods=["POST"])
def api_edit_spot(pid, sid):
    project = store.load(pid)
    spot = store.get_spot(project, sid) if project else None
    if not spot:
        return fail("No spot with that id.", 404)
    body = request.get_json(silent=True) or {}
    if "script" in body:
        spot["script"] = str(body["script"] or "").strip()[:4000]
        # A hand edit is not the same thing as a template fallback. The
        # 'ai' flag answers "did the writer run?"; this answers "has a
        # person been in here since?" — both are worth seeing.
        if body.get("hand_edited"):
            spot["hand_edited"] = True
        spot["status"] = "draft"
        store.push_version(spot, "hand edited", actor_name())
    if "hidden" in body:
        spot["hidden"] = bool(body["hidden"])
    decorate(project, spot)
    store.save(project)
    return jsonify({"ok": True, "spot": spot})


@app.route("/api/projects/<pid>/spots/<sid>", methods=["DELETE"])
def api_delete_spot(pid, sid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    before = len(project.get("spots") or [])
    project["spots"] = [s for s in project.get("spots") or []
                        if s.get("id") != sid]
    if len(project["spots"]) == before:
        return fail("No spot with that id.", 404)
    store.save(project)
    return jsonify({"ok": True})


# =====================================================================
# Casting and recording
# =====================================================================
@app.route("/api/projects/<pid>/cast", methods=["POST"])
def api_cast(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    if not voices.ready():
        return fail("ElevenLabs isn't configured. Add ELEVENLABS_API_KEY to "
                    "record; scripts still work without it.", 503)
    body = request.get_json(silent=True) or {}
    profile = ai.voice_profile(project.get("brief") or {},
                               body.get("tone") or project.get("tone", "warm"))
    want = profile.get("recommendation") or {}
    try:
        matches = voices.match_voices(want, int(body.get("count") or 4))
    except voices.VoiceError as exc:
        return fail(str(exc), 503)
    project["voice_profile"] = profile
    store.save(project)
    return jsonify({"ok": True, "profile": profile, "voices": matches})


@app.route("/api/projects/<pid>/voice", methods=["POST"])
def api_set_voice(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    body = request.get_json(silent=True) or {}
    vid = str(body.get("voice_id") or "").strip()
    if not vid:
        return fail("Pick a voice.")
    project["voice"] = {"voice_id": vid,
                        "name": str(body.get("name") or "")[:80],
                        "energy": body.get("energy") or "energetic"}
    store.save(project)
    return jsonify({"ok": True, "voice": project["voice"]})


@app.route("/api/projects/<pid>/spots/<sid>/record", methods=["POST"])
def api_record(pid, sid):
    project = store.load(pid)
    spot = store.get_spot(project, sid) if project else None
    if not spot:
        return fail("No spot with that id.", 404)
    if not spot.get("script"):
        return fail("Nothing to record — write the spot first.")
    # The trademark check runs before the voice check: it's the problem
    # that has to be fixed either way, and it costs nothing to find.
    check = phrases.scan(spot["script"], banned_terms(project),
                         spot.get("daypart") or "",
                         spot.get("outcome") or "neutral")
    if not check["clean"]:
        hits = ", ".join(h["term"] for h in check["blocked"])
        return fail(f"This script still says: {hits}. That's a trademark — "
                    f"fix it before spending a render.")
    voice = project.get("voice") or {}
    if not voice.get("voice_id"):
        return fail("Cast a voice for this project first.")
    if rate_limited("record", 40, 600):
        return fail("Too many renders in a row — give it a minute.", 429)

    spoken = speech.normalize_for_speech(spot["script"],
                                         project.get("pronunciation"))
    try:
        out = voices.render_audio(voice["voice_id"], spoken["spoken"],
                                  voice.get("energy") or "energetic")
    except voices.VoiceError as exc:
        return fail(str(exc), 503)

    stored = store.store_audio(project, spot, out["audio"])
    spot.update({"audio_url": stored["url"], "audio_where": stored["where"],
                 "audio_seconds": out["seconds"],
                 "audio_measured": out["measured"],
                 "audio_voice": voice.get("name") or voice["voice_id"],
                 "recorded_at": store.now()})
    over = round(out["seconds"] - float(spot["seconds"]), 2)
    spot["runtime_note"] = (
        f"{out['seconds']}s — {abs(over)}s over a {spot['seconds']}s slot."
        if over > 0.35 else
        f"{out['seconds']}s — fits a {spot['seconds']}s slot.")
    decorate(project, spot)
    store.save(project)
    _log("spot_recorded", project=pid, spot=sid, seconds=out["seconds"])
    payload = {"ok": True, "spot": spot, "spoken": spoken}
    if stored.get("warning"):
        payload["warning"] = stored["warning"]
    return jsonify(payload)


# =====================================================================
# Sharing
# =====================================================================
def share_url(project: dict) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    path = f"/tools/fan-radio/r/{(project.get('share') or {}).get('token')}"
    return (base + path) if base else path


@app.route("/api/projects/<pid>/share", methods=["POST"])
def api_share(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    body = request.get_json(silent=True) or {}
    share = project.setdefault("share", {})
    share.setdefault("token", store.new_token())
    if "enabled" in body:
        share["enabled"] = bool(body["enabled"])
    for key, cap in (("headline", 120), ("intro", 600),
                     ("cta_label", 60), ("cta_url", 400)):
        if key in body:
            share[key] = str(body[key] or "").strip()[:cap]
    if body.get("regenerate"):
        share["token"] = store.new_token()
        share["opened"] = 0
    store.save(project)
    _log("share_updated", project=pid, enabled=bool(share.get("enabled")))
    url = share_url(project)
    warning = None
    if share.get("enabled") and not os.environ.get("PUBLIC_BASE_URL"):
        warning = ("PUBLIC_BASE_URL isn't set, so the link above is a path, "
                   "not a full URL. Set it on Render before you send it to "
                   "a client.")
    return jsonify({"ok": True, "share": share, "share_url": url,
                    "warning": warning})


# =====================================================================
# Public — the customer's page. No Hub login.
# =====================================================================
@app.route("/r/<token>")
def public_page(token):
    project = store.find_by_token(token)
    if not project or not (project.get("share") or {}).get("enabled"):
        return render_template("share.html", missing=True, token=""), 404
    project["share"]["opened"] = int(project["share"].get("opened") or 0) + 1
    store.save(project)
    return render_template("share.html", missing=False, token=token,
                           company=project.get("company") or "")


@app.route("/api/public/<token>")
def api_public(token):
    if rate_limited("public", 120, 60):
        return fail("Too many requests. Refresh in a moment.", 429)
    project = store.find_by_token(token)
    if not project or not (project.get("share") or {}).get("enabled"):
        return fail("This link isn't active. Ask your account manager for a "
                    "fresh one.", 404)
    return jsonify({"ok": True, **public_view(project)})


@app.route("/api/public/<token>/feedback", methods=["POST"])
def api_public_feedback(token):
    if rate_limited("feedback", 30, 300):
        return fail("That's a lot of comments at once — give it a minute.", 429)
    project = store.find_by_token(token)
    if not project or not (project.get("share") or {}).get("enabled"):
        return fail("This link isn't active. Ask your account manager for a "
                    "fresh one.", 404)

    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action not in ("approve", "changes", "comment", "approve_all"):
        return fail("Pick approve or request changes.")
    name = str(body.get("name") or "").strip()
    if not name:
        return fail("Add your name so we know who approved it.")
    comment = str(body.get("comment") or "").strip()
    if action == "changes" and not comment:
        return fail("Tell us what to change and we'll turn it around.")

    sid = str(body.get("spot_id") or "").strip()
    targets = []
    if action == "approve_all":
        targets = [s for s in project.get("spots") or []
                   if s.get("script") and not s.get("hidden")]
    elif sid:
        spot = store.get_spot(project, sid)
        if not spot:
            return fail("That spot isn't on this page.")
        targets = [spot]
    elif action != "comment":
        return fail("That spot isn't on this page.")

    # Written before anything else runs — no notification, no redirect, no
    # webhook happens ahead of the record being on disk.
    if targets:
        for spot in targets:
            spot["status"] = "approved" if action in ("approve", "approve_all") \
                else "changes"
            spot["decided_at"] = store.now()
            spot["decided_by"] = name[:80]
            store.add_feedback(project, {
                "name": name, "spot_id": spot["id"],
                "action": "approve" if action in ("approve", "approve_all")
                else action, "comment": comment})
    else:
        store.add_feedback(project, {"name": name, "spot_id": "",
                                     "action": "comment", "comment": comment})
    store.save(project)
    _log("customer_feedback", project=project["id"], action=action,
         spots=len(targets), by=name[:40])
    _notify(project, name, action, comment, len(targets))
    return jsonify({"ok": True, **public_view(project)})


def _notify(project: dict, name: str, action: str, comment: str, count: int):
    """Best-effort ping. Runs after the write, never before, and a failure
    here can't lose the feedback."""
    url = (os.environ.get("FAN_RADIO_NOTIFY_URL") or "").strip()
    if not url:
        return
    try:
        requests.post(url, json={
            "source": "fan-radio", "project": project.get("id"),
            "company": project.get("company"), "client": project.get("client"),
            "by": name, "action": action, "spots": count,
            "comment": comment[:1000], "at": store.now()}, timeout=6)
    except requests.RequestException:
        pass


@app.route("/audio/<name>")
def audio(name):
    """Local-disk fallback for renders when Cloudinary isn't configured.
    Public because the customer page needs it; the filename is random."""
    path = store.local_audio_path(name)
    if not path:
        return fail("Not found.", 404)
    return send_file(path, mimetype="audio/mpeg", conditional=True)


if __name__ == "__main__":                            # standalone dev run
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8091")),
            debug=False)
