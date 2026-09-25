import pytest

from app.core.config import Settings, require_test_database_url


def test_test_database_url_does_not_fall_back_to_development_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    settings = Settings(
        database_url="postgresql+psycopg://dev/evalx",
        _env_file=None,  # type: ignore[call-arg]
    )

    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL must be configured"):
        require_test_database_url(settings)


def test_test_database_url_must_differ_from_development_url() -> None:
    database_url = "postgresql+psycopg://user:password@localhost:5432/evalx"
    settings = Settings(
        database_url=database_url,
        test_database_url=database_url,
    )

    with pytest.raises(RuntimeError, match="must differ from DATABASE_URL"):
        require_test_database_url(settings)


def test_test_database_url_is_returned_when_explicit_and_distinct() -> None:
    test_database_url = "postgresql+psycopg://user:password@localhost:5432/evalx_test"
    settings = Settings(
        database_url="postgresql+psycopg://user:password@localhost:5432/evalx",
        test_database_url=test_database_url,
    )

    assert require_test_database_url(settings) == test_database_url
