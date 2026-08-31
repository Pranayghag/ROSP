"""Complaint submission, listing and details.

Covers Phase 5 (submission), Phase 6 (photo evidence), Phase 9 (status
changes), Phase 10 (resolution photos) and Phase 11 (student verification).
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..constants import AttachmentType, Status
from ..extensions import db
from ..forms import ComplaintForm
from ..models import Category, Complaint, Location
from ..services import mailers, notifications, suggestions
from ..services.sla import apply_sla
from ..services.uploads import UploadError, store_evidence
from ..services.workflow import (
    WorkflowError,
    available_actions,
    build_timeline,
    open_complaint,
    transition,
)

bp = Blueprint("complaints", __name__, url_prefix="/complaints")


def _populate_choices(form: ComplaintForm) -> None:
    """Fill the category and location dropdowns from the database."""
    categories = db.session.scalars(
        select(Category).where(Category.is_active.is_(True)).order_by(Category.name)
    ).all()
    locations = db.session.scalars(
        select(Location).where(Location.is_active.is_(True)).order_by(Location.name)
    ).all()

    form.category_id.choices = [(c.id, c.name) for c in categories]
    form.location_id.choices = [(loc.id, loc.name) for loc in locations]
    form.max_photos = current_app.config["MAX_FILES_PER_COMPLAINT"]


def _load_visible_complaint(complaint_id: int) -> Complaint:
    """Fetch a complaint the current user may see, or abort with 403/404."""
    complaint = db.session.scalar(
        select(Complaint)
        .where(Complaint.id == complaint_id)
        .options(
            selectinload(Complaint.attachments),
            selectinload(Complaint.events),
        )
    )
    if complaint is None:
        abort(404)
    if not complaint.is_visible_to(current_user):
        abort(403)
    return complaint


@bp.route("/")
@login_required
def index():
    """Complaint history: a student's own, or a staff member's assigned queue."""
    query = select(Complaint).options(selectinload(Complaint.attachments))

    if current_user.is_student:
        query = query.where(Complaint.student_id == current_user.id)
        heading = "My Complaints"
    elif current_user.is_staff:
        query = query.where(Complaint.assigned_staff_id == current_user.id)
        heading = "Assigned to Me"
    else:
        heading = "All Complaints"

    status_filter = request.args.get("status", "").upper()
    if status_filter in Status.ALL:
        query = query.where(Complaint.status == status_filter)

    complaints = db.session.scalars(query.order_by(Complaint.created_at.desc())).all()
    return render_template(
        "complaints/list.html",
        complaints=complaints,
        heading=heading,
        status_filter=status_filter,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    """Submit a new complaint, with up to N photos of evidence."""
    if not (current_user.is_student or current_user.is_admin):
        abort(403)

    form = ComplaintForm()
    _populate_choices(form)

    if not form.category_id.choices or not form.location_id.choices:
        flash(
            "No categories or locations are configured yet. Run the seed script.",
            "warning",
        )

    if form.validate_on_submit():
        complaint = Complaint(
            title=form.title.data.strip(),
            description=form.description.data.strip(),
            category_id=form.category_id.data,
            location_id=form.location_id.data,
            # The student's own choice is what gets stored. Any suggestion the
            # UI offered was advisory only -- see services/suggestions.py.
            priority=form.priority.data,
            status=Status.PENDING,
            student_id=current_user.id,
        )
        db.session.add(complaint)

        try:
            # flush() assigns the primary key so the complaint code and the
            # attachment rows can reference it, without committing yet.
            db.session.flush()
            complaint.assign_code()
            apply_sla(complaint)

            for attachment in store_evidence(
                form.photos.data, complaint, current_user.id
            ):
                db.session.add(attachment)

            open_complaint(complaint, current_user)
            notifications.notify_admins(
                f"New complaint {complaint.code}: {complaint.title}", complaint
            )
            db.session.commit()

        except UploadError as exc:
            # Nothing is written: store_evidence removes any partial files and
            # the rollback discards the complaint row itself.
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("complaints/new.html", form=form)

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create complaint")
            flash("Could not submit the complaint. Please try again.", "danger")
            return render_template("complaints/new.html", form=form)

        photo_count = len(complaint.attachments)
        flash(
            f"Complaint {complaint.code} submitted"
            + (f" with {photo_count} photo(s)." if photo_count else "."),
            "success",
        )
        return redirect(url_for("complaints.detail", complaint_id=complaint.id))

    return render_template("complaints/new.html", form=form)


@bp.route("/<int:complaint_id>")
@login_required
def detail(complaint_id: int):
    """Complaint details: evidence gallery, status, timeline and actions."""
    complaint = _load_visible_complaint(complaint_id)
    return render_template(
        "complaints/detail.html",
        complaint=complaint,
        timeline=build_timeline(complaint),
        actions=available_actions(complaint, current_user),
    )


@bp.route("/<int:complaint_id>/status", methods=["POST"])
@login_required
def update_status(complaint_id: int):
    """Apply a status transition (Phase 9) or a student verdict (Phase 11)."""
    complaint = _load_visible_complaint(complaint_id)
    new_status = (request.form.get("status") or "").upper()
    note = (request.form.get("note") or "").strip() or None

    email_log = None

    try:
        # --- Staff finishing the work ------------------------------------
        if new_status == Status.RESOLVED:
            resolution_note = (request.form.get("resolution_note") or "").strip()
            if len(resolution_note) < 10:
                flash(
                    "Describe what you did before marking the complaint resolved "
                    "(at least 10 characters).",
                    "danger",
                )
                return redirect(url_for("complaints.detail", complaint_id=complaint.id))
            complaint.resolution_note = resolution_note

        # --- Student saying it is still broken ---------------------------
        if new_status == Status.REOPENED:
            reason = (request.form.get("reopen_reason") or "").strip()
            if len(reason) < 5:
                flash("Please say what is still wrong.", "danger")
                return redirect(url_for("complaints.detail", complaint_id=complaint.id))
            complaint.reopen_reason = reason
            note = note or reason

        transition(complaint, new_status, current_user, note)

        # Proof-of-repair photos arrive in the same submit as the resolution.
        if new_status == Status.RESOLVED:
            _attach_resolution_photos(complaint)

        notifications.notify_complaint_parties(
            complaint,
            f"{complaint.code} is now {complaint.status.replace('_', ' ').title()}.",
            exclude_user_id=current_user.id,
        )

        # --- Emails ------------------------------------------------------
        if new_status == Status.RESOLVED:
            notifications.notify(
                complaint.student_id,
                f"{complaint.code} has been marked as resolved. "
                "Please verify the resolution.",
                complaint,
            )
            email_log = mailers.notify_student_resolved(complaint)

        elif new_status == Status.REOPENED:
            email_log = mailers.notify_reopened(complaint, complaint.assigned_staff)
            notifications.notify_admins(
                f"{complaint.code} was reopened by the student.", complaint
            )

        db.session.commit()

        if email_log is not None and not email_log.was_delivered:
            flash(
                "Complaint updated, but the notification email could NOT be sent.",
                "warning",
            )
        else:
            flash("Complaint updated.", "success")

    except (WorkflowError, UploadError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Status update failed for complaint %s", complaint_id)
        flash("Could not update the complaint. Please try again.", "danger")

    return redirect(url_for("complaints.detail", complaint_id=complaint.id))


def _attach_resolution_photos(complaint: Complaint) -> None:
    """Store staff 'after' photos against a complaint (Phase 10).

    Uses exactly the same validation pipeline as student evidence; only the
    ``attachment_type`` differs.
    """
    photos = request.files.getlist("resolution_photos")
    for attachment in store_evidence(photos, complaint, current_user.id):
        attachment.attachment_type = AttachmentType.RESOLUTION
        db.session.add(attachment)


@bp.route("/suggest", methods=["POST"])
@login_required
def suggest():
    """Return advisory category/priority hints for the text typed so far.

    This endpoint never modifies anything. The response is rendered as a
    dismissible hint next to the dropdowns; the student remains free to submit
    whatever they chose.
    """
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", ""))[:200]
    description = str(payload.get("description", ""))[:5000]

    result = suggestions.suggest(title, description)
    category, priority = result["category"], result["priority"]

    category_id = None
    if category.is_useful:
        match = db.session.scalar(select(Category).where(Category.name == category.value))
        category_id = match.id if match else None

    return jsonify(
        {
            "category": {
                "value": category.value if category.is_useful else None,
                "id": category_id,
                "confidence": round(category.confidence, 2),
                "reason": category.reason,
            },
            "priority": {
                "value": priority.value if priority.is_useful else None,
                "confidence": round(priority.confidence, 2),
                "reason": priority.reason,
            },
            # Restated in the payload so any future client cannot mistake these
            # for values to apply automatically.
            "advisory_only": True,
        }
    )
