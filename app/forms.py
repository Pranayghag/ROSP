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
    Regexp,
    ValidationError,
)

from .constants import DESIGNATIONS, AuthorizationStatus, Priority, Role


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=190)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign In")


class RegisterForm(FlaskForm):
    """Self-registration, for students and staff.

    ``role`` deliberately offers only Student and Staff. An administrator
    account can never be created this way -- and the view re-checks the
    submitted value against that same allow-list, because a select field in the
    browser is only a suggestion.
    """

    role = SelectField(
        "Register As",
        choices=[(Role.STUDENT, "Student"), (Role.STAFF, "Staff")],
        default=Role.STUDENT,
        validators=[DataRequired()],
    )

    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=190)])
    # No Optional() here: it would stop the chain before the staff-only
    # checks in validate_phone / validate_department could run.
    phone = StringField("Phone", validators=[Length(max=30)])
    department = StringField("Department", validators=[Length(max=120)])

    # --- Student-only ---
    roll_no = StringField("Roll Number", validators=[Optional(), Length(max=50)])

    # --- Staff-only ---
    # validate_choice is off because a student's form never submits this field
    # at all, which would otherwise fail the built-in choice check. Membership
    # in DESIGNATIONS is enforced for staff in validate_designation below.
    designation = SelectField(
        "Designation",
        choices=[("", "Select a designation")] + [(d, d) for d in DESIGNATIONS],
        validate_choice=False,
    )
    staff_id = StringField("Staff / Employee ID", validators=[Optional(), Length(max=50)])

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

    def validate_role(self, field) -> None:
        """Never trust the role the browser sent."""
        if field.data not in (Role.STUDENT, Role.STAFF):
            raise ValidationError("Choose either Student or Staff.")

    def validate_department(self, field) -> None:
        if self.role.data == Role.STAFF and not (field.data or "").strip():
            raise ValidationError("Department is required for staff accounts.")

    def validate_designation(self, field) -> None:
        if self.role.data == Role.STAFF:
            if not (field.data or "").strip():
                raise ValidationError("Designation is required for staff accounts.")
            if field.data not in DESIGNATIONS:
                raise ValidationError("Choose a designation from the list.")

    def validate_phone(self, field) -> None:
        if self.role.data == Role.STAFF and not (field.data or "").strip():
            raise ValidationError("Phone number is required for staff accounts.")


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


class OtpForm(FlaskForm):
    """Step two of signing in."""

    code = StringField(
        "Verification code",
        validators=[
            DataRequired(message="Enter the 6-digit code from your email."),
            Length(min=6, max=6, message="The code is 6 digits."),
            Regexp(r"^\d{6}$", message="The code is 6 digits."),
        ],
    )
    submit = SubmitField("Verify")


class StaffCreateForm(FlaskForm):
    """Admin-created staff account.

    No password field: the staff member sets their own via an emailed link, so
    a credential never travels through the admin or through email.
    """

    name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=190)],
        description="The real address of the staff member. They receive a setup link here.",
    )
    phone = StringField("Phone", validators=[DataRequired(), Length(max=30)])
    staff_id = StringField("Staff / Employee ID", validators=[Optional(), Length(max=50)])
    department = StringField("Department", validators=[DataRequired(), Length(max=120)])
    designation = SelectField(
        "Designation",
        choices=[("", "Select a designation")] + [(d, d) for d in DESIGNATIONS],
        validators=[DataRequired(message="Choose a designation.")],
    )
    role = SelectField(
        "Role",
        choices=[(Role.STAFF, "Staff"), (Role.ADMIN, "Administrator")],
        default=Role.STAFF,
        validators=[DataRequired()],
    )
    submit = SubmitField("Create Account")

    def validate_role(self, field) -> None:
        if field.data not in (Role.STAFF, Role.ADMIN):
            raise ValidationError("Choose a valid role.")


class StaffDecisionForm(FlaskForm):
    """Authorize, reject or suspend a staff account."""

    decision = SelectField(
        "Decision",
        choices=[
            (AuthorizationStatus.AUTHORIZED, "Authorize"),
            (AuthorizationStatus.REJECTED, "Reject"),
            (AuthorizationStatus.SUSPENDED, "Suspend"),
            (AuthorizationStatus.PENDING, "Return to pending"),
        ],
        validators=[DataRequired()],
    )
    note = TextAreaField(
        "Note (shown to the staff member)",
        validators=[Optional(), Length(max=500)],
    )
    submit = SubmitField("Apply")

    def validate_decision(self, field) -> None:
        if field.data not in AuthorizationStatus.ALL:
            raise ValidationError("Unknown decision.")


class SetPasswordForm(FlaskForm):
    """Used by a staff member opening their emailed setup link."""

    password = PasswordField(
        "Choose a password",
        validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Set Password")


class ResolveForm(FlaskForm):
    """Staff marking work complete, with proof-of-repair photos."""

    resolution_note = TextAreaField(
        "Resolution description",
        validators=[
            DataRequired(message="Describe what you did."),
            Length(min=10, max=2000),
        ],
    )
    resolution_photos = MultipleFileField(
        "Upload completion / resolution photo",
        validators=[
            FileAllowed(
                ["jpg", "jpeg", "png", "webp"],
                "Only JPG, JPEG, PNG and WEBP images are accepted.",
            )
        ],
    )
    submit = SubmitField("Mark Complaint as Resolved")

    #: Set by the view from config.
    max_photos = 5

    def validate_resolution_photos(self, field) -> None:
        chosen = [f for f in (field.data or []) if f and getattr(f, "filename", "")]
        if len(chosen) > self.max_photos:
            raise ValidationError(
                f"You can attach at most {self.max_photos} photos "
                f"(you selected {len(chosen)})."
            )


class ReopenForm(FlaskForm):
    """Student saying the problem is not actually fixed."""

    reason = TextAreaField(
        "What is still wrong?",
        validators=[
            DataRequired(message="Tell the staff member what is still wrong."),
            Length(min=5, max=500),
        ],
    )
    submit = SubmitField("Reopen Complaint")
