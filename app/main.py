
from fastapi import FastAPI, File, HTTPException, UploadFile

from app.services.hospital_service import (
    process_hospitals,
    retry_failed_hospitals,
)
from app.utils.csv_utils import read_hospitals_csv


app = FastAPI(title="Hospital Bulk Processing API")


@app.post("/hospitals/bulk/validate")
async def validate_hospitals_csv(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file",
        )

    contents = await file.read()
    hospitals = read_hospitals_csv(contents)

    return {
        "valid": True,
        "total_hospitals": len(hospitals),
        "message": "CSV validation successful",
    }


@app.post("/hospitals/bulk")
async def bulk_create_hospitals(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file",
        )

    contents = await file.read()
    hospitals = read_hospitals_csv(contents)

    return await process_hospitals(hospitals)


@app.post("/hospitals/bulk/{batch_id}/retry")
async def retry_hospital_batch(batch_id: str):
    result = await retry_failed_hospitals(batch_id)

    if "error" in result:
        if result["error"] == "Batch not found":
            raise HTTPException(status_code=404, detail=result["error"])

        raise HTTPException(status_code=400, detail=result["error"])

    return result
