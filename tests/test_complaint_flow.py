"""End-to-end complaint lifecycle, driven through HTTP like a real browser.

Covers submission with evidence (Phases 5-6), assignment and status changes
(Phase 9), resolution photos (Phase 10) and student verification (Phase 11).
"""

from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import select

from app.constants import Priority, Status
from app.extensions import db
from app.models import Attachment, Category, Complaint, Location, Notification
from tests.conftest import make_image_bytes


@pytest.fixture
def form_ids(app):
    category = db.session.scalar(select(Category).where(Category.name == "Plumbing & Water"))
    location = db.session.scalar(select(Location))
    return {"category_id": category.id, "location_id": location.id}


def photo(name: str = "leak.jpg", fmt: str = "JPEG"):
    """A file tuple in the shape Werkzeug's test client expects."""
    return (BytesIO(make_image_bytes(fmt)), name)


def submit(client, form_ids, photos=None, **overrides):
    data = {
        "title": "Water leaking from the ceiling",
        "description": "Water is leaking from the ceiling of Room 204 onto the benches.",
        "category_id": str(form_ids["category_id"]),
        "location_id": str(form_ids["location_id"]),
        "priority": Priority.HIGH,
    }
    data.update(overrides)
    if photos:
        data["photos"] = photos

    return client.post(
        "/complaints/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------


def test_submit_complaint_without_photos(client, login, form_ids):
    login("student1@rosp.edu")
    response = submit(client, form_ids)

    assert response.status_code == 200
    complaint = db.session.scalar(select(Complaint))
    assert complaint is not None
    assert complaint.status == Status.PENDING
    assert complaint.attachments == []


def test_submit_complaint_with_photos(client, login, form_ids):
    login("student1@rosp.edu")
    response = submit(client, form_ids, photos=[photo("leak.jpg"), photo("wall.png", "PNG")])

    assert response.status_code == 200

    complaint = db.session.scalar(select(Complaint))
    assert len(complaint.attachments) == 2
    assert {a.file_name for a in complaint.attachments} == {"leak.jpg", "wall.png"}
    assert all(a.uploaded_by == complaint.student_id for a in complaint.attachments)
    assert all(a.attachment_type == "EVIDENCE" for a in complaint.attachments)


def test_complaint_gets_reference_code_and_deadline(client, login, form_ids):
    login("student1@rosp.edu")
    submit(client, form_ids)

    complaint = db.session.scalar(select(Complaint))
    assert complaint.code == f"CC{100 + complaint.id}"
    assert complaint.due_at is not None, "an SLA deadline must be stamped on creation"


def test_submission_opens_the_timeline(client, login, form_ids):
    login("student1@rosp.edu")
    submit(client, form_ids)

    complaint = db.session.scalar(select(Complaint))
    recorded = [event.status for event in complaint.events]
    assert recorded == ["SUBMITTED", Status.PENDING]


def test_submission_notifies_admins(client, login, form_ids):
    login("student1@rosp.edu")
    submit(client, form_ids)

    messages = db.session.scalars(select(Notification)).all()
    assert any("New complaint" in note.message for note in messages)


def test_bad_photo_rejects_the_whole_submission(client, login, form_ids):
    """A complaint must not be created when its evidence fails validation."""
    login("student1@rosp.edu")
    response = submit(
        client, form_ids, photos=[(BytesIO(b"this is definitely not an image"), "fake.jpg")]
    )

    assert response.status_code == 200
    assert "not a real image" in response.get_data(as_text=True).lower()
    assert db.session.scalar(select(Complaint)) is None
    assert db.session.scalar(select(Attachment)) is None


def test_exceeding_photo_limit_is_rejected(client, login, app, form_ids):
    app.config["MAX_FILES_PER_COMPLAINT"] = 2
    login("student1@rosp.edu")

    response = submit(client, form_ids, photos=[photo(f"p{i}.jpg") for i in range(3)])

    assert "at most 2 photos" in response.get_data(as_text=True)
    assert db.session.scalar(select(Complaint)) is None


def test_staff_cannot_file_complaints(client, login, form_ids):
    login("staff1@rosp.edu")
    response = client.get("/complaints/new")

    assert response.status_code == 403


# --------------------------------------------------------------------------
# The full workflow
# --------------------------------------------------------------------------


def test_complaint_runs_the_whole_lifecycle(client, login, form_ids, users):
    # 1. Student files it with evidence.
    login("student1@rosp.edu")
    submit(client, form_ids, photos=[photo()])
    complaint = db.session.scalar(select(Complaint))
    assert complaint.status == Status.PENDING
    client.get("/logout")

    # 2. Admin assigns it to a staff member.
    login("admin@rosp.edu")
    client.post(
        f"/admin/complaints/{complaint.id}/assign",
        data={"staff_id": str(users["staff1"].id), "note": "Please look today"},
        follow_redirects=True,
    )
    db.session.refresh(complaint)
    assert complaint.status == Status.ASSIGNED
    assert complaint.assigned_staff_id == users["staff1"].id
    client.get("/logout")

    # 3. Staff start work.
    login("staff1@rosp.edu")
    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.IN_PROGRESS},
        follow_redirects=True,
    )
    db.session.refresh(complaint)
    assert complaint.status == Status.IN_PROGRESS

    # 4. Staff resolve it, attaching a photo of the repair.
    client.post(
        f"/complaints/{complaint.id}/status",
        data={
            "status": Status.RESOLVED,
            "resolution_note": "Replaced the projector lamp and tested both HDMI ports.",
            "resolution_photos": [photo("fixed.jpg")],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    db.session.refresh(complaint)

    # Resolving hands straight over to the student to confirm.
    assert complaint.status == Status.STUDENT_VERIFICATION
    assert complaint.resolved_at is not None
    assert len(complaint.resolution_photos) == 1
    assert len(complaint.evidence) == 1
    client.get("/logout")

    # 5. Student confirms the fix, which closes the complaint.
    login("student1@rosp.edu")
    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.CLOSED},
        follow_redirects=True,
    )
    db.session.refresh(complaint)
    assert complaint.status == Status.CLOSED
    assert complaint.closed_at is not None

    recorded = [event.status for event in complaint.events]
    for expected in (
        "SUBMITTED",
        Status.PENDING,
        Status.ASSIGNED,
        Status.IN_PROGRESS,
        Status.RESOLVED,
        Status.STUDENT_VERIFICATION,
        Status.CLOSED,
    ):
        assert expected in recorded, f"{expected} missing from the timeline"


def test_student_can_reopen_an_unfixed_complaint(client, login, form_ids, users):
    login("student1@rosp.edu")
    submit(client, form_ids)
    complaint = db.session.scalar(select(Complaint))
    complaint.assigned_staff_id = users["staff1"].id
    complaint.status = Status.STUDENT_VERIFICATION
    db.session.commit()

    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.REOPENED, "reopen_reason": "It is still dripping."},
        follow_redirects=True,
    )
    db.session.refresh(complaint)

    # Reopening sends it back to the assigned staff member.
    assert complaint.status == Status.IN_PROGRESS
    assert complaint.resolved_at is None
    assert Status.REOPENED in [event.status for event in complaint.events]


def test_unassigned_staff_cannot_change_status(client, login, form_ids, users):
    login("student1@rosp.edu")
    submit(client, form_ids)
    complaint = db.session.scalar(select(Complaint))
    complaint.assigned_staff_id = users["staff1"].id
    complaint.status = Status.ASSIGNED
    db.session.commit()
    client.get("/logout")

    login("staff2@rosp.edu")
    response = client.post(
        f"/complaints/{complaint.id}/status", data={"status": Status.IN_PROGRESS}
    )

    assert response.status_code == 403
    db.session.refresh(complaint)
    assert complaint.status == Status.ASSIGNED


def test_student_cannot_start_work_on_own_complaint(client, login, form_ids, users):
    login("student1@rosp.edu")
    submit(client, form_ids)
    complaint = db.session.scalar(select(Complaint))
    complaint.assigned_staff_id = users["staff1"].id
    complaint.status = Status.ASSIGNED
    db.session.commit()

    response = client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.IN_PROGRESS},
        follow_redirects=True,
    )

    assert "not available" in response.get_data(as_text=True).lower()
    db.session.refresh(complaint)
    assert complaint.status == Status.ASSIGNED


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------


def test_student_list_shows_only_their_own_complaints(client, login, form_ids, users):
    login("student1@rosp.edu")
    submit(client, form_ids, title="Student one problem")
    client.get("/logout")

    login("student2@rosp.edu")
    submit(client, form_ids, title="Student two problem")
    body = client.get("/complaints/").get_data(as_text=True)

    assert "Student two problem" in body
    assert "Student one problem" not in body
