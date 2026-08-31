"""Remove the seeded demo accounts and everything attached to them.

    python scripts/purge_demo_data.py           # show what would go
    python scripts/purge_demo_data.py --apply   # actually delete

The seed script creates placeholder people on an obviously-fake domain so a
fresh clone has something to look at. Once a deployment holds real users those
placeholders are worse than useless -- they clutter Staff Management and would
have notifications addressed to addresses nobody reads.

Categories and locations are kept: they are reference data, not demo data.

Matching is by email domain (``@rosp.edu`` by default), so a real account is
never caught by accident.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import (
    Attachment,
    Complaint,
    ComplaintEvent,
    EmailLog,
    Notification,
    OtpCode,
    User,
)
from app.services.uploads import resolve_stored_path

DEFAULT_DOMAIN = "@rosp.edu"


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete seeded demo accounts.")
    parser.add_argument(
        "--domain",
        default=DEFAULT_DOMAIN,
        help=f"email domain identifying demo accounts (default: {DEFAULT_DOMAIN})",
    )
    parser.add_argument("--apply", action="store_true", help="actually delete")
    options = parser.parse_args()

    app = create_app()
    with app.app_context():
        users = db.session.scalars(
            select(User).where(User.email.like(f"%{options.domain}"))
        ).all()

        if not users:
            print(f"No accounts matching *{options.domain}. Nothing to do.")
            return

        user_ids = [u.id for u in users]

        complaints = db.session.scalars(
            select(Complaint).where(
                db.or_(
                    Complaint.student_id.in_(user_ids),
                    Complaint.assigned_staff_id.in_(user_ids),
                )
            )
        ).all()
        complaint_ids = [c.id for c in complaints]

        attachments = (
            db.session.scalars(
                select(Attachment).where(Attachment.complaint_id.in_(complaint_ids))
            ).all()
            if complaint_ids
            else []
        )

        print(f"Accounts matching *{options.domain}:\n")
        for user in users:
            print(f"  {user.role:<8} {user.email:<24} {user.name}")

        print("\nWould also delete:")
        print(f"  {len(complaints)} complaint(s)")
        print(f"  {len(attachments)} attachment(s), including their files on disk")
        print("\nKept: categories, locations.")

        if not options.apply:
            print("\nDry run. Re-run with --apply to delete.")
            return

        # 1. Files on disk first -- a row without its file is recoverable,
        #    an orphaned file nobody references is not.
        removed_files = 0
        for attachment in attachments:
            try:
                path = resolve_stored_path(attachment.file_path)
                if path.is_file():
                    path.unlink()
                    removed_files += 1
            except Exception as exc:  # pragma: no cover - filesystem dependent
                print(f"  could not remove file for attachment {attachment.id}: {exc}")

        # 2. Rows that point at users or complaints, before their targets.
        for model, column in (
            (Notification, Notification.user_id),
            (OtpCode, OtpCode.user_id),
            (EmailLog, EmailLog.user_id),
        ):
            for row in db.session.scalars(select(model).where(column.in_(user_ids))).all():
                db.session.delete(row)

        if complaint_ids:
            for row in db.session.scalars(
                select(EmailLog).where(EmailLog.complaint_id.in_(complaint_ids))
            ).all():
                db.session.delete(row)
            for row in db.session.scalars(
                select(ComplaintEvent).where(
                    ComplaintEvent.complaint_id.in_(complaint_ids)
                )
            ).all():
                db.session.delete(row)
            for row in attachments:
                db.session.delete(row)

        db.session.flush()

        for complaint in complaints:
            db.session.delete(complaint)
        db.session.flush()

        # Clear the self-referential authorizer link before removing the rows.
        for user in users:
            user.authorized_by = None
        db.session.flush()

        for user in users:
            db.session.delete(user)

        db.session.commit()

        print(f"\nDeleted {len(users)} account(s), {len(complaints)} complaint(s), "
              f"{len(attachments)} attachment(s), {removed_files} file(s).")
        print("Categories and locations were kept.")


if __name__ == "__main__":
    main()
