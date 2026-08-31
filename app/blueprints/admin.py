"""Administrator views: queue, assignment, escalation and analytics.

Covers Phase 7 (admin dashboard), Phase 9 (assignment) and Phase 15 (analytics).
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from ..constants import AuthorizationStatus, Priority, Role, Status
from ..decorators import admin_required
from ..extensions import db
from ..forms import AssignForm, StaffCreateForm, StaffDecisionForm
from ..models import Attachment, Category, Complaint, EmailLog, User, _as_utc, utcnow
from ..services import mailers, notifications
from ..services.email import absolute_url
from ..services.sla import escalate_overdue, overdue_complaints
from ..services.tokens import generate_setup_token
from ..services.workflow import WorkflowError, assign

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _staff_choices() -> list[tuple[int, str]]:
    """Staff who may be given a complaint.

    Only AUTHORIZED accounts appear. A PENDING, REJECTED or SUSPENDED staff
    member must never be assignable -- they cannot sign in to act on the work,
    and assigning to them would silently strand the complaint.
    """
    staff = db.session.scalars(
        select(User)
        .where(
            User.role.in_([Role.STAFF, Role.ADMIN]),
            User.authorization_status == AuthorizationStatus.AUTHORIZED,
        )
        .order_by(User.name)
    ).all()
    return [
        (
            member.id,
            member.name
            + (f" - {member.designation}" if member.designation else "")
            + (f" ({member.department})" if member.department else ""),
        )
        for member in staff
    ]


@bp.route("/")
@login_required
@admin_required
def dashboard():
    """Headline numbers plus the queues that need an admin's attention."""
    status_rows = db.session.execute(
        select(Complaint.status, func.count(Complaint.id)).group_by(Complaint.status)
    ).all()
    counts = {status: 0 for status in Status.ALL}
    for status, count in status_rows:
        counts[status] = count
    counts["TOTAL"] = sum(counts[s] for s in Status.ALL)
    counts["OPEN"] = counts["TOTAL"] - counts[Status.CLOSED]

    unassigned = db.session.scalars(
        select(Complaint)
        .where(Complaint.assigned_staff_id.is_(None), Complaint.status == Status.PENDING)
        .order_by(Complaint.created_at.asc())
    ).all()

    return render_template(
        "admin/dashboard.html",
        counts=counts,
        unassigned=unassigned,
        overdue=overdue_complaints(),
        total_evidence=db.session.scalar(select(func.count(Attachment.id))) or 0,
    )


@bp.route("/complaints")
@login_required
@admin_required
def queue():
    """Every complaint, with filters for status, priority and category."""
    query = select(Complaint).options(selectinload(Complaint.attachments))

    status_filter = request.args.get("status", "").upper()
    priority_filter = request.args.get("priority", "").upper()
    category_filter = request.args.get("category", type=int)
    unassigned_only = request.args.get("unassigned") == "1"

    if status_filter in Status.ALL:
        query = query.where(Complaint.status == status_filter)
    if priority_filter in Priority.ALL:
        query = query.where(Complaint.priority == priority_filter)
    if category_filter:
        query = query.where(Complaint.category_id == category_filter)
    if unassigned_only:
        query = query.where(Complaint.assigned_staff_id.is_(None))

    complaints = db.session.scalars(query.order_by(Complaint.created_at.desc())).all()
    categories = db.session.scalars(select(Category).order_by(Category.name)).all()

    return render_template(
        "admin/queue.html",
        complaints=complaints,
        categories=categories,
        status_filter=status_filter,
        priority_filter=priority_filter,
        category_filter=category_filter,
        unassigned_only=unassigned_only,
    )


@bp.route("/complaints/<int:complaint_id>/assign", methods=["GET", "POST"])
@login_required
@admin_required
def assign_complaint(complaint_id: int):
    """Hand a complaint to a staff member (Phase 9)."""
    complaint = db.session.get(Complaint, complaint_id)
    if complaint is None:
        return redirect(url_for("admin.queue"))

    form = AssignForm()
    form.staff_id.choices = _staff_choices()

    if not form.staff_id.choices:
        flash("No staff accounts exist yet. Create one before assigning.", "warning")
        return redirect(url_for("admin.queue"))

    if form.validate_on_submit():
        staff = db.session.get(User, form.staff_id.data)
        try:
            assign(complaint, staff, current_user, form.note.data or None)
            notifications.notify(
                staff.id,
                f"You have been assigned {complaint.code}: {complaint.title}",
                complaint,
            )
            notifications.notify(
                complaint.student_id,
                f"{complaint.code} has been assigned to {staff.name}.",
                complaint,
            )
            # Full complaint details, with the evidence photos embedded.
            log = mailers.notify_staff_assigned(complaint, staff)
            db.session.commit()

            if log and log.was_delivered:
                flash(
                    f"{complaint.code} assigned to {staff.name}, who has been "
                    f"emailed at {staff.email}.",
                    "success",
                )
            else:
                # Say plainly that the email did not go out. The assignment
                # itself is saved either way.
                flash(
                    f"{complaint.code} assigned to {staff.name}, but the "
                    f"notification email could NOT be sent. They have an "
                    f"in-app notification.",
                    "warning",
                )
            return redirect(url_for("complaints.detail", complaint_id=complaint.id))
        except WorkflowError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template("admin/assign.html", form=form, complaint=complaint)


@bp.route("/escalate", methods=["POST"])
@login_required
@admin_required
def escalate():
    """Escalate every complaint that has breached its SLA (Phase 13)."""
    escalated = escalate_overdue()
    db.session.commit()
    if escalated:
        flash(f"Escalated {len(escalated)} overdue complaint(s).", "warning")
    else:
        flash("No new SLA breaches to escalate.", "info")
    return redirect(url_for("admin.dashboard"))


@bp.route("/analytics")
@login_required
@admin_required
def analytics():
    """Reports and graphs (Phase 15).

    Every figure is computed server-side and rendered as CSS/SVG, so the page
    needs no charting library and works with no internet connection.
    """
    by_status = dict(
        db.session.execute(
            select(Complaint.status, func.count(Complaint.id)).group_by(Complaint.status)
        ).all()
    )
    by_priority = dict(
        db.session.execute(
            select(Complaint.priority, func.count(Complaint.id)).group_by(
                Complaint.priority
            )
        ).all()
    )
    by_category = db.session.execute(
        select(Category.name, func.count(Complaint.id))
        .select_from(Category)
        .outerjoin(Complaint, Complaint.category_id == Category.id)
        .group_by(Category.name)
        .order_by(func.count(Complaint.id).desc())
    ).all()

    # Average time from submission to resolution, in hours.
    resolved = db.session.scalars(
        select(Complaint).where(Complaint.resolved_at.is_not(None))
    ).all()
    durations = [
        (_as_utc(c.resolved_at) - _as_utc(c.created_at)).total_seconds() / 3600
        for c in resolved
    ]
    avg_resolution_hours = round(sum(durations) / len(durations), 1) if durations else None

    # Volume over the last 14 days, oldest first.
    today = utcnow().date()
    daily = []
    all_complaints = db.session.scalars(select(Complaint)).all()
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        count = sum(1 for c in all_complaints if _as_utc(c.created_at).date() == day)
        daily.append({"date": day, "count": count})

    total = len(all_complaints)
    closed = by_status.get(Status.CLOSED, 0)

    return render_template(
        "admin/analytics.html",
        by_status=by_status,
        by_priority=by_priority,
        by_category=by_category,
        daily=daily,
        total=total,
        closed=closed,
        resolution_rate=round(closed / total * 100) if total else 0,
        avg_resolution_hours=avg_resolution_hours,
        overdue_count=len(overdue_complaints()),
    )


@bp.route("/users")
@login_required
@admin_required
def users():
    """Directory of accounts, so an admin can see who can be assigned work."""
    everyone = db.session.scalars(select(User).order_by(User.role, User.name)).all()
    return render_template("admin/users.html", users=everyone)


# ==========================================================================
# Staff management
# ==========================================================================


@bp.route("/staff")
@login_required
@admin_required
def staff_list():
    """Every staff account, filterable by authorization status."""
    query = select(User).where(User.role.in_([Role.STAFF, Role.ADMIN]))

    status_filter = request.args.get("status", "").upper()
    search = (request.args.get("q") or "").strip()

    if status_filter in AuthorizationStatus.ALL:
        query = query.where(User.authorization_status == status_filter)

    if search:
        # Parameterised by SQLAlchemy; the raw string never reaches the SQL.
        pattern = f"%{search}%"
        query = query.where(
            or_(
                User.name.ilike(pattern),
                User.email.ilike(pattern),
                User.staff_id.ilike(pattern),
                User.department.ilike(pattern),
                User.designation.ilike(pattern),
            )
        )

    staff = db.session.scalars(
        query.order_by(User.authorization_status, User.name)
    ).all()

    # Counts for the filter chips, independent of the current filter.
    rows = db.session.execute(
        select(User.authorization_status, func.count(User.id))
        .where(User.role.in_([Role.STAFF, Role.ADMIN]))
        .group_by(User.authorization_status)
    ).all()
    counts = {status: 0 for status in AuthorizationStatus.ALL}
    for status, count in rows:
        counts[status] = count
    counts["TOTAL"] = sum(counts[s] for s in AuthorizationStatus.ALL)

    return render_template(
        "admin/staff_list.html",
        staff=staff,
        counts=counts,
        status_filter=status_filter,
        search=search,
    )


def _load_staff(user_id: int) -> User | None:
    user = db.session.get(User, user_id)
    if user is None or user.role not in (Role.STAFF, Role.ADMIN):
        return None
    return user


@bp.route("/staff/<int:user_id>")
@login_required
@admin_required
def staff_detail(user_id: int):
    """One staff member's profile, with the authorize/reject controls.

    This is the page the "Review Staff Registration" link in the admin email
    opens. It requires an authenticated admin session -- the emailed link
    carries no credentials and grants nothing by itself.
    """
    staff = _load_staff(user_id)
    if staff is None:
        flash("No such staff account.", "warning")
        return redirect(url_for("admin.staff_list"))

    form = StaffDecisionForm(decision=staff.authorization_status)

    assigned = db.session.scalars(
        select(Complaint)
        .where(Complaint.assigned_staff_id == staff.id)
        .order_by(Complaint.created_at.desc())
    ).all()

    recent_email = db.session.scalars(
        select(EmailLog)
        .where(EmailLog.user_id == staff.id)
        .order_by(EmailLog.created_at.desc())
        .limit(5)
    ).all()

    return render_template(
        "admin/staff_detail.html",
        staff=staff,
        form=form,
        assigned=assigned,
        open_count=sum(1 for c in assigned if c.is_open),
        recent_email=recent_email,
    )


@bp.route("/staff/<int:user_id>/decision", methods=["POST"])
@login_required
@admin_required
def staff_decision(user_id: int):
    """Authorize, reject, suspend or re-pend a staff account.

    The decision is stored permanently. Nothing in the sign-in path ever
    changes it, so an authorized staff member is never asked to be approved
    again -- only another explicit action here can change their status.
    """
    staff = _load_staff(user_id)
    if staff is None:
        flash("No such staff account.", "warning")
        return redirect(url_for("admin.staff_list"))

    if staff.id == current_user.id:
        flash("You cannot change your own authorization status.", "danger")
        return redirect(url_for("admin.staff_detail", user_id=staff.id))

    form = StaffDecisionForm()
    if not form.validate_on_submit():
        flash("That decision was not understood.", "danger")
        return redirect(url_for("admin.staff_detail", user_id=staff.id))

    decision = form.decision.data
    note = (form.note.data or "").strip() or None
    previous = staff.authorization_status

    staff.authorization_status = decision
    staff.authorization_note = note
    staff.authorized_by = current_user.id
    staff.authorized_at = utcnow()

    log = None
    if decision == AuthorizationStatus.AUTHORIZED and previous != decision:
        notifications.notify(
            staff.id, "Your staff account has been authorized. You can now sign in."
        )
        log = mailers.notify_staff_authorized(staff)
    elif decision in (AuthorizationStatus.REJECTED, AuthorizationStatus.SUSPENDED):
        notifications.notify(staff.id, f"Your staff account is now {decision.lower()}.")
        log = mailers.notify_staff_rejected(staff, note)

    db.session.commit()

    message = f"{staff.name} is now {decision.title()}."
    if log is not None and not log.was_delivered:
        flash(f"{message} The notification email could NOT be sent.", "warning")
    else:
        flash(message, "success")

    return redirect(url_for("admin.staff_detail", user_id=staff.id))


@bp.route("/staff/new", methods=["GET", "POST"])
@login_required
@admin_required
def staff_new():
    """Create a staff account directly.

    No password is chosen here. The account is created already AUTHORIZED but
    with ``password_set=False``, and the staff member receives a signed,
    expiring link to set their own -- so no credential is ever typed by the
    admin or carried in an email.
    """
    form = StaffCreateForm()

    if form.validate_on_submit():
        email = form.email.data.strip().lower()

        if db.session.scalar(select(User).where(User.email == email)):
            flash("An account with that email already exists.", "warning")
            return render_template("admin/staff_new.html", form=form)

        staff = User(
            name=form.name.data.strip(),
            email=email,
            role=form.role.data,
            phone=form.phone.data.strip(),
            department=form.department.data.strip(),
            designation=form.designation.data,
            staff_id=(form.staff_id.data or "").strip() or None,
            authorization_status=AuthorizationStatus.AUTHORIZED,
            authorized_by=current_user.id,
            authorized_at=utcnow(),
            password_set=False,
        )
        # A random unusable placeholder: the column is NOT NULL, and this value
        # can never be guessed or used to sign in.
        staff.set_password(secrets.token_urlsafe(32))
        db.session.add(staff)
        db.session.flush()

        setup_url = absolute_url(
            "auth.setup_account", token=generate_setup_token(staff)
        )
        log = mailers.notify_staff_account_created(staff, setup_url)
        db.session.commit()

        if log and log.was_delivered:
            flash(
                f"Account created. A setup link has been emailed to {staff.email}.",
                "success",
            )
        else:
            flash(
                f"Account created for {staff.email}, but the setup email could "
                f"NOT be sent. Use 'Resend setup link' once mail is working.",
                "warning",
            )
        return redirect(url_for("admin.staff_detail", user_id=staff.id))

    return render_template("admin/staff_new.html", form=form)


@bp.route("/staff/<int:user_id>/resend-setup", methods=["POST"])
@login_required
@admin_required
def staff_resend_setup(user_id: int):
    """Send a fresh account-setup link."""
    staff = _load_staff(user_id)
    if staff is None:
        flash("No such staff account.", "warning")
        return redirect(url_for("admin.staff_list"))

    if staff.password_set:
        flash(f"{staff.name} has already set a password.", "info")
        return redirect(url_for("admin.staff_detail", user_id=staff.id))

    setup_url = absolute_url("auth.setup_account", token=generate_setup_token(staff))
    log = mailers.notify_staff_account_created(staff, setup_url)
    db.session.commit()

    if log and log.was_delivered:
        flash(f"A new setup link was emailed to {staff.email}.", "success")
    else:
        flash("The setup email could NOT be sent. Check the mail settings.", "danger")
    return redirect(url_for("admin.staff_detail", user_id=staff.id))


@bp.route("/email-log")
@login_required
@admin_required
def email_log():
    """What the application actually tried to send, and whether it worked."""
    logs = db.session.scalars(
        select(EmailLog).order_by(EmailLog.created_at.desc()).limit(200)
    ).all()
    return render_template("admin/email_log.html", logs=logs)
