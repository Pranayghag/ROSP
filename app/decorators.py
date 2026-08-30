"""Authorisation decorators."""

from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user

from .constants import Role


def role_required(*roles: str):
    """Restrict a view to the given roles.

    Returns 403 rather than redirecting, so an unauthorised request never leaks
    whether the target resource exists.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


admin_required = role_required(Role.ADMIN)
staff_required = role_required(Role.STAFF, Role.ADMIN)
