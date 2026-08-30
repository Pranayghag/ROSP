"""In-app notifications (Phase 12).

Notifications are written inside the same transaction as the change that
triggered them, so a user is never told about something that was rolled back.

Delivery is deliberately in-app only. Email or SMS would need credentials and a
mail server, which would make the project harder to clone and run -- an
important consideration for an open-source project. ``docs/ROADMAP.md`` records
how to add email later.
"""

from __future__ import annotations

from sqlalchemy import select

from ..constants import Role
from ..extensions import db
from ..models import Complaint, Notification, User


def notify(user_id: int | None, message: str, complaint: Complaint | None = None) -> None:
    """Queue a notification for one user. Silently ignores a missing user."""
    if not user_id:
        return
    db.session.add(
        Notification(
            user_id=user_id,
            complaint_id=complaint.id if complaint else None,
            message=message[:300],
        )
    )


def notify_admins(message: str, complaint: Complaint | None = None) -> None:
    """Queue a notification for every administrator."""
    admin_ids = db.session.scalars(select(User.id).where(User.role == Role.ADMIN)).all()
    for admin_id in admin_ids:
        notify(admin_id, message, complaint)


def notify_complaint_parties(
    complaint: Complaint, message: str, exclude_user_id: int | None = None
) -> None:
    """Notify the student and the assigned staff member about a complaint.

    ``exclude_user_id`` skips whoever performed the action -- there is no point
    telling someone what they just did themselves.
    """
    for user_id in (complaint.student_id, complaint.assigned_staff_id):
        if user_id and user_id != exclude_user_id:
            notify(user_id, message, complaint)


def unread_count(user: User) -> int:
    """Number of unread notifications, used for the navbar badge."""
    if not getattr(user, "is_authenticated", False):
        return 0
    return (
        db.session.scalar(
            select(db.func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.is_read.is_(False)
            )
        )
        or 0
    )


def recent_for(user: User, limit: int = 20) -> list[Notification]:
    return list(
        db.session.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        ).all()
    )


def mark_all_read(user: User) -> int:
    """Mark every unread notification for ``user`` as read; returns the count."""
    unread = db.session.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.is_read.is_(False)
        )
    ).all()
    for item in unread:
        item.is_read = True
    return len(unread)
