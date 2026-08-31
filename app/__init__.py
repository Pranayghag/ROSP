"""Application factory.

Creating the app inside a function (rather than at import time) lets the test
suite build an isolated instance with its own configuration and database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import current_user

from config import get_config

from .constants import (
    ATTACHMENT_TYPE_LABELS,
    AUTHORIZATION_COLOURS,
    AUTHORIZATION_LABELS,
    PRIORITY_COLOURS,
    STATUS_COLOURS,
    STATUS_LABELS,
    AttachmentType,
    AuthorizationStatus,
    Priority,
    Role,
    Status,
)
from .extensions import csrf, db, login_manager


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    # Uploads must exist before the first request writes to it.
    app.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Importing models registers them with SQLAlchemy's metadata.
    from . import models  # noqa: F401

    _register_blueprints(app)
    _register_error_handlers(app)
    _register_template_helpers(app)
    _register_security_headers(app)
    _register_cli(app)

    return app


def _register_blueprints(app: Flask) -> None:
    from .blueprints.admin import bp as admin_bp
    from .blueprints.attachments import bp as attachments_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.complaints import bp as complaints_bp
    from .blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(complaints_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(admin_bp)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(401)
    def unauthorised(_error):
        return redirect(url_for("auth.login", next=request.path)), 302

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def payload_too_large(_error):
        """Werkzeug rejects an oversized request before any view runs.

        Without this handler the student would see a bare browser error, so
        translate it into the same guidance the upload validator would give.
        """
        limit = app.config["MAX_FILE_SIZE_MB"]
        count = app.config["MAX_FILES_PER_COMPLAINT"]
        flash(
            f"Those files are too large. Each photo must be under {limit} MB, "
            f"with at most {count} photos per complaint.",
            "danger",
        )
        return redirect(url_for("complaints.new")), 302

    @app.errorhandler(500)
    def server_error(_error):  # pragma: no cover - exercised only on real faults
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_template_helpers(app: Flask) -> None:
    from .services import notifications

    @app.context_processor
    def inject_globals():
        """Expose shared vocabulary and helpers to every template.

        This also runs when an email template is rendered from a CLI script,
        where there is no logged-in user and ``current_user`` is None rather
        than an anonymous user -- hence the defensive getattr.
        """
        signed_in = getattr(current_user, "is_authenticated", False)
        return {
            "Status": Status,
            "Priority": Priority,
            "Role": Role,
            "AttachmentType": AttachmentType,
            "AuthorizationStatus": AuthorizationStatus,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_COLOURS": STATUS_COLOURS,
            "PRIORITY_COLOURS": PRIORITY_COLOURS,
            "ATTACHMENT_TYPE_LABELS": ATTACHMENT_TYPE_LABELS,
            "AUTHORIZATION_LABELS": AUTHORIZATION_LABELS,
            "AUTHORIZATION_COLOURS": AUTHORIZATION_COLOURS,
            "unread_notifications": (
                notifications.unread_count(current_user) if signed_in else 0
            ),
            "max_photos": app.config["MAX_FILES_PER_COMPLAINT"],
            "max_photo_mb": app.config["MAX_FILE_SIZE_MB"],
            "current_year": datetime.now(UTC).year,
        }

    @app.template_filter("datetime")
    def format_datetime(value, fmt: str = "%d %b %Y, %H:%M"):
        if not value:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.strftime(fmt)

    @app.template_filter("since")
    def time_since(value):
        """Compact relative time, e.g. '3h ago'."""
        if not value:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        seconds = (datetime.now(UTC) - value).total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        return f"{int(seconds // 86400)}d ago"

    @app.template_filter("status_label")
    def status_label(value):
        return STATUS_LABELS.get(value, str(value).replace("_", " ").title())


def _register_security_headers(app: Flask) -> None:
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


def _register_cli(app: Flask) -> None:
    """Register ``flask`` CLI helpers for setting up the database."""

    @app.cli.command("init-db")
    def init_db_command():
        """Create every table defined by the models."""
        db.create_all()
        print("Database tables created.")

    @app.cli.command("seed")
    def seed_command():
        """Load demo categories, locations, users and complaints."""
        from scripts.seed import seed_all

        seed_all()
