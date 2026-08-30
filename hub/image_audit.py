"""Every image this Hub makes, and whether anybody can say whose it is.

Ten tools in this Hub create, upload or choose an image. Each one writes it to
Cloudinary and files a record of its own — and the question somebody actually
asks, *"what have we made for this client?"*, is answerable only from the ones
that recorded a client. An image with nobody's name on it is not lost: it is
worse than lost, because it sits in the account looking like an asset while
being unfindable from the only screen anybody opens.

So this audits both halves, and the second half is the reason a row count on
its own would mislead.

**The stores** — the records that exist — are counted and split into filed,
unfiled, and *not measured* where a store could not be read. "This tool has
produced nothing for anybody" and "we could not open its index" are different
answers and only the first is good news.

**The producers** — the code paths that make an image — are checked for
whether they file into a client gallery at all. A tool that has never filed
anything has no unfiled rows to count: it is invisible to a data audit and
reads as the cleanest tool in the building. That check is an **AST** read for
a real call, not a text match, for the reason `hub/config.py`'s drift check
gives at length: three modules in this repo explain a bug they no longer have
by quoting the code that caused it, and a checker that matches text reports
the explanation as the defect.

Three rules the findings themselves follow:

* **A client and a lead are the same answer here.** `image_picker` galleries
  carry `kind` — `client` once attached to a Hub record, `prospect` until then
  — and a prospect's photographs are exactly as filed as a client's. What is
  unacceptable is *neither*.
* **Absent is not zero.** A store that raises is reported with the exception,
  never as an empty shelf.
* **Nothing here reaches Cloudinary.** The account is the ground truth for
  what exists, and walking it costs a paged API call per thousand assets on a
  page somebody opens to triage. This audits what the Hub *recorded*; an asset
  in Cloudinary that no store knows about is a different report, and it is
  named as out of scope rather than silently excluded.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent

# The one filing path. Every producer below is measured against whether it
# reaches this, because a client gallery is the single place the question
# "what have we made for them?" is answered from.
FILING_CALL = "file_asset"

# The same filing, reached over HTTP. The IO Builder uploads creative straight
# from the browser to Cloudinary and files it from there, so its Python never
# mentions file_asset and an AST-only check calls the one tool that does file
# its worst offender. A route literal is what tools/linkcheck.py matches in a
# template, and it is what this matches too.
FILING_ROUTE = "/tools/image-picker/api/staff/file"

# What each producer writes into the row's `provider` column. Declared here so
# test_image_audit.py can require that every one of them has a heading in
# `filing.SOURCE_LABELS` -- a value with no label reaches a client's gallery
# as a bare key under no heading, which is the state `io_creative` was in.


# ---------------------------------------------------------------- producers
# label            what a person calls the tool
# module           the file that does the creating/uploading/choosing
# makes            create | upload | choose  -- the three verbs
# why              what the image is, in a sentence, for somebody triaging
PRODUCERS = [
    {"key": "seo_images", "label": "SEO Image Pipeline",
     "provider": ["seo_image"],
     "module": "modules/seo_images/app.py", "makes": "upload",
     "why": "Client photographs renamed and optimized for their website."},
    {"key": "image_picker", "label": "Client Image Uploads",
     "provider": ["local", "camera", "url"],
     "module": "modules/image_picker/filing.py", "makes": "upload",
     "why": "What a client sends us through their own upload link."},
    {"key": "blog_images", "label": "Blog featured images",
     "provider": ["blog"],
     "module": "hub/blog_images.py", "makes": "create",
     "why": "Generated artwork for a blog post on the client's site."},
    {"key": "client_logos", "label": "Client logos",
     "provider": ["logo_brand", "logo_scan"],
     "module": "hub/client_logos.py", "makes": "upload",
     "why": "A logo found on the brand record or the last site scan."},
    {"key": "display_ads", "label": "Display Ad Builder",
     "provider": ["display_ads", "display_ad"],
     "module": "hub/ad_builder_link.py", "makes": "create",
     "why": "Finished banner sets a client receives."},
    {"key": "gpt_ads", "label": "GPT Ads Builder",
     "provider": ["gpt_ads"],
     "module": "modules/gpt_ads/app.py", "makes": "create",
     "why": "The 1:1 square that ships in the ad pack."},
    {"key": "bg_remover", "label": "Background Remover",
     "provider": ["bg_remover"],
     "module": "modules/bg_remover/app.py", "makes": "create",
     "why": "Cut-outs lifted off a client photograph."},
    {"key": "image_creator", "label": "Image Creator",
     "provider": ["image_creator"],
     "module": "modules/image_creator/projects.py", "makes": "create",
     "why": "Graphics composed on the canvas."},
    {"key": "page_image_optimizer", "label": "Page Image Optimizer",
     "provider": ["page_image_optimizer"],
     "module": "modules/page_image_optimizer/archive.py", "makes": "upload",
     "why": "Images pulled off a live page, resized and put back."},
    {"key": "stock_photos", "label": "Stock Photo Search",
     "provider": ["pexels", "pixabay", "unsplash", "library"],
     "module": "modules/stock_photos/app.py", "makes": "choose",
     "why": "A stock or library photo taken for a client's creative."},
    {"key": "io_builder", "label": "IO creative uploads",
     "provider": ["io_creative"],
     "module": "modules/io_builder/app.py", "makes": "upload",
     "also": ["modules/io_builder/templates/index.html"],
     "why": "Creative attached to an insertion order."},
    {"key": "prospect_assets", "label": "Prospect 360 files",
     "provider": ["prospect"],
     "module": "hub/prospect.py", "makes": "upload",
     "filing_call": "add_asset",
     "why": "Mock-ups, screenshots and signed pages collected against a "
            "business before they are a client."},
    {"key": "commercial_builder", "label": "Commercial Builder",
     "provider": ["commercial_builder"],
     "module": "modules/commercial_builder/services/cloudinary_service.py",
     "makes": "create",
     "why": "Stills and spokesperson frames in a commercial."},
]


def _files_via_route(path: str) -> tuple[bool, str]:
    """Does this template post to the filing route."""
    full = ROOT / path
    try:
        text = full.read_text(errors="ignore")
    except FileNotFoundError:
        return False, "that file is not in this checkout"
    if FILING_ROUTE in text:
        line = text[:text.index(FILING_ROUTE)].count("\n") + 1
        return True, f"posts to {FILING_ROUTE} at {path}:{line}"
    return False, ""


def _files(path: str, call: str = FILING_CALL) -> tuple[bool, str]:
    """(does this module call the filing path, why we say so).

    An AST read, so a docstring that merely mentions `file_asset` — and three
    files here explain the trap by naming it — cannot report as a call site.

    ``call`` because there are two right answers, not one. A client's work
    goes to a gallery; a **prospect's** goes to that prospect's own record
    through `hub/prospect.add_asset`, keyed on the lead. A rule that demanded
    `file_asset` of everything would report the lead half as unfiled — which
    is precisely the "attached to either a lead or a client" this audit is
    about.
    """
    full = ROOT / path
    try:
        tree = ast.parse(full.read_text(errors="ignore"))
    except FileNotFoundError:
        return False, "that file is not in this checkout"
    except SyntaxError as exc:                            # noqa: PERF203
        return False, f"could not be parsed ({exc.msg})"

    FILING_CALL_LOCAL = call
    for node in ast.walk(tree):
        # The filing path itself files by definition. Without this the module
        # that DEFINES file_asset reports as the worst offender in the list,
        # which is the kind of finding that gets a report switched off.
        if isinstance(node, ast.FunctionDef) and node.name == FILING_CALL_LOCAL:
            return True, f"defines {FILING_CALL_LOCAL}() — this is the filing path"
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name) else "")
        if name == FILING_CALL_LOCAL:
            return True, f"calls {FILING_CALL_LOCAL}() at line {node.lineno}"
    return False, f"no call to {FILING_CALL_LOCAL}() anywhere in this file"


def producers() -> list[dict]:
    """Each image-producing tool, and whether it files what it makes."""
    out = []
    for p in PRODUCERS:
        files, evidence = _files(p["module"], p.get("filing_call", FILING_CALL))
        for extra in p.get("also") or []:
            if files:
                break
            files, evidence = _files_via_route(extra)
        if not files and not evidence:
            evidence = f"no call to {FILING_CALL}() and no post to {FILING_ROUTE}"
        row = {**p, "files": files, "evidence": evidence}
        if not files:
            row["cost"] = (
                f"Every image {p['label']} produces is written to Cloudinary "
                "and recorded nowhere a client's record can read, so it does "
                "not appear on their gallery or their 360 page.")
        out.append(row)
    return out


# ------------------------------------------------------------------- stores
def _seo_images():
    from modules.seo_images.app import load_archive
    for r in load_archive():
        yield {"id": r.get("id", ""), "client": r.get("company", ""),
               "public_id": r.get("public_id", ""),
               "label": r.get("filename") or r.get("seo_filename") or "image",
               "url": r.get("url", ""), "when": r.get("saved_at", ""),
               "where": r.get("project", "")}


def _image_picker():
    from modules.image_picker.models import PickerClient, SavedImage, session
    from sqlalchemy import select
    db = session()
    try:
        names = {c.id: (c.name, c.kind) for c in
                 db.execute(select(PickerClient)).scalars().all()}
        for img in db.execute(select(SavedImage)).scalars().all():
            name, kind = names.get(img.client_id, ("", ""))
            yield {"id": str(img.id), "client": name,
                   "public_id": img.cloudinary_public_id or "",
                   "kind_of_client": kind or "prospect",
                   "label": img.filename or img.alt_text or "image",
                   "url": img.cloudinary_url or "",
                   "when": str(img.created_at or "")[:19],
                   "where": img.collection_label or ""}
    finally:
        db.close()


def _image_creator():
    from modules.image_creator.projects import load_index
    for r in load_index():
        yield {"id": r.get("id", ""), "client": r.get("client", ""),
               "public_id": (f"{_creator_folder()}/previews/{r.get('id')}"
                             if r.get("id") else ""),
               "label": r.get("name") or "project",
               "url": r.get("preview_url", ""), "when": r.get("updated", ""),
               "where": ", ".join(r.get("tags") or [])}


def _creator_folder() -> str:
    """Image Creator's Cloudinary folder, so a preview's id can be derived.

    The project index does not store the public_id -- it stores the URL -- and
    the id is deterministic from the folder and the project id, which is how
    the gallery filing already builds it.
    """
    try:
        from modules.image_creator.projects import FOLDER
        return FOLDER
    except Exception:                                     # noqa: BLE001
        return "smart1-image-projects"


def _page_images():
    from modules.page_image_optimizer import archive
    for r in archive.recent(limit=2000):
        yield {"id": r.get("public_id", ""), "client": r.get("company", ""),
               "public_id": r.get("public_id", ""),
               "label": r.get("filename") or "image", "url": r.get("url", ""),
               "when": r.get("saved_at", ""), "where": r.get("page_name", "")}


def _gpt_ads():
    from modules.gpt_ads.app import _read_index
    for r in _read_index():
        yield {"id": r.get("id", ""), "client": r.get("client", ""),
               "public_id": r.get("image_public_id", ""),
               "label": r.get("headline") or r.get("offer") or "ad pack",
               "url": r.get("image_url", ""),
               "when": r.get("updated") or r.get("created", ""), "where": ""}


def _prospect_assets():
    """Files kept against a prospect — the lead half of "a client or a lead".

    Attached by construction: the store is keyed on the lead id, so a row that
    exists names somebody. It is counted here anyway, because a report that
    only shows what is broken cannot say how much is right.
    """
    from hub import leads as _leads, prospect
    names = {}
    try:
        # A generous window: a file kept against a prospect long outlives the
        # thirty days the panel opens on, and a lead we cannot name still
        # counts as attached — it is the id that does the attaching.
        for row in (_leads.listing(days=3650) or {}).get("leads", []):
            names[str(row.get("id"))] = (row.get("business")
                                         or row.get("name") or "")
    except Exception:                                     # noqa: BLE001
        names = {}
    for lead_id, rows in (prospect._all_assets() or {}).items():
        for r in rows or []:
            yield {"id": r.get("id", ""),
                   "client": names.get(str(lead_id)) or f"prospect {lead_id}",
                   "public_id": r.get("public_id", ""),
                   "kind_of_client": "prospect",
                   "label": r.get("label") or r.get("filename") or "file",
                   "url": r.get("url", ""), "when": r.get("added", ""),
                   "where": "Prospect 360"}


STORES: list[dict] = [
    {"key": "prospect_assets", "label": "Prospect 360 files",
     "reader": _prospect_assets,
     "fix": "/sales/leads"},
    {"key": "seo_images", "label": "SEO Image Pipeline archive",
     "reader": _seo_images,
     "fix": "/tools/seo-images/"},
    {"key": "image_picker", "label": "Client galleries",
     "reader": _image_picker,
     "fix": "/tools/image-picker/"},
    {"key": "image_creator", "label": "Image Creator projects",
     "reader": _image_creator,
     "fix": "/tools/image-creator/"},
    {"key": "page_image_optimizer", "label": "Page Image Optimizer archive",
     "reader": _page_images,
     "fix": "/tools/page-images/"},
    {"key": "gpt_ads", "label": "GPT ad packs",
     "reader": _gpt_ads,
     "fix": "/tools/gpt-ads/"},
]


def _read(reader: Callable) -> tuple[list[dict], str]:
    try:
        return list(reader()), ""
    except Exception as exc:                              # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"


def stores(limit_unfiled: int = 200) -> list[dict]:
    """Every image record the Hub holds, split by whether it names anybody."""
    out = []
    for s in STORES:
        rows, err = _read(s["reader"])
        if err:
            out.append({**{k: v for k, v in s.items() if k != "reader"},
                        "measured": False, "error": err,
                        "total": None, "filed": None, "unfiled": None,
                        "rows": []})
            continue
        unfiled = [r for r in rows if not str(r.get("client") or "").strip()]
        out.append({**{k: v for k, v in s.items() if k != "reader"},
                    "measured": True, "error": "",
                    "total": len(rows),
                    "filed": len(rows) - len(unfiled),
                    "unfiled": len(unfiled),
                    # A prospect gallery is filed. What is not acceptable is
                    # neither a client nor a lead.
                    "prospects": sum(1 for r in rows
                                     if r.get("kind_of_client") == "prospect"),
                    "rows": unfiled[:limit_unfiled]})
    return out


def audit(limit_unfiled: int = 200) -> dict:
    """Both halves, plus the totals a heading can state without lying."""
    prod = producers()
    st = stores(limit_unfiled)
    measured = [s for s in st if s["measured"]]
    unmeasured = [s for s in st if not s["measured"]]
    return {
        "producers": prod,
        "not_filing": [p for p in prod if not p["files"]],
        "stores": st,
        # Only over the stores that answered. A total that quietly counts an
        # unreadable store as nought is the confident wrong answer this whole
        # file exists to find.
        "total": sum(s["total"] for s in measured),
        "unfiled": sum(s["unfiled"] for s in measured),
        "measured": not unmeasured,
        "unmeasured": [{"label": s["label"], "error": s["error"]}
                       for s in unmeasured],
        "note": ("Counts cover what the Hub recorded. An asset sitting in "
                 "Cloudinary that no tool wrote a record for is outside this "
                 "report — walking the account costs a paged call per thousand "
                 "assets, which is not something a triage page should do."),
    }


# -------------------------------------------------------------- reconcile
# The other direction. Everything above audits what the Hub RECORDED; the
# account is the ground truth for what EXISTS, and an asset sitting in
# Cloudinary that no store has a row for is invisible to all of it -- which is
# most of what the six tools that filed nothing produced before they were
# fixed.
#
# `hub/storage.manifest()` was written for exactly this and had no caller: its
# docstring says it "feeds the orphaned-asset audit", and that audit has never
# existed. The third declared-but-unwired integration point in this corner of
# the codebase, after page_image_optimizer's RECORD_HOOK and io_creative's
# missing writer.

# Which Cloudinary folder trees belong to an image-producing tool. Only these:
# the account also holds backups and a raw order counter, and reporting those
# as unattached images would be a page of findings nobody can act on.
RECONCILE_KINDS = [
    ("seo_images", "SEO Image Pipeline"),
    ("image_projects", "Image Creator"),
    ("cutouts", "Background Remover"),
    ("client_logos", "Client logos"),
    ("stock_photos", "Stock photos"),
    ("social_requests", "Social content requests"),
    ("prospects", "Prospect 360 files"),
    ("commercials", "Commercial Builder"),
    ("ads_logos", "Smart 1 Ads logos"),
]

# Deliberately NOT reconciled, each for its own reason. Named rather than
# omitted: a folder silently left out of a completeness report is the same
# failure the report is about.
NOT_RECONCILED = {
    "proposals": "Proposal PDFs, not images.",
    "backups": "The jsonstore mirror. Nothing here is client creative.",
}


def known_public_ids() -> tuple[set, list]:
    """(every public_id any store has a row for, stores that would not answer).

    A store that fails is carried as an error rather than as an empty set,
    because an unreadable store makes every asset it knows about look
    orphaned -- which would turn one outage into a page of false findings.
    """
    known, failed = set(), []
    for store in STORES:
        rows, err = _read(store["reader"])
        if err:
            failed.append({"label": store["label"], "error": err})
            continue
        for r in rows:
            pid = str(r.get("public_id") or "").strip()
            if pid:
                known.add(pid)
    return known, failed


def _client_from_public_id(public_id: str, kind: str) -> dict:
    """A proposed owner for an orphan, and how it was arrived at.

    Never applied on its own. Every one of these is a guess from a path, and
    filing one client's creative into another's gallery is the mistake this
    whole area of the Hub is arranged to prevent -- so it is a proposal a
    person confirms, and it says which of the three ways it was derived.
    """
    parts = [p for p in str(public_id or "").split("/") if p]
    # <folder>/<client-slug>/... is the shape bg_remover, stock_photos and
    # the commercial builder all use.
    if len(parts) >= 3:
        slug = parts[1]
        if slug and slug not in ("previews", "unfiled", "documents"):
            try:
                from hub import clients_registry
                for row in clients_registry.all_clients():
                    from hub.clients_registry import slugify
                    if slugify(row.get("name", "")) == slug:
                        return {"client": row["name"], "how": "the folder it is in",
                                "confident": True}
            except Exception:                             # noqa: BLE001
                pass
            return {"client": slug.replace("-", " ").title(),
                    "how": "the folder it is in, but no client of that name",
                    "confident": False}
    return {"client": "", "how": "", "confident": False}


def reconcile(kinds: list | None = None, max_per_kind: int = 500) -> dict:
    """What is in Cloudinary that no store has a record for.

    Billed and slow -- a paged Admin API call per folder tree -- so this is
    behind a button and never a page load, the rule `/tools/domains` settled
    for its own registry pull. Nothing is written and nothing is deleted: an
    orphan here is a file somebody made for somebody, and the answer is to
    attach it, not to bin it.
    """
    try:
        from hub import storage
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False, "error": f"storage is unavailable: {exc}",
                "folders": []}
    if not storage.ready():
        # Not measured, not "nothing found". Reporting an unconfigured account
        # as a clean bill is the confident wrong answer this file is about.
        return {"measured": False, "configured": False,
                "error": "Cloudinary is not configured, so the account could "
                         "not be listed.",
                "folders": []}

    known, failed_stores = known_public_ids()
    wanted = [(k, label) for k, label in RECONCILE_KINDS
              if not kinds or k in kinds]

    folders, total, orphans = [], 0, 0
    for key, label in wanted:
        try:
            rows = storage.manifest(key, max_results=max_per_kind)
        except Exception as exc:                          # noqa: BLE001
            folders.append({"key": key, "label": label, "measured": False,
                            "error": f"{type(exc).__name__}: {exc}",
                            "count": None, "orphans": None, "rows": []})
            continue
        loose = []
        for r in rows:
            pid = str(r.get("public_id") or "")
            if not pid or pid in known:
                continue
            proposed = _client_from_public_id(pid, key)
            loose.append({"public_id": pid, "bytes": r.get("bytes"),
                          "created_at": r.get("created_at", ""),
                          "resource_type": r.get("resource_type", "image"),
                          "url": r.get("secure_url", ""),
                          "kind": key,
                          "proposed": proposed["client"],
                          "proposed_how": proposed["how"],
                          "confident": proposed["confident"]})
        total += len(rows)
        orphans += len(loose)
        folders.append({"key": key, "label": label, "measured": True,
                        "error": "", "count": len(rows),
                        "orphans": len(loose),
                        # A folder listing that hit the cap has not been fully
                        # read, and saying so beats an undercount that looks
                        # like good news.
                        "truncated": len(rows) >= max_per_kind,
                        "rows": loose[:200]})

    unread = [f for f in folders if not f["measured"]]
    return {
        "measured": not unread and not failed_stores,
        "configured": True,
        "folders": folders,
        "total": total,
        "orphans": orphans,
        "stores_unread": failed_stores,
        "not_reconciled": [{"key": k, "why": v} for k, v in NOT_RECONCILED.items()],
        "note": ("Counted against every public_id the Hub has a record for. A "
                 "store that could not be read is named above rather than "
                 "letting the assets it knows about read as orphans."),
    }


# ------------------------------------------------------------------- attach
# A row that reports a problem beside a control that refuses to fix it is not
# a control, and sending somebody to another screen to find the same row again
# is how a list stays unactioned -- the note the Sites Admin domain cell
# carries. So each store says how one of its rows is attached to a client.


def _attach_seo(row_id: str, client: str) -> dict:
    from modules.seo_images.app import load_archive, save_archive
    rows = load_archive()
    hit = next((r for r in rows if r.get("id") == row_id), None)
    if hit is None:
        return {"ok": False, "error": "That image is no longer in the archive."}
    hit["company"] = client
    save_archive(rows)
    return {"ok": True, "url": hit.get("url", ""),
            "public_id": hit.get("public_id", ""),
            "filename": hit.get("filename", "")}


def _attach_creator(row_id: str, client: str) -> dict:
    from modules.image_creator import projects
    rows = projects.load_index()
    hit = next((r for r in rows if r.get("id") == row_id), None)
    if hit is None:
        return {"ok": False, "error": "That project is no longer in the index."}
    hit["client"] = client
    hit["client_slug"] = projects.slugify(client)
    projects._save_index(rows)
    return {"ok": True, "url": hit.get("preview_url", ""),
            "public_id": f"{projects.FOLDER}/previews/{row_id}",
            "filename": f"{hit.get('slug') or 'graphic'}.png"}


def _attach_page_image(row_id: str, client: str) -> dict:
    from hub import jsonstore
    from modules.page_image_optimizer import archive
    rows = jsonstore.read_json(archive.FALLBACK_ARCHIVE, default=[])
    if not isinstance(rows, list):
        return {"ok": False, "error": "That archive could not be read."}
    hit = next((r for r in rows if r.get("public_id") == row_id), None)
    if hit is None:
        return {"ok": False, "error": "That image is no longer in the archive."}
    hit["company"] = client
    jsonstore.write_json(archive.FALLBACK_ARCHIVE, rows)
    return {"ok": True, "url": hit.get("url", ""), "public_id": row_id,
            "filename": hit.get("filename", "")}


def _attach_gpt_ads(row_id: str, client: str) -> dict:
    from modules.gpt_ads.app import load_pack, save_pack
    pack = load_pack(row_id)
    if not pack:
        return {"ok": False, "error": "That pack no longer exists."}
    pack["client"] = client
    save_pack(pack)
    return {"ok": True, "url": pack.get("image_url", ""),
            "public_id": pack.get("image_public_id", ""),
            "filename": f"{row_id}.png"}


ATTACHERS = {
    "seo_images": _attach_seo,
    "image_creator": _attach_creator,
    "page_image_optimizer": _attach_page_image,
    "gpt_ads": _attach_gpt_ads,
}


def attach(store: str, row_id: str, client: str, *, actor: str = "") -> dict:
    """Give one unfiled image a client, and file it into their gallery.

    Two writes, reported separately, for the reason `hub/domain_links.py`
    gives at length: "attached" and "attached in one of two places" are
    different outcomes, and one tick for both is how somebody learns not to
    trust the tick. The store is the tool's own record; the gallery is what a
    client's page reads, and an image in one and not the other is exactly the
    split this audit exists to find.

    The client is taken as given rather than matched: the caller is a picker
    of real clients, and guessing a name here is the mistake the whole report
    is about.
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "Pick a client — an image filed to a "
                                      "guess is worse than one filed to nobody."}
    fn = ATTACHERS.get(store)
    if fn is None:
        return {"ok": False, "error": f"There is no way to attach a {store} row."}
    try:
        out = fn(str(row_id or ""), client)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not out.get("ok"):
        return out

    filed = {"ok": False, "error": "nothing to file — that row has no stored URL"}
    if out.get("url", "").startswith("https://") and out.get("public_id"):
        try:
            from modules.image_picker.filing import file_asset
            filed = file_asset(client_name=client, public_id=out["public_id"],
                               url=out["url"], kind=store if store in
                               ("seo_image",) else _KIND_FOR.get(store, "upload"),
                               filename=out.get("filename", ""),
                               provider=store, saved_by=actor or "system")
        except Exception as exc:                          # noqa: BLE001
            filed = {"ok": False, "error": str(exc)}

    try:
        from hub import audit as _audit
        _audit.log("image_picker", "image_attached", client=client,
                   actor=actor or "", store=store, ref=str(row_id))
    except Exception:                                     # noqa: BLE001
        pass

    return {"ok": True, "client": client,
            "store_updated": True,
            "gallery_filed": bool(filed.get("ok")),
            "gallery_error": "" if filed.get("ok") else str(filed.get("error", "")),
            "gallery_url": filed.get("gallery_url", "")}


_KIND_FOR = {
    "seo_images": "seo_image",
    "image_creator": "graphic",
    "page_image_optimizer": "page_image",
    "gpt_ads": "display_ad",
}


# --------------------------------------------------------------- bulk work
def attach_many(items, *, actor: str = "") -> dict:
    """Attach several unfiled rows at once, and say what each one did.

    The report offers this because a client with forty unattached images is
    forty presses, and forty confirm dialogs is how somebody stops reading
    them. Every result carries its own outcome — a bulk action that reports
    one number hides the two that failed, the rule
    `hub/client_urls.accept_many()` already works to.
    """
    done, failed = [], []
    for item in items or []:
        store = str((item or {}).get("store") or "")
        row_id = str((item or {}).get("id") or "")
        client = str((item or {}).get("client") or "").strip()
        out = attach(store, row_id, client, actor=actor)
        if out.get("ok"):
            done.append({"store": store, "id": row_id, "client": client,
                         "gallery_filed": bool(out.get("gallery_filed")),
                         "gallery_error": out.get("gallery_error", "")})
        else:
            failed.append({"store": store, "id": row_id, "client": client,
                           "error": out.get("error", "could not be attached")})
    return {
        "ok": True,
        "attached": len(done),
        "failed": len(failed),
        # Counted apart, because "attached" and "attached in one of two
        # places" are different outcomes and a single tick over both is how
        # somebody learns not to trust the tick.
        "gallery_filed": sum(1 for d in done if d["gallery_filed"]),
        "results": done, "failures": failed,
    }


def file_orphan(public_id: str, url: str, client: str, kind: str = "",
                *, actor: str = "") -> dict:
    """Give a Cloudinary asset nobody has a record for a client and a row.

    This is the reconciliation's own action, and it is a different job from
    `attach()`: there is no store row to update, because the absence of one is
    the finding. It creates the gallery row, which is what makes the asset
    findable from the client's page.
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "Pick a client — an image filed to a "
                                      "guess is worse than one filed to nobody."}
    public_id = str(public_id or "").strip()
    if not public_id:
        return {"ok": False, "error": "That asset has no id to file."}
    if not str(url or "").startswith("https://"):
        return {"ok": False, "error": "That asset has no stored URL to file."}
    try:
        from modules.image_picker.filing import file_asset
        out = file_asset(client_name=client, public_id=public_id, url=url,
                         kind=_KIND_FOR.get(kind, "upload"),
                         filename=public_id.split("/")[-1],
                         provider=kind or "cloudinary",
                         saved_by=actor or "system")
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error", "It could not be filed.")}
    try:
        from hub import audit as _audit
        _audit.log("image_picker", "orphan_filed", client=client,
                   actor=actor or "", ref=public_id, kind=kind or "")
    except Exception:                                     # noqa: BLE001
        pass
    return {"ok": True, "client": client, "gallery_filed": True,
            "gallery_url": out.get("gallery_url", "")}


# ------------------------------------------------------------------- routes
from flask import Blueprint, jsonify, redirect, render_template, request  # noqa: E402

bp = Blueprint("image_audit", __name__)


@bp.before_request
def _require_login():
    """One guard on the blueprint, not one per view.

    This report names every client we hold an image for and carries a write
    route. `hub/auth.py` names the failure in its own docstring, and
    Commercial Builder shipped forty views answering 200 to anyone with the
    URL because the guard was written per view.
    """
    from hub import access, current_user
    if current_user():
        return None
    if access.wants_json(request.path or "/", request.headers.get("Accept", "")):
        return jsonify({"error": "Sign in to read this report."}), 401
    return redirect("/login?next=" + (request.path or "/"))


def _actor() -> str:
    try:
        from hub import current_user
        return current_user() or ""
    except Exception:                                     # noqa: BLE001
        return ""


@bp.route("/qa/unattached-images")
def page_image_audit():
    return render_template("image_audit.html")


@bp.route("/api/image-audit")
def api_image_audit():
    return jsonify(audit())


@bp.route("/api/image-audit/attach", methods=["POST"])
def api_image_attach():
    """Attach one unfiled image to a client. A POST: it writes two stores."""
    body = request.get_json(silent=True) or {}
    out = attach(str(body.get("store") or ""), str(body.get("id") or ""),
                 str(body.get("client") or ""), actor=_actor())
    return jsonify(out), (200 if out.get("ok") else 400)


@bp.route("/api/image-audit/attach-many", methods=["POST"])
def api_image_attach_many():
    """Attach a selection at once. Every row reports its own outcome."""
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "Nothing was selected."}), 400
    return jsonify(attach_many(items[:200], actor=_actor()))


@bp.route("/api/image-audit/reconcile", methods=["POST"])
def api_image_reconcile():
    """List the account and report what no store has a record for.

    A POST because it is a paged Admin API call per folder tree — billed and
    slow — and a GET that costs money is one a reload or a prefetch fires
    without anybody asking.
    """
    body = request.get_json(silent=True) or {}
    kinds = body.get("kinds")
    return jsonify(reconcile(kinds if isinstance(kinds, list) else None))


@bp.route("/api/image-audit/file-orphan", methods=["POST"])
def api_file_orphan():
    """File one Cloudinary asset that no store knows about."""
    body = request.get_json(silent=True) or {}
    out = file_orphan(str(body.get("public_id") or ""),
                      str(body.get("url") or ""),
                      str(body.get("client") or ""),
                      str(body.get("kind") or ""), actor=_actor())
    return jsonify(out), (200 if out.get("ok") else 400)


def register_image_audit(app):
    app.register_blueprint(bp)
    return app
