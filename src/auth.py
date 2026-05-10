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


def login(email: str, password: str) -> dict | None:
    """Return user dict if credentials are valid and account is active, else None."""
    user = get_user_by_email(email.strip().lower())
    if not user:
        return None
    if not user["is_active"]:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def register(name: str, email: str, password: str,
             company: str = "") -> tuple[bool, str]:
    """Create a new user. Returns (success, message)."""
    email = email.strip().lower()
    if not name.strip():
        return False, "Name is required."
    if "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if get_user_by_email(email):
        return False, "An account with this email already exists."

    # First user ever becomes admin automatically
    role = "admin" if count_users() == 0 else "user"
    pw_hash = hash_password(password)
    create_user(name.strip(), email, pw_hash, company.strip(), role)
    return True, role
