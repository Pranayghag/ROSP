"""Set an account's password from the command line.

    python scripts/set_password.py --email you@college.edu

The password is read interactively (hidden), or from the ``NEW_PASSWORD``
environment variable for scripted use. It is deliberately **not** accepted as a
command-line argument, because argv lands in shell history and in the process
list where other users on the machine can read it.

Intended for recovering an administrator account. Everyone else should use the
emailed setup link, which never exposes a password to anyone but its owner.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app import create_app
from app.constants import AuthorizationStatus
from app.extensions import db
from app.models import OtpCode, User

MIN_LENGTH = 8


def main() -> None:
    parser = argparse.ArgumentParser(description="Set an account password.")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--authorize",
        action="store_true",
        help="also mark the account AUTHORIZED",
    )
    options = parser.parse_args()

    password = os.environ.get("NEW_PASSWORD")
    if not password:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords did not match. Nothing was changed.")

    if len(password) < MIN_LENGTH:
        raise SystemExit(f"Use at least {MIN_LENGTH} characters. Nothing was changed.")

    app = create_app()
    with app.app_context():
        email = options.email.strip().lower()
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit(f"No account found for {email}.")

        user.set_password(password)
        user.password_set = True

        if options.authorize:
            user.authorization_status = AuthorizationStatus.AUTHORIZED

        # Any outstanding sign-in code belongs to the previous credentials.
        outstanding = db.session.scalars(
            select(OtpCode).where(OtpCode.user_id == user.id, OtpCode.used.is_(False))
        ).all()
        for code in outstanding:
            code.used = True

        db.session.commit()

        print(f"Password updated for {user.email} (role={user.role})")
        print(f"Authorization: {user.authorization_status}")
        if outstanding:
            print(f"Invalidated {len(outstanding)} outstanding verification code(s).")
        print(
            "\nNote: any account-setup link previously emailed to this user has "
            "stopped working, because those links are tied to the old password."
        )


if __name__ == "__main__":
    main()
