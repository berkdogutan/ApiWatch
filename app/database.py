"""SQLAlchemy engine, session factory and database initialisation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(url)

    in_memory = url.database in (None, "", ":memory:") or url.query.get("mode") == "memory"
    kwargs: dict = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    if in_memory:
        # Every new connection to ":memory:" opens a separate, empty database, so all
        # threads must share one connection.
        kwargs["poolclass"] = StaticPool
    else:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if not in_memory:
            # Write-ahead logging: dashboard reads never block storing check results.
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create missing tables and indexes. Safe to call on every start-up."""
    from app import models  # noqa: F401  - register models on Base.metadata

    Base.metadata.create_all(engine)
    # create_all skips the indexes of tables that already exist; add any new ones.
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to the app's engine."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


if __name__ == "__main__":  # python -m app.database
    # Use the importable module, not this __main__ copy: the models register their tables
    # on app.database.Base.
    from app import database
    from app.config import get_settings

    database_url = get_settings().database_url
    database.init_db(database.make_engine(database_url))
    print(f"Database initialised at {database_url}")
