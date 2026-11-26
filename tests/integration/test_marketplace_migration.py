from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_upgrade_existing_0004_and_fresh_install(tmp_path: Path) -> None:
    db = tmp_path / "existing.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}", "APP_ENV": "development"}

    def alembic(*args: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, check=True, capture_output=True
        )

    alembic("upgrade", "0004_subcontract_rental_tariffs")
    with sqlite3.connect(db) as conn:
        # 0001 dynamically creates the current metadata even at the old revision.
        conn.execute("DROP TABLE marketplace_draft_versions")
        conn.execute("DROP TABLE marketplace_drafts")
        conn.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO legacy_marker VALUES ('untouched')")
    alembic("upgrade", "head")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM legacy_marker").fetchone() == ("untouched",)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0005_marketplace_drafts",
        )
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"marketplace_drafts", "marketplace_draft_versions"}.issubset(tables)

    fresh = tmp_path / "fresh.db"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{fresh}"
    alembic("upgrade", "head")
    with sqlite3.connect(fresh) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0005_marketplace_drafts",
        )
