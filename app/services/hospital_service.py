import asyncio
import time
import uuid

import httpx

from app.services.batch_store import batches


HOSPITAL_API_URL = "https://hospital-directory.onrender.com"

# The external API runs on Render's free tier and can take up to a minute
# to wake up, so allow generous timeouts and a few retries with backoff.
REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=30.0)
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # seconds; doubles after each failed attempt

# Gateway errors and rate limiting mean the request was not processed,
# so they are safe to retry even for POST.
SAFE_RETRY_STATUSES = {429, 502, 503, 504}


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


async def send_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    idempotent: bool,
    **kwargs,
) -> httpx.Response:
    """Send a request, retrying transient failures with exponential backoff.

    Non-idempotent requests (POST) are only retried when the request
    definitely did not reach the server or was rejected before processing
    (connection errors, 429, 502, 503, 504). Retrying a POST after a read
    timeout or a 500 could create a duplicate hospital, so those failures
    are returned to the caller and handled by the reconciling retry endpoint.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await getattr(client, method)(url, **kwargs)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status in SAFE_RETRY_STATUSES or (
                idempotent and status >= 500
            )
            if not retryable or attempt == MAX_ATTEMPTS:
                raise

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # The request never reached the server: always safe to retry.
            if attempt == MAX_ATTEMPTS:
                raise

        except httpx.TransportError:
            # Read/write timeouts etc.: the server may have processed it.
            if not idempotent or attempt == MAX_ATTEMPTS:
                raise

        await asyncio.sleep(RETRY_BASE_DELAY * 2 ** (attempt - 1))

    raise RuntimeError("unreachable")


async def warm_up(client: httpx.AsyncClient, batch_id: str) -> None:
    """Wake the external API before sending concurrent requests.

    One cheap read absorbs a cold start, so the batch's POST requests
    don't all time out together. Failures are ignored: the real requests
    have their own retries.
    """
    try:
        await send_with_retry(
            client,
            "get",
            f"{HOSPITAL_API_URL}/hospitals/batch/{batch_id}",
            idempotent=True,
        )
    except httpx.HTTPError:
        pass


def batch_summary(batch: dict) -> dict:
    return {k: v for k, v in batch.items() if k != "hospitals_input"}


def create_batch(hospitals: list[dict]) -> str:
    """Register a new batch in the store and return its ID."""
    batch_id = str(uuid.uuid4())

    batches[batch_id] = {
        "batch_id": batch_id,
        "status": "processing",
        "hospitals_input": hospitals,
        "total_hospitals": len(hospitals),
        "processed_hospitals": 0,
        "failed_hospitals": 0,
        "processing_time_seconds": 0,
        "batch_activated": False,
        "hospitals": [],
    }

    return batch_id


async def run_batch(batch_id: str) -> dict:
    """Create all hospitals of a batch, updating progress live."""
    batch = batches[batch_id]
    hospitals = batch["hospitals_input"]
    start_time = time.perf_counter()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        await warm_up(client, batch_id)

        async def create_hospital(index: int, row: dict) -> dict:
            hospital = {
                "name": row["name"].strip(),
                "address": row["address"].strip(),
                "phone": (row.get("phone") or "").strip() or None,
                "creation_batch_id": batch_id,
            }

            try:
                response = await send_with_retry(
                    client,
                    "post",
                    f"{HOSPITAL_API_URL}/hospitals/",
                    idempotent=False,
                    json=hospital,
                )
                data = response.json()

                result = {
                    "row": index,
                    "hospital_id": data.get("id"),
                    "name": hospital["name"],
                    "status": "created",
                }
                batch["processed_hospitals"] += 1

            except (httpx.HTTPError, ValueError) as exc:
                result = {
                    "row": index,
                    "name": hospital["name"],
                    "status": "failed",
                    "error": str(exc),
                }
                batch["failed_hospitals"] += 1

            # Publish progress as soon as each hospital finishes.
            batch["hospitals"].append(result)
            batch["processing_time_seconds"] = round(
                time.perf_counter() - start_time, 2
            )
            return result

        results = await asyncio.gather(
            *[
                create_hospital(index, row)
                for index, row in enumerate(hospitals, start=1)
            ]
        )

        # Keep final results in CSV row order.
        batch["hospitals"] = list(results)

        if batch["failed_hospitals"] == 0:
            batch["status"] = "activating"
            try:
                await send_with_retry(
                    client,
                    "patch",
                    f"{HOSPITAL_API_URL}/hospitals/batch/"
                    f"{batch_id}/activate",
                    idempotent=True,
                )
                batch["batch_activated"] = True

                for result in results:
                    result["status"] = "created_and_activated"

            except httpx.HTTPError as exc:
                batch["activation_error"] = str(exc)

    batch["processing_time_seconds"] = round(
        time.perf_counter() - start_time, 2
    )
    batch["status"] = "completed" if batch["batch_activated"] else "failed"

    return batch_summary(batch)


async def process_hospitals(hospitals: list[dict]) -> dict:
    """Create a batch and process it, waiting for the full result."""
    batch_id = create_batch(hospitals)
    return await run_batch(batch_id)


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

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if failed_results:
            # Reconcile with the external API before retrying POSTs.
            # This reduces duplicate risk if a previous POST succeeded
            # remotely but our service did not receive its response.
            try:
                response = await send_with_retry(
                    client,
                    "get",
                    f"{HOSPITAL_API_URL}/hospitals/batch/{batch_id}",
                    idempotent=True,
                )
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
                    response = await send_with_retry(
                        client,
                        "post",
                        f"{HOSPITAL_API_URL}/hospitals/",
                        idempotent=False,
                        json=hospital,
                    )
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
                await send_with_retry(
                    client,
                    "patch",
                    f"{HOSPITAL_API_URL}/hospitals/batch/"
                    f"{batch_id}/activate",
                    idempotent=True,
                )

                batch["batch_activated"] = True
                batch.pop("activation_error", None)

                for result in batch["hospitals"]:
                    result["status"] = "created_and_activated"

            except httpx.HTTPError as exc:
                batch["batch_activated"] = False
                batch["activation_error"] = str(exc)

    batch["status"] = (
        "completed" if batch["batch_activated"] else "failed"
    )

    return batch_summary(batch)
