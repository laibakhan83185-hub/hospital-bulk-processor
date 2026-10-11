import pytest
from pydantic import ValidationError

from app.schemas import (
    BatchResponse,
    HospitalCreate,
    HospitalResult,
    HospitalRow,
    ValidationResponse,
)


def batch(**overrides):
    data = {
        "batch_id": "b1",
        "status": "completed",
        "total_hospitals": 1,
        "processed_hospitals": 1,
        "failed_hospitals": 0,
        "processing_time_seconds": 1.2,
        "batch_activated": True,
        "hospitals": [
            {"row": 1, "hospital_id": 5, "name": "A", "status": "created_and_activated"}
        ],
    }
    data.update(overrides)
    return data


def test_hospital_row_strips_whitespace_and_blank_phone_becomes_none():
    row = HospitalRow(name="  City Hospital ", address=" MG Road ", phone="  ")

    assert row.name == "City Hospital"
    assert row.address == "MG Road"
    assert row.phone is None


def test_hospital_row_reports_each_problem():
    with pytest.raises(ValidationError) as exc_info:
        HospitalRow(name="", address="", phone="abc")

    messages = [error["msg"] for error in exc_info.value.errors()]
    assert messages == [
        "Hospital name is missing",
        "Hospital address is missing",
        "Invalid phone number: abc",
    ]


def test_hospital_create_payload_shape():
    payload = HospitalCreate(name="A", address="X", creation_batch_id="b1")

    assert payload.model_dump() == {
        "name": "A",
        "address": "X",
        "phone": None,
        "creation_batch_id": "b1",
    }


def test_failed_hospital_result_requires_error():
    with pytest.raises(ValidationError, match="must include an error"):
        HospitalResult(row=1, name="A", status="failed")


def test_valid_batch_response():
    response = BatchResponse(**batch())

    assert response.batch_activated is True


def test_counts_cannot_exceed_total():
    with pytest.raises(ValidationError, match="cannot exceed total_hospitals"):
        BatchResponse(**batch(processed_hospitals=2))


def test_batch_with_failures_cannot_be_activated():
    with pytest.raises(ValidationError, match="cannot be activated"):
        BatchResponse(
            **batch(
                total_hospitals=2,
                failed_hospitals=1,
                hospitals=[
                    {"row": 1, "name": "A", "status": "failed", "error": "boom"}
                ],
            )
        )


def test_completed_batch_must_be_activated():
    with pytest.raises(ValidationError, match="must be activated"):
        BatchResponse(**batch(batch_activated=False))


def test_unknown_status_is_rejected():
    with pytest.raises(ValidationError):
        BatchResponse(**batch(status="done"))


def test_validation_response_must_be_consistent():
    with pytest.raises(ValidationError, match="valid must be true"):
        ValidationResponse(
            valid=True,
            total_hospitals=0,
            error_count=1,
            errors=[{"row": 2, "message": "Hospital name is missing"}],
            message="x",
        )

    with pytest.raises(ValidationError, match="error_count must match"):
        ValidationResponse(
            valid=False,
            total_hospitals=0,
            error_count=2,
            errors=[{"row": 2, "message": "Hospital name is missing"}],
            message="x",
        )
