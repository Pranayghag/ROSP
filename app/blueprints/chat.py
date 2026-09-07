"""The assistant endpoints behind the chat widget.

Three routes, all requiring a signed-in user: one to open the conversation, one
to list the questions the assistant understands, and one to answer a message.
Nothing is persisted -- the transcript lives in the browser tab and is gone when
it closes. There is no reason to keep it, and not keeping it means there is no
store of student questions to leak.

Every answer is produced by :mod:`app.services.chatbot`, which scopes each
lookup to the person asking.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..services import chatbot

bp = Blueprint("chat", __name__, url_prefix="/assistant")

#: Anything beyond this is not even measured -- a multi-megabyte body should
#: be dropped at the door. Messages between the assistant's own limit and this
#: one are passed through so it can reply "that is a lot to read at once"
#: rather than silently answering a truncated question.
HARD_LIMIT = 10_000


@bp.route("/open", methods=["GET"])
@login_required
def open_chat():
    """The greeting shown when the widget is first opened."""
    return jsonify(chatbot.greeting_for(current_user).as_dict())


@bp.route("/topics", methods=["GET"])
@login_required
def topics():
    """Every question the assistant understands, grouped for browsing.

    Built from the same catalogue that does the matching, so the menu can never
    advertise something the assistant cannot actually answer. Staff-only topics
    are filtered out for students here rather than in the browser.
    """
    return jsonify(chatbot.catalogue_for(current_user))


@bp.route("/ask", methods=["POST"])
@login_required
def ask():
    """Answer one message.

    The reply is built from fixed guidance plus rows the current user is
    entitled to see -- never from another student's complaints.
    """
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")

    if not isinstance(message, str):
        return jsonify({"error": "message must be text"}), 400

    if len(message) > HARD_LIMIT:
        message = message[:HARD_LIMIT]

    answer = chatbot.respond(current_user, message)
    return jsonify(answer.as_dict())
