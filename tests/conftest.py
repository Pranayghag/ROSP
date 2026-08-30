"""Shared pytest fixtures.

Tests run against a throwaway SQLite file and a throwaway upload directory, so
they need no MySQL server and leave nothing behind. The application code is
identical either way -- only the connection URI differs.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.constants import Role
from app.extensions import db
from app.models import Category, Location, User


@pytest.fixture
def app(tmp_path):
    """A fully configured application bound to temporary storage."""
    application = create_app("testing")
    application.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite'}",
        UPLOAD_DIR=tmp_path / "uploads",
        WTF_CSRF_ENABLED=False,
        TESTING=True,
    )
    application.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)

    with application.app_context():
        db.create_all()
        _create_baseline_data()
        yield application
        db.session.remove()
        db.drop_all()


def _create_baseline_data() -> None:
    """One category, one location, and one user of each role."""
    db.session.add_all(
        [
            Category(name="Plumbing & Water", description="Leaks and taps", sla_hours=24),
            Category(name="Classroom Equipment", description="Projectors", sla_hours=48),
            Location(name="Room 204", building="Block A"),
        ]
    )

    people = [
        ("Student One", "student1@rosp.edu", Role.STUDENT),
        ("Student Two", "student2@rosp.edu", Role.STUDENT),
        ("Staff One", "staff1@rosp.edu", Role.STAFF),
        ("Staff Two", "staff2@rosp.edu", Role.STAFF),
        ("Admin", "admin@rosp.edu", Role.ADMIN),
    ]
    for name, email, role in people:
        user = User(name=name, email=email, role=role)
        user.set_password("Password@123")
        db.session.add(user)

    db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def users(app):
    """Every seeded user, keyed by the local part of their email address."""
    from sqlalchemy import select

    return {
        user.email.split("@")[0]: user
        for user in db.session.scalars(select(User)).all()
    }


@pytest.fixture
def login(client):
    """Sign in as a given email; returns the response."""

    def _login(email: str, password: str = "Password@123"):
        return client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    return _login


# --------------------------------------------------------------------------
# Upload helpers
# --------------------------------------------------------------------------


def make_image_bytes(fmt: str = "JPEG", size: tuple[int, int] = (240, 180)) -> bytes:
    """A real, decodable image of the requested format."""
    buffer = BytesIO()
    Image.new("RGB", size, (120, 150, 190)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def image_bytes():
    return make_image_bytes


def make_upload(
    data: bytes, filename: str = "photo.jpg", content_type: str = "image/jpeg"
) -> FileStorage:
    """Wrap raw bytes as a Werkzeug upload, as a real request would deliver it."""
    return FileStorage(
        stream=BytesIO(data), filename=filename, content_type=content_type
    )


@pytest.fixture
def upload_factory():
    return make_upload
