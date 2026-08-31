"""Signing in with two-step verification switched off.

``TWO_FACTOR_ENABLED=false`` is the deployment default. Removing the second
factor must remove *only* that: the password check, the authorization gate and
the password-set check all still have to hold, and no code should be generated
at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.constants import AuthorizationStatus, Role
from app.extensions import db
from app.models import OtpCode, User


@pytest.fixture(autouse=True)
def single_step(app):
    """Turn two-step verification off for every test in this module."""
    app.config["TWO_FACTOR_ENABLED"] = False
    return app


def sign_in(client, email="student1@rosp.edu", password="Password@123"):
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=True
    )


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_password_alone_signs_you_in(client):
    sign_in(client)

    assert client.get("/dashboard").status_code == 200


def test_no_code_is_generated(client):
    sign_in(client)

    assert db.session.scalar(select(OtpCode)) is None, "no OTP should exist at all"


def test_no_email_is_sent(client, otp_codes):
    sign_in(client)

    assert otp_codes == []


def test_verify_page_redirects_away(client):
    sign_in(client)
    client.get("/logout")

    response = client.get("/verify", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_resend_is_inert(client):
    response = client.post("/verify/resend", follow_redirects=False)

    assert response.status_code == 302
    assert db.session.scalar(select(OtpCode)) is None


# --------------------------------------------------------------------------
# Everything else still guards the door
# --------------------------------------------------------------------------


def test_wrong_password_is_still_refused(client):
    response = sign_in(client, password="not-the-password")

    assert "incorrect email or password" in response.get_data(as_text=True).lower()
    assert client.get("/dashboard").status_code == 302


def test_unknown_email_is_still_refused(client):
    response = sign_in(client, email="nobody@nowhere.example")

    assert "incorrect email or password" in response.get_data(as_text=True).lower()
    assert client.get("/dashboard").status_code == 302


@pytest.mark.parametrize(
    "status,expected",
    [
        (AuthorizationStatus.PENDING, "awaiting administrator authorization"),
        (AuthorizationStatus.REJECTED, "was not approved"),
        (AuthorizationStatus.SUSPENDED, "suspended"),
    ],
)
def test_unauthorized_staff_still_blocked(client, users, status, expected):
    """The authorization gate is independent of the second factor."""
    staff = users["staff1"]
    staff.authorization_status = status
    db.session.commit()

    response = sign_in(client, email=staff.email)

    assert expected in response.get_data(as_text=True).lower()
    assert client.get("/dashboard").status_code == 302


def test_account_without_a_password_still_blocked(client, users):
    user = users["student1"]
    user.password_set = False
    db.session.commit()

    response = sign_in(client)

    assert "incorrect email or password" not in response.get_data(as_text=True).lower()
    assert client.get("/dashboard").status_code == 302


def test_role_restrictions_still_apply(client):
    sign_in(client)

    assert client.get("/admin/").status_code == 403
    assert client.get("/admin/staff").status_code == 403


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_student_registration_signs_in_directly(client):
    client.post(
        "/register",
        data={
            "role": Role.STUDENT,
            "name": "Single Step Student",
            "email": "single@college.example",
            "roll_no": "CS26099",
            "department": "Computer Science",
            "password": "StrongPass@123",
            "confirm": "StrongPass@123",
        },
        follow_redirects=True,
    )

    assert client.get("/dashboard").status_code == 200
    assert db.session.scalar(select(OtpCode)) is None


def test_staff_registration_still_waits_for_authorization(client):
    """Removing 2FA must not let an unapproved staff account in."""
    client.post(
        "/register",
        data={
            "role": Role.STAFF,
            "name": "Single Step Staff",
            "email": "singlestaff@college.example",
            "phone": "9000000001",
            "department": "Electrical",
            "designation": "Electrical Staff",
            "password": "StrongPass@123",
            "confirm": "StrongPass@123",
        },
        follow_redirects=True,
    )

    staff = db.session.scalar(
        select(User).where(User.email == "singlestaff@college.example")
    )
    assert staff.authorization_status == AuthorizationStatus.PENDING
    assert client.get("/dashboard").status_code == 302


# --------------------------------------------------------------------------
# The switch itself
# --------------------------------------------------------------------------


def test_turning_it_back_on_restores_the_second_step(client, app, otp_codes):
    app.config["TWO_FACTOR_ENABLED"] = True

    sign_in(client)

    assert len(otp_codes) == 1, "a code should be issued again"
    assert client.get("/dashboard").status_code == 302, "not signed in until verified"
