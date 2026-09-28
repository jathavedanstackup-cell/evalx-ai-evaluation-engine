from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset, DatasetCase
from app.schemas.dataset import DatasetCreate, DatasetUpdate
from app.schemas.dataset_case import DatasetCaseCreate, DatasetCaseUpdate
from app.schemas.validation import DatasetValidationResult, ValidationIssue


async def create_dataset(
    session: AsyncSession,
    data: DatasetCreate,
    owner_user_id: UUID | None = None,
) -> Dataset:
    dataset = Dataset(
        name=data.name,
        description=data.description,
        version=1,
        owner_user_id=owner_user_id,
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


async def get_dataset(
    session: AsyncSession,
    dataset_id: UUID,
    owner_user_id: UUID | None = None,
) -> Dataset | None:
    query = select(Dataset).where(Dataset.id == dataset_id)
    if owner_user_id is not None:
        query = query.where(
            or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
        )
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def get_dataset_with_case_count(
    session: AsyncSession,
    dataset_id: UUID,
    owner_user_id: UUID | None = None,
) -> tuple[Dataset, int] | None:
    query = (
        select(Dataset, func.count(DatasetCase.id).label("case_count"))
        .outerjoin(DatasetCase, Dataset.id == DatasetCase.dataset_id)
        .where(Dataset.id == dataset_id)
    )
    if owner_user_id is not None:
        query = query.where(
            or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
        )
    query = query.group_by(Dataset.id)
    result = await session.execute(query)
    row = result.first()
    if row is None:
        return None
    dataset, count = row
    return dataset, int(count)


async def list_datasets(
    session: AsyncSession,
    owner_user_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> tuple[list[tuple[Dataset, int]], int]:
    base_query = select(Dataset)
    count_query = select(func.count(Dataset.id))

    if owner_user_id is not None:
        base_query = base_query.where(
            or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
        )
        count_query = count_query.where(
            or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
        )

    if search:
        search_pattern = f"%{search.strip()}%"
        base_query = base_query.where(Dataset.name.ilike(search_pattern))
        count_query = count_query.where(Dataset.name.ilike(search_pattern))

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    # Query items with joined case count to avoid N+1 queries
    query = select(Dataset, func.count(DatasetCase.id).label("case_count")).outerjoin(
        DatasetCase, Dataset.id == DatasetCase.dataset_id
    )
    if owner_user_id is not None:
        query = query.where(
            or_(Dataset.owner_user_id == owner_user_id, Dataset.owner_user_id.is_(None))
        )
    if search:
        query = query.where(Dataset.name.ilike(f"%{search.strip()}%"))

    query = (
        query.group_by(Dataset.id)
        .order_by(Dataset.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await session.execute(query)
    rows = [(row[0], int(row[1])) for row in result.all()]
    return rows, total


async def update_dataset(
    session: AsyncSession,
    dataset: Dataset,
    data: DatasetUpdate,
) -> Dataset:
    if data.name is not None:
        dataset.name = data.name
    if data.description is not None:
        dataset.description = data.description

    await session.commit()
    await session.refresh(dataset)
    return dataset


async def delete_dataset(
    session: AsyncSession,
    dataset: Dataset,
) -> None:
    await session.delete(dataset)
    await session.commit()


async def create_dataset_case(
    session: AsyncSession,
    dataset_id: UUID,
    data: DatasetCaseCreate,
) -> DatasetCase:
    case = DatasetCase(
        dataset_id=dataset_id,
        input=data.input,
        expected_output=data.expected_output,
        context=data.context,
        metadata_=data.metadata,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


async def get_dataset_case(
    session: AsyncSession,
    dataset_id: UUID,
    case_id: UUID,
) -> DatasetCase | None:
    query = select(DatasetCase).where(
        DatasetCase.id == case_id,
        DatasetCase.dataset_id == dataset_id,
    )
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def list_dataset_cases(
    session: AsyncSession,
    dataset_id: UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[DatasetCase], int]:
    count_query = select(func.count(DatasetCase.id)).where(
        DatasetCase.dataset_id == dataset_id
    )
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    query = (
        select(DatasetCase)
        .where(DatasetCase.dataset_id == dataset_id)
        .order_by(DatasetCase.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(query)
    cases = list(result.scalars().all())
    return cases, total


async def update_dataset_case(
    session: AsyncSession,
    case: DatasetCase,
    data: DatasetCaseUpdate,
) -> DatasetCase:
    if data.input is not None:
        case.input = data.input
    if data.expected_output is not None:
        case.expected_output = data.expected_output
    if data.context is not None:
        case.context = data.context
    if data.metadata is not None:
        case.metadata_ = data.metadata

    await session.commit()
    await session.refresh(case)
    return case


async def delete_dataset_case(
    session: AsyncSession,
    case: DatasetCase,
) -> None:
    await session.delete(case)
    await session.commit()


async def bulk_create_dataset_cases(
    session: AsyncSession,
    dataset_id: UUID,
    cases_data: list[DatasetCaseCreate],
) -> list[DatasetCase]:
    cases = [
        DatasetCase(
            dataset_id=dataset_id,
            input=c.input,
            expected_output=c.expected_output,
            context=c.context,
            metadata_=c.metadata,
        )
        for c in cases_data
    ]
    session.add_all(cases)
    await session.commit()
    for case in cases:
        await session.refresh(case)
    return cases


def validate_case_item(
    case: DatasetCase,
    index: int,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    # Check input
    if not case.input or not case.input.strip():
        issues.append(
            ValidationIssue(
                severity="error",
                case_index=index,
                field="input",
                message="Input query is required and cannot be blank",
            )
        )

    # Check context
    if case.context is not None:
        if isinstance(case.context, list):
            for ctx_idx, chunk in enumerate(case.context):
                if not isinstance(chunk, str) or not chunk.strip():
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            case_index=index,
                            field=f"context[{ctx_idx}]",
                            message="Context passage chunks must be non-empty strings",
                        )
                    )
        elif not isinstance(case.context, dict):
            issues.append(
                ValidationIssue(
                    severity="error",
                    case_index=index,
                    field="context",
                    message=(
                        "Context must be a list of string passages or a JSON document"
                    ),
                )
            )

    # Check metadata
    if case.metadata_ is not None and not isinstance(case.metadata_, dict):
        issues.append(
            ValidationIssue(
                severity="error",
                case_index=index,
                field="metadata",
                message="Metadata must be a key-value dictionary",
            )
        )

    return issues


async def validate_dataset(
    session: AsyncSession,
    dataset_id: UUID,
) -> DatasetValidationResult:
    # Fetch all cases for validation
    query = (
        select(DatasetCase)
        .where(DatasetCase.dataset_id == dataset_id)
        .order_by(DatasetCase.created_at.asc())
    )
    result = await session.execute(query)
    cases = list(result.scalars().all())

    issues: list[ValidationIssue] = []
    seen_inputs: dict[str, int] = {}
    cases_with_expected_output = 0
    cases_with_context = 0
    cases_with_metadata = 0

    for idx, case in enumerate(cases):
        case_issues = validate_case_item(case, idx)
        issues.extend(case_issues)

        # Statistics
        if case.expected_output and case.expected_output.strip():
            cases_with_expected_output += 1
        if case.context:
            cases_with_context += 1
        if case.metadata_:
            cases_with_metadata += 1

        # Duplicate detection within dataset
        normalized_input = case.input.strip().lower()
        if normalized_input in seen_inputs:
            first_idx = seen_inputs[normalized_input]
            issues.append(
                ValidationIssue(
                    severity="warning",
                    case_index=idx,
                    field="input",
                    message=(
                        f"Duplicate input detected; identical to case at index "
                        f"{first_idx}"
                    ),
                )
            )
        else:
            seen_inputs[normalized_input] = idx

    errors = [i for i in issues if i.severity == "error"]
    warnings_list = [i for i in issues if i.severity == "warning"]

    summary: dict[str, Any] = {
        "total_cases": len(cases),
        "cases_with_expected_output": cases_with_expected_output,
        "cases_with_context": cases_with_context,
        "cases_with_metadata": cases_with_metadata,
        "unique_inputs": len(seen_inputs),
    }

    return DatasetValidationResult(
        dataset_id=dataset_id,
        is_valid=len(errors) == 0,
        total_cases=len(cases),
        error_count=len(errors),
        warning_count=len(warnings_list),
        issues=issues,
        summary=summary,
    )
