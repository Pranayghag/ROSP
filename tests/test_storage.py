"""The storage backend behind evidence photos.

Swapping where bytes live must not weaken anything. These tests pin down the
key-validation rules that stop a poisoned database value from reaching the
filesystem or a remote API, and check the backend is selected by configuration
alone.
"""

from __future__ import annotations

import pytest

from app.services import storage
from app.services.storage import (
    CloudinaryStorage,
    LocalStorage,
    StorageError,
    get_storage,
    validate_key,
)
from app.services.uploads import build_relative_path, generate_stored_name

# --------------------------------------------------------------------------
# Key validation -- the guard every backend shares
# --------------------------------------------------------------------------


def test_keys_we_generate_are_accepted():
    key = build_relative_path(generate_stored_name(".jpg"))

    assert validate_key(key) == key


@pytest.mark.parametrize("extension", [".jpg", ".jpeg", ".png", ".webp"])
def test_every_allowed_extension_validates(extension):
    key = build_relative_path(generate_stored_name(extension))

    assert validate_key(key)


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd",
        "2026/08/31/../../../secret.jpg",
        "/absolute/path.jpg",
        "2026/08/31/shell.php",
        "2026/08/31/notahash.jpg",
        "2026/8/31/0123456789abcdef0123456789abcdef.jpg",   # unpadded month
        "2026/08/31/0123456789abcdef0123456789abcdef.exe",
        "http://evil.example/image.jpg",
        "",
        None,
    ],
)
def test_hostile_keys_are_refused(hostile):
    with pytest.raises(StorageError):
        validate_key(hostile)


# --------------------------------------------------------------------------
# Local backend
# --------------------------------------------------------------------------


def test_local_round_trip(app):
    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])
        key = build_relative_path(generate_stored_name(".jpg"))

        size = backend.save(key, b"some bytes", "image/jpeg")
        assert size == 10
        assert backend.exists(key)
        assert backend.load(key) == b"some bytes"

        backend.delete(key)
        assert not backend.exists(key)


def test_local_refuses_to_escape_its_root(app):
    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])

        with pytest.raises(StorageError):
            backend.path_for("../../outside.jpg")


def test_local_reports_a_missing_object(app):
    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])
        key = build_relative_path(generate_stored_name(".jpg"))

        assert not backend.exists(key)
        with pytest.raises(StorageError):
            backend.load(key)


def test_deleting_a_missing_object_is_not_an_error(app):
    with app.app_context():
        backend = LocalStorage(app.config["UPLOAD_DIR"])
        backend.delete(build_relative_path(generate_stored_name(".jpg")))
        backend.delete("not-even-a-valid-key")


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------


def test_local_is_the_default(app):
    with app.app_context():
        assert isinstance(get_storage(), LocalStorage)


def test_cloudinary_is_chosen_by_configuration(app, monkeypatch):
    """Selection is configuration only -- no code path names a backend."""
    built = {}

    class FakeCloudinary(CloudinaryStorage):
        def __init__(self, folder="campuscare"):
            built["folder"] = folder  # skip the real SDK and credentials

    monkeypatch.setattr(storage, "CloudinaryStorage", FakeCloudinary)

    app.config["STORAGE_BACKEND"] = "cloudinary"
    app.config["CLOUDINARY_FOLDER"] = "campuscare-test"

    with app.app_context():
        backend = get_storage()

    assert isinstance(backend, FakeCloudinary)
    assert built["folder"] == "campuscare-test"


def test_an_unknown_backend_name_falls_back_to_local(app):
    """A typo in configuration must not silently disable storage."""
    app.config["STORAGE_BACKEND"] = "s3-maybe"

    with app.app_context():
        assert isinstance(get_storage(), LocalStorage)


# --------------------------------------------------------------------------
# Uploads go through whichever backend is configured
# --------------------------------------------------------------------------


def test_store_evidence_uses_the_configured_backend(app, monkeypatch):
    """A different backend must receive the bytes, with no other change."""
    from sqlalchemy import select

    from app.extensions import db
    from app.models import Category, Complaint, Location, User
    from app.services.uploads import store_evidence
    from tests.conftest import make_image_bytes, make_upload

    saved: dict[str, bytes] = {}

    class MemoryStorage(LocalStorage):
        def __init__(self):
            pass

        def save(self, key, data, mime):
            validate_key(key)
            saved[key] = data
            return len(data)

        def load(self, key):
            return saved[key]

        def delete(self, key):
            saved.pop(key, None)

        def exists(self, key):
            return key in saved

    with app.app_context():
        category = db.session.scalar(select(Category))
        location = db.session.scalar(select(Location))
        student = db.session.scalar(select(User))

        complaint = Complaint(
            title="Backend swap test",
            description="Checking the storage abstraction end to end.",
            category_id=category.id,
            location_id=location.id,
            student_id=student.id,
        )
        db.session.add(complaint)
        db.session.flush()

        monkeypatch.setattr(storage, "get_storage", lambda: MemoryStorage())
        from app.services import uploads

        monkeypatch.setattr(uploads, "get_storage", lambda: MemoryStorage())

        with app.test_request_context():
            attachments = store_evidence(
                [make_upload(make_image_bytes(), "photo.jpg")], complaint, student.id
            )

        assert len(attachments) == 1
        assert len(saved) == 1

        key = attachments[0].file_path
        assert key in saved
        # Re-encoded through Pillow, so not the bytes that arrived.
        assert saved[key][:3] == b"\xff\xd8\xff"
        # And nothing touched the local upload directory.
        assert not list(app.config["UPLOAD_DIR"].rglob("*.jpg"))
