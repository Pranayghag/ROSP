"""Administrator views: queue, assignment, escalation and analytics.

Covers Phase 7 (admin dashboard), Phase 9 (assignment) and Phase 15 (analytics).
"""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..constants import Priority, Role, Status
from ..decorators import admin_required
from ..extensions import db
from ..forms import AssignForm
from ..models import Attachment, Category, Complaint, User, _as_utc, utcnow
from ..services import notifications
from ..services.sla import escalate_overdue, overdue_complaints
from ..services.workflow import WorkflowError, assign

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _staff_choices() -> list[tuple[int, str]]:
    staff = db.session.scalars(
        select(User)
        .where(User.role.in_([Role.STAFF, Role.ADMIN]))
        .order_by(User.name)
    ).all()
    return [
        (member.id, f"{member.name}" + (f" - {member.department}" if member.department else ""))
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
            db.session.commit()
            flash(f"{complaint.code} assigned to {staff.name}.", "success")
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
