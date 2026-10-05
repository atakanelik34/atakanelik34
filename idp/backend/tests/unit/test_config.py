from pathlib import Path

import pytest
from pydantic import ValidationError

from idp.config import Environment, StorageBackend
from tests.conftest import make_settings


def test_short_jwt_secret_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make_settings(tmp_path, jwt_secret="too-short")


def test_production_refuses_local_storage(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not allowed in production"):
        make_settings(tmp_path, environment=Environment.PRODUCTION)


def test_production_refuses_wildcard_cors(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="CORS"):
        make_settings(
            tmp_path,
            environment=Environment.PRODUCTION,
            storage_backend=StorageBackend.S3,
            s3_access_key_id="k",
            s3_secret_access_key="s",
            cors_origins="*",
        )


def test_s3_requires_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="S3_ACCESS_KEY_ID"):
        make_settings(tmp_path, storage_backend=StorageBackend.S3)


def test_cors_origins_parse_from_comma_separated_string(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_secrets_do_not_leak_in_repr(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert settings.jwt_secret.get_secret_value() not in repr(settings)


def test_default_time_budgets_nest(tmp_path: Path) -> None:
    """F15: page budget ≤ document budget < attempt timeout; heartbeat ≪ lease."""
    s = make_settings(tmp_path)
    assert s.digitize_page_timeout_seconds <= s.digitize_timeout_seconds
    assert s.digitize_timeout_seconds + s.probe_timeout_seconds < s.job_timeout_seconds
    assert s.job_heartbeat_seconds * 2 <= s.job_lease_seconds


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"job_lease_seconds": 60, "job_heartbeat_seconds": 45}, "JOB_HEARTBEAT_SECONDS"),
        ({"job_timeout_seconds": 600, "digitize_timeout_seconds": 1800}, "JOB_TIMEOUT_SECONDS"),
        (
            {"digitize_page_timeout_seconds": 100, "digitize_timeout_seconds": 50},
            "DIGITIZE_PAGE_TIMEOUT_SECONDS",
        ),
    ],
)
def test_incoherent_time_budgets_are_refused(
    tmp_path: Path, overrides: dict[str, float], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(tmp_path, **overrides)
