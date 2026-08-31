"""Featured images for blog posts — generate, approve, then optimise.

A written post with no image doesn't get published, so the image is part of
finishing the post rather than a separate errand. This generates one as soon
as the content exists, using the post's own title and body as the brief.

## The prompt, and why it's constrained

Todd's brief, near verbatim:

    Using its content and title to guide you, create a photo realistic image
    with no text or logos. I just want an image.

The "no text" instruction is doing real work. Image models render text badly —
misspelled words and mangled letterforms are the single most common reason a
generated image is unusable — and a featured image with a garbled headline
baked into it is worse than no image. The prompt says it three ways because
one mention is not reliably obeyed.

## Nothing is published without a human looking at it

Generated images land as `pending`. An AI image of a plumber can have six
fingers, a storefront can carry an invented brand name, and neither is
something to discover on a client's live site. Approve, regenerate with a
note, or delete.

## Optimising happens on the way in, and says when it could not

Every image is capped and converted to WebP before it is stored, rather than
at approval: holding a multi-megabyte payload in the client store would bloat
every read of that record, and approving would then pay for a second
Cloudinary operation to do what the first could have done. A 3 MB PNG hero is
a Core Web Vitals problem on the very page the post was written to rank.

`_optimise_bytes()` returns nothing at all when Pillow cannot read the bytes,
and the original was stored in its place **in silence** — so a 3 MB hero went
into the client's gallery with `bytes` recording 3 MB and every screen
reporting a clean success. `optimised` is on the image now and the note says
so, because that is the one number on this record somebody would act on.

## One image per post, not one per title

The Cloudinary object was named from the post's **title**, `overwrite=True`
and `unique_filename=False` — so two posts that slugify alike share one
object, and the second one generated silently replaces the first's picture at
the same URL. That is not a coincidence to guard against: the planner's
fallback titles **cycle through six**, so any plan needing more than six
top-up titles holds each of them twice, verbatim, in one month. Approving the
second then overwrites the first's approved, filed image as well.

The post id is unique by construction and was already being written into the
upload context, so it is in the name now. Nothing is re-keyed: every existing
row carries its own `public_id`, and `_promote()` and `_file_in_gallery()`
read that rather than deriving it — the rule this codebase applies to
`audit.LOG_NAMES` and `video_library.TAG_ALIASES`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

FOLDER = "Blogs"
BASE_PROMPT = (
    "Create a photorealistic image for a blog post. "
    "Absolutely no text, no words, no letters, no numbers, no logos, no "
    "watermarks and no signage anywhere in the image. "
    "A clean editorial photograph, natural lighting, shallow depth of field, "
    "nothing that looks like stock clip art or an illustration. "
    "Landscape orientation suitable for a featured image."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def image_name(post: dict) -> str:
    """What one post's image is called in Cloudinary.

    The title alone is not a name: it is chosen by a model, it is not unique,
    and `_stage()` uploads with `overwrite=True`. The id is what makes it one
    object per post; the slug stays in front of it so the folder is readable
    by somebody looking at the account rather than a column of numbers.

    A post with no id falls back to the slug alone — that is the shape every
    row written before this carries, and inventing an id for it would rename
    an object the store already points at.
    """
    slug = re.sub(r"[^a-z0-9]+", "-",
                  str(post.get("title") or "post").lower()).strip("-")[:60]
    pid = str(post.get("id") or "").strip()
    return f"{slug}-{pid}" if pid else (slug or "post")


def _strip_html(html: str, limit: int = 900) -> str:
    text = re.sub(r"<[^>]+>", " ", str(html or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def build_prompt(post: dict, client: str = "", extra: str = "") -> str:
    """Turn the post into an image brief.

    The body matters as much as the title: "Spring Maintenance Checklist" is
    ambiguous on its own, and the content is what says whether that's an
    HVAC technician or a lawn crew.
    """
    title = str(post.get("title") or "").strip()
    body = _strip_html(post.get("content") or post.get("summary") or "")
    parts = [BASE_PROMPT, f'The post is titled "{title}".']
    if body:
        parts.append(f"It covers: {body}")
    if client:
        # Named for subject matter only — the model must not try to render a
        # brand, which would mean text.
        parts.append(f"The business is {client}, but do not depict any "
                     f"branding, signage or company name.")
    if extra.strip():
        parts.append(f"Additional direction: {extra.strip()}")
    parts.append("Remember: no text or lettering of any kind in the image.")
    return " ".join(parts)


def generate(client: str, post_id, extra: str = "", actor: str = "") -> dict:
    """Generate a featured image for one post. Leaves it pending approval."""
    from hub import audit, seo
    store = seo.load_store(client) or {}
    posts = (store.get("blogs") or {}).get("posts") or []
    post = next((p for p in posts if str(p.get("id")) == str(post_id)), None)
    if not post:
        return {"error": "No such post."}
    if not (post.get("content") or post.get("summary")):
        return {"error": "Write the post first — the image is generated from "
                         "its content, so there's nothing to work from yet."}

    prompt = build_prompt(post, client, extra)
    # ai.image() returns raw bytes, not a dict — optimise and file it
    # immediately rather than holding a multi-megabyte payload in the client
    # store, which would bloat every read of that record.
    try:
        from hub import ai
        raw = ai.image(prompt, module="seo", purpose="blog_featured",
                       size="1536x1024")
    except Exception as exc:                            # noqa: BLE001
        return {"error": f"Couldn't generate the image ({type(exc).__name__}). "
                         f"Check OPENAI_IMAGE_MODEL on Render."}
    if not raw:
        return {"error": "The image model returned nothing."}
    # Pillow answers with nothing at all when it cannot read the bytes, and
    # falling back to the original is right -- an image nobody can shrink is
    # still the image. Doing it silently is not: what gets stored is then the
    # 3 MB hero this module exists to prevent, with `bytes` recording 3 MB and
    # every screen reporting a clean success.
    staged = _optimise_bytes(raw)
    data = staged or raw
    preview = _stage(client, post, data, pending=True)
    note = preview.get("note", "")
    if not staged:
        big = f" It is {len(raw) // 1024} KB." if len(raw) > 400_000 else ""
        note = (note + " " if note else "") + (
            "Stored at full size -- it could not be converted, so this one "
            "was not optimized." + big).strip()

    history = post.get("image_history") or []
    if post.get("image"):
        history.append({k: post["image"].get(k)
                        for k in ("url", "prompt", "created", "status")})
    post["image"] = {
        "url": preview.get("url", ""), "public_id": preview.get("public_id", ""),
        "bytes": len(data), "optimised": bool(staged),
        "prompt": prompt, "extra": extra, "created": _now(),
        "status": "pending", "by": actor,
        "note": note,
    }
    post["image_history"] = history[-4:]        # keep a few, not all
    seo.save_store(client, store)
    audit.log("seo", "blog_image_generated", actor=actor or None,
              client=client, post=post_id, regenerated=bool(history))
    return {"ok": True, "post_id": post_id, "image": post["image"],
            "attempt": len(history) + 1,
            "note": "Generated and awaiting approval. Check it before it goes "
                    "anywhere near the client's site — generated people and "
                    "storefronts are where these go wrong."}


def approve(client: str, post_id, actor: str = "") -> dict:
    """Approve, optimise, and file into the client's Blogs gallery."""
    from hub import audit, seo
    store = seo.load_store(client) or {}
    posts = (store.get("blogs") or {}).get("posts") or []
    post = next((p for p in posts if str(p.get("id")) == str(post_id)), None)
    if not post or not post.get("image"):
        return {"error": "No image to approve."}

    img = post["image"]
    saved = _promote(client, post, img)
    img.update({"status": "approved", "approved_by": actor,
                "approved_at": _now()})
    if saved.get("url"):
        img["gallery_url"] = saved["url"]
        img["url"] = saved["url"]               # serve the optimised one
        img.pop("b64", None)                    # don't keep the raw payload
    # Saved before the gallery write, because a gallery that is unavailable
    # must not cost somebody an approval -- and saved again afterwards,
    # because `img` is a reference into `store` and assigning to it once the
    # file has been written left `gallery_folder` in memory alone: the value
    # reached the browser, the next read of the record had never heard of it.
    seo.save_store(client, store)
    filed = _file_in_gallery(client, post, img, saved)
    if filed:
        img["gallery_folder"] = filed
        seo.save_store(client, store)
    audit.log("seo", "blog_image_approved", actor=actor or None,
              client=client, post=post_id, folder=FOLDER)
    return {"ok": True, "image": img, "stored": saved,
            "gallery": filed, "note": saved.get("note", "")}


def _file_in_gallery(client: str, post: dict, img: dict, saved: dict) -> str:
    """Put an approved blog image into the client's gallery, under Blogs.

    Approved images were written to a Cloudinary folder called Blogs and left
    there. The gallery the client is actually pointed at never learned they
    existed, so the images they were most likely to want — the ones made for
    their own posts — were the ones they could not find.

    Labelled with the post it belongs to, because "blog-image-4.webp" in a grid
    of forty tells nobody which article it illustrates.
    """
    url = saved.get("url") or img.get("url") or ""
    public_id = saved.get("public_id") or img.get("public_id") or ""
    if not url or not public_id:
        return ""
    title = str(post.get("title") or "").strip()
    try:
        from modules.image_picker import filing
        out = filing.file_asset(
            client_name=client, public_id=public_id, url=url,
            kind="blog", key=str(post.get("id") or "")[:80],
            label=f"Blog — {title}" if title else "Blog images",
            filename=(public_id.rsplit("/", 1)[-1] + ".webp"),
            alt=str(img.get("alt") or title or "")[:500],
            resource_type="image", provider="blog",
            saved_by="blog images")
        return out.get("gallery_url", "") if out.get("ok") else ""
    except Exception:                                   # noqa: BLE001
        # A gallery that is unavailable must not fail an approval that has
        # already been written to the store.
        return ""


def reject(client: str, post_id, actor: str = "") -> dict:
    """Delete the image, keeping the post."""
    from hub import audit, seo
    store = seo.load_store(client) or {}
    posts = (store.get("blogs") or {}).get("posts") or []
    post = next((p for p in posts if str(p.get("id")) == str(post_id)), None)
    if not post:
        return {"error": "No such post."}
    post.pop("image", None)
    seo.save_store(client, store)
    audit.log("seo", "blog_image_deleted", actor=actor or None,
              client=client, post=post_id)
    return {"ok": True}


def _optimise_bytes(raw: bytes) -> bytes:
    """Cap the longest edge, then convert to WebP. Order matters.

    WebP alone leaves a 1536px hero at 1536px — that was the v1.5.0 finding
    and it applies here too. A featured image that slows the page defeats the
    post it was made for.
    """
    import io
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=82, method=6)
        return buf.getvalue()
    except Exception:                                   # noqa: BLE001
        return b""


def _stage(client: str, post: dict, data: bytes, pending: bool = False) -> dict:
    """Put the image in the client's Blogs folder.

    Pending images go to a `pending/` subfolder so an unapproved image is
    never mistaken for a finished asset by anything browsing the gallery.
    """
    slug = image_name(post)
    try:
        import cloudinary.uploader
        from hub.config import settings
        if not settings.cloudinary_ready:
            return {"note": "Generated, but Cloudinary isn't configured so "
                            "there's nowhere to put it."}
        folder = f"{settings.folder('seo_images')}/{_slug(client)}/{FOLDER}"
        if pending:
            folder += "/pending"
        res = cloudinary.uploader.upload(
            data, folder=folder, public_id=slug, resource_type="image",
            overwrite=True, unique_filename=False,
            context={"client": client, "kind": "blog-featured",
                     "post": str(post.get("id")),
                     "status": "pending" if pending else "approved"})
        # One Cloudinary operation, for the credit estimate on /diagnostics.
        try:
            from hub import quotas as _q
            _q.record_asset(module="blog_images", nbytes=len(data),
                            detail=res.get("public_id", ""))
        except Exception:                     # noqa: BLE001
            pass
        return {"url": res.get("secure_url"), "public_id": res.get("public_id"),
                "folder": folder}
    except Exception as exc:                            # noqa: BLE001
        return {"note": f"Couldn't store it ({type(exc).__name__})."}


def _promote(client: str, post: dict, img: dict) -> dict:
    """Move an approved image out of pending/ into the Blogs folder."""
    pid = img.get("public_id") or ""
    if not pid:
        return {"note": "No stored image to promote."}
    try:
        import cloudinary.uploader
        target = pid.replace("/pending/", "/")
        if target == pid:
            return {"url": img.get("url"), "note": "Already in place."}
        res = cloudinary.uploader.rename(pid, target, overwrite=True)
        return {"url": res.get("secure_url"), "public_id": res.get("public_id"),
                "note": f"Filed under {FOLDER}."}
    except Exception as exc:                            # noqa: BLE001
        return {"url": img.get("url"),
                "note": f"Approved, but couldn't move it out of pending "
                        f"({type(exc).__name__})."}


# `_optimise_and_store()` stood here: a second, unreached implementation of
# resize-then-convert-then-file, written when approval was meant to be the
# step that optimised. Nothing has called it since the work moved into
# `generate()`, and it is deleted rather than left as a 69-line description
# of a path the module no longer takes -- the reader who finds it next
# believes it. `test_unwired.py` could never have said so: it skips names
# beginning with an underscore, because a private helper called from inside
# its own module is the ordinary case.


def _slug(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-") or "client"


def status(client: str) -> dict:
    """Which posts have images, and which are waiting on a human.

    This is the one number that says "somebody needs to look at these", and it
    is drawn as a badge on a Blogs section that is collapsed by default — so
    it has to count exactly the posts the section can show. It counted every
    post whose status is `written`, and archiving does not change that status:
    `/api/seo/blogs` filters `archived` out of the working list and this did
    not, so archiving a post with a pending image left a badge reading "1
    image to approve" above a table with no row to click. Amber for ever, and
    nothing anywhere to clear it — two readings of which posts are in play,
    disagreeing on one screen.

    What is left over is counted rather than dropped: an archived post with a
    pending image has a file sitting in `pending/` that nobody will now
    approve or delete, and a badge that silently gets shorter cannot be told
    from one that failed to load.
    """
    from hub import seo
    every = ((seo.load_store(client) or {}).get("blogs") or {}).get("posts") or []
    posts = [p for p in every if not p.get("archived")]
    written = [p for p in posts if p.get("status") == "written"]
    pending = [p for p in written if (p.get("image") or {}).get("status") == "pending"]
    approved = [p for p in written if (p.get("image") or {}).get("status") == "approved"]
    none_yet = [p for p in written if not p.get("image")]
    stranded = [p for p in every if p.get("archived")
                and (p.get("image") or {}).get("status") == "pending"]
    note = (f"{len(pending)} image(s) waiting for approval." if pending else
            f"{len(none_yet)} written post(s) have no image yet." if none_yet
            else "Every written post has an approved image.")
    if stranded:
        note += (f" {len(stranded)} more sit on archived posts, which are not "
                 f"on the working list — nobody is going to approve those.")
    return {"written": len(written), "pending": len(pending),
            "approved": len(approved), "without_image": len(none_yet),
            "archived_pending": len(stranded),
            "pending_ids": [p.get("id") for p in pending],
            "note": note}
