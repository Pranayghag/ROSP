"""Authenticated serving of complaint evidence.

Uploaded photos are deliberately **not** exposed as static files. If they were
served from a public ``/uploads/`` URL, anyone who guessed or was shown a URL
could read another student's evidence. Instead every image is fetched by its
database id through this blueprint, which re-checks permissions on each request:

    /evidence/42        ->  403 unless the caller is the complaint's student,
                            the assigned staff member, or an admin.

The stored filesystem path never appears in a URL, a page, or an error message.
"""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, abort, current_app, send_file
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Attachment
from ..services.uploads import UploadError, delete_stored_file, read_attachment

bp = Blueprint("attachments", __name__, url_prefix="/evidence")


def _load_visible_attachment(attachment_id: int) -> Attachment:
    """Fetch an attachment the current user is allowed to see, or abort."""
    attachment = db.session.get(Attachment, attachment_id)
    if attachment is None:
        abort(404)
    if not attachment.complaint.is_visible_to(current_user):
        # 403 rather than 404: the caller is authenticated, so there is no
        # enumeration benefit in hiding existence, and 403 is the honest status.
        abort(403)
    return attachment


@bp.route("/<int:attachment_id>")
@login_required
def view(attachment_id: int):
    """Stream one evidence image to an authorised viewer."""
    attachment = _load_visible_attachment(attachment_id)

    # Read through the storage backend rather than from disk directly. On a
    # host with an ephemeral filesystem the bytes live in object storage, and
    # they are fetched here -- server-side, after the permission check above --
    # so a photo never has a URL anyone could share.
    try:
        data = read_attachment(attachment)
    except UploadError as exc:
        current_app.logger.warning(
            "Could not read attachment %s: %s", attachment.id, exc
        )
        abort(404)

    response = send_file(
        BytesIO(data),
        mimetype=attachment.file_type,
        as_attachment=False,
        download_name=attachment.file_name,
        conditional=False,
    )
    # Stop a browser from re-interpreting the bytes as anything but the declared
    # image type, and keep evidence out of shared/proxy caches.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@bp.route("/<int:attachment_id>/delete", methods=["POST"])
@login_required
def delete(attachment_id: int):
    """Let a student remove a photo from a complaint that is still pending.

    Once staff have begun work the evidence is frozen, so it cannot be pulled
    out from under them.
    """
    from ..constants import Status

    attachment = _load_visible_attachment(attachment_id)
    complaint = attachment.complaint

    is_owner = current_user.id == complaint.student_id
    if not (is_owner or current_user.is_admin):
        abort(403)
    if not current_user.is_admin and complaint.status != Status.PENDING:
        abort(403)

    delete_stored_file(attachment)
    db.session.delete(attachment)
    db.session.commit()

    from flask import flash, redirect, url_for

    flash("Photo removed from the complaint.", "info")
    return redirect(url_for("complaints.detail", complaint_id=complaint.id))
