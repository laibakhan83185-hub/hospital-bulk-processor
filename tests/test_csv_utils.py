import pytest
from fastapi import HTTPException

from app.utils.csv_utils import read_hospitals_csv


def test_duplicate_hospitals_are_rejected():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        "City Hospital,MG Road,9876543211\n"
    )

    with pytest.raises(HTTPException) as exc_info:
        read_hospitals_csv(csv_content.encode("utf-8"))

    assert exc_info.value.status_code == 400
    assert "Duplicate hospital found at CSV row 3" in exc_info.value.detail


def test_duplicate_with_different_case_and_whitespace_is_rejected():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        " city   hospital , MG   Road ,9876543211\n"
    )

    with pytest.raises(HTTPException) as exc_info:
        read_hospitals_csv(csv_content.encode("utf-8"))

    assert exc_info.value.status_code == 400
    assert "Duplicate hospital found at CSV row 3" in exc_info.value.detail


def test_different_hospitals_are_accepted():
    csv_content = (
        "name,address,phone\n"
        "City Hospital,MG Road,9876543210\n"
        "County Hospital,Park Street,9876543211\n"
    )

    hospitals = read_hospitals_csv(csv_content.encode("utf-8"))

    assert len(hospitals) == 2
