"""Smart 1 Hub — the radio spot's rules, as data.

`modules/radio_promo` writes, casts and records radio commercials; this is the
half of that job that is not about any one tool. It carries the bed
vocabulary, the mix levels, the length arithmetic, the QC checks and the one
honest way to measure a finished file, so `modules/fan_radio` can read the
same rules later without a second copy of them being written first.

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


def compose_bed(prompt: str, seconds) -> dict:
    """One composed bed at the spot's own length, or a reason. Never raises.

    Everything expensive about this -- the content-keyed cache on the shared
    disk, the per-generation metering, the refusal that keeps its row -- is
    the audio service's and is inherited rather than repeated.
    """
    audio, error = _cb_audio()
    if not audio:
        return {"audio_bytes": None, "seconds": None, "error": error}
    length = bed_length_ms(seconds)
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
       bed: dict | None = None, vo_only: bool = False) -> dict:
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
        if abs(off) <= tol:
            checks.append(_row("length_match", "Mix lands on the clock", "pass",
                               f"{mixed_seconds:.2f}s against a :{target_seconds} "
                               f"slot, inside ±{tol:g}s.", off=off))
        else:
            way = "over" if off > 0 else "under"
            checks.append(_row("length_match", "Mix lands on the clock", "block",
                               f"{mixed_seconds:.2f}s is {abs(off):.2f}s {way} the "
                               f":{target_seconds} slot, outside ±{tol:g}s. A station "
                               "rejects the file rather than trimming it.", off=off))

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
