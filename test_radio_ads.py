"""The Radio Ad Creator's second half: beds, the mix, the checks, variations.

`modules/radio_promo` writes, casts and records radio spots and always could.
What it shipped without is everything after the read, and its own docstring
says why: *"What did not [carry over]: ffmpeg music beds and loudness mastering
(no ffmpeg in the Hub runtime)."* That is still true -- there is no ffmpeg,
ffprobe, pydub or numpy here -- so both halves of this are built around it
rather than around a dependency somebody would have to add.

What was actually live before this is worth stating, because it is the shape
this codebase keeps having to undo: the Music step **generated nothing**. It
saved a text prompt and the builder played three oscillators at a pitch chosen
by a regex over that prompt, so a rep could pick a bed, hear a tone, and file a
spot with silence under the voice. Every screen reported success.

So the checks here are mostly about telling *real* from *reported*:

* A bed is composed by ElevenLabs through `hub/radio_spec.py` -- to the spot's
  own length, so nothing is trimmed to fit -- and a mock-mode bed with no audio
  behind it is refused at the door rather than saved and blocked later.
* The mix is rendered in the browser and its length is read back off the
  finished **WAV header** on the server. That round trip is the load-bearing
  one: if the encoder and the probe ever disagree, no mix can be filed at all,
  so the encoder is lifted out of the template and driven in node against
  `radio_spec.wav_seconds()`.
* `not_measured` is never folded into `pass`, and `measured` excludes the one
  check that is deliberately reserved -- counted in, that flag would be False
  on every spot ever built and would therefore say nothing.

Every check below was confirmed red against the defect it was written for
before it was confirmed green.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import struct
import subprocess
import sys
import threading
import time
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Own directory AND own database. Setting only the first is the trap
# `test_jsonstore.py` pins: `key_for()` keys the mirror relative to the data
# root, so a fresh directory in front of an inherited DATABASE_URL is refilled
# with the last run's rows -- an empty disk in front of a full mirror.
_TMP = tempfile.mkdtemp(prefix="radio_ads_")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/radio_ads.db"
for _k in ("ELEVENLABS_API", "ELEVENLABS_API_KEY", "ELEVENLABS_KEY",
           "OPENAI_API_KEY", "CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME",
           "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "PUBLIC_BASE_URL"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import radio_spec                                      # noqa: E402
from modules.radio_promo import app as rp_app                   # noqa: E402
from modules.radio_promo import ai as rp_ai                     # noqa: E402
from modules.radio_promo import catalog as rp_catalog           # noqa: E402
from modules.radio_promo import store as rp_store               # noqa: E402
from hub import jsonstore as _jstore                            # noqa: E402

client = rp_app.app.test_client()
TEMPLATE = (ROOT / "modules" / "radio_promo" / "templates" / "index.html").read_text()
SPEC_SRC = (ROOT / "hub" / "radio_spec.py").read_text()


def wav(seconds, rate=44100, channels=2, bits=16, data_field=None, pad_chunk=False):
    """A WAV with a real header, which is the only thing here that measures."""
    frames = int(rate * seconds)
    size = frames * channels * (bits // 8)
    byte_rate = rate * channels * (bits // 8)
    out = io.BytesIO()
    out.write(b"RIFF" + struct.pack("<I", 36 + size) + b"WAVE")
    if pad_chunk:                       # an odd-length chunk before fmt
        out.write(b"LIST" + struct.pack("<I", 5) + b"INFOx" + b"\x00")
    out.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate,
                                    channels * (bits // 8), bits))
    out.write(b"data" + struct.pack("<I", size if data_field is None else data_field))
    out.write(b"\x00" * size)
    return out.getvalue()


# =====================================================================
section("The music table is read, not restated")
# =====================================================================
# `config.ducked_db()`'s own note says why: two lookups of one table, each with
# its own fallback, is how the panel and the render come to disagree about how
# loud something is. So radio reads Commercial Builder's table, and keeps no
# fallback copy -- where it cannot be read, it refuses rather than inventing a
# dB pair, and says which happened.
from modules.commercial_builder import config as cb_config      # noqa: E402

check("the level table is Commercial Builder's own",
      [(l["label"], l["bed_db"], l["ducked_db"]) for l in radio_spec.bed_levels()["levels"]],
      [(k, v[0], v[1]) for k, v in cb_config.MUSIC_LEVELS.items()])
check("and the reference level travels with it",
      radio_spec.bed_levels()["reference"], cb_config.MUSIC_LEVEL_REFERENCE)
check("a level the table knows is marked known",
      radio_spec.ducked_db(cb_config.MUSIC_LEVEL_REFERENCE)["known"], True)
check("a level it does not know is NOT marked known",
      radio_spec.ducked_db("Whimsical")["known"], False)
check("but still answers with the reference pair rather than nothing",
      radio_spec.ducked_db("Whimsical")["bed"],
      cb_config.MUSIC_LEVELS[cb_config.MUSIC_LEVEL_REFERENCE][0])

# The point of reading rather than copying is that no dB number is written down
# twice. A literal here would pass every test above and drift the day the
# shared table moved.
# Matched as a NUMBER rather than as a substring. A bare `"-9" in source` is
# true of the character class `[A-Za-z0-9-]` in the phone-number pattern, so
# the first version of this check reported a file with no dB literal in it at
# all -- a check with a false positive is one somebody switches off, and it
# would take the real finding with it.
import re as _re                                                # noqa: E402
_dbs = {v for pair in cb_config.MUSIC_LEVELS.values() for v in pair}
_hard = sorted(v for v in _dbs
               if _re.search(rf"(?<![\w.\-]){_re.escape(str(v))}(?![\w.\-])", SPEC_SRC))
check("no dB value from that table is hard-coded in radio_spec", _hard, [])
check("the bed length is the shared arithmetic",
      radio_spec.bed_length_ms(30), cb_config.music_length_ms(30))
check("and a bed is asked for at the spot's own length, less the tail trim",
      radio_spec.bed_length_ms(60), 60_000 - cb_config.MUSIC_TAIL_TRIM_MS)

# Each mood tile carries the words it will actually send -- the
# `characteristics_detail()` rule. A tile promising something other than what
# is composed is a rep picking three wrong beds before listening.
_moods = radio_spec.bed_moods()
check("every mood tile carries a prompt", all(m["prompt"] for m in _moods), True)
check("and it is the prompt the shared table sends",
      [m["prompt"] for m in _moods],
      [cb_config.music_prompt_starter(m["label"]) for m in _moods])
check("a mood with no prompt behind it is not offered at all",
      [m for m in _moods if not m["prompt"]], [])


# =====================================================================
section("A length is measured off the bytes, or it is not measured")
# =====================================================================
check("a stereo WAV measures", radio_spec.wav_seconds(wav(30.0)), 30.0)
check("a mono WAV measures", radio_spec.wav_seconds(wav(15.0, channels=1)), 15.0)
check("a :60 measures", radio_spec.wav_seconds(wav(60.0)), 60.0)
check("a chunk before fmt is walked past, not tripped over",
      radio_spec.wav_seconds(wav(30.0, pad_chunk=True)), 30.0)
check("a streamed WAV declaring no data length reads what is there",
      radio_spec.wav_seconds(wav(10.0, data_field=0)), 10.0)
# An MP3 is the case that matters: nothing here decodes one, so its length is
# not measured -- never a number, and never zero.
check("an MP3 is not measured rather than measured as zero",
      radio_spec.wav_seconds(b"ID3\x04\x00" + b"\x00" * 200), None)
check("a truncated file is not measured", radio_spec.wav_seconds(b"RIFF\x00\x00\x00\x00WAVE"), None)
check("and neither is nothing at all",
      (radio_spec.wav_seconds(b""), radio_spec.wav_seconds(None)), (None, None))


# =====================================================================
section("The browser's encoder and the server's probe agree")
# =====================================================================
# The load-bearing one. The mix is encoded in the browser and measured on the
# server, and those are two implementations of one format: if they disagree,
# every mix is refused as "not a WAV this can read" and the step is dead. So
# the encoder is lifted out of the template and driven in node -- restating it
# here would be a third copy to keep in step, which is the arrangement
# `test_menu_layout.py` uses over hub-crumbs.js.
_start = TEMPLATE.index("function toWav(buf){")
_end = TEMPLATE.index("async function makeMix(")
ENCODER = TEMPLATE[_start:_end]
check("the encoder is where the test expects to find it",
      "v.setUint32(40" in ENCODER and "RIFF" in ENCODER, True)

_harness = """
class Blob{ constructor(parts, opt){ this._bytes = parts[0]; this.type = (opt||{}).type; } }
""" + ENCODER + """
function buf(sec, ch, rate){
  const len = Math.round(sec*rate), data = [];
  for(let c=0;c<ch;c++){ const a = new Float32Array(len);
    for(let k=0;k<len;k++) a[k] = Math.sin(k/40)*0.5; data.push(a); }
  return {numberOfChannels: ch, length: len, sampleRate: rate,
          getChannelData: c => data[c]};
}
const out = [];
for(const [sec,ch,rate] of [[30,2,44100],[15,1,44100],[60,2,44100],[5.5,2,48000]]){
  const b = toWav(buf(sec,ch,rate));
  out.push({sec, ch, rate, type: b.type, bytes: Array.from(new Uint8Array(b._bytes))});
}
process.stdout.write(JSON.stringify(out));
"""
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
    fh.write(_harness)
    _js = fh.name
_run = subprocess.run(["node", _js], capture_output=True, text=True)
os.unlink(_js)
if _run.returncode:
    check("the encoder runs in node", _run.stderr.strip()[:200], "")
else:
    for row in json.loads(_run.stdout):
        data = bytes(row["bytes"])
        check(f"a {row['sec']}s {row['ch']}-channel mix at {row['rate']} round-trips",
              radio_spec.wav_seconds(data), float(row["sec"]))
    check("and it is labeled as a WAV on the way out",
          {r["type"] for r in json.loads(_run.stdout)}, {"audio/wav"})


# =====================================================================
section("What a spot tells a listener to do")
# =====================================================================
for text, kinds, quoted in (
    ("Call (317) 555-0142 today.", ["phone"], "(317) 555-0142"),
    ("Call 317-555-0142 today.", ["phone"], "317-555-0142"),
    ("Call 1-800-FLOWERS now.", ["phone"], "1-800-FLOWERS"),
    ("Visit acmesolar.com before Friday.", ["url"], "acmesolar.com"),
    ("Go to www.acme-solar.net today.", ["url"], "www.acme-solar.net"),
    ("Use promo code SAVE20 at checkout.", ["code"], "promo code SAVE20"),
):
    found = radio_spec.find_cta(text)
    check(f"{kinds[0]}: {text[:34]!r}", found["kinds"], kinds)
    check("  and the evidence is findable in the script",
          quoted in text and found["evidence"][0]["text"] == quoted, True)

# The spoken form, because `speech.py` spells a web address out loud -- so a
# script that has been through that pass carries no dot at all.
check("a web address spelled out loud still counts",
      radio_spec.find_cta("Head to acme dot com now.")["kinds"], ["url"])

# A check that fires on ordinary copy is a check somebody switches off.
for text in ("Our zip code is 46032.", "We opened in 2019 and hired 40 people.",
             "Serving you since 1998.", "The dress code is casual.",
             "Just a nice spot about roofing, no numbers at all."):
    check(f"not a call to action: {text[:38]!r}",
          radio_spec.find_cta(text)["found"], False)

# Where it lands. A number said early and never repeated is the one nobody
# remembers, so position is its own answer -- and it is a share of the words,
# which is the same arithmetic `estimate_seconds()` already does.
_late = "Acme Solar cuts your bill. " * 6 + "Call (317) 555-0142."
_early = "Call (317) 555-0142. " + "Acme Solar cuts your bill. " * 6
check("a closing call to action is in the last fifth",
      radio_spec.cta_share(_late, radio_spec.find_cta(_late)["last_offset"])
      >= radio_spec.CTA_TAIL_SHARE, True)
check("one buried at the top is not",
      radio_spec.cta_share(_early, radio_spec.find_cta(_early)["last_offset"])
      < radio_spec.CTA_TAIL_SHARE, True)
check("and a script with none has no share to report",
      radio_spec.cta_share("no numbers here", None), None)


# =====================================================================
section("A check that could not run is not a check that passed")
# =====================================================================
GOOD = ("Acme Solar cuts your power bill. Ask about the spring rebate. "
        "Visit acmesolar.com or call (317) 555-0142. That's (317) 555-0142.")
BED = {"kind": "composed", "audio_url": "https://res.cloudinary.com/x/bed.mp3"}


def report(**kw):
    base = dict(script=GOOD, words=70, words_low=65, words_high=85,
                target_seconds=30, bed=BED)
    base.update(kw)
    return radio_spec.qc(**base)


_clean = report(mixed_seconds=29.8)
check("a spot with everything in place passes", _clean["status"], "pass")
check("and every check that could run, ran", _clean["measured"], True)
check("the reserved loudness row is still not measured",
      [c["level"] for c in _clean["checks"] if c["id"] == "vo_clarity"], ["not_measured"])
# `measured` excluding the reserved row is the whole point of that flag: counted
# in, it would read False on every spot ever built and mean nothing.
check("but it is not what makes the report unmeasured", _clean["pending"], [])

_nomix = report()
check("with no mix, the length check has not been taken",
      [c["level"] for c in _nomix["checks"] if c["id"] == "length_match"],
      ["not_measured"])
check("which is not a pass", _nomix["measured"], False)
check("and it names what is still outstanding", _nomix["pending"], ["length_match"])

_long = report(mixed_seconds=34.0)
check("a mix 4s over its slot stops filing", _long["blocking"], ["length_match"])
_near = report(mixed_seconds=30.9)
check("one inside the shared tolerance does not", _near["blocking"], [])
check("and the tolerance is the shared one",
      _near["tolerance_s"], cb_config.MUSIC_LENGTH_TOLERANCE_S)

check("a script with no call to action stops filing",
      report(script="Acme Solar is great, think about solar.",
             mixed_seconds=29.8)["blocking"], ["cta_present"])
check("a word count outside the budget is worth a look, not a block",
      report(words=30, mixed_seconds=29.8)["warnings"], ["word_count"])


# =====================================================================
section("The bed under the voice is real audio, or it is named")
# =====================================================================
# The spec this was built from asked for a "bed is licensed" block against a
# catalogue of cleared tracks. There is no such catalogue -- a bed is composed
# on demand or uploaded -- so what protects the client is provenance: real
# audio, with a source recorded against it.
def bed_level(bed, **kw):
    got = radio_spec.qc(script=GOOD, mixed_seconds=29.8, target_seconds=30,
                        bed=bed, **kw)
    return [c["level"] for c in got["checks"] if c["id"] == "bed_source"][0]


check("a composed bed with audio passes", bed_level(BED), "pass")
check("an uploaded track passes too",
      bed_level({"kind": "upload", "audio_url": "https://x/own.wav"}), "pass")
# This is the defect that was live: a bed that was only ever *described*.
check("a bed that was only described stops filing",
      bed_level({"kind": "", "prompt": "upbeat and bright"}), "block")
check("and so does a mock-mode bed that composed nothing",
      bed_level({"kind": "composed", "mock": True}), "block")
check("audio with no record of where it came from stops filing too",
      bed_level({"audio_url": "https://x/mystery.mp3"}), "block")
# No bed is a real answer -- a sponsor read and a news-style read both ship
# without music -- and blocking the correct thing is how a check gets switched
# off, which would cost the call-to-action check with it.
check("no bed at all is a straight read, and passes", bed_level(None), "pass")
check("and so is a deliberate voice-only spot", bed_level(BED, vo_only=True), "pass")


# =====================================================================
section("The :60 is asked for, not assumed")
# =====================================================================
# The property, not the count. This asserted "three lengths" and went red the
# day a :10 was added -- a number in an assertion is a number that drifts, and
# what actually matters is that everything outside the pair is opt-in.
check("the catalog sells the pair and the units either side",
      list(rp_catalog.SLOT_KEYS), ["ten", "fifteen", "thirty", "sixty"])
check("and every length outside the pair has to be asked for",
      sorted(set(rp_catalog.SLOT_KEYS) - set(rp_catalog.slots_of({}))),
      ["sixty", "ten"])
check("a project with no slot list on it is the pair it always was",
      list(rp_catalog.slots_of({})), ["fifteen", "thirty"])
check("a row saved before the field existed reads the same way",
      list(rp_catalog.slots_of({"company": "Acme"})), ["fifteen", "thirty"])
check("a slot key nothing can grade is dropped rather than carried",
      list(rp_catalog.slots_of({"slots": ["ninety", "sixty"]})), ["sixty"])
check("and a list of nothing but junk falls back to the pair",
      list(rp_catalog.slots_of({"slots": ["ninety"]})), ["fifteen", "thirty"])
check("the slots come back in clock order however they were asked for",
      list(rp_catalog.slots_of({"slots": ["sixty", "fifteen", "thirty"]})),
      ["fifteen", "thirty", "sixty"])

# The :60 budget is 140-170 rather than the 150-180 the build spec asked for,
# because at this pace 180 words is a 69-second read: a :60 written to the top
# of that range cannot be recorded inside its own slot.
from modules.radio_promo import speech as rp_speech               # noqa: E402

for slot in rp_catalog.DURATIONS:
    top = slot["high"] / rp_speech.WORDS_PER_SECOND
    check(f'the top of the {slot["label"]} budget is a read that slot can hold',
          top <= slot["seconds"] + 6, True)
check("and the :60's top is inside a minute plus the usual overshoot",
      rp_catalog.duration_by_key("sixty")["high"], 170)

# The minimum-read floor was a literal 25 on the :30 and nothing on the others.
# It is now derived from each slot's own budget floor, and the :30's has to come
# out at exactly the number it replaced -- a generalisation that quietly moves
# the one case that already worked is a regression wearing a refactor.
_floor = lambda k: round(rp_catalog.duration_by_key(k)["low"] / rp_speech.WORDS_PER_SECOND)
check("the :30 floor is still the 25 seconds it always was", _floor("thirty"), 25)
check("the :15 has one now too, from its own budget", _floor("fifteen"), 13)
check("and so does the :60", _floor("sixty"), 54)

# The budgets reach the writer derived, rather than hand-typed into the prompt.
_line = rp_catalog.budget_line()
for slot in rp_catalog.DURATIONS:
    check(f'the writer is told the {slot["label"]} budget',
          f'{slot["low"]}-{slot["high"]} words' in _line, True)

_seen = {}
rp_ai.chat_json = lambda system, user, **kw: (
    _seen.update(system=system, user=user, kw=kw), {"hook": "h"})[1]
rp_ai.write_scripts({"summary": "solar"}, {}, {}, "upbeat",
                    slots=("fifteen", "thirty", "sixty"))
check("the prompt asks for exactly the slots in play",
      [k for k in ("fifteen", "thirty", "sixty") if f'"{k}":' in _seen["user"]],
      ["fifteen", "thirty", "sixty"])
check("and the JSON it asks for is valid JSON",
      json.loads(_seen["user"][_seen["user"].index("{", _seen["user"].index("Return JSON:")):
                               _seen["user"].index("}\nThe script fields") + 1]
                 ) is not None, True)
_three = _seen["kw"]["max_tokens"]
rp_ai.write_scripts({"summary": "solar"}, {}, {}, "upbeat")
check("a pair is written under the ceiling it always was",
      _seen["kw"]["max_tokens"], 1400)
check("and three scripts get more room rather than being truncated",
      _three > _seen["kw"]["max_tokens"], True)
check("the pair's prompt does not mention a slot it is not writing",
      '"sixty":' in _seen["user"], False)


# =====================================================================
section("The template stopped keeping its own copy of the budgets")
# =====================================================================
# renderCopy carried 42 and 85 as literals, which is why a :60 could not be
# added without editing the template too. Both now come off the server's table.
check("the word budgets are read from the slot table",
      "slotHigh(k)" in TEMPLATE and "slotLow(k)" in TEMPLATE, True)
for literal in ("k==='fifteen'?42:85", "k==='fifteen'?35:70",
                "k==='fifteen' ? ':15' : ':30'"):
    check(f"the old literal is gone: {literal[:26]!r}", literal in TEMPLATE, False)
check("and the slot table reaches the browser",
      "window.__DURATIONS" in TEMPLATE, True)

# Every mix setting the browser reads has to be one the server actually sends.
# `lead_in_ms` was read as `(M.lead_in_ms||0)` and served by nothing, so the
# bed's moment at the top of the spot was silently zero -- the `||0` is what
# hid it, and a swept assertion is the only thing that catches the next one.
import re as _re2                                               # noqa: E402
_read = set(_re2.findall(r"\bM\.([a-z_]+)", TEMPLATE))
_served = set(radio_spec.mix_defaults("Medium"))
check("every mix setting the browser reads is one the server sends",
      sorted(_read - _served), [])
check("and the sweep is looking at something",
      sorted(_read)[:4], ["bed_db", "channels", "duck_attack_ms", "duck_release_ms"])

# The bed that generated nothing. Three oscillators at a pitch chosen by a
# regex over the prompt, so a described bed "played" and then shipped as
# silence. Its absence is asserted, because the fix is the removal.
for gone in ("function bedTone(", "function playBed(", "createOscillator"):
    check(f"the synthesised bed preview is gone: {gone!r}", gone in TEMPLATE, False)
check("and a bed is played back from the stored file instead",
      "ref=bed:" in TEMPLATE, True)

# The write route that produced those described-only beds is gone rather than
# kept: nothing posted to it any more, and the only state it could still create
# is the one bed_source blocks. Keeping a write path whose only product is a
# state the checks refuse is keeping a way to make the mistake.
check("the described-bed write route is gone",
      any("music-beds" in str(r) for r in rp_app.app.url_map.iter_rules()), False)
# But the rows it wrote are not orphaned -- the words are still somebody's.
check("and descriptions already on disk are offered as prompts to compose from",
      "legacyBeds(" in TEMPLATE and "P.music_beds" in TEMPLATE, True)
_legacy = rp_store.create({"company": "Old", "slots": ["thirty"]})
rp_store.update(_legacy["id"], {"music_beds": [{"id": "bed_1",
                                               "prompt": "warm acoustic guitar"}]})
check("a project carrying one still loads",
      client.get(f"/api/projects/{_legacy['id']}").get_json()["project"]["music_beds"][0]["prompt"],
      "warm acoustic guitar")
check("and it reads as having no bed rather than as having a silent one",
      rp_store.get(_legacy["id"]).get("beds"), {})


# =====================================================================
section("Composing a bed: what is saved and what is refused")
# =====================================================================
created = client.post("/api/projects", json={
    "company": "Acme Solar", "home_url": "https://acmesolar.com",
    "slots": ["fifteen", "thirty", "sixty"]}).get_json()
PID = created["project"]["id"]
check("a project records the lengths it was asked for",
      created["project"]["slots"], ["fifteen", "thirty", "sixty"])
rp_store.update(PID, {"scripts": {"thirty": {
    "script": GOOD, "spoken": GOOD, "word_count": 70}}})

_real_compose = radio_spec.compose_bed
radio_spec.compose_bed = lambda prompt, seconds: {
    "audio_bytes": b"\xff\xfbfake-mp3", "seconds": 29.7,
    "requested_seconds": 29.75, "bytes": 9, "_asked": (prompt, seconds)}
_asked = {}
radio_spec.compose_bed = lambda prompt, seconds: (
    _asked.update(prompt=prompt, seconds=seconds),
    {"audio_bytes": b"\xff\xfbfake-mp3", "seconds": 29.7,
     "requested_seconds": 29.75, "bytes": 9})[1]

_bed = client.post(f"/api/projects/{PID}/bed/compose",
                   json={"slot": "thirty", "mood": "Corporate"}).get_json()
check("a mood tile composes a bed", _bed["ok"], True)
check("at the slot's own length", _asked["seconds"], 30)
check("using the words that tile promised",
      _asked["prompt"], cb_config.music_prompt_starter("Corporate"))
check("and it is recorded as composed", _bed["bed"]["kind"], "composed")
check("with audio behind it", bool(_bed["bed"]["audio_url"]), True)

check("a mood the table does not carry is not composed under its own name",
      client.post(f"/api/projects/{PID}/bed/compose",
                  json={"slot": "fifteen", "mood": "Whimsical"}).status_code, 400)

# Mock mode is the defect this refuses: it composes nothing and says so, and a
# bed recorded from it would ship as silence.
radio_spec.compose_bed = lambda prompt, seconds: {
    "audio_bytes": None, "seconds": None, "_mock": True,
    "note": "Mock mode — no ELEVENLABS_API key is set, so no music was composed."}
_mock = client.post(f"/api/projects/{PID}/bed/compose",
                    json={"slot": "fifteen", "mood": "Rock"})
check("a mock bed is refused rather than saved", _mock.status_code, 502)
check("and the provider's own sentence is what is shown",
      "Mock mode" in _mock.get_json()["error"], True)
check("nothing was written to the fifteen",
      rp_store.get(PID).get("beds", {}).get("fifteen"), None)
radio_spec.compose_bed = _real_compose

check("a bed can be uploaded instead",
      client.post(f"/api/projects/{PID}/bed/upload",
                  data={"slot": "sixty",
                        "file": (io.BytesIO(wav(58.0)), "bed.wav", "audio/wav")},
                  content_type="multipart/form-data").get_json()["bed"]["kind"],
      "upload")
check("and an uploaded WAV bed is measured off its header",
      rp_store.get(PID)["beds"]["sixty"]["seconds"], 58.0)


# =====================================================================
section("Filing a mix: measured here, and refused by name")
# =====================================================================
check("the voice can be a recording somebody already had",
      client.post(f"/api/projects/{PID}/voice/upload",
                  data={"slot": "thirty",
                        "file": (io.BytesIO(b"\xff\xfbmp3read"), "read.mp3", "audio/mpeg")},
                  content_type="multipart/form-data").get_json()["ok"], True)
_spot = [s for s in rp_store.get(PID)["spots"] if s["slot"] == "thirty"][0]
check("but an uploaded MP3's length is NOT measured", _spot["measured"], False)
check("and it says why rather than reporting a number",
      "not measured" in _spot["measure_note"], True)

# The mix must be a WAV, because a WAV is the only thing here that can be
# measured. Anything else is refused rather than filed with an unknown length.
_bad = client.post(f"/api/projects/{PID}/mix",
                   data={"slot": "thirty",
                         "file": (io.BytesIO(b"\xff\xfbnot-a-wav"), "mix.wav", "audio/wav")},
                   content_type="multipart/form-data")
check("a mix that is not a readable WAV is refused", _bad.status_code, 400)
check("and it is refused for a reason somebody can act on",
      "not a WAV" in _bad.get_json()["error"], True)


def file_mix(length, **extra):
    data = {"slot": "thirty", "level": "Medium",
            "file": (io.BytesIO(wav(length)), "mix.wav", "audio/wav")}
    data.update(extra)
    return client.post(f"/api/projects/{PID}/mix", data=data,
                       content_type="multipart/form-data")


_ok = file_mix(29.9)
check("a clean mix files", _ok.status_code, 200)
_mix = _ok.get_json()["mix"]
check("and its length is measured from the bytes we stored", _mix["seconds"], 29.9)
check("measured, not reported", _mix["measured"], True)
# The page is given every chance to lie about it. A 29.9s WAV posted with a
# `seconds` field claiming 30.0 must still be filed at 29.9 -- otherwise the
# length on a client's deliverable is whatever the browser felt like saying,
# which is the whole reason the mix comes back as a WAV in the first place.
check("a length the page claims is ignored in favor of the header",
      file_mix(29.9, seconds="30.0").get_json()["mix"]["seconds"], 29.9)
check("the level it rendered at is recorded", _mix["level"], "Medium")
check("with the dB pair from the shared table",
      (_mix["bed_db"], _mix["ducked_db"]),
      cb_config.MUSIC_LEVELS["Medium"])
check("and nobody overrode anything", _mix["override"], False)

_blocked = file_mix(34.0)
check("a mix 4s over its slot is not filed", _blocked.status_code, 409)
check("the report comes back with it",
      _blocked.get_json()["qc"]["blocking"], ["length_match"])
check("and the mix that was already filed is untouched",
      rp_store.get(PID)["mixes"]["thirty"]["seconds"], 29.9)
check("an override with no reason is refused",
      file_mix(34.0, override="1").status_code, 400)
_over = file_mix(34.0, override="1",
                 override_reason="Station takes 34s on this buy.").get_json()["mix"]
check("an override with a reason files", _over["seconds"], 34.0)
check("recorded as an override", _over["override"], True)
check("with the reason on it", _over["override_reason"], "Station takes 34s on this buy.")
check("and a name against it", bool(_over["override_by"]), True)

# A public_id that names a SLOT rather than a file has to replace what is
# already there. `mix-thirty` is deterministic, so re-mixing lands on the asset
# the last mix wrote -- and with overwrite off Cloudinary keeps the old bytes
# while the store records the new length, so the file a client is sent and the
# duration filed against it disagree. Swept rather than asserted per call site,
# because the next slot-named asset somebody adds has the same problem.
_app_src = (ROOT / "modules" / "radio_promo" / "app.py").read_text()
import re as _re3                                               # noqa: E402


def _calls(src, name):
    """Every call to `name`, matched on balanced parens.

    A regex guessing at the nesting is what the first version of this used, and
    it stopped early on the multi-line calls -- reporting 3 of 5 and passing the
    "every one of them" check on the two it happened to see, which is the sweep
    that quietly stops sweeping.
    """
    out = []
    for m in _re3.finditer(_re3.escape(name) + r"\(", src):
        i, depth = m.end(), 1
        while i < len(src) and depth:
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        out.append(src[m.start():i])
    return out


_slotty = [c for c in _calls(_app_src, "upload_asset")
           if "{slot}" in c and "def upload_asset" not in c]
check("every slot-named upload replaces its predecessor",
      [" ".join(c.split())[:52] for c in _slotty if "overwrite=True" not in c], [])
check("and the sweep found every one of them", len(_slotty), 5)


# =====================================================================
section("The browser only reads back what the project recorded")
# =====================================================================
# The mix is rendered in the browser, so the browser has to fetch both tracks
# and decode them -- and a cross-origin fetch a CDN declines fails silently, as
# a button that does nothing. So both are read back same-origin through one
# route whose allowlist is the project's own row. Nothing takes a URL from the
# caller, which is the rule the ad builder had to be given after a path in a
# POST body could lift any readable file into a web-served folder.
for ref, want in (("vo:thirty", 200), ("bed:thirty", 200), ("mix:thirty", 200),
                  ("bed:fifteen", 404), ("nope:thirty", 404), ("vo:ninety", 404),
                  ("vo", 404), ("", 404)):
    check(f"reading back {ref!r}", client.get(
        f"/api/projects/{PID}/audio?ref={ref}").status_code, want)
check("the route takes no URL from the caller at all",
      "request.args.get(\"url\"" in (ROOT / "modules" / "radio_promo" / "app.py").read_text(),
      False)
check("and a stranger cannot read a project back either",
      client.get("/api/projects/rp_nosuchproject/audio?ref=vo:thirty").status_code, 404)


# =====================================================================
section("A variation carries the choices and never the audio")
# =====================================================================
_var = client.post(f"/api/projects/{PID}/variations", json={
    "name": "Fishers store",
    "patch": {"company": "Acme Solar Fishers", "promotion": "New spring offer"}}).get_json()
KID = _var["project"]["id"]
check("the scripts come across", bool(_var["project"]["scripts"]), True)
check("so do the lengths it was writing", _var["project"]["slots"],
      ["fifteen", "thirty", "sixty"])
check("and the patch is applied", _var["project"]["company"], "Acme Solar Fishers")
check("what was patched is named", sorted(_var["patched"]), ["company", "promotion"])
# The audio does NOT. A rendered read and a finished mix are audio of the
# previous wording; cloned onto a project whose offer just changed they are the
# wrong file, and both play perfectly well, so nothing would say so.
check("no recorded read comes across", _var["project"]["spots"], [])
check("no mix comes across", _var["project"]["mixes"], {})
check("no bed comes across", _var["project"]["beds"], {})
# And the guard is the store rather than a denylist beside it: `store.create()`
# builds its row from named fields, so audio cannot arrive through it however
# the caller asks. Asserted directly, because a denylist that never fires reads
# as the mechanism and is not one.
_forced = rp_store.create({"company": "Guard", "spots": [{"slot": "thirty"}],
                           "mixes": {"thirty": {"seconds": 9}},
                           "beds": {"thirty": {"kind": "composed"}},
                           "versions": [{"k": 1}], "banner": {"art_url": "x"}})
check("audio handed straight to the store is not carried either",
      (_forced["spots"], _forced["mixes"], _forced["beds"],
       _forced["versions"], _forced["banner"]), ([], {}, {}, [], None))
check("while the fields a variation is meant to carry still arrive",
      rp_store.create({"company": "C", "scripts": {"thirty": {"script": "x"}},
                       "tone_id": "upbeat"})["scripts"], {"thirty": {"script": "x"}})
check("and the answer says so rather than leaving it to be discovered",
      "re-record" in _var["note"], True)
check("the variation names its parent", _var["project"]["variation_of"], PID)
check("and the parent lists the variation",
      [v["id"] for v in rp_store.get(PID)["variations"]], [KID])
check("a variation is not itself born with variations",
      rp_store.get(KID)["variations"], [])


# =====================================================================
section("None of this is a client's to open")
# =====================================================================
# The routes on this page -- beds, mixes, voice uploads, variations -- name
# clients, carry briefs and spend money per press, so none of them may be
# reachable without a Hub login. Radio Promo now declares its own client
# review page too (hub/radio_share.py, the one implementation with Fan
# Radio's), so PUBLIC_PREFIXES is no longer empty -- it is exactly the three
# mount-relative prefixes a customer reaches, and none of the billed routes
# this file exercises fall under any of them.
check("Radio Promo declares exactly the client review page as public",
      getattr(rp_app, "PUBLIC_PREFIXES", None), ("/r/", "/api/public/", "/file/"))
_writes = [r for r in rp_app.app.url_map.iter_rules()
           if {"POST"} & r.methods and any(
               k in str(r) for k in ("/bed/", "/mix", "/voice/upload", "/variations"))]
check("and every new write route is inside that mount", len(_writes), 6)
check("and none of the billed writes fall under the public prefixes",
      all(not str(r).startswith(("/tools/radio-promo/r/",
                                 "/tools/radio-promo/api/public/",
                                 "/tools/radio-promo/file/"))
          for r in rp_app.app.url_map.iter_rules()
          if {"POST"} & r.methods and any(
              k in str(r) for k in ("/bed/", "/mix", "/voice/upload", "/variations"))),
      True)



# =====================================================================
# Two people, two projects, and only one edit survived
# =====================================================================
# Every project in this module lives in ONE file, so changing one is written
# back as the whole list. The lock covered the write and not the read, so two
# threads that had already read the same snapshot overwrote each other -- and
# it needs no contention over a single project to happen: two people editing
# two UNRELATED projects lose one of the two edits. Both are told it saved.
#
# The module docstring also promises every draft and rewrite is *appended*
# rather than overwriting, "so nothing a client approved can be silently
# lost". Measured before the fix, eight concurrent appends kept one.
section("One file for every project, changed one project at a time")

_a = rp_store.create({"project_name": "Alpha"})
_b = rp_store.create({"project_name": "Bravo"})

_real_read = _jstore.read_json


def _slow_read(path, default=None, **kw):
    got = _real_read(path, default=default, **kw)
    time.sleep(0.02)                  # the window a read-change-write has
    return got


_answers = {}


def _rename(pid, name):
    _answers[name] = rp_store.update(pid, {"project_name": name})


_jstore.read_json = _slow_read
_pair = [threading.Thread(target=_rename, args=(_a["id"], "Alpha EDITED")),
         threading.Thread(target=_rename, args=(_b["id"], "Bravo EDITED"))]
for _th in _pair:
    _th.start()
for _th in _pair:
    _th.join()
_jstore.read_json = _real_read

check("both saves report success", all(bool(v) for v in _answers.values()), True)
_titles = {r["id"]: r.get("project_name") for r in rp_store.all_projects()}
check("and the edit to one project does not drop the other",
      sorted([_titles.get(_a["id"]) or "", _titles.get(_b["id"]) or ""]),
      ["Alpha EDITED", "Bravo EDITED"])


def _append(i):
    rp_store.add_version(_a["id"], f"kind{i}", {"n": i})


_jstore.read_json = _slow_read
_appends = [threading.Thread(target=_append, args=(i,)) for i in range(8)]
for _th in _appends:
    _th.start()
for _th in _appends:
    _th.join()
_jstore.read_json = _real_read
check("eight concurrent appends all land, as the docstring promises",
      len((rp_store.get(_a["id"]) or {}).get("versions") or []), 8)

# And it must not go back to deciding for itself. A threading.Lock here reads
# as correct and is half a lock: it cannot see the second gunicorn worker.
_store_src = (pathlib.Path(__file__).parent
              / "modules/radio_promo/store.py").read_text()
check("the store keeps no per-process lock of its own",
      "threading.Lock()" in _store_src, False)
check("and reads the shared read-change-write",
      "jsonstore.update_json(" in _store_src, True)


print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    sys.exit(1)
print("beds are composed rather than described, the mix is measured off the file, "
      "and a variation carries the choices without the audio")
