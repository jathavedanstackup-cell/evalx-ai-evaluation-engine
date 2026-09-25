# Changelog

All notable changes to the EVALX Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-22

### Added
- Initial release of the official EVALX Python SDK (`evalx-sdk`).
- Dual client support: `EvalXClient` (synchronous) and `AsyncEvalXClient` (asynchronous).
- Typed resource clients:
  - `client.datasets`: dataset CRUD, test cases, atomic bulk ingestion, and dataset validation.
  - `client.evaluations`: evaluation runs, execution results, run comparisons, failure analysis, cancellation, and audit trail.
  - `client.configurations`: evaluation configuration presets, custom evaluators, and version history.
  - `client.regression_gates`: regression gate management, versioning, and baseline-vs-candidate evaluations.
  - `client.schedules`: automated evaluation schedules, pause/resume, manual triggers, and execution history.
  - `client.audit`: tenant audit trail queries and real-time metrics snapshots.
- Bounded polling support via `client.evaluations.wait_for_completion()` with configurable timeouts and intervals.
- Robust HTTP transport with automatic correlation ID propagation (`X-Correlation-ID`).
- Idempotency-guarded automatic retries with exponential backoff for transient 429/502/503/504 errors.
- Strict security guardrails preventing token leakage in exceptions, representations, and logs.
- Typed exception hierarchy mapping all EVALX HTTP status codes (`EvalXValidationError`, `EvalXNotFoundError`, `EvalXRateLimitError`, etc.).
- Safe, bounded pagination helpers with `Page[T]` and `.iter_all()`.

