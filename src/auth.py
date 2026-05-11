"""Authentication helpers — bcrypt password hashing + login/register."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from src.database import (
    count_users, create_user, get_user_by_email,
    create_reset_token, get_reset_token, mark_token_used, update_user_password,
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def login(email: str, password: str) -> tuple[dict | None, str]:
    """Return (user, "") on success, or (None, reason) on failure.

    Reasons: "invalid" | "pending" | "disabled"
    """
    user = get_user_by_email(email.strip().lower())
    if not user:
        return None, "invalid"
    if not verify_password(password, user["password_hash"]):
        return None, "invalid"
    status = user.get("status", "active")
    if status == "pending_approval":
        return None, "pending"
    if status == "disabled" or not user["is_active"]:
        return None, "disabled"
    return user, ""


def request_password_reset(email: str) -> None:
    """Generate a reset token and email it. Always returns silently (don't reveal if email exists)."""
    user = get_user_by_email(email.strip().lower())
    if not user:
        return
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    create_reset_token(user["id"], token, expires_at)
    _send_reset_email(user["email"], user["name"], token)


def _send_reset_email(email: str, name: str, token: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    base_url = os.environ.get("APP_URL", "https://review.atechsustainability.com")
    reset_url = f"{base_url}?page=reset&token={token}"
    from_addr = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")

    if not api_key:
        return

    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": f"ATECH Audit Reviewer <{from_addr}>",
            "to": [email],
            "subject": "Reset your ATECH password",
            "html": (
                f"<p>Hi {name},</p>"
                f"<p>You requested a password reset. Click the link below to set a new password. "
                f"This link is valid for <strong>1 hour</strong>.</p>"
                f"<p><a href='{reset_url}' style='background:#007B8A;color:white;padding:10px 20px;"
                f"border-radius:6px;text-decoration:none;'>Reset Password</a></p>"
                f"<p>If you did not request this, ignore this email — your password will not change.</p>"
                f"<p>— ATECH Sustainability Consultancy</p>"
            ),
        })
    except Exception:
        pass


def reset_password(token: str, new_password: str) -> tuple[bool, str]:
    """Validate token and update password. Returns (success, message)."""
    row = get_reset_token(token)
    if not row or row["used"]:
        return False, "This reset link is invalid or has already been used."
    expires = datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return False, "This reset link has expired. Please request a new one."
    if len(new_password) < 8:
        return False, "Password must be at least 8 characters."
    pw_hash = hash_password(new_password)
    update_user_password(row["user_id"], pw_hash)
    mark_token_used(token)
    return True, "Password updated successfully. You can now sign in."


def register(name: str, email: str, password: str,
             company: str = "") -> tuple[bool, str]:
    """Create a new customer account pending staff approval.

    Returns (success, message).
    On success message is "pending" (awaiting approval) or "superadmin" (env-seeded account).
    """
    email = email.strip().lower()
    if not name.strip():
        return False, "Name is required."
    if "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if get_user_by_email(email):
        return False, "An account with this email already exists."

    pw_hash = hash_password(password)
    create_user(name.strip(), email, pw_hash, company.strip(),
                role="customer", status="pending_approval")
    return True, "pending"
