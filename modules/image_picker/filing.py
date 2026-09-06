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
import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

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
    # Its own heading rather than folded into display_ad. A client looking at
    # their gallery is choosing what to run, and "the 300x250" and "the 300x250
    # that moves" are two files that run in different placements -- most
    # placements take only the still one. Grouped together they read as
    # duplicates and somebody deletes one.
    "animated_ad": "Animated display ads",
    # Filed by hub/client_logos.py from the client's brand record or their
    # last site scan. Declared here so the gallery groups them under a name
    # rather than under a bare key -- the same reason hub/audit.LOG_NAMES
    # declares a log name the directory cannot guess.
    "logo": "Logo",
    "cutout": "Cut-outs",
    "graphic": "Graphics",
    "page_image": "Website images",
    "stock": "Stock photo picks",
    "commercial": "Commercial stills",
    "creative_information": "Creative Information",
    # Creative that ran on a campaign, copied out of the Drive folder the
    # media team was keeping it in and filed under the IO it belongs to.
    # hub/ad_assets.py writes it; the folder shape is ad_asset_folder() below.
    "ad_asset": "Ad Assets",
    # Footage saved out of Video Search -- the owned Cloudinary library, or a
    # clip pulled in from Pexels/Pixabay/Coverr. Its own heading rather than
    # folded into `stock`: a client's own reel of saved footage is a different
    # thing to browse than the stock photos chosen for their creative, and the
    # two tools file at different times for different reasons.
    "video_search": "Video Searches",
    # A dead-air cut or a reframe out of modules/video_tools, saved as its
    # own asset once a rep has decided the edit is the deliverable rather
    # than a trial. Its own heading rather than folded into "commercial":
    # this tool works on any video a client already has, not only a
    # commercial the Commercial Builder made.
    "video_edit": "Video edits",
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
    "io_builder": "IO documents",
    "creative_information": "Creative information",
    "ad_asset": "Ad assets for their campaigns",
    "animated_ad": "Animated display ads",
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
    "stock": "Stock photo picks",
    "stock_photos": "Stock photo picks",
    "commercial_builder": "Commercial stills",
    "commercial": "Commercial stills",
    "gpt_ads": "GPT ads",
    "logo_brand": "Logo (from their brand record)",
    "logo_scan": "Logo (seen on their website)",
    "logo_upload": "Logo (uploaded)",
    "display_ads": "Display ads",
    # Files kept against a business before they were a client. They live on
    # the prospect record while it is one, and a conversion carries them
    # across -- so the heading has to exist here or they arrive in the new
    # client's gallery as a bare key under nothing.
    "prospect": "Collected before they were a client",
    # A photograph a location manager sent in with a social content request.
    # `modules/social_planner` has filed these under this provider since the
    # day it was written and the table never named it, so they arrived in the
    # client's gallery as a bare `social_request` chip under no heading and,
    # unlisted, in the tier that claims nothing -- a photograph the client
    # themselves sent us, sorted in with stock.
    "social_request": "Sent with a social request",
    # Video Search's own kind heading, so `kind in labels` -- which
    # test_image_audit.py requires of every entry in KIND_LABELS -- holds for
    # this one too. What actually lands in `provider` for a saved clip is
    # `video_library`, `pexels`, `pixabay` or `coverr` below, never this key.
    "video_search": "Video Searches",
    # Owned footage out of hub/video_library.py's indexed Cloudinary folders --
    # the video equivalent of `library` above, and left out of THEIRS/WE_MADE
    # for the same reason: it is stock we already hold, not made for this
    # client specifically, so it sorts last and claims nothing.
    "video_library": "Our video library",
    "coverr": "Coverr",
    # Generated for a Performance Max asset group in modules/ads_builder --
    # a distinct provider from Display Ad Builder's own "display_ads" because
    # a different tool made it, and the two must be able to disagree without
    # one silently covering for the other.
    "ads_pmax": "Performance Max creative",
    # One design resized into a whole size set -- modules/magic_resize. Its
    # own provider rather than "display_ads": that is the Display Ad Builder,
    # a different tool that generates a set from copy and a brand rather than
    # resizing a design somebody drew.
    "magic_resize": "Magic Resize",
    "video_edit": "Video edits",
    # A dead-air cut or a reframe, kept as its own asset -- modules/video_tools.
    "video_tools": "Video edits",
}

# Which of the three questions a group answers. The first thing anybody asks
# of a client gallery is "which of these are theirs?", so that is the tier,
# not a column. Anything unlisted is stock, which is the safe default: it
# sorts last and claims nothing.
THEIRS = ("local", "camera", "google_drive", "dropbox", "instagram",
          "facebook", "url", "social_request")
WE_MADE = ("io_creative", "blog", "seo_image", "seo_images", "display_ad",
           "display_ads", "ad_builder", "ads_pmax", "magic_resize", "logo",
           "logo_brand", "logo_scan", "logo_upload", "client_logos",
           "bg_remover", "cutout", "image_creator", "graphic",
           "page_image_optimizer", "page_image", "commercial_builder",
           "commercial", "gpt_ads", "prospect", "io_builder",
           "video_edit", "video_tools")


def source_tiers() -> dict:
    """The label table and the two tiers, for whatever renders a gallery."""
    return {"labels": dict(SOURCE_LABELS),
            "theirs": list(THEIRS), "we_made": list(WE_MADE)}


def folders_for(client_name: str, kind: str) -> list[dict]:
    """Named sub-groups already used under one kind, for this client.

    There is no folder table -- a folder here is nothing but a
    `collection_key`/`collection_label` pair somebody has already filed an
    asset under, so this is a distinct scan of what is already on disk rather
    than a lookup of anything separately stored. That is deliberate: a client
    picking "Homepage refresh" a second time should land on the same rows the
    first save produced, not on a second folder of the same name a typo would
    otherwise create.

    Returns `[]` for a client with no gallery yet -- a picker offering no
    existing folders is exactly right for a client nothing has been saved for.
    """
    kind = (kind or "").strip().lower()[:20]
    if not kind:
        return []
    db = session()
    client = gallery_for_name(db, client_name)
    if client is None:
        return []
    rows = db.execute(
        select(SavedImage.collection_key, SavedImage.collection_label,
              func.count(SavedImage.id), func.max(SavedImage.created_at))
        .where(SavedImage.client_id == client.id,
              SavedImage.collection_kind == kind,
              SavedImage.collection_key.isnot(None),
              SavedImage.collection_key != "")
        .group_by(SavedImage.collection_key, SavedImage.collection_label)
        .order_by(func.max(SavedImage.created_at).desc())
    ).all()
    return [{"key": key, "label": label or key, "count": n}
            for key, label, n, _ in rows]


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


def asset_folder(*, client_name: str, tool: str, completed_on: str = "",
                 io_number: str = "", product_number: str = "",
                 project_name: str = "") -> str:
    """The one folder convention for completed client work.

    A product number is preferred because it is the durable link back to
    Smart 1 Team.  When it is absent, the project name retains useful context
    under the IO.  The values are slugged here so callers cannot create paths
    from raw browser input.
    """
    def clean(value: str, fallback: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
        return value[:100] or fallback
    day = str(completed_on or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        day = datetime.now(timezone.utc).date().isoformat()
    leaf = (f"product-{clean(product_number, 'unassigned')}" if product_number
            else f"project-{clean(project_name, 'general')}")
    return "/".join([
        "client-assets", clean(client_name, "client"), clean(tool, "tool"), day,
        f"io-{clean(io_number, 'unassigned')}", leaf,
    ])


def ad_asset_folder(*, client_name: str, io_number: str = "",
                    product_number: str = "", subpath: str = "") -> str:
    """Where creative for a campaign lives: Ad Assets, then IO, then product.

    A second shape beside `asset_folder()` rather than an argument to it,
    because the two answer different questions and folding them together is
    how one of them quietly changes. `asset_folder()` files *the work a tool
    finished*, and the date is load-bearing there -- it is how somebody finds
    the images the SEO pipeline saved last Tuesday. This files *the creative
    that ran on a line of an insertion order*, where the date is noise: the
    banner delivered in March and its April revision belong in one place,
    which is the product, and a date level between them puts them in two.

    So: `client-assets/<client>/ad-assets/io-<io>/product-<n>`, with the
    product level present only when Knack carried a product number -- an
    `unassigned` folder that exists on most rows is a folder that means
    nothing. `subpath` preserves the shape of the Drive folder underneath,
    because "Final" and "Revised" beside each other is the distinction the
    media team was keeping and flattening it loses which is which.
    """
    def clean(value: str, fallback: str = "") -> str:
        value = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
        return value[:100] or fallback

    parts = ["client-assets", clean(client_name, "client"), "ad-assets",
             f"io-{clean(io_number, 'unassigned')}"]
    product = clean(product_number)
    if product:
        parts.append(f"product-{product}")
    for piece in str(subpath or "").split("/"):
        piece = clean(piece)
        if piece:
            parts.append(piece)
    return "/".join(parts)


def file_asset(*, client_name: str, public_id: str, url: str,
               kind: str = "upload", label: str = "", key: str = "",
               filename: str = "", alt: str = "", resource_type: str = "image",
               width=None, height=None, size_bytes=None,
               spec: dict | None = None, provider: str = "",
               saved_by: str = "system", create_client: bool = True,
               push_to_suite: bool = True, tool: str = "",
               completed_on: str = "", project_name: str = "",
               io_number: str = "", product_number: str = "",
               external: bool = False, folder: str = "") -> dict:
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
    tool = str(tool or provider or kind).strip()[:80]
    completed_on = str(completed_on or "")[:10]
    project_name = str(project_name or "")[:200]
    io_number = str(io_number or "")[:80]
    product_number = str(product_number or "")[:80]
    # A caller that has already decided where this belongs says so. The Ad
    # Assets tree is the one shape the date-keyed default is wrong for --
    # ad_asset_folder() above says why -- and passing the folder in beats a
    # second convention branching inside the default.
    folder = str(folder or "")[:600] or asset_folder(
        client_name=client_name, tool=tool, completed_on=completed_on,
        io_number=io_number, product_number=product_number,
        project_name=project_name)

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
            tool=tool or None,
            completed_on=completed_on or None,
            project_name=project_name or None,
            io_number=io_number or None,
            product_number=product_number or None,
            asset_folder=folder,
            external=bool(external),
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


def file_external_link(*, client_name: str, url: str, filename: str = "",
                       tool: str = "creative-information", project_name: str = "",
                       io_number: str = "", product_number: str = "",
                       saved_by: str = "system") -> dict:
    """Index an existing Drive/shared file without moving or exposing it.

    Drive permissions remain authoritative.  The gallery stores a labelled
    reference and opens the original URL; it never tries to download a private
    client document with an agency credential.
    """
    url = str(url or "").strip()
    if not url.startswith("https://"):
        return {"ok": False, "error": "Only secure shared-file links can be added."}
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return file_asset(
        client_name=client_name, public_id=f"external/{digest}", url=url,
        kind="creative_information", label="Creative Information",
        filename=filename or "Google Drive file", resource_type="raw",
        provider="google_drive", saved_by=saved_by, push_to_suite=False,
        tool=tool, project_name=project_name, io_number=io_number,
        product_number=product_number, external=True,
    )
