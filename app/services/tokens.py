"""Signed, expiring tokens for links sent by email.

Used for the one-time "set your password" link given to a staff member whose
account an administrator created.

The token carries only the user id and is signed with ``SECRET_KEY`` via
``itsdangerous`` (already a Flask dependency), so it cannot be forged or
tampered with. It is *not* encrypted -- never put anything secret in it.

Two extra properties beyond the signature:

* it expires (``ACTION_TOKEN_TTL_SECONDS``), and
* it is invalidated once used, because the salt includes the current password
  hash -- so setting a password makes every outstanding link for that account
  stop working.
"""

from __future__ import annotations

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..extensions import db
from ..models import User

_SALT = "campuscare-account-setup"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALT)


def _fingerprint(user: User) -> str:
    """A short digest of the current password hash.

    Including it in the payload means the token stops working the moment the
    password changes -- so a setup link cannot be replayed after use, and a
    leaked link is useless once the account is set up.
    """
    return (user.password_hash or "")[-16:]


def generate_setup_token(user: User) -> str:
    """A single-use, expiring token identifying ``user``."""
    return _serializer().dumps({"uid": user.id, "fp": _fingerprint(user)})


def verify_setup_token(token: str) -> User | None:
    """Resolve a token back to its user, or None if it is invalid or expired."""
    try:
        payload = _serializer().loads(
            token, max_age=current_app.config["ACTION_TOKEN_TTL_SECONDS"]
        )
    except SignatureExpired:
        current_app.logger.info("Account setup token expired")
        return None
    except BadSignature:
        current_app.logger.warning("Account setup token failed signature check")
        return None

    if not isinstance(payload, dict):
        return None

    user = db.session.get(User, payload.get("uid"))
    if user is None:
        return None

    # Reject a token minted against a different password hash.
    if payload.get("fp") != _fingerprint(user):
        current_app.logger.info("Account setup token already used for user %s", user.id)
        return None

    return user
