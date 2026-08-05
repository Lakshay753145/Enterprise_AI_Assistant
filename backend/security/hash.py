"""Password hashing and strength policy."""

from __future__ import annotations

import re
import secrets

from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)

#: bcrypt silently truncates at 72 bytes; reject rather than truncate so a user
#: never ends up with a password that is not the one they typed.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 10

#: A well-formed bcrypt hash of a value nobody knows, generated at import.
#: Used by dummy_verify() to burn identical CPU on the "no such user" path.
_DUMMY_HASH = pwd_context.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verification. Never raises on malformed hashes."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """True when the stored hash uses outdated parameters."""
    try:
        return pwd_context.needs_update(hashed_password)
    except Exception:
        return False


def dummy_verify() -> None:
    """Burn the same CPU as a real verification.

    Called on the "user not found" path so the response time does not reveal
    whether a username exists.
    """
    try:
        pwd_context.verify("not-a-real-password", _DUMMY_HASH)
    except Exception:
        # Must never propagate - this runs on the login failure path, where an
        # exception would turn a normal "wrong username" into a 500 and, worse,
        # reveal by its timing that the username did not exist.
        pass


def validate_password_strength(password: str) -> list[str]:
    """Return a list of policy failures. Empty list means the password is fine."""
    problems: list[str] = []

    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        problems.append(
            f"Password must not exceed {MAX_PASSWORD_BYTES} bytes."
        )
    if not re.search(r"[A-Z]", password):
        problems.append("Password must contain an uppercase letter.")
    if not re.search(r"[a-z]", password):
        problems.append("Password must contain a lowercase letter.")
    if not re.search(r"\d", password):
        problems.append("Password must contain a digit.")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("Password must contain a symbol.")

    lowered = password.lower()
    for common in ("password", "aerolloy", "12345678", "qwerty", "admin"):
        if common in lowered:
            problems.append(f"Password must not contain '{common}'.")
            break

    return problems


def generate_password(length: int = 16) -> str:
    """Generate a strong password for admin-provisioned accounts."""
    alphabet = (
        "abcdefghijkmnopqrstuvwxyz"
        "ABCDEFGHJKLMNPQRSTUVWXYZ"
        "23456789"
        "!@#$%^&*-_=+"
    )
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if not validate_password_strength(candidate):
            return candidate
