"""Generate ``docs/ASSISTANT.md`` from the assistant's own catalogue.

    python scripts/dump_assistant_topics.py

``app/services/chatbot.py`` is the single source of truth for what the
assistant understands. Writing the documentation by hand would guarantee it
drifts: a pattern gets widened, a topic gets added, and the list in the docs
quietly becomes a list of things that used to work.

So the docs are rendered from the same tuple that does the matching, and
``tests/test_assistant_coverage.py`` proves every question in it still routes
where it claims to. Re-run this after changing ``TOPICS``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.constants import Role
from app.services import chatbot

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "ASSISTANT.md"

HEADER = """\
# The CampusCare Assistant

*GENERATED FILE — do not edit by hand.*
*Regenerate with:* `python scripts/dump_assistant_topics.py`

The assistant is the chat panel behind the **Need help?** button, available on
every page once signed in. It answers from the database, scoped to whoever is
asking, and it is **not** a language model — see the module docstring in
`app/services/chatbot.py` for why that is a deliberate choice rather than a
limitation.

This file lists everything it can answer. The same list is browsable inside the
app: open the assistant and press the **☰** button in its header.

## How a message is resolved

Four stages, stopping at the first that produces an answer:

1. **A complaint code** anywhere in the message wins outright — `CC102`,
   `cc-102`, `complaint 102`. A code the asker is not entitled to see is
   answered as *not found*, revealing nothing about it.
2. **An exact pattern** from the catalogue below.
3. **Keyword scoring** across the catalogue. A confident match is answered
   directly; a weak one is answered but prefixed with *"I am not certain I
   understood…"*, so a wrong turn is obvious.
4. **Problem detection.** A message describing something broken is treated as
   a report rather than a question: the assistant names the category and
   priority the wording suggests, says plainly that a complaint typed into the
   chat reaches nobody, and links to the form.

Only when all four find nothing does it say so — and even then it offers the
closest questions it does know, plus a route to a person.

## What it will never do

- Reveal a complaint the asker cannot already open in the UI. Scoping lives in
  one function, `_own_complaints`, and code lookups reuse the same
  `Complaint.is_visible_to` rule the complaint page uses.
- Invent a status, a name, or a date. When it does not know, it says so.
- Change anything. It has no write path — it cannot file, assign, close or
  message on anyone's behalf.
- Store the conversation. The transcript lives in the browser tab and is gone
  when the tab closes.

"""

FOOTER = """\

## Adding a topic

Add one `Topic(...)` to `TOPICS` in `app/services/chatbot.py`, carrying:

| Field | Purpose |
| --- | --- |
| `key` | Stable identifier, also the `intent` in the JSON reply |
| `group` | Heading it appears under, from `GROUP_ORDER` |
| `title` | Short label in the browsable menu |
| `examples` | Questions a student would actually type — the first is the one shown |
| `keywords` | Softer signals for the keyword-scoring fallback |
| `pattern` | The precise regular expression |
| `handler` | Function returning an `Answer` |
| `roles` | Optional: restrict to `staff`/`admin` |

Order matters — the list is checked top to bottom, so a new broad pattern
placed too early will steal questions from narrower topics below it. The menu,
this document and the fallback all update themselves from that one row.

Then run the tests: `test_every_advertised_question_reaches_its_own_topic`
fails if the new pattern steals anything, or if anything steals from it.
"""


class _Everyone:
    """Stand-in asker used only to render the whole catalogue.

    The real filtering is by role, and this document should show every topic
    including the staff-only ones, so it reports as an admin.
    """

    role = Role.ADMIN
    is_admin = True
    is_staff = False
    is_student = False


def render() -> str:
    parts = [HEADER]

    listed = [topic for topic in chatbot.TOPICS if topic.listed]
    unlisted = [topic for topic in chatbot.TOPICS if not topic.listed]

    parts.append(
        f"## What a student can ask\n\n"
        f"{len(listed)} topics across "
        f"{len(chatbot._visible_groups(_Everyone()))} groups.\n"
    )

    for group in chatbot._visible_groups(_Everyone()):
        parts.append(f"\n### {group['name']}\n")
        for topic in group["topics"]:
            audience = ""
            if topic.roles:
                audience = f" *(only shown to {', '.join(topic.roles)})*"
            parts.append(f"\n**{topic.title}**{audience}\n")
            for example in topic.examples:
                parts.append(f"- “{example}”\n")

    parts.append(
        "\n## Recognised without being listed\n\n"
        "Handled, but kept out of the menu because nobody browses for them:\n\n"
    )
    for topic in unlisted:
        parts.append(f"- **{topic.title}** — e.g. “{topic.examples[0]}”\n")

    parts.append(FOOTER)
    return "".join(parts)


def main() -> None:
    app = create_app()
    with app.app_context(), app.test_request_context():
        OUTPUT.write_text(render(), encoding="utf-8")

    listed = sum(1 for topic in chatbot.TOPICS if topic.listed)
    questions = sum(len(topic.examples) for topic in chatbot.TOPICS)
    print(f"Wrote {OUTPUT.relative_to(Path.cwd())}")
    print(f"  {len(chatbot.TOPICS)} topics ({listed} listed), {questions} example questions")


if __name__ == "__main__":
    main()
