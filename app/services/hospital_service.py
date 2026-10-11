
import asyncio
import time
import uuid

import httpx

from app.services.batch_store import batches


HOSPITAL_API_URL = "https://hospital-directory.onrender.com"


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


async def process_hospitals(hospitals: list[dict]) -> dict:
    batch_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    batches[batch_id] = {
        "batch_id": batch_id,
        "hospitals_input": hospitals,
        "hospitals": [],
        "batch_activated": False,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def create_hospital(index: int, row: dict) -> dict:
            hospital = {
                "name": row["name"].strip(),
                "address": row["address"].strip(),
                "phone": (row.get("phone") or "").strip() or None,
                "creation_batch_id": batch_id,
            }

            try:
                response = await client.post(
                    f"{HOSPITAL_API_URL}/hospitals/",
                    json=hospital,
                )
                response.raise_for_status()
                data = response.json()

                return {
                    "row": index,
                    "hospital_id": data.get("id"),
                    "name": hospital["name"],
                    "status": "created",
                }

            except (httpx.HTTPError, ValueError) as exc:
                return {
                    "row": index,
                    "name": hospital["name"],
                    "status": "failed",
                    "error": str(exc),
                }

        results = await asyncio.gather(
            *[
                create_hospital(index, row)
                for index, row in enumerate(hospitals, start=1)
            ]
        )

        failed_count = sum(
            result["status"] == "failed" for result in results
        )

        batch_activated = False
        activation_error = None

        if failed_count == 0:
            try:
                response = await client.patch(
                    f"{HOSPITAL_API_URL}/hospitals/batch/"
                    f"{batch_id}/activate"
                )
                response.raise_for_status()
                batch_activated = True

                for result in results:
                    result["status"] = "created_and_activated"

            except httpx.HTTPError as exc:
                activation_error = str(exc)

    summary = {
        "batch_id": batch_id,
        "total_hospitals": len(hospitals),
        "processed_hospitals": len(hospitals) - failed_count,
        "failed_hospitals": failed_count,
        "processing_time_seconds": round(
            time.perf_counter() - start_time, 2
        ),
        "batch_activated": batch_activated,
        "hospitals": results,
    }

    if activation_error:
        summary["activation_error"] = activation_error

    batches[batch_id].update(summary)

    return summary


async def retry_failed_hospitals(batch_id: str) -> dict:
    batch = batches.get(batch_id)

    if batch is None:
        return {"error": "Batch not found"}

    if batch["batch_activated"]:
        return {"error": "Batch is already activated"}

    failed_results = [
        result
        for result in batch["hospitals"]
        if result["status"] == "failed"
    ]

    # No failed creations? Retry activation only if activation previously failed.
    if not failed_results and "activation_error" not in batch:
        return {"error": "No failed hospitals to retry"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        if failed_results:
            # Reconcile with the external API before retrying POSTs.
            # This reduces duplicate risk if a previous POST succeeded
            # remotely but our service did not receive its response.
            try:
                response = await client.get(
                    f"{HOSPITAL_API_URL}/hospitals/batch/{batch_id}"
                )
                response.raise_for_status()
                existing_hospitals = response.json()

                if not isinstance(existing_hospitals, list):
                    return {
                        "error": (
                            "Could not verify existing batch hospitals; "
                            "no retries were attempted"
                        )
                    }

            except (httpx.HTTPError, ValueError) as exc:
                return {
                    "error": (
                        "Could not verify existing batch hospitals; "
                        f"no retries were attempted: {exc}"
                    )
                }

            for result in failed_results:
                row = batch["hospitals_input"][result["row"] - 1]

                # Check whether the external API already created this record.
                existing = next(
                    (
                        hospital
                        for hospital in existing_hospitals
                        if normalize(hospital.get("name", ""))
                        == normalize(row["name"])
                        and normalize(hospital.get("address", ""))
                        == normalize(row["address"])
                    ),
                    None,
                )

                if existing:
                    result["hospital_id"] = existing.get("id")
                    result["status"] = "created"
                    result.pop("error", None)
                    continue

                hospital = {
                    "name": row["name"].strip(),
                    "address": row["address"].strip(),
                    "phone": (row.get("phone") or "").strip() or None,
                    "creation_batch_id": batch_id,
                }

                try:
                    response = await client.post(
                        f"{HOSPITAL_API_URL}/hospitals/",
                        json=hospital,
                    )
                    response.raise_for_status()
                    data = response.json()

                    result["hospital_id"] = data.get("id")
                    result["status"] = "created"
                    result.pop("error", None)

                except (httpx.HTTPError, ValueError) as exc:
                    result["error"] = str(exc)

        failed_count = sum(
            result["status"] == "failed"
            for result in batch["hospitals"]
        )

        batch["failed_hospitals"] = failed_count
        batch["processed_hospitals"] = (
            batch["total_hospitals"] - failed_count
        )

        if failed_count == 0:
            try:
                response = await client.patch(
                    f"{HOSPITAL_API_URL}/hospitals/batch/"
                    f"{batch_id}/activate"
                )
                response.raise_for_status()

                batch["batch_activated"] = True
                batch.pop("activation_error", None)

                for result in batch["hospitals"]:
                    result["status"] = "created_and_activated"

            except httpx.HTTPError as exc:
                batch["batch_activated"] = False
                batch["activation_error"] = str(exc)

    return batch
