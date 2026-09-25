import logging
from typing import Any

import jwt
from fastapi import Request

from app.auth.errors import AuthenticationError
from app.auth.principal import AuthPrincipal
from app.auth.provider import AuthProvider
from app.core.config import settings

logger = logging.getLogger(__name__)


def _format_pem_key(key: str) -> str:
    """Ensures a PEM public key string has standard PEM header and footer."""
    stripped = key.strip().replace("\\n", "\n")
    if "BEGIN PUBLIC KEY" in stripped:
        return stripped
    # Wrap raw base64 key in PEM boundaries
    clean_lines = [stripped[i : i + 64] for i in range(0, len(stripped), 64)]
    return (
        "-----BEGIN PUBLIC KEY-----\n"
        + "\n".join(clean_lines)
        + "\n-----END PUBLIC KEY-----\n"
    )


class ClerkAuthProvider(AuthProvider):
    """Production Clerk auth provider supporting networkless JWT verification."""

    def __init__(
        self,
        secret_key: str | None = None,
        jwt_key: str | None = None,
        authorized_parties: list[str] | None = None,
    ) -> None:
        self._secret_key = secret_key or settings.clerk_secret_key
        self._jwt_key = jwt_key or settings.clerk_jwt_key
        self._authorized_parties = (
            authorized_parties
            if authorized_parties is not None
            else settings.authorized_parties_list
        )

    async def authenticate_request(self, request: Request) -> AuthPrincipal:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise AuthenticationError("Missing Authorization header")

        if not auth_header.startswith("Bearer "):
            raise AuthenticationError("Malformed Authorization header")

        token = auth_header[7:].strip()
        if not token:
            raise AuthenticationError("Empty Bearer token")

        # Networkless JWT verification when CLERK_JWT_KEY is provided
        if self._jwt_key:
            return self._verify_jwt_token(token)

        # Fallback to Clerk SDK request authentication
        return await self._verify_via_clerk_sdk(request)

    def _verify_jwt_token(self, token: str) -> AuthPrincipal:
        assert self._jwt_key is not None
        pem_key = _format_pem_key(self._jwt_key)

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                pem_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Invalid or tampered token: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected JWT verification failure: %s", exc)
            raise AuthenticationError("Authentication failed") from exc

        # Verify authorized parties (azp claim) if configured
        if self._authorized_parties:
            azp = payload.get("azp")
            if azp not in self._authorized_parties:
                logger.warning(
                    "Token azp claim '%s' not in authorized parties: %s",
                    azp,
                    self._authorized_parties,
                )
                raise AuthenticationError("Unauthorized party (azp claim mismatch)")

        sub = payload.get("sub")
        if not sub:
            raise AuthenticationError("Token missing required 'sub' claim")

        return AuthPrincipal(
            provider="clerk",
            external_user_id=str(sub),
            session_id=str(payload.get("sid")) if payload.get("sid") else None,
            is_authenticated=True,
            email=payload.get("email"),
        )

    async def _verify_via_clerk_sdk(self, request: Request) -> AuthPrincipal:
        try:
            from clerk_backend_api import Clerk
            from clerk_backend_api.security import AuthenticateRequestOptions

            clerk = Clerk(bearer_auth=self._secret_key or "")
            opts = AuthenticateRequestOptions(
                authorized_parties=self._authorized_parties or None
            )
            state = clerk.authenticate_request(request, opts)
            if not getattr(state, "is_signed_in", False):
                raise AuthenticationError("Invalid or unsigned Clerk session")

            payload = getattr(state, "payload", {}) or {}
            sub = payload.get("sub") or getattr(state, "user_id", None)
            if not sub:
                raise AuthenticationError("Missing user identity in Clerk session")

            return AuthPrincipal(
                provider="clerk",
                external_user_id=str(sub),
                session_id=str(payload.get("sid")) if payload.get("sid") else None,
                is_authenticated=True,
                email=payload.get("email"),
            )
        except AuthenticationError:
            raise
        except Exception as exc:
            logger.error("Clerk SDK authentication error: %s", exc)
            raise AuthenticationError(f"Authentication failed: {exc}") from exc
