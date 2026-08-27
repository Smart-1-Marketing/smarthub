"""Smart 1 Hub — Social Content Planner.

Bulk-builds a month of organic social posts for a client we manage, and exports
them for Smart 1 Suite's Social Planner.

## The bottleneck this is aimed at

Writing twenty posts is not twenty times the work of writing one — it is twenty
times the *context switching*. What the client does, who they serve, what
they're allowed to claim, which photos exist, what ran last month. That context
already lives in the Hub: the client registry, the Brandfetch brand kit, the
live Knack product records, the image gallery. A blank prompt box makes the
strategist re-assemble it from memory every time.

So the flow is: pick a client, and the plan arrives already knowing who they
are. Everything after that is editing rather than authoring.

    client  ->  grid  ->  draft  ->  images  ->  review  ->  export

Each stage is separately re-runnable. Re-drafting one slot does not touch the
other nineteen, and nothing is destructive: drafting only fills slots that are
empty, and image assignment only fills slots with no image.

## What this deliberately does not do

**It does not post.** Phase 1 ends at a CSV, which Social Planner ingests under
Bulk Upload. Pushing through the Social Planner API needs
`social-media-posting.write` on the marketplace app, and adding a scope to
`hub/ghl_oauth.py` requires re-consent at the agency — a one-time manual step
that has to happen before any code depending on it is worth writing. Ending at
the CSV means the whole drafting pipeline is in production and earning its keep
while that is pending, and if the scope never lands the tool still works.

**It does not generate images.** The client's gallery usually already has what
a post needs, and Image Creator is a better tool than a button here would be.
Slots with no gallery match link straight into it.

## Storage

One file per batch under ``jsonstore.data_dir("social")``, plus an index. Both
go through ``hub.jsonstore`` so they are mirrored into the database — a month
of approved copy that existed only on the Render disk would vanish on a disk
resize with no error anywhere, which is exactly the failure that module is for.
Deletes go through ``jsonstore.delete_json``; a bare ``os.remove`` leaves the
database copy to be restored on the next read, so the delete undoes itself.
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from hub import jsonstore, social_plan

try:
    from hub import audit as hub_audit
except Exception:                                     # noqa: BLE001
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

MODULE = "social_planner"
MAX_BATCHES = 400

_lock = threading.Lock()


# ------------------------------------------------------------------ storage
def _dir() -> str:
    return jsonstore.data_dir("social")


def _index_path() -> str:
    return os.path.join(_dir(), "index.json")


def _batch_path(batch_id: str) -> str:
    return os.path.join(_dir(), f"batch-{batch_id}.json")


def _read_index() -> list[dict]:
    rows = jsonstore.read_json(_index_path(), default=[])
    return rows if isinstance(rows, list) else []


def _write_index(rows: list[dict]) -> None:
    jsonstore.write_json(_index_path(), rows[:MAX_BATCHES], indent=1)


def _summarise(batch: dict) -> dict:
    """The index row. Counts are recomputed here rather than stored twice —
    a summary that can disagree with the thing it summarises always eventually
    does."""
    counts = social_plan.validate_batch(batch)
    return {
        "id": batch["id"], "client": batch.get("client", ""),
        "domain": batch.get("domain", ""), "month": batch.get("month", ""),
        "status": batch.get("status", "draft"),
        "channels": batch.get("channels", []),
        "slots": counts["slots"], "drafted": counts["drafted"],
        "block": counts["block"], "warn": counts["warn"],
        "created_at": batch.get("created_at", ""),
        "created_by": batch.get("created_by", ""),
        "updated_at": batch.get("updated_at", ""),
    }


def load_batch(batch_id: str) -> dict | None:
    if not re.match(r"^[a-z0-9]{6,24}$", str(batch_id or "")):
        return None
    batch = jsonstore.read_json(_batch_path(batch_id), default=None)
    return batch if isinstance(batch, dict) and batch.get("id") else None


def save_batch(batch: dict) -> dict:
    batch["updated_at"] = _now()
    social_plan.validate_batch(batch)
    with _lock:
        jsonstore.write_json(_batch_path(batch["id"]), batch, indent=1)
        rows = [r for r in _read_index() if r.get("id") != batch["id"]]
        rows.insert(0, _summarise(batch))
        _write_index(rows)
    return batch


def delete_batch(batch_id: str) -> bool:
    with _lock:
        rows = _read_index()
        remaining = [r for r in rows if r.get("id") != batch_id]
        if len(remaining) == len(rows):
            return False
        # jsonstore.delete_json, never os.remove: removing only the file leaves
        # the database copy to be restored by the next read.
        jsonstore.delete_json(_batch_path(batch_id))
        _write_index(remaining)
    return True


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def actor_name() -> str:
    return request.environ.get("s1hub.user") or "Unknown"


def _new_id() -> str:
    return format(int(time.time() * 1000), "x")[-9:] + os.urandom(2).hex()


def _log(event: str, **extra):
    if hub_audit is None:
        return
    try:
        # audit.log()'s first positional is `module`; extras use tool=.
        hub_audit.log(MODULE, event, actor=actor_name(), **extra)
    except Exception:                                 # noqa: BLE001
        pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                 # noqa: BLE001
        return ""


def _fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _str(value, limit: int = 4000) -> str:
    return str(value if value is not None else "")[:limit]


# ------------------------------------------------------------------ context
def _client_context(client: str, url: str = "") -> dict:
    """What the Hub already knows about this client, assembled once.

    Every lookup is optional and every one is wrapped: a client with no
    Brandfetch record, no products and no gallery must still be plannable. An
    absent source reports itself as absent rather than as an empty result —
    "no photos on file" and "the gallery is unreachable" are different answers
    and the strategist needs to know which one they got.
    """
    out = {"client": client, "url": url, "domain": "", "industry": "",
           "description": "", "products": [], "colors": [], "logo": "",
           "gallery": [], "gallery_note": "", "brand_note": ""}
    try:
        from hub.client_context import canonical_domain
        out["domain"] = canonical_domain(url or client) or ""
    except Exception:                                 # noqa: BLE001
        pass

    try:
        from hub import clients_registry
        row = clients_registry.find_client(client)
        if row:
            out["url"] = out["url"] or row.get("url") or ""
            out["domain"] = out["domain"] or row.get("domain") or ""
            products = sorted(row.get("running") or row.get("products") or [])
            out["products"] = [str(p) for p in products][:12]
    except Exception:                                 # noqa: BLE001
        pass

    try:
        from hub import client_brand
        kit = client_brand.brand_kit(client, out["domain"])
        if kit.get("found"):
            out["description"] = kit.get("description") or ""
            out["colors"] = [c["hex"] for c in (kit.get("colors") or [])][:6]
            logos = kit.get("logos") or []
            out["logo"] = logos[0]["url"] if logos else ""
        else:
            out["brand_note"] = kit.get("note") or ""
    except Exception:                                 # noqa: BLE001
        out["brand_note"] = "Brand lookup unavailable."

    images, note = _gallery_images(client)
    out["gallery"] = images
    out["gallery_note"] = note
    return out


def _gallery_images(client: str, limit: int = 60) -> tuple[list[dict], str]:
    """The client's existing images, newest first.

    Reads the Image Picker gallery directly rather than over HTTP — it is the
    same process, and a background draft should not need a session cookie to
    talk to it. Name matching is the narrow kind ``filing.gallery_for_name``
    does: an exact slug or nothing, because filing one client's photos onto
    another client's Facebook page is the worst thing this tool could do.
    """
    try:
        from sqlalchemy import select

        from modules.image_picker import filing
        from modules.image_picker.models import SavedImage, session
    except Exception:                                 # noqa: BLE001
        return [], "Image gallery unavailable in this environment."

    try:
        db = session()
    except Exception:                                 # noqa: BLE001
        return [], "Image gallery database unreachable."
    try:
        gallery = filing.gallery_for_name(db, client)
        if gallery is None:
            return [], f"No image gallery on file for {client}."
        rows = db.execute(
            select(SavedImage)
            .where(SavedImage.client_id == gallery.id)
            .where(SavedImage.resource_type == "image")
            .order_by(SavedImage.created_at.desc())
            .limit(limit)
        ).scalars().all()
        out = []
        for row in rows:
            url = row.cloudinary_url or row.source_url or ""
            if not url.startswith("https://"):
                continue
            out.append({
                "url": url,
                "public_id": row.cloudinary_public_id or "",
                "alt": (row.alt_text or "")[:300],
                "label": (row.collection_label or row.filename or "")[:120],
            })
        if not out:
            return [], f"{client}'s gallery is empty — add images in Client " \
                       "Image Uploads or make one in Image Creator."
        return out, ""
    except Exception as exc:                          # noqa: BLE001
        return [], f"Image gallery read failed ({type(exc).__name__})."
    finally:
        try:
            db.close()
        except Exception:                             # noqa: BLE001
            pass


# =====================================================================
# Pages
# =====================================================================
@app.route("/")
def index():
    # Everything the page needs arrives as one JSON blob in a
    # <script type="application/json"> tag rather than as Jinja interpolated
    # into JavaScript. That keeps the page's real script block free of {{ }},
    # so tools/jscheck.py can hand it to node --check — the strict parser —
    # instead of skipping it for checktemplates' balance check.
    boot = {"spec": social_plan.spec_payload(),
            "client": request.args.get("client", "")[:200],
            "url": request.args.get("url", "")[:300]}
    return render_template("index.html", version=_version(), boot=boot)


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": _version(),
                    "batches": len(_read_index())})


# =====================================================================
# Clients and context
# =====================================================================
@app.route("/api/clients")
def api_clients():
    q = _str(request.args.get("q", ""), 80).strip()
    try:
        from hub import clients_registry
        rows = clients_registry.search_clients(q, limit=12) if q else \
            clients_registry.all_clients()[:12]
    except Exception as exc:                          # noqa: BLE001
        return _fail(f"Client list unavailable ({type(exc).__name__}).", 502)
    out = [{"name": r.get("name", ""), "url": r.get("url", ""),
            "domain": r.get("domain", ""), "live": bool(r.get("live"))}
           for r in rows if r.get("name")]
    return jsonify({"ok": True, "clients": out})


@app.route("/api/holidays")
def api_holidays():
    """The dated hooks available in a month, filtered to the client's trade.

    Offered rather than applied: a plan is built with the ones the strategist
    ticked, and the list says out loud that it is ours rather than an authority
    — there is no authority publishing national days.
    """
    month = _str(request.args.get("month", ""), 10).strip()
    if not month:
        return _fail("Pick a month first.")
    industries = [i for i in (request.args.get("industries") or "").split(",") if i.strip()]
    if not industries:
        client = _str(request.args.get("client", ""), 200).strip()
        if client:
            ctx = _client_context(client)
            industries = [ctx.get("industry", "")] if ctx.get("industry") else []
    try:
        days = social_plan.holidays_for(month, industries)
    except ValueError as exc:
        return _fail(str(exc))
    return jsonify({"ok": True, "holidays": days,
                    "source": social_plan.HOLIDAY_SOURCE})


@app.route("/api/context")
def api_context():
    client = _str(request.args.get("client", ""), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    return jsonify({"ok": True,
                    "context": _client_context(client,
                                               _str(request.args.get("url", ""), 300))})


# =====================================================================
# Batches
# =====================================================================
@app.route("/api/batches")
def api_batches():
    return jsonify({"ok": True, "batches": _read_index()[:120]})


@app.route("/api/batches", methods=["POST"])
def api_create_batch():
    data = request.get_json(silent=True) or {}
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    month = _str(data.get("month"), 10).strip()
    channels = [c for c in (data.get("channels") or [])
                if c in social_plan.CHANNELS]
    if not channels:
        return _fail("Pick at least one channel.")
    try:
        per_week = int(data.get("per_week") or 3)
    except (TypeError, ValueError):
        per_week = 3
    mix = data.get("mix") if isinstance(data.get("mix"), dict) else None
    blackout = [_str(d, 10) for d in (data.get("blackout") or [])][:31]

    holidays = [h for h in (data.get("holidays") or [])
                if isinstance(h, dict) and h.get("date") and h.get("name")][:40]
    try:
        slots = social_plan.build_grid(month, channels=channels,
                                       per_week=per_week, mix=mix,
                                       blackout=blackout, holidays=holidays)
    except ValueError as exc:
        return _fail(str(exc))
    if not slots:
        return _fail("That month has no posting days left once the blackout "
                     "dates are removed.")

    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    context = _client_context(client, _str(data.get("url"), 300))
    batch = {
        "id": _new_id(),
        "client": client,
        "url": context.get("url", ""),
        "domain": context.get("domain", ""),
        "month": month,
        "channels": channels,
        "per_week": per_week,
        "mix": mix or dict(social_plan.DEFAULT_MIX),
        "blackout": blackout,
        "status": "draft",
        "brief": {
            # Picked tones and the free-text box both survive: the options
            # carry the guidance the model needs, and the box is for the
            # client whose voice is genuinely their own.
            "tones": [t for t in (brief.get("tones") or [])
                      if t in social_plan.TONES][:4],
            "tone": _str(brief.get("tone"), 200),
            "promote": [_str(x, 200) for x in (brief.get("promote") or [])
                        if str(x).strip()][:12],
            "use_holidays": bool(data.get("use_holidays")),
            "offers": _str(brief.get("offers"), 2000),
            "notes": _str(brief.get("notes"), 4000),
            "phone": _str(brief.get("phone"), 40),
            "url": context.get("url", ""),
            "avoid": _str(brief.get("avoid"), 500),
            "must_include": [_str(x, 200) for x in
                             (brief.get("must_include") or [])][:10],
        },
        "context": {k: context.get(k) for k in
                    ("industry", "description", "products", "colors", "logo")},
        "slots": slots,
        "created_at": _now(),
        "created_by": actor_name(),
    }
    save_batch(batch)
    _log("batch_created", client=client, month=month, slots=len(slots),
         channels=",".join(channels))
    return jsonify({"ok": True, "batch": batch})


@app.route("/api/batches/<batch_id>")
def api_batch(batch_id: str):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    social_plan.validate_batch(batch)
    return jsonify({"ok": True, "batch": batch,
                    "context": _client_context(batch.get("client", ""),
                                               batch.get("url", ""))})


@app.route("/api/batches/<batch_id>", methods=["PUT"])
def api_save_batch(batch_id: str):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    data = request.get_json(silent=True) or {}

    if isinstance(data.get("brief"), dict):
        for key in ("tone", "offers", "notes", "phone", "avoid"):
            if key in data["brief"]:
                batch["brief"][key] = _str(data["brief"][key], 4000)
        if "tones" in data["brief"]:
            batch["brief"]["tones"] = [t for t in (data["brief"]["tones"] or [])
                                       if t in social_plan.TONES][:4]
        if "promote" in data["brief"]:
            batch["brief"]["promote"] = [_str(x, 200) for x in
                                         (data["brief"]["promote"] or [])
                                         if str(x).strip()][:12]

    incoming = {s.get("id"): s for s in (data.get("slots") or [])
                if isinstance(s, dict) and s.get("id")}
    for slot in batch["slots"]:
        edit = incoming.get(slot["id"])
        if not edit:
            continue
        # Only the fields a person can change on the review screen. The date,
        # the channels and the type come from the grid; changing them here
        # would let the calendar and the plan disagree silently.
        if "copy" in edit:
            new_copy = _str(edit["copy"], 6000)
            if new_copy != slot.get("copy", ""):
                slot["copy"] = new_copy
                slot["status"] = "edited" if new_copy.strip() else "empty"
        if "hashtags" in edit:
            slot["hashtags"] = [_str(t, 60) for t in (edit["hashtags"] or [])][:30]
        if "link" in edit:
            slot["link"] = _str(edit["link"], 500)
        if "image_url" in edit:
            slot["image_url"] = _str(edit["image_url"], 700)
            slot["image_public_id"] = _str(edit.get("image_public_id"), 400)
            slot["image_source"] = "gallery" if slot["image_url"] else ""
        if edit.get("status") in social_plan.STATUSES:
            slot["status"] = edit["status"]

    save_batch(batch)
    return jsonify({"ok": True, "batch": batch})


@app.route("/api/batches/<batch_id>", methods=["DELETE"])
def api_delete_batch(batch_id: str):
    if not load_batch(batch_id):
        return _fail("That plan no longer exists.", 404)
    delete_batch(batch_id)
    _log("batch_deleted", batch=batch_id)
    return jsonify({"ok": True})


@app.route("/api/batches/<batch_id>/status", methods=["POST"])
def api_batch_status(batch_id: str):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    wanted = _str((request.get_json(silent=True) or {}).get("status"), 20)
    if wanted not in ("draft", "review", "approved"):
        return _fail("Unknown status.")
    counts = social_plan.validate_batch(batch)
    if wanted == "approved" and counts["block"]:
        return _fail(f"{counts['block']} post(s) still have a blocking flag. "
                     "Those are the ones that could publish something the "
                     "client never authorized.")
    batch["status"] = wanted
    save_batch(batch)
    _log("batch_" + wanted, client=batch.get("client", ""),
         month=batch.get("month", ""), slots=counts["slots"])
    return jsonify({"ok": True, "batch": batch})


# =====================================================================
# Drafting — one request per slot
# =====================================================================
@app.route("/api/batches/<batch_id>/draft", methods=["POST"])
def api_draft(batch_id: str):
    """Write one slot. The browser loops so the loader can name what it is on
    and one failed slot costs one slot."""
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    slot_id = _str((request.get_json(silent=True) or {}).get("slot"), 12)
    slot = next((s for s in batch["slots"] if s["id"] == slot_id), None)
    if not slot:
        return _fail("Unknown slot.", 404)
    if slot.get("status") == "approved":
        return _fail("That post is approved — unapprove it before rewriting.")

    from hub import ai
    context = dict(batch.get("context") or {})
    context["client"] = batch.get("client", "")
    messages = social_plan.draft_messages(batch, slot, context)
    try:
        result = ai.chat_json(messages, module=MODULE,
                              purpose=f"social:{slot.get('type')}",
                              max_tokens=700, temperature=0.7)
    except Exception as exc:                          # noqa: BLE001
        # The provider's own wording never reaches the screen — it has echoed
        # key prefixes before. hub/ai.py already logged the real error.
        return _fail(f"Couldn't write that post ({type(exc).__name__}). The "
                     "other posts are unaffected — try this one again.", 502)

    copy = _str(result.get("copy"), 6000).strip()
    if not copy:
        return _fail("The model returned an empty post. Try again.", 502)
    tags = [_str(t, 60) for t in (result.get("hashtags") or []) if str(t).strip()]
    slot["copy"] = copy
    slot["hashtags"] = tags[:30]
    slot["status"] = "drafted"
    slot["flags"] = social_plan.validate_slot(slot, batch.get("brief"))
    save_batch(batch)
    return jsonify({"ok": True, "slot": slot})


# =====================================================================
# Images — gallery first
# =====================================================================
@app.route("/api/batches/<batch_id>/images", methods=["POST"])
def api_assign_images(batch_id: str):
    """Fill empty image slots from the client's existing gallery.

    Gallery-first because the expensive mistake is generating twenty images
    when eighteen already exist. Slots that already have an image are left
    alone, and the response says plainly how many are still without one rather
    than reporting a tidy success.
    """
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    images, note = _gallery_images(batch.get("client", ""))
    if not images:
        return jsonify({"ok": True, "assigned": 0, "remaining": sum(
            1 for s in batch["slots"] if not s.get("image_url")), "note": note})

    assigned = 0
    for slot in batch["slots"]:
        if slot.get("image_url"):
            continue
        # Round-robin rather than repeating the first photo: the same picture
        # on four consecutive posts is what an automated feed looks like.
        pick = images[assigned % len(images)]
        slot["image_url"] = pick["url"]
        slot["image_public_id"] = pick["public_id"]
        slot["image_source"] = "gallery"
        assigned += 1
    save_batch(batch)
    remaining = sum(1 for s in batch["slots"] if not s.get("image_url"))
    _log("images_assigned", client=batch.get("client", ""), count=assigned)
    return jsonify({"ok": True, "assigned": assigned, "remaining": remaining,
                    "pool": len(images), "batch": batch,
                    "note": f"{len(images)} image(s) in the gallery, reused "
                            f"across {assigned} post(s)." if assigned else note})


# =====================================================================
# Export
# =====================================================================
@app.route("/api/batches/<batch_id>/export.csv")
def api_export(batch_id: str):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    social_plan.validate_batch(batch)
    kind = _str(request.args.get("format", "planner"), 12)
    if kind == "review":
        body = social_plan.review_csv(batch)
        name = f"{batch.get('month', 'plan')}-review.csv"
    else:
        body = social_plan.planner_csv(batch)
        name = f"{batch.get('month', 'plan')}-social-planner.csv"
    slug = re.sub(r"[^a-z0-9]+", "-", batch.get("client", "client").lower()).strip("-")
    _log("exported", client=batch.get("client", ""), format=kind,
         month=batch.get("month", ""))
    return Response(body, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{slug}-{name}"'})
