"""Bring an existing database up to date with the current models.

    python scripts/migrate.py            # show what would change
    python scripts/migrate.py --apply    # apply it

Additive and idempotent: it creates missing tables and adds missing columns,
and never drops or rewrites anything. Safe to run against a database that
already holds real complaints -- unlike ``init_db.py --reset``, which destroys
everything.

New columns are added as NULLable (or with a default) because existing rows
have no value for them; sensible values are then backfilled, e.g. every account
that existed before staff authorization was introduced is marked AUTHORIZED so
nobody is locked out by the upgrade.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app import create_app
from app.constants import AuthorizationStatus, Role
from app.extensions import db


def _column_sql(column, dialect) -> str:
    """Render one column definition for an ALTER TABLE ADD COLUMN."""
    spec = f"{column.name} {column.type.compile(dialect=dialect)}"
    if not column.nullable:
        spec += " NOT NULL"
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            spec += f" DEFAULT {1 if value else 0}"
        elif isinstance(value, (int, float)):
            spec += f" DEFAULT {value}"
        elif isinstance(value, str):
            spec += f" DEFAULT '{value}'"
    return spec


def plan(app) -> tuple[list[str], list[str]]:
    """Work out which tables and columns are missing."""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    missing_tables, statements = [], []

    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing_tables.append(table.name)
            continue

        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                spec = _column_sql(column, db.engine.dialect)
                statements.append(f"ALTER TABLE {table.name} ADD COLUMN {spec}")

    return missing_tables, statements


def backfill() -> list[str]:
    """Give existing rows sensible values for the new columns."""
    notes = []

    # Everyone who predates the authorization gate keeps working.
    result = db.session.execute(
        text(
            "UPDATE users SET authorization_status = :status "
            "WHERE authorization_status IS NULL OR authorization_status = ''"
        ),
        {"status": AuthorizationStatus.AUTHORIZED},
    )
    if result.rowcount:
        notes.append(f"marked {result.rowcount} existing account(s) AUTHORIZED")

    result = db.session.execute(
        text("UPDATE users SET password_set = 1 WHERE password_set IS NULL")
    )
    if result.rowcount:
        notes.append(f"marked {result.rowcount} account(s) as having a password set")

    # Staff and admin rows should carry a designation for the new UI.
    result = db.session.execute(
        text(
            "UPDATE users SET designation = 'Other' "
            "WHERE designation IS NULL AND role IN (:staff, :admin)"
        ),
        {"staff": Role.STAFF, "admin": Role.ADMIN},
    )
    if result.rowcount:
        notes.append(f"defaulted designation on {result.rowcount} staff account(s)")

    db.session.commit()
    return notes


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply additive schema changes.")
    parser.add_argument("--apply", action="store_true", help="actually run the changes")
    options = parser.parse_args()

    app = create_app()
    with app.app_context():
        missing_tables, statements = plan(app)

        if not missing_tables and not statements:
            print("Schema is already up to date.")
        else:
            print("Pending changes:\n")
            for name in missing_tables:
                print(f"  CREATE TABLE {name}")
            for statement in statements:
                print(f"  {statement}")
            print()

        if not options.apply:
            print("Dry run. Re-run with --apply to make these changes.")
            return

        # create_all only creates tables that do not exist; it never alters.
        db.create_all()
        for name in missing_tables:
            print(f"created table {name}")

        for statement in statements:
            db.session.execute(text(statement))
            print(f"ok  {statement}")
        db.session.commit()

        for note in backfill():
            print(f"backfill: {note}")

        print("\nMigration complete.")


if __name__ == "__main__":
    main()
