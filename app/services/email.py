"""Outbound email.

Design notes
------------
**Nothing here ever claims to have sent a message it did not send.** Every
attempt writes an :class:`~app.models.EmailLog` row with one of three statuses:

* ``SENT``            -- the SMTP server accepted it
* ``FAILED``          -- SMTP was configured but the send raised
* ``NOT_CONFIGURED``  -- no SMTP credentials, so nothing left the machine

That last case is deliberate: the application must stay runnable by anyone who
clones it without handing over a mail password. With SMTP unset the whole
workflow still works end to end, the message body is printed to the server log,
and the UI says plainly that mail is disabled.

**Secrets never appear in a log.** The one-time password is passed to the
template and nowhere else -- it is not written to `EmailLog`, not printed, and
not included in any exception message. Passwords are never emailed at all.

Sending is synchronous. A college-scale deployment sends a handful of messages
a day, and a background queue would add moving parts for little gain; the SMTP
timeout is bounded so a dead mail server cannot hang a request indefinitely.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from flask import current_app, has_request_context, render_template, url_for

from ..extensions import db
from ..models import EmailLog


@dataclass
class InlineImage:
    """An image embedded in the message body via a Content-ID reference."""

    cid: str
    data: bytes
    mime: str
    filename: str

    @property
    def src(self) -> str:
        """What the template puts in an ``<img src=...>``."""
        return f"cid:{self.cid.strip('<>')}"


def absolute_url(endpoint: str, **values) -> str:
    """Build a full URL for use inside an email.

    An email is read outside the browser session, so a relative path is
    useless. ``BASE_URL`` is used rather than the request host, because a
    message can also be generated from a CLI command -- and ``url_for`` refuses
    to run outside a request context, so one is borrowed from BASE_URL when
    there isn't a real one.
    """
    base = current_app.config["BASE_URL"].rstrip("/")

    if has_request_context():
        return base + url_for(endpoint, **values)

    with current_app.test_request_context(base_url=base):
        return base + url_for(endpoint, **values)


def _render(template: str, context: dict) -> tuple[str, str | None]:
    """Render the HTML body, plus a plain-text alternative when one exists."""
    html = render_template(f"email/{template}.html", **context)
    try:
        text = render_template(f"email/{template}.txt", **context)
    except Exception:
        text = None
    return html, text


def _build_message(
    to_address: str,
    subject: str,
    html: str,
    text: str | None,
    inline_images: list[InlineImage] | None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["To"] = to_address

    sender = current_app.config["EMAIL_FROM"] or current_app.config["SMTP_USER"]
    if "<" not in sender:
        sender = formataddr((current_app.config["APP_NAME"], sender))
    message["From"] = sender

    message.set_content(text or "This message requires an HTML-capable email client.")
    message.add_alternative(html, subtype="html")

    for image in inline_images or []:
        subtype = image.mime.split("/")[-1]
        # related=True attaches to the HTML part, so cid: references resolve.
        message.get_payload()[1].add_related(
            image.data,
            maintype="image",
            subtype=subtype,
            cid=image.cid,
            filename=image.filename,
        )

    return message


def send_email(
    to_address: str,
    subject: str,
    template: str,
    context: dict | None = None,
    *,
    inline_images: list[InlineImage] | None = None,
    user=None,
    complaint=None,
    commit: bool = False,
) -> EmailLog:
    """Render and deliver one message, recording the outcome.

    Never raises: a mail failure must not roll back the action that triggered
    it (a staff registration is still valid even if the notification bounces).
    Inspect the returned :class:`EmailLog` to see what actually happened.
    """
    config = current_app.config
    context = dict(context or {})
    context.setdefault("app_name", config["APP_NAME"])
    context.setdefault("app_tagline", config["APP_TAGLINE"])
    context.setdefault("base_url", config["BASE_URL"])
    context.setdefault("inline_images", inline_images or [])

    log = EmailLog(
        to_address=to_address,
        subject=subject,
        template=template,
        status=EmailLog.NOT_CONFIGURED,
        user_id=getattr(user, "id", None),
        complaint_id=getattr(complaint, "id", None),
    )

    try:
        html, text = _render(template, context)
    except Exception as exc:
        current_app.logger.exception("Could not render email template %s", template)
        log.status = EmailLog.FAILED
        log.error = f"template error: {exc}"[:500]
        db.session.add(log)
        if commit:
            db.session.commit()
        return log

    if not to_address:
        log.status = EmailLog.FAILED
        log.error = "no recipient address"
        db.session.add(log)
        if commit:
            db.session.commit()
        return log

    if not config["EMAIL_ENABLED"]:
        # Say so loudly rather than pretending. The body goes to the server log
        # so the workflow can still be followed during development.
        current_app.logger.warning(
            "[email disabled] would send %r to %s -- set SMTP_* in .env to deliver",
            subject,
            to_address,
        )
        log.error = "SMTP not configured; message not sent"
        db.session.add(log)
        if commit:
            db.session.commit()
        return log

    message = _build_message(to_address, subject, html, text, inline_images)
    message["Message-ID"] = make_msgid()

    try:
        if config["SMTP_USE_TLS"]:
            with smtplib.SMTP(
                config["SMTP_HOST"], config["SMTP_PORT"], timeout=config["SMTP_TIMEOUT"]
            ) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(
                config["SMTP_HOST"],
                config["SMTP_PORT"],
                timeout=config["SMTP_TIMEOUT"],
                context=ssl.create_default_context(),
            ) as server:
                server.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
                server.send_message(message)

        log.status = EmailLog.SENT
        current_app.logger.info("Email sent: %r to %s", subject, to_address)

    except Exception as exc:
        # Log the class and message, never the credentials or the body.
        current_app.logger.error(
            "Email FAILED: %r to %s -- %s: %s",
            subject,
            to_address,
            type(exc).__name__,
            exc,
        )
        log.status = EmailLog.FAILED
        log.error = f"{type(exc).__name__}: {exc}"[:500]

    db.session.add(log)
    if commit:
        db.session.commit()
    return log


def send_to_admin(subject: str, template: str, context: dict | None = None, **kwargs):
    """Send to the configured administrator address.

    Returns ``None`` when ``ADMIN_EMAIL`` is unset, having logged a warning --
    the caller's own work still succeeds.
    """
    admin_email = current_app.config.get("ADMIN_EMAIL")
    if not admin_email:
        current_app.logger.warning(
            "ADMIN_EMAIL is not set; skipping admin notification %r", subject
        )
        return None
    return send_email(admin_email, subject, template, context, **kwargs)
