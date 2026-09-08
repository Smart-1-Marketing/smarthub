"""The JSON behind the radio script tool's one page.

Every write route returns the row it wrote, so the page never has to
reconstruct what the server decided -- the rule `modules/video_tools/api.py`
states about the plan/options split, one tool along.
"""
from __future__ import annotations

import json

from flask import jsonify, request

from . import engine, pdf
from .db import db
from .models import RadioScriptSet


def _actor() -> str:
    """Who is doing this, read the way the rest of the Hub reads it -- the
    signed session, never `flask.session`, which nothing in this Hub has
    ever written a name into. See modules/video_tools/api.py's own note:
    getting this wrong is the unattributable-write failure this repo has
    already had to undo in seven modules."""
    try:
        from hub import current_user
        return str(current_user() or "")[:60]
    except Exception:                                 # noqa: BLE001
        return ""


try:
    from hub import audit as _hub_audit
    _log = _hub_audit.for_module("radio_scripts", _actor)
except Exception:                                     # noqa: BLE001 — standalone
    def _log(*_a, **_k):
        return None

try:
    from hub.webargs import clamp_int
except Exception:                                     # noqa: BLE001
    def clamp_int(raw, default, low=1, high=200):
        try:
            return max(low, min(high, int(float(raw))))
        except (TypeError, ValueError):
            return default


def _fail(message: str, code: int = 400):
    return jsonify({"ok": False, "error": str(message)}), code


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _brief_from(body: dict) -> dict:
    shows = body.get("local_shows")
    if isinstance(shows, str):
        shows = [s.strip() for s in shows.split(",") if s.strip()]
    elif not isinstance(shows, list):
        shows = []
    funnel = {}
    for key in ("fan_homes", "reachable_homes", "screens"):
        val = str(body.get(key) or "").strip()
        if val:
            funnel[key] = val
    return {
        "company": str(body.get("client_name") or body.get("company") or "").strip(),
        "client_name": str(body.get("client_name") or "").strip(),
        "market": str(body.get("market") or "").strip(),
        "team": str(body.get("team") or "").strip(),
        "package": str(body.get("package") or "").strip(),
        "local_shows": shows[:12],
        "offer": str(body.get("offer") or "").strip(),
        "phone": str(body.get("phone") or "").strip(),
        "url": str(body.get("url") or "").strip(),
        "funnel": funnel,
        "lead_id": str(body.get("lead_id") or "").strip(),
    }


def attach(bp):
    """Hang this module's routes on the blueprint `__init__.py` builds."""

    @bp.post("/api/generate")
    def api_generate():
        body = _body()
        brief = _brief_from(body)
        if not brief["market"] and not brief["client_name"]:
            return _fail("Add at least a client name or a market before generating.")
        try:
            result = engine.generate(brief)
        except RuntimeError as exc:
            return _fail(str(exc), 502)

        row = RadioScriptSet(
            client_name=brief["client_name"],
            lead_id=brief["lead_id"],
            brief_json=json.dumps(brief),
            concepts_json=json.dumps(result["concepts"]),
            flags_json=json.dumps(result["flags"]),
            actor=_actor(),
        )
        db.session.add(row)
        db.session.commit()
        _log("script_set_generated", client=brief["client_name"] or None,
            set_id=row.id, concepts=len(result["concepts"]))
        return jsonify({"ok": True, "set": row.to_dict()})

    @bp.get("/api/sets")
    def api_sets():
        limit = clamp_int(request.args.get("limit"), 25, low=1, high=100)
        offset = clamp_int(request.args.get("offset"), 0, low=0, high=1000000)
        query = RadioScriptSet.query
        search = (request.args.get("q") or "").strip()
        if search:
            from sqlalchemy import or_
            # Escape LIKE wildcards so a user's percent or underscore is literal.
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.filter(or_(*(column.ilike(pattern, escape="\\") for column in (
                RadioScriptSet.client_name, RadioScriptSet.actor,
                RadioScriptSet.brief_json, RadioScriptSet.concepts_json))))
        count = query.count()
        rows = (query.order_by(RadioScriptSet.updated_at.desc(), RadioScriptSet.id.desc())
                .offset(offset).limit(limit).all())
        return jsonify(ok=True, sets=[r.to_dict(full=False) for r in rows], count=count,
                       has_more=offset + len(rows) < count)

    @bp.get("/api/sets/<int:set_id>")
    def api_set_detail(set_id: int):
        row = RadioScriptSet.query.get(set_id)
        if not row:
            return _fail("No such script set.", 404)
        return jsonify({"ok": True, "set": row.to_dict()})

    @bp.post("/api/sets/<int:set_id>/regenerate")
    def api_regenerate(set_id: int):
        row = RadioScriptSet.query.get(set_id)
        if not row:
            return _fail("No such script set.", 404)
        body = _body()
        try:
            index = int(body.get("concept_index"))
        except (TypeError, ValueError):
            return _fail("Which concept to regenerate?")
        concepts = row.concepts()
        if not (0 <= index < len(concepts)):
            return _fail("No such concept on this set.")
        try:
            row.concepts_json = json.dumps(
                engine.regenerate_concept(row.brief(), concepts, index))
        except (RuntimeError, ValueError) as exc:
            return _fail(str(exc), 502)
        db.session.commit()
        _log("script_concept_regenerated", client=row.client_name or None,
            set_id=row.id, concept_index=index)
        return jsonify({"ok": True, "set": row.to_dict()})

    @bp.get("/api/sets/<int:set_id>/pdf")
    def api_set_pdf(set_id: int):
        row = RadioScriptSet.query.get(set_id)
        if not row:
            return _fail("No such script set.", 404)
        from flask import Response
        data = pdf.build_script_pdf(row.to_dict())
        client = (row.client_name or "radio-scripts").replace(" ", "-")
        return Response(data, mimetype="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{client}-radio-scripts.pdf"'})
