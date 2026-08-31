# Contributing to CampusCare

Thanks for taking an interest. This project began as a college mini project and
is deliberately kept small and readable, so contributions of every size are
welcome — including your first pull request anywhere.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Getting set up

```bash
git clone https://github.com/<your-username>/CampusCare.git
cd CampusCare
python -m venv .venv
```

```bash
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
cp .env.example .env
```

You need a MySQL or MariaDB server running to use the app. On Windows, XAMPP
provides one — start MySQL from its Control Panel. Then:

```bash
python scripts/init_db.py
python scripts/seed.py
python run.py
```

**You do not need MySQL to run the tests.** They use a temporary SQLite file:

```bash
pytest
```

---

## Ways to help

Good first contributions:

- **Screenshots** for the README — genuinely useful, and needs no Python
- **Keyword lists** in `app/services/suggestions.py` — add terms for problems
  your campus actually has
- **Mobile layout** improvements in `app/static/css/style.css`
- **Accessibility**: labels, focus order, colour contrast
- **Translations** of the interface strings

Larger pieces that would be welcome:

- Email notifications alongside the in-app ones (must stay optional — the
  project has to keep running with no mail server configured)
- Export of complaints to CSV or PDF
- A REST API for a future mobile client
- Alembic migrations, so schema changes do not require a database reset

Please open an issue before starting anything large, so we can agree on the
approach first.

---

## Coding conventions

The existing code is the best guide, but in short:

- **Formatting**: 4-space indent, ~90 character lines, double quotes.
  `black` and `isort` defaults match what is already there.
- **Docstrings**: every module and non-obvious function gets one. Say *why*,
  not just *what* — the reader can see what the code does.
- **Comments**: explain the reasoning behind a decision, especially a security
  one. Do not narrate the obvious.
- **Type hints** on function signatures where they clarify intent.
- **Imports**: standard library, then third party, then local — each group
  separated by a blank line.
- **Templates**: keep logic in the view or the service, not in Jinja.
- **Naming**: the vocabulary in `app/constants.py` is authoritative. Never
  hardcode a status string like `"IN_PROGRESS"` — use `Status.IN_PROGRESS`.

### Where code belongs

| Kind of change | Goes in |
| --- | --- |
| Database shape | `app/models.py`, then regenerate `sql/schema.sql` |
| Business rules | `app/services/` |
| HTTP routing, request/response | `app/blueprints/` |
| Form fields and basic validation | `app/forms.py` |
| Anything visual | `app/templates/`, `app/static/` |

Keep views thin. If a route grows past roughly 40 lines, the logic probably
belongs in a service.

---

## Rules that must not be broken

These are the invariants the project is built around. A pull request that
violates one will be asked to change, however convenient the shortcut.

1. **Never trust an uploaded file.** Extension, MIME type and magic bytes are
   all checked, and Pillow must decode the image, before anything touches
   disk. Do not move a check out of the server into JavaScript — the client-
   side checks in `evidence-upload.js` are a convenience, not a control.

2. **Never build a path from a user-supplied filename.** Stored names come
   from `secrets.token_hex`. The original is kept for display only.

3. **Never serve uploads statically.** Every image goes through
   `attachments.view`, which re-checks permissions per request.

4. **Never expose a filesystem path** in a URL, a page or an error message.

5. **A photo must never set a complaint's category or priority.** Analysis may
   only *suggest*, for a human to accept. See `app/services/suggestions.py`.

6. **Never commit `.env`, real uploads, or credentials.**

If you add a feature that touches uploads or permissions, add a test for it.

---

## Pull request process

1. Fork the repository and create a branch off `main`:

   ```bash
   git checkout -b feature/short-description
   ```

2. Make your change, with tests for anything behavioural.

3. Make sure the suite passes:

   ```bash
   pytest
   ```

4. Write clear commit messages — a short imperative summary line, then a body
   explaining *why* if it is not obvious:

   ```
   Reject WEBP files with a mismatched RIFF header

   Pillow accepts some malformed WEBP containers that other decoders
   refuse, so the stored file could fail to render for viewers.
   ```

5. Open the pull request, fill in the template, and link any related issue.

Reviews aim to be prompt and friendly. Expect questions — they are about the
code, never about you.

---

## Reporting bugs

Open an issue using the bug report template. The most useful reports include
what you expected, what happened, and the exact steps to reproduce it.

For anything security-related, please read [SECURITY.md](SECURITY.md) first and
do **not** open a public issue.
