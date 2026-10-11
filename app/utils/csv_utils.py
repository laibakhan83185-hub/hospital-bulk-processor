import csv
import io
import re

from fastapi import HTTPException


MAX_HOSPITALS = 20
REQUIRED_COLUMNS = {"name", "address"}
ALLOWED_COLUMNS = {"name", "address", "phone"}
PHONE_PATTERN = re.compile(r"^[0-9+\-() .]{3,30}$")


def hospital_key(row: dict) -> tuple[str, str]:
    """Create a normalized key to identify duplicate hospitals."""
    name = " ".join(row["name"].casefold().split())
    address = " ".join(row["address"].casefold().split())
    return name, address


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
    header = next(reader, None)

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

    for values in reader:
        line = reader.line_num

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

        row_is_valid = True

        if not row["name"]:
            errors.append(row_error(line, "Hospital name is missing"))
            row_is_valid = False

        if not row["address"]:
            errors.append(row_error(line, "Hospital address is missing"))
            row_is_valid = False

        phone = row.get("phone", "")
        if phone and not PHONE_PATTERN.match(phone):
            errors.append(row_error(line, f"Invalid phone number: {phone}"))
            row_is_valid = False

        if row["name"] and row["address"]:
            key = hospital_key(row)
            if key in seen:
                errors.append(
                    row_error(
                        line,
                        f"Duplicate hospital found (same as row {seen[key]})",
                    )
                )
                row_is_valid = False
            else:
                seen[key] = line

        if row_is_valid:
            hospitals.append(
                {
                    "name": row["name"],
                    "address": row["address"],
                    "phone": phone,
                }
            )

    if data_rows > MAX_HOSPITALS:
        errors.insert(
            0,
            row_error(
                None,
                f"Maximum {MAX_HOSPITALS} hospitals allowed "
                f"(found {data_rows})",
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
