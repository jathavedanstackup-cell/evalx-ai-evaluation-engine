from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.snapshot import compute_configuration_snapshot
from app.models.evaluation_configuration import (
    EvaluationConfig,
    EvaluationConfigVersion,
)
from app.schemas.evaluation_config import (
    EvaluationConfigCreate,
    EvaluationConfigUpdate,
)


async def create_configuration(
    session: AsyncSession,
    data: EvaluationConfigCreate,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> EvaluationConfig:
    """Create a new evaluation configuration preset and record its initial version."""
    evaluators_dicts = [e.model_dump() for e in data.evaluators]
    _, snapshot_hash = compute_configuration_snapshot(
        name=data.name,
        description=data.description,
        version=1,
        evaluators=evaluators_dicts,
    )

    config = EvaluationConfig(
        name=data.name,
        description=data.description,
        version=1,
        evaluators=evaluators_dicts,
        snapshot_hash=snapshot_hash,
        owner_user_id=owner_user_id,
    )
    session.add(config)
    await session.flush()

    # Record initial immutable version record
    config_version = EvaluationConfigVersion(
        config_id=config.id,
        version=1,
        name=data.name,
        description=data.description,
        evaluators=evaluators_dicts,
        snapshot_hash=snapshot_hash,
    )
    session.add(config_version)

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.CONFIG_CREATED,
        correlation_id=correlation_id or "evalx-config",
        resource_type="config",
        resource_id=config.id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
        metadata={"name": config.name, "version": config.version},
    )

    await session.commit()
    await session.refresh(config)
    return config


async def get_configuration(
    session: AsyncSession,
    config_id: UUID,
    owner_user_id: UUID | None = None,
    version: int | None = None,
) -> EvaluationConfig | EvaluationConfigVersion | None:
    """Retrieve an evaluation configuration.

    If version is specified, fetches the historical snapshot matching that version.
    Otherwise fetches the latest configuration entity.
    Enforces tenant isolation by owner_user_id.
    """
    if version is not None:
        query = (
            select(EvaluationConfigVersion)
            .join(
                EvaluationConfig,
                EvaluationConfigVersion.config_id == EvaluationConfig.id,
            )
            .where(
                EvaluationConfigVersion.config_id == config_id,
                EvaluationConfigVersion.version == version,
            )
        )
        if owner_user_id is not None:
            query = query.where(EvaluationConfig.owner_user_id == owner_user_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()

    base_query = select(EvaluationConfig).where(EvaluationConfig.id == config_id)
    if owner_user_id is not None:
        base_query = base_query.where(EvaluationConfig.owner_user_id == owner_user_id)
    result = await session.execute(base_query)
    return result.scalar_one_or_none()


async def list_configurations(
    session: AsyncSession,
    owner_user_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> tuple[list[EvaluationConfig], int]:
    """List tenant-scoped evaluation configurations with pagination and search."""
    base_query = select(EvaluationConfig)
    count_query = select(func.count(EvaluationConfig.id))

    if owner_user_id is not None:
        base_query = base_query.where(EvaluationConfig.owner_user_id == owner_user_id)
        count_query = count_query.where(EvaluationConfig.owner_user_id == owner_user_id)

    if search:
        search_pattern = f"%{search.strip()}%"
        base_query = base_query.where(EvaluationConfig.name.ilike(search_pattern))
        count_query = count_query.where(EvaluationConfig.name.ilike(search_pattern))

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    query = (
        base_query.order_by(
            EvaluationConfig.created_at.desc(),
            EvaluationConfig.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items_result = await session.execute(query)
    items = list(items_result.scalars().all())
    return items, total


async def update_configuration(
    session: AsyncSession,
    config_id: UUID,
    data: EvaluationConfigUpdate,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> EvaluationConfig | None:
    """Update an evaluation configuration by incrementing its version.

    Preserves previous versions in evaluation_config_versions for
    immutable reproducibility.
    """
    config_query = select(EvaluationConfig).where(EvaluationConfig.id == config_id)
    if owner_user_id is not None:
        config_query = config_query.where(
            EvaluationConfig.owner_user_id == owner_user_id
        )
    result = await session.execute(config_query)
    config = result.scalar_one_or_none()
    if config is None:
        return None

    new_name = data.name if data.name is not None else config.name
    new_desc = data.description if data.description is not None else config.description
    new_evaluators: list[dict[str, Any]]
    if data.evaluators is not None:
        new_evaluators = [e.model_dump() for e in data.evaluators]
    else:
        new_evaluators = list(config.evaluators)

    new_version = config.version + 1
    _, snapshot_hash = compute_configuration_snapshot(
        name=new_name,
        description=new_desc,
        version=new_version,
        evaluators=new_evaluators,
    )

    config.name = new_name
    config.description = new_desc
    config.evaluators = new_evaluators
    config.version = new_version
    config.snapshot_hash = snapshot_hash

    # Record the new immutable version
    new_version_record = EvaluationConfigVersion(
        config_id=config.id,
        version=new_version,
        name=new_name,
        description=new_desc,
        evaluators=new_evaluators,
        snapshot_hash=snapshot_hash,
    )
    session.add(new_version_record)

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.CONFIG_UPDATED,
        correlation_id=correlation_id or "evalx-config",
        resource_type="config",
        resource_id=config.id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
        metadata={"name": config.name, "version": config.version},
    )

    await session.commit()
    await session.refresh(config)
    return config


async def delete_configuration(
    session: AsyncSession,
    config_id: UUID,
    owner_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> bool:
    """Delete an evaluation configuration and cascade to its version history."""
    config_query = select(EvaluationConfig).where(EvaluationConfig.id == config_id)
    if owner_user_id is not None:
        config_query = config_query.where(
            EvaluationConfig.owner_user_id == owner_user_id
        )
    result = await session.execute(config_query)
    config = result.scalar_one_or_none()
    if config is None:
        return False

    from app.observability.events import EvaluationEventType
    from app.services import audit_service

    await audit_service.record_audit_event(
        session=session,
        event_type=EvaluationEventType.CONFIG_DELETED,
        correlation_id=correlation_id or "evalx-config",
        resource_type="config",
        resource_id=config_id,
        actor_user_id=owner_user_id,
        owner_user_id=owner_user_id,
        outcome="success",
    )

    await session.delete(config)
    await session.commit()
    return True
