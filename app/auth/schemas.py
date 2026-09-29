"""Pydantic schemas for the auth API's requests, responses, and resolved identity.

`email` fields are plain `str` with a lightweight shape check, not Pydantic's `EmailStr` —
`EmailStr` requires the separate `email-validator` package, and full RFC email validation
isn't worth a new dependency here (an invalid email just fails at registration/login with
no matching user, which is already handled).
"""

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["admin", "user"]

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email_shape(value: str) -> str:
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("must be a valid email address")
    return value


class CurrentUser(BaseModel):
    """The authenticated caller, resolved from a validated access token."""

    id: uuid.UUID
    role: Role


class RegisterRequest(BaseModel):
    """A new-user registration request. Always registers with the `user` role."""

    email: str
    password: str = Field(min_length=8)

    _validate_email = field_validator("email")(_validate_email_shape)


class UserResponse(BaseModel):
    """A registered user's public profile."""

    id: uuid.UUID
    email: str
    role: Role
    is_active: bool


class LoginRequest(BaseModel):
    """An email + password login request."""

    email: str
    password: str

    _validate_email = field_validator("email")(_validate_email_shape)


class TokenResponse(BaseModel):
    """An issued access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class AuthActionResponse(BaseModel):
    """Returned by `POST /auth/login`/`/refresh` in place of `TokenResponse` (ERP-116).

    The access/refresh tokens are delivered as `httpOnly` cookies, never in this body.
    `user_id` is the one piece of (non-sensitive) identity data the frontend still needs
    synchronously, since it can no longer decode a JWT it never receives.

    `csrf_token` is also returned here, not just as a cookie -- a real fix for a design bug
    found live (2026-09-29): the double-submit pattern assumed frontend JS could read the
    `csrf_token` cookie via `document.cookie` and echo it back as a header, but `document.cookie`
    only ever exposes cookies belonging to the *current page's own origin*. Since the frontend
    (Vercel) and this API are different origins, that cookie is permanently invisible to the
    frontend's JS no matter what `SameSite`/`credentials` settings are used -- those govern
    whether the cookie gets *attached* to outgoing requests, a separate mechanism from whether
    script on another origin can *read* it. Returning the value here, in a response the frontend
    already legitimately reads cross-origin (it's its own fetch call), is what actually lets the
    frontend obtain a value to echo back at all.
    """

    user_id: uuid.UUID
    csrf_token: str


class UpdateUserActiveRequest(BaseModel):
    """An admin request to enable or disable a user account."""

    is_active: bool
