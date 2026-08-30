"""Complaint lifecycle: status transitions, assignment and the timeline.

The flow follows the specification:

    Submitted -> Pending -> Assigned -> In Progress -> Resolved
              -> Student Verification -> Closed

Every change appends a row to ``complaint_events``, which is append-only. The
timeline shown on the details page is derived from those events rather than
from the current status alone, so history stays accurate even after a complaint
is reopened.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..constants import TIMELINE_STEPS, Role, Status
from ..extensions import db
from ..models import Complaint, ComplaintEvent, User


class WorkflowError(RuntimeError):
    """Raised when a requested transition is not permitted."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def record_event(
    complaint: Complaint,
    status: str,
    actor: User | None = None,
    note: str | None = None,
) -> ComplaintEvent:
    """Append an entry to the complaint's audit trail."""
    event = ComplaintEvent(
        complaint_id=complaint.id,
        status=status,
        note=note,
        actor_id=actor.id if actor else None,
    )
    db.session.add(event)
    return event


def open_complaint(complaint: Complaint, actor: User) -> None:
    """Record the two opening events for a newly submitted complaint."""
    record_event(complaint, "SUBMITTED", actor, "Complaint submitted by student.")
    record_event(complaint, Status.PENDING, actor, "Awaiting review by admin.")


def assign(complaint: Complaint, staff: User, actor: User, note: str | None = None) -> None:
    """Assign a complaint to a staff member. Admin only."""
    if not actor.is_admin:
        raise WorkflowError("Only an admin can assign complaints.")
    if staff.role not in (Role.STAFF, Role.ADMIN):
        raise WorkflowError("Complaints can only be assigned to staff members.")
    if complaint.status in (Status.CLOSED,):
        raise WorkflowError("A closed complaint cannot be reassigned.")

    complaint.assigned_staff_id = staff.id
    complaint.status = Status.ASSIGNED
    complaint.updated_at = _utcnow()
    record_event(
        complaint,
        Status.ASSIGNED,
        actor,
        note or f"Assigned to {staff.name}.",
    )


def transition(
    complaint: Complaint, new_status: str, actor: User, note: str | None = None
) -> None:
    """Move a complaint to ``new_status`` if ``actor`` is allowed to do so."""
    allowed = {key for key, _label, _style in available_actions(complaint, actor)}
    if new_status not in allowed:
        raise WorkflowError("That status change is not available for this complaint.")

    complaint.status = new_status
    complaint.updated_at = _utcnow()

    if new_status == Status.RESOLVED:
        complaint.resolved_at = _utcnow()
        record_event(complaint, Status.RESOLVED, actor, note or "Marked as resolved.")
        # Hand straight over to the student to confirm the fix.
        complaint.status = Status.STUDENT_VERIFICATION
        record_event(
            complaint,
            Status.STUDENT_VERIFICATION,
            actor,
            "Awaiting confirmation from the student.",
        )
        return

    if new_status == Status.CLOSED:
        complaint.closed_at = _utcnow()
        record_event(complaint, Status.CLOSED, actor, note or "Complaint closed.")
        return

    if new_status == Status.REOPENED:
        complaint.resolved_at = None
        # Send it back to the assigned staff member to work on again.
        record_event(
            complaint,
            Status.REOPENED,
            actor,
            note or "Student reported the issue is not fixed.",
        )
        complaint.status = Status.IN_PROGRESS
        record_event(complaint, Status.IN_PROGRESS, actor, "Reopened for further work.")
        return

    record_event(complaint, new_status, actor, note)


def available_actions(complaint: Complaint, user: User) -> list[tuple[str, str, str]]:
    """Transitions ``user`` may perform on ``complaint`` right now.

    Returns ``(status, button_label, bootstrap_style)`` triples so the template
    can render exactly the buttons the current user is entitled to press.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    status = complaint.status
    is_owner = user.id == complaint.student_id
    is_assignee = bool(complaint.assigned_staff_id) and user.id == complaint.assigned_staff_id
    can_work = user.is_admin or is_assignee

    actions: list[tuple[str, str, str]] = []

    if can_work and status == Status.ASSIGNED:
        actions.append((Status.IN_PROGRESS, "Start Work", "primary"))

    if can_work and status in (Status.IN_PROGRESS, Status.REOPENED):
        actions.append((Status.RESOLVED, "Mark Resolved", "success"))

    if status == Status.STUDENT_VERIFICATION:
        if is_owner:
            actions.append((Status.CLOSED, "Confirm Fixed & Close", "success"))
            actions.append((Status.REOPENED, "Not Fixed - Reopen", "danger"))
        # An admin can close on the student's behalf if they never respond.
        if user.is_admin:
            actions.append((Status.CLOSED, "Close Complaint", "dark"))

    return actions


def build_timeline(complaint: Complaint) -> list[dict]:
    """Describe each timeline step for rendering.

    Each entry carries ``key``, ``label``, ``state`` (``complete``/``current``/
    ``upcoming``), and the timestamp plus note of the most recent matching event.
    """
    events_by_status: dict[str, ComplaintEvent] = {}
    for event in complaint.events:
        events_by_status[event.status] = event

    order = [key for key, _label in TIMELINE_STEPS]
    current_index = order.index(complaint.status) if complaint.status in order else -1

    timeline = []
    for index, (key, label) in enumerate(TIMELINE_STEPS):
        event = events_by_status.get(key)

        if key == complaint.status:
            state = "current"
        elif event is not None or (current_index >= 0 and index < current_index):
            state = "complete"
        else:
            state = "upcoming"

        timeline.append(
            {
                "key": key,
                "label": label,
                "state": state,
                "at": event.created_at if event else None,
                "note": event.note if event else None,
                "actor": event.actor.name if event and event.actor else None,
            }
        )
    return timeline
