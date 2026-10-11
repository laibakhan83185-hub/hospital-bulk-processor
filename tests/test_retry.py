import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.hospital_service import MAX_ATTEMPTS, send_with_retry

URL = "https://example.com/hospitals/"


def ok_response():
    response = MagicMock()
    response.raise_for_status.return_value = None
    return response


def error_response(status: int):
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error",
        request=httpx.Request("POST", URL),
        response=httpx.Response(status),
    )
    return response


def run(client, method, idempotent):
    return asyncio.run(send_with_retry(client, method, URL, idempotent=idempotent))


def test_post_retries_on_gateway_error_then_succeeds():
    client = MagicMock()
    client.post = AsyncMock(side_effect=[error_response(503), ok_response()])

    run(client, "post", idempotent=False)

    assert client.post.call_count == 2


def test_post_retries_on_connection_error():
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[httpx.ConnectError("cold start"), ok_response()]
    )

    run(client, "post", idempotent=False)

    assert client.post.call_count == 2


def test_post_does_not_retry_read_timeout_to_avoid_duplicates():
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(httpx.ReadTimeout):
        run(client, "post", idempotent=False)

    assert client.post.call_count == 1


def test_post_does_not_retry_internal_server_error():
    client = MagicMock()
    client.post = AsyncMock(return_value=error_response(500))

    with pytest.raises(httpx.HTTPStatusError):
        run(client, "post", idempotent=False)

    assert client.post.call_count == 1


def test_post_does_not_retry_client_error():
    client = MagicMock()
    client.post = AsyncMock(return_value=error_response(422))

    with pytest.raises(httpx.HTTPStatusError):
        run(client, "post", idempotent=False)

    assert client.post.call_count == 1


def test_patch_retries_read_timeout_because_it_is_idempotent():
    client = MagicMock()
    client.patch = AsyncMock(side_effect=[httpx.ReadTimeout("slow"), ok_response()])

    run(client, "patch", idempotent=True)

    assert client.patch.call_count == 2


def test_gives_up_after_max_attempts():
    client = MagicMock()
    client.patch = AsyncMock(return_value=error_response(503))

    with pytest.raises(httpx.HTTPStatusError):
        run(client, "patch", idempotent=True)

    assert client.patch.call_count == MAX_ATTEMPTS


@pytest.mark.warm_up
def test_warm_up_ignores_failures():
    from app.services.hospital_service import warm_up

    client = MagicMock()
    client.get = AsyncMock(return_value=error_response(503))

    asyncio.run(warm_up(client, "batch-1"))

    assert client.get.call_count == MAX_ATTEMPTS


def test_retry_attempts_are_logged_as_warnings(caplog):
    client = MagicMock()
    client.post = AsyncMock(side_effect=[error_response(503), ok_response()])

    with caplog.at_level("WARNING", logger="app.services.hospital_service"):
        run(client, "post", idempotent=False)

    assert "failed (attempt 1/3), retrying" in caplog.text
