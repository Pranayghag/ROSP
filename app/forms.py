"""WTForms definitions.

Flask-WTF gives every form CSRF protection automatically via ``hidden_tag()``.

Form-level file validation here is a convenience for the user; the
authoritative checks live in :mod:`app.services.uploads`, which inspects the
actual bytes of every upload. Never rely on this layer alone.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed
from wtforms import (
    MultipleFileField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
    ValidationError,
)

from .constants import Priority


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=190)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign In")


class RegisterForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=190)])
    roll_no = StringField("Roll Number", validators=[Optional(), Length(max=50)])
    department = StringField("Department", validators=[Optional(), Length(max=120)])
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(min=8, message="Use at least 8 characters."),
        ],
    )
    confirm = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Create Account")


class ComplaintForm(FlaskForm):
    """The 'Submit New Complaint' form, including photo evidence."""

    title = StringField(
        "Complaint Title",
        validators=[DataRequired(), Length(min=5, max=200)],
    )
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    location_id = SelectField("Location", coerce=int, validators=[DataRequired()])
    priority = SelectField(
        "Priority",
        choices=[(p, p.title()) for p in Priority.ALL],
        default=Priority.MEDIUM,
        validators=[DataRequired()],
    )
    description = TextAreaField(
        "Description",
        validators=[DataRequired(), Length(min=10, max=5000)],
    )
    photos = MultipleFileField(
        "Upload Photos / Evidence",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"],
                "Only JPG, JPEG, PNG and WEBP images are accepted.",
            )
        ],
    )
    submit = SubmitField("Submit Complaint")

    #: Set by the view from config so validation can report the real limit.
    max_photos = 5

    def validate_photos(self, field) -> None:
        """Reject obviously-too-many files before any byte is processed."""
        chosen = [f for f in (field.data or []) if f and getattr(f, "filename", "")]
        if len(chosen) > self.max_photos:
            raise ValidationError(
                f"You can attach at most {self.max_photos} photos "
                f"(you selected {len(chosen)})."
            )


class AssignForm(FlaskForm):
    """Admin action: hand a complaint to a staff member."""

    staff_id = SelectField("Assign to staff", coerce=int, validators=[DataRequired()])
    note = StringField("Note (optional)", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Assign")


class StatusForm(FlaskForm):
    """Carries a single status transition plus an optional note."""

    status = StringField(validators=[DataRequired()])
    note = StringField(validators=[Optional(), Length(max=500)])
    submit = SubmitField("Update")
