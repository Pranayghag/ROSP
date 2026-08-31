"""Create (or promote) an administrator account.

    python scripts/create_admin.py
    python scripts/create_admin.py --email you@example.com --name "Your Name"

With no ``--email`` it uses ``ADMIN_EMAIL`` from the environment.

No password is set here, and none is ever typed on the command line where it
would land in shell history. The account is created with an unusable random
password and ``password_set=False``; the script prints a signed, expiring
one-time link for setting a real one, and emails the same link when SMTP is
configured.

Run this before purging demo data, so the system is never left without an
administrator.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app import create_app
from app.constants import AuthorizationStatus, Role
from app.extensions import db
from app.models import User, utcnow
from app.services import mailers
from app.services.email import absolute_url
from app.services.tokens import generate_setup_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an administrator account.")
    parser.add_argument("--email", help="defaults to ADMIN_EMAIL from the environment")
    parser.add_argument("--name", default="Administrator")
    parser.add_argument("--phone", default=None)
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help=(
            "discard the existing password and require a new one via the setup "
            "link (use when nobody knows the current password)"
        ),
    )
    options = parser.parse_args()

    app = create_app()
    with app.app_context():
        email = (options.email or app.config.get("ADMIN_EMAIL") or "").strip().lower()
        if not email:
            raise SystemExit(
                "No email given and ADMIN_EMAIL is not set.\n"
                "Either pass --email, or add ADMIN_EMAIL to your .env file."
            )

        user = db.session.scalar(select(User).where(User.email == email))

        if user is None:
            user = User(
                name=options.name,
                email=email,
                role=Role.ADMIN,
                phone=options.phone,
                designation="Administrator",
                authorization_status=AuthorizationStatus.AUTHORIZED,
                authorized_at=utcnow(),
                password_set=False,
            )
            # Unusable placeholder: the column is NOT NULL, and this value
            # cannot be guessed or used to sign in.
            user.set_password(secrets.token_urlsafe(32))
            db.session.add(user)
            db.session.flush()
            action = "Created"
        else:
            user.role = Role.ADMIN
            user.authorization_status = AuthorizationStatus.AUTHORIZED
            user.authorized_at = utcnow()
            action = "Promoted existing account"

            if options.reset_password:
                # Replace the current password with an unusable random value,
                # so the only way in is the setup link printed below.
                user.set_password(secrets.token_urlsafe(32))
                user.password_set = False
                action += " (password reset)"

        setup_url = absolute_url("auth.setup_account", token=generate_setup_token(user))
        log = mailers.notify_staff_account_created(user, setup_url)
        db.session.commit()

        ttl_hours = max(1, app.config["ACTION_TOKEN_TTL_SECONDS"] // 3600)

        print(f"\n{action}: {user.email} (role={user.role})")
        print(f"Authorization: {user.authorization_status}")
        print(f"Password set:  {'yes' if user.password_set else 'no -- use the link below'}")

        if log and log.was_delivered:
            print(f"\nA setup link was emailed to {user.email}.")
        else:
            print("\nEmail is not configured, so nothing was sent.")

        print("\nOpen this link to choose a password "
              f"(valid for {ttl_hours} hours, single use):\n")
        print(f"  {setup_url}\n")
        print("Keep it private: anyone with this link can set the password.")


if __name__ == "__main__":
    main()
