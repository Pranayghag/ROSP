"""Evidence upload: validation, safe storage and retrieval.

Security model
--------------
Everything a browser sends about a file is attacker-controlled: the filename,
its extension, and the ``Content-Type`` header. This module therefore trusts
none of them individually. An upload is accepted only when *four* independent
checks agree:

1. **Size** -- rejected above ``MAX_FILE_SIZE_MB`` (and Werkzeug already caps
   the whole request via ``MAX_CONTENT_LENGTH``).
2. **Extension** -- the original extension must be one of ``.jpg/.jpeg/.png/.webp``.
3. **Declared MIME type** -- the browser-supplied type must be an allowed image type.
4. **Actual bytes** -- the file's magic bytes are sniffed and the image is fully
   decoded by Pillow. The detected type must match the extension and the
   declared type.

Nothing is written to disk until all four pass, so a rejected upload never
exists as a file. This is what stops ``shell.php`` renamed to ``photo.jpg``,
a forged ``Content-Type``, and polyglot files.

Storage
-------
The original filename is never used to build a path. Each accepted image gets
a fresh name of 32 hex characters from a CSPRNG plus the canonical extension
for its *detected* type, stored under a ``YYYY/MM/DD`` shard. That removes path
traversal, extension smuggling (``a.php.jpg``), unicode tricks and collisions
in one step.

Images are additionally re-encoded through Pillow before being written, which
strips EXIF metadata. That protects student privacy (EXIF can carry GPS
coordinates and device identifiers) and discards any payload smuggled into a
metadata segment.

NOTE ON IMAGE ANALYSIS
----------------------
Uploaded photos are treated purely as *evidence*. Nothing in this module
inspects image content to derive a complaint's category or priority. If smart
image analysis is added later it must only ever produce a **suggestion** shown
to the admin or student for confirmation -- never an automatic classification.
"""

from __future__ import annotations

import contextlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath

from flask import current_app
from PIL import Image, UnidentifiedImageError

from ..models import Attachment
from .storage import LocalStorage, StorageError, get_storage

# Guard against decompression bombs: a small file that expands to gigabytes of
# pixels. 50 megapixels is far beyond any phone photo of a leaking ceiling.
MAX_IMAGE_PIXELS = 50_000_000

#: Magic-byte signatures for the formats we accept, mapped to a canonical
#: extension and the set of extensions a user may legitimately have used.
_SIGNATURES = (
    {
        "mime": "image/jpeg",
        "canonical_ext": ".jpg",
        "extensions": {".jpg", ".jpeg"},
        # FF D8 FF -- JPEG Start of Image marker
        "test": lambda b: b[:3] == b"\xff\xd8\xff",
    },
    {
        "mime": "image/png",
        "canonical_ext": ".png",
        "extensions": {".png"},
        "test": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    },
    {
        "mime": "image/webp",
        "canonical_ext": ".webp",
        "extensions": {".webp"},
        "test": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    },
)

#: Pillow format name -> the MIME type we expect the magic bytes to have said.
_PIL_FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class UploadError(ValueError):
    """Raised when an upload fails validation.

    The message is safe to show to the user: it never contains a filesystem
    path or other server-side detail.
    """


@dataclass(frozen=True)
class ValidatedImage:
    """An upload that passed every check and is ready to be written."""

    data: bytes
    mime: str
    canonical_ext: str
    display_name: str
    pil_format: str


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------


def sanitize_display_name(original: str | None) -> str:
    """Reduce a user-supplied filename to something safe to *display*.

    The result is never used to build a path -- see :func:`generate_stored_name`
    -- but it is echoed back into HTML, so strip directory components, control
    characters and quoting metacharacters.
    """
    raw = str(original or "")
    # Handle both POSIX and Windows separators regardless of the server OS.
    base = raw.replace("\\", "/").split("/")[-1]
    base = "".join(ch for ch in base if ch.isprintable())
    for bad in '\\/:*?"<>|':
        base = base.replace(bad, "_")
    base = " ".join(base.split()).lstrip(".").strip()

    if not base:
        return "evidence"
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = f"{stem[:100]}.{ext}" if dot else base[:120]
    return base


def generate_stored_name(canonical_ext: str) -> str:
    """Return a random, collision-free filename with a server-chosen extension."""
    return f"{secrets.token_hex(16)}{canonical_ext}"


def build_relative_path(stored_name: str, when: datetime | None = None) -> str:
    """Shard uploads by date so one directory never grows without bound.

    Returns a POSIX-style path *relative* to ``UPLOAD_DIR``.
    """
    when = when or datetime.now(UTC)
    return f"{when.year:04d}/{when.month:02d}/{when.day:02d}/{stored_name}"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def detect_image_type(data: bytes) -> dict | None:
    """Identify an image purely from its leading bytes.

    Returns the matching signature dict, or ``None`` when the bytes are not one
    of the accepted image formats.
    """
    if not data:
        return None
    for signature in _SIGNATURES:
        if signature["test"](data):
            return signature
    return None


def validate_image(storage) -> ValidatedImage:
    """Validate one uploaded file, returning its bytes ready for storage.

    :param storage: a Werkzeug ``FileStorage`` from ``request.files``
    :raises UploadError: with a user-safe message when any check fails
    """
    config = current_app.config
    max_bytes = config["MAX_FILE_SIZE_MB"] * 1024 * 1024

    display_name = sanitize_display_name(getattr(storage, "filename", ""))

    # --- 1. Size ---------------------------------------------------------
    data = storage.read()
    if not data:
        raise UploadError(f"{display_name} is empty.")
    if len(data) > max_bytes:
        raise UploadError(
            f"{display_name} is larger than the "
            f"{config['MAX_FILE_SIZE_MB']} MB limit."
        )

    # --- 2. Extension ----------------------------------------------------
    suffix = PurePosixPath(display_name).suffix.lower()
    if suffix not in config["ALLOWED_EXTENSIONS"]:
        raise UploadError(
            f"{display_name} is not an accepted image type. "
            "Allowed formats: JPG, JPEG, PNG, WEBP."
        )

    # --- 3. Declared MIME type -------------------------------------------
    declared = (getattr(storage, "mimetype", "") or "").lower()
    if declared not in config["ALLOWED_MIME_TYPES"]:
        raise UploadError(f"{display_name} was not sent as a supported image type.")

    # --- 4. Actual content ------------------------------------------------
    signature = detect_image_type(data)
    if signature is None:
        raise UploadError(
            f"{display_name} is not a real image file. "
            "Renaming a document or program to .jpg does not make it a photo."
        )
    if suffix not in signature["extensions"]:
        raise UploadError(
            f"{display_name} has a .{suffix.lstrip('.')} extension but its "
            "contents are a different format."
        )
    if declared != signature["mime"]:
        raise UploadError(f"{display_name} does not match its declared file type.")

    # Fully decode the image. A file can carry a valid header and still be
    # malformed or hostile; verify() walks the whole structure.
    pil_format = _verify_decodable(data, display_name)
    if _PIL_FORMAT_TO_MIME.get(pil_format) != signature["mime"]:
        raise UploadError(f"{display_name} could not be verified as a valid image.")

    return ValidatedImage(
        data=data,
        mime=signature["mime"],
        canonical_ext=signature["canonical_ext"],
        display_name=display_name,
        pil_format=pil_format,
    )


def _verify_decodable(data: bytes, display_name: str) -> str:
    """Confirm Pillow can decode the bytes; return the detected format name."""
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            # verify() consumes the file object, so it must be the last call on
            # this handle; the image is reopened later for re-encoding.
            image.verify()
        return image_format or ""
    except Image.DecompressionBombError as exc:
        raise UploadError(f"{display_name} has too many pixels to process.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadError(f"{display_name} is damaged or not a readable image.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def upload_root() -> Path:
    """Absolute path of the local upload directory.

    Only meaningful for the local backend; kept because scripts and tests
    reason about the directory directly.
    """
    return Path(current_app.config["UPLOAD_DIR"]).resolve()


def resolve_stored_path(key: str) -> Path:
    """Absolute local path for a storage key.

    Local backend only. Raises :class:`UploadError` for a key this application
    did not generate, or one that escapes the upload directory -- defence in
    depth against a poisoned database value.
    """
    backend = get_storage()
    if not isinstance(backend, LocalStorage):
        raise UploadError("Attachments are not stored on the local filesystem.")
    try:
        return backend.path_for(key)
    except StorageError as exc:
        raise UploadError(str(exc)) from exc


def read_attachment(attachment) -> bytes:
    """The stored bytes for an attachment, whichever backend holds them."""
    try:
        return get_storage().load(attachment.file_path)
    except StorageError as exc:
        raise UploadError(str(exc)) from exc


def _sanitised_bytes(validated: ValidatedImage) -> bytes:
    """Re-encode the image in memory, stripping metadata.

    Re-encoding rather than passing the original bytes through is deliberate:
    it removes EXIF (which can carry GPS coordinates) and discards anything
    hidden in a metadata segment. Done in memory so the result can go to any
    backend, not only a filesystem.
    """
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    buffer = BytesIO()
    try:
        with Image.open(BytesIO(validated.data)) as image:
            save_kwargs: dict = {}

            if validated.pil_format == "JPEG":
                # JPEG cannot store alpha; flatten anything with transparency.
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                save_kwargs = {"quality": 90, "optimize": True, "progressive": True}
            elif validated.pil_format == "PNG":
                save_kwargs = {"optimize": True}
            elif validated.pil_format == "WEBP":
                save_kwargs = {"quality": 90, "method": 4}

            # Copying the pixels into a fresh image guarantees that no EXIF
            # block, PNG text chunk or other metadata from the original
            # survives into the stored file. Pillow only writes metadata that
            # is passed explicitly to save(), and nothing is passed here.
            clean = Image.new(image.mode, image.size)
            clean.paste(image)
            clean.save(buffer, format=validated.pil_format, **save_kwargs)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    return buffer.getvalue()


def store_evidence(files, complaint, uploader_id: int) -> list[Attachment]:
    """Validate and persist every uploaded photo for a complaint.

    All files are validated *before* any are written, so a batch containing one
    bad file is rejected as a whole and leaves nothing behind on disk.

    :param files: list of Werkzeug ``FileStorage`` objects
    :param complaint: the :class:`~app.models.Complaint` being attached to
    :param uploader_id: id of the user performing the upload
    :returns: the created :class:`~app.models.Attachment` rows (not committed)
    :raises UploadError: if any file fails validation or the limit is exceeded
    """
    candidates = [f for f in (files or []) if f and getattr(f, "filename", "")]
    if not candidates:
        return []

    max_files = current_app.config["MAX_FILES_PER_COMPLAINT"]
    already = len(complaint.attachments or [])
    if already + len(candidates) > max_files:
        raise UploadError(
            f"A complaint can have at most {max_files} photos "
            f"(you tried to add {len(candidates)} to {already} existing)."
        )

    # Phase 1 -- validate everything, touching no files.
    validated = [validate_image(storage) for storage in candidates]

    # Phase 2 -- store. Any failure removes whatever was already stored, so a
    # rejected batch leaves nothing behind in either backend.
    backend = get_storage()
    attachments: list[Attachment] = []
    stored_keys: list[str] = []
    try:
        for item in validated:
            key = build_relative_path(generate_stored_name(item.canonical_ext))
            size = backend.save(key, _sanitised_bytes(item), item.mime)
            stored_keys.append(key)

            attachments.append(
                Attachment(
                    complaint_id=complaint.id,
                    file_name=item.display_name,
                    file_path=key,
                    file_type=item.mime,
                    file_size=size,
                    uploaded_by=uploader_id,
                )
            )
    except StorageError as exc:
        for key in stored_keys:
            backend.delete(key)
        raise UploadError(str(exc)) from exc
    except Exception:
        for key in stored_keys:
            backend.delete(key)
        raise

    return attachments


def delete_stored_file(attachment: Attachment) -> None:
    """Remove an attachment's stored object, ignoring one already gone."""
    # A key that no longer validates is not ours to delete.
    with contextlib.suppress(StorageError):
        get_storage().delete(attachment.file_path)
