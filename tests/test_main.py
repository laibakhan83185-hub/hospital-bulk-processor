import asyncio
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_failed_hospital_prevents_batch_activation():
    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        client_instance = mock_client.return_value.__aenter__.return_value

        failed_response = MagicMock()
        failed_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "API error",
            request=httpx.Request(
                "POST", "https://example.com/hospitals/"
            ),
            response=httpx.Response(500),
        )
        client_instance.post.return_value = failed_response

        response = client.post(
            "/hospitals/bulk",
            files={
                "file": (
                    "hospitals.csv",
                    "name,address\nCity Hospital,MG Road\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    assert response.json()["failed_hospitals"] == 1
    client_instance.patch.assert_not_called()


def test_activation_failure_is_reported():
    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        client_instance = mock_client.return_value.__aenter__.return_value

        created_response = MagicMock()
        created_response.json.return_value = {"id": 123}
        client_instance.post.return_value = created_response

        activation_response = MagicMock()
        activation_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Activation error",
            request=httpx.Request(
                "PATCH", "https://example.com/activate"
            ),
            response=httpx.Response(500),
        )
        client_instance.patch.return_value = activation_response

        response = client.post(
            "/hospitals/bulk",
            files={
                "file": (
                    "hospitals.csv",
                    "name,address\nCity Hospital,MG Road\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    assert response.json()["batch_activated"] is False
    assert "activation_error" in response.json()


def test_validate_valid_csv():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        "County Hospital,Park Street,9876543211\n"
    )

    response = client.post(
        "/hospitals/bulk/validate",
        files={
            "file": ("hospitals.csv", csv_content, "text/csv")
        },
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["total_hospitals"] == 2


def test_validate_duplicate_csv():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        "City Hospital,MG Road,9876543211\n"
    )

    response = client.post(
        "/hospitals/bulk/validate",
        files={
            "file": ("hospitals.csv", csv_content, "text/csv")
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"] == [
        {"row": 3, "message": "Duplicate hospital found (same as row 2)"}
    ]


def test_validate_does_not_create_hospitals():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
    )

    response = client.post(
        "/hospitals/bulk/validate",
        files={
            "file": ("hospitals.csv", csv_content, "text/csv")
        },
    )

    assert response.status_code == 200


def test_validate_never_processes_hospitals():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
    )

    with patch("app.main.process_hospitals") as mock_process:
        response = client.post(
            "/hospitals/bulk/validate",
            files={
                "file": ("hospitals.csv", csv_content, "text/csv")
            },
        )

    assert response.status_code == 200
    mock_process.assert_not_called()


def test_retry_unknown_batch_returns_404():
    response = client.post(
        "/hospitals/bulk/unknown-batch/retry"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Batch not found"


def test_retry_without_failed_hospitals_returns_400():
    from app.services.batch_store import batches

    batches["test-batch"] = {
        "batch_id": "test-batch",
        "batch_activated": False,
        "hospitals": [],
        "hospitals_input": [],
    }

    try:
        response = client.post(
            "/hospitals/bulk/test-batch/retry"
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "No failed hospitals to retry"
    finally:
        batches.pop("test-batch", None)


def test_retry_successfully_creates_failed_hospital():
    from app.services.batch_store import batches
    from app.services.hospital_service import retry_failed_hospitals

    batch_id = "retry-test-batch"

    batches[batch_id] = {
        "batch_id": batch_id,
        "hospitals_input": [
            {
                "name": "City Hospital",
                "address": "MG Road",
                "phone": "",
            }
        ],
        "hospitals": [
            {
                "row": 1,
                "name": "City Hospital",
                "status": "failed",
                "error": "Temporary error",
            }
        ],
        "total_hospitals": 1,
        "processed_hospitals": 0,
        "failed_hospitals": 1,
        "batch_activated": False,
    }

    try:
        with patch(
            "app.services.hospital_service.httpx.AsyncClient"
        ) as mock_client:
            client_instance = (
                mock_client.return_value.__aenter__.return_value
            )
            
            existing_response = MagicMock()
            existing_response.json.return_value = []
            client_instance.get.return_value = existing_response
            
            created_response = MagicMock()
            created_response.json.return_value = {"id": 123}
            client_instance.post.return_value = created_response

            activation_response = MagicMock()
            client_instance.patch.return_value = activation_response

            result = asyncio.run(
                retry_failed_hospitals(batch_id)
            )

        assert result["failed_hospitals"] == 0
        assert result["batch_activated"] is True
        assert result["hospitals"][0]["hospital_id"] == 123
        assert (
            result["hospitals"][0]["status"]
            == "created_and_activated"
        )

    finally:
        batches.pop(batch_id, None)


def test_retry_activation_without_recreating_hospitals():
    from app.services.batch_store import batches
    from app.services.hospital_service import retry_failed_hospitals

    batch_id = "activation-retry-test"

    batches[batch_id] = {
        "batch_id": batch_id,
        "hospitals_input": [
            {"name": "City Hospital", "address": "MG Road", "phone": ""}
        ],
        "hospitals": [
            {
                "row": 1,
                "hospital_id": 123,
                "name": "City Hospital",
                "status": "created",
            }
        ],
        "total_hospitals": 1,
        "processed_hospitals": 1,
        "failed_hospitals": 0,
        "batch_activated": False,
        "activation_error": "Previous activation failed",
    }

    try:
        with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
            client_instance = mock_client.return_value.__aenter__.return_value

            activation_response = MagicMock()
            activation_response.raise_for_status.return_value = None
            client_instance.patch.return_value = activation_response

            result = asyncio.run(retry_failed_hospitals(batch_id))

            assert result["batch_activated"] is True
            assert "activation_error" not in result
            client_instance.patch.assert_called_once()
            client_instance.post.assert_not_called()
    finally:
        batches.pop(batch_id, None)
        
        
def test_retry_reconciles_existing_hospital_without_duplicate_creation():
    from app.services.batch_store import batches
    from app.services.hospital_service import retry_failed_hospitals

    batch_id = "reconciliation-test"

    batches[batch_id] = {
        "batch_id": batch_id,
        "hospitals_input": [
            {"name": "City Hospital", "address": "MG Road", "phone": ""}
        ],
        "hospitals": [
            {
                "row": 1,
                "name": "City Hospital",
                "status": "failed",
                "error": "Connection timeout",
            }
        ],
        "total_hospitals": 1,
        "processed_hospitals": 0,
        "failed_hospitals": 1,
        "batch_activated": False,
    }

    try:
        with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
            client_instance = mock_client.return_value.__aenter__.return_value

            lookup_response = MagicMock()
            lookup_response.json.return_value = [
                {
                    "id": 456,
                    "name": "City Hospital",
                    "address": "MG Road",
                    "creation_batch_id": batch_id,
                }
            ]
            lookup_response.raise_for_status.return_value = None
            client_instance.get.return_value = lookup_response

            activation_response = MagicMock()
            activation_response.raise_for_status.return_value = None
            client_instance.patch.return_value = activation_response

            result = asyncio.run(retry_failed_hospitals(batch_id))

            assert result["failed_hospitals"] == 0
            assert result["batch_activated"] is True
            assert result["hospitals"][0]["hospital_id"] == 456
            assert result["hospitals"][0]["status"] == "created_and_activated"

            client_instance.get.assert_called_once()
            client_instance.post.assert_not_called()
    finally:
        batches.pop(batch_id, None)
    from app.services.batch_store import batches
    from app.services.hospital_service import retry_failed_hospitals

    batch_id = "activation-retry-test"

    batches[batch_id] = {
        "batch_id": batch_id,
        "hospitals_input": [
            {"name": "City Hospital", "address": "MG Road", "phone": ""}
        ],
        "hospitals": [
            {
                "row": 1,
                "hospital_id": 123,
                "name": "City Hospital",
                "status": "created",
            }
        ],
        "total_hospitals": 1,
        "processed_hospitals": 1,
        "failed_hospitals": 0,
        "batch_activated": False,
        "activation_error": "Previous activation failed",
    }

    try:
        with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
            client_instance = mock_client.return_value.__aenter__.return_value

            activation_response = MagicMock()
            activation_response.raise_for_status.return_value = None
            client_instance.patch.return_value = activation_response

            result = asyncio.run(retry_failed_hospitals(batch_id))

            assert result["batch_activated"] is True
            assert "activation_error" not in result
            client_instance.patch.assert_called_once()
            client_instance.post.assert_not_called()
    finally:
        batches.pop(batch_id, None)


def test_get_batch_status_not_found():
    response = client.get("/hospitals/bulk/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Batch not found"


def test_background_mode_returns_immediately_and_status_is_pollable():
    with patch("app.main.run_batch") as mock_run:
        response = client.post(
            "/hospitals/bulk?background=true",
            files={
                "file": (
                    "hospitals.csv",
                    "name,address\nCity Hospital,MG Road\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    assert body["total_hospitals"] == 1
    assert body["processed_hospitals"] == 0
    assert "hospitals_input" not in body
    mock_run.assert_called_once_with(body["batch_id"])

    status = client.get(f"/hospitals/bulk/{body['batch_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "processing"


def test_progress_is_updated_while_processing():
    from app.services.batch_store import batches
    from app.services.hospital_service import create_batch, run_batch

    snapshots = []

    async def slow_post(url, json):
        # Record progress seen by a poller before this request finishes.
        snapshots.append(batches[json["creation_batch_id"]]["processed_hospitals"])
        await asyncio.sleep(0.01 * len(snapshots))
        response = MagicMock()
        response.json.return_value = {"id": len(snapshots)}
        return response

    with patch("app.services.hospital_service.httpx.AsyncClient") as mock_client:
        client_instance = mock_client.return_value.__aenter__.return_value
        client_instance.post.side_effect = slow_post
        client_instance.patch.return_value = MagicMock()

        batch_id = create_batch(
            [
                {"name": "A", "address": "X"},
                {"name": "B", "address": "Y"},
            ]
        )
        result = asyncio.run(run_batch(batch_id))

    assert snapshots == [0, 0]
    assert result["status"] == "completed"
    assert result["processed_hospitals"] == 2
    assert result["batch_activated"] is True
    assert [h["row"] for h in result["hospitals"]] == [1, 2]
    assert all(h["status"] == "created_and_activated" for h in result["hospitals"])


def test_root_points_to_docs():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
