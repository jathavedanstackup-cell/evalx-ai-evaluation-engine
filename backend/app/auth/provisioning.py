import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)


async def get_or_create_user(
    session: AsyncSession,
    external_auth_id: str,
    email: str | None = None,
) -> User:
    """Safely retrieves or provisions a local User by external_auth_id.

    Guarantees:
    - Primary lookup by external_auth_id.
    - Race-condition safety: on insert conflict, rolls back and re-fetches.
    - Never creates duplicate users.
    - Never falls back to email matching for identity/ownership.
    """
    # 1. Lookup existing user by external_auth_id
    stmt = select(User).where(User.external_auth_id == external_auth_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    # 2. Provision new local user
    try:
        user = User(external_auth_id=external_auth_id, email=email)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info(
            "Provisioned new local user %s for external_auth_id %s",
            user.id,
            external_auth_id,
        )
        return user
    except IntegrityError:
        # Concurrent insertion conflict: rollback failed insert and re-query
        await session.rollback()
        logger.debug(
            "Concurrent provisioning conflict for %s; re-fetching.",
            external_auth_id,
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is not None:
            return user
        raise
