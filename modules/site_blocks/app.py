"""Smart 1 Hub — Website Block Builder.

Builds the HTML for a landing page section on smart1marketing.com, in the
house style, and hands it over as something a rep can paste.

## Why this is a tool rather than a folder of files

The industry landing pages (`/land/hvac`, `/land/boat`, `/land/ski` and the
rest) are the look we want more of, and until now the way to get another one
was to copy one of those files and edit it. That works exactly once. The
second copy drifts, the third is a different company's website, and none of
them can be updated together because the design is spread across nine hand-
written files. `blocks.py` holds the design once; this app is the way in.

## What comes out

Three shapes, and the choice is the rep's:

* **One block** — a `<style>` and a `<section>`, self-contained, for a Custom
  HTML element in the website builder. Paste as many as you like; the
  stylesheet is identical each time and repeating it changes nothing.
* **All blocks** — the same sections with the stylesheet emitted once, for a
  page being assembled in one editor.
* **A whole page** — a standalone `<!doctype html>` document with the Smart 1
  nav and footer, for a page that will live on its own URL.

Nothing here writes to the website. The output is copied or downloaded, which
keeps the publishing decision with a person.

## The trap this module is shaped around

The Hub's chrome — the sidebar, the help layer, the feedback tab — is injected
into HTML responses. That is right for this builder, which is a staff page,
and wrong for the HTML it produces. So the generated HTML never leaves through
a route that returns `text/html`: `/api/render` answers JSON with the markup as
a string, and `/api/download` sends a file attachment. The preview is drawn
into an iframe by the browser from that JSON, so what the rep looks at is
exactly what they will paste, sidebar-free, without needing a chromeless route
at all.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from hub import jsonstore

from . import blocks as B

try:
    from hub import audit as hub_audit
except Exception:                                     # noqa: BLE001
    hub_audit = None

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

STORE = "pages.json"
MODULE = "site_blocks"


# ------------------------------------------------------------------ storage
def _path() -> str:
    # jsonstore.data_dir, not os.environ.get("HUB_DATA_DIR", "data"): that
    # spelling resolves to ./data inside the container and is wiped on every
    # deploy. Saved pages are the only copy of a rep's work here.
    return str(Path(jsonstore.data_dir(MODULE)) / STORE)


def _load() -> list[dict]:
    rows = jsonstore.read_json(_path(), default=[])
    return rows if isinstance(rows, list) else []


def _save(rows: list[dict]) -> None:
    jsonstore.write_json(_path(), rows)


def _actor() -> str:
    return request.environ.get("s1hub.user") or ""


def _log(type_: str, **extra) -> None:
    # audit.log's first positional is `module`. Passing module= in the extras
    # raises TypeError and silently zeroes cost tracking -- the tool= spelling
    # is the one that works.
    if hub_audit is None:
        return
    try:
        hub_audit.log(MODULE, type_, actor=_actor(), tool="Website Blocks", **extra)
    except Exception:                                 # noqa: BLE001
        pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean_blocks(raw) -> list[dict]:
    """Keep only block types we know, and only the fields they declare.

    A saved page is replayed into the renderer, so this is the boundary where
    an unknown key stops being carried. It also means a block type that is
    later removed does not break every page that used it.
    """
    out = []
    for b in (raw or []):
        if not isinstance(b, dict):
            continue
        kind = str(b.get("kind") or "")
        if kind not in B.BLOCKS:
            continue
        keep = {"kind": kind, "anchor": str(b.get("anchor") or "")[:60]}
        for f in B.BLOCKS[kind]["fields"]:
            if f["k"] in b:
                keep[f["k"]] = b[f["k"]]
        out.append(keep)
    return out


# ------------------------------------------------------------------ pages
@app.route("/")
def index():
    # The catalogue is fetched by the page rather than inlined here: an
    # inlined `<script type="application/json">` is still a script block to
    # tools/pagecheck.py, which hands every block to `node --check` -- and
    # JSON is not JavaScript, so the page failed a check it could not pass.
    return render_template("index.html")


@app.route("/api/catalogue")
def api_catalogue():
    return jsonify(B.catalogue())


@app.route("/api/sample")
def api_sample():
    theme = request.args.get("theme", B.DEFAULT_THEME)
    return jsonify(B.sample_page(theme))


@app.route("/api/block/<kind>")
def api_block(kind):
    try:
        return jsonify(B.new_block(kind))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/render", methods=["POST"])
def api_render():
    """The markup, as a JSON string rather than an HTML response.

    Deliberately not `text/html`: the hub injects its sidebar into HTML
    replies, and a block carrying the staff sidebar pasted onto the public
    website is the failure this avoids.
    """
    body = request.get_json(silent=True) or {}
    theme = str(body.get("theme") or B.DEFAULT_THEME)
    mode = str(body.get("mode") or "page")
    blocks = _clean_blocks(body.get("blocks"))

    if mode == "block":
        idx = int(body.get("index") or 0)
        if not (0 <= idx < len(blocks)):
            return jsonify({"error": "No such block."}), 400
        html = B.render_block(blocks[idx], theme)
    elif mode == "document":
        html = B.render_page({
            "title": str(body.get("title") or "Smart 1 Marketing"),
            "description": str(body.get("description") or ""),
            "disclaimer": str(body.get("disclaimer") or ""),
            "with_nav": bool(body.get("with_nav")),
            "with_footer": bool(body.get("with_footer")),
            "theme": theme, "blocks": blocks,
        })
    else:
        html = B.render_blocks(blocks, theme)

    return jsonify({"html": html, "bytes": len(html.encode("utf-8")),
                    "mode": mode, "theme": theme})


@app.route("/api/download", methods=["POST"])
def api_download():
    """The same HTML as a file. An attachment, so no chrome is injected."""
    body = request.get_json(silent=True) or {}
    theme = str(body.get("theme") or B.DEFAULT_THEME)
    blocks = _clean_blocks(body.get("blocks"))
    title = str(body.get("title") or "smart-1-landing-page")
    if str(body.get("mode") or "document") == "document":
        html = B.render_page({
            "title": title,
            "description": str(body.get("description") or ""),
            "disclaimer": str(body.get("disclaimer") or ""),
            "with_nav": bool(body.get("with_nav")),
            "with_footer": bool(body.get("with_footer")),
            "theme": theme, "blocks": blocks,
        })
    else:
        html = B.render_blocks(blocks, theme)
    name = (re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "landing-page")
    _log("download", detail=f"{len(blocks)} block(s), {theme}")
    return Response(html, mimetype="text/html", headers={
        "Content-Disposition": f'attachment; filename="{name}.html"'})


# ------------------------------------------------------------------ saved
@app.route("/api/pages")
def api_pages():
    rows = sorted(_load(), key=lambda r: r.get("updated", ""), reverse=True)
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [r for r in rows if q in str(r.get("title", "")).lower()]
    return jsonify({"pages": [{k: r.get(k) for k in
                               ("id", "title", "theme", "updated", "actor",
                                "block_count")} for r in rows]})


@app.route("/api/pages", methods=["POST"])
def api_page_save():
    body = request.get_json(silent=True) or {}
    blocks = _clean_blocks(body.get("blocks"))
    if not blocks:
        return jsonify({"error": "There are no blocks to save."}), 400
    rows = _load()
    pid = str(body.get("id") or "").strip()
    row = next((r for r in rows if r.get("id") == pid), None)
    if row is None:
        row = {"id": uuid.uuid4().hex[:12], "created": _now()}
        rows.append(row)
    row.update({
        "title": str(body.get("title") or "Untitled page")[:120],
        "description": str(body.get("description") or "")[:400],
        "disclaimer": str(body.get("disclaimer") or "")[:1200],
        "theme": str(body.get("theme") or B.DEFAULT_THEME),
        "with_nav": bool(body.get("with_nav")),
        "with_footer": bool(body.get("with_footer")),
        "blocks": blocks, "block_count": len(blocks),
        "updated": _now(), "actor": _actor(),
    })
    _save(rows)
    _log("save", detail=row["title"])
    return jsonify({"ok": True, "id": row["id"], "page": row})


@app.route("/api/pages/<pid>")
def api_page_get(pid):
    row = next((r for r in _load() if r.get("id") == pid), None)
    if not row:
        return jsonify({"error": "No such page."}), 404
    return jsonify(row)


@app.route("/api/pages/<pid>", methods=["DELETE"])
def api_page_delete(pid):
    rows = _load()
    keep = [r for r in rows if r.get("id") != pid]
    if len(keep) == len(rows):
        return jsonify({"error": "No such page."}), 404
    _save(keep)
    _log("delete", detail=pid)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ AI copy
# The brief is explicit about the two things a model will otherwise supply
# unasked: numbers and claims. A stat band with an invented figure on it, or a
# rate chip carrying a CPM nobody quoted, is worse than an empty one -- it
# reads as measured, and the rep has no way to tell which figures came from
# the brief and which the model wrote.
_WRITE_RULES = """You write marketing copy for Smart 1 Marketing, a local
media agency. You are filling in one section of a landing page.

House voice: plain, confident, specific. Short sentences. Second person
("your market", "your team"). No exclamation marks, no "unlock", "leverage",
"revolutionise", "game-changing", "in today's fast-paced world".

Rules you must follow:
- Invent no numbers. No percentages, CPMs, prices, client counts, years in
  business, or results figures unless they appear in the brief. Where a field
  asks for a figure and the brief does not give one, return an empty string.
- Claim no awards, guarantees, certifications, review counts or locations that
  are not in the brief.
- Never mention Smart 1 Labs.
- Do not argue against any media the client is already running.
- Keep each field to the length shown in the example: a card body is two or
  three sentences, not a paragraph.

Return JSON only, with exactly the keys of the block you are given, and the
same list lengths."""


@app.route("/api/write", methods=["POST"])
def api_write():
    """Write the copy for the blocks on the page, one request per block.

    One request per block rather than one for the page, for the reason the
    Proposal Builder learned: a single failed section should not cost the
    other ten, and a loader can name what it is working on.
    """
    from hub import ai

    body = request.get_json(silent=True) or {}
    brief = str(body.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Say what the page is for first."}), 400
    blocks = _clean_blocks(body.get("blocks"))
    idx = body.get("index")
    if idx is not None:
        idx = int(idx)
        if not (0 <= idx < len(blocks)):
            return jsonify({"error": "No such block."}), 400
        blocks = [blocks[idx]]

    if not ai.ready():
        return jsonify({"error": "OpenAI is not configured on this server, so "
                                 "copy has to be written by hand. Everything "
                                 "else in the builder still works."}), 503

    out = []
    for b in blocks:
        kind = b["kind"]
        shape = {k: v for k, v in b.items() if k not in ("kind", "anchor")}
        try:
            got = ai.chat_json([
                {"role": "system", "content": _WRITE_RULES},
                {"role": "user", "content":
                    f"The page: {brief}\n\n"
                    f"The block is a '{B.BLOCKS[kind]['label']}' section — "
                    f"{B.BLOCKS[kind]['note']}\n\n"
                    "Rewrite the copy in this JSON, keeping its exact shape:\n"
                    + json.dumps(shape, ensure_ascii=False)},
            ], module=MODULE, purpose=f"block copy: {kind}", max_tokens=1400)
            out.append({"index": len(out), "kind": kind, "ok": True,
                        "block": dict(got, kind=kind, anchor=b.get("anchor", ""))})
        except Exception as exc:                      # noqa: BLE001
            # A named failure, not a silently unchanged block: the rep has to
            # be able to tell "the model declined" from "it wrote the same
            # thing back".
            out.append({"index": len(out), "kind": kind, "ok": False,
                        "error": str(exc), "block": b})

    _log("write", detail=f"{sum(1 for r in out if r['ok'])}/{len(out)} sections")
    return jsonify({"results": out})


@app.route("/api/status")
def api_status():
    try:
        from hub import ai
        ai_ready = ai.ready()
    except Exception:                                 # noqa: BLE001
        ai_ready = False
    return jsonify({"ai": ai_ready, "themes": len(B.THEMES),
                    "blocks": len(B.BLOCKS), "saved": len(_load())})
