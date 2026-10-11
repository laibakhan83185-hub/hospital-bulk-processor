import logging

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
)

from app.logging_config import setup_logging
from app.schemas import (
    BatchResponse,
    ErrorResponse,
    HealthResponse,
    RootResponse,
    ValidationResponse,
)
from app.services.batch_store import batches
from app.services.hospital_service import (
    batch_summary,
    create_batch,
    process_hospitals,
    retry_failed_hospitals,
    run_batch,
)
from app.utils.csv_utils import check_hospitals_csv, read_hospitals_csv

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Hospital Bulk Processing API")

BAD_REQUEST = {400: {"model": ErrorResponse, "description": "Invalid request"}}
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Batch not found"}}


@app.get("/", response_model=RootResponse)
async def root():
    return {
        "service": "Hospital Bulk Processing API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@app.post(
    "/hospitals/bulk/validate",
    response_model=ValidationResponse,
    responses=BAD_REQUEST,
)
async def validate_hospitals_csv(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file",
        )

    contents = await file.read()
    hospitals, errors = check_hospitals_csv(contents)

    if errors:
        logger.info(
            "CSV validation failed for %s: %d error(s)",
            file.filename,
            len(errors),
        )
        return {
            "valid": False,
            "total_hospitals": len(hospitals),
            "error_count": len(errors),
            "errors": errors,
            "message": "CSV validation failed",
        }

    return {
        "valid": True,
        "total_hospitals": len(hospitals),
        "error_count": 0,
        "errors": [],
        "message": "CSV validation successful",
    }


@app.post(
    "/hospitals/bulk",
    response_model=BatchResponse,
    response_model_exclude_none=True,
    responses=BAD_REQUEST,
)
async def bulk_create_hospitals(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    background: bool = Query(
        False,
        description=(
            "Return immediately with the batch ID and process in the "
            "background. Poll GET /hospitals/bulk/{batch_id} for progress."
        ),
    ),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file",
        )

    contents = await file.read()
    hospitals = read_hospitals_csv(contents)

    logger.info(
        "Received %s with %d hospitals (background=%s)",
        file.filename,
        len(hospitals),
        background,
    )

    if background:
        batch_id = create_batch(hospitals)
        background_tasks.add_task(run_batch, batch_id)
        return batch_summary(batches[batch_id])

    return await process_hospitals(hospitals)


@app.get(
    "/hospitals/bulk/{batch_id}",
    response_model=BatchResponse,
    response_model_exclude_none=True,
    responses=NOT_FOUND,
)
async def get_batch_status(batch_id: str):
    batch = batches.get(batch_id)

    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    return batch_summary(batch)


@app.post(
    "/hospitals/bulk/{batch_id}/retry",
    response_model=BatchResponse,
    response_model_exclude_none=True,
    responses={**BAD_REQUEST, **NOT_FOUND},
)
async def retry_hospital_batch(batch_id: str):
    result = await retry_failed_hospitals(batch_id)

    if "error" in result:
        logger.warning("Retry for batch %s rejected: %s", batch_id, result["error"])
        if result["error"] == "Batch not found":
            raise HTTPException(status_code=404, detail=result["error"])

        raise HTTPException(status_code=400, detail=result["error"])

    return result
