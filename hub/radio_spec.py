"""Smart 1 Hub — the radio spot's rules, as data.

`modules/radio_promo` and `modules/fan_radio` write, cast and record radio
commercials; this is the half of that job that is not about either tool. It
carries the **length menu and its word budgets**, the bed vocabulary, the mix
levels, the length arithmetic, the QC checks and the two honest ways to measure
a finished file.

It was written so Fan Radio "can read the same rules later without a second
copy of them being written first", and that is what it now does. The lengths
arrived here from `modules/radio_promo/catalog.py` when it did, because the
second copy already existed and had already drifted: Fan Radio's :15 was 30-38
words against 35-42 here and its :30 was 65-75 against 65-85, so a script that
read as on the clock in one tool read as short or long in the other -- while
Fan Radio's own README promised the two agreed. `modules/radio_promo/catalog.py`
re-exports what moved under its old names, so one table answers both and no
call site had to change.

## The music table is Commercial Builder's, read rather than restated

`modules/commercial_builder/config.py` already holds `MUSIC_LEVELS`,
`MUSIC_PROMPT_STARTERS` and `music_length_ms()`, and
`services/elevenlabs_audio_service.py` already composes a bed to a spot's own
runway with the caching and the per-generation metering that go with it. Two
readings of "how loud is a bed" is how the panel and the render come to
disagree, which is the note `config.ducked_db()` carries in as many words --
so this module **imports that table and keeps no fallback copy of it**.

The consequence is stated rather than discovered: where that import fails,
`available()` says so and `compose_bed()` refuses. It does not invent a dB
pair. A bed mixed at numbers nobody published is the confident wrong answer
this codebase keeps having to undo, and "we could not read the shared music
table" is a sentence somebody can act on.

The import is lazy and guarded, the arrangement `hub/ad_builder_link.py`
already uses to reach `modules/image_picker`: a module-level import of a
mounted module's service would make this file's import order load-bearing.

What is deliberately **not** done is moving that service into `hub/` outright.
It is the right long-term home and the opportunistic-migration rule says to
take a module's shared code with you when you are already in it -- but the
composer, its content-keyed cache, its per-generation metering and its limits
all landed one release ago, and lifting them wholesale to give radio a bed is
a rewrite of a just-shipped billing path in service of a feature that does not
need one. Reading it from here costs nothing and moves the day somebody is in
that file for its own reasons.

## Nothing here decodes audio, and that decides what "measured" means

There is no ffmpeg, ffprobe, pydub or numpy in this runtime -- which is
exactly why `modules/radio_promo` shipped without music beds, and why the mix
is rendered in the browser through the Web Audio API rather than on the
server. What comes back from that mix is a **WAV**, and a WAV states its own
sample rate, channel count and data length in its header: `wav_seconds()` is
arithmetic on those, so the length of the file we filed is genuinely
measured, by us, from the bytes we stored.

`mp3_seconds()` beside it is the weaker reading and every caller labels it
*estimated*: exact for a constant-bitrate file, an estimate for a variable one.
It is here because both builders had a copy of it and one of them was **wrong**
-- Fan Radio's advanced four bytes per candidate sync word rather than by the
frame's own length, so it counted sync patterns inside frame data as frames and
reported a multiple of the real duration, on the number a rep reads to decide
whether a read fits its slot.

An uploaded MP3 is the opposite case and says so. `cbr_seconds` in the audio
service is only valid at a bitrate we asked for, and an MP3 somebody uploads
is at a bitrate nobody here chose, so its length is **not measured** -- never
a number the page reported, which is the rule `_dimensions()` in
`modules/bg_remover` arrived at for an image's own pixels.

## A check that could not run is not a check that passed

`qc()` answers `pass`, `warn`, `block` or `not_measured` per row, and the last
of those is never folded into the first. A spot with no mix yet has not
passed the length check; it has not taken it.

Two of the checks are worth reading for what they refuse to be. The spec this
was built from asked for a **"music bed licensed"** block against a catalog
of cleared tracks. There is no such catalog here -- a bed is composed on
demand or uploaded by whoever is making the spot -- so the check that
actually protects a client is `bed_source`: the bed on the project must be
real audio with a provenance we recorded. A described-only bed and a mock-mode
bed both come back from that check as blocking, because both produce a spot
that renders and is silent under the voice, which is the placeholder failure
`qrcode_service` already paid for on the end card of a CTV spot.

And **nothing here refuses a render.** `QR_CODE_RULES` is the precedent: a
check that blocks the correct thing is a check somebody switches off, and
switching this one off costs the CTA check with it. `qc()` reports; the route
that files a mix asks for an explicit override and records who gave it.
"""

from __future__ import annotations

import math
import re
import struct

# ---------------------------------------------------------------------------
# The shared music table, reached lazily.
# ---------------------------------------------------------------------------
_MISSING = ("The shared music table lives in Commercial Builder's config and "
            "could not be read, so no bed level or length can be quoted here.")


def _cb_config():
    """Commercial Builder's config, or ``(None, reason)``. Never raises."""
    try:
        from modules.commercial_builder import config as cb_config
        return cb_config, ""
    except Exception as exc:                                   # noqa: BLE001
        return None, f"{_MISSING} ({exc})"


def _cb_audio():
    """The ElevenLabs audio service, or ``(None, reason)``. Never raises."""
    try:
        from modules.commercial_builder.services import elevenlabs_audio_service
        return elevenlabs_audio_service, ""
    except Exception as exc:                                   # noqa: BLE001
        return None, ("Commercial Builder's ElevenLabs audio service could not "
                      f"be read, so no bed can be composed. ({exc})")


def available() -> dict:
    """Whether the shared music rules can be read at all, and why not.

    Tri-state on purpose: a panel that cannot quote a level must say that
    rather than drawing a slider over numbers it made up.
    """
    cfg, cfg_error = _cb_config()
    audio, audio_error = _cb_audio()
    return {"levels": bool(cfg), "compose": bool(audio and cfg),
            "error": cfg_error or audio_error}


# ---------------------------------------------------------------------------
# The lengths a radio spot is sold in.
#
# This table was `modules/radio_promo/catalog.DURATIONS` and is here because a
# second builder now reads it. `modules/fan_radio` shipped its own copy of the
# :15 and :30 budgets -- and its own catalog docstring claimed they were "the
# same clock Radio Promo and the Commercial Builder use, so a script written
# here drops straight into either without re-timing". They were not: a :15 was
# 30-38 words there against 35-42 here, and a :30 was 65-75 against 65-85. So a
# script that read as on the clock in one tool read as short or long in the
# other, both screens internally consistent, with the README promising the
# opposite. That is the drift `hub/storage.py` exists to stop, wearing a word
# budget, and there is one table now.
#
# The studio shipped the :15/:30 pair -- written together so they share a hook
# -- and that is still the default, because it is what most streaming buys are
# sold as. The :60 arrived with the mix work; the :10 is the sponsorship tag
# every station sells against a live read.
#
# Word budgets are the studio's, measured at the natural 2.6 words/second read
# `speech.WORDS_PER_SECOND` holds. Every other reader of these numbers reads
# them from here: the AI system prompts state them, both builders color the
# word count against them and `qc()` below judges the script on them, and each
# of those was a hand-typed second copy of the table before now.
#
# THE :60 IS 140-170, NOT the 150-180 the build spec asked for. At this pace
# 180 words is a 69-second read, so a :60 written to the top of that range
# cannot be recorded inside its own slot -- it comes back over, gets tightened,
# and the budget that sent it there was ours. 170 words is 65 seconds, which is
# the same deliberate overshoot the :15 and :30 carry: `grade_duration()` flags
# a render more than 0.4s long, so the top of a budget is allowed to be a
# little over the clock and the measured read is what actually decides it.
#
# `min_seconds` is the ONE read floor, and it is deliberately set on the long
# slots only. A :30 or a :60 is bought and billed by the second, so a read that
# lands well under it is dead air somebody paid for. A :10 or a :15 is a tag:
# it is naturally tight, and a floor there would refuse correct copy, which is
# the crying wolf that gets a check switched off.
#
# Two labels, because two things want naming. `label` is the clock (":30") and
# is what the prompts and the pickers print; `name` is what the unit is for.
# ---------------------------------------------------------------------------
DURATIONS = [
    {"seconds": 10, "key": "ten", "label": ":10",
     "word_target": "22-28 words", "low": 22, "high": 28, "min_seconds": None,
     "name": "Sponsorship tag",
     "note": "One idea and the brand. No offer, no phone number -- ten seconds "
             "cannot carry a response somebody acts on.",
     # And so a :10 is not judged for leaving one out. The note above has said
     # this since the table was written and nothing read it, so the content
     # check demanded a web address in a sponsorship tag -- a check refusing
     # correct copy, which is how a panel comes to be switched off.
     "carries_response": False,
     "cost": "Cheapest read on the menu -- roughly a third of a :30 in "
             "voiceover characters."},
    {"seconds": 15, "key": "fifteen", "label": ":15",
     "word_target": "35-42 words", "low": 35, "high": 42, "min_seconds": None,
     "name": "Standard short",
     "note": "One message, one call to action. The brand said at least once, "
             "the address last.",
     "carries_response": True,
     "cost": "Low -- about half a :30 in voiceover characters."},
    # A :30 is never a short tag. At the normal 2.6 words/second read this
    # floor is just over 25 seconds, leaving room for natural pauses.
    {"seconds": 30, "key": "thirty", "label": ":30",
     "word_target": "65-85 words (25+ second read)", "low": 65, "high": 85,
     "min_seconds": 25,
     "name": "The workhorse",
     "note": "The unit most streaming audio is sold in. Hook, value, close, "
             "with the brand said at least twice.",
     "carries_response": True,
     "cost": "Moderate -- the length every other read here is priced against."},
    {"seconds": 60, "key": "sixty", "label": ":60",
     "word_target": "140-170 words (54+ second read)", "low": 140, "high": 170,
     "min_seconds": 54,
     "name": "Long form",
     "note": "Room for a story, a testimonial or a real explanation rather "
             "than an offer. Worth it where the listener is already yours.",
     "carries_response": True,
     "cost": "Roughly twice a :30 in voiceover characters.",
     "warning": "A :60 is about twice a :30 in voiceover characters, and "
                "ElevenLabs bills the character -- so every re-record of it "
                "costs twice as much too. Build one where the air is bought "
                "for it, and cut a :30 or :15 alongside for everywhere else."},
]

# The pair every project has always produced. The :10 and the :60 are opt-in
# rather than two more scripts on every job: each is a model call and a slot
# nobody asked for, and a project saved before this existed carries no slot
# list at all -- `normalize_slots()` reads that as the pair rather than
# migrating rows nobody has re-opened.
DEFAULT_SLOTS = ("fifteen", "thirty")

SLOT_KEYS = tuple(d["key"] for d in DURATIONS)
SLOT_SECONDS = tuple(d["seconds"] for d in DURATIONS)


def duration_by_key(key: str) -> dict | None:
    for slot in DURATIONS:
        if slot["key"] == key:
            return slot
    return None


def duration_by_seconds(seconds) -> dict | None:
    """The slot a length in seconds names, or None.

    The other way of asking `duration_by_key()`, because the two builders key
    the same table differently: Radio Promo stores a slot key on the project
    and Fan Radio stores the integer seconds on each spot. One table, two
    lookups -- rather than a second table keyed the other way, which is how
    they came to disagree about a :15 in the first place.
    """
    try:
        want = int(seconds)
    except (TypeError, ValueError):
        return None
    for slot in DURATIONS:
        if slot["seconds"] == want:
            return slot
    return None


def normalize_slots(keys) -> tuple[str, ...]:
    """The slots to write, in clock order, deduped, never empty.

    Ordered by length rather than by the order somebody ticked them, so a :60
    and a :15 come back the same way round however they were picked. An
    unknown key is dropped rather than carried: a slot this table cannot
    describe would reach the writer as a length with no budget behind it, and
    nothing downstream could price or grade it.
    """
    asked = {str(k or "").strip() for k in (keys or ())}
    keys_out = tuple(k for k in SLOT_KEYS if k in asked)
    return keys_out or tuple(DEFAULT_SLOTS)


def budget_line() -> str:
    """The word budgets as one sentence, for a writer's system prompt.

    Derived rather than typed, because the prompt is what the model is
    actually held to and a stale copy of it there is a script written to a
    budget the checker no longer uses. The floor is `min_seconds` -- the same
    number the checks refuse against -- rather than one re-derived here, and a
    slot with no floor says nothing rather than inventing one.
    """
    parts = []
    for slot in DURATIONS:
        line = f'a {slot["label"]} runs {slot["low"]}-{slot["high"]} words'
        floor = slot.get("min_seconds")
        if floor:
            line += f" and at least {floor:g} seconds"
        parts.append(line)
    return "; ".join(parts)


def slot_budget_line(slot_key: str) -> str:
    """The one slot's budget, for a picker and a copy screen.

    `budget_line()` answers for the whole menu because that is what a system
    prompt needs; this answers for the slot somebody is looking at.
    """
    slot = duration_by_key(slot_key) or {}
    words = slot.get("word_target") or ""
    floor = slot.get("min_seconds")
    if not floor or "second read" in words:
        return words
    return f"{words}, and at least {floor:g} seconds at a natural read pace"


def length_warning(slot_key: str) -> str:
    """The warning for a slot, or empty where there is none.

    Empty rather than a cheerful reassurance: a note on every length is a note
    nobody reads, and then the one that mattered goes past unread too.
    """
    return (duration_by_key(slot_key) or {}).get("warning", "")


# ---------------------------------------------------------------------------
# How long a read takes, before anybody has paid for one.
#
# These arrived from `modules/radio_promo/speech.py`, which is where the only
# copy lived -- so Fan Radio had no way to answer *is this :30 short* until it
# had spent the ElevenLabs characters and listened to the dead air. That is the
# same shape of defect as the word budgets that used to be two tables: the
# number existed, one tool could read it, and the other could not.
#
# `WORDS_PER_SECOND` is a read pace rather than a fact about a file, so
# everything derived from it is named an **estimate** at every call site. The
# render still measures; `wav_seconds()` below is the measurement.
# ---------------------------------------------------------------------------
WORDS_PER_SECOND = 2.6          # natural commercial read pace


def count_words(script: str = "") -> int:
    """Words in a script, counted the one way.

    `grade_words()` below had its own `str.split()` count. The two agreed, and
    two functions answering *how many words is this* is how a panel comes to
    report 84 words beside a budget check that saw 85 -- so it reads this one.
    """
    return len([w for w in re.split(r"\s+", str(script or "")) if w])


def estimate_seconds(script: str = "") -> float:
    """Roughly how long this copy takes to read aloud. An estimate, always."""
    return round(count_words(script) / WORDS_PER_SECOND, 1)


def grade_duration(seconds: float | None, target) -> dict:
    """How far off the clock a read is, and whether that matters.

    Answers for an estimate or for a measured file -- the caller knows which it
    handed over and says so. A read that runs long is never trimmed
    automatically: trimming clips a word off the end of the phone number, so it
    comes back flagged with how many words to cut instead.
    """
    target = int(target or 0)
    if not seconds or not target:
        return {"status": "unknown", "label": "Length not measured"}
    over = seconds - target
    if over > 0.4:
        words = max(1, round(over * WORDS_PER_SECOND))
        return {"status": "long", "over": round(over, 1), "trim_words": words,
                "label": f"{seconds:.1f}s — {over:.1f}s over. Roughly {words} "
                         f"word{'' if words == 1 else 's'} too many."}
    if seconds < target - 2.5:
        under = target - seconds
        return {"status": "short", "under": round(under, 1),
                "label": f"{seconds:.1f}s — {under:.1f}s of dead air at the end."}
    return {"status": "good", "label": f"{seconds:.1f}s — lands on the clock."}


def grade_words(script: str, seconds) -> dict:
    """Word count against the budget for that length, in one shape.

    Both builders drew this and neither drew it the same way: Radio Promo
    colored a count against `low`/`high` and Fan Radio returned its own
    `{state, delta, note}`. The numbers are the same table now, so the verdict
    is one function rather than two readings that can disagree about whether a
    script fits.
    """
    words = count_words(script)
    slot = duration_by_seconds(seconds)
    if not slot:
        return {"words": words, "state": "not_measured", "delta": 0,
                "note": "No word budget is on file for that length."}
    if words > slot["high"]:
        over = words - slot["high"]
        return {"words": words, "state": "long", "delta": over,
                "note": f"{over} word(s) over a {slot['label']} read."}
    if words < slot["low"]:
        under = slot["low"] - words
        return {"words": words, "state": "short", "delta": under,
                "note": f"{under} word(s) short — there's room."}
    return {"words": words, "state": "ok", "delta": 0,
            "note": f"On the clock for a {slot['label']}."}


# ---------------------------------------------------------------------------
# The beats a read is built on.
#
# The Commercial Builder has shown its structure above the storyboard since it
# was written (config.STRUCTURE_TEMPLATES) and radio had none — so the shape of
# a read lived in the prompt, where a rep could not see it and could not tell a
# script that had wandered from one that was written to a plan.
#
# Same shape as the Commercial Builder's, deliberately, so somebody moving
# between the tools is reading one idea. The guidance is radio's own: there is
# no picture, so every beat has to earn its seconds in words.
# ---------------------------------------------------------------------------
STRUCTURE_TEMPLATES = {
    "ten": [
        {"label": "Brand + one line", "start_pct": 0, "end_pct": 100,
         "guidance": "Name the business and say one thing about it. No offer, "
                     "no phone number, no second idea — this is recall, and "
                     "ten seconds is gone before anybody can act on it."},
    ],
    "fifteen": [
        {"label": "Hook", "start_pct": 0, "end_pct": 27,
         "guidance": "One line that makes somebody stop scrolling past the "
                     "audio. A question, a pain point, or the offer itself."},
        {"label": "Offer", "start_pct": 27, "end_pct": 73,
         "guidance": "What they get and why it is worth hearing out. One "
                     "benefit, not three — a :15 has room for exactly one."},
        {"label": "Call", "start_pct": 73, "end_pct": 100,
         "guidance": "The brand and the address, last, said clean and "
                     "unhurried. The last thing heard is the thing recalled."},
    ],
    "thirty": [
        {"label": "Hook", "start_pct": 0, "end_pct": 20,
         "guidance": "Open on the listener's problem or the moment the offer "
                     "solves. Do not open on the company name."},
        {"label": "Value", "start_pct": 20, "end_pct": 70,
         "guidance": "The offer and the proof behind it. This is where the "
                     "brand name is said the first of its two times."},
        {"label": "Call", "start_pct": 70, "end_pct": 100,
         "guidance": "Brand, address, and any disclaimer word for word before "
                     "it. Leave the last beat unhurried — a rushed URL is a "
                     "URL nobody caught."},
    ],
    "sixty": [
        {"label": "Open", "start_pct": 0, "end_pct": 15,
         "guidance": "Set a scene or a moment. A :60 is the one radio length "
                     "with room to earn attention rather than grab it."},
        {"label": "Story", "start_pct": 15, "end_pct": 55,
         "guidance": "The narrative, the testimonial or the real explanation. "
                     "This is the beat that does not exist in any shorter cut "
                     "and the only reason to buy this length."},
        {"label": "Offer", "start_pct": 55, "end_pct": 82,
         "guidance": "Land the offer and the proof. By here the listener has "
                     "given you forty seconds — say something specific."},
        {"label": "Call", "start_pct": 82, "end_pct": 100,
         "guidance": "Brand, address, disclaimer word for word. Say the "
                     "address twice if it is hard to spell."},
    ],
}


def structure_for(slot_key: str) -> list:
    """The beats for a slot, or the :30's where a slot has none of its own."""
    return STRUCTURE_TEMPLATES.get(slot_key, STRUCTURE_TEMPLATES["thirty"])


# ---------------------------------------------------------------------------
# Beds.
# ---------------------------------------------------------------------------
def bed_moods() -> list[dict]:
    """The mood tiles, each carrying the prompt it will actually send.

    The words are printed on the tile rather than summarized, the rule
    `hub/voice_casting.characteristics_detail()` works to: "Country" is not a
    mood, it is a request for acoustic guitar and brushed drums, and a rep who
    can read that picks differently before composing three wrong beds.

    There is deliberately **no mood-tag or genre taxonomy** beside this. The
    prompt already names the instruments and the feel, so a search box over
    the label and the prompt filters on what is really sent; a second table of
    tags would be a vocabulary nobody sends, drifting against the one that is.
    """
    cfg, _ = _cb_config()
    if not cfg:
        return []
    out = []
    for mood in getattr(cfg, "MUSIC_MOODS", []):
        prompt = cfg.music_prompt_starter(mood)
        if not prompt:
            # A mood with no prompt behind it would fill the box with its own
            # name, which is a worse brief than an empty one.
            continue
        out.append({"id": mood.lower().replace(" ", "-"), "label": mood,
                    "prompt": prompt})
    return out


def bed_levels() -> dict:
    """The bed / ducked dB pairs, and which one a louder bed is measured against."""
    cfg, error = _cb_config()
    if not cfg:
        return {"levels": [], "reference": "", "error": error}
    levels = [{"label": label, "bed_db": pair[0], "ducked_db": pair[1]}
              for label, pair in cfg.MUSIC_LEVELS.items()]
    return {"levels": levels, "reference": cfg.MUSIC_LEVEL_REFERENCE, "error": ""}


def ducked_db(level: str) -> dict:
    """The two dB values this level renders at, from the one shared table."""
    cfg, error = _cb_config()
    if not cfg:
        return {"bed": None, "ducked": None, "known": False, "error": error}
    pair = cfg.ducked_db(level)
    return {"bed": pair["bed"], "ducked": pair["ducked"],
            "known": pair["known"], "error": ""}


def bed_length_ms(seconds) -> int | None:
    """How long a bed for a spot of this length is asked for."""
    cfg, _ = _cb_config()
    if not cfg:
        return None
    return cfg.music_length_ms(seconds)


def bed_limits() -> dict:
    """ElevenLabs' published composing range, in seconds."""
    cfg, error = _cb_config()
    if not cfg:
        return {"min_seconds": None, "max_seconds": None, "error": error}
    return {"min_seconds": cfg.MUSIC_MIN_LENGTH_MS / 1000.0,
            "max_seconds": cfg.MUSIC_MAX_LENGTH_MS / 1000.0, "error": ""}


def generation_enabled() -> bool:
    """Whether the Compose button is offered at all on this deployment."""
    cfg, _ = _cb_config()
    if not cfg:
        return False
    try:
        return bool(cfg.music_generation_enabled())
    except Exception:                                          # noqa: BLE001
        return False


def compose_bed(prompt: str, seconds, *, extra_seconds: float = 0) -> dict:
    """One composed bed, with any requested runway added, or a reason.

    Everything expensive about this -- the content-keyed cache on the shared
    disk, the per-generation metering, the refusal that keeps its row -- is
    the audio service's and is inherited rather than repeated.
    """
    audio, error = _cb_audio()
    if not audio:
        return {"audio_bytes": None, "seconds": None, "error": error}
    try:
        runway = float(seconds or 0) + max(0, float(extra_seconds or 0))
    except (TypeError, ValueError):
        runway = float(seconds or 0)
    length = bed_length_ms(runway)
    if length is None:
        return {"audio_bytes": None, "seconds": None, "error": _MISSING}
    try:
        return audio.compose_music(prompt, length)
    except Exception as exc:                                   # noqa: BLE001
        # compose_music is written not to raise; this is the belt on that
        # promise, because a bed that breaks the page it is composed on is
        # worse than a bed that could not be composed.
        return {"audio_bytes": None, "seconds": None,
                "error": f"The bed could not be composed: {exc}"}


# ---------------------------------------------------------------------------
# The mix.
# ---------------------------------------------------------------------------
# Both ends of the bed, in milliseconds. A bed that starts at full level on
# the first sample reads as a mistake rather than a choice, and one that stops
# dead on the last is what a dropped line sounds like.
MIX_FADE_IN_MS = 400
MIX_FADE_OUT_MS = 900

# The moment of bed before the read starts. Without it the music begins on the
# same sample as the first syllable, which reads as a fault rather than as a
# bed -- and it is short on purpose, because every millisecond of it is a
# millisecond of the slot the voice does not get.
MIX_LEAD_IN_MS = 300

# How long the bed takes to get out of the way of the voice and come back.
# Faster in than out: a bed still up on the first syllable buries it, and one
# that snaps back the instant a line ends sounds like a fault.
MIX_DUCK_ATTACK_MS = 180
MIX_DUCK_RELEASE_MS = 450

# The mix is rendered at this rate whatever the sources are. 44.1k stereo is
# what every station on this book accepts and what the composer returns, so
# resampling once here beats each source arriving at its own rate.
MIX_SAMPLE_RATE = 44100
MIX_CHANNELS = 2

# A rendered mix is WAV rather than MP3, and the reason is a dependency: MP3
# encoding in the browser needs a library from a CDN, which this Hub does not
# add for one format. WAV is the format a station asks for anyway -- the MP3
# is the convenience copy, and nothing here pretends to produce one.
MIX_FORMAT = "wav"


def mix_defaults(level: str = "") -> dict:
    """Everything the browser needs to render the mix, decided here."""
    pair = ducked_db(level or "")
    return {"fade_in_ms": MIX_FADE_IN_MS, "fade_out_ms": MIX_FADE_OUT_MS,
            "lead_in_ms": MIX_LEAD_IN_MS,
            "duck_attack_ms": MIX_DUCK_ATTACK_MS,
            "duck_release_ms": MIX_DUCK_RELEASE_MS,
            "sample_rate": MIX_SAMPLE_RATE, "channels": MIX_CHANNELS,
            "format": MIX_FORMAT, "bed_db": pair["bed"],
            "ducked_db": pair["ducked"], "level_known": pair["known"],
            "error": pair["error"]}


# ---------------------------------------------------------------------------
# Getting an over-long read back inside the slot.
# ---------------------------------------------------------------------------
# A read that was *recorded here* and overruns has an obvious fix: tighten the
# script, or drop the voice's speed, and record it again. A read somebody
# **uploaded** has neither. It is a finished file made by talent who has gone
# home, and the only lever left in a runtime with no ffmpeg is the one the
# browser already has: play it faster. A station's own playout does exactly
# this -- time compression is how a :32 read makes a :30 log -- so the answer
# is to work out the rate, say what it costs, and let somebody approve it,
# rather than blocking the mix and leaving them nowhere to go.
#
# Two numbers bound it, and both are about the ear rather than the arithmetic:
SPEED_CLEAN_MAX = 1.05   # under this nobody hears it; stations compress here daily
SPEED_MAX = 1.15         # past this the read is audibly hurried, whatever the clock says
SPEED_STEP = 0.01        # the rate is quoted to the hundredth, always rounded UP

# What playing a buffer faster actually does, stated rather than glossed. The
# Web Audio API's `playbackRate` resamples: the read gets shorter *and* higher,
# because there is no pitch-preserving time-stretch in this runtime and this
# Hub does not add a library from a CDN for one feature. At 1.05x that is 0.84
# of a semitone, which is the honest reason SPEED_CLEAN_MAX sits where it does.
#
# There are two ways to apply a rate, they cost different things, and quoting
# the wrong cost is worse than quoting none:
#
# * **resample** -- play a finished file faster. The only lever a read somebody
#   UPLOADED has, because there is no re-record to ask for. Shorter and higher
#   together, and the semitones are the price.
# * **reread** -- ask the voice to read it again at that pace. What a *generated*
#   read has: ElevenLabs takes `speed` in `voice_settings`, so the words come
#   back at the new pace at the same pitch. The price is a paid take instead,
#   and no pitch figure applies -- printing one would be a cost this mode does
#   not have.
#
# Both share the arithmetic and the ceiling. Past SPEED_MAX a read sounds
# hurried however it got there, and 1.15 also sits inside the 0.7-1.2 window
# ElevenLabs accepts, so the ceiling never becomes a value the provider
# silently ignores.
SPEED_MODES = ("resample", "reread")


def speed_semitones(speed) -> float | None:
    """How far the voice rises at this rate, in semitones. ``None`` if unusable."""
    try:
        rate = float(speed)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    return round(12.0 * math.log2(rate), 2)


def speed_suggestion(*, vo_seconds, target_seconds, lead_in_ms=None,
                     mixed_seconds=None, tolerance_s=None,
                     mode: str = "resample") -> dict:
    """The rate that lands an over-long read inside its slot, and what it costs.

    Advice, never a measurement. The read's length comes from whatever decoded
    it -- the browser, on an uploaded MP3 this runtime cannot measure -- so
    every number here is arithmetic on a reported duration. What gets filed is
    still measured from the WAV's own header by `wav_seconds()`, and this
    changes nothing about that.

    ``needed`` is False for a read that already fits, and ``speed`` is ``None``
    whenever no rate inside `SPEED_MAX` gets there -- with ``trim_seconds``
    saying how much still has to come out of the script, because "speed it up"
    is not an answer to a read that is five seconds long.

    ``mode`` is how the rate will be applied, and it changes only what the note
    says it costs -- see `SPEED_MODES`. A generated read is `"reread"` and pays
    a take; an uploaded one is `"resample"` and pays the semitones. The
    arithmetic and the ceiling are the same either way, which is the point of
    them living here.
    """
    def _num(value):
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    vo = _num(vo_seconds)
    target = _num(target_seconds)
    if vo is None or vo <= 0:
        return {"available": False, "needed": False, "speed": None,
                "reason": "The read's length is not known, so there is no rate "
                          "to work out."}
    if target is None or target <= 0:
        return {"available": False, "needed": False, "speed": None,
                "reason": "This spot has no slot length on it to fit the read into."}

    lead_ms = MIX_LEAD_IN_MS if lead_in_ms is None else (_num(lead_in_ms) or 0.0)
    lead = max(0.0, lead_ms / 1000.0)
    runway = target - lead
    if runway <= 0:
        return {"available": False, "needed": False, "speed": None,
                "reason": f"A :{target:g} slot is shorter than the bed's "
                          f"{lead:g}s lead-in, so there is no runway to fit a "
                          "read into."}

    tol = _num(tolerance_s)
    tol = length_tolerance_s() if tol is None else tol
    # The mix renders at the longer of the slot and the read, so it is never
    # short -- the bed fills the rest. Over is the only direction this can go.
    rendered = _num(mixed_seconds)
    if rendered is None:
        rendered = max(target, lead + vo)
    over = round(rendered - target, 2)

    base = {"available": True, "vo_seconds": round(vo, 2),
            "target_seconds": target, "lead_seconds": round(lead, 3),
            "rendered_seconds": round(rendered, 2), "over_seconds": over,
            "tolerance_s": tol, "clean_max": SPEED_CLEAN_MAX,
            "max_speed": SPEED_MAX, "reason": "",
            "mode": "reread" if str(mode or "").strip().lower() == "reread"
                    else "resample"}

    if over <= tol:
        return {**base, "needed": False, "speed": None, "comfort": "fits",
                "note": f"{rendered:.2f}s against a :{target:g} slot, inside "
                        f"±{tol:g}s. Nothing to speed up."}

    # The rate that fits the read into what is left after the lead-in, rounded
    # UP to the hundredth so it lands at or inside the slot rather than one
    # rounding short of it.
    exact = vo / runway
    speed = math.ceil(exact / SPEED_STEP) * SPEED_STEP
    speed = round(speed, 2)
    lands = round(max(target, lead + vo / speed), 2)

    if speed > SPEED_MAX:
        # What the fastest honest rate still leaves, said two ways: how far
        # over the slot the mix would land, and -- the one somebody can act on
        # -- how much has to come out of the read as it was recorded.
        over_at_max = round(max(0.0, (lead + vo / SPEED_MAX) - target), 2)
        trim = round(max(0.0, vo - runway * SPEED_MAX), 2)
        return {**base, "needed": True, "speed": None, "comfort": "too_far",
                "speed_needed": speed, "trim_seconds": trim,
                "over_at_max_seconds": over_at_max,
                "note": f"This read is {over:.2f}s over a :{target:g} slot, and "
                        f"no speed fixes that: it would take {speed:.2f}x, past "
                        f"the {SPEED_MAX:g}x where a read stops sounding like "
                        f"one. Even at {SPEED_MAX:g}x the mix lands "
                        f"{over_at_max:.2f}s over, so roughly {trim:.2f}s has to "
                        "come out of the read itself. Cut the script and have it "
                        "read again."}

    semis = speed_semitones(speed)
    comfort = "clean" if speed <= SPEED_CLEAN_MAX else "audible"
    reread = str(mode or "").strip().lower() == "reread"
    # "Inside what a station's playout does" is a statement about time
    # compression and says nothing true about a fresh read, so the two modes
    # answer "is this audible" in their own terms.
    if comfort == "clean":
        heard = "under the 5% nobody hears"
    elif reread:
        heard = ("a touch brisker than the take you have, and well inside the "
                 "0.7-1.2 window ElevenLabs reads in")
    else:
        heard = ("audible on a close listen, and inside what a station's own "
                 "playout does")
    if reread:
        # No pitch figure: the words are spoken again at the new pace, so the
        # semitones a resample would cost are a price this mode does not pay.
        # What it does cost is a billed take, and that is what the sentence
        # has to say instead -- an offer that reads as free gets pressed twice.
        action = (f"Recording the read again at {speed:.2f}x brings it to "
                  f"{lands:.2f}s")
        cost = ("The voice reads it at the new pace rather than the file being "
                "played faster, so nothing shifts in pitch — it costs one more "
                "paid take.")
    else:
        action = f"Playing the read at {speed:.2f}x lands the mix on {lands:.2f}s"
        cost = ((f"The voice rises about {semis:.2f} of a semitone with it"
                 if semis is not None and semis < 1
                 else f"The voice rises about {semis:.2f} semitones with it")
                + ", because resampling is the only time-stretch this runtime "
                  "has.")
    return {**base, "needed": True, "speed": speed, "comfort": comfort,
            "mode": "reread" if reread else "resample",
            "lands_seconds": lands,
            "semitones": None if reread else semis,
            "note": f"{rendered:.2f}s is {over:.2f}s over the :{target:g} slot. "
                    f"{action} — {heard}. {cost}"}


# ---------------------------------------------------------------------------
# Dead air, and the order the two levers are pulled in.
# ---------------------------------------------------------------------------
# A read that runs long has two things wrong with it and only one of them is
# the performance. Before anybody is asked to speed a read up -- which costs a
# take, or costs the pitch -- the silence should come out, because that is free
# and nobody can hear it go. A :32 read with six half-second gaps in it is a
# :29 read somebody recorded with pauses.
#
# So the order is fixed: **measure, trim the dead air, measure again, and only
# then offer a rate.** Offering a rate first spends money to fix something a
# gap edit would have fixed for nothing.
#
# There is no ffmpeg, pydub or numpy in this runtime, so none of this happens
# here. The browser already decodes audio for the mix through the Web Audio
# API, which means it can decode an upload in any format it supports, find the
# quiet runs in the samples and hand back a WAV -- and a WAV is the one thing
# this runtime CAN measure, off its own header. That is why an upload is
# decoded and re-encoded on the way in rather than stored as it arrived: it
# turns "not measured" into measured for every file somebody uploads.
#
# What lives here is the vocabulary and the limits, for the same reason the dB
# pair does: the panel that draws the advanced controls and the route that
# validates them must not each keep their own idea of what is allowed.
DEAD_AIR_DEFAULTS = {
    # Below this counts as silence. Room tone on a decent home recording sits
    # around -50 dBFS; a breath is louder. Too high and it eats the front of
    # words, which is the one failure nobody forgives.
    "threshold_db": -45.0,
    # A gap shorter than this is the rhythm of the read, not dead air. Cutting
    # at 150ms gives you a voice with no punctuation.
    "min_gap_ms": 350.0,
    # What a trimmed gap is shortened TO, rather than removed. A gap closed to
    # nothing runs two sentences together and reads as a splice.
    "keep_ms": 180.0,
    # Leading and trailing silence, which are not gaps and are usually the
    # biggest single win on a phone recording.
    "head_ms": 120.0,
    "tail_ms": 200.0,
}

# What an advanced control may be set to. Each bound is a failure somebody
# would otherwise ship: a threshold at the loud end of this range starts cutting
# quiet speech, a keep of 0 splices sentences together, and a min gap under
# 150ms removes the pauses that make a read a read. The bounds are written once,
# below, rather than restated in this sentence.
DEAD_AIR_LIMITS = {
    "threshold_db": (-70.0, -20.0),
    "min_gap_ms": (150.0, 2000.0),
    "keep_ms": (0.0, 1000.0),
    "head_ms": (0.0, 2000.0),
    "tail_ms": (0.0, 2000.0),
}


def dead_air_settings(sent=None) -> tuple[dict, list]:
    """The trim settings to actually use, and what was refused.

    Every value is clamped to `DEAD_AIR_LIMITS` and anything unparseable falls
    back to the default -- with a sentence naming it, because a control that
    silently ignores what somebody typed is the slider that does nothing, and
    this codebase has already paid for one of those.
    """
    out, notes = dict(DEAD_AIR_DEFAULTS), []
    for key, (low, high) in DEAD_AIR_LIMITS.items():
        if not isinstance(sent, dict) or key not in sent:
            continue
        raw = sent.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            notes.append(f"{key} was not a number, so the default "
                         f"{DEAD_AIR_DEFAULTS[key]:g} was used.")
            continue
        if not math.isfinite(value):
            notes.append(f"{key} was not a number, so the default "
                         f"{DEAD_AIR_DEFAULTS[key]:g} was used.")
            continue
        clamped = min(high, max(low, value))
        if clamped != value:
            notes.append(f"{key} {value:g} is outside {low:g}–{high:g}, "
                         f"so {clamped:g} was used.")
        out[key] = clamped
    return out, notes


def speed_ok(speed) -> tuple[float, str]:
    """A rate a caller sent, or a sentence. ``1.0`` means no time compression."""
    if speed in (None, "", "1", "1.0"):
        return 1.0, ""
    try:
        rate = float(speed)
    except (TypeError, ValueError):
        return 1.0, "That playback rate is not a number."
    if not math.isfinite(rate):
        return 1.0, "That playback rate is not a number."
    rate = round(rate, 4)
    if rate < 1.0:
        return 1.0, ("A mix is never short of its slot — the bed fills the rest "
                     "— so a slower read has nothing to fix here.")
    if rate > SPEED_MAX:
        return 1.0, (f"{rate:g}x is past the {SPEED_MAX:g}x where a read stops "
                     "sounding like one. Cut the script and have it read again.")
    return rate, ""


# ---------------------------------------------------------------------------
# Measuring a file we stored.
# ---------------------------------------------------------------------------
def wav_seconds(data: bytes) -> float | None:
    """The length of a WAV, read off its own header. ``None`` if it is not one.

    Never raises and never guesses. A truncated file, a format this does not
    recognize and a file that is not a WAV at all all answer ``None``, which
    every reader renders as *not measured* -- the one answer that is true when
    the bytes will not say.
    """
    if not data or len(data) < 44:
        return None
    try:
        if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
            return None
        pos, rate, channels, bits, data_size = 12, 0, 0, 0, 0
        end = len(data)
        while pos + 8 <= end:
            chunk = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            body = pos + 8
            if chunk == b"fmt " and body + 16 <= end:
                channels = struct.unpack_from("<H", data, body + 2)[0]
                rate = struct.unpack_from("<I", data, body + 4)[0]
                bits = struct.unpack_from("<H", data, body + 14)[0]
            elif chunk == b"data":
                # A streamed WAV can carry 0 or 0xFFFFFFFF here, in which case
                # what is actually present is the rest of the file.
                data_size = size if 0 < size <= end - body else end - body
                break
            pos = body + size + (size & 1)                    # chunks pad to even
        if not (rate and channels and bits and data_size):
            return None
        byte_rate = rate * channels * (bits // 8)
        if byte_rate <= 0:
            return None
        return round(data_size / float(byte_rate), 2)
    except Exception:                                          # noqa: BLE001
        return None


# An MP3's length, off its own frame headers.
#
# This is the labelled fallback, never a measurement in the sense `wav_seconds`
# is: it is exact for a constant-bitrate file and an estimate for a variable
# one, which is why every caller reports it as "estimated" rather than
# "measured". It is here because both builders had a copy and one of them was
# wrong -- `modules/fan_radio` advanced four bytes per candidate sync word
# instead of by the frame's own length, so it counted sync patterns inside
# frame data as frames and reported durations several times the real one, on
# the number a rep reads to decide whether a read fits its slot.
_MP3_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256,
                 320, 0]
_MP3_RATES = [44100, 48000, 32000, 0]


def mp3_seconds(data: bytes) -> float | None:
    """Duration from MP3 frame headers, or ``None``. Never raises."""
    try:
        i, total, frames = 0, 0.0, 0
        n = len(data or b"")
        if data[:3] == b"ID3":
            size = struct.unpack(">4B", data[6:10])
            i = 10 + (size[0] << 21 | size[1] << 14 | size[2] << 7 | size[3])
        while i + 4 <= n and frames < 200000:
            if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
                i += 1
                continue
            bitrate = _MP3_BITRATES[(data[i + 2] & 0xF0) >> 4]
            rate = _MP3_RATES[(data[i + 2] & 0x0C) >> 2]
            if not bitrate or not rate:
                i += 1
                continue
            padding = (data[i + 2] & 0x02) >> 1
            length = int(144000 * bitrate / rate) + padding
            total += 1152 / rate
            frames += 1
            # By the frame's own length. Advancing a fixed step is what made
            # the copy this replaces count the same audio many times over.
            i += max(length, 1)
        return round(total, 2) if frames else None
    except Exception:                                          # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# The call to action.
# ---------------------------------------------------------------------------
# Both self-serve platforms this was specced against report the same finding:
# the commonest reason a radio spot underperforms is that it never says what
# to do next. So a spot must carry a phone number, a web address or a code,
# and it must say it late enough to be remembered.
#
# Every pattern carries its match through to the reader. A finding quoting
# something a reader cannot find in the script is not evidence, which is the
# note `modules/commercial_builder/compliance_spec.py` had to fix after a
# first draft quoted `recovered $`.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
# 1-800-FLOWERS. Four or more letters, or it matches a hyphenated number.
_VANITY_RE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?8(?:00|33|44|55|66|77|88)"
                        r"[\s.-]?[A-Za-z][A-Za-z0-9-]{3,}(?!\w)")
_URL_RE = re.compile(
    r"(?:https?://\S+|www\.[\w-]+(?:\.[\w-]+)+"
    r"|\b[\w-]{2,}(?:\.[\w-]{2,})*\.(?:com|net|org|co|us|biz|info|io|shop|store)\b)",
    re.I)
# The spoken form. `speech.py` spells a web address out loud, so a script that
# has been through that pass says "acme dot com" and carries no dot at all.
_SPOKEN_URL_RE = re.compile(r"\b[\w-]{2,}\s+dot\s+(?:com|net|org|co|us|biz|info|io)\b",
                            re.I)
# "code SAVE20". Deliberately not a bare "code": a zip code, an area code and
# a dress code are not offers, and a check that fires on those is one somebody
# learns to ignore.
_CODE_RE = re.compile(r"\b(?:promo(?:tion(?:al)?)?\s+code|coupon\s+code|offer\s+code"
                      r"|discount\s+code|code(?:\s+word)?)\s*:?\s*"
                      r"[\"'“‘]?([A-Za-z0-9][A-Za-z0-9-]{2,19})\b", re.I)
_NOT_A_CODE = re.compile(r"\b(?:zip|postal|area|dress|building|door|source|error)\s+code\b",
                         re.I)

CTA_KINDS = {"phone": "a phone number", "url": "a web address",
             "code": "a promo code"}


def find_cta(text: str = "") -> dict:
    """What this script tells a listener to do, and the words that say so."""
    body = str(text or "")
    if not body.strip():
        return {"found": False, "kinds": [], "evidence": [], "last_offset": None}

    hits: list[tuple[int, str, str]] = []                    # (offset, kind, matched)
    for match in _PHONE_RE.finditer(body):
        hits.append((match.start(), "phone", match.group(0).strip()))
    for match in _VANITY_RE.finditer(body):
        hits.append((match.start(), "phone", match.group(0).strip()))
    for pattern in (_URL_RE, _SPOKEN_URL_RE):
        for match in pattern.finditer(body):
            hits.append((match.start(), "url", match.group(0).strip()))
    masked = _NOT_A_CODE.sub(lambda m: " " * len(m.group(0)), body)
    for match in _CODE_RE.finditer(masked):
        hits.append((match.start(), "code", body[match.start():match.end()].strip()))

    if not hits:
        return {"found": False, "kinds": [], "evidence": [], "last_offset": None}
    hits.sort(key=lambda h: h[0])
    kinds, evidence, seen = [], [], set()
    for _offset, kind, matched in hits:
        if kind not in kinds:
            kinds.append(kind)
        if matched.lower() not in seen:
            seen.add(matched.lower())
            evidence.append({"kind": kind, "text": matched})
    return {"found": True, "kinds": kinds, "evidence": evidence[:6],
            "last_offset": hits[-1][0]}


def cta_share(text: str = "", offset: int | None = None) -> float | None:
    """How far into the read the last call to action lands, 0.0-1.0.

    A word position rather than a clock, because the clock is the same
    arithmetic: `speech.estimate_seconds()` divides the word count by one
    house pace, so the fraction of the words before a point is the fraction of
    the runtime before it. Deriving it a second way would put two answers on
    one screen.
    """
    body = str(text or "")
    if offset is None or not body.strip():
        return None
    total = len(body.split())
    if not total:
        return None
    before = len(body[:offset].split())
    return round(min(1.0, before / float(total)), 3)


# The last fifth of the read. Both platforms' guidance is the same: the number
# goes at the end and, on a :30 or longer, it goes twice.
CTA_TAIL_SHARE = 0.8


# ---------------------------------------------------------------------------
# QC.
# ---------------------------------------------------------------------------
# ±1s of the slot, which is Commercial Builder's own tolerance for a bed
# against its runway, read from there rather than typed again.
def length_tolerance_s() -> float:
    cfg, _ = _cb_config()
    return float(getattr(cfg, "MUSIC_LENGTH_TOLERANCE_S", 1.0) or 1.0)


def _row(check_id, label, level, detail, **extra) -> dict:
    """One QC row, carrying its own label.

    The label rides on the row rather than in a map the renderer holds,
    because a check missing from such a map is skipped **silently** -- which
    is how `scene_assets` never appeared on the panel it was written for.
    """
    return {"id": check_id, "label": label, "level": level, "detail": detail,
            **extra}


def qc(*, script: str = "", words: int | None = None,
       words_low: int | None = None, words_high: int | None = None,
       target_seconds: int | None = None, mixed_seconds: float | None = None,
       bed: dict | None = None, vo_only: bool = False,
       speed: float | None = None) -> dict:
    """Every check, each answering for itself.

    Four levels, and the fourth is the point: ``not_measured`` is never folded
    into ``pass``. A spot with no mix has not passed the length check, it has
    not taken it.
    """
    checks: list[dict] = []
    tol = length_tolerance_s()

    # 1. Length. The one check the deliverable is actually judged on by a
    #    station, and the only one that can be answered from the bytes.
    if mixed_seconds is None:
        checks.append(_row("length_match", "Mix lands on the clock", "not_measured",
                           "No mix has been rendered yet, so nothing has been "
                           "measured. A length the browser reported for a file "
                           "we did not store is not a measurement."))
    elif not target_seconds:
        checks.append(_row("length_match", "Mix lands on the clock", "not_measured",
                           "This spot has no slot length on it to measure against."))
    else:
        off = round(float(mixed_seconds) - float(target_seconds), 2)
        # A mix that only fits because the read was played faster says so on
        # the row itself. Left off, the panel reads as a read that landed on
        # the clock -- and the next person to re-cut the spot would expect it
        # to land there again at 1.00x.
        try:
            rate = round(float(speed), 2)
        except (TypeError, ValueError):
            rate = 1.0
        compressed = (f" The read was time-compressed to {rate:.2f}x to land "
                      f"there ({speed_semitones(rate):+.2f} semitones)."
                      if rate > 1.0 else "")
        if abs(off) <= tol:
            checks.append(_row("length_match", "Mix lands on the clock", "pass",
                               f"{mixed_seconds:.2f}s against a :{target_seconds} "
                               f"slot, inside ±{tol:g}s." + compressed,
                               off=off, speed=rate))
        else:
            way = "over" if off > 0 else "under"
            checks.append(_row("length_match", "Mix lands on the clock", "block",
                               f"{mixed_seconds:.2f}s is {abs(off):.2f}s {way} the "
                               f":{target_seconds} slot, outside ±{tol:g}s. A station "
                               "rejects the file rather than trimming it."
                               + compressed, off=off, speed=rate))

    # 2. Word count. A warning, because the mix length above is the real
    #    constraint and this is the proxy for it before one exists.
    if words is None or words_low is None or words_high is None:
        checks.append(_row("word_count", "Script fits the slot", "not_measured",
                           "No word budget is on file for this slot."))
    elif words_low <= words <= words_high:
        checks.append(_row("word_count", "Script fits the slot", "pass",
                           f"{words} words, inside the {words_low}-{words_high} "
                           "budget for this slot.", words=words))
    else:
        way = "over" if words > words_high else "under"
        checks.append(_row("word_count", "Script fits the slot", "warn",
                           f"{words} words is {way} the {words_low}-{words_high} "
                           "budget. The measured read is what decides it.",
                           words=words))

    # 3. and 4. The call to action, and whether it lands late enough to stick.
    cta = find_cta(script)
    if cta["found"]:
        named = ", ".join(CTA_KINDS.get(k, k) for k in cta["kinds"])
        quoted = ", ".join(f"“{e['text']}”" for e in cta["evidence"][:3])
        checks.append(_row("cta_present", "The spot says what to do next", "pass",
                           f"Carries {named}: {quoted}.", evidence=cta["evidence"]))
        share = cta_share(script, cta["last_offset"])
        if share is None:
            checks.append(_row("cta_position", "The call to action lands late",
                               "not_measured",
                               "Where it falls in the read could not be worked out."))
        elif share >= CTA_TAIL_SHARE:
            checks.append(_row("cta_position", "The call to action lands late", "pass",
                               f"The last one falls {round(share * 100)}% of the way "
                               "through, inside the closing fifth.", share=share))
        else:
            checks.append(_row("cta_position", "The call to action lands late", "warn",
                               f"The last one falls {round(share * 100)}% of the way "
                               "through, so the read carries on past it. A number "
                               "said early and not repeated is the one nobody "
                               "remembers.", share=share))
    else:
        checks.append(_row("cta_present", "The spot says what to do next", "block",
                           "No phone number, web address or code anywhere in the "
                           "read. A listener has nothing to act on."))
        checks.append(_row("cta_position", "The call to action lands late",
                           "not_measured",
                           "There is no call to action to place."))

    # 5. The bed's provenance. This is the honest form of the spec's "bed is
    #    licensed" check: there is no cleared-track catalog here, so what
    #    protects the client is that the bed is real audio we can account for.
    checks.append(_bed_check(bed, vo_only=vo_only))

    # 6. Reserved. Loudness and clipping need a decoder this runtime does not
    #    have, so the row says so rather than being absent -- an absent row is
    #    a report shape that changes the day somebody adds the check.
    checks.append(_row("vo_clarity", "Voice is clean and unclipped", "not_measured",
                       "Not measured: loudness and clipping need an audio decoder, "
                       "and there is none in this runtime. Listen to the mix.",
                       reserved=True))

    blocking = [c["id"] for c in checks if c["level"] == "block"]
    warnings = [c["id"] for c in checks if c["level"] == "warn"]
    unmeasured = [c["id"] for c in checks if c["level"] == "not_measured"]
    # `measured` answers "did every check that could run, run" -- so the
    # reserved row is out of it. Counted in, the flag is False on every spot
    # ever built and therefore says nothing, which is the assertion that
    # cannot fail wearing a QC report.
    pending = [c["id"] for c in checks
               if c["level"] == "not_measured" and not c.get("reserved")]
    status = "blocked" if blocking else ("warn" if warnings else "pass")
    return {"checks": checks, "blocking": blocking, "warnings": warnings,
            "not_measured": unmeasured, "status": status,
            "measured": not pending, "pending": pending, "tolerance_s": tol}


def _bed_check(bed: dict | None, *, vo_only: bool = False) -> dict:
    """Whether the bed under this voice is real audio with a source on it."""
    label = "The bed is real audio"
    if vo_only or not bed:
        # A straight read with no bed is an ordinary radio spot -- a sponsor
        # mention, a news-style read -- and refusing it would be a check
        # blocking the correct thing.
        return _row("bed_source", label, "pass",
                    "No bed: a straight voice read. Nothing to account for.")
    kind = str(bed.get("kind") or "").strip()
    if bed.get("mock"):
        return _row("bed_source", label, "block",
                    "Mock mode composed no audio, so this bed is a description "
                    "with nothing behind it. Filing it would deliver a spot "
                    "that is silent under the voice.")
    if not bed.get("audio_url"):
        return _row("bed_source", label, "block",
                    "This bed is a written description that was never composed. "
                    "Compose it, or upload a track.")
    if not kind:
        return _row("bed_source", label, "block",
                    "This bed has audio but no record of where it came from, so "
                    "nothing here can say what is going out under the voice.")
    origin = {"composed": "composed by ElevenLabs on this project",
              "upload": "uploaded by whoever built the spot"}.get(kind, kind)
    return _row("bed_source", label, "pass", f"Real audio, {origin}.", kind=kind)
