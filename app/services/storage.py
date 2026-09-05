"""Where evidence photos actually live.

Two backends behind one interface:

* **LocalStorage** -- files under ``UPLOAD_DIR``. The default, and what runs in
  development and in the tests.
* **CloudinaryStorage** -- an object store, for hosts with an ephemeral
  filesystem. Every free serverless tier (Vercel, Render, Fly) wipes local
  files on redeploy, so a deployed instance must not keep evidence on disk.

Which one is used depends only on configuration; nothing above this module
knows the difference. ``Attachment.file_path`` holds an opaque *storage key*
either way -- a relative path for local, a public id for Cloudinary -- so
switching backends needs no schema change.

SECURITY -- read before adding a backend
----------------------------------------
Bytes are **never** served to a browser directly from storage. Cloudinary
uploads use the ``authenticated`` delivery type, so the object has no public
URL, and ``attachments.view`` fetches it server-side after checking
permissions. That keeps the rule the whole project is built on: a photo is
visible only to the student who filed the complaint, the assigned staff member,
and admins.

A backend that returned a public URL would quietly destroy that guarantee, no
matter how unguessable the URL looked.
"""

from __future__ import annotations

import contextlib
import re
from abc import ABC, abstractmethod
from pathlib import Path

from flask import current_app


class StorageError(RuntimeError):
    """Raised when a backend cannot store or retrieve an object."""


#: A storage key we generated: date-sharded, random stem, known extension.
#: Anything else is rejected before it reaches a filesystem or an API call.
_SAFE_KEY = re.compile(r"^\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")


def validate_key(key: str) -> str:
    """Reject any storage key this application did not generate.

    Defence in depth. Even if a malicious value reached the database, it cannot
    be turned into ``../../etc/passwd`` or an arbitrary remote fetch.
    """
    if not key or not _SAFE_KEY.match(key):
        raise StorageError("Invalid attachment location.")
    return key


class Storage(ABC):
    """The contract every backend implements."""

    @abstractmethod
    def save(self, key: str, data: bytes, mime: str) -> int:
        """Store ``data`` under ``key``; return the number of bytes stored."""

    @abstractmethod
    def load(self, key: str) -> bytes:
        """Return the stored bytes, or raise StorageError if unavailable."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object. Missing objects are not an error."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Whether the object is retrievable."""


class LocalStorage(Storage):
    """Files on the local filesystem, under ``UPLOAD_DIR``."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path_for(self, key: str) -> Path:
        """Absolute path for a key, refusing anything outside the root."""
        validate_key(key)
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError("Invalid attachment location.")
        return candidate

    def save(self, key: str, data: bytes, mime: str) -> int:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination.stat().st_size

    def load(self, key: str) -> bytes:
        path = self.path_for(key)
        if not path.is_file():
            raise StorageError("Attachment file is missing.")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        # A key that no longer validates is not ours to delete.
        with contextlib.suppress(StorageError):
            self.path_for(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except StorageError:
            return False


class CloudinaryStorage(Storage):
    """Objects in Cloudinary, uploaded as ``authenticated`` (no public URL).

    The storage key doubles as the Cloudinary ``public_id``, minus the
    extension, so the two systems never disagree about what a file is called.
    """

    def __init__(self, folder: str = "campuscare"):
        import cloudinary  # imported lazily: unused in local development

        self.cloudinary = cloudinary
        self.folder = folder.strip("/")

        config = current_app.config
        cloudinary.config(
            cloud_name=config["CLOUDINARY_CLOUD_NAME"],
            api_key=config["CLOUDINARY_API_KEY"],
            api_secret=config["CLOUDINARY_API_SECRET"],
            secure=True,
        )

    def _public_id(self, key: str) -> str:
        validate_key(key)
        # Cloudinary appends the format itself, so the id carries no extension.
        return f"{self.folder}/{key.rsplit('.', 1)[0]}"

    def save(self, key: str, data: bytes, mime: str) -> int:
        import cloudinary.uploader

        try:
            cloudinary.uploader.upload(
                data,
                public_id=self._public_id(key),
                resource_type="image",
                # No public URL exists for an authenticated asset; it can only
                # be fetched with a signature, which happens server-side.
                type="authenticated",
                overwrite=False,
                invalidate=True,
            )
        except Exception as exc:
            raise StorageError(f"Could not store the image: {exc}") from exc

        return len(data)

    def load(self, key: str) -> bytes:
        import urllib.request

        import cloudinary.utils

        url, _options = cloudinary.utils.cloudinary_url(
            self._public_id(key),
            resource_type="image",
            type="authenticated",
            sign_url=True,
            secure=True,
        )
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                return response.read()
        except Exception as exc:
            raise StorageError(f"Could not read the image: {exc}") from exc

    def delete(self, key: str) -> None:
        import cloudinary.uploader

        try:
            cloudinary.uploader.destroy(
                self._public_id(key), resource_type="image", type="authenticated"
            )
        except Exception:
            current_app.logger.warning("Could not delete %s from Cloudinary", key)

    def exists(self, key: str) -> bool:
        import cloudinary.api

        try:
            cloudinary.api.resource(
                self._public_id(key), resource_type="image", type="authenticated"
            )
            return True
        except Exception:
            return False


def get_storage() -> Storage:
    """The backend for this request, chosen by configuration.

    Built per call rather than cached on the app: Cloudinary's client config is
    process-global, and rebuilding keeps a test that swaps configuration mid-run
    honest.
    """
    if current_app.config.get("STORAGE_BACKEND", "local") == "cloudinary":
        return CloudinaryStorage(current_app.config["CLOUDINARY_FOLDER"])
    return LocalStorage(current_app.config["UPLOAD_DIR"])
