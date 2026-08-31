"""One-time passwords for two-step verification.

The code is generated with ``secrets``, hashed before storage, and only ever
leaves the process inside the email that carries it. It is never logged, never
returned to a template, and never stored in the session.

Guards, all enforced server-side:

* **Expiry** -- ``OTP_TTL_SECONDS`` (default 5 minutes)
* **Attempt limit** -- ``OTP_MAX_ATTEMPTS`` wrong guesses burn the code
* **Single use** -- verifying marks it used; a replay fails
* **Supersession** -- issuing a new code invalidates any outstanding one
* **Resend cooldown** -- ``OTP_RESEND_COOLDOWN_SECONDS`` between sends
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import OtpCode, User, _as_utc, utcnow

LOGIN = "LOGIN"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of checking a submitted code."""

    ok: bool
    message: str = ""
    #: True when the user should be sent back to request a fresh code.
    exhausted: bool = False


def _generate_code() -> str:
    """A cryptographically random code of the configured length."""
    length = current_app.config["OTP_LENGTH"]
    # randbelow avoids the modulo bias a naive random % 10**n would introduce.
    return f"{secrets.randbelow(10 ** length):0{length}d}"


def _active_code(user: User, purpose: str) -> OtpCode | None:
    """The most recent unused code for this user and purpose."""
    return db.session.scalar(
        select(OtpCode)
        .where(
            OtpCode.user_id == user.id,
            OtpCode.purpose == purpose,
            OtpCode.used.is_(False),
        )
        .order_by(OtpCode.created_at.desc())
    )


def seconds_until_resend(user: User, purpose: str = LOGIN) -> int:
    """How long the user must wait before another code may be sent."""
    latest = _active_code(user, purpose)
    if latest is None:
        return 0
    cooldown = current_app.config["OTP_RESEND_COOLDOWN_SECONDS"]
    elapsed = (utcnow() - _as_utc(latest.created_at)).total_seconds()
    return max(0, int(cooldown - elapsed))


def issue(user: User, purpose: str = LOGIN) -> str:
    """Create a new code, invalidating any outstanding one.

    Returns the plaintext so the caller can email it. **Do not log it, store
    it, or put it in a response.** The caller is responsible for committing.
    """
    # Supersede anything still outstanding, so only one code is ever live.
    outstanding = db.session.scalars(
        select(OtpCode).where(
            OtpCode.user_id == user.id,
            OtpCode.purpose == purpose,
            OtpCode.used.is_(False),
        )
    ).all()
    for old in outstanding:
        old.used = True
        old.consumed_at = utcnow()

    code = _generate_code()
    method = current_app.config.get("PASSWORD_HASH_METHOD")
    db.session.add(
        OtpCode(
            user_id=user.id,
            otp_hash=(
                generate_password_hash(code, method=method)
                if method
                else generate_password_hash(code)
            ),
            purpose=purpose,
            expires_at=utcnow() + timedelta(seconds=current_app.config["OTP_TTL_SECONDS"]),
        )
    )
    return code


def verify(user: User, submitted: str, purpose: str = LOGIN) -> VerifyResult:
    """Check a submitted code against the outstanding one.

    The caller is responsible for committing; attempt counts are recorded on
    the session either way, so a failed guess still costs an attempt.
    """
    submitted = (submitted or "").strip()
    record = _active_code(user, purpose)

    if record is None:
        return VerifyResult(False, "That code is no longer valid. Request a new one.", True)

    if record.is_expired:
        record.used = True
        record.consumed_at = utcnow()
        return VerifyResult(False, "That code has expired. Request a new one.", True)

    max_attempts = current_app.config["OTP_MAX_ATTEMPTS"]
    if record.attempts >= max_attempts:
        record.used = True
        record.consumed_at = utcnow()
        return VerifyResult(
            False, "Too many incorrect attempts. Request a new code.", True
        )

    # Count the attempt before checking, so a crash mid-verify cannot be used
    # to get a free guess.
    record.attempts += 1

    if not check_password_hash(record.otp_hash, submitted):
        remaining = max_attempts - record.attempts
        if remaining <= 0:
            record.used = True
            record.consumed_at = utcnow()
            return VerifyResult(
                False, "Too many incorrect attempts. Request a new code.", True
            )
        return VerifyResult(
            False,
            f"Incorrect code. {remaining} attempt{'' if remaining == 1 else 's'} left.",
        )

    record.used = True
    record.consumed_at = utcnow()
    return VerifyResult(True)


def purge_expired(older_than_days: int = 7) -> int:
    """Delete spent codes. Housekeeping only; nothing depends on it."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    stale = db.session.scalars(
        select(OtpCode).where(OtpCode.created_at < cutoff)
    ).all()
    for record in stale:
        db.session.delete(record)
    return len(stale)
