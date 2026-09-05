"""Vercel serverless entry point.

Vercel looks for a module-level WSGI callable named ``app``. Everything else --
routing, sessions, the database -- is the same application that runs locally;
only the process model differs.

Two things behave differently under serverless, and both are handled by
configuration rather than by code here:

* **The filesystem is ephemeral.** ``/tmp`` is the only writable path and is
  wiped between invocations, so ``STORAGE_BACKEND=cloudinary`` is required or
  evidence photos would silently vanish. The check below fails loudly at boot
  rather than letting that happen quietly.
* **There is no local MySQL.** ``DATABASE_URL`` must point at a hosted
  database; ``config.py`` normalises the provider's URL for SQLAlchemy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The project root is one level up; Vercel does not add it to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app

app = create_app("production")

if os.getenv("VERCEL") and app.config["STORAGE_BACKEND"] != "cloudinary":
    # Better to refuse to start than to accept uploads that cannot survive the
    # next request.
    raise RuntimeError(
        "STORAGE_BACKEND must be 'cloudinary' on Vercel: the filesystem is "
        "ephemeral, so evidence photos written to disk would be lost."
    )
