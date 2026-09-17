"""One staff asset catalog; original files and tool records remain authoritative.

Sections are permanent views, not Cloudinary folders to rename. Reading this
catalog never uploads, publishes, enables sharing, or writes gallery rows.
Legacy readers are client-scoped before their files are returned. A failed
reader is named rather than reported as an empty, complete library.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit

from sqlalchemy import select

from .models import SavedImage
from .provisioning import _norm

log = logging.getLogger(__name__)
# The five folders every client asset home offers, empty or not. An empty
# section is drawn as a section with nothing in it, never dropped: the whole
# point of a fixed set is that the person adding a logo finds the Logos folder
# waiting rather than inventing "logo", "Logo" and "logos" across three
# clients. Client 360 draws the same five from the same list.
SECTIONS = [
    {"key": "uploads", "label": "Client Uploads", "description": "Files supplied by the client"},
    {"key": "creative", "label": "Creative", "description": "Brand assets, campaign creative, and saved media"},
    {"key": "projects", "label": "Hub Projects", "description": "Work created in the Hub, organized by project"},
    {"key": "logos", "label": "Logos", "description": "Every version of their mark, from the brand record, their website, or an upload"},
    {"key": "internal", "label": "Internal", "description": "Files our team added from Drive, Dropbox, or a desk"},
]
SECTION_KEYS = tuple(s["key"] for s in SECTIONS)
SECTION_LABELS = {s["key"]: s["label"] for s in SECTIONS}


def section_for_folder(folder: str) -> str:
    """The section a folder NAME belongs to, or "" when it is an ordinary folder.

    "Logos" typed into the upload panel is the Logos section, whatever the
    capitalisation -- that is the request this exists for: a second upload of
    logo versions has to land beside the first rather than in a new folder
    spelled slightly differently.
    """
    key = _norm(folder)
    for section in SECTIONS:
        if key and key in (_norm(section["label"]), section["key"]):
            return section["key"]
    return ""


def safe_url(value):
    value = str(value or "").strip()
    if "\\" in value or any(ord(c) < 32 for c in value):
        return ""
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == "https" and parsed.netloc else ""
    except ValueError:
        return ""


def organize(row):
    """Purpose precedes transport: a Drive campaign import is creative."""
    row = dict(row)
    kind = row.get("collection_kind") or ""
    provider = row.get("provider") or ""
    named = section_for_folder(row.get("project_name") or row.get("collection_label") or "")
    if kind == "logo" or provider in {"logo_brand", "logo_scan", "logo_upload", "client_logos"} or named == "logos":
        section = "logos"
    elif kind == "internal" or named == "internal":
        section = "internal"
    elif kind in {"ad_asset", "io_creative", "creative_information", "stock", "video_search"} or provider in {"pexels", "pixabay", "unsplash", "shutterstock", "getty", "istock", "library", "coverr", "video_library"}:
        section = "creative"
    elif kind in {"upload", "client_upload"} or provider in {"local", "camera", "social_request"}:
        section = "uploads"
    elif kind or row.get("tool") or row.get("project_name"):
        section = "projects"
    else:
        section = "creative"
    row["section"] = row.get("section") or section
    parts = []
    if row.get("io_number"):
        parts.append("IO " + str(row["io_number"]))
    if row.get("product_number"):
        parts.append("Product " + str(row["product_number"]))
    if row.get("project_name"):
        parts.append(str(row["project_name"]))
    if row["section"] == "logos":
        # One folder for every logo however it arrived: hub/client_logos.py
        # files under the label "Logo", an upload panel types "Logos", and
        # two chips for one mark is the drift the fixed section list exists
        # to stop.
        row["folder"] = SECTION_LABELS["logos"]
    else:
        row["folder"] = " / ".join(parts) or row.get("collection_label") or "General"
    row["url"] = safe_url(row.get("url"))
    row["thumb"] = safe_url(row.get("thumb"))
    row["source_url"] = safe_url(row.get("source_url"))
    if row.get("resource_type") == "image" and row["url"]:
        from hub.storage import preview_url
        row["thumb"] = preview_url(row["url"], "image")
    row["editable"] = isinstance(row.get("id"), int)
    return row


def reference(source, identity, title, url, *, when="", project="", resource_type="raw", **extra):
    return organize({"id": f"{source}:{identity}", "filename": title,
                     "url": url, "resource_type": resource_type, "provider": source,
                     "collection_kind": source, "collection_label": project or title,
                     "project_name": project, "created_at": str(when or ""),
                     "section": "projects", "tool": source, "external": True, **extra})


def legacy_images(name):
    from hub import image_audit
    for source in image_audit.STORES:
        if source["key"] not in {"seo_images", "image_creator", "page_image_optimizer", "blog_images"}:
            continue
        try:
            rows = list(source["reader"]())
            matches = []
            for row in rows:
                if _norm(row.get("client")) != _norm(name):
                    continue
                if source["key"] == "blog_images" and row.get("where") != "approved":
                    continue
                matches.append(reference(source["key"], row["id"], row["label"], row["url"],
                                         when=row.get("when"), project=row.get("where") or source["label"],
                                         resource_type="image", public_id=row.get("public_id"),
                                         thumb=row.get("url")))
            yield source["label"], matches, ""
        except Exception:
            log.exception("Master gallery could not read %s", source["key"])
            yield source["label"], [], "Could not load this source. Retry to include its files."


def project_sources(name):
    """Small project indexes; links reopen the saved project, never a new one."""
    from importlib import import_module
    def reader(module, function):
        return lambda: getattr(import_module(module), function)()
    return [
        ("Image Creator", reader("modules.image_creator.projects", "load_index"), "client", "name", "/tools/image-creator/?project="),
        ("GPT Ads", reader("modules.gpt_ads.app", "_read_index"), "client", "campaign", "/tools/gpt-ads/?ad="),
        ("Social Planner", reader("modules.social_planner.app", "_read_index"), "client", "month", "/tools/social/?batch="),
        ("Fan Radio", reader("modules.fan_radio.store", "index"), "client", "company", "/tools/fan-radio/#"),
        ("Radio Promo", reader("modules.radio_promo.store", "all_projects"), "client", "project_name", "/tools/radio-promo/#p="),
    ]


def commercial_projects(name):
    from modules.commercial_builder.models import Client, CommercialProject, RenderJob, RenderApproval
    from hub.extensions import db
    # Query through an independent session: a missing optional table must not
    # leave the shared request transaction aborted on PostgreSQL.
    from sqlalchemy.orm import Session
    with Session(db.engine) as session:
        clients = session.scalars(select(Client)).all()
        ids = [c.id for c in clients if _norm(c.name) == _norm(name)]
        if len(ids) > 1:
            raise ValueError("Ambiguous commercial client")
        for p in session.scalars(select(CommercialProject).where(CommercialProject.client_id.in_(ids))):
            yield reference("commercial_builder", p.id, p.title or "Commercial",
                            f"/tools/commercial-builder/project/{p.id}/preview", project=p.title or "Commercial",
                            when=p.updated_at, is_project=True, status=p.status)
            for job in session.scalars(select(RenderJob).where(RenderJob.project_id == p.id,
                                                               RenderJob.status == "succeeded")):
                if job.output_url:
                    approval = session.scalars(select(RenderApproval).where(RenderApproval.render_job_id == job.id)).first()
                    yield reference("commercial_builder", f"render-{job.id}",
                                    f"{p.title or 'Commercial'} — {job.format}",
                                    (approval.stored_url if approval else "") or job.output_url,
                                    resource_type="video", project=p.title or "Commercial",
                                    when=job.created_at, status="Approved" if approval else "Rendered — review in project")
            music = p.music or {}
            if music.get("voice_track_url"):
                yield reference("commercial_builder", f"voice-{p.id}", "Voice track", music["voice_track_url"],
                                project=p.title or "Commercial", resource_type="video", media_type="audio",
                                status="Needs regeneration" if music.get("voice_track_stale") else "Saved")


def proposal_projects(name):
    from modules.sales_builder.app import Quote, SessionLocal
    from hub import proposals
    with SessionLocal() as session:
        for p in session.scalars(select(Quote)):
            if _norm(p.client) != _norm(name):
                continue
            title = "Proposal " + p.quote_number
            yield reference("proposal_builder", p.id, title, f"/sales/builder/?quote={p.id}",
                            project=title, when=p.updated_at, is_project=True, status=p.status)
            # Archived files are reads. The normal PDF endpoint can generate a
            # fresh PDF, so never use it as a stored-file reference.
            for key, label in (("pdf_url", "Proposal PDF"), ("io_client_pdf_url", "Client IO"),
                               ("io_internal_pdf_url", "Internal IO")):
                if getattr(p, key):
                    yield reference("proposal_builder", f"{p.id}-{key}", label, getattr(p, key),
                                    project=title, io_number=p.io_number, when=p.updated_at)
    for p in proposals.list_proposals(name, backfill=False):
        yield reference("uploaded_proposal", p["id"], p.get("filename") or "Proposal document",
                        p.get("url"), project="Proposals", when=p.get("uploaded_at"))


def project_files(label, row):
    """Existing outputs are listed beside their project without copying bytes."""
    pid = row["id"]
    if label in {"Fan Radio", "Radio Promo"}:
        if label == "Fan Radio":
            from modules.fan_radio import store
            project = store.load(pid) or {}
        else:
            project = row
        if _norm(project.get("client")) != _norm(row.get("client")):
            raise ValueError("Project owner no longer matches its index")
        title = row.get("project_name") or row.get("company") or label
        audio = []
        for spot in project.get("spots") or []:
            audio.append((spot.get("id") or spot.get("slot") or len(audio), spot))
            if spot.get("mix"):
                audio.append((str(spot.get("id")) + "-mix", spot["mix"]))
        audio.extend(("mix-" + str(k), v) for k, v in (project.get("mixes") or {}).items())
        for key, item in audio:
            url = item.get("audio_url")
            if url and url.startswith("audio/"):
                url = "/tools/fan-radio/" + url if label == "Fan Radio" else "/tools/radio-promo/" + url
            if url:
                yield reference(label, f"{pid}-{key}", f"{title} — {key}", url, project=title,
                                resource_type="video", media_type="audio", status=item.get("status") or "Saved")
    if label == "GPT Ads":
        from modules.gpt_ads.app import load_pack
        pack = load_pack(pid) or {}
        if _norm(pack.get("client")) != _norm(row.get("client")):
            raise ValueError("Ad owner no longer matches its index")
        image = pack.get("image") or {}
        if image.get("url"):
            yield reference("gpt_ads", pid, pack.get("campaign") or "Ad image", image["url"],
                            project=pack.get("campaign") or "GPT Ads", resource_type="image",
                            public_id=image.get("public_id"), thumb=image["url"])


def catalog(db, client, name):
    assets, projects, sources = [], [], []
    if client is not None:
        assets = [organize(r.to_dict()) for r in db.scalars(
            select(SavedImage).where(SavedImage.client_id == client.id)
            .order_by(SavedImage.created_at.desc(), SavedImage.id.desc())).all()]
    sources.append({"label": "Client gallery", "count": len(assets), "error": ""})
    from .vision import descriptions_for
    seen = descriptions_for([r["id"] for r in assets])
    for row in assets:
        row["seen"] = seen.get(row["id"])
    # The SEO copy of each upload, and how far the backlog has got. Read
    # through the module's own functions so a store that cannot be read is
    # "not measured" on the page rather than a gallery with no copies.
    optimized, progress = {}, {"measured": False}
    if client is not None:
        try:
            from . import optimize
            optimized = optimize.copies_for(db, [r["id"] for r in assets])
            progress = optimize.progress(db, client.id)
        except Exception:
            log.exception("Master gallery could not read optimizations")
    for label, rows, error in legacy_images(name):
        sources.append({"label": label, "count": len(rows), "error": error})
        assets.extend(rows)
    try:
        specs = project_sources(name)
    except Exception:
        log.exception("Master gallery project sources unavailable")
        specs = []
        sources.append({"label": "Hub projects", "count": 0, "error": "Could not load project indexes. Retry to include them."})
    for label, reader, owner, title, prefix in specs:
        try:
            rows = [r for r in reader() if _norm(r.get(owner)) == _norm(name)]
            for r in rows:
                projects.append(reference(label, r["id"], r.get(title) or label,
                    prefix + quote(str(r["id"]), safe=""), project=r.get(title) or label,
                    when=r.get("updated_at") or r.get("updated"), is_project=True,
                    status=r.get("status") or "Saved"))
                assets.extend(project_files(label, r))
            sources.append({"label": label, "count": len(rows), "error": ""})
        except Exception:
            log.exception("Master gallery could not read %s", label)
            sources.append({"label": label, "count": 0, "error": "Could not load projects from this tool. Retry to include them."})
    for label, reader in (("Commercial Builder", commercial_projects), ("Proposals and IOs", proposal_projects)):
        try:
            rows = list(reader(name))
            assets.extend(r for r in rows if not r.get("is_project"))
            projects.extend(r for r in rows if r.get("is_project"))
            sources.append({"label": label, "count": len(rows), "error": ""})
        except Exception:
            log.exception("Master gallery could not read %s", label)
            sources.append({"label": label, "count": 0, "error": "Could not load this source. Retry to include its files."})
    # A legacy reference and a filed row are one file. Keep actual gallery
    # records first, including deliberate references into different projects.
    known_ids, known_urls, merged = set(), set(), []
    for row in assets:
        if not row["url"]:
            continue
        pid, url = row.get("public_id"), row["url"]
        if not row["editable"] and ((pid and pid in known_ids) or url in known_urls):
            continue
        merged.append(row)
        if pid:
            known_ids.add(pid)
        known_urls.add(url)
    for row in merged:
        if row.get("editable") and row["id"] in optimized:
            row["optimized"] = optimized[row["id"]]
    return {"ok": True, "images": merged, "projects": projects, "sources": sources,
            "sections": SECTIONS, "total": len(merged),
            "folders": folders_of(merged + projects),
            "optimization": progress,
            "complete": not any(s["error"] for s in sources)}


def folders_of(rows) -> list[dict]:
    """Every folder in use, with its section and count, defaults first.

    The five sections are listed even at zero so an upload panel can offer
    them; named folders follow, newest activity first, so the one somebody
    filed into last week is near the top of the list they pick from.
    """
    counts: dict[tuple, int] = {}
    latest: dict[tuple, str] = {}
    for r in rows:
        key = (r.get("section") or "creative", r.get("folder") or "General")
        counts[key] = counts.get(key, 0) + 1
        when = str(r.get("created_at") or "")
        if when > latest.get(key, ""):
            latest[key] = when
    out = []
    for section in SECTIONS:
        n = sum(v for (sec, _f), v in counts.items() if sec == section["key"])
        out.append({"label": section["label"], "section": section["key"],
                    "count": n, "default": True})
    named = [{"label": folder, "section": sec, "count": n, "default": False,
              "latest": latest.get((sec, folder), "")}
             for (sec, folder), n in counts.items()
             if folder != SECTION_LABELS.get(sec)]
    named.sort(key=lambda f: (f["latest"], f["label"]), reverse=True)
    return out + named


def folder_index(db, client) -> list[dict]:
    """The folders of one gallery, from its own rows alone.

    What the upload panel reads -- on the client's share link as well as the
    staff gallery -- so it only ever needs the gallery's own table, never the
    project readers a share token has no business reaching.
    """
    if client is None:
        return folders_of([])
    rows = [organize(r.to_dict()) for r in db.scalars(
        select(SavedImage).where(SavedImage.client_id == client.id)).all()]
    return folders_of(rows)
