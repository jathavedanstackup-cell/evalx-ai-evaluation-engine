from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation_configuration import EvaluationConfig
from app.models.experiment import Experiment
from app.schemas.experiment import ExperimentCreate, ExperimentUpdate
from app.services.evaluation_run_service import get_evaluation_run


async def create_experiment(
    session: AsyncSession,
    data: ExperimentCreate,
    owner_user_id: UUID | None = None,
) -> Experiment:
    """Creates a new experiment grouping runs and configurations."""
    if data.configuration_id is not None:
        cfg_stmt = select(EvaluationConfig).where(
            EvaluationConfig.id == data.configuration_id
        )
        if owner_user_id is not None:
            cfg_stmt = cfg_stmt.where(EvaluationConfig.owner_user_id == owner_user_id)
        if (await session.execute(cfg_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evaluation configuration {data.configuration_id} not found",
            )

    if data.baseline_run_id is not None:
        run = await get_evaluation_run(
            session, data.baseline_run_id, owner_user_id=owner_user_id
        )
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Baseline evaluation run {data.baseline_run_id} not found",
            )

    experiment = Experiment(
        owner_user_id=owner_user_id,
        name=data.name,
        description=data.description,
        configuration_id=data.configuration_id,
        baseline_run_id=data.baseline_run_id,
        status="active",
    )
    session.add(experiment)
    await session.commit()
    await session.refresh(experiment)
    return experiment


async def get_experiment(
    session: AsyncSession,
    experiment_id: UUID,
    owner_user_id: UUID | None = None,
) -> Experiment | None:
    """Fetch an experiment with strict tenant scoping."""
    query = select(Experiment).where(Experiment.id == experiment_id)
    if owner_user_id is not None:
        query = query.where(Experiment.owner_user_id == owner_user_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def list_experiments(
    session: AsyncSession,
    owner_user_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    status_filter: str | None = None,
) -> tuple[list[Experiment], int]:
    """List tenant-scoped experiments with pagination, search, and status filter."""
    base_query = select(Experiment)
    count_query = select(func.count(Experiment.id))

    if owner_user_id is not None:
        base_query = base_query.where(Experiment.owner_user_id == owner_user_id)
        count_query = count_query.where(Experiment.owner_user_id == owner_user_id)

    if status_filter:
        base_query = base_query.where(Experiment.status == status_filter)
        count_query = count_query.where(Experiment.status == status_filter)

    if search:
        search_pattern = f"%{search.strip()}%"
        base_query = base_query.where(Experiment.name.ilike(search_pattern))
        count_query = count_query.where(Experiment.name.ilike(search_pattern))

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    query = (
        base_query.order_by(
            Experiment.created_at.desc(),
            Experiment.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items_result = await session.execute(query)
    items = list(items_result.scalars().all())
    return items, total


async def update_experiment(
    session: AsyncSession,
    experiment_id: UUID,
    data: ExperimentUpdate,
    owner_user_id: UUID | None = None,
) -> Experiment | None:
    """Updates an experiment."""
    experiment = await get_experiment(
        session, experiment_id, owner_user_id=owner_user_id
    )
    if experiment is None:
        return None

    if data.configuration_id is not None:
        cfg_stmt = select(EvaluationConfig).where(
            EvaluationConfig.id == data.configuration_id
        )
        if owner_user_id is not None:
            cfg_stmt = cfg_stmt.where(EvaluationConfig.owner_user_id == owner_user_id)
        if (await session.execute(cfg_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evaluation configuration {data.configuration_id} not found",
            )

    if data.baseline_run_id is not None:
        run = await get_evaluation_run(
            session, data.baseline_run_id, owner_user_id=owner_user_id
        )
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Baseline evaluation run {data.baseline_run_id} not found",
            )

    if data.name is not None:
        experiment.name = data.name
    if data.description is not None:
        experiment.description = data.description
    if data.configuration_id is not None:
        experiment.configuration_id = data.configuration_id
    if data.baseline_run_id is not None:
        experiment.baseline_run_id = data.baseline_run_id
    if data.status is not None:
        experiment.status = data.status

    await session.commit()
    await session.refresh(experiment)
    return experiment


async def delete_experiment(
    session: AsyncSession,
    experiment_id: UUID,
    owner_user_id: UUID | None = None,
) -> bool:
    """Deletes an experiment."""
    experiment = await get_experiment(
        session, experiment_id, owner_user_id=owner_user_id
    )
    if experiment is None:
        return False

    await session.delete(experiment)
    await session.commit()
    return True
