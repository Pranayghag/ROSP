"""Who can see a complaint, and who can see its photos.

The specification requires that students see photos on their own complaints,
that admins and the assigned staff member can see them too, and that nobody
else can. These tests pin that down at the HTTP level, because the permission
check has to hold on the URL a browser actually requests -- not just in the
model layer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.constants import Status
from app.extensions import db
from app.models import Attachment, Category, Complaint, Location
from app.services.uploads import store_evidence
from tests.conftest import make_image_bytes, make_upload


@pytest.fixture
def complaint_with_photo(app, users):
    """A complaint filed by student1, assigned to staff1, with one photo."""
    category = db.session.scalar(select(Category))
    location = db.session.scalar(select(Location))

    complaint = Complaint(
        title="Water leaking from the ceiling",
        description="Water is dripping onto the back benches in Room 204.",
        category_id=category.id,
        location_id=location.id,
        student_id=users["student1"].id,
        assigned_staff_id=users["staff1"].id,
        status=Status.ASSIGNED,
    )
    db.session.add(complaint)
    db.session.flush()
    complaint.assign_code()

    with app.test_request_context():
        for attachment in store_evidence(
            [make_upload(make_image_bytes(), "leak.jpg")],
            complaint,
            users["student1"].id,
        ):
            db.session.add(attachment)

    db.session.commit()
    return complaint


@pytest.fixture
def attachment_id(complaint_with_photo):
    return db.session.scalar(select(Attachment.id))


# --------------------------------------------------------------------------
# Evidence files
# --------------------------------------------------------------------------


def test_anonymous_user_cannot_fetch_evidence(client, attachment_id):
    response = client.get(f"/evidence/{attachment_id}")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_owner_can_fetch_own_evidence(client, login, attachment_id):
    login("student1@rosp.edu")
    response = client.get(f"/evidence/{attachment_id}")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert len(response.data) > 0


def test_unrelated_student_cannot_fetch_evidence(client, login, attachment_id):
    """The core requirement: another student must not read this photo."""
    login("student2@rosp.edu")
    response = client.get(f"/evidence/{attachment_id}")

    assert response.status_code == 403


def test_assigned_staff_can_fetch_evidence(client, login, attachment_id):
    login("staff1@rosp.edu")
    response = client.get(f"/evidence/{attachment_id}")

    assert response.status_code == 200


def test_unassigned_staff_cannot_fetch_evidence(client, login, attachment_id):
    """Being staff is not enough -- the complaint must be assigned to you."""
    login("staff2@rosp.edu")
    response = client.get(f"/evidence/{attachment_id}")

    assert response.status_code == 403


def test_admin_can_fetch_any_evidence(client, login, attachment_id):
    login("admin@rosp.edu")
    response = client.get(f"/evidence/{attachment_id}")

    assert response.status_code == 200


def test_missing_attachment_returns_404(client, login):
    login("admin@rosp.edu")
    assert client.get("/evidence/999999").status_code == 404


def test_evidence_response_sets_protective_headers(client, login, attachment_id):
    login("student1@rosp.edu")
    response = client.get(f"/evidence/{attachment_id}")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "private" in response.headers["Cache-Control"]


def test_uploads_are_not_served_as_static_files(client, login, app, attachment_id):
    """There must be no public URL that maps onto the upload directory."""
    stored = db.session.get(Attachment, attachment_id).file_path

    login("student2@rosp.edu")
    for candidate in (f"/uploads/{stored}", f"/static/uploads/{stored}", f"/{stored}"):
        assert client.get(candidate).status_code in (403, 404)


# --------------------------------------------------------------------------
# Complaint pages
# --------------------------------------------------------------------------


def test_unrelated_student_cannot_open_complaint(client, login, complaint_with_photo):
    login("student2@rosp.edu")
    response = client.get(f"/complaints/{complaint_with_photo.id}")

    assert response.status_code == 403


def test_owner_sees_evidence_on_detail_page(client, login, complaint_with_photo, attachment_id):
    login("student1@rosp.edu")
    response = client.get(f"/complaints/{complaint_with_photo.id}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f"/evidence/{attachment_id}" in body
    assert complaint_with_photo.code in body


def test_detail_page_never_leaks_the_filesystem_path(
    client, login, app, complaint_with_photo, attachment_id
):
    """The stored path and the server's upload directory must stay private."""
    stored = db.session.get(Attachment, attachment_id).file_path

    login("student1@rosp.edu")
    body = client.get(f"/complaints/{complaint_with_photo.id}").get_data(as_text=True)

    assert stored not in body
    assert str(app.config["UPLOAD_DIR"]) not in body
    # The random stored filename must not appear anywhere either.
    assert stored.rsplit("/", 1)[-1] not in body


def test_admin_only_pages_reject_students(client, login):
    login("student1@rosp.edu")

    for path in ("/admin/", "/admin/complaints", "/admin/analytics", "/admin/users"):
        assert client.get(path).status_code == 403, path


def test_admin_only_pages_reject_staff(client, login):
    login("staff1@rosp.edu")
    assert client.get("/admin/").status_code == 403


# --------------------------------------------------------------------------
# Deleting evidence
# --------------------------------------------------------------------------


def test_other_student_cannot_delete_evidence(client, login, attachment_id):
    login("student2@rosp.edu")
    response = client.post(f"/evidence/{attachment_id}/delete")

    assert response.status_code == 403
    assert db.session.get(Attachment, attachment_id) is not None


def test_owner_cannot_delete_once_work_has_started(client, login, attachment_id):
    """Evidence is frozen after a complaint leaves PENDING."""
    login("student1@rosp.edu")
    response = client.post(f"/evidence/{attachment_id}/delete")

    assert response.status_code == 403
    assert db.session.get(Attachment, attachment_id) is not None


def test_owner_can_delete_while_pending(
    client, login, app, complaint_with_photo, attachment_id
):
    complaint_with_photo.status = Status.PENDING
    db.session.commit()

    stored_path = db.session.get(Attachment, attachment_id).file_path
    on_disk = app.config["UPLOAD_DIR"] / stored_path
    assert on_disk.is_file()

    login("student1@rosp.edu")
    response = client.post(f"/evidence/{attachment_id}/delete", follow_redirects=True)

    assert response.status_code == 200
    assert db.session.get(Attachment, attachment_id) is None
    assert not on_disk.exists(), "the file itself must be removed, not just the row"
