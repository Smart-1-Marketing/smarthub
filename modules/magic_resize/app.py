"""Magic Resize — one design, the whole size set.

Mounted at ``/tools/magic-resize`` and **not** under ``/tools/display-ads``,
which is the Display Ad Builder's. DispatcherMiddleware routes purely by URL
prefix, so a second module under that prefix never receives a request — the
first trap `CLAUDE.md` names, and it does not even 404: the request is
swallowed by whichever mount owns the prefix.

Dispatcher-mounted rather than registered as a blueprint, so ``AuthGuard`` and
``HubBar`` apply to every route by the mount rather than by each view
remembering. Every screen here is staff-facing and there are **no public
prefixes**: nothing in this tool is served to a client, so there is nothing to
exempt from the login or from the chrome.
"""
from __future__ import annotations

import io

from flask import Flask, jsonify, render_template, request, send_file

from . import engine, export, fabric_io, qc, recompose, roles as R, sizes as S
from . import store
from . import templates_layout as L

try:                                                   # pragma: no cover
    from hub import audit as hub_audit
except Exception:                                      # noqa: BLE001
    hub_audit = None                                   # type: ignore[assignment]

# No static folder: this module ships no asset of its own, and an empty one
# is a directory git will not carry, so the route would point at nothing on a
# fresh clone.
app = Flask(__name__, template_folder="templates", static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

MODULE = "magic_resize"


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def _log(event: str, **extra) -> None:
    """Work filed against a client reaches that client's record.

    `magic_resize` is declared in `hub.client_brand.WORK_KINDS`: a module the
    work log cannot name is one whose rows are written, kept, and then dropped
    on the way to the record they were written for — the `display_ads`
    failure, and the reason `check_work_kinds()` runs at high severity.
    """
    if hub_audit is None:                              # pragma: no cover
        return
    try:
        hub_audit.log(MODULE, event, actor=actor_name(), **extra)
    except Exception:                                  # noqa: BLE001
        pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                  # noqa: BLE001
        return ""


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", version=_version(),
                           projects=store.load_index()[:60],
                           bundles=S.BUNDLES, sizes=S.PLATFORM_SIZES,
                           roles=R.ROLES,
                           alignment=S.check_kit_alignment(),
                           house=S.house_sizes())


@app.route("/p/<pid>")
def project_page(pid: str):
    project = store.get(pid)
    if not project:
        return render_template("missing.html", version=_version()), 404
    return render_template("project.html", version=_version(),
                           project=project, roles=R.ROLES,
                           bundles=S.BUNDLES,
                           min_font=qc.MIN_FONT_PX,
                           min_font_source=qc.MIN_FONT_SOURCE)


@app.route("/p/<pid>/frames/<size_id>/edit")
def frame_edit_page(pid: str, size_id: str):
    """The Fabric editing surface, one frame at a time.

    The API behind this has been here since the module shipped --
    api_frame_fabric() hands out the frame as editable Fabric objects
    and api_frame_save() takes them back -- and nothing has ever called
    either from a browser. This is that screen: drag, resize, rotate and
    retext with Fabric's own controls, then Save posts the canvas back.
    """
    project = store.get(pid)
    frame = (project or {}).get("frames", {}).get(size_id)
    if not frame:
        return render_template("missing.html", version=_version()), 404
    return render_template("frame_edit.html", version=_version(),
                           project=project, pid=pid, size_id=size_id,
                           frame=frame)


# --------------------------------------------------------------------------
# Configuration a screen reads rather than restating
# --------------------------------------------------------------------------

@app.route("/api/config")
def api_config():
    return jsonify({
        "sizes": S.PLATFORM_SIZES,
        "bundles": S.BUNDLES,
        "roles": R.ROLES,
        "required_roles": R.REQUIRED,
        "templates": [{"id": t["id"], "label": t["label"], "note": t["note"],
                       "slots": L.slot_roles(t)} for t in L.TEMPLATES.values()],
        "min_font_px": qc.MIN_FONT_PX,
        "min_font_source": qc.MIN_FONT_SOURCE,
        "ratio_shift_limit": engine.RATIO_SHIFT_LIMIT,
        "kit": S.check_kit_alignment(),
    })


@app.route("/health")
def health():
    alignment = S.check_kit_alignment()
    return jsonify({
        "status": "ok" if alignment.get("measured") else "degraded",
        "sizes": len(S.PLATFORM_SIZES),
        "kit": alignment,
        "projects": len(store.load_index()),
    })


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

@app.route("/api/projects")
def api_projects():
    return jsonify({"projects": store.load_index()[:200]})


@app.route("/api/projects", methods=["POST"])
def api_project_create():
    body = _body()
    source = body.get("source") or {}
    if body.get("from_image_creator"):
        source, err = _import_image_creator(body["from_image_creator"],
                                            body.get("role_map") or {})
        if err:
            return jsonify({"error": err}), 400
    if not (source.get("width") and source.get("height")):
        return jsonify({"error": "The design has no canvas size."}), 400

    project = store.create(name=body.get("name", ""),
                           client=body.get("client", ""),
                           source=source,
                           bundle=body.get("bundle", "display_standard"),
                           created_by=actor_name())
    report = store.generate(project)
    store.save(project)
    _log("project_created", detail=project["name"],
         client=project.get("client", ""))
    return jsonify({"project": project, "report": report})


def _import_image_creator(pid: str, role_map: dict) -> tuple[dict, str]:
    """Open an Image Creator project as a source design.

    Read through that module's own `get_canvas` rather than off its disk: how
    a canvas is stored is its business, and a second reader here is the copy
    that stops matching the day it changes.
    """
    try:
        from modules.image_creator import projects as ic_projects
    except Exception as exc:                           # noqa: BLE001
        return {}, f"Image Creator could not be read: {exc}"
    canvas = ic_projects.get_canvas(pid)
    if not canvas:
        return {}, "No Image Creator project of that id."
    return fabric_io.to_frame(canvas, role_map=role_map), ""


@app.route("/api/projects/<pid>")
def api_project(pid: str):
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    return jsonify({"project": project, "role_map": store.role_map(project)})


@app.route("/api/projects/<pid>", methods=["DELETE"])
def api_project_delete(pid: str):
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    store.delete(pid)
    _log("project_deleted", detail=project.get("name", ""),
         client=project.get("client", ""))
    return jsonify({"ok": True})


@app.route("/api/projects/<pid>/source", methods=["POST"])
def api_project_source(pid: str):
    """Save the design, then apply §6 — copy everywhere, layout only where allowed.

    `kind` says what changed. "text" is a copy edit and reaches every frame,
    an edited one included. "layout" rebuilds the frames that may be rebuilt
    and leaves an edited one exactly as it was. "both" does both, in that
    order, so a rebuilt frame is built from the new words rather than from the
    old ones and then patched.
    """
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    body = _body()
    kind = (body.get("kind") or "both").lower()
    if kind not in ("text", "layout", "both"):
        return jsonify({"error": f"'{kind}' is not a kind of change."}), 400

    if body.get("source"):
        project["source"] = store._clean_source(body["source"])
    if body.get("bundle"):
        project["bundle"] = body["bundle"]

    # Layout first, then copy. A rebuild replaces a frame's objects outright,
    # so laying the words on beforehand writes them into objects that are
    # about to be thrown away — and the frames a rebuild deliberately did not
    # touch are exactly the ones the copy pass exists for.
    layout_report = (store.generate(project) if kind in ("layout", "both")
                     else {"built": [], "kept": [], "skipped": []})
    text_report = (store.propagate_text(project)
                   if kind in ("text", "both")
                   else {"changed": [], "flagged_edited": []})
    store.save(project)
    return jsonify({"project": project, "text": text_report,
                    "layout": layout_report})


@app.route("/api/projects/<pid>/brand")
def api_brand_preview(pid: str):
    """What a client's brand kit holds, before anything is changed."""
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    domain = request.args.get("domain", "")
    return jsonify(store.brand_preview(project, domain))


@app.route("/api/projects/<pid>/brand", methods=["POST"])
def api_brand_apply(pid: str):
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    body = _body()
    result = store.apply_brand(project, domain=body.get("domain", ""),
                               logo=bool(body.get("logo", True)),
                               color_hex=body.get("color", ""))
    if not result.get("applied"):
        return jsonify(result), 400
    store.save(project)
    _log("brand_applied", detail=result.get("domain", ""),
         client=project.get("client", ""))
    return jsonify({"project": project, **result})


@app.route("/api/projects/<pid>/resize", methods=["POST"])
def api_resize(pid: str):
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    body = _body()
    report = store.generate(project, only=body.get("only") or None)
    store.propagate_text(project)
    store.save(project)
    _log("frames_built", detail=f"{len(report['built'])} sizes",
         client=project.get("client", ""))
    return jsonify({"project": project, "report": report})


# --------------------------------------------------------------------------
# One frame
# --------------------------------------------------------------------------

@app.route("/api/projects/<pid>/frames/<size_id>", methods=["POST"])
def api_frame_save(pid: str, size_id: str):
    """Save a frame the editor hand-tuned.

    The body is `{"fabric": <canvas.toJSON() output>}` -- exactly what the
    browser already has once somebody has dragged, resized or retexted an
    object, and exactly the shape api_frame_fabric() below handed out to
    load the canvas in the first place. fabric_io.to_frame() is the one
    reading of what that means as the engine's boxes; a second copy of that
    mapping in the browser is what fabric_io.py's own docstring exists to
    avoid, so nothing here asks the client to compute x/y/w/h itself.
    """
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    existing = (project.get("frames") or {}).get(size_id)
    if not existing:
        return jsonify({"error": "No frame of that size on this project."}), 404
    canvas_json = _body().get("fabric")
    if not isinstance(canvas_json, dict):
        return jsonify({"error": "No Fabric canvas in the request body."}), 400
    parsed = fabric_io.to_frame(canvas_json, width=existing["width"],
                                height=existing["height"])
    frame = store.mark_edited(project, size_id, parsed["objects"])
    if frame is None:
        return jsonify({"error": "No frame of that size on this project."}), 404
    store.save(project)
    return jsonify({"frame": frame, "fabric": fabric_io.to_fabric(frame)})


@app.route("/api/projects/<pid>/frames/<size_id>/fabric")
def api_frame_fabric(pid: str, size_id: str):
    """The frame as editable Fabric objects — never a flattened image."""
    project = store.get(pid)
    frame = (project or {}).get("frames", {}).get(size_id)
    if not frame:
        return jsonify({"error": "No frame of that size on this project."}), 404
    return jsonify({"fabric": fabric_io.to_fabric(frame)})


@app.route("/api/projects/<pid>/frames/<size_id>/recompose", methods=["POST"])
def api_recompose(pid: str, size_id: str):
    """Ask a model for a layout. Writes nothing — the answer is a proposal.

    One frame per press, by construction: there is no route that recomposes a
    set. A model asked for eight layouts produces eight a person then has to
    check, which is the work the templates already did.
    """
    project = store.get(pid)
    frame = (project or {}).get("frames", {}).get(size_id)
    if not frame:
        return jsonify({"error": "No frame of that size on this project."}), 404
    result = recompose.propose(frame)
    if not result.get("ok"):
        return jsonify(result), 502
    result["fabric"] = fabric_io.to_fabric(
        {**frame, "objects": result["objects"]})
    return jsonify(result)


@app.route("/api/projects/<pid>/frames/<size_id>/recompose/keep",
           methods=["POST"])
def api_recompose_keep(pid: str, size_id: str):
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    frame = store.mark_ai(project, size_id, _body().get("objects") or [])
    if frame is None:
        return jsonify({"error": "No frame of that size on this project."}), 404
    store.save(project)
    _log("frame_recomposed", detail=size_id, client=project.get("client", ""))
    return jsonify({"frame": frame})


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

@app.route("/api/projects/<pid>/export", methods=["POST"])
def api_export(pid: str):
    """Judge rendered frames against their ceilings, and pack what passed.

    The browser renders — Fabric is the only thing that can draw a Fabric
    frame — so what arrives is finished bytes per size. A frame that cannot
    get under its ceiling above the quality floor is named rather than shipped
    soft.
    """
    project = store.get(pid)
    if not project:
        return jsonify({"error": "No project of that id."}), 404
    body = _body()
    rendered = body.get("frames") or []
    prepared = []
    for row in rendered:
        data = _decode(row.get("image", ""))
        prepared.append(export.prepare(row.get("size_id", ""), data,
                                       fmt=row.get("fmt", "png")))
    blob, report = export.bundle(prepared)
    _log("pack_exported",
         detail=f"{sum(1 for r in report if r.get('included'))} of {len(report)}",
         client=project.get("client", ""))
    if not body.get("report_only"):
        _file_to_gallery(project, prepared)
    if body.get("report_only"):
        return jsonify({"report": report})
    name = store.slugify(project.get("name", "resize")) or "resize"
    return send_file(io.BytesIO(blob), mimetype="application/zip",
                     as_attachment=True, download_name=f"{name}-sizes.zip")


def _file_to_gallery(project: dict, prepared: list[dict]) -> None:
    """Put every size that made the cut into the client's own gallery.

    The ZIP handed to a browser is a download, not a record: nothing else
    reads it, and a client asking "what have you built us?" would see
    nothing here at all. Every other producer in this Hub files what it
    makes (`hub/image_audit.py`), so this does too — one Cloudinary upload
    and one gallery row per size, best-effort, because the export a rep is
    waiting on has already succeeded and a gallery that will not answer must
    not cost them that.
    """
    client = str(project.get("client") or "").strip()
    if not client:
        return
    try:
        from hub import storage
        from modules.image_picker import filing
    except Exception:                                    # noqa: BLE001
        return
    name = project.get("name") or "resize"
    for row in prepared:
        if row.get("ok") is False or not row.get("data"):
            continue
        size_id = row.get("size_id", "")
        try:
            stored = storage.put(
                "magic_resize", export.filename_for(size_id, row.get("fmt", "png")),
                row["data"], client=client, subpath=store.slugify(name))
            filing.file_asset(
                client_name=client, public_id=stored.public_id, url=stored.url,
                kind="display_ad", label=f"Magic Resize — {name}",
                key=store.slugify(name)[:80],
                filename=export.filename_for(size_id, row.get("fmt", "png")),
                resource_type="image", size_bytes=stored.bytes,
                provider="magic_resize", saved_by=actor_name())
        except Exception:                                # noqa: BLE001
            continue


def _decode(data_url: str) -> bytes:
    import base64
    raw = str(data_url or "")
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:                                  # noqa: BLE001
        return b""
