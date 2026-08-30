"""Registration, sign-in and sign-out."""

from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from ..constants import Role
from ..extensions import db
from ..forms import LoginForm, RegisterForm
from ..models import User

bp = Blueprint("auth", __name__)


def _is_safe_redirect(target: str | None) -> bool:
    """Allow only same-site relative redirects, blocking open-redirect abuse."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith("/")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            select(User).where(User.email == form.email.data.strip().lower())
        )
        # One generic message for both branches: never reveal which addresses
        # are registered.
        if user is None or not user.check_password(form.password.data):
            flash("Incorrect email or password.", "danger")
        else:
            login_user(user)
            flash(f"Welcome back, {user.name}.", "success")
            nxt = request.args.get("next")
            if _is_safe_redirect(nxt):
                return redirect(nxt)
            return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        existing = db.session.scalar(select(User).where(User.email == email))
        if existing:
            flash("An account with that email already exists.", "warning")
        else:
            # Self-registration always creates a student. Staff and admin
            # accounts are created deliberately by an administrator.
            user = User(
                name=form.name.data.strip(),
                email=email,
                role=Role.STUDENT,
                roll_no=(form.roll_no.data or "").strip() or None,
                department=(form.department.data or "").strip() or None,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash("Account created. Welcome to ROSP.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("auth/register.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
