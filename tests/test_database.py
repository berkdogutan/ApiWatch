from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.database import init_db, make_engine
from app.main import create_app
from tests.conftest import monitor_payload

REPO_ROOT = Path(__file__).resolve().parent.parent


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}


def test_init_command_creates_the_tables(tmp_path: Path) -> None:
    database = tmp_path / "apiwatch.db"
    env = {**os.environ, "APIWATCH_DATABASE_URL": f"sqlite:///{database}"}
    completed = subprocess.run(
        [sys.executable, "-m", "app.database"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert {"monitors", "check_results", "incidents", "json_baselines"} <= table_names(database)


def test_file_databases_use_write_ahead_logging(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'wal.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()


def test_init_db_adds_indexes_missing_from_existing_databases(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    init_db(engine)
    with engine.begin() as connection:  # simulate a database created by an older version
        connection.execute(text("DROP INDEX ix_check_results_failed"))
    init_db(engine)
    with engine.connect() as connection:
        indexes = {
            name
            for (name,) in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'index'")
            )
        }
    assert "ix_check_results_failed" in indexes
    engine.dispose()


def test_in_memory_database_is_shared_across_threads(transport) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        scheduler_enabled=False,
        trusted_hosts=["testserver"],
    )
    with TestClient(create_app(settings, transport=transport)) as client:
        assert client.post("/api/monitors", json=monitor_payload()).status_code == 201
        assert len(client.get("/api/monitors").json()) == 1
