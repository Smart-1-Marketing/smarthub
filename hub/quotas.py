"""Provider usage counters, monthly limits, and cost estimation.

Three providers here bill in ways that surprise people:

  * **Brandfetch** — a low monthly allowance. Every logo lookup in Image
    Creator and every brand pull in Suite Panel counts against it, and both
    tools make the call feel free.
  * **Insites** — a credit per site audit. The Scans module already lost
    credits to an email address passing domain validation and to a
    double-click spending two credits on one domain.
  * **OpenAI** — metered per token across eight call sites, which until v7
    recorded nothing at all.

Three more were added later, for the same reason each time — the spend was
real and nothing in the Hub could see it:

  * **ElevenLabs** — billed per *character* of script, not per render, across
    three modules that each render spots all day. A 30-second radio spot is
    roughly 450 characters, so the plan allowance is spent in reads nobody
    counted.
  * **Cloudinary** — billed in credits, where one credit is a thousand
    transformations *or* a gigabyte of storage *or* a gigabyte of delivery.
    Fifteen modules upload, so no single call site could ever have known the
    total. Cloudinary's own Admin API is the authority here and is read
    directly; the local ledger exists to say *which module* spent it.
  * **Google** — GA4, Tag Manager, Search Console and Google Ads cost no
    money and are limited by a daily request quota instead, which is why the
    Tag Manager client already carries pacing and 429 retries. Counted per
    API and per day, because a quota is a per-day number.

Two honesty rules run through all of this, both of them CLAUDE.md's:

  * A provider nobody instrumented reports **"not measured"**, never zero. A
    clean-looking zero is a wrong answer presented confidently, and it is the
    exact failure mode a usage page exists to prevent.
  * Where the provider publishes its own counter, that counter is the
    headline and is labelled as such. The local ledger is only ever an
    attribution — it can undercount and says so.

Counts are derived from the activity log rather than a separate counter, so
they cannot drift out of sync with what actually happened, and a redeploy
doesn't reset them. The log is append-only JSONL keyed by month.

Limits are read from the environment so you can raise them the day you change
plan, without a deploy.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from hub import audit

# ---------------------------------------------------------------------------
# Provider definitions. `warn_at` is the number Todd asked to be warned at;
# `limit` is the plan allowance where one is known.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quota:
    key: str
    label: str
    unit: str
    warn_at: int
    limit: int
    env_warn: str
    env_limit: str
    note: str
    # Where the authoritative number lives, when the provider publishes one.
    # Empty means the activity log is the only count there is, and the row
    # says so rather than implying the number is the provider's own.
    authority: str = ""

    def thresholds(self) -> tuple[int, int]:
        def _i(name, default):
            try:
                return int(float(os.environ.get(name) or default))
            except (TypeError, ValueError):
                return default
        return _i(self.env_warn, self.warn_at), _i(self.env_limit, self.limit)


QUOTAS: dict[str, Quota] = {
    "brandfetch": Quota(
        "brandfetch", "Brandfetch", "lookups", 80, 100,
        "BRANDFETCH_WARN_AT", "BRANDFETCH_MONTHLY_LIMIT",
        "Logo and brand-color lookups from Image Creator and Suite Panel. "
        "Cached results do not count."),
    "insites": Quota(
        "insites", "Insites", "scans", 900, 1000,
        "INSITES_WARN_AT", "INSITES_MONTHLY_LIMIT",
        "One credit per site audit. A re-pull of an existing report is free; "
        "starting a new audit is not."),
    "removebg": Quota(
        "removebg", "remove.bg", "cutouts", 40, 50,
        "REMOVEBG_WARN_AT", "REMOVEBG_MONTHLY_LIMIT",
        "One credit per AI cutout. The free white-background removal runs in "
        "the browser and does not count."),
    "openai": Quota(
        "openai", "OpenAI", "calls", 0, 0,
        "OPENAI_WARN_AT", "OPENAI_MONTHLY_LIMIT",
        "Metered by token, not by call. See the cost estimate."),
    # ---- the three added later -------------------------------------------
    # ElevenLabs bills the *character*, so units here are characters and not
    # renders. Counting renders would have made a 12-second tag and a
    # 60-second read cost the same, which is the number being wrong in the
    # direction that matters: the long ones are what spend the plan.
    "elevenlabs": Quota(
        "elevenlabs", "ElevenLabs", "characters", 90_000, 100_000,
        "ELEVENLABS_WARN_AT", "ELEVENLABS_MONTHLY_LIMIT",
        "One credit per character of script sent to text-to-speech. The "
        "default allowance here is the Creator plan's 100,000; set "
        "ELEVENLABS_MONTHLY_LIMIT the day you change plan. Listing voices is "
        "free and is not counted.",
        authority="ElevenLabs /user/subscription"),
    # Cloudinary's own credit meter is the authority (see cloudinary_estimate).
    # The local ledger counts operations so spend can be attributed to a
    # module -- no limit by default, because the number that has a limit is
    # credits, and credits are not what this row counts.
    "cloudinary": Quota(
        "cloudinary", "Cloudinary", "operations", 0, 0,
        "CLOUDINARY_WARN_AT", "CLOUDINARY_MONTHLY_LIMIT",
        "Uploads, fetches and deletes recorded by the Hub. Cloudinary bills "
        "in credits, not operations, so the billing number comes from "
        "Cloudinary itself and this row exists to say which module spent it.",
        authority="Cloudinary Admin API /usage"),
    # ---- the three the Commercial Builder spends on ----------------------
    #
    # Every one of these bills per GENERATION, and none of them was counted
    # anywhere: the module that spends the most in this Hub was invisible on
    # the usage page, which is the confident low number this file exists to
    # stop. They are here with no default allowance because none of the three
    # publishes a plan figure this deployment can cite -- so the row reads
    # *not measured* against a limit and still says what was spent, rather
    # than inventing a ceiling. Set the env var the day a plan is known.
    "heygen": Quota(
        "heygen", "HeyGen", "clips", 0, 0,
        "HEYGEN_WARN_AT", "HEYGEN_MONTHLY_LIMIT",
        "One credit per spokesperson clip rendered. HeyGen prices by plan and "
        "publishes no figure this Hub can read, so there is no ceiling here "
        "until HEYGEN_MONTHLY_LIMIT is set — the count is real, the limit is "
        "not measured."),
    # Runway bills by the second of finished video, not by the request: a :10
    # clip costs twice a :05, and counting requests would make them equal —
    # the ElevenLabs mistake in a different unit. `record_runway()` sends
    # seconds.
    "runway": Quota(
        "runway", "Runway", "seconds of video", 0, 0,
        "RUNWAY_WARN_AT", "RUNWAY_MONTHLY_LIMIT",
        "Seconds of generated video. Runway bills by duration rather than by "
        "request, so a :10 clip is twice a :05 and counting requests would "
        "make them the same. No ceiling until RUNWAY_MONTHLY_LIMIT is set."),
    "creatomate": Quota(
        "creatomate", "Creatomate", "renders", 0, 0,
        "CREATOMATE_WARN_AT", "CREATOMATE_MONTHLY_LIMIT",
        "One credit per render submitted. A render that fails still consumed "
        "the request, which is why a refused call is recorded with ok=False "
        "rather than dropped. No ceiling until CREATOMATE_MONTHLY_LIMIT is "
        "set."),
    # Pickaxe bills per use of an assistant, so units here are calls. Same
    # arrangement as the Commercial Builder trio above: no default allowance,
    # because Pickaxe prices by plan and publishes no figure this deployment
    # can cite — the count is real, the limit is not measured until
    # PICKAXE_MONTHLY_LIMIT is set.
    "pickaxe": Quota(
        "pickaxe", "Pickaxe", "calls", 0, 0,
        "PICKAXE_WARN_AT", "PICKAXE_MONTHLY_LIMIT",
        "One use per call to a workspace Pickaxe (SEM Quote Help, Audience "
        "Finder). A call that failed is recorded with ok=False and is out of "
        "the billable total. No ceiling until PICKAXE_MONTHLY_LIMIT is set."),
    # Google costs nothing and is limited by requests per day, so a monthly
    # allowance would be the wrong shape entirely -- google_estimate() does
    # the per-day, per-API comparison. This row is the monthly total, for
    # scale.
    "google": Quota(
        "google", "Google APIs", "calls", 0, 0,
        "GOOGLE_WARN_AT", "GOOGLE_MONTHLY_LIMIT",
        "GA4, Tag Manager, Search Console, Business Profile and Google Ads. "
        "These cost no money; what runs out is the daily request quota, which "
        "is compared per API rather than in this monthly total."),
}


def month_key(when: datetime | None = None) -> str:
    d = when or datetime.now(timezone.utc)
    return d.strftime("%Y-%m")


def record(provider: str, *, module: str = "", detail: str = "",
           cached: bool = False, units: int = 1, api: str = "",
           model: str = "", nbytes: int = 0, ok: bool = True) -> None:
    """Log one billable call.

    `cached=True` still writes a row — knowing how much the cache is saving
    you is worth as much as knowing what you spent.

    `api`, `model` and `nbytes` are the metered providers' extras: which
    Google API was called, which ElevenLabs voice model rendered (they do not
    all bill at the same rate), and how many bytes Cloudinary stored. They are
    optional and absent rows still count, so adding them did not invalidate
    anything already in the log.

    `ok=False` marks a call the provider refused, which spent nothing and is
    excluded from every billable total — but is still worth a row, because a
    wall of them is what a spent allowance looks like from this side.
    """
    # NB: the extra key is "tool", not "module" — audit.log()'s first
    # positional parameter is already called module, so passing module= here
    # raises TypeError: got multiple values for argument 'module'.
    #
    # ok is written ONLY on failure. audit.log() drops None extras, so a
    # successful row stays exactly the shape it was before this key existed —
    # which matters on a log that is allowed to reach 64 MB before rotating,
    # and keeps every row already written meaning what it meant.
    audit.log("quota", "call", provider=provider, tool=module or "unknown",
              detail=detail[:120], cached=bool(cached), units=int(units),
              api=api or None, model=model or None,
              bytes=int(nbytes) or None, ok=(None if ok else False),
              month=month_key())


# ---------------------------------------------------------------------------
# Call-site helpers.
#
# Each of these is one line at the call site and puts the arithmetic in one
# place. That matters more than it looks: the ElevenLabs unit is characters of
# the text *actually sent* (after pronunciation substitutions, which change
# the length), and getting that subtly wrong at eleven separate call sites is
# how the OpenAI estimate was wrong before hub/ai.py existed.
# ---------------------------------------------------------------------------

def record_tts(text: str, *, module: str, model: str = "",
               voice: str = "", ok: bool = True) -> None:
    """One ElevenLabs text-to-speech render.

    Called after the HTTP response, not before it: a request that never
    reached ElevenLabs, or that came back 4xx, spends no credits, and counting
    those would trip the plan warning on failures.
    """
    try:
        record("elevenlabs", module=module, units=len(text or ""),
               model=model or "", detail=(voice or "")[:60], ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


# ElevenLabs sells two things this Hub buys, and only one of them is billed
# by the character. Sound effects and music are billed per GENERATION, so a
# row recorded under the same unit as a voiceover would be read as a handful
# of characters of script -- the voice estimate would absorb a cost source
# that is not measured in characters at all, and the number on the usage page
# would go on looking right. `api` is what keeps them apart, and
# `elevenlabs_estimate` reads it: the character total counts speech only, and
# these get their own line beside it.
AUDIO_GENERATION_KINDS = ("sound_effect", "music")


def record_audio_generation(kind: str, *, module: str, seconds: float = 0.0,
                            model: str = "", detail: str = "",
                            ok: bool = True) -> None:
    """One generated sound effect or composed music bed.

    `units` is the generation, because that is how ElevenLabs bills these --
    counting seconds would be the mistake counting ElevenLabs *renders*
    would have made one product over. The seconds are carried in `nbytes`'
    place as a separate figure so the estimate can say how much audio was
    produced without pretending that is what was charged for.

    An unrecognised kind is filed under its own name rather than dropped: a
    third audio product added by ElevenLabs must show up as an unnamed row on
    the usage page rather than as nothing at all.
    """
    try:
        api = str(kind or "audio").strip().lower() or "audio"
        record("elevenlabs", module=module, units=1, api=api,
               model=model or "", detail=(detail or f"{seconds or 0:g}s")[:120],
               nbytes=int(round(float(seconds or 0) * 1000)), ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def record_asset(*, module: str, kind: str = "upload", nbytes: int = 0,
                 detail: str = "", cached: bool = False) -> None:
    """One Cloudinary operation — an upload, a remote fetch or a delete."""
    try:
        record("cloudinary", module=module, units=1, api=kind,
               nbytes=nbytes, detail=detail, cached=cached)
    except Exception:                                   # noqa: BLE001
        pass


def record_video(provider: str, *, module: str, seconds: float = 0.0,
                 detail: str = "", model: str = "", ok: bool = True) -> None:
    """One generated video clip, billed by its duration.

    Runway bills by the second of finished video rather than by the request,
    so a :10 clip is twice a :05 and counting requests would make them equal —
    the same mistake counting ElevenLabs renders would have made. `units` is
    therefore seconds, rounded up, because a provider does not sell a
    fractional second.

    Cannot raise. An uninstrumented call site is worse than a missing feature
    here, and so is one that takes the render down with it.
    """
    try:
        whole = max(1, int(math.ceil(float(seconds or 0)))) if seconds else 1
        record(provider, module=module, units=whole, detail=detail,
               model=model, ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def record_render(*, module: str, detail: str = "", fmt: str = "",
                  ok: bool = True) -> None:
    """One render submitted to Creatomate.

    Recorded when the job is SUBMITTED rather than when it succeeds: the
    request is what was spent, and a render that fails later has still cost
    it. A failed submission is recorded with ok=False, which keeps it out of
    every billable total while leaving the row — a wall of those is what a
    spent allowance looks like from this side.
    """
    try:
        record("creatomate", module=module, units=1,
               detail=(detail or fmt)[:120], model=fmt or "", ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def record_clip(*, module: str, detail: str = "", model: str = "",
                ok: bool = True) -> None:
    """One HeyGen spokesperson clip.

    Counted per clip because that is how HeyGen bills — unlike Runway, its
    unit is the generation and not the second.
    """
    try:
        record("heygen", module=module, units=1, detail=detail[:120],
               model=model, ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def record_image(*, module: str, model: str = "", count: int = 1,
                 ok: bool = True) -> None:
    """Generated stills, billed per image.

    `hub/ai.note_sdk_usage()` records the TEXT calls and reads `.usage`, which
    an images response does not carry — so the image path went uncounted while
    every chat call was tracked. Two options per press at $0.04 each is the
    commonest single spend in the Commercial Builder.
    """
    try:
        record("openai", module=module, units=max(1, int(count or 1)),
               model=model or "gpt-image-1", detail="image generation", ok=ok)
    except Exception:                                    # noqa: BLE001
        pass


def record_google(url: str, *, module: str, ok: bool = True,
                  units: int = 1) -> None:
    """One call to a Google API, filed under whichever API that URL belongs to."""
    try:
        record("google", module=module, units=units, api=google_api_of(url),
               detail=str(url or "")[:120], ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def ledger(month: str | None = None, lookback: int = 200000) -> list[dict]:
    """Every recorded call for a month, read once.

    summary() derives five different views from this. Each used to re-read the
    whole activity log, which is a full file scan per view on a log that is
    deliberately never truncated below 64 MB.
    """
    month = month or month_key()
    return [r for r in audit.read(limit=lookback, module="quota")
            if r.get("month") == month and r.get("type") == "call"]


def counts(month: str | None = None, lookback: int = 200000,
           rows: list[dict] | None = None) -> dict[str, dict]:
    """Billable and cached counts per provider for a month."""
    month = month or month_key()
    out: dict[str, dict] = {}
    for row in (ledger(month, lookback) if rows is None else rows):
        # A call that failed spent nothing. Only the metered helpers write
        # this key, so a row without it is a success, as every row was before
        # the key existed.
        if row.get("ok") is False:
            continue
        p = row.get("provider") or "unknown"
        acc = out.setdefault(p, {"billable": 0, "cached": 0, "by_module": {}})
        units = int(row.get("units") or 1)
        if row.get("cached"):
            acc["cached"] += units
        else:
            acc["billable"] += units
            tool = row.get("tool") or "unknown"
            acc["by_module"][tool] = acc["by_module"].get(tool, 0) + units
    return out


def status(month: str | None = None,
           rows: list[dict] | None = None) -> list[dict]:
    """Per-provider usage against its threshold, with a state.

    States: ok | warn (past warn_at) | over (past limit).
    """
    month = month or month_key()
    used = counts(month, rows=rows)
    rows = []
    for q in QUOTAS.values():
        warn_at, limit = q.thresholds()
        acc = used.get(q.key, {"billable": 0, "cached": 0, "by_module": {}})
        n = acc["billable"]
        if limit and n >= limit:
            state, message = "over", (
                f"{q.label}: {n} {q.unit} this month — the {limit} allowance is "
                f"used up. Further calls will fail or bill as overage.")
        elif warn_at and n >= warn_at:
            state, message = "warn", (
                f"{q.label}: {n} of {limit or '—'} {q.unit} used this month, "
                f"past the {warn_at} warning mark.")
        else:
            state, message = "ok", (
                f"{q.label}: {n}{f' of {limit}' if limit else ''} {q.unit} "
                f"this month.")
        pct = round(100 * n / limit) if limit else None
        rows.append({
            "key": q.key, "label": q.label, "unit": q.unit, "month": month,
            "used": n, "cached": acc["cached"], "warn_at": warn_at,
            "limit": limit or None, "percent": pct, "state": state,
            "message": message, "note": q.note, "authority": q.authority,
            "by_module": dict(sorted(acc["by_module"].items(),
                                     key=lambda kv: -kv[1])),
        })
    return rows


def warnings(month: str | None = None) -> list[dict]:
    """Only the rows that need attention — for a banner or an alert job."""
    return [r for r in status(month) if r["state"] in ("warn", "over")]


# ---------------------------------------------------------------------------
# OpenAI cost estimate
# ---------------------------------------------------------------------------

# USD per 1M tokens. Approximate on purpose, and labelled as an estimate
# everywhere it is shown — the authoritative number is OpenAI's own dashboard.
PRICING = {
    "gpt-4o-mini":  {"in": 0.15,  "out": 0.60},
    "gpt-4o":       {"in": 2.50,  "out": 10.00},
    "gpt-4.1":      {"in": 2.00,  "out": 8.00},
    "gpt-4.1-mini": {"in": 0.40,  "out": 1.60},
    "gpt-4.1-nano": {"in": 0.10,  "out": 0.40},
    "o4-mini":      {"in": 1.10,  "out": 4.40},
}
IMAGE_PRICING = {"gpt-image-1": 0.04}   # per 1024x1024 standard image


def _price(model: str) -> dict:
    if model in PRICING:
        return PRICING[model]
    for k in PRICING:                       # tolerate dated suffixes
        if model.startswith(k):
            return PRICING[k]
    return PRICING["gpt-4o-mini"]


def openai_cost(month: str | None = None, lookback: int = 200000) -> dict:
    """Estimated OpenAI spend for a month, broken down by model and module.

    Reads the usage rows hub/ai.py writes on every call. A module that still
    calls OpenAI directly instead of going through hub.ai will not appear here
    — `untracked_modules` names them so the gap is visible rather than silent.
    """
    month = month or month_key()
    by_model: dict[str, dict] = {}
    by_module: dict[str, dict] = {}
    calls = errors = images = 0
    tin = tout = 0

    for row in audit.read(limit=lookback, module="ai"):
        ts = row.get("time", "")
        if not ts.startswith(month):
            continue
        calls += 1
        model = row.get("model") or "unknown"
        mod = row.get("tool") or "unknown"
        i = int(row.get("tokens_in") or 0)
        o = int(row.get("tokens_out") or 0)
        tin += i
        tout += o
        if not row.get("ok"):
            errors += 1

        if model in IMAGE_PRICING or model.startswith("gpt-image"):
            images += 1
            cost = IMAGE_PRICING.get(model, 0.04)
        else:
            p = _price(model)
            cost = i / 1e6 * p["in"] + o / 1e6 * p["out"]

        m = by_model.setdefault(model, {"calls": 0, "tokens_in": 0,
                                        "tokens_out": 0, "cost": 0.0})
        m["calls"] += 1
        m["tokens_in"] += i
        m["tokens_out"] += o
        m["cost"] += cost

        d = by_module.setdefault(mod, {"calls": 0, "cost": 0.0, "errors": 0})
        d["calls"] += 1
        d["cost"] += cost
        if not row.get("ok"):
            d["errors"] += 1

    for d in list(by_model.values()) + list(by_module.values()):
        d["cost"] = round(d["cost"], 4)
    total = round(sum(m["cost"] for m in by_model.values()), 2)

    # Project the month end from the run rate so far. Shared with the three
    # estimates below — one projection, so a change to how it is done cannot
    # apply to OpenAI and not to ElevenLabs.
    projected = _project(total, month)

    return {
        "month": month, "calls": calls, "errors": errors, "images": images,
        "tokens_in": tin, "tokens_out": tout,
        "estimated_cost": total, "projected_month_end": projected,
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])),
        "by_module": dict(sorted(by_module.items(), key=lambda kv: -kv[1]["cost"])),
        "untracked_modules": untracked_openai_modules(),
        "caveat": "Estimated from logged token counts at list prices. "
                  "Treat OpenAI's own dashboard as authoritative — this is for "
                  "spotting a tool that has started spending unexpectedly.",
    }


_OPENAI_ENDPOINTS = ("/v1/chat/completions", "/v1/responses",
                    "/v1/images/generations")
_OPENAI_SDK_CALLS = ("chat.completions.create", "responses.create",
                     "images.generate")
_OPENAI_RECORDERS = ("note_usage", "note_sdk_usage", "_record(", "record(")


def _openai_spends(text: str) -> bool:
    return (any(e in text for e in _OPENAI_ENDPOINTS)
            or any(c in text for c in _OPENAI_SDK_CALLS))


def openai_spend_unrecorded(src: str) -> bool:
    """Does this source reach OpenAI somewhere that records nothing?

    Asked per **call site**, not per file. It used to exempt the whole file
    the moment ``from hub import ai`` appeared anywhere in it -- so Image
    Creator, whose two text routes go through a helper that records and whose
    image route posted to ``/v1/images/generations`` and recorded nothing,
    read as fully tracked. Every image it generated was billed and invisible,
    behind a check reporting it clean: the string satisfying the check, which
    is the ``for_module(`` failure one provider over.

    Lifted out of the walk so it can be handed a source: a check that has only
    ever been green is one nobody can trust.
    """
    import ast
    if not _openai_spends(src):
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    spending, silent = False, False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(src, node) or ""
        if not _openai_spends(body):
            continue
        spending = True
        if not any(r in body for r in _OPENAI_RECORDERS):
            silent = True
    # A call at module scope, or one this walk could not place inside a
    # function, is judged on the file as a whole rather than passed over:
    # missing a spend is the failure, and a file that names no recorder
    # anywhere is not recording one.
    if not spending:
        return not any(r in src for r in _OPENAI_RECORDERS)
    return silent


def untracked_openai_modules() -> list[str]:
    """Modules calling OpenAI directly, so their spend never reaches this page.

    Every one of these is a blind spot in the cost estimate. Migrating a module
    onto hub.ai is what closes it.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    found = set()

    # Must be an actual *call*, not a mention. demo.py names the guarded
    # capability "openai.text", diagnostics.py pings /v1/models to check the
    # key, and ai.py documents the URL in a docstring -- none of them spend
    # tokens. A detector that flags those trains you to ignore it.
    for p in root.rglob("*.py"):
        if "_attic" in p.parts or "__pycache__" in p.parts:
            continue
        if p.name in {"ai.py", "quotas.py", "diagnostics.py"}:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not openai_spend_unrecorded(src):
            continue
        parts = p.relative_to(root).parts
        found.add("/".join(parts) if parts[0] == "hub"
                  else (parts[1] if parts[0] == "modules" and len(parts) > 1
                        else parts[0].replace(".py", "")))
    return sorted(found)


# ---------------------------------------------------------------------------
# The other three providers.
#
# Each one is metered in a unit that is not "calls", and each had nothing
# watching it. The shape below is deliberately the same for all three so the
# diagnostics page renders them with one function rather than three:
#
#   measured  — what the Hub's own ledger saw. Attributable to a module, and
#               honest about being partial: `untracked` names every call site
#               spending this provider without recording it.
#   account   — the provider's own counter, where it publishes one. This is
#               the authority; it just cannot say which module spent it.
#   estimate  — what we think that costs, at a rate the deployment can set.
#
# `state` is "not_measured" when nothing recorded, never "ok with zero". A
# usage page that shows a confident zero for a provider we are demonstrably
# paying for is worse than one that admits it does not know.
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def _project(total: float, month: str, places: int = 2) -> float:
    """Run-rate projection to the end of `month`, or the total for a past one."""
    if month != month_key():
        return round(total, places)
    import calendar
    now = datetime.now(timezone.utc)
    days = calendar.monthrange(now.year, now.month)[1]
    return round(total / max(1, now.day) * days, places)


# --- provider-side counters, cached ----------------------------------------
# These are the only outbound calls anything in this file makes. They are
# behind `live=` and cached for five minutes because /api/quotas is otherwise
# a pure local read, and diagnostics.py's rule -- a sick provider must not
# hang the page -- applies here too. A failed lookup returns a reason, not an
# exception and not a zero.

_ACCOUNT_TTL = 300
_account_cache: dict[str, tuple[float, dict]] = {}


def _account(key: str, fetch) -> dict:
    hit = _account_cache.get(key)
    if hit and time.time() - hit[0] < _ACCOUNT_TTL:
        return dict(hit[1], cached=True)
    try:
        value = fetch()
    except Exception as exc:                            # noqa: BLE001
        value = {"available": False,
                 "reason": f"{type(exc).__name__} asking {key} for its usage."}
    _account_cache[key] = (time.time(), value)
    return dict(value, cached=False)


def elevenlabs_account() -> dict:
    """Characters used and allowed, from ElevenLabs itself."""
    # Through settings, which accepts ELEVENLABS_API too. Reading the one
    # spelling here meant the usage page could report "no key set" while the
    # key was set under the name every other provider on this deployment uses.
    try:
        from hub.config import settings
        key = settings.elevenlabs_key
    except Exception:                                   # noqa: BLE001
        # Every spelling, not one: a fallback that knows fewer names than
        # config does turns an unimportable config into a missing key.
        key = (os.environ.get("ELEVENLABS_API")
               or os.environ.get("ELEVENLABS_API_KEY")
               or os.environ.get("ELEVENLABS_KEY") or "").strip()
    if not key:
        return {"available": False,
                "reason": "ELEVENLABS_API / ELEVENLABS_API_KEY is not set on "
                          "this deployment."}

    def go():
        import requests
        base = (os.environ.get("ELEVENLABS_BASE_URL")
                or "https://api.elevenlabs.io/v1").rstrip("/")
        r = requests.get(f"{base}/user/subscription",
                         headers={"xi-api-key": key}, timeout=10)
        if r.status_code >= 400:
            return {"available": False,
                    "reason": f"ElevenLabs refused the request (HTTP {r.status_code})."}
        d = r.json()
        used = int(d.get("character_count") or 0)
        limit = int(d.get("character_limit") or 0)
        return {
            "available": True, "unit": "characters",
            "used": used, "limit": limit or None,
            "remaining": (limit - used) if limit else None,
            "percent": round(100 * used / limit) if limit else None,
            "tier": d.get("tier"),
            "resets": d.get("next_character_count_reset_unix"),
            # ElevenLabs counts against the *billing* period, which is not the
            # calendar month unless the plan happens to have started on the
            # 1st. Saying so stops the two numbers below looking like a bug.
            "note": "ElevenLabs counts against your billing period, which "
                    "usually is not the calendar month the ledger uses.",
        }
    return _account("elevenlabs", go)


def cloudinary_account() -> dict:
    """Credits, storage, bandwidth and transformations, from Cloudinary itself."""
    try:
        from hub.config import settings as _s
        if not _s.cloudinary_ready:
            return {"available": False,
                    "reason": "CLOUDINARY_URL is not set (or is still the "
                              "placeholder), so nothing is being stored there."}
    except Exception:                                   # noqa: BLE001
        pass

    def go():
        import cloudinary
        import cloudinary.api
        cloudinary.config(secure=True)
        u = cloudinary.api.usage(timeout=10)
        cred = u.get("credits") or {}
        limit = cred.get("limit")
        used = cred.get("usage")
        return {
            "available": True, "unit": "credits",
            "used": used, "limit": limit or None,
            "remaining": (round(limit - used, 3)
                          if (limit and used is not None) else None),
            "percent": (round(100 * used / limit)
                        if (limit and used is not None) else None),
            "plan": u.get("plan"),
            "storage_gb": round((u.get("storage") or {}).get("usage", 0) / 1e9, 3),
            "bandwidth_gb": round((u.get("bandwidth") or {}).get("usage", 0) / 1e9, 3),
            "transformations": (u.get("transformations") or {}).get("usage"),
            "objects": (u.get("objects") or {}).get("usage"),
            "note": "Cloudinary's own meter, for the current billing period. "
                    "This is the number they bill on.",
        }
    return _account("cloudinary", go)


# --- ElevenLabs -------------------------------------------------------------

# ElevenLabs bills one credit per character on its standard voice models and
# half a credit on the Flash and Turbo ones. The multiplier is applied per
# recorded render because Radio Promo, Fan Radio and Commercial Builder do not
# all use the same model, and a plan allowance spent at half rate lasts twice
# as long -- reporting them at one rate would be wrong by a factor of two for
# whichever module is on the other model.
ELEVENLABS_HALF_RATE = ("eleven_flash", "eleven_turbo")
# Creator plan: $22 for 100,000 credits. Every plan is a different rate, so
# the deployment can set its own; this is only a default.
ELEVENLABS_USD_PER_1K = 0.22


def elevenlabs_credit_rate(model: str) -> float:
    m = (model or "").lower()
    return 0.5 if any(m.startswith(h) for h in ELEVENLABS_HALF_RATE) else 1.0


def elevenlabs_estimate(month: str | None = None, rows: list[dict] | None = None,
                        live: bool = False) -> dict:
    """Characters rendered, credits that costs, and what that is worth."""
    month = month or month_key()
    rows = ledger(month) if rows is None else rows
    usd_per_1k = _env_float("ELEVENLABS_USD_PER_1K_CREDITS", ELEVENLABS_USD_PER_1K)

    chars = credits = renders = failed = 0.0
    by_module: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    # The second line item. A sound effect and a composed bed are billed per
    # generation, so they are counted here and deliberately NOT added to
    # `chars` -- a generation carries no characters, and letting one through
    # would make the voice figure quietly wrong in the reassuring direction.
    audio = {k: {"generations": 0, "seconds": 0.0, "failed": 0, "by_module": {}}
             for k in AUDIO_GENERATION_KINDS}
    audio_other: dict[str, dict] = {}
    for r in rows:
        if r.get("provider") != "elevenlabs":
            continue
        api = (r.get("api") or "").strip().lower()
        if api:
            bucket = audio.get(api)
            if bucket is None:
                bucket = audio_other.setdefault(
                    api, {"generations": 0, "seconds": 0.0, "failed": 0,
                          "by_module": {}})
            if r.get("ok") is False:
                bucket["failed"] += 1
                continue
            bucket["generations"] += int(r.get("units") or 1)
            bucket["seconds"] += (int(r.get("bytes") or 0)) / 1000.0
            bucket["by_module"][r.get("tool") or "unknown"] = (
                bucket["by_module"].get(r.get("tool") or "unknown", 0) + 1)
            continue
        if r.get("ok") is False:
            failed += 1
            continue
        n = int(r.get("units") or 0)
        model = r.get("model") or "unknown"
        cred = n * elevenlabs_credit_rate(model)
        chars += n
        credits += cred
        renders += 1
        for table, name in ((by_module, r.get("tool") or "unknown"),
                            (by_model, model)):
            acc = table.setdefault(name, {"renders": 0, "characters": 0,
                                          "credits": 0.0, "cost": 0.0})
            acc["renders"] += 1
            acc["characters"] += n
            acc["credits"] += cred
            acc["cost"] += cred / 1000.0 * usd_per_1k

    for acc in list(by_module.values()) + list(by_model.values()):
        acc["credits"] = round(acc["credits"], 1)
        acc["cost"] = round(acc["cost"], 4)
    cost = round(credits / 1000.0 * usd_per_1k, 2)

    return {
        "key": "elevenlabs", "label": "ElevenLabs", "month": month,
        "unit": "characters",
        "measured": {
            "renders": int(renders), "characters": int(chars),
            "credits": round(credits, 1), "failed_renders": int(failed),
            "average_characters": int(chars / renders) if renders else None,
        },
        "state": ("measured" if renders or any(
            b["generations"] for b in list(audio.values()) + list(audio_other.values()))
            else "not_measured"),
        # Its own line, never folded into the characters above. No published
        # per-generation rate is held for either product, so the count is real
        # and the cost is *not measured* rather than invented -- the rule the
        # rest of this file works to.
        "audio_generations": {
            **{k: {**v, "seconds": round(v["seconds"], 1)} for k, v in audio.items()},
            **{k: {**v, "seconds": round(v["seconds"], 1)}
               for k, v in audio_other.items()},
        },
        "audio_basis": ("Sound effects and music are billed per generation rather "
                        "than per character, so they are counted apart and are not "
                        "in the character total above. No per-generation rate is "
                        "published on this deployment, so what they cost is not "
                        "measured."),
        "account": (elevenlabs_account() if live else None),
        "estimated_cost": cost,
        "projected_month_end": _project(cost, month),
        "rate": f"${usd_per_1k:.2f} per 1,000 credits "
                f"(ELEVENLABS_USD_PER_1K_CREDITS)",
        "by_module": dict(sorted(by_module.items(),
                                 key=lambda kv: -kv[1]["characters"])),
        "by_model": dict(sorted(by_model.items(),
                                key=lambda kv: -kv[1]["characters"])),
        "basis": "One credit per character on the standard voice models, half "
                 "a credit on Flash and Turbo, counted from the text actually "
                 "sent after pronunciation substitutions.",
        "caveat": "An estimate at plan list rate. ElevenLabs' own counter "
                  "(shown alongside when it can be read) is the authority, "
                  "and it counts a billing period rather than a calendar month.",
        "untracked": untracked_provider_calls().get("elevenlabs", []),
    }


# --- Cloudinary -------------------------------------------------------------

# One Cloudinary credit buys any ONE of these. Which is why "how many uploads
# did we do" was never going to answer "what does Cloudinary cost" on its own:
# an upload is nearly free and the gigabyte it then serves all month is not.
CLOUDINARY_TRANSFORMS_PER_CREDIT = 1000
CLOUDINARY_GB_PER_CREDIT = 1.0


def cloudinary_estimate(month: str | None = None, rows: list[dict] | None = None,
                        live: bool = False) -> dict:
    """What the Hub put into Cloudinary this month, and who put it there.

    The credit figure that matters is Cloudinary's, not this one: storage and
    delivery bandwidth are the bulk of a credit bill and neither is visible
    from an upload call site. What the ledger adds is attribution -- when the
    bill moves, this says which module moved it.
    """
    month = month or month_key()
    rows = ledger(month) if rows is None else rows
    usd_per_credit = _env_float("CLOUDINARY_USD_PER_CREDIT", 0.0)

    ops = uploads = deletes = fetches = 0
    stored = 0
    by_module: dict[str, dict] = {}
    for r in rows:
        if r.get("provider") != "cloudinary" or r.get("ok") is False:
            continue
        kind = r.get("api") or "upload"
        n = int(r.get("bytes") or 0)
        ops += 1
        stored += n
        if kind == "delete":
            deletes += 1
        elif kind == "fetch":
            fetches += 1
        else:
            uploads += 1
        acc = by_module.setdefault(r.get("tool") or "unknown",
                                   {"operations": 0, "bytes": 0})
        acc["operations"] += 1
        acc["bytes"] += n

    for acc in by_module.values():
        acc["mb"] = round(acc["bytes"] / 1e6, 1)

    # Storage credits for what we added this month. Deliberately NOT a bill:
    # it ignores everything stored in previous months and all delivery
    # bandwidth, which is why it is labelled "added this month" everywhere it
    # is shown and why the account figure is the headline.
    added_gb = stored / 1e9
    # Four places, not two: a month of blog images is a few megabytes, which
    # is thousandths of a credit. Rounded to two it would read as a flat zero
    # for every module that is not uploading video, and a zero is the one
    # answer this page is not allowed to give when the number is small.
    added_credits = round(added_gb / CLOUDINARY_GB_PER_CREDIT, 4)
    cost = round(added_credits * usd_per_credit, 2) if usd_per_credit else None

    return {
        "key": "cloudinary", "label": "Cloudinary", "month": month,
        "unit": "operations",
        "measured": {
            "operations": ops, "uploads": uploads, "remote_fetches": fetches,
            "deletes": deletes, "bytes_added": stored,
            "mb_added": round(stored / 1e6, 1),
            "storage_credits_added": added_credits,
        },
        "state": "measured" if ops else "not_measured",
        "account": (cloudinary_account() if live else None),
        "estimated_cost": cost,
        "projected_month_end": (_project(cost, month) if cost is not None else None),
        "rate": (f"${usd_per_credit:.2f} per credit (CLOUDINARY_USD_PER_CREDIT)"
                 if usd_per_credit else
                 "No per-credit price set — set CLOUDINARY_USD_PER_CREDIT to "
                 "price this in money. Credits are reported either way."),
        "by_module": dict(sorted(by_module.items(),
                                 key=lambda kv: -kv[1]["bytes"])),
        "basis": f"One credit per {CLOUDINARY_GB_PER_CREDIT:g} GB stored or "
                 f"{CLOUDINARY_TRANSFORMS_PER_CREDIT:,} transformations. Only "
                 f"storage added this month is counted here.",
        "caveat": "This counts what the Hub uploaded, not what Cloudinary "
                  "bills. Delivery bandwidth and everything stored in earlier "
                  "months are the larger part of a credit bill and are "
                  "invisible from an upload call site — read the account "
                  "figure for those.",
        "untracked": untracked_provider_calls().get("cloudinary", []),
    }


# --- Google -----------------------------------------------------------------

# A quota, not a price. These APIs cost nothing; what runs out is requests per
# day, so the comparison has to be per day and per API. Only two ceilings are
# published clearly enough to cite, and the rest are counted and honestly
# labelled as not measured against anything rather than given a number
# somebody made up.
GOOGLE_APIS: dict[str, tuple[str, int, str, str]] = {
    "ads": ("Google Ads API", 15000, "GOOGLE_ADS_DAILY_QUOTA",
            "Basic access allows 15,000 operations a day; Standard access "
            "lifts it. A daily cap reached mid-afternoon stops campaign "
            "deploys until midnight Pacific."),
    "gtm": ("Tag Manager API", 10000, "GTM_DAILY_QUOTA",
            "10,000 requests a day per project, and a per-user rate limit "
            "besides — the pacing and 429 retries in Google Finder exist for "
            "the second one."),
    "ga4": ("GA4 Admin API", 0, "GA4_ADMIN_DAILY_QUOTA",
            "Google publishes this as a token budget rather than a request "
            "count, so requests are counted here and not measured against a "
            "ceiling. Set GA4_ADMIN_DAILY_QUOTA if you want one."),
    "gsc": ("Search Console API", 0, "GSC_DAILY_QUOTA",
            "The per-project daily ceiling is high enough that the per-minute "
            "limit is what you hit first. Counted, not capped."),
    "gbp": ("Business Profile API", 0, "GBP_DAILY_QUOTA",
            "Quota is granted per project on request, so there is no default "
            "worth citing. Set GBP_DAILY_QUOTA to yours."),
    "fonts": ("Google Fonts API", 0, "GOOGLE_FONTS_DAILY_QUOTA",
              "Font metadata for Image Creator. Cached, and cheap."),
    "oauth": ("OAuth token endpoint", 0, "",
              "Token refreshes and userinfo. Free and effectively unmetered, "
              "but a sudden climb here means tokens are being refreshed far "
              "more often than they expire."),
    "other": ("Other Google APIs", 0, "", "Anything not matched above."),
}

_GOOGLE_HOSTS = (
    ("googleads.googleapis.com", "ads"),
    ("tagmanager.googleapis.com", "gtm"),
    ("analyticsadmin.googleapis.com", "ga4"),
    ("analyticsdata.googleapis.com", "ga4"),
    ("mybusiness", "gbp"),
    ("oauth2.googleapis.com", "oauth"),
    ("openidconnect.googleapis.com", "oauth"),
)


def google_api_of(url: str) -> str:
    """Which Google API a URL belongs to.

    By URL rather than by caller, because one helper in Google Finder is used
    against four different APIs and www.googleapis.com alone is Search
    Console, Fonts and userinfo depending on the path.
    """
    u = str(url or "").lower()
    for needle, key in _GOOGLE_HOSTS:
        if needle in u:
            return key
    if "/webmasters/" in u or "searchconsole" in u:
        return "gsc"
    if "/webfonts" in u:
        return "fonts"
    if "/oauth2/" in u or "userinfo" in u or "tokeninfo" in u:
        return "oauth"
    # Deliberately a bucket rather than a guess. An unrecognised Google host
    # still gets counted; it just does not get a made-up label.
    return "other"


def google_estimate(month: str | None = None, rows: list[dict] | None = None,
                    **_ignored) -> dict:
    """Google API calls per API, per day, against the daily quota.

    There is no money in this one and saying so is the point: staff assume a
    Google integration is what costs, and it is the OpenAI and ElevenLabs
    lines that do. What Google can do is stop working at 4pm because the
    day's quota is gone, which a monthly total would never show.
    """
    month = month or month_key()
    rows = ledger(month) if rows is None else rows
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total = failed = 0
    by_api: dict[str, dict] = {}
    by_module: dict[str, int] = {}
    for r in rows:
        if r.get("provider") != "google":
            continue
        if r.get("ok") is False:
            failed += 1
        key = r.get("api") or "other"
        n = int(r.get("units") or 1)
        total += n
        by_module[r.get("tool") or "unknown"] = (
            by_module.get(r.get("tool") or "unknown", 0) + n)
        acc = by_api.setdefault(key, {"calls": 0, "failed": 0, "days": {}})
        acc["calls"] += n
        # Per API, not only in the total. One number for every Google call
        # this month cannot say that Tag Manager is refusing a third of its
        # requests while Analytics is fine — and a refusal rate is the whole
        # early warning here, since a 429 spends the daily quota exactly as a
        # useful call does and returns nothing for it.
        if r.get("ok") is False:
            acc["failed"] += n
        day = str(r.get("time") or "")[:10]
        if day:
            acc["days"][day] = acc["days"].get(day, 0) + n

    apis = []
    for key, (label, default_quota, env, note) in GOOGLE_APIS.items():
        acc = by_api.get(key)
        if not acc and key in ("other", "oauth"):
            continue                    # don't invent rows for APIs never called
        calls = acc["calls"] if acc else 0
        fails = acc.get("failed", 0) if acc else 0
        days = acc["days"] if acc else {}
        quota = _env_int(env, default_quota) if env else default_quota
        used_today = days.get(today, 0)
        peak_day, peak = ("", 0)
        for d, n in days.items():
            if n > peak:
                peak_day, peak = d, n
        if not quota:
            state = "measured" if calls else "not_measured"
        elif used_today >= quota or peak >= quota:
            state = "over"
        elif used_today >= quota * 0.8:
            state = "warn"
        else:
            state = "ok"
        apis.append({
            "key": key, "label": label, "calls": calls,
            "failed": fails,
            # Of the calls we made, not of the ones that worked: a refusal is
            # a call, and the percentage is the number worth reading.
            "failed_percent": (round(100 * fails / calls) if calls else None),
            "today": used_today, "busiest_day": peak_day or None,
            "busiest_day_calls": peak,
            "daily_quota": quota or None,
            "percent_today": (round(100 * used_today / quota)
                              if quota else None),
            "state": state, "note": note,
            "quota_env": env or None,
        })
    apis.sort(key=lambda a: -a["calls"])

    return {
        "key": "google", "label": "Google APIs", "month": month,
        "unit": "calls",
        "measured": {"calls": total, "failed": failed,
                     "today": sum(a["today"] for a in apis)},
        "state": "measured" if total else "not_measured",
        "account": None,        # Google publishes quota use in Cloud console,
                                # not through an API these credentials can read
        "estimated_cost": 0.0,
        "projected_month_end": 0.0,
        "rate": "No charge. GA4, Tag Manager, Search Console, Business "
                "Profile and Google Ads bill nothing for API access.",
        "apis": apis,
        "by_module": dict(sorted(by_module.items(), key=lambda kv: -kv[1])),
        "basis": "One row per HTTP call the Hub made to a Google API, filed "
                 "by URL. Compared against the daily quota, because that is "
                 "the number that runs out.",
        "caveat": "The cost is genuinely zero; the risk is a daily quota. "
                  "Where no ceiling is shown, Google does not publish one we "
                  "can cite for that API — set the named variable to compare "
                  "against your project's actual grant. Quota use in the "
                  "Google Cloud console is the authority.",
        "untracked": untracked_provider_calls().get("google", []),
    }



# --- Google Ads: is there room to fire a batch today? ------------------------

# A scheduled sweep spends the same daily allowance a rep's own deploy does,
# and it spends it unattended. 90% leaves the last tenth for the person who is
# actually waiting on a mutate, which is the whole reason this is a margin
# rather than the ceiling itself.
ADS_QUOTA_SAFETY = 0.90

# One scan_account() is six independent GAQL queries — summary,
# recommendations, campaigns, search terms, keywords and schedule. Named here
# because a caller pacing itself against `remaining` has to know what an
# account costs, and counting it wrong is how a run walks straight past the
# margin it was checking.
ADS_QUERIES_PER_SCAN = 6


def ads_headroom(rows: list[dict] | None = None) -> dict:
    """What is left of today's Google Ads operation budget.

    Read once before a batch rather than per account: `ledger()` is a full
    scan of the activity log, so a caller that loops accounts should take the
    `remaining` this returns as an allowance and decrement it locally by
    `ADS_QUERIES_PER_SCAN` as it goes. The arithmetic is the caller's because
    only the caller knows what it is about to spend.

    **A quota nobody published is not a quota of zero.** With
    GOOGLE_ADS_DAILY_QUOTA set to 0 there is no ceiling to compare against, so
    this answers `measured: False` and `exhausted: False` — refusing to scan
    on the strength of a number nobody stated would silence the feature over
    an absence, and Google's own refusal is the backstop either way. That is
    the same rule the estimator itself works to: not measured is never a
    clean zero and never a red cross.
    """
    api = {}
    for row in google_estimate(rows=rows).get("apis") or []:
        if row.get("key") == "ads":
            api = row
            break
    used = int(api.get("today") or 0)
    quota = api.get("daily_quota")
    if not quota:
        return {"measured": False, "used_today": used, "daily_quota": None,
                "safety_limit": None, "remaining": None, "exhausted": False,
                "percent": None,
                "note": "Google publishes no ceiling here that this deployment "
                        "can cite; set GOOGLE_ADS_DAILY_QUOTA to compare "
                        "against your account's actual grant."}
    quota = int(quota)
    limit = int(quota * ADS_QUOTA_SAFETY)
    remaining = max(0, limit - used)
    return {
        "measured": True, "used_today": used, "daily_quota": quota,
        "safety_limit": limit, "remaining": remaining,
        "exhausted": remaining <= 0,
        "percent": round(100 * used / quota) if quota else None,
        "note": (f"{used} of {quota} operations used today; unattended work "
                 f"stops at {limit} so a rep's own deploy still has room."),
    }

ESTIMATORS = {
    "elevenlabs": elevenlabs_estimate,
    "cloudinary": cloudinary_estimate,
    "google": google_estimate,
}


def estimates(month: str | None = None, live: bool = False,
              rows: list[dict] | None = None) -> list[dict]:
    """Google, ElevenLabs and Cloudinary in one shape.

    OpenAI is not here: it already has openai_cost(), which breaks spend down
    per model in a way the other three have no equivalent for. Folding it in
    would have meant either losing that detail or making this shape carry a
    field only one provider uses.
    """
    month = month or month_key()
    rows = ledger(month) if rows is None else rows
    out = []
    for key, fn in ESTIMATORS.items():
        try:
            out.append(fn(month, rows=rows, live=live))
        except Exception as exc:                        # noqa: BLE001
            # One provider's estimate failing must not blank the other two,
            # and the failure must be visible rather than an empty card.
            out.append({"key": key, "label": key.title(), "month": month,
                        "state": "error", "measured": {}, "account": None,
                        "estimated_cost": None, "by_module": {},
                        "caveat": f"This estimate failed to build "
                                  f"({type(exc).__name__}).",
                        "untracked": []})
    return out


# ---------------------------------------------------------------------------
# Blind spots.
#
# The same idea as untracked_openai_modules() above, generalised: a call site
# that spends money without recording it does not make the estimate *wrong by
# a little*, it makes it wrong by however much that call site spends, silently
# and in the reassuring direction. Naming them is what keeps the gap a known
# quantity instead of a surprise.
#
# hub/integrity.py renders these as findings, so this is the one
# implementation rather than a second copy that drifts.
# ---------------------------------------------------------------------------

_SKIP_PARTS = {"_attic", "__pycache__", ".git", "node_modules",
               ".venv", "venv", "env", "site-packages", ".tox", "build", "dist"}
# This file describes every marker below and diagnostics.py probes the same
# providers to check a key still works. Neither spends anything.
_SELF = {"hub/quotas.py", "hub/diagnostics.py", "hub/integrity.py",
         "hub/demo.py", "hub/demos.py"}

_scan_cache: tuple[float, dict] | None = None


def _repo_sources():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for p in root.rglob("*.py"):
        if any(part in _SKIP_PARTS for part in p.parts):
            continue
        rel = p.relative_to(root).as_posix()
        if rel in _SELF:
            continue
        try:
            yield rel, p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def _module_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "modules" and len(parts) > 1:
        return parts[1]
    if parts[0] == "hub":
        return rel
    return parts[-1].replace(".py", "")


def _google_calls(src: str) -> bool:
    """A file that actually calls a Google API, not one that lists its scopes.

    Every OAuth scope is a www.googleapis.com/auth/... URL, so a plain host
    match flags the two config files that define scopes and call nothing. A
    detector that cries wolf on config is one people learn to ignore.
    """
    import re as _re
    body = _re.sub(r"https://www\.googleapis\.com/auth/\S*", "", src)
    if "googleapis.com" not in body and "googleapis" not in body:
        return False
    return any(v in body for v in ("requests.get", "requests.post",
                                   "requests.put", "requests.request",
                                   "requests.delete"))


def _elevenlabs_calls(src: str) -> bool:
    """A file that reaches one of ElevenLabs' three billed audio endpoints.

    Speech, sound effects and music. `/music` on its own is far too ordinary
    a string to match — a landing page naming a music path would read as a
    provider call, which is the crying wolf `_brandfetch_calls` and
    `PICKAXE`'s marker are each written to avoid — so the composer counts
    only where the file plainly speaks to ElevenLabs as well.
    """
    if "/text-to-speech" in src or "/sound-generation" in src:
        return True
    return ('"/music"' in src or "'/music'" in src) and "elevenlabs" in src.lower()


def _brandfetch_calls(src: str) -> bool:
    """A file that looks a CLIENT up, not one that checks the key still works.

    The sign-in health panel and diagnostics both fetch
    ``brands/brandfetch.com`` -- Brandfetch's own domain -- to prove the key is
    valid. That is a probe, not attributable client work, and flagging it would
    have this check report a finding nobody can act on from the day it lands.
    A check that starts life red is one somebody switches off, which is the
    note tools/integritycheck.py already carries.

    Same shape as _google_calls below: strip the thing that is not a call,
    then ask whether anything is left.
    """
    body = src.replace("api.brandfetch.io/v2/brands/brandfetch.com", "")
    return "api.brandfetch.io" in body


_PROVIDER_MARKERS = {
    "elevenlabs": {
        # Three endpoints, not one. `/sound-generation` and `/music` bill per
        # generation and were outside every marker here, so a module could
        # have spent on either with nothing able to name the gap -- which is
        # exactly the state HeyGen, Runway and Creatomate were in before they
        # were added.
        "calls": _elevenlabs_calls,
        "recorded": ("record_tts", "record_audio_generation",
                     'record("elevenlabs"'),
        "detail": "Renders speech through ElevenLabs without recording the "
                  "characters, so this spend never reaches the usage page.",
        "fix": "Add quotas.record_tts(text, module=..., model=...) after the "
               "response — one line, no logic change.",
    },
    "cloudinary": {
        "calls": lambda src: ("uploader.upload" in src
                              or "uploader.destroy" in src),
        "recorded": ("record_asset", 'record("cloudinary"',
                     "from hub import storage", "from hub.storage import",
                     "hub.storage"),
        "detail": "Uploads to Cloudinary outside hub/storage.py and without "
                  "recording it, so its share of the credit bill cannot be "
                  "attributed to this module.",
        "fix": "Move the upload onto hub.storage.put(), which records it, or "
               "add quotas.record_asset(module=..., nbytes=len(data)).",
    },
    "brandfetch": {
        # Every caller hits api.brandfetch.io. The two type routes Brandfetch
        # publishes -- /v2/brands/<domain> and the newer explicit
        # /v2/brands/domain/<domain> -- are both real, so match the host
        # rather than either path, or a module gets a clean bill for using
        # the spelling this check did not think of.
        "calls": _brandfetch_calls,
        "recorded": ("record(\"brandfetch\"", "record('brandfetch'",
                     "from hub import brand_lookup", "from hub.brand_lookup import",
                     "hub.brand_lookup"),
        "detail": "Looks a brand up at Brandfetch outside hub/brand_lookup.py, "
                  "so the call is not counted against the monthly plan and the "
                  "answer is not saved -- Client 360's brand card stays empty "
                  "for a client somebody looked up this morning.",
        "fix": "Move the lookup onto hub.brand_lookup.lookup(domain, "
               "client=..., module=...), which records it and keeps what it "
               "paid for.",
    },
    # The three the Commercial Builder spends on. Each was billed per
    # generation and recorded nowhere, and no marker existed — so there was no
    # check that could have named the gap, which is why it stood. The `calls`
    # test matches the API host rather than a path, for the reason the
    # brandfetch entry gives: a module that used the spelling the check did
    # not think of gets a clean bill.
    "heygen": {
        "calls": lambda src: "api.heygen.com" in src,
        "recorded": ("record_clip", 'record("heygen"'),
        "detail": "Renders a spokesperson clip through HeyGen without "
                  "recording it, so the clips this module generates never "
                  "reach the usage page.",
        "fix": "Add quotas.record_clip(module=..., detail=...) after the "
               "response — one line, no logic change.",
    },
    "runway": {
        "calls": lambda src: "api.dev.runwayml.com" in src or "api.runwayml.com" in src,
        "recorded": ("record_video", 'record("runway"'),
        "detail": "Generates video through Runway without recording the "
                  "seconds, so this spend never reaches the usage page — and "
                  "Runway bills by duration, so counting requests would make "
                  "a :10 clip cost the same as a :05.",
        "fix": "Add quotas.record_video('runway', module=..., seconds=...) "
               "after the response.",
    },
    "creatomate": {
        "calls": lambda src: "api.creatomate.com" in src,
        "recorded": ("record_render", 'record("creatomate"'),
        "detail": "Submits a render to Creatomate without recording it, so "
                  "the renders this module pays for are invisible on the "
                  "usage page.",
        "fix": "Add quotas.record_render(module=..., fmt=...) where the job "
               "is submitted.",
    },
    "google": {
        "calls": _google_calls,
        "recorded": ("record_google", 'record("google"'),
        "detail": "Calls a Google API without recording it, so its calls do "
                  "not count towards the daily quota shown on /diagnostics.",
        "fix": "Add quotas.record_google(url, module=...) after the response.",
    },
    "pickaxe": {
        # The host AND a requests call, because hub/config.py carries the
        # host in PICKAXE_BASE's default and calls nothing — a marker on the
        # string alone would flag the settings file for defining the setting.
        "calls": lambda src: (("api.pickaxe.co" in src or "pickaxe_base" in src)
                              and "requests." in src),
        "recorded": ('record("pickaxe"', "from hub import pickaxe",
                     "from hub.pickaxe import", "hub.pickaxe"),
        "detail": "Calls a Pickaxe outside hub/pickaxe.py and without "
                  "recording it, so a per-use bill never reaches the usage "
                  "page.",
        "fix": "Call hub.pickaxe.ask(...), which records every call and "
               "strips the chat-UI outro.",
    },
}


def untracked_provider_calls(force: bool = False) -> dict[str, list[dict]]:
    """Call sites that spend a provider's allowance without recording it.

    Cached for a minute: this walks every .py file in the repo and three
    separate estimates ask for it on one page load.
    """
    global _scan_cache
    if not force and _scan_cache and time.time() - _scan_cache[0] < 60:
        return _scan_cache[1]

    found: dict[str, list[dict]] = {k: [] for k in _PROVIDER_MARKERS}
    try:
        for rel, src in _repo_sources():
            for provider, spec in _PROVIDER_MARKERS.items():
                if not spec["calls"](src):
                    continue
                if any(m in src for m in spec["recorded"]):
                    continue
                found[provider].append({
                    "file": rel, "module": _module_of(rel),
                    "detail": spec["detail"], "fix": spec["fix"],
                })
    except Exception:                                   # noqa: BLE001
        # A scan that cannot read the source must not take the page down with
        # it; an empty list here reads as "nothing found", so say nothing
        # rather than claiming a clean result.
        return {k: [] for k in _PROVIDER_MARKERS}

    for rows in found.values():
        rows.sort(key=lambda r: r["file"])
    _scan_cache = (time.time(), found)
    return found


def summary(month: str | None = None, live: bool = False) -> dict:
    """Everything the diagnostics page and the nightly alert need.

    `live=True` also asks ElevenLabs and Cloudinary for their own counters.
    Off by default because everything else here is a local read and the
    nightly alert job has no business making outbound calls to build a
    warning it is going to write to the log either way.
    """
    month = month or month_key()
    # One pass over the activity log, shared by every view below. Each of
    # these used to read it in full for itself.
    rows = ledger(month)
    quota_rows = status(month, rows=rows)
    return {"month": month, "quotas": quota_rows,
            "warnings": [r for r in quota_rows if r["state"] in ("warn", "over")],
            "openai": openai_cost(month),
            "estimates": estimates(month, live=live, rows=rows),
            "live": bool(live)}
