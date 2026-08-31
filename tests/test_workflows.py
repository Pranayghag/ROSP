"""The eight end-to-end workflows from the specification (section 27).

Each test walks a complete journey through real HTTP requests -- registration,
authorization, assignment, resolution, verification -- asserting the database
state, the notifications and the emails at every step.

These are the acceptance tests: if one of them fails, a user-visible promise of
the system is broken.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import select

from app.constants import AttachmentType, AuthorizationStatus, Role, Status
from app.extensions import db
from app.models import Complaint, EmailLog, Notification, User
from app.services.tokens import generate_setup_token
from tests.conftest import make_image_bytes


def photo(name="broken_fan.jpg", fmt="JPEG"):
    return (BytesIO(make_image_bytes(fmt)), name)


def emails_to(address: str) -> list[EmailLog]:
    return list(
        db.session.scalars(select(EmailLog).where(EmailLog.to_address == address)).all()
    )


def notifications_for(user_id: int) -> list[Notification]:
    return list(
        db.session.scalars(
            select(Notification).where(Notification.user_id == user_id)
        ).all()
    )


def fetch_user(email: str) -> User | None:
    return db.session.scalar(select(User).where(User.email == email))


# ==========================================================================
# TEST 1 -- Student registration through to the dashboard
# ==========================================================================


def test_1_student_registration_to_dashboard(client, otp_codes):
    client.post(
        "/register",
        data={
            "role": Role.STUDENT,
            "name": "Nisha Patel",
            "email": "nisha@college.example",
            "roll_no": "CS26011",
            "department": "Computer Science",
            "password": "StrongPass@123",
            "confirm": "StrongPass@123",
        },
        follow_redirects=True,
    )

    student = fetch_user("nisha@college.example")
    assert student is not None
    assert student.role == Role.STUDENT
    assert student.authorization_status == AuthorizationStatus.AUTHORIZED

    # Registration puts them straight into two-step verification.
    assert len(otp_codes) == 1
    client.post("/verify", data={"code": otp_codes[-1]}, follow_redirects=True)

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Nisha" in dashboard.get_data(as_text=True)


# ==========================================================================
# TEST 2 -- Staff registration, admin authorization, staff sign-in
# ==========================================================================


def test_2_staff_registration_authorization_and_login(client, login, app, users, otp_codes):
    # --- staff registers -------------------------------------------------
    client.post(
        "/register",
        data={
            "role": Role.STAFF,
            "name": "Ravi Kumar",
            "email": "ravi@college.example",
            "phone": "9812345670",
            "department": "Electrical",
            "designation": "Electrical Staff",
            "staff_id": "EMP4410",
            "password": "StrongPass@123",
            "confirm": "StrongPass@123",
        },
        follow_redirects=True,
    )

    staff = fetch_user("ravi@college.example")
    assert staff.authorization_status == AuthorizationStatus.PENDING
    assert otp_codes == [], "an unapproved account must not receive a sign-in code"

    # --- the admin is told ------------------------------------------------
    admin_mail = emails_to(app.config["ADMIN_EMAIL"])
    assert any("Authorization" in m.subject for m in admin_mail)

    # --- staff cannot get in yet -----------------------------------------
    blocked = client.post(
        "/login",
        data={"email": staff.email, "password": "StrongPass@123"},
        follow_redirects=True,
    )
    assert "awaiting administrator authorization" in blocked.get_data(as_text=True).lower()
    assert client.get("/dashboard").status_code == 302

    # --- admin authorizes -------------------------------------------------
    login("admin@rosp.edu")
    client.post(
        f"/admin/staff/{staff.id}/decision",
        data={"decision": AuthorizationStatus.AUTHORIZED, "note": "Checked ID card"},
        follow_redirects=True,
    )
    client.get("/logout")

    db.session.refresh(staff)
    assert staff.authorization_status == AuthorizationStatus.AUTHORIZED
    assert staff.authorized_by == users["admin"].id
    assert staff.authorized_at is not None

    # --- staff is told ----------------------------------------------------
    assert any("Authorized" in m.subject for m in emails_to(staff.email))
    assert any("authorized" in n.message.lower() for n in notifications_for(staff.id))

    # --- and can now sign in ---------------------------------------------
    login("ravi@college.example", "StrongPass@123")
    assert client.get("/dashboard").status_code == 200


# ==========================================================================
# TEST 3 -- A rejected staff member stays locked out
# ==========================================================================


def test_3_rejected_staff_cannot_access(client, login, users, otp_codes):
    staff = users["staff1"]
    staff.authorization_status = AuthorizationStatus.PENDING
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/staff/{staff.id}/decision",
        data={"decision": AuthorizationStatus.REJECTED, "note": "Not on the staff roll"},
        follow_redirects=True,
    )
    client.get("/logout")

    db.session.refresh(staff)
    assert staff.authorization_status == AuthorizationStatus.REJECTED
    assert staff.authorization_note == "Not on the staff roll"

    codes_before = len(otp_codes)
    response = client.post(
        "/login",
        data={"email": staff.email, "password": "Password@123"},
        follow_redirects=True,
    )

    assert "was not approved" in response.get_data(as_text=True).lower()
    assert len(otp_codes) == codes_before
    assert client.get("/dashboard").status_code == 302
    assert client.get("/complaints/").status_code == 302


# ==========================================================================
# TEST 4 -- Admin creates a staff account directly
# ==========================================================================


def test_4_admin_adds_staff_who_sets_own_password(client, login, app, otp_codes):
    login("admin@rosp.edu")
    client.post(
        "/admin/staff/new",
        data={
            "name": "Meera Nair",
            "email": "meera@college.example",
            "phone": "9900112233",
            "staff_id": "EMP7788",
            "department": "Plumbing",
            "designation": "Maintenance Staff",
            "role": Role.STAFF,
        },
        follow_redirects=True,
    )
    client.get("/logout")

    staff = fetch_user("meera@college.example")
    assert staff.authorization_status == AuthorizationStatus.AUTHORIZED
    assert staff.password_set is False, "the admin must not choose their password"

    # A setup email went out, and it carries no password.
    setup_mail = emails_to(staff.email)
    assert any("Set Up" in m.subject for m in setup_mail)

    # Cannot sign in until a password is chosen.
    codes_before = len(otp_codes)
    client.post(
        "/login",
        data={"email": staff.email, "password": "guess"},
        follow_redirects=True,
    )
    assert len(otp_codes) == codes_before

    # The emailed link lets them set one.
    with app.test_request_context():
        token = generate_setup_token(staff)
    client.post(
        f"/account/setup/{token}",
        data={"password": "MeeraPass@123", "confirm": "MeeraPass@123"},
        follow_redirects=True,
    )

    db.session.refresh(staff)
    assert staff.password_set is True

    # And then sign in normally, through both steps.
    client.post(
        "/login",
        data={"email": staff.email, "password": "MeeraPass@123"},
        follow_redirects=True,
    )
    client.post("/verify", data={"code": otp_codes[-1]}, follow_redirects=True)
    assert client.get("/dashboard").status_code == 200


# ==========================================================================
# TEST 5 -- Complaint assignment, with a real email carrying the details
# ==========================================================================


@pytest.fixture
def filed_complaint(client, login, form_data):
    """A student files a complaint with two photos."""
    login("student1@rosp.edu")
    client.post(
        "/complaints/new",
        data={**form_data, "photos": [photo("fan1.jpg"), photo("fan2.png", "PNG")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    client.get("/logout")
    return db.session.scalar(select(Complaint))


@pytest.fixture
def form_data(app):
    from app.models import Category, Location

    category = db.session.scalar(select(Category))
    location = db.session.scalar(select(Location))
    return {
        "title": "Ceiling fan is broken",
        "description": "The fan in Room 204 does not spin and makes a grinding noise.",
        "category_id": str(category.id),
        "location_id": str(location.id),
        "priority": "HIGH",
    }


def test_5_assignment_emails_staff_with_full_details(client, login, users, filed_complaint):
    complaint = filed_complaint
    assert len(complaint.evidence) == 2

    staff = users["staff1"]
    staff.authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/complaints/{complaint.id}/assign",
        data={"staff_id": str(staff.id), "note": "Please check today"},
        follow_redirects=True,
    )

    db.session.refresh(complaint)
    assert complaint.status == Status.ASSIGNED
    assert complaint.assigned_staff_id == staff.id

    # In-app notification for both sides.
    assert any(complaint.code in n.message for n in notifications_for(staff.id))
    assert any(complaint.code in n.message for n in notifications_for(complaint.student_id))

    # And an email addressed to the staff member's real address.
    mail = [m for m in emails_to(staff.email) if m.complaint_id == complaint.id]
    assert mail, "the assigned staff member must be emailed"
    assert complaint.code in mail[-1].subject


def test_5b_assignment_email_body_carries_every_field(app, users, filed_complaint):
    """The email must contain the details the specification lists."""
    from flask import render_template

    from app.services.mailers import PRIORITY_TONES, _collect_inline_evidence

    complaint = filed_complaint
    staff = users["staff1"]

    with app.test_request_context():
        images = _collect_inline_evidence(complaint)
        html = render_template(
            "email/complaint_assigned.html",
            app_name=app.config["APP_NAME"],
            app_tagline=app.config["APP_TAGLINE"],
            base_url=app.config["BASE_URL"],
            staff=staff,
            complaint=complaint,
            location_text="Block A - Room 204",
            submitted_on="30 Aug 2026",
            due_on="31 Aug 2026",
            priority_tone=PRIORITY_TONES.get(complaint.priority, "neutral"),
            evidence_count=len(complaint.evidence),
            complaint_url="http://localhost/complaints/1",
            dashboard_url="http://localhost/dashboard",
            inline_images=images,
        )

    # Values are HTML-escaped in the rendered body, so compare against the
    # escaped form -- a category like "Plumbing & Water" becomes "&amp;".
    from markupsafe import escape

    for expected in [
        complaint.code,
        complaint.title,
        complaint.category.name,
        complaint.priority,
        "Block A - Room 204",
        complaint.student.name,
        complaint.description,
        "View Complaint",
        "Open Staff Dashboard",
        "ASSIGNED",
    ]:
        assert str(escape(expected)) in html, f"assignment email is missing {expected!r}"

    # The student's photos travel with the message.
    assert len(images) == 2
    assert all(f"cid:{img.cid}" in html for img in images)

    # And no server path is ever exposed.
    assert str(app.config["UPLOAD_DIR"]) not in html
    for attachment in complaint.evidence:
        assert attachment.file_path not in html


# ==========================================================================
# TEST 6 -- Staff resolve the work and upload an "after" photo
# ==========================================================================


@pytest.fixture
def assigned_complaint(client, login, users, filed_complaint):
    complaint = filed_complaint
    users["staff1"].authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/complaints/{complaint.id}/assign",
        data={"staff_id": str(users["staff1"].id)},
        follow_redirects=True,
    )
    client.get("/logout")
    db.session.refresh(complaint)
    return complaint


def test_6_staff_resolution_with_after_photo(client, login, users, assigned_complaint):
    complaint = assigned_complaint

    login("staff1@rosp.edu")

    # Start the work.
    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.IN_PROGRESS},
        follow_redirects=True,
    )
    db.session.refresh(complaint)
    assert complaint.status == Status.IN_PROGRESS

    # Finish it, with notes and a photo of the repair.
    client.post(
        f"/complaints/{complaint.id}/status",
        data={
            "status": Status.RESOLVED,
            "resolution_note": "Replaced the fan bearing and balanced the blades.",
            "resolution_photos": [photo("fixed_fan.jpg")],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    db.session.refresh(complaint)

    assert complaint.status == Status.STUDENT_VERIFICATION
    assert complaint.resolved_at is not None
    assert "Replaced the fan bearing" in complaint.resolution_note

    # Both sets of photos survive, kept apart by attachment_type.
    assert len(complaint.evidence) == 2
    assert len(complaint.resolution_photos) == 1
    assert {a.attachment_type for a in complaint.attachments} == {
        AttachmentType.EVIDENCE,
        AttachmentType.RESOLUTION,
    }

    # The student is told, in-app and by email.
    assert any("resolved" in n.message.lower() for n in notifications_for(complaint.student_id))
    student_mail = emails_to(complaint.student.email)
    assert any(complaint.code in m.subject for m in student_mail)


def test_6b_resolution_requires_a_description(client, login, users, assigned_complaint):
    """A photo alone is not a resolution -- staff must say what they did."""
    complaint = assigned_complaint
    login("staff1@rosp.edu")
    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.IN_PROGRESS},
        follow_redirects=True,
    )

    response = client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.RESOLVED, "resolution_note": "done"},
        follow_redirects=True,
    )

    db.session.refresh(complaint)
    assert complaint.status == Status.IN_PROGRESS, "must not resolve without a description"
    assert "describe what you did" in response.get_data(as_text=True).lower()


# ==========================================================================
# TEST 7 -- The student accepts, and the complaint closes
# ==========================================================================


@pytest.fixture
def resolved_complaint(client, login, users, assigned_complaint):
    complaint = assigned_complaint
    login("staff1@rosp.edu")
    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.IN_PROGRESS},
        follow_redirects=True,
    )
    client.post(
        f"/complaints/{complaint.id}/status",
        data={
            "status": Status.RESOLVED,
            "resolution_note": "Replaced the fan bearing and balanced the blades.",
            "resolution_photos": [photo("fixed_fan.jpg")],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    client.get("/logout")
    db.session.refresh(complaint)
    return complaint


def test_7_student_accepts_and_complaint_closes(client, login, resolved_complaint):
    complaint = resolved_complaint
    assert complaint.status == Status.STUDENT_VERIFICATION

    login("student1@rosp.edu")

    # The student can see both photos before deciding.
    page = client.get(f"/complaints/{complaint.id}").get_data(as_text=True)
    assert "BEFORE" in page and "AFTER" in page
    assert "Replaced the fan bearing" in page
    for attachment in complaint.attachments:
        assert f"/evidence/{attachment.id}" in page

    client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.CLOSED},
        follow_redirects=True,
    )
    db.session.refresh(complaint)

    assert complaint.status == Status.CLOSED
    assert complaint.closed_at is not None

    recorded = [e.status for e in complaint.events]
    for step in ("SUBMITTED", Status.PENDING, Status.ASSIGNED, Status.IN_PROGRESS,
                 Status.RESOLVED, Status.STUDENT_VERIFICATION, Status.CLOSED):
        assert step in recorded, f"{step} missing from the timeline"


# ==========================================================================
# TEST 8 -- The student rejects, and the complaint reopens
# ==========================================================================


def test_8_student_rejects_and_complaint_reopens(client, login, users, resolved_complaint):
    complaint = resolved_complaint

    login("student1@rosp.edu")
    client.post(
        f"/complaints/{complaint.id}/status",
        data={
            "status": Status.REOPENED,
            "reopen_reason": "The fan still rattles at speed 3.",
        },
        follow_redirects=True,
    )
    db.session.refresh(complaint)

    # Back with the staff member, not closed.
    assert complaint.status == Status.IN_PROGRESS
    assert complaint.resolved_at is None
    assert "still rattles" in complaint.reopen_reason
    assert Status.REOPENED in [e.status for e in complaint.events]

    # Staff and admins are told.
    staff_id = users["staff1"].id
    assert any(complaint.code in n.message for n in notifications_for(staff_id))
    assert any("reopened" in n.message.lower() for n in notifications_for(users["admin"].id))
    assert any(complaint.code in m.subject for m in emails_to(users["staff1"].email))

    # Both photo sets are still intact for the second attempt.
    assert len(complaint.evidence) == 2
    assert len(complaint.resolution_photos) == 1


def test_8b_reopening_requires_a_reason(client, login, resolved_complaint):
    complaint = resolved_complaint

    login("student1@rosp.edu")
    response = client.post(
        f"/complaints/{complaint.id}/status",
        data={"status": Status.REOPENED, "reopen_reason": ""},
        follow_redirects=True,
    )

    db.session.refresh(complaint)
    assert complaint.status == Status.STUDENT_VERIFICATION
    assert "what is still wrong" in response.get_data(as_text=True).lower()


# ==========================================================================
# Cross-cutting: evidence stays private throughout the whole journey
# ==========================================================================


def test_evidence_stays_private_through_the_whole_flow(client, login, users, resolved_complaint):
    complaint = resolved_complaint
    attachment_ids = [a.id for a in complaint.attachments]

    # An unrelated student sees nothing, at any point in the lifecycle.
    login("student2@rosp.edu")
    assert client.get(f"/complaints/{complaint.id}").status_code == 403
    for attachment_id in attachment_ids:
        assert client.get(f"/evidence/{attachment_id}").status_code == 403
    client.get("/logout")

    # An unassigned staff member likewise.
    users["staff2"].authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()
    login("staff2@rosp.edu")
    assert client.get(f"/complaints/{complaint.id}").status_code == 403
    for attachment_id in attachment_ids:
        assert client.get(f"/evidence/{attachment_id}").status_code == 403
    client.get("/logout")

    # The three entitled parties can.
    for who in ("student1@rosp.edu", "staff1@rosp.edu", "admin@rosp.edu"):
        login(who)
        assert client.get(f"/complaints/{complaint.id}").status_code == 200
        for attachment_id in attachment_ids:
            assert client.get(f"/evidence/{attachment_id}").status_code == 200, who
        client.get("/logout")
