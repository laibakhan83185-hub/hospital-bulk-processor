
import csv
import io

from fastapi import HTTPException


MAX_HOSPITALS = 20


def hospital_key(row: dict) -> tuple[str, str]:
    """Create a normalized key to identify duplicate hospitals."""
    name = " ".join(row["name"].casefold().split())
    address = " ".join(row["address"].casefold().split())
    return name, address


def read_hospitals_csv(contents: bytes) -> list[dict]:
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="CSV must use UTF-8 encoding",
        )

    reader = csv.DictReader(io.StringIO(text))

    required_columns = {"name", "address"}
    columns = set(reader.fieldnames or [])

    if not required_columns.issubset(columns):
        raise HTTPException(
            status_code=400,
            detail="CSV must contain name and address columns",
        )

    rows = list(reader)

    if not rows:
        raise HTTPException(
            status_code=400,
            detail="CSV contains no hospital records",
        )

    if len(rows) > MAX_HOSPITALS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_HOSPITALS} hospitals allowed",
        )

    
    seen = set()

    for row_number, row in enumerate(rows, start=2):
        name = (row.get("name") or "").strip()
        address = (row.get("address") or "").strip()

        if not name:
            raise HTTPException(
                status_code=400,
                detail=f"Hospital name is missing at CSV row {row_number}",
            )

        if not address:
            raise HTTPException(
                status_code=400,
                detail=f"Hospital address is missing at CSV row {row_number}",
            )

        key = hospital_key({"name": name, "address": address})

        if key in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate hospital found at CSV row {row_number}",
            )

        seen.add(key)

    return rows
