"""Project attribution for the existing provider meters, including failed calls."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import logging

_scope = ContextVar("commercial_usage", default=None)


def record(provider, *, operation="", units=1, cost=None, cached=False, ok=True):
    scope = _scope.get()
    if scope is not None:
        scope[1].append(dict(project_id=scope[0], provider=provider, operation=str(operation)[:120],
                             units=units, cost_usd=0 if cached else cost, cached=cached, ok=ok))


def _flush(scope):
    if not scope or not scope[1]:
        return
    from sqlalchemy.orm import Session
    from .db import db
    from .finishing_models import ProductionUsage
    try:
        with Session(db.engine) as session:
            session.add_all(ProductionUsage(**r) for r in scope[1])
            session.commit()
    except Exception:
        logging.getLogger(__name__).exception("Commercial usage could not be saved")


@contextmanager
def scope(project_id):
    if _scope.get() is not None:
        yield
        return
    value = (project_id, [])
    token = _scope.set(value)
    try:
        yield
    finally:
        _scope.reset(token)
        _flush(value)


def install(bp):
    from flask import g, request

    @bp.before_request
    def start_usage():
        project_id = (request.view_args or {}).get("project_id")
        if project_id:
            value = (project_id, [])
            g.cb_usage = (value, _scope.set(value))

    @bp.teardown_request
    def finish_usage(_error):
        captured = g.pop("cb_usage", None)
        if captured:
            value, token = captured
            _scope.reset(token)
            _flush(value)


def metered(fn):
    @wraps(fn)
    def wrapped(project, *args, **kwargs):
        with scope(getattr(project, "project_id", None) or project.id):
            return fn(project, *args, **kwargs)
    return wrapped
