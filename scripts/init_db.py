"""Create the MySQL database and every table.

    python scripts/init_db.py            # create database + tables
    python scripts/init_db.py --reset    # DROP the database first (destructive)

Creating the schema from the SQLAlchemy models keeps ``app/models.py`` as the
single source of truth. ``sql/schema.sql`` is generated from the same models by
``scripts/dump_schema.py`` for anyone who prefers to run raw SQL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Allow running this file directly: `python scripts/init_db.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db


def _server_connection_args(uri: str) -> tuple[dict, str]:
    """Split a MySQL URI into connection kwargs and the database name."""
    parts = urlsplit(uri)
    database = parts.path.lstrip("/").split("?")[0]
    return (
        {
            "host": parts.hostname or "127.0.0.1",
            "port": parts.port or 3306,
            "user": parts.username or "root",
            "password": parts.password or "",
        },
        database,
    )


def ensure_database(uri: str, reset: bool = False) -> None:
    """Create the target database if it does not exist yet.

    SQLAlchemy cannot create its own database, so connect to the server without
    selecting one and issue the DDL directly.
    """
    if not uri.startswith("mysql"):
        # SQLite and friends create themselves on first connect.
        return

    import pymysql

    args, database = _server_connection_args(uri)
    if not database:
        raise SystemExit("No database name found in the connection URI.")

    connection = pymysql.connect(**args)
    try:
        with connection.cursor() as cursor:
            if reset:
                cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
                print(f"Dropped database `{database}`.")
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()
        print(f"Database `{database}` is ready.")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the CampusCare database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop the database before recreating it (destroys all data)",
    )
    options = parser.parse_args()

    app = create_app()
    uri = app.config["SQLALCHEMY_DATABASE_URI"]

    if options.reset:
        confirmation = input(
            "This will DELETE every complaint, user and attachment record.\n"
            "Type 'reset' to continue: "
        )
        if confirmation.strip().lower() != "reset":
            print("Aborted. Nothing was changed.")
            return

    try:
        ensure_database(uri, reset=options.reset)
    except Exception as exc:  # pragma: no cover - depends on local MySQL
        print(f"\nCould not reach the database server: {exc}\n")
        print("Is MySQL running? With XAMPP, open the control panel and")
        print("press Start next to MySQL, then run this command again.")
        raise SystemExit(1) from exc

    with app.app_context():
        db.create_all()
        table_names = ", ".join(sorted(db.metadata.tables))
        print(f"Tables ready: {table_names}")


if __name__ == "__main__":
    main()
