"""Service-level agreements and escalation (Phase 13).

Each category carries an ``sla_hours`` target. When a complaint is filed its
``due_at`` is stamped from that target, adjusted by priority: an URGENT problem
gets a quarter of the time a LOW one does.

Escalation is a pull, not a push: :func:`escalate_overdue` is safe to call
repeatedly (from a scheduled job, or from the admin dashboard) and only acts on
complaints that have breached their deadline and not yet been escalated.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ..constants import Priority, Status
from ..extensions import db
from ..models import Complaint, _as_utc, utcnow
from .notifications import notify, notify_admins

#: Multiplier applied to a category's SLA hours for each priority level.
PRIORITY_FACTOR = {
    Priority.URGENT: 0.25,
    Priority.HIGH: 0.5,
    Priority.MEDIUM: 1.0,
    Priority.LOW: 1.5,
}


def compute_due_at(complaint: Complaint):
    """Deadline for a complaint, from its category SLA and priority."""
    base_hours = complaint.category.sla_hours if complaint.category else 72
    factor = PRIORITY_FACTOR.get(complaint.priority, 1.0)
    hours = max(1, round(base_hours * factor))
    created = _as_utc(complaint.created_at) if complaint.created_at else utcnow()
    return created + timedelta(hours=hours)


def apply_sla(complaint: Complaint) -> None:
    """Stamp ``due_at`` on a complaint. Call on creation and on priority change."""
    complaint.due_at = compute_due_at(complaint)


def overdue_complaints() -> list[Complaint]:
    """Every open complaint past its deadline, most overdue first."""
    candidates = db.session.scalars(
        select(Complaint)
        .where(Complaint.status != Status.CLOSED, Complaint.due_at.is_not(None))
        .order_by(Complaint.due_at.asc())
    ).all()
    return [c for c in candidates if c.is_overdue]


def escalate_overdue() -> list[Complaint]:
    """Escalate breached complaints that have not been escalated already.

    Escalation notifies every admin and the assigned staff member, and stamps
    ``escalated_at`` so the same complaint is not reported twice. The caller is
    responsible for committing.
    """
    escalated = []
    for complaint in overdue_complaints():
        if complaint.escalated_at:
            continue

        complaint.escalated_at = utcnow()
        notify_admins(
            f"SLA breached: {complaint.code} ({complaint.title}) is overdue.",
            complaint,
        )
        if complaint.assigned_staff_id:
            notify(
                complaint.assigned_staff_id,
                f"{complaint.code} has passed its deadline and been escalated.",
                complaint,
            )
        escalated.append(complaint)

    return escalated
