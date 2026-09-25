from typing import Protocol

from fastapi import Request

from app.auth.principal import AuthPrincipal


class AuthProvider(Protocol):
    """Abstract provider interface for authenticating incoming HTTP requests."""

    async def authenticate_request(self, request: Request) -> AuthPrincipal:
        """Authenticates incoming HTTP request and returns an AuthPrincipal.

        Raises AuthenticationError if credentials are missing, expired, or invalid.
        """
        ...
