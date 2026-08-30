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


def _database_uri() -> str:
    """Build the SQLAlchemy URI.

    ``DATABASE_URL`` wins if it is set (handy for Docker or CI). Otherwise the
    individual MySQL settings are assembled into a PyMySQL URI. Credentials are
    URL-quoted so passwords containing ``@`` or ``:`` do not corrupt the URI.
    """
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit

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

    # --- Session cookies --------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)


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
