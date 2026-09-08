"""Ad Assets — creative out of Google Drive and into the client library.

## The situation this fixes

Creative that ran on a campaign lives in a Google Drive folder, and Knack's
product record carries the address of it — up to four External Creative Links
per product, `knack_products.F_CREATIVE_URLS`. Every screen in the Hub that
shows a client their creative is showing a Drive URL: Client 360's Creative
Information card, the Clients module, the Stale Creative audit's evidence.

That is an address in somebody else's filing cabinet. It moves when a folder
is reorganised, it dies when the person who owned it leaves, it 403s when a
share is tightened — and none of that changes how the row looks on Client 360,
which is the failure this repo counts a dozen of: a link that exists and
nobody can reach.

The client library already exists (`modules/image_picker`), already carries
`io_number` and `product_number` on every row, and is already what the Gallery
links on Client 360 open. What it did not have was the creative itself.

This module copies it in. Folder shape is `filing.ad_asset_folder()`:

    client-assets/<client>/ad-assets/io-<io>/product-<n>/<drive subfolders>

## Four rules, each of which is a way to be quietly wrong

**A copy, never a move.** Nothing here writes to Drive: no trash, no rename,
no re-share. The original folder is left exactly as it is, because the day
after a migration is the wrong day to discover the copy missed something and
find the source gone too. Removing the Drive originals is a separate decision
somebody makes later, with the library in front of them.

**Refused is not empty.** A Drive folder we cannot read and a Drive folder
with nothing in it produce the same number of files and mean opposite things.
`hub/drive_files.access()` answers with a reason, every skipped link carries
why it was skipped, and a run that could not authenticate at all reports that
rather than a tidy "0 assets migrated" — the same distinction google_finder
had to learn for Tag Manager scopes, and the same one that made this repo's
"no clarification needed" default wrong.

**Filed twice is worse than filed once.** The Drive file id is written to the
gallery row's `collection_key` as `gdrive:<id>`, and that is what a re-run
matches on. Not the URL (the same file arrives under three URL shapes), not
the filename ("final.jpg" is every client's whole Drive), and not the
Cloudinary public id (which is derived, so it changes when the folder shape
does). A second run over the same client is a no-op that says so.

**A client is selected, never typed from memory.** The box on this page
asked for the client name *exactly as Smart 1 Team has it*, and a name one
character out matched nothing and was answered with "no Google Drive creative
links on this client" -- a clean nothing about a client with a year of
creative in Drive, which is the confident wrong answer this whole module
exists downstream of. It is a searchable list of the real client book now, and
selecting one runs `lookup()`: the reverse lookup against the product records,
saying what Knack actually holds -- the IOs, the product lines, the Drive links
and how much is already filed -- before a byte is read. Matching is on the
client field **and** the organisation field, which is the rule
`knack_products.for_client()` already applies, and it is exact or nothing:
"Riverside HVAC" must not collect "Riverside HVAC Supply", because copying one
company's creative into another company's library is billed and is not
undoable from the gallery. A near name is listed as a *did you mean* and never
acted on.

**Knack is not written to by a migration.** Rewriting the External Creative
Link fields to point at the library is the right end state and it is a write
to the system of record, so it is proposed here and applied separately, from
a list a person has read — `proposals()` and `apply_proposals()`. Until then
the Hub resolves to the library copy at display time and Knack keeps its Drive
URL, so nothing on Client 360 waits for that decision.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import re

from flask import Blueprint, jsonify, render_template, request

from hub import drive_files, jsonstore, knack_products, storage
# Product names whose "creative link" is never artwork. Imported rather than
# retyped: `knack_data.CREATIVE_EXCLUDE` is the list Client 360's Creative
# Information card and the Stale Creative audit both read, and a second copy
# here is how the three quietly stop agreeing about what counts as creative.
from hub.knack_data import CREATIVE_EXCLUDE as EXCLUDE

logger = logging.getLogger(__name__)

bp = Blueprint("ad_assets", __name__)

TOOL = "ad-assets"
KIND = "ad_asset"
PROVIDER = "google_drive"
# What these used to be, and what a bare relative path cost. `write_json` hands
# the path to `_atomic_write`, which resolves it against the **process working
# directory** rather than the data root -- so on Render these two landed at
# `/app/ad_assets/*.json`, inside the container image, and `key_for()` saw a
# path outside the root and keyed the mirror `abs:/app/...` instead of a
# root-relative key.
#
# Neither file was ever lost by that on its own: the mirror is written at save
# time and `read_json` restores by key, and WORKDIR is stable. What was lost is
# the **repair**. `sweep()` walks the data root, so it never scanned these --
# measured, `scanned: 1` over a rooted file and this one side by side -- and a
# save made while the mirror was unavailable is exactly what that sweep exists
# to pick up afterwards. For every other store in the Hub it does; for these
# two the gap stood until somebody saved again, and the next redeploy took the
# file with it. The `abs:` key also defeats the one property `key_for()`'s own
# docstring names: a production blob restoring into a development checkout.
#
# The old spellings are still **read**, so nothing already recorded is
# orphaned -- the rule `audit.LOG_NAMES` and `video_library.TAG_ALIASES` work
# to. Every write goes to the rooted path, so each store moves itself the
# first time it is written.
LEGACY_PROPOSALS_PATH = "ad_assets/knack_proposals.json"
LEGACY_RUNS_PATH = "ad_assets/runs.json"


def _proposals_path() -> str:
    """Which Knack creative links we have already rewritten.

    Resolved at call time rather than fixed at import, because `data_root()`
    reads `HUB_DATA_DIR` and a constant frozen at import is the trap
    `config.settings` already carries -- a test or a correction made after the
    module loads would be answered with the old root.
    """
    return os.path.join(jsonstore.data_dir("ad_assets"), "knack_proposals.json")


def _runs_path() -> str:
    """The log of who migrated what, for which client, and when."""
    return os.path.join(jsonstore.data_dir("ad_assets"), "runs.json")


def _read_legacy(legacy: str, default):
    """Read a store from where it lived before the move.

    Restoring is left on: after a redeploy the pre-move file is gone from the
    image and the mirror is the only copy, so this is what recovers it. The
    cost is that `read_json` writes what it restored back to the old location,
    which is why every caller reaches this only while the rooted store is
    empty -- once, rather than on every run.
    """
    return jsonstore.read_json(legacy, default=default)


def _read_store(path: str, legacy: str, default):
    """Read the rooted file, falling back to the pre-move one.

    Only when the rooted file holds nothing: a store that has been written
    since the move is the answer, and consulting the old location past that
    point would resurrect rows somebody has since removed.
    """
    rows = jsonstore.read_json(path, default=None)
    if rows:
        return rows
    return _read_legacy(legacy, default)

MAX_FILE_MB = int(os.environ.get("AD_ASSETS_MAX_FILE_MB") or 200)


# ---------------------------------------------------------------------------
# What is out there
# ---------------------------------------------------------------------------

def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _is_theirs(row: dict, want: set) -> bool:
    """Does this product row belong to the client `want` describes?

    Client *or* organisation, the rule `knack_products.for_client()` already
    applies: Knack holds both and a product is filed under whichever the
    salesperson used, so reading one field answered "this client has no
    creative" about a client whose whole book is under the other.

    One reading, because `candidates()` and `lookup()` both ask it and two
    copies of a match rule is how the count on the page and the files the run
    copies come to disagree about which rows are the client's.
    """
    if not want:
        return True
    return bool({_norm(row.get("client")), _norm(row.get("organization"))} & want)


def _variants(client: str) -> dict:
    """Every spelling that means one client, resolved once.

    The box on this page used to ask for the name *exactly as Smart 1 Team
    has it*, which is the whole difficulty wearing a placeholder. The name a
    rep knows is the one on Client 360; Knack files a product under either
    its client field or its organisation field, and a line written up by a
    different salesperson carries a third spelling again. A name one
    character out matched nothing and was answered with "no Google Drive
    creative links on this client" -- a clean nothing, which is the answer
    this module exists not to give.

    Three rules, each a way that goes quietly wrong.

    **Exact or nothing.** The registry lookup is `client_key.resolve()` with
    no fuzzy pass, so "Riverside HVAC" cannot collect "Riverside HVAC
    Supply": copying one company's creative into another company's library
    is the worst outcome available here, it is billed, and it is not
    undoable from the gallery. A near name is *named* by `lookup()` and
    never acted on.

    **The name given is always kept.** A client the registry has never heard
    of -- one written up on their first insertion order -- still has product
    records in Knack, and a lookup that consulted only the registry would
    refuse exactly the client whose creative has never been filed anywhere.

    **The registry's spelling is what it files under**, so two spellings of
    one company cannot become two galleries. What makes that safe rather
    than a re-copy is that `filed_keys()` reads *every* variant's gallery: a
    client filed last month under the product record's own wording is
    already filed as far as this run is concerned.
    """
    given = str(client or "").strip()
    if not given:
        return {"given": "", "file_as": "", "names": [], "norms": set(),
                "resolved": {}, "registry_error": ""}

    resolved, registry_error = {}, ""
    try:
        from hub import client_key
        resolved = client_key.resolve(given)
    except Exception as exc:                            # noqa: BLE001
        # "We could not read the client book" is not "nobody is called that".
        # The run still goes ahead under the name it was given -- refusing
        # over a registry outage would take the tool down for a lookup it
        # only uses to improve a spelling.
        registry_error = f"{type(exc).__name__}: {exc}"[:200]
        logger.warning("ad_assets: the client registry could not be read: %s", exc)

    canonical = (str(resolved.get("client") or "").strip()
                 if resolved.get("known") else "")
    norms = {n for n in (_norm(given), _norm(canonical)) if n}

    names, seen = [], set()
    for name in (canonical, given):     # the registry's spelling leads
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    # Knack's own spellings. These add no new normalised form -- a value is
    # only collected where it already matches one -- so this widens what is
    # *read* and never what is *matched*, which is the whole of the exact-or-
    # nothing rule above.
    try:
        for row in knack_products.rows().get("rows") or []:
            for field in ("client", "organization"):
                value = str(row.get(field) or "").strip()
                if value and _norm(value) in norms and value.lower() not in seen:
                    seen.add(value.lower())
                    names.append(value)
    except Exception:                                   # noqa: BLE001
        pass

    return {"given": given, "file_as": names[0] if names else given,
            "names": names, "norms": norms, "resolved": resolved,
            "registry_error": registry_error}


def candidates(client: str = "", *, live_only: bool = False,
               norms: set | None = None) -> dict:
    """Every product line carrying a Drive creative link.

    One entry per (product record, link): a product with a proof and two
    revisions is three rows here, because each is a folder somebody has to be
    able to open and each migrates or fails on its own.

    `norms` is the resolved spelling set from `_variants()`. Passed, it is
    what decides the match; a caller that hands over only a name gets that
    name's own normalised form, which is what this did before there was a
    picker in front of it.
    """
    got = knack_products.rows()
    want = set(norms) if norms is not None else ({_norm(client)} if client else set())
    want.discard("")
    out, skipped_not_drive = [], 0
    for row in got.get("rows") or []:
        if not _is_theirs(row, want):
            continue
        if live_only and str(row.get("status") or "").lower() != "live":
            continue
        if any(x in str(row.get("product") or "").lower() for x in EXCLUDE):
            continue
        for url in row.get("creative_urls") or []:
            if not drive_files.is_drive(url):
                skipped_not_drive += 1
                continue
            out.append({
                "client": row.get("client") or row.get("organization") or "",
                "io": str(row.get("io") or ""),
                "product": row.get("product") or "",
                "product_num": str(row.get("product_num") or ""),
                "record_id": str(row.get("record_id") or ""),
                "status": row.get("status") or "",
                "url": url,
            })
    return {"ok": True, "source": got.get("source"),
            "age_minutes": got.get("age_minutes"),
            "links": out, "clients": sorted({c["client"] for c in out if c["client"]}),
            "non_drive_links": skipped_not_drive}


def lookup(client: str) -> dict:
    """What Smart 1 Team holds for one client, before a byte is copied.

    The reverse lookup the picker runs on selection: name a client and this
    answers with the product records filed under them, the IOs those sit on,
    the Drive links on them and how much of it is already in the library --
    all of it off the cached product rows, with no Drive call and no write.

    Four answers, kept apart because they send somebody to four different
    places, and all four used to render as the same empty run:

    * **Links to pull in.** The ordinary case.
    * **Products, and no Drive link on any of them.** Their creative is
      somewhere else, or the links are click-thrus rather than artwork --
      `non_drive_links` says which.
    * **A client we know, with no product records under that name.** A house
      client, or one whose campaign has not been written up yet.
    * **Nothing filed under this name at all**, with the near names *listed*
      rather than one of them chosen. `resolve()` is asked for its fuzzy pass
      only here, only to say "did you mean", and what it returns is printed
      and never acted on -- the rule `hub/client_urls.py` and the Google
      orphan list both work to.

    And a fifth state that is not one of the four: **the product records
    could not be read at all**. `knack_products.rows()` never raises -- it
    falls back to a stale cache, then to the private export, then to nothing
    -- and that last answer is indistinguishable from a client with no
    campaigns. `measured` is False there and the note says so, because "we
    could not look" rendered as "nothing is filed under this name" is the
    confident wrong answer this whole module exists downstream of.
    """
    variants = _variants(client)
    if not variants["given"]:
        return {"ok": False, "error": "Which client?"}

    got = knack_products.rows()
    want = variants["norms"]
    records, ios, spellings, live = set(), set(), [], 0
    for row in got.get("rows") or []:
        if not _is_theirs(row, want):
            continue
        records.add(str(row.get("record_id") or row.get("id") or ""))
        if str(row.get("io") or ""):
            ios.add(str(row.get("io")))
        if str(row.get("status") or "").lower() == "live":
            live += 1
        for field in ("client", "organization"):
            value = str(row.get(field) or "").strip()
            if value and value not in spellings:
                spellings.append(value)

    found = candidates(variants["file_as"], norms=want)
    links = found["links"]

    filed = 0
    try:
        filed = len(filed_keys(variants["file_as"], names=variants["names"]))
    except Exception:                                   # noqa: BLE001
        filed = 0

    resolved = variants["resolved"] or {}
    measured = str(got.get("source") or "none") != "none"
    out = {
        "ok": True,
        "measured": measured,
        "query": variants["given"],
        "client": variants["file_as"],
        "names": variants["names"],
        "spellings": spellings,
        "known": bool(resolved.get("known")),
        "matched_on": resolved.get("matched_on", ""),
        "confidence": resolved.get("confidence", ""),
        "why": resolved.get("why", ""),
        "registry_error": variants["registry_error"],
        "products": len(records),
        "live_products": live,
        "ios": sorted(ios),
        "links": links,
        "link_count": len(links),
        "non_drive_links": found["non_drive_links"],
        "already_filed": filed,
        "source": got.get("source"),
        "age_minutes": got.get("age_minutes"),
        "near": [],
    }

    name = variants["file_as"]
    if not measured:
        out["note"] = (
            "The product records in Smart 1 Team could not be read"
            + (f" \u2014 {got.get('note')}" if got.get("note") else "")
            + ". Nothing was looked up, so this is not a report that "
              f"{name} has no creative.")
        return out
    if links:
        out["note"] = (f"{len(links)} Google Drive link(s) on {len(records)} "
                       f"product line(s) for {name}.")
    elif records:
        out["note"] = (f"Smart 1 Team has {len(records)} product line(s) for "
                       f"{name}, and none of them carries a Google Drive "
                       f"creative link.")
    else:
        if out["known"]:
            out["note"] = (f"The client book knows {name}, and Smart 1 Team "
                           f"has no product records filed under that name.")
        else:
            out["note"] = ("Nothing in Smart 1 Team is filed under "
                           f"\u201c{variants['given']}\u201d.")
            try:
                from hub import client_key
                near = client_key.resolve(variants["given"], allow_fuzzy=True)
                out["near"] = ([near["client"]] if near.get("known")
                               else list(near.get("candidates") or []))[:8]
            except Exception:                           # noqa: BLE001
                pass
    if len(variants["names"]) > 1:
        out["note"] += (" Looked under: "
                        + ", ".join(variants["names"][:4]) + ".")
    return out


# ---------------------------------------------------------------------------
# What is already filed
# ---------------------------------------------------------------------------

def filed_keys(client: str, *, names: list | None = None) -> dict:
    """`gdrive:<id>` -> the stored URL, for everything already in the library.

    Read once per run rather than per file: a client with 300 assets would
    otherwise be 300 queries to answer a question one query answers.

    **Every spelling, not only the one this run files under.** The picker
    resolves a selected client to the registry's name, and a client whose
    creative was filed by an earlier run under the product record's own
    wording sits in a gallery with a different slug. Read one of the two and
    the dedupe key finds nothing, so the second run copies the whole folder
    again -- which is not a duplicate row, it is a duplicate upload, billed,
    into a second gallery nobody opens.
    """
    try:
        from modules.image_picker.filing import gallery_for_name
        from modules.image_picker.models import SavedImage, session
        from sqlalchemy import select
    except Exception as exc:                            # noqa: BLE001
        logger.warning("ad_assets: the client library is not importable: %s", exc)
        return {}
    wanted = [n for n in (names or [client]) if str(n or "").strip()]
    out: dict[str, str] = {}
    try:
        db = session()
        try:
            seen_galleries = set()
            for name in wanted:
                gallery = gallery_for_name(db, name)
                if gallery is None or gallery.id in seen_galleries:
                    continue
                seen_galleries.add(gallery.id)
                rows = db.execute(
                    select(SavedImage).where(SavedImage.client_id == gallery.id)
                ).scalars().all()
                for row in rows:
                    key = row.collection_key or ""
                    if key.startswith("gdrive:"):
                        out.setdefault(key, row.cloudinary_url or "")
        finally:
            db.close()
    except Exception as exc:                            # noqa: BLE001
        logger.warning("ad_assets: could not read %s's library: %s", client, exc)
        return {}
    return out


def library_index(client: str, *, names: list | None = None) -> dict:
    """Original Drive URL -> the library copy, for one client.

    What Client 360 reads to show the copy instead of the Drive link. Keyed on
    the URL Knack holds rather than on the file id, because that is the string
    the record carries and the string the page has in hand.

    Every spelling of the client, for `filed_keys()`'s reason read from the
    other end: Client 360 asks under the name *it* holds, a run files under
    the registry's, and a card that could not find the copy shows the Drive
    link with nothing saying a copy exists.
    """
    try:
        from modules.image_picker.filing import gallery_for_name
        from modules.image_picker.models import SavedImage, session
        from sqlalchemy import select
    except Exception:                                   # noqa: BLE001
        return {}
    wanted = names
    if wanted is None:
        try:
            wanted = _variants(client)["names"]
        except Exception:                               # noqa: BLE001
            wanted = [client]
    wanted = [n for n in (wanted or [client]) if str(n or "").strip()]

    found: list[tuple] = []
    try:
        db = session()
        try:
            seen_galleries = set()
            for name in wanted:
                gallery = gallery_for_name(db, name)
                if gallery is None or gallery.id in seen_galleries:
                    continue
                seen_galleries.add(gallery.id)
                rows = db.execute(
                    select(SavedImage).where(SavedImage.client_id == gallery.id,
                                             SavedImage.tool == TOOL)
                ).scalars().all()
                found.extend((gallery.id, row) for row in rows)
        finally:
            db.close()
    except Exception:                                   # noqa: BLE001
        return {}

    index: dict[str, dict] = {}
    for gallery_id, row in found:
        origin = str(row.source_url or "")
        if not origin:
            continue
        entry = index.setdefault(origin, {
            "count": 0, "files": [],
            "gallery": f"/tools/image-picker/gallery/{gallery_id}"})
        entry["count"] += 1
        if len(entry["files"]) < 24:
            entry["files"].append({
                "url": row.cloudinary_url or "",
                "filename": row.filename or "",
                "io": row.io_number or "",
                "product": row.product_number or "",
                "folder": row.asset_folder or "",
            })
    return index


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def migrate(client: str, *, apply: bool = False, actor: str = "",
            live_only: bool = False, limit: int = 500) -> dict:
    """Copy one client's Drive creative into their library.

    `apply=False` is the honest dry run: it authenticates, walks every folder
    and lists exactly what would be copied and what would be skipped, without
    downloading a byte or writing a row.

    The name is resolved through `_variants()` first, so what the picker
    selects and what Knack calls the same company do not have to be the same
    string, and so a rep who typed a spelling nobody files under is not
    answered with an empty run.
    """
    variants = _variants(client)
    if not variants["given"]:
        return {"ok": False, "error": "Which client?"}
    # Filed under the registry's spelling where it knows one, so two spellings
    # of one company cannot become two galleries. `filed_keys()` reads every
    # variant, so a client already filed under the Knack wording is not copied
    # a second time by the change of name.
    client = variants["file_as"]

    found = candidates(client, live_only=live_only, norms=variants["norms"])
    links = found["links"][:max(1, int(limit or 500))]
    if not links:
        note = (f"No Google Drive creative links on {client}'s product "
                f"records in Smart 1 Team.")
        if len(variants["names"]) > 1:
            note += " Looked under: " + ", ".join(variants["names"][:4]) + "."
        return {"ok": True, "client": client, "apply": apply, "links": 0,
                "names": variants["names"],
                "copied": [], "skipped": [], "failed": [], "note": note}

    auth = drive_files.access()
    if not auth["ok"]:
        return {"ok": False, "client": client, "reason": auth["reason"],
                "error": auth["detail"], "links": len(links),
                "note": "Nothing was read from Drive, so this is not a report "
                        "that there is nothing there."}
    token = auth["token"]

    already = filed_keys(client, names=variants["names"]) if apply else {}
    copied, skipped, failed = [], [], []

    for link in links:
        try:
            items = drive_files.files_for(token, link["url"])
        except drive_files.DriveRefused as exc:
            failed.append({**link, "error": exc.detail, "reason": exc.reason})
            continue
        except Exception as exc:                        # noqa: BLE001
            failed.append({**link, "error": f"{type(exc).__name__}: {exc}"[:200],
                           "reason": "error"})
            continue
        if not items:
            skipped.append({**link, "reason": "empty",
                            "detail": "That Drive folder has no files in it."})
            continue

        for item in items:
            key = f"gdrive:{item.get('id')}"
            plan = {
                "client": client, "io": link["io"],
                "product_num": link["product_num"], "product": link["product"],
                "source_url": link["url"], "drive_id": item.get("id", ""),
                "filename": item.get("name", ""), "path": item.get("path", ""),
                "mime": item.get("mimeType", ""),
                "folder": _folder_for(client, link, item),
            }
            if key in already:
                skipped.append({**plan, "reason": "already_filed",
                                "url": already[key]})
                continue
            if not apply:
                copied.append({**plan, "planned": True})
                continue
            result = _copy_one(token, item, link, client, actor)
            if result.get("ok"):
                already[key] = result["url"]
                copied.append({**plan, "url": result["url"]})
            else:
                failed.append({**plan, "error": result.get("error", ""),
                               "reason": result.get("reason", "error")})

    out = {"ok": True, "client": client, "apply": apply, "account": auth["email"],
           "names": variants["names"],
           "links": len(links), "copied": copied, "skipped": skipped,
           "failed": failed,
           "counts": {"copied": len(copied), "skipped": len(skipped),
                      "failed": len(failed)}}
    if apply:
        _record_run(out, actor)
    return out


def _folder_for(client: str, link: dict, item: dict) -> str:
    from modules.image_picker.filing import ad_asset_folder
    return ad_asset_folder(client_name=client, io_number=link["io"],
                           product_number=link["product_num"],
                           subpath=item.get("path", ""))


def _copy_one(token: str, item: dict, link: dict, client: str,
              actor: str) -> dict:
    """Drive bytes -> Cloudinary -> one library row. Never raises."""
    from modules.image_picker.filing import file_asset
    try:
        data, filename = drive_files.download(
            token, item, max_bytes=MAX_FILE_MB * 1024 * 1024)
    except drive_files.DriveRefused as exc:
        return {"ok": False, "error": exc.detail, "reason": exc.reason}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "reason": "download"}

    folder = _folder_for(client, link, item)
    try:
        stored = storage.put(
            "ad_assets", filename, data, folder=folder,
            context={"client": client, "io": link["io"],
                     "product": link["product_num"],
                     "source": link["url"]},
            tags=["ad-assets", f"io-{link['io'] or 'unassigned'}"])
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "reason": "storage"}

    filed = file_asset(
        client_name=client, public_id=stored.public_id, url=stored.url,
        kind=KIND, key=f"gdrive:{item.get('id')}",
        label="Ad Assets", filename=filename,
        alt=f"{link['product'] or 'Campaign creative'} — IO {link['io'] or '—'}",
        resource_type=stored.resource_type, size_bytes=stored.bytes,
        provider=PROVIDER, saved_by=actor or TOOL, tool=TOOL,
        io_number=link["io"], product_number=link["product_num"],
        project_name=link["product"], folder=folder,
    )
    if not filed.get("ok"):
        return {"ok": False, "error": filed.get("error", "Filing failed."),
                "reason": "filing"}
    # The row's source_url is the Drive address it came from, which is what
    # library_index() keys on and what makes the copy traceable back to the
    # folder the media team is still working out of.
    _set_source_url(filed.get("image") or {}, link["url"])
    return {"ok": True, "url": stored.url, "public_id": stored.public_id}


def _set_source_url(image: dict, url: str) -> None:
    """Point the filed row's source_url at Drive rather than at Cloudinary.

    `file_asset()` stores the URL it was given as both the stored copy and the
    source, which is true for a stock photo and wrong here: the source of this
    file is the Drive folder, and losing that is losing the only thread back to
    where the media team still keeps it.
    """
    try:
        from modules.image_picker.models import SavedImage, session
        db = session()
        try:
            row = db.get(SavedImage, image.get("id"))
            if row is not None:
                row.source_url = url[:500]
                db.commit()
        finally:
            db.close()
    except Exception:                                   # noqa: BLE001
        pass


def _record_run(result: dict, actor: str) -> None:
    """Write this run into the log, without dropping a concurrent one.

    Read-the-whole-list, insert, write-the-whole-list back is the shape
    `jsonstore.update_json` exists for: two runs starting from one snapshot
    and the second to finish silently drops the first. It is not theoretical
    on this store -- the scheduled catch-up sweep and a rep pressing Migrate
    overlap by design, there are two gunicorn workers, and eight concurrent
    records measured **1 of 8 kept** before this. What is lost is the only
    account of who moved which client's creative and when.
    """
    entry = {
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "client": result.get("client", ""), "actor": actor or "system",
        "counts": result.get("counts", {}),
        "account": result.get("account", ""),
    }

    def add(runs):
        # The fallback is read *here* rather than passed as `default`, and the
        # difference is not tidiness. A `default=` argument is evaluated on
        # every call whether or not it is needed, and `read_json` writes a
        # restored blob back to disk -- so reading the old location eagerly
        # re-created the very file this move exists to abandon, on every run,
        # for ever. Inside the mutate it is reached only where the rooted
        # store is empty, which is once.
        if not runs:
            runs = _read_legacy(LEGACY_RUNS_PATH, {"runs": []})
        runs = runs if isinstance(runs, dict) else {"runs": []}
        rows = runs.setdefault("runs", [])
        rows.insert(0, entry)
        runs["runs"] = rows[:200]
        return runs

    jsonstore.update_json(_runs_path(), add, default=None)
    try:
        from hub import audit
        audit.log(TOOL, "migrated", actor=actor, client=result.get("client", ""),
                  detail=str(result.get("counts", {})))
    except Exception:                                   # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Proposed Knack rewrites — read, then applied on purpose
# ---------------------------------------------------------------------------

def proposals(client: str = "") -> dict:
    """Which Knack creative links now have a library copy behind them.

    A proposal is only made where **every** file behind that link was copied.
    A folder half-migrated is a link that must keep pointing at Drive, because
    replacing it would hide the files that did not come across.
    """
    found = candidates(client)
    by_client: dict[str, dict] = {}
    out = []
    for link in found["links"]:
        name = link["client"]
        if not name:
            continue
        if name not in by_client:
            by_client[name] = library_index(name)
        entry = (by_client[name] or {}).get(link["url"])
        if not entry:
            continue
        out.append({
            **link,
            "files": entry["count"],
            "gallery_url": _scoped_gallery(name, link),
            "library": entry["files"][:4],
        })
    stored = _read_store(_proposals_path(), LEGACY_PROPOSALS_PATH,
                         {"applied": []}) or {}
    applied = {a.get("key") for a in stored.get("applied", [])}
    for row in out:
        row["applied"] = _proposal_key(row) in applied
    return {"ok": True, "proposals": out, "count": len(out)}


def _proposal_key(row: dict) -> str:
    return f"{row.get('record_id')}|{row.get('url')}"


def _scoped_gallery(client: str, link: dict) -> str:
    from urllib.parse import quote
    scope = (f"&product={quote(link['product_num'])}" if link["product_num"]
             else (f"&io={quote(link['io'])}" if link["io"] else ""))
    return (f"/tools/image-picker/gallery/for-client?name={quote(client)}"
            f"{scope}")


def apply_proposals(keys: list, *, actor: str = "") -> dict:
    """Write the chosen links back onto their Knack product records.

    Explicitly keyed rather than "apply everything that qualifies": the list a
    person read and the list a second call recomputes are not the same list,
    and the difference between them is exactly the rows nobody looked at.
    """
    from hub import knack_api
    if not knack_api.configured():
        return {"ok": False, "error": "Knack API credentials aren't set, so "
                                      "nothing can be written back."}
    wanted = {str(k) for k in (keys or []) if k}
    if not wanted:
        return {"ok": False, "error": "Nothing was selected."}

    ready = {_proposal_key(p): p for p in proposals().get("proposals", [])}
    done, failed, fresh = [], [], []
    for key in wanted:
        row = ready.get(key)
        if not row:
            failed.append({"key": key, "error": "That link is no longer "
                                                "fully migrated."})
            continue
        res = knack_api.set_creative_url(row["record_id"], row["url"],
                                         row["gallery_url"])
        if res.get("ok"):
            fresh.append({"key": key, "client": row["client"],
                          "io": row["io"], "from": row["url"],
                          "to": row["gallery_url"], "actor": actor or "system",
                          "at": _dt.datetime.now(_dt.timezone.utc)
                          .isoformat(timespec="seconds")})
            done.append(key)
        else:
            failed.append({"key": key, "error": res.get("error", "")})

    # Every Knack write is finished before the store is touched, deliberately.
    # `update_json` holds a lock across two workers, and a network call made
    # inside it would hold the other worker off for as long as Knack takes to
    # answer -- so the lock covers the append and nothing else. Recorded only
    # where Knack accepted: a rewrite it refused has not happened, and a row
    # claiming it did is what makes this file unreadable as a record.
    if fresh:
        def add(stored):
            if not stored:
                stored = _read_legacy(LEGACY_PROPOSALS_PATH, {"applied": []})
            stored = stored if isinstance(stored, dict) else {"applied": []}
            rows = stored.setdefault("applied", [])
            rows.extend(fresh)
            stored["applied"] = rows[-2000:]
            return stored

        jsonstore.update_json(_proposals_path(), add, default=None)
    return {"ok": bool(done), "applied": len(done), "failed": failed}


# ---------------------------------------------------------------------------
# The catch-up sweep
# ---------------------------------------------------------------------------

def sweep(limit_clients: int = 25, actor: str = "scheduler") -> dict:
    """Clients with Drive creative that is not in their library yet.

    Run nightly. It is the same `migrate()` every client gets by hand, which
    is the point: a tool that only ever runs when somebody remembers is a
    backfill, and the folder full of new creative from last Thursday is
    exactly the one nobody remembers.
    """
    auth = drive_files.access()
    if not auth["ok"]:
        return {"ok": False, "reason": auth["reason"], "error": auth["detail"],
                "clients": 0}
    names = candidates().get("clients", [])[:max(1, int(limit_clients or 25))]
    results, copied = [], 0
    for name in names:
        res = migrate(name, apply=True, actor=actor)
        counts = res.get("counts", {})
        copied += counts.get("copied", 0)
        results.append({"client": name, **counts,
                        "error": res.get("error", "")})
    return {"ok": True, "clients": len(names), "copied": copied,
            "results": results}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
#
# A blueprint registered on the hub app is not behind AuthGuard -- that
# middleware only wraps dispatcher-mounted apps in wsgi.py, and the hub app
# has no blanket gate of its own. Without this, every route below (client
# names, IO numbers and product identifiers included, plus the two writes
# that copy files and rewrite Knack) answers 200 to anyone with the URL, the
# way the Commercial Builder once did. Nothing here is client-facing, so
# there is no `public` prefix to carve out.

try:                                                    # pragma: no cover
    from hub import blueprint_guard
    blueprint_guard.install(bp)
except Exception:                                       # noqa: BLE001
    pass


def _actor() -> str:
    try:
        from hub import current_user
        return current_user() or ""
    except Exception:                                   # noqa: BLE001
        return ""


@bp.route("/tools/ad-assets")
def page():
    return render_template("ad_assets.html")


@bp.route("/api/ad-assets/candidates")
def api_candidates():
    client = request.args.get("client", "")
    norms = _variants(client)["norms"] if client else None
    return jsonify(candidates(client, live_only=request.args.get("live") == "1",
                              norms=norms))


@bp.route("/api/ad-assets/clients")
def api_clients():
    """The picker: the Hub's own client book, marked with what Knack holds.

    A searchable list of real clients rather than a text box, for the reason
    `hub/client_key.py` gives at length -- a name typed one character out
    matches nothing and reads as a client with no creative. Each row carries
    the number of Drive links on that client's product records, so somebody
    can tell "nothing to pull in" from "I have not selected them yet" before
    pressing anything.

    Clients with no Drive creative are **marked, never filtered out**: a
    client missing from the box reads as a broken search, and zero is a real
    answer that this page is the right place to give.

    `(clients, error)`, because "nobody is called that" and "we could not
    read the book" are different answers and only the first means stop
    looking -- `connected_accounts_result()`'s rule, one tool along.
    """
    from hub.webargs import clamp_int
    query = request.args.get("q", "")
    limit = clamp_int(request.args.get("limit"), 12, 1, 50)
    try:
        from hub import clients_registry
        rows = clients_registry.search_clients(query, limit=limit) or []
    except Exception as exc:                            # noqa: BLE001
        logger.warning("ad_assets: client search failed: %s", exc)
        return jsonify({"ok": False, "clients": [],
                        "error": "The client book could not be read, so this "
                                 "is not a list of everybody we have."})

    have: dict[str, int] = {}
    knack_error = ""
    try:
        found = candidates()
        # A product source that answered with nothing at all is not a book
        # with no Drive creative in it. Counted as measured, every row would
        # read "no Drive links" and the picker would be quietly telling
        # somebody there is nothing to pull in for anybody.
        if str(found.get("source") or "none") == "none":
            knack_error = "The product records could not be read."
        for link in found["links"]:
            key = _norm(link["client"])
            if key:
                have[key] = have.get(key, 0) + 1
    except Exception as exc:                            # noqa: BLE001
        knack_error = f"{type(exc).__name__}: {exc}"[:200]

    out = []
    for row in rows:
        name = row.get("name") or ""
        out.append({
            "name": name,
            "domain": row.get("domain") or "",
            "products": row.get("product_count", 0),
            # None rather than 0 where the product rows could not be read: a
            # nought there is a claim about the client, and this one would be
            # a claim about our own connection.
            "drive_links": (None if knack_error else have.get(_norm(name), 0)),
        })
    return jsonify({"ok": True, "clients": out, "query": query,
                    "knack_error": knack_error})


@bp.route("/api/ad-assets/lookup")
def api_lookup():
    return jsonify(lookup(request.args.get("client", "")))


@bp.route("/api/ad-assets/access")
def api_access():
    got = drive_files.access()
    got.pop("token", None)
    return jsonify(got)


@bp.route("/api/ad-assets/migrate", methods=["POST"])
def api_migrate():
    body = request.get_json(silent=True) or {}
    return jsonify(migrate(str(body.get("client") or ""),
                           apply=bool(body.get("apply")),
                           live_only=bool(body.get("live_only")),
                           actor=_actor()))


@bp.route("/api/ad-assets/proposals")
def api_proposals():
    return jsonify(proposals(request.args.get("client", "")))


@bp.route("/api/ad-assets/proposals/apply", methods=["POST"])
def api_apply():
    body = request.get_json(silent=True) or {}
    return jsonify(apply_proposals(body.get("keys") or [], actor=_actor()))


def register_ad_assets(app):
    app.register_blueprint(bp)
    return app
