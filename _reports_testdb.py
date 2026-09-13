"""Which database a reports test runs against, decided in one place.

Every ``test_reports_*.py`` file used to hard-wire a SQLite file, and two of
the seven defects the module's first review found were Postgres-only:
``Numeric(8, 4)`` refusing a pace of 10,000x and failing the whole hourly
insert, and ``substr()`` over a timestamp column, which Postgres has no
function for. SQLite ignores column precision and has ``substr`` on
everything, so no test could see either -- production is Postgres and the
tests were not.

``bind(tmp)`` sets ``REPORTS_DATABASE_URL`` before ``modules.reports.store``
is imported: the SQLite file under the test's own directory by default, or
the URL in ``REPORTS_TEST_DATABASE_URL`` when one is set, which is how
``checks.yml`` runs the same files a second time against the job's Postgres.
``reset(store)`` is called once the store is imported and, on Postgres, drops
and recreates this module's tables (``reports_*`` and nothing else) so a run
starts from an empty book rather than the previous file's rows -- the shape
``test_jsonstore.py`` records about a fresh directory in front of an
inherited database. On SQLite it is a no-op: the file is new.

Not a ``test_*.py`` file, so ``test_ci_gate.py`` does not expect a step for
it, and importable from a test that lives at the repo root.
"""
from __future__ import annotations

import os


def bind(tmp: str) -> str:
    """Point the reports store at its database for this run and say which
    kind it is: ``"postgres"`` or ``"sqlite"``."""
    url = (os.environ.get("REPORTS_TEST_DATABASE_URL") or "").strip()
    if url:
        os.environ["REPORTS_DATABASE_URL"] = url
        return "postgres" if url.startswith("postgres") else "other"
    os.environ["REPORTS_DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "reports.sqlite3")
    return "sqlite"


def reset(store, extra_tables: tuple[str, ...] = ()) -> None:
    """Start from an empty reports book on a shared database.

    Drops this module's own tables (through its metadata) plus any raw
    provider tables a test created by hand, then re-runs the module's boot
    DDL so the late columns land too. SQLite needs none of it -- the file is
    this run's own -- and a drop there would only cost time.
    """
    if not store.is_postgres():
        return
    from sqlalchemy import text
    with store.engine.begin() as conn:
        for name in extra_tables:
            conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
    store.Base.metadata.drop_all(store.engine)
    err = store._create_tables()
    if err:
        raise RuntimeError(f"reports tables could not be recreated: {err}")
    # The boot probe recorded its verdict at import, against the tables that
    # were there then; re-ask so db_error() reads the fresh schema.
    store.db_error()
