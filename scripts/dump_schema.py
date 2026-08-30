"""Generate ``sql/schema.sql`` from the SQLAlchemy models.

    python scripts/dump_schema.py

``app/models.py`` is the single source of truth for the schema. This script
renders the same definitions as plain MySQL DDL, for anyone who would rather
create the tables by hand (or read them in a report) than run the application.

Re-run it whenever a model changes so the two never drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import create_app
from app.extensions import db

HEADER = """\
-- ---------------------------------------------------------------------------
-- ROSP database schema (MySQL / MariaDB)
--
-- GENERATED FILE -- do not edit by hand.
-- Regenerate with:  python scripts/dump_schema.py
-- The authoritative definitions live in app/models.py
--
-- To create everything from scratch without running the application:
--   mysql -u root -p < sql/schema.sql
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS `rosp`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `rosp`;

SET FOREIGN_KEY_CHECKS = 0;
"""

FOOTER = """
SET FOREIGN_KEY_CHECKS = 1;
"""


def main() -> None:
    app = create_app()
    dialect = mysql.dialect()
    statements: list[str] = [HEADER]

    with app.app_context():
        # sorted_tables orders parents before children, so the foreign keys
        # resolve even with checks enabled.
        for table in db.metadata.sorted_tables:
            ddl = str(CreateTable(table).compile(dialect=dialect)).strip()
            statements.append(f"\n-- {table.name} " + "-" * (68 - len(table.name)))
            statements.append(f"{ddl} ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")

            for index in sorted(table.indexes, key=lambda i: i.name or ""):
                statements.append(
                    str(CreateIndex(index).compile(dialect=dialect)).strip() + ";"
                )

    statements.append(FOOTER)

    target = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(statements) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
