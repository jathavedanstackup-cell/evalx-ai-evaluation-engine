# EVALX Python SDK (`evalx-sdk`)

The official Python client library for the **EVALX AI Evaluation & Reliability Platform**.

`evalx-sdk` provides a clean, fully typed, asynchronous and synchronous interface for benchmarking LLMs, managing evaluation datasets, defining regression gates, automating recurring evaluation runs, and diagnosing failures.

---

## Installation

```bash
pip install evalx-sdk
```

Requirements: Python `>= 3.11`, `httpx >= 0.27.0`, and `pydantic >= 2.0.0`.

---

## Quickstart

### Synchronous Client

```python
from evalx import EvalXClient

client = EvalXClient(
    base_url="http://localhost:8000",
    api_key="your_api_token",  # or export EVALX_API_KEY
)

# 1. Create a dataset
dataset = client.datasets.create(
    name="Customer Support QA",
    description="Validation set for refund and policy questions",
)

# 2. Add test cases
case = client.datasets.create_case(
    dataset_id=dataset.id,
    input="What is the refund window for annual subscriptions?",
    expected_output="Customers may request a full refund within 30 days of purchase.",
)

# 3. Create an evaluation run
run = client.evaluations.runs.create(
    evaluation_id="00000000-0000-0000-0000-000000000001",
    dataset_id=dataset.id,
)

# 4. Wait for run completion with bounded polling
completed_run = client.evaluations.runs.wait_for_completion(
    run.id,
    timeout_seconds=60.0,
    poll_interval_seconds=2.0,
)
print(f"Run {completed_run.id} finished with status: {completed_run.status}")
print(f"Aggregate Score: {completed_run.aggregate_score}")

# 5. Retrieve detailed results
results = client.evaluations.runs.get_results(run.id)
for res in results.results:
    print(f"Case {res.case_id} Passed: {res.passed} (Score: {res.score})")
```

---

### Asynchronous Client

```python
import asyncio
from evalx import AsyncEvalXClient


async def main():
    async with AsyncEvalXClient(base_url="http://localhost:8000") as client:
        # List all datasets
        page = await client.datasets.list(page=1, page_size=10)
        for ds in page.items:
            print(f"- {ds.name} ({ds.case_count} cases)")


asyncio.run(main())
```

---

## Feature Overview

### 1. Dataset Management & Ingestion

```python
# Create dataset
dataset = client.datasets.create(name="RAG Medical Q&A")

# Bulk ingest test cases atomically
client.datasets.bulk_create_cases(
    dataset_id=dataset.id,
    cases=[
        {
            "input": "Symptoms of acute bronchitis",
            "expected_output": "Persistent cough, mucus, fatigue, mild fever",
            "context": ["Medical textbook excerpt on respiratory conditions..."],
        },
        {
            "input": "Recommended dosage for amoxicillin",
            "expected_output": "Consult prescribing physician for dosage",
        },
    ],
)

# Validate dataset integrity (completeness, duplicates)
report = client.datasets.validate(dataset.id)
print(f"Valid: {report.is_valid}, Issues: {len(report.issues)}")
```

---

### 2. Evaluation Configurations & Presets

```python
from evalx.models import EvaluatorSpec

config = client.configurations.create(
    name="Strict Factuality & Relevance",
    preset_name="rag_strict",
    evaluators=[
        EvaluatorSpec(
            evaluator_type="exact_match",
            metric_name="exact_match",
            threshold=1.0,
        ),
        EvaluatorSpec(
            evaluator_type="llm_judge",
            metric_name="factuality",
            threshold=0.85,
        ),
    ],
)

# Retrieve historical version snapshot
v1 = client.configurations.get(config.id, version=1)
```

---

### 3. Run Comparisons & Regression Gates

```python
# Compare candidate run against baseline
comparison = client.evaluations.compare(
    baseline_run_id="00000000-0000-0000-0000-000000000001",
    candidate_run_id="00000000-0000-0000-0000-000000000002",
)
print(f"Score Diff: {comparison.score_difference:+.4f}")
print(f"Improved Cases: {len(comparison.improved_cases)}")
print(f"Regressed Cases: {len(comparison.regressed_cases)}")

# Evaluate against regression gate
gate_result = client.regression_gates.evaluate(
    gate_id="00000000-0000-0000-0000-000000000003",
    candidate_run_id=candidate_run.id,
)
if not gate_result.passed:
    print(f"Gate Failed! Reasons: {gate_result.failure_reasons}")
```

---

### 4. Failure Analysis & Diagnostics

```python
analysis = client.evaluations.runs.get_analysis(run.id)
print(f"Failure Rate: {analysis.failure_rate:.1%}")
for cluster in analysis.clusters:
    print(f"Cluster '{cluster.name}': {cluster.case_count} cases affected")
    print(f"  Summary: {cluster.description}")
```

---

### 5. Automated Evaluation Schedules

```python
# Create daily evaluation schedule
schedule = client.schedules.create(
    name="Nightly RAG Evaluation",
    schedule_type="daily",
    schedule_definition={"time_of_day": "02:00", "timezone": "UTC"},
    dataset_id=dataset.id,
    configuration_id=config.id,
)

# Trigger immediate execution outside schedule
execution = client.schedules.trigger(schedule.id)

# Pause / Resume
client.schedules.disable(schedule.id)
client.schedules.enable(schedule.id)
```

---

### 6. Audit Trail & Metrics

```python
# List audit events
events = client.audit.list(action="RUN_STARTED", page_size=20)
for evt in events:
    print(
        f"[{evt.timestamp}] {evt.action} on {evt.resource_type} (CID: {evt.correlation_id})"
    )

# Real-time metrics snapshot
metrics = client.audit.get_metrics()
print(
    f"Total Runs: {metrics.total_runs_completed} completed, {metrics.total_runs_failed} failed"
)
```

---

## Advanced Usage

### Bounded Pagination Helper

```python
from evalx import paginate_all

# Safely iterate through items across all pages (capped at max_items=500)
for case in paginate_all(
    lambda page, size: client.datasets.list_cases(
        dataset.id, page=page, page_size=size
    ),
    max_items=500,
):
    print(case.input)
```

### Exception Handling

All EVALX errors are typed and carry correlation IDs:

```python
from evalx.exceptions import (
    EvalXError,
    EvalXNotFoundError,
    EvalXRateLimitError,
    EvalXValidationError,
)

try:
    client.datasets.get("non-existent-id")
except EvalXNotFoundError as exc:
    print(f"Resource not found (CID: {exc.correlation_id})")
except EvalXRateLimitError as exc:
    print(f"Rate limited! Retry after {exc.retry_after} seconds.")
except EvalXValidationError as exc:
    print(f"Validation errors: {exc.errors}")
except EvalXError as exc:
    print(f"EVALX API error: {exc.message} (HTTP {exc.status_code})")
```

### Retries & Timeouts

```python
client = EvalXClient(
    timeout=15.0,  # Total request timeout in seconds
    connect_timeout=5.0,  # TCP connect timeout
    max_retries=3,  # Retries only safe/idempotent operations
    retry_backoff_factor=0.5,
)
```

---

## Security & Privacy Guarantees

1. **No Token Leakage**: The client never includes bearer tokens, passwords, or secret credentials in exception messages, string representations (`repr(client)` masks the key with `***`), or diagnostic outputs.
2. **Idempotency Safeguards**: Non-idempotent operations (`POST /evaluation-runs`) are never retried automatically on transport errors to prevent unintentional duplicate evaluation executions.
3. **Bounded Polling**: `wait_for_completion` requires a strict `timeout_seconds` boundary, preventing indefinite blocking.

