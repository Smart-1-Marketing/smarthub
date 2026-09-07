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


def candidates(client: str = "", *, live_only: bool = False) -> dict:
    """Every product line carrying a Drive creative link.

    One entry per (product record, link): a product with a proof and two
    revisions is three rows here, because each is a folder somebody has to be
    able to open and each migrates or fails on its own.
    """
    got = knack_products.rows()
    want = _norm(client)
    out, skipped_not_drive = [], 0
    for row in got.get("rows") or []:
        if want and _norm(row.get("client")) != want:
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
                "client": row.get("client") or "",
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


# ---------------------------------------------------------------------------
# What is already filed
# ---------------------------------------------------------------------------

def filed_keys(client: str) -> dict:
    """`gdrive:<id>` -> the stored URL, for everything already in the library.

    Read once per run rather than per file: a client with 300 assets would
    otherwise be 300 queries to answer a question one query answers.
    """
    try:
        from modules.image_picker.filing import gallery_for_name
        from modules.image_picker.models import SavedImage, session
        from sqlalchemy import select
    except Exception as exc:                            # noqa: BLE001
        logger.warning("ad_assets: the client library is not importable: %s", exc)
        return {}
    try:
        db = session()
        try:
            gallery = gallery_for_name(db, client)
            if gallery is None:
                return {}
            rows = db.execute(
                select(SavedImage).where(SavedImage.client_id == gallery.id)
            ).scalars().all()
        finally:
            db.close()
    except Exception as exc:                            # noqa: BLE001
        logger.warning("ad_assets: could not read %s's library: %s", client, exc)
        return {}
    return {row.collection_key: (row.cloudinary_url or "")
            for row in rows if (row.collection_key or "").startswith("gdrive:")}


def library_index(client: str) -> dict:
    """Original Drive URL -> the library copy, for one client.

    What Client 360 reads to show the copy instead of the Drive link. Keyed on
    the URL Knack holds rather than on the file id, because that is the string
    the record carries and the string the page has in hand.
    """
    try:
        from modules.image_picker.filing import gallery_for_name
        from modules.image_picker.models import SavedImage, session
        from sqlalchemy import select
    except Exception:                                   # noqa: BLE001
        return {}
    try:
        db = session()
        try:
            gallery = gallery_for_name(db, client)
            if gallery is None:
                return {}
            rows = db.execute(
                select(SavedImage).where(SavedImage.client_id == gallery.id,
                                         SavedImage.tool == TOOL)
            ).scalars().all()
            gallery_id = gallery.id
        finally:
            db.close()
    except Exception:                                   # noqa: BLE001
        return {}

    index: dict[str, dict] = {}
    for row in rows:
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
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "Which client?"}

    found = candidates(client, live_only=live_only)
    links = found["links"][:max(1, int(limit or 500))]
    if not links:
        return {"ok": True, "client": client, "apply": apply, "links": 0,
                "copied": [], "skipped": [], "failed": [],
                "note": f"No Google Drive creative links on {client}'s product "
                        f"records in Smart 1 Team."}

    auth = drive_files.access()
    if not auth["ok"]:
        return {"ok": False, "client": client, "reason": auth["reason"],
                "error": auth["detail"], "links": len(links),
                "note": "Nothing was read from Drive, so this is not a report "
                        "that there is nothing there."}
    token = auth["token"]

    already = filed_keys(client) if apply else {}
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
    return jsonify(candidates(request.args.get("client", ""),
                              live_only=request.args.get("live") == "1"))


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
