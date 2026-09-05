"""Application configuration.

Values are read from the environment (optionally via a local ``.env`` file) so
that no credential is ever committed. See ``.env.example`` for the full list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote_plus

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalise_database_url(url: str) -> str:
    """Make a hosted provider's URL usable by SQLAlchemy 2.

    Neon, Vercel Postgres, Render and Heroku all hand out ``postgres://``,
    a scheme SQLAlchemy 2 removed. They also omit the driver, so pin psycopg
    explicitly rather than depending on whichever DBAPI happens to be
    installed. Anything already carrying a driver is left alone.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    # Managed Postgres requires TLS; most providers include it, some do not.
    if url.startswith("postgresql+psycopg://") and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    return url


def _database_uri() -> str:
    """Build the SQLAlchemy URI.

    ``DATABASE_URL`` wins if it is set (handy for Docker or CI). Otherwise the
    individual MySQL settings are assembled into a PyMySQL URI. Credentials are
    URL-quoted so passwords containing ``@`` or ``:`` do not corrupt the URI.
    """
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return _normalise_database_url(explicit)

    user = quote_plus(os.getenv("MYSQL_USER", "root"))
    password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = _env_int("MYSQL_PORT", 3306)
    name = os.getenv("MYSQL_DB", "rosp")

    credentials = f"{user}:{password}" if password else user
    return f"mysql+pymysql://{credentials}@{host}:{port}/{name}?charset=utf8mb4"


class Config:
    """Base configuration shared by every environment."""

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-secret-change-me")

    # --- Database ---------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict] = {"pool_pre_ping": True, "pool_recycle": 3600}

    # --- Evidence upload --------------------------------------------------
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads")).resolve()
    MAX_FILE_SIZE_MB = _env_int("MAX_FILE_SIZE_MB", 5)
    MAX_FILES_PER_COMPLAINT = _env_int("MAX_FILES_PER_COMPLAINT", 5)

    #: Extensions a student may submit. Checked against the *detected* image
    #: type as well -- see ``app/services/uploads.py``.
    ALLOWED_EXTENSIONS: ClassVar[set[str]] = {".jpg", ".jpeg", ".png", ".webp"}
    ALLOWED_MIME_TYPES: ClassVar[set[str]] = {"image/jpeg", "image/png", "image/webp"}

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    # Hard ceiling enforced by Werkzeug before the request body is parsed, so a
    # multi-gigabyte POST is rejected at the door rather than buffered.
    # 1 MB of slack covers the text fields and multipart overhead.
    MAX_CONTENT_LENGTH = (
        _env_int("MAX_FILE_SIZE_MB", 5)
        * _env_int("MAX_FILES_PER_COMPLAINT", 5)
        * 1024
        * 1024
    ) + (1024 * 1024)

    # --- Evidence storage --------------------------------------------------
    #: "local" writes under UPLOAD_DIR; "cloudinary" uses object storage.
    #:
    #: Serverless and free PaaS tiers have an ephemeral filesystem -- files are
    #: wiped on every redeploy -- so any deployed instance must use object
    #: storage or it will silently lose evidence photos.
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()

    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
    #: Read from the environment only -- never written to source or committed.
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
    CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "campuscare")

    # --- Session cookies --------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)

    # --- Branding ---------------------------------------------------------
    APP_NAME = os.getenv("APP_NAME", "CampusCare")
    APP_TAGLINE = "Smart College Complaint & Maintenance Management System"

    #: Absolute base used to build links inside emails. Emails are read outside
    #: the browser session, so relative URLs are useless there.
    BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000").rstrip("/")

    # --- Email ------------------------------------------------------------
    #: Where administrator notifications are sent.
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = _env_int("SMTP_PORT", 587)
    SMTP_USER = os.getenv("SMTP_USER", "")
    #: Read from the environment only -- never written to source or committed.
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", True)
    SMTP_TIMEOUT = _env_int("SMTP_TIMEOUT", 20)
    EMAIL_FROM = os.getenv("EMAIL_FROM", "") or SMTP_USER

    #: True when enough SMTP settings exist to actually deliver mail.
    #:
    #: When false the application still runs: messages are recorded in the
    #: email log and printed to the server log instead of being sent, so the
    #: whole workflow can be exercised without credentials. Nothing silently
    #: claims to have sent an email that was not sent.
    EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

    # --- Two-step verification (2FA) --------------------------------------
    #: When false, signing in is a single step: email and password only.
    #:
    #: The OTP machinery stays in place and can be switched back on with one
    #: environment variable -- nothing about it has been deleted. Turning it on
    #: is strongly recommended for any deployment reachable from a network, and
    #: requires working SMTP so codes can actually be delivered.
    TWO_FACTOR_ENABLED = _env_bool("TWO_FACTOR_ENABLED", False)

    OTP_LENGTH = 6
    OTP_TTL_SECONDS = _env_int("OTP_TTL_SECONDS", 300)          # 5 minutes
    OTP_MAX_ATTEMPTS = _env_int("OTP_MAX_ATTEMPTS", 5)
    OTP_RESEND_COOLDOWN_SECONDS = _env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)

    #: Password/OTP hashing cost. None uses Werkzeug's default, which is
    #: deliberately slow. Only the test config lowers it -- a fast hash on a
    #: 6-digit OTP would make it brute-forceable from a database dump.
    PASSWORD_HASH_METHOD = None

    #: Signed, expiring tokens for links emailed to staff (e.g. set password).
    ACTION_TOKEN_TTL_SECONDS = _env_int("ACTION_TOKEN_TTL_SECONDS", 172800)  # 48h


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)


class TestingConfig(Config):
    """Tests run against an isolated SQLite database.

    This keeps ``pytest`` (and CI) runnable with no MySQL server, while the
    application itself still targets MySQL in development and production.
    """

    TESTING = True
    WTF_CSRF_ENABLED = False

    # A real KDF cost would make the suite take minutes: every test builds a
    # fresh set of accounts. Security of the hash is not what these tests
    # exercise, so use the cheapest valid setting here and nowhere else.
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1"

    # Two-step verification is on under test regardless of the deployment
    # default, because it is the more complex path and needs the coverage.
    # tests/test_single_step_login.py flips it off to cover the other branch.
    TWO_FACTOR_ENABLED = True
    SQLALCHEMY_DATABASE_URI = "sqlite+pysqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict] = {}


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None):
    """Resolve a config class by name, defaulting to ``FLASK_ENV``."""
    key = (name or os.getenv("FLASK_ENV") or "development").lower()
    return CONFIGS.get(key, DevelopmentConfig)
