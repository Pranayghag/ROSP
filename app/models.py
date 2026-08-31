"""Database models.

The ``attachments`` table mirrors the project specification exactly:
``id, complaint_id, file_name, file_path, file_type, file_size, uploaded_by,
uploaded_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .constants import (
    AUTHORIZATION_MESSAGES,
    AttachmentType,
    AuthorizationStatus,
    Priority,
    Role,
    Status,
)
from .extensions import db, login_manager


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp used as the default for every date column."""
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Treat a naive timestamp from the database as UTC.

    MySQL DATETIME columns carry no timezone, so values read back are naive
    while ``utcnow()`` is aware. Comparing the two directly raises TypeError.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(190), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=Role.STUDENT, index=True)
    department = db.Column(db.String(120))
    roll_no = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # --- Staff profile ----------------------------------------------------
    phone = db.Column(db.String(30))
    designation = db.Column(db.String(60))
    staff_id = db.Column(db.String(50))

    # --- Authorization ----------------------------------------------------
    #: Students are AUTHORIZED on creation; staff start PENDING and must be
    #: approved by an administrator. Set once and stored permanently -- signing
    #: in never changes it.
    authorization_status = db.Column(
        db.String(20),
        nullable=False,
        default=AuthorizationStatus.AUTHORIZED,
        index=True,
    )
    authorized_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    authorized_at = db.Column(db.DateTime)
    #: Why an admin rejected or suspended the account, shown back to them.
    authorization_note = db.Column(db.String(500))

    #: True once the account holder has chosen their own password. Accounts
    #: created by an admin start False and are sent a one-time setup link.
    password_set = db.Column(db.Boolean, nullable=False, default=True)

    authorizer = db.relationship("User", remote_side=[id], foreign_keys=[authorized_by])

    complaints = db.relationship(
        "Complaint",
        back_populates="student",
        foreign_keys="Complaint.student_id",
        cascade="all, delete-orphan",
    )
    assigned_complaints = db.relationship(
        "Complaint",
        back_populates="assigned_staff",
        foreign_keys="Complaint.assigned_staff_id",
    )

    # --- Passwords --------------------------------------------------------
    def set_password(self, raw_password: str) -> None:
        """Store a salted PBKDF2 hash. The plaintext is never persisted."""
        from flask import current_app, has_app_context

        method = (
            current_app.config.get("PASSWORD_HASH_METHOD") if has_app_context() else None
        )
        self.password_hash = (
            generate_password_hash(raw_password, method=method)
            if method
            else generate_password_hash(raw_password)
        )

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    # --- Role helpers -----------------------------------------------------
    @property
    def is_student(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def is_staff(self) -> bool:
        return self.role == Role.STAFF

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    # --- Authorization helpers -------------------------------------------
    @property
    def is_authorized(self) -> bool:
        """Whether this account may be used at all."""
        return self.authorization_status == AuthorizationStatus.AUTHORIZED

    @property
    def is_pending(self) -> bool:
        return self.authorization_status == AuthorizationStatus.PENDING

    @property
    def blocked_reason(self) -> str | None:
        """Message to show someone who cannot sign in, or None if they can."""
        if self.is_authorized:
            return None
        return AUTHORIZATION_MESSAGES.get(
            self.authorization_status, "This account cannot be used."
        )

    @property
    def can_be_assigned(self) -> bool:
        """Only authorized staff may receive complaints."""
        return self.role in (Role.STAFF, Role.ADMIN) and self.is_authorized

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} ({self.role})>"


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    #: Service-level target in hours, used to compute ``Complaint.due_at``.
    sla_hours = db.Column(db.Integer, nullable=False, default=72)

    complaints = db.relationship("Complaint", back_populates="category")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Category {self.name}>"


class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    building = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    complaints = db.relationship("Complaint", back_populates="location")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Location {self.name}>"


class Complaint(db.Model):
    __tablename__ = "complaints"

    id = db.Column(db.Integer, primary_key=True)
    #: Human-friendly reference such as CC102, assigned right after insert.
    code = db.Column(db.String(20), unique=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=False)

    priority = db.Column(db.String(20), nullable=False, default=Priority.MEDIUM)
    status = db.Column(db.String(30), nullable=False, default=Status.PENDING, index=True)

    student_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    assigned_staff_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    resolved_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)

    # --- SLA tracking (Phase 13) -----------------------------------------
    #: When this complaint is due, derived from its category's ``sla_hours``.
    due_at = db.Column(db.DateTime)
    #: Set the first time the complaint is escalated for breaching its SLA.
    escalated_at = db.Column(db.DateTime)

    # --- Resolution -------------------------------------------------------
    #: What the staff member did, written when they mark the work resolved.
    resolution_note = db.Column(db.Text)
    #: Why the student said the problem was not actually fixed.
    reopen_reason = db.Column(db.String(500))

    category = db.relationship("Category", back_populates="complaints")
    location = db.relationship("Location", back_populates="complaints")
    student = db.relationship(
        "User", back_populates="complaints", foreign_keys=[student_id]
    )
    assigned_staff = db.relationship(
        "User", back_populates="assigned_complaints", foreign_keys=[assigned_staff_id]
    )

    attachments = db.relationship(
        "Attachment",
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="Attachment.id",
    )
    events = db.relationship(
        "ComplaintEvent",
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="ComplaintEvent.created_at",
    )

    def assign_code(self) -> None:
        """Derive the public complaint code from the primary key.

        Call after ``db.session.flush()`` so ``self.id`` is populated.
        """
        if self.id and not self.code:
            self.code = f"CC{100 + self.id}"

    def is_visible_to(self, user) -> bool:
        """Authorisation rule for the complaint *and* its evidence.

        A complaint -- and therefore every photo attached to it -- may be
        viewed by the student who filed it, the staff member it is assigned to,
        and any admin. Nobody else.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if user.is_admin:
            return True
        if user.id == self.student_id:
            return True
        return bool(self.assigned_staff_id) and user.id == self.assigned_staff_id

    # --- SLA helpers ------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.status not in (Status.CLOSED,)

    @property
    def is_overdue(self) -> bool:
        """True when an open complaint has passed its SLA deadline."""
        if not self.due_at or not self.is_open:
            return False
        return _as_utc(self.due_at) < utcnow()

    @property
    def hours_remaining(self) -> float | None:
        """Hours left before the SLA deadline; negative once breached."""
        if not self.due_at or not self.is_open:
            return None
        delta = _as_utc(self.due_at) - utcnow()
        return round(delta.total_seconds() / 3600, 1)

    @property
    def evidence(self) -> list[Attachment]:
        """Photos submitted by the student as evidence of the problem."""
        return [a for a in self.attachments if a.attachment_type == AttachmentType.EVIDENCE]

    @property
    def resolution_photos(self) -> list[Attachment]:
        """Photos uploaded by staff showing the completed fix (Phase 10)."""
        return [a for a in self.attachments if a.attachment_type == AttachmentType.RESOLUTION]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Complaint {self.code or self.id} {self.status}>"


class Attachment(db.Model):
    """A single piece of photographic evidence attached to a complaint.

    ``file_name`` keeps the sanitised *original* name purely so the UI can show
    the student what they uploaded. ``file_path`` is the randomly-generated
    path relative to ``UPLOAD_DIR`` and is never rendered to a user -- images
    are served by attachment id through an authorisation check instead.
    """

    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(
        db.Integer,
        db.ForeignKey("complaints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(100), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    #: EVIDENCE (student, at filing) or RESOLUTION (staff, proof of repair).
    #: Beyond the base specification, this supports the after-photo workflow.
    attachment_type = db.Column(
        db.String(20), nullable=False, default=AttachmentType.EVIDENCE, index=True
    )

    complaint = db.relationship("Complaint", back_populates="attachments")
    uploader = db.relationship("User")

    @property
    def size_display(self) -> str:
        """File size formatted for humans (e.g. 1.4 MB)."""
        kb = self.file_size / 1024
        return f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Attachment {self.id} of complaint {self.complaint_id}>"


class ComplaintEvent(db.Model):
    """Append-only audit trail powering the complaint timeline."""

    __tablename__ = "complaint_events"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(
        db.Integer,
        db.ForeignKey("complaints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(30), nullable=False)
    note = db.Column(db.String(500))
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    complaint = db.relationship("Complaint", back_populates="events")
    actor = db.relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.status} on {self.complaint_id}>"


class Notification(db.Model):
    """In-app notification delivered to a single user (Phase 12).

    Kept deliberately simple: notifications are written whenever a complaint
    changes hands or status, and are marked read when the user opens them.
    """

    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    complaint_id = db.Column(
        db.Integer, db.ForeignKey("complaints.id", ondelete="CASCADE"), index=True
    )
    message = db.Column(db.String(300), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")
    complaint = db.relationship("Complaint")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification to {self.user_id}: {self.message[:30]}>"


class OtpCode(db.Model):
    """A one-time password issued during two-step verification.

    The code itself is never stored. Only a salted hash is kept, so a database
    leak cannot be replayed, and the plaintext exists only in the email that
    was sent and in memory for the length of one request.
    """

    __tablename__ = "otp_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    otp_hash = db.Column(db.String(255), nullable=False)
    purpose = db.Column(db.String(30), nullable=False, default="LOGIN", index=True)

    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    used = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    consumed_at = db.Column(db.DateTime)

    user = db.relationship("User")

    @property
    def is_expired(self) -> bool:
        return _as_utc(self.expires_at) < utcnow()

    @property
    def is_usable(self) -> bool:
        """A code can be checked only while unused, unexpired and under budget."""
        from flask import current_app

        max_attempts = current_app.config.get("OTP_MAX_ATTEMPTS", 5)
        return not self.used and not self.is_expired and self.attempts < max_attempts

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OtpCode user={self.user_id} used={self.used}>"


class EmailLog(db.Model):
    """Record of every message the application tried to send.

    Written whether or not delivery succeeded, and whether or not SMTP is
    configured at all, so it is always possible to answer "was this actually
    sent?" honestly. Bodies are not stored -- only metadata.
    """

    __tablename__ = "email_logs"

    #: Delivery outcomes.
    SENT = "SENT"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"

    id = db.Column(db.Integer, primary_key=True)
    to_address = db.Column(db.String(190), nullable=False, index=True)
    subject = db.Column(db.String(255), nullable=False)
    template = db.Column(db.String(60))
    status = db.Column(db.String(20), nullable=False, index=True)
    error = db.Column(db.String(500))

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    complaint_id = db.Column(
        db.Integer, db.ForeignKey("complaints.id", ondelete="SET NULL")
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User")
    complaint = db.relationship("Complaint")

    @property
    def was_delivered(self) -> bool:
        return self.status == EmailLog.SENT

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EmailLog {self.status} to={self.to_address}>"
