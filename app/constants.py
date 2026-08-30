"""Shared vocabulary: roles, complaint statuses, priorities.

Defined in one place so the models, forms, templates and tests can never drift
apart on spelling.
"""

from __future__ import annotations


class Role:
    STUDENT = "student"
    STAFF = "staff"
    ADMIN = "admin"

    ALL = (STUDENT, STAFF, ADMIN)


class Status:
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    STUDENT_VERIFICATION = "STUDENT_VERIFICATION"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"

    ALL = (
        PENDING,
        ASSIGNED,
        IN_PROGRESS,
        RESOLVED,
        STUDENT_VERIFICATION,
        CLOSED,
        REOPENED,
    )


#: Human-readable labels for every status.
STATUS_LABELS = {
    Status.PENDING: "Pending",
    Status.ASSIGNED: "Assigned",
    Status.IN_PROGRESS: "In Progress",
    Status.RESOLVED: "Resolved",
    Status.STUDENT_VERIFICATION: "Student Verification",
    Status.CLOSED: "Closed",
    Status.REOPENED: "Reopened",
}

#: Bootstrap contextual colour for each status badge.
STATUS_COLOURS = {
    Status.PENDING: "secondary",
    Status.ASSIGNED: "info",
    Status.IN_PROGRESS: "primary",
    Status.RESOLVED: "success",
    Status.STUDENT_VERIFICATION: "warning",
    Status.CLOSED: "dark",
    Status.REOPENED: "danger",
}

#: The timeline rendered on the complaint details page, in display order.
#: "SUBMITTED" is a synthetic first step: every complaint has been submitted,
#: so it is always complete.
TIMELINE_STEPS = (
    ("SUBMITTED", "Submitted"),
    (Status.PENDING, "Pending"),
    (Status.ASSIGNED, "Assigned"),
    (Status.IN_PROGRESS, "In Progress"),
    (Status.RESOLVED, "Resolved"),
    (Status.STUDENT_VERIFICATION, "Student Verification"),
    (Status.CLOSED, "Closed"),
)


class Priority:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"

    ALL = (LOW, MEDIUM, HIGH, URGENT)


PRIORITY_COLOURS = {
    Priority.LOW: "success",
    Priority.MEDIUM: "info",
    Priority.HIGH: "warning",
    Priority.URGENT: "danger",
}


class AttachmentType:
    """Distinguishes the student's evidence from the staff's proof of repair."""

    #: Photos of the problem, uploaded by the student when filing.
    EVIDENCE = "EVIDENCE"
    #: Photos of the completed fix, uploaded by staff on resolution (Phase 10).
    RESOLUTION = "RESOLUTION"

    ALL = (EVIDENCE, RESOLUTION)


ATTACHMENT_TYPE_LABELS = {
    AttachmentType.EVIDENCE: "Evidence",
    AttachmentType.RESOLUTION: "Resolution Photo",
}
