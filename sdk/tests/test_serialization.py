"""Unit tests for SDK model serialization, validation, and forward compatibility."""

from __future__ import annotations

import uuid
from datetime import datetime

from evalx.models import (
    Dataset,
    EvaluationRun,
    GateRule,
)


def test_dataset_model_ignores_unknown_extra_fields() -> None:
    raw_data = {
        "id": str(uuid.uuid4()),
        "name": "Dataset Alpha",
        "description": "Forward-compatible test",
        "case_count": 5,
        "version": 2,
        "created_at": "2026-09-22T10:00:00Z",
        "updated_at": "2026-09-22T10:30:00Z",
        # Server adds new future fields in Step 18+
        "future_feature_flag": True,
        "experimental_score_schema": {"tier": "enterprise"},
    }

    ds = Dataset.model_validate(raw_data)
    assert ds.name == "Dataset Alpha"
    assert ds.case_count == 5
    assert isinstance(ds.id, uuid.UUID)
    assert isinstance(ds.created_at, datetime)
    assert not hasattr(ds, "future_feature_flag")


def test_evaluation_run_serialization() -> None:
    run_id = uuid.uuid4()
    eval_id = uuid.uuid4()
    now = datetime.now()

    run = EvaluationRun(
        id=run_id,
        evaluation_id=eval_id,
        status="RUNNING",
        created_at=now,
        updated_at=now,
    )
    dumped = run.model_dump()
    assert dumped["id"] == run_id
    assert dumped["status"] == "RUNNING"


def test_gate_rule_defaults() -> None:
    rule = GateRule(metric="factuality", operator=">=", threshold=0.85)
    assert rule.required is True
    assert rule.threshold == 0.85
