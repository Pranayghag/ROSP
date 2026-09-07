<p align="center">
  <img src="app/static/img/campuscare-logo.png" alt="CampusCare" width="420">
</p>
just checking something on github
# CampusCare — Smart College Complaint & Maintenance Management System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000.svg)](https://flask.palletsprojects.com/)
[![Tests](https://img.shields.io/badge/tests-144%20passing-brightgreen.svg)](tests/)

Students report campus problems — leaking ceilings, dead projectors, broken
fans, patchy Wi‑Fi — verbally or in scattered WhatsApp messages. Nothing has a
reference number, nobody owns it, and there is no way to tell whether it was
ever fixed.

**CampusCare replaces that with a tracked workflow.** A student files a complaint with
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
- [Accounts and access](#accounts-and-access)
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
- Ask the built-in **assistant** anything about their complaints — see
  [docs/ASSISTANT.md](docs/ASSISTANT.md) for the full list of questions

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

**Security & accounts**
- **Two-step verification** available for every role: password, then a
  6-digit emailed code (opt-in via `TWO_FACTOR_ENABLED`)
- **Staff authorization** — a staff account is unusable until an administrator
  approves it, and that approval is permanent
- Admins can create staff accounts directly; the person sets their own password
  through a signed, expiring link (no password ever travels by email)
- Only *authorized* staff can be assigned complaints

**Throughout**
- Role-based access control (student / staff / admin), re-checked server-side
- In-app notifications **and email** on every handover
- Per-category SLA targets, scaled by priority, with escalation of breaches
- Advisory category and priority suggestions — never applied automatically
- An email log recording every send attempt, so "was it delivered?" always has
  an honest answer
- A **built-in assistant** on every page, answering from the asker's own rows.
  Not a language model: it is keyword matching over the database, so it needs
  no API key, runs offline, cannot invent a status, and every reply traces to a
  line of code

---

## Screens

| Page | What it shows |
| --- | --- |
| `/complaints/new` | Submission form with photo upload, previews and removal |
| `/complaints/<id>` | Details, evidence gallery, timeline, available actions |
| `/dashboard` | Role-aware summary for students and staff |
| `/admin/` | Unassigned queue, SLA breaches, headline counts |
| `/admin/staff` | Staff Management: filter, search, authorize, reject, suspend |
| `/admin/staff/new` | Create a staff account and email a setup link |
| `/admin/analytics` | Reports and graphs |
| `/admin/email-log` | Every delivery attempt and its outcome |
| `/verify` | Two-step verification |

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
| Tests | pytest | 144 tests, no database server required |

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
[`app/static/css/style.css`](app/static/css/style.css) — re-skinning CampusCare for
another institution means editing that one block.

---

## Quick start

**Prerequisites:** Python 3.11 or newer, and a MySQL/MariaDB server.
On Windows, [XAMPP](https://www.apachefriends.org/) provides MySQL — start it
from the XAMPP Control Panel before continuing.

```bash
git clone https://github.com/<your-username>/CampusCare.git
cd CampusCare
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

Create the database and tables, then load the reference data (categories and
locations):

```bash
python scripts/init_db.py
```

```bash
python scripts/seed.py
```

Create your administrator account. No password is typed here — the script
prints a one-time link for setting one:

```bash
python scripts/create_admin.py --email you@yourcollege.edu --name "Your Name"
```

Run it:

```bash
python run.py
```

Open <http://127.0.0.1:5000>.

### Optional demo data

`scripts/seed.py` creates **no user accounts** by default — a real deployment
should not contain invented people. For a local walkthrough you can add
placeholder ones:

```bash
python scripts/seed.py --demo
```

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@campuscare.invalid` | `Admin@123` |
| Staff | `ramesh@campuscare.invalid` | `Staff@123` |
| Student | `aman@campuscare.invalid` | `Student@123` |

These use the reserved `.invalid` domain, so they can never receive real mail.
Remove them before the system holds real users:

```bash
python scripts/purge_demo_data.py --apply
```

> With email configured, signing in needs the 6-digit code sent to the address
> on the account. Placeholder addresses cannot receive one — use them only
> while `SMTP_HOST` is unset, when codes are written to the server log instead.

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
| `APP_NAME` | `CampusCare` | Shown in the UI and in email subjects |
| `BASE_URL` | `http://127.0.0.1:5000` | Used to build links inside emails |
| `ADMIN_EMAIL` | *(unset)* | Where administrator notifications go |
| `SMTP_HOST` / `SMTP_PORT` | *(unset)* / `587` | Mail server |
| `SMTP_USER` / `SMTP_PASSWORD` | *(unset)* | **Never commit these** |
| `EMAIL_FROM` | `SMTP_USER` | The `From:` header |
| `OTP_TTL_SECONDS` | `300` | How long a verification code lasts |
| `OTP_MAX_ATTEMPTS` | `5` | Wrong guesses before a code is burned |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` | Wait between code requests |
| `ACTION_TOKEN_TTL_SECONDS` | `172800` | Lifetime of an account-setup link |

### Email

Leave `SMTP_HOST` blank and the application still runs end to end: every
message is recorded in the email log marked `NOT_CONFIGURED`, and its body goes
to the server log. Nothing ever claims to have sent mail it did not send.

For Gmail you must use an **App Password** (Google Account → Security → 2-Step
Verification → App passwords), not your account password.

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

## Accounts and access

### Two-step verification

Controlled by `TWO_FACTOR_ENABLED`, which is **off by default** — signing in is
email and password only. Set it to `true` (and configure SMTP, so codes can be
delivered) to add the second step:

```
email + password  →  credentials verified  →  6-digit code emailed
                  →  code verified         →  dashboard
```

With it on, the password alone grants nothing: until the code is accepted the
session holds only a user id and a timestamp, which is worthless on its own and
expires with the code.

Turning it off removes the second factor and **nothing else** — the password
check, the staff authorization gate and the password-set check all still run.
`tests/test_single_step_login.py` exists to keep that true.

The code is generated with `secrets`, **hashed before storage**, and exists in
plaintext only inside the email. It is never logged, never put in a template
variable, and never kept in the session. It expires (default 5 minutes), dies
after `OTP_MAX_ATTEMPTS` wrong guesses, works once, and is superseded whenever a
new one is issued. Requests for a fresh code are rate-limited.

### Staff authorization

A staff account is not usable the moment it is created:

```
staff registers  →  PENDING  →  admin notified by email
                 →  admin reviews in Staff Management
                 →  AUTHORIZED  →  staff emailed  →  can now sign in
```

Until approved, signing in is refused at the password step — no code is even
sent. The four statuses are `PENDING`, `AUTHORIZED`, `REJECTED` and `SUSPENDED`.

**Authorization is granted once.** Signing in never re-opens the question; only
another explicit admin decision changes a status. A test
(`test_signing_in_never_changes_authorization`) exists to keep that true.

Only `AUTHORIZED` staff appear in the assignment dropdown, and an attempt to
assign to anyone else is rejected server-side.

### Registration and roles

The registration form offers **Student** or **Staff** only. The submitted role
is re-checked against that allow-list in the view, because a `<select>` in the
browser is only a suggestion — an administrator account can never be created by
self-registration.

Admins can create staff directly from Staff Management. No password is chosen
there: the account is created with an unusable random one and the person
receives a signed, expiring link to set their own. **No password is ever sent
by email**, and the link stops working the moment it is used.

---

## Project structure

```
CampusCare/
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
| `test_two_factor.py` | Codes expire, are single-use, rate-limited, never stored in plaintext |
| `test_single_step_login.py` | With 2FA off, every other guard still holds |
| `test_workflows.py` | The eight end-to-end journeys from the specification |
| `test_staff_authorization.py` | Pending staff blocked, approval is permanent, only authorized staff assignable |

> The "malicious" test payloads are assembled from hex at runtime. A source
> file containing a literal shell one-liner gets quarantined by antivirus
> software, which would delete the test file for anyone who clones the repo.

---

## Development notes

Regenerate the SQL schema after changing a model:

```bash
python scripts/dump_schema.py
```

Apply schema changes to an existing database **without losing data**:

```bash
python scripts/migrate.py
```

```bash
python scripts/migrate.py --apply
```

Reset the database completely (destructive — it asks for confirmation):

```bash
python scripts/init_db.py --reset
```

Create a staff account: sign in as an administrator and use
**Staff Management → Add New Staff**. The person receives a link to set their
own password.

Create another administrator from the command line:

```bash
python scripts/create_admin.py --email them@yourcollege.edu --name "Their Name"
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
| [docs/ASSISTANT.md](docs/ASSISTANT.md) | Every question the in-app assistant answers |
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
