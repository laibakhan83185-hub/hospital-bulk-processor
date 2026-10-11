import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

PHONE_PATTERN = re.compile(r"^[0-9+\-() .]{3,30}$")

HospitalStatus = Literal["created", "created_and_activated", "failed"]
BatchStatus = Literal["processing", "activating", "completed", "failed"]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class HospitalRow(BaseModel):
    """One hospital row from the uploaded CSV."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    address: str
    phone: str | None = None

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        if not value:
            raise PydanticCustomError("missing_name", "Hospital name is missing")
        return value

    @field_validator("address")
    @classmethod
    def address_required(cls, value: str) -> str:
        if not value:
            raise PydanticCustomError("missing_address", "Hospital address is missing")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        if not value:
            return None
        digits = sum(char.isdigit() for char in value)
        if not PHONE_PATTERN.match(value) or digits < 3:
            raise PydanticCustomError(
                "invalid_phone",
                "Invalid phone number: {phone}",
                {"phone": value},
            )
        return value


class HospitalCreate(BaseModel):
    """Payload sent to POST /hospitals/ on the Hospital Directory API."""

    name: str
    address: str
    phone: str | None = None
    creation_batch_id: str


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HospitalResult(BaseModel):
    row: int = Field(ge=1)
    hospital_id: int | None = None
    name: str
    status: HospitalStatus
    error: str | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> "HospitalResult":
        if self.status == "failed" and not self.error:
            raise ValueError("A failed hospital must include an error")
        if self.status != "failed" and self.error:
            raise ValueError("Only failed hospitals can have an error")
        return self


class BatchResponse(BaseModel):
    batch_id: str
    status: BatchStatus
    total_hospitals: int = Field(ge=0)
    processed_hospitals: int = Field(ge=0)
    failed_hospitals: int = Field(ge=0)
    processing_time_seconds: float = Field(ge=0)
    batch_activated: bool
    activation_error: str | None = None
    error: str | None = Field(
        None, description="Set if processing stopped because of an unexpected error"
    )
    hospitals: list[HospitalResult]

    @model_validator(mode="after")
    def check_counts(self) -> "BatchResponse":
        done = self.processed_hospitals + self.failed_hospitals
        if done > self.total_hospitals:
            raise ValueError(
                "processed + failed hospitals cannot exceed total_hospitals"
            )
        if self.batch_activated and self.failed_hospitals:
            raise ValueError("A batch with failed hospitals cannot be activated")
        if self.status == "completed" and not self.batch_activated:
            raise ValueError("A completed batch must be activated")
        return self


class ValidationErrorItem(BaseModel):
    row: int | None = Field(
        None, description="CSV line number, or null for file-level errors"
    )
    message: str


class ValidationResponse(BaseModel):
    valid: bool
    total_hospitals: int = Field(ge=0)
    error_count: int = Field(ge=0)
    errors: list[ValidationErrorItem]
    message: str

    @model_validator(mode="after")
    def check_consistency(self) -> "ValidationResponse":
        if self.error_count != len(self.errors):
            raise ValueError("error_count must match the number of errors")
        if self.valid != (self.error_count == 0):
            raise ValueError("valid must be true exactly when there are no errors")
        return self


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class RootResponse(BaseModel):
    service: str
    docs: str
    health: str
