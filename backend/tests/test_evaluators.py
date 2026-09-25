import pytest

from app.evaluation.engine import EvaluationEngine
from app.evaluation.enums import EvaluatorType, MetricStatus
from app.evaluation.errors import UnsupportedEvaluatorError
from app.evaluation.evaluators import (
    ConsistencyEvaluator,
    ContainsEvaluator,
    ExactMatchEvaluator,
    FactualityEvaluator,
    FaithfulnessEvaluator,
    HallucinationEvaluator,
    JSONSchemaEvaluator,
    KeywordConstraintEvaluator,
    LengthConstraintEvaluator,
    OutputFormatEvaluator,
)
from app.evaluation.registry import EvaluatorRegistry
from app.evaluation.types import EvaluationInput

# ===========================================================================
# 1. ExactMatchEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_exact_match_success() -> None:
    evaluator = ExactMatchEvaluator(case_sensitive=False, strip_whitespace=True)
    input_data = EvaluationInput.create(
        input="Capital of France?",
        response="  Paris  ",
        expected_output="paris",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 1.0
    assert metric.passed is True
    assert metric.status == MetricStatus.SUCCESS
    assert "matches expected output" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_exact_match_failure() -> None:
    evaluator = ExactMatchEvaluator(case_sensitive=False)
    input_data = EvaluationInput.create(
        input="Capital of France?",
        response="London",
        expected_output="Paris",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.0
    assert metric.passed is False
    assert metric.status == MetricStatus.THRESHOLD_FAILED


@pytest.mark.asyncio
async def test_exact_match_case_sensitivity() -> None:
    eval_cs = ExactMatchEvaluator(case_sensitive=True)
    input_data = EvaluationInput.create(
        input="Echo",
        response="Hello",
        expected_output="hello",
    )
    metric = await eval_cs.evaluate(input_data)
    assert metric.score == 0.0

    eval_ci = ExactMatchEvaluator(case_sensitive=False)
    metric_ci = await eval_ci.evaluate(input_data)
    assert metric_ci.score == 1.0


@pytest.mark.asyncio
async def test_exact_match_ignore_punctuation() -> None:
    evaluator = ExactMatchEvaluator(ignore_punctuation=True, case_sensitive=False)
    input_data = EvaluationInput.create(
        input="Statement",
        response="Hello, World!",
        expected_output="Hello World",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 1.0


@pytest.mark.asyncio
async def test_exact_match_missing_expected_output() -> None:
    evaluator = ExactMatchEvaluator()
    input_data = EvaluationInput.create(
        input="Prompt without answer",
        response="Some response",
        expected_output=None,
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.passed is None
    assert metric.status == MetricStatus.UNAVAILABLE


# ===========================================================================
# 2. ContainsEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_contains_success_with_substrings() -> None:
    evaluator = ContainsEvaluator(substrings=["Python", "FastAPI"])
    input_data = EvaluationInput.create(
        input="Tech stack?",
        response="We build with Python and FastAPI for high performance.",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 1.0
    assert metric.passed is True


@pytest.mark.asyncio
async def test_contains_failure_missing_substring() -> None:
    evaluator = ContainsEvaluator(substrings=["Python", "PostgreSQL"], require_all=True)
    input_data = EvaluationInput.create(
        input="Tech stack?",
        response="We use Python and SQLite.",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.0
    assert metric.passed is False
    assert "PostgreSQL" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_contains_proportional_scoring() -> None:
    evaluator = ContainsEvaluator(
        substrings=["alpha", "beta", "gamma", "delta"],
        require_all=False,
    )
    input_data = EvaluationInput.create(
        input="Greek letters?",
        response="Here are alpha and beta.",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.5  # 2 of 4 found
    assert metric.status == MetricStatus.THRESHOLD_FAILED  # threshold=1.0 default


@pytest.mark.asyncio
async def test_contains_fallback_to_expected_output() -> None:
    evaluator = ContainsEvaluator(case_sensitive=False)
    input_data = EvaluationInput.create(
        input="Identify key concept",
        response="Photosynthesis is the process by which green plants...",
        expected_output="photosynthesis",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 1.0


@pytest.mark.asyncio
async def test_contains_missing_targets_unavailable() -> None:
    evaluator = ContainsEvaluator()
    input_data = EvaluationInput.create(
        input="Test",
        response="Test response",
        expected_output=None,
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score is None
    assert metric.status == MetricStatus.UNAVAILABLE


# ===========================================================================
# 3. KeywordConstraintEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_keyword_constraint_success() -> None:
    evaluator = KeywordConstraintEvaluator(
        required_keywords=["secure", "verified"],
        forbidden_keywords=["deprecated", "insecure"],
    )
    input_data = EvaluationInput.create(
        input="Security posture?",
        response="The connection is secure and verified by certificate authority.",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 1.0
    assert metric.passed is True


@pytest.mark.asyncio
async def test_keyword_constraint_forbidden_detected() -> None:
    evaluator = KeywordConstraintEvaluator(
        required_keywords=["secure"],
        forbidden_keywords=["insecure"],
    )
    input_data = EvaluationInput.create(
        input="Security posture?",
        response="The protocol is secure, but the fallback mode is insecure.",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 0.0
    assert metric.passed is False
    assert "Forbidden keyword(s) detected" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_keyword_constraint_word_boundary() -> None:
    # "cat" should NOT match "category" when word_boundary=True
    evaluator = KeywordConstraintEvaluator(
        forbidden_keywords=["cat"],
        word_boundary=True,
    )
    input_data = EvaluationInput.create(
        input="Classification?",
        response="The category is technology.",
    )
    metric = await evaluator.evaluate(input_data)
    assert metric.score == 1.0

    # But without word boundary, "cat" matches "category"
    evaluator_nobound = KeywordConstraintEvaluator(
        forbidden_keywords=["cat"],
        word_boundary=False,
    )
    metric_nobound = await evaluator_nobound.evaluate(input_data)
    assert metric_nobound.score == 0.0


def test_keyword_constraint_empty_config_raises() -> None:
    with pytest.raises(ValueError, match="at least one required or forbidden"):
        KeywordConstraintEvaluator(
            required_keywords=[],
            forbidden_keywords=[],
        )


# ===========================================================================
# 4. LengthConstraintEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_length_constraint_characters() -> None:
    evaluator = LengthConstraintEvaluator(
        min_length=10, max_length=50, unit="character"
    )
    evaluator = LengthConstraintEvaluator(
        min_length=10, max_length=50, unit="character"
    )

    # Within bounds
    in_bounds = EvaluationInput.create(input="Hi", response="123456789012345")
    m1 = await evaluator.evaluate(in_bounds)
    assert m1.score == 1.0
    assert m1.passed is True

    # Below minimum
    too_short = EvaluationInput.create(input="Hi", response="short")
    m2 = await evaluator.evaluate(too_short)
    assert m2.score == 0.0
    assert m2.passed is False
    assert "below minimum" in (m2.explanation or "")

    # Above maximum
    too_long = EvaluationInput.create(input="Hi", response="a" * 60)
    m3 = await evaluator.evaluate(too_long)
    assert m3.score == 0.0
    assert "exceeds maximum" in (m3.explanation or "")


@pytest.mark.asyncio
async def test_length_constraint_words() -> None:
    evaluator = LengthConstraintEvaluator(min_length=3, max_length=6, unit="word")

    good = EvaluationInput.create(input="Hi", response="One two three four")
    assert (await evaluator.evaluate(good)).score == 1.0

    few = EvaluationInput.create(input="Hi", response="One two")
    assert (await evaluator.evaluate(few)).score == 0.0


def test_length_constraint_invalid_bounds_raise() -> None:
    with pytest.raises(ValueError, match="requires min_length, max_length"):
        LengthConstraintEvaluator(min_length=None, max_length=None)

    with pytest.raises(ValueError, match="cannot exceed max_length"):
        LengthConstraintEvaluator(min_length=100, max_length=50)

    with pytest.raises(ValueError, match="must be non-negative"):
        LengthConstraintEvaluator(min_length=-5)

    with pytest.raises(ValueError, match="Invalid unit"):
        LengthConstraintEvaluator(min_length=10, unit="tokens")


# ===========================================================================
# 5. JSONSchemaEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_json_schema_valid() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer", "minimum": 0},
        },
        "required": ["name", "age"],
    }
    evaluator = JSONSchemaEvaluator(schema=schema)

    valid_input = EvaluationInput.create(
        input="Generate profile",
        response='{"name": "Alice", "age": 30}',
    )
    metric = await evaluator.evaluate(valid_input)
    assert metric.score == 1.0
    assert metric.passed is True


@pytest.mark.asyncio
async def test_json_schema_fenced_markdown() -> None:
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }
    evaluator = JSONSchemaEvaluator(schema=schema)

    fenced_input = EvaluationInput.create(
        input="Report",
        response='```json\n{"status": "ok"}\n```',
    )
    metric = await evaluator.evaluate(fenced_input)
    assert metric.score == 1.0


@pytest.mark.asyncio
async def test_json_schema_syntax_error() -> None:
    schema = {"type": "object"}
    evaluator = JSONSchemaEvaluator(schema=schema)

    bad_json = EvaluationInput.create(
        input="Test",
        response='{"unclosed: object',
    )
    metric = await evaluator.evaluate(bad_json)
    assert metric.score == 0.0
    assert "not valid JSON" in (metric.explanation or "")


@pytest.mark.asyncio
async def test_json_schema_validation_failure() -> None:
    schema = {
        "type": "object",
        "properties": {"score": {"type": "number", "minimum": 0, "maximum": 10}},
        "required": ["score"],
    }
    evaluator = JSONSchemaEvaluator(schema=schema)

    out_of_bounds = EvaluationInput.create(
        input="Rate",
        response='{"score": 99}',
    )
    metric = await evaluator.evaluate(out_of_bounds)
    assert metric.score == 0.0
    assert "schema validation failed" in (metric.explanation or "").lower()


def test_json_schema_malformed_schema_raises() -> None:
    with pytest.raises(ValueError, match="non-empty schema dictionary"):
        JSONSchemaEvaluator(schema={})

    with pytest.raises(ValueError, match="Invalid JSON schema"):
        JSONSchemaEvaluator(schema={"type": "unsupported_type_xyz"})


# ===========================================================================
# 6. OutputFormatEvaluator Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_output_format_json() -> None:
    evaluator = OutputFormatEvaluator(expected_format="json")

    valid_json = EvaluationInput.create(input="q", response='{"key": "value"}')
    assert (await evaluator.evaluate(valid_json)).score == 1.0

    invalid_json = EvaluationInput.create(input="q", response="Just plain text")
    assert (await evaluator.evaluate(invalid_json)).score == 0.0


@pytest.mark.asyncio
async def test_output_format_list() -> None:
    evaluator = OutputFormatEvaluator(expected_format="list")

    # Numbered list
    numbered = EvaluationInput.create(
        input="q", response="1. First item\n2. Second item\n3. Third item"
    )
    assert (await evaluator.evaluate(numbered)).score == 1.0

    # Bulleted list
    bulleted = EvaluationInput.create(
        input="q", response="- Item alpha\n- Item beta\n* Item gamma"
    )
    assert (await evaluator.evaluate(bulleted)).score == 1.0

    # JSON array
    json_arr = EvaluationInput.create(input="q", response='["item1", "item2"]')
    assert (await evaluator.evaluate(json_arr)).score == 1.0

    # Single line or unstructured text
    not_a_list = EvaluationInput.create(
        input="q", response="This is just a regular sentence with no list items."
    )
    assert (await evaluator.evaluate(not_a_list)).score == 0.0


@pytest.mark.asyncio
async def test_output_format_markdown() -> None:
    evaluator = OutputFormatEvaluator(expected_format="markdown")

    md_header = EvaluationInput.create(input="q", response="# Title\nSome content")
    assert (await evaluator.evaluate(md_header)).score == 1.0

    md_bold = EvaluationInput.create(input="q", response="This is **important**.")
    assert (await evaluator.evaluate(md_bold)).score == 1.0

    plain = EvaluationInput.create(
        input="q", response="Plain text with no markdown elements."
    )
    plain = EvaluationInput.create(
        input="q", response="Plain text with no markdown elements."
    )
    assert (await evaluator.evaluate(plain)).score == 0.0


@pytest.mark.asyncio
async def test_output_format_plain_text() -> None:
    evaluator = OutputFormatEvaluator(expected_format="plain_text")

    plain = EvaluationInput.create(input="q", response="This is normal plain text.")
    assert (await evaluator.evaluate(plain)).score == 1.0

    html = EvaluationInput.create(input="q", response="<p>This is HTML</p>")
    assert (await evaluator.evaluate(html)).score == 0.0

    raw_json = EvaluationInput.create(input="q", response='{"data": 123}')
    assert (await evaluator.evaluate(raw_json)).score == 0.0


def test_output_format_unsupported_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported format"):
        OutputFormatEvaluator(expected_format="yaml")


# ===========================================================================
# 7. Reference-Aware Evaluator Stubs Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_reference_aware_evaluators_missing_reference() -> None:
    # When reference ground truth and context are missing, evaluators return UNAVAILABLE
    input_data = EvaluationInput.create(
        input="Question",
        response="Answer",
    )

    evaluators = [
        FactualityEvaluator(),
        FaithfulnessEvaluator(),
        HallucinationEvaluator(),
        ConsistencyEvaluator(),
    ]

    for evaluator in evaluators:
        metric = await evaluator.evaluate(input_data)
        assert metric.score is None
        assert metric.passed is None
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.explanation is not None


# ===========================================================================
# 8. EvaluatorRegistry Tests
# ===========================================================================


def test_evaluator_registry_registration_and_lookup() -> None:
    registry = EvaluatorRegistry()
    em = ExactMatchEvaluator(name="em_custom")
    fact = FactualityEvaluator(name="fact_custom")

    registry.register(em)
    registry.register(fact)

    assert registry.has("em_custom") is True
    assert registry.has("nonexistent") is False
    assert registry.get("em_custom") is em
    assert len(registry.list_evaluators()) == 2

    # Lookup by EvaluatorType
    instruction_evals = registry.get_by_type(EvaluatorType.INSTRUCTION_FOLLOWING)
    assert len(instruction_evals) == 1
    assert instruction_evals[0].name == "em_custom"

    fact_evals = registry.get_by_type(EvaluatorType.FACTUALITY)
    assert len(fact_evals) == 1
    assert fact_evals[0].name == "fact_custom"


def test_evaluator_registry_rejection_of_unsupported() -> None:
    registry = EvaluatorRegistry()

    # Non-existent name
    with pytest.raises(UnsupportedEvaluatorError, match="not registered"):
        registry.get("missing_evaluator")

    # Invalid EvaluatorType
    with pytest.raises(UnsupportedEvaluatorError, match="Unsupported evaluator type"):
        registry.get_by_type("totally_unknown_category")


def test_evaluator_registry_unregistration() -> None:
    registry = EvaluatorRegistry()
    em = ExactMatchEvaluator(name="em")
    registry.register(em)
    assert registry.has("em") is True

    registry.unregister("em")
    assert registry.has("em") is False
    assert len(registry.get_by_type(EvaluatorType.INSTRUCTION_FOLLOWING)) == 0


@pytest.mark.asyncio
async def test_engine_with_custom_registry() -> None:
    registry = EvaluatorRegistry()
    em = ExactMatchEvaluator(name="em", case_sensitive=False)
    registry.register(em)

    engine = EvaluationEngine(registry=registry)
    assert engine.list_evaluators() == ["em"]

    input_data = EvaluationInput.create(
        input="City?",
        response="Berlin",
        expected_output="berlin",
    )
    res = await engine.evaluate_case(input_data)
    assert res.overall_score == 1.0
    assert res.passed is True
    assert "em" in res.metrics
