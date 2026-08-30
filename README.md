# ROSP — Campus Complaint Management System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000.svg)](https://flask.palletsprojects.com/)
[![Tests](https://img.shields.io/badge/tests-71%20passing-brightgreen.svg)](tests/)

Students report campus problems — leaking ceilings, dead projectors, broken
fans, patchy Wi‑Fi — verbally or in scattered WhatsApp messages. Nothing has a
reference number, nobody owns it, and there is no way to tell whether it was
ever fixed.

**ROSP replaces that with a tracked workflow.** A student files a complaint with
photographic evidence, an administrator assigns it to the right staff member,
the staff member resolves it and uploads proof of the repair, and the student
confirms the fix before it closes. Every step is recorded with a name and a
timestamp.

```
Student → submits complaint (+ photos) → Admin → assigns → Staff → resolves
        → Student verifies → Closed
```

---

## Contents

- [Features](#features)
- [Screens](#screens)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [How photo evidence is handled](#how-photo-evidence-is-handled)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Development notes](#development-notes)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Features

**For students**
- Register, sign in, and file complaints with title, description, category,
  location and priority
- Attach up to 5 photos as evidence, with live previews and one-click removal
  before submitting
- Follow a complaint through a seven-step timeline
- Confirm a fix, or reopen the complaint if the problem is still there

**For administrators**
- Dashboard of everything open, unassigned, or past its deadline
- Filterable queue across status, priority, category and assignment
- Assign complaints to staff, with notifications to both sides
- Reports: volume over time, breakdown by status/priority/category, average
  resolution time

**For staff**
- A queue of only the complaints assigned to them
- Move work through *In Progress* → *Resolved*
- Upload "after" photos as proof of the completed repair

**Throughout**
- Role-based access control (student / staff / admin)
- In-app notifications on every handover
- Per-category SLA targets, scaled by priority, with escalation of breaches
- Advisory category and priority suggestions — never applied automatically

---

## Screens

| Page | What it shows |
| --- | --- |
| `/complaints/new` | Submission form with photo upload, previews and removal |
| `/complaints/<id>` | Details, evidence gallery, timeline, available actions |
| `/dashboard` | Role-aware summary for students and staff |
| `/admin/` | Unassigned queue, SLA breaches, headline counts |
| `/admin/analytics` | Reports and graphs |

> Add screenshots to `docs/screenshots/` and link them here — they make the
> repository far more approachable to newcomers.

---

## Tech stack

Everything is open source and free to use.

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11+ | Widely taught, huge ecosystem |
| Web framework | Flask 3 | Small enough to read end to end |
| Templates | Jinja2 | Ships with Flask |
| Styling | Bootstrap 5 + custom design system | Vendored — no CDN, works offline |
| Type | Inter (vendored, OFL) | Matches the design system; no Google Fonts call |
| Icons | Inline SVG | No icon font, no extra request, no licence to track |
| ORM | SQLAlchemy 2 + Flask-SQLAlchemy | Models are the single source of truth |
| Database | MySQL / MariaDB | Available in XAMPP; PyMySQL needs no compiler |
| Auth | Flask-Login + Werkzeug PBKDF2 | Standard, no rolled-my-own crypto |
| Forms & CSRF | Flask-WTF | CSRF protection on every POST |
| Images | Pillow | Verifies uploads really decode as images |
| Tests | pytest | 71 tests, no database server required |

Bootstrap, Inter and the icon set are all committed under
[`app/static/vendor/`](app/static/vendor/) and
[`_icons.html`](app/templates/partials/_icons.html), so the application renders
identically with **no internet connection** — which matters in a lab or during
a demo.

### Design

The interface follows an "Academic Integrity" design system: **Academic Blue**
(`#002045`) on a near-white ground, Inter throughout, tonal layering with 1px
outlines rather than heavy shadows, pill-shaped status chips, and soft 4–12px
corners.

Every value lives as a CSS custom property at the top of
[`app/static/css/style.css`](app/static/css/style.css) — re-skinning ROSP for
another institution means editing that one block.

---

## Quick start

**Prerequisites:** Python 3.11 or newer, and a MySQL/MariaDB server.
On Windows, [XAMPP](https://www.apachefriends.org/) provides MySQL — start it
from the XAMPP Control Panel before continuing.

```bash
git clone https://github.com/<your-username>/ROSP.git
cd ROSP
```

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
```

```bash
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

Copy the example configuration (the defaults match a stock XAMPP install —
user `root` with an empty password):

```bash
cp .env.example .env
```

Create the database and tables, then load demo data:

```bash
python scripts/init_db.py
```

```bash
python scripts/seed.py
```

Run it:

```bash
python run.py
```

Open <http://127.0.0.1:5000>.

### Demo accounts

The seed script creates these. **Change or remove them before deploying
anywhere real.**

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@rosp.edu` | `Admin@123` |
| Staff | `ramesh@rosp.edu` | `Staff@123` |
| Staff | `sunita@rosp.edu` | `Staff@123` |
| Student | `aman@rosp.edu` | `Student@123` |
| Student | `priya@rosp.edu` | `Student@123` |

Sign in as the student to see complaints with evidence, then as the admin to
assign one, then as the staff member to resolve it.

---

## Configuration

All settings come from the environment, read from `.env` if present. See
[`.env.example`](.env.example) for the annotated list.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | dev placeholder | Signs sessions and CSRF tokens — **set a real one** |
| `FLASK_ENV` | `development` | `development` \| `production` \| `testing` |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | Database server |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / *(empty)* | Credentials |
| `MYSQL_DB` | `rosp` | Database name |
| `DATABASE_URL` | *(unset)* | Full SQLAlchemy URL; overrides the above |
| `UPLOAD_DIR` | `./uploads` | Where evidence is written |
| `MAX_FILE_SIZE_MB` | `5` | Per-photo size cap |
| `MAX_FILES_PER_COMPLAINT` | `5` | Photos per complaint |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` when serving over HTTPS |

Generate a real secret key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## How photo evidence is handled

This is the part worth reading before you change anything, and the reason the
project exists in this shape.

### Validation — four independent checks

A browser controls the filename, the extension and the `Content-Type` header,
so none of them is trusted alone. Every upload must pass all four checks in
[`app/services/uploads.py`](app/services/uploads.py):

1. **Size** — rejected above `MAX_FILE_SIZE_MB`. Werkzeug also caps the whole
   request body via `MAX_CONTENT_LENGTH`, so an enormous POST is refused before
   it is buffered.
2. **Extension** — must be `.jpg`, `.jpeg`, `.png` or `.webp`.
3. **Declared MIME type** — must be `image/jpeg`, `image/png` or `image/webp`.
4. **Actual bytes** — magic bytes are sniffed and Pillow fully decodes the
   image. The detected format must agree with both the extension and the
   declared type.

Nothing is written to disk until all four pass, so a rejected file never exists
as a file. A batch is all-or-nothing: one bad photo rejects the whole
submission and leaves nothing behind.

This is what stops a script renamed to `photo.jpg`, a forged `Content-Type`,
`shell.php.jpg`, and executables sent as images.

### Storage — the original filename is discarded

Each accepted image is stored under 32 hex characters from a CSPRNG plus the
canonical extension for its *detected* type, sharded by date:

```
uploads/2026/08/30/9f2c1ab4e7d05c318be6a2d7f4c19e0b.jpg
```

The original name is kept in `attachments.file_name` for display only. Because
no user-supplied string ever reaches a path, path traversal, extension
smuggling and collisions are all designed out rather than filtered.

Images are re-encoded through Pillow before being written, which strips EXIF —
protecting students from publishing GPS coordinates in a photo of a leaking
ceiling, and discarding anything hidden in a metadata segment.

### Serving — permissions re-checked on every request

Uploads are **not** served as static files. There is no public URL that maps
onto the upload directory. Every image is fetched by attachment id:

```
GET /evidence/42
```

and [`app/blueprints/attachments.py`](app/blueprints/attachments.py) re-checks
on each request that the caller is the student who filed the complaint, the
staff member it is assigned to, or an administrator. Everyone else gets `403`.
The stored path never appears in a URL, a page, or an error message.

### Photos are evidence, not a classifier

An uploaded photo **never** determines a complaint's category or priority.
The values the student chose are the values that get stored.

[`app/services/suggestions.py`](app/services/suggestions.py) offers advisory
hints from the complaint's wording, shown as *"Suggestion: Plumbing & Water
(matched: leaking, water) — Use this"*. Nothing is applied unless the student
clicks it. If image analysis is added later it must plug in the same way: a
proposal for a person to accept or reject. Two tests in
[`tests/test_suggestions.py`](tests/test_suggestions.py) exist purely to make
sure that property cannot be quietly lost.

---

## Project structure

```
ROSP/
├── app/
│   ├── __init__.py            application factory
│   ├── config-driven modules  constants.py, decorators.py, extensions.py
│   ├── models.py              users, complaints, attachments, events
│   ├── forms.py               WTForms definitions + CSRF
│   ├── blueprints/
│   │   ├── auth.py            register / login / logout
│   │   ├── main.py            dashboards, notifications
│   │   ├── complaints.py      submit, list, details, status changes
│   │   ├── attachments.py     authenticated evidence serving
│   │   └── admin.py           queue, assignment, analytics
│   ├── services/
│   │   ├── uploads.py         validation + safe storage  ← start here
│   │   ├── workflow.py        status transitions, timeline
│   │   ├── notifications.py   in-app notifications
│   │   ├── sla.py             deadlines and escalation
│   │   └── suggestions.py     advisory hints only
│   ├── templates/             Jinja2 + Bootstrap
│   └── static/                css, js, vendored Bootstrap
├── scripts/
│   ├── init_db.py             create database + tables
│   ├── seed.py                demo data (with generated photos)
│   └── dump_schema.py         regenerate sql/schema.sql
├── sql/schema.sql             generated MySQL DDL
├── tests/                     pytest suite
├── uploads/                   evidence (gitignored)
├── config.py                  environment-driven settings
└── run.py                     development entry point
```

---

## Testing

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

The suite runs against a temporary SQLite file and a temporary upload
directory, so **no MySQL server is needed** — which also lets it run in CI.

```bash
pytest --cov=app --cov-report=term-missing
```

What is covered:

| File | Focus |
| --- | --- |
| `test_upload_validation.py` | Format acceptance, hostile files, filename safety, EXIF stripping |
| `test_access_control.py` | Who can read evidence; path never leaked |
| `test_complaint_flow.py` | Submission through to closure |
| `test_suggestions.py` | Suggestions stay advisory |

> The "malicious" test payloads are assembled from hex at runtime. A source
> file containing a literal shell one-liner gets quarantined by antivirus
> software, which would delete the test file for anyone who clones the repo.

---

## Development notes

Regenerate the SQL schema after changing a model:

```bash
python scripts/dump_schema.py
```

Reset the database completely (destructive — it asks for confirmation):

```bash
python scripts/init_db.py --reset
```

Create a staff or admin account (self-registration always creates a student):

```bash
flask shell
```

```python
from app.extensions import db
from app.models import User

user = User(name="Name", email="staff@rosp.edu", role="staff", department="Electrical")
user.set_password("ChangeMe@123")
db.session.add(user)
db.session.commit()
```

### Before deploying anywhere real

- Set a strong `SECRET_KEY` and `FLASK_ENV=production`
- Serve over HTTPS and set `SESSION_COOKIE_SECURE=true`
- Use a dedicated MySQL user, not `root`
- Delete the seeded demo accounts
- Run behind a WSGI server: `gunicorn "app:create_app()"`
- Back up `uploads/` — evidence is not stored in the database

---

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the code fits together, and why |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What could come next, including image analysis |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, conventions, pull request process |
| [SECURITY.md](SECURITY.md) | Reporting process and the measures already in place |

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
setup, coding conventions and pull request process, and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

Good first issues: adding screenshots, extending the keyword lists in
`suggestions.py`, adding a language translation, or improving mobile layout.

Found a security problem? Please follow [SECURITY.md](SECURITY.md) rather than
opening a public issue.

---

## License

[MIT](LICENSE) — free to use, modify and distribute, including for coursework.

Bootstrap, bundled in `app/static/vendor/`, is also MIT licensed and remains
copyright of the Bootstrap Authors.
