"""Load demo data so the project is explorable the moment it is cloned.

    python scripts/seed.py

Creates categories, locations, one admin, three staff, three students and a
handful of complaints spread across the workflow -- including complaints with
generated photo evidence, so the gallery and the permission checks can be seen
working without anyone having to find real images first.

Safe to re-run: existing rows are matched by natural key and left alone.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app import create_app
from app.constants import Priority, Role, Status
from app.extensions import db
from app.models import Category, Complaint, Location, User, utcnow
from app.services.sla import apply_sla
from app.services.uploads import store_evidence
from app.services.workflow import open_complaint, record_event

CATEGORIES = [
    ("Classroom Equipment", "Projectors, boards, benches and podium hardware", 48),
    ("Electrical", "Fans, lights, sockets and wiring faults", 24),
    ("Plumbing & Water", "Leaks, taps, drains and water coolers", 24),
    ("Washroom & Sanitation", "Cleanliness and sanitation issues", 12),
    ("Wi-Fi & Network", "Campus internet and network connectivity", 48),
    ("Furniture", "Desks, chairs and cupboards", 96),
    ("Air Conditioning", "Air conditioning and ventilation", 48),
    ("Civil & Building", "Ceilings, walls, floors, doors and windows", 96),
    ("Computer Lab", "Lab machines, peripherals and printers", 48),
]

LOCATIONS = [
    ("Room 101", "Academic Block A"),
    ("Room 204", "Academic Block A"),
    ("Room 305", "Academic Block B"),
    ("Computer Lab 1", "Academic Block B"),
    ("Computer Lab 2", "Academic Block B"),
    ("Central Library", "Library Building"),
    ("Boys Hostel - Block C", "Hostel"),
    ("Girls Hostel - Block D", "Hostel"),
    ("Main Canteen", "Student Centre"),
    ("Seminar Hall", "Administrative Block"),
    ("Ground Floor Washroom", "Academic Block A"),
    ("Sports Complex", "Sports Ground"),
]

USERS = [
    # (name, email, password, role, department, roll_no)
    ("Admin User", "admin@rosp.edu", "Admin@123", Role.ADMIN, "Administration", None),
    ("Ramesh Electrician", "ramesh@rosp.edu", "Staff@123", Role.STAFF, "Electrical", None),
    ("Sunita Plumber", "sunita@rosp.edu", "Staff@123", Role.STAFF, "Plumbing", None),
    ("Vikram IT Support", "vikram@rosp.edu", "Staff@123", Role.STAFF, "IT Services", None),
    ("Aman Gupta", "aman@rosp.edu", "Student@123", Role.STUDENT, "Computer Science", "CS21042"),
    ("Priya Sharma", "priya@rosp.edu", "Student@123", Role.STUDENT, "Information Tech", "IT21088"),
    ("Rahul Verma", "rahul@rosp.edu", "Student@123", Role.STUDENT, "Electronics", "EC21015"),
]

#: (title, description, category, location, priority, student email, status,
#:  [(photo label, colour)])
COMPLAINTS = [
    (
        "Water leaking from the ceiling",
        (
            "Water is leaking from the ceiling of Room 204. It has been dripping "
            "onto the back benches since yesterday and the floor stays wet all day. "
            "Students sitting there have to move to other seats."
        ),
        "Plumbing & Water",
        "Room 204",
        Priority.HIGH,
        "aman@rosp.edu",
        Status.IN_PROGRESS,
        [("Water leak on ceiling", (86, 130, 168)), ("Damaged ceiling panel", (140, 122, 96))],
    ),
    (
        "Projector not working",
        (
            "The projector in Room 204 is not displaying anything. The power light "
            "turns on but the screen stays blank even after trying two different "
            "laptops and both HDMI cables."
        ),
        "Classroom Equipment",
        "Room 204",
        Priority.MEDIUM,
        "aman@rosp.edu",
        Status.PENDING,
        [("Blank projector screen", (64, 68, 82))],
    ),
    (
        "Ceiling fan making loud noise",
        (
            "The ceiling fan in Room 101 makes a loud grinding noise at every speed "
            "and wobbles badly. It feels unsafe to sit directly underneath it."
        ),
        "Electrical",
        "Room 101",
        Priority.URGENT,
        "priya@rosp.edu",
        Status.ASSIGNED,
        [("Wobbling ceiling fan", (150, 140, 120))],
    ),
    (
        "Wi-Fi keeps disconnecting in the library",
        (
            "The campus Wi-Fi in the Central Library disconnects every few minutes. "
            "It reconnects on its own but drops again within five minutes, which "
            "makes it impossible to submit online assignments."
        ),
        "Wi-Fi & Network",
        "Central Library",
        Priority.MEDIUM,
        "priya@rosp.edu",
        Status.STUDENT_VERIFICATION,
        [],
    ),
    (
        "Washroom tap is broken",
        (
            "The second tap in the ground floor washroom cannot be closed properly "
            "and water runs continuously. A lot of water is being wasted every day."
        ),
        "Plumbing & Water",
        "Ground Floor Washroom",
        Priority.HIGH,
        "rahul@rosp.edu",
        Status.CLOSED,
        [("Running tap", (110, 145, 160))],
    ),
    (
        "Lab computer will not boot",
        (
            "System number 14 in Computer Lab 1 does not start. The power LED "
            "blinks but nothing appears on the monitor."
        ),
        "Computer Lab",
        "Computer Lab 1",
        Priority.MEDIUM,
        "rahul@rosp.edu",
        Status.PENDING,
        [],
    ),
    (
        "Broken chairs in the seminar hall",
        (
            "Four chairs in the last two rows of the Seminar Hall have broken "
            "backrests and cannot be used during events."
        ),
        "Furniture",
        "Seminar Hall",
        Priority.LOW,
        "aman@rosp.edu",
        Status.PENDING,
        [],
    ),
]


def make_sample_photo(label: str, colour: tuple[int, int, int]) -> FileStorage:
    """Generate a labelled placeholder JPEG as an in-memory upload.

    Real photographs are not shipped with the repository -- they would bloat
    the clone and could carry personal metadata. These generated stand-ins go
    through exactly the same validation pipeline as a real upload.
    """
    width, height = 900, 640
    image = Image.new("RGB", (width, height), colour)
    draw = ImageDraw.Draw(image)

    # A few translucent bands so the thumbnails are visually distinguishable.
    for index in range(0, height, 90):
        shade = tuple(min(255, channel + 18) for channel in colour)
        draw.rectangle([0, index, width, index + 45], fill=shade)

    draw.rectangle([40, height // 2 - 60, width - 40, height // 2 + 60], fill=(0, 0, 0))
    draw.text((70, height // 2 - 18), label, fill=(255, 255, 255))
    draw.text((70, height // 2 + 6), "ROSP sample evidence", fill=(200, 200, 200))

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    buffer.seek(0)

    filename = label.lower().replace(" ", "-") + ".jpg"
    return FileStorage(stream=buffer, filename=filename, content_type="image/jpeg")


def _get_or_create(model, defaults: dict | None = None, **lookup):
    """Fetch a row by natural key, creating it when absent."""
    instance = db.session.scalar(
        select(model).filter_by(**lookup)
    )
    if instance:
        return instance, False
    instance = model(**lookup, **(defaults or {}))
    db.session.add(instance)
    return instance, True


def seed_all() -> None:
    """Populate the database with demo data."""
    created = {"categories": 0, "locations": 0, "users": 0, "complaints": 0, "photos": 0}

    for name, description, sla_hours in CATEGORIES:
        _, made = _get_or_create(
            Category, {"description": description, "sla_hours": sla_hours}, name=name
        )
        created["categories"] += int(made)

    for name, building in LOCATIONS:
        _, made = _get_or_create(Location, {"building": building}, name=name)
        created["locations"] += int(made)

    db.session.flush()

    for name, email, password, role, department, roll_no in USERS:
        user, made = _get_or_create(
            User,
            {"name": name, "role": role, "department": department, "roll_no": roll_no},
            email=email,
        )
        if made:
            user.set_password(password)
            created["users"] += 1

    db.session.flush()

    users = {u.email: u for u in db.session.scalars(select(User)).all()}
    categories = {c.name: c for c in db.session.scalars(select(Category)).all()}
    locations = {loc.name: loc for loc in db.session.scalars(select(Location)).all()}
    staff = [u for u in users.values() if u.role == Role.STAFF]
    admin = next(u for u in users.values() if u.role == Role.ADMIN)

    for index, spec in enumerate(COMPLAINTS):
        (title, description, category, location, priority, email, status, photos) = spec

        if db.session.scalar(select(Complaint).where(Complaint.title == title)):
            continue

        student = users[email]
        complaint = Complaint(
            title=title,
            description=description,
            category_id=categories[category].id,
            location_id=locations[location].id,
            priority=priority,
            status=Status.PENDING,
            student_id=student.id,
            # Stagger creation times so the analytics chart has a shape.
            created_at=utcnow() - timedelta(days=index, hours=index * 3),
        )
        db.session.add(complaint)
        db.session.flush()
        complaint.assign_code()
        apply_sla(complaint)
        open_complaint(complaint, student)
        created["complaints"] += 1

        if photos:
            uploads = [make_sample_photo(label, colour) for label, colour in photos]
            for attachment in store_evidence(uploads, complaint, student.id):
                db.session.add(attachment)
                created["photos"] += 1

        # Walk the complaint forward to its target status, recording the
        # intermediate events so the timeline looks realistic.
        if status != Status.PENDING and staff:
            assignee = staff[index % len(staff)]
            complaint.assigned_staff_id = assignee.id
            complaint.status = Status.ASSIGNED
            record_event(complaint, Status.ASSIGNED, admin, f"Assigned to {assignee.name}.")

            if status in (
                Status.IN_PROGRESS,
                Status.RESOLVED,
                Status.STUDENT_VERIFICATION,
                Status.CLOSED,
            ):
                complaint.status = Status.IN_PROGRESS
                record_event(complaint, Status.IN_PROGRESS, assignee, "Work started.")

            if status in (Status.RESOLVED, Status.STUDENT_VERIFICATION, Status.CLOSED):
                complaint.resolved_at = utcnow() - timedelta(hours=2)
                record_event(complaint, Status.RESOLVED, assignee, "Marked as resolved.")
                complaint.status = Status.STUDENT_VERIFICATION
                record_event(
                    complaint,
                    Status.STUDENT_VERIFICATION,
                    assignee,
                    "Awaiting confirmation from the student.",
                )

            if status == Status.CLOSED:
                complaint.closed_at = utcnow() - timedelta(hours=1)
                complaint.status = Status.CLOSED
                record_event(complaint, Status.CLOSED, student, "Student confirmed the fix.")
            elif status in (Status.ASSIGNED, Status.IN_PROGRESS):
                complaint.status = status

    db.session.commit()

    print("Seed complete:")
    for key, value in created.items():
        print(f"  {key:<12} +{value}")
    print("\nDemo accounts (password shown):")
    print(f"  {'admin@rosp.edu':<22} Admin@123     (admin)")
    print(f"  {'ramesh@rosp.edu':<22} Staff@123     (staff)")
    print(f"  {'aman@rosp.edu':<22} Student@123   (student)")


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        seed_all()


if __name__ == "__main__":
    main()
