"""Put an already-stored asset into a client's gallery.

Four pipelines produce work for a client: images picked from a stock provider,
files the client uploads themselves, creative attached to an insertion order,
blog featured images, and the SEO image optimiser. Until now only the first two
ended up in the gallery. The rest wrote to Cloudinary and stopped there, so the
one page a client is pointed at showed a fraction of what had been made for
them — and staff answering "what have we produced for this account?" had to
know which of five folders to look in.

This is the single way in. It takes an asset that already exists in Cloudinary
and records it, because every caller has already uploaded by the time it has
anything worth filing; re-uploading here would double the storage and break the
public_id the caller is holding.

Deliberately not an HTTP call. The route in app.py is a thin wrapper around
this, so a background job filing a blog image does not need a session cookie to
talk to its own process.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from . import ghl, taxonomy
from .models import PickerClient, SavedImage, new_token, session, slugify, unique_slug

logger = logging.getLogger(__name__)

# What a folder is called in the gallery. The kind is stored on the row and the
# label is what a person reads, so renaming the label later does not orphan the
# rows filed under the old one.
KIND_LABELS = {
    "upload": "Client upload",
    "io_creative": "IO creative",
    "blog": "Blog images",
    "seo_image": "SEO images",
    "display_ad": "Display ads",
    # Filed by hub/client_logos.py from the client's brand record or their
    # last site scan. Declared here so the gallery groups them under a name
    # rather than under a bare key -- the same reason hub/audit.LOG_NAMES
    # declares a log name the directory cannot guess.
    "logo": "Logo",
    "cutout": "Cut-outs",
    "graphic": "Graphics",
    "page_image": "Website images",
    "stock": "Stock photos",
    "commercial": "Commercial stills",
}

# A label declared here and written by nothing is the failure this codebase
# has already paid for twice: `display_ads` sat in audit.LOG_NAMES while the
# work went unrecorded, and `io_creative` sat here while every asset attached
# to an insertion order went to Cloudinary and stopped. hub/image_audit.py
# checks the other direction -- which producers reach this function at all --
# and test_image_audit.py asserts every label below has a writer.

# What a person reads above a group in the gallery. Every way a file can
# arrive, in one table, because the gallery template used to keep a second
# hand-typed copy of this -- so a new kind showed up in the client's gallery
# as a bare key like `cutout` under a heading nobody had written, sorted in
# with stock. The page reads this now and a kind added next month is named
# without the template being edited.
SOURCE_LABELS = {
    "upload": "Client upload",
    "local": "Uploaded from their device",
    "url": "Added by web address",
    "camera": "Taken with a camera",
    "google_drive": "From Google Drive",
    "dropbox": "From Dropbox",
    "instagram": "From Instagram",
    "facebook": "From Facebook",
    "image_search": "From image search",
    "shutterstock": "Shutterstock", "getty": "Getty", "istock": "iStock",
    "unsplash": "Unsplash", "pexels": "Pexels", "pixabay": "Pixabay",
    "library": "Our own library",
    "io_creative": "Creative for their insertion orders",
    "blog": "Blog images",
    "seo_image": "SEO images",
    "seo_images": "SEO images",
    "display_ad": "Display ads",
    "ad_builder": "Display ads",
    "logo": "Logos",
    "client_logos": "Logos",
    "bg_remover": "Cut-outs",
    "cutout": "Cut-outs",
    "image_creator": "Graphics",
    "graphic": "Graphics",
    "page_image_optimizer": "Website images",
    "page_image": "Website images",
    "stock": "Stock photos",
    "stock_photos": "Stock photos",
    "commercial_builder": "Commercial stills",
    "commercial": "Commercial stills",
    "gpt_ads": "GPT ads",
    "logo_brand": "Logo (from their brand record)",
    "logo_scan": "Logo (seen on their website)",
    "display_ads": "Display ads",
    # Files kept against a business before they were a client. They live on
    # the prospect record while it is one, and a conversion carries them
    # across -- so the heading has to exist here or they arrive in the new
    # client's gallery as a bare key under nothing.
    "prospect": "Collected before they were a client",
}

# Which of the three questions a group answers. The first thing anybody asks
# of a client gallery is "which of these are theirs?", so that is the tier,
# not a column. Anything unlisted is stock, which is the safe default: it
# sorts last and claims nothing.
THEIRS = ("local", "camera", "google_drive", "dropbox", "instagram",
          "facebook", "url")
WE_MADE = ("io_creative", "blog", "seo_image", "seo_images", "display_ad",
           "display_ads", "ad_builder", "logo", "logo_brand", "logo_scan",
           "client_logos", "bg_remover", "cutout", "image_creator", "graphic",
           "page_image_optimizer", "page_image", "commercial_builder",
           "commercial", "gpt_ads", "prospect")


def source_tiers() -> dict:
    """The label table and the two tiers, for whatever renders a gallery."""
    return {"labels": dict(SOURCE_LABELS),
            "theirs": list(THEIRS), "we_made": list(WE_MADE)}


def gallery_for_name(db, name: str, *, create: bool = False) -> PickerClient | None:
    """The gallery for a client name. None when there isn't one.

    Name matching is a known source of false positives in this codebase —
    "Riverside HVAC" and "Riverside HVAC LLC" are different records — so this is
    deliberately narrow: an exact slug match, and creation only when the caller
    explicitly asks. A near-miss returns nothing rather than filing one client's
    work into another client's gallery.
    """
    name = str(name or "").strip()[:200]
    if not name:
        return None
    found = db.execute(
        select(PickerClient).where(PickerClient.slug == slugify(name))
    ).scalar_one_or_none()
    if found or not create:
        return found
    client = PickerClient(
        name=name,
        slug=unique_slug(db, name),
        industry_key=taxonomy.guess_industry(name),
        kind="prospect",
        share_token=new_token(),
    )
    db.add(client)
    db.commit()
    return client


def file_asset(*, client_name: str, public_id: str, url: str,
               kind: str = "upload", label: str = "", key: str = "",
               filename: str = "", alt: str = "", resource_type: str = "image",
               width=None, height=None, size_bytes=None,
               spec: dict | None = None, provider: str = "",
               saved_by: str = "system", create_client: bool = True,
               push_to_suite: bool = True) -> dict:
    """Record one asset in a client's gallery.

    Returns a dict with `ok`, and on success the `image` row and `gallery_url`.
    Never raises: every caller is finishing a piece of work that already
    succeeded, and losing a generated blog image because the gallery write
    failed would be a worse outcome than the image not appearing in the gallery.
    """
    public_id = str(public_id or "").strip()
    url = str(url or "").strip()
    if not public_id or not url.startswith("https://"):
        return {"ok": False, "error": "That asset has no stored URL."}

    kind = (kind or "upload").strip().lower()[:20]
    provider = (provider or kind).strip().lower()[:40]
    spec = spec if isinstance(spec, dict) else {}

    try:
        db = session()
        client = gallery_for_name(db, client_name, create=create_client)
        if client is None:
            return {"ok": False, "error": "No gallery for that client."}

        existing = db.execute(
            select(SavedImage).where(SavedImage.client_id == client.id,
                                     SavedImage.provider == provider,
                                     SavedImage.provider_image_id == public_id)
        ).scalar_one_or_none()
        if existing:
            return {"ok": True, "duplicate": True, "image": existing.to_dict(),
                    "gallery_url": f"/tools/image-picker/gallery/{client.id}"}

        rtype = (resource_type or "image").strip().lower()
        img = SavedImage(
            client_id=client.id,
            provider=provider,
            provider_image_id=public_id,
            source_url=url,
            filename=str(filename or "")[:300] or None,
            alt_text=str(alt or "")[:500] or None,
            resource_type="raw" if rtype not in ("image", "video") else rtype,
            cloudinary_public_id=public_id,
            cloudinary_url=url,
            width=width or None,
            height=height or None,
            bytes=size_bytes or None,
            collection_kind=kind,
            collection_key=str(key or "")[:80] or None,
            collection_label=str(label or KIND_LABELS.get(kind, "") or "")[:200] or None,
            spec_result=str(spec.get("result") or "")[:10] or None,
            spec_summary=str(spec.get("summary") or "") or None,
            spec_unit=str(((spec.get("unit") or {}).get("id")) or "")[:60] or None,
            ghl_status="pending",
            saved_by=str(saved_by or "system")[:200],
        )
        db.add(img)
        db.commit()
    except Exception as exc:                            # noqa: BLE001
        logger.warning("gallery filing failed for %s: %s", client_name, exc)
        return {"ok": False, "error": str(exc)}

    if push_to_suite:
        # Straight on to Suite where the gallery has a location, skipped where
        # it doesn't — the normal case for a prospect, and the reason the row is
        # committed before this runs rather than after. push_image never raises.
        try:
            pushed = ghl.push_image(
                client, file_url=img.cloudinary_url,
                name=(img.filename or public_id.rsplit("/", 1)[-1]))
            img.ghl_status = pushed["status"]
            img.ghl_file_id = pushed["file_id"] or None
            img.ghl_url = pushed["url"] or None
            img.ghl_error = pushed["error"] or None
            db.commit()
        except Exception:                               # noqa: BLE001
            db.rollback()

    return {"ok": True, "image": img.to_dict(),
            "gallery_url": f"/tools/image-picker/gallery/{client.id}"}
