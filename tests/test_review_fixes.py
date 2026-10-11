"""Regression tests for issues found in code review."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import hospital_service as hs
from app.services.batch_store import batches
from app.utils.csv_utils import check_hospitals_csv

client = TestClient(app)


def ok(data):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = data
    return response


def status_error(code: int):
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error",
        request=httpx.Request("POST", "https://internal.example/hospitals/"),
        response=httpx.Response(code),
    )
    return response


def failed_batch(batch_id: str) -> dict:
    batches[batch_id] = {
        "batch_id": batch_id,
        "status": "failed",
        "hospitals_input": [
            {"row": 2, "name": "A", "address": "X", "phone": None},
            {"row": 3, "name": "B", "address": "Y", "phone": None},
        ],
        "hospitals": [
            {"row": 2, "hospital_id": 1, "name": "A", "status": "created"},
            {"row": 3, "name": "B", "status": "failed", "error": "boom"},
        ],
        "total_hospitals": 2,
        "processed_hospitals": 1,
        "failed_hospitals": 1,
        "processing_time_seconds": 1.0,
        "batch_activated": False,
    }
    return batches[batch_id]


@pytest.fixture(autouse=True)
def clean_store():
    yield
    for batch_id in [b for b in batches if b.startswith("review-")]:
        batches.pop(batch_id, None)
        hs._busy_batches.discard(batch_id)


# Bug 1: retry while a batch is still processing --------------------------


def test_retry_is_refused_while_batch_is_processing():
    batch_id = hs.create_batch([{"row": 2, "name": "A", "address": "X"}])

    try:
        response = client.post(f"/hospitals/bulk/{batch_id}/retry")
        assert response.status_code == 409
        assert "still being processed" in response.json()["detail"]
    finally:
        batches.pop(batch_id, None)
        hs._busy_batches.discard(batch_id)


def test_concurrent_retries_create_the_failed_row_only_once():
    failed_batch("review-concurrent")

    async def slow_lookup(*args, **kwargs):
        await asyncio.sleep(0.05)
        return ok([{"id": 1, "name": "A", "address": "X"}])

    async def scenario():
        with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
            upstream = mock_client.return_value.__aenter__.return_value
            upstream.get = AsyncMock(side_effect=slow_lookup)
            upstream.post = AsyncMock(return_value=ok({"id": 2}))
            upstream.patch = AsyncMock(return_value=ok({}))

            results = await asyncio.gather(
                hs.retry_failed_hospitals("review-concurrent"),
                hs.retry_failed_hospitals("review-concurrent"),
                return_exceptions=True,
            )
            return results, upstream.post.call_count

    results, post_calls = asyncio.run(scenario())

    assert post_calls == 1
    assert sum(isinstance(r, hs.BatchBusy) for r in results) == 1
    finished = next(r for r in results if isinstance(r, dict))
    assert finished["batch_activated"] is True
    assert finished["processed_hospitals"] == 2


def test_counters_never_exceed_total_after_retry():
    failed_batch("review-counts")

    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        upstream = mock_client.return_value.__aenter__.return_value
        upstream.get = AsyncMock(return_value=ok([]))
        upstream.post = AsyncMock(return_value=ok({"id": 2}))
        upstream.patch = AsyncMock(return_value=ok({}))
        asyncio.run(hs.retry_failed_hospitals("review-counts"))

    response = client.get("/hospitals/bulk/review-counts")
    assert response.status_code == 200
    body = response.json()
    assert body["processed_hospitals"] + body["failed_hospitals"] == 2


# Bug 2: unexpected errors must not leave batches stuck ---------------------


def test_unexpected_response_shape_fails_the_row_not_the_batch():
    batch_id = hs.create_batch([{"row": 2, "name": "A", "address": "X"}])

    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        upstream = mock_client.return_value.__aenter__.return_value
        upstream.post = AsyncMock(return_value=ok(["not", "an", "object"]))
        result = asyncio.run(hs.run_batch(batch_id))

    assert result["status"] == "failed"
    assert result["hospitals"][0]["status"] == "failed"
    assert batch_id not in hs._busy_batches
    batches.pop(batch_id, None)


def test_unexpected_exception_marks_batch_failed():
    batch_id = hs.create_batch([{"row": 2, "name": "A", "address": "X"}])

    async def explode(*args, **kwargs):
        raise RuntimeError("boom")

    with (
        patch("app.services.hospital_service.httpx.AsyncClient"),
        patch.object(hs, "warm_up", explode),
    ):
        result = asyncio.run(hs.run_batch(batch_id))

    assert result["status"] == "failed"
    assert "RuntimeError" in result["error"]
    assert batch_id not in hs._busy_batches

    status = client.get(f"/hospitals/bulk/{batch_id}")
    assert status.status_code == 200
    batches.pop(batch_id, None)


# Bug 3: oversized fields and uploads ---------------------------------------


def test_huge_csv_field_is_a_validation_error_not_a_500():
    contents = b"name,address\n" + b"A" * 200_000 + b",X\n"

    response = client.post(
        "/hospitals/bulk/validate",
        files={"file": ("h.csv", contents, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "Could not parse CSV" in body["errors"][0]["message"]


def test_upload_over_size_limit_is_rejected(monkeypatch):
    monkeypatch.setattr("app.main.settings.max_upload_bytes", 100)

    response = client.post(
        "/hospitals/bulk",
        files={"file": ("h.csv", b"name,address\n" + b"A,X\n" * 50, "text/csv")},
    )

    assert response.status_code == 413


# Bug 4: row numbers match between endpoints ---------------------------------


def test_bulk_reports_csv_line_numbers():
    contents = "name,address\nA,X\n\nB,Y\n"

    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        upstream = mock_client.return_value.__aenter__.return_value
        upstream.post = AsyncMock(side_effect=[ok({"id": 1}), ok({"id": 2})])
        upstream.patch = AsyncMock(return_value=ok({}))
        response = client.post(
            "/hospitals/bulk",
            files={"file": ("h.csv", contents, "text/csv")},
        )

    assert [h["row"] for h in response.json()["hospitals"]] == [2, 4]


# Bug 5: upstream details stay out of client responses -----------------------


def test_upstream_error_details_are_not_exposed():
    batch_id = hs.create_batch([{"row": 2, "name": "A", "address": "X"}])

    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        upstream = mock_client.return_value.__aenter__.return_value
        upstream.post = AsyncMock(return_value=status_error(422))
        result = asyncio.run(hs.run_batch(batch_id))

    error = result["hospitals"][0]["error"]
    assert error == "Hospital Directory API returned HTTP 422"
    assert "internal.example" not in error
    batches.pop(batch_id, None)


# Smaller fixes ------------------------------------------------------------------


def test_phone_needs_digits():
    _, errors = check_hospitals_csv(b"name,address,phone\nA,X,...\n")

    assert errors == [{"row": 2, "message": "Invalid phone number: ..."}]


def test_old_finished_batches_are_evicted(monkeypatch):
    monkeypatch.setattr(hs.settings, "max_stored_batches", 2)
    for name in ("review-old-1", "review-old-2"):
        failed_batch(name)

    new_id = hs.create_batch([{"row": 2, "name": "A", "address": "X"}])

    assert "review-old-1" not in batches
    assert "review-old-2" in batches
    batches.pop(new_id, None)
    hs._busy_batches.discard(new_id)
