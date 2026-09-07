"""Coverage of the assistant's question catalogue.

:mod:`tests.test_assistant` proves the assistant cannot leak and does not
invent. This file proves the other half of the promise: that it actually
*answers*.

Three properties, each of which would otherwise rot silently:

* **The menu tells the truth.** Every example question advertised in the
  browsable list must route to the topic that advertises it. Without this,
  adding a broad new pattern quietly steals questions from a narrower one and
  the menu starts offering things that no longer work.
* **Real phrasings land.** Students do not type the catalogue wording, so a
  spread of paraphrases is checked against the topic each ought to reach.
* **An unrecognised message still helps.** A description of a fault becomes a
  filing suggestion; anything else gets an honest dead end that still offers a
  way forward.
"""

from __future__ import annotations

import pytest

from app.constants import Role
from app.services import chatbot


@pytest.fixture
def ask_direct(app, users):
    """Call the assistant in-process, outside the HTTP layer.

    Faster than a request per question, and these tests are about routing
    rather than about the endpoint, which ``test_assistant.py`` covers.
    """

    def _ask(message, role=Role.STUDENT):
        who = {
            Role.STUDENT: users["student1"],
            Role.STAFF: users["staff1"],
            Role.ADMIN: users["admin"],
        }[role]
        with app.test_request_context():
            return chatbot.respond(who, message)

    return _ask


# ==========================================================================
# The catalogue is self-consistent
# ==========================================================================


def test_every_advertised_question_reaches_its_own_topic(app, users):
    """The browsable menu may not offer a question it cannot answer.

    Each example is routed through the full :func:`respond` pipeline and
    compared against calling that topic's own handler. Anything that differs
    has been stolen by an earlier, broader pattern.
    """
    misrouted = []
    with app.test_request_context():
        for topic in chatbot.TOPICS:
            user = users["staff1"] if topic.roles else users["student1"]
            for example in topic.examples:
                routed = chatbot.respond(user, example)
                intended = topic.handler(user, example)
                if routed.intent != intended.intent:
                    misrouted.append(
                        f"[{topic.key}] {example!r} "
                        f"expected {intended.intent}, got {routed.intent}"
                    )

    assert not misrouted, "\n" + "\n".join(misrouted)


def test_catalogue_has_no_duplicate_keys():
    keys = [topic.key for topic in chatbot.TOPICS]
    assert len(keys) == len(set(keys))


def test_every_topic_is_in_a_known_group():
    for topic in chatbot.TOPICS:
        assert topic.group in chatbot.GROUP_ORDER, topic.key


def test_every_listed_topic_has_an_example():
    for topic in chatbot.TOPICS:
        assert topic.examples, topic.key
        assert topic.keywords, topic.key


def test_students_are_not_offered_staff_topics(client, login):
    login("student1@rosp.edu")
    body = client.get("/assistant/topics").get_json()

    titles = [
        topic["title"]
        for group in body["groups"]
        for topic in group["topics"]
    ]
    assert "Resolving an assigned complaint" not in titles
    assert "Filing a complaint" in titles


def test_staff_are_offered_staff_topics(client, login, users):
    from app.constants import AuthorizationStatus
    from app.extensions import db

    users["staff1"].authorization_status = AuthorizationStatus.AUTHORIZED
    db.session.commit()

    login("staff1@rosp.edu")
    body = client.get("/assistant/topics").get_json()

    titles = [
        topic["title"]
        for group in body["groups"]
        for topic in group["topics"]
    ]
    assert "Resolving an assigned complaint" in titles


def test_topics_requires_signing_in(client):
    assert client.get("/assistant/topics").status_code == 302


# ==========================================================================
# Questions in a student's own words
# ==========================================================================

#: Phrasings deliberately unlike the catalogue wording, each paired with the
#: topic it must reach. These are the questions the assistant was failing on
#: before the fallback existed.
PARAPHRASES = [
    # Tracking
    ("i want to know what stage my issue is at", "my_complaints"),
    ("show everything i have raised so far", "my_complaints"),
    ("can somebody tell me who got assigned to my request", "assignee"),
    ("whats left pending from my side", "my_complaints"),
    # Timing
    ("any idea when someone will come and fix it", "deadline"),
    ("how much time do they normally take", "deadline"),
    ("has anything of mine gone past the deadline", "overdue"),
    # Filing
    ("is there a way to cancel what i submitted", "edit_or_delete"),
    ("what should i put in the description", "what_to_write"),
    # Photos
    ("do i need to upload pictures", "photo_help"),
    ("how many pics can i put", "photo_help"),
    # Process
    ("what if the guy says its fixed but its not", "verification"),
    ("what happens once i press submit", "lifecycle"),
    # Privacy and account
    ("am i allowed to complain without my name showing", "privacy"),
    ("i cant remember my login details", "account"),
    ("is there someone i can call", "contact"),
]


@pytest.mark.parametrize("question,topic_key", PARAPHRASES)
def test_real_phrasings_reach_the_right_topic(ask_direct, question, topic_key):
    """A student's own wording must land where the catalogue wording lands."""
    answer = ask_direct(question)
    expected = chatbot.TOPICS_BY_KEY[topic_key]

    # Handlers vary their intent by what the data holds ("overdue" becomes
    # "overdue_none" when nothing is late), so compare on the stem.
    assert answer.intent.startswith(topic_key) or answer.intent in (
        expected.key,
        f"{topic_key}_none",
        f"{topic_key}_empty",
    ), f"{question!r} produced {answer.intent}"


# ==========================================================================
# A message that is not a question at all
# ==========================================================================

FAULT_REPORTS = [
    "the fan in room 204 is not working",
    "water is leaking from the ceiling near the library",
    "wifi keeps disconnecting in the hostel",
    "the projector in the seminar hall wont switch on",
    "there is a broken chair in my classroom",
]


@pytest.mark.parametrize("report", FAULT_REPORTS)
def test_a_described_fault_becomes_a_filing_suggestion(ask_direct, report):
    """Typing a problem at the assistant must not vanish into a shrug.

    A complaint typed into the chat reaches nobody, so the assistant has to say
    that plainly and point at the form.
    """
    answer = ask_direct(report)

    assert answer.intent == "problem_report"
    assert answer.links, "must offer the complaint form"
    assert "/complaints/new" in answer.links[0][1]


def test_a_fault_report_suggests_a_category_it_can_justify(ask_direct):
    answer = ask_direct("water is leaking from the tap in the washroom")

    assert "Plumbing & Water" in answer.text
    # The suggestion is explained, and explicitly not binding.
    assert "matched:" in answer.text
    assert "suggestion" in answer.text.lower()


def test_a_fault_report_never_claims_to_have_filed_anything(ask_direct):
    answer = ask_direct("the light in room 204 is broken")

    lowered = answer.text.lower()
    assert "i have filed" not in lowered
    assert "i've filed" not in lowered
    assert "has been submitted" not in lowered


# ==========================================================================
# Safety
# ==========================================================================

HAZARDS = [
    "there is smoke coming from the switchboard",
    "a wire is sparking in the corridor",
    "someone got an electric shock in the lab",
    "the ceiling is about to collapse",
]


@pytest.mark.parametrize("message", HAZARDS)
def test_a_hazard_is_answered_before_anything_else(ask_direct, message):
    """Danger outranks every other reading of a message.

    Several of these would otherwise match the electrical category and be
    answered as a routine filing suggestion.
    """
    answer = ask_direct(message)

    assert answer.intent == "emergency"
    assert "security" in answer.text.lower()


# ==========================================================================
# The dead end is still useful
# ==========================================================================


def test_unknown_offers_the_nearest_real_questions(ask_direct):
    answer = ask_direct("tell me about the complaint deadline policy document")

    # Whatever it does, it must not be a bare apology.
    assert answer.suggestions


@pytest.mark.parametrize(
    "message",
    ["what is the capital of Mongolia", "write me a poem", "what is 2 + 2"],
)
def test_out_of_scope_questions_are_refused_honestly(ask_direct, message):
    answer = ask_direct(message)

    assert answer.intent == "unknown"
    assert "did not follow" in answer.text.lower()
    assert answer.suggestions


def test_unknown_points_at_the_full_question_list(ask_direct):
    answer = ask_direct("what is the capital of Mongolia")

    assert "what can i ask" in answer.text.lower()


def test_the_catalogue_can_be_asked_for_in_chat(ask_direct):
    answer = ask_direct("what can I ask you?")

    assert answer.intent == "catalogue"
    # Every group a student can use should appear.
    for group in (chatbot.TRACKING, chatbot.FILING, chatbot.PHOTOS):
        assert group in answer.text


def test_a_tentative_match_says_that_it_is_tentative(ask_direct):
    """A weak keyword hit is answered, but never presented as certain."""
    answer = ask_direct("is there someone i can call")

    assert answer.intent == "contact"
    assert "not certain" in answer.text.lower()


# ==========================================================================
# Answers that read from real rows
# ==========================================================================


def test_status_glossary_covers_every_status(ask_direct):
    from app.constants import STATUS_LABELS, Status

    answer = ask_direct("what are all the statuses?")

    assert answer.intent == "status_meaning"
    for status in Status.ALL:
        assert STATUS_LABELS[status] in answer.text


def test_one_status_can_be_asked_about_on_its_own(ask_direct):
    answer = ask_direct("what does pending mean?")

    assert answer.intent == "status_meaning"
    assert "administrator" in answer.text.lower()
    # Just the one asked about, not the whole glossary.
    assert "Reopened" not in answer.text


def test_locations_come_from_the_database(ask_direct, app):
    answer = ask_direct("what locations can I choose?")

    assert answer.intent == "locations"
    assert "Room 204" in answer.text


def test_profile_reads_back_the_signed_in_account(ask_direct, users):
    answer = ask_direct("what are my account details?")

    assert answer.intent == "profile"
    assert users["student1"].email in answer.text
    assert "Student" in answer.text


def test_profile_does_not_leak_another_account(ask_direct, users):
    answer = ask_direct("what are my account details?")

    assert users["student2"].email not in answer.text
    assert users["admin"].email not in answer.text


def test_counts_are_real(app, users, ask_direct):
    from tests.test_assistant import make_complaint

    with app.test_request_context():
        make_complaint(users["student1"], title="One")
        make_complaint(users["student1"], title="Two")
        make_complaint(users["student2"], title="Not mine")

    answer = ask_direct("how many complaints do I have?")

    assert answer.intent == "counts"
    assert "**2**" in answer.text
    assert "Not mine" not in answer.text


def test_timeline_reports_the_audit_trail(app, users, ask_direct):
    from app.extensions import db
    from app.services.workflow import open_complaint
    from tests.test_assistant import make_complaint

    with app.test_request_context():
        complaint = make_complaint(users["student1"], title="Traceable")
        open_complaint(complaint, users["student1"])
        db.session.commit()

    answer = ask_direct(f"show the history of {complaint.code}")

    assert answer.intent == "timeline"
    assert "Submitted" in answer.text
    assert "Pending" in answer.text


def test_timeline_of_someone_elses_complaint_reveals_nothing(app, users, ask_direct):
    from tests.test_assistant import make_complaint

    with app.test_request_context():
        theirs = make_complaint(users["student2"], title="Private matter")

    answer = ask_direct(f"show the history of {theirs.code}")

    assert "Private matter" not in answer.text
    assert answer.intent == "complaint_not_found"


def test_filtering_by_status_uses_real_rows(app, users, ask_direct):
    from app.constants import Status
    from tests.test_assistant import make_complaint

    with app.test_request_context():
        make_complaint(users["student1"], title="Still going")
        make_complaint(users["student1"], title="All done", status=Status.CLOSED)

    answer = ask_direct("show my closed complaints")

    assert "All done" in answer.text
    assert "Still going" not in answer.text


def test_which_category_explains_its_reasoning(ask_direct):
    answer = ask_direct("which category should I pick for a leaking tap?")

    assert answer.intent == "which_category"
    assert "Plumbing & Water" in answer.text
    assert "matched:" in answer.text


def test_which_category_admits_when_it_cannot_tell(ask_direct):
    answer = ask_direct("which category should I choose?")

    assert answer.intent == "which_category_unclear"
    assert answer.links


def test_email_answer_is_honest_about_this_installation(ask_direct):
    """Tests run with no SMTP host, and the answer must say so.

    Telling a student an email is coming when none can be sent is exactly the
    kind of confident falsehood this assistant exists to avoid.
    """
    answer = ask_direct("will I get an email?")

    assert answer.intent == "email"
    assert "no mail server is configured" in answer.text.lower()


def test_two_factor_answer_matches_the_configuration(ask_direct, app):
    answer = ask_direct("what is the OTP for?")

    assert answer.intent == "two_factor"
    state = "**on**" if app.config["TWO_FACTOR_ENABLED"] else "**off**"
    assert state in answer.text
