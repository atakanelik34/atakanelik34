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
