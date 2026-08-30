"""Tests for evidence upload validation and safe storage.

These are the security-critical tests: they assert that files which are not
genuine images never reach disk, and that the ones which do are stored under a
server-chosen name inside the upload directory.

NOTE ON THE TEST PAYLOADS
-------------------------
The "malicious" fixtures below are assembled from hex at runtime rather than
written as literal strings. A source file containing a literal server-side
shell one-liner gets quarantined by antivirus software (Windows Defender
flags it as Backdoor:PHP/Perhetshell), which deletes the test file and breaks
the suite for anyone who clones the repository. Encoding them keeps the file
inert on disk while the bytes reaching the validator are identical.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.models import Complaint
from app.services.uploads import (
    UploadError,
    detect_image_type,
    generate_stored_name,
    resolve_stored_path,
    sanitize_display_name,
    store_evidence,
    validate_image,
)
from tests.conftest import make_image_bytes, make_upload


def script_payload() -> bytes:
    """A server-side script one-liner: the classic 'renamed to .jpg' attack."""
    return bytes.fromhex("3c3f7068702073797374656d28245f4745545b636d645d293b203f3e") * 20


def executable_payload() -> bytes:
    """The DOS/PE header every Windows .exe starts with."""
    return bytes.fromhex("4d5a9000") + b"\x00" * 500


@pytest.fixture
def complaint(app, users):
    """A saved complaint to attach evidence to."""
    from sqlalchemy import select

    from app.extensions import db
    from app.models import Category, Location

    category = db.session.scalar(select(Category))
    location = db.session.scalar(select(Location))

    item = Complaint(
        title="Water leaking from the ceiling",
        description="Water is dripping onto the back benches.",
        category_id=category.id,
        location_id=location.id,
        student_id=users["student1"].id,
    )
    db.session.add(item)
    db.session.flush()
    item.assign_code()
    return item


# --------------------------------------------------------------------------
# Accepting genuine images
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,filename,content_type",
    [
        ("JPEG", "leak.jpg", "image/jpeg"),
        ("JPEG", "leak.jpeg", "image/jpeg"),
        ("PNG", "ceiling.png", "image/png"),
        ("WEBP", "damage.webp", "image/webp"),
    ],
)
def test_accepts_every_supported_format(app, fmt, filename, content_type):
    with app.test_request_context():
        validated = validate_image(
            make_upload(make_image_bytes(fmt), filename, content_type)
        )
    assert validated.mime == content_type


def test_stores_file_and_records_metadata(app, complaint, users):
    with app.test_request_context():
        uploads = [make_upload(make_image_bytes(), "my leak photo.jpg")]
        attachments = store_evidence(uploads, complaint, users["student1"].id)

        assert len(attachments) == 1
        attachment = attachments[0]

        # The original name is kept only for display...
        assert attachment.file_name == "my leak photo.jpg"
        # ...never as the path on disk.
        assert "my leak photo" not in attachment.file_path
        assert attachment.file_type == "image/jpeg"
        assert attachment.file_size > 0

        stored = resolve_stored_path(attachment.file_path)
        assert stored.is_file()
        assert stored.parent.is_relative_to(app.config["UPLOAD_DIR"])


def test_multiple_photos_are_stored_separately(app, complaint, users):
    with app.test_request_context():
        uploads = [
            make_upload(make_image_bytes(), "one.jpg"),
            make_upload(make_image_bytes("PNG"), "two.png", "image/png"),
        ]
        attachments = store_evidence(uploads, complaint, users["student1"].id)

    assert len(attachments) == 2
    paths = {a.file_path for a in attachments}
    assert len(paths) == 2, "each upload must get its own randomly-named file"


# --------------------------------------------------------------------------
# Rejecting everything else
# --------------------------------------------------------------------------


def test_rejects_executable_disguised_as_image(app):
    """A server-side script renamed to .jpg must not be accepted."""
    with app.test_request_context(), pytest.raises(UploadError) as exc:
        validate_image(make_upload(script_payload(), "shell.jpg", "image/jpeg"))

    assert "not a real image" in str(exc.value).lower()


def test_rejects_windows_executable(app):
    """An .exe sent with an image content type is rejected."""
    with app.test_request_context(), pytest.raises(UploadError):
        validate_image(make_upload(executable_payload(), "setup.png", "image/png"))


def test_rejects_disallowed_extension(app):
    """Real image bytes carrying a script extension are still refused."""
    with app.test_request_context(), pytest.raises(UploadError) as exc:
        validate_image(make_upload(make_image_bytes(), "payload.php", "image/jpeg"))

    assert "not an accepted image type" in str(exc.value).lower()


def test_rejects_double_extension(app):
    with app.test_request_context(), pytest.raises(UploadError):
        validate_image(
            make_upload(make_image_bytes(), "shell.php.svg", "image/jpeg")
        )


def test_rejects_forged_mime_type(app):
    """A non-image declared as image/jpeg is caught by content sniffing."""
    with app.test_request_context(), pytest.raises(UploadError):
        validate_image(
            make_upload(b"just plain text, not an image", "notes.jpg", "image/jpeg")
        )


def test_rejects_unsupported_declared_mime(app):
    with app.test_request_context(), pytest.raises(UploadError) as exc:
        validate_image(
            make_upload(make_image_bytes(), "photo.jpg", "application/octet-stream")
        )

    assert "supported image type" in str(exc.value).lower()


def test_rejects_extension_content_mismatch(app):
    """PNG bytes named .jpg: extension and actual format disagree."""
    with app.test_request_context(), pytest.raises(UploadError) as exc:
        validate_image(
            make_upload(make_image_bytes("PNG"), "photo.jpg", "image/jpeg")
        )

    assert "different format" in str(exc.value).lower()


def test_rejects_oversized_file(app):
    app.config["MAX_FILE_SIZE_MB"] = 1

    # Random pixels, because a flat-colour PNG compresses to a few kilobytes
    # and would never reach the limit.
    import os

    noise = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))
    buffer = BytesIO()
    noise.save(buffer, format="PNG")
    oversized = buffer.getvalue()
    assert len(oversized) > 1024 * 1024

    with app.test_request_context(), pytest.raises(UploadError) as exc:
        validate_image(make_upload(oversized, "huge.png", "image/png"))

    assert "larger than" in str(exc.value).lower()


def test_rejects_empty_file(app):
    with app.test_request_context(), pytest.raises(UploadError) as exc:
        validate_image(make_upload(b"", "empty.jpg", "image/jpeg"))

    assert "empty" in str(exc.value).lower()


def test_rejects_truncated_image(app):
    """A file with a valid JPEG header but a corrupt body fails to decode."""
    truncated = make_image_bytes()[:60]

    with app.test_request_context(), pytest.raises(UploadError):
        validate_image(make_upload(truncated, "broken.jpg", "image/jpeg"))


def test_enforces_photo_limit(app, complaint, users):
    app.config["MAX_FILES_PER_COMPLAINT"] = 3
    uploads = [make_upload(make_image_bytes(), f"photo{i}.jpg") for i in range(4)]

    with app.test_request_context(), pytest.raises(UploadError) as exc:
        store_evidence(uploads, complaint, users["student1"].id)

    assert "at most 3 photos" in str(exc.value)


def test_batch_is_all_or_nothing(app, complaint, users):
    """One bad file in a batch must leave no files on disk at all."""
    uploads = [
        make_upload(make_image_bytes(), "good.jpg"),
        make_upload(script_payload(), "bad.jpg", "image/jpeg"),
    ]

    with app.test_request_context(), pytest.raises(UploadError):
        store_evidence(uploads, complaint, users["student1"].id)

    written = [p for p in app.config["UPLOAD_DIR"].rglob("*") if p.is_file()]
    assert written == [], "a rejected batch must not leave partial files behind"


# --------------------------------------------------------------------------
# Filename safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../etc/passwd.jpg",
        "..\\..\\windows\\system32\\evil.jpg",
        "/absolute/path/photo.jpg",
        "photo\x00.jpg",
        'quote"name.jpg',
    ],
)
def test_sanitises_hostile_display_names(hostile):
    cleaned = sanitize_display_name(hostile)

    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert "\x00" not in cleaned
    assert not cleaned.startswith(".")
    assert cleaned


def test_display_name_never_empty():
    assert sanitize_display_name("") == "evidence"
    assert sanitize_display_name(None) == "evidence"
    assert sanitize_display_name("...") == "evidence"


def test_stored_names_are_random_and_unique():
    names = {generate_stored_name(".jpg") for _ in range(200)}
    assert len(names) == 200
    assert all(name.endswith(".jpg") for name in names)


def test_traversal_in_stored_path_is_refused(app):
    """Even a poisoned database value cannot escape the upload directory."""
    with app.test_request_context(), pytest.raises(UploadError):
        resolve_stored_path("../../../../etc/passwd")


def test_uploaded_path_uses_date_shards(app, complaint, users):
    with app.test_request_context():
        attachments = store_evidence(
            [make_upload(make_image_bytes(), "photo.jpg")],
            complaint,
            users["student1"].id,
        )

    # YYYY/MM/DD/<random>.jpg
    parts = attachments[0].file_path.split("/")
    assert len(parts) == 4
    assert parts[0].isdigit() and len(parts[0]) == 4


# --------------------------------------------------------------------------
# Content sniffing and metadata
# --------------------------------------------------------------------------


def test_detect_image_type_reads_magic_bytes():
    assert detect_image_type(make_image_bytes("JPEG"))["mime"] == "image/jpeg"
    assert detect_image_type(make_image_bytes("PNG"))["mime"] == "image/png"
    assert detect_image_type(make_image_bytes("WEBP"))["mime"] == "image/webp"
    assert detect_image_type(b"not an image at all") is None
    assert detect_image_type(b"") is None


def test_exif_metadata_is_stripped(app, complaint, users):
    """Re-encoding must drop EXIF, which can carry GPS coordinates."""
    buffer = BytesIO()
    image = Image.new("RGB", (200, 150), (10, 20, 30))
    exif = image.getexif()
    exif[271] = "SecretCameraMake"  # EXIF tag 271 = Make
    image.save(buffer, format="JPEG", exif=exif)
    original = buffer.getvalue()

    assert b"SecretCameraMake" in original

    with app.test_request_context():
        attachments = store_evidence(
            [make_upload(original, "geo.jpg")], complaint, users["student1"].id
        )
        stored = resolve_stored_path(attachments[0].file_path).read_bytes()

    assert b"SecretCameraMake" not in stored
