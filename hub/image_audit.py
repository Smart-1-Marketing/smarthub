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


def _files(path: str) -> tuple[bool, str]:
    """(does this module call the filing path, why we say so).

    An AST read, so a docstring that merely mentions `file_asset` — and three
    files here explain the trap by naming it — cannot report as a call site.
    """
    full = ROOT / path
    try:
        tree = ast.parse(full.read_text(errors="ignore"))
    except FileNotFoundError:
        return False, "that file is not in this checkout"
    except SyntaxError as exc:                            # noqa: PERF203
        return False, f"could not be parsed ({exc.msg})"

    for node in ast.walk(tree):
        # The filing path itself files by definition. Without this the module
        # that DEFINES file_asset reports as the worst offender in the list,
        # which is the kind of finding that gets a report switched off.
        if isinstance(node, ast.FunctionDef) and node.name == FILING_CALL:
            return True, f"defines {FILING_CALL}() — this is the filing path"
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name) else "")
        if name == FILING_CALL:
            return True, f"calls {FILING_CALL}() at line {node.lineno}"
    return False, f"no call to {FILING_CALL}() anywhere in this file"


def producers() -> list[dict]:
    """Each image-producing tool, and whether it files what it makes."""
    out = []
    for p in PRODUCERS:
        files, evidence = _files(p["module"])
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
               "label": r.get("name") or "project",
               "url": r.get("preview_url", ""), "when": r.get("updated", ""),
               "where": ", ".join(r.get("tags") or [])}


def _page_images():
    from modules.page_image_optimizer import archive
    for r in archive.recent(limit=2000):
        yield {"id": r.get("public_id", ""), "client": r.get("company", ""),
               "label": r.get("filename") or "image", "url": r.get("url", ""),
               "when": r.get("saved_at", ""), "where": r.get("page_name", "")}


def _gpt_ads():
    from modules.gpt_ads.app import _read_index
    for r in _read_index():
        yield {"id": r.get("id", ""), "client": r.get("client", ""),
               "label": r.get("headline") or r.get("offer") or "ad pack",
               "url": r.get("image_url", ""),
               "when": r.get("updated") or r.get("created", ""), "where": ""}


STORES: list[dict] = [
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


def register_image_audit(app):
    app.register_blueprint(bp)
    return app
