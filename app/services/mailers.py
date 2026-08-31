"""High-level email composition.

One function per message the application sends. Views call these rather than
:func:`app.services.email.send_email` directly, so subjects, templates and
context stay consistent and are defined in exactly one place.

Every function returns the :class:`~app.models.EmailLog` row (or ``None`` when
there was no address to send to), so a caller can report honestly whether
delivery actually happened.
"""

from __future__ import annotations

from flask import current_app

from ..constants import AUTHORIZATION_LABELS, AttachmentType, Priority
from ..models import _as_utc
from .email import InlineImage, absolute_url, send_email, send_to_admin
from .uploads import resolve_stored_path

#: How many evidence photos to embed, and the total byte budget for them.
#: Mail servers reject large messages, and a 25 MB email helps nobody.
MAX_INLINE_IMAGES = 3
MAX_INLINE_BYTES = 3 * 1024 * 1024

#: Priority -> the pill colour used in email templates.
PRIORITY_TONES = {
    Priority.LOW: "good",
    Priority.MEDIUM: "active",
    Priority.HIGH: "pending",
    Priority.URGENT: "bad",
}


def _format(value, fmt: str = "%d %b %Y at %H:%M") -> str:
    return _as_utc(value).strftime(fmt) if value else "Not set"


def _location(complaint) -> str:
    location = complaint.location
    if location.building:
        return f"{location.building} - {location.name}"
    return location.name


def _collect_inline_evidence(complaint) -> list[InlineImage]:
    """Read evidence photos off disk for embedding.

    Failures are swallowed on purpose: a missing or unreadable file must not
    stop the assignment notification from going out. The "View Complaint"
    button in the template is always present as the reliable path.
    """
    images: list[InlineImage] = []
    budget = MAX_INLINE_BYTES

    for attachment in complaint.attachments:
        if attachment.attachment_type != AttachmentType.EVIDENCE:
            continue
        if len(images) >= MAX_INLINE_IMAGES:
            break
        try:
            path = resolve_stored_path(attachment.file_path)
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > budget:
                continue
            data = path.read_bytes()
        except Exception:
            current_app.logger.warning(
                "Could not embed attachment %s in email", attachment.id
            )
            continue

        budget -= size
        images.append(
            InlineImage(
                cid=f"evidence{attachment.id}@campuscare",
                data=data,
                mime=attachment.file_type,
                filename=attachment.file_name,
            )
        )

    return images


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


def send_otp(user, code: str):
    """Email a one-time code.

    ``code`` is passed straight to the template and is never logged or stored
    in plaintext anywhere.
    """
    ttl_minutes = max(1, current_app.config["OTP_TTL_SECONDS"] // 60)
    return send_email(
        user.email,
        f"{current_app.config['APP_NAME']} verification code",
        "otp",
        {"user": user, "otp_code": code, "ttl_minutes": ttl_minutes},
        user=user,
    )


# --------------------------------------------------------------------------
# Staff lifecycle
# --------------------------------------------------------------------------


def notify_admin_new_staff(staff):
    """Tell the administrator that someone is waiting for authorization."""
    app_name = current_app.config["APP_NAME"]
    return send_to_admin(
        f"{app_name} - New Staff Registration Requires Authorization",
        "staff_registration_admin",
        {
            "staff": staff,
            "registered_at": _format(staff.created_at),
            "review_url": absolute_url("admin.staff_detail", user_id=staff.id),
        },
        user=staff,
    )


def notify_staff_authorized(staff):
    app_name = current_app.config["APP_NAME"]
    return send_email(
        staff.email,
        f"{app_name} - Staff Account Authorized",
        "staff_authorized",
        {"staff": staff, "login_url": absolute_url("auth.login")},
        user=staff,
    )


def notify_staff_rejected(staff, reason: str | None = None):
    app_name = current_app.config["APP_NAME"]
    return send_email(
        staff.email,
        f"{app_name} - Staff Registration Update",
        "staff_rejected",
        {
            "staff": staff,
            "reason": reason,
            "status_label": AUTHORIZATION_LABELS.get(
                staff.authorization_status, staff.authorization_status
            ),
        },
        user=staff,
    )


def notify_staff_account_created(staff, setup_url: str):
    """Invite a staff member an admin created to set their own password."""
    app_name = current_app.config["APP_NAME"]
    ttl_hours = max(1, current_app.config["ACTION_TOKEN_TTL_SECONDS"] // 3600)
    return send_email(
        staff.email,
        f"{app_name} - Set Up Your Staff Account",
        "staff_account_created",
        {"staff": staff, "setup_url": setup_url, "ttl_hours": ttl_hours},
        user=staff,
    )


# --------------------------------------------------------------------------
# Complaints
# --------------------------------------------------------------------------


def notify_staff_assigned(complaint, staff):
    """Send the assigned staff member the full complaint, with its photos."""
    app_name = current_app.config["APP_NAME"]
    images = _collect_inline_evidence(complaint)

    return send_email(
        staff.email,
        f"{app_name} - New Complaint Assigned {complaint.code}",
        "complaint_assigned",
        {
            "staff": staff,
            "complaint": complaint,
            "location_text": _location(complaint),
            "submitted_on": _format(complaint.created_at),
            "due_on": _format(complaint.due_at),
            "priority_tone": PRIORITY_TONES.get(complaint.priority, "neutral"),
            "evidence_count": len(complaint.evidence),
            "complaint_url": absolute_url(
                "complaints.detail", complaint_id=complaint.id
            ),
            "dashboard_url": absolute_url("main.dashboard"),
        },
        inline_images=images,
        user=staff,
        complaint=complaint,
    )


def notify_student_resolved(complaint):
    """Ask the student to verify the work that was just marked resolved."""
    app_name = current_app.config["APP_NAME"]
    student = complaint.student
    return send_email(
        student.email,
        f"{app_name} - Complaint {complaint.code} Resolved, Please Verify",
        "complaint_resolved",
        {
            "complaint": complaint,
            "location_text": _location(complaint),
            "resolved_by": (
                complaint.assigned_staff.name
                if complaint.assigned_staff
                else "Maintenance team"
            ),
            "resolved_on": _format(complaint.resolved_at),
            "complaint_url": absolute_url(
                "complaints.detail", complaint_id=complaint.id
            ),
        },
        user=student,
        complaint=complaint,
    )


def notify_reopened(complaint, recipient):
    """Tell staff (or an admin) that a student rejected the resolution."""
    app_name = current_app.config["APP_NAME"]
    if recipient is None:
        return None
    return send_email(
        recipient.email,
        f"{app_name} - Complaint {complaint.code} Reopened",
        "complaint_reopened",
        {
            "complaint": complaint,
            "recipient": recipient,
            "location_text": _location(complaint),
            "complaint_url": absolute_url(
                "complaints.detail", complaint_id=complaint.id
            ),
        },
        user=recipient,
        complaint=complaint,
    )
