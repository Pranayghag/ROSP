"""CampusCare Assistant -- the in-app help chat for students.

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

For the handful of questions that are genuinely procedural ("how do I attach a
photo?") the answer is fixed text, which is exactly right: the procedure does
not vary, and a fixed answer cannot drift from what the software really does.

The result needs no API key, costs nothing, runs offline, and every reply can
be traced to a line of code or a database row -- which also makes it defensible
in a viva.

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
from dataclasses import dataclass, field

from flask import url_for
from sqlalchemy import select

from ..constants import STATUS_LABELS, Status
from ..extensions import db
from ..models import Complaint, _as_utc, utcnow

#: Complaint references as students write them: CC102, cc-102, "complaint 102".
_CODE = re.compile(r"\b(?:cc[\s-]?)(\d{2,6})\b|\bcomplaint\s+(?:no\.?|number\s*)?#?(\d{2,6})\b", re.I)


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
    about complaints they filed; staff and admins see their assigned queue.
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


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Intent handlers
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
        suggestions = ["Why is it taking so long?", "Who is handling it?"]

    return Answer(
        text,
        suggestions=suggestions,
        links=[_complaint_link(complaint)],
        intent="complaint_status",
    )


def _handle_my_complaints(user, message: str) -> Answer:
    complaints = _own_complaints(user)

    if not complaints:
        return Answer(
            "You have not filed any complaints yet. When something on campus "
            "needs fixing -- a broken fan, a leaking ceiling, a dead projector "
            "-- file it here and you will be able to follow it right through to "
            "the repair.",
            suggestions=["How do I file a complaint?"],
            links=[("File a complaint", url_for("complaints.new"))],
            intent="my_complaints_empty",
        )

    open_ones = [c for c in complaints if c.is_open]
    lines = [
        f"You have **{len(complaints)}** complaint(s), **{len(open_ones)}** still open."
    ]

    for complaint in complaints[:5]:
        label = STATUS_LABELS.get(complaint.status, complaint.status)
        flag = " ⚠ overdue" if complaint.is_overdue else ""
        lines.append(f"• **{complaint.code}** — {complaint.title} — {label}{flag}")

    if len(complaints) > 5:
        lines.append(f"…and {len(complaints) - 5} more.")

    awaiting = [c for c in complaints if c.status == Status.STUDENT_VERIFICATION]
    if awaiting:
        codes = ", ".join(c.code for c in awaiting)
        lines.append(
            f"\n**{codes}** need{'s' if len(awaiting) == 1 else ''} you to confirm "
            "the work is done."
        )

    return Answer(
        "\n".join(lines),
        suggestions=["File a new complaint", "Anything overdue?"],
        links=[("My complaints", url_for("complaints.index"))],
        intent="my_complaints",
    )


def _handle_overdue(user, message: str) -> Answer:
    overdue = [c for c in _own_complaints(user) if c.is_overdue]

    if not overdue:
        return Answer(
            "Nothing of yours is past its deadline. Every open complaint is "
            "still within the time its category allows.",
            suggestions=["Show my complaints"],
            intent="overdue_none",
        )

    lines = [f"**{len(overdue)}** of your complaints {'is' if len(overdue) == 1 else 'are'} overdue:"]
    for complaint in overdue[:5]:
        hours = abs(complaint.hours_remaining or 0)
        lines.append(f"• **{complaint.code}** — {complaint.title} — {hours:.0f}h late")

    lines.append(
        "\nOverdue complaints are flagged to administrators automatically, so "
        "someone can see they have slipped."
    )
    return Answer(
        "\n".join(lines),
        links=[_complaint_link(overdue[0])],
        intent="overdue",
    )


def _handle_who_is_handling(user, message: str) -> Answer:
    complaint = _find_by_code(user, message)
    candidates = [complaint] if complaint else [
        c for c in _own_complaints(user) if c.is_open
    ]

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
            lines.append(
                f"• **{item.code}** — {who}" + (f" ({dept})" if dept else "")
            )
        else:
            lines.append(
                f"• **{item.code}** — not assigned yet; an administrator reviews "
                "new complaints and passes them to the right staff member"
            )

    return Answer(
        "Here is who is handling your open complaints:\n" + "\n".join(lines),
        suggestions=["Why is it taking so long?"],
        intent="assignee",
    )


def _handle_how_to_file(user, message: str) -> Answer:
    return Answer(
        "Filing a complaint takes about a minute:\n"
        "1. Open **New Complaint**\n"
        "2. Give it a short title and describe what is wrong, where, and since when\n"
        "3. Pick the category and the location\n"
        "4. Set a priority — be honest; *Urgent* is for anything unsafe\n"
        "5. **Attach photos.** Evidence is the single biggest thing that gets a "
        "complaint fixed quickly\n"
        "6. Submit\n\n"
        "You get a reference like CC102 straight away, and you can follow every "
        "step from your dashboard.",
        suggestions=["How do I attach photos?", "What categories are there?"],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="how_to_file",
    )


def _handle_photo_help(user, message: str) -> Answer:
    return Answer(
        "On the complaint form, use **+ Choose Photos** or drag images onto the "
        "upload box.\n\n"
        "• Up to **5 photos**, **5 MB** each\n"
        "• JPG, JPEG, PNG or WEBP\n"
        "• You see a preview before submitting, and can remove any photo with "
        "the ✕ on its corner\n"
        "• While a complaint is still *Pending* you can remove a photo from its "
        "details page too\n\n"
        "Your photos are private. Only you, the staff member assigned to the "
        "complaint, and administrators can open them.",
        suggestions=["How do I file a complaint?"],
        links=[("File a complaint", url_for("complaints.new"))],
        intent="photo_help",
    )


def _handle_why_slow(user, message: str) -> Answer:
    complaint = _find_by_code(user, message)
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
        text, links=[_complaint_link(target)], intent="why_slow"
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
        "and goes back to the staff member"
    )

    if awaiting:
        codes = ", ".join(c.code for c in awaiting)
        text = f"**{codes}** {'is' if len(awaiting) == 1 else 'are'} waiting on you right now.\n\n" + text
        return Answer(
            text, links=[_complaint_link(awaiting[0])], intent="verification"
        )

    return Answer(text, suggestions=["Show my complaints"], intent="verification")


def _handle_categories(user, message: str) -> Answer:
    from ..models import Category

    categories = db.session.scalars(
        select(Category).where(Category.is_active.is_(True)).order_by(Category.name)
    ).all()

    if not categories:
        return Answer(
            "No categories are set up yet. An administrator needs to add them.",
            intent="categories_empty",
        )

    lines = ["You can file a complaint under any of these:"]
    for category in categories:
        target = f"{category.sla_hours}h target"
        lines.append(f"• **{category.name}** — {category.description} ({target})")

    lines.append(
        "\nPick the closest match. If you are unsure the form suggests one from "
        "your wording, and an administrator can change it later."
    )
    return Answer(
        "\n".join(lines),
        suggestions=["How do I file a complaint?"],
        intent="categories",
    )


def _handle_account(user, message: str) -> Answer:
    return Answer(
        "For anything to do with your account:\n"
        "• Your name and email are shown in the menu at the top right\n"
        "• Signing out is in that same menu\n"
        "• If you cannot sign in, contact the administration office — they can "
        "reset an account\n\n"
        "CampusCare will never ask for your password by email or message.",
        intent="account",
    )


def _handle_greeting(user, message: str) -> Answer:
    name = (user.name or "").split()[0] if getattr(user, "name", None) else None
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
        ],
        intent="greeting",
    )


def _handle_thanks(user, message: str) -> Answer:
    return Answer(
        "Glad to help. Ask any time — I am here on every page.",
        intent="thanks",
    )


def _handle_help(user, message: str) -> Answer:
    return Answer(
        "I can help with:\n"
        "• **Where your complaint has got to** — try “status of CC102”\n"
        "• **Filing one** — how it works, what to include\n"
        "• **Photos** — how to attach them, and who can see them\n"
        "• **Timing** — what the deadline is and what happens when it passes\n"
        "• **Verifying a repair** — what to do when it is marked resolved\n\n"
        "Ask in your own words; you do not need special commands.",
        suggestions=[
            "Show my complaints",
            "How do I file a complaint?",
            "What categories are there?",
        ],
        intent="help",
    )


# --------------------------------------------------------------------------
# Intent matching
# --------------------------------------------------------------------------
#
# Ordered most specific first. Each entry is (name, pattern, handler); the
# first pattern that matches wins, so a message naming a complaint code is
# answered with that complaint rather than with generic advice.

_INTENTS: list[tuple[str, re.Pattern, object]] = [
    (
        "verification",
        re.compile(
            r"\b(verify|verification|confirm|not fixed|still broken|reopen|"
            r"resolved|approve the (fix|repair)|what happens (next|after))\b",
            re.I,
        ),
        _handle_verification,
    ),
    (
        "overdue",
        re.compile(r"\b(overdue|late|past (the )?deadline|missed|delayed)\b", re.I),
        _handle_overdue,
    ),
    (
        "assignee",
        re.compile(
            r"\b(who('s| is)? (handling|working|assigned|fixing|responsible)|"
            r"which staff|assigned to whom|who will fix)\b",
            re.I,
        ),
        _handle_who_is_handling,
    ),
    (
        "why_slow",
        re.compile(
            r"\b(taking so long|how long|when will|no (one|body) (has )?"
            r"(responded|replied|come)|still waiting|any update|slow|"
            r"deadline|sla)\b",
            re.I,
        ),
        _handle_why_slow,
    ),
    (
        "photo_help",
        re.compile(
            r"\b(photo|image|picture|upload|attach|evidence|camera|jpg|png)\b", re.I
        ),
        _handle_photo_help,
    ),
    (
        "how_to_file",
        re.compile(
            r"\b(how (do|can) i (file|raise|submit|report|make|lodge)|"
            r"file a (new )?complaint|new complaint|raise a complaint|"
            r"report (a|an) (issue|problem)|submit a complaint)\b",
            re.I,
        ),
        _handle_how_to_file,
    ),
    (
        "categories",
        re.compile(r"\b(categor|what kind|what type|which department)\w*\b", re.I),
        _handle_categories,
    ),
    (
        "my_complaints",
        re.compile(
            r"\b(my complaints?|list|show|all my|status|track|progress|"
            r"what('s| is) happening)\b",
            re.I,
        ),
        _handle_my_complaints,
    ),
    (
        "account",
        re.compile(r"\b(password|log ?in|log ?out|sign ?in|account|profile|email)\b", re.I),
        _handle_account,
    ),
    (
        "greeting",
        re.compile(r"^\s*(hi|hey|hello|good (morning|afternoon|evening)|namaste)\b", re.I),
        _handle_greeting,
    ),
    (
        "thanks",
        re.compile(r"\b(thanks|thank you|thx|got it|helpful|cheers)\b", re.I),
        _handle_thanks,
    ),
    (
        "help",
        re.compile(r"\b(help|what can you do|options|menu|commands)\b", re.I),
        _handle_help,
    ),
]


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


def respond(user, message: str) -> Answer:
    """Answer one message from ``user``.

    Everything returned is either fixed guidance or read from rows the user is
    entitled to see. Nothing is invented: when no intent matches, the reply
    says so rather than guessing.
    """
    text = (message or "").strip()

    if not text:
        return _handle_help(user, text)
    if len(text) > 500:
        return Answer(
            "That is a lot to read at once. Try a shorter question -- for "
            "example “status of CC102” or “how do I attach photos?”",
            suggestions=["Show my complaints", "How do I file a complaint?"],
            intent="too_long",
        )

    # A named complaint beats every other reading of the message.
    specific = _handle_specific_complaint(user, text)
    if specific is not None:
        return specific

    for _name, pattern, handler in _INTENTS:
        if pattern.search(text):
            return handler(user, text)

    return Answer(
        "I did not follow that one. I am best at questions about your "
        "complaints -- where one has got to, how to file a new one, or what "
        "happens after a repair.\n\n"
        "If you need a person rather than me, an administrator sees every "
        "complaint and can be reached through the administration office.",
        suggestions=[
            "Show my complaints",
            "How do I file a complaint?",
            "What can you do?",
        ],
        intent="unknown",
    )


def greeting_for(user) -> Answer:
    """The opening message when the chat is first opened."""
    return _handle_greeting(user, "")
