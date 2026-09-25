import hashlib
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID


def compute_dataset_snapshot_hash(cases: Sequence[Any]) -> str:
    """Computes a deterministic SHA-256 hex digest representing a dataset snapshot.

    The hash is calculated over the ordered set of cases (sorted by case ID),
    incorporating:
      - case_id
      - input
      - context
      - expected_output
      - metadata

    Uses canonical JSON serialization (sorted keys, compact separators).
    """
    case_records: list[dict[str, Any]] = []

    for case in cases:
        if isinstance(case, dict):
            raw_id = case.get("id") or case.get("case_id")
            case_id_str = str(raw_id) if raw_id is not None else ""
            c_input = case.get("input", "")
            c_context = case.get("context")
            c_expected = case.get("expected_output")
            c_meta = case.get("metadata_") or case.get("metadata")
        else:
            raw_id = getattr(case, "id", None) or getattr(case, "case_id", None)
            case_id_str = str(raw_id) if raw_id is not None else ""
            c_input = getattr(case, "input", "")
            c_context = getattr(case, "context", None)
            c_expected = getattr(case, "expected_output", None)
            c_meta = getattr(case, "metadata_", None)
            if c_meta is None:
                raw_meta = getattr(case, "metadata", None)
                if isinstance(raw_meta, dict):
                    c_meta = raw_meta

        case_records.append(
            {
                "case_id": case_id_str,
                "input": c_input,
                "context": c_context,
                "expected_output": c_expected,
                "metadata": c_meta,
            }
        )

    # Sort deterministically by case_id
    case_records.sort(key=lambda r: r["case_id"])

    def _json_default(obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        return str(obj)

    canonical_json = json.dumps(
        case_records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )

    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


_SENSITIVE_KEY_SUBSTRINGS = (
    "key",
    "secret",
    "token",
    "password",
    "credential",
    "auth",
)


def _sanitize_config_dict(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not cfg:
        return {}
    sanitized: dict[str, Any] = {}
    for k, v in cfg.items():
        k_lower = str(k).lower()
        if any(s in k_lower for s in _SENSITIVE_KEY_SUBSTRINGS):
            sanitized[str(k)] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[str(k)] = _sanitize_config_dict(v)
        else:
            sanitized[str(k)] = v
    return sanitized


def compute_configuration_snapshot(
    name: str,
    description: str | None,
    version: int,
    evaluators: Sequence[Any],
) -> tuple[dict[str, Any], str]:
    """Computes a deterministic canonical configuration snapshot and its SHA-256 hash.

    Requirements:
      - Stable field ordering
      - Deterministic serialization (sorted keys, compact separators)
      - No secrets or credentials (sanitized configuration values)
      - Bounded snapshot size
    """
    clean_evaluators: list[dict[str, Any]] = []

    for ev in evaluators:
        if isinstance(ev, dict):
            e_type = str(ev.get("evaluator_type", ""))
            e_backend = str(ev.get("backend", "llm_judge"))
            e_thresh = ev.get("threshold")
            e_weight = float(ev.get("weight", 1.0))
            e_enabled = bool(ev.get("enabled", True))
            e_name = str(ev.get("name") or e_type)
            e_cfg = _sanitize_config_dict(ev.get("configuration"))
        else:
            e_type = str(getattr(ev, "evaluator_type", ""))
            e_backend = str(getattr(ev, "backend", "llm_judge"))
            e_thresh = getattr(ev, "threshold", None)
            e_weight = float(getattr(ev, "weight", 1.0))
            e_enabled = bool(getattr(ev, "enabled", True))
            e_name = str(getattr(ev, "name", None) or e_type)
            e_cfg = _sanitize_config_dict(getattr(ev, "configuration", None))

        clean_evaluators.append(
            {
                "backend": e_backend,
                "configuration": e_cfg,
                "enabled": e_enabled,
                "evaluator_type": e_type,
                "name": e_name,
                "threshold": e_thresh,
                "weight": e_weight,
            }
        )

    # Deterministically sort evaluators
    clean_evaluators.sort(key=lambda e: (e["evaluator_type"], e["name"], e["backend"]))

    snapshot_dict: dict[str, Any] = {
        "description": description or None,
        "evaluators": clean_evaluators,
        "name": name,
        "version": version,
    }

    canonical_json = json.dumps(
        snapshot_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    sha256_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return snapshot_dict, sha256_hash


def compute_gate_snapshot(
    name: str,
    description: str | None,
    version: int,
    rules: Sequence[Any],
) -> tuple[dict[str, Any], str]:
    """Computes a deterministic canonical regression gate snapshot and its SHA-256 hash.

    Requirements:
      - Stable field ordering
      - Deterministic serialization (sorted keys, compact separators)
      - Canonical sorting of rules
      - Bounded snapshot size
    """
    clean_rules: list[dict[str, Any]] = []

    for r in rules:
        if isinstance(r, dict):
            r_metric = str(r.get("metric_name", "")).strip().lower()
            r_op = str(r.get("operator", "")).strip().lower()
            if hasattr(r.get("operator"), "value"):
                r_op = str(r["operator"].value).lower()
            r_thresh = float(r.get("threshold", 0.0))
            r_delta = bool(r.get("is_delta", False))
            r_sev = str(r.get("severity", "critical")).strip().lower()
            if hasattr(r.get("severity"), "value"):
                r_sev = str(r["severity"].value).lower()
            r_enabled = bool(r.get("enabled", True))
        else:
            r_metric = str(getattr(r, "metric_name", "")).strip().lower()
            raw_op = getattr(r, "operator", "")
            r_op = str(getattr(raw_op, "value", raw_op)).strip().lower()
            r_thresh = float(getattr(r, "threshold", 0.0))
            r_delta = bool(getattr(r, "is_delta", False))
            raw_sev = getattr(r, "severity", "critical")
            r_sev = str(getattr(raw_sev, "value", raw_sev)).strip().lower()
            r_enabled = bool(getattr(r, "enabled", True))

        clean_rules.append(
            {
                "enabled": r_enabled,
                "is_delta": r_delta,
                "metric_name": r_metric,
                "operator": r_op,
                "severity": r_sev,
                "threshold": r_thresh,
            }
        )

    clean_rules.sort(key=lambda x: (x["metric_name"], x["operator"], x["is_delta"]))

    snapshot_dict: dict[str, Any] = {
        "description": description or None,
        "name": name,
        "rules": clean_rules,
        "version": version,
    }

    canonical_json = json.dumps(
        snapshot_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    sha256_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return snapshot_dict, sha256_hash


def compute_analysis_snapshot(
    run_id: UUID,
    baseline_run_id: UUID | None,
    analysis_data: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Computes a deterministic canonical analysis snapshot and its SHA-256 hash.

    Requirements:
      - Stable field ordering
      - Deterministic serialization (sorted keys, compact separators)
      - No secrets or tracebacks
      - Independent of generation timestamps
    """
    clean_data = dict(analysis_data)
    clean_data.pop("created_at", None)
    clean_data.pop("generated_at", None)
    clean_data.pop("snapshot_hash", None)
    clean_data.pop("correlation_id", None)

    snapshot_dict: dict[str, Any] = {
        "analysis": clean_data,
        "baseline_run_id": str(baseline_run_id) if baseline_run_id else None,
        "run_id": str(run_id),
    }

    def _json_default(obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        if hasattr(obj, "value"):
            return str(obj.value)
        return str(obj)

    canonical_json = json.dumps(
        snapshot_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )

    sha256_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return snapshot_dict, sha256_hash
