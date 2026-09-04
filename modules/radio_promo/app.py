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
and Cloudinary filing. What did not: the studio's own password — auth is the
Hub's, via the wsgi AuthGuard.

**Music beds did not carry over either, and now they have.** This docstring
said for a long time that they could not, "no ffmpeg in the Hub runtime", and
that half is still true — there is no ffmpeg, ffprobe, pydub or numpy here.
What changed is that neither job needs one. A bed is **composed** by ElevenLabs
through `hub/radio_spec.py`, at the spot's own length, so nothing has to be
trimmed to fit; and the voice is mixed over it **in the browser** through the
Web Audio API, which hands back a WAV whose length this module then reads off
its own header. So the length filed against a spot is measured here, from the
bytes we stored, rather than reported by the page that made them.

What is genuinely still absent is **loudness mastering** — no LUFS
normalisation, no limiter, no clipping verdict — because all three need to
decode audio and nothing here can. The mix panel reports a peak measured in the
browser and labels it as such, and `radio_spec.qc()` carries `vo_clarity` as a
row that says *not measured* rather than leaving it off the report entirely.

Also new, and all of it staff-only behind the same AuthGuard: a **:60** beside
the pair (opt-in, because each length is a model call and a slot somebody then
has to record), **upload-your-own** for both the read and the bed, the **QC**
checks, and **variations** — clone the spot, patch the offer, and re-record,
because audio of the previous wording filed under a new name is the wrong file
and it plays perfectly well.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import VERSION, VERSION_DATE
from . import ai, qc, speech, store, voices
from .catalog import (DEFAULT_SLOTS, DURATIONS, SLOT_KEYS, TONES,
                      VOICE_CHARACTERISTICS, duration_by_key,
                      length_warning, slot_budget_line, slots_of,
                      structure_for, tone_by_id)

from hub import script_contents

try:
    from hub import radio_spec
except Exception:                                            # noqa: BLE001
    radio_spec = None

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


def _ext_for(kind: str) -> str:
    """The extension a stored asset carries.

    Asked for by name rather than guessed from the bytes: an extension decides
    what Cloudinary and every player treat the file as, and a rendered mix is a
    WAV where every other audio asset here is an MP3.
    """
    return {"audio": "mp3", "wav": "wav", "image": "png"}.get(kind, "bin")


def upload_asset(data: bytes, folder: str, public_id: str, kind: str,
                 *, overwrite: bool = False) -> dict:
    """Cloudinary when configured, persistent disk when not.

    Audio is stored under Cloudinary's ``video`` resource type. That is normal
    for MP3, not a mistake — ``image`` would make the delivery URL 403.

    ``overwrite`` is for an asset whose public_id names **a slot rather than a
    file**: this project's :30 read, its :30 bed, its :30 mix. Those public_ids
    are deterministic, so re-recording or re-mixing a slot lands on the one that
    is already there — and with overwrite off, Cloudinary keeps the old bytes
    while the store records the new length, so the file a client is sent and the
    duration filed against it disagree. The disk branch below has always
    overwritten, which is the other half of the same inconsistency.
    """
    if cloud_ready():
        try:
            # Through hub.storage. The "video for audio" rule this call site
            # knew about now lives in hub.storage.resource_type_for, so it is
            # applied everywhere rather than only where someone remembered.
            from hub import storage
            ext = _ext_for(kind)
            asset = storage.put("radio_promo", f"{public_id}.{ext}", data,
                                folder=folder,
                                public_id=f"{folder}/{public_id}",
                                overwrite=overwrite)
            return {"url": asset.url, "public_id": asset.public_id,
                    "store": "cloudinary"}
        except Exception as exc:                             # noqa: BLE001
            print("radio_promo cloudinary upload failed:", exc)
    ext = _ext_for(kind)
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
    slots = [dict(slot, budget=slot_budget_line(slot["key"]),
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
    # Which lengths this job writes. Normalised through the catalog rather
    # than stored as typed: a slot key nothing can grade or price would reach
    # the writer as a length with no budget behind it.
    data["slots"] = list(slots_of(data))
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
    if "client" in changes:
        changes["client"] = (changes["client"] or "").strip()
    if "slots" in data:
        changes["slots"] = list(slots_of(data))
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


def _qc(row: dict) -> dict:
    """Every named check for this project's chosen reads.

    `catalog.slots_of()` rather than a second reading of which lengths this
    project writes: two answers to that question is how the store and the
    panel come to disagree about what is being graded.
    """
    return qc.run(row, list(slots_of(row)), required=qc.required_for(row))


def _required_script_gaps(row: dict, script: str, slot: str) -> list[str]:
    """Non-negotiables are checked in the app, not entrusted to a prompt.

    The three content rules are `hub/script_contents.py`, because the
    Commercial Builder has to answer the same question and was answering a
    much easier one -- see that module. Radio has one channel, so nothing is
    `shown`: if the read does not say it, nobody gets it. That shared rule is
    also what stops three false positives this check used to make, each of
    which sent a writer round the loop again: a legal suffix nobody reads
    aloud, a phone number compared on its punctuation rather than its digits,
    and a web address already written the way it is spoken.

    The minimum-read rule stays here. It is about the clock rather than the
    copy, and it is radio's own: a :30 slot is bought and billed by the
    second, so a read that lands well under it is dead air somebody paid for.
    The floor is `min_seconds` -- the one number the prompt also states -- and
    a slot with none is a tag, which is naturally tight and would be refused
    by a floor derived for it.
    """
    result = script_contents.check(qc.facts_for(row), spoken=script,
                                   require=qc.required_for(row))
    gaps = script_contents.gap_labels(result)
    spec = duration_by_key(slot) or {}
    floor = spec.get("min_seconds")
    if floor and speech.estimate_seconds(script) < floor:
        gaps.append(f'a minimum {floor:g}-second {spec.get("label", slot)} read')
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
    slots = slots_of(row)
    try:
        written = ai.write_scripts(row["analysis"], row.get("brand") or {}, row,
                                   tone_id, (data.get("revision_note") or "").strip(),
                                   row.get("scripts"), slots=slots)
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
    if slot not in SLOT_KEYS:
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
    if slot not in SLOT_KEYS:
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
    for slot in slots_of(row):
        if scripts.get(slot, {}).get("script"):
            notes = scripts[slot].get("notes", "")
            scripts[slot] = _decorate(slot, scripts[slot]["script"], prons)
            scripts[slot]["notes"] = notes
    row = store.update(pid, {"pronunciations": prons, "scripts": scripts})
    return jsonify({"ok": True, "project": row})


@app.route("/api/projects/<pid>/script-qc")
def api_script_qc(pid):
    """The script panel, on demand. Cheap: it reads the copy and reaches no
    provider, which is the whole point of running it on the Copy step.

    Its own path, and deliberately not `/qc`. That one is the **mix** panel --
    the bed's source, the loudness, the length measured off the stored WAV --
    and it answers after a render. This one answers before anybody has paid
    for a voice. They are neighbours rather than two readings of one question,
    so they are asked separately; sharing a URL would have made whichever
    registered second the only one anybody could reach.
    """
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    return jsonify({"ok": True, "qc": _qc(row), "labels": qc.CHECK_LABELS})


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
# `POST /api/projects/<pid>/music-beds` used to live here. It saved a bed
# *description* and the builder played three oscillators at a pitch chosen by a
# regex over it, so a bed could be picked, "heard", and filed with silence
# under the voice. `/bed/compose` and `/bed/upload` replace it, and the route
# is gone rather than kept: nothing posted to it any more, and the only thing
# it could still produce is a bed with no audio behind it -- which is exactly
# the state `hub/radio_spec.qc()`'s bed_source check now blocks. Keeping a
# write path whose only product is a state the checks refuse is keeping a way
# to make the mistake.
#
# The rows it wrote are NOT orphaned. A project on disk carries `music_beds`,
# and the Music step offers each of those descriptions as a one-press prompt to
# compose from, so words somebody wrote before this existed are still reachable
# and are now worth something.


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
    if slot not in SLOT_KEYS:
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
                         f"{slot}-{(data.get('voice_name') or 'vo')}", "audio",
                         overwrite=True)

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


# ============================================================================
# Beds, the mix, QC and variations.
#
# This is the half `modules/radio_promo` shipped without. Its own docstring
# says why -- "ffmpeg music beds and loudness mastering (no ffmpeg in the Hub
# runtime)" -- and that is still true: there is no ffmpeg, ffprobe, pydub or
# numpy here. What changed is that neither of the two jobs actually needs one.
#
# A bed is **composed to the spot's own length** by ElevenLabs through
# `hub/radio_spec.py`, so nothing has to be trimmed to fit. And the mix is
# rendered **in the browser** through the Web Audio API, which decodes both
# tracks, ducks the bed under the voice and hands back a WAV -- a WAV states
# its own length in its header, so what we file is measured by us from the
# bytes we stored rather than reported by the page.
#
# What was here before was a bed that generated nothing: `api_music_bed` saved
# a text prompt and the builder synthesized a preview tone in the browser, so a
# rep could pick a bed, hear something, and file a spot with silence under the
# voice. That route still exists and its rows are still read -- a project saved
# before this carries them -- but it is now the *describe* step in front of a
# compose, rather than the whole feature.
# ============================================================================
def _need_spec():
    """`hub/radio_spec`, or a reason. Never raises."""
    if radio_spec is None:
        return None, ("The shared radio rules could not be loaded, so no bed "
                      "level, length or check can be quoted here.")
    return radio_spec, ""


def _beds(row: dict) -> dict:
    """The bed on each slot. Per slot, because a :15 and a :60 need beds of
    their own length -- one bed on the project would be the wrong length for
    every slot but the one it was composed for."""
    return dict(row.get("beds") or {})


def _bed_for(row: dict, slot: str) -> dict | None:
    return _beds(row).get(slot)


def _spot_for(row: dict, slot: str) -> dict | None:
    for spot in (row.get("spots") or []):
        if spot.get("slot") == slot:
            return spot
    return None


def _slot_or_fail(data: dict):
    slot = (data.get("slot") or "").strip()
    if slot not in SLOT_KEYS:
        raise ValueError("Unknown slot.")
    return slot


def _read_upload(field: str = "file", *, kinds: set[str] | None = None,
                 cap_mb: int = 25):
    """One uploaded audio file, or a sentence saying why not.

    The refusals are named individually because they send somebody to different
    places: a file too large is re-exported, a file of the wrong type is
    converted, and an empty one is a failed export upstream.
    """
    upload = request.files.get(field)
    if not upload or not upload.filename:
        raise ValueError("Choose a file to upload.")
    data = upload.read()
    if not data:
        raise ValueError(f"{upload.filename} came through empty.")
    if len(data) > cap_mb * 1024 * 1024:
        raise ValueError(f"{upload.filename} is larger than the {cap_mb} MB limit.")
    allowed = kinds if kinds is not None else {
        "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave",
        "audio/mp4", "audio/aac", "audio/ogg", "audio/webm", "video/webm"}
    if upload.mimetype and upload.mimetype not in allowed:
        raise ValueError(f"{upload.filename} is not a supported audio format.")
    return upload.filename, data, (upload.mimetype or "audio/mpeg")


def _measured(data: bytes, filename: str) -> dict:
    """How long this file is, and whether we actually know.

    A WAV says so in its header and is measured. Anything else is **not
    measured** -- an MP3 somebody uploaded is at a bitrate nobody here chose,
    so the byte-count arithmetic that prices a bed we asked for does not apply
    to it, and a number the browser reported about a file is not a measurement
    of the file we stored.
    """
    spec, _ = _need_spec()
    seconds = spec.wav_seconds(data) if spec else None
    if seconds is not None:
        return {"seconds": seconds, "measured": True}
    return {"seconds": None, "measured": False,
            "measure_note": (f"{filename} is not a WAV, and there is no audio "
                             "decoder in this runtime, so its length is not "
                             "measured. The mix is what gets measured.")}


# ------------------------------------------------------------------- config
@app.route("/api/mix/config")
def api_mix_config():
    """Everything the Music and Mix steps need, decided server-side.

    The browser renders the mix but chooses none of it: the dB pair, the fades
    and the sample rate come from here, so the level a panel shows is the level
    that renders. A second copy of those numbers in JavaScript is how the
    screen and the file come to disagree -- which is the note
    `config.ducked_db()` already carries about the panel and the render.
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
                         "(MUSIC_GENERATION_ENABLED), so the mood tiles fill the "
                         "prompt box but nothing is composed. Upload a bed instead."),
        "error": state["error"] or levels.get("error", ""),
        "moods": spec.bed_moods(),
        "levels": levels["levels"],
        "level_reference": levels["reference"],
        "limits": spec.bed_limits(),
        "mix": spec.mix_defaults(levels["reference"]),
        "durations": DURATIONS,
    })


# --------------------------------------------------------------------- beds
@app.route("/api/projects/<pid>/bed/compose", methods=["POST"])
def api_bed_compose(pid):
    """Compose one real bed, at this slot's own length.

    Billed per generation, so it is a button and never a page load -- and the
    cache, the metering and the refusal that keeps its row are all the shared
    audio service's rather than repeated here.
    """
    try:
        row = need(pid)
        slot = _slot_or_fail(body())
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))
    spec, error = _need_spec()
    if not spec:
        return fail(error, 503)
    if not spec.generation_enabled():
        return fail("Composing is switched off on this deployment. Upload a bed "
                    "instead, or set MUSIC_GENERATION_ENABLED.", 503)

    data = body()
    prompt = str(data.get("prompt") or "").strip()
    mood = str(data.get("mood") or "").strip()
    if not prompt and mood:
        # A mood tile fills the box with the words it will actually send. A
        # mood the table does not carry contributes nothing rather than its own
        # name -- "Whimsical" as the whole brief is worse than an empty box.
        for entry in spec.bed_moods():
            if mood.lower() in (entry["id"], entry["label"].lower()):
                prompt = entry["prompt"]
                break
    if not prompt:
        return fail("Describe the bed, or pick a mood to fill the box in.")

    seconds = (duration_by_key(slot) or {}).get("seconds")
    out = spec.compose_bed(prompt, seconds)
    if out.get("error"):
        return fail(out["error"], 502)
    if out.get("_mock") or not out.get("audio_bytes"):
        # Mock mode produces no audio and says so. Recording it as a bed would
        # file a spot that is silent under the voice, which is exactly what
        # `qc()`'s bed_source check blocks -- so it is refused here instead of
        # written and then blocked later.
        return fail(out.get("note") or "No audio came back, so there is no bed "
                    "to save.", 502)

    asset = upload_asset(out["audio_bytes"], store.cloud_folder(row),
                         f"bed-{slot}", "audio", overwrite=True)
    bed = {"kind": "composed", "prompt": prompt, "mood": mood,
           "audio_url": asset["url"], "public_id": asset["public_id"],
           "store": asset["store"], "slot": slot,
           "seconds": out.get("seconds"),
           "measured": out.get("seconds") is not None,
           "requested_seconds": out.get("requested_seconds"),
           "bytes": out.get("bytes"), "at": store.now()}
    beds = _beds(row)
    beds[slot] = bed
    # A new bed invalidates the mix that was made from the old one: a mix is a
    # statement about the two tracks that went into it, and one left standing
    # over a replaced bed is a file nobody can account for.
    mixes = {k: v for k, v in (row.get("mixes") or {}).items() if k != slot}
    row = store.update(pid, {"beds": beds, "mixes": mixes})
    store.add_version(pid, "bed-compose", {"slot": slot, "prompt": prompt}, actor())
    log("project.bed", project=pid, slot=slot, kind="composed",
        client=row.get("client") or "")
    return jsonify({"ok": True, "project": row, "bed": bed})


@app.route("/api/projects/<pid>/bed/upload", methods=["POST"])
def api_bed_upload(pid):
    """A bed somebody already has. The other half of the same choice.

    Neither self-serve platform this was specced against forces generated
    music, and a client who arrives with a licensed track should not be made to
    compose one.
    """
    try:
        row = need(pid)
        slot = _slot_or_fail(request.form)
        filename, data, mimetype = _read_upload()
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))

    asset = upload_asset(data, store.cloud_folder(row), f"bed-{slot}-own", "audio",
                         overwrite=True)
    length = _measured(data, filename)
    bed = {"kind": "upload", "prompt": "", "filename": filename,
           "mimetype": mimetype, "audio_url": asset["url"],
           "public_id": asset["public_id"], "store": asset["store"],
           "slot": slot, "bytes": len(data), "at": store.now(), **length}
    beds = _beds(row)
    beds[slot] = bed
    mixes = {k: v for k, v in (row.get("mixes") or {}).items() if k != slot}
    row = store.update(pid, {"beds": beds, "mixes": mixes})
    store.add_version(pid, "bed-upload", {"slot": slot, "filename": filename}, actor())
    log("project.bed", project=pid, slot=slot, kind="upload",
        client=row.get("client") or "")
    return jsonify({"ok": True, "project": row, "bed": bed})


@app.route("/api/projects/<pid>/bed/clear", methods=["POST"])
def api_bed_clear(pid):
    """No bed: a straight voice read.

    A real answer rather than an unfinished one -- a sponsor mention and a
    news-style read both ship without music -- so it is a deliberate press and
    `qc()` passes it rather than treating a missing bed as a gap.
    """
    try:
        row = need(pid)
        slot = _slot_or_fail(body())
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))
    beds = {k: v for k, v in _beds(row).items() if k != slot}
    mixes = {k: v for k, v in (row.get("mixes") or {}).items() if k != slot}
    row = store.update(pid, {"beds": beds, "mixes": mixes})
    store.add_version(pid, "bed-clear", {"slot": slot}, actor())
    return jsonify({"ok": True, "project": row})


# ---------------------------------------------------------------- own voice
@app.route("/api/projects/<pid>/voice/upload", methods=["POST"])
def api_voice_upload(pid):
    """A finished read somebody already recorded.

    The spec's own finding: neither platform forces a synthetic voice, and a
    client who has their own talent has one recording they want used. It lands
    on the same `spots` list a rendered read lands on, so the mix, QC and
    filing downstream cannot tell the two apart -- except that its length is
    honestly *not measured* where a rendered one is.
    """
    try:
        row = need(pid)
        slot = _slot_or_fail(request.form)
        filename, data, mimetype = _read_upload()
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))

    asset = upload_asset(data, store.cloud_folder(row), f"{slot}-own-vo", "audio",
                         overwrite=True)
    seconds = (duration_by_key(slot) or {"seconds": 30})["seconds"]
    length = _measured(data, filename)
    script = (row.get("scripts") or {}).get(slot) or {}
    grade = speech.grade_duration(length["seconds"], seconds)

    spots = [sp for sp in (row.get("spots") or []) if sp.get("slot") != slot]
    spots.append({"slot": slot, "seconds": seconds, "provider": "upload",
                  "voice_id": "", "voice_name": filename,
                  "audio_url": asset["url"], "public_id": asset["public_id"],
                  "store": asset["store"],
                  "measured_seconds": length["seconds"],
                  "measured": length["measured"],
                  "measure_note": length.get("measure_note", ""),
                  "grade": grade, "script": script.get("script", ""),
                  "spoken": script.get("spoken", ""),
                  "approved": False, "at": store.now()})
    spots.sort(key=lambda sp: sp["seconds"])
    mixes = {k: v for k, v in (row.get("mixes") or {}).items() if k != slot}
    row = store.update(pid, {"spots": spots, "mixes": mixes, "status": "recorded"})
    store.add_version(pid, "voice-upload", {"slot": slot, "filename": filename},
                      actor())
    log("project.voice_upload", project=pid, slot=slot,
        client=row.get("client") or "")
    return jsonify({"ok": True, "project": row, "grade": grade,
                    "measured": length["measured"]})


# ------------------------------------------------------------- audio, served
# The mix is rendered in the browser, which means the browser has to *fetch*
# the voice and the bed and decode them. Those live wherever `upload_asset`
# put them -- Cloudinary when it is configured, the persistent disk when it is
# not -- and a cross-origin fetch that a CDN declines to allow fails in the one
# way this Hub keeps having to undo: silently, as a button that does nothing.
#
# So both are read back through here, same-origin by construction, and the
# allowlist is the project's own row: a `ref` names a slot and a role, and the
# URL comes from what this service already recorded against it. Nothing takes a
# URL from the caller, which is the rule `assets.generatedImagePath()` in the
# ad builder had to be given after a path from a POST body could lift any
# readable file into a web-served folder.
_AUDIO_REFS = ("vo", "bed", "mix")
_PROXY_CAP_BYTES = 40 * 1024 * 1024
_PROXY_TIMEOUT = (5, 30)


def _recorded_audio(row: dict, role: str, slot: str) -> dict | None:
    """The asset this project recorded for that role and slot, or None."""
    if role == "vo":
        return _spot_for(row, slot)
    if role == "bed":
        return _bed_for(row, slot)
    if role == "mix":
        return (row.get("mixes") or {}).get(slot)
    return None


@app.route("/api/projects/<pid>/audio")
def api_audio(pid):
    """One of this project's own audio assets, same-origin.

    Never a redirect to the CDN: a redirect lands the browser back on the
    origin whose CORS answer is the thing being worked around.
    """
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    ref = (request.args.get("ref") or "").strip()
    role, _, slot = ref.partition(":")
    if role not in _AUDIO_REFS or slot not in SLOT_KEYS:
        return fail("Unknown audio reference.", 404)
    asset = _recorded_audio(row, role, slot)
    url = str((asset or {}).get("audio_url") or "")
    if not url:
        return fail("There is no audio recorded for that yet.", 404)

    # Stored on the disk: the name is one this service wrote, and it is served
    # from the same directory `/file/<name>` already serves.
    prefix = f"{MOUNT}/file/"
    if url.startswith(prefix):
        from flask import send_from_directory
        name = url[len(prefix):]
        if "/" in name or "\\" in name or name.startswith("."):
            return fail("Unknown audio reference.", 404)
        return send_from_directory(_local_dir(), name)

    if not url.lower().startswith("https://"):
        # Everything Cloudinary hands back is https. Anything else is not a
        # URL this service wrote, whatever it is doing on the row.
        return fail("That audio is not stored anywhere this can read.", 502)
    try:
        upstream = requests.get(url, timeout=_PROXY_TIMEOUT, stream=True)
        upstream.raise_for_status()
        payload = upstream.raw.read(_PROXY_CAP_BYTES + 1, decode_content=True)
    except Exception as exc:                                 # noqa: BLE001
        return fail(f"That audio could not be read back: {exc}", 502)
    if len(payload) > _PROXY_CAP_BYTES:
        return fail("That audio is too large to read back through the Hub.", 502)
    from flask import Response
    return Response(payload, mimetype=upstream.headers.get(
        "Content-Type", "application/octet-stream"))


# ----------------------------------------------------------------------- QC
def _qc_for(row: dict, slot: str) -> dict:
    """This slot's QC report, built from what is actually on the project."""
    spec, error = _need_spec()
    if not spec:
        return {"available": False, "error": error, "checks": [],
                "status": "not_measured", "blocking": [], "warnings": []}
    script = (row.get("scripts") or {}).get(slot) or {}
    budget = duration_by_key(slot) or {}
    mix = (row.get("mixes") or {}).get(slot) or {}
    bed = _bed_for(row, slot)
    report = spec.qc(
        script=script.get("script", ""),
        words=script.get("word_count"),
        words_low=budget.get("low"), words_high=budget.get("high"),
        target_seconds=budget.get("seconds"),
        mixed_seconds=mix.get("seconds") if mix.get("measured") else None,
        bed=bed, vo_only=bool(row.get("vo_only")) or bed is None)
    report["available"] = True
    report["error"] = ""
    report["slot"] = slot
    return report


@app.route("/api/projects/<pid>/qc")
def api_qc(pid):
    """Every slot's checks, or one. Reports; refuses nothing."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    only = (request.args.get("slot") or "").strip()
    slots = (only,) if only in SLOT_KEYS else slots_of(row)
    return jsonify({"ok": True, "reports": {s: _qc_for(row, s) for s in slots}})


# ---------------------------------------------------------------- the mix
@app.route("/api/projects/<pid>/mix", methods=["POST"])
def api_mix(pid):
    """File the mix the browser rendered.

    What arrives is a WAV, and that is what makes this honest: the length is
    read off its own header here, so the number filed against the spot was
    measured from the bytes we stored rather than reported by the page that
    made them.

    QC runs before anything is stored. A blocking finding answers **409 with
    the report** rather than filing quietly -- and an override is available,
    recorded against a name, because a check that refuses the correct thing is
    a check somebody switches off, and switching this one off costs the call-to
    -action check with it.
    """
    try:
        row = need(pid)
        slot = _slot_or_fail(request.form)
        filename, data, _mimetype = _read_upload(
            kinds={"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"},
            cap_mb=40)
    except LookupError as exc:
        return fail(str(exc), 404)
    except ValueError as exc:
        return fail(str(exc))

    spec, error = _need_spec()
    if not spec:
        return fail(error, 503)
    seconds = spec.wav_seconds(data)
    if seconds is None:
        return fail("That file is not a WAV this can read, so its length cannot "
                    "be measured. The mix step renders a WAV — re-run it.")

    spot = _spot_for(row, slot)
    if not spot or not spot.get("audio_url"):
        return fail("Record or upload the voice for that slot before mixing it.")

    level = (request.form.get("level") or "").strip()
    pair = spec.ducked_db(level)
    report = _qc_for(dict(row, mixes=dict(row.get("mixes") or {},
                                          **{slot: {"seconds": seconds,
                                                    "measured": True}})), slot)
    override = str(request.form.get("override") or "").strip().lower() in ("1", "true", "yes")
    reason = str(request.form.get("override_reason") or "").strip()
    if report["blocking"] and not override:
        return jsonify({"ok": False, "blocked": True, "qc": report,
                        "error": "This mix has findings that stop it being "
                                 "filed. Fix them, or file it with a reason."}), 409
    if report["blocking"] and override and not reason:
        return fail("Say why this is being filed with findings outstanding. "
                    "An override nobody can explain later is not a record.")

    asset = upload_asset(data, store.cloud_folder(row), f"mix-{slot}", "wav",
                         overwrite=True)
    mix = {"slot": slot, "audio_url": asset["url"], "public_id": asset["public_id"],
           "store": asset["store"], "seconds": seconds, "measured": True,
           "bytes": len(data), "filename": filename, "format": spec.MIX_FORMAT,
           "level": level or spec.bed_levels().get("reference", ""),
           "bed_db": pair["bed"], "ducked_db": pair["ducked"],
           "level_known": pair["known"],
           "bed": (_bed_for(row, slot) or {}).get("kind") or "",
           "qc_status": report["status"], "qc": report,
           "override": bool(report["blocking"] and override),
           "override_reason": reason if report["blocking"] and override else "",
           "override_by": actor() if report["blocking"] and override else "",
           "at": store.now()}
    mixes = dict(row.get("mixes") or {})
    mixes[slot] = mix
    row = store.update(pid, {"mixes": mixes, "status": "mixed"})
    store.add_version(pid, "mix", {"slot": slot, "seconds": seconds,
                                   "qc_status": report["status"],
                                   "override": mix["override"],
                                   "override_reason": mix["override_reason"]}, actor())
    log("project.mix", project=pid, slot=slot, qc=report["status"],
        override=mix["override"], client=row.get("client") or "")
    return jsonify({"ok": True, "project": row, "mix": mix, "qc": report})


# ---------------------------------------------------------------- variations
# What carries across and what does not, written down once. A variation exists
# to run the same spot with a new offer, a new location or a new voice, so the
# intake, the brief, the tone and the pronunciations come with it.
#
# The audio deliberately does not. Cloning a rendered read and a finished mix
# onto a project whose offer is about to change files last month's words under
# this month's name -- silently, since both play perfectly well. So a variation
# arrives with its scripts and its bed choices and nothing recorded, and the
# lineage is on both rows.
_VARIATION_CARRIES = (
    "client", "spec", "company", "project_name", "team_member", "home_url",
    "landing_url", "include_phone", "phone", "promotion", "disclaimer",
    "brand", "pronunciations", "tone_id", "analysis", "scripts", "slots",
    "voice_want", "vo_only",
)
# There is deliberately no denylist beside that allowlist. One was written --
# spots, mixes, beds, versions, banner, share, feedback, pushed -- and every
# key in it was already refused by `store.create()`, which builds its row from
# named fields and carries nothing else. A second guard that cannot fire reads
# as the mechanism and is not one, which is the shape this repo counts six of;
# `test_radio_ads.py` asserts the guard that actually holds instead.


@app.route("/api/projects/<pid>/variations", methods=["POST"])
def api_variation(pid):
    """Clone this spot and patch what changed."""
    try:
        row = need(pid)
    except LookupError as exc:
        return fail(str(exc), 404)
    data = body()
    patch = data.get("patch") if isinstance(data.get("patch"), dict) else {}

    fields = {k: row.get(k) for k in _VARIATION_CARRIES if row.get(k) is not None}
    allowed = ("company", "project_name", "home_url", "landing_url",
               "include_phone", "phone", "promotion", "disclaimer", "tone_id",
               "client", "slots")
    fields.update({k: patch[k] for k in allowed if k in patch})
    fields["slots"] = list(slots_of(fields))
    name = str(data.get("name") or "").strip()
    fields["project_name"] = name or f'{row.get("project_name") or row.get("company") or "Spot"} — variation'
    fields["variation_of"] = pid

    clone = store.create(fields)
    # A script whose offer or wording was patched is no longer the script on
    # file. Said rather than silently kept: the words are still there to edit,
    # and what a rep has to know is that nothing recorded came across.
    touched = [k for k in ("promotion", "disclaimer", "tone_id", "landing_url",
                           "phone", "company") if k in patch]
    kids = list(row.get("variations") or [])
    kids.append({"id": clone["id"], "name": fields["project_name"],
                 "at": store.now(), "patched": touched})
    store.update(pid, {"variations": kids})
    store.add_version(pid, "variation", {"child": clone["id"],
                                         "patched": touched}, actor())
    log("project.variation", project=clone["id"], parent=pid,
        client=clone.get("client") or "")
    return jsonify({"ok": True, "project": clone, "parent": pid,
                    "patched": touched,
                    "note": ("The scripts, brief and tone came across. Nothing "
                             "recorded did — re-record the voice and re-mix, "
                             "because audio of the previous wording filed under "
                             "this name is the wrong file.")})


if __name__ == "__main__":
    app.run(port=5061, debug=True)
