"""JWT issuing and verification.

The token carries the user's department. That single claim is what every
downstream filter, RLS context, and isolation assert reads. It is set once, at
login, from the database row - never from anything the client sends.

The previous implementation put the *email* in `sub` while the consumer looked
`sub` up by *username*, so every protected route 401'd. Here `sub` is the
username and the numeric id travels alongside in `uid`, so lookups are
unambiguous.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel, Field

from backend.config.config import settings
from backend.core.exceptions import TokenError

TokenType = Literal["access", "refresh"]


class TokenPayload(BaseModel):
    """Validated JWT contents.

    Field names are short because they travel on every request.
    """

    sub: str = Field(description="username")
    uid: int = Field(description="user id")
    dept: str = Field(description="department - the isolation boundary")
    role: str
    typ: TokenType
    jti: str
    exp: int
    iat: int

    @property
    def username(self) -> str:
        return self.sub

    @property
    def user_id(self) -> int:
        return self.uid

    @property
    def department(self) -> str:
        return self.dept


def _encode(
    *,
    username: str,
    user_id: int,
    department: str,
    role: str,
    token_type: TokenType,
    expires_delta: timedelta,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": username,
        "uid": user_id,
        "dept": department,
        "role": role,
        "typ": token_type,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(
    *, username: str, user_id: int, department: str, role: str
) -> str:
    return _encode(
        username=username,
        user_id=user_id,
        department=department,
        role=role,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(
    *, username: str, user_id: int, department: str, role: str
) -> str:
    return _encode(
        username=username,
        user_id=user_id,
        department=department,
        role=role,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, *, expected_type: TokenType = "access") -> TokenPayload:
    """Decode and validate a token.

    Raises :class:`TokenError` for anything malformed, expired, or of the wrong
    type. Accepting a refresh token where an access token is expected would let
    a long-lived credential be replayed against the API, so the type check is
    not optional.
    """
    try:
        raw = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except ExpiredSignatureError as exc:
        raise TokenError("Your session has expired. Please sign in again.") from exc
    except JWTError as exc:
        raise TokenError("Invalid authentication token.") from exc

    try:
        payload = TokenPayload(**raw)
    except Exception as exc:
        raise TokenError("Malformed authentication token.") from exc

    if payload.typ != expected_type:
        raise TokenError(
            f"Expected a {expected_type} token but received a {payload.typ} token."
        )

    return payload


def verify_access_token(token: str) -> TokenPayload | None:
    """Non-raising variant, for places where absence is not an error."""
    try:
        return decode_token(token, expected_type="access")
    except TokenError:
        return None
