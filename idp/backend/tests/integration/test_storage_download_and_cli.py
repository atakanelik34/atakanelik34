import io
import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import httpx
import pytest

from idp.container import Container
from idp.infrastructure.storage.base import build_key
from tests.conftest import BACKEND_ROOT


async def test_local_signed_download(client: httpx.AsyncClient, container: Container) -> None:
    key = build_key(uuid.uuid4(), "documents", "d1", "original")
    await container.storage.put(key, io.BytesIO(b"data"), content_type="text/plain", size=4)
    url = urlparse(await container.storage.signed_url(key, expires_in=60))

    ok = await client.get(f"{url.path}?{url.query}")
    assert ok.status_code == 200
    assert ok.content == b"data"

    tampered = await client.get(f"{url.path}?{url.query[:-4]}0000")
    assert tampered.status_code == 403
    assert (await client.get(url.path)).status_code == 422


@pytest.mark.usefixtures("container")
def test_cli_bootstrap_is_idempotent_with_if_missing() -> None:
    env = {
        **os.environ,
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "IDP_BOOTSTRAP_PASSWORD": "Cli-Bootstrap-Pass-1!",
    }
    args = [
        sys.executable,
        "-m",
        "idp.cli",
        "bootstrap",
        "--tenant-slug",
        "initech",
        "--tenant-name",
        "Initech",
        "--email",
        "owner@initech.test",
        "--name",
        "Bill",
    ]
    first = subprocess.run(  # noqa: S603
        args, cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert first.returncode == 0, first.stderr
    assert "Cli-Bootstrap-Pass-1!" not in first.stdout

    second = subprocess.run(  # noqa: S603
        args, cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert second.returncode == 1

    third = subprocess.run(  # noqa: S603
        [*args, "--if-missing"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert third.returncode == 0
    assert "skipped" in third.stdout
