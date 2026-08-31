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

`Complaint.is_visible_to(user)` is the one place the read-permission rule lives.
Both the complaint page and the evidence route call it, so they cannot drift
apart.

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

### `app/services/suggestions.py`

Transparent keyword scoring, not a trained model — no dataset, runs offline, and
every suggestion can be explained ("matched: leaking, water").

**It only ever suggests.** See [the design rule](#the-rule-that-must-not-break).

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
tests/conftest.py              temporary SQLite + temporary upload dir
tests/test_upload_validation.py   the security-critical suite
tests/test_access_control.py      who can read evidence
tests/test_complaint_flow.py      submission through to closure
tests/test_suggestions.py         suggestions stay advisory
```

The suite needs no MySQL, so it runs anywhere including CI.

The hostile payloads in `test_upload_validation.py` are assembled from hex at
runtime. A source file containing a literal shell one-liner gets quarantined by
antivirus software, which would delete the test file for anyone cloning the
repository.
