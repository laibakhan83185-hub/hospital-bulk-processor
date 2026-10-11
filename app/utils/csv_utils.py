import csv
import io

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas import HospitalRow
from app.utils.text import normalize

MAX_HOSPITALS = 20
REQUIRED_COLUMNS = {"name", "address"}
ALLOWED_COLUMNS = {"name", "address", "phone"}


def hospital_key(row: dict) -> tuple[str, str]:
    """Create a normalized key to identify duplicate hospitals."""
    return normalize(row["name"]), normalize(row["address"])


def row_error(row: int | None, message: str) -> dict:
    return {"row": row, "message": message}


def check_hospitals_csv(contents: bytes) -> tuple[list[dict], list[dict]]:
    """Parse and validate a hospitals CSV.

    Returns (hospitals, errors). Every problem found is reported, not just
    the first one. Row numbers are CSV line numbers (the header is line 1);
    file-level errors have row None. Hospitals are returned with normalized
    column names and stripped values.
    """
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [row_error(None, "CSV must use UTF-8 encoding")]

    reader = csv.reader(io.StringIO(text))

    # Read everything up front so malformed CSV (for example a field over
    # Python's csv size limit) becomes a normal validation error, not a 500.
    try:
        header = next(reader, None)
        records = [(reader.line_num, values) for values in reader]
    except csv.Error as exc:
        return [], [row_error(reader.line_num or None, f"Could not parse CSV: {exc}")]

    if header is None:
        return [], [row_error(None, "CSV file is empty")]

    # Accept headers like " Name " or "NAME".
    columns = [column.strip().casefold() for column in header]

    missing = REQUIRED_COLUMNS - set(columns)
    if missing:
        return [], [
            row_error(
                None,
                "CSV must contain name and address columns "
                f"(missing: {', '.join(sorted(missing))})",
            )
        ]

    errors = []

    unknown = [column for column in columns if column not in ALLOWED_COLUMNS]
    if unknown:
        errors.append(
            row_error(
                None,
                f"Unexpected column(s): {', '.join(unknown)}. "
                "Allowed columns are name, address, phone",
            )
        )

    hospitals = []
    data_rows = 0
    seen: dict[tuple[str, str], int] = {}

    for line, values in records:
        # Skip blank lines, including ones that only contain commas.
        if not any(value.strip() for value in values):
            continue

        data_rows += 1

        if len(values) > len(columns):
            errors.append(
                row_error(
                    line,
                    f"Row has {len(values)} values but the header has "
                    f"{len(columns)} columns",
                )
            )
            continue

        row = {
            column: (values[i].strip() if i < len(values) else "")
            for i, column in enumerate(columns)
        }

        try:
            hospital = HospitalRow(**row)
        except ValidationError as exc:
            hospital = None
            for error in exc.errors():
                errors.append(row_error(line, error["msg"]))

        # Duplicates are checked on the raw values, so a row with a bad phone
        # can still be reported as a duplicate in the same pass.
        if row["name"] and row["address"]:
            key = hospital_key(row)
            if key in seen:
                errors.append(
                    row_error(
                        line,
                        f"Duplicate hospital found (same as row {seen[key]})",
                    )
                )
                hospital = None
            else:
                seen[key] = line

        if hospital is not None:
            hospitals.append({"row": line, **hospital.model_dump()})

    if data_rows > MAX_HOSPITALS:
        errors.insert(
            0,
            row_error(
                None,
                f"Maximum {MAX_HOSPITALS} hospitals allowed (found {data_rows})",
            ),
        )

    if data_rows == 0:
        errors.append(row_error(None, "CSV contains no hospital records"))

    return hospitals, errors


def format_errors(errors: list[dict]) -> str:
    return "; ".join(
        f"Row {error['row']}: {error['message']}"
        if error["row"] is not None
        else error["message"]
        for error in errors
    )


def read_hospitals_csv(contents: bytes) -> list[dict]:
    """Return validated hospitals or raise a 400 listing every error."""
    hospitals, errors = check_hospitals_csv(contents)

    if errors:
        raise HTTPException(status_code=400, detail=format_errors(errors))

    return hospitals
