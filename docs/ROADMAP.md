# Roadmap

Where CampusCare could go next. Nothing here is committed work — treat it as a list of
good issues to pick up. Please open an issue before starting anything large.

---

## Near term

### Email notifications

Notifications are currently in-app only, deliberately: requiring SMTP
credentials would make the project harder to clone and run, which matters for an
open-source project a stranger should be able to try in five minutes.

If added, email must stay **optional** — absent configuration, the app must run
exactly as it does now.

- Add `MAIL_*` settings to `.env.example`, all optional
- Send from `app/services/notifications.py`, alongside the database write
- Queue sends outside the request cycle so a slow mail server never blocks a
  student's submission
- Let users opt out per notification type

### Alembic migrations

Schema changes currently require `init_db.py --reset`, which destroys data.
Adding Alembic would let deployments upgrade in place.

### Export

CSV and PDF export of a filtered complaint list, for reports to a department
head.

---

## Longer term

### REST API

A small JSON API would let someone build a mobile client. It needs a token
scheme separate from session cookies, and the same permission checks — the
`is_visible_to()` rule already lives in one place, so this is mostly plumbing.

### Full-text search

MySQL full-text indexes on complaint title and description, to find "that
projector complaint from last term".

### Per-department routing

Auto-suggest an assignee from the category's department, so an admin confirms
rather than chooses from scratch. **Suggest, not assign** — same principle as
category suggestions.

---

## Smart image analysis

Worth stating carefully, because it is the feature most likely to be added
badly.

**The rule:** an uploaded photo is evidence. It must never automatically set a
complaint's category or priority.

A model that looks at a photo of a wet ceiling and proposes *Plumbing & Water*
is genuinely useful. The same model silently re-filing a complaint is not — it
is wrong sometimes, invisibly, and the student has no idea why their complaint
went to the wrong department.

If you add it:

1. Plug into `app/services/suggestions.py` and return a `Suggestion`, exactly
   like the text analysis already does.
2. Surface it in the UI as a dismissible hint with a "Use this" button. The
   click is the validation step.
3. Show the reasoning. The keyword engine reports "matched: leaking, water"; an
   image model should be equally legible about why it thinks what it thinks.
4. Keep it optional. The app must work with the model absent or failing — a
   suggestion service that times out must never block a submission.
5. Keep it offline-capable, or clearly optional. Sending a student's evidence
   photo to a third-party API is a privacy decision, not a technical one, and
   the institution has to make it deliberately.
6. Add tests mirroring `test_uploaded_photo_does_not_change_category_or_priority`.

A local ONNX or TensorFlow Lite classifier over the nine seeded categories would
fit this shape well. Anything that phones home needs an explicit configuration
flag, defaulting to off.

---

## Deliberately not planned

- **Public complaint browsing.** Complaints name rooms and sometimes people. The
  permission model is intentionally narrow: the student, the assigned staff
  member, the admins.
- **Anonymous complaints.** Without an owner there is nobody to verify the fix,
  and the verification step is what makes the workflow honest.
- **Auto-closing stale complaints.** A complaint nobody attended to is a
  problem to escalate, not to hide.
