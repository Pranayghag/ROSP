"""Staff registration, admin authorization, and the access rules that follow.

The central guarantee here: a staff account is usable only after an
administrator approves it, that approval is stored permanently, and signing in
never re-opens the question.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.constants import AuthorizationStatus, Role, Status
from app.extensions import db
from app.models import Category, Complaint, EmailLog, Location, User
from app.services.tokens import generate_setup_token

STAFF_REGISTRATION = {
    "role": Role.STAFF,
    "name": "Real Staff Member",
    "email": "new.staff@college.example",
    "phone": "9876543210",
    "department": "Electrical",
    "designation": "Electrical Staff",
    "staff_id": "EMP2026",
    "password": "StrongPass@123",
    "confirm": "StrongPass@123",
}

STUDENT_REGISTRATION = {
    "role": Role.STUDENT,
    "name": "Real Student",
    "email": "new.student@college.example",
    "roll_no": "CS26001",
    "department": "Computer Science",
    "password": "StrongPass@123",
    "confirm": "StrongPass@123",
}


def register(client, **overrides):
    data = dict(STAFF_REGISTRATION)
    data.update(overrides)
    return client.post("/register", data=data, follow_redirects=True)


def fetch(email: str) -> User | None:
    return db.session.scalar(select(User).where(User.email == email))


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_staff_registration_starts_pending(client):
    response = register(client)

    staff = fetch(STAFF_REGISTRATION["email"])
    assert staff is not None
    assert staff.role == Role.STAFF
    assert staff.authorization_status == AuthorizationStatus.PENDING
    assert "registration received" in response.get_data(as_text=True).lower()


def test_staff_registration_does_not_sign_them_in(client):
    register(client)
    assert client.get("/dashboard").status_code == 302


def test_staff_registration_emails_the_admin(client, app):
    register(client)

    logs = db.session.scalars(select(EmailLog)).all()
    assert any(
        log.to_address == app.config["ADMIN_EMAIL"]
        and "Authorization" in log.subject
        for log in logs
    ), "the administrator must be notified of a new staff registration"


def test_student_registration_is_authorized_immediately(client, otp_codes):
    client.post("/register", data=STUDENT_REGISTRATION, follow_redirects=True)

    student = fetch(STUDENT_REGISTRATION["email"])
    assert student.role == Role.STUDENT
    assert student.authorization_status == AuthorizationStatus.AUTHORIZED

    # And they are put straight into the normal two-step sign-in.
    assert len(otp_codes) == 1
    client.post("/verify", data={"code": otp_codes[-1]}, follow_redirects=True)
    assert client.get("/dashboard").status_code == 200


def test_cannot_self_register_as_admin(client):
    """The role is re-checked server-side; a forged value must be refused."""
    register(client, role=Role.ADMIN, email="attacker@college.example")

    user = fetch("attacker@college.example")
    assert user is None or user.role != Role.ADMIN


def test_staff_registration_requires_department_and_designation(client):
    register(client, department="", designation="")

    assert fetch(STAFF_REGISTRATION["email"]) is None


# --------------------------------------------------------------------------
# The access rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (AuthorizationStatus.PENDING, "awaiting administrator authorization"),
        (AuthorizationStatus.REJECTED, "was not approved"),
        (AuthorizationStatus.SUSPENDED, "suspended"),
    ],
)
def test_unauthorized_staff_cannot_sign_in(client, users, otp_codes, status, expected):
    staff = users["staff1"]
    staff.authorization_status = status
    db.session.commit()

    response = client.post(
        "/login",
        data={"email": staff.email, "password": "Password@123"},
        follow_redirects=True,
    )

    assert expected in response.get_data(as_text=True).lower()
    assert otp_codes == [], "no code should be sent to an account that cannot sign in"
    assert client.get("/dashboard").status_code == 302


def test_authorized_staff_can_sign_in(client, users, login):
    users["staff1"].authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()

    login("staff1@rosp.edu")
    assert client.get("/dashboard").status_code == 200


def test_signing_in_never_changes_authorization(client, users, login):
    """Approval is granted once and must survive every later sign-in."""
    staff = users["staff1"]
    staff.authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()

    for _ in range(3):
        login("staff1@rosp.edu")
        client.get("/logout")

    db.session.refresh(staff)
    assert staff.authorization_status == AuthorizationStatus.AUTHORIZED


# --------------------------------------------------------------------------
# Admin decisions
# --------------------------------------------------------------------------


def test_admin_authorizes_a_pending_staff_member(client, login, users):
    staff = users["staff1"]
    staff.authorization_status = AuthorizationStatus.PENDING
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/staff/{staff.id}/decision",
        data={"decision": AuthorizationStatus.AUTHORIZED, "note": "Verified in person"},
        follow_redirects=True,
    )

    db.session.refresh(staff)
    assert staff.authorization_status == AuthorizationStatus.AUTHORIZED
    assert staff.authorized_by == users["admin"].id
    assert staff.authorized_at is not None

    logs = db.session.scalars(select(EmailLog).where(EmailLog.user_id == staff.id)).all()
    assert any("Authorized" in log.subject for log in logs)


def test_admin_rejects_a_staff_member(client, login, users):
    staff = users["staff1"]
    staff.authorization_status = AuthorizationStatus.PENDING
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/staff/{staff.id}/decision",
        data={"decision": AuthorizationStatus.REJECTED, "note": "Not a member of staff"},
        follow_redirects=True,
    )

    db.session.refresh(staff)
    assert staff.authorization_status == AuthorizationStatus.REJECTED
    assert staff.authorization_note == "Not a member of staff"


def test_admin_cannot_change_their_own_status(client, login, users):
    admin = users["admin"]

    login("admin@rosp.edu")
    response = client.post(
        f"/admin/staff/{admin.id}/decision",
        data={"decision": AuthorizationStatus.SUSPENDED},
        follow_redirects=True,
    )

    db.session.refresh(admin)
    assert admin.authorization_status == AuthorizationStatus.AUTHORIZED
    assert "cannot change your own" in response.get_data(as_text=True).lower()


def test_students_cannot_reach_staff_management(client, login):
    login("student1@rosp.edu")
    assert client.get("/admin/staff").status_code == 403
    assert client.get("/admin/staff/new").status_code == 403


def test_staff_cannot_reach_staff_management(client, login):
    login("staff1@rosp.edu")
    assert client.get("/admin/staff").status_code == 403


# --------------------------------------------------------------------------
# Assignment is limited to authorized staff
# --------------------------------------------------------------------------


@pytest.fixture
def complaint(app, users):
    category = db.session.scalar(select(Category))
    location = db.session.scalar(select(Location))
    item = Complaint(
        title="Fan is broken",
        description="The ceiling fan does not spin at any speed.",
        category_id=category.id,
        location_id=location.id,
        student_id=users["student1"].id,
    )
    db.session.add(item)
    db.session.flush()
    item.assign_code()
    db.session.commit()
    return item


def test_only_authorized_staff_appear_in_the_assign_dropdown(client, login, users, complaint):
    users["staff1"].authorization_status = AuthorizationStatus.AUTHORIZED
    users["staff2"].authorization_status = AuthorizationStatus.PENDING
    db.session.commit()

    login("admin@rosp.edu")
    body = client.get(f"/admin/complaints/{complaint.id}/assign").get_data(as_text=True)

    assert users["staff1"].name in body
    assert users["staff2"].name not in body


def test_assigning_to_unauthorized_staff_is_refused(client, login, users, complaint):
    users["staff2"].authorization_status = AuthorizationStatus.SUSPENDED
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/complaints/{complaint.id}/assign",
        data={"staff_id": str(users["staff2"].id)},
        follow_redirects=True,
    )

    db.session.refresh(complaint)
    assert complaint.assigned_staff_id is None
    assert complaint.status == Status.PENDING


def test_assignment_emails_the_staff_member(client, login, users, complaint):
    users["staff1"].authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()

    login("admin@rosp.edu")
    client.post(
        f"/admin/complaints/{complaint.id}/assign",
        data={"staff_id": str(users["staff1"].id)},
        follow_redirects=True,
    )

    db.session.refresh(complaint)
    assert complaint.assigned_staff_id == users["staff1"].id

    logs = db.session.scalars(
        select(EmailLog).where(EmailLog.complaint_id == complaint.id)
    ).all()
    assert any(
        log.to_address == users["staff1"].email and complaint.code in log.subject
        for log in logs
    ), "the assigned staff member must be emailed the complaint"


# --------------------------------------------------------------------------
# Admin-created accounts
# --------------------------------------------------------------------------


def test_admin_creates_staff_without_a_password(client, login):
    login("admin@rosp.edu")
    client.post(
        "/admin/staff/new",
        data={
            "name": "Added By Admin",
            "email": "added@college.example",
            "phone": "9000000000",
            "staff_id": "EMP9",
            "department": "Maintenance",
            "designation": "Maintenance Staff",
            "role": Role.STAFF,
        },
        follow_redirects=True,
    )

    staff = fetch("added@college.example")
    assert staff is not None
    assert staff.authorization_status == AuthorizationStatus.AUTHORIZED
    assert staff.password_set is False


def test_account_without_a_password_cannot_sign_in(client, login, otp_codes):
    login("admin@rosp.edu")
    codes_before = len(otp_codes)
    client.post(
        "/admin/staff/new",
        data={
            "name": "Added By Admin",
            "email": "added@college.example",
            "phone": "9000000000",
            "department": "Maintenance",
            "designation": "Maintenance Staff",
            "role": Role.STAFF,
        },
        follow_redirects=True,
    )
    client.get("/logout")

    response = client.post(
        "/login",
        data={"email": "added@college.example", "password": "anything"},
        follow_redirects=True,
    )
    assert len(otp_codes) == codes_before, "no code for an account with no password"
    assert "incorrect email or password" in response.get_data(as_text=True).lower()


def test_setup_link_sets_a_password_and_then_stops_working(client, app, users):
    staff = users["staff1"]
    staff.password_set = False
    db.session.commit()

    with app.test_request_context():
        token = generate_setup_token(staff)

    response = client.post(
        f"/account/setup/{token}",
        data={"password": "BrandNew@123", "confirm": "BrandNew@123"},
        follow_redirects=True,
    )
    assert "please sign in" in response.get_data(as_text=True).lower()

    db.session.refresh(staff)
    assert staff.password_set is True
    assert staff.check_password("BrandNew@123")

    # The token embeds a fingerprint of the old password hash, so reusing it
    # after the password changed must fail.
    replay = client.get(f"/account/setup/{token}", follow_redirects=True)
    assert "invalid or has expired" in replay.get_data(as_text=True).lower()


def test_tampered_setup_token_is_rejected(client):
    response = client.get("/account/setup/not-a-real-token", follow_redirects=True)

    assert "invalid or has expired" in response.get_data(as_text=True).lower()
