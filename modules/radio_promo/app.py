"""Smart 1 Hub — Radio Promo.

Streaming-radio commercials written, cast, recorded and filed from inside the
Hub. Ported from the standalone Smart-1-Marketing/radio-studio app, with the
one change Todd asked for: **every project is either a spec piece or attached
to a client.**

* **Spec** — no client on the record. Written to win business: pick a company,
  point it at their site, and produce a real :15/:30 pair with audio to play in
  the pitch. Files under ``smart1-radio-promo/spec/…``.
* **Attached** — a client from the Hub's own registry (Knack clients, website
  records and house URLs, the same picker the SEO tools use). Files under the
  client slug, shows on the client's library, and can be pushed to GoHighLevel.

A spec project can be attached to a client later without losing a thing, which
is the normal path: spec spot wins the account, spot becomes theirs.

What carried over from the studio: the fifteen tones, the brief-from-the-site
read, the matched :15/:30 pair written to the clock, the pronunciation pass,
ElevenLabs casting and rendering, runtime checking against the slot with a
one-button tighten, companion banner copy and artwork, full version history,
and Cloudinary filing. What did not: ffmpeg music beds and loudness mastering
(no ffmpeg in the Hub runtime), and the studio's own password — auth is the
Hub's, via the wsgi AuthGuard.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import VERSION, VERSION_DATE
from . import ai, qc, speech, store, voices
from .catalog import (DEFAULT_SLOTS, DURATIONS, TONES, VOICE_CHARACTERISTICS,
                      budget_line, duration_by_key, length_warning,
                      normalize_slots, structure_for, tone_by_id)

from hub import script_contents

try:
    from hub import audit as hub_audit
except Exception:                                            # noqa: BLE001
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config.update(JSON_SORT_KEYS=False)

MOUNT = "/tools/radio-promo"

_CLOUD_URL = (os.environ.get("CLOUDINARY_URL") or "").strip()
_CLOUD_NAME = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
_CLOUD_KEY = (os.environ.get("CLOUDINARY_API_KEY") or "").strip()
_CLOUD_SECRET = (os.environ.get("CLOUDINARY_API_SECRET") or "").strip()

try:
    import cloudinary
    import cloudinary.uploader
    if _CLOUD_URL.startswith("cloudinary://"):
        cloudinary.config(secure=True)                        # reads CLOUDINARY_URL
    elif _CLOUD_NAME and _CLOUD_KEY and _CLOUD_SECRET:
        cloudinary.config(cloud_name=_CLOUD_NAME, api_key=_CLOUD_KEY,
                          api_secret=_CLOUD_SECRET, secure=True)
except Exception:                                            # noqa: BLE001
    cloudinary = None


def cloud_ready() -> bool:
    return bool(cloudinary) and bool(_CLOUD_URL or (_CLOUD_NAME and _CLOUD_KEY and _CLOUD_SECRET))


# ------------------------------------------------------------------ helpers
def actor() -> str:
    user = request.environ.get("s1hub.user")
    if isinstance(user, dict):
        return user.get("email") or user.get("name") or "Team"
    return str(user or "Team")


def log(event: str, **extra):
    if hub_audit is not None:
        try:
            hub_audit.log("radio_promo", event, actor=actor(), **extra)
        except Exception:                                    # noqa: BLE001
            pass


def fail(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


def body() -> dict:
    return request.get_json(silent=True) or {}


def need(project_id: str):
    row = store.get(project_id)
    if not row:
        raise LookupError("That project no longer exists.")
    return row


def _local_dir() -> str:
    path = os.path.join(store.data_dir(), "files")
    os.makedirs(path, exist_ok=True)
    return path


def upload_asset(data: bytes, folder: str, public_id: str, kind: str) -> dict:
    """Cloudinary when configured, persistent disk when not.

    Audio is stored under Cloudinary's ``video`` resource type. That is normal
    for MP3, not a mistake — ``image`` would make the delivery URL 403.
    """
    if cloud_ready():
        try:
            # Through hub.storage. The "video for audio" rule this call site
            # knew about now lives in hub.storage.resource_type_for, so it is
            # applied everywhere rather than only where someone remembered.
            from hub import storage
            ext = "mp3" if kind == "audio" else "png"
            asset = storage.put("radio_promo", f"{public_id}.{ext}", data,
                                folder=folder,
                                public_id=f"{folder}/{public_id}",
                                overwrite=False)
            return {"url": asset.url, "public_id": asset.public_id,
                    "store": "cloudinary"}
        except Exception as exc:                             # noqa: BLE001
            print("radio_promo cloudinary upload failed:", exc)
    ext = "mp3" if kind == "audio" else "png"
    name = f"{store.slugify(folder + '-' + public_id, 'asset')}.{ext}"
    with open(os.path.join(_local_dir(), name), "wb") as fh:
        fh.write(data)
    return {"url": f"{MOUNT}/file/{name}", "public_id": name, "store": "disk"}


# -------------------------------------------------------------------- pages
def _version() -> str:
    try:
        from hub import version as hub_version
        return hub_version.label()
    except Exception:                                        # noqa: BLE001
        return f"v{VERSION} · {VERSION_DATE}"


@app.route("/")
def index():
    slots = [dict(slot, budget=budget_line(slot["key"]),
                  warning=length_warning(slot["key"]),
                  beats=structure_for(slot["key"]))
             for slot in DURATIONS]
    return render_template("index.html", version=_version(), tones=TONES,
                           characteristics=VOICE_CHARACTERISTICS,
                           durations=slots, default_slots=list(DEFAULT_SLOTS),
                           mount=MOUNT)


@app.route("/library")
def library_page():
    return render_template("library.html", version=_version(), mount=MOUNT)


@app.route("/file/<path:name>")
def local_file(name: str):
    from flask import send_from_directory
    return send_from_directory(_local_dir(), name)


@app.route("/health")
def health():
    return jsonify({"ok": True, "module": "radio_promo", "version": VERSION,
                    "ai": ai.ready(), "voices": voices.ready(),
                    "cloudinary": cloud_ready(),
                    "projects": len(store.all_projects())})


# ------------------------------------------------------------------ clients
@app.route("/api/clients")
def api_clients():
    try:
        from hub import clients_registry
    except Exception:                                        # noqa: BLE001
        return jsonify({"ok": True, "clients": []})
    rows = clients_registry.search_clients(request.args.get("q", ""), limit=12)
    return jsonify({"ok": True, "clients": rows})


# ----------------------------------------------------------------- projects
@app.route("/api/projects", methods=["POST"])
def api_create():
    data = body()
    company = (data.get("company") or "").strip()
    client = (data.get("client") or "").strip()
    if not company and not client:
        return fail("Give the spot a business name, or pick a client.")
    if not (data.get("home_url") or data.get("landing_url")):
        return fail("Add a home page or landing-page URL. Every radio script must say it.")
    if data.get("include_phone") and not str(data.get("phone") or "").strip():
        return fail("Add the phone number to include it in every script.")
    data["spec"] = not client
    if client and not company:
        data["company"] = client
    row = store.create(data)
    log("project.create", project=row["id"],
        kind="spec" if row["spec"] else "client", client=row.get("client") or "")
    return jsonify({"ok": True, "project": row})


@app.route("/api/projects/<pid>")
def api_get(pid):
    row = store.get(pid)
    if not row:
        return fail("That project no longer exists.", 404)
    return jsonify({"ok": True, "project": row})


@app.route("/api/projects/<pid>/settings", methods=["POST"])
def api_settings(pid):
    """Edit the intake, including attaching a spec project to a client."""
    try:
        need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    allowed = ("client", "company", "project_name", "team_member", "home_url",
               "landing_url", "include_phone", "phone", "promotion", "disclaimer", "pronunciations", "brand")
    changes = {k: data[k] for k in allowed if k in data}
    if "slots" in data:
        changes["slots"] = normalize_slots(data.get("slots"))
    if "client" in changes:
        changes["client"] = (changes["client"] or "").strip()
    row = store.update(pid, changes)
    if "client" in changes:
        log("project.attach", project=pid, client=changes["client"] or "(spec)")
    return jsonify({"ok": True, "project": row})


@app.route("/api/projects/<pid>/delete", methods=["POST"])
def api_delete(pid):
    ok = store.delete(pid)
    if ok:
        log("project.delete", project=pid)
    return jsonify({"ok": ok})


@app.route("/api/library")
def api_library():
    rows = store.library(request.args.get("q", ""), request.args.get("scope", "all"))
    slim = [{k: r.get(k) for k in ("id", "created_at", "updated_at", "status",
                                   "spec", "client", "company", "project_name",
                                   "team_member", "tone_id")}
            | {"spots": len(r.get("spots") or [])} for r in rows]
    return jsonify({"ok": True, "projects": slim, "count": len(slim)})


# -------------------------------------------------------------------- brief
@app.route("/api/projects/<pid>/analyse", methods=["POST"])
def api_analyse(pid):
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    try:
        analysis = ai.analyse_project(row.get("brand") or {}, row)
    except ai.AIError as exc:
        return fail(str(exc), 502)
    row = store.update(pid, {"analysis": analysis, "status": "briefed"})
    store.add_version(pid, "brief", {"analysis": analysis}, actor())
    log("project.brief", project=pid)
    return jsonify({"ok": True, "project": row})


# ------------------------------------------------------------------ scripts
def _decorate(slot_key: str, script: str, pronunciations: list) -> dict:
    slot = duration_by_key(slot_key) or {"seconds": 30}
    words = speech.count_words(script)
    spoken = speech.normalize_for_speech(script, pronunciations)
    return {"script": script, "word_count": words,
            "estimated_seconds": speech.estimate_seconds(script),
            "target_seconds": slot["seconds"],
            "over_budget": words > slot.get("high", 85),
            "spoken": spoken["spoken"], "changes": spoken["changes"]}


def project_slots(row: dict) -> list:
    """The lengths this project writes.

    Read from the row rather than hardcoded, and normalized on the way out, so
    a project saved before the menu widened writes the pair it always wrote
    and a row carrying a slot this build no longer offers cannot reach the
    prompt. Every route that used to test `in ("fifteen", "thirty")` asks this.
    """
    return normalize_slots(row.get("slots") or DEFAULT_SLOTS)


def _qc(row: dict) -> dict:
    """Every named check for this project's chosen reads."""
    return qc.run(row, project_slots(row), required=qc.required_for(row))


def _required_script_gaps(row: dict, script: str, slot: str) -> list[str]:
    """Non-negotiables are checked in the app, not entrusted to a prompt.

    The three content rules are `hub/script_contents.py` now, because the
    Commercial Builder has to answer the same question and was answering a
    much easier one — see that module. Radio has one channel, so nothing is
    `shown`: if the read does not say it, nobody gets it.

    The minimum-read rule stays here. It is about the clock rather than the
    copy, and it is radio's own: a :30 slot is sold by the second.
    """
    result = script_contents.check(qc.facts_for(row), spoken=script,
                                   require=qc.required_for(row))
    gaps = script_contents.gap_labels(result)
    floor = (duration_by_key(slot) or {}).get("min_seconds")
    if floor and speech.estimate_seconds(script) < floor:
        gaps.append(f"a minimum {floor:g}-second :{(duration_by_key(slot) or {}).get('seconds')} read")
    return gaps


@app.route("/api/projects/<pid>/scripts", methods=["POST"])
def api_scripts(pid):
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    tone_id = (data.get("tone_id") or row.get("tone_id") or "").strip()
    if not tone_by_id(tone_id):
        return fail("Pick a tone first.")
    if not row.get("analysis"):
        return fail("Read the site first — the brief feeds the script.")
    slots = normalize_slots(data.get("slots") or row.get("slots") or DEFAULT_SLOTS)
    try:
        written = ai.write_scripts(row["analysis"], row.get("brand") or {}, row,
                                   tone_id, (data.get("revision_note") or "").strip(),
                                   row.get("scripts"), slots)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    prons = row.get("pronunciations") or []
    scripts = {"hook": written.get("hook", "")}
    for key in slots:
        part = written.get(key) or {}
        script = str(part.get("script") or "").strip()
        gaps = _required_script_gaps(row, script, key)
        if gaps:
            return fail(f"The {key} script is missing " + ", ".join(gaps) + ". Please rewrite it.", 422)
        scripts[key] = _decorate(key, script, prons)
        scripts[key]["notes"] = part.get("notes", "")
    row = store.update(pid, {"scripts": scripts, "tone_id": tone_id,
                             "slots": slots, "status": "scripted"})
    store.add_version(pid, "revision" if data.get("revision_note") else "draft",
                      {"tone_id": tone_id, "scripts": scripts,
                       "note": data.get("revision_note", "")}, actor())
    log("project.scripts", project=pid, tone=tone_id)
    return jsonify({"ok": True, "project": row, "qc": _qc(row)})


@app.route("/api/projects/<pid>/script/edit", methods=["POST"])
def api_script_edit(pid):
    """Hand edit. A one-word change should not need a full regenerate."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    slot = data.get("slot")
    if slot not in project_slots(row):
        return fail("Unknown slot.")
    scripts = dict(row.get("scripts") or {})
    if not scripts.get(slot):
        return fail("There's no script in that slot yet.")
    notes = scripts[slot].get("notes", "")
    text = str(data.get("script") or "").strip()
    gaps = _required_script_gaps(row, text, slot)
    if gaps:
        return fail("Every script must include " + ", ".join(gaps) + ".", 422)
    scripts[slot] = _decorate(slot, text,
                              row.get("pronunciations") or [])
    scripts[slot]["notes"] = notes
    row = store.update(pid, {"scripts": scripts})
    store.add_version(pid, "hand-edit", {"slot": slot, "scripts": scripts}, actor())
    return jsonify({"ok": True, "project": row, "qc": _qc(row)})


@app.route("/api/projects/<pid>/tighten", methods=["POST"])
def api_tighten(pid):
    """Cut the read to fit the slot. Never trims audio — rewrites shorter."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    slot = data.get("slot")
    if slot not in project_slots(row):
        return fail("Unknown slot.")
    scripts = dict(row.get("scripts") or {})
    current = scripts.get(slot) or {}
    if not current.get("script"):
        return fail("There's no script in that slot yet.")
    trim = int(data.get("trim_words") or 0)
    if trim <= 0:
        slot_def = duration_by_key(slot) or {"high": 85}
        trim = max(1, current.get("word_count", 0) - slot_def["high"])
    seconds = (duration_by_key(slot) or {"seconds": 30})["seconds"]
    try:
        out = ai.tighten_script(current["script"], seconds, trim,
                                row.get("tone_id", ""), row.get("analysis") or {}, row)
    except ai.AIError as exc:
        return fail(str(exc), 502)
    notes = current.get("notes", "")
    text = str(out.get("script") or "").strip()
    gaps = _required_script_gaps(row, text, slot)
    if gaps:
        return fail("Could not tighten without removing " + ", ".join(gaps) + ".", 422)
    scripts[slot] = _decorate(slot, text,
                              row.get("pronunciations") or [])
    scripts[slot]["notes"] = notes
    scripts[slot]["tighten_note"] = out.get("whatWentAndWhy", "")
    row = store.update(pid, {"scripts": scripts})
    store.add_version(pid, "tighten", {"slot": slot, "trim_words": trim,
                                       "scripts": scripts}, actor())
    return jsonify({"ok": True, "project": row, "qc": _qc(row)})


@app.route("/api/projects/<pid>/pronunciations", methods=["POST"])
def api_pronunciations(pid):
    """Per-project overrides, for a business name TTS keeps mangling."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    prons = [p for p in (body().get("pronunciations") or [])
             if isinstance(p, dict) and str(p.get("from") or "").strip()]
    scripts = dict(row.get("scripts") or {})
    for slot in project_slots(row):
        if scripts.get(slot, {}).get("script"):
            notes = scripts[slot].get("notes", "")
            scripts[slot] = _decorate(slot, scripts[slot]["script"], prons)
            scripts[slot]["notes"] = notes
    row = store.update(pid, {"pronunciations": prons, "scripts": scripts})
    return jsonify({"ok": True, "project": row})


@app.route("/api/projects/<pid>/qc")
def api_qc(pid):
    """The panel, on demand. Cheap: it reads the copy and reaches no provider."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    return jsonify({"ok": True, "qc": _qc(row)})


@app.route("/api/speech/preview", methods=["POST"])
def api_speech_preview():
    data = body()
    out = speech.normalize_for_speech(data.get("text") or "",
                                      data.get("pronunciations") or [])
    return jsonify({"ok": True, **out})


# ------------------------------------------------------------------- voices
@app.route("/api/voices/suggest", methods=["POST"])
def api_voice_suggest():
    data = body()
    row = store.get(data.get("project_id") or "")
    if not row or not row.get("analysis"):
        return fail("Read the site first.")
    try:
        out = ai.suggest_voice_profile(row["analysis"], row,
                                       [row.get("tone_id") or ""])
    except ai.AIError as exc:
        return fail(str(exc), 502)
    return jsonify({"ok": True, **out})


@app.route("/api/voices/match", methods=["POST"])
def api_voice_match():
    data = body()
    want = {k: data.get(k) for k in ("gender", "age", "accent", "energy", "delivery")}
    want["search_terms"] = data.get("search_terms") or []
    try:
        matches = voices.match_voices(want, int(data.get("count") or 3))
    except voices.VoiceError as exc:
        return fail(str(exc), 502)
    pid = data.get("project_id")
    if pid and store.get(pid):
        store.update(pid, {"voice_want": want, "voice_matches": matches})
    return jsonify({"ok": True, "voices": matches})


@app.route("/api/voices/by-id", methods=["POST"])
def api_voice_by_id():
    vid = (body().get("voice_id") or "").strip()
    if not vid:
        return fail("Paste an ElevenLabs voice ID.")
    try:
        return jsonify({"ok": True, "voice": voices.get_voice(vid)})
    except voices.VoiceError as exc:
        return fail(str(exc), 502)


@app.route("/api/voices/clone", methods=["POST"])
def api_voice_clone():
    """Relay authorized audio directly to ElevenLabs; never retain it here."""
    name = (request.form.get("name") or "").strip()
    if not name:
        return fail("Name the voice clone.")
    if request.form.get("authorized") != "true":
        return fail("Confirm that you own this voice or have the speaker's written permission.")
    allowed = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac",
               "audio/ogg", "audio/webm"}
    samples = []
    for upload in request.files.getlist("files"):
        if not upload or not upload.filename:
            continue
        data = upload.read()
        if not data:
            continue
        if len(data) > 25 * 1024 * 1024:
            return fail(f"{upload.filename} is larger than the 25 MB upload limit.")
        if upload.mimetype and upload.mimetype not in allowed:
            return fail(f"{upload.filename} is not a supported audio format.")
        samples.append((upload.filename, data, upload.mimetype or "audio/mpeg"))
    if not samples:
        return fail("Upload at least one MP3, WAV, M4A, AAC, OGG, or WebM recording.")
    try:
        voice = voices.clone_voice(name, samples, request.form.get("description") or "",
                                   request.form.get("remove_noise") == "true")
    except voices.VoiceError as exc:
        return fail(str(exc), 502)
    pid = request.form.get("project_id") or ""
    if store.get(pid):
        store.update(pid, {"voice_matches": [voice]})
        store.add_version(pid, "voice-clone", {"voice_id": voice.get("voice_id"),
                                                "name": voice.get("name")}, actor())
    log("voice.clone", project=pid, voice=voice.get("voice_id"))
    return jsonify({"ok": True, "voice": voice})


# -------------------------------------------------------------------- music
@app.route("/api/projects/<pid>/music-beds", methods=["POST"])
def api_music_bed(pid):
    """Save a described bed. Playback is generated in the browser so a music
    preview is available even when no external music provider is configured."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    prompt = str(body().get("prompt") or "").strip()
    if not prompt:
        return fail("Describe the music bed you want.")
    beds = list(row.get("music_beds") or [])
    beds.insert(0, {"id": "bed_" + str(len(beds) + 1), "prompt": prompt,
                    "created_at": store.now()})
    row = store.update(pid, {"music_beds": beds[:12]})
    store.add_version(pid, "music-bed", {"prompt": prompt}, actor())
    return jsonify({"ok": True, "project": row})


@app.route("/api/voices/account")
def api_voice_account():
    try:
        return jsonify({"ok": True, **voices.account_check()})
    except voices.VoiceError as exc:
        return fail(str(exc), 502)


# ------------------------------------------------------------------ renders
@app.route("/api/projects/<pid>/render", methods=["POST"])
def api_render(pid):
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    slot = data.get("slot")
    if slot not in project_slots(row):
        return fail("Unknown slot.")
    script = (row.get("scripts") or {}).get(slot) or {}
    if not script.get("spoken"):
        return fail("Write the script before recording it.")
    voice_id = (data.get("voice_id") or "").strip()
    if not voice_id:
        return fail("Assign a voice to this spot first.")
    # ElevenLabs bills the character, so a read with a fact wrong in it is
    # money spent twice. Only the checks that cannot be wrong refuse — the
    # timing verdict is an estimate and is reported rather than enforced.
    panel = qc.run_slot(row, slot, required=qc.required_for(row))
    stopped = qc.blocking(panel)
    if stopped:
        return fail("This read is not ready to record: " + " ".join(
            panel["checks"][key]["message"] for key in stopped), 422)

    try:
        out = voices.render_audio(voice_id, script["spoken"],
                                  (row.get("voice_want") or {}).get("energy")
                                  or "conversational")
    except voices.VoiceError as exc:
        return fail(str(exc), 502)

    seconds = (duration_by_key(slot) or {"seconds": 30})["seconds"]
    grade = speech.grade_duration(out.get("seconds"), seconds)
    asset = upload_asset(out["audio"], store.cloud_folder(row),
                         f"{slot}-{(data.get('voice_name') or 'vo')}", "audio")

    spots = [s for s in (row.get("spots") or []) if s.get("slot") != slot]
    spots.append({"slot": slot, "seconds": seconds, "voice_id": voice_id,
                  "voice_name": data.get("voice_name") or "",
                  "audio_url": asset["url"], "public_id": asset["public_id"],
                  "store": asset["store"], "measured_seconds": out.get("seconds"),
                  "measured": out.get("measured", False), "grade": grade,
                  "script": script["script"], "spoken": script["spoken"],
                  "approved": False, "at": store.now()})
    spots.sort(key=lambda s: s["seconds"])
    row = store.update(pid, {"spots": spots, "status": "recorded"})
    store.add_version(pid, "render", {"slot": slot, "voice_id": voice_id,
                                      "grade": grade}, actor())
    log("project.render", project=pid, slot=slot, status=grade.get("status"))
    return jsonify({"ok": True, "project": row, "grade": grade})


@app.route("/api/projects/<pid>/approve-spot", methods=["POST"])
def api_approve_spot(pid):
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    spots = list(row.get("spots") or [])
    for spot in spots:
        if spot.get("slot") == data.get("slot"):
            spot["approved"] = bool(data.get("approved", True))
    approved = [s for s in spots if s.get("approved")]
    row = store.update(pid, {"spots": spots,
                             "status": "approved" if approved else row.get("status")})
    return jsonify({"ok": True, "project": row})


# ------------------------------------------------------------------- banner
@app.route("/api/projects/<pid>/banner", methods=["POST"])
def api_banner(pid):
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    if not row.get("tone_id"):
        return fail("Pick a tone first — the artwork follows it.")
    tone = tone_by_id(row["tone_id"]) or {}
    try:
        copy = ai.banner_copy(row.get("analysis") or {}, row.get("brand") or {},
                              row, row["tone_id"])
    except ai.AIError as exc:
        return fail(str(exc), 502)
    banner = {"headline": copy.get("headline") or tone.get("line", ""),
              "subline": copy.get("subline", ""), "cta": copy.get("cta", "Learn more"),
              "click_url": row.get("landing_url") or row.get("home_url") or "",
              "art_url": "", "note": ""}
    if body().get("with_art", True):
        try:
            art = ai.banner_art(row.get("brand") or {}, row["tone_id"],
                                banner["headline"])
            asset = upload_asset(base64.b64decode(art["b64"]),
                                 store.cloud_folder(row), "companion-banner", "image")
            banner["art_url"] = asset["url"]
        except ai.AIError as exc:
            banner["note"] = f"Copy written; artwork skipped — {exc}"
    row = store.update(pid, {"banner": banner})
    store.add_version(pid, "banner", banner, actor())
    return jsonify({"ok": True, "project": row})


# ---------------------------------------------------------------------- GHL
@app.route("/api/projects/<pid>/push", methods=["POST"])
def api_push(pid):
    """Send the finished playlist to GoHighLevel.

    Spec work is deliberately refused: an opportunity for a business that has
    not asked for one pollutes the pipeline. Attach it to a client first.
    """
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    if row.get("spec"):
        return fail("This is a spec spot. Attach it to a client before pushing "
                    "it to the Suite.")
    hook = (os.environ.get("GHL_OPPORTUNITY_WEBHOOK_URL") or "").strip()
    if not hook:
        return fail("GHL_OPPORTUNITY_WEBHOOK_URL isn't set, so there's nowhere "
                    "to send this.", 503)
    spots = [s for s in (row.get("spots") or []) if s.get("approved")]
    if not spots:
        return fail("Approve at least one spot first.")
    payload = {
        "source": "Smart 1 Hub — Radio Promo",
        "company": row.get("company") or row.get("client"),
        "client": row.get("client"),
        "opportunityName": f"{row.get('company') or row.get('client')} — "
                           f"{row.get('project_name') or 'Radio'}",
        "projectName": row.get("project_name"),
        "teamMember": row.get("team_member"),
        "homeUrl": row.get("home_url"), "landingUrl": row.get("landing_url"),
        "promotionDetails": row.get("promotion"),
        "tone": row.get("tone_id"), "spotCount": len(spots),
        "cloudinaryFolder": store.cloud_folder(row),
        "audioUrls": [s.get("audio_url") for s in spots],
        "bannerUrls": [(row.get("banner") or {}).get("art_url")]
        if (row.get("banner") or {}).get("art_url") else [],
        "commercials": [{"tone": row.get("tone_id"), "length": f"{s['seconds']}s",
                         "script": s.get("script"), "voice": s.get("voice_name"),
                         "audioUrl": s.get("audio_url")} for s in spots],
    }
    import requests
    try:
        res = requests.post(hook, json=payload, timeout=25)
    except requests.RequestException as exc:
        return fail(f"Couldn't reach GoHighLevel ({exc.__class__.__name__}).", 502)
    if res.status_code >= 400:
        return fail(f"GoHighLevel refused the push (HTTP {res.status_code}).", 502)
    row = store.update(pid, {"status": "delivered", "pushed_at": store.now()})
    log("project.push", project=pid, client=row.get("client") or "")
    return jsonify({"ok": True, "project": row})


if __name__ == "__main__":
    app.run(port=5061, debug=True)
