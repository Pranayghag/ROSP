"""Smart suggestions must stay advisory (Phase 14).

The specification is explicit: evidence must not automatically determine a
complaint's category or priority without validation, and any future analysis
may only *suggest*. These tests exist to make that a property the code cannot
quietly lose -- if someone later wires the suggestion engine straight into the
stored value, the last two tests here fail.
"""

from __future__ import annotations

from io import BytesIO

from sqlalchemy import select

from app.constants import Priority
from app.extensions import db
from app.models import Category, Complaint, Location
from app.services.suggestions import suggest, suggest_category, suggest_priority
from tests.conftest import make_image_bytes

# --------------------------------------------------------------------------
# The engine itself
# --------------------------------------------------------------------------


def test_suggests_plumbing_for_a_leak():
    result = suggest_category(
        "Water leaking from the ceiling",
        "Water is leaking from the ceiling of Room 204 and dripping on the benches.",
    )

    assert result.value == "Plumbing & Water"
    assert result.is_useful
    assert "leaking" in result.matched


def test_suggests_classroom_equipment_for_a_projector():
    result = suggest_category(
        "Projector not working", "The projector is not displaying anything."
    )

    assert result.value == "Classroom Equipment"


def test_safety_wording_raises_priority():
    result = suggest_priority(
        "Sparking socket", "The socket near the door is sparking and gave me a shock."
    )

    assert result.value == Priority.URGENT


def test_minor_wording_lowers_priority():
    result = suggest_priority(
        "Repaint request", "A minor cosmetic issue, please repaint whenever possible."
    )

    assert result.value == Priority.LOW


def test_empty_text_suggests_nothing():
    result = suggest_category("", "")

    assert result.value is None
    assert not result.is_useful


def test_unrecognised_text_is_not_forced_into_a_category():
    result = suggest_category("Xyzzy", "Plugh plover fee fie foe.")

    assert not result.is_useful


def test_suggest_returns_both_fields():
    result = suggest("Fan is broken", "The ceiling fan is not working at all.")

    assert set(result) == {"category", "priority"}


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def test_endpoint_requires_login(client):
    response = client.post("/complaints/suggest", json={"title": "x", "description": "y"})

    assert response.status_code == 302


def test_endpoint_marks_itself_advisory(client, login):
    login("student1@rosp.edu")
    response = client.post(
        "/complaints/suggest",
        json={
            "title": "Water leaking from the ceiling",
            "description": "Water is dripping onto the benches all day.",
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["advisory_only"] is True
    assert payload["category"]["value"] == "Plumbing & Water"
    assert payload["category"]["reason"].startswith("matched:")


def test_endpoint_changes_nothing_in_the_database(client, login):
    login("student1@rosp.edu")
    before = db.session.scalar(select(db.func.count(Complaint.id)))

    client.post(
        "/complaints/suggest",
        json={"title": "Sparking socket", "description": "It is sparking badly."},
    )

    assert db.session.scalar(select(db.func.count(Complaint.id))) == before


# --------------------------------------------------------------------------
# The guarantee: a suggestion never overrides what the student chose
# --------------------------------------------------------------------------


def test_submitted_priority_wins_over_the_suggestion(client, login):
    """Text screaming URGENT must not override the student's choice of LOW."""
    category = db.session.scalar(select(Category).where(Category.name == "Classroom Equipment"))
    location = db.session.scalar(select(Location))

    login("student1@rosp.edu")
    client.post(
        "/complaints/new",
        data={
            "title": "Sparking socket with fire and smoke",
            "description": "There is sparking, smoke and a real danger of fire here.",
            "category_id": str(category.id),
            "location_id": str(location.id),
            "priority": Priority.LOW,
        },
        follow_redirects=True,
    )

    complaint = db.session.scalar(select(Complaint))
    # The engine would say URGENT; the stored value is what the student picked.
    assert suggest_priority(complaint.title, complaint.description).value == Priority.URGENT
    assert complaint.priority == Priority.LOW


def test_uploaded_photo_does_not_change_category_or_priority(client, login):
    """Attaching evidence must not reclassify the complaint."""
    category = db.session.scalar(select(Category).where(Category.name == "Classroom Equipment"))
    location = db.session.scalar(select(Location))

    login("student1@rosp.edu")
    client.post(
        "/complaints/new",
        data={
            "title": "Water leaking from the ceiling",
            "description": "Water is leaking badly from the ceiling in Room 204.",
            "category_id": str(category.id),
            "location_id": str(location.id),
            "priority": Priority.MEDIUM,
            "photos": [(BytesIO(make_image_bytes()), "leak.jpg")],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    complaint = db.session.scalar(select(Complaint))
    assert len(complaint.attachments) == 1
    # Text and photo both point at Plumbing; the student's choice still stands.
    assert complaint.category_id == category.id
    assert complaint.priority == Priority.MEDIUM
