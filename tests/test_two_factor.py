"""Two-step verification.

Signing in must take two stages. These tests pin down that a correct password
alone grants nothing, and that the one-time code obeys its expiry, attempt
limit, single-use rule and resend cooldown.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import OtpCode, utcnow
from app.services import otp


def password_step(client, email="student1@rosp.edu", password="Password@123"):
    """Do only stage one, leaving the login half-finished."""
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=True
    )


# --------------------------------------------------------------------------
# The password alone is not enough
# --------------------------------------------------------------------------


def test_correct_password_does_not_sign_you_in(client, otp_codes):
    password_step(client)

    # A code was issued, but the dashboard is still out of reach.
    assert len(otp_codes) == 1
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_wrong_password_issues_no_code(client, otp_codes):
    response = password_step(client, password="wrong-password")

    assert otp_codes == []
    assert "incorrect email or password" in response.get_data(as_text=True).lower()


def test_verify_page_is_unreachable_without_starting(client):
    response = client.get("/verify", follow_redirects=False)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_full_two_step_reaches_the_dashboard(client, otp_codes):
    password_step(client)
    response = client.post(
        "/verify", data={"code": otp_codes[-1]}, follow_redirects=True
    )

    assert response.status_code == 200
    assert client.get("/dashboard").status_code == 200


# --------------------------------------------------------------------------
# Code handling
# --------------------------------------------------------------------------


def test_wrong_code_is_rejected_and_costs_an_attempt(client, otp_codes):
    password_step(client)
    response = client.post("/verify", data={"code": "000000"}, follow_redirects=True)

    body = response.get_data(as_text=True).lower()
    assert "incorrect code" in body
    assert client.get("/dashboard").status_code == 302

    record = db.session.scalar(select(OtpCode))
    assert record.attempts == 1


def test_code_is_burned_after_too_many_attempts(client, app, otp_codes):
    app.config["OTP_MAX_ATTEMPTS"] = 3
    password_step(client)
    real_code = otp_codes[-1]

    for _ in range(3):
        client.post("/verify", data={"code": "000000"}, follow_redirects=True)

    # Even the correct code no longer works.
    client.post("/verify", data={"code": real_code}, follow_redirects=True)
    assert client.get("/dashboard").status_code == 302


def test_code_cannot_be_used_twice(client, otp_codes):
    password_step(client)
    code = otp_codes[-1]
    client.post("/verify", data={"code": code}, follow_redirects=True)
    client.get("/logout")

    # Replaying the same code on a fresh attempt must fail.
    password_step(client)
    response = client.post("/verify", data={"code": code}, follow_redirects=True)

    assert client.get("/dashboard").status_code == 302
    assert "incorrect code" in response.get_data(as_text=True).lower()


def test_expired_code_is_refused(client, app, otp_codes):
    password_step(client)

    record = db.session.scalar(select(OtpCode))
    record.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()

    response = client.post(
        "/verify", data={"code": otp_codes[-1]}, follow_redirects=True
    )

    assert "expired" in response.get_data(as_text=True).lower()
    assert client.get("/dashboard").status_code == 302


def test_issuing_a_new_code_supersedes_the_old_one(client, app, otp_codes):
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 0
    password_step(client)
    first = otp_codes[-1]

    client.post("/verify/resend", follow_redirects=True)
    second = otp_codes[-1]
    assert first != second

    # The superseded code must not work.
    client.post("/verify", data={"code": first}, follow_redirects=True)
    assert client.get("/dashboard").status_code == 302

    client.post("/verify", data={"code": second}, follow_redirects=True)
    assert client.get("/dashboard").status_code == 200


def test_resend_is_rate_limited(client, app, otp_codes):
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 300
    password_step(client)
    issued = len(otp_codes)

    response = client.post("/verify/resend", follow_redirects=True)

    assert len(otp_codes) == issued, "cooldown must block a second code"
    assert "please wait" in response.get_data(as_text=True).lower()


def test_cancelling_clears_the_pending_login(client, otp_codes):
    password_step(client)
    client.post("/verify/cancel", follow_redirects=True)

    response = client.post(
        "/verify", data={"code": otp_codes[-1]}, follow_redirects=False
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def test_code_is_not_stored_in_plaintext(client, otp_codes):
    password_step(client)
    code = otp_codes[-1]

    record = db.session.scalar(select(OtpCode))
    assert code not in record.otp_hash
    assert record.otp_hash.startswith("pbkdf2:")


def test_generated_codes_are_six_digits(app):
    with app.test_request_context():
        codes = {otp._generate_code() for _ in range(200)}

    assert all(len(c) == 6 and c.isdigit() for c in codes)
    # 200 draws from a million values should not collapse to a handful.
    assert len(codes) > 150


def test_verified_code_is_marked_used(client, otp_codes):
    password_step(client)
    client.post("/verify", data={"code": otp_codes[-1]}, follow_redirects=True)

    record = db.session.scalar(select(OtpCode))
    assert record.used is True
    assert record.consumed_at is not None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 345"])
def test_malformed_codes_are_rejected(client, otp_codes, bad):
    password_step(client)
    client.post("/verify", data={"code": bad}, follow_redirects=True)

    assert client.get("/dashboard").status_code == 302


# --------------------------------------------------------------------------
# The development-only on-screen code
# --------------------------------------------------------------------------
#
# Showing a verification code in the page defeats the point of emailing it, so
# the gate has to be exactly right: debug mode AND no SMTP. These tests pin
# down that both halves are required.


def test_code_is_shown_when_debug_and_email_is_off(client, app, otp_codes):
    app.debug = True
    app.config["EMAIL_ENABLED"] = False

    password_step(client)
    body = client.get("/verify").get_data(as_text=True)

    assert otp_codes[-1] in body
    assert "development mode" in body.lower()


def test_code_is_hidden_when_not_debugging(client, app, otp_codes):
    """Production has DEBUG off, so the code must never reach the page."""
    app.debug = False
    app.config["EMAIL_ENABLED"] = False

    password_step(client)
    body = client.get("/verify").get_data(as_text=True)

    assert otp_codes[-1] not in body


def test_code_is_hidden_once_email_works(client, app, otp_codes):
    """With SMTP configured the code goes to the inbox, not the screen."""
    app.debug = True
    app.config["EMAIL_ENABLED"] = True

    password_step(client)
    body = client.get("/verify").get_data(as_text=True)

    assert otp_codes[-1] not in body


def test_shown_code_is_cleared_after_signing_in(client, app, otp_codes):
    app.debug = True
    app.config["EMAIL_ENABLED"] = False

    password_step(client)
    code = otp_codes[-1]
    client.post("/verify", data={"code": code}, follow_redirects=True)

    with client.session_transaction() as session:
        assert "pending_dev_code" not in session
