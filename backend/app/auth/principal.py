from dataclasses import dataclass


@dataclass(frozen=True)
class AuthPrincipal:
    """Internal provider-neutral representation of an authenticated user principal."""

    provider: str
    external_user_id: str
    session_id: str | None = None
    is_authenticated: bool = True
    email: str | None = None
