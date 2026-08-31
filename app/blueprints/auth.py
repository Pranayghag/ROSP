"""Registration, sign-in, sign-out and account setup.

Signing in has one or two stages, depending on ``TWO_FACTOR_ENABLED``.

**Single step** (the default): ``/login`` checks the password, the account's
authorization status, and whether a password has been set at all -- then signs
the user in. No code is generated.

**Two steps** (``TWO_FACTOR_ENABLED=true``):

1. ``/login``  -- email and password. On success the user is **not** logged in
   yet; their id is parked in the session and a one-time code is emailed.
2. ``/verify`` -- the 6-digit code. Only here does ``login_user()`` run.

The pending state is deliberately thin: a user id and a timestamp, nothing
more. It expires with the code, so an abandoned half-login cannot be resumed
later, and holding it grants no access on its own.

Every other guard -- password check, authorization status, password-set check
-- runs identically in both modes. Turning two-step off removes the second
factor and nothing else.
"""

from __future__ import annotations

from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from ..constants import AuthorizationStatus, Role
from ..extensions import db
from ..forms import LoginForm, OtpForm, RegisterForm, SetPasswordForm
from ..models import User, utcnow
from ..services import mailers, notifications, otp
from ..services.tokens import verify_setup_token

bp = Blueprint("auth", __name__)

#: Session keys holding the half-completed login.
PENDING_USER = "pending_user_id"
PENDING_SINCE = "pending_since"
PENDING_NEXT = "pending_next"

#: Development-only: the code, so it can be shown on screen when there is no
#: way to email it. See :func:`_show_code_on_screen`.
PENDING_DEV_CODE = "pending_dev_code"


def _show_code_on_screen() -> bool:
    """Whether to display the verification code in the page itself.

    Only when the server is in debug mode **and** no SMTP credentials exist --
    that is, a developer running locally who otherwise could not sign in at
    all, because the code has nowhere to go.

    Both halves matter. ``DEBUG`` is false in production, and configuring SMTP
    turns this off even in development, so a real deployment can never reach
    this branch. The page says loudly that it is a development affordance.
    """
    return bool(current_app.debug) and not current_app.config["EMAIL_ENABLED"]


def _is_safe_redirect(target: str | None) -> bool:
    """Allow only same-site relative redirects, blocking open-redirect abuse."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith("/")


def _clear_pending() -> None:
    for key in (PENDING_USER, PENDING_SINCE, PENDING_NEXT, PENDING_DEV_CODE):
        session.pop(key, None)


def _pending_user() -> User | None:
    """The user waiting on a code, if that state is still fresh."""
    user_id = session.get(PENDING_USER)
    started = session.get(PENDING_SINCE)
    if not user_id or not started:
        return None

    # The half-login lives exactly as long as a code could.
    age = utcnow().timestamp() - float(started)
    if age > current_app.config["OTP_TTL_SECONDS"] * 2:
        _clear_pending()
        return None

    return db.session.get(User, int(user_id))


def _begin_two_step(user: User, next_url: str | None) -> bool:
    """Issue and email a code, parking the login. True if the mail went out."""
    code = otp.issue(user)
    log = mailers.send_otp(user, code)
    db.session.commit()

    session[PENDING_USER] = user.id
    session[PENDING_SINCE] = str(utcnow().timestamp())
    if _is_safe_redirect(next_url):
        session[PENDING_NEXT] = next_url

    if _show_code_on_screen():
        session[PENDING_DEV_CODE] = code

    return bool(log and log.was_delivered)


# --------------------------------------------------------------------------
# Sign in
# --------------------------------------------------------------------------


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
            return render_template("auth/login.html", form=form)

        # An account created by an admin has no password of its own yet.
        if not user.password_set:
            flash(
                "This account still needs a password. Use the setup link that "
                "was emailed to you, or ask an administrator to resend it.",
                "warning",
            )
            return render_template("auth/login.html", form=form)

        # Authorization is checked here, before any code is sent -- there is no
        # point emailing a code to someone who cannot get in anyway.
        if not user.is_authorized:
            flash(user.blocked_reason, "warning")
            return render_template("auth/login.html", form=form)

        next_url = request.args.get("next")

        # Single-step sign-in: no code is generated, so none can leak or be
        # brute-forced. The authorization and password checks above still run.
        if not current_app.config["TWO_FACTOR_ENABLED"]:
            login_user(user)
            flash(f"Welcome back, {user.name}.", "success")
            if _is_safe_redirect(next_url):
                return redirect(next_url)
            return redirect(url_for("main.dashboard"))

        delivered = _begin_two_step(user, next_url)
        if delivered:
            flash("We emailed you a 6-digit verification code.", "info")
        elif _show_code_on_screen():
            # The next page shows the code itself, so saying "check your email"
            # would just be wrong.
            flash("Email is not configured. Your code is shown below.", "warning")
        else:
            flash(
                "Your password was accepted, but the verification code could "
                "not be emailed. Ask an administrator to check the mail "
                "settings.",
                "warning",
            )
        return redirect(url_for("auth.verify"))

    return render_template("auth/login.html", form=form)


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Step two: the one-time code."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if not current_app.config["TWO_FACTOR_ENABLED"]:
        _clear_pending()
        return redirect(url_for("auth.login"))

    user = _pending_user()
    if user is None:
        flash("Your sign-in attempt expired. Please start again.", "warning")
        return redirect(url_for("auth.login"))

    form = OtpForm()
    if form.validate_on_submit():
        result = otp.verify(user, form.code.data)
        db.session.commit()

        if result.ok:
            next_url = session.get(PENDING_NEXT)
            _clear_pending()
            login_user(user)
            flash(f"Welcome back, {user.name}.", "success")
            if _is_safe_redirect(next_url):
                return redirect(next_url)
            return redirect(url_for("main.dashboard"))

        flash(result.message, "danger")
        if result.exhausted:
            return redirect(url_for("auth.verify"))

    return render_template(
        "auth/verify.html",
        form=form,
        email=user.email,
        resend_in=otp.seconds_until_resend(user),
        dev_code=session.get(PENDING_DEV_CODE) if _show_code_on_screen() else None,
    )


@bp.route("/verify/resend", methods=["POST"])
def resend_code():
    if not current_app.config["TWO_FACTOR_ENABLED"]:
        return redirect(url_for("auth.login"))

    user = _pending_user()
    if user is None:
        flash("Your sign-in attempt expired. Please start again.", "warning")
        return redirect(url_for("auth.login"))

    waiting = otp.seconds_until_resend(user)
    if waiting > 0:
        flash(f"Please wait {waiting} more second(s) before requesting a new code.", "warning")
        return redirect(url_for("auth.verify"))

    code = otp.issue(user)
    log = mailers.send_otp(user, code)
    db.session.commit()

    if _show_code_on_screen():
        session[PENDING_DEV_CODE] = code

    if log and log.was_delivered:
        flash("A new code is on its way.", "info")
    else:
        flash("The code could not be emailed. Contact an administrator.", "danger")
    return redirect(url_for("auth.verify"))


@bp.route("/verify/cancel", methods=["POST"])
def cancel_verify():
    _clear_pending()
    flash("Sign-in cancelled.", "info")
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()

        if db.session.scalar(select(User).where(User.email == email)):
            flash("An account with that email already exists.", "warning")
            return render_template("auth/register.html", form=form)

        # Re-check the role server-side. A browser can submit anything, and an
        # administrator account must never be self-registerable.
        role = form.role.data
        if role not in (Role.STUDENT, Role.STAFF):
            flash("Choose either Student or Staff.", "danger")
            return render_template("auth/register.html", form=form)

        is_staff = role == Role.STAFF

        user = User(
            name=form.name.data.strip(),
            email=email,
            role=role,
            phone=(form.phone.data or "").strip() or None,
            department=(form.department.data or "").strip() or None,
            roll_no=(form.roll_no.data or "").strip() or None if not is_staff else None,
            designation=(form.designation.data or "").strip() or None if is_staff else None,
            staff_id=(form.staff_id.data or "").strip() or None if is_staff else None,
            # Students are usable immediately; staff wait for an administrator.
            authorization_status=(
                AuthorizationStatus.PENDING if is_staff else AuthorizationStatus.AUTHORIZED
            ),
            password_set=True,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()

        if is_staff:
            notifications.notify_admins(
                f"New staff registration from {user.name} awaiting authorization.",
                None,
            )
            log = mailers.notify_admin_new_staff(user)
            db.session.commit()

            if log is None:
                current_app.logger.warning("ADMIN_EMAIL unset: admin was not emailed")
            return render_template("auth/registration_pending.html", user=user)

        db.session.commit()

        if not current_app.config["TWO_FACTOR_ENABLED"]:
            login_user(user)
            flash(f"Account created. Welcome, {user.name}.", "success")
            return redirect(url_for("main.dashboard"))

        # Students go straight into the normal two-step sign-in.
        delivered = _begin_two_step(user, None)
        if delivered:
            flash("Account created. We emailed you a verification code.", "success")
        elif _show_code_on_screen():
            flash("Account created. Email is off, so your code is shown below.", "warning")
        else:
            flash(
                "Account created, but the verification code could not be "
                "emailed. Ask an administrator to check the mail settings.",
                "warning",
            )
        return redirect(url_for("auth.verify"))

    return render_template("auth/register.html", form=form)


# --------------------------------------------------------------------------
# Account setup (admin-created accounts)
# --------------------------------------------------------------------------


@bp.route("/account/setup/<token>", methods=["GET", "POST"])
def setup_account(token: str):
    """Let a staff member set their own password from an emailed link.

    The token is signed and expiring, and stops working the moment a password
    is set -- so the link cannot be reused.
    """
    user = verify_setup_token(token)
    if user is None:
        flash(
            "That setup link is invalid or has expired. Ask an administrator "
            "to send a new one.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.password_set = True
        db.session.commit()

        flash("Password set. Please sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/setup_account.html", form=form, user=user)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    _clear_pending()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
