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
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from . import ghl, taxonomy
from .models import PickerClient, SavedImage, new_token, session, slugify, unique_slug, provider_identity

logger = logging.getLogger(__name__)

# Activity logging. Guarded the same way modules/image_picker/app.py guards
# it, so the module still imports standalone -- and called rather than merely
# bound, which is the failure /api/integrity's silent-module check reads a
# real call site for.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("image_picker")
except Exception:                                       # noqa: BLE001
    def _audit(*a, **k):                                # no-op outside the Hub
        return None

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


# --------------------------------------------------------------------------- #
# A file the gallery already has
#
# Until now a duplicate was reported and nothing else: the row stayed exactly
# where it was and whoever uploaded it was told it was already there. That is
# the right answer when somebody uploaded it twice by accident and the wrong
# one every other time -- the same photograph genuinely does belong to a
# second project, and a client who sends it again usually means "use this one
# here as well". There was no way to say so, so the answer was always the one
# that changes nothing.
#
# Three things can be meant, and they are kept apart because they are three
# different statements about the file rather than three strengths of one:
#
#   keep       it belongs in both places. The row that exists is untouched and
#              a second row is recorded against the new project pointing at the
#              SAME Cloudinary asset -- no second copy of the bytes, and
#              deliberately no second push into the client's Suite media
#              library, which would be exactly the duplicate this avoids.
#   duplicate  an independent copy, to edit or delete without touching the
#              original. The one branch that really creates a second asset,
#              and therefore the one that costs storage.
#   move       it belongs to the new project only. The existing row's project
#              and folder fields are rewritten in place. Nothing is created
#              and, in particular, nothing is deleted -- the Cloudinary object
#              is the same object, and a "move" that destroyed a row would be
#              a delete wearing a filing decision.
#
# The default is none of them. Eleven tools file through file_asset() and four
# of them -- the display ad link, blog images, the SEO pipeline and the IO
# builder -- are finishing a piece of work with nobody watching, so a caller
# that says nothing gets precisely what it got before.
# --------------------------------------------------------------------------- #

CHOICES = ("keep", "duplicate", "move")

# What extension to hand hub.storage for a copy, when the filename we hold
# has none or has one that would be read as a different kind of file.
# storage.resource_type_for() derives the Cloudinary resource_type from the
# name, and getting that wrong is the bug that file's own docstring opens
# with: a PDF uploaded as an image stores fine and then 403s on delivery.
_EXT_FOR_TYPE = {"image": ".jpg", "video": ".mp4", "raw": ".pdf"}


def _project_ref(provider_id: str, tail: str) -> str:
    """A provider id for a second row pointing at one asset.

    `SavedImage` carries a unique constraint on (client, provider,
    provider_image_id) -- one provider photo lands in one client's gallery
    once, which is what stops a double-tap duplicating the Cloudinary asset
    and the Suite upload. "Keep it here as well" needs two rows for one
    asset, so the second is spelled with the project it was kept for on the
    end. The constraint still holds, and it still holds against a second
    "keep" into the same project, which is what makes that press safe to make
    twice.

    The base spelling is never re-used, so the row every other caller's
    duplicate check finds is still the original.
    """
    tail = re.sub(r"[^a-z0-9]+", "-", str(tail or "").lower()).strip("-")[:28] or "kept"
    base = str(provider_id or "")
    if len(base) + len(tail) + 1 > 120:
        base = "sha256:" + hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"{base}#{tail}"


def _same_project(row, *, kind: str, key: str, project_name: str) -> bool:
    """Is this row already filed where the caller is trying to file it?"""
    return ((row.collection_kind or "") == (kind or "")
            and (row.collection_key or "") == (key or "")
            and (row.project_name or "") == (project_name or ""))


def filed_under(row) -> str:
    """Where a row sits, in the words a person reads.

    The answer to "you already have this" is useless without it: *where* the
    file already is decides whether keeping, duplicating or moving it is the
    sensible press, and a panel that will not say costs somebody a trip to
    the gallery to find out.
    """
    parts = [str(row.project_name or "").strip(),
             str(row.collection_label or "").strip()]
    parts = [p for p in parts if p]
    if not parts:
        parts = [KIND_LABELS.get(row.collection_kind or "", "")
                 or (row.collection_kind or "the gallery")]
    seen, out = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return " — ".join(out)


def _copy_asset(row) -> dict:
    """Make a second, independent asset out of one already in Cloudinary.

    The only one of the three choices that spends storage, which is why it is
    the one that has to be asked for. Cloudinary fetches the file from its own
    delivery URL rather than this process downloading it and posting it back:
    hub/storage.put_remote() exists for that, and going through the shared
    layer is what keeps the credit estimate counting this.

    The copy lands beside the original, under the original's own public_id
    with a random tail. An explicit public_id with overwrite off hands back
    the asset that is already there, so a copy named deterministically would
    be the original wearing a new row -- which is the one outcome this branch
    must not produce, since somebody is about to edit or delete it.
    """
    src = str(row.cloudinary_url or "")
    base = str(row.cloudinary_public_id or "")[:360]
    if bool(row.external):
        return {"ok": False, "error": "That row is a link to a file kept "
                                      "somewhere else, so there is nothing "
                                      "here to copy."}
    if not base or not src.startswith("https://"):
        return {"ok": False, "error": "That file has no stored copy to duplicate."}

    try:
        from hub import storage as _storage
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"Storage is unavailable: {exc}"}

    rtype = (row.resource_type or "image").strip().lower()
    name = str(row.filename or "").strip() or base.rsplit("/", 1)[-1]
    if _storage.resource_type_for(name) != rtype:
        stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name) or "file"
        name = f"{stem}{_EXT_FOR_TYPE.get(rtype, '.pdf')}"
    try:
        asset = _storage.put_remote(
            "client_images", src, filename=name,
            public_id=f"{base}-copy-{secrets.token_hex(4)}", unique=False)
    except _storage.StorageError as exc:
        # Our own validation text, which is the one exception message that
        # belongs on a screen: "Cloudinary is not configured" names the thing
        # to fix. Anything the SDK raises does not -- it carries hosts, paths
        # and occasionally a credential fragment, which is the failure the two
        # file optimizers were fixed for.
        logger.warning("gallery duplicate copy refused for %s: %s", base, exc)
        return {"ok": False, "error": f"The copy could not be stored: {exc}"}
    except Exception:                                   # noqa: BLE001
        logger.warning("gallery duplicate copy failed for %s", base, exc_info=True)
        return {"ok": False, "error": "The copy could not be stored. The "
                                      "original is untouched; try again in a "
                                      "moment."}
    if not asset.public_id or not str(asset.url or "").startswith("https://"):
        return {"ok": False, "error": "The copy did not come back with a "
                                      "stored address."}
    return {"ok": True, "public_id": asset.public_id, "url": asset.url,
            "bytes": asset.bytes or row.bytes, "filename": name}


def _twin(db, row, *, kind: str, key: str, project_name: str):
    """A row for the same asset already filed under this project, if there is one.

    Keyed on the Cloudinary public_id rather than on `provider_image_id`,
    because that is the identity a kept copy shares with its original -- the
    provider id is the thing _project_ref() has to vary to satisfy the unique
    constraint.
    """
    rows = db.execute(
        select(SavedImage).where(
            SavedImage.client_id == row.client_id,
            SavedImage.provider == row.provider,
            SavedImage.cloudinary_public_id == row.cloudinary_public_id)
    ).scalars().all()
    for candidate in rows:
        if _same_project(candidate, kind=kind, key=key, project_name=project_name):
            return candidate
    return None


def resolve_duplicate(db, row, choice: str, *, kind: str = "", key: str = "",
                      label: str = "", project_name: str = "",
                      io_number: str = "", product_number: str = "",
                      folder: str = "", saved_by: str = "system",
                      push_to_suite: bool = True) -> dict:
    """Act on a file the gallery already has: keep it here too, copy it, or move it.

    One description of what the three choices mean, read by both places a
    duplicate is detected -- `file_asset()` below and the widget upload route
    in app.py. Two would drift, and the first thing to drift would be whether
    "move" deletes anything.

    Never raises: like `file_asset()`, every caller is finishing an upload
    that has already succeeded, and the file existing twice is not a reason
    to lose the answer.
    """
    choice = str(choice or "").strip().lower()
    if choice not in CHOICES:
        return {"ok": False, "duplicate": True, "action": "",
                "error": "Say whether to keep it here as well, duplicate it, "
                         "or move it."}

    kind = (kind or row.collection_kind or "upload").strip().lower()[:20]
    key = str(key or "")[:80]
    label = str(label or "")[:200]
    project_name = str(project_name or "")[:200]

    try:
        if choice == "move":
            # The project and folder fields, and nothing else. `tool` and
            # `completed_on` record how the file was made, which moving it
            # between projects does not change; io/product/asset_folder are
            # rewritten only where this call actually named one, because a
            # move inside a gallery does not move the Cloudinary object and a
            # recomputed folder would have the row claim a place the bytes
            # are not.
            row.collection_kind = kind or None
            row.collection_key = key or None
            row.collection_label = (label or KIND_LABELS.get(kind, "") or "")[:200] or None
            row.project_name = project_name or None
            if io_number:
                row.io_number = str(io_number)[:80]
            if product_number:
                row.product_number = str(product_number)[:80]
            if folder:
                row.asset_folder = str(folder)[:600]
            db.commit()
            _audit("gallery_duplicate", client=_client_name(db, row),
                   choice="move", filename=(row.filename or ""),
                   public_id=(row.cloudinary_public_id or ""),
                   filed_under=filed_under(row), by=str(saved_by or "system"))
            return {"ok": True, "duplicate": True, "action": "move",
                    "created": False, "image": row.to_dict(),
                    "note": f"Moved to {filed_under(row)}."}

        twin = _twin(db, row, kind=kind, key=key, project_name=project_name)
        if choice == "keep" and twin is not None:
            # Already in both places, which is what was asked for. Said as a
            # state rather than reported as a failure: pressing it twice is
            # the ordinary way somebody checks that the first press took.
            return {"ok": True, "duplicate": True, "action": "keep",
                    "created": False, "image": twin.to_dict(),
                    "note": f"It was already filed under {filed_under(twin)}."}

        copy = None
        if choice == "duplicate":
            copy = _copy_asset(row)
            if not copy.get("ok"):
                return {"ok": False, "duplicate": True, "action": "duplicate",
                        "error": copy.get("error") or "The copy could not be made."}

        new = SavedImage(
            client_id=row.client_id,
            provider=row.provider,
            # A kept row points at the original asset, so its provider id has
            # to differ or the unique constraint refuses it. A duplicate is a
            # genuinely new asset and carries its own.
            provider_image_id=(provider_identity(copy["public_id"]) if copy
                               else _project_ref(row.provider_image_id, key or project_name or kind)),
            source_url=(copy["url"] if copy else row.source_url),
            author=row.author,
            author_url=row.author_url,
            alt_text=row.alt_text,
            filename=(copy["filename"][:300] if copy else row.filename),
            industry_key=row.industry_key,
            collection_kind=kind or None,
            collection_key=key or None,
            collection_label=(label or KIND_LABELS.get(kind, "") or "")[:200] or None,
            resource_type=row.resource_type,
            cloudinary_public_id=(copy["public_id"] if copy else row.cloudinary_public_id),
            cloudinary_url=(copy["url"] if copy else row.cloudinary_url),
            width=row.width, height=row.height,
            bytes=(copy.get("bytes") if copy else row.bytes),
            spec_result=row.spec_result,
            spec_summary=row.spec_summary,
            spec_unit=row.spec_unit,
            tool=row.tool,
            completed_on=row.completed_on,
            project_name=project_name or None,
            io_number=(str(io_number)[:80] if io_number else row.io_number),
            product_number=(str(product_number)[:80] if product_number else row.product_number),
            asset_folder=(str(folder)[:600] if folder else row.asset_folder),
            external=bool(row.external),
            ghl_status="pending",
            saved_by=str(saved_by or "system")[:200],
        )
        if choice == "keep":
            # The bytes are already in the client's Suite media library from
            # the first row. Pushing them again would put a second copy there,
            # which is the duplicate this branch exists not to make -- so the
            # twin reports the Suite state its original earned rather than
            # sitting at "pending" for ever.
            new.ghl_status = row.ghl_status
            new.ghl_file_id = row.ghl_file_id
            new.ghl_url = row.ghl_url
            new.ghl_error = row.ghl_error
        db.add(new)
        db.commit()
    except Exception as exc:                            # noqa: BLE001
        try:
            db.rollback()
        except Exception:                               # noqa: BLE001
            logger.warning("duplicate rollback failed", exc_info=True)
        logger.warning("duplicate %s failed: %s", choice, exc, exc_info=True)
        # A sentence rather than the exception. A SQLAlchemy error carries the
        # statement and the connection it was run on, and this one is read by
        # a panel rather than by a log.
        return {"ok": False, "duplicate": True, "action": choice,
                "error": "That could not be saved. Nothing was changed; try "
                         "again in a moment."}

    client_name = _client_name(db, new)
    if choice == "duplicate" and push_to_suite:
        # A genuinely separate file, so it goes to Suite as one. Never raises
        # and answers "skipped" where the gallery has no Suite location, which
        # is why the row is committed before this runs.
        try:
            pushed = ghl.push_image(
                new.client, file_url=new.cloudinary_url,
                name=(new.filename or (new.cloudinary_public_id or "").rsplit("/", 1)[-1]))
            new.ghl_status = pushed["status"]
            new.ghl_file_id = pushed["file_id"] or None
            new.ghl_url = pushed["url"] or None
            new.ghl_error = pushed["error"] or None
            db.commit()
        except Exception:                               # noqa: BLE001
            db.rollback()

    _audit("gallery_duplicate", client=client_name, choice=choice,
           filename=(new.filename or ""),
           public_id=(new.cloudinary_public_id or ""),
           filed_under=filed_under(new), by=str(saved_by or "system"))
    note = (f"Copied into {filed_under(new)}." if choice == "duplicate"
            else f"Kept here as well, under {filed_under(new)}.")
    return {"ok": True, "duplicate": True, "action": choice, "created": True,
            "image": new.to_dict(), "note": note}


def _client_name(db, row) -> str:
    """The gallery's own name, for the activity log.

    Read off the row rather than taken from the caller: `work_log()` reads
    the client from the entry and a name typed at the route is a name that can
    be wrong, which on a client record is the one mistake worth avoiding.
    Never raises -- a log line is not worth losing a filing over.
    """
    try:
        client = db.get(PickerClient, row.client_id)
        return (client.name if client else "") or ""
    except Exception:                                   # noqa: BLE001
        return ""


def file_asset(*, client_name: str, public_id: str, url: str,
               kind: str = "upload", label: str = "", key: str = "",
               filename: str = "", alt: str = "", resource_type: str = "image",
               width=None, height=None, size_bytes=None,
               spec: dict | None = None, provider: str = "",
               saved_by: str = "system", create_client: bool = True,
               push_to_suite: bool = True, tool: str = "",
               completed_on: str = "", project_name: str = "",
               io_number: str = "", product_number: str = "",
               external: bool = False, folder: str = "",
               on_duplicate: str = "") -> dict:
    """Record one asset in a client's gallery.

    Returns a dict with `ok`, and on success the `image` row and `gallery_url`.
    Never raises: every caller is finishing a piece of work that already
    succeeded, and losing a generated blog image because the gallery write
    failed would be a worse outcome than the image not appearing in the gallery.

    `on_duplicate` says what to do about a file this gallery already has --
    "keep", "duplicate" or "move", described at CHOICES above. Anything else,
    including nothing, is the answer this has always given: report the
    duplicate and change not one row. The reply carries `choices` and
    `filed_under` either way, because a screen cannot offer the three without
    knowing where the file already is.
    """
    public_id = str(public_id or "").strip()
    url = str(url or "").strip()
    if not public_id or not url.startswith("https://"):
        return {"ok": False, "error": "That asset has no stored URL."}

    # Provider identity is bounded to 120 characters in Postgres. Preserve the
    # full delivery identity separately; truncation would merge distinct assets
    # whose folder/name prefixes happen to match.
    provider_id = provider_identity(public_id)

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
    # Kept apart from the resolved `folder` below, because the duplicate
    # branch has to be able to tell "the caller named a destination" from
    # "we computed the usual one": moving a file between projects does not
    # move the Cloudinary object, so rewriting a row's folder to a freshly
    # computed default would have it claim a place the bytes are not.
    named_folder = str(folder or "")[:600]
    folder = named_folder or asset_folder(
        client_name=client_name, tool=tool, completed_on=completed_on,
        io_number=io_number, product_number=product_number,
        project_name=project_name)

    db = None
    try:
        db = session()
        client = gallery_for_name(db, client_name, create=create_client)
        if client is None:
            return {"ok": False, "error": "No gallery for that client."}

        existing = db.execute(
            select(SavedImage).where(SavedImage.client_id == client.id,
                                     SavedImage.provider == provider,
                                     SavedImage.provider_image_id.in_([provider_id, public_id]))
        ).scalar_one_or_none()
        if existing:
            gallery_url = f"/tools/image-picker/gallery/{client.id}"
            choice = str(on_duplicate or "").strip().lower()
            if choice not in CHOICES:
                return {"ok": True, "duplicate": True, "image": existing.to_dict(),
                        "gallery_url": gallery_url,
                        "choices": list(CHOICES),
                        "filed_under": filed_under(existing)}
            out = resolve_duplicate(
                db, existing, choice, kind=kind, key=str(key or "")[:80],
                label=str(label or KIND_LABELS.get(kind, "") or "")[:200],
                project_name=project_name, io_number=io_number,
                product_number=product_number, folder=named_folder,
                saved_by=saved_by, push_to_suite=push_to_suite)
            out["gallery_url"] = gallery_url
            out.setdefault("choices", list(CHOICES))
            return out

        rtype = (resource_type or "image").strip().lower()
        img = SavedImage(
            client_id=client.id,
            provider=provider,
            provider_image_id=provider_id,
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
        if db is not None:
            # This session is reused by background batches. A failed flush
            # must not poison every later asset in the same worker.
            try:
                db.rollback()
            except Exception:                           # noqa: BLE001
                logger.warning("gallery filing rollback failed", exc_info=True)
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
