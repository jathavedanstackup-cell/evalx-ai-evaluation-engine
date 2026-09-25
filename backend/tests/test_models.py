import uuid

from sqlalchemy.orm import configure_mappers
from sqlalchemy.sql.schema import CallableColumnDefault

from app.database.base import Base
from app.models import (
    Dataset,
    DatasetCase,
    Evaluation,
    EvaluationResult,
    EvaluationRun,
    EvaluatorConfig,
    User,
)


def test_mapper_configuration_succeeds() -> None:
    # Ensure all relationships, back_populates, and mapper configurations are valid
    configure_mappers()


def test_metadata_contains_all_seven_tables() -> None:
    expected_tables = {
        "users",
        "datasets",
        "dataset_cases",
        "evaluations",
        "evaluator_configs",
        "evaluation_runs",
        "evaluation_results",
    }
    assert expected_tables.issubset(Base.metadata.tables.keys())


def test_uuid_primary_key_defaults() -> None:
    for table_name in [
        "users",
        "datasets",
        "dataset_cases",
        "evaluations",
        "evaluator_configs",
        "evaluation_runs",
        "evaluation_results",
    ]:
        table = Base.metadata.tables[table_name]
        col = table.c.id
        assert col.primary_key is True
        assert isinstance(col.default, CallableColumnDefault)
        generated_id = col.default.arg(None)  # type: ignore[arg-type]
        assert isinstance(generated_id, uuid.UUID)


def test_user_model_instantiation() -> None:
    user_id = uuid.uuid4()
    user = User(id=user_id, email="evaluator@example.com")
    assert user.id == user_id
    assert user.email == "evaluator@example.com"


def test_dataset_and_case_relationship() -> None:
    dataset_id = uuid.uuid4()
    dataset = Dataset(
        id=dataset_id, name="Test Dataset", description="A dataset for testing"
    )
    assert dataset.id == dataset_id
    assert dataset.version == 1

    case_id = uuid.uuid4()
    case = DatasetCase(
        id=case_id,
        dataset_id=dataset.id,
        input="What is 2 + 2?",
        expected_output="4",
        context={"domain": "math"},
        metadata_={"difficulty": "easy"},
    )
    assert case.id == case_id
    assert case.context == {"domain": "math"}
    assert case.metadata_ == {"difficulty": "easy"}

    dataset.cases.append(case)
    assert len(dataset.cases) == 1
    assert dataset.cases[0].input == "What is 2 + 2?"

    rag_case = DatasetCase(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        input="What is the capital of France?",
        expected_output="Paris",
        context=["Paris is the capital of France.", "France is in Europe."],
        metadata_={"source": "geography_kb"},
    )
    assert rag_case.context == [
        "Paris is the capital of France.",
        "France is in Europe.",
    ]
    dataset.cases.append(rag_case)
    assert len(dataset.cases) == 2


def test_evaluation_and_evaluator_config() -> None:
    dataset_id = uuid.uuid4()
    eval_id = uuid.uuid4()
    evaluation = Evaluation(
        id=eval_id,
        name="Accuracy Eval",
        model_provider="openai",
        model_name="gpt-4o",
        dataset_id=dataset_id,
    )
    assert evaluation.id == eval_id
    assert evaluation.model_provider == "openai"

    config_id = uuid.uuid4()
    config = EvaluatorConfig(
        id=config_id,
        evaluation_id=evaluation.id,
        evaluator_type="factuality",
        enabled=True,
        configuration={"threshold": 0.8},
    )
    assert config.id == config_id
    assert config.enabled is True
    assert config.configuration == {"threshold": 0.8}

    evaluation.evaluator_configs.append(config)
    assert len(evaluation.evaluator_configs) == 1


def test_evaluation_run_and_result() -> None:
    eval_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = EvaluationRun(
        id=run_id,
        evaluation_id=eval_id,
        status="completed",
        overall_score=0.95,
    )
    assert run.id == run_id
    assert run.status == "completed"

    case_id = uuid.uuid4()
    result_id = uuid.uuid4()
    result = EvaluationResult(
        id=result_id,
        run_id=run.id,
        case_id=case_id,
        response="The answer is 4.",
        factuality_score=1.0,
        relevance_score=0.9,
        overall_score=0.95,
        feedback="Accurate and relevant response.",
    )
    assert result.id == result_id
    assert result.factuality_score == 1.0

    run.results.append(result)
    assert len(run.results) == 1
    assert run.results[0].overall_score == 0.95


def test_evaluation_result_unique_constraint() -> None:
    table = Base.metadata.tables["evaluation_results"]
    constraint_names = {c.name for c in table.constraints}
    assert "uq_result_run_case" in constraint_names


def test_user_unique_constraint() -> None:
    table = Base.metadata.tables["users"]
    constraint_names = {c.name for c in table.constraints}
    assert "uq_users_email" in constraint_names
