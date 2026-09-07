"""CampusCare Assistant -- the in-app help chat.

WHAT THIS IS, AND WHY IT IS NOT A LANGUAGE MODEL
------------------------------------------------
The questions students actually ask are about *their own data*:

    "Where has my complaint got to?"
    "Why has nobody looked at CC102 yet?"
    "Who is fixing the fan in Room 101?"

A general-purpose model cannot answer any of those without reading the
database, and if it guesses it will invent a status -- which is worse than
saying nothing, because a student would act on it. So this assistant matches
intent and then answers from real rows: the actual status, the actual staff
member, the actual deadline.

For the questions that are genuinely procedural ("how do I attach a photo?")
the answer is fixed text, which is exactly right: the procedure does not vary,
and a fixed answer cannot drift from what the software really does.

The result needs no API key, costs nothing, runs offline, and every reply can
be traced to a line of code or a database row -- which also makes it defensible
in a viva.

ONE CATALOGUE, FOUR JOBS
------------------------
:data:`TOPICS` is the single source of truth. Each entry carries the questions
a student might type, the keywords that hint at it, the precise pattern that
recognises it, and the handler that answers it. That one table is used for:

1. **Matching** -- the ordered pattern list, most specific first.
2. **Fuzzy fallback** -- keyword scoring, for phrasings no pattern anticipated.
3. **The browsable "What can I ask?" list** inside the widget.
4. **The documentation** in ``docs/ASSISTANT.md``, generated from the same rows.

Adding a capability therefore means adding one row; the menu, the docs and the
fallback all pick it up for free, and cannot drift out of sync.

HOW A MESSAGE IS RESOLVED
-------------------------
Four stages, stopping at the first that produces an answer:

1. A **complaint code** anywhere in the message wins outright (CC102).
2. An **exact pattern** from the catalogue.
3. **Keyword scoring** across the catalogue -- a confident match answers
   directly, a weak one answers but says it is unsure.
4. **Problem detection** -- if the message describes something broken rather
   than asking a question, it is treated as a report: the assistant names the
   category and priority its wording suggests and points at the form.

Only when all four find nothing does it say so, and even then it offers the
closest questions it *does* know and a route to a human.

SECURITY
--------
**Every query is scoped to the user who is asking.** The assistant is not a
search tool over all complaints: a student can only ever be told about rows
where they are the author. That scoping lives in :func:`_own_complaints` and is
the only way this module reaches the Complaint table.

HONESTY
-------
When nothing matches, the assistant says so and offers real next steps. It
never fabricates a status, a name, or a date.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from flask import current_app, url_for
from sqlalchemy import select

from ..constants import (
    DESIGNATIONS,
    STATUS_LABELS,
    TIMELINE_STEPS,
    Priority,
    Role,
    Status,
)
from ..extensions import db
from ..models import Complaint, _as_utc, utcnow
from . import suggestions as suggestion_engine

#: Complaint references as students write them: CC102, cc-102, "complaint 102".
_CODE = re.compile(
    r"\b(?:cc[\s-]?)(\d{2,6})\b|\bcomplaint\s+(?:no\.?|number\s*)?#?(\d{2,6})\b", re.I
)

#: Longest message the assistant will attempt. Past this it asks for a shorter
#: question rather than answering half of one.
MAX_MESSAGE = 500


@dataclass
class Answer:
    """One reply from the assistant."""

    text: str
    #: Quick replies offered as buttons, to save typing on a phone.
    suggestions: list[str] = field(default_factory=list)
    #: ``(label, url)`` pairs rendered as links.
    links: list[tuple[str, str]] = field(default_factory=list)
    #: Names the intent that produced this, for the tests and for debugging.
    intent: str = "unknown"

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "suggestions": self.suggestions,
            "links": [{"label": label, "url": url} for label, url in self.links],
            "intent": self.intent,
        }


# --------------------------------------------------------------------------
# Data access -- the only place this module touches complaints
# --------------------------------------------------------------------------


def _own_complaints(user, *, newest_first: bool = True) -> list[Complaint]:
    """Every complaint belonging to ``user``.

    The scoping filter is here and nowhere else. A student is only ever told
    about complaints they filed; staff see their assigned queue. An admin gets
    the complaints they are personally involved in -- for the whole-college
    view they have the admin queue, which enforces its own permissions.
    """
    query = select(Complaint)
    if getattr(user, "is_student", False):
        query = query.where(Complaint.student_id == user.id)
    elif getattr(user, "is_staff", False):
        query = query.where(Complaint.assigned_staff_id == user.id)
    else:
        query = query.where(
            db.or_(
                Complaint.student_id == user.id,
                Complaint.assigned_staff_id == user.id,
            )
        )

    order = Complaint.created_at.desc() if newest_first else Complaint.created_at.asc()
    return list(db.session.scalars(query.order_by(order)).all())


def _find_by_code(user, message: str) -> Complaint | None:
    """The complaint a message refers to, if the user is entitled to it."""
    match = _CODE.search(message)
    if not match:
        return None

    number = match.group(1) or match.group(2)
    code = f"CC{number}"

    complaint = db.session.scalar(select(Complaint).where(Complaint.code == code))
    # is_visible_to is the same rule the complaint page uses, so the assistant
    # can never reveal something the UI would refuse to show.
    if complaint is not None and complaint.is_visible_to(user):
        return complaint
    return None


def _categories():
    from ..models import Category

    return db.session.scalars(
        select(Category).where(Category.is_active.is_(True)).order_by(Category.name)
    ).all()


def _locations():
    from ..models import Location

    return db.session.scalars(
        select(Location).where(Location.is_active.is_(True)).order_by(Location.name)
    ).all()


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _first_name(user) -> str | None:
    name = getattr(user, "name", None)
    return name.split()[0] if name else None


def _role_of(user) -> str:
    for attribute, role in (
        ("is_admin", Role.ADMIN),
        ("is_staff", Role.STAFF),
        ("is_student", Role.STUDENT),
    ):
        if getattr(user, attribute, False):
            return role
    return Role.STUDENT


def _app_name() -> str:
    return current_app.config.get("APP_NAME", "CampusCare")


def _ago(moment) -> str:
    """Human phrasing for how long ago something happened."""
    if not moment:
        return "recently"
    seconds = (utcnow() - _as_utc(moment)).total_seconds()
    if seconds < 3600:
        return "less than an hour ago"
    if seconds < 86400:
        return f"about {int(seconds // 3600)} hours ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


def _status_sentence(complaint: Complaint) -> str:
    """A plain-English description of where a complaint stands."""
    label = STATUS_LABELS.get(complaint.status, complaint.status)
    staff = complaint.assigned_staff.name if complaint.assigned_staff else None

    if complaint.status == Status.PENDING:
        return (
            f"{complaint.code} is **{label}**. An administrator has not assigned "
            "it to anyone yet."
        )
    if complaint.status == Status.ASSIGNED:
        return f"{complaint.code} is **{label}** to {staff}, who has not started yet."
    if complaint.status == Status.IN_PROGRESS:
        return f"{complaint.code} is **{label}**. {staff} is working on it now."
    if complaint.status == Status.STUDENT_VERIFICATION:
        return (
            f"{complaint.code} has been marked resolved"
            + (f" by {staff}" if staff else "")
            + ". **It is waiting for you to confirm the fix.**"
        )
    if complaint.status == Status.CLOSED:
        return f"{complaint.code} is **Closed**. You confirmed the problem was fixed."
    if complaint.status == Status.REOPENED:
        return f"{complaint.code} was **reopened** and is back with the staff member."
    return f"{complaint.code} is **{label}**."


def _deadline_sentence(complaint: Complaint) -> str:
    """What the SLA says about this complaint, in words."""
    if not complaint.due_at or not complaint.is_open:
        return ""
    if complaint.is_overdue:
        hours = abs(complaint.hours_remaining or 0)
        return (
            f" It is **{hours:.0f} hours past its deadline**, so it counts as "
            "overdue and administrators can see that."
        )
    remaining = complaint.hours_remaining or 0
    if remaining < 24:
        return f" It is due in about {remaining:.0f} hours."
    return f" It is due in about {remaining / 24:.0f} days."


def _complaint_link(complaint: Complaint) -> tuple[str, str]:
    return (
        f"Open {complaint.code}",
        url_for("complaints.detail", complaint_id=complaint.id),
    )


def _bullet_list(complaints: list[Complaint], limit: int = 5) -> list[str]:
    """One line per complaint, showing code, title, status and overdue flag."""
    lines = []
    for complaint in complaints[:limit]:
        label = STATUS_LABELS.get(complaint.status, complaint.status)
        flag = " ⚠ overdue" if complaint.is_overdue else ""
        lines.append(f"• **{complaint.code}** — {complaint.title} — {label}{flag}")
    if len(complaints) > limit:
        lines.append(f"…and {len(complaints) - limit} more.")
    return lines


def _no_complaints_answer(user, intent: str = "my_complaints_empty") -> Answer:
    """The empty state, worded for whoever is asking."""
    if _role_of(user) == Role.STUDENT:
        return Answer(
            "You have not filed any complaints yet. When something on campus "
            "needs fixing -- a broken fan, a leaking ceiling, a dead projector "
            "-- file it here and you will be able to follow it right through to "
            "the repair.",
            suggestions=["How do I file a complaint?", "What categories are there?"],
            links=[("File a complaint", url_for("complaints.new"))],
            intent=intent,
        )
    if _role_of(user) == Role.STAFF:
        return Answer(
            "Nothing is assigned to you at the moment. An administrator passes "
            "new complaints to staff, and you will get a notification the "
            "moment one reaches you.",
            suggestions=["How do I resolve a complaint?"],
            links=[("Assigned to me", url_for("complaints.index"))],
            intent=intent,
        )
    return Answer(
        "Nothing is filed by you or assigned to you personally. The whole-"
        "college view is on the admin queue.",
        links=[("All complaints", url_for("admin.queue"))],
        intent=intent,
    )


# --------------------------------------------------------------------------
# Handlers: tracking your complaints
# --------------------------------------------------------------------------


def _handle_specific_complaint(user, message: str) -> Answer | None:
    complaint = _find_by_code(user, message)
    if complaint is None:
        # They named something like CC999 that is not theirs or does not exist.
        if _CODE.search(message):
            return Answer(
                "I could not find that complaint under your account. Check the "
                "reference number on your complaints list -- it looks like "
                "CC101, CC102 and so on.",
                suggestions=["Show my complaints", "How do I file a complaint?"],
                links=[("My complaints", url_for("complaints.index"))],
                intent="complaint_not_found",
            )
        return None

    text = _status_sentence(complaint) + _deadline_sentence(complaint)
    text += f"\n\nIt was filed {_ago(complaint.created_at)} about “{complaint.title}”."
    text += f"\nCategory **{complaint.category.name}**, location **{complaint.location.name}**, priority **{complaint.priority.title()}**."

    if complaint.evidence:
        count = len(complaint.evidence)
        text += f"\nYou attached **{count}** photo{'s' if count != 1 else ''} as evidence."

    if complaint.resolution_note:
        text += f"\n\nWhat the staff member did: “{complaint.resolution_note}”"

    suggestions = []
    if complaint.status == Status.STUDENT_VERIFICATION:
        text += (
            "\n\nOpen it to compare the before and after photos, then confirm "
            "whether it is actually fixed."
        )
        suggestions = ["What if it is not fixed?"]
    elif complaint.is_open:
        suggestions = [
            "Why is it taking so long?",
            "Who is handling it?",
            f"History of {complaint.code}",
        ]

    return Answer(
        text,
        suggestions=suggestions,
        links=[_complaint_link(complaint)],
        intent="complaint_status",
    )


def _handle_my_complaints(user, message: str) -> Answer:
    complaints = _own_complaints(user)

    if not complaints:
        return _no_complaints_answer(user)

    open_ones = [c for c in complaints if c.is_open]
    lines = [
        f"You have **{len(complaints)}** complaint(s), **{len(open_ones)}** still open."
    ]
    lines.extend(_bullet_list(complaints))

    awaiting = [c for c in complaints if c.status == Status.STUDENT_VERIFICATION]
    if awaiting:
        codes = ", ".join(c.code for c in awaiting)
        lines.append(
            f"\n**{codes}** need{'s' if len(awaiting) == 1 else ''} you to confirm "
            "the work is done."
        )

    return Answer(
        "\n".join(lines),
        suggestions=["File a new complaint", "Anything overdue?", "How many are open?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="my_complaints",
    )


#: Words a student uses for each status, so "show my finished ones" works.
_STATUS_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (Status.PENDING, ("pending", "unassigned", "waiting", "not assigned", "new")),
    (Status.ASSIGNED, ("assigned", "allotted", "allocated")),
    (Status.IN_PROGRESS, ("in progress", "ongoing", "being fixed", "started", "underway")),
    (
        Status.STUDENT_VERIFICATION,
        ("verification", "verify", "confirm", "awaiting me", "waiting on me"),
    ),
    (Status.CLOSED, ("closed", "finished", "completed", "done", "settled")),
    (Status.REOPENED, ("reopened", "reopen")),
    (Status.RESOLVED, ("resolved", "fixed", "repaired")),
)


def _requested_status(message: str) -> str | None:
    """Which status a message is asking about, if any."""
    lowered = message.lower()
    for status, words in _STATUS_SYNONYMS:
        for word in words:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                return status
    return None


def _handle_filtered(user, message: str) -> Answer:
    """'Show my open complaints', 'my closed ones', 'anything pending?'."""
    complaints = _own_complaints(user)
    if not complaints:
        return _no_complaints_answer(user)

    lowered = message.lower()
    wanted = _requested_status(message)

    if re.search(r"\b(open|active|ongoing|pending|unresolved|still)\b", lowered) and (
        wanted in (None, Status.PENDING)
    ):
        selected = [c for c in complaints if c.is_open]
        description = "open"
    elif wanted == Status.RESOLVED:
        # "Resolved" is a moment, not a resting state -- it becomes Student
        # Verification immediately. Answer what they meant.
        selected = [
            c
            for c in complaints
            if c.status in (Status.STUDENT_VERIFICATION, Status.CLOSED)
        ]
        description = "resolved or closed"
    elif wanted:
        selected = [c for c in complaints if c.status == wanted]
        description = STATUS_LABELS.get(wanted, wanted).lower()
    else:
        return _handle_my_complaints(user, message)

    if not selected:
        return Answer(
            f"None of your complaints are **{description}** right now.",
            suggestions=["Show my complaints", "Anything overdue?"],
            links=[("My complaints", url_for("complaints.index"))],
            intent="filtered_none",
        )

    lines = [
        f"You have **{len(selected)}** {description} complaint(s):",
        *_bullet_list(selected),
    ]
    return Answer(
        "\n".join(lines),
        suggestions=["Show my complaints", "Anything overdue?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="filtered",
    )


def _handle_counts(user, message: str) -> Answer:
    """'How many complaints do I have?' answered from the real rows."""
    complaints = _own_complaints(user)
    if not complaints:
        return _no_complaints_answer(user, intent="counts_empty")

    tally: dict[str, int] = {}
    for complaint in complaints:
        tally[complaint.status] = tally.get(complaint.status, 0) + 1

    open_count = len([c for c in complaints if c.is_open])
    overdue = len([c for c in complaints if c.is_overdue])

    lines = [
        (
            f"**{len(complaints)}** in total — **{open_count}** open, "
            f"**{len(complaints) - open_count}** closed."
        )
    ]
    for status in Status.ALL:
        if tally.get(status):
            lines.append(f"• {STATUS_LABELS[status]}: **{tally[status]}**")
    if overdue:
        lines.append(f"\n**{overdue}** {'is' if overdue == 1 else 'are'} past deadline.")

    return Answer(
        "\n".join(lines),
        suggestions=["Show my complaints", "Anything overdue?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="counts",
    )


def _handle_latest(user, message: str) -> Answer:
    """The newest complaint, in full."""
    complaints = _own_complaints(user)
    if not complaints:
        return _no_complaints_answer(user, intent="latest_none")

    newest = complaints[0]
    text = (
        f"Your most recent one is **{newest.code}** — “{newest.title}”, filed "
        f"{_ago(newest.created_at)}.\n\n"
        + _status_sentence(newest)
        + _deadline_sentence(newest)
    )
    return Answer(
        text,
        suggestions=[f"History of {newest.code}", "Show my complaints"],
        links=[_complaint_link(newest)],
        intent="latest",
    )


def _handle_timeline(user, message: str) -> Answer:
    """The audit trail for one complaint: every step, who did it, when."""
    complaint = _find_by_code(user, message)
    # Naming a complaint we cannot show them is answered honestly, rather
    # than by quietly falling back to a different one of theirs.
    if complaint is None and _CODE.search(message):
        return _handle_specific_complaint(user, message)

    if complaint is None:
        open_ones = [c for c in _own_complaints(user) if c.is_open]
        complaint = open_ones[0] if open_ones else None

    if complaint is None:
        return _no_complaints_answer(user, intent="timeline_none")

    lines = [f"Everything that has happened to **{complaint.code}**:"]
    for event in complaint.events:
        label = STATUS_LABELS.get(event.status, event.status.replace("_", " ").title())
        who = f" by {event.actor.name}" if event.actor else ""
        lines.append(f"• **{label}** — {_ago(event.created_at)}{who}")
        if event.note:
            lines.append(f"  {event.note}")

    if len(lines) == 1:
        lines.append("• Submitted — no further activity recorded yet.")

    lines.append(
        "\nThat history is append-only: nothing can be edited or removed after "
        "the fact, which is what makes it worth trusting."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["Who is handling it?", "Why is it taking so long?"],
        links=[_complaint_link(complaint)],
        intent="timeline",
    )


def _handle_overdue(user, message: str) -> Answer:
    overdue = [c for c in _own_complaints(user) if c.is_overdue]

    if not overdue:
        return Answer(
            "Nothing of yours is past its deadline. Every open complaint is "
            "still within the time its category allows.",
            suggestions=["Show my complaints", "When will it be fixed?"],
            intent="overdue_none",
        )

    lines = [
        (
            f"**{len(overdue)}** of your complaints "
            f"{'is' if len(overdue) == 1 else 'are'} overdue:"
        )
    ]
    for complaint in overdue[:5]:
        hours = abs(complaint.hours_remaining or 0)
        lines.append(f"• **{complaint.code}** — {complaint.title} — {hours:.0f}h late")

    lines.append(
        "\nOverdue complaints are flagged to administrators automatically, so "
        "someone can see they have slipped."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["How do I escalate?", "Who is handling it?"],
        links=[_complaint_link(overdue[0])],
        intent="overdue",
    )


def _handle_who_is_handling(user, message: str) -> Answer:
    complaint = _find_by_code(user, message)
    # Naming a complaint we cannot show them is answered honestly, rather
    # than by quietly falling back to a different one of theirs.
    if complaint is None and _CODE.search(message):
        return _handle_specific_complaint(user, message)

    candidates = (
        [complaint] if complaint else [c for c in _own_complaints(user) if c.is_open]
    )

    if not candidates:
        return Answer(
            "You have no open complaints, so nothing is assigned at the moment.",
            suggestions=["Show my complaints"],
            intent="assignee_none",
        )

    lines = []
    for item in candidates[:5]:
        if item.assigned_staff:
            who = item.assigned_staff.name
            dept = item.assigned_staff.department
            role = item.assigned_staff.designation
            detail = ", ".join(part for part in (role, dept) if part)
            lines.append(f"• **{item.code}** — {who}" + (f" ({detail})" if detail else ""))
        else:
            lines.append(
                f"• **{item.code}** — not assigned yet; an administrator reviews "
                "new complaints and passes them to the right staff member"
            )

    return Answer(
        "Here is who is handling your open complaints:\n" + "\n".join(lines),
        suggestions=["Why is it taking so long?", "How do I contact them?"],
        intent="assignee",
    )


def _handle_deadline(user, message: str) -> Answer:
    """'When will it be fixed?' -- the actual due date, not a guess."""
    complaint = _find_by_code(user, message)
    # Naming a complaint we cannot show them is answered honestly, rather
    # than by quietly falling back to a different one of theirs.
    if complaint is None and _CODE.search(message):
        return _handle_specific_complaint(user, message)

    open_ones = [c for c in _own_complaints(user) if c.is_open]
    target = complaint or (open_ones[0] if open_ones else None)

    rule = (
        "Each category carries its own target time, and priority scales it: "
        "**Urgent** gets a quarter of the normal window, **High** a half, "
        "**Low** half again as long."
    )

    if target is None:
        return Answer(
            "You have no open complaints, so there is no deadline running.\n\n" + rule,
            suggestions=["What categories are there?", "How do I file a complaint?"],
            intent="deadline_none",
        )

    if not target.due_at:
        return Answer(
            f"{target.code} has no deadline recorded.\n\n" + rule,
            links=[_complaint_link(target)],
            intent="deadline",
        )

    text = (
        f"**{target.code}** is due **{_as_utc(target.due_at).strftime('%d %b %Y, %H:%M')}** "
        f"(UTC)."
        + _deadline_sentence(target)
        + "\n\n"
        + rule
        + "\n\nA deadline is a target, not a promise — but missing one is "
        "recorded and visible to administrators."
    )
    return Answer(
        text,
        suggestions=["Why is it taking so long?", "How do I escalate?"],
        links=[_complaint_link(target)],
        intent="deadline",
    )


def _handle_why_slow(user, message: str) -> Answer:
    complaint = _find_by_code(user, message)
    # Naming a complaint we cannot show them is answered honestly, rather
    # than by quietly falling back to a different one of theirs.
    if complaint is None and _CODE.search(message):
        return _handle_specific_complaint(user, message)

    open_ones = [c for c in _own_complaints(user) if c.is_open]
    target = complaint or (open_ones[0] if open_ones else None)

    base = (
        "Every category has a target time. Urgent problems get a quarter of the "
        "normal window, low-priority ones get half again as long. When a "
        "complaint passes its deadline it is flagged to administrators "
        "automatically."
    )

    if target is None:
        return Answer(base, suggestions=["Show my complaints"], intent="why_slow")

    text = _status_sentence(target) + _deadline_sentence(target) + "\n\n" + base
    if target.status == Status.PENDING:
        text += (
            "\n\nYours is still waiting to be assigned, which is the step an "
            "administrator does."
        )
    return Answer(
        text,
        suggestions=["How do I escalate?", "Who is handling it?"],
        links=[_complaint_link(target)],
        intent="why_slow",
    )


def _handle_escalate(user, message: str) -> Answer:
    """What to do when a complaint has stalled."""
    overdue = [c for c in _own_complaints(user) if c.is_overdue]

    text = (
        "Escalation is automatic. When a complaint passes its deadline the "
        "system flags it, notifies every administrator and the assigned staff "
        "member, and stamps the breach on the record so it cannot quietly "
        "disappear.\n\n"
        "What you can do on top of that:\n"
        "• Add a comment when you reopen, so the staff member knows exactly "
        "what is still wrong\n"
        "• Raise the priority *only* if the situation genuinely changed — a "
        "hazard appeared, an exam is affected\n"
        "• Contact the administration office if nothing moves; they can "
        "reassign it to someone else"
    )

    if overdue:
        codes = ", ".join(c.code for c in overdue[:3])
        text = (
            f"**{codes}** {'has' if len(overdue) == 1 else 'have'} already "
            "breached the deadline and been flagged to administrators.\n\n" + text
        )
        return Answer(
            text,
            suggestions=["Who is handling it?"],
            links=[_complaint_link(overdue[0])],
            intent="escalate",
        )

    return Answer(text, suggestions=["Anything overdue?"], intent="escalate")


def _handle_status_meaning(user, message: str) -> Answer:
    """The status glossary -- one status, or all of them."""
    explanations = {
        Status.PENDING: (
            "filed and waiting for an administrator to review it and pick the "
            "right staff member"
        ),
        Status.ASSIGNED: (
            "a named staff member now owns it, but has not started the work yet"
        ),
        Status.IN_PROGRESS: "the staff member has started work on it",
        Status.RESOLVED: (
            "the staff member says the work is done — it moves straight to "
            "Student Verification, so you will rarely see it resting here"
        ),
        Status.STUDENT_VERIFICATION: (
            "**your turn.** The work is claimed done and you decide whether it "
            "really is"
        ),
        Status.CLOSED: "you confirmed the fix, and the complaint is finished",
        Status.REOPENED: (
            "you said it was not actually fixed, so it went back to the staff "
            "member"
        ),
    }

    wanted = _requested_status(message)
    if wanted and wanted in explanations:
        label = STATUS_LABELS[wanted]
        return Answer(
            f"**{label}** means {explanations[wanted]}.",
            suggestions=["What are all the statuses?", "What happens after I submit?"],
            intent="status_meaning",
        )

    lines = ["A complaint moves through these states, in this order:"]
    for status in (
        Status.PENDING,
        Status.ASSIGNED,
        Status.IN_PROGRESS,
        Status.RESOLVED,
        Status.STUDENT_VERIFICATION,
        Status.CLOSED,
    ):
        lines.append(f"• **{STATUS_LABELS[status]}** — {explanations[status]}")
    lines.append(
        f"• **{STATUS_LABELS[Status.REOPENED]}** — {explanations[Status.REOPENED]}"
    )
    lines.append(
        "\nThe complaint page shows exactly which step yours has reached, with "
        "the date of each one."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["What happens after I submit?", "Show my complaints"],
        intent="status_meaning",
    )


# --------------------------------------------------------------------------
# Handlers: filing a complaint
# --------------------------------------------------------------------------


def _handle_how_to_file(user, message: str) -> Answer:
    limit = current_app.config.get("MAX_FILES_PER_COMPLAINT", 5)
    return Answer(
        "Filing a complaint takes about a minute:\n"
        "1. Open **New Complaint**\n"
        "2. Give it a short title and describe what is wrong, where, and since when\n"
        "3. Pick the category and the location\n"
        "4. Set a priority — be honest; *Urgent* is for anything unsafe\n"
        f"5. **Attach photos** (up to {limit}). Evidence is the single biggest "
        "thing that gets a complaint fixed quickly\n"
        "6. Submit\n\n"
        "You get a reference like CC102 straight away, and you can follow every "
        "step from your dashboard.",
        suggestions=[
            "What should I write?",
            "How do I attach photos?",
            "What categories are there?",
        ],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="how_to_file",
    )


def _handle_what_to_write(user, message: str) -> Answer:
    return Answer(
        "A complaint that gets fixed quickly answers four questions:\n"
        "• **What** is wrong — “the ceiling fan grinds and wobbles at every speed”\n"
        "• **Where** exactly — room number, floor, which wall or which of the "
        "three taps\n"
        "• **Since when** — “since Monday” tells staff whether it is sudden\n"
        "• **What it stops you doing** — “the lab cannot run practicals” is why "
        "priority exists\n\n"
        "Two things to avoid: a title like “problem” that says nothing, and "
        "several unrelated faults in one complaint — file those separately so "
        "each can be tracked and closed on its own.\n\n"
        "Then attach a photo. One clear photo saves a round trip of questions.",
        suggestions=["How do I file a complaint?", "How do I attach photos?"],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="what_to_write",
    )


def _handle_categories(user, message: str) -> Answer:
    categories = _categories()

    if not categories:
        return Answer(
            "No categories are set up yet. An administrator needs to add them.",
            intent="categories_empty",
        )

    lines = ["You can file a complaint under any of these:"]
    for category in categories:
        lines.append(
            f"• **{category.name}** — {category.description} "
            f"({category.sla_hours}h target)"
        )

    lines.append(
        "\nPick the closest match. If you are unsure the form suggests one from "
        "your wording, and an administrator can change it later."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["Which category should I pick?", "How do I file a complaint?"],
        intent="categories",
    )


def _handle_which_category(user, message: str) -> Answer:
    """Run the real suggestion engine over whatever they described."""
    proposed = suggestion_engine.suggest_category(message, "")
    priority = suggestion_engine.suggest_priority(message, "")

    if not proposed.is_useful:
        return Answer(
            "Tell me what is actually wrong and I will suggest one — for "
            "example “the tap in the washroom is leaking” or “the projector "
            "will not switch on”.\n\n"
            "If nothing fits neatly, pick the closest category and describe it "
            "clearly. An administrator can move it, and moving a complaint "
            "costs nothing.",
            suggestions=["What categories are there?", "How do I file a complaint?"],
            links=[("See the categories", url_for("complaints.new"))],
            intent="which_category_unclear",
        )

    text = (
        f"From your wording that looks like **{proposed.value}** "
        f"({proposed.reason})."
    )
    if priority.value and priority.value != Priority.MEDIUM:
        text += f"\n\nPriority-wise it reads as **{priority.value.title()}**"
        text += f" ({priority.reason})." if priority.matched else "."

    text += (
        "\n\nThat is a suggestion, not a decision — the form offers the same "
        "hint and you are free to pick something else. Nothing is ever "
        "categorised automatically."
    )
    return Answer(
        text,
        suggestions=["What categories are there?", "How do I file a complaint?"],
        links=[("File it now", url_for("complaints.new"))],
        intent="which_category",
    )


def _handle_locations(user, message: str) -> Answer:
    locations = _locations()
    if not locations:
        return Answer(
            "No locations are set up yet. An administrator needs to add them.",
            intent="locations_empty",
        )

    lines = ["These are the locations you can choose from:"]
    for location in locations[:20]:
        building = f" ({location.building})" if location.building else ""
        lines.append(f"• **{location.name}**{building}")
    if len(locations) > 20:
        lines.append(f"…and {len(locations) - 20} more on the form.")

    lines.append(
        "\nIf your exact room is not listed, choose the nearest one and put the "
        "precise place in the description — “second floor, left washroom”. An "
        "administrator can add new locations."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["How do I file a complaint?"],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="locations",
    )


def _handle_priority(user, message: str) -> Answer:
    return Answer(
        "Priority decides how much time the college gives itself to fix it:\n"
        "• **Urgent** — someone could be hurt. Sparking wires, exposed cables, "
        "flooding, anything structural. Gets a quarter of the normal window\n"
        "• **High** — unusable and blocking work. No power in a lab, no water, "
        "a projector dead before an exam. Half the window\n"
        "• **Medium** — the normal case. Something is broken and should be "
        "fixed, but you can work around it\n"
        "• **Low** — cosmetic or a request. Repainting, a wobbly chair. Half "
        "again as long\n\n"
        "Be honest. Marking everything Urgent does not make anything faster — "
        "it just makes the genuinely dangerous ones harder to see. If there is "
        "real danger right now, tell campus security first, then file it.",
        suggestions=["Which category should I pick?", "How do I file a complaint?"],
        intent="priority",
    )


def _handle_edit_or_delete(user, message: str) -> Answer:
    return Answer(
        "A submitted complaint cannot be edited or deleted, and that is "
        "deliberate — the record is what staff and administrators act on, so it "
        "has to be stable.\n\n"
        "What you *can* do:\n"
        "• **Remove a photo** while the complaint is still *Pending*, from its "
        "details page\n"
        "• **Add the correction as a note** when the complaint reaches you for "
        "verification\n"
        "• **Ask an administrator** to change the category, priority or "
        "assignee — they can, and it is recorded on the timeline\n"
        "• **File a fresh complaint** and mention the old reference if the "
        "first one was about the wrong thing entirely\n\n"
        "If you filed something by mistake, say so in the administration "
        "office; a complaint can be closed without work being done.",
        suggestions=["Show my complaints", "How do I contact an administrator?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="edit_or_delete",
    )


def _handle_duplicate(user, message: str) -> Answer:
    return Answer(
        "File it anyway. A duplicate costs an administrator ten seconds to "
        "spot, and a problem nobody reports because everyone assumed somebody "
        "else had is much more expensive.\n\n"
        "Two things help:\n"
        "• Mention in the description that others have reported it too — "
        "several reports of the same fault is useful evidence of how widespread "
        "it is\n"
        "• Check **My Complaints** first, in case the one you are thinking of "
        "is your own and already in progress",
        suggestions=["Show my complaints", "How do I file a complaint?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="duplicate",
    )


# --------------------------------------------------------------------------
# Handlers: photos and evidence
# --------------------------------------------------------------------------


def _handle_photo_help(user, message: str) -> Answer:
    limit = current_app.config.get("MAX_FILES_PER_COMPLAINT", 5)
    size = current_app.config.get("MAX_FILE_SIZE_MB", 5)
    return Answer(
        "On the complaint form, use **+ Choose Photos** or drag images onto the "
        "upload box.\n\n"
        f"• Up to **{limit} photos**, **{size} MB** each\n"
        "• JPG, JPEG, PNG or WEBP\n"
        "• You see a preview before submitting, and can remove any photo with "
        "the ✕ on its corner\n"
        "• While a complaint is still *Pending* you can remove a photo from its "
        "details page too\n\n"
        "Your photos are private. Only you, the staff member assigned to the "
        "complaint, and administrators can open them — they are served through "
        "a permission check every single time, never from a public URL.",
        suggestions=["My photo will not upload", "Can I add photos later?"],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="photo_help",
    )


def _handle_photo_rejected(user, message: str) -> Answer:
    limit = current_app.config.get("MAX_FILES_PER_COMPLAINT", 5)
    size = current_app.config.get("MAX_FILE_SIZE_MB", 5)
    return Answer(
        "An upload is refused for one of four reasons, and the message on "
        "screen names which:\n"
        f"• **Too large** — each photo must be under {size} MB. Most phones "
        "have a “resize when sharing” option\n"
        f"• **Too many** — at most {limit} per complaint\n"
        "• **Wrong type** — only JPG, JPEG, PNG and WEBP. A screenshot saved "
        "as PDF or HEIC will be refused; re-save it as JPG\n"
        "• **Not really an image** — the file is checked by reading its actual "
        "contents, not just its name, so a renamed document is caught\n\n"
        "If one photo in a batch fails, none of them are saved — fix that one "
        "and submit again, so you never end up with half your evidence "
        "attached.",
        suggestions=["How do I attach photos?", "How do I file a complaint?"],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="photo_rejected",
    )


def _handle_photo_later(user, message: str) -> Answer:
    return Answer(
        "Photos are attached when you file, and can be **removed** while the "
        "complaint is still *Pending*. There is no way to add more afterwards — "
        "once staff are working from the evidence, it stays fixed.\n\n"
        "If you have an important extra photo, the practical route is to show "
        "it to the assigned staff member, or mention it when the complaint "
        "comes back to you for verification.",
        suggestions=["How do I attach photos?", "Show my complaints"],
        intent="photo_later",
    )


def _handle_who_sees_photos(user, message: str) -> Answer:
    return Answer(
        "Three people, and nobody else:\n"
        "• **You**, because you filed it\n"
        "• **The staff member it is assigned to** — and only after it is "
        "assigned to them\n"
        "• **Administrators**, who see every complaint\n\n"
        "Photos are not public files. They have no shareable link: every image "
        "is fetched through a permission check, and the storage path never "
        "appears in a page, a URL or an error message. Camera metadata "
        "(including GPS location) is stripped from every photo before it is "
        "stored.",
        suggestions=["Is my complaint private?", "How do I attach photos?"],
        intent="who_sees_photos",
    )


# --------------------------------------------------------------------------
# Handlers: how the process works
# --------------------------------------------------------------------------


def _handle_lifecycle(user, message: str) -> Answer:
    steps = " → ".join(label for _key, label in TIMELINE_STEPS)
    return Answer(
        "Here is the whole journey:\n"
        f"**{steps}**\n\n"
        "1. **You submit** and get a reference number immediately\n"
        "2. An **administrator reviews** it and assigns the right staff member "
        "— that is the *Pending* wait\n"
        "3. The staff member **starts work** (*In Progress*)\n"
        "4. They **mark it resolved**, describe what they did, and upload a "
        "photo of the finished work\n"
        "5. It comes **back to you** to confirm — compare the before and after "
        "photos\n"
        "6. You say fixed and it **closes**, or not fixed and it **reopens** "
        "straight back to them\n\n"
        "You get a notification at every hand-off, and the whole history is on "
        "the complaint page.",
        suggestions=[
            "What does Pending mean?",
            "What happens when it is resolved?",
            "How do I file a complaint?",
        ],
        intent="lifecycle",
    )


def _handle_who_assigns(user, message: str) -> Answer:
    return Answer(
        "An **administrator** does. Complaints are not routed automatically:\n"
        "• A new complaint arrives as *Pending*\n"
        "• An administrator reads it and picks a staff member — by department "
        "and by what the complaint actually needs\n"
        "• The staff member gets an email with the complaint details **and your "
        "evidence photos attached**, plus an in-app notification\n\n"
        "Only authorised staff can receive an assignment. A staff account has "
        "to be approved by an administrator before it can see any complaint at "
        "all.\n\n"
        "The wording of a complaint suggests a category, but never sets one by "
        "itself — a person decides.",
        suggestions=["Who is handling my complaint?", "Why is it taking so long?"],
        intent="who_assigns",
    )


def _handle_verification(user, message: str) -> Answer:
    awaiting = [
        c for c in _own_complaints(user) if c.status == Status.STUDENT_VERIFICATION
    ]

    text = (
        "When a staff member finishes, they mark the complaint resolved and "
        "upload a photo of the completed work. It then waits for **you**.\n\n"
        "Open the complaint and compare the before and after photos:\n"
        "• If it is fixed, choose **Yes, problem is solved** and it closes\n"
        "• If it is not, choose **No** and say what is still wrong — it reopens "
        "and goes back to the staff member\n\n"
        "There is no limit on reopening. A complaint is not closed because "
        "somebody says it is; it is closed because *you* agree."
    )

    if awaiting:
        codes = ", ".join(c.code for c in awaiting)
        text = (
            f"**{codes}** {'is' if len(awaiting) == 1 else 'are'} waiting on you "
            "right now.\n\n" + text
        )
        return Answer(
            text,
            suggestions=["What if it is not fixed?"],
            links=[_complaint_link(awaiting[0])],
            intent="verification",
        )

    return Answer(text, suggestions=["Show my complaints"], intent="verification")


def _handle_notifications(user, message: str) -> Answer:
    from .notifications import unread_count

    unread = unread_count(user)
    opening = (
        f"You have **{unread}** unread notification(s). "
        if unread
        else "You have no unread notifications right now. "
    )

    return Answer(
        opening
        + "The bell in the top bar shows the count, and opening the list marks "
        "them read.\n\nYou are notified when:\n"
        "• Your complaint is assigned to a staff member\n"
        "• Work starts on it\n"
        "• It is marked resolved and needs your verification\n"
        "• It is reopened or closed\n"
        "• It passes its deadline and gets escalated",
        suggestions=["Will I get an email?", "Show my complaints"],
        links=[("Open notifications", url_for("main.notification_list"))],
        intent="notifications",
    )


def _handle_email(user, message: str) -> Answer:
    configured = bool(current_app.config.get("SMTP_HOST"))
    text = (
        "Email goes out at the points that matter: when your complaint is "
        "assigned, when it is marked resolved and needs your verification, and "
        "when it is reopened. The staff member's assignment email carries your "
        "evidence photos as attachments.\n\n"
        "In-app notifications are separate and always work — the bell in the "
        "top bar never depends on mail being configured."
    )
    if not configured:
        text += (
            "\n\n**On this installation no mail server is configured**, so "
            "emails are recorded but not delivered. Use the bell for updates; "
            "an administrator can see the log of what would have been sent."
        )
    text += "\n\nCampusCare never puts a password in an email."
    return Answer(
        text,
        suggestions=["Where are my notifications?", "Show my complaints"],
        links=[("Open notifications", url_for("main.notification_list"))],
        intent="email",
    )


# --------------------------------------------------------------------------
# Handlers: account, privacy, safety
# --------------------------------------------------------------------------


def _handle_account(user, message: str) -> Answer:
    return Answer(
        "For anything to do with your account:\n"
        "• Your name and email are shown in the menu at the top right\n"
        "• Signing out is in that same menu\n"
        "• If you cannot sign in, contact the administration office — they can "
        "reset an account\n\n"
        f"{_app_name()} will never ask for your password by email or message, "
        "and your password is stored only as a salted hash — nobody, including "
        "an administrator, can read it back.",
        suggestions=["What are my details?", "Is my data safe?"],
        intent="account",
    )


def _handle_profile(user, message: str) -> Answer:
    """Read back the signed-in user's own record."""
    role = _role_of(user)
    lines = [
        f"• **Name** — {user.name}",
        f"• **Email** — {user.email}",
        f"• **Role** — {role.title()}",
    ]
    if getattr(user, "roll_no", None):
        lines.append(f"• **Roll number** — {user.roll_no}")
    if getattr(user, "department", None):
        lines.append(f"• **Department** — {user.department}")
    if role != Role.STUDENT:
        if getattr(user, "designation", None):
            lines.append(f"• **Designation** — {user.designation}")
        if getattr(user, "staff_id", None):
            lines.append(f"• **Staff ID** — {user.staff_id}")
        lines.append(
            f"• **Authorisation** — "
            f"{getattr(user, 'authorization_status', '').title()}"
        )

    return Answer(
        "This is what your account holds:\n"
        + "\n".join(lines)
        + "\n\nTo correct any of it, contact the administration office — "
        "profile fields are changed by an administrator so that the record "
        "stays trustworthy.",
        suggestions=["Show my complaints", "Is my data safe?"],
        intent="profile",
    )


def _handle_two_factor(user, message: str) -> Answer:
    enabled = current_app.config.get("TWO_FACTOR_ENABLED", False)
    state = (
        "Two-step verification is **on** for this installation, so signing in "
        "sends a one-time code to your email."
        if enabled
        else "Two-step verification is **off** on this installation, so signing "
        "in needs only your password."
    )
    return Answer(
        state
        + "\n\nWhen it is on:\n"
        "• The code is six digits and expires after a few minutes\n"
        "• It works once. A new code cancels the previous one\n"
        "• Too many wrong attempts and the code is discarded — request a fresh "
        "one\n"
        "• There is a short cooldown before you can resend\n\n"
        "If the code has not arrived, check the spam folder and confirm your "
        "email address with the administration office. The code is stored only "
        "as a hash, so nobody can look yours up — they can only send a new one.",
        suggestions=["I forgot my password", "Is my data safe?"],
        intent="two_factor",
    )


def _handle_register(user, message: str) -> Answer:
    return Answer(
        "Registration is on the sign-in page, under **Register**.\n\n"
        "• **Students** choose Student, give their name, college email, roll "
        "number and department, and can sign in immediately\n"
        "• **Staff** choose Staff and additionally give a designation "
        f"({', '.join(DESIGNATIONS[:3])} and others), department, phone and "
        "staff ID. A staff account starts **Pending** and cannot see anything "
        "until an administrator authorises it\n\n"
        "Nobody can register as an administrator. That role is created "
        "directly by whoever runs the installation — it is not offered as a "
        "choice on the form, and the role you send from the browser is never "
        "trusted.",
        suggestions=["My staff account is pending", "I forgot my password"],
        intent="register",
    )


def _handle_staff_authorization(user, message: str) -> Answer:
    status = getattr(user, "authorization_status", None)
    text = (
        "Every staff account is vetted before it can be used:\n"
        "• A new registration sits at **Pending** — it can sign in, but sees no "
        "complaints\n"
        "• An administrator reviews the name, designation, department and staff "
        "ID and either **authorises** or **rejects** it\n"
        "• Authorisation is granted once and stored permanently. Signing in "
        "never resets it\n"
        "• An administrator can **suspend** an account later; only an "
        "administrator can lift that\n\n"
        "Only an authorised staff account can be assigned a complaint, which is "
        "what stops an unvetted account from ever seeing a student's photos."
    )
    if status and _role_of(user) != Role.STUDENT:
        text = f"Your account is currently **{status.title()}**.\n\n" + text
    return Answer(
        text,
        suggestions=["How do I contact an administrator?"],
        intent="staff_authorization",
    )


def _handle_privacy(user, message: str) -> Answer:
    return Answer(
        "Complaints are not anonymous, and cannot be. Your name is on the "
        "complaint because the person fixing it usually needs to reach you, and "
        "because you are the one who confirms it was actually fixed.\n\n"
        "But it is not public either. A complaint is visible to exactly three "
        "parties: **you**, **the staff member it is assigned to**, and "
        "**administrators**. No other student can see it, search it, or find "
        "out that it exists — not even if they guess the reference number.\n\n"
        "If something genuinely needs to be raised anonymously, that is a "
        "conversation for the administration office rather than this system.",
        suggestions=["Who can see my photos?", "Is my data safe?"],
        intent="privacy",
    )


def _handle_security(user, message: str) -> Answer:
    return Answer(
        "The short version of how your data is protected:\n"
        "• **Passwords** are stored as salted hashes, never as text. Nobody can "
        "read yours back\n"
        "• **Photos** are validated four ways before anything is written to "
        "disk — size, extension, declared type, and the file's actual contents\n"
        "• **Camera metadata**, including GPS coordinates, is stripped from "
        "every photo\n"
        "• **Images have no public URL.** Each one is served through a "
        "permission check, and the stored path never appears anywhere\n"
        "• **Every form** is protected against cross-site request forgery\n"
        "• **Roles are enforced on the server.** What your browser claims about "
        "who you are is never trusted\n\n"
        "This chat is not saved anywhere — closing the tab discards it.",
        suggestions=["Is my complaint private?", "Who can see my photos?"],
        intent="security",
    )


def _handle_emergency(user, message: str) -> Answer:
    """Anything that sounds dangerous is answered before everything else."""
    return Answer(
        "**If someone is in danger right now, do not use this form first.**\n\n"
        "• Get people away from the hazard\n"
        "• Tell campus security or the department office in person or by phone "
        "— they can cut power, shut a valve, or evacuate a room in minutes\n"
        "• Call the emergency services if anyone is hurt\n\n"
        "Then file it here as **Urgent**, with a photo if it is safe to take "
        "one. The written record matters — it is what gets the permanent repair "
        "scheduled and what proves the hazard was reported — but it is not a "
        "substitute for telling a person immediately.",
        suggestions=["How do I file a complaint?", "What does Urgent mean?"],
        links=[("File it as urgent", url_for("complaints.new"))],
        intent="emergency",
    )


def _handle_contact(user, message: str) -> Answer:
    return Answer(
        "For a person rather than this assistant:\n"
        "• **The administration office** sees every complaint and can reassign, "
        "reprioritise or close one. This is the right place for anything that "
        "has stalled\n"
        "• **The assigned staff member** is named on the complaint page once it "
        "is assigned, with their department\n"
        "• **Campus security** for anything unsafe — immediately, and before "
        "filing\n\n"
        "This assistant does not send messages on your behalf, and it cannot "
        "see complaints other than your own — so if you need something escalated "
        "to a person, use those routes.",
        suggestions=["How do I escalate?", "Who is handling my complaint?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="contact",
    )


# --------------------------------------------------------------------------
# Handlers: for staff
# --------------------------------------------------------------------------


def _handle_staff_resolve(user, message: str) -> Answer:
    return Answer(
        "Working through an assigned complaint:\n"
        "1. Open it from **Assigned to Me**\n"
        "2. Press **Start Work** — the student is notified it has begun\n"
        "3. When the job is done, press **Mark Resolved**\n"
        "4. Describe what you actually did (at least 10 characters — “fixed” is "
        "not enough for the student to verify against)\n"
        "5. **Attach a photo of the finished work.** It sits beside the "
        "student's original photo as a before-and-after\n\n"
        "It then goes to the student to confirm. If they say it is not fixed, "
        "it comes straight back to you as *In Progress* with their reason "
        "attached. You cannot close a complaint yourself — only the student "
        "who filed it can, which is the point.",
        suggestions=["What is assigned to me?", "What does In Progress mean?"],
        links=[("Assigned to me", url_for("complaints.index"))],
        intent="staff_resolve",
    )


# --------------------------------------------------------------------------
# Handlers: about, and conversational
# --------------------------------------------------------------------------


def _handle_about(user, message: str) -> Answer:
    name = _app_name()
    return Answer(
        f"**{name}** is the college's complaint and maintenance system. "
        "Instead of a problem being mentioned to somebody who might pass it on, "
        "it becomes a tracked record with a reference number, an owner, a "
        "deadline, photographic evidence, and a student who has to agree it was "
        "actually fixed before it can be closed.\n\n"
        "Three roles use it: **students** file and verify, **staff** carry out "
        "the work and prove it with a photo, **administrators** assign, monitor "
        "deadlines and see the analytics.",
        suggestions=["What happens after I submit?", "How do I file a complaint?"],
        intent="about",
    )


def _handle_open_source(user, message: str) -> Answer:
    return Answer(
        f"**{_app_name()}** is open source under the MIT licence — the source "
        "is on GitHub, and anyone may read, run, modify or deploy it.\n\n"
        "It is built entirely on open technologies:\n"
        "• **Flask** and **Jinja2** (Python) on the server\n"
        "• **MySQL** or **MariaDB** for data, **SQLAlchemy** to reach it\n"
        "• **Bootstrap 5** and plain JavaScript in the browser, both vendored "
        "so the whole application runs with no internet connection\n"
        "• **Pillow** for image validation, **pytest** for the test suite\n\n"
        "Even this assistant is open: it is keyword matching over your own "
        "database rows, not a language model, so it needs no API key, costs "
        "nothing to run, and every answer can be traced to a line of code.",
        suggestions=["What is CampusCare?", "Is my data safe?"],
        intent="open_source",
    )


def _handle_identity(user, message: str) -> Answer:
    return Answer(
        f"I am the {_app_name()} assistant — a small piece of this application, "
        "not a general chatbot and not a person.\n\n"
        "I look things up in your own complaints and explain how the system "
        "works. I do not guess: if I cannot find something, I say so, because a "
        "made-up status is worse than no answer. I can only see complaints you "
        "filed or that are assigned to you, and I cannot change anything, "
        "message anyone, or close a complaint for you.\n\n"
        "This conversation is not saved.",
        suggestions=["What can I ask you?", "Show my complaints"],
        intent="identity",
    )


def _handle_greeting(user, message: str) -> Answer:
    name = _first_name(user)
    greeting = f"Hello {name}. " if name else "Hello. "

    open_count = len([c for c in _own_complaints(user) if c.is_open])
    if open_count:
        greeting += f"You have **{open_count}** open complaint(s)."
    else:
        greeting += "You have no open complaints at the moment."

    return Answer(
        greeting + "\n\nI can look up where a complaint has got to, explain how "
        "to file one, or tell you what happens next. What do you need?",
        suggestions=[
            "Show my complaints",
            "How do I file a complaint?",
            "Anything overdue?",
            "What can I ask you?",
        ],
        intent="greeting",
    )


def _handle_thanks(user, message: str) -> Answer:
    return Answer(
        "Glad to help. Ask any time — I am here on every page.",
        suggestions=["Show my complaints", "What can I ask you?"],
        intent="thanks",
    )


def _handle_bye(user, message: str) -> Answer:
    return Answer(
        "Goodbye. Your complaints keep moving whether this chat is open or "
        "not, and the bell in the top bar will tell you when something changes.",
        intent="bye",
    )


def _handle_frustration(user, message: str) -> Answer:
    """Somebody is annoyed. Acknowledge it, then be useful."""
    overdue = [c for c in _own_complaints(user) if c.is_overdue]

    text = (
        "That is fair, and it is worth saying properly rather than being "
        "brushed off."
    )
    if overdue:
        codes = ", ".join(c.code for c in overdue[:3])
        text += (
            f" **{codes}** {'is' if len(overdue) == 1 else 'are'} genuinely past "
            "the deadline, and that has already been flagged to administrators "
            "automatically."
        )
    text += (
        "\n\nWhat actually moves things:\n"
        "• The administration office can reassign a stalled complaint to "
        "someone else\n"
        "• If it was marked fixed and is not, reopen it and say exactly what is "
        "still wrong — reopening is recorded and there is no limit on it\n"
        "• If there is a hazard, tell campus security rather than waiting on a "
        "ticket\n\n"
        "I can show you exactly where each of yours stands, if that helps."
    )
    return Answer(
        text,
        suggestions=["Show my complaints", "How do I escalate?", "Anything overdue?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="frustration",
    )


def _handle_help(user, message: str) -> Answer:
    groups = []
    for group in _visible_groups(user):
        titles = ", ".join(topic.title for topic in group["topics"][:4])
        groups.append(f"• **{group['name']}** — {titles}")

    return Answer(
        "I can help with:\n"
        + "\n".join(groups)
        + "\n\nAsk in your own words; you do not need special commands. If you "
        "want the full list of things I understand, say **what can I ask**.",
        suggestions=[
            "What can I ask you?",
            "Show my complaints",
            "How do I file a complaint?",
        ],
        intent="help",
    )


def _handle_catalogue(user, message: str) -> Answer:
    """The full menu of questions, grouped."""
    lines = ["Everything I can answer, grouped. Any of these can be typed as-is:"]
    for group in _visible_groups(user):
        lines.append(f"\n**{group['name']}**")
        for topic in group["topics"]:
            lines.append(f"• {topic.examples[0]}")

    lines.append(
        "\nYou do not have to use this wording — I match on meaning, and if I "
        "am unsure I will say so rather than guess."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["Show my complaints", "How do I file a complaint?"],
        intent="catalogue",
    )


# --------------------------------------------------------------------------
# The catalogue: one row per capability
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    """One thing the assistant can answer.

    ``pattern`` recognises the question precisely; ``keywords`` are the softer
    signals used by the fallback when no pattern matched; ``examples`` are shown
    to the user, so the first one should read as something a student would
    actually type.
    """

    key: str
    group: str
    title: str
    examples: tuple[str, ...]
    keywords: tuple[str, ...]
    pattern: re.Pattern
    handler: Callable[[object, str], Answer]
    #: Which roles see this in the browsable menu. Empty means everyone.
    roles: tuple[str, ...] = ()
    #: False for conversational filler that would only clutter the menu.
    listed: bool = True

    def visible_to(self, user) -> bool:
        return self.listed and (not self.roles or _role_of(user) in self.roles)


def _p(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


TRACKING = "Tracking your complaints"
FILING = "Filing a complaint"
PHOTOS = "Photos and evidence"
PROCESS = "How the process works"
TIMING = "Timing and deadlines"
ACCOUNT = "Your account"
PRIVACY = "Privacy and safety"
STAFF = "For staff"
ABOUT = "About CampusCare"

#: Ordered most specific first: the first pattern that matches wins. Emergencies
#: are checked before anything else; the broad "my complaints" catch-all sits
#: near the end so it never steals a more precise question.
TOPICS: tuple[Topic, ...] = (
    Topic(
        key="emergency",
        group=PRIVACY,
        title="Something dangerous",
        examples=(
            "There is a live wire hanging in the corridor",
            "What do I do in an emergency?",
        ),
        keywords=(
            "fire", "smoke", "sparking", "spark", "electric shock", "shock",
            "gas leak", "collapse", "flooding", "injured", "injury", "bleeding",
            "danger", "dangerous", "emergency", "unsafe", "hazard",
            "electrocuted", "live wire", "short circuit",
        ),
        pattern=_p(
            r"\b(fire|smoke|sparking|sparks?|electric(al)? shock|electrocut\w+|"
            r"gas leak|live wire|short circuit|collaps\w+|flood\w*|emergency|"
            r"someone (is )?(hurt|injured|bleeding)|life threatening|"
            r"dangerous|unsafe|hazard(ous)?)\b"
        ),
        handler=_handle_emergency,
    ),
    Topic(
        key="catalogue",
        group=ABOUT,
        title="Everything you can ask",
        examples=("What can I ask you?", "Show me the list of questions"),
        keywords=(
            "what can i ask", "list of questions", "example questions", "topics",
            "all questions", "what do you know",
        ),
        pattern=_p(
            r"\b(what can i ask|list of questions|example questions|"
            r"(all|the) (questions|topics)|what (questions|things) can\b|"
            r"show me (the )?(questions|topics)|what do you know)\b"
        ),
        handler=_handle_catalogue,
    ),
    Topic(
        key="staff_resolve",
        group=STAFF,
        title="Resolving an assigned complaint",
        examples=("How do I resolve a complaint?", "How do I upload a repair photo?"),
        keywords=(
            "mark resolved", "resolve a complaint", "start work", "resolution photo",
            "repair photo", "finish the job", "complete the work",
        ),
        pattern=_p(
            r"\b(mark (it |this )?(as )?resolved|how do i resolve|start work|"
            r"(resolution|repair|after|completion) photo|proof of (repair|work)|"
            r"photo of the (repair|work|fix)|"
            r"finish the (job|work))\b"
        ),
        handler=_handle_staff_resolve,
        roles=(Role.STAFF, Role.ADMIN),
    ),
    Topic(
        key="staff_authorization",
        group=ACCOUNT,
        title="Staff account approval",
        examples=("My staff account is pending", "When will my account be authorised?"),
        keywords=(
            "authorisation", "authorization", "approved", "approval", "pending account",
            "staff account", "suspended", "rejected",
        ),
        pattern=_p(
            r"\b(authoris\w+|authoriz\w+|approv\w+|suspend\w+|rejected)\b"
            r"[^?]{0,30}\b(account|staff|registration)\b"
            r"|\b(staff|my) account\b[^?]{0,30}\b(pending|approv\w+|authoris\w+|"
            r"authoriz\w+|suspend\w+|rejected)\b"
        ),
        handler=_handle_staff_authorization,
    ),
    Topic(
        key="two_factor",
        group=ACCOUNT,
        title="Two-step verification",
        examples=("What is the OTP for?", "My verification code has not arrived"),
        keywords=(
            "otp", "one time password", "verification code", "two factor", "2fa",
            "code not received", "six digit",
        ),
        pattern=_p(
            r"\b(otp|one[- ]time (password|code)|verification code|two[- ]?factor|"
            r"2fa|two[- ]step|six[- ]digit code)\b"
        ),
        handler=_handle_two_factor,
    ),
    Topic(
        key="status_meaning",
        group=PROCESS,
        title="What a status means",
        examples=(
            "What does Pending mean?",
            "What are all the statuses?",
            "What does Student Verification mean?",
        ),
        keywords=(
            "status meaning", "statuses", "what does pending mean",
            "states", "glossary", "terminology", "what each status",
        ),
        pattern=_p(
            r"\b(what (does|do)|meaning of|explain|means?|difference between)\b"
            r"[^?]{0,40}\b(pending|assigned|in ?progress|student verification|"
            r"reopened|closed|resolved|status(es)?)\b"
            r"|\b(all|the|different|list of) statuses\b"
            r"|\bstatus(es)? mean\b"
        ),
        handler=_handle_status_meaning,
    ),
    Topic(
        key="lifecycle",
        group=PROCESS,
        title="What happens after you submit",
        examples=("What happens after I submit?", "How does the whole process work?"),
        keywords=(
            "what happens after", "process", "workflow", "steps", "stages",
            "how does it work", "journey", "lifecycle",
        ),
        pattern=_p(
            r"\b(what happens (after|once|when i)|the (whole )?process|workflow|"
            r"what are the steps|how does (this|it|the system) work|life ?cycle)\b"
        ),
        handler=_handle_lifecycle,
    ),
    Topic(
        key="verification",
        group=PROCESS,
        title="Confirming a repair",
        examples=(
            "What happens when it is resolved?",
            "It is still broken, what do I do?",
            "How do I reopen a complaint?",
        ),
        keywords=(
            "verify", "verification", "confirm", "not fixed", "still broken",
            "reopen", "approve", "before and after", "close it", "fixed",
            "says it is fixed", "says its fixed", "not actually fixed",
            "marked it done", "claims it is fixed",
        ),
        pattern=_p(
            r"\b(verify|verification|confirm|not fixed|still broken|reopen|"
            r"resolved|approve the (fix|repair)|what happens (next|after))\b"
        ),
        handler=_handle_verification,
    ),
    Topic(
        key="overdue",
        group=TIMING,
        title="Overdue complaints",
        examples=("Is anything overdue?", "Has my complaint missed its deadline?"),
        keywords=("overdue", "late", "past deadline", "missed", "delayed", "breached"),
        pattern=_p(r"\b(overdue|late|past (the )?deadline|missed|delayed|breach\w*)\b"),
        handler=_handle_overdue,
    ),
    Topic(
        key="escalate",
        group=TIMING,
        title="Escalating a stalled complaint",
        examples=("How do I escalate?", "Nobody is doing anything about it"),
        keywords=(
            "escalate", "escalation", "higher authority", "nobody is doing",
            "no action", "ignored", "stuck", "chase",
        ),
        pattern=_p(
            r"\b(escalat\w+|higher (up|authority)|no(body|-one| one) (is )?"
            r"(doing|taking|bothering)|no action|being ignored|chase (it|this) up)\b"
        ),
        handler=_handle_escalate,
    ),
    Topic(
        key="assignee",
        group=TRACKING,
        title="Who is handling it",
        examples=("Who is handling my complaint?", "Which staff member has it?"),
        keywords=(
            "who is handling", "assigned to", "which staff", "who will fix",
            "responsible", "technician", "worker",
        ),
        pattern=_p(
            r"\b(who('s| is)? (handling|working|assigned|fixing|responsible|looking)|"
            r"which staff|assigned to whom|who will fix|who has (it|my complaint))\b"
        ),
        handler=_handle_who_is_handling,
    ),
    Topic(
        key="who_assigns",
        group=PROCESS,
        title="How complaints get assigned",
        examples=("Who assigns complaints?", "How is my complaint routed?"),
        keywords=(
            "who assigns", "how is it assigned", "routing", "allocation",
            "automatic", "who decides",
        ),
        pattern=_p(
            r"\b(who (assigns|allocates|decides|routes)|how (is|are|does) "
            r"(it|they|my complaint|complaints) (get )?(assign|allocat|rout)\w*)\b"
        ),
        handler=_handle_who_assigns,
    ),
    Topic(
        key="deadline",
        group=TIMING,
        title="When it will be fixed",
        examples=("When will my complaint be fixed?", "What is the deadline?"),
        keywords=(
            "when will", "how long", "how much time", "how soon", "deadline",
            "due date", "eta", "target time", "sla", "timeframe", "by when",
            "expected", "come and fix", "fix it", "normally take",
            "usually take", "time taken", "get done",
        ),
        pattern=_p(
            r"\b(when will|when is|by when|how long (does|will|do|it)|"
            r"due date|target time|time ?frame|expected (time|date)|eta|sla)\b"
        ),
        handler=_handle_deadline,
    ),
    Topic(
        key="why_slow",
        group=TIMING,
        title="Why it is taking so long",
        examples=("Why is it taking so long?", "Nobody has responded yet"),
        keywords=(
            "taking so long", "slow", "still waiting", "any update", "no response",
            "delay", "why so long",
        ),
        pattern=_p(
            r"\b(taking so long|so long|no(body|-one| one| body) (has )?"
            r"(responded|replied|come|turned up)|still waiting|any update|slow)\b"
        ),
        handler=_handle_why_slow,
    ),
    Topic(
        key="timeline",
        group=TRACKING,
        title="The full history of a complaint",
        examples=(
            "Show the history of my complaint",
            "What has happened to my complaint?",
        ),
        keywords=(
            "history", "timeline", "activity", "log", "audit", "what happened",
            "progress so far", "updates on",
        ),
        pattern=_p(
            r"\b(history|timeline|audit|activity log|what (has )?happened (to|with)|"
            r"progress so far|all the updates)\b"
        ),
        handler=_handle_timeline,
    ),
    Topic(
        key="counts",
        group=TRACKING,
        title="How many you have",
        examples=("How many complaints do I have?", "How many are still open?"),
        keywords=("how many complaints", "count", "total", "number of complaints"),
        pattern=_p(
            r"\b(how many|count of|total (number )?of|number of)\b[^?]{0,25}"
            r"\b(complaints?|issues?|requests?|ones?|do i have|"
            r"are (still )?(open|closed|pending|overdue|left))\b"
        ),
        handler=_handle_counts,
    ),
    Topic(
        key="latest",
        group=TRACKING,
        title="Your most recent complaint",
        examples=("What is my latest complaint?", "Show my last complaint"),
        keywords=("latest", "most recent", "last one", "newest", "just filed"),
        pattern=_p(
            r"\b(latest|most recent|newest|last (complaint|one)|"
            r"(the )?one i (just )?filed)\b"
        ),
        handler=_handle_latest,
    ),
    Topic(
        key="photo_rejected",
        group=PHOTOS,
        title="A photo that will not upload",
        examples=("My photo will not upload", "Why was my photo rejected?"),
        keywords=(
            "will not upload", "won't upload", "upload failed", "rejected",
            "too large", "invalid file", "not accepted", "error uploading",
            "file type", "heic", "pdf",
        ),
        pattern=_p(
            r"\b((wo|will) ?n[o']?t upload|upload (failed|error|problem)|"
            r"(photo|image|picture|file) (was )?(rejected|refused|not accepted|"
            r"too (large|big))|invalid (file|image)|heic)\b"
        ),
        handler=_handle_photo_rejected,
    ),
    Topic(
        key="photo_later",
        group=PHOTOS,
        title="Adding photos afterwards",
        examples=("Can I add photos later?", "Can I attach more evidence now?"),
        keywords=("add photos later", "more photos", "afterwards", "forgot to attach"),
        pattern=_p(
            r"\b(add|attach|upload|send)\b[^?]{0,30}\b(later|afterwards|after "
            r"submitting|now)\b|\bforgot to (attach|add|upload)\b"
        ),
        handler=_handle_photo_later,
    ),
    Topic(
        key="who_sees_photos",
        group=PRIVACY,
        title="Who can see your photos",
        examples=("Who can see my photos?", "Are my photos public?"),
        keywords=(
            "who can see", "photos public", "private photos", "visible to",
            "anyone see",
        ),
        pattern=_p(
            r"\b(who (can|else) (can )?(see|view|open|access))\b[^?]{0,30}"
            r"\b(photos?|pics?|pictures?|images?|evidence)\b"
            r"|\b(photo|pic|picture|image|evidence)s?\b[^?]{0,20}\b(public|private)\b"
        ),
        handler=_handle_who_sees_photos,
    ),
    Topic(
        key="photo_help",
        group=PHOTOS,
        title="Attaching photos",
        examples=("How do I attach photos?", "What image formats are allowed?"),
        keywords=(
            "photo", "photos", "pic", "pics", "image", "picture", "upload",
            "attach", "evidence", "camera", "snap",
            "jpg", "png", "webp", "screenshot", "file size",
        ),
        pattern=_p(
            r"\b(photos?|pics?|pictures?|images?|upload|attach|evidence|camera|"
            r"jpg|jpeg|png|webp)\b"
        ),
        handler=_handle_photo_help,
    ),
    Topic(
        key="what_to_write",
        group=FILING,
        title="What to write in a complaint",
        examples=("What should I write in the description?", "How much detail?"),
        keywords=(
            "what should i write", "description", "how much detail", "title",
            "wording", "write it",
        ),
        pattern=_p(
            r"\b(what should i (write|say|put)|how much detail|what (to|do i) "
            r"(write|include)|good description|write (a|the) (title|description))\b"
        ),
        handler=_handle_what_to_write,
    ),
    Topic(
        key="edit_or_delete",
        group=FILING,
        title="Editing or deleting a complaint",
        examples=("Can I edit my complaint?", "How do I delete a complaint?"),
        keywords=(
            "edit", "change my complaint", "delete", "remove complaint", "cancel",
            "withdraw", "wrong category", "made a mistake", "undo",
        ),
        pattern=_p(
            r"\b(edit|change|update|correct|modif\w+|delete|remove|cancel|withdraw|"
            r"undo)\b[^?]{0,30}\bcomplaint\b"
            r"|\bcomplaint\b[^?]{0,30}\b(by mistake|wrong (category|location|"
            r"priority)|mistake)\b"
        ),
        handler=_handle_edit_or_delete,
    ),
    Topic(
        key="duplicate",
        group=FILING,
        title="Somebody already reported it",
        examples=(
            "Someone else already reported this",
            "Will my complaint be a duplicate?",
        ),
        keywords=(
            "duplicate", "already reported", "already filed", "same problem",
            "someone else", "twice",
        ),
        pattern=_p(
            r"\b(duplicate|already (been )?(reported|filed|raised|submitted)|"
            r"same (problem|issue|complaint)|someone else (has )?(filed|reported)|"
            r"file (it )?twice)\b"
        ),
        handler=_handle_duplicate,
    ),
    Topic(
        key="which_category",
        group=FILING,
        title="Which category to choose",
        examples=(
            "Which category should I pick for a broken fan?",
            "What category is a leaking tap?",
        ),
        keywords=(
            "which category", "what category", "category should i", "which one to pick",
        ),
        pattern=_p(
            r"\b(which|what) categor\w+\b[^?]{0,60}\b(pick|choose|select|use|is|for|"
            r"should)\b|\bcategor\w+ (should|do) i\b"
        ),
        handler=_handle_which_category,
    ),
    Topic(
        key="how_to_file",
        group=FILING,
        title="Filing a complaint",
        examples=("How do I file a complaint?", "How do I report a problem?"),
        keywords=(
            "how to file", "raise a complaint", "submit a complaint", "report a problem",
            "lodge", "new complaint", "register a complaint",
        ),
        pattern=_p(
            r"\b(how (do|can|to) i? ?(file|raise|submit|report|make|lodge)|"
            r"file a (new )?complaint|new complaint|raise a complaint|"
            r"register a complaint|report (a|an) (issue|problem)|"
            r"submit a complaint)\b"
        ),
        handler=_handle_how_to_file,
    ),
    Topic(
        key="register",
        group=ACCOUNT,
        title="Registering an account",
        examples=("How do I register?", "How do I create an account?"),
        keywords=("register", "sign up", "create account", "new account", "join"),
        pattern=_p(
            r"\b(how (do|can) i (register|sign ?up|create an account)|"
            r"sign ?up|registration|create (a|an) (new )?account)\b"
        ),
        handler=_handle_register,
    ),
    Topic(
        key="priority",
        group=FILING,
        title="Choosing a priority",
        examples=("What does Urgent mean?", "Which priority should I choose?"),
        keywords=(
            "priority", "urgent", "high priority", "low priority", "how serious",
            "severity",
        ),
        pattern=_p(r"\b(priorit\w+|urgen\w+|severity|how serious)\b"),
        handler=_handle_priority,
    ),
    Topic(
        key="locations",
        group=FILING,
        title="Locations you can pick",
        examples=("What locations can I choose?", "My room is not in the list"),
        keywords=(
            "location", "locations", "building", "room", "block", "which place",
            "not listed",
        ),
        pattern=_p(
            r"\b(what|which|list of|all the) (location|building|block|place|room)s?\b"
            r"|\b(location|room|building)\b[^?]{0,25}\bnot "
            r"(listed|there|available|(in|on) the list)\b"
        ),
        handler=_handle_locations,
    ),
    Topic(
        key="categories",
        group=FILING,
        title="The categories available",
        examples=("What categories are there?", "What kinds of problems can I report?"),
        keywords=(
            "categories", "category", "types of complaint", "what kind", "department",
        ),
        pattern=_p(r"\b(categor|what kind|what type|which department)\w*\b"),
        handler=_handle_categories,
    ),
    Topic(
        key="notifications",
        group=PROCESS,
        title="Notifications",
        examples=("Where are my notifications?", "How will I know when it updates?"),
        keywords=(
            "notification", "notifications", "bell", "alert", "informed", "told",
            "unread",
        ),
        pattern=_p(
            r"\b(notification|notifications|the bell|alerts?|"
            r"how (will|do) i (know|find out)|be (informed|told|notified))\b"
        ),
        handler=_handle_notifications,
    ),
    Topic(
        key="email",
        group=PROCESS,
        title="Email updates",
        examples=("Will I get an email?", "I did not receive any email"),
        keywords=("email", "mail", "inbox", "spam", "gmail", "message"),
        pattern=_p(
            r"\be-?mails?\b[^?]{0,40}\b(get|receive|received|sent|send|notif\w+|"
            r"inbox|spam|arrive[sd]?|came|come)\b"
            r"|\b(get|got|getting|receiv\w+|sent|send|expect\w*)\b[^?]{0,30}"
            r"\be-?mails?\b"
            r"|\bno e-?mails?\b|\be-?mail (notification|update|alert)s?\b"
        ),
        handler=_handle_email,
    ),
    Topic(
        key="privacy",
        group=PRIVACY,
        title="Who can see your complaint",
        examples=("Can I file anonymously?", "Who can see my complaint?"),
        keywords=(
            "anonymous", "anonymously", "private", "confidential", "who can see",
            "will they know", "identity", "name shown", "without my name",
            "my name showing", "hide my name", "keep it secret",
        ),
        pattern=_p(
            r"\b(anonym\w+|confidential|private|privacy)\b"
            r"|\bwho (can|else) (can )?(see|view|read)\b[^?]{0,30}\bcomplaint\b"
            r"|\bwill (they|anyone|others) know\b"
        ),
        handler=_handle_privacy,
    ),
    Topic(
        key="security",
        group=PRIVACY,
        title="How your data is protected",
        examples=("Is my data safe?", "How secure is this system?"),
        keywords=(
            "secure", "security", "safe", "protected", "encrypted", "hacked",
            "data safe", "gdpr",
        ),
        pattern=_p(
            r"\b(secur\w+|is my data safe|data (is )?safe|encrypt\w+|hack\w+|"
            r"protected|safe(ty)? of my (data|information))\b"
        ),
        handler=_handle_security,
    ),
    Topic(
        key="identity",
        group=ABOUT,
        title="What this assistant is",
        examples=("Are you a real person?", "Who are you?"),
        keywords=(
            "who are you", "are you a bot", "are you human", "are you ai",
            "chatgpt", "your name", "robot",
        ),
        pattern=_p(
            r"\b(who are you|are you (a )?(bot|robot|human|real|person|ai|an ai)|"
            r"what are you|chat ?gpt|your name)\b"
        ),
        handler=_handle_identity,
        listed=False,
    ),
    Topic(
        key="contact",
        group=ABOUT,
        title="Reaching a person",
        examples=("How do I contact an administrator?", "I want to talk to someone"),
        keywords=(
            "contact", "talk to someone", "phone", "office", "human", "real person",
            "who do i ask", "helpdesk", "call", "ring", "speak", "complain to",
        ),
        pattern=_p(
            r"\b(contact|talk to (a|someone|somebody|a person|a human)|"
            r"speak to|phone number|help ?desk|reach (the )?(admin|office|someone)|"
            r"real person)\b"
        ),
        handler=_handle_contact,
    ),
    Topic(
        key="filtered",
        group=TRACKING,
        title="Filtering by status",
        examples=("Show my open complaints", "Which of my complaints are closed?"),
        keywords=(
            "open complaints", "closed complaints", "pending complaints",
            "resolved complaints", "active ones", "finished ones", "pending",
            "outstanding", "still open", "left pending",
        ),
        pattern=_p(
            r"\b(open|closed|pending|resolved|finished|completed|active|ongoing|"
            r"unresolved|in ?progress)\b[^?]{0,20}\b(complaints?|ones?|issues?|mine)\b"
            r"|\b(complaints?|ones?|mine)\b[^?]{0,25}\b(that|which|still|of mine) ?(are )?"
            r"(open|closed|pending|resolved|finished|active|ongoing)\b"
        ),
        handler=_handle_filtered,
    ),
    Topic(
        key="my_complaints",
        group=TRACKING,
        title="All of your complaints",
        examples=("Show my complaints", "What is the status of my complaints?"),
        keywords=(
            "my complaints", "list", "show", "track", "progress", "status",
            "where is it", "what is happening", "stage", "my issue",
            "my request", "i have raised", "i have filed", "so far",
        ),
        pattern=_p(
            r"\b(my complaints?|list|show|all my|status|track|progress|"
            r"what('s| is) happening|where (is|are) (it|they|mine))\b"
        ),
        handler=_handle_my_complaints,
    ),
    Topic(
        key="profile",
        group=ACCOUNT,
        title="Your own details",
        examples=("What are my account details?", "What is my role?"),
        keywords=(
            "my profile", "my details", "my roll number", "my department",
            "my role", "my email address",
        ),
        pattern=_p(
            r"\bmy (profile|details|account details|roll ?(no|number)|department|"
            r"designation|role|staff id)\b|\bwho am i\b"
        ),
        handler=_handle_profile,
    ),
    Topic(
        key="account",
        group=ACCOUNT,
        title="Passwords and signing in",
        examples=("I forgot my password", "How do I sign out?"),
        keywords=(
            "password", "login", "log in", "logout", "sign in", "sign out",
            "account", "locked out", "cannot log in",
        ),
        pattern=_p(r"\b(password|log ?in|log ?out|sign ?in|sign ?out|account|email)\b"),
        handler=_handle_account,
    ),
    Topic(
        key="about",
        group=ABOUT,
        title="What CampusCare is",
        examples=("What is CampusCare?", "What is this system for?"),
        keywords=(
            "what is campuscare", "what is this", "about this", "purpose",
            "what does this do",
        ),
        pattern=_p(
            r"\bwhat is (campuscare|this (app|site|system|website|portal|thing))\b"
            r"|\babout (campuscare|this (app|system|website))\b"
            r"|\bwhat does this (app|system|website|do)\b"
        ),
        handler=_handle_about,
    ),
    Topic(
        key="open_source",
        group=ABOUT,
        title="Open source and technology",
        examples=("Is this open source?", "What technology is it built with?"),
        keywords=(
            "open source", "github", "source code", "licence", "license", "mit",
            "tech stack", "built with", "flask", "python", "technology",
        ),
        pattern=_p(
            r"\b(open ?source|github|source code|licen[cs]e|mit licen[cs]e|"
            r"tech(nology)? stack|built (with|using|on)|which technolog\w+|"
            r"what (language|framework))\b"
        ),
        handler=_handle_open_source,
    ),
    Topic(
        key="frustration",
        group=ABOUT,
        title="When nothing is happening",
        examples=("This is useless", "I am fed up with waiting"),
        keywords=(
            "useless", "rubbish", "terrible", "worst", "fed up", "angry",
            "frustrated", "waste of time", "nonsense", "pathetic", "hopeless",
        ),
        pattern=_p(
            r"\b(useless|rubbish|terrible|worst|awful|pathetic|hopeless|nonsense|"
            r"fed up|frustrat\w+|waste of (time|my time)|annoy\w+|"
            r"(so|very) (angry|annoyed))\b"
        ),
        handler=_handle_frustration,
        listed=False,
    ),
    Topic(
        key="greeting",
        group=ABOUT,
        title="Saying hello",
        examples=("Hello",),
        keywords=("hello", "hi", "hey", "good morning", "namaste"),
        pattern=_p(r"^\s*(hi|hey|hello|good (morning|afternoon|evening)|namaste)\b"),
        handler=_handle_greeting,
        listed=False,
    ),
    Topic(
        key="thanks",
        group=ABOUT,
        title="Saying thanks",
        examples=("Thanks",),
        keywords=("thanks", "thank you", "cheers", "helpful"),
        pattern=_p(r"\b(thanks|thank you|thx|got it|helpful|cheers)\b"),
        handler=_handle_thanks,
        listed=False,
    ),
    Topic(
        key="bye",
        group=ABOUT,
        title="Saying goodbye",
        examples=("Bye",),
        keywords=("bye", "goodbye", "see you", "that is all"),
        pattern=_p(r"\b(bye|goodbye|see you|that('s| is) all|nothing else)\b"),
        handler=_handle_bye,
        listed=False,
    ),
    Topic(
        key="help",
        group=ABOUT,
        title="What I can help with",
        examples=("What can you do?", "Help"),
        keywords=("help", "options", "menu", "commands", "guide me"),
        pattern=_p(r"\b(help|what can you do|options|menu|commands)\b"),
        handler=_handle_help,
        listed=False,
    ),
)

#: Fast lookup by key, for tests and for the catalogue endpoint.
TOPICS_BY_KEY: dict[str, Topic] = {topic.key: topic for topic in TOPICS}

#: Topics whose handlers resolve a complaint code for themselves. When a
#: message names a code *and* matches one of these, the specific question wins
#: over the generic status answer. Ordered most specific first.
_CODE_AWARE = ("timeline", "deadline", "why_slow", "assignee")

#: Group order in the browsable menu -- roughly the order a student meets them.
GROUP_ORDER = (
    TRACKING,
    FILING,
    PHOTOS,
    PROCESS,
    TIMING,
    ACCOUNT,
    PRIVACY,
    STAFF,
    ABOUT,
)


def _visible_groups(user) -> list[dict]:
    """The catalogue as groups, filtered to what this user can use."""
    grouped: dict[str, list[Topic]] = {}
    for topic in TOPICS:
        if topic.visible_to(user):
            grouped.setdefault(topic.group, []).append(topic)

    return [
        {"name": name, "topics": grouped[name]}
        for name in GROUP_ORDER
        if grouped.get(name)
    ]


def catalogue_for(user) -> dict:
    """The browsable question list, shaped for the widget."""
    return {
        "groups": [
            {
                "name": group["name"],
                "topics": [
                    {"key": topic.key, "title": topic.title, "examples": list(topic.examples)}
                    for topic in group["topics"]
                ],
            }
            for group in _visible_groups(user)
        ]
    }


# --------------------------------------------------------------------------
# Stage 3: keyword scoring, for phrasings no pattern anticipated
# --------------------------------------------------------------------------

#: A multi-word keyword is strong evidence; a single word is a hint. One phrase
#: or two separate words is enough to answer confidently.
_PHRASE_WEIGHT = 1.4
_WORD_WEIGHT = 0.7
_CONFIDENT = 1.4
_TENTATIVE = 0.7


#: Words too common to say anything about what a question is about.
_STOPWORDS = {
    "a", "am", "an", "and", "any", "are", "as", "at", "be", "by", "can", "did",
    "do", "does", "for", "from", "get", "has", "have", "how", "i", "if", "in",
    "is", "it", "its", "me", "my", "no", "not", "of", "on", "or", "should",
    "so", "that", "the", "their", "them", "there", "they", "this", "to", "was",
    "we", "what", "when", "where", "which", "who", "why", "will", "with",
    "would", "you", "your",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def _score_topic(topic: Topic, text: str, tokens: set[str]) -> float:
    """How strongly ``text`` suggests ``topic``."""
    score = 0.0
    for keyword in topic.keywords:
        if " " in keyword:
            if keyword in text:
                score += _PHRASE_WEIGHT
        elif keyword in tokens:
            score += _WORD_WEIGHT
    return score


def _rank_topics(user, message: str) -> list[tuple[float, Topic]]:
    """Every topic that shares vocabulary with the message, best first."""
    text = message.lower()
    tokens = _tokens(text)

    ranked = []
    for topic in TOPICS:
        if topic.roles and _role_of(user) not in topic.roles:
            continue
        score = _score_topic(topic, text, tokens)
        if score:
            ranked.append((score, topic))

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def _nearest_examples(message: str, limit: int = 3) -> list[str]:
    """The catalogue questions that share the most words with the message.

    Used when nothing else matched: showing three real questions is far more
    useful than an apology, and it teaches the vocabulary the assistant knows.
    """
    tokens = _tokens(message) - _STOPWORDS
    if not tokens:
        return []

    scored: list[tuple[int, str]] = []
    for topic in TOPICS:
        if not topic.listed:
            continue
        for example in topic.examples:
            overlap = len(tokens & (_tokens(example) - _STOPWORDS))
            if overlap:
                scored.append((overlap, example))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    seen: list[str] = []
    for _overlap, example in scored:
        if example not in seen:
            seen.append(example)
        if len(seen) == limit:
            break
    return seen


# --------------------------------------------------------------------------
# Stage 4: treat an unrecognised message as a description of a problem
# --------------------------------------------------------------------------

#: Wording that describes something broken rather than asking about the system.
_PROBLEM_WORDS = re.compile(
    r"\b(broken|breaks?|not working|does ?n[o']?t work|isn'?t working|damaged|"
    r"leak\w*|block\w+|clog\w+|dirty|smell\w*|stink\w*|stuck|dead|burnt|burned|"
    r"crack\w+|missing|torn|noisy|jam\w+|fused|out of order|not turning on|"
    r"no (water|power|light|electricity|internet|wifi|signal)|"
    r"needs? (repair|fixing|cleaning|replacing))\b",
    re.I,
)


def _looks_like_a_problem(message: str) -> bool:
    return bool(
        _PROBLEM_WORDS.search(message)
        or suggestion_engine.suggest_category(message, "").is_useful
    )


def _handle_problem_report(user, message: str) -> Answer:
    """They described a fault. Turn it into a filing suggestion."""
    category = suggestion_engine.suggest_category(message, "")
    priority = suggestion_engine.suggest_priority(message, "")

    lines = [
        (
            "That reads as something to **report** rather than a question I "
            "can look up — so let us get it filed, because a complaint typed "
            "at me does not reach anybody."
        )
    ]

    if category.is_useful:
        lines.append(
            f"\nFrom your wording it looks like **{category.value}** "
            f"({category.reason})"
            + (
                f", priority **{priority.value.title()}**."
                if priority.value and priority.value != Priority.MEDIUM
                else "."
            )
        )
        lines.append(
            "Both are suggestions the form will offer you — you can pick "
            "something else, and nothing is ever categorised automatically."
        )
    else:
        lines.append(
            "\nPick the closest category on the form; an administrator can move "
            "it later if it needs a different team."
        )

    lines.append(
        "\nInclude where exactly it is and since when, and **attach a photo** — "
        "that is the single thing that most speeds up a repair. You will get a "
        "reference number straight away."
    )

    return Answer(
        "\n".join(lines),
        suggestions=[
            "How do I file a complaint?",
            "What should I write?",
            "How do I attach photos?",
        ],
        links=[("File this complaint", url_for("complaints.new"))],
        intent="problem_report",
    )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _handle_unknown(user, message: str) -> Answer:
    """The honest dead end -- but never an empty one."""
    text = (
        "I did not follow that one. I am best at questions about your "
        "complaints -- where one has got to, how to file a new one, or what "
        "happens after a repair."
    )

    nearest = _nearest_examples(message)
    if nearest:
        text += "\n\nDid you mean one of these?\n" + "\n".join(
            f"• {example}" for example in nearest
        )

    text += (
        "\n\nSay **what can I ask** for the full list of everything I "
        "understand. If you need a person rather than me, an administrator "
        "sees every complaint and can be reached through the administration "
        "office."
    )

    return Answer(
        text,
        suggestions=nearest[:2]
        + ["What can I ask you?", "Show my complaints"][: 3 - len(nearest[:2])],
        intent="unknown",
    )


def respond(user, message: str) -> Answer:
    """Answer one message from ``user``.

    Everything returned is either fixed guidance or read from rows the user is
    entitled to see. Nothing is invented: when no intent matches, the reply
    says so rather than guessing.
    """
    text = (message or "").strip()

    if not text:
        return _handle_help(user, text)

    if len(text) > MAX_MESSAGE:
        return Answer(
            "That is a lot to read at once. Try a shorter question -- for "
            "example “status of CC102” or “how do I attach photos?”",
            suggestions=["Show my complaints", "How do I file a complaint?"],
            intent="too_long",
        )

    # 1. A named complaint usually beats every other reading of the message.
    #    The exception is a question that is *about* that complaint in a
    #    particular way -- its history, its deadline, who holds it. Those
    #    handlers resolve the code themselves and give the better answer, so a
    #    student asking "history of CC102" gets the timeline rather than the
    #    status they did not ask for.
    if _CODE.search(text):
        for key in _CODE_AWARE:
            topic = TOPICS_BY_KEY[key]
            if topic.pattern.search(text):
                return topic.handler(user, text)

    specific = _handle_specific_complaint(user, text)
    if specific is not None:
        return specific

    # 2. An exact pattern from the catalogue.
    for topic in TOPICS:
        if topic.roles and _role_of(user) not in topic.roles:
            continue
        if topic.pattern.search(text):
            return topic.handler(user, text)

    # 3. Keyword scoring. A confident match is answered as though it had
    #    matched a pattern; a weak one is answered but flagged as a guess, so
    #    the student knows to rephrase if it went the wrong way.
    ranked = _rank_topics(user, text)
    if ranked and ranked[0][0] >= _CONFIDENT:
        return ranked[0][1].handler(user, text)

    # 4. A description of something broken is a report, not a question.
    if _looks_like_a_problem(text):
        return _handle_problem_report(user, text)

    if ranked and ranked[0][0] >= _TENTATIVE:
        topic = ranked[0][1]
        answer = topic.handler(user, text)
        answer.text = (
            f"I am not certain I understood, but here is what I know about "
            f"**{topic.title.lower()}**.\n\n" + answer.text
        )
        if "What can I ask you?" not in answer.suggestions:
            answer.suggestions = [*answer.suggestions, "What can I ask you?"][:4]
        return answer

    return _handle_unknown(user, text)


def greeting_for(user) -> Answer:
    """The opening message when the chat is first opened."""
    return _handle_greeting(user, "")
