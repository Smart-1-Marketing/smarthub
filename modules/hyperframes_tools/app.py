"""Paint Animation and Vox Explainer as standalone tools.

Two blueprints, one set of plumbing. Submitting a render, polling it,
attaching the finished file to a client and listing what somebody has made
are identical for both; only the parameters and the copy differ.

**Where the finished file goes, and why it is not the image gallery.** Both
tools produce video. `modules/image_picker/filing.file_asset` models an image
or a raw file, and the Commercial Builder deliberately leaves its finished
commercials, spokesperson clips and voice tracks in the client's Cloudinary
tree for exactly that reason — a gallery row whose thumbnail can never render
is worse than an absent one. So a kept render goes to the client's Cloudinary
tree and to the **activity log**, which is what puts it on their 360 record,
and the two writes are reported apart: "stored" and "stored and on their
record" are different outcomes, and one tick over both is how somebody learns
not to trust the tick.

**A client is optional and is never guessed at.** With none picked the render
is still made and still downloadable — the Background Remover and the UTM
Builder both work that way — and nothing is filed anywhere. What is refused is
a *typed* client name: it is a searchable list of real clients or nothing, for
the reason `hub/client_key.py` gives at length, because a name matching
nothing files a render under a client nothing joins to and reads as success.

**A mock is never filed.** With no render service configured a job reports
success and produces no file; `hyperframes.is_deliverable()` is the single
test, the refusal `approve_render` already makes about a mock Creatomate
render.

**Staff only, on the blueprint rather than on each view.** These are hub-app
blueprints, so `wsgi.py`'s `AuthGuard` — which wraps dispatcher-mounted
modules — never sees them, and the hub app has no blanket gate of its own.
The guard goes on the blueprint so the next route added does not have to
remember, which is the arrangement `hub/blueprint_guard.py` exists for.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from hub import hyperframes

from . import jobs

log = logging.getLogger(__name__)

PAINT_MOUNT = "/tools/paint-animation"
VOX_MOUNT = "/tools/vox-explainer"

paint_bp = Blueprint("paint_animation", __name__,
                     template_folder="templates",
                     static_folder="static",
                     static_url_path="/static")
vox_bp = Blueprint("vox_explainer", __name__,
                   template_folder="templates",
                   static_folder="static",
                   static_url_path="/static")

for _bp, _mount in ((paint_bp, PAINT_MOUNT), (vox_bp, VOX_MOUNT)):
    try:
        from hub.blueprint_guard import install as _install_guard
        _install_guard(_bp, mount=_mount)
    except Exception:                                    # noqa: BLE001
        pass                                             # standalone, no Hub

# Activity logging. Both tools produce a finished file for a client, and work
# that is not logged is work nobody can point to later.
#
# Two names rather than one shared one, because `hub/client_brand.WORK_KINDS`
# reads them to put the row on the client's 360 record, and "a paint
# animation" and "an explainer" are two different deliverables to see listed
# there. The name is DATA and the call is direct: an earlier version passed
# each tool's bound logger down as a parameter, and a logger reached through a
# parameter is one no static walk can follow -- `/api/integrity`'s
# silent-module check duly reported this module as never logging at all, which
# was a true statement about what it could see. The fix is a call it can read
# rather than an exemption saying to trust us.
try:
    from hub import audit as _hub_audit
except Exception:                                        # noqa: BLE001
    _hub_audit = None

LOG_NAMES = {"paint-animation": "paint_animation",
             "vox-explainer": "vox_explainer"}


def _record(tool, event, *, client, detail):
    """One activity row. Never raises — a log that breaks a filing is worse."""
    if _hub_audit is None:
        return False
    try:
        _hub_audit.log(LOG_NAMES.get(tool, tool), event,
                       client=client, detail=detail)
        return True
    except Exception as exc:                             # noqa: BLE001
        log.warning("activity log failed: %s", exc)
        return False


# Writes here that deliberately record nothing, with the reason. The line
# these tools record on is whether a file reaches the client's own Cloudinary
# tree: `keep_paint` and `keep_vox` do and they record; starting a render that
# nobody has kept yet has produced nothing for anybody.
HOUSEKEEPING_ROUTES = {
    "start_paint": "starts a render. Nothing has been made for a client until "
                   "it is kept, and keeping it is what records.",
    "start_vox": "same — the beat list is a draft until it is rendered and kept.",
    "draft_vox_beats": "writes an outline for somebody to read. A POST because "
                       "it reaches OpenAI, which `hub/ai.py` records; nothing "
                       "is stored and nothing has been made for a client.",
    "forget_paint": "drops this person's own row for a render. The file, if it "
                    "was kept, is in the client's library and is untouched.",
    "forget_vox": "same.",
}


# --------------------------------------------------------------------------- #
# shared plumbing
# --------------------------------------------------------------------------- #

def _owner() -> str:
    """Who is asking. Never a display name.

    Two people on this roster share a first name, so a list keyed on one shows
    somebody another person's renders — `hub/celebrations.mine()` has paid for
    that already. A shared-password session has no account behind it and is
    given its own bucket rather than somebody's.
    """
    try:
        from hub.users_routes import current_account
        account = current_account()
        if account and getattr(account, "email", ""):
            return str(account.email).strip().lower()
    except Exception:                                    # noqa: BLE001
        pass
    return "shared-login"


def _fail(message, code=400, **extra):
    return jsonify({"ok": False, "error": message, **extra}), code


def _unavailable():
    return _fail(hyperframes.why_unavailable(), 503, configured=False)


def _resolve_client(name: str):
    """A real client, or nothing. Never a typed string taken at face value.

    Exactly one match or none, through the Hub's own resolver — no substring,
    for the reason `hub/client_key.py` gives at length: "Riverside HVAC" must
    not collect "Riverside HVAC Supply", and a render filed under the wrong
    client is the one mistake here nobody can undo by editing a row.

    Returns `(name, error)`. A client list that could not be read is its own
    answer: "no such client" and "we could not look" send somebody to two
    different places, and only the first means the name is wrong.
    """
    name = (name or "").strip()
    if not name:
        return "", ""
    try:
        from hub import client_key
        match = client_key.resolve(name, allow_fuzzy=False)
    except Exception as exc:                             # noqa: BLE001
        log.warning("client lookup failed: %s", exc)
        return "", ("The client list could not be read, so this was not filed "
                    "against anybody. The render is still yours to download.")
    # `resolve()` always answers, so `known` is the question rather than
    # truthiness: it hands back the input verbatim under `client` when nothing
    # matched, and reading that as a match is exactly the typed-name failure
    # this function exists to refuse.
    if not (match or {}).get("known"):
        return "", (f"No client is filed as “{name}”, so this was not attached "
                    "to anybody. Pick one from the list.")
    return str(match.get("client") or name), ""


def _submit(*, tool, template, params, client, label):
    """One render, written down. Shared by both tools."""
    if not hyperframes.is_configured():
        return _unavailable()
    job = hyperframes.submit(template, params)
    row = jobs.create(tool=tool, owner=_owner(), params=params, job=job,
                      client=client, label=label)
    if job.get("status") == "failed":
        return jsonify({"ok": False, "error": job.get("error"), "job": row}), 502
    return jsonify({"ok": True, "job": row})


def _poll(job_id, *, tool):
    """Where a render got to, and the file attached when it lands.

    The write-through is the point: any request for the status stores a
    finished URL on the row, so closing the tab does not lose a render nobody
    is going to start again.
    """
    row = jobs.get(job_id)
    if not row or row.get("tool") != tool or row.get("owner") != _owner():
        # Somebody else's job and one that never existed answer the same 404.
        return _fail("There is no such render.", 404)
    if row.get("status") in ("done", "failed"):
        return jsonify({"ok": row["status"] == "done", "job": row})

    state = hyperframes.status(row.get("job_id"))
    row = jobs.update(row["id"], status=state.get("status") or row["status"],
                      url=state.get("url") or row.get("url"),
                      error=state.get("error"),
                      duration_seconds=state.get("duration_seconds")) or row
    return jsonify({"ok": state.get("status") != "failed", "job": row})


def _keep(job_id, *, tool, event, kind_label):
    """File a finished render against the client it was made for.

    Two writes, reported apart — the `hub/domain_links.py` rule. The video
    goes to the client's Cloudinary tree (never the image gallery: a row whose
    thumbnail can never render is worse than an absent one), and the activity
    log is what puts it on their 360 record.
    """
    row = jobs.get(job_id)
    if not row or row.get("tool") != tool or row.get("owner") != _owner():
        return _fail("There is no such render.", 404)
    if not hyperframes.is_deliverable({"url": row.get("url"),
                                       "status": row.get("status"),
                                       "_mock": row.get("mock")}):
        # A mock reports success and produces no file. Filing one is a
        # delivered asset with nothing behind it.
        return _fail("This render produced no file, so there is nothing to "
                     "file. That is what happens with no render service "
                     "configured — the job is a mock.")

    client = (request.get_json(silent=True) or {}).get("client") or row.get("client")
    resolved, why = _resolve_client(client)
    if not resolved:
        return _fail(why or "Pick a client to file this against.")

    stored, store_error = _store(row["url"], resolved, row["id"], tool)
    # Recorded only where the file actually reached the client's library. A
    # row saying we made them something we could not store is the confident
    # wrong answer `approve_render` refuses one module over.
    logged = _record(tool, event, client=resolved,
                     detail=f"{kind_label} · {row.get('label') or row['id']}") \
        if stored else False

    row = jobs.update(row["id"], client=resolved,
                      filed={"url": stored, "logged": logged,
                             "error": store_error}) or row
    return jsonify({
        "ok": bool(stored), "job": row,
        # Reported apart, because "stored" and "stored and on their record"
        # are different outcomes.
        "stored": bool(stored), "on_record": logged,
        "error": store_error or ("" if stored else "The file could not be stored."),
    })


def _store(url, client_name, job_id, tool):
    """Copy the render into the client's Cloudinary tree.

    The render service keeps its output behind its own retention sweep, so a
    link to it is one that stops working — the same reason a HeyGen clip is
    mirrored rather than linked. Returns `(url, error)`; never raises.
    """
    try:
        from hub import storage
        # put_remote, not put: Cloudinary fetches the file itself rather than
        # this process downloading a video and posting it back up, which is
        # slower, doubles the bandwidth and fails in cases where Cloudinary's
        # own fetch succeeds. `kind` is a logical bucket resolved to a folder
        # by hub/config -- never a raw folder string -- and the client is
        # passed so the asset lands in that client's own subtree.
        asset = storage.put_remote(kind="commercials", url=url,
                                   filename=f"{tool}-{job_id}.mp4",
                                   client=client_name, subpath=tool)
        if not getattr(asset, "url", ""):
            return "", "The file could not be stored in the client's library."
        return asset.url, ""
    except Exception as exc:                             # noqa: BLE001
        log.warning("store failed: %s", exc)
        return "", ("The render was made but could not be copied into the "
                    "client's library. The link below still works for now.")


# --------------------------------------------------------------------------- #
# Paint Animation
# --------------------------------------------------------------------------- #

@paint_bp.get("/")
def paint_home():
    return render_template("hf_paint.html",
                           styles=hyperframes.PAINT_STYLES,
                           formats=[f["id"] for f in _formats()],
                           ready=hyperframes.is_configured(),
                           note=hyperframes.why_unavailable(),
                           max_seconds=hyperframes.PAINT_MAX_SECONDS)


@paint_bp.post("/api/render")
def start_paint():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "")
    image_url = str(data.get("image_url") or "")
    seconds = data.get("seconds", 5)
    refusal = hyperframes.paint_refusal(text=text, image_url=image_url,
                                        seconds=seconds)
    if refusal:
        return _fail(refusal)
    params = hyperframes.paint_params(text=text, image_url=image_url,
                                      style=data.get("style"), seconds=seconds,
                                      format_id=data.get("format") or "16:9")
    return _submit(tool="paint-animation", template="paint-animation",
                   params=params, client=str(data.get("client") or ""),
                   label=(text or image_url)[:120])


@paint_bp.get("/api/render/<job_id>")
def paint_status(job_id):
    return _poll(job_id, tool="paint-animation")


@paint_bp.post("/api/render/<job_id>/keep")
def keep_paint(job_id):
    return _keep(job_id, tool="paint-animation",
                 event="paint_animation_made", kind_label="Paint animation")


@paint_bp.get("/api/renders")
def paint_jobs():
    return jsonify({"ok": True, "jobs": jobs.listing(_owner(), "paint-animation")})


@paint_bp.delete("/api/render/<job_id>")
def forget_paint(job_id):
    return _forget(job_id, "paint-animation")


# --------------------------------------------------------------------------- #
# Vox Explainer
# --------------------------------------------------------------------------- #

@vox_bp.get("/")
def vox_home():
    from modules.commercial_builder import vox_spec
    return render_template("hf_vox.html",
                           source_kinds=vox_spec.SOURCE_KINDS,
                           treatments=vox_spec.TREATMENTS,
                           formats=list(vox_spec.FORMATS),
                           min_beats=vox_spec.MIN_BEATS,
                           max_beats=vox_spec.MAX_BEATS,
                           vox_min=hyperframes.VOX_MIN_SECONDS,
                           vox_max=hyperframes.VOX_MAX_SECONDS,
                           target=vox_spec.TARGET_SECONDS,
                           ready=hyperframes.is_configured(),
                           note=hyperframes.why_unavailable())


@vox_bp.post("/api/beats")
def draft_vox_beats():
    """The outline, for somebody to read before anything is rendered.

    Deliberately its own press. A render is minutes of headless Chrome and
    the beat list is the only thing anybody can correct before it — the
    "approve before spend" shape every expensive step in this Hub uses.
    """
    from modules.commercial_builder import vox_spec
    from modules.commercial_builder.services import openai_service
    data = request.get_json(silent=True) or {}
    result = openai_service.generate_vox_beats(
        vox_spec.clean_source_kind(data.get("source_kind")),
        str(data.get("source_text") or ""),
        {}, title=str(data.get("title") or ""),
        total_seconds=vox_spec.TARGET_SECONDS,
        link=str(data.get("link") or ""))
    if not result.get("beats"):
        return _fail(result.get("error") or "Nothing could be built into an "
                     "explainer from that.", 502,
                     dropped=result.get("dropped") or [])
    return jsonify({"ok": True, **result})


@vox_bp.post("/api/render")
def start_vox():
    from modules.commercial_builder import vox_spec
    data = request.get_json(silent=True) or {}
    checked = vox_spec.validate(data.get("beats"),
                                total_seconds=vox_spec.TARGET_SECONDS)
    if len(checked["beats"]) < vox_spec.MIN_BEATS:
        return _fail(f"An explainer needs at least {vox_spec.MIN_BEATS} beats "
                     f"and this has {len(checked['beats'])}.",
                     dropped=checked["dropped"])
    params = hyperframes.vox_params(title=str(data.get("title") or ""),
                                    beats=checked["beats"],
                                    format_id=data.get("format") or "16:9")
    return _submit(tool="vox-explainer", template="vox-explainer",
                   params=params, client=str(data.get("client") or ""),
                   label=str(data.get("title") or "")[:120])


@vox_bp.get("/api/render/<job_id>")
def vox_status(job_id):
    return _poll(job_id, tool="vox-explainer")


@vox_bp.post("/api/render/<job_id>/keep")
def keep_vox(job_id):
    return _keep(job_id, tool="vox-explainer",
                 event="vox_explainer_made", kind_label="Vox explainer")


@vox_bp.get("/api/renders")
def vox_jobs():
    return jsonify({"ok": True, "jobs": jobs.listing(_owner(), "vox-explainer")})


@vox_bp.delete("/api/render/<job_id>")
def forget_vox(job_id):
    return _forget(job_id, "vox-explainer")


# --------------------------------------------------------------------------- #

def _forget(job_id, tool):
    row = jobs.get(job_id)
    if not row or row.get("tool") != tool or row.get("owner") != _owner():
        return _fail("There is no such render.", 404)
    jobs.remove(row["id"])
    # Said out loud, because the row and the file are different things and
    # somebody pressing this should not be left wondering which went.
    return jsonify({"ok": True,
                    "note": ("Removed from your list. Anything you filed "
                             "against a client is untouched.")})


def _formats():
    """The output shapes, read from the Commercial Builder rather than typed.

    One description of what 16:9 and 9:16 mean here, or the standalone tool
    and the wizard come to offer different sets.
    """
    try:
        from modules.commercial_builder.config import OUTPUT_FORMATS
        return OUTPUT_FORMATS
    except Exception:                                    # noqa: BLE001
        return [{"id": "16:9"}, {"id": "9:16"}, {"id": "1:1"}]


def register(app):
    app.register_blueprint(paint_bp, url_prefix=PAINT_MOUNT)
    app.register_blueprint(vox_bp, url_prefix=VOX_MOUNT)
