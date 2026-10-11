from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
)

from app.services.batch_store import batches
from app.services.hospital_service import (
    batch_summary,
    create_batch,
    process_hospitals,
    run_batch,
    retry_failed_hospitals,
)
from app.utils.csv_utils import check_hospitals_csv, read_hospitals_csv


app = FastAPI(title="Hospital Bulk Processing API")


@app.get("/")
async def root():
    return {
        "service": "Hospital Bulk Processing API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/hospitals/bulk/validate")
async def validate_hospitals_csv(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file",
        )

    contents = await file.read()
    hospitals, errors = check_hospitals_csv(contents)

    if errors:
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


@app.post("/hospitals/bulk")
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

    if background:
        batch_id = create_batch(hospitals)
        background_tasks.add_task(run_batch, batch_id)
        return batch_summary(batches[batch_id])

    return await process_hospitals(hospitals)


@app.get("/hospitals/bulk/{batch_id}")
async def get_batch_status(batch_id: str):
    batch = batches.get(batch_id)

    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    return batch_summary(batch)


@app.post("/hospitals/bulk/{batch_id}/retry")
async def retry_hospital_batch(batch_id: str):
    result = await retry_failed_hospitals(batch_id)

    if "error" in result:
        if result["error"] == "Batch not found":
            raise HTTPException(status_code=404, detail=result["error"])

        raise HTTPException(status_code=400, detail=result["error"])

    return result
