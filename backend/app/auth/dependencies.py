from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.clerk import ClerkAuthProvider
from app.auth.errors import AuthenticationError
from app.auth.principal import AuthPrincipal
from app.auth.provider import AuthProvider
from app.auth.provisioning import get_or_create_user
from app.database.session import get_async_session
from app.models.user import User


def get_auth_provider() -> AuthProvider:
    """Dependency providing the configured AuthProvider (default: ClerkAuthProvider)."""
    return ClerkAuthProvider()


async def get_auth_principal(
    request: Request,
    provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> AuthPrincipal:
    """Dependency authenticating the incoming request and returning an AuthPrincipal.

    Raises HTTP 401 Unauthorized on missing, invalid, or expired tokens.
    """
    try:
        return await provider.authenticate_request(request)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unauthorized: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    principal: Annotated[AuthPrincipal, Depends(get_auth_principal)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    """Dependency resolving or provisioning local EVALX User for principal."""
    return await get_or_create_user(
        session=session,
        external_auth_id=principal.external_user_id,
        email=principal.email,
    )
