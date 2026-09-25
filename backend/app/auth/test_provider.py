from fastapi import Request

from app.auth.errors import AuthenticationError
from app.auth.principal import AuthPrincipal
from app.auth.provider import AuthProvider


class TestAuthProvider(AuthProvider):
    """Deterministic fake authentication provider for test suites.

    Eliminates monkey-patching and real Clerk calls during testing.
    Supports:
    - Default authenticated test user when no header is supplied (if set)
    - Bearer <user_id> header for switching tenant users (e.g. user-a vs user-b)
    - Explicit failure tokens ("invalid", "expired", "tampered")
    """

    __test__ = False

    def __init__(self, default_user_id: str | None = "test-user-default") -> None:
        self.default_user_id = default_user_id

    async def authenticate_request(self, request: Request) -> AuthPrincipal:
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            if self.default_user_id is not None:
                return AuthPrincipal(
                    provider="test",
                    external_user_id=self.default_user_id,
                    session_id=f"sess-{self.default_user_id}",
                    is_authenticated=True,
                )
            raise AuthenticationError("Missing Authorization header")

        if not auth_header.startswith("Bearer "):
            raise AuthenticationError("Malformed Authorization header")

        token = auth_header[7:].strip()
        if not token or token.lower() in ("invalid", "tampered"):
            raise AuthenticationError("Invalid or tampered token")
        if token.lower() == "expired":
            raise AuthenticationError("Token has expired")

        return AuthPrincipal(
            provider="test",
            external_user_id=token,
            session_id=f"sess-{token}",
            is_authenticated=True,
        )


def make_test_auth_headers(user_id: object = "test-user-default") -> dict[str, str]:
    """Helper returning standard Bearer authorization headers for test requests."""
    return {"Authorization": f"Bearer {user_id}"}
