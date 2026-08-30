"""Landing page, role-aware dashboards and notifications.

Covers Phase 4 (student dashboard), Phase 8 (staff dashboard) and the
notification inbox from Phase 12.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from ..constants import Status
from ..extensions import db
from ..models import Complaint
from ..services import notifications

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("index.html")


def _status_counts(base_filter) -> dict[str, int]:
    """Count complaints per status for a given ownership filter."""
    rows = db.session.execute(
        select(Complaint.status, func.count(Complaint.id))
        .where(base_filter)
        .group_by(Complaint.status)
    ).all()
    counts = {status: 0 for status in Status.ALL}
    for status, count in rows:
        counts[status] = count
    counts["TOTAL"] = sum(counts[s] for s in Status.ALL)
    counts["OPEN"] = counts["TOTAL"] - counts[Status.CLOSED]
    return counts


@bp.route("/dashboard")
@login_required
def dashboard():
    """Send each role to the view that matches their job."""
    if current_user.is_admin:
        return redirect(url_for("admin.dashboard"))

    if current_user.is_staff:
        ownership = Complaint.assigned_staff_id == current_user.id
        template = "staff_dashboard.html"
    else:
        ownership = Complaint.student_id == current_user.id
        template = "student_dashboard.html"

    recent = db.session.scalars(
        select(Complaint).where(ownership).order_by(Complaint.created_at.desc()).limit(5)
    ).all()

    active = db.session.scalars(
        select(Complaint)
        .where(ownership, Complaint.status != Status.CLOSED)
        .order_by(Complaint.created_at.asc())
    ).all()

    return render_template(
        template,
        counts=_status_counts(ownership),
        recent=recent,
        overdue=[c for c in active if c.is_overdue],
        awaiting_me=[
            c
            for c in active
            if (current_user.is_student and c.status == Status.STUDENT_VERIFICATION)
            or (current_user.is_staff and c.status in (Status.ASSIGNED, Status.IN_PROGRESS))
        ],
    )


@bp.route("/notifications")
@login_required
def notification_list():
    items = notifications.recent_for(current_user)
    return render_template("notifications.html", notifications=items)


@bp.route("/notifications/read", methods=["POST"])
@login_required
def mark_notifications_read():
    count = notifications.mark_all_read(current_user)
    db.session.commit()
    flash(f"Marked {count} notification(s) as read.", "info")
    return redirect(url_for("main.notification_list"))
