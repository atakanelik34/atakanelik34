import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from idp.config import Settings
from idp.domain.errors import AuthenticationError
from idp.infrastructure.security.passwords import hash_password, verify_password
from idp.infrastructure.security.tokens import TokenService


def test_password_hash_roundtrip_and_mismatch() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "wrong password")


def test_verify_without_hash_is_false_not_error() -> None:
    assert not verify_password(None, "anything")
    assert not verify_password("not-a-hash", "anything")


def test_token_roundtrip(settings: Settings) -> None:
    service = TokenService(settings)
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    issued = service.issue(user_id=user_id, tenant_id=tenant_id, token_version=3)
    claims = service.decode(issued.token)
    assert (claims.user_id, claims.tenant_id, claims.token_version) == (user_id, tenant_id, 3)


def test_token_signed_with_other_secret_is_rejected(settings: Settings) -> None:
    other = settings.model_copy(update={"jwt_secret": settings.jwt_secret.__class__("x" * 48)})
    token = TokenService(other).issue(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), token_version=0)
    with pytest.raises(AuthenticationError, match="Invalid access token"):
        TokenService(settings).decode(token.token)


def test_expired_token_is_rejected(settings: Settings) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "ver": 0,
        "iat": int(past.timestamp()),
        "exp": int(past.timestamp()) + 60,
        "jti": "x",
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(AuthenticationError, match="expired"):
        TokenService(settings).decode(token)


def test_alg_none_token_is_rejected(settings: Settings) -> None:
    payload = {"iss": settings.jwt_issuer, "sub": str(uuid.uuid4()), "tid": str(uuid.uuid4())}
    token = jwt.encode(payload, key=None, algorithm="none")
    with pytest.raises(AuthenticationError):
        TokenService(settings).decode(token)


def test_token_missing_tenant_claim_is_rejected(settings: Settings) -> None:
    now = int(datetime.now(UTC).timestamp())
    payload = {"iss": settings.jwt_issuer, "sub": str(uuid.uuid4()), "iat": now, "exp": now + 60}
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(AuthenticationError):
        TokenService(settings).decode(token)
