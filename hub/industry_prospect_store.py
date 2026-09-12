"""Durable prospect state and a cross-worker operation lock.

Paid attempts are saved before the provider call. A process crash leaves the
attempt (and lock) for reconciliation instead of automatically buying again.
Uses the Hub database, including its advisory-locked schema creation.
"""
from contextlib import contextmanager
import time

from sqlalchemy import Column, JSON, MetaData, String, Table, select
from sqlalchemy.exc import IntegrityError

from hub.extensions import create_all_metadata, shared_engine

metadata = MetaData()
records = Table("industry_prospect_records", metadata,
                Column("id", String(220), primary_key=True),
                Column("kind", String(30), nullable=False, index=True),
                Column("data", JSON, nullable=False))


class ProspectError(RuntimeError):
    pass


def init_store():
    error = create_all_metadata(metadata)
    if error:
        raise ProspectError("Prospect database is unavailable.")


def get(key, default=None):
    with shared_engine().connect() as conn:
        result = conn.execute(select(records.c.data).where(records.c.id == key)).first()
    return result[0] if result else default


def put(key, kind, data):
    # All service writes are serialized by operation().
    with shared_engine().begin() as conn:
        changed = conn.execute(records.update().where(records.c.id == key)
                               .values(kind=kind, data=data))
        if not changed.rowcount:
            conn.execute(records.insert().values(id=key, kind=kind, data=data))


def rows(kind):
    with shared_engine().connect() as conn:
        return list(conn.execute(select(records.c.data).where(records.c.kind == kind)).scalars())


@contextmanager
def operation(actor, action):
    try:
        with shared_engine().begin() as conn:
            conn.execute(records.insert().values(id="operation-lock", kind="lock",
                         data={"actor": actor, "action": action, "started": time.time()}))
    except IntegrityError as exc:
        raise ProspectError("Another prospect operation is running. If it was interrupted, an administrator must reconcile it before clearing the lock.") from exc
    try:
        yield
    finally:
        with shared_engine().begin() as conn:
            conn.execute(records.delete().where(records.c.id == "operation-lock"))
