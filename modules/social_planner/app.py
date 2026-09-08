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

    client  ->  selected ideas  ->  brief  ->  photos  ->  review / export

Each stage is separately re-runnable. Re-drafting one slot does not touch the
other nineteen, and nothing is destructive: drafting only fills slots that are
empty, and image assignment only fills slots with no image.

## What this deliberately does not do

**Pushing straight into Suite is real, and off by default.** `suite_client.py`
implements it — `push()`, `fetch()`, `performance()` — but it needs
`socialplanner/post.write` on the marketplace app, and HighLevel grants what
it recognises at consent and says nothing about the rest: a Suite connection
that reports Connected can still be missing this scope entirely. So every
screen asks `hub/suite_accounts.publishing()` first and offers the push button
only once that answers ready — never a button that fails at the moment
somebody is waiting on it. Until an agency re-consents, the CSV under Bulk
Upload is the route that works, and the whole drafting pipeline earns its keep
either way.

Photos can come from stock search, the client's gallery, a fresh upload, or
explicitly requested AI generation. New images are previewed before selection.

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
from zoneinfo import ZoneInfo
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


def _planning_today():
    return datetime.now(ZoneInfo("America/New_York")).date()


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
    """What the Hub already knows about this client — one shared reader.

    `hub/client_context.tool_context()`. This module and the GPT Ads Builder
    carried the same forty lines character for character, plus the same
    `_gallery_images` under them, which is the second copy CLAUDE.md names as
    a failure twice: the image-resize rule and the Pexels key each had to be
    found and fixed in more than one place, and the second one was missed.

    Moving it also gained the half neither copy had — the client's own site
    scan. Most of the local businesses this tool is used for publish no brand
    record anywhere, and their last Insites audit knows the logo, the palette
    their pages actually paint, the business name their site uses and their
    phone number.
    """
    from hub.client_context import tool_context
    return tool_context(client, url)


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
    boot = {"spec": social_plan.spec_payload(), "today": _planning_today().isoformat(),
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
        days = [h for h in social_plan.holidays_for(month, industries)
                if h["date"] >= _planning_today().isoformat()]
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


@app.route("/api/pages-review", methods=["POST"])
def api_pages_review():
    """Review the client's social pages — from what was measured, only.

    The harvested Pickaxe prompt's rubric, fed the per-platform data the
    Hub actually holds: the last site audit's social section
    (`scan_facts.social_snapshot`) and the profile URLs saved on the client
    record. The original Pickaxe listed four URLs and told the model to
    analyze them — pages a model can neither open nor, for Facebook, even
    fetch reliably from a server — so it invented reviews of pages it never
    saw. Here a platform with nothing measured is handed the line "no data
    could be retrieved" and the prompt tells the model to say it was not
    reviewed; a client with nothing measured on any platform is refused
    rather than billed for a page of "not reviewed".

    A POST behind a button: the call is billed, and a page load must not
    spend one.
    """
    data = request.get_json(silent=True) or {}
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    ctx = _client_context(client)
    domain = ctx.get("domain") or ""

    from hub import scan_facts
    snapshot = scan_facts.social_snapshot(domain) if domain else \
        {"found": False, "error": "", "platforms": [], "gbp": {}}
    saved = {}
    try:
        from hub import seo as hub_seo
        saved = hub_seo.get_social(client, domain) or {}
    except Exception:                                  # noqa: BLE001
        saved = {}

    lines, any_measured = [], False
    for row in snapshot.get("platforms") or []:
        label = row["platform"]
        saved_url = saved.pop(label.lower(), "")
        if row.get("name") or row.get("link"):
            any_measured = True
            bits = [f"{label}: {row.get('name') or row.get('link')}"]
            if row.get("link") and row.get("name"):
                bits.append(row["link"])
            bits.extend(row.get("details") or [])
            lines.append(" — ".join(bits))
        elif row.get("found") is False:
            any_measured = True
            lines.append(f"{label}: the last site audit found no page.")
        elif saved_url:
            lines.append(f"{label}: {saved_url} — no data could be retrieved.")
        else:
            lines.append(f"{label}: no data could be retrieved.")
    # Platforms only the client record knows (Pinterest, a custom link):
    # a URL with nothing measured behind it.
    for key, url in saved.items():
        lines.append(f"{key.title()}: {url} — no data could be retrieved.")

    gbp = snapshot.get("gbp") or {}
    if gbp.get("measured"):
        any_measured = True
        gbp_bits = []
        if gbp.get("found") is False:
            gbp_bits.append("no listing was found")
        else:
            if gbp.get("claimed") is False:
                gbp_bits.append("the listing is UNCLAIMED")
            if gbp.get("rating") is not None:
                gbp_bits.append(f"rating {gbp['rating']}")
            if gbp.get("reviews") is not None:
                gbp_bits.append(f"{gbp['reviews']} Google reviews")
        lines.append("Google Business Profile: "
                     + (", ".join(gbp_bits) or "found, nothing more measured"))

    if not any_measured:
        reason = snapshot.get("error") or (
            "no completed site audit holds social data for this client"
            if domain else "the client has no website on file to audit")
        return _fail("Nothing measured to review — " + reason + ". "
                     "Run a site audit first; a review of bare URLs is a "
                     "review of pages nobody looked at.")

    if snapshot.get("scanned_at"):
        lines.append(f"\n(Data from the site audit of {snapshot['scanned_at']}.)")

    from hub import ai as hub_ai, prompts_harvested as hp
    spec = hp.SOCIAL_PAGES_REVIEW
    prompt = spec["prompt"].format(client=client, pages_block="\n".join(lines))
    try:
        text = hub_ai.chat([{"role": "user", "content": prompt}],
                           module="social_planner", purpose=spec["purpose"],
                           temperature=spec["temperature"], max_tokens=1600)
    except hub_ai.AIUnavailable as exc:
        return _fail(f"The pages were read but the review could not be "
                     f"written: {exc}", 502)
    return jsonify({"ok": True, "review": text.strip(),
                    "pages_block": "\n".join(lines),
                    "scan_url": snapshot.get("scan_url", ""),
                    "note": "Reviewed from the last site audit's measurements "
                            "and the client record — not from opening the "
                            "pages live."})


@app.route("/api/calendar-draft", methods=["POST"])
def api_calendar_draft():
    """A month of content ideas as a brainstorm — never a plan.

    The month *builder* above is what creates posts: slots, copy checks,
    the client's tone and never-mention list, approval and the push. This
    is the step before it — a table of ideas a strategist reads while
    deciding what the month should even be — and it deliberately writes
    nothing: a second route that created slots would be a second
    description of what a post is, which is the two-proposal-builders
    failure. The note on the answer says so in words.

    A POST behind a button, because the call is billed.
    """
    data = request.get_json(silent=True) or {}
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    month = _str(data.get("month"), 10).strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        return _fail("Pick a month first.")
    channels = [c for c in (data.get("channels") or [])
                if c in social_plan.CHANNELS]
    if not channels:
        return _fail("Pick at least one channel.")
    focus = _str(data.get("focus"), 300).strip() or \
        "nothing specific was named — keep the mix balanced"
    ctx = _client_context(client)

    from hub import ai as hub_ai, prompts_harvested as hp
    spec = hp.CONTENT_CALENDAR
    prompt = spec["prompt"].format(
        client=client,
        industry=ctx.get("industry") or "not recorded",
        month=month, focus=focus, channels=", ".join(channels))
    try:
        text = hub_ai.chat([{"role": "user", "content": prompt}],
                           module="social_planner", purpose=spec["purpose"],
                           temperature=spec["temperature"], max_tokens=2200)
    except hub_ai.AIUnavailable as exc:
        return _fail(f"The ideas could not be written: {exc}", 502)
    return jsonify({"ok": True, "draft": text.strip(),
                    "note": "A brainstorm, not a plan — nothing here is "
                            "scheduled or saved. Build the month to create "
                            "real posts, with the copy checks on them."})


# =====================================================================
# Batches
# =====================================================================
@app.route("/api/batches")
def api_batches():
    return jsonify({"ok": True, "batches": _read_index()[:120]})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json(silent=True) or {}
    try:
        slots = social_plan.build_grid(_str(data.get("month"), 10),
                                      per_week=int(data.get("per_week") or 3),
                                      start_date=_planning_today())
    except (TypeError, ValueError) as exc:
        return _fail(str(exc))
    return jsonify(ok=True, dates=[s["date"] for s in slots],
                   today=_planning_today().isoformat())


@app.route("/api/batches/<batch_id>/photos", methods=["POST"])
def api_photos(batch_id):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("Plan not found.", 404)
    data = request.get_json(silent=True) or {}
    slot = next((s for s in batch["slots"] if s["id"] == data.get("slot")), None)
    if not slot:
        return _fail("Choose a post first.")
    from hub import stock_search
    query = _str(data.get("query"), 200).strip() or _str(
        slot.get("idea_title") or slot.get("holiday") or
        " ".join(batch.get("brief", {}).get("promote", [])) or
        batch.get("context", {}).get("industry") or batch["client"], 200)
    found = stock_search.search([query], sources=["library", "pexels", "pixabay"], limit=3)
    industry = _str(batch.get("context", {}).get("industry"), 100).strip()
    if not found["results"] and not data.get("query") and industry and industry != query:
        query = industry
        found = stock_search.search([query], sources=["library", "pexels", "pixabay"], limit=3)
    return jsonify(ok=True, query=query, **found)


@app.route("/api/batches/<batch_id>/photo", methods=["POST"])
def api_create_photo(batch_id):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("Plan not found.", 404)
    data = request.form if request.files else (request.get_json(silent=True) or {})
    slot = next((s for s in batch["slots"] if s["id"] == data.get("slot")), None)
    if not slot:
        return _fail("Choose a post first.")
    from hub import images, storage
    upload = request.files.get("file")
    source = "uploaded" if upload else "generated"
    if upload:
        raw = upload.read(12 * 1024 * 1024 + 1)
        if len(raw) > 12 * 1024 * 1024:
            return _fail("Choose an image under 12 MB.")
    else:
        prompt = _str(data.get("prompt"), 1000).strip()
        if not prompt:
            return _fail("Describe the photo to create.")
        from hub import ai
        try:
            raw = ai.image("Create a social media photograph. No lettering or logos. " + prompt,
                           module=MODULE, purpose="social:photo", size="1024x1024")
        except Exception:
            return _fail("Image generation is unavailable. Try again or choose another source.", 502)
    try:
        processed = images.optimise(raw, max_edge=2048, fmt="JPEG", quality=92)
    except Exception:
        return _fail("That image could not be read. Use a PNG, JPEG or WebP photo.")
    filename = f"{batch_id}-{slot['id']}.jpg"
    try:
        asset = storage.put("social_requests", filename, processed.data,
                            client=batch["client"], tags=["social-planner", source])
    except Exception:
        return _fail("The photo could not be stored. Please retry.", 502)
    if not asset.url.startswith("https://"):
        return _fail("Image hosting is unavailable. Please try again when storage is connected.", 503)
    if source == "uploaded":
        _file_into_gallery(batch["client"], asset, filename, "")
    return jsonify(ok=True, image={"url": asset.url, "public_id": asset.public_id,
                                   "source": source})


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
                                       blackout=blackout, holidays=holidays,
                                       start_date=_planning_today())
    except ValueError as exc:
        return _fail(str(exc))
    if not slots:
        return _fail("No posting days remain from today onward. Choose a later month or a different posting frequency.")

    selected = data.get("selected_ideas") or []
    if not isinstance(selected, list):
        return _fail("Choose ideas from the suggestions.")
    selected = [s for s in selected if isinstance(s, dict) and s.get("title")]
    available = [s for s in slots if not s.get("holiday")]
    if len(selected) > len(available):
        return _fail(f"There are {len(available)} open posting dates. Select fewer ideas or increase posts per week.")
    for slot, idea in zip(available, selected):
        slot["idea_title"] = _str(idea["title"], 300)
        slot["origin"] = "staff_selected"
        if idea.get("type") in social_plan.POST_TYPES:
            slot["type"] = idea["type"]

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
            source = edit.get("image_source", "gallery")
            slot["image_source"] = source if slot["image_url"] and source in ("gallery", "stock", "uploaded", "generated") else ""
            slot["image_credit"] = _str(edit.get("image_credit"), 500) if slot["image_url"] else ""
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
    from hub.client_context import gallery_images
    images, note = gallery_images(batch.get("client", ""))
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


# =====================================================================
# Client intake, ideas and the Suite push
#
# Everything below is the layer *around* a post: where the ask came from, who
# asked, what a client is being offered to swipe on, and what has to be true
# before anything reaches Smart 1 Suite. `hub/social_content.py` is the spec
# it reads; this file is the routes.
#
# It lives in this module rather than in a `modules/social_content` beside it
# because a second module is a second description of what a post is. The
# Proposal Builder was two tools for one job for a year and the same client
# got quoted two ways depending on which one a rep opened. A month drafted by
# a strategist, an idea a client liked and a photograph a location manager
# sent in all converge on the same slot, in the same batch, going out through
# the same export or the same push.
# =====================================================================
from hub import social_content                                    # noqa: E402
from . import agent, ideas, intake, links, suite_client           # noqa: E402

# /c/ is the client's half: four pages reached with a signed token and no Hub
# login. wsgi.py hands this to BOTH the AuthGuard (so a client can open them)
# and HubBar (so the staff sidebar, help layer and feedback tab are not
# injected into a page a client reads). One list, read from the module, so the
# mount and the module can never disagree about what is public — the same
# arrangement modules/scans and modules/ads_builder use.
PUBLIC_PREFIXES = ("/c/",)

# The client's own copy of a plan only ever shows posts that have been put to
# them. A slot a strategist is still writing is not a decision anybody is
# waiting on, and showing it invites a change request on a draft.
CLIENT_VISIBLE = ("pending_client_approval", "changes_requested", "approved")


def _slot_of(batch: dict, slot_id: str) -> dict | None:
    return next((s for s in (batch.get("slots") or []) if s.get("id") == slot_id), None)


def _client_of_request(row: dict) -> tuple[str, str]:
    return str(row.get("client") or ""), str(row.get("client_url") or "")


# ---------------------------------------------------------------- staff pages
@app.route("/requests")
def page_requests():
    boot = {"client": request.args.get("client", "")[:200],
            "url": request.args.get("url", "")[:300],
            "spec": {
                "types": social_content.REQUEST_TYPES,
                "statuses": social_content.REQUEST_STATUSES,
                "flow": list(social_content.REQUEST_FLOW),
                "dateModes": social_content.DATE_MODES,
                "tags": social_content.IDEA_TAGS,
                "guardrails": list(social_content.GUARDRAILS),
                "duplicateWindow": social_content.duplicate_window_days(),
            },
            "waiting": intake.clients_with_open_requests()[:40]}
    return render_template("staff_requests.html", version=_version(), boot=boot)


@app.route("/api/requests")
def api_requests():
    client = _str(request.args.get("client", ""), 200).strip()
    if not client:
        # Deliberately not "no requests": a screen with no client picked has
        # asked nothing, and answering it with an empty queue reads as an
        # answer about a client.
        return jsonify({"ok": True, "picked": False, "requests": [],
                        "waiting": intake.clients_with_open_requests()[:40]})
    url = _str(request.args.get("url", ""), 300).strip()
    status = _str(request.args.get("status", ""), 20).strip()
    statuses = (status,) if status in social_content.REQUEST_STATUSES else None
    rows = intake.for_client(client, url, statuses=statuses)
    return jsonify({
        "ok": True, "picked": True, "client": client, "url": url,
        "requests": rows,
        "summary": intake.summary(client, url),
        "locations": intake.locations(client, url),
        "links": links.all_links(client, url, request.host_url),
        "link_revoked": links.is_revoked(client),
        "suite": suite_client.preflight(client, url),
    })


@app.route("/api/requests", methods=["POST"])
def api_create_request():
    """Staff filing a request on somebody's behalf — a phone call, a forwarded
    email. It goes through the same door as the client's own form so the queue
    holds one kind of row, and `source` says which it was."""
    data = request.get_json(silent=True) or {}
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    try:
        row = intake.submit(client, _str(data.get("url"), 300),
                            payload=data, source="staff")
    except ValueError as exc:
        return _fail(str(exc))
    _log("request_received", client=client, source="staff",
         location=row.get("location_label", ""))
    return jsonify({"ok": True, "request": row})


@app.route("/api/requests/<req_id>/triage", methods=["POST"])
def api_triage_request(req_id: str):
    row = intake.mark_triaged(req_id, actor_name())
    if not row:
        return _fail("That request no longer exists.", 404)
    return jsonify({"ok": True, "request": intake.decorate([row])[0]})


@app.route("/api/requests/<req_id>/decline", methods=["POST"])
def api_decline_request(req_id: str):
    reason = _str((request.get_json(silent=True) or {}).get("reason"), 600)
    try:
        row = intake.decline(req_id, reason, actor_name())
    except ValueError as exc:
        return _fail(str(exc))
    if not row:
        return _fail("That request no longer exists.", 404)
    _log("request_declined", client=row.get("client", ""), request=req_id)
    return jsonify({"ok": True, "request": intake.decorate([row])[0]})


@app.route("/api/requests/<req_id>/duplicate", methods=["POST"])
def api_duplicate_request(req_id: str):
    """Confirm a flagged duplicate, or clear the flag. Never automatic: two
    locations wanting the same week is as often two real asks as one ask
    twice, and nothing here can tell them apart."""
    of_id = _str((request.get_json(silent=True) or {}).get("of"), 40)
    row = intake.mark_duplicate(req_id, of_id, actor_name())
    if not row:
        return _fail("That request no longer exists.", 404)
    return jsonify({"ok": True, "request": intake.decorate([row])[0]})


@app.route("/api/requests/<req_id>/promote", methods=["POST"])
def api_promote_request(req_id: str):
    """Turn a request into a slot on a plan, carrying everything it came with.

    Two things this deliberately will not do. It will not **invent a plan**:
    with no month built for this client it refuses and names the step, because
    a batch conjured to hold one request is a month nobody chose the channels
    or the mix for. And it will not silently **overwrite a slot** that already
    has copy on it — a request is added as its own slot, so promoting two
    requests for the same week produces two posts to look at rather than one
    that quietly replaced the other.
    """
    row = intake.get(req_id)
    if not row:
        return _fail("That request no longer exists.", 404)
    client, url = _client_of_request(row)
    data = request.get_json(silent=True) or {}

    batch_id = _str(data.get("batch"), 40)
    batch = load_batch(batch_id) if batch_id else None
    if not batch:
        mine = [r for r in _read_index()
                if str(r.get("client") or "").strip().lower() == client.strip().lower()
                and r.get("status") != "approved"]
        batch = load_batch(mine[0]["id"]) if mine else None
    if not batch:
        return _fail("There is no open plan for this client to promote into. "
                     "Build the month first — the channels and the post mix "
                     "are decisions that belong to the plan, not to one "
                     "request.", 409)

    when = row.get("requested_date_start") or ""
    if not when:
        # ASAP. The next unwritten slot's date is the honest answer, and the
        # strategist can move it; inventing "today" would put it in the past
        # by the time anybody looks.
        empty = [s for s in batch["slots"] if s.get("status") == "empty"]
        when = (empty[0]["date"] if empty
                else (batch["slots"][-1]["date"] if batch["slots"] else ""))

    existing = [s["id"] for s in batch["slots"] if str(s.get("id", "")).startswith("r")]
    slot = {
        "id": f"r{len(existing) + 1:02d}",
        "date": when,
        "time": social_plan.POST_TIMES[len(batch["slots"]) % len(social_plan.POST_TIMES)],
        "channels": list(batch.get("channels") or []),
        "type": "promo" if row.get("request_type") in ("promo", "event") else "announcement",
        "copy": _str(row.get("copy_suggestion"), 6000),
        "hashtags": [], "link": "",
        "image_url": "", "image_public_id": "",
        "image_source": "client_upload" if row.get("asset_refs") else "",
        "holiday": "",
        "status": "edited" if row.get("copy_suggestion") else "empty",
        "flags": [],
        # Traceability back to the ask, both ways. Without it the request is a
        # dead form entry and somebody re-reads the whole queue to work out
        # which ones were done.
        "origin": "client_requested",
        "source_request_id": req_id,
        "source_location": row.get("location_label", ""),
        # What a person at the client typed. Read by validate_slot as
        # authorization, or the checks block the client's own offer.
        "supplied": social_content.authorized_text(row),
        "delivery": "", "delivery_error": "", "ghl_post_id": "",
    }
    assets = row.get("asset_refs") or []
    if assets:
        slot["image_url"] = assets[0]
    if slot["type"] not in social_plan.POST_TYPES:
        slot["type"] = "announcement"

    batch["slots"].append(slot)
    save_batch(batch)
    intake.link_post(req_id, batch["id"], slot["id"], actor_name())
    _log("request_promoted", client=client, request=req_id, batch=batch["id"])
    return jsonify({"ok": True, "batch": batch, "slot": slot,
                    "request": intake.decorate([intake.get(req_id)])[0]})


# ---------------------------------------------------------------- locations
@app.route("/api/locations")
def api_locations():
    client = _str(request.args.get("client", ""), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    url = _str(request.args.get("url", ""), 300).strip()
    return jsonify({"ok": True,
                    "locations": intake.locations(client, url, include_inactive=True)})


@app.route("/api/locations", methods=["POST"])
def api_add_location():
    data = request.get_json(silent=True) or {}
    try:
        row = intake.add_location(
            _str(data.get("client"), 200), _str(data.get("url"), 300),
            name=_str(data.get("name"), 120),
            contact_name=_str(data.get("contact_name"), 120),
            contact_email=_str(data.get("contact_email"), 200),
            contact_phone=_str(data.get("contact_phone"), 40),
            address=_str(data.get("address"), 300), actor=actor_name())
    except ValueError as exc:
        return _fail(str(exc))
    return jsonify({"ok": True, "location": row})


@app.route("/api/locations/<loc_id>", methods=["PUT"])
def api_update_location(loc_id: str):
    data = request.get_json(silent=True) or {}
    row = intake.update_location(loc_id, **{k: v for k, v in data.items()
                                            if k in ("name", "contact_name",
                                                     "contact_email",
                                                     "contact_phone",
                                                     "address", "active")})
    if not row:
        return _fail("That location no longer exists.", 404)
    return jsonify({"ok": True, "location": row})


# ---------------------------------------------------------------- client links
@app.route("/api/client-link", methods=["POST"])
def api_client_link():
    """Turn a client's link off, or back on.

    There is one link per client account and it is derived rather than stored,
    so this is the only writing this feature does. Revoking is rare and
    deliberate — the link is on somebody's intranet page — so it is a POST and
    it says what it costs.
    """
    data = request.get_json(silent=True) or {}
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    if data.get("revoked"):
        links.revoke(client, actor_name())
    else:
        links.restore(client)
    _log("client_link", client=client, revoked=bool(data.get("revoked")))
    return jsonify({"ok": True, "revoked": links.is_revoked(client),
                    "links": links.all_links(client, _str(data.get("url"), 300),
                                             request.host_url)})


# ---------------------------------------------------------------- ideas (staff)
@app.route("/api/ideas")
def api_ideas():
    client = _str(request.args.get("client", ""), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    url = _str(request.args.get("url", ""), 300).strip()
    return jsonify({"ok": True,
                    "ideas": ideas.for_client(client, url),
                    "weights": ideas.weight_table(client, url),
                    "preferences": ideas.preferences(client, url),
                    "explore_ratio": social_content.explore_ratio(),
                    "batch_size": social_content.batch_size()})


@app.route("/api/ideas", methods=["POST"])
def api_add_idea():
    data = request.get_json(silent=True) or {}
    try:
        row = ideas.add(_str(data.get("client"), 200), _str(data.get("url"), 300),
                        title=_str(data.get("title"), 300),
                        idea_tag=_str(data.get("idea_tag"), 40),
                        origin="staff")
    except ValueError as exc:
        return _fail(str(exc))
    return jsonify({"ok": True, "idea": row})


@app.route("/api/ideas/generate", methods=["POST"])
def api_generate_ideas():
    data = request.get_json(silent=True) or {}
    client = _str(data.get("client"), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    url = _str(data.get("url"), 300).strip()
    context = _client_context(client, url)
    context["client"] = client
    result = ideas.generate(client, url, context=context,
                            extra_tags=agent.idea_tags_for(client, url))
    _log("ideas_generated", client=client, count=len(result["ideas"]),
         source=result["source"])
    return jsonify({"ok": True, **result})


@app.route("/api/ideas/<idea_id>/promote", methods=["POST"])
def api_promote_idea(idea_id: str):
    """A liked idea becomes a slot, exactly as a triaged request does.

    Staff-reviewed on purpose, which is open item 6 in the spec answered the
    way this codebase answers it everywhere else: a client liking a one-line
    title has not approved a post, and promoting automatically would put copy
    nobody has read in front of them under the heading "approve this".
    """
    row = ideas.get(idea_id)
    if not row:
        return _fail("That idea no longer exists.", 404)
    data = request.get_json(silent=True) or {}
    batch = load_batch(_str(data.get("batch"), 40))
    if not batch:
        return _fail("Pick the plan to promote it into.", 409)
    slot_id = _str(data.get("slot"), 20)
    slot = _slot_of(batch, slot_id)
    if not slot:
        return _fail("Unknown slot.", 404)
    if slot.get("copy", "").strip():
        return _fail("That slot already has copy in it. Pick an empty one — "
                     "an idea landing on written copy would replace work "
                     "somebody has done.")
    slot["idea_title"] = _str(row.get("title"), 300)
    slot["origin"] = "client_idea" if row.get("client_response") == "liked" else "agent_suggested"
    slot["source_idea_id"] = idea_id
    if row.get("idea_tag") in social_plan.POST_TYPES:
        slot["type"] = row["idea_tag"]
    save_batch(batch)
    ideas.mark_promoted(idea_id, batch["id"], slot_id)
    return jsonify({"ok": True, "batch": batch, "slot": slot})


# ---------------------------------------------------------------- the agent
@app.route("/api/agent")
def api_agent():
    client = _str(request.args.get("client", ""), 200).strip()
    if not client:
        return _fail("Pick a client first.")
    url = _str(request.args.get("url", ""), 300).strip()
    data = agent.signals(client, url)
    return jsonify({"ok": True, "signals": data,
                    "notes": agent.notes(client, url, data),
                    "what_worked": agent.what_worked(client, url)})


# ---------------------------------------------------------------- the push
@app.route("/api/batches/<batch_id>/suite")
def api_suite_state(batch_id: str):
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    return jsonify({"ok": True,
                    "suite": suite_client.preflight(batch.get("client", ""),
                                                    batch.get("url", ""))})


@app.route("/api/batches/<batch_id>/push", methods=["POST"])
def api_push(batch_id: str):
    """Push one approved post into this client's Social Planner.

    One at a time and never a whole month: a loop that pushes twenty and fails
    on the eleventh leaves a person working out which ten landed, and the one
    thing that must never happen here is posting something twice on a client's
    own page.

    A failed push leaves the post **approved**, with the error on it, and the
    retry is this same button — never automatic, because a flaky response is
    precisely the case where the write may well have landed.
    """
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    slot = _slot_of(batch, _str((request.get_json(silent=True) or {}).get("slot"), 20))
    if not slot:
        return _fail("Unknown slot.", 404)
    if slot.get("status") != "approved":
        return _fail("Only an approved post is pushed. Approve it first.")
    if slot.get("ghl_post_id"):
        return _fail("That post is already in Social Planner. Pushing it again "
                     "would post it twice — edit it there instead.", 409)
    if [f for f in (slot.get("flags") or []) if f.get("level") == "block"]:
        return _fail("That post still has a blocking flag on it. Those are the "
                     "ones that could publish something the client never "
                     "authorized.")

    result = suite_client.push(batch, slot, batch.get("client", ""),
                               batch.get("url", ""))
    suite_client.apply_push_result(slot, result)
    save_batch(batch)
    if slot.get("source_request_id"):
        intake.sync_from_post(slot["source_request_id"], slot.get("delivery", ""))
    _log("post_pushed" if result.get("ok") else "post_push_failed",
         client=batch.get("client", ""), batch=batch_id, slot=slot["id"])
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", ""),
                        "blocked_by": result.get("blocked_by", ""),
                        "slot": slot}), 502
    return jsonify({"ok": True, "slot": slot, "unmapped": result.get("unmapped") or []})


@app.route("/api/batches/<batch_id>/push-status", methods=["POST"])
def api_push_status(batch_id: str):
    """Read one pushed post back and write what Suite says onto the slot.

    Writing it here rather than leaving it to the browser is the Commercial
    Builder's HeyGen rule: a job whose only observer was a tab somebody closed
    is a job whose result is lost.
    """
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    slot = _slot_of(batch, _str((request.get_json(silent=True) or {}).get("slot"), 20))
    if not slot or not slot.get("ghl_post_id"):
        return _fail("That post has not been pushed.", 404)
    result = suite_client.fetch(slot["ghl_post_id"], batch.get("client", ""),
                                batch.get("url", ""))
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "")}), 502
    slot["delivery"] = result["status"]
    save_batch(batch)
    if slot.get("source_request_id"):
        intake.sync_from_post(slot["source_request_id"], slot["delivery"])
    return jsonify({"ok": True, "slot": slot})


# =====================================================================
# The client's half — signed token, no login
#
# Four pages, one link. Everything here is served to somebody who has no Hub
# account, so three rules hold across all of it:
#
#   * **Revoked, deleted and never-existed answer the same page.** A
#     client-facing URL that says "this one expired" tells whoever is probing
#     which tokens are real. modules/ads_builder settled this.
#   * **Nothing staff-facing leaks in.** No client names but their own, no
#     flags, no internal notes, no version tag, no sidebar — /c/ is in
#     PUBLIC_PREFIXES, which wsgi.py hands to HubBar as well as to AuthGuard.
#   * **A client can only ever write about their own client.** The token *is*
#     the client, so no route below takes a client name from the request body.
# =====================================================================
def _client_page(token: str):
    found = links.client_for(token)
    if not found:
        return None, (render_template("client_gone.html"), 404)
    return found, None


@app.route("/c/<token>/request")
def page_client_request(token: str):
    found, gone = _client_page(token)
    if gone:
        return gone
    client, url = found
    boot = {"client": client, "token": token,
            "locations": [{"id": r["id"], "name": r["name"]}
                          for r in intake.locations(client, url)],
            "types": social_content.REQUEST_TYPES,
            "dateModes": social_content.DATE_MODES,
            # Measured or not promised. Open item 1, answered the way this
            # codebase answers every "shall we quote a number nobody has
            # checked" question.
            "turnaround": social_content.turnaround_note(
                intake.for_client(client, url))}
    return render_template("client_request.html", boot=boot, client=client,
                           token=token, pages=links.PAGES)


@app.route("/c/<token>/request", methods=["POST"])
def api_client_request(token: str):
    found = links.client_for(token)
    if not found:
        return _fail("This link is no longer active.", 404)
    client, url = found
    data = request.get_json(silent=True) or {}
    if not _str(data.get("request_type"), 40):
        return _fail("Tell us what this is about.")
    try:
        row = intake.submit(client, url, payload=data, source="client_link")
    except ValueError as exc:
        return _fail(str(exc))
    _log("request_received", client=client, source="client_link",
         location=row.get("location_label", ""))

    # Every request is also a prospect's contact detail arriving, but this one
    # is an existing client's staff member — so it is deliberately NOT written
    # through hub/leads.py. A location manager is not a lead, and filing them
    # as one puts a live client into the sales panel as a new business.
    return jsonify({"ok": True, "id": row["id"],
                    "location": row.get("location_label", ""),
                    "turnaround": social_content.turnaround_note(
                        intake.for_client(client, url))})


@app.route("/c/<token>/upload", methods=["POST"])
def api_client_upload(token: str):
    """Photographs and video, straight to Cloudinary through hub/storage.py.

    Through the shared uploader rather than a local `cloudinary.uploader`
    call: this module would otherwise be the sixteenth to configure Cloudinary
    itself, which is the migration CLAUDE.md asks for whenever a module is
    being edited anyway.
    """
    found = links.client_for(token)
    if not found:
        return _fail("This link is no longer active.", 404)
    client, _url = found
    files = request.files.getlist("file")
    if not files:
        return _fail("No file arrived.")
    from hub import storage
    where = _str((request.form or {}).get("location_label"), 120)
    stored, failed, unfiled = [], [], 0
    for item in files[:10]:
        try:
            asset = storage.put("social_requests", item.filename or "upload",
                                item.read(), client=client,
                                tags=["social-request"])
        except Exception as exc:                          # noqa: BLE001
            # Named, never counted. "Four of five went up" is a different
            # answer from "five went up", and only the client can re-send the
            # fifth.
            failed.append({"name": item.filename or "file",
                           "why": type(exc).__name__})
            continue
        stored.append({"url": asset.url, "public_id": asset.public_id})
        if not _file_into_gallery(client, asset, item.filename or "", where):
            unfiled += 1

    if not stored and failed:
        return _fail("None of those would upload. Try again, or send them to "
                     "us the usual way.", 502)
    return jsonify({"ok": True, "assets": stored, "failed": failed,
                    # The client never sees this — their photograph arrived
                    # either way. It is on the answer so the staff queue and
                    # the tests can tell a stored-but-unfiled photo from a
                    # filed one, which is the whole difference between the
                    # composer being able to offer it and not.
                    "unfiled": unfiled})


def _file_into_gallery(client: str, asset, filename: str, where: str) -> bool:
    """Put a client's own photograph into their gallery, not only in Cloudinary.

    This is the half §5 of the spec is actually about. `storage.put()` stores
    the bytes; the *composer* — and Image Creator, and every other tool that
    offers "the client's own assets first" — reads
    `client_context.gallery_images()`, which reads the image picker's gallery.
    A photograph that went to Cloudinary and not to the gallery is one the
    tool built to prefer it cannot see, while the client has been told it
    arrived. Nothing errors at either end.

    Reported **separately** from the upload rather than folded into it, the
    rule `hub/domain_links.py` gives: "stored" and "stored in one of two
    places" are different outcomes, and one tick for both is how somebody
    learns not to trust the tick.

    The gallery row is created where a client has none. That is a deliberate
    exception to `provisioning.py`'s "creating is asked for, not assumed" —
    there the question is whether a link should exist, and here there are
    already bytes from a named client on a link they were sent. Refusing
    would lose the photograph, which is the one outcome worse than an extra
    empty gallery.

    Never raises: the upload has already succeeded and the client is watching.
    """
    try:
        from modules.image_picker.filing import file_asset
    except Exception:                                     # noqa: BLE001
        return False
    try:
        label = "Sent in by the client"
        if where:
            label += f" — {where}"
        result = file_asset(
            client_name=client,
            public_id=getattr(asset, "public_id", "") or "",
            url=getattr(asset, "url", "") or "",
            kind="client_upload", provider="social_request",
            label=label, filename=filename[:200],
            resource_type=getattr(asset, "resource_type", "") or "image",
            saved_by="social request", create_client=True,
            # Not pushed to the Suite media library: this is a photograph
            # somebody sent us to consider, not an approved asset, and the
            # client's own media library is where approved work goes.
            push_to_suite=False)
        return bool(result.get("ok"))
    except Exception:                                     # noqa: BLE001
        return False


@app.route("/c/<token>/ideas")
def page_client_ideas(token: str):
    found, gone = _client_page(token)
    if gone:
        return gone
    client, url = found
    cards = ideas.pending(client, url, limit=12)
    boot = {"client": client, "token": token,
            "ideas": [{"id": r["id"], "title": r["title"],
                       "tag": social_content.idea_tag_label(r.get("idea_tag"))}
                      for r in cards]}
    return render_template("client_ideas.html", boot=boot, client=client,
                           token=token, pages=links.PAGES)


@app.route("/c/<token>/ideas/<idea_id>/respond", methods=["POST"])
def api_client_idea_respond(token: str, idea_id: str):
    found = links.client_for(token)
    if not found:
        return _fail("This link is no longer active.", 404)
    client, url = found
    row = ideas.get(idea_id)
    # The token is the client, so an idea belonging to somebody else is a 404
    # rather than a refusal that confirms the id exists.
    if not row or not links.token_for(row.get("client", ""), row.get("client_url", "")):
        return _fail("That idea is no longer there.", 404)
    from hub.client_key import same_client
    if not same_client(client, url, row.get("client", ""), row.get("client_url", "")):
        return _fail("That idea is no longer there.", 404)
    response = _str((request.get_json(silent=True) or {}).get("response"), 10)
    try:
        updated = ideas.respond(idea_id, response)
    except ValueError as exc:
        return _fail(str(exc))
    if not updated:
        return _fail("That idea is no longer there.", 404)
    return jsonify({"ok": True, "id": idea_id,
                    "response": updated.get("client_response")})


@app.route("/c/<token>/approve")
def page_client_approve(token: str):
    found, gone = _client_page(token)
    if gone:
        return gone
    client, url = found
    boot = {"client": client, "token": token, "posts": _posts_for_client(client)}
    return render_template("client_approve.html", boot=boot, client=client,
                           token=token, pages=links.PAGES)


def _posts_for_client(client: str) -> list[dict]:
    """The posts actually put to this client, and only what they need to see.

    A subset a renderer merely happens to omit is one the next renderer
    prints, so the shape is built here rather than left to a template: no
    flags, no internal notes, no strategist's name, no other client's
    anything. `modules/scans` strips its audit the same way for the same
    reason.
    """
    out = []
    for row in _read_index():
        if str(row.get("client") or "").strip().lower() != client.strip().lower():
            continue
        batch = load_batch(row["id"])
        if not batch:
            continue
        for slot in batch.get("slots") or []:
            if slot.get("client_state") not in CLIENT_VISIBLE:
                continue
            out.append({
                "batch": batch["id"], "slot": slot["id"],
                "date": slot.get("date", ""), "time": slot.get("time", ""),
                "channels": [social_plan.channel_label(c)
                             for c in (slot.get("channels") or [])],
                "copy": social_plan.post_text(slot),
                "image_url": slot.get("image_url", ""),
                "state": slot.get("client_state", ""),
                "note": slot.get("client_note", ""),
                "from_request": bool(slot.get("source_request_id")),
                "location": slot.get("source_location", ""),
            })
    out.sort(key=lambda r: (r["state"] != "pending_client_approval",
                            r["date"], r["time"]))
    return out


@app.route("/c/<token>/approve/<batch_id>/<slot_id>", methods=["POST"])
def api_client_approve(token: str, batch_id: str, slot_id: str):
    """Approve, ask for a change, or skip.

    Three answers rather than two, the rule `modules/ads_builder/spec.py`
    arrived at: "yes", "yes with my changes" and "not this one" are the three
    real replies, and an approve/reject pair forces the middle one into
    whichever end is nearest. A change request **needs the words** — "the
    client wants it different" is not actionable.
    """
    found = links.client_for(token)
    if not found:
        return _fail("This link is no longer active.", 404)
    client, _url = found
    batch = load_batch(batch_id)
    if not batch or str(batch.get("client") or "").strip().lower() != client.strip().lower():
        return _fail("That post is no longer there.", 404)
    slot = _slot_of(batch, slot_id)
    if not slot or slot.get("client_state") not in CLIENT_VISIBLE:
        return _fail("That post is no longer there.", 404)

    data = request.get_json(silent=True) or {}
    decision = _str(data.get("decision"), 20)
    note = _str(data.get("note"), 2000).strip()
    if decision == "approved":
        slot["client_state"] = "approved"
        slot["client_note"] = ""
    elif decision == "changes_requested":
        if not note:
            return _fail("Tell us what to change — one line is plenty. "
                         "Without it we would be guessing.")
        slot["client_state"] = "changes_requested"
        slot["client_note"] = note
        # A post the client has asked to change is not an approved post any
        # more, whatever a strategist had marked it. Leaving the status alone
        # would let it be pushed while the change request sat unread.
        slot["status"] = "edited"
    elif decision == "rejected":
        slot["client_state"] = "rejected"
        slot["client_note"] = note
        slot["status"] = "edited"
    else:
        return _fail("Unknown answer.")
    slot["client_answered_at"] = _now()
    save_batch(batch)
    if slot.get("source_request_id") and decision == "approved":
        intake.sync_from_post(slot["source_request_id"], "approved")
    _log("client_" + decision, client=client, batch=batch_id, slot=slot_id)
    return jsonify({"ok": True, "state": slot["client_state"],
                    "posts": _posts_for_client(client)})


@app.route("/c/<token>/preferences")
def page_client_preferences(token: str):
    found, gone = _client_page(token)
    if gone:
        return gone
    client, url = found
    prefs = ideas.preferences(client, url)
    boot = {"client": client, "token": token, "preferences": prefs,
            "tags": {k: v["label"] for k, v in social_content.IDEA_TAGS.items()}}
    return render_template("client_preferences.html", boot=boot, client=client,
                           token=token, pages=links.PAGES)


@app.route("/c/<token>/preferences", methods=["POST"])
def api_client_preferences(token: str):
    found = links.client_for(token)
    if not found:
        return _fail("This link is no longer active.", 404)
    client, url = found
    data = request.get_json(silent=True) or {}
    prefs = ideas.save_preferences(
        client, url,
        topics_wanted=[t for t in (data.get("topics_wanted") or [])],
        topics_avoid=_str(data.get("topics_avoid"), 1000),
        tone=_str(data.get("tone"), 300),
        standing_notes=_str(data.get("standing_notes"), 2000))
    _log("preferences_saved", client=client)
    return jsonify({"ok": True, "preferences": prefs})


# ---------------------------------------------------------------- staff: send
@app.route("/api/batches/<batch_id>/send", methods=["POST"])
def api_send_to_client(batch_id: str):
    """Put drafted posts in front of the client.

    Only slots that have copy and no blocking flag go: a blocking flag is a
    claim nobody authorized, and asking a client to approve one is asking them
    to authorize it after the fact. What was held back is **counted and
    named** rather than quietly skipped — a send that reports success over
    nine of twelve posts is one nobody can tell from a complete one.
    """
    batch = load_batch(batch_id)
    if not batch:
        return _fail("That plan no longer exists.", 404)
    social_plan.validate_batch(batch)
    sent = held = empty = 0
    for slot in batch.get("slots") or []:
        if slot.get("client_state") in CLIENT_VISIBLE:
            continue
        if not slot.get("copy", "").strip():
            empty += 1
            continue
        if [f for f in (slot.get("flags") or []) if f.get("level") == "block"]:
            held += 1
            continue
        slot["client_state"] = "pending_client_approval"
        sent += 1
    save_batch(batch)
    _log("sent_to_client", client=batch.get("client", ""), batch=batch_id,
         sent=sent, held=held)
    link = links.link(batch.get("client", ""), batch.get("url", ""), "approve",
                      request.host_url)
    return jsonify({"ok": True, "sent": sent, "held": held, "empty": empty,
                    "link": link,
                    "note": (f"{sent} post(s) are now with the client."
                             + (f" {held} were held back for a blocking flag."
                                if held else "")
                             + (f" {empty} have no copy yet." if empty else ""))})
