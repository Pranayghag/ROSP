# Architecture

A tour of how CampusCare is put together, for anyone picking the codebase up.

---

## The shape of a request

```
Browser
  │
  ├─ app/__init__.py          application factory: config, extensions, blueprints,
  │                           error handlers, template filters, security headers
  │
  ├─ app/blueprints/*.py      routing and HTTP concerns only — parse the request,
  │                           check permissions, call a service, render a template
  │
  ├─ app/services/*.py        the actual rules: uploads, workflow, SLA,
  │                           notifications, suggestions
  │
  ├─ app/models.py            SQLAlchemy models — the single source of truth
  │                           for the database schema
  │
  └─ MySQL / MariaDB
```

Views stay thin on purpose. If a route grows past roughly 40 lines, the logic
belongs in a service, where it can be tested without an HTTP request.

---

## Modules

### `config.py`

Everything configurable is an environment variable, read via `python-dotenv`.
Three config classes — `DevelopmentConfig`, `ProductionConfig`, `TestingConfig`
— differ only in debug flags, cookie security, and the database URI.

`TestingConfig` points at SQLite so the suite runs with no database server. The
application code is identical in both cases; only the URI differs.

### `app/models.py`

| Model | Purpose |
| --- | --- |
| `User` | Students, staff and admins. Role is a string column checked against `Role`. |
| `Category` | Complaint types, each with an `sla_hours` target. |
| `Location` | Rooms and buildings. |
| `Complaint` | The central record, plus SLA fields and the `is_visible_to()` rule. |
| `Attachment` | One photo. Matches the specified table, plus `attachment_type`. |
| `ComplaintEvent` | Append-only audit trail; the timeline is derived from it. |
| `Notification` | In-app messages for a single user. |
| `OtpCode` | A hashed one-time code, with its expiry and attempt count. |
| `EmailLog` | Every delivery attempt and its outcome. |

`Complaint.is_visible_to(user)` is the one place the read-permission rule lives.
Both the complaint page and the evidence route call it, so they cannot drift
apart.

`User.authorization_status` gates staff access. Students are `AUTHORIZED` on
creation; staff start `PENDING` and stay unusable until an administrator
approves them. **The value is written only by an explicit admin decision** —
nothing in the sign-in path touches it, so approval is granted once and never
re-litigated.

### `app/services/uploads.py`

The most security-sensitive file in the project. Four independent checks — size,
extension, declared MIME type, and the actual decoded bytes — must all agree
before anything is written. Storage uses a CSPRNG-generated name, and images are
re-encoded to strip EXIF.

Read the module docstring before changing anything here.

### `app/services/workflow.py`

Status transitions and the timeline. `available_actions(complaint, user)` returns
exactly the transitions a given user may perform, and the template renders one
button per entry — so the UI and the authorisation check can never disagree.

`build_timeline()` derives the seven display steps from `complaint_events`
rather than from the current status alone, so history survives a reopen.

### `app/services/sla.py`

Each category has an `sla_hours` target, scaled by priority
(`URGENT` = ¼ of the time, `LOW` = 1½×). `escalate_overdue()` is idempotent: it
only acts on complaints past their deadline that have not been escalated yet,
so it is safe to call from a cron job or a button.

### `app/services/email.py` and `mailers.py`

`email.py` is the transport: render a template, build a multipart message with
any inline images, send it, and record an `EmailLog` row. `mailers.py` sits on
top with one function per message the application sends, so subjects and
context live in one place.

**It never claims to have sent something it did not.** With no SMTP configured
the row is written as `NOT_CONFIGURED` and the body goes to the server log; the
caller inspects the returned row and tells the user the truth.

### `app/services/otp.py` and `tokens.py`

`otp.py` issues and verifies the 6-digit codes — hashed before storage, single
use, expiring, attempt-limited, superseded on reissue. Active only when
`TWO_FACTOR_ENABLED` is set.

`tokens.py` mints the signed, expiring links emailed to staff for setting a
password. The payload includes a digest of the current password hash, so the
link stops working the moment it is used.

### `app/services/suggestions.py`

Transparent keyword scoring, not a trained model — no dataset, runs offline, and
every suggestion can be explained ("matched: leaking, water").

**It only ever suggests.** See [the design rule](#the-rule-that-must-not-break).

### `app/services/chatbot.py`

The in-app assistant. A single ordered catalogue, `TOPICS`, does four jobs: it
matches questions, feeds the keyword fallback, renders the browsable menu in the
widget, and generates [ASSISTANT.md](ASSISTANT.md). Adding a capability is one
row, so the menu and the docs cannot drift from what the code does.

Deliberately **not** a language model. The questions students ask are about
their own rows ("where has CC102 got to?"), which a model cannot answer without
reading the database — and if it guesses, it invents a status the student then
acts on. So each reply is either fixed procedural text or a real row.

Every lookup is scoped in `_own_complaints`, and code lookups reuse
`Complaint.is_visible_to` — the same rule the complaint page enforces, so the
assistant can never reveal what the UI would refuse to show.

---

## Data flow: filing a complaint with evidence

1. `GET /complaints/new` renders the form. Categories and locations come from
   the database.
2. As the student types, the page POSTs to `/complaints/suggest` and shows an
   advisory hint. Nothing is applied unless they click "Use this".
3. On submit, `complaints.new`:
   - builds the `Complaint` and `db.session.flush()`es it to get an id
   - assigns the public code (`CC` + `100 + id`) and the SLA deadline
   - calls `store_evidence()`, which validates **every** file before writing
     **any** of them
   - records the opening timeline events and notifies admins
   - commits — one transaction for the whole thing
4. If any photo fails validation, the rollback discards the complaint too, and
   `store_evidence()` removes any files it had already written. A rejected
   submission leaves nothing behind.

---

## Serving evidence

Uploads are never static files. There is no URL that maps onto `UPLOAD_DIR`.

```
GET /evidence/<attachment_id>
  → load the Attachment
  → complaint.is_visible_to(current_user)?   no → 403
  → resolve_stored_path()                    escapes UPLOAD_DIR → 404
  → send_file() with nosniff + private cache
```

The stored path never reaches the browser: templates link to
`url_for('attachments.view', attachment_id=...)`, and `file_name` is only ever
rendered as text.

---

## The rule that must not break

> An uploaded photo is **evidence**. It must never automatically determine a
> complaint's category or priority.

The values a student picks are the values that get stored. `suggestions.py` may
propose alternatives, and the UI surfaces them as a dismissible hint with a
"Use this" button — that click is the human validation the specification calls
for.

If image analysis is added later it must return a `Suggestion` like everything
else. Two tests in `tests/test_suggestions.py` exist purely to keep this true.

---

## Front end

Server-rendered Jinja2 with Bootstrap 5. No build step, no bundler, no
node_modules.

- **Design system** — the "Academic Integrity" palette and type scale, expressed
  as CSS custom properties at the top of `app/static/css/style.css`. Re-skinning
  for another institution means editing that one block.
- **Offline first** — Bootstrap and Inter are committed under
  `app/static/vendor/`, and icons are inline SVG (`templates/partials/_icons.html`).
  The application renders identically with no internet connection, which matters
  for a lab demo.
- **`evidence-upload.js`** — drag-and-drop, previews and removal. It maintains
  its own array of `File` objects and writes it back to the input via
  `DataTransfer`, which is what makes "remove one photo before submitting"
  possible. Every check it performs is repeated on the server.

---

## Testing

```
tests/conftest.py                  temporary SQLite + temporary upload dir
tests/test_upload_validation.py    the security-critical suite
tests/test_access_control.py       who can read evidence
tests/test_complaint_flow.py       submission through to closure
tests/test_suggestions.py          suggestions stay advisory
tests/test_two_factor.py           OTP expiry, attempts, single use, cooldown
tests/test_single_step_login.py    with 2FA off, every other guard still holds
tests/test_staff_authorization.py  approval is required, and permanent
tests/test_workflows.py            the eight end-to-end journeys
```

144 tests. The suite needs no MySQL and no `.env`, so it runs anywhere
including CI — a fresh clone passes with nothing configured.

`conftest.py` intercepts `mailers.send_otp` to capture codes, because a real
code is hashed before storage and cannot be read back. The OTP service itself
still runs unmodified.

The hostile payloads in `test_upload_validation.py` are assembled from hex at
runtime. A source file containing a literal shell one-liner gets quarantined by
antivirus software, which would delete the test file for anyone cloning the
repository.
