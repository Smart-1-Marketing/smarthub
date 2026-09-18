"""Commercial Builder — generated sound effects and music.

    python3 test_commercial_audio.py

Same shape as test_commercial_meter.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database.

## What this file holds

  1. **The Music step generated nothing.** A mood tile, a level slider, and
     no track for the level to duck — so `routes/render.py` read
     `project.music["music_track_url"]` and nothing in this module had ever
     written it. That is the same shape as the voiceover that was generated,
     billed for and thrown away, one product over on the same account.

  2. **A limit is refused by name, never clamped.** ElevenLabs publishes
     0.5-30s for an effect and 3s-10min for a bed. Somebody who asked for 40
     seconds of rain and silently got 30 has been told something different
     from what they asked for, on a file that then goes into a spot —
     `hub/quote_validity.py`'s rule about a quote window, wearing a noise.

  3. **A retry never re-spends, on either worker.** The cache is keyed on the
     content and lives on the shared data disk, because gunicorn runs two of
     them and a module-level dict is a cache that works about half the time —
     which is what `modules/bg_remover` had to undo on the one module whose
     stated design goal was not spending money twice.

  4. **A duration is derived or it is not measured.** Nothing here decodes
     audio. Both endpoints are asked for a constant-bitrate MP3 and the
     length is arithmetic on the byte count; anything else answers None, and
     `music_length_mismatch` renders that as *not measured* rather than as a
     tick over a length nobody checked.

  5. **The meter counts these apart from speech.** ElevenLabs bills the
     character for a voiceover and the generation for these, so a row folded
     into the character total would read as a handful of characters of
     script and the voice estimate would quietly absorb a cost source that is
     not measured in characters at all.

  6. **The effect ducks by the bed's own numbers**, on a track of its own,
     capped to the shot it sits on — so nothing on that track can overlap and
     there is no second gain system to disagree with the first.

  7. **None of it reaches a client**, and every route needs a login.
"""
import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbaudio_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

# Both assigned, never setdefault: a fresh directory is not isolation on its
# own, and an inherited DATABASE_URL refills it with the last run's rows.
os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cbaudio-test-secret"
os.environ["PANEL_PASSWORD"] = "cbaudio-test-password"
# Every provider off. What is worth asserting is what this module does when
# ElevenLabs says nothing at all.
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY",
           "ELEVENLABS_KEY", "HEYGEN_API", "RUNWAY_API_KEY",
           "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


MOUNT = "/tools/commercial-builder"

from hub import quotas                                                    # noqa: E402
from modules.commercial_builder import config as cb_config                # noqa: E402
from modules.commercial_builder.services import (creatomate_service,      # noqa: E402
                                                 qc_service)
from modules.commercial_builder.services import (                          # noqa: E402
    elevenlabs_audio_service as audio)


# ---------------------------------------------------------------------------
section("The published limits are transcribed, and a value outside them is refused")
# ---------------------------------------------------------------------------
# From the official ElevenLabs Agent Skills. Transcribed rather than fetched,
# for the reason hub/creative_specs.py gives about the spec kit: a limit
# pulled live changes what a refusal says with no diff to point at.
check("an effect runs from half a second", cb_config.SOUND_EFFECTS_MIN_DURATION_S, 0.5)
check("...to thirty", cb_config.SOUND_EFFECTS_MAX_DURATION_S, 30.0)
check("the v2 sound model is named", cb_config.SOUND_EFFECTS_MODEL_ID,
      "eleven_text_to_sound_v2")
check("music composes from three seconds", cb_config.MUSIC_MIN_LENGTH_MS, 3_000)
check("...to ten minutes", cb_config.MUSIC_MAX_LENGTH_MS, 600_000)
check("and the current music model is named", cb_config.MUSIC_MODEL_ID, "music_v2")

# Refused by NAME, not clamped. The refusal has to say what the limit is, or
# it is an error message somebody cannot act on.
# Asserted on the REFUSAL rather than on the absence of audio: with no key
# there is never any audio, so `audio_bytes is None` passes just as happily
# against a version that clamps 40 to 30 and generates — which is the shape of
# a vacuous assertion this repo has had to undo more than once.
over = audio.generate_sound_effect("forty seconds of rain", duration_seconds=40)
check("40s is refused rather than quietly becoming 30", bool(over.get("error")), True)
check("...and the refusal names the range", "0.5 and 30" in (over.get("error") or ""), True)
check("...and it is a refusal rather than mock mode answering",
      over.get("_mock"), None)
under = audio.generate_sound_effect("a tick", duration_seconds=0.1)
check("a tenth of a second is refused too", bool(under.get("error")), True)
short = audio.compose_music("a bed", music_length_ms=500)
check("half a second of music is refused", bool(short.get("error")), True)
check("...and says what the range is", "3 seconds" in (short.get("error") or ""), True)
blank = audio.generate_sound_effect("   ")
check("an empty prompt is refused before anything is spent",
      "needs a description" in (blank.get("error") or ""), True)

# A length is DERIVED from the spot, not asked of the caller — so a browser
# cannot request a length this spot does not have.
check("a :30 asks for its own runway less the tail trim",
      cb_config.music_length_ms(30), 30_000 - cb_config.MUSIC_TAIL_TRIM_MS)
check("...and a :05 is still above ElevenLabs' floor",
      cb_config.music_length_ms(5) >= cb_config.MUSIC_MIN_LENGTH_MS, True)
check("a nonsense length falls to the floor rather than raising",
      cb_config.music_length_ms(None), cb_config.MUSIC_MIN_LENGTH_MS)


# ---------------------------------------------------------------------------
section("Mock mode produces no audio and says so")
# ---------------------------------------------------------------------------
# The failure this avoids is a silent placeholder file: a spot that renders
# and is empty, with every screen reporting success — which is what
# approve_render already refuses to file as a delivered commercial.
check("with no key, nothing is live", audio.is_live(), False)
m = audio.generate_sound_effect("cash register")
check("mock mode is marked", m.get("_mock"), True)
check("...and produces no audio at all", m.get("audio_bytes"), None)
check("...and says which variable would fix it", "ELEVENLABS_API" in (m.get("note") or ""), True)
mm = audio.compose_music("warm acoustic bed", 30_000)
check("the same for music", (mm.get("_mock"), mm.get("audio_bytes")), (True, None))


# ---------------------------------------------------------------------------
section("A duration is derived at a known bitrate, or it is not measured")
# ---------------------------------------------------------------------------
# 128 kbps CBR: one second is 16,000 bytes. Nothing decodes audio here, and
# where the answer cannot be derived it is None rather than a number.
check("a second of 128k mp3 measures a second", audio.cbr_seconds(16_000), 1.0)
check("ten seconds measures ten", audio.cbr_seconds(160_000), 10.0)
check("an empty file measures nothing", audio.cbr_seconds(0), None)
check("...as does a bitrate we do not know", audio.cbr_seconds(16_000, 0), None)
check("and rubbish does not raise", audio.cbr_seconds("banana"), None)
check("the format asked for is the one the arithmetic assumes",
      cb_config.AUDIO_OUTPUT_FORMAT.endswith(str(cb_config.AUDIO_OUTPUT_KBPS)), True)


# ---------------------------------------------------------------------------
section("The cache is on the shared disk, keyed on the content, and per client")
# ---------------------------------------------------------------------------
# The trap: a module-level dict is a cache that works on one worker in two, so
# half the retries pay again — and every screen reports success either way.
src = inspect.getsource(audio)
check("the cache reaches the shared data directory",
      "jsonstore.data_dir(" in src, True)
check("...and is not a module-level dict pretending to be one",
      "_results: dict" in src or "_cache: dict" in src, False)

k1 = audio.cache_key("sound_effect", "acme", "cash register", duration=2)
k2 = audio.cache_key("sound_effect", "acme", "Cash Register  ", duration=2)
check("the same prompt is the same key whatever its case", k1, k2)
k3 = audio.cache_key("sound_effect", "acme", "cash register", duration=3)
check("...and a different duration is a different key", k1 == k3, False)
k4 = audio.cache_key("sound_effect", "riverside", "cash register", duration=2)
check("a different client is a different key, so no hit crosses a client",
      k1 == k4, False)

check("nothing is cached before anything is generated", audio.cached(k1), None)
audio.remember(k1, {"url": "https://res.cloudinary.com/x/audio/upload/sfx.mp3",
                    "public_id": "acme/audio/sfx", "seconds": 2.0,
                    "prompt": "cash register"})
hit = audio.cached(k1)
check("a stored take comes back", (hit or {}).get("public_id"), "acme/audio/sfx")
check("...marked as a reuse rather than a fresh generation", (hit or {}).get("cached"), True)
check("and the other client still gets nothing", audio.cached(k4), None)
# It must never raise, whatever it is handed: a cache that can break the tool
# it accelerates is worse than no cache.
audio.remember("", {"url": "x"})
audio.remember(k1, {})
check("a useless write is a no-op rather than an exception", audio.cached("") is None, True)


# ---------------------------------------------------------------------------
section("The meter counts a generation apart from a character of speech")
# ---------------------------------------------------------------------------
check("the recorder exists", hasattr(quotas, "record_audio_generation"), True)
check("and the two kinds are named", sorted(quotas.AUDIO_GENERATION_KINDS),
      ["music", "sound_effect"])

quotas.record_tts("a thirty word script " * 5, module="commercial_builder",
                  model="eleven_multilingual_v2")
quotas.record_audio_generation("sound_effect", module="commercial_builder", seconds=2.5)
quotas.record_audio_generation("music", module="commercial_builder", seconds=29.75)
quotas.record_audio_generation("music", module="commercial_builder", seconds=29.75, ok=False)

est = quotas.elevenlabs_estimate()
check("the voiceover is still counted in characters",
      est["measured"]["characters"], len("a thirty word script " * 5))
check("...and one render, not three", est["measured"]["renders"], 1)
gens = est["audio_generations"]
check("the effect has its own line", gens["sound_effect"]["generations"], 1)
check("...carrying the seconds it produced", gens["sound_effect"]["seconds"], 2.5)
check("the bed has its own line too", gens["music"]["generations"], 1)
check("...and a refusal is kept apart rather than counted as spend",
      gens["music"]["failed"], 1)
check("a refused generation is not in the failed-render count either",
      est["measured"]["failed_renders"], 0)
check("the basis says why these are not in the character total",
      "billed per generation" in est["audio_basis"], True)
check("...and that no per-generation rate is claimed",
      "not measured" in est["audio_basis"], True)
check("the estimate reads as measured once anything was generated",
      est["state"], "measured")

# The marker: a call site the scanner cannot see is a gap nothing can name,
# which is exactly the state HeyGen, Runway and Creatomate were in.
check("the scanner recognizes the new service as an ElevenLabs call site",
      quotas._PROVIDER_MARKERS["elevenlabs"]["calls"](src), True)
check("...and sees that it records",
      any(m in src for m in quotas._PROVIDER_MARKERS["elevenlabs"]["recorded"]), True)
# The path is built rather than written out, and that is not squeamishness:
# `tools/linkcheck.py` reads a URL literal out of any file and duly reported
# this fixture as a broken link — the check reporting the explanation as the
# defect, which is the "prose is not a call site" rule arriving from a third
# direction. Split, it is invisible to linkcheck (that file's own note about a
# concatenated URL) and still exactly the string the marker has to refuse.
_not_a_call = 'PLAYLIST = "/' + 'music"  # our own route, no provider anywhere'
check("a quoted music path with no provider named is not a call site",
      quotas._elevenlabs_calls(_not_a_call), False)
check("nothing anywhere spends ElevenLabs without recording it",
      [r["file"] for r in quotas.untracked_provider_calls(force=True).get("elevenlabs", [])],
      [])


# ---------------------------------------------------------------------------
section("On the timeline: its own track, the bed's own numbers, capped to its shot")
# ---------------------------------------------------------------------------
_project = {"length_seconds": 30, "music": {"level": "Medium"}, "cta": {}, "platform": "ctv"}
_scenes = [
    {"id": 1, "start": 0, "end": 4, "narration": "Spring tune-up time.",
     "asset_url": "https://x/a.mp4", "asset_type": "stock", "asset_meta": {
         "media": "video",
         "sfx": {"url": "https://x/whoosh.mp3", "seconds": 1.5, "prompt": "whoosh"}}},
    {"id": 2, "start": 4, "end": 8, "narration": "",
     "asset_url": "https://x/b.mp4", "asset_type": "stock", "asset_meta": {
         "media": "video",
         "sfx": {"url": "https://x/rain.mp3", "seconds": 30, "prompt": "rain"}}},
    {"id": 3, "start": 8, "end": 12, "narration": "Call today.",
     "asset_url": "https://x/c.mp4", "asset_type": "stock", "asset_meta": {"media": "video"}},
]
source = creatomate_service.build_source(_project, _scenes, "16:9",
                                         voice_track_url="https://x/vo.mp3",
                                         music_track_url="https://x/bed.mp3")
sfx = [e for e in source["elements"] if str(e.get("id", "")).startswith("sfx_")]
check("one element per scene that has an effect, and no more", len(sfx), 2)
check("they are on a track of their own",
      {e["track"] for e in sfx}, {creatomate_service.TRACK_SFX})
check("...which is not the music's", creatomate_service.TRACK_SFX
      == creatomate_service.TRACK_MUSIC, False)
check("each sits at its own scene's start", [e["time"] for e in sfx], [0, 4])

bed, ducked = cb_config.MUSIC_LEVELS["Medium"]
check("an effect under narration ducks to the bed's ducked level",
      sfx[0]["volume"], f"{ducked}dB")
check("...and one with no narration under it sits at the bed level",
      sfx[1]["volume"], f"{bed}dB")
check("a short effect keeps its own length", sfx[0]["duration"], 1.5)
# The cap is what makes "nothing on this track overlaps" true by construction
# rather than by hope: a 30-second effect on a four-second shot would
# otherwise run into the next two scenes.
check("an effect longer than its shot is capped to the shot", sfx[1]["duration"], 4.0)
check("...so no two effects overlap",
      all(sfx[i]["time"] + sfx[i]["duration"] <= sfx[i + 1]["time"] + 0.001
          for i in range(len(sfx) - 1)), True)
check("a scene with no effect gets no element",
      any(e["id"] == "sfx_3" for e in source["elements"]), False)

# One reading of the level pair, shared with QC — two lookups of one table,
# each with its own fallback, is how the panel and the render come to
# disagree about how loud something is.
check("an unknown level falls back and says so",
      cb_config.ducked_db("Thunderous"),
      {"bed": bed, "ducked": ducked, "known": False})
check("...and a real one is known", cb_config.ducked_db("High")["known"], True)
check("the render reads that one function rather than the table directly",
      "ducked_db(music_level)" in inspect.getsource(creatomate_service), True)


# ---------------------------------------------------------------------------
section("Two advisory checks, and neither refuses a render")
# ---------------------------------------------------------------------------
for key in ("sfx_gain_conflict", "music_length_mismatch"):
    check(f"{key} advises rather than blocks", key in qc_service.ADVISORY_CHECKS, True)

# The gain check. Every effect ducks; the question is whether the level
# somebody picked ducks it far enough — judged against the MIDDLE setting,
# read out of the table rather than typed as a number.
loud = qc_service._check_sfx_gain({"music": {"level": "High"}}, _scenes)
check("an effect over narration at High is flagged", loud["passed"], False)
check("...naming the shot it is on", "1" in loud["message"], True)
check("...and the setting it is measured against",
      cb_config.MUSIC_LEVEL_REFERENCE in loud["message"], True)
quiet = qc_service._check_sfx_gain({"music": {"level": "Low"}}, _scenes)
check("the same spot at Low passes", quiet["passed"], True)
none = qc_service._check_sfx_gain({"music": {"level": "Medium"}}, [_scenes[2]])
check("a spot with no effects passes and says so", none["passed"], True)
unknown = qc_service._check_sfx_gain({"music": {"level": "Whatever"}}, _scenes)
check("a level nothing recognizes is its own finding", unknown["passed"], False)
check("...because the render is using a pair nobody picked",
      "not one this tool knows" in unknown["message"], True)

# The length check. Three answers, and the middle one is the point.
want_ms = cb_config.music_length_ms(30)
ok = qc_service._check_music_length({"music": {
    "music_track_url": "https://x/bed.mp3", "music_seconds": want_ms / 1000.0,
    "music_requested_ms": want_ms}})
check("a bed at the length asked for passes", ok["passed"], True)
drift = qc_service._check_music_length({"music": {
    "music_track_url": "https://x/bed.mp3", "music_seconds": 24.0,
    "music_requested_ms": want_ms}})
check("one six seconds short is flagged", drift["passed"], False)
check("...and says the tolerance is ours rather than a published figure",
      "ours, not a published figure" in drift["message"], True)
near = qc_service._check_music_length({"music": {
    "music_track_url": "https://x/bed.mp3",
    "music_seconds": (want_ms / 1000.0) - (cb_config.MUSIC_LENGTH_TOLERANCE_S / 2),
    "music_requested_ms": want_ms}})
check("a bed inside the tolerance passes", near["passed"], True)
# The one worth having: unmeasured must never render as agreement.
unmeasured = qc_service._check_music_length({"music": {
    "music_track_url": "https://x/bed.mp3", "music_seconds": None,
    "music_requested_ms": want_ms}})
check("a length that could not be derived is a finding, not a tick",
      unmeasured["passed"], False)
check("...and says it is not measured rather than not agreed",
      "not measured" in unmeasured["message"], True)
nobed = qc_service._check_music_length({"music": {}})
check("a spot with no generated bed passes", nobed["passed"], True)


# ---------------------------------------------------------------------------
section("Both checks reach the panel, on both screens")
# ---------------------------------------------------------------------------
# A check absent from a screen's label map is skipped SILENTLY by the render
# loop — which is how `scene_assets`, the check that catches an unfinished
# scene, never appeared on the panel it was written for.
for js_file in ("blueprint.js", "preview.js"):
    text = (ROOT / "modules/commercial_builder/static/js" / js_file).read_text(encoding="utf-8")
    block = text[text.index("QC_LABELS = {"):text.index("};", text.index("QC_LABELS = {"))]
    for key in ("sfx_gain_conflict", "music_length_mismatch"):
        check(f"{js_file} labels {key}", f"{key}:" in block, True)


# ---------------------------------------------------------------------------
section("Every mood tile is a prompt that will actually be sent")
# ---------------------------------------------------------------------------
# A tile that fills the box with its own name would be a worse brief than an
# empty box, which at least asks somebody to write one.
missing = [m for m in cb_config.MUSIC_MOODS if not cb_config.music_prompt_starter(m)]
check("no mood is offered without one", missing, [])
check("a mood nothing knows contributes nothing rather than its own name",
      cb_config.music_prompt_starter("Whimsical"), "")
bare = [m for m in cb_config.MUSIC_MOODS
        if cb_config.music_prompt_starter(m).strip().lower() == m.lower()]
check("...and no starter is just the label again", bare, [])


# ---------------------------------------------------------------------------
section("The routes: two options a press, nothing generated by arriving")
# ---------------------------------------------------------------------------
import wsgi                                                              # noqa: E402
from werkzeug.test import Client                                         # noqa: E402
from werkzeug.wrappers import Response                                   # noqa: E402

C = Client(wsgi.application, Response)
C.post("/login", data={"password": "cbaudio-test-password"}, follow_redirects=True)


def J(r):
    try:
        return json.loads(r.data)
    except Exception:
        return {}


cid = J(C.post(f"{MOUNT}/api/clients",
                json={"name": "Audio Co", "industry": "home_services"}))["client"]["id"]
pid = J(C.post(f"{MOUNT}/api/projects", json={
    "client_id": cid, "title": "Audio", "commercial_type": "promo_sale",
    "lengths": [30], "platform": "ctv", "formats": ["16:9"]}))["projects"][0]["id"]
C.put(f"{MOUNT}/api/projects/{pid}/brief", json={"what_advertising": "Spring tune-up"})
con = J(C.post(f"{MOUNT}/api/projects/{pid}/concepts"))
C.post(f"{MOUNT}/api/projects/{pid}/select-concept",
       json={"concept_id": con["concepts"][0]["id"]})
C.post(f"{MOUNT}/api/projects/{pid}/script")
scenes = J(C.get(f"{MOUNT}/api/projects/{pid}/scenes")).get("scenes") or []
check("the script produced scenes to work with", len(scenes) > 0, True)
sid = scenes[0]["id"]

opts = J(C.get(f"{MOUNT}/api/projects/{pid}/audio/options"))
check("the panel is told the length that will be asked for",
      opts.get("length_ms"), cb_config.music_length_ms(30))
check("...and every mood with the prompt behind it",
      len(opts.get("moods") or []), len(cb_config.MUSIC_MOODS))
check("...and the effect limits, so the picker cannot offer what is refused",
      opts.get("sfx_duration"), {"min": 0.5, "max": 30.0})
check("...and reports mock mode rather than drawing a button that works",
      opts.get("live"), False)

sfx_r = J(C.post(f"{MOUNT}/api/projects/{pid}/scenes/{sid}/sound-effect",
                  json={"prompt": "cinematic whoosh"}))
check("the effect route reports live: false with no key", sfx_r.get("live"), False)
check("...and each option carries its OWN reason rather than one for the batch",
      all(o.get("error") for o in sfx_r.get("options") or []), True)
check("...and there are two of them", len(sfx_r.get("options") or []), 2)
blank_r = C.post(f"{MOUNT}/api/projects/{pid}/scenes/{sid}/sound-effect", json={"prompt": ""})
check("an effect with no description is refused before anything is spent",
      blank_r.status_code, 400)

mus_r = J(C.post(f"{MOUNT}/api/projects/{pid}/music/compose",
                  json={"prompt": "upbeat corporate bed"}))
check("the compose route reports live: false", mus_r.get("live"), False)
check("...and composes to the spot's own runway rather than a caller's number",
      mus_r.get("length_ms"), cb_config.music_length_ms(30))

# Attaching is what puts audio into the spot, and `music_track_url` is the key
# routes/render.py reads — the key nothing in this module had ever written.
chosen = J(C.post(f"{MOUNT}/api/projects/{pid}/music/choose",
                   json={"url": "https://res.cloudinary.com/x/video/upload/bed.mp3",
                         "prompt": "upbeat corporate bed", "seconds": 29.75}))
check("choosing writes the key the renderer reads",
      chosen["music"]["music_track_url"],
      "https://res.cloudinary.com/x/video/upload/bed.mp3")
check("...and keeps both numbers, or the length check has nothing to compare",
      (chosen["music"]["music_seconds"], chosen["music"]["music_requested_ms"]),
      (29.75, cb_config.music_length_ms(30)))

# Merged, never assigned. `set_music` has already paid for the version of this
# that replaced the dict: a music save that wiped the voice track and rendered
# a silent commercial with nothing erroring at either end.
C.put(f"{MOUNT}/api/projects/{pid}/music", json={"mood": "Energetic", "level": "Low"})
after = J(C.get(f"{MOUNT}/api/projects/{pid}"))["project"]["music"]
check("saving the mood keeps the bed on the timeline",
      after.get("music_track_url"), "https://res.cloudinary.com/x/video/upload/bed.mp3")
check("...and the mood really was saved", after.get("mood"), "Energetic")

C.post(f"{MOUNT}/api/projects/{pid}/scenes/{sid}/sound-effect/choose",
       json={"url": "https://res.cloudinary.com/x/video/upload/whoosh.mp3",
             "prompt": "cinematic whoosh", "seconds": 1.2})
scene_after = J(C.get(f"{MOUNT}/api/projects/{pid}/scenes"))["scenes"][0]
check("an attached effect rides on the scene's own meta",
      (scene_after["asset_meta"].get("sfx") or {}).get("seconds"), 1.2)
check("...beside the footage rather than over it",
      "media" in scene_after["asset_meta"] or "sfx_options" in scene_after["asset_meta"], True)
C.delete(f"{MOUNT}/api/projects/{pid}/scenes/{sid}/sound-effect")
cleared = J(C.get(f"{MOUNT}/api/projects/{pid}/scenes"))["scenes"][0]
check("removing it takes the effect off and leaves the rest",
      "sfx" in cleared["asset_meta"], False)


# ---------------------------------------------------------------------------
section("A switch that is off says so rather than drawing a button that fails")
# ---------------------------------------------------------------------------
os.environ["MUSIC_GENERATION_ENABLED"] = "0"
check("the switch is read at call time", cb_config.music_generation_enabled(), False)
off = C.post(f"{MOUNT}/api/projects/{pid}/music/compose", json={"prompt": "anything"})
check("...and composing is refused by name", off.status_code, 400)
check("...naming the variable that would turn it back on",
      "MUSIC_GENERATION_ENABLED" in J(off).get("error", ""), True)
check("the panel is told, so it can stop offering it",
      J(C.get(f"{MOUNT}/api/projects/{pid}/audio/options")).get("enabled"), False)
os.environ.pop("MUSIC_GENERATION_ENABLED", None)
check("and back on by default", cb_config.music_generation_enabled(), True)


# ---------------------------------------------------------------------------
section("None of it answers a stranger, and none of it reaches a client")
# ---------------------------------------------------------------------------
anon = Client(wsgi.application, Response)
for path, method in ((f"{MOUNT}/api/projects/{pid}/audio/options", "get"),
                     (f"{MOUNT}/api/projects/{pid}/music/compose", "post"),
                     (f"{MOUNT}/api/projects/{pid}/music/choose", "post"),
                     (f"{MOUNT}/api/projects/{pid}/scenes/{sid}/sound-effect", "post")):
    r = getattr(anon, method)(path, json={})
    check(f"{method.upper()} {path.split('/api/')[1]} needs a login",
          r.status_code in (301, 302, 401, 403), True)

# The client's review page does not extend the staff layout, so none of this
# module's JavaScript reaches it. Asserted rather than trusted: a staff note
# on the page a client signs a spot off on is an internal note in front of a
# customer.
review_tpl = (ROOT / "modules/commercial_builder/templates/commercial_review.html"
              ).read_text(encoding="utf-8")
for needle in ("sound-effect", "music/compose", "voice.js", "blueprint.js"):
    check(f"the client's review page carries no {needle}", needle in review_tpl, False)


# ---------------------------------------------------------------------------
section("The live path, with the provider stubbed")
# ---------------------------------------------------------------------------
# Mock mode exercises the refusals and nothing else, and the branches that
# actually spend money are the ones worth pinning: which endpoint is called,
# what is in the body, and what is done with what comes back. Stubbed rather
# than called, because what is worth asserting is what this module does when
# ElevenLabs answers — the shape test_ghl_forms.py settled on.
import types                                                             # noqa: E402

os.environ["ELEVENLABS_API"] = "stub-key"
_real_requests = audio.requests
_calls = []


class _Resp:
    headers = {"Content-Type": "audio/mpeg"}
    content = b"\x00" * 32_000          # two seconds at 128 kbps

    def raise_for_status(self):
        return None


audio.requests = types.SimpleNamespace(
    post=lambda url, headers=None, json=None, timeout=None:
    (_calls.append((url, json)), _Resp())[1])

check("a key makes it live", audio.is_live(), True)
sfx_live = audio.generate_sound_effect("cash register", duration_seconds=2)
check("the effect goes to the sound-generation endpoint",
      _calls[-1][0].endswith("/v1/sound-generation"), True)
body = _calls[-1][1]
check("...on the v2 sound model", body["model_id"], cb_config.SOUND_EFFECTS_MODEL_ID)
check("...asking for a constant-bitrate mp3, which is what makes a length knowable",
      body["output_format"], cb_config.AUDIO_OUTPUT_FORMAT)
check("...carrying the duration that was asked for", body["duration_seconds"], 2.0)
check("...and the prompt influence", body["prompt_influence"],
      cb_config.SOUND_EFFECTS_DEFAULT_INFLUENCE)
check("two seconds of audio measures two seconds", sfx_live["seconds"], 2.0)
check("...and the bytes come back to be stored", len(sfx_live["audio_bytes"]), 32_000)

# Blank duration is ElevenLabs' own default and must not be sent as null: the
# field's absence is what means "you decide".
audio.generate_sound_effect("a distant thump")
check("no duration sent means the field is left out entirely",
      "duration_seconds" in _calls[-1][1], False)

music_live = audio.compose_music("warm acoustic bed", 30_000)
check("the bed goes to the music endpoint", _calls[-1][0].endswith("/v1/music"), True)
check("...on the current music model", _calls[-1][1]["model_id"], cb_config.MUSIC_MODEL_ID)
check("...at the length it was given", _calls[-1][1]["music_length_ms"], 30_000)
check("and it comes back measured", music_live["seconds"], 2.0)

# The one that matters: a response that is not the format we asked for cannot
# be measured, and answers None rather than a plausible number.
class _Odd(_Resp):
    headers = {"Content-Type": "application/octet-stream"}


audio.requests = types.SimpleNamespace(post=lambda *a, **k: _Odd())
check("a format we did not ask for is not measured",
      audio.generate_sound_effect("x")["seconds"], None)
check("...and the audio is still returned rather than thrown away",
      bool(audio.generate_sound_effect("x")["audio_bytes"]), True)


class _Empty(_Resp):
    content = b""


audio.requests = types.SimpleNamespace(post=lambda *a, **k: _Empty())
check("an empty file is a refusal, not a zero-length effect",
      "empty file" in (audio.generate_sound_effect("x").get("error") or ""), True)


def _boom(*a, **k):
    raise RuntimeError("429 rate limited")


audio.requests = types.SimpleNamespace(post=_boom)
refused = audio.generate_sound_effect("x")
check("a provider refusal carries the provider's own sentence",
      "429 rate limited" in (refused.get("error") or ""), True)
check("...and no audio", refused.get("audio_bytes"), None)

audio.requests = _real_requests
os.environ.pop("ELEVENLABS_API", None)
check("and the key comes back off", audio.is_live(), False)


# ---------------------------------------------------------------------------
section("The narration's length, and the pace that gets it inside the spot")
# ---------------------------------------------------------------------------
# Three things were wrong here and each was invisible from the screen.
#
#   1. The Speed slider did nothing. `generate_voiceover` took `speed`,
#      divided it into a word-count estimate, and sent a payload without it —
#      while both radio builders have always put it in `voice_settings`. A rep
#      dropped the pace and got byte-identical audio.
#   2. "Estimated 34.1s of narration" was the script's word count over 150,
#      reported about a file nobody had looked at, on the number somebody uses
#      to decide whether a read fits a :30.
#   3. And an overrun here does not run long, it gets CUT. The voice element
#      goes on the timeline at `time: 0` with no duration, inside a
#      composition whose length is the spot's, so the end is dropped — and the
#      end is the call to action.
from modules.commercial_builder.services import elevenlabs_service as vo   # noqa: E402
from hub import radio_spec                                                 # noqa: E402

# --- the rate reaches the provider -------------------------------------
check("the read pace is clamped to the window ElevenLabs honors",
      (vo.clamp_speed(1.4), vo.clamp_speed(0.4)), (1.2, 0.7))
check("...which is the same window both radio builders clamp to",
      (vo.SPEED_MIN, vo.SPEED_MAX), (0.7, 1.2))
check("a pace that is not a number reads at 1.0 rather than raising",
      vo.clamp_speed("brisk"), 1.0)
check("and the slider on the page cannot ask for one the provider ignores",
      'id="voice-speed" min="0.7" max="1.2"' in
      (ROOT / "modules/commercial_builder/templates/commercial_voice.html").read_text(),
      True)


class _Sent:
    """One captured request, answering with a CBR MP3 of a chosen length."""

    def __init__(self, seconds=30.0, ctype="audio/mpeg"):
        self.seconds, self.ctype, self.payload, self.params = seconds, ctype, None, None

    def post(self, url, headers=None, params=None, json=None, timeout=None):
        self.payload, self.params = json, params
        nbytes = int(self.seconds * cb_config.AUDIO_OUTPUT_KBPS * 1000 / 8)
        return types.SimpleNamespace(
            content=b"\x00" * nbytes, headers={"Content-Type": self.ctype},
            raise_for_status=lambda: None)


_real_vo_requests = vo.requests
os.environ["ELEVENLABS_API"] = "test-key"
_sent = _Sent(seconds=31.8)
vo.requests = _sent
_take = vo.generate_voiceover("word " * 80, "voice-1", speed=1.08)
check("the pace is actually SENT, in voice_settings",
      _sent.payload["voice_settings"]["speed"], 1.08)
_fast = _Sent()
vo.requests = _fast
vo.generate_voiceover("x", "v", speed=1.4)
check("and one outside the window is sent clamped rather than dropped",
      _fast.payload["voice_settings"]["speed"], 1.2)
vo.requests = _sent

# --- and the length is derived from the file, not the word count -------
check("the take's length comes off the bytes that came back",
      (_take["seconds"], _take["measured"]), (31.8, True))
check("...at the bitrate the request itself named",
      _sent.params["output_format"], cb_config.AUDIO_OUTPUT_FORMAT)
check("and it says where the number came from",
      "byte count" in _take["length_note"], True)
# The word-count guess survives as the fallback and is labelled one. Deleting
# it would leave mock mode with no answer at all, which reads as broken.
check("the words-per-minute guess is still there, beside it",
      _take["duration_estimate"] > 0, True)
_notmp3 = _Sent(seconds=30.0, ctype="application/json")
vo.requests = _notmp3
_odd = vo.generate_voiceover("x", "v")
check("a response that is not the MP3 asked for is NOT measured",
      (_odd["seconds"], _odd["measured"]), (None, False))
check("and it says which type came back instead",
      "application/json" in _odd["length_note"], True)
os.environ.pop("ELEVENLABS_API", None)
_mock = vo.generate_voiceover("word " * 80, "voice-1", speed=1.08)
check("mock mode measures nothing and says the number is the script's",
      (_mock["seconds"], _mock["measured"],
       "words-per-minute" in _mock["length_note"]), (None, False, True))
vo.requests = _real_vo_requests

# --- the rate itself is the shared module's, in its own mode -----------
# A generated read is re-recorded at the pace rather than resampled, so the
# semitones a resample pays are a cost this mode does not have -- and quoting
# one would be inventing a price.
_reread = radio_spec.speed_suggestion(vo_seconds=31.8, target_seconds=30,
                                      lead_in_ms=0, mode="reread")
_resample = radio_spec.speed_suggestion(vo_seconds=31.8, target_seconds=30,
                                        lead_in_ms=0)
check("both modes work the same rate out", _reread["speed"], _resample["speed"])
check("...and it is the rate that lands the read on the spot",
      _reread["lands_seconds"], 30.0)
check("a re-read quotes no pitch shift, because it has none",
      _reread["semitones"], None)
check("and says what it does cost instead",
      "paid take" in _reread["note"], True)
check("a resample still quotes the semitones", _resample["semitones"] is not None, True)
check("and still says resampling is why",
      "resampling" in _resample["note"], True)
check("resample is the default, so neither radio builder changed",
      _resample["mode"], "resample")
# The ceiling is shared for one reason in radio and two here: past 1.15x a
# read sounds hurried either way, and 1.15 also sits inside the 0.7-1.2 the
# provider honors, so the ceiling never becomes a number quietly ignored.
check("the ceiling is inside what ElevenLabs will actually read",
      radio_spec.SPEED_MAX <= vo.SPEED_MAX, True)
check("past it no rate is offered, in either mode",
      radio_spec.speed_suggestion(vo_seconds=40.0, target_seconds=30,
                                  lead_in_ms=0, mode="reread")["speed"], None)

# --- the check that stops it, which is the point -----------------------
# A suggestion with no check behind it stops nothing. `voice_fits` was a word
# count alone: 95 words read briskly can land inside a :30 and 70 read slowly
# can miss it, and the number that decides is the length of the file going on
# the timeline.
def _fits(words=95, **music):
    return qc_service._check_voice_fits(
        {"script": {"word_count": words}, "length_seconds": 30, "music": music})


check("with no take, the word count answers exactly as before",
      _fits()["passed"], False)
_on_target = _fits(words=65)
check("an on-target word count still passes before any take exists",
      _on_target["passed"], True)
check("...and says it is a word count rather than a length",
      "word count rather than a length" in _on_target["message"], True)
_measured_ok = _fits(voice_seconds=29.4, voice_measured=True)
check("a measured take that fits passes on an off-target word count",
      _measured_ok["passed"], True)
check("and the message is the length, with the words beside it",
      ("29.4s" in _measured_ok["message"], "95 words" in _measured_ok["message"]),
      (True, True))
_measured_over = _fits(voice_seconds=33.1, voice_measured=True)
check("a measured take past the spot fails", _measured_over["passed"], False)
check("...and names what an overrun actually costs here",
      "cuts it off" in _measured_over["message"], True)
check("a length nobody measured is not judged as one",
      "33.1" in _fits(voice_seconds=33.1, voice_measured=False)["message"], False)
check("a take read faster says so on the row",
      "1.08x" in _fits(voice_seconds=29.8, voice_measured=True,
                       voice_speed=1.08)["message"], True)
# It stays a fail rather than an advisory: a render that silently drops the
# phone number is the failure the gate is for.
check("and it still refuses the render rather than advising",
      "voice_fits" in qc_service.ADVISORY_CHECKS, False)


# ---------------------------------------------------------------------------
section("Hardening: the bitrate, the cache key, and the scene that gets cut")
# ---------------------------------------------------------------------------
# Three ways the narration work above could still go quietly wrong.

# --- 1. The format and the bitrate cannot disagree ---------------------
# They were two constants that had to agree and nothing checked. Bump the
# format to mp3_44100_192 and leave the number at 128 and every duration in
# this module comes back 1.5x too long -- reported as MEASURED, because the
# arithmetic is on the byte count and the byte count is fine. The bed, the
# voice take and music_length_mismatch all read it, so one wrong number is
# wrong in three places at once.
check("the bitrate is derived from the format rather than typed twice",
      cb_config.AUDIO_OUTPUT_KBPS, cb_config._kbps_of(cb_config.AUDIO_OUTPUT_FORMAT))
check("...so a format change carries its own bitrate with it",
      (cb_config._kbps_of("mp3_44100_192"), cb_config._kbps_of("mp3_22050_32")),
      (192, 32))
# Only an mp3_<rate>_<kbps> shape parses. A pcm format would otherwise hand
# back its SAMPLE rate as a bitrate -- a confident wrong number where 0 is the
# true one, and 0 is what cbr_seconds() already refuses to measure from.
check("a format the arithmetic is not valid for answers 0, not a sample rate",
      [cb_config._kbps_of(f) for f in
       ("pcm_44100", "opus_48000_64", "mp3_44100_xx", "", None)],
      [0, 0, 0, 0, 0])
check("and a zero bitrate is not measured rather than divided by",
      audio.cbr_seconds(100_000, 0), None)

# --- 2. A pace the provider clamps must not buy a second take ----------
# The scene cache is keyed on a fingerprint of the settings. Asking for 1.25
# and 1.30 both read at 1.2 and come back byte-identical, so fingerprinting the
# raw number makes two keys for one take -- a paid regeneration that buys
# nothing, on the cache whose stated job is not spending twice.
from modules.commercial_builder.services import media_state as _ms           # noqa: E402


def _take_key(raw_speed):
    settings = {"stability": 0.5, "style": 0.5, "speed": vo.clamp_speed(raw_speed)}
    return _ms.fingerprint(["sig", "voice-1", settings, None])


check("two paces that clamp to the same rate share one cache key",
      _take_key(1.25), _take_key(1.30))
check("and two that genuinely differ do not",
      _take_key(1.0) == _take_key(1.15), False)
check("the route clamps before it fingerprints, not after",
      "settings[\"speed\"] = elevenlabs_service.clamp_speed" in
      (ROOT / "modules/commercial_builder/routes/voices.py").read_text(), True)

# --- 3. The scene that gets cut ----------------------------------------
# Scenes mode places each read with an explicit `duration` of that scene's own
# span, so a long read is cut at the SCENE boundary, mid-word, while the spot's
# total length is perfectly fine. Nothing was asking: `scene_assets` is about
# the FOOTAGE being shorter than the scene, not the narration being longer.
check("the renderer really does cap each scene's read to its scene",
      '"duration": scene["end"] - scene["start"], "type": "audio"' in
      (ROOT / "modules/commercial_builder/services/creatomate_service.py").read_text(),
      True)


def _scene(n, start, end, seconds=None, measured=True, presenter=False, narration="hi"):
    meta = {}
    if seconds is not None:
        meta["voiceover"] = {"seconds": seconds, "measured": measured}
    if presenter:
        meta["spokesperson_url"] = "https://x/clip.mp4"
    return {"id": n, "start": start, "end": end, "narration": narration,
            "asset_meta": meta}


_mixed = [_scene(1, 0, 4, 5.4), _scene(2, 4, 24, 26.0), _scene(3, 24, 30, 5.0)]
_rows = _ms.scene_voice_fits(_mixed)
check("every narration scene with a measured take is read",
      [r["scene"] for r in _rows], [1, 2, 3])
check("and each says how far over its own scene it runs",
      [r["over"] for r in _rows], [1.4, 6.0, -1.0])
# The binding scene is the one needing the hardest PUSH, not the biggest
# overrun: 1.4s over a 4s scene needs more than 6s over a 20s one, and one pace
# is asked of the whole spot, so the wrong pick leaves a scene still cut.
check("the scene that binds is the one needing the hardest push",
      _ms.worst_scene_voice_fit(_mixed)["scene"], 1)
check("...which is NOT the biggest overrun",
      max(_rows, key=lambda r: r["over"])["scene"], 2)
check("a take nobody measured is not judged as one",
      [r["scene"] for r in _ms.scene_voice_fits([_scene(1, 0, 4, 9.9, measured=False)])],
      [])
check("nor is a presenter scene, whose clip carries the read",
      _ms.scene_voice_fits([_scene(1, 0, 4, 9.9, presenter=True)]), [])
check("a scene with no span is skipped rather than divided by",
      _ms.scene_voice_fits([_scene(1, 5, 5, 3.0)]), [])
check("and nothing over means nothing binds",
      _ms.worst_scene_voice_fit([_scene(1, 0, 6, 5.4)]), None)

_scenes_project = {"script": {"word_count": 65}, "length_seconds": 30,
                   "music": {"voice_mode": "scenes"}}
# The scene tolerance is its own number and deliberately not the bed's. One
# second is 3% of a :30 runway and a QUARTER of a four-second scene, and the
# two readings disagreed once: the per-scene finding flagged an overrun that
# the rate to fix it then called "fits", because the rate was worked out
# against the bed's tolerance.
check("a scene is judged at its own tolerance, not the bed's",
      (_ms.scene_tolerance_s(), cb_config.SCENE_VOICE_TOLERANCE_S), (0.1, 0.1))
check("...which is tighter than the bed's, because the cut is hard",
      _ms.scene_tolerance_s() < cb_config.MUSIC_LENGTH_TOLERANCE_S, True)
check("a trailing breath over is not a finding",
      _ms.worst_scene_voice_fit([_scene(1, 0, 4, 4.05)]), None)
check("but a third of a second on a four-second scene is",
      (_ms.worst_scene_voice_fit([_scene(1, 0, 4, 4.3)]) or {}).get("scene"), 1)
check("and the gate reads that same number rather than the bed's",
      qc_service._check_voice_fits(_scenes_project,
                                   [_scene(1, 0, 4, 4.3)])["passed"], False)
check("a scene carrying more read than scene stops the render",
      qc_service._check_voice_fits(_scenes_project, _mixed)["passed"], False)
check("...and the finding names the scene and both numbers",
      "1 (5.4s in 4.0s)" in
      qc_service._check_voice_fits(_scenes_project, _mixed)["message"], True)
check("scenes that all fit pass", qc_service._check_voice_fits(
      _scenes_project, [_scene(1, 0, 6, 5.4), _scene(2, 6, 12, 5.0)])["passed"], True)
# A spot whose every scene is a presenter has no narration to measure and is
# perfectly shippable -- failing it here would be the gate refusing the correct
# thing, which is the note QR_CODE_RULES carries.
_presenter_only = qc_service._check_voice_fits(
    _scenes_project, [_scene(1, 0, 15, presenter=True, narration=""),
                      _scene(2, 15, 30, presenter=True, narration="")])
check("a presenter-only spot is not refused for having no narration",
      _presenter_only["passed"], True)
check("...and says why rather than reporting a gap",
      "Every scene is a presenter" in _presenter_only["message"], True)
# Narration that exists but has not been generated falls back to the word
# count, exactly as the single-track path does before a take exists.
check("narration not yet generated falls back to the word count",
      "word count rather than a length" in qc_service._check_voice_fits(
          _scenes_project, [_scene(1, 0, 6)])["message"], True)


print("\n" + "-" * 62)
print(f"{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
