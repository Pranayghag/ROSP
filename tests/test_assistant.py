"""The CampusCare Assistant.

Two things matter most here and get the heaviest coverage:

* **It cannot leak.** Every lookup is scoped to the person asking, so naming
  another student's complaint code must reveal nothing at all -- not the title,
  not the status, not even that it exists.
* **It does not invent.** When nothing matches, it says so. A fabricated status
  is worse than no answer, because a student would act on it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.constants import Status
from app.extensions import db
from app.models import Category, Complaint, Location


def make_complaint(student, title="Ceiling fan is broken", **overrides):
    category = db.session.scalar(select(Category))
    location = db.session.scalar(select(Location))

    complaint = Complaint(
        title=title,
        description="The fan grinds loudly and wobbles at every speed.",
        category_id=category.id,
        location_id=location.id,
        student_id=student.id,
        **overrides,
    )
    db.session.add(complaint)
    db.session.flush()
    complaint.assign_code()
    db.session.commit()
    return complaint


def ask(client, message):
    return client.post("/assistant/ask", json={"message": message})


# ==========================================================================
# Access control -- the part that must never regress
# ==========================================================================


def test_assistant_requires_signing_in(client):
    response = client.post("/assistant/ask", json={"message": "hello"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_open_requires_signing_in(client):
    assert client.get("/assistant/open").status_code == 302


def test_cannot_read_another_students_complaint(client, login, users):
    """Naming someone else's code must reveal nothing about it."""
    theirs = make_complaint(users["student2"], title="Secret leaking pipe")

    login("student1@rosp.edu")
    body = ask(client, f"status of {theirs.code}").get_json()

    assert "Secret leaking pipe" not in body["text"]
    assert theirs.status not in body["text"]
    assert body["intent"] == "complaint_not_found"
    assert "could not find" in body["text"].lower()


def test_listing_shows_only_your_own(client, login, users):
    make_complaint(users["student1"], title="My own broken fan")
    make_complaint(users["student2"], title="Another student's problem")

    login("student1@rosp.edu")
    body = ask(client, "show my complaints").get_json()

    assert "My own broken fan" in body["text"]
    assert "Another student's problem" not in body["text"]


def test_staff_see_their_assigned_queue(client, login, users):
    """A staff member's view is their assignments, not every complaint."""
    mine = make_complaint(
        users["student1"], title="Assigned to me", assigned_staff_id=users["staff1"].id
    )
    make_complaint(users["student1"], title="Someone else's job")

    login("staff1@rosp.edu")
    body = ask(client, "my complaints").get_json()

    assert mine.code in body["text"]
    assert "Someone else's job" not in body["text"]


# ==========================================================================
# Answering from real data
# ==========================================================================


def test_reports_the_actual_status(client, login, users):
    complaint = make_complaint(users["student1"], status=Status.IN_PROGRESS,
                               assigned_staff_id=users["staff1"].id)

    login("student1@rosp.edu")
    body = ask(client, f"where is {complaint.code}").get_json()

    assert complaint.code in body["text"]
    assert users["staff1"].name in body["text"]
    assert body["intent"] == "complaint_status"
    assert any(complaint.code in link["label"] for link in body["links"])


@pytest.mark.parametrize(
    "phrasing",
    ["CC101", "cc101", "cc 101", "cc-101", "status of complaint 101"],
)
def test_understands_how_students_write_a_code(client, login, users, phrasing):
    complaint = make_complaint(users["student1"])
    assert complaint.code == "CC101"

    login("student1@rosp.edu")
    body = ask(client, phrasing).get_json()

    assert body["intent"] == "complaint_status"


def test_flags_a_complaint_waiting_on_the_student(client, login, users):
    complaint = make_complaint(
        users["student1"],
        status=Status.STUDENT_VERIFICATION,
        assigned_staff_id=users["staff1"].id,
        resolution_note="Replaced the bearing and balanced the blades.",
    )

    login("student1@rosp.edu")
    body = ask(client, f"what about {complaint.code}").get_json()

    assert "waiting for you" in body["text"].lower()
    assert "Replaced the bearing" in body["text"]


def test_reports_overdue_complaints(client, login, users):
    from datetime import timedelta

    from app.models import utcnow

    complaint = make_complaint(users["student1"], title="Long overdue thing")
    complaint.due_at = utcnow() - timedelta(hours=30)
    db.session.commit()

    login("student1@rosp.edu")
    body = ask(client, "is anything overdue?").get_json()

    assert body["intent"] == "overdue"
    assert complaint.code in body["text"]


def test_says_nothing_is_overdue_when_true(client, login, users):
    make_complaint(users["student1"])

    login("student1@rosp.edu")
    body = ask(client, "anything overdue?").get_json()

    assert body["intent"] == "overdue_none"


def test_names_the_assigned_staff_member(client, login, users):
    make_complaint(users["student1"], assigned_staff_id=users["staff1"].id,
                   status=Status.ASSIGNED)

    login("student1@rosp.edu")
    body = ask(client, "who is handling my complaint?").get_json()

    assert users["staff1"].name in body["text"]
    assert body["intent"] == "assignee"


def test_lists_the_real_categories(client, login):
    login("student1@rosp.edu")
    body = ask(client, "what categories are there?").get_json()

    categories = db.session.scalars(select(Category)).all()
    assert body["intent"] == "categories"
    for category in categories:
        assert category.name in body["text"]


def test_greeting_counts_your_open_complaints(client, login, users):
    make_complaint(users["student1"])
    make_complaint(users["student1"], title="Second one")

    login("student1@rosp.edu")
    body = client.get("/assistant/open").get_json()

    assert body["intent"] == "greeting"
    assert "2" in body["text"]
    assert users["student1"].name.split()[0] in body["text"]


def test_empty_state_is_encouraging_not_an_error(client, login):
    login("student1@rosp.edu")
    body = ask(client, "show my complaints").get_json()

    assert body["intent"] == "my_complaints_empty"
    assert "not filed any" in body["text"].lower()
    assert body["links"], "should offer a way to file one"


# ==========================================================================
# Guidance intents
# ==========================================================================


@pytest.mark.parametrize(
    "question,expected_intent",
    [
        ("how do I file a complaint?", "how_to_file"),
        ("how do I attach photos?", "photo_help"),
        ("can I upload an image?", "photo_help"),
        ("what happens when it is resolved?", "verification"),
        ("it is still broken", "verification"),
        ("why is it taking so long?", "why_slow"),
        ("what categories are there?", "categories"),
        ("I forgot my password", "account"),
        ("hello", "greeting"),
        ("thanks", "thanks"),
        ("what can you do?", "help"),
    ],
)
def test_intent_matching(client, login, question, expected_intent):
    login("student1@rosp.edu")
    body = ask(client, question).get_json()

    assert body["intent"] == expected_intent


def test_unknown_questions_admit_it(client, login):
    login("student1@rosp.edu")
    body = ask(client, "what is the capital of Mongolia").get_json()

    assert body["intent"] == "unknown"
    assert "did not follow" in body["text"].lower()
    assert body["suggestions"], "a dead end should still offer a way forward"


def test_does_not_invent_a_status(client, login):
    """With no complaints at all, nothing may look like a real answer."""
    login("student1@rosp.edu")
    body = ask(client, "what is the status of my complaint").get_json()

    assert "in progress" not in body["text"].lower()
    assert "resolved" not in body["text"].lower()
    assert body["intent"] in ("my_complaints_empty", "my_complaints")


# ==========================================================================
# Input handling
# ==========================================================================


def test_blank_message_offers_help(client, login):
    login("student1@rosp.edu")
    body = ask(client, "   ").get_json()

    assert body["intent"] == "help"


def test_very_long_message_is_refused_politely(client, login):
    login("student1@rosp.edu")
    body = ask(client, "x" * 600 + " help").get_json()

    assert body["intent"] == "too_long"


def test_oversized_body_is_truncated_not_crashed(client, login):
    login("student1@rosp.edu")
    response = ask(client, "y" * 5000)

    assert response.status_code == 200


def test_non_string_message_is_rejected(client, login):
    login("student1@rosp.edu")
    response = client.post("/assistant/ask", json={"message": {"nested": "object"}})

    assert response.status_code == 400


def test_missing_message_key_is_handled(client, login):
    login("student1@rosp.edu")
    response = client.post("/assistant/ask", json={})

    assert response.status_code == 200
    assert response.get_json()["intent"] == "help"


# ==========================================================================
# The widget itself
# ==========================================================================


def test_widget_renders_for_signed_in_users(client, login):
    login("student1@rosp.edu")
    body = client.get("/dashboard").get_data(as_text=True)

    assert 'id="assistant"' in body
    assert "assistant.js" in body


def test_widget_is_absent_when_signed_out(client):
    body = client.get("/login").get_data(as_text=True)

    assert 'id="assistant"' not in body
    assert "assistant.js" not in body
