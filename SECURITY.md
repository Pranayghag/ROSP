# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub's
[Report a vulnerability](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
button on the Security tab, or email the maintainer directly.

Please include:

- What the issue is and why it matters
- Steps to reproduce, ideally with a minimal example
- The version or commit you tested against

You can expect an acknowledgement within a few days and an update as the fix
progresses. Credit is given in the release notes unless you prefer otherwise.

Please give a reasonable amount of time for a fix before disclosing publicly.

---

## Scope

This is a college project, not audited software. It is intended to be run on a
campus network by a small team. Do not deploy it as-is on the public internet
without reviewing the deployment notes in the README.

Reports about the following are in scope:

- Bypassing the evidence upload validation
- Reading a complaint or photo you are not entitled to see
- Escalating from student to staff or admin
- Session, CSRF or authentication weaknesses
- SQL injection or path traversal

Out of scope:

- The seeded demo accounts and their well-known passwords — they exist for
  local demonstration and the README says to remove them
- The default `SECRET_KEY` in `.env.example`, which the application warns about
  at startup when `FLASK_ENV=production`
- Denial of service through sheer request volume

---

## Security measures already in place

Understanding these may save you time when investigating.

### Evidence uploads

Every uploaded file must pass four independent checks before anything is
written to disk (`app/services/uploads.py`):

| Check | What it stops |
| --- | --- |
| Size limit, plus `MAX_CONTENT_LENGTH` on the request | Resource exhaustion |
| Extension allow-list | `.php`, `.exe`, `.svg` and friends |
| Declared MIME type allow-list | Obvious mismatches |
| Magic bytes + full Pillow decode | Renamed scripts, forged headers, polyglots |

The detected format must agree with both the extension and the declared type.
A batch is all-or-nothing: one bad file rejects the whole submission and leaves
no partial files behind.

Decompression bombs are limited via `Image.MAX_IMAGE_PIXELS` (50 MP).

### Storage

- Stored filenames are 32 hex characters from `secrets.token_hex`, plus a
  server-chosen extension. No user-supplied string reaches a path.
- `resolve_stored_path()` refuses any path that resolves outside `UPLOAD_DIR`,
  even if the database value were tampered with.
- Images are re-encoded through Pillow, stripping EXIF (including GPS) and any
  payload hidden in a metadata segment.

### Access control

- Uploads are never served statically. `GET /evidence/<id>` re-checks on every
  request that the caller is the complaint's student, its assigned staff
  member, or an admin.
- Responses carry `X-Content-Type-Options: nosniff`, a restrictive
  `Content-Security-Policy`, and `Cache-Control: private`.
- Filesystem paths never appear in URLs, pages or error messages.

### Application

- Passwords hashed with Werkzeug's PBKDF2 (`generate_password_hash`)
- CSRF protection on every POST via Flask-WTF
- `HttpOnly`, `SameSite=Lax` session cookies; `Secure` in production
- All database access through SQLAlchemy's parameterised queries
- Login errors are deliberately generic, so registered addresses cannot be
  enumerated
- Redirect targets after login are validated as same-site and relative
- Self-registration can only ever create a student account

### Tested

`tests/test_upload_validation.py` and `tests/test_access_control.py` assert
these properties directly. If you find a bypass, a failing test is the most
useful possible bug report.
