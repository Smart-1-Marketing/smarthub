"""What is actually in the photographs a client sent us.

## The one bucket nothing looked at

Client Image Uploads exists so a client can hand over their own photography
through `/tools/image-picker/pick/<token>` rather than emailing it. Forty
photographs of a shop arrive, and every one of them lands with `alt_text` taken
from `body.get("alt")` — typed by a rep, or, in practice, blank. The gallery
has no search, so what a client sent is forty thumbnails nobody can find
anything in, and the Client 360 image tiles carry nothing to read.

The Hub already runs vision twice on the same Cloudinary account.
`modules/seo_images` writes filenames and alt text for images a rep picked, and
`hub/video_library.index_backlog()` describes *every clip* in two folder trees,
twenty an hour under a wall-clock budget. The client's own photographs were the
bucket left out, and they are the bucket least likely ever to get a description
typed by hand.

This is `video_library`'s sweep, aimed at those. Every rule in it is that
module's, inherited rather than restated, because both are the same job and the
next fix to it should land once.

## Inherited rules

**The tag vocabulary is closed.** A vision model asked for free-form keywords
invents a new one per photograph, and a search that has to match "storefront",
"shop front", "shopfront" and "store exterior" finds none of them. Terms
outside `VOCAB` are dropped and **counted**, so a prompt that has started
drifting is visible rather than absorbed.

**A clip that fails comes straight back**, so without a ceiling one unreadable
file costs a vision call an hour for ever, and every individual run looks like
a normal batch that happened to have one failure in it. `MAX_ATTEMPTS` failures
and the row is given up on **in writing** — `state="given_up"` in the table,
because a give-up held in memory forgets itself on the next deploy.

**A wall-clock budget, not only a count.** Scheduler jobs share one thread and
a vision call has no useful ceiling on how long it takes, so a count limit
alone lets one slow batch hold up every job behind it.

## The rule that is this module's own

**A description is an observation, never the alt text.** The whole reason
`alt_text` is sometimes blank is that nobody typed it — and the whole reason it
is sometimes filled is that somebody did. A sweep that wrote into it would
overwrite the second to fix the first, silently, on work a client may have
worded themselves. So the description is stored beside the image and *offered*
into an empty `alt_text`, kept on a press: the overlay rule `hub/client_urls.py`
works to, and the rule `hub/scan_facts.py` applies to a logo it photographed on
a page. `accept()` is the press.

And **alt text is not a description.** A screen reader wants one short sentence
about what the image shows; a search wants the sentence and the tags. They are
written as two fields by one call, because asking twice costs twice and
answering once and truncating gives a bad version of both. `alt_suggestion`
goes through `hub/alt_text`'s cleaner, so the three rules that file already
enforces — the length, the "image of" preamble, and stripped markup — hold here
too rather than being restated and drifting.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select

from hub import ai as _hub_ai
from hub.config import settings

from .models import ImageDescription, SavedImage, session

# One batch of the scheduler's, and the ceiling on how long it may hold the
# thread. Both are hub/video_library's numbers: twenty an hour clears a
# realistic backlog in days rather than months, and ninety seconds is long
# enough for twenty calls and short enough that nothing queues behind it.
BATCH = 20
BUDGET_SECONDS = 90

# Three, then it is given up on in writing. The fourth attempt on a file that
# has failed three times is a vision call spent to learn the same thing again.
MAX_ATTEMPTS = 3

# The closed vocabulary. A term here becomes a search chip on the gallery, so
# adding one is a decision and inventing one per photograph is the failure this
# list exists to stop. Deliberately about *what a local business photographs*
# — the video library's list is about footage and is the wrong shelf for this.
VOCAB: dict[str, tuple[str, ...]] = {
    "subject": (
        "storefront", "interior", "exterior", "team", "portrait", "customer",
        "product", "equipment", "vehicle", "signage", "logo", "building",
        "workshop", "office", "kitchen", "food", "drink", "before-after",
        "install", "repair", "landscape", "aerial", "event", "award",
        "certificate", "document", "screenshot", "artwork",
    ),
    "look": (
        "bright", "dark", "warm", "cool", "daylight", "night", "indoor",
        "outdoor", "close-up", "wide", "busy", "clean",
    ),
    "usable": (
        # Facts that decide where a photograph can go, kept apart from what it
        # is *of*: "can this sit behind a headline" is a different question
        # from "show me the storefront", the split video_library draws between
        # VOCAB and FLAGS.
        "has-people", "has-faces", "has-text", "low-quality", "hero-ready",
    ),
}

ALL_TERMS = {t for group in VOCAB.values() for t in group}

_PROMPT = (
    "You are describing a photograph a small business sent to their marketing "
    "agency, so that agency staff can find it again and use it.\n\n"
    "Answer with JSON only:\n"
    '{"description": "<one or two plain sentences saying what is in the '
    'photograph and what it would be useful for>", '
    '"alt": "<one short sentence of alt text for a screen reader, under 125 '
    'characters, not starting with \\"image of\\">", '
    '"tags": ["<terms from the list below>"]}\n\n'
    "Rules:\n"
    "1. Use ONLY tags from this list. Do not invent a tag. If none fits, "
    "return an empty list.\n"
    "2. Describe what you can see. Do not guess the business's name, the "
    "town, a price, or a date — none of those can be read off a photograph "
    "reliably, and a wrong one is worse than a missing one.\n"
    "3. If the picture is unusable — blank, corrupt, a screenshot of an "
    "error — say so in the description and tag it low-quality.\n\n"
    "Tags you may use:\n"
)


def _prompt() -> str:
    lines = [_PROMPT]
    for group, terms in VOCAB.items():
        lines.append(f"{group}: " + ", ".join(terms))
    return "\n".join(lines)


def can_describe() -> bool:
    """Is there a key to spend? Reported rather than assumed anywhere."""
    return bool(settings.openai_ready)


def _clean_tags(raw) -> tuple[list[str], int]:
    """Keep the terms in VOCAB, and count what was dropped.

    Counted rather than discarded quietly: a prompt that has started returning
    terms of its own is something to look at, and a search vocabulary that
    silently grows is one nobody can put chips on.
    """
    out, dropped = [], 0
    for item in (raw or []):
        term = str(item or "").strip().lower().replace(" ", "-")
        if term in ALL_TERMS:
            if term not in out:
                out.append(term)
        elif term:
            dropped += 1
    return out[:8], dropped


def _clean_alt(text: str) -> str:
    """hub/alt_text's cleaner, not a second copy of its three rules."""
    try:
        from hub import alt_text
        return alt_text._clean_alt(str(text or ""))       # noqa: SLF001
    except Exception:                                     # noqa: BLE001
        return str(text or "").strip()[:125]


def _row_for(db, image_id: int) -> ImageDescription:
    row = db.execute(select(ImageDescription)
                     .where(ImageDescription.image_id == image_id)).scalar_one_or_none()
    if row is None:
        row = ImageDescription(image_id=image_id, state="pending", attempts=0)
        db.add(row)
    return row


def describe_image(image: SavedImage) -> dict:
    """One vision call for one saved image. Raises nothing the caller must catch.

    Returns `{"ok": bool, "error": str, ...}` — never a bare exception, because
    one unreadable file must cost its own row and not the batch.
    """
    url = (image.cloudinary_url or image.source_url or "").strip()
    if not url:
        return {"ok": False, "error": "no URL to look at"}
    if (image.resource_type or "image") != "image":
        # A brochure PDF is a real thing to receive and not a thing to describe
        # from its pixels. Said in words rather than left as a failure that
        # retries twice more.
        return {"ok": False, "error": "not an image", "skip": True}
    try:
        answer = _hub_ai.chat_json(
            [{"role": "user", "content": [
                {"type": "text", "text": _prompt()},
                {"type": "image_url", "image_url": {"url": url}}]}],
            module="image_picker", purpose="describe_upload",
            model=settings.openai_vision_model, max_tokens=500, temperature=0.2)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    tags, dropped = _clean_tags(answer.get("tags"))
    return {
        "ok": True, "error": "",
        "description": str(answer.get("description") or "").strip()[:1000],
        "alt": _clean_alt(answer.get("alt")),
        "tags": tags, "dropped_tags": dropped,
        "model": settings.openai_vision_model,
    }


def pending_count() -> dict:
    """How many saved images have no reading, and how many were given up on.

    Tri-state, like every other count in this Hub that a page prints: a store
    we could not read is **not measured**, never zero, because zero here reads
    as "everything has been described" on the one screen that decides whether
    to spend anything.
    """
    try:
        with session() as db:
            total = db.execute(select(SavedImage.id)).scalars().all()
            rows = db.execute(select(ImageDescription.image_id,
                                     ImageDescription.state)).all()
            done = {r[0] for r in rows if r[1] == "described"}
            gave_up = {r[0] for r in rows if r[1] == "given_up"}
            return {"measured": True, "images": len(total),
                    "described": len(done), "given_up": len(gave_up),
                    "pending": max(0, len(total) - len(done) - len(gave_up)),
                    "configured": can_describe(), "error": ""}
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False, "images": 0, "described": 0, "given_up": 0,
                "pending": 0, "configured": False,
                "error": f"{type(exc).__name__}: {exc}"}


def describe_backlog(limit: int = BATCH, *, max_seconds: int = BUDGET_SECONDS,
                     actor: str = "scheduler") -> dict:
    """One bounded pass over the images nothing has looked at yet.

    Oldest first, so a client who uploaded a month ago is described before one
    who uploaded this morning — the backlog is the point, and newest-first
    would leave the oldest never reached.
    """
    if not can_describe():
        # Not an error and not silence: an unconfigured Hub would otherwise
        # write an identical failure into the activity log every hour for ever,
        # which is the noise hub/google_index.py had to learn to stop making.
        return {"skipped": "OPENAI_API_KEY is not set", "described": 0}

    started = time.time()
    described = failed = gave_up = dropped_tags = 0
    errors: list[str] = []
    try:
        with session() as db:
            seen = {r[0] for r in db.execute(
                select(ImageDescription.image_id, ImageDescription.state)
            ).all() if r[1] in ("described", "given_up")}
            todo = [im for im in db.execute(
                select(SavedImage).order_by(SavedImage.created_at.asc())
            ).scalars().all() if im.id not in seen][:max(1, int(limit))]

            for image in todo:
                if time.time() - started > max_seconds:
                    break
                out = describe_image(image)
                row = _row_for(db, image.id)
                row.updated_at = datetime.now(timezone.utc)
                if out.get("ok"):
                    row.state = "described"
                    row.description = out["description"]
                    row.alt_suggestion = out["alt"]
                    row.tags = ",".join(out["tags"])
                    row.model = out.get("model") or ""
                    row.last_error = ""
                    dropped_tags += int(out.get("dropped_tags") or 0)
                    described += 1
                    continue
                row.attempts = int(row.attempts or 0) + 1
                row.last_error = str(out.get("error") or "")[:400]
                # A file that is not an image is given up on at once rather
                # than retried twice more to learn the same thing.
                if out.get("skip") or row.attempts >= MAX_ATTEMPTS:
                    row.state = "given_up"
                    gave_up += 1
                else:
                    row.state = "pending"
                    failed += 1
                errors.append(row.last_error)
            db.commit()
    except Exception as exc:                              # noqa: BLE001
        # A provider outage or a sleeping database must not take the scheduler
        # down with it. The undescribed images simply come back next hour.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "described": described}

    out = {"ok": True, "described": described, "failed": failed,
           "gave_up": gave_up, "seconds": round(time.time() - started, 1),
           "actor": actor}
    if dropped_tags:
        # Named, not swallowed: terms outside the vocabulary mean the prompt
        # and the list have started to disagree, and a search vocabulary that
        # grows in silence is one nobody can put chips on.
        out["dropped_tags"] = dropped_tags
    if errors:
        out["last_error"] = errors[-1]
    return out


def accept(image_id: int, *, actor: str = "") -> dict:
    """Take the suggested alt text onto the image. The press, not the sweep.

    Two rules, both the overlay rule this Hub applies everywhere it offers
    somebody a value: it fills an **empty** field only — a rep or a client who
    typed alt text is the better source and is never written over — and nothing
    is written until this is called.
    """
    try:
        with session() as db:
            row = db.execute(select(ImageDescription)
                             .where(ImageDescription.image_id == int(image_id))
                             ).scalar_one_or_none()
            if row is None or not (row.alt_suggestion or "").strip():
                return {"ok": False,
                        "error": "There is no suggested alt text for that image."}
            image = db.get(SavedImage, int(image_id))
            if image is None:
                return {"ok": False, "error": "That image is no longer here."}
            if (image.alt_text or "").strip():
                return {"ok": False, "written": False,
                        "error": "That image already has alt text, and a value "
                                 "somebody typed is the better source — it is "
                                 "not written over."}
            image.alt_text = row.alt_suggestion
            row.accepted_at = datetime.now(timezone.utc)
            row.accepted_by = str(actor or "")[:200]
            db.commit()
            return {"ok": True, "written": True, "alt": image.alt_text}
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def descriptions_for(image_ids) -> dict:
    """`{image_id: to_dict()}` for a page drawing a gallery. Never raises."""
    ids = [int(i) for i in (image_ids or []) if str(i).isdigit()]
    if not ids:
        return {}
    try:
        with session() as db:
            rows = db.execute(select(ImageDescription)
                              .where(ImageDescription.image_id.in_(ids))
                              ).scalars().all()
            return {r.image_id: r.to_dict() for r in rows}
    except Exception:                                     # noqa: BLE001
        return {}
