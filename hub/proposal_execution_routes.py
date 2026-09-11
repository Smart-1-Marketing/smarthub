"""Staff routes for the Proposal Execution Center.

The engine owns state and task semantics; this is only the page/API layer. Every
route is behind the Hub login. The scheduler bridge is installed at registration
time so one proposal task advances on the same single-leader minute tick already
used by the durable creative queue.
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, render_template, request

from hub.blueprint_guard import install as install_guard
from hub import proposal_execution as pe

# MVP hardening kept beside registration so it is active before the first run is
# created. Empty lists must stay lists (the original helper's ``value or {}``
# collapsed [] to {}), because dependencies and missing-inputs are arrays. This
# assignment can disappear once the helper itself is folded into main.
def _stable_dumps(value):
    return json.dumps({} if value is None else value, ensure_ascii=False,
                      separators=(",", ":"))
pe._dumps = _stable_dumps

# The Monogram proposal names YouTube Channel Optimization as a one-time option.
# We still prepare it in the batch so nothing promised in a proposal is lost,
# but an option is reviewable rather than silently treated as committed scope.
_base_build_task_specs = pe.build_task_specs
def _build_task_specs_with_optional_gate(analysis):
    specs = _base_build_task_specs(analysis)
    for spec in specs:
        if spec.get("key") == "youtube_optimization":
            spec["mode"] = "approval"
    return specs
pe.build_task_specs = _build_task_specs_with_optional_gate

bp = Blueprint("proposal_execution", __name__)
install_guard(bp, mount="/proposal-execution")


def _who():
    """(stable notification owner, display name)."""
    try:
        from hub.users_routes import current_account
        account = current_account()
        if account is not None and getattr(account, "email", ""):
            return (account.email or "").strip().lower(), (account.name or account.email or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        from hub import current_user
        return "shared-login", current_user() or ""
    except Exception:  # noqa: BLE001
        return "shared-login", ""


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(exc, status=400):
    if not isinstance(exc, ValueError):
        try:
            from hub import errors
            errors.log_exception("proposal_execution", exc, path=request.path,
                                 actor=_who()[0])
        except Exception:  # noqa: BLE001
            pass
        return jsonify(ok=False,
                       error="Proposal Execution could not complete that action. The error was logged."), 500
    return jsonify(ok=False, error=str(exc)), status


@bp.get("/proposal-execution")
def page():
    _owner, name = _who()
    return render_template("proposal_execution.html", user=name, active="salesb")


@bp.get("/api/proposal-execution/proposals")
def proposals():
    client = str(request.args.get("client") or "").strip()
    if not client:
        return jsonify(ok=True, proposals=[])
    try:
        return jsonify(ok=True, proposals=pe.proposal_choices(client))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.get("/api/proposal-execution/runs")
def runs():
    try:
        return jsonify(ok=True, runs=pe.list_runs(
            str(request.args.get("client") or "").strip(),
            request.args.get("limit") or 50))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/analyze")
def analyze():
    body = _body()
    owner, _name = _who()
    try:
        run, created = pe.create_run(
            body.get("client"), body.get("proposal_id"), owner=owner, actor=owner,
            force=bool(body.get("force")))
        return jsonify(ok=True, created=created, run=run.as_dict(full=True))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.get("/api/proposal-execution/run/<int:run_id>")
def run_detail(run_id):
    try:
        run = pe.get_run(run_id)
        if not run:
            return jsonify(ok=False, error="That execution run could not be found."), 404
        return jsonify(ok=True, run=run.as_dict(full=True),
                       events=pe.events_for_run(run.id, 80))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/run/<int:run_id>/inputs")
def save_inputs(run_id):
    owner, _name = _who()
    try:
        run = pe.update_inputs(run_id, _body().get("inputs") or {}, actor=owner)
        return jsonify(ok=True, run=run.as_dict(full=True))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/run/<int:run_id>/start")
def start(run_id):
    owner, _name = _who()
    try:
        run = pe.start_run(run_id, actor=owner)
        return jsonify(ok=True, run=run.as_dict(full=True))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/run/<int:run_id>/pause")
def pause(run_id):
    owner, _name = _who()
    try:
        run = pe.pause_run(run_id, actor=owner)
        return jsonify(ok=True, run=run.as_dict(full=True))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/run/<int:run_id>/retry-failed")
def retry_failed(run_id):
    owner, _name = _who()
    try:
        run = pe.retry_failed(run_id, actor=owner)
        return jsonify(ok=True, run=run.as_dict(full=True))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/task/<int:task_id>/approve")
def approve(task_id):
    owner, _name = _who()
    try:
        task = pe.approve_task(task_id, actor=owner)
        run = pe.get_run(task.run_id)
        return jsonify(ok=True, task=task.as_dict(), run=run.as_dict(full=True))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/task/<int:task_id>/changes")
def changes(task_id):
    owner, _name = _who()
    try:
        task = pe.request_changes(task_id, _body().get("feedback") or "", actor=owner)
        return jsonify(ok=True, task=task.as_dict())
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/task/<int:task_id>/rerun")
def rerun(task_id):
    owner, _name = _who()
    try:
        task = pe.rerun_task(task_id, actor=owner)
        return jsonify(ok=True, task=task.as_dict())
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.post("/api/proposal-execution/task/<int:task_id>/mark")
def mark(task_id):
    owner, _name = _who()
    try:
        task = pe.mark_task(task_id, str(_body().get("state") or ""), actor=owner)
        return jsonify(ok=True, task=task.as_dict())
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@bp.get("/api/proposal-execution/adapters")
def adapters():
    return jsonify(ok=True, adapters=pe.adapters())


def register_proposal_execution(app):
    app.register_blueprint(bp)
    pe.install_scheduler_bridge()
    return app
