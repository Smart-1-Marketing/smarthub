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
from pathlib import Path

import requests
from flask import (Flask, Response, jsonify, render_template, request,
                   send_file)

from . import ai, catalog, phrases, speech, store, voices
from hub import radio_share, voice_casting

try:
    from hub import radio_spec
except Exception:                                     # noqa: BLE001
    # Standalone, or the shared rules failed to import. Every reader of it
    # below asks `_need_spec()` and says so rather than drawing a level or a
    # check over numbers it made up.
    radio_spec = None

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

# The three mount-relative prefixes the CUSTOMER reaches, and a customer has
# no Hub login. Declared here rather than in wsgi.py so the mount and the
# module cannot disagree about what is public -- the arrangement
# modules/scans, modules/ads_builder and modules/sales_builder already use.
# wsgi.py hands it to _mount, which passes it to BOTH halves: AuthGuard, so
# the page is reachable at all, and HubBar, so the staff sidebar, help layer
# and feedback tab are not injected into a page a client reads. Either half
# missing is its own failure, in opposite directions.
#
# /r/            the approval page itself
# /api/public/   what that page fetches, and the approve/comment it posts
# /audio/        the local-disk render the page's <audio> element plays,
#                which is why a missing Cloudinary credential must not also
#                mean a silent player
PUBLIC_PREFIXES = ("/r/", "/api/public/", "/audio/")
MOUNT = "/tools/fan-radio"

MAX_SPOTS = 12
_limiter = radio_share.RateLimiter()


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
    return _limiter.hit(bucket, client_ip(), limit, window)


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
    was_spoken = spot.get("spoken")
    spoken = speech.normalize_for_speech(spot.get("script") or "",
                                         project.get("pronunciation"))
    spot["spoken"] = spoken["spoken"]
    spot["speech_changes"] = spoken["changes"]

    # What the voice is handed has changed, so anything recorded from the
    # previous wording is audio of words nobody approved. Decided here rather
    # than at the four routes that can change a script — an edit, a rewrite, a
    # tighten and a pronunciation save — because a rule three of four call
    # sites remember is not a rule, and this is the one function all four
    # already pass through.
    #
    # The read is **marked, never deleted**: it cost money, it is still the
    # right voice, and a player that vanishes reads as a fault. The mix is
    # dropped, because a mix is a statement about two particular tracks and it
    # plays perfectly well while being of the wrong script — which is exactly
    # what makes it worth removing rather than flagging.
    if was_spoken is not None and was_spoken != spot["spoken"]:
        if spot.get("audio_url"):
            spot["audio_stale"] = True
        _drop_mix(spot, "The script changed, so the mix of the previous "
                        "wording went with it. Re-record and render again.")
    elif spot.get("audio_url") and was_spoken == spot["spoken"]:
        spot.pop("audio_stale", None)
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
        mix = spot.get("mix") or {}
        spots.append({
            "id": spot.get("id"),
            "daypart": spot.get("daypart"),
            "daypart_label": dp["label"],
            "daypart_when": dp["when"],
            "seconds": spot.get("seconds"),
            "length_label": catalog.budget(spot.get("seconds") or 30)["label"],
            "outcome": spot.get("outcome") or "neutral",
            "script": spot.get("script"),
            # The finished mix is what a client should hear. A spot not yet
            # mixed falls back to its raw read, so there is still something to
            # review while a bed is being composed; a spot with neither is left
            # out above, because offering an empty player is worse than not
            # listing it.
            "audio_url": (mix.get("audio_url") or spot.get("audio_url") or ""),
            "audio_seconds": (mix.get("seconds") if mix.get("audio_url")
                              else spot.get("audio_seconds")) or 0,
            "audio_is_mix": bool(mix.get("audio_url")),
            "has_bed": bool((spot.get("bed") or {}).get("audio_url")),
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
        "lengths": [{"seconds": s, **catalog.LENGTHS[s]}
                    for s in catalog.LENGTH_IDS],
        # Which of the four are ticked when the picker is first drawn. Read
        # from the catalog rather than decided in the browser, or the tool
        # would bill for a :10 and a :60 on every job that wanted the pair.
        "default_lengths": catalog.DEFAULT_LENGTH_IDS,
        # The casting question, with the words each answer actually matches on
        # and, for energy, the `style` value it sends on the render. Printed on
        # the picker rather than summarised: "Announcer" is not a mood, it is a
        # search for announcer/commercial/broadcast/promo in what ElevenLabs
        # publishes about a voice, and a screen that says so lets somebody pick
        # differently before listening to three wrong ones.
        "voice_characteristics": voice_casting.characteristics_detail(),
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
    # `products` on a registry row is the LIST of product names --
    # product_count is the number. Passed through raw, the browser joined
    # the list into "RETARGETING: Website Retargeting,..." and printed it
    # where a count belongs.
    return jsonify({"ok": True, "registry": True, "clients": [
        {"name": r.get("name"), "slug": r.get("slug"),
         "url": r.get("url") or "",
         "products": r.get("product_count")
         if r.get("product_count") is not None
         else (len(r["products"]) if isinstance(r.get("products"), (list, tuple, set))
               else r.get("products") or 0)}
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


@app.route("/api/voices/match", methods=["POST"])
def api_voice_match():
    """Re-rank against characteristics somebody edited by hand.

    `/cast` asks a model what the read should sound like; this is the same
    ranking asked again once a rep has disagreed with it. Two controls, one
    scoring pass — the alternative is a shortlist a rep cannot argue with,
    which is what this tool had.
    """
    if not voices.ready():
        return fail("ElevenLabs isn't configured. Add ELEVENLABS_API_KEY to "
                    "record; scripts still work without it.", 503)
    body = request.get_json(silent=True) or {}
    want = body.get("want") if isinstance(body.get("want"), dict) else {}
    try:
        matched = voices.match_voices(want, int(body.get("count") or 4))
    except voices.VoiceError as exc:
        return fail(str(exc), 503)
    return jsonify({"ok": True, "voices": matched,
                    "quality": voices.match_quality(matched),
                    "asked": voice_casting.asked_count(want)})


@app.route("/api/voices/by-id", methods=["POST"])
def api_voice_by_id():
    """One voice named rather than ranked.

    The way past the shortlist: a client who has already chosen a voice, or one
    cloned on the account and carrying no labels for the ranking to score, is
    reachable by ID. Cloning itself is deliberately not here — see the note on
    `/api/voices/account` below.
    """
    if not voices.ready():
        return fail("ElevenLabs isn't configured.", 503)
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, "voice": voices.get_voice(body.get("voice_id"))})
    except voices.VoiceError as exc:
        return fail(str(exc), 502)


@app.route("/api/voices/account")
def api_voice_account():
    """What is left of the month's ElevenLabs characters.

    A button rather than a page load: one authenticated call that generates
    nothing and bills nothing. It matters more here than it used to, because
    the length menu now runs to a :60 — about twice a :30 of the allowance,
    every time it is re-recorded.

    **Voice cloning is deliberately not offered in this tool.** The Radio Ad
    Creator has it, it creates a voice on the shared ElevenLabs account out of
    somebody's recordings, and that is a consent question rather than a button
    — one place to answer it is the right number. A voice cloned there shows up
    in this account's pool, so `/api/voices/by-id` reaches it here.
    """
    try:
        return jsonify({"ok": True, **voices.account_check()})
    except voices.VoiceError as exc:
        return fail(str(exc), 502)


@app.route("/api/speech/preview", methods=["POST"])
def api_speech_preview():
    """What the voice is actually handed, before a render is paid for.

    `speech.normalize_for_speech()` rewrites the copy a rep typed into the copy
    ElevenLabs reads, and until now nothing showed the difference — so a
    pronunciation that was not taking looked identical to one that was, and the
    only way to find out was to spend a render.
    """
    body = request.get_json(silent=True) or {}
    out = speech.normalize_for_speech(str(body.get("script") or ""),
                                      body.get("pronunciation") or [])
    return jsonify({"ok": True, **out})


@app.route("/api/projects/<pid>/pronunciations", methods=["POST"])
def api_pronunciations(pid):
    """How this client's name is said.

    The field has been on every project row since this tool was written and no
    route ever set it: `decorate()` reads it on every spot and it could only
    ever be empty — a declared-and-never-wired integration point, on the one
    thing a client notices immediately when it is wrong.

    Every spot is re-decorated on save rather than only the ones written after
    the change, or a pronunciation added halfway through a job would apply to
    half the spots and nothing on screen would say which.
    """
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    body = request.get_json(silent=True) or {}
    # `{"from", "to"}`, which is the shape `speech.normalize_for_speech()` has
    # always read. Storing `{"word", "say"}` here would have been the same
    # failure one level on: a field written, kept, and silently ignored by the
    # only thing that reads it.
    rows = []
    for row in (body.get("pronunciation") or [])[:60]:
        if not isinstance(row, dict):
            continue
        frm = str(row.get("from") or "").strip()[:60]
        to = str(row.get("to") or "").strip()[:80]
        if frm and to:
            rows.append({"from": frm, "to": to})
    project["pronunciation"] = rows
    for spot in project.get("spots") or []:
        decorate(project, spot)
    store.save(project)
    return jsonify({"ok": True, "project": project,
                    "pronunciation": rows})


@app.route("/api/projects/<pid>/voice", methods=["POST"])
def api_set_voice(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    body = request.get_json(silent=True) or {}
    vid = str(body.get("voice_id") or "").strip()
    if not vid:
        return fail("Pick a voice.")
    try:
        speed = min(1.2, max(0.7, float(body.get("speed", 1.0))))
    except (TypeError, ValueError):
        speed = 1.0
    project["voice"] = {"voice_id": vid,
                        "name": str(body.get("name") or "")[:80],
                        "energy": body.get("energy") or "energetic",
                        "speed": speed}
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
                                  voice.get("energy") or "energetic",
                                  voice.get("speed", 1.0))
    except voices.VoiceError as exc:
        return fail(str(exc), 503)

    stored = store.store_audio(project, spot, out["audio"])
    spot.update({"audio_url": stored["url"], "audio_where": stored["where"],
                 "audio_seconds": out["seconds"],
                 "audio_measured": out["measured"],
                 "audio_voice": voice.get("name") or voice["voice_id"],
                 "recorded_at": store.now()})
    # A new read retires the mix made from the old one: a mix is a statement
    # about two particular tracks, and one left standing over a re-recorded
    # voice is a file nobody can account for — which plays perfectly well.
    _drop_mix(spot, "This spot was re-recorded, so the mix made from the "
                    "previous read went with it. Render it again.")
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
# Background music, the mix, and the checks on it
# =====================================================================
# Everything in this section is `hub/radio_spec.py`'s, read rather than
# restated. That module's own opening line says why it exists: it carries the
# bed vocabulary, the mix levels, the length arithmetic, the QC checks and the
# one honest way to measure a finished file "so `modules/fan_radio` can read
# the same rules later without a second copy of them being written first."
# This is that later.
#
# Two constraints decide the shape, and both are inherited rather than
# rediscovered. There is no ffmpeg, ffprobe, pydub or numpy in this runtime, so
# a bed is **composed at least three seconds longer than the spot** by
# ElevenLabs and nothing is ever trimmed to fit; and the mix is rendered
# **in the browser** through the
# Web Audio API, which hands back a WAV whose header states its own length — so
# the duration filed against a spot is measured here, from the bytes we stored,
# rather than reported by the page that made them.
#
# The unit is the **spot**, not a slot. Radio Promo keys its beds and mixes on
# a length because a project there writes one script per length; a Fan Radio
# project writes several spots that may share a length and differ by daypart
# and outcome, so a bed keyed on ":30" would be the same music under the
# pre-game and the post-game read. They live on the spot's own row.
_BED_CAP_MB = 25
_MIX_CAP_MB = 40
_AUDIO_ROLES = ("vo", "bed", "mix")
_PROXY_CAP_BYTES = 40 * 1024 * 1024
_PROXY_TIMEOUT = (5, 30)
_UPLOAD_KINDS = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
                 "audio/wave", "audio/mp4", "audio/aac", "audio/ogg",
                 "audio/webm", "video/webm"}
_BED_EXTRA_SECONDS = 3.0


def _bed_minimum_seconds(spot: dict) -> float:
    """The required runway for every Fan Radio bed, generated or uploaded."""
    return float(spot.get("seconds") or 30) + _BED_EXTRA_SECONDS


def _need_spec():
    """`hub/radio_spec`, or a reason. Never raises."""
    if radio_spec is None:
        return None, ("The shared radio rules could not be loaded, so no bed "
                      "level, length or check can be quoted here.")
    return radio_spec, ""


def _spot_or_fail(pid: str, sid: str):
    project = store.load(pid)
    if not project:
        raise LookupError("No project with that id.")
    spot = store.get_spot(project, sid)
    if not spot:
        raise LookupError("No spot with that id.")
    return project, spot


def _drop_mix(spot: dict, why: str) -> None:
    """Retire the mix, because what went into it has changed.

    A mix is a statement about two particular tracks. Left standing over a
    replaced bed, a re-recorded read or an edited script it is a file nobody
    can account for — and it plays perfectly well, which is what makes it worth
    dropping rather than flagging. The reason is kept so the panel can say the
    mix went and why, rather than a player quietly disappearing.
    """
    if spot.pop("mix", None):
        spot["mix_note"] = why
    else:
        spot.pop("mix_note", None)


def _read_upload(field: str = "file", *, kinds=None, cap_mb: int = 25):
    """One uploaded audio file, or a sentence saying why not.

    The refusals are named individually because they send somebody to
    different places: a file too large is re-exported, a file of the wrong
    type is converted, and an empty one is a failed export upstream.
    """
    upload = request.files.get(field)
    if not upload or not upload.filename:
        raise ValueError("Choose a file to upload.")
    data = upload.read()
    if not data:
        raise ValueError(f"{upload.filename} came through empty.")
    if len(data) > cap_mb * 1024 * 1024:
        raise ValueError(f"{upload.filename} is larger than the {cap_mb} MB limit.")
    allowed = _UPLOAD_KINDS if kinds is None else kinds
    if upload.mimetype and upload.mimetype not in allowed:
        raise ValueError(f"{upload.filename} is not a supported audio format.")
    return upload.filename, data, (upload.mimetype or "audio/mpeg")


def _measured(data: bytes, filename: str) -> dict:
    """How long this file is, and whether we actually know.

    A WAV says so in its header and is measured. Anything else is **not
    measured** — an MP3 somebody uploaded is at a bitrate nobody here chose, so
    the byte-count arithmetic that prices a bed we asked for does not apply to
    it, and a number the browser reported about a file is not a measurement of
    the file we stored.
    """
    spec, _ = _need_spec()
    seconds = spec.wav_seconds(data) if spec else None
    if seconds is not None:
        return {"seconds": seconds, "measured": True}
    return {"seconds": None, "measured": False,
            "measure_note": (f"{filename} is not a WAV, and there is no audio "
                             "decoder in this runtime, so its length is not "
                             "measured. The mix is what gets measured.")}


def _ext_of(filename: str, fallback: str = "mp3") -> str:
    ext = str(filename or "").rsplit(".", 1)[-1].lower()
    return ext if ext in store.AUDIO_EXTS else fallback


# ------------------------------------------------------------------- config
@app.route("/api/mix/config")
def api_mix_config():
    """Everything the Music and Mix steps need, decided server-side.

    The browser renders the mix but chooses none of it: the dB pair, the fades
    and the sample rate come from here, so the level a panel shows is the level
    that renders. A second copy of those numbers in JavaScript is how the
    screen and the file come to disagree about how loud something is.
    """
    spec, error = _need_spec()
    if not spec:
        return jsonify({"ok": True, "available": False, "error": error,
                        "moods": [], "levels": [], "mix": {}})
    state = spec.available()
    levels = spec.bed_levels()
    return jsonify({
        "ok": True,
        "available": state["levels"],
        "can_compose": state["compose"] and spec.generation_enabled(),
        "compose_note": ("" if spec.generation_enabled() else
                         "Composing is switched off on this deployment "
                         "(MUSIC_GENERATION_ENABLED), so the mood tiles fill "
                         "the prompt box but nothing is composed. Upload a bed "
                         "instead."),
        "error": state["error"] or levels.get("error", ""),
        "moods": spec.bed_moods(),
        "levels": levels["levels"],
        "level_reference": levels["reference"],
        "limits": spec.bed_limits(),
        "mix": spec.mix_defaults(levels["reference"]),
    })


@app.route("/api/music-library")
def api_music_library():
    return jsonify({"ok": True, "tracks": store.music_library()})


# --------------------------------------------------------------------- beds
@app.route("/api/projects/<pid>/spots/<sid>/bed/compose", methods=["POST"])
def api_bed_compose(pid, sid):
    """Compose one real bed, at this spot's own length.

    Billed per generation, so it is a button and never a page load — and the
    content-keyed cache, the metering and the refusal that keeps its row are
    all the shared audio service's rather than repeated here.
    """
    try:
        project, spot = _spot_or_fail(pid, sid)
    except LookupError as exc:
        return fail(str(exc), 404)
    spec, error = _need_spec()
    if not spec:
        return fail(error, 503)
    if not spec.generation_enabled():
        return fail("Composing is switched off on this deployment. Upload a bed "
                    "instead, or set MUSIC_GENERATION_ENABLED.", 503)

    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt") or "").strip()
    mood = str(data.get("mood") or "").strip()
    if not prompt and mood:
        # A mood tile fills the box with the words it will actually send. A
        # mood the shared table does not carry contributes nothing rather than
        # its own name — "Whimsical" as the whole brief is worse than an empty
        # box.
        for entry in spec.bed_moods():
            if mood.lower() in (entry["id"], entry["label"].lower()):
                prompt = entry["prompt"]
                break
    if not prompt:
        return fail("Describe the bed, or pick a mood to fill the box in.")
    if rate_limited("bed", 20, 600):
        return fail("Too many beds composed in a row — give it a minute.", 429)

    minimum_seconds = _bed_minimum_seconds(spot)
    out = spec.compose_bed(prompt, spot.get("seconds") or 30,
                           extra_seconds=_BED_EXTRA_SECONDS)
    if out.get("error"):
        return fail(out["error"], 502)
    if out.get("_mock") or not out.get("audio_bytes"):
        # Mock mode produces no audio and says so. Recording it as a bed would
        # file a spot that is silent under the voice, which is exactly what the
        # bed_source check blocks — so it is refused here rather than written
        # and then blocked later.
        return fail(out.get("note") or "No audio came back, so there is no bed "
                    "to save.", 502)

    asset = store.store_asset(project, spot, "bed", out["audio_bytes"], "mp3")
    spot["bed"] = {"kind": "composed", "prompt": prompt, "mood": mood,
                   "audio_url": asset["url"], "audio_where": asset["where"],
                   "seconds": out.get("seconds"),
                   "measured": out.get("seconds") is not None,
                   "requested_seconds": out.get("requested_seconds"),
                   "minimum_seconds": minimum_seconds,
                   "bytes": out.get("bytes"), "at": store.now()}
    save_name = str(data.get("save_name") or "").strip()[:80]
    if save_name:
        track = store.save_music_track(save_name, out["audio_bytes"], "mp3", {
            "kind": "composed", "prompt": prompt, "mood": mood,
            "seconds": out.get("seconds"), "measured": out.get("seconds") is not None,
            "minimum_seconds": minimum_seconds,
        })
        spot["bed"]["library_track_id"] = track["id"]
    _drop_mix(spot, "The bed changed, so the mix made from the old one went "
                    "with it. Render it again.")
    store.save(project)
    _log("bed_composed", project=pid, spot=sid,
         client=project.get("client") or "")
    payload = {"ok": True, "spot": spot}
    if asset.get("warning"):
        payload["warning"] = asset["warning"]
    return jsonify(payload)


@app.route("/api/projects/<pid>/spots/<sid>/bed/upload", methods=["POST"])
def api_bed_upload(pid, sid):
    """A bed somebody already has. The other half of the same choice.

    A client who arrives with a licensed track should not be made to compose
    one, and neither self-serve platform this was specced against forces
    generated music.
    """
    try:
        project, spot = _spot_or_fail(pid, sid)
        filename, data, mimetype = _read_upload(cap_mb=_BED_CAP_MB)
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))

    spec, error = _need_spec()
    if not spec:
        return fail(error, 503)
    length = _measured(data, filename)
    # WAV duration is exact. MP3 frames provide an estimate; formats that do
    # not expose enough information here are refused because this rule is a
    # guarantee, not a suggestion the browser may happen to notice.
    if not length["measured"]:
        estimated = spec.mp3_seconds(data)
        if estimated is None:
            return fail("Use a WAV or MP3 bed so Fan Radio can verify it is at "
                        "least three seconds longer than the commercial.")
        length.update({"seconds": estimated, "estimated": True,
                       "measure_note": "MP3 duration estimated from its frames."})
    minimum_seconds = _bed_minimum_seconds(spot)
    if float(length["seconds"]) + 0.01 < minimum_seconds:
        return fail(f"This bed is {length['seconds']}s. It must be at least "
                    f"{minimum_seconds:g}s for this {spot.get('seconds')}s commercial.")

    ext = _ext_of(filename)
    asset = store.store_asset(project, spot, "bed", data, ext)
    spot["bed"] = {"kind": "upload", "prompt": "", "filename": filename,
                   "mimetype": mimetype, "audio_url": asset["url"],
                   "audio_where": asset["where"], "bytes": len(data),
                   "minimum_seconds": minimum_seconds, "at": store.now(), **length}
    save_name = str(request.form.get("save_name") or "").strip()[:80]
    if save_name:
        track = store.save_music_track(save_name, data, ext, {
            "kind": "upload", "filename": filename, "mimetype": mimetype,
            "minimum_seconds": minimum_seconds, **length,
        })
        spot["bed"]["library_track_id"] = track["id"]
    _drop_mix(spot, "The bed changed, so the mix made from the old one went "
                    "with it. Render it again.")
    store.save(project)
    _log("bed_uploaded", project=pid, spot=sid,
         client=project.get("client") or "")
    payload = {"ok": True, "spot": spot}
    if asset.get("warning"):
        payload["warning"] = asset["warning"]
    return jsonify(payload)


@app.route("/api/projects/<pid>/spots/<sid>/bed/apply-to-project", methods=["POST"])
def api_apply_bed_to_project(pid, sid):
    """Reuse this bed wherever its verified length can cover the commercial."""
    try:
        project, source = _spot_or_fail(pid, sid)
    except LookupError as exc:
        return fail(str(exc), 404)
    bed = source.get("bed") or {}
    if not bed.get("audio_url") or not bed.get("seconds"):
        return fail("Create or upload a verified bed first.")
    applied = []
    for spot in project.get("spots") or []:
        if spot.get("id") == sid or _bed_minimum_seconds(spot) > float(bed["seconds"]):
            continue
        spot["bed"] = {**bed, "reused_from": sid, "at": store.now()}
        _drop_mix(spot, "The shared bed changed, so the old mix went with it. Render it again.")
        applied.append(spot.get("id"))
    store.save(project)
    return jsonify({"ok": True, "project": project, "applied": applied,
                    "skipped": len(project.get("spots") or []) - len(applied) - 1})


@app.route("/api/projects/<pid>/music-library/<track_id>/apply", methods=["POST"])
def api_apply_library_track(pid, track_id):
    project = store.load(pid)
    track = store.music_track(track_id)
    if not project:
        return fail("No project with that id.", 404)
    if not track or not track.get("audio_url") or not track.get("seconds"):
        return fail("That saved track is no longer available.", 404)
    applied = []
    for spot in project.get("spots") or []:
        if _bed_minimum_seconds(spot) > float(track["seconds"]):
            continue
        spot["bed"] = {**track, "library_track_id": track_id,
                       "kind": "library", "at": store.now()}
        _drop_mix(spot, "The library bed changed, so the old mix went with it. Render it again.")
        applied.append(spot.get("id"))
    store.save(project)
    return jsonify({"ok": True, "project": project, "applied": applied})


@app.route("/api/projects/<pid>/spots/<sid>/bed/clear", methods=["POST"])
def api_bed_clear(pid, sid):
    """No bed: a straight voice read.

    A real answer rather than an unfinished one — a sponsor mention and a
    news-style read both ship without music — so it is a deliberate press, and
    the bed check passes it rather than treating a missing bed as a gap.
    """
    try:
        project, spot = _spot_or_fail(pid, sid)
    except LookupError as exc:
        return fail(str(exc), 404)
    spot.pop("bed", None)
    _drop_mix(spot, "The bed was cleared, so the mix made with it went too.")
    store.save(project)
    return jsonify({"ok": True, "spot": spot})


# ---------------------------------------------------------------- own voice
@app.route("/api/projects/<pid>/spots/<sid>/voice-upload", methods=["POST"])
def api_voice_upload(pid, sid):
    """A finished read somebody already recorded.

    Neither platform this was specced against forces a synthetic voice, and a
    client who has their own talent has one recording they want used. It lands
    on the same fields a rendered read lands on, so the mix, the checks and the
    customer page cannot tell the two apart — except that its length is
    honestly **not measured** where a rendered one is.
    """
    try:
        project, spot = _spot_or_fail(pid, sid)
        filename, data, mimetype = _read_upload(cap_mb=_BED_CAP_MB)
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))

    ext = _ext_of(filename)
    asset = store.store_asset(project, spot, "vo", data, ext)
    length = _measured(data, filename)
    spot.update({"audio_url": asset["url"], "audio_where": asset["where"],
                 "audio_seconds": length["seconds"],
                 "audio_measured": length["measured"],
                 "audio_provider": "upload",
                 "audio_voice": filename,
                 "recorded_at": store.now()})
    spot["runtime_note"] = (
        length.get("measure_note")
        or f"{length['seconds']}s, measured from the file you uploaded.")
    _drop_mix(spot, "The read changed, so the mix made from the old one went "
                    "with it. Render it again.")
    decorate(project, spot)
    store.save(project)
    _log("voice_uploaded", project=pid, spot=sid,
         client=project.get("client") or "")
    payload = {"ok": True, "spot": spot, "measured": length["measured"]}
    if asset.get("warning"):
        payload["warning"] = asset["warning"]
    return jsonify(payload)


# ------------------------------------------------------------- audio, served
# The mix is rendered in the browser, which means the browser has to *fetch*
# the voice and the bed and decode them. Those live wherever the store put them
# — Cloudinary when it is configured, the persistent disk when it is not — and
# a cross-origin fetch that a CDN declines to allow fails in the one way this
# Hub keeps having to undo: silently, as a button that does nothing.
#
# So both are read back through here, same-origin by construction, and the
# allowlist is the spot's own row: a `ref` names a role and the URL comes from
# what this service already recorded against it. Nothing takes a URL from the
# caller.
@app.route("/api/projects/<pid>/spots/<sid>/audio")
def api_spot_audio(pid, sid):
    """One of this spot's own audio assets, same-origin.

    Never a redirect to the CDN: a redirect lands the browser back on the
    origin whose CORS answer is the thing being worked around.
    """
    try:
        project, spot = _spot_or_fail(pid, sid)
    except LookupError as exc:
        return fail(str(exc), 404)
    role = (request.args.get("ref") or "").strip()
    if role not in _AUDIO_ROLES:
        return fail("Unknown audio reference.", 404)
    asset = spot if role == "vo" else (spot.get(role) or {})
    url = str(asset.get("audio_url") or "")
    if not url:
        return fail("There is no audio recorded for that yet.", 404)

    # Stored on the disk: the name is one this service wrote, and it is served
    # from the same directory /audio/<name> already serves.
    if url.startswith("audio/"):
        path = store.local_audio_path(url[len("audio/"):])
        if not path:
            return fail("That audio is not stored anywhere this can read.", 404)
        return send_file(path, conditional=True)

    if not url.lower().startswith("https://"):
        # Everything Cloudinary hands back is https. Anything else is not a URL
        # this service wrote, whatever it is doing on the row.
        return fail("That audio is not stored anywhere this can read.", 502)
    try:
        upstream = requests.get(url, timeout=_PROXY_TIMEOUT, stream=True)
        upstream.raise_for_status()
        payload = upstream.raw.read(_PROXY_CAP_BYTES + 1, decode_content=True)
    except Exception as exc:                              # noqa: BLE001
        return fail(f"That audio could not be read back: {exc}", 502)
    if len(payload) > _PROXY_CAP_BYTES:
        return fail("That audio is too large to read back through the Hub.", 502)
    return Response(payload, mimetype=upstream.headers.get(
        "Content-Type", "application/octet-stream"))


# ----------------------------------------------------------------------- QC
def _qc_for(project: dict, spot: dict) -> dict:
    """One spot's checks, built from what is actually on it."""
    spec, error = _need_spec()
    if not spec:
        return {"available": False, "error": error, "checks": [],
                "status": "not_measured", "blocking": [], "warnings": []}
    # `duration_by_seconds()` rather than `catalog.budget()`, which falls back
    # to the :30 for a length it does not know. `grade()` answers *not
    # measured* for the same input, so reading the two differently here would
    # put a confident word budget beside a verdict that declined to give one.
    budget = radio_spec.duration_by_seconds(spot.get("seconds")) or {}
    grade = spot.get("grade") or catalog.grade(spot.get("script") or "",
                                               spot.get("seconds") or 0)
    mix = spot.get("mix") or {}
    bed = spot.get("bed")
    report = spec.qc(
        script=spot.get("script") or "",
        words=grade.get("words"),
        words_low=budget.get("low"), words_high=budget.get("high"),
        target_seconds=spot.get("seconds"),
        mixed_seconds=mix.get("seconds") if mix.get("measured") else None,
        bed=bed, vo_only=bed is None)
    report["available"] = True
    report["error"] = ""
    report["spot"] = spot.get("id")
    return report


@app.route("/api/projects/<pid>/qc")
def api_qc(pid):
    """Every spot's checks, or one. Reports; refuses nothing."""
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    only = (request.args.get("spot") or "").strip()
    spots = [s for s in (project.get("spots") or [])
             if not only or s.get("id") == only]
    return jsonify({"ok": True,
                    "reports": {s["id"]: _qc_for(project, s) for s in spots}})


# ------------------------------------------------------------------ the mix
@app.route("/api/projects/<pid>/spots/<sid>/mix", methods=["POST"])
def api_mix(pid, sid):
    """File the mix the browser rendered.

    What arrives is a WAV, and that is what makes this honest: the length is
    read off its own header here, so the number filed against the spot was
    measured from the bytes we stored rather than reported by the page that
    made them.

    The checks run before anything is stored. A blocking finding answers **409
    with the report** rather than filing quietly — and an override is
    available, recorded against a name with a reason required, because a check
    that refuses the correct thing is a check somebody switches off, and
    switching this one off costs the call-to-action check with it.
    """
    try:
        project, spot = _spot_or_fail(pid, sid)
        filename, data, _mime = _read_upload(
            kinds={"audio/wav", "audio/x-wav", "audio/wave",
                   "application/octet-stream"}, cap_mb=_MIX_CAP_MB)
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))

    spec, error = _need_spec()
    if not spec:
        return fail(error, 503)
    seconds = spec.wav_seconds(data)
    if seconds is None:
        return fail("That file is not a WAV this can read, so its length "
                    "cannot be measured. The mix step renders a WAV — re-run "
                    "it.")
    if not spot.get("audio_url"):
        return fail("Record or upload the read for this spot before mixing it.")

    # The trademark scan runs here too, and it is not a duplicate of the one on
    # the record route: a script can be hand-edited after a read was recorded,
    # and this is the file a client is actually sent.
    check = phrases.scan(spot.get("script") or "", banned_terms(project),
                         spot.get("daypart") or "",
                         spot.get("outcome") or "neutral")
    if not check["clean"]:
        hits = ", ".join(h["term"] for h in check["blocked"])
        return fail(f"This script still says: {hits}. That's a trademark — it "
                    "cannot be filed, with or without a reason.", 422)

    level = (request.form.get("level") or "").strip()
    pair = spec.ducked_db(level)
    probe = dict(spot, mix={"seconds": seconds, "measured": True})
    report = _qc_for(project, probe)
    override = str(request.form.get("override") or "").strip().lower() in (
        "1", "true", "yes")
    reason = str(request.form.get("override_reason") or "").strip()
    if report["blocking"] and not override:
        return jsonify({"ok": False, "blocked": True, "qc": report,
                        "error": "This mix has findings that stop it being "
                                 "filed. Fix them, or file it with a reason."}), 409
    if report["blocking"] and override and not reason:
        return fail("Say why this is being filed with findings outstanding. "
                    "An override nobody can explain later is not a record.")

    asset = store.store_asset(project, spot, "mix", data, "wav")
    spot["mix"] = {"audio_url": asset["url"], "audio_where": asset["where"],
                   "seconds": seconds, "measured": True, "bytes": len(data),
                   "filename": filename, "format": spec.MIX_FORMAT,
                   "level": level or spec.bed_levels().get("reference", ""),
                   "bed_db": pair["bed"], "ducked_db": pair["ducked"],
                   "level_known": pair["known"],
                   "bed": (spot.get("bed") or {}).get("kind") or "",
                   "qc_status": report["status"], "qc": report,
                   "override": bool(report["blocking"] and override),
                   "override_reason": reason if report["blocking"] and override else "",
                   "override_by": actor_name() if report["blocking"] and override else "",
                   "at": store.now()}
    spot.pop("mix_note", None)
    store.save(project)
    _log("spot_mixed", project=pid, spot=sid, qc=report["status"],
         override=spot["mix"]["override"], client=project.get("client") or "")
    payload = {"ok": True, "spot": spot, "mix": spot["mix"], "qc": report}
    if asset.get("warning"):
        payload["warning"] = asset["warning"]
    return jsonify(payload)


# =====================================================================
# Sharing
# =====================================================================
def share_url(project: dict) -> str:
    return radio_share.share_url(MOUNT, (project.get("share") or {}).get("token"))


@app.route("/api/projects/<pid>/share", methods=["POST"])
def api_share(pid):
    project = store.load(pid)
    if not project:
        return fail("No project with that id.", 404)
    body = request.get_json(silent=True) or {}
    share = radio_share.update_share(project.get("share") or {}, body)
    project["share"] = share
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
    valid = radio_share.validate_feedback(body)
    if not valid["ok"]:
        return fail(valid["error"])
    action, name, comment = valid["action"], valid["name"], valid["comment"]

    sid = valid["spot_id"]
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
    feedback = project.setdefault("feedback", [])
    if targets:
        for spot in targets:
            radio_share.record_decision(spot, feedback, name=name,
                                        action=action, comment=comment,
                                        spot_id=spot["id"])
    else:
        radio_share.record_decision(None, feedback, name=name, action=action,
                                    comment=comment)
    project["feedback"] = feedback[-500:]
    store.save(project)
    _log("customer_feedback", project=project["id"], action=action,
         spots=len(targets), by=name[:40])
    radio_share.notify("FAN_RADIO_NOTIFY_URL", {
        "source": "fan-radio", "project": project.get("id"),
        "company": project.get("company"), "client": project.get("client"),
        "by": name, "action": action, "spots": len(targets),
        "comment": comment[:1000], "at": store.now()})
    return jsonify({"ok": True, **public_view(project)})


# The MIME type follows the extension. Every file here was an MP3 until the
# mix existed, so this was hardcoded to audio/mpeg -- which a browser handed a
# WAV under that type may decline to play, silently, on the customer's own
# page. `local_audio_path()` is what decides which extensions exist at all.
_AUDIO_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
               "ogg": "audio/ogg", "webm": "audio/webm"}


@app.route("/audio/<name>")
def audio(name):
    """Local-disk fallback for renders when Cloudinary isn't configured.
    Public because the customer page needs it. A rendered read carries a
    random suffix; a bed and a mix are named for the spot and the role, which
    is deterministic on purpose -- re-composing or re-mixing has to replace
    what is already there, or the bytes a client is sent and the duration
    filed against them disagree. The spot id in front of it is still a random
    token, so neither is enumerable."""
    path = store.local_audio_path(name)
    if not path:
        return fail("Not found.", 404)
    ext = name.rsplit(".", 1)[-1].lower()
    return send_file(path, mimetype=_AUDIO_MIME.get(ext, "audio/mpeg"),
                     conditional=True)


if __name__ == "__main__":                            # standalone dev run
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8091")),
            debug=False)
