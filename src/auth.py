"""Authentication helpers — bcrypt password hashing + login/register."""

from __future__ import annotations

import bcrypt

from src.database import (
    count_users, create_user, get_user_by_email,
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
