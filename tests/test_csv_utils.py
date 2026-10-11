import pytest
from fastapi import HTTPException

from app.utils.csv_utils import check_hospitals_csv, read_hospitals_csv


def test_duplicate_hospitals_are_rejected():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        "City Hospital,MG Road,9876543211\n"
    )

    with pytest.raises(HTTPException) as exc_info:
        read_hospitals_csv(csv_content.encode("utf-8"))

    assert exc_info.value.status_code == 400
    assert "Row 3: Duplicate hospital found (same as row 2)" in exc_info.value.detail


def test_duplicate_with_different_case_and_whitespace_is_rejected():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        " city   hospital , MG   Road ,9876543211\n"
    )

    with pytest.raises(HTTPException) as exc_info:
        read_hospitals_csv(csv_content.encode("utf-8"))

    assert exc_info.value.status_code == 400
    assert "Row 3: Duplicate hospital found (same as row 2)" in exc_info.value.detail


def test_different_hospitals_are_accepted():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        "County Hospital,Park Street,9876543211\n"
    )

    hospitals = read_hospitals_csv(csv_content.encode("utf-8"))

    assert len(hospitals) == 2


def check(text: str):
    return check_hospitals_csv(text.encode("utf-8"))


def test_all_errors_are_reported_at_once():
    hospitals, errors = check(
        "name,address,phone\n"
        ",MG Road,123\n"
        "City Hospital,,123\n"
        "Good Hospital,Park Street,abc\n"
    )

    assert hospitals == []
    assert errors == [
        {"row": 2, "message": "Hospital name is missing"},
        {"row": 3, "message": "Hospital address is missing"},
        {"row": 4, "message": "Invalid phone number: abc"},
    ]


def test_headers_are_normalized():
    hospitals, errors = check(" Name , ADDRESS ,Phone\nCity Hospital,MG Road,\n")

    assert errors == []
    assert hospitals == [{"name": "City Hospital", "address": "MG Road", "phone": None}]


def test_blank_and_comma_only_lines_are_skipped():
    hospitals, errors = check("name,address\nA,X\n\n,\n , \nB,Y\n")

    assert errors == []
    assert [h["name"] for h in hospitals] == ["A", "B"]


def test_blank_lines_do_not_count_toward_limit():
    rows = "".join(f"H{i},X\n" for i in range(20))
    hospitals, errors = check("name,address\n" + rows + ",\n,\n")

    assert errors == []
    assert len(hospitals) == 20


def test_more_than_twenty_hospitals_is_rejected():
    rows = "".join(f"H{i},X\n" for i in range(21))
    _, errors = check("name,address\n" + rows)

    assert errors[0] == {
        "row": None,
        "message": "Maximum 20 hospitals allowed (found 21)",
    }


def test_row_with_too_many_values_is_rejected():
    _, errors = check("name,address,phone\nA,X,1,EXTRA\n")

    assert errors == [
        {
            "row": 2,
            "message": "Row has 4 values but the header has 3 columns",
        }
    ]


def test_unknown_column_is_rejected():
    _, errors = check("name,address,email\nA,X,a@b.com\n")

    assert errors[0]["row"] is None
    assert "Unexpected column(s): email" in errors[0]["message"]


def test_missing_required_column_names_the_column():
    _, errors = check("name,phone\nA,1\n")

    assert errors == [
        {
            "row": None,
            "message": ("CSV must contain name and address columns (missing: address)"),
        }
    ]


def test_empty_file_and_header_only():
    assert check("")[1] == [{"row": None, "message": "CSV file is empty"}]
    assert check("name,address\n")[1] == [
        {"row": None, "message": "CSV contains no hospital records"}
    ]


def test_row_numbers_match_csv_lines_after_blank_lines():
    _, errors = check("name,address\nA,X\n\n\n,Y\n")

    assert errors == [{"row": 5, "message": "Hospital name is missing"}]


def test_read_hospitals_csv_lists_every_error_in_detail():
    with pytest.raises(HTTPException) as exc_info:
        read_hospitals_csv(b"name,address\n,X\nB,\n")

    assert exc_info.value.detail == (
        "Row 2: Hospital name is missing; Row 3: Hospital address is missing"
    )
